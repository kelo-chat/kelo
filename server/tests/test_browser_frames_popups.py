"""Buying through a window the page opens and a frame from another host of the same site (the shape of many ticket, lottery and
booking sites): the window becomes the tab's page, the frame's text is read and its buttons clicked, the
approved order covers the site's other hosts (Public Suffix List), and after confirm_purchase the final button needs no second card.
A click that changes nothing says so. Real Chromium on pages served from memory (skipped without Playwright's browser)."""

from __future__ import annotations

import asyncio

import pytest

from assistant.agent.fence import plain
from assistant.store import Store
from assistant.tools import humanize
from assistant.tools.browser import NO_CHANGE_NOTE, TAB_CLOSED_NOTE
from assistant.tools.sites import registrable, same_site

from fixtures.order_harness import Env

pw = pytest.importorskip("playwright.async_api")

HOME = "https://www.ticketshop.example/"
GAME = "https://ol.ticketshop.example/game"
FRAME = "https://pay.ticketshop.example/frame"

PAGES = {
    HOME: """<h1>Ticket shop</h1><p>이번 주 회차 티켓</p>
<a id=open href="#" onclick="window.open('https://ol.ticketshop.example/game','game','width=900,height=700');return false">구매하기 (새창)</a>
<button id=dead onclick="return false">Send message</button>
<a id=blank href="https://ol.ticketshop.example/game?via=blank" target=_blank>새 탭으로</a>""",
    GAME: """<h1>Game</h1><p>번호를 고르고 구매하세요</p>
<iframe id=ifr src="https://pay.ticketshop.example/frame" width=700 height=420></iframe>
<button id=bye onclick="window.close()">닫기</button>""",
    FRAME: """<p>1장 5,000원</p><button id=auto onclick="document.getElementById('nums').textContent='1 2 3 4 5 6'">자동선택</button>
<div id=nums></div>
<button id=buy onclick="if(confirm('구매하시겠습니까?')){fetch('/buy',{method:'POST'}).then(()=>{document.body.innerHTML='<p>구매 완료 번호 A-1</p>'})}">구매하기</button>""",
}


def test_one_site_is_its_registrable_domain():
    assert registrable("ol.dhlottery.co.kr") == "dhlottery.co.kr" and registrable("www.example.co.uk") == "example.co.uk"
    assert same_site("pay.ticketshop.example", "www.ticketshop.example")
    assert not same_site("badexample.co.kr", "example.co.kr") and not same_site("alice.github.io", "bob.github.io")
    assert registrable("x.y.city.kawasaki.jp") == "city.kawasaki.jp"   # an exception rule of the list
    assert registrable("127.0.0.1") == "127.0.0.1" and registrable("") == ""


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)
        c.bought, c.seen, c.heads = [], [], []

        async def serve(route):
            req = route.request
            url = req.url.split("?")[0]
            h = {**await req.all_headers(), **req.headers}   # req.headers carries what an earlier route changed (route.fallback)
            c.seen.append((url, h.get("user-agent", "")))
            c.heads.append((url, h))
            if url.endswith("/buy") and req.method == "POST":
                c.bought.append(url)
                await route.fulfill(status=200, content_type="application/json", body="{}")
                return
            body = PAGES.get(url, "<p>home</p>")
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width\">"
                                     f"<title>Tickets</title><body>{body}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


async def env_on(ctx) -> Env:
    env = Env(Store(":memory:"), await ctx.new_page())
    env.session.overlay_pause = 0.05
    return env


async def test_an_approved_order_through_a_window_and_a_frame_is_exactly_one_card(ctx):
    env = await env_on(ctx)
    run = await env.run([
        ("browser_open", {"url": HOME}),
        ("begin_order", {"site": "ticketshop.example", "what": "이번 주 티켓 1장 자동"}),
        ("browser_click", {"selector": "#open"}),          # opens the game window on another host of the site
        ("browser_click", {"selector": "#auto"}),          # inside the frame from a third host
        ("confirm_purchase", {"summary": "티켓 1장 (자동 1 2 3 4 5 6)", "amount": 5000, "currency": "KRW", "site": "ticketshop.example"}),
        ("browser_click", {"selector": "#buy"}),           # the final button, in the frame of the window: no second card
        ("browser_read", {}),
    ])
    assert run.card_tools() == ["confirm_purchase"], [(c.tool, c.detail) for c in run.cards]
    assert all(not r.is_error for r in run.results), [(r.name, r.output[:300]) for r in run.results if r.is_error]
    opened = plain(run.result("browser_click", 0).output)
    assert "opened a new window" in opened and "ol.ticketshop.example/game" in opened
    assert "A frame inside the page, from pay.ticketshop.example" in opened and "5,000원" in opened
    assert "1 2 3 4 5 6" in plain(run.result("browser_click", 1).output)
    assert ctx.bought == ["https://pay.ticketshop.example/buy"]                   # bought once
    assert "구매 완료 번호 A-1" in plain(run.result("browser_read").output)
    assert "accepted (inside the purchase the user allowed)" in plain(run.result("browser_click", 2).output)


async def test_after_the_window_closes_the_task_is_back_on_its_opener_tab(ctx):
    env = await env_on(ctx)
    run = await env.run([("browser_open", {"url": HOME}), ("browser_click", {"selector": "#open"}),
                         ("browser_click", {"selector": "#bye"}), ("browser_read", {})])
    closed = plain(run.result("browser_click", 1).output)   # the window closed itself: the reading after the click is the opener
    note = TAB_CLOSED_NOTE.split("{")[0]   # the window's own tab is now the one that is active (brief C1: the popup is a tab of the task)
    assert not run.result("browser_click", 1).is_error and note in closed and f"URL: {HOME}" in closed
    assert f"URL: {HOME}" in plain(run.result("browser_read").output)


async def test_a_click_that_changes_nothing_says_so_and_an_allowed_one_is_not_asked_again(ctx):
    env = await env_on(ctx)
    run = await env.run([("browser_open", {"url": HOME}), ("browser_click", {"selector": "#dead"}),
                         ("browser_click", {"selector": "button:has-text('Send message')"})])
    assert run.card_tools() == ["browser_click"]          # "Send message" asks once; the same button again (no change) does not
    first = plain(run.result("browser_click", 0).output)
    assert NO_CHANGE_NOTE in first
