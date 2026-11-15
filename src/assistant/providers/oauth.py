"""OAuth for subscription providers: openai-codex, minimax-oauth, qwen-oauth.

Tokens live in one 0600 JSON file (``$ASSISTANT_OAUTH_FILE``, else ``$ASSISTANT_HOME/oauth.json``, else ``~/.assistant/oauth.json``):
``{"openai-codex": {...}, "minimax-oauth": {...}, "qwen-oauth": {...}}``.
Login:   ``python -m assistant.providers.oauth login <provider> [--region cn]``
Runtime: ``await resolve(provider_name)`` -> ``(base_url_or_None, api_key, extra_headers)``;
         refreshes near-expiry tokens (refresh tokens rotate, so refreshes are serialized).

Qwen additionally falls back to / imports ``~/.qwen/oauth_creds.json`` (Qwen CLI) if present.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import platform
import secrets
import sys
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

try:
    import fcntl
except ImportError:  # Windows: no cross-process lock, in-process lock only
    fcntl = None  # type: ignore[assignment]

from . import releases
from .types import ProviderError

OAUTH_AUTH_TYPES = {"oauth_external", "oauth_codex", "oauth_minimax", "oauth_qwen"}
OAUTH_PROVIDERS = ("openai-codex", "minimax-oauth", "qwen-oauth")

# ---- constants (from Hermes auth_constants.py, NousResearch/hermes-agent, MIT, Copyright (c) 2025 Nous Research; see NOTICE) ----
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_ISSUER = "https://auth.openai.com"
CODEX_TOKEN_URL = f"{CODEX_ISSUER}/oauth/token"
CODEX_SKEW = 120
CODEX_ORIGINATOR = "codex_cli_rs"   # the official CLI's default `originator` (DEFAULT_ORIGINATOR)

MINIMAX_CLIENT_ID = "78257093-7e40-4613-99e0-527b14b39113"
MINIMAX_SCOPE = "group_id profile model.completion"
MINIMAX_GRANT = "urn:ietf:params:oauth:grant-type:user_code"
MINIMAX_REGIONS = {
    "global": ("https://api.minimax.io", "https://api.minimax.io/anthropic"),
    "cn": ("https://api.minimaxi.com", "https://api.minimaxi.com/anthropic"),
}
MINIMAX_SKEW = 60

QWEN_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"
QWEN_BASE = "https://chat.qwen.ai"
QWEN_TOKEN_URL = f"{QWEN_BASE}/api/v1/oauth2/token"
QWEN_DEVICE_URL = f"{QWEN_BASE}/api/v1/oauth2/device/code"
QWEN_SCOPE = "openid profile email model.completion"
QWEN_SKEW = 120
QWEN_CODE_VERSION = "0.14.1"  # Qwen Portal expects a QwenCode-looking client

_FORM = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()
_flock_depth = 0  # reentrancy guard for the cross-process file lock
LOCK_TIMEOUT = 30.0


class OAuthError(ProviderError):
    """Not logged in / refresh failed. Message says how to fix it."""


def store_path() -> Path:
    """$ASSISTANT_OAUTH_FILE, else oauth.json in $ASSISTANT_HOME (the writable /state volume in Docker)."""
    if explicit := os.environ.get("ASSISTANT_OAUTH_FILE"):
        return Path(explicit)
    return Path(os.environ.get("ASSISTANT_HOME") or Path.home() / ".assistant") / "oauth.json"


def _qwen_cli_path() -> Path:
    return Path.home() / ".qwen" / "oauth_creds.json"


def _load() -> dict[str, Any]:
    p = store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise OAuthError(f"cannot read {p}: {e}") from e
    return data if isinstance(data, dict) else {}


def _write_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _loop_lock() -> asyncio.Lock:
    """In-process refresh lock, one per event loop (an asyncio.Lock must not cross loops)."""
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


def _open_lockfile() -> int:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return os.open(p.with_name(p.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)


@contextlib.contextmanager
def _file_lock() -> Iterator[None]:
    """Cross-process exclusive lock (flock) around read-modify-write of the store. Reentrant."""
    global _flock_depth
    if _flock_depth or fcntl is None:
        _flock_depth += 1
        try:
            yield
        finally:
            _flock_depth -= 1
        return
    fd = _open_lockfile()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _flock_depth = 1
        try:
            yield
        finally:
            _flock_depth = 0
    finally:
        os.close(fd)  # closing releases the lock


@contextlib.asynccontextmanager
async def _afile_lock() -> AsyncIterator[None]:
    """Like _file_lock but polls so it never blocks the event loop."""
    global _flock_depth
    if _flock_depth or fcntl is None:
        _flock_depth += 1
        try:
            yield
        finally:
            _flock_depth -= 1
        return
    fd = _open_lockfile()
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise OAuthError(f"timed out waiting for {store_path()} lock (another process refreshing?)") from None
                await asyncio.sleep(0.05)
        _flock_depth = 1
        try:
            yield
        finally:
            _flock_depth = 0
    finally:
        os.close(fd)


def _save(provider: str, state: dict[str, Any]) -> None:
    with _file_lock():
        data = _load()
        data[provider] = state
        _write_private(store_path(), data)


def logout(provider: str) -> bool:
    """Remove the stored sign-in for one provider. True when something was removed.

    For qwen-oauth the Qwen CLI's own token file is removed too: the server reads it as a sign-in (and rewrites it on
    refresh), so leaving it would keep the provider signed in."""
    with _file_lock():
        data = _load()
        found = data.pop(provider, None) is not None
        if found:
            _write_private(store_path(), data)
        if provider == "qwen-oauth":
            cli = _qwen_cli_path()
            try:
                cli.unlink()
                found = True
            except FileNotFoundError:
                pass
    return found


def _account(name: str, st: dict[str, Any]) -> str | None:
    """The signed-in account's e-mail when the stored tokens say it (never a token). Only Codex' JWT carries one."""
    if name != "openai-codex":
        return None
    claims = _jwt_claims(str((st.get("tokens") or {}).get("access_token") or ""))
    if not isinstance(claims, dict):
        return None
    prof = claims.get("https://api.openai.com/profile")
    email = (prof.get("email") if isinstance(prof, dict) else None) or claims.get("email")
    return email[:120] if isinstance(email, str) and "@" in email else None


def status() -> dict[str, dict[str, Any]]:
    """Per-provider login state, no secrets."""
    out: dict[str, dict[str, Any]] = {}
    data = _load()
    for name in OAUTH_PROVIDERS:
        st = data.get(name)
        if not st and name == "qwen-oauth" and _qwen_cli_path().exists():
            out[name] = {"logged_in": True, "source": "qwen-cli"}
        else:
            logged = bool(st and st.get("access_token") or st and st.get("tokens", {}).get("access_token"))
            out[name] = {"logged_in": logged, **({"account": a} if logged and (a := _account(name, st)) else {})}
    return out


# ---------------------------------------------------------------- helpers

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def _jwt_exp(token: str) -> float:
    exp = _jwt_claims(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else 0.0


def _err_body(r: httpx.Response) -> str:
    return r.text[:300].replace("\n", " ")


def _qwen_headers() -> dict[str, str]:
    ua = f"QwenCode/{QWEN_CODE_VERSION} ({platform.system().lower()}; {platform.machine()})"
    return {"User-Agent": ua, "X-DashScope-CacheControl": "enable", "X-DashScope-UserAgent": ua,
            "X-DashScope-AuthType": "qwen-oauth"}


def codex_user_agent(version: str) -> str:
    """The official client's format (codex-rs login/src/auth/default_client.rs `get_codex_user_agent`):
    `<originator>/<version> (<os type> <os version>; <arch>) <terminal>` plus an optional ` (<suffix>)` naming the embedding app."""
    os_type = {"Darwin": "Mac OS", "Windows": "Windows"}.get(platform.system(), platform.system() or "Linux")
    ua = f"{CODEX_ORIGINATOR}/{version} ({os_type} {platform.release() or 'unknown'}; {platform.machine() or 'unknown'}) unknown (assistant)"
    return "".join(ch if " " <= ch <= "~" else "_" for ch in ua)


def codex_headers(access_token: str, version: str | None = None) -> dict[str, str]:
    """Identity + workspace headers for chatgpt.com/backend-api/codex (from the JWT). `version`: the Codex client version to present,
    the same one the model list was asked with (releases.codex_version()); ChatGPT offers a model only to clients new enough for it."""
    h = {"User-Agent": codex_user_agent(version or releases.CODEX_PINNED), "originator": CODEX_ORIGINATOR}
    auth = _jwt_claims(access_token).get("https://api.openai.com/auth") or {}
    if isinstance(auth.get("chatgpt_account_id"), str) and auth["chatgpt_account_id"]:
        h["ChatGPT-Account-ID"] = auth["chatgpt_account_id"]
    res = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
    if isinstance(res, str) and res.strip():
        h["x-openai-internal-codex-residency"] = res.strip()
    return h


# ---------------------------------------------------------------- refresh (async, runtime)

async def _refresh_codex(st: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
    tokens = st.get("tokens") or {}
    rt = tokens.get("refresh_token")
    if not rt:
        raise OAuthError("openai-codex: no refresh token; run `python -m assistant.providers.oauth login openai-codex`")
    r = await client.post(CODEX_TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"},
                          data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": CODEX_CLIENT_ID})
    if r.status_code == 429:
        raise OAuthError("openai-codex: token endpoint rate-limited (429); retry later", status=429)
    if r.status_code != 200:
        raise OAuthError(f"openai-codex: refresh failed (HTTP {r.status_code}); re-login. {_err_body(r)}", status=r.status_code)
    p = r.json()
    if not p.get("access_token"):
        raise OAuthError("openai-codex: refresh response missing access_token")
    new = {**st, "tokens": {"access_token": p["access_token"], "refresh_token": p.get("refresh_token") or rt},
           "last_refresh": time.time()}
    _save("openai-codex", new)
    return new


async def _refresh_minimax(st: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
    if not st.get("refresh_token"):
        raise OAuthError("minimax-oauth: no refresh token; re-login")
    r = await client.post(f"{st['portal_base_url']}/oauth/token", headers=_FORM,
                          data={"grant_type": "refresh_token", "client_id": st["client_id"],
                                "refresh_token": st["refresh_token"]})
    if r.status_code != 200:
        raise OAuthError(f"minimax-oauth: refresh failed (HTTP {r.status_code}); re-login. {_err_body(r)}", status=r.status_code)
    p = r.json()
    if p.get("status") != "success" or not p.get("access_token") or not p.get("expired_in"):
        raise OAuthError("minimax-oauth: refresh did not return a usable token; re-login")
    new = {**st, "access_token": p["access_token"], "refresh_token": p.get("refresh_token", st["refresh_token"]),
           "expires_at": _minimax_expiry(p["expired_in"])}
    _save("minimax-oauth", new)
    return new


async def _refresh_qwen(st: dict[str, Any], client: httpx.AsyncClient, *, source: str) -> dict[str, Any]:
    rt = (st.get("refresh_token") or "").strip()
    if not rt:
        raise OAuthError("qwen-oauth: no refresh token; re-login")
    r = await client.post(QWEN_TOKEN_URL, headers=_FORM,
                          data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": QWEN_CLIENT_ID})
    if r.status_code >= 400:
        raise OAuthError(f"qwen-oauth: refresh failed (HTTP {r.status_code}); re-login. {_err_body(r)}", status=r.status_code)
    p = r.json()
    if not str(p.get("access_token") or "").strip():
        raise OAuthError("qwen-oauth: refresh response missing access_token")
    try:
        ttl = int(p.get("expires_in"))
    except (TypeError, ValueError):
        ttl = 6 * 3600
    new = {"access_token": p["access_token"].strip(), "refresh_token": (p.get("refresh_token") or rt).strip(),
           "token_type": p.get("token_type") or "Bearer",
           "resource_url": p.get("resource_url") or st.get("resource_url") or "portal.qwen.ai",
           "expiry_date": int(time.time() * 1000) + max(1, ttl) * 1000}
    if source == "qwen-cli":
        _write_private(_qwen_cli_path(), new)
    else:
        _save("qwen-oauth", new)
    return new


def _minimax_expiry(expired_in: Any) -> float:
    """MiniMax returns either a unix-ms deadline or a TTL in seconds."""
    raw = int(expired_in)
    now = time.time()
    return raw / 1000.0 if raw > now * 1000 / 2 else now + max(1, raw)


# ---------------------------------------------------------------- resolve (runtime)

_QWEN_HOST_SUFFIXES = (".qwen.ai", ".aliyuncs.com")


def _qwen_base_url(resource_url: Any) -> str | None:
    """Token-supplied endpoint, only if it is a known Qwen/Alibaba host (never send tokens elsewhere)."""
    host = str(resource_url or "").strip().removeprefix("https://").split("/")[0].lower()
    if host and ("." + host).endswith(_QWEN_HOST_SUFFIXES):
        return f"https://{host}/v1"
    return None


def _qwen_state() -> tuple[str, dict[str, Any]]:
    source, st = "store", _load().get("qwen-oauth") or {}
    if not st.get("access_token") and _qwen_cli_path().exists():
        try:
            st, source = json.loads(_qwen_cli_path().read_text("utf-8-sig")), "qwen-cli"
        except (OSError, json.JSONDecodeError) as e:
            raise OAuthError(f"qwen-oauth: cannot read {_qwen_cli_path()}: {e}") from e
    return source, st


async def _refresh_once(
    reload: Callable[[], dict[str, Any]], token_of: Callable[[dict[str, Any]], Any], seen: Any,
    refresh: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    """Refresh under the cross-process lock, unless another process already rotated the token."""
    async with _afile_lock():
        st = reload()
        cur = token_of(st)
        if cur and cur != seen:
            return st
        return await refresh(st)


async def resolve(
    provider: str, *, client: httpx.AsyncClient | None = None, base_url: str | None = None,
    force_refresh: bool = False,
) -> tuple[str | None, str, dict[str, str]]:
    """Return (base_url override or None, bearer token, extra headers), refreshing if needed.

    ``force_refresh`` is for a 401 from the provider: refresh even if the token looks fresh
    (unless another process already rotated it).
    """
    owned = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
    login_hint = f"run `python -m assistant.providers.oauth login {provider}`"
    codex_ver = await releases.codex_version() if provider == "openai-codex" else None   # looked up outside the refresh lock
    try:
        async with _loop_lock():  # refresh tokens rotate: never refresh concurrently
            if provider == "openai-codex":
                reload = lambda: _load().get("openai-codex") or {}  # noqa: E731
                token_of = lambda s: (s.get("tokens") or {}).get("access_token")  # noqa: E731
                st = reload()
                tok = token_of(st)
                if not tok:
                    raise OAuthError(f"openai-codex: not logged in; {login_hint}")
                exp = _jwt_exp(tok)  # opaque (non-JWT) tokens: rely on force_refresh after a 401
                if force_refresh or (exp and exp - time.time() <= CODEX_SKEW):
                    st = await _refresh_once(reload, token_of, tok, lambda s: _refresh_codex(s, http))
                    tok = token_of(st)
                return None, tok, codex_headers(tok, codex_ver)

            if provider == "minimax-oauth":
                reload = lambda: _load().get("minimax-oauth") or {}  # noqa: E731
                st = reload()
                if not st.get("access_token"):
                    raise OAuthError(f"minimax-oauth: not logged in; {login_hint}")
                if force_refresh or st.get("expires_at", 0) - time.time() <= MINIMAX_SKEW:
                    st = await _refresh_once(reload, lambda s: s.get("access_token"), st["access_token"],
                                             lambda s: _refresh_minimax(s, http))
                return st["inference_base_url"].rstrip("/"), st["access_token"], {}

            if provider == "qwen-oauth":
                source, st = _qwen_state()
                if not st.get("access_token"):
                    raise OAuthError(f"qwen-oauth: not logged in; {login_hint}")
                try:
                    expiring = (time.time() + QWEN_SKEW) * 1000 >= int(st.get("expiry_date"))
                except (TypeError, ValueError):
                    expiring = True
                if force_refresh or expiring:
                    st = await _refresh_once(lambda: _qwen_state()[1], lambda s: s.get("access_token"),
                                             st["access_token"], lambda s: _refresh_qwen(s, http, source=source))
                base = os.environ.get("QWEN_BASE_URL", "").rstrip("/") or _qwen_base_url(st.get("resource_url"))
                return base, st["access_token"].strip(), _qwen_headers()
    finally:
        if owned:
            await http.aclose()
    raise OAuthError(f"{provider}: not an OAuth provider")


# ---------------------------------------------------------------- what a finished sign-in stores (both login paths)

def _codex_saved(tk: dict[str, Any]) -> dict[str, Any]:
    return {"tokens": {"access_token": tk["access_token"], "refresh_token": tk.get("refresh_token", "")},
            "last_refresh": time.time(), "source": "device-code"}


def _minimax_saved(region: str, tk: dict[str, Any]) -> dict[str, Any]:
    portal, inference = MINIMAX_REGIONS[region]
    return {"region": region, "portal_base_url": portal, "inference_base_url": inference,
            "client_id": MINIMAX_CLIENT_ID, "scope": MINIMAX_SCOPE, "token_type": tk.get("token_type", "Bearer"),
            "access_token": tk["access_token"], "refresh_token": tk["refresh_token"],
            "expires_at": _minimax_expiry(tk["expired_in"])}


def _qwen_saved(tk: dict[str, Any]) -> dict[str, Any]:
    ttl = int(tk.get("expires_in") or 6 * 3600)
    return {"access_token": tk["access_token"], "refresh_token": tk.get("refresh_token", ""),
            "token_type": tk.get("token_type", "Bearer"), "resource_url": tk.get("resource_url", "portal.qwen.ai"),
            "expiry_date": int(time.time() * 1000) + ttl * 1000}


# ---------------------------------------------------------------- phone sign-in (async, one step per request)
#
# The same device flows as `login` below, split so the server can answer the phone at once: device_start() gets the address and
# the code to show, device_poll() asks once whether the user has approved (and stores the tokens when they have). The returned
# state holds the device secrets (device code, PKCE verifier); the server keeps it in memory and never sends it to the phone.

async def device_start(provider: str, client: httpx.AsyncClient, *, region: str = "global") -> dict[str, Any]:
    """{url, user_code, expires_in (seconds), interval, ...private} for one of OAUTH_PROVIDERS."""
    if provider == "openai-codex":
        r = await client.post(f"{CODEX_ISSUER}/api/accounts/deviceauth/usercode", json={"client_id": CODEX_CLIENT_ID})
        if r.status_code != 200:
            raise OAuthError(f"codex device code request failed: HTTP {r.status_code}", status=502)
        d = r.json()
        return {"url": f"{CODEX_ISSUER}/codex/device", "user_code": str(d["user_code"]), "expires_in": 900,
                "interval": max(3, int(d.get("interval", 5))), "device_auth_id": d["device_auth_id"]}
    if provider == "qwen-oauth":
        verifier, challenge = _pkce()
        r = await client.post(QWEN_DEVICE_URL, headers=_FORM, data={
            "client_id": QWEN_CLIENT_ID, "scope": QWEN_SCOPE, "code_challenge": challenge, "code_challenge_method": "S256"})
        if r.status_code != 200:
            raise OAuthError(f"qwen device authorization failed: HTTP {r.status_code}", status=502)
        d = r.json()
        return {"url": d.get("verification_uri_complete") or d["verification_uri"], "user_code": str(d.get("user_code") or ""),
                "expires_in": int(d.get("expires_in") or 600), "interval": max(2, int(d.get("interval") or 5)),
                "device_code": d["device_code"], "verifier": verifier}
    if provider == "minimax-oauth":
        if region not in MINIMAX_REGIONS:
            raise OAuthError(f"minimax-oauth: unknown region {region!r}", status=422)
        portal = MINIMAX_REGIONS[region][0]
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(16)
        r = await client.post(f"{portal}/oauth/code", headers={**_FORM, "x-request-id": str(uuid.uuid4())}, data={
            "response_type": "code", "client_id": MINIMAX_CLIENT_ID, "scope": MINIMAX_SCOPE,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
        if r.status_code != 200:
            raise OAuthError(f"minimax authorization failed: HTTP {r.status_code}", status=502)
        d = r.json()
        if d.get("state") != state:
            raise OAuthError("minimax state mismatch (possible CSRF)", status=502)
        return {"url": d["verification_uri"], "user_code": str(d["user_code"]), "expires_in": max(0, int(_minimax_expiry(d["expired_in"]) - time.time())),
                "interval": max(2.0, int(d.get("interval") or 2000) / 1000.0), "verifier": verifier, "region": region}
    raise OAuthError(f"{provider}: not an OAuth provider", status=404)


async def device_poll(provider: str, d: dict[str, Any], client: httpx.AsyncClient) -> str:
    """Ask once: "pending" (not approved yet), "slow_down" (asked too often) or "done" (tokens stored). OAuthError = it failed.
    The caller keeps track of the deadline."""
    if provider == "openai-codex":
        p = await client.post(f"{CODEX_ISSUER}/api/accounts/deviceauth/token",
                              json={"device_auth_id": d["device_auth_id"], "user_code": d["user_code"]})
        if p.status_code in (403, 404):
            return "pending"
        if p.status_code != 200:
            raise OAuthError(f"codex device polling failed: HTTP {p.status_code}", status=502)
        code = p.json()
        t = await client.post(CODEX_TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
            "grant_type": "authorization_code", "code": code["authorization_code"],
            "redirect_uri": f"{CODEX_ISSUER}/deviceauth/callback", "client_id": CODEX_CLIENT_ID,
            "code_verifier": code["code_verifier"]})
        if t.status_code != 200 or not t.json().get("access_token"):
            raise OAuthError(f"codex token exchange failed: HTTP {t.status_code}", status=502)
        _save("openai-codex", _codex_saved(t.json()))
        return "done"
    if provider == "qwen-oauth":
        p = await client.post(QWEN_TOKEN_URL, headers=_FORM, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": QWEN_CLIENT_ID,
            "device_code": d["device_code"], "code_verifier": d["verifier"]})
        j = p.json() if p.text else {}
        if p.status_code == 200 and j.get("access_token"):
            _save("qwen-oauth", _qwen_saved(j))
            return "done"
        err = j.get("error") if isinstance(j, dict) else None
        if err in ("authorization_pending", "slow_down"):
            return "pending" if err == "authorization_pending" else "slow_down"
        raise OAuthError(f"qwen login failed: HTTP {p.status_code} {err or ''}".strip(), status=502)
    if provider == "minimax-oauth":
        portal = MINIMAX_REGIONS[d["region"]][0]
        p = await client.post(f"{portal}/oauth/token", headers=_FORM, data={
            "grant_type": MINIMAX_GRANT, "client_id": MINIMAX_CLIENT_ID, "user_code": d["user_code"],
            "code_verifier": d["verifier"]})
        if p.status_code != 200:
            raise OAuthError(f"minimax token error: HTTP {p.status_code}", status=502)
        j = p.json() if p.text else {}
        if j.get("status") == "error":
            raise OAuthError("minimax reported an error", status=502)
        if j.get("status") == "success":
            if not all(j.get(k) for k in ("access_token", "refresh_token", "expired_in")):
                raise OAuthError("minimax success payload missing token fields", status=502)
            _save("minimax-oauth", _minimax_saved(d["region"], j))
            return "done"
        return "pending"
    raise OAuthError(f"{provider}: not an OAuth provider", status=404)


# ---------------------------------------------------------------- login (sync, interactive)

def _login_codex() -> None:
    with httpx.Client(timeout=15.0) as c:
        r = c.post(f"{CODEX_ISSUER}/api/accounts/deviceauth/usercode", json={"client_id": CODEX_CLIENT_ID})
        if r.status_code != 200:
            raise OAuthError(f"codex device code request failed: HTTP {r.status_code} {_err_body(r)}")
        d = r.json()
        interval = max(3, int(d.get("interval", 5)))
        print(f"\n1. Open  {CODEX_ISSUER}/codex/device\n2. Enter {d['user_code']}\nWaiting for sign-in...", flush=True)
        deadline, code_resp = time.monotonic() + 900, None
        while time.monotonic() < deadline:
            time.sleep(interval)
            p = c.post(f"{CODEX_ISSUER}/api/accounts/deviceauth/token",
                       json={"device_auth_id": d["device_auth_id"], "user_code": d["user_code"]})
            if p.status_code == 200:
                code_resp = p.json()
                break
            if p.status_code not in (403, 404):
                raise OAuthError(f"codex device polling failed: HTTP {p.status_code}")
        if not code_resp:
            raise OAuthError("codex login timed out")
        t = c.post(CODEX_TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
            "grant_type": "authorization_code", "code": code_resp["authorization_code"],
            "redirect_uri": f"{CODEX_ISSUER}/deviceauth/callback", "client_id": CODEX_CLIENT_ID,
            "code_verifier": code_resp["code_verifier"]})
        if t.status_code != 200 or not t.json().get("access_token"):
            raise OAuthError(f"codex token exchange failed: HTTP {t.status_code}")
        tk = t.json()
    _save("openai-codex", _codex_saved(tk))


def _login_minimax(region: str) -> None:
    portal = MINIMAX_REGIONS[region][0]
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    with httpx.Client(timeout=15.0, follow_redirects=True) as c:
        r = c.post(f"{portal}/oauth/code", headers={**_FORM, "x-request-id": str(uuid.uuid4())}, data={
            "response_type": "code", "client_id": MINIMAX_CLIENT_ID, "scope": MINIMAX_SCOPE,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
        if r.status_code != 200:
            raise OAuthError(f"minimax authorization failed: HTTP {r.status_code} {_err_body(r)}")
        d = r.json()
        if d.get("state") != state:
            raise OAuthError("minimax state mismatch (possible CSRF)")
        print(f"\nOpen  {d['verification_uri']}\nCode  {d['user_code']}\nWaiting for approval...", flush=True)
        deadline = _minimax_expiry(d["expired_in"])
        interval = max(2.0, int(d.get("interval") or 2000) / 1000.0)
        tk = None
        while time.time() < deadline:
            p = c.post(f"{portal}/oauth/token", headers=_FORM, data={
                "grant_type": MINIMAX_GRANT, "client_id": MINIMAX_CLIENT_ID,
                "user_code": d["user_code"], "code_verifier": verifier})
            if p.status_code != 200:
                raise OAuthError(f"minimax token error: HTTP {p.status_code} {_err_body(p)}")
            j = p.json() if p.text else {}
            if j.get("status") == "error":
                raise OAuthError("minimax reported an error; try again later")
            if j.get("status") == "success":
                if not all(j.get(k) for k in ("access_token", "refresh_token", "expired_in")):
                    raise OAuthError("minimax success payload missing token fields")
                tk = j
                break
            time.sleep(interval)
        if not tk:
            raise OAuthError("minimax login timed out")
    _save("minimax-oauth", _minimax_saved(region, tk))


def _login_qwen() -> None:
    """Qwen Code device flow (RFC 8628 + PKCE) against chat.qwen.ai."""
    verifier, challenge = _pkce()
    with httpx.Client(timeout=15.0) as c:
        r = c.post(QWEN_DEVICE_URL, headers=_FORM, data={
            "client_id": QWEN_CLIENT_ID, "scope": QWEN_SCOPE,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        if r.status_code != 200:
            raise OAuthError(f"qwen device authorization failed: HTTP {r.status_code} {_err_body(r)}")
        d = r.json()
        url = d.get("verification_uri_complete") or d["verification_uri"]
        print(f"\nOpen  {url}\n" + (f"Code  {d['user_code']}\n" if "verification_uri_complete" not in d else "")
              + "Waiting for approval...", flush=True)
        interval = max(2, int(d.get("interval") or 5))
        deadline = time.monotonic() + int(d.get("expires_in") or 600)
        tk = None
        while time.monotonic() < deadline:
            time.sleep(interval)
            p = c.post(QWEN_TOKEN_URL, headers=_FORM, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": QWEN_CLIENT_ID,
                "device_code": d["device_code"], "code_verifier": verifier})
            j = p.json() if p.text else {}
            if p.status_code == 200 and j.get("access_token"):
                tk = j
                break
            err = j.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            raise OAuthError(f"qwen login failed: HTTP {p.status_code} {err or _err_body(p)}")
        if not tk:
            raise OAuthError("qwen login timed out")
    _save("qwen-oauth", _qwen_saved(tk))


def login(provider: str, *, region: str = "global") -> None:
    if provider == "openai-codex":
        _login_codex()
    elif provider == "minimax-oauth":
        _login_minimax(region)
    elif provider == "qwen-oauth":
        _login_qwen()
    else:
        raise OAuthError(f"{provider}: not an OAuth provider (choose from {', '.join(OAUTH_PROVIDERS)})")
    print(f"✓ {provider} logged in ({store_path()})")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m assistant.providers.oauth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("login")
    lp.add_argument("provider", choices=OAUTH_PROVIDERS)
    lp.add_argument("--region", choices=list(MINIMAX_REGIONS), default="global", help="minimax-oauth only")
    op = sub.add_parser("logout")
    op.add_argument("provider", choices=OAUTH_PROVIDERS)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "login":
            login(a.provider, region=a.region)
        elif a.cmd == "logout":
            print("logged out" if logout(a.provider) else "nothing to remove")
        else:
            for k, v in status().items():
                print(f"{k}: {'logged in' if v['logged_in'] else 'not logged in'}" + (f" ({v['source']})" if v.get("source") else ""))
    except ProviderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
