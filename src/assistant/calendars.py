"""Every calendar the owner connected, behind one interface, for the morning briefing and the proactive pass.

An account is one place events come from: the phones' calendars (phone_calendar.py), a Google account (google_calendar.py), the Outlook
calendar (microsoft_calendar.py), a CalDAV calendar, or a Kelo Nest connector that provides a calendar (nest_sources.py). `Calendars.accounts()` lists them, `Calendars.upcoming(hours)` reads the next
`hours` of the ones asked for: [{"id", "title", "start": unix, "end": unix, "where", "source": the account's label, "source_id"}], all-day
events left out (they have no time to say). One account failing is reported to `report` and skipped; the others still count.

CalDAV (RFC 4791) is read only, with the password the owner already gave for the mailbox of the same provider (an app password; the
vault item "mail-<provider>" is opened for each use and the use is logged) or with a "CalDAV account" the owner added (own address,
user name and app password: vault item "caldav-<name>", kind "email"). Which providers offer CalDAV (CALDAV below):
  iCloud   caldav.icloud.com. Apple: app-specific passwords let third-party apps reach the mail, contacts and calendars of an iCloud
           account (support.apple.com/102654). Calendars live on pNN-caldav.icloud.com hosts that the principal answer names.
  Yahoo    caldav.calendar.yahoo.com. Yahoo's help (help.yahoo.com/kb/SLN4707.html): enter that address, the Yahoo ID and an app password.
  Naver    caldav.calendar.naver.com. Naver's help pages are not reachable from here and the guides that describe it (user name = Naver ID,
           an application password when 2-step verification is on) call it unofficial, so it is tried and a refusal is explained, never
           assumed to work.
  Daum / Kakao Mail: no CalDAV service is documented (Kakao's calendar has a REST API with Kakao Login only), so no calendar is read.
  Gmail    Google offers CalDAV only with OAuth: the Google Calendar connector covers it.
Requests go through netguard.fetch: https only, one host pinned per request (the provider's, or the one the user typed), the address
checked to be public, no redirects. Discovery: the principal (current-user-principal), its calendar-home-set, then the calendars
(resourcetype calendar, VEVENT); then one calendar-query REPORT with a time range per calendar. Events are expanded with
recurring-ical-events (RRULE, EXDATE, RECURRENCE-ID, time zones, X-WR-TIMEZONE); a time without a zone is the owner's local time.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
import recurring_ical_events

from .i18n import N_, t
from .netguard import NetGuardError, fetch, valid_host
from .vault import VaultError, use_credential

log = logging.getLogger(__name__)

DAV = "DAV:"
NS_CALDAV = "urn:ietf:params:xml:ns:caldav"
MAX_BODY = 4_000_000
MAX_CALENDARS = 12
MAX_PER_CALENDAR = 400
HOME_TTL = 3600.0
ACCOUNT_TIMEOUT = 45.0   # one account's whole read (discovery included); longer is reported as "slow" and skipped this time
GENERIC_PREFIX = "caldav-"

Fetcher = Callable[..., Awaitable[Any]]


class CalDavError(Exception):
    """A CalDAV account that could not be read. `code`: auth (refused the sign-in), host (an address outside the allowed hosts), off (no calendar offered over CalDAV, or CalDAV
    not turned on), unreachable, other. The message is safe to show."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class CalDavProvider:
    id: str                              # the mail provider it belongs to
    name: str                            # N_: the calendar's name
    url: str                             # where discovery starts
    hosts: tuple[str, ...]               # regular expressions of the hosts its answers may name (matched whole)
    howto: str                           # N_: what to do when it can not be read
    starts: tuple[str, ...] = ()         # other paths to try for the principal ({user} = the user name)


CALDAV: dict[str, CalDavProvider] = {p.id: p for p in [
    CalDavProvider("icloud", N_("iCloud Calendar"), "https://caldav.icloud.com/", (r"caldav\.icloud\.com", r"p\d{1,3}-caldav\.icloud\.com"),
                   N_("Connect iCloud Mail again with an app-specific password from account.apple.com → Sign-In and Security → App-Specific Passwords.")),
    CalDavProvider("yahoo", N_("Yahoo Calendar"), "https://caldav.calendar.yahoo.com/", (r"caldav\.calendar\.yahoo\.com",),
                   N_("Connect Yahoo Mail again with a new app password (Yahoo account security → Generate app password)."),
                   ("/dav/{user}/Calendar/",)),
    CalDavProvider("naver", N_("Naver Calendar"), "https://caldav.calendar.naver.com/", (r"caldav\.calendar\.naver\.com",),
                   N_("Naver does not document CalDAV and may not offer it. Turn on 2-step verification, connect Naver Mail again with an application password, or leave the Naver calendar out."),
                   ("/principals/users/{user}/",)),
]}
# Mail services whose calendar can not be read, and why (shown nowhere as an error: there is nothing to turn on)
NO_CALDAV = {"daum": "Daum Mail has no CalDAV calendar service.", "gmail": "Gmail's calendar is read through Google Calendar."}


def _zone(name: str | None) -> Any:
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


# ---------------------------------------------------------------------------------------------------------------- CalDAV client
def _q(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def multistatus(body: bytes) -> list[dict[str, Any]]:
    """The responses of a 207 answer: [{"href", "props": {tag: Element}}], only the properties the server answered 2xx for."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise CalDavError("other", "the calendar server's answer could not be read") from None
    out = []
    for r in root.iter(_q(DAV, "response")):
        href = (r.findtext(_q(DAV, "href")) or "").strip()
        props: dict[str, ET.Element] = {}
        for ps in r.findall(_q(DAV, "propstat")):
            m = re.search(r"\s(\d{3})\b", ps.findtext(_q(DAV, "status")) or "")
            if m and not m.group(1).startswith("2"):
                continue
            for prop in ps.findall(_q(DAV, "prop")):
                for el in prop:
                    props[el.tag] = el
        out.append({"href": href, "props": props})
    return out


def _href_of(el: ET.Element | None) -> str:
    if el is None:
        return ""
    h = el.find(_q(DAV, "href"))
    return (h.text or "").strip() if h is not None and h.text else ""


_PRINCIPAL = (b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>')
_HOME = (b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>'
         b"<c:calendar-home-set/></d:prop></d:propfind>")
_CALENDARS = (b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>'
              b"<d:resourcetype/><d:displayname/><c:supported-calendar-component-set/></d:prop></d:propfind>")


def _report(start: datetime, end: datetime) -> bytes:
    a, b = (x.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") for x in (start, end))
    return ('<?xml version="1.0" encoding="utf-8"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><c:calendar-data/></d:prop><c:filter><c:comp-filter name=\"VCALENDAR\"><c:comp-filter name=\"VEVENT\">"
            f'<c:time-range start="{a}" end="{b}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>').encode()


class CalDavClient:
    """One CalDAV account, read only. `host_ok(host)` says which hosts its answers may point to; every request is pinned to one."""

    def __init__(self, url: str, user: str, password: str, *, host_ok: Callable[[str], bool], fetcher: Fetcher = fetch,
                 starts: tuple[str, ...] = (), now: Callable[[], float] = time.time):
        self.url, self.user, self.fetcher, self.host_ok, self.starts, self.now = url, user, fetcher, host_ok, starts, now
        raw = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = f"Basic {raw}"

    def _check(self, url: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not self.host_ok(host) or parts.port not in (None, 443):
            raise CalDavError("host", f"the calendar server named an address that is not allowed ({host or '?'})")
        return host

    async def _send(self, method: str, url: str, body: bytes, depth: str | None = None) -> Any:
        host = self._check(url)
        headers = {"Authorization": self._auth, "Content-Type": "application/xml; charset=utf-8", "Accept": "application/xml, text/xml, */*",
                   "User-Agent": "Kelo-CalDAV/1"}
        if depth is not None:
            headers["Depth"] = depth
        try:
            reply = await self.fetcher(method, url, allowed_hosts=[host], headers=headers, content=body, max_body=MAX_BODY)
        except NetGuardError as e:
            msg = str(e)
            code = "unreachable" if ("timed out" in msg or "did not complete" in msg or "could not resolve" in msg) else "other"
            raise CalDavError(code, msg) from None
        if reply.status == 401:
            raise CalDavError("auth", "the calendar server refused the sign-in")
        if reply.status in (403, 404, 405, 501):
            raise CalDavError("off", f"the calendar server answered HTTP {reply.status}")
        if reply.status >= 500:
            raise CalDavError("unreachable", f"the calendar server answered HTTP {reply.status}")
        if reply.status not in (200, 207):
            raise CalDavError("other", f"the calendar server answered HTTP {reply.status}")
        return reply

    async def calendars(self) -> list[dict[str, str]]:
        """[{"url", "name"}] of the calendars that hold events."""
        starts = [self.url] + [urljoin(self.url, s.replace("{user}", quote(self.user, safe="@"))) for s in self.starts]
        principal, last = "", None
        for start in starts:
            try:
                reply = await self._send("PROPFIND", start, _PRINCIPAL, "0")
            except CalDavError as e:
                if e.code in ("auth", "unreachable", "host"):
                    raise
                last = e
                continue
            for r in multistatus(reply.body):
                p = _href_of(r["props"].get(_q(DAV, "current-user-principal")))
                if p:
                    principal = urljoin(start, p)
                    break
            if principal:
                break
            principal = ""
        homes: list[str] = []
        if principal:
            reply = await self._send("PROPFIND", principal, _HOME, "0")
            for r in multistatus(reply.body):
                h = _href_of(r["props"].get(_q(NS_CALDAV, "calendar-home-set")))
                if h:
                    homes.append(urljoin(principal, h))
            homes = homes or [principal]
        else:
            homes = starts   # no principal answered: the address may itself be the calendar home or a calendar
        found: list[dict[str, str]] = []
        for home in homes:
            try:
                reply = await self._send("PROPFIND", home, _CALENDARS, "1")
            except CalDavError as e:
                if e.code in ("auth", "unreachable", "host"):
                    raise
                last = e
                continue
            for r in multistatus(reply.body):
                rt = r["props"].get(_q(DAV, "resourcetype"))
                if rt is None or rt.find(_q(NS_CALDAV, "calendar")) is None:
                    continue
                comps = r["props"].get(_q(NS_CALDAV, "supported-calendar-component-set"))
                if comps is not None and len(comps) and not any(c.get("name", "").upper() == "VEVENT" for c in comps):
                    continue   # a task list or a journal
                url = urljoin(home, r["href"])
                name = (r["props"][_q(DAV, "displayname")].text or "").strip() if _q(DAV, "displayname") in r["props"] else ""
                if url not in {c["url"] for c in found}:
                    found.append({"url": url, "name": name})
            if found:
                break
        if not found:
            raise CalDavError("off", str(last) if last else "the account offers no calendar over CalDAV")
        return found[:MAX_CALENDARS]

    async def raw_events(self, calendar_url: str, start: datetime, end: datetime) -> list[str]:
        reply = await self._send("REPORT", calendar_url, _report(start, end), "1")
        texts = []
        for r in multistatus(reply.body):
            el = r["props"].get(_q(NS_CALDAV, "calendar-data"))
            if el is not None and (el.text or "").strip():
                texts.append(el.text)
        return texts[:MAX_PER_CALENDAR]


def expand(texts: list[str], start: datetime, end: datetime, tz: Any) -> list[dict[str, Any]]:
    """The occurrences between `start` and `end` of iCalendar texts: [{"uid", "title", "start", "end" (unix), "where", "all_day"}].
    Repeats (RRULE/RDATE), skipped days (EXDATE), moved or changed single occurrences (RECURRENCE-ID) and time zones are applied."""
    out: list[dict[str, Any]] = []
    for text in texts:
        try:
            cal = icalendar.Calendar.from_ical(text)
            events = recurring_ical_events.of(cal).between(start, end)
        except Exception as e:  # noqa: BLE001 - one broken resource never hides the others
            log.info("caldav: an event could not be read (%s)", str(e)[:100])
            continue
        for ev in events:
            if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                continue
            try:
                a, b = ev.start, ev.end
            except Exception:  # noqa: BLE001
                continue
            all_day = not isinstance(a, datetime)
            if all_day:
                a, b = datetime(a.year, a.month, a.day, tzinfo=tz), datetime(b.year, b.month, b.day, tzinfo=tz)
            else:
                a = a if a.tzinfo else a.replace(tzinfo=tz)   # a time without a zone is the owner's local time
                b = b if b.tzinfo else b.replace(tzinfo=tz)
            out.append({"uid": str(ev.get("UID", "")), "title": re.sub(r"\s+", " ", str(ev.get("SUMMARY", ""))).strip()[:200],
                        "start": a.timestamp(), "end": max(b.timestamp(), a.timestamp()),
                        "where": re.sub(r"\s+", " ", str(ev.get("LOCATION", ""))).strip()[:200], "all_day": all_day})
    return sorted(out, key=lambda e: e["start"])


# ---------------------------------------------------------------------------------------------------------------- accounts
@dataclass(frozen=True)
class CalAccount:
    id: str                 # phone | google:<item> | outlook:<item> | caldav:<item>
    provider: str           # phone | google | outlook | icloud | yahoo | naver | caldav
    name: str               # N_: the provider's calendar name
    address: str = ""
    item: str = ""          # the vault item it uses
    url: str = ""           # a CalDAV account's own server address
    extra: dict[str, Any] = field(default_factory=dict, compare=False)


PHONE_NAME, GOOGLE_NAME, OUTLOOK_NAME, CALDAV_NAME = N_("Phone calendar"), N_("Google Calendar"), N_("Outlook Calendar"), N_("CalDAV calendar")


def generic_accounts(store: Any) -> list[dict[str, str]]:
    """The CalDAV accounts the owner added with their own address: [{"name": vault item, "address": user name, "url"}]."""
    return [{"name": c["name"], "address": str(c["label"] or "").split(" · ")[-1], "url": str(c["placement"].get("url") or "")}
            for c in store.credentials()
            if c["name"].startswith(GENERIC_PREFIX) and c["kind"] == "email" and c["has_secret"] and c["placement"].get("type") == "caldav"]


def _host_checker(patterns: tuple[str, ...]) -> Callable[[str], bool]:
    rx = [re.compile(p) for p in patterns]
    return lambda host: any(r.fullmatch(host) for r in rx)


class Calendars:
    def __init__(self, store: Any, mail: Any, phone: Any, google: Any, outlook: Any, *, fetcher: Fetcher = fetch,
                 timezone_name: Callable[[], str | None] | None = None, clock: Callable[[], float] = time.time, nest: Any = None):
        self.store, self.mail, self.phone, self.google, self.outlook, self.fetcher, self.clock = store, mail, phone, google, outlook, fetcher, clock
        self.nest = nest   # nest_sources.NestCalendars: calendars that installed Nest connectors provide
        self.timezone_name = timezone_name or (lambda: store.profile().get("timezone"))
        self._homes: dict[str, tuple[float, list[dict[str, str]]]] = {}

    def tz(self) -> Any:
        return _zone(self.timezone_name())

    # ---- which accounts exist
    def accounts(self) -> list[CalAccount]:
        out: list[CalAccount] = []
        if self.phone.rows():
            out.append(CalAccount("phone", "phone", PHONE_NAME))
        for a in self.google.accounts():
            out.append(CalAccount(f"google:{a['name']}", "google", GOOGLE_NAME, a.get("address", ""), a["name"]))
        if self.outlook.connected():
            item = self.store.credential("microsoft-api") or {}
            label = str(item.get("label") or "")
            out.append(CalAccount("outlook:microsoft-api", "outlook", OUTLOOK_NAME, label.split(" · ")[-1] if " · " in label else "", "microsoft-api"))
        for a in self.mail.accounts():
            p = CALDAV.get(a["provider"])
            if p is not None and not self._is_oauth(a["name"]):
                out.append(CalAccount(f"caldav:{a['name']}", p.id, p.name, a["address"], a["name"]))
        for g in generic_accounts(self.store):
            out.append(CalAccount(f"caldav:{g['name']}", "caldav", CALDAV_NAME, g["address"], g["name"], g["url"]))
        for p in (self.nest.accounts() if self.nest is not None else []):
            out.append(CalAccount(f"nest:{p.id}", "nest", p.text("name"), "", p.id))
        return out

    def _is_oauth(self, item: str) -> bool:
        c = next((c for c in self.store.credentials() if c["name"] == item), None)
        return bool(c and c["placement"].get("via") == "oauth")

    # ---- reading
    async def upcoming_of(self, acct: CalAccount, hours: float = 24) -> list[dict[str, Any]]:
        """The next `hours` of one account, timed events only (its own errors propagate; see `upcoming` for the isolated version)."""
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        return await self.events_of(acct, now, now + timedelta(hours=hours))

    async def events_of(self, acct: CalAccount, start: datetime, end: datetime, *, all_day: bool = False,
                        tool: str = "proactive", pages: int = 1) -> list[dict[str, Any]]:
        """One account's events overlapping [start, end): [{"id", "title", "start", "end"?, "where", "all_day", "day"? (an all-day
        event's first date), "source_id"}]; all-day events only with `all_day`."""
        evs: list[dict[str, Any]] = []
        if acct.provider == "phone":
            rows, _ = self.phone.events(start.timestamp(), end.timestamp())
            for e in rows:
                if e["all_day"] and not all_day:
                    continue
                evs.append({"id": f"phone:{int(e['start'])}:{e['title'][:40]}", "title": e["title"], "start": e["start"], "end": e["end"],
                            "where": e["location"], "all_day": bool(e["all_day"]), **({"day": e["day"]} if e["all_day"] and e.get("day") else {})})
        elif acct.provider == "google":
            for ev in await self.google.events(acct.item, start, end, tool=tool, **({"pages": pages} if pages > 1 else {})):
                if ev["start"] is None or (ev["all_day"] and not all_day):
                    continue
                stop = ev.get("end")
                evs.append({"id": f"gcal:{ev['id']}", "title": ev["title"], "start": ev["start"].timestamp(), "where": ev["location"],
                            "all_day": bool(ev["all_day"]), **({"end": stop.timestamp()} if stop is not None else {}),
                            **({"day": ev["start"].date().isoformat()} if ev["all_day"] else {})})
        elif acct.provider == "outlook":
            evs = await self.outlook.between(start, end, all_day=all_day, tool=tool, **({"pages": pages} if pages > 1 else {}))
        elif acct.provider == "nest":
            evs = await self.nest.events(acct.item, start, end, all_day=all_day, tz=self.tz())
        else:
            evs = await self._caldav(acct, start, end, all_day=all_day, tool=tool)
        return [{**e, "source_id": acct.id} for e in evs]

    def _client(self, acct: CalAccount, tool: str = "proactive") -> CalDavClient:
        cred = use_credential(self.store, acct.item, tool=tool, kind="email", detail="calendar events")
        data = json.loads(cred["secret"])
        if acct.provider == "caldav":
            host = (urlsplit(acct.url).hostname or "").lower()
            return CalDavClient(acct.url, data["u"], data["p"], host_ok=lambda h, host=host: h == host, fetcher=self.fetcher)
        p = CALDAV[acct.provider]
        return CalDavClient(p.url, data["u"], data["p"], host_ok=_host_checker(p.hosts), fetcher=self.fetcher, starts=p.starts)

    async def _caldav(self, acct: CalAccount, start: datetime, end: datetime, *, all_day: bool = False,
                      tool: str = "proactive") -> list[dict[str, Any]]:
        try:
            client = self._client(acct, tool)
        except VaultError as e:
            raise CalDavError("other", str(e)) from None
        except (ValueError, KeyError):
            raise CalDavError("auth", "the saved sign-in could not be read") from None
        try:
            cached = self._homes.get(acct.id)
            if cached and self.clock() - cached[0] < HOME_TTL:
                cals = cached[1]
            else:
                cals = await client.calendars()
                self._homes[acct.id] = (self.clock(), cals)
            tz = self.tz()
            seen: set[tuple[str, float]] = set()
            out: list[dict[str, Any]] = []
            for cal in cals:
                texts = await client.raw_events(cal["url"], start, end)
                for e in expand(texts, start, end, tz):
                    key = (e["uid"] or e["title"], e["start"])
                    if (e["all_day"] and not all_day) or key in seen or e["end"] < start.timestamp():
                        continue
                    seen.add(key)
                    digest = hashlib.sha256(f"{acct.id}|{e['uid']}".encode()).hexdigest()[:10]
                    day = datetime.fromtimestamp(e["start"], tz).date().isoformat() if e["all_day"] else ""
                    out.append({"id": f"caldav:{digest}:{int(e['start'])}", "title": e["title"], "start": e["start"], "end": e["end"], "where": e["where"],
                                "all_day": e["all_day"], **({"day": day} if day else {})})
            return out
        except CalDavError as e:
            if e.code in ("auth", "off"):
                self._homes.pop(acct.id, None)
            raise
        except VaultError as e:
            raise CalDavError("other", str(e)) from None

    async def upcoming(self, hours: float = 24, *, wanted: Callable[[CalAccount], bool] | None = None,
                       report: Callable[[CalAccount, str, str], None] | None = None, all_day: bool = False,
                       pages: int = 1) -> list[dict[str, Any]]:
        """Timed events (all-day ones too with `all_day`) of every wanted account in the next `hours`, by start time (see `read`)."""
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        return await self.read(now, now + timedelta(hours=hours), wanted=wanted, report=report, all_day=all_day, pages=pages)

    async def read(self, start: datetime, end: datetime, *, all_day: bool = False, wanted: Callable[[CalAccount], bool] | None = None,
                   report: Callable[[CalAccount, str, str], None] | None = None, tool: str = "proactive",
                   pages: int = 1) -> list[dict[str, Any]]:
        """Events of every wanted account overlapping [start, end), by start time, each with "source_id". The accounts are read at the
        same time, each within ACCOUNT_TIMEOUT. One that fails or takes too long is reported (account, code, detail; code "slow" for
        the time limit) and skipped; `report(account, "ok", "")` after one that worked. `pages` > 1 lets Google and Outlook return more than
        one page (a search over a long span)."""
        accts = [a for a in self.accounts() if wanted is None or wanted(a)]

        async def one(acct: CalAccount) -> list[dict[str, Any]]:
            try:
                got = await asyncio.wait_for(self.events_of(acct, start, end, all_day=all_day, tool=tool, pages=pages), ACCOUNT_TIMEOUT)
            except asyncio.TimeoutError:
                code, detail = "slow", f"no answer within {int(ACCOUNT_TIMEOUT)} seconds"
            except CalDavError as e:
                code, detail = e.code, str(e)
            except Exception as e:  # noqa: BLE001 - one calendar failing never stops the others
                code, detail = classify(e), str(e)
            else:
                if report is not None:
                    report(acct, "ok", "")
                return got
            log.info("calendar %s failed (%s)", acct.id, detail[:120])
            if report is not None:
                report(acct, code, detail[:200])
            return []

        out = [e for evs in await asyncio.gather(*(one(a) for a in accts)) for e in evs]
        return sorted(out, key=lambda e: e["start"])

    # ---- adding a CalDAV account of your own
    async def connect(self, url: str, user: str, password: str) -> dict[str, Any]:
        """A CalDAV account with its own address (Fastmail, Zoho, Nextcloud ...): signed in once to check it, only then saved."""
        url, user = (url or "").strip(), (user or "").strip()
        if "://" not in url:
            url = "https://" + url
        parts = urlsplit(url)
        try:
            host = valid_host(parts.hostname or "")
        except NetGuardError:
            raise CalDavError("other", t("Please check the server address.")) from None
        if parts.scheme != "https" or parts.port not in (None, 443) or parts.username or parts.password or len(url) > 300:
            raise CalDavError("other", t("Please check the server address."))
        if not user or len(user) > 200 or any(c in user for c in "\r\n\x00"):
            raise CalDavError("other", t("Please check the user name."))
        if not password or len(password) > 200 or any(c in password for c in "\r\n\x00"):
            raise CalDavError("other", t("Please check the password."))
        client = CalDavClient(url, user, password, host_ok=lambda h: h == host, fetcher=self.fetcher)
        try:
            await client.calendars()
        except CalDavError as e:
            if e.code == "auth":
                raise CalDavError("auth", t("The sign-in was refused. Nothing was saved.") + " " + t("Please check the user name and whether an app password is needed.")) from None
            if e.code == "off":
                raise CalDavError("off", t("This address offers no calendar over CalDAV. Nothing was saved.")) from None
            raise CalDavError(e.code, t("Could not reach the {name} server. Please try again in a moment.", name=host)) from None
        slug = re.sub(r"[^a-z0-9]+", "-", f"{host}-{user}".lower()).strip("-")[:40]
        name = f"{GENERIC_PREFIX}{slug}-{hashlib.sha256(f'{url}|{user}'.encode()).hexdigest()[:6]}"
        self.store.delete_credential(name)
        self.store.save_credential(name, f"{t(CALDAV_NAME)} · {user}", [host], {"type": "caldav", "url": url}, "", kind="email")
        self.store.set_credential_secret(name, json.dumps({"u": user, "p": password}))
        self.store.set_credential_result(name, "ok", "signed in when it was added")
        self.store.add_vault_log(name, "saved", "CalDAV calendar, checked with a sign-in")
        self._homes.pop(f"caldav:{name}", None)
        return {"name": name, "label": f"{t(CALDAV_NAME)} · {user}", "address": user}


def classify(e: Exception) -> str:
    """auth | off | unreachable | other for an error from a provider that has no codes of its own (mail, Google, Outlook)."""
    msg = str(e).lower()
    if isinstance(e, VaultError):
        return "other"
    if any(w in msg for w in ("refused the sign-in", "ran out", "did not renew", "http 401", "http 403", "sign-in")):
        return "auth"
    if any(w in msg for w in ("could not be reached", "timed out", "did not complete", "could not resolve", "http 5")):
        return "unreachable"
    return "other"


__all__ = ["CALDAV", "CalAccount", "CalDavClient", "CalDavError", "Calendars", "NO_CALDAV", "classify", "expand", "generic_accounts", "multistatus"]
