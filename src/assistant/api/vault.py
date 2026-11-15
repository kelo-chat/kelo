"""Secrets coming in: OAuth device-flow connections (oauth_connect.py), the vault's website logins, lock and log, and the fill-in cards the agent asked for (secret_requests.py). Values only ever flow in; nothing here returns one."""

from __future__ import annotations

import logging
import time
from typing import Any
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from ..oauth_connect import OAuthError
from .. import credentials as cred_clean
from ..netguard import NetGuardError
from ..tools.vault_tools import site_host
from ..vault import VaultError
from fastapi import APIRouter
from .context import AppContext

log = logging.getLogger("uvicorn.error")


class ClientIdIn(BaseModel):
    client_id: str = Field(default="", max_length=100)


class DeviceStartIn(BaseModel):
    scope: str = Field(default="", max_length=20)


class EntryCardIn(BaseModel):
    card_id: str = Field(min_length=1, max_length=80)


class LockIn(BaseModel):
    locked: bool


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=512)
    save: bool = True                              # older form of `mode`: true = now, false = once
    mode: str | None = Field(default=None, pattern="^(now|after_success|once)$")


class LoginRegIn(BaseModel):
    site: str = Field(max_length=300)
    label: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=64)


class VaultPatch(BaseModel):
    auto_allow: bool | None = None
    label: str | None = Field(default=None, max_length=80)
    clear_result: bool = False


class VaultSettingsIn(BaseModel):
    mail_codes_auto: bool | None = None

DRAFT_KEEP_SECONDS = 86400.0  # a request for a login or key that nobody answered is forgotten after a day


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    device_flows = ctx.device_flows
    device_provider = ctx.device_provider
    entries = ctx.entries
    require_auth = ctx.require_auth
    schedule_grants = ctx.schedule_grants
    store = ctx.store
    transient = ctx.transient
    wallet = ctx.wallet
    workers = ctx.workers

    @router.put("/api/oauth/{provider}/client", dependencies=[Depends(require_auth)])
    async def put_oauth_client(provider: str, body: ClientIdIn) -> dict[str, Any]:
        """The service's public client id (not a secret), entered once. Empty forgets it."""
        try:
            device_flows.set_client_id(device_provider(provider), body.client_id)
        except OAuthError as e:
            raise HTTPException(422, str(e)) from None
        return device_flows.describe(provider)

    @router.post("/api/oauth/{provider}/start", dependencies=[Depends(require_auth)])
    async def start_oauth(provider: str, body: DeviceStartIn) -> dict[str, Any]:
        try:
            return (await device_flows.start(device_provider(provider), body.scope)).public()
        except OAuthError as e:
            raise HTTPException(422, str(e)) from None

    @router.get("/api/oauth/flows/{flow_id}", dependencies=[Depends(require_auth)])
    async def get_oauth_flow(flow_id: str) -> dict[str, Any]:
        flow = device_flows.get(flow_id)
        if flow is None:
            raise HTTPException(404, "that connection attempt is gone")
        return flow.public()

    @router.delete("/api/oauth/flows/{flow_id}", dependencies=[Depends(require_auth)])
    async def cancel_oauth_flow(flow_id: str) -> dict[str, bool]:
        return {"ok": True, "cancelled": device_flows.cancel(flow_id)}

    # ---- the vault proper (website logins, the lock, the log). Values only ever flow IN through these; nothing here returns one.
    @router.get("/api/vault", dependencies=[Depends(require_auth)])
    async def get_vault() -> dict[str, Any]:
        """`items`: what the vault really holds (a value is stored). `pending`: requests that have no value yet (the assistant asked for a
        login, or the user started adding one); they are not in the vault, and one nobody answers is forgotten after a day."""
        rows = store.credentials()
        if store.purge_drafts(time.time() - DRAFT_KEEP_SECONDS, [c["name"] for c in rows if transient.has(c["name"])]):
            rows = store.credentials()
        held: dict[str, list[dict[str, Any]]] = {}
        for g in schedule_grants.list(kind="login"):
            held.setdefault(g["login"], []).append(g)
        rows = [{**c, "schedule_grants": held.get(c["name"], [])} if c["kind"] == "login" else c for c in rows]
        for c in rows:
            if not c["has_secret"] and c["kind"] in ("api", "login") and not transient.has(c["name"]):
                entries.ensure_draft("key" if c["kind"] == "api" else "login", c["name"], c["label"], c["hosts"])
        return {"locked": store.vault_locked(), "mail_codes_auto": store.mail_codes_auto(),
                "items": [c for c in rows if c["has_secret"]], "pending": [c for c in rows if not c["has_secret"]],
                "entered_once": [c["name"] for c in rows if transient.has(c["name"])],
                "verifying": [c["name"] for c in rows if transient.promotes(c["name"])]}

    @router.delete("/api/vault/{name}/draft", dependencies=[Depends(require_auth)])
    async def cancel_entry(name: str) -> dict[str, Any]:
        """The user closed the entry form without entering anything: forget the request. An entry that holds a value is never removed here."""
        if transient.has(name):   # a login typed for one use, or until a sign-in works, is being held for it
            return {"ok": True, "deleted": False, "reply": "none", "request": None}
        deleted = store.delete_draft(name)
        if deleted:
            store.add_vault_log(name, "cancelled", "the entry form was closed without entering anything")
        ended = [r for r in (entries.cancel_for_full(kind, name) for kind in ("key", "login")) if r["request"] is not None]
        return {"ok": True, "deleted": deleted, "reply": ended[0]["reply"] if ended else "none",
                "request": ended[0]["request"] if ended else None}

    @router.post("/api/vault/logins", dependencies=[Depends(require_auth)])
    async def add_login(body: LoginRegIn) -> dict[str, Any]:
        try:
            host = site_host(body.site)
            name = cred_clean.clean_name(body.name or "login-" + host.replace(".", "-"))
            store.save_credential(name, body.label.strip()[:80] or host, [host], {}, "", kind="login")
        except (NetGuardError, VaultError) as e:
            raise HTTPException(422, str(e)) from None
        store.add_vault_log(name, "added", f"website login for {host}")
        return {"name": name, "items": store.credentials()}

    @router.put("/api/vault/{name}/login", dependencies=[Depends(require_auth)])
    async def put_login(name: str, body: LoginIn) -> dict[str, Any]:
        item = store.credential(name)
        if not item or item["kind"] != "login":
            raise HTTPException(404, "no such website login")
        if any(c in body.username + body.password for c in "\x00\r\n"):
            raise HTTPException(422, "the user name and password must be a single line")
        mode = body.mode or ("now" if body.save else "once")
        if mode == "now":
            transient.discard(name)
            store.set_login(name, body.username, body.password)
            store.add_vault_log(name, "replaced" if item["has_secret"] else "saved", "website login, not tried yet")
        else:
            transient.put(name, body.username, body.password, promote=mode == "after_success")
            store.add_vault_log(name, "entered-once", "website login held until a sign-in works" if mode == "after_success" else "website login, for one use, not stored")
        workers.login_saved(name)   # a background job that stopped for this login goes on
        got = entries.saved_full("login", name, {"mode": mode})
        # reply: live | started | queued | none (secret_requests.REPLY_STATES): whether a reply is still coming for the agent's request;
        # request: that request as it ended (null when nothing asked for this login)
        return {"ok": True, "saved": mode == "now", "mode": mode, "reply": got["reply"], "request": got["request"],
                **({"turn_id": got["turn_id"]} if got["turn_id"] else {})}

    @router.patch("/api/vault/{name}", dependencies=[Depends(require_auth)])
    async def patch_item(name: str, body: VaultPatch) -> list[dict[str, Any]]:
        item = store.credential(name)
        if not item:
            raise HTTPException(404, "no such item")
        if body.auto_allow is not None:
            if item["kind"] != "login":
                raise HTTPException(422, "only website logins have this setting")
            store.set_auto_allow(name, body.auto_allow)
            store.add_vault_log(name, "setting", "signs in without asking" if body.auto_allow else "asks each time")
        if body.label is not None and body.label.strip():
            store.set_credential_label(name, body.label.strip())
        if body.clear_result:
            store.set_credential_result(name, None)
            store.add_vault_log(name, "setting", "the user allowed trying again")
        return store.credentials()

    # ---- fill-in cards the agent asked for (secret_requests.py). Values never come here: the app sends them to the vault / wallet.
    @router.get("/api/secret-requests", dependencies=[Depends(require_auth)])
    async def get_secret_requests() -> dict[str, Any]:
        """Open requests, oldest first: the chat shows the agent's ones under `message_id` (or at the end while that reply is still
        being written); the Keychain lists all of them under "waiting for input"."""
        return {"items": entries.pending()}

    def entry_or_404(request_id: str) -> dict[str, Any]:
        req = entries.get(request_id)
        if req is None:
            raise HTTPException(404, "no such request")
        return req

    @router.post("/api/secret-requests/{request_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel_secret_request(request_id: str) -> dict[str, Any]:
        """[Cancel] on the entry: the agent is told (in the waiting reply, or once with the next message) and does not ask again. An empty
        vault item made for the request is removed; one that holds a value is never touched."""
        req = entry_or_404(request_id)
        if req["state"] == "open" and req["kind"] in ("key", "login") and req["name"] and not transient.has(req["name"]):
            if store.delete_draft(req["name"]):
                store.add_vault_log(req["name"], "cancelled", "the fill-in card was closed without entering anything")
        got = entries.answer_full(request_id, "cancelled")
        return {"ok": True, "request": got["request"], "reply": got["reply"]}

    @router.post("/api/secret-requests/{request_id}/card", dependencies=[Depends(require_auth)])
    async def card_for_secret_request(request_id: str, body: EntryCardIn) -> dict[str, Any]:
        """The card form of a card entry saved its card through the wallet (POST /api/wallet/cards): the request is answered with that
        card's nickname and last four digits, and the agent goes on."""
        req = entry_or_404(request_id)
        if req["kind"] != "card":
            raise HTTPException(422, "this request is not for a card")
        card = wallet.card(body.card_id)
        if card is None:
            raise HTTPException(404, "no such card")
        got = entries.answer_full(request_id, "saved", {"card": {"id": card["id"], "label": card["label"], "brand": card["brand"],
                                                                 "last4": card["last4"], "default": card.get("default", False)}})
        return {"ok": True, "request": got["request"], "turn_id": got["turn_id"], "reply": got["reply"]}

    @router.get("/api/vault/log", dependencies=[Depends(require_auth)])
    async def get_vault_log(limit: int = 100) -> list[dict[str, Any]]:
        return store.vault_log(limit)

    @router.put("/api/vault/lock", dependencies=[Depends(require_auth)])
    async def put_lock(body: LockIn) -> dict[str, bool]:
        store.set_vault_locked(body.locked)
        store.add_vault_log("vault", "locked" if body.locked else "unlocked", "by the user")
        return {"locked": body.locked}

    @router.put("/api/vault/settings", dependencies=[Depends(require_auth)])
    async def put_vault_settings(body: VaultSettingsIn) -> dict[str, bool]:
        """"Use codes from my mail automatically" (default on): during a sign-in Kelo itself started, a code that site mailed is typed
        without asking (tools/browser_more.py auto_code)."""
        if body.mail_codes_auto is not None:
            store.set_mail_codes_auto(body.mail_codes_auto)
            store.add_vault_log("vault", "setting", "codes from mail are typed automatically" if body.mail_codes_auto else "codes from mail ask first")
        return {"mail_codes_auto": store.mail_codes_auto()}

    return router
