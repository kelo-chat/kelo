"""Tests for FollowPlan and Planner validation."""

from __future__ import annotations

import pytest

from assistant.follow.checks import Reading, Source
from assistant.follow.items import UpcomingItem
from assistant.follow.plan import FollowPlan, Planner
from assistant.providers.types import TextDelta


class FakeFlightSource(Source):
    kind = "flight_status"

    def __init__(self, is_available: bool = True):
        self._available = is_available

    def available(self) -> bool:
        return self._available

    def covers(self, refs: dict[str, str]) -> bool:
        return "flight" in refs

    async def read(self, refs: dict[str, str]) -> Reading:
        return Reading(fields={"gate": "A11", "status": "on_time"}, ok=True)


class FakePageSource(Source):
    kind = "page"

    def available(self) -> bool:
        return True

    def covers(self, refs: dict[str, str]) -> bool:
        return "url" in refs

    async def read(self, refs: dict[str, str]) -> Reading:
        return Reading(fields={"value": "open"}, ok=True)


def make_item(key: str = "cal:1", kind: str = "flight", starts_at: float = 1763500000.0) -> UpcomingItem:
    return UpcomingItem(
        key=key,
        kind=kind,
        title="Flight to Tokyo",
        starts_at=starts_at,
        ends_at=starts_at + 7200,
        place="Incheon",
        refs={"flight": "KE123", "date": "2026-11-20"},
        source="calendar",
    )


def test_planner_validate_unknown_kinds_dropped():
    sources = [FakeFlightSource(is_available=True)]
    planner = Planner(sources=sources)
    item = make_item()

    raw_plan = {
        "checks": [
            {"kind": "flight_status", "refs": {"flight": "KE123"}, "every_minutes": 10},
            {"kind": "weather", "refs": {"lat": "37.5"}, "every_minutes": 30},
            {"kind": "train_status", "refs": {"train": "KTX101"}, "every_minutes": 15},
        ],
        "prep": [],
    }

    plan = planner.validate(raw_plan, item)
    assert len(plan.checks) == 1
    assert plan.checks[0]["kind"] == "flight_status"


def test_planner_validate_intervals_clamped():
    sources = [FakeFlightSource(is_available=True)]
    planner = Planner(sources=sources)
    item = make_item()

    raw_plan = {
        "checks": [
            {"kind": "flight_status", "refs": {"flight": "KE123"}, "every_minutes": 2},
        ],
        "prep": [],
    }

    plan = planner.validate(raw_plan, item)
    assert len(plan.checks) == 1
    assert plan.checks[0]["every_minutes"] == 5  # clamped to MIN_EVERY = 5


def test_planner_validate_window_clamped_to_3_days():
    sources = [FakeFlightSource(is_available=True)]
    planner = Planner(sources=sources)
    item = make_item()

    # Window from -10 days to +1 day (span of 11 days)
    raw_plan = {
        "checks": [
            {
                "kind": "flight_status",
                "refs": {"flight": "KE123"},
                "from": "-10d",
                "until": "+1d",
                "every_minutes": 10,
            },
        ],
        "prep": [],
    }

    plan = planner.validate(raw_plan, item)
    assert len(plan.checks) == 1
    span = plan.checks[0]["window_end"] - plan.checks[0]["window_start"]
    assert span <= 3 * 86400  # at most 3 days


def test_planner_validate_max_checks_capped_at_6():
    sources = [FakeFlightSource(is_available=True)]
    planner = Planner(sources=sources)
    item = make_item()

    raw_plan = {
        "checks": [
            {"kind": "flight_status", "refs": {"flight": f"KE{i}"}, "every_minutes": 10}
            for i in range(10)
        ],
        "prep": [],
    }

    plan = planner.validate(raw_plan, item)
    assert len(plan.checks) == 6  # capped at MAX_CHECKS = 6


def test_planner_validate_nothing_to_do_kept():
    sources = [FakeFlightSource(is_available=True)]
    planner = Planner(sources=sources)
    item = make_item()

    raw_plan = {"checks": [], "prep": []}
    plan = planner.validate(raw_plan, item)
    assert plan.is_empty()
    assert plan.checks == []
    assert plan.prep == []


def test_no_key_drops_flight_check_page_check_still_works():
    # Flight source unavailable (no key), page source available
    flight_src = FakeFlightSource(is_available=False)
    page_src = FakePageSource()
    planner = Planner(sources=[flight_src, page_src])

    item = UpcomingItem(
        key="cal:2",
        kind="other",
        title="Event with page and flight",
        starts_at=1763500000.0,
        ends_at=None,
        place="",
        refs={"url": "https://example.com/status", "flight": "KE123"},
        source="calendar",
    )

    raw_plan = {
        "checks": [
            {"kind": "flight_status", "refs": {"flight": "KE123"}, "every_minutes": 10},
            {"kind": "page", "refs": {"url": "https://example.com/status"}, "every_minutes": 15},
        ],
        "prep": [],
    }

    plan = planner.validate(raw_plan, item)
    # Flight check dropped because FakeFlightSource is not available
    assert len(plan.checks) == 1
    assert plan.checks[0]["kind"] == "page"


@pytest.mark.asyncio
async def test_planner_model_call():
    sources = [FakeFlightSource(is_available=True)]

    async def fake_stream(_provider, _req, **_kw):
        json_output = (
            '{"checks": [{"kind": "flight_status", "refs": {"flight": "KE123"}, "every_minutes": 10}], '
            '"prep": [{"what": "check-in", "when": "-24h"}], "tell": {"on_change": true}}'
        )
        yield TextDelta(text="```json\n" + json_output + "\n```")

    planner = Planner(
        stream_fn=fake_stream,
        model=lambda: ("openai", "gpt-4o-mini"),
        sources=sources,
    )

    item = make_item()
    plan = await planner.plan(item)
    assert len(plan.checks) == 1
    assert plan.checks[0]["kind"] == "flight_status"
    assert len(plan.prep) == 1
    assert plan.prep[0]["what"] == "check-in" and plan.prep[0]["kind"] == "other"   # the model named no kind: the apps show it as a plain preparation
    assert plan.prep[0]["state"] == "later"


def test_plan_is_empty_false_when_tell_lead_has_offsets():
    # Empty checks and prep, but tell.lead has offsets -> not empty
    p1 = FollowPlan(checks=[], prep=[], tell={"lead": ["-1d", "-3h"], "on_change": True})
    assert not p1.is_empty()

    # Empty tell or no lead -> empty
    p2 = FollowPlan(checks=[], prep=[], tell={"on_change": True})
    assert p2.is_empty()

    p3 = FollowPlan(checks=[], prep=[], tell={"lead": []})
    assert p3.is_empty()


@pytest.mark.asyncio
async def test_scripted_plan_flight_and_booking():
    # Flight item without any available source
    planner = Planner(sources=[])
    flight_item = make_item(key="cal:fl-1", kind="flight")
    flight_plan = await planner.plan(flight_item)
    assert not flight_plan.is_empty()
    assert len(flight_plan.checks) == 0  # dropped because no source available
    assert len(flight_plan.prep) >= 1
    assert flight_plan.prep[0]["what"] == "online check-in"
    assert flight_plan.tell.get("lead") == ["-1d", "-3h"]

    # Booking item
    booking_item = UpcomingItem(
        key="mail:bk-1",
        kind="booking",
        title="Hotel Booking in Paris",
        starts_at=1763500000.0,
        ends_at=1763586400.0,
        place="Paris",
        refs={"booking": "HTL999"},
        source="mail",
    )
    booking_plan = await planner.plan(booking_item)
    assert not booking_plan.is_empty()
    assert len(booking_plan.prep) >= 1
    assert "booking" in booking_plan.prep[0]["what"] or "document" in booking_plan.prep[0]["what"]
    assert booking_plan.tell.get("lead") == ["-1d", "-3h"]


def test_the_prep_kind_comes_from_the_plan_not_from_the_words_of_what():
    p = Planner(stream_fn=None, model=lambda: None, key_args=lambda p: {}, sources=[])
    item = UpcomingItem(key="k", kind="flight", title="t", starts_at=1.0, ends_at=2.0, place="", refs={}, source="calendar")
    plan = p.validate({"prep": [{"what": "online check-in", "when": "-24h", "kind": "check_in"}, {"what": "체크인", "when": "-24h"},
                                {"what": "x", "kind": "made-up"}, {"what": "y", "kind": ["check_in"]}], "tell": {}}, item)
    assert [x["kind"] for x in plan.prep] == ["check_in", "other", "other", "other"]
    assert all(x["kind"] == "check_in" for x in p._scripted_plan(UpcomingItem(key="f", kind="flight", title="t", starts_at=1.0, ends_at=2.0, place="",
                                                                                refs={"flight": "KE123"}, source="calendar")).prep)
