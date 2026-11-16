"""One entry point for every provider: stream_chat()."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import cli_agents, cli_remote, oauth, refused, vertex_auth
from .registry import KEYLESS_OK, USER_ENDPOINT_PREFIX, Provider, get_provider
from .transports import anthropic_messages, bedrock_converse, openai_chat, responses
from .types import ChatRequest, Event, ProviderError, ProviderStalled, UnsupportedProvider, Usage, late_notes_as_user

_TRANSPORTS = {
    "chat_completions": openai_chat.stream,
    "anthropic_messages": anthropic_messages.stream,
    "codex_responses": responses.stream,
    "responses": responses.stream,
}
log = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
# Gemini's own "the tool call you wrote could not be parsed" (MALFORMED_FUNCTION_CALL), as agy reports it: worth one more ask
MALFORMED_CALL = re.compile(r"improperly formatted function call|MALFORMED_FUNCTION_CALL", re.I)
MALFORMED_RETRIES = 2
# Gemini out of capacity for a moment ("Our servers are experiencing high traffic right now, please try again in a minute",
# UNAVAILABLE 503 "No capacity available for model"): a job of 123 steps failed on one such answer (2026-11-11). Waited out a few times.
BUSY_CALL = re.compile(r"high traffic|No capacity available|UNAVAILABLE \(code 503\)|try again in a minute", re.I)
BUSY_WAITS = (20.0, 40.0, 60.0)

# The stall guard: a request is given up when NOTHING arrives from the provider for this long (no answer, no reasoning delta, not
# even an SSE keep-alive comment). Streamed reasoning counts, so a model that thinks for many minutes while it streams is never cut;
# the limit is for the silent case (a server that hangs, or reasons without streaming anything). Five minutes leaves room for a
# reasoning model that sends nothing while it thinks (OpenAI's own Chat Completions does that) and for a local model reading a long
# prompt before its first token. ASSISTANT_PROVIDER_STALL_SECONDS changes it.
STALL_SECONDS = 300.0


def stall_seconds() -> float:
    try:
        v = float(os.environ.get("ASSISTANT_PROVIDER_STALL_SECONDS", "") or STALL_SECONDS)
    except ValueError:
        return STALL_SECONDS
    return v if v > 0 else STALL_SECONDS


def build_headers(provider: Provider, api_key: str | None) -> dict[str, str]:
    headers = {"content-type": "application/json", **provider.default_headers}
    if provider.transport == "anthropic_messages":
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if api_key:
            headers["x-api-key"] = api_key
    elif api_key:
        headers["authorization"] = f"Bearer {api_key}"
    return headers


# The output cap: a provider that bills by the most a reply may use (OpenRouter holds credit for max_tokens, and without one for the
# model's whole output limit, 131072 for some) gets a sensible number per model: the model's own output limit when it is lower than
# the provider's default_max_tokens (providers.json), else that default. A 402 that names what the balance can still pay for
# ("can only afford 9882") is tried once more under it; below MIN_AFFORDABLE it is the balance that ran out (model_limits.py).
MIN_AFFORDABLE = 1024
_AFFORD = re.compile(r"can only afford (\d+)", re.I)


def output_cap(prov: Provider, req: ChatRequest) -> ChatRequest:
    if req.max_tokens is not None or not prov.default_max_tokens:
        return req
    from ..windows import provider_windows, table_window
    listed = provider_windows(prov.name).get(req.model) or table_window(req.model)
    own = listed[1] if listed and listed[1] else None
    return replace(req, max_tokens=min(int(prov.default_max_tokens), own) if own else int(prov.default_max_tokens))


def affordable(error: str) -> int | None:
    """The output a 402 says the balance still covers, a little under it; None when it says none or too little to answer."""
    m = _AFFORD.search(error or "")
    n = int(m.group(1)) * 9 // 10 if m else 0
    return n if n >= MIN_AFFORDABLE else None


def _without(req: ChatRequest, field: str) -> ChatRequest:
    return replace(req, extra={k: v for k, v in req.extra.items() if k != field})


def _codex_request(req: ChatRequest) -> ChatRequest:
    """chatgpt.com/backend-api/codex: needs instructions + store=false, rejects max_output_tokens/temperature."""
    extra = {"store": False, **req.extra}
    if not any(m.get("role") == "system" and m.get("content") for m in req.messages):
        extra.setdefault("instructions", "You are a helpful assistant.")
    return replace(req, max_tokens=None, temperature=None, extra=extra)


def _oauth_base_url(name: str, override: str | None, default_url: str) -> str:
    """Never send an OAuth bearer token to a host other than the provider's own, unless opted in."""
    if not override:
        return default_url
    host = urlsplit(override).hostname
    if host == urlsplit(default_url).hostname or os.environ.get("ASSISTANT_OAUTH_ALLOW_BASE_URL"):
        return override.rstrip("/")
    raise ProviderError(
        f"{name}: refusing to send OAuth token to {host!r}; set ASSISTANT_OAUTH_ALLOW_BASE_URL=1 to allow a custom base_url"
    )


async def stream_chat(
    provider: Provider | str,
    request: ChatRequest,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    env: dict[str, str] | None = None,
    aws_client: Any | None = None,
    region: str | None = None,
    vertex_token_source: Callable[[], tuple[str, str | None]] | None = None,
) -> AsyncIterator[Event]:
    """Stream normalized events from any supported provider.

    `api_key` / `base_url` override registry + environment values.
    Pass your own `client` to share connection pools (and for tests).
    """
    prov = get_provider(provider) if isinstance(provider, str) else provider
    if reason := prov.unsupported_reason():
        raise UnsupportedProvider(f"{prov.name}: {reason}")
    # system text only at the start, on every wire (types.late_notes_as_user): a note added mid-turn goes as tagged user text
    if (fixed := late_notes_as_user(list(request.messages))) != request.messages:
        request = replace(request, messages=fixed)

    if prov.transport in cli_agents.CLI_TRANSPORTS:
        # The CLI owns auth and the wire. In Docker it runs in a sidecar container (cli_remote).
        if prov.name == cli_agents.AGY:   # agy shows the model only the start of a long system prompt: hand it over in two parts
            request = replace(request, messages=cli_agents.fit_agy_rules(list(request.messages)))
        malformed = busy = 0
        while True:
            source = (cli_remote.stream(prov.name, request) if cli_remote.remote_url(prov.name)
                      else cli_agents.stream(prov.name, request))
            shown = False
            try:
                async for event in source:
                    if not isinstance(event, Usage):
                        shown = True
                    yield event
                return
            except ProviderError as e:
                # Gemini (agy) now and then writes a tool call it cannot parse and gives up with "improperly formatted function
                # call ... Retries remaining". Nothing reached the reply yet: the same call is simply asked again (2026-11-11: a job
                # applying to 471 items failed twice on it). Any other error, or one after output, goes on as before.
                if shown:
                    raise
                if MALFORMED_CALL.search(str(e)) and malformed < MALFORMED_RETRIES:
                    malformed += 1
                    log.info("%s: malformed function call, asking again (%d/%d)", prov.name, malformed, MALFORMED_RETRIES)
                    continue
                if BUSY_CALL.search(str(e)) and busy < len(BUSY_WAITS):
                    wait = BUSY_WAITS[busy]
                    busy += 1
                    log.info("%s: the model is busy, asking again in %.0f s (%d/%d)", prov.name, wait, busy, len(BUSY_WAITS))
                    await asyncio.sleep(wait)
                    continue
                raise

    if prov.transport == "bedrock_converse":
        # boto3 handles credentials and signing; no HTTP client involved.
        async for event in bedrock_converse.stream(request, client=aws_client, region=region):
            yield event
        return

    owned = client is None
    stall = stall_seconds()
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0, read=stall))
    try:
        if prov.auth_type == "vertex":
            url, key = await vertex_auth.resolve(env, vertex_token_source)
            if base_url:
                url = base_url.rstrip("/")
        elif prov.name in oauth.OAUTH_PROVIDERS:
            oauth_url, key, oauth_headers = await oauth.resolve(prov.name, client=http)
            default_url = (oauth_url or prov.resolve_base_url(None, env) or "").rstrip("/")
            url = _oauth_base_url(prov.name, base_url, default_url)
            if prov.name == "openai-codex":
                request = _codex_request(request)
        else:
            key = prov.resolve_api_key(api_key, env)
            if key is None and prov.api_key_envs and prov.name not in KEYLESS_OK and not prov.name.startswith(USER_ENDPOINT_PREFIX):
                raise ProviderError(f"{prov.name}: no API key (set one of {', '.join(prov.api_key_envs)})")
            url = prov.resolve_base_url(base_url, env)
        if not url:
            raise ProviderError(f"{prov.name}: no base URL configured")

        impl = _TRANSPORTS[prov.transport]
        is_oauth = prov.name in oauth.OAUTH_PROVIDERS
        request = output_cap(prov, request)
        afforded = False
        # the thinking level on an OpenAI-compatible wire: left out where this server and model turned it away before (refused.py)
        effort_field = refused.EFFORT_FIELD if prov.transport == "chat_completions" and refused.EFFORT_FIELD in request.extra else ""
        if effort_field and refused.refused(effort_field, url, request.model):
            request, effort_field = _without(request, effort_field), ""
        refreshed = without_effort = False
        while True:
            if is_oauth:
                headers = {**build_headers(prov, None), **oauth_headers, "authorization": f"Bearer {key}"}
            else:
                headers = build_headers(prov, key)
            started = False
            try:
                async for event in impl(http, url, headers, request):
                    if not started and without_effort:   # it answered once the field was gone: this server does not take it
                        refused.mark(effort_field, url, request.model)
                    started = True
                    yield event
                return
            except httpx.ReadTimeout as e:
                raise ProviderStalled(http.timeout.read or stall) from e
            except ProviderError as e:
                if started:
                    raise
                if is_oauth and not refreshed and e.status == 401:
                    # Token rejected before any output: force one refresh and retry once.
                    refreshed = True
                    _, key, oauth_headers = await oauth.resolve(prov.name, client=http, force_refresh=True)
                    continue
                if e.status == 402 and not afforded and (n := affordable(str(e))) is not None and n < (request.max_tokens or n + 1):
                    request, afforded = replace(request, max_tokens=n), True   # once, under what the balance still covers
                    continue
                if effort_field and not without_effort and e.status == 400:
                    # A 400 with the thinking level in the request: once more without it, and the turn goes on. When the error names
                    # the field it is noted at once; otherwise only when the request then works (the field was the only difference).
                    if refused.mentions(str(e), effort_field):
                        refused.mark(effort_field, url, request.model)
                    request, without_effort = _without(request, effort_field), True
                    continue
                raise
    finally:
        if owned:
            await http.aclose()
