"""Weather's other routes (weather.py): Open-Meteo through another of its servers when the normal route does not answer
(alternate_resolver, asked over DNS over HTTPS), and the US National Weather Service for a US place on auto (NwsForecast). Every
service is a fake (weather_fakes.py): no network, no real DNS (conftest.py)."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from assistant.netguard import NetGuardError, fetch
from assistant.store import Store
from assistant.weather import (
    METNO,
    NWS,
    OPEN_METEO,
    Place,
    Weather,
    alternate_resolver,
    describe,
)
from weather_fakes import NEW_YORK, FakeWeather, nws, open_meteo, ts

SEOUL_PLACE = Place("서울특별시", 37.566, 126.978, "KR", "서울특별시", "Asia/Seoul")
TOKYO = Place("Tokyo", 35.6895, 139.6917, "JP", "Tokyo", "Asia/Tokyo")
NY = Place("New York", 40.7128, -74.0060, "US", "New York", "America/New_York")
NY_NOW = ts(2026, 11, 17, 7, 0, tz=NEW_YORK)


def make_seoul(now: float = ts(2026, 11, 9, 7, 0)) -> tuple[Store, FakeWeather, Weather]:
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    fake = FakeWeather()
    return store, fake, Weather(store, fetcher=fake, clock=lambda: now, language=lambda: "ko")


def make_ny() -> tuple[Store, FakeWeather, Weather]:
    store = Store()
    store.set_profile(timezone="America/New_York")
    fake = FakeWeather()
    fake.forecast = open_meteo((2026, 11, 17), tz=NEW_YORK, tzname="America/New_York")
    return store, fake, Weather(store, fetcher=fake, clock=lambda: NY_NOW, language=lambda: "en")


def forecast_hosts(fake: FakeWeather) -> list[str]:
    return [urlsplit(u).hostname or "" for u in fake.calls if "/v1/forecast" in u]


# ------------------------------------------------------------------------------------------------- another route to Open-Meteo
async def test_the_normal_route_times_out_and_another_server_answers_for_the_same_name():
    _, fake, w = make_seoul()
    fake.plain_timeout.add("api.open-meteo.com")
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.provider == OPEN_METEO and fc.fallback_from == ""      # the service still answers: no fallback is shown
    assert fc.day("2026-11-09").high == 11.0                          # the fake's day
    assert fake.resolved == [["157.90.83.168"]]                       # the retry went to the address DNS gave 85.10.0.0/24
    assert ("api.open-meteo.com", "85.10.0.0/24") in fake.doh_answers
    assert forecast_hosts(fake) == ["api.open-meteo.com", "api.open-meteo.com"]   # the name, and so the certificate, did not change


async def test_the_other_server_is_kept_for_half_an_hour_and_asked_again_after():
    clock = {"t": ts(2026, 11, 9, 7, 0)}
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    fake = FakeWeather()
    w = Weather(store, fetcher=fake, clock=lambda: clock["t"], language=lambda: "ko")
    fake.plain_timeout.add("api.open-meteo.com")
    await w.forecast(SEOUL_PLACE)
    assert len(fake.doh_answers) == 1
    clock["t"] += 10 * 60
    w._cache.clear()
    await w.forecast(SEOUL_PLACE)
    assert len(fake.doh_answers) == 1 and len(fake.resolved) == 2     # kept: no new DNS question
    clock["t"] += 31 * 60
    w._cache.clear()
    await w.forecast(SEOUL_PLACE)
    assert len(fake.doh_answers) == 2                                  # older than ALT_SECONDS: asked again


async def test_when_the_other_server_does_not_answer_either_the_chain_goes_on_to_met_norway():
    _, fake, w = make_seoul()
    fake.plain_timeout.add("api.open-meteo.com")
    fake.reachable_ips = set()
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.provider == METNO and fc.fallback_from == OPEN_METEO and "timed out" in fc.note
    assert len(fake.resolved) == 1                                     # one retry only, not a loop


async def test_no_other_address_means_no_second_request():
    _, fake, w = make_seoul()
    fake.plain_timeout.add("api.open-meteo.com")
    fake.doh = {}                                                      # DNS names no other server for any region
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.provider == METNO and fake.resolved == []
    assert len(fake.doh_answers) == 2                                  # both regions were asked


async def test_a_private_address_in_the_dns_answer_is_refused_by_netguard():
    fake = FakeWeather()
    fake.doh = {"85.10.0.0/24": ["10.1.2.3"], "8.8.8.0/24": ["10.1.2.3"]}
    resolver = await alternate_resolver("api.open-meteo.com", fake, clock=lambda: 0.0)
    assert resolver is not None and resolver.ips == ("10.1.2.3",)     # found, then refused when it is used
    with pytest.raises(NetGuardError, match="non-public"):
        await fetch("GET", "https://api.open-meteo.com/v1/forecast?x=1", allowed_hosts=["api.open-meteo.com"], resolver=resolver)


async def test_an_answer_equal_to_the_normal_route_is_no_other_server():
    fake = FakeWeather()
    fake.doh = {"85.10.0.0/24": ["51.81.167.44"], "8.8.8.0/24": ["51.81.167.44"]}   # the same server as this network's own
    assert await alternate_resolver("api.open-meteo.com", fake, clock=lambda: 0.0) is None


# ---------------------------------------------------------------------------------------------- the US National Weather Service
async def test_a_us_place_on_auto_is_answered_by_the_national_weather_service_in_its_units():
    _, fake, w = make_ny()
    fake.nws_points, fake.nws_hourly, fake.nws_daily = nws(rain_from=15)
    fc = await w.forecast(NY)
    assert fc.provider == NWS and fc.fallback_from == ""
    day = fc.day("2026-11-17")
    assert day.high == pytest.approx((50 - 32) * 5 / 9) and day.low == pytest.approx((36 - 32) * 5 / 9)   # 50 and 36 °F, stored in °C
    assert fc.hours[0].wind_kmh == pytest.approx(10 * 1.609344)        # "5 to 10 mph" -> the stronger figure, in km/h
    assert any(h.wet() for h in fc.hours)
    assert fc.attribution() == ("Forecast by the US National Weather Service", "https://www.weather.gov/")
    text = describe(fc, NY_NOW)
    assert "high 50°F, low 36°F" in text and "US National Weather Service" in text
    assert not any(urlsplit(u).hostname == "api.open-meteo.com" for u in fake.calls)   # nothing from Open-Meteo's forecast


async def test_nws_down_means_open_meteo_answers_and_says_so():
    _, fake, w = make_ny()
    fake.nws_down = True
    fc = await w.forecast(NY)
    assert fc.provider == OPEN_METEO and fc.fallback_from == NWS and "could not resolve" in fc.note
    assert "US National Weather Service failed" in describe(fc, NY_NOW)


async def test_a_non_us_place_never_asks_the_national_weather_service():
    _, fake, w = make_seoul()
    await w.forecast(SEOUL_PLACE)
    await w.forecast(TOKYO)
    assert not any(urlsplit(u).hostname == "api.weather.gov" for u in fake.calls)


async def test_a_us_place_asking_past_the_national_weather_services_range_goes_to_open_meteo():
    _, fake, w = make_ny()
    fc = await w.forecast(NY, 10)
    assert fc.provider == OPEN_METEO and not any(urlsplit(u).hostname == "api.weather.gov" for u in fake.calls)


async def test_an_owner_pick_of_open_meteo_is_kept_for_a_us_place():
    _, fake, w = make_ny()
    w.update(provider=OPEN_METEO)
    fc = await w.forecast(NY)
    assert fc.provider == OPEN_METEO and not any(urlsplit(u).hostname == "api.weather.gov" for u in fake.calls)


async def test_after_a_dead_normal_route_the_other_server_is_asked_first_so_the_timeout_is_paid_once():
    """2026-11-17: each request first waited ~30 s on the dead normal route before the other server; after one failure the other
    server goes first for ALT_SECONDS."""
    clock = {"t": ts(2026, 11, 9, 7, 0)}
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    fake = FakeWeather()
    w = Weather(store, fetcher=fake, clock=lambda: clock["t"], language="ko".__str__)
    fake.plain_timeout.add("api.open-meteo.com")
    await w.forecast(SEOUL_PLACE)
    normal_tries = fake.plain_tries if hasattr(fake, "plain_tries") else None
    clock["t"] += 5 * 60
    w._cache.clear()
    before = len(fake.calls)
    await w.forecast(SEOUL_PLACE)
    forecast_calls = [u for u in fake.calls[before:] if "api.open-meteo.com" in u and "/v1/forecast" in u]
    assert len(forecast_calls) == 1, forecast_calls          # straight to the other server: no second wait on the normal route
