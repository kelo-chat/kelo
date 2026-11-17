"""Tests for follow sources: IncheonFlights, AeroApiFlights, and PageSource."""

from __future__ import annotations

import json
import sqlite3
import pytest

from assistant.follow.sources.flight_aeroapi import AeroApiFlights, ALLOWANCE_STOP, FREE_PER_MONTH
from assistant.follow.sources.flight_icn import IncheonFlights
from assistant.follow.sources.page import PageSource
from assistant.follow.store import FollowStore
from assistant.netguard import Reply


class FakeStore:
    def __init__(self, key: str | None = None):
        self._key = key

    def credential(self, name: str):
        if self._key:
            return {"name": name, "secret": self._key}
        return None

    def key(self, name: str):
        return self._key


@pytest.mark.asyncio
async def test_incheon_flights_available_without_key_is_false():
    src_no_key = IncheonFlights(store=FakeStore(key=None))
    assert src_no_key.available() is False

    src_with_key = IncheonFlights(store=FakeStore(key="my-data-go-kr-key"))
    assert src_with_key.available() is True


@pytest.mark.asyncio
async def test_incheon_flights_read_fake():
    async def fake_fetch(method: str, url: str, **kw):
        assert "serviceKey=my-key" in url
        assert "flight_id=KE123" in url
        body = json.dumps({
            "response": {
                "body": {
                    "items": [
                        {
                            "flightId": "KE123",
                            "gatenumber": "A11",
                            "terminalid": "T2",
                            "scheduleDateTime": "1405",
                            "remark": "출발",
                        }
                    ]
                }
            }
        })
        return Reply(200, {}, body.encode(), False)

    src = IncheonFlights(key="my-key", fetcher=fake_fetch)
    reading = await src.read({"flight": "KE123", "date": "2026-11-20"})
    assert reading.ok is True
    assert reading.fields["gate"] == "A11"
    assert reading.fields["terminal"] == "T2"
    assert reading.fields["departure_time"] == "1405"
    assert reading.fields["status"] == "출발"


@pytest.mark.asyncio
async def test_aeroapi_flights_allowance_counter():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    follow_store = FollowStore(db)
    notices: list[tuple[str, str, str]] = []

    def fake_notify(kind: str, title: str, detail: str):
        notices.append((kind, title, detail))

    async def fake_fetch(method: str, url: str, **kw):
        assert kw["headers"].get("x-apikey") == "fa-key"
        body = json.dumps({
            "flights": [
                {
                    "ident": "KE123",
                    "gate_origin": "A11",
                    "terminal_origin": "2",
                    "estimated_out": "14:50",
                    "status": "Scheduled",
                }
            ]
        })
        return Reply(200, {}, body.encode(), False)

    src = AeroApiFlights(
        key="fa-key",
        follow_store=follow_store,
        fetcher=fake_fetch,
        notify=fake_notify,
        clock=lambda: 1763500000.0,
    )

    # 1. Under allowance limit: available is True, read succeeds
    assert src.available() is True
    reading = await src.read({"flight": "KE123"})
    assert reading.ok is True
    assert reading.fields["gate"] == "A11"
    assert follow_store.get_usage("flight_aeroapi", "2025-11") == 1
    assert len(notices) == 0

    # 2. Advance usage to 90% threshold (450 calls out of 500)
    month = "2025-11"
    limit = int(FREE_PER_MONTH * ALLOWANCE_STOP)
    for _ in range(limit):
        follow_store.count_usage("flight_aeroapi", month)

    # 3. Now allowance exceeded: the source is paused (the key is still there), read refuses and tells once
    assert src.available() is True
    assert src.paused({"flight": "KE123"}) != ""
    reading2 = await src.read({"flight": "KE123"})
    assert reading2.ok is False
    assert "allowance reached 90%" in reading2.detail
    assert len(notices) == 1
    assert notices[0][0] == "follow_problem"
    assert notices[0][1] == "FlightAware"

    # 4. A second attempt also refuses, but does NOT send another notification
    reading3 = await src.read({"flight": "KE123"})
    assert reading3.ok is False
    assert len(notices) == 1


@pytest.mark.asyncio
async def test_page_source_read():
    html_content = "<html><body><div id='price'>$129</div></body></html>"

    async def fake_fetch_html(url: str):
        return html_content

    src = PageSource(fetch_fn=fake_fetch_html)
    assert src.available() is True
    assert src.covers({"url": "https://example.com/item"}) is True

    reading = await src.read({
        "url": "https://example.com/item",
        "selector": "#price",
        "field": "price",
    })
    assert reading.ok is True
    assert reading.fields["price"] == "$129"
