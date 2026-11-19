"""The phone's own calendar (Android CalendarContract), shared by the app as a snapshot.

When the user turns it on, the app reads the events in a window (7 days back to 60 days ahead, recurring events as their occurrences,
declined ones left out) and sends the whole snapshot: on open/resume, after the phone's calendar changes and from its 15-minute
background job. Each phone (paired device, or "owner" for the access key) has one snapshot that the next upload replaces; turning the
feature off deletes it. Snapshots are sealed at rest with AES-256-GCM (a key derived from the store's master key for personal data,
bound to the device it came from). Read-only by default; when the user also turns on "일정 추가·수정도 허용" (WRITE_CALENDAR), the agent's
tools phone_calendar_add/update/delete (phone_data.py) ask on an approval card and the phone itself makes the change in the calendar
the user picked (write_calendar), then sends a fresh snapshot.

PUT    /api/phone-calendar   {"device_name"?, "timezone"?, "window_start": unix, "window_end": unix, "events": [Event],
                              "can_write"?: bool, "write_calendar"?: {"id", "name"}}   (writing: only when the user allowed it in the app)
                             -> {"ok", "count", "uploaded_at"}           (413-free: more than MAX_EVENTS are cut, title etc. clipped)
       Event: {"id"?: phone event id, "title", "start": unix, "end": unix, "all_day": bool, "day"?: "YYYY-MM-DD", "last_day"?: "YYYY-MM-DD",
               "location"?, "calendar"?, "attendees"?: int, "notes"?: str (500), "account"?: str (the account the calendar syncs from),
               "account_type"?: str ("com.google", "local", ...), "own"?: bool (the owner organizes it on a calendar they own; older apps
               send none: those events are read as others' text)}
       Older apps send none of the optional fields; everything works without them.
DELETE /api/phone-calendar   -> {"ok"}                                   (the feature turned off on this phone: its snapshot is wiped)
GET    /api/phone-calendar   -> {"shared": bool, "uploaded_at", "count", "window_start", "window_end",
                                 "devices": [{"name", "uploaded_at", "count", "this": bool}]}
The agent's tool phone_calendar_events(start, end, query?) reads every phone's snapshot together and says how fresh each one is.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .agent import Ours
from .agent.fence import data, marked
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .agent import Tool
from .event_search import matches, no_match_line, query_terms
from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS phone_calendar (
    owner TEXT PRIMARY KEY, device_name TEXT NOT NULL DEFAULT '', data TEXT NOT NULL, uploaded_at REAL NOT NULL,
    window_start REAL NOT NULL DEFAULT 0, window_end REAL NOT NULL DEFAULT 0, count INTEGER NOT NULL DEFAULT 0);
"""
MAX_EVENTS = 3000
MAX_SHOWN = 150


def _zone(name: str | None) -> Any:
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0 < f < 4_102_444_800 else None   # before 2100


def _day(v: Any) -> str:
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError:
        return ""


def clean_event(e: Any) -> dict[str, Any] | None:
    if not isinstance(e, dict):
        return None
    start, end = _num(e.get("start")), _num(e.get("end"))
    if start is None:
        return None
    end = end if end is not None and end >= start else start
    out = {"title": str(e.get("title") or "").strip()[:300], "start": start, "end": end, "all_day": bool(e.get("all_day")),
           "location": str(e.get("location") or "").strip()[:300], "calendar": str(e.get("calendar") or "").strip()[:120]}
    out["own"] = e.get("own") is True   # only an app that says so: the owner's own event (a phone snapshot has no organizer data yet)
    for key, limit in (("notes", 500), ("account", 120), ("account_type", 80)):
        value = str(e.get(key) or "").strip()
        if value:
            out[key] = value[:limit]
    try:
        out["attendees"] = max(0, min(int(e.get("attendees") or 0), 10_000))
    except (TypeError, ValueError):
        out["attendees"] = 0
    try:
        if e.get("id") is not None:
            out["id"] = max(0, int(e.get("id")))   # the phone's own event id: what a change or a deletion names
    except (TypeError, ValueError):
        pass
    if out["all_day"]:
        out["day"], out["last_day"] = _day(e.get("day")), _day(e.get("last_day") or e.get("day"))
    return out


def ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{round(s / 60)} minutes ago"
    if s < 172_800:
        return f"{round(s / 3600)} hours ago"
    return f"{round(s / 86400)} days ago"


class PhoneCalendars:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    @staticmethod
    def _aad(owner: str) -> str:
        return f"phone-calendar:{owner}"

    def put(self, owner: str, body: dict[str, Any]) -> dict[str, Any]:
        raw = body.get("events")
        if not isinstance(raw, list):
            raise ValueError("events must be a list")
        events = [e for e in (clean_event(x) for x in raw[:MAX_EVENTS]) if e is not None]
        now = self.clock()
        ws, we = _num(body.get("window_start")) or 0.0, _num(body.get("window_end")) or 0.0
        data = {"timezone": str(body.get("timezone") or "")[:64], "events": events, "can_write": bool(body.get("can_write"))}
        wc = body.get("write_calendar")
        if data["can_write"] and isinstance(wc, dict) and wc.get("id") is not None:
            try:
                data["write_calendar"] = {"id": int(wc["id"]), "name": str(wc.get("name") or "").strip()[:120]}
            except (TypeError, ValueError):
                data["can_write"] = False
        else:
            data["can_write"] = False
        sealed = self.store.data_sealer.seal(json.dumps(data, ensure_ascii=False, separators=(",", ":")), self._aad(owner))
        name = str(body.get("device_name") or "").strip()[:40]
        self.store._run("INSERT INTO phone_calendar(owner, device_name, data, uploaded_at, window_start, window_end, count) VALUES(?,?,?,?,?,?,?) "
                        "ON CONFLICT(owner) DO UPDATE SET device_name = excluded.device_name, data = excluded.data, uploaded_at = excluded.uploaded_at, "
                        "window_start = excluded.window_start, window_end = excluded.window_end, count = excluded.count",
                        (owner, name, sealed, now, ws, we, len(events)))
        return {"ok": True, "count": len(events), "uploaded_at": now}

    def delete(self, owner: str) -> bool:
        return self.store._run("DELETE FROM phone_calendar WHERE owner = ?", (owner,)).rowcount > 0

    def rows(self) -> list[dict[str, Any]]:
        return self.store._all("SELECT owner, device_name, uploaded_at, window_start, window_end, count FROM phone_calendar ORDER BY uploaded_at DESC")

    def status(self, owner: str) -> dict[str, Any]:
        rows = self.rows()
        mine = next((r for r in rows if r["owner"] == owner), None)
        return {"shared": mine is not None, "uploaded_at": mine["uploaded_at"] if mine else None, "count": mine["count"] if mine else 0,
                "window_start": mine["window_start"] if mine else None, "window_end": mine["window_end"] if mine else None,
                "devices": [{"name": r["device_name"], "uploaded_at": r["uploaded_at"], "count": r["count"], "this": r["owner"] == owner} for r in rows]}

    def snapshots(self) -> list[dict[str, Any]]:
        out = []
        for r in self.store._all("SELECT * FROM phone_calendar ORDER BY uploaded_at DESC"):
            plain = self.store.data_sealer.open(r["data"], self._aad(r["owner"]))
            if plain is None:
                continue   # the master key changed or the row was altered: treated as not shared
            try:
                data = json.loads(plain)
            except ValueError:
                continue
            out.append({**{k: r[k] for k in ("owner", "device_name", "uploaded_at", "window_start", "window_end")},
                        "timezone": data.get("timezone", ""), "events": data.get("events", []),
                        "can_write": bool(data.get("can_write")), "write_calendar": data.get("write_calendar")})
        return out

    def events(self, start: float, end: float, query: Any = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(events overlapping [start, end) from every phone, one copy of each, by start time; the snapshots they came from).
        `query`: words (event_search.query_terms); an event with any of them in its title, place, notes or calendar is kept."""
        snaps = self.snapshots()
        terms = query_terms(query)
        seen: set[tuple[str, float]] = set()
        out = []
        for s in snaps:
            for e in s["events"]:
                if e["end"] < start or e["start"] >= end or (e["end"] == e["start"] and e["start"] < start):
                    continue
                if terms and not matches(terms, e["title"], e["location"], e.get("notes", ""), e["calendar"]):
                    continue
                key = (e["title"].lower(), e["start"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({**e, "device": s["device_name"], "owner": s["owner"]})
        out.sort(key=lambda e: e["start"])
        return out, snaps

    def upcoming(self, hours: float = 24) -> list[dict[str, Any]]:
        now = self.clock()
        evs, _ = self.events(now, now + hours * 3600)
        return [{"id": f"phone:{int(e['start'])}:{e['title'][:40]}", "title": e["title"], "start": e["start"], "where": e["location"]}
                for e in evs if not e["all_day"]]


def _when(e: dict[str, Any], tz: Any) -> str:
    if e["all_day"]:
        first, last = e.get("day") or datetime.fromtimestamp(e["start"], tz).strftime("%Y-%m-%d"), e.get("last_day") or ""
        return f"{first} – {last}, all day" if last and last != first else f"{first}, all day"
    a, b = datetime.fromtimestamp(e["start"], tz), datetime.fromtimestamp(e["end"], tz)
    if b <= a:
        return a.strftime("%Y-%m-%d %H:%M")
    return f"{a.strftime('%Y-%m-%d %H:%M')}–{b.strftime('%H:%M')}" if a.date() == b.date() else \
        f"{a.strftime('%Y-%m-%d %H:%M')} – {b.strftime('%Y-%m-%d %H:%M')}"


def _parse(text: str, tz: Any, end_of_day: bool = False) -> datetime:
    s = str(text).strip().replace(" ", "T", 1)
    if len(s) == 10:
        d = date.fromisoformat(s)
        day = datetime(d.year, d.month, d.day, tzinfo=tz)
        return day + timedelta(days=1) if end_of_day else day
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def phone_calendar_tools(cals: PhoneCalendars, timezone_name: Callable[[], str | None]) -> list[Tool]:
    async def phone_calendar_events(args: dict[str, Any]) -> str:
        tz = _zone(timezone_name())
        try:
            start = _parse(args["start"], tz) if args.get("start") else datetime.now(tz)
            end = _parse(args["end"], tz, end_of_day=True) if args.get("end") else start + timedelta(days=7)
        except (ValueError, TypeError):
            return "error: give start and end as a date (2026-11-08) or a local time (2026-11-08T15:00)"
        if end <= start:
            return "error: end is before start"
        terms = query_terms(args.get("query"))
        evs, snaps = cals.events(start.timestamp(), end.timestamp(), terms)
        if not snaps:
            return Ours("The phone calendar is not shared. The user can turn it on in the Kelo app (My → Permissions → Phone calendar); "
                    "it stays on that phone and is read-only.")
        now = cals.clock()
        lines = []
        for s in snaps:
            covers = ""
            if s["window_start"] and s["window_end"]:
                covers = (f", covers {datetime.fromtimestamp(s['window_start'], tz).strftime('%Y-%m-%d')} to "
                          f"{datetime.fromtimestamp(s['window_end'], tz).strftime('%Y-%m-%d')}")
                if start.timestamp() < s["window_start"] or end.timestamp() > s["window_end"]:
                    covers += " (part of the asked range is outside it)"
            stale = " — old, the phone has not sent an update for a while" if now - s["uploaded_at"] > 86400 else ""
            lines.append(f"Snapshot from {s['device_name'] or 'a phone'}, updated {ago(now - s['uploaded_at'])}{covers}{stale}.")
        zone = getattr(tz, "key", "UTC")
        span = f"between {start.strftime('%Y-%m-%d %H:%M')} and {end.strftime('%Y-%m-%d %H:%M')} ({zone})"
        if not evs:
            if terms:
                total = len(cals.events(start.timestamp(), end.timestamp())[0])
                lines.append(no_match_line(terms, span, total))
            else:
                lines.append(f"No events {span}.")
            return Ours("\n".join(lines))
        many = len(snaps) > 1
        lines.append(f"Events ({zone}), {len(evs)}{' (first ' + str(MAX_SHOWN) + ' shown)' if len(evs) > MAX_SHOWN else ''}:")
        for e in evs[:MAX_SHOWN]:
            acct = e.get("account", "")
            shown_cal = e["calendar"] if e["calendar"] and e["calendar"] != acct else ""
            account = ""
            if acct and e.get("account_type", "").lower() != "local":
                account = f" · account {acct}" + (" (Google)" if e.get("account_type") == "com.google" else "")
            # an event the app did not mark as the owner's own: its title, place and calendar name are others' text (fenced by the loop)
            title, place, cal_name = (e["title"], e["location"], shown_cal) if e.get("own") else (
                data(e["title"]) if e["title"] else "", data(e["location"]) if e["location"] else "", data(shown_cal) if shown_cal else "")
            extra = "".join([f" · at {place}" if e["location"] else "", f" · {e['attendees']} people" if e["attendees"] else "",
                             f" · {cal_name}" if shown_cal else "", account, f" · on {e['device']}" if many and e["device"] else "",
                             f" · event {e['id']}" if e.get("id") else ""])
            lines.append(f"- {_when(e, tz)} · {title or '(no title)'}{extra}")
        text = "\n".join(lines)
        return text if marked(text) else Ours(text)

    when = "YYYY-MM-DD or YYYY-MM-DDTHH:MM, the user's time zone"
    return [Tool("phone_calendar_events", "Read events from the calendar on the user's phone only (a shared snapshot; default: the next 7 days), "
                 "with each event's id (needed to change one) and how fresh the snapshot is. For what is on across all calendars use calendar_events.",
                 {"type": "object", "properties": {"start": {"type": "string", "description": when}, "end": {"type": "string", "description": when},
                                                   "query": {"type": "array", "items": {"type": "string"},
                                                             "description": "words to look for; an event whose title, place, notes or calendar "
                                                                            "contains any one of them is shown (any case)"}}},
                 phone_calendar_events, read_only=True, untrusted=True, source="calendar", trail="calendar")]
