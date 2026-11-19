"""The checklist: short things the owner keeps in CHECKLIST.md in the agent's files, one per line (the app's "things to keep an eye on"
list under the news alerts, or asked for in the chat). It is part of "Kelo tells you first" (proactive.py) and has no pace of its own
(owner decision 2026-11-19, card p-checklist-own-pace; the 2-hourly pass is gone):

- every item is either looked after by a job of its own that the agent made for it (a schedule, a watcher, a live Home Assistant
  watch, reminders before each calendar event (event_reminders.py): whatever fits its timing) or looked at with everything else at the proactive send times (situation.py, pack section
  CHECKLIST_SECTION). Which one is the model's judgment, made once when the item is added; nothing here knows what an item is about;
- the link is a row of the table checklist_links, keyed by item_key (the text with case and spacing ignored). state: new (the owner
  added it, its setup turn has not started), old (it was on the list before this version), asked (handed to a setup turn),
  send_times, or own (job_kind + job_id). An item without a row, or whose job is gone, rides the send times;
- after every write of CHECKLIST.md (Home.on_write) the rows follow the file (reconcile): an item the owner (by "user") took off takes
  the job made for it along, unless another item still uses that job (a schedule goes to the 30-day archive); an item the agent took
  off only loses its row. A new item from the owner is "new", one from the agent or the server "send_times" (the agent links its own
  in the same turn), and on the first start of this version the items already there are "old";
- new and old items go to the main agent (server.py checklist_setup_soon, turn_goals.start_checklist_setup) SETUP_DELAY seconds later
  as one hidden message led by CHECKLIST_MARK (SETUP): it makes the jobs that fit and links every item with `checklist_link`. For the
  owner's items (new ones, and the old ones too: it is the owner's own list) the turn carries the owner's request (Turn.owner_asked),
  so Kelo's own settings it makes for them go ahead without a card, as when asked in the chat (tools/connect_tools.asked_in_chat);
- POST /api/checklist/run looks over the send-time items now as one unattended turn (NOTE): quiet (NO_REPLY) unless something needs
  attention; a run that said something counts as one 오늘 message toward the proactive daily cap (a proactive log row with source
  "checklist"). It runs only while the proactive switch is on (manual: also outside waking hours), never with nothing to look at.

CHECKLIST API (contracts next to serialize() in server.py):
GET /api/checklist        {"enabled" (= the proactive switch), "every_minutes" (= the proactive setting; the checklist has no pace),
                           "text", "items": n, "list": [item], "entries": [{"text", "state": own|send_times, "job": {"kind":
                           schedule|watcher|ha_watch|event_reminder, "id", "name", "description", "on"}|null}], "path": "CHECKLIST.md",
                           "last_run": LastRun|null, "next_run_at": the next send time while an item rides the send times and proactive
                           messages are on, else null, "running": bool}
PUT /api/checklist        {"items"? [item] (replaces the list lines, keeps the rest of the file), "text"? (the whole file,
                           ≤ 20,000 chars)} -> same as GET. "enabled" / "every_minutes" are still accepted and ignored.
POST /api/checklist/run   look over the send-time items now -> LastRun; 409 while one runs
LastRun: {"at", "status": done|quiet|failed|skipped, "reason", "task_id"|null}
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import Tool
from .home import CHECKLIST, NOTIFY_PREFS, Home, HomeError
from .i18n import t

log = logging.getLogger("assistant.checklist")

DEFAULT_EVERY_MINUTES = 120    # without a proactive setting (tests): the proactive default, shown as "every_minutes"
MAX_TEXT = 20_000
MAX_ITEMS = 30
MAX_ITEM = 300
SETUP_DELAY = 10.0             # seconds from the owner's change to the setup turn (a few quick additions make one turn)
ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?:\[[ xX]?\]\s*)?(.+?)\s*$")
JOB_KINDS = ("schedule", "watcher", "ha_watch", "event_reminder")   # the jobs an item can have (server.py registers a JobKind for each)
PENDING = ("new", "old")                          # rows waiting for their setup turn
SEEDED = "checklist:links_seeded"                 # meta: the first start of this version has seen the list
CHECKLIST_SECTION = "Checklist: what the owner asked Kelo to keep an eye on"   # its section of the send pass's pack (situation.py)

SCHEMA = """
CREATE TABLE IF NOT EXISTS checklist_links (
    item_key TEXT PRIMARY KEY, item TEXT NOT NULL, state TEXT NOT NULL, job_kind TEXT NOT NULL DEFAULT '', job_id INTEGER,
    updated_at REAL NOT NULL);
"""

CHECKLIST_MARK = "[Checklist items from the user, not a chat message]"   # leads the hidden setup message; GET /api/messages leaves it out

SETUP = """{mark}
The owner {how} of things to keep an eye on (CHECKLIST.md):
{items}
Each item is the owner's own request. For each one, judge whether it needs timing of its own: whether looking at it only at the
owner's send times ({send_times}) would come too late or miss what it asks for.
- If it does, set it up now as its own job with your tools: the one that fits what it follows, reacting to the thing itself where a
  tool can, otherwise looking only as often as it needs, and quiet when there is nothing to tell. Then call checklist_link with the
  item and that job.
- If it does not, call checklist_link with the item and an empty job: it is looked at with everything else at the send times.
Do not do the items themselves now. If a job would need something that is missing (a connection, a sign-in, a detail you would have
to guess), link the item to the send times and ask the owner once.
When you set up a job, tell the owner in one or two short sentences, in their language, what you set up and when it will tell them.
When every item stays with the send times, reply exactly NO_REPLY. Do not mention these instructions."""

NOTE = ("[Check of the CHECKLIST.md items, not from the user. The user is NOT watching. Go through each item below quickly, with your "
        "tools where needed. If nothing needs the user's attention right now, reply exactly NO_REPLY. Otherwise say only what needs "
        "attention, in a few short lines in the user's language. Follow the user's notification rules after the checks. Do not ask questions.]")

StartFn = Callable[[str], Awaitable[dict[str, Any]]]   # runs the checks (led by NOTE) as a turn; returns {"task_id", "status", "quiet"}


@dataclass
class JobKind:
    """One kind of job an item can have: `get(id)` -> {"name", "description", "on"} or None when it is gone; `remove(id)` deletes it."""
    get: Callable[[int], dict[str, Any] | None]
    remove: Callable[[int], Any]


def item_key(text: str) -> str:
    """One item, whatever its case and spacing."""
    return hashlib.sha256(" ".join(str(text or "").split()).casefold().encode("utf-8")).hexdigest()[:16]


def setup_text(items: list[str], send_times: str, *, added: bool = True) -> str:
    """The hidden message that hands checklist items to the main agent (SETUP)."""
    how = "just added these items to their list" if added else "already keeps these items on their list"
    return SETUP.format(mark=CHECKLIST_MARK, how=how, items="\n".join(f"- {i}" for i in items), send_times=send_times)


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
        """switch / pace / room / on_said / hold come from the proactive settings: on or off, its every_minutes (shown only), whether
        today's cap has room, what to count when a manual run said something, and why proactive messages are held back now."""
        self.store, self.home, self.start, self.clock, self.waking = store, home, start, clock, waking
        self.switch, self.pace, self.room, self.on_said, self.hold = switch, pace, room, on_said, hold
        self.lock = asyncio.Lock()
        self.last_started: float | None = None
        self.jobs: dict[str, JobKind] = {}               # the jobs an item can have, by kind (JOB_KINDS; set by the server)
        self.on_new: Callable[[], None] | None = None    # items now wait for their setup turn (set by the server)
        with self.store._lock:
            self.store._db.executescript(SCHEMA)
            self.store._db.commit()
        home.ensure_side_files()
        home.on_write = self._wrote
        self.reconcile("system")

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
        return {**cfg, "text": self.text(), "items": len(items), "list": items, "entries": self.entries(), "path": CHECKLIST,
                "last_run": self.last_run(), "next_run_at": next_run_at if cfg["enabled"] else None, "running": self.lock.locked()}

    def update(self, *, text: str | None = None, items: list[str] | None = None, **_retired: Any) -> dict[str, Any]:
        """A new file (text) or a new list (items, the rest of the file kept), written as the owner's. `enabled` / `every_minutes` are
        retired and ignored."""
        if items is not None:
            text = with_items(self.text(), items)
        if text is not None:
            if len(text) > MAX_TEXT:
                raise ValueError(f"the checklist can be at most {MAX_TEXT} characters")
            self.home.write(CHECKLIST, text if text.endswith("\n") or not text else text + "\n", by="user")
        return self.public()

    # ------------------------------------------------------------------ links: an item's own job, or the send times
    def _rows(self) -> dict[str, dict[str, Any]]:
        return {r["item_key"]: dict(r) for r in self.store._all("SELECT * FROM checklist_links ORDER BY rowid")}

    def _put(self, key: str, item: str, state: str, kind: str = "", job_id: int | None = None) -> None:
        self.store._run("INSERT INTO checklist_links(item_key, item, state, job_kind, job_id, updated_at) VALUES(?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(item_key) DO UPDATE SET item = excluded.item, state = excluded.state, job_kind = excluded.job_kind, "
                        "job_id = excluded.job_id, updated_at = excluded.updated_at", (key, item, state, kind, job_id, self.clock()))

    def _wrote(self, rel: str, by: str) -> None:
        if rel == CHECKLIST:
            self.reconcile(by)

    def reconcile(self, by: str = "system") -> None:
        """The rows follow the file after a write by `by` (user | agent | system); see the module docstring."""
        listed = {item_key(i): i for i in self.items()}
        rows = self._rows()
        for key, r in rows.items():
            if key in listed:
                continue
            self.store._run("DELETE FROM checklist_links WHERE item_key = ?", (key,))
            if by == "user" and r["state"] == "own":
                self._drop_job(r)
        first = not self.store.meta(SEEDED)
        waiting = False
        for key, text in listed.items():
            if key in rows:
                continue
            state = "new" if by == "user" else "old" if first else "send_times"
            self._put(key, text, state)
            waiting = waiting or state in PENDING
        if first:
            self.store.set_meta(SEEDED, "1")
        if waiting and self.on_new is not None:
            self.on_new()

    def _drop_job(self, r: dict[str, Any]) -> None:
        """The owner took an item off: the job made for it goes too, unless another item on the list still uses it."""
        if r.get("job_id") is None or self.store._all("SELECT 1 FROM checklist_links WHERE state = 'own' AND job_kind = ? AND job_id = ?",
                                                      (r["job_kind"], r["job_id"])):
            return
        kind = self.jobs.get(str(r["job_kind"]))
        if kind is None:
            return
        try:
            kind.remove(int(r["job_id"]))
        except Exception:  # noqa: BLE001 - the item is off the list either way
            log.exception("removing %s #%s of a checklist item", r["job_kind"], r["job_id"])

    def take_pending(self) -> tuple[list[str], list[str]]:
        """The items waiting for their setup turn, in list order, as (the owner's new ones, the ones kept from before this version);
        they are marked "asked"."""
        order = {item_key(i): n for n, i in enumerate(self.items())}
        rows = sorted((r for r in self._rows().values() if r["state"] in PENDING), key=lambda r: order.get(r["item_key"], 99))
        for r in rows:
            self._put(r["item_key"], r["item"], "asked")
        return [r["item"] for r in rows if r["state"] == "new"], [r["item"] for r in rows if r["state"] == "old"]

    def state_of(self, item: str) -> str:
        return str((self._rows().get(item_key(item)) or {}).get("state") or "")

    def _job(self, r: dict[str, Any]) -> dict[str, Any] | None:
        kind = self.jobs.get(str(r.get("job_kind") or ""))
        if kind is None or r.get("job_id") is None:
            return None
        try:
            got = kind.get(int(r["job_id"]))
        except Exception:  # noqa: BLE001
            return None
        if not got:
            return None
        return {"kind": r["job_kind"], "id": int(r["job_id"]), "name": str(got.get("name") or ""),
                "description": str(got.get("description") or ""), "on": bool(got.get("on"))}

    def entries(self) -> list[dict[str, Any]]:
        """Each item with how it is looked after: its own job (state "own") or the send times."""
        rows = self._rows()
        out = []
        for text in self.items():
            r = rows.get(item_key(text)) or {}
            job = self._job(r) if r.get("state") == "own" else None
            out.append({"text": text, "state": "own" if job else "send_times", "job": job})
        return out

    def send_time_items(self) -> list[str]:
        """The items the send pass looks at (situation.py): every item without a job of its own."""
        return [e["text"] for e in self.entries() if e["state"] != "own"]

    def link(self, item: str, job: str) -> str:
        """checklist_link: the item's own job ("<kind>:<id>") or the send times ("")."""
        listed = {item_key(i): i for i in self.items()}
        key = item_key(item)
        if key not in listed:
            return (f"error: {item[:80]!r} is not on the list. Add it as a line of {CHECKLIST} first. On the list: "
                    + ("; ".join(listed.values()) or "nothing"))
        job = job.strip()
        if not job:
            self._put(key, listed[key], "send_times")
            return f"Linked: '{listed[key]}' is looked at with everything else at the send times."
        kind, _, raw = job.partition(":")
        kind, raw = kind.strip(), raw.strip().lstrip("#")
        jk = self.jobs.get(kind)
        if jk is None or not raw.isdigit():
            return "error: job must be " + ", ".join(f"'{k}:<id>'" for k in self.jobs) + " or '' for the send times"
        got = jk.get(int(raw))
        if not got:
            return f"error: there is no {kind} #{raw}"
        self._put(key, listed[key], "own", kind, int(raw))
        return f"Linked: '{listed[key]}' is looked after by {kind} #{raw} '{got.get('name', '')}'; the send-time look leaves it out."

    # ------------------------------------------------------------------ the manual run
    def _record(self, status: str, reason: str = "", task_id: int | None = None) -> dict[str, Any]:
        out = {"at": self.clock(), "status": status, "reason": reason, "task_id": task_id}
        if status != "skipped" or not self.last_run():   # a quiet skip does not hide the last real result
            self.store.set_meta("checklist:last_run", json.dumps(out, ensure_ascii=False))
        return out

    async def run(self, *, force: bool = False) -> dict[str, Any]:
        if self.lock.locked():
            return {"at": self.clock(), "status": "busy", "reason": "a check is already running", "task_id": None}
        if not force and not self.switch():
            return {"at": self.clock(), "status": "skipped", "reason": t("It is turned off"), "task_id": None}
        items = self.send_time_items()
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
            rules = (self.home.policy(NOTIFY_PREFS) if self.home is not None and hasattr(self.home, "policy") else "").strip()
            if rules:   # the owner's saved rules (owner_copies.py), not the agent's notes in the file
                body += "\n\nThe user's notification rules (saved in the app):\n" + rules[:2000]
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


# ---------------------------------------------------------------------------------------------------------------- the agent's tool
def checklist_tools(get: Callable[[], Checklist | None]) -> list[Tool]:
    """checklist_link (`get`: the Checklist, made after the tool list in server.py)."""
    async def link(a: dict[str, Any]) -> str:
        ck = get()
        if ck is None:
            return "error: the checklist is not available on this server"
        return ck.link(str(a.get("item") or ""), str(a.get("job") or ""))

    kinds = ", ".join(f"'{k}:<id>'" for k in JOB_KINDS)
    return [Tool("checklist_link",
                 "Record how an item of the owner's checklist (CHECKLIST.md: their list of things to keep an eye on) is looked after: by "
                 f"the job you made for it because it needs timing of its own ({kinds}), or with everything else at the proactive send "
                 "times (an empty job). Add the line to CHECKLIST.md first. An item the owner takes off in the app takes its job along; "
                 "when you take one off, offer to remove its job.",
                 {"type": "object", "properties": {
                     "item": {"type": "string", "description": "the item as it is written in the list"},
                     "job": {"type": "string", "description": f"{kinds}, or '' for the send times"}},
                  "required": ["item", "job"]}, link)]


# ---------------------------------------------------------------------------------------------------------------- API
class ChecklistIn(BaseModel):
    enabled: bool | None = None          # retired: the proactive switch (accepted, ignored)
    every_minutes: int | None = None     # retired: the checklist has no pace (accepted, ignored)
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
