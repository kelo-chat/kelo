"""Approvals: answering a card, what is waiting, the history, standing rules, and the per-task permissions (tools/task_permission.py)."""

from __future__ import annotations

import time
from typing import Any
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from .. import approval_edit, approval_sign, i18n, mail_html
from ..channels import needs_app
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
    remember: str | None = Field(default=None, pattern="^(site|schedule|recipient)$")
    sig: str | None = Field(default=None, max_length=400)   # signed payment approvals: the phone's signature (approval_sign.py)
    # the fixed outgoing message of a card with "editable": {that card's field: the new text}; read only with approve=true (approval_edit.py)
    edited: dict[str, str] | None = None
    # where the answer comes from (the history shows it): the app's card, or a button on the phone's notification
    via: str = Field(default="app", pattern="^(app|notification)$")


def check_payment_signature(device_id: int | None, store: Any, approval_id: str, meta: dict[str, Any], sig: str | None) -> None:
    """A payment approved with Allow must carry the signature of the calling device's approval key (docs/approval-signing.md). A device
    without a key (an older app) is allowed as before, unless the owner turned on require_signed_payments."""
    key = store.approval_key(device_id) if device_id is not None else ""
    if key:
        if not sig:
            raise HTTPException(403, {"code": "signature_required", "message": i18n.t("Approve payments in the Kelo phone app.")})
        if not approval_sign.verify(key, approval_sign.approval_message(approval_id, True, int(meta.get("issued_at") or 0)), sig):
            raise HTTPException(403, {"code": "bad_signature", "message": i18n.t("This payment approval was not signed by this phone.")})
        return
    if store.meta("require_signed_payments") == "1":
        raise HTTPException(403, {"code": "signature_required",
                                  "message": i18n.t("Approve payments in the Kelo phone app (you may need to update the app).")})


def notify_actions(item: dict[str, Any]) -> list[str]:
    """What a phone notification for this pending approval may offer as buttons, ONE rule for every app. A payment offers only Deny: an Allow
    needs the phone's biometric signature, which only the open app can give (approval_sign.is_payment). A card that needs input in the
    app (a PIN or payment step, a wallet or card choice, a login or secret, a code from the mail, a human check, a location) offers
    nothing. Everything else: Allow and Deny. An Allow from a notification is always "once": `remember` is never offered there."""
    if approval_sign.is_payment(item):
        return ["deny"]
    if needs_app(item) or item.get("pin") or item.get("type") == "payment_step":
        return []
    return ["allow", "deny"]


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
    store = ctx.store

    # ---- approvals: answers, history, standing rules, what is waiting ----------------------------------------------------------
    @router.post("/api/approvals/{approval_id}", dependencies=[Depends(require_auth)])
    async def answer(request: Request, approval_id: str, body: ApprovalIn) -> dict[str, Any]:
        meta = pending_approvals.get(approval_id) or {}
        fut = broker.pending.get(approval_id)
        if body.approve and fut is not None and not fut.done() and approval_sign.is_payment(meta):
            check_payment_signature(request.state.device_id, store, approval_id, meta, body.sig)   # before the card is answered: a refusal keeps it open
        try:
            result = answer_approval(approval_id, body.approve, body.remember, body.edited if body.approve else None, body.via)
        except approval_edit.EditRefused as e:   # the card stays open: the owner fixes the text and tries again
            raise HTTPException(400, str(e)) from None
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
        return {"items": [{**{k: v for k, v in e.items() if k not in ("arguments", "answer") and not k.startswith("_")},
                           "notify_actions": notify_actions(e)} for e in items], "resolved": done}

    # MAIL PREVIEW (the card of a designed email_send; mail_html.preview): the sanitized HTML that will be sent, in a document that
    # can load and run nothing, plus its blocks (the desktop app draws them). Only while the card waits: after the answer the apps keep
    # what they fetched. 404 {"detail": "no such pending mail"} for an unknown or answered card, a card that is not a mail, a plain mail.
    # 413 {"code": "too_big"} when the document is over mail_html.PREVIEW_MAX_BYTES. 200: {"html", "blocks", "cut", "pictures", "links",
    # "bytes"}, Cache-Control: no-store, the preview's Content-Security-Policy, nosniff.
    @router.get("/api/approvals/{approval_id}/mail-preview", dependencies=[Depends(require_auth)])
    async def get_mail_preview(approval_id: str) -> Response:
        meta = pending_approvals.get(approval_id) or {}
        fut = broker.pending.get(approval_id)
        if meta.get("name") != "email_send" or fut is None or fut.done():
            raise HTTPException(404, "no such pending mail")
        preview = meta.get("_mail_preview")
        if preview is None:
            html = str((meta.get("arguments") or {}).get("html") or "")
            preview = mail_html.preview(html) if html else None
            if preview is None:
                raise HTTPException(404, "no such pending mail")
            meta["_mail_preview"] = preview   # computed once per card; never listed (get_pending_approvals drops "_" keys)
        size = len(preview.html.encode("utf-8"))
        if size > mail_html.PREVIEW_MAX_BYTES:
            raise HTTPException(413, {"code": "too_big", "message": i18n.t("The preview is too large to show.")})
        return JSONResponse({"html": preview.html, "blocks": preview.blocks, "cut": preview.cut, "pictures": preview.pictures,
                             "links": preview.links, "bytes": size},
                            headers={"Cache-Control": "no-store", "Content-Security-Policy": mail_html.PREVIEW_CSP,
                                     "X-Content-Type-Options": "nosniff"})

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

    # RECIPIENT ALLOWS (data_trail.py): "always for this recipient" on a data-trail card. Only the owner's app lists and removes them;
    # the agent has no tool for either.
    # GET    /api/permissions/recipients        {"items": [{"id", "recipient", "kind", "created_at", "approval_id"}]}
    # DELETE /api/permissions/recipients/{id}   {"ok": true}; 404 when there is no such allow
    @router.get("/api/permissions/recipients", dependencies=[Depends(ctx.require_owner)])
    async def get_recipient_allows() -> dict[str, Any]:
        return {"items": ctx.trail_store.allows()}

    @router.delete("/api/permissions/recipients/{allow_id}", dependencies=[Depends(ctx.require_owner)])
    async def delete_recipient_allow(allow_id: int) -> dict[str, bool]:
        if not ctx.trail_store.delete_allow(allow_id):
            raise HTTPException(404, "no such allow")
        return {"ok": True}
    return router
