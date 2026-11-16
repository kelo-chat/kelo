"""Background jobs the agent hands off (workers.py has the jobs): the turns a job runs, a job handing its result back to the chat, the usage limit notices. Moved out of server.create_app (2026-11-10); each function takes the app context first."""

from __future__ import annotations

from collections.abc import Awaitable, Callable  # noqa: F401
from typing import TYPE_CHECKING, Any

from .reply_text import ReplyText
from .turn_engine import Turn, _server  # noqa: F401

if TYPE_CHECKING:
    from .agent import Tool  # noqa: F401
    TurnContext = Any


def start_worker_turn(ctx: Any, w: dict[str, Any], transcript: list[dict[str, Any]], steps: int, worker_tools: list[Tool],
                      observe: Callable[[dict[str, Any] | None], None]) -> Turn:
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    Callable = S.Callable
    Tool = S.Tool
    activity = ctx.activity
    resolve_model = ctx.resolve_model
    start_turn = ctx.start_turn
    store = ctx.store
    system_text = ctx.system_text
    provider, model = resolve_model(None, None)
    tz = store.profile()["timezone"]
    messages = [{"role": "system", "content": system_text(provider, model, tz)}, *transcript]
    turn = start_turn(provider, model, messages, observe, source="worker", title=w["title"], unattended=True, owner=w["tab"],
                      tools_=worker_tools, max_steps=steps, tool_scope=f"job:{w['id']}")
    turn.quiet_end = True
    turn.worker_id = w["id"]
    activity.link_task(turn.task_id, w["parent_task_id"], w["id"])
    return turn


async def worker_handoff(ctx: Any, w: dict[str, Any], text: str, head: str, quiet: bool = False, *, again: dict[str, Any] | None = None,
                         cond: str = "") -> None:
    """A job's report reaches the main conversation: stored as a hidden message (GET /api/messages leaves it out) and answered by the
    main agent in a turn of its own, one at a time and after a chat reply that is running.
    `again`: the same turn once more after a restart or a model outage ({"message_id", "note", "from", "restarts"}): the hidden
    message is already stored, the note says why it goes on."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    BACKGROUND_NOTE = S.BACKGROUND_NOTE
    HTTPException = S.HTTPException
    NO_REPLY = S.NO_REPLY
    asyncio = S.asyncio
    collapse_repeat = S.collapse_repeat
    i18n = S.i18n
    log = S.log
    secrets = S.secrets
    TurnContext = ctx.TurnContext
    activity = ctx.activity
    handoff_lock = ctx.handoff_lock
    keep_resume = ctx.keep_resume
    reactions = ctx.reactions
    resolve_model = ctx.resolve_model
    resume_context = ctx.resume_context
    running_turns = ctx.running_turns
    start_turn = ctx.start_turn
    stopping = ctx.stopping
    store = ctx.store
    workers = ctx.workers
    async with handoff_lock:
        for _ in range(600):
            if not any(t.source in ("chat", "api") for t in running_turns()):
                break
            await asyncio.sleep(1)
        try:
            provider, model = resolve_model(None, None)
        except HTTPException:
            return
        tz = store.profile()["timezone"]
        if again is None:
            mid = store.add_message("user", text)
            ctx = TurnContext(tz, [{"role": "user", "content": text}], mid)
        else:
            mid = int(again["message_id"])
            ctx = resume_context(mid, str(again["note"]))
            text = next((str(r["content"]) for r in store._all("SELECT content FROM messages WHERE id = ?", (mid,))), text)
        said = ReplyText()   # a stopped turn keeps only the answer it was writing (reply_text.py)

        def observe(item: dict[str, Any] | None) -> None:
            if item is None:
                reply = said.text().strip()
                if reply and reply != NO_REPLY:
                    store.add_message("assistant", collapse_repeat(reply))
                said.clear()
            else:
                said.feed(item)

        turn = start_turn(provider, model, ctx.tail, observe, source="handoff", title=w["title"], unattended=True, owner="owner",
                          context=ctx, cond=cond)
        # a retry of an approval that ran out (followups.py) starts this again: a watcher's hit keeps working for the watcher
        # (`cond`), so what the owner allowed it at setup still covers the step, as it did on the first run
        turn.rerun = {"kind": "background", "prompt": text[:8000], "title": w["title"], "source": "background",
                      "owner": f"task:{secrets.token_hex(4)}", "note": BACKGROUND_NOTE,
                      "saved_as": i18n.t("[Task handed over · {title}]", title=w["title"]), **({"cond": cond} if cond else {})}
        turn.note_head = head
        turn.quiet_end = quiet
        activity.link_task(turn.task_id, w["parent_task_id"], w["id"])
        keep_resume(turn, {"kind": "handoff", "message_id": mid, "title": w["title"], "head": head, "quiet": quiet,
                           "parent_task_id": w["parent_task_id"], "worker_id": w["id"], **({"cond": cond} if cond else {})},
                    int((again or {}).get("restarts") or 0), (again or {}).get("from"))
        await turn.ended.wait()
        if not stopping[0]:   # the job's work is over (unless it waits for the user): its messages get their emoji
            try:
                reactions.job_ended(w["id"], str((workers._row(w["id"]) or {}).get("status") or ""), turn.reaction_choice)
            except Exception:  # noqa: BLE001
                log.exception("reaction update failed")


async def worker_emit(ctx: Any, item: dict[str, Any], task_ids: list[int], turn_ids: list[str]) -> None:
    turns = ctx.turns
    for t in list(turns.values()):
        if not t.done and (t.task_id in task_ids or t.id in turn_ids):
            await t.emit(item)


def worker_window(ctx: Any) -> int:
    journal_model = ctx.journal_model
    window_for = ctx.window_for
    chosen = journal_model()
    return window_for(*chosen).context if chosen else 150_000


def delegated(ctx: Any, tid: str, wid: str) -> int:
    """A job was handed off: it may do what its task was allowed (task grants) and starts with the tools its conversation has
    loaded (the delegate call runs inside that conversation's run, whose ToolBox is current)."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    current_box = S.current_box
    schedule_grants = ctx.schedule_grants
    task_grants = ctx.task_grants
    tool_scopes = ctx.tool_scopes
    turn_scopes = ctx.turn_scopes
    turns = ctx.turns
    cur = current_box.get()
    if cur is not None and cur[0] is not None and cur[0].active:
        tool_scopes.add(f"job:{wid}", cur[0].active)
    if (parent := turns.get(tid)) is not None:
        parent.jobs.append(wid)   # its message keeps 👀 until the job ends (reactions.py)
        if (scopes := turn_scopes(parent)):
            schedule_grants.link_job(wid, scopes[0])   # a job a conditional task hands work to works under its setup permission
    return task_grants.inherit(f"turn:{tid}", f"job:{wid}") if tid else 0


def worker_limit_until(ctx: Any) -> float | None:
    """When the main model's usage hold ends (None = not held): a job paused by the limit waits at least this long."""
    journal_model = ctx.journal_model
    limit_guard = ctx.limit_guard
    main = journal_model()
    return limit_guard.until(main[0]) if main else None


def worker_limit_known(ctx: Any) -> bool:
    journal_model = ctx.journal_model
    limit_guard = ctx.limit_guard
    main = journal_model()
    return bool(main and (limit_guard.state(main[0]) or {}).get("known"))


def worker_limit_text(ctx: Any, until: float | None) -> str:
    """The paused job's line: when it goes on, only when the provider said when (an estimate is not promised); a balance that ran
    out says so (a top-up, not a time)."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    i18n = S.i18n
    model_limits = S.model_limits
    journal_model = ctx.journal_model
    limit_guard = ctx.limit_guard
    store = ctx.store
    worker_limit_known = ctx.worker_limit_known
    main = journal_model()
    if main and limit_guard.balance_out(main[0]):
        return i18n.t(model_limits.BALANCE_STOPPED, provider=model_limits.provider_name(main[0]))
    return model_limits.paused_text(until if until and worker_limit_known() else None, store.profile()["timezone"])


def worker_limit_notice(ctx: Any, row: dict[str, Any], until: float) -> bool:
    """ONE notice per paused job, inside the active hours (False = not now: asked again on the next tick)."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    i18n = S.i18n
    model_limits = S.model_limits
    activity = ctx.activity
    journal_model = ctx.journal_model
    limit_guard = ctx.limit_guard
    proactive = ctx.proactive
    worker_limit_text = ctx.worker_limit_text
    if not proactive.awake():
        return False
    main = journal_model()
    if main and limit_guard.balance_out(main[0]):
        name = model_limits.provider_name(main[0])
        activity.notify("task_failed", f"{i18n.t(model_limits.BALANCE_USED_UP, provider=name)}: {row.get('title') or ''}"[:120],
                        worker_limit_text(until), task_id=int(row["parent_task_id"]) if row.get("parent_task_id") else None)
        return True
    activity.notify("task_waiting", i18n.t(model_limits.DELAYED_HEAD, title=row.get("title") or "")[:120],
                    worker_limit_text(until), task_id=int(row["parent_task_id"]) if row.get("parent_task_id") else None)
    return True


async def worker_limit_tick(ctx: Any) -> list[str]:
    workers = ctx.workers
    return workers.limit_tick()
