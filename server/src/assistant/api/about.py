"""GET /api/about: version, minimum app builds and the database's health (used by `assistant update`)."""

from __future__ import annotations

import asyncio
from typing import Any
from fastapi import Depends, Request, Response
from fastapi import APIRouter
from .context import AppContext
from .. import app_updates
from .. import version as release_version


SERVER_HEADER = "X-Kelo-Server-Update"   # a newer server release the caller may install (server_updates.offer); see server_update_for


def server_update_for(ctx: AppContext, request: Request) -> str | None:
    """ctx.server_updates.offer() for a caller allowed to update this server (the access key, or a signed-in device that is not on a
    review copy: require_owner's rule), else None. Never raises."""
    try:
        st = request.state
        allowed = getattr(st, "device_id", None) is None or bool(getattr(st, "device_owner", False))
        su = getattr(ctx, "server_updates", None)
        return su.offer() if allowed and su is not None else None
    except Exception:  # noqa: BLE001 - a notice never breaks an answer
        return None


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    settings = ctx.settings
    store = ctx.store

    @router.get("/api/about", dependencies=[Depends(require_auth)])
    async def about(request: Request, response: Response) -> dict[str, Any]:
        rel = release_version.current()   # version.json: the release and its build (docs/versioning.md)
        # min_app_version: the oldest app build this server works with, per platform ({} = none); also in /api/profile
        # db: {"ok", "schema" (fingerprint of the tables), "tables", "migrations": "done"}; `assistant update` reads it before and
        # after an update (an error here means the database did not open)
        try:
            db = await asyncio.to_thread(store.schema_info)
        except Exception as e:  # noqa: BLE001 - reported, never raised: the update's health check needs the answer
            db = {"ok": False, "error": type(e).__name__}
        # version, build: this server's release; min_app: the oldest app release it works with (426 app_too_old below it);
        # min_server: the oldest server the apps of this release work with
        # build as a string: the apps (Android/desktop MyModels.About, iOS ApiModels.About) read it as one, and an int there failed
        # the whole answer, so My showed an old cached server version
        # app_update: the newest released app for the caller's platform and channel that works with this server and is newer than the
        # caller (null: none; also the X-Kelo-App-Update header; app_updates.py)
        update = await ctx.app_updates.for_header(request.headers.get("x-kelo-app"), request.headers.get(app_updates.CHANNEL_HEADER))
        if update:
            response.headers[app_updates.HEADER] = update
        # server_update: a newer server release the caller may install (null: none; also X-Kelo-Server-Update; server_updates.offer)
        server_update = server_update_for(ctx, request)
        if server_update:
            response.headers[SERVER_HEADER] = server_update
        return {"version": rel.version, "build": str(rel.build), "min_app": rel.min_app, "min_server": rel.min_server,
                "min_app_version": dict(settings.min_app_version), "min_app_ignored": list(settings.min_app_ignored), "db": db,
                "app_update": update, "server_update": server_update}

    return router
