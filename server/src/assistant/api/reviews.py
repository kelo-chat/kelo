"""The nightly review (nightly_review.py): the days reviewed, the corrections kept open until fixed."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..nightly_review import NightlyReview
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    review = ctx.nightly_review
    require_auth = ctx.require_auth

    def need_review() -> NightlyReview:
        if review is None:
            raise HTTPException(404, "the agent's files are not enabled on this server")
        return review

    @router.get("/api/reviews", dependencies=[Depends(require_auth)])
    async def get_reviews(limit: int = 30) -> dict[str, Any]:
        r = need_review()
        return {"days": r.days(min(max(limit, 1), 365)), "last_run": r.last_run()}

    @router.post("/api/reviews/run", dependencies=[Depends(require_auth)])
    async def run_review() -> dict[str, Any]:
        out = await need_review().run(force=True)
        if out["status"] == "busy":
            raise HTTPException(409, out["reason"])
        return out

    @router.get("/api/corrections", dependencies=[Depends(require_auth)])
    async def get_corrections(status: str = "open") -> dict[str, Any]:
        if status not in ("open", "resolved", "dropped", "all"):
            raise HTTPException(422, "status must be open, resolved, dropped or all")
        return {"items": need_review().corrections(None if status == "all" else status)}

    @router.post("/api/corrections/{correction_id}/resolve", dependencies=[Depends(require_auth)])
    async def resolve_correction(correction_id: int) -> dict[str, Any]:
        try:
            return need_review().resolve(correction_id)
        except KeyError:
            raise HTTPException(404, "no such correction") from None

    return router
