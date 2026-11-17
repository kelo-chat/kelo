"""Forget on request (forget.py, docs/forget.md): what Kelo keeps out of its memory, and the list of a waiting cleanup plan.

GET    /api/forget                {"items": [{"id", "label", "created_at", "removed": {category: n}}]}   the topics kept out of memory
DELETE /api/forget/{id}           {"ok"}   Kelo may write about that topic again (404 unknown id)
GET    /api/forget/plans/{id}     {"id", "label", "items": [{"category", "show"}]}   the waiting plan's list (404 when it is gone or too old)

The cleanup itself is two agent tools (forget_plan, forget_confirm) and the owner's card in the chat; these routes only let an app show
the list and the topics. Categories: memory, notes, reminders, skills, history.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    forget = ctx.forget
    tombstones = ctx.tombstones

    @router.get("/api/forget", dependencies=[Depends(require_auth)])
    async def list_forgotten() -> dict[str, Any]:
        return {"items": tombstones.public()}

    @router.delete("/api/forget/{tid}", dependencies=[Depends(require_auth)])
    async def allow_again(tid: str) -> dict[str, bool]:
        if not tombstones.remove(tid):
            raise HTTPException(404, "no such forgotten topic")
        return {"ok": True}

    @router.get("/api/forget/plans/{pid}", dependencies=[Depends(require_auth)])
    async def forget_plan_view(pid: str) -> dict[str, Any]:
        got = forget.plan_view(pid) if forget is not None else None
        if got is None:
            raise HTTPException(404, "no such plan")
        return got

    return router
