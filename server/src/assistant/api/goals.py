"""Goals (goals.py): the list and each goal, the workspace and suggestion routes (goal_routes), and stopping a goal's work when it is deleted."""

from __future__ import annotations

import asyncio
from typing import Any
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from .. import i18n
from ..store import GOAL_STATUSES
from ..goals import goal_routes
from fastapi import APIRouter
from .context import AppContext


class GoalIn(BaseModel):
    title: str = Field(min_length=1)
    category: str = ""
    note: str = ""
    kickoff: bool = True


class GoalPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    category: str | None = None
    note: str | None = None
    status: str | None = None


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    app = ctx.app
    cancel_turn = ctx.cancel_turn
    goal_book = ctx.goal_book
    goal_queued = ctx.goal_queued
    goal_task_ids = ctx.goal_task_ids
    require_auth = ctx.require_auth
    scheduler = ctx.scheduler
    schedules = ctx.schedules
    start_goal_work = ctx.start_goal_work
    store = ctx.store
    turns = ctx.turns
    with_running = ctx.with_running
    workers = ctx.workers

    goal_routes(router, goal_book, require_auth, schedules_of=lambda gid: [with_running(s) for s in schedules.list(gid)],
                start_work=lambda g, text: start_goal_work(g, text))   # before the /api/goals/{id} routes below

    @router.get("/api/goals", dependencies=[Depends(require_auth)])
    async def get_goals(include: str | None = Query(None)) -> list[dict[str, Any]]:
        """The user's goals; `include=work` adds the work items Kelo carries for the user (an older app gets goals only)."""
        if include not in (None, "", "work"):
            raise HTTPException(422, "include must be work")
        return goal_book.listing(("goal", "work") if include == "work" else ("goal",))

    @router.post("/api/goals", dependencies=[Depends(require_auth)], status_code=201)
    async def post_goal(body: GoalIn) -> dict[str, Any]:
        g = goal_book.create(body.title, category=body.category, note=body.note)
        if body.kickoff:
            start_goal_work(g, goal_book.kickoff_text(g))
        return goal_book.public(store.goal(g["id"]) or g)

    @router.patch("/api/goals/{goal_id}", dependencies=[Depends(require_auth)])
    async def patch_goal(goal_id: int, body: GoalPatch) -> dict[str, Any]:
        if body.status is not None and body.status not in GOAL_STATUSES:
            raise HTTPException(422, f"status must be one of {', '.join(GOAL_STATUSES)}")
        goal = store.update_goal(goal_id, **body.model_dump(exclude_none=True))
        if goal is None:
            raise HTTPException(404, "no such goal")
        return goal_book.public(goal)

    async def stop_goal_work(goal_id: int, schedule_ids: set[int]) -> dict[str, int]:
        """Stop the work running for a goal that is being deleted: its kickoff / resume turns (also one still waiting its turn), the
        background jobs those turns started, and the running runs of `schedule_ids` (the linked schedules the user turns off with it).
        A chat reply the user wrote, or a turn that only touched the goal with a goal tool, is not stopped: it was not started for
        this goal alone (its goal tools then answer "no such goal"). Stopping a turn resolves its waiting approvals and hand-overs
        as cancelled (ask_approval / hand_over); this waits briefly for that so the app's next look shows them gone."""
        for fut, gid in list(goal_queued.items()):
            if gid == goal_id and not fut.done():
                fut.cancel()
        runs = {f"schedule:{sid}" for sid in schedule_ids}
        victims = [t for t in turns.values() if not t.done and (goal_id in t.goal_origin or t.source in runs)]
        task_ids = goal_task_ids.pop(goal_id, set()) | {t.task_id for t in victims if goal_id in t.goal_origin}
        for t in victims:
            cancel_turn(t)
        jobs = [w["id"] for tid in task_ids for w in workers.list(200, None, tid)
                if w["status"] in ("running", "needs_input", "blocked", "interrupted")]
        for wid in jobs:
            await workers.stop(wid)
        if victims:
            await asyncio.wait([asyncio.ensure_future(t.ended.wait()) for t in victims], timeout=5)
        return {"turns": len(victims), "jobs": len(jobs)}

    @router.delete("/api/goals/{goal_id}", dependencies=[Depends(require_auth)])
    async def remove_goal(goal_id: int, schedules_: str = Query("keep", alias="schedules")) -> dict[str, Any]:
        """`schedules`: keep (the default, also for older apps: the linked schedules keep running, taken off the goal) | delete (every
        linked schedule goes to the archive with the goal, restorable there; a run of theirs in progress is stopped). `pause` is what
        apps before 0.9.4 send for "turn them off too": it deletes as well. The goal's own work in progress always stops. Its smaller
        goals (one level) go with it, with the same choice for their schedules; `deleted_goals` lists them."""
        if schedules_ not in ("keep", "delete", "pause"):
            raise HTTPException(422, "schedules must be keep or delete")
        if store.goal(goal_id) is None:
            raise HTTPException(404, "no such goal")
        children = [int(c["id"]) for c in store._all("SELECT id FROM goals WHERE parent_id = ? ORDER BY id", (goal_id,))]
        out: dict[str, Any] = {"ok": True, "stopped_turns": 0, "stopped_jobs": 0, "deleted_schedules": [], "kept_schedules": [],
                               "deleted_goals": children}
        for gid in [*children, goal_id]:
            linked = [int(s["id"]) for s in schedules.list(gid)]
            deleted = linked if schedules_ != "keep" else []
            stopped = await stop_goal_work(gid, set(deleted))
            for sid in deleted:
                schedules.delete(sid)
            if deleted:
                scheduler.poke()
            if not store.delete_goal(gid):
                raise HTTPException(404, "no such goal")
            goal_book.delete(gid)
            out["stopped_turns"] += stopped["turns"]
            out["stopped_jobs"] += stopped["jobs"]
            out["deleted_schedules"] += deleted
            out["kept_schedules"] += [sid for sid in linked if sid not in deleted]
        return out

    return router
