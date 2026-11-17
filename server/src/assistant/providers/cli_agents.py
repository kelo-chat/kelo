"""Adapter for the vendored Hermes plugins that drive a subscription CLI as the model.

* ``claude-subscription-directsdk-experimental`` -> ``vendor/claude_directsdk`` (official plugin; may differ from current upstream, see NOTICE)
* ``antigravity-oauth``                          -> ``vendor/antigravity``       (neerazz/hermes-antigravity-oauth; may differ from current upstream, see NOTICE)

Both plugins expose an OpenAI-style client (``client.chat.completions.create(..., stream=True)``).
This module only (1) makes the two Hermes host helpers they import available (``vendor/hermes_shim``,
verbatim copies, used only when the real ones are not importable), (2) runs the blocking client in a
worker thread, and (3) converts its chat-completion chunks into our ``Event`` types.
The CLIs never execute tools; calls come back as ``ToolCall`` events for our agent loop.
"""

from __future__ import annotations

import asyncio
import atexit
import importlib
import json
import re
import sys
import threading
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cli_manage import claude_env, extend_agy_native_tools, wrap_agy_child_env
from .types import CONVERSATION_SUMMARY_HEAD, ChatRequest, Done, Event, ProviderError, TextDelta, Thinking, ToolCall, Usage, late_notes_as_user

CLAUDE = "claude-subscription-directsdk-experimental"
AGY = "antigravity-oauth"
CLI_PROVIDERS = (CLAUDE, AGY)
CLI_TRANSPORTS = {"cli_claude", "cli_agy"}

_VENDOR = Path(__file__).with_name("vendor")
_clients: dict[str, Any] = {}
_clients_lock = threading.Lock()
_DONE = object()


def _install_shims() -> None:
    """directsdk.py imports tools.schema_sanitizer / agent.reasoning_effort from Hermes."""
    shim = str(_VENDOR / "hermes_shim")
    if shim not in sys.path:
        sys.path.append(shim)  # appended: a real Hermes install on the path still wins


# agy loads the system prompt from a workspace rules file (GEMINI.md) and shows the model only its first ~24,000 bytes, ending in
# "<truncated N bytes>" (measured 2026-11-07: the model saw the persona up to the middle of "## Tools" and nothing after it - not the
# skills list, the app's rules, its own files or the tool-call contract at the end of that file). The model then went reading
# GEMINI.md and its workspace to find the rest. So a long system prompt is split here: the first part stays the system prompt
# (well under the limit, leaving room for the plugin's header and tool contract), the rest goes in as the first message of the
# conversation, which agy passes in full.
AGY_RULES_BUDGET = 20_000   # bytes of system prompt kept in the rules file
RULES_CONTINUED = "\n\n[These instructions continue in the first message of the conversation, marked as the app's instructions.]"
PART_TWO = ("[Not from the user: the app's instructions, part 2. Your rules file holds part 1; it was split because of its size. "
            "Follow this part with the same authority.]\n\n")


def _split_at_heading(text: str, budget: int) -> tuple[str, str]:
    """(head, rest): head is the longest run of whole sections (cut before a '## ' / '### ' line) within `budget` bytes."""
    cut = 0
    for m in re.finditer(r"\n(?=#{2,3} )", text):
        if len(text[:m.start()].encode()) > budget:
            break
        cut = m.start()
    if cut == 0:   # no heading early enough: cut at the last line break within the budget
        head = text.encode()[:budget].decode("utf-8", "ignore")
        cut = head.rfind("\n") if "\n" in head else len(head)
    return text[:cut].rstrip("\n"), text[cut:].lstrip("\n")


SUMMARY_NOTE = "[Not from the user: the app's summary of the earlier conversation.]\n"
APP_NOTE = "[Not from the user: a note from the app.]\n"


def fit_agy_rules(messages: list[dict[str, Any]], budget: int = AGY_RULES_BUDGET) -> list[dict[str, Any]]:
    """Messages for agy with no more than `budget` bytes of system text (see AGY_RULES_BUDGET). Unchanged when they fit.
    The summary of the earlier conversation (a system block, compaction.py) belongs to the conversation, not the rules file: it is
    handed over as a message in its place and never counts against the budget."""
    if any(m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"].startswith(CONVERSATION_SUMMARY_HEAD)
           for m in messages):
        messages = [{"role": "user", "content": SUMMARY_NOTE + m["content"]}
                    if m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"].startswith(CONVERSATION_SUMMARY_HEAD)
                    else m for m in messages]
    # A system message added in the middle of the conversation (the agent loop's "answer now" reminder) is a note for this point of
    # the conversation, not a rule: as a rule it would rewrite GEMINI.md, which makes the plugin restart agy (~5 s) and drop the session.
    talk = next((i for i, m in enumerate(messages) if m.get("role") != "system"), len(messages))
    if any(m.get("role") == "system" and isinstance(m.get("content"), str) for m in messages[talk:]):
        messages = [*messages[:talk], *({"role": "user", "content": APP_NOTE + m["content"]}
                                       if m.get("role") == "system" and isinstance(m.get("content"), str) else m for m in messages[talk:])]
    systems = [i for i, m in enumerate(messages) if m.get("role") == "system" and isinstance(m.get("content"), str)]
    if not systems:
        return messages
    others = sum(len(messages[i]["content"].encode()) for i in systems[1:])
    first = systems[0]
    text = messages[first]["content"]
    if len(text.encode()) + others <= budget:
        return messages
    head, rest = _split_at_heading(text, max(2_000, budget - others - len(RULES_CONTINUED.encode())))
    if not rest:
        return messages
    out = list(messages)
    out[first] = {**messages[first], "content": head + RULES_CONTINUED}
    out.insert(first + 1, {"role": "user", "content": PART_TWO + rest})
    return out


def _get_client(provider: str) -> Any:
    with _clients_lock:
        if provider not in _clients:
            _install_shims()
            try:
                if provider == CLAUDE:
                    mod = importlib.import_module(".vendor.claude_directsdk.directsdk", __package__)
                    _clients[provider] = mod.Client(env=claude_env())
                else:
                    mod = importlib.import_module(".vendor.antigravity.client", __package__)
                    if not getattr(mod.build_child_env, "_scrubbed", False):  # agy's child env also copied os.environ
                        mod.build_child_env = wrap_agy_child_env(mod.build_child_env)
                    extend_agy_native_tools()
                    from .agy_pool import AgyPool   # one live agy session per system prompt, kept between calls (agy_pool.py)
                    _clients[provider] = AgyPool()
            except ProviderError:
                raise
            except Exception as e:
                raise ProviderError(f"{provider}: cannot start ({type(e).__name__}: {e})") from e
        return _clients[provider]


def retire_client(provider: str) -> Any:
    """Forget the cached client (it holds the binary's path from when it started); the next run starts a new one.
    Returns the old one so the caller can close it once no run uses it any more."""
    with _clients_lock:
        return _clients.pop(provider, None)


@atexit.register
def _close_all() -> None:
    for c in list(_clients.values()):
        try:
            c.close()
        except Exception:
            pass


def _g(o: Any, key: str, default: Any = None) -> Any:
    if o is None:
        return default
    return o.get(key, default) if isinstance(o, dict) else getattr(o, key, default)


def _kwargs(provider: str, req: ChatRequest) -> dict[str, Any]:
    kw: dict[str, Any] = {"model": req.model, "messages": req.messages, "stream": True}
    if req.tools:
        kw["tools"] = req.tools
    effort = req.extra.get("reasoning_effort") or req.extra.get("effort")
    if provider == CLAUDE:
        if req.max_tokens is not None:
            kw["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            kw["temperature"] = req.temperature  # the plugin validates and drops it
        if effort:
            kw["extra_body"] = {"reasoning": {"enabled": True, "effort": str(effort)}}
        elif isinstance(req.extra.get("reasoning"), dict):
            kw["extra_body"] = {"reasoning": req.extra["reasoning"]}
    elif effort:
        kw["reasoning_effort"] = str(effort)
    return kw


_CODE_MARK = re.compile(r"\((?:code|HTTP) (\d{3})\)|\bHTTP (\d{3})\b")   # the status a CLI prints in its error text: "(code 503)"


def _status(e: BaseException) -> int | None:
    """The HTTP status of a CLI's failure: the SDK error's status_code, else the status the CLI printed in its text."""
    s = getattr(e, "status_code", None)
    if isinstance(s, int):
        return s
    m = _CODE_MARK.search(str(e))
    return int(m.group(1) or m.group(2)) if m else None


def session_note(lease: Any) -> str:
    """How an agy call used the session pool, for the usage record: "turn" / "round" (the session went on) or "new: <why>"."""
    if lease is None:
        return ""
    mode = str(getattr(lease, "mode", "") or "")
    why = str(getattr(lease, "reason", "") or "")
    return f"new: {why}" if mode == "new" and why else mode


async def warm(provider: str, req: ChatRequest) -> bool:
    """Start the chat's agy session for `req`'s system prompt without a model request (AgyPool.warm); False when it could not."""
    client = await asyncio.to_thread(_get_client, provider)
    if not hasattr(client, "warm"):
        return False   # claude: no session to warm
    req = replace(req, messages=late_notes_as_user(list(req.messages)))
    return bool(await asyncio.to_thread(client.warm, req.messages, req.model,
                                        req.extra.get("reasoning_effort") or req.extra.get("effort")))


async def stream(provider: str, req: ChatRequest) -> AsyncIterator[Event]:
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[Any] = asyncio.Queue()
    client = await asyncio.to_thread(_get_client, provider)
    # the sidecar runs this too: whatever sent the request, no system message after the conversation started (types.late_notes_as_user)
    req = replace(req, messages=late_notes_as_user(list(req.messages)))
    kwargs = _kwargs(provider, req)
    lease = None
    if hasattr(client, "lease"):   # agy: the session that goes on from this conversation, or a fresh one (agy_pool.py)
        try:
            lease = await asyncio.to_thread(client.lease, req.messages, req.tools, req.model,
                                            req.extra.get("reasoning_effort") or req.extra.get("effort"), req.purpose)
        except Exception as e:
            raise ProviderError(f"{provider}: cannot start ({type(e).__name__}: {e})") from e
        client, kwargs["messages"] = lease.client, lease.messages
    holder: dict[str, Any] = {}
    stop = threading.Event()

    def pump() -> None:
        try:
            s = client.chat.completions.create(**kwargs)
            holder["stream"] = s
            for chunk in s:
                if stop.is_set():
                    break
                loop.call_soon_threadsafe(q.put_nowait, chunk)
        except BaseException as e:  # noqa: BLE001 - forwarded to the consumer
            loop.call_soon_threadsafe(q.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, _DONE)

    t = threading.Thread(target=pump, daemon=True, name=f"{provider}-pump")
    t.start()

    calls: dict[int, dict[str, str]] = {}
    current: dict[int, int] = {}   # the index a delta gives -> the slot its call is being collected in
    finish: str | None = None
    usage = Usage()
    thinking = False
    said: list[str] = []
    ok = False
    try:
        while True:
            item = await q.get()
            if item is _DONE:
                ok = True
                break
            if isinstance(item, BaseException):
                raise ProviderError(f"{provider}: {item}", status=_status(item)) from item
            u = _g(item, "usage")
            if u:
                details = _g(u, "prompt_tokens_details")
                cached = _g(details, "cached_tokens", 0) or 0
                prompt = _g(u, "prompt_tokens", 0) or 0
                if lease is not None:   # agy reports input WITHOUT the cached part (measured 2026-11-13): count the whole prompt, as for the API
                    prompt += cached
                usage = Usage(prompt, _g(u, "completion_tokens", 0) or 0, cached, _g(u, "cache_creation_input_tokens", 0) or 0,
                              session=session_note(lease))
            for ch in _g(item, "choices", None) or []:
                d = _g(ch, "delta")
                if _g(d, "reasoning_content") and not thinking:
                    thinking = True
                    yield Thinking()
                if text := _g(d, "content"):
                    thinking = False
                    said.append(text)
                    yield TextDelta(text)
                for tc in _g(d, "tool_calls", None) or []:
                    idx = _g(tc, "index", 0) or 0
                    key = current.get(idx, idx)
                    named = _g(_g(tc, "function"), "name") or ""
                    if named and calls.get(key, {}).get("name"):
                        # agy numbers the calls of each <tool_call> block from 0, so two blocks both say index 0: a delta that
                        # names a function while this slot already has one is the next call, not more of the same name
                        key = max(calls) + 1
                    current[idx] = key
                    slot = calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = _g(tc, "id") or slot["id"]
                    fn = _g(tc, "function")
                    slot["name"] += _g(fn, "name") or ""
                    a = _g(fn, "arguments") or ""
                    slot["arguments"] += a if isinstance(a, str) else json.dumps(a)
                finish = _g(ch, "finish_reason") or finish
    finally:
        stop.set()
        s = holder.get("stream")
        if s is not None:  # consumer cancelled or finished: kill the CLI process
            await asyncio.to_thread(lambda: getattr(s, "close", lambda: None)())
        else:
            cancel = getattr(client, "cancel", None)
            if cancel and t.is_alive():
                cancel()
        if lease is not None:
            lease.release(ok, "".join(said))

    yield usage
    out = [ToolCall(c["id"] or f"call_{i + 1}", c["name"], c["arguments"] or "{}") for i, c in sorted(calls.items())]
    for c in out:
        yield c
    yield Done("tool_calls" if out else "length" if finish == "length" else "stop")  # type: ignore[arg-type]
