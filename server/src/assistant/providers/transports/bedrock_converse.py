"""AWS Bedrock Converse API (needs the optional `aws` extra: boto3).

boto3 is blocking, so the stream is read in a worker thread and handed to asyncio
through a queue. Credentials come from boto3's normal chain (env vars, profile,
SSO, instance role, or a Bedrock API key in AWS_BEARER_TOKEN_BEDROCK).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..types import ChatRequest, Done, Event, Message, ProviderError, TextDelta, ToolCall, ToolSpec, Usage
from ..vision import bedrock_blocks

_STOP = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
_END = object()
CACHE_POINT = {"cachePoint": {"type": "default"}}
LATE_SYSTEM = "[Not from the user: a note from the app.]\n"


def caches(model_id: str) -> bool:
    """Whether the model takes Converse cache points (Claude and Nova models); others would refuse the request."""
    m = (model_id or "").lower()
    return "anthropic" in m or "claude" in m or "nova" in m


def add_cache_points(kwargs: dict[str, Any], stable_tools: int = 0) -> None:
    """Cache points (at most four, as with Anthropic's breakpoints; see anthropic_messages): after the stable leading tools when more
    follow, after the system text (or after the tools when there is none), after the message before the last and after the last."""
    tools = (kwargs.get("toolConfig") or {}).get("tools")
    if tools:
        if 0 < stable_tools < len(tools):
            tools.insert(stable_tools, dict(CACHE_POINT))
        if not kwargs.get("system"):
            tools.append(dict(CACHE_POINT))
    if kwargs.get("system"):
        kwargs["system"].append(dict(CACHE_POINT))
    for m in (kwargs.get("messages") or [])[-2:]:
        if m.get("content"):
            m["content"].append(dict(CACHE_POINT))


def convert_messages(messages: list[Message]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        role, content = m["role"], m.get("content")
        if role == "system" and not out:   # the leading system text
            if content:
                system.append({"text": str(content)})
        elif role == "system":            # later: marked text of the user turn, so the cached system text stays as it was
            if content:
                push("user", [{"text": LATE_SYSTEM + str(content)}])
        elif role == "user":
            push("user", [{"text": content}] if isinstance(content, str) else bedrock_blocks(list(content or [])))
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"text": content})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"toolUse": {"toolUseId": tc["id"], "name": fn["name"], "input": args}})
            if blocks:
                push("assistant", blocks)
        elif role == "tool":
            push("user", [{"toolResult": {"toolUseId": m["tool_call_id"], "content": [{"text": str(content or "")}]}}])
    return system, out


def convert_tools(tools: list[ToolSpec]) -> dict[str, Any]:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "inputSchema": {"json": t["function"].get("parameters") or {"type": "object", "properties": {}}},
                }
            }
            for t in tools
        ]
    }


def default_client(region: str | None = None) -> Any:
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise ProviderError("bedrock needs the 'aws' extra: uv sync --extra aws") from e
    region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("bedrock-runtime", region_name=region)


async def stream(req: ChatRequest, client: Any | None = None, region: str | None = None) -> AsyncIterator[Event]:
    system, messages = convert_messages(req.messages)
    kwargs: dict[str, Any] = {"modelId": req.model, "messages": messages}
    if system:
        kwargs["system"] = system
    if req.tools:
        kwargs["toolConfig"] = convert_tools(req.tools)
    inference: dict[str, Any] = {}
    if req.max_tokens is not None:
        inference["maxTokens"] = req.max_tokens
    if req.temperature is not None:
        inference["temperature"] = req.temperature
    if inference:
        kwargs["inferenceConfig"] = inference
    if caches(req.model):
        add_cache_points(kwargs, req.stable_tools)
    kwargs.update(req.extra)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()

    def pump() -> None:
        try:
            bedrock = client or default_client(region)
            for ev in bedrock.converse_stream(**kwargs)["stream"]:
                loop.call_soon_threadsafe(queue.put_nowait, ev)
        except Exception as e:  # noqa: BLE001 - surfaced to the consumer below
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _END)

    task = asyncio.create_task(asyncio.to_thread(pump))
    tools: dict[int, dict[str, str]] = {}
    finish = "stop"
    try:
        while (ev := await queue.get()) is not _END:
            if isinstance(ev, ProviderError):
                raise ev
            if isinstance(ev, Exception):
                raise ProviderError(f"bedrock error: {type(ev).__name__}: {str(ev)[:300]}") from None
            if "contentBlockStart" in ev:
                start = ev["contentBlockStart"]
                if tu := (start.get("start") or {}).get("toolUse"):
                    tools[start["contentBlockIndex"]] = {"id": tu["toolUseId"], "name": tu["name"], "arguments": ""}
            elif "contentBlockDelta" in ev:
                d = ev["contentBlockDelta"]
                delta = d.get("delta") or {}
                if delta.get("text"):
                    yield TextDelta(delta["text"])
                elif "toolUse" in delta and d["contentBlockIndex"] in tools:
                    tools[d["contentBlockIndex"]]["arguments"] += delta["toolUse"].get("input", "")
            elif "messageStop" in ev:
                finish = _STOP.get(ev["messageStop"].get("stopReason", ""), "other")
            elif "metadata" in ev:
                u = ev["metadata"].get("usage") or {}
                read, write = u.get("cacheReadInputTokens") or 0, u.get("cacheWriteInputTokens") or 0
                yield Usage((u.get("inputTokens") or 0) + read + write, u.get("outputTokens", 0), read, write)
        for index in sorted(tools):
            t = tools[index]
            yield ToolCall(t["id"], t["name"], t["arguments"] or "{}")
        yield Done(finish)  # type: ignore[arg-type]
    finally:
        await task
