"""Structured facts a tool hands to the apps next to its text (docs/no-word-lists.md, hc-rest A2, A3). The apps used to read a tool's
sentence ("... already connected", "Saved the key for 'x' ... (hosts: ...)") to decide what card to show; a tool now puts the facts here and
the loop sends them in the tool_result event as "facts". The model never sees them. One dict per tool call, set by the loop."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

current: ContextVar[dict[str, Any] | None] = ContextVar("tool_facts", default=None)


def put(**facts: Any) -> None:
    """Add facts to the call that is running (a no-op outside a run)."""
    box = current.get()
    if box is not None:
        box.update(facts)
