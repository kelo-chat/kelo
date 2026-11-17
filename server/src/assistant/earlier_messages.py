"""`read_earlier_messages`: the exact words of the chat messages that compaction folded into the rolling summary.

A summary keeps the gist, not the wording, a number or a date. This read-only tool pages back through the conversation's own
messages (store.messages: the user's and the assistant's, never tool results or anything of other users) that are no longer in the
live context: the ones after the last /new, up to the summary's `up_to_message_id`. Newest page first by default, oldest first inside
a page, with the message id so the agent can page on with `before`. The text is the user's own words, matched with SQL LIKE.
"""

from __future__ import annotations

from typing import Any

from .agent import Tool
from .compaction import TIME_TAG, stamp, zone_of
from .secret_guard import scrub_rows

DEFAULT_LIMIT, MAX_LIMIT = 10, 20
TEXT_CHARS = 2_000


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_earlier(store: Any, args: dict[str, Any]) -> str:
    folded = store.conversation_summary()["up_to_message_id"]
    since = store.session_start() if hasattr(store, "session_start") else 0
    before = folded + 1                                       # only what was condensed: nothing at or after the summary's end
    if args.get("before") not in (None, ""):
        before = min(before, _int(args.get("before"), before))
    limit = max(1, min(MAX_LIMIT, _int(args.get("limit"), DEFAULT_LIMIT)))
    query = str(args.get("query") or "").strip()
    if folded <= since:
        return "No messages have been condensed yet: everything of this conversation is still in your context."
    rows, older = store.condensed_messages(before_id=before, after_id=since, query=query, limit=limit)
    if not rows:
        return "No earlier messages match." + (f" (query: {query})" if query else "") + "\nolder messages: no"
    zone = zone_of(store.profile().get("timezone"))
    lines = []
    for m in scrub_rows(store, rows):
        text = TIME_TAG.sub("", str(m["content"] or "")).strip()
        if len(text) > TEXT_CHARS:
            text = text[:TEXT_CHARS] + "…"
        lines.append(f"#{m['id']} {stamp(m['created_at'], zone)} {m['role']}: {text}".replace("  ", " "))
    more = "yes (call again with before=%d)" % rows[0]["id"] if older else "no"
    return "\n".join(lines) + f"\nolder messages: {more}"


def earlier_messages_tool(store: Any) -> Tool:
    async def read_earlier_messages(args: dict[str, Any]) -> str:
        return read_earlier(store, args)

    return Tool(
        "read_earlier_messages",
        "Read the exact earlier messages of this conversation that were condensed into the summary: use it when you need the exact "
        "wording, a number, a date or a detail the summary does not have.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "optional: only messages containing this text (case-insensitive), in the words the user used"},
            "before": {"type": "integer", "description": "optional: only messages older than this message id (to page back; default: the newest condensed ones)"},
            "limit": {"type": "integer", "description": "how many messages, 1 to 20 (default 10)"}}},
        read_earlier_messages, read_only=True)
