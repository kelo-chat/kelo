"""briefing_sources.py: which connected calendars and mailboxes the briefing and the proactive pass read. Everything by default;
one switch per account (a new account starts on; older "only these" settings carried over); the agent's tool; one account failing never breaks the rest and
shows as a quiet line; the briefing card with three or more sources and the label of each. Scripted model, fake CalDAV server,
fake weather: no network, no real accounts."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant import i18n
from assistant.briefing import Briefing
from assistant.briefing_sources import SelectedMail, Sources, sources_routes
from assistant.calendars import Calendars
from assistant.google_calendar import CalendarError
from assistant.home import Home
from assistant.mail import Mail
from assistant.phone_calendar import PhoneCalendars
from assistant.proactive import Proactive
from assistant.store import Store
from assistant.weather import Place, Weather

from caldav_fake import FakeCalDav
from weather_fakes import FakeWeather

SEOUL = ZoneInfo("Asia/Seoul")


def ts(h: int, m: int = 0, day: int = 9) -> float:
    return datetime(2026, 11, day, h, m, tzinfo=SEOUL).timestamp()


NOW = ts(7, 30)


class FakeGoogle:
    def __init__(self, accounts: list[tuple[str, str]], events: list[dict[str, Any]] | None = None, fail: bool = False):
        self._accounts, self.rows, self.fail = accounts, events or [], fail

    def accounts(self) -> list[dict[str, Any]]:
        return [{"name": n, "address": a} for n, a in self._accounts]

    async def events(self, account: str, start: datetime, end: datetime, **_: Any) -> list[dict[str, Any]]:
        if self.fail:
            raise CalendarError("Google did not renew the sign-in")
        return [{"id": "g" + str(i), "title": r["title"], "start": datetime.fromtimestamp(r["at"], SEOUL), "end": None, "all_day": False,
                 "location": r.get("where", "")} for i, r in enumerate(self.rows)]


class FakeOutlook:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows

    def connected(self) -> bool:
        return self.rows is not None

    async def between(self, start: datetime, end: datetime, *, all_day: bool = False, tool: str = "", **_: Any) -> list[dict[str, Any]]:
        return [{"id": "outlook:" + r["title"], "title": r["title"], "start": r["at"], "where": "", "all_day": bool(r.get("all_day")),
                 **({"day": r["day"]} if r.get("day") else {})}
                for r in self.rows or [] if (all_day or not r.get("all_day")) and start.timestamp() <= r["at"] < end.timestamp()]


class FakeMail(Mail):
    """The real account list (from the vault items); the mailboxes' unread mail is scripted, a mailbox can be made to fail."""

    def __init__(self, store: Store, unread: dict[str, list[dict[str, Any]]] | None = None, failing: set[str] = frozenset()):  # type: ignore[assignment]
        super().__init__(store)
        self.unread, self.failing, self.asked = unread or {}, set(failing), []

    async def list(self, account: str = "", folder: str = "INBOX", query: str = "", limit: int = 10, unread: bool = False,
                   skip_bulk: bool = False) -> list[dict[str, Any]]:
        self.asked.append(account)
        if account in self.failing:
            from assistant.mail import MailError
            raise MailError("the mail server refused the sign-in; ask the user to connect the mailbox again in the Connectors tab")
        return self.unread.get(account, [])

    async def arrivals(self, account: str, cursor, kind: str = "inbox"):
        """What the proactive pass reads: whatever is scripted counts as arrived since the cursor (the cursor logic: test_mail_arrivals.py)."""
        self.asked.append(account)
        if kind != "inbox":
            return None
        if account in self.failing:
            from assistant.mail import MailError
            raise MailError("the mail server refused the sign-in; ask the user to connect the mailbox again in the Connectors tab")
        return {"rows": self.unread.get(account, []), "cursor": {"t": 1}}


def mailbox(store: Store, provider: str, address: str, password: str = "app-pass-123") -> str:
    name = f"mail-{provider}"
    store.save_credential(name, f"{provider} · {address}", [f"imap.{provider}.test"], {"type": "mailbox"}, "", kind="email")
    store.set_credential_secret(name, json.dumps({"u": address, "p": password}))
    return name


def build(store: Store, *, fake: FakeCalDav | None = None, google: FakeGoogle | None = None, outlook: FakeOutlook | None = None,
          phone: list[dict[str, Any]] | None = None, mail: Mail | None = None, clock=lambda: NOW) -> tuple[Sources, Calendars, Mail]:
    phones = PhoneCalendars(store, clock=clock)
    if phone is not None:
        phones.put("owner", {"device_name": "Pixel", "events": [{"title": p["title"], "start": p["at"], "end": p["at"] + 1800,
                                                                  "location": p.get("where", "")} for p in phone]})
    store.set_profile(timezone="Asia/Seoul")
    mail = mail or FakeMail(store)
    cal = Calendars(store, mail, phones, google or FakeGoogle([]), outlook or FakeOutlook(), fetcher=(fake.fetcher if fake else None) or _no_fetch,
                    timezone_name=lambda: "Asia/Seoul", clock=clock)
    return Sources(store, cal, mail, clock=clock), cal, mail


async def _no_fetch(*a: Any, **kw: Any) -> Any:
    raise AssertionError("no CalDAV request was expected")


def ids(rows: list[dict[str, Any]]) -> list[str]:
    return [r["id"] for r in rows]


def full_store(tmp_path) -> Store:
    store = Store(tmp_path / "s.db")
    mailbox(store, "naver", "kang@naver.test")
    mailbox(store, "icloud", "kang@icloud.test")
    mailbox(store, "daum", "kang@daum.test")
    store.save_credential("microsoft-api", "Microsoft · work@corp.test", ["graph.microsoft.com"], {"type": "bearer_header"}, "", kind="api")
    store.set_credential_secret("microsoft-api", "tok")
    return store


# ------------------------------------------------------------------------------------------------------ the accounts and the choice
def test_by_default_every_connected_calendar_and_mailbox_is_read(tmp_path):
    store = full_store(tmp_path)
    src, _, _ = build(store, google=FakeGoogle([("google-calendar", "me@gmail.test")]), outlook=FakeOutlook([]), phone=[])
    pub = src.public()
    assert ids(pub["calendar"]["accounts"]) == ["phone", "google:google-calendar", "outlook:microsoft-api", "caldav:mail-naver", "caldav:mail-icloud"]
    assert ids(pub["mail"]["accounts"]) == ["mail:mail-naver", "mail:mail-icloud", "mail:mail-daum"]   # Daum has mail but no CalDAV calendar
    assert "mode" not in pub["calendar"] and all(a["on"] for k in ("calendar", "mail") for a in pub[k]["accounts"])
    names = {a["id"]: (a["name"], a["address"]) for a in pub["calendar"]["accounts"]}
    assert names["caldav:mail-naver"] == ("Naver Calendar", "kang@naver.test") and names["outlook:microsoft-api"][1] == "work@corp.test"


def test_one_switch_per_account_and_a_new_account_starts_on(tmp_path):
    store = full_store(tmp_path)
    src, _, _ = build(store, phone=[])
    src.set_group("mail", accounts={"mail:mail-naver": False})
    assert [a["id"] for a in src.public()["mail"]["accounts"] if a["on"]] == ["mail:mail-icloud", "mail:mail-daum"]
    mailbox(store, "yahoo", "kang@yahoo.test")                       # connected later: on at once
    assert src.wanted("mail", "mail:mail-yahoo") and not src.wanted("mail", "mail:mail-naver")
    src.set_group("mail", accounts={"mail:mail-daum": False, "mail:mail-naver": True})
    assert [a.id for a in src.selected("mail")] == ["mail:mail-naver", "mail:mail-icloud", "mail:mail-yahoo"]
    store.delete_credential("mail-daum")
    mailbox(store, "daum", "kang@daum.test")
    assert not src.wanted("mail", "mail:mail-daum")                   # connected again: its switch is remembered
    assert src.config()["calendar"] == {"off": []}                    # calendars are switched on their own
    with pytest.raises(ValueError):
        src.set_group("mail", accounts={"mail:nope": True})
    with pytest.raises(ValueError):
        src.set_group("feed")


def test_an_older_servers_modes_become_switches(tmp_path):
    store = full_store(tmp_path)
    src, _, _ = build(store, phone=[{"title": "x", "at": ts(9)}])
    # "only these" for mail (Naver chosen), "all except" for calendars (iCloud left out)
    store.set_meta("briefing:sources", json.dumps({"mail": {"mode": "only", "ids": ["mail:mail-naver"]},
                                                   "calendar": {"mode": "all", "ids": ["caldav:mail-icloud"]}}))
    assert src.config() == {"calendar": {"off": ["caldav:mail-icloud"]}, "mail": {"off": ["mail:mail-icloud", "mail:mail-daum"]}}
    assert json.loads(store.meta("briefing:sources")) == src.config()   # saved in the new shape once
    assert [a.id for a in src.selected("mail")] == ["mail:mail-naver"]
    mailbox(store, "yahoo", "kang@yahoo.test")                         # a new account starts on now
    assert src.wanted("mail", "mail:mail-yahoo")
    assert [a.id for a in src.selected("calendar")] == ["phone", "caldav:mail-naver", "caldav:mail-yahoo"]   # its calendar too


async def test_only_the_calendars_chosen_are_read(tmp_path):
    store = full_store(tmp_path)
    with FakeCalDav(host="caldav.calendar.naver.com") as fake:
        src, _, _ = build(store, fake=fake, google=FakeGoogle([("google-calendar", "me@gmail.test")], [{"title": "Google thing", "at": ts(9)}]),
                          outlook=FakeOutlook([{"title": "Outlook thing", "at": ts(10)}]), phone=[{"title": "Phone thing", "at": ts(11)}])
        store.delete_credential("mail-icloud")
        fake.home_host = "caldav.calendar.naver.com"
        everything = await src.upcoming_events(24)
        assert {e["title"] for e in everything} >= {"Google thing", "Outlook thing", "Phone thing", "Lunch with Mina"}
        assert {e["source"] for e in everything} == {"Google", "Outlook", "Phone calendar", "Naver"}   # four calendars: each event says which
        src.set_group("calendar", accounts={"phone": False, "outlook:microsoft-api": False, "google:google-calendar": False})
        got = await src.upcoming_events(24)
        assert {e["source_id"] for e in got} == {"caldav:mail-naver"} and all("source" not in e for e in got)   # one calendar: no labels
        src.set_group("calendar", accounts={"phone": True, "outlook:microsoft-api": True, "google:google-calendar": True, "caldav:mail-naver": False})
        assert "Lunch with Mina" not in {e["title"] for e in await src.upcoming_events(24)}


# ------------------------------------------------------------------------------------------------------ the agent's tool
async def test_the_tool_sets_it_from_the_chat_and_says_what_it_did(tmp_path):
    store = full_store(tmp_path)
    mailbox(store, "yahoo", "work@company.test")
    src, _, _ = build(store, google=FakeGoogle([("google-calendar", "me@gmail.test"), ("google-calendar-2", "personal@gmail.test")]), phone=[])
    tool = src.tool()
    assert tool.name == "briefing_sources" and not tool.requires_approval and tool.auto_approve is None   # no approval card
    shown = await tool.handler({"action": "show"})
    assert "mail:mail-yahoo | Yahoo Mail | work@company.test | read: yes" in shown and "google:google-calendar-2" in shown
    # "leave my work mail out"
    amb = await tool.handler({"action": "off", "accounts": ["work@company.test"]})      # a mailbox and a calendar of that address: ask
    assert amb.startswith("error: 'work@company.test' is both a calendar and a mailbox") and src.config()["mail"] == {"off": []}
    out = await tool.handler({"action": "off", "kind": "mail", "accounts": ["work@company.test"]})
    assert out.startswith("ok: switched off: work@company.test (mail)")
    assert "mail:mail-yahoo" not in [a.id for a in src.selected("mail")] and len(src.selected("calendar")) == len(src.accounts("calendar"))
    out = await tool.handler({"action": "off", "kind": "calendar", "accounts": ["personal@gmail.test"]})
    assert "switched off: personal@gmail.test (calendar)" in out and "google:google-calendar-2 | " in out and "read: no" in out
    assert "google:google-calendar-2" not in [a.id for a in src.selected("calendar")] and "google:google-calendar" in [a.id for a in src.selected("calendar")]
    # "put it back"
    await tool.handler({"action": "on", "kind": "mail", "accounts": ["work@company.test"]})
    assert "mail:mail-yahoo" in [a.id for a in src.selected("mail")]
    await tool.handler({"action": "off", "kind": "both", "accounts": ["naver"]})              # "naver" is a mailbox and a calendar: both
    assert "mail:mail-naver" not in {a.id for a in src.selected("mail")} and "caldav:mail-naver" not in {a.id for a in src.selected("calendar")}
    assert (await tool.handler({"action": "on", "accounts": ["all"]})).startswith("ok:")
    assert src.config() == {"calendar": {"off": []}, "mail": {"off": []}}
    # mistakes come back as words, nothing changes
    bad = await tool.handler({"action": "off", "accounts": ["nobody@nowhere.test"]})
    assert bad.startswith("error: no connected account matches") and "work@company.test" in bad
    assert (await tool.handler({"action": "off"})).startswith("error:") and (await tool.handler({"action": "only", "accounts": ["naver"]})).startswith("error:")
    assert (await tool.handler({"action": "off", "kind": "feed", "accounts": ["x"]})).startswith("error:")
    assert src.config() == {"calendar": {"off": []}, "mail": {"off": []}}
    assert set(tool.parameters["properties"]["action"]["enum"]) == {"show", "on", "off"}


# ------------------------------------------------------------------------------------------------------ failures
async def test_a_calendar_that_fails_is_skipped_and_shows_a_quiet_line_with_what_to_do(tmp_path):
    store = full_store(tmp_path)
    with FakeCalDav(host="caldav.calendar.naver.com", principal=False, calendars={}) as fake:     # Naver without CalDAV
        src, _, _ = build(store, fake=fake, google=FakeGoogle([("google-calendar", "me@gmail.test")], [{"title": "Google thing", "at": ts(9)}]),
                          phone=[{"title": "Phone thing", "at": ts(11)}])
        store.delete_credential("mail-icloud")
        evs = await src.upcoming_events(24)                             # does not raise
        assert {e["title"] for e in evs} == {"Google thing", "Phone thing"}
        row = next(a for a in src.public()["calendar"]["accounts"] if a["id"] == "caldav:mail-naver")
        assert row["on"] and row["problem"].startswith("Could not read Naver Calendar: ") and "CalDAV is not turned on" in row["problem"]
        assert "Naver does not document CalDAV" in row["problem"]
        tok = i18n.use("ko")
        try:
            ko = next(a for a in src.public()["calendar"]["accounts"] if a["id"] == "caldav:mail-naver")["problem"]
        finally:
            i18n.reset(tok)
        assert ko.startswith("네이버 캘린더: 읽지 못했어요. ") and "CalDAV" in ko
        assert all(a["problem"] is None for a in src.public()["calendar"]["accounts"] if a["id"] != "caldav:mail-naver")
        # a Google sign-in that ran out, as well
        src.calendars.google.fail = True
        evs = await src.upcoming_events(24)
        assert {e["title"] for e in evs} == {"Phone thing"}
        g = next(a for a in src.public()["calendar"]["accounts"] if a["id"] == "google:google-calendar")
        assert g["problem"].startswith("Could not read Google Calendar: The sign-in was refused. Connect it again in the Connectors tab.")
        # it works again: the line goes away
        src.calendars.google.fail = False
        await src.upcoming_events(24)
        assert next(a for a in src.public()["calendar"]["accounts"] if a["id"] == "google:google-calendar")["problem"] is None


async def test_an_account_switched_off_is_not_read_and_not_blamed(tmp_path):
    store = full_store(tmp_path)
    src, _, _ = build(store, google=FakeGoogle([("google-calendar", "me@gmail.test")], fail=True), phone=[{"title": "Phone thing", "at": ts(11)}])
    store.delete_credential("mail-naver")
    store.delete_credential("mail-icloud")
    src.set_group("calendar", accounts={"google:google-calendar": False})
    assert [e["title"] for e in await src.upcoming_events(24)] == ["Phone thing"]
    assert all(a["problem"] is None for a in src.public()["calendar"]["accounts"])


async def test_a_mailbox_that_fails_is_skipped_and_the_others_are_listed(tmp_path):
    store = full_store(tmp_path)
    unread = {"mail-icloud": [{"uid": "5", "subject": "Hello", "from": "Jin", "date": ""}], "mail-naver": [{"uid": "9", "subject": "Hidden", "from": "X"}]}
    mail = FakeMail(store, unread, failing={"mail-naver"})
    src, _, _ = build(store, mail=mail)
    view = src.mail_view()
    assert isinstance(view, SelectedMail) and [a["name"] for a in view.accounts()] == ["mail-naver", "mail-icloud", "mail-daum"]
    assert await view.list("mail-naver", unread=True) == [] and [m["uid"] for m in await view.list("mail-icloud", unread=True)] == ["5"]
    row = next(a for a in src.public()["mail"]["accounts"] if a["id"] == "mail:mail-naver")
    assert row["problem"].startswith("Could not read Naver Mail: The sign-in was refused. Check that IMAP is turned on")
    src.set_group("mail", accounts={"mail:mail-naver": False, "mail:mail-daum": False})
    assert [a["name"] for a in view.accounts()] == ["mail-icloud"]      # a mailbox left out is not even asked
    mail.asked.clear()
    await view.list("mail-icloud", unread=True)
    assert mail.asked == ["mail-icloud"]
    mail.failing.clear()
    src.set_group("mail", accounts={"mail:mail-naver": True})
    await view.list("mail-naver", unread=True)
    assert next(a for a in src.public()["mail"]["accounts"] if a["id"] == "mail:mail-naver")["problem"] is None


# ------------------------------------------------------------------------------------------------------ the API
async def test_the_api_lists_switches_and_checks(tmp_path):
    store = full_store(tmp_path)
    mail = FakeMail(store, failing={"mail-icloud"})
    src, _, _ = build(store, mail=mail, phone=[], google=FakeGoogle([("google-calendar", "me@gmail.test")]))
    app = FastAPI()
    sources_routes(app, src, lambda: None)
    c = TestClient(app)
    got = c.get("/api/briefing/sources").json()
    assert "mode" not in got["mail"] and [a["id"] for a in got["mail"]["accounts"]] == ["mail:mail-naver", "mail:mail-icloud", "mail:mail-daum"]
    assert {"id", "kind", "provider", "name", "address", "label", "on", "problem"} <= set(got["calendar"]["accounts"][0])
    r = c.put("/api/briefing/sources", json={"mail": {"accounts": {"mail:mail-daum": False}}}).json()
    assert [a["on"] for a in r["mail"]["accounts"]] == [True, True, False] and all(a["on"] for a in r["calendar"]["accounts"])
    assert c.put("/api/briefing/sources", json={"mail": {"accounts": {"mail:ghost": True}}}).status_code == 422
    checked = c.post("/api/briefing/sources/check")
    assert checked.status_code == 200
    by = {a["id"]: a for a in checked.json()["mail"]["accounts"]}
    assert by["mail:mail-icloud"]["problem"] and by["mail:mail-naver"]["problem"] is None   # the failing mailbox is found before tomorrow morning


# ------------------------------------------------------------------------------------------------------ the briefing card
def briefing(store: Store, src: Sources, tmp_path, *, now: float = NOW) -> Briefing:
    home = Home(tmp_path / "home")
    p = Proactive(store, home, stream_fn=None, model=lambda: None, clock=lambda: now, mail=src.mail_view(), calendar=src.upcoming_events)
    w = Weather(store, fetcher=FakeWeather(), clock=lambda: now, language=lambda: "en")
    w.set_here(37.566, 126.978, "서울특별시", "KR")
    store.add_message("user", "hi")
    return Briefing(store, w, p, home=home, mail=src.mail_view(), calendar=src.upcoming_events, sources=src, clock=lambda: now, language=lambda: "en")


async def test_the_card_with_four_calendars_and_two_mailboxes_labels_each_one_and_survives_a_failure(tmp_path):
    store = full_store(tmp_path)
    unread = {"mail-naver": [{"uid": "11", "subject": "Contract to sign", "from": "Kim"}], "mail-icloud": [{"uid": "21", "subject": "Dinner?", "from": "Mina"}]}
    mail = FakeMail(store, unread, failing={"mail-daum"})
    with FakeCalDav(host="caldav.calendar.naver.com") as fake:
        src, cal, _ = build(store, fake=fake, mail=mail, google=FakeGoogle([("google-calendar", "me@gmail.test")], [{"title": "Google thing", "at": ts(14), "where": "HQ"}]),
                            outlook=FakeOutlook([{"title": "Board", "at": ts(16)}]), phone=[{"title": "Dentist", "at": ts(10)}])
        store.delete_credential("mail-icloud")                # iCloud mail (and its calendar) is not part of this one
        mailbox(store, "icloud", "kang@icloud.test")          # ... but is back for the mail side, with a calendar that fails (the fake has no iCloud host)
        cal.fetcher = _icloud_fails(fake)
        b = briefing(store, src, tmp_path)
        store._run("INSERT INTO proactive_log(key, source, title, urgency, status, decided_at, expires_at, day) VALUES(?,?,?,?,?,?,?,?)",
                   ("mail:mail-naver:11", "mail", "Contract to sign", "오늘", "sent", NOW - 3600, NOW + 86400, "2026-11-09"))
        card = await b.build(NOW)
        by_title = {e["title"]: e for e in card["events"]}
        assert by_title["Dentist"]["source"] == "Phone calendar" and by_title["Board"]["source"] == "Outlook" and by_title["Google thing"]["source"] == "Google"
        # the Naver calendar's lunch and bank call are there (its Standup is EXDATEd today); iCloud failed and is simply missing
        assert [e["title"] for e in card["events"]] == ["Dentist", "Call from the bank", "Lunch with Mina", "Google thing", "Board"]
        assert by_title["Lunch with Mina"]["source"] == "Naver" and by_title["Lunch with Mina"]["where"] == "Gangnam station"
        mail_item = next(m for m in card["missed"] if m["kind"] == "mail")
        assert mail_item["title"] == "Contract to sign" and mail_item["source"] == "Naver"           # two mailboxes are read: labelled
        text = b.plain(card)
        assert "- 10:00 Dentist · Phone calendar" in text and "- 14:00 Google thing (HQ) · Google" in text and "- Contract to sign · Naver" in text
        pub = src.public()
        assert next(a for a in pub["calendar"]["accounts"] if a["id"] == "caldav:mail-icloud")["problem"]
        assert next(a for a in pub["mail"]["accounts"] if a["id"] == "mail:mail-daum")["problem"]

        # one calendar and one mailbox left: no labels, nothing else changes
        src.set_group("calendar", accounts={a.id: a.id == "phone" for a in src.accounts("calendar")})
        src.set_group("mail", accounts={a.id: a.id == "mail:mail-naver" for a in src.accounts("mail")})
        card = await b.build(NOW)
        assert [e["title"] for e in card["events"]] == ["Dentist"] and "source" not in card["events"][0]
        assert all("source" not in m for m in card["missed"])
        assert "· Phone calendar" not in b.plain(card)


def _icloud_fails(fake: FakeCalDav):
    async def fetcher(method, url, **kw):
        if "icloud" in kw["allowed_hosts"][0]:
            from assistant.netguard import NetGuardError
            raise NetGuardError("the request timed out; if it was a write, check the state before retrying")
        return await fake.fetcher(method, url, **kw)
    return fetcher


async def test_the_proactive_pass_reads_the_same_accounts(tmp_path):
    store = full_store(tmp_path)
    unread = {"mail-naver": [{"uid": "11", "subject": "Work thing", "from": "Kim", "date": ""}],
              "mail-icloud": [{"uid": "21", "subject": "Private thing", "from": "Mina", "date": ""}],
              "mail-daum": [{"uid": "31", "subject": "Daum thing", "from": "X", "date": ""}]}
    mail = FakeMail(store, unread, failing={"mail-daum"})
    src, _, _ = build(store, mail=mail, phone=[{"title": "Dentist", "at": ts(8)}])
    home = Home(tmp_path / "home")
    p = Proactive(store, home, stream_fn=None, model=lambda: None, clock=lambda: NOW, mail=src.mail_view(), calendar=src.upcoming_events)
    got, notes = await p.gather(NOW - 3600)
    assert {c.title for c in got if c.source == "mail"} == {"Work thing", "Private thing"} and any(c.title == "Dentist" for c in got)   # Daum failed: skipped
    src.set_group("mail", accounts={"mail:mail-naver": True, "mail:mail-icloud": False, "mail:mail-daum": False})
    got, notes = await p.gather(NOW - 3600)
    assert {c.title for c in got if c.source == "mail"} == {"Work thing"}


# ------------------------------------------------------------------------------------------------------ the whole app
async def test_the_app_adds_a_caldav_account_lists_it_in_connectors_and_reads_it(tmp_path):
    import httpx
    from assistant.server import Settings, create_app
    from test_server import AUTH, TOKEN, Running
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False, home_dir=str(tmp_path / "home"))
    with FakeCalDav(host="dav.fastmail.test", prefix="/dav/") as fake:
        app = create_app(s, tools=[], weather_fetcher=FakeWeather(), caldav_fetcher=fake.fetcher)
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            row = next(r for r in (await c.get("/api/connectors")).json() if r["id"] == "caldav_calendar")
            assert row["connect"] == "caldav" and row["status"] != "connected"
            bad = await c.post("/api/caldav/accounts", json={"url": "https://dav.fastmail.test/dav/", "username": "me@fastmail.test", "password": "nope"})
            assert bad.status_code == 422 and "refused" in bad.json()["detail"]
            assert (await c.post("/api/caldav/accounts", json={"url": "http://dav.fastmail.test/", "username": "u", "password": "p"})).status_code == 422
            ok = await c.post("/api/caldav/accounts", json={"url": "https://dav.fastmail.test/dav/", "username": "me@fastmail.test", "password": "app-pass-123"})
            assert ok.status_code == 200 and ok.json()["address"] == "me@fastmail.test" and "app-pass" not in ok.text
            assert (await c.get("/api/caldav")).json()["accounts"][0]["address"] == "me@fastmail.test"
            row = next(r for r in (await c.get("/api/connectors")).json() if r["id"] == "caldav_calendar")
            assert row["status"] == "connected" and row["account"] == "me@fastmail.test"
            got = (await c.get("/api/briefing/sources")).json()
            assert [(a["provider"], a["address"], a["on"]) for a in got["calendar"]["accounts"]] == [("caldav", "me@fastmail.test", True)]
            assert got["mail"]["accounts"] == []
            items = (await c.get("/api/vault")).json()["items"]    # the vault lists the item and never its value
            assert any(i["name"].startswith("caldav-") for i in items) and "app-pass" not in json.dumps(items)


# ------------------------------------------------------------------------------------------------------ the chat sees the same calendars
async def test_the_chat_tool_reads_every_chosen_calendar_with_all_day_events_and_says_what_it_could_not_read(tmp_path):
    store = full_store(tmp_path)
    with FakeCalDav(host="caldav.calendar.naver.com") as fake:
        src, cal, _ = build(store, fake=fake, google=FakeGoogle([("google-calendar", "me@gmail.test")], [{"title": "Google thing", "at": ts(9, day=10)}]),
                            outlook=FakeOutlook([{"title": "Board", "at": ts(16, day=10)},
                                                 {"title": "Company holiday", "at": ts(0, day=10), "all_day": True, "day": "2026-11-10"}]),
                            phone=[{"title": "Dentist", "at": ts(10, day=10)}])
        cal.fetcher = _icloud_fails(fake)                     # iCloud cannot be reached; Naver works
        tool = src.calendar_tool(lambda: "Asia/Seoul")
        assert tool.name == "calendar_events" and tool.untrusted and tool.source == "calendar" and not tool.requires_approval
        out = await tool.handler({"start": "2026-11-10", "end": "2026-11-10"})
        assert "Calendars read: Phone calendar, Google Calendar (me@gmail.test), Outlook Calendar (work@corp.test), Naver Calendar (kang@naver.test)." in out
        assert "Could not read now: iCloud Calendar" in out
        assert "- 2026-11-10, all day · Company holiday · Outlook" in out
        assert "- 2026-11-10 09:00 · Google thing · Google" in out and "- 2026-11-10 10:00–10:30 · Dentist · Phone calendar" in out
        assert "Standup · at Room 4 · Naver" in out                      # the repeating Naver event (EXDATE on the 9th, here on the 10th)
        assert out.index("Company holiday") < out.index("Google thing") < out.index("Dentist") < out.index("Board")
        # the same setting as the briefing: a calendar left out is not read, and is named as left out
        src.set_group("calendar", accounts={"google:google-calendar": False})
        out = await tool.handler({"start": "2026-11-10", "end": "2026-11-10"})
        assert "Google thing" not in out and "Left out by the user's choice: Google Calendar." in out
        assert "Dentist" in (await tool.handler({"start": "2026-11-10", "end": "2026-11-11", "query": "dent"})) and \
            "Board" not in (await tool.handler({"start": "2026-11-10", "end": "2026-11-11", "query": "dent"}))
        assert (await tool.handler({"start": "2026-11-12", "end": "2026-11-10"})).startswith("error:")
        assert (await tool.handler({"start": "2026-11-10", "end": "2027-03-10"})).startswith("error:")
        assert (await tool.handler({"start": "tomorrow-ish"})).startswith("error:")
        src.set_group("calendar", accounts={a.id: False for a in src.accounts("calendar")})
        assert "left every calendar out" in await tool.handler({})


async def test_one_search_finds_every_spelling_over_eighteen_months(tmp_path):
    """A trip saved as "상하이" is found by one call naming every spelling (상해, 상하이, Shanghai, PVG), matched as OR, over a long span;
    Google and Outlook are asked for more than one page then, and a plain list still stops at two months."""
    store = full_store(tmp_path)
    asked: list[dict[str, Any]] = []

    class PagedGoogle(FakeGoogle):
        async def events(self, account, start, end, **kw):
            asked.append(kw)
            return await super().events(account, start, end, **kw)

    class PagedOutlook(FakeOutlook):
        async def between(self, start, end, **kw):
            asked.append(kw)
            return await super().between(start, end, **kw)

    src, _, _ = build(store, google=PagedGoogle([("google-calendar", "me@gmail.test")], [{"title": "상하이 여행", "at": ts(9, day=20) + 200 * 86400}]),
                      outlook=PagedOutlook([{"title": "Flight KE893 ICN-PVG", "at": ts(8, day=10) + 300 * 86400},
                                            {"title": "Tokyo trip", "at": ts(8, day=11) + 300 * 86400}]),
                      phone=[{"title": "Dinner", "at": ts(19, day=12), "where": "Shanghai Tower"}])
    tool = src.calendar_tool(lambda: "Asia/Seoul")
    out = await tool.handler({"start": "2026-11-09", "end": "2028-03-31", "query": ["상해", "상하이", "Shanghai", "PVG"]})
    assert "상하이 여행" in out and "Flight KE893 ICN-PVG" in out and "Dinner · at Shanghai Tower" in out and "Tokyo trip" not in out
    assert any(kw.get("pages", 1) > 1 for kw in asked)
    assert "shanghai" in (await tool.handler({"start": "2026-11-09", "end": "2027-12-31", "query": "SHANGHAI"})).lower()   # a string still works
    none = await tool.handler({"start": "2026-11-09", "end": "2027-12-31", "query": ["Osaka", "KIX"]})
    assert 'No events matching "osaka" or "kix"' in none
    assert (await tool.handler({"start": "2026-11-09", "end": "2028-12-31", "query": ["상해"]})).startswith("error:")    # past 18 months
    assert "give query" in await tool.handler({"start": "2026-11-09", "end": "2027-03-10"})                           # a list: two months
    props = tool.parameters["properties"]["query"]
    assert props["type"] == "array" and "spelling" in tool.description and "airport" in tool.description


async def test_google_and_outlook_follow_next_pages_only_when_asked(tmp_path):
    from types import SimpleNamespace

    from assistant.microsoft_calendar import MicrosoftCalendar
    store = full_store(tmp_path)
    urls: list[str] = []

    async def fetcher(method, url, **kw):
        urls.append(url)
        if "page2" in url:
            return SimpleNamespace(status=200, body=json.dumps({"value": [
                {"id": "b", "subject": "Second page", "start": {"dateTime": "2027-05-01T01:00:00"}, "end": {"dateTime": "2027-05-01T02:00:00"},
                 "isAllDay": False}]}).encode())
        return SimpleNamespace(status=200, body=json.dumps({"@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView?page2", "value": [
            {"id": "a", "subject": "First page", "start": {"dateTime": "2026-12-01T01:00:00"}, "end": {"dateTime": "2026-12-01T02:00:00"},
             "isAllDay": False}]}).encode())
    ms = MicrosoftCalendar(store, fetcher=fetcher)
    import assistant.microsoft_calendar as mc

    async def no_renew(store, cred, fetcher):
        return {"secret": "tok"}
    mc.open_token, keep = no_renew, mc.open_token
    try:
        start, end = datetime(2026, 11, 9, tzinfo=SEOUL), datetime(2028, 1, 1, tzinfo=SEOUL)
        assert [e["title"] for e in await ms.between(start, end)] == ["First page"] and len(urls) == 1
        urls.clear()
        assert [e["title"] for e in await ms.between(start, end, pages=8)] == ["First page", "Second page"] and len(urls) == 2
        assert "%24top=250" in urls[0]
    finally:
        mc.open_token = keep


async def test_every_chosen_mailbox_is_read_and_a_slow_one_is_skipped_and_shown(tmp_path, monkeypatch):
    import asyncio
    from assistant import briefing_sources, proactive
    store = Store(tmp_path / "s.db")
    names = []
    for i in range(10):                                      # more mailboxes than any old cap: every one counts
        n = f"mail-p{i}"
        store.save_credential(n, f"x · u{i}@mail.test", ["imap.x.test"], {"type": "mailbox"}, "", kind="email")
        store.set_credential_secret(n, json.dumps({"u": f"u{i}@mail.test", "p": "pw"}))
        names.append(n)

    class ManyMail(FakeMail):
        def accounts(self):
            return [{"name": n, "provider": "naver", "label": f"Naver Mail · u{i}@mail.test", "address": f"u{i}@mail.test"} for i, n in enumerate(names)]

        async def arrivals(self, account, cursor, kind="inbox"):
            if kind != "inbox":
                return None
            self.asked.append(account)
            if account == "mail-p3":
                await asyncio.sleep(5)                           # far past the per-account limit below
            return {"rows": [{"uid": "1", "subject": f"Hello from {account}", "from": "x", "date": ""}], "cursor": {"t": 1}}

    monkeypatch.setattr(briefing_sources, "ARRIVALS_TIMEOUT", 0.2)
    mail = ManyMail(store)
    src, _, _ = build(store, mail=mail)
    p = proactive.Proactive(store, Home(tmp_path / "home"), stream_fn=None, model=lambda: None, clock=lambda: NOW, mail=src.mail_view(), calendar=None)
    got, _ = await p.gather(NOW - 3600)
    titles = {c.title for c in got if c.source == "mail"}
    assert len(titles) == 9 and "Hello from mail-p3" not in titles
    assert sorted(mail.asked) == sorted(names) and p._cursors.keys() == set(names) - {"mail-p3"}   # the slow one keeps its old cursor
    row = next(a for a in src.public()["mail"]["accounts"] if a["id"] == "mail:mail-p3")
    assert row["problem"] and "took too long" in row["problem"]


async def test_a_calendar_that_takes_too_long_is_skipped_and_shown(tmp_path, monkeypatch):
    import asyncio
    from assistant import calendars

    class SlowGoogle(FakeGoogle):
        async def events(self, *a, **kw):
            await asyncio.sleep(5)
            return []
    monkeypatch.setattr(calendars, "ACCOUNT_TIMEOUT", 0.2)
    store = Store(tmp_path / "s.db")
    src, _, _ = build(store, google=SlowGoogle([("google-calendar", "me@gmail.test")]), phone=[{"title": "Dentist", "at": ts(10)}])
    assert [e["title"] for e in await src.upcoming_events(24)] == ["Dentist"]
    row = next(a for a in src.public()["calendar"]["accounts"] if a["id"] == "google:google-calendar")
    assert "took too long" in row["problem"]
