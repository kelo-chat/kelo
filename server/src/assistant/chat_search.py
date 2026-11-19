"""Search across the whole conversation: the words of earlier chat messages, and the messages around a hit.

GET /api/messages/search?q=&limit=&before=  -> [{"id", "role", "snippet", "created_at"}], newest first. The snippet is a window of the
message text with the match wrapped in « and ». Matches are case-insensitive substrings. Queries of three or more characters use the
FTS5 trigram index (store.SEARCH_INDEX, kept in step by triggers, so Korean and other languages match inside words); shorter queries
cannot use a trigram index, so they scan the last SHORT_SCAN_ROWS messages with LIKE.
GET /api/messages/around?id=&n=  -> the messages around a hit, oldest first, in the same shape as GET /api/messages.

What is searched and shown: the chat's own messages (role user or assistant), as GET /api/messages shows them. The rows the server
keeps for its own use (handoffs, schedules, goals, recheck and share notes: the user-role rows that start with a mark) are left out
here too. Nothing else is in the messages table, and each conversation is the owner's own. Tool results are not stored as messages.
"""

from __future__ import annotations

import re
from typing import Any

from .calls import CALL_MARK
from .checklist import CHECKLIST_MARK
from .goals import GOAL_MARK
from .nest_drafts import NEST_SHARE_MARK
from .recheck import RECHECK_MARK
from .schedules import SCHEDULE_MARK
from .watchers import WATCH_MARK
from .workers import HANDOFF_MARK

# user-role rows that are not the user's words (api/conversation.py lists them for GET /api/messages; this is the same list)
NOT_THE_USERS_WORDS: tuple[str, ...] = (HANDOFF_MARK, WATCH_MARK, GOAL_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK, CHECKLIST_MARK, CALL_MARK)

DEFAULT_LIMIT, MAX_LIMIT = 20, 50
DEFAULT_AROUND, MAX_AROUND = 10, 50
MIN_TRIGRAM = 3
SHORT_SCAN_ROWS = 5_000
SNIPPET_CHARS = 40          # characters kept on each side of the match
OPEN, CLOSE = "«", "»"


def clamp(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _visible(alias: str = "m") -> tuple[str, list[Any]]:
    """SQL that leaves out the user-role rows that are not the user's words (the same rule as GET /api/messages)."""
    parts = " OR ".join(f"substr({alias}.content, 1, ?) = ?" for _ in NOT_THE_USERS_WORDS)
    args: list[Any] = []
    for mark in NOT_THE_USERS_WORDS:
        args += [len(mark), mark]
    return f"NOT ({alias}.role = 'user' AND ({parts}))", args


def snippet(content: str, query: str) -> str:
    """A short window of `content` around the first match of `query` (case-insensitive), the match wrapped in « and »."""
    text = re.sub(r"\s+", " ", content).strip()
    found = re.search(re.escape(query), text, re.IGNORECASE) if query else None
    if found is None:
        return text[: 2 * SNIPPET_CHARS] + ("…" if len(text) > 2 * SNIPPET_CHARS else "")
    start = max(0, found.start() - SNIPPET_CHARS)
    end = min(len(text), found.end() + SNIPPET_CHARS)
    return ("…" if start > 0 else "") + text[start:found.start()] + OPEN + found.group(0) + CLOSE + text[found.end():end] \
        + ("…" if end < len(text) else "")


def search(store: Any, query: str, *, limit: int = DEFAULT_LIMIT, before: int | None = None) -> list[dict[str, Any]]:
    """The newest matches first (ids below `before` when given). An empty query finds nothing."""
    q = str(query or "").strip()
    if not q:
        return []
    limit = clamp(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    visible, vargs = _visible("m")
    if len(q) >= MIN_TRIGRAM:
        match = '"' + q.replace('"', '""') + '"'   # one phrase: the words in this order, no FTS operators
        rows = store._all(
            "SELECT m.id, m.role, m.content, m.created_at FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
            f"WHERE messages_fts MATCH ? AND (? IS NULL OR m.id < ?) AND {visible} ORDER BY m.id DESC LIMIT ?",
            (match, before, before, *vargs, limit))
    else:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = store._all(
            "SELECT m.id, m.role, m.content, m.created_at FROM messages m "
            "WHERE m.id > (SELECT COALESCE(MAX(id), 0) FROM messages) - ? AND (? IS NULL OR m.id < ?) "
            f"AND m.content LIKE ? ESCAPE '\\' AND {visible} ORDER BY m.id DESC LIMIT ?",
            (SHORT_SCAN_ROWS, before, before, like, *vargs, limit))
    return [{"id": int(r["id"]), "role": r["role"], "snippet": snippet(str(r["content"] or ""), q),
             "created_at": float(r["created_at"])} for r in rows]


def around(store: Any, anchor: int, *, n: int = DEFAULT_AROUND) -> list[dict[str, Any]] | None:
    """`n` visible messages before `anchor`, the anchor itself and `n` after it, oldest first. None when the anchor is unknown or hidden."""
    n = clamp(n, DEFAULT_AROUND, 1, MAX_AROUND)
    visible, vargs = _visible("m")
    cols = "m.id, m.role, m.content, m.created_at, m.reaction, m.via, m.call_id"
    here = store._all(f"SELECT {cols} FROM messages m WHERE m.id = ? AND {visible}", (anchor, *vargs))
    if not here:
        return None
    before = store._all(f"SELECT {cols} FROM messages m WHERE m.id < ? AND {visible} ORDER BY m.id DESC LIMIT ?", (anchor, *vargs, n))
    after = store._all(f"SELECT {cols} FROM messages m WHERE m.id > ? AND {visible} ORDER BY m.id ASC LIMIT ?", (anchor, *vargs, n))
    return list(reversed(before)) + here + after
