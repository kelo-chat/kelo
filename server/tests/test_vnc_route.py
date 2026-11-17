"""GET /api/browser/sessions/{key}/vnc against a fake RFB server (what Xvnc is in the browser container), and the live socket's
`frames` switch. Real server (uvicorn), real WebSocket clients, a fake browser session."""

import asyncio
import base64
import json
import struct

import httpx
import pytest
import websockets

from assistant import e2e
from assistant.server import create_app
from assistant.tools import live as live_mod
from assistant.tools.live import FrameGate, pump_frames
from assistant.tools.vnc import DEFAULT_SIZE, VncScreen, XScreen

from e2e_client import E2EClient
from fake_rfb import FakeRfb
from test_agent_profile import app_settings
from test_browser_live import FakePage, calling, make_session, recv_until, ws_url
from test_server import AUTH, Running
from test_takeover_server import EditCDP, session_with

POINTER = struct.pack(">BBHH", 5, 1, 120, 340)
KEY = struct.pack(">BBxxI", 4, 1, 0x61)
UPDATE = struct.pack(">BBHHHH", 3, 1, 0, 0, 500, 889)
QUERY = "width=900&height=1600&density=2"   # 450 x 800 CSS px: under the 500 floor, so scaled up to 500 x 889


async def until(cond, seconds=3.0):
    end = asyncio.get_running_loop().time() + seconds
    while not cond():
        assert asyncio.get_running_loop().time() < end, "the condition did not come true"
        await asyncio.sleep(0.02)


async def freed(s):
    """The screen has no tab and everything that put it back is done."""
    await until(lambda: not s.vnc.holds("owner") and not s.vnc._lock.locked())


async def until_status(ws, ok):
    """Read the live socket's status lines until one satisfies `ok`."""
    for _ in range(20):
        if ok(st := await recv_until(ws, "status")):
            return st
    raise AssertionError("the status never became what was expected")


async def ticket(c, key="owner"):
    return (await c.post(f"/api/browser/sessions/{key}/live-ticket")).json()["ticket"]


async def rfb_handshake(ws):
    """The viewer's side of RFB 3.8 (security None) over the relay; returns ServerInit's (width, height)."""
    async def take(n):
        got = b""
        while len(got) < n:
            got += await asyncio.wait_for(ws.recv(), 3)
        return got
    assert await take(12) == b"RFB 003.008\n"
    await ws.send(b"RFB 003.008\n")
    assert await take(2) == b"\x01\x01"
    await ws.send(b"\x01")
    assert await take(4) == bytes(4)
    await ws.send(b"\x01")
    init = await take(4 + 16 + 4 + 4)   # size, pixel format, name length, the name "fake"
    return struct.unpack(">HH", init[:4])


class Setup:
    """The app, one fake browser with two tabs, and a fake X server."""

    def __init__(self, tmp_path, fake=None, with_second_tab=True, mobile=True):
        """`mobile`: the owner tab shows sites as a phone's browser (the X screen follows the viewer); False is a desktop-view tab."""
        self.tmp_path, self.fake = tmp_path, fake or FakeRfb()
        self.sess, self.cdps = make_session()
        if mobile:
            self.sess._view_cdp["owner"] = Phone()
        if with_second_tab:
            self.sess._pages["other"] = FakePage()

    async def __aenter__(self):
        port = await self.fake.start()
        self.vnc = VncScreen(self.sess, XScreen("127.0.0.1", port))
        self.app = create_app(app_settings(self.tmp_path), tools=[], stream_fn=calling(), browser=self.sess, vnc=self.vnc)
        self.srv = await Running(self.app).__aenter__()
        self.http = httpx.AsyncClient(base_url=self.srv.url, headers=AUTH)
        return self

    async def __aexit__(self, *exc):
        await self.http.aclose()
        await self.srv.__aexit__(*exc)
        await self.fake.stop()

    def url(self, key="owner", query=QUERY, t=None):
        return ws_url(self.srv, f"/api/browser/sessions/{key}/vnc?ticket={t}&{query}")


async def test_a_full_relay_sizes_the_screen_drops_a_watchers_input_passes_a_holders_and_puts_the_screen_back(tmp_path):
    async with Setup(tmp_path) as s:
        async with websockets.connect(s.url(t=await ticket(s.http))) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 3)) == {"type": "vnc", "width": 450, "height": 800}
            assert await rfb_handshake(ws) == (450, 800)                       # the X screen was sized for this tab
            assert s.fake.size == (450, 800) and s.vnc.holds("owner")
            relay = lambda: s.fake.connections[1]   # noqa: E731 - connection 0 was the short one that set the size
            await ws.send(POINTER + UPDATE + KEY)                                # a viewer that does not hold the tab
            await until(lambda: len(s.fake.connections) > 1 and [m[0] for m in relay()] == [3])
            assert s.fake.handshakes[1] == b"RFB 003.008\n\x01\x01"
            # the same device takes the tab over on the live socket, then its pointer and keys pass
            async with websockets.connect(ws_url(s.srv, f"/api/browser/sessions/owner/live?ticket={await ticket(s.http)}")) as live:
                await live.send(json.dumps({"type": "control", "take": True}))
                await until_status(live, lambda st: st["you_control"] is True)
                await ws.send(POINTER + KEY + UPDATE)
                await until(lambda: [m[0] for m in relay()] == [3, 5, 4, 3])
                assert relay()[1] == POINTER and relay()[2] == KEY
                await live.send(json.dumps({"type": "control", "take": False}))
                await until_status(live, lambda st: st["you_control"] is False)
                await ws.send(POINTER + UPDATE)                                  # given back: dropped again
                await until(lambda: [m[0] for m in relay()] == [3, 5, 4, 3, 3])
            # nothing comes back to the viewer that it did not ask the server for: bytes from the screen arrive untouched
        await freed(s)   # the last viewer left: back to 1440 x 900
        assert s.fake.resizes == [(450, 800, 7, 1), (1440, 900, 7, 1)]


async def test_bytes_from_the_screen_reach_the_viewer_untouched(tmp_path):
    async with Setup(tmp_path) as s:
        async with websockets.connect(s.url(t=await ticket(s.http))) as ws:
            await ws.recv()
            await rfb_handshake(ws)
            await ws.send(UPDATE)
            reply = await asyncio.wait_for(ws.recv(), 3)
            assert isinstance(reply, bytes) and reply[:1] == b"\x02"      # the fake's Bell and update, raw, as binary frames


async def test_a_second_tab_gets_busy_and_the_first_keeps_its_screen(tmp_path):
    async with Setup(tmp_path) as s:
        async with websockets.connect(s.url(t=await ticket(s.http))) as first:
            assert json.loads(await first.recv())["type"] == "vnc"
            async with websockets.connect(s.url("other", t=await ticket(s.http, "other"))) as second:
                assert json.loads(await asyncio.wait_for(second.recv(), 3)) == {"type": "error", "code": "busy"}
                with pytest.raises(websockets.ConnectionClosed):
                    await second.recv()
            assert s.vnc.holds("owner") and s.fake.size == (450, 800)
        await freed(s)
        async with websockets.connect(s.url("other", t=await ticket(s.http, "other"))) as third:   # free again
            assert json.loads(await third.recv())["type"] == "vnc"


async def test_two_devices_on_the_same_tab_share_the_screen(tmp_path):
    async with Setup(tmp_path) as s:
        async with websockets.connect(s.url(t=await ticket(s.http))) as a:
            await a.recv()
            async with websockets.connect(s.url(query="width=1000&height=1000&density=1", t=await ticket(s.http))) as b:
                assert json.loads(await b.recv()) == {"type": "vnc", "width": 450, "height": 800}   # the first viewer's size stays
            assert s.vnc.holds("owner")
        await freed(s)
        assert s.fake.resizes == [(450, 800, 7, 1), (1440, 900, 7, 1)]


async def test_without_a_ticket_or_with_a_spent_one_the_answer_is_unauthorized_and_a_close(tmp_path):
    async with Setup(tmp_path) as s:
        t = await ticket(s.http)
        async with websockets.connect(s.url(t=t)) as ws:
            await ws.recv()
        await until(lambda: not s.vnc.holds("owner") and len(s.fake.connections) == 3)   # set up, the relay, put back
        for url in (s.url(t=t), s.url(t="")):   # spent, and none
            async with websockets.connect(url) as ws:
                assert json.loads(await ws.recv())["code"] == "unauthorized"
                with pytest.raises(websockets.ConnectionClosed) as closed:
                    await ws.recv()
                assert closed.value.rcvd.code == 4401
        assert len(s.fake.connections) == 3 and not s.vnc.holds("owner")   # nothing was set up for the refused ones


async def test_the_bearer_header_works_instead_of_a_ticket(tmp_path):
    async with Setup(tmp_path) as s:
        async with websockets.connect(ws_url(s.srv, f"/api/browser/sessions/owner/vnc?{QUERY}"), additional_headers=AUTH) as ws:
            assert json.loads(await ws.recv())["type"] == "vnc"


async def test_a_screen_that_cannot_be_set_up_says_unavailable_and_changes_nothing(tmp_path):
    fake = FakeRfb(resize_status=1)   # the X server refuses the size
    async with Setup(tmp_path, fake) as s:
        async with websockets.connect(s.url(t=await ticket(s.http))) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 3)) == {"type": "error", "code": "unavailable"}
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await ws.recv()
            assert closed.value.rcvd.code == 4503
        assert not s.vnc.holds("owner")


async def test_a_server_without_an_x_screen_says_unavailable(tmp_path):
    fake = FakeRfb()
    sess, _ = make_session()
    port = await fake.start()
    await fake.stop()   # nothing listens any more
    vnc = VncScreen(sess, XScreen("127.0.0.1", port, timeout=1))
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess, vnc=vnc)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        t = await ticket(c)
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/vnc?ticket={t}&{QUERY}")) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 5)) == {"type": "error", "code": "unavailable"}


@pytest.mark.parametrize("query", ["", "height=1600&density=2", "width=abc&height=1&density=1", "width=0&height=1600&density=2"])
async def test_a_screen_size_that_cannot_be_used_says_unavailable(tmp_path, query):
    async with Setup(tmp_path) as s:
        async with websockets.connect(s.url(query=query, t=await ticket(s.http))) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 3)) == {"type": "error", "code": "unavailable"}
        assert s.fake.connections == []


async def test_a_tab_that_does_not_exist_says_unavailable(tmp_path):
    async with Setup(tmp_path) as s:
        async with websockets.connect(s.url("task:nope", t=await ticket(s.http, "task:nope"))) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 3)) == {"type": "error", "code": "unavailable"}


async def test_a_desktop_view_tab_keeps_the_desktop_screen_whatever_the_phone_asks_for(tmp_path):
    """Owner report 2026-11-17: opening the live view re-laid out a desktop-view tab ~500 px wide (coupang showed its narrow layout).
    The X screen stays 1440 x 900; the app scales the whole picture down and zooms on a pinch."""
    async with Setup(tmp_path, mobile=False) as s:
        async with websockets.connect(s.url(t=await ticket(s.http))) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 3)) == {"type": "vnc", "width": 1440, "height": 900}
            assert await rfb_handshake(ws) == (1440, 900)
            assert s.fake.resizes == [] and s.sess.view("owner") == "desktop"
        await freed(s)
        assert s.fake.resizes == []          # nothing to put back


class Phone:
    """The DevTools session holding a tab's phone view."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send(self, method, params=None):
        self.sent.append((method, dict(params or {})))
        return {}


async def test_a_phone_view_tab_is_laid_out_with_the_mobile_floor_and_gets_its_phone_back(tmp_path):
    async with Setup(tmp_path) as s:
        phone = s.sess._view_cdp["owner"] = Phone()
        async with websockets.connect(s.url(t=await ticket(s.http))) as ws:
            assert json.loads(await ws.recv()) == {"type": "vnc", "width": 450, "height": 800}   # 450 is over the mobile floor of 320
            assert phone.sent[0] == ("Emulation.setDeviceMetricsOverride", {"width": 450, "height": 800, "deviceScaleFactor": 1, "mobile": True,
                                                                           "screenWidth": 450, "screenHeight": 800})
            assert phone.sent[1] == ("Emulation.setEmitTouchEventsForMouse", {"enabled": True, "configuration": "mobile"})
        await freed(s)
        assert ("Emulation.setEmitTouchEventsForMouse", {"enabled": False}) in phone.sent
        assert phone.sent[-1][0] == "Emulation.setDeviceMetricsOverride" and phone.sent[-1][1]["width"] == 412   # the phone's own screen again


async def test_the_socket_ends_and_the_screen_goes_back_when_the_tab_is_closed(tmp_path):
    async with Setup(tmp_path) as s:
        async with websockets.connect(s.url(t=await ticket(s.http))) as ws:
            await ws.recv()
            assert await s.sess.close_tab("owner") is True
            with pytest.raises(websockets.ConnectionClosed):
                while True:
                    await asyncio.wait_for(ws.recv(), 3)
        await freed(s)


async def test_a_viewer_that_sends_something_that_is_not_rfb_is_cut_off(tmp_path):
    async with Setup(tmp_path) as s:
        async with websockets.connect(s.url(t=await ticket(s.http))) as ws:
            await ws.recv()
            await rfb_handshake(ws)
            await ws.send(bytes([99, 1, 2, 3]))
            with pytest.raises(websockets.ConnectionClosed) as closed:
                while True:
                    await asyncio.wait_for(ws.recv(), 3)
            assert closed.value.rcvd.code == 1003
        await freed(s)


async def test_the_route_works_through_ke2e_text_first_then_binary(tmp_path):
    """The apps open /vnc through the same sealed WebSocket as the live socket: one sealed message per frame, text then binary."""
    async with Setup(tmp_path) as s:
        info = (await s.http.get("/api/e2e/info")).json()
        client = E2EClient(base64.urlsafe_b64decode(info["pk"] + "=" * (-len(info["pk"]) % 4)))
        sealed = client.seal("GET", f"/api/browser/sessions/owner/vnc?ticket={await ticket(s.http)}&{QUERY}")
        channel = sealed.ws_channel()
        async with websockets.connect(ws_url(s.srv, "/e2e/ws")) as ws:
            await ws.send(sealed.envelope)
            assert json.loads(channel.open(await asyncio.wait_for(ws.recv(), 3)))["s"] == 101
            assert json.loads(channel.open(await asyncio.wait_for(ws.recv(), 3))) == {"type": "vnc", "width": 450, "height": 800}
            assert channel.open(await asyncio.wait_for(ws.recv(), 3)) == b"RFB 003.008\n"   # binary, as the screen sent it
            await ws.send(channel.seal(b"RFB 003.008\n"))
            assert channel.open(await asyncio.wait_for(ws.recv(), 3)) == b"\x01\x01"
        await freed(s)


# ---------------------------------------------------------------------------------------------------------- frames on / off
async def test_frames_off_stops_the_pictures_of_that_viewer_and_on_sends_the_newest_one_at_once(tmp_path):
    sess, cdps = make_session()
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        t = await ticket(c)
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={t}")) as ws:
            await recv_until(ws, "status")
            cdps[0].frame(sid=1)
            first = await recv_until(ws, "frame")
            assert isinstance(await asyncio.wait_for(ws.recv(), 2), bytes)
            await ws.send(json.dumps({"type": "frames", "on": False}))
            await ws.send(json.dumps({"type": "ping"}))
            await recv_until(ws, "pong")                                   # the switch is in before the next picture is made
            cdps[0].frame(sid=2)
            cdps[0].frame(sid=3)
            seen: list[str] = []
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), 0.5)
                    seen.append(json.loads(msg)["type"] if isinstance(msg, str) else "binary")
            except asyncio.TimeoutError:
                pass
            assert "frame" not in seen and "binary" not in seen            # status and pointer lines still come
            await ws.send(json.dumps({"type": "frames", "on": True}))
            again = await recv_until(ws, "frame")
            assert again["seq"] > first["seq"]
            assert isinstance(await asyncio.wait_for(ws.recv(), 2), bytes)


async def test_turning_frames_on_again_resends_the_newest_picture_even_when_the_page_has_not_changed(tmp_path):
    sess, cdps = make_session()
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={await ticket(c)}")) as ws:
            await recv_until(ws, "status")
            cdps[0].frame(sid=1)
            first = await recv_until(ws, "frame")
            await ws.recv()
            await ws.send(json.dumps({"type": "frames", "on": False}))
            await ws.send(json.dumps({"type": "frames", "on": True}))
            again = await recv_until(ws, "frame")                           # no new screencast frame came: the same one goes out again
            assert again["seq"] == first["seq"]


async def test_frames_off_is_per_viewer(tmp_path):
    sess, cdps = make_session()
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={await ticket(c)}")) as quiet, \
                websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={await ticket(c)}")) as loud:
            await quiet.send(json.dumps({"type": "frames", "on": False}))
            await quiet.send(json.dumps({"type": "ping"}))
            await recv_until(quiet, "pong")
            cdps[0].frame(sid=1)
            assert (await recv_until(loud, "frame"))["seq"] == 1
            seen = []
            try:
                while True:
                    msg = await asyncio.wait_for(quiet.recv(), 0.4)
                    seen.append(json.loads(msg)["type"] if isinstance(msg, str) else "binary")
            except asyncio.TimeoutError:
                pass
            assert "frame" not in seen and "binary" not in seen


async def test_pump_frames_waits_on_its_gate():
    from types import SimpleNamespace

    sent: list[int] = []
    frames = [SimpleNamespace(seq=1), SimpleNamespace(seq=2)]
    state = {"newest": 1}

    class FakeHub:
        async def wait_frame(self, key, after, timeout):
            for f in frames[:state["newest"]]:
                if f.seq > after:
                    return f
            await asyncio.sleep(0.02)
            return None

        def latest(self, key):
            return frames[state["newest"] - 1]

    async def send(frame):
        sent.append(frame.seq)

    gate = FrameGate()
    task = asyncio.ensure_future(pump_frames(FakeHub(), "k", send, fps=lambda: 1000, gate=gate))
    try:
        await until(lambda: sent == [1])
        gate.set(False)
        await asyncio.sleep(0.05)
        state["newest"] = 2
        await asyncio.sleep(0.2)
        assert sent == [1]                       # off: the second frame is not sent
        gate.set(True)
        await until(lambda: sent == [1, 2])      # on: the newest goes out at once
        gate.set(False)
        gate.set(True)                           # off and on again before the pump looked: the newest still goes out again
        await until(lambda: sent == [1, 2, 2])
    finally:
        task.cancel()


# ---------------------------------------------------------------------------------------------------------- meta while frames are off
class TitledPage(FakePage):
    """A page whose title can change, as after a navigation."""

    def __init__(self, url="https://shop.example.com/cart", title="장바구니"):
        super().__init__(url)
        self.now = title

    async def title(self):
        return self.now


async def test_a_viewer_with_frames_off_is_told_the_new_address_and_title(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.tools.live.META_EVERY", 0.05)
    page = TitledPage()
    sess, cdps = make_session(page)
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={await ticket(c)}")) as ws:
            await recv_until(ws, "status")
            await ws.send(json.dumps({"type": "frames", "on": False}))
            first = await recv_until(ws, "meta")                          # at once: the header shows what the tab is now
            assert (first["url"], first["title"]) == ("https://shop.example.com/cart", "장바구니")
            page.url, page.now = "https://shop.example.com/pay", "결제"      # a navigation while the X screen shows
            second = await recv_until(ws, "meta")
            assert (second["url"], second["title"]) == ("https://shop.example.com/pay", "결제")
            page.now = "결제 완료"                                           # the title alone changes
            assert (await recv_until(ws, "meta"))["title"] == "결제 완료"


async def test_meta_is_sent_only_when_it_changed_and_not_while_frames_are_on(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.tools.live.META_EVERY", 0.05)
    page = TitledPage()
    sess, cdps = make_session(page)
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={await ticket(c)}")) as ws:
            async def kinds(seconds):
                seen: list[str] = []
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), seconds)
                        seen.append(json.loads(msg)["type"] if isinstance(msg, str) else "binary")
                except asyncio.TimeoutError:
                    return seen
            assert "meta" not in await kinds(0.4)                         # frames on (the start): the frame headers carry them
            await ws.send(json.dumps({"type": "frames", "on": False}))
            assert (await kinds(0.5)).count("meta") == 1                  # nothing changed: one line, not one per look
            await ws.send(json.dumps({"type": "frames", "on": True}))
            await ws.send(json.dumps({"type": "ping"}))
            await recv_until(ws, "pong")
            page.url = "https://shop.example.com/other"
            assert "meta" not in await kinds(0.4)                         # on again: no meta
            await ws.send(json.dumps({"type": "frames", "on": False}))
            assert (await recv_until(ws, "meta"))["url"] == "https://shop.example.com/other"   # off again: the newest look goes at once


# ---------------------------------------------------------------------------------------------------------- meta carries the focused field
async def test_meta_says_whether_a_text_field_has_focus_and_a_change_of_that_alone_sends_a_new_line(tmp_path, monkeypatch):
    """The X screen sends no picture headers, so the app's keyboard only comes up if the meta line carries `editing`."""
    monkeypatch.setattr("assistant.tools.live.META_EVERY", 0.05)
    monkeypatch.setattr("assistant.tools.live.EDITING_EVERY", 0.02)
    sess, cdps = session_with(EditCDP, TitledPage())
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={await ticket(c)}")) as ws:
            await recv_until(ws, "status")
            await ws.send(json.dumps({"type": "control", "take": True}))
            await until_status(ws, lambda st: st["you_control"] is True)
            await ws.send(json.dumps({"type": "frames", "on": False}))
            first = await recv_until(ws, "meta")
            assert first["editing"] is False                              # no frame was ever sent: the look is the meta pump's own
            cdps[0].editing = True                                        # a field gets focus; the address and title stay the same
            second = await recv_until(ws, "meta")
            assert second["editing"] is True and (second["url"], second["title"]) == (first["url"], first["title"])
            assert [m for m in cdps[0].methods("Runtime.evaluate") if m["expression"] == live_mod.EDITING_JS]
            cdps[0].editing = False
            assert (await recv_until(ws, "meta"))["editing"] is False


async def test_meta_never_reports_a_focused_field_to_a_viewer_that_does_not_hold_the_tab(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.tools.live.META_EVERY", 0.05)
    monkeypatch.setattr("assistant.tools.live.EDITING_EVERY", 0.02)
    sess, cdps = session_with(EditCDP, TitledPage())
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={await ticket(c)}")) as ws:
            await recv_until(ws, "status")
            await ws.send(json.dumps({"type": "frames", "on": False}))
            first = await recv_until(ws, "meta")
            assert first["editing"] is False
            for cdp in cdps:
                cdp.editing = True
            await asyncio.sleep(0.3)
            assert not [m for cdp in cdps for m in cdp.methods("Runtime.evaluate") if m["expression"] == live_mod.EDITING_JS]   # nobody controls: not even looked at
