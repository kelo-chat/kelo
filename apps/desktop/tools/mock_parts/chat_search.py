"""Chat search on the desktop mock: GET /api/messages/search and GET /api/messages/around (the same shapes as the real server's
server/src/assistant/chat_search.py, which the phone's search uses).

- GET /api/messages/search?q=&limit=&before=  -> [{"id", "role", "snippet", "created_at"}], newest first. Case-insensitive substring
  match on the message text; the snippet is a window around the first match with the match wrapped in « and ». limit defaults to 20
  and is clamped to 1..50; before=<id> only returns matches below that id.
- GET /api/messages/around?id=&n=  -> the n messages before the id, the message itself and the n after it, oldest first (n clamped
  to 1..50). An unknown id is a 404.

The messages are the chat's own: with KELO_MOCK_LONG_HISTORY=<n> they come from the long history (mock_parts/ios_catchup.py), else
from the state's messages.
- KELO_MOCK_CHAT_SEARCH_FAIL=1: both search endpoints answer 503 {"detail": "search unavailable"} (the page's failed line).
"""

from __future__ import annotations

import os
import re
import sys

SNIPPET_CHARS = 40          # characters kept on each side of the match (server/src/assistant/chat_search.py)
OPEN, CLOSE = "«", "»"


def _clamp(value: str, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _snippet(content: str, query: str) -> str:
    text = re.sub(r"\s+", " ", content).strip()
    found = re.search(re.escape(query), text, re.IGNORECASE) if query else None
    if found is None:
        return text[: 2 * SNIPPET_CHARS] + ("…" if len(text) > 2 * SNIPPET_CHARS else "")
    start = max(0, found.start() - SNIPPET_CHARS)
    end = min(len(text), found.end() + SNIPPET_CHARS)
    return ("…" if start > 0 else "") + text[start:found.start()] + OPEN + found.group(0) + CLOSE + text[found.end():end] \
        + ("…" if end < len(text) else "")


def _messages(s) -> list[dict]:
    # _long takes s.lock itself, so it is called before the lock is held here (the long history loads on first use)
    n = int(os.environ.get("KELO_MOCK_LONG_HISTORY") or 0)
    if n > 0:
        catchup = sys.modules.get("mock_parts_ios_catchup")
        if catchup is not None:
            return list(catchup._long(s, n))
    with s.lock:
        return list(s.messages)


def route(h, method: str, path: str, q: dict, s) -> bool:
    if method != "GET" or path not in ("/api/messages/search", "/api/messages/around"):
        return False
    if os.environ.get("KELO_MOCK_CHAT_SEARCH_FAIL"):
        h.send_json({"detail": "search unavailable"}, 503)
        return True
    msgs = _messages(s)
    if path == "/api/messages/search":
        query = (q.get("q", [""])[0] or "").strip()
        limit = _clamp(q.get("limit", ["20"])[0], 20, 1, 50)
        before = q.get("before", [""])[0]
        hits = []
        if query:
            needle = query.lower()
            for m in reversed(msgs):
                if before and m["id"] >= int(before):
                    continue
                content = str(m.get("content") or "")
                if needle in content.lower():
                    hits.append({"id": m["id"], "role": m["role"], "snippet": _snippet(content, query),
                                 "created_at": float(m["created_at"])})
                    if len(hits) == limit:
                        break
        h.send_json(hits)
        return True
    anchor = q.get("id", [""])[0]
    n = _clamp(q.get("n", ["10"])[0], 10, 1, 50)
    at = next((i for i, m in enumerate(msgs) if str(m["id"]) == anchor), None)
    if at is None:
        h.send_json({"detail": "unknown message"}, 404)
        return True
    h.send_json(msgs[max(0, at - n): at + n + 1])
    return True
