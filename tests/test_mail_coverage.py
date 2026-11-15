"""Every mailbox and every calendar is read, whatever the connection (mailboxes.COVERAGE): a connector added to the catalog that looks
like mail or a calendar must be wired in (and is then checked to show up in the briefing's accounts and the email tools once connected)
or be listed in NOT_A_SOURCE with the reason. Kelo Nest connectors are covered by their `provides`: installed, they join the same
sources with no code change, and a package that declares mail without its required operations is refused. Fakes only."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from assistant.briefing_sources import Sources
from assistant.calendars import Calendars
from assistant.catalog import CATALOG
from assistant.connect_ways import ways
from assistant.google_calendar import GoogleCalendar
from assistant.mail import Mail, Selection
from assistant.mailboxes import COVERAGE, NOT_A_SOURCE, Mailboxes
from assistant.mcp_oauth import SERVICES as MCP
from assistant.microsoft_calendar import MicrosoftCalendar
from assistant.microsoft_mail import GraphMail
from assistant.nest_sources import NestCalendars, NestMail
from assistant.packages import MANIFEST, PackageError, load_package
from assistant.phone_calendar import PhoneCalendars
from assistant.store import Store
from assistant.tools.mail_tools import mail_tools

from test_nest import World, copy_pkg, installed

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "packages" / "example_mail"
LOOKS_LIKE = re.compile(r"\b(e-?mails?|mail|mailbox|inbox|calendars?|schedul\w*|events?|meetings?|invitations?)\b", re.I)


def test_every_connector_that_looks_like_mail_or_a_calendar_is_read_or_says_why():
    """The catalog's names and descriptions and the MCP blurbs: anything about mail, calendars, schedules, events or meetings is either
    read (COVERAGE) or listed with a reason (NOT_A_SOURCE). A new connector in neither table fails here."""
    found = {s.id for s in CATALOG if LOOKS_LIKE.search(f"{s.name} {s.description}")}
    found |= {sid for sid, svc in MCP.items() if LOOKS_LIKE.search(f"{svc.name} {svc.blurb}")}
    missing = sorted(found - set(COVERAGE) - set(NOT_A_SOURCE))
    assert not missing, f"connectors that look like mail or a calendar, neither read nor explained (mailboxes.COVERAGE / NOT_A_SOURCE): {missing}"
    ids = {s.id for s in CATALOG}
    assert set(COVERAGE) <= ids and set(NOT_A_SOURCE) <= ids | set(MCP)   # no stale entries
    assert not set(COVERAGE) & set(NOT_A_SOURCE) and all(len(r) > 20 for r in NOT_A_SOURCE.values())
    assert {k for kinds in COVERAGE.values() for k in kinds} == {"mail", "calendar"}


class NoGoogleWeb:
    def open_here(self) -> bool:
        return True


def connect(store: Store, cid: str) -> None:
    """The connection a catalog entry makes, as the vault holds it once the owner connected it."""
    def put(name: str, label: str, hosts: list[str], placement: dict[str, Any], secret: str, kind: str) -> None:
        store.save_credential(name, label, hosts, placement, "", kind=kind)
        store.set_credential_secret(name, secret)
    if cid == "gmail":
        put("mail-gmail", "Gmail · me@gmail.com", ["imap.gmail.com", "smtp.gmail.com"], {"type": "mailbox"}, json.dumps({"u": "me@gmail.com", "p": "x"}), "email")
    elif cid == "imap_email":
        put("mail-naver", "Naver Mail · me@naver.com", ["imap.naver.com", "smtp.naver.com"], {"type": "mailbox"}, json.dumps({"u": "me@naver.com", "p": "x"}), "email")
    elif cid in ("outlook_mail", "outlook_calendar"):
        put("microsoft-api", "Microsoft · me@outlook.com", ["graph.microsoft.com"], {"type": "bearer_header"}, "token", "api")
    elif cid == "google_calendar":
        put("google-calendar", "Google Calendar · me@gmail.com", ["www.googleapis.com"], {"type": "google_oauth"},
            json.dumps({"u": "me@gmail.com", "scopes": ["https://www.googleapis.com/auth/calendar.readonly"]}), "api")
    elif cid == "caldav_calendar":
        put("caldav-dav-example-com-me-abc123", "CalDAV calendar · me", ["dav.example.com"], {"type": "caldav", "url": "https://dav.example.com/"},
            json.dumps({"u": "me", "p": "x"}), "email")
    else:
        raise AssertionError(f"COVERAGE has {cid} but this test does not know how it connects: add it here")


def world(store: Store, nest: Any = None) -> tuple[Mailboxes, Calendars, Sources]:
    mail = Mail(store)
    boxes = Mailboxes(mail, GraphMail(store), NestMail(nest, store) if nest is not None else None)
    cals = Calendars(store, mail, PhoneCalendars(store), GoogleCalendar(store, NoGoogleWeb()), MicrosoftCalendar(store),
                     nest=NestCalendars(nest) if nest is not None else None)
    return boxes, cals, Sources(store, cals, boxes)


@pytest.mark.parametrize("cid", sorted(COVERAGE))
def test_each_connection_type_shows_up_in_the_briefing_and_the_tools_once_connected(cid, tmp_path):
    store = Store(tmp_path / "s.db")
    boxes, cals, src = world(store)
    assert not src.accounts("mail") and not src.accounts("calendar")
    connect(store, cid)
    row = ways(store, Mail(store), browser=False, google_open=True)[cid]
    assert row["connected"], f"{cid}: the Connectors tab does not see the connection this test made"
    for kind in COVERAGE[cid]:
        accts = src.accounts(kind)
        assert accts, f"{cid} is connected but the briefing reads no {kind} account from it"
        assert all(a["on"] for a in src.public()[kind]["accounts"])   # a new account starts on
        if kind == "mail":
            names = {a["name"] for a in boxes.accounts()}
            assert {a.item for a in accts} <= names                     # the email tools reach the same mailboxes
            for a in accts:
                backend, name = boxes._pick(a.item)
                assert name == a.item and backend is not None
    for kind in {"mail", "calendar"} - set(COVERAGE[cid]):
        if cid not in ("outlook_mail", "outlook_calendar"):            # one Outlook sign-in serves both
            assert not src.accounts(kind), f"{cid} reads {kind} although COVERAGE does not say so"


# ---------------------------------------------------------------------------------------------------------------- Nest
class FakeService:
    """The example mail service's MCP tools."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.unread = {"m1", "m2"}

    async def __call__(self, p: Any, tool: str, args: dict[str, Any]) -> Any:
        self.calls.append((tool, args))
        if tool == "search_messages":
            rows = [{"id": "m1", "subject": "Lunch tomorrow?", "from": {"name": "Ana", "email": "ana@example.org"}, "received": "2026-11-10T08:00:00Z",
                     "unread": "m1" in self.unread, "promotion": False},
                    {"id": "m2", "subject": "50% off everything", "from": "deals@shop.example.com", "received": "2026-11-10T07:00:00Z",
                     "unread": "m2" in self.unread, "promotion": True}]
            return {"messages": [r for r in rows if r["unread"] or not args.get("unread_only")]}
        if tool == "get_message":
            return {"subject": "Lunch tomorrow?", "body": "Are you free at noon?", "from": "ana@example.org", "received": "2026-11-10T08:00:00Z"}
        if tool == "set_read":
            (self.unread.discard if args["read"] else self.unread.add)(args["id"])
            return {"ok": True}
        if tool == "move_message":
            return {"ok": True}
        if tool == "list_events":
            return {"events": [{"id": "e1", "title": "Flight to Shanghai", "start": "2026-11-29T09:00:00+09:00", "end": "2026-11-29T11:30:00+09:00",
                                "location": "ICN"},
                               {"id": "e2", "title": "Trip", "start": "2026-11-29", "all_day": True}]}
        raise AssertionError(tool)


async def test_a_nest_connector_that_provides_mail_and_a_calendar_joins_every_feature_with_no_code(tmp_path):
    w = World(tmp_path)
    await installed(w, EXAMPLE)
    w.store.set_profile(timezone="Asia/Seoul")
    svc = FakeService()
    w.nest._mcp = svc
    boxes, cals, src = world(w.store, w.nest)
    assert not src.accounts("mail")                                   # installed but not connected yet
    w.store.save_credential("pkg-example_mail", "Example Mail", ["mcp.mail.example.com"], {"type": "bearer_header"}, "", kind="api")
    w.store.set_credential_secret("pkg-example_mail", "token")
    assert [(a.id, t_) for a in src.accounts("mail") for t_ in [a.name]] == [("mail:nest-example_mail", "Example Mail")]
    assert [a.id for a in src.accounts("calendar")] == ["nest:example_mail"]
    # the briefing / heads-ups path: unread, without the promotion
    view = src.mail_view()
    assert [a["name"] for a in view.accounts()] == ["nest-example_mail"]
    rows = await view.list("nest-example_mail", unread=True, limit=5, skip_bulk=True)
    assert [r["subject"] for r in rows] == ["Lunch tomorrow?"] and svc.calls[-1] == ("search_messages", {"unread_only": True, "max": 5, "q": "", "folder": "INBOX"})
    # the chat's email tools
    tools = {t.name: t for t in mail_tools(boxes)}
    listed = await tools["email_list"].handler({"account": "nest-example_mail", "unread": True})
    assert "Lunch tomorrow?" in listed and "50% off everything" in listed
    uid = re.search(r"uid (\d+) \(unread\) · \S+ · from Ana <ana@example.org> · Lunch", listed).group(1)
    assert "Are you free at noon?" in await tools["email_read"].handler({"account": "nest-example_mail", "uid": uid})
    out = await boxes.update("nest-example_mail", "read", Selection.of(uids=[uid]))
    assert out["done"] == 1 and svc.calls[-1] == ("set_read", {"id": "m1", "read": True}) and "m1" not in svc.unread
    out = await boxes.update("nest-example_mail", "trash", Selection.of(uids=[uid]))
    assert out["done"] == 1 and svc.calls[-1] == ("move_message", {"id": "m1", "to": "trash"})
    # calendar_events
    from datetime import datetime
    from zoneinfo import ZoneInfo
    seoul = ZoneInfo("Asia/Seoul")
    evs, read, _left, failed = await src.events(datetime(2026, 11, 28, tzinfo=seoul), datetime(2026, 12, 1, tzinfo=seoul))
    assert [e["title"] for e in evs] == ["Trip", "Flight to Shanghai"] and not failed and evs[0]["all_day"]
    # the owner takes "reads your mail" back: it is no longer read
    w.nest.set_permissions("example_mail", revoke=["reads.mail"])
    assert not src.accounts("mail") and [a.id for a in src.accounts("calendar")] == ["nest:example_mail"]


def test_every_installed_nest_package_that_provides_mail_or_a_calendar_is_a_source(tmp_path):
    """Whatever an installed package provides is read: the sources list exactly the installed, connected ones that provide it."""
    w = World(tmp_path)

    class Reg:
        def installed(self, kind: str | None = None):
            return [load_package(EXAMPLE)]
    w.nest.registry = Reg()
    w.store.save_credential("pkg-example_mail", "Example Mail", ["mcp.mail.example.com"], {"type": "bearer_header"}, "", kind="api")
    w.store.set_credential_secret("pkg-example_mail", "token")
    w.nest.granted = lambda p: {"reads": list(p.permissions["reads"])}
    boxes, cals, src = world(w.store, w.nest)
    for p in w.nest.registry.installed("connector"):
        if "mail" in p.provides:
            assert f"mail:nest-{p.id}" in {a.id for a in src.accounts("mail")}
        if "calendar" in p.provides:
            assert f"nest:{p.id}" in {a.id for a in src.accounts("calendar")}


@pytest.mark.parametrize("change, why", [
    (lambda m: m["provides"]["mail"].pop("trash"), "needs the operations trash"),
    (lambda m: m["provides"]["mail"]["list"]["fields"].pop("uid"), "fields needs uid"),
    (lambda m: m["provides"]["calendar"]["events"]["fields"].pop("start"), "fields needs start"),
    (lambda m: m["provides"]["mail"]["read"]["arguments"].update(id="{{secret}}"), "is not one of uid"),
    (lambda m: m["provides"].update(contacts={}), "unknown"),
    (lambda m: m["permissions"]["reads"].remove("mail"), "declare mail in permissions.reads"),
    (lambda m: m["permissions"]["mcp_tools"].remove("set_read"), "declare set_read in permissions.mcp_tools"),
    (lambda m: m["provides"]["mail"]["archive"].update(request={"method": "POST", "path": "/archive"}), "either a tool or a request"),
])
def test_an_incomplete_or_undeclared_capability_is_refused(tmp_path, change, why):
    folder = copy_pkg(EXAMPLE, tmp_path / "example_mail", change)
    with pytest.raises(PackageError) as e:
        load_package(folder)
    assert why in str(e.value), str(e.value)


def test_a_request_needs_an_api_and_a_tool_needs_an_mcp_server(tmp_path):
    def to_key(m: dict) -> None:
        m["connector"]["connect"] = {"type": "key", "hosts": ["api.mail.example.com"], "base_url": "https://api.mail.example.com/v1"}
        m["permissions"]["hosts"] = ["api.mail.example.com"]
    with pytest.raises(PackageError) as e:
        load_package(copy_pkg(EXAMPLE, tmp_path / "example_mail", to_key))
    assert "needs an MCP connection" in str(e.value)

    def as_requests(m: dict) -> None:
        to_key(m)
        for cap, ops in m["provides"].items():
            for op in ops.values():
                op.pop("tool"), op.pop("arguments")
                op["request"] = {"method": "GET", "path": "/x/{{uid}}" if cap == "mail" and "uid" in json.dumps(op) else "/x"}
        m["provides"]["mail"]["read"]["request"]["path"] = "/messages/{{uid}}"
        m["provides"]["mail"]["list"]["request"]["path"] = "/messages?unread={{unread}}&q={{query}}"
        m["permissions"]["mcp_tools"] = []
    p = load_package(copy_pkg(EXAMPLE, tmp_path / "b" / "example_mail", as_requests))
    assert p.provides["mail"]["read"]["request"]["path"] == "/messages/{{uid}}" and p.permissions["reads"] == ("mail", "calendar")
    assert json.loads((EXAMPLE / MANIFEST).read_text())["provides"]   # the example itself stays a complete one
