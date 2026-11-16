from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..types import ProviderError


async def sse_events(response: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    """Yield (event_name, data) pairs from a text/event-stream response."""
    event: str | None = None
    data: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


def parse_json(data: str) -> dict[str, Any] | None:
    try:
        value = json.loads(data)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


async def raise_for_status(response: httpx.Response) -> None:
    """Raise ProviderError with the server's message but never our request headers."""
    if response.status_code < 400:
        return
    body = (await response.aread()).decode("utf-8", "replace")[:500]
    raise ProviderError(f"HTTP {response.status_code} from provider: {body}", status=response.status_code,
                        retry_after=retry_after(response.headers.get("retry-after")))


def retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header (a number of seconds, or an HTTP date); None when absent or unreadable."""
    if not value or not value.strip():
        return None
    v = value.strip()
    if v.replace(".", "", 1).isdigit():
        return float(v)
    try:
        from email.utils import parsedate_to_datetime
        import time as _time
        return max(0.0, parsedate_to_datetime(v).timestamp() - _time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
