"""The phone's screen handed to the owner (android/handover.py, the android_show tool): the apps' phone live view, not a browser tab. It
ends by itself when the owner is done (held the phone and gave it back; or did something and then 20 s with no input and an unchanged
picture), never while they still touch it, and the card carries the device's name and kind "device"."""

import asyncio
import base64
import functools
import io

import httpx
import pytest
from PIL import Image

from assistant import i18n
from assistant.agent import handover
from assistant.android.device import DeviceClient
from assistant.android.handover import PhoneHandover
from assistant.android.live import DeviceLive
from assistant.android.screen import ScreenReader
from assistant.android.tools import AndroidSession, android_tools

TOKEN = "t" * 40


def png(color):
    buf = io.BytesIO()
    Image.new("RGB", (108, 234), color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Clock:
    """Fake time: a sleep moves it on, `on_tick(clock_now)` may change the world."""

    def __init__(self):
        self.now, self.on_tick = 0.0, None

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        if self.on_tick:
            self.on_tick(self.now)
        await asyncio.sleep(0)


class Bridge:
    def __init__(self):
        self.png = png((1, 2, 3))

    def __call__(self, request):
        if request.url.path == "/status":
            return httpx.Response(200, json={"connected": True, "size": [108, 234], "device": "Pixel 8"})
        if request.url.path == "/screen":
            return httpx.Response(200, json={"png": self.png})
        return httpx.Response(200, json={"ok": True})


def make(**kw):
    bridge, clock = Bridge(), Clock()
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(bridge)))
    live = DeviceLive(client, clock=clock, sleep=clock.sleep)
    return PhoneHandover(live, clock=clock, sleep=clock.sleep, **kw), live, bridge, clock


async def hold_and_tap(live, clock, at):
    """The owner takes the phone and taps at `at` (clock seconds)."""
    live.take("android", "d1")
    await asyncio.sleep(0)
    await live.input("android", "d1", {"type": "tap", "x": 5, "y": 5, "frame_w": 108, "frame_h": 234})


async def test_giving_the_phone_back_is_done():
    h, live, _, clock = make()
    task = asyncio.ensure_future(h.watch())
    await asyncio.sleep(0.01)
    live.take("android", "d1")
    live.connected("android", "d1", 1)   # the app's live connection is open: it keeps the phone
    await asyncio.sleep(0.01)
    assert not task.done()
    await live.give_back("android", "d1")
    assert await asyncio.wait_for(task, 5) == "done" and live.watching("android") == 0


async def test_input_then_twenty_quiet_seconds_is_done():
    h, live, _, clock = make()
    task = asyncio.ensure_future(h.watch())
    for _ in range(5):
        await asyncio.sleep(0)
    live.take("android", "d1")
    for _ in range(5):
        await asyncio.sleep(0)
    await live.input("android", "d1", {"type": "key", "key": "back"})
    at = clock.now
    assert await asyncio.wait_for(task, 5) == "done"
    assert 20 <= clock.now - at < 40


async def test_the_screen_changing_by_itself_with_no_owner_view_keeps_waiting():
    h, live, bridge, clock = make(limit=100.0)
    clock.on_tick = lambda now: setattr(bridge, "png", png((9, 9, 9))) if 3 <= now < 3.6 else None   # a spinner settles
    assert await asyncio.wait_for(h.watch(), 10) == "timeout"


async def test_the_owner_has_the_view_open_the_screen_changes_then_standing_still_is_done():
    h, live, bridge, clock = make()
    await live.join("android", "owner-phone")   # the app's viewer
    clock.on_tick = lambda now: (live.touch("android", "owner-phone"),
                                 setattr(bridge, "png", png((9, 9, 9))) if 3 <= now < 3.6 else None)
    assert await asyncio.wait_for(h.watch(), 10) == "done"
    assert 23 <= clock.now < 45


async def test_it_does_not_end_while_the_owner_keeps_touching():
    h, live, _, clock = make()
    live.take("android", "d1")
    live._seen["d1"] = 1e12   # the controller stays connected
    live.cast = None
    ticks = {"n": 0}

    def touch(now):
        ticks["n"] += 1
        if ticks["n"] % 20 == 0 and now < 120:   # an input every ~10 s
            live.last_input = now
    clock.on_tick = touch
    assert await asyncio.wait_for(h.watch(), 10) == "done"
    assert clock.now > 120   # it ended 20 s after the last touch, not before


async def test_an_untouched_phone_waits_for_the_limit():
    h, live, _, clock = make(limit=100.0)
    assert await asyncio.wait_for(h.watch(), 10) == "timeout" and 100 <= clock.now < 102


async def test_the_card_carries_the_device_name_and_kind_device():
    h, live, _, clock = make()
    seen = {}

    async def hand(info, gone):
        seen.update(info)
        await asyncio.sleep(0)
        return "solved"        # the owner pressed Done on the card

    token = handover.set(hand)
    try:
        assert await h.show("Sign in to Play: a human check") == "done"
    finally:
        handover.reset(token)
    assert seen["site"] == "Pixel 8" and seen["kind"] == "device" and seen["tab"] == "android"
    assert seen["name"] == "Sign in to Play: a human check" and live.watching("android") == 0


async def test_skipping_is_reported_and_stops_watching():
    h, live, _, clock = make()

    async def hand(info, gone):
        return "skipped"
    token = handover.set(hand)
    try:
        assert await h.show("x") == "skipped"
    finally:
        handover.reset(token)
    assert live.watching("android") == 0


async def test_the_listing_titles_the_phone_with_its_name():
    _, live, _, _ = make()
    assert (await live.listing([]))["title"] == "Pixel 8"


# ---- the tool
class StubHandover:
    def __init__(self, how="done", detail=""):
        self.how, self.detail, self.asked = how, detail, []

    async def show(self, reason, *, lang="en"):
        self.asked.append((reason, lang))
        return self.how


def tool_for(stub):
    bridge = httpx.MockTransport(lambda r: httpx.Response(200, json={
        "xml": '<hierarchy><node bounds="[0,0][720,1280]" package="com.example.shop"/></hierarchy>', "png": None, "app": "com.example.shop/.Main"}))
    client = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=bridge))
    return {t.name: t for t in android_tools(client, session=AndroidSession(client, ScreenReader()), handover=stub)}


async def test_android_show_needs_a_handover_and_a_reason():
    assert "android_show" not in tool_for(None)
    stub = StubHandover()
    tool = tool_for(stub)["android_show"]
    assert tool.untrusted and not tool.requires_approval and "phone app" in tool.description
    assert (await tool.handler({"reason": "  "})).startswith("Error: reason is needed") and stub.asked == []


@pytest.mark.parametrize("how,words", [("done", "is done"), ("timeout", "10 minutes"), ("error", "could not be shown"),
                                       ("skipped", "Do not ask again"), ("closed", "ended before")])
async def test_android_show_tells_how_it_ended_and_returns_the_new_screen(how, words):
    stub = StubHandover(how, "adb timed out")
    out = await tool_for(stub)["android_show"].handler({"reason": "  Sign in to Play:\n a human check "})
    assert words in out.head + out.text and "App: com.example.shop" in out.text and stub.asked == [("Sign in to Play: a human check", i18n.current())]


async def test_a_phone_handover_never_opens_a_browser_tab():
    """Owner rule: the phone always goes to the phone view. The hand-over has no browser session at all; its card points at the phone's
    live view (key android), and when the phone cannot be reached there is no card and no tab, only the error."""
    import inspect
    assert "session" not in inspect.signature(PhoneHandover.__init__).parameters
    h, live, _, clock = make()
    seen = []

    async def hand(info, gone):
        seen.append(info)
        return "solved"
    token = handover.set(hand)
    try:
        await h.show("x")
    finally:
        handover.reset(token)
    assert seen[0]["kind"] == "device" and seen[0]["tab"] == "android" and ":" not in seen[0]["tab"]
    # the bridge is away: the card is never raised
    down = DeviceClient("http://android:8090", TOKEN, http=functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(
        lambda r: httpx.Response(502, json={"error": "adb is away"}))))
    away = PhoneHandover(DeviceLive(down, clock=clock, sleep=clock.sleep), clock=clock, sleep=clock.sleep)
    seen.clear()
    token = handover.set(hand)
    try:
        assert await away.show("x") == "error"
    finally:
        handover.reset(token)
    assert seen == [] and away.detail == "the phone screen could not be opened"
