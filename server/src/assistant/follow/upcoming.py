"""Things said in chat become follow items (part C).

The nightly look-back (reflection.py) asks, in its own request, for dated things in the owner's life that came up in the conversation
and that the owner would want looked after ("the booking opens on Feb 11"). A date comes only from the owner's own words: each item
needs a quote found in the owner's messages of that run (reflection.find_quote), and its day of the month must be a number the owner
wrote in that run. The assistant's own answers are never a source of a date. A made-up quote, a past date, a date too far away or a
thing already in the list (any source: a calendar or mail item the model is shown, and a repeat of a chat item) is left out, and a
chat item that the owner's later words correct is removed. The rest are stored as follow items with source "chat" and state "later":
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
LISTED_MAX = 20                     # items shown to the model as already kept (the soonest ones)
TITLE_MAX, PLACE_MAX, WHAT_MAX = 120, 120, 200

EXTRACT_PROMPT = """

Upcoming. In the same JSON, also give "upcoming": [{"title": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "place": "...", "what": "...", "quote": "...", "same_as": "..."}] and "upcoming_drop": ["..."].
- At most 5 dated things in the owner's life that came up in this conversation and that the owner would want looked after: an event (a trip, an appointment), a booking or sale window that opens, a deadline. Not things already past, not vague dates.
- The owner's own words are the only source of a date in this list. Never take a date from your own earlier answers, forecasts or guesses about what the owner meant; a question that names no date gives no date. The calendar and mail items under "Already kept" are dates you may rely on.
- same_as: when the thing is already under "Already kept" (any source), give that item's key here; such an item is linked to what is kept and is not added again.
- upcoming_drop: keys of chat items under "Already kept" that the owner's own later words correct or cancel (a wrong date, a trip that is not happening). Calendar and mail keys are never listed here.
- title: short, in the language named above. date: the day as YYYY-MM-DD, the year from "Today". time and place: only when the owner's words give them, else leave them out. what: what the assistant should do or remind the owner of, in English, at most 200 characters.
- quote: the owner's words that state the thing, copied exactly from one of their messages (no paraphrase, no translation). An item without such a quote is thrown away.
- When nothing fits, give [] and []."""


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

    def kept(self) -> list[dict[str, Any]]:
        """Every follow item the look-back is shown: calendar, mail and chat, not stopped, soonest first (LISTED_MAX of them)."""
        now = self.clock()
        items = [it for it in self.store.list_items() if it.get("state") != "stopped" and float(it["starts_at"]) >= now - 86400]
        return items[:LISTED_MAX]

    def known_block(self) -> str:
        """Today's date (so the model can name the year) and what is already kept, with each item's key and source."""
        today = datetime.fromtimestamp(self.clock(), self.tz()).date().isoformat()
        lines = [f"- {it['key']}: {it['title']} on {self._local_day(float(it['starts_at']))} ({it.get('source') or 'unknown'})"
                 for it in self.kept()]
        return f"Today: {today}\nAlready kept (do not add them again; same_as and upcoming_drop use these keys):\n" + (
            "\n".join(lines) if lines else "(none)")

    def _known(self, item: Any) -> bool:
        """The same item already followed (same key, or the same title on the same day, whatever its source or state)."""
        day = self._local_day(item.starts_at)
        for it in self.store.list_items():
            if it["key"] == item.key:
                return True
            if normalize_title(it["title"]) == normalize_title(item.title) and self._local_day(float(it["starts_at"])) == day:
                return True
        return False

    def apply(self, out: dict[str, Any], owner_words: list[str]) -> int:
        """The model's "upcoming" -> chat items stored as "later" (at most PER_NIGHT), and its "upcoming_drop" -> chat items the owner
        corrected are removed. `owner_words`: the owner's own messages of the run (never the assistant's)."""
        tz, now, added = self.tz(), self.clock(), 0
        numbers = {int(n) for w in owner_words for n in re.findall(r"\d+", w)}   # the numbers the owner wrote
        for key in out.get("upcoming_drop") if isinstance(out.get("upcoming_drop"), list) else []:
            row = self.store.get_item(str(key)) if isinstance(key, str) else None
            if row is not None and row.get("source") == "chat":
                self.store.delete_item(row["key"])
        items = out.get("upcoming") if isinstance(out.get("upcoming"), list) else []
        known = {it["key"] for it in self.store.list_items()}
        for it in items[:PER_NIGHT]:
            if not isinstance(it, dict):
                continue
            title = _line(it.get("title"), TITLE_MAX)
            if _line(it.get("same_as"), 200) in known:   # already kept under its own source: linked, nothing new
                continue
            quote = find_quote(it.get("quote"), owner_words)
            if not title or quote is None:
                log.info("nightly look-back: left out an upcoming item not backed by the owner's words: %r", title[:80])
                continue
            date = _line(it.get("date"), 10)
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or int(date[8:10]) not in numbers:
                log.info("nightly look-back: left out an upcoming item whose day the owner never wrote: %r", title[:80])
                continue
            try:
                item = make_chat_item(title, date, _line(it.get("time"), 5) or None,
                                      place=_line(it.get("place"), PLACE_MAX), what=_line(it.get("what"), WHAT_MAX),
                                      quote=quote, tz=tz)
            except ValueError:
                log.info("nightly look-back: left out an upcoming item with a bad date: %r", title[:80])
                continue
            if not now < item.starts_at <= now + MAX_AHEAD_DAYS * 86400:
                log.info("nightly look-back: left out an upcoming item that is past or too far: %r", title[:80])
                continue
            if self.store.is_removed(item.key, item.starts_at) or self._known(item):
                continue   # the owner took it off the list before: a later look does not put it back
            self.store.save_item(item, state="later")
            known.add(item.key)
            added += 1
        return added

    def recent_titles(self, now: float) -> list[str]:
        """Titles of the chat items made in the last day or so and still followed (the briefing's line)."""
        return [it["title"] for it in self.store.list_items()
                if it.get("source") == "chat" and it.get("state") in ("later", "watching")
                and float(it.get("created_at") or 0) >= now - RECENT_SECONDS]
