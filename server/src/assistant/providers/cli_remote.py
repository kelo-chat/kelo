"""Run the claude/agy CLI in a sidecar container instead of in this process.

The sidecar (``assistant.cli_runner``) executes the same ``cli_agents.stream`` and relays its events over
a private network. Configure with ``ASSISTANT_CLI_CLAUDE_URL`` / ``ASSISTANT_CLI_AGY_URL`` (+ the shared
``CLI_RUNNER_TOKEN``). Unset = run the CLI locally.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

import httpx

from ..i18n import t
from .types import ChatRequest, Done, Event, ListUnavailable, ProviderError, TextDelta, Thinking, ToolCall, Usage

_URL_ENV = {
    "claude-subscription-directsdk-experimental": "ASSISTANT_CLI_CLAUDE_URL",
    "antigravity-oauth": "ASSISTANT_CLI_AGY_URL",
}
_PROFILE = {"claude-subscription-directsdk-experimental": "claude", "antigravity-oauth": "agy"}


# One client for every sidecar call made on this event loop. Making a client loads the CA certificates: 0.05 s on an idle machine,
# seconds on a busy one. It is made once, on a worker thread, so the event loop (every other request) never waits for it. Calls
# pass their own timeout.
_shared_http: tuple[Any, httpx.AsyncClient] | None = None


def new_client(timeout: httpx.Timeout) -> Any:
    """An httpx.AsyncClient made on a worker thread (await it)."""
    return asyncio.to_thread(httpx.AsyncClient, timeout=timeout)


async def _shared() -> httpx.AsyncClient:
    global _shared_http
    loop = asyncio.get_running_loop()
    if _shared_http is None or _shared_http[0] is not loop or _shared_http[1].is_closed:
        made = await new_client(httpx.Timeout(20.0, connect=3.0))
        if _shared_http is None or _shared_http[0] is not loop or _shared_http[1].is_closed:   # another call may have made one meanwhile
            _shared_http = (loop, made)
        else:
            await made.aclose()
    return _shared_http[1]


def remote_url(provider: str) -> str | None:
    var = _URL_ENV.get(provider)
    return (os.environ.get(var, "").rstrip("/") or None) if var else None


def profile_hint(provider: str) -> str:
    return f"docker compose --profile {_PROFILE.get(provider, provider)} up -d --build"


def runner_token() -> str:
    return os.environ.get("CLI_RUNNER_TOKEN", "")


def encode_event(ev: Event) -> dict[str, Any]:
    match ev:
        case Thinking():
            return {"type": "thinking"}
        case TextDelta():
            return {"type": "text", "text": ev.text}
        case ToolCall():
            return {"type": "tool_call", "id": ev.id, "name": ev.name, "arguments": ev.arguments}
        case Usage():
            return {"type": "usage", "input_tokens": ev.input_tokens, "output_tokens": ev.output_tokens,
                    "cache_read_tokens": ev.cache_read_tokens, "cache_write_tokens": ev.cache_write_tokens, "session": ev.session}
        case Done():
            return {"type": "done", "finish_reason": ev.finish_reason}
    raise TypeError(type(ev))


def decode_event(d: dict[str, Any]) -> Event:
    match d.get("type"):
        case "thinking":
            return Thinking()
        case "text":
            return TextDelta(str(d["text"]))
        case "tool_call":
            return ToolCall(str(d["id"]), str(d["name"]), str(d["arguments"]))
        case "usage":
            return Usage(int(d.get("input_tokens", 0)), int(d.get("output_tokens", 0)), int(d.get("cache_read_tokens", 0) or 0),
                         int(d.get("cache_write_tokens", 0) or 0), session=str(d.get("session") or "")[:120])   # an older sidecar sends no cache fields
        case "done":
            return Done(d.get("finish_reason", "stop"))
        case "error":
            raise ProviderError(str(d.get("message", "runner error")), status=d.get("status"))
    raise ProviderError(f"unexpected runner event {d.get('type')!r}")


def request_to_json(req: ChatRequest) -> dict[str, Any]:
    return asdict(req)


def request_from_json(d: dict[str, Any]) -> ChatRequest:
    return ChatRequest(
        model=str(d.get("model", "")), messages=list(d.get("messages") or []), tools=list(d.get("tools") or []),
        max_tokens=d.get("max_tokens"), temperature=d.get("temperature"), extra=dict(d.get("extra") or {}),
        purpose=str(d.get("purpose") or ""))


def _auth() -> dict[str, str]:
    return {"authorization": f"Bearer {runner_token()}"}


async def warm(provider: str, req: ChatRequest, *, client: httpx.AsyncClient | None = None) -> bool:
    """Ask the sidecar to start its agy session for this request's system prompt (POST /warm); no model request is made."""
    url = remote_url(provider)
    if not url:
        from . import cli_agents   # no sidecar: the CLI runs in this process
        return await cli_agents.warm(provider, req)
    http = client or await _shared()
    try:
        r = await http.post(f"{url}/warm", headers=_auth(), json={"request": request_to_json(req)}, timeout=httpx.Timeout(60.0, connect=5.0))
    except httpx.HTTPError:
        return False
    return r.status_code == 200 and bool(r.json().get("warm"))


async def stream(provider: str, req: ChatRequest, *, client: httpx.AsyncClient | None = None) -> AsyncIterator[Event]:
    url = remote_url(provider)
    if not url:
        raise ProviderError(f"{provider}: no sidecar URL configured")
    http = client or await _shared()
    limit = httpx.Timeout(600.0, connect=5.0)
    try:
        async with http.stream("POST", f"{url}/run", headers=_auth(), json={"request": request_to_json(req)}, timeout=limit) as r:
            if r.status_code == 401:
                raise ProviderError(t("CLI sidecar sign-in failed: the server and the sidecar have different CLI_RUNNER_TOKEN values"), status=502)
            if r.status_code != 200:
                raise ProviderError(t("CLI sidecar error (HTTP {status})", status=r.status_code), status=502)
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    yield decode_event(json.loads(line[6:]))
    except httpx.ConnectError:
        raise ProviderError(t("Could not reach the CLI sidecar. To turn it on: {command}", command=profile_hint(provider)), status=502) from None
    except httpx.HTTPError as e:
        raise ProviderError(t("CLI sidecar connection error ({error})", error=type(e).__name__), status=502) from None


async def models(provider: str, *, client: httpx.AsyncClient | None = None) -> dict[str, str]:
    url = remote_url(provider)
    http = client or await _shared()
    limit = httpx.Timeout(40.0, connect=3.0)
    try:
        r = await http.get(f"{url}/models", headers=_auth(), timeout=limit)
    except httpx.ConnectError:
        raise ListUnavailable(t("Could not reach the CLI sidecar. To turn it on: {command}", command=profile_hint(provider))) from None
    except httpx.HTTPError as e:
        raise ListUnavailable(t("CLI sidecar connection error ({error})", error=type(e).__name__)) from None
    if r.status_code == 401:
        raise ProviderError(t("The server and the sidecar have different CLI_RUNNER_TOKEN values"))
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        err = ListUnavailable if r.status_code >= 500 or r.status_code == 429 else ProviderError
        raise err(t(str(detail)) if detail else t("CLI sidecar error (HTTP {status})", status=r.status_code))
    return {str(k): str(v) for k, v in r.json().get("models", {}).items()}


async def usage(provider: str, *, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    url = remote_url(provider)
    http = client or await _shared()
    limit = httpx.Timeout(60.0, connect=3.0)
    try:
        r = await http.get(f"{url}/usage", headers=_auth(), timeout=limit)
    except httpx.ConnectError:
        raise ProviderError(t("Could not reach the CLI sidecar. To turn it on: {command}", command=profile_hint(provider))) from None
    except httpx.HTTPError as e:
        raise ProviderError(t("CLI sidecar connection error ({error})", error=type(e).__name__)) from None
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise ProviderError(t(str(detail)) if detail else t("CLI sidecar error (HTTP {status})", status=r.status_code))
    return list(r.json().get("windows", []))


async def logout(provider: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Ask the sidecar to sign its CLI out (its login lives in the sidecar's own home volume)."""
    url = remote_url(provider)
    http = client or await _shared()
    limit = httpx.Timeout(40.0, connect=3.0)
    try:
        r = await http.post(f"{url}/logout", headers=_auth(), timeout=limit)
    except httpx.ConnectError:
        raise ProviderError(t("Could not reach the CLI sidecar. To turn it on: {command}", command=profile_hint(provider))) from None
    except httpx.HTTPError as e:
        raise ProviderError(t("CLI sidecar connection error ({error})", error=type(e).__name__)) from None
    if r.status_code == 401:
        raise ProviderError(t("The server and the sidecar have different CLI_RUNNER_TOKEN values"))
    if r.status_code in (404, 405):   # a sidecar built before sign-out existed
        raise ProviderError(t("The CLI sidecar is out of date. Update it: assistant update --cli {command}",
                              command=_PROFILE.get(provider, provider)))
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise ProviderError(t(str(detail)) if detail else t("CLI sidecar error (HTTP {status})", status=r.status_code))
    body = r.json()
    return {"removed": bool(body.get("removed")), "still_signed_in": bool(body.get("still_signed_in"))}


def hints(provider: str, kind: str) -> dict[str, str]:
    """The hint lines of a sidecar status in the user's language. kind: "off" (not reachable), "token" (CLI_RUNNER_TOKEN differs) or
    "ok". The server's status cache (providers/cli_status_cache.py) keeps the status without them and writes them per request."""
    cmd = _PROFILE.get(provider, provider)
    if kind == "token":
        return {"hint": t("The server and the sidecar have different CLI_RUNNER_TOKEN values (check .env, then up again)")}
    if kind == "ok":
        return {"update_hint": t("Update to the latest: assistant update --cli {command}", command=cmd),
                "hint": t("Sign in on the sidecar: docker compose exec -it {command}-cli {command}", command=cmd)}
    return {"hint": t("The CLI sidecar is off: {command}", command=profile_hint(provider))}


async def status(provider: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """The sidecar's view of its CLI, or an 'off' answer with the command that turns it on. "hint_kind" says which hints() the
    answer carries (the status cache writes them again in each request's language)."""
    url = remote_url(provider)
    off = {"installed": False, "installable": False, "logged_in": None, "remote": True, "version": None,
           "hint_kind": "off", **hints(provider, "off")}
    http = client or await _shared()
    limit = httpx.Timeout(4.0, connect=2.0)
    try:
        r = await http.get(f"{url}/status", headers=_auth(), timeout=limit)
    except httpx.HTTPError:
        return off
    if r.status_code == 401:
        return {**off, "hint_kind": "token", **hints(provider, "token")}
    if r.status_code != 200:
        return off
    st = r.json()
    st.update(remote=True, installable=False, hint_kind="ok", **hints(provider, "ok"))
    return st


class SidecarOff(ProviderError):
    """The CLI's container is not running (or not reachable): the phone shows how to turn it on."""


def enable_hint(provider: str) -> str:
    return t("The {name} container on the server is off. On the server computer run `assistant enable oauth` "
             "(it also starts the Claude and Antigravity containers), then try again.",
             name="Claude" if _PROFILE.get(provider) == "claude" else "Antigravity")


async def _login_call(provider: str, method: str, path: str, *, json_body: dict[str, Any] | None = None,
                      params: dict[str, str] | None = None, timeout: float = 90.0,
                      client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """One sign-in call to the sidecar (cli_runner /login/...). Errors keep the sidecar's own (already readable) reason."""
    url = remote_url(provider)
    if not url:
        raise SidecarOff(enable_hint(provider), status=503)
    http = client or await _shared()
    limit = httpx.Timeout(timeout, connect=3.0)
    try:
        r = await http.request(method, f"{url}{path}", headers=_auth(), json=json_body, params=params, timeout=limit)
    except httpx.ConnectError:
        raise SidecarOff(enable_hint(provider), status=503) from None
    except httpx.HTTPError as e:
        raise ProviderError(t("CLI sidecar connection error ({error})", error=type(e).__name__), status=502) from None
    if r.status_code == 401:
        raise ProviderError(t("The server and the sidecar have different CLI_RUNNER_TOKEN values"), status=502)
    if r.status_code in (404, 405) and path == "/login/start":   # a sidecar built before phone sign-in existed
        raise ProviderError(t("The CLI sidecar is out of date. Update it: assistant update --cli {command}",
                              command=_PROFILE.get(provider, provider)), status=503)
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise ProviderError(t(str(detail)) if detail else t("CLI sidecar error (HTTP {status})", status=r.status_code),
                            status=r.status_code if 400 <= r.status_code < 500 else 502)
    body = r.json()
    return body if isinstance(body, dict) else {}


async def login_start(provider: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    return await _login_call(provider, "POST", "/login/start", client=client)


async def login_finish(provider: str, session: str, code: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    return await _login_call(provider, "POST", "/login/finish", json_body={"session": session, "code": code}, client=client)


async def login_status(provider: str, session: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    return await _login_call(provider, "GET", "/login/status", params={"session": session}, timeout=10.0, client=client)


async def login_cancel(provider: str, session: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    return await _login_call(provider, "POST", "/login/cancel", json_body={"session": session}, timeout=10.0, client=client)


# ---- the sidecar updating its own CLI into its home volume (cli_runner POST /update, GET /update/status; providers/cli_update.py)

async def _update_call(provider: str, method: str, path: str, *, json_body: dict[str, Any] | None = None,
                       client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    url = remote_url(provider)
    if not url:
        raise ProviderError(t("Only a CLI that runs in its own container can be updated from the app"), status=409)
    http = client or await _shared()
    limit = httpx.Timeout(20.0, connect=3.0)
    try:
        r = await http.request(method, f"{url}{path}", headers=_auth(), json=json_body, timeout=limit)
    except httpx.ConnectError:
        raise SidecarOff(enable_hint(provider), status=503) from None
    except httpx.HTTPError as e:
        raise ProviderError(t("CLI sidecar connection error ({error})", error=type(e).__name__), status=502) from None
    if r.status_code == 401:
        raise ProviderError(t("The server and the sidecar have different CLI_RUNNER_TOKEN values"), status=502)
    if r.status_code in (404, 405):   # a sidecar built before it could update itself
        raise ProviderError(t("The CLI sidecar is out of date. Update it: assistant update --cli {command}",
                              command=_PROFILE.get(provider, provider)), status=409)
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise ProviderError(t(str(detail)) if detail else t("CLI sidecar error (HTTP {status})", status=r.status_code),
                            status=r.status_code if 400 <= r.status_code < 500 else 502)
    body = r.json()
    if not isinstance(body, dict):
        return {}
    if body.get("error_key"):   # the runner's reason is an English template: shown in the user's language
        fields = body.get("error_fields") if isinstance(body.get("error_fields"), dict) else {}
        body["error"] = t(str(body["error_key"]), **fields)
    return body


async def update_start(provider: str, version: str | None = None, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    return await _update_call(provider, "POST", "/update", json_body={"version": version} if version else {}, client=client)


async def update_status(provider: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    return await _update_call(provider, "GET", "/update/status", client=client)
