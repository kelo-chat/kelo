"""places_near: places that match what the user wants (a kind like "parking" or "pharmacy", or a name, in any language) near the
user's position or near a place they named, nearest first, with the distance and the address (backlog 24).

It only gains capability, never rules: no list of kinds, sites or categories; the model passes the user's words. Data: the server's
PlaceLookup (location.py: Photon by komoot on OpenStreetMap, coordinates rounded to ~110 m before they leave the server, cached,
one request a second); every answer carries its attribution. The user's position is the one their phone last sent (weather's
place record); when none is known, the phone is asked with the chat's location card (`location.location_ask`) and the call waits for
the answer, exactly as my_location does.
"""

from __future__ import annotations

import time
from typing import Any

from ..agent import Ours, Tool, ToolOutput
from ..agent.fence import data
from ..location import ATTRIBUTION, distance_m, location_ask

MAX_LIMIT = 15
REFUSED = ("The user did not allow location on their phone. Ask them which neighbourhood, station or address they are near and call "
           "places_near again with `near`; do not ask for the permission again in this conversation.")
UNKNOWN = ("No location is known yet: the phone app sends one when it is opened, once the user allows location{why}. Ask the user "
           "where they are (a neighbourhood, station or address) and call places_near again with `near`.")


def _distance(m: int) -> str:
    return f"{m} m" if m < 1000 else f"{m / 1000:.1f} km"


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60} minutes ago" if s < 3600 else f"{s // 3600} hours ago" if s < 2 * 86400 else f"{s // 86400} days ago"


def places_tool(weather: Any, *, clock: Any = time.time) -> Tool:
    """The tool, on the server's Weather (its `places` lookup, its place record of the phone and its geocoder)."""

    def here() -> dict[str, Any]:
        raw = getattr(weather, "here_raw", None) or weather._here_raw   # a public here_raw() once the server has one
        got = raw()
        return got if isinstance(got, dict) else {}

    async def anchor(near: str, raw: dict[str, Any]) -> tuple[float, float, str] | None:
        """The coordinates of a place the user named: searched near the user's position when known, else by its name."""
        if "lat" in raw:
            hits = await weather.places.search_near(near, float(raw["lat"]), float(raw["lon"]), limit=1)
            if hits:
                h = hits[0]
                return h["lat"], h["lon"], ", ".join(x for x in (h["name"], h["address"]) if x) or near
        try:
            found = await weather.geocode(near, 1)
        except Exception:  # noqa: BLE001 - a name the geocoder does not take: not found
            found = []
        if not found:
            return None
        g = found[0]
        hits = await weather.places.search_near(near, g.lat, g.lon, limit=1)   # a station or a building inside that town, if it is one
        if hits:
            h = hits[0]
            return h["lat"], h["lon"], ", ".join(x for x in (h["name"], h["address"]) if x) or near
        return g.lat, g.lon, g.label()

    async def run(args: dict[str, Any]) -> Any:
        query = " ".join(str(args.get("query") or "").split())[:100]
        near = " ".join(str(args.get("near") or "").split())[:100]
        try:
            limit = max(1, min(int(args.get("limit") or 8), MAX_LIMIT))
        except (TypeError, ValueError):
            limit = 8
        if len(query) < 2:
            return Ours("Error: say what to look for in `query` (a kind of place or a name, in the user's words).")
        raw = here()
        if near:
            got = await anchor(near, raw)
            if got is None:
                return Ours(f"Could not find {near!r} on the map. Ask the user for a better-known place nearby or the address.")
            lat, lon, where = got
            head = f"Places for {query!r} near {where} ({lat:.4f}, {lon:.4f}), nearest first:"
        else:
            if "lat" not in raw:
                ask = location_ask.get()
                outcome = await ask(str(args.get("reason") or "")[:200]) if ask is not None else "unavailable"
                raw = here()
                if outcome == "refused":
                    return Ours(REFUSED)
                if "lat" not in raw:
                    return Ours(UNKNOWN.format(why=" (the card asking for it was not answered)" if outcome == "expired" else ""))
            lat, lon = float(raw["lat"]), float(raw["lon"])
            acc = f", accurate to about {raw['accuracy_m']} m" if raw.get("accuracy_m") is not None else ""
            area = ", ".join(x for x in (str(raw.get("sublocality") or ""), str(raw.get("name") or "")) if x)
            head = (f"Places for {query!r} near the user's last known position ({lat}, {lon}{acc}, sent "
                    f"{_ago(clock() - float(raw.get('at') or 0))}{', around ' + area if area else ''}), nearest first:")
        hits = await weather.places.search_near(query, lat, lon, limit=limit)
        if not hits:
            return Ours(f"{head} nothing found on the map ({ATTRIBUTION}). Try other words for it (another language, a broader "
                        "kind), a larger area, or ask the user.")
        lines = []
        for i, h in enumerate(sorted(hits, key=lambda h: h.get("distance_m", 0)), 1):
            d = h.get("distance_m")
            d = distance_m(lat, lon, float(h["lat"]), float(h["lon"])) if d is None else int(d)
            bits = [h.get("name") or "(no name)"] + ([f"({h['kind']})"] if h.get("kind") else [])
            lines.append(f"{i}. {' '.join(bits)} — {_distance(d)}" + (f" — {h['address']}" if h.get("address") else "")
                         + f" [{h['lat']}, {h['lon']}]")
        tail = (f"Map data {ATTRIBUTION}: name the source when you list these. Distances are straight-line from the point above; "
                "the map may miss new or unlisted places.")
        return ToolOutput(data("\n".join(lines)), head=head, tail=tail)

    return Tool(
        "places_near",
        "Places near the user (or near `near`): any kind or name, any language; nearest first with distance and address. Uses the "
        "phone's last position, asking for it if unknown. Use known coordinates before asking where the user is.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "what to find, in the user's words"},
            "near": {"type": "string", "description": "a place or address to search around"},
            "limit": {"type": "integer", "description": f"default 8, max {MAX_LIMIT}"},
            "reason": {"type": "string", "description": "a few words for the location card, in the user's language"}},
         "required": ["query"]},
        run, untrusted=True, source="web", read_only=True)
