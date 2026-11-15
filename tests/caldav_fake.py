"""A fake CalDAV server for the tests: a real HTTP server on 127.0.0.1 (in a thread) that answers the requests calendars.py makes
(PROPFIND for the principal, the calendar home and the calendars; REPORT calendar-query) the way iCloud, Yahoo and others do, with
Basic authentication. `fetcher` is what Calendars/CalDavClient get instead of netguard.fetch: it checks the pinned host the same way
and sends the request to the fake server on this machine, so the wire format is the real one."""

from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from assistant.netguard import NetGuardError, Reply

SEOUL_STANDUP = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//kelo-test//EN
BEGIN:VEVENT
UID:standup@test
DTSTART;TZID=Asia/Seoul:20261102T090000
DTEND;TZID=Asia/Seoul:20261102T093000
RRULE:FREQ=DAILY;COUNT=20
EXDATE;TZID=Asia/Seoul:20261109T090000
SUMMARY:Standup
LOCATION:Room 4
END:VEVENT
END:VCALENDAR
"""

# a weekly meeting in New York time whose Nov 9 turn was moved by one hour (RECURRENCE-ID)
NEW_YORK_REVIEW = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//kelo-test//EN
BEGIN:VEVENT
UID:review@test
DTSTART;TZID=America/New_York:20261102T200000
DTEND;TZID=America/New_York:20261102T210000
RRULE:FREQ=WEEKLY;COUNT=6
SUMMARY:Review
END:VEVENT
BEGIN:VEVENT
UID:review@test
RECURRENCE-ID;TZID=America/New_York:20261109T200000
DTSTART;TZID=America/New_York:20261109T210000
DTEND;TZID=America/New_York:20261109T220000
SUMMARY:Review (moved)
END:VEVENT
END:VCALENDAR
"""

SIMPLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//kelo-test//EN
BEGIN:VEVENT
UID:lunch@test
DTSTART:20261109T120000
SUMMARY:Lunch with Mina
LOCATION:Gangnam station
END:VEVENT
BEGIN:VEVENT
UID:call@test
DTSTART:20261109T010000Z
DTEND:20261109T011500Z
SUMMARY:Call from the bank
END:VEVENT
BEGIN:VEVENT
UID:holiday@test
DTSTART;VALUE=DATE:20261109
DTEND;VALUE=DATE:20261110
SUMMARY:Holiday
END:VEVENT
BEGIN:VEVENT
UID:cancelled@test
DTSTART:20261109T050000Z
STATUS:CANCELLED
SUMMARY:Cancelled thing
END:VEVENT
BEGIN:VEVENT
UID:later@test
DTSTART:20261120T050000Z
SUMMARY:Next week
END:VEVENT
END:VCALENDAR
"""

# a calendar with X-WR-TIMEZONE: its times without a zone are Paris time
PARIS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//kelo-test//EN
X-WR-TIMEZONE:Europe/Paris
BEGIN:VEVENT
UID:paris@test
DTSTART:20261109T100000
DTEND:20261109T110000
SUMMARY:Paris office
END:VEVENT
END:VCALENDAR
"""

TASKS = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:x\nBEGIN:VTODO\nUID:t\nSUMMARY:todo\nEND:VTODO\nEND:VCALENDAR\n"


def multistatus(*responses: str) -> bytes:
    return ('<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            + "".join(responses) + "</d:multistatus>").encode()


def response(href: str, props: str) -> str:
    return f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class FakeCalDav:
    """`user`: the only user name it accepts (None: any, with the right password). `calendars`: {"work": [ics, ...], ...}; a calendar named in `tasks` only supports VTODO. `host` is the name the account is
    reached by (what the absolute calendar-home-set address names)."""

    def __init__(self, calendars: dict[str, list[str]] | None = None, *, user: str | None = None, password: str = "app-pass-123",
                 host: str = "caldav.example.test", home_host: str | None = None, tasks: tuple[str, ...] = (), principal: bool = True,
                 status: int | None = None, prefix: str = ""):
        self.calendars = calendars if calendars is not None else {"work": [SEOUL_STANDUP, NEW_YORK_REVIEW], "personal": [SIMPLE]}
        self.user, self.password, self.host, self.tasks, self.principal, self.status = user, password, host, tasks, principal, status
        self.prefix = prefix   # the address the service answers under (Fastmail: /dav/)
        self.home_host = home_host or host
        self.requests: list[dict[str, Any]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                fake.requests.append({"method": self.command, "path": self.path, "depth": self.headers.get("Depth"),
                                      "auth": self.headers.get("Authorization"), "body": body.decode(errors="replace")})
                status, payload = fake.answer(self.command, self.path, self.headers.get("Authorization") or "", body.decode(errors="replace"))
                self.send_response(status)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_PROPFIND = do_REPORT = do_GET = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeCalDav":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    # ---- the protocol
    def answer(self, method: str, path: str, auth: str, body: str) -> tuple[int, bytes]:
        try:
            who, _, pw = base64.b64decode(auth.removeprefix("Basic ")).decode().partition(":")
        except ValueError:
            who, pw = "", ""
        if not auth.startswith("Basic ") or pw != self.password or (self.user is not None and who != self.user):
            return 401, b"unauthorized"
        if self.status:
            return self.status, b"nope"
        if self.prefix and path.startswith(self.prefix):
            path = "/" + path[len(self.prefix):]
        home = f"https://{self.home_host}/home/"
        if method == "PROPFIND" and path == "/" or path.startswith("/principals/users/") and "current-user-principal" in body:
            if not self.principal:
                return 404, b"not found"
            return 207, multistatus(response(path, "<d:current-user-principal><d:href>/principals/u1/</d:href></d:current-user-principal>"))
        if method == "PROPFIND" and path == "/principals/u1/" and "calendar-home-set" in body:
            return 207, multistatus(response(path, f"<c:calendar-home-set><d:href>{home}</d:href></c:calendar-home-set>"))
        if method == "PROPFIND" and path == "/home/":
            rows = [response("/home/", "<d:resourcetype><d:collection/></d:resourcetype><d:displayname>home</d:displayname>")]
            for name in self.calendars:
                comp = "VTODO" if name in self.tasks else "VEVENT"
                rows.append(response(f"/home/{name}/", "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                                     f"<d:displayname>{name}</d:displayname><c:supported-calendar-component-set><c:comp name=\"{comp}\"/>"
                                     "</c:supported-calendar-component-set>"))
            return 207, multistatus(*rows)
        if method == "REPORT" and path.startswith("/home/") and "calendar-query" in body:
            name = path.strip("/").split("/")[-1]
            if name not in self.calendars:
                return 404, b"no such calendar"
            rows = [response(f"/home/{name}/{i}.ics", f"<c:calendar-data>{escape(ics)}</c:calendar-data>") for i, ics in enumerate(self.calendars[name])]
            return 207, multistatus(*rows)
        return 404, b"not found"

    # ---- what the client sends instead of netguard.fetch
    async def fetcher(self, method: str, url: str, *, allowed_hosts: list[str], headers: dict[str, str] | None = None,
                      content: bytes | None = None, **_: Any) -> Reply:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "") not in allowed_hosts or parts.port not in (None, 443):
            raise NetGuardError(f"{parts.hostname} is not a registered host")
        self.hosts_used = getattr(self, "hosts_used", [])
        self.hosts_used.append(parts.hostname)
        target = f"http://127.0.0.1:{self.port}{parts.path or '/'}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.request(method, target, headers=headers, content=content)
        return Reply(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content, False)
