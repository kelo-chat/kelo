"""Tests for GET /api/actions readable lines, sources, and storage invariants."""

from __future__ import annotations

import httpx
import pytest

from assistant import i18n
from assistant.api.actions import connector_display_name, line_of, source_name_of
from assistant.done_once import ActionLedger, Record
from assistant.schedules import Schedules
from assistant.store import Store
from test_server import AUTH, Running


class DummyRecord:
    def __init__(self, kind: str, targets: list[str], connector: str = "", tool: str = "custom_tool"):
        self.kind = kind
        self.targets = tuple(targets)
        self.connector = connector
        self.tool = tool


class FakeActivity:
    def __init__(self, tasks: dict[int, dict]):
        self._tasks = tasks

    def task(self, task_id: int):
        return self._tasks.get(task_id)


class FakeSchedules:
    def __init__(self, items: dict[int, dict]):
        self._items = items

    def get(self, sid: int):
        return self._items.get(sid)


# -------------------------------------------------------------------------------------------------- line families & fallbacks
def test_send_mail_line_in_en_and_ko():
    rec = DummyRecord("send", ["alice@example.com"], tool="email_send")
    assert line_of(rec, lang="en") == "Sent a mail to alice@example.com"
    assert line_of(rec, lang="ko") == "alice@example.com에게 메일을 보냈어요"


def test_send_site_line_in_en_and_ko():
    rec = DummyRecord("send", ["site:coupang.com"], tool="browser_click")
    assert line_of(rec, lang="en") == "Sent a form on coupang.com"
    assert line_of(rec, lang="ko") == "coupang.com에서 양식을 보냈어요"


def test_change_calendar_line_in_en_and_ko():
    rec_target = DummyRecord("change", ["calendar"], tool="custom_tool")
    assert line_of(rec_target, lang="en") == "Changed the calendar"
    assert line_of(rec_target, lang="ko") == "캘린더를 바꿨어요"

    rec_tool = DummyRecord("change", ["evt_123"], tool="gcal_update_event")
    assert line_of(rec_tool, lang="en") == "Changed the calendar"
    assert line_of(rec_tool, lang="ko") == "캘린더를 바꿨어요"


def test_delete_connector_line_in_en_and_ko():
    rec = DummyRecord("delete", ["row_1"], connector="slack", tool="connector_send")
    assert line_of(rec, lang="en") == "Deleted in Slack"
    assert line_of(rec, lang="ko") == "Slack에서 지웠어요"

    rec_target = DummyRecord("delete", ["connector:notion"], tool="connector_send")
    assert line_of(rec_target, lang="en") == "Deleted in Notion"
    assert line_of(rec_target, lang="ko") == "Notion에서 지웠어요"


def test_fallbacks_per_kind_in_en_and_ko():
    send_fallback = DummyRecord("send", ["unknown_target"])
    assert line_of(send_fallback, lang="en") == "Sent something"
    assert line_of(send_fallback, lang="ko") == "무언가를 보냈어요"

    change_fallback = DummyRecord("change", ["device_settings"])
    assert line_of(change_fallback, lang="en") == "Changed something"
    assert line_of(change_fallback, lang="ko") == "무언가를 바꿨어요"

    delete_fallback = DummyRecord("delete", ["doc_456"])
    assert line_of(delete_fallback, lang="en") == "Deleted something"
    assert line_of(delete_fallback, lang="ko") == "무언가를 지웠어요"


# -------------------------------------------------------------------------------------------------- source_name
def test_source_name_for_chat_in_en_and_ko():
    assert source_name_of("chat", None, lang="en") == "Chat"
    assert source_name_of("chat", None, lang="ko") == "채팅"


def test_source_name_for_schedules():
    scheds = FakeSchedules({10: {"id": 10, "name": "Morning Briefing"}})
    assert source_name_of("schedule:10", None, schedules=scheds, lang="en") == "Morning Briefing"
    assert source_name_of("schedule:10", None, schedules=scheds, lang="ko") == "Morning Briefing"

    # deleted schedule (not found in schedules)
    assert source_name_of("schedule:99", None, schedules=scheds, lang="en") == "a schedule that is gone"
    assert source_name_of("schedule:99", None, schedules=scheds, lang="ko") == "지금은 없는 예약"


def test_source_name_for_job_with_known_task_id():
    activity = FakeActivity({42: {"id": 42, "title": "Check unread emails"}})
    assert source_name_of("job", 42, activity=activity, lang="en") == "Check unread emails"
    assert source_name_of("job", 42, activity=activity, lang="ko") == "Check unread emails"


# -------------------------------------------------------------------------------------------------- API & storage invariant
async def test_api_actions_returns_computed_fields_and_nothing_new_stored(tmp_path):
    from test_prompt import talk_app

    app = talk_app(tmp_path, [])
    ledger = app.state.done_once.ledger
    store = ledger.store

    # Record actions with different kinds, targets, and sources
    rid1 = ledger.record(
        run_id="r1",
        task_id=1,
        turn_id="t1",
        source="chat",
        tool="email_send",
        kind="send",
        connector="gmail",
        targets=["bob@example.com"],
        fingerprint="fp1",
        state="done",
    )
    rid2 = ledger.record(
        run_id="r2",
        task_id=None,
        turn_id="t2",
        source="schedule:9999",
        tool="browser_click",
        kind="send",
        connector="",
        targets=["site:shop.com"],
        fingerprint="fp2",
        state="unknown",
    )

    # Verify table schema has NOT gained any new column
    columns = [col["name"] for col in store._all("PRAGMA table_info(actions)")]
    assert "line" not in columns
    assert "source_name" not in columns

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as client:
        # Request in Korean
        r_ko = await client.get("/api/actions?days=7", headers={"Accept-Language": "ko-KR,ko;q=0.9"})
        assert r_ko.status_code == 200
        items_ko = {it["id"]: it for it in r_ko.json()["items"]}

        assert items_ko[rid1]["line"] == "bob@example.com에게 메일을 보냈어요"
        assert items_ko[rid1]["source_name"] == "채팅"

        assert items_ko[rid2]["line"] == "shop.com에서 양식을 보냈어요"
        assert items_ko[rid2]["source_name"] == "지금은 없는 예약"

        # Request in English
        r_en = await client.get("/api/actions?days=7", headers={"Accept-Language": "en-US,en;q=0.9"})
        assert r_en.status_code == 200
        items_en = {it["id"]: it for it in r_en.json()["items"]}

        assert items_en[rid1]["line"] == "Sent a mail to bob@example.com"
        assert items_en[rid1]["source_name"] == "Chat"

        assert items_en[rid2]["line"] == "Sent a form on shop.com"
        assert items_en[rid2]["source_name"] == "a schedule that is gone"

    # Verify database contents: row in DB does not contain computed line or source_name
    row = store._all("SELECT * FROM actions WHERE id = ?", (rid1,))[0]
    assert "line" not in row and "source_name" not in row
