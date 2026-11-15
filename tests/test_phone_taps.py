"""In the phone view a click is a touch, as on a phone (2026-11-10: a phone site's buttons that listen to touch only ignored the
agent's mouse clicks and the owner's taps in "take control"). Real Chromium (skipped without Playwright's browser)."""

from __future__ import annotations

import asyncio

import pytest

from assistant.agent.fence import plain
from assistant.tools import humanize
from assistant.tools.browser import BrowserSession, browser_owner
from assistant.tools.live import LiveHub

from fixtures.order_harness import public as PUBLIC

pw = pytest.importorskip("playwright.async_api")

PAGE = "https://m.example.com/"
BODY = """<h1>Phone</h1>
<button id=touch style="width:200px;height:60px" ontouchend="document.getElementById('out').textContent+='touch '">Touch only</button>
<button id=pointer style="width:200px;height:60px">Pointer touch only</button>
<button id=mouse style="width:200px;height:60px" onmousedown="document.getElementById('out').textContent+='mouse '">Mouse</button>
<p id=out></p>
<script>document.getElementById('pointer').addEventListener('pointerdown', (e) => {
  if (e.pointerType === 'touch') document.getElementById('out').textContent += 'pointer '; });</script>"""


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)

        async def serve(route):
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width\"><body>{BODY}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


async def phone(ctx):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    await s.open(PAGE, "mobile")
    return s, page


async def test_the_agents_click_in_the_phone_view_is_a_touch(ctx):
    s, page = await phone(ctx)
    await s.click("#touch")
    await s.click("#pointer")
    out = plain(await s.click("#mouse"))                       # a page that listens to the mouse still gets it (a tap makes it)
    assert (await page.inner_text("#out")).split() == ["touch", "pointer", "mouse"], out


async def test_the_owners_tap_in_take_control_of_a_phone_view_tab_is_a_touch(ctx):
    s, page = await phone(ctx)
    hub = LiveHub(s, reaper=False)
    key = browser_owner.get()
    await hub.join(key, "viewer")
    frame = await hub.wait_frame(key, 0, 5)
    assert frame is not None
    hub.take(key, "dev")
    try:
        for sel in ("#touch", "#pointer"):
            box = await page.locator(sel).bounding_box()
            m = frame.meta
            sx = frame.w / float(m.get("deviceWidth") or frame.w)
            sy = frame.h / float(m.get("deviceHeight") or frame.h)
            x = (box["x"] + box["width"] / 2) * sx
            y = (box["y"] + box["height"] / 2 + float(m.get("offsetTop") or 0)) * sy
            out = await hub.input(key, "dev", {"type": "tap", "x": x, "y": y})
            assert out.get("touch") is True
            await asyncio.sleep(0.2)
    finally:
        await hub.give_back(key, "dev")
        await hub.leave(key, "viewer")
    assert (await page.inner_text("#out")).split() == ["touch", "pointer"]
