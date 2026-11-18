"""The owner's own messages: the one place memory code asks "did the owner say this?" (docs/memory-pipeline.md, "Owner's words only").

A row of `messages` is the owner's own message when its `author` is `owner`: typed or pasted in the app, sent from the paired private
messenger chat, or sent by an API client with the owner's token. `forwarded` is a messenger message the owner forwarded (someone else's
words), `tapped` is Kelo's own wording sent by a tap (an offer label, a briefing step). Every other row (assistant replies, every hidden
handoff, watcher, recheck, goal, schedule and nest row) has an empty author, so it is never the owner's words.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .secret_guard import scrub_rows

OWNER, FORWARDED, TAPPED = "owner", "forwarded", "tapped"
AUTHORS = (OWNER, FORWARDED, TAPPED)
BACKFILL_META = "messages:author_v1"


def normalize(text: Any) -> str:
    """Quotes and whitespace folded, for verbatim checks (a quote matches whatever the quote marks or spacing)."""
    s = str(text or "")
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


class OwnerMessages:
    """The owner's own messages: rows of `messages` whose author is `owner`."""

    def __init__(self, store: Any):
        self.store = store

    def is_owner(self, message_id: int) -> bool:
        return bool(self.store._all("SELECT 1 FROM messages WHERE id = ? AND author = ?", (int(message_id or 0), OWNER)))

    def text(self, message_id: int) -> str | None:
        """The text of one owner message (secrets the owner marked are removed); None unless it is the owner's own."""
        rows = self.store._all("SELECT id, content FROM messages WHERE id = ? AND author = ?", (int(message_id or 0), OWNER))
        if not rows:
            return None
        return str(scrub_rows(self.store, [{"id": rows[0]["id"], "content": rows[0]["content"]}])[0]["content"] or "")

    def since(self, ts: float, limit: int = 2000) -> list[dict[str, Any]]:
        """The owner's messages written after `ts`, oldest first: {"id", "role", "text", "created_at"}."""
        rows = self.store._all("SELECT id, content, created_at FROM messages WHERE author = ? AND created_at > ? ORDER BY id LIMIT ?",
                               (OWNER, float(ts), int(limit)))
        return self._rows(rows)

    def recent(self, before_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """The newest owner messages (before `before_id` when given), oldest first."""
        rows = self.store._all("SELECT id, content, created_at FROM messages WHERE author = ? AND (? IS NULL OR id < ?) "
                               "ORDER BY id DESC LIMIT ?", (OWNER, before_id, before_id, int(limit)))
        return self._rows(rows)[::-1]

    def by_ids(self, ids: Iterable[int]) -> list[dict[str, Any]]:
        """The owner's messages with these ids (the ones an inbox line names, however old), oldest first."""
        wanted = [int(i or 0) for i in ids]
        if not wanted:
            return []
        marks = ",".join("?" * len(wanted))
        rows = self.store._all(f"SELECT id, content, created_at FROM messages WHERE author = ? AND id IN ({marks}) ORDER BY id",
                               (OWNER, *wanted))
        return self._rows(rows)

    def find(self, quote: str, ids: Iterable[int]) -> int | None:
        """The first of `ids` (owner messages only) whose text contains the quote, verbatim after `normalize`; None otherwise."""
        wanted = normalize(quote)
        if not wanted:
            return None
        for mid in ids:
            text = self.text(int(mid or 0))
            if text is not None and wanted in normalize(text):
                return int(mid)
        return None

    def _rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean = scrub_rows(self.store, [{"id": r["id"], "content": r["content"]} for r in rows])
        return [{"id": r["id"], "role": "user", "text": str(c.get("content") or ""), "created_at": r["created_at"]}
                for r, c in zip(rows, clean)]


def author_for(*, tapped: bool, forwarded: bool) -> str:
    """The author a new user message is stored with: tapped wording first, then a forward, else the owner's own."""
    return TAPPED if tapped else FORWARDED if forwarded else OWNER


def backfill_authors(db: Any) -> bool:
    """Once per database (meta `messages:author_v1`): every user row written before the author column is the owner's own, except the
    hidden rows the server writes in the user role (handoffs, watcher, recheck, goal, schedule, nest and automatic runs). Those are
    matched here, and only here, by their saved prefixes; afterwards the column decides. Returns True when it ran."""
    from .goals import GOAL_MARK
    from .nest_drafts import NEST_SHARE_MARK
    from .recheck import RECHECK_MARK
    from .reflection import AUTO_PREFIXES
    from .schedules import SCHEDULE_MARK
    from .watchers import WATCH_MARK
    from .workers import HANDOFF_MARK

    if db.execute("SELECT 1 FROM meta WHERE key = ?", (BACKFILL_META,)).fetchone():
        return False
    db.execute("UPDATE messages SET author = ? WHERE role = 'user' AND author = ''", (OWNER,))
    for prefix in (HANDOFF_MARK, WATCH_MARK, GOAL_MARK, SCHEDULE_MARK, RECHECK_MARK, NEST_SHARE_MARK, *AUTO_PREFIXES):
        if prefix:
            db.execute("UPDATE messages SET author = '' WHERE author = ? AND substr(content, 1, ?) = ?", (OWNER, len(prefix), prefix))
    db.execute("INSERT INTO meta(key, value) VALUES(?, ?)", (BACKFILL_META, "1"))
    db.commit()
    return True


CLAIMS_META = "memory_claims:owner_only_v1"


def retract_unowned_claims(db: Any) -> int:
    """Once per database (meta `memory_claims:owner_only_v1`): an active claim that is not the owner's own words (a speaker other than
    user, or a handle that is not one of the owner's messages) is retracted. The rows stay, so the log and memory_explain still show
    them. Returns how many were retracted (0 when it already ran)."""
    if db.execute("SELECT 1 FROM meta WHERE key = ?", (CLAIMS_META,)).fetchone():
        return 0
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_claims'").fetchone():
        return 0
    cur = db.execute("""UPDATE memory_claims SET status = 'retracted'
        WHERE status = 'active' AND (speaker <> 'user' OR handle NOT LIKE 'm%'
          OR CAST(substr(handle, 2) AS INTEGER) NOT IN (SELECT id FROM messages WHERE author = ?))""", (OWNER,))
    db.execute("INSERT INTO meta(key, value) VALUES(?, ?)", (CLAIMS_META, "1"))
    db.commit()
    return cur.rowcount
