"""browser_fill_card's field finder in a real Chromium (skipped when Playwright's browser is not installed): common checkout forms,
a card number split over boxes, expiry as one field or two selects, a payment provider's cross-origin iframe, and a page with no card
form. Pages are served from memory under fake https addresses; nothing goes to the network. Test card numbers only."""

from datetime import datetime, timezone

import pytest

from assistant.page_judge import PageJudge
from assistant.tools.browser import BrowserSession
from fake_page_judge import FakePageJudge

from test_purchase import PUBLIC

pw = pytest.importorskip("playwright.async_api")

VISA, AMEX = "4242424242424242", "378282246310005"
NOW = datetime.now(timezone.utc)
M, Y = NOW.month, NOW.year + 3

PAGES = {
    "https://shop.example.com/autocomplete": """<form>
        <label>Email <input type=email name=email></label>
        <label>Name on card <input autocomplete="cc-name"></label>
        <input autocomplete="cc-number" placeholder="1234 1234 1234 1234">
        <input autocomplete="cc-exp" placeholder="MM / YY">
        <input autocomplete="cc-csc" placeholder="CVC">
        <label>Billing ZIP <input autocomplete="billing postal-code"></label>
        <button>Pay</button></form>""",
    "https://shop.example.com/korean": """<form>
        <div>카드번호 <input name="cardNo1" maxlength="4"><input name="x2" maxlength="4"><input type="password" name="x3" maxlength="4">
        <input type="password" name="x4" maxlength="4"></div>
        <label>유효기간 월 <select name="expMonth"><option value="">월</option>""" + "".join(
        f'<option value="{m:02d}">{m}월</option>' for m in range(1, 13)) + """</select></label>
        <label>유효기간 년 <select name="expYear"><option value="">년</option>""" + "".join(
        f'<option value="{y}">{y}년</option>' for y in range(NOW.year, NOW.year + 10)) + """</select></label>
        <label>CVC <input type="password" name="cvc" maxlength="3"></label>
        <label>우편번호 <input name="zip" value="99999"></label>
        </form>""",
    "https://shop.example.com/framed": """<h1>Checkout</h1><p>Total 24,000</p>
        <iframe src="https://pay.provider.example/frame" width="400" height="300"></iframe>""",
    "https://pay.provider.example/frame": """<input name="cardnumber" placeholder="Card number">
        <input name="exp-date" placeholder="MM/YY" maxlength="5"><input name="cvc" placeholder="CVC">""",
    "https://shop.example.com/nocard": """<form><label>Name <input name=name></label><label>Birth month <select name=month>
        <option>1</option></select></label><label>Coupon code <input name=code></label></form>""",
    "https://shop.example.com/split-exp": """<form><label>Card number <input id=cc maxlength=19></label>
        <label>Expiry month <input id=em placeholder="MM" maxlength=2></label><label>Expiry year <input id=ey placeholder="YY" maxlength=2></label>
        <label>Security code <input id=sc></label><p>Please complete 3-D Secure in your bank app.</p>
        <input id=otp autocomplete="one-time-code" maxlength=6></form>""",
}


@pytest.fixture
async def browser():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True, args=["--site-per-process"])
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        ctx = await b.new_context()

        async def serve(route):
            body = PAGES.get(route.request.url.split("?")[0])
            await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><body>{body or 'not found'}</body>")
        await ctx.route("https://**/*", serve)
        yield ctx
        await b.close()


async def open_session(ctx, url):
    page = await ctx.new_page()

    async def connect():
        return page
    session = BrowserSession(connect=connect, resolver=PUBLIC)
    session.page_judge = PageJudge(FakePageJudge().ask)   # stands in for the owner's main model: it reads the labels of fields without autocomplete
    await (await session.page()).goto(url)
    return session, page


async def values(frame, selector="input, select"):
    return await frame.eval_on_selector_all(selector, "els => els.map(e => [e.name || e.id || e.getAttribute('autocomplete'), e.value])")


async def test_autocomplete_form(browser):
    session, page = await open_session(browser, "https://shop.example.com/autocomplete")
    res = await session.fill_card(VISA, "987", M, Y, "HONG GILDONG", "04524")
    assert res.filled == ["number", "expiry", "cvc", "holder", "postal code"] and res.missing == [] and res.auth == ""
    got = dict(await values(page))
    assert got["cc-number"] == VISA and got["cc-exp"] == f"{M:02d} / {Y % 100:02d}" and got["cc-csc"] == "987"
    assert got["cc-name"] == "HONG GILDONG" and got["billing postal-code"] == "04524" and got["email"] == ""
    assert VISA not in res.text and await page.evaluate("document.querySelectorAll('[data-kelo-cc]').length") == 0   # marks removed


async def test_korean_split_number_selects_and_a_filled_postal_code_left_alone(browser):
    session, page = await open_session(browser, "https://shop.example.com/korean")
    res = await session.fill_card(VISA, "987", M, Y, "HONG", "04524")
    assert set(res.filled) == {"number", "expiry", "cvc"} and res.missing == []
    got = dict(await values(page))
    assert [got["cardNo1"], got["x2"], got["x3"], got["x4"]] == ["4242", "4242", "4242", "4242"]
    assert got["expMonth"] == f"{M:02d}" and got["expYear"] == str(Y) and got["cvc"] == "987"
    assert got["zip"] == "99999"                                                             # someone's own entry is not overwritten


async def test_payment_provider_iframe(browser):
    session, page = await open_session(browser, "https://shop.example.com/framed")
    await page.wait_for_load_state("load")
    res = await session.fill_card(AMEX, "1234", M, Y)
    assert res.filled == ["number (pay.provider.example)", "expiry (pay.provider.example)", "cvc (pay.provider.example)"]
    frame = next(f for f in page.frames if "pay.provider.example" in f.url)
    got = dict(await values(frame))
    assert got["cardnumber"] == AMEX and got["exp-date"] == f"{M:02d}/{Y % 100:02d}" and got["cvc"] == "1234"


async def test_a_page_without_a_card_form_gets_nothing(browser):
    session, page = await open_session(browser, "https://shop.example.com/nocard")
    res = await session.fill_card(VISA, "987", M, Y, "HONG", "04524")
    assert res.filled == [] and res.missing == ["number", "expiry", "cvc"]
    assert all(v in ("", "1") for _, v in await values(page))


async def test_separate_month_and_year_inputs_and_extra_authentication(browser):
    session, page = await open_session(browser, "https://shop.example.com/split-exp")
    res = await session.fill_card(VISA, "987", M, Y)
    got = dict(await values(page))
    assert got["cc"] == VISA and got["em"] == f"{M:02d}" and got["ey"] == f"{Y % 100:02d}" and got["sc"] == "987"
    assert res.filled == ["number", "expiry", "cvc"] and res.auth == "a one-time-code box"   # by structure: the words of the page ("3-D Secure ...") decide nothing
