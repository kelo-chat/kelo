"""Request fields an endpoint turned away, remembered per endpoint address and model.

A custom OpenAI-compatible server may answer 400 to a field it does not know (`reasoning_effort`, the thinking level). The client
then sends the request again without it (client.stream_chat) and notes here that this server and model do not take it, so later
requests leave it out from the start and the app can say so quietly (effort.refused_note).

The server keeps the notes in its database: `on_change` is called with every change, `restore` loads them at start-up.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

EFFORT_FIELD = "reasoning_effort"

_seen: dict[str, float] = {}   # "field|base_url|model" -> when it was noted (epoch seconds)
on_change: Callable[[dict[str, float]], None] | None = None


def _key(field: str, base_url: str, model: str) -> str:
    return f"{field}|{(base_url or '').rstrip('/')}|{model}"


def mark(field: str, base_url: str, model: str) -> None:
    key = _key(field, base_url, model)
    if key in _seen:
        return
    _seen[key] = time.time()
    if on_change is not None:
        try:
            on_change(dict(_seen))
        except Exception:  # noqa: BLE001 - saving the note must never break a reply
            pass


def refused(field: str, base_url: str, model: str) -> bool:
    return _key(field, base_url, model) in _seen


def forget(base_url: str | None = None) -> None:
    """Drop the notes for one address (it was changed or set again), or all of them."""
    if base_url is None:
        _seen.clear()
    else:
        prefix = (base_url or "").rstrip("/")
        for k in [k for k in _seen if k.split("|", 2)[1] == prefix]:
            _seen.pop(k, None)
    if on_change is not None:
        try:
            on_change(dict(_seen))
        except Exception:  # noqa: BLE001
            pass


def restore(saved: Any) -> None:
    if isinstance(saved, dict):
        for k, v in saved.items():
            if isinstance(k, str) and k.count("|") >= 2 and isinstance(v, (int, float)):
                _seen.setdefault(k, float(v))


def mentions(body: str, field: str) -> bool:
    """The error text names the field (`reasoning_effort`, or reasoning in general)."""
    low = (body or "").lower()
    return field in low or "reasoning" in low
