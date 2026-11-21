"""One durable record per visible delivery (a scheduled run's result, a proactive message), so it reaches the chat exactly once across
restarts. Each record has an immutable once_key; every row a delivery creates (the chat message, the task, the notification) carries
the same key and is inserted only when no row has it, so a delivery tried again never makes a second row. A record left "sending" by an
earlier process start is taken again at once; one left by this process is taken when it is due. No try limit: a record is tried again
until it is delivered (a failed try comes back after SEND_AGAIN_SECONDS). A delivered record is deleted one year after its delivery
(purge, from the daily cleanup)."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

SEND_AGAIN_SECONDS = 3600.0   # a failed try comes back within the hour
KEEP_DELIVERED_SECONDS = 365 * 86400   # owner 2026-11-20: a delivered record is deleted one year after it was delivered (dropped: one year after)
STATES = ("waiting", "sending", "delivered", "dropped")

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    once_key TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'waiting', boot TEXT, tries INTEGER NOT NULL DEFAULT 0, send_at REAL NOT NULL,
    last_error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL, delivered_at REAL);
CREATE INDEX IF NOT EXISTS deliveries_due ON deliveries(state, send_at);
"""


@dataclass(frozen=True)
class Taken:
    once_key: str
    kind: str
    payload: dict[str, Any]
    tries: int
    again: bool      # the record had been taken by an earlier process start (another boot)


class Deliveries:
    def __init__(self, store: Any, *, clock: Callable[[], float] = time.time, boot: str | None = None) -> None:
        self.store, self.clock = store, clock
        self.boot = boot or secrets.token_hex(8)     # one per process start
        self.handlers: dict[str, Callable[[Taken], Awaitable[None]]] = {}
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def on(self, kind: str, handler: Callable[[Taken], Awaitable[None]]) -> None:
        self.handlers[kind] = handler

    def put(self, once_key: str, kind: str, ref: str, payload: dict[str, Any], *, also: Sequence[tuple[str, tuple]] = ()) -> bool:
        """Record one delivery (INSERT ... ON CONFLICT DO NOTHING), with the `also` statements in the same transaction, only when the
        record is new. True when it is new."""
        now = self.clock()
        db = self.store._db
        with self.store._lock:
            try:
                cur = db.execute("INSERT INTO deliveries(once_key, kind, ref, payload, state, boot, tries, send_at, created_at, updated_at) "
                                 "VALUES (?,?,?,?,'waiting',NULL,0,?,?,?) ON CONFLICT(once_key) DO NOTHING",
                                 (once_key, kind, ref, json.dumps(payload, ensure_ascii=False), now, now, now))
                new = cur.rowcount > 0
                if new:
                    for sql, args in also:
                        db.execute(sql, args)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return new

    def take(self, once_key: str) -> Taken | None:
        """Claim a record for this process: a record left sending by another boot, or a waiting one that is due."""
        now = self.clock()
        db = self.store._db
        with self.store._lock:
            row = db.execute("SELECT * FROM deliveries WHERE once_key = ?", (once_key,)).fetchone()
            if row is None:
                return None
            again = row["boot"] is not None and row["boot"] != self.boot
            cur = db.execute("UPDATE deliveries SET state = 'sending', boot = ?, tries = tries + 1, send_at = ?, updated_at = ? "
                             "WHERE once_key = ? AND state IN ('waiting', 'sending') AND ((boot IS NOT NULL AND boot != ?) "
                             "OR (state = 'waiting' AND send_at <= ?))",
                             (self.boot, now + SEND_AGAIN_SECONDS, now, once_key, self.boot, now))
            db.commit()
            if cur.rowcount == 0:
                return None
        return Taken(once_key, row["kind"], json.loads(row["payload"]), int(row["tries"]) + 1, again)

    def delivered(self, t: Taken) -> None:
        now = self.clock()
        with self.store._lock:
            self.store._db.execute("UPDATE deliveries SET state = 'delivered', delivered_at = ?, last_error = '', updated_at = ? "
                                   "WHERE once_key = ? AND state IN ('waiting', 'sending')", (now, now, t.once_key))
            self.store._db.commit()

    def later(self, t: Taken, error: str) -> None:
        now = self.clock()
        with self.store._lock:
            self.store._db.execute("UPDATE deliveries SET state = 'waiting', send_at = ?, last_error = ?, updated_at = ? "
                                   "WHERE once_key = ? AND state = 'sending' AND boot = ? AND tries = ?",
                                   (now + SEND_AGAIN_SECONDS, error[:300], now, t.once_key, self.boot, t.tries))
            self.store._db.commit()

    def drop(self, t: Taken, error: str) -> None:
        """Only for a record no handler can deliver (an unknown kind)."""
        now = self.clock()
        with self.store._lock:
            self.store._db.execute("UPDATE deliveries SET state = 'dropped', last_error = ?, updated_at = ? WHERE once_key = ?",
                                   (error[:300], now, t.once_key))
            self.store._db.commit()

    def purge(self, now: float | None = None) -> int:
        """Delivered records older than KEEP_DELIVERED_SECONDS (by delivered_at) go, and dropped ones (by when they were dropped); waiting
        and sending ones always stay."""
        now = self.clock() if now is None else now
        cut = now - KEEP_DELIVERED_SECONDS
        with self.store._lock:
            cur = self.store._db.execute("DELETE FROM deliveries WHERE (state = 'delivered' AND delivered_at IS NOT NULL AND delivered_at < ?) "
                                         "OR (state = 'dropped' AND updated_at < ?)", (cut, cut))
            self.store._db.commit()
            return cur.rowcount

    def due(self) -> list[str]:
        """Waiting records whose time came, and records another boot left in flight; oldest first."""
        now = self.clock()
        rows = self.store._all("SELECT once_key FROM deliveries WHERE state IN ('waiting', 'sending') "
                               "AND ((boot IS NOT NULL AND boot != ?) OR send_at <= ?) ORDER BY send_at, created_at",
                               (self.boot, now))
        return [r["once_key"] for r in rows]

    async def send(self, once_key: str) -> bool:
        """Claim, hand to the kind's handler, mark delivered. A failure is kept for a later try. Never raises; True when delivered."""
        t = self.take(once_key)
        if t is None:
            return False
        handler = self.handlers.get(t.kind)
        if handler is None:
            self.drop(t, f"no handler for {t.kind}")
            return False
        try:
            await handler(t)
        except Exception as e:  # noqa: BLE001 - kept for a later try, the error is recorded
            self.later(t, f"{type(e).__name__}: {str(e)[:200]}")
            return False
        self.delivered(t)
        return True

    async def drain(self) -> int:
        """Send every due record, one at a time; returns how many were delivered."""
        n = 0
        for key in self.due():
            if await self.send(key):
                n += 1
        return n
