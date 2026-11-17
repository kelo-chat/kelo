"""Tests for follow API routes and agent tools."""

from __future__ import annotations

import sqlite3
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
    assert "No items" in res

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
    assert "watching" in res2

    # Stop tool
    stop_res = await by_name["follow_stop"].handler({"key": "cal:123"})
    assert "Stopped watching" in stop_res
    it = store.get_item("cal:123")
    assert it["state"] == "stopped"
