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
still only through confirm_purchase), browser cards and the live view work, and each run of a job is a task in the task log, linked to the
task that started the job (tasks.parent_id / worker_id).

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
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import tool_labels
from .activity import host_of, title_from
from .agent import Tool, emit_ui, inbox
from .agent.found_secrets import scrub
from .agent.loop import digest_tool_results
from .compaction import request_tokens
from .i18n import N_, t

log = logging.getLogger("uvicorn.error")

KINDS = ("browser", "research", "general")
REPORT_STATUSES = ("done", "needs_input", "blocked", "failed")
STATUSES = ("running", "needs_input", "blocked", "done", "failed", "stopped", "interrupted")
PAUSED = ("needs_input", "blocked", "interrupted")
MAIN_ONLY = ("delegate", "workers_list", "worker_status", "worker_message", "worker_stop", "resume",
             "await_reply", "followup_snooze", "followup_cancel",   # these three: follow-ups (followups.py) are the main chat's
             "set_tone",   # how Kelo talks to the user: changed only when the user asks in the chat
             "browser_forget_site")
HANDOFF_MARK = "[Handoff from a background job, not from the user]"   # leads the hidden message; GET /api/messages leaves these out
STATUS_HEAD = {"done": N_("Finished"), "needs_input": N_("Needs your help"), "blocked": N_("Got stuck"), "failed": N_("It did not work out")}
RESTART_REASON = N_("Stopped because the server restarted")   # translate with t() where it is saved or shown
LIMIT_RETRY = 1800.0   # a job paused by the usage limit with no reset time known looks again after this long
KEEP_WORKERS = 200          # finished jobs kept (with their transcripts); older ones are forgotten
LINES_KEPT = 40             # activity lines kept per job
PAUSED_TAB_SECONDS = 86400.0   # a paused job keeps its tab this long (the user may take it over and hand it back)
KEPT_TAB_SECONDS = 3600.0      # a finished job that asked to keep its tab keeps it this long
MAX_DEADLINE_MIN = 240
CUT_NOTE = "[This step was cut off (a restart or a stop) before it gave a result. Check the page before you go on.]"
# A job that is getting somewhere never stops at its budget: it goes on in chunks, each with a fresh step / time / token budget, as long
# as the chunk before made progress (new distinct tool calls that succeeded: a new item handled, a new result read). A chunk without any
# is a loop (the same steps again) and stops the job; MAX_CHUNKS bounds it all.
MAX_CHUNKS = 25
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
    segments INTEGER NOT NULL DEFAULT 0, auto_resume INTEGER NOT NULL DEFAULT 0, wake_at REAL NOT NULL DEFAULT 0, retries INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS worker_messages (
    worker_id TEXT NOT NULL, seq INTEGER NOT NULL, message TEXT NOT NULL, PRIMARY KEY (worker_id, seq));
"""


BUSY_GIVE_UP = 48 * 3600.0   # a job waiting for a busy / unreachable model gives up after this long in all


def _outage_text(text: str) -> bool:
    """The job's turn failed because the model was busy or unreachable (resume.is_outage on its error), not because of the job."""
    if not text:
        return False
    from .providers.types import ProviderError
    from .resume import is_outage
    return is_outage(ProviderError(text))


@dataclass(slots=True)
class Limits:
    concurrent: int = 3        # jobs running at once per copy
    steps: int = 200           # steps of a browser job (research: half, general: under a third)
    minutes: int = 60          # minutes of a browser job (the others: half)
    tokens: int = 4_000_000    # input + output tokens per job, counted from its last start

    def budget(self, kind: str, deadline_min: Any = None) -> tuple[int, int]:
        share = {"browser": 1.0, "research": 0.5}.get(kind, 0.3)
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
    tab_open: Callable[[str], bool] = lambda _k: False
    has_picture: Callable[[str], bool] = lambda _k: False
    controlled: Callable[[str], bool] = lambda _k: False
    retire_tab: Callable[[str], Awaitable[Any]] | None = None
    login_saved: Callable[[str], bool] = lambda _n: False
    window: Callable[[], int] = lambda: 150_000
    on_delegate: Callable[[str, str], Any] = lambda _turn_id, _wid: None   # (parent turn id, job id): the job takes on the turn's permissions
    on_end: Callable[[str, str], Any] = lambda _wid, _status: None   # (job id, status) a job ended without a report (stopped)
    # the model's usage limit (model_limits.py): the paused line, the hold's end now (None = not held), and the one notice
    # (row, until) -> True once it was given (False: not now, e.g. quiet time)
    limit_text: Callable[[float | None], str] = lambda _until: ""
    limit_until: Callable[[], float | None] = lambda: None
    limit_notice: Callable[[dict[str, Any], float], bool] = lambda _row, _until: True
    # how far the job's allowed items are: (done, total) from its task permissions, or None
    progress: Callable[[str], tuple[int, int] | None] = lambda _wid: None


@dataclass
class _Live:
    task: asyncio.Task | None = None
    turn: Any = None
    inbox: list[dict[str, Any]] = field(default_factory=list)
    stop: bool = False
    timed_out: bool = False
    report: dict[str, Any] | None = None
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
        self._live: dict[str, _Live] = {}
        self._queue: list[tuple[str, str]] = []     # automatic resumes waiting for a free slot: (id, why)
        self._closing = False
        with store._lock:
            store._db.executescript(SCHEMA)
            have = {r[1] for r in store._db.execute("PRAGMA table_info(workers)")}
            if "wake_at" not in have:
                store._db.execute("ALTER TABLE workers ADD COLUMN wake_at REAL NOT NULL DEFAULT 0")
            if "retries" not in have:
                store._db.execute("ALTER TABLE workers ADD COLUMN retries INTEGER NOT NULL DEFAULT 0")
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

    # ------------------------------------------------------------------ what the app and the agent see
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
        status = "waiting" if r["status"] == "blocked" and wake_at is not None else r["status"]
        out = {"id": r["id"], "kind": r["kind"], "title": r["title"], "status": status, "parent_task_id": r["parent_task_id"],
               "task_id": r["task_id"], "turn_id": r["turn_id"] or None, "tab": r["tab"], "started_at": r["created_at"],
               "updated_at": r["updated_at"], "finished_at": r["finished_at"], "steps": r["steps"], "max_steps": r["max_steps"],
               "deadline_at": r["deadline_at"] if r["status"] == "running" else None, "wake_at": wake_at,
               "waiting_for": wait.get("kind"),
               "login": wait.get("login"), "reason": reason, "status_text": r["status_text"] or None, "last_url": r["last_url"] or None,
               "thumbnail": f"/api/browser/sessions/{r['tab']}/screenshot" if self.host.has_picture(r["tab"]) else None,
               "progress": ({"done": p[0], "total": p[1]} if (p := self.progress_of(r["id"])) else None)}
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
    def _brief(self, wid: str, task: str, kind: str, url: str, files: list[str], steps: int, minutes: int) -> str:
        lines = [f"[Not from the user: you are a background job ({wid}) the main assistant started for the user. Nobody watches this run "
                 "and nobody reads text you write: work with your tools until the job is finished, then call report. The job:]", task]
        if url:
            lines.append(f"Start at: {url}")
        if files:
            lines.append("Files to use (in your files; read them with read_file or extract_document): " + ", ".join(files))
        if kind == "browser":
            lines.append("Use your browser tools; this job has a browser tab of its own.")
        elif kind == "research":
            lines.append("Look things up with web_search and web_fetch (the browser only for pages that need it) and read the sources; "
                         "put the addresses you used in details.")
        lines.append("If the job does the same kind of step on many items (all / every / these N), find the items first, then call "
                     "request_task_permission once with the real count and the list, then do them one after another. Work in order and "
                     "keep track of what is done: when a stretch of work uses its budget and you were getting somewhere, a fresh budget "
                     "follows by itself. Keep going until every item is done or something really stops you (a limit, a refusal, a check you "
                     "cannot solve, an approval declined); do not stop to ask whether to continue. Show how far you are with "
                     "job_progress(done, total, label) every 10-25 items.")
        lines.append(
            f"Budget: about {steps} steps and {minutes} minutes. Finish by calling report(status, summary, details?, keep_tab?) once, as your last step:\n"
            "- done: the job is complete. summary: the result in the user's language, with facts (prices, times, numbers) copied exactly.\n"
            "- needs_input: you need the user: a login, a choice, a one-time code, or a step they must do on the page. Before you pause "
            "for anything but a login or a code, try 2-3 other ways yourself (the per-item action again, the site's own request, another "
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

    async def delegate(self, args: dict[str, Any]) -> str:
        if current_worker.get():
            return ("Error: you are a background job yourself and cannot start another. Do the work with your own tools, or call "
                    "report(status='needs_input') if you need the user.")
        task = str(args.get("task") or "").strip()
        if not task:
            return "Error: task is required: describe the whole job so it can be done without asking (what, where, what result to bring back)."
        kind = str(args.get("kind") or "general").strip().lower()
        if kind not in KINDS:
            return f"Error: kind must be one of {', '.join(KINDS)}."
        if kind == "browser" and not self.host.browser:
            return "Error: this copy has no browser, so a browser job cannot run. Use kind research or general, or do it yourself."
        if (n := self.running_count()) >= self.limits.concurrent:
            return (f"Error: {n} background jobs are running already (the most at once). Wait for one to report, or stop one with "
                    "worker_stop, then try again.")
        url = str(args.get("url") or "").strip()
        if url and not re.match(r"^https?://[^\s/]+", url):
            if re.match(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", url, re.I):
                url = "https://" + url
            else:
                return "Error: url must be a web address (https://...)."
        files = [str(f).strip()[:200] for f in (args.get("files") or []) if str(f).strip()][:10] if isinstance(args.get("files"), list) else []
        steps, minutes = self.limits.budget(kind, args.get("deadline_min"))
        parent = current_turn.get() or {}
        wid = "w" + secrets.token_hex(3)
        while self._row(wid) is not None:
            wid = "w" + secrets.token_hex(3)
        now = self._clock()
        title = title_from(task)
        self.store._run(
            "INSERT INTO workers(id, kind, title, task, url, files, status, parent_task_id, parent_source, tab, created_at, updated_at, "
            "max_steps, deadline_at, minutes, token_cap) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (wid, kind, title, task[:8000], url, json.dumps(files, ensure_ascii=False), "running", parent.get("task_id"),
             str(parent.get("source") or "chat"), f"task:{wid}", now, now, steps, now + minutes * 60, minutes, self.limits.tokens))
        self._append(wid, 0, {"role": "user", "content": self._brief(wid, task, kind, url, files, steps, minutes)})
        try:
            self.host.on_delegate(str(parent.get("turn_id") or ""), wid)
        except Exception:  # noqa: BLE001 - without it the job's steps ask as usual
            log.exception("worker %s: could not pass on the task's permissions", wid)
        self._prune()
        self._launch(wid, "")
        await emit_ui({"type": "worker", "id": wid, "status": "running", "title": title})
        return (f"Handed off; you will hear back when it is done. (job {wid}: {kind}, up to {steps} steps / {minutes} min, in a browser tab of its own.) "
                "It runs on its own; its report comes back to you by itself as a handoff message. Do not check on it with workers_list "
                "or worker_status now: tell the user in one short line that it is underway, and go on.")

    def _launch(self, wid: str, note: str) -> None:
        live = self._live.setdefault(wid, _Live())
        live.stop = live.timed_out = False
        live.report, live.turn = None, None
        live.chunk_progress = live.chunks = 0
        try:
            loop = asyncio.get_running_loop()
            live.task = loop.create_task(self._run(wid, note), context=contextvars.Context())
        except RuntimeError:
            live.task = None

    def resume(self, wid: str, note: str = "", *, why: str = "", auto: bool = False) -> tuple[bool, str]:
        """Go on with a job that is not running: same transcript, same tab (a new one when it is gone), a fresh budget from here.
        (ok, "resumed" | "queued" | "running" | "limit" | "missing")."""
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
        if r["last_url"] and not self.host.tab_open(r["tab"]):
            parts.append(f"Your browser tab was closed meanwhile; a new blank tab will open. Continue from the history above: open "
                         f"{r['last_url']} again if you need it.")
        parts.append("Go on from where you stopped and finish with report.")
        self._update(wid, status="running", wait_on="", report="", error="", finished_at=None, auto_resume=0, keep_tab=0,
                     wake_at=0.0, max_steps=r["steps"] + steps, deadline_at=now + minutes * 60, minutes=minutes,
                     token_cap=r["input_tokens"] + r["output_tokens"] + self.limits.tokens)
        self._queue = [q for q in self._queue if q[0] != wid]
        self._launch(wid, "[Not from the user: " + " ".join(parts) + "]")
        self._emit(wid)
        return True, "resumed"

    async def stop(self, wid: str) -> bool:
        r = self._row(wid)
        if r is None:
            return False
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
        self._update(wid, status="stopped", finished_at=self._clock(), wait_on="", keep_tab=0, wake_at=0.0)
        self._emit(wid)
        try:
            self.host.on_end(wid, "stopped")
        except Exception:  # noqa: BLE001
            log.exception("worker %s: on_end failed", wid)
        await self._retire(r["tab"])
        return True

    def message(self, wid: str, text: str) -> tuple[bool, str]:
        """A word for a job: a running one reads it before its next step; one that is not running is resumed with it."""
        r = self._row(wid)
        if r is None:
            return False, "missing"
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
        if device is not None or not key.startswith("task:w"):
            return
        r = self._row(key[5:])
        if r is not None and r["status"] in ("needs_input", "blocked"):
            self.resume(r["id"], why="The user handed the browser back to you (they may have done a step on the page themselves). Read the "
                                     "page with browser_read and go on.", auto=True)

    def _drain(self) -> None:
        while self._queue and self.running_count() < self.limits.concurrent and not self._closing:
            wid, why = self._queue.pop(0)
            r = self._row(wid)
            if r is not None and r["status"] in PAUSED:
                self.resume(wid, why=why, auto=True)

    # ------------------------------------------------------------------ running
    def _worker_tools(self) -> list[Tool]:
        return [t for t in self.host.tools() if t.name not in MAIN_ONLY and t.name != "report"] + [self.progress_tool(), self.report_tool()]

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
                    turn = self.host.start_turn(r, self._fit(self.transcript(wid)), left, self._worker_tools(), rec)
                except Exception as e:  # noqa: BLE001 - no model, ...
                    detail = getattr(e, "detail", None) or f"{type(e).__name__}: {e}"
                    outcome = ("failed", t("Could not start: {error}", error=str(detail)[:200]), "", False)
                    break
                finally:
                    inbox.reset(token)
                live.turn = turn
                self._update(wid, turn_id=turn.id, task_id=turn.task_id, segments=r["segments"] + 1)
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
                if turn.final_status == "failed" and _outage_text(turn.final_error or rec.error):
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
        self._update(wid, status=status, report=json.dumps(report, ensure_ascii=False), wait_on=json.dumps(wait), keep_tab=int(keep),
                     wake_at=wake_at, finished_at=now if status in ("done", "failed") else None)
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

    def _handoff_text(self, r: dict[str, Any], rep: dict[str, Any], wait: dict[str, Any]) -> str:
        lines = [HANDOFF_MARK, f"The background job {r['id']} (\"{r['title']}\", {r['kind']}) that you started with delegate reported: "
                 f"{rep['status']}.", f"Summary: {rep['summary'] or '-'}"]
        if rep["details"]:
            lines.append(f"Details: {rep['details']}")
        if progress := self._progress_line(r):
            lines.append(progress)
        st = rep["status"]
        if st == "done":
            lines.append("Pass the result on to the user.")
        elif st == "needs_input":
            if "cookie" in rep.get("summary", "").lower() or "쿠키" in rep.get("summary", ""):
                lines.append(f"Ask the user with quick_replies (two options: clear and retry / stop), using the user's language. If they agree to clear, call browser_forget_site first, then call resume(worker_id='{r['id']}', note='Cookies cleared. Sign in again and continue from the home page.').")
            else:
                how = {"login": f"It goes on by itself once the user saves the login {wait.get('login')!r} in the app.",
                       "control": "It goes on by itself once the user hands its browser tab back."}.get(
                    wait.get("kind", ""), f"When the user answers, call resume(worker_id='{r['id']}', note=<their answer>).")
                lines.append(f"Tell the user exactly what is needed. {how}")
        elif st == "blocked":
            if wait.get("wake_at"):
                time_str = self._format_time(float(wait["wake_at"]))
                lines.append(f"The job will retry by itself at {time_str}. Tell the user once, in one line (e.g. that the site is "
                             f"blocking for now and it will try again at {time_str}; they can keep talking).")
            else:
                lines.append("Say what stopped it: tell the user the site does not let Kelo's browser in right now and offer the live view "
                             "(take over) or another shop; it goes on by itself when they hand the browser tab back.")
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
        """After a restart: a job that was running is interrupted (said plainly), and goes on by itself when it may."""
        now = self._clock()
        self.store._run("UPDATE workers SET status = 'interrupted', error = ?, auto_resume = 1, updated_at = ? WHERE status = 'running'",
                        (t(RESTART_REASON), now))
        resumed = []
        if self.resume_on_start:
            for r in self.store._all("SELECT id FROM workers WHERE status = 'interrupted' AND auto_resume = 1 ORDER BY created_at"):
                ok, _ = self.resume(r["id"], why="The server restarted while you were working.", auto=True)
                if ok:
                    resumed.append(r["id"])
        return resumed

    async def shutdown(self) -> None:
        self._closing = True
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
            retry_after_min = None
            if "retry_after_min" in args and args["retry_after_min"] is not None:
                try:
                    retry_after_min = max(5, min(30, int(args["retry_after_min"])))
                except (TypeError, ValueError):
                    pass
            live.report = {"status": status, "summary": scrub(summary), "details": scrub(str(args.get("details") or "")),
                           "keep_tab": bool(args.get("keep_tab")), "deadline": str(args.get("deadline") or "")}
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
                                                      "retry_after_min": {"type": "integer", "description": "blocked: minutes to wait before retrying once (5-30, use 10 for access-denied sites)"}},
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
            _ok, how = self.message(str(args.get("id") or args.get("worker_id") or ""), text)
            return {"delivered": "Sent: the job reads it before its next step.", "resumed": "The job was not running; it goes on now with your message.",
                    "queued": "The job goes on with your message as soon as a slot is free.", "missing": "Error: no such job.",
                    "limit": "Error: too many jobs are running to start this one again now.", "running": "Error: the job is busy; try again."}.get(how, how)

        async def worker_stop(args: dict[str, Any]) -> str:
            return "Stopped." if await self.stop(str(args.get("id") or args.get("worker_id") or "")) else "Error: no such job."

        async def resume(args: dict[str, Any]) -> str:
            ok, how = self.resume(str(args.get("worker_id") or args.get("id") or ""), str(args.get("note") or ""), why="The main assistant resumed you.")
            return {"resumed": "The job goes on from where it stopped; its report comes back to you by itself.", "missing": "Error: no such job.",
                    "running": "Error: that job is running already.", "limit": "Error: too many jobs are running; wait for one to report."}[how]

        wid_prop = {"id": {"type": "string", "description": "the job id"}}
        return [
            Tool("delegate", "Hand long work (many browser steps, research across many sources) to a background job and return at once; "
                 "tell the user in one line that it started. It cannot ask the user anything: put everything it needs in task, including the "
                 "user's own choices and saved preferences that apply (sizes, colors, options, address; from your memory files). Its report "
                 "comes back by itself.",
                 {"type": "object", "properties": {"task": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
                                                   "url": {"type": "string", "description": "where to start"},
                                                   "files": {"type": "array", "items": {"type": "string"}, "description": "your files to use"},
                                                   "deadline_min": {"type": "integer"}}, "required": ["task"]}, delegate),
            Tool("workers_list", "List your background jobs with their status, when the user asks about them.",
                 {"type": "object", "properties": {}}, workers_list, read_only=True),
            Tool("worker_status", "One background job: its status, report and last steps.", {"type": "object", "properties": wid_prop, "required": ["id"]},
                 worker_status, read_only=True),
            Tool("worker_message", "Tell a background job something (a correction, the user's answer); a stopped job goes on with it.",
                 {"type": "object", "properties": {**wid_prop, "text": {"type": "string"}}, "required": ["id", "text"]}, worker_message),
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
        text = check(body.text, body.secrets) if check is not None else body.text
        ok, how = workers.message(wid, text)
        if not ok:
            raise HTTPException({"missing": 404, "running": 409, "limit": 429}.get(how, 409), how)
        return {"ok": True, "delivered": how, "worker": workers.get(wid)}
