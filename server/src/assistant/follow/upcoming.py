"""Things said in chat become follow items (part C).

The nightly look-back (reflection.py) asks, in its own request, for dated things in the owner's life that came up in the conversation
and that the owner would want looked after ("the booking opens on Feb 11"). Each one needs a quote found in that run's messages (the
owner's or the assistant's, reflection.find_quote); a made-up quote, a past date, a date too far away or a repeat of a follow item
(same title on the same day, from any source) is left out. The rest are stored as follow items with source "chat" and state "later":
the runner plans them once they enter the look-ahead window.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..reflection import find_quote
from .items import make_chat_item, normalize_title
from .store import FollowStore

log = logging.getLogger(__name__)

PER_NIGHT = 5                       # chat items one nightly run may add
MAX_AHEAD_DAYS = 400                # a date further away waits for a later look-back
RECENT_SECONDS = 30 * 3600          # the briefing names the chat items made since about yesterday
LISTED_MAX = 20                     # chat items shown to the model as already kept
TITLE_MAX, PLACE_MAX, WHAT_MAX = 120, 120, 200

EXTRACT_PROMPT = """

Upcoming. In the same JSON, also give "upcoming": [{"title": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "place": "...", "what": "...", "quote": "..."}].
- At most 5 dated things in the owner's life that came up in this conversation and that the owner would want looked after: an event (a trip, an appointment), a booking or sale window that opens ("the booking opens on Feb 11"), a deadline. Not things already past, not vague dates, nothing listed under "Upcoming already kept".
- title: short, in the language named above. date: the day as YYYY-MM-DD, the year from "Today". time and place: only when the conversation gives them, else leave them out. what: what the assistant should do or remind the owner of, in English, at most 200 characters.
- quote: the words that state it, copied exactly from one of the messages (the owner's or the assistant's; no paraphrase, no translation). An item without such a quote is thrown away.
- When nothing fits, give []."""


def _line(value: Any, cap: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:cap]


class UpcomingExtractor:
    """Chat items from the nightly look-back: rides on that run's own request, so no call of its own."""

    def __init__(self, store: FollowStore, tz: Callable[[], ZoneInfo] = lambda: ZoneInfo("UTC"),
                 clock: Callable[[], float] | None = None):
        self.store = store
        self.tz = tz
        self.clock = clock or store.clock

    def prompt_part(self) -> str:
        return EXTRACT_PROMPT

    def _local_day(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.tz()).date().isoformat()

    def known_block(self) -> str:
        """Today's date (so the model can name the year) and the chat items already kept."""
        today = datetime.fromtimestamp(self.clock(), self.tz()).date().isoformat()
        kept = [it for it in self.store.list_items() if it.get("source") == "chat"][-LISTED_MAX:]
        lines = [f"- {it['title']} on {self._local_day(float(it['starts_at']))}" for it in kept]
        return f"Today: {today}\nUpcoming already kept (do not add them again):\n" + ("\n".join(lines) if lines else "(none)")

    def _known(self, item: Any) -> bool:
        """The same item already followed (same key, or the same title on the same day, whatever its source or state)."""
        day = self._local_day(item.starts_at)
        for it in self.store.list_items():
            if it["key"] == item.key:
                return True
            if normalize_title(it["title"]) == normalize_title(item.title) and self._local_day(float(it["starts_at"])) == day:
                return True
        return False

    def apply(self, out: dict[str, Any], words: list[str]) -> int:
        """The model's "upcoming" -> chat items stored as "later" (at most PER_NIGHT). `words`: the messages of the run, both sides."""
        items = out.get("upcoming") if isinstance(out.get("upcoming"), list) else []
        tz, now, added = self.tz(), self.clock(), 0
        for it in items[:PER_NIGHT]:
            if not isinstance(it, dict):
                continue
            title = _line(it.get("title"), TITLE_MAX)
            quote = find_quote(it.get("quote"), words)
            if not title or quote is None:
                log.info("nightly look-back: left out an upcoming item not backed by a quote: %r", title[:80])
                continue
            try:
                item = make_chat_item(title, _line(it.get("date"), 10), _line(it.get("time"), 5) or None,
                                      place=_line(it.get("place"), PLACE_MAX), what=_line(it.get("what"), WHAT_MAX),
                                      quote=quote, tz=tz)
            except ValueError:
                log.info("nightly look-back: left out an upcoming item with a bad date: %r", title[:80])
                continue
            if not now < item.starts_at <= now + MAX_AHEAD_DAYS * 86400:
                log.info("nightly look-back: left out an upcoming item that is past or too far: %r", title[:80])
                continue
            if self._known(item):
                continue
            self.store.save_item(item, state="later")
            added += 1
        return added

    def recent_titles(self, now: float) -> list[str]:
        """Titles of the chat items made in the last day or so and still followed (the briefing's line)."""
        return [it["title"] for it in self.store.list_items()
                if it.get("source") == "chat" and it.get("state") in ("later", "watching")
                and float(it.get("created_at") or 0) >= now - RECENT_SECONDS]
