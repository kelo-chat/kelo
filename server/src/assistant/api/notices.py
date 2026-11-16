"""Kelo notices API: unread notices for the signed-in device, and marking them read.

GET  /api/notices?lang=ko  → {"notices": [{id, date, level, title, body, link?}]}
     Unread notices only, newest first. `lang` picks the language (fallback: en, then ko).
     Filtered by min_app against the calling app's X-Kelo-App header when present (ignored if absent or unparsable).
     The feed is refreshed on this request if older than 6 h.

POST /api/notices/{id}/read  → 204
     Idempotent; unknown id → 204 too.

Auth: any signed-in device (Depends(require_auth)).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from ..version import APP_HEADER, parse_app_header
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    r = APIRouter()
    require_auth = ctx.require_auth
    notices_feed = ctx.notices_feed

    @r.get("/api/notices", dependencies=[Depends(require_auth)])
    async def get_notices(request: Request, lang: str = "en") -> dict[str, Any]:
        """Unread notices for the calling device, newest first. `lang` selects the language (fallback: en/ko)."""
        await notices_feed.refresh_if_stale()
        app_header = request.headers.get(APP_HEADER)
        app_version = parse_app_header(app_header)
        return {"notices": notices_feed.unread(lang, app_version)}

    @r.post("/api/notices/{id}/read", dependencies=[Depends(require_auth)], status_code=204)
    async def mark_notice_read(id: str) -> Response:
        """Mark a notice as read (idempotent; unknown id → 204)."""
        notices_feed.mark_read(id)
        return Response(status_code=204)

    return r
