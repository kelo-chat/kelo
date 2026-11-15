"""GET /api/about: version, minimum app builds and the database's health (used by `assistant update`)."""

from __future__ import annotations

import asyncio
from typing import Any
from fastapi import Depends
from fastapi import APIRouter
from .context import AppContext
from .. import version as release_version


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    settings = ctx.settings
    store = ctx.store

    @router.get("/api/about", dependencies=[Depends(require_auth)])
    async def about() -> dict[str, Any]:
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
        return {"version": rel.version, "build": str(rel.build), "min_app": rel.min_app, "min_server": rel.min_server,
                "min_app_version": dict(settings.min_app_version), "min_app_ignored": list(settings.min_app_ignored), "db": db}

    return router
