"""Tests for the weather check source (Open-Meteo through a fake HTTP client) and its path through the follow runner."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from assistant.follow.items import ItemFinder, UpcomingItem
from assistant.follow.plan import Planner
from assistant.follow.runner import FollowRunner, format_change_line
from assistant.follow.sources.weather import WeatherSource, parse_at, place_candidates
from assistant.follow.store import FollowStore
from assistant.netguard import NetGuardError, Reply
from assistant.store import Store
from assistant.weather import Weather

from weather_fakes import met_norway

H0 = 1763499600.0   # a full hour (UTC)
AT = H0 + 5 * 3600 + 600   # the item: 10 minutes into the sixth hour
PLACE = "Seoul Forest, Seongdong-gu, Seoul"
GEO = {"results": [{"name": "Seoul", "latitude": 37.57, "longitude": 126.98, "country_code": "KR", "admin1": "Seoul",
                    "timezone": "Asia/Seoul", "feature_code": "PPLC", "population": 10_000_000}]}


class FakeOpenMeteo:
    """Answers the geocoding and the forecast request the way Open-Meteo does; `rain`, `temp`, `gust` are the hour of the item."""

    def __init__(self) -> None:
        self.rain, self.temp, self.gust, self.code = 10, 12.0, 20.0, 1
        self.calls: list[str] = []
        self.geo: dict[str, Any] = GEO
        self.fail = False
        self.meteo_down = False            # api.open-meteo.com cannot be reached (the geocoding host still answers)
        self.met: dict[str, Any] | None = None   # MET Norway's answer; None = that host is down too

    def forecast(self) -> dict[str, Any]:
        n = 48
        hours = [H0 + i * 3600 for i in range(n)]
        prob = [self.rain if h <= AT < h + 3600 else 0 for h in hours]
        precip = [1.2 if p >= 60 else 0.0 for p in prob]
        temp = [self.temp if h <= AT < h + 3600 else 10.0 for h in hours]
        gust = [self.gust if h <= AT < h + 3600 else 5.0 for h in hours]
        code = [(63 if p >= 60 else self.code) for p in prob]
        return {"timezone": "Asia/Seoul",
                "hourly": {"time": hours, "temperature_2m": temp, "precipitation": precip, "precipitation_probability": prob,
                           "snowfall": [0.0] * n, "wind_speed_10m": [8.0] * n, "wind_gusts_10m": gust, "weather_code": code},
                "daily": {"time": [H0 - 86400, H0, H0 + 86400], "temperature_2m_max": [14, 15, 16], "temperature_2m_min": [3, 4, 5],
                          "weather_code": [1, 1, 1]}}

    async def __call__(self, method: str, url: str, **kw: Any) -> Reply:
        self.calls.append(url)
        if "dns.google" in url:   # DNS over HTTPS, the other route to Open-Meteo: this fake names no other server
            return Reply(200, {}, json.dumps({"Status": 0}).encode(), False)
        if "api.met.no" in url:
            if self.met is None:
                raise NetGuardError("could not resolve host")
            return Reply(200, {}, json.dumps(self.met).encode(), False)
        if self.meteo_down and "//api.open-meteo.com/" in url:
            raise NetGuardError("could not resolve host")
        if "air-quality-api" in url:
            return Reply(503, {}, b"{}", False)   # no air quality here: the forecast does without it
        if self.fail:
            return Reply(503, {}, b"{}", False)
        if "geocoding-api.open-meteo.com" in url:
            return Reply(200, {}, json.dumps(self.geo).encode(), False)
        assert "api.open-meteo.com/v1/forecast" in url
        assert kw["allowed_hosts"] == ["api.open-meteo.com"]
        return Reply(200, {}, json.dumps(self.forecast()).encode(), False)


class Clock:
    def __init__(self, now: float = H0 + 3600) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Activity:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, note_type: str, title: str, detail: str, push: bool = False) -> None:
        self.notes.append((note_type, title, detail))


def refs() -> dict[str, str]:
    return {"place": PLACE, "at": str(int(AT))}


def test_refs_and_place_candidates():
    assert parse_at("1763500000") == 1763500000.0
    assert parse_at("2026-11-20T09:00:00+09:00") is not None
    assert parse_at("2026-11-20T09:00:00") is None   # no offset: not a point in time
    assert parse_at("tomorrow") is None
    assert place_candidates("A, B ,C") == ["A, B ,C", "C", "B", "A"]
    assert place_candidates("Busan") == ["Busan"]
    src = WeatherSource(fetcher=FakeOpenMeteo())
    assert src.available() and src.covers(refs())
    assert not src.covers({"place": "", "at": "1"}) and not src.covers({"place": "Seoul"}) and not src.covers({"at": "1"})


@pytest.mark.asyncio
async def test_weather_reading_is_parsed():
    fake = FakeOpenMeteo()
    fake.rain, fake.temp, fake.gust = 72, 11.6, 62.0
    src = WeatherSource(fetcher=fake, clock=Clock())
    r = await src.read(refs())
    assert r.ok, r.detail
    assert r.fields["rain_chance"] == 72
    assert r.fields["temperature"] == 12
    assert r.fields["condition"] == "rain"
    assert r.fields["precip_mm"] == 1.2
    assert r.fields["warning"] == "wind"
    assert "Seoul" in r.detail
    # only the two Open-Meteo hosts are used (the fake asserts the forecast host list)
    assert any("geocoding-api" in c for c in fake.calls) and any("v1/forecast" in c for c in fake.calls)


@pytest.mark.asyncio
async def test_weather_place_falls_back_to_comma_parts_and_caches():
    fake = FakeOpenMeteo()
    asked: list[str] = []

    async def geocode(name: str):
        asked.append(name)
        from assistant.weather import geocode_places
        return await geocode_places(fake, name, "en", 1) if name == "Seoul" else []

    clock = Clock()
    src = WeatherSource(fetcher=fake, geocode=geocode, clock=clock)
    assert (await src.read(refs())).ok
    assert asked == [PLACE, "Seoul"]   # the whole text first, then the parts from the last
    assert (await src.read(refs())).ok
    assert asked == [PLACE, "Seoul"]   # place and forecast are cached: no second lookup
    assert sum("v1/forecast" in c for c in fake.calls) == 1
    clock.now += 1300
    assert (await src.read(refs())).ok
    assert sum("v1/forecast" in c for c in fake.calls) == 2


@pytest.mark.asyncio
async def test_weather_failures_are_readings_not_crashes():
    fake = FakeOpenMeteo()
    src = WeatherSource(fetcher=fake, clock=Clock())
    fake.geo = {"results": []}
    r = await src.read(refs())
    assert not r.ok and "no place found" in r.detail
    fake.geo = GEO
    fake.fail = True
    src2 = WeatherSource(fetcher=fake, clock=Clock())
    assert not (await src2.read(refs())).ok
    fake.fail = False
    r3 = await WeatherSource(fetcher=fake, clock=Clock()).read({"place": PLACE, "at": str(int(H0 + 90 * 3600))})
    assert not r3.ok and "no forecast" in r3.detail
    assert not (await src.read({"place": PLACE})).ok


def test_weather_significant_moves_only():
    src = WeatherSource(fetcher=FakeOpenMeteo())
    assert not src.significant("rain_chance", 10, 25)
    assert src.significant("rain_chance", 10, 30)
    assert not src.significant("temperature", 12, 14)
    assert src.significant("temperature", 12, 15)
    assert src.significant("condition", "clear", "rain") and src.significant("warning", "", "wind")
    assert not src.significant("precip_mm", 0.0, 3.0)


def test_weather_change_lines():
    assert format_change_line("rain_chance", "10", "70") == "Chance of rain 10% -> 70%"
    assert format_change_line("temperature", "12", "6") == "Temperature 12° -> 6°"
    assert format_change_line("condition", "clear", "rain") == "Weather: clear -> rain"
    assert format_change_line("warning", "", "wind,heat") == "Weather warning: strong wind, heat"
    assert format_change_line("warning", "wind", "") == "Weather warning: none"


class WeatherPlanner(Planner):
    async def plan(self, item, notify_prefs=""):
        return self.validate({"checks": [{"kind": "weather", "fields": ["rain_chance", "temperature", "condition", "warning"],
                                          "from": "-5h", "until": "+1h", "every_minutes": 30}], "tell": {}}, item)


def make_runner(clock: Clock, fake: FakeOpenMeteo, db: sqlite3.Connection | None = None):
    db = db or sqlite3.connect(":memory:", check_same_thread=False)
    store = FollowStore(db, clock=clock)
    activity = Activity()
    source = WeatherSource(fetcher=fake, clock=clock, follow_store=store)
    finder = ItemFinder(calendar=lambda: [], tz=ZoneInfo("UTC"))
    # a thing said in chat is kept "later" and planned once it is inside the look-ahead window (the finder only reads big events)
    store.save_item(UpcomingItem(key="chat:picnic", kind="appointment", title="Picnic with friends", starts_at=AT, ends_at=AT + 7200,
                                 place=PLACE, refs={}, source="chat"), state="later")
    runner = FollowRunner(store=store, finder=finder, planner=WeatherPlanner(sources=[source], clock=clock), sources=[source],
                          activity=activity, jitter=False, clock=clock)
    return runner, store, activity, source


@pytest.mark.asyncio
async def test_plan_fills_the_weather_refs_from_the_item():
    clock, fake = Clock(), FakeOpenMeteo()
    runner, store, _a, source = make_runner(clock, fake)
    item = UpcomingItem(key="cal:x", kind="appointment", title="Picnic", starts_at=AT, ends_at=None, place=PLACE, refs={}, source="calendar")
    plan = runner.planner.validate({"checks": [{"kind": "weather"}]}, item)
    assert plan.checks[0]["refs"] == {"place": PLACE, "at": str(int(AT))}
    assert plan.checks[0]["fields"] == list(source.default_fields)
    nowhere = UpcomingItem(key="cal:y", kind="appointment", title="Call", starts_at=AT, ends_at=None, place="", refs={}, source="calendar")
    assert runner.planner.validate({"checks": [{"kind": "weather"}]}, nowhere).checks == []   # no place: nothing to watch


@pytest.mark.asyncio
async def test_rain_change_is_told_once_and_small_moves_are_not():
    clock, fake = Clock(), FakeOpenMeteo()
    runner, store, activity, source = make_runner(clock, fake)

    await runner.tick()   # plans the picnic and reads: 10 % rain, 12 degrees
    check = store.list_checks(item_key="chat:picnic")[0]
    assert check["kind"] == "weather" and check["state"] == "active"
    assert store.last_reading(check["id"])["fields"]["rain_chance"] == 10
    assert activity.notes == []

    clock.now += 1900   # the forecast cache has run out
    fake.rain, fake.temp = 25, 13.0   # a small move: neither told nor stored
    await runner.tick()
    assert activity.notes == []
    assert store.last_reading(check["id"])["fields"]["rain_chance"] == 10

    clock.now += 1900
    fake.rain = 70   # 60 points above the told value: rain is likely now
    await runner.tick()
    assert [(n[0], n[1]) for n in activity.notes] == [("follow_change", "Picnic with friends")]
    detail = activity.notes[0][2]
    assert "Chance of rain 10% -> 70%" in detail
    assert "Weather: clear -> rain" in detail or "Weather: partly cloudy -> rain" in detail

    clock.now += 1900
    await runner.tick()   # the same forecast again: quiet
    assert len(activity.notes) == 1


@pytest.mark.asyncio
async def test_slow_drift_adds_up_to_one_message():
    clock, fake = Clock(), FakeOpenMeteo()
    runner, store, activity, _s = make_runner(clock, fake)
    await runner.tick()
    for chance in (20, 30):   # +10 each time: the first is no change against the told 10 %, the second is 20 above it
        clock.now += 1900
        fake.rain = chance
        await runner.tick()
        assert len(activity.notes) == (1 if chance == 30 else 0)
    assert "Chance of rain 10% -> 30%" in activity.notes[0][2]


@pytest.mark.asyncio
async def test_weather_calls_are_counted_per_month():
    clock, fake = Clock(), FakeOpenMeteo()
    _r, store, _a, source = make_runner(clock, fake)
    assert (await source.read(refs())).ok
    use = source.usage()
    assert use is not None and use["source"] == "weather_open_meteo" and use["used"] == 2 and use["allowance"] is None   # one place lookup, one forecast


@pytest.mark.asyncio
async def test_open_meteo_down_the_follow_weather_check_still_gets_a_forecast_from_met_norway():
    fake = FakeOpenMeteo()
    fake.meteo_down = True
    fake.met = met_norway(day=(2025, 11, 19))   # covers AT (2025-11-19 11:10 in Seoul)
    clock = Clock()
    src = WeatherSource(fetcher=fake, clock=clock, weather=Weather(Store(), fetcher=fake, clock=clock))
    r = await src.read(refs())
    assert r.ok, r.detail
    assert "temperature" in r.fields and "condition" in r.fields and "Seoul" in r.detail
    assert any("api.met.no" in c for c in fake.calls)


@pytest.mark.asyncio
async def test_every_forecast_down_a_recent_forecast_still_answers_a_check_and_an_old_one_does_not():
    fake = FakeOpenMeteo()
    fake.met = met_norway(day=(2025, 11, 19))
    clock = Clock()
    src = WeatherSource(fetcher=fake, clock=clock, weather=Weather(Store(), fetcher=fake, clock=clock))
    assert (await src.read(refs())).ok                      # fetched and kept
    clock.now += 30 * 60                                     # past the 20-minute cache, inside the 12-hour allowance
    fake.meteo_down = True
    fake.met = None
    assert (await src.read(refs())).ok
    clock.now += 13 * 3600                                   # older than the allowance
    r = await src.read(refs())
    assert not r.ok

