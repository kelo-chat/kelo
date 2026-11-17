"""The Android device as a live source (android/live.py, android/card.py) through a fake bridge: no picture is fetched without a viewer, at
most 3 a second, the same picture is not encoded again, taps are mapped from the frame to the device, typed text is forwarded and never
logged, taking the phone stops the agent's android_* steps until it is given back, the routes (key `android`) exist only with a device,
and the phone card of the chat is throttled."""

import asyncio
import base64
import functools
import io
import json
import logging

import httpx
import pytest
import websockets
from PIL import Image

from assistant.agent import ToolResult, run_agent, ui_sink
from assistant.android import live as live_mod
from assistant.android.card import DeviceCard
from assistant.android.device import DeviceClient
from assistant.android.live import OWNER_USING, DeviceLive
from assistant.android.screen import ScreenReader
from assistant.android.tools import AndroidSession, android_tools
from assistant.providers import Done, TextDelta
from assistant.server import create_app
from assistant.tools.browser import browser_owner
from assistant.tools.live import LiveEnded, LiveError

from test_agent_profile import app_settings
from test_browser_live import calling, ws_url
from test_server import AUTH, Running
from test_toolbox import scripted

TOKEN = "t" * 40
SECRET = "비밀번호 Hunter2"
ON = {"android_url": "http://android:8090", "android_token": TOKEN, "android_device": "192.168.1.50:5555"}


def png(w=1080, h=2340, color=(30, 60, 90), dot=None):
    img = Image.new("RGB", (w, h), color)
    if dot:
        img.putpixel(dot, (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Clock:
    """Fake time: a sleep moves it on (and lets other tasks run)."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


class Bridge:
    """The bridge: a picture the tests change, every call recorded."""

    def __init__(self, picture=None):
        self.png, self.log, self.frames, self.fail_text = picture or png(), [], 0, None

    def __call__(self, request):
        path, body = request.url.path, (json.loads(request.content) if request.content else {})
        if path == "/status":
            return httpx.Response(200, json={"connected": True, "size": [1080, 2340], "app": "", "android": "15", "device": "x"})
        if path == "/screen":
            assert request.url.params["image"] in ("only", "0", "1")
            if request.url.params["image"] != "0":   # a read of the screen's items (the owner-holds-phone look) is no picture
                self.frames += 1
            return httpx.Response(200, json={"png": self.png})
        self.log.append((path, body))
        if path == "/text" and self.fail_text:
            return httpx.Response(502, json={"error": self.fail_text})
        return httpx.Response(200, json={"ok": True})

    def sent(self, path):
        return [b for p, b in self.log if p == path]


def client_of(bridge):
    return DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(bridge)))


def make(bridge=None, **kw):
    bridge = bridge or Bridge()
    return bridge, DeviceLive(client_of(bridge), runner=False, **kw)


# ------------------------------------------------------------------------------------------------------------ frames
async def test_no_picture_is_fetched_without_a_viewer():
    bridge, live = make()
    await live.step()
    live.take("android", "d1")                      # the owner holding the phone is not a viewer
    await live.step()
    assert bridge.frames == 0 and live.latest("android") is None
    await live.join("android", "v1")
    await live.step()
    assert bridge.frames == 1 and live.latest("android").seq == 1
    await live.leave("android", "v1")
    await live.step()
    assert bridge.frames == 1                       # the last viewer left: nothing more is asked of the device


async def test_frames_are_jpeg_at_most_720_wide_and_carry_the_device_size():
    _, live = make()
    cast = await live.join("android", "v1")
    await live.step()
    f = live.latest("android")
    assert (f.w, f.h) == (720, 1560) and f.jpeg[:2] == b"\xff\xd8" and f.meta == {"deviceWidth": 1080, "deviceHeight": 2340}
    assert f.header()["css_w"] == 1080 and cast.settings == {"max_width": 720, "quality": 55, "fps": 3, "every_nth_frame": 1}


async def test_the_phone_frames_always_say_a_secret_field_shows():
    _, live = make()
    await live.join("android", "v1")
    await live.step()
    assert live.latest("android").header()["secure"] is True


async def test_at_most_three_pictures_a_second_and_only_while_watched():
    bridge = Bridge()
    clock = Clock()
    live = DeviceLive(client_of(bridge), clock=clock, sleep=clock.sleep)
    await live.join("android", "v1", fps=15)       # the app asks for 15: the cap is 3
    assert live.cast.settings["fps"] == 3
    while clock.now < 10.0:
        await asyncio.sleep(0)
        live.touch("android", "v1")
    await live.leave("android", "v1")
    assert 25 <= bridge.frames <= 31, bridge.frames  # 10 s at 3 a second (+ the first at once)
    seen = bridge.frames
    await asyncio.sleep(0.05)
    assert bridge.frames == seen


async def test_the_same_picture_is_not_encoded_again_and_sends_no_new_frame(monkeypatch):
    bridge, live = make()
    calls = []
    real = live_mod.encode
    monkeypatch.setattr(live_mod, "encode", lambda *a: calls.append(a) or real(*a))
    await live.join("android", "v1")
    await live.step()
    await live.step()
    await live.step()
    assert len(calls) == 1 and live.latest("android").seq == 1 and bridge.frames == 3
    bridge.png = png(color=(200, 10, 10))
    await live.step()
    assert len(calls) == 2 and live.latest("android").seq == 2
    assert await live.wait_frame("android", 2, 0.05) is None and (await live.wait_frame("android", 1, 0.05)).seq == 2


async def test_a_silent_viewer_is_dropped_and_a_failing_bridge_ends_the_view():
    bridge, live = make()
    live.clock = clock = Clock()
    cast = await live.join("android", "v1")
    clock.now = 61.0
    await live.step()
    assert live.watching("android") == 0 and live.end_reason("android", "v1", cast) == "tab_closed"
    with pytest.raises(LiveEnded):
        await live.wait_frame("android", 0, 0.01)
    bridge.png = ""                                  # the device sends no picture
    cast = await live.join("android", "v2")
    for _ in range(5):
        await live.step()
    assert live.end_reason("android", "v2", cast) == "tab_closed"


# ------------------------------------------------------------------------------------------------------------ control and input
async def test_taps_swipes_and_keys_are_mapped_to_the_device_and_need_the_controller():
    bridge, live = make()
    await live.join("android", "v1", max_width=360)
    await live.step()
    f = live.latest("android")
    assert (f.w, f.h) == (360, 780)                 # a 360x780 frame of a 1080x2340 device
    with pytest.raises(LiveError) as e:
        await live.input("android", "d1", {"type": "tap", "x": 1, "y": 1})
    assert e.value.status == 409 and e.value.code == "not_controller"
    live.take("android", "d1")
    with pytest.raises(LiveError) as e:
        live.take("android", "d2")
    assert e.value.status == 409
    out = await live.input("android", "d1", {"type": "tap", "x": 180, "y": 390})
    assert bridge.sent("/tap") == [{"x": 540, "y": 1170}] and out["x"] == 540 and out["y"] == 1170
    await live.input("android", "d1", {"type": "swipe", "x1": 180, "y1": 600, "x2": 180, "y2": 100, "ms": 250})
    assert bridge.sent("/swipe") == [{"x1": 540, "y1": 1800, "x2": 540, "y2": 300, "ms": 250}]
    await live.input("android", "d1", {"type": "tap", "x": 9999, "y": -5})
    assert bridge.sent("/tap")[-1] == {"x": 1079, "y": 0}      # kept on the screen
    for name, sent in (("back", "back"), ("home", "home"), ("recents", "recent"), ("enter", "enter")):
        await live.input("android", "d1", {"type": "key", "key": name})
        assert bridge.sent("/key")[-1] == {"key": sent}
    for bad in ({"type": "key", "key": "power"}, {"type": "tap", "x": "a", "y": 1}, {"type": "pinch"}, {"type": "text", "text": ""}):
        with pytest.raises(LiveError) as e:
            await live.input("android", "d1", bad)
        assert e.value.status == 422


async def test_a_tap_needs_a_frame_first():
    _, live = make()
    live.take("android", "d1")
    with pytest.raises(LiveError) as e:
        await live.input("android", "d1", {"type": "tap", "x": 1, "y": 1})
    assert e.value.code == "no_frame"


async def test_typed_text_is_forwarded_and_never_logged_or_kept(caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("assistant").addHandler(caplog.handler)
    bridge, live = make()
    live.take("android", "d1")
    out = await live.input("android", "d1", {"type": "text", "text": SECRET})
    assert bridge.sent("/text") == [{"text": SECRET}] and out == {"ok": True, "type": "text", "chars": len(SECRET)}
    assert SECRET not in json.dumps(out) and SECRET not in caplog.text and SECRET not in repr(vars(live))
    bridge.fail_text = f"could not type {SECRET}"
    with pytest.raises(LiveError) as e:
        await live.input("android", "d1", {"type": "text", "text": SECRET})
    assert e.value.status == 502 and SECRET not in e.value.message and SECRET not in caplog.text


async def test_the_owner_taking_the_phone_stops_the_agents_steps_and_giving_back_releases_them():
    bridge = Bridge()
    live = DeviceLive(client_of(bridge), runner=False, control_wait=0.05)
    session = AndroidSession(live.client, ScreenReader())
    tools = android_tools(live.client, session=session, hold=live.hold_for_user)
    assert all(t.hold is not None for t in tools)
    live.take("android", "d1")
    events = []

    async def agent(*calls):
        token = browser_owner.set("owner")
        try:
            async for ev in run_agent("p", "m", [{"role": "user", "content": "x"}], tools, stream_fn=calling(*calls)):
                events.append(ev)
        finally:
            browser_owner.reset(token)

    await agent(("android_swipe", '{"direction": "down"}'))
    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.output == OWNER_USING and bridge.sent("/swipe") == []     # it waited, then said so, and did not act
    assert await live.hold_for_user() == OWNER_USING
    waiting = asyncio.ensure_future(live.hold_for_user())                   # a step waiting meanwhile goes on at once when given back
    live.control_wait = 5.0
    await asyncio.sleep(0.01)
    await live.give_back("android", "d1")
    assert await asyncio.wait_for(waiting, 1) is None and live.holder("android") is None
    events.clear()
    await agent(("android_swipe", '{"direction": "down"}'))
    assert len(bridge.sent("/swipe")) == 1


async def test_the_phone_goes_back_to_the_agent_after_the_grace_time_without_a_connection():
    _, live = make()
    live.clock = clock = Clock()
    live.take("android", "d1")
    live.connected("android", "d1", +1)
    clock.now = 100.0
    await live.step()
    assert live.holder("android") == "d1"           # a live connection is open
    live.connected("android", "d1", -1)
    clock.now = 120.0
    await live.step()
    assert live.holder("android") == "d1"           # inside the grace time
    clock.now = 140.0
    await live.step()
    assert live.holder("android") is None


# ------------------------------------------------------------------------------------------------------------ routes
def app_with_device(tmp_path, **kw):
    bridge = Bridge()
    settings = app_settings(tmp_path, home_dir=str(tmp_path / "home"), **{**ON, **kw})
    http = functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(bridge))
    return bridge, create_app(settings, stream_fn=scripted([TextDelta("ok"), Done("stop")]), with_schedule_check=False, android_http=http)


async def test_the_key_android_is_404_when_no_device_is_set_up(tmp_path):
    settings = app_settings(tmp_path, home_dir=str(tmp_path / "home"))
    app = create_app(settings, stream_fn=scripted([TextDelta("ok"), Done("stop")]), with_schedule_check=False)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/browser/sessions/android/live-ticket")).status_code == 404
        assert (await c.get("/api/browser/sessions/android/frame?wait=0")).status_code == 404
        assert (await c.post("/api/browser/sessions/android/control", json={"take": True})).status_code == 404
        assert (await c.post("/api/browser/sessions/android/input", json={"type": "key", "key": "home"})).status_code == 404
        assert (await c.get("/api/browser/sessions")).json() == {"available": False, "items": []}


async def test_the_sessions_list_shows_the_device_only_when_it_is_set_up(tmp_path):
    _, app = app_with_device(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        body = (await c.get("/api/browser/sessions")).json()
        assert body["available"] is True
        (item,) = body["items"]
        assert (item["key"], item["kind"], item["title"], item["width"], item["height"]) == ("android", "device", "x", 1080, 2340)
        assert item["id"] == "android" and item["source"] == "device" and item["controller"] == "agent"


async def test_watching_the_device_over_http_and_websocket_and_taking_it_over(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    bridge, app = app_with_device(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.get("/api/browser/sessions/android/frame?wait=2")             # polling: the first frame, no WebSocket
        assert r.status_code == 200 and r.headers["X-Frame-W"] == "720" and r.headers["X-Frame-Css-W"] == "1080" and r.content[:2] == b"\xff\xd8"
        assert r.headers["X-Controller"] == "agent"
        assert (await c.delete("/api/browser/sessions/android/frame")).json() == {"ok": True}
        ticket = (await c.post("/api/browser/sessions/android/live-ticket")).json()["ticket"]
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/android/live?ticket={ticket}")) as ws:
            st = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert st["type"] == "status" and st["id"] == "android" and st["controller"] == "agent" and st["viewers"] == 1
            head = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert head["type"] == "frame" and head["w"] == 720 and head["h"] == 1560 and head["title"] == "Android"
            assert len(await asyncio.wait_for(ws.recv(), 2)) == head["bytes"]
            await ws.send(json.dumps({"type": "control", "take": True}))
            while True:
                msg = await asyncio.wait_for(ws.recv(), 2)
                if isinstance(msg, str) and json.loads(msg).get("type") == "status" and json.loads(msg)["you_control"]:
                    break
            await ws.send(json.dumps({"type": "key", "key": "home", "ack": True}))
            await ws.send(json.dumps({"type": "text", "text": SECRET, "ack": True}))
            await ws.send(json.dumps({"type": "tap", "x": 360, "y": 780, "ack": True}))
            await asyncio.sleep(0.3)
        assert bridge.sent("/key") == [{"key": "home"}] and bridge.sent("/text") == [{"text": SECRET}]
        assert bridge.sent("/tap") == [{"x": 540, "y": 1170}]
        assert SECRET not in caplog.text
        again = (await c.post("/api/browser/sessions/android/live-ticket")).json()["ticket"]
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/android/live?ticket={again}")) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 2))["type"] == "status"
        r = await c.post("/api/browser/sessions/android/control", json={"take": False})
        assert r.json()["controller"] == "agent"
        r = await c.post("/api/browser/sessions/android/control", json={"take": True})
        assert r.json() == {"id": "android", "controller": "user", "controlled_by": "owner", "you_control": True}
        assert (await c.post("/api/browser/sessions/android/input", json={"type": "key", "key": "back"})).json()["ok"] is True
        assert (await c.post("/api/browser/sessions/android/control", json={"take": False})).json()["controller"] == "agent"
        assert (await c.post("/api/browser/sessions/android/input", json={"type": "key", "key": "back"})).status_code == 409


# ------------------------------------------------------------------------------------------------------------ the phone card
async def test_the_phone_card_is_a_device_event_at_most_one_per_two_seconds_with_a_small_picture():
    bridge = Bridge()
    clock = Clock()
    card = DeviceCard(client_of(bridge), clock=clock)
    got = []

    async def sink(item):
        got.append(item)

    token, owner = ui_sink.set(sink), browser_owner.set("owner")
    try:
        first = await card.show("Tapping on the phone")
        clock.now = 1.0
        assert await card.show("Typing on the phone") is None                # too soon: no event, no picture fetched
        assert bridge.frames == 1
        clock.now = 2.5
        second = await card.show("Typing on the phone")
        forced = await card.show("Waiting", force=True)
    finally:
        ui_sink.reset(token)
        browser_owner.reset(owner)
    assert [e["status"] for e in got] == ["Tapping on the phone", "Typing on the phone", "Waiting"] and first and second and forced
    e = got[0]
    assert e["type"] == "device" and e["key"] == "android" and e["image"].startswith("data:image/jpeg;base64,")
    assert 0 < e["bytes"] <= 120_000 and e["width"] <= 720 and e["height"] > e["width"] and e["at"] > 0
    assert len(base64.b64decode(e["image"].split(",", 1)[1])) == e["bytes"]


async def test_no_card_without_a_chat_listening_and_a_card_without_a_picture_when_the_bridge_is_away():
    bridge = Bridge()
    card = DeviceCard(client_of(bridge))
    assert await card.show("x") is None and bridge.frames == 0               # no chat listening: nothing fetched
    bridge.png = ""
    got = []

    async def sink(item):
        got.append(item)

    token = ui_sink.set(sink)
    try:
        await card.show("Looking at the phone")
    finally:
        ui_sink.reset(token)
    assert got[0]["image"] is None and got[0]["status"] == "Looking at the phone"


async def test_the_android_tools_show_the_card_after_a_step_and_the_server_wires_it_with_the_hold(tmp_path, monkeypatch):
    from assistant.android import tools as android
    monkeypatch.setattr(android, "SETTLE_SECONDS", 0)
    bridge = Bridge()
    client = client_of(bridge)
    tools = {t.name: t for t in android_tools(client, session=AndroidSession(client, ScreenReader()), card=DeviceCard(client, interval=0),
                                              handover=object())}
    assert "phone card" in tools["android_show"].description
    got = []

    async def sink(item):
        got.append(item)

    token, owner = ui_sink.set(sink), browser_owner.set("owner")
    try:
        await tools["android_key"].handler({"key": "home"})
    finally:
        ui_sink.reset(token)
        browser_owner.reset(owner)
    assert [e["type"] for e in got] == ["device"] and got[0]["status"] == "Pressing a key on the phone"
    _, app = app_with_device(tmp_path)
    wired = {t.name: t for t in app.state.tools if t.name.startswith("android_")}
    assert wired["android_tap"].hold is not None and wired["android_look"].hold is not None
