"""browser_open on a tab that already shows the address does not load it again (owner report 2026-11-17: Kelo opened coupang 4 times
in 6 minutes, then told the owner the page was closed). Real Chromium on pages served from memory (skipped without Playwright's browser)."""

import pytest

from assistant.agent.fence import plain
from assistant.tools import humanize
from assistant.tools.browser import BrowserSession, browser_tools

from test_purchase import PUBLIC

pw = pytest.importorskip("playwright.async_api")
HOME = "https://www.shop.example.com/"
OTHER = "https://www.shop.example.com/cart"


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)
        loads: list[str] = []

        async def serve(route):
            if route.request.resource_type == "document":
                loads.append(route.request.url)
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body="<!doctype html><meta charset=utf-8><title>Coupang</title><body><h1>Rocket delivery</h1></body>")
        await c.route("https://**/*", serve)
        c.loads = loads
        yield c
        await b.close()
    humanize.configure(reset=True)


async def session_on(ctx):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    return s, {t.name: t for t in browser_tools(s)}


async def test_two_opens_of_the_same_address_on_one_tab_navigate_once(ctx):
    s, tools = await session_on(ctx)
    first = plain(await tools["browser_open"].handler({"url": HOME}))
    assert "Rocket delivery" in first and ctx.loads == [HOME]
    second = plain(await tools["browser_open"].handler({"url": HOME}))
    assert ctx.loads == [HOME]                                           # not loaded again
    assert second.startswith(f"This tab is open and already shows {HOME} (Coupang)") and "Rocket delivery" in second   # the page read comes with it


async def test_the_same_page_written_a_little_differently_is_still_the_same_page(ctx):
    s, tools = await session_on(ctx)
    await tools["browser_open"].handler({"url": HOME})
    out = plain(await tools["browser_open"].handler({"url": "https://shop.example.com#top"}))
    assert ctx.loads == [HOME] and "already shows" in out


async def test_another_address_navigates_as_before(ctx):
    s, tools = await session_on(ctx)
    await tools["browser_open"].handler({"url": HOME})
    out = plain(await tools["browser_open"].handler({"url": OTHER}))
    assert ctx.loads == [HOME, OTHER] and "already shows" not in out
    await tools["browser_open"].handler({"url": HOME})                   # back to the first: the tab shows the cart now, so it loads
    assert ctx.loads == [HOME, OTHER, HOME]


def test_which_addresses_are_the_same_page():
    from assistant.tools.browser import same_address

    assert same_address("https://www.a.com/x/?q=1#f", "https://a.com/x?q=1")
    assert same_address("https://a.com", "https://a.com/")
    assert not same_address("https://a.com/x?q=1", "https://a.com/x?q=2")
    assert not same_address("https://a.com/x", "https://a.com/y")
    assert not same_address("http://a.com/", "https://a.com/")
    assert not same_address("https://a.com:8443/", "https://a.com/")
    assert not same_address("about:blank", "about:blank")
    assert not same_address("", "https://a.com/")
