"""The agent's picture (avatar.py) and the notification list (activity.py)."""

from __future__ import annotations

import asyncio
from typing import Any
from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from ..avatar import AvatarError, MAX_BYTES as MAX_AVATAR
from fastapi import APIRouter
from .context import AppContext
from .schemas import SeenIn


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    avatar = ctx.avatar
    require_auth = ctx.require_auth
    wallet = ctx.wallet

    # ---- the agent's picture -------------------------------------------------------------------------------------------------------
    @router.put("/api/agent/avatar", dependencies=[Depends(require_auth)])
    async def put_avatar(request: Request) -> dict[str, Any]:
        if int(request.headers.get("content-length") or 0) > MAX_AVATAR:
            raise HTTPException(413, "the picture is larger than 2 MB")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_AVATAR:
                raise HTTPException(413, "the picture is larger than 2 MB")
        try:
            return await asyncio.to_thread(avatar.put, bytes(data))
        except AvatarError as e:
            raise HTTPException(e.status, str(e)) from None

    @router.get("/api/agent/avatar", dependencies=[Depends(require_auth)])
    async def get_avatar(request: Request) -> Response:
        pic = avatar.get()
        if pic is None:
            raise HTTPException(404, "no picture set")
        etag = f'"{pic["etag"]}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(pic["data"], media_type=pic["mime"], headers=headers)

    @router.delete("/api/agent/avatar", dependencies=[Depends(require_auth)])
    async def delete_avatar() -> dict[str, Any]:
        return {"ok": avatar.delete(), "avatar_version": 0}

    # ---- notifications -----------------------------------------------------------------------------------------------------------
    @router.get("/api/notifications", dependencies=[Depends(require_auth)])
    async def get_notifications(after: int = 0, limit: int = 50) -> dict[str, Any]:
        return activity.notifications(after, limit)

    @router.post("/api/notifications/seen", dependencies=[Depends(require_auth)])
    async def seen_notifications(body: SeenIn) -> dict[str, Any]:
        marked, pays = activity.mark_notifications_seen(body.up_to)
        if pays:
            wallet.mark_seen(max(pays))   # the wallet's own list agrees
        return {"ok": True, "marked": marked, "unseen": activity.unseen_notifications()}

    return router
