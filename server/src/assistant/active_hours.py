"""The owner's real active hours, learned from their own activity, so contact hours (the proactive settings' wake_start..wake_end, which
follow-ups and the checklist use too) can follow a night owl or an early bird instead of a fixed 07:00–22:00.

What counts as activity, in the profile's time zone, over the last KEEP_DAYS days:
- the owner's own chat messages (hidden rows such as job handoffs are not theirs);
- app use: every foreground request marks its 15-minute slot (quiet_hours.py keeps one 96-character string of 0/1 per day), which
  covers opening the app and answering a card.

How the window is found (learn_window): each day is a set of active 15-minute slots. Days with any activity are counted; with fewer than
MIN_DAYS nothing is learned. The clock is cut in the middle of the quietest CUT_SLOTS-long stretch (so a day that runs past midnight,
e.g. 10:00–02:00, is one piece); the window runs from the EARLY percentile of each day's first active slot to the LATE percentile of
each day's last one, in that cut order: a few
odd days do not move it. The start is rounded down and the end up to ROUND_MIN. A window shorter than MIN_SPAN or longer than MAX_SPAN
is not trusted (no suggestion).

A learned window that differs from the current setting by THRESHOLD_MIN or more at either end becomes a suggestion (proactive.py keeps
it, the apps show it on the Records page); "keep current" holds the next one back RESUGGEST_DAYS days.

Two windows (learn_windows): weekdays and weekends with public holidays (day_types.py) are learned apart, each from its own days only:
weekdays need MIN_DAYS active days, weekends MIN_WEEKEND_DAYS. Until the weekend window is learned, weekends use the weekday window.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .day_types import WEEKDAY, WEEKEND, day_type

SLOT_MIN = 15
SLOTS = 24 * 60 // SLOT_MIN
KEEP_DAYS = 28
MIN_DAYS = 7
MIN_WEEKEND_DAYS = 4           # weekends and holidays are fewer: four of them are enough for their own window
CUT_SLOTS = 16                 # 4 hours: the quietest stretch of this length is where the day is cut
EARLY, LATE = 0.25, 0.75       # of each day's first and last active slot: a few odd days do not move the window
ROUND_MIN = 30
MIN_SPAN = 6 * 60
MAX_SPAN = 23 * 60
THRESHOLD_MIN = 60
RESUGGEST_DAYS = 14
LIVE_SECONDS = 30 * 60         # a chat message this recent: the owner is awake now, whatever the window says
USAGE_PREFIX = "usage_slots:"  # quiet_hours.PREFIX


def hhmm(minutes: int) -> str:
    m = minutes % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def minutes_of(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def circular_gap(a: int, b: int) -> int:
    """Minutes between two times of day, the short way round the clock."""
    d = abs(a - b) % (24 * 60)
    return min(d, 24 * 60 - d)


def activity_days(store: Any, tz: ZoneInfo, now: float, owner_marks: tuple[str, ...] = ()) -> dict[str, set[int]]:
    """{local date: active 15-minute slots} over the last KEEP_DAYS days (today included): app use and the owner's chat messages."""
    today = datetime.fromtimestamp(now, tz).date()
    days: dict[str, set[int]] = {}
    for i in range(KEEP_DAYS):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        bits = store.meta(USAGE_PREFIX + d) or ""
        got = {s for s, b in enumerate(bits[:SLOTS]) if b == "1"}
        if got:
            days[d] = got
    since = now - KEEP_DAYS * 86400
    # nothing after "now" (a clock set back, or a test clock): a later message would count as a day that has not happened yet
    for r in store._all("SELECT content, created_at FROM messages WHERE role = 'user' AND created_at >= ? AND created_at <= ?", (since, now)):
        if owner_marks and str(r["content"]).startswith(owner_marks):
            continue
        local = datetime.fromtimestamp(float(r["created_at"]), tz)
        days.setdefault(local.strftime("%Y-%m-%d"), set()).add((local.hour * 60 + local.minute) // SLOT_MIN)
    return days


def _percentile(sorted_vals: list[int], q: float) -> int:
    """Nearest rank."""
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def learn_window(days: dict[str, set[int]], *, min_days: int = MIN_DAYS) -> dict[str, Any] | None:
    """{"wake_start": "HH:MM", "wake_end": "HH:MM", "active_days": n} from the active slots of each day, or None when there is too
    little data or the result is not a usable window (end may be before start: a window past midnight)."""
    active = {d: s for d, s in days.items() if s}
    if len(active) < min_days:
        return None
    counts = [0] * SLOTS
    for slots in active.values():
        for s in slots:
            counts[s % SLOTS] += 1
    # the quietest stretch (ties: the one nearest 04:00), cut in its middle
    best = None
    for start in range(SLOTS):
        load = sum(counts[(start + k) % SLOTS] for k in range(CUT_SLOTS))
        mid = (start + CUT_SLOTS // 2) % SLOTS
        key = (load, circular_gap(mid * SLOT_MIN, 4 * 60))
        if best is None or key < best[0]:
            best = (key, mid)
    cut = best[1] if best else 0
    firsts = sorted(min((s - cut) % SLOTS for s in day) for day in active.values())
    lasts = sorted(max((s - cut) % SLOTS for s in day) for day in active.values())
    lo, hi = _percentile(firsts, EARLY), _percentile(lasts, LATE)
    start_min = (cut + lo) * SLOT_MIN                 # minutes from the start of the day the cut is in (may pass 24:00)
    end_min = (cut + hi + 1) * SLOT_MIN
    start_min -= start_min % ROUND_MIN
    end_min = (end_min + ROUND_MIN - 1) // ROUND_MIN * ROUND_MIN
    span = end_min - start_min
    if not MIN_SPAN <= span <= MAX_SPAN:
        return None
    return {"wake_start": hhmm(start_min), "wake_end": hhmm(end_min), "active_days": len(active)}


def learn_windows(days: dict[str, set[int]], country: str) -> dict[str, dict[str, Any] | None]:
    """{"weekday": window|None, "weekend": window|None}: each from the days of its own type (public holidays are weekend days)."""
    split: dict[str, dict[str, set[int]]] = {WEEKDAY: {}, WEEKEND: {}}
    for d, s in days.items():
        try:
            kind = day_type(date.fromisoformat(d), country)
        except ValueError:
            continue
        split[kind][d] = s
    return {WEEKDAY: learn_window(split[WEEKDAY], min_days=MIN_DAYS), WEEKEND: learn_window(split[WEEKEND], min_days=MIN_WEEKEND_DAYS)}


def differs(learned: dict[str, Any], start: str, end: str, threshold: int = THRESHOLD_MIN) -> bool:
    """Whether a learned window is meaningfully different from the current one (THRESHOLD_MIN or more at either end)."""
    return (circular_gap(minutes_of(learned["wake_start"]), minutes_of(start)) >= threshold
            or circular_gap(minutes_of(learned["wake_end"]), minutes_of(end)) >= threshold)


__all__ = ["LIVE_SECONDS", "MIN_DAYS", "MIN_WEEKEND_DAYS", "RESUGGEST_DAYS", "THRESHOLD_MIN", "activity_days", "circular_gap", "differs",
           "learn_window", "learn_windows"]
