"""Background jobs: the main agent hands a self-contained job to a run of its own with `delegate` and the chat stays free.

A job (a "worker" in the code and the API) is a separate agent run: its own transcript (not the conversation), the user's main model,
the same tools minus the ones that start or steer jobs (one level only: a job cannot start another; signing in uses the vault tools
inside it), its own browser tab (owner key "task:<id>") and a much larger step budget than a chat turn (Limits), renewed in stretches
while it makes progress (MAX_CHUNKS, CHUNK_NOTE). It keeps going until
it calls `report(status, summary, details?, keep_tab?)`:
  done         the job is complete
  needs_input  waiting for the user (a login, a choice, a one-time code, a step on the page)
  blocked      a hard stop (the site refuses; a human check the agent could not do is handed to the user while the step waits, see
               BrowserSession.snapshot)
  failed       something broke
The report goes back to the main conversation as a hidden handoff message (stored, never shown as the user's words); the main agent then
decides what to tell the user, or says nothing (NO_REPLY) when a quiet run has nothing worth saying. A paused job (needs_input / blocked /
interrupted) goes on where it stopped, same tab and same transcript, with `resume`: from the main agent, from the app
(POST /api/workers/{id}/resume), or on its own when the login it asked for is saved or the user hands its browser tab back.

Every step of a job is a turn of the server (start_turn): approvals reach the user like any unattended run (card + notification, payments
still only through checkout_review), browser cards and the live view work, and each run of a job is a task in the task log, linked to the
task that started the job (tasks.parent_id / worker_id).

A cut job (a restart or a lost browser, job_cuts.py): the checkpoint is the finished tool call. The one call that was running at the
cut gets a cut result chosen by what it could have done: a read is done again; a step that sends, changes or deletes is checked first
(its ledger record is unknown until action_result); a pay press is never pressed again for the same order (the shop's page decides).
It goes on by itself at once, then after 60 s and 300 s; a fourth cut in a row at the same step stops it as blocked, and the owner
decides (a manual resume starts the count again). A finished tool call after a pick-up resets the count.

State lives in SQLite (tables workers, worker_messages). The transcript is rebuilt from the turn's events as they happen, so a restart loses
at most the step in flight. On start-up a job that was running is marked interrupted and resumed (in a new tab when its tab is gone, told
to go on from the history above).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import tool_labels, tool_roles
from .chat_views import PHONE_KEY
from .activity import host_of, title_from
from .agent import Tool, emit_ui, inbox
from .agent.found_secrets import scrub
from .agent.loop import digest_tool_results
from .compaction import request_tokens
from .i18n import N_, t
from .job_cuts import Cut, cut_result, next_pickup, open_calls, pickup_note
from .outside_rule import OWNER_SOURCES
from .shopping import ProductResults, pages_read, verify
from .tools.sites import registrable

log = logging.getLogger("uvicorn.error")

KINDS = ("browser", "research", "general", tool_roles.CHECKOUT_KIND, tool_roles.PHONE_KIND)
REPORT_STATUSES = ("done", "needs_input", "blocked", "failed")
REPORT_NEEDS = ("answer", "site_data_reset")       # report(need=...): a typed reason the code may branch on; the summary is never read
REPORT_REASONS = ("site_refused", "sign_in", "human_check", "not_available", "site_error")
STATUSES = ("running", "needs_input", "blocked", "done", "failed", "stopped", "interrupted")
PAUSED = ("needs_input", "blocked", "interrupted")
MAIN_ONLY = ("delegate", "workers_list", "worker_status", "worker_message", "worker_stop", "resume",
             "await_reply", "followup_snooze", "followup_cancel",   # these three: follow-ups (followups.py) are the main chat's
             "set_owner_settings", "finish_first_chat",   # the first conversation is the main chat's (first_chat.py)
             "set_tone",   # how Kelo talks to the user: changed only when the user asks in the chat
             "browser_forget_site", "browser_profiles", "browser_profile_delete",   # sign-in data controls: the owner asks the chat
             "chat_view")   # the browser or phone shown in the chat is hidden only by the main agent (chat_views.py)
HANDOFF_MARK = "[Handoff from a background job, not from the user]"   # leads the hidden message; GET /api/messages leaves these out
ALREADY_SAID_NOTE = ("Already told the owner in the chat after this job was started or sent on (your own words): {said}\n"
                     "Do not say any of that again. Say only what is new since then: what the job did or found, and what is needed now.")
SAID_CHARS = 800


def already_said(messages: list[dict[str, Any]], since: float) -> str:
    """The chat's own replies written since `since` (the start of the job's run), oldest first, joined and cut to the last SAID_CHARS
    characters; "" when there are none or the start is not known."""
    if since <= 0:
        return ""
    said = [m["content"].strip() for m in messages if m.get("role") == "assistant" and isinstance(m.get("content"), str)
            and float(m.get("created_at") or 0) >= since - 2 and m["content"].strip()]
    return " / ".join(said)[-SAID_CHARS:]
STATUS_HEAD = {"done": N_("Finished"), "needs_input": N_("Needs your help"), "blocked": N_("Got stuck"), "failed": N_("It did not work out")}
RESTART_REASON = N_("Stopped because the server restarted")   # translate with t() where it is saved or shown
LIMIT_RETRY = 1800.0   # a job paused by the usage limit with no reset time known looks again after this long
KEEP_WORKERS = 200          # finished jobs kept (with their transcripts); older ones are forgotten
LINES_KEPT = 40             # activity lines kept per job
PAUSED_TAB_SECONDS = 86400.0   # a paused job keeps its tab this long (the user may take it over and hand it back)
KEPT_TAB_SECONDS = 3600.0      # a finished job that asked to keep its tab keeps it this long
MAX_DEADLINE_MIN = 240
CUT_NOTE = "[This step was cut off (a restart or a stop) before it gave a result. Check the page before you go on.]"
PICKED_UP_LINE = N_("Picked up where it stopped after the Kelo server restarted")
BROWSER_PICKED_UP_LINE = N_("The browser restarted; it went on in a new tab")
CUT_LOOP_LINE = N_("Stopped: the Kelo server restarted {n} times while this job was at the same step. Resume it to try again.")
DRAIN_SECONDS = 25.0   # a stop waits this long for a step that sends, changes or pays to finish before it cuts it (job_cuts.py)
# A job that is getting somewhere never stops at its budget: it goes on in chunks, each with a fresh step / time / token budget, as long
# as the chunk before made progress (new distinct tool calls that succeeded: a new item handled, a new result read). A chunk without any
# is a loop (the same steps again) and stops the job; MAX_CHUNKS bounds it all.
MAX_CHUNKS = 25
# The payment split (docs/browser-workers.md): a job that finds things has no paying tool; a job of kind "checkout" pays and cannot search.
FINDER_NOTE = (" You have no payment tools: you can fill a cart and read a checkout page, but you cannot pay. When the job ends at a purchase, "
               "report done and put a checkout brief in details so a payment specialist can finish it without asking: the shop's address "
               "and the cart or checkout page, each item with its variants and quantity, the quoted total and currency, delivery and contact "
               "choices already made, the limits the user set, and the stage to stop at.")
CHECKOUT_BRIEF = ("You are the payment specialist. You complete one checkout and nothing else: you cannot search the web or start other jobs. "
                  "Start on the shop's home page named above, click to the cart like a person, check the cart against the assignment (items, variants, quantities, total, currency, delivery), "
                  "fill the delivery and contact fields with your page tools, then pay only through checkout_review (draft, then ready) and "
                  "checkout_submit, and read the shop's own confirmation and call purchase_result. When the cart differs from the "
                  "assignment (another item, a higher price, an extra fee, a sign-up) or a choice is missing, do not guess: report "
                  "needs_input and name it. When the offer is gone or the shop refuses, report blocked. Never start a second purchase for "
                  "an order whose outcome is not settled. Put the outcome (placed, not_placed or unknown) and the confirmation in the report.")
# The phone split (docs/phone-worker.md): only a job of kind "phone" drives the Android device; every other run has no phone tool.
PHONE_NOTE = (" You have no phone tools: when the job needs an app on the Android device, say so in details and put a phone brief there "
              "(the app, the goal in the user's terms, the choices already made, the limits the user set, where to stop), so a phone job "
              "can do it without asking.")
# The browser split (docs/browser-workers.md): the chat does not click or type on websites; the instructions every run reads say so, so a job is told it is the one.
BROWSER_NOTE = (" Your instructions say the chat gives website work to a job: you are that job, so click, type and sign in with your own browser "
                "tools yourself. You have no one to ask but the user: report needs_input with one clear question.")
PHONE_BRIEF = ("You are the phone worker: the one job that drives the Android device, and you do only the assignment above, in the app it "
               "names. You cannot search the web, use a browser or start other jobs. Look at the screen (android_look) before you tap "
               "or type; open the app (android_open) when it is not in front. You cannot see the conversation: do not guess. When the "
               "app shows something the assignment does not cover (a choice, a sign-in you cannot do with a saved login, a permission "
               "dialog you may not answer, an order or a price that differs from the assignment), stop and report needs_input with the "
               "question, the options and your recommendation. A step that needs the user's own hands (a human check, a screen you "
               "cannot read) is theirs: report needs_input saying what they must do on the phone; the agent you report to hands them the phone "
               "and resumes you. A new app's first screens (a permission dialog, an intro, an ad, a rating box, terms) are handled by the tool "
               "results: when one names the controls that get through, use them, and put each \"First-run step\" line a tool result gave "
               "into your report. Stop where the assignment says to stop; never go beyond it. The user may watch "
               "the device on the phone card and take it over: follow what the tool results say.")
CHUNK_PROGRESS = 1
CHUNK_NOTE = ("[Not from the user: this job's step budget for one stretch of work is used up and a fresh one starts now ({done} so far). "
              "Go on with what is left; do not redo finished items. Finish with report when everything is done.]")
NO_PROGRESS = N_("Stopped: the last steps went in circles without getting anywhere. Start it again to go on.")
TOO_MANY_CHUNKS = N_("Stopped after {n} stretches of work. Start it again to go on.")
NUDGE = ("[Not from the user: you stopped without calling report. Call report now with the outcome (done, needs_input, blocked or "
         "failed) and a short summary.]")

current_worker: ContextVar[str] = ContextVar("current_worker", default="")      # the job this run belongs to ("" = not a job)
# the turn running now, set by the server in each turn: {"task_id", "turn_id", "source", "unattended"}
current_turn: ContextVar[dict[str, Any] | None] = ContextVar("current_turn", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, task TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, parent_task_id INTEGER, parent_source TEXT NOT NULL DEFAULT 'chat',
    task_id INTEGER, turn_id TEXT NOT NULL DEFAULT '', tab TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    finished_at REAL, steps INTEGER NOT NULL DEFAULT 0, max_steps INTEGER NOT NULL, deadline_at REAL NOT NULL, minutes INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, token_cap INTEGER NOT NULL,
    report TEXT NOT NULL DEFAULT '', wait_on TEXT NOT NULL DEFAULT '', login TEXT NOT NULL DEFAULT '', last_url TEXT NOT NULL DEFAULT '',
    status_text TEXT NOT NULL DEFAULT '', lines TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '', keep_tab INTEGER NOT NULL DEFAULT 0,
    segments INTEGER NOT NULL DEFAULT 0, auto_resume INTEGER NOT NULL DEFAULT 0, wake_at REAL NOT NULL DEFAULT 0, retries INTEGER NOT NULL DEFAULT 0,
    question_id TEXT NOT NULL DEFAULT '', cut TEXT NOT NULL DEFAULT '', cuts INTEGER NOT NULL DEFAULT 0,
    pickups INTEGER NOT NULL DEFAULT 0, picked_up_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS worker_messages (
    worker_id TEXT NOT NULL, seq INTEGER NOT NULL, message TEXT NOT NULL, PRIMARY KEY (worker_id, seq));
"""


def new_question_id() -> str:
    """The id of a job's open question: `q_` and 16 hex digits (worker_message's answer_to names it)."""
    return "q_" + secrets.token_hex(8)


BUSY_GIVE_UP = 48 * 3600.0   # a job waiting for a busy / unreachable model gives up after this long in all


@dataclass(slots=True)
class Limits:
    concurrent: int = 3        # jobs running at once per copy
    steps: int = 200           # steps of a browser job (research: half, general: under a third)
    minutes: int = 60          # minutes of a browser job (the others: half)
    tokens: int = 4_000_000    # input + output tokens per job, counted from its last start

    def budget(self, kind: str, deadline_min: Any = None) -> tuple[int, int]:
        share = {"browser": 1.0, "research": 0.5, tool_roles.CHECKOUT_KIND: 0.5, tool_roles.PHONE_KIND: 0.5}.get(kind, 0.3)
        steps = max(10, int(self.steps * share))
        minutes = max(5, int(self.minutes * (1.0 if kind == "browser" else 0.5)))
        try:
            if deadline_min is not None and str(deadline_min).strip():
                minutes = max(1, min(MAX_DEADLINE_MIN, int(float(deadline_min))))
        except (TypeError, ValueError):
            pass
        return steps, minutes


@dataclass
class WorkerHost:
    """What the server lends the jobs."""
    start_turn: Callable[..., Any]                       # (row, messages, max_steps, tools, observe) -> Turn (has .id .task_id .ended .done .final_status)
    cancel_turn: Callable[[Any], None]
    handoff: Callable[[dict[str, Any], str, str], Awaitable[None]]    # (row, hidden message, notification head)
    emit: Callable[[dict[str, Any], list[int], list[str]], Awaitable[None]]   # a turn event to the running turns of these tasks / ids
    tools: Callable[[], list[Tool]]                      # every tool of the app (the jobs' list is made from it)
    browser: bool = False
    pay_split: bool = True                               # only a job of kind "checkout" has the payment tools (tool_roles.py)
    phone_split: bool = True                             # only a job of kind "phone" has the phone tools (tool_roles.py)
    browser_split: bool = True                           # the chat does not click or type on websites; a job does (tool_roles.py)
    tab_open: Callable[[str], bool] = lambda _k: False
    has_picture: Callable[[str], bool] = lambda _k: False
    controlled: Callable[[str], bool] = lambda _k: False
    retire_tab: Callable[[str], Awaitable[Any]] | None = None
    login_saved: Callable[[str], bool] = lambda _n: False
    window: Callable[[], int] = lambda: 150_000
    bind_profile: Callable[[str, str], Any] = lambda _key, _profile: None   # (tab key, named browser profile id): the job's window opens in it
    on_delegate: Callable[[str, str], Any] = lambda _turn_id, _wid: None   # (parent turn id, job id): the job takes on the turn's permissions
    on_end: Callable[[str, str], Any] = lambda _wid, _status: None   # (job id, status) a job ended without a report (stopped)
    # the model's usage limit (model_limits.py): the paused line, the hold's end now (None = not held), and the one notice
    # (row, until) -> True once it was given (False: not now, e.g. quiet time)
    limit_text: Callable[[float | None], str] = lambda _until: ""
    limit_until: Callable[[], float | None] = lambda: None
    limit_notice: Callable[[dict[str, Any], float], bool] = lambda _row, _until: True
    # how far the job's allowed items are: (done, total) from its task permissions, or None
    progress: Callable[[str], tuple[int, int] | None] = lambda _wid: None
    # is a running turn working with this browser tab / the phone right now? (a hand-back then needs no hidden message: the turn gets the note)
    turn_on: Callable[[str], bool] = lambda _k: False
    # a cut job (job_cuts.py): the unknown records of a task as (id, tool, what it was); the open pay attempt of a tab; the tab book's
    # state of a tab ("kept" | "new" | "held" | "none"); moving records to a picked-up run; is a step that sends or pays in flight?
    cut_records: Callable[[int | None], list[tuple[int, str, str]]] = lambda _task: []
    cut_pay: Callable[[str], str | None] = lambda _tab: None
    tab_state: Callable[[str], str] = lambda _tab: "none"
    adopt_records: Callable[[list[int], int | None], None] = lambda _ids, _task: None
    step_busy: Callable[[], bool] = lambda: False


@dataclass
class _Live:
    task: asyncio.Task | None = None
    started_at: float = 0.0                        # when this run of the job began (_launch)
    turn: Any = None
    inbox: list[dict[str, Any]] = field(default_factory=list)
    stop: bool = False
    timed_out: bool = False
    report: dict[str, Any] | None = None
    product_refused: int = 0
    report_products: list[Any] | None = None
    seen: set[str] = field(default_factory=set)   # distinct tool calls that succeeded in this job (progress)
    chunk_progress: int = 0                        # of them, in the stretch of work going on now
    chunks: int = 0                                # fresh budgets given since the job was (re)started
    progress: str = ""                             # "300 / 1,545 · label" from job_progress: stays on the job card while it works


class _Recorder:
    """The job's transcript, rebuilt from the turn's events as they come and written row by row (a restart keeps every finished step)."""

    def __init__(self, workers: Workers, wid: str, start: int, live: _Live | None = None):
        self.w, self.wid, self.start, self.next = workers, wid, start, start
        self.live = live
        self.text: list[str] = []
        self.open: dict[str, str] = {}
        self.calls: dict[str, str] = {}
        self.started = 0
        self.step_limit = False
        self.done: dict[str, Any] | None = None
        self.error = ""
        self.login = ""
        self.device = False   # the job showed the phone to the owner (a "device" card): it waits on the phone, not on a tab
        self.cleared = False  # the first finished call of this stretch resets the job's cut count (a pick-up that made progress)

    def add(self, msg: dict[str, Any]) -> None:
        self.w._append(self.wid, self.next, msg)
        self.next += 1

    def flush_text(self) -> None:
        t = "".join(self.text)
        self.text.clear()
        if t.strip():
            self.add({"role": "assistant", "content": t})

    def __call__(self, item: dict[str, Any] | None) -> None:
        try:
            self._see(item)
        except Exception:  # noqa: BLE001 - the record never breaks the job
            log.exception("worker %s: could not record a step", self.wid)

    def _see(self, item: dict[str, Any] | None) -> None:
        if item is None:
            self.flush_text()
            for cid in list(self.open):
                self.add({"role": "tool", "tool_call_id": cid, "content": CUT_NOTE})
            self.open.clear()
            return
        kind = item.get("type")
        if kind == "text":
            self.text.append(str(item.get("text", "")))
        elif kind == "tool_started":
            t = "".join(self.text)
            self.text.clear()
            self.add({"role": "assistant", "content": t or None, "tool_calls": [{"id": item["id"], "type": "function", "function": {
                "name": item["name"], "arguments": json.dumps(item.get("arguments") or {}, ensure_ascii=False)}}]})
            self.open[item["id"]] = item["name"]
            self.calls[item["id"]] = item["name"] + " " + json.dumps(item.get("arguments") or {}, ensure_ascii=False, sort_keys=True)
            self.started += 1
            self.w._line(self.wid, item)
        elif kind == "tool_result":
            key = self.calls.pop(str(item.get("id")), "")
            if key and self.live is not None and not item.get("is_error") and key not in self.live.seen:
                self.live.seen.add(key)
                self.live.chunk_progress += 1
            if item.get("id") in self.open:
                self.open.pop(item["id"])
                self.add({"role": "tool", "tool_call_id": item["id"], "content": str(item.get("output", ""))})
            if not item.get("is_error") and not self.cleared:
                self.cleared = True
                self.w._clear_cuts(self.wid)
            out = str(item.get("output", ""))
            if item.get("name") == "request_login" and not item.get("is_error") and (m := re.search(r"browser_login\(name='([^']+)'\)", out)):
                self.login = m.group(1)
                self.w._update(self.wid, login=self.login)
        elif kind in ("retry", "fallback"):   # the turn starts over from the same messages: forget what this pass wrote
            self.w._truncate(self.wid, self.start)
            self.next, self.started = self.start, 0
            self.text.clear()
            self.open.clear()
        elif kind == "step_limit":
            self.step_limit = True
        elif kind == "done":
            self.done = item
        elif kind == "error":
            self.error = str(item.get("message", ""))
        elif kind == "device":
            self.device = True
        elif kind == "browser":
            self.w._update(self.wid, last_url=str(item.get("url") or "")[:500], status_text=self.w._status(self.wid, str(item.get("status") or "")))


# ---- the app's API for jobs (mounted by the server; contracts next to serialize() in server.py) ------------------------------------
class ResumeIn(BaseModel):
    note: str = Field(default="", max_length=4000)


class WorkerMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    secrets: str | None = Field(default=None, pattern="^(allow|mask)$")   # as the chat's TalkIn.secrets (secret_guard.py)


class Workers:
    def __init__(self, store: Any, host: WorkerHost, limits: Limits | None = None, *, resume_on_start: bool = True,
                 clock: Callable[[], float] = time.time):
        self.store, self.host, self.limits, self.resume_on_start, self._clock = store, host, limits or Limits(), resume_on_start, clock
        self.products = ProductResults(store)
        self.chat_views: Any = None   # the chat's views (chat_views.py), set by server.py
        self._live: dict[str, _Live] = {}
        self._owner_words: dict[str, tuple[str, str]] = {}   # job -> (key, the owner's own message passed to it); done_once uses it once
        self._queue: list[tuple[str, str]] = []     # automatic resumes waiting for a free slot: (id, why)
        self._closing = False
        self._cut_seen: set[str] = set()            # jobs this process already cut at its start (startup may run again after a Keyguard unlock)
        with store._lock:
            store._db.executescript(SCHEMA)
            have = {r[1] for r in store._db.execute("PRAGMA table_info(workers)")}
            if "wake_at" not in have:
                store._db.execute("ALTER TABLE workers ADD COLUMN wake_at REAL NOT NULL DEFAULT 0")
            if "retries" not in have:
                store._db.execute("ALTER TABLE workers ADD COLUMN retries INTEGER NOT NULL DEFAULT 0")
            if "question_id" not in have:   # the id of the open question of a job that reported needs_input ("q_" + 16 hex), "" otherwise
                store._db.execute("ALTER TABLE workers ADD COLUMN question_id TEXT NOT NULL DEFAULT ''")
            if "parent_cond" not in have:   # the conditional task (watcher:N / schedule:N) that handed the job over, "" for a chat
                store._db.execute("ALTER TABLE workers ADD COLUMN parent_cond TEXT NOT NULL DEFAULT ''")
            for col, ddl in (("profile", "TEXT NOT NULL DEFAULT ''"), ("cut", "TEXT NOT NULL DEFAULT ''"), ("cuts", "INTEGER NOT NULL DEFAULT 0"),
                             ("pickups", "INTEGER NOT NULL DEFAULT 0"), ("picked_up_at", "REAL NOT NULL DEFAULT 0")):
                if col not in have:   # job_cuts.py: the last cut, the cuts in a row, the pick-ups, when the last one started
                    store._db.execute(f"ALTER TABLE workers ADD COLUMN {col} {ddl}")
            store._db.commit()

    # ------------------------------------------------------------------ storage
    def _row(self, wid: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM workers WHERE id = ?", (str(wid),))
        return rows[0] if rows else None

    def _update(self, wid: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = self._clock()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.store._run(f"UPDATE workers SET {cols} WHERE id = ?", (*fields.values(), wid))

    def _append(self, wid: str, seq: int, msg: dict[str, Any]) -> None:
        self.store._run("INSERT OR REPLACE INTO worker_messages(worker_id, seq, message) VALUES(?,?,?)",
                        (wid, seq, json.dumps(msg, ensure_ascii=False)))

    def _truncate(self, wid: str, seq: int) -> None:
        self.store._run("DELETE FROM worker_messages WHERE worker_id = ? AND seq >= ?", (wid, seq))

    def _next_seq(self, wid: str) -> int:
        return int(self.store._all("SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM worker_messages WHERE worker_id = ?", (wid,))[0]["n"])

    def transcript(self, wid: str) -> list[dict[str, Any]]:
        return [json.loads(r["message"]) for r in self.store._all(
            "SELECT message FROM worker_messages WHERE worker_id = ? ORDER BY seq", (wid,))]

    def _clear_cuts(self, wid: str) -> None:
        self.store._run("UPDATE workers SET cuts = 0 WHERE id = ? AND cuts > 0", (wid,))

    def _note_line(self, wid: str, text: str) -> None:
        """One line on the job card's steps list (not a step: nothing ran)."""
        r = self._row(wid)
        if r is None:
            return
        lines = json.loads(r["lines"] or "[]")[-(LINES_KEPT - 1):]
        lines.append({"at": round(self._clock(), 3), "text": text})
        self._update(wid, lines=json.dumps(lines, ensure_ascii=False))

    def _line(self, wid: str, item: dict[str, Any]) -> None:
        name = str(item.get("name", ""))
        if tool_labels.hidden(name):
            return
        args = item.get("arguments") or {}
        target = ""
        for k in ("url", "site", "query", "q", "selector", "name", "path"):
            v = args.get(k) if isinstance(args, dict) else None
            if isinstance(v, str) and v.strip():
                target = host_of(v) if k in ("url", "site") else v.strip()[:60]
                break
        label = item.get("label") or tool_labels.label(name)
        r = self._row(wid)
        if r is None:
            return
        lines = json.loads(r["lines"] or "[]")[-(LINES_KEPT - 1):]
        lines.append({"at": round(self._clock(), 3), "text": f"{label} · {target}" if target else label})
        self._update(wid, lines=json.dumps(lines, ensure_ascii=False), steps=r["steps"], status_text=self._status(wid, label))

    def _status(self, wid: str, label: str) -> str:
        """The job card's one line (80 characters): the job's own progress while it reported one, else what the last step was."""
        live = self._live.get(wid)
        return ((live.progress if live is not None and live.progress else label) or "")[:80]

    def _prune(self) -> None:
        old = self.store._all("SELECT id FROM workers WHERE status NOT IN ('running', 'needs_input', 'blocked', 'interrupted') "
                              "ORDER BY created_at DESC LIMIT -1 OFFSET ?", (KEEP_WORKERS,))
        for r in old:
            self.store._run("DELETE FROM worker_messages WHERE worker_id = ?", (r["id"],))
            self.store._run("DELETE FROM workers WHERE id = ?", (r["id"],))
        self.products.forget_workers({r["id"] for r in self.store._all("SELECT id FROM workers")})

    # ------------------------------------------------------------------ what the app and the agent see
    def open_job_for(self, turn_id: str = "", task_id: int | None = None, owner: str = "", known: Any = ()) -> dict[str, Any] | None:
        """A job that is still open (running, needs_input, blocked) and belongs to this reply: one it handed off itself, one of the same
        task, or one working in the reply's own tab or on the phone it shows. None when there is none."""
        rows = self.store._all("SELECT * FROM workers WHERE status = 'running' OR status IN (%s) ORDER BY created_at DESC"
                               % ",".join("?" * len(PAUSED)), PAUSED)
        for r in rows:
            if r["id"] in known or (task_id is not None and r["parent_task_id"] == task_id) or (turn_id and r["turn_id"] == turn_id):
                return r
            if owner and r["tab"] == owner:
                return r
            if r["status"] in PAUSED and (json.loads(r["wait_on"] or "{}")).get("kind") == "device":
                return r
        return None

    def running_count(self) -> int:
        return int(self.store._all("SELECT COUNT(*) AS n FROM workers WHERE status = 'running'")[0]["n"])

    def public(self, r: dict[str, Any], full: bool = False) -> dict[str, Any]:
        wait = json.loads(r["wait_on"] or "{}") if r["status"] in PAUSED else {}
        report = json.loads(r["report"]) if r["report"] else None
        reason = None
        if r["status"] in ("needs_input", "blocked", "failed"):
            reason = (report or {}).get("summary") or r["error"] or None
        elif r["status"] == "interrupted":
            reason = r["error"] or t(RESTART_REASON)
        wake_at = float(r["wake_at"]) if float(r.get("wake_at") or 0) > 0 else None
        if wait.get("kind") == "cut" and r["status"] == "interrupted":   # a cut job goes on at this time (job_cuts.next_pickup)
            wake_at = float(wait.get("until") or 0) or None
        status = "waiting" if r["status"] == "blocked" and wake_at is not None else r["status"]
        picked = None
        if r.get("picked_up_at") and r["status"] == "running":
            cut = Cut.of(r.get("cut") or "")
            picked = {"at": float(r["picked_up_at"]), "why": cut.why if cut is not None else "restart"}
        out = {"id": r["id"], "kind": r["kind"], "title": r["title"], "status": status, "parent_task_id": r["parent_task_id"],
               "task_id": r["task_id"], "turn_id": r["turn_id"] or None, "tab": r["tab"], "started_at": r["created_at"],
               "updated_at": r["updated_at"], "finished_at": r["finished_at"], "steps": r["steps"], "max_steps": r["max_steps"],
               "deadline_at": r["deadline_at"] if r["status"] == "running" else None, "wake_at": wake_at,
               "waiting_for": wait.get("kind"),
               "login": wait.get("login"), "reason": reason, "status_text": r["status_text"] or None, "last_url": r["last_url"] or None,
               "thumbnail": f"/api/browser/sessions/{r['tab']}/screenshot" if self.host.has_picture(r["tab"]) else None,
               "progress": ({"done": p[0], "total": p[1]} if (p := self.progress_of(r["id"])) else None), "picked_up": picked}
        if r["status"] in ("needs_input", "blocked") and r.get("question_id"):   # a paused job's question: answer_to names it
            out["question_id"] = r["question_id"]
        if full:
            out.update(task=r["task"], url=r["url"] or None, files=json.loads(r["files"] or "[]"), report=report,
                       lines=json.loads(r["lines"] or "[]"), segments=r["segments"], input_tokens=r["input_tokens"],
                       output_tokens=r["output_tokens"], error=r["error"] or None, keep_tab=bool(r["keep_tab"]))
        return out

    def list(self, limit: int = 50, status: str | None = None, parent_task_id: int | None = None) -> list[dict[str, Any]]:
        if status == "waiting":
            rows = self.store._all("SELECT * FROM workers WHERE (status = 'waiting' OR (status = 'blocked' AND wake_at > 0)) AND (? IS NULL OR parent_task_id = ?) "
                                   "ORDER BY created_at DESC LIMIT ?", (parent_task_id, parent_task_id, max(1, min(limit, 200))))
        else:
            rows = self.store._all("SELECT * FROM workers WHERE (? IS NULL OR status = ?) AND (? IS NULL OR parent_task_id = ?) "
                                   "ORDER BY created_at DESC LIMIT ?", (status, status, parent_task_id, parent_task_id, max(1, min(limit, 200))))
        return [self.public(r) for r in rows]

    def get(self, wid: str) -> dict[str, Any] | None:
        r = self._row(wid)
        return self.public(r, full=True) if r else None

    def waits_for_owner(self, key: str) -> bool:
        """A job's tab ("task:<job id>") whose job ended its run asking the owner for something (needs_input): the browser list
        shows it as "waiting", not as an idle tab (owner report 2026-11-19)."""
        if not key.startswith("task:w"):
            return False
        r = self._row(key[5:])
        return r is not None and r["status"] == "needs_input"

    def kind_of(self, wid: str) -> str:
        """The kind of a job ("browser", "research", ...), "" when there is no such job."""
        r = self._row(wid)
        return str(r["kind"] or "") if r else ""

    def prompt_block(self, within: float = 86400) -> str:
        """For the main agent's turn lines: the jobs that run or wait now and the ones that ended in the last day, with their state
        right now. The chat may still say "it runs in the background" after the user stopped a job from the Work page (a stop sends
        no message), so without this a reply would tell them it is still going."""
        now = self._clock()
        lines = []
        for w in self.list(10):
            st, at = w["status"], float(w.get("updated_at") or 0)
            if st not in ("running", "needs_input", "blocked") and now - at > within:
                continue
            ago = max(0, int((now - at) // 60))
            state = {"running": "running now",
                     "done": f"finished {ago} min ago",
                     "failed": f"ended without success {ago} min ago",
                     "stopped": f"stopped {ago} min ago: it is not running and does nothing more unless the user asks again",
                     "interrupted": f"cut off by a server restart {ago} min ago, not running now"}.get(st, "paused, waiting for the user")
            lines.append(f"- {w['id']} · {w['title']} · {state}")
        if not lines:
            return ""
        return ("Background jobs, their state right now (this overrides what earlier messages say about them; bring them up only when "
                "it matters or the user asks):\n" + "\n".join(lines))

    def holds_tab(self, key: str) -> bool:
        """The browser tab of a job that is running, paused (the user may take it over and hand it back) or kept: it must stay open."""
        if not key.startswith("task:w"):
            return False
        r = self._row(key[5:])
        if r is None:
            return False
        now = self._clock()
        if r["status"] == "running":
            return True
        if r["status"] in PAUSED:
            return now - r["updated_at"] < PAUSED_TAB_SECONDS
        return bool(r["keep_tab"]) and r["status"] == "done" and now - (r["finished_at"] or 0) < KEPT_TAB_SECONDS

    # ------------------------------------------------------------------ starting, going on, stopping
    def _notes(self) -> str:
        """What a job that is not the payment specialist or the phone worker is told it does not have (the splits that are on)."""
        return ((FINDER_NOTE if self.host.pay_split else "") + (PHONE_NOTE if self.host.phone_split else "")
                + (BROWSER_NOTE if self.host.browser_split else ""))

    def _brief(self, wid: str, task: str, kind: str, url: str, files: list[str], steps: int, minutes: int) -> str:
        lines = [f"[Not from the user: you are a background job ({wid}) the main assistant started for the user. Nobody watches this run "
                 "and nobody reads text you write: work with your tools until the job is finished, then call report. The job:]", task]
        if url and kind == tool_roles.CHECKOUT_KIND:   # owner answer 2026-11-19 (S7b): the cart address is never opened; the shop's home page is
            parts = urlsplit(url)
            lines.append(f"Start at: {parts.scheme}://{parts.netloc}/ (the shop's home page; the cart is reached by clicking the shop's own "
                         "cart link or button, never by an address you were given)")
        elif url:
            lines.append(f"Start at: {url}")
        if files:
            lines.append("Files to use (in your files; read them with read_file or extract_document): " + ", ".join(files))
        if kind == tool_roles.CHECKOUT_KIND:
            lines.append(CHECKOUT_BRIEF + (BROWSER_NOTE if self.host.browser_split else ""))
        elif kind == "browser":
            lines.append("Use your browser tools; this job has a browser tab of its own." + self._notes())
        elif kind == "research":
            lines.append("Look things up with web_search and web_fetch (the browser only for pages that need it) and read the sources; "
                         "put the addresses you used in details. This browser is not signed in to any site. When a step needs a "
                         "sign-in, skip it and say in your report what you could not check." + self._notes())
        elif kind == tool_roles.PHONE_KIND:
            lines.append(PHONE_BRIEF)
        elif self.host.phone_split or self.host.browser_split:
            lines.append(((PHONE_NOTE if self.host.phone_split else "") + (BROWSER_NOTE if self.host.browser_split else "")).strip())
        many = ("If the job does the same kind of step on many items (all / every / these N), find the items first, then ask once with "
                "the real count and the list (report needs_input), then do them one after another. " if kind == tool_roles.PHONE_KIND else
                "If the job does the same kind of step on many items (all / every / these N), find the items first, then call "
                "request_task_permission once with the real count and the list, then do them one after another. ")
        lines.append(many + "Work in order and "
                     "keep track of what is done: when a stretch of work uses its budget and you were getting somewhere, a fresh budget "
                     "follows by itself. Keep going until every item is done or something really stops you (a limit, a refusal, a check you "
                     "cannot solve, an approval declined); do not stop to ask whether to continue. Show how far you are with "
                     "job_progress(done, total, label) every 10-25 items.")
        lines.append(
            f"Budget: about {steps} steps and {minutes} minutes. Finish by calling report(status, summary, details?, keep_tab?) once, as your last step:\n"
            "- done: the job is complete. summary: the result in the user's language, with facts (prices, times, numbers) copied exactly.\n"
            "- needs_input: you need the user: a login, a choice, a one-time code, or a step they must do on the page. A step the site requires "
            "before it goes on that your task forbids or does not mention is the user's choice: do not take it, and do not back out of the site's "
            "flow to look for a way around; report needs_input at once, naming that step and what it does, and asking whether to take it. Before "
            "you pause for anything else but a login or a code, try 2-3 other ways yourself (the per-item action again, the site's own request, another "
            "page or tool). Then the summary says in 2-4 lines what you tried and why each failed, the concrete options with your "
            "recommendation, and ends with one question; never just \"tell me how\". When a website login "
            "is missing, call request_login for that site first. You will be resumed here, with this history and this tab, once it is "
            "saved or once the user hands the browser back.\n"
            "- blocked: a hard stop you cannot get past (the site refuses you).\n"
            "A human check on a page: you may try it yourself, at most twice (the page note says how); after that it is handed to the "
            "user by itself and your browser step waits: when it returns, follow its note (go on from the page, or go on without that "
            "site and say so in the report). Do not report for it.\n"
            "- failed: something broke and you could not finish.\n"
            "keep_tab: true leaves the browser tab open for the user to look at. Approvals you ask for go to the user's phone and may take "
            "a while; if one is declined or not answered, report needs_input and name the step that waits. You cannot start other "
            "background jobs.")
        return "\n".join(lines)

    async def delegate(self, args: dict[str, Any], *, by_app: bool = False) -> str:
        """The `delegate` tool. `by_app`: the app itself starts the job (the hand-over of a final checkout step, tools/checkout_handover.py),
        so the caller may be a job."""
        if current_worker.get() and not by_app:
            return ("Error: you are a background job yourself and cannot start another. Do the work with your own tools, or call "
                    "report(status='needs_input') if you need the user.")
        task = str(args.get("task") or "").strip()
        if not task:
            return "Error: task is required: describe the whole job so it can be done without asking (what, where, what result to bring back)."
        kind = str(args.get("kind") or "general").strip().lower()
        if kind not in KINDS:
            return f"Error: kind must be one of {', '.join(KINDS)}."
        if kind in ("browser", tool_roles.CHECKOUT_KIND) and not self.host.browser:
            return f"Error: this copy has no browser, so a {kind} job cannot run. Use kind research or general, or do it yourself."
        if kind == tool_roles.PHONE_KIND:
            if not any(tool_roles.is_phone_tool(t.name) for t in self.host.tools()):
                return "Error: this copy has no Android device set up, so a phone job cannot run. Tell the user the device is not set up."
            if (other := self._open_phone()) is not None:
                return (f"Error: phone job {other} is still open. There is one device, so one phone job at a time: continue that job with "
                        "worker_message (a correction, the user's answer) or resume instead of starting another.")
        if (n := self.running_count()) >= self.limits.concurrent:
            return (f"Error: {n} background jobs are running already (the most at once). Wait for one to report, or stop one with "
                    "worker_stop, then try again.")
        url = str(args.get("url") or "").strip()
        if kind == tool_roles.PHONE_KIND:
            url = ""   # the device is the job's surface, there is no page to start on
        if url and not re.match(r"^https?://[^\s/]+", url):
            if re.match(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", url, re.I):
                url = "https://" + url
            else:
                return "Error: url must be a web address (https://...)."
        if kind == tool_roles.CHECKOUT_KIND:
            if not url:
                return ("Error: a checkout job needs url: the shop's home page (it clicks to the cart itself). Write the whole assignment in task: the items "
                        "with variants and quantities, the quoted total and currency, delivery and contact choices, the limits the user set.")
            if (other := self._open_checkout(host_of(url))) is not None:
                return (f"Error: checkout job {other} for this shop is still open. A shop gets one checkout window at a time: continue that "
                        "job with worker_message or resume instead of starting another (a new job is not a way to retry an unresolved order).")
        profile = str(args.get("profile") or "").strip()
        if profile and kind == "browser":   # another account on the same site: a browser job only (research is signed out, a checkout is the owner's)
            if not any(p["id"] == profile for p in self.store.browser_profiles()):
                return "Error: there is no browser profile with this id. browser_profiles lists them; leave profile out for the default one."
        else:
            profile = ""
        files = [str(f).strip()[:200] for f in (args.get("files") or []) if str(f).strip()][:10] if isinstance(args.get("files"), list) else []
        steps, minutes = self.limits.budget(kind, args.get("deadline_min"))
        parent = current_turn.get() or {}
        wid = "w" + secrets.token_hex(3)
        while self._row(wid) is not None:
            wid = "w" + secrets.token_hex(3)
        now = self._clock()
        title = str(args.get("title") or "").strip()[:80] or title_from(task)
        self.store._run(
            "INSERT INTO workers(id, kind, title, task, url, files, status, parent_task_id, parent_source, parent_cond, tab, created_at, "
            "updated_at, max_steps, deadline_at, minutes, token_cap) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (wid, kind, title, task[:8000], url, json.dumps(files, ensure_ascii=False), "running", parent.get("task_id"),
             str(parent.get("source") or "chat"), str(parent.get("cond") or "")[:80], f"task:{wid}", now, now, steps, now + minutes * 60,
             minutes, self.limits.tokens))
        if profile:
            self.store._run("UPDATE workers SET profile = ? WHERE id = ?", (profile, wid))
            self.host.bind_profile(f"task:{wid}", profile)
        self._append(wid, 0, {"role": "user", "content": self._brief(wid, task, kind, url, files, steps, minutes)})
        try:
            self.host.on_delegate(str(parent.get("turn_id") or ""), wid)
        except Exception:  # noqa: BLE001 - without it the job's steps ask as usual
            log.exception("worker %s: could not pass on the task's permissions", wid)
        self._prune()
        self._launch(wid, "")
        await emit_ui({"type": "worker", "id": wid, "status": "running", "title": title})
        if self.chat_views is not None and str(parent.get("source") or "chat") in ("chat", "api"):
            # a job a chat asked for shows its tab (or the phone) in the chat from its start (chat_views.py); its first card follows
            if kind == tool_roles.PHONE_KIND:
                self.chat_views.open(PHONE_KEY, "phone", worker_id=wid)
            elif kind in ("browser", tool_roles.CHECKOUT_KIND):
                self.chat_views.open(f"task:{wid}", "browser", worker_id=wid)
        where = "on the Android device" if kind == tool_roles.PHONE_KIND else "in a browser tab of its own"
        return (f"Handed off; you will hear back when it is done. (job {wid}: {kind}, up to {steps} steps / {minutes} min, {where}.) "
                "It runs on its own; its report comes back to you by itself as a handoff message. Do not check on it with workers_list "
                "or worker_status now: tell the user in one short line that it is underway, and go on.")

    def job_kind(self, wid: str) -> str:
        r = self._row(wid)
        return str(r["kind"]) if r is not None else ""

    def open_checkout(self, host: str) -> str | None:
        return self._open_checkout(host)

    def _open_checkout(self, host: str) -> str | None:
        """The id of a checkout job on this shop that is running or paused (one checkout window per shop at a time), else None."""
        for r in self.store._all("SELECT id, url FROM workers WHERE kind = ? AND status IN ('running', 'needs_input', 'blocked', 'interrupted')",
                                 (tool_roles.CHECKOUT_KIND,)):
            if host and host_of(r["url"]) == host:
                return str(r["id"])
        return None

    def _open_phone(self) -> str | None:
        """The id of a phone job that is running or paused (there is one device), else None."""
        rows = self.store._all("SELECT id FROM workers WHERE kind = ? AND status IN ('running', 'needs_input', 'blocked', 'interrupted') "
                               "ORDER BY created_at LIMIT 1", (tool_roles.PHONE_KIND,))
        return str(rows[0]["id"]) if rows else None

    def run_started(self, wid: str) -> float:
        """When this run of the job began (0 when it is not known)."""
        live = self._live.get(wid)
        return live.started_at if live else 0.0

    def _launch(self, wid: str, note: str) -> None:
        live = self._live.setdefault(wid, _Live())
        live.started_at = self._clock()
        live.stop = live.timed_out = False
        live.report, live.turn = None, None
        live.chunk_progress = live.chunks = 0
        try:
            loop = asyncio.get_running_loop()
            live.task = loop.create_task(self._run(wid, note), context=contextvars.Context())
        except RuntimeError:
            live.task = None

    def resume(self, wid: str, note: str = "", *, why: str = "", auto: bool = False, cut: Cut | None = None) -> tuple[bool, str]:
        """Go on with a job that is not running: same transcript, same tab (a new one when it is gone), a fresh budget from here.
        `cut`: an automatic pick-up of a cut job (its pick-up note in `why` says the tab; the cut count stays). Any other resume starts
        the count again. (ok, "resumed" | "queued" | "running" | "limit" | "missing")."""
        r = self._row(wid)
        if r is None:
            return False, "missing"
        live = self._live.get(wid)
        if r["status"] == "running" or (live and live.task and not live.task.done()):
            return False, "running"
        if self.running_count() >= self.limits.concurrent:
            if auto:
                self._queue = [q for q in self._queue if q[0] != wid] + [(wid, why or note)]
                return True, "queued"
            return False, "limit"
        steps, minutes = self.limits.budget(r["kind"])
        now = self._clock()
        parts = [why or "You are resumed."]
        if note.strip():
            parts.append(f"Message for you: {note.strip()}")
        if cut is None and r["last_url"] and not self.host.tab_open(r["tab"]):
            parts.append(f"Your browser tab was closed meanwhile; a new blank tab will open. Continue from the history above. Your last page "
                         f"was {r['last_url']}: if you need it, open that site's home page and get back to it through the site's own links.")
        if cut is None:   # a pick-up's note ends with its own tail (job_cuts.PICKUP_TAIL)
            parts.append("Go on from where you stopped and finish with report.")
        self._update(wid, status="running", wait_on="", report="", error="", finished_at=None, auto_resume=0, keep_tab=0, question_id="",
                     wake_at=0.0, cuts=r["cuts"] if cut is not None else 0, max_steps=r["steps"] + steps, deadline_at=now + minutes * 60, minutes=minutes,
                     token_cap=r["input_tokens"] + r["output_tokens"] + self.limits.tokens)
        self._queue = [q for q in self._queue if q[0] != wid]
        self._launch(wid, "[Not from the user: " + " ".join(parts) + "]")
        self._emit(wid)
        return True, "resumed"

    async def stop(self, wid: str) -> bool:
        r = self._row(wid)
        if r is None:
            return False
        self._owner_words.pop(wid, None)
        live = self._live.get(wid)
        self._queue = [q for q in self._queue if q[0] != wid]
        if live and live.task and not live.task.done():
            live.stop = True
            if live.turn is not None and not live.turn.done:
                self.host.cancel_turn(live.turn)
            try:
                await asyncio.wait_for(asyncio.shield(live.task), 5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001 - it ends on its own
                pass
            return True
        if r["status"] in ("done", "failed", "stopped"):
            return True
        self._update(wid, status="stopped", finished_at=self._clock(), wait_on="", keep_tab=0, wake_at=0.0, question_id="")
        self._emit(wid)
        try:
            self.host.on_end(wid, "stopped")
        except Exception:  # noqa: BLE001
            log.exception("worker %s: on_end failed", wid)
        await self._retire(r["tab"])
        return True

    def owner_said(self, wid: str, text: str) -> None:
        """The owner's own words reach this job (their chat message relayed by the main agent, or typed in the app for it)."""
        if text.strip():
            self._owner_words[wid] = (secrets.token_hex(4), text.strip()[:2000])

    def owner_words(self, wid: str) -> tuple[str, str] | None:
        """(key, the owner's own words) passed to this job and not used yet, or None. Only the server sets them (owner_said)."""
        return self._owner_words.get(wid) if wid else None

    def owner_words_used(self, wid: str, key: str) -> None:
        """done_once ran a step again on these words: they are used up (one "again" per message)."""
        if wid in self._owner_words and self._owner_words[wid][0] == key:
            self._owner_words.pop(wid, None)

    def message(self, wid: str, text: str, answer_to: str = "", *, owner_text: str = "") -> tuple[bool, str]:
        """A word for a job: a running one reads it before its next step; one that is not running is resumed with it. `answer_to` (the
        question id a job's needs_input report carries) makes it the answer to that question: refused ("stale" / "wrong") unless it is
        the job's open question. `owner_text`: the owner's own words this message carries (their chat message, or the app's text); a
        job's repeat check may use them once (done_once.py)."""
        r = self._row(wid)
        if r is None:
            return False, "missing"
        if owner_text:
            self.owner_said(wid, owner_text)
        if answer_to:
            if r["status"] not in ("needs_input", "blocked") or not r.get("question_id"):
                return False, "stale"
            if answer_to != r["question_id"]:
                return False, "wrong"
            return self.resume(wid, text, why="The user answered your question.")
        live = self._live.get(wid)
        if r["status"] == "running" and live and live.task and not live.task.done():
            live.inbox.append({"role": "user", "content": f"[Not from the user directly: a message for you from the main assistant: {text.strip()}]"})
            return True, "delivered"
        return self.resume(wid, text, why="A new message came for you.")

    # ------------------------------------------------------------------ automatic triggers
    def login_saved(self, name: str) -> list[str]:
        """The user saved a website login: jobs waiting for exactly that one go on."""
        out = []
        for r in self.store._all("SELECT * FROM workers WHERE status = 'needs_input'"):
            wait = json.loads(r["wait_on"] or "{}")
            if wait.get("kind") == "login" and wait.get("login") == name:
                ok, _ = self.resume(r["id"], why=f"The user saved the login {name!r}. If you are not on its sign-in page, open it, then call "
                                                f"browser_login(name={name!r}).", auto=True)
                if ok:
                    out.append(r["id"])
        return out

    def control_changed(self, key: str, device: str | None) -> None:
        """A tab changed hands. The user handing a paused job's tab back (they did a step on the page, solved a check) lets it go on."""
        if device is None:
            self.handed_back(key, "", "owner", "browser")

    def handed_back(self, key: str, summary: str = "", reason: str = "owner", kind: str = "browser") -> None:
        """The owner handed a browser tab (`key`) or the phone back, or lost it by a time-out (`reason`). `summary`: what they did there (the
        tools' own note for the agent's next look; never typed text). Paused jobs waiting on that tab / the phone go on by themselves. When no
        job and no running turn takes it up, the summary is written into the chat as hidden context (it does not wake the model; the next
        message sees it): a chat turn that ended still learns what the owner did and that handing back approved nothing."""
        woke = False
        if kind == "phone":
            rows = [r for r in self.store._all("SELECT * FROM workers WHERE status IN ('needs_input', 'blocked')")
                    if (json.loads(r["wait_on"] or "{}")).get("kind") == "device"]
            what = "phone"
        else:
            r = self._row(key[5:]) if key.startswith("task:w") else None
            rows = [r] if r is not None and r["status"] in ("needs_input", "blocked") else []
            what = "browser"
        for r in rows:
            lead = "The owner's control timed out, so it came back to you. " if reason == "timeout" else ""
            ok, _ = self.resume(r["id"], why=lead + (f"The user handed the {what} back to you (they may have done a step on it themselves). "
                                                     + ("Look at the screen with android_look and go on. " if what == "phone"
                                                        else "Read the page with browser_read and go on. ")
                                                     + "Handing it back approves no purchase."), auto=True)
            woke = woke or ok
        if woke or not summary or self.host.turn_on(key):
            return
        try:
            self.store.add_message("user", f"{HANDOFF_MARK}\nThe owner used the {what} themselves and handed it back; no job or reply was waiting on it.\n"
                                           f"{summary}")
        except Exception:  # noqa: BLE001 - the note is a courtesy; handing back never fails because of it
            log.exception("could not record a hand-back")

    def _drain(self) -> None:
        while self._queue and self.running_count() < self.limits.concurrent and not self._closing:
            wid, why = self._queue.pop(0)
            r = self._row(wid)
            if r is not None and r["status"] in PAUSED:
                if (json.loads(r["wait_on"] or "{}")).get("kind") == "cut":
                    self._pickup(wid)
                else:
                    self.resume(wid, why=why, auto=True)

    # ------------------------------------------------------------------ running
    def _worker_tools(self, kind: str = "general") -> list[Tool]:
        """The job's tools: every tool minus the ones that steer jobs; by role (tool_roles.py) a payment specialist has the paying tools and
        the browser's page tools only, the phone job has the phone's tools only, every other job has no paying and no phone tool."""
        base = [t for t in self.host.tools() if t.name not in MAIN_ONLY and t.name != "report"]
        base = tool_roles.for_kind(base, kind, pay_split=self.host.pay_split, phone_split=self.host.phone_split, browser_split=self.host.browser_split)
        return base + [self.progress_tool(), self.report_tool()]

    def _fit(self, msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A long job's history for the next request: older results as one-line digests, then the oldest steps left out (the brief stays)."""
        target = int(max(8000, self.host.window()) * 0.5)
        if request_tokens(msgs) <= target:
            return msgs
        msgs, _ = digest_tool_results(msgs, target, keep_last=3)
        if request_tokens(msgs) <= target:
            return msgs
        head, rest, dropped = msgs[:1], list(msgs[1:]), 0
        while len(rest) > 6 and request_tokens(head + rest) > target:
            rest.pop(0)
            dropped += 1
            while rest and rest[0].get("role") == "tool":
                rest.pop(0)
                dropped += 1
        return [*head, {"role": "user", "content": f"[Not from the user: {dropped} earlier messages of this job were left out to save room.]"}, *rest]

    async def _run(self, wid: str, note: str) -> None:
        live = self._live[wid]
        current_worker.set(wid)
        if (r0 := self._row(wid)) is not None and r0["profile"]:
            self.host.bind_profile(f"task:{wid}", r0["profile"])   # a resumed job's window opens in its profile again
        if note:
            self._append(wid, self._next_seq(wid), {"role": "user", "content": note})
        nudged = False
        outcome: tuple[str, str, str, bool] | None = None
        rec: _Recorder | None = None
        held: tuple[float] | None = None
        busy = False   # the model was busy / unreachable (resume.is_outage): the job pauses and goes on by itself
        try:
            while True:
                r = self._row(wid)
                if r is None or live.stop:
                    break
                left, time_left = r["max_steps"] - r["steps"], r["deadline_at"] - self._clock()
                spent = r["input_tokens"] + r["output_tokens"] >= r["token_cap"]
                if left <= 0 or time_left <= 0 or spent:
                    if live.chunk_progress >= CHUNK_PROGRESS and live.chunks < MAX_CHUNKS:
                        self._next_chunk(wid, r, live)   # getting somewhere: a fresh budget, and on
                        continue
                    if live.chunk_progress < CHUNK_PROGRESS:
                        outcome = ("blocked", t(NO_PROGRESS), "", False)
                    else:
                        outcome = ("blocked", t(TOO_MANY_CHUNKS, n=live.chunks + 1), "", False)
                    break
                rec = _Recorder(self, wid, self._next_seq(wid), live)

                def take(rec: _Recorder = rec) -> list[dict[str, Any]]:
                    got, live.inbox[:] = list(live.inbox), []
                    if got:
                        rec.flush_text()
                        for m in got:
                            rec.add(m)
                    return got

                token = inbox.set(take)
                try:
                    turn = self.host.start_turn(r, self._fit(self.transcript(wid)), left, self._worker_tools(r["kind"]), rec)
                except Exception as e:  # noqa: BLE001 - no model, ...
                    detail = getattr(e, "detail", None) or f"{type(e).__name__}: {e}"
                    outcome = ("failed", t("Could not start: {error}", error=str(detail)[:200]), "", False)
                    break
                finally:
                    inbox.reset(token)
                live.turn = turn
                self._update(wid, turn_id=turn.id, task_id=turn.task_id, segments=r["segments"] + 1)
                self._adopt_cut(wid, turn)
                self._emit(wid)
                try:
                    await asyncio.wait_for(asyncio.shield(turn.ended.wait()), max(1.0, time_left))
                except asyncio.TimeoutError:
                    live.timed_out = True
                    self.host.cancel_turn(turn)
                    await turn.ended.wait()
                used = int(rec.done.get("steps") or 0) if rec.done else rec.started
                tin, tout = (int(rec.done.get("input_tokens") or 0), int(rec.done.get("output_tokens") or 0)) if rec.done else (0, 0)
                r = self._row(wid) or r
                self._update(wid, steps=r["steps"] + max(used, 1 if rec.started else 0), input_tokens=r["input_tokens"] + tin,
                             output_tokens=r["output_tokens"] + tout)
                if self._closing:
                    return
                if live.report is not None:
                    rep = live.report
                    outcome = (rep["status"], rep["summary"], rep.get("details", ""), bool(rep.get("keep_tab")))
                    break
                if live.stop:
                    break
                if live.timed_out:
                    live.timed_out = False
                    continue   # the time is up: the top of the loop goes on in a fresh stretch, or stops a job going nowhere
                if rec.step_limit:
                    continue   # the step count is used up: the top of the loop says so
                if turn.final_status == "failed" and getattr(turn, "limited", False):
                    held = (getattr(turn, "limit_reset", None) or 0.0,)   # the model's usage is used up: pause, go on after the reset
                    break
                if turn.final_status == "failed" and getattr(turn, "outage", False):
                    busy = True   # 2026-11-11: Gemini's "high traffic / no capacity" ended a 130-step job twice; it waits now
                    break
                if turn.final_status == "failed":
                    outcome = ("failed", t("Stopped by an error: {error}", error=(turn.final_error or rec.error)[:200]), "", False)
                    break
                if turn.final_status == "stopped":   # stopped from the agent screen (POST /api/tasks/{id}/stop)
                    live.stop = True
                    break
                if nudged:   # it said it was done but never reported: its last words are the report
                    last = next((m.get("content") for m in reversed(self.transcript(wid))
                                 if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip()), "")
                    outcome = ("done", last[:2000], "", False) if last else ("failed", t("Stopped without a report."), "", False)
                    break
                nudged = True
                self._append(wid, self._next_seq(wid), {"role": "user", "content": NUDGE})
        except asyncio.CancelledError:
            if live.turn is not None and not live.turn.done:
                self.host.cancel_turn(live.turn)
            raise
        except Exception as e:  # noqa: BLE001 - a broken job says so instead of hanging as "running"
            log.exception("worker %s failed", wid)
            outcome = ("failed", t("Stopped by an error: {error}", error=type(e).__name__), "", False)
        if self._closing:
            return
        if held is not None and not live.stop:
            self._hold_for_limit(wid, held[0] or None)
            return
        if busy and not live.stop and self._hold_for_busy(wid):
            return
        if busy and outcome is None:
            outcome = ("failed", t("Stopped by an error: {error}", error=t("the AI model stayed unavailable for too long")), "", False)
        await self._settle(wid, outcome, rec)

    def _adopt_cut(self, wid: str, turn: Any) -> None:
        """A picked-up run owns the unknown records of the run it was cut from (once: the cut then names none)."""
        r = self._row(wid)
        cut = Cut.of(r["cut"]) if r is not None else None
        if cut is None or not cut.records:
            return
        try:
            self.host.adopt_records(list(cut.records), turn.task_id)
        except Exception:  # noqa: BLE001 - the records stay with the old run; the job goes on
            log.exception("worker %s: could not move the unknown records of the cut run", wid)
        self._update(wid, cut=replace(cut, records=(), texts=()).to_json())

    def _next_chunk(self, wid: str, r: dict[str, Any], live: _Live) -> None:
        """The job made progress in the stretch that just used its budget: a fresh step / time / token budget, and a note to go on."""
        steps, minutes = self.limits.budget(r["kind"])
        now = self._clock()
        live.chunks += 1
        live.chunk_progress = 0
        self._update(wid, max_steps=r["steps"] + steps, deadline_at=now + minutes * 60, minutes=minutes,
                     token_cap=r["input_tokens"] + r["output_tokens"] + self.limits.tokens)
        done = self.progress_of(wid)
        so_far = f"{done[0]} of {done[1]} items done" if done else f"stretch {live.chunks + 1}"
        self._append(wid, self._next_seq(wid), {"role": "user", "content": CHUNK_NOTE.format(done=so_far)})
        log.info("worker %s: a fresh budget for stretch %d (%s)", wid, live.chunks + 1, so_far)
        self._emit(wid)

    def progress_of(self, wid: str) -> tuple[int, int] | None:
        try:
            got = self.host.progress(wid)
        except Exception:  # noqa: BLE001
            return None
        return (int(got[0]), int(got[1])) if got and got[1] else None

    # ------------------------------------------------------------------ the model's usage limit (model_limits.py)
    def _hold_for_limit(self, wid: str, until: float | None) -> None:
        """The model's usage is used up: the job pauses as it is (transcript, tab), and goes on by itself after the reset
        (limit_tick). No report goes to the conversation: answering it would need the model too."""
        line = self.host.limit_text(until)
        self._update(wid, status="interrupted", error=line, auto_resume=0, keep_tab=1,
                     wait_on=json.dumps({"kind": "model_limit", "until": until or self._clock() + LIMIT_RETRY, "noted": False}))
        self._emit(wid)
        self._drain()

    def _hold_for_busy(self, wid: str) -> bool:
        """The model was busy or unreachable: pause the job as it is and go on by itself after a wait that grows (resume.next_wait:
        1, 5, 15, then every 60 minutes). False once it has waited BUSY_GIVE_UP in all: then it stops with its report."""
        from .resume import next_wait
        r = self._row(wid) or {}
        try:
            prev = json.loads(r.get("wait_on") or "{}")
        except ValueError:
            prev = {}
        now = self._clock()
        n = int(prev.get("n") or 0) if prev.get("kind") == "model_busy" else 0
        since = float(prev.get("since") or now) if prev.get("kind") == "model_busy" else now
        if now - since > BUSY_GIVE_UP:
            return False
        until = now + next_wait(n)
        self._update(wid, status="interrupted", error=t("The AI model is busy; the job goes on by itself in a few minutes."),
                     auto_resume=0, keep_tab=1,
                     wait_on=json.dumps({"kind": "model_busy", "until": until, "n": n + 1, "since": since}))
        self._emit(wid)
        self._drain()
        return True

    def wake_tick(self, now: float | None = None) -> list[str]:
        """Jobs paused with wake_at (e.g. after a site block) resume when wake_at is reached."""
        now = self._clock() if now is None else now
        out = []
        for r in self.store._all("SELECT * FROM workers WHERE status = 'blocked' AND wake_at > 0"):
            if float(r["wake_at"] or 0) <= now:
                wid = r["id"]
                self._update(wid, retries=int(r.get("retries") or 0) + 1, wake_at=0.0)
                note_text = "The site blocked the browser earlier; this is the one automatic retry. Go back to the site's home page and continue like a person."
                ok, _ = self.resume(
                    wid,
                    note=note_text,
                    why=note_text,
                    auto=True,
                )
                if ok:
                    out.append(wid)
        return out

    def tick(self, now: float | None = None) -> list[str]:
        return self.limit_tick(now)

    def limit_tick(self, now: float | None = None) -> list[str]:
        """Called every minute: jobs held by the usage limit tell the user once (when the host says it is a good time) and go on once
        the hold is over (a provider still held pushes it back)."""
        now = self._clock() if now is None else now
        out = []
        out.extend(self.wake_tick(now))
        for r in self.store._all("SELECT * FROM workers WHERE status = 'interrupted' AND wait_on LIKE '%model_busy%'"):
            try:
                wait = json.loads(r["wait_on"] or "{}")
            except ValueError:
                continue
            if wait.get("kind") == "model_busy" and float(wait.get("until") or 0) <= now:
                ok, _ = self.resume(r["id"], why="The AI model was busy and you were paused for a while; go on from where you were.",
                                    auto=True)
                if ok:
                    out.append(r["id"])
        for r in self.store._all("SELECT * FROM workers WHERE status = 'interrupted' AND wait_on LIKE '%cut%'"):
            try:
                wait = json.loads(r["wait_on"] or "{}")
            except ValueError:
                continue
            if wait.get("kind") == "cut" and float(wait.get("until") or 0) <= now and self._pickup(r["id"]):
                out.append(r["id"])
        for r in self.store._all("SELECT * FROM workers WHERE status = 'interrupted' AND wait_on LIKE '%model_limit%'"):
            try:
                wait = json.loads(r["wait_on"] or "{}")
            except ValueError:
                continue
            if wait.get("kind") != "model_limit":
                continue
            later = self.host.limit_until()
            if later and later > float(wait.get("until") or 0):
                wait["until"] = later
                self._update(r["id"], wait_on=json.dumps(wait))
            if float(wait.get("until") or 0) <= now:
                ok, _ = self.resume(r["id"], why="The AI model's usage limit was used up and you were paused; it has reset now.", auto=True)
                if ok:
                    out.append(r["id"])
            elif not wait.get("noted") and self.host.limit_notice(dict(r), float(wait["until"])):
                wait["noted"] = True
                self._update(r["id"], wait_on=json.dumps(wait))
        return out

    async def _settle(self, wid: str, outcome: tuple[str, str, str, bool] | None, rec: _Recorder | None) -> None:
        self._owner_words.pop(wid, None)   # the owner's words were for this run
        live = self._live.get(wid) or _Live()
        r = self._row(wid)
        if r is None:
            return
        now = self._clock()
        if outcome is None or live.stop:
            self._update(wid, status="stopped", finished_at=now, wait_on="", keep_tab=0)
            self._emit(wid)
            try:
                self.host.on_end(wid, "stopped")
            except Exception:  # noqa: BLE001
                log.exception("worker %s: on_end failed", wid)
            await self._retire(r["tab"])
            self._drain()
            return
        status, summary, details, keep = outcome
        wait: dict[str, Any] = {}
        wake_at = 0.0
        if status == "needs_input":
            login = (rec.login if rec else "") or r["login"]
            if login and not self.host.login_saved(login):
                wait = {"kind": "login", "login": login}
            elif rec is not None and rec.device:
                wait = {"kind": "device"}
            elif self.host.controlled(r["tab"]):
                wait = {"kind": "control"}
            else:
                wait = {"kind": "user"}
        elif status == "blocked":
            retry_min = live.report.get("retry_after_min") if live.report else None
            if retry_min is not None and int(r.get("retries") or 0) < 1 and not float(r.get("wake_at") or 0):
                wake_at = now + retry_min * 60.0
                wait = {"kind": "retry", "wake_at": wake_at}
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_later(max(0.0, wake_at - now), lambda: self.wake_tick())
                except RuntimeError:
                    pass
            else:
                wait = {"kind": "user"}
        report = {"status": status, "summary": summary.strip()[:4000], "details": (details or "").strip()[:8000], "keep_tab": keep, "at": now}
        if wake_at > 0:
            report["wake_at"] = wake_at
            if live.report and live.report.get("retry_after_min"):
                report["retry_after_min"] = live.report["retry_after_min"]
        if live.report and str(live.report.get("deadline") or "").strip():
            report["deadline"] = str(live.report["deadline"]).strip()[:40]   # when waiting longer is too late (followups.py reminds before it)
        if live.report and live.report.get("need"):
            report["need"] = live.report["need"]                             # a typed reason (REPORT_NEEDS), never read from the summary
        if live.report and live.report.get("reason"):
            report["reason"] = live.report["reason"]
        if getattr(live, "report_products", None):
            self.products.save(wid, live.report_products, now)
            report["products"] = [p.id for p in live.report_products]
        self._update(wid, status=status, report=json.dumps(report, ensure_ascii=False), wait_on=json.dumps(wait), keep_tab=int(keep),
                     wake_at=wake_at, finished_at=now if status in ("done", "failed") else None,
                     question_id=new_question_id() if status == "needs_input" or (status == "blocked" and wake_at <= 0) else "")
        self._emit(wid)
        if status in ("done", "failed") and not keep:
            await self._retire(r["tab"])
        self._drain()
        try:
            await self.host.handoff(self._row(wid) or r, self._handoff_text(self._row(wid) or r, report, wait), t(STATUS_HEAD[status]))
        except Exception:  # noqa: BLE001 - the report stays readable in the app
            log.exception("worker %s: the handoff to the conversation failed", wid)

    def _format_time(self, ts: float) -> str:
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime
            tz_name = self.store.profile().get("timezone") if hasattr(self.store, "profile") else None
            if tz_name:
                return datetime.fromtimestamp(ts, ZoneInfo(tz_name)).strftime("%H:%M")
        except Exception:
            pass
        return time.strftime("%H:%M", time.localtime(ts))

    def _view_of(self, wid: str) -> Any:
        """The chat view this job works in (its tab, or the phone), or None (chat_views.py)."""
        views = getattr(self, "chat_views", None)
        if views is None:
            return None
        return next((v for v in views.items() if v.worker_id == wid), None)

    def _handoff_text(self, r: dict[str, Any], rep: dict[str, Any], wait: dict[str, Any]) -> str:
        lines = [HANDOFF_MARK, f"The background job {r['id']} (\"{r['title']}\", {r['kind']}) that you started with delegate reported: "
                 f"{rep['status']}.", f"Summary: {rep['summary'] or '-'}"]
        if rep["details"]:
            lines.append(f"Details: {rep['details']}")
        if rep.get("products"):
            lines.append("Products this job saw on the sellers' own pages (offer only these, by id):")
            for pid in rep["products"]:
                p = self.products.get(pid)
                if p:
                    sale = f" (now {p.sale_price})" if p.sale_price else ""
                    lines.append(f"- {p.id} · {p.name} · {p.price}{sale} · {p.seller}")
            lines.append(
                "Offer them as one choice card: quick_replies with one option per product (a short name as the label) and each card's product set to "
                "its id, one of them recommended."
            )
            lines.append(
                "In your text give one short takeaway and the decisive difference, naming at most two; do not list them."
            )
            lines.append("Do not rebuild products from the summary.")
        if progress := self._progress_line(r):
            lines.append(progress)
        if (picks := int(r.get("pickups") or 0)) > 0:
            lines.append(f"It was cut off {picks} time(s) by a restart and picked up where it stopped by itself; say so in a few words "
                         "only if it matters to the owner.")
        st = rep["status"]
        if (view := self._view_of(r["id"])) is not None:   # its tab or the phone shown in the chat: only the main agent hides it
            target = "phone" if view.key == "android" else r["id"]
            if st in ("done", "failed", "stopped") and not view.owner_asked:
                lines.append(f"Its tab or the phone is still shown in the chat. When the owner no longer needs to see it, call chat_view(action='close', target='{target}', reason='done'); keep it if the owner asked to see it.")
            elif st in ("needs_input", "blocked"):
                lines.append("Its tab or the phone stays shown in the chat for the owner to act in; do not close it now.")
        if st == "done":
            lines.append("Pass the result on to the user.")
        elif st == "needs_input":
            if rep.get("need") == "site_data_reset":
                lines.append(f"Ask the user with quick_replies (two options: clear and retry / stop), using the user's language. If they agree to clear, call browser_forget_site first, then call resume(worker_id='{r['id']}', note='Cookies cleared. Sign in again and continue from the home page.').")
            else:
                how = {"login": f"It goes on by itself once the user saves the login {wait.get('login')!r} in the app.",
                       "control": "It goes on by itself once the user hands its browser tab back.",
                       "device": "It goes on by itself once the owner hands the phone back."}.get(
                    wait.get("kind", ""), f"When the user answers, call worker_message(id='{r['id']}', text=<their answer>, "
                                          f"answer_to='{r.get('question_id') or ''}').")
                if r.get("question_id"):
                    lines.append(f"Question id: {r['question_id']}")
                lines.append(f"Tell the user exactly what is needed. {how}")
        elif st == "blocked":
            if wait.get("wake_at"):
                time_str = self._format_time(float(wait["wake_at"]))
                lines.append(f"The job will retry by itself at {time_str}. Tell the user once, in one line (e.g. that the site is "
                             f"blocking for now and it will try again at {time_str}; they can keep talking).")
            else:
                site = registrable(urlsplit(str(r.get("last_url") or "")).hostname or "") if r.get("last_url") else ""
                if not site:
                    site = "the site"
                if r.get("parent_source") not in ("chat", "api"):
                    lines.append(f"Say in the result that {site} stopped the job and why; nothing else.")
                elif rep.get("reason") in ("site_refused", "sign_in", "human_check"):
                    lines.append(
                        f"Tell the user in one or two lines that {site} did not let Kelo's browser in, so nothing was found or bought there, "
                        f"and the next step: take over the browser (the live view) to sign in or get past it, or save that site's login; the job "
                        f"goes on by itself when they hand the browser back. If no other seller could be opened either, give only the refusal and "
                        f"the sign-in path, no prices. Never show prices, stock or links from a text search, not even as information."
                    )
                else:
                    lines.append("Say in one or two lines what stopped it and the next step. Never show prices, stock or links from a text search, not even as information.")
                if r.get("question_id"):
                    lines.append(f"Question id: {r['question_id']}. When the owner answers or decides, call worker_message(id='{r['id']}', "
                                 f"text=<their words>, answer_to='{r['question_id']}') to let it go on, or worker_stop(id='{r['id']}') when "
                                 "they no longer want it.")
        else:
            lines.append("Say that it stopped, why, and how far it got (the counts above are the server's own record: use them; never "
                         "guess that something was not done).")
        quiet = r["parent_source"] not in ("chat", "api") and st == "done"
        lines.append("Answer briefly in the user's language. Do not start a new job for the same thing; use resume or worker_message."
                     + (f" This came from a run the user is not watching: if nothing here is worth telling them, reply exactly NO_REPLY." if quiet else ""))
        return "\n".join(lines)

    def _progress_line(self, r: dict[str, Any]) -> str:
        """How far the job got, from the server's own records (never the model's memory): its steps, and the steps that committed
        something in the user's name and went through (approval_log: allowed by the user, by their request or a rule), by site.
        2026-11-11: a job stopped by a model error after 30+ applications was reported as "only the first one was done"."""
        try:
            tasks = [int(x["id"]) for x in self.store._all("SELECT id FROM tasks WHERE worker_id = ?", (r["id"],))]
            done: list[dict[str, Any]] = []
            if tasks:
                marks = ",".join("?" * len(tasks))
                done = self.store._all(f"SELECT site, COUNT(*) AS n FROM approval_log WHERE task_id IN ({marks}) AND decision LIKE 'allowed%' "
                                       "GROUP BY site ORDER BY n DESC", tuple(tasks))
        except Exception:  # noqa: BLE001 - the handoff goes out without it
            return ""
        parts = [f"Progress (server record): {int(r.get('steps') or 0)} steps"]
        if done:
            parts.append("; steps that committed something and went through: " + ", ".join(f"{d['n']} on {d['site']}" for d in done[:5]))
        else:
            parts.append("; no step that commits something went through")
        if r.get("last_url"):
            parts.append(f"; last page: {str(r['last_url'])[:200]}")
        return "".join(parts) + "."

    def _emit(self, wid: str) -> None:
        r = self._row(wid)
        if r is None:
            return
        item = {"type": "worker", "id": wid, "status": r["status"], "title": r["title"]}
        if (p := self.progress_of(wid)):
            item["progress"] = {"done": p[0], "total": p[1]}
        try:
            fut = self.host.emit(item, [r["parent_task_id"]] if r["parent_task_id"] else [], [r["turn_id"]] if r["turn_id"] else [])
            asyncio.ensure_future(fut)
        except RuntimeError:   # no loop (a test calling from outside one)
            if hasattr(fut, "close"):
                fut.close()

    async def _retire(self, tab: str) -> None:
        if self.host.retire_tab is not None:
            try:
                await self.host.retire_tab(tab)
            except Exception:  # noqa: BLE001 - the sweeper tries again
                pass

    # ------------------------------------------------------------------ clearing the chat history (chat_clear.py)
    async def stop_all(self) -> int:
        """Every job that is running, waiting for the user or interrupted is stopped the way `stop` does it (its turn cancelled, its
        tab retired), and nothing queued goes on by itself. Returns how many were stopped. The rows are deleted afterwards."""
        self._queue.clear()
        ids = [r["id"] for r in self.store._all("SELECT id FROM workers WHERE status IN ('running', 'needs_input', 'blocked', 'interrupted')")]
        ids += [wid for wid, live in self._live.items() if live.task is not None and not live.task.done() and wid not in ids]
        await asyncio.gather(*(self.stop(wid) for wid in ids), return_exceptions=True)
        return len(ids)

    def forget(self) -> None:
        """After the tables were emptied: drop what memory still holds of jobs that are over (and anything queued since)."""
        self._queue.clear()
        self._live = {wid: live for wid, live in self._live.items() if live.task is not None and not live.task.done()}

    # ------------------------------------------------------------------ start-up and shut-down
    def startup(self) -> list[str]:
        """After a restart: a job that was running (or that a graceful stop marked) is cut (job_cuts.py): its open call gets a cut result,
        and it goes on by itself at once or after the wait (limit_tick); the cuts in a row past the limit stop it (blocked, the owner
        decides). Once per job and process: a second call (after a Keyguard unlock) only picks up what is due."""
        now = self._clock()
        for r in self.store._all("SELECT * FROM workers WHERE status = 'running' OR (status = 'interrupted' AND auto_resume = 1) "
                                 "ORDER BY created_at"):
            if r["id"] not in self._cut_seen:
                self._cut(r, now)
        resumed = []
        if self.resume_on_start:
            for r in self.store._all("SELECT * FROM workers WHERE status = 'interrupted' AND auto_resume = 1 ORDER BY created_at"):
                wait = json.loads(r["wait_on"] or "{}")
                if wait.get("kind") == "cut":
                    if float(wait.get("until") or 0) > now or not self._pickup(r["id"]):
                        continue   # the wait is not over (limit_tick picks it up), or no slot: _drain does
                    resumed.append(r["id"])
                    continue
                ok, _ = self.resume(r["id"], why="The server restarted while you were working.", auto=True)
                if ok:
                    resumed.append(r["id"])
        return resumed

    def _cut(self, r: dict[str, Any], now: float) -> None:
        wid = r["id"]
        calls = open_calls(self.transcript(wid))
        recs = self._safe(lambda: self.host.cut_records(r["task_id"]), [])
        pay = self._safe(lambda: self.host.cut_pay(r["tab"]), None)
        tab = self._safe(lambda: self.host.tab_state(r["tab"]), "none")
        if tab == "none" and r["last_url"] and not self._safe(lambda: self.host.tab_open(r["tab"]), False):
            tab = "new"   # no browser record of the tab, and its page is not open: a new tab, the last url is known
        by_tool = {tool: rid for rid, tool, _ in recs}
        seq = self._next_seq(wid)
        for call in calls:   # the open call (at most one after a hard kill) gets its cut result, so the transcript stays valid
            self._append(wid, seq, {"role": "tool", "tool_call_id": call.call_id, "content": cut_result(call, by_tool, pay)})
            seq += 1
        cut = Cut(now, "restart", calls[-1] if calls else None, tuple(rid for rid, _, _ in recs), pay, tab, r["last_url"] or "",
                  tuple(what for _, _, what in recs))
        cuts = int(r["cuts"] or 0) + 1
        self._cut_seen.add(wid)
        self._update(wid, status="interrupted", error=t(RESTART_REASON), auto_resume=1, cut=cut.to_json(), cuts=cuts)
        wait = next_pickup(cuts)
        if wait is None:
            self._cut_stop(wid, cuts)
            return
        self._update(wid, wait_on=json.dumps({"kind": "cut", "until": now + wait}))

    def _cut_stop(self, wid: str, cuts: int) -> None:
        """The job was cut too many times at the same step: it stops as blocked and the report goes to the conversation."""
        text = t(CUT_LOOP_LINE, n=cuts)
        try:
            asyncio.get_running_loop()
        except RuntimeError:   # no loop (a start outside the server): the row says it, the report goes on at the next settle
            report = {"status": "blocked", "summary": text, "details": "", "keep_tab": False, "at": self._clock()}
            self._update(wid, status="blocked", report=json.dumps(report, ensure_ascii=False), wait_on=json.dumps({"kind": "user"}),
                         wake_at=0.0)
            return
        asyncio.ensure_future(self._settle(wid, ("blocked", text, "", False), None))

    def _pickup(self, wid: str) -> bool:
        """Go on with a cut job: the pick-up note says what happened and what to check first (job_cuts.pickup_note)."""
        r = self._row(wid)
        if r is None:
            return False
        cut = Cut.of(r["cut"] or "")
        why = pickup_note(cut) if cut is not None else "The server restarted while you were working."
        ok, how = self.resume(wid, why=why, auto=True, cut=cut)
        if ok and how == "resumed":
            self._update(wid, pickups=int(r["pickups"] or 0) + 1, picked_up_at=self._clock())
            self._note_line(wid, t(BROWSER_PICKED_UP_LINE if cut is not None and cut.why == "browser" else PICKED_UP_LINE))
        return ok

    def _safe(self, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001 - a host hook that fails never stops the start
            log.exception("worker host hook failed at a start")
            return default

    def browser_lost(self, key: str) -> None:
        """A browser that restarted or crashed under a running tab: a job's card says it went on in a new tab (job_cuts.py)."""
        if key.startswith("task:w"):
            self._note_line(key[5:], t(BROWSER_PICKED_UP_LINE))

    def step_busy(self) -> bool:
        """A step that sends, changes or pays is in flight, or a payment job runs: the nightly update waits for it (D3)."""
        if self._safe(lambda: self.host.step_busy(), False):
            return True
        return bool(self.store._all("SELECT id FROM workers WHERE kind = ? AND status = 'running' LIMIT 1", (tool_roles.CHECKOUT_KIND,)))

    async def drain_steps(self, seconds: float = DRAIN_SECONDS) -> None:
        """A stop waits up to `seconds` for a step that sends, changes or pays to finish (its result is then in the transcript)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and self._safe(lambda: self.host.step_busy(), False):
            await asyncio.sleep(0.2)

    async def shutdown(self) -> None:
        self._closing = True
        await self.drain_steps()
        tasks = []
        for wid, live in self._live.items():
            if live.task is not None and not live.task.done():
                self._update(wid, status="interrupted", error=t(RESTART_REASON), auto_resume=1)
                if live.turn is not None and not live.turn.done:
                    self.host.cancel_turn(live.turn)
                live.task.cancel()
                tasks.append(live.task)
        if tasks:
            await asyncio.wait(tasks, timeout=5)

    # ------------------------------------------------------------------ tools
    def progress_tool(self) -> Tool:
        async def job_progress(args: dict[str, Any]) -> str:
            wid = current_worker.get()
            live = self._live.get(wid) if wid else None
            if live is None:
                return "Error: job_progress is only for background jobs."
            try:
                done, total = int(args.get("done")), int(args.get("total") or 0)
            except (TypeError, ValueError):
                return "Error: done and total must be whole numbers."
            if done < 0 or total < 0:
                return "Error: done and total cannot be negative."
            label = " ".join(str(args.get("label") or "").split())
            live.progress = (f"{done:,} / {total:,}" if total else f"{done:,}") + (f" · {label}" if label else "")
            live.progress = live.progress[:80]
            self._update(wid, status_text=live.progress)
            self._emit(wid)
            return "Progress shown. Go on with the work."

        return Tool("job_progress", "Show the user how far this job is (item-by-item work over many items): done of total and what is being "
                    "done, e.g. done=300, total=1545, label='deleting comments'. Call it about every 10-25 items, not after each one; "
                    "it costs no step of the work.",
                    {"type": "object", "properties": {"done": {"type": "integer"}, "total": {"type": "integer", "description": "0 if unknown"},
                                                      "label": {"type": "string", "description": "a few words, in the user's language"}},
                     "required": ["done", "total"]}, job_progress)

    def report_tool(self) -> Tool:
        async def report(args: dict[str, Any]) -> str:
            wid = current_worker.get()
            live = self._live.get(wid) if wid else None
            if live is None:
                return "Error: report is only for background jobs."
            status = str(args.get("status") or "").strip()
            summary = str(args.get("summary") or "").strip()
            if status not in REPORT_STATUSES:
                return f"Error: status must be one of {', '.join(REPORT_STATUSES)}."
            if not summary:
                return "Error: summary is required: say in a few sentences what came of the job, in the user's language."
            raw = args.get("products")
            if raw not in (None, []):
                kept, left = verify(raw if isinstance(raw, list) else [], pages_read(self.transcript(wid)), wid)
                if left and live.product_refused == 0:
                    live.product_refused = 1
                    return ("Error: not recorded. Report only products whose own page you opened in this job, with the price that page shows "
                            "and only when it can be bought now. Left out:\n- " + "\n- ".join(left) +
                            "\nOpen each one's page and report it again as it shows there, or leave it out, then call report again.")
                live.report_products = kept          # second try: what passed is kept, the rest stays out (no loop)
            retry_after_min = None
            if "retry_after_min" in args and args["retry_after_min"] is not None:
                try:
                    retry_after_min = max(5, min(30, int(args["retry_after_min"])))
                except (TypeError, ValueError):
                    pass
            live.report = {"status": status, "summary": scrub(summary), "details": scrub(str(args.get("details") or "")),
                           "keep_tab": bool(args.get("keep_tab")), "deadline": str(args.get("deadline") or "")}
            if args.get("need") in REPORT_NEEDS:
                live.report["need"] = args["need"]
            if args.get("reason") in REPORT_REASONS:
                live.report["reason"] = args["reason"]
            if retry_after_min is not None:
                live.report["retry_after_min"] = retry_after_min
            return "Report received. Stop here."

        return Tool("report", "End this background job and send its outcome to the main assistant, once, as your last step. status: done, "
                    "needs_input (you need the user), blocked (a hard stop), failed.",
                    {"type": "object", "properties": {"status": {"type": "string", "enum": list(REPORT_STATUSES)},
                                                      "summary": {"type": "string", "description": "the outcome in the user's language, facts exact"},
                                                      "details": {"type": "string", "description": "longer findings, optional"},
                                                      "keep_tab": {"type": "boolean", "description": "leave the tab open for the user"},
                                                      "deadline": {"type": "string", "description": "needs_input: local date-time when waiting is "
                                                                   "too late, if the page shows a cutoff"},
                                                      "need": {"type": "string", "enum": list(REPORT_NEEDS),
                                                               "description": "needs_input: what you need from the user. site_data_reset = the site "
                                                                              "keeps refusing you and clearing its cookies and site data, then retrying, may help"},
                                                      "products": {"type": "array", "description": "products found, each from its own page you opened in this job: name, url (that page), price (as the page shows it), sale_price (only a real reduced price the page shows), image_url (a direct https picture address from the page), availability ('in_stock' only when the product page indicates it can be added to the cart or purchased now)", "items": {"type": "object", "properties": {"name": {"type": "string"}, "url": {"type": "string"}, "price": {"type": "string"}, "sale_price": {"type": "string"}, "image_url": {"type": "string"}, "availability": {"type": "string", "enum": ["in_stock"]}}, "required": ["name", "url", "price", "availability"]}},
                                                      "reason": {"type": "string", "enum": list(REPORT_REASONS), "description": "blocked or needs_input: what stopped you. site_refused = a block or access-denied page, or the site says it does not allow assistants; sign_in = the site needs the user's sign-in and no saved login works; human_check = a check you could not pass; not_available = the site does not have or sell it; site_error = the site is down or fails"},
                                                      "retry_after_min": {"type": "integer", "description": "blocked: minutes to wait before retrying once (5-30), only when a service asked you to slow down; never for a site that refused you"}},
                     "required": ["status", "summary"]}, report, ends_turn=True)

    def main_tools(self) -> list[Tool]:
        async def delegate(args: dict[str, Any]) -> str:
            return await self.delegate(args)

        def line(w: dict[str, Any]) -> str:
            ago = max(0, int((self._clock() - (w["updated_at"] or 0)) // 60))
            wait = f"; waiting for {w['waiting_for']}" + (f" {w['login']!r}" if w.get("login") else "") if w.get("waiting_for") else ""
            return f"- {w['id']} · {w['status']} · {w['kind']} · {w['title']} · {w['steps']}/{w['max_steps']} steps · updated {ago} min ago{wait}"

        async def workers_list(args: dict[str, Any]) -> str:
            items = self.list(20)
            if not items:
                return "No background jobs."
            return "Background jobs (newest first):\n" + "\n".join(line(w) for w in items)

        async def worker_status(args: dict[str, Any]) -> str:
            w = self.get(str(args.get("id") or args.get("worker_id") or ""))
            if w is None:
                return "Error: no such job. workers_list shows them."
            out = [line(w)]
            if w.get("question_id"):
                out.append(f"Open question id: {w['question_id']} (answer it with worker_message and answer_to)")
            if w["report"]:
                out.append(f"Report: {w['report']['status']}: {w['report']['summary']}")
                if w["report"].get("details"):
                    out.append(f"Details: {w['report']['details'][:3000]}")
            if w["reason"] and not w["report"]:
                out.append(f"Reason: {w['reason']}")
            if w["lines"]:
                out.append("Last steps: " + "; ".join(x["text"] for x in w["lines"][-10:]))
            return "\n".join(out)

        async def worker_message(args: dict[str, Any]) -> str:
            text = str(args.get("text") or "").strip()
            if not text:
                return "Error: text is required."
            wid = str(args.get("id") or args.get("worker_id") or "")
            answer_to = str(args.get("answer_to") or "").strip()
            turn = current_turn.get() or {}   # the owner's own message of this turn (never the model's words, never a page's)
            own = str(turn.get("source") or "") in OWNER_SOURCES and not turn.get("unattended")
            owner_text = str(turn.get("request") or "") if own else ""
            _ok, how = self.message(wid, text, answer_to, owner_text=owner_text)
            if how in ("stale", "wrong"):
                r = self._row(wid)
                open_id = r["question_id"] if r is not None and r["status"] in ("needs_input", "blocked") else ""
                return ("Error: " + (f"that job has no open question with id {answer_to} (it was answered, or the job went on). "
                        "Send the text without answer_to if it is only an update, or check worker_status." if how == "stale" or not open_id else
                        f"answer_to {answer_to} is not the job's open question; the open question's id is {open_id}. Use that id, "
                        "or leave answer_to out for an update."))
            return {"delivered": "Sent: the job reads it before its next step. Its report comes back by itself: tell the owner in one short line "
                                 "that you passed it on.",
                    "resumed": "The job was not running; it goes on now with your message. Its report comes back to you by itself: tell the owner "
                               "in one short line that you passed it on, and promise nothing about the result.",
                    "queued": "The job goes on with your message as soon as a slot is free; tell the owner that in one short line.",
                    "missing": "Error: no such job.",
                    "limit": "Error: too many jobs are running to start this one again now.", "running": "Error: the job is busy; try again."}.get(how, how)

        async def worker_stop(args: dict[str, Any]) -> str:
            return "Stopped." if await self.stop(str(args.get("id") or args.get("worker_id") or "")) else "Error: no such job."

        async def resume(args: dict[str, Any]) -> str:
            ok, how = self.resume(str(args.get("worker_id") or args.get("id") or ""), str(args.get("note") or ""), why="The main assistant resumed you.")
            return {"resumed": "The job goes on from where it stopped; its report comes back to you by itself.", "missing": "Error: no such job.",
                    "running": "Error: that job is running already.", "limit": "Error: too many jobs are running; wait for one to report."}[how]

        wid_prop = {"id": {"type": "string", "description": "the job id"}}
        return [
            Tool("delegate", "Hand work to a background job and return at once; tell the user in one line that it started. Kind browser: "
                 "anything done on a website (you do not click or type on websites yourself), research: reading many sources. The job cannot "
                 "read this chat or your memory: put everything it needs in task, including the "
                 "user's own choices and saved preferences that apply (sizes, colors, options, address; from your memory files). Its report "
                 "comes back by itself. Only kind checkout can pay: for a purchase, booking or payment use it with url = the shop's home page "
                 "and a self-contained task (items, variants, quantities, total, delivery, limits). Only kind phone drives "
                 "the Android device (you have no phone tools): task = app, goal, choices made, limits, where to stop; one at a time.",
                 {"type": "object", "properties": {"task": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
                                                   "url": {"type": "string", "description": "where to start: the site's home page, or the exact address the user gave (the job goes on by clicking)"},
                                                   "files": {"type": "array", "items": {"type": "string"}, "description": "your files to use"},
                                                   "profile": {"type": "string", "description": "a browser_profiles id"},
                                                   "deadline_min": {"type": "integer"}}, "required": ["task"]}, delegate),
            Tool("workers_list", "List your background jobs with their status, when the user asks about them.",
                 {"type": "object", "properties": {}}, workers_list, read_only=True),
            Tool("worker_status", "One background job: its status, report and last steps.", {"type": "object", "properties": wid_prop, "required": ["id"]},
                 worker_status, read_only=True),
            Tool("worker_message", "Tell a background job something: an update or correction (send everything about the same work to the "
                 "same job), or the answer to its question: pass the question's id as answer_to. A stopped job goes on with it.",
                 {"type": "object", "properties": {**wid_prop, "text": {"type": "string"},
                                                   "answer_to": {"type": "string", "description": "the id (q_...) of the job's open question this text answers; "
                                                                 "leave out for an update"}}, "required": ["id", "text"]}, worker_message),
            Tool("worker_stop", "Stop a background job.",
                 {"type": "object", "properties": wid_prop, "required": ["id"]}, worker_stop),
            Tool("resume", "Let a stopped background job go on from where it stopped (same tab and history).",
                 {"type": "object", "properties": {"worker_id": {"type": "string"},
                                                   "note": {"type": "string", "description": "what changed, or the user's answer"}},
                  "required": ["worker_id"]}, resume),
        ]


def worker_routes(app: FastAPI, workers: Workers, auth: Callable[..., Any]) -> None:
    """GET /api/workers, GET /api/workers/{id}, POST /api/workers/{id}/resume|stop|message (contracts next to serialize())."""

    @app.get("/api/workers", dependencies=[Depends(auth)])
    async def list_workers(limit: int = 50, status: str | None = None, parent_task_id: int | None = None) -> dict[str, Any]:
        return {"items": workers.list(limit, status, parent_task_id), "running": workers.running_count(), "limit": workers.limits.concurrent}

    @app.get("/api/workers/{wid}", dependencies=[Depends(auth)])
    async def get_worker(wid: str) -> dict[str, Any]:
        w = workers.get(wid)
        if w is None:
            raise HTTPException(404, "no such job")
        return w

    @app.post("/api/workers/{wid}/resume", dependencies=[Depends(auth)])
    async def resume_worker(wid: str, body: ResumeIn | None = None) -> dict[str, Any]:
        ok, how = workers.resume(wid, body.note if body else "", why="The user resumed you from the app.")
        if not ok:
            raise HTTPException({"missing": 404, "running": 409, "limit": 429}.get(how, 409),
                                {"missing": "no such job", "running": "this job is running already",
                                 "limit": "too many jobs are running; wait for one to finish"}.get(how, how))
        return {"ok": True, "worker": workers.get(wid)}

    @app.post("/api/workers/{wid}/stop", dependencies=[Depends(auth)])
    async def stop_worker(wid: str) -> dict[str, Any]:
        if not await workers.stop(wid):
            raise HTTPException(404, "no such job")
        return {"ok": True, "worker": workers.get(wid)}

    @app.post("/api/workers/{wid}/message", dependencies=[Depends(auth)])
    async def message_worker(wid: str, body: WorkerMessageIn, request: Request) -> dict[str, Any]:
        check = getattr(request.app.state, "secret_check", None)   # a pasted key or password stops here (409), as in chat
        text = await check(body.text, body.secrets) if check is not None else body.text
        ok, how = workers.message(wid, text, owner_text=text)   # the owner typed it: their own words
        if not ok:
            raise HTTPException({"missing": 404, "running": 409, "limit": 429}.get(how, 409), how)
        return {"ok": True, "delivered": how, "worker": workers.get(wid)}
