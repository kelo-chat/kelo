"""A site that answers with an access-denied page ends the step fast with a clear text; a host that blocked twice is not tried again."""

import pytest

from assistant.agent.fence import plain
from assistant.tools import humanize
from assistant.tools.browser import BrowserSession
from assistant.tools.challenge import blocked

from test_purchase import fake_resolver

PUBLIC = fake_resolver({h: "93.184.216.34" for h in ("denied.example.test", "big.example.test", "home.example.test")})

DENIED = "<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>You don't have permission to access this page.<p>Reference #18.4f2e1002.1731700000.9a8b7c</p></body></html>"
BIG = "<title>Forbidden area</title><h1>Members only</h1>" + "<p>Some long page text about the members area. </p>" * 80


def test_blocked_recognises_an_edge_access_denied_page_and_nothing_else():
    why = blocked(403, {"server": "AkamaiGHost"}, "Access Denied", "Access Denied\nYou don't have permission\nReference #18.4f2e")
    assert why == 'HTTP 403 "Access Denied"'
    assert blocked(403, {"x-cache": "Error from cloudfront"}, "ERROR: The request could not be satisfied", "The request could not be satisfied.") is not None
    assert blocked(200, {"server": "AkamaiGHost"}, "Access Denied", "Access Denied") is None                      # a normal answer
    assert blocked(403, {"server": "nginx"}, "Members", "Please sign in to see this page. " * 80) is None          # a big 403 page
    assert blocked(403, {"server": "nginx"}, "Members only", "Please sign in") is not None                          # a short 403 is a refusal page, whatever it says
    assert blocked(401, {"server": "nginx"}, "Members only", "Please sign in") is None                              # 401 asks to sign in: not a block
    assert blocked(404, {}, "Not found", "Not found") is None
    # a human check (a puzzle a person can solve) keeps its own path
    assert blocked(403, {"cf-mitigated": "challenge", "server": "cloudflare"}, "Attention", "Access denied") is None
    assert blocked(403, {"x-px-cookie": "1"}, "Access to this page has been denied", "Press and hold") is None


@pytest.fixture
async def ctx():
    pw = pytest.importorskip("playwright.async_api")
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context()
        hits = []

        async def serve(route):
            url = route.request.url
            hits.append(url)
            if "denied.example.test" in url:
                await route.fulfill(status=403, content_type="text/html", headers={"Server": "AkamaiGHost"}, body=DENIED)
            elif "big.example.test" in url:
                await route.fulfill(status=403, content_type="text/html", body=BIG)
            else:
                await route.fulfill(status=200, content_type="text/html", body="<h1>Home</h1><a id=go href='https://denied.example.test/s?q=1'>search</a>")
        await c.route("https://**/*", serve)
        c.hits = hits
        yield c
        await b.close()
    humanize.configure(reset=True)


async def session_on(ctx):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    return s, page


async def test_an_access_denied_page_says_so_and_a_host_blocked_twice_is_not_tried_again(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://denied.example.test/a"))
    assert "is blocking Kelo's browser" in out and 'HTTP 403 "Access Denied"' in out and "Do not retry" in out
    assert "Blocked again" not in out and "Access Denied" in out          # the page's own text stays
    n = len(ctx.hits)
    out = plain(await s.open("https://denied.example.test/b"))            # the refusal paused the site: answered at once, nothing loaded
    assert "will not load" in out and "is blocking" not in out and len(ctx.hits) == n
    s._refused_until.clear()                                                    # six hours later it may be tried again
    s._blocked[(s._task(), "denied.example.test")] = (1, s.clock() - 700)
    n = len(ctx.hits)
    await s.open("https://denied.example.test/d")
    assert len(ctx.hits) > n


async def test_a_click_on_the_page_of_a_host_that_blocked_twice_is_answered_without_clicking(ctx):
    s, page = await session_on(ctx)
    await s.open("https://denied.example.test/a")
    n = len(ctx.hits)
    out = plain(await s.click("#go"))
    assert "will not load" in out and len(ctx.hits) == n


async def test_a_long_403_page_is_an_ordinary_page(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://big.example.test/x"))
    assert "blocking Kelo's browser" not in out and "Members only" in out


def test_a_korean_permission_page_is_blocked():
    text = "요청하신 페이지의 사용권한이 없습니다.\n입력하신 페이지주소는 사용권한이 제한된 페이지입니다. Ref: 1A2B_3C4D"
    assert blocked(403, {}, "", text)
    assert blocked(200, {}, "", text) is None
