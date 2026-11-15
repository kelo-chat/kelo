"""The turn engine's own objects: a reply in progress (`Turn`), the approvals it waits on (`ApprovalBroker`) and the events that
mean the model started answering. The engine that runs turns is still built in server.create_app (start_turn and its helpers);
these moved out first (2026-11-10). server.py re-exports them.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .injection_watch import current as injection_watch_var
from .reply_text import ReplyText
from .schedule_grants import APPLIED_TITLE, PROGRESS_TITLE

if TYPE_CHECKING:   # only for annotations: TurnContext is create_app's own class (the conversation of a turn)
    from .agent import Tool
    TurnContext = Any


@dataclass(slots=True)
class ApprovalBroker:
    pending: dict[str, asyncio.Future[bool]] = field(default_factory=dict)

    def create(self) -> tuple[str, asyncio.Future[bool]]:
        approval_id = uuid.uuid4().hex
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.pending[approval_id] = fut
        return approval_id, fut

    def resolve(self, approval_id: str, approve: bool) -> bool:
        fut = self.pending.get(approval_id)
        if fut is None or fut.done():
            return False
        fut.set_result(approve)
        return True

    def discard(self, approval_id: str) -> None:
        fut = self.pending.pop(approval_id, None)
        if fut and not fut.done():
            fut.set_result(False)


# Reply events that mean the model has started answering: the user's message gets 👀 (reactions.py) on the first of them, not
# while the reply still waits for the model (queued, model down, usage limit).
MODEL_STARTED = frozenset({"thinking", "text", "tool_started", "tool_result", "browser", "replies", "approval_request",
                           "secret_request", "human_check", "followup", "draft"})


class Turn:
    """One reply in progress. Everything it emits is kept in order, so a reader whose connection broke (a phone that went to sleep,
    a tunnel that dropped the stream) can ask again from where it stopped instead of losing the reply."""

    def __init__(self) -> None:
        self.id = secrets.token_hex(16)
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.task: asyncio.Task[None] | None = None
        self.touched = time.monotonic()    # the last time a reader asked
        self.finished = 0.0
        self.approvals: set[str] = set()
        self._cond = asyncio.Condition()
        self.task_id = 0                   # its record in the task log
        self.title = ""
        self.source = "chat"               # chat | api | schedule:<id> | background
        self.unattended = False            # nobody is expected to watch: never stopped for want of a reader, approvals wait longer
        self.owner = "owner"               # whose browser tab it drives
        self.status_text = ""              # the live line for the agent screen ("Opening a site", ...)
        self.thinking_since: float | None = None   # monotonic time the model began its current reasoning (live_line: a long think)
        self.thinking_text = ""            # the status_text that reasoning set (another status since then: not thinking any more)
        self.waiting = 0                   # approvals waiting for an answer right now
        self.handing = 0                   # human checks handed to the user right now (the reply is not stopped for want of a reader)
        self.final_status = ""             # done | failed | stopped, once it is over
        self.final_error = ""
        self.ended = asyncio.Event()
        self.quiet_end = False             # no task_* notification at the end (a background job's own steps: its report speaks for it)
        self.note_head = ""                # the first words of the end notification instead of "Finished" (a job's report: "Needs your help")
        self.worker_id = ""                # a background job's step: its job (an expired approval resumes it)
        self.rerun: dict[str, Any] | None = None   # how to start this unattended run again (an expired approval's retry, followups.py)
        self.resume: dict[str, Any] | None = None  # how to start this turn again after a restart or a model outage (resume.py)
        self.restarts = 0                  # how many restarts the turns before this one (the same work) went through
        self.offer = ""                    # restart | model: its last message gets a "Continue" button that resumes it
        self.down_error = ""               # the provider error that made it wait for the model
        self.limited = False               # it failed because the model's usage limit is used up (model_limits.py)
        self.limit_reset: float | None = None   # then: when the model may be called again
        self.goal_ids: set[int] = set()    # the goals it works on (goals.py "working": the app shows them as being worked on)
        self.goal_origin: set[int] = set() # the goals it was started for (a kickoff / resume): deleting one of them stops it
        self.reaction_msg = 0              # the owner's message it answers: 👀 now, its emoji when the work is over (reactions.py)
        self.reaction_choice = ""          # the emoji the model chose with `react`
        self.asked = False                 # it ended on a question to the user (quick_replies, await_reply)
        self.jobs: list[str] = []          # background jobs it handed work to (delegate)
        self.request = ""                  # the user's words that started it (its last user message's text)
        self.cond = ""                     # the conditional task a watcher's hit started it for ("watcher:<id>"; schedule_grants.py)
        self.reacted = False               # the end reaction was sent (right before "done", or at the end of a failed reply)
        self.read_pending = False          # 👀 is due once the model starts answering (its first thinking, text or tool event)
        self.log: Any = None               # the turn log that writes its events down (turn_log.py), set by run_turn

    async def emit(self, item: dict[str, Any]) -> None:
        async with self._cond:
            self.events.append(item)
            if self.log is not None:
                self.log.add(self.id, len(self.events) - 1, item)
            self._cond.notify_all()

    async def close(self) -> None:
        async with self._cond:
            if not self.done:
                self.done, self.finished = True, time.monotonic()
                if self.log is not None:
                    self.log.finished(self.id, self.final_status or "stopped")
            self._cond.notify_all()

    async def wait(self, after: int, timeout: float) -> tuple[list[dict[str, Any]], bool]:
        """Events from position `after` on, waiting up to `timeout` seconds for the first one; and whether the reply is over."""
        deadline = time.monotonic() + timeout
        async with self._cond:
            while len(self.events) <= after and not self.done:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    await asyncio.wait_for(self._cond.wait(), left)
                except asyncio.TimeoutError:
                    break
            self.touched = time.monotonic()
            return self.events[after:], self.done



def _server() -> Any:
    """The server module, read at call time: tests tune its timing constants (TURN_IDLE_SECONDS, LONG_THINKING_SECONDS, ...)."""
    from . import server
    return server


class TurnRegistry:
    """The replies of this server: running ones, and finished ones for a little while (a reader that is catching up).

    `session_of` / `hub_of` give the browser session and the live-view hub at call time (create_app sets them after this exists).
    """

    def __init__(self, session_of: Callable[[], Any], hub_of: Callable[[], Any]):
        self.turns: dict[str, Turn] = {}
        self.goal_task_ids: dict[int, set[int]] = {}   # goal -> the task ids of the turns started for it (deleting the goal stops their jobs)
        self._session_of, self._hub_of = session_of, hub_of

    def find(self, turn_id: str) -> Turn | None:
        return self.turns.get(turn_id)

    def running(self) -> list[Turn]:
        """The replies running now, newest task first (the agent screen, the live view, the tab list)."""
        return sorted((t for t in self.turns.values() if not t.done), key=lambda t: t.task_id, reverse=True)

    def held_by(self, owner: str) -> str | None:
        session = self._session_of()
        return session.controlled_by(owner) if session is not None else None

    def turn_line(self, t: Turn) -> str:
        """The turn's status; a reasoning that has gone on for LONG_THINKING_SECONDS says it is a long think."""
        from . import i18n
        s = _server()
        if (t.thinking_since is not None and t.status_text and t.status_text == t.thinking_text
                and time.monotonic() - t.thinking_since >= s.LONG_THINKING_SECONDS):
            return i18n.t(s.LONG_THINKING)
        return t.status_text or i18n.t("Thinking")

    @staticmethod
    def cancel(turn: Turn) -> None:
        if turn.task is not None and not turn.task.done():
            turn.task.cancel()

    def looked_after(self, owner: str) -> bool:
        """The user holds this tab or watches it live: its reply is being followed even if the chat is not asked."""
        session, hub = self._session_of(), self._hub_of()
        return session is not None and (session.controlled_by(owner) is not None or bool(hub and hub.watching(owner)))

    def sweep(self) -> None:
        """Stop replies that nobody is following any more, and forget old finished ones."""
        s = _server()
        now = time.monotonic()
        for t in list(self.turns.values()):
            if t.done:
                if now - t.finished > s.TURN_KEEP_SECONDS:
                    self.turns.pop(t.id, None)
            elif now - t.touched > s.TURN_IDLE_SECONDS and not t.unattended and not t.handing and not self.looked_after(t.owner):
                self.cancel(t)


def run_turn(ctx: Any, provider: str, model: str, messages: list[dict[str, Any]],
               observe: Callable[[dict[str, Any] | None], None] | None = None,
               effort: str | None = None, fallback: bool = True, *, source: str = "chat", title: str = "",
               unattended: bool = False, owner: str | None = None, context: TurnContext | None = None,
               tools_: list[Tool] | None = None, max_steps: int | None = None, tool_scope: str | None = None, cond: str = "",
               stream_wrap: Callable[[Any], Any] | None = None) -> Turn:
    """Begin a reply in the background. Whoever is watching (a stream, or a phone asking again and again) reads it from the Turn.
    `context`: the stored conversation, built (and compacted when needed) for each model that runs the turn; `messages` is then
    only this turn's own message(s). Without it `messages` is sent as it is.
    `tools_` / `max_steps`: another tool list and step budget than a chat turn's (a background job's run, workers.py).
    `tool_scope`: whose loaded tools this run shows and adds to (tools on demand, agent/toolbox.py): default "chat" for the
    conversation's own turns (chat, api, handoff, background), else the source ("schedule:<id>", "checklist", ...).
    `stream_wrap`: wraps this turn's stream function (the remembered checkout steps answer the model's requests themselves, buy_replay.py).
    Every reply is also a task in the task log (`source`: chat | api | schedule:<id> | background | worker | handoff). `unattended`: nobody watches it
    (it is never stopped for want of a reader, and approvals wait UNATTENDED_APPROVAL_SECONDS and leave a notification)."""
    S = _server()   # constants tests tune are read at each call, as they were when this lived in create_app
    APPROVAL_TIMEOUT_SECONDS = S.APPROVAL_TIMEOUT_SECONDS
    AgentDone = S.AgentDone
    ApprovalRequest = S.ApprovalRequest
    AssistantText = S.AssistantText
    Awaitable = S.Awaitable
    CONTINUE_LABEL = S.CONTINUE_LABEL
    Callable = S.Callable
    ContextOverflow = S.ContextOverflow
    DISK_FULL_CODE = S.DISK_FULL_CODE
    EMPTY_REPLY_NUDGE = S.EMPTY_REPLY_NUDGE
    ENTRY_WAIT_SECONDS = S.ENTRY_WAIT_SECONDS
    FALLBACK_REASON = S.FALLBACK_REASON
    GOAL_MARK = S.GOAL_MARK
    GRANT_REASONS = S.GRANT_REASONS
    HANDOVER_JOB_MARGIN = S.HANDOVER_JOB_MARGIN
    HANDOVER_PER_SITE = S.HANDOVER_PER_SITE
    HANDOVER_RESOLVED_KEEP = S.HANDOVER_RESOLVED_KEEP
    HANDOVER_SECONDS = S.HANDOVER_SECONDS
    HANDOVER_UNATTENDED_SECONDS = S.HANDOVER_UNATTENDED_SECONDS
    LIMIT_CODE = S.LIMIT_CODE
    MODEL_DOWN_LINE = S.MODEL_DOWN_LINE
    MODEL_STARTED = S.MODEL_STARTED
    NO_ANSWER_LINE = S.NO_ANSWER_LINE
    OWN_DATA_SOURCES = S.OWN_DATA_SOURCES
    REPEAT_NOTE = S.REPEAT_NOTE
    RepeatGuard = S.RepeatGuard
    STEP_LIMIT_BACKGROUND = S.STEP_LIMIT_BACKGROUND
    STEP_LIMIT_CONTINUE = S.STEP_LIMIT_CONTINUE
    STEP_LIMIT_PROGRESS = S.STEP_LIMIT_PROGRESS
    STEP_LIMIT_STOPPED = S.STEP_LIMIT_STOPPED
    StepLimit = S.StepLimit
    TURN_NOTE = S.TURN_NOTE
    Tool = S.Tool
    ToolResult = S.ToolResult
    ToolStarted = S.ToolStarted
    UNATTENDED_APPROVAL_SECONDS = S.UNATTENDED_APPROVAL_SECONDS
    WATCHED_SECONDS = S.WATCHED_SECONDS
    WORKER_PAUSED = S.WORKER_PAUSED
    approval_timeout = S.approval_timeout
    arg_lines = S.arg_lines
    asyncio = S.asyncio
    browser_owner = S.browser_owner
    chat_error_text = S.chat_error_text
    continue_buttons = S.continue_buttons
    current_approval = S.current_approval
    current_call = S.current_call
    current_turn = S.current_turn
    datetime = S.datetime
    disk_full = S.disk_full
    disk_full_text = S.disk_full_text
    effort_extra = S.effort_extra
    effort_listed = S.effort_listed
    effort_restore = S.effort_restore
    entry_context = S.entry_context
    get_provider = S.get_provider
    handover = S.handover
    i18n = S.i18n
    is_outage = S.is_outage
    limit_of = S.limit_of
    location_ask = S.location_ask
    log = S.log
    looks_final = S.looks_final
    model_limits = S.model_limits
    one_run_tab = S.one_run_tab
    re = S.re
    readable_args = S.readable_args
    rule_kind = S.rule_kind
    run_agent = S.run_agent
    sched_zone = S.sched_zone
    schedule_rule_ok = S.schedule_rule_ok
    serialize = S.serialize
    time = S.time
    title_from = S.title_from
    tool_labels = S.tool_labels
    ui_sink = S.ui_sink
    uuid = S.uuid
    TurnContext = ctx.TurnContext
    activity = ctx.activity
    approval_site = ctx.approval_site
    broker = ctx.broker
    chat_images = ctx.chat_images
    compactor = ctx.compactor
    covered_calls = ctx.covered_calls
    run_progress = ctx.run_progress
    deliveries = ctx.deliveries
    entries = ctx.entries
    fallback_model = ctx.fallback_model
    followups = ctx.followups
    goal_book = ctx.goal_book
    goal_task_ids = ctx.goal_task_ids
    goals_of_turn = ctx.goals_of_turn
    grant_done_note = ctx.grant_done_note
    handover_seen = ctx.handover_seen
    key_args = ctx.key_args
    learn_window = ctx.learn_window
    meta_json = ctx.meta_json
    orders = ctx.orders
    payment_ledger = getattr(ctx, "payment_ledger", None)   # the approved payments (purchase_result closes the receipt check of each)
    owner_tasks = ctx.owner_tasks
    pending_approvals = ctx.pending_approvals
    pending_replies = ctx.pending_replies
    pending_comparisons = ctx.pending_comparisons
    comparisons = ctx.comparisons
    persona = ctx.persona
    pin_deliveries = ctx.pin_deliveries
    provider_key = ctx.provider_key
    provider_identity = getattr(ctx, "provider_identity", provider_key)   # a custom endpoint is its host
    reactions = ctx.reactions
    resolved_checks = ctx.resolved_checks
    retire_tab = ctx.retire_tab
    schedule_grants = ctx.schedule_grants
    scope_of = ctx.scope_of
    session = ctx.session
    settings = ctx.settings
    stopping = ctx.stopping
    storage_watch = ctx.storage_watch
    store = ctx.store
    stream_fn = stream_wrap(ctx.stream_fn) if stream_wrap is not None else ctx.stream_fn
    sweep = ctx.sweep
    tab_failed = ctx.tab_failed
    task_closed = ctx.task_closed
    task_grants = ctx.task_grants
    tools = ctx.tools
    turn_scopes = ctx.turn_scopes
    turn_toolbox = ctx.turn_toolbox
    permission_gate = getattr(ctx, "permission_gate", None)   # the owner's per-connector limits and the privacy guard (permissions.py)
    done_once = getattr(ctx, "done_once", None)   # a step that sends, changes or deletes is done once (done_once.py)
    watch = getattr(ctx, "injection_watch", None)   # notices instructions aimed at the assistant in outside text (injection_watch.py)
    turns = ctx.turns
    window_for = ctx.window_for
    workers = ctx.workers
    sweep()
    turn = Turn()
    turn.source, turn.unattended, turn.owner = source, unattended, owner or browser_owner.get()
    turn.cond = cond
    last_user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "")
    turn.title = (title or title_from(last_user if isinstance(last_user, str) else ""))[:80]
    # the user's own words for this turn (an order the agent starts without begin_order is read from them: tools/order.py)
    turn.request = (last_user if isinstance(last_user, str) else " ".join(
        str(p.get("text") or "") for p in (last_user or []) if isinstance(p, dict) and p.get("type") == "text"))[:2000]
    turn.task_id = activity.start_task(turn.title, source, turn.id)
    turn.msg_floor = int(store._all("SELECT COALESCE(MAX(id), 0) AS m FROM messages")[0]["m"])   # quick replies go on a newer message
    turn.goal_ids = goals_of_turn(source, last_user if isinstance(last_user, str) else "")
    if isinstance(last_user, str) and last_user.startswith(GOAL_MARK):
        turn.goal_origin = set(turn.goal_ids)   # its own goal work: the jobs it starts are that goal's work too (stop_goal_work)
        for gid in turn.goal_origin:
            goal_task_ids.setdefault(gid, set()).add(turn.task_id)
    owner_tasks[turn.owner] = turn.task_id
    turns[turn.id] = turn
    turn.log = getattr(ctx, "turn_log", None)
    if turn.log is not None:
        turn.log.started(turn.id)
    run_key = run_progress.key(turn.id, turn.worker_id)
    run_progress.begin(run_key)
    answer: list[str] = []
    said = ReplyText()   # what the task's record keeps as the reply: a stopped reply's line that led into a tool step is not its answer
    outcome = {"status": "", "error": ""}
    # hand-over and location cards this reply raised that the apps have not yet seen end (approval id -> the closing event, without
    # its state): a card's own coroutine sends the end; when the reply is stopped while one waits, finish() sends "cancelled"
    open_cards: dict[str, dict[str, Any]] = {}
    applied: list[tuple[dict[str, Any], str]] = []   # applications an apply permission covered in this run: (grant, what), told at the end

    def track(item: dict[str, Any]) -> None:
        kind = item.get("type")
        said.feed(item)
        if kind != "thinking" and kind in MODEL_STARTED:
            turn.thinking_since = None   # the model moved on (text, a tool, a card): a later think starts its own clock
        if kind == "text":
            answer.append(item.get("text", ""))
            turn.status_text = i18n.t("Writing the answer")
        elif kind == "thinking":
            turn.status_text = i18n.t("Thinking")
            if turn.thinking_since is None:
                turn.thinking_since, turn.thinking_text = time.monotonic(), turn.status_text
        elif kind == "tool_started":
            turn.status_text = item.get("label") or turn.status_text
        elif kind == "browser":
            turn.status_text = item.get("status") or turn.status_text
        elif kind == "tool_result" and str(item.get("name", "")).startswith("browser_"):
            tab_failed[turn.owner] = bool(item.get("is_error"))
        elif kind == "payment_auto":
            pay = item.get("payment") or {}
            followed = not turn.unattended and time.monotonic() - turn.touched < WATCHED_SECONDS
            sg = item.get("schedule_grant") or {}
            title = (i18n.t("Bought: {summary}, {amount}, via {via}", summary=str(pay.get("summary", ""))[:60],
                            amount=pay.get("amount_text", ""), via=sg.get("via", "")) if sg else
                     i18n.t("Paid automatically: {amount}", amount=pay.get('amount_text', '')))
            activity.notify("payment_auto", title,
                            f"{pay.get('site', '')} · {pay.get('summary', '')}"[:400], task_id=turn.task_id,
                            payment_id=pay.get("id"), push=not followed)
        elif kind == "replies" or (kind == "followup" and item.get("kind") == "reply"):
            turn.asked = True   # a question back to the user: the message's work is not over yet
        elif kind == "done":
            outcome["status"] = "done"
        elif kind == "error":   # the task's record keeps the detail (the Work page, the log); the chat shows only the message
            outcome["status"], outcome["error"] = "failed", str(item.get("detail") or item.get("message", ""))

    repeats = RepeatGuard()   # text the model says again after a tool call is not passed on twice (repeats.py)

    async def put(item: dict[str, Any]) -> None:
        for it in repeats.feed(item):
            await deliver(it)

    async def deliver(item: dict[str, Any]) -> None:
        if observe:
            observe(item)   # what was said is saved as it is said, whether or not anyone is still reading
        try:
            track(item)
        except Exception:  # noqa: BLE001 - the log must never break a reply
            log.exception("task log update failed")
        if turn.read_pending and item.get("type") in MODEL_STARTED:   # the first answer from the model: 👀 goes on the message
            turn.read_pending = False
            try:
                await turn.emit(reactions.read(turn.reaction_msg))
            except Exception:  # noqa: BLE001 - a reaction must never break a reply
                log.exception("reaction update failed")
        if item.get("type") in ("done", "error"):   # the message's emoji (⚠️ for an error) right before the reply's last word
            turn.read_pending = False             # the end emoji replaces a 👀 that never came
            await react_end("done" if item["type"] == "done" else "failed")
        await turn.emit(item)

    async def react_end(status: str) -> None:
        """👀 gives way to the message's emoji, or stays while its work goes on (reactions.py); once per reply. A stopped reply's
        ⚠️ is only stored: whoever stopped it has stopped reading, and the next load of the messages shows it."""
        if not turn.reaction_msg or turn.reacted or stopping[0]:
            return
        turn.reacted = True
        try:
            live = [w for w in turn.jobs if (r := workers._row(w)) is not None and r["status"] in ("running", *WORKER_PAUSED)]
            for ev in reactions.finish(turn.reaction_msg, status, turn.reaction_choice, live, turn.asked):
                if status != "stopped":
                    await turn.emit(ev)
        except Exception:  # noqa: BLE001 - never break the end of a reply
            log.exception("reaction update failed")

    def tell_progress(count: int) -> None:
        """Backlog 66: a long run says once how far it got. Not while the owner watches the reply live (they see it there)."""
        if not turn.unattended and time.monotonic() - turn.touched < WATCHED_SECONDS:
            return
        name = applied[0][0].get("task_name") or turn.title if applied else turn.title
        items = "; ".join(dict.fromkeys(w for _, w in applied if w))
        activity.notify("task_done", i18n.t(PROGRESS_TITLE, count=count)[:120],
                        (f"{name} · {items}" if name else items)[:400], task_id=turn.task_id)

    async def finish() -> None:
        if turn.done:
            return
        for approval_id in list(turn.approvals):   # deny anything still waiting for a person
            broker.discard(approval_id)
            deliveries.discard(approval_id)          # and forget any card sent for it
            pin_deliveries.discard(approval_id)      # and any payment PIN
        await close_cards()
        if applied:
            try:   # "N건 신청했어요" with what was applied for: one notice for the run
                followed = not turn.unattended and time.monotonic() - turn.touched < WATCHED_SECONDS
                name = applied[0][0].get("task_name") or turn.title
                items = "; ".join(dict.fromkeys(w for _, w in applied if w))
                activity.notify("task_done", i18n.t(APPLIED_TITLE, count=len(applied))[:120],
                                f"{name} · {items}"[:400] if name else items[:400], task_id=turn.task_id, push=not followed)
            except Exception:  # noqa: BLE001 - never break the end of a reply
                log.exception("could not tell what was applied for")
            applied.clear()
        if not turn.worker_id:
            handover_seen.pop(turn.id, None)          # a chat reply's skipped sites count for that reply only
            run_progress.end(run_key)
        task_grants.end(f"turn:{turn.id}")            # what was allowed for this reply ends with it (a job it started keeps it)
        for key in [k for k in covered_calls if k[0] == turn.id]:
            covered_calls.pop(key, None)
        try:
            schedule_grants.settle(turn_scopes(turn))   # a one-shot task that used its count: its grants go with this run
            if turn.worker_id and (row := workers._row(turn.worker_id)) is not None and row["status"] in ("done", "failed", "stopped"):
                schedule_grants.drop_job(turn.worker_id)   # a job's own grants end with the job
                run_progress.end(run_key)
        except Exception:  # noqa: BLE001
            log.exception("task grants could not be settled")
        orders.end_scope(f"turn:{turn.id}")           # and so does the order it was preparing
        if repeats.end():
            log.info("a repeated reply text was left out (turn %s)", turn.id)
        if payment_ledger is not None:
            try:   # a payment of this task whose receipt was never checked against the approval: one plain line in the report
                for paid in payment_ledger.unchecked(turn.task_id):
                    paid.warned = True
                    line = i18n.t("Kelo did not check the order after paying at {site}; please look at the order yourself.", site=paid.host)
                    await deliver({"type": "text", "text": "\n\n" + line})
                    if turn.quiet_end:   # no closing notice will carry it
                        activity.notify("purchase_unchecked", line[:120], paid.summary, task_id=turn.task_id, urgent=True)
            except Exception:  # noqa: BLE001 - never break the end of a reply
                log.exception("could not tell that a receipt was not checked")
        if done_once is not None and turn.unattended:
            try:   # a step of this run whose result is still unknown: one follow-up asks the owner to check (followups.py)
                if done_once.run_ended({"task_id": turn.task_id, "turn_id": turn.id, "unattended": True}):
                    asyncio.ensure_future(followups.tick())
            except Exception:  # noqa: BLE001 - never break the end of a run
                log.exception("could not tell about an unknown result")
        if observe:
            observe(None)   # finished or stopped: keep whatever was said
        try:   # screenshots shown during the reply go with the message it ended with
            if chat_images.count_for(turn.id):
                row = store._all("SELECT MAX(id) AS m FROM messages WHERE role = 'assistant' AND id > ?", (turn.msg_floor,))[0]["m"]
                if row:
                    chat_images.attach(turn.id, int(row))
        except Exception:  # noqa: BLE001
            log.exception("chat pictures could not be attached")
        if (asked := pending_replies.pop(turn.id, None)) is not None:   # buttons under the message this reply ended with
            try:
                row = store._all("SELECT MAX(id) AS m FROM messages WHERE role = 'assistant' AND id > ?", (turn.msg_floor,))[0]["m"]
                if row:
                    goal_book.attach_replies(int(row), asked[0], asked[1])
                    if turn.offer:   # the "Continue" button resumes this turn's task
                        activity.add_offer(int(row), turn.task_id, turn.offer, asked[0][0]["options"][0])
            except Exception:  # noqa: BLE001
                log.exception("quick replies could not be attached")
        if (card := pending_comparisons.pop(turn.id, None)) is not None:   # the comparison card under the message this reply ended with
            try:
                row = store._all("SELECT MAX(id) AS m FROM messages WHERE role = 'assistant' AND id > ?", (turn.msg_floor,))[0]["m"]
                if row:
                    comparisons.attach(int(row), card)
            except Exception:  # noqa: BLE001
                log.exception("the comparison card could not be attached")
        turn.final_status, turn.final_error = outcome["status"] or "stopped", outcome["error"] or turn.down_error
        await react_end(turn.final_status)   # a reply that ended without "done" or "error" (stopped, cut off)
        try:
            if not stopping[0]:   # a server going down: the task stays "interrupted" for the next start (resume_after_restart)
                task_closed(turn, turn.final_status, said.text(), turn.final_error)
        except Exception:  # noqa: BLE001
            log.exception("task log update failed")
        await turn.close()
        turn.ended.set()
        if session is not None and session.has_tab(turn.owner):
            if one_run_tab(turn.owner):
                asyncio.ensure_future(retire_tab(turn.owner))   # the run is over: keep its last picture, close the tab
            else:
                session.touch(turn.owner)                      # a chat tab's idle hour counts from the end of its last turn

    async def page_title() -> str:
        """The title of this reply's tab, read without opening one ("" when there is none)."""
        try:
            page = getattr(session, "_pages", {}).get(turn.owner)
            return " ".join(str(await asyncio.wait_for(page.title(), 2)).split())[:120] if page is not None and not page.is_closed() else ""
        except Exception:  # noqa: BLE001 - the notice then names the site
            return ""

    async def close_cards() -> None:
        """The reply ends while hand-over or location cards are still open (stopped by the user, the sweep, a cleared history): each
        one ends as "cancelled" for the apps, in this reply's events (the live card closes), in the pending list and, for a hand-over,
        in the resolved list the apps read (GET /api/approvals/pending)."""
        for approval_id, closing in list(open_cards.items()):
            open_cards.pop(approval_id, None)
            try:
                if pending_approvals.pop(approval_id, None) is not None:   # its own coroutine never got to its ending
                    activity.answer_approval(approval_id, "cancelled")
                    activity.resolve_approval_notes(approval_id)
                if closing["type"] == "human_check" and approval_id not in resolved_checks:
                    resolved_checks[approval_id] = {"approval_id": approval_id, "kind": "human_check", "outcome": "cancelled",
                                                    "site": closing.get("site", ""), "tab": closing.get("tab", ""),
                                                    "task_id": turn.task_id, "at": time.time()}
            except Exception:  # noqa: BLE001 - the card still closes in the reply
                log.exception("could not close a card of a stopped reply")
            await turn.emit({**closing, "state": "cancelled"})

    async def approve(req: ApprovalRequest) -> bool:
        label = tool_labels.label(req.name)
        site = approval_site(req.name, req.arguments)
        from .tools.browser import POINT_TOOLS, step_key
        clicks = ("browser_click", *POINT_TOOLS)
        sel = step_key(req.name, req.arguments)   # the selector, or "point:x,y" of a browser_click_at / browser_drag
        # a ref (e12) is judged by the words the page reading gave it, never by the ref itself
        words = session.selector_words(sel) if session is not None and hasattr(session, "selector_words") else sel
        final_click = req.name in clicks and ((req.name == "browser_click" and looks_final(words))
                                              or (session is not None and session.judged(sel) == "pay"))
        # a step whose target Kelo could not read at all (commit kind "unknown"): like a final click, no rule ever allows it
        final_click = final_click or (session is not None and req.name in (*clicks, "browser_type")
                                      and session.judged(sel) == "unknown")
        if not final_click and not req.force and (rule := activity.matching_rule(req.name, site)):
            # the user allowed this kind of step on this site before ("allow on this site"); payments never get here
            activity.log_approval(uuid.uuid4().hex, task_id=turn.task_id, tool=req.name, label=label, site=site, detail=req.detail,
                                  decision="allowed_by_rule", rule_id=rule["id"], unattended=turn.unattended)
            return True
        sched_id = int(turn.source[9:]) if turn.source.startswith("schedule:") and turn.source[9:].isdigit() else None
        if not final_click and not req.force and sched_id and activity.matching_schedule_rule(sched_id, req.name, site):
            # "keep allowing in this scheduled job": the same step on the same site in this schedule's runs; never payments
            activity.log_approval(uuid.uuid4().hex, task_id=turn.task_id, tool=req.name, label=label, site=site, detail=req.detail,
                                  decision="allowed_by_rule", unattended=turn.unattended)
            return True
        # a "when X happens, do Y" task the user allowed at setup (task_allow, schedule_grants.py): a message or a change inside
        # its permission goes ahead without a card (logged, and the user is told right after it ran); one outside it asks
        commit = session.judged(sel) if session is not None and req.name in (*clicks, "browser_type") else ""
        secret = session is not None and req.name == "browser_type" and session.judged_secret(sel)
        # (a card the owner's own setting or the privacy guard asked for is never skipped by a task's permission either)
        grant_check = schedule_grants.action_check(req.name, req.arguments, site, commit) if not secret and not final_click and not req.force else None
        if grant_check is not None and grant_check.grant is not None:
            g = grant_check.grant
            if schedule_grants.use_action(g, req.name, site, commit):
                what = req.detail or readable_args(req.arguments)
                if g.get("kind") == "apply":   # what was applied for: the page's title (one notice after the run lists them)
                    what = await page_title() or site
                covered_calls[(turn.id, req.id)] = (g, label, what)
            return True
        approval_id, fut = broker.create()
        turn.approvals.add(approval_id)
        current_approval.set((req.id, approval_id))   # the tool, once allowed, can tell which approval it runs under
        extra: dict[str, Any] = {}
        tool = by_name.get(req.name)
        if tool is not None and tool.approval_extra is not None:
            try:
                extra = dict(tool.approval_extra(approval_id, req.arguments) or {})
            except Exception:  # noqa: BLE001 - the card still works without the extra fields
                extra = {}
        timeout = UNATTENDED_APPROVAL_SECONDS if turn.unattended else APPROVAL_TIMEOUT_SECONDS
        notice_title = str(extra.pop("notice_title", "") or "")   # a card with its own notice title (purchase_result's wrong charge)
        if grant_check is not None and grant_check.reason:   # the task has a permission of this kind, but this step is outside it
            extra["lines"] = [{"label": i18n.t("Why it asks"), "value": i18n.t(GRANT_REASONS.get(grant_check.reason, grant_check.reason))},
                              *(extra.get("lines") or arg_lines(req.arguments))][:4]
        urgent, missed = schedule_grants.urgency() if turn.unattended else (False, "")
        if urgent:
            extra.update(urgent=True, missed=missed)
        event = {"type": "approval_request", "approval_id": approval_id, "id": req.id, "name": req.name, "arguments": req.arguments,
                 "detail": req.detail or readable_args(req.arguments), "lines": arg_lines(req.arguments), "label": label,
                 "ask": tool_labels.ask(req.name, req.arguments), "site": site, "commit": commit,
                 "can_remember": bool(rule_kind(req.name) and site and not final_click and not req.force),
                 "can_remember_schedule": bool(sched_id and schedule_rule_ok(req.name) and not final_click and not req.force
                                               and not extra.get("payment")),
                 "schedule_id": sched_id, "task_id": turn.task_id, "expires_at": int(time.time() + timeout), **extra}
        # source / worker_id / title: what asked (a chat reply, a background job, a scheduled run), so the chat can show the card too
        pending_approvals[approval_id] = {**event, "turn_id": turn.id, "asked_at": time.time(), "unattended": turn.unattended,
                                          "source": turn.source, "worker_id": turn.worker_id or None, "title": turn.title}
        activity.log_approval(approval_id, task_id=turn.task_id, tool=req.name, label=label, site=site, detail=req.detail,
                              unattended=turn.unattended)
        if turn.unattended and (urgent or extra.get("urgent")):   # time matters: an urgent notice that says what is missed without an answer
            until = datetime.fromtimestamp(time.time() + timeout, sched_zone(store.profile()["timezone"])).strftime("%H:%M")
            body = i18n.t("Answer by {time}, or {missed}", time=until, missed=missed) if missed else i18n.t("Answer by {time}", time=until)
            activity.notify("approval_waiting", notice_title[:120] or i18n.t("Urgent: please confirm {label}", label=label),
                            f"{body} · {req.detail or turn.title}"[:400], task_id=turn.task_id, approval_id=approval_id, urgent=True)
        elif turn.unattended:
            activity.notify("approval_waiting", i18n.t("Please confirm: {label}", label=label), req.detail or turn.title, task_id=turn.task_id,
                            approval_id=approval_id)
        turn.waiting += 1
        activity.set_task_status(turn.task_id, "waiting_approval")
        turn.status_text = i18n.t("Waiting for approval")
        await put(event)
        allowed = False
        try:
            allowed = await asyncio.wait_for(fut, timeout)
            return allowed
        except asyncio.TimeoutError:
            activity.answer_approval(approval_id, "expired")
            if turn.unattended:   # nobody answered: the follow-ups offer to try the step again, the first reminder right away
                try:
                    origin = {"kind": "worker", "worker_id": turn.worker_id} if turn.worker_id else turn.rerun
                    if followups.approval_expired(pending_approvals.get(approval_id) or event, origin) is not None:
                        asyncio.ensure_future(followups.tick())
                except Exception:  # noqa: BLE001 - the step is skipped either way
                    log.exception("follow-up for an expired approval failed")
            return False
        finally:
            broker.discard(approval_id)
            pending_approvals.pop(approval_id, None)
            activity.answer_approval(approval_id, "cancelled")   # only if nobody answered (the reply was stopped)
            activity.resolve_approval_notes(approval_id)
            turn.waiting -= 1
            if turn.waiting <= 0:
                activity.set_task_status(turn.task_id, "running")
            if not allowed:
                deliveries.discard(approval_id)   # a card the phone may already have sent for it is dropped
                pin_deliveries.discard(approval_id)   # and so is a payment PIN

    async def hand_over(info: dict[str, Any], gone: Callable[[], Awaitable[bool]]) -> str:
        """A human check on the tab this reply works in: a pending item of kind human_check (the chat's live card, the Work page,
        a notification) while the browser step waits. It ends when the check leaves the page (`gone`), the user skips the site
        (POST /api/approvals/{id} approve=false; approve=true from an older app = "done"), it runs out, or the reply stops."""
        site = str(info.get("site") or "")
        seen = handover_seen.setdefault(turn.worker_id or turn.id, {})
        if seen.get(site, 0) < 0 or seen.get(site, 0) >= HANDOVER_PER_SITE:
            return "repeat"   # skipped before, or solved twice already: never a loop of the same blocked page
        timeout = HANDOVER_UNATTENDED_SECONDS if turn.unattended else HANDOVER_SECONDS
        if turn.worker_id:
            try:
                if (row := workers._row(turn.worker_id)) is not None:
                    timeout = max(60.0, min(timeout, float(row["deadline_at"]) - workers._clock() - HANDOVER_JOB_MARGIN))
            except Exception:  # noqa: BLE001 - the plain timeout then
                log.exception("could not read the job's deadline")
        approval_id, fut = broker.create()
        turn.approvals.add(approval_id)
        where = site or i18n.t("The site")
        verifying = info.get("kind") == "verification"   # a sign-in check (a code to type, an approval on the phone; tools/verification.py)
        if verifying:
            label = i18n.t("Sign-in verification")
            title = i18n.t("{site} asks you to confirm the sign-in", site=where)
            detail = str(info.get("name") or "") or title   # what to do, already in the user's language
        else:
            label = i18n.t("Human check")
            title = i18n.t("{site} wants to confirm a person is there", site=where)
            detail = i18n.t("{site} wants to confirm a person is there. Solve it yourself and Kelo will carry on.", site=where)
        sched_id = int(turn.source[9:]) if turn.source.startswith("schedule:") and turn.source[9:].isdigit() else None
        check = {k: info.get(k) for k in ("vendor", "name", "kind", "step")}   # step: approve | code (a sign-in verification)
        tab = str(info.get("tab") or turn.owner)
        event = {"type": "approval_request", "kind": "human_check", "approval_id": approval_id, "id": current_call.get(),
                 "name": "browser_check", "arguments": {}, "detail": detail, "lines": [], "label": label, "ask": title, "site": site,
                 "can_remember": False, "can_remember_schedule": False, "schedule_id": sched_id, "task_id": turn.task_id,
                 "expires_at": int(time.time() + timeout), "tab": tab, "check": check}
        pending_approvals[approval_id] = {**event, "turn_id": turn.id, "asked_at": time.time(), "unattended": turn.unattended,
                                          "source": turn.source, "worker_id": turn.worker_id or None, "title": turn.title}
        activity.log_approval(approval_id, task_id=turn.task_id, tool="browser_check", label=label, site=site, detail=detail,
                              unattended=turn.unattended)
        # one notification (the phone shows it only when the app is not on screen); it opens the chat, where the card waits
        activity.notify("approval_waiting", title, detail if verifying else i18n.t("Solve it yourself and Kelo will carry on."),
                        task_id=turn.task_id, approval_id=approval_id)
        turn.waiting += 1
        turn.handing += 1
        activity.set_task_status(turn.task_id, "waiting_approval")
        turn.status_text = title if verifying else i18n.t("Waiting for you to confirm you are a person")
        open_cards[approval_id] = {"type": "human_check", "approval_id": approval_id, "site": site, "tab": tab}
        await put({**{k: v for k, v in event.items() if k != "arguments"}, "type": "human_check", "state": "waiting"})
        # a verification's `gone` (BrowserSession.wait_verification_gone) itself says True only on signed-in evidence
        # (signin_state.still_signing_in), and "progressed" when the step the user was asked for left the page but the sign-in goes
        # on (a "stay signed in?" page after an approval in an app): the agent then takes the next step (2026-11-10)
        watch = asyncio.ensure_future(gone())
        outcome = "cancelled"
        try:
            done, _ = await asyncio.wait({fut, watch}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if watch in done:
                got = watch.result() if not watch.cancelled() and watch.exception() is None else False
                outcome = "progressed" if got == "progressed" else "solved" if got else "closed"
            elif fut in done:
                answer = (pending_approvals.get(approval_id) or {}).get("answer")
                outcome = {"skip": "skipped", "done": "done"}.get(str(answer), "cancelled")
            else:
                outcome = "expired"
        finally:
            watch.cancel()
            broker.discard(approval_id)
            pending_approvals.pop(approval_id, None)
            decision = {"solved": "solved", "done": "solved", "skipped": "skipped", "expired": "expired",
                        "progressed": "progressed"}.get(outcome, "cancelled")   # progressed: the user's part is done, not signed in
            activity.answer_approval(approval_id, decision)
            activity.resolve_approval_notes(approval_id)
            now = time.time()
            for k in [k for k, v in resolved_checks.items() if now - v["at"] > HANDOVER_RESOLVED_KEEP]:
                resolved_checks.pop(k, None)
            resolved_checks[approval_id] = {"approval_id": approval_id, "kind": "human_check", "outcome": decision, "site": site,
                                            "tab": tab, "task_id": turn.task_id, "at": now}
            seen[site] = seen.get(site, 0) + 1 if decision in ("solved", "progressed") else -1
            turn.waiting -= 1
            turn.handing -= 1
            if turn.waiting <= 0:
                activity.set_task_status(turn.task_id, "running")
        if verifying and decision == "solved":   # signed in on evidence: the login that stopped at this verification worked
            from .signin_state import login_verified
            login_verified(store, site)
        turn.status_text = i18n.t("Carrying on") if decision in ("solved", "progressed") else turn.status_text
        if open_cards.pop(approval_id, None) is not None:   # not already closed by the reply's end (close_cards)
            await put({"type": "human_check", "approval_id": approval_id, "state": decision, "site": site, "tab": tab})
        return outcome

    async def ask_location(reason: str) -> str:
        """my_location found no location: a card in this followed reply asks the phone for it (LOCATION REQUEST) and the tool waits,
        at most as long as an approval card. "allowed" (the app sent the place), "refused", "expired" or "cancelled"."""
        approval_id, fut = broker.create()
        turn.approvals.add(approval_id)
        refused_before = bool(store.meta("location_refused_at"))
        timeout = APPROVAL_TIMEOUT_SECONDS
        title = i18n.t("Kelo needs your location")
        event = {"type": "location_request", "approval_id": approval_id, "state": "waiting", "reason": reason,
                 "settings": refused_before, "task_id": turn.task_id, "tab": turn.owner, "expires_at": int(time.time() + timeout)}
        pending_approvals[approval_id] = {**event, "kind": "location", "name": "my_location", "label": title, "ask": title,
                                          "detail": reason, "site": "", "turn_id": turn.id, "asked_at": time.time(),
                                          "unattended": False, "source": turn.source, "worker_id": turn.worker_id or None,
                                          "title": turn.title, "can_remember": False, "can_remember_schedule": False}
        activity.log_approval(approval_id, task_id=turn.task_id, tool="my_location", label=title, site="", detail=reason)
        activity.notify("approval_waiting", title, reason or i18n.t("Open Kelo to share where you are."), task_id=turn.task_id,
                        approval_id=approval_id)
        turn.waiting += 1
        activity.set_task_status(turn.task_id, "waiting_approval")
        open_cards[approval_id] = {"type": "location_request", "approval_id": approval_id}
        await put(event)
        outcome = "cancelled"
        try:
            try:
                await asyncio.wait_for(asyncio.shield(fut), timeout)
                answer = (pending_approvals.get(approval_id) or {}).get("answer")
                outcome = answer if answer in ("allowed", "refused") else "refused" if not fut.result() else "allowed"
            except asyncio.TimeoutError:
                outcome = "expired"
        finally:
            broker.discard(approval_id)
            pending_approvals.pop(approval_id, None)
            activity.answer_approval(approval_id, {"allowed": "allowed_once", "refused": "denied"}.get(outcome, outcome))
            activity.resolve_approval_notes(approval_id)
            turn.waiting -= 1
            if turn.waiting <= 0:
                activity.set_task_status(turn.task_id, "running")
            if outcome == "refused":
                store.set_meta("location_refused_at", str(time.time()))
            elif outcome == "allowed":
                store.set_meta("location_refused_at", "")
        if open_cards.pop(approval_id, None) is not None:   # not already closed by the reply's end (close_cards)
            await put({"type": "location_request", "approval_id": approval_id, "state": outcome})
        return outcome

    async def wait_entry(req: dict[str, Any]) -> str:
        """A fill-in card the agent asked for in this followed reply: wait for the user's answer, at most ENTRY_WAIT_SECONDS. The reply
        is not stopped for want of a reader meanwhile (the user may be in another app copying the key). A stopped reply leaves the
        request open: saving it later starts a new reply that goes on (entry_answered)."""
        turn.waiting += 1
        turn.handing += 1
        activity.set_task_status(turn.task_id, "waiting_approval")
        before = turn.status_text
        turn.status_text = i18n.t("Waiting for you to fill in the card below")
        try:
            return await entries.wait(req["id"], ENTRY_WAIT_SECONDS)
        finally:
            turn.waiting -= 1
            turn.handing -= 1
            if turn.waiting <= 0:
                activity.set_task_status(turn.task_id, "running")
            turn.status_text = before

    def effort_args(prov_name: str, model_name: str) -> dict[str, Any]:
        try:
            real = get_provider(prov_name).name
        except KeyError:
            return {}
        if not effort_listed(real):   # after a restart: what the saved list said, until the list is fetched again
            effort_restore(real, (meta_json(f"models_last:{real}") or {}).get("effort"))
        return effort_extra(real, effort or store.profile()["effort"], model_name)

    turn_tools = tools if tools_ is None else tools_
    by_name = {t.name: t for t in turn_tools}
    toolbox = turn_toolbox(tool_scope or scope_of(source))
    committed = False   # something reached the user that a second try would repeat or contradict: text, or a tool that changes things
    tokens = [0, 0, 0, 0, 0]   # input / output / cache-read / cache-write / reasoning tokens of every pass, finished or not: discarded ones were spent too

    def measured(n: int) -> None:
        compactor.last_request_tokens = n

    limited: list[StepLimit] = []   # the pass used its step budget (agent/loop.py StepLimit: its progress decides what follows)
    used_browser = [False]

    async def run_once(prov_: str, model_: str, thread: list[dict[str, Any]], *, stall_ok: bool = False) -> tuple[dict[str, Any] | None, bool, bool]:
        """One pass of the agent loop; what it does streams to the client as it happens.
        Returns (its finish event, whether it wrote an answer, whether it did anything at all).
        With `stall_ok`, a pass that is going in circles through read-only tools (it repeats a call it already made, before
        writing anything or changing anything) is cut short: the caller can start over instead of waiting for it to give up."""
        nonlocal committed
        done_event: dict[str, Any] | None = None
        answered = acted = False
        built = context is not None and context.provider == prov_ and context.model == model_
        agen = run_agent(prov_, model_, thread, turn_tools, approve=approve,
                         max_steps=max_steps or settings.max_steps, stream_fn=stream_fn,
                         request_extra=effort_args(prov_, model_), measure=measured,
                         context_window=window_for(prov_, model_).context, compact_at=settings.compact_at,
                         compact=context.compact_in_turn if built else None, toolbox=toolbox,
                         extra_steps=task_grants.step_bonus, wrap_up=not turn.worker_id, gate=permission_gate, done_once=done_once, **key_args(prov_))
        try:
            async for ev in agen:
                if isinstance(ev, AgentDone):
                    done_event = serialize(ev)  # held back: only a pass that said or did something may finish the turn
                    tokens[0] += ev.input_tokens
                    tokens[1] += ev.output_tokens
                    tokens[2] += ev.cache_read_tokens
                    tokens[3] += ev.cache_write_tokens
                    tokens[4] += ev.reasoning_tokens
                    continue
                if isinstance(ev, StepLimit):
                    limited.append(ev)
                    if not turn.worker_id:
                        continue   # a chat reply says what follows itself (step_limit_end); a job's run chains its chunks
                if isinstance(ev, ToolStarted):
                    acted = True
                    used_browser[0] = used_browser[0] or ev.name.startswith("browser_")
                    if (tool := by_name.get(ev.name)) is not None and not tool.read_only:
                        committed = True
                if (isinstance(ev, ToolResult) and not ev.is_error and (src := getattr(by_name.get(ev.name), "source", ""))
                        in OWN_DATA_SOURCES):
                    try:   # links in the user's own data: a permission for many items may cover the sites they lead to
                        task_grants.note_links(ev.output)
                    except Exception:  # noqa: BLE001
                        log.exception("could not note the links of %s", src)
                if isinstance(ev, AssistantText) and ev.text:
                    acted = answered = committed = True
                if isinstance(ev, ToolResult) and not ev.is_error and (tool := by_name.get(ev.name)) is not None and tool.ends_turn:
                    answered = True   # a job's final report ends its run: no answer text is due
                if isinstance(ev, ToolResult) and (hit := covered_calls.pop((turn.id, ev.id), None)) is not None and not ev.is_error:
                    if hit[0].get("kind") == "apply":   # applications: one notice after the run (finish), not one per click
                        applied.append((hit[0], hit[2]))
                        so_far = run_progress.add(run_key)
                        if run_progress.due(run_key):
                            try:
                                tell_progress(so_far)
                            except Exception:  # noqa: BLE001 - a notification never breaks the reply
                                log.exception("could not tell how far the run got")
                        await put(serialize(ev))
                        continue
                    try:   # a message or change a task's setup permission covered: the user hears right after
                        grant_done_note(turn, hit)
                    except Exception:  # noqa: BLE001 - a notification never breaks the reply
                        log.exception("grant notification failed")
                await put(serialize(ev))
                if stall_ok and not committed and isinstance(ev, ToolResult) and ev.output.startswith(REPEAT_NOTE):
                    break
        finally:
            await agen.aclose()
        return done_event, answered, acted

    def short_thread(thread: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The same conversation under the app's own short rules (no long rules, skills list or files).
        The summary block of the earlier conversation stays."""
        if turn.source.startswith(("group:", "recheck")):   # a messenger group's turn has its own short prompt: never the owner's rules, names and goals
            return thread
        noted = any(isinstance(m.get("content"), str) and m["content"].startswith(TURN_NOTE) for m in thread)   # the turn's lines are there
        return [{"role": "system", "content": settings.system_prompt + ("" if noted else persona())},
                *(m for i, m in enumerate(thread) if not (i == 0 and m.get("role") == "system"))]

    def status(text: str) -> None:
        turn.status_text = text

    async def thread_for(prov_: str, model_: str) -> list[dict[str, Any]]:
        if context is None:
            return messages
        context.on_status = status
        return await context.build(prov_, model_)

    async def first_pass(prov_: str, model_: str) -> tuple[list[dict[str, Any]], tuple[dict[str, Any] | None, bool, bool]]:
        """The turn's first pass on this model. Too long for the model: lower its window, compact under it, try once more."""
        thread = await thread_for(prov_, model_)
        try:
            return thread, await run_once(prov_, model_, thread, stall_ok=True)
        except ContextOverflow as e:
            learn_window(prov_, model_, e.tokens)
            if committed or context is None:
                raise
            await put({"type": "retry", "reason": "the conversation was too long for the model"})
            thread = await thread_for(prov_, model_)
            return thread, await run_once(prov_, model_, thread, stall_ok=True)

    async def pump() -> None:
        ui_sink.set(put)   # browser cards and other extra events from tools reach this reply (this task only)
        current_turn.set({"task_id": turn.task_id, "turn_id": turn.id, "source": turn.source, "unattended": turn.unattended,
                          "cond": turn.cond, "request": turn.request})
        browser_owner.set(turn.owner)
        injection_watch_var.set(watch)
        approval_timeout.set(UNATTENDED_APPROVAL_SECONDS if turn.unattended else APPROVAL_TIMEOUT_SECONDS)
        handover.set(hand_over)   # a human check on this reply's tab is handed to the user (BrowserSession.snapshot)
        entry_context.set((entries, None if turn.unattended else wait_entry))   # fill-in cards: a followed reply waits for the answer
        location_ask.set(None if turn.unattended else ask_location)   # my_location may ask the phone with a card (location.py)
        attempts = [(provider, model)]
        fb = fallback_model() if fallback else None
        if fb and provider_identity(fb[0]) != provider_identity(provider):   # a fallback on the same provider is no way out of its trouble
            attempts.append(fb)
        try:
            for i, (prov_, model_) in enumerate(attempts):
                try:
                    thread, (done_event, answered, acted) = await first_pass(prov_, model_)
                    if not acted:
                        # Reasoning only / nothing: typically the model asked for a shell the CLI refuses to give it.
                        # Tell it once what it has and try again before treating the model as failed.
                        done_event, answered, acted = await run_once(prov_, model_, [*thread, {"role": "system", "content": EMPTY_REPLY_NUDGE}])
                    if acted and not answered and not committed:
                        # It only looked around (files, lists) and never acted or answered, and nothing was changed: some models
                        # do this under a very long prompt. Start the turn again under the short rules.
                        await put({"type": "retry", "reason": "the model only looked around"})
                        done_event, answered, _ = await run_once(prov_, model_, short_thread(thread))
                    if not acted:
                        raise RuntimeError("the model returned an empty reply")
                    if limited and not turn.worker_id:
                        await step_limit_end(limited[-1], thread, answered)
                    elif not answered:
                        if i + 1 < len(attempts) and not committed:
                            raise RuntimeError("the model used tools but gave no answer")   # the fallback model may answer
                        # it ran tools but never wrote an answer, even when asked without tools: a plain line and Continue,
                        # never a blank screen or an error code
                        await put({"type": "text", "text": i18n.t(NO_ANSWER_LINE)})
                        await offer_continue_button()
                    if done_event:
                        cached = {"cache_read_tokens": tokens[2], "cache_write_tokens": tokens[3]} if tokens[2] or tokens[3] else {}
                        if tokens[0]:
                            log.info("turn %s on %s/%s: %d input tokens (%d read from the prompt cache, %d written), %d output "
                                     "(%d of them reasoning)", turn.id, prov_, model_, tokens[0], tokens[2], tokens[3], tokens[1],
                                     tokens[4])
                        await put({**done_event, "input_tokens": tokens[0], "output_tokens": tokens[1], **cached})
                    return
                except Exception as e:  # noqa: BLE001 - report to the client, never crash the stream
                    if i + 1 < len(attempts) and not committed:
                        nxt = attempts[i + 1]
                        await put({"type": "fallback", "provider": nxt[0], "model": nxt[1],
                                   "reason": i18n.t(FALLBACK_REASON), "detail": f"{type(e).__name__}: {str(e)[:160]}"})
                        continue
                    lim = limit_of(e)
                    if lim is not None:
                        turn.limited, turn.limit_reset = True, lim.until   # what the usage limit holds it for (workers.py reads it)
                    if turn.resume is not None and is_outage(e):   # the provider is down, not the request: wait for it
                        await model_down(e)
                        return
                    if lim is not None:   # the usage is used up: say when it can be used again, not a stack line
                        if turn.unattended:
                            turn.quiet_end = True   # a quiet run (the recurring check) just runs next time; a job's step: its job waits
                        await put({"type": "error", "message": model_limits.chat_line(lim, store.profile()["timezone"]),
                                   "code": LIMIT_CODE, "reset_at": lim.reset_at})
                        return
                    if disk_full(e):   # the server machine's disk is full: say so in the user's words, not as a stack line
                        storage_watch.mark_dirty()
                        await put({"type": "error", "message": disk_full_text(), "code": DISK_FULL_CODE})
                    else:   # the user's words; the exception itself goes to the log and the task's record, never the chat
                        log.warning("turn %s failed: %s: %s", turn.id, type(e).__name__, str(e)[:300])
                        await put({"type": "error", "message": chat_error_text(e), "code": "failed",
                                   "detail": f"{type(e).__name__}: {str(e)[:300]}"})
                    return
        finally:
            await finish()

    async def offer_continue_button() -> None:
        if turn.unattended:
            return   # nobody reads a run's buttons: its task's record says where it stopped
        label = i18n.t(CONTINUE_LABEL)
        pending_replies[turn.id] = (continue_buttons(label), None)
        await put({"type": "replies", "questions": continue_buttons(label), "goal_id": None})

    async def step_limit_end(ev: StepLimit, thread: list[dict[str, Any]], answered: bool) -> None:
        """A chat reply used its step budget. Work that was moving (new results, new items done) is never just dropped: the rest goes
        to a background job that carries on in chunks (it keeps this task's permissions) and the user is told so. A reply that went
        in circles stops with a plain line and a Continue button."""
        nxt, job = "continue", ""
        if ev.progress >= STEP_LIMIT_PROGRESS and "delegate" in by_name and turn.source != "handoff":
            asked = next((m["content"] for m in reversed(thread) if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
            said = "".join(answer).strip()[-2000:]
            task = (f"Carry on with the user's request from the chat; the chat reply ran out of steps. The user asked:\n{asked[:4000]}\n\n"
                    f"What the chat reply already did, in its own words:\n{said or '(it wrote nothing)'}\n\n"
                    "Do the items that are left, one after another; do not redo finished ones. Check what is already done before you "
                    "act when you are not sure. The permission the user gave for this task holds in this job too.")
            try:
                out = await workers.delegate({"task": task, "kind": "browser" if used_browser[0] and session is not None else "general"})
            except Exception:  # noqa: BLE001 - then the Continue button
                log.exception("could not hand the rest of the reply to a job")
                out = "Error"
            if not out.startswith("Error"):
                nxt, job = "background", (re.search(r"\(job (w[0-9a-f]+)", out) or [None, ""])[1]
        elif ev.progress < STEP_LIMIT_PROGRESS:
            nxt = "stopped"
        await put({"type": "step_limit", "steps": ev.steps, "progress": ev.progress, "next": nxt, **({"job_id": job} if job else {})})
        line = {"background": STEP_LIMIT_BACKGROUND, "stopped": STEP_LIMIT_STOPPED}.get(nxt, STEP_LIMIT_CONTINUE)
        await put({"type": "text", "text": ("\n\n" if "".join(answer).strip() else "") + i18n.t(line)})
        if nxt != "background":
            await offer_continue_button()

    async def model_down(e: BaseException) -> None:
        """Every model failed for a reason of the provider. An unattended turn waits for the model (its task: waiting_model); a chat
        turn ends with the "connection dropped" line and a Continue button, and goes on by itself once the model is back."""
        turn.down_error = f"{type(e).__name__}: {str(e)[:300]}"
        lim = limit_of(e)
        if lim is not None:   # the usage limit, not an outage: it waits for the reset (model_wait_tick), the reply says when
            turn.down_error = f"QuotaExhausted: {str(lim)[:300]}"
        log.warning("model unreachable, task %s waits for it: %s", turn.task_id, turn.down_error)
        if not turn.unattended:
            label = i18n.t(CONTINUE_LABEL)
            line = model_limits.chat_line(lim, store.profile()["timezone"]) if lim is not None else i18n.t(MODEL_DOWN_LINE)
            await put({"type": "text", "text": ("\n\n" if "".join(answer).strip() else "") + line})
            pending_replies[turn.id] = (continue_buttons(label), None)
            turn.offer = "model"
            await put({"type": "replies", "questions": continue_buttons(label), "goal_id": None})
            await put({"type": "done", "input_tokens": tokens[0], "output_tokens": tokens[1], "steps": 0})
        outcome["status"], outcome["error"] = "waiting_model", turn.down_error

    turn.task = asyncio.create_task(pump())
    # a reply stopped before it ever got going never reaches pump()'s own ending, so make sure it is closed either way
    turn.task.add_done_callback(lambda _t: None if turn.done else asyncio.ensure_future(finish()))
    return turn


def start_background(ctx: Any, prompt: str, *, title: str, source: str, owner: str, note: str, saved_as: str, extra_note: str = "",
                     restarts: int = 0, resumed_from: int | None = None, cond: str = "") -> Turn:
    """A turn nobody watches (a schedule, a task handed over from the agent screen): the user's main model with the full prompt,
    the conversation as a chat turn sees it (summary + history, compacted inline if it must be), and `note` saying the user is
    away. Its answer is added to the conversation, led by `saved_as`; it never starts the after-turn fold itself.
    `cond`: the conditional task it works for ("watcher:<id>", a watcher's hit tried again): what the owner allowed that task
    covers its steps as it did on the first run (schedule_grants.current_scopes). Raises HTTPException when no model is set."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    NO_REPLY = S.NO_REPLY
    collapse_repeat = S.collapse_repeat
    TurnContext = ctx.TurnContext
    keep_resume = ctx.keep_resume
    pending_links = ctx.pending_links
    resolve_model = ctx.resolve_model
    start_turn = ctx.start_turn
    store = ctx.store
    retried = getattr(getattr(ctx, "schedules", None), "retried_tasks", {})   # tasks of runs that asked to be tried again later
    provider, model = resolve_model(None, None)
    tz = store.profile()["timezone"]
    text = f"{note}\n\n{prompt}" + (f"\n\n{extra_note}" if extra_note else "")
    ctx = TurnContext(tz, [{"role": "user", "content": text}], None)
    said = ReplyText()   # a stopped run keeps only the answer it was writing, never a line that led into a tool step

    def observe(item: dict[str, Any] | None) -> None:
        if item is None:
            if said and said.text().strip() != NO_REPLY and turn.task_id not in retried:   # NO_REPLY (or a retry asked for): nothing joins the chat
                store.add_message("assistant", f"{saved_as}\n" + collapse_repeat(said.text()))
            said.clear()
        else:
            said.feed(item)

    turn = start_turn(provider, model, ctx.tail, observe, source=source, title=title, unattended=True, owner=owner, context=ctx,
                      cond=cond)
    sid = source[9:] if source.startswith("schedule:") else ""
    turn.rerun = ({"kind": "schedule", "schedule_id": int(sid)} if sid.isdigit() else
                  {"kind": "background", "prompt": prompt[:8000], "title": title, "source": source, "owner": owner, "note": note,
                   "saved_as": saved_as, **({"cond": cond} if cond else {})})
    if (link := pending_links.pop(source, None)) is not None:   # an automatic run started again after a restart / outage
        resumed_from, restarts = link
    keep_resume(turn, None if source == "checklist" else dict(turn.rerun), restarts, resumed_from)   # the checklist just runs next time
    return turn
