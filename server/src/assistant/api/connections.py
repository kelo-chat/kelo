"""Connections to services: ones the user registers (REST/MCP, credentials), OAuth device-flow providers, GitHub with a sign-in button and its broker (the GitHub app secret stays on the operator's server)."""

from __future__ import annotations

import time
from collections import deque
from typing import Any
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from .. import i18n
from ..oauth_connect import PROVIDERS as DEVICE_PROVIDERS, OAuthError
from ..mcp_oauth import (McpOAuthError)
from ..github_web import GitHubWebError
from ..cred_keyring import spawn_sync
from .. import credentials as cred_clean
from ..netguard import NetGuardError
from ..netguard import fetch as netguard_fetch
from ..secret_requests import check_key
from ..vault import VaultError
from fastapi import APIRouter
from .context import AppContext
from .schemas import ExchangeIn, GitHubAppIn


class CredentialIn(BaseModel):
    name: str
    label: str = ""
    hosts: list[str]
    placement: dict[str, str]
    mcp_endpoint: str = ""


class SecretIn(BaseModel):
    secret: str = Field(min_length=8, max_length=4096)


class WebStartIn(BaseModel):
    origin: str = Field(max_length=300)
    scope: str = Field(default="", max_length=20)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    broker_hits = ctx.broker_hits
    device_flows = ctx.device_flows
    entries = ctx.entries
    github_web = ctx.github_web
    mcp_fetcher = ctx.mcp_fetcher
    require_auth = ctx.require_auth
    require_owner = ctx.require_owner
    schedule_grants = ctx.schedule_grants
    settings = ctx.settings
    connector_data = ctx.connector_data
    store = ctx.store
    transient = ctx.transient

    # ---- connections to services the user registers (REST/MCP). The secret is write-only: no endpoint returns it.
    @router.get("/api/credentials", dependencies=[Depends(require_auth)])
    async def get_credentials() -> list[dict[str, Any]]:
        return store.credentials()

    @router.post("/api/credentials", dependencies=[Depends(require_auth)])
    async def add_credential(body: CredentialIn) -> list[dict[str, Any]]:
        try:
            name = cred_clean.clean_name(body.name)
            hosts = cred_clean.clean_hosts(body.hosts)
            placement = cred_clean.clean_placement(body.placement)
            endpoint = cred_clean.clean_endpoint(body.mcp_endpoint, hosts)
        except NetGuardError as e:
            raise HTTPException(422, str(e)) from None
        try:
            store.save_credential(name, body.label.strip()[:80] or name, hosts, placement, endpoint)
        except VaultError as e:
            raise HTTPException(409, str(e)) from None
        store.add_vault_log(name, "added", f"service key for {', '.join(hosts)}")
        return store.credentials()

    @router.put("/api/credentials/{name}/secret", dependencies=[Depends(require_auth)])
    async def put_credential_secret(name: str, body: SecretIn) -> dict[str, Any]:
        try:
            secret = cred_clean.clean_secret(body.secret)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        item = store.credential(name)
        if not item or item["kind"] != "api":
            raise HTTPException(404, "no such connection")
        # a key the agent asked for is tried first when there is a check for it (a known service's own, or the one the agent gave)
        req = entries.open_for("key", name)
        outcome, detail = await check_key(item, secret, entries.check_url(req["id"]) if req else "", mcp_fetcher or netguard_fetch)
        if outcome == "rejected":
            store.add_vault_log(name, "rejected", f"tried before saving: {detail}; not saved", False)
            raise HTTPException(422, i18n.t("The service did not accept this key ({detail}). Nothing was saved; please check the key.",
                                            detail=detail))
        had = item["has_secret"]
        store.set_credential_secret(name, secret)
        spawn_sync(store, getattr(ctx, "keyring", None))
        if outcome == "ok":
            store.set_credential_result(name, "ok", "checked when it was added")
        store.add_vault_log(name, "replaced" if had else "saved", "service key" + (", checked with a real request" if outcome == "ok" else ""))
        got = entries.saved_full("key", name, {"checked": outcome})   # reply / request: as PUT /api/vault/{name}/login
        return {"ok": True, **({"checked": True} if outcome == "ok" else {}), "reply": got["reply"], "request": got["request"],
                **({"turn_id": got["turn_id"]} if got["turn_id"] else {})}

    @router.delete("/api/credentials/{name}", dependencies=[Depends(require_auth)])
    async def remove_credential(name: str) -> dict[str, Any]:
        """Disconnects a connection. "data" says what came from it that is still kept (connector_data.py); nothing is deleted here:
        the app asks, and POST /api/connectors/{id}/forget deletes it on the owner's word."""
        if not store.delete_credential(name):
            raise HTTPException(404, "no such connection")
        spawn_sync(store, getattr(ctx, "keyring", None))
        schedule_grants.drop_login(name)   # schedules that were allowed to use it ask again (or stop at the missing login)
        transient.discard(name)
        store.add_vault_log(name, "deleted", "removed by the user")
        for kind in ("key", "login"):
            entries.cancel_for(kind, name)
        return {"ok": True, "data": connector_data.summary(name)}

    # ---- services connected with OAuth's device flow (a code typed on the service's own page). The token goes into the vault; no endpoint returns it.
    def device_provider(name: str) -> str:
        if name not in DEVICE_PROVIDERS:
            raise HTTPException(404, "no such service")
        return name

    @router.get("/api/oauth/providers", dependencies=[Depends(require_auth)])
    async def get_oauth_providers() -> list[dict[str, Any]]:
        web = await github_web.available()
        return [{**device_flows.describe(p), "web": web if p == "github" else False} for p in DEVICE_PROVIDERS]

    @router.post("/api/github/web/start", dependencies=[Depends(require_auth)])
    async def start_github_web(body: WebStartIn) -> dict[str, str]:
        try:
            return {"url": await github_web.start(body.origin, body.scope)}
        except (GitHubWebError, McpOAuthError) as e:
            raise HTTPException(422, str(e)) from None

    # the operator's settings: the GitHub OAuth App every copy signs in with. The secret goes in, never out.
    @router.get("/api/oauth-apps/github", dependencies=[Depends(require_owner)])
    async def get_github_app() -> dict[str, Any]:
        return {"client_id": device_flows.client_id("github"), "has_secret": bool(store.key("oauth-app:github")), "is_broker": github_web.is_broker(),
                "callback": settings.oauth_relay, "broker": settings.oauth_broker}

    @router.put("/api/oauth-apps/github", dependencies=[Depends(require_owner)])
    async def put_github_app(body: GitHubAppIn) -> dict[str, Any]:
        try:
            github_web.set_app(body.client_id, body.client_secret)
        except (GitHubWebError, OAuthError) as e:
            raise HTTPException(422, str(e)) from None
        return await get_github_app()

    # ---- the broker: public, for the other copies of Kelo. It only turns a code into a token (with the secret) and keeps nothing.
    @router.get("/broker/github/client", include_in_schema=False)
    async def broker_client() -> dict[str, str]:
        if not github_web.is_broker():
            raise HTTPException(404, "this server is not a GitHub sign-in broker")
        return {"client_id": device_flows.client_id("github")}

    @router.post("/broker/github/exchange", include_in_schema=False)
    async def broker_exchange(body: ExchangeIn, request: Request) -> dict[str, Any]:
        if not github_web.is_broker():
            raise HTTPException(404, "this server is not a GitHub sign-in broker")
        if body.redirect_uri != settings.oauth_relay:
            raise HTTPException(422, "the sign-in must come back through this server's relay page")
        who = request.client.host if request.client else "?"
        now = time.monotonic()
        hits = broker_hits.setdefault(who, deque())
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= 20:
            raise HTTPException(429, "too many sign-ins from one place; try again in a minute")
        hits.append(now)
        try:
            tok = await github_web.exchange_here(body.code, body.redirect_uri, body.code_verifier)
        except GitHubWebError as e:
            raise HTTPException(502, str(e)) from None
        return {k: tok[k] for k in ("access_token", "scope", "token_type", "error", "error_description") if k in tok}

    ctx.add(device_provider=device_provider)
    return router
