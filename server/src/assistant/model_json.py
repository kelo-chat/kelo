"""One short request to the owner's main model that must answer with one JSON object (the order check, the step judge of
docs/no-word-lists.md principle 3). A fresh context: only the text given here is sent, never the conversation. Any failure (no model,
an error, a timeout, an answer that is not one JSON object) is None: the callers then fail closed (ask the owner / open choice)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from .providers import ChatRequest, TextDelta

log = logging.getLogger(__name__)
TIMEOUT = 40.0
# 500 cut the forget judge's answer off mid-JSON on every call (owner 2026-11-17: "forget tool keeps failing"): a list of matches,
# or a model that writes a line before the object, needs more room. Callers that expect a long answer pass their own.
MAX_TOKENS = 1500


def parse_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in the answer (a model may fence it); None when there is none."""
    s = (text or "").strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        got = json.loads(s[start:end + 1])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


class ModelJson:
    """`stream_fn`: a metered stream function (server.counted(kind)); `model()` -> (provider, model) | None; `key_args(provider)`."""

    def __init__(self, stream_fn: Callable[..., AsyncIterator[Any]], model: Callable[[], tuple[str, str] | None],
                 key_args: Callable[[str], dict[str, Any]] = lambda _p: {}, timeout: float = TIMEOUT) -> None:
        self.stream_fn, self.model, self.key_args, self.timeout = stream_fn, model, key_args, timeout

    async def ask(self, system: str, user: str, *, max_tokens: int | None = None) -> dict[str, Any] | None:
        chosen = self.model()
        if not chosen:
            return None
        provider, model = chosen
        req = ChatRequest(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], tools=[],
                          max_tokens=max_tokens or MAX_TOKENS, extra={})

        async def run() -> str:
            parts: list[str] = []
            async for ev in self.stream_fn(provider, req, **self.key_args(provider)):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
            return "".join(parts)
        try:
            text = await asyncio.wait_for(run(), self.timeout)
            got = parse_object(text)
            if got is None:   # visible in the log: an answer that is not one JSON object (cut off, or prose only)
                log.warning("model json: no JSON object in the answer (%d chars; ends %r)", len(text), text[-40:])
            return got
        except Exception as e:  # noqa: BLE001 - fail closed
            log.debug("model json request failed: %s: %s", type(e).__name__, str(e)[:160])
            return None
