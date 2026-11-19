"""hc-pagefields (docs/no-word-lists.md): the page and field decisions that used to read words are made by structure, then by the page
judge (page_judge.PageJudge), and by the SAFER side when neither can tell. Real Chromium on local pages (skipped without Playwright's
browser); the judge is a test stand-in (fake_page_judge.py), no model, no network."""

from __future__ import annotations

import ast
import json
import re
import unicodedata
from pathlib import Path

import pytest

from assistant import page_judge as pj
from assistant.page_judge import PageJudge
from assistant.signin_state import sign_in_address, still_signing_in, under_sign_in
from assistant.tools import browser as browser_mod
from assistant.tools import keypad
from assistant.tools import takeover
from assistant.tools.browser import BrowserSession, browser_owner

from fake_page_judge import FakePageJudge
from fixtures.order_harness import public as PUBLIC

pw = pytest.importorskip("playwright.async_api")


# ------------------------------------------------------------------------------------------------------------ the judge, strict
async def test_the_page_judge_answers_only_in_its_exact_shapes_and_asks_once_per_page_text():
    good = {"kind": "app", "expired": False, "seconds": 90, "code": 0, "qr": True, "resend": 1, "line": 0}
    calls = []

    async def ask(system, user):
        calls.append(system)
        return dict(good)
    judge = PageJudge(ask)
    read = {"text": "Approve in your app 12:00", "codes": ["482 913"], "controls": ["a", "b"], "lines": ["Approve in your app please"]}
    assert (await judge.payment_step(read))["resend"] == 1
    assert await judge.payment_step({**read, "text": "Approve in your app 11:59"}) is not None and len(calls) == 1   # digits do not ask again
    for bad in ({**good, "kind": "maybe"}, {**good, "code": 3}, {**good, "resend": -1}, {**good, "seconds": True}, {**good, "expired": "no"},
                {**good, "line": 9}, "text", None, {"kind": "none"}):
        async def ask_bad(system, user, bad=bad):
            return bad
        assert await PageJudge(ask_bad).payment_step(read) is None, bad
    assert await PageJudge(FakePageJudge(down=True).ask).payment_step(read) is None

    async def boom(system, user):
        raise RuntimeError("model down")
    assert await PageJudge(boom).verification({"text": "x"}) is None


async def test_the_field_judge_may_name_only_known_refs_and_kinds_of_its_form():
    rows = [{"ref": "f0", "label": "x"}, {"ref": "f1", "label": "y"}]
    for answer, want in (({"fields": {"f0": "number", "f1": "cvc"}}, {"f0": "number", "f1": "cvc"}),
                         ({"fields": {"f0": "number-0"}}, {"f0": "number-0"}), ({"fields": {}}, {}),
                         ({"fields": {"f9": "number"}}, None), ({"fields": {"f0": "tel"}}, None), ({"fields": []}, None), ({}, None)):
        async def ask(system, user, answer=answer):
            return answer
        assert await PageJudge(ask).fields("card", rows) == want, answer
    assert await PageJudge(FakePageJudge().ask).fields("nothing", rows) is None


# ------------------------------------------------------------------------------------------------------------ real pages
@pytest.fixture
async def browser():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context()
        yield c
        await b.close()


async def served(ctx, body: str, url: str = "https://shop.example.com/x"):
    page = await ctx.new_page()

    async def serve(route):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=f"<!doctype html><meta charset=utf-8><body>{body}</body>")
    await page.route("https://**/*", serve)
    await page.goto(url)
    return page


KEYS = "".join(f"<img src='data:image/gif;base64,R0lGODlhAQABAAAAACw=' width=48 height=40 style='cursor:pointer' onclick='1'>" for _ in range(12))
PADS = {
    "one-password-box": "<input type=password maxlength=6>",
    "numeric-password-box": "<input type=password inputmode=numeric>",
    "six-password-boxes": "".join("<input type=password maxlength=1 size=1>" for _ in range(6)),
}


@pytest.mark.parametrize("name,body,mode,boxes", [
    ("a one-box PIN by its shape", PADS["one-password-box"], "input", 1),
    ("a numeric password box by its inputmode", PADS["numeric-password-box"], "input", 1),
    ("six masked boxes, one per digit", PADS["six-password-boxes"], "input", 6),
    ("no label or word is read: the same box with another language around it", "<p>결제 비밀번호 6자리</p><input type=password maxlength=6 name=x>", "input", 1),
    ("a login form (user name and a long password): not a PIN", "<input name=u autocomplete=username><input type=password>", "", 0),
    ("an open text box of PIN length is not a PIN box", "<p>PIN</p><input type=text maxlength=6>", "", 0),
    ("a row of OPEN one-character boxes is a one-time code, not a PIN", "".join("<input maxlength=1 size=1>" for _ in range(6)), "", 0),
    ("two password boxes of PIN shape: ambiguous, not typed", "<input type=password maxlength=6><input type=password maxlength=6>", "", 0),
    ("a one-time-code box is never a PIN box", "<input type=password maxlength=6 autocomplete=one-time-code>", "", 0),
    ("the words 'secure keyboard' on a page decide nothing", "<p>보안 키보드 / security keyboard / install the plug-in</p>", "", 0),
    ("a grid of blank keys is protected by structure", f"<div style='display:grid;grid-template-columns:repeat(4,60px)'>{KEYS}</div>", "protected", 0),
    ("a canvas next to a password box is protected", "<canvas width=200 height=100></canvas><input type=password readonly>", "protected", 0),
    ("a canvas alone is not", "<canvas width=200 height=100></canvas>", "", 0),
    ("an embedded plug-in object is protected", "<object data='x' width=100 height=100></object>", "protected", 0),
])
async def test_the_pin_field_is_found_by_structure_only(browser, name, body, mode, boxes):
    page = await served(browser, body)
    got = await page.evaluate(keypad.FIND_JS)
    assert got["mode"] == mode and got["boxes"] == boxes, name
    assert "words" not in got


async def test_a_keypad_with_digit_buttons_is_still_typed(browser):
    keys = "".join(f"<button onclick='1'>{d}</button>" for d in "0123456789")
    page = await served(browser, keys)
    assert (await page.evaluate(keypad.FIND_JS))["mode"] == "text"


# ------------------------------------------------------------------------------------------------------------ the take-over journal
async def test_the_journal_quotes_only_what_structure_says_is_public_and_masks_the_rest(browser):
    page = await browser.new_page()
    seen = []

    async def note(source, payload):
        seen.append(json.loads(payload))
    await page.expose_binding(takeover.BINDING, note)
    body = ("<input id=text aria-label='anything'><input id=pw type=password><input id=cc autocomplete='cc-number'><input id=otp autocomplete='one-time-code'>"
            "<input id=tel type=tel><input id=q type=search><input id=check type=checkbox><select id=sel><option>A</option><option>B</option></select>"
            "<iframe id=f srcdoc=\"<input id=inner>\"></iframe><textarea id=ta></textarea>")
    await page.set_content(f"<!doctype html><body>{body}</body>")
    await page.evaluate(takeover.SCRIPT.replace("%(binding)s", takeover.BINDING).replace("%%", "%"))
    for sel, val in (("#text", "secret-1"), ("#pw", "secret-2"), ("#cc", "4242424242424242"), ("#otp", "123456"), ("#tel", "01012345678"),
                     ("#q", "blue mugs"), ("#ta", "my notes")):
        await page.fill(sel, val)
        await page.dispatch_event(sel, "change")
    await page.check("#check")
    await page.select_option("#sel", "B")
    await page.wait_for_timeout(200)
    fields = {e["label"]: e for e in seen if e["k"] == "field"}
    quoted = {e["label"]: e["value"] for e in fields.values() if not e["secret"]}
    assert set(quoted.values()) == {"blue mugs", "on", "B"}, quoted
    assert all(e["secret"] and e["value"] is None for e in fields.values() if e["value"] not in ("blue mugs", "on", "B"))
    blob = json.dumps(seen)
    for hidden in ("secret-1", "secret-2", "4242424242424242", "123456", "01012345678", "my notes"):
        assert hidden not in blob, hidden
    frame = page.frames[1]
    await frame.fill("#inner", "typed in a frame")
    await frame.dispatch_event("#inner", "change")
    await page.wait_for_timeout(100)
    assert "typed in a frame" not in json.dumps(seen)   # (a frame is masked; the script may not even run there)


async def test_a_clicked_fields_own_value_is_never_its_label(browser):
    page = await browser.new_page()
    seen = []

    async def note(source, payload):
        seen.append(json.loads(payload))
    await page.expose_binding(takeover.BINDING, note)
    await page.set_content("<!doctype html><body><input id=x value='4242 4242 4242 4242'><input type=submit id=go value='Pay now'></body>")
    await page.evaluate(takeover.SCRIPT.replace("%(binding)s", takeover.BINDING).replace("%%", "%"))
    await page.click("#x")
    await page.click("#go")
    await page.wait_for_timeout(100)
    labels = [e["label"] for e in seen if e["k"] == "click"]
    assert "4242" not in " ".join(labels) and "Pay now" in labels


# ------------------------------------------------------------------------------------------------------------ browser fields
async def session_on(ctx, url, body, judge=None):
    page = await served(ctx, body, url)

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.page_judge = judge
    return s, page


NO_AC_CARD = ("<form><label>카드번호 <input name=n maxlength=19></label><label>유효기간 <input name=e maxlength=5></label>"
              "<label>보안코드 <input name=c maxlength=3></label></form>")
VISA = "4242424242424242"


async def test_card_fields_without_autocomplete_are_typed_only_after_the_judge_names_them(browser):
    s, page = await session_on(browser, "https://shop.example.com/pay", NO_AC_CARD)
    res = await s.fill_card(VISA, "987", 12, 2031)
    assert res.filled == [] and set(res.missing) == {"number", "expiry", "cvc"}      # no judge: nothing is typed on a label
    assert await page.eval_on_selector_all("input", "els => els.map(e => e.value)") == ["", "", ""]
    s.page_judge = PageJudge(FakePageJudge().ask)
    res = await s.fill_card(VISA, "987", 12, 2031)
    assert res.filled == ["number", "expiry", "cvc"] and res.missing == []


async def test_a_judge_that_names_a_phone_sized_box_as_the_card_number_is_not_obeyed(browser):
    async def ask(system, user):
        return {"fields": {"f0": "number", "f1": "exp", "f2": "cvc"}}
    s, page = await session_on(browser, "https://shop.example.com/pay", "<input maxlength=11><input maxlength=5><input maxlength=3>", PageJudge(ask))
    res = await s.fill_card(VISA, "987", 12, 2031)
    assert "number" not in res.filled and (await page.eval_on_selector_all("input", "els => els.map(e => e.value)"))[0] == ""


async def test_shipping_fields_by_autocomplete_and_phone_type_alone_and_the_rest_by_the_judge(browser):
    body = ("<input autocomplete='shipping postal-code'><input type=tel><input name=a><input type=email><input autocomplete='billing name'>"
            "<input autocomplete='username'><input id=late>")
    s, page = await session_on(browser, "https://shop.example.com/ship", body)
    got = await page.evaluate(browser_mod._TAG_ADDRESS_FIELDS, None)
    assert sorted(got["found"]) == ["postal", "tel"]                                    # structure only; the unlabeled box is open
    assert [r["ref"] for r in got["open"]] == ["f2", "f6"]                              # the e-mail, billing and user-name fields never reach the judge
    again = await page.evaluate(browser_mod._TAG_ADDRESS_FIELDS, {"f2": "line1"})
    assert sorted(again["found"]) == ["line1", "postal", "tel"]


async def test_a_code_box_is_the_one_box_shaped_like_a_code_or_a_row_of_one_character_boxes(browser):
    for body, boxes in (("<input autocomplete=one-time-code>", 1), ("<input inputmode=numeric maxlength=6>", 1),
                        ("<input maxlength=6><input maxlength=8>", 0), ("<input name=zip maxlength=5 autocomplete=postal-code>", 0),
                        ("".join("<input maxlength=1>" for _ in range(6)), 6), ("<input name=otp><input name=q>", 0)):
        page = await served(browser, body)
        assert (await page.evaluate(browser_mod._TAG_CODE_FIELDS, None))["boxes"] == boxes, body


# ------------------------------------------------------------------------------------------------------------ extra authentication
async def test_extra_authentication_is_a_one_time_code_box_or_leaving_the_shop_never_words(browser):
    s, page = await session_on(browser, "https://shop.example.com/pay", "<p>Please complete 3-D Secure in your bank app 본인인증</p>")
    token = browser_owner.set("owner")
    try:
        s._pages["owner"] = page
        s.allow_purchase("shop.example.com")
        assert await s.extra_auth_signs() == ""                                     # the words alone: nothing
        await page.set_content("<!doctype html><input autocomplete=one-time-code maxlength=6>")
        assert await s.extra_auth_signs() == "a one-time-code box"
        await page.set_content("<!doctype html><input autocomplete=cc-csc maxlength=6><input type=tel maxlength=6 autocomplete=tel>")
        assert await s.extra_auth_signs() == ""
        await page.goto("https://bank.example.org/acs")
        assert "left the shop's site" in await s.extra_auth_signs()
    finally:
        browser_owner.reset(token)


# ------------------------------------------------------------------------------------------------------------ sign-in address
def test_a_sign_in_flow_is_its_own_folder_of_pages_never_words_in_the_path():
    origin = "https://id.example.com/login"
    assert under_sign_in("https://id.example.com/login/keep", origin) and not under_sign_in("https://id.example.com/home", origin)
    assert not under_sign_in("https://other.example.com/login/keep", origin) and not under_sign_in("https://id.example.com/x", "https://id.example.com/")
    probe = {"kelo_check": 1, "text": "Welcome", "text_len": 7, "url": "https://id.example.com/login/keep", "otp": 0, "secret_box": False,
             "judged_step": "none", "title": ""}
    assert still_signing_in(probe, origin) and not still_signing_in(probe)           # inside the flow's folder only
    assert not sign_in_address("https://shop.example.com/oauth/authorize") and not sign_in_address("https://shop.example.com/login")


# ------------------------------------------------------------------------------------------------------------ the old lists are gone
SRC = Path(__file__).resolve().parents[1] / "src" / "assistant"


def test_the_browser_page_field_word_lists_are_gone():
    for name in ("_EXTRA_AUTH", "extra_auth", "_BY_MAIL", "PROTECTED_WORDS", "protected_words", "APP_WORDS", "PASSKEY_WORDS", "OTP_WORDS",
                 "EXPIRED", "_VERIFY", "_SIGN_IN_PAGE", "_SIGN_IN"):
        for rel in ("tools/browser.py", "tools/keypad.py", "tools/payment_step.py", "tools/verification.py", "tools/vault_tools.py", "signin_state.py"):
            assert not re.search(rf"^{re.escape(name)}\b|^def {re.escape(name)}\(", (SRC / rel).read_text(encoding="utf-8"), re.M), (rel, name)
    # the page scripts of browser.py hold no letter of another script: no card, shipping, code-box or login word of any language
    tree = ast.parse((SRC / "tools/browser.py").read_text(encoding="utf-8"))
    scripts = {"_TAG_CARD_FIELDS", "_TAG_ADDRESS_FIELDS", "_TAG_CODE_FIELDS", "_EXTRA_AUTH_JS"}
    found = 0
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in scripts for t in node.targets):
            found += 1
            value = node.value.args[0] if isinstance(node.value, ast.Call) else node.value   # with_path(<script>) wraps a script
            text = ast.literal_eval(value)
            bad = [c for c in text if ord(c) > 127 and unicodedata.category(c).startswith("L")]
            assert bad == [], (node.targets[0].id, bad[:5])
    assert found == len(scripts)
