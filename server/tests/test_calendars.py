"""calendars.py: CalDAV discovery and reading against a fake CalDAV server (recurring events, EXDATE, RECURRENCE-ID, time zones,
all-day, X-WR-TIMEZONE), which mail provider has a CalDAV calendar, the password coming from the mail item in the vault (and every use
logged), the hosts a request may go to, failures told apart, and a CalDAV account of your own."""

from __future__ import annotations

import functools
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant.calendars import CALDAV, CalAccount, CalDavClient, CalDavError, Calendars, expand
from assistant.mail import Mail
from assistant.netguard import fetch
from assistant.store import Store

from caldav_fake import NEW_YORK_REVIEW, PARIS, SEOUL_STANDUP, SIMPLE, FakeCalDav

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 9, 6, 0, tzinfo=SEOUL).timestamp()


def kst(h: int, m: int = 0, day: int = 9) -> float:
    return datetime(2026, 11, day, h, m, tzinfo=SEOUL).timestamp()


class NoPhone:
    def rows(self) -> list:
        return []

    def upcoming(self, hours: float = 24) -> list:
        return []


class NoGoogle:
    def accounts(self) -> list:
        return []


class NoOutlook:
    def connected(self) -> bool:
        return False


def mail_item(store: Store, provider: str, address: str = "me@example.com", password: str = "app-pass-123", oauth: bool = False) -> str:
    name = f"mail-{provider}"
    store.save_credential(name, f"{provider} · {address}", [f"imap.{provider}.test"], {"type": "mailbox", **({"via": "oauth"} if oauth else {})}, "", kind="email")
    store.set_credential_secret(name, json.dumps({"u": address, "oauth": {}} if oauth else {"u": address, "p": password}))
    return name


def hub(store: Store, fake: FakeCalDav) -> Calendars:
    return Calendars(store, Mail(store), NoPhone(), NoGoogle(), NoOutlook(), fetcher=fake.fetcher, timezone_name=lambda: "Asia/Seoul", clock=lambda: NOW)


# ------------------------------------------------------------------------------------------------------ iCalendar
def window(hours: int = 30) -> tuple[datetime, datetime]:
    a = datetime.fromtimestamp(NOW, timezone.utc)
    return a, a + timedelta(hours=hours)


def test_a_repeating_event_skips_its_exdate_and_keeps_its_time_zone():
    got = expand([SEOUL_STANDUP], *window(), SEOUL)
    assert [(e["title"], e["start"], e["end"], e["where"]) for e in got] == [("Standup", kst(9, 0, 10), kst(9, 30, 10), "Room 4")]   # Nov 9 is EXDATEd


def test_a_moved_occurrence_replaces_the_original_in_another_time_zone():
    got = expand([NEW_YORK_REVIEW], *window(), SEOUL)
    # the series meets Mondays 20:00 in New York (10:00 the next morning in Seoul); this Monday's turn was moved by an hour
    assert [(e["title"], e["start"]) for e in got] == [("Review (moved)", kst(11, 0, 10))]


def test_times_without_a_zone_are_local_utc_is_exact_and_x_wr_timezone_counts():
    got = {e["title"]: e for e in expand([SIMPLE, PARIS], *window(), SEOUL)}
    assert got["Lunch with Mina"]["start"] == kst(12) and got["Lunch with Mina"]["where"] == "Gangnam station"
    assert got["Call from the bank"]["start"] == kst(10) and got["Call from the bank"]["end"] == kst(10, 15)
    assert got["Paris office"]["start"] == kst(18)   # 10:00 in Paris in November = 18:00 in Seoul
    assert "Cancelled thing" not in got and "Next week" not in got   # cancelled, and outside the window


def test_an_all_day_event_is_marked_and_not_taken_for_a_time():
    got = {e["title"]: e for e in expand([SIMPLE], *window(), SEOUL)}
    assert got["Holiday"]["all_day"] is True and got["Holiday"]["start"] == kst(0) and got["Holiday"]["end"] == kst(0, 0, 10)
    assert got["Lunch with Mina"]["all_day"] is False


def test_a_broken_resource_does_not_hide_the_good_ones():
    got = expand(["BEGIN:VCALENDAR\nthis is not a calendar", SIMPLE], *window(), SEOUL)
    assert {"Lunch with Mina", "Call from the bank"} <= {e["title"] for e in got}


# ------------------------------------------------------------------------------------------------------ the protocol
async def test_discovery_then_a_time_range_report_per_calendar_with_the_mail_password(tmp_path):
    store = Store(tmp_path / "s.db")
    mail_item(store, "icloud")
    with FakeCalDav(tasks=("tasks",), calendars={"work": [SEOUL_STANDUP, NEW_YORK_REVIEW], "personal": [SIMPLE, PARIS], "tasks": [SIMPLE]},
                    host="caldav.icloud.com", home_host="p12-caldav.icloud.com") as fake:
        cal = hub(store, fake)
        assert [a.id for a in cal.accounts()] == ["caldav:mail-icloud"]
        evs = await cal.upcoming_of(cal.accounts()[0], 30)
        titles = [e["title"] for e in evs]
        assert sorted(titles) == sorted(["Lunch with Mina", "Call from the bank", "Paris office", "Standup", "Review (moved)"])
        assert "Holiday" not in titles                                 # all-day events have no time to say
        assert all(e["source_id"] == "caldav:mail-icloud" and e["id"].startswith("caldav:") for e in evs)
        # the requests: principal, home set, calendars, then a REPORT for each calendar that holds events (not the task list)
        seen = [(r["method"], r["path"], r["depth"]) for r in fake.requests]
        assert seen[:3] == [("PROPFIND", "/", "0"), ("PROPFIND", "/principals/u1/", "0"), ("PROPFIND", "/home/", "1")]
        assert sorted(p for m, p, _ in seen if m == "REPORT") == ["/home/personal/", "/home/work/"]
        report = next(r for r in fake.requests if r["method"] == "REPORT")
        assert 'time-range start="20261108T210000Z" end="20261110T030000Z"' in report["body"]
        assert all(r["auth"].startswith("Basic ") for r in fake.requests)
        # the calendar home named a partition host: the next requests were pinned to it, the first one to the provider's
        assert fake.hosts_used[0] == "caldav.icloud.com" and set(fake.hosts_used[2:]) == {"p12-caldav.icloud.com"}
    log = [r for r in store.vault_log() if r["item"] == "mail-icloud"]
    assert log and all(r["action"] == "used" for r in log) and "calendar" in log[0]["detail"]


async def test_the_second_read_goes_straight_to_the_calendars(tmp_path):
    store = Store(tmp_path / "s.db")
    mail_item(store, "yahoo")
    with FakeCalDav(host="caldav.calendar.yahoo.com") as fake:
        cal = hub(store, fake)
        await cal.upcoming_of(cal.accounts()[0], 24)
        first = len(fake.requests)
        await cal.upcoming_of(cal.accounts()[0], 24)
        assert len(fake.requests) - first == 2 and all(r["method"] == "REPORT" for r in fake.requests[first:])   # discovery is remembered


async def test_a_calendar_home_on_another_host_is_refused_and_never_called(tmp_path):
    store = Store(tmp_path / "s.db")
    mail_item(store, "icloud")
    with FakeCalDav(host="caldav.icloud.com", home_host="evil.example.com") as fake:
        cal = hub(store, fake)
        with pytest.raises(CalDavError) as e:
            await cal.upcoming_of(cal.accounts()[0], 24)
        assert e.value.code == "host" and "not allowed" in str(e.value)
        assert "evil.example.com" not in getattr(fake, "hosts_used", [])


async def test_the_failures_are_told_apart(tmp_path):
    store = Store(tmp_path / "s.db")
    mail_item(store, "naver", password="wrong")
    with FakeCalDav(host="caldav.calendar.naver.com") as fake:
        cal = hub(store, fake)
        with pytest.raises(CalDavError) as e:
            await cal.upcoming_of(cal.accounts()[0], 24)
        assert e.value.code == "auth"                                  # a wrong or expired password: 401
    store2 = Store(tmp_path / "s2.db")
    mail_item(store2, "naver")
    with FakeCalDav(host="caldav.calendar.naver.com", principal=False, calendars={}) as fake2:
        with pytest.raises(CalDavError) as e:
            await hub(store2, fake2).upcoming_of(hub(store2, fake2).accounts()[0], 24)
        assert e.value.code == "off"                                   # CalDAV not offered or turned off: nothing to read
    with FakeCalDav(host="caldav.calendar.naver.com", status=503) as fake3:
        with pytest.raises(CalDavError) as e:
            await hub(store2, fake3).upcoming_of(hub(store2, fake3).accounts()[0], 24)
        assert e.value.code == "unreachable"


# ------------------------------------------------------------------------------------------------------ which accounts
def test_each_mail_provider_gets_its_own_caldav_calendar_or_none(tmp_path):
    store = Store(tmp_path / "s.db")
    for p in ("icloud", "yahoo", "naver", "daum", "gmail"):
        mail_item(store, p, f"me@{p}.test")
    mail_item(store, "gmail", "g@gmail.test", oauth=True)
    got = {a.id: (a.provider, a.address) for a in hub(store, FakeCalDav()).accounts()}
    # iCloud, Yahoo and Naver offer CalDAV; Daum has no CalDAV service; Gmail's calendar is the Google Calendar connector's
    assert got == {"caldav:mail-icloud": ("icloud", "me@icloud.test"), "caldav:mail-yahoo": ("yahoo", "me@yahoo.test"),
                   "caldav:mail-naver": ("naver", "me@naver.test")}
    assert set(CALDAV) == {"icloud", "yahoo", "naver"}


async def test_every_provider_is_pinned_to_its_own_hosts(tmp_path):
    store = Store(tmp_path / "s.db")
    for p in ("icloud", "yahoo", "naver"):
        mail_item(store, p)
    with FakeCalDav(host="caldav.example.test") as fake:
        cal = hub(store, fake)
        hosts = {}
        for a in cal.accounts():
            fake.hosts_used = []
            with pytest.raises(CalDavError):     # the fake's home names caldav.example.test, which none of them may use
                await cal.upcoming_of(a, 24)
            hosts[a.provider] = fake.hosts_used[0]
        assert hosts == {"icloud": "caldav.icloud.com", "yahoo": "caldav.calendar.yahoo.com", "naver": "caldav.calendar.naver.com"}


async def test_one_failing_account_is_reported_and_the_others_still_count(tmp_path):
    store = Store(tmp_path / "s.db")
    mail_item(store, "icloud")
    mail_item(store, "naver", password="wrong")
    with FakeCalDav(host="caldav.example.test") as fake:
        cal = hub(store, fake)
        results: list[tuple[str, str]] = []

        async def fetcher(method, url, **kw):
            if "naver" in kw["allowed_hosts"][0]:
                kw["headers"] = {**kw["headers"], "Authorization": "Basic wrong"}
            return await fake.fetcher(method, url, **kw)

        cal.fetcher = fetcher
        # iCloud's home would name the fake's own host, so point the fake at iCloud's: the account is reachable
        fake.home_host = "caldav.icloud.com"
        evs = await cal.upcoming(24, report=lambda a, code, detail: results.append((a.id, code)))
        assert ("caldav:mail-icloud", "ok") in results and ("caldav:mail-naver", "auth") in results
        assert {e["source_id"] for e in evs} == {"caldav:mail-icloud"} and evs


# ------------------------------------------------------------------------------------------------------ through netguard
async def test_the_requests_pass_netguard_with_their_methods_and_bodies(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, text="no")

    fetcher = functools.partial(fetch, transport=httpx.MockTransport(handler), resolver=lambda *a: [(0, 0, 0, "", ("93.184.216.34", 443))])
    client = CalDavClient("https://caldav.icloud.com/", "me@icloud.com", "pw", host_ok=lambda h: h == "caldav.icloud.com", fetcher=fetcher)
    with pytest.raises(CalDavError) as e:
        await client.calendars()
    assert e.value.code == "auth"
    r = seen[0]
    assert r.method == "PROPFIND" and r.headers["host"] == "caldav.icloud.com" and r.headers["depth"] == "0" and b"current-user-principal" in r.content
    # an address outside the pin never reaches the network
    bad = CalDavClient("https://other.example.com/", "u", "p", host_ok=lambda h: h == "caldav.icloud.com", fetcher=fetcher)
    with pytest.raises(CalDavError):
        await bad.calendars()
    assert len(seen) == 1
    # a redirect is not followed
    redirect = functools.partial(fetch, transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "https://evil.test/"})),
                                 resolver=lambda *a: [(0, 0, 0, "", ("93.184.216.34", 443))])
    with pytest.raises(CalDavError):
        await CalDavClient("https://caldav.icloud.com/", "u", "p", host_ok=lambda h: h == "caldav.icloud.com", fetcher=redirect).calendars()


# ------------------------------------------------------------------------------------------------------ an account of your own
async def test_an_own_caldav_account_is_checked_then_saved_in_the_vault_and_read(tmp_path):
    store = Store(tmp_path / "s.db")
    with FakeCalDav(host="dav.fastmail.com", prefix="/dav/") as fake:
        cal = hub(store, fake)
        with pytest.raises(CalDavError) as e:
            await cal.connect("https://dav.fastmail.com/dav/", "me@fastmail.test", "wrong-password")
        assert "refused" in str(e.value) and not [c for c in store.credentials() if c["name"].startswith("caldav-")]   # nothing saved
        got = await cal.connect("https://dav.fastmail.com/dav/", "me@example.com", "app-pass-123")
        item = store.credential(got["name"])
        assert item["kind"] == "email" and item["hosts"] == ["dav.fastmail.com"] and item["placement"]["type"] == "caldav"
        assert json.loads(item["secret"]) == {"u": "me@example.com", "p": "app-pass-123"} and "app-pass" not in item["label"]
        accts = cal.accounts()
        assert [(a.provider, a.address) for a in accts] == [("caldav", "me@example.com")]
        evs = await cal.upcoming_of(accts[0], 30)
        assert any(e["title"] == "Standup" for e in evs)
        assert fake.hosts_used and set(fake.hosts_used) == {"dav.fastmail.com"}
        assert any(r["action"] == "used" for r in store.vault_log() if r["item"] == got["name"])


async def test_an_own_caldav_address_must_be_a_public_https_host(tmp_path):
    store = Store(tmp_path / "s.db")
    cal = hub(store, FakeCalDav())
    for bad in ("http://dav.example.com/", "https://127.0.0.1/dav/", "https://192.168.0.5:8443/", "https://user:pw@dav.example.com/", "https://localhost/"):
        with pytest.raises(CalDavError):
            await cal.connect(bad, "me", "pw")
    assert not store.credentials()


def test_the_account_labels_and_provider_table_are_complete():
    for p in CALDAV.values():
        assert p.url.startswith("https://") and p.howto and p.name
    assert CalAccount("phone", "phone", "Phone calendar").id == "phone"
