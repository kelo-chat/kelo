"""Live view of a browser tab (CDP screencast) and the user taking control, against a fake DevTools session."""

import asyncio
from types import SimpleNamespace
import base64
import io
import json
import logging

import httpx
import pytest
import websockets
from PIL import Image

from assistant.agent import ApprovalRequest, ToolResult, run_agent
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import _NoTickets, create_app
from assistant.tools import live as live_mod
from assistant.tools.browser import CHAT_CONTROL_WAIT_SECONDS, CONTROLLED_CHAT_NOTE, CONTROLLED_NOTE, BrowserSession, browser_owner, browser_tools, hold_patience
from assistant.tools.live import Frame, LiveError, LiveHub, Tickets, map_delta, map_point, page_point, pump_frames, settings_from

from test_agent_profile import app_settings
from test_server import AUTH, Running, follow

META = {"offsetTop": 0, "pageScaleFactor": 1, "deviceWidth": 1440, "deviceHeight": 900, "scrollOffsetX": 0, "scrollOffsetY": 0}


def jpeg(w=720, h=450, color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=50)
    return buf.getvalue()


class FakeCDP:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self.handlers: dict[str, list] = {}
        self.detached = False
        self.casting = False
        self.history = {"currentIndex": 1, "entries": [{"id": 10}, {"id": 11}, {"id": 12}]}
        self.secure = None           # what the secret-field look answers (None: an answer without a value)
        self.evaluate_error = False

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    async def send(self, method, params=None):
        self.sent.append((method, dict(params or {})))
        if method == "Page.startScreencast":
            self.casting = True
        elif method == "Page.stopScreencast":
            self.casting = False
        elif method == "Page.getNavigationHistory":
            return self.history
        elif method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {"clientWidth": 1440, "clientHeight": 900, "pageX": 0, "pageY": 300, "scale": 1}}
        elif method == "Page.captureScreenshot":
            return {"data": base64.b64encode(jpeg(720, 450)).decode()}
        elif method == "Runtime.evaluate" and (params or {}).get("expression") == live_mod.SECURE_JS:
            if self.evaluate_error:
                raise RuntimeError("the page is going away")
            if self.secure is not None:
                return {"result": {"type": "boolean", "value": self.secure}}
        return {}

    async def detach(self):
        self.detached = True

    def frame(self, sid=1, w=720, h=450, meta=None):
        for fn in self.handlers.get("Page.screencastFrame", []):
            fn({"data": base64.b64encode(jpeg(w, h)).decode(), "sessionId": sid, "metadata": dict(meta or META)})

    def methods(self, name):
        return [p for m, p in self.sent if m == name]


class FakePage:
    def __init__(self, url="https://shop.example.com/cart"):
        self.url, self._closed, self.gone_to = url, False, []

    def is_closed(self):
        return self._closed

    async def title(self):
        return "장바구니"

    async def evaluate(self, js):
        return "page text"

    async def goto(self, url, **kw):
        self.gone_to.append(url)
        self.url = url

    async def close(self):
        self._closed = True


def public(host, port):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def make_session(page=None, key="owner"):
    cdps: list[FakeCDP] = []

    async def cdp(_page):
        c = FakeCDP()
        cdps.append(c)
        return c

    sess = BrowserSession(connect=None, cdp=cdp, resolver=public)
    sess._pages[key] = page or FakePage()
    return sess, cdps


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


async def settle(n=5):
    for _ in range(n):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------------------------------------- frames
async def test_frames_are_acked_shared_by_viewers_and_the_screencast_stops_when_the_last_viewer_leaves():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    await hub.join("owner", "b")
    assert len(cdps) == 1
    cdp = cdps[0]
    start = cdp.methods("Page.startScreencast")
    assert start == [{"format": "jpeg", "quality": 55, "maxWidth": 720, "maxHeight": 1440, "everyNthFrame": 1}]   # bandwidth defaults
    cdp.frame(sid=7)
    frame = await hub.wait_frame("owner", 0, 1.0)
    assert (frame.seq, frame.w, frame.h, frame.url) == (1, 720, 450, "https://shop.example.com/cart")
    assert frame.jpeg[:2] == b"\xff\xd8"
    await settle()
    assert cdp.methods("Page.screencastFrameAck") == [{"sessionId": 7}]
    assert (await hub.wait_frame("owner", 1, 0.05)) is None   # nothing newer
    await hub.leave("owner", "a")
    assert cdp.casting and not cdp.detached
    await hub.leave("owner", "b")
    assert not cdp.casting and cdp.detached and hub.watching("owner") == 0
    with pytest.raises(live_mod.LiveEnded):
        await hub.wait_frame("owner", 0, 0.01)


async def test_a_viewer_silent_for_a_minute_is_dropped_and_the_screencast_stops():
    clock = Clock()
    sess, cdps = make_session()
    hub = LiveHub(sess, clock, reaper=False)
    await hub.join("owner", "a")
    await hub.join("owner", "b")
    clock.t += 40
    hub.touch("owner", "b")
    clock.t += 30
    assert await hub.reap() == [("owner", "a")]
    assert cdps[0].casting
    clock.t += 61
    assert await hub.reap() == [("owner", "b")]
    assert not cdps[0].casting and "owner" not in hub.casts


async def test_a_still_page_gets_one_picture_and_a_closed_tab_ends_the_view(monkeypatch):
    monkeypatch.setattr(live_mod, "SEED_AFTER", 0.01)
    page = FakePage()
    sess, cdps = make_session(page)
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    frame = await hub.wait_frame("owner", 0, 1.0)
    assert frame.meta["deviceWidth"] == 1440 and frame.meta["scrollOffsetY"] == 300 and frame.w == 720
    assert cdps[0].methods("Page.captureScreenshot")[0]["clip"]["scale"] == 0.5
    page._closed = True
    await hub.reap()
    with pytest.raises(live_mod.LiveEnded):
        await hub.wait_frame("owner", 0, 0.01)


async def test_a_slow_phone_skips_frames_and_never_queues_more_than_one():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    cdp = cdps[0]
    gate, sent = asyncio.Event(), []

    async def slow_send(frame):
        sent.append(frame.seq)
        if len(sent) == 1:
            await gate.wait()   # the phone's connection is stuck on the first frame

    pump = asyncio.ensure_future(pump_frames(hub, "owner", slow_send, fps=lambda: 15))
    cdp.frame(sid=1)
    await asyncio.sleep(0.05)
    for i in range(2, 11):   # nine more frames while the phone is stuck
        cdp.frame(sid=i)
        await settle()
    await asyncio.sleep(0.05)
    assert sent == [1]
    gate.set()
    await asyncio.sleep(0.3)
    assert sent == [1, 10]   # straight to the newest; frames 2..9 were never sent nor kept
    pump.cancel()


async def test_quality_requests_are_clamped_and_restart_the_screencast():
    assert settings_from(1080, 95, 30) == {"max_width": 1080, "quality": 80, "fps": 15, "every_nth_frame": 1}
    assert settings_from(100, 10, 0)["max_width"] == 360 and settings_from(100, 10, 0)["quality"] == 40 and settings_from(1, 1, 0)["fps"] == 1
    assert settings_from("x", None, True) == {"max_width": 720, "quality": 55, "fps": 10, "every_nth_frame": 1}
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    got = await hub.configure("owner", max_width=1080, quality=70, fps=5)
    assert got["max_width"] == 1080 and got["fps"] == 5
    starts = cdps[0].methods("Page.startScreencast")
    assert len(starts) == 2 and starts[1]["maxWidth"] == 1080 and starts[1]["quality"] == 70
    await hub.configure("owner", fps=8)   # only the rate: no restart
    assert len(cdps[0].methods("Page.startScreencast")) == 2


# ---------------------------------------------------------------------------------------------------------- coordinates
def test_frame_pixels_map_to_css_pixels_of_the_viewport():
    assert map_point(100, 50, 720, 450, META) == (200.0, 100.0)               # a frame at half size
    assert map_point(360, 225, 1080, 675, META) == (480.0, 300.0)             # 1080 px wide frame of the same screen
    assert map_point(100, 60, 720, 450, {**META, "offsetTop": 40}) == (200.0, 80.0)   # page area starts lower
    assert map_point(-5, 9999, 720, 450, META) == (0.0, 899.0)                 # kept inside the viewport
    # scroll does not change where the input lands (viewport coordinates); the page's zoom does (CSS px per screen unit)
    zoomed = {**META, "pageScaleFactor": 2, "scrollOffsetX": 10, "scrollOffsetY": 500}
    assert map_point(100, 50, 720, 450, zoomed) == (100.0, 50.0)       # zoomed in 2x: half as many CSS px per screen unit
    assert page_point(100.0, 50.0, zoomed) == (110.0, 550.0)
    assert map_point(100, 50, 720, 450, {**META, "pageScaleFactor": 0.5}) == (400.0, 200.0)   # zoomed out: more CSS px per screen unit
    assert map_delta(0, -100, 720, 450, {**META, "pageScaleFactor": 0.5}) == (0.0, -400.0)
    assert map_delta(0, -100, 720, 450, META) == (0.0, -200.0)


# ---------------------------------------------------------------------------------------------------------- tickets
def test_tickets_are_single_use_short_lived_and_bound_to_their_tab():
    clock = Clock()
    t = Tickets(clock)
    a = t.issue("owner", "3")
    assert t.redeem(a, "owner") == "3"
    assert t.redeem(a, "owner") is None             # used up
    b = t.issue("owner", "3")
    assert t.redeem(b, "task:1") is None            # another tab
    assert t.redeem(b, "owner") is None             # ...and spent by that try
    c = t.issue("owner", "3")
    clock.t += 31
    assert t.redeem(c, "owner") is None             # expired
    assert t.redeem("", "owner") is None and t.redeem("made-up", "owner") is None


def test_the_ticket_never_reaches_a_log_line():
    f = _NoTickets()
    rec = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, '%s - "WebSocket %s" [accepted]',
                            ("1.2.3.4:5", "/api/browser/sessions/owner/live?ticket=SECRETTICKET&fps=10"), None)
    f.filter(rec)
    assert "SECRETTICKET" not in rec.getMessage() and "fps=10" in rec.getMessage()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/x?token=SECRETTOKEN", "1.1", 200), None)
    f.filter(rec)
    assert "SECRETTOKEN" not in rec.getMessage()


# ---------------------------------------------------------------------------------------------------------- control
async def test_only_the_device_that_took_control_sends_input_and_handing_back_closes_the_purchase_window():
    clock = Clock()
    page = FakePage()
    sess, cdps = make_session(page)
    hub = LiveHub(sess, clock, reaper=False)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    with pytest.raises(LiveError) as e:
        await hub.input("owner", "1", {"type": "tap", "x": 1, "y": 1})
    assert e.value.code == "not_controller"
    hub.take("owner", "1")
    hub.connected("owner", "1", +1)              # a holder with a live connection keeps the tab against another device
    assert sess.controlled_by("owner") == "1" and hub.controller("owner") == "user"
    with pytest.raises(LiveError) as e:
        hub.take("owner", "2")
    assert e.value.status == 409
    with pytest.raises(LiveError):
        await hub.input("owner", "2", {"type": "tap", "x": 1, "y": 1})
    out = await hub.input("owner", "1", {"type": "tap", "x": 100, "y": 50})
    assert (out["x"], out["y"]) == (200.0, 100.0)
    mouse = cdps[0].methods("Input.dispatchMouseEvent")
    assert [m["type"] for m in mouse] == ["mouseMoved", "mousePressed", "mouseReleased"] and mouse[1]["x"] == 200.0 and mouse[1]["y"] == 100.0
    await hub.input("owner", "1", {"type": "scroll", "dy": 100})
    assert cdps[0].methods("Input.dispatchMouseEvent")[-1] == {"type": "mouseWheel", "x": 720.0, "y": 450.0, "deltaX": 0.0, "deltaY": 200.0}
    await hub.input("owner", "1", {"type": "key", "key": "Enter"})
    keys = cdps[0].methods("Input.dispatchKeyEvent")
    assert keys[0]["type"] == "keyDown" and keys[0]["text"] == "\r" and keys[0]["windowsVirtualKeyCode"] == 13 and keys[1]["type"] == "keyUp"
    await hub.input("owner", "1", {"type": "text", "text": "hunter2"})
    assert cdps[0].methods("Input.insertText") == [{"text": "hunter2"}]
    await hub.input("owner", "1", {"type": "back"})
    assert cdps[0].methods("Page.navigateToHistoryEntry") == [{"entryId": 10}]
    cdps[0].history["currentIndex"] = 2
    assert (await hub.input("owner", "1", {"type": "forward"}))["ok"] is False
    await hub.input("owner", "1", {"type": "reload"})
    assert cdps[0].methods("Page.reload") == [{}]
    for bad in ({"type": "key", "key": "F5"}, {"type": "tap", "x": "a", "y": 1}, {"type": "text", "text": ""}, {"type": "jump"}):
        with pytest.raises(LiveError) as e:
            await hub.input("owner", "1", bad)
        assert e.value.status == 422
    # a purchase the user allowed earlier must not carry over to the agent after the user's own clicks
    token = browser_owner.set("owner")
    try:
        sess.allow_purchase("shop.example.com")
        assert sess.purchase_active()
        with pytest.raises(LiveError):
            await hub.give_back("owner", "2")
        await hub.give_back("owner", "1")
        assert sess.controlled_by("owner") is None and not sess.purchase_active()
    finally:
        browser_owner.reset(token)


async def test_long_press_holds_the_button(monkeypatch):
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1")
    slept = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        slept.append(s)
        await real_sleep(0)

    monkeypatch.setattr(live_mod.asyncio, "sleep", fake_sleep)
    await hub.input("owner", "1", {"type": "long_press", "x": 10, "y": 10, "ms": 900})
    monkeypatch.setattr(live_mod.asyncio, "sleep", real_sleep)
    assert 0.9 in slept
    assert [m["type"] for m in cdps[0].methods("Input.dispatchMouseEvent")] == ["mouseMoved", "mousePressed", "mouseReleased"]


async def test_control_goes_back_to_the_agent_30_s_after_the_controller_disconnects():
    clock = Clock()
    sess, _ = make_session()
    hub = LiveHub(sess, clock, reaper=False)
    hub.take("owner", "1", by="handover")   # a hand-over hold: the 30 s grace (a button hold never times out: test_owner_hold.py)
    hub.connected("owner", "1", +1)
    clock.t += 120
    await hub.reap()
    assert sess.controlled_by("owner") == "1"        # connected: kept however long
    hub.connected("owner", "1", -1)
    clock.t += 20
    await hub.reap()
    assert sess.controlled_by("owner") == "1"        # within the grace time (a phone reconnecting)
    clock.t += 11
    await hub.reap()
    assert sess.controlled_by("owner") is None


async def test_handing_back_leaves_a_private_page_the_agent_may_not_open():
    page = FakePage("http://192.168.0.1/admin")
    sess, _ = make_session(page)
    sess._resolver = lambda host, port: [(2, 1, 6, "", ("192.168.0.1", port))]
    sess.take_control("owner", "1")
    await sess.release_control("owner")
    assert page.gone_to == ["about:blank"]


# ---------------------------------------------------------------------------------------------------------- the agent waits
def calling(*calls):
    turns = iter([[*(ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(calls)), Done("tool_calls")], [TextDelta("ok"), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(turns):
            yield ev
    return fn


async def test_agent_browser_steps_wait_while_the_user_controls_and_resume_after_release():
    sess, _ = make_session()
    tools = browser_tools(sess, approve_actions=False)
    sess.take_control("owner", "1")
    token = browser_owner.set("owner")
    try:
        events = []

        async def run():
            async for ev in run_agent("p", "m", [{"role": "user", "content": "x"}], tools, stream_fn=calling(("browser_read", "{}"))):
                events.append(ev)

        task = asyncio.ensure_future(run())
        await asyncio.sleep(0.1)
        assert not task.done() and not any(isinstance(e, ToolResult) for e in events)   # paused, not failed
        await sess.release_control("owner")
        await asyncio.wait_for(task, 2)
        result = next(e for e in events if isinstance(e, ToolResult))
        assert not result.is_error and "page text" in result.output
    finally:
        browser_owner.reset(token)


async def test_an_agent_step_that_waited_too_long_says_the_user_is_controlling_and_asks_nothing():
    sess, _ = make_session()
    sess.control_wait = 0.05
    tools = browser_tools(sess, approve_actions=True)
    sess.take_control("owner", "1")
    asked: list[ApprovalRequest] = []

    async def approve(req):
        asked.append(req)
        return True

    token = browser_owner.set("owner")
    try:
        events = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "x"}], tools, approve=approve,
                                               stream_fn=calling(("browser_click", '{"selector": "text=Buy"}')))]
    finally:
        browser_owner.reset(token)
    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.output == CONTROLLED_NOTE.format(minutes=1) and asked == []
    assert sess._pages["owner"].gone_to == []


# ---------------------------------------------------------------------------------------------------------- over HTTP and WebSocket
def ws_url(srv, path):
    return srv.url.replace("http://", "ws://") + path


async def recv_until(ws, kind, limit=20):
    for _ in range(limit):
        msg = await asyncio.wait_for(ws.recv(), 2)
        if isinstance(msg, str) and json.loads(msg).get("type") == kind:
            return json.loads(msg)
    raise AssertionError(f"no {kind} message")


async def test_live_websocket_with_a_one_time_ticket_frames_status_control_and_input(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    sess, cdps = make_session()
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        server_logs = [logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access", "assistant")]
        levels = [lg.level for lg in server_logs]
        for lg in server_logs:   # the server's loggers do not reach the root logger: listen to them directly, at every level
            lg.addHandler(caplog.handler)
            lg.setLevel(logging.DEBUG)
        r = (await c.post("/api/browser/sessions/owner/live-ticket")).json()
        assert r["expires_in"] == 30 and len(r["ticket"]) > 20
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={r['ticket']}")) as ws:
            st = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert st["type"] == "status" and st["controller"] == "agent" and st["you_control"] is False and st["viewers"] == 1
            cdps[0].frame(sid=3)
            head = json.loads(await asyncio.wait_for(ws.recv(), 2))
            body = await asyncio.wait_for(ws.recv(), 2)
            assert head["type"] == "frame" and head["seq"] == 1 and (head["w"], head["h"]) == (720, 450) and head["css_w"] == 1440
            assert isinstance(body, bytes) and len(body) == head["bytes"] and body[:2] == b"\xff\xd8"
            await ws.send(json.dumps({"type": "ping"}))
            assert (await recv_until(ws, "pong"))["ts"]
            await ws.send(json.dumps({"type": "quality", "max_width": 1080, "quality": 60, "fps": 12}))
            q = await recv_until(ws, "quality")
            assert (q["max_width"], q["quality"], q["fps"]) == (1080, 60, 12)
            await ws.send(json.dumps({"type": "tap", "x": 10, "y": 10}))
            assert (await recv_until(ws, "error"))["code"] == "not_controller"
            await ws.send(json.dumps({"type": "control", "take": True}))
            st = await recv_until(ws, "status")
            assert st["controller"] == "user" and st["you_control"] is True and st["label"] == "You are in control" and st["controlled_by"] == "owner"
            # the agent screen and the tab list say so too
            status = (await c.get("/api/agent/status")).json()
            assert status["controller"] == "user" and status["controlled_by"] == "owner"
            tab = (await c.get("/api/browser/sessions", params={"thumbnails": False})).json()["items"][0]
            assert tab["controller"] == "user" and tab["status_text"] == "You are in control" and tab["viewers"] == 1
            await ws.send(json.dumps({"type": "tap", "x": 100, "y": 50, "ack": True}))
            ok = await recv_until(ws, "input_ok")
            assert (ok["x"], ok["y"]) == (200.0, 100.0)
            await ws.send(json.dumps({"type": "text", "text": "pa$$w0rd-SECRET"}))
            await ws.send(json.dumps({"type": "text", "text": "x" * 3000}))
            err = await recv_until(ws, "error")
            assert err["code"] == "bad_event" and "x" * 50 not in json.dumps(err)
            assert {"text": "pa$$w0rd-SECRET"} in cdps[0].methods("Input.insertText")
            # REST fallback for the same device
            assert (await c.post("/api/browser/sessions/owner/input", json={"type": "key", "key": "Tab"})).json()["ok"] is True
            r2 = await c.post("/api/browser/sessions/owner/input", json={"type": "text", "text": "rest-SECRET-typed"})
            assert r2.json() == {"ok": True, "type": "text", "chars": 17}
            await ws.send(json.dumps({"type": "control", "take": False}))
            st = await recv_until(ws, "status")
            assert st["controller"] == "agent" and st["you_control"] is False
        await asyncio.sleep(0.05)
        assert "owner" not in sess.controls() and sess.controlled_by("owner") is None
        assert not cdps[0].casting                       # the last viewer left: screencast stopped
        # the ticket was spent: a second use is refused
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={r['ticket']}")) as ws:
            assert json.loads(await ws.recv())["code"] == "unauthorized"
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await ws.recv()
            assert closed.value.rcvd.code == 4401
        # no such tab: a clean close, not a broken upgrade
        t2 = (await c.post("/api/browser/sessions/task:nope/live-ticket")).json()["ticket"]
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/task:nope/live?ticket={t2}")) as ws:
            assert json.loads(await ws.recv()) == {"type": "ended", "reason": "no_tab"}
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await ws.recv()
            assert closed.value.rcvd.code == 4404
        # the bearer header works instead of a ticket (native apps can send it)
        async with websockets.connect(ws_url(srv, "/api/browser/sessions/owner/live"), additional_headers=AUTH) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 2))["type"] == "status"
    for lg, level in zip(server_logs, levels):
        lg.removeHandler(caplog.handler)
        lg.setLevel(level)
    server_lines = "\n".join(f"{rec.name} {rec.getMessage()}" for rec in caplog.records
                              if not rec.name.startswith(("websockets.client", "httpx", "httpcore")))   # what the test client logs is not the server's
    assert "WebSocket /api/browser/sessions/owner/live?ticket=…" in server_lines and "POST /api/browser/sessions/owner/input" in server_lines
    assert "SECRET" not in server_lines and r["ticket"] not in server_lines


def test_websocket_frames_are_never_logged_even_at_debug_level():
    f = _NoTickets()
    rec = logging.LogRecord("uvicorn.error", logging.DEBUG, "", 0, "< %s", ("TEXT '{\"type\": \"text\", \"text\": \"pw\"}'",), None)
    assert f.filter(rec) is False


async def test_long_poll_frames_and_rest_control(tmp_path):
    sess, cdps = make_session()
    sess._pages["7"] = FakePage("https://other.example.com")
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        poll = asyncio.ensure_future(c.get("/api/browser/sessions/owner/frame", params={"after": 0, "wait": 3}))
        for _ in range(100):
            if cdps:
                break
            await asyncio.sleep(0.01)
        cdps[0].frame(sid=1)
        r = await poll
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert (r.headers["x-frame-seq"], r.headers["x-frame-w"], r.headers["x-frame-h"]) == ("1", "720", "450")
        assert r.headers["x-frame-css-w"] == "1440" and r.headers["x-controller"] == "agent" and r.headers["x-you-control"] == "0"
        assert r.content[:2] == b"\xff\xd8"
        r = await c.get("/api/browser/sessions/owner/frame", params={"after": 1, "wait": 0.1})
        assert r.status_code == 204
        assert (await c.get("/api/browser/sessions/nope/frame", params={"wait": 0})).status_code == 404
        assert (await c.post("/api/browser/sessions/owner/input", json={"type": "tap", "x": 1, "y": 1})).status_code == 409
        assert (await c.post("/api/browser/sessions/owner/control", json={"take": True})).json() == \
            {"id": "owner", "controller": "user", "controlled_by": "owner", "you_control": True, "hold": "button", "holder_away": True}
        r = await c.post("/api/browser/sessions/owner/input", json={"type": "tap", "x": 360, "y": 225})
        assert r.json()["x"] == 720.0 and r.json()["y"] == 450.0
        assert (await c.post("/api/browser/sessions/owner/input", json={"type": "tap"})).status_code == 422
        assert (await c.post("/api/browser/sessions/nope/control", json={"take": True})).status_code == 404
        assert (await c.post("/api/browser/sessions/owner/control", json={"take": False})).json()["controller"] == "agent"
        assert (await c.delete("/api/browser/sessions/owner/frame")).json() == {"ok": True}
        assert not cdps[0].casting


async def test_a_reply_waits_while_the_user_holds_its_tab_and_hears_when_it_is_handed_back(tmp_path):
    sess, cdps = make_session()
    fn = calling(("browser_read", "{}"))
    app = create_app(app_settings(tmp_path), tools=browser_tools(sess, approve_actions=False), stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/browser/sessions/owner/control", json={"take": True})).status_code == 200
        tid = (await c.post("/api/turns", json={"text": "장바구니 봐줘"})).json()["id"]
        for _ in range(100):
            st = (await c.get("/api/agent/status")).json()
            if st["busy"] and st["status"] == "You are in control":
                break
            await asyncio.sleep(0.02)
        assert st["status"] == "You are in control" and st["controller"] == "user" and st["running"][0]["controller"] == "user"
        await asyncio.sleep(0.2)
        assert (await c.get(f"/api/turns/{tid}", params={"wait": 0})).json()["done"] is False   # waiting, not failed
        assert (await c.post("/api/browser/sessions/owner/control", json={"take": False})).json()["controller"] == "agent"
        events = await follow(c, tid)
    assert {"type": "browser_control", "id": "owner", "controller": "agent"} in events
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] is False and "page text" in result["output"]
    assert events[-1]["type"] == "done"


# ---------------------------------------------------------------------------------------------------------- the agent's pointer
def test_the_pointer_is_throttled_to_20_a_second_and_a_press_or_release_goes_at_once():
    t = live_mod.PointerThrottle()
    for i in range(10):                                   # ten moves within 20 ms: one message, the newest
        t.offer(i, i, False, 100.0 + i * 0.002)
    assert t.take(100.02) == [{"type": "pointer", "x": 9, "y": 9, "down": False}]
    t = live_mod.PointerThrottle()
    t.offer(1, 2, False, 100.0)
    assert t.take(100.0) == [{"type": "pointer", "x": 1, "y": 2, "down": False}]
    t.offer(3, 4, False, 100.01)
    t.offer(5, 6, False, 100.02)
    assert t.take(100.02) == [] and 0 < t.wait(100.02) <= live_mod.POINTER_EVERY
    assert t.take(100.06) == [{"type": "pointer", "x": 5, "y": 6, "down": False}] and t.wait(100.06) is None
    t.offer(7, 8, True, 100.061)                         # a tap: down, then up, both at once and in order
    t.offer(7, 8, False, 100.062)
    assert [(m["down"]) for m in t.take(100.062)] == [True, False]


async def test_live_websocket_sends_the_agent_pointer_and_puts_the_last_one_in_the_status(tmp_path):
    from assistant.tools import humanize as human
    sess, cdps = make_session()
    page = sess._pages["owner"]
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        human._set_pointer(page, (30.0, 40.0))            # moved before anyone watches
        r = (await c.post("/api/browser/sessions/owner/live-ticket")).json()
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={r['ticket']}")) as ws:
            st = await recv_until(ws, "status")
            assert st["pointer"] == {"x": 30.0, "y": 40.0, "down": False}      # a new watcher draws it at once
            human.set_pressed(page, True, (50.5, 60.0))
            human.set_pressed(page, False)
            got = [await recv_until(ws, "pointer") for _ in range(2)]
            assert [(m["x"], m["y"], m["down"]) for m in got] == [(50.5, 60.0, True), (50.5, 60.0, False)]
            human._set_pointer(page, (70.0, 80.0))
            assert await recv_until(ws, "pointer") == {"type": "pointer", "x": 70.0, "y": 80.0, "down": False}


# ---------------------------------------------------------------------------------------- secret fields on the picture (`secure`)
async def test_a_frame_says_when_a_secret_field_shows_and_the_page_is_looked_at_once_a_second():
    clock = Clock()
    sess, cdps = make_session()
    hub = LiveHub(sess, clock, reaper=False)
    await hub.join("owner", "a")
    cdp = cdps[0]

    def looks():
        return sum(p.get("expression") == live_mod.SECURE_JS for p in cdp.methods("Runtime.evaluate"))

    cdp.secure = False
    cdp.frame(sid=1)
    first = await hub.wait_frame("owner", 0, 1.0)
    assert first.header()["secure"] is True      # no answer yet: secure
    await settle(10)
    assert looks() == 1
    cdp.secure = True                             # the page changed, but this second has its look already
    cdp.frame(sid=1)                              # the same second: no new look, the kept answer (False) goes on
    second = await hub.wait_frame("owner", first.seq, 1.0)
    await settle(10)
    assert looks() == 1 and second.header()["secure"] is False
    clock.t += 1.0
    cdp.frame(sid=1)                              # a second later: one more look; its answer comes with the frame after
    third = await hub.wait_frame("owner", second.seq, 1.0)
    await settle(10)
    assert looks() == 2 and third.header()["secure"] is False
    cdp.frame(sid=1)
    fourth = await hub.wait_frame("owner", third.seq, 1.0)
    assert fourth.header()["secure"] is True and fourth.secure is True
    await hub.leave("owner", "a")


async def test_a_look_that_fails_keeps_the_last_answer():
    clock = Clock()
    sess, cdps = make_session()
    hub = LiveHub(sess, clock, reaper=False)
    await hub.join("owner", "a")
    cdp = cdps[0]
    cdp.secure = False
    cdp.frame(sid=1)
    await settle(10)
    cdp.frame(sid=1)
    f = await hub.wait_frame("owner", 1, 1.0)
    assert f.secure is False                       # the first answer
    cdp.evaluate_error = True
    cdp.secure = True
    clock.t += 1.0
    cdp.frame(sid=1)
    await settle(10)
    g = await hub.wait_frame("owner", f.seq, 1.0)
    assert g.secure is False                       # the failed look changed nothing
    await hub.leave("owner", "a")


async def test_a_page_without_a_value_keeps_the_default_until_an_answer():
    clock = Clock()
    sess, cdps = make_session()
    hub = LiveHub(sess, clock, reaper=False)
    await hub.join("owner", "a")
    cdp = cdps[0]                                  # secure stays None: the answer carries no boolean
    cdp.frame(sid=1)
    await settle(10)
    cdp.frame(sid=1)
    f = await hub.wait_frame("owner", 1, 1.0)
    assert f.header()["secure"] is True
    await hub.leave("owner", "a")


def test_an_old_frame_without_the_answer_has_no_secure_field():
    f = Frame(1, b"\xff\xd8", 10, 10, 1.0, "", "", {})
    assert "secure" not in f.header()



# ---------------------------------------------------------------------------------------- a chat reply does not sit silently on a held tab (2026-11-18)
async def test_a_chat_reply_step_on_a_held_tab_is_told_at_once_and_the_reply_says_so():
    """The owner pressed 직접 조작 and the chat asks for a step: the step is not held for minutes behind nothing; the reply is told (the
    owner's rule: no step goes on until the owner hands the tab back, and the reply says that it waits for them)."""
    import time
    sess, _ = make_session()
    sess.control_wait = 300.0   # an unattended run keeps the long wait
    tools = browser_tools(sess, approve_actions=True)
    sess.take_control("owner", "1")

    async def approve(req):
        return True

    token, patience = browser_owner.set("owner"), hold_patience.set(CHAT_CONTROL_WAIT_SECONDS)
    try:
        started = time.monotonic()
        events = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "x"}], tools, approve=approve,
                                               stream_fn=calling(("browser_click", '{"selector": "text=Buy"}')))]
        waited = time.monotonic() - started
    finally:
        hold_patience.reset(patience)
        browser_owner.reset(token)
    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.output == CONTROLLED_CHAT_NOTE and waited < 60
    assert sess._pages["owner"].gone_to == []


async def test_a_hand_back_during_the_short_chat_wait_lets_the_step_run():
    sess, _ = make_session()
    sess.control_wait = 300.0
    tools = browser_tools(sess, approve_actions=True)
    sess.take_control("owner", "1")

    async def approve(req):
        return True

    async def hand_back():
        await asyncio.sleep(0.05)
        await sess.release_control("owner", "owner")

    async def run():
        token, patience = browser_owner.set("owner"), hold_patience.set(10.0)
        try:
            return [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "x"}], tools, approve=approve,
                                                 stream_fn=calling(("browser_click", '{"selector": "text=Buy"}')))]
        finally:
            hold_patience.reset(patience)
            browser_owner.reset(token)

    events, _ = await asyncio.gather(run(), hand_back())
    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.output not in (CONTROLLED_NOTE, CONTROLLED_CHAT_NOTE)


async def test_the_owners_scroll_is_a_mouse_wheel_on_the_one_desktop_view():
    clock = Clock()
    page = FakePage()
    sess, cdps = make_session(page)
    hub = LiveHub(sess, clock, reaper=False)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1")
    hub.connected("owner", "1", +1)
    await hub.input("owner", "1", {"type": "scroll", "dy": 100})
    assert cdps[0].methods("Input.dispatchMouseEvent")[-1]["type"] == "mouseWheel"
    assert not cdps[0].methods("Input.dispatchTouchEvent")




async def test_the_picture_follows_a_tab_switch_at_once():
    """C1d: when the active tab of a key changes (take-over: a switch or a new tab), a running picture follows it at once."""
    sess, _cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    followed = []

    async def follow(cast, page):
        followed.append((cast.key, page))

    hub._follow = follow
    hub.casts["owner"] = SimpleNamespace(key="owner")
    await hub.tab_switched("owner")
    assert followed == [("owner", sess.raw_page("owner"))]
    await hub.tab_switched("task:nobody")          # no picture running for it: nothing to follow
    assert len(followed) == 1


async def test_the_holder_switches_and_closes_tabs_of_the_held_window_and_nobody_else_does(tmp_path):
    """C1d API: POST /api/browser/sessions/{key}/tabs/{tab}: only the device holding the tab may switch or close one of its tabs; the
    window keeps its last tab."""
    from assistant.tools.task_window import TaskWindow
    sess, _cdps = make_session()
    first = sess._pages["owner"]
    win = sess._windows["owner"] = TaskWindow("owner")
    t1 = win.add(first)
    win.activate(t1.id)
    second = FakePage("https://b.example/")
    t2 = win.add(second)
    win.activate(t2.id)
    sess._pages["owner"] = second
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/browser/sessions/owner/tabs/t1", json={"action": "activate"})).status_code == 409
        assert (await c.post("/api/browser/sessions/owner/control", json={"take": True})).status_code == 200
        r = await c.post("/api/browser/sessions/owner/tabs/t1", json={"action": "activate"})
        assert r.status_code == 200 and sess._windows["owner"].active == "t1" and sess._pages["owner"] is first
        assert (await c.post("/api/browser/sessions/owner/tabs/t9", json={"action": "activate"})).status_code == 409
        assert (await c.post("/api/browser/sessions/owner/tabs/t1", json={"action": "fly"})).status_code == 422
        assert (await c.post("/api/browser/sessions/owner/tabs/t2", json={"action": "close"})).status_code == 200
        assert second.is_closed()
        assert (await c.post("/api/browser/sessions/owner/tabs/t1", json={"action": "close"})).status_code == 409
        assert [t.id for t in sess._windows["owner"].tabs] == ["t1"]
