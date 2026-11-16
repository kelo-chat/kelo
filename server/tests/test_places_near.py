"""places_near (tools/places.py): places near the user or near a named place, nearest first, with distance, address and the map
data's attribution; the location card when no position is known. A fake Photon and a fake geocoder (no external calls)."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from assistant import location as loc_mod
from assistant.agent.fence import is_fenced, plain
from assistant.agent.loop import _result_text
from assistant.netguard import Reply
from assistant.store import Store
from assistant.tools.places import places_tool
from assistant.weather import Weather

NOW = 1_800_000_000.0


def feature(name, lon, lat, kind="", **props):
    return {"geometry": {"coordinates": [lon, lat]}, "properties": {"name": name, "osm_value": kind, **props}}


class Maps:
    """Photon (/api near a point) and Open-Meteo geocoding, answering from tables."""

    def __init__(self):
        self.urls: list[str] = []
        self.near: dict[str, list] = {}
        self.towns: dict[str, dict] = {}

    async def __call__(self, method, url, *, allowed_hosts, headers=None, **kw):
        self.urls.append(url)
        q = parse_qs(urlsplit(url).query)
        if "photon.komoot.io/api" in url:
            return Reply(200, {}, json.dumps({"features": self.near.get(q["q"][0], [])}).encode(), False)
        if "geocoding-api.open-meteo.com" in url:
            hit = self.towns.get(q["name"][0])
            return Reply(200, {}, json.dumps({"results": [hit] if hit else []}).encode(), False)
        return Reply(404, {}, b"{}", False)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(loc_mod, "MIN_INTERVAL", 0.0)


def setup(tmp_path, *, here=True):
    maps = Maps()
    w = Weather(Store(tmp_path / "s.db"), fetcher=maps, clock=lambda: NOW, language=lambda: "ko")
    if here:
        w.set_here(37.50012, 127.03678, name="Seoul", country_code="KR", accuracy=35, sublocality="역삼동")
    return w, maps, places_tool(w, clock=lambda: NOW + 600)


async def test_nearby_places_of_the_user_nearest_first_with_distance_address_and_attribution(tmp_path):
    w, maps, tool = setup(tmp_path)
    maps.near["주차장"] = [feature("Far Parking", 127.06, 37.51, "parking", city="Seoul"),
                         feature("Near Parking", 127.0369, 37.5003, "parking", street="Teheran-ro", district="Yeoksam-dong", city="Seoul")]
    out = await tool.handler({"query": "주차장"})
    text = plain(_result_text(tool, out))
    assert text.index("Near Parking") < text.index("Far Parking")
    assert "Teheran-ro, Yeoksam-dong, Seoul" in text and " m —" in text and "km" in text
    assert "OpenStreetMap contributors" in text and "around 역삼동" in text and "10 minutes ago" in text
    assert is_fenced(_result_text(tool, out))                      # the names come from the map: external data
    q = parse_qs(urlsplit(maps.urls[-1]).query)
    assert q["q"] == ["주차장"] and q["lat"] == ["37.5"] and q["lon"] == ["127.037"]   # rounded before it leaves the server


async def test_near_a_named_place_searches_around_it(tmp_path):
    w, maps, tool = setup(tmp_path, here=False)
    maps.towns["Busan"] = {"name": "Busan", "latitude": 35.1796, "longitude": 129.0756, "country_code": "KR", "timezone": "Asia/Seoul"}
    maps.near["Busan"] = []
    maps.near["pharmacy"] = [feature("Haeundae Pharmacy", 129.08, 35.18, "pharmacy", city="Busan")]
    asked = []
    token = loc_mod.location_ask.set(lambda reason: asked.append(reason))
    try:
        text = plain(_result_text(tool, await tool.handler({"query": "pharmacy", "near": "Busan"})))
    finally:
        loc_mod.location_ask.reset(token)
    assert "Haeundae Pharmacy" in text and "near Busan" in text and not asked   # a named place needs no location card


async def test_without_a_position_the_phone_is_asked_and_the_search_goes_on(tmp_path):
    w, maps, tool = setup(tmp_path, here=False)
    maps.near["cafe"] = [feature("Corner Cafe", 127.0369, 37.5003, "cafe")]
    asked = []

    async def ask(reason):
        asked.append(reason)
        w.set_here(37.5001, 127.0368, name="Seoul", country_code="KR")   # the user allowed it: the phone sent its place
        return "allowed"
    token = loc_mod.location_ask.set(ask)
    try:
        text = plain(_result_text(tool, await tool.handler({"query": "cafe", "reason": "근처 카페 찾기"})))
    finally:
        loc_mod.location_ask.reset(token)
    assert asked == ["근처 카페 찾기"] and "Corner Cafe" in text


async def test_refused_or_unanswered_location_says_what_to_do(tmp_path):
    w, maps, tool = setup(tmp_path, here=False)

    async def refuse(reason):
        return "refused"
    token = loc_mod.location_ask.set(refuse)
    try:
        out = await tool.handler({"query": "cafe"})
    finally:
        loc_mod.location_ask.reset(token)
    assert "did not allow location" in out and "`near`" in out
    out = await tool.handler({"query": "cafe"})                 # no card possible here (not a followed chat reply)
    assert "No location is known yet" in out
    assert "Error" in await tool.handler({"query": "x"})


async def test_nothing_found_and_an_unknown_place(tmp_path):
    w, maps, tool = setup(tmp_path)
    out = await tool.handler({"query": "volcano"})
    assert "nothing found" in out and "OpenStreetMap" in out
    w2, maps2, tool2 = setup(tmp_path / "b", here=False) if (tmp_path / "b").mkdir() is None else (None, None, None)
    out = await tool2.handler({"query": "cafe", "near": "Nowhere-at-all"})
    assert "Could not find" in out


def test_the_tool_text_says_to_use_known_coordinates_before_asking():
    from assistant.tools.places import places_tool as make
    from types import SimpleNamespace
    t = make(SimpleNamespace(places=None, _here_raw=dict))
    assert "known coordinates before asking" in t.description and len(t.description) < 260 and t.read_only and t.untrusted
