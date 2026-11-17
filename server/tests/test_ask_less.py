"""Ask less (run ask-less): typing personal details and sending an ordinary form show no card; the step judge's flags and two typing
cases tied to money or sign-in still do (docs/browser.md "What asks for approval", docs/click-judge.md). The reference
agent's rule: "an ordinary browser form submit is currently allowed by default". Ask on what the step DOES, never on what is typed. The judge is a fake; the browser tests run real Chromium on local pages (skipped without it)."""

from __future__ import annotations

import pytest

from assistant.tools.commits import judge_type, kind_of_verdict

from fake_judge import FakeJudge, verdict
from test_commits import el
from test_grant_only_commits import PAGES, TITLES, asks, ctx, setup, turn  # noqa: F401  (ctx, turn: fixtures)

SITE = "https://forms.example.com"
CARD = "4111 1111 1111 1111"      # a test card number (Luhn-valid)
PAGES.update({
    f"{SITE}/contact": "<form action=/x method=post><input id=mail type=email autocomplete=email value='me@example.com'>"
                       "<input id=nm autocomplete=name value='Kim'><textarea id=msg>Hello</textarea>"
                       "<button id=go type=submit>Next</button></form>",
    f"{SITE}/typing": "<form action=/x method=post><input id=mail autocomplete=email><input id=tel autocomplete=tel>"
                      "<input id=nm autocomplete=name><input id=plain><input id=pw type=password>"
                      "<input id=otp autocomplete=one-time-code></form>",
})
TITLES.setdefault(SITE, "Forms")


# ---- a. typing e-mail, phone and name asks nothing
@pytest.mark.parametrize("info,text", [
    (el(tag="input", autocomplete="email"), "me@example.com"),
    (el(tag="input", autocomplete="tel"), "010-1234-5678"),
    (el(tag="input", autocomplete="name"), "Kim Minsu"),
    (el(tag="input", type="email"), "me@example.com"),
    (el(tag="input", type="tel"), "+82 10 1234 5678"),
    (el(tag="input", autocomplete="street-address"), "1 Main St"),
    (el(tag="input"), "me@example.com"),
    (el(tag="input"), "010-1234-5678"),
    (el(tag="textarea"), "call me at 010-1234-5678 or me@example.com"),
    (None, "me@example.com"),
])
def test_typing_an_email_a_phone_or_a_name_shows_no_card(info, text):
    assert judge_type(info, "#f", text) == ""
    assert judge_type(info, "#f", text, submit=True) == ""


# ---- b. an ordinary form submit with no judge flag shows no card
@pytest.mark.parametrize("info", [
    el("Next", submits=True, personal=2, message=1),
    el("Next", submits=True, personal=0, message=1),
    el("Next", submits=True, personal=3),
])
def test_a_form_with_details_and_a_message_and_no_judge_flag_shows_no_card(info):
    assert kind_of_verdict(verdict(), info) == ""


# ---- c. the same form, judged as sending: the "send" card
def test_the_same_form_judged_sends_still_asks():
    info = el("Next", submits=True, personal=2, message=1)
    assert kind_of_verdict(verdict("sends"), info) == "send"
    assert kind_of_verdict(verdict("spends_money"), info) == "pay"
    assert kind_of_verdict(verdict("applies"), info) == "apply"


# ---- d. a card number by value and a password field still ask
def test_a_card_number_by_value_asks():
    assert judge_type(el(tag="input"), "#x", CARD) == "personal"
    assert judge_type(el(tag="input", search_form=True), "#q", "4111111111111111") == "personal"
    assert judge_type(None, "#x", CARD) == "personal"
    assert judge_type(el(tag="input"), "#x", "4111 1111 1111 1112") == ""      # fails Luhn: just digits


@pytest.mark.parametrize("info", [el(tag="input", type="password"), el(tag="input", autocomplete="current-password"),
                                  el(tag="input", autocomplete="new-password"), el(tag="input", autocomplete="one-time-code")])
def test_typing_into_a_password_field_asks(info):
    assert judge_type(info, "#pw", "hunter2") == "personal"


# ---- e. the judge being unavailable asks
def test_no_verdict_asks():
    assert kind_of_verdict(None, el("Next", submits=True)) == "unknown"
    assert kind_of_verdict(None, el("Next", submits=True, personal=2)) == "unknown"


# ---- the same through the browser session (real page, fake judge)
async def test_typing_personal_details_and_sending_an_ordinary_form_in_the_browser_ask_nothing(ctx, turn):
    s, _p, click = await setup(ctx)
    s.step_judge = FakeJudge(())
    await s.open(f"{SITE}/contact")
    assert await s.judge("browser_type", {"selector": "#mail", "text": "me@example.com"}) == ""
    assert await s.judge("browser_type", {"selector": "#nm", "text": "Kim"}) == ""
    assert not await asks(click, "#go")                                           # the form submit: no flag, no card
    s.step_judge = FakeJudge(("sends",))
    s._kinds_seen.clear()
    assert await s.judge("browser_click", {"selector": "#go"}) == "send"           # the judge says it sends: the card
    s.step_judge = FakeJudge(None)
    s._kinds_seen.clear()
    assert await s.judge("browser_click", {"selector": "#go"}) == "unknown" and await asks(click, "#go")   # judge down: asks


async def test_a_card_number_and_a_password_field_still_ask_in_the_browser(ctx, turn):
    s, _p, _click = await setup(ctx)
    s.step_judge = FakeJudge(())
    await s.open(f"{SITE}/typing")
    for sel in ("#mail", "#tel", "#nm", "#plain"):
        assert await s.judge("browser_type", {"selector": sel, "text": "Kim"}) == "", sel
    assert await s.judge("browser_type", {"selector": "#plain", "text": CARD}) == "personal"
    assert await s.judge("browser_type", {"selector": "#pw", "text": "x"}) == "personal"
    assert await s.judge("browser_type", {"selector": "#otp", "text": "123456"}) == "personal"
