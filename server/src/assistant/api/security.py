"""What the injection watch saw (injection_watch.py): outside text with instructions aimed at the assistant, and stopped leaks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    injection_log = ctx.injection_log

    @router.get("/api/security/injections", dependencies=[Depends(require_auth)])
    async def injections(days: int = Query(30, ge=1, le=90)) -> dict[str, Any]:
        """The events of the last `days` days (up to 90), newest first: {"items": [{"id", "ts", "run", "source", "origin", "kind"
        (override | exfil | money | marker | leak), "score", "excerpt" (at most 160 characters, secrets removed), "acted"}]}."""
        return {"items": injection_log.events(days)}

    return router
