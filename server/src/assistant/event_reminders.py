"""Reminders before calendar events for an item of the owner's checklist (owner decision 2026-11-19, card
p-checklist-event-reminders).

An item that asks to hear some time before each of the owner's calendar events is served by reminders tied to the events themselves,
not by a polling job: one reminder per event at its start minus the lead, in step with the calendars, quiet otherwise. Nothing here
knows what an item says: the main agent judges, when the item is added (checklist.py SETUP) or asked for in the chat, that the item
asks for this, and makes one job with `event_reminders_set` (lead minutes; optionally words its events must contain, the calendars
they must come from, and the owner's note), which also links the item to it (checklist_links, job kind KIND).

How it runs (code only, no model call per event):
- the jobs are rows of `event_reminders`. A job runs only while a checklist item links it (`linked`); one that no item links any more
  is deleted at the next tick. The owner taking the item off deletes it at once (checklist.py, JobKind.remove);
- a tick every 60 s (server.py) does nothing while no job is linked. Otherwise it reads every calendar Kelo reads (`read`:
  briefing_sources.Sources.events, the owner's switches and per-connector limits apply) at most every READ_EVERY seconds, for the next
  longest lead + 2 x READ_EVERY; timed events only. A calendar that could not be read keeps the events of its last good read (a failed
  read is not a cancellation);
- an event is due for a job when one of its leads has come and the event has not started. Before telling, a read older than FRESH
  seconds is done again, so a moved or cancelled event is never told under its old time. Leads that came together (an event found
  late) make one reminder. Sent reminders are rows of `event_reminders_sent` keyed by job, event, start and lead: an event moved to
  another time is reminded again at its new time; the rows go a day after their event's start;
- the reminder is a notification of type "follow_reminder" (as follow-through's lead reminders; the released apps show it with these
  words): title = the event's title, body = follow's format_reminder ("<title> at <time> (<place>)") and the owner's note. In quiet
  hours it waits for the waking hours unless the event starts within NOW_WINDOW (as follow-through does).

Authority (the owner's rule: outside text never makes Kelo create jobs): making, changing or removing a job is a change to Kelo's own
settings, so it asks on a card unless the owner asked for it (connect_tools.asked_in_chat: their chat, or the checklist setup turn
that carries their request, with no outside text read yet in that run); such an approval never becomes a standing rule
(activity.rule_kind, schedule_rule_ok). What a job does per event is fixed here: one notification to the owner from fixed words, the
event's own fields and the owner's note. No model reads the calendar text on this way and nothing else can be done with it, so the
owner's go-ahead for the item covers exactly these reminders. Every other path (a scheduled run that read a calendar, a mail, a page)
still asks before it makes a job.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .agent import Tool
from .briefing_sources import search_terms
from .checklist import item_key
from .follow.runner import NOW_WINDOW, format_reminder
from .i18n import t
from .tools.connect_tools import asked_in_chat

log = logging.getLogger("assistant.event_reminders")

KIND = "event_reminder"          # the checklist's job kind (checklist.JOB_KINDS)
READ_EVERY = 900.0               # seconds between reads of the calendars while a job is linked
FRESH = 120.0                    # a read older than this is done again before a reminder goes out
MAX_LEADS = 3
MAX_LEAD_MINUTES = 7 * 24 * 60
MAX_NOTE = 200
MAX_NAME = 60
KEEP_SENT_SECONDS = 86400.0      # a sent row goes this long after its event's start

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '', item TEXT NOT NULL, leads TEXT NOT NULL,
    words TEXT NOT NULL DEFAULT '[]', calendars TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
    updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS event_reminders_sent (
    job_id INTEGER NOT NULL, event_key TEXT NOT NULL, starts_at INTEGER NOT NULL, lead INTEGER NOT NULL, sent_at REAL NOT NULL,
    PRIMARY KEY (job_id, event_key, starts_at, lead));
"""

Reader = Callable[[datetime, datetime], Awaitable[tuple[list[dict[str, Any]], list[str]]]]   # (events, ids of calendars that failed)


class ReminderError(ValueError):
    pass


def lead_word(minutes: int) -> str:
    if minutes % 1440 == 0:
        return t("{n} d", n=minutes // 1440)
    if minutes % 60 == 0:
        return t("{n} h", n=minutes // 60)
    return t("{n} min", n=minutes)


def lead_words(leads: list[int]) -> str:
    """"30 min before each event", "1 d, 30 min before each event" (the longest lead first), in the owner's language."""
    return t("{lead} before each event", lead=", ".join(lead_word(m) for m in sorted(leads, reverse=True)))


def clean_leads(value: Any) -> list[int]:
    """lead_minutes as distinct whole minutes, longest first."""
    if value in (None, "", []):
        raise ReminderError("give lead_minutes: how many minutes before each event")
    out: list[int] = []
    for v in value if isinstance(value, (list, tuple)) else [value]:
        if isinstance(v, bool):
            raise ReminderError("lead_minutes must be whole minutes")
        try:
            m = int(v)
        except (TypeError, ValueError):
            raise ReminderError("lead_minutes must be whole minutes") from None
        if not 1 <= m <= MAX_LEAD_MINUTES:
            raise ReminderError(f"each lead must be 1 to {MAX_LEAD_MINUTES} minutes")
        if m not in out:
            out.append(m)
    if len(out) > MAX_LEADS:
        raise ReminderError(f"give at most {MAX_LEADS} leads")
    return sorted(out, reverse=True)


def event_key(ev: dict[str, Any]) -> str:
    return str(ev.get("id") or f"{ev.get('title') or ''}:{int(float(ev.get('start') or 0))}")


class EventReminders:
    def __init__(self, store: Any, read: Reader, *, notify: Callable[[str, str, str], Any], linked: Callable[[], set[int]],
                 awake: Callable[[float], bool] = lambda _ts: True, tz: Callable[[], Any] = lambda: timezone.utc,
                 clock: Callable[[], float] = time.time):
        """read: every calendar Kelo reads between two times; notify(type, title, body); linked(): the ids of the jobs a checklist
        item links now; awake(ts): inside the owner's waking hours."""
        self.store, self.read, self.notify, self.linked, self.awake, self.tz, self.clock = store, read, notify, linked, awake, tz, clock
        self.cache: list[dict[str, Any]] = []     # the events of the last read (timed ones)
        self.read_at: float | None = None
        self.reach = 0.0                          # how far ahead the last read went (seconds)
        with self.store._lock:
            self.store._db.executescript(SCHEMA)
            self.store._db.commit()

    # ------------------------------------------------------------------ the jobs
    @staticmethod
    def _job(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": int(r["id"]), "name": r["name"], "item": r["item"], "leads": json.loads(r["leads"]), "words": json.loads(r["words"]),
                "calendars": json.loads(r["calendars"]), "note": r["note"], "created_at": r["created_at"]}

    def get(self, jid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM event_reminders WHERE id = ?", (int(jid),))
        return self._job(rows[0]) if rows else None

    def list(self) -> list[dict[str, Any]]:
        return [self._job(r) for r in self.store._all("SELECT * FROM event_reminders ORDER BY id")]

    @staticmethod
    def check(lead_minutes: Any, words: Any = None, calendars: Any = None, note: Any = "", name: Any = "") -> dict[str, Any]:
        """The fields of a job, cleaned; ReminderError says what is wrong."""
        leads = clean_leads(lead_minutes)
        terms = search_terms(words if isinstance(words, list) else [words] if words else [])
        raw = calendars if isinstance(calendars, list) else [calendars] if calendars else []
        cals = list(dict.fromkeys(str(c).strip() for c in raw if str(c).strip()))
        note = " ".join(str(note or "").split())
        if len(note) > MAX_NOTE:
            raise ReminderError(f"the note can be at most {MAX_NOTE} characters")
        return {"leads": leads, "words": terms, "calendars": cals, "note": note, "name": " ".join(str(name or "").split())[:MAX_NAME]}

    def create(self, item: str, lead_minutes: Any, *, words: Any = None, calendars: Any = None, note: Any = "", name: Any = "") -> dict[str, Any]:
        f = self.check(lead_minutes, words, calendars, note, name)
        now = self.clock()
        jid = self.store._run("INSERT INTO event_reminders(name, item, leads, words, calendars, note, created_at, updated_at) "
                              "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                              (f["name"], str(item)[:300], json.dumps(f["leads"]), json.dumps(f["words"], ensure_ascii=False),
                               json.dumps(f["calendars"]), f["note"], now, now)).lastrowid
        self.read_at = None   # the next tick reads the calendars for it
        return self.get(int(jid or 0)) or {}

    def update(self, jid: int, *, item: str | None = None, lead_minutes: Any = None, words: Any = None, calendars: Any = None,
               note: Any = None, name: Any = None) -> dict[str, Any] | None:
        """A field left None keeps its value."""
        cur = self.get(jid)
        if cur is None:
            return None
        f = self.check(cur["leads"] if lead_minutes in (None, "", []) else lead_minutes, cur["words"] if words is None else words,
                       cur["calendars"] if calendars is None else calendars, cur["note"] if note is None else note,
                       cur["name"] if name is None else name)
        self.store._run("UPDATE event_reminders SET name = ?, item = ?, leads = ?, words = ?, calendars = ?, note = ?, updated_at = ? "
                        "WHERE id = ?", (f["name"], str(cur["item"] if item is None else item)[:300], json.dumps(f["leads"]),
                                         json.dumps(f["words"], ensure_ascii=False), json.dumps(f["calendars"]), f["note"], self.clock(),
                                         int(jid)))
        self.read_at = None
        return self.get(jid)

    def delete(self, jid: int) -> bool:
        gone = self.store._run("DELETE FROM event_reminders WHERE id = ?", (int(jid),)).rowcount > 0
        self.store._run("DELETE FROM event_reminders_sent WHERE job_id = ?", (int(jid),))
        return gone

    @staticmethod
    def name_of(job: dict[str, Any]) -> str:
        return job["name"] or lead_words(job["leads"])

    def public(self, jid: int) -> dict[str, Any] | None:
        """The checklist's view of a job (JobKind.get): {"name", "description", "on"}, or None when it is gone."""
        j = self.get(jid)
        if j is None:
            return None
        return {"name": self.name_of(j), "description": lead_words(j["leads"]) + (" · " + ", ".join(j["words"]) if j["words"] else ""),
                "on": True}

    def active(self) -> list[dict[str, Any]]:
        """The jobs a checklist item links now; the others are deleted (their item is gone)."""
        keep = self.linked()
        out = []
        for j in self.list():
            if j["id"] in keep:
                out.append(j)
            else:
                self.delete(j["id"])
        return out

    @staticmethod
    def matches(job: dict[str, Any], ev: dict[str, Any]) -> bool:
        if job["calendars"] and ev.get("source_id") not in job["calendars"]:
            return False
        text = f"{ev.get('title') or ''} {ev.get('where') or ''}".casefold()
        return not job["words"] or any(w in text for w in job["words"])

    def next_at(self, job: dict[str, Any], now: float) -> float | None:
        """When this job's next reminder is due, by the last read (None: none known)."""
        times = [float(e["start"]) - m * 60 for e in self.cache if self.matches(job, e) for m in job["leads"]
                 if float(e["start"]) - m * 60 > now]
        return min(times) if times else None

    # ------------------------------------------------------------------ the tick
    async def tick(self) -> int:
        """One pass (every 60 s): the reminders that are due go out. Returns how many went out."""
        try:
            jobs = self.active()
        except Exception:  # noqa: BLE001 - the links could not be read: nothing is deleted or told this time
            log.exception("event reminders: reading the checklist links")
            return 0
        if not jobs:
            self.cache, self.read_at, self.reach = [], None, 0.0
            return 0
        now = self.clock()
        reach = max(m for j in jobs for m in j["leads"]) * 60 + 2 * READ_EVERY
        if self.read_at is None or now - self.read_at >= READ_EVERY or reach > self.reach:
            await self._read(now, reach)
        due = self._due(jobs, now)
        if due and self.read_at is not None and now - self.read_at > FRESH:
            await self._read(now, reach)
            due = self._due(jobs, now)
        told = 0
        for job, ev, leads in due:
            start = float(ev["start"])
            if not self.awake(now) and start - now > NOW_WINDOW:
                continue   # quiet hours: it waits for the waking hours, or until the event is close
            self._tell(job, ev, now)
            for m in leads:
                self.store._run("INSERT OR IGNORE INTO event_reminders_sent(job_id, event_key, starts_at, lead, sent_at) "
                                "VALUES(?, ?, ?, ?, ?)", (job["id"], event_key(ev), int(start), m, now))
            told += 1
        self.store._run("DELETE FROM event_reminders_sent WHERE starts_at < ?", (int(now - KEEP_SENT_SECONDS),))
        return told

    async def _read(self, now: float, reach: float) -> None:
        try:
            events, failed = await self.read(datetime.fromtimestamp(now - 60, timezone.utc), datetime.fromtimestamp(now + reach, timezone.utc))
        except Exception:  # noqa: BLE001 - the last read stays; tried again after READ_EVERY
            log.exception("event reminders: reading the calendars")
            self.read_at = now
            return
        bad = set(failed)
        fresh = [e for e in events if not e.get("all_day") and e.get("start")]
        keys = {event_key(e) for e in fresh}
        kept = [e for e in self.cache if e.get("source_id") in bad and event_key(e) not in keys]
        self.cache, self.read_at, self.reach = fresh + kept, now, reach

    def _due(self, jobs: list[dict[str, Any]], now: float) -> list[tuple[dict[str, Any], dict[str, Any], list[int]]]:
        sent = {(r["job_id"], r["event_key"], r["starts_at"], r["lead"])
                for r in self.store._all("SELECT job_id, event_key, starts_at, lead FROM event_reminders_sent")}
        out = []
        for j in jobs:
            for ev in self.cache:
                start = float(ev["start"])
                if start <= now or not self.matches(j, ev):
                    continue
                key = event_key(ev)
                leads = [m for m in j["leads"] if start - m * 60 <= now and (j["id"], key, int(start), m) not in sent]
                if leads:
                    out.append((j, ev, leads))
        return out

    def _tell(self, job: dict[str, Any], ev: dict[str, Any], now: float) -> None:
        tz = self.tz()
        start = datetime.fromtimestamp(float(ev["start"]), tz)
        when = start.strftime("%H:%M") if start.date() == datetime.fromtimestamp(now, tz).date() else start.strftime("%Y-%m-%d %H:%M")
        title = str(ev.get("title") or "").strip() or self.name_of(job)
        body = format_reminder(title, when, str(ev.get("where") or "").strip())
        if job["note"]:
            body += "\n" + job["note"]
        try:
            self.notify("follow_reminder", title[:120], body)
        except Exception:  # noqa: BLE001 - one notification failing never stops the others
            log.exception("event reminders: notifying")


# ---------------------------------------------------------------------------------------------------------------- the agent's tools
def event_reminder_tools(reminders: EventReminders, checklist: Callable[[], Any], calendar_ids: Callable[[], list[str]]) -> list[Tool]:
    """event_reminders_set / _list / _remove (`checklist`: the Checklist, made after the tool list in server.py; `calendar_ids`: the
    ids of the connected calendars, as briefing_sources lists them)."""

    def jid(a: dict[str, Any]) -> int:
        try:
            return int(str(a.get("id", "")).lstrip("#"))
        except ValueError:
            return 0

    def listed_item(a: dict[str, Any]) -> tuple[str | None, str]:
        """(the item as the list writes it, or None; the list for an error)."""
        ck = checklist()
        items = ck.items() if ck is not None else []
        by_key = {item_key(i): i for i in items}
        return by_key.get(item_key(str(a.get("item") or ""))), "; ".join(items) or "nothing"

    def validate_set(a: dict[str, Any]) -> str | None:
        if checklist() is None:
            return "the checklist is not available on this server"
        item, on_list = listed_item(a)
        if item is None:
            return (f"{str(a.get('item') or '')[:80]!r} is not on the checklist. Add it as a line of CHECKLIST.md first. "
                    f"On the list: {on_list}")
        cur = reminders.get(jid(a)) if a.get("id") not in (None, "") else None
        if a.get("id") not in (None, "") and cur is None:
            return f"there is no event reminder #{a.get('id')}"
        try:
            f = reminders.check(a.get("lead_minutes") if a.get("lead_minutes") not in (None, "", []) or cur is None else cur["leads"],
                                a.get("words"), a.get("calendars"), a.get("note") or "", a.get("name") or "")
        except ReminderError as e:
            return str(e)
        known = calendar_ids()
        unknown = [c for c in f["calendars"] if c not in known]
        if unknown:
            return f"no calendar {', '.join(unknown)}. Calendars: {', '.join(known) or 'none connected'}"
        return None

    async def describe_set(a: dict[str, Any]) -> str:
        try:
            timing = lead_words(clean_leads(a.get("lead_minutes")))
        except ReminderError:
            cur = reminders.get(jid(a)) if a.get("id") not in (None, "") else None
            timing = lead_words(cur["leads"]) if cur else ""
        words = search_terms(a.get("words") if isinstance(a.get("words"), list) else [])
        return " · ".join(x for x in (t("Reminders before events"), timing, ", ".join(words), str(a.get("item") or "")[:80]) if x)

    async def set_(a: dict[str, Any]) -> str:
        ck = checklist()
        if ck is None:
            return "error: the checklist is not available on this server"
        item, on_list = listed_item(a)
        if item is None:
            return (f"error: {str(a.get('item') or '')[:80]!r} is not on the checklist. Add it as a line of CHECKLIST.md first. "
                    f"On the list: {on_list}")
        try:
            if a.get("id") not in (None, ""):
                j = reminders.update(jid(a), item=item, lead_minutes=a.get("lead_minutes"), words=a.get("words"),
                                     calendars=a.get("calendars"), note=a.get("note"), name=a.get("name"))
                if j is None:
                    return f"error: there is no event reminder #{a.get('id')}"
            else:
                j = reminders.create(item, a.get("lead_minutes"), words=a.get("words"), calendars=a.get("calendars"),
                                     note=a.get("note") or "", name=a.get("name") or "")
        except ReminderError as e:
            return f"error: {e}"
        ck.link(item, f"{KIND}:{j['id']}")   # no await between the save and the link: the tick never sees it unlinked
        return (f"Saved event reminder #{j['id']} '{reminders.name_of(j)}' for the checklist item '{item}': one reminder per event, "
                f"{', '.join(str(m) for m in j['leads'])} minutes before it. It follows the calendars by itself (new, moved and "
                "cancelled events) and says nothing when there is no event. The item is linked to it; no checklist_link is needed.")

    async def list_(a: dict[str, Any]) -> str:
        jobs = reminders.list()
        if not jobs:
            return "There are no event reminders."
        now, tz = reminders.clock(), reminders.tz()
        lines = []
        for j in jobs:
            nxt = reminders.next_at(j, now)
            when = datetime.fromtimestamp(nxt, tz).strftime("%Y-%m-%d %H:%M") if nxt else "none known yet"
            lines.append(f"- #{j['id']} '{reminders.name_of(j)}': {', '.join(str(m) for m in j['leads'])} minutes before each event"
                         + (f"; only events with {', '.join(repr(w) for w in j['words'])}" if j["words"] else "")
                         + (f"; only calendars {', '.join(j['calendars'])}" if j["calendars"] else "")
                         + (f"; note: {j['note']}" if j["note"] else "")
                         + f"; for the checklist item '{j['item']}'; next reminder {when}")
        return "Event reminders:\n" + "\n".join(lines)

    async def describe_remove(a: dict[str, Any]) -> str:
        j = reminders.get(jid(a))
        return t("Reminders before events") + (f" · {reminders.name_of(j)}" if j else "")

    async def remove(a: dict[str, Any]) -> str:
        return "Removed." if reminders.delete(jid(a)) else f"error: there is no event reminder #{a.get('id')}"

    props = {
        "item": {"type": "string", "description": "the checklist item this is for, as it is written in the list"},
        "lead_minutes": {"type": "array", "items": {"type": "integer"},
                         "description": f"how many minutes before each event (1 to {MAX_LEADS} leads, each up to 7 days)"},
        "words": {"type": "array", "items": {"type": "string"},
                  "description": "only events whose title or place contains one of these (any case); leave out for every timed event"},
        "calendars": {"type": "array", "items": {"type": "string"},
                      "description": "only events of these calendars (ids as briefing_sources lists them); leave out for all of them"},
        "note": {"type": "string", "description": "the owner's own words to add to each reminder, if the item has some"},
        "name": {"type": "string", "description": "a short name in the owner's language (default: the timing)"},
        "id": {"type": "integer", "description": "to change an existing one"}}
    return [
        Tool("event_reminders_set",
             "Remind the owner a set time before each of their calendar events, for an item of their checklist (CHECKLIST.md) that asks "
             "for that: one reminder per event at its start minus the lead, made by Kelo without a model run. It follows every calendar "
             "Kelo reads by itself (new, moved and cancelled events; timed events only) and is quiet when there is no event, so no "
             "schedule or other job is needed for it. Add the line to CHECKLIST.md first; this links the item to it (no checklist_link "
             "needed), and it runs only while the item is on the list. You do not need to read the calendar first. Give id to change one.",
             {"type": "object", "properties": props, "required": ["item"]}, set_, requires_approval=True, validate=validate_set,
             describe=describe_set, auto_approve=lambda a: asked_in_chat()),
        Tool("event_reminders_list",
             "The reminders before calendar events (event_reminders_set): each with its timing, what it is limited to, the checklist "
             "item it serves and when its next reminder is due.",
             {"type": "object", "properties": {}}, list_, read_only=True),
        Tool("event_reminders_remove",
             "Stop and delete reminders before calendar events by id. The checklist item it served is then looked at at the send times.",
             {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}, remove, requires_approval=True,
             describe=describe_remove, auto_approve=lambda a: asked_in_chat()),
    ]
