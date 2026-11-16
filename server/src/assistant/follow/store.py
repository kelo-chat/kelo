"""Database persistence for follow-through: upcoming items, checks, readings, and source usage."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .items import UpcomingItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS follow_items (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    starts_at REAL NOT NULL,
    ends_at REAL,
    place TEXT NOT NULL DEFAULT '',
    refs TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'planned',
    plan TEXT NOT NULL DEFAULT '{}',
    prep TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS follow_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL REFERENCES follow_items(key) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    refs TEXT NOT NULL DEFAULT '{}',
    fields TEXT NOT NULL DEFAULT '[]',
    from_offset TEXT NOT NULL DEFAULT '-3h',
    until_offset TEXT NOT NULL DEFAULT '+30m',
    window_start REAL NOT NULL,
    window_end REAL NOT NULL,
    every_minutes INTEGER NOT NULL DEFAULT 10,
    next_at REAL NOT NULL,
    last_at REAL,
    state TEXT NOT NULL DEFAULT 'active',
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_follow_checks_item ON follow_checks(item_key);
CREATE INDEX IF NOT EXISTS idx_follow_checks_due ON follow_checks(state, next_at);
CREATE TABLE IF NOT EXISTS follow_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id INTEGER NOT NULL REFERENCES follow_checks(id) ON DELETE CASCADE,
    at REAL NOT NULL,
    ok INTEGER NOT NULL DEFAULT 1,
    fields TEXT NOT NULL DEFAULT '{}',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_follow_readings_check ON follow_readings(check_id, at);
CREATE TABLE IF NOT EXISTS follow_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    month TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    notified_allowance INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, month)
);
"""


ITEM_COLS = ("key", "kind", "title", "starts_at", "ends_at", "place", "refs", "source", "state", "plan", "prep", "created_at", "updated_at")
CHECK_COLS = ("id", "item_key", "kind", "refs", "fields", "from_offset", "until_offset", "window_start", "window_end", "every_minutes", "next_at", "last_at", "state", "consecutive_errors", "last_error", "created_at")
READING_COLS = ("id", "check_id", "at", "ok", "fields", "detail")


def _to_dict(row: Any, cols: tuple[str, ...]) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    if isinstance(row, (tuple, list)):
        return {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
    return {}


class FollowStore:
    def __init__(self, db_or_store: Any, clock: Any = time.time):
        self.clock = clock
        if isinstance(db_or_store, sqlite3.Connection):
            self._db = db_or_store
            self._store = None
        else:
            self._store = db_or_store
            self._db = getattr(db_or_store, "_db", db_or_store)

        with getattr(self._store, "_lock", _DummyLock()):
            self._db.row_factory = sqlite3.Row
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _run(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with getattr(self._store, "_lock", _DummyLock()):
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with getattr(self._store, "_lock", _DummyLock()):
            return self._db.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with getattr(self._store, "_lock", _DummyLock()):
            return self._db.execute(sql, params).fetchone()

    # ---- items -----------------------------------------------------------------
    def save_item(self, item: UpcomingItem, plan: dict[str, Any] | None = None, state: str = "planned",
                  prep: list[dict[str, Any]] | None = None) -> None:
        now = self.clock()
        plan_json = json.dumps(plan or {})
        prep_json = json.dumps(prep or [])
        refs_json = json.dumps(item.refs or {})
        self._run(
            "INSERT INTO follow_items(key, kind, title, starts_at, ends_at, place, refs, source, state, plan, prep, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, title=excluded.title, starts_at=excluded.starts_at, "
            "ends_at=excluded.ends_at, place=excluded.place, refs=excluded.refs, source=excluded.source, state=excluded.state, "
            "plan=excluded.plan, prep=excluded.prep, updated_at=excluded.updated_at",
            (item.key, item.kind, item.title, item.starts_at, item.ends_at, item.place, refs_json, item.source,
             state, plan_json, prep_json, now, now),
        )

    def get_item(self, key: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM follow_items WHERE key = ?", (key,))
        return self._item_dict(row) if row else None

    def list_items(self, state: str | None = None) -> list[dict[str, Any]]:
        if state:
            rows = self._all("SELECT * FROM follow_items WHERE state = ? ORDER BY starts_at", (state,))
        else:
            rows = self._all("SELECT * FROM follow_items ORDER BY starts_at")
        return [self._item_dict(r) for r in rows]

    def update_item_state(self, key: str, state: str) -> bool:
        cur = self._run("UPDATE follow_items SET state = ?, updated_at = ? WHERE key = ?", (state, self.clock(), key))
        return cur.rowcount > 0

    def update_item(self, key: str, **fields: Any) -> bool:
        if not fields:
            return False
        sets = []
        params = []
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            if k in ("refs", "plan", "prep"):
                params.append(json.dumps(v))
            else:
                params.append(v)
        params.append(self.clock())
        params.append(key)
        cur = self._run(f"UPDATE follow_items SET {', '.join(sets)}, updated_at = ? WHERE key = ?", tuple(params))
        return cur.rowcount > 0

    def _item_dict(self, row: Any) -> dict[str, Any]:
        d = _to_dict(row, ITEM_COLS)
        d["refs"] = json.loads(d["refs"] or "{}") if isinstance(d.get("refs"), str) else (d.get("refs") or {})
        d["plan"] = json.loads(d["plan"] or "{}") if isinstance(d.get("plan"), str) else (d.get("plan") or {})
        d["prep"] = json.loads(d["prep"] or "[]") if isinstance(d.get("prep"), str) else (d.get("prep") or [])
        return d

    # ---- checks ----------------------------------------------------------------
    def save_check(self, item_key: str, kind: str, refs: dict[str, str], fields: list[str],
                   from_offset: str, until_offset: str, window_start: float, window_end: float,
                   every_minutes: int, next_at: float, state: str = "active") -> int:
        now = self.clock()
        cur = self._run(
            "INSERT INTO follow_checks(item_key, kind, refs, fields, from_offset, until_offset, window_start, window_end, "
            "every_minutes, next_at, state, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item_key, kind, json.dumps(refs or {}), json.dumps(fields or []), from_offset, until_offset,
             window_start, window_end, every_minutes, next_at, state, now),
        )
        return int(cur.lastrowid or 0)

    def get_check(self, check_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM follow_checks WHERE id = ?", (check_id,))
        return self._check_dict(row) if row else None

    def list_checks(self, item_key: str | None = None, state: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM follow_checks WHERE 1=1"
        params: list[Any] = []
        if item_key:
            sql += " AND item_key = ?"
            params.append(item_key)
        if state:
            sql += " AND state = ?"
            params.append(state)
        sql += " ORDER BY id"
        return [self._check_dict(r) for r in self._all(sql, tuple(params))]

    def due_checks(self, now: float) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM follow_checks WHERE state = 'active' AND window_start <= ? AND window_end >= ? AND next_at <= ? "
            "ORDER BY next_at",
            (now, now, now),
        )
        return [self._check_dict(r) for r in rows]

    def expired_checks(self, now: float) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM follow_checks WHERE state = 'active' AND window_end < ?", (now,))
        return [self._check_dict(r) for r in rows]

    def update_check(self, check_id: int, **fields: Any) -> bool:
        if not fields:
            return False
        sets = []
        params = []
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            if k in ("refs", "fields"):
                params.append(json.dumps(v))
            else:
                params.append(v)
        params.append(check_id)
        cur = self._run(f"UPDATE follow_checks SET {', '.join(sets)} WHERE id = ?", tuple(params))
        return cur.rowcount > 0

    def delete_checks_for_item(self, item_key: str) -> None:
        self._run("DELETE FROM follow_checks WHERE item_key = ?", (item_key,))

    def _check_dict(self, row: Any) -> dict[str, Any]:
        d = _to_dict(row, CHECK_COLS)
        d["refs"] = json.loads(d["refs"] or "{}") if isinstance(d.get("refs"), str) else (d.get("refs") or {})
        d["fields"] = json.loads(d["fields"] or "[]") if isinstance(d.get("fields"), str) else (d.get("fields") or [])
        return d

    def stop_item(self, key: str) -> bool:
        cur = self._run("UPDATE follow_items SET state = 'stopped', updated_at = ? WHERE key = ?", (self.clock(), key))
        if cur.rowcount > 0:
            self._run("UPDATE follow_checks SET state = 'stopped' WHERE item_key = ? AND state = 'active'", (key,))
            return True
        return False

    # ---- readings --------------------------------------------------------------
    def save_reading(self, check_id: int, at: float, ok: bool, fields: dict[str, Any], detail: str = "") -> int:
        cur = self._run(
            "INSERT INTO follow_readings(check_id, at, ok, fields, detail) VALUES(?, ?, ?, ?, ?)",
            (check_id, at, 1 if ok else 0, json.dumps(fields or {}), detail),
        )
        return int(cur.lastrowid or 0)

    def last_reading(self, check_id: int, ok_only: bool = True) -> dict[str, Any] | None:
        sql = "SELECT * FROM follow_readings WHERE check_id = ?"
        if ok_only:
            sql += " AND ok = 1"
        sql += " ORDER BY at DESC, id DESC LIMIT 1"
        row = self._one(sql, (check_id,))
        if not row:
            return None
        d = _to_dict(row, READING_COLS)
        d["fields"] = json.loads(d["fields"] or "{}") if isinstance(d.get("fields"), str) else (d.get("fields") or {})
        d["ok"] = bool(d["ok"])
        return d

    # ---- usage -----------------------------------------------------------------
    def count_usage(self, source: str, month: str) -> int:
        self._run(
            "INSERT INTO follow_usage(source, month, calls) VALUES(?, ?, 1) "
            "ON CONFLICT(source, month) DO UPDATE SET calls = calls + 1",
            (source, month),
        )
        return self.get_usage(source, month)

    def get_usage(self, source: str, month: str) -> int:
        row = self._one("SELECT calls FROM follow_usage WHERE source = ? AND month = ?", (source, month))
        if not row:
            return 0
        return int(row[0]) if isinstance(row, (tuple, list)) or hasattr(row, "__getitem__") else int(row["calls"])

    def notified_allowance(self, source: str, month: str) -> bool:
        row = self._one("SELECT notified_allowance FROM follow_usage WHERE source = ? AND month = ?", (source, month))
        if not row:
            return False
        return bool(row[0]) if isinstance(row, (tuple, list)) or hasattr(row, "__getitem__") else bool(row["notified_allowance"])

    def set_notified_allowance(self, source: str, month: str, notified: bool = True) -> None:
        self._run(
            "INSERT INTO follow_usage(source, month, calls, notified_allowance) VALUES(?, ?, 0, ?) "
            "ON CONFLICT(source, month) DO UPDATE SET notified_allowance = excluded.notified_allowance",
            (source, month, 1 if notified else 0),
        )


class _DummyLock:
    def __enter__(self) -> None:
        pass

    def __exit__(self, *args: Any) -> None:
        pass
