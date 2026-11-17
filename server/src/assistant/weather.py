"""Weather without a browser: forecasts straight from a weather service, for the morning briefing (briefing.py) and the agent's
`weather` tool.

Providers (one interface: `forecast(place, days)` -> Forecast; each server calls them itself, with the owner's own keys if any; there is
no key shared by all Kelo servers and none is built in):
- Open-Meteo (default, no key): https://api.open-meteo.com/v1/forecast (hourly temperature_2m, precipitation, precipitation_probability,
  snowfall, wind_speed_10m, wind_gusts_10m, weather_code; daily max/min; timezone=auto; past_days=1 for yesterday), the air-quality API
  https://air-quality-api.open-meteo.com/v1/air-quality (hourly pm10, pm2_5, us_aqi) and the geocoding API
  https://geocoding-api.open-meteo.com/v1/search (place names in any language; full names work best, e.g. "서울특별시", "Busan").
  Terms (open-meteo.com/en/terms, checked 2026-11-09): free for non-commercial use, fewer than 10,000 calls a day, 5,000 an hour, 600 a
  minute, counted per IP address; commercial use needs a paid plan. Each Kelo server is its owner's own personal assistant, run and
  used by that person (personal use, like a home automation setup), and calls the API from its own address. The data are CC BY 4.0:
  wherever it is shown, credit "Weather data by Open-Meteo.com" with a link to https://open-meteo.com/ (ATTRIBUTION below; the
  briefing card shows it under the weather, the tool's answer carries it).
  Calls here: at most one forecast and one air-quality request per place per CACHE_SECONDS, plus a geocoding request when the agent's
  tool is asked about a place by name. Forecasts go up to 16 days ahead (Open-Meteo's whole range; the briefing asks for 2).
  Typical values for a date (the agent's tool, `date` past the forecast): Open-Meteo's historical weather API
  https://archive-api.open-meteo.com/v1/archive (daily max/min and precipitation, the same terms), the days around that date in each of
  the last TYPICAL_YEARS years, averaged in one request per place and date (cached a day); always labelled as past values, not a forecast.
- Korea Meteorological Administration short-term forecast (기상청 단기예보, data.go.kr VilageFcstInfoService_2.0/getVilageFcst) with the
  owner's own data.go.kr service key (free; a development account allows 10,000 calls a day; KOGL type 1: credit the source, "기상청").
  Used when that key is saved and the place is in Korea. Forecast on the KMA 5 km grid (lat/lon -> nx/ny: kma_grid, the KMA's Lambert
  conformal conic formula), issued at 02, 05, 08, 11, 14, 17, 20, 23 h KST and available about 10 minutes later; about three days ahead.
  No yesterday's numbers and no air quality: those come from the stored day records and Open-Meteo's air-quality API.
- WeatherAPI.com with the owner's own key (free plan: 100,000 calls a month, 3 days of forecast, air quality included, commercial use
  allowed; a link back is appreciated). Used only when the owner picks it.
- MET Norway locationforecast (https://api.met.no/weatherapi/locationforecast/2.0/complete, no key, CC BY 4.0; credit "Weather data from
  MET Norway" with https://api.met.no/). Never picked by hand and never in the providers list: only the last fallback, when Open-Meteo
  cannot be reached either (2026-11-17, the owner's Open-Meteo host timed out on the TLS handshake). Hourly steps for about 2.5 days,
  then daily highs and lows from its 6-hourly points. Identifies the app with a generic User-Agent ("Kelo/<version> (+https://kelo.chat)").
- US National Weather Service (https://api.weather.gov, no key, public domain; credit "Forecast by the US National Weather Service"):
  the default route for a place in the United States when the owner left the provider on auto (2026-11-17). Its /points answers 404
  elsewhere, so it is never asked for another country. Not in the providers list either (NwsForecast, NWS_ICONS).
- Another route to Open-Meteo (2026-11-17): Open-Meteo answers from several servers and DNS gives each network the nearest one. When the
  normal route fails (no answer, a timeout), the forecast is asked once more through another of its servers: alternate_resolver asks
  DNS over HTTPS (dns.google) with two client subnets from other regions (ALT_SUBNETS, only to learn which server those regions get,
  no user address is sent) and keeps the addresses that differ from the normal ones for ALT_SECONDS. netguard still checks that the
  address is public and connects with the service's own host name as SNI, so the certificate is still checked for api.open-meteo.com.
When the picked provider fails (a bad key, the service down), Open-Meteo answers instead and the forecast says so (`fallback_from`);
when Open-Meteo fails too, MET Norway answers; when all fail, a forecast for the same place from `stale_seconds` ago is used, if any.
A US place's chain: NWS -> Open-Meteo -> MET Norway -> the last forecast.

Keys are kept in the vault like any service key (kind "api"; KEYS below: the item names, the only hosts each may go to and where it goes
in the request), checked with one real request before they are saved, and never returned.

Units follow the place's country: °F and mph in the United States (and Liberia and Myanmar), m/s for wind in Korea, Japan, China and
Russia, else °C and km/h.

Where the owner is: the newest location any of the owner's phones sent (POST /api/briefing/location when the app is opened, in the
foreground only; precise or approximate, whichever the owner allowed, with its accuracy; only the newest one kept, only on this
server). There is no home place to set: the
weather is always where the phone last was. With no location at all the briefing leaves the weather out. Servers from before kept a
home area (meta "weather:home") and a use_phone_location switch; both are dropped when the service starts.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import re
import socket
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from .agent import Tool
from .i18n import t
from .netguard import NetGuardError, Reply, fetch
from .version import current as server_release

Fetcher = Callable[..., Awaitable[Reply]]
log = logging.getLogger("uvicorn.error")

OPEN_METEO, KMA, WEATHERAPI = "open_meteo", "kma", "weatherapi"
PROVIDERS = (OPEN_METEO, KMA, WEATHERAPI)          # the choices the owner can make (METNO and NWS are not among them)
CHOICES = ("auto", *PROVIDERS)
METNO = "met_no"                                    # the last fallback (no key); see MetNorway
NWS = "nws"                                         # the US National Weather Service, the default for US places; see NwsForecast
FORECAST_HOST, AIR_HOST, GEO_HOST = "api.open-meteo.com", "air-quality-api.open-meteo.com", "geocoding-api.open-meteo.com"
ARCHIVE_HOST = "archive-api.open-meteo.com"
METNO_HOST = "api.met.no"
METNO_URL = f"https://{METNO_HOST}/weatherapi/locationforecast/2.0/complete"
NWS_HOST = "api.weather.gov"
KMA_HOST, WEATHERAPI_HOST = "apis.data.go.kr", "api.weatherapi.com"
KMA_URL = f"https://{KMA_HOST}/1360000/VilageFcstInfoService_2.0/getVilageFcst"
ALT_DNS = "dns.google"                              # DNS over HTTPS, asked for the other servers of Open-Meteo (alternate_resolver)
ALT_SUBNETS = ("85.10.0.0/24", "8.8.8.0/24")         # one European, one North American network prefix (never the owner's address)
ALT_SECONDS = 30 * 60
_NORMAL_DOWN_UNTIL: dict[str, float] = {}             # host -> until when its normal route is taken as down (asked second)
ATTRIBUTION = {OPEN_METEO: ("Weather data by Open-Meteo.com", "https://open-meteo.com/"),
               KMA: ("기상청 (Korea Meteorological Administration)", "https://www.data.go.kr/data/15084084/openapi.do"),
               WEATHERAPI: ("Powered by WeatherAPI.com", "https://www.weatherapi.com/"),
               METNO: ("Weather data from MET Norway", "https://api.met.no/"),
               NWS: ("Forecast by the US National Weather Service", "https://www.weather.gov/")}
NAMES = {OPEN_METEO: "Open-Meteo", KMA: "기상청 (KMA)", WEATHERAPI: "WeatherAPI.com", METNO: "MET Norway", NWS: "US National Weather Service"}
MAX_DAYS = {OPEN_METEO: 16, KMA: 3, WEATHERAPI: 3, METNO: 9, NWS: 7}
TYPICAL_YEARS, TYPICAL_SPAN = 10, 3   # typical values: that date +-3 days in each of the last 10 years
ARCHIVE_LAG_DAYS = 6                  # the historical API has about the last five days missing
CACHE_SECONDS = 20 * 60
CONFIG_META, HERE_META, DAYS_META = "weather:config", "weather:here", "weather:days"
OLD_HOME_META = "weather:home"  # the home area older servers kept; dropped at start


@dataclass(frozen=True)
class KeySpec:
    item: str
    host: str
    param: str
    label: str


KEYS = {KMA: KeySpec("kma-weather-api", KMA_HOST, "serviceKey", "기상청 단기예보 (data.go.kr)"),
        WEATHERAPI: KeySpec("weatherapi-api", WEATHERAPI_HOST, "key", "WeatherAPI.com")}


class WeatherError(Exception):
    """Shown to the user (or the model) as it is."""


# ---------------------------------------------------------------------------------------------------------------- data
@dataclass
class Place:
    name: str
    lat: float
    lon: float
    country_code: str = ""
    admin1: str = ""
    timezone: str = ""
    source: str = "phone"           # phone | search

    def key(self) -> str:
        return f"{self.lat:.2f},{self.lon:.2f}"

    def label(self) -> str:
        bits = [self.name]
        if self.admin1 and self.admin1 != self.name:
            bits.append(self.admin1)
        return ", ".join(bits)


@dataclass
class Hour:
    ts: float
    temp: float | None = None
    precip_mm: float = 0.0
    prob: float | None = None         # chance of precipitation, %
    snow_cm: float = 0.0
    wind_kmh: float | None = None
    gust_kmh: float | None = None
    precip_kind: str = ""             # "" | rain | snow | sleet
    sky: str = ""                     # clear | partly | cloudy | fog | thunder | ""

    def wet(self) -> bool:
        return bool(self.precip_kind) and (self.precip_mm >= 0.2 or self.snow_cm >= 0.1 or (self.prob or 0) >= 60)


@dataclass
class Day:
    date: str
    high: float | None
    low: float | None
    sky: str = ""


@dataclass
class Air:
    ts: float
    pm2_5: float | None = None
    pm10: float | None = None
    us_aqi: float | None = None


@dataclass
class Forecast:
    place: Place
    provider: str
    timezone: str
    hours: list[Hour] = field(default_factory=list)
    days: list[Day] = field(default_factory=list)
    yesterday: Day | None = None
    air: list[Air] = field(default_factory=list)
    fallback_from: str = ""
    note: str = ""

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone or self.place.timezone or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def day(self, date: str) -> Day | None:
        return next((d for d in self.days if d.date == date), None)

    def attribution(self) -> tuple[str, str]:
        return ATTRIBUTION[self.provider]


# ---------------------------------------------------------------------------------------------------------------- helpers
_WMO_RAIN = {51, 53, 55, 61, 63, 65, 80, 81, 82}
_WMO_SLEET = {56, 57, 66, 67}
_WMO_SNOW = {71, 73, 75, 77, 85, 86}
_WMO_THUNDER = {95, 96, 99}


def wmo_sky(code: int | None) -> str:
    if code is None:
        return ""
    if code == 0 or code == 1:
        return "clear"
    if code == 2:
        return "partly"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in _WMO_THUNDER:
        return "thunder"
    if code in _WMO_SNOW:
        return "snow"
    if code in _WMO_SLEET:
        return "sleet"
    if code in _WMO_RAIN:
        return "rain"
    return ""


def wmo_kind(code: int | None, precip: float, snow: float) -> str:
    if snow > 0 or (code in _WMO_SNOW):
        return "snow"
    if code in _WMO_SLEET:
        return "sleet"
    if precip > 0 or code in _WMO_RAIN or code in _WMO_THUNDER:
        return "rain"
    return ""


def in_korea(place: Place) -> bool:
    if place.country_code:
        return place.country_code.upper() == "KR"
    return 33.0 <= place.lat <= 38.7 and 124.5 <= place.lon <= 131.9


def units_for(country: str) -> dict[str, str]:
    cc = (country or "").upper()
    if cc in ("US", "LR", "MM"):
        return {"temp": "F", "wind": "mph"}
    if cc in ("KR", "JP", "CN", "RU", "KP", "TW", "MN"):
        return {"temp": "C", "wind": "m/s"}
    return {"temp": "C", "wind": "km/h"}


def temp_in(c: float | None, unit: str) -> int | None:
    if c is None:
        return None
    return round(c * 9 / 5 + 32) if unit == "F" else round(c)


def wind_in(kmh: float | None, unit: str) -> int | None:
    if kmh is None:
        return None
    return round(kmh / 1.609) if unit == "mph" else round(kmh / 3.6) if unit == "m/s" else round(kmh)


def kma_grid(lat: float, lon: float) -> tuple[int, int]:
    """The KMA 5 km forecast grid point (nx, ny) of a latitude/longitude (Lambert conformal conic, the KMA's own constants)."""
    re_, grid, slat1, slat2, olon, olat, xo, yo = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    deg = math.pi / 180.0
    r = re_ / grid
    s1, s2, ol, oa = slat1 * deg, slat2 * deg, olon * deg, olat * deg
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(math.tan(math.pi * 0.25 + s2 * 0.5) / math.tan(math.pi * 0.25 + s1 * 0.5))
    sf = math.pow(math.tan(math.pi * 0.25 + s1 * 0.5), sn) * math.cos(s1) / sn
    ro = r * sf / math.pow(math.tan(math.pi * 0.25 + oa * 0.5), sn)
    ra = r * sf / math.pow(math.tan(math.pi * 0.25 + lat * deg * 0.5), sn)
    theta = lon * deg - ol
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn
    return int(math.floor(ra * math.sin(theta) + xo + 0.5)), int(math.floor(ro - ra * math.cos(theta) + yo + 0.5))


def kma_base(now: float) -> tuple[str, str]:
    """(base_date, base_time) of the newest short-term forecast out at `now` (issued every 3 h from 02:00 KST, out ~10 min later)."""
    kst = datetime.fromtimestamp(now, ZoneInfo("Asia/Seoul")) - timedelta(minutes=10)
    hours = [h for h in (2, 5, 8, 11, 14, 17, 20, 23) if h <= kst.hour]
    if not hours:
        kst -= timedelta(days=1)
        hour = 23
    else:
        hour = hours[-1]
    return kst.strftime("%Y%m%d"), f"{hour:02d}00"


def _kma_amount(text: str) -> float:
    """PCP / SNO values: "강수없음", "적설없음", "1mm 미만", "1.0mm", "30.0~50.0mm", "50.0mm 이상", "5.0cm"."""
    t = (text or "").strip()
    if not t or "없음" in t or t in ("0", "-"):
        return 0.0
    if "미만" in t:
        return 0.5
    num = "".join(c if (c.isdigit() or c in ".~") else " " for c in t).split()
    if not num:
        return 0.0
    try:
        return float(num[0].split("~")[0])
    except ValueError:
        return 0.0


def _json(r: Reply, what: str) -> Any:
    try:
        return json.loads(r.body)
    except ValueError:
        raise WeatherError(f"{what} did not answer with data (HTTP {r.status})") from None


async def geocode_places(fetcher: Fetcher, name: str, language: str = "en", count: int = 5) -> list[Place]:
    """Places for a name (Open-Meteo geocoding, any language), towns and regions first, then by population."""
    q = (name or "").strip()
    if not 2 <= len(q) <= 100:
        raise WeatherError(t("Type a place name (2 to 100 characters)."))
    lang = (language or "en").split("-")[0]
    url = f"https://{GEO_HOST}/v1/search?" + urlencode({"name": q, "count": 20, "language": lang, "format": "json"})
    try:
        r = await fetcher("GET", url, allowed_hosts=[GEO_HOST], headers={"Accept": "application/json"})
    except NetGuardError as e:
        raise WeatherError(t("Could not look the place up: {error}", error=e)) from None
    data = _json(r, "Open-Meteo geocoding")
    rows = (data or {}).get("results") or [] if isinstance(data, dict) else []

    def rank(x: dict[str, Any]) -> tuple[int, float]:
        fc = str(x.get("feature_code") or "")
        return (0 if fc.startswith(("PPL", "ADM")) else 1, -float(x.get("population") or 0))
    out = []
    for x in sorted(rows, key=rank)[:count]:
        try:
            out.append(Place(str(x.get("name") or q)[:80], float(x["latitude"]), float(x["longitude"]), str(x.get("country_code") or ""),
                             str(x.get("admin1") or "")[:80], str(x.get("timezone") or ""), "search"))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ------------------------------------------------------------------------------------------------------- another route
# Open-Meteo answers from several servers and DNS gives each network the nearest one. On 2026-11-17 the nearest server never answered
# from the owner's network (TLS timed out, for any name), while the same name answered from a server found through another region's
# client subnet. So a failed request is asked once more through that other server. The name, the certificate check and the SNI stay the
# service's own: only the address changes, and netguard still refuses any address that is not public.
async def _local_ips(host: str) -> set[str]:
    """The addresses this server's own DNS gives `host` (the normal route)."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
    except OSError:
        return set()
    return {str(i[4][0]) for i in infos}


async def _dns_answers(host: str, subnet: str, fetcher: Fetcher) -> list[str]:
    """The IPv4 answers DNS over HTTPS gives `host` when asked as if from `subnet` (EDNS client subnet: the region, never the owner)."""
    q = urlencode({"name": host, "type": "A", "edns_client_subnet": subnet})
    r = await fetcher("GET", f"https://{ALT_DNS}/resolve?{q}", allowed_hosts=[ALT_DNS], headers={"Accept": "application/dns-json"})
    if r.status != 200:
        raise WeatherError(f"DNS over HTTPS refused the question (HTTP {r.status})")
    data = _json(r, "DNS over HTTPS")
    answers = data.get("Answer") if isinstance(data, dict) else None
    out: list[str] = []
    for a in answers if isinstance(answers, list) else []:
        if not isinstance(a, dict) or a.get("type") != 1:
            continue
        try:
            out.append(str(ipaddress.IPv4Address(str(a.get("data") or ""))))
        except ValueError:
            continue
    return out


async def alternate_resolver(host: str, fetcher: Fetcher = fetch, *, clock: Callable[[], float] = time.time) -> Callable[..., list] | None:
    """A getaddrinfo-shaped resolver for `host` with the addresses of another server than the normal route's, or None when no other
    address is found. The first client subnet (ALT_SUBNETS) whose answers differ from the normal ones wins; the result is kept for
    ALT_SECONDS. A failed DNS question is not kept, so the next request asks again."""
    now = clock()
    hit = _ALT.get(host)
    if hit is None or now - hit[0] >= ALT_SECONDS:
        normal = await _local_ips(host)
        found: tuple[str, ...] = ()
        asked = False
        for subnet in ALT_SUBNETS:
            try:
                answers = await _dns_answers(host, subnet, fetcher)
            except (NetGuardError, WeatherError):
                continue
            asked = True
            other = tuple(dict.fromkeys(ip for ip in answers if ip not in normal))
            if other:
                found = other
                break
        if not asked:
            return None
        _ALT[host] = hit = (now, found)
    ips = hit[1]
    if not ips:
        return None

    def resolver(name: str, port: int, *_: Any) -> list:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    resolver.ips = ips   # type: ignore[attr-defined]   (for the log line: which addresses answered)
    return resolver


_ALT: dict[str, tuple[float, tuple[str, ...]]] = {}   # host -> (when asked, the other addresses; () = none found)


# ---------------------------------------------------------------------------------------------------------------- providers
class OpenMeteo:
    name = OPEN_METEO

    def __init__(self, fetcher: Fetcher, clock: Callable[[], float] = time.time):
        self.fetcher, self.clock = fetcher, clock

    async def _get(self, url: str, headers: dict[str, str]) -> Reply:
        """The request on the normal route; when that fails (no answer, a timeout, a refused connection), once more through another
        server of Open-Meteo (alternate_resolver). After a failure the other server is asked FIRST for ALT_SECONDS, so a dead normal
        route costs its timeout once, not on every request. Any other error is not retried."""
        tried_other = False
        if _NORMAL_DOWN_UNTIL.get(FORECAST_HOST, 0) > self.clock():
            resolver = await alternate_resolver(FORECAST_HOST, self.fetcher, clock=self.clock)
            if resolver is not None:
                tried_other = True
                try:
                    return await self.fetcher("GET", url, allowed_hosts=[FORECAST_HOST], headers=headers, resolver=resolver)
                except NetGuardError:
                    pass   # the other server failed too: the normal route gets its chance below
        try:
            r = await self.fetcher("GET", url, allowed_hosts=[FORECAST_HOST], headers=headers)
            _NORMAL_DOWN_UNTIL.pop(FORECAST_HOST, None)
            return r
        except NetGuardError as e:
            _NORMAL_DOWN_UNTIL[FORECAST_HOST] = self.clock() + ALT_SECONDS
            if tried_other:
                raise   # both routes were asked once in this request
            resolver = await alternate_resolver(FORECAST_HOST, self.fetcher, clock=self.clock)
            if resolver is None:
                raise
            log.info("Open-Meteo did not answer on the normal route (%s); asking another of its servers", str(e)[:120])
            r = await self.fetcher("GET", url, allowed_hosts=[FORECAST_HOST], headers=headers, resolver=resolver)
            log.info("Open-Meteo answered from another of its servers: %s", ", ".join(resolver.ips))
            return r

    async def forecast(self, place: Place, days: int, now: float) -> Forecast:
        q = urlencode({"latitude": f"{place.lat:.4f}", "longitude": f"{place.lon:.4f}",
                       "hourly": "temperature_2m,precipitation,precipitation_probability,snowfall,wind_speed_10m,wind_gusts_10m,weather_code",
                       "daily": "temperature_2m_max,temperature_2m_min,weather_code", "timezone": "auto", "timeformat": "unixtime",
                       "past_days": 1, "forecast_days": max(1, min(days, MAX_DAYS[OPEN_METEO]))})
        r = await self._get(f"https://{FORECAST_HOST}/v1/forecast?{q}", {"Accept": "application/json"})
        data = _json(r, "Open-Meteo")
        if r.status != 200 or not isinstance(data, dict) or "hourly" not in data:
            raise WeatherError(f"Open-Meteo refused the request (HTTP {r.status}): {str(data.get('reason', '') if isinstance(data, dict) else '')[:120]}")
        tzname = str(data.get("timezone") or place.timezone or "UTC")
        fc = Forecast(place, OPEN_METEO, tzname)
        tz = fc.tz()
        h = data.get("hourly") or {}
        for i, t in enumerate(h.get("time") or []):
            def v(key: str, i: int = i) -> Any:
                arr = h.get(key) or []
                return arr[i] if i < len(arr) else None
            code = v("weather_code")
            precip, snow = float(v("precipitation") or 0), float(v("snowfall") or 0)
            fc.hours.append(Hour(float(t), v("temperature_2m"), precip, v("precipitation_probability"), snow, v("wind_speed_10m"),
                                 v("wind_gusts_10m"), wmo_kind(code, precip, snow), wmo_sky(code)))
        d = data.get("daily") or {}
        today = datetime.fromtimestamp(now, tz).date().isoformat()
        for i, t in enumerate(d.get("time") or []):
            date = datetime.fromtimestamp(float(t), tz).date().isoformat()
            arr = lambda k, i=i: (d.get(k) or [None] * (i + 1))[i] if i < len(d.get(k) or []) else None  # noqa: E731
            day = Day(date, arr("temperature_2m_max"), arr("temperature_2m_min"), wmo_sky(arr("weather_code")))
            if date < today:
                fc.yesterday = day
            else:
                fc.days.append(day)
        return fc


# MET Norway's symbol codes (https://api.met.no/weatherapi/weathericon/2.0/documentation), without their _day / _night /
# _polartwilight suffix -> (Kelo's sky, precipitation kind). A protocol value table: the codes name weather, not words to match.
METNO_SYMBOLS: dict[str, tuple[str, str]] = {
    "clearsky": ("clear", ""), "fair": ("clear", ""), "partlycloudy": ("partly", ""), "cloudy": ("cloudy", ""), "fog": ("fog", ""),
    "lightrain": ("rain", "rain"), "rain": ("rain", "rain"), "heavyrain": ("rain", "rain"),
    "lightrainshowers": ("rain", "rain"), "rainshowers": ("rain", "rain"), "heavyrainshowers": ("rain", "rain"),
    "lightrainandthunder": ("thunder", "rain"), "rainandthunder": ("thunder", "rain"), "heavyrainandthunder": ("thunder", "rain"),
    "lightrainshowersandthunder": ("thunder", "rain"), "rainshowersandthunder": ("thunder", "rain"),
    "heavyrainshowersandthunder": ("thunder", "rain"),
    "lightsleet": ("sleet", "sleet"), "sleet": ("sleet", "sleet"), "heavysleet": ("sleet", "sleet"),
    "lightsleetshowers": ("sleet", "sleet"), "sleetshowers": ("sleet", "sleet"), "heavysleetshowers": ("sleet", "sleet"),
    "lightsleetandthunder": ("thunder", "sleet"), "sleetandthunder": ("thunder", "sleet"), "heavysleetandthunder": ("thunder", "sleet"),
    "lightsleetshowersandthunder": ("thunder", "sleet"), "sleetshowersandthunder": ("thunder", "sleet"),
    "heavysleetshowersandthunder": ("thunder", "sleet"),
    "lightsnow": ("snow", "snow"), "snow": ("snow", "snow"), "heavysnow": ("snow", "snow"),
    "lightsnowshowers": ("snow", "snow"), "snowshowers": ("snow", "snow"), "heavysnowshowers": ("snow", "snow"),
    "lightsnowandthunder": ("thunder", "snow"), "snowandthunder": ("thunder", "snow"), "heavysnowandthunder": ("thunder", "snow"),
    "lightsnowshowersandthunder": ("thunder", "snow"), "snowshowersandthunder": ("thunder", "snow"),
    "heavysnowshowersandthunder": ("thunder", "snow"),
}


class MetNorway:
    """MET Norway's locationforecast (api.met.no, no key, CC BY 4.0): the last fallback when Open-Meteo cannot be reached. Its hourly
    steps cover about the first two and a half days; further days have daily highs and lows only (from its 6-hourly points). It has no
    yesterday and no air quality (the forecast keeps the day records and Open-Meteo's air API, as before)."""
    name = METNO

    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    async def forecast(self, place: Place, days: int, now: float) -> Forecast:
        q = urlencode({"lat": f"{place.lat:.4f}", "lon": f"{place.lon:.4f}"})
        r = await self.fetcher("GET", f"{METNO_URL}?{q}", allowed_hosts=[METNO_HOST],
                               headers={"Accept": "application/json", "User-Agent": _generic_agent()})
        data = _json(r, "MET Norway")
        series = (data.get("properties") or {}).get("timeseries") if isinstance(data, dict) else None
        if r.status != 200 or not isinstance(series, list) or not series:
            raise WeatherError(f"MET Norway refused the request (HTTP {r.status})")
        fc = Forecast(place, METNO, place.timezone or "UTC")
        tz = fc.tz()
        today = datetime.fromtimestamp(now, tz).date().isoformat()
        temps: dict[str, list[float]] = {}
        skies: dict[str, list[str]] = {}
        for item in series:
            try:
                when = datetime.fromisoformat(str(item["time"]).replace("Z", "+00:00")).timestamp()
                data_at = item.get("data") or {}
                inst = (data_at.get("instant") or {}).get("details") or {}
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            date = datetime.fromtimestamp(when, tz).date().isoformat()
            if inst.get("air_temperature") is not None:
                temps.setdefault(date, []).append(float(inst["air_temperature"]))
            hourly = data_at.get("next_1_hours") or {}
            symbol = str((hourly.get("summary") or {}).get("symbol_code") or "") or str(
                ((data_at.get("next_6_hours") or {}).get("summary") or {}).get("symbol_code") or "")
            base = symbol.rsplit("_", 1)[0] if "_" in symbol else symbol
            sky, kind = METNO_SYMBOLS.get(base, ("", ""))
            if sky:
                skies.setdefault(date, []).append(sky)
            if not hourly:
                continue   # the 6-hourly part: its highs and lows count for the day, its amounts are not hours
            details = hourly.get("details") or {}
            precip = float(details.get("precipitation_amount") or 0)
            prob = details.get("probability_of_precipitation")
            wind, gust = inst.get("wind_speed"), inst.get("wind_speed_of_gust")
            kind = kind or ("rain" if precip > 0 else "")
            fc.hours.append(Hour(when, inst.get("air_temperature"), precip, float(prob) if prob is not None else None, 0.0,
                                 float(wind) * 3.6 if wind is not None else None, float(gust) * 3.6 if gust is not None else None,
                                 kind, sky))
        for date in sorted(temps):
            if date < today or len(fc.days) >= max(1, min(days, MAX_DAYS[METNO])):
                continue
            seen = skies.get(date) or []
            fc.days.append(Day(date, max(temps[date]), min(temps[date]), Counter(seen).most_common(1)[0][0] if seen else ""))
        if not fc.days:
            raise WeatherError("MET Norway sent no days to show")
        return fc


def _generic_agent() -> str:
    """The User-Agent of the keyless services that ask for one: a generic identification, no name and no address."""
    return f"Kelo/{server_release().version} (+https://kelo.chat)"


# The US National Weather Service's icon codes (the last path part of its icon URL, before any ",<percent>") -> (Kelo's sky, precipitation
# kind). A protocol value table, as METNO_SYMBOLS.
NWS_ICONS: dict[str, tuple[str, str]] = {
    "skc": ("clear", ""), "hot": ("clear", ""), "cold": ("clear", ""), "wind_skc": ("clear", ""),
    "few": ("partly", ""), "sct": ("partly", ""), "wind_few": ("partly", ""), "wind_sct": ("partly", ""),
    "bkn": ("cloudy", ""), "ovc": ("cloudy", ""), "wind_bkn": ("cloudy", ""), "wind_ovc": ("cloudy", ""),
    "fog": ("fog", ""), "haze": ("fog", ""), "smoke": ("fog", ""), "dust": ("fog", ""),
    "rain": ("rain", "rain"), "rain_showers": ("rain", "rain"), "rain_showers_hi": ("rain", "rain"),
    "tsra": ("thunder", "rain"), "tsra_sct": ("thunder", "rain"), "tsra_hi": ("thunder", "rain"),
    "tornado": ("thunder", ""), "hurricane": ("thunder", ""), "tropical_storm": ("thunder", ""),
    "snow": ("snow", "snow"), "blizzard": ("snow", "snow"),
    "sleet": ("sleet", "sleet"), "rain_sleet": ("sleet", "sleet"), "snow_sleet": ("sleet", "sleet"), "rain_snow": ("sleet", "sleet"),
    "fzra": ("sleet", "sleet"), "rain_fzra": ("sleet", "sleet"), "snow_fzra": ("sleet", "sleet"),
}


def _nws_icon(url: Any) -> tuple[str, str]:
    """(sky, precipitation kind) of an NWS icon URL such as .../icons/land/day/rain_showers,30?size=medium."""
    code = urlsplit(str(url or "")).path.rsplit("/", 1)[-1].split(",", 1)[0]
    return NWS_ICONS.get(code, ("", ""))


def _nws_periods(data: dict[str, Any]) -> list[dict[str, Any]]:
    props = data.get("properties") if isinstance(data, dict) else None
    rows = props.get("periods") if isinstance(props, dict) else None
    return [p for p in rows if isinstance(p, dict)] if isinstance(rows, list) else []


def _nws_temp_c(p: dict[str, Any]) -> float | None:
    v = p.get("temperature")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return (float(v) - 32.0) * 5.0 / 9.0 if str(p.get("temperatureUnit") or "F").upper() == "F" else float(v)


def _nws_wind_kmh(text: Any) -> float | None:
    """NWS wind as text ("5 mph", "5 to 15 mph") -> the stronger figure in km/h."""
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(text or ""))]
    return max(nums) * 1.609344 if nums else None


class NwsForecast:
    """The US National Weather Service (api.weather.gov: no key, public domain, US places only). The default route for a US place when
    the owner left the provider on auto. Hours come from the hourly periods; they carry no rain amounts, so `wet` follows the chance of
    precipitation. Days: the high of the daytime period and the low of the night period that start on that date (the night after it);
    a missing side takes the hourly values of that date."""
    name = NWS

    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    async def _json(self, url: str) -> dict[str, Any]:
        r = await self.fetcher("GET", url, allowed_hosts=[NWS_HOST], headers={"Accept": "application/geo+json", "User-Agent": _generic_agent()})
        data = _json(r, "the US National Weather Service") if r.status == 200 else None
        if r.status != 200 or not isinstance(data, dict):
            raise WeatherError(f"the US National Weather Service refused the request (HTTP {r.status})")
        return data

    async def forecast(self, place: Place, days: int, now: float) -> Forecast:
        points = await self._json(f"https://{NWS_HOST}/points/{place.lat:.4f},{place.lon:.4f}")
        props = points.get("properties") or {}
        if not isinstance(props, dict) or not props.get("forecastHourly") or not props.get("forecast"):
            raise WeatherError("the US National Weather Service has no forecast for this place")
        hourly = await self._json(str(props["forecastHourly"]))
        daily = await self._json(str(props["forecast"]))
        fc = Forecast(place, NWS, str(props.get("timeZone") or place.timezone or "UTC"))
        tz = fc.tz()
        for p in _nws_periods(hourly):
            try:
                when = datetime.fromisoformat(str(p["startTime"])).timestamp()
            except (KeyError, TypeError, ValueError):
                continue
            sky, kind = _nws_icon(p.get("icon"))
            prob = (p.get("probabilityOfPrecipitation") or {}).get("value") if isinstance(p.get("probabilityOfPrecipitation"), dict) else None
            fc.hours.append(Hour(when, _nws_temp_c(p), 0.0, float(prob) if isinstance(prob, (int, float)) else None, 0.0,
                                 _nws_wind_kmh(p.get("windSpeed")), None, kind, sky))
        temps: dict[str, list[float]] = {}
        skies: dict[str, list[str]] = {}
        for h in fc.hours:
            d = datetime.fromtimestamp(h.ts, tz).date().isoformat()
            if h.temp is not None:
                temps.setdefault(d, []).append(h.temp)
            if h.sky:
                skies.setdefault(d, []).append(h.sky)
        highs: dict[str, float] = {}
        lows: dict[str, float] = {}
        day_sky: dict[str, str] = {}
        for p in _nws_periods(daily):
            try:
                d = datetime.fromisoformat(str(p["startTime"])).date().isoformat()
            except (KeyError, TypeError, ValueError):
                continue
            temp = _nws_temp_c(p)
            sky, _ = _nws_icon(p.get("icon"))
            if temp is not None and p.get("isDaytime") is True:
                highs.setdefault(d, temp)
            elif temp is not None:
                lows.setdefault(d, temp)
            if sky and p.get("isDaytime") is True:
                day_sky.setdefault(d, sky)
        today = datetime.fromtimestamp(now, tz).date()
        limit = max(1, min(days, MAX_DAYS[NWS]))
        for d in sorted(set(temps) | set(highs) | set(lows)):
            if datetime.fromisoformat(d).date() < today:
                continue
            seen = temps.get(d) or []
            high = highs.get(d, max(seen) if seen else None)
            low = lows.get(d, min(seen) if seen else None)
            sky = day_sky.get(d) or (Counter(skies.get(d) or []).most_common(1)[0][0] if skies.get(d) else "")
            fc.days.append(Day(d, high, low, sky))
            if len(fc.days) >= limit:
                break
        if not fc.days:
            raise WeatherError("the US National Weather Service sent no days to show")
        return fc


class KmaShortTerm:
    name = KMA

    def __init__(self, fetcher: Fetcher, key: str):
        self.fetcher, self.key = fetcher, key

    def url(self, place: Place, now: float) -> str:
        base_date, base_time = kma_base(now)
        nx, ny = kma_grid(place.lat, place.lon)
        # the portal hands out the key twice: as is ("Decoding") and already URL-encoded ("Encoding"); either works here
        key = self.key if "%" in self.key else quote(self.key, safe="")
        q = urlencode({"pageNo": 1, "numOfRows": 1000, "dataType": "JSON", "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny})
        return f"{KMA_URL}?serviceKey={key}&{q}"

    async def forecast(self, place: Place, days: int, now: float) -> Forecast:
        r = await self.fetcher("GET", self.url(place, now), allowed_hosts=[KMA_HOST], headers={"Accept": "application/json"})
        text = r.body.decode("utf-8", "replace")
        if "SERVICE_KEY" in text or r.status in (401, 403):
            raise WeatherError("the data.go.kr service key was not accepted (check that the short-term forecast service is approved for it)")
        data = _json(r, "data.go.kr")
        head = ((data or {}).get("response") or {}).get("header") or {}
        if str(head.get("resultCode")) != "00":
            raise WeatherError(f"data.go.kr answered {head.get('resultCode')}: {str(head.get('resultMsg') or '')[:120]}")
        items = (((data["response"].get("body") or {}).get("items") or {}).get("item")) or []
        tz = ZoneInfo("Asia/Seoul")
        slots: dict[str, dict[str, str]] = {}
        highs: dict[str, float] = {}
        lows: dict[str, float] = {}
        for it in items:
            when = f"{it.get('fcstDate')}{it.get('fcstTime')}"
            cat, val = str(it.get("category")), str(it.get("fcstValue"))
            slots.setdefault(when, {})[cat] = val
            day = f"{when[:4]}-{when[4:6]}-{when[6:8]}"
            try:
                if cat == "TMX":
                    highs[day] = float(val)
                elif cat == "TMN":
                    lows[day] = float(val)
            except ValueError:
                pass
        fc = Forecast(place, KMA, "Asia/Seoul")
        for when in sorted(slots):
            v = slots[when]
            ts = datetime.strptime(when, "%Y%m%d%H%M").replace(tzinfo=tz).timestamp()
            pty = v.get("PTY", "0")
            kind = {"1": "rain", "2": "sleet", "3": "snow", "4": "rain", "5": "rain", "6": "sleet", "7": "snow"}.get(pty, "")
            sky = {"1": "clear", "3": "partly", "4": "cloudy"}.get(v.get("SKY", ""), "")

            def num(k: str) -> float | None:
                try:
                    return float(v[k])
                except (KeyError, ValueError):
                    return None
            wsd = num("WSD")
            fc.hours.append(Hour(ts, num("TMP"), _kma_amount(v.get("PCP", "")), num("POP"), _kma_amount(v.get("SNO", "")),
                                 None if wsd is None else wsd * 3.6, None, kind, sky))
        for day in sorted({datetime.fromtimestamp(h.ts, tz).date().isoformat() for h in fc.hours})[:max(1, min(days, MAX_DAYS[KMA]))]:
            temps = [h.temp for h in fc.hours if h.temp is not None and datetime.fromtimestamp(h.ts, tz).date().isoformat() == day]
            hi = highs.get(day, max(temps) if temps else None)
            lo = lows.get(day, min(temps) if temps else None)
            skies = [h.sky for h in fc.hours if datetime.fromtimestamp(h.ts, tz).date().isoformat() == day and 9 <= datetime.fromtimestamp(h.ts, tz).hour <= 18]
            fc.days.append(Day(day, hi, lo, max(set(skies), key=skies.count) if skies else ""))
        return fc


_WAPI_SNOW = {1066, 1114, 1117, 1210, 1213, 1216, 1219, 1222, 1225, 1255, 1258, 1279, 1282}
_WAPI_SLEET = {1069, 1072, 1168, 1171, 1198, 1201, 1204, 1207, 1237, 1249, 1252, 1261, 1264}
_WAPI_THUNDER = {1087, 1273, 1276}


def wapi_sky(code: int | None) -> str:
    if code is None:
        return ""
    if code == 1000:
        return "clear"
    if code == 1003:
        return "partly"
    if code in (1006, 1009):
        return "cloudy"
    if code in (1030, 1135, 1147):
        return "fog"
    if code in _WAPI_THUNDER:
        return "thunder"
    if code in _WAPI_SNOW:
        return "snow"
    if code in _WAPI_SLEET:
        return "sleet"
    return "rain" if code >= 1063 else ""


class WeatherApiCom:
    name = WEATHERAPI

    def __init__(self, fetcher: Fetcher, key: str):
        self.fetcher, self.key = fetcher, key

    async def forecast(self, place: Place, days: int, now: float) -> Forecast:
        q = urlencode({"key": self.key, "q": f"{place.lat:.4f},{place.lon:.4f}", "days": max(1, min(days, MAX_DAYS[WEATHERAPI])),
                       "aqi": "yes", "alerts": "no"})
        r = await self.fetcher("GET", f"https://{WEATHERAPI_HOST}/v1/forecast.json?{q}", allowed_hosts=[WEATHERAPI_HOST],
                               headers={"Accept": "application/json"})
        data = _json(r, "WeatherAPI.com")
        if r.status != 200 or not isinstance(data, dict) or "forecast" not in data:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            raise WeatherError(f"WeatherAPI.com refused the request (HTTP {r.status}): {str(err.get('message') or '')[:120]}")
        tzname = str((data.get("location") or {}).get("tz_id") or place.timezone or "UTC")
        fc = Forecast(place, WEATHERAPI, tzname)
        for fd in (data.get("forecast") or {}).get("forecastday") or []:
            day = fd.get("day") or {}
            fc.days.append(Day(str(fd.get("date")), day.get("maxtemp_c"), day.get("mintemp_c"), wapi_sky((day.get("condition") or {}).get("code"))))
            for h in fd.get("hour") or []:
                code = (h.get("condition") or {}).get("code")
                precip, snow = float(h.get("precip_mm") or 0), float(h.get("snow_cm") or 0)
                kind = "snow" if (code in _WAPI_SNOW or snow > 0) else "sleet" if code in _WAPI_SLEET else (
                    "rain" if (precip > 0 or (code or 0) >= 1063) else "")
                prob = max(float(h.get("chance_of_rain") or 0), float(h.get("chance_of_snow") or 0))
                fc.hours.append(Hour(float(h.get("time_epoch") or 0), h.get("temp_c"), precip, prob, snow, h.get("wind_kph"), h.get("gust_kph"),
                                     kind, wapi_sky(code)))
        aq = (data.get("current") or {}).get("air_quality") or {}
        if aq:
            fc.air.append(Air(now, aq.get("pm2_5"), aq.get("pm10"), None))
        return fc


# ---------------------------------------------------------------------------------------------------------------- notable changes
def notable(fc: Forecast, now: float, yesterday: Day | None = None) -> list[dict[str, Any]]:
    """What is worth a line today, from `now` to the end of the local day, in this order: rain or snow (starting at a time, or now
    and until when), a big drop from yesterday's high or low (5 °C or more), strong wind (gusts 50 km/h or wind 36 km/h), heat (high
    33 °C or more), cold (low -10 °C or less), poor air (PM2.5 36 µg/m³ or more, or US AQI 101 or more). Each: {"kind", ...numbers}."""
    tz = fc.tz()
    local = datetime.fromtimestamp(now, tz)
    today = local.date().isoformat()
    start = local.replace(minute=0, second=0, microsecond=0).timestamp()
    end = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    hours = [h for h in fc.hours if start <= h.ts < end]
    out: list[dict[str, Any]] = []
    wet = [h for h in hours if h.wet()]
    if wet:
        first = wet[0]
        kind = first.precip_kind or "rain"
        streak_end = None
        for h in hours[hours.index(first):]:
            if not h.wet():
                streak_end = h.ts
                break
        item: dict[str, Any] = {"kind": f"{kind}_now" if first.ts <= start else f"{kind}_start", "at": first.ts, "until": streak_end,
                                "prob": max((h.prob or 0) for h in wet) or None}
        out.append(item)
    day = fc.day(today)
    yday = yesterday or fc.yesterday
    if day and yday:
        drops = [(yday.high - day.high) if (yday.high is not None and day.high is not None) else 0.0,
                 (yday.low - day.low) if (yday.low is not None and day.low is not None) else 0.0]
        if max(drops) >= 5:
            out.append({"kind": "temp_drop", "delta": round(max(drops), 1)})
    gust = max((h.gust_kmh or 0) for h in hours) if hours else 0
    wind = max((h.wind_kmh or 0) for h in hours) if hours else 0
    if gust >= 50 or wind >= 36:
        out.append({"kind": "wind", "kmh": round(max(gust, wind), 1)})
    if day and day.high is not None and day.high >= 33:
        out.append({"kind": "heat", "high": day.high})
    if day and day.low is not None and day.low <= -10:
        out.append({"kind": "cold", "low": day.low})
    air = [a for a in fc.air if start - 3600 <= a.ts < end]
    pm = max((a.pm2_5 or 0) for a in air) if air else 0
    aqi = max((a.us_aqi or 0) for a in air) if air else 0
    if pm >= 36 or aqi >= 101:
        out.append({"kind": "air", "pm2_5": round(pm) if pm else None, "us_aqi": round(aqi) if aqi else None})
    return out


# ---------------------------------------------------------------------------------------------------------------- the service
class Weather:
    def __init__(self, store: Any, *, fetcher: Fetcher = fetch, clock: Callable[[], float] = time.time,
                 language: Callable[[], str] = lambda: "en"):
        self.store, self.fetcher, self.clock, self.language = store, fetcher, clock, language
        from .location import PlaceLookup
        self.places = PlaceLookup(store, fetcher=fetcher, clock=clock, language=language)   # coordinates -> place names (location.py)
        self._cache: dict[tuple[str, str, int], tuple[float, Forecast]] = {}
        self._typical: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._air: dict[str, tuple[float, list[Air]]] = {}
        if store.meta(OLD_HOME_META):
            store.set_meta(OLD_HOME_META, "")   # the home area of older servers: the weather now always follows the phone

    # ------------------------------------------------------------------ settings and places
    def config(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.store.meta(CONFIG_META) or "{}")
        except ValueError:
            raw = {}
        return {"provider": raw.get("provider") if isinstance(raw, dict) and raw.get("provider") in CHOICES else "auto"}

    def update(self, **changes: Any) -> dict[str, Any]:
        cfg = self.config()
        if changes.get("provider") is not None:
            if changes["provider"] not in CHOICES:
                raise WeatherError(f"provider must be one of {', '.join(CHOICES)}")
            cfg["provider"] = changes["provider"]
        self.store.set_meta(CONFIG_META, json.dumps(cfg))
        return cfg

    def _place(self, meta: str) -> Place | None:
        try:
            raw = json.loads(self.store.meta(meta) or "{}")
        except ValueError:
            return None
        if not isinstance(raw, dict) or "lat" not in raw:
            return None
        try:
            return Place(str(raw.get("name") or ""), float(raw["lat"]), float(raw["lon"]), str(raw.get("country_code") or ""),
                         str(raw.get("admin1") or ""), str(raw.get("timezone") or ""), str(raw.get("source") or "phone"))
        except (TypeError, ValueError):
            return None

    def here(self) -> tuple[Place, float] | None:
        """The place a phone last sent, with when."""
        p = self._place(HERE_META)
        if p is None:
            return None
        return p, float(self._here_raw().get("at") or 0)

    def here_raw(self) -> dict[str, Any]:
        """The newest place a phone sent, as stored: lat, lon, accuracy_m, at, name, sublocality, country_code ({} = none)."""
        return self._here_raw()

    def _here_raw(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.store.meta(HERE_META) or "{}")
        except ValueError:
            return {}
        return raw if isinstance(raw, dict) else {}

    def set_here(self, lat: float, lon: float, name: str = "", country_code: str = "", accuracy: float | None = None,
                 sublocality: str = "") -> dict[str, Any]:
        """A phone's location (sent when the app is opened; precise or approximate, as the owner allowed, with its accuracy in metres).
        Kept only on this server, and only the newest one (any of the owner's phones: the newest wins)."""
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise WeatherError(t("A place needs a name and a real position."))
        data = {"name": (name or "").strip()[:80], "lat": round(lat, 5), "lon": round(lon, 5), "country_code": (country_code or "").upper()[:2],
                "source": "phone", "at": self.clock()}
        if accuracy is not None and 0 <= accuracy < 1_000_000:
            data["accuracy_m"] = round(accuracy)
        if (sub := " ".join((sublocality or "").split())[:80]):
            data["sublocality"] = sub   # the neighbourhood (dong) from the phone's own geocoder, when it knows it
        self.store.set_meta(HERE_META, json.dumps(data, ensure_ascii=False))
        return data

    def current_place(self) -> Place | None:
        """Where the owner is, for forecasts: the last location a phone sent, however old (None when no phone ever sent one), rounded
        to about a kilometre so a weather service never gets the precise position."""
        here = self.here()
        if not here:
            return None
        p = here[0]
        return Place(p.name, round(p.lat, 2), round(p.lon, 2), p.country_code, p.admin1, p.timezone, p.source)

    async def geocode(self, name: str, count: int = 5) -> list[Place]:
        """Places for a name (Open-Meteo geocoding), towns and regions first, then by population."""
        return await geocode_places(self.fetcher, name, self.language() or "en", count)

    # ------------------------------------------------------------------ keys (the owner's own; checked, then sealed in the vault)
    def key(self, provider: str) -> str | None:
        spec = KEYS.get(provider)
        if spec is None:
            return None
        cred = self.store.credential(spec.item)
        return (cred or {}).get("secret") or None

    def keys(self) -> dict[str, bool]:
        return {p: bool(self.key(p)) for p in KEYS}

    async def save_key(self, provider: str, key: str) -> dict[str, Any]:
        spec = KEYS.get(provider)
        if spec is None:
            raise WeatherError(t("This weather service needs no key."))
        key = (key or "").strip()
        if not 8 <= len(key) <= 512 or any(c.isspace() for c in key) or not key.isprintable():
            raise WeatherError(t("Please check the {what}. It must be one line with no spaces.", what=t("key")))
        test = Place("Seoul", 37.566, 126.978, "KR") if provider == KMA else (self.current_place() or Place("London", 51.5072, -0.1276, "GB"))
        maker = KmaShortTerm(self.fetcher, key) if provider == KMA else WeatherApiCom(self.fetcher, key)
        try:
            await maker.forecast(test, 1, self.clock())
        except NetGuardError as e:
            raise WeatherError(t("Could not connect to {name}: {error}", name=NAMES[provider], error=e)) from None
        except WeatherError as e:
            raise WeatherError(t("{name} did not accept this information. Nothing was saved. ({error})", name=NAMES[provider], error=e)) from None
        old = self.store.credential(spec.item)
        if old and old["kind"] != "api":
            raise WeatherError(t("The vault has another item named '{item}'. Delete that item and try again.", item=spec.item))
        self.store.delete_credential(spec.item)
        self.store.save_credential(spec.item, spec.label, [spec.host], {"type": "query_param", "param": spec.param}, "", kind="api")
        self.store.set_credential_secret(spec.item, key)
        self.store.set_credential_result(spec.item, "ok", "checked when it was added")
        self.store.add_vault_log(spec.item, "saved", f"{spec.label}, checked with a real request")
        self._cache.clear()
        return {"provider": provider, "saved": True}

    def delete_key(self, provider: str) -> bool:
        spec = KEYS.get(provider)
        if spec is None or self.store.credential(spec.item) is None:
            return False
        self.store.delete_credential(spec.item)
        self.store.add_vault_log(spec.item, "deleted", "removed in the briefing settings")
        self._cache.clear()
        return True

    # ------------------------------------------------------------------ forecasts
    def choose(self, place: Place) -> str:
        """Which provider answers for `place`: auto = the KMA when its key is saved and the place is in Korea, else Open-Meteo; a
        provider picked by hand when its key is there (the KMA only for Korea), else Open-Meteo."""
        pref = self.config()["provider"]
        if pref == "auto":
            return KMA if self.key(KMA) and in_korea(place) else OPEN_METEO
        if pref == KMA:
            return KMA if self.key(KMA) and in_korea(place) else OPEN_METEO
        if pref == WEATHERAPI:
            return WEATHERAPI if self.key(WEATHERAPI) else OPEN_METEO
        return OPEN_METEO

    def _provider(self, name: str) -> Any:
        if name == KMA:
            return KmaShortTerm(self.fetcher, self.key(KMA) or "")
        if name == WEATHERAPI:
            return WeatherApiCom(self.fetcher, self.key(WEATHERAPI) or "")
        if name == NWS:
            return NwsForecast(self.fetcher)
        return OpenMeteo(self.fetcher, self.clock)

    async def forecast(self, place: Place, days: int = 2, *, stale_seconds: float = 0) -> Forecast:
        """`stale_seconds`: when the service cannot be reached, a cached forecast this young is returned instead of an error."""
        now = self.clock()
        chosen = self.choose(place)
        if days > MAX_DAYS[chosen]:
            chosen = OPEN_METEO   # a longer range than the chosen service gives: the one with the longest range answers
        if chosen == OPEN_METEO and self.config()["provider"] == "auto" and (place.country_code or "").upper() == "US" and days <= MAX_DAYS[NWS]:
            chosen = NWS   # a US place on auto: the National Weather Service first (an owner's own pick of a service is kept as it is)
        ck = (chosen, place.key(), days)
        hit = self._cache.get(ck)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        try:
            fc = await self._provider(chosen).forecast(place, days, now)
        except (WeatherError, NetGuardError) as e:
            # chain: the chosen service -> Open-Meteo (when it was keyed) -> MET Norway (no key) -> the last forecast, if young enough
            why, fc = str(e)[:200], None
            if chosen != OPEN_METEO:
                try:
                    fc = await OpenMeteo(self.fetcher, self.clock).forecast(place, days, now)
                    fc.fallback_from, fc.note = chosen, why
                except (WeatherError, NetGuardError) as e2:
                    why = str(e2)[:200]
            if fc is None:
                try:
                    fc = await MetNorway(self.fetcher).forecast(place, days, now)
                    fc.fallback_from, fc.note = chosen, why
                except (WeatherError, NetGuardError) as e3:
                    if stale_seconds and hit and now - hit[0] < stale_seconds:
                        return hit[1]   # every service is down for now: the last forecast for this place is still today's best answer
                    raise WeatherError(f"the weather service could not be reached: {e3}") from None
        if fc.provider != WEATHERAPI:
            fc.air = await self.air(place)
        if fc.yesterday is None:
            fc.yesterday = self._recorded(place, now, fc.tz())
        self._record(place, fc)
        self._cache[ck] = (now, fc)
        return fc

    async def typical(self, place: Place, date: str) -> dict[str, Any]:
        """Past values for a calendar date at a place: that date +-TYPICAL_SPAN days in each of the last TYPICAL_YEARS years (Open-Meteo's
        historical weather), as {"years": (first, last), "days": n, "high", "low" (averages, C), "high_range", "low_range" ((min, max)),
        "wet_share" (days with 1 mm or more, 0..1), "mm" (average a day), "on_the_day": Day | None (that very date, when it is past)}."""
        want = datetime.fromisoformat(date).date()
        now = self.clock()
        hit = self._typical.get((place.key(), date))
        if hit and now - hit[0] < 86400:
            return hit[1]
        last_ok = datetime.fromtimestamp(now).date() - timedelta(days=ARCHIVE_LAG_DAYS)

        def on(year: int) -> Any:
            try:
                return want.replace(year=year)
            except ValueError:   # 29 February
                return want.replace(year=year, day=28)
        last = last_ok.year if on(last_ok.year) + timedelta(days=TYPICAL_SPAN) <= last_ok else last_ok.year - 1
        first = last - TYPICAL_YEARS + 1
        start, end = on(first) - timedelta(days=TYPICAL_SPAN), on(last) + timedelta(days=TYPICAL_SPAN)
        if want <= last_ok:   # a past date: that day itself too
            start, end = min(start, want), max(end, want)
        q = urlencode({"latitude": f"{place.lat:.4f}", "longitude": f"{place.lon:.4f}",
                       "start_date": start.isoformat(), "end_date": end.isoformat(),
                       "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum", "timezone": "auto"})
        try:
            r = await self.fetcher("GET", f"https://{ARCHIVE_HOST}/v1/archive?{q}", allowed_hosts=[ARCHIVE_HOST],
                                   headers={"Accept": "application/json"}, max_body=4_000_000)
        except NetGuardError as e:
            raise WeatherError(f"the historical weather could not be reached: {e}") from None
        data = _json(r, "Open-Meteo")
        if r.status != 200 or not isinstance(data, dict) or "daily" not in data:
            raise WeatherError(f"Open-Meteo's historical weather refused the request (HTTP {r.status})")
        d = data.get("daily") or {}
        times = d.get("time") or []

        def col(k: str, i: int) -> float | None:
            arr = d.get(k) or []
            v = arr[i] if i < len(arr) else None
            return float(v) if isinstance(v, (int, float)) else None
        windows = [(on(y) - timedelta(days=TYPICAL_SPAN), on(y) + timedelta(days=TYPICAL_SPAN)) for y in range(first, last + 1)]
        highs, lows, rains, exact = [], [], [], None
        for i, ts in enumerate(times):
            try:
                day = datetime.fromisoformat(str(ts)).date()
            except ValueError:
                continue
            hi, lo, mm = col("temperature_2m_max", i), col("temperature_2m_min", i), col("precipitation_sum", i)
            if day == want:
                exact = Day(day.isoformat(), hi, lo)
            if not any(a <= day <= b for a, b in windows):
                continue
            if hi is not None:
                highs.append(hi)
            if lo is not None:
                lows.append(lo)
            if mm is not None:
                rains.append(mm)
        if not highs or not lows:
            raise WeatherError("no past weather is recorded for this place and date")
        out = {"years": (first, last), "days": len(highs), "high": sum(highs) / len(highs), "low": sum(lows) / len(lows),
               "high_range": (min(highs), max(highs)), "low_range": (min(lows), max(lows)),
               "wet_share": (sum(1 for x in rains if x >= 1.0) / len(rains)) if rains else None,
               "mm": (sum(rains) / len(rains)) if rains else None, "on_the_day": exact}
        self._typical[(place.key(), date)] = (now, out)
        return out

    async def air(self, place: Place) -> list[Air]:
        """Hourly air quality from Open-Meteo for today (an empty list when it cannot be had: it is only a nice-to-have)."""
        now = self.clock()
        hit = self._air.get(place.key())
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        q = urlencode({"latitude": f"{place.lat:.4f}", "longitude": f"{place.lon:.4f}", "hourly": "pm10,pm2_5,us_aqi", "timezone": "auto",
                       "timeformat": "unixtime", "forecast_days": 1})
        try:
            r = await self.fetcher("GET", f"https://{AIR_HOST}/v1/air-quality?{q}", allowed_hosts=[AIR_HOST], headers={"Accept": "application/json"})
            data = json.loads(r.body) if r.status == 200 else {}
        except (NetGuardError, ValueError):
            data = {}
        h = (data or {}).get("hourly") or {} if isinstance(data, dict) else {}
        out = []
        for i, t in enumerate(h.get("time") or []):
            pick = lambda k, i=i: (h.get(k) or [])[i] if i < len(h.get(k) or []) else None  # noqa: E731
            out.append(Air(float(t), pick("pm2_5"), pick("pm10"), pick("us_aqi")))
        self._air[place.key()] = (now, out)
        return out

    def _records(self) -> dict[str, Any]:
        try:
            v = json.loads(self.store.meta(DAYS_META) or "{}")
        except ValueError:
            v = {}
        return v if isinstance(v, dict) else {}

    def _record(self, place: Place, fc: Forecast) -> None:
        """Keep each day's high and low per place (a few days), so a provider without yesterday's numbers can still be compared."""
        rec = self._records()
        mine = rec.get(place.key()) if isinstance(rec.get(place.key()), dict) else {}
        for d in fc.days[:1]:
            mine[d.date] = [d.high, d.low]
        mine = dict(sorted(mine.items())[-4:])
        rec[place.key()] = mine
        rec = dict(list(rec.items())[-6:])
        self.store.set_meta(DAYS_META, json.dumps(rec))

    def _recorded(self, place: Place, now: float, tz: ZoneInfo) -> Day | None:
        y = (datetime.fromtimestamp(now, tz).date() - timedelta(days=1)).isoformat()
        got = (self._records().get(place.key()) or {}).get(y)
        if isinstance(got, list) and len(got) == 2:
            return Day(y, got[0], got[1])
        return None

    def public(self) -> dict[str, Any]:
        here = self.here()
        return {**self.config(),
                "here": {**asdict(here[0]), "at": here[1], "accuracy_m": self._here_raw().get("accuracy_m")} if here else None,
                "keys": self.keys(),
                "providers": [{"id": p, "name": NAMES[p], "needs_key": p in KEYS, "attribution": ATTRIBUTION[p][0],
                               "attribution_url": ATTRIBUTION[p][1], "max_days": MAX_DAYS[p]} for p in PROVIDERS]}

    # ------------------------------------------------------------------ the agent's tool
    def tool(self) -> Tool:
        async def weather(a: dict[str, Any]) -> str:
            try:
                days = max(1, min(int(a.get("days") or 2), MAX_DAYS[OPEN_METEO]))
            except (TypeError, ValueError):
                days = 2
            date = str(a.get("date") or "").strip()[:10]
            if date:
                try:
                    datetime.fromisoformat(date)
                except ValueError:
                    return "Error: date must look like 2026-11-29"
            name = str(a.get("place") or "").strip()
            try:
                if name:
                    found = await self.geocode(name, 1)
                    if not found:
                        return f"No place called {name!r} was found. Try the full name of a city or region (and its country)."
                    place = found[0]
                else:
                    place = self.current_place()
                    if place is None:
                        return ("Where the user is is not known yet (the phone app sends a coarse location when it is opened, once "
                                "location is allowed) and no place was given. Ask the user for a city.")
                if date:
                    return await self._for_date(place, date, days)
                fc = await self.forecast(place, days)
            except WeatherError as e:
                return f"Error: {e}"
            return describe(fc, self.clock(), days)
        return Tool("weather", "Weather for a place (default: where the user is): highs, lows, rain or snow, wind, air quality, "
                    f"up to {MAX_DAYS[OPEN_METEO]} days. With date: that day's forecast, or outside the forecast the values around that "
                    f"date in the last {TYPICAL_YEARS} years. Use it instead of web search or a browser.",
                    {"type": "object", "properties": {
                        "place": {"type": "string", "description": "a city or region, any language"},
                        "days": {"type": "integer", "description": f"how many days, 1 to {MAX_DAYS[OPEN_METEO]} (default 2)"},
                        "date": {"type": "string", "description": "YYYY-MM-DD, a day the question is about (any distance ahead or back)"}}},
                    weather, read_only=True)

    async def _for_date(self, place: Place, date: str, days: int) -> str:
        """The forecast when `date` is within its range; otherwise the past values for that date (always labelled as such)."""
        now = self.clock()
        probe = await self.forecast(place, 1)
        tz = probe.tz()
        today = datetime.fromtimestamp(now, tz).date()
        want = datetime.fromisoformat(date).date()
        ahead = (want - today).days
        if 0 <= ahead < MAX_DAYS[OPEN_METEO]:
            n = max(days, ahead + 1)
            return describe(await self.forecast(place, n), now, n)
        got = await self.typical(place, date)
        return describe_typical(place, date, got, ahead)


    def location_tool(self) -> Tool:
        async def my_location(a: dict[str, Any]) -> str:
            raw = self._here_raw()
            if "lat" not in raw:
                from .location import location_ask
                ask = location_ask.get()
                outcome = await ask(str(a.get("reason") or "")[:200]) if ask is not None else "unavailable"
                raw = self._here_raw()
                if outcome == "refused":
                    return ("The user did not allow location on their phone. Ask them which neighbourhood or address they are near; "
                            "do not ask for the permission again in this conversation.")
                if "lat" not in raw:
                    return ("No location is known yet: the phone app sends one when it is opened, once the user allows location"
                            + (" (the card asking for it was not answered)" if outcome == "expired" else "") + ". Ask the user where they are.")
            age = max(0, int(self.clock() - float(raw.get("at") or 0)))
            ago = (f"{age // 60} minutes" if age < 3600 else f"{age // 3600} hours" if age < 2 * 86400 else f"{age // 86400} days") + " ago"
            acc = f", accurate to about {raw['accuracy_m']} m" if raw.get("accuracy_m") is not None else ""
            near = ", ".join(x for x in (str(raw.get("sublocality") or ""), str(raw.get("name") or ""), str(raw.get("country_code") or ""))
                             if x)
            out = (f"The user's last known location: {raw['lat']}, {raw['lon']}{acc}, sent {ago} (when they last opened the app)"
                   + (f", in {near}" if near else "") + ".")
            if not raw.get("sublocality"):   # the phone gave no neighbourhood: look it up on the map (rounded to ~110 m)
                place = await self.places.place_name(float(raw["lat"]), float(raw["lon"]))
                if place:
                    out += f" On the map that is around {place['name']} ({place['attribution']})."
            return out
        return Tool("my_location", "The user's last known location from their phone: coordinates, accuracy in metres, how old, and the "
                    "neighbourhood it is in. Use it for questions about places near the user, before asking where they are. If no "
                    "location is known, the user is asked in the chat to allow it and this waits for the answer.",
                    {"type": "object", "properties": {"reason": {"type": "string", "description": "why the location is needed, in a "
                     "few words in the user's language (shown on the card if the phone must be asked)"}}}, my_location, read_only=True,
                    trail="device")


def describe(fc: Forecast, now: float, days: int = 2) -> str:
    """The forecast as short lines for the model (it answers in the user's language)."""
    tz = fc.tz()
    u = units_for(fc.place.country_code)
    tu, wu = "°" + u["temp"], u["wind"]
    lines = [f"Weather for {fc.place.label()}{(' (' + fc.place.country_code + ')') if fc.place.country_code else ''}, from "
             f"{NAMES[fc.provider]}" + (f" ({NAMES[fc.fallback_from]} failed: {fc.note})" if fc.fallback_from else "") + "."]
    local_now = datetime.fromtimestamp(now, tz)
    if fc.yesterday and fc.yesterday.high is not None:
        lines.append(f"Yesterday: high {temp_in(fc.yesterday.high, u['temp'])}{tu}, low {temp_in(fc.yesterday.low, u['temp'])}{tu}.")
    for d in fc.days[:days]:
        hours = [h for h in fc.hours if datetime.fromtimestamp(h.ts, tz).date().isoformat() == d.date]
        wet = [h for h in hours if h.wet()]
        bits = [f"{d.date}: high {temp_in(d.high, u['temp'])}{tu}, low {temp_in(d.low, u['temp'])}{tu}" + (f", {d.sky}" if d.sky else "")]
        if wet:
            spans = sorted({datetime.fromtimestamp(h.ts, tz).strftime('%H:%M') for h in wet})
            kinds = sorted({h.precip_kind for h in wet})
            prob = max((h.prob or 0) for h in wet)
            bits.append(f"{'/'.join(kinds)} at {spans[0]}–{spans[-1]}" + (f" (up to {round(prob)}%)" if prob else ""))
        gust = max(((h.gust_kmh or h.wind_kmh or 0) for h in hours), default=0)
        if gust:
            bits.append(f"wind up to {wind_in(gust, wu)} {wu}")
        lines.append("; ".join(bits) + ".")
        if d.date == local_now.date().isoformat():
            ahead = [h for h in hours if h.ts >= now - 3600][::3][:8]
            if ahead:
                lines.append("  Today by the hour: " + ", ".join(
                    f"{datetime.fromtimestamp(h.ts, tz).strftime('%H:%M')} {temp_in(h.temp, u['temp'])}{tu}"
                    + (f" {h.precip_kind}" if h.wet() else "") for h in ahead))
    air = [a for a in fc.air if a.pm2_5 is not None]
    if air:
        pm = max(a.pm2_5 or 0 for a in air)
        aqi = max((a.us_aqi or 0) for a in air)
        lines.append(f"Air today: PM2.5 up to {round(pm)} µg/m³" + (f", US AQI up to {round(aqi)}" if aqi else "") + ".")
    text, url = ATTRIBUTION[fc.provider]
    lines.append(f"Source: {text} ({url})" + (" — CC BY 4.0, mention it when you share these numbers." if fc.provider in (OPEN_METEO, METNO) else "."))
    return "\n".join(lines)


def describe_typical(place: Place, date: str, got: dict[str, Any], ahead: int) -> str:
    """Past values for a date as short lines for the model, labelled as past values (not a forecast)."""
    u = units_for(place.country_code)
    tu = "°" + u["temp"]

    def deg(c: float | None) -> str:
        return f"{temp_in(c, u['temp'])}{tu}"
    first, last = got["years"]
    where = f"{place.label()}{(' (' + place.country_code + ')') if place.country_code else ''}"
    span = f"{date[5:]} ±{TYPICAL_SPAN} days"
    lines = [f"Weather for {where} on {date}: " + ("past the forecast range" if ahead >= 0 else "a past date") + "."]
    if got.get("on_the_day") and got["on_the_day"].high is not None:
        lines.append(f"Recorded on {date}: high {deg(got['on_the_day'].high)}, low {deg(got['on_the_day'].low)}.")
    lines.append(f"Typical values (not a forecast): recorded weather for {span} in each year {first}–{last} ({got['days']} days): "
                 f"high {deg(got['high'])} on average ({deg(got['high_range'][0])} to {deg(got['high_range'][1])}), low {deg(got['low'])} "
                 f"({deg(got['low_range'][0])} to {deg(got['low_range'][1])})"
                 + (f"; 1 mm or more of rain or snow on {round(got['wet_share'] * 100)}% of those days" if got.get("wet_share") is not None else "")
                 + ".")
    if ahead >= 0:
        lines.append(f"The forecast covers the next {MAX_DAYS[OPEN_METEO]} days; this date is {ahead} days away.")
    lines.append("Source: Weather data by Open-Meteo.com (https://open-meteo.com/), historical weather — CC BY 4.0, mention it when you "
                 "share these numbers.")
    return "\n".join(lines)


__all__ = ["ATTRIBUTION", "CHOICES", "KEYS", "KMA", "METNO", "OPEN_METEO", "PROVIDERS", "WEATHERAPI", "Air", "Day", "Forecast", "Hour", "Place",
           "Weather", "WeatherError", "describe", "describe_typical", "in_korea", "kma_base", "kma_grid", "notable", "temp_in", "units_for", "wind_in"]
