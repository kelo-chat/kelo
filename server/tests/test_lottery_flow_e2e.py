"""A purchase through a window opened from a tab, the shape of a lottery site (2026-11-10, tasks 162-165: the quantity
list was never set, and after the approved payment the buy button "did nothing"). A local fake on real Chromium:
sign-in -> "바로구매" opens a window on another host of the site -> a native quantity <select> AND a custom quantity dropdown ->
"구매하기" asks confirm("구매하시겠습니까?") -> result page. Quantity 5 is set in one step per list; after confirm_purchase the click
goes through, the confirm is accepted, the result page is reached, and there is no second card."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from assistant.agent.fence import plain
from assistant.store import Store
from assistant.tools import humanize

from fixtures.order_harness import Env

pw = pytest.importorskip("playwright.async_api")

KEY = Fernet.generate_key().decode()
LOGIN = "https://www.lotto.example/login"
MAIN = "https://www.lotto.example/main"
GAME = "https://ol.lotto.example/game"
PAGES = {
    LOGIN: "<h1>로그인</h1><form action=/main method=post><input name=u><input name=p type=password><button>로그인</button></form>",
    MAIN: """<h1>로또</h1><p>나의 예치금 20,000원</p>
<button id=buynow onclick="window.open('https://ol.lotto.example/game','game','width=900,height=700')">바로구매</button>""",
    GAME: """<h1>구매하기</h1>
<label for=qty>수량</label><select id=qty name=qty onchange="document.getElementById('sum').textContent=(this.value*1000).toLocaleString()+'원'">
<option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select>
<div>자동 게임 수: <div id=cqty role=button aria-haspopup=listbox onclick="document.getElementById('cl').hidden=false">1</div>
<ul id=cl role=listbox hidden>""" + "".join(
        f"<li role=option onclick=\"document.getElementById('cqty').textContent='{n}';document.getElementById('cl').hidden=true\">{n}</li>"
        for n in range(1, 6)) + """</ul></div>
<p>합계 <span id=sum>1,000원</span></p>
<button id=buy onclick="if(confirm('구매하시겠습니까?')){fetch('/buy',{method:'POST',body:document.getElementById('qty').value+','+document.getElementById('cqty').textContent}).then(()=>{document.body.innerHTML='<h1>구매 완료</h1><p>5게임 5,000원</p>'})}">구매하기</button>""",
}


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)
        c.bought = []

        async def serve(route):
            req = route.request
            url = req.url.split("?")[0]
            if url.endswith("/buy") and req.method == "POST":
                c.bought.append(req.post_data)
                await route.fulfill(status=200, content_type="application/json", body="{}")
                return
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width\">"
                                     f"<title>Lotto</title><body>{PAGES.get(url, '<p>x</p>')}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


async def test_a_purchase_in_a_window_sets_the_quantity_and_goes_through_after_one_card(ctx, tmp_path):
    store = Store(tmp_path / "a.db", KEY)
    store.save_credential("lotto", "Lotto", ["www.lotto.example"], {}, kind="login")
    store.set_login("lotto", "kim", "secret-pw")
    env = Env(store, await ctx.new_page())
    env.session.overlay_pause = 0.05
    run = await env.run([
        ("browser_open", {"url": LOGIN}),
        ("browser_login", {"name": "lotto"}),
        ("begin_order", {"site": "lotto.example", "what": "로또 자동 5게임"}),
        ("browser_click", {"selector": "#buynow"}),                     # the purchase window
        ("browser_type", {"selector": "select#qty", "text": "5"}),       # a native list: one step
        ("browser_type", {"selector": "#cqty", "text": "5"}),            # a custom list: one step
        ("confirm_purchase", {"summary": "로또 자동 5게임", "amount": 5000, "currency": "KRW", "site": "lotto.example"}),
        ("browser_click", {"selector": "#buy"}),                         # confirm("구매하시겠습니까?") follows
        ("browser_read", {}),
    ])
    assert all(not r.is_error for r in run.results), [(r.name, r.output[:300]) for r in run.results if r.is_error]
    assert run.card_tools() == ["browser_login", "confirm_purchase"], [(c.tool, c.detail) for c in run.cards]   # no second card
    opened = plain(run.result("browser_click", 0).output)
    assert "opened a new window" in opened and "select#qty" in opened and "1 | 2 | 3 | 4 | 5" in opened and "Lists on the page" in opened
    assert "it now shows “5”" in plain(run.result("browser_type", 0).output)
    assert "chose “5”; it now shows “5”" in plain(run.result("browser_type", 1).output)
    bought = plain(run.result("browser_click", 1).output)
    assert "accepted (inside the purchase the user allowed)" in bought
    assert ctx.bought == ["5,5"]                                                      # bought once, five games
    assert "구매 완료" in plain(run.result("browser_read").output)
