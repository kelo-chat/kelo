"""This phone's push registration (push.py; PUSH API next to serialize()). The token is write-only."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ..push import PushError
from fastapi import APIRouter
from .context import AppContext


class PushRegisterIn(BaseModel):
    token: str = Field(min_length=1, max_length=4096)
    platform: str = Field(default="android", max_length=20)
    environment: str = Field(default="", max_length=20)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    push = ctx.push
    require_auth = ctx.require_auth

    # ---- push notifications (see PUSH API next to serialize()). The token is write-only: no answer contains it.
    @router.post("/api/push/register", dependencies=[Depends(require_auth)])
    async def push_register(body: PushRegisterIn, request: Request) -> dict[str, Any]:
        dev = request.state.device_id
        if dev is None:
            raise HTTPException(403, "only a paired phone can register for push notifications")
        try:
            push.register(int(dev), body.token.strip(), body.platform.strip().lower(), body.environment)
        except PushError as e:
            raise HTTPException(e.status, str(e)) from None
        return {"ok": True, "push": bool(push.mode())}

    @router.delete("/api/push/register", dependencies=[Depends(require_auth)])
    async def push_unregister(request: Request) -> dict[str, Any]:
        dev = request.state.device_id
        return {"ok": True, "removed": push.unregister(int(dev)) if dev is not None else False, "push": bool(push.mode())}

    @router.get("/api/push/status", dependencies=[Depends(require_auth)])
    async def push_status(request: Request) -> dict[str, Any]:
        return await push.status(request.state.device_id)

    return router
