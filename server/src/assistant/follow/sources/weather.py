"""Weather check source: the forecast for the item's place at the item's hour (no key, no card).

The forecast comes from the shared weather service (weather.py `Weather.forecast`), so a follow-up gets the same provider chain as the
morning briefing: the owner's chosen service -> Open-Meteo -> MET Norway -> a forecast from the last 12 hours. The place lookup is
Open-Meteo's geocoding (`geocode_places`), through the server's own HTTP client; nothing is shown to the model. The place is the item's free text, looked up by the provider; when the whole text finds
nothing, its comma-separated parts are tried from the last (a street address ends in the town). Refs: {"place": text, "at": epoch
seconds or an ISO 8601 time with offset}. Fields: rain_chance (percent, 0-100), precip_mm, temperature (°C, whole degrees),
condition (rain | snow | sleet | thunder | fog | cloudy | partly | clear), warning (comma-joined codes: wind, heat, cold, thunder).

Open-Meteo has no official warning feed; a warning is derived from numbers (gusts 50 km/h or wind 36 km/h, a day high of 33 °C or
more, a day low of -10 °C or less, a thunderstorm code), the same numbers the briefing's weather lines use.

A forecast moves a little with every update, so only a real change is told (`significant`): the chance of rain by 20 points or more,
the temperature by 3 °C or more, a new condition or warning. Smaller moves are not stored, so slow drift adds up to one message.
Calls are counted in follow_usage ("weather_open_meteo", per month) for the operator's usage line; Open-Meteo's free terms allow far
more than a server's checks need (see weather.py).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, ClassVar

from ...netguard import Reply, fetch
from ...netguard import NetGuardError
from ...weather import CACHE_SECONDS, Forecast, Hour, MetNorway, OpenMeteo, Place, Weather, WeatherError, geocode_places
from ..checks import Reading, Source

USAGE_NAME = "weather_open_meteo"
RAIN_STEP = 20        # points of rain chance that count as a change
TEMP_STEP = 3         # degrees Celsius that count as a change
GUST_KMH, WIND_KMH = 50, 36
HEAT_C, COLD_C = 33, -10
FORECAST_DAYS = 16    # Open-Meteo's whole range
FOLLOW_STALE_SECONDS = 12 * 3600   # when every forecast service is down, the last forecast this young still answers a check
Geocoder = Callable[[str], Awaitable[list[Place]]]


def parse_at(value: Any) -> float | None:
    """The hour a check is about: epoch seconds, or an ISO 8601 time with an offset. None when it is neither."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.timestamp() if dt.tzinfo else None


def place_candidates(text: str) -> list[str]:
    """What to ask the place lookup, best first: the whole text, then its comma-separated parts from the last."""
    whole = " ".join(str(text or "").split())
    parts = [p.strip() for p in whole.split(",") if p.strip()]
    out = [whole] + list(reversed(parts)) if len(parts) > 1 else [whole]
    return [c for i, c in enumerate(out) if c and c not in out[:i]]


class WeatherSource(Source):
    kind: ClassVar[str] = "weather"
    default_fields: ClassVar[tuple[str, ...]] = ("rain_chance", "temperature", "condition", "warning")

    def __init__(self, fetcher: Callable[..., Awaitable[Reply]] = fetch,
                 geocode: Geocoder | None = None,
                 language: Callable[[], str] = lambda: "en",
                 follow_store: Any = None,
                 clock: Callable[[], float] = time.time,
                 weather: Weather | None = None) -> None:
        self.fetcher = fetcher
        self.weather = weather   # the shared service (the server passes its own); without one, Open-Meteo then MET Norway directly
        self._geocode = geocode
        self.language = language
        self.follow_store = follow_store
        self.clock = clock
        self._places: dict[str, Place | None] = {}
        self._forecasts: dict[str, tuple[float, Forecast]] = {}

    def available(self) -> bool:
        return True   # free and keyless

    def covers(self, refs: dict[str, str]) -> bool:
        return bool(str(refs.get("place") or "").strip()) and parse_at(refs.get("at")) is not None

    def _month(self) -> str:
        return time.strftime("%Y-%m", time.gmtime(self.clock()))

    def _count(self) -> None:
        if self.follow_store is not None and hasattr(self.follow_store, "count_usage"):
            self.follow_store.count_usage(USAGE_NAME, self._month())

    def usage(self) -> dict[str, Any] | None:
        if self.follow_store is None or not hasattr(self.follow_store, "get_usage"):
            return None
        return {"source": USAGE_NAME, "month": self._month(), "used": self.follow_store.get_usage(USAGE_NAME, self._month()),
                "allowance": None, "stop_at": None, "paused": False}

    async def _lookup(self, text: str) -> Place | None:
        key = " ".join(text.lower().split())
        if key in self._places:
            return self._places[key]
        place: Place | None = None
        for candidate in place_candidates(text):
            self._count()
            if self._geocode is not None:
                found = await self._geocode(candidate)
            else:
                found = await geocode_places(self.fetcher, candidate, self.language() or "en", 1)
            if found:
                place = found[0]
                break
        self._places[key] = place
        return place

    async def _forecast(self, place: Place, now: float) -> Forecast:
        hit = self._forecasts.get(place.key())
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        self._count()
        fc = await self._fetch(place, now)
        self._forecasts[place.key()] = (now, fc)
        return fc

    async def _fetch(self, place: Place, now: float) -> Forecast:
        if self.weather is not None:
            return await self.weather.forecast(place, FORECAST_DAYS, stale_seconds=FOLLOW_STALE_SECONDS)
        try:
            return await OpenMeteo(self.fetcher).forecast(place, FORECAST_DAYS, now)
        except (WeatherError, NetGuardError):
            return await MetNorway(self.fetcher).forecast(place, FORECAST_DAYS, now)

    async def read(self, refs: dict[str, str]) -> Reading:
        now = self.clock()
        at = parse_at(refs.get("at"))
        text = str(refs.get("place") or "").strip()
        if at is None or not text:
            return Reading(fields={}, at=now, ok=False, detail="weather check needs a place and a time")
        try:
            place = await self._lookup(text)
            if place is None:
                return Reading(fields={}, at=now, ok=False, detail=f"no place found for {text[:80]!r}")
            fc = await self._forecast(place, now)
        except Exception as e:   # WeatherError, NetGuardError, a bad body: one failed reading, the runner retries
            return Reading(fields={}, at=now, ok=False, detail=str(e)[:200])
        hour = next((h for h in fc.hours if h.ts <= at < h.ts + 3600), None)
        if hour is None:
            return Reading(fields={}, at=now, ok=False, detail="no forecast for that hour yet")
        return Reading(fields=self._fields(fc, hour, at), at=now, ok=True, detail=place.label())

    @staticmethod
    def _fields(fc: Forecast, hour: Hour, at: float) -> dict[str, Any]:
        tz = fc.tz()
        date = datetime.fromtimestamp(at, tz).date().isoformat()
        day = fc.day(date)
        warnings: list[str] = []
        if (hour.gust_kmh or 0) >= GUST_KMH or (hour.wind_kmh or 0) >= WIND_KMH:
            warnings.append("wind")
        if day and day.high is not None and day.high >= HEAT_C:
            warnings.append("heat")
        if day and day.low is not None and day.low <= COLD_C:
            warnings.append("cold")
        if hour.sky == "thunder":
            warnings.append("thunder")
        condition = hour.precip_kind if hour.wet() else (hour.sky or "")
        fields: dict[str, Any] = {
            "rain_chance": int(round(hour.prob)) if hour.prob is not None else 0,
            "precip_mm": round(hour.precip_mm, 1),
            "condition": condition,
            "warning": ",".join(warnings),
        }
        if hour.temp is not None:
            fields["temperature"] = int(round(hour.temp))
        return fields

    def significant(self, field: str, old: Any, new: Any) -> bool:
        try:
            if field == "rain_chance":
                return abs(float(new) - float(old)) >= RAIN_STEP
            if field == "temperature":
                return abs(float(new) - float(old)) >= TEMP_STEP
        except (TypeError, ValueError):
            return True
        if field == "precip_mm":
            return False   # the amount follows the chance and the condition; it is shown, never told alone
        return True
