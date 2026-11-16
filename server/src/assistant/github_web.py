"""GitHub with a sign-in button: the user signs in on GitHub's own page (user name, password, two-step check there), presses Authorize,
and comes back to their own Kelo. Nobody else has to register anything.

How: the operator registers ONE GitHub OAuth App. Its client id is public; its client secret stays on the operator's server (the
"broker"), which is the only place that turns a returning code into a token. Every other copy of Kelo asks the broker to do that one
step and keeps the token itself. GitHub sends every sign-in back to the shared relay page, which forwards it to the copy that started
it (named in `state`). PKCE binds the code to the copy that started the sign-in, so a code that leaks on the way is useless.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from .i18n import t
from .mcp_oauth import clean_origin, relay_state
from .netguard import NetGuardError, Reply, fetch
from .oauth_connect import PROVIDERS, DeviceFlows
from .store import Store
from .vault import VaultError

Fetcher = Callable[..., Awaitable[Reply]]
AUTHORIZE = "https://github.com/login/oauth/authorize"
TOKEN = "https://github.com/login/oauth/access_token"
SECRET = "oauth-app:github"          # the client secret, encrypted like the AI service keys (only on the broker)
PENDING_SECONDS = 900.0


class GitHubWebError(Exception):
    """Shown to the user as it is."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class GitHubWeb:
    def __init__(self, store: Store, flows: DeviceFlows, *, relay: str = "", broker: str = "", fetcher: Fetcher = fetch):
        self.store, self.flows, self.relay, self.fetcher = store, flows, relay.strip(), fetcher
        self.broker = broker.strip().rstrip("/")
        self.pending: dict[str, dict[str, Any]] = {}
        self._remote_id: tuple[str, float] = ("", 0.0)

    # ---- the operator's side
    def is_broker(self) -> bool:
        return bool(self.store.key(SECRET) and self.flows.client_id("github"))

    def set_app(self, client_id: str | None, client_secret: str | None) -> None:
        if client_id is not None:
            self.flows.set_client_id("github", client_id)
        if client_secret is not None:
            sec = client_secret.strip()
            if sec and (len(sec) < 20 or len(sec) > 100 or not sec.isalnum()):
                raise GitHubWebError(t("The client secret is about 40 letters and numbers, copied from GitHub."))
            if sec:
                self.store.set_key(SECRET, sec)
            else:
                self.store.delete_key(SECRET)

    async def exchange_here(self, code: str, redirect_uri: str, verifier: str) -> dict[str, Any]:
        """The broker's one job: code -> token with the secret. Nothing is kept."""
        secret = self.store.key(SECRET)
        cid = self.flows.client_id("github")
        if not secret or not cid:
            raise GitHubWebError(t("This server has no GitHub app secret."))
        form = {"client_id": cid, "client_secret": secret, "code": code, "redirect_uri": redirect_uri}
        if verifier:
            form["code_verifier"] = verifier
        return await self._post_json(TOKEN, form, form_encoded=True)

    # ---- every copy's side
    async def client_id(self) -> str:
        own = self.flows.client_id("github")
        if own or not self.broker:
            return own
        cid, at = self._remote_id
        if cid and time.time() - at < 3600:
            return cid
        try:
            r = await self.fetcher("GET", f"{self.broker}/broker/github/client", allowed_hosts=[urlsplit(self.broker).hostname or ""],
                                   headers={"Accept": "application/json"})
            cid = str(json.loads(r.body).get("client_id") or "") if r.status == 200 else ""
        except (NetGuardError, ValueError):
            cid = ""
        self._remote_id = (cid, time.time())
        return cid

    async def available(self) -> bool:
        return bool(self.relay and (self.is_broker() or self.broker) and await self.client_id())

    async def start(self, origin: str, scope_id: str = "") -> str:
        if not self.relay:
            raise GitHubWebError(t("This server has no fixed address (sign-in relay) set up. Please connect with the code method."))
        cid = await self.client_id()
        if not cid:
            raise GitHubWebError(t("GitHub sign-in is not ready yet. Please connect with the code method."))
        p = PROVIDERS["github"]
        scope = next((s for s in p.scopes if s.id == scope_id), p.scopes[0])
        here = clean_origin(origin)
        verifier = _b64(secrets.token_bytes(32))
        state = relay_state(_b64(secrets.token_bytes(24)), here)
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v["created"] < PENDING_SECONDS}
        self.pending[state] = {"verifier": verifier, "scope": scope.id, "origin": here, "created": now, "client_id": cid}
        self.store.add_vault_log(p.item, "requested", f"GitHub sign-in started ({scope.id})")
        return AUTHORIZE + "?" + urlencode({"client_id": cid, "redirect_uri": self.relay, "state": state, "scope": scope.scope,
                                             "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
                                             "allow_signup": "true"})

    def owns(self, state: str) -> bool:
        return state in self.pending

    async def finish(self, state: str, code: str = "", error: str = "") -> tuple[str, str]:
        """(the app's address to go back to, a problem to show or "")."""
        p = self.pending.pop(state, None)
        if p is None:
            return "", t("This connection request has expired or was already handled. Please connect again from the app.")
        if error or not code:
            self.store.add_vault_log("github-api", "failed", f"GitHub sign-in: {(error or 'no code')[:60]}", ok=False)
            return p["origin"], "access_denied" if error == "access_denied" else (error or "no code")
        try:
            if self.is_broker():
                tok = await self.exchange_here(code, self.relay, p["verifier"])
            else:
                tok = await self._post_json(f"{self.broker}/broker/github/exchange",
                                            {"code": code, "redirect_uri": self.relay, "code_verifier": p["verifier"]})
        except GitHubWebError as e:
            return p["origin"], str(e)
        token = tok.get("access_token")
        if not token:
            self.store.add_vault_log("github-api", "failed", f"GitHub token: {str(tok.get('error') or 'none')[:60]}", ok=False)
            return p["origin"], t("GitHub did not give a token ({detail}).", detail=str(tok.get('error_description') or tok.get('error') or t("unknown"))[:80])
        try:
            await self.flows.store_token("github", str(token), p["scope"], tok, p["client_id"])
        except VaultError:
            return p["origin"], t("The vault has another item named 'github-api'. Delete that item and connect again.")
        return p["origin"], ""

    async def _post_json(self, url: str, data: dict[str, str], *, form_encoded: bool = False) -> dict[str, Any]:
        headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" if form_encoded else "application/json"}
        content = (urlencode(data) if form_encoded else json.dumps(data)).encode()
        try:
            r = await self.fetcher("POST", url, allowed_hosts=[urlsplit(url).hostname or ""], headers=headers, content=content)
        except NetGuardError as e:
            raise GitHubWebError(t("Could not connect: {error}", error=e)) from None
        try:
            body = json.loads(r.body)
        except ValueError:
            raise GitHubWebError(t("Could not read the answer (HTTP {status}).", status=r.status)) from None
        if not isinstance(body, dict):
            raise GitHubWebError(t("The answer looks wrong."))
        if r.status >= 400 and "access_token" not in body:
            raise GitHubWebError(str(body.get("detail") or body.get("error_description") or body.get("error") or f"HTTP {r.status}")[:120])
        return body
