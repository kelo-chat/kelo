"""The day's send times of "Kelo tells you first" (owner decision 2026-11-19, card p-proactive-slots).

A day's contact window (proactive.py: its day type's window, the hours in effect) is cut into equal parts, one per send a day
(daily_total), plus one for the morning briefing while it is on: window length / (daily_total + 1). The parts start at the window
start; each time is a whole minute, rounded down, on the wall clock of the profile's time zone. The briefing (never before the window
start, briefing.py) takes the part nearest to its due time that day (a tie: the earlier part); the other parts are the proactive send
times, when the hourly look (situation.py) looks at everything and messages may go out. A window past midnight keeps its times on the
evening's day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .proactive import _minutes

DAY_MINUTES = 24 * 60


@dataclass(frozen=True)
class DayPlan:
    day: date                        # the day the window starts on
    start: float                     # the window's start (unix)
    end: float                       # the window's end (unix)
    times: tuple[float, ...]         # the proactive send times, in order
    briefing: float | None           # the time of the briefing's part (None: the briefing is off)


def plan(day: date, start: str, end: str, total: int, briefing_at: float | None, tz: ZoneInfo) -> DayPlan:
    """The send times of `day`'s window start..end ("HH:MM"; the same start and end = the whole day) for `total` sends a day;
    `briefing_at`: the briefing's due time that day, or None while it is off."""
    a, b = _minutes(start), _minutes(end)
    length = (b - a) % DAY_MINUTES or DAY_MINUTES
    w0 = datetime(day.year, day.month, day.day, a // 60, a % 60, tzinfo=tz)
    n = max(0, int(total)) + (1 if briefing_at is not None else 0)
    grid = [(w0 + timedelta(minutes=(k * length) // n)).timestamp() for k in range(n)]
    taken = min(range(n), key=lambda k: (abs(grid[k] - briefing_at), k)) if briefing_at is not None and grid else None
    return DayPlan(day, w0.timestamp(), (w0 + timedelta(minutes=length)).timestamp(),
                   tuple(g for k, g in enumerate(grid) if k != taken), grid[taken] if taken is not None else None)
