"""The checklist: short things the user (or the agent) keeps in CHECKLIST.md in the agent's files, one per line ("- tell me if anything
on today's calendar starts within 30 minutes"). It is part of "Kelo tells you first" (proactive.py), not a feature of its own:

- it runs only while the proactive switch is on, the file has at least one item, it is waking hours (the proactive settings'
  wake_start..wake_end in the profile's time zone) and today's proactive cap (daily_total) still has room — so an empty file never
  costs a model call;
- it goes as often as the proactive pass (the proactive `every_minutes`, an internal job: Scheduler.add_every), as an unattended turn
  of the main agent with its tools;
- the turn is told the user is away and to reply exactly NO_REPLY when nothing needs attention: then nothing is said and no push is
  sent (the run is still in the task log, source "checklist"); otherwise its short answer joins the chat with a notification (both made
  in server.py) and counts as one 오늘 message toward the proactive daily cap (a proactive log row with source "checklist");
- a run still going is never started again; the last result is kept (meta "checklist:last_run").

CHECKLIST API (contracts next to serialize() in server.py):
GET /api/checklist                {"enabled" (= the proactive switch), "every_minutes" (= the proactive pass), "text", "items": n,
                                   "list": [item], "path": "CHECKLIST.md", "last_run": LastRun|null, "next_run_at": unix|null,
                                   "running": bool}
PUT /api/checklist                {"items"? [item] (replaces the list lines, keeps the rest of the file), "text"? (the whole file,
                                   ≤ 20,000 chars)} -> same as GET. "enabled" / "every_minutes" are still accepted and ignored: the
                                   switch and the pace are the proactive ones (PUT /api/proactive).
POST /api/checklist/run           run it now (even outside waking hours; still not when empty) -> LastRun; 409 while one runs
LastRun: {"at", "status": done|quiet|failed|skipped, "reason", "task_id"|null}
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .home import CHECKLIST, Home, HomeError
from .i18n import t

DEFAULT_EVERY_MINUTES = 120    # without a proactive pace (tests): the proactive default
MAX_TEXT = 20_000
MAX_ITEMS = 30
MAX_ITEM = 300
ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?:\[[ xX]?\]\s*)?(.+?)\s*$")

NOTE = ("[Periodic check from CHECKLIST.md, not from the user. The user is NOT watching. Go through each item below quickly, with your "
        "tools where needed. If nothing needs the user's attention right now, reply exactly NO_REPLY. Otherwise say only what needs "
        "attention, in a few short lines in the user's language. Follow NOTIFY_PREFS.md. Do not ask questions.]")

StartFn = Callable[[str], Awaitable[dict[str, Any]]]   # runs the checks (led by NOTE) as a turn; returns {"task_id", "status", "quiet"}


def with_items(text: str, items: list[str]) -> str:
    """The file with its list lines replaced by `items` ("- item" each); headings and notes stay where they are."""
    keep = [line for line in (text or "").splitlines() if not ITEM.match(line)]
    while keep and not keep[-1].strip():
        keep.pop()
    clean = [re.sub(r"\s+", " ", i).strip()[:MAX_ITEM] for i in items]
    lines = [f"- {i}" for i in clean if re.search(r"\w", i)][:MAX_ITEMS]
    return "\n".join([*keep, *([""] if keep and lines else []), *lines]) + "\n"


def checklist_items(text: str) -> list[str]:
    """The checks: list lines with words in them ("- ...", "* ...", "1. ..."). Headings, notes and empty bullets do not count."""
    out = []
    for line in (text or "").splitlines():
        m = ITEM.match(line)
        if m and re.search(r"\w", m.group(1)):
            out.append(m.group(1)[:MAX_ITEM])
    return out[:MAX_ITEMS]


class Checklist:
    def __init__(self, store: Any, home: Home, start: StartFn | None = None, *, clock: Callable[[], float] = time.time,
                 waking: Callable[[float], bool] = lambda _t: True, switch: Callable[[], bool] = lambda: True,
                 pace: Callable[[], float] = lambda: DEFAULT_EVERY_MINUTES * 60.0, room: Callable[[], bool] = lambda: True,
                 on_said: Callable[[int | None], None] | None = None, hold: Callable[[], str | None] = lambda: None):
        """switch / pace / room / on_said / hold come from the proactive settings: on or off, how often (seconds), whether today's cap
        has room, what to count when a run said something, and why proactive messages are held back now (the unanswered guard), if
        they are."""
        self.store, self.home, self.start, self.clock, self.waking = store, home, start, clock, waking
        self.switch, self.pace, self.room, self.on_said, self.hold = switch, pace, room, on_said, hold
        self.lock = asyncio.Lock()
        self.last_started: float | None = None
        home.ensure_side_files()

    # ------------------------------------------------------------------ settings
    def config(self) -> dict[str, Any]:
        return {"enabled": bool(self.switch()), "every_minutes": int(self.period() // 60)}

    def period(self) -> float:
        try:
            return max(60.0, float(self.pace()))
        except Exception:  # noqa: BLE001
            return DEFAULT_EVERY_MINUTES * 60.0

    def text(self) -> str:
        p = self.home.root / CHECKLIST
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() and not p.is_symlink() else ""

    def items(self) -> list[str]:
        return checklist_items(self.text())

    def last_run(self) -> dict[str, Any] | None:
        try:
            v = json.loads(self.store.meta("checklist:last_run") or "null")
        except ValueError:
            return None
        return v if isinstance(v, dict) else None

    def public(self, next_run_at: float | None = None) -> dict[str, Any]:
        cfg = self.config()
        items = self.items()
        return {**cfg, "text": self.text(), "items": len(items), "list": items, "path": CHECKLIST, "last_run": self.last_run(),
                "next_run_at": next_run_at if cfg["enabled"] else None, "running": self.lock.locked()}

    def update(self, *, text: str | None = None, items: list[str] | None = None, **_retired: Any) -> dict[str, Any]:
        """A new file (text) or a new list (items, the rest of the file kept). `enabled` / `every_minutes` are retired (the proactive
        switch and pace) and ignored."""
        if items is not None:
            text = with_items(self.text(), items)
        if text is not None:
            if len(text) > MAX_TEXT:
                raise ValueError(f"the checklist can be at most {MAX_TEXT} characters")
            self.home.write(CHECKLIST, text if text.endswith("\n") or not text else text + "\n", by="user")
        return self.public()

    # ------------------------------------------------------------------ running
    def _record(self, status: str, reason: str = "", task_id: int | None = None) -> dict[str, Any]:
        out = {"at": self.clock(), "status": status, "reason": reason, "task_id": task_id}
        if status != "skipped" or not self.last_run():   # a quiet skip does not hide the last real result
            self.store.set_meta("checklist:last_run", json.dumps(out, ensure_ascii=False))
        return out

    async def tick(self) -> dict[str, Any]:
        """What the internal job calls every period."""
        return await self.run(force=False)

    async def run(self, *, force: bool = False) -> dict[str, Any]:
        if self.lock.locked():
            return {"at": self.clock(), "status": "busy", "reason": "a check is already running", "task_id": None}
        if not force and not self.switch():
            return {"at": self.clock(), "status": "skipped", "reason": t("It is turned off"), "task_id": None}
        items = self.items()
        if not items:
            return {"at": self.clock(), "status": "skipped", "reason": t("There is nothing to check"), "task_id": None}
        if not force and not self.waking(self.clock()):
            return {"at": self.clock(), "status": "skipped", "reason": t("It is quiet time"), "task_id": None}
        held = None if force else self.hold()
        if held:
            return {"at": self.clock(), "status": "skipped", "reason": held, "task_id": None}
        if not force and not self.room():
            return {"at": self.clock(), "status": "skipped", "reason": t("Today's limit is reached"), "task_id": None}
        if self.start is None:
            return self._record("failed", "the agent is not available")
        async with self.lock:
            self.last_started = self.clock()
            body = "Checks:\n" + "\n".join(f"- {i}" for i in items)
            try:
                out = await self.start(body)   # the server adds NOTE (the user is away; NO_REPLY when nothing needs attention)
            except Exception as e:  # noqa: BLE001
                return self._record("failed", f"{type(e).__name__}: {str(e)[:160]}")
            status = out.get("status") or "failed"
            if status == "done":
                if not out.get("quiet") and self.on_said:
                    try:
                        self.on_said(out.get("task_id"))
                    except Exception:  # noqa: BLE001
                        pass
                return self._record("quiet" if out.get("quiet") else "done", "", out.get("task_id"))
            return self._record("failed", str(out.get("error") or status)[:200], out.get("task_id"))


# ---------------------------------------------------------------------------------------------------------------- API
class ChecklistIn(BaseModel):
    enabled: bool | None = None          # retired: the proactive switch (accepted, ignored)
    every_minutes: int | None = None     # retired: the proactive pace (accepted, ignored)
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    items: list[str] | None = Field(default=None, max_length=MAX_ITEMS)


def checklist_routes(app: FastAPI, checklist: Checklist, auth: Callable[..., Any], next_run: Callable[[], float | None] = lambda: None) -> None:
    """The CHECKLIST API (contracts in this module's docstring and next to serialize())."""

    @app.get("/api/checklist", dependencies=[Depends(auth)])
    async def get_checklist() -> dict[str, Any]:
        return checklist.public(next_run())

    @app.put("/api/checklist", dependencies=[Depends(auth)])
    async def put_checklist(body: ChecklistIn) -> dict[str, Any]:
        try:
            checklist.update(text=body.text, items=body.items)
        except (ValueError, HomeError) as e:
            raise HTTPException(422, str(e)) from None
        return checklist.public(next_run())

    @app.post("/api/checklist/run", dependencies=[Depends(auth)])
    async def run_checklist() -> dict[str, Any]:
        if checklist.lock.locked():
            raise HTTPException(409, "a check is already running")
        return await checklist.run(force=True)
