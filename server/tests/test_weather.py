"""Weather without a browser (weather.py): the KMA grid and issue times, which provider answers, parsing each provider, the notable
changes of a day (rain from 15:00, snow, a drop from yesterday, wind, air), the owner's own keys (checked before saving), place search,
and the agent's `weather` tool. Every service is a fake (weather_fakes.py): no network."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from assistant.store import Store
from assistant.weather import (KMA, METNO, OPEN_METEO, WEATHERAPI, Day, Place, Weather, WeatherError, describe, in_korea, kma_base, kma_grid,
                               notable, units_for)

from weather_fakes import FakeWeather, kma_items, met_norway, open_meteo, air, ts, wapi_day

SEOUL_PLACE = Place("서울특별시", 37.566, 126.978, "KR", "서울특별시", "Asia/Seoul")
TOKYO = Place("Tokyo", 35.6895, 139.6917, "JP", "Tokyo", "Asia/Tokyo")


def make(now: float = ts(2026, 11, 9, 7, 0)) -> tuple[Store, FakeWeather, Weather]:
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    fake = FakeWeather()
    return store, fake, Weather(store, fetcher=fake, clock=lambda: now, language=lambda: "ko")


# ---------------------------------------------------------------------------------------------------------- KMA grid and times
def test_kma_grid_matches_the_kma_points():
    assert kma_grid(37.5665, 126.9780) == (60, 127)      # Seoul City Hall
    assert kma_grid(35.1796, 129.0756) == (98, 76)       # Busan
    assert kma_grid(33.4996, 126.5312) == (53, 38)       # Jeju City


def test_kma_issue_times():
    assert kma_base(ts(2026, 11, 9, 7, 0)) == ("20261109", "0500")
    assert kma_base(ts(2026, 11, 9, 5, 5)) == ("20261109", "0200")    # 05:00 is not out yet
    assert kma_base(ts(2026, 11, 9, 1, 0)) == ("20261108", "2300")
    assert kma_base(ts(2026, 11, 9, 23, 30)) == ("20261109", "2300")


# ---------------------------------------------------------------------------------------------------------- which provider
async def test_provider_choice_kma_only_with_a_key_and_in_korea():
    store, fake, w = make()
    assert w.choose(SEOUL_PLACE) == OPEN_METEO                          # no key: Open-Meteo
    await w.save_key(KMA, "good-kma-key-123")
    assert w.choose(SEOUL_PLACE) == KMA and w.choose(TOKYO) == OPEN_METEO
    assert in_korea(Place("x", 35.1, 129.0)) and not in_korea(Place("x", 35.1, 139.0))
    w.update(provider=WEATHERAPI)
    assert w.choose(SEOUL_PLACE) == OPEN_METEO                          # picked, but no WeatherAPI key: Open-Meteo
    fake.wapi = wapi_day()
    await w.save_key(WEATHERAPI, "good-wapi-key-123")
    assert w.choose(SEOUL_PLACE) == WEATHERAPI
    w.update(provider=OPEN_METEO)
    assert w.choose(SEOUL_PLACE) == OPEN_METEO
    with pytest.raises(WeatherError):
        w.update(provider="somewhere")


async def test_keys_are_checked_before_saving_and_never_shown():
    store, fake, w = make()
    with pytest.raises(WeatherError):
        await w.save_key(KMA, "wrong-key-000")
    assert store.credential("kma-weather-api") is None                   # refused: nothing saved
    await w.save_key(KMA, "good-kma-key-123")
    cred = store.credential("kma-weather-api")
    assert cred["secret"] == "good-kma-key-123" and cred["hosts"] == ["apis.data.go.kr"]
    assert w.public()["keys"] == {KMA: True, WEATHERAPI: False} and "good-kma-key-123" not in str(w.public())
    assert w.delete_key(KMA) and store.credential("kma-weather-api") is None and not w.delete_key(KMA)
    with pytest.raises(WeatherError):
        await w.save_key(OPEN_METEO, "anything-123")


# ---------------------------------------------------------------------------------------------------------- parsing
async def test_open_meteo_forecast_with_yesterday_and_air():
    store, fake, w = make()
    fake.forecast = open_meteo(rain_from=15)
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.provider == OPEN_METEO and fc.yesterday.high == 12.0 and fc.day("2026-11-09").high == 11.0
    assert fc.air and fc.air[0].pm2_5 == 12.0
    assert all("open-meteo.com" in u for u in fake.calls)
    n = len(fake.calls)
    await w.forecast(SEOUL_PLACE)
    assert len(fake.calls) == n                                         # kept for a while: no second call


async def test_kma_forecast_rain_at_15_and_its_highs():
    store, fake, w = make()
    await w.save_key(KMA, "good-kma-key-123")
    fake.calls.clear()
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.provider == KMA and fc.day("2026-11-09").high == 10.0 and fc.day("2026-11-09").low == 2.0
    url = next(u for u in fake.calls if "data.go.kr" in u)
    assert "nx=60" in url and "ny=127" in url and "base_time=0500" in url
    notes = notable(fc, ts(2026, 11, 9, 7, 0))
    assert notes[0]["kind"] == "rain_start" and notes[0]["at"] == ts(2026, 11, 9, 15, 0)


async def test_a_failing_provider_falls_back_to_open_meteo():
    store, fake, w = make()
    await w.save_key(KMA, "good-kma-key-123")
    fake.kma = "server error"
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.provider == OPEN_METEO and fc.fallback_from == KMA


async def test_weatherapi_forecast():
    store, fake, w = make(ts(2026, 11, 9, 7, 0, tz=ZoneInfo("Europe/London")))
    fake.wapi = wapi_day()
    await w.save_key(WEATHERAPI, "good-wapi-key-123")
    w.update(provider=WEATHERAPI)
    london = Place("London", 51.5, -0.12, "GB", timezone="Europe/London")
    fc = await w.forecast(london)
    assert fc.provider == WEATHERAPI and fc.day("2026-11-09").high == 11.0 and fc.air[0].pm2_5 == 8.0
    notes = notable(fc, ts(2026, 11, 9, 7, 0, tz=ZoneInfo("Europe/London")))
    assert notes[0]["kind"] == "rain_start" and notes[0]["at"] == ts(2026, 11, 9, 17, 0, tz=ZoneInfo("Europe/London"))


# ---------------------------------------------------------------------------------------------------------- notable changes
async def notes_for(**kw) -> list[dict]:
    store, fake, w = make()
    a = kw.pop("air_pm", None)
    fake.forecast = open_meteo(**kw)
    if a is not None:
        fake.air = air(pm=a)
    fc = await w.forecast(SEOUL_PLACE)
    return notable(fc, ts(2026, 11, 9, 7, 0))


async def test_rain_starting_at_15():
    notes = await notes_for(rain_from=15, rain_to=19)
    assert notes == [{"kind": "rain_start", "at": ts(2026, 11, 9, 15, 0), "until": ts(2026, 11, 9, 19, 0), "prob": 80}]


async def test_rain_now_and_until_when():
    notes = await notes_for(rain_from=0, rain_to=10)
    assert notes[0]["kind"] == "rain_now" and notes[0]["until"] == ts(2026, 11, 9, 10, 0)


async def test_snow_in_the_evening():
    notes = await notes_for(snow_from=19, highs=(3.0, 2.0, 1.0), lows=(-2.0, -3.0, -4.0))
    assert notes[0]["kind"] == "snow_start" and notes[0]["at"] == ts(2026, 11, 9, 19, 0)


async def test_a_big_drop_from_yesterday_and_strong_wind_and_poor_air():
    notes = await notes_for(highs=(15.0, 6.0, 5.0), lows=(8.0, 1.0, 0.0), gust=62.0, air_pm=48)
    kinds = [n["kind"] for n in notes]
    assert kinds == ["temp_drop", "wind", "air"] and notes[0]["delta"] == 9.0 and notes[2]["pm2_5"] == 48


async def test_an_ordinary_day_has_nothing_notable():
    assert await notes_for() == []


async def test_heat_and_cold():
    assert [n["kind"] for n in await notes_for(highs=(33.0, 34.0, 33.0), lows=(25.0, 26.0, 25.0))] == ["heat"]
    assert [n["kind"] for n in await notes_for(highs=(-6.0, -5.0, -4.0), lows=(-12.0, -11.0, -10.0))] == ["cold"]


async def test_a_drop_compares_with_the_recorded_day_when_the_provider_has_no_yesterday():
    store, fake, w = make()
    await w.save_key(KMA, "good-kma-key-123")
    store.set_meta("weather:days", '{"37.57,126.98": {"2026-11-08": [17.0, 6.0]}}')
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.yesterday == Day("2026-11-08", 17.0, 6.0)
    assert notable(fc, ts(2026, 11, 9, 7, 0))[1]["kind"] == "temp_drop"


# ---------------------------------------------------------------------------------------------------------- places
async def test_place_search_puts_towns_first():
    store, fake, w = make()
    found = await w.geocode("부산")
    assert found[0].name == "부산광역시" and found[0].country_code == "KR"
    assert "language=ko" in fake.calls[-1]
    with pytest.raises(WeatherError):
        await w.geocode("x")
    assert units_for("US") == {"temp": "F", "wind": "mph"} and units_for("KR")["wind"] == "m/s"


def test_the_weather_is_where_a_phone_last_was():
    store, fake, w = make()
    assert w.current_place() is None and w.public()["here"] is None          # no phone sent one yet: no weather
    w.set_here(37.50, 127.03, "강남구")                                       # always taken: there is no switch
    assert w.current_place().name == "강남구" and w.current_place().source == "phone"
    fake_now = w.clock()
    got = w.set_here(35.1796, 129.0756, "부산")                               # another phone (or the same one, later): the newest wins
    assert got["lat"] == 35.1796 and got["lon"] == 129.0756 and w.current_place().name == "부산"
    assert w.public()["here"]["at"] == fake_now and "home" not in w.public() and "use_phone_location" not in w.public()
    with pytest.raises(WeatherError):
        w.set_here(200, 0)


async def test_my_location_tells_the_agent_the_last_known_place():
    store, fake, w = make()
    tool = w.location_tool()
    assert tool.name == "my_location" and tool.read_only
    assert (await tool.handler({})).startswith("No location is known yet")
    w.set_here(37.498123, 127.027612, "강남구", "kr", accuracy=35.4)        # precise when the owner allowed it, with its accuracy
    assert (w.current_place().lat, w.current_place().lon) == (37.5, 127.03)   # a weather service only gets ~1 km
    w.clock = lambda: ts(2026, 11, 9, 9, 0)
    out = await tool.handler({})
    assert "37.49812, 127.02761" in out and "about 35 m" in out and "2 hours ago" in out and "강남구, KR" in out
    w.set_here(37.5, 127.0)                                                   # approximate, no accuracy given: still works
    assert "accurate" not in await tool.handler({}) and w.public()["here"]["accuracy_m"] is None


def test_an_older_servers_home_area_is_dropped():
    store, fake, w = make()
    store.set_meta("weather:home", '{"name": "서울특별시", "lat": 37.566, "lon": 126.978, "country_code": "KR"}')
    store.set_meta("weather:config", '{"provider": "kma", "use_phone_location": false}')
    w2 = Weather(store, fetcher=fake, clock=w.clock)
    assert not store.meta("weather:home") and w2.current_place() is None
    assert w2.config() == {"provider": "kma"}


# ---------------------------------------------------------------------------------------------------------- the agent's tool
async def test_the_weather_tool_answers_without_a_browser():
    store, fake, w = make()
    fake.forecast = open_meteo(rain_from=15, rain_to=18)
    tool = w.tool()
    out = await tool.handler({})
    assert "not known yet" in out
    w.set_here(SEOUL_PLACE.lat, SEOUL_PLACE.lon, SEOUL_PLACE.name, "KR")
    out = await tool.handler({"days": 2})
    assert "서울특별시" in out and "high 11°C" in out and "rain at 15:00–17:00" in out and "Weather data by Open-Meteo.com" in out
    out = await tool.handler({"place": "부산", "days": 1})
    assert "부산광역시" in out
    assert tool.read_only and all(u.split("/")[2].endswith(("open-meteo.com", "data.go.kr", "weatherapi.com")) for u in fake.calls)


async def test_the_tool_reaches_the_whole_forecast_range_and_past_values_for_a_far_date():
    """More capability, no rules: up to 16 days of forecast (Open-Meteo's range, past the 7 Kelo used to cap), and for a date past the
    forecast the values recorded around that date in each of the last 10 years, labelled as past values, not a forecast."""
    store, fake, w = make()
    tool = w.tool()
    assert "16 days" in tool.description and "last 10 years" in tool.description
    assert not any(word in tool.description.lower() for word in ("never", "always", "must", "should"))   # capability, no rules
    w.set_here(SEOUL_PLACE.lat, SEOUL_PLACE.lon, SEOUL_PLACE.name, "KR")
    await tool.handler({"days": 16})
    assert "forecast_days=16" in fake.calls[-2] or any("forecast_days=16" in u for u in fake.calls)
    out = await tool.handler({"place": "부산", "date": "2026-11-29"})   # 20 days away: past the forecast
    q = fake.archive_queries[-1]
    assert q["start_date"] == "2016-11-26" and q["end_date"] == "2025-12-02"   # 11-29 +-3 days in 2016..2025, one request
    assert "Typical values (not a forecast)" in out and "2016–2025" in out and "70 days" in out
    assert "past the forecast range" in out and "20 days away" in out and "historical weather" in out
    assert "Recorded on" not in out
    out = await tool.handler({"date": "2026-11-12"})                    # inside the range: the forecast itself
    assert "Typical values" not in out and "2026-11-09" in out
    out = await tool.handler({"date": "2025-11-29"})                    # a past date: that day as recorded, and the typical values
    assert "a past date" in out and "Recorded on 2025-11-29" in out
    assert (await tool.handler({"date": "next week"})).startswith("Error")


# ---------------------------------------------------------------------------------------------------------- MET Norway (the last fallback)
async def test_open_meteo_down_means_met_norway_answers_with_its_credit_and_a_generic_user_agent():
    store, fake, w = make()
    fake.down.add("api.open-meteo.com")
    fake.met = met_norway(rain_from=15)
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.provider == METNO and fc.fallback_from == OPEN_METEO and "could not resolve" in fc.note
    day = fc.day("2026-11-09")
    assert day.high == 12.0 and day.low == 3.0 and day.sky in {"clear", "partly", "rain"}
    assert any(h.precip_kind == "rain" and h.wet() for h in fc.hours) and fc.hours[0].wind_kmh == 10.8
    assert fc.attribution() == ("Weather data from MET Norway", "https://api.met.no/")
    text = describe(fc, ts(2026, 11, 9, 7, 0))
    assert "MET Norway" in text and "Open-Meteo failed" in text and "CC BY 4.0" in text
    i = next(n for n, u in enumerate(fake.calls) if "api.met.no" in u)
    assert fake.calls[i].startswith("https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=37.5660&lon=126.9780")
    ua = fake.headers[i]["User-Agent"]
    assert ua.startswith("Kelo/") and ua.endswith("(+https://kelo.chat)")
    assert METNO not in [p["id"] for p in w.public()["providers"]] and METNO not in ("auto", OPEN_METEO, KMA, WEATHERAPI)


async def test_a_keyed_service_and_open_meteo_both_down_still_get_met_norway():
    store, fake, w = make()
    await w.save_key(KMA, "good-kma-key-123")
    fake.kma = "server error"
    fake.down.add("api.open-meteo.com")
    fc = await w.forecast(SEOUL_PLACE)
    assert fc.provider == METNO and fc.fallback_from == KMA and fc.day("2026-11-09").high == 12.0


async def test_when_every_service_is_down_a_young_forecast_is_used_and_an_old_one_is_not():
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    fake = FakeWeather()
    clock = {"t": ts(2026, 11, 9, 7, 0)}
    w = Weather(store, fetcher=fake, clock=lambda: clock["t"], language=lambda: "ko")
    first = await w.forecast(SEOUL_PLACE)                       # fetched at 07:00 and kept
    clock["t"] += 30 * 60                                        # past the 20-minute cache, within an hour
    fake.down |= {"api.open-meteo.com", "api.met.no"}
    assert await w.forecast(SEOUL_PLACE, stale_seconds=3600) is first
    clock["t"] += 3600                                           # now older than stale_seconds
    with pytest.raises(WeatherError):
        await w.forecast(SEOUL_PLACE, stale_seconds=3600)
    with pytest.raises(WeatherError):
        await w.forecast(SEOUL_PLACE)                            # no stale allowance at all


async def test_with_every_service_down_and_nothing_kept_the_error_is_raised():
    store, fake, w = make()
    fake.down |= {"api.open-meteo.com", "api.met.no"}
    with pytest.raises(WeatherError):
        await w.forecast(SEOUL_PLACE)

