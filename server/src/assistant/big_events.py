"""Big events ahead: trips, flights, stays and events that run over several days, found in calendar events by code (no model).

The proactive pass (proactive.py) reads the chosen calendars AHEAD_DAYS ahead at its usual pace and mentions each big event once at
each lead time the owner set (proactive setting "ahead_days", default 7 days before and the day before); only then does the model write
the message (what it is, what to prepare). The morning briefing (briefing.py) adds a short "coming up" line for big events this week.

kind(ev, tz, model) -> "flight" | "days" | "stay" | "trip" | None. Structure first (no word of any language, docs/no-word-lists.md):
- flight: the title holds an airport pair such as "ICN-PVG" / "ICN → NRT" (IATA codes);
- days: the event lasts 20 hours or more and covers two local dates or more (an all-day event over several days, a timed event from
  Friday evening to Sunday): trips and hotel stays are these. Longer than MAX_SPAN_DAYS (a semester, a "working from home" block) is
  not an event to prepare for. For the rest, `model` holds the main model's batched reading (event_kinds.EventKinds) of the title and place:
a single-day "stay" or "trip" counts. No model, or it failed: structure only.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

AHEAD_DAYS = 14          # how far ahead the proactive pass looks for big events
WEEK_DAYS = 7            # the briefing's "coming up": big events in the next this many days (today's are in its event list)
MAX_SPAN_DAYS = 31       # longer than this is a period, not an event to prepare for
MIN_HOURS = 20           # a several-day event lasts at least this long

# An airport pair is a code pattern (IATA: three capitals, a separator, three capitals), not a word of a language.
AIRPORT_PAIR = re.compile(r"\b[A-Z]{3}\s*(?:-|–|—|→|->|>|/)\s*[A-Z]{3}\b")
def first_day(ev: dict[str, Any], tz: ZoneInfo) -> date:
    if ev.get("all_day") and ev.get("day"):
        try:
            return date.fromisoformat(str(ev["day"])[:10])
        except ValueError:
            pass
    return datetime.fromtimestamp(float(ev.get("start") or 0), tz).date()


def last_day(ev: dict[str, Any], tz: ZoneInfo) -> date:
    """The local date the event ends on (an all-day event's end is the start of the day after: that day is not part of it)."""
    a = first_day(ev, tz)
    end = float(ev.get("end") or 0)
    start = float(ev.get("start") or 0)
    if end <= start:
        return a
    b = datetime.fromtimestamp(end - 1, tz).date()
    return max(a, b)


def event_key(ev: dict[str, Any]) -> str:
    """One calendar event as the classifier caches it: its id (else title and start) and its updated time, so an edited event is judged again."""
    ident = str(ev.get("id") or f"{ev.get('title') or ''}:{int(float(ev.get('start') or 0))}")
    return f"{ident}|{ev.get('updated') or ''}"


MODEL_KINDS = ("flight", "stay", "trip")   # what the model's classification (event_kinds.py) can add to the structural kinds


def unclassified(events: list[dict[str, Any]], now: float, tz: ZoneInfo, days: int = AHEAD_DAYS) -> list[dict[str, Any]]:
    """The events starting after today within `days` days that structure alone does not make big: what the model is asked about."""
    today = datetime.fromtimestamp(now, tz).date()
    out = []
    for ev in events:
        left = (first_day(ev, tz) - today).days
        if 1 <= left <= days and kind(ev, tz) is None:
            out.append(ev)
    return out


def kind(ev: dict[str, Any], tz: ZoneInfo, model: dict[str, str] | None = None) -> str | None:
    a, b = first_day(ev, tz), last_day(ev, tz)
    span = (b - a).days + 1
    if span > MAX_SPAN_DAYS:
        return None
    if AIRPORT_PAIR.search(str(ev.get("title") or "")):
        return "flight"
    start, end = float(ev.get("start") or 0), float(ev.get("end") or 0)
    long_enough = (end - start) >= MIN_HOURS * 3600 or (bool(ev.get("all_day")) and span >= 2)
    if span >= 2 and long_enough:
        return "days"
    named = (model or {}).get(event_key(ev))   # the main model's reading of the title and place (event_kinds.py), when it was asked
    return named if named in MODEL_KINDS else None


def ahead(events: list[dict[str, Any]], now: float, tz: ZoneInfo, days: int = AHEAD_DAYS,
          model: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Big events starting after today and within `days` local days, one per title and first day, by start:
    [{**event, "kind", "first": date, "last": date, "days_left": int}]."""
    today = datetime.fromtimestamp(now, tz).date()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, date]] = set()
    for ev in sorted(events, key=lambda e: float(e.get("start") or 0)):
        k = kind(ev, tz, model)
        if k is None:
            continue
        a = first_day(ev, tz)
        left = (a - today).days
        key = (str(ev.get("title") or "").strip().casefold(), a)
        if not 1 <= left <= days or key in seen:
            continue
        seen.add(key)
        out.append({**ev, "kind": k, "first": a, "last": last_day(ev, tz), "days_left": left})
    return out


def lead_for(days_left: int, leads: list[int]) -> int | None:
    """The lead time an event `days_left` days away is at: the smallest lead it is within (7 for 5 days away with leads 7 and 1; 1 for
    tomorrow). None when it is farther than every lead."""
    within = [n for n in leads if days_left <= n]
    return min(within) if within else None


def span_text(ev: dict[str, Any]) -> str:
    a, b = ev["first"], ev["last"]
    return a.isoformat() if a == b else f"{a.isoformat()} to {b.isoformat()}"


def week_end(now: float, tz: ZoneInfo) -> float:
    d = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=WEEK_DAYS + 1)
    return d.timestamp()
