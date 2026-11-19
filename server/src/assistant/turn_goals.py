"""Goals and turns (goals.py has the goal book): the agent starts on a new goal, the goals a turn works on, a schedule handed to the agent to check, checklist items handed to the agent for their timing, a schedule linked to a goal. Moved out of server.create_app (2026-11-10); each function takes the app context first."""

from __future__ import annotations

from collections.abc import Awaitable, Callable  # noqa: F401
from typing import TYPE_CHECKING, Any

from .turn_engine import Turn, _server  # noqa: F401

if TYPE_CHECKING:
    from .agent import Tool  # noqa: F401
    TurnContext = Any


def start_goal_work(ctx: Any, g: dict[str, Any], text: str) -> bool:
    """A turn of the main agent on goal `g` (kickoff or resume), answered in the chat like a job's report. False: no model.
    The goal shows as being worked on from now (also while the turn waits for a chat reply to end) until the turn is over."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    asyncio = S.asyncio
    i18n = S.i18n
    background_folds = ctx.background_folds
    goal_queued = ctx.goal_queued
    journal_model = ctx.journal_model
    worker_handoff = ctx.worker_handoff
    if journal_model() is None:
        return False
    task = asyncio.ensure_future(worker_handoff({"id": "", "title": g["title"][:80], "parent_task_id": None}, text, i18n.t("Your goal")))
    background_folds.add(task)   # keep a reference until it ends
    task.add_done_callback(background_folds.discard)
    goal_queued[task] = int(g["id"])
    task.add_done_callback(lambda t: goal_queued.pop(t, None))
    return True


def goals_of_turn(ctx: Any, source: str, text: str) -> set[int]:
    """The goals a reply starts out working on: the one its hidden goal message names (kickoff / resume), and, for a message the user
    wrote, the goals that were waiting for the user's answers (that message is most likely the answer). Its goal tools add more."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    GOAL_MARK = S.GOAL_MARK
    HANDOFF_MARK = S.HANDOFF_MARK
    NEST_SHARE_MARK = getattr(S, "NEST_SHARE_MARK", "")
    CHECKLIST_MARK = getattr(S, "CHECKLIST_MARK", "")
    RECHECK_MARK = S.RECHECK_MARK
    SCHEDULE_MARK = S.SCHEDULE_MARK
    WATCH_MARK = S.WATCH_MARK
    re = S.re
    goal_book = ctx.goal_book
    if text.startswith(GOAL_MARK):
        m = re.search(r"goal_id (\d+)", text) or re.search(r"#(\d+)", text)   # the kickoff / resume names it as "goal_id N"
        return {int(m.group(1))} if m else set()
    marks = tuple(m for m in (HANDOFF_MARK, WATCH_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK, CHECKLIST_MARK) if m)
    if source in ("chat", "api") and not text.startswith(marks):
        return goal_book.answering()
    return set()


def busy_goals(ctx: Any) -> set[int]:
    goal_queued = ctx.goal_queued
    turns = ctx.turns
    return {gid for t in turns.values() if not t.done for gid in t.goal_ids} | set(goal_queued.values())


def start_schedule_check(ctx: Any, s: dict[str, Any], *, edited: bool = False) -> bool:
    """A schedule the user saved in the app goes to the main agent, which checks it can run unattended (logins, connectors, phone
    permissions, unclear details): silent (NO_REPLY) when all is ready, else one question in the chat. False: no model."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    asyncio = S.asyncio
    i18n = S.i18n
    schedule_check_text = S.schedule_check_text
    background_folds = ctx.background_folds
    journal_model = ctx.journal_model
    schedule_check_on = ctx.schedule_check_on
    worker_handoff = ctx.worker_handoff
    if not schedule_check_on or journal_model() is None or not s.get("enabled", True):
        return False
    task = asyncio.ensure_future(worker_handoff({"id": "", "title": str(s["name"])[:80], "parent_task_id": None},
                                                schedule_check_text(s, edited=edited), i18n.t("Your schedule")))
    background_folds.add(task)
    task.add_done_callback(background_folds.discard)
    return True


def link_schedule(ctx: Any, goal_id: int, schedule_id: int) -> str:
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    ScheduleError = S.ScheduleError
    schedules = ctx.schedules
    if schedules.get(schedule_id) is None:
        return f"Error: there is no schedule #{schedule_id}"
    try:
        schedules.update(schedule_id, goal_id=goal_id)
    except ScheduleError as e:
        return f"Error: {e}"
    return f"Schedule #{schedule_id} now serves goal #{goal_id}."


def start_checklist_setup(ctx: Any, items: list[str], *, owner_asked: bool, added: bool | None = None) -> bool:
    """Checklist items go to the main agent, which gives each the timing it needs (checklist.py SETUP): a job of its own made with its
    tools, or the send times. owner_asked: the owner added them in the app, so Kelo's own settings it makes for them go ahead like a
    chat request (connect_tools.asked_in_chat). `added`: the items were just added (the setup text says so; default: as owner_asked).
    False: nothing to hand over, or no model."""
    S = _server()   # read at each call (tests tune some of these on assistant.server)
    asyncio = S.asyncio
    i18n = S.i18n
    setup_text = S.checklist_setup_text
    if not items or ctx.journal_model() is None:
        return False
    ctx.tool_scopes.add("chat", ["checklist_link", "event_reminders_set"])   # the hand-off shows these tools without a search first
    title = i18n.t("Things to keep an eye on")
    task = asyncio.ensure_future(ctx.worker_handoff({"id": "", "title": title, "parent_task_id": None},
                                                    setup_text(items, ctx.checklist_send_times(), added=owner_asked if added is None else added), title,
                                                    owner_asked=owner_asked))
    ctx.background_folds.add(task)
    task.add_done_callback(ctx.background_folds.discard)
    return True
