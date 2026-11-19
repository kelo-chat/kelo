"""Google Calendar with a Google sign-in (the same operator-registered Google client as Gmail, see google_web.py).

Connecting asks Google only for the calendar scopes (calendar.readonly + calendar.events) with include_granted_scopes, so an account that
already gave Gmail access keeps it and the same account can hold both. Each connected account is one vault item ("google-calendar",
"google-calendar-2", ...) holding {"u": address, "scopes": [granted scopes], "oauth": {access_token, refresh_token, expires_at}};
nothing of it ever reaches the model. Tokens are renewed like Gmail's: here when this server holds the client secret, otherwise through
the operator's broker (POST /broker/google/refresh).

The assistant's tools: gcal_calendars, gcal_events (read, free) and gcal_create_event / gcal_update_event / gcal_delete_event (each one
asks the user on an approval card that says what will change, in words).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .agent import Ours, Tool
from .agent.fence import data, marked
from .event_search import matches, no_match_line, query_terms
from .google_web import CALENDAR_SCOPES, GoogleWeb, GoogleWebError
from .i18n import t
from .netguard import NetGuardError, Reply, fetch
from .tools.connect_tools import owner_request
from .store import Store

Fetcher = Callable[..., Awaitable[Reply]]
# Google's event types the owner makes themself (the API's enum). "fromGmail" events are made from mail and "birthday" ones from contacts:
# their text came from somewhere else, so they are read as others' text.
OWN_EVENT_TYPES = ("default", "focusTime", "outOfOffice", "workingLocation")
API = "https://www.googleapis.com/calendar/v3"
HOST = "www.googleapis.com"
ITEM = "google-calendar"
READ_SCOPES = {*CALENDAR_SCOPES, "https://www.googleapis.com/auth/calendar"}
WRITE_SCOPES = {"https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar"}
MAX_CALENDARS = 10
MAX_EVENTS = 50
PAGE_EVENTS = 250     # per page when a long span is searched (calendar_events with query)
SEARCH_PAGES_GOOGLE = 8   # pages read for a search: Google matches words itself no longer, so every event of the span is read and filtered here


class CalendarError(Exception):
    """Shown to the user (connecting) or the model (tools) as it is."""


def _zone(name: str | None) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def parse_when(text: str, tz: Any, *, end_of_day: bool = False) -> tuple[datetime, bool]:
    """(an aware datetime, whether only a date was given). Accepts 2026-11-08, 2026-11-08T15:00, 2026-11-08 15:00, with or without an
    offset (a time without one is in the user's time zone)."""
    s = str(text or "").strip().replace(" ", "T", 1)
    if not s:
        raise CalendarError("a date or time is missing")
    if len(s) == 10:
        try:
            d = date.fromisoformat(s)
        except ValueError:
            raise CalendarError(f"{text!r} is not a date like 2026-11-08") from None
        day = datetime(d.year, d.month, d.day, tzinfo=tz)
        return (day + timedelta(days=1) if end_of_day else day), True
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise CalendarError(f"{text!r} is not a time like 2026-11-08T15:00") from None
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)), False


def _event_times(ev: dict[str, Any], tz: Any) -> tuple[datetime | None, datetime | None, bool]:
    s, e = ev.get("start") or {}, ev.get("end") or {}
    if s.get("date"):
        try:
            a, b = date.fromisoformat(s["date"]), date.fromisoformat(e.get("date") or s["date"])
        except ValueError:
            return None, None, True
        return datetime(a.year, a.month, a.day, tzinfo=tz), datetime(b.year, b.month, b.day, tzinfo=tz), True
    try:
        a = datetime.fromisoformat(str(s.get("dateTime", "")).replace("Z", "+00:00")).astimezone(tz)
        b = datetime.fromisoformat(str(e.get("dateTime", "")).replace("Z", "+00:00")).astimezone(tz) if e.get("dateTime") else a
    except ValueError:
        return None, None, False
    return a, b, False


def when_text(start: datetime | None, end: datetime | None, all_day: bool) -> str:
    """A short readable span for cards and lists: "2026-11-08 15:00–16:00", "2026-11-08, all day"."""
    if start is None:
        return "?"
    if all_day:
        last = (end - timedelta(days=1)) if end and end > start else start
        if last.date() > start.date():
            return t("{start} – {end}, all day", start=start.strftime("%Y-%m-%d"), end=last.strftime("%Y-%m-%d"))
        return t("{date}, all day", date=start.strftime("%Y-%m-%d"))
    if end is None or end <= start:
        return start.strftime("%Y-%m-%d %H:%M")
    if end.date() == start.date():
        return f"{start.strftime('%Y-%m-%d %H:%M')}–{end.strftime('%H:%M')}"
    return f"{start.strftime('%Y-%m-%d %H:%M')} – {end.strftime('%Y-%m-%d %H:%M')}"


class GoogleCalendar:
    def __init__(self, store: Store, google: GoogleWeb, *, fetcher: Fetcher = fetch, timezone_name: Callable[[], str | None] | None = None):
        self.store, self.google, self.fetcher = store, google, fetcher
        self.timezone_name = timezone_name or (lambda: store.profile().get("timezone"))

    def tz(self) -> Any:
        return _zone(self.timezone_name())

    # ---- accounts
    def accounts(self) -> list[dict[str, Any]]:
        out = []
        for c in self.store.credentials():
            if (c["name"] == ITEM or c["name"].startswith(ITEM + "-")) and c["has_secret"] and c["placement"].get("type") == "google_oauth":
                full = self.store.credential(c["name"]) or {}
                try:
                    data = json.loads(full.get("secret") or "{}")
                except ValueError:
                    data = {}
                scopes = [s for s in data.get("scopes", []) if isinstance(s, str)]
                out.append({"name": c["name"], "address": str(data.get("u") or ""), "scopes": scopes,
                            "can_write": bool(WRITE_SCOPES & set(scopes)), "problem": c["last_result"] == "rejected"})
        return out

    def _item_for(self, address: str) -> str:
        names = {a["name"]: a["address"] for a in self.accounts()}
        for name, addr in names.items():
            if addr.lower() == address.lower():
                return name
        taken = {c["name"] for c in self.store.credentials()}
        if ITEM not in taken:
            return ITEM
        n = 2
        while f"{ITEM}-{n}" in taken:
            n += 1
        return f"{ITEM}-{n}"

    async def connect(self, address: str, tok: dict[str, Any]) -> dict[str, Any]:
        """A Google account signed in on Google's page. The calendar list is read once before anything is saved."""
        granted = [s for s in str(tok.get("scope") or "").split() if s]
        if not tok.get("access_token") or not tok.get("refresh_token") or not address:
            raise CalendarError(t("Google did not give calendar access. Please connect again and allow the calendar."))
        if granted and not READ_SCOPES & set(granted):
            raise CalendarError(t("The calendar box was not ticked on Google's page. Please connect again and allow the calendar."))
        r = await self._request("GET", f"{API}/users/me/calendarList?maxResults=1", str(tok["access_token"]))
        if r.status in (401, 403):
            raise CalendarError(self._refusal(r) + " " + t("Nothing was saved."))
        if r.status >= 400:
            raise CalendarError(t("Google Calendar did not answer properly (HTTP {status}). Nothing was saved.", status=r.status))
        name = self._item_for(address)
        label = f"Google Calendar · {address}"
        self.store.delete_credential(name)
        self.store.save_credential(name, label, [HOST], {"type": "google_oauth", "via": "oauth", "service": "calendar"}, "", kind="api")
        self.store.set_credential_secret(name, json.dumps({"u": address, "scopes": granted or list(CALENDAR_SCOPES), "oauth": {
            "access_token": str(tok["access_token"]), "refresh_token": str(tok["refresh_token"]),
            "expires_at": time.time() + float(tok.get("expires_in") or 3600)}}))
        self.store.set_credential_result(name, "ok", "calendar list read when it was added")
        self.store.add_vault_log(name, "saved", "Google Calendar, Google sign-in, checked")
        return {"name": name, "address": address, "label": label}

    @staticmethod
    def _refusal(r: Reply) -> str:
        try:
            err = json.loads(r.body).get("error") or {}
        except (ValueError, AttributeError):
            err = {}
        reason = " ".join(str(x.get("reason", "")) for x in (err.get("errors") or []) if isinstance(x, dict)) if isinstance(err, dict) else ""
        msg = str(err.get("message", "")) if isinstance(err, dict) else ""
        if "accessNotConfigured" in reason or "has not been used" in msg or "SERVICE_DISABLED" in json.dumps(err):
            return t("The Google Calendar API is not turned on in the operator's Google project yet.")
        if "insufficient" in (reason + msg).lower():
            return t("This Google account did not allow the calendar. Please connect again and allow the calendar.")
        return t("Google did not allow calendar access (HTTP {status}).", status=r.status)

    # ---- calls
    async def _request(self, method: str, url: str, access: str, body: dict[str, Any] | None = None) -> Reply:
        headers = {"Authorization": f"Bearer {access}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            return await self.fetcher(method, url, allowed_hosts=[HOST], headers=headers,
                                      content=json.dumps(body).encode() if body is not None else None)
        except NetGuardError as e:
            raise CalendarError(f"could not reach Google Calendar: {e}") from None

    def _pick(self, account: str) -> dict[str, Any]:
        """The connected account the call means (its name, address, scopes); the same errors whichever tool asks."""
        mine = self.accounts()
        if not mine:
            raise CalendarError("no Google Calendar is connected. Ask the user to connect it with connect_service(service='google_calendar').")
        a = account.strip().lower()
        pick = next((x for x in mine if a and (x["name"] == a or x["address"].lower() == a)), None) if a else (mine[0] if len(mine) == 1 else None)
        if pick is None:
            raise CalendarError("say which Google account with account: " + ", ".join(x["address"] for x in mine))
        return pick

    def _open(self, account: str, tool: str) -> tuple[str, dict[str, Any]]:
        from .vault import use_credential
        pick = self._pick(account)
        cred = use_credential(self.store, pick["name"], tool=tool)
        return pick["name"], json.loads(cred["secret"])

    async def _fresh(self, name: str, data: dict[str, Any], force: bool = False) -> str:
        o = data["oauth"]
        if not force and time.time() < float(o.get("expires_at") or 0) - 60:
            return str(o["access_token"])
        try:
            tok = await self.google.refresh(str(o["refresh_token"]))
        except GoogleWebError as e:
            self.store.set_credential_result(name, "rejected", "Google did not renew the sign-in")
            raise CalendarError(f"Google did not renew the sign-in ({str(e)[:80]}); ask the user to connect Google Calendar again in the Connectors tab") from None
        if not tok.get("access_token"):
            self.store.set_credential_result(name, "rejected", "Google did not renew the sign-in")
            raise CalendarError("Google did not renew the sign-in; ask the user to connect Google Calendar again in the Connectors tab")
        o = {**o, "access_token": str(tok["access_token"]), "expires_at": time.time() + float(tok.get("expires_in") or 3600),
             **({"refresh_token": str(tok["refresh_token"])} if tok.get("refresh_token") else {})}
        scopes = str(tok.get("scope") or "").split() or data.get("scopes", [])
        self.store.set_credential_secret(name, json.dumps({**data, "scopes": scopes, "oauth": o}))
        data.update({"scopes": scopes, "oauth": o})
        return str(o["access_token"])

    async def call(self, account: str, tool: str, method: str, path: str, params: dict[str, Any] | None = None,
                   body: dict[str, Any] | None = None, *, write: bool = False) -> Any:
        name, data = self._open(account, tool)
        if write and not WRITE_SCOPES & set(data.get("scopes") or []):
            raise CalendarError("this Google account allowed reading the calendar only; ask the user to connect Google Calendar again "
                                "and allow changing events")
        url = API + path + ("?" + urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")}) if params else "")
        r = await self._request(method, url, await self._fresh(name, data), body)
        if r.status == 401:   # revoked early or the clock is off: renew once
            r = await self._request(method, url, await self._fresh(name, data, force=True), body)
        if r.status in (204, 205) or (method == "DELETE" and r.status in (200, 410)):
            return {}
        try:
            answer = json.loads(r.body) if r.body else {}
        except ValueError:
            answer = {}
        if r.status >= 400:
            if r.status in (401, 403):
                raise CalendarError(self._refusal(r))
            msg = str((answer.get("error") or {}).get("message", "")) if isinstance(answer, dict) and isinstance(answer.get("error"), dict) else ""
            raise CalendarError(f"Google Calendar refused (HTTP {r.status}){': ' + msg[:160] if msg else ''}")
        self.store.set_credential_result(name, "ok", "")
        return answer

    async def calendars(self, account: str = "", tool: str = "gcal_calendars") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = ""
        for _ in range(5):
            r = await self.call(account, tool, "GET", "/users/me/calendarList", {"maxResults": 100, "pageToken": page})
            out += [c for c in r.get("items", []) if isinstance(c, dict)]
            page = str(r.get("nextPageToken") or "")
            if not page:
                break
        return out

    async def events(self, account: str, start: datetime, end: datetime, query: Any = "", calendar: str = "",
                     tool: str = "gcal_events", pages: int = 1) -> list[dict[str, Any]]:
        """Events overlapping [start, end); `pages` > 1 follows Google's next pages (a long search span), PAGE_EVENTS each.
        `query` (words, event_search.query_terms) is matched here, not by Google: only the events with any of the words are kept."""
        tz = self.tz()
        terms = query_terms(query)
        address = self._pick(account)["address"]
        pages = max(pages, SEARCH_PAGES_GOOGLE) if terms else pages
        try:   # the calendar list once, in every case: each calendar's access role says whose events these are (own data, see gcal_events)
            listed = await self.calendars(account, tool)
        except (CalendarError, NetGuardError):
            if not calendar:
                raise
            listed = []   # a given calendar still reads; its events are then all read as others' text
        roles = {str(c.get("id", "")): str(c.get("accessRole") or "") for c in listed}
        if calendar:
            cals = [{"id": calendar, "summary": calendar}]
        else:
            cals = [c for c in listed if c.get("primary") or c.get("selected", False)][:MAX_CALENDARS] or \
                [{"id": "primary", "summary": "primary"}]
        rows: list[dict[str, Any]] = []
        for c in cals:
            calendar_own = roles.get(str(c["id"]), "") == "owner"
            items: list[Any] = []
            token = ""
            for _ in range(max(1, pages)):
                r = await self.call(account, tool, "GET", f"/calendars/{quote(str(c['id']), safe='')}/events", {
                    "timeMin": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "timeMax": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "singleEvents": "true", "orderBy": "startTime", "maxResults": MAX_EVENTS if pages <= 1 else PAGE_EVENTS,
                    "timeZone": getattr(tz, "key", "UTC"), "pageToken": token})
                items += r.get("items", []) if isinstance(r.get("items"), list) else []
                token = str(r.get("nextPageToken") or "")
                if not token:
                    break
            for ev in items:
                if not isinstance(ev, dict) or ev.get("status") == "cancelled":
                    continue
                me = next((a for a in ev.get("attendees") or [] if isinstance(a, dict) and a.get("self")), None)
                if me and me.get("responseStatus") == "declined":
                    continue
                a, b, all_day = _event_times(ev, tz)
                people = " ".join(f"{x.get('displayName', '')} {x.get('email', '')}" for x in ev.get("attendees") or [] if isinstance(x, dict))
                rows.append({"id": str(ev.get("id", "")), "calendar_id": str(c["id"]), "calendar": str(c.get("summaryOverride") or c.get("summary") or ""),
                             "title": str(ev.get("summary") or t("(no title)")), "start": a, "end": b, "all_day": all_day,
                             "location": str(ev.get("location") or ""), "attendees": len(ev.get("attendees") or []),
                             "recurring": bool(ev.get("recurringEventId")), "notes": str(ev.get("description") or "")[:2000],
                             "people": people[:1000], "account": address,
                             "own": calendar_own and _own_event(ev), "calendar_own": calendar_own})
        if terms:
            rows = [r for r in rows if matches(terms, r["title"], r["location"], r["notes"], r["people"])]
        rows.sort(key=lambda x: x["start"] or datetime.max.replace(tzinfo=timezone.utc))
        return rows

    async def event(self, account: str, calendar: str, event_id: str, tool: str) -> dict[str, Any]:
        return await self.call(account, tool, "GET", f"/calendars/{quote(calendar or 'primary', safe='')}/events/{quote(event_id, safe='')}")

    async def upcoming(self, hours: float = 24) -> list[dict[str, Any]]:
        """Events in the next `hours` of every connected account, for the proactive pass: [{"id", "title", "start": unix, "where"}]."""
        now = datetime.now(timezone.utc)
        out = []
        for acct in self.accounts()[:3]:
            for ev in await self.events(acct["name"], now, now + timedelta(hours=hours), tool="proactive"):
                if ev["start"] is not None and not ev["all_day"]:
                    out.append({"id": f"gcal:{ev['id']}", "title": ev["title"], "start": ev["start"].timestamp(), "where": ev["location"]})
        return out


def _own_event(ev: dict[str, Any]) -> bool:
    """An event the owner made themself: nobody else organized or created it, and it is not one made from mail or contacts."""
    organizer, creator = ev.get("organizer"), ev.get("creator")
    return ((("organizer" not in ev) or (isinstance(organizer, dict) and organizer.get("self") is True))
            and (("creator" not in ev) or (isinstance(creator, dict) and creator.get("self") is True))
            and ev.get("eventType", "default") in OWN_EVENT_TYPES)


# ---------------------------------------------------------------------------------------------------------------- the tools
def _event_body(args: dict[str, Any], tz: Any, *, partial: bool) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if "title" in args or not partial:
        title = str(args.get("title") or "").strip()
        if not title and not partial:
            raise CalendarError("title is required")
        if title:
            body["summary"] = title[:300]
    for k, g in (("location", "location"), ("description", "description")):
        if k in args and args[k] is not None:
            body[g] = str(args[k])[:2000]
    if args.get("start") or not partial:
        start, date_only = parse_when(str(args.get("start", "")), tz)
        all_day = bool(args.get("all_day")) or date_only
        zone = getattr(tz, "key", "UTC")
        if all_day:
            if args.get("end"):
                end, _ = parse_when(str(args["end"]), tz)
                stop = end.date() + timedelta(days=1)   # the end date the user names is included; Google's end date is not
            else:
                stop = start.date() + timedelta(days=1)
            if stop <= start.date():
                raise CalendarError("the end is before the start")
            body["start"], body["end"] = {"date": start.date().isoformat()}, {"date": stop.isoformat()}
        else:
            end = parse_when(str(args["end"]), tz)[0] if args.get("end") else start + timedelta(minutes=int(args.get("minutes") or 60))
            if end <= start:
                raise CalendarError("the end is before the start")
            body["start"] = {"dateTime": start.isoformat(), "timeZone": zone}
            body["end"] = {"dateTime": end.isoformat(), "timeZone": zone}
    elif args.get("end"):
        raise CalendarError("give start together with end when moving an event")
    return body


def _span_of(body_or_event: dict[str, Any], tz: Any) -> str:
    a, b, all_day = _event_times(body_or_event, tz)
    return when_text(a, b, all_day)


def calendar_tools(cal: GoogleCalendar) -> list[Tool]:
    account = {"type": "string", "description": "which connected Google account, when there are several"}
    when = "YYYY-MM-DD or YYYY-MM-DDTHH:MM, the user's time zone"

    def err(e: Exception) -> str:
        return f"error: {e}"

    async def gcal_calendars(args: dict[str, Any]) -> str:
        try:
            rows = await cal.calendars(str(args.get("account", "")))
        except (CalendarError, NetGuardError) as e:
            return err(e)
        if not rows:
            return "No calendars."
        lines = []
        for c in rows:
            name = str(c.get('summaryOverride') or c.get('summary') or '?')
            if c.get("accessRole") != "owner":   # a calendar someone else owns: its name is others' text
                name = data(name)
            lines.append(f"- {name} (calendar: {c.get('id')}"
                         f"{', primary' if c.get('primary') else ''}{', read-only' if c.get('accessRole') in ('reader', 'freeBusyReader') else ''})")
        text = "\n".join(lines)
        return text if marked(text) else Ours(text)

    async def gcal_events(args: dict[str, Any]) -> str:
        tz = cal.tz()
        terms = query_terms(args.get("query"))
        try:
            start = parse_when(str(args["start"]), tz)[0] if args.get("start") else datetime.now(tz)
            end = parse_when(str(args["end"]), tz, end_of_day=True)[0] if args.get("end") else start + timedelta(days=7)
            if end <= start:
                return "error: end is before start"
            rows = await cal.events(str(args.get("account", "")), start, min(end, start + timedelta(days=366)), "",
                                    str(args.get("calendar") or ""), pages=SEARCH_PAGES_GOOGLE if terms else 1)
        except (CalendarError, NetGuardError) as e:
            return err(e)
        zone = getattr(tz, "key", "UTC")
        span = f"between {start.strftime('%Y-%m-%d %H:%M')} and {end.strftime('%Y-%m-%d %H:%M')} ({zone})"
        total = len(rows)
        if terms:
            rows = [r for r in rows if matches(terms, r["title"], r["location"], r["notes"], r["people"])]
        if not rows:
            return Ours(no_match_line(terms, span, total) if terms else f"No events {span}.")
        lines = [f"Events ({zone}), {len(rows)}:"]
        for r in rows[:150]:   # the owner's own events are the app's words; an event others wrote has its title and place marked as data
            title = r["title"] if r["own"] else data(r["title"])
            place = r["location"] if r["own"] or not r["location"] else data(r["location"])
            calendar = r["calendar"] if r["calendar_own"] else data(r["calendar"])
            extra = "".join([f" · at {place}" if r["location"] else "", f" · {r['attendees']} people" if r["attendees"] else "",
                             " · repeats" if r["recurring"] else ""])
            lines.append(f"- {when_text(r['start'], r['end'], r['all_day'])} · {title}{extra} · {calendar} "
                         f"(event_id: {r['id']}, calendar: {r['calendar_id']})")
        text = "\n".join(lines)
        return text if marked(text) else Ours(text)

    async def gcal_create_event(args: dict[str, Any]) -> str:
        try:
            body = _event_body(args, cal.tz(), partial=False)
            ev = await cal.call(str(args.get("account", "")), "gcal_create_event", "POST",
                                f"/calendars/{quote(str(args.get('calendar') or 'primary'), safe='')}/events", None, body, write=True)
        except (CalendarError, NetGuardError) as e:
            return err(e)
        return f"Added: {ev.get('summary', body.get('summary'))} · {_span_of(ev or body, cal.tz())} (event_id: {ev.get('id', '')})"

    async def gcal_update_event(args: dict[str, Any]) -> str:
        try:
            body = _event_body(args, cal.tz(), partial=True)
            if not body:
                return "error: nothing to change (give title, start/end, location or description)"
            ev = await cal.call(str(args.get("account", "")), "gcal_update_event", "PATCH",
                                f"/calendars/{quote(str(args.get('calendar') or 'primary'), safe='')}/events/{quote(str(args.get('event_id', '')), safe='')}",
                                None, body, write=True)
        except (CalendarError, NetGuardError) as e:
            return err(e)
        return f"Changed: {ev.get('summary', '')} · {_span_of(ev, cal.tz())}"

    async def gcal_delete_event(args: dict[str, Any]) -> str:
        try:
            await cal.call(str(args.get("account", "")), "gcal_delete_event", "DELETE",
                           f"/calendars/{quote(str(args.get('calendar') or 'primary'), safe='')}/events/{quote(str(args.get('event_id', '')), safe='')}",
                           write=True)
        except (CalendarError, NetGuardError) as e:
            return err(e)
        return "Deleted."

    # ---- the approval card's line: what will change, in words (never the raw arguments)
    async def describe_create(args: dict[str, Any]) -> str:
        tz = cal.tz()
        body = _event_body(args, tz, partial=False)
        line = t("Add to Google Calendar: {title} · {when}", title=body.get("summary", ""), when=_span_of(body, tz))
        return line + (" · " + t("Place: {place}", place=body["location"]) if body.get("location") else "")

    async def _current(args: dict[str, Any], tool: str) -> tuple[str, str]:
        tz = cal.tz()
        try:
            ev = await cal.event(str(args.get("account", "")), str(args.get("calendar") or "primary"), str(args.get("event_id", "")), tool)
            return str(ev.get("summary") or t("(no title)")), _span_of(ev, tz)
        except (CalendarError, NetGuardError):
            return t("an event"), ""

    async def describe_update(args: dict[str, Any]) -> str:
        tz = cal.tz()
        title, span = await _current(args, "gcal_update_event")
        body = _event_body(args, tz, partial=True)
        changes = []
        if body.get("summary"):
            changes.append(t("title: {title}", title=body["summary"]))
        if body.get("start"):
            changes.append(t("time: {when}", when=_span_of(body, tz)))
        if "location" in body:
            changes.append(t("place: {place}", place=body["location"] or "-"))
        if "description" in body:
            changes.append(t("note changed"))
        head = t("Change in Google Calendar: {title}", title=title) + (f" ({span})" if span else "")
        return head + (" → " + ", ".join(changes) if changes else "")

    async def describe_delete(args: dict[str, Any]) -> str:
        title, span = await _current(args, "gcal_delete_event")
        return t("Delete from Google Calendar: {title}", title=title) + (f" ({span})" if span else "")

    def check_body(partial: bool) -> Callable[[dict[str, Any]], str | None]:
        def validate(args: dict[str, Any]) -> str | None:
            if partial and not str(args.get("event_id") or "").strip():
                return "event_id is required (from gcal_events)"
            try:
                _event_body(args, cal.tz(), partial=partial)
            except CalendarError as e:
                return str(e)
            return None
        return validate

    def need_id(args: dict[str, Any]) -> str | None:
        return None if str(args.get("event_id") or "").strip() else "event_id is required (from gcal_events)"

    fields = {"title": {"type": "string"}, "start": {"type": "string", "description": when},
              "end": {"type": "string", "description": when + "; all-day: the last day, included"},
              "all_day": {"type": "boolean"}, "location": {"type": "string"},
              "description": {"type": "string", "description": "notes, only when the user asked for them"},
              "calendar": {"type": "string", "description": "calendar id (default: primary)"}, "account": account}
    target = {"event_id": {"type": "string", "description": "from gcal_events"},
              "calendar": {"type": "string", "description": "the event's calendar id from gcal_events"}, "account": account}
    return [
        Tool("gcal_calendars", "List the calendars of the user's Google Calendar (ids, primary, read-only).",
             {"type": "object", "properties": {"account": account}}, gcal_calendars, read_only=True, untrusted=True, source="calendar", trail="calendar"),
        Tool("gcal_events", "List or search events in the user's Google Calendar only (default: the next 7 days), each occurrence of a "
             "repeating event apart, with the ids needed to change one. For what is on across all calendars use calendar_events.",
             {"type": "object", "properties": {"start": {"type": "string", "description": when}, "end": {"type": "string", "description": when},
                                               "query": {"type": "array", "items": {"type": "string"},
                                                         "description": "words to look for; an event whose title, place, notes or guests contain "
                                                                        "any one of them is shown (any case)"},
                                               "calendar": {"type": "string", "description": "only this calendar id"},
                                               "account": account}},
             gcal_events, read_only=True, untrusted=True, source="calendar", trail="calendar"),
        Tool("gcal_create_event", "Add an event to the user's Google Calendar. When the user asked for it in the chat it runs at once; otherwise "
             "the user approves it on a card. A date-only start is all-day; a timed event without end lasts an hour. For several changes of "
             "one request, call them together in one step: the user then sees one card.",
             {"type": "object", "properties": fields, "required": ["title", "start"]},
             gcal_create_event, requires_approval=True, describe=describe_create, validate=check_body(False),
             auto_approve=lambda a: owner_request(), shares_card=True),
        Tool("gcal_update_event", "Change an event in the user's Google Calendar, giving only what changes. When the user asked for it in the "
             "chat it runs at once; otherwise the user approves it on a card. For several changes of one request, call them together in one step.",
             {"type": "object", "properties": {**fields, **target}, "required": ["event_id"]},
             gcal_update_event, requires_approval=True, describe=describe_update, validate=check_body(True),
             auto_approve=lambda a: owner_request(), shares_card=True),
        Tool("gcal_delete_event", "Delete an event (one occurrence of a repeating one) from the user's Google Calendar. When the user asked for it "
             "in the chat it runs at once; otherwise the user approves it on a card. For several deletions of one request, call them together in one step.",
             {"type": "object", "properties": target, "required": ["event_id"]},
             gcal_delete_event, requires_approval=True, describe=describe_delete, validate=need_id,
             auto_approve=lambda a: owner_request(), shares_card=True),
    ]
