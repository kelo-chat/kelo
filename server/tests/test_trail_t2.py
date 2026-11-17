"""Data trail phase T2 (docs/data-trail.md): the owner's private values typed into page fields (autocomplete token + the task's site),
and the browser login checks (masked password field, https and the credential's site, typed length) in a real Chromium."""

import re
from pathlib import Path

import pytest

from assistant import form_values
from assistant.outside_rule import OutsideReview, OwnerWords
from assistant.permissions import Gate
from assistant.tools.browser import BrowserPolicyError, BrowserSession

from test_outside_rule import RULE, perms_with_gmail
from test_privacy_guard import FakeHome, FakeShipping, FakeStore, FakeWallet, PrivacyGuard, fake_ask
from assistant.store import Store

ADDRESS = "서울시 마포구 월드컵북로 12길 34, 502호"
PHONE = "010-2345-6789"


# ---------------------------------------------------------------------------------------------------------------- the token table
@pytest.mark.parametrize("attr, kind", [
    ("street-address", "address"), ("shipping address-line1", "address"), ("section-b billing postal-code", "address"),
    ("home tel", "phone"), ("mobile tel-national", "phone"), ("name", "name"), ("email", "email"), ("cc-number", "card"),
    ("off", ""), ("on", ""), ("", ""), ("nonsense", ""), ("street-address tel", ""), (None, ""),
])
def test_the_whatwg_token_table_gives_the_kind_of_a_field(attr, kind):
    assert form_values.field_kind(attr) == kind


def test_a_value_is_vouched_for_only_by_a_token_of_its_own_kind():
    assert form_values.vouches(["address"], "shipping street-address")
    assert form_values.vouches(["phone"], "tel")
    assert not form_values.vouches(["address"], "tel") and not form_values.vouches(["address"], "name")
    assert not form_values.vouches(["address", "phone"], "street-address")          # a field of one kind takes one kind
    assert not form_values.vouches(["health"], "street-address") and not form_values.vouches(["card"], "cc-number")
    assert not form_values.vouches(["address"], "") and not form_values.vouches([], "street-address")


def test_the_token_table_is_data_not_code():
    src = Path(form_values.__file__).read_text(encoding="utf-8")
    assert "postal-code" not in src and "given-name" not in src


# ---------------------------------------------------------------------------------------------------------------- the gate
def gate(field, *, turn=None, order="", granted=()):
    turn = turn or {"source": "chat", "request": "order the flowers on blooms.example"}
    owner = OwnerWords(None, granted=lambda: set(granted))
    owner.people = type("P", (), {"all": lambda self: []})()
    rev = OutsideReview(RULE, owner, seen=lambda: False, turn=lambda: turn, ordered_site=lambda: order)

    async def step(tool, args):
        return "personal", args.get("site", "www.blooms.example")
    g = PrivacyGuard(home=FakeHome(), store=FakeStore(), wallet=FakeWallet(), shipping=FakeShipping(), own_addresses=lambda: ["me@gmail.com"], ask=fake_ask)
    return Gate(perms_with_gmail(Store()), g, browser_step=step, outside=rev, form_field=lambda sel: field.get(sel), site_named=rev.names_site)


FIELDS = {"#ship": {"autocomplete": "shipping street-address", "type": "text"}, "#news": {"autocomplete": "name", "type": "text"},
          "#plain": {"autocomplete": "", "type": "text"}, "#tel": {"autocomplete": "tel", "type": "tel"}}


async def test_the_home_address_into_a_shipping_address_field_on_the_tasks_site_passes():
    v = await gate(FIELDS).check("browser_type", {"selector": "#ship", "text": ADDRESS})
    assert v.action == "pass"


async def test_the_home_address_into_a_newsletter_name_field_asks():
    v = await gate(FIELDS).check("browser_type", {"selector": "#news", "text": ADDRESS})
    assert v.action == "ask" and "home address" in v.card


async def test_a_field_without_a_token_or_one_that_was_not_read_asks_for_private_values():
    assert (await gate(FIELDS).check("browser_type", {"selector": "#plain", "text": ADDRESS})).action == "ask"
    assert (await gate(FIELDS).check("browser_type", {"selector": "#gone", "text": ADDRESS})).action == "ask"


async def test_the_right_token_on_another_site_asks_unless_a_grant_or_the_order_covers_it():
    args = {"selector": "#ship", "text": ADDRESS, "site": "www.elsewhere.example"}
    assert (await gate(FIELDS).check("browser_type", args)).action == "ask"
    assert (await gate(FIELDS, granted={"site:elsewhere.example"}).check("browser_type", args)).action == "pass"
    assert (await gate(FIELDS, order="elsewhere.example").check("browser_type", args)).action == "pass"


async def test_a_phone_needs_a_phone_token_and_an_address_token_does_not_vouch_for_it():
    assert (await gate(FIELDS).check("browser_type", {"selector": "#tel", "text": PHONE})).action == "pass"
    assert (await gate(FIELDS).check("browser_type", {"selector": "#ship", "text": PHONE})).action == "ask"


async def test_other_private_kinds_always_ask_whatever_the_token():
    f = {"#h": {"autocomplete": "street-address"}}
    assert (await gate(f).check("browser_type", {"selector": "#h", "text": "건강: 땅콩 알레르기와 고혈압 약 복용"})).action == "ask"


async def test_the_privacy_marker_is_never_passed_by_a_token():
    from assistant.injection_watch import LeakMarker
    g = gate(FIELDS)
    marker = LeakMarker(Store())
    g.guard.marker = marker
    v = await g.check("browser_type", {"selector": "#ship", "text": ADDRESS + " " + marker.token()})
    assert v.action == "off"


# ---------------------------------------------------------------------------------------------------------------- the login checks
pw = pytest.importorskip("playwright.async_api")
SECRET = "hunter2-Pa$$w0rd!"
LOGIN = """<form><input id="u" name="user" type="text"><input id="p" name="pass" type="password" {extra}><input type="submit" value="Go"></form>
{script}"""
PAGES = {
    "https://login.example.com/ok": LOGIN.format(extra="", script=""),
    "https://login.example.com/maxlen": LOGIN.format(extra='maxlength="5"', script=""),
    # the field turns into a plain text box when it takes the focus (a "show password" page script)
    "https://login.example.com/unmask": LOGIN.format(extra="", script="<script>document.getElementById('p').addEventListener('focus', e => { e.target.type = 'text'; });</script>"),
    # CSS can not unmask a password input (the browser's own rule wins), a masked text box is no password field either
    "https://login.example.com/two": """<form><input type="text" id="u"><input type="password" id="p"><input type="password" id="p2"></form>""",
    "https://login.example.com/textbox": """<form><input type="text" id="u"><input type="text" id="p" style="-webkit-text-security: disc"></form>""",
    "https://other.example.net/ok": LOGIN.format(extra="", script=""),
}


@pytest.fixture
async def ctx():
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


async def session_on(ctx, url):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))])
    s.settle_seconds, s.sign_in_wait = 0, 0
    await (await s.page()).goto(url)
    return s, page


async def values(page):
    return await page.evaluate("() => Object.fromEntries([...document.querySelectorAll('input')].map(e => [e.id, e.value]))")


SITE = lambda url: "login.example.com" in url  # noqa: E731


async def test_a_masked_password_field_is_typed_and_its_length_checked(ctx):
    s, page = await session_on(ctx, "https://login.example.com/ok")
    await s.fill_login("kim", SECRET, submit=False, origin_ok=SITE)
    got = await values(page)
    assert got["u"] == "kim" and got["p"] == SECRET


async def test_a_password_field_that_is_not_masked_when_the_typing_starts_gets_nothing(ctx):
    s, page = await session_on(ctx, "https://login.example.com/unmask")
    with pytest.raises(BrowserPolicyError, match="not masked"):
        await s.fill_login("kim", SECRET, submit=False, origin_ok=SITE)
    assert (await values(page))["p"] == ""


async def test_a_text_box_is_not_a_password_field(ctx):
    s, page = await session_on(ctx, "https://login.example.com/textbox")
    with pytest.raises(BrowserPolicyError, match="no sign-in form"):
        await s.fill_login("kim", SECRET, step="password", submit=False, origin_ok=SITE)
    assert (await values(page))["p"] == ""


async def test_a_page_with_two_password_fields_is_ambiguous_and_nothing_is_typed(ctx):
    s, page = await session_on(ctx, "https://login.example.com/two")
    with pytest.raises(BrowserPolicyError, match="ambiguous"):
        await s.fill_login("kim", SECRET, submit=False, origin_ok=SITE)
    assert await values(page) == {"u": "", "p": "", "p2": ""}


async def test_a_typing_length_that_does_not_match_is_reported_and_the_field_cleared(ctx):
    s, page = await session_on(ctx, "https://login.example.com/maxlen")
    with pytest.raises(BrowserPolicyError) as e:
        await s.fill_login("kim", SECRET, submit=True, origin_ok=SITE)
    assert "length does not match" in str(e.value) and "Do not retry" in str(e.value) and SECRET not in str(e.value)
    assert (await values(page))["p"] == ""
    assert page.url.endswith("/maxlen")                                          # nothing was sent


async def test_another_site_is_refused_at_the_moment_of_typing(ctx):
    s, page = await session_on(ctx, "https://other.example.net/ok")
    with pytest.raises(BrowserPolicyError, match="credential's own site"):
        await s.fill_login("kim", SECRET, submit=False, origin_ok=SITE)
    assert (await values(page))["p"] == ""


async def test_the_source_of_a_typed_secret_is_never_read_back(ctx):
    src = Path(__import__("assistant.tools.browser", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    state = src[src.index("_LOGIN_STATE = "):src.index("_LOGIN_STATE = ") + 700]
    assert "length: String(e.value" in state and not re.search(r"value:\s*e\.value|return[^;]*e\.value\s*[,}]", state)
