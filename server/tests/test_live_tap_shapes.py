"""The owner's tap in "take control" lands where the owner tapped and fires what a tap fires, for the shapes a
mobile shop pop-up uses (2026-11-16: a pop-up's close button on a phone shop page could not be tapped). Local pages only, real
Chromium (skipped without Playwright's browser). Each case taps the way the app does: a point of the frame picture."""

from __future__ import annotations

import asyncio

import pytest

from assistant.tools import humanize
from assistant.tools.browser import BrowserSession, browser_owner
from assistant.tools.live import LiveHub

from fixtures.order_harness import public as PUBLIC

pw = pytest.importorskip("playwright.async_api")

HOST = "https://m.example.com"
CLOSE = """<button id=close style="position:absolute;right:8px;top:8px;width:48px;height:48px">x</button>"""
LOG = "<p id=out></p><script>window.log=(s)=>{document.getElementById('out').textContent+=s+' '}</script>"

PAGES = {
    # the close button of a fixed overlay, handled on click
    "click": f"""<div style="position:fixed;inset:0;background:#fffc;z-index:10"><div style="position:relative;margin:80px 20px;height:300px;background:#ddd">{CLOSE}</div></div>{LOG}
<script>document.getElementById('close').addEventListener('click',()=>log('click'))</script>""",
    # one that listens to pointerup only
    "pointerup": f"""<div style="position:fixed;inset:0;z-index:10"><div style="position:relative;margin:80px 20px;height:300px;background:#ddd">{CLOSE}</div></div>{LOG}
<script>document.getElementById('close').addEventListener('pointerup',()=>log('pointerup'))</script>""",
    # the button sits behind a transparent layer that passes the tap through (pointer-events:none)
    "layer": f"""<div style="position:relative;margin:80px 20px;height:300px;background:#ddd">{CLOSE}</div>
<div style="position:fixed;inset:0;z-index:10;pointer-events:none"></div>{LOG}
<script>document.getElementById('close').addEventListener('click',()=>log('click'))</script>""",
    # a page that scrolled before the tap
    "scrolled": f"""<div style="height:1500px"></div><div style="position:relative;height:200px;background:#ddd">{CLOSE}</div><div style="height:1500px"></div>{LOG}
<script>document.getElementById('close').addEventListener('click',()=>log('click'));window.scrollTo(0,1450)</script>""",
    "frame": f"""<iframe id=f src="https://other.example.net/inner" style="position:fixed;inset:60px 10px;width:380px;height:400px;border:0"></iframe>{LOG}""",
}
INNER = f"""<body style="margin:0">{CLOSE}<p id=out></p><script>window.log=(s)=>{{document.getElementById('out').textContent+=s+' '}};
document.getElementById('close').addEventListener('click',()=>log('click'))</script>"""

META = {"phone": '<meta name=viewport content="width=device-width, initial-scale=1">',
        "no-meta": ""}


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)
        state = {"page": "click", "meta": "phone"}

        async def serve(route):
            url = route.request.url
            body = INNER if url.startswith("https://other.example.net") else PAGES[state["page"]]
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8>{META[state['meta']]}<body style='margin:0'>{body}</body>")
        await c.route("https://**/*", serve)
        c.state = state
        yield c
        await b.close()
    humanize.configure(reset=True)


async def tap_close(ctx, shape, meta="phone", ratio=None):
    ctx.state.update(page=shape, meta=meta)
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    await s.open(HOST + "/")
    await asyncio.sleep(0.4)
    hub = LiveHub(s, reaper=False)
    key = browser_owner.get()
    await hub.join(key, "viewer")
    frame = await hub.wait_frame(key, 0, 5)
    assert frame is not None
    hub.take(key, "dev")
    try:
        target = page.frame_locator("#f").locator("#close") if shape == "frame" else page.locator("#close")
        # where the button is on the screen the owner looks at: the layout box through the visual viewport (zoom, offset)
        rect = await (page.frames[-1] if shape == "frame" else page).evaluate(
            "() => { const r = document.getElementById('close').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }")
        origin = [0.0, 0.0]
        if shape == "frame":
            fb = await page.locator("#f").bounding_box()
            origin = [fb["x"], fb["y"]]
        vv = await page.evaluate("() => ({s: visualViewport.scale, ox: visualViewport.offsetLeft, oy: visualViewport.offsetTop})")
        m = frame.meta
        dx, dy = rect[0] + origin[0], rect[1] + origin[1]
        sx = frame.w / float(m.get("deviceWidth") or frame.w)
        sy = frame.h / float(m.get("deviceHeight") or frame.h)
        x = (dx - vv["ox"]) * vv["s"] * sx
        y = (dy - vv["oy"]) * vv["s"] * sy + float(m.get("offsetTop") or 0) * sy
        out = await hub.input(key, "dev", {"type": "tap", "x": x, "y": y})
        await asyncio.sleep(0.3)
        read = await (page.frames[-1] if shape == "frame" else page).inner_text("#out")
        return read.split(), out
    finally:
        await hub.give_back(key, "dev")
        await hub.leave(key, "viewer")


@pytest.mark.parametrize("shape", ["click", "layer", "scrolled", "frame"])
async def test_a_tap_on_a_click_handler_fires_click(ctx, shape):
    got, out = await tap_close(ctx, shape)
    assert got == ["click"], (got, out)


async def test_a_tap_on_a_pointerup_handler_fires_it(ctx):
    got, out = await tap_close(ctx, "pointerup")
    assert got == ["pointerup"], out


@pytest.mark.parametrize("shape", ["click", "scrolled", "frame"])
async def test_a_tap_on_a_page_without_a_viewport_meta_lands_where_the_owner_tapped(ctx, shape):
    got, out = await tap_close(ctx, shape, meta="no-meta")
    assert got == ["click"], (got, out)
