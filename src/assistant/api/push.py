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


class LiveActivityIn(BaseModel):
    turn_id: str = Field(min_length=1, max_length=100)
    token: str = Field(min_length=1, max_length=400)          # the activity's APNs push token (hex)
    environment: str = Field(default="production", max_length=20)
    key: str = Field(min_length=1, max_length=100)            # base64 of the phone's 32-byte key (live_activity.py)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    push = ctx.push
    require_auth = ctx.require_auth
    turns = ctx.turns
    live_activities = ctx.live_activities

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

    # ---- iPhone Live Activities (live_activity.py): the lock screen shows the step of a running reply
    @router.post("/api/live-activity", dependencies=[Depends(require_auth)])
    async def live_activity_start(body: LiveActivityIn) -> dict[str, bool]:
        turn = turns.get(body.turn_id)
        if turn is None or turn.ended.is_set():
            raise HTTPException(404, "that reply is gone (it was stopped, or finished a while ago)")
        if not push.mode() or push.apns is None or (push.mode() == "local" and not push.apns.enabled()):
            raise HTTPException(404, "this server does not send iPhone push notifications")
        try:
            live_activities.register(turn, body.token, body.environment, body.key)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"ok": True}

    @router.delete("/api/live-activity/{turn_id}", dependencies=[Depends(require_auth)])
    async def live_activity_end(turn_id: str) -> dict[str, bool]:
        live_activities.forget(turn_id)
        return {"ok": True}

    return router
