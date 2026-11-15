"""Going on after a restart or a model outage (resume.py has the rules and words): Continue offers, chat and background turns resumed after a restart, waiting for a model that is down or at its usage limit. Moved out of server.create_app (2026-11-10); each function takes the app context first."""

from __future__ import annotations

from collections.abc import Awaitable, Callable  # noqa: F401
from typing import TYPE_CHECKING, Any

from .reply_text import ReplyText
from .turn_engine import Turn, _server  # noqa: F401

if TYPE_CHECKING:
    from .agent import Tool  # noqa: F401
    TurnContext = Any


def last_user_id(ctx: Any) -> int:
    """The user's own newest message (hidden handoff / goal / schedule messages are not theirs)."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    GOAL_MARK = S.GOAL_MARK
    HANDOFF_MARK = S.HANDOFF_MARK
    RECHECK_MARK = S.RECHECK_MARK
    SCHEDULE_MARK = S.SCHEDULE_MARK
    WATCH_MARK = S.WATCH_MARK
    store = ctx.store
    marks = (HANDOFF_MARK, WATCH_MARK, GOAL_MARK, SCHEDULE_MARK, RECHECK_MARK)
    return next((int(r["id"]) for r in store._all("SELECT id, content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 50")
                 if not str(r["content"]).startswith(marks)), 0)


def resume_context(ctx: Any, message_id: int, note: str) -> TurnContext:
    """A turn again from the saved conversation. When the message it answered is still the last one, that message with the note
    added; when something came after it (the part of the answer said before the cut), the whole conversation and the note."""
    TurnContext = ctx.TurnContext
    store = ctx.store
    tz = store.profile()["timezone"]
    rows = store._all("SELECT id, content FROM messages WHERE id = ?", (message_id,))
    later = store._all("SELECT 1 FROM messages WHERE id > ? LIMIT 1", (message_id,))
    if rows and not later:
        return TurnContext(tz, [{"role": "user", "content": f"{rows[0]['content']}\n\n{note}"}], message_id)
    return TurnContext(tz, [{"role": "user", "content": note}], None)


def use_offer(ctx: Any, message_id: int) -> None:
    """A Continue button did its work: it goes, from the list and from under its message."""
    activity = ctx.activity
    store = ctx.store
    activity.close_offer(message_id)
    store._run("DELETE FROM message_replies WHERE message_id = ?", (message_id,))


def plain_note(ctx: Any, note: str) -> str:
    return note.strip().removeprefix("[").removesuffix("]").removeprefix("Not from the user: ")


def offer_continue(ctx: Any, row: dict[str, Any], why: str) -> str:
    """The user's own chat turn is not run again behind their back: the stopped line with a Continue button, unless the user
    has written since (they moved on)."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    CONTINUE_LABEL = S.CONTINUE_LABEL
    MODEL_DOWN_LINE = S.MODEL_DOWN_LINE
    RESTART_LINE = S.RESTART_LINE
    continue_buttons = S.continue_buttons
    i18n = S.i18n
    activity = ctx.activity
    goal_book = ctx.goal_book
    last_user_id = ctx.last_user_id
    store = ctx.store
    spec = row["spec"]
    if last_user_id() > int(spec.get("message_id") or 0):
        activity.resolve_resume(row["id"], "skipped")
        return "skipped"
    label = i18n.t(CONTINUE_LABEL)
    mid = store.add_message("assistant", i18n.t(RESTART_LINE if why == "restart" else MODEL_DOWN_LINE))
    goal_book.attach_replies(mid, continue_buttons(label), None)
    activity.add_offer(mid, row["id"], why, label)
    activity.resolve_resume(row["id"], "offered")
    return "offered"


def continue_chat(ctx: Any, row: dict[str, Any], restarts: int) -> str:
    """The model is back: a chat turn that stopped for it goes on by itself, unless the user wrote something newer than its
    Continue button (then the button, or their new message, is the way on)."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    MODEL_NOTE = S.MODEL_NOTE
    collapse_repeat = S.collapse_repeat
    i18n = S.i18n
    TurnContext = ctx.TurnContext
    activity = ctx.activity
    keep_resume = ctx.keep_resume
    last_user_id = ctx.last_user_id
    resolve_model = ctx.resolve_model
    start_turn = ctx.start_turn
    store = ctx.store
    use_offer = ctx.use_offer
    offer = activity.offer_for_task(row["id"])
    if offer is None or last_user_id() > int(offer["message_id"]):
        activity.resolve_resume(row["id"], "skipped", status="interrupted", result=i18n.t("Went on once the AI model was back"))
        return "skipped"
    use_offer(int(offer["message_id"]))
    provider, model = resolve_model(None, None)
    ctx = TurnContext(store.profile()["timezone"], [{"role": "user", "content": S.with_language(MODEL_NOTE, i18n.owner())}], None)
    said = ReplyText()   # a stopped turn keeps only the answer it was writing (reply_text.py)

    def observe(item: dict[str, Any] | None) -> None:
        if item is None:
            if said:
                store.add_message("assistant", collapse_repeat(said.text()))
            said.clear()
        else:
            said.feed(item)

    turn = start_turn(provider, model, ctx.tail, observe, source="chat", title=row["title"], context=ctx)
    keep_resume(turn, dict(row["spec"]), restarts, row["id"])
    turn.reaction_msg = int(row["spec"].get("message_id") or 0)   # the same message, still 👀
    return "resumed"


def resume_task(ctx: Any, row: dict[str, Any], why: str) -> str:
    """Start task `row` (activity.resume_row) again; why: restart | model. Returns resumed | offered | skipped | failed."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    BACKGROUND_NOTE = S.BACKGROUND_NOTE
    CATCH_UP_SECONDS = S.CATCH_UP_SECONDS
    HTTPException = S.HTTPException
    MAX_RESTART_RESUMES = S.MAX_RESTART_RESUMES
    MODEL_NOTE = S.MODEL_NOTE
    RESTART_NOTE = S.RESTART_NOTE
    asyncio = S.asyncio
    i18n = S.i18n
    log = S.log
    secrets = S.secrets
    time = S.time
    activity = ctx.activity
    background_folds = ctx.background_folds
    continue_chat = ctx.continue_chat
    journal_model = ctx.journal_model
    limit_wait = ctx.limit_wait
    offer_continue = ctx.offer_continue
    pending_links = ctx.pending_links
    plain_note = ctx.plain_note
    running_turns = ctx.running_turns
    scheduler = ctx.scheduler
    schedules = ctx.schedules
    start_background = ctx.start_background
    worker_handoff = ctx.worker_handoff
    spec, tid = row.get("spec") or {}, int(row["id"])
    kind = spec.get("kind")
    restarts = int(row.get("restarts") or 0) + (1 if why == "restart" else 0)
    note = S.with_language(RESTART_NOTE if why == "restart" else MODEL_NOTE, i18n.owner())
    went_on = i18n.t("Went on once the AI model was back") if why == "model" else None
    if kind not in ("chat", "handoff", "background", "schedule") or row.get("source") in ("worker", "api", "checklist"):
        activity.resolve_resume(tid, "skipped")   # a job's own turns: workers.py resumes the job; the rest just runs next time
        return "skipped"
    if why == "restart" and restarts > MAX_RESTART_RESUMES:
        msg = i18n.t("Stopped for good: the server restarted {n} times while this was running", n=restarts)
        activity.resolve_resume(tid, "failed", status="failed", result=msg, seen=False)
        activity.notify("task_failed", f"{i18n.t('It did not work out')}: {row['title']}"[:120], msg, task_id=tid)
        log.warning("task %s not resumed: the server restarted %d times while it ran", tid, restarts)
        return "failed"
    if kind == "chat":
        return offer_continue(row, why) if why == "restart" else continue_chat(row, restarts)
    if kind == "schedule":
        sid = int(spec.get("schedule_id") or 0)
        s = schedules.get(sid)
        late = time.time() - float(row["started_at"]) > CATCH_UP_SECONDS and not limit_wait(row)   # one held by the usage limit runs late
        if s is None or not s.get("enabled", True) or late:
            activity.resolve_resume(tid, "skipped", status="interrupted")   # gone, off, or too late: its next time comes as usual
            return "skipped"
        pending_links[f"schedule:{sid}"] = (tid, restarts)
        if not scheduler.start_run(s, note=plain_note(note)):
            pending_links.pop(f"schedule:{sid}", None)
            activity.resolve_resume(tid, "skipped", status="interrupted")   # it is running already
            return "skipped"
        activity.resolve_resume(tid, "resuming", status="interrupted", result=went_on)
        return "resumed"
    if journal_model() is None:
        return "skipped"   # no model set: left pending for the next start
    activity.resolve_resume(tid, "resuming", status="interrupted", result=went_on)   # not picked up twice while it waits to start
    if kind == "handoff":
        w = {"id": str(spec.get("worker_id") or ""), "title": str(spec.get("title") or row["title"])[:80],
             "parent_task_id": spec.get("parent_task_id")}
        task = asyncio.ensure_future(worker_handoff(w, "", str(spec.get("head") or ""), bool(spec.get("quiet")),
                                                    again={"message_id": spec["message_id"], "note": note, "from": tid,
                                                           "restarts": restarts}, cond=str(spec.get("cond") or "")))
        background_folds.add(task)
        task.add_done_callback(background_folds.discard)
        return "resumed"
    owner = str(spec.get("owner") or "")
    if not owner or owner == "owner" or any(t.owner == owner for t in running_turns()):
        owner = f"task:{secrets.token_hex(4)}"
    try:
        start_background(str(spec.get("prompt") or ""), title=str(spec.get("title") or row["title"]),
                         source=str(spec.get("source") or "background"), owner=owner, note=str(spec.get("note") or BACKGROUND_NOTE),
                         saved_as=str(spec.get("saved_as") or ""), extra_note=note, restarts=restarts, resumed_from=tid,
                         cond=str(spec.get("cond") or ""))
    except HTTPException:
        activity.resolve_resume(tid, "pending")
        return "skipped"
    return "resumed"


def resume_after_restart(ctx: Any) -> dict[int, str]:
    """At start-up: every task the last shutdown (or crash) cut off goes on, gets a Continue button, or is left as it is."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    log = S.log
    activity = ctx.activity
    resume_task = ctx.resume_task
    out: dict[int, str] = {}
    for row in activity.pending_resumes():
        try:
            out[row["id"]] = resume_task(row, "restart")
        except Exception:  # noqa: BLE001 - one task never keeps the server from starting
            log.exception("task %s could not be resumed after the restart", row["id"])
    if out:
        log.info("after the restart: %s", ", ".join(f"task {k} {v}" for k, v in out.items()))
    return out


async def model_reachable(ctx: Any) -> bool:
    """A light check: the main model (else the fallback) answers a one-word request."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    ChatRequest = S.ChatRequest
    Done = S.Done
    TextDelta = S.TextDelta
    asyncio = S.asyncio
    log = S.log
    counted = ctx.counted
    fallback_model = ctx.fallback_model
    journal_model = ctx.journal_model
    key_args = ctx.key_args
    chosen = [m for m in (journal_model(), fallback_model()) if m]
    for provider, model in chosen:
        req = ChatRequest(model=model, messages=[{"role": "user", "content": "Reply with the single word OK."}], tools=[],
                          max_tokens=16, extra={})

        async def ask(p: str = provider, r: ChatRequest = req) -> bool:
            agen = counted("probe")(p, r, **key_args(p))
            try:
                async for ev in agen:
                    if isinstance(ev, (TextDelta, Done)):
                        return True
                return True
            finally:
                await agen.aclose()
        try:
            if await asyncio.wait_for(ask(), 60):
                return True
        except Exception as e:  # noqa: BLE001
            log.info("model check of %s failed: %s", provider, type(e).__name__)
    return False


def limit_wait(ctx: Any, row: dict[str, Any]) -> bool:
    """The task waits because the model's usage limit is used up (not because the model is down)."""
    return str(row.get("error") or "").startswith("QuotaExhausted")


def limit_urgent(ctx: Any, row: dict[str, Any]) -> bool:
    """A held run whose time matters: a one-time scheduled run (a reminder, a booking) or a run allowed to buy on its own."""
    schedule_grants = ctx.schedule_grants
    schedules = ctx.schedules
    spec = row.get("spec") or {}
    if spec.get("kind") != "schedule":
        return False
    sid = int(spec.get("schedule_id") or 0)
    s = schedules.get(sid) or {}
    return s.get("kind") == "once" or any(g["kind"] == "purchase" or g["urgent"] for g in schedule_grants.list(sid))


def limit_notices(ctx: Any, waits: list[dict[str, Any]], now: float) -> None:
    """Important work held by the usage limit: ONE notification each, saying when it will run, inside the learned active hours
    (a time-bound one at once). A chat turn needs none (its reply said it), nor a run of a schedule whose newer run waits too."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    i18n = S.i18n
    model_limits = S.model_limits
    activity = ctx.activity
    journal_model = ctx.journal_model
    limit_guard = ctx.limit_guard
    limit_urgent = ctx.limit_urgent
    limit_wait = ctx.limit_wait
    proactive = ctx.proactive
    store = ctx.store
    main = journal_model()
    if main and limit_guard.balance_out(main[0]):
        return   # a balance that ran out: the waiting work stops with its own words (model_wait_tick), no "it will run" notice
    held = [r for r in waits if limit_wait(r)]
    newest: dict[str, int] = {}
    for r in held:
        spec = r.get("spec") or {}
        if spec.get("kind") == "schedule":
            key = f"schedule:{spec.get('schedule_id')}"
            if key in newest and newest[key] != int(r["id"]):   # an older run of the same schedule: the newer one stands for it
                old = next(x for x in held if int(x["id"]) == newest[key])
                activity.resolve_resume(int(old["id"]), "skipped", status="interrupted", result=i18n.t(model_limits.USED_UP))
                if old.get("wait_noted"):
                    activity.bump_wait(int(r["id"]), float(r.get("retry_at") or now), noted=True)
                    r["wait_noted"] = 1
            newest[key] = int(r["id"])
    live = {int(x) for x in newest.values()}
    main = journal_model()
    until = limit_guard.until(main[0]) if main else None
    for r in held:
        spec = r.get("spec") or {}
        if r.get("wait_noted") or spec.get("kind") == "chat" or (spec.get("kind") == "schedule" and int(r["id"]) not in live):
            continue
        if not (limit_urgent(r) or proactive.awake(now)):
            continue   # quiet time: told once the day starts, unless it has run by then
        reset = until or (float(r["retry_at"]) if r.get("retry_at") else None)
        known = bool(main and (limit_guard.state(main[0]) or {}).get("known"))
        activity.notify("task_waiting", i18n.t(model_limits.DELAYED_HEAD, title=r["title"])[:120],
                        model_limits.delayed_text(reset if known else None, store.profile()["timezone"], now), task_id=int(r["id"]),
                        schedule_id=int(spec["schedule_id"]) if spec.get("kind") == "schedule" and spec.get("schedule_id") else None)
        activity.bump_wait(int(r["id"]), float(r.get("retry_at") or now), noted=True)


async def model_wait_tick(ctx: Any, now: float | None = None) -> dict[int, str]:
    """Tasks waiting for the AI model: when one is due, check the model once; back = they go on, still down = the next check later
    (1, 5, 15, then every 60 minutes), a word to the user after 6 hours, failed after 48. A task held by the usage limit is first
    checked at the reset (the provider is not called before it), its user hears once when it will run, and it only gives up after
    model_limits.MAX_HOLD."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    MODEL_WAIT_GIVE_UP = S.MODEL_WAIT_GIVE_UP
    MODEL_WAIT_NOTIFY_AFTER = S.MODEL_WAIT_NOTIFY_AFTER
    i18n = S.i18n
    log = S.log
    model_limits = S.model_limits
    next_wait = S.next_wait
    time = S.time
    activity = ctx.activity
    journal_model = ctx.journal_model
    limit_guard = ctx.limit_guard
    limit_notices = ctx.limit_notices
    limit_wait = ctx.limit_wait
    model_reachable = ctx.model_reachable
    resume_task = ctx.resume_task
    now = time.time() if now is None else now
    waits = activity.model_waits()
    try:
        limit_notices(waits, now)
    except Exception:  # noqa: BLE001 - a notice never keeps the waiting tasks from going on
        log.exception("usage limit notices failed")
    out: dict[int, str] = {}
    main = journal_model()
    if main and limit_guard.balance_out(main[0]):
        # the prepaid balance ran out: nothing resets it, so the work held by it stops now with plain words (no model check, no
        # retries on a timer); the owner tops up and asks again
        name = model_limits.provider_name(main[0])
        msg = i18n.t(model_limits.BALANCE_STOPPED, provider=name)
        for r in [x for x in activity.model_waits() if limit_wait(x)]:
            tid = int(r["id"])
            activity.resolve_resume(tid, "failed", status="failed", result=msg, seen=False)
            if (r.get("spec") or {}).get("kind") != "chat":   # a chat reply already said it
                activity.notify("task_failed", f"{i18n.t(model_limits.BALANCE_USED_UP, provider=name)}: {r['title']}"[:120], msg, task_id=tid)
            out[tid] = "failed"
        return out
    due = [r for r in activity.model_waits() if (r.get("retry_at") or 0) <= now]
    if not due:
        return out
    up = await model_reachable()
    for r in due:
        tid = int(r["id"])
        try:
            if up:
                out[tid] = resume_task(r, "model")
                continue
            since = float(r.get("wait_since") or now)
            main = journal_model()
            held_until = limit_guard.until(main[0]) if main else None
            if limit_wait(r) or held_until:   # the usage limit: wait for its reset, no "not answered for hours" notice
                if now - since >= model_limits.MAX_HOLD:
                    msg = i18n.t("Failed: {error}", error=i18n.t(model_limits.USED_UP))
                    activity.resolve_resume(tid, "failed", status="failed", result=msg, seen=False)
                    activity.notify("task_failed", f"{i18n.t('It did not work out')}: {r['title']}"[:120], msg, task_id=tid)
                    out[tid] = "failed"
                    continue
                activity.bump_wait(tid, held_until or now + next_wait(int(r.get("waits") or 0) + 1))
                out[tid] = "waiting"
                continue
            if now - since >= MODEL_WAIT_GIVE_UP:
                msg = i18n.t("Failed: {error}", error=r.get("error") or i18n.t("the AI model could not be reached"))
                activity.resolve_resume(tid, "failed", status="failed", result=msg, seen=False)
                activity.notify("task_failed", f"{i18n.t('It did not work out')}: {r['title']}"[:120], msg, task_id=tid)
                out[tid] = "failed"
                continue
            tell = now - since >= MODEL_WAIT_NOTIFY_AFTER and not r.get("wait_noted")
            if tell:
                activity.notify("task_waiting", f"{i18n.t('Waiting for the AI model to come back')}: {r['title']}"[:120],
                                i18n.t("The AI model has not answered for hours. The task goes on by itself once it is back."),
                                task_id=tid)
            activity.bump_wait(tid, now + next_wait(int(r.get("waits") or 0) + 1), noted=tell)
            out[tid] = "waiting"
        except Exception:  # noqa: BLE001
            log.exception("task %s could not go on after the model came back", tid)
    return out
