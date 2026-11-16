"""When the app is usually not in use at night, learned from the user's own pattern.

Every foreground request marks the current 15-minute slot of the local day as "used" (one 96-character string of 0/1 per day,
kept 28 days in the store's meta table, written at most once a minute). Each evening the nightly jobs (journal, look-back) pick
the quietest slot of the coming night: the start of the 90-minute stretch, inside NIGHT_START..NIGHT_END, that was used on the
fewest of the recorded days (ties: the slot nearest DEFAULT). With fewer than MIN_DAYS days of data the default time is used.
The jobs still wait until the app has been left alone for a while (reflection.py) before they start.
Weekday and weekend nights are judged apart when the day type is known (day_types.py, by the date of the morning after): a night before
a weekend or holiday morning is chosen from such nights only, when at least MIN_SAME_NIGHTS of them are recorded; else from all nights.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SLOT_MIN = 15
SLOTS = 24 * 60 // SLOT_MIN           # 96 a day
KEEP_DAYS = 28
MIN_DAYS = 7
WINDOW_SLOTS = 6                      # 90 minutes judged together
NIGHT_START = 22 * 4                  # 22:00 (in slots)
NIGHT_END = 8 * 4                     # 08:00 next morning (the stretch must start before 06:30 so it ends by 08:00)
DEFAULT = (3, 50)
MIN_SAME_NIGHTS = 3                   # nights of the same kind (before a weekday / a weekend morning) needed to judge them apart
PREFIX = "usage_slots:"


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


class QuietHours:
    def __init__(self, store: Any, tz: Callable[[], str | None], clock: Callable[[], float] = time.time,
                 kind_of: Callable[[str], str] | None = None):
        self.store, self.tz, self.clock = store, tz, clock
        self.kind_of = kind_of   # "YYYY-MM-DD" -> weekday | weekend (day_types.py); None: every night alike
        self._day, self._bits, self._dirty_since = "", ["0"] * SLOTS, 0.0

    # ------------------------------------------------------------------ recording
    def record(self, at: float | None = None) -> None:
        at = self.clock() if at is None else at
        local = datetime.fromtimestamp(at, _zone(self.tz()))
        day = local.strftime("%Y-%m-%d")
        if day != self._day:
            self._flush()
            self._day = day
            self._bits = list(self.store.meta(PREFIX + day) or "0" * SLOTS)
        slot = (local.hour * 60 + local.minute) // SLOT_MIN
        if self._bits[slot] == "1":
            return
        self._bits[slot] = "1"
        if not self._dirty_since:
            self._dirty_since = at
        if at - self._dirty_since >= 60 or slot != (local.hour * 60 + local.minute - 1) // SLOT_MIN:
            self._flush()

    def _flush(self) -> None:
        if self._day and self._dirty_since:
            self.store.set_meta(PREFIX + self._day, "".join(self._bits))
            self._dirty_since = 0.0

    def days(self, now: float | None = None) -> dict[str, str]:
        """Recorded days within KEEP_DAYS, oldest dropped from the store as a side effect."""
        self._flush()
        now = self.clock() if now is None else now
        today = datetime.fromtimestamp(now, _zone(self.tz())).date()
        out: dict[str, str] = {}
        for i in range(1, KEEP_DAYS + 3):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            v = self.store.meta(PREFIX + d)
            if not v:
                continue
            if i > KEEP_DAYS:
                self.store.set_meta(PREFIX + d, "")
                continue
            out[d] = v
        return out

    # ------------------------------------------------------------------ choosing
    def quiet_slot(self, now: float | None = None) -> tuple[int, int, str]:
        """(hour, minute, source) of tonight's run: "learned" or "default"."""
        days = self.days(now)
        if len(days) < MIN_DAYS:
            return DEFAULT[0], DEFAULT[1], "default"
        dates = sorted(days)
        # a night = the evening of day D (slots 88..95) + the morning of day D+1 (slots 0..31)
        nights, mornings = [], []
        for d in dates:
            nxt = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            if nxt in days:
                nights.append(days[d][NIGHT_START:] + days[nxt][:NIGHT_END])
                mornings.append(nxt)
        if len(nights) < MIN_DAYS - 1:
            return DEFAULT[0], DEFAULT[1], "default"
        if self.kind_of is not None:
            local = datetime.fromtimestamp(self.clock() if now is None else now, _zone(self.tz()))
            evening = local - timedelta(days=1) if local.hour < 8 else local
            want = self.kind_of((evening + timedelta(days=1)).strftime("%Y-%m-%d"))
            same = [n for n, m in zip(nights, mornings) if self.kind_of(m) == want]
            if len(same) >= MIN_SAME_NIGHTS:
                nights = same
        length = len(nights[0])
        default_pos = (24 * 4 - NIGHT_START) + (DEFAULT[0] * 60 + DEFAULT[1]) // SLOT_MIN
        best = None
        for start in range(0, length - WINDOW_SLOTS + 1):
            used = sum(1 for n in nights if "1" in n[start:start + WINDOW_SLOTS])
            key = (used, abs(start - default_pos))
            if best is None or key < best[0]:
                best = (key, start)
        start = best[1] if best else default_pos
        minutes = (NIGHT_START * SLOT_MIN + start * SLOT_MIN) % (24 * 60)
        return minutes // 60, minutes % 60, "learned"


class NightlyRunner:
    """Runs the nightly jobs once a night at the quiet slot. Checked every few minutes, so a restart during the night still runs them."""

    def __init__(self, store: Any, quiet: QuietHours, jobs: list[Callable[[], Awaitable[Any]]],
                 clock: Callable[[], float] = time.time):
        self.store, self.quiet, self.jobs, self.clock = store, quiet, jobs, clock

    def night_key(self, local: datetime) -> str | None:
        """The evening date this moment's night belongs to, or None outside 22:00..08:00."""
        if local.hour >= 22:
            return local.strftime("%Y-%m-%d")
        if local.hour < 8:
            return (local - timedelta(days=1)).strftime("%Y-%m-%d")
        return None

    def plan(self) -> dict[str, Any]:
        now = self.clock()
        local = datetime.fromtimestamp(now, _zone(self.quiet.tz()))
        h, m, source = self.quiet.quiet_slot(now)
        return {"hour": h, "minute": m, "source": source, "last_night": self.store.meta("nightly:last") or None}

    async def tick(self) -> bool:
        now = self.clock()
        local = datetime.fromtimestamp(now, _zone(self.quiet.tz()))
        key = self.night_key(local)
        if key is None or self.store.meta("nightly:last") == key:
            return False
        planned = self.store.meta("nightly:plan")
        if not planned or not planned.startswith(key + " "):
            h, m, source = self.quiet.quiet_slot(now)
            planned = f"{key} {h:02d}:{m:02d} {source}"
            self.store.set_meta("nightly:plan", planned)
        hh, mm = (int(x) for x in planned.split()[1].split(":"))
        evening = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=local.tzinfo)
        at = evening + timedelta(days=0 if hh >= 22 else 1, hours=hh, minutes=mm)
        if local < at:
            return False
        self.store.set_meta("nightly:last", key)
        for job in self.jobs:
            try:
                await job()
            except Exception:  # noqa: BLE001 - one job failing must not stop the next
                pass
        return True
