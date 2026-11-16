"""Every reply's events, written down as they happen (backlog 73), so a reply can be read again after the server restarts and a
client that reconnects gets exactly what it missed.

turn_log: one row per reply (status running | done | failed | stopped | interrupted, started_at, ended_at); turn_events: its events
in order (seq = the event's index, the same number GET /api/turns/{id}?after= and the WebSocket use). Writes are batched: an event
is queued and the queue is written every FLUSH_SECONDS (and at once when the reply ends), so a stream of text pieces costs a few
writes a second, not one each. On start-up every reply still "running" (the server stopped under it) gets a last event
{"type": "interrupted", "reason": "restart"} and the status "interrupted"; the resume offer (resume.py) is what carries on.
Replies are kept KEEP_HOURS after they end, and at most MAX_TURNS of them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("uvicorn.error")

SCHEMA = """
CREATE TABLE IF NOT EXISTS turn_log (turn_id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL);
CREATE TABLE IF NOT EXISTS turn_events (turn_id TEXT NOT NULL, seq INTEGER NOT NULL, ev TEXT NOT NULL, PRIMARY KEY (turn_id, seq));
"""
FLUSH_SECONDS = 0.25
KEEP_HOURS = 24
MAX_TURNS = 500
PRUNE_EVERY = 3600.0
INTERRUPTED = {"type": "interrupted", "reason": "restart"}


class TurnLog:
    def __init__(self, store: Any, clock: Callable[[], float] = time.time, flush_every: float = FLUSH_SECONDS) -> None:
        self.store, self.clock, self.flush_every = store, clock, flush_every
        self.pending: list[tuple[str, int, str]] = []
        self._timer: asyncio.Task[None] | None = None
        self._pruned = 0.0
        store._db.executescript(SCHEMA)

    def started(self, turn_id: str) -> None:
        self.store._run("INSERT OR REPLACE INTO turn_log(turn_id, status, started_at, ended_at) VALUES(?, 'running', ?, NULL)",
                        (turn_id, self.clock()))

    def add(self, turn_id: str, seq: int, ev: dict[str, Any]) -> None:
        try:
            text = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = json.dumps({"type": str(ev.get("type", "unknown"))})
        self.pending.append((turn_id, seq, text))
        if self._timer is None or self._timer.done():
            try:
                self._timer = asyncio.get_running_loop().create_task(self._later())
            except RuntimeError:            # no loop (a test calling it directly): write now
                self.flush()

    async def _later(self) -> None:
        await asyncio.sleep(self.flush_every)
        self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        rows, self.pending = self.pending, []
        try:
            with self.store._lock:
                self.store._db.executemany("INSERT OR IGNORE INTO turn_events(turn_id, seq, ev) VALUES(?, ?, ?)", rows)
                self.store._db.commit()
        except Exception:  # noqa: BLE001
            log.exception("turn log: %d event(s) could not be written", len(rows))

    def finished(self, turn_id: str, status: str) -> None:
        self.flush()
        self.store._run("UPDATE turn_log SET status = ?, ended_at = ? WHERE turn_id = ?", (status or "stopped", self.clock(), turn_id))
        if self.clock() - self._pruned > PRUNE_EVERY:
            self.prune()

    def events(self, turn_id: str, after: int = 0) -> list[dict[str, Any]] | None:
        """The events from `after` on, or None when this reply is not known (never logged, or pruned)."""
        self.flush()
        if not self.store._all("SELECT 1 FROM turn_log WHERE turn_id = ?", (turn_id,)):
            return None
        out = []
        for r in self.store._all("SELECT ev FROM turn_events WHERE turn_id = ? AND seq >= ? ORDER BY seq", (turn_id, max(0, after))):
            try:
                out.append(json.loads(r["ev"]))
            except ValueError:
                continue
        return out

    def status(self, turn_id: str) -> str | None:
        rows = self.store._all("SELECT status FROM turn_log WHERE turn_id = ?", (turn_id,))
        return rows[0]["status"] if rows else None

    def interrupt_running(self) -> list[str]:
        """At start-up: the replies the server stopped under get their last event and the status "interrupted"."""
        ids = [r["turn_id"] for r in self.store._all("SELECT turn_id FROM turn_log WHERE status = 'running'")]
        for tid in ids:
            seq = int(self.store._all("SELECT COALESCE(MAX(seq), -1) AS m FROM turn_events WHERE turn_id = ?", (tid,))[0]["m"]) + 1
            self.store._run("INSERT OR IGNORE INTO turn_events(turn_id, seq, ev) VALUES(?, ?, ?)", (tid, seq, json.dumps(INTERRUPTED)))
            self.store._run("UPDATE turn_log SET status = 'interrupted', ended_at = ? WHERE turn_id = ?", (self.clock(), tid))
        return ids

    def prune(self) -> None:
        self._pruned = self.clock()
        cut = self.clock() - KEEP_HOURS * 3600
        old = [r["turn_id"] for r in self.store._all("SELECT turn_id FROM turn_log WHERE ended_at IS NOT NULL AND ended_at < ?", (cut,))]
        extra = [r["turn_id"] for r in self.store._all(
            "SELECT turn_id FROM turn_log WHERE status != 'running' ORDER BY started_at DESC LIMIT -1 OFFSET ?", (MAX_TURNS,))]
        for tid in set(old) | set(extra):
            self.store._run("DELETE FROM turn_events WHERE turn_id = ?", (tid,))
            self.store._run("DELETE FROM turn_log WHERE turn_id = ?", (tid,))
