"""What Kelo carries for the owner as one list (owner 2026-11-20, D15): the work items Kelo took on (goals.py, kind work) and the follow
items gathered from the calendar, mail and chat (follow/). The same real thing shows once: a follow item linked to a work item
(table work_follow) is shown inside that work item. A link comes only from structure: the agent naming the follow item (work_create /
work_log follow_key), the same source id (refs.event, refs.mail), the same flight and day, the same tracking number, or the nightly
look-back's same_as key. Titles never link.

Owner decision 18 (option 나): every follow item becomes a work item once, at the first start after this change (migrate), and every
follow item found later becomes one too (match_follow), keeping its dates, source and state (the follow item is unchanged). Owner
decision 17: the list is shown on the goal screen where "Kelo가 맡은 일" was; open items come nearest date first, undated ones after."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from .follow.items import UpcomingItem, same_thing
from .i18n import N_

SCHEMA = """
CREATE TABLE IF NOT EXISTS work_follow (
    work_id INTEGER NOT NULL, follow_key TEXT NOT NULL UNIQUE, how TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (work_id, follow_key));
"""
STARTED_FROM = N_("Kelo started following this from {source}")
REF_KEYS = ("flight", "date", "tracking", "booking")


class CarriedLinks:
    def __init__(self, store: Any, follow: Any, book: Any = None, clock: Any = time.time) -> None:
        self.store, self.follow, self.book, self.clock = store, follow, book, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    # ---- links -------------------------------------------------------------------------------------------------
    def link(self, work_id: int, follow_key: str, how: str) -> bool:
        """Links one follow item to a work item. False when it is linked to another work item already."""
        have = self.work_of(follow_key)
        if have is not None:
            return have == int(work_id)
        self.store._run("INSERT OR IGNORE INTO work_follow(work_id, follow_key, how, created_at) VALUES(?,?,?,?)",
                        (int(work_id), follow_key, how, self.clock()))
        return True

    def unlink_follow(self, follow_key: str) -> None:
        """The follow item is gone: its link goes with it (the work item stays)."""
        self.store._run("DELETE FROM work_follow WHERE follow_key = ?", (follow_key,))

    def of_work(self, work_id: int) -> list[str]:
        return [r["follow_key"] for r in self.store._all("SELECT follow_key FROM work_follow WHERE work_id = ? ORDER BY created_at, follow_key",
                                                         (int(work_id),))]

    def work_of(self, follow_key: str) -> int | None:
        rows = self.store._all("SELECT work_id FROM work_follow WHERE follow_key = ?", (follow_key,))
        return int(rows[0]["work_id"]) if rows else None

    # ---- structure -------------------------------------------------------------------------------------------------
    def _works(self) -> list[dict[str, Any]]:
        return self.store._all("SELECT * FROM goals WHERE kind = 'work' ORDER BY id")

    @staticmethod
    def _refs(row: dict[str, Any]) -> dict[str, str]:
        try:
            got = json.loads(row.get("refs") or "{}")
        except (TypeError, ValueError):
            return {}
        return {k: str(v) for k, v in got.items() if isinstance(v, (str, int, float)) and str(v)} if isinstance(got, dict) else {}

    def same(self, work: dict[str, Any], follow: dict[str, Any]) -> bool:
        """The same thing by structure: the same event or mail id, the same flight and day, or the same tracking number (same_thing)."""
        refs = self._refs(work)
        keys = {follow["key"], *(follow.get("aliases") or [])}
        if refs.get("event") in keys or refs.get("mail") in keys:
            return True
        # the work item has no start time of its own: far in the past, so only the flight and tracking rules of same_thing can fire
        a = UpcomingItem(key=f"work:{work['id']}", kind="other", title=str(work.get("title") or ""), starts_at=-1e12, ends_at=None,
                         place="", refs=refs, source="goal")
        b = UpcomingItem(key=follow["key"], kind=str(follow.get("kind") or ""), title=str(follow.get("title") or ""),
                         starts_at=float(follow.get("starts_at") or 0), ends_at=None, place="",
                         refs={k: str(v) for k, v in (follow.get("refs") or {}).items() if isinstance(v, (str, int, float))},
                         source=str(follow.get("source") or ""))
        return same_thing(a, b)

    def match_work(self, work_id: int) -> list[str]:
        """After a work item is made or its refs change: every open follow item that is the same thing is linked to it."""
        work = self.store.goal(int(work_id))
        if not work or work.get("kind") != "work":
            return []
        out = []
        for f in self.follow.list_items():
            if f.get("state") == "stopped" or self.work_of(f["key"]) is not None:
                continue
            if self.same(work, f) and self.link(int(work_id), f["key"], "ref"):
                out.append(f["key"])
        return out

    def match_follow(self, follow_key: str) -> int | None:
        """A follow item was found: linked to the open work item that is the same thing, else it becomes a work item of its own (decision
        18). Returns the work item's id."""
        have = self.work_of(follow_key)
        if have is not None:
            return have
        f = self.follow.get_item(follow_key)
        if f is None:
            return None
        for w in self._works():
            if w.get("status") != "done" and self.same(w, f) and self.link(int(w["id"]), follow_key, "ref"):
                return int(w["id"])
        return self.convert(follow_key)

    def convert(self, follow_key: str) -> int | None:
        """A follow item becomes a work item of its own: its outside ids are its refs, its source is in the first entry (Kelo started
        following it from there). The follow item itself does not change (its dates, source and state stay)."""
        f = self.follow.get_item(follow_key)
        if f is None or self.book is None:
            return None
        refs = {k: str(v) for k, v in (f.get("refs") or {}).items() if k in REF_KEYS and v}
        if follow_key.startswith("cal:"):
            refs["event"] = follow_key
        elif follow_key.startswith("mail:"):
            refs["mail"] = follow_key
        source = str(f.get("source") or "")
        day = refs.get("date") or datetime.fromtimestamp(float(f.get("starts_at") or 0), timezone.utc).date().isoformat()
        w = self.book.create(str(f.get("title") or follow_key), kind="work", summary=f"{day} · {source}", origin="agent")
        self.book.merge_refs(int(w["id"]), refs)
        self.link(int(w["id"]), follow_key, "ref")
        self.book.log_t(int(w["id"]), STARTED_FROM, "note", "agent", source=source)
        return int(w["id"])

    def migrate(self) -> None:
        """Once at start (meta carried:v1): the work items with refs take the follow items they name, then every other follow item not yet
        linked becomes a work item (decision 18)."""
        if self.store.meta("carried:v1"):
            return
        for w in self._works():
            if self._refs(w):
                self.match_work(int(w["id"]))
        for f in self.follow.list_items():
            if self.work_of(f["key"]) is None and f.get("state") != "stopped":
                self.convert(f["key"])
        self.store.set_meta("carried:v1", "1")

    # ---- the one list ----------------------------------------------------------------------------------------------
    def _starts(self, work: dict[str, Any], linked: list[dict[str, Any]]) -> tuple[float | None, bool]:
        open_ = [f for f in linked if f.get("state") not in ("done", "stopped") and f.get("starts_at") is not None]
        if open_:
            first = min(open_, key=lambda f: float(f["starts_at"]))
            return float(first["starts_at"]), bool(first.get("all_day"))
        date = self._refs(work).get("date", "")
        try:
            return datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp(), True
        except ValueError:
            return None, False

    def items(self) -> list[dict[str, Any]]:
        """Every work item with the follow items linked to it: open ones first, nearest date first, undated after; done ones last."""
        from .follow.api import item_view
        out = []
        for w in self._works():
            wid = int(w["id"])
            linked = [item_view(self.follow, f) for f in (self.follow.get_item(k) for k in self.of_work(wid)) if f]
            starts, all_day = self._starts(w, linked)
            done = w.get("status") == "done"
            public = self.book.public(self.store.goal(wid)) if self.book is not None else dict(w)
            out.append({"key": f"work:{wid}", "title": w.get("title") or "", "state": "done" if done else "open",
                        "starts_at": starts, "all_day": all_day, "work": public, "follow": linked})
        out.sort(key=lambda c: (c["state"] == "done", c["starts_at"] is None, c["starts_at"] or 0.0, int(c["key"][5:])))
        return out

    def follow_of(self, follow_key: str) -> dict[str, Any] | None:
        return self.follow.get_item(follow_key)


def carried_routes(app: Any, links: CarriedLinks, auth: Any) -> None:
    """GET /api/carried: {"items": [Carried]} (see items())."""
    from fastapi import Depends

    @app.get("/api/carried", dependencies=[Depends(auth)])
    async def get_carried() -> dict[str, Any]:
        return {"items": links.items()}
