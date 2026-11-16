"""Where the user is, as a place (backlog 24): coordinates -> a place name, places near coordinates, and the chat card that asks
for the phone's location permission while the agent's turn waits.

Provider: Photon by komoot (https://photon.komoot.io), OpenStreetMap data, free and without a key, fair use (no bulk use; one
request per second per server here, and every answer is cached). Attribution: "© OpenStreetMap contributors" (ODbL), which every
answer the agent gets carries. Coordinates are rounded before they leave the server (3 decimals, about 110 m), so the provider never
sees the precise position, and the place name is kept at neighbourhood level (no street or house number).

Contracts:
- `PlaceLookup.place_name(lat, lon)` -> {"name": "Yeoksam-dong, Gangnam-gu, Seoul", "parts": {...}, "attribution"} or None.
- `PlaceLookup.search_near(query, lat, lon, limit)` -> [{"name", "kind", "address", "lat", "lon", "distance_m"}], for the
  near-coordinates place search tool (the runtime agent's tools/; see docs/location.md).
- `location_ask` (set by the server for a followed chat turn): `await ask(reason)` -> "allowed" | "refused" | "expired" |
  "unavailable"; the card and its answer endpoint are described in LOCATION REQUEST in server.py (approval-like card).
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlencode

from .netguard import NetGuardError, fetch

PHOTON_HOST = "photon.komoot.io"
ATTRIBUTION = "© OpenStreetMap contributors (Photon by komoot)"
ROUND = 3                      # decimals sent to the provider and used as the cache key (~110 m)
CACHE_SECONDS = 30 * 86400
CACHE_MAX = 2000
MIN_INTERVAL = 1.0             # seconds between two requests to the provider (fair use)
PHOTON_LANGS = ("en", "de", "fr")   # Photon's own languages; anything else gets the local names ("default")
SCHEMA = """
CREATE TABLE IF NOT EXISTS place_cache (key TEXT PRIMARY KEY, data TEXT NOT NULL, at REAL NOT NULL);
"""

# Set by the server for a followed chat turn: ask the phone for its location with a card in the chat and wait for the answer.
location_ask: ContextVar[Callable[[str], Awaitable[str]] | None] = ContextVar("location_ask", default=None)


def rounded(lat: float, lon: float) -> tuple[float, float]:
    return round(float(lat), ROUND), round(float(lon), ROUND)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(round(2 * r * math.asin(math.sqrt(a))))


def _place_parts(props: dict[str, Any]) -> dict[str, str]:
    keep = ("name", "district", "locality", "city", "county", "state", "country", "countrycode", "postcode", "type", "osm_value")
    return {k: str(props[k]) for k in keep if props.get(k)}


def _neighbourhood_name(parts: dict[str, str]) -> str:
    """'Yeoksam-dong, Gangnam-gu, Seoul': the smallest area names first, without street or house number."""
    order = [parts.get("district") or parts.get("locality"), parts.get("county") if parts.get("city") else None,
             parts.get("city") or parts.get("county"), parts.get("state")]
    if parts.get("type") in ("district", "locality", "suburb", "neighbourhood", "city", "county", "state") and parts.get("name"):
        order.insert(0, parts["name"])
    out: list[str] = []
    for x in order:
        if x and x not in out:
            out.append(x)
    return ", ".join(out[:3])


class PlaceLookup:
    def __init__(self, store: Any, *, fetcher: Callable[..., Awaitable[Any]] = fetch, clock: Callable[[], float] = time.time,
                 language: Callable[[], str] | None = None):
        self.store, self.fetcher, self.clock, self.language = store, fetcher, clock, language or (lambda: "")
        self._lock = asyncio.Lock()
        self._last = 0.0
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def _lang(self) -> str:
        lang = (self.language() or "").split("-")[0].lower()
        return lang if lang in PHOTON_LANGS else ""

    def _cached(self, key: str) -> Any:
        rows = self.store._all("SELECT data, at FROM place_cache WHERE key = ?", (key,))
        if rows and self.clock() - rows[0]["at"] < CACHE_SECONDS:
            return json.loads(rows[0]["data"])
        return None

    def _keep(self, key: str, data: Any) -> None:
        self.store._run("INSERT INTO place_cache(key, data, at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET data = excluded.data, "
                        "at = excluded.at", (key, json.dumps(data, ensure_ascii=False), self.clock()))
        n = self.store._all("SELECT COUNT(*) AS n FROM place_cache")[0]["n"]
        if n > CACHE_MAX:
            self.store._run("DELETE FROM place_cache WHERE key IN (SELECT key FROM place_cache ORDER BY at LIMIT ?)", (n - CACHE_MAX,))

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:   # one request at a time, at most one a second
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            url = f"https://{PHOTON_HOST}{path}?" + urlencode(params)
            try:
                r = await self.fetcher("GET", url, allowed_hosts=[PHOTON_HOST], headers={"Accept": "application/json"})
            except NetGuardError:
                return None
        if getattr(r, "status", 0) != 200:
            return None
        try:
            data = json.loads(r.body)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def place_name(self, lat: float, lon: float) -> dict[str, Any] | None:
        """The neighbourhood-level name of a position, or None (the provider did not answer or knows nothing there)."""
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        rlat, rlon = rounded(lat, lon)
        lang = self._lang()
        key = f"rev:{rlat}:{rlon}:{lang}"
        hit = self._cached(key)
        if hit is not None:
            return hit or None
        params: dict[str, Any] = {"lat": rlat, "lon": rlon, "limit": 1}
        if lang:
            params["lang"] = lang
        data = await self._get("/reverse", params)
        if data is None:
            return None   # not cached: a later look may work
        feats = data.get("features") or []
        props = (feats[0].get("properties") or {}) if feats and isinstance(feats[0], dict) else {}
        parts = _place_parts(props)
        name = _neighbourhood_name(parts)
        out = {"name": name, "parts": parts, "attribution": ATTRIBUTION} if name else {}
        self._keep(key, out)
        return out or None

    async def search_near(self, query: str, lat: float, lon: float, limit: int = 8) -> list[dict[str, Any]]:
        """Places matching `query` (a kind like "parking" or a name) near a position, nearest first."""
        q = " ".join(str(query or "").split())[:100]
        if len(q) < 2 or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return []
        rlat, rlon = rounded(lat, lon)
        limit = max(1, min(int(limit), 20))
        lang = self._lang()
        key = f"near:{rlat}:{rlon}:{lang}:{limit}:{q.casefold()}"
        hit = self._cached(key)
        if hit is not None:
            return hit
        params: dict[str, Any] = {"q": q, "lat": rlat, "lon": rlon, "limit": limit}
        if lang:
            params["lang"] = lang
        data = await self._get("/api", params)
        if data is None:
            return []
        out = []
        for f in data.get("features") or []:
            if not isinstance(f, dict):
                continue
            props = f.get("properties") or {}
            coords = ((f.get("geometry") or {}).get("coordinates") or [None, None])
            if not isinstance(coords, list) or len(coords) < 2 or coords[0] is None:
                continue
            plon, plat = float(coords[0]), float(coords[1])
            addr = ", ".join(x for x in (" ".join(str(props.get(k) or "") for k in ("street", "housenumber")).strip(),
                                         str(props.get("district") or props.get("locality") or ""), str(props.get("city") or "")) if x)
            out.append({"name": str(props.get("name") or ""), "kind": str(props.get("osm_value") or props.get("type") or ""),
                        "address": addr, "lat": round(plat, 5), "lon": round(plon, 5), "distance_m": distance_m(lat, lon, plat, plon)})
        out.sort(key=lambda p: p["distance_m"])
        self._keep(key, out)
        return out
