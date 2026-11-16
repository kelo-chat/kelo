"""The desktop / phone view of a browser tab (BrowserSession.set_view, browser_open(view=...), browser_view): a site that turns one
view away works in the other, with the same sign-ins and cookies; the view that worked is used again on that site; the address
checks hold in both views. Real Chromium on pages served from memory (skipped without Playwright's browser)."""

import pytest

from assistant.agent.fence import plain
from assistant.tools import humanize
from assistant.tools.browser import BrowserPolicyError, BrowserSession, browser_tools, mobile_identity

from test_purchase import PUBLIC

M_ONLY = "https://shop.example.com/deal"        # turns desktop browsers away
D_ONLY = "https://other.example.org/portal"     # turns phones away

pw = pytest.importorskip("playwright.async_api")


def _mobile(headers: dict) -> bool:
    return "Mobile" in headers.get("user-agent", "") and headers.get("sec-ch-ua-mobile", "?1") == "?1"


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)   # as the server's contexts: the window decides the size
        seen: list[dict] = []

        async def serve(route):
            url = route.request.url.split("?")[0]
            h = await route.request.all_headers()
            seen.append({"url": url, **h})
            mobile = _mobile(h)
            if url == M_ONLY:
                ok, body = mobile, "<h1>오늘의 딜</h1><p>모바일 전용 특가 9,900원</p>"
            elif url == D_ONLY:
                ok, body = not mobile, "<h1>Portal</h1><p>Desktop dashboard ready</p>"
            else:
                ok, body = True, "<p>home</p>"
            await route.fulfill(status=200 if ok else 403, content_type="text/html; charset=utf-8",
                                headers={"set-cookie": "sid=abc123; Path=/"} if url.endswith("/login") else {},
                                body=f"<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width\"><body>{body if ok else 'This page is not available on your device.'}</body>")
        await c.route("https://**/*", serve)
        c.seen = seen
        yield c
        await b.close()
    humanize.configure(reset=True)


async def session_on(ctx):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    return s, page, {t.name: t for t in browser_tools(s)}


async def test_a_site_that_turns_desktops_away_works_in_the_phone_view(ctx):
    s, page, tools = await session_on(ctx)
    await s.open("https://shop.example.com/login")               # a sign-in cookie, before switching
    out = plain(await tools["browser_open"].handler({"url": M_ONLY}))
    assert "not available on your device" in out
    out = plain(await tools["browser_view"].handler({"view": "mobile"}))
    assert "mobile view" in out and "모바일 전용 특가" in out
    facts = await page.evaluate("""() => ({ua: navigator.userAgent, mobile: navigator.userAgentData && navigator.userAgentData.mobile,
        platform: navigator.userAgentData && navigator.userAgentData.platform, w: innerWidth, dpr: devicePixelRatio,
        touch: navigator.maxTouchPoints, cookie: document.cookie})""")
    assert "Android" in facts["ua"] and "Mobile" in facts["ua"] and facts["mobile"] is True and facts["platform"] == "Android"
    assert facts["w"] == 412 and abs(facts["dpr"] - 2.625) < 0.01 and facts["touch"] > 0
    assert "sid=abc123" in facts["cookie"]                       # same sign-ins and cookies
    assert _mobile(ctx.seen[-1]) and ctx.seen[-1]["url"] == M_ONLY  # the request itself said phone (UA and client hints)
    # the view that worked is used again on this site, and said
    await s.set_view("desktop")
    out = plain(await tools["browser_open"].handler({"url": M_ONLY}))
    assert "worked on this site before" in out and "모바일 전용 특가" in out and s.view() == "mobile"


async def test_a_site_that_turns_phones_away_works_back_in_the_desktop_view(ctx):
    s, page, tools = await session_on(ctx)
    out = plain(await tools["browser_open"].handler({"url": D_ONLY, "view": "mobile"}))
    assert "not available on your device" in out and s.view() == "mobile"
    out = plain(await tools["browser_view"].handler({"view": "desktop"}))
    assert "desktop view" in out and "Desktop dashboard ready" in out
    facts = await page.evaluate("() => ({ua: navigator.userAgent, mobile: navigator.userAgentData && navigator.userAgentData.mobile, touch: navigator.maxTouchPoints, w: innerWidth})")
    assert "Mobile" not in facts["ua"] and facts["mobile"] is False and facts["w"] != 412
    assert not _mobile(ctx.seen[-1])


async def test_switching_views_asks_nothing_and_the_address_checks_hold_in_both(ctx):
    s, page, tools = await session_on(ctx)
    assert not tools["browser_view"].requires_approval and tools["browser_view"].auto_approve is None
    assert "try the other view" in tools["browser_view"].description
    assert tools["browser_view"].validate({"view": "tablet"})
    for view in ("mobile", "desktop"):
        await s.set_view(view)
        with pytest.raises(BrowserPolicyError):
            await s.open("http://127.0.0.1:8080/admin")
        with pytest.raises(BrowserPolicyError):
            await s.open("http://192.168.0.1/")


def test_the_phone_identity_matches_the_desktop_brands():
    p = mobile_identity("131.0.6778.85")
    md = p["userAgentMetadata"]
    assert "Chrome/131.0.0.0 Mobile Safari" in p["userAgent"] and md["mobile"] is True and md["platform"] == "Android"
    assert {b["brand"] for b in md["brands"]} >= {"Chromium", "Google Chrome"} and md["formFactors"] == ["Mobile"]
