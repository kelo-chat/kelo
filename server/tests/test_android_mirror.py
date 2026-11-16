"""The device inside Kelo's browser view (android/mirror.py) and the android_show tool, with a fake DevTools session, a fake clock and a fake
bridge: pictures pushed only when they changed and at most twice a second, taps mapped from the picture's pixels to the device's, typed
text forwarded and never logged, Done ends it, ten minutes end it, a failing bridge ends it, and the tab is always closed."""

import asyncio
import base64
import functools
import json
import logging
import shutil
import struct
import subprocess

import httpx
import pytest

from assistant import i18n
from assistant.agent import handover
from assistant.android import mirror as mirror_mod
from assistant.android.device import DeviceClient
from assistant.android.mirror import SCRIPT, AndroidMirror, page_html, png_size, to_device
from assistant.android.screen import ScreenReader
from assistant.android.tools import AndroidSession, android_tools
from assistant.tools.browser import browser_owner

TOKEN = "t" * 40
SECRET = "비밀번호 Hunter2"


def fake_png(w, h, tag=b""):
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00" + tag).decode()


class Clock:
    """Fake time: a sleep moves it on (and lets other tasks run), `on_tick(n)` may change the world at the n-th sleep."""

    def __init__(self):
        self.now, self.ticks, self.on_tick = 0.0, 0, None

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        self.ticks += 1
        if self.on_tick:
            self.on_tick(self.ticks)
        await asyncio.sleep(0)


class Bridge:
    """The bridge: status size, a picture that tests change, every call recorded."""

    def __init__(self, size=(720, 1280), png=None):
        self.size, self.png, self.log, self.fail_frames, self.fail_text = size, png or fake_png(720, 1280, b"a"), [], None, None
        self.frames = 0

    def __call__(self, request):
        path, body = request.url.path, (json.loads(request.content) if request.content else {})
        if path == "/status":
            return httpx.Response(200, json={"connected": True, "size": list(self.size), "app": "", "android": "15", "device": "x"})
        if path == "/screen":
            self.frames += 1
            if self.fail_frames:
                return httpx.Response(502, json={"error": self.fail_frames})
            assert request.url.params["image"] == "only"     # the picture alone: no dump for a live view
            return httpx.Response(200, json={"png": self.png})
        self.log.append((path, body))
        if path == "/text" and self.fail_text:
            return httpx.Response(502, json={"error": self.fail_text})
        return httpx.Response(200, json={"ok": True})

    def sent(self, path):
        return [b for p, b in self.log if p == path]


class Cdp:
    """A DevTools session on the mirror's page: keeps the page's queue, records what is written into it."""

    def __init__(self):
        self.queue, self.html, self.frames, self.scripts, self.detached = [], "", [], [], False

    async def send(self, method, params=None):
        params = params or {}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "F1"}}}
        if method == "Page.setDocumentContent":
            assert params["frameId"] == "F1"
            self.html = params["html"]
            return {}
        assert method == "Runtime.evaluate" and params.get("returnByValue")
        expr = params["expression"]
        if expr.startswith("JSON.stringify"):
            out, self.queue = self.queue, []
            return {"result": {"value": json.dumps(out)}}
        if expr.startswith("window.__keloFrame("):
            self.frames.append(json.loads(expr[len("window.__keloFrame("):-1]))
            return {"result": {}}
        self.scripts.append(expr)
        return {"result": {}}

    async def detach(self):
        self.detached = True


class Session:
    """The BrowserSession's part the mirror uses."""

    def __init__(self):
        self.cdp, self.page_obj, self.opened, self.closed, self.gone = Cdp(), object(), [], [], False

    async def page(self):
        self.opened.append(browser_owner.get())
        return self.page_obj

    async def cdp_session(self, page):
        assert page is self.page_obj
        return self.cdp

    def raw_page(self, key):
        return None if self.gone else self.page_obj

    async def close_tab(self, key):
        self.closed.append(key)
        return True


def make(bridge=None, **kw):
    bridge = bridge or Bridge()
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(bridge)))
    session, clock = Session(), Clock()
    mirror = AndroidMirror(client, session, clock=clock, sleep=clock.sleep, **kw)
    return mirror, session, bridge, clock


def run(coro):
    return asyncio.run(coro)


def press_done_at(clock, session, tick):
    previous = clock.on_tick

    def go(n):
        if previous:
            previous(n)
        if n == tick:
            session.cdp.queue.append({"type": "done"})
    clock.on_tick = go


# ------------------------------------------------------------------------------------------------ pictures
def test_a_picture_is_pushed_only_when_it_changed_and_at_most_twice_a_second():
    mirror, session, bridge, clock = make()
    second = fake_png(720, 1280, b"b")

    def change(n):
        if n == 12:
            bridge.png = second
    clock.on_tick = change
    press_done_at(clock, session, 24)
    assert run(mirror.show("look")) == "done"
    assert session.cdp.frames == ["data:image/png;base64," + fake_png(720, 1280, b"a"), "data:image/png;base64," + second]   # two pictures, two pushes
    assert mirror.pushed == 2 and bridge.frames > 2                                                                      # asked more often, pushed on change
    assert bridge.frames <= 2 * clock.now + 1                                                                            # never more than two a second


def test_the_page_is_written_over_devtools_with_no_network_and_its_script_is_run():
    mirror, session, _, clock = make()
    press_done_at(clock, session, 3)
    run(mirror.show("look"))
    html = session.cdp.html
    assert "<img" in html and 'id="done"' in html and 'data-key="back"' in html and 'data-key="home"' in html and 'data-key="enter"' in html
    assert "http://" not in html and "https://" not in html and "src=" not in html.replace('id="screen"', "")            # nothing is fetched
    assert SCRIPT in html and session.cdp.scripts == [SCRIPT]


def test_the_page_speaks_the_owners_language():
    token = i18n.use("ko")
    try:
        out = page_html("ko")
    finally:
        i18n.reset(token)
    assert 'lang="ko"' in out and "다 했어요" in out and "뒤로" in out and "I&#x27;m done" not in out and "<script>" in out
    assert "<b>" not in page_html("en") and page_html('"><x').count("<x") == 0       # the words are escaped


# ------------------------------------------------------------------------------------------------ what the owner does
@pytest.mark.parametrize("device,want", [((720, 1280), {"x": 360, "y": 640}), ((360, 640), {"x": 180, "y": 320})])
def test_a_tap_in_the_picture_becomes_a_tap_on_the_device(device, want):
    mirror, session, bridge, clock = make(Bridge(size=device))   # the picture is 720 wide

    def tap(n):
        if n == 3:
            session.cdp.queue.append({"type": "tap", "x": 360, "y": 640})
    clock.on_tick = tap
    press_done_at(clock, session, 6)
    assert run(mirror.show("look")) == "done"
    assert bridge.sent("/tap") == [want]


def test_the_mapping_in_pure_form():
    assert to_device(360, 640, (720, 1280), (720, 1280)) == (360, 640)
    assert to_device(360, 640, (720, 1280), (360, 640)) == (180, 320)
    assert to_device(10, 20, (0, 0), (720, 1280)) == (10, 20)           # no size known: as it is
    assert png_size(fake_png(720, 1280)) == (720, 1280) and png_size("not a png") == (0, 0)


def test_keys_and_text_are_forwarded_and_unknown_events_are_not():
    mirror, session, bridge, clock = make()

    def act(n):
        if n == 3:
            session.cdp.queue.extend([{"type": "key", "key": "back"}, {"type": "key", "key": "reboot"}, {"type": "launch", "x": 1},
                                      {"type": "tap", "x": "a", "y": 2}, {"type": "text", "value": ""}, "junk", {"type": "text", "value": SECRET}])
    clock.on_tick = act
    press_done_at(clock, session, 6)
    run(mirror.show("look"))
    assert bridge.sent("/key") == [{"key": "back"}] and bridge.sent("/text") == [{"text": SECRET}] and bridge.sent("/tap") == []


def test_typed_text_is_never_logged_nor_in_an_error(caplog):
    caplog.set_level(logging.DEBUG)
    mirror, session, bridge, clock = make()
    bridge.fail_text = f"could not type {SECRET}"
    clock.on_tick = lambda n: session.cdp.queue.append({"type": "text", "value": SECRET}) if n in (3, 4, 5) else None
    assert run(mirror.show("look")) == "error"
    assert SECRET not in caplog.text and SECRET not in mirror.detail and "[hidden]" in mirror.detail


# ------------------------------------------------------------------------------------------------ the end
def test_done_ends_it_and_the_tab_is_closed():
    mirror, session, _, clock = make()
    press_done_at(clock, session, 5)
    assert run(mirror.show("look")) == "done"
    assert session.closed == session.opened and session.opened[0].endswith(":phone") and session.cdp.detached


def test_ten_minutes_end_it():
    mirror, session, _, clock = make()
    assert run(mirror.show("look")) == "timeout"
    assert 600 <= clock.now < 601 and session.closed == session.opened


def test_a_failing_bridge_stops_it_early():
    bridge = Bridge()
    bridge.fail_frames = "adb timed out after 10 s"
    mirror, session, _, clock = make(bridge)
    assert run(mirror.show("look")) == "error"
    assert mirror.detail == "adb timed out after 10 s" and clock.now < 5 and session.closed == session.opened


def test_one_failed_picture_does_not_stop_it():
    bridge = Bridge()
    mirror, session, _, clock = make(bridge)
    clock.on_tick = lambda n: setattr(bridge, "fail_frames", "adb timed out" if n == 2 else None)
    press_done_at(clock, session, 10)
    assert run(mirror.show("look")) == "done"


def test_a_tab_that_closed_ends_it():
    mirror, session, _, clock = make()
    clock.on_tick = lambda n: setattr(session, "gone", n == 4)
    assert run(mirror.show("look")) == "closed" and session.closed == session.opened


def test_the_owner_is_handed_the_tab_like_a_human_check_and_done_ends_the_wait():
    mirror, session, _, clock = make()
    press_done_at(clock, session, 5)
    seen = {}

    async def hand(info, gone):
        seen["info"] = info
        seen["gone"] = await gone()
        return "solved" if seen["gone"] else "closed"

    async def go():
        token = handover.set(hand)
        try:
            return await mirror.show("Sign in to Play: a human check")
        finally:
            handover.reset(token)
    assert run(go()) == "done" and seen["gone"] is True
    assert seen["info"]["kind"] == "device" and seen["info"]["name"] == "Sign in to Play: a human check"
    assert seen["info"]["tab"] == session.opened[0] and session.closed == session.opened


def test_the_owner_skipping_closes_the_tab_and_stops_the_mirror():
    mirror, session, _, clock = make()

    async def hand(info, gone):
        await asyncio.sleep(0)
        return "skipped"

    async def go():
        token = handover.set(hand)
        try:
            return await mirror.show("look")
        finally:
            handover.reset(token)
    assert run(go()) == "skipped" and session.closed == session.opened and session.cdp.detached


# ------------------------------------------------------------------------------------------------ the page's own script
@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_pages_script_queues_what_the_owner_does(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text("""
const handlers = {};
const el = (id, extra = {}) => ({ id, listeners: {}, addEventListener(t, f) { this.listeners[t] = f; }, ...extra });
const img = el('screen', { naturalWidth: 720, naturalHeight: 1280, getBoundingClientRect: () => ({ left: 10, top: 20, width: 360, height: 640 }) });
const box = el('text', { value: '' }), send = el('send'), done = el('done');
const back = el('b', { dataset: { key: 'back' } });
global.window = {};
global.document = { getElementById: (id) => ({ screen: img, text: box, send, done })[id], querySelectorAll: () => [back] };
%s
%s
img.listeners.click({ clientX: 190, clientY: 340 });
back.listeners.click();
box.value = 'hello'; send.listeners.click();
box.value = 'x'; box.listeners.keydown({ key: 'Enter', preventDefault() {} });
done.listeners.click();
window.__keloFrame('data:image/png;base64,AAAA');
console.log(JSON.stringify({ queue: window.__keloQueue, src: img.src, box: box.value }));
""" % (SCRIPT, SCRIPT))   # run twice: the second run must change nothing
    out = json.loads(subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert out["queue"] == [{"type": "tap", "x": 360, "y": 640}, {"type": "key", "key": "back"}, {"type": "text", "value": "hello"},
                            {"type": "text", "value": "x"}, {"type": "done"}]
    assert out["src"] == "data:image/png;base64,AAAA" and out["box"] == ""


# ------------------------------------------------------------------------------------------------ the tool
class StubMirror:
    def __init__(self, how="done", detail=""):
        self.how, self.detail, self.asked = how, detail, []

    async def show(self, reason, *, lang="en"):
        self.asked.append((reason, lang))
        return self.how


def tool_for(mirror):
    bridge = httpx.MockTransport(lambda r: httpx.Response(200, json={
        "xml": '<hierarchy><node bounds="[0,0][720,1280]" package="com.example.shop"/></hierarchy>', "png": None, "app": "com.example.shop/.Main"}))
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=bridge))
    tools = {t.name: t for t in android_tools(client, session=AndroidSession(client, ScreenReader()), mirror=mirror)}
    return tools


def test_android_show_exists_only_with_a_browser_and_needs_a_reason():
    assert "android_show" not in tool_for(None)
    stub = StubMirror()
    tool = tool_for(stub)["android_show"]
    assert tool.untrusted and not tool.requires_approval and "phone app" in tool.description
    refused = run(tool.handler({"reason": "  "}))
    assert refused.startswith("Error: reason is needed") and stub.asked == []


@pytest.mark.parametrize("how,words", [("done", "pressed Done"), ("timeout", "10 minutes"), ("error", "stopped showing"),
                                       ("skipped", "Do not ask again"), ("closed", "closed before")])
def test_android_show_tells_how_it_ended_and_returns_the_new_screen(how, words):
    stub = StubMirror(how, "adb timed out")
    out = run(tool_for(stub)["android_show"].handler({"reason": "  Sign in to Play:\n a human check "}))
    text = out.head + out.text
    assert words in text and "App: com.example.shop" in text and stub.asked == [("Sign in to Play: a human check", i18n.current())]
