"""The operator's events on a site copy (site_only.py): what the operator's own main copy should hear about, kept for a while so a
main copy that was down catches up by polling GET /api/operator/events?after=<id> (operator_site.py).

Only what a notification needs is kept, and no address of a visitor: a sign-up's email and phone model, a beta request's email and
products, a report's id and reason, the Kelo address pool's numbers. Rows older than KEEP_DAYS go whenever an event is added.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from .store import Store

log = logging.getLogger("uvicorn.error")
EVENT_KINDS = ("tester_signup", "beta_request", "report", "names_busy", "names_pool")
KEEP_DAYS = 30
SCHEMA = """
CREATE TABLE IF NOT EXISTS operator_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}');
"""


class OperatorEvents:
    def __init__(self, store: Store, wall: Callable[[], float] = time.time) -> None:
        store._db.executescript(SCHEMA)
        self.store, self.wall = store, wall

    def add(self, kind: str, data: dict[str, Any]) -> int:
        """Keeps one event; returns its id (0 when it could not be kept). Never raises into the caller (the sign-up or report
        that caused it is kept either way); only the error's type is logged."""
        try:
            self.store._run("DELETE FROM operator_events WHERE at < ?", (self.wall() - KEEP_DAYS * 86400,))
            cur = self.store._run("INSERT INTO operator_events(at, kind, data) VALUES(?,?,?)",
                                  (self.wall(), kind, json.dumps(data, ensure_ascii=False)))
            return int(cur.lastrowid or 0)
        except Exception as e:  # noqa: BLE001
            log.warning("operator events: could not keep %s (%s)", kind, type(e).__name__)
            return 0

    def since(self, after: int, limit: int = 100) -> dict[str, Any]:
        """The events with an id above `after`, oldest first (at most `limit`); `last` is the newest id kept (0 when none)."""
        rows = self.store._all("SELECT * FROM operator_events WHERE id > ? ORDER BY id LIMIT ?", (max(0, after), max(1, min(limit, 500))))
        newest = self.store._all("SELECT COALESCE(MAX(id), 0) AS n FROM operator_events", ())[0]["n"]
        events = [{"id": r["id"], "at": r["at"], "kind": r["kind"], "data": json.loads(r["data"] or "{}")} for r in rows]
        return {"events": events, "last": int(newest)}
