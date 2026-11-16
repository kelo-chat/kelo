"""Tests for UpcomingItem and ItemFinder."""

from __future__ import annotations

from zoneinfo import ZoneInfo
import pytest

from assistant.follow.items import ItemFinder, UpcomingItem, extract_flight_ref


@pytest.mark.asyncio
async def test_extract_flight_ref():
    assert extract_flight_ref("Flight KE123 to Tokyo") == "KE123"
    assert extract_flight_ref("Korean Air KE 1234") == "KE1234"
    assert extract_flight_ref("OZ202 departure") == "OZ202"
    assert extract_flight_ref("7C101 flight") == "7C101"
    assert extract_flight_ref("Lunch with Alice") is None


@pytest.mark.asyncio
async def test_item_finder_calendar_flight_with_flight_number():
    now = 1763500000.0
    tz = ZoneInfo("UTC")

    events = [
        {
            "id": "ev-flight-1",
            "title": "Flight KE123 to Tokyo",
            "start": now + 7200,
            "end": now + 14400,
            "where": "Incheon Terminal 2",
        }
    ]

    finder = ItemFinder(calendar=lambda: events, tz=tz)
    items = await finder.find(now)

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, UpcomingItem)
    assert item.key == "cal:ev-flight-1"
    assert item.kind == "flight"
    assert item.title == "Flight KE123 to Tokyo"
    assert item.refs["flight"] == "KE123"
    assert "date" in item.refs
    assert item.source == "calendar"
    assert item.place == "Incheon Terminal 2"


@pytest.mark.asyncio
async def test_item_finder_booking_mail():
    now = 1763500000.0
    tz = ZoneInfo("UTC")

    messages = [
        {
            "id": "msg-flight-99",
            "subject": "Booking confirmation: Flight OZ202",
            "body": "Your flight OZ202 on 2026-11-20 is confirmed.",
            "starts_at": now + 86400,
            "place": "Incheon",
        }
    ]

    finder = ItemFinder(calendar=lambda: [], mail=lambda: messages, tz=tz)
    items = await finder.find(now)

    assert len(items) == 1
    item = items[0]
    assert item.key == "mail:msg-flight-99"
    assert item.kind == "flight"
    assert item.refs["flight"] == "OZ202"
    assert item.source == "mail"


@pytest.mark.asyncio
async def test_item_finder_nothing_found():
    now = 1763500000.0
    tz = ZoneInfo("UTC")

    # Regular meeting without big_events kind and no flight number
    events = [
        {
            "id": "meeting-1",
            "title": "Quick sync with Bob",
            "start": now + 1800,
            "end": now + 3600,
            "where": "Room 301",
        }
    ]

    finder = ItemFinder(calendar=lambda: events, mail=lambda: [], tz=tz)
    items = await finder.find(now)
    assert len(items) == 0


@pytest.mark.asyncio
async def test_item_finder_ignores_events_outside_lookahead():
    now = 1763500000.0
    tz = ZoneInfo("UTC")

    # Event 20 days ahead (past LOOK_AHEAD_DAYS = 14)
    events = [
        {
            "id": "far-flight",
            "title": "Flight KE123",
            "start": now + 20 * 86400,
            "end": now + 20 * 86400 + 7200,
        }
    ]

    finder = ItemFinder(calendar=lambda: events, tz=tz)
    items = await finder.find(now)
    assert len(items) == 0


@pytest.mark.asyncio
async def test_item_finder_merges_duplicates_across_sources():
    now = 1763500000.0
    tz = ZoneInfo("UTC")

    # Calendar event from phone calendar
    ev_phone = {
        "id": "phone:101",
        "title": "Flight KE123 to Tokyo",
        "start": now + 7200,
        "end": now + 14400,
        "where": "Terminal 2",
    }
    # Calendar event from Google calendar (different spacing, casing, start time 5 min later)
    ev_gcal = {
        "id": "gcal:202",
        "title": "  flight   ke123  to  tokyo  ",
        "start": now + 7200 + 300,  # 5 min later (within 10 min window)
        "end": now + 14400,
        "where": "Incheon Terminal 2",
    }

    finder = ItemFinder(calendar=lambda: [ev_phone, ev_gcal], tz=tz)
    items = await finder.find(now)

    assert len(items) == 1
    item = items[0]
    assert item.key == "cal:phone:101"
    assert "cal:gcal:202" in item.aliases
    assert finder.aliases.get("cal:gcal:202") == "cal:phone:101"

    # Subsequent pass: even if only the alias is returned by the calendar, it is not re-added
    finder.calendar = lambda: [ev_gcal]
    second_items = await finder.find(now)
    assert len(second_items) == 0


@pytest.mark.asyncio
async def test_item_finder_merges_across_calendar_and_mail():
    now = 1763500000.0
    tz = ZoneInfo("UTC")

    ev = {
        "id": "cal-trip-1",
        "title": "Trip to Jeju",
        "start": now + 3600,
        "end": now + 72000,
        "where": "Jeju Island",
    }
    mail_msg = {
        "id": "mail-jeju-booking",
        "subject": "trip   to  jeju",
        "body": "Booking confirmation #JEJU12345",
        "starts_at": now + 3600 + 400,  # within 10 min
        "place": "Jeju",
    }

    finder = ItemFinder(calendar=lambda: [ev], mail=lambda: [mail_msg], tz=tz)
    items = await finder.find(now)

    assert len(items) == 1
    assert items[0].key == "cal:cal-trip-1"
    assert "mail:mail-jeju-booking" in items[0].aliases
    assert finder.aliases.get("mail:mail-jeju-booking") == "cal:cal-trip-1"

