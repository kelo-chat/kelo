"""Anthropic Messages API (Anthropic, MiniMax, other Anthropic-compatible gateways).

Prompt caching: up to four `cache_control` breakpoints (the API's limit), each on content later requests repeat unchanged. The cache
prefix runs tools → system → messages, so: after the stable leading tools (the core set, `ChatRequest.stable_tools`; only when more
tools follow), at the end of the leading system text (this covers every tool before it as well), on the message before the last (the
history up to this turn: the next turn starts with the same messages) and on the last block (the next step of this turn). System messages that come later in the conversation (an app note in the middle of a turn) are sent as marked user text,
not merged into the system prompt, so they never change the cached start."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..types import ChatRequest, Done, Event, Message, TextDelta, Thinking, ToolCall, ToolSpec, Usage
from ..vision import anthropic_blocks
from ._sse import parse_json, raise_for_status, sse_events

_STOP = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
DEFAULT_MAX_TOKENS = 4096  # the API requires max_tokens
CACHE = {"type": "ephemeral"}
LATE_SYSTEM = "[Not from the user: a note from the app.]\n"


def convert_messages(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI-shaped messages -> (system, anthropic messages)."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        # Anthropic wants strictly alternating roles; merge consecutive same-role turns.
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        role, content = m["role"], m.get("content")
        if role == "system" and not out:   # the leading system text: the system prompt
            if content:
                system_parts.append(content if isinstance(content, str) else json.dumps(content))
        elif role == "system":            # later in the conversation: marked text of the user turn, so the system prompt stays as it was
            if content:
                push("user", [{"type": "text", "text": LATE_SYSTEM + (content if isinstance(content, str) else json.dumps(content))}])
        elif role == "user":
            push("user", [{"type": "text", "text": content}] if isinstance(content, str) else anthropic_blocks(list(content or [])))
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": fn["name"], "input": args})
            if blocks:
                push("assistant", blocks)
        elif role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": str(content or "")}])
    return ("\n\n".join(system_parts) or None), out


def convert_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}},
        }
        for t in tools
    ]


def build_body(req: ChatRequest) -> dict[str, Any]:
    """The request body, with the cache breakpoints (see the module text)."""
    system, messages = convert_messages(req.messages)
    body: dict[str, Any] = {
        "model": req.model,
        "messages": messages,
        "max_tokens": req.max_tokens or DEFAULT_MAX_TOKENS,
        "stream": True,
    }
    if system:
        body["system"] = [{"type": "text", "text": system, "cache_control": dict(CACHE)}]
    if req.tools:
        tools = convert_tools(req.tools)
        if 0 < req.stable_tools < len(tools):
            tools[req.stable_tools - 1]["cache_control"] = dict(CACHE)
        if not system:   # nothing after the tools to mark: the tools' own end
            tools[-1]["cache_control"] = dict(CACHE)
        body["tools"] = tools
    for m in messages[-2:]:
        last = m["content"][-1] if m["content"] else None
        if isinstance(last, dict) and last.get("type") in ("text", "image", "tool_use", "tool_result", "document"):
            last["cache_control"] = dict(CACHE)
    return body


async def stream(
    client: httpx.AsyncClient, base_url: str, headers: dict[str, str], req: ChatRequest
) -> AsyncIterator[Event]:
    body = build_body(req)
    if req.temperature is not None:
        body["temperature"] = req.temperature
    body.update(req.extra)

    blocks: dict[int, dict[str, str]] = {}
    in_tokens = out_tokens = cache_read = cache_write = 0
    finish = "stop"
    thinking = False
    async with client.stream("POST", f"{base_url}/v1/messages", json=body, headers=headers) as r:
        await raise_for_status(r)
        async for name, data in sse_events(r):
            ev = parse_json(data)
            if not ev:
                continue
            kind = name or ev.get("type")
            if kind == "message_start":
                u = ev.get("message", {}).get("usage") or {}
                cache_read, cache_write = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
                in_tokens = (u.get("input_tokens") or 0) + cache_read + cache_write   # the whole prompt; the cached share apart
            elif kind == "content_block_start":
                cb = ev.get("content_block") or {}
                if cb.get("type") == "tool_use":
                    blocks[ev["index"]] = {"id": cb["id"], "name": cb["name"], "arguments": ""}
                elif cb.get("type") in ("thinking", "redacted_thinking") and not thinking:
                    thinking = True
                    yield Thinking()
            elif kind == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "thinking_delta" and not thinking:
                    thinking = True
                    yield Thinking()
                if d.get("type") == "text_delta" and d.get("text"):
                    thinking = False
                    yield TextDelta(d["text"])
                elif d.get("type") == "input_json_delta" and ev.get("index") in blocks:
                    blocks[ev["index"]]["arguments"] += d.get("partial_json", "")
            elif kind == "message_delta":
                if sr := (ev.get("delta") or {}).get("stop_reason"):
                    finish = _STOP.get(sr, "other")
                out_tokens = (ev.get("usage") or {}).get("output_tokens", out_tokens)
            elif kind == "error":
                from ..types import ProviderError

                raise ProviderError(f"provider stream error: {(ev.get('error') or {}).get('message', 'unknown')}",
                                    code=str((ev.get("error") or {}).get("type") or ""))

    for index in sorted(blocks):
        b = blocks[index]
        yield ToolCall(b["id"], b["name"], b["arguments"] or "{}")
    yield Usage(in_tokens, out_tokens, cache_read, cache_write)
    yield Done(finish)  # type: ignore[arg-type]
