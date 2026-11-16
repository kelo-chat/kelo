"""Schedules (schedules.py): list, create, change, delete and run now; archive, pause and run history come from schedule_routes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from ..schedules import ScheduleError, schedule_routes
from fastapi import APIRouter
from .context import AppContext


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    prompt: str = Field(min_length=1, max_length=2000)
    cron: str | None = Field(default=None, max_length=120)
    run_at: str | float | None = None
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    every_minutes: int | None = None
    window_minutes: int | None = None
    goal_id: int | None = None
    starts_on: str | None = Field(default=None, max_length=32)
    ends_on: str | None = Field(default=None, max_length=32)


class SchedulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    cron: str | None = Field(default=None, max_length=120)
    run_at: str | float | None = None
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    every_minutes: int | None = None
    window_minutes: int | None = None
    goal_id: int | None = None   # 0 takes it off its goal
    starts_on: str | None = Field(default=None, max_length=32)   # "" clears it
    ends_on: str | None = Field(default=None, max_length=32)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    app = ctx.app
    require_auth = ctx.require_auth
    schedule_grants = ctx.schedule_grants
    scheduler = ctx.scheduler
    schedules = ctx.schedules
    start_schedule_check = ctx.start_schedule_check
    turns = ctx.turns
    with_running = ctx.with_running

    # ---- schedules -------------------------------------------------------------------------------------------------------------
    def schedule_call(fn: Callable[[], Any]) -> Any:
        try:
            out = fn()
        except ScheduleError as e:
            raise HTTPException(422, str(e)) from None
        scheduler.poke()
        return out

    @router.get("/api/schedules", dependencies=[Depends(require_auth)])
    async def get_schedules(goal_id: int | None = None) -> dict[str, Any]:
        return {"items": [with_running(s) for s in schedules.list(goal_id)], "timezone": schedules.default_tz()}

    @router.post("/api/schedules", dependencies=[Depends(require_auth)], status_code=201)
    async def post_schedule(body: ScheduleIn) -> dict[str, Any]:
        s = schedule_call(lambda: schedules.create(body.name, body.prompt, cron=body.cron or None, run_at=body.run_at,
                                                   timezone=body.timezone or "", enabled=body.enabled,
                                                   every_minutes=body.every_minutes, window_minutes=body.window_minutes,
                                                   goal_id=body.goal_id, starts_on=body.starts_on, ends_on=body.ends_on))
        start_schedule_check(s)   # the agent checks ahead that it can run unattended (silent when all is ready)
        return with_running(s)

    schedule_routes(router, schedules, scheduler, activity, require_auth, schedule_grants)   # archive, restore, pause/resume, runs, rules (before /{sid})

    def need_schedule(sid: int) -> dict[str, Any]:
        s = schedules.get(sid)
        if s is None:
            raise HTTPException(404, "no such schedule")
        return s

    @router.get("/api/schedules/{sid}", dependencies=[Depends(require_auth)])
    async def get_schedule(sid: int) -> dict[str, Any]:
        return with_running(need_schedule(sid))

    @router.patch("/api/schedules/{sid}", dependencies=[Depends(require_auth)])
    async def patch_schedule(sid: int, body: SchedulePatch) -> dict[str, Any]:
        before = need_schedule(sid)
        s = schedule_call(lambda: schedules.update(sid, **body.model_dump()))
        # a new task or time (not the switch alone) goes to the agent again, to check ahead like a new schedule
        if s and any(s.get(k) != before.get(k) for k in ("name", "prompt", "description", "starts_on", "ends_on")):
            start_schedule_check(s, edited=True)
        return with_running(s)

    @router.delete("/api/schedules/{sid}", dependencies=[Depends(require_auth)])
    async def delete_schedule(sid: int, forever: bool = False) -> dict[str, bool]:
        """Into the archive (restorable for ARCHIVE_DAYS); `?forever=true`: deleted for good at once (the dialog's 삭제)."""
        need_schedule(sid)
        if forever:
            schedules.erase(sid)
        else:
            schedules.delete(sid)
        return {"ok": True}

    @router.post("/api/schedules/{sid}/run", dependencies=[Depends(require_auth)])
    async def run_schedule_now(sid: int) -> dict[str, Any]:
        s = need_schedule(sid)
        if not scheduler.start_run(s):
            raise HTTPException(409, "this schedule is already running")
        for _ in range(20):   # the run starts its turn on its first step: wait for that to say which task it is
            await asyncio.sleep(0)
            t = next((t for t in turns.values() if t.source == f"schedule:{sid}" and not t.done), None)
            if t is not None:
                return {"ok": True, "task_id": t.task_id, "turn_id": t.id}
        return {"ok": True, "task_id": None, "turn_id": None}
    return router
