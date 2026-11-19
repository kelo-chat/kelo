"""Live view feels quicker while the owner holds the tab: 30 frames a second while held, a 500 CSS px floor for the page, a sharp
picture a quarter second after the owner's last input, and a short pause between a tap's move and its press. Fake DevTools session."""

import asyncio
import base64
import time

import pytest

from assistant.tools import live as live_mod
from assistant.tools.live import LiveHub, viewer_css_size

from test_browser_live import META, jpeg, make_session


def frame_bytes() -> str:
    return base64.b64encode(jpeg(720, 450)).decode()


def test_the_floor_is_500_css_px_not_800():
    w, h, ratio = viewer_css_size(1080, 2208, 2.625)   # 411 css px wide
    assert w == 500 and ratio == 2.16 and abs(h / w - 2208 / 1080) < 0.01
    assert viewer_css_size(300, 600, 3)[0] == 500   # a narrower screen is scaled up to the floor


async def test_rate_is_30_while_held_and_the_asked_fps_while_only_watched():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    assert hub.rate("owner") == live_mod.DEFAULT_FPS   # nobody watches it
    await hub.join("owner", "a", fps=5)
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    assert hub.rate("owner") == 5
    hub.take("owner", "1")
    assert hub.rate("owner") == live_mod.HELD_FPS == 30
    await hub.give_back("owner", "1")
    assert hub.rate("owner") == 5
    await hub.leave("owner", "a")


async def test_held_acks_are_spaced_one_thirtieth_of_a_second_and_watched_ones_by_the_asked_fps(monkeypatch):
    class FakeTime:
        t = 100.0

        @staticmethod
        def monotonic():
            return FakeTime.t

        @staticmethod
        def time():
            return 1.0

    monkeypatch.setattr(live_mod, "time", FakeTime)
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    cast = await hub.join("owner", "a", fps=5)
    params = {"data": frame_bytes(), "sessionId": 1, "metadata": dict(META)}
    await hub._on_frame(cast, dict(params))
    assert cast.next_ack == 100.0 + 1 / 5   # only watched: the asked 5 a second
    hub.take("owner", "1")
    FakeTime.t = 100.3                       # past the last ack: no waiting in the test
    await hub._on_frame(cast, dict(params))
    assert cast.next_ack == pytest.approx(100.3 + 1 / 30)
    await hub.leave("owner", "a")


async def test_three_inputs_a_tenth_apart_leave_one_sharp_picture_a_quarter_second_after_the_last():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    cdp = cdps[0]
    cdp.frame()   # a frame came at once: the still picture taken at the start is not needed
    await hub.wait_frame("owner", 0, 1.0)
    shots: list[float] = []
    sent = cdp.send

    async def timed(method, params=None):
        if method == "Page.captureScreenshot":
            shots.append(time.monotonic())
        return await sent(method, params)

    cdp.send = timed
    hub.take("owner", "1")
    last = 0.0
    for _ in range(3):
        await hub.input("owner", "1", {"type": "text", "text": "a"})
        last = time.monotonic()
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.6)
    caps = cdp.methods("Page.captureScreenshot")
    assert len(caps) == 1 and caps[0]["format"] == "jpeg" and caps[0]["quality"] == 85
    assert 0.2 <= shots[0] - last <= 0.45
    frame = await hub.wait_frame("owner", 1, 1.0)
    assert frame is not None and frame.meta.get("seed") is True
    await hub.leave("owner", "a")


async def test_a_tap_moves_then_pauses_then_presses():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    cdp = cdps[0]
    cdp.frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1")
    stamps: dict[str, float] = {}
    sent = cdp.send

    async def timed(method, params=None):
        if method == "Input.dispatchMouseEvent":
            stamps.setdefault(params["type"], time.monotonic())
        return await sent(method, params)

    cdp.send = timed
    await hub.input("owner", "1", {"type": "tap", "x": 100, "y": 50})
    assert [m["type"] for m in cdp.methods("Input.dispatchMouseEvent")] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert 0.02 <= stamps["mousePressed"] - stamps["mouseMoved"] < 0.2
    await hub.leave("owner", "a")
