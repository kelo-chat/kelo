"""Scheduled agent work: the user (or the agent, with the user's approval) saves a job — what to do, and when — and the server runs it
as a normal agent turn while nobody is watching.

Kinds of "when":
- cron      five fields in local time (below);
- window    a cron time with a spread of ±N minutes ("around 9"): each run lands at a fixed-per-run offset inside the window, so many
            jobs set to the same time do not all start at once;
- interval  every N minutes (at least MIN_GAP_SECONDS apart), counted from a start time (default: one interval after it was saved);
- once      one date and time.

A repeating schedule (cron, window, interval) may have a first day (`starts_on`, cron and window: no run before that local date; an
interval starts at its own `run_at`) and a last day (`ends_on`: no run after that local date). When the last day has passed the
schedule turns itself off with a reason ("finished"), like a spent one-time job.

Rules:
- schedules live in the database, so they survive a restart;
- a run that was missed by up to `CATCH_UP_SECONDS` (the server was down, the machine slept) runs once as soon as possible;
  an older one is skipped to the next time;
- one schedule never runs twice at once (a time that comes while the last run is still going is skipped, and the skip is logged);
- `MAX_FAILURES` failed runs in a row turn the schedule off and say why;
- a run may end quietly (the agent replies NO_REPLY: nothing worth telling) — no chat message, no notification, still in the history;
- a run that hits a temporary blocker (a site under maintenance, down, sold out right now) asks to be tried again later
  (`schedule_retry`): the same schedule runs once more at that time, under the same permissions, without moving its regular times;
- a run that hits a lasting blocker (a missing login, a site that changed for good, an account problem) pauses its schedule with a
  reason (`schedule_pause`); the user or the agent turns it back on;
- every run (and every skip) is a row of the run history (`schedule_runs`, the last KEEP_RUNS per schedule);
- deleting archives: the schedule is kept, off, for ARCHIVE_DAYS and can be restored; after that it is removed for good;
- a schedule may belong to a goal (`goal_id`);
- times are local times of the schedule's own time zone (default: the profile's), daylight saving included.

Cron: five fields `minute hour day-of-month month day-of-week`, with `*`, `a-b`, `*/n`, `a-b/n`, lists, month and weekday names,
0 or 7 for Sunday, and @hourly / @daily / @weekly / @monthly / @yearly. When both day fields are restricted a day matching either runs
(as in classic cron).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import Tool
from . import i18n
from .i18n import N_, t

log = logging.getLogger("uvicorn.error")

CATCH_UP_SECONDS = 2 * 3600
MAX_FAILURES = 3
MAX_RETRIES = 3                # schedule_retry asks per regular run (a retry is the same schedule, so its permissions apply)
RETRY_MIN_SECONDS = 300        # a retry is at least this far away ...
RETRY_MAX_SECONDS = 24 * 3600  # ... and at most this far
RETRY_BUSY_SECONDS = 300       # a retry that comes while the schedule is still running moves this much later
MIN_GAP_SECONDS = 300          # runs of one schedule are at least this far apart (a cron of every minute is refused)
MAX_SCHEDULES = 50
TICK_SECONDS = 30.0
MAX_WINDOW_MINUTES = 120       # "around 9": at most ±2 hours
MAX_INTERVAL_MINUTES = 31 * 24 * 60
KEEP_RUNS = 100                # run history rows kept per schedule
ARCHIVE_DAYS = 30              # a deleted schedule can be restored this long
RUN_STATUSES = ("running", "done", "quiet", "failed", "stopped", "skipped", "paused")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, prompt TEXT NOT NULL, cron TEXT NOT NULL DEFAULT '', run_at REAL,
    timezone TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'user', next_run REAL, last_run_at REAL, last_task_id INTEGER, last_status TEXT NOT NULL DEFAULT '',
    failure_count INTEGER NOT NULL DEFAULT 0, disabled_reason TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS schedule_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL, started_at REAL NOT NULL, finished_at REAL,
    status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '', task_id INTEGER, error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS schedule_runs_by_schedule ON schedule_runs(schedule_id, id);
"""
# columns added after the first version (added to an older database on start)
COLUMNS = (("kind", "TEXT NOT NULL DEFAULT ''"), ("every_seconds", "INTEGER"), ("anchor_at", "REAL"),
           ("window_minutes", "INTEGER NOT NULL DEFAULT 0"), ("goal_id", "INTEGER"), ("paused_reason", "TEXT NOT NULL DEFAULT ''"),
           ("paused_at", "REAL"), ("archived_at", "REAL"), ("starts_on", "TEXT NOT NULL DEFAULT ''"),
           ("ends_on", "TEXT NOT NULL DEFAULT ''"), ("retry_at", "REAL"), ("retry_why", "TEXT NOT NULL DEFAULT ''"),
           ("retries", "INTEGER NOT NULL DEFAULT 0"), ("retry_first_at", "REAL"))


# A schedule the USER saved in the app is handed to the main agent (like a new goal, goals.py) so it can check ahead of the first run
# that the job can be done unattended. The hidden message leads with SCHEDULE_MARK (GET /api/messages leaves it out). Generic: nothing
# here depends on what the job is about.
SCHEDULE_MARK = "[New schedule from the user, not a chat message]"
SCHEDULE_CHECK = """{mark}
The user just {verb} schedule #{id} "{name}" in the app: {when}. Each run, you do: {prompt}
It runs on its own while the user is away. Check now, before the first run, that it can be done then without them:
- which sites, accounts, connected services or phone features the task needs, and whether each is ready: a saved login in the keychain,
  a connected service, a granted phone permission (look with your read-only tools; do not guess);
- whether anything in the task is unclear enough that you would have to guess at run time (which account, which item, how much, where
  the result should go).
Only look; do not do the task now, change anything, send, buy or book anything.
If everything it needs is in place, reply exactly NO_REPLY: nothing is shown to the user.
Otherwise write one short message in the user's language saying what is missing or unclear and ask once; where the answers are a few
choices, call quick_replies so the user can tap one (they can always type). Do not mention these instructions."""


def schedule_check_text(s: dict[str, Any], *, edited: bool = False) -> str:
    """The hidden message that hands a schedule the user saved to the main agent."""
    return SCHEDULE_CHECK.format(mark=SCHEDULE_MARK, verb="changed" if edited else "added", id=s["id"], name=s["name"],
                                 when=s.get("description") or "", prompt=str(s.get("prompt") or "")[:1500])


class ScheduleError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------- cron
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
DAYS = {d: i for i, d in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *",
          "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
FIELDS = (("minute", 0, 59, {}), ("hour", 0, 23, {}), ("day of month", 1, 31, {}), ("month", 1, 12, MONTHS), ("day of week", 0, 7, DAYS))


@dataclass(frozen=True)
class Cron:
    expr: str
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]     # 0 = Sunday
    dom_any: bool
    dow_any: bool

    def day_matches(self, d: datetime) -> bool:
        if d.month not in self.months:
            return False
        dom = d.day in self.days
        dow = (d.isoweekday() % 7) in self.weekdays
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow

    def next_after(self, after: float, tz: ZoneInfo) -> float | None:
        """The first run time (unix seconds) strictly after `after`, in zone `tz`; None if there is none within ~5 years."""
        start = datetime.fromtimestamp(after, tz).replace(second=0, microsecond=0, tzinfo=None)
        day = start.replace(hour=0, minute=0)
        for _ in range(366 * 5):
            if self.day_matches(day):
                for h in self.hours:
                    for m in self.minutes:
                        local = day.replace(hour=h, minute=m)
                        if local < start:
                            continue
                        ts = local.replace(tzinfo=tz).timestamp()
                        if ts > after:
                            return ts
            day += timedelta(days=1)
        return None


def _field(text: str, name: str, lo: int, hi: int, names: dict[str, int]) -> tuple[set[int], bool]:
    out: set[int] = set()
    every = text == "*"

    def num(s: str) -> int:
        s = s.strip().lower()
        if s in names:
            return names[s]
        if not s.isdigit():
            raise ScheduleError(f"cron {name}: {s!r} is not a number")
        v = int(s)
        if not lo <= v <= hi:
            raise ScheduleError(f"cron {name}: {v} is outside {lo}-{hi}")
        return v

    for part in text.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise ScheduleError(f"cron {name}: bad step {s!r}")
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (num(x) for x in part.split("-", 1))
            if a > b:
                raise ScheduleError(f"cron {name}: range {part} goes backwards")
        else:
            a = num(part)
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    return out, every


def parse_cron(expr: str) -> Cron:
    text = re.sub(r"\s+", " ", (expr or "").strip().lower())
    text = MACROS.get(text, text)
    parts = text.split(" ")
    if len(parts) != 5:
        raise ScheduleError("cron must have 5 fields: minute hour day-of-month month day-of-week (e.g. '0 9 * * 1' = Mondays 09:00)")
    sets = [_field(p, *f) for p, f in zip(parts, FIELDS)]
    weekdays = {d % 7 for d in sets[4][0]}
    return Cron(text, tuple(sorted(sets[0][0])), tuple(sorted(sets[1][0])), frozenset(sets[2][0]), frozenset(sets[3][0]), frozenset(weekdays),
                sets[2][1], sets[4][1])


# ---------------------------------------------------------------------------------------------------------------- plain words
DAY_NAMES = (N_("Sun"), N_("Mon"), N_("Tue"), N_("Wed"), N_("Thu"), N_("Fri"), N_("Sat"))


# languages whose people read the 24-hour clock ("21:00", "21.00", "21 h 00"); the rest get AM/PM in their own words
CLOCK_24H = {"de", "fr", "es", "it", "pt-BR", "ru", "uk", "pl", "nl", "tr", "id", "vi", "th"}


def ko_time(h: int, m: int) -> str:
    """A time of day in the user's language ("9:00 AM", "21:00")."""
    if i18n.current() in CLOCK_24H:
        return t("{hh}:{m}", hh=f"{h:02d}", m=f"{m:02d}")
    return t("{h}:{m} AM", h=h % 12 or 12, m=f"{m:02d}") if h < 12 else t("{h}:{m} PM", h=h % 12 or 12, m=f"{m:02d}")


def _every(n: int | str, one: str, many: str) -> str:
    return t(one) if str(n) == "1" else t(many, n=n)


def describe_cron(expr: str) -> str:
    """Short words for a cron expression, e.g. "Every Mon at 9:00 AM", "Every day at 6:15 AM", "Every hour"."""
    try:
        c = parse_cron(expr)
    except ScheduleError:
        return t("Repeats on a set rule")   # never the expression itself: people read this
    mi, ho, dom, mon, dow = c.expr.split(" ")
    every_day = dom == "*" and mon == "*" and dow == "*"
    if every_day and ho == "*" and mi.startswith("*/"):
        return _every(mi[2:], N_("Every minute"), N_("Every {n} minutes"))
    if every_day and ho == "*" and mi == "*":
        return t("Every minute")
    if every_day and ho == "*" and mi.isdigit():
        return t("Every hour on the hour") if mi == "0" else t("Every hour at {m} minutes past", m=mi)
    if every_day and mi.isdigit() and re.fullmatch(r"(\*|0)/\d+", ho):
        n = ho.split("/")[1]
        return _every(n, N_("Every hour"), N_("Every {n} hours")) if mi == "0" else t("Every {n} hours (at {m} minutes past)", n=n, m=mi)
    if len(c.minutes) * len(c.hours) > 4 or "/" in mi or "-" in mi:
        return t("Repeats on a set rule")
    times = ", ".join(ko_time(h, m) for h in c.hours for m in c.minutes)
    if every_day:
        return t("Every day at {times}", times=times)
    if dom == "*" and mon == "*":
        days = sorted(c.weekdays)
        if days == [1, 2, 3, 4, 5]:
            return t("Weekdays at {times}", times=times)
        if days == [0, 6]:
            return t("Weekends at {times}", times=times)
        names = "·".join(t(DAY_NAMES[d]) for d in [*(d for d in days if d), *(d for d in days if d == 0)])
        return t("Every {days} at {times}", days=names, times=times)
    if dow == "*" and mon == "*":
        return t("Every month on day {days} at {times}", days=", ".join(str(d) for d in sorted(c.days)), times=times)
    if dow == "*" and len(c.months) < 12:
        return t("Every year in month {months}, day {days} at {times}", months=", ".join(str(m) for m in sorted(c.months)),
                 days=", ".join(str(d) for d in sorted(c.days)), times=times)
    return t("Repeats on a set rule")


def describe_once(ts: float, tz: ZoneInfo) -> str:
    d = datetime.fromtimestamp(ts, tz)
    return t("Once on {year}-{month}-{day} ({weekday}) at {time}", year=d.year, month=d.month, day=d.day,
             weekday=t(DAY_NAMES[d.isoweekday() % 7]), time=ko_time(d.hour, d.minute))


def describe_interval(seconds: int) -> str:
    """"Every 30 minutes", "Every 2 hours", "Every 1 h 30 min", "Every 3 days"."""
    m = max(1, int(seconds) // 60)
    if m % 1440 == 0:
        return _every(m // 1440, N_("Every day"), N_("Every {n} days"))
    if m % 60 == 0:
        return _every(m // 60, N_("Every hour"), N_("Every {n} hours"))
    if m > 60:
        return t("Every {h} h {m} min", h=m // 60, m=m % 60)
    return _every(m, N_("Every minute"), N_("Every {n} minutes"))


def describe_window(expr: str, minutes: int) -> str:
    """A cron time with a spread: "Every day at 9:00 AM, give or take 15 minutes"."""
    return t("{when}, give or take {n} minutes", when=describe_cron(expr), n=minutes)


def parse_run_at(value: Any, tz: ZoneInfo) -> float:
    """A one-time run: ISO 8601 date-time ('2026-11-08T09:00', with or without an offset; without one it is local to `tz`) or unix seconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    if not text:
        raise ScheduleError("run_at is empty")
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1) if "T" not in text and " " in text else text.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleError("run_at must be a date and time like 2026-11-08T09:00") from None
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d.timestamp()


def parse_day(value: Any, field: str) -> str:
    """A local date ("2026-11-08", or the date part of a date-time) as YYYY-MM-DD; "" for nothing."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise ScheduleError(f"{field} must be a date like 2026-11-08") from None


def day_start(day: str, tz: ZoneInfo) -> float:
    return datetime.fromisoformat(day).replace(tzinfo=tz).timestamp()


def day_end(day: str, tz: ZoneInfo) -> float:
    """The first moment after that local date."""
    return (datetime.fromisoformat(day) + timedelta(days=1)).replace(tzinfo=tz).timestamp()


def _ymd(day: str) -> dict[str, int]:
    d = date.fromisoformat(day)
    return {"year": d.year, "month": d.month, "day": d.day}


def zone(name: str | None, fallback: str = "UTC") -> ZoneInfo:
    for n in (name, fallback, "UTC"):
        try:
            if n:
                return ZoneInfo(n)
        except Exception:  # noqa: BLE001
            continue
    return ZoneInfo("UTC")


# ---------------------------------------------------------------------------------------------------------------- store
RunFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
# runs one schedule as an agent turn; returns {"task_id", "status": done|failed|stopped, "error", "quiet": bool, "result": one line}


def _window_offset(sid: int, base: float, minutes: int) -> float:
    """A fixed offset (seconds, -minutes..+minutes) for one run time of one schedule: the same every time it is computed."""
    if minutes <= 0:
        return 0.0
    h = int(hashlib.sha256(f"{sid}:{int(base)}".encode()).hexdigest()[:8], 16)
    return float((h % (2 * minutes + 1)) - minutes) * 60.0


class Schedules:
    def __init__(self, store: Any, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        self.on_paused: Callable[[dict[str, Any]], None] | None = None   # set by the server: a notification when a schedule pauses itself
        self.on_deleted: Callable[[int], None] | None = None   # set by the server: what was allowed for the schedule goes with it
        self.retried_tasks: dict[int, str] = {}   # task id -> history line, for runs that called schedule_retry (no notification; start_run reads it)
        with store._lock:
            store._db.executescript(SCHEMA)
            have = {r[1] for r in store._db.execute("PRAGMA table_info(schedules)")}
            for col, ddl in COLUMNS:
                if col not in have:
                    store._db.execute(f"ALTER TABLE schedules ADD COLUMN {col} {ddl}")
            store._db.commit()
        # a restart ended whatever was running: say so in the history instead of showing it as running forever
        store._run("UPDATE schedule_runs SET status = 'stopped', finished_at = ?, result = ? WHERE status = 'running'",
                   (clock(), t("Stopped because the server restarted")))

    def default_tz(self) -> str:
        return self.store.profile().get("timezone") or "UTC"

    @staticmethod
    def kind_of(r: dict[str, Any]) -> str:
        if r.get("every_seconds"):
            return "interval"
        if r["run_at"] is not None:
            return "once"
        return "window" if (r.get("window_minutes") or 0) > 0 else "cron"

    def describe(self, r: dict[str, Any]) -> str:
        tz = zone(r["timezone"], self.default_tz())
        kind = self.kind_of(r)
        if kind == "once":
            return describe_once(r["run_at"], tz)
        if kind == "interval":
            when = describe_interval(r["every_seconds"])
        elif kind == "window":
            when = describe_window(r["cron"], r["window_minutes"])
        else:
            when = describe_cron(r["cron"])
        starts, ends = (r.get("starts_on") or ""), (r.get("ends_on") or "")
        if starts and kind != "interval" and day_start(starts, tz) > self.clock():
            when = t("{when}, starting {year}-{month}-{day}", when=when, **_ymd(starts))
        if ends:
            when = t("{when}, until {year}-{month}-{day}", when=when, **_ymd(ends))
        return when

    def _public(self, r: dict[str, Any]) -> dict[str, Any]:
        tz = zone(r["timezone"], self.default_tz())
        kind = self.kind_of(r)
        one = kind == "once"
        live = bool(r["enabled"]) and not r.get("archived_at")
        return {"id": r["id"], "name": r["name"], "prompt": r["prompt"], "kind": kind, "cron": r["cron"] or None,
                "run_at": _iso(r["run_at"], tz) if one else None,
                "every_minutes": int(r["every_seconds"] // 60) if kind == "interval" else None,
                "start_at": _iso(r["anchor_at"], tz) if kind == "interval" else None,
                "window_minutes": int(r.get("window_minutes") or 0) if kind == "window" else 0,
                "starts_on": (r.get("starts_on") or None) if kind in ("cron", "window") else None,
                "ends_on": (r.get("ends_on") or None) if kind != "once" else None,
                "timezone": tz.key, "enabled": bool(r["enabled"]), "description": self.describe(r),
                "next_run": _iso(r["next_run"], tz) if live and r["next_run"] else None,
                "next_run_ts": r["next_run"] if live else None, "created_at": r["created_at"], "updated_at": r["updated_at"],
                "created_by": r["created_by"], "goal_id": r.get("goal_id"),
                "last_run": ({"task_id": r["last_task_id"], "status": r["last_status"], "at": r["last_run_at"]} if r["last_run_at"] else None),
                "failure_count": r["failure_count"], "disabled_reason": r["disabled_reason"] or None,
                "paused": bool(r.get("paused_reason")), "paused_reason": r.get("paused_reason") or None, "paused_at": r.get("paused_at"),
                "archived_at": r.get("archived_at"),
                "retry_at": r.get("retry_at") if not r.get("archived_at") else None,
                "retry_why": (r.get("retry_why") or None) if r.get("retry_at") and not r.get("archived_at") else None,
                "purge_at": (r["archived_at"] + ARCHIVE_DAYS * 86400) if r.get("archived_at") else None}

    def _row(self, sid: int, archived: bool = False) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM schedules WHERE id = ?" + ("" if archived else " AND archived_at IS NULL"), (sid,))
        return rows[0] if rows else None

    def get(self, sid: int) -> dict[str, Any] | None:
        r = self._row(sid)
        return self._public(r) if r else None

    def list(self, goal_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT * FROM schedules WHERE archived_at IS NULL AND (? IS NULL OR goal_id = ?) "
                               "ORDER BY enabled DESC, COALESCE(next_run, 1e18), id", (goal_id, goal_id))
        return [self._public(r) for r in rows]

    def archived(self) -> list[dict[str, Any]]:
        return [self._public(r) for r in self.store._all("SELECT * FROM schedules WHERE archived_at IS NOT NULL ORDER BY archived_at DESC")]

    @staticmethod
    def _check_text(name: str, prompt: str) -> None:
        if not name.strip() or len(name) > 60:
            raise ScheduleError("name is required (at most 60 characters)")
        if not prompt.strip() or len(prompt) > 2000:
            raise ScheduleError("prompt is required: what to do, in words (at most 2000 characters)")

    def _check_goal(self, goal_id: Any) -> int | None:
        if goal_id in (None, "", 0):
            return None
        try:
            gid = int(goal_id)
        except (TypeError, ValueError):
            raise ScheduleError("goal_id must be a goal's number") from None
        if not self.store.goal(gid):
            raise ScheduleError(f"there is no goal #{gid}")
        return gid

    def _check(self, name: str, prompt: str, cron: str | None, run_at: Any, tz_name: str, every_minutes: Any = None,
               window_minutes: Any = 0) -> tuple[str, float | None, ZoneInfo, int | None, int]:
        """Returns (cron expression, one-time or interval start, zone, interval seconds|None, window minutes)."""
        self._check_text(name, prompt)
        if tz_name:
            try:
                ZoneInfo(tz_name)
            except Exception:  # noqa: BLE001
                raise ScheduleError(f"unknown time zone {tz_name!r}") from None
        tz = zone(tz_name, self.default_tz())
        try:
            window = int(window_minutes or 0)
        except (TypeError, ValueError):
            raise ScheduleError("window_minutes must be a number of minutes") from None
        if not 0 <= window <= MAX_WINDOW_MINUTES:
            raise ScheduleError(f"window_minutes must be between 0 and {MAX_WINDOW_MINUTES}")
        has_run_at = run_at not in (None, "")
        if every_minutes not in (None, "", 0):
            if cron:
                raise ScheduleError("give either cron, run_at (one time) or every_minutes (interval), not two of them")
            try:
                every = int(float(every_minutes))
            except (TypeError, ValueError):
                raise ScheduleError("every_minutes must be a number of minutes") from None
            if every * 60 < MIN_GAP_SECONDS:
                raise ScheduleError(f"runs must be at least {MIN_GAP_SECONDS // 60} minutes apart")
            if every > MAX_INTERVAL_MINUTES:
                raise ScheduleError("every_minutes is too long (at most 31 days)")
            if window:
                raise ScheduleError("window_minutes goes with cron, not with every_minutes")
            start = parse_run_at(run_at, tz) if has_run_at else self.clock() + every * 60
            return "", start, tz, every * 60, 0
        if bool(cron) == has_run_at:
            raise ScheduleError("give either cron (repeating), run_at (one time) or every_minutes (interval), not both")
        if cron:
            c = parse_cron(cron)
            first = c.next_after(self.clock(), tz)
            if first is None:
                raise ScheduleError("this cron never runs")
            second = c.next_after(first, tz)
            third = c.next_after(second, tz) if second else None
            gaps = [b - a for a, b in ((first, second), (second, third)) if a and b]
            if any(g < MIN_GAP_SECONDS for g in gaps):
                raise ScheduleError(f"runs must be at least {MIN_GAP_SECONDS // 60} minutes apart")
            if window and any(g < MIN_GAP_SECONDS + 2 * window * 60 for g in gaps):
                raise ScheduleError("the window is too wide for how often this runs (runs would overlap)")
            return c.expr, None, tz, None, window
        if window:
            raise ScheduleError("window_minutes goes with cron, not with run_at")
        ts = parse_run_at(run_at, tz)
        if ts < self.clock() + 30:
            raise ScheduleError("run_at is in the past; pick a future time")
        return "", ts, tz, None, 0

    def _next(self, r: dict[str, Any], after: float) -> float | None:
        """The next run strictly after `after`, inside the schedule's first and last day."""
        if r["run_at"] is not None and not r.get("every_seconds"):
            return self._next_time(r, after)
        tz = zone(r["timezone"], self.default_tz())
        starts, ends = (r.get("starts_on") or ""), (r.get("ends_on") or "")
        if starts and not r.get("every_seconds"):
            after = max(after, day_start(starts, tz) - 1)
        nxt = self._next_time(r, after)
        if nxt is not None and ends and nxt >= day_end(ends, tz):
            return None
        return nxt

    def _days(self, kind: str, starts_on: Any, ends_on: Any, tz: ZoneInfo) -> tuple[str, str]:
        """The checked first and last day for a schedule of `kind` (a one-time job has neither)."""
        if kind == "once":
            return "", ""
        starts = parse_day(starts_on, "starts_on") if kind != "interval" else ""
        ends = parse_day(ends_on, "ends_on")
        if starts and ends and ends < starts:
            raise ScheduleError("ends_on is before starts_on")
        if ends and day_end(ends, tz) <= self.clock():
            raise ScheduleError("ends_on has already passed; pick a later day")
        return starts, ends

    def _next_time(self, r: dict[str, Any], after: float) -> float | None:
        if r.get("every_seconds"):
            every, anchor = float(r["every_seconds"]), float(r["anchor_at"] or r["created_at"])
            if after < anchor:
                return anchor
            return anchor + (int((after - anchor) // every) + 1) * every
        if r["run_at"] is not None:
            return r["run_at"] if r["run_at"] > after else None
        try:
            c = parse_cron(r["cron"])
        except ScheduleError:
            return None
        tz = zone(r["timezone"], self.default_tz())
        window = int(r.get("window_minutes") or 0)
        if window <= 0:
            return c.next_after(after, tz)
        x = after - window * 60
        for _ in range(16):
            base = c.next_after(x, tz)
            if base is None:
                return None
            t = base + _window_offset(r["id"], base, window)
            if t > after:
                return t
            x = base
        return None

    def create(self, name: str, prompt: str, *, cron: str | None = None, run_at: Any = None, timezone: str = "",
               created_by: str = "user", enabled: bool = True, every_minutes: Any = None, window_minutes: Any = 0,
               goal_id: Any = None, starts_on: Any = None, ends_on: Any = None) -> dict[str, Any]:
        if self.store._all("SELECT COUNT(*) AS n FROM schedules WHERE archived_at IS NULL")[0]["n"] >= MAX_SCHEDULES:
            raise ScheduleError(f"at most {MAX_SCHEDULES} schedules; delete one first")
        expr, ts, tz, every, window = self._check(name, prompt, cron, run_at, timezone, every_minutes, window_minutes)
        gid = self._check_goal(goal_id)
        now = self.clock()
        kind = "interval" if every else ("once" if ts is not None else ("window" if window else "cron"))
        starts, ends = self._days(kind, starts_on, ends_on, tz)
        probe = {"id": 0, "every_seconds": every, "anchor_at": ts if every else None, "created_at": now, "run_at": None if every else ts,
                 "cron": expr, "timezone": timezone or "", "window_minutes": window, "starts_on": starts, "ends_on": ends}
        if kind != "once" and self._next(probe, now) is None:
            raise ScheduleError("it never runs between starts_on and ends_on")
        sid = int(self.store._run(
            "INSERT INTO schedules(name, prompt, cron, run_at, timezone, enabled, created_at, updated_at, created_by, kind, every_seconds, "
            "anchor_at, window_minutes, goal_id, starts_on, ends_on) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name.strip(), prompt.strip(), expr, None if every else ts, timezone or "", int(enabled), now, now, created_by, kind, every,
             ts if every else None, window, gid, starts, ends)).lastrowid or 0)
        r = self._row(sid)
        assert r is not None
        self.store._run("UPDATE schedules SET next_run = ? WHERE id = ?", (self._next(r, now) if enabled else None, sid))
        return self.get(sid) or {}

    def update(self, sid: int, **changes: Any) -> dict[str, Any] | None:
        r = self._row(sid)
        if r is None:
            return None
        given = lambda k: changes.get(k) not in (None, "")  # noqa: E731
        name = str(changes["name"]) if given("name") else r["name"]
        prompt = str(changes["prompt"]) if given("prompt") else r["prompt"]
        tz_name = str(changes["timezone"]) if given("timezone") else r["timezone"]
        when_given = given("cron") or given("run_at") or given("every_minutes")
        if given("every_minutes"):
            cron, run_at, every_m = None, changes.get("run_at"), changes["every_minutes"]
            window = 0
        elif given("cron"):
            cron, run_at, every_m = str(changes["cron"]), None, None
            window = changes["window_minutes"] if changes.get("window_minutes") is not None else (r.get("window_minutes") or 0)
        elif given("run_at"):
            cron, run_at, every_m, window = None, changes["run_at"], None, 0
        else:
            every_m = (r["every_seconds"] // 60) if r.get("every_seconds") else None
            cron = r["cron"] or None
            run_at = r["anchor_at"] if every_m else r["run_at"]
            window = changes["window_minutes"] if (changes.get("window_minutes") is not None and cron) else (r.get("window_minutes") or 0)
        window_changed = changes.get("window_minutes") is not None and int(changes["window_minutes"] or 0) != int(r.get("window_minutes") or 0)
        enabled = bool(r["enabled"]) if changes.get("enabled") is None else bool(changes["enabled"])
        gid = self._check_goal(changes["goal_id"]) if "goal_id" in changes and changes["goal_id"] is not None else r.get("goal_id")
        if changes.get("goal_id") == 0:
            gid = None
        if when_given or window_changed or given("timezone") or (enabled and not r["enabled"]):
            if every_m and not when_given:   # turning an interval back on: keep its grid (a start in the past is fine)
                self._check_text(name, prompt)
                expr, ts, every, window = "", r["anchor_at"], r["every_seconds"], 0
            else:
                expr, ts, _, every, window = self._check(name, prompt, cron, run_at, tz_name, every_m, window)
        else:
            self._check_text(name, prompt)
            expr, ts, every = r["cron"], (r["anchor_at"] if r.get("every_seconds") else r["run_at"]), r.get("every_seconds")
            window = r.get("window_minutes") or 0
        kind = "interval" if every else ("once" if ts is not None else ("window" if window else "cron"))
        now = self.clock()
        tz = zone(tz_name, self.default_tz())
        # first / last day: None keeps what is saved, "" clears it; a change of kind drops what the new kind cannot have
        want_starts = (r.get("starts_on") or "") if changes.get("starts_on") is None else changes["starts_on"]
        want_ends = (r.get("ends_on") or "") if changes.get("ends_on") is None else changes["ends_on"]
        if changes.get("ends_on") is None and want_ends and day_end(want_ends, tz) <= now:
            starts, ends = self._days(kind, want_starts, "", tz)[0], want_ends if kind != "once" else ""   # an old, passed end stays
        else:
            starts, ends = self._days(kind, want_starts, want_ends, tz)
        if enabled and kind != "once":
            probe = {**r, "every_seconds": every, "anchor_at": ts if every else None, "run_at": None if every else ts, "cron": expr or "",
                     "timezone": tz_name or "", "window_minutes": int(window or 0), "starts_on": starts, "ends_on": ends}
            if self._next(probe, now) is None:
                raise ScheduleError("it never runs again before ends_on; pick a later end day" if ends else "this schedule never runs")
        self.store._run(
            "UPDATE schedules SET name = ?, prompt = ?, cron = ?, run_at = ?, timezone = ?, enabled = ?, updated_at = ?, kind = ?, "
            "every_seconds = ?, anchor_at = ?, window_minutes = ?, goal_id = ?, starts_on = ?, ends_on = ?, "
            "failure_count = CASE WHEN ? = 1 AND enabled = 0 THEN 0 ELSE failure_count END, "
            "disabled_reason = CASE WHEN ? = 1 THEN '' ELSE disabled_reason END, "
            "paused_reason = CASE WHEN ? = 1 THEN '' ELSE paused_reason END, paused_at = CASE WHEN ? = 1 THEN NULL ELSE paused_at END "
            "WHERE id = ?",
            (name.strip(), prompt.strip(), expr or "", None if every else ts, tz_name or "", int(enabled), now, kind, every,
             ts if every else None, int(window or 0), gid, starts, ends, int(enabled), int(enabled), int(enabled), int(enabled), sid))
        r = self._row(sid)
        assert r is not None
        self.store._run("UPDATE schedules SET next_run = ? WHERE id = ?", (self._next(r, now) if enabled else None, sid))
        if changes.get("enabled") is False:   # the user switched it off: no try-again later either
            self.store._run("UPDATE schedules SET retry_at = NULL, retry_why = '' WHERE id = ?", (sid,))
        return self.get(sid)

    # ------------------------------------------------------------------ pause (a lasting blocker), archive (delete), restore
    def pause(self, sid: int, reason: str, by: str = "agent") -> dict[str, Any] | None:
        """Turn a schedule off because something keeps it from working (a missing login, a site that changed for good, an account
        problem). It shows as paused with the reason until the user or the agent turns it back on."""
        r = self._row(sid)
        if r is None:
            return None
        why = re.sub(r"\s+", " ", str(reason or "")).strip()[:300] or t("No reason was given")
        now = self.clock()
        self.store._run("UPDATE schedules SET enabled = 0, next_run = NULL, paused_reason = ?, paused_at = ?, updated_at = ?, "
                        "retry_at = NULL, retry_why = '' WHERE id = ?", (why, now, now, sid))
        s = self.get(sid)
        if s and self.on_paused:
            try:
                self.on_paused({**s, "paused_by": by})
            except Exception:  # noqa: BLE001
                log.exception("schedule %s: the pause notification failed", sid)
        return s

    def delete(self, sid: int) -> bool:
        """Archive: off and out of the list, restorable for ARCHIVE_DAYS. What the user allowed for its runs (logins, purchases,
        schedule_grants.py) is removed at once: a restored schedule asks again."""
        now = self.clock()
        gone = self.store._run("UPDATE schedules SET archived_at = ?, next_run = NULL, updated_at = ?, retry_at = NULL, retry_why = '' "
                               "WHERE id = ? AND archived_at IS NULL",
                               (now, now, sid)).rowcount > 0
        if gone and self.on_deleted is not None:
            try:
                self.on_deleted(sid)
            except Exception:  # noqa: BLE001
                log.exception("schedule %s: removing its permissions failed", sid)
        return gone

    def finish(self, sid: int, reason: str) -> dict[str, Any] | None:
        """Off because its task is done (a one-shot "when X, do Y" task used what it was allowed, schedule_grants.py): no more runs;
        the run going on finishes. It shows as off with the reason and can be turned on again (asking again at setup)."""
        if self._row(sid) is None:
            return None
        self.store._run("UPDATE schedules SET enabled = 0, next_run = NULL, disabled_reason = ?, updated_at = ?, retry_at = NULL, "
                        "retry_why = '' WHERE id = ?", (reason[:300], self.clock(), sid))
        return self.get(sid)

    def runs_per_month(self, sid: int, days: int = 31, most: int = 62) -> int:
        """How many runs the schedule makes in the next `days` days (1 for a one-time job, at most `most`): the cadence a monthly
        spending cap of a standing purchase permission is worked out from."""
        r = self._row(sid)
        if r is None:
            return 0
        if self.kind_of(r) == "once":
            return 1
        now = self.clock()
        end, n, at = now + days * 86400, 0, now
        while n < most:
            nxt = self._next(r, at)
            if nxt is None or nxt > end:
                break
            n, at = n + 1, nxt
        return max(1, n)

    def restore(self, sid: int) -> dict[str, Any] | None:
        r = self._row(sid, archived=True)
        if r is None or not r["archived_at"]:
            return None
        if r["archived_at"] < self.clock() - ARCHIVE_DAYS * 86400:
            return None
        if self.store._all("SELECT COUNT(*) AS n FROM schedules WHERE archived_at IS NULL")[0]["n"] >= MAX_SCHEDULES:
            raise ScheduleError(f"at most {MAX_SCHEDULES} schedules; delete one first")
        now = self.clock()
        nxt = self._next(r, now) if r["enabled"] else None
        enabled = int(r["enabled"] and nxt is not None)
        self.store._run("UPDATE schedules SET archived_at = NULL, enabled = ?, next_run = ?, updated_at = ?, "
                        "disabled_reason = CASE WHEN ? = 0 AND ? = 1 THEN ? ELSE disabled_reason END WHERE id = ?",
                        (enabled, nxt if enabled else None, now, enabled, int(r["enabled"]), t("Restored turned off because its time has passed"), sid))
        return self.get(sid)

    def erase(self, sid: int, *, archived_only: bool = False) -> bool:
        """Delete for good now (the 삭제 choice, or 삭제 on an archived one): the schedule and its run history go and cannot be
        restored. A live one first loses what the user allowed for its runs, as an archived one already did. `archived_only`: only
        one in the archive."""
        r = self._row(sid, archived=True)
        if r is None or (archived_only and not r.get("archived_at")):
            return False
        if not r.get("archived_at") and self.on_deleted is not None:
            try:
                self.on_deleted(sid)
            except Exception:  # noqa: BLE001
                log.exception("schedule %s: removing its permissions failed", sid)
        self.store._run("DELETE FROM schedule_runs WHERE schedule_id = ?", (sid,))
        self.store._run("DELETE FROM schedules WHERE id = ?", (sid,))
        return True

    def purge_archived(self, now: float | None = None) -> int:
        """Remove for good what was archived more than ARCHIVE_DAYS ago (with its run history)."""
        cut = (self.clock() if now is None else now) - ARCHIVE_DAYS * 86400
        ids = [r["id"] for r in self.store._all("SELECT id FROM schedules WHERE archived_at IS NOT NULL AND archived_at < ?", (cut,))]
        for sid in ids:
            self.store._run("DELETE FROM schedule_runs WHERE schedule_id = ?", (sid,))
            self.store._run("DELETE FROM schedules WHERE id = ?", (sid,))
        return len(ids)

    # ------------------------------------------------------------------ the runner's side
    def due(self, now: float) -> list[dict[str, Any]]:
        return self.store._all("SELECT * FROM schedules WHERE enabled = 1 AND archived_at IS NULL AND next_run IS NOT NULL AND next_run <= ? "
                               "ORDER BY next_run", (now,))

    def next_times(self) -> list[float]:
        return [r["next_run"] for r in self.store._all(
            "SELECT next_run FROM schedules WHERE enabled = 1 AND archived_at IS NULL AND next_run IS NOT NULL")] + [
            r["retry_at"] for r in self.store._all("SELECT retry_at FROM schedules WHERE archived_at IS NULL AND retry_at IS NOT NULL")]

    # ------------------------------------------------------------------ a temporary blocker: try again later (schedule_retry)
    def tz_of(self, r: dict[str, Any]) -> ZoneInfo:
        return zone(r["timezone"], self.default_tz())

    @staticmethod
    def _clock_text(ts: float, tz: ZoneInfo) -> str:
        return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")

    def set_retry(self, sid: int, at: Any, why: str, task_id: int | None = None) -> float:
        """The run going on asks to be tried again at `at` (local time or ISO, in the schedule's zone): the same schedule runs once more
        then, under the same permissions, and its regular times stay as they are. Raises ScheduleError when it cannot be. Returns the
        time (unix seconds). The run is recorded as skipped with a line that says so (Scheduler.start_run), never as a failure."""
        r = self._row(sid)
        if r is None:
            raise ScheduleError("no such schedule")
        tz = self.tz_of(r)
        text = re.sub(r"\s+", " ", str(why or "")).strip()
        if not text:
            raise ScheduleError("say why in `why`: what blocks the job right now")
        if len(text) > 160:
            raise ScheduleError("`why` is too long (at most 160 characters)")
        try:
            ts = parse_run_at(at, tz)
        except ScheduleError:
            raise ScheduleError("`at` must be a local date and time like 2026-11-08T09:00") from None
        now = self.clock()
        if ts < now + RETRY_MIN_SECONDS:
            raise ScheduleError(f"`at` is too soon: pick a time at least {RETRY_MIN_SECONDS // 60} minutes from now")
        if ts > now + RETRY_MAX_SECONDS:
            raise ScheduleError("`at` is too far away: at most 24 hours from now (a block that lasts longer: schedule_pause)")
        if r["enabled"] and r["next_run"] is not None and ts >= r["next_run"]:
            raise ScheduleError(f"`at` is not before this job's next regular run ({self._clock_text(r['next_run'], tz)}): that run will do it")
        if int(r.get("retries") or 0) >= MAX_RETRIES:
            raise ScheduleError(f"it was already tried again {MAX_RETRIES} times since the last regular run: report what blocks it "
                                "(schedule_pause when it is lasting)")
        first = r.get("retry_first_at")
        if not int(r.get("retries") or 0) or first is None:   # the run that began this stretch of retries
            got = self.store._all("SELECT started_at FROM schedule_runs WHERE schedule_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1", (sid,))
            first = got[0]["started_at"] if got else now
        self.store._run("UPDATE schedules SET retry_at = ?, retry_why = ?, retries = retries + 1, retry_first_at = ? WHERE id = ?",
                        (ts, text, first, sid))
        with i18n.owner_language():
            line = t("Will try again at {time}: {why}", time=self._clock_text(ts, tz), why=text)
        if task_id:
            self.retried_tasks[int(task_id)] = line
        return ts

    def retry_due(self, now: float) -> list[dict[str, Any]]:
        return self.store._all("SELECT * FROM schedules WHERE retry_at IS NOT NULL AND retry_at <= ? AND archived_at IS NULL "
                               "AND (enabled = 1 OR (run_at IS NOT NULL AND every_seconds IS NULL)) ORDER BY retry_at", (now,))

    def retry_note(self, r: dict[str, Any]) -> str:
        """What a retry run is told (English, for the model); `r` is the row before its retry_at is taken."""
        first = r.get("retry_first_at") or r.get("last_run_at") or self.clock()
        return (f"[Retry {int(r.get('retries') or 1)} of this job: the run at {self._clock_text(first, self.tz_of(r))} stopped because: "
                f"{r.get('retry_why') or 'it was blocked'}. Do the job again. In your report say that the earlier try could not be "
                "done and why.]")

    def take_retry(self, sid: int) -> None:
        """The retry starts now: it is no longer pending (its count stays until the next regular run)."""
        self.store._run("UPDATE schedules SET retry_at = NULL WHERE id = ?", (sid,))

    def postpone_retry(self, sid: int, at: float) -> None:
        self.store._run("UPDATE schedules SET retry_at = ? WHERE id = ?", (at, sid))

    def regular_run(self, sid: int) -> None:
        """A regular run (its own time, or one the user asked for) starts: it replaces a pending retry and the count starts again."""
        r = self._row(sid)
        if r is None or not (r.get("retry_at") is not None or r.get("retries") or r.get("retry_first_at") is not None):
            return
        if r.get("retry_at") is not None:
            log.info("schedule %s: a regular run replaces its pending retry", sid)
        self.store._run("UPDATE schedules SET retry_at = NULL, retry_why = '', retries = 0, retry_first_at = NULL WHERE id = ?", (sid,))

    def advance(self, r: dict[str, Any], now: float) -> None:
        """Move past the time that came due (whether it ran, was skipped or missed). A one-time schedule is spent."""
        if r["run_at"] is not None and not r.get("every_seconds"):
            self.store._run("UPDATE schedules SET next_run = NULL, enabled = 0 WHERE id = ?", (r["id"],))
            return
        nxt = self._next(r, now)
        if nxt is None and r.get("ends_on"):   # its last day has passed: finished
            self.store._run("UPDATE schedules SET next_run = NULL, enabled = 0, disabled_reason = ? WHERE id = ?",
                            (t("Finished: its last day has passed"), r["id"]))
        else:
            self.store._run("UPDATE schedules SET next_run = ? WHERE id = ?", (nxt, r["id"]))

    def mark_missed(self, r: dict[str, Any], now: float) -> None:
        self.store._run("UPDATE schedules SET last_status = 'missed' WHERE id = ?", (r["id"],))
        if r["run_at"] is not None and not r.get("every_seconds"):
            self.store._run("UPDATE schedules SET disabled_reason = ? WHERE id = ?",
                            (t("Could not run at the set time because the server was off"), r["id"]))
        self.add_run(r["id"], "skipped", t("Could not run at the set time because the server was off"))
        self.advance(r, now)

    # ------------------------------------------------------------------ run history
    def add_run(self, sid: int, status: str, result: str = "", task_id: int | None = None) -> int:
        """A finished row at once (a skip). Returns its id."""
        now = self.clock()
        rid = int(self.store._run("INSERT INTO schedule_runs(schedule_id, started_at, finished_at, status, result, task_id) VALUES(?,?,?,?,?,?)",
                                  (sid, now, now if status != "running" else None, status, result[:300], task_id)).lastrowid or 0)
        self.store._run("DELETE FROM schedule_runs WHERE schedule_id = ? AND id <= ? - ? AND id NOT IN "
                        "(SELECT id FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?)", (sid, rid, KEEP_RUNS, sid, KEEP_RUNS))
        return rid

    def runs(self, sid: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?", (sid, max(1, min(int(limit), KEEP_RUNS))))
        return [{"id": r["id"], "schedule_id": r["schedule_id"], "started_at": r["started_at"], "finished_at": r["finished_at"],
                 "status": r["status"], "result": r["result"], "task_id": r["task_id"], "error": r["error"] or None} for r in rows]

    def runs_since(self, since: float) -> list[dict[str, Any]]:
        """Finished runs of every schedule since `since` (for the proactive pass), oldest first."""
        rows = self.store._all("SELECT r.*, s.name FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id "
                               "WHERE r.finished_at IS NOT NULL AND r.finished_at > ? ORDER BY r.id", (since,))
        return [dict(r) for r in rows]

    def record_run(self, sid: int, task_id: int | None, status: str, error: str = "", *, run_id: int | None = None,
                   result: str = "", quiet: bool = False) -> dict[str, Any] | None:
        """What a run came to. Returns the schedule when this run turned it off (too many failures in a row), else None.
        The history row says done | quiet (nothing worth telling) | failed | stopped | paused (the run paused its schedule)."""
        r = self._row(sid, archived=True)
        if r is None:
            return None
        started = None
        if run_id:
            got = self.store._all("SELECT started_at FROM schedule_runs WHERE id = ?", (run_id,))
            started = got[0]["started_at"] if got else None
        paused_now = bool(r.get("paused_reason")) and started is not None and (r.get("paused_at") or 0) >= started
        shown = "paused" if paused_now else ("quiet" if quiet and status == "done" else status)
        line = (r.get("paused_reason") if paused_now else "") or result or (error[:200] if status == "failed" else "")
        if run_id:
            self.store._run("UPDATE schedule_runs SET finished_at = ?, status = ?, result = ?, task_id = ?, error = ? WHERE id = ?",
                            (self.clock(), shown, (line or "")[:300], task_id, (error or "")[:500], run_id))
        else:
            self.add_run(sid, shown, line or "", task_id)
        fails = r["failure_count"] + 1 if status == "failed" else (0 if status == "done" else r["failure_count"])
        self.store._run("UPDATE schedules SET last_run_at = ?, last_task_id = ?, last_status = ?, failure_count = ? WHERE id = ?",
                        (self.clock(), task_id, shown, fails, sid))
        if status == "failed" and fails >= MAX_FAILURES and r["enabled"] and not r.get("archived_at"):
            reason = t("Turned off after failing {n} times in a row", n=MAX_FAILURES) + (f": {error[:160]}" if error else "")
            self.store._run("UPDATE schedules SET enabled = 0, next_run = NULL, disabled_reason = ?, retry_at = NULL, retry_why = '' "
                            "WHERE id = ?", (reason, sid))
            return self.get(sid)
        return None


def _iso(ts: float | None, tz: ZoneInfo) -> str | None:
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="minutes") if ts else None


# ---------------------------------------------------------------------------------------------------------------- the runner
class Scheduler:
    """Wakes up every `TICK_SECONDS` (or sooner when a run is due) and starts what is due. `tick(now)` is the whole decision, so a
    test drives it with a fake clock. Internal jobs are plain callables: daily ones run once per local day at a set time
    (`add_daily`), repeating ones every so many seconds (`add_every`; the first run one period after the server starts)."""

    def __init__(self, schedules: Schedules, run: RunFn | None = None, *, clock: Callable[[], float] = time.time,
                 on_disabled: Callable[[dict[str, Any]], None] | None = None):
        self.schedules, self.run_fn, self.clock, self.on_disabled = schedules, run, clock, on_disabled
        self.running: dict[int, asyncio.Task[Any]] = {}
        self.daily: list[tuple[str, int, int, Callable[[], Awaitable[Any]]]] = []   # (name, hour, minute, job)
        self._daily_done: dict[str, str] = {}
        self.every: list[tuple[str, Callable[[], float], Callable[[], Awaitable[Any]]]] = []   # (name, period in seconds, job)
        # Keyguard (keyguard.py): while `paused()` is true (no key since a restart), jobs that need a secret or a provider key wait
        # quietly: not run, not marked done, so each runs once at the first tick after the unlock if it is still due. Jobs that need
        # none (housekeeping) are added with needs_secrets=False and keep running.
        self.paused: Callable[[], bool] = lambda: False
        self.no_secrets: set[str] = set()
        self._held_logged = False
        self._every_last: dict[str, float] = {}
        self._every_task: dict[str, asyncio.Task[Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    def add_daily(self, name: str, hour: int, minute: int, job: Callable[[], Awaitable[Any]], *, needs_secrets: bool = True) -> None:
        self.daily.append((name, hour, minute, job))
        if not needs_secrets:
            self.no_secrets.add(name)

    def held(self, name: str) -> bool:
        """This internal job waits for Keyguard's unlock now (logged once per locked stretch, never with anything secret)."""
        if name in self.no_secrets or not self.paused():
            return False
        if not self._held_logged:
            self._held_logged = True
            log.info("Keyguard is locked: background jobs that need a key wait for the unlock")
        return True

    def add_every(self, name: str, seconds: float | Callable[[], float], job: Callable[[], Awaitable[Any]], *,
                  needs_secrets: bool = True) -> None:
        """A repeating internal job (the checklist, watchers, the proactive pass). `seconds` may be a callable, read on every tick
        (a period the user can change). A run that is still going is never started again. needs_secrets=False: housekeeping that
        uses no key, so it keeps running while Keyguard is locked."""
        self.every.append((name, seconds if callable(seconds) else (lambda s=float(seconds): s), job))
        if not needs_secrets:
            self.no_secrets.add(name)

    def busy(self, sid: int) -> bool:
        t = self.running.get(sid)
        return t is not None and not t.done()

    def start_run(self, r: dict[str, Any], note: str = "", *, retry: bool = False) -> bool:
        """Start one run of schedule row/public `r` now. False when that schedule is already running.
        `note`: a word for this run only (an expired approval tried again), given to run_fn as the schedule's "retry_note".
        `retry`: the try-again a run asked for (schedule_retry); any other run is a regular one and replaces a pending retry."""
        sid = int(r["id"])
        if self.busy(sid) or self.run_fn is None:
            return False
        if not retry:
            self.schedules.regular_run(sid)
        sched = self.schedules.get(sid) or {}
        if note:
            sched = {**sched, "retry_note": note}
        run_id = self.schedules.add_run(sid, "running")

        async def go() -> None:
            try:
                with i18n.owner_language():   # a run speaks the owner's language, also when a request (a "run now" button) started it
                    out = await self.run_fn(sched)
            except Exception as e:  # noqa: BLE001 - a run that could not even start counts as a failure
                log.warning("schedule %s could not run: %s", sid, e)
                out = {"task_id": None, "status": "failed", "error": f"{type(e).__name__}: {str(e)[:200]}"}
            status, result, error = out.get("status", "failed"), str(out.get("result") or ""), out.get("error", "")
            asked = self.schedules.retried_tasks.pop(out.get("task_id") or 0, None)
            if asked and status in ("done", "failed"):   # it asked to be tried again later: a skip with the time, not a failure
                status, result, error = "skipped", asked, ""
            off = self.schedules.record_run(sid, out.get("task_id"), status, error, run_id=run_id,
                                            result=result, quiet=bool(out.get("quiet")) and status != "skipped")
            if off and self.on_disabled:
                self.on_disabled(off)

        self.running[sid] = asyncio.ensure_future(go())
        return True

    def tick(self, now: float | None = None) -> list[int]:
        """Start everything due at `now`; returns the ids started."""
        now = self.clock() if now is None else now
        started = []
        for r in self.schedules.due(now):
            late = now - r["next_run"]
            if late > CATCH_UP_SECONDS:
                self.schedules.mark_missed(r, now)
                self.schedules.regular_run(r["id"])
                continue
            self.schedules.advance(r, now)
            if self.busy(r["id"]):
                self.schedules.add_run(r["id"], "skipped", t("Skipped this time because the last run had not finished"))
                continue      # the last run is still going: this time is skipped
            if self.start_run(r):
                started.append(r["id"])
        for r in self.schedules.retry_due(now):   # the tries a run asked for: the same schedule again, its regular times untouched
            sid = int(r["id"])
            if now - r["retry_at"] > CATCH_UP_SECONDS:   # the server was off for long: the next regular run does the job
                self.schedules.take_retry(sid)
                self.schedules.add_run(sid, "skipped", t("Could not run at the set time because the server was off"))
                continue
            if self.busy(sid):
                self.schedules.postpone_retry(sid, now + RETRY_BUSY_SECONDS)
                continue
            note = self.schedules.retry_note(r)
            self.schedules.take_retry(sid)
            if self.start_run(r, note, retry=True):
                started.append(sid)
        if not self.paused():
            self._held_logged = False
        self._daily(now)
        self._every(now)
        return started

    def _daily(self, now: float) -> None:
        tz = zone(self.schedules.default_tz())
        local = datetime.fromtimestamp(now, tz)
        store = self.schedules.store
        day = local.strftime("%Y-%m-%d")
        for name, h, m, job in self.daily:
            last = self._daily_done.get(name) or store.meta(f"daily:{name}")
            if not last:
                # the very first start of this feature: begin with the next day's run, not at once (no model call on an upgrade)
                self._daily_done[name] = day
                store.set_meta(f"daily:{name}", day)
                continue
            if last >= day or (local.hour, local.minute) < (h, m):
                continue
            if self.held(name):
                continue   # not marked done: it runs at the first tick after the unlock
            self._daily_done[name] = day
            store.set_meta(f"daily:{name}", day)       # a restart later today does not run it again
            asyncio.ensure_future(_quiet(name, job))

    def _every(self, now: float) -> None:
        for name, period, job in self.every:
            last = self._every_last.get(name)
            if last is None:
                self._every_last[name] = now          # the first period starts with the server, not at once
                continue
            try:
                p = max(1.0, float(period()))
            except Exception:  # noqa: BLE001
                continue
            t = self._every_task.get(name)
            if now - last < p or (t is not None and not t.done()):
                continue
            if self.held(name):
                continue   # its period is not restarted: it runs at the first tick after the unlock
            self._every_last[name] = now
            self._every_task[name] = asyncio.ensure_future(_quiet(name, job))

    def poke(self) -> None:
        self._wake.set()

    async def loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the loop must keep going
                log.exception("scheduler tick failed")
            nxt = self.schedules.next_times()
            wait = TICK_SECONDS
            if nxt:
                wait = max(1.0, min(wait, min(nxt) - self.clock() + 0.5))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self.loop())

    async def stop(self) -> None:
        for t in [self._task, *self.running.values(), *self._every_task.values()]:
            if t is not None and not t.done():
                t.cancel()
        self._task = None


async def _quiet(name: str, job: Callable[[], Awaitable[Any]]) -> None:
    from .keyguard import Locked
    try:
        await job()
    except Locked:   # a key was needed while Keyguard is locked: tried again after the unlock (no trace, nothing secret logged)
        log.info("internal job %s waits for Keyguard's unlock", name)
    except Exception:  # noqa: BLE001
        log.exception("internal job %s failed", name)


# ---------------------------------------------------------------------------------------------------------------- agent tools
RUN_WORDS = {"running": N_("Running"), "done": N_("Done"), "quiet": N_("Nothing to tell"), "failed": N_("Failed"),
             "stopped": N_("Stopped"), "skipped": N_("Skipped"), "paused": N_("Paused")}   # translate with t() where shown


def current_schedule_id() -> int | None:
    """The schedule whose run is going on in this turn (None in a chat turn)."""
    from .workers import current_turn   # the server sets it for every turn
    src = str((current_turn.get() or {}).get("source") or "")
    return int(src[9:]) if src.startswith("schedule:") and src[9:].isdigit() else None


def schedule_tools(schedules: Schedules, scheduler: Scheduler | None = None) -> list[Tool]:
    def line(s: dict[str, Any]) -> str:
        if s["paused"]:
            state = f"paused ({s['paused_reason']})"
        else:
            state = "on" if s["enabled"] else f"off{' (' + s['disabled_reason'] + ')' if s['disabled_reason'] else ''}"
        last = f"; last run {s['last_run']['status']}" if s["last_run"] else ""
        goal = f"; goal #{s['goal_id']}" if s.get("goal_id") else ""
        return (f"- #{s['id']} '{s['name']}': {s['description']} ({s['timezone']}); {state}; next {s['next_run'] or '-'}{last}{goal}; "
                f"does: {s['prompt'][:200]}")

    def sid(args: dict[str, Any]) -> int:
        try:
            return int(str(args.get("id", "")).lstrip("#"))
        except ValueError:
            return 0

    def when_args(args: dict[str, Any]) -> tuple[str | None, Any, Any, Any]:
        return (str(args.get("cron") or "") or None, args.get("run_at"), args.get("every_minutes"), args.get("window_minutes") or 0)

    def validate_create(args: dict[str, Any]) -> str | None:
        cron, run_at, every, window = when_args(args)
        try:
            schedules._check(str(args.get("name") or ""), str(args.get("prompt") or ""), cron, run_at, str(args.get("timezone") or ""),
                             every, window)
            schedules._check_goal(args.get("goal_id"))
        except ScheduleError as e:
            return str(e)
        return None

    def when_words(args: dict[str, Any]) -> str:
        """The asked-for time in words (never a cron expression), with its first / last day."""
        tz = zone(str(args.get("timezone") or ""), schedules.default_tz())
        cron, run_at, every, window = when_args(args)
        if every not in (None, "", 0):
            when = describe_interval(int(float(every)) * 60)
        elif cron:
            when = describe_window(cron, int(window)) if int(window or 0) else describe_cron(cron)
        else:
            return describe_once(parse_run_at(run_at, tz), tz)
        try:
            starts, ends = parse_day(args.get("starts_on"), "starts_on"), parse_day(args.get("ends_on"), "ends_on")
        except ScheduleError:
            starts = ends = ""
        if starts and cron:
            when = t("{when}, starting {year}-{month}-{day}", when=when, **_ymd(starts))
        if ends:
            when = t("{when}, until {year}-{month}-{day}", when=when, **_ymd(ends))
        return when

    def card_lines(args: dict[str, Any]) -> list[dict[str, str]]:
        """The approval card's readable pairs: the name, the time as a sentence (never a cron expression) and the task."""
        cur = schedules.get(sid(args)) or {} if args.get("id") is not None else {}
        out = []
        if args.get("name") or cur.get("name"):
            out.append({"label": t("Name"), "value": str(args.get("name") or cur.get("name"))[:60]})
        if args.get("cron") or args.get("run_at") not in (None, "") or args.get("every_minutes") not in (None, "", 0):
            try:
                out.append({"label": t("When"), "value": when_words(args)})
            except ScheduleError:
                pass
        if args.get("prompt"):
            out.append({"label": t("Task"), "value": str(args["prompt"])[:120]})
        return out

    def extra(_approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"lines": card_lines(args)}

    async def describe_create(args: dict[str, Any]) -> str:
        when = when_words(args)
        return t("Create a schedule: '{name}' · {when} · task: {task}", name=str(args.get("name"))[:60], when=when,
                 task=str(args.get("prompt"))[:150])

    async def create(args: dict[str, Any]) -> str:
        cron, run_at, every, window = when_args(args)
        try:
            s = schedules.create(str(args["name"]), str(args["prompt"]), cron=cron, run_at=run_at, timezone=str(args.get("timezone") or ""),
                                 created_by="agent", every_minutes=every, window_minutes=window, goal_id=args.get("goal_id"),
                                 starts_on=args.get("starts_on"), ends_on=args.get("ends_on"))
        except ScheduleError as e:
            return f"error: {e}"
        if scheduler:
            scheduler.poke()
        return (f"Saved schedule #{s['id']} '{s['name']}': {s['description']}, next run {s['next_run']} ({s['timezone']}). If its runs "
                "will sign in, pay, book, send something or change something, ask now, once, with task_allow(schedule_id="
                f"{s['id']}, trigger, action, ...) so they do not stop for an approval nobody is there to give.")

    async def list_(args: dict[str, Any]) -> str:
        gid = args.get("goal_id")
        items = schedules.list(int(gid) if str(gid or "").isdigit() else None)
        return ("Schedules:\n" + "\n".join(line(s) for s in items)) if items else "There are no schedules."

    def validate_id(args: dict[str, Any]) -> str | None:
        return None if schedules.get(sid(args)) else f"no schedule #{args.get('id')}; see schedule_list"

    def validate_update(args: dict[str, Any]) -> str | None:
        if problem := validate_id(args):
            return problem
        s = schedules.get(sid(args)) or {}
        cron, run_at, every, window = when_args(args)
        if cron or run_at not in (None, "") or every not in (None, "", 0):
            try:
                schedules._check(str(args.get("name") or s["name"]), str(args.get("prompt") or s["prompt"]), cron,
                                 None if cron else run_at, str(args.get("timezone") or ""), every, window)
            except ScheduleError as e:
                return str(e)
        if args.get("goal_id") not in (None, "", 0):
            try:
                schedules._check_goal(args.get("goal_id"))
            except ScheduleError as e:
                return str(e)
        return None

    def only_pausing(args: dict[str, Any]) -> bool:
        return args.get("enabled") is False and not any(args.get(k) not in (None, "") for k in
                                                         ("name", "prompt", "cron", "run_at", "timezone", "every_minutes", "window_minutes"))

    async def describe_update(args: dict[str, Any]) -> str:
        """What the approval card says: the changed parts in words, and the new time as a sentence (never field names or cron)."""
        s = schedules.get(sid(args)) or {}
        labels = {"name": N_("name"), "prompt": N_("task"), "cron": N_("time"), "run_at": N_("time"), "every_minutes": N_("time"),
                  "window_minutes": N_("time"), "starts_on": N_("first day"), "ends_on": N_("last day"), "timezone": N_("time zone"),
                  "enabled": N_("on/off"), "goal_id": N_("goal")}
        parts: list[str] = []
        for k, label in labels.items():
            if (args.get(k) not in (None, "") or (k == "ends_on" and args.get(k) == "")) and t(label) not in parts:
                parts.append(t(label))
        out = t("Change schedule '{name}' ({what})", name=s.get("name", ""), what=", ".join(parts))
        if args.get("cron") or args.get("run_at") not in (None, "") or args.get("every_minutes") not in (None, "", 0):
            try:
                out += " · " + when_words(args)
            except ScheduleError:
                pass
        return out + (t(" · task: {task}", task=str(args["prompt"])[:120]) if args.get("prompt") else "")

    async def update(args: dict[str, Any]) -> str:
        try:
            s = schedules.update(sid(args), name=args.get("name") or None, prompt=args.get("prompt") or None, cron=args.get("cron") or None,
                                 run_at=args.get("run_at"), timezone=args.get("timezone") or None, enabled=args.get("enabled"),
                                 every_minutes=args.get("every_minutes") or None, window_minutes=args.get("window_minutes"),
                                 goal_id=args.get("goal_id"), starts_on=args.get("starts_on"), ends_on=args.get("ends_on"))
        except ScheduleError as e:
            return f"error: {e}"
        if s is None:
            return "error: no such schedule"
        if scheduler:
            scheduler.poke()
        return "Updated:\n" + line(s)

    async def describe_delete(args: dict[str, Any]) -> str:
        s = schedules.get(sid(args)) or {}
        return t("Delete schedule '{name}' ({description}) (it can be restored for {days} days)", name=s.get("name", ""),
                 description=s.get("description", ""), days=ARCHIVE_DAYS)

    async def delete(args: dict[str, Any]) -> str:
        return "Deleted." if schedules.delete(sid(args)) else "error: no such schedule"

    def pause_id(args: dict[str, Any]) -> int:
        return sid(args) or (current_schedule_id() or 0)

    def validate_pause(args: dict[str, Any]) -> str | None:
        if not schedules.get(pause_id(args)):
            return f"no schedule #{args.get('id')}; see schedule_list"
        if not str(args.get("reason") or "").strip():
            return "say why in reason (what blocks it and what the user needs to do)"
        return None

    async def pause(args: dict[str, Any]) -> str:
        s = schedules.pause(pause_id(args), str(args.get("reason") or ""))
        if s is None:
            return "error: no such schedule"
        return (f"Paused schedule #{s['id']} '{s['name']}': {s['paused_reason']}. The user was notified; it stays off until they (or you, "
                "with schedule_update enabled=true) turn it back on.")

    async def retry(args: dict[str, Any]) -> str:
        from .workers import current_turn
        own = current_schedule_id()
        if not own:
            return "error: only a scheduled run can do this"
        try:
            ts = schedules.set_retry(own, args.get("at"), str(args.get("why") or ""), (current_turn.get() or {}).get("task_id"))
        except ScheduleError as e:
            return f"error: {e}"
        if scheduler:
            scheduler.poke()
        when = schedules._clock_text(ts, schedules.tz_of(schedules._row(own) or {"timezone": ""}))
        return (f"Will try again at {when}. End this run now: reply exactly NO_REPLY (nothing is sent now; the next run's report says "
                "what happened).")

    async def runs(args: dict[str, Any]) -> str:
        s = schedules.get(sid(args))
        if s is None:
            return f"error: no schedule #{args.get('id')}"
        items = schedules.runs(s["id"], int(args.get("limit") or 10))
        if not items:
            return f"Schedule #{s['id']} '{s['name']}' has not run yet."
        tz = zone(s["timezone"])
        rows = [f"- {datetime.fromtimestamp(r['started_at'], tz).strftime('%m-%d %H:%M')} {r['status']}"
                + (f" (task {r['task_id']})" if r["task_id"] else "") + (f": {r['result']}" if r["result"] else "") for r in items]
        return f"Runs of #{s['id']} '{s['name']}' (newest first):\n" + "\n".join(rows)

    props = {"name": {"type": "string", "description": "short name the user will recognise"},
             "prompt": {"type": "string", "description": (
                 "a self-contained runbook for each run, whatever the job is (a run starts with no memory of this chat): the goal; "
                 "what must be true first and what to do when it is not (something missing: ask the user for it); exactly where it "
                 "happens (the addresses, services, files, devices or people you used or found); the steps in order with the user's "
                 "own choices and saved preferences spelled out (numbers, sizes, options, amounts, accounts, wording; from your memory files); how to see it worked; what to report to "
                 "the user; when to stop or skip (already done this time, nothing new, the price or terms changed). Never write in "
                 "approval or confirmation steps for what the user asked this job to do: their task_allow permission covers it, and "
                 "the server alone decides when a card is needed")},
             "cron": {"type": "string", "description": "repeating: 5-field cron in local time"},
             "window_minutes": {"type": "integer", "description": "with cron: run within ± this many minutes of the time"},
             "every_minutes": {"type": "integer", "description": "repeating every N minutes (at least 5); run_at then is the first run"},
             "run_at": {"type": "string", "description": "one time: local date-time"},
             "timezone": {"type": "string", "description": "IANA zone; default the user's"},
             "starts_on": {"type": "string", "description": "with cron: first local day (YYYY-MM-DD)"},
             "ends_on": {"type": "string", "description": "repeating: last local day (YYYY-MM-DD); '' clears it"},
             "goal_id": {"type": "integer", "description": "the goal this job serves"}}
    bare = {k: {kk: vv for kk, vv in v.items() if kk != "description"} for k, v in props.items()}   # schedule_update: described once
    return [
        Tool("schedule_create", "Save a job that runs on its own later or on a repeat; the user approves it. Give exactly one of cron, "
             "every_minutes or run_at (run_at may start every_minutes). To wait for a condition that needs a sign-in or browsing, poll "
             "with every_minutes as often as the request needs (5 for something that sells out fast). Then ask once with task_allow for "
             "what its runs will do. Tell the user the time in words, never as cron. When the user has not said when or how often "
             "it repeats, never pick it yourself: ask first in your one setup question, with quick_replies kind weekdays_time and "
             "1-2 sensible suggestions, and create it after they answer.",
             {"type": "object", "properties": props, "required": ["name", "prompt"]}, create, requires_approval=True,
             validate=validate_create, describe=describe_create, approval_extra=extra),
        Tool("schedule_list", "List the scheduled jobs with their times, state and last result.",
             {"type": "object", "properties": {"goal_id": {"type": "integer", "description": "only this goal's"}}}, list_, read_only=True),
        Tool("schedule_update", "Change a scheduled job (fields as in schedule_create) or turn it on/off (on also resumes a paused one). The "
             "user approves changes; turning one off needs no approval.",
             {"type": "object", "properties": {"id": {"type": "integer"}, **bare, "enabled": {"type": "boolean"}}, "required": ["id"]},
             update, requires_approval=True, validate=validate_update, describe=describe_update, auto_approve=only_pausing,
             approval_extra=extra),
        Tool("schedule_delete", "Delete a scheduled job (restorable in the app for 30 days); the user approves it.",
             {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}, delete, requires_approval=True,
             validate=validate_id, describe=describe_delete),
        Tool("schedule_pause", "Pause a scheduled job that something blocks for good (not a one-off failure); the user is told your reason. "
             "In a scheduled run, id defaults to that job.",
             {"type": "object", "properties": {"id": {"type": "integer"}, "reason": {"type": "string", "description":
                                                "what blocks it and what the user needs to do, in the user's language"}},
              "required": ["reason"]}, pause, validate=validate_pause),
        Tool("schedule_retry", "Only in a scheduled run that a temporary block stops (the site is under maintenance, down or sold out right "
             "now): run this same job again later, with the same permissions. Never make a new schedule for that.",
             {"type": "object", "properties": {
                 "at": {"type": "string", "description": "local date-time it should work again (the site's own reopening time when it shows "
                                                         "one); 5 minutes to 24 hours from now, before the job's next regular run"},
                 "why": {"type": "string", "description": "what blocks it, in the user's language (at most 160 characters)"}},
              "required": ["at", "why"]}, retry),
        Tool("schedule_runs", "The run history of a scheduled job: start, outcome and result line of each run.",
             {"type": "object", "properties": {"id": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["id"]}, runs,
             read_only=True),
    ]


# ---------------------------------------------------------------------------------------------------------------- API
class PauseIn(BaseModel):
    reason: str = Field(default="", max_length=300)


def schedule_routes(app: FastAPI, schedules: Schedules, scheduler: Scheduler, activity: Any, auth: Callable[..., Any],
                    grants: Any = None) -> None:
    """The schedule endpoints added with archive, pause, run history and per-schedule approval rules (contracts next to serialize()
    in server.py). Registered before /api/schedules/{id} so that /api/schedules/archive is not read as an id."""

    def running(s: dict[str, Any]) -> dict[str, Any]:
        return {**s, "running": scheduler.busy(s["id"])}

    def need(sid: int) -> dict[str, Any]:
        s = schedules.get(sid)
        if s is None:
            raise HTTPException(404, "no such schedule")
        return s

    @app.get("/api/schedules/archive", dependencies=[Depends(auth)])
    async def archived_schedules() -> dict[str, Any]:
        return {"items": schedules.archived(), "keep_days": ARCHIVE_DAYS}

    @app.delete("/api/schedules/archive/{sid}", dependencies=[Depends(auth)])
    async def erase_archived_schedule(sid: int) -> dict[str, bool]:
        """삭제 on an archived schedule: gone for good (it cannot be restored any more)."""
        if not schedules.erase(sid, archived_only=True):
            raise HTTPException(404, "no such deleted schedule")
        return {"ok": True}

    @app.post("/api/schedules/{sid}/restore", dependencies=[Depends(auth)])
    async def restore_schedule(sid: int) -> dict[str, Any]:
        try:
            s = schedules.restore(sid)
        except ScheduleError as e:
            raise HTTPException(422, str(e)) from None
        if s is None:
            raise HTTPException(404, "no such deleted schedule (or it was deleted more than 30 days ago)")
        scheduler.poke()
        return running(s)

    @app.post("/api/schedules/{sid}/pause", dependencies=[Depends(auth)])
    async def pause_schedule(sid: int, body: PauseIn | None = None) -> dict[str, Any]:
        need(sid)
        return running(schedules.pause(sid, (body.reason if body else "") or t("You paused it for now"), by="user") or {})

    @app.post("/api/schedules/{sid}/resume", dependencies=[Depends(auth)])
    async def resume_schedule(sid: int) -> dict[str, Any]:
        need(sid)
        try:
            s = schedules.update(sid, enabled=True)
        except ScheduleError as e:
            raise HTTPException(422, str(e)) from None
        scheduler.poke()
        return running(s or {})

    @app.get("/api/schedules/{sid}/runs", dependencies=[Depends(auth)])
    async def schedule_runs(sid: int, limit: int = 20) -> dict[str, Any]:
        if schedules._row(sid, archived=True) is None:
            raise HTTPException(404, "no such schedule")
        return {"items": schedules.runs(sid, limit)}

    @app.get("/api/schedules/{sid}/rules", dependencies=[Depends(auth)])
    async def schedule_rules(sid: int) -> dict[str, Any]:
        need(sid)
        return {"items": activity.schedule_rules(sid)}

    @app.delete("/api/schedules/{sid}/rules/{rule_id}", dependencies=[Depends(auth)])
    async def delete_schedule_rule(sid: int, rule_id: int) -> dict[str, bool]:
        if not activity.delete_schedule_rule(rule_id, sid):
            raise HTTPException(404, "no such rule")
        return {"ok": True}

    @app.get("/api/schedules/{sid}/grants", dependencies=[Depends(auth)])
    async def schedule_grants(sid: int) -> dict[str, Any]:
        need(sid)
        return {"items": grants.list(sid) if grants is not None else []}

    @app.delete("/api/schedules/{sid}/grants/{grant_id}", dependencies=[Depends(auth)])
    async def revoke_schedule_grant(sid: int, grant_id: int) -> dict[str, bool]:
        if grants is None or not grants.revoke(grant_id, sid):
            raise HTTPException(404, "no such permission")
        return {"ok": True}

    @app.get("/api/goals/{goal_id}/schedules", dependencies=[Depends(auth)])
    async def goal_schedules(goal_id: int) -> dict[str, Any]:
        if not schedules.store.goal(goal_id):
            raise HTTPException(404, "no such goal")
        return {"items": [running(s) for s in schedules.list(goal_id)]}
