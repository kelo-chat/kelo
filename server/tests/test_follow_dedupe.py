"""One real-world thing is one follow item: the same flight from two calendars, the same parcel from two mails, a start-up merge of
rows already doubled, and an event removed from one source or from both. Structure only (refs, start time, case/space-folded title)."""

from __future__ import annotations

import sqlite3
from zoneinfo import ZoneInfo

import pytest

from assistant.follow.items import ItemFinder, UpcomingItem
from assistant.follow.plan import FollowPlan, Planner
from assistant.follow.runner import FollowRunner
from assistant.follow.store import FollowStore

NOW = 1763500000.0
UTC = ZoneInfo("UTC")


def flight_event(eid: str, title: str, start: float) -> dict:
    return {"id": eid, "title": title, "start": start, "end": start + 7200, "where": "ICN"}


class Cal:
    def __init__(self, events: list[dict]) -> None:
        self.events = events

    def __call__(self) -> list[dict]:
        return list(self.events)


async def no_plan(_item, _prefs=""):
    return FollowPlan(checks=[], tell={}, prep=[])


def runner_for(cal, mail=None, bookings=None):
    store = FollowStore(sqlite3.connect(":memory:", check_same_thread=False), clock=lambda: NOW)
    planner = Planner(sources=[], clock=lambda: NOW)
    planner.plan = no_plan   # type: ignore[method-assign]
    finder = ItemFinder(calendar=cal, mail=mail, bookings=bookings, tz=UTC)
    runner = FollowRunner(store=store, finder=finder, planner=planner, sources=[], jitter=False, clock=lambda: NOW)
    return runner, store, finder


@pytest.mark.asyncio
async def test_same_flight_from_phone_and_google_is_one_item_across_passes():
    start = NOW + 3 * 86400
    phone = flight_event("phone:1795931700:Flight to Shanghai (9C 7050)", "Flight to Shanghai (9C 7050)", start)
    gcal = flight_event("gcal:0teno077", "Shanghai trip 9C7050", start + 1800)   # other title, other minute: only refs agree
    cal = Cal([phone])
    runner, store, finder = runner_for(cal)
    await runner.tick(NOW)
    assert [i["key"] for i in store.list_items()] == ["cal:" + phone["id"]]

    cal.events = [gcal, phone]   # Google starts syncing the same trip; a later pass, a fresh finder (restart) too
    runner.finder = ItemFinder(calendar=cal, tz=UTC)
    runner._found_at = None
    await runner.tick(NOW + 1000)
    items = store.list_items()
    assert len(items) == 1
    assert items[0]["key"] == "cal:" + phone["id"] and "cal:gcal:0teno077" in items[0]["aliases"]

    cal.events = [gcal]   # only Google answers now: still the same item
    runner.finder = ItemFinder(calendar=cal, tz=UTC)
    runner._found_at = None
    await runner.tick(NOW + 2000)
    assert [i["key"] for i in store.list_items()] == ["cal:" + phone["id"]]


@pytest.mark.asyncio
async def test_same_parcel_from_two_mails_is_one_item():
    class Bookings:
        async def read(self, mails):
            return {mid: {"kind": "parcel", "tracking": "1234567890123", "carrier": "X", "url": "https://t.example/1"} for mid, _s, _b in mails}

    mails = [
        {"id": "m1", "subject": "Your order shipped", "body": "b", "starts_at": NOW - 3600},
        {"id": "m2", "subject": "Out for delivery soon", "body": "b", "starts_at": NOW - 600},
    ]
    runner, store, _ = runner_for(None, mail=lambda: mails, bookings=Bookings())
    await runner.tick(NOW)
    items = store.list_items()
    assert len(items) == 1 and items[0]["kind"] == "parcel"


@pytest.mark.asyncio
async def test_two_different_flights_same_day_stay_two():
    start = NOW + 3 * 86400
    cal = Cal([flight_event("phone:1", "Flight KE123", start), flight_event("gcal:2", "Flight KE456", start)])
    runner, store, _ = runner_for(cal)
    await runner.tick(NOW)
    assert len(store.list_items()) == 2


def item(key: str, start: float = NOW + 86400) -> UpcomingItem:
    return UpcomingItem(key=key, kind="flight", title=key, starts_at=start, ends_at=start + 7200, place="ICN",
                        refs={"flight": "9C7050", "date": "2026-11-29"}, source="calendar")


def add_check(store, key, kind="flight", readings=0):
    cid = store.save_check(key, kind, {"flight": "9C7050"}, ["gate"], "-3h", "+30m", NOW, NOW + 9999, 10, NOW)
    for n in range(readings):
        store.save_reading(cid, NOW + n, True, {"gate": "A1"})
    return cid


def test_startup_merge_moves_checks_to_the_kept_item_and_drops_the_duplicate(tmp_path):
    db = sqlite3.connect(str(tmp_path / "f.db"), check_same_thread=False)
    # rows as the live database had them: written by an older build, two keys for one flight
    old = FollowStore(db, clock=lambda: NOW)
    old.save_item(item("cal:phone:1"), state="watching")
    old.save_item(item("cal:gcal:2"), state="watching")
    keep_check = add_check(old, "cal:phone:1", readings=3)
    twin = add_check(old, "cal:gcal:2", readings=1)             # same check as the kept one: folded into it
    extra = add_check(old, "cal:gcal:2", kind="weather")         # a check only the duplicate had: moves over
    store = FollowStore(db, clock=lambda: NOW)                   # start-up
    assert [i["key"] for i in store.list_items()] == ["cal:phone:1"]
    assert store.get_item("cal:phone:1")["aliases"] == ["cal:gcal:2"]
    checks = {c["id"] for c in store.list_checks(item_key="cal:phone:1")}
    assert checks == {keep_check, extra}
    assert twin not in checks
    n = db.execute("SELECT COUNT(*) FROM follow_readings WHERE check_id = ?", (keep_check,)).fetchone()[0]
    assert n == 4
    assert db.execute("SELECT COUNT(*) FROM follow_checks WHERE item_key = 'cal:gcal:2'").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_removed_from_one_source_keeps_the_item_removed_from_both_ends_it():
    start = NOW + 3 * 86400
    phone = flight_event("phone:1", "Flight KE123", start)
    gcal = flight_event("gcal:2", "Flight KE123", start)
    cal = Cal([phone, gcal])
    runner, store, finder = runner_for(cal)
    await runner.tick(NOW)
    assert len(store.list_items()) == 1

    for who, left in (("phone", [gcal]), ("gcal", [phone])):   # either one alone keeps it
        cal.events = left
        runner.finder = ItemFinder(calendar=cal, tz=UTC)
        runner._found_at = None
        await runner.tick(NOW + 100)
        assert len(store.list_items()) == 1, who

    cal.events = []
    runner.finder = ItemFinder(calendar=cal, tz=UTC)
    runner._found_at = None
    await runner.tick(NOW + 200)
    assert store.list_items() == []


@pytest.mark.asyncio
async def test_a_failed_calendar_read_removes_nothing():
    start = NOW + 3 * 86400
    cal = Cal([flight_event("phone:1", "Flight KE123", start)])
    runner, store, _ = runner_for(cal)
    await runner.tick(NOW)

    def boom():
        raise RuntimeError("calendar down")

    runner.finder = ItemFinder(calendar=boom, tz=UTC)
    runner._found_at = None
    await runner.tick(NOW + 100)
    assert len(store.list_items()) == 1
