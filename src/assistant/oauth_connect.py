"""Connect a service with OAuth's device flow: the app shows a short code, the user types it on the service's own page (on any device),
and the app receives an access token. It needs no callback address and no client secret, so it works for a copy of the app that runs
on someone's own computer behind a tunnel whose address changes.

The token goes straight into the vault as a service key for the service's API host; nothing here returns it, and the assistant never sees it.
Only the endpoints written below are ever called (they are fixed in code, not taken from a request).
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from .i18n import N_, t
from .store import Store
from .vault import VaultError

PostForm = Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]]
GetJson = Callable[[str, str], Awaitable[dict[str, Any]]]

CLIENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,63}$")
KEEP_FINISHED_SECONDS = 600.0
MIN_INTERVAL = 1.0   # never ask a service more often than this, whatever it says


@dataclass(frozen=True)
class Scope:
    id: str
    label: str
    detail: str
    scope: str


@dataclass(frozen=True)
class DeviceProvider:
    id: str                 # the connector's id
    item: str               # the name of the vault item that holds its token (not the id: a website login may already be called "github")
    name: str
    device_url: str
    verify_url: str         # where the user types the code
    token_url: str
    hosts: tuple[str, ...]  # the API hosts the token may be sent to
    scopes: tuple[Scope, ...]
    env_client_id: str      # an environment variable that may hold a client id for every user of this copy
    whoami_url: str
    whoami_field: str
    settings_url: str       # where the user can take the permission back
    help: tuple[str, ...]   # how to get a client id, in plain steps
    blurb: str              # what the user gets, for the connector list
    connectors: tuple[str, ...] = ()   # the catalog entries this connection serves (default: just `id`)
    at: str = "@"           # how the account name is shown after the service name
    default_client_id: str = ""   # Kelo's own public app, used when neither this copy's settings nor its environment name one

    @property
    def serves(self) -> tuple[str, ...]:
        return self.connectors or (self.id,)


PROVIDERS: dict[str, DeviceProvider] = {
    "github": DeviceProvider(
        id="github", item="github-api", name="GitHub",
        device_url="https://github.com/login/device/code",
        verify_url="https://github.com/login/device",
        token_url="https://github.com/login/oauth/access_token",
        hosts=("api.github.com",),
        scopes=(
            Scope("private", N_("Private repositories too"), N_("Reads my repositories (private ones too) and can open issues and PRs. I approve each write one by one."), "repo read:user"),
            Scope("public", N_("Public repositories only"), N_("Can read and write public repositories only."), "public_repo read:user"),
            Scope("profile", N_("Only my profile and public info"), N_("Can read only public repositories and cannot change anything."), "read:user"),
        ),
        env_client_id="ASSISTANT_GITHUB_CLIENT_ID",
        whoami_url="https://api.github.com/user", whoami_field="login",
        settings_url="https://github.com/settings/applications",
        blurb=N_("Reads your repositories, issues and PRs, and creates them when you allow it. Connect by typing a code once."),
        help=(N_("On GitHub, open Settings → Developer settings → OAuth Apps → New OAuth App."),
              N_("Any name works. For Homepage URL and Authorization callback URL, enter https://github.com (they are not used)."),
              N_("After creating it, turn on 'Enable Device Flow' and save."),
              N_("Copy the Client ID and paste it here. The client secret is not needed.")),
    ),
    "microsoft": DeviceProvider(
        id="microsoft", item="microsoft-api", name="Microsoft",
        device_url="https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
        verify_url="https://microsoft.com/devicelogin",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        hosts=("graph.microsoft.com",),
        scopes=(
            Scope("all", N_("Mail, calendar and contacts"), N_("Finds and reads mail and looks at events and contacts. I approve each send or change one by one."),
                  "offline_access User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Contacts.ReadWrite"),
            Scope("read", N_("Read only"), N_("Only reads mail, events and contacts."), "offline_access User.Read Mail.Read Calendars.Read Contacts.Read"),
        ),
        env_client_id="ASSISTANT_MICROSOFT_CLIENT_ID",
        whoami_url="https://graph.microsoft.com/v1.0/me", whoami_field="userPrincipalName",
        settings_url="https://account.live.com/consent/Manage",
        blurb=N_("Uses Outlook mail, calendar and contacts. Connect by typing a code once."),
        help=(N_("Open portal.azure.com → Microsoft Entra ID → App registrations → New registration."),
              N_("For supported account types, choose 'Accounts in any organizational directory and personal Microsoft accounts'. Leave the redirect URI empty."),
              N_("In the new app, go to Authentication → set 'Allow public client flows' to 'Yes' and save."),
              N_("Copy the 'Application (client) ID' from Overview and paste it here. No secret is needed.")),
        connectors=("outlook_mail", "outlook_calendar", "outlook_contacts"), at="",
        # Kelo's own Microsoft Entra app (any organisational directory + personal accounts, public client flows on, no secret):
        # a public client id is not a secret, so every copy can use it and nobody has to register an app of their own
        default_client_id="30e50ec1-d4d9-4e6c-a3e2-35a495c7b7f5",
    ),
}

FRIENDLY = {
    "device_flow_disabled": N_("Turn on 'Enable Device Flow' in the GitHub OAuth App settings."),
    "incorrect_client_credentials": N_("The Client ID is not right. Please check the Client ID of the GitHub OAuth App again."),
    "unauthorized_client": N_("This Client ID cannot use the code method. Please check that 'Enable Device Flow' is on."),
    "access_denied": N_("The connection was declined."),
    "authorization_declined": N_("The connection was declined."),
    "invalid_client": N_("The Client ID is not right, or 'Allow public client flows' is off."),
    "expired_token": N_("The code has expired. Please start over."),
}


@dataclass
class Flow:
    id: str
    provider: str
    user_code: str
    verification_uri: str
    expires_at: float
    interval: float
    status: str = "pending"       # pending | connected | denied | expired | error | cancelled
    message: str = ""
    account: str = ""
    finished: float = 0.0
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "provider": self.provider, "status": self.status, "message": self.message, "account": self.account,
                "user_code": self.user_code, "verification_uri": self.verification_uri,
                "expires_in": max(0, int(self.expires_at - time.time()))}


class OAuthError(Exception):
    """Something the user can act on; the text is shown in the app."""


async def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        r = await client.post(url, data=data, headers={"Accept": "application/json"})
    try:
        body = r.json()
    except ValueError:
        raise OAuthError(f"{url.split('/')[2]} answered with something unexpected (HTTP {r.status_code})") from None
    return body if isinstance(body, dict) else {}


async def _get_json(url: str, token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        r = await client.get(url, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
    body = r.json() if r.status_code == 200 else {}
    return body if isinstance(body, dict) else {}


def grant_label(store: Store, item: str) -> str:
    """The permission a typed-code connection (vault item `item`) was made with, in English, when it is narrower than the widest one its
    service offers (the first of its scopes): Microsoft "Read only", GitHub "Public repositories only". "" when it is the widest, not
    known, or `item` is not such a connection. vault.record_http uses it so a refused change is not taken for a wrong key."""
    p = next((p for p in PROVIDERS.values() if p.item == item), None)
    if p is None:
        return ""
    got = DeviceFlows(store).granted_scope(p.id)
    scope = next((s for s in p.scopes if s.id == got), None)
    return scope.label if scope is not None and scope.id != p.scopes[0].id else ""


class DeviceFlows:
    """Starts and follows device-flow connections. One background task per connection polls the service until the user has typed the code."""

    def __init__(self, store: Store, *, post_form: PostForm = _post_form, get_json: GetJson = _get_json, env: dict[str, str] | None = None,
                 keyring: Any = None):
        self.store, self.post_form, self.get_json = store, post_form, get_json
        self.env = env if env is not None else {}
        self.keyring = keyring
        self.flows: dict[str, Flow] = {}

    # ---- the client id: public, entered once in the app (or set for the whole copy in the environment)
    def client_id(self, provider: str) -> str:
        p = PROVIDERS[provider]
        return (self.store.meta(f"oauth_client_id.{provider}") or self.env.get(p.env_client_id, "") or p.default_client_id).strip()

    def set_client_id(self, provider: str, client_id: str) -> None:
        cid = (client_id or "").strip()
        if cid and not CLIENT_ID.match(cid):
            raise OAuthError(t("A Client ID is 6 to 64 letters and numbers. Please paste the value exactly as copied from GitHub."))
        self.store.set_meta(f"oauth_client_id.{provider}", cid)

    def describe(self, provider: str) -> dict[str, Any]:
        p = PROVIDERS[provider]
        item = self.store.credential(p.item)
        return {"id": p.id, "item": p.item, "name": p.name, "has_client_id": bool(self.client_id(provider)), "connected": bool(item and item["has_secret"]),
                "account": (item or {}).get("label", "") if item and item["has_secret"] else "", "settings_url": p.settings_url, "help": [t(h) for h in p.help],
                "scopes": [{"id": s.id, "label": t(s.label), "detail": t(s.detail)} for s in p.scopes],
                "granted_scope": self.granted_scope(provider) if item and item["has_secret"] else ""}

    def granted_scope(self, provider: str) -> str:
        """Which permission choice the stored connection was made with ("" = not known), so the app shows that one as chosen."""
        p = PROVIDERS[provider]
        got = self.store.meta(f"oauth_scope.{provider}")
        if not got:   # connections made before this was kept: the vault log says "OAuth connection (<id>)"
            rows = self.store._all("SELECT detail FROM vault_log WHERE item = ? AND action = 'saved' ORDER BY id DESC LIMIT 1", (p.item,))
            m = re.search(r"\(([\w-]+)\)$", (rows[0]["detail"] if rows else "") or "")
            got = m.group(1) if m else ""
        return got if any(s.id == got for s in p.scopes) else ""

    # ---- a connection in progress
    def sweep(self) -> None:
        now = time.time()
        for f in list(self.flows.values()):
            if f.status != "pending" and now - f.finished > KEEP_FINISHED_SECONDS:
                self.flows.pop(f.id, None)

    async def start(self, provider: str, scope_id: str = "") -> Flow:
        p = PROVIDERS[provider]
        cid = self.client_id(provider)
        if not cid:
            raise OAuthError(t("First enter the Client ID of the {name} OAuth App.", name=p.name))
        scope = next((s for s in p.scopes if s.id == scope_id), p.scopes[0])
        self.sweep()
        for f in list(self.flows.values()):    # one at a time per service: asking again replaces the earlier code
            if f.provider == provider and f.status == "pending":
                self.cancel(f.id)
        try:
            reply = await self.post_form(p.device_url, {"client_id": cid, "scope": scope.scope})
        except (httpx.HTTPError, OAuthError) as e:
            raise OAuthError(t("Cannot reach {name}: {error}", name=p.name, error=type(e).__name__)) from None
        if "device_code" not in reply or "user_code" not in reply:
            err = str(reply.get("error", ""))
            raise OAuthError(t(FRIENDLY[err]) if err in FRIENDLY else t("{name} did not give a code{detail}.", name=p.name, detail=f" ({err})" if err else ""))
        said = str(reply.get("verification_uri") or "")
        # the page the user is sent to must be on the service's own site, whatever the answer says
        page = said if said.startswith("https://" + urlsplit(p.device_url).hostname + "/") else p.verify_url
        flow = Flow(id=secrets.token_hex(12), provider=provider, user_code=str(reply["user_code"])[:32], verification_uri=page,
                    expires_at=time.time() + float(reply.get("expires_in", 900)), interval=max(MIN_INTERVAL, float(reply.get("interval", 5))))
        self.flows[flow.id] = flow
        self.store.add_vault_log(p.item, "requested", f"OAuth connection started ({scope.id})")
        flow.task = asyncio.create_task(self._poll(flow, cid, str(reply["device_code"]), scope))
        return flow

    def get(self, flow_id: str) -> Flow | None:
        self.sweep()
        return self.flows.get(flow_id)

    def cancel(self, flow_id: str) -> bool:
        flow = self.flows.get(flow_id)
        if not flow or flow.status != "pending":
            return False
        self._end(flow, "cancelled", t("Cancelled."))
        if flow.task and not flow.task.done():
            flow.task.cancel()
        return True

    def _end(self, flow: Flow, status: str, message: str = "") -> None:
        flow.status, flow.message, flow.finished = status, message, time.time()

    async def _poll(self, flow: Flow, client_id: str, device_code: str, scope: Scope) -> None:
        p = PROVIDERS[flow.provider]
        interval = flow.interval
        try:
            while time.time() < flow.expires_at:
                await asyncio.sleep(interval)
                try:
                    reply = await self.post_form(p.token_url, {"client_id": client_id, "device_code": device_code,
                                                               "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
                except (httpx.HTTPError, OAuthError):
                    continue                    # a hiccup on the way: ask again after the interval
                token = reply.get("access_token")
                if token:
                    await self._save(flow, p, str(token), scope, reply, client_id)
                    return
                err = str(reply.get("error", ""))
                if err == "authorization_pending":
                    continue
                if err == "slow_down":
                    interval = max(MIN_INTERVAL, float(reply.get("interval") or interval + 5))
                    continue
                if err in ("access_denied", "authorization_declined"):
                    self._end(flow, "denied", t(FRIENDLY["access_denied"]))
                elif err == "expired_token":
                    self._end(flow, "expired", t(FRIENDLY["expired_token"]))
                else:
                    self._end(flow, "error", t(FRIENDLY[err]) if err in FRIENDLY else
                              t("{name} could not finish the connection{detail}.", name=p.name, detail=f" ({err})" if err else ""))
                self.store.add_vault_log(p.item, "failed", f"OAuth connection: {err or 'no answer'}", ok=False)
                return
            self._end(flow, "expired", t(FRIENDLY["expired_token"]))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - never leave a connection spinning
            self._end(flow, "error", t("Something went wrong while connecting: {error}", error=type(e).__name__))

    async def store_token(self, provider: str, token: str, scope_id: str, reply: dict[str, Any] | None = None, client_id: str = "") -> str:
        """Keep a token the service gave (by a typed code or by a sign-in) in the vault. Returns the account name it belongs to."""
        p = PROVIDERS[provider]
        account = ""
        try:
            account = str((await self.get_json(p.whoami_url, token)).get(p.whoami_field, ""))[:60]
        except (httpx.HTTPError, ValueError):
            pass
        label = f"{p.name} · {p.at}{account}" if account else p.name
        self.store.save_credential(p.item, label, list(p.hosts), {"type": "bearer_header"}, "", kind="api")   # VaultError: another kind holds the name
        if reply and reply.get("refresh_token"):   # a token that runs out (Microsoft: an hour) is kept with what renews it
            from .mcp_oauth import bundle
            self.store.set_credential_secret(p.item, json.dumps(bundle({"token_endpoint": p.token_url, "client_id": client_id, "resource": ""}, reply)))
        else:
            self.store.set_credential_secret(p.item, token)
        self.store.set_credential_result(p.item, None)
        self.store.add_vault_log(p.item, "saved", f"OAuth connection ({scope_id})")
        from .cred_keyring import spawn_sync
        spawn_sync(self.store, self.keyring)
        self.store.set_meta(f"oauth_scope.{provider}", scope_id)
        return account

    async def _save(self, flow: Flow, p: DeviceProvider, token: str, scope: Scope, reply: dict[str, Any] | None = None, client_id: str = "") -> None:
        try:
            flow.account = await self.store_token(p.id, token, scope.id, reply, client_id)
        except VaultError:
            self._end(flow, "error", t("The vault already has another item named '{item}'. Delete that item and connect again.", item=p.item))
            return
        self._end(flow, "connected", "")
