"""GET /api/android and POST /api/android/check (api/android_device.py) through a fake bridge: not configured, connected with every field,
the bridge down, and neither the device's address nor the bridge's token in any answer."""

import functools

import httpx

from assistant.providers import Done, TextDelta
from assistant.server import create_app

from test_agent_profile import app_settings
from test_server import AUTH, Running
from test_toolbox import scripted

TOKEN = "t" * 40
ADDRESS = "192.168.1.50:5555"
ON = {"android_url": "http://android:8090", "android_token": TOKEN, "android_device": ADDRESS}
STATUS = {"connected": True, "device": ADDRESS, "android": "15", "model": "Pixel 7", "size": [720, 1280],
          "app": "com.android.launcher3/.Main", "play_store": True, "korean_input": False}


class Bridge:
    """The bridge's /status, one answer at a time; counts the calls."""

    def __init__(self, status=STATUS, down=False):
        self.status, self.down, self.calls = status, down, []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if self.down:
            raise httpx.ConnectError("refused")
        assert request.url.path == "/status"
        return httpx.Response(200, json=self.status)


def make(tmp_path, bridge, **kw):
    settings = app_settings(tmp_path, home_dir=str(tmp_path / "home"), **{**ON, **kw})
    http = functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(bridge))
    return create_app(settings, stream_fn=scripted([TextDelta("ok"), Done("stop")]), with_schedule_check=False, android_http=http)


async def test_not_configured_answers_200_with_nothing_filled(tmp_path):
    bridge = Bridge()
    app = make(tmp_path, bridge, android_device="")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for r in (await c.get("/api/android"), await c.post("/api/android/check")):
            assert r.status_code == 200
            assert r.json() == {"configured": False, "connected": False, "model": "", "android_version": "", "screen": None,
                                "play_store": False, "korean_input": False, "checked_at": 0.0}
    assert bridge.calls == []          # nothing asked of the bridge


async def test_connected_gives_every_field_and_check_reads_fresh(tmp_path):
    bridge = Bridge()
    app = make(tmp_path, bridge)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        body = (await c.get("/api/android")).json()
        assert {k: v for k, v in body.items() if k != "checked_at"} == {
            "configured": True, "connected": True, "model": "Pixel 7", "android_version": "15",
            "screen": {"width": 720, "height": 1280}, "play_store": True, "korean_input": False}
        assert body["checked_at"] > 0
        bridge.status = {**STATUS, "korean_input": True, "model": "Pixel 8"}
        r = await c.post("/api/android/check")
        assert r.status_code == 200 and r.json()["korean_input"] is True and r.json()["model"] == "Pixel 8"
    assert [call[:2] for call in bridge.calls] == [("GET", "/status")] * 2 and bridge.calls[0][2] == f"Bearer {TOKEN}"


async def test_a_device_that_does_not_answer_is_not_connected(tmp_path):
    bridge = Bridge(status={**STATUS, "connected": False, "android": "", "size": [0, 0], "model": "", "play_store": False})
    app = make(tmp_path, bridge)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        body = (await c.get("/api/android")).json()
        assert body["configured"] is True and body["connected"] is False and body["screen"] is None and body["model"] == ""


async def test_the_bridge_down_is_connected_false_not_an_error(tmp_path):
    app = make(tmp_path, Bridge(down=True))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for r in (await c.get("/api/android"), await c.post("/api/android/check")):
            assert r.status_code == 200
            body = r.json()
            assert body["configured"] is True and body["connected"] is False and body["screen"] is None
            assert body["play_store"] is False and body["korean_input"] is False and body["checked_at"] > 0


async def test_the_address_and_the_token_are_in_no_answer(tmp_path):
    for bridge in (Bridge(), Bridge(down=True)):
        app = make(tmp_path / ("a" if not bridge.down else "b"), bridge)
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            for r in (await c.get("/api/android"), await c.post("/api/android/check")):
                assert ADDRESS not in r.text and "192.168" not in r.text and TOKEN not in r.text and "android:8090" not in r.text
                assert "device" not in r.json() and "app" not in r.json()


async def test_it_needs_the_owner_token(tmp_path):
    app = make(tmp_path, Bridge())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/android")).status_code == 401
        assert (await c.post("/api/android/check")).status_code == 401
