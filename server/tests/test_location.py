"""Where the user is, as a place (location.py, backlog 24): the place name for coordinates (Photon, rounded, cached, fair use),
places near coordinates, my_location with the neighbourhood, and the chat card that asks the phone for its location while the
reply waits (LOCATION REQUEST)."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from assistant import location as loc_mod
from assistant import server as server_mod
from assistant.location import PlaceLookup
from assistant.netguard import Reply
from assistant.server import create_app
from assistant.store import Store

from test_agent_profile import app_settings
from test_handover import scripted, waiting_item, last_tool_output
from test_server import AUTH, Running, follow
from test_workers import call, say

REVERSE = {"features": [{"geometry": {"coordinates": [127.036, 37.5]}, "properties": {
    "name": "Yeoksam-dong", "type": "district", "district": "Yeoksam-dong", "county": "Gangnam-gu", "city": "Seoul",
    "country": "South Korea", "countrycode": "KR", "street": "Teheran-ro", "housenumber": "123"}}]}
NEAR = {"features": [
    {"geometry": {"coordinates": [127.04, 37.501]}, "properties": {"name": "Far Parking", "osm_value": "parking", "city": "Seoul"}},
    {"geometry": {"coordinates": [127.0361, 37.5001]}, "properties": {"name": "Near Parking", "osm_value": "parking",
                                                                      "street": "Teheran-ro", "district": "Yeoksam-dong", "city": "Seoul"}}]}


class Photon:
    def __init__(self):
        self.urls = []

    async def __call__(self, method, url, *, allowed_hosts, headers=None, **kw):
        assert allowed_hosts == ["photon.komoot.io"] and method == "GET"
        self.urls.append(url)
        if "photon.komoot.io" not in url:   # weather and other services in the same app: nothing to say
            return Reply(404, {}, b"{}", False)
        body = REVERSE if "/reverse?" in url else NEAR
        return Reply(200, {}, json.dumps(body).encode(), False)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(loc_mod, "MIN_INTERVAL", 0.0)


async def test_place_name_is_rounded_cached_and_never_holds_the_street(tmp_path):
    photon = Photon()
    places = PlaceLookup(Store(tmp_path / "s.db"), fetcher=photon, language=lambda: "ko")
    got = await places.place_name(37.500123, 127.036789)
    assert got["name"] == "Yeoksam-dong, Gangnam-gu, Seoul" and "OpenStreetMap" in got["attribution"]
    assert "Teheran" not in json.dumps(got) and "123" not in got["name"]
    q = parse_qs(urlsplit(photon.urls[0]).query)
    assert q["lat"] == ["37.5"] and q["lon"] == ["127.037"] and "lang" not in q   # ~110 m; Korean: the local names
    assert await places.place_name(37.5004, 127.0371) == got and len(photon.urls) == 1   # same rounded spot: from the cache
    places_en = PlaceLookup(Store(tmp_path / "s.db"), fetcher=photon, language=lambda: "en-US")
    await places_en.place_name(37.5, 127.0)
    assert parse_qs(urlsplit(photon.urls[-1]).query)["lang"] == ["en"]
    assert await places.place_name(91, 0) is None


async def test_a_provider_that_does_not_answer_gives_nothing_and_is_not_cached(tmp_path):
    calls = []

    async def down(method, url, **kw):
        calls.append(url)
        return Reply(503, {}, b"", False)
    places = PlaceLookup(Store(tmp_path / "s.db"), fetcher=down)
    assert await places.place_name(37.5, 127.0) is None and await places.place_name(37.5, 127.0) is None
    assert len(calls) == 2


async def test_search_near_lists_nearest_first(tmp_path):
    photon = Photon()
    places = PlaceLookup(Store(tmp_path / "s.db"), fetcher=photon)
    got = await places.search_near("parking", 37.5, 127.036)
    assert [p["name"] for p in got] == ["Near Parking", "Far Parking"] and got[0]["distance_m"] < got[1]["distance_m"]
    assert got[0]["address"] == "Teheran-ro, Yeoksam-dong, Seoul"
    assert await places.search_near("p", 37.5, 127.0) == []


def location_app(tmp_path, fn, photon=None):
    return create_app(app_settings(tmp_path), tools=None, stream_fn=fn, weather_fetcher=photon or Photon())


async def test_my_location_adds_the_neighbourhood_unless_the_phone_sent_it(tmp_path):
    fn = scripted(call("my_location"), say("근처예요."), call("my_location"), say("네."))
    async with Running(location_app(tmp_path, fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        await c.post("/api/briefing/location", json={"lat": 37.5001, "lon": 127.0361, "name": "Seoul", "country_code": "KR"})
        await follow(c, (await c.post("/api/turns", json={"text": "여기 어디야?"})).json()["id"])
        out = last_tool_output(fn)
        assert "37.5001, 127.0361" in out and "around Yeoksam-dong, Gangnam-gu, Seoul" in out and "OpenStreetMap" in out
        await c.post("/api/briefing/location", json={"lat": 37.5001, "lon": 127.0361, "name": "Seoul", "sublocality": "Yeoksam 1-dong"})
        await follow(c, (await c.post("/api/turns", json={"text": "지금은?"})).json()["id"])
        out = last_tool_output(fn)
        assert "in Yeoksam 1-dong, Seoul" in out and "around" not in out   # the phone's own neighbourhood: no lookup


async def test_no_location_asks_with_a_card_and_the_reply_goes_on_once_the_place_comes(tmp_path):
    fn = scripted(call("my_location", {"reason": "근처 주차장을 찾으려고요"}), say("찾았어요."))
    async with Running(location_app(tmp_path, fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        tid = (await c.post("/api/turns", json={"text": "근처 주차장"})).json()["id"]
        item = await waiting_item(c)
        assert item["kind"] == "location" and item["reason"] == "근처 주차장을 찾으려고요" and item["settings"] is False
        assert (await c.post(f"/api/location-requests/{item['approval_id']}", json={"allowed": True})).status_code == 422
        r = await c.post(f"/api/location-requests/{item['approval_id']}",
                         json={"allowed": True, "place": {"lat": 37.5001, "lon": 127.0361, "sublocality": "Yeoksam 1-dong"}})
        assert r.json() == {"ok": True, "state": "allowed"}
        events = await follow(c, tid)
        states = [e["state"] for e in events if e["type"] == "location_request"]
        assert states == ["waiting", "allowed"]
        assert "37.5001, 127.0361" in last_tool_output(fn) and "Yeoksam 1-dong" in last_tool_output(fn)
        assert (await c.post(f"/api/location-requests/{item['approval_id']}", json={"allowed": False})).status_code == 404
        assert (await c.get("/api/approvals/pending")).json()["items"] == []


async def test_a_refusal_is_told_to_the_agent_and_the_next_card_offers_settings(tmp_path):
    fn = scripted(call("my_location"), say("알겠어요."), call("my_location"), say("네."))
    async with Running(location_app(tmp_path, fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        tid = (await c.post("/api/turns", json={"text": "근처 카페"})).json()["id"]
        item = await waiting_item(c)
        await c.post(f"/api/approvals/{item['approval_id']}", json={"approve": False})   # Not now on the card
        await follow(c, tid)
        assert "did not allow location" in last_tool_output(fn)
        tid = (await c.post("/api/turns", json={"text": "다시"})).json()["id"]
        item = await waiting_item(c)
        assert item["settings"] is True                               # refused before: the card offers the phone's Settings
        await c.post(f"/api/location-requests/{item['approval_id']}", json={"allowed": False})
        await follow(c, tid)


async def test_an_unanswered_card_runs_out_like_an_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "APPROVAL_TIMEOUT_SECONDS", 0.3)
    fn = scripted(call("my_location"), say("어디 계세요?"))
    async with Running(location_app(tmp_path, fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "근처 약국"})).json()["id"])
        assert [e["state"] for e in events if e["type"] == "location_request"] == ["waiting", "expired"]
        assert "was not answered" in last_tool_output(fn) and "Ask the user where they are" in last_tool_output(fn)
