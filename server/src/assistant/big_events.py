"""Big events ahead: trips, flights, stays and events that run over several days, found in calendar events by code (no model).

The proactive pass (proactive.py) reads the chosen calendars AHEAD_DAYS ahead at its usual pace and mentions each big event once at
each lead time the owner set (proactive setting "ahead_days", default 7 days before and the day before); only then does the model write
the message (what it is, what to prepare). The morning briefing (briefing.py) adds a short "coming up" line for big events this week.

kind(ev, tz) -> "flight" | "stay" | "trip" | "days" (several days) | None, from the event's title, place and times:
- flight: a flight word (flight, 항공, 탑승, フライト, 航班, vuelo ...) or an airport pair such as "ICN-PVG" / "ICN → NRT";
- stay: a hotel / lodging word (hotel, 호텔, 숙소, 체크인, ホテル, 酒店, Airbnb ...);
- trip: a travel word (trip, 여행, 출장, 휴가, 旅行, 出張, viaje, Reise ...);
- days: anything else that lasts 20 hours or more and covers two local dates or more (an all-day event over several days, a timed
  event from Friday evening to Sunday). Longer than MAX_SPAN_DAYS (a semester, a "working from home" block) is not an event to prepare for.
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

_LATIN = re.compile(r"[A-Za-z]")


def _words(*items: str) -> re.Pattern[str]:
    """Words matched case-insensitively: Latin ones as whole words, the others (Korean, Japanese, Chinese, Thai ...) anywhere."""
    parts = [(r"\b" + re.escape(w) + r"\b") if _LATIN.search(w) else re.escape(w) for w in items]
    return re.compile("|".join(parts), re.IGNORECASE)


FLIGHT = _words("flight", "flights", "boarding", "✈", "✈️", "항공", "비행기", "탑승", "출국", "입국", "フライト", "搭乗", "航空便", "航班", "登机",
                "vuelo", "vol", "flug", "volo", "voo", "vlucht", "рейс", "uçuş", "penerbangan", "chuyến bay", "เที่ยวบิน",
                "उड़ान", "رحلة طيران")
AIRPORT_PAIR = re.compile(r"\b[A-Z]{3}\s*(?:-|–|—|→|->|>|/|to)\s*[A-Z]{3}\b")
STAY = _words("hotel", "hostel", "airbnb", "resort", "ryokan", "lodging", "accommodation", "호텔", "숙소", "체크인",
              "리조트", "펜션", "게스트하우스", "민박", "ホテル", "旅館", "宿泊", "チェックイン", "酒店", "宾馆", "民宿", "入住", "hôtel",
              "hotel", "alojamiento", "unterkunft", "albergo", "hospedagem", "гостиница", "отель", "otel", "penginapan", "khách sạn",
              "โรงแรม", "होटल", "فندق")
TRIP = _words("trip", "travel", "vacation", "journey", "getaway", "여행", "출장", "휴가", "투어", "旅行", "出張", "休暇", "旅游", "出差",
              "度假", "viaje", "vacaciones", "voyage", "vacances", "reise", "urlaub", "viaggio", "vacanza", "viagem", "férias", "reis",
              "vakantie", "podróż", "urlop", "поездка", "путешествие", "отпуск", "командировка", "seyahat", "tatil", "perjalanan",
              "liburan", "du lịch", "chuyến đi", "เที่ยว", "การเดินทาง", "यात्रा", "छुट्टी", "رحلة", "سفر", "عطلة")


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


def kind(ev: dict[str, Any], tz: ZoneInfo) -> str | None:
    text = f"{ev.get('title') or ''} {ev.get('where') or ''}"
    a, b = first_day(ev, tz), last_day(ev, tz)
    span = (b - a).days + 1
    if span > MAX_SPAN_DAYS:
        return None
    if FLIGHT.search(text) or AIRPORT_PAIR.search(str(ev.get("title") or "")):
        return "flight"
    if STAY.search(text):
        return "stay"
    if TRIP.search(text):
        return "trip"
    start, end = float(ev.get("start") or 0), float(ev.get("end") or 0)
    long_enough = (end - start) >= MIN_HOURS * 3600 or (bool(ev.get("all_day")) and span >= 2)
    return "days" if span >= 2 and long_enough else None


def ahead(events: list[dict[str, Any]], now: float, tz: ZoneInfo, days: int = AHEAD_DAYS) -> list[dict[str, Any]]:
    """Big events starting after today and within `days` local days, one per title and first day, by start:
    [{**event, "kind", "first": date, "last": date, "days_left": int}]."""
    today = datetime.fromtimestamp(now, tz).date()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, date]] = set()
    for ev in sorted(events, key=lambda e: float(e.get("start") or 0)):
        k = kind(ev, tz)
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
