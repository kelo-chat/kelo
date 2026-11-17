"""Back off when a service says slow down. When a connector's own service answers "too many requests" (HTTP 429, or a 403/503 that marks
a rate limit by its structure: a Retry-After header, Google's error.status RESOURCE_EXHAUSTED, or a reason rateLimitExceeded /
userRateLimitExceeded), Kelo stops calling that connector until the service's own time is over: Retry-After if it sent one, else 30 s,
doubling on each repeat up to 15 min, back to 30 s after a call that worked. While a connector cools down, every tool call to it (any
turn: chat, schedules, background jobs) is answered at once, without a request ("<name> asked Kelo to slow down; try again after HH:MM.").
A plain 403 (a refusal, no rate limit) is not a slow-down. Protocol values only, no word lists.

How it is wired: agent/loop.py names the connector a tool call acts on (`connector_now`, from the gate's verdict) while the tool runs;
netguard.fetch / fetch_local pass every reply to `observe`, which records or clears the cooldown of that connector; permissions.Permissions
asks `blocked(cid)` before a call (Gate.check). Kept in memory (a restart starts without cooldowns).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

FIRST_DELAY = 30.0       # seconds, without a Retry-After
LAST_DELAY = 15 * 60.0   # the doubling stops here
RATE_REASONS = frozenset({"ratelimitexceeded", "userratelimitexceeded", "rate_limit_exceeded"})   # Google's protocol values, lower-cased
RATE_STATUSES = frozenset({"resource_exhausted"})
MAX_WAIT = 24 * 3600.0   # a Retry-After beyond a day is a broken header, not a plan

# the connector the tool call running now acts on ("" = none): set by agent/loop.py around the tool's handler
connector_now: ContextVar[str] = ContextVar("connector_now", default="")


def retry_after(headers: dict[str, str] | None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or an HTTP date); None when absent or unreadable."""
    raw = str((headers or {}).get("retry-after") or (headers or {}).get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        secs = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        secs = (when - datetime.now(timezone.utc)).total_seconds()
    if math.isnan(secs) or secs < 0:
        return None
    return min(secs, MAX_WAIT)


def _google_marks_rate_limit(body: bytes) -> bool:
    try:
        err = (json.loads(body.decode("utf-8", "replace")) or {}).get("error")
    except (ValueError, AttributeError):
        return False
    if not isinstance(err, dict):
        return False
    if str(err.get("status") or "").lower() in RATE_STATUSES:
        return True
    rows = [r for r in (err.get("errors") or []) if isinstance(r, dict)] + [r for r in (err.get("details") or []) if isinstance(r, dict)]
    return any(str(r.get("reason") or "").lower() in RATE_REASONS for r in rows)


def is_rate_limit(status: int, headers: dict[str, str] | None, body: bytes = b"") -> bool:
    """429 always; 403 and 503 only when the reply marks a rate limit by its structure."""
    if status == 429:
        return True
    if status not in (403, 503):
        return False
    return retry_after(headers) is not None or _google_marks_rate_limit(body)


class Cooldowns:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._until: dict[str, float] = {}
        self._delay: dict[str, float] = {}

    def note_rate_limit(self, cid: str, headers: dict[str, str] | None = None) -> float:
        """The connector's service said slow down: cool down until its own time (or the next step of 30 s doubling); returns that time."""
        now = self.clock()
        asked = retry_after(headers)
        if asked is None and self._until.get(cid, 0.0) > now:   # a call already in flight when the cooldown began: not a new step
            return self._until[cid]
        if asked is None:
            asked = FIRST_DELAY if cid not in self._delay else min(self._delay[cid] * 2, LAST_DELAY)
            self._delay[cid] = asked
        until = max(self._until.get(cid, 0.0), now + asked)
        self._until[cid] = until
        return until

    def note_success(self, cid: str) -> None:
        self._delay.pop(cid, None)
        if self._until.get(cid, 0.0) <= self.clock():
            self._until.pop(cid, None)

    def blocked(self, cid: str) -> float | None:
        """The time (epoch seconds) the connector may be called again, None when it is not cooling down."""
        until = self._until.get(cid)
        if until is None:
            return None
        if until <= self.clock():
            self._until.pop(cid, None)
            return None
        return until

    def clear(self, cid: str | None = None) -> None:
        if cid is None:
            self._until.clear()
            self._delay.clear()
        else:
            self._until.pop(cid, None)
            self._delay.pop(cid, None)


cooldowns = Cooldowns()   # the server's shared one


def observe(status: int, headers: dict[str, str] | None, body: bytes = b"") -> None:
    """A reply to a request of the connector the running tool call acts on: record a slow-down, or clear the doubling after a success."""
    cid = connector_now.get()
    if not cid:
        return
    if is_rate_limit(status, headers, body):
        cooldowns.note_rate_limit(cid, headers)
    elif 200 <= status < 300:
        cooldowns.note_success(cid)
