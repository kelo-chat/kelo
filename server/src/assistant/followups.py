"""Follow-ups: when something is stalled waiting for the user, the agent reminds them, at most twice, in waking hours.

What counts as stalled (an "item", one row of the table followups):
  login     a login the agent asked for with request_login whose keychain entry still has no value (tied to the task or background
            job that asked for it)
  worker    a background job (workers.py) in needs_input: waiting for a choice, a code, a step on its page, or a login (a job waiting
            for a login that already has a login item is folded into that item instead of becoming a second one)
  approval  an approval card of an unattended run (payments included) that nobody has answered
  approval_expired
            such a card ran out unanswered (UNATTENDED_APPROVAL_SECONDS, 30 min, in server.py): the step was skipped. The item offers
            to try again ("retry" action, POST /api/followups/{id}/retry): a background job (workers.py) is resumed (or, still running,
            told) to redo the step; a schedule's run or a background task is started again with a note to go on from where it
            stopped. Either way the step asks for approval again: a payment is never approved by trying again. Several cards of one
            run that ran out make one item. It closes when tried again, dropped, or when the run moved on by itself (the job was
            resumed some other way or is gone; the schedule ran again or was deleted).
  entry     a fill-in card the agent showed in the chat for a service key or token, a payment card or a payment PIN (not a login: that is
            the "login" kind) that nobody has filled in yet (secret_requests.py hands it over when it opens). It closes when the card is
            filled in, cancelled, replaced or expired. Its follow-ups are the same as a login's (timing, waking hours, at most two); its
            actions are only snooze and drop (the card itself is in the chat and under My → Keychain → Waiting for input).
  reply     a question the agent asked in chat and needs answered to go on. Only when the agent itself calls
            await_reply(topic, deadline?); nothing is guessed from the text.
  unknown_result
            an unattended run ended with a step that sends, changes or deletes (done_once.py) whose result is still unknown (a timeout:
            it may or may not have gone out) and which the run did not check. One follow-up, once, in waking hours: "Kelo did <action>
            on <target> at <time> but could not confirm it went through; please check". It closes when the record is settled
            (action_result) or dropped; snooze and drop only.
Every pass (once a minute, and on GET /api/followups) the items are checked against the real state, so they close by themselves.

Timing (all settings, GET/PUT /api/followups/settings): the first follow-up `first_delay_min` (30) after the stall began, the second
and last `second_delay_min` (180) after the first; then the item is "on hold": no more follow-ups, still listed, its actions still
work. When a deadline is known (await_reply / request_login / a job's report pass one; else the next run of the schedule whose run is
waiting) one extra follow-up goes out about an hour before it, once, if that moment came after the stall began (also for an item on
hold). An approval card stops waiting after a while (30 minutes in an unattended run, the same as the first delay): an approval
item's regular reminder that would come at or after that moment is not sent, because the approval_expired item takes over there.
Its first follow-up goes out right when the card runs out (the user has already been away the whole wait), its second
`second_delay_min` after that, then it is on hold. A job that then pauses for the user (needs_input, not for a login) while its
approval_expired item is open is folded into that item. Two follow-ups due within 30 minutes of each other go out as one. Follow-ups go out only in waking hours (except the one
reminder of a card of a task marked urgent at setup, URGENT_FIRST after it was asked, at any hour) (the proactive
settings' wake_start..wake_end, profile time zone; outside them they wait for the next morning, and a deadline reminder that would
land after its deadline is skipped), never while a chat reply is running, and at most two per pass. A follow-up already due goes out
outside those hours too when the owner wrote in the chat within the last 30 minutes (they are up: proactive.owner_live).

Stop conditions:
- the item resolves: the login is saved (or entered for one use, or its request is closed), the approval is answered or runs out
  (running out opens an approval_expired item), the job is resumed or stopped, an expired approval is tried again, or, for a reply
  item, the user writes anything in the chat after it was made;
- the user is engaged: a new message from the user while a blocking item (login / worker / approval / approval_expired) is still open does not close it
  (the state says it is still blocking, and nothing here tries to judge whether the message was about it); it restarts the item's
  wait for its next follow-up from that message, so a reminder never lands in the middle of a conversation;
- the user drops or postpones it: the drop / snooze buttons (POST /api/followups/{id}/drop|snooze) or the agent's tools
  followup_cancel(item) / followup_snooze(item, until). Dropping stops the reminders only (it never denies an approval or stops a job);
- NOTIFY_PREFS.md: an item whose title, topic, site or name matches a short keyword line under "Do not tell me" is kept but marked
  suppressed: listed, never followed up.

Delivery: one chat message from the agent, written by the user's main model from a short prompt (what waits, why, what is needed,
the deadline; in the conversation's language; offering the obvious choices), or a fixed template in the app's language when the model
fails. It is stored in the conversation, and a notification of type "followup" (pushed) carries it. The buttons the app can show are
the item's `actions`, under the message whose id is the item's `message_id`.

FOLLOW-UPS API (also next to serialize() in server.py):
Followup: {"id": int, "kind": login|worker|approval|approval_expired|reply|entry|unknown_result, "title", "detail", "stage": first|second|on_hold (first: no follow-up sent
  yet, the first is next; second: the first went out, the second is next; on_hold: both went out, no more), "sent": n, "next_at":
  unix|null (when the next one goes out, after waking hours, snoozes and the deadline; null on hold or suppressed), "began_at",
  "deadline": unix|null, "deadline_sent": bool, "snoozed_until": unix|null, "suppressed": bool, "last_sent_at": unix|null,
  "message_id": the chat message of the last follow-up|null, "task_id"|null, "worker_id"|null, "approval_id"|null (approval only:
  an expired card cannot be answered), "login"|null, "topic"|null, "waiting_for": login|control|user|null (worker), "origin":
  worker|schedule|background|null (approval_expired: what a retry starts again), "schedule_id"|null, "status": open|resolved|dropped,
  "closed_why": str|null, "actions": [{"kind": "open_login", "name", "label"} | {"kind": "approve"|"deny", "approval_id", "label"} |
  {"kind": "resume", "worker_id", "label"} | {"kind": "retry", "followup_id", "label"} | {"kind": "snooze", "followup_id",
  "minutes": 60, "label"} | {"kind": "drop", "followup_id", "label"}]}   (`label`: the button's words in the app's language)
GET  /api/followups?closed=false   {"items": [Followup] open ones, newest first (closed=true: the last 50 closed ones as well)}
POST /api/followups/{id}/snooze {"minutes": 5..10080}   {"ok", "item"}: nothing before then; at that time one follow-up, and the
       count goes on from there. 404 unknown or closed, 422 bad minutes.
POST /api/followups/{id}/drop   {"ok", "item"}: no more follow-ups for it (404 unknown or closed)
POST /api/followups/{id}/retry  (approval_expired only) {"ok": true, "item" (now resolved, closed_why "tried again"), "how":
       worker_resumed|worker_queued|worker_delivered|schedule_started|background_started, "worker_id"?, "schedule_id"?, "task_id"?,
       "turn_id"?}: the run is started again and the step asks for approval again. 404 unknown or closed, 409 {"detail"} not an
       approval_expired item or it cannot be started again (the job or schedule is gone, the schedule is running, no model).
GET  /api/followups/settings    {"enabled" (true), "first_delay_min" (30), "second_delay_min" (180), "wake_start", "wake_end",
       "timezone", "awake"}  (the waking hours are the proactive settings: change them with PUT /api/proactive)
PUT  /api/followups/settings    any of enabled, first_delay_min (5..1440), second_delay_min (5..2880) -> as GET; 422 {"detail"}
Turn event: {"type": "followup", "id" (the tool call), "followup_id", "kind", "status": open|snoozed|dropped, "topic"?, "until"?}
when the agent calls await_reply / followup_snooze / followup_cancel in a reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from . import i18n
from .activity import _TIME_TAG
from .agent import Tool, emit_ui
from .agent.fence import fence
from .home import NOTIFY_PREFS
from .i18n import N_, t
from .proactive import NEVER_HEADINGS, in_waking_hours, keywords, section
from .prompt import tone_line
from .providers import ChatRequest, TextDelta

log = logging.getLogger("uvicorn.error")

KINDS = ("login", "worker", "approval", "approval_expired", "reply", "entry", "unknown_result")
BLOCKING = ("login", "worker", "approval", "approval_expired", "entry")
DEFAULTS: dict[str, Any] = {"enabled": True, "first_delay_min": 30, "second_delay_min": 180}
LIMITS = {"first_delay_min": (5, 1440), "second_delay_min": (5, 2880)}
DEADLINE_LEAD = 3600.0        # the extra reminder goes out this long before a known deadline
URGENT_FIRST = 300.0          # an urgent card (a task marked urgent at setup) is reminded this soon, also outside waking hours
MERGE_WINDOW = 1800.0         # two follow-ups due this close together go out as one
PER_PASS = 2                  # at most this many follow-ups per pass
SNOOZE_MINUTES = 60           # the snooze button's length
KEEP_CLOSED = 30 * 86400
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
    extra TEXT NOT NULL DEFAULT '{}', task_id INTEGER, worker_id TEXT NOT NULL DEFAULT '', schedule_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open', began_at REAL NOT NULL, deadline REAL, sent INTEGER NOT NULL DEFAULT 0, first_sent_at REAL,
    last_sent_at REAL, deadline_sent INTEGER NOT NULL DEFAULT 0, snoozed_until REAL, engaged_at REAL, seen_msg INTEGER NOT NULL DEFAULT 0,
    message_id INTEGER, suppressed INTEGER NOT NULL DEFAULT 0, closed_at REAL, closed_why TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS followups_kind_ref ON followups(kind, ref);
CREATE INDEX IF NOT EXISTS followups_status ON followups(status);
"""

PROMPT = """You write one short follow-up message from a personal agent to the user it works for. Something is waiting for the user and
nothing can go on until they act. Write in the language of the user's recent messages below (if there are none, in the language named by
"App language"). Be natural and specific: say what is waiting, why it matters, what the user needs to do, and the deadline if there is
one; offer the obvious choices (for example do it now, remind later, or drop it). One to three short sentences. No greeting, no headings,
no markdown, no tool names, no file names, no codes or ids. If it is the last reminder, say you will not ask again. Reply with the
message only."""

# what the user can do, per kind, for the model's facts (English, for the model only)
NEEDED = {"login": "enter the login in the app's keychain sheet (never in chat)", "approval": "allow or deny the step on its card",
          "worker": "give the background job what it waits for, then let it go on", "reply": "answer the question in the chat",
          "approval_expired": "say whether to try the step again (it will ask for their OK again; nothing is paid without it)",
          "entry": "fill in the card the agent showed in the chat (or open My → Keychain → Waiting for input), or tell the agent to drop it; "
                   "the value goes straight into the app's card, never into the chat",
          "unknown_result": "look at the outside themself (the sent folder, the site's history or order page) and tell the agent if it did not go "
                            "through"}
# what an entry item waits for, in words (the detail of the item; N_ keys, translated when the follow-up is written)
ENTRY_WHAT = {"key": N_("a key"), "card": N_("a payment card"), "payment_pin": N_("a payment PIN")}
ENTRY_WHAT_EN = {"key": "a service key or token", "card": "a payment card", "payment_pin": "a payment PIN"}   # for the model's facts

TEMPLATES = {
    "login": N_("I still need your {label} login to go on. You can enter it in the app now, have me remind you later, or drop it."),
    "worker": N_("\"{title}\" is still waiting for you ({reason}). Shall I go on, wait a bit, or drop it?"),
    "approval": N_("A step is still waiting for your OK: {what}. You can allow it, deny it, or have me drop it."),
    "reply": N_("I am still waiting for your answer about {topic} before I can go on. Reply when you can, or tell me to drop it."),
    "approval_expired": N_("The request for your OK ({what}) ran out while you were away, so I skipped that step. Shall I try again?"),
    "entry": N_("I still need {what} ({label}) to go on. Fill in the card in the chat (or My → Keychain → Waiting for input), or tell me to drop it."),
    "unknown_result": N_("Kelo did {action} on {target} at {time} but could not confirm it went through; please check."),
}
PAYMENT_NOTE = N_(" Trying again asks for your OK again; nothing is paid without it.")
DEADLINE_NOTE = N_(" It has to be done by {when}.")
LAST_NOTE = N_(" I will not remind you again after this.")
NEEDS_YOU = N_("it needs you")
LABELS = {"open_login": N_("Enter the login"), "approve": N_("Allow"), "deny": N_("Deny"), "resume": N_("Go on"),
          "retry": N_("Try again"), "snooze": N_("Remind me in an hour"), "drop": N_("Drop it")}
# what the run is told when the user asks to try an expired step again (English, for the model only)
RETRY_NOTE = ("Earlier the step \"{what}\" waited for the user's approval, and the request ran out before they could answer, so it was "
              "skipped. The user now asks you to try it again: redo that step (it asks for their approval again{pay}) and go on from "
              "where you stopped. Do not redo what was already finished; the earlier run's report may be in the conversation above.")
RETRY_PAY = "; it is a payment: it is never paid without their OK"

DeliverFn = Callable[[dict[str, Any], str], Awaitable[int | None]]   # (item, text) -> the chat message id
RetryFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]   # (item + "origin" + "note") -> {"how", ids...}; ValueError: cannot


class RetryError(Exception):
    """An expired approval cannot be tried again (the message says why)."""


def next_awake(ts: float, start: str, end: str, tz: ZoneInfo) -> float:
    """`ts` itself inside waking hours, else the next wake_start after it."""
    if in_waking_hours(ts, start, end, tz):
        return ts
    m = HHMM.match(start)
    h, mi = (int(m.group(1)), int(m.group(2))) if m else (7, 0)
    d = datetime.fromtimestamp(ts, tz)
    w = d.replace(hour=h, minute=mi, second=0, microsecond=0)
    if w <= d:
        w += timedelta(days=1)
    return w.timestamp()


def parse_when(value: Any, tz: ZoneInfo, now: float) -> float | None:
    """A moment from the model or the app: unix seconds, an ISO date-time (local when it has no offset), or "HH:MM" (today, or tomorrow
    when that has passed). None when it cannot be read."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 1e9 else None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{9,11}(\.\d+)?", text):
        return float(text)
    if (m := HHMM.match(text)):
        d = datetime.fromtimestamp(now, tz).replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return (d + timedelta(days=1)).timestamp() if d.timestamp() <= now else d.timestamp()
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d.timestamp()


class Followups:
    def __init__(self, store: Any, *, home: Any = None, stream_fn: Callable[..., AsyncIterator[Any]] | None = None,
                 model: Callable[[], tuple[str, str] | None] = lambda: None, key_args: Callable[[str], dict[str, Any]] = lambda _p: {},
                 deliver: DeliverFn | None = None, wake: Callable[[], tuple[str, str]] = lambda: ("07:00", "22:00"),
                 busy: Callable[[], bool] = lambda: False, workers: Any = None,
                 pending_approvals: Callable[[], list[dict[str, Any]]] = lambda: [],
                 login_state: Callable[[str], str] | None = None, schedule_next: Callable[[int], float | None] = lambda _s: None,
                 schedule_since: Callable[[int, float], str] | None = None, retry: RetryFn | None = None,
                 user_marks: tuple[str, ...] = (), clock: Callable[[], float] = time.time,
                 live: Callable[[float], bool] = lambda _t: False, next_wake: Callable[[float], float] | None = None,
                 tone: Callable[[], str] | None = None, entries: Any = None, action_state: Callable[[int], str] | None = None):
        self.store, self.home, self.stream_fn, self.model, self.key_args = store, home, stream_fn, model, key_args
        self.action_state = action_state or (lambda _i: "unknown")   # done_once.ActionLedger.state_of: "unknown" | "done" | "failed" | "gone"
        self.entries = entries   # the fill-in cards (secret_requests.SecretRequests): an entry item closes when its card is no longer open
        self.tone = tone or (lambda: store.profile().get("tone", ""))   # how Kelo talks to the user (prompt.tone_line)
        self.live = live   # the owner wrote in the chat within the last 30 minutes: awake now, even outside the contact hours
        self.next_wake = next_wake   # the next moment in the contact hours, each day by its own window (weekday / weekend: proactive.py)
        self.deliver, self.wake, self.busy, self.workers, self.pending_approvals = deliver, wake, busy, workers, pending_approvals
        self.login_state = login_state or self._login_state
        self.schedule_next, self.user_marks, self.clock = schedule_next, user_marks, clock
        self.schedule_since, self.retry_fn = schedule_since, retry   # schedule_since(sid, ts): "gone" | "ran" (a run began after ts) | ""
        self.lock = asyncio.Lock()
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    # ------------------------------------------------------------------ settings
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.store.profile().get("timezone") or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def config(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.store.meta("followups:config") or "{}")
        except ValueError:
            raw = {}
        return {**DEFAULTS, **{k: v for k, v in (raw if isinstance(raw, dict) else {}).items() if k in DEFAULTS}}

    def settings(self) -> dict[str, Any]:
        start, end = self.wake()
        return {**self.config(), "wake_start": start, "wake_end": end, "timezone": self.tz().key,
                "awake": in_waking_hours(self.clock(), start, end, self.tz())}

    def update(self, **changes: Any) -> dict[str, Any]:
        cfg = self.config()
        for k, v in changes.items():
            if v is None or k not in DEFAULTS:
                continue
            if k == "enabled":
                cfg[k] = bool(v)
                continue
            lo, hi = LIMITS[k]
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a whole number of minutes") from None
            if not lo <= n <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
            cfg[k] = n
        self.store.set_meta("followups:config", json.dumps(cfg))
        return self.settings()

    # ------------------------------------------------------------------ storage
    def _row(self, fid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM followups WHERE id = ?", (int(fid),))
        return rows[0] if rows else None

    def _open_rows(self, kind: str | None = None) -> list[dict[str, Any]]:
        return self.store._all("SELECT * FROM followups WHERE status = 'open' AND (? IS NULL OR kind = ?) ORDER BY id", (kind, kind))

    def _find(self, kind: str, ref: str, open_only: bool = True) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM followups WHERE kind = ? AND ref = ? AND (? = 0 OR status = 'open') ORDER BY id DESC LIMIT 1",
                               (kind, ref, int(open_only)))
        return rows[0] if rows else None

    def _set(self, fid: int, **fields: Any) -> None:
        fields["updated_at"] = self.clock()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.store._run(f"UPDATE followups SET {cols} WHERE id = ?", (*fields.values(), fid))

    def _add(self, kind: str, ref: str, title: str, *, detail: str = "", extra: dict[str, Any] | None = None, task_id: int | None = None,
             worker_id: str = "", schedule_id: int | None = None, began_at: float | None = None, deadline: float | None = None,
             status: str = "open", closed_why: str = "") -> int:
        now = self.clock()
        fid = int(self.store._run(
            "INSERT INTO followups(kind, ref, title, detail, extra, task_id, worker_id, schedule_id, status, began_at, deadline, seen_msg, "
            "closed_at, closed_why, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (kind, ref, title[:200], detail[:600], json.dumps(extra or {}, ensure_ascii=False), task_id, worker_id, schedule_id, status,
             began_at if began_at is not None else now, deadline, self._last_user_msg(), now if status != "open" else None, closed_why,
             now, now)).lastrowid or 0)
        self.store._run("DELETE FROM followups WHERE status != 'open' AND closed_at < ?", (now - KEEP_CLOSED,))
        return fid

    def _close(self, fid: int, status: str, why: str) -> None:
        self._set(fid, status=status, closed_at=self.clock(), closed_why=why)

    def _last_user_msg(self) -> int:
        """The id of the user's newest own message (handoffs from jobs and watchers are stored as user messages but are not theirs)."""
        marks = self.user_marks
        for r in self.store._all("SELECT id, content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 20"):
            if not marks or not str(r["content"]).startswith(marks):
                return int(r["id"])
        return 0

    def _login_state(self, name: str) -> str:
        c = self.store.credential(name)
        return "gone" if not c else "saved" if c.get("secret") else "waiting"

    def _card_deadline(self, card: dict[str, Any]) -> float | None:
        """The time a card itself says matters (purchase_result: until when cancelling a wrong charge is free or cheaper), if ahead."""
        try:
            at = float(card.get("deadline_at") or 0)
        except (TypeError, ValueError):
            return None
        return at if at > self.clock() else None

    def _schedule_deadline(self, schedule_id: int | None) -> float | None:
        if not schedule_id:
            return None
        try:
            at = self.schedule_next(int(schedule_id))
        except Exception:  # noqa: BLE001
            return None
        return float(at) if at and at > self.clock() else None

    # ------------------------------------------------------------------ what starts an item
    def login_requested(self, name: str, label: str, host: str, deadline: Any = None, *, task: dict[str, Any] | None = None,
                        worker: str = "") -> int | None:
        """request_login asked the user for a login: a new stall (or the same one again, kept as it is with any new deadline)."""
        if self.login_state(name) == "saved":
            return None
        dl = parse_when(deadline, self.tz(), self.clock())
        task = task or {}
        src = str(task.get("source") or "")
        sched = int(src[9:]) if src.startswith("schedule:") and src[9:].isdigit() else None
        row = self._find("login", name)
        if row is not None:
            self._set(row["id"], deadline=dl or row["deadline"], worker_id=worker or row["worker_id"],
                      task_id=task.get("task_id") or row["task_id"])
            return int(row["id"])
        return self._add("login", name, label or host, detail=host, extra={"login": name, "site": host}, task_id=task.get("task_id"),
                         worker_id=worker, schedule_id=sched, deadline=dl)

    def entry_requested(self, req: dict[str, Any]) -> int | None:
        """The agent showed a fill-in card (a key, a payment card, a payment PIN; logins have their own item) and nobody has filled it:
        a stall like a login. `req` is the secret_requests row (secret_requests.SecretRequests.on_open)."""
        rid, kind = str(req.get("id") or ""), str(req.get("kind") or "")
        if not rid or kind not in ENTRY_WHAT:
            return None
        row = self._find("entry", rid)
        if row is not None:
            return int(row["id"])
        src = str(req.get("run_source") or "")
        sched = int(src[9:]) if src.startswith("schedule:") and src[9:].isdigit() else None
        return self._add("entry", rid, str(req.get("label") or req.get("name") or t(ENTRY_WHAT[kind])),
                         detail=t(ENTRY_WHAT[kind]),
                         extra={"entry": kind, "request_id": rid}, task_id=req.get("task_id"), worker_id=str(req.get("worker_id") or ""),
                         schedule_id=sched)

    def unknown_result(self, info: dict[str, Any]) -> int | None:
        """An unattended run ended with a step whose result is still unknown (done_once.DoneOnce.run_ended; `info`: id, ts, task_id, source,
        action (a msgid), target). One item per step, once: the first follow-up goes out in waking hours and there is no second."""
        rid = str(info.get("id") or "")
        if not rid or self._find("unknown_result", rid, open_only=False) is not None:
            return None
        src = str(info.get("source") or "")
        sched = int(src[9:]) if src.startswith("schedule:") and src[9:].isdigit() else None
        action, target, at = str(info.get("action") or ""), str(info.get("target") or ""), float(info.get("ts") or self.clock())
        return self._add("unknown_result", rid, f"{t(action)} · {target}"[:200], detail=target,
                         extra={"record_id": int(rid), "action": action, "target": target, "at": at}, task_id=info.get("task_id"),
                         schedule_id=sched, began_at=self.clock())

    def awaiting_reply(self, topic: str, deadline: float | None, task_id: int | None) -> int:
        topic = re.sub(r"\s+", " ", topic).strip()[:200]
        for row in self._open_rows("reply"):
            if row["title"].lower() == topic.lower():
                self._set(row["id"], deadline=deadline or row["deadline"], began_at=self.clock(), seen_msg=self._last_user_msg())
                return int(row["id"])
        return self._add("reply", f"r{int(self.clock() * 1000)}", topic, extra={"topic": topic}, task_id=task_id, deadline=deadline)

    def approval_expired(self, a: dict[str, Any], origin: dict[str, Any] | None) -> int | None:
        """An unattended approval card (`a`: its pending_approvals entry) ran out unanswered. `origin`: what a retry starts again,
        {"kind": "worker", "worker_id"} | {"kind": "schedule", "schedule_id"} | {"kind": "background", "prompt", "title", "source",
        "owner", "note", "saved_as"} | None. Closes its approval item; opens (or, for another card of the same run, extends) an
        approval_expired item whose first follow-up is due now. Returns the item id."""
        aid = str(a.get("approval_id") or "")
        if not aid or not a.get("unattended"):
            return None
        if (row := self._find("approval", aid)) is not None:
            self._close(row["id"], "resolved", "approval expired")
        if (row := self._find("approval_expired", aid, open_only=False)) is not None:
            return int(row["id"])
        origin = dict(origin) if origin and origin.get("kind") in ("worker", "schedule", "background") else None
        pay = a.get("payment") or {}
        what = " · ".join(x for x in (str(a.get("label") or ""), str(pay.get("amount_text") or ""), str(a.get("site") or "")) if x)
        what = what or t("Approval")
        task_id = a.get("task_id")
        if task_id:
            for row in self._open_rows("approval_expired"):
                if row["task_id"] == task_id:      # another card of the same run: one item, one retry
                    extra = _extra(row)
                    extra["payment"] = bool(extra.get("payment") or pay)
                    title = row["title"] if what in row["title"] else f"{row['title']}; {what}"
                    self._set(row["id"], title=title[:200], extra=json.dumps(extra, ensure_ascii=False))
                    return int(row["id"])
        sid = (origin or {}).get("schedule_id") or a.get("schedule_id")
        return self._add("approval_expired", aid, what, detail=str(a.get("detail") or "")[:300],
                         extra={"approval_id": aid, "site": a.get("site") or "", "payment": bool(pay), "expired_at": self.clock(),
                                "origin": origin},
                         task_id=task_id, worker_id=str((origin or {}).get("worker_id") or ""), schedule_id=sid, began_at=self.clock())

    # ------------------------------------------------------------------ checking against the real state
    def reconcile(self) -> None:
        now, last_msg = self.clock(), self._last_user_msg()
        pending = {}
        try:
            pending = {str(a.get("approval_id")): a for a in self.pending_approvals() if a.get("unattended")}
        except Exception:  # noqa: BLE001
            log.exception("followups: could not read the pending approvals")
        for row in self._open_rows():
            kind, fid = row["kind"], row["id"]
            if kind == "login":
                st = self.login_state(row["ref"])
                if st != "waiting":
                    self._close(fid, "resolved", "login saved" if st == "saved" else "login request closed")
                    continue
            elif kind == "entry":
                req = self.entries.get(row["ref"]) if self.entries is not None else None
                if req is None or req.get("state") != "open":
                    self._close(fid, "resolved", "the card was filled in" if req and req.get("state") == "saved" else "the card was closed")
                    continue
            elif kind == "approval":
                if row["ref"] not in pending:
                    self._close(fid, "resolved", "approval answered or expired")
                    continue
            elif kind == "worker":
                w = self.workers.get(row["worker_id"]) if self.workers is not None else None
                ep = row["ref"]
                if w is None or w["status"] != "needs_input" or ep != _episode(w):
                    self._close(fid, "resolved", "job moved on")
                    continue
            elif kind == "reply":
                if last_msg > row["seen_msg"]:
                    self._close(fid, "resolved", "user replied")
                    continue
            elif kind == "approval_expired":
                if (why := self._expired_moved_on(row)):
                    self._close(fid, "resolved", why)
                    continue
            elif kind == "unknown_result":
                if (st := self.action_state(int(row["ref"]))) != "unknown":
                    self._close(fid, "resolved", "the owner's agent checked it" if st in ("done", "failed") else "the record is gone")
                    continue
            if kind in BLOCKING and last_msg > row["seen_msg"]:
                self._set(fid, engaged_at=now, seen_msg=last_msg)   # the user is talking to the agent: wait again from here
        for aid, a in pending.items():
            if self._find("approval", aid, open_only=False) is not None:
                continue
            pay = a.get("payment") or {}
            what = " · ".join(x for x in (str(a.get("label") or ""), str(pay.get("amount_text") or ""), str(a.get("site") or "")) if x)
            sid = a.get("schedule_id")
            self._add("approval", aid, what or t("Approval"), detail=str(a.get("detail") or "")[:300],
                      extra={"approval_id": aid, "site": a.get("site") or "", "payment": bool(pay), "expires_at": a.get("expires_at"),
                             "urgent": bool(a.get("urgent"))},
                      task_id=a.get("task_id"),
                      schedule_id=sid, began_at=float(a.get("asked_at") or now),
                      deadline=self._card_deadline(a) or self._schedule_deadline(sid))
        if self.workers is not None:
            for w in self.workers.list(50, "needs_input"):
                full = self.workers.get(w["id"]) or w
                ep = _episode(full)
                if self._find("worker", ep, open_only=False) is not None:
                    continue
                rep = full.get("report") or {}
                raw = self.workers._row(w["id"]) if hasattr(self.workers, "_row") else None   # who started it (a schedule's run?)
                src = str((raw or {}).get("parent_source") or "")
                sid = int(src[9:]) if src.startswith("schedule:") and src[9:].isdigit() else None
                dl = parse_when(rep.get("deadline"), self.tz(), now) or self._schedule_deadline(sid)
                began = float(rep.get("at") or w.get("updated_at") or now)
                extra = {"waiting_for": w.get("waiting_for"), "login": w.get("login"), "worker_id": w["id"]}
                login_row = self._find("login", w["login"]) if w.get("waiting_for") == "login" and w.get("login") else None
                if login_row is None and w.get("waiting_for") != "login":   # it stopped after its card ran out: the retry covers it
                    login_row = next((r for r in self._open_rows("approval_expired") if r["worker_id"] == w["id"]), None)
                if login_row is not None:   # the same stall as the login (or expired approval) item: fold the job into it
                    if login_row["kind"] == "login":
                        self._set(login_row["id"], worker_id=w["id"], deadline=dl or login_row["deadline"])
                    self._add("worker", ep, w["title"], detail=str(w.get("reason") or ""), extra=extra, task_id=w.get("task_id"),
                              worker_id=w["id"], schedule_id=sid, began_at=began, deadline=dl, status="resolved",
                              closed_why=f"folded into follow-up {login_row['id']}")
                    continue
                self._add("worker", ep, w["title"], detail=str(w.get("reason") or ""), extra=extra, task_id=w.get("task_id"),
                          worker_id=w["id"], schedule_id=sid, began_at=began, deadline=dl)

    def _expired_moved_on(self, row: dict[str, Any]) -> str:
        """Why an approval_expired item is no longer worth a retry ("" = it still is): its job is gone or went on another way
        (it stopped after the card ran out, then runs again without our retry), or its schedule is gone or ran again."""
        extra = _extra(row)
        origin = extra.get("origin") or {}
        if origin.get("kind") == "worker" and self.workers is not None:
            w = self.workers.get(row["worker_id"])
            if w is None:
                return "job gone"
            if w["status"] != "running" and not extra.get("job_stopped"):
                self._set(row["id"], extra=json.dumps({**extra, "job_stopped": True}, ensure_ascii=False))
            elif w["status"] == "running" and extra.get("job_stopped"):
                return "job moved on"
        elif origin.get("kind") == "schedule" and self.schedule_since is not None and row["schedule_id"]:
            try:
                st = self.schedule_since(int(row["schedule_id"]), float(extra.get("expired_at") or row["began_at"]))
            except Exception:  # noqa: BLE001
                st = ""
            if st == "gone":
                return "schedule deleted"
            if st == "ran":
                return "schedule ran again"
        return ""

    # ------------------------------------------------------------------ timing
    def _when_awake(self, at: float) -> float:
        """`at` when the owner is awake then (inside the contact hours, or — for a moment already here — just active in the chat), else
        the next start of the contact hours."""
        now = self.clock()
        if at <= now and self.live(now):
            return at
        if self.next_wake is not None:
            return self.next_wake(at)
        start, end = self.wake()
        return next_awake(at, start, end, self.tz())

    def _regular_at(self, row: dict[str, Any], cfg: dict[str, Any]) -> float | None:
        """When the next follow-up of the ladder (first, second, or the one a snooze asked for) goes out, in waking hours."""
        first, second = cfg["first_delay_min"] * 60, cfg["second_delay_min"] * 60
        if row["kind"] == "unknown_result" and row["sent"] >= 1:
            return None                        # said once; never again
        if row["kind"] in ("approval_expired", "unknown_result"):
            first = 0                          # the user has been away the card's whole wait already: the first one goes out now
        urgent = row["kind"] == "approval" and bool(_extra(row).get("urgent"))
        if urgent:                             # a card of a task marked urgent at setup: one reminder soon, whatever the hour
            first = min(first, URGENT_FIRST)
        if row["snoozed_until"]:
            at = row["snoozed_until"]
        elif row["sent"] == 0:
            at = max(row["began_at"] + first, (row["engaged_at"] or 0) + first)
        elif row["sent"] == 1:
            at = max((row["first_sent_at"] or row["last_sent_at"] or row["began_at"]) + second, (row["engaged_at"] or 0) + second)
        else:
            return None                        # on hold
        if not urgent:
            at = self._when_awake(at)
        lapses = _extra(row).get("expires_at") if row["kind"] == "approval" else None
        if lapses and at >= float(lapses):
            return None                        # the card is gone by then: its approval_expired item reminds the user instead
        return at

    def _deadline_at(self, row: dict[str, Any]) -> float | None:
        """When the extra reminder before a known deadline goes out (in waking hours, still before the deadline), or None."""
        if not row["deadline"] or row["deadline_sent"]:
            return None
        at = row["deadline"] - DEADLINE_LEAD
        if at < row["began_at"]:
            return None                        # the deadline was already that close when the stall began
        if row["snoozed_until"]:
            at = max(at, row["snoozed_until"])
        at = self._when_awake(at)
        return at if at < row["deadline"] else None

    def due(self, row: dict[str, Any], cfg: dict[str, Any] | None = None) -> tuple[float, str] | None:
        """(when, why) of the item's next follow-up: why is "regular" or "deadline". None when nothing more is due."""
        if row["status"] != "open" or row["suppressed"]:
            return None
        cfg = cfg or self.config()
        out = [(at, why) for at, why in ((self._regular_at(row, cfg), "regular"), (self._deadline_at(row), "deadline")) if at is not None]
        return min(out) if out else None

    # ------------------------------------------------------------------ the pass
    def prefs_block(self, row: dict[str, Any]) -> bool:
        if self.home is None:
            return False
        p = self.home.root / NOTIFY_PREFS
        text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() and not p.is_symlink() else ""
        never = keywords(section(text, *NEVER_HEADINGS))
        extra = _extra(row)
        hay = " ".join(str(x) for x in (row["title"], row["detail"], extra.get("site"), extra.get("login"), extra.get("topic")) if x).lower()
        return any(k in hay for k in never)

    async def tick(self) -> int:
        """What the internal job calls every minute: check the items, send what is due. Returns how many went out."""
        if self.lock.locked():
            return 0
        async with self.lock:
            try:
                self.reconcile()
            except Exception:  # noqa: BLE001
                log.exception("followups: the check failed")
                return 0
            cfg = self.config()
            if not cfg["enabled"] or self.busy():
                return 0
            now, sent = self.clock(), 0
            for row in self._open_rows():
                if sent >= PER_PASS:
                    break
                d = self.due(row, cfg)
                if d is None or d[0] > now:
                    continue
                if self.prefs_block(row):
                    self._set(row["id"], suppressed=1)
                    continue
                try:
                    await self.send(row, d[1], cfg)
                    sent += 1
                except Exception:  # noqa: BLE001 - one item failing never stops the others
                    log.exception("followups: sending follow-up %s failed", row["id"])
            return sent

    async def send(self, row: dict[str, Any], why: str, cfg: dict[str, Any]) -> None:
        now = self.clock()
        regular, deadline = why == "regular", why == "deadline"
        other = self._deadline_at(row) if regular else self._regular_at(row, cfg)
        if other is not None and other - now <= MERGE_WINDOW:   # the other one is due soon anyway: one message covers both
            regular = deadline = True
        last = regular and row["sent"] == 1                     # the second of the ladder: nothing more after it
        text = await self.compose(row, deadline=deadline, last=last) or self.template(row, deadline=deadline, last=last)
        mid = await self.deliver(self.public(row), text) if self.deliver is not None else None
        fields: dict[str, Any] = {"last_sent_at": now, "message_id": mid, "engaged_at": None, "seen_msg": self._last_user_msg()}
        if regular:
            fields.update(sent=row["sent"] + 1, snoozed_until=None)
            if row["sent"] == 0:
                fields["first_sent_at"] = now
        if deadline:
            fields["deadline_sent"] = 1
        self._set(row["id"], **fields)

    # ------------------------------------------------------------------ the words
    def _when(self, ts: float) -> str:
        d = datetime.fromtimestamp(ts, self.tz())
        today = datetime.fromtimestamp(self.clock(), self.tz()).date()
        return d.strftime("%H:%M") if d.date() == today else d.strftime("%m-%d %H:%M")

    def template(self, row: dict[str, Any], *, deadline: bool = False, last: bool = False) -> str:
        extra = _extra(row)
        kind = row["kind"]
        if kind == "worker" and extra.get("waiting_for") == "login" and extra.get("login"):
            text = t(TEMPLATES["login"], label=extra["login"])
        elif kind == "login":
            text = t(TEMPLATES["login"], label=row["title"])
        elif kind == "entry":
            text = t(TEMPLATES["entry"], what=t(ENTRY_WHAT.get(extra.get("entry"), ENTRY_WHAT["key"])), label=row["title"])
        elif kind == "worker":
            text = t(TEMPLATES["worker"], title=row["title"], reason=(row["detail"] or t(NEEDS_YOU)).strip().rstrip(".")[:160])
        elif kind == "approval":
            text = t(TEMPLATES["approval"], what=row["title"])
        elif kind == "approval_expired":
            text = t(TEMPLATES["approval_expired"], what=row["title"]) + (t(PAYMENT_NOTE) if extra.get("payment") else "")
        elif kind == "unknown_result":
            text = t(TEMPLATES["unknown_result"], action=t(extra.get("action") or "a change"), target=extra.get("target") or "?",
                     time=self._when(float(extra.get("at") or row["began_at"])))
        else:
            text = t(TEMPLATES["reply"], topic=row["title"])
        if deadline and row["deadline"]:
            text += t(DEADLINE_NOTE, when=self._when(row["deadline"]))
        if last:
            text += t(LAST_NOTE)
        return text

    def _recent_user_lines(self) -> list[str]:
        out = []
        for r in reversed(self.store._all("SELECT content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 20")):
            c = str(r["content"])
            if self.user_marks and c.startswith(self.user_marks):
                continue
            c = re.sub(r"\s+", " ", _TIME_TAG.sub("", c)).strip()
            if c:
                out.append(c[:200])
        return out[-3:]

    async def compose(self, row: dict[str, Any], *, deadline: bool, last: bool) -> str | None:
        chosen = self.model()
        if not chosen or self.stream_fn is None:
            return None
        extra = _extra(row)
        tz = self.tz()
        # the item's title and details can quote a page, a job's report or a site's words: fenced as data, our own lines outside
        about = f"{row['title']}" + (f"\nDetails: {row['detail'][:300]}" if row["detail"] else "")
        lines = [f"Now: {datetime.fromtimestamp(self.clock(), tz).strftime('%a %Y-%m-%d %H:%M')} ({tz.key})",
                 f"What is waiting ({row['kind']}):", fence(about, budget=800)]
        if extra.get("waiting_for"):
            lines.append(f"The background job waits for: {extra['waiting_for']}" + (f" (login {extra['login']})" if extra.get("login") else ""))
        if extra.get("payment"):
            lines.append("It is a payment.")
        if row["kind"] == "entry":
            lines.append(f"The agent asked for {ENTRY_WHAT_EN.get(extra.get('entry'), 'a value')} with a fill-in card in the chat and nobody "
                         "has filled it in yet.")
        if row["kind"] == "approval_expired":
            lines.append(f"The request for the user's OK ran out unanswered at {self._when(float(extra.get('expired_at') or row['began_at']))}"
                         ", so that step was skipped. Ask whether to try again.")
        if row["kind"] == "unknown_result":
            lines.append(f"At {self._when(float(extra.get('at') or row['began_at']))} Kelo did this in an automatic run; the answer never came "
                         "back, so it may or may not have gone through. Say it plainly and ask the user to check. This is the only reminder.")
        lines.append(f"What the user needs to do: {NEEDED.get(row['kind'], 'act on it')}")
        lines.append(f"Waiting since: {self._when(row['began_at'])}")
        if row["deadline"]:
            lines.append(f"Deadline: {self._when(row['deadline'])}" + (" (this reminder is because the deadline is near)" if deadline else ""))
        lines.append("This is the last reminder." if last else ("This is a reminder about it." if row["sent"] == 0 else "This is a further reminder."))
        lines.append(f"App language: {i18n.current()}")
        lines.append(tone_line(self.tone(), switch=False))
        recent = self._recent_user_lines()
        lines.append("The user's recent messages (for the language only):\n" + ("\n".join(f"- {x}" for x in recent) if recent else "(none)"))
        provider, model = chosen
        req = ChatRequest(model=model, messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": "\n".join(lines)}],
                          tools=[], extra={})
        try:
            parts: list[str] = []
            async for ev in self.stream_fn(provider, req, **self.key_args(provider)):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
        except Exception:  # noqa: BLE001 - the template takes over
            log.warning("followups: the model could not write follow-up %s", row["id"], exc_info=False)
            return None
        text = re.sub(r"\s+\n", "\n", "".join(parts)).strip().strip('"').strip()
        if not text or text == "NO_REPLY" or len(text) > 1200:
            return None
        return text

    # ------------------------------------------------------------------ what the app and the agent see
    def actions(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        extra, fid = _extra(row), row["id"]
        out: list[dict[str, Any]] = []
        login = extra.get("login") if row["kind"] == "login" or extra.get("waiting_for") == "login" else None
        if login:
            out.append({"kind": "open_login", "name": login})
        elif row["kind"] == "approval":
            out += [{"kind": "approve", "approval_id": row["ref"]}, {"kind": "deny", "approval_id": row["ref"]}]
        elif row["kind"] == "worker":
            out.append({"kind": "resume", "worker_id": row["worker_id"]})
        elif row["kind"] == "approval_expired" and row["status"] == "open" and (extra.get("origin") or {}).get("kind"):
            out.append({"kind": "retry", "followup_id": fid})
        if row["status"] == "open":
            out += [{"kind": "snooze", "followup_id": fid, "minutes": SNOOZE_MINUTES}, {"kind": "drop", "followup_id": fid}]
        return [{**a, "label": t(LABELS[a["kind"]])} for a in out]

    def public(self, row: dict[str, Any]) -> dict[str, Any]:
        extra = _extra(row)
        d = self.due(row) if row["status"] == "open" else None
        stage = "first" if row["sent"] == 0 else "second" if row["sent"] == 1 and row["kind"] != "unknown_result" else "on_hold"
        return {"id": row["id"], "kind": row["kind"], "title": row["title"], "detail": row["detail"], "stage": stage, "sent": row["sent"],
                "next_at": d[0] if d else None, "began_at": row["began_at"], "deadline": row["deadline"],
                "deadline_sent": bool(row["deadline_sent"]), "snoozed_until": row["snoozed_until"], "suppressed": bool(row["suppressed"]),
                "last_sent_at": row["last_sent_at"], "message_id": row["message_id"], "task_id": row["task_id"],
                "worker_id": row["worker_id"] or None, "approval_id": row["ref"] if row["kind"] == "approval" else None,
                "login": extra.get("login") or None, "topic": extra.get("topic") or None, "waiting_for": extra.get("waiting_for") or None,
                "origin": (extra.get("origin") or {}).get("kind") if row["kind"] == "approval_expired" else None,
                "schedule_id": row["schedule_id"], "status": row["status"], "closed_why": row["closed_why"] or None, "actions": self.actions(row)}

    def items(self, closed: bool = False) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT * FROM followups WHERE status = 'open' ORDER BY id DESC")
        if closed:
            rows += self.store._all("SELECT * FROM followups WHERE status != 'open' ORDER BY id DESC LIMIT 50")
        return [self.public(r) for r in rows]

    def get(self, fid: int) -> dict[str, Any] | None:
        r = self._row(fid)
        return self.public(r) if r else None

    def open_count(self) -> int:
        return int(self.store._all("SELECT COUNT(*) AS n FROM followups WHERE status = 'open'")[0]["n"])

    def snooze(self, fid: int, until: float) -> dict[str, Any] | None:
        r = self._row(fid)
        if r is None or r["status"] != "open":
            return None
        self._set(fid, snoozed_until=float(until))
        return self.get(fid)

    def drop(self, fid: int, why: str = "dropped by the user") -> dict[str, Any] | None:
        r = self._row(fid)
        if r is None or r["status"] != "open":
            return None
        self._close(fid, "dropped", why)
        return self.get(fid)

    async def retry(self, fid: int) -> dict[str, Any] | None:
        """Try an expired approval's run again (see approval_expired). None: unknown or closed. RetryError: it cannot be."""
        r = self._row(fid)
        if r is None or r["status"] != "open":
            return None
        if r["kind"] != "approval_expired":
            raise RetryError("only an expired approval can be tried again")
        extra = _extra(r)
        origin = extra.get("origin") or {}
        if self.retry_fn is None or not origin.get("kind"):
            raise RetryError("this run cannot be started again")
        note = RETRY_NOTE.format(what=r["title"], pay=RETRY_PAY if extra.get("payment") else "")
        try:
            out = await self.retry_fn({**self.public(r), "origin": origin, "note": note})
        except ValueError as e:
            raise RetryError(str(e)) from None
        self._close(fid, "resolved", "tried again")
        return {**out, "ok": True, "item": self.get(fid)}

    def match(self, item: str) -> list[dict[str, Any]]:
        """Open items the agent means: "f12" / "12", "all", a login name, a job id, an approval id, or words of the title or topic."""
        text = str(item or "").strip()
        rows = self._open_rows()
        if not text:
            return []
        if text.lower() == "all":
            return rows
        if (m := re.fullmatch(r"[fF]?(\d+)", text)):
            return [r for r in rows if r["id"] == int(m.group(1))]
        low = text.lower()
        exact = [r for r in rows if low in (r["ref"].lower(), r["worker_id"].lower(), str(_extra(r).get("login") or "").lower())]
        return exact or [r for r in rows if low in r["title"].lower() or low in r["detail"].lower()]

    def listing(self) -> str:
        rows = self._open_rows()
        if not rows:
            return "Nothing is waiting for the user."
        return "Open follow-ups: " + "; ".join(f"f{r['id']} ({r['kind']}: {r['title'][:60]})" for r in rows)

    # ------------------------------------------------------------------ the agent's tools
    def tools(self, current_task: Callable[[], dict[str, Any] | None] = lambda: None) -> list[Tool]:
        async def await_reply(args: dict[str, Any]) -> str:
            topic = str(args.get("topic") or "").strip()
            if not topic:
                return "Error: topic is required: a few words naming what you asked, in the user's language."
            dl = None
            if args.get("deadline") not in (None, ""):
                dl = parse_when(args.get("deadline"), self.tz(), self.clock())
                if dl is None or dl <= self.clock():
                    return "Error: deadline must be a future local date-time like 2026-11-07T18:00 (or leave it out)."
            task = current_task() or {}
            fid = self.awaiting_reply(topic, dl, task.get("task_id"))
            await emit_ui({"type": "followup", "followup_id": fid, "kind": "reply", "status": "open", "topic": topic[:200]})
            return (f"Noted as f{fid}. If the user does not answer, they get a reminder later; any message from them closes it. "
                    "Do not ask again in this reply: end your turn now.")

        async def followup_snooze(args: dict[str, Any]) -> str:
            rows = self.match(str(args.get("item") or ""))
            if not rows:
                return "Error: no open follow-up matches that. " + self.listing()
            now = self.clock()
            until = None
            if args.get("minutes") not in (None, ""):
                try:
                    until = now + max(5, min(10080, int(args.get("minutes")))) * 60
                except (TypeError, ValueError):
                    return "Error: minutes must be a whole number."
            elif args.get("until") not in (None, ""):
                until = parse_when(args.get("until"), self.tz(), now)
            if until is None or until <= now:
                return "Error: give until (a future local date-time like 2026-11-08T09:00, or HH:MM) or minutes."
            for r in rows:
                self.snooze(r["id"], until)
                await emit_ui({"type": "followup", "followup_id": r["id"], "kind": r["kind"], "status": "snoozed", "until": until})
            return f"Snoozed {', '.join('f' + str(r['id']) for r in rows)} until {self._when(until)}: no reminder before then."

        async def followup_cancel(args: dict[str, Any]) -> str:
            rows = self.match(str(args.get("item") or ""))
            if not rows:
                return "Error: no open follow-up matches that. " + self.listing()
            for r in rows:
                self.drop(r["id"], "dropped by the agent at the user's word")
                await emit_ui({"type": "followup", "followup_id": r["id"], "kind": r["kind"], "status": "dropped"})
            return (f"Stopped the reminders for {', '.join('f' + str(r['id']) for r in rows)}. This only stops reminding: it does not "
                    "deny an approval or stop a job.")

        item_desc = "which one: its id, 'all', or a login name, job id or words of what it is about"
        return [
            Tool("await_reply", "Call when your reply ends with a question you must have answered to go on (not small talk or optional "
                 "questions); Kelo reminds the user later if they do not answer.",
                 {"type": "object", "properties": {"topic": {"type": "string", "description": "a few words, in the user's language"},
                                                   "deadline": {"type": "string", "description": "local date-time when an answer is too late"}},
                  "required": ["topic"]},
                 await_reply, closes_reply=True),
            Tool("followup_snooze", "Put off Kelo's reminders about something that waits for the user, when they ask.",
                 {"type": "object", "properties": {"item": {"type": "string", "description": item_desc},
                                                   "until": {"type": "string", "description": "local date-time or HH:MM"},
                                                   "minutes": {"type": "integer"}},
                  "required": ["item"]}, followup_snooze),
            Tool("followup_cancel", "Stop Kelo's reminders about something that waits for the user, when they ask (only the reminders).",
                 {"type": "object", "properties": {"item": {"type": "string", "description": item_desc}}, "required": ["item"]},
                 followup_cancel),
        ]


def _extra(row: dict[str, Any]) -> dict[str, Any]:
    try:
        v = json.loads(row.get("extra") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _episode(w: dict[str, Any]) -> str:
    """One pause of a job (a job may stop for the user more than once): its id and the time of the report that paused it."""
    rep = w.get("report") or {}
    return f"{w['id']}:{int(float(rep.get('at') or 0))}"


# ---------------------------------------------------------------------------------------------------------------- API
class SnoozeIn(BaseModel):
    minutes: int


class FollowupSettingsIn(BaseModel):
    enabled: bool | None = None
    first_delay_min: int | None = None
    second_delay_min: int | None = None


def followup_routes(app: FastAPI, followups: Followups, auth: Callable[..., Any]) -> None:
    """The FOLLOW-UPS API (contracts in this module's docstring and next to serialize() in server.py)."""

    @app.get("/api/followups/settings", dependencies=[Depends(auth)])
    async def get_followup_settings() -> dict[str, Any]:
        return followups.settings()

    @app.put("/api/followups/settings", dependencies=[Depends(auth)])
    async def put_followup_settings(body: FollowupSettingsIn) -> dict[str, Any]:
        try:
            return followups.update(**body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/followups", dependencies=[Depends(auth)])
    async def get_followups(closed: bool = False) -> dict[str, Any]:
        followups.reconcile()
        return {"items": followups.items(closed)}

    @app.post("/api/followups/{fid}/snooze", dependencies=[Depends(auth)])
    async def snooze_followup(fid: int, body: SnoozeIn) -> dict[str, Any]:
        if not 5 <= body.minutes <= 10080:
            raise HTTPException(422, "minutes must be between 5 and 10080")
        item = followups.snooze(fid, followups.clock() + body.minutes * 60)
        if item is None:
            raise HTTPException(404, "no such open follow-up")
        return {"ok": True, "item": item}

    @app.post("/api/followups/{fid}/retry", dependencies=[Depends(auth)])
    async def retry_followup(fid: int) -> dict[str, Any]:
        try:
            out = await followups.retry(fid)
        except RetryError as e:
            raise HTTPException(409, str(e)) from None
        if out is None:
            raise HTTPException(404, "no such open follow-up")
        return out

    @app.post("/api/followups/{fid}/drop", dependencies=[Depends(auth)])
    async def drop_followup(fid: int) -> dict[str, Any]:
        item = followups.drop(fid)
        if item is None:
            raise HTTPException(404, "no such open follow-up")
        return {"ok": True, "item": item}
