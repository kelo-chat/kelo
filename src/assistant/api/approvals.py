"""Approvals: answering a card, what is waiting, the history, standing rules, and the per-task permissions (tools/task_permission.py)."""

from __future__ import annotations

import time
from typing import Any
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from ..server import HANDOVER_RESOLVED_KEEP
from ..weather import WeatherError
from fastapi import APIRouter
from .context import AppContext


class PlaceIn(BaseModel):
    lat: float
    lon: float
    name: str = ""
    country_code: str = ""
    accuracy: float | None = None
    sublocality: str = ""


class LocationAnswerIn(BaseModel):
    allowed: bool
    place: PlaceIn | None = None


class ApprovalIn(BaseModel):
    approve: bool
    remember: str | None = Field(default=None, pattern="^(site|schedule)$")


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    activity = ctx.activity
    answer_approval = ctx.answer_approval
    weather = ctx.weather
    broker = ctx.broker
    pending_approvals = ctx.pending_approvals
    require_auth = ctx.require_auth
    resolved_checks = ctx.resolved_checks
    task_grants = ctx.task_grants

    # ---- approvals: answers, history, standing rules, what is waiting ----------------------------------------------------------
    @router.post("/api/approvals/{approval_id}", dependencies=[Depends(require_auth)])
    async def answer(approval_id: str, body: ApprovalIn) -> dict[str, Any]:
        result = answer_approval(approval_id, body.approve, body.remember)
        if result is None:
            raise HTTPException(404, "no such pending approval")
        return result

    # LOCATION REQUEST (location.py; the card in server.py ask_location): the app answers the card with the phone's place, or that the
    # user refused (in the OS prompt or on the card). {"allowed": bool, "place"?: {"lat", "lon", "name"?, "country_code"?,
    # "accuracy"?, "sublocality"?}} -> {"ok", "state": "allowed" | "refused"}. Allowed without a place (none could be read) counts as
    # not answered: the card waits on until it runs out. 404 when the card is no longer waiting.
    @router.post("/api/location-requests/{approval_id}", dependencies=[Depends(require_auth)])
    async def answer_location(approval_id: str, body: LocationAnswerIn) -> dict[str, Any]:
        fut = broker.pending.get(approval_id)
        meta = pending_approvals.get(approval_id) or {}
        if fut is None or fut.done() or meta.get("kind") != "location":
            raise HTTPException(404, "no such location request")
        if body.allowed:
            if body.place is None:
                raise HTTPException(422, "allowed needs the place")
            try:
                weather.set_here(body.place.lat, body.place.lon, body.place.name, body.place.country_code, body.place.accuracy,
                                 body.place.sublocality)
            except WeatherError as e:
                raise HTTPException(422, str(e)) from None
        meta["answer"] = "allowed" if body.allowed else "refused"
        broker.resolve(approval_id, body.allowed)
        return {"ok": True, "state": meta["answer"]}

    @router.get("/api/approvals/pending", dependencies=[Depends(require_auth)])
    async def get_pending_approvals() -> dict[str, Any]:
        items = sorted(pending_approvals.values(), key=lambda e: e["asked_at"], reverse=True)
        now = time.time()
        done = sorted((v for v in resolved_checks.values() if now - v["at"] <= HANDOVER_RESOLVED_KEEP), key=lambda v: v["at"], reverse=True)
        return {"items": [{k: v for k, v in e.items() if k not in ("arguments", "answer")} for e in items], "resolved": done}

    @router.get("/api/approvals/history", dependencies=[Depends(require_auth)])
    async def get_approval_history(limit: int = 50, before: float | None = None, task_id: int | None = None) -> dict[str, Any]:
        items = activity.approvals(limit, before, task_id)
        return {"items": items, "next_before": items[-1]["asked_at"] if len(items) >= max(1, min(limit, 200)) else None}

    @router.get("/api/task-permissions", dependencies=[Depends(require_auth)])
    async def get_task_permissions() -> dict[str, Any]:
        return {"items": task_grants.active()}

    @router.delete("/api/task-permissions/{grant_id}", dependencies=[Depends(require_auth)])
    async def delete_task_permission(grant_id: str) -> dict[str, bool]:
        if not task_grants.revoke(grant_id):
            raise HTTPException(404, "no such permission (it may have ended with its task)")
        return {"ok": True}

    @router.get("/api/approvals/rules", dependencies=[Depends(require_auth)])
    async def get_approval_rules() -> dict[str, Any]:
        return {"items": activity.rules()}

    @router.delete("/api/approvals/rules/{rule_id}", dependencies=[Depends(require_auth)])
    async def delete_approval_rule(rule_id: int) -> dict[str, bool]:
        if not activity.delete_rule(rule_id):
            raise HTTPException(404, "no such rule")
        return {"ok": True}
    return router
