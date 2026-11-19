"""Tests for follow API routes and agent tools."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant.follow.api import follow_routes, follow_tools
from assistant.follow.checks import Reading, Source
from assistant.follow.items import ItemFinder, UpcomingItem
from assistant.follow.plan import Planner
from assistant.follow.runner import FollowRunner
from assistant.follow.store import FollowStore


class FakeSource(Source):
    kind = "flight_status"

    def available(self) -> bool:
        return True

    def covers(self, refs: dict[str, str]) -> bool:
        return True

    async def read(self, refs: dict[str, str]) -> Reading:
        return Reading(fields={"gate": "A11", "status": "on_time"}, ok=True)


@pytest.fixture
def test_setup():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    store = FollowStore(db)
    source = FakeSource()
    planner = Planner(sources=[source])
    finder = ItemFinder()
    runner = FollowRunner(store=store, finder=finder, planner=planner, sources=[source])

    app = FastAPI()
    follow_routes(app, runner, auth=lambda: None)
    client = TestClient(app)
    return store, runner, client


def test_get_follow_empty(test_setup):
    store, runner, client = test_setup
    resp = client.get("/api/follow")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"items": [], "usage": []}


def test_get_follow_with_items(test_setup):
    store, runner, client = test_setup
    item = UpcomingItem(
        key="cal:123",
        kind="flight",
        title="Flight KE123",
        starts_at=1763500000.0,
        ends_at=1763507200.0,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )
    store.save_item(item, state="watching", prep=[{"what": "check-in", "when": "-24h"}])
    store.save_check(
        item_key="cal:123",
        kind="flight_status",
        refs={"flight": "KE123"},
        fields=["gate", "status"],
        from_offset="-3h",
        until_offset="+30m",
        window_start=1763489200.0,
        window_end=1763501800.0,
        every_minutes=10,
        next_at=1763489200.0,
    )

    resp = client.get("/api/follow")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["key"] == "cal:123"
    assert it["title"] == "Flight KE123"
    assert it["state"] == "watching"
    assert len(it["checks"]) == 1
    assert it["checks"][0]["kind"] == "flight_status"
    assert len(it["prep"]) == 1
    assert it["prep"][0]["what"] == "check-in"


def test_stop_follow_item(test_setup):
    store, runner, client = test_setup
    item = UpcomingItem(
        key="cal:123",
        kind="flight",
        title="Flight KE123",
        starts_at=1763500000.0,
        ends_at=1763507200.0,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )
    store.save_item(item, state="watching")
    cid = store.save_check(
        item_key="cal:123",
        kind="flight_status",
        refs={"flight": "KE123"},
        fields=["gate"],
        from_offset="-3h",
        until_offset="+30m",
        window_start=1763489200.0,
        window_end=1763501800.0,
        every_minutes=10,
        next_at=1763489200.0,
    )

    # Stop existing item
    resp = client.post("/api/follow/cal:123/stop")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "key": "cal:123", "state": "stopped"}

    it = store.get_item("cal:123")
    assert it["state"] == "stopped"
    chk = store.get_check(cid)
    assert chk["state"] == "stopped"

    # Stop non-existent item -> 404
    resp404 = client.post("/api/follow/cal:none/stop")
    assert resp404.status_code == 404


def test_replan_follow_item(test_setup):
    store, runner, client = test_setup
    item = UpcomingItem(
        key="cal:123",
        kind="flight",
        title="Flight KE123",
        starts_at=1763500000.0,
        ends_at=1763507200.0,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )
    store.save_item(item, state="stopped")

    resp = client.post("/api/follow/cal:123/replan")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["state"] == "watching"

    it = store.get_item("cal:123")
    assert it["state"] == "watching"
    checks = store.list_checks(item_key="cal:123")
    assert len(checks) == 1
    assert checks[0]["state"] == "active"


@pytest.mark.asyncio
async def test_follow_agent_tools(test_setup):
    store, runner, _client = test_setup
    tools = follow_tools(runner)
    by_name = {t.name: t for t in tools}
    assert "follow_list" in by_name
    assert "follow_stop" in by_name

    # Empty list
    res = await by_name["follow_list"].handler({})
    assert "nothing" in res

    # Add item
    item = UpcomingItem(
        key="cal:123",
        kind="flight",
        title="Flight KE123",
        starts_at=1763500000.0,
        ends_at=1763507200.0,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )
    store.save_item(item, state="watching")
    store.save_check(
        item_key="cal:123",
        kind="flight_status",
        refs={"flight": "KE123"},
        fields=["gate"],
        from_offset="-3h",
        until_offset="+30m",
        window_start=1763489200.0,
        window_end=1763501800.0,
        every_minutes=10,
        next_at=1763489200.0,
    )

    res2 = await by_name["follow_list"].handler({})
    assert "Flight KE123" in res2
    assert "being watched" in res2

    # Stop tool
    stop_res = await by_name["follow_stop"].handler({"key": "cal:123"})
    assert "Stopped watching" in stop_res
    it = store.get_item("cal:123")
    assert it["state"] == "stopped"


def test_follow_list_is_a_core_tool_so_chat_can_read_what_kelo_keeps():
    from assistant.agent.toolbox import CORE_TOOLS
    assert "follow_list" in CORE_TOOLS and "follow_stop" not in CORE_TOOLS


@pytest.mark.asyncio
async def test_follow_list_reads_in_the_owners_zone_and_says_what_kelo_does(test_setup):
    from zoneinfo import ZoneInfo
    from assistant.follow.items import make_chat_item
    store, runner, _client = test_setup
    runner.tz = ZoneInfo("Asia/Seoul")
    flight = UpcomingItem(key="cal:9c", kind="flight", title="Flight to 상하이 (9C 7050)",
                          starts_at=datetime(2026, 11, 29, 14, 55, tzinfo=ZoneInfo("Asia/Seoul")).timestamp(), ends_at=None,
                          place="CJJ", refs={"flight": "9C7050", "date": "2026-11-29"}, source="calendar")
    store.save_item(flight, state="watching")
    day = make_chat_item("상해 가는 날", "2026-11-29", quote="상해 가는 날 언제야?", what="Trip day to Shanghai", tz=ZoneInfo("Asia/Seoul"))
    store.save_item(day, state="later")
    out = await {t.name: t for t in follow_tools(runner)}["follow_list"].handler({})
    assert "2026-11-29 14:55 Flight to 상하이 (9C 7050)" in out and "from calendar" in out
    assert "2026-11-29 상해 가는 날" in out                     # all day: the day only, no invented clock time
    assert "what: Trip day to Shanghai" in out and "the owner's words: 상해 가는 날 언제야?" in out
    assert "from chat" in out and "waiting until its window" in out
    assert "1795" not in out and "starts:" not in out            # no raw epoch numbers
    assert "key cal:9c" in out and "key chat:" in out


def test_the_get_route_marks_an_all_day_item(test_setup):
    from assistant.follow.items import make_chat_item
    store, runner, client = test_setup
    store.save_item(make_chat_item("여행 날", "2027-02-11"), state="later")
    item = client.get("/api/follow").json()["items"][0]
    assert item["all_day"] is True


def test_the_per_turn_line_names_the_next_five_and_is_in_the_turn(test_setup):
    from zoneinfo import ZoneInfo
    from assistant.prompt import follow_line, turn_lines
    rows = [{"title": f"Item {i}", "starts_at": datetime(2026, 11, 20 + i, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")).timestamp(),
             "source": "calendar", "all_day": i == 1} for i in range(7)]
    line = follow_line(rows, "Asia/Seoul", datetime(2026, 11, 18, tzinfo=ZoneInfo("Asia/Seoul")))
    assert "2026-11-20 09:00 Item 0 (calendar)" in line and "2026-11-21 Item 1 (calendar)" in line
    assert "Item 4" in line and "Item 5" not in line and "(+2 more)" in line and "follow_list" in line
    assert follow_line([], "Asia/Seoul") == ""
    assert any(l.startswith("Kept for the owner") for l in turn_lines(tz_name="Asia/Seoul", follow=rows))
    assert not any(l.startswith("Kept for the owner") for l in turn_lines(tz_name="Asia/Seoul"))


def _save_cal_item(store, key="cal:123", starts_at=1763500000.0):
    store.save_item(UpcomingItem(key=key, kind="flight", title="Flight KE123", starts_at=starts_at, ends_at=None,
                                 place="Incheon", refs={"flight": "KE123"}, source="calendar"), state="watching")
    store.save_check(item_key=key, kind="flight_status", refs={"flight": "KE123"}, fields=["gate"], from_offset="-3h",
                     until_offset="+30m", window_start=1763489200.0, window_end=1763501800.0, every_minutes=10, next_at=1763489200.0)


def test_delete_follow_item_removes_it_and_its_checks(test_setup):
    store, runner, client = test_setup
    _save_cal_item(store)
    resp = client.delete("/api/follow/cal:123")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert store.get_item("cal:123") is None
    assert store.list_checks(item_key="cal:123") == []
    assert client.get("/api/follow").json()["items"] == []


def test_delete_unknown_follow_item_is_404(test_setup):
    store, runner, client = test_setup
    assert client.delete("/api/follow/cal:none").status_code == 404


def test_a_removed_item_is_not_created_again_by_the_next_calendar_look(test_setup):
    store, runner, client = test_setup
    _save_cal_item(store)
    assert client.delete("/api/follow/cal:123").status_code == 200
    # the same event, same start: the calendar still shows it, so the look finds it again and the tombstone keeps it out
    assert store.is_removed("cal:123", 1763500000.0)
    assert not store.is_removed("cal:123", 1763500000.0 + 2 * 86400)   # moved to another day: may come back


def test_follow_remove_agent_tool_removes_the_item(test_setup):
    import asyncio
    store, runner, _client = test_setup
    _save_cal_item(store)
    by_name = {t.name: t for t in follow_tools(runner)}
    assert "follow_remove" in by_name
    res = asyncio.run(by_name["follow_remove"].handler({"key": "cal:123"}))
    assert "Removed" in res and store.get_item("cal:123") is None
    assert "No follow item" in asyncio.run(by_name["follow_remove"].handler({"key": "cal:123"}))
