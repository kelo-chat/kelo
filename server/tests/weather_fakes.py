"""Fake weather services for the tests (no network): Open-Meteo forecast / air quality / geocoding, the KMA short-term forecast and
WeatherAPI.com, answering from data the test sets."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from assistant.netguard import NetGuardError, Reply

SEOUL = ZoneInfo("Asia/Seoul")


def ts(*a: int, tz: ZoneInfo = SEOUL) -> float:
    return datetime(*a, tzinfo=tz).timestamp()


def open_meteo(day: tuple[int, int, int] = (2026, 11, 9), *, tz: ZoneInfo = SEOUL, rain_from: int | None = None, rain_to: int = 24,
               snow_from: int | None = None, highs: tuple[float, float, float] = (12.0, 11.0, 9.0),
               lows: tuple[float, float, float] = (3.0, 4.0, 2.0), gust: float = 15.0, tzname: str = "Asia/Seoul") -> dict[str, Any]:
    """Three days of hourly data from the day before `day` (past_days=1): rain (and/or snow) on `day` from the given hour."""
    start = datetime(*day, tzinfo=tz) - timedelta(days=1)
    h: dict[str, list[Any]] = {k: [] for k in ("time", "temperature_2m", "precipitation", "precipitation_probability", "snowfall",
                                               "wind_speed_10m", "wind_gusts_10m", "weather_code")}
    for i in range(72):
        t = start + timedelta(hours=i)
        today = t.date() == datetime(*day).date()
        wet_rain = today and rain_from is not None and rain_from <= t.hour < rain_to
        wet_snow = today and snow_from is not None and t.hour >= snow_from
        h["time"].append(int(t.timestamp()))
        h["temperature_2m"].append(round(lows[min(i // 24, 2)] + (highs[min(i // 24, 2)] - lows[min(i // 24, 2)]) * (1 if 12 <= t.hour <= 16 else 0.4), 1))
        h["precipitation"].append(1.2 if wet_rain else 0.0)
        h["precipitation_probability"].append(80 if (wet_rain or wet_snow) else 5)
        h["snowfall"].append(0.7 if wet_snow else 0.0)
        h["wind_speed_10m"].append(10.0)
        h["wind_gusts_10m"].append(gust)
        h["weather_code"].append(73 if wet_snow else 63 if wet_rain else 2)
    daily = {"time": [int((start + timedelta(days=i)).timestamp()) for i in range(3)], "temperature_2m_max": list(highs),
             "temperature_2m_min": list(lows), "weather_code": [2, 63 if rain_from is not None else 2, 1]}
    return {"latitude": 37.55, "longitude": 127.0, "timezone": tzname, "utc_offset_seconds": 32400, "hourly": h, "daily": daily}


def air(day: tuple[int, int, int] = (2026, 11, 9), pm: float = 12.0, aqi: float = 40.0, tz: ZoneInfo = SEOUL) -> dict[str, Any]:
    start = datetime(*day, tzinfo=tz)
    times = [int((start + timedelta(hours=i)).timestamp()) for i in range(24)]
    return {"hourly": {"time": times, "pm2_5": [pm] * 24, "pm10": [pm * 1.5] * 24, "us_aqi": [aqi] * 24}}


def kma_items(base_date: str = "20261109", *, rain_hour: int | None = 15, tmx: float = 10.0, tmn: float = 2.0) -> dict[str, Any]:
    items = []
    d = base_date
    for hour in range(6, 24):
        hh = f"{hour:02d}00"
        items += [{"category": "TMP", "fcstDate": d, "fcstTime": hh, "fcstValue": str(3 + hour // 3)},
                  {"category": "POP", "fcstDate": d, "fcstTime": hh, "fcstValue": "70" if rain_hour is not None and hour >= rain_hour else "10"},
                  {"category": "PTY", "fcstDate": d, "fcstTime": hh, "fcstValue": "1" if rain_hour is not None and hour >= rain_hour else "0"},
                  {"category": "PCP", "fcstDate": d, "fcstTime": hh, "fcstValue": "2.0mm" if rain_hour is not None and hour >= rain_hour else "강수없음"},
                  {"category": "SNO", "fcstDate": d, "fcstTime": hh, "fcstValue": "적설없음"},
                  {"category": "SKY", "fcstDate": d, "fcstTime": hh, "fcstValue": "4" if rain_hour is not None and hour >= rain_hour else "1"},
                  {"category": "WSD", "fcstDate": d, "fcstTime": hh, "fcstValue": "3.1"}]
    items += [{"category": "TMN", "fcstDate": d, "fcstTime": "0600", "fcstValue": str(tmn)},
              {"category": "TMX", "fcstDate": d, "fcstTime": "1500", "fcstValue": str(tmx)}]
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                         "body": {"dataType": "JSON", "items": {"item": items}, "totalCount": len(items)}}}


GEO = {"results": [
    {"name": "부산", "latitude": 37.04, "longitude": 128.07, "feature_code": "MT", "country_code": "KR", "admin1": "충청북도", "timezone": "Asia/Seoul"},
    {"name": "부산광역시", "latitude": 35.1796, "longitude": 129.0756, "feature_code": "PPLA", "country_code": "KR", "admin1": "부산광역시",
     "timezone": "Asia/Seoul", "population": 3400000},
]}


class FakeWeather:
    """An async fetcher like netguard.fetch: answers by host; `calls` records every URL (no real network)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.forecast: dict[str, Any] = open_meteo()
        self.air: dict[str, Any] = air()
        self.geo: dict[str, Any] = GEO
        self.kma: dict[str, Any] | str = kma_items()
        self.kma_ok_keys = {"good-kma-key-123"}
        self.wapi: dict[str, Any] = {}
        self.wapi_ok_keys = {"good-wapi-key-123"}
        self.down: set[str] = set()
        self.archive_queries: list[dict[str, str]] = []

    async def __call__(self, method: str, url: str, *, allowed_hosts: list[str], headers: dict[str, str] | None = None, **_: Any) -> Reply:
        self.calls.append(url)
        parts = urlsplit(url)
        host = parts.hostname or ""
        assert host in allowed_hosts, (host, allowed_hosts)
        assert parts.scheme == "https"
        if host in self.down:
            raise NetGuardError("could not resolve host")
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if host == "api.open-meteo.com":
            return self._json(self.forecast)
        if host == "air-quality-api.open-meteo.com":
            return self._json(self.air)
        if host == "geocoding-api.open-meteo.com":
            return self._json(self.geo)
        if host == "archive-api.open-meteo.com":   # past daily values: every day of the range, highs 10 + (day of the year % 7)
            self.archive_queries.append(q)
            first, last = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            return self._json({"timezone": "Asia/Shanghai", "daily": {
                "time": [d.isoformat() for d in days], "temperature_2m_max": [10.0 + d.timetuple().tm_yday % 7 for d in days],
                "temperature_2m_min": [2.0 for _ in days], "precipitation_sum": [3.0 if d.day % 4 == 0 else 0.0 for d in days]}})
        if host == "apis.data.go.kr":
            if q.get("serviceKey") not in self.kma_ok_keys:
                return Reply(200, {}, b"<OpenAPI_ServiceResponse><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></OpenAPI_ServiceResponse>", False)
            return self._json(self.kma) if isinstance(self.kma, dict) else Reply(500, {}, str(self.kma).encode(), False)
        if host == "api.weatherapi.com":
            if q.get("key") not in self.wapi_ok_keys:
                return Reply(401, {}, json.dumps({"error": {"code": 2006, "message": "API key is invalid."}}).encode(), False)
            return self._json(self.wapi)
        return Reply(404, {}, b"{}", False)

    @staticmethod
    def _json(data: Any) -> Reply:
        return Reply(200, {"content-type": "application/json"}, json.dumps(data, ensure_ascii=False).encode(), False)


def wapi_day(day: tuple[int, int, int] = (2026, 11, 9), tz: ZoneInfo = ZoneInfo("Europe/London")) -> dict[str, Any]:
    start = datetime(*day, tzinfo=tz)
    hours = [{"time_epoch": int((start + timedelta(hours=i)).timestamp()), "temp_c": 8.0, "precip_mm": 0.0, "chance_of_rain": 0,
              "chance_of_snow": 0, "snow_cm": 0.0, "wind_kph": 12.0, "gust_kph": 20.0, "condition": {"code": 1003}} for i in range(24)]
    for h in hours[17:]:
        h.update(precip_mm=0.8, chance_of_rain=85, condition={"code": 1183})
    return {"location": {"tz_id": "Europe/London"},
            "current": {"air_quality": {"pm2_5": 8.0, "pm10": 12.0, "us-epa-index": 1}},
            "forecast": {"forecastday": [{"date": f"{day[0]}-{day[1]:02d}-{day[2]:02d}",
                                          "day": {"maxtemp_c": 11.0, "mintemp_c": 5.0, "condition": {"code": 1063}}, "hour": hours}]}}
