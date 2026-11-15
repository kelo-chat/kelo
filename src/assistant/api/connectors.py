"""Connectors: the list with how each connects (connectors.py, connect_ways.py), their icons, keys and tokens made on a service (keyconnect.py), hosted MCP servers signed in on their own page and the OAuth return pages (mcp_oauth.py)."""

from __future__ import annotations

import json
from html import escape as escape_html
from urllib.parse import urlsplit
from typing import Any
from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from .. import i18n
from .. import connector_icons
from ..connectors import connector_rows, regions_for
from ..mail import MailError
from ..mcp_oauth import (CLIENT_METADATA_PATH, McpOAuthError, client_metadata, client_metadata_url, relay_target)
from ..connect_ways import ways as connect_ways
from ..google_web import GoogleWebError, email_of
from ..google_calendar import CalendarError
from ..keyconnect import KeyError_, preset as key_preset
from ..packages import ICON_PREFIX as PACKAGE_ICON_PREFIX
from ..presets import NETWORK_IDS, public as preset_public
from ..permissions import Info, PermissionsError, family_of, flat_body
from ..effects import preset_sends
from .. import landing
from fastapi import APIRouter
from .context import AppContext


class KeyValuesIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict, max_length=6)
    force: bool = False        # VPN / Proxy: change the route now even if the agent is browsing (an open purchase still waits)


class VpnApplyIn(BaseModel):
    force: bool = False


class OriginIn(BaseModel):
    origin: str = Field(max_length=300)


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    beta = ctx.beta
    gcal = ctx.gcal
    github_web = ctx.github_web
    google_web = ctx.google_web
    keys = ctx.keys
    mail = ctx.mail
    mcp_conn = ctx.mcp_conn
    permissions = ctx.permissions
    ways_table = ctx.ways_table
    packages = ctx.packages
    require_auth = ctx.require_auth
    settings = ctx.settings
    store = ctx.store
    tools = ctx.tools
    vpn = ctx.vpn

    @router.get("/api/connectors", dependencies=[Depends(require_auth)])
    async def get_connectors(request: Request) -> list[dict[str, Any]]:
        """Rows and the featured rules: connectors.py (worldwide FEATURED first, then services of the device's region)."""
        await google_web.client_id()   # a copy that leans on the broker reads the broker's switch with it (cached for an hour)
        await beta.refresh()           # and whether its beta request was approved (beta.py; cached a few minutes)
        if vpn.active():
            await vpn.status()         # "exit <ip>" on the VPN row (checked at most every 30 s)
        table = connect_ways(store, mail, browser=any(t.name.startswith("browser_") for t in tools), google_open=google_web.open_here(),
                             registry=packages, relay=settings.oauth_relay, vpn=vpn)
        rows = connector_rows(table, regions=regions_for(request.headers.get("accept-language")), registry=packages)
        for r in rows:   # anything the owner limited for it (permissions.py)
            r["limited"] = False if r["id"] in NETWORK_IDS else permissions.limited(r["id"], info_of(r["id"], table.get(r["id"])))
        return rows

    def info_of(cid: str, row: dict[str, Any] | None) -> Info:
        pkg = next((p for p in packages.installed() if p.id == cid), None)
        return Info(cid, permissions.names(cid), family_of(cid, row, package=pkg), preset_sends(cid))

    def need_info(cid: str) -> Info:
        if cid in NETWORK_IDS:   # the VPN / Proxy connectors set a route; there is nothing to allow or ask
            raise HTTPException(404, "this connector has no permissions")
        row = ways_table().get(cid)
        pkg = next((p for p in packages.installed() if p.id == cid), None)
        if cid != "phone" and row is None and not (pkg is not None and pkg.store):
            raise HTTPException(404, "no such connector")
        return info_of(cid, row)

    @router.get("/api/connectors/{cid}/permissions", dependencies=[Depends(require_auth)])
    async def get_permissions(cid: str) -> dict[str, Any]:
        """What Kelo may do with this connector (permissions.py has the contract)."""
        return permissions.describe(need_info(cid))

    @router.put("/api/connectors/{cid}/permissions", dependencies=[Depends(require_auth)])
    async def put_permissions(cid: str, body: dict[str, Any]) -> dict[str, Any]:
        info = need_info(cid)
        try:
            permissions.put(info, flat_body(body, permissions.describe(info)))
        except PermissionsError as e:
            raise HTTPException(422, str(e)) from None
        return permissions.describe(info)

    @router.delete("/api/connectors/{cid}/permissions", dependencies=[Depends(require_auth)])
    async def reset_permissions(cid: str) -> dict[str, Any]:
        info = need_info(cid)
        permissions.reset(cid)
        return permissions.describe(info)

    @router.get("/api/connectors/icons/{name}.png", include_in_schema=False)
    async def connector_icon(name: str, request: Request) -> Response:
        """A service's app icon (connector_icons.py). Public: the same picture for everyone, nothing about this server in it."""
        got = packages.icon(name) if name.startswith(PACKAGE_ICON_PREFIX) else connector_icons.read(name)
        if got is None:
            raise HTTPException(404, "not found")
        data, tag = got
        # a link with the current ?v= may be kept for a year; anything else is checked again after a day
        cache = "public, max-age=31536000, immutable" if request.query_params.get("v") == tag else "public, max-age=86400"
        headers = {**connector_icons.HEADERS, "ETag": f'"{tag}"', "Cache-Control": cache}
        if f'"{tag}"' in (request.headers.get("if-none-match") or ""):
            return Response(status_code=304, headers=headers)
        return Response(data, media_type="image/png", headers=headers)

    # ---- keys and tokens the user makes on a service (and Zoom apps, Google service accounts): checked with one real request, then sealed
    @router.get("/api/keys/{preset_id}", dependencies=[Depends(require_auth)])
    async def get_key_preset(preset_id: str) -> dict[str, Any]:
        try:
            p = key_preset(preset_id, packages)
        except KeyError_:
            raise HTTPException(404, "no such service") from None
        c = store.credential(p.item)
        return {**preset_public(p), "connected": bool(c and c["has_secret"]), "label": c["label"] if c and c["has_secret"] else ""}

    @router.post("/api/keys/{preset_id}", dependencies=[Depends(require_auth)])
    async def connect_key(preset_id: str, body: KeyValuesIn) -> dict[str, Any]:
        try:
            key_preset(preset_id, packages)
        except KeyError_:
            raise HTTPException(404, "no such service") from None
        try:
            return await keys.connect(preset_id, body.values, force=body.force)
        except KeyError_ as e:
            raise HTTPException(422, str(e)) from None

    # ---- the VPN / Proxy connectors (vpn.py): how Kelo's web browsing goes out now, and "switch now" for a change that waits for the browser
    @router.get("/api/vpn", dependencies=[Depends(require_auth)])
    async def get_vpn() -> dict[str, Any]:
        """{"available", "mode": direct|wireguard|proxy, "connected", "exit_ip"?, "pending"?: the kind of a change waiting for the browser}"""
        return {"available": vpn.available, **(await vpn.status(fresh=True) if vpn.available else {"mode": "direct", "connected": False})}

    @router.post("/api/vpn/apply", dependencies=[Depends(require_auth)])
    async def apply_vpn(body: VpnApplyIn) -> dict[str, Any]:
        """Makes the change the vault asks for (or the one waiting for the browser). `force`: even if the agent is browsing; an open purchase still waits."""
        if not vpn.available:
            raise HTTPException(404, "no VPN support in this installation")
        got = await vpn.reconcile(force=body.force)
        return got or {"state": "applied", "message": ""}

    # ---- hosted MCP servers signed in on the service's own page (OAuth with automatic registration). Tokens go into the vault.
    @router.post("/api/mcp-oauth/{service}/start", dependencies=[Depends(require_auth)])
    async def start_mcp_oauth(service: str, body: OriginIn) -> dict[str, str]:
        if packages.mcp_service(service) is None:
            raise HTTPException(404, "no such service")
        try:
            return {"url": await mcp_conn.start(service, body.origin)}
        except McpOAuthError as e:
            raise HTTPException(422, str(e)) from None

    @router.get("/oauth/relay", include_in_schema=False)
    async def oauth_relay(request: Request) -> Response:
        """The one fixed return address that services know. It sends a returning sign-in on to the copy of the app that started it,
        named in `state`; nothing is kept here, and the code is useless without the secret the starting copy holds (PKCE)."""
        target = relay_target(request.query_params.get("state", ""))
        if not target:
            return Response("<!doctype html><meta charset=utf-8><p>" + escape_html(i18n.t("It is not clear where this connection request should go. Connect again from the app.")) + "</p>",
                            status_code=400, media_type="text/html", headers={"Cache-Control": "no-store"})
        return Response(status_code=302, headers={"Location": f"{target}/oauth/callback?{request.url.query}", "Cache-Control": "no-store",
                                                  "Referrer-Policy": "no-referrer"})

    @router.get(CLIENT_METADATA_PATH, include_in_schema=False)
    async def oauth_client_metadata(request: Request) -> Response:
        """Kelo's Client ID Metadata Document: public (services fetch it without a token), and only on the server that holds the shared
        relay page (the operator's: its public address and the relay share a host), since the document's address is the client_id and its
        return address is that relay. Every copy uses this one document (mcp_oauth.client_metadata_url)."""
        url = client_metadata_url(settings.oauth_relay)
        public = urlsplit(settings.public_url or "")
        # the relay's host is this server when either its public address or the address this request came in on is that host
        # (a server reached by two names while it moves to a new one serves the document on the relay's name)
        asked = (request.headers.get("host") or "").lower()
        host = urlsplit(url).netloc if url else ""
        if not url or not ((public.scheme == "https" and (public.netloc or "").lower() == host) or asked == host):
            raise HTTPException(404, "this server does not publish a client metadata document")
        return Response(json.dumps(client_metadata(settings.oauth_relay)), media_type="application/json",
                        headers={"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"})

    @router.get("/oauth/callback", include_in_schema=False)
    async def oauth_callback(state: str = "", code: str = "", error: str = "") -> Response:
        """Where the service sends the user back. No token is needed: the one-time `state` says which request this answers."""
        if github_web.owns(state):
            origin, problem = await github_web.finish(state, code, error)
            name = "GitHub"
        elif google_web.owns(state):
            product = google_web.product_of(state)
            name = "Google Calendar" if product == "calendar" else "Gmail"
            if error or not code:
                origin, problem = google_web.forget(state), ("access_denied" if error == "access_denied" else (error or "no code"))
            else:
                origin = google_web.pending[state]["origin"]
                try:
                    tok, _ = await google_web.exchange(state, code)
                    if not tok.get("access_token"):
                        raise GoogleWebError(i18n.t("Google did not give a token ({detail}).",
                                              detail=str(tok.get('error_description') or tok.get('error') or i18n.t("unknown"))[:80]))
                    if product == "calendar":
                        await gcal.connect(email_of(str(tok.get("id_token") or "")), tok)
                    else:
                        await mail.connect_oauth("gmail", email_of(str(tok.get("id_token") or "")), tok)
                    problem = ""
                except (GoogleWebError, MailError, CalendarError) as e:
                    problem = str(e)
        else:
            svc, origin, problem = await mcp_conn.finish(state, code, error)
            name = svc.name if svc else i18n.t("Service")
        if not problem:
            title, text = i18n.t("{name} connected", name=name), i18n.t("{name} is connected. Go back to the app and you can use it right away.", name=name)
        elif problem == "access_denied":
            title, text = i18n.t("Connection canceled"), i18n.t("Connecting {name} was canceled. Nothing was saved.", name=name)
        else:
            title, text = i18n.t("Couldn't connect"), problem
        page = landing.page(title, text, button=i18n.t("Back to the Kelo app"), auto_open=not problem)
        return Response(page, media_type="text/html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    return router
