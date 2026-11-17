"""OpenAI Responses-style API (xAI, Ramp router, Meta, OpenAI)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..types import ChatRequest, Done, Event, Message, ProviderError, TextDelta, Thinking, ToolCall, ToolSpec, Usage
from ..vision import responses_parts
from ._sse import parse_json, raise_for_status, sse_events


def convert_input(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for m in messages:
        role, content = m["role"], m.get("content")
        if role == "system":
            if content:
                instructions.append(str(content))
        elif role in ("user", "assistant"):
            if content:
                items.append({"role": role, "content": responses_parts(content, role) if isinstance(content, list) else content})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                items.append(
                    {"type": "function_call", "call_id": tc["id"], "name": fn["name"], "arguments": fn.get("arguments") or "{}"}
                )
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": str(content or "")})
    return ("\n\n".join(instructions) or None), items


def convert_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "parameters": t["function"].get("parameters") or {"type": "object", "properties": {}},
        }
        for t in tools
    ]


async def stream(
    client: httpx.AsyncClient, base_url: str, headers: dict[str, str], req: ChatRequest
) -> AsyncIterator[Event]:
    instructions, items = convert_input(req.messages)
    body: dict[str, Any] = {"model": req.model, "input": items, "stream": True}
    if instructions:
        body["instructions"] = instructions
    if req.tools:
        body["tools"] = convert_tools(req.tools)
    if req.max_tokens is not None:
        body["max_output_tokens"] = req.max_tokens
    if req.temperature is not None:
        body["temperature"] = req.temperature
    body.update(req.extra)

    calls: dict[str, dict[str, str]] = {}  # item_id -> call
    order: list[str] = []
    finish = "stop"
    completed = False
    thinking = False
    async with client.stream("POST", f"{base_url}/responses", json=body, headers=headers) as r:
        await raise_for_status(r)
        async for name, data in sse_events(r):
            ev = parse_json(data)
            if not ev:
                continue
            kind = name or ev.get("type")
            if not thinking and (str(kind).startswith("response.reasoning")
                                 or (kind == "response.output_item.added" and (ev.get("item") or {}).get("type") == "reasoning")):
                thinking = True
                yield Thinking()
            if kind == "response.output_text.delta" and ev.get("delta"):
                thinking = False
                yield TextDelta(ev["delta"])
            elif kind == "response.output_item.added":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    key = item.get("id") or item.get("call_id")
                    calls[key] = {"id": item.get("call_id") or key, "name": item.get("name", ""), "arguments": ""}
                    order.append(key)
            elif kind == "response.function_call_arguments.delta":
                if (key := ev.get("item_id")) in calls:
                    calls[key]["arguments"] += ev.get("delta", "")
            elif kind == "response.output_item.done":
                item = ev.get("item") or {}
                key = item.get("id") or item.get("call_id")
                if item.get("type") == "function_call" and key in calls and item.get("arguments"):
                    calls[key]["arguments"] = item["arguments"]
            elif kind == "response.completed":
                completed = True
                resp = ev.get("response") or {}
                if u := resp.get("usage"):
                    yield Usage(u.get("input_tokens", 0), u.get("output_tokens", 0), (u.get("input_tokens_details") or {}).get("cached_tokens") or 0,
                                reasoning_tokens=int((u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0))
                if (resp.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
                    finish = "length"
            elif kind == "response.incomplete":
                completed = True
                if ((ev.get("response") or {}).get("incomplete_details") or {}).get("reason") == "max_output_tokens":
                    finish = "length"
            elif kind in ("response.failed", "error"):
                err = (ev.get("response") or {}).get("error") or ev.get("error") or {}
                code = err.get("code")
                raise ProviderError(f"provider stream error{f' [{code}]' if code else ''}: {err.get('message', 'unknown')}", code=str(code or ""))
    if not completed:
        raise ProviderError("provider stream ended without response.completed")

    for key in order:
        c = calls[key]
        yield ToolCall(c["id"], c["name"], c["arguments"] or "{}")
    if order and finish == "stop":
        finish = "tool_calls"
    yield Done(finish)  # type: ignore[arg-type]
