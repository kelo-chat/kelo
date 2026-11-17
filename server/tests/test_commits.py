"""Which browser steps commit something in the user's name (tools/commits.py): structure decides what is judged, the step judge says
what it does, the owner's personal values are found by structure (docs/click-judge.md). The judge is a fake here."""

import pytest

from assistant.tools.commits import click_route, is_count, judge_type, kind_of_verdict, selector_words, structure

from fake_judge import verdict


def el(text="", *, tag="button", type="", href="", words="", submits=False, search_form=False, personal=0, message=0,
       url="https://shop.example/item/1", title="Item", attrs="", name="", role="", autocomplete="", editable=False, in_form=None,
       src="", read=True, target="", secure=False):
    return {"structure_read": read, "handler_src": src, "link_target": target, "secure_form": secure,
            "in_form": submits if in_form is None else in_form, "tag": tag, "type": type, "role": role, "text": text, "words": words,
            "attrs": attrs, "href": href, "editable": editable, "autocomplete": autocomplete, "name": name, "submits": submits,
            "search_form": search_form, "form_personal": personal, "form_message": message, "url": url, "title": title}


def test_a_real_link_or_a_window_opener_only_looks_whatever_it_says():
    for label in ("Buy now", "구매하기", "購入する", "Delete account"):
        assert click_route(el(label, tag="a", href="/go")) == ""
        assert click_route(el(label, tag="a", href="/go", target="_blank")) == ""
        assert click_route(el(label, src="window.open('/x')")) == ""


def test_plain_text_is_not_pressable():
    plain = el("Send", tag="p")
    assert click_route(plain) == ""


def test_a_submit_a_request_or_an_unreadable_element_goes_to_the_judge():
    assert click_route(el("x", submits=True, type="submit")) == "judge"
    assert click_route(el("x", src="fetch('/api', {method: 'POST'})")) == "judge"
    assert click_route(None) == "judge"
    assert click_route(el("x", read=False)) == "judge"
    assert structure(el("x", submits=True)) == "changes"


def test_a_link_inside_a_sign_in_or_security_form_is_judged():
    assert click_route(el("go", tag="a", href="/next", secure=True)) == "judge"
    assert click_route(el("go", tag="a", href="/next")) == ""


def test_controls_that_only_change_the_pages_own_state_look():
    assert click_route(el("", tag="input", type="checkbox", src="x")) in ("", "judge")
    assert click_route(el("", tag="input", type="checkbox", src="")) == ""
    assert click_route(el("Tab", role="tab")) == ""
    assert click_route(el("", tag="select")) == ""


def test_counts_and_prices_are_digits_not_words():
    assert is_count("7,855") and is_count("1,234 reviews") and is_count("4.8 (1,024)") and is_count("₩82,524")
    assert not is_count("Apply!") and not is_count("")


def test_the_verdict_decides_the_kind():
    assert kind_of_verdict(None, el("x")) == "unknown"
    assert kind_of_verdict(verdict(), el("x", submits=True)) == ""
    assert kind_of_verdict(verdict("sends"), el("x")) == "send"
    assert kind_of_verdict(verdict("deletes", "sends"), el("x")) == "delete"
    assert kind_of_verdict(verdict("spends_money"), el("x")) == "pay"
    assert kind_of_verdict(verdict("applies"), el("x")) == "apply"
    assert kind_of_verdict(verdict("opts_out"), el("x")) == "optout"
    assert kind_of_verdict(verdict("signs_up"), el("x")) == "account"
    assert kind_of_verdict(verdict("account_security"), el("x")) == "security"


def test_a_form_with_the_owners_details_asks_nothing_when_the_judge_said_nothing():
    assert kind_of_verdict(verdict(), el("x", submits=True, personal=2)) == ""
    assert kind_of_verdict(verdict(), el("x", submits=True, message=1)) == ""
    assert kind_of_verdict(verdict(), el("x", submits=True, personal=2, search_form=True)) == ""
    assert kind_of_verdict(verdict("sends"), el("x", submits=True, personal=2)) == "send"


def test_selector_words_for_an_unreadable_element():
    assert selector_words("text=Pay") == "Pay"
    assert selector_words("button:has-text('Place order')") == "Place order"
    assert selector_words("[aria-label='Close']") == "Close"


@pytest.mark.parametrize("info,selector,text", [
    (el(tag="input", type="search"), "#q", "shanghai disneyland ticket"),
    (el(tag="input", role="combobox"), "#dest", "Shanghai"),
    (el(tag="input", search_form=True), "#checkin", "2026-12-25"),
    (el(tag="input"), "#min", "50000"),
    (None, "#search", "x"), (None, "#from", "ICN"),
    (el(tag="textarea"), "textarea", "Hello there"), (el(tag="div", editable=True), "div.editor", "hi"),
])
def test_typing_that_only_searches_or_writes_does_not_ask(info, selector, text):
    assert judge_type(info, selector, text) == ""


@pytest.mark.parametrize("info,selector,text", [
    (el(tag="input", type="password"), "#pw", "x"),
    (el(tag="input", autocomplete="current-password"), "#pw", "x"),
    (el(tag="input", autocomplete="new-password"), "#pw", "x"),
    (el(tag="input", autocomplete="one-time-code"), "#otp", "123456"),
    (el(tag="input"), "#x", "4111 1111 1111 1111"),
    (el(tag="input", search_form=True), "#q", "4111111111111111"),
    (None, "#x", "4111 1111 1111 1111"),
])
def test_typing_a_card_number_or_into_a_password_field_asks(info, selector, text):
    assert judge_type(info, selector, text) == "personal"


@pytest.mark.parametrize("info,selector,text", [
    (el(tag="input", type="email"), "#e", "x"),
    (el(tag="input", type="tel"), "#p", "1"),
    (el(tag="input", autocomplete="tel"), "#p", "1"),
    (el(tag="input", autocomplete="given-name"), "#n", "Kim"),
    (el(tag="input", autocomplete="street-address"), "#a", "1 Main St"),
    (el(tag="input", search_form=True), "#q", "me@example.com"),
    (el(tag="input"), "#x", "010-1234-5678"),
    (None, "#x", "me@example.com"),
    (el(tag="textarea"), "textarea", "call me at 010-1234-5678"),
])
def test_typing_an_email_a_phone_a_name_or_an_address_asks_nothing(info, selector, text):
    assert judge_type(info, selector, text) == ""


def test_a_field_named_after_a_detail_is_not_personal_by_its_name():
    assert judge_type(el(tag="input", attrs="phone email 이름"), "#n", "홍길동") == ""


def test_typing_with_enter_into_a_form_holding_details_asks_nothing_by_itself():
    assert judge_type(el(tag="input"), "#c", "SALE10", submit=True) == ""
    assert judge_type(el(tag="input", personal=2), "#c", "SALE10", submit=True) == ""
    assert judge_type(el(tag="input", personal=2), "#c", "SALE10", submit=False) == ""
    assert judge_type(el(tag="input", personal=2, search_form=True), "#c", "x", submit=True) == ""


def test_dates_are_not_phone_numbers():
    assert judge_type(el(tag="input"), "#d", "2026-12-25 ~ 2026-12-27") == ""
    assert judge_type(el(tag="input"), "#d", "20261225") == ""
