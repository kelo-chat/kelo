"""Mail, calendars and what a phone shares: mailboxes (IMAP/SMTP, Gmail by sign-in), Google Calendar, CalDAV, the phone's calendar and its contacts, messages, calls, health and notifications; the Google sign-in app and its broker."""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from .. import i18n
from ..calendars import CalDavError
from .. import connector_icons
from ..mail import PROVIDERS as MAIL_PROVIDERS, MailError
from ..mcp_oauth import (McpOAuthError)
from ..google_web import GoogleReviewPending, GoogleWebError
from ..phone_data import PATHS as PHONE_PATHS, MAX_BYTES as PHONE_MAX_BYTES
from ..beta import install_hash
from fastapi import APIRouter
from .context import AppContext
from .schemas import ExchangeIn, GitHubAppIn

log = logging.getLogger("uvicorn.error")


class CalDavIn(BaseModel):
    url: str = Field(max_length=300)
    username: str = Field(max_length=200)
    password: str = Field(max_length=200)


class GoogleOpenIn(BaseModel):
    open: bool


class GoogleStartIn(BaseModel):
    origin: str = Field(max_length=300)
    product: str = Field(default="gmail", max_length=20)


class MailIn(BaseModel):
    provider: str = Field(max_length=20)
    address: str = Field(max_length=200)
    password: str = Field(max_length=200)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=600)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    beta = ctx.beta
    beta_desk = ctx.beta_desk
    broker_hits = ctx.broker_hits
    calendars = ctx.calendars
    device_of = ctx.device_of
    gcal = ctx.gcal
    google_web = ctx.google_web
    mail = ctx.mail
    phone_cals = ctx.phone_cals
    phone_data = ctx.phone_data
    require_auth = ctx.require_auth
    require_owner = ctx.require_owner
    settings = ctx.settings
    store = ctx.store

    # ---- mailboxes connected with an app password (IMAP/SMTP). Checked with a real sign-in before anything is saved.
    @router.get("/api/mail/providers", dependencies=[Depends(require_auth)])
    async def get_mail_providers() -> dict[str, Any]:
        await beta.refresh()   # an approved beta request opens the Google sign-in for this copy (beta.py)
        return {"providers": [{"id": p.id, "name": i18n.t(p.name), "help": [i18n.t(h) for h in p.help], **connector_icons.for_mail(p.id)}
                              for p in MAIL_PROVIDERS.values()], "accounts": mail.accounts(),
                "google_web": await google_web.available() and google_web.open_here()}

    @router.post("/api/google/web/start", dependencies=[Depends(require_auth)])
    async def start_google_web(body: GoogleStartIn) -> dict[str, str]:
        try:
            return {"url": await google_web.start(body.origin, body.product)}
        except GoogleReviewPending as e:   # new Google sign-ins are closed until Google's review is done (google_web.py)
            raise HTTPException(409, str(e)) from None
        except (GoogleWebError, McpOAuthError) as e:
            raise HTTPException(422, str(e)) from None

    # ---- Google Calendar with the Google sign-in (google_calendar.py). The app's card: whether the sign-in can be used here (and why
    # not, in words), and the connected accounts. Disconnecting is DELETE /api/credentials/{name}.
    @router.get("/api/google/calendar", dependencies=[Depends(require_auth)])
    async def get_google_calendar() -> dict[str, Any]:
        ready = await google_web.available()
        reason = "" if ready else (i18n.t("This server has no fixed address (sign-in relay) set up, so Google sign-in can't be used.")
                                   if not settings.oauth_relay else i18n.t("The operator hasn't set up Google sign-in yet."))
        await beta.refresh()   # an approved beta request opens the sign-in for this copy (beta.py)
        pending = not google_web.open_here()   # accounts already connected keep working; only new sign-ins wait
        if pending and ready:
            ready, reason = False, google_web.review_note()
        return {"available": ready, "reason": reason, "pending_review": pending,
                "accounts": [{k: a[k] for k in ("name", "address", "can_write", "problem")} for a in gcal.accounts()]}

    # ---- the phone's own calendar (phone_calendar.py): one snapshot per phone, replaced on each upload, wiped when turned off
    @router.put("/api/phone-calendar", dependencies=[Depends(require_auth)])
    async def put_phone_calendar(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > 3_000_000:
            raise HTTPException(413, "the calendar snapshot is too large")
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected an object")
            return phone_cals.put(device_of(request), body)
        except ValueError as e:
            raise HTTPException(422, str(e)[:200]) from None

    @router.delete("/api/phone-calendar", dependencies=[Depends(require_auth)])
    async def delete_phone_calendar(request: Request) -> dict[str, bool]:
        phone_cals.delete(device_of(request))
        phone_data.delete(device_of(request), "calendar")   # calendar changes still waiting for this phone are dropped
        return {"ok": True}

    # ---- the rest of what a phone shares from itself (phone_data.py): contacts, messages, call log, health, notifications, dialer
    def phone_kind(kind: str) -> str:
        k = PHONE_PATHS.get(kind)
        if k is None:
            raise HTTPException(404, "no such kind")
        return k

    @router.get("/api/phone", dependencies=[Depends(require_auth)])
    async def get_phone(request: Request) -> dict[str, Any]:
        return phone_data.summary(device_of(request))

    @router.post("/api/phone/tasks/claim", dependencies=[Depends(require_auth)])
    async def claim_phone_tasks(request: Request) -> dict[str, Any]:
        return {"items": phone_data.claim(device_of(request))}

    @router.post("/api/phone/tasks/{task_id}/result", dependencies=[Depends(require_auth)])
    async def phone_task_result(task_id: int, request: Request) -> dict[str, bool]:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(422, "expected an object")
        if not phone_data.result(device_of(request), task_id, body):
            raise HTTPException(404, "no such task waiting for this phone")
        return {"ok": True}

    async def phone_body(request: Request, kind: str) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > PHONE_MAX_BYTES[kind]:
            raise HTTPException(413, "too large")
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(422, "expected an object")
        return body

    @router.put("/api/phone/{kind}", dependencies=[Depends(require_auth)])
    async def put_phone(kind: str, request: Request) -> dict[str, Any]:
        k = phone_kind(kind)
        if k == "notifications":
            raise HTTPException(405, "notifications are added with POST")
        try:
            return phone_data.put(device_of(request), k, await phone_body(request, k))
        except ValueError as e:
            raise HTTPException(422, str(e)[:200]) from None

    @router.post("/api/phone/notifications", dependencies=[Depends(require_auth)])
    async def post_phone_notifications(request: Request) -> dict[str, Any]:
        try:
            return phone_data.add_notes(device_of(request), await phone_body(request, "notifications"))
        except ValueError as e:
            raise HTTPException(422, str(e)[:200]) from None

    @router.delete("/api/phone/{kind}", dependencies=[Depends(require_auth)])
    async def delete_phone(kind: str, request: Request) -> dict[str, bool]:
        phone_data.delete(device_of(request), phone_kind(kind))
        return {"ok": True}

    @router.get("/api/phone/{kind}", dependencies=[Depends(require_auth)])
    async def get_phone_kind(kind: str, request: Request) -> dict[str, Any]:
        return phone_data.status(device_of(request), phone_kind(kind))

    @router.get("/api/phone-calendar", dependencies=[Depends(require_auth)])
    async def get_phone_calendar(request: Request) -> dict[str, Any]:
        return phone_cals.status(device_of(request))

    @router.get("/api/oauth-apps/google", dependencies=[Depends(require_owner)])
    async def get_google_app() -> dict[str, Any]:
        return {"client_id": google_web.own_client_id(), "has_secret": bool(store.key("oauth-app:google")), "is_broker": google_web.is_broker(),
                "callback": settings.oauth_relay, "broker": settings.oauth_broker, "open_to_all": google_web.open_to_all()}

    @router.put("/api/oauth-apps/google/open", dependencies=[Depends(require_owner)])
    async def put_google_open(body: GoogleOpenIn) -> dict[str, Any]:
        """The operator's switch: let everyone start a new Google sign-in (after Google has verified the client; 100 users before that)."""
        google_web.set_open(body.open)
        return await get_google_app()

    @router.put("/api/oauth-apps/google", dependencies=[Depends(require_owner)])
    async def put_google_app(body: GitHubAppIn) -> dict[str, Any]:
        try:
            google_web.set_app(body.client_id, body.client_secret)
        except GoogleWebError as e:
            raise HTTPException(422, str(e)) from None
        return await get_google_app()

    def broker_gate(request: Request) -> None:
        who = request.client.host if request.client else "?"
        now = time.monotonic()
        hits = broker_hits.setdefault(who, deque())
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= 20:
            raise HTTPException(429, "too many sign-ins from one place; try again in a minute")
        hits.append(now)

    @router.get("/broker/google/client", include_in_schema=False)
    async def broker_google_client() -> dict[str, Any]:
        if not google_web.is_broker():
            raise HTTPException(404, "this server is not a Google sign-in broker")
        return {"client_id": google_web.own_client_id(), "open": google_web.open_to_all()}

    @router.post("/broker/google/exchange", include_in_schema=False)
    async def broker_google_exchange(body: ExchangeIn, request: Request) -> dict[str, Any]:
        if not google_web.is_broker():
            raise HTTPException(404, "this server is not a Google sign-in broker")
        if body.redirect_uri != settings.oauth_relay:
            raise HTTPException(422, "the sign-in must come back through this server's relay page")
        if not google_web.open_to_all() and not (body.install_id and beta_desk.allowed(install_hash(body.install_id))):
            raise HTTPException(409, google_web.review_note())   # closed, except for a copy whose beta request was approved
        broker_gate(request)
        try:
            tok = await google_web.token_here({"grant_type": "authorization_code", "code": body.code, "redirect_uri": body.redirect_uri,
                                               "code_verifier": body.code_verifier})
        except GoogleWebError as e:
            raise HTTPException(502, str(e)) from None
        return {k: tok[k] for k in ("access_token", "refresh_token", "expires_in", "id_token", "scope", "token_type", "error", "error_description") if k in tok}

    @router.post("/broker/google/refresh", include_in_schema=False)
    async def broker_google_refresh(body: RefreshIn, request: Request) -> dict[str, Any]:
        if not google_web.is_broker():
            raise HTTPException(404, "this server is not a Google sign-in broker")
        broker_gate(request)
        try:
            tok = await google_web.token_here({"grant_type": "refresh_token", "refresh_token": body.refresh_token})
        except GoogleWebError as e:
            raise HTTPException(502, str(e)) from None
        return {k: tok[k] for k in ("access_token", "expires_in", "refresh_token", "scope", "error", "error_description") if k in tok}

    @router.post("/api/mail/accounts", dependencies=[Depends(require_auth)])
    async def add_mail_account(body: MailIn) -> dict[str, Any]:
        try:
            return await mail.connect(body.provider, body.address, body.password)
        except MailError as e:
            raise HTTPException(422, str(e)) from None

    # a CalDAV calendar with its own address (Fastmail, Zoho, Nextcloud ...), read only, signed in once to check it (calendars.py)
    @router.get("/api/caldav", dependencies=[Depends(require_auth)])
    async def get_caldav() -> dict[str, Any]:
        return {"accounts": [{"name": a.item, "address": a.address, "url": a.url} for a in calendars.accounts() if a.provider == "caldav"]}

    @router.post("/api/caldav/accounts", dependencies=[Depends(require_auth)])
    async def add_caldav_account(body: CalDavIn) -> dict[str, Any]:
        try:
            return await calendars.connect(body.url, body.username, body.password)
        except CalDavError as e:
            raise HTTPException(422, str(e)) from None

    return router
