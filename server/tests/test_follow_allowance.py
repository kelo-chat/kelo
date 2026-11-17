"""Tests for the FlightAware monthly allowance: the setting, the count, the stop at 90 %, the fallback and the paused check."""

from __future__ import annotations

import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant.follow.api import follow_routes
from assistant.follow.checks import Reading, Source
from assistant.follow.items import ItemFinder
from assistant.follow.plan import Planner
from assistant.follow.runner import FollowRunner
from assistant.follow.sources.flight_aeroapi import FREE_PER_MONTH, AeroApiFlights, stop_at
from assistant.follow.store import FollowStore
from assistant.netguard import Reply
from assistant.settings import Settings

T0 = 1763500000.0   # 2025-11
MONTH = "2025-11"
BODY = json.dumps({"flights": [{"ident": "KE123", "gate_origin": "A11", "estimated_out": "14:50", "status": "Scheduled"}]}).encode()


class Net:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, method: str, url: str, **kw):
        self.calls += 1
        return Reply(200, {}, BODY, False)


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Activity:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, note_type: str, title: str, detail: str, push: bool = False) -> None:
        self.notes.append((note_type, title, detail))


class OtherFlights(Source):
    """The other flight source: covers every flight, answers or fails as told."""
    kind = "flight_status"

    def __init__(self, ok: bool = True) -> None:
        self.ok, self.calls = ok, 0

    def available(self) -> bool:
        return True

    def covers(self, refs):
        return bool(refs.get("flight"))

    async def read(self, refs):
        self.calls += 1
        return Reading(fields={"gate": "B2", "status": "On time"}, ok=True) if self.ok else Reading(ok=False, detail="no data")


def aero(store: FollowStore, net: Net, allowance=FREE_PER_MONTH, notify=None, clock=None) -> AeroApiFlights:
    return AeroApiFlights(key="fa-key", follow_store=store, fetcher=net, notify=notify, clock=clock or Clock(), allowance=allowance)


def test_the_setting_defaults_to_the_free_amount_and_reads_the_environment():
    base = {"ASSISTANT_TOKEN": "x" * 20}
    assert Settings.from_env(base).aeroapi_allowance == FREE_PER_MONTH == 500
    assert Settings.from_env({**base, "ASSISTANT_AEROAPI_ALLOWANCE": "120"}).aeroapi_allowance == 120
    assert Settings.from_env({**base, "ASSISTANT_AEROAPI_ALLOWANCE": "0"}).aeroapi_allowance == 0
    with pytest.raises(RuntimeError):
        Settings.from_env({**base, "ASSISTANT_AEROAPI_ALLOWANCE": "many"})


def test_stop_point_is_ninety_percent_of_the_allowance():
    assert [stop_at(n) for n in (500, 10, 30, 70, 0, 1)] == [450, 9, 27, 63, 0, 0]


@pytest.mark.asyncio
async def test_calls_are_counted_per_month_and_stop_at_ninety_percent():
    store = FollowStore(sqlite3.connect(":memory:", check_same_thread=False))
    net, notes = Net(), []
    src = aero(store, net, allowance=10, notify=lambda *a: notes.append(a))   # lookups stop at 9
    for i in range(9):
        assert src.paused({"flight": "KE123"}) == ""
        assert (await src.read({"flight": "KE123"})).ok
        assert store.get_usage("flight_aeroapi", MONTH) == i + 1
    assert net.calls == 9
    assert len(notes) == 1 and notes[0][0] == "follow_problem"   # told when the 9th call reached the stop point
    reason = src.paused({"flight": "KE123"})
    assert "9 of 10" in reason
    assert src.available()                      # the key is there: plans stay valid, the source is only paused
    refused = await src.read({"flight": "KE123"})
    assert not refused.ok and net.calls == 9    # no call goes out
    assert len(notes) == 1                      # told once, not on every refusal or every paused() check
    use = src.usage()
    assert use == {"source": "flight_aeroapi", "month": MONTH, "used": 9, "allowance": 10, "stop_at": 9, "paused": True}


@pytest.mark.asyncio
async def test_a_new_month_starts_at_zero():
    store = FollowStore(sqlite3.connect(":memory:", check_same_thread=False))
    clock = Clock()
    src = AeroApiFlights(key="k", follow_store=store, fetcher=Net(), clock=clock, allowance=2)
    assert (await src.read({"flight": "KE123"})).ok
    assert src.allowance_exceeded()   # 1 call of an allowance of 2 is past the stop point (1)
    clock.now += 31 * 86400   # December
    assert not src.allowance_exceeded() and src.usage()["used"] == 0 and src.usage()["month"] == "2025-12"
    assert (await src.read({"flight": "KE123"})).ok


@pytest.mark.asyncio
async def test_allowance_zero_never_calls_and_a_changed_setting_applies_at_once():
    store = FollowStore(sqlite3.connect(":memory:", check_same_thread=False))
    net, setting = Net(), {"n": 0}
    src = aero(store, net, allowance=lambda: setting["n"])
    assert src.paused({"flight": "KE123"}) != ""
    assert not (await src.read({"flight": "KE123"})).ok and net.calls == 0
    setting["n"] = 100
    assert src.paused({"flight": "KE123"}) == "" and (await src.read({"flight": "KE123"})).ok


def runner_with(sources, store: FollowStore, clock: Clock, activity: Activity) -> FollowRunner:
    return FollowRunner(store=store, finder=ItemFinder(tz=ZoneInfo("UTC")), planner=Planner(sources=sources),
                        sources=sources, activity=activity, jitter=False, clock=clock)


def add_check(store: FollowStore, clock: Clock) -> int:
    from assistant.follow.items import UpcomingItem
    item = UpcomingItem(key="cal:f", kind="flight", title="Flight KE123", starts_at=T0 + 7200, ends_at=T0 + 40 * 86400, place="",
                        refs={"flight": "KE123"}, source="calendar")
    store.save_item(item, plan={"checks": []}, state="watching")
    return store.save_check(item_key="cal:f", kind="flight_status", refs={"flight": "KE123"}, fields=["gate", "status"], from_offset="-3h",
                            until_offset="+30m", window_start=T0 - 100, window_end=T0 + 9000, every_minutes=10, next_at=T0, state="active")


@pytest.mark.asyncio
async def test_a_paused_source_hands_over_to_the_other_flight_source():
    clock, activity = Clock(), Activity()
    store = FollowStore(sqlite3.connect(":memory:", check_same_thread=False), clock=clock)
    net, other = Net(), OtherFlights()
    only_aero = aero(store, net, allowance=10)
    for _ in range(9):
        store.count_usage("flight_aeroapi", MONTH)
    runner = runner_with([only_aero, other], store, clock, activity)   # the paused source is listed first on purpose
    cid = add_check(store, clock)
    await runner.tick()
    assert net.calls == 0 and other.calls == 1
    assert store.last_reading(cid)["fields"]["gate"] == "B2"
    ch = store.list_checks(item_key="cal:f")[0]
    assert ch["state"] == "active" and ch["last_error"] == "" and ch["consecutive_errors"] == 0


@pytest.mark.asyncio
async def test_a_failing_first_source_falls_back_to_aeroapi_and_counts_the_call():
    clock, activity = Clock(), Activity()
    store = FollowStore(sqlite3.connect(":memory:", check_same_thread=False), clock=clock)
    net, broken = Net(), OtherFlights(ok=False)
    runner = runner_with([broken, aero(store, net)], store, clock, activity)
    cid = add_check(store, clock)
    await runner.tick()
    assert broken.calls == 1 and net.calls == 1
    assert store.last_reading(cid)["fields"]["gate"] == "A11"
    assert store.get_usage("flight_aeroapi", MONTH) == 1


@pytest.mark.asyncio
async def test_no_other_source_means_the_check_says_it_paused_and_counts_no_errors():
    clock, activity = Clock(), Activity()
    store = FollowStore(sqlite3.connect(":memory:", check_same_thread=False), clock=clock)
    net = Net()
    paused = aero(store, net, allowance=10, notify=activity.notify, clock=clock)
    for _ in range(9):
        store.count_usage("flight_aeroapi", MONTH)
    runner = runner_with([paused], store, clock, activity)
    add_check(store, clock)
    for _ in range(7):   # more ticks than MAX_ERRORS: a pause never stops the check
        await runner.tick()
        clock.now += 1200
    assert net.calls == 0
    ch = store.list_checks(item_key="cal:f")[0]
    assert ch["state"] == "active" and ch["consecutive_errors"] == 0
    assert "paused until next month" in ch["last_error"]
    assert [n[0] for n in activity.notes] == ["follow_problem"]   # told once for the month

    app = FastAPI()
    follow_routes(app, runner, auth=lambda: None)
    data = TestClient(app).get("/api/follow").json()
    check = data["items"][0]["checks"][0]
    assert check["paused"] is True and "paused until next month" in check["note"]
    assert data["usage"] == [{"source": "flight_aeroapi", "month": MONTH, "used": 9, "allowance": 10, "stop_at": 9, "paused": True}]

    # the next month the same check reads again and says nothing is paused
    clock.now = T0 + 31 * 86400
    store.update_check(ch["id"], window_end=clock.now + 9000, window_start=clock.now - 100, next_at=clock.now)
    await runner.tick()
    ch2 = store.list_checks(item_key="cal:f")[0]
    assert net.calls == 1 and ch2["last_error"] == ""
    again = TestClient(app).get("/api/follow").json()
    assert again["items"][0]["checks"][0]["paused"] is False
    assert again["usage"][0]["month"] == "2025-12" and again["usage"][0]["used"] == 1
