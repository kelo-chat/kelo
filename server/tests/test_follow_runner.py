"""Tests for FollowRunner: execution loop, field comparison, quiet hours, and cutoff rules."""

from __future__ import annotations

import sqlite3
import pytest
from zoneinfo import ZoneInfo

from assistant.follow.checks import Reading, Source
from assistant.follow.items import ItemFinder, UpcomingItem
from assistant.follow.plan import FollowPlan, Planner
from assistant.follow.runner import FollowRunner, format_change_line
from assistant.follow.store import FollowStore


class ScriptedFlightSource(Source):
    kind = "flight_status"

    def __init__(self, readings: list[dict[str, str]] | None = None):
        self._readings = list(readings or [])
        self._idx = 0
        self.call_count = 0

    def available(self) -> bool:
        return True

    def covers(self, refs: dict[str, str]) -> bool:
        return "flight" in refs

    def set_next(self, fields: dict[str, str]) -> None:
        self._readings.append(fields)

    async def read(self, refs: dict[str, str]) -> Reading:
        self.call_count += 1
        if self._idx < len(self._readings):
            fields = self._readings[self._idx]
            self._idx += 1
            return Reading(fields=fields, ok=True)
        if self._readings:
            return Reading(fields=self._readings[-1], ok=True)
        return Reading(fields={}, ok=False, detail="no readings configured")


class FailingSource(Source):
    kind = "flight_status"

    def __init__(self):
        self.call_count = 0

    def available(self) -> bool:
        return True

    def covers(self, refs: dict[str, str]) -> bool:
        return True

    async def read(self, refs: dict[str, str]) -> Reading:
        self.call_count += 1
        return Reading(fields={}, ok=False, detail="network connection timeout")


class FakeActivity:
    def __init__(self):
        self.notifications: list[tuple[str, str, str]] = []

    def notify(self, note_type: str, title: str, detail: str, push: bool = False):
        self.notifications.append((note_type, title, detail))


class FakeClock:
    def __init__(self, start: float = 1763500000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


@pytest.mark.asyncio
async def test_whole_loop_gate_change_notification():
    clock = FakeClock(1763500000.0)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    store = FollowStore(db, clock=clock)
    activity = FakeActivity()

    # Flight item starts in 2 hours
    flight_item = UpcomingItem(
        key="cal:flight-ke123",
        kind="flight",
        title="Flight KE123 to Tokyo",
        starts_at=clock.now + 7200,
        ends_at=clock.now + 14400,
        place="Incheon Airport T2",
        refs={"flight": "KE123", "date": "2026-11-20"},
        source="calendar",
    )

    finder = ItemFinder(calendar=lambda: [{
        "id": "flight-ke123",
        "title": "Flight KE123 to Tokyo",
        "start": flight_item.starts_at,
        "end": flight_item.ends_at,
        "where": flight_item.place,
    }], tz=ZoneInfo("UTC"))

    # Initial reading: gate A11, departure 14:05, on_time
    flight_source = ScriptedFlightSource([
        {"gate": "A11", "departure_time": "14:05", "status": "on_time"},
    ])

    planner = Planner(sources=[flight_source], clock=clock)
    runner = FollowRunner(
        store=store,
        finder=finder,
        planner=planner,
        sources=[flight_source],
        activity=activity,
        jitter=False,
        clock=clock,
    )

    # Tick 1: item planned, check created, initial reading fetched (gate A11)
    await runner.tick()
    assert len(activity.notifications) == 0
    item = store.get_item("cal:flight-ke123")
    assert item is not None
    assert item["state"] == "watching"
    checks = store.list_checks(item_key="cal:flight-ke123")
    assert len(checks) == 1
    assert checks[0]["state"] == "active"

    # Advance clock by 10 minutes (next check interval)
    clock.advance(600)
    # Next reading has gate changed: A11 -> A6!
    flight_source.set_next({"gate": "A6", "departure_time": "14:05", "status": "on_time"})

    # Tick 2: gate change detected!
    await runner.tick()
    assert len(activity.notifications) == 1
    note_type, title, detail = activity.notifications[0]
    assert note_type == "follow_change"
    assert title == "Flight KE123 to Tokyo"
    assert "A11 -> A6" in detail

    # Advance clock by 10 minutes again
    clock.advance(600)
    # Next reading is the SAME: gate A6 again
    flight_source.set_next({"gate": "A6", "departure_time": "14:05", "status": "on_time"})

    # Tick 3: same reading again -> nothing told!
    await runner.tick()
    assert len(activity.notifications) == 1  # Still exactly 1 notification!


@pytest.mark.asyncio
async def test_five_errors_stop_check_and_tell_once():
    clock = FakeClock(1763500000.0)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    store = FollowStore(db, clock=clock)
    activity = FakeActivity()

    flight_item = UpcomingItem(
        key="cal:failing-flight",
        kind="flight",
        title="Flight OZ101",
        starts_at=clock.now + 7200,
        ends_at=clock.now + 14400,
        place="Incheon",
        refs={"flight": "OZ101"},
        source="calendar",
    )

    finder = ItemFinder(calendar=lambda: [{
        "id": "failing-flight",
        "title": "Flight OZ101",
        "start": flight_item.starts_at,
        "end": flight_item.ends_at,
    }])

    failing_source = FailingSource()
    planner = Planner(sources=[failing_source], clock=clock)
    runner = FollowRunner(
        store=store,
        finder=finder,
        planner=planner,
        sources=[failing_source],
        activity=activity,
        jitter=False,
        clock=clock,
    )

    # Tick 1: planned and 1st error
    await runner.tick()
    check = store.list_checks(item_key="cal:failing-flight")[0]
    assert check["consecutive_errors"] == 1
    assert check["state"] == "active"
    assert len(activity.notifications) == 0

    # Errors 2, 3, 4
    for _ in range(3):
        clock.advance(600)
        await runner.tick()
        assert len(activity.notifications) == 0

    # 5th error: reaches MAX_ERRORS = 5 -> stops check and tells once
    clock.advance(600)
    await runner.tick()
    assert len(activity.notifications) == 1
    note_type, title, detail = activity.notifications[0]
    assert note_type == "follow_problem"
    assert title == "Flight OZ101"
    assert "repeated errors" in detail.lower() or "오류" in detail

    check = store.list_checks(item_key="cal:failing-flight")[0]
    assert check["state"] == "error"

    # 6th tick: does not check again or re-notify
    clock.advance(600)
    await runner.tick()
    assert len(activity.notifications) == 1


@pytest.mark.asyncio
async def test_window_end_stops_quietly():
    clock = FakeClock(1763500000.0)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    store = FollowStore(db, clock=clock)
    activity = FakeActivity()

    # Event starts at now + 1800, window ends at now + 3600
    flight_item = UpcomingItem(
        key="cal:expiring-flight",
        kind="flight",
        title="Flight KE999",
        starts_at=clock.now + 1800,
        ends_at=clock.now + 3600,
        place="Incheon",
        refs={"flight": "KE999"},
        source="calendar",
    )

    finder = ItemFinder(calendar=lambda: [{
        "id": "expiring-flight",
        "title": "Flight KE999",
        "start": flight_item.starts_at,
        "end": flight_item.ends_at,
    }])

    flight_source = ScriptedFlightSource([{"gate": "B2", "status": "on_time"}])
    planner = Planner(sources=[flight_source], clock=clock)
    runner = FollowRunner(
        store=store,
        finder=finder,
        planner=planner,
        sources=[flight_source],
        activity=activity,
        jitter=False,
        clock=clock,
    )

    await runner.tick()
    check = store.list_checks(item_key="cal:expiring-flight")[0]
    assert check["state"] == "active"

    # Fast forward past window_end
    clock.advance(7200)
    await runner.tick()

    # Check is stopped quietly: state becomes done, no notification sent
    check = store.list_checks(item_key="cal:expiring-flight")[0]
    assert check["state"] == "done"
    item = store.get_item("cal:expiring-flight")
    assert item["state"] == "done"
    assert len(activity.notifications) == 0


@pytest.mark.asyncio
async def test_quiet_hours_holds_changes_except_near_start():
    clock = FakeClock(1763500000.0)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    store = FollowStore(db, clock=clock)
    activity = FakeActivity()

    # Flight starts 4 hours from now (farther than NOW_WINDOW = 3h)
    flight_item = UpcomingItem(
        key="cal:quiet-flight",
        kind="flight",
        title="Flight KE777 to Osaka",
        starts_at=clock.now + 4 * 3600,
        ends_at=clock.now + 8 * 3600,
        place="Incheon",
        refs={"flight": "KE777"},
        source="calendar",
    )

    finder = ItemFinder(calendar=lambda: [{
        "id": "quiet-flight",
        "title": "Flight KE777 to Osaka",
        "start": flight_item.starts_at,
        "end": flight_item.ends_at,
    }])

    flight_source = ScriptedFlightSource([
        {"gate": "C1", "departure_time": "08:00", "status": "on_time"},
    ])

    class FakeProactive:
        def __init__(self):
            self.awake_state = False

        def awake(self, _ts: float) -> bool:
            return self.awake_state

    proactive = FakeProactive()
    planner = Planner(sources=[flight_source], clock=clock)

    async def custom_plan(item, _prefs=""):
        return planner.validate({
            "checks": [{
                "kind": "flight_status",
                "refs": dict(item.refs),
                "fields": ["gate", "departure_time", "status"],
                "from": "-5h",
                "until": "+30m",
                "every_minutes": 10,
            }],
        }, item)

    planner.plan = custom_plan
    runner = FollowRunner(
        store=store,
        finder=finder,
        planner=planner,
        sources=[flight_source],
        activity=activity,
        proactive=proactive,
        jitter=False,
        clock=clock,
    )

    # 1. Baseline reading
    await runner.tick()

    # 2. Gate changes during quiet hours (awake=False) and > 3h before flight
    clock.advance(600)
    flight_source.set_next({"gate": "C9", "departure_time": "08:00", "status": "on_time"})
    await runner.tick()

    # Notification should be held because it's quiet hours and > 3h away
    assert len(activity.notifications) == 0

    # 3. User wakes up (awake becomes True)
    proactive.awake_state = True
    clock.advance(600)
    await runner.tick()

    # Notification delivered!
    assert len(activity.notifications) == 1
    assert "C1 -> C9" in activity.notifications[0][2]


@pytest.mark.asyncio
async def test_lead_reminders_fire_once_each_and_done_after_end():
    start_ts = 1763500000.0
    clock = FakeClock(start_ts)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT);")
    store = FollowStore(db, clock=clock)
    activity = FakeActivity()

    # Event starts in 2 days, ends 2 hours after start
    item_start = start_ts + 2 * 86400
    item_end = item_start + 7200
    item = UpcomingItem(
        key="cal:trip-tokyo",
        kind="trip",
        title="Flight ICN-HND",
        starts_at=item_start,
        ends_at=item_end,
        place="Haneda",
        refs={"dest": "Tokyo"},
        source="calendar",
    )

    finder = ItemFinder(calendar=lambda: [{
        "id": "trip-tokyo",
        "title": "Flight ICN-HND",
        "start": item_start,
        "end": item_end,
        "where": "Haneda",
    }], tz=ZoneInfo("UTC"))

    planner = Planner(sources=[], clock=clock)

    async def custom_plan(_item, _prefs=""):
        return FollowPlan(
            checks=[],
            prep=[],
            tell={"lead": ["-1d", "-3h"], "on_change": True},
        )

    planner.plan = custom_plan
    runner = FollowRunner(
        store=store,
        finder=finder,
        planner=planner,
        sources=[],
        activity=activity,
        jitter=False,
        clock=clock,
    )

    # 1. Initial tick at start_ts (2 days before)
    await runner.tick()
    saved = store.get_item("cal:trip-tokyo")
    assert saved is not None
    assert saved["state"] == "watching"
    assert len(activity.notifications) == 0

    # 2. Advance to 1 day before (-1d)
    clock.advance(86400 + 60)  # now is start - 1d + 60s
    await runner.tick()
    assert len(activity.notifications) == 1
    n1_type, n1_title, n1_detail = activity.notifications[0]
    assert n1_type == "follow_reminder"
    assert n1_title == "Flight ICN-HND"
    assert "Flight ICN-HND" in n1_detail
    assert "Haneda" in n1_detail

    # Tick again: first lead does NOT fire again
    clock.advance(600)
    await runner.tick()
    assert len(activity.notifications) == 1

    # 3. Advance to 3 hours before (-3h)
    # Total elapsed so far: 86460 + 600 = 87060s. Need to reach 2*86400 - 3*3600 = 162000s.
    clock.advance(162000 - 87060 + 60)
    await runner.tick()
    assert len(activity.notifications) == 2
    n2_type, n2_title, n2_detail = activity.notifications[1]
    assert n2_type == "follow_reminder"
    assert n2_title == "Flight ICN-HND"
    assert "Flight ICN-HND" in n2_detail

    # Still watching
    saved = store.get_item("cal:trip-tokyo")
    assert saved["state"] == "watching"

    # 4. Advance past ends_at
    # Total elapsed so far: 162060s. item_end is 2*86400 + 7200 = 180000s.
    clock.advance(180000 - 162060 + 100)
    await runner.tick()

    # Item is now done! No extra notifications after the end
    saved = store.get_item("cal:trip-tokyo")
    assert saved["state"] == "done"
    assert len(activity.notifications) == 2


class FakeWorkers:
    def __init__(self):
        self.delegated: list[dict[str, Any]] = []
        self._jobs: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def delegate(self, args: dict[str, Any]) -> str:
        self._counter += 1
        wid = f"w{self._counter:03d}"
        self.delegated.append(dict(args))
        self._jobs[wid] = {"id": wid, "status": "running", "task": args.get("task"), "kind": args.get("kind")}
        return f"Handed off; you will hear back when it is done. (job {wid}: {args.get('kind', 'general')}, up to 10 steps / 5 min)"

    def get(self, wid: str) -> dict[str, Any] | None:
        return self._jobs.get(wid)

    def finish_job(self, wid: str, status: str = "done") -> None:
        if wid in self._jobs:
            self._jobs[wid]["status"] = status


@pytest.mark.asyncio
async def test_prep_starts_background_job_and_updates_state():
    start_ts = 1763500000.0
    clock = FakeClock(start_ts)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT);")
    store = FollowStore(db, clock=clock)
    activity = FakeActivity()
    workers = FakeWorkers()

    # Event starts in 2 days (172800s)
    item_start = start_ts + 2 * 86400
    item = UpcomingItem(
        key="cal:flight-prep",
        kind="flight",
        title="Flight KE123",
        starts_at=item_start,
        ends_at=item_start + 7200,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )

    finder = ItemFinder(calendar=lambda: [{
        "id": "flight-prep",
        "title": "Flight KE123",
        "start": item_start,
        "end": item_start + 7200,
        "where": "Incheon",
    }], tz=ZoneInfo("UTC"))

    planner = Planner(sources=[], clock=clock)

    async def custom_plan(_item, _prefs=""):
        return FollowPlan(
            checks=[],
            prep=[
                {"what": "online check-in", "when": "-24h", "needs": ["browser"]},
            ],
            tell={"lead": [], "on_change": True},
        )

    planner.plan = custom_plan
    runner = FollowRunner(
        store=store,
        finder=finder,
        planner=planner,
        sources=[],
        activity=activity,
        workers=workers,
        jitter=False,
        clock=clock,
    )

    # 1. Tick at start_ts (48h before flight): prep is not due yet
    await runner.tick()
    saved = store.get_item("cal:flight-prep")
    assert saved is not None
    assert saved["state"] == "watching"
    assert len(workers.delegated) == 0
    assert saved["prep"][0]["state"] == "later"

    # 2. Advance to 24h before flight (-24h offset reached)
    clock.advance(86400 + 60)
    await runner.tick()

    # Worker started!
    assert len(workers.delegated) == 1
    call = workers.delegated[0]
    assert call["kind"] == "browser"
    expected_prefix = "Prepare for Flight KE123 at"
    expected_suffix = "online check-in. Follow the skills. Payments and anything outside existing permissions still ask the user."
    assert expected_prefix in call["task"]
    assert expected_suffix in call["task"]

    saved = store.get_item("cal:flight-prep")
    assert saved["prep"][0]["state"] == "running"
    job_id = saved["prep"][0]["job_id"]
    assert job_id == "w001"

    # 3. Next tick while worker is running: does not launch another job
    clock.advance(600)
    await runner.tick()
    assert len(workers.delegated) == 1
    assert store.get_item("cal:flight-prep")["prep"][0]["state"] == "running"

    # 4. Worker finishes
    workers.finish_job(job_id, "done")
    clock.advance(60)
    await runner.tick()

    saved = store.get_item("cal:flight-prep")
    assert saved["prep"][0]["state"] == "done"


@pytest.mark.asyncio
async def test_prep_safety_caps():
    start_ts = 1763500000.0
    clock = FakeClock(start_ts)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT);")
    store = FollowStore(db, clock=clock)
    workers = FakeWorkers()

    # Item with 3 preps (exceeds per-item cap of 2)
    item_start = start_ts + 3600
    item1 = UpcomingItem(
        key="cal:item1",
        kind="flight",
        title="Flight KE201",
        starts_at=item_start,
        ends_at=item_start + 3600,
        place="",
        refs={},
        source="calendar",
    )

    finder = ItemFinder(calendar=lambda: [{
        "id": "item1",
        "title": "Flight KE201",
        "start": item_start,
        "end": item_start + 3600,
    }], tz=ZoneInfo("UTC"))

    planner = Planner(sources=[], clock=clock)

    async def custom_plan(_item, _prefs=""):
        return FollowPlan(
            checks=[],
            prep=[
                {"what": "prep1", "when": "-2h", "needs": []},
                {"what": "prep2", "when": "-2h", "needs": []},
                {"what": "prep3", "when": "-2h", "needs": []},
            ],
            tell={"lead": [], "on_change": True},
        )

    planner.plan = custom_plan
    runner = FollowRunner(
        store=store,
        finder=finder,
        planner=planner,
        sources=[],
        workers=workers,
        jitter=False,
        clock=clock,
    )

    await runner.tick()
    # At start_ts, all 3 preps are due (-2h from start_ts + 3600 = start_ts - 3600 <= start_ts)
    # But MAX_PREP_JOBS_PER_ITEM is 2!
    assert len(workers.delegated) == 2

    # Now add another item with 3 preps to test the daily cap of 4
    item2 = UpcomingItem(
        key="cal:item2",
        kind="flight",
        title="Flight KE202",
        starts_at=item_start,
        ends_at=item_start + 3600,
        place="",
        refs={},
        source="calendar",
    )
    store.save_item(
        item2,
        plan={"checks": [], "prep": [{"what": "prep4", "when": "-2h"}, {"what": "prep5", "when": "-2h"}, {"what": "prep6", "when": "-2h"}], "tell": {}},
        state="watching",
        prep=[
            {"what": "prep4", "when": "-2h", "state": "later"},
            {"what": "prep5", "when": "-2h", "state": "later"},
            {"what": "prep6", "when": "-2h", "state": "later"},
        ],
    )

    clock.advance(60)
    await runner.tick()

    # Total jobs across all items today must not exceed MAX_PREP_JOBS_PER_DAY = 4
    assert len(workers.delegated) == 4


@pytest.mark.asyncio
async def test_prep_completion_notices_done_and_failed():
    start_ts = 1763500000.0
    clock = FakeClock(start_ts)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT);")
    store = FollowStore(db, clock=clock)
    activity = FakeActivity()
    workers = FakeWorkers()

    item_start = start_ts + 2 * 86400
    item = UpcomingItem(
        key="cal:flight-notice-test",
        kind="flight",
        title="Flight KE123",
        starts_at=item_start,
        ends_at=item_start + 7200,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )

    store.save_item(
        item,
        plan={"checks": [], "prep": [{"what": "online check-in", "when": "-24h"}], "tell": {}},
        state="watching",
        prep=[{"what": "online check-in", "when": "-24h", "state": "later", "needs": ["browser"]}],
    )

    runner = FollowRunner(
        store=store,
        finder=ItemFinder(tz=ZoneInfo("UTC")),
        planner=Planner(sources=[], clock=clock),
        sources=[],
        activity=activity,
        workers=workers,
        jitter=False,
        clock=clock,
    )

    # 1. Advance to 24h before flight -> prep job starts
    clock.advance(86400 + 60)
    await runner.tick()
    assert len(workers.delegated) == 1
    assert len(activity.notifications) == 0
    saved = store.get_item("cal:flight-notice-test")
    assert saved["prep"][0]["state"] == "running"
    job_id = saved["prep"][0]["job_id"]

    # 2. Worker finishes as done -> send ONE follow_done notice
    workers.finish_job(job_id, "done")
    clock.advance(60)
    await runner.tick()

    saved = store.get_item("cal:flight-notice-test")
    assert saved["prep"][0]["state"] == "done"
    assert len(activity.notifications) == 1
    n_type, n_title, n_detail = activity.notifications[0]
    assert n_type == "follow_done"
    assert n_title == "Flight KE123"
    assert "Flight KE123: online check-in done ahead" in n_detail or "미리 해 뒀어요" in n_detail

    # 3. Next tick: no double notice!
    clock.advance(600)
    await runner.tick()
    assert len(activity.notifications) == 1

    # 4. Failed prep job sends follow_problem notice
    item2 = UpcomingItem(
        key="cal:hotel-fail-test",
        kind="stay",
        title="Hotel Booking",
        starts_at=item_start,
        ends_at=item_start + 7200,
        place="Tokyo",
        refs={},
        source="calendar",
    )
    store.save_item(
        item2,
        plan={"checks": [], "prep": [{"what": "confirm booking", "when": "-24h"}], "tell": {}},
        state="watching",
        prep=[{"what": "confirm booking", "when": "-24h", "state": "later", "needs": []}],
    )

    # Start the second job
    await runner.tick()
    assert len(workers.delegated) == 2
    saved2 = store.get_item("cal:hotel-fail-test")
    assert saved2["prep"][0]["state"] == "running"
    job2_id = saved2["prep"][0]["job_id"]

    # Finish worker with failure
    workers.finish_job(job2_id, "failed")
    clock.advance(60)
    await runner.tick()

    saved2 = store.get_item("cal:hotel-fail-test")
    assert saved2["prep"][0]["state"] == "failed"
    assert len(activity.notifications) == 2
    n2_type, n2_title, n2_detail = activity.notifications[1]
    assert n2_type == "follow_problem"
    assert n2_title == "Hotel Booking"
    assert "preparation failed" in n2_detail or "준비에 실패했어요" in n2_detail

    # 5. Next tick: no double notice for failed prep either
    clock.advance(600)
    await runner.tick()
    assert len(activity.notifications) == 2


@pytest.mark.asyncio
async def test_prep_done_quiet_hours():
    start_ts = 1763500000.0
    clock = FakeClock(start_ts)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT);")
    store = FollowStore(db, clock=clock)
    activity = FakeActivity()
    workers = FakeWorkers()

    # Event starts in 2 days (outside 3h NOW_WINDOW)
    item_start = start_ts + 2 * 86400
    item = UpcomingItem(
        key="cal:quiet-flight",
        kind="flight",
        title="Flight KE123",
        starts_at=item_start,
        ends_at=item_start + 7200,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )
    store.save_item(
        item,
        plan={"checks": [], "prep": [{"what": "online check-in", "when": "-24h"}], "tell": {}},
        state="watching",
        prep=[{"what": "online check-in", "when": "-24h", "state": "later", "needs": ["browser"]}],
    )

    runner = FollowRunner(
        store=store,
        finder=ItemFinder(tz=ZoneInfo("UTC")),
        planner=Planner(sources=[], clock=clock),
        sources=[],
        activity=activity,
        workers=workers,
        jitter=False,
        clock=clock,
    )

    # User is asleep
    awake = False
    runner.is_awake = lambda _ts: awake

    # Advance to -24h: job starts
    clock.advance(86400 + 60)
    await runner.tick()
    job_id = workers.delegated[0]["task"]
    saved = store.get_item("cal:quiet-flight")
    job_id = saved["prep"][0]["job_id"]

    # Worker finishes while user is asleep
    workers.finish_job(job_id, "done")
    clock.advance(60)
    await runner.tick()

    # Notice is held due to quiet hours!
    assert len(activity.notifications) == 0
    assert len(runner._pending_notices) == 1

    # User wakes up -> notice delivered
    awake = True
    clock.advance(60)
    await runner.tick()
    assert len(activity.notifications) == 1
    assert activity.notifications[0][0] == "follow_done"


@pytest.mark.asyncio
async def test_missing_login_advance_notice():
    from assistant.secret_requests import SecretRequests
    from assistant.i18n import use

    start_ts = 1763500000.0
    clock = FakeClock(start_ts)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT);")
    store = FollowStore(db, clock=clock)
    entries = SecretRequests(store, clock=clock)
    activity = FakeActivity()
    workers = FakeWorkers()

    # Event starts in 3 days. Prep is at -24h (2 days from now).
    # 1 day before prep time is at 1 day from now.
    item_start = start_ts + 3 * 86400
    item = UpcomingItem(
        key="cal:flight-missing-login",
        kind="flight",
        title="Flight KE123",
        starts_at=item_start,
        ends_at=item_start + 7200,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )

    store.save_item(
        item,
        plan={"checks": [], "prep": [{"what": "online check-in", "when": "-24h"}], "tell": {}},
        state="watching",
        prep=[{"what": "online check-in", "when": "-24h", "state": "later", "needs": ["browser", "login:airline.com"]}],
    )

    runner = FollowRunner(
        store=store,
        finder=ItemFinder(tz=ZoneInfo("UTC")),
        planner=Planner(sources=[], clock=clock),
        sources=[],
        activity=activity,
        workers=workers,
        secret_requests=entries,
        jitter=False,
        clock=clock,
    )

    # 1. At start_ts (2 days before prep time): too early, nothing asked
    await runner.tick()
    assert len(activity.notifications) == 0
    assert len(entries.pending()) == 0

    # 2. Advance to 1 day before prep time (start_ts + 86400 + 60)
    clock.advance(86400 + 60)
    await runner.tick()

    # Login card created and notice sent!
    assert len(activity.notifications) == 1
    n_type, n_title, n_detail = activity.notifications[0]
    assert n_type == "follow_reminder"
    assert n_title == "Flight KE123"
    assert "airline.com" in n_detail
    assert "Flight KE123" in n_detail

    open_cards = entries.pending()
    assert len(open_cards) == 1
    assert open_cards[0]["kind"] == "login"
    assert "airline.com" in open_cards[0]["hosts"]
    assert "airline.com" in open_cards[0]["reason"]

    # 3. Next tick: does not fire again (no double card, no double notice)
    clock.advance(600)
    await runner.tick()
    assert len(activity.notifications) == 1
    assert len(entries.pending()) == 1

    # 4. In Korean locale: translates to "<제목> 준비에 <사이트> 로그인이 필요해요"
    from assistant.i18n import reset
    token = use("ko")
    try:
        from assistant.i18n import t
        ko_text = t("I need your {site} login to prepare for {title}", site="대한항공", title="도쿄 비행편")
        assert ko_text == "도쿄 비행편 준비에 대한항공 로그인이 필요해요"
    finally:
        reset(token)


@pytest.mark.asyncio
async def test_missing_login_advance_notice_skipped_when_login_saved():
    from assistant.secret_requests import SecretRequests

    start_ts = 1763500000.0
    clock = FakeClock(start_ts)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT);")
    store = FollowStore(db, clock=clock)
    entries = SecretRequests(store, clock=clock)
    activity = FakeActivity()
    workers = FakeWorkers()

    item_start = start_ts + 3 * 86400
    item = UpcomingItem(
        key="cal:flight-saved-login",
        kind="flight",
        title="Flight KE123",
        starts_at=item_start,
        ends_at=item_start + 7200,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )

    store.save_item(
        item,
        plan={"checks": [], "prep": [{"what": "online check-in", "when": "-24h"}], "tell": {}},
        state="watching",
        prep=[{"what": "online check-in", "when": "-24h", "state": "later", "needs": ["browser", "login:airline.com"]}],
    )

    # Vault HAS a saved login for airline.com
    fake_vault = [{"kind": "login", "hosts": ["airline.com"], "has_secret": True}]

    runner = FollowRunner(
        store=store,
        finder=ItemFinder(tz=ZoneInfo("UTC")),
        planner=Planner(sources=[], clock=clock),
        sources=[],
        activity=activity,
        workers=workers,
        secret_requests=entries,
        vault=fake_vault,
        jitter=False,
        clock=clock,
    )

    # Advance to 1 day before prep time
    clock.advance(86400 + 60)
    await runner.tick()

    # Nothing asked because login is already saved!
    assert len(activity.notifications) == 0
    assert len(entries.pending()) == 0


@pytest.mark.asyncio
async def test_missing_login_dedupe_rules_apply():
    from assistant.secret_requests import SecretRequests

    start_ts = 1763500000.0
    clock = FakeClock(start_ts)
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, role TEXT);")
    store = FollowStore(db, clock=clock)
    entries = SecretRequests(store, clock=clock)
    activity = FakeActivity()
    workers = FakeWorkers()

    # Two items both needing login for airline.com
    item_start = start_ts + 3 * 86400
    item1 = UpcomingItem(
        key="cal:flight-dedupe-1",
        kind="flight",
        title="Flight KE123",
        starts_at=item_start,
        ends_at=item_start + 7200,
        place="Incheon",
        refs={"flight": "KE123"},
        source="calendar",
    )
    store.save_item(
        item1,
        plan={"checks": [], "prep": [{"what": "online check-in", "when": "-24h"}], "tell": {}},
        state="watching",
        prep=[{"what": "online check-in", "when": "-24h", "state": "later", "needs": ["browser", "login:airline.com"]}],
    )

    item2 = UpcomingItem(
        key="cal:flight-dedupe-2",
        kind="flight",
        title="Flight KE124",
        starts_at=item_start,
        ends_at=item_start + 7200,
        place="Tokyo",
        refs={"flight": "KE124"},
        source="calendar",
    )
    store.save_item(
        item2,
        plan={"checks": [], "prep": [{"what": "seat selection", "when": "-24h"}], "tell": {}},
        state="watching",
        prep=[{"what": "seat selection", "when": "-24h", "state": "later", "needs": ["browser", "login:airline.com"]}],
    )

    runner = FollowRunner(
        store=store,
        finder=ItemFinder(tz=ZoneInfo("UTC")),
        planner=Planner(sources=[], clock=clock),
        sources=[],
        activity=activity,
        workers=workers,
        secret_requests=entries,
        jitter=False,
        clock=clock,
    )

    # Advance to 1 day before prep time
    clock.advance(86400 + 60)
    await runner.tick()

    # Login-dedupe rules ensure exactly ONE open card exists for airline.com!
    open_cards = entries.pending()
    assert len(open_cards) == 1
    assert "airline.com" in open_cards[0]["hosts"]




class _RulesHome:
    def __init__(self, text):
        self.text = text

    def policy(self, rel):
        return self.text


async def test_the_do_not_tell_rules_are_read_by_the_main_model_and_unknown_means_not_planned():
    from notify_fake import FakeRules
    item = UpcomingItem(key="cal:x", kind="dining", title="Dinner at the sushi bar", starts_at=1.0, ends_at=2.0, place="Seoul", refs={}, source="calendar")
    prefs = "# NOTIFY_PREFS.md\n\n## Do not tell me\n- anything about sushi\n"
    clock = FakeClock(1763500000.0)

    def runner(text, rules):
        r = FollowRunner(FollowStore(sqlite3.connect(":memory:", check_same_thread=False), clock=clock), ItemFinder(tz=ZoneInfo("UTC")),
                         Planner(stream_fn=None, model=lambda: None, key_args=lambda p: {}, sources=[]), [], home=_RulesHome(text), tz=ZoneInfo("UTC"))
        r.notify_block = rules.block if rules else None
        return r
    assert await runner(prefs, FakeRules("sushi"))._is_suppressed(item) is True
    assert await runner(prefs, FakeRules("pizza"))._is_suppressed(item) is False
    assert await runner(prefs, FakeRules(down=True))._is_suppressed(item) is None       # unknown: the item is not planned or stopped now
    assert await runner(prefs, None)._is_suppressed(item) is None
    assert await runner("# NOTIFY_PREFS.md\n\n## Do not tell me\n\n## Tell me\n", FakeRules("sushi"))._is_suppressed(item) is False   # no rules written
