"""Proactive messages: now and then the agent tells the user something without being asked — a few times a day at most, only in waking
hours, and only what passes the user's own rules in NOTIFY_PREFS.md (sections "tell me" / "don't tell me" / "hours" / "style", with the
headings written by home.py; the agent keeps that file when the user says "don't tell me things like this").

The urgency levels 보통 (regular) / 오늘 (today) / 지금 (now) are fixed data values: they are stored, returned by the API and written by
the model, so they stay as they are. The "don't tell me" heading is "Do not tell me" in new files and "알리지 마" in older ones; both are read.

One pass (an internal job; every `every_minutes`, default 120, inside wake_start..wake_end of the profile's time zone, default
07:00–22:00; the first pass of the day runs at wake_start):
1. Candidates are gathered in code from what is connected today: mail (every connected mailbox unless briefing_sources.py leaves it out; see
   "Mail" below), calendar events in the
   next 24 hours (when a calendar source is wired in; it is read big_events.AHEAD_DAYS ahead in the same one read per pass), big
   events (big_events.py: trips, flights, stays, several days; found in code) once at each lead time in "ahead_days" (default 7 days
   and 1 day before; key "ahead:<event>:<days>" in the ledger so a lead time is never told twice; the model writes the message with
   what to prepare, within the same daily limits), schedule runs and watcher hits since the last pass (already told by their own
   notifications: logged as dropped), tracked goals with a date within 3 days (a "deadline" field, or a date in the title or note), and
   dated lines under "## Commitments" in MEMORY.md within 2 days, and whatever else is in `Proactive.sources` (CandidateSource: today
   the rechecks, recheck.py, which are told by their own message through the main agent, so they are logged here as already told).
   Rechecks (config "rechecks", on by default, and "recheck_per_day", 4 research runs a day at most) run on their own tick, not in a pass.
   Mail can be switched off as a whole (config "mail_check", on by default): no mailbox is opened, so no new-mail candidates, no spam /
   promotions skim, no change alerts, no cursor moves and no mail line for the calendar cross-check; calendar, checklist and the other
   sources run as before. Turned on again, the first pass moves every mailbox cursor to "now" without candidates (meta
   "proactive:mail_resume" holds the switch-on time until that pass is through): old unread mail is never replayed. The morning
   briefing chooses its mailboxes in briefing_sources.py and is not affected.
   Mail: per mailbox EVERY unread, non-bulk message that arrived since the last successful look is a candidate (no count limit): the
   cursor per mailbox is in meta "proactive:mail:<account>" (IMAP: UIDVALIDITY + last UID + time, the UID range is read in pages of
   mail.FETCH_CHUNK; Outlook: receivedDateTime, paged; a Nest mail package has no received time, so the ledger keeps a message from
   being judged twice). A mailbox seen for the first time starts at "now" (no backlog). The cursor is saved only when the pass got
   through (every mail it read judged and logged): a mailbox that failed to read, a model that gave no usable answer or a delivery that
   failed leaves it where it was and the same mail is looked at again next pass. Mail already read, and promotions / bulk mail (Gmail's
   categories, Outlook's Other inbox, list/bulk headers: mail.is_bulk) are left out. Once per local day (config "spam_skim") the
   spam folder (its \\Junk mark, else a usual name; Outlook Junk Email) and the provider's promotions sorting (Gmail's categories,
   Outlook's Other inbox) are skimmed from their own cursors; the model drops them unless one is clearly important and misfiled (a
   bill, a booking, a security alert, a person writing personally), and a kept one says "Found in the spam folder / promotions".
   Booking, payment and travel confirmations are compared by the model with the calendar lines it is given (config
   "calendar_check"): a mismatch of date, time, people or place is a candidate of at least 오늘 that says what differs.
   Mail that reports a change (a flight or train changed or cancelled, a booking changed, a security alert from the real service) is
   flagged "change" by the model (config "change_alerts"): the change class, see 4.
2. Anything already in the ledger (told or dropped, not yet expired) is left out. Nothing new -> the pass ends without a model call.
3. Short entries under the "don't tell me" heading (one keyword per "- " line, at most 20 characters) drop matching items in code; the rest go to the
   user's main model in one compact request that classifies each: drop / 보통 / 오늘 / 지금 and writes a one- or two-sentence message.
   Rules: wording alone never makes something urgent; when unsure between levels the model picks 오늘. In code: 지금 needs a concrete
   time within NOW_WINDOW (the item's own, or a "due" time the model read from it), else it becomes 오늘. The model is asked for a
   reason on every item, drop too; when it gives none, a short code-side reason is stored. More than BATCH (40) items go in as several
   requests of at most BATCH (nothing is left out).
4. Per local day at most `daily_regular` (2) 보통 items and `daily_total` (3) 보통 + 오늘 items go out; 지금 items go out at once (at most
   `daily_now`, 3; over that they count as 오늘). What does not fit is dropped until the next day.
   The change class (mail flagged "change", urgency stored as 지금, log kind "change") is outside all of that: it goes out at once in the
   pass that finds it (so at most `every_minutes` after the mail arrived; waking hours only, like 지금), is not counted in
   daily_regular / daily_total / daily_now, and is not held back by the unanswered guard nor counted by it (when the guard holds, the
   pass still reads the mailboxes but only change alerts go out; the other mail it read is logged as held back). Its own generous
   limit `daily_change` (10 a day, 0..50) only stops a flood: over it, a change is an ordinary 오늘 item.
5. What one pass tells goes out as ONE message per mailbox (all mail of one mailbox together; everything that is not mail together), not
   one per item: the pass collects the accepted items (limits and the unanswered guard still count per item), folds duplicates (the same
   message text, or the same sender and subject: one line with "(2 mails)") and delivers each group once as a chat message from the
   agent, a task (source "proactive") and a notification (type "proactive", pushed). One item is told exactly as its message; several
   are one "• " line each; with more than one mailbox connected a mail group starts with the mailbox's name. Every item of a group is
   logged as sent with the group's task id. The notification title of one mail says what it is ("New mail · <sender> · <subject>",
   localized, at most 60 characters), of several "New mail · <count>"; any other group's title is the agent's name. A group whose
   delivery failed is not logged as sent and the pass fails without moving the mailbox cursors. Every decision, drop included, is
   logged with its reason (GET /api/proactive/log).

The checklist (CHECKLIST.md, checklist.py) is part of this: it runs at the same pace, only while this is on, in waking hours and
while today's daily_total has room; a run that said something counts as one 오늘 message (log source "checklist").

Off by default: the user turns it on in the app (PUT /api/proactive {"enabled": true}). All limits are settings.

Guard against talking to nobody (see "the unanswered guard" in the class): nothing runs before the owner's first chat message on this
server; after `unanswered_limit` (3) proactive messages in a row with no chat message from the owner (checklist runs that said something
count too) nothing more goes out for `pause_hours` (18); then one may go, and each further unanswered one doubles the next pause, capped
at `pause_max_days` (7): at most one per that many days. Any chat message from the owner resets all of it; opening the app does not.
Watchers, schedule/job results, approvals, a running task's questions and reminders are the owner's own requests and never held back.
Contact hours follow the owner (active_hours.py): once a day the owner's active window is learned from their chat messages and app use
(28 days, at least 7 active days; a window past midnight such as 10:00–02:00 works). When it differs from wake_start..wake_end by an
hour or more at either end it is offered as hours_suggestion (Accept / Keep current; after "keep" not again for 14 days), or applied
at once when auto_hours is on (off by default). A chat message from the owner in the last 30 minutes counts as awake now for
follow-ups (followups.py); proactive messages keep to the contact hours.
Weekdays and weekends: there are two windows, one for weekdays and one for weekends and public holidays (day_types.py: the time
zone's country). Each is learned from its own days (weekdays 7 active days, weekends 4), suggested and accepted on its
own, and set by hand on its own (hand-set hours are "manual" and are never changed by Kelo; auto_hours applies a learned window only
while that day type's hours are default or learned, and only offers it over manual ones). Until the weekend window is set or learned,
weekends follow the weekday one (weekend_source "default"). Everything that reads the hours (proactive passes, follow-ups, storage
warnings, server updates, the briefing, the apps' mascot) uses the window of the day a moment belongs to (a night past midnight stays
with the evening before: day_types.logical_date). wake_start / wake_end / hours_source / hours_suggestion stay the weekday ones, so
older apps keep working.
Every pass leaves one record in last_pass: why it ended or was skipped (reason_code, one of SKIP_REASONS, plus "failed" /
"bad_answer"; "" when it ran through), when, and the backoff level and the next allowed time. When the AI model's usage limit is used
up (model_limits.py) the pass is skipped quietly (reason_code "model_limit"): nothing is told or dropped, and the next pass looks again.

PROACTIVE API (also next to serialize() in server.py):
GET  /api/proactive        {"enabled", "ahead_days": [7, 1], "wake_start": "HH:MM", "wake_end": "HH:MM", "every_minutes", "daily_regular", "daily_total",
                            "daily_now", "daily_change", "spam_skim", "calendar_check", "change_alerts", "mail_check" (the last four: true = on, the default), "rechecks" (true; recheck.py), "recheck_per_day" (4; 0..20), "offers" (true; offers.py: the ready-to-run cards),
                            "timezone", "prefs_file": "NOTIFY_PREFS.md", "today": {"보통": n, "오늘": n, "지금": n} (change alerts not
                            counted), "today_changes": n,
                            "unanswered_limit", "pause_hours", "pause_max_days",
                            "last_pass": {"at", "status": done|nothing_new|skipped|failed, "reason", "reason_code", "candidates", "sent",
                                          "dropped", "backoff_level", "next_allowed_at"|null}|null,
                            "awake": bool (inside waking hours now),
                            "guard": {"owner_spoke", "unanswered", "unanswered_limit", "backoff_level", "pause_hours", "next_allowed_at"|null},
                            "auto_hours": bool (false), "hours_source": default|manual|learned (where wake_start..wake_end came from),
                            "hours_suggestion": {"id", "day_type", "wake_start", "wake_end", "current_start", "current_end",
                                                 "active_days", "created_at"}|null (weekdays),
                            "weekend_start", "weekend_end" (in effect: the weekday hours while weekends have none of their own),
                            "weekend_source": default (follows weekdays)|manual|learned, "weekend_suggestion": as hours_suggestion|null,
                            "day_type": weekday|weekend (now), "today_start", "today_end" (the window in effect now),
                            "holiday_country": "KR"|"" , "day_types": {"YYYY-MM-DD": weekday|weekend} from yesterday, 9 days,
                            "learned": {"weekday"|"weekend": {"wake_start", "wake_end", "active_days"}|null} (learned and different
                            from the hours in effect; computed, no model)}
PUT  /api/proactive        any of enabled, wake_start, wake_end, weekend_start, weekend_end ("" for both: weekends follow weekdays
                           again), every_minutes (30..1440), daily_regular (0..10), daily_total (0..20),
                           daily_now (0..10), daily_change (0..50), recheck_per_day (0..20), rechecks (boolean), offers (boolean), spam_skim, calendar_check, change_alerts, mail_check (booleans: the owner can switch a part
                           off; mail_check false = the 2-hourly check does not read mail at all, only calendar, checklist and the other sources;
                           turned on again it starts from "now", no old mail is alerted; the morning briefing is not affected),
                           unanswered_limit (1..10), pause_hours (1..168), pause_max_days (1..30), auto_hours,
                           ahead_days (lead times for big events: up to 4 whole days 1..14, [] = off; the agent's big_event_notice tool)
                           -> the same as GET (a changed wake_start / wake_end makes hours_source "manual"); 422 {"detail"} for a bad value
GET  /api/proactive/log?limit=50&before=<id>   {"items": [{"id", "key", "source": mail|calendar|schedule|watcher|goal|memory|checklist, "title",
                            "urgency": 보통|오늘|지금|null, "status": sent|dropped, "reason", "message", "decided_at", "expires_at",
                            "task_id"|null, "kind": ""|change}] newest first, "next_before": id|null}
POST /api/proactive/hours/{id}/accept   the suggested contact hours become the setting of its day type (source "learned") -> as GET
POST /api/proactive/hours/{id}/keep     keep the current hours; no new suggestion for 14 days -> the same as GET; 404 unknown id
POST /api/proactive/hours/learned/{weekday|weekend}/apply   that day type's learned window becomes its hours ("learned") -> as GET;
                           404 when there is none that differs
POST /api/proactive/run    one pass now (outside waking hours too) -> last_pass; 409 when it is off or a pass is running
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .active_hours import LIVE_SECONDS, RESUGGEST_DAYS, activity_days, differs as hours_differ, learn_windows
from . import big_events
from .day_types import DAY_TYPES, WEEKDAY, WEEKEND, country_for, day_type, type_at, upcoming as upcoming_days
from .home import NOTIFY_PREFS, Home
from .model_limits import limit_of
from .prompt import tone_line
from .i18n import N_, t
from .providers import ChatRequest, TextDelta
from .reflection import parse_output

log = logging.getLogger("uvicorn.error")

URGENCIES = ("보통", "오늘", "지금")
DEFAULTS: dict[str, Any] = {"enabled": False, "wake_start": "07:00", "wake_end": "22:00", "every_minutes": 120, "daily_regular": 2,
                            "daily_total": 3, "daily_now": 3, "unanswered_limit": 3, "pause_hours": 18, "pause_max_days": 7,
                            "auto_hours": False, "hours_source": "default", "weekend_start": "", "weekend_end": "",
                            "weekend_source": "default", "ahead_days": [7, 1], "daily_change": 10, "spam_skim": True,
                            "calendar_check": True, "change_alerts": True, "mail_check": True, "rechecks": True, "recheck_per_day": 4, "offers": True}
HOURS_SOURCES = ("default", "manual", "learned")   # where the contact hours (wake_start..wake_end) came from
# per day type: the config keys of its window and where it came from (weekend "" = not set: weekends follow the weekday window)
WINDOW_KEYS = {WEEKDAY: ("wake_start", "wake_end", "hours_source"), WEEKEND: ("weekend_start", "weekend_end", "weekend_source")}
SUGGESTION_KEYS = {WEEKDAY: ("hours_suggestion", "hours_kept_at"), WEEKEND: ("weekend_suggestion", "weekend_kept_at")}
LIMITS = {"every_minutes": (30, 1440), "daily_regular": (0, 10), "daily_total": (0, 20), "daily_now": (0, 10), "daily_change": (0, 50),
          "unanswered_limit": (1, 10), "pause_hours": (1, 168), "pause_max_days": (1, 30), "recheck_per_day": (0, 20)}
AHEAD_LEADS_MAX = 4           # ahead_days: at most this many lead times, each 1..big_events.AHEAD_DAYS days before
MAX_LEVEL = 32                 # the backoff level stops growing here (the pause is long capped well before)

# Why a pass did not run (or ended early): one code each, the same in last_pass.reason_code of GET /api/proactive.
SKIP_REASONS = {
    "off": N_("It is turned off"),
    "outside_waking_hours": N_("It is quiet time"),
    "already_running": N_("A pass is already running"),
    "chat_running": N_("A chat reply is running"),
    "no_owner_message": N_("Waiting for your first message on this server"),
    "waiting_for_reply": N_("Waiting for your reply to the earlier messages"),
    "no_model": N_("No model is set"),
    "nothing_new": N_("Nothing new to look at"),
    "model_limit": N_("The AI model's usage limit is used up"),
}
NOW_WINDOW = 3 * 3600          # 지금 needs something that starts or closes within this many seconds
NEVER_HEADINGS = ("Do not tell me", "알리지 마")   # NOTIFY_PREFS.md heading: English in new files (home.py), Korean in older ones
BATCH = 40                    # items per model request: a pass with more is sent in as few requests as this allows (nothing is left out)
MAIL_NOTICE_CHARS = 60        # the notification title of a mail (what it is, who, which subject)
CALENDAR_LINES = 150          # calendar lines shown to the model next to mail, for the booking cross-check
PREFS_CHARS = 2_000
SENT_KEEP = 30 * 86400         # a told item is not told again for this long
DROPPED_KEEP = 7 * 86400       # an item judged not worth telling is not looked at again for this long
KEEP_LOG = 4000
UNREAD_PER_ACCOUNT = 5        # the briefing's newest unread per mailbox (briefing.py); the proactive pass reads everything since its last look
MAIL_ACCOUNT_TIMEOUT = 45.0   # one mailbox's read for the briefing; longer is skipped this time (and shown in the settings, briefing_sources.py)
ARRIVALS_TIMEOUT = 330.0      # one mailbox's look at everything that arrived since the last pass (a long gap is read in pages)
MAIL_CURSOR = "proactive:mail:"   # + account name: where the last look ended (meta; see Proactive.mail_state)
MAIL_RESUME = "proactive:mail_resume"   # meta: when "mail_check" was turned back on, until the pass that moved every mailbox cursor to "now" is through
SKIM_KINDS = (("junk", "spam"), ("promo", "promotions"))   # the folders skimmed once a day: (mail kind, the tag the model sees)


async def unread_by_account(mail: Any, accounts: list[dict[str, Any]], limit: int = UNREAD_PER_ACCOUNT) -> list[tuple[dict[str, Any], Any]]:
    """[(account, its newest unread messages | the exception)] for every account, read at the same time, each within
    MAIL_ACCOUNT_TIMEOUT. One mailbox failing or slow never holds up or drops the others. Marketing and bulk mail is left out for every
    mailbox and provider (mail.is_bulk: List-Unsubscribe, List-Id, Precedence, Auto-Submitted; the provider's own sorting where it has
    one: Gmail's categories, Outlook's Other inbox; only the inbox is read, so a provider's own promotions folder stays out), before the
    model judges what is left."""
    async def one(acct: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        try:
            rows = await asyncio.wait_for(mail.list(acct["name"], unread=True, limit=limit, skip_bulk=True), MAIL_ACCOUNT_TIMEOUT)
            return acct, [m for m in rows if not m.get("bulk")]
        except Exception as e:  # noqa: BLE001 - reported per account by the caller
            return acct, e
    return list(await asyncio.gather(*(one(a) for a in accounts)))


HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS proactive_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', urgency TEXT,
    status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '', decided_at REAL NOT NULL,
    expires_at REAL NOT NULL, day TEXT NOT NULL, task_id INTEGER, kind TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS proactive_log_key ON proactive_log(key, expires_at);
"""

PROMPT = """You decide which of the items below deserve a short message to the user that they did not ask for. Reply with JSON only:
{"items": [{"n": 1, "urgency": "drop", "due": null, "reason": "...", "message": "", "change": false, "mismatch": false}]}
One entry per item, by its number n. urgency is one of: drop, 보통, 오늘, 지금.
- 지금: waiting a few hours would be too late: it starts, closes or must be answered within about 3 hours. Put its local time (HH:MM) in "due".
- 오늘: it matters today (something due, happening or to be answered today).
- 보통: useful to know, not tied to a time.
- drop: not worth interrupting for (newsletters, promotions, routine notices, receipts, things already handled), or the preferences say not to tell.
Wording alone never makes something urgent: words like urgent, ASAP or "deadline soon" (in any language) or many exclamation marks are not evidence. When unsure between two levels, pick 오늘. When unsure whether to tell at all, drop.
The user's preferences come first: anything under "Do not tell me" (or 알리지 마) is drop; anything under "Tell me" (or 알려 줘) is at least 보통.
An item marked "coming up" is a big event still days away (a trip, a flight, a stay, several days): tell it (보통; 오늘 when it is
tomorrow) unless the preferences say not to, and in the message say when it is and what to prepare now (bookings, documents, packing,
time off), briefly.
- Mail only. "change": true for a mail that reports a CHANGE to something the user already has: a flight or train changed, delayed or cancelled, a booking, reservation or appointment changed or cancelled, or a security alert about the user's account from the real service (new sign-in, password changed, suspicious activity). Such a mail is told at once and does not use up the daily limits, so set "change" only for a real change or alert, never for marketing, receipts, "your order shipped", newsletters or a mail that looks like phishing; set urgency 지금 for it.
- Mail only. A confirmation of a booking, payment or trip (flight, train, hotel, restaurant, appointment): when "Calendar" lines are given, compare it with the matching calendar entry. If the date, time, people or place differ, set "mismatch": true, urgency at least 오늘, and say in the message exactly what differs. No matching entry, or everything agrees: "mismatch": false.
- An item tagged "found in spam" or "found in promotions" comes from a folder that is normally junk: drop it unless it is clearly important and misfiled (a bill or payment notice, a booking or travel confirmation, a security alert from a real service, a real person writing to the user personally). Marketing stays drop. For one you keep, the message says it was found there.
message (not for drop): one or two short sentences in the user's language, from you to the user: what it is and why it matters now. No greeting, no headings, no markdown.
reason: REQUIRED for every item, drop too: one short sentence in the user's language saying what the item is and why you chose that level. Never leave it empty."""


@dataclass
class Candidate:
    key: str
    source: str                      # mail | calendar | schedule | watcher | goal | memory
    title: str
    detail: str = ""
    due: float | None = None         # a concrete time (unix seconds) it starts / closes, when known
    day: str = ""                    # its local date (YYYY-MM-DD), when known
    told: str = ""                   # already told by its own notification: the reason it is dropped without the model
    extra: dict[str, Any] = field(default_factory=dict)

    def line(self, tz: ZoneInfo) -> str:
        when = f" (at {datetime.fromtimestamp(self.due, tz).strftime('%m-%d %H:%M')})" if self.due else (f" (on {self.day})" if self.day else "")
        found = self.extra.get("found")
        tag = f"{self.source}, found in {found}" if found else self.source
        return f"[{tag}] {self.title[:160]}{when}" + (f" — {self.detail[:240]}" if self.detail else "")


class CandidateSource(Protocol):
    """Where a pass gets more candidates from: `Proactive.sources` lists them and gather() asks each one. (Mail, calendar, schedules,
    watchers, goals and commitments are gathered inline above and are not moved here.) The first user is recheck.RecheckSource."""

    def candidates(self, now: float) -> list[Candidate]: ...


# ---------------------------------------------------------------------------------------------------------------- helpers
def _minutes(hhmm: str) -> int:
    m = HHMM.match(hhmm)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def in_waking_hours(ts: float, start: str, end: str, tz: ZoneInfo) -> bool:
    """Whether local time `ts` is inside start..end (end not included; a span past midnight, e.g. 22:00–02:00, works too)."""
    d = datetime.fromtimestamp(ts, tz)
    now, a, b = d.hour * 60 + d.minute, _minutes(start), _minutes(end)
    if a == b:
        return True
    return a <= now < b if a < b else (now >= a or now < b)


def wake_started_at(ts: float, start: str, tz: ZoneInfo) -> float:
    """Today's (local) wake-start time as unix seconds (yesterday's when it is still before it)."""
    d = datetime.fromtimestamp(ts, tz)
    m = _minutes(start)
    w = d.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
    if w > d:
        w -= timedelta(days=1)
    return w.timestamp()


def section(text: str, *headings: str) -> str:
    """The body of the first "## <heading>" found in a Markdown file, trying each heading in turn ("" when none is there)."""
    for heading in headings:
        m = re.search(rf"^##\s*{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text or "", re.M | re.S)
        if m:
            return m.group(1).strip()
    return ""


def keywords(body: str) -> list[str]:
    """Short "- word" lines (≤ 20 characters) of a preferences section: matched in code."""
    out = []
    for line in body.splitlines():
        m = re.match(r"^\s*[-*•]\s+(.+?)\s*$", line)
        if m and 2 <= len(m.group(1)) <= 20:
            out.append(m.group(1).lower())
    return out


_DATE_FORMS = (
    re.compile(r"(?P<y>20\d\d)[-./](?P<m>\d{1,2})[-./](?P<d>\d{1,2})(?:[ T]+(?P<H>\d{1,2}):(?P<M>\d{2}))?"),
    re.compile(r"(?:(?P<y>20\d\d)\s*년\s*)?(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"
               r"(?:[^\d\n]{0,8}?(?P<ap>오전|오후)?\s*(?P<H>\d{1,2})\s*(?::(?P<M>\d{2})|시(?:\s*(?P<M2>\d{1,2})\s*분)?))?"),
)


def find_date(text: str, now: float, tz: ZoneInfo) -> tuple[str, float | None] | None:
    """The first date in `text` -> (YYYY-MM-DD, unix seconds when a time is given else None). A date without a year is this year's, or
    next year's when that date has already passed by more than a month."""
    today = datetime.fromtimestamp(now, tz)
    for rx in _DATE_FORMS:
        m = rx.search(text or "")
        if not m:
            continue
        g = m.groupdict()
        try:
            y = int(g["y"]) if g.get("y") else today.year
            d = datetime(y, int(g["m"]), int(g["d"]), tzinfo=tz)
            if not g.get("y") and d < today - timedelta(days=31):
                d = d.replace(year=y + 1)
        except ValueError:
            continue
        due = None
        if g.get("H"):
            h = int(g["H"])
            if g.get("ap") == "오후" and h < 12:
                h += 12
            mi = int(g.get("M") or g.get("M2") or 0)
            if h < 24 and mi < 60:
                due = d.replace(hour=h, minute=mi).timestamp()
        return d.strftime("%Y-%m-%d"), due
    return None


def clean_leads(v: Any) -> list[int]:
    """ahead_days as given (a list of whole days, or one number) -> distinct days 1..AHEAD_DAYS, largest first; [] turns it off."""
    raw = v if isinstance(v, (list, tuple)) else [v]
    out: set[int] = set()
    for x in raw:
        try:
            n = int(x)
        except (TypeError, ValueError):
            raise ValueError("ahead_days must be whole numbers of days") from None
        if not 1 <= n <= big_events.AHEAD_DAYS:
            raise ValueError(f"ahead_days must be between 1 and {big_events.AHEAD_DAYS} days")
        out.add(n)
    if len(out) > AHEAD_LEADS_MAX:
        raise ValueError(f"ahead_days takes at most {AHEAD_LEADS_MAX} lead times")
    return sorted(out, reverse=True)


def _day(ts: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------------------------------------------- the pass
DeliverFn = Callable[[dict[str, Any]], Awaitable[int | None]]   # one message to the chat + notification; returns its task id
CalendarFn = Callable[[], Awaitable[list[dict[str, Any]]]]       # events [{"id", "title", "start": unix, "where"?}] in the next day


class Proactive:
    def __init__(self, store: Any, home: Home | None, *, stream_fn: Callable[..., AsyncIterator[Any]],
                 model: Callable[[], tuple[str, str] | None], key_args: Callable[[str], dict[str, Any]] = lambda _p: {},
                 deliver: DeliverFn | None = None, mail: Any = None, schedules: Any = None, watchers: Any = None,
                 calendar: CalendarFn | None = None, busy: Callable[[], bool] = lambda: False, clock: Callable[[], float] = time.time,
                 owner_marks: tuple[str, ...] = (), tone: Callable[[], str] | None = None):
        self.store, self.home, self.stream_fn, self.model, self.key_args = store, home, stream_fn, model, key_args
        self.tone = tone or (lambda: store.profile().get("tone", ""))   # how Kelo talks to the user (prompt.tone_line)
        self.deliver, self.mail, self.schedules, self.watchers, self.calendar = deliver, mail, schedules, watchers, calendar
        self.busy, self.clock = busy, clock
        self.owner_marks = tuple(owner_marks)   # leads of hidden 'user' rows that are not the owner's words (job handoffs and the like)
        self.lock = asyncio.Lock()
        if home is not None:
            home.ensure_side_files()
        with store._lock:
            store._db.executescript(SCHEMA)
            if "kind" not in {r[1] for r in store._db.execute("PRAGMA table_info(proactive_log)")}:   # a log from before change alerts
                store._db.execute("ALTER TABLE proactive_log ADD COLUMN kind TEXT NOT NULL DEFAULT ''")
            store._db.commit()
        self.sources: list[CandidateSource] = []           # more candidate sources, asked by gather() (see CandidateSource)
        self._events: list[dict[str, Any]] = []            # the calendar events read in this pass (the booking cross-check)
        self._cursors: dict[str, dict[str, Any]] = {}      # mailbox -> where this pass's look ended: saved when the pass is through
        self._mailbox_count = 0                            # mailboxes this pass looked at (a mail message names its mailbox when > 1)
        self._mailbox_names: dict[str, str] = {}           # mailbox -> the name shown in a message

    # ------------------------------------------------------------------ settings
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.store.profile().get("timezone") or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def config(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.store.meta("proactive:config") or "{}")
        except ValueError:
            raw = {}
        return {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}

    def update(self, **changes: Any) -> dict[str, Any]:
        cfg = self.config()
        for k, v in changes.items():
            if v is None or k not in DEFAULTS:
                continue
            if k in ("enabled", "auto_hours", "spam_skim", "calendar_check", "change_alerts", "mail_check", "rechecks", "offers"):
                if k == "mail_check" and bool(v) and not cfg[k]:   # mail is back on: the first pass starts every mailbox at "now" (no backlog)
                    self.store.set_meta(MAIL_RESUME, str(self.clock()))
                elif k == "mail_check" and not bool(v):
                    self.store.set_meta(MAIL_RESUME, "")
                cfg[k] = bool(v)
            elif k == "ahead_days":
                cfg[k] = clean_leads(v)
            elif k in ("hours_source", "weekend_source"):
                continue   # set by the server only: manual when the owner changes the hours, learned from a suggestion
            elif k in ("weekend_start", "weekend_end") and isinstance(v, str) and not v.strip():
                cfg.update(weekend_start="", weekend_end="", weekend_source="default")   # weekends follow the weekday hours again
            elif k in ("wake_start", "wake_end", "weekend_start", "weekend_end"):
                if not isinstance(v, str) or not HHMM.match(v.strip()):
                    raise ValueError(f"{k} must be a time like 07:00")
                h, m = v.strip().split(":")
                kind = WEEKEND if k.startswith("weekend") else WEEKDAY
                if kind == WEEKEND and not cfg["weekend_start"]:   # the first weekend time set: the other end starts from the weekday one
                    cfg["weekend_start"], cfg["weekend_end"] = cfg["wake_start"], cfg["wake_end"]
                if cfg[k] != f"{int(h):02d}:{m}":
                    cfg[k] = f"{int(h):02d}:{m}"
                    cfg[WINDOW_KEYS[kind][2]] = "manual"
            else:
                lo, hi = LIMITS[k]
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    raise ValueError(f"{k} must be a whole number") from None
                if not lo <= n <= hi:
                    raise ValueError(f"{k} must be between {lo} and {hi}")
                cfg[k] = n
        if cfg["daily_total"] < cfg["daily_regular"]:
            raise ValueError("daily_total counts regular and today messages together, so it cannot be below daily_regular")
        self.store.set_meta("proactive:config", json.dumps(cfg))
        for kind in DAY_TYPES:
            sug, (start, end, _src) = self.suggestion(kind), self.window(kind)
            if sug and (sug.get("current_start"), sug.get("current_end")) != (start, end):
                if not hours_differ(sug, start, end):
                    self._save_state(**{SUGGESTION_KEYS[kind][0]: None})   # the owner set (about) the suggested hours by hand
                else:
                    self._save_state(**{SUGGESTION_KEYS[kind][0]: {**sug, "current_start": start, "current_end": end}})
        return self.public()

    # ------------------------------------------------------------------ the two windows (day_types.py)
    def country(self) -> str:
        return country_for(self.store)

    def window(self, kind: str = WEEKDAY, cfg: dict[str, Any] | None = None) -> tuple[str, str, str]:
        """(start, end, source) of one day type's contact hours. Weekends without their own hours follow the weekday ones (source
        "default")."""
        cfg = cfg or self.config()
        if kind == WEEKEND and cfg.get("weekend_start") and cfg.get("weekend_end"):
            return cfg["weekend_start"], cfg["weekend_end"], cfg.get("weekend_source") or "manual"
        if kind == WEEKEND:
            return cfg["wake_start"], cfg["wake_end"], "default"
        return cfg["wake_start"], cfg["wake_end"], cfg["hours_source"]

    def day_type_at(self, ts: float | None = None) -> str:
        return type_at(self.clock() if ts is None else ts, self.tz(), self.country())

    def window_at(self, ts: float | None = None) -> tuple[str, str, str]:
        """(start, end, day type) that applies at `ts`: the window of the day the moment belongs to (day_types.logical_date)."""
        kind = self.day_type_at(ts)
        start, end, _src = self.window(kind)
        return start, end, kind

    def awake(self, ts: float | None = None) -> bool:
        ts = self.clock() if ts is None else ts
        start, end, _kind = self.window_at(ts)
        return in_waking_hours(ts, start, end, self.tz())

    def next_awake(self, ts: float) -> float:
        """`ts` when it is inside the contact hours of its day, else the next start of the contact hours (each day by its own type)."""
        if self.awake(ts):
            return ts
        tz, country = self.tz(), self.country()
        d = datetime.fromtimestamp(ts, tz)
        for k in range(0, 3):
            day = (d + timedelta(days=k)).date()
            start = _minutes(self.window(day_type(day, country))[0])
            w = datetime(day.year, day.month, day.day, start // 60, start % 60, tzinfo=tz)
            if w.timestamp() > ts:
                return w.timestamp()
        return ts + 86400

    def wake_start_today(self, ts: float | None = None) -> float:
        """Today's (local) start of the contact hours as unix seconds, by today's day type (yesterday's start when still before it)."""
        ts = self.clock() if ts is None else ts
        tz, country = self.tz(), self.country()
        d = datetime.fromtimestamp(ts, tz)
        for back in (0, 1):
            day = (d - timedelta(days=back)).date()
            m = _minutes(self.window(day_type(day, country))[0])
            w = datetime(day.year, day.month, day.day, m // 60, m % 60, tzinfo=tz).timestamp()
            if w <= ts:
                return w
        return wake_started_at(ts, self.window_at(ts)[0], tz)

    # ------------------------------------------------------------------ learned contact hours (active_hours.py)
    def owner_live(self, ts: float | None = None) -> bool:
        """The owner sent a chat message within LIVE_SECONDS: awake now even outside the contact hours. For follow-ups and what the
        owner asked for; purely proactive messages keep to the contact hours and the unanswered guard."""
        now = self.clock() if ts is None else ts
        rows = self.store._all("SELECT content, created_at FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 50")
        marks = self.owner_marks
        last = next((float(r["created_at"]) for r in rows if not (marks and str(r["content"]).startswith(marks))), None)
        return last is not None and now - LIVE_SECONDS <= last <= now + 60

    def learned_hours(self, now: float | None = None, kind: str = WEEKDAY) -> dict[str, Any] | None:
        return self.learned_windows(now)[kind]

    def learned_windows(self, now: float | None = None) -> dict[str, dict[str, Any] | None]:
        """{"weekday": window|None, "weekend": window|None} learned from the owner's own activity (active_hours.learn_windows)."""
        now = self.clock() if now is None else now
        return learn_windows(activity_days(self.store, self.tz(), now, self.owner_marks), self.country())

    def hours_suggestion(self) -> dict[str, Any] | None:
        return self.suggestion(WEEKDAY)

    def suggestion(self, kind: str) -> dict[str, Any] | None:
        v = self.state().get(SUGGESTION_KEYS[kind][0])
        return v if isinstance(v, dict) else None

    def check_hours(self, now: float | None = None, *, force: bool = False) -> dict[str, Any] | None:
        """Once a local day (or when `force`): learn the weekday and the weekend window; where one differs meaningfully from its contact
        hours, apply it (auto_hours on: both day types) or offer it as a suggestion (at most once every RESUGGEST_DAYS days after "keep
        current", counted per day type). Hours the owner set by hand (source "manual") are never changed by Kelo, auto_hours or not: a learned
        window is only offered for them. Returns the open weekday suggestion, if any (the weekend one: suggestion("weekend"))."""
        now = self.clock() if now is None else now
        day = _day(now, self.tz())
        st = self.state()
        if not force and st.get("hours_checked_day") == day:
            return self.hours_suggestion()
        self._save_state(hours_checked_day=day)
        learned = self.learned_windows(now)
        for kind in DAY_TYPES:
            self._check_one(kind, learned[kind], now)
        return self.hours_suggestion()

    def _check_one(self, kind: str, learned: dict[str, Any] | None, now: float) -> None:
        cfg = self.config()
        sug_key, kept_key = SUGGESTION_KEYS[kind]
        start, end, src = self.window(kind, cfg)
        if learned is None or not hours_differ(learned, start, end):
            if learned is not None:
                self._save_state(**{sug_key: None})   # the setting already fits
            return
        if cfg["auto_hours"] and src != "manual":   # hours the owner set by hand are never changed by Kelo: only offered
            self._apply_hours(learned["wake_start"], learned["wake_end"], kind)
            self._save_state(**{sug_key: None}, **{("hours_auto_applied" if kind == WEEKDAY else "weekend_auto_applied"): {
                "at": now, "from": [start, end], "to": [learned["wake_start"], learned["wake_end"]]}})
            return
        kept = float(self.state().get(kept_key) or 0)
        if kept and now - kept < RESUGGEST_DAYS * 86400:
            return
        cur = self.suggestion(kind)
        if cur and cur.get("wake_start") == learned["wake_start"] and cur.get("wake_end") == learned["wake_end"]:
            if (cur.get("current_start"), cur.get("current_end")) != (start, end):
                cur = {**cur, "current_start": start, "current_end": end, "active_days": learned["active_days"]}
                self._save_state(**{sug_key: cur})   # the owner changed the hours by hand since: show what they are now
            return
        sug = {"id": hashlib.sha256(f"{kind}-{learned['wake_start']}-{learned['wake_end']}-{now}".encode()).hexdigest()[:12],
               "day_type": kind, "wake_start": learned["wake_start"], "wake_end": learned["wake_end"], "current_start": start,
               "current_end": end, "active_days": learned["active_days"], "created_at": now}
        self._save_state(**{sug_key: sug})

    def _apply_hours(self, start: str, end: str, kind: str = WEEKDAY) -> None:
        cfg = self.config()
        ks, ke, src = WINDOW_KEYS[kind]
        cfg.update({ks: start, ke: end, src: "learned"})
        self.store.set_meta("proactive:config", json.dumps(cfg))

    def answer_hours(self, sid: str, accept: bool) -> dict[str, Any]:
        """Accept (the contact hours of the suggestion's day type become the suggested ones, marked learned) or keep current (no new
        suggestion of that type for RESUGGEST_DAYS days). KeyError when there is no such open suggestion."""
        for kind in DAY_TYPES:
            sug = self.suggestion(kind)
            if not sug or sug.get("id") != sid:
                continue
            sug_key, kept_key = SUGGESTION_KEYS[kind]
            if accept:
                self._apply_hours(sug["wake_start"], sug["wake_end"], kind)
                self._save_state(**{sug_key: None})
            else:
                self._save_state(**{sug_key: None, kept_key: self.clock()})
            return self.public()
        raise KeyError(sid)

    def state(self) -> dict[str, Any]:
        try:
            v = json.loads(self.store.meta("proactive:state") or "{}")
        except ValueError:
            v = {}
        return v if isinstance(v, dict) else {}

    def _save_state(self, **kw: Any) -> None:
        self.store.set_meta("proactive:state", json.dumps({**self.state(), **kw}, ensure_ascii=False))

    def today_counts(self) -> dict[str, int]:
        day = _day(self.clock(), self.tz())
        rows = self.store._all("SELECT urgency, COUNT(*) AS n FROM proactive_log WHERE status = 'sent' AND kind != 'change' AND day = ? "
                               "GROUP BY urgency", (day,))
        out = {u: 0 for u in URGENCIES}
        for r in rows:
            if r["urgency"] in out:
                out[r["urgency"]] = r["n"]
        return out

    def today_changes(self) -> int:
        """Change alerts (mail about a changed flight, booking or a security alert) sent today: told at once, outside every daily limit
        except their own daily_change."""
        day = _day(self.clock(), self.tz())
        return int(self.store._all("SELECT COUNT(*) AS n FROM proactive_log WHERE status = 'sent' AND kind = 'change' AND day = ?", (day,))[0]["n"])

    def room_today(self) -> bool:
        """Whether today's cap (daily_total: 보통 + 오늘) still has room: the checklist (checklist.py) asks before it runs."""
        n = self.today_counts()
        return n["보통"] + n["오늘"] < int(self.config()["daily_total"])

    def checklist_said(self, task_id: int | None) -> None:
        """A checklist run said something: it counts as one 오늘 message toward today's cap and is in the log (source "checklist")."""
        now = self.clock()
        c = Candidate(key=f"checklist:{task_id or int(now)}", source="checklist", title="CHECKLIST.md")
        self._record(c, "sent", "checklist", urgency="오늘", keep=SENT_KEEP, task_id=task_id)
        self._count_unanswered(now)

    # ------------------------------------------------------------------ the unanswered guard
    # Messages nobody answers stop: after `unanswered_limit` proactive messages (checklist ones included) with no chat message from the
    # owner in between, nothing more goes out for `pause_hours`; then one message may go, and if that one is not answered either the next
    # pause doubles (18 h -> 36 h -> 72 h ...), capped at `pause_max_days` (so at most one message per that many days). Any chat message
    # from the owner resets the count and the level. Opening the app or reading a notification does not. Nothing runs at all before the
    # owner's first chat message on this server. What the owner asked for (watchers, schedule and job results, approvals, questions of a
    # running task, reminders) does not go through here and is never held back by it.
    def _owner_newest(self) -> int:
        """Id of the owner's newest chat message (0 when there is none); hidden rows led by an owner mark are not theirs."""
        rows = self.store._all("SELECT id, content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 50")
        marks = self.owner_marks
        return next((int(r["id"]) for r in rows if not (marks and str(r["content"]).startswith(marks))), 0)

    def _sync_owner(self) -> dict[str, Any]:
        """The guard's state, reset when the owner wrote since it was last looked at."""
        st = self.state()
        newest = self._owner_newest()
        if newest and newest != int(st.get("owner_seen_id") or 0):
            st = {**st, "owner_seen_id": newest, "unanswered": 0, "backoff_level": 0, "last_unanswered_at": None}
            self._save_state(owner_seen_id=newest, unanswered=0, backoff_level=0, last_unanswered_at=None)
        return st

    def pause_seconds(self, level: int, cfg: dict[str, Any] | None = None) -> float:
        """How long the pause after reaching backoff `level` (1, 2, ...) lasts: pause_hours doubled per level, capped."""
        cfg = cfg or self.config()
        if level <= 0:
            return 0.0
        return min(float(cfg["pause_hours"]) * 3600 * (2 ** min(level - 1, MAX_LEVEL)), float(cfg["pause_max_days"]) * 86400)

    def guard(self, now: float | None = None) -> dict[str, Any]:
        """{"owner_spoke", "unanswered", "unanswered_limit", "backoff_level", "pause_hours" (of the current level), "next_allowed_at"
        (unix seconds or None when nothing holds it back), "room" (proactive messages that may go out now)}."""
        now = self.clock() if now is None else now
        cfg, st = self.config(), self._sync_owner()
        n, level = int(st.get("unanswered") or 0), int(st.get("backoff_level") or 0)
        limit = int(cfg["unanswered_limit"])
        next_at: float | None = None
        if level <= 0 and n < limit:
            room = limit - n
        else:
            level = max(level, 1)    # the limit was lowered under the count: the first pause applies
            next_at = float(st.get("last_unanswered_at") or now) + self.pause_seconds(level, cfg)
            room = 1 if now >= next_at else 0
        return {"owner_spoke": bool(int(st.get("owner_seen_id") or 0)), "unanswered": n, "unanswered_limit": limit,
                "backoff_level": level if next_at is not None else 0,
                "pause_hours": round(self.pause_seconds(max(level, 1), cfg) / 3600, 2), "next_allowed_at": next_at, "room": room}

    def _count_unanswered(self, now: float) -> None:
        """One more proactive message the owner has not answered (yet): the limit, then each further one, raises the backoff level."""
        st = self._sync_owner()
        n, level = int(st.get("unanswered") or 0) + 1, int(st.get("backoff_level") or 0)
        if level > 0:
            level = min(level + 1, MAX_LEVEL)
        elif n >= int(self.config()["unanswered_limit"]):
            level = 1
        self._save_state(unanswered=n, backoff_level=level, last_unanswered_at=now)

    def hold_reason(self, now: float | None = None) -> str | None:
        """The skip code that holds proactive messages back now ("no_owner_message" / "waiting_for_reply"), or None."""
        g = self.guard(now)
        if not g["owner_spoke"]:
            return "no_owner_message"
        if g["room"] <= 0:
            return "waiting_for_reply"
        return None

    def checklist_hold(self) -> str | None:
        """For the checklist (checklist.py): the reason text when the guard holds it back, else None."""
        code = self.hold_reason()
        return t(SKIP_REASONS[code]) if code else None

    def _skip(self, code: str, *, force: bool = False) -> dict[str, Any]:
        """Record "skipped because <code>" as last_pass. A skip with the same code is written again only once per pass period, so a
        minute-by-minute tick does not rewrite it; last_pass_at (the pace) is left as it is."""
        now, cfg = self.clock(), self.config()
        prev = self.state().get("last_pass") or {}
        out = {"at": now, "status": "skipped", "reason": t(SKIP_REASONS[code]), "reason_code": code, "candidates": 0, "sent": 0,
               "dropped": 0, **self._backoff_fields(now)}
        if force or prev.get("status") != "skipped" or prev.get("reason_code") != code or now - float(prev.get("at") or 0) >= cfg["every_minutes"] * 60:
            self._save_state(last_pass=out)
        return out

    def _backoff_fields(self, now: float) -> dict[str, Any]:
        g = self.guard(now)
        return {"backoff_level": g["backoff_level"], "next_allowed_at": g["next_allowed_at"]}

    def public(self) -> dict[str, Any]:
        g = self.guard()
        g.pop("room", None)
        cfg, now = self.config(), self.clock()
        wk_start, wk_end, wk_src = self.window(WEEKEND, cfg)
        start, end, kind = self.window_at(now)
        country = self.country()
        return {**cfg, "weekend_start": wk_start, "weekend_end": wk_end, "weekend_source": wk_src,
                "timezone": self.tz().key, "prefs_file": NOTIFY_PREFS, "today": self.today_counts(), "today_changes": self.today_changes(),
                "last_pass": self.state().get("last_pass"), "awake": self.awake(), "guard": g,
                "hours_suggestion": self.hours_suggestion(), "weekend_suggestion": self.suggestion(WEEKEND),
                "day_type": kind, "today_start": start, "today_end": end, "holiday_country": country,
                "day_types": upcoming_days(now, self.tz(), country), "learned": self.learned_differs(now)}

    def learned_differs(self, now: float | None = None) -> dict[str, dict[str, Any] | None]:
        """Per day type, the learned window when there is one and it differs from the hours in effect (the settings show it with an
        Apply button, also while a "keep current" holds the suggestion back), else None."""
        out: dict[str, dict[str, Any] | None] = {}
        for kind, got in self.learned_windows(now).items():
            start, end, _src = self.window(kind)
            out[kind] = got if got is not None and hours_differ(got, start, end) else None
        return out

    def apply_learned(self, kind: str) -> dict[str, Any]:
        """The owner pressed Apply next to a learned window: it becomes that day type's hours (source "learned"). KeyError when there
        is no learned window that differs."""
        if kind not in DAY_TYPES:
            raise KeyError(kind)
        got = self.learned_differs().get(kind)
        if not got:
            raise KeyError(kind)
        self._apply_hours(got["wake_start"], got["wake_end"], kind)
        self._save_state(**{SUGGESTION_KEYS[kind][0]: None})
        return self.public()

    def log(self, limit: int = 50, before: int | None = None) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT * FROM proactive_log WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                               (before, before, max(1, min(int(limit), 200))))
        return [{"id": r["id"], "key": r["key"], "source": r["source"], "title": r["title"], "urgency": r["urgency"], "status": r["status"],
                 "reason": r["reason"], "message": r["message"], "decided_at": r["decided_at"], "expires_at": r["expires_at"],
                 "task_id": r["task_id"], "kind": r["kind"]} for r in rows]

    def _ledgered(self, key: str, now: float) -> bool:
        return bool(self.store._all("SELECT 1 FROM proactive_log WHERE key = ? AND expires_at > ? LIMIT 1", (key, now)))

    def _record(self, c: Candidate, status: str, reason: str, *, urgency: str | None = None, message: str = "", keep: float,
                task_id: int | None = None, kind: str = "") -> None:
        now = self.clock()
        lid = int(self.store._run("INSERT INTO proactive_log(key, source, title, urgency, status, reason, message, decided_at, expires_at, day, "
                                  "task_id, kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (c.key, c.source, c.title[:200], urgency, status, reason[:300], message[:1000], now, now + keep,
                                   _day(now, self.tz()), task_id, kind)).lastrowid or 0)
        self.store._run("DELETE FROM proactive_log WHERE id <= ? - ?", (lid, KEEP_LOG))

    def prefs(self) -> str:
        if self.home is None:
            return ""
        p = self.home.root / NOTIFY_PREFS
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() and not p.is_symlink() else ""

    # ------------------------------------------------------------------ candidates
    async def gather(self, since: float, *, mail_only: bool = False) -> tuple[list[Candidate], dict[str, str]]:
        """Everything worth a look from the connected sources (`mail_only`: just the mailboxes, for a pass held back by the unanswered
        guard, which only lets change alerts through). Returns (candidates, per-source notes such as an error)."""
        now, tz = self.clock(), self.tz()
        out: list[Candidate] = []
        notes: dict[str, str] = {}
        self._cursors = {}
        self._mailbox_count, self._mailbox_names = 0, {}
        self._events = []
        if self.mail is not None and self.config()["mail_check"]:   # off: no mailbox is opened, no cursor moves, no mail line anywhere
            cands, mail_note = await self.gather_mail(now)
            out += cands
            if mail_note:
                notes["mail"] = mail_note
        if mail_only:
            return out, notes
        if self.calendar is not None:
            try:
                evs = await self.calendar()
                self._events = evs
                big = big_events.ahead(evs, now, tz)
                later = {(str(e.get("title") or "").strip().casefold(), e["first"]) for e in big}
                for ev in evs:
                    start = float(ev.get("start") or 0)
                    if ev.get("all_day") or (str(ev.get("title") or "").strip().casefold(), big_events.first_day(ev, tz)) in later:
                        continue   # all-day events are not timed alerts; a big event on a later day is told at its lead times below
                    if now - 600 <= start <= now + 86400:
                        out.append(Candidate(f"calendar:{ev.get('id')}:{int(start)}", "calendar", str(ev.get("title") or t("Event")),
                                             str(ev.get("where") or ""), due=start, day=_day(start, tz)))
                out += self.ahead_candidates(big, cfg_leads=self.config()["ahead_days"])
            except Exception as e:  # noqa: BLE001
                notes["calendar"] = f"{type(e).__name__}: {str(e)[:120]}"
        if self.schedules is not None:
            for r in self.schedules.runs_since(since):
                if r["status"] in ("done", "failed", "paused"):
                    out.append(Candidate(f"schedule-run:{r['id']}", "schedule", r["name"], r["result"] or r["status"],
                                         told=t("The schedule already told you")))
        if self.watchers is not None:
            for e in self.watchers.fired_since(since):
                out.append(Candidate(f"watch-hit:{e['id']}", "watcher", e["name"], e["detail"], told=t("The watcher already told you")))
        for g in self.store.goals():
            if g.get("status") != "tracking":
                continue
            found = None
            if g.get("deadline"):
                found = find_date(str(g["deadline"]), now, tz)
            found = found or find_date(f"{g['title']} {g.get('note') or ''}", now, tz)
            if found and -86400 <= (datetime.fromisoformat(found[0]).replace(tzinfo=tz).timestamp() - now) <= 3 * 86400:
                out.append(Candidate(f"goal:{g['id']}:{found[0]}", "goal", g["title"], g.get("note") or "", due=found[1], day=found[0]))
        if self.home is not None:
            mem = self.home.root / "MEMORY.md"
            text = mem.read_text(encoding="utf-8", errors="replace") if mem.is_file() and not mem.is_symlink() else ""
            for line in section(text, "Commitments").splitlines():
                line = re.sub(r"^\s*[-*•]\s*", "", line).strip()
                found = find_date(line, now, tz) if line else None
                if not found:
                    continue
                start = datetime.fromisoformat(found[0]).replace(tzinfo=tz).timestamp()
                if -3600 <= ((found[1] or start + 86399) - now) and start - now <= 2 * 86400:
                    key = "memory:" + hashlib.sha256(line.encode()).hexdigest()[:12] + f":{found[0]}"
                    out.append(Candidate(key, "memory", line[:160], "", due=found[1], day=found[0]))
        for src in self.sources:
            try:
                out += src.candidates(now)
            except Exception as e:  # noqa: BLE001 - one source failing never stops the pass
                notes[type(src).__name__] = f"{type(e).__name__}: {str(e)[:120]}"
        seen, unique = set(), []
        for c in out:
            if c.key not in seen:
                seen.add(c.key)
                unique.append(c)
        return unique, notes

    # ------------------------------------------------------------------ mail: everything since the last look
    def mail_state(self, name: str) -> dict[str, Any]:
        """Where the last look at one mailbox ended: {"inbox": cursor, "junk": cursor, "promo": cursor, "skim_day": "YYYY-MM-DD"}. A
        cursor is what the mailbox's arrivals() gave back (IMAP: {"v": UIDVALIDITY, "uid": last UID, "t": time}; Graph and others:
        {"t": ...}); a kind with none starts at "now" the first time (no backlog)."""
        try:
            v = json.loads(self.store.meta(MAIL_CURSOR + name) or "{}")
        except ValueError:
            v = {}
        return v if isinstance(v, dict) else {}

    def commit_mail(self) -> None:
        """Save the cursors this pass ended at. Called only once the pass got through (every mail it read has been judged and logged), so
        a pass that failed on the model or on delivery looks at the same mail again; a mailbox that could not be read has none here and
        keeps its old cursor."""
        for name, st in self._cursors.items():
            self.store.set_meta(MAIL_CURSOR + name, json.dumps(st))
        if self._cursors and self.store.meta(MAIL_RESUME):
            self.store.set_meta(MAIL_RESUME, "")   # the catch-up after switching mail back on is done
        self._cursors = {}

    async def gather_mail(self, now: float) -> tuple[list[Candidate], str]:
        """Candidates from every connected mailbox: ALL unread, non-bulk mail that arrived after the mailbox's cursor (no count limit),
        plus, once per local day (config "spam_skim"), what arrived since its own cursor in the spam folder and in the provider's
        promotions sorting, tagged "found in spam" / "found in promotions". One mailbox failing never stops the others."""
        try:
            accounts = self.mail.accounts()
        except Exception as e:  # noqa: BLE001 - one source failing never stops the pass
            return [], f"{type(e).__name__}: {str(e)[:120]}"
        self._mailbox_count, self._mailbox_names = len(accounts), mailbox_names(accounts)
        today = _day(now, self.tz())
        skim = bool(self.config()["spam_skim"])
        resume = bool(self.store.meta(MAIL_RESUME))
        notes: list[str] = []

        async def catch_up(name: str) -> tuple[list[Candidate], dict[str, Any] | None, str]:
            """Mail was switched off and is on again: no cursor means "start at now", so what came in meanwhile is not alerted."""
            new: dict[str, Any] = {}
            try:
                for kind, _tag in (("inbox", ""), *SKIM_KINDS):
                    try:
                        got = await self.mail.arrivals(name, None, kind)
                    except Exception:  # noqa: BLE001 - the spam/promo folder missing or unreadable: it starts from nothing next time
                        if kind == "inbox":
                            raise
                        continue
                    if got is not None:
                        new[kind] = got["cursor"]
                return [], new, ""
            except Exception as e:  # noqa: BLE001
                return [], None, f"{type(e).__name__}: {str(e)[:120]}"

        async def one(acct: dict[str, Any]) -> tuple[list[Candidate], dict[str, Any] | None, str]:
            name = acct["name"]
            if resume:
                return await catch_up(name)
            st, found = self.mail_state(name), []
            new = dict(st)
            try:
                if skim and st.get("skim_day") != today:
                    done = True
                    for kind, tag in SKIM_KINDS:
                        try:
                            got = await self.mail.arrivals(name, st.get(kind), kind)
                        except Exception as e:  # noqa: BLE001 - the skim is retried next pass; the inbox below is still read
                            done = False
                            notes.append(f"{name} ({tag}): {type(e).__name__}: {str(e)[:100]}")
                            continue
                        if got is None:
                            continue   # this provider has no such folder
                        new[kind] = got["cursor"]
                        found += [self._mail_candidate(name, m, tag, kind) for m in got["rows"]]
                    if done:
                        new["skim_day"] = today
                got = await self.mail.arrivals(name, st.get("inbox"), "inbox")
                if got is None:
                    return found, None, ""
                new["inbox"] = got["cursor"]
                return found + [self._mail_candidate(name, m) for m in got["rows"]], new, ""
            except Exception as e:  # noqa: BLE001 - a mailbox that fails (expired password, IMAP off, too slow) keeps its old cursor
                return [], None, f"{type(e).__name__}: {str(e)[:120]}"

        out: list[Candidate] = []
        for acct, (cands, new, note) in zip(accounts, await asyncio.gather(*(one(a) for a in accounts))):
            out += cands
            if note:
                notes.append(note)
            if new is not None:
                self._cursors[acct["name"]] = new
        return out, "; ".join(notes)

    @staticmethod
    def _mail_candidate(name: str, m: dict[str, Any], tag: str = "", kind: str = "") -> Candidate:
        sender = str(m.get("from") or "")
        snippet = " ".join(str(m.get("snippet") or "").split())[:160]
        key = f"mail:{name}:{kind}:{m['uid']}" if kind else f"mail:{name}:{m['uid']}"
        detail = f"from {sender[:70]}" + (f" — {snippet}" if snippet else "")
        return Candidate(key, "mail", m.get("subject") or t("(no subject)"), detail,
                         extra={"from": sender, "account": name, **({"found": tag} if tag else {})})

    def ahead_candidates(self, big: list[dict[str, Any]], cfg_leads: list[int]) -> list[Candidate]:
        """Big events (big_events.ahead) at one of the owner's lead times: one candidate per event and lead time, keyed so the ledger
        never lets the same lead time be told twice ("ahead:<event>:<days>"). An event first seen 5 days before (leads 7 and 1) is at
        lead 7; the day before, at lead 1."""
        out: list[Candidate] = []
        for ev in big:
            lead = big_events.lead_for(ev["days_left"], list(cfg_leads))
            if lead is None:
                continue
            ident = hashlib.sha256(f"{str(ev.get('title') or '').strip().casefold()}|{ev['first'].isoformat()}".encode()).hexdigest()[:12]
            words = {"flight": "a flight", "stay": "a stay", "trip": "a trip", "days": "an event over several days"}[ev["kind"]]
            left = "tomorrow" if ev["days_left"] == 1 else f"in {ev['days_left']} days"
            detail = (f"coming up: {words}, {left} ({big_events.span_text(ev)})" + (f", at {ev['where']}" if ev.get("where") else "")
                      + "; say what to prepare")
            out.append(Candidate(f"ahead:{ident}:{lead}", "calendar", str(ev.get("title") or t("Event")), detail,
                                 day=ev["first"].isoformat(), extra={"lead": lead, "kind": ev["kind"]}))
        return out

    def ahead_tool(self) -> Any:
        """big_event_notice: the owner says how early to be told about trips, flights, stays and several-day events."""
        from .agent import Tool

        async def big_event_notice(a: dict[str, Any]) -> str:
            if a.get("days") is None:
                days = self.config()["ahead_days"]
                return (f"Big events are mentioned {', '.join(map(str, days))} day(s) before." if days else
                        "Big events are not mentioned ahead (off).")
            try:
                got = self.update(ahead_days=a.get("days"))["ahead_days"]
            except ValueError as e:
                return f"Error: {e}"
            return (f"Saved: big events are now mentioned {', '.join(map(str, got))} day(s) before, once each." if got else
                    "Saved: big events are no longer mentioned ahead.")
        return Tool("big_event_notice",
                    "Read or change how many days before a big event (a trip, flight, stay or several-day event in the calendar) you "
                    "mention it first, with what to prepare, when the user asks. Default 7 days and 1 day before; [] turns it off.",
                    {"type": "object", "properties": {"days": {"type": "array", "items": {"type": "integer", "minimum": 1,
                                                                                            "maximum": big_events.AHEAD_DAYS},
                                                               "description": "days before, e.g. [7, 1]; leave out to read the setting"}}},
                    big_event_notice)

    # ------------------------------------------------------------------ running
    async def tick(self) -> dict[str, Any] | None:
        """What the internal job calls every minute: a pass when one is due."""
        cfg = self.config()
        now = self.clock()
        try:
            self.check_hours(now)   # once a local day, whether proactive messages are on or not (follow-ups use the hours too)
        except Exception:  # noqa: BLE001
            log.exception("proactive: learning the active hours failed")
        cfg = self.config()
        if self.lock.locked():
            return None
        if not cfg["enabled"]:
            self._skip("off")
            return None
        last = float(self.state().get("last_pass_at") or 0)
        due = now - last >= cfg["every_minutes"] * 60 or last < self.wake_start_today(now)
        if not due:
            return None
        if not self.awake(now):
            self._skip("outside_waking_hours")   # not even the mailbox is opened
            return None
        hold = self.hold_reason(now)
        if hold:
            if hold == "waiting_for_reply" and cfg["change_alerts"] and cfg["mail_check"] and self.mail is not None and not self.busy():
                return await self.run(guarded=True, held=hold)   # only change alerts may go out; the rest of the mail is logged as held
            self._skip(hold)
            return None
        if self.busy():
            self._skip("chat_running")   # a chat reply is running: try again on the next tick
            return None
        return await self.run(guarded=True)

    async def run(self, *, guarded: bool = False, held: str = "") -> dict[str, Any]:
        """One pass. `guarded` (the internal job): the unanswered guard limits how many go out. A pass the owner starts by hand
        (POST /api/proactive/run) is not held back, but what it sends still counts as unanswered."""
        if self.lock.locked():
            return self._skip("already_running", force=True)
        async with self.lock:
            try:
                out = await self._run(guarded, held)
                if held and not out["sent"] and out["status"] in ("done", "nothing_new"):   # nothing got through: still "held back" to the app
                    out = {**out, "status": "skipped", "reason": t(SKIP_REASONS[held]), "reason_code": held}
                    self._save_state(last_pass=out)
                return out
            except Exception as e:  # noqa: BLE001
                if limit_of(e) is not None:   # the usage is used up (or nearly): a quiet skip, the items are looked at next time
                    log.info("proactive pass skipped: %s", str(e)[:160])
                    return self._finish("skipped", t(SKIP_REASONS["model_limit"]), code="model_limit")
                log.exception("proactive pass failed")
                return self._finish("failed", f"{type(e).__name__}: {str(e)[:160]}", code="failed")

    def _finish(self, status: str, reason: str = "", *, code: str = "", **counts: int) -> dict[str, Any]:
        now = self.clock()
        out = {"at": now, "status": status, "reason": reason, "reason_code": code, "candidates": counts.get("candidates", 0),
               "sent": counts.get("sent", 0), "dropped": counts.get("dropped", 0), **self._backoff_fields(now)}
        self._save_state(last_pass=out, last_pass_at=out["at"])
        return out

    async def _run(self, guarded: bool = False, held: str = "") -> dict[str, Any]:
        now, tz, cfg = self.clock(), self.tz(), self.config()
        since = float(self.state().get("last_pass_at") or now - 86400)
        found, notes = await self.gather(since, mail_only=bool(held))
        fresh = [c for c in found if not self._ledgered(c.key, now)]
        dropped = 0
        for c in [c for c in fresh if c.told]:
            self._record(c, "dropped", c.told, keep=SENT_KEEP)
            dropped += 1
        fresh = [c for c in fresh if not c.told]
        prefs = self.prefs()
        never = keywords(section(prefs, *NEVER_HEADINGS))
        if never:
            for c in [c for c in fresh if any(k in f"{c.title} {c.detail}".lower() for k in never)]:
                self._record(c, "dropped", t("It is on your don't-tell-me list"), keep=DROPPED_KEEP)
                dropped += 1
                fresh.remove(c)
        if not fresh:
            self.commit_mail()
            note = "; ".join(f"{k}: {v}" for k, v in notes.items())
            return self._finish("nothing_new", note or t(SKIP_REASONS["nothing_new"]), code="nothing_new", candidates=len(found), dropped=dropped)
        chosen = self.model()
        if not chosen:
            return self._finish("skipped", t(SKIP_REASONS["no_model"]), code="no_model", candidates=len(found), dropped=dropped)
        decisions = await self._classify(chosen, fresh, prefs, cfg, now, tz)
        if decisions is None:
            return self._finish("failed", "the model did not answer in the expected form", code="bad_answer", candidates=len(found),
                                dropped=dropped)
        counts = self.today_counts()
        changes = self.today_changes()
        rank = {"지금": 0, "오늘": 1, "보통": 2}
        order = sorted(range(len(fresh)), key=lambda i: (0 if decisions.get(i, {}).get("change") else 1,
                                                         rank.get(decisions.get(i, {}).get("urgency", ""), 9), i))
        outbox: list[Outgoing] = []
        sent = counted = 0   # counted: the ones the unanswered guard counts (a change alert is not one)
        room = 0 if held else (self.guard(now)["room"] if guarded else None)   # proactive messages the unanswered guard still lets out
        tomorrow = (datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
        for i in order:
            c, d = fresh[i], decisions.get(i)
            if d is None:
                self._record(c, "dropped", t("Got no decision for it"), keep=max(3600.0, tomorrow - now))
                dropped += 1
                continue
            urgency, reason, message = d["urgency"], d["reason"], d["message"]
            change = bool(d.get("change")) and c.source == "mail" and bool(cfg["change_alerts"])
            if change and changes >= cfg["daily_change"]:   # a flood of "changes" (or a misjudging model): over the limit they are 오늘
                change, urgency = False, ("오늘" if urgency in ("drop", "보통") else urgency)
                reason = (reason + t(" (over the limit for changes, so moved to 'today')")).strip()
            if change:
                urgency = "지금"   # told at once: outside daily_regular / daily_total / daily_now and not held by the unanswered guard
                message = message or c.title + (f" — {c.detail[:120]}" if c.detail else "")
            if urgency == "drop":
                self._record(c, "dropped", reason or t("Not worth telling you"), keep=DROPPED_KEEP)
                dropped += 1
                continue
            if c.extra.get("found"):
                message = t("Found in {where}: {message}", where=t("the spam folder") if c.extra["found"] == "spam" else t("promotions"),
                            message=message)
            if urgency == "지금" and not change and counts["지금"] >= cfg["daily_now"]:
                urgency, reason = "오늘", reason + t(" (over the limit for 'now', so moved to 'today')")
            if urgency == "지금":
                ok = True
            elif urgency == "오늘":
                ok = counts["보통"] + counts["오늘"] < cfg["daily_total"]
            else:
                ok = counts["보통"] < cfg["daily_regular"] and counts["보통"] + counts["오늘"] < cfg["daily_total"]
            if ok and not change and room is not None and counted >= room:
                self._record(c, "dropped", t(SKIP_REASONS["waiting_for_reply"]), urgency=urgency, keep=max(3600.0, tomorrow - now))
                dropped += 1
                continue
            if not ok:
                self._record(c, "dropped", t("Over the number that can be sent today ({urgency})", urgency=urgency), urgency=urgency, keep=max(3600.0, tomorrow - now))
                dropped += 1
                continue
            outbox.append(Outgoing(c, urgency, reason, message, change))
            if change:
                changes += 1   # not counted as unanswered either: like what the owner asked for, it is never held back
            else:
                counts[urgency] += 1
                counted += 1
            sent += 1
        failure: BaseException | None = None
        for g in group_outbox(outbox):   # one message per group; a group that fails to go out is not logged and keeps the cursors where they are
            try:
                task_id = await self._deliver_group(g)
            except Exception as e:  # noqa: BLE001 - the other groups still go out; the pass then fails as a whole (below)
                log.warning("proactive delivery failed: %s", str(e)[:160])
                failure = failure or e
                continue
            for o in g.items:
                self._record(o.c, "sent", o.reason, urgency=o.urgency, message=o.message, keep=SENT_KEEP, task_id=task_id,
                             kind="change" if o.change else "")
                if not o.change:
                    self._count_unanswered(self.clock())
        if failure is not None:
            raise failure
        self.commit_mail()
        return self._finish("done", "", candidates=len(found), sent=sent, dropped=dropped)

    async def _deliver_group(self, g: Group) -> int | None:
        """Tell one group as one message (see the module docstring, 5); returns its task id."""
        first = g.entries[0].item
        name = self._mailbox_names.get(g.key[1], g.key[1]) if g.is_mail and self._mailbox_count > 1 else ""
        many = len(g.entries) > 1
        urgency = min((o.urgency for o in g.items), key=lambda u: {"지금": 0, "오늘": 1}.get(u, 2))
        if not g.is_mail:
            notice = ""
        elif not many:
            notice = mail_notice(str(first.c.extra.get("from") or ""), first.c.title)
        elif name:
            notice = t("New mail · {mailbox} · {count}", mailbox=name, count=len(g.entries))[:MAIL_NOTICE_CHARS]
        else:
            notice = t("New mail · {count}", count=len(g.entries))[:MAIL_NOTICE_CHARS]
        if self.deliver is None:
            return None
        return await self.deliver({"key": first.c.key, "source": "mail" if g.is_mail else first.c.source,
                                   "title": t("{n} things to tell you", n=len(g.entries)) if many else first.c.title,
                                   "urgency": urgency, "message": group_text(g.entries, t("{mailbox}:", mailbox=name) if name else ""),
                                   "kind": "change" if any(o.change for o in g.items) else "", "notice": notice})

    async def _classify(self, chosen: tuple[str, str], items: list[Candidate], prefs: str, cfg: dict[str, Any], now: float,
                        tz: ZoneInfo) -> dict[int, dict[str, Any]] | None:
        """Every item judged, BATCH at a time (as few requests as that allows; none is left out). None when one batch gets no answer in
        the expected form: the pass then fails as a whole and looks at the same items next time."""
        out: dict[int, dict[str, Any]] = {}
        for at in range(0, len(items), BATCH):
            got = await self._classify_batch(chosen, items[at:at + BATCH], prefs, cfg, now, tz)
            if got is None:
                return None
            out.update({at + i: d for i, d in got.items()})
        return out

    def _calendar_block(self, tz: ZoneInfo) -> str:
        """The calendar lines the model compares booking mails with ("" when off or nothing is read)."""
        if not self._events:
            return ""
        lines = []
        for e in sorted(self._events, key=lambda e: float(e.get("start") or 0))[:CALENDAR_LINES]:
            start = float(e.get("start") or 0)
            when = datetime.fromtimestamp(start, tz).strftime("%Y-%m-%d" if e.get("all_day") else "%Y-%m-%d %H:%M")
            lines.append(f"- {when} {str(e.get('title') or '')[:80]}" + (f" @ {str(e['where'])[:60]}" if e.get("where") else ""))
        return "\n".join(lines)

    async def _classify_batch(self, chosen: tuple[str, str], items: list[Candidate], prefs: str, cfg: dict[str, Any], now: float,
                              tz: ZoneInfo) -> dict[int, dict[str, Any]] | None:
        provider, model = chosen
        counts = self.today_counts()
        left_regular = max(0, cfg["daily_regular"] - counts["보통"])
        left_total = max(0, cfg["daily_total"] - counts["보통"] - counts["오늘"])
        head = (f"Now: {datetime.fromtimestamp(now, tz).strftime('%a %Y-%m-%d %H:%M')} ({tz.key}). Still allowed today: {left_regular} 보통, "
                f"{left_total} 보통+오늘 together.\n{tone_line(self.tone(), switch=False)}\n\nThe user's preferences (NOTIFY_PREFS.md):\n{(prefs.strip() or '(none)')[:PREFS_CHARS]}\n\nItems:\n")
        body = "\n".join(f"{n}. {c.line(tz)}" for n, c in enumerate(items, 1))
        cal = self._calendar_block(tz) if cfg["calendar_check"] and any(c.source == "mail" for c in items) else ""
        if cal:
            head = head.replace("Items:\n", f"Calendar (to compare booking mails with):\n{cal}\n\nItems:\n")
        req = ChatRequest(model=model, messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": head + body}], tools=[],
                          extra={})
        parts: list[str] = []
        async for ev in self.stream_fn(provider, req, **self.key_args(provider)):
            if isinstance(ev, TextDelta):
                parts.append(ev.text)
        data = parse_output("".join(parts))
        if not data or not isinstance(data.get("items"), list):
            return None
        out: dict[int, dict[str, Any]] = {}
        for it in data["items"]:
            if not isinstance(it, dict):
                continue
            try:
                i = int(it.get("n")) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= i < len(items) or i in out:
                continue
            c = items[i]
            urgency = str(it.get("urgency") or "").strip()
            if urgency.lower() == "drop" or urgency in ("버림", "안 알림"):
                urgency = "drop"
            elif urgency not in URGENCIES:
                urgency = "오늘"   # unclear: 오늘, as the rules say
            reason = str(it.get("reason") or "").strip()[:200]
            if not reason:   # the log always says why (the model was asked for a reason on every item and gave none)
                reason = t("No reason given; judged not worth telling you") if urgency == "drop" else t("No reason given; judged worth telling you")
            message = re.sub(r"\s+\n", "\n", str(it.get("message") or "")).strip()[:600]
            if urgency != "drop" and not message:
                message = f"{c.title}" + (f" — {c.detail[:120]}" if c.detail else "")
            if urgency == "지금":
                due = c.due or _due_from(str(it.get("due") or ""), now, tz)
                if due is None or not (now - 900 <= due <= now + NOW_WINDOW):
                    urgency, reason = "오늘", (reason + t(" (no sign of a time soon, so moved to 'today')")).strip()
            mismatch = it.get("mismatch") is True and c.source == "mail" and bool(cfg["calendar_check"])
            if mismatch and urgency in ("drop", "보통"):   # a booking that disagrees with the calendar matters at least today
                urgency = "오늘"
                if not message:
                    message = f"{c.title}" + (f" — {c.detail[:120]}" if c.detail else "")
            out[i] = {"urgency": urgency, "reason": reason, "message": message, "mismatch": mismatch,
                      "change": it.get("change") is True and c.source == "mail"}
        return out


@dataclass
class Outgoing:
    """One accepted item waiting to be told in this pass (delivery happens per group, after the loop)."""
    c: Candidate
    urgency: str
    reason: str
    message: str
    change: bool


@dataclass
class Entry:
    """One line of a group: the first of several duplicates and how many there were (`items`: all of them, for the log)."""
    item: Outgoing
    items: list[Outgoing] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class Group:
    key: tuple[str, str]             # ("mail", account) | ("other", "")
    entries: list[Entry] = field(default_factory=list)

    @property
    def is_mail(self) -> bool:
        return self.key[0] == "mail"

    @property
    def items(self) -> list[Outgoing]:
        return [o for e in self.entries for o in e.items]


def mailbox_names(accounts: list[dict[str, Any]]) -> dict[str, str]:
    """How a message names each mailbox, as the briefing does: the provider's name, or the address when two mailboxes share a provider
    (else the account name)."""
    from .briefing_sources import BRAND   # not at the top: briefing_sources imports this module

    out: dict[str, str] = {}
    for a in accounts:
        provider = str(a.get("provider") or "")
        shared = sum(1 for b in accounts if b.get("provider") == a.get("provider")) > 1
        out[a["name"]] = (str(a.get("address") or "") if shared else "") or BRAND.get(provider) or str(a.get("title") or "") or a["name"]
    return out


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _same(a: Outgoing, b: Outgoing) -> bool:
    """The same thing told twice: equal message text, or (mail) equal sender and subject."""
    if _norm(a.message) == _norm(b.message):
        return True
    if a.c.source == "mail" and b.c.source == "mail":
        sender = _norm(str(a.c.extra.get("from") or ""))
        return bool(sender) and sender == _norm(str(b.c.extra.get("from") or "")) and _norm(a.c.title) == _norm(b.c.title)
    return False


def group_outbox(outbox: list[Outgoing]) -> list[Group]:
    """The accepted items of one pass as the messages that go out: one group per mailbox for mail, one for everything else, in the
    order each group's first item appears; inside a group the order is kept and duplicates are folded into one entry with a count."""
    groups: dict[tuple[str, str], Group] = {}
    for o in outbox:
        key = ("mail", str(o.c.extra.get("account") or "")) if o.c.source == "mail" else ("other", "")
        g = groups.setdefault(key, Group(key))
        for e in g.entries:
            if _same(e.item, o):
                e.items.append(o)
                break
        else:
            g.entries.append(Entry(o, [o]))
    return list(groups.values())


def _line(e: Entry) -> str:
    m = e.item.message
    if e.count <= 1:
        return m
    return m + (t(" ({n} mails)", n=e.count) if e.item.c.source == "mail" else t(" ({n} times)", n=e.count))


def group_text(entries: list[Entry], header: str = "") -> str:
    """The message of one group: the item's own words when it is alone, else one "• " line per entry; `header` (a mailbox's name) is a
    first line of its own."""
    body = _line(entries[0]) if len(entries) == 1 else "\n".join("• " + _line(e) for e in entries)
    return f"{header}\n{body}" if header else body


def mail_notice(sender: str, subject: str, limit: int = MAIL_NOTICE_CHARS) -> str:
    """The notification title of a mail: "New mail · <sender> · <subject>" (in the owner's language), at most `limit` characters: the
    sender by its name (else the address before the @) up to 24 characters, the subject cut with "…" to what is left."""
    name, addr = parseaddr(sender or "")
    who = " ".join((name or addr.split("@")[0] or addr or "?").split())
    who = who if len(who) <= 24 else who[:23].rstrip() + "…"
    subj = " ".join((subject or "").split()) or t("(no subject)")
    room = limit - len(t("New mail · {sender} · {subject}", sender=who, subject=""))
    if len(subj) > room:
        subj = subj[:max(room - 1, 1)].rstrip() + "…"
    return t("New mail · {sender} · {subject}", sender=who, subject=subj)[:limit]


def _due_from(text: str, now: float, tz: ZoneInfo) -> float | None:
    """A "HH:MM" the model read from an item -> today's (local) unix time, or None."""
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if not m:
        return None
    d = datetime.fromtimestamp(now, tz).replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    return d.timestamp()


# ---------------------------------------------------------------------------------------------------------------- API
class ProactiveIn(BaseModel):
    enabled: bool | None = None
    wake_start: str | None = None
    wake_end: str | None = None
    every_minutes: int | None = None
    daily_regular: int | None = None
    daily_total: int | None = None
    daily_now: int | None = None
    unanswered_limit: int | None = None
    pause_hours: int | None = None
    pause_max_days: int | None = None
    auto_hours: bool | None = None
    weekend_start: str | None = None
    weekend_end: str | None = None
    ahead_days: list[int] | None = None
    daily_change: int | None = None
    spam_skim: bool | None = None
    calendar_check: bool | None = None
    change_alerts: bool | None = None
    mail_check: bool | None = None
    rechecks: bool | None = None
    recheck_per_day: int | None = None
    offers: bool | None = None


def proactive_routes(app: FastAPI, proactive: Proactive, auth: Callable[..., Any]) -> None:
    """The PROACTIVE API (contracts in this module's docstring and next to serialize())."""

    @app.get("/api/proactive", dependencies=[Depends(auth)])
    async def get_proactive() -> dict[str, Any]:
        return proactive.public()

    @app.put("/api/proactive", dependencies=[Depends(auth)])
    async def put_proactive(body: ProactiveIn) -> dict[str, Any]:
        try:
            return proactive.update(**body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/proactive/log", dependencies=[Depends(auth)])
    async def get_proactive_log(limit: int = 50, before: int | None = None) -> dict[str, Any]:
        items = proactive.log(limit, before)
        return {"items": items, "next_before": items[-1]["id"] if items and len(items) >= max(1, min(limit, 200)) else None}

    @app.post("/api/proactive/hours/{sid}/accept", dependencies=[Depends(auth)])
    async def accept_hours(sid: str) -> dict[str, Any]:
        try:
            return proactive.answer_hours(sid, True)
        except KeyError:
            raise HTTPException(404, "no such suggestion") from None

    @app.post("/api/proactive/hours/{sid}/keep", dependencies=[Depends(auth)])
    async def keep_hours(sid: str) -> dict[str, Any]:
        try:
            return proactive.answer_hours(sid, False)
        except KeyError:
            raise HTTPException(404, "no such suggestion") from None

    @app.post("/api/proactive/hours/learned/{kind}/apply", dependencies=[Depends(auth)])
    async def apply_learned_hours(kind: str) -> dict[str, Any]:
        try:
            return proactive.apply_learned(kind)
        except KeyError:
            raise HTTPException(404, "no learned hours to apply for that day type") from None

    @app.post("/api/proactive/run", dependencies=[Depends(auth)])
    async def run_proactive() -> dict[str, Any]:
        if not proactive.config()["enabled"]:
            raise HTTPException(409, "proactive messages are off; turn them on first")
        if proactive.lock.locked():
            raise HTTPException(409, "a pass is already running")
        return await proactive.run()
