"""Upcoming items for follow-through: items coming up in the owner's life from calendar and mail."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .. import big_events

LOOK_AHEAD_DAYS = 14

# Flight number: 2-character IATA airline designator followed by 1-4 digits (KE123, 7C101, AA1234)
FLIGHT_RE = re.compile(r"\b([A-Z]{2}|[A-Z][0-9]|[0-9][A-Z])\s*(\d{1,4})\b")


@dataclass(frozen=True, slots=True)
class UpcomingItem:
    key: str                 # stable: "cal:<event id>", "mail:<message id>", "order:<order id>", "goal:<id>"
    kind: str                # flight | train | stay | dining | appointment | parcel | trip | deadline | other
    title: str
    starts_at: float         # epoch seconds (UTC)
    ends_at: float | None
    place: str               # free text as found (airport, address, shop)
    refs: dict[str, str]     # what identifies it outside: {"flight": "KE123", "date": "2026-11-20"}, {"tracking": "..."}, {"booking": "..."}
    source: str              # calendar | mail | order | goal | chat
    aliases: tuple[str, ...] = ()


def normalize_title(title: str) -> str:
    """Normalize title for comparison: case and space insensitive."""
    return re.sub(r"\s+", " ", str(title or "").strip().lower())


def make_chat_item(title: str, date: str, time_str: str | None = None, place: str = "",
                   what: str = "", quote: str = "", tz: ZoneInfo | None = None) -> UpcomingItem:
    """Create an upcoming item from chat look-back."""
    zone = tz or ZoneInfo("UTC")
    if time_str and re.match(r"^\d{1,2}:\d{2}$", time_str.strip()):
        dt = datetime.strptime(f"{date} {time_str.strip()}", "%Y-%m-%d %H:%M").replace(tzinfo=zone)
    else:
        dt = datetime.strptime(date, "%Y-%m-%d").replace(hour=9, minute=0, tzinfo=zone)
    starts_at = dt.timestamp()
    slug = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", title.lower()).strip("-")[:40]
    key = f"chat:{date}:{slug}" if slug else f"chat:{date}:{int(starts_at)}"
    refs = {"date": date, "what": what, "quote": quote}
    if time_str:
        refs["time"] = time_str
    return UpcomingItem(
        key=key,
        kind="other",
        title=title.strip(),
        starts_at=starts_at,
        ends_at=None,
        place=place.strip(),
        refs=refs,
        source="chat",
    )


def extract_flight_ref(text: str) -> str | None:
    """Extract flight identifier from text (e.g. 'Flight KE 123 to Tokyo' -> 'KE123')."""
    m = FLIGHT_RE.search(text.upper())
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None


class ItemFinder:
    def __init__(self, calendar: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
                 mail: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
                 tz: ZoneInfo | None = None) -> None:
        self.calendar = calendar
        self.mail = mail
        self.tz = tz or ZoneInfo("UTC")
        self.aliases: dict[str, str] = {}
        self._seen: list[UpcomingItem] = []

    def _is_duplicate(self, a: UpcomingItem, b: UpcomingItem) -> bool:
        return normalize_title(a.title) == normalize_title(b.title) and abs(a.starts_at - b.starts_at) <= 600

    async def find(self, now: float) -> list[UpcomingItem]:
        """Find upcoming items starting between now and now + LOOK_AHEAD_DAYS."""
        max_start = now + LOOK_AHEAD_DAYS * 86400
        raw_items: list[UpcomingItem] = []

        # 1. Calendar events
        if self.calendar is not None:
            try:
                res = self.calendar()
                events = await res if hasattr(res, "__await__") else res
            except Exception:
                events = []
            for ev in events:
                item = self._item_from_event(ev, now, max_start)
                if item is not None:
                    raw_items.append(item)

        # 2. Mail items (booking confirmations)
        if self.mail is not None:
            try:
                res = self.mail()
                messages = await res if hasattr(res, "__await__") else res
            except Exception:
                messages = []
            for msg in messages:
                item = self._item_from_mail(msg, now, max_start)
                if item is not None:
                    raw_items.append(item)

        # Merge duplicates across sources: same normalised title (case/space-insensitive) and start within 10 min
        self._seen = [it for it in self._seen if it.starts_at >= now - 86400]
        items: list[UpcomingItem] = []

        for it in raw_items:
            # If this key is already an alias for another item, do not re-add
            if it.key in self.aliases:
                continue

            # Check if it matches an item already in this batch
            match_idx = -1
            for idx, existing in enumerate(items):
                if self._is_duplicate(it, existing):
                    match_idx = idx
                    break

            if match_idx >= 0:
                first = items[match_idx]
                if it.key != first.key:
                    self.aliases[it.key] = first.key
                new_aliases = (*first.aliases, it.key, *it.aliases)
                merged_refs = {**it.refs, **first.refs}
                merged = UpcomingItem(
                    key=first.key,
                    kind=first.kind,
                    title=first.title,
                    starts_at=first.starts_at,
                    ends_at=first.ends_at or it.ends_at,
                    place=first.place or it.place,
                    refs=merged_refs,
                    source=first.source,
                    aliases=new_aliases,
                )
                items[match_idx] = merged
                for s_idx, s in enumerate(self._seen):
                    if s.key == first.key:
                        self._seen[s_idx] = merged
                        break
                continue

            # Check if it matches an earlier seen primary item
            matched_seen: UpcomingItem | None = None
            for s in self._seen:
                if self._is_duplicate(it, s):
                    matched_seen = s
                    break

            if matched_seen is not None and it.key != matched_seen.key:
                self.aliases[it.key] = matched_seen.key
                continue

            items.append(it)
            if not any(s.key == it.key for s in self._seen):
                self._seen.append(it)

        return items

    def _item_from_event(self, ev: dict[str, Any], now: float, max_start: float) -> UpcomingItem | None:
        start = float(ev.get("start") or 0)
        if not (now - 300 <= start <= max_start):
            return None

        title = str(ev.get("title") or "").strip()
        where = str(ev.get("where") or "").strip()
        text = f"{title} {where}"
        ev_kind = big_events.kind(ev, self.tz)

        flight_no = extract_flight_ref(text)
        is_flight = ev_kind == "flight" or flight_no is not None or big_events.FLIGHT.search(text)

        refs: dict[str, str] = {}
        dt = datetime.fromtimestamp(start, self.tz)
        date_str = dt.strftime("%Y-%m-%d")

        if is_flight and flight_no:
            refs["flight"] = flight_no
            refs["date"] = date_str
            kind = "flight"
        elif ev_kind:
            kind = "trip" if ev_kind == "days" else ev_kind
            refs["date"] = date_str
        elif flight_no:
            refs["flight"] = flight_no
            refs["date"] = date_str
            kind = "flight"
        else:
            return None

        ev_id = str(ev.get("id") or f"{title}:{int(start)}")
        key = f"cal:{ev_id}"
        ends_at = float(ev["end"]) if ev.get("end") else None

        return UpcomingItem(
            key=key,
            kind=kind,
            title=title,
            starts_at=start,
            ends_at=ends_at,
            place=where,
            refs=refs,
            source="calendar",
        )

    def _item_from_mail(self, msg: dict[str, Any], now: float, max_start: float) -> UpcomingItem | None:
        subject = str(msg.get("subject") or msg.get("title") or "").strip()
        body = str(msg.get("body") or msg.get("detail") or "").strip()
        text = f"{subject} {body}"

        # Check for flight or booking information
        flight_no = extract_flight_ref(text)
        start = float(msg.get("starts_at") or msg.get("start") or msg.get("date") or 0)
        if start <= 0:
            # Try to use message timestamp if no starts_at provided
            start = float(msg.get("created_at") or msg.get("ts") or now)

        if not (now - 300 <= start <= max_start):
            return None

        refs: dict[str, str] = {}
        dt = datetime.fromtimestamp(start, self.tz)
        date_str = dt.strftime("%Y-%m-%d")

        if flight_no:
            refs["flight"] = flight_no
            refs["date"] = date_str
            kind = "flight"
        else:
            # Check for booking reference number
            m = re.search(r"(?:booking|confirmation|reservation|ticket)\s*(?:#|no\.?|code|ref)?\s*[:]?\s*([A-Z0-9]{5,12})\b", text, re.I)
            if m:
                refs["booking"] = m.group(1)
                refs["date"] = date_str
                kind = "stay" if big_events.STAY.search(text) else "other"
            else:
                return None

        mid = str(msg.get("id") or msg.get("message_id") or f"{subject}:{int(start)}")
        return UpcomingItem(
            key=f"mail:{mid}",
            kind=kind,
            title=subject or f"Booking {refs.get('booking', '')}",
            starts_at=start,
            ends_at=float(msg["ends_at"]) if msg.get("ends_at") else None,
            place=str(msg.get("place") or msg.get("where") or ""),
            refs=refs,
            source="mail",
        )
