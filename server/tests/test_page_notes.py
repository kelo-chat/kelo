"""What a page reading adds beyond the page's text (tools/page_notes.py via BrowserSession.snapshot): a window covering the page is
closed with its own close button (never "don't show again") or reported; values that look masked or not loaded get a note; an
address that answered 404 says so. The window finder runs in a real Chromium (skipped without Playwright's browser) on pages served
from memory; the rest uses plain fakes."""

import pytest

from assistant.agent.fence import plain
from assistant.tools import humanize
from assistant.tools.browser import BrowserSession, browser_tools
from assistant.tools.page_notes import masked_values, overlays

from test_purchase import PUBLIC

NOTICE = """<h1>Shop</h1><p>Today's deals: apples 3,000원</p>
<div id=layer style="position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9999">
  <div style="margin:20vh auto;width:300px;background:#fff"><h3>서버 점검 안내</h3><p>11월 12일 02:00-04:00 점검합니다.</p>
  <label><input type=checkbox id=today> 오늘 그만 보기</label>
  <button onclick="window.saidToday=1;document.getElementById('layer').remove()">오늘 그만 보기</button>
  <button onclick="document.getElementById('layer').remove()">닫기</button></div></div>"""
PAGES = {
    "https://shop.example.com/notice": NOTICE,
    # a dialog that can only be confirmed: reported, never pressed
    "https://shop.example.com/confirm": """<p>Account</p><div role=dialog aria-modal=true style="position:fixed;top:30%;left:20%;
        width:60%;height:40%;background:#fff;z-index:50"><p>Your session will end soon.</p><button onclick="window.ok=1">확인</button></div>""",
    # a sign-in window the agent may be using: left alone, nothing said
    "https://shop.example.com/login": """<p>Welcome</p><div role=dialog style="position:fixed;inset:10%;background:#fff;z-index:50">
        <input name=id placeholder=ID><input type=password name=pw><button onclick="this.parentNode.remove()">닫기</button></div>""",
    # a cookie banner: its "reject" button, never "accept"
    "https://shop.example.com/cookies": """<p>News today</p><div class=cookie-banner style="position:fixed;bottom:0;left:0;right:0;
        z-index:1000;background:#eee">We use cookies. <button onclick="window.cookies='all';this.parentNode.remove()">Accept all</button>
        <button onclick="window.cookies='none';this.parentNode.remove()">Reject all</button></div>""",
    # an icon-only close button labelled for screen readers
    "https://shop.example.com/icon": """<p>Article</p><div role=dialog aria-modal=true aria-label="Newsletter" style="position:fixed;
        inset:25%;background:#fff;z-index:100"><p>Sign up for our letter</p><button aria-label="Close" onclick="this.parentNode.remove()">
        <svg width=10 height=10></svg></button></div>""",
    # a date picker the agent's own click opens on the same page
    "https://shop.example.com/picker": """<p>Booking</p><button id=open onclick="document.getElementById('cal').style.display='block'">
        날짜 선택</button><div id=cal role=dialog style="display:none;position:fixed;inset:20%;background:#fff;z-index:100">
        <p>2026년 11월</p><button>12</button><button onclick="this.parentNode.style.display='none'">닫기</button></div>""",
    # a checkout with the payment provider's window: never closed
    "https://shop.example.com/checkout": """<p>Order</p><div role=dialog style="position:fixed;inset:10%;background:#fff;z-index:100">
        <p>Pay</p><button onclick="this.parentNode.remove()">닫기</button></div>""",
    "https://shop.example.com/embed": """<p>Page</p><div style="position:fixed;inset:10%;background:#fff;z-index:100">
        <iframe src="about:blank" width=300 height=200></iframe><button onclick="this.parentNode.remove()">닫기</button></div>""",
    # an "install the app" layer over the whole page: an icon-only cross in the top right corner (an svg, no words, no label)
    "https://shop.example.com/appsheet": """<h1>Results</h1><button id=search onclick="window.searched=1">Search</button>
        <div id=layer style="position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999">
        <a href="intent://open#Intent;scheme=shop;end" id=open style="position:absolute;bottom:20px;left:20px;width:300px;height:50px;background:#fff">Open in app</a>
        <a href="https://store.example.test/app" id=store style="position:absolute;top:100px;right:20px;width:40px;height:40px"><svg width=20 height=20></svg></a>
        <div id=x role=button onclick="document.getElementById('layer').remove()" style="position:absolute;top:10px;right:10px;width:40px;height:40px">
        <svg width=20 height=20><path d="M0 0L20 20M20 0L0 20" stroke="#fff"/></svg></div></div>""",
    # an install layer with no control at all: it only goes away with Escape
    "https://shop.example.com/appesc": """<h1>Results</h1><p>Search results for shoes: many pairs, sizes, colours and prices listed for you.</p><p>More text about the shop and its delivery so that the page is longer than its layer.</p><div id=layer style="position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999">
        <div style="margin:30vh auto;width:300px;background:#fff;padding:10px">Get the app for a better view <a href="market://details?id=x">Install</a></div></div>
        <script>document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('layer').remove(); })</script>""",
    # only a link to the store and the open-in-app control: nothing may be pressed, it is reported
    "https://shop.example.com/applinks": """<h1>Results</h1><div id=layer style="position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999">
        <a href="intent://open#Intent;scheme=shop;end" style="position:absolute;top:10px;right:10px;width:40px;height:40px"><svg width=20 height=20></svg></a>
        <a href="https://store.example.test/app" style="position:absolute;bottom:20px;left:20px">Install the app</a></div>""",
    # a layer the page opens after typing (no new address) that covers the search button
    "https://shop.example.com/lateapp": """<h1>Results</h1><input id=q><button id=search onclick="window.searched=1">Search</button>
        <script>document.getElementById('q').addEventListener('input', () => { if (document.getElementById('layer')) return;
        const d = document.createElement('div'); d.id = 'layer';
        d.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999';
        d.innerHTML = '<a href="market://x" style="position:absolute;bottom:20px;left:20px">Install the app</a><div id=x role=button style="position:absolute;top:10px;right:10px;width:40px;height:40px"><svg width=20 height=20></svg></div>';
        d.querySelector('#x').onclick = () => d.remove(); document.body.appendChild(d); });</script>""",
    "https://shop.example.com/home": """<a id=go href="/notice">Deals</a>""",
    "https://shop.example.com/account": """<div>내 계좌</div><div>예치금 0* 원</div><div class=panel>보유 예치금 40,000원</div>""",
}

pw = pytest.importorskip("playwright.async_api")


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context()

        async def serve(route):
            body = PAGES.get(route.request.url.split("?")[0])
            await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><body>{body or 'not found'}</body>")
        await c.route("https://**/*", serve)
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


async def test_a_notice_window_is_closed_with_its_own_close_button_never_dont_show_today(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://shop.example.com/notice"))
    assert "A layer covered the page" in out and "서버 점검 안내" in out and "Closed it with its own 닫기 button" in out
    assert "not its 오늘 그만 보기 option" in out
    assert await page.evaluate("() => !document.getElementById('layer') && !window.saidToday")
    assert "Today's deals" in out and "점검합니다" not in out.split("]\n", 2)[-1]   # the text is the page behind it


async def test_a_window_without_a_plain_close_button_is_reported_not_pressed(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://shop.example.com/confirm"))
    assert "A pop-up window still covers the page" in out and "no plain close button" in out
    assert await page.evaluate("() => !window.ok")


async def test_a_sign_in_window_is_left_alone(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://shop.example.com/login"))
    assert "covers the page" not in out and "covered the page" not in out
    assert await page.evaluate("() => !!document.querySelector('input[type=password]')")


async def test_a_cookie_banner_is_closed_by_rejecting_never_accepting(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://shop.example.com/cookies"))
    assert "A cookie banner covered the page" in out and "Reject all" in out
    assert await page.evaluate("() => window.cookies") == "none"


async def test_an_icon_close_button_counts(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://shop.example.com/icon"))
    assert "A pop-up window covered the page: Newsletter" in out and "Closed it with its own Close button" in out


async def test_a_window_the_agents_own_click_opened_stays_open(ctx):
    s, page = await session_on(ctx)
    tools = {t.name: t for t in browser_tools(s)}
    await s.open("https://shop.example.com/picker")
    out = plain(await s.click("#open"))
    assert "covered" not in out and "covers" not in out and "2026년 11월" in out
    out = plain(await tools["browser_read"].handler({}))
    assert "covered" not in out and await page.evaluate("() => getComputedStyle(document.getElementById('cal')).display") == "block"


async def test_payment_and_embedded_windows_are_never_closed(ctx):
    s, page = await session_on(ctx)
    for url in ("https://shop.example.com/checkout", "https://shop.example.com/embed"):
        out = plain(await s.open(url))
        assert "covered" not in out and "covers" not in out
        assert await page.evaluate("() => !!document.querySelector('div[style*=fixed]')"), url


async def test_a_notice_on_the_page_a_click_leads_to_is_closed(ctx):
    s, page = await session_on(ctx)
    await s.open("https://shop.example.com/home")
    out = plain(await s.click("#go"))
    assert "Closed it with its own 닫기 button" in out and await page.evaluate("() => !document.getElementById('layer')")


async def test_a_masked_value_and_a_guessed_address_are_flagged(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://shop.example.com/account"))
    assert "look masked, partial or not loaded" in out and "예치금 0* 원" in out and "Never state them as facts" in out
    out = plain(await s.open("https://shop.example.com/my/deposit"))
    assert out.startswith("[This address answered 404 (not found)") and "Do not guess addresses" in out


# ---------------------------------------------------------------------------------------------------------- without a browser
def test_masked_values_are_found_and_plain_numbers_are_not():
    assert masked_values("내 계좌\n예치금 0* 원\n보유 예치금 40,000원") == ["예치금 0* 원"]
    assert masked_values("잔액 1,2**원") == ["잔액 1,2**원"]
    assert masked_values("Card ****-****-1234") and masked_values("PIN ●●●●") and masked_values("Balance $--")
    assert masked_values("Points: - 원") == ["Points: - 원"]
    for fine in ("보유 예치금 40,000원", "Total $12.50", "5 - 3 = 2", "* first item\n* second", "2026-11-09 10:00", "1 • 2 km"):
        assert masked_values(fine) == [], fine


def test_odd_window_readings_are_ignored():
    assert overlays("page text") == [] and overlays(None) == [] and overlays({"found": [{"label": "x"}]}) == []
    got = overlays({"kelo_overlay": 1, "found": [{"kind": "dialog", "label": "Hi", "close": True}, {"label": 3}]})
    assert got == [{"kind": "dialog", "label": "Hi", "close": None, "close_label": "", "skipped": [], "app": False, "backdrop": None}]


class FakePage:
    url = "about:blank"

    def is_closed(self): return False
    async def title(self): return "T"
    async def evaluate(self, js): return "page text"
    async def wait_for_load_state(self, *a, **kw): pass

    async def goto(self, url, **kw):
        self.url = url

        class Resp:
            status = 404
        return Resp()


async def test_a_404_says_the_address_was_a_guess_that_failed_and_open_says_not_to_guess():
    page = FakePage()

    async def connect():
        return page
    humanize.configure(enabled=False)
    try:
        out = await BrowserSession(connect=connect, resolver=PUBLIC).open("https://shop.example.com/made-up")
    finally:
        humanize.configure(reset=True)
    assert out.startswith("[This address answered 404") and "follow its own links, menu or search" in out
    tools = {t.name: t for t in browser_tools(BrowserSession(connect=connect, resolver=PUBLIC))}
    assert "never one you made up" in tools["browser_open"].description
    assert "questions a value you read, read the page again" in tools["browser_read"].description


# ---------------------------------------------------------------------------------------------------------- app-install layers
async def test_an_icon_only_cross_in_the_top_corner_of_an_app_install_layer_is_closed_never_the_open_in_app_link(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://shop.example.com/appsheet"))
    assert "covered the page" in out and "Results" in out
    assert await page.evaluate("() => !document.getElementById('layer')")
    assert await page.evaluate("() => location.href") == "https://shop.example.com/appsheet"   # no link was followed


async def test_a_layer_with_no_control_goes_away_with_escape(ctx):
    s, page = await session_on(ctx)
    await s.open("https://shop.example.com/appesc")
    assert await page.evaluate("() => !document.getElementById('layer')")


async def test_links_to_the_app_or_a_store_are_never_pressed_the_layer_is_reported(ctx):
    s, page = await session_on(ctx)
    out = plain(await s.open("https://shop.example.com/applinks"))
    assert "still covers the page" in out and await page.evaluate("() => location.href") == "https://shop.example.com/applinks"
    assert await page.evaluate("() => !!document.getElementById('layer')")


async def test_a_layer_that_appears_after_typing_is_closed_and_a_covered_target_is_not_waited_for(ctx):
    s, page = await session_on(ctx)
    await s.open("https://shop.example.com/lateapp")
    out = plain(await s.type("#q", "shoes"))
    assert await page.evaluate("() => !document.getElementById('layer')")
    assert "covered the page" in out
    # the same page, layer opened again behind our back, the click's target is under it: closed first, then clicked
    await page.evaluate("() => { document.getElementById('q').dispatchEvent(new Event('input')); }")
    out = plain(await s.click("#search"))
    assert await page.evaluate("() => window.searched === 1 && !document.getElementById('layer')")


async def test_a_target_under_a_layer_that_cannot_be_closed_answers_at_once_with_the_covering_words(ctx):
    import time
    s, page = await session_on(ctx)
    await s.open("https://shop.example.com/applinks")
    await page.evaluate("() => { const b = document.createElement('button'); b.id = 'under'; b.textContent = 'Go'; b.style.cssText = 'position:absolute;top:200px;left:50px;width:100px;height:40px'; document.body.appendChild(b); }")
    start = time.monotonic()
    out = plain(await s.click("#under"))
    assert time.monotonic() - start < 8 and "is covered by" in out and "take over the browser" in out
