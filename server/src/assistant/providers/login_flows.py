"""Sign in to a subscription provider from the phone, with no terminal (server side of /api/providers/{name}/login/...).

Every provider ends in one of two shapes the app knows:

- ``device_code`` (openai-codex, qwen-oauth, minimax-oauth): the app opens ``url`` and shows ``user_code`` to type there (Qwen's
  address already carries it). The server asks the provider whether it was approved each time the app asks for the status
  (GET .../login/status), so nothing runs in the background. Tokens go to the same 0600 store as the terminal login (oauth.py).
- ``url_code`` (claude, agy): the app opens ``url``; the page ends on a code, which the user pastes back (or pastes the whole
  address the page ended on). The CLI's own login runs in its sidecar (cli_login via cli_runner), or here when the server runs
  outside Docker; the CLI keeps its login in its own home as it does after a terminal login.

Sessions live in memory for their lifetime only (a restart forgets them; the app then starts again). Nothing here logs or returns
a token, a device secret or a pasted code.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..i18n import t
from . import cli_login, cli_manage, cli_remote, oauth
from .types import ProviderError

CLI_PROVIDERS = (cli_manage.CLAUDE, cli_manage.AGY)
SIGNIN_PROVIDERS = (*CLI_PROVIDERS, *oauth.OAUTH_PROVIDERS)
MAX_SESSIONS = 20


class _Flow:
    def __init__(self, provider: str, kind: str, url: str, user_code: str, expires_at: float, private: dict[str, Any]) -> None:
        self.id = secrets.token_urlsafe(18)
        self.provider = provider
        self.kind = kind
        self.url = url
        self.user_code = user_code
        self.expires_at = expires_at
        self.private = private          # device secrets, or the sidecar's own session id
        self.state = "pending"          # pending | done | failed | expired
        self.message = ""
        self.next_poll = 0.0
        self.lock = asyncio.Lock()

    def public(self, now: float) -> dict[str, Any]:
        return {"session": self.id, "provider": self.provider, "kind": self.kind, "url": self.url,
                **({"user_code": self.user_code} if self.user_code else {}),
                "expires_in": max(0, int(self.expires_at - now)) if self.state == "pending" else 0,
                "state": self.state, **({"message": self.message} if self.message else {})}


class LoginFlows:
    def __init__(self, *, http: Callable[[], httpx.AsyncClient] | None = None, now: Callable[[], float] = time.time,
                 local_cli: Callable[[str], cli_login.CliLogin] | None = None) -> None:
        self._http = http or (lambda: httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True))   # MiniMax answers with a redirect first
        self._now = now
        self._flows: dict[str, _Flow] = {}
        self._local: dict[str, cli_login.CliLogin] = {}
        self._make_local = local_cli or (lambda name: cli_login.CliLogin(name))

    # ---- where a CLI sign-in runs
    def _cli_where(self, provider: str) -> str:
        if cli_remote.remote_url(provider):
            return "sidecar"
        if cli_manage.in_container():
            raise cli_remote.SidecarOff(cli_remote.enable_hint(provider), status=503)
        return "local"

    def _local_login(self, provider: str) -> cli_login.CliLogin:
        if provider not in self._local:
            self._local[provider] = self._make_local(provider)
        return self._local[provider]

    def _gc(self) -> None:
        now = self._now()
        for fid, f in list(self._flows.items()):
            if f.state != "pending" and now > f.expires_at + 600 or now > f.expires_at + 3600:
                self._flows.pop(fid, None)
        while len(self._flows) > MAX_SESSIONS:
            self._flows.pop(next(iter(self._flows)))

    def _get(self, provider: str, sid: str) -> _Flow:
        f = self._flows.get(str(sid))
        if not f or f.provider != provider:
            raise ProviderError(t("This sign-in is no longer open. Start again"), status=404)
        if f.state == "pending" and self._now() > f.expires_at:
            f.state, f.message = "expired", t("The sign-in took too long. Start again")
        return f

    # ---- the API the server routes call
    async def start(self, provider: str, *, region: str = "global") -> dict[str, Any]:
        self._gc()
        for f in [f for f in self._flows.values() if f.provider == provider and f.state == "pending"]:
            await self._cancel_flow(f)        # one open sign-in per provider: a new start replaces the old one
        now = self._now()
        if provider in CLI_PROVIDERS:
            if self._cli_where(provider) == "sidecar":
                r = await cli_remote.login_start(provider)
            else:
                r = await self._local_login(provider).start()
            f = _Flow(provider, "url_code", str(r.get("url") or ""), "", now + float(r.get("expires_in") or cli_login.TTL),
                      {"cli_session": str(r.get("session") or "")})
        elif provider in oauth.OAUTH_PROVIDERS:
            async with self._http() as c:
                try:
                    d = await oauth.device_start(provider, c, region=region)
                except httpx.HTTPError as e:
                    raise ProviderError(t("Could not reach the sign-in service ({error})", error=type(e).__name__), status=502) from None
                except (KeyError, ValueError, TypeError):
                    raise ProviderError(t("The sign-in service gave an answer Kelo does not understand. Try again later"), status=502) from None
            f = _Flow(provider, "device_code", str(d["url"]), str(d.get("user_code") or ""), now + float(d["expires_in"]), d)
            f.next_poll = now + float(d.get("interval") or 5)
        else:
            raise ProviderError(f"{provider} has no sign-in", status=400)
        if not f.url.startswith("https://"):
            raise ProviderError(t("The sign-in service gave an answer Kelo does not understand. Try again later"), status=502)
        self._flows[f.id] = f
        return f.public(now)

    async def status(self, provider: str, sid: str) -> dict[str, Any]:
        f = self._get(provider, sid)
        if f.state == "pending" and f.kind == "device_code" and self._now() >= f.next_poll:
            await self._poll(f)
        return f.public(self._now())

    async def _poll(self, f: _Flow) -> None:
        async with f.lock:
            if f.state != "pending" or self._now() < f.next_poll:
                return
            interval = float(f.private.get("interval") or 5)
            try:
                async with self._http() as c:
                    res = await oauth.device_poll(f.provider, f.private, c)
            except oauth.OAuthError:
                f.state, f.message = "failed", t("The sign-in was not completed. Start again")
                f.private.clear()
                return
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                f.next_poll = self._now() + interval     # a passing network problem: ask again later
                return
            if res == "slow_down":
                f.private["interval"] = interval = interval + 5
            if res == "done":
                f.state = "done"
                f.private.clear()                         # the device secrets are not needed any more
            f.next_poll = self._now() + interval

    async def finish(self, provider: str, sid: str, code: str) -> dict[str, Any]:
        f = self._get(provider, sid)
        if f.kind == "device_code":                       # "I approved it": ask the provider now
            if f.state == "pending":
                f.next_poll = 0.0
                await self._poll(f)
            return f.public(self._now())
        if f.state == "done":
            return f.public(self._now())
        if f.state != "pending":
            raise ProviderError(f.message or t("This sign-in is no longer open. Start again"), status=410)
        value = cli_login.normalize_code(provider, code)   # checked here too, before it leaves this server
        async with f.lock:
            try:
                if self._cli_where(provider) == "sidecar":
                    await cli_remote.login_finish(provider, f.private["cli_session"], value)
                else:
                    await self._local_login(provider).finish(f.private["cli_session"], value)
            except ProviderError as e:
                if e.status in (404, 410):
                    f.state, f.message = "expired", str(e)
                raise
            f.state = "done"
            cli_manage.forget_status(provider)
        return f.public(self._now())

    async def cancel(self, provider: str, sid: str) -> dict[str, Any]:
        f = self._flows.get(str(sid))
        if not f or f.provider != provider:
            return {"ok": False}
        await self._cancel_flow(f)
        return {"ok": True}

    async def _cancel_flow(self, f: _Flow) -> None:
        self._flows.pop(f.id, None)
        if f.kind == "url_code" and f.state == "pending":
            try:
                if cli_remote.remote_url(f.provider):
                    await cli_remote.login_cancel(f.provider, f.private.get("cli_session", ""))
                elif f.provider in self._local:
                    await self._local[f.provider].cancel(f.private.get("cli_session", ""))
            except ProviderError:
                pass
        f.state = "expired"
        f.private.clear()
