"""When the AI model's usage runs out: a subscription's 5-hour / weekly window is used up, an API key hits its rate limit, the credit
is gone. This is its own kind of failure, apart from "the model could not be reached" (resume.py) and from errors in what was asked.

How each provider says it (all end up as a ProviderError from the stream):
- agy (Antigravity / Gemini): agy retries a 429 by itself for a while, then the turn fails with "Antigravity model error:
  RESOURCE_EXHAUSTED ... (code 429) Individual quota reached ..." or "You have exhausted your capacity on this model. Your quota will
  reset after 2h3m10s." (no HTTP status). `agy -p /usage` lists the windows with their reset times (costs no tokens).
- Claude Code (subscription): status 429 with "You've hit your session limit · resets 3pm (Asia/Seoul)"; `claude -p /usage` too.
- API providers: HTTP 429 (Retry-After header, Gemini's "retryDelay": "30s"), 402 / "insufficient_quota" / "credit balance is too
  low" / "requires more credits" when the credit is gone.

A prepaid balance that ran out (is_balance: HTTP 402, or the provider's words for it) is not a timed limit: nothing resets it but a
top-up. The owner is told plainly that the provider's balance ran out and needs topping up, with no reset time (none is estimated), and
nothing is retried on a timer: background calls stay held until a call answers again (the owner's next chat message after the top-up
clears it), and work that was waiting for the model stops with the same words instead of waiting.

Busy is not used up (2026-11-17, busy-429). A router or an API answers HTTP 429 when its upstream is crowded, often with no reset time;
that is "busy", not a used-up window. Decided by structure only (is_limit_shape + the reset time, no words): a limit that is not a
balance, with no reset time or a reset within BUSY_MAX_WAIT, is busy. Busy calls that gave nothing yet are retried (BUSY_WAITS, or the
provider's Retry-After) up to BUSY_RETRIES, for every caller including chat; still busy, the provider is held only BUSY_HOLD for
background calls, and the 4th busy hold in a row takes the used-up ladder. A used-up window, a balance, a CLI's own reset, or a reset
more than BUSY_MAX_WAIT away keeps the used-up words and holds.

What this module does with it (QuotaGuard, wired into the one stream function every model call goes through, usage.metered):
- a limit error becomes QuotaExhausted, with the reset time when the error says it or the CLI's /usage windows can tell; without
  one the provider is held for a capped backoff (BACKOFF);
- until then, calls that are not the user's own chat do not reach the provider at all: they fail at once with QuotaExhausted (no
  retry storm, nothing spent); a call that answers clears the hold early;
- while a measured window is nearly used up (LOW_AT), the jobs nobody waits for (DEFER_KINDS) step aside so chat keeps what is left.
What each job does with QuotaExhausted is decided where the job lives (server.py, workers.py, proactive.py ...): quiet jobs skip,
important ones wait for the reset and go on; nothing is lost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import i18n
from .i18n import N_, t
from .providers.types import CODES_BALANCE, CODES_CONTEXT, CODES_LIMIT, ProviderError, QuotaExhausted

log = logging.getLogger(__name__)

LIMIT_CODE = "model_limit"              # the error event's code in a reply (the apps show its message)
LOW_AT = 90.0                           # a window this full (percent used) makes the background jobs step aside
RESET_MARGIN = 30.0                     # seconds after the reset before the first call
BACKOFF = (600.0, 1800.0, 3600.0, 3 * 3600.0)   # no reset time known: hold this long after each hit in a row (the last repeats)
BUSY_MAX_WAIT = 60.0                    # a limit (429 / its code) whose reset is this near, or unknown, is BUSY, not used up
BUSY_RETRIES = 3                        # a busy call that gave nothing yet is tried again this many times (any caller, chat too)
BUSY_WAITS = (2.0, 5.0, 15.0)           # seconds before each retry when the provider gave no Retry-After
BUSY_HOLD = 120.0                       # a busy hold: background calls wait this long, chat is never held
BUSY_LADDER_AFTER = 3                   # busy holds in a row; the next one takes the used-up BACKOFF ladder (it may really be used up)
MAX_HOLD = 8 * 86400.0                  # never hold longer than this (a weekly window and a day)
DEFER_RECHECK = 1800.0                  # a background job that stepped aside looks again after at most this long
WINDOWS_TTL = 600.0                     # seconds the /usage windows are reused
CHAT_KINDS = frozenset({"chat"})        # the user's own replies: always tried
# the jobs nobody waits for: they step aside while the usage is nearly gone (a follow-up then goes out in its plain template)
DEFER_KINDS = frozenset({"reflection", "memory", "journal", "proactive", "compaction", "followup", "checklist", "situation"})
META = "model_limit:"

LINE_AT = N_("The AI model's usage limit is used up. You can use it again {when}.")
LINE_LATER = N_("The AI model's usage limit is used up. Try again a little later.")
DELAYED_HEAD = N_("Delayed until the AI model can be used again: {title}")
DELAYED_AT = N_("This did not run on time because the AI model's usage limit is used up. It will run by itself {when}.")
DELAYED_LATER = N_("This did not run on time because the AI model's usage limit is used up. It will run by itself once the model "
                   "can be used again.")
PAUSED_AT = N_("Paused because the AI model's usage limit is used up. It goes on by itself {when}.")
PAUSED_LATER = N_("Paused because the AI model's usage limit is used up. It goes on by itself once the model can be used again.")
BUSY_LINE = N_("The AI model is busy right now, so it could not answer. Send it again in a minute.")
BUSY_DELAYED = N_("This did not run on time because the AI model was busy. It goes on by itself in a few minutes.")
BUSY_PAUSED = N_("Paused because the AI model was busy. It goes on by itself in a few minutes.")
USED_UP = N_("The AI model's usage limit is used up")
BALANCE_LINE = N_("Your {provider} balance has run out, so the AI model could not answer. Top it up on {provider}'s site, then send "
                  "your message again.")
BALANCE_STOPPED = N_("This did not run because your {provider} balance has run out. Top it up on {provider}'s site; it is not "
                     "tried again by itself.")
BALANCE_USED_UP = N_("The {provider} balance has run out")

_UNIT = {"ms": 0.001, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60,
         "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600, "d": 86400, "day": 86400, "days": 86400}
_PART = r"\d+(?:\.\d+)?\s*(?:ms|days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-z])"   # agy writes 2h3m10s
_DURATION = re.compile(r"(?:resets?\s+(?:after|in)|retry(?:ing)?\s+(?:after|in)|try again in|available again in|please wait)\s+"
                       rf"((?:{_PART}[\s,]*(?:and\s+)?)+)", re.I)
_DUR_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-z])", re.I)
_DELAY = re.compile(r'retry[_ ]?delay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)s', re.I)
_EPOCH = re.compile(r"(?:limit reached|reset)\D{0,4}\|?\s*(\d{10})\b", re.I)
_ISO = re.compile(r"resets?(?:_time|_at| at| time)?\W{0,4}(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)", re.I)
_CLOCK = re.compile(r"resets?\s+(?:at\s+)?(?:(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?"
                    r"\s*(?P<ap>[ap]\.?m\.?)?(?:\s*\((?P<tz>[A-Za-z_]+(?:/[A-Za-z_+-]+)*)\))?", re.I)
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


# ------------------------------------------------------------------ reading the error

def is_limit(e: BaseException) -> bool:
    """Whether a model call failed because the usage limit (or the rate limit, or the credit) is used up, by what the provider said in
    structure: HTTP 429 or 402, or its own error code (providers/types.py CODES_LIMIT). A failure with neither (a CLI's plain text) is
    judged by `QuotaGuard.classify` from the CLI's own usage windows; no word of the message is read."""
    if isinstance(e, QuotaExhausted):
        return True
    if not isinstance(e, ProviderError):
        return False
    status = getattr(e, "status", None)
    if status in (401, 403, 404, 413):
        return False
    code = getattr(e, "code", "")
    if code in CODES_CONTEXT:
        return False
    return status in (402, 429) or code in CODES_LIMIT


def is_balance(e: BaseException) -> bool:
    """Whether a model call failed because the provider's prepaid balance ran out: HTTP 402, or its own error code (CODES_BALANCE)."""
    if isinstance(e, QuotaExhausted):
        return bool(getattr(e, "balance", False))
    if not isinstance(e, ProviderError):
        return False
    status = getattr(e, "status", None)
    if status in (401, 403, 404, 413):
        return False
    return status == 402 or getattr(e, "code", "") in CODES_BALANCE


def is_limit_shape(e: BaseException) -> bool:
    """A limit that is a rate limit by its shape: HTTP 429 or the provider's rate/quota code, and not a balance that ran out. Only the
    "busy or used up" decision (is_busy_error, hit) reads it; the reset time decides the rest."""
    return is_limit(e) and not is_balance(e) and not isinstance(e, QuotaExhausted)


def is_opaque(e: BaseException) -> bool:
    """A provider failure that says nothing in structure (no HTTP status, no error code): a CLI's text."""
    return isinstance(e, ProviderError) and not isinstance(e, QuotaExhausted) and getattr(e, "status", None) is None and not getattr(e, "code", "")


def provider_name(provider: str) -> str:
    """The provider as the owner knows it ("OpenRouter")."""
    try:
        from .providers.registry import get_provider
        return get_provider(provider).display_name or provider
    except Exception:  # noqa: BLE001 - a provider of the owner's own
        return provider or "AI"


def limit_of(e: BaseException | None) -> QuotaExhausted | None:
    """The QuotaExhausted in `e` or in what caused it, if any."""
    seen = 0
    while e is not None and seen < 5:
        if isinstance(e, QuotaExhausted):
            return e
        e = e.__cause__ or e.__context__
        seen += 1
    return None


def _zone(name: str | None) -> Any:
    try:
        return ZoneInfo(name) if name else timezone.utc
    except Exception:  # noqa: BLE001
        return timezone.utc


def reset_from_error(e: BaseException, now: float, tz_name: str | None = None) -> float | None:
    """The time (unix) the error says the limit resets, if it says one."""
    ra = getattr(e, "retry_after", None)
    if isinstance(ra, (int, float)) and ra > 0:
        return now + float(ra)
    text = str(e)
    if m := _DURATION.search(text):
        secs = sum(float(n) * _UNIT[u.lower()] for n, u in _DUR_PART.findall(m.group(1)))
        if secs > 0:
            return now + secs
    if m := _DELAY.search(text):
        return now + float(m.group(1))
    if m := _EPOCH.search(text):
        v = float(m.group(1))
        if now - 3600 < v < now + MAX_HOLD:
            return v
    if m := _ISO.search(text):
        try:
            d = datetime.fromisoformat(m.group(1).replace("Z", "+00:00").replace(" ", "T"))
            d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            return d.timestamp()
        except ValueError:
            pass
    for m in _CLOCK.finditer(text):
        if not (m.group("ap") or m.group("m")):
            continue   # "resets 5" alone is not a time
        tz = _zone(m.group("tz") or tz_name)
        hour = int(m.group("h"))
        if m.group("ap"):
            if hour > 12:
                continue
            hour = hour % 12 + (12 if m.group("ap").lower().startswith("p") else 0)
        if hour > 23:
            continue
        local = datetime.fromtimestamp(now, tz)
        try:
            if m.group("mon") and m.group("mon").lower() in _MONTHS:
                d = local.replace(month=_MONTHS[m.group("mon").lower()], day=int(m.group("day")), hour=hour, minute=int(m.group("m") or 0),
                                  second=0, microsecond=0)
                if d.timestamp() < now - 2 * 86400:
                    d = d.replace(year=d.year + 1)
            else:
                d = local.replace(hour=hour, minute=int(m.group("m") or 0), second=0, microsecond=0)
                if d.timestamp() <= now:
                    d += timedelta(days=1)
        except ValueError:
            continue
        return d.timestamp()
    return None


# ------------------------------------------------------------------ the CLI's /usage windows

def _iso_ts(v: Any) -> float | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def relevant(windows: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """The windows that count for `model`: those whose group names it ("Gemini" for gemini-3-pro), else all of them."""
    m = (model or "").lower()
    hits = [w for w in windows if any(len(tok) >= 3 and tok in m for tok in re.split(r"[^a-z0-9]+", str(w.get("group") or "").lower()))]
    return hits or list(windows)


def full_until(windows: list[dict[str, Any]], model: str, now: float) -> float | None:
    """When every used-up window that counts for `model` has reset again; None when none is used up (or none says when)."""
    ends = [ts for w in relevant(windows, model) if float(w.get("used_percent") or 0) >= 99.5
            and (ts := _iso_ts(w.get("resets_at"))) is not None and ts > now]
    return max(ends) if ends else None


def low_until(windows: list[dict[str, Any]], model: str, now: float, low_at: float = LOW_AT) -> float | None:
    """The first reset of a nearly used-up window that counts for `model`; None when there is room."""
    ends = [ts for w in relevant(windows, model) if float(w.get("used_percent") or 0) >= low_at
            and (ts := _iso_ts(w.get("resets_at"))) is not None and ts > now]
    return min(ends) if ends else None


# ------------------------------------------------------------------ words for the user

_CLOCK_STYLE = {"en": "after", "hi": "after", "ar": "after", "ko": "before", "zh-Hans": "before", "zh-Hant": "before"}


def clock_text(d: datetime, lang: str | None = None) -> str:
    """3:10 PM / 오후 3:10 / 15:10, by the language's habit."""
    style = _CLOCK_STYLE.get(lang or i18n.current())
    if style is None:
        return f"{d.hour:02d}:{d.minute:02d}"
    h12 = d.hour % 12 or 12
    mark = t("PM", lang) if d.hour >= 12 else t("AM", lang)
    return f"{mark} {h12}:{d.minute:02d}" if style == "before" else f"{h12}:{d.minute:02d} {mark}"


def when_text(ts: float, tz_name: str | None, now: float | None = None, lang: str | None = None) -> str:
    """'at 3:10 PM' / 'tomorrow at 3:10 PM' / 'on 11/12 at 3:10 PM' in the owner's time zone (rounded up to the minute)."""
    tz = _zone(tz_name)
    d = datetime.fromtimestamp(ts + 59, tz).replace(second=0, microsecond=0)
    today = datetime.fromtimestamp(time.time() if now is None else now, tz).date()
    clock = clock_text(d, lang)
    if d.date() <= today:
        return t("at {time}", lang, time=clock)
    if d.date() == today + timedelta(days=1):
        return t("tomorrow at {time}", lang, time=clock)
    return t("on {month}/{day} at {time}", lang, month=d.month, day=d.day, time=clock)


def chat_line(e: QuotaExhausted, tz_name: str | None, now: float | None = None) -> str:
    """The reply's line: the limit is used up, and when the model can be used again when that is known; a balance that ran out says
    so and asks for a top-up (never a time)."""
    if getattr(e, "balance", False):
        return t(BALANCE_LINE, provider=provider_name(e.provider))
    if getattr(e, "busy", False):   # the model is busy, not used up: no reset is promised
        return t(BUSY_LINE)
    if e.reset_at:
        return t(LINE_AT, when=when_text(e.reset_at, tz_name, now))
    return t(LINE_LATER)


def delayed_text(reset_at: float | None, tz_name: str | None, now: float | None = None, busy: bool = False) -> str:
    if busy:
        return t(BUSY_DELAYED)
    return t(DELAYED_AT, when=when_text(reset_at, tz_name, now)) if reset_at else t(DELAYED_LATER)


def paused_text(reset_at: float | None, tz_name: str | None, now: float | None = None, busy: bool = False) -> str:
    if busy:
        return t(BUSY_PAUSED)
    return t(PAUSED_AT, when=when_text(reset_at, tz_name, now)) if reset_at else t(PAUSED_LATER)


# ------------------------------------------------------------------ the hold per provider

WindowsFn = Callable[[str], Awaitable[list[dict[str, Any]]]]


class BusyExhausted(QuotaExhausted):
    """The model is busy (a rate limit with no reset coming soon), not used up. Its reply says so, and background work waits a few
    minutes (BUSY_HOLD) instead of the used-up ladder."""
    busy = True


class QuotaGuard:
    """Which providers are out of usage, until when (kept in the store, so a restart keeps the hold), and whether the background jobs
    should step aside for chat. `windows(provider)` returns the CLI's /usage windows (raises or returns [] when it has none).
    `pause(seconds)` waits before a busy retry (tests patch it on the class)."""

    def __init__(self, store: Any, windows: WindowsFn | None = None, *, clock: Callable[[], float] = time.time,
                 tz: Callable[[], str | None] = lambda: None):
        self.store, self.windows_fn, self.clock, self.tz = store, windows, clock, tz
        self._windows: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._defer: dict[str, float] = {}

    async def pause(self, seconds: float) -> None:
        """Waits before a busy retry (a method, so a test can make it instant)."""
        await asyncio.sleep(seconds)

    # state ---------------------------------------------------------
    def state(self, provider: str) -> dict[str, Any] | None:
        try:
            v = json.loads(self.store.meta(META + provider) or "null")
        except ValueError:
            return None
        return v if isinstance(v, dict) and isinstance(v.get("until"), (int, float)) else None

    def held(self) -> dict[str, dict[str, Any]]:
        """Every provider held now: {provider: state}."""
        now = self.clock()
        out = {}
        for r in self.store._all("SELECT key FROM meta WHERE key LIKE ?", (META + "%",)):
            p = str(r["key"])[len(META):]
            if (st := self.state(p)) and st["until"] > now:
                out[p] = st
        return out

    def until(self, provider: str) -> float | None:
        st = self.state(provider)
        return float(st["until"]) if st and st["until"] > self.clock() else None

    def _exc(self, provider: str, st: dict[str, Any], *, deferred: bool = False) -> QuotaExhausted:
        if st.get("balance"):   # no reset is coming: no time in the words, only a top-up helps
            return QuotaExhausted(f"{provider}: the provider's balance has run out (top-up needed)" + (f": {st['why']}" if st.get("why") else ""),
                                  provider=provider, reset_at=None, until=float(st["until"]), balance=True)
        reset = float(st["until"]) if st.get("known") else None
        when = datetime.fromtimestamp(float(st["until"]), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        what = ("steps aside for chat: the usage is nearly used up" if deferred else
                "the AI model is busy" if st.get("busy") else "the AI model's usage limit is used up")
        cls = BusyExhausted if st.get("busy") else QuotaExhausted
        return cls(f"{provider}: {what} (until {when}{'' if st.get('known') else ', estimated'})"
                   + (f": {st['why']}" if st.get("why") else ""), provider=provider, reset_at=reset, until=float(st["until"]),
                   deferred=deferred)

    def balance_out(self, provider: str) -> bool:
        """The provider is held because its prepaid balance ran out (until a call answers again)."""
        st = self.state(provider)
        return bool(st and st.get("balance") and st["until"] > self.clock())

    def busy_now(self, provider: str) -> bool:
        """The provider is held as busy (not used up) right now: the job's words say "busy" and "a few minutes"."""
        st = self.state(provider)
        return bool(st and st.get("busy") and st["until"] > self.clock())

    async def busy_wait(self, provider: str, model: str, e: BaseException, attempt: int) -> float | None:
        """Seconds to wait before retrying a call that failed busy (a rate limit whose reset is near or unknown), or None when it is not
        busy or the retries are used up. The provider's Retry-After (or the reset in the error) is waited for; else BUSY_WAITS."""
        if attempt >= BUSY_RETRIES or not is_limit_shape(e):
            return None
        now = self.clock()
        reset = reset_from_error(e, now, self.tz())
        if reset is None:
            reset = full_until(await self.windows(provider), model, now)
        if reset is not None and reset - now > BUSY_MAX_WAIT:
            return None   # used up: the reset is far, no retry
        return max(0.0, reset - now) if reset is not None else BUSY_WAITS[min(attempt, len(BUSY_WAITS) - 1)]

    def blocked(self, provider: str, kind: str = "") -> QuotaExhausted | None:
        """Why a call of `kind` to `provider` should not be made now, or None."""
        now = self.clock()
        st = self.state(provider)
        if st and st["until"] > now:
            return self._exc(provider, st)
        if kind in DEFER_KINDS and self._defer.get(provider, 0) > now:
            return self._exc(provider, {"until": self._defer[provider], "known": True}, deferred=True)
        return None

    def clear(self, provider: str) -> None:
        """A call answered: the provider is usable again."""
        self._defer.pop(provider, None)
        if self.store.meta(META + provider):
            self.store.set_meta(META + provider, "")
            log.info("model usage of %s is back", provider)

    # windows -------------------------------------------------------
    def remember(self, provider: str, windows: list[dict[str, Any]]) -> None:
        """Windows someone else just read (GET /api/usage): reused for WINDOWS_TTL."""
        self._windows[provider] = (time.monotonic(), list(windows or []))

    async def windows(self, provider: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        hit = self._windows.get(provider)
        if hit and not refresh and time.monotonic() - hit[0] < WINDOWS_TTL:
            return hit[1]
        got: list[dict[str, Any]] = []
        if self.windows_fn is not None:
            try:
                got = list(await self.windows_fn(provider) or [])
            except Exception as e:  # noqa: BLE001 - no windows: the error's own words or the backoff decide
                log.debug("usage windows of %s: %s", provider, e)
        self._windows[provider] = (time.monotonic(), got)
        return got

    async def low(self, provider: str, model: str) -> float | None:
        """The reset time of a nearly used-up window (so background jobs step aside until then), or None."""
        return low_until(await self.windows(provider), model, self.clock())

    # the gate --------------------------------------------------------
    async def before(self, provider: str, model: str, kind: str) -> None:
        """Called before every model call. Raises QuotaExhausted for a call that must not go out now (never for chat)."""
        if kind in CHAT_KINDS:
            return
        if (b := self.blocked(provider, kind)) is not None:
            raise b
        if kind in DEFER_KINDS and self.windows_fn is not None:
            now = self.clock()
            if (u := await self.low(provider, model)) is not None:
                self._defer[provider] = min(u, now + DEFER_RECHECK)
                log.info("background %s steps aside on %s: the usage is nearly used up", kind, provider)
                raise self._exc(provider, {"until": self._defer[provider], "known": True}, deferred=True)

    async def classify(self, provider: str, model: str, e: BaseException) -> QuotaExhausted | None:
        """A failed call: QuotaExhausted (and the provider held) when the usage ran out, else None."""
        if isinstance(e, QuotaExhausted):
            return e
        if not is_limit(e):
            if not is_opaque(e):
                return None
            # no status and no code (a CLI's plain text): it is a limit when the CLI's own usage windows say this model's is used up
            if full_until(await self.windows(provider, refresh=True), model, self.clock()) is None:
                return None
        return await self.hit(provider, model, e)

    async def hit(self, provider: str, model: str, e: BaseException) -> QuotaExhausted:
        now = self.clock()
        if is_balance(e):   # not a window that resets: held until a call answers (after a top-up), no time estimated
            st = {"until": now + MAX_HOLD, "known": False, "balance": True, "hits": 1, "since": now, "why": str(e)[:200]}
            self.store.set_meta(META + provider, json.dumps(st, ensure_ascii=False))
            log.warning("the balance of %s has run out: %s", provider, str(e)[:200])
            return self._exc(provider, st)
        reset = reset_from_error(e, now, self.tz())
        if reset is None:
            reset = full_until(await self.windows(provider, refresh=True), model, now)
        prev = self.state(provider) or {}
        # busy (a rate limit whose reset is near or unknown) is held for BUSY_HOLD; the 4th busy hold in a row is taken as possibly used up
        busy = is_limit_shape(e) and (reset is None or reset - now <= BUSY_MAX_WAIT)
        run = int(prev.get("busy_run") or 0) + 1 if busy else 0
        if busy and run <= BUSY_LADDER_AFTER:
            st = {"until": now + BUSY_HOLD, "known": False, "busy": True, "busy_run": run, "hits": int(prev.get("hits") or 0),
                  "since": float(prev.get("since") or now), "why": str(e)[:200]}
            self.store.set_meta(META + provider, json.dumps(st, ensure_ascii=False))
            log.warning("model of %s is busy: held %.0f s for background work: %s", provider, BUSY_HOLD,
                        str(e)[:200])
            return self._exc(provider, st)
        hits = int(prev.get("hits") or 0) + 1
        known = reset is not None and reset > now
        until = min((reset + RESET_MARGIN) if known else now + BACKOFF[min(hits, len(BACKOFF)) - 1], now + MAX_HOLD)
        st = {"until": until, "known": known, "hits": hits, "since": float(prev.get("since") or now), "why": str(e)[:200],
              "busy_run": run}
        self.store.set_meta(META + provider, json.dumps(st, ensure_ascii=False))
        log.warning("model usage of %s is used up until %s (%s): %s", provider,
                    datetime.fromtimestamp(until, timezone.utc).isoformat(timespec="minutes"), "known" if known else "estimated",
                    str(e)[:200])
        return self._exc(provider, st)
