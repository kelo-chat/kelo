"""What conditional tasks and background jobs may do on their own (schedule_grants.py, tools/task_permission.py): list and revoke."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, HTTPException
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    schedule_grants = ctx.schedule_grants
    watchers = ctx.watchers
    workers = ctx.workers

    # ---- what conditional tasks may do on their own (schedule_grants.py; contracts with SCHEDULES above)
    @router.get("/api/watchers/{wid}/grants", dependencies=[Depends(require_auth)])
    async def watcher_grants(wid: int) -> dict[str, Any]:
        if watchers.get(wid) is None:
            raise HTTPException(404, "no such watcher")
        return {"items": schedule_grants.list(scope=f"watcher:{wid}")}

    @router.delete("/api/watchers/{wid}/grants/{gid}", dependencies=[Depends(require_auth)])
    async def revoke_watcher_grant(wid: int, gid: int) -> dict[str, bool]:
        if not schedule_grants.revoke(gid, scope=f"watcher:{wid}"):
            raise HTTPException(404, "no such permission")
        return {"ok": True}

    @router.get("/api/workers/{wid}/grants", dependencies=[Depends(require_auth)])
    async def worker_grants(wid: str) -> dict[str, Any]:
        if workers.get(wid) is None:
            raise HTTPException(404, "no such job")
        return {"items": schedule_grants.list(scope=f"job:{wid}")}

    @router.delete("/api/workers/{wid}/grants/{gid}", dependencies=[Depends(require_auth)])
    async def revoke_worker_grant(wid: str, gid: int) -> dict[str, bool]:
        if not schedule_grants.revoke(gid, scope=f"job:{wid}"):
            raise HTTPException(404, "no such permission")
        return {"ok": True}

    @router.get("/api/task-grants", dependencies=[Depends(require_auth)])
    async def all_task_grants() -> dict[str, Any]:
        return {"items": schedule_grants.list()}

    @router.delete("/api/task-grants/{gid}", dependencies=[Depends(require_auth)])
    async def revoke_task_grant(gid: int) -> dict[str, bool]:
        if not schedule_grants.revoke(gid):
            raise HTTPException(404, "no such permission")
        return {"ok": True}

    return router
