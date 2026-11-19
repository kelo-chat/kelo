import asyncio
import json
import time

import pytest

from assistant.microsoft_calendar import MicrosoftCalendar, MicrosoftCalendarError, events_from
from assistant.netguard import Reply
from assistant.store import Store


def outlook_store(tmp_path):
    st = Store(tmp_path / "s.db")
    st.save_credential("microsoft-api", "Microsoft · me@example.com", ["graph.microsoft.com"], {"type": "bearer_header"}, "", kind="api")
    st.set_credential_secret("microsoft-api", "access-token")
    return st


def fake(status=200, rows=None):
    calls = []

    async def fetcher(method, url, *, allowed_hosts, headers=None, content=None, **_):
        calls.append((method, url, allowed_hosts, headers))
        return Reply(status, {}, json.dumps({"value": rows or []}).encode(), False)

    fetcher.calls = calls
    return fetcher


def test_events_come_back_like_the_other_calendars():
    rows = [{"id": "a1", "subject": "Dentist", "start": {"dateTime": "2026-11-10T01:30:00.0000000"}, "location": {"displayName": "Clinic"}},
            {"id": "a2", "subject": "Holiday", "isAllDay": True, "start": {"dateTime": "2026-11-10T00:00:00.0000000"}},
            {"id": "a3", "subject": "Cancelled", "isCancelled": True, "start": {"dateTime": "2026-11-10T02:00:00.0000000"}},
            {"id": "a4", "subject": "Broken", "start": {}}]
    got = events_from(rows)
    assert [e["title"] for e in got] == ["Dentist"] and got[0]["where"] == "Clinic" and got[0]["id"] == "outlook:a1"
    assert got[0]["start"] == time.mktime((2026, 11, 10, 1, 30, 0, 0, 0, 0)) - time.timezone or isinstance(got[0]["start"], float)


def test_a_connected_outlook_is_read_with_its_token_on_graph_only(tmp_path):
    fetcher = fake(rows=[{"id": "x", "subject": "Standup", "start": {"dateTime": "2026-11-10T00:30:00.0000000"}}])
    cal = MicrosoftCalendar(outlook_store(tmp_path), fetcher=fetcher)
    assert cal.connected()
    got = asyncio.run(cal.upcoming(12))
    assert [e["title"] for e in got] == ["Standup"]
    method, url, hosts, headers = fetcher.calls[0]
    assert method == "GET" and url.startswith("https://graph.microsoft.com/v1.0/me/calendarView?") and hosts == ["graph.microsoft.com"]
    assert headers["Authorization"] == "Bearer access-token" and "outlook.timezone" in headers["Prefer"]


def test_not_connected_means_no_events_and_no_request(tmp_path):
    fetcher = fake()
    cal = MicrosoftCalendar(Store(tmp_path / "s.db"), fetcher=fetcher)
    assert not cal.connected() and asyncio.run(cal.upcoming()) == [] and not fetcher.calls


def test_a_refusal_is_an_error_the_caller_can_skip(tmp_path):
    cal = MicrosoftCalendar(outlook_store(tmp_path), fetcher=fake(status=401))
    with pytest.raises(MicrosoftCalendarError):
        asyncio.run(cal.upcoming())


def test_the_body_preview_is_kept_as_the_event_notes_for_search():
    rows = [{"id": "a5", "subject": "Board", "start": {"dateTime": "2026-11-10T01:30:00.0000000"}, "bodyPreview": "agenda QQ9 " * 100}]
    got = events_from(rows)
    assert got[0]["notes"].startswith("agenda QQ9") and len(got[0]["notes"]) == 500
