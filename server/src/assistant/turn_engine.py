"""The turn engine's own objects: a reply in progress (`Turn`), the approvals it waits on (`ApprovalBroker`) and the events that
mean the model started answering. The engine that runs turns is still built in server.create_app (start_turn and its helpers);
these moved out first (2026-11-10). server.py re-exports them.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import approval_edit
from .activity import host_of, is_short_reply, rule_kind, task_title
from .run_asks import RunAsks, purpose_body, purpose_lines
from .chat_views import view_wanted
from .injection_watch import current as injection_watch_var
from .privacy_guard import URL_TOOLS
from .tools.browser import CHAT_CONTROL_WAIT_SECONDS, hold_patience
from .reply_paths import ReplyPaths
from .reply_text import BOOKKEEPING, ReplyText
from .scheduled_runs import PROMISE_ROUND_NOTE, PROMISE_WAIT_S, RunEvidence, promise_check_needed
from .tool_roles import for_kind
from .schedule_grants import ACK_LINE, APPLIED_TITLE, PROGRESS_NOTICE, PROGRESS_TITLE

SLOW_TOOL_PREFIXES = ("browser_",)
SLOW_TOOL_NAMES = frozenset({"delegate", "web_search", "web_fetch"})
ACK_SECONDS = 4.0   # seconds without text before acknowledging slow chat work
_LEADING_BREAK = re.compile(r"^\s*<message_break\s*/?>")
_TRAILING_BREAK = re.compile(r"<message_break\s*/?>\s*$")
PROGRESS_MONITOR_INTERVAL = 10.0   # seconds between progress monitor checks


def is_slow_tool(name: str) -> bool:
    return name.startswith(SLOW_TOOL_PREFIXES) or name in SLOW_TOOL_NAMES

if TYPE_CHECKING:   # only for annotations: TurnContext is create_app's own class (the conversation of a turn)
    from .agent import Tool
    TurnContext = Any

# a reply that ends with a payment whose receipt was never checked gets one more round, with this note (not the user's words)
RECEIPT_NOTE = ("[Not from the user: you paid on {site} but did not check the order. Open the site's order/receipt page now and call "
                "purchase_result with what it shows before you finish.]")


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
MODEL_STARTED = frozenset({"thinking", "text", "tool_started", "tool_result", "browser", "device", "replies", "approval_request",
                           "secret_request", "human_check", "followup", "draft", "nest_changed"})


# Run kinds Kelo starts on its own: nobody asked for this run and nobody can answer it. Such a run never raises a card, a hand-over
# or a progress notice; a step that needs the owner's OK is skipped and the run goes on with what it has (the owner hears only its
# result, through the path that kind already has).
SELF_STARTED = frozenset({"recheck", "situation", "situation_select", "situation_write", "order_check", "checklist"})


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
        # (tool, site, card line) of the addresses the owner allowed in this reply: a second identical card for the same site is not
        # asked again in the same reply (Coupang, 2026-11-18: two opens of one site asked twice)
        self.opened_sites: set[tuple[str, str, str]] = set()
        self._cond = asyncio.Condition()
        self.task_id = 0                   # its record in the task log
        self.title = ""
        self.why = ""                      # the run's own reason for its cards (its record): one "Why" line, and the push says it
        self.reason = ""                   # the clause its one line before the first card gives ("Because <reason>, I am going to check ...")
        self.self_started = False          # Kelo started this run on its own (SELF_STARTED, or the caller says so): no cards, hand-overs or progress notices
        self.asks = RunAsks()              # what this run was allowed already (a site is not asked again in the run) and whether it said why
        self.source = "chat"               # chat | api | schedule:<id> | background
        self.unattended = False            # nobody is expected to watch: never stopped for want of a reader, approvals wait longer
        self.can_call = False              # the app this reply writes to can ring a call screen (call_tools.py)
        self.call_id = ""                  # the in-app call this reply speaks in (calls.py); "" for a typed reply
        self.owner_words = ""              # a call reply: the owner's spoken words, without the notes the model gets with them
        self.promise_checked = False       # the promise rule ran in this reply (promise_round): once per reply
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
        self.outage = False                # it failed because the provider was busy or unreachable (resume.is_outage), not because of the request
        self.limited = False               # it failed because the model's usage limit is used up (model_limits.py)
        self.limit_reset: float | None = None   # then: when the model may be called again
        self.goal_ids: set[int] = set()    # the goals it works on (goals.py "working": the app shows them as being worked on)
        self.goal_origin: set[int] = set() # the goals it was started for (a kickoff / resume): deleting one of them stops it
        self.reaction_msg = 0              # the owner's message it answers: 👀 now, its emoji when the work is over (reactions.py)
        self.owner_message = 0             # the owner's own message (author owner, owner_messages.py) this turn answers; 0 = none
        self.reaction_choice = ""          # the emoji the model chose with `react`
        self.asked = False                 # it ended on a question to the user (quick_replies, await_reply)
        self.evidence = RunEvidence()      # what its tool calls did (scheduled_runs.py): the closing phase and the promise rule read it
        self.defer_report = False          # a scheduled run's working phase: its end says nothing to the owner, the closing phase decides
        self.report = ""                   # ... and its private report (the text the working phase ended with)
        self.jobs: list[str] = []          # background jobs it handed work to (delegate)
        self.picked: set[str] = set()      # ids of the items the owner picked or attached for this message (data_trail.py decision 4)
        self.request = ""                  # the user's words that started it (its last user message's text)
        self.cond = ""                     # the conditional task a watcher's hit started it for ("watcher:<id>"; schedule_grants.py)
        self.owner_asked = False           # it carries the owner's request from the app (checklist items they added): connect_tools.asked_in_chat
        self.reacted = False               # the end reaction was sent (right before "done", or at the end of a failed reply)
        self.read_pending = False          # 👀 is due once the model starts answering (its first thinking, text or tool event)
        self.log: Any = None               # the turn log that writes its events down (turn_log.py), set by run_turn
        self.ack_sent = False              # code-level acknowledgment sent before slow work (backlog long-runs)

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
               stream_wrap: Callable[[Any], Any] | None = None, why: str = "", reason: str = "", can_call: bool = False,
               self_started: bool = False, owner_asked: bool = False) -> Turn:
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
    STOP_GRACE_SECONDS = S.STOP_GRACE_SECONDS
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
    CONTINUE_JOB_TITLE = S.CONTINUE_JOB_TITLE
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
    model_limits = S.model_limits
    one_run_tab = S.one_run_tab
    re = S.re
    readable_args = S.readable_args

    def _item_detail(req: ApprovalRequest) -> str:
        """A request's line on a shared card: its words, without the outside-text reason (that one is shown once, on "Why it asks")."""
        text = req.detail or readable_args(req.arguments)
        if req.why and text.startswith(req.why):
            text = text[len(req.why):].lstrip("\n")
        return text[:300]
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
    held_by = getattr(ctx, "held_by", None)   # the owner's hold of a tab (server: the browser or the phone is held by the owner)
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
    chat_views = getattr(ctx, "chat_views", None)   # the browser tabs and the phone the chat shows (chat_views.py)
    sweep()
    turn = Turn()
    turn.source, turn.unattended, turn.owner = source, unattended, owner or browser_owner.get()
    turn.self_started = bool(self_started or (unattended and source in SELF_STARTED))   # an attended turn never is
    turn.can_call = can_call   # the app the owner writes from can show a call screen (call_tools.py)
    turn.cond = cond
    turn.owner_asked = owner_asked
    last_user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "")
    earlier_request, own_title = "", bool(title)
    if not title and isinstance(last_user, str) and is_short_reply(last_user):   # "너가" to "제가 할까요?": name it after the request
        hidden = tuple(getattr(S, name) for name in ("HANDOFF_MARK", "WATCH_MARK", "GOAL_MARK", "SCHEDULE_MARK", "RECHECK_MARK", "NEST_SHARE_MARK", "CHECKLIST_MARK") if hasattr(S, name))
        earlier = [str(m.get("content") or "") for m in reversed(store.messages(limit=12)) if m.get("role") == "user"
                   and isinstance(m.get("content"), str) and not str(m.get("content")).startswith(hidden)]
        earlier = [e for e in earlier if e.strip() != last_user.strip()]
        title = task_title(last_user, earlier)
        earlier_request = next((e for e in earlier if not is_short_reply(e)), "")
    turn.title = (title or title_from(last_user if isinstance(last_user, str) else ""))[:80]
    turn.why = " ".join(why.split())[:200]
    turn.reason = " ".join(reason.split())[:200]
    # the user's own words for this turn (an order the agent starts without begin_order is read from them: tools/order.py); an offer's
    # run has none: its request is the agent's own wording, and the owner only tapped the card (offers.py)
    turn.request = "" if source == "offer" else (last_user if isinstance(last_user, str) else " ".join(
        str(p.get("text") or "") for p in (last_user or []) if isinstance(p, dict) and p.get("type") == "text"))[:2000]
    turn.task_id = activity.start_task(turn.title, source, turn.id)
    if (namer := getattr(ctx, "task_namer", None)) is not None and not own_title and isinstance(last_user, str):
        namer.name_later(turn.task_id, turn.request or last_user, earlier_request, source)   # a short name of the task, made in the background
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

    by_name: dict[str, Any] = {}   # the turn's tools by name (set below, before the first model pass)

    def track(item: dict[str, Any]) -> None:
        kind = item.get("type")
        said.feed(item)
        turn.evidence.feed(item, by_name)
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
        elif kind in ("browser", "device"):   # the phone card is a browser card for the Android device
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
    # the model's working folder (a provider sandbox path) in the reply's text becomes Kelo's own file name (reply_paths.py)
    reply_paths = ReplyPaths(getattr(ctx, "home_relativize", None))

    def show_view(item: dict[str, Any]) -> None:
        """A browser or phone card of a chat reply (or of a job a chat asked for) makes its view: it stays in the chat until closed."""
        if chat_views is None or item.get("type") not in ("browser", "device"):
            return
        parent = str((workers._row(turn.worker_id) or {}).get("parent_source") or "") if turn.worker_id else ""
        if not view_wanted(turn, parent):
            return
        if item["type"] == "device":
            chat_views.card("android", "phone", item, turn_id=turn.id, worker_id=turn.worker_id or None)
        else:
            chat_views.card(str(item.get("tab") or turn.owner), "browser", item, turn_id=turn.id, worker_id=turn.worker_id or None)

    ended_on_break = [False]   # the text shown last ended with a <message_break/>

    async def put(item: dict[str, Any], *, keep: bool = True) -> None:
        """`keep`: False for a line only shown (the ack): it reaches the screen but is never stored as what the reply said."""
        show_view(item)
        if item.get("type") == "text":
            text = reply_paths.feed(str(item.get("text", "")))
            if not text:
                return
            if ended_on_break[0]:   # two breaks in a row make an empty line: the second one goes
                text = _LEADING_BREAK.sub("", text, count=1)
                if not text:
                    return
            if text.strip():
                ended_on_break[0] = bool(_TRAILING_BREAK.search(text))
            item = {**item, "text": text}
        else:   # anything else comes after the text it follows: a path held back there is said first
            tail = reply_paths.flush()
            if tail:
                for it in repeats.feed({"type": "text", "text": tail}):
                    await deliver(it)
        for it in repeats.feed(item):
            await deliver(it, keep)

    async def deliver(item: dict[str, Any], keep: bool = True) -> None:
        if observe and keep:
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

    async def tell_progress(count: int = 0) -> None:
        """Backlog 66 / long runs: any run still working after 10 minutes sends one progress line in the path it already uses."""
        if turn.self_started:
            return   # nobody waits for this run: no progress notice
        if applied:
            followed = not turn.unattended and time.monotonic() - turn.touched < WATCHED_SECONDS
            if not followed:
                name = applied[0][0].get("task_name") or turn.title
                items = "; ".join(dict.fromkeys(w for _, w in applied if w))
                activity.notify("task_done", i18n.t(PROGRESS_TITLE, count=count or len(applied))[:120],
                                (f"{name} · {items}" if name else items)[:400], task_id=turn.task_id)
            return

        is_background = turn.unattended or bool(turn.worker_id)
        if is_background:
            proactive = getattr(ctx, "proactive", None)
            if proactive is not None and hasattr(proactive, "awake") and not proactive.awake():
                return

        if turn.worker_id and (r := workers._row(turn.worker_id)) is not None and r.get("status_text"):
            step_label = r["status_text"]
        else:
            step_label = turn.status_text or i18n.t("Thinking")

        if turn.worker_id and (r := workers._row(turn.worker_id)) is not None:
            step_count = max(int(r.get("steps") or 0), run_progress.count(run_key))
        else:
            step_count = count or run_progress.count(run_key)

        line = i18n.t(PROGRESS_NOTICE, step=step_label, count=step_count)
        try:
            if not is_background:
                await put({"type": "text", "text": f"{line}<message_break/>"})
                if time.monotonic() - turn.touched >= WATCHED_SECONDS:
                    activity.notify("task_done", line[:120], turn.title[:400], task_id=turn.task_id)
            else:
                activity.notify("task_done", line[:120], turn.title[:400], task_id=turn.task_id)
        except Exception:  # noqa: BLE001 - a notification never breaks the reply
            log.exception("could not tell how far the run got")

    async def finish() -> None:
        if turn.done:
            return
        tail = reply_paths.flush()   # a stopped reply still says what it had started to write (a held path)
        if tail:
            await put({"type": "text", "text": tail})
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
            if turn.worker_id and (prow := workers._row(turn.worker_id)) is not None and prow.get("parent_cond"):
                # the watcher or schedule that handed this job over: a checkout job can pay its one-shot purchase after that run has
                # ended, and then the grant must go with the job (2026-11-17). settle() drops only a count that was used up.
                schedule_grants.settle([str(prow["parent_cond"])])
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
        if chat_views is not None:   # this reply's cards go after the reply's own message (chat_views.py)
            try:
                row = store._all("SELECT MAX(id) AS m FROM messages WHERE role = 'assistant' AND id > ?", (turn.msg_floor,))[0]["m"]
                if row:
                    chat_views.anchor(turn.id, int(row))
            except Exception:  # noqa: BLE001
                log.exception("the chat views could not be anchored")
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

    async def settle(req: ApprovalRequest, ctx: dict[str, Any]) -> bool | None:
        """Everything before a card: the owner's own rules, the task's permission, a step nobody can answer. True / False = settled, no card;
        None = a card is needed, and `ctx` carries what that card reuses (card, group_card). Runs once per request: it logs and counts."""
        label = tool_labels.label(req.name)
        site = approval_site(req.name, req.arguments)
        kind, target = approval_edit.where_of(req.name, req.arguments, site)   # for the history: what kind of step, and to whom
        from .tools.browser import POINT_TOOLS, step_key
        clicks = ("browser_click", *POINT_TOOLS)
        sel = step_key(req.name, req.arguments)   # the selector, or "point:x,y" of a browser_click_at / browser_drag
        # a step whose target Kelo could not read at all (commit kind "unknown"): no rule ever allows it
        final_click = session is not None and req.name in (*clicks, "browser_type") and session.judged(sel) == "unknown"
        if not final_click and not req.force and (rule := activity.matching_rule(req.name, site)):
            # the user allowed this kind of step on this site before ("allow on this site"); payments never get here
            activity.log_approval(uuid.uuid4().hex, task_id=turn.task_id, tool=req.name, label=label, site=site, detail=req.detail,
                                  decision="allowed_by_rule", rule_id=rule["id"], unattended=turn.unattended, kind=kind, target=target)
            return True
        # a site the owner allowed a kind of step on earlier in this same run: not asked again in it (never for a step that cannot be remembered)
        run_kind, run_host = rule_kind(req.name), host_of(site)
        if not final_click and not req.force and turn.asks.allowed_before(run_kind, run_host):
            activity.log_approval(uuid.uuid4().hex, task_id=turn.task_id, tool=req.name, label=label, site=site, detail=req.detail,
                                  decision="allowed_this_run", unattended=turn.unattended, kind=kind, target=target)
            return True
        sched_id = int(turn.source[9:]) if turn.source.startswith("schedule:") and turn.source[9:].isdigit() else None
        if not final_click and not req.force and sched_id and activity.matching_schedule_rule(sched_id, req.name, site):
            # "keep allowing in this scheduled job": the same step on the same site in this schedule's runs; never payments
            activity.log_approval(uuid.uuid4().hex, task_id=turn.task_id, tool=req.name, label=label, site=site, detail=req.detail,
                                  decision="allowed_by_rule", unattended=turn.unattended, kind=kind, target=target)
            return True
        # a "when X happens, do Y" task the user allowed at setup (task_allow, schedule_grants.py): a message or a change inside
        # its permission goes ahead without a card (logged, and the user is told right after it ran); one outside it asks
        page_key = (req.name, site, req.detail or "")
        if req.name in URL_TOOLS and page_key in turn.opened_sites:   # allowed earlier in this reply: no second card for the same site
            return True
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
        if turn.self_started:   # nobody started this run and nobody can answer it: the step is skipped, never a card
            log.info("turn %s (%s): %s on %s skipped, it would need the owner's OK", turn.id, turn.source, req.name, site or "-")
            req.outcome = "skipped"
            return False
        ctx.update(label=label, site=site, kind=kind, target=target, commit=commit, final_click=final_click, run_kind=run_kind,
                   run_host=run_host, sched_id=sched_id, grant_check=grant_check, page_key=page_key)
        return None

    async def card(req: ApprovalRequest, ctx: dict[str, Any]) -> bool:
        """The card for one request (settle said it needs one): the owner's answer, or nobody's (skipped, expired)."""
        label, site, kind, target, commit = ctx["label"], ctx["site"], ctx["kind"], ctx["target"], ctx["commit"]
        final_click, run_kind, run_host, sched_id = ctx["final_click"], ctx["run_kind"], ctx["run_host"], ctx["sched_id"]
        grant_check, page_key = ctx["grant_check"], ctx["page_key"]
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
        notice_body = str(extra.pop("notice_body", "") or "")     # the notice's own text (a designed mail: its words, not the card's detail)
        if grant_check is not None and grant_check.reason:   # the task has a permission of this kind, but this step is outside it
            extra["lines"] = [{"label": i18n.t("Why it asks"), "value": i18n.t(GRANT_REASONS.get(grant_check.reason, grant_check.reason))},
                              *(extra.get("lines") or arg_lines(req.arguments))][:4]
        if turn.why:   # the run's own reason, from its record: the card says what it checks and why
            extra["lines"] = purpose_lines(turn.why, list(extra.get("lines") or arg_lines(req.arguments)))
        urgent, missed = schedule_grants.urgency() if turn.unattended else (False, "")
        if urgent:
            extra.update(urgent=True, missed=missed)
        issued_at = int(time.time())   # the moment the card is asked: a signed payment approval signs this (approval_sign.py)
        if turn.unattended and (said := turn.asks.announce(turn.reason or turn.title, site or label)):   # one line before the run's first card
            store.add_message("assistant", said)
        event = {"type": "approval_request", "approval_id": approval_id, "id": req.id, "name": req.name, "arguments": req.arguments,
                 "detail": req.detail or readable_args(req.arguments), "lines": arg_lines(req.arguments), "label": label,
                 "ask": tool_labels.ask(req.name, req.arguments), "site": site, "commit": commit,
                 "can_remember": bool(rule_kind(req.name) and site and not final_click and not req.force),
                 "can_remember_recipient": bool(req.trail), "trail": req.trail,
                 "can_remember_schedule": bool(sched_id and schedule_rule_ok(req.name) and not final_click and not req.force
                                               and not extra.get("payment")),
                 "schedule_id": sched_id, "task_id": turn.task_id, "expires_at": int(time.time() + timeout), "issued_at": issued_at,
                 **extra}
        if req.editable and not extra.get("payment") and (editable := approval_edit.editable(req.name, req.arguments)) is not None:   # a message to send: the owner may fix its text first
            event["editable"] = editable
        # source / worker_id / title: what asked (a chat reply, a background job, a scheduled run), so the chat can show the card too
        pending_approvals[approval_id] = {**event, "turn_id": turn.id, "asked_at": time.time(), "unattended": turn.unattended,
                                          "source": turn.source, "worker_id": turn.worker_id or None, "title": turn.title}
        activity.log_approval(approval_id, task_id=turn.task_id, tool=req.name, label=label, site=site, detail=req.detail,
                              unattended=turn.unattended, kind=kind, target=target)
        push_what = notice_body or req.detail or turn.title
        push_what = purpose_body(turn.why, turn.title, push_what) if not notice_body else push_what
        if turn.unattended and (urgent or extra.get("urgent")):   # time matters: an urgent notice that says what is missed without an answer
            until = datetime.fromtimestamp(time.time() + timeout, sched_zone(store.profile()["timezone"])).strftime("%H:%M")
            body = i18n.t("Answer by {time}, or {missed}", time=until, missed=missed) if missed else i18n.t("Answer by {time}", time=until)
            activity.notify("approval_waiting", notice_title[:120] or i18n.t("Urgent: please confirm {label}", label=label),
                            f"{body} · {push_what}"[:400], task_id=turn.task_id, approval_id=approval_id, urgent=True)
        elif turn.unattended:
            activity.notify("approval_waiting", i18n.t("Please confirm: {label}", label=label), push_what,
                            task_id=turn.task_id, approval_id=approval_id)
        turn.waiting += 1
        activity.set_task_status(turn.task_id, "waiting_approval")
        turn.status_text = i18n.t("Waiting for approval")
        await put(event)
        allowed = False
        try:
            allowed = await asyncio.wait_for(fut, timeout)
            if allowed:
                turn.asks.allow(run_kind, run_host)   # the same site, the same kind in this run: not asked again
            if not allowed:
                await stop_grace()
            elif (pending_approvals.get(approval_id) or {}).get("edited"):
                req.edited = True   # the owner fixed the message: the call re-checks its new text before it runs (agent/loop.py)
            if allowed and req.name in URL_TOOLS:
                turn.opened_sites.add(page_key)
            return allowed
        except asyncio.TimeoutError:
            req.outcome = "expired"   # the run is told nobody answered (not that the user declined)
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

    async def approve(req: ApprovalRequest) -> bool:
        ctx: dict[str, Any] = {}
        settled = await settle(req, ctx)
        return settled if settled is not None else await card(req, ctx)

    async def approve_many(reqs: list[ApprovalRequest]) -> list[bool]:
        """Answers for the calls of one step that share a card (Tool.shares_card): each settled as alone; the rest on one card when two or
        more need it (group_card), on their own card when one does."""
        ctxs: list[dict[str, Any]] = [{} for _ in reqs]
        answers: list[bool | None] = [await settle(r, c) for r, c in zip(reqs, ctxs)]
        left = [i for i, a in enumerate(answers) if a is None]
        if len(left) == 1:
            answers[left[0]] = await card(reqs[left[0]], ctxs[left[0]])
        elif left:
            for i, answer in zip(left, await group_card([reqs[i] for i in left], [ctxs[i] for i in left])):
                answers[i] = answer
        return [bool(a) for a in answers]

    async def group_card(reqs: list[ApprovalRequest], ctxs: list[dict[str, Any]]) -> list[bool]:
        """ONE card for several requests of one step: one approval id and one wait; the owner's answer applies to all of them. The card
        lists the changes (lines: at most four, then `items` with every one); the apps that know `steps` answer it as any card."""
        first, ctx = reqs[0], ctxs[0]
        approval_id, fut = broker.create()
        turn.approvals.add(approval_id)
        timeout = UNATTENDED_APPROVAL_SECONDS if turn.unattended else APPROVAL_TIMEOUT_SECONDS
        details = [_item_detail(r) for r in reqs]
        whys = list(dict.fromkeys(r.why for r in reqs if r.why))
        lines: list[dict[str, str]] = [{"label": i18n.t("Why it asks"), "value": "\n".join(whys)[:300]}] if whys else []
        for i, d in enumerate(details, 1):
            if len(lines) < 4:
                lines.append({"label": str(i), "value": d})
        issued_at = int(time.time())
        event: dict[str, Any] = {
            "type": "approval_request", "approval_id": approval_id, "id": first.id, "name": first.name, "arguments": {},
            "detail": "\n".join(details)[:300], "lines": lines, "label": ctx["label"], "ask": i18n.t("Allow these {count} changes?", count=len(reqs)),
            "site": ctx["site"], "commit": "", "can_remember": False, "can_remember_recipient": False, "trail": [],
            "can_remember_schedule": False, "schedule_id": ctx["sched_id"], "task_id": turn.task_id, "expires_at": int(time.time() + timeout),
            "issued_at": issued_at, "steps": [{"id": r.id, "name": r.name, "detail": d} for r, d in zip(reqs, details)],
        }
        if len(lines) - (1 if whys else 0) < len(details):
            event["items"] = details   # every change, when the lines above do not show them all
        pending_approvals[approval_id] = {**event, "turn_id": turn.id, "asked_at": time.time(), "unattended": turn.unattended,
                                          "source": turn.source, "worker_id": turn.worker_id or None, "title": turn.title}
        activity.log_approval(approval_id, task_id=turn.task_id, tool=first.name, label=ctx["label"], site=ctx["site"],
                              detail="\n".join(details), unattended=turn.unattended, kind=ctx["kind"], target=ctx["target"])
        turn.waiting += 1
        activity.set_task_status(turn.task_id, "waiting_approval")
        turn.status_text = i18n.t("Waiting for approval")
        await put(event)
        allowed = False
        try:
            allowed = bool(await asyncio.wait_for(fut, timeout))
            if not allowed:
                await stop_grace()
            return [allowed] * len(reqs)
        except asyncio.TimeoutError:
            for r in reqs:
                r.outcome = "expired"   # the run is told nobody answered (not that the user declined)
            activity.answer_approval(approval_id, "expired")
            return [False] * len(reqs)
        finally:
            broker.discard(approval_id)
            pending_approvals.pop(approval_id, None)
            activity.answer_approval(approval_id, "cancelled")
            activity.resolve_approval_notes(approval_id)
            turn.waiting -= 1
            if turn.waiting <= 0:
                activity.set_task_status(turn.task_id, "running")
            if not allowed:
                deliveries.discard(approval_id)
                pin_deliveries.discard(approval_id)

    async def hand_over(info: dict[str, Any], gone: Callable[[], Awaitable[bool]]) -> str:
        """A human check on the tab this reply works in: a pending item of kind human_check (the chat's live card, the Work page,
        a notification) while the browser step waits. It ends when the check leaves the page (`gone`), the user skips the site
        (POST /api/approvals/{id} approve=false; approve=true from an older app = "done"), it runs out, or the reply stops."""
        site = str(info.get("site") or "")
        seen = handover_seen.setdefault(turn.worker_id or turn.id, {})
        device = info.get("kind") == "device"   # the phone's live view (android_show, android/handover.py): the owner asked to look, no check
        if not device and (seen.get(site, 0) < 0 or seen.get(site, 0) >= HANDOVER_PER_SITE):
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
        elif device:
            label = i18n.t("Phone screen")
            title = i18n.t("Kelo needs you at the phone screen")
            detail = str(info.get("name") or "") or i18n.t("Use the phone screen and press the button when you are finished.")   # why, in the owner's language
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
        activity.notify("approval_waiting", title, detail if verifying or device else i18n.t("Solve it yourself and Kelo will carry on."),
                        task_id=turn.task_id, approval_id=approval_id)
        turn.waiting += 1
        turn.handing += 1
        activity.set_task_status(turn.task_id, "waiting_approval")
        turn.status_text = title if verifying or device else i18n.t("Waiting for you to confirm you are a person")
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
            if not device:
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

    async def stop_grace() -> None:
        """A card the user closed (or an approval they declined) is told to the model only after a short moment: the owner's stop
        button may follow at once (it closes the card, then stops the reply), and then the reply is cancelled right here and the model
        never reads "the user did not enter it / declined" as if it were their answer (2026-11-15: the last text of a stopped reply
        said "you did not enter the login, so I stopped"). A stop during this moment cancels the task: nothing is reported."""
        if not turn.unattended:   # nobody presses stop on a run nobody watches
            await asyncio.sleep(STOP_GRACE_SECONDS)

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
            state = await entries.wait(req["id"], ENTRY_WAIT_SECONDS)
            if state == "cancelled":
                await stop_grace()
            return state
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
    if tools_ is None and (settings.payment_specialist or settings.phone_worker or settings.browser_worker):   # the chat and every run on the app's own list: no payment tool, no phone tool, no acting browser tool (tool_roles.py)
        turn_tools = for_kind(turn_tools, None, pay_split=settings.payment_specialist, phone_split=settings.phone_worker, browser_split=settings.browser_worker)
    if turn.unattended and source.startswith("schedule:"):   # the working phase of a scheduled run cannot message the owner
        turn_tools = [t for t in turn_tools if not getattr(t, "reaches_owner", False)]
    by_name = {t.name: t for t in turn_tools}
    toolbox = turn_toolbox(tool_scope or scope_of(source))
    committed = False   # something reached the user that a second try would repeat or contradict: text, or a tool that changes things
    tokens = [0, 0, 0, 0, 0]   # input / output / cache-read / cache-write / reasoning tokens of every pass, finished or not: discarded ones were spent too

    def measured(n: int) -> None:
        compactor.last_request_tokens = n

    limited: list[StepLimit] = []   # the pass used its step budget (agent/loop.py StepLimit: its progress decides what follows)
    used_browser = [False]

    async def run_once(prov_: str, model_: str, thread: list[dict[str, Any]], *, stall_ok: bool = False,
                       first_pass_model: bool = False) -> tuple[dict[str, Any] | None, bool, bool]:
        """One pass of the agent loop; what it does streams to the client as it happens.
        Returns (its finish event, whether it wrote an answer, whether it did anything at all).
        With `stall_ok`, a pass that is going in circles through read-only tools (it repeats a call it already made, before
        writing anything or changing anything) is cut short: the caller can start over instead of waiting for it to give up."""
        nonlocal committed
        done_event: dict[str, Any] | None = None
        answered = acted = False
        built = context is not None and context.provider == prov_ and context.model == model_
        is_chat = turn.source in ("chat", "api") and not turn.unattended and not turn.worker_id
        start_mono = time.monotonic()
        saw_text = [False]
        first_tool_checked = [False]
        first_tool_slow = [False]

        def owner_holds() -> bool:
            try:
                return held_by is not None and held_by(turn.owner) is not None
            except Exception:  # noqa: BLE001 - not knowing whether the owner holds it: no ack either way is worse, so ack
                return False

        async def maybe_ack() -> None:
            if turn.done or not is_chat or not first_pass_model or getattr(turn, "ack_sent", False) or saw_text[0] or not first_tool_slow[0]:
                return
            if owner_holds():   # the owner holds this tab: "on it" would be a promise the step cannot keep; the step says it waits
                return
            if time.monotonic() - start_mono >= ACK_SECONDS:
                turn.ack_sent = True
                await put({"type": "text", "text": f"{i18n.t(ACK_LINE)}<message_break/>"}, keep=False)

        async def ack_timer() -> None:
            try:
                await asyncio.sleep(ACK_SECONDS)
                await maybe_ack()
            except asyncio.CancelledError:
                pass

        ack_task: asyncio.Task[None] | None = None
        if is_chat and first_pass_model and not getattr(turn, "ack_sent", False):
            ack_task = asyncio.create_task(ack_timer())

        agen = run_agent(prov_, model_, thread, turn_tools, approve=approve, approve_many=approve_many,
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
                    if not first_tool_checked[0]:
                        first_tool_checked[0] = True
                        first_tool_slow[0] = is_slow_tool(ev.name)
                    await maybe_ack()
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
                    saw_text[0] = True
                    if ack_task and not ack_task.done():
                        ack_task.cancel()
                    acted = answered = committed = True
                if isinstance(ev, ToolResult) and not ev.is_error and (tool := by_name.get(ev.name)) is not None and tool.ends_turn:
                    answered = True   # a job's final report ends its run: no answer text is due
                if isinstance(ev, ToolResult) and not ev.is_error and ev.name not in BOOKKEEPING:
                    run_progress.add(run_key)
                    await maybe_ack()
                if isinstance(ev, ToolResult) and (hit := covered_calls.pop((turn.id, ev.id), None)) is not None and not ev.is_error:
                    if hit[0].get("kind") == "apply":   # applications: one notice after the run (finish), not one per click
                        applied.append((hit[0], hit[2]))
                        if run_progress.due(run_key):
                            try:
                                await tell_progress(len(applied))
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
            if ack_task and not ack_task.done():
                ack_task.cancel()
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
            return thread, await run_once(prov_, model_, thread, stall_ok=True, first_pass_model=True)
        except ContextOverflow as e:
            learn_window(prov_, model_, e.tokens)
            if committed or context is None:
                raise
            await put({"type": "retry", "reason": "the conversation was too long for the model"})
            thread = await thread_for(prov_, model_)
            return thread, await run_once(prov_, model_, thread, stall_ok=True, first_pass_model=True)

    async def receipt_rounds(prov_: str, model_: str, thread: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Before the reply ends with a payment whose receipt no purchase_result closed: one more round, with a note to open the
        order page now. Once per payment; what is still unchecked after that is said at the end (finish). Returns that round's
        finish event (None: no round was made)."""
        if payment_ledger is None:
            return None
        last: dict[str, Any] | None = None
        while due := [p for p in payment_ledger.unchecked(turn.task_id) if not p.asked_receipt]:
            for p in due:
                p.asked_receipt = True
            note = RECEIPT_NOTE.format(site=", ".join(dict.fromkeys(p.host for p in due)))
            if toolbox is not None and "purchase_result" in by_name:
                toolbox.activate(["purchase_result"], by_name)   # the receipt check is shown in this round, whatever loaded before
            try:
                done_event, _, _ = await run_once(prov_, model_, [*thread, {"role": "system", "content": note}])
            except Exception:  # noqa: BLE001 - the reply already said its words; the end says what is still unchecked
                log.exception("the receipt round of turn %s failed", turn.id)
                break
            last = done_event or last
        return last

    async def promise_round(prov_: str, model_: str, thread: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The promise rule (scheduled_runs.py), before an attended reply ends (owner 2026-11-20): a later action the reply promised
        with nothing set up to do it gets one more round of this reply, to set up exactly that now and confirm it in one sentence (a
        missing detail: one question). It is the same reply, so a call speaks it and the chat shows one message. How the reply said it
        will respond from now on is no such promise; in a call those words are kept for the call's next turns (call_keep.py). Once per
        reply. Returns that round's finish event (None: no round)."""
        judge = getattr(ctx, "promise_judge", None)
        if (judge is None or turn.promise_checked or turn.source not in ("chat", "api") or turn.unattended or turn.worker_id
                or turn.evidence.kept_promise or stopping[0] or not said.text().strip()
                or not promise_check_needed(turn.waiting, pending_approvals.values(), turn.id)):
            return None
        turn.promise_checked = True
        try:
            verdict = await asyncio.wait_for(judge.judge(said.text(), turn.owner_words or turn.request, steps=said.timeline()),
                                             PROMISE_WAIT_S)
        except Exception:  # noqa: BLE001 - a check never breaks a reply (a slow one is skipped)
            log.info("turn %s: the promise check did not answer in time", turn.id)
            return None
        book = getattr(ctx, "call_book", None)
        if verdict.keeps and turn.call_id and book is not None:
            book.keep(turn.call_id, verdict.keeps)
        if not verdict.promise or said.lead_in_done(verdict.promise):
            return None
        log.info("turn %s: a later action was promised with nothing set up; one round to set it up", turn.id)
        try:
            done_event, _, _ = await run_once(prov_, model_, [*thread, {"role": "system",
                                                                         "content": PROMISE_ROUND_NOTE.format(quote=verdict.promise)}])
        except Exception:  # noqa: BLE001 - the reply already said its words
            log.exception("the promise round of turn %s failed", turn.id)
            return None
        return done_event

    async def pump() -> None:
        ui_sink.set(put)   # browser cards and other extra events from tools reach this reply (this task only)
        current_turn.set({"task_id": turn.task_id, "turn_id": turn.id, "source": turn.source, "unattended": turn.unattended,
                          "cond": turn.cond, "request": turn.request, "picked": sorted(turn.picked),
                          "owner_message": turn.owner_message, "can_call": turn.can_call,
                          "self_started": turn.self_started, "owner_asked": turn.owner_asked})
        browser_owner.set(turn.owner)
        # a chat reply's step on a tab the owner holds is told at once (browser tools); a job keeps the long wait
        hold_patience.set(CHAT_CONTROL_WAIT_SECONDS if turn.source in ("chat", "api") and not turn.unattended and not turn.worker_id else None)
        injection_watch_var.set(watch)
        approval_timeout.set(UNATTENDED_APPROVAL_SECONDS if turn.unattended else APPROVAL_TIMEOUT_SECONDS)
        handover.set(None if turn.self_started else hand_over)   # a human check on this reply's tab is handed to the user (BrowserSession.snapshot)
        entry_context.set((entries, None if turn.unattended else wait_entry))   # fill-in cards: a followed reply waits for the answer
        location_ask.set(None if turn.unattended else ask_location)   # my_location may ask the phone with a card (location.py)
        attempts = [(provider, model)]
        fb = fallback_model() if fallback else None
        if fb and provider_identity(fb[0]) != provider_identity(provider):   # a fallback on the same provider is no way out of its trouble
            attempts.append(fb)

        async def monitor_progress() -> None:
            while not turn.done:
                try:
                    await asyncio.sleep(PROGRESS_MONITOR_INTERVAL)
                    if not turn.done and run_progress.due(run_key):
                        await tell_progress()
                except asyncio.CancelledError:
                    break
                except Exception:
                    log.exception("progress monitor error")

        mon_task = asyncio.create_task(monitor_progress())
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
                    done_event = await receipt_rounds(prov_, model_, thread) or done_event
                    if done_event and answered and not limited:   # a promised later action nothing set up: set it up now (promise_round)
                        done_event = await promise_round(prov_, model_, thread) or done_event
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
                    turn.outage = is_outage(e)
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
            mon_task.cancel()
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
        existing = workers.open_job_for(turn.id, turn.task_id, turn.owner or "", turn.jobs) if turn.source != "handoff" else None
        if existing is not None and ev.progress >= STEP_LIMIT_PROGRESS:
            nxt, job = "background", existing["id"]   # a job of this reply (or on its tab or phone) carries on: never a second one
        elif ev.progress >= STEP_LIMIT_PROGRESS and "delegate" in by_name and turn.source != "handoff":
            asked = next((m["content"] for m in reversed(thread) if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
            said = "".join(answer).strip()[-2000:]
            task = (f"Carry on with the user's request from the chat; the chat reply ran out of steps. The user asked:\n{asked[:4000]}\n\n"
                    f"What the chat reply already did, in its own words:\n{said or '(it wrote nothing)'}\n\n"
                    "Do the items that are left, one after another; do not redo finished ones. Check what is already done before you "
                    "act when you are not sure. The permission the user gave for this task holds in this job too.")
            try:
                out = await workers.delegate({"task": task, "title": i18n.t(CONTINUE_JOB_TITLE, request=title_from(asked, 40)),
                                              "kind": "browser" if used_browser[0] and session is not None else "general"})
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
                     restarts: int = 0, resumed_from: int | None = None, cond: str = "", two_phase: bool = False) -> Turn:
    """A turn nobody watches (a schedule, a task handed over from the agent screen): the user's main model with the full prompt,
    the conversation as a chat turn sees it (summary + history, compacted inline if it must be), and `note` saying the user is
    away. Its answer is added to the conversation, led by `saved_as`; it never starts the after-turn fold itself.
    `cond`: the conditional task it works for ("watcher:<id>", a watcher's hit tried again): what the owner allowed that task
    covers its steps as it did on the first run (schedule_grants.current_scopes). `two_phase`: a scheduled run's working phase: its end
    says nothing to the owner (no chat message, no notification); `turn.report` keeps what it wrote and the caller's closing phase
    decides (scheduled_runs.py). Raises HTTPException when no model is set."""
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
            if two_phase:
                turn.report = collapse_repeat(said.text()) if said else ""   # private: the closing phase decides what the owner reads
            elif said and said.text().strip() != NO_REPLY and turn.task_id not in retried:   # NO_REPLY (or a retry asked for): nothing joins the chat
                store.add_message("assistant", f"{saved_as}\n" + collapse_repeat(said.text()))
            said.clear()
        else:
            said.feed(item)

    turn = start_turn(provider, model, ctx.tail, observe, source=source, title=title, unattended=True, owner=owner, context=ctx,
                      cond=cond)
    sid = source[9:] if source.startswith("schedule:") else ""
    turn.defer_report = two_phase
    turn.rerun = ({"kind": "schedule", "schedule_id": int(sid)} if sid.isdigit() else
                  {"kind": "background", "prompt": prompt[:8000], "title": title, "source": source, "owner": owner, "note": note,
                   "saved_as": saved_as, **({"cond": cond} if cond else {})})
    if (link := pending_links.pop(source, None)) is not None:   # an automatic run started again after a restart / outage
        resumed_from, restarts = link
    keep_resume(turn, None if source == "checklist" else dict(turn.rerun), restarts, resumed_from)   # the checklist just runs next time
    return turn
