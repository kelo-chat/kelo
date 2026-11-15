"""A click that only opens a window or a page prepares; it never gets the "this may confirm a payment" card, whatever its words
(tools/commits.structure, read in a real Chromium from the element, its inline code, the page functions it calls and the listeners
added with addEventListener). A form submit or a request still asks. And an order the user asked for in chat starts by itself when
the agent buys without begin_order (tools/order.Orders.start_implicit), while the final payment still asks.

Pages are served from memory; skipped without Playwright's Chromium."""

import pytest

from assistant.tools import humanize
from assistant.tools.browser import BrowserSession, browser_tools
from assistant.tools.commits import judge_click, structure
from assistant.tools.order import Orders, buy_request
from assistant.workers import current_turn

from test_purchase import PUBLIC

SHOP = "https://shop.example.com"
PAGES = {
    # a lottery-style "buy" that only opens the game window: inline handler calling a page function that calls window.open
    "/game": """<h1>로또 6/45</h1><script>function openGame(u){ var w = window.open(u, 'game', 'width=900,height=700'); w && w.focus(); }</script>
        <a id=buywin href="javascript:openGame('/game/play')">바로구매 새창</a>
        <button id=buybtn onclick="openGame('/game/play')">구매하기</button>
        <a id=buyblank href="/game/play" target="_blank">바로구매</a>
        <a id=buyplain href="/game/play">구매하기</a>
        <button id=buylistener>바로 구매</button>
        <button id=buypost>구매하기</button>
        <script>
          document.getElementById('buylistener').addEventListener('click', () => { window.open('/game/play', '_blank'); });
          document.getElementById('buypost').addEventListener('click', () => { fetch('/api/buy', {method: 'POST', body: '1'}); });
        </script>""",
    # the window the opener shows: its final pay is a form submit and always asks
    "/game/play": """<h1>구매</h1><form action="/api/pay" method=post><label>결제 수단 <select name=pay_method><option>예치금</option></select></label>
        <button id=pay type=submit>결제하기</button></form>""",
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
            path = "/" + route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
            body = PAGES.get(path)
            await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><body>{body or 'ok'}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


async def on(ctx, path, orders=None):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    await s.open(SHOP + path)
    tools = {t.name: t for t in browser_tools(s, orders=orders)}
    return s, tools


async def no_card(tools, selector):
    return await tools["browser_click"].auto_approve({"selector": selector})


@pytest.mark.parametrize("selector", ["#buywin", "#buybtn", "#buyblank", "#buyplain", "#buylistener"])
async def test_a_buy_that_only_opens_a_window_or_a_page_gets_no_card(ctx, selector):
    s, tools = await on(ctx, "/game")
    assert await no_card(tools, selector) is True
    assert s.judged(selector) == ""
    assert structure(s.judged_info(selector)) in ("opens", "goes")


async def test_a_buy_that_sends_a_request_still_asks(ctx):
    s, tools = await on(ctx, "/game")
    assert await no_card(tools, "#buypost") is False
    assert s.judged("#buypost") == "pay"
    assert structure(s.judged_info("#buypost")) == "changes"


async def test_the_submit_pay_button_asks(ctx):
    s, tools = await on(ctx, "/game/play")
    assert await no_card(tools, "#pay") is False
    assert s.judged("#pay") == "pay"
    assert "payment" in (await tools["browser_click"].describe({"selector": "#pay"})).lower()


async def test_an_order_starts_from_the_request_when_the_agent_skips_begin_order(ctx):
    orders = Orders(scope=lambda: "turn:t1")
    started = []

    def implicit(host):
        turn = current_turn.get() or {}
        if not buy_request(str(turn.get("request") or "")):
            return None
        o = orders.start(host, turn["request"])
        o.implicit = True
        started.append(o)
        return o
    orders.implicit = implicit
    token = current_turn.set({"request": "로또 5게임 사줘", "turn_id": "t1"})
    try:
        # the order page's "next step" (judged pay by its words, a submit on an order form): covered by the order started for it
        s, tools = await on(ctx, "/game", orders)
        assert await no_card(tools, "#buypost") is True
        assert started and started[0].site == "example.com" and started[0].what == "로또 5게임 사줘"
        assert any("begin_order" in n for n in s._notes.get("owner", []))
        # the final pay still asks
        s2, tools2 = await on(ctx, "/game/play", orders)
        assert await no_card(tools2, "#pay") is False
    finally:
        current_turn.reset(token)


async def test_no_order_starts_when_the_user_did_not_ask_to_buy(ctx):
    orders = Orders(scope=lambda: "turn:t2")
    orders.implicit = lambda host: orders.start(host, "x") if buy_request(str((current_turn.get() or {}).get("request") or "")) else None
    token = current_turn.set({"request": "로또 당첨 번호 알려줘", "turn_id": "t2"})
    try:
        s, tools = await on(ctx, "/game", orders)
        assert await no_card(tools, "#buypost") is False
        assert orders.current() is None
    finally:
        current_turn.reset(token)


def test_without_the_structure_read_the_words_still_decide():
    # an element read the old way (no code read): a pay link keeps asking, as before
    old = {"tag": "a", "href": "/order/pay", "text": "결제하기", "words": "", "url": "https://shop.example/item/1", "title": "Item"}
    assert judge_click(old, "text=결제하기") == "pay"
    # read structurally: a plain link to a payment address still asks; one to an ordinary page does not
    new = {**old, "structure_read": True, "handler_src": "", "link_target": ""}
    assert judge_click(new, "text=결제하기") == "pay"
    assert judge_click({**new, "href": "/game/play", "text": "구매하기"}, "text=구매하기") == ""
    # deleting on a plain link still asks
    assert judge_click({**new, "href": "/account/leave", "text": "회원 탈퇴"}, "text=회원 탈퇴") == "delete"
