"""OpenAI-compatible /chat/completions (OpenRouter, DeepSeek, Ollama, vLLM, Gemini-compat, ...)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from ..types import ChatRequest, Done, Event, TextDelta, Thinking, ToolCall, Usage
from ._sse import parse_json, raise_for_status, sse_events

_FINISH = {"stop": "stop", "tool_calls": "tool_calls", "length": "length"}


def reasoning_tokens(usage: dict) -> int:
    """Output tokens spent on reasoning: `completion_tokens_details.reasoning_tokens` (OpenAI and most compatible servers) or a
    top-level `reasoning_tokens` (some servers); 0 when not reported."""
    details = usage.get("completion_tokens_details") or {}
    n = (details.get("reasoning_tokens") if isinstance(details, dict) else None) or usage.get("reasoning_tokens") or 0
    try:
        return max(0, int(n))
    except (TypeError, ValueError):
        return 0


async def stream(
    client: httpx.AsyncClient, base_url: str, headers: dict[str, str], req: ChatRequest
) -> AsyncIterator[Event]:
    body: dict = {
        "model": req.model,
        "messages": req.messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if req.tools:
        body["tools"] = req.tools
    if req.max_tokens is not None:
        body["max_tokens"] = req.max_tokens
    if req.temperature is not None:
        body["temperature"] = req.temperature
    body.update(req.extra)

    calls: dict[int, dict[str, str]] = {}
    finish = "stop"
    thinking = False
    async with client.stream("POST", f"{base_url}/chat/completions", json=body, headers=headers) as r:
        await raise_for_status(r)
        async for _, data in sse_events(r):
            if data.strip() == "[DONE]":
                break
            chunk = parse_json(data)
            if not chunk:
                continue
            if usage := chunk.get("usage"):
                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0   # implicit caching, where reported
                yield Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), cached,
                            reasoning_tokens=reasoning_tokens(usage))
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                # reasoning (DeepSeek, EXAONE, vLLM `reasoning_content`; OpenRouter `reasoning`) is a status signal only: its text is
                # never passed on, so it can never end up in the answer
                if (delta.get("reasoning_content") or delta.get("reasoning")) and not thinking:
                    thinking = True
                    yield Thinking()
                if (text := delta.get("content")) and isinstance(text, str):
                    thinking = False
                    yield TextDelta(text)
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    finish = _FINISH.get(choice["finish_reason"], "other")

    for index in sorted(calls):
        c = calls[index]
        yield ToolCall(c["id"] or f"call_{index}", c["name"], c["arguments"] or "{}")
    if calls and finish == "stop":
        finish = "tool_calls"
    yield Done(finish)  # type: ignore[arg-type]
