"""Which browser steps commit something in the user's name (and get a card) and which only look (tools/commits.py)."""

import pytest

from assistant.tools.commits import judge_click, judge_type, judge_words, selector_words


def el(text="", *, tag="button", type="", href="", words="", submits=False, search_form=False, personal=0, message=0,
       url="https://shop.example/item/1", title="Item", attrs="", name="", role="", autocomplete="", editable=False, in_form=None):
    return {"in_form": submits if in_form is None else in_form, "tag": tag, "type": type, "role": role, "text": text, "words": words, "attrs": attrs, "href": href, "editable": editable,
            "autocomplete": autocomplete, "name": name, "submits": submits, "search_form": search_form, "form_personal": personal,
            "form_message": message, "url": url, "title": title}


@pytest.mark.parametrize("label", [
    "상하이 디즈니랜드 입장권", "82,524", "예약 가능 날짜 보기", "더보기", "상세보기", "필터", "Show more", "See availability", "Next page",
    "Reviews", "Accept all cookies", "Sort by price", "주문 내역", "Airport transfer", "Booking details", "닫기", "Cancel", "취소",
])
def test_words_that_only_look(label):
    assert judge_words(label) == ""


@pytest.mark.parametrize("label,kind", [
    ("결제하기", "pay"), ("Pay now", "pay"), ("Buy now", "pay"), ("Place order", "pay"), ("Book now", "pay"), ("예약하기", "pay"),
    ("바로 구매", "pay"), ("Subscribe", "pay"), ("Reserve", "pay"), ("Confirm booking", "pay"), ("송금", "pay"), ("Donate", "pay"),
    ("Send", "send"), ("Post", "send"), ("Reply", "send"), ("댓글 등록", "send"), ("보내기", "send"), ("Publish", "send"),
    ("Delete", "delete"), ("예약 취소", "delete"), ("Cancel my subscription", "delete"), ("회원 탈퇴", "delete"), ("삭제", "delete"),
    ("신청하기", "apply"), ("Apply now", "apply"), ("응모하기", "apply"), ("RSVP", "apply"),
    ("Sign up", "account"), ("회원가입", "account"), ("Create account", "account"), ("Save changes", "account"), ("비밀번호 변경", "account"),
])
def test_words_that_commit(label, kind):
    assert judge_words(label) == kind


def test_a_long_link_is_a_result_or_a_product_card_not_a_commit():
    card = el("상하이 디즈니랜드 1일 입장권 바로 예약 ₩82,524부터", tag="a", href="/ko/activity/2128")
    assert judge_click(card, "text=82,524") == ""
    # a link to the booking page only opens it; the button there that books asks
    assert judge_click(el("예약하기", tag="a", href="/ko/booking/2128"), "text=예약하기") == ""
    assert judge_click(el("예약하기"), "text=예약하기") == "pay"


# ---- going somewhere is not a commit, whatever its words (the 체험단 report: 로그인 / 회원가입, "7,855명 신청") ----
def test_a_sign_in_or_sign_up_entry_link_never_asks():
    link = el("로그인 / 회원가입", tag="a", href="/auth/login", url="https://www.shop.example/", title="Shop")
    assert judge_click(link, "text=로그인 / 회원가입") == ""
    assert judge_click(None, "text=로그인 / 회원가입") == ""            # not readable: the label alone is an entry
    assert judge_click(el("로그인 / 회원가입"), "text=로그인 / 회원가입") == ""   # the same entry as a header button
    assert judge_click(el("회원가입", tag="a", href="/join"), "text=회원가입") == ""
    assert judge_click(el("Sign up", role="link"), "text=Sign up") == ""
    assert judge_click(el("Sign in or register", tag="a", href="/login"), "text=Sign in or register") == ""


def test_a_campaign_card_counter_never_asks():
    counter = el("7,855명 신청", tag="span", url="https://www.shop.example/campaign", title="체험단")
    assert judge_click(counter, "text=7,855명 신청") == ""
    assert judge_click(None, "text=7,855명 신청") == ""
    assert judge_click(el("7,855명 신청", tag="a", href="/campaign/123"), "text=7,855명 신청") == ""
    for label in ("1,234 reviews", "₩82,524", "(312)", "4.8 (1,024)", "12명 응모", "3,200원"):
        assert judge_click(None, f"text={label}") == "", label
    for label in ("신청하기", "1개 구매하기", "₩12,000 결제하기", "Pay $12.00", "2건 삭제"):
        assert judge_click(None, f"text={label}") != "", label


def test_the_submit_that_sends_an_application_or_a_sign_up_asks():
    apply = el("신청하기", tag="button", type="submit", submits=True, url="https://www.shop.example/campaign/123/apply")
    assert judge_click(apply, "text=신청하기") == "apply"
    assert judge_click(el("신청하기"), "text=신청하기") == "apply"            # a plain button (a page without a form element)
    assert judge_click(None, "text=신청하기") == "apply"
    join = el("회원가입", tag="button", type="submit", submits=True, personal=3, url="https://www.shop.example/join", title="회원가입")
    assert judge_click(join, "text=회원가입") == "account"
    assert judge_click(el("가입하기", tag="input", type="submit", submits=True), "input[type=submit]") == "account"


def test_payment_words_keep_asking_even_on_a_link():
    assert judge_click(el("결제하기"), "text=결제하기") == "pay"
    assert judge_click(el("결제하기", tag="a", href="/order/pay"), "text=결제하기") == "pay"
    assert judge_click(el("Buy now", tag="a", href="/buy?item=1"), "text=Buy now") == "pay"
    assert judge_click(el("결제하기", tag="button", type="submit", submits=True, url="https://shop.example/checkout"), "text=결제하기") == "pay"
    assert judge_click(None, "text=결제하기") == "pay"
    # a link inside a form on a checkout page is judged like a button
    assert judge_click(el("예약 확정", tag="a", href="/booking/confirm", in_form=True, url="https://shop.example/checkout"), "text=예약 확정") == "pay"
    # deleting on a link still asks (some sites delete with a plain link)
    assert judge_click(el("회원 탈퇴", tag="a", href="/account/leave"), "text=회원 탈퇴") == "delete"


def test_navigation_roles_are_not_commits():
    assert judge_click(el("예약", role="tab"), "text=예약") == ""
    assert judge_click(el("체험단 신청", role="menuitem"), "text=체험단 신청") == ""
    assert judge_click(el("Apply", tag="a", href="/jobs/apply"), "text=Apply") == ""


def test_plain_confirm_words_commit_only_where_they_stand():
    assert judge_click(el("확인"), "text=확인") == ""
    assert judge_click(el("OK"), "text=OK") == ""
    assert judge_click(el("확인", url="https://shop.example/checkout/pay"), "text=확인") == "pay"
    assert judge_click(el("Next", title="Payment details"), "text=Next") == "pay"
    assert judge_click(el("Save", url="https://site.example/account/settings"), "text=Save") == "account"
    assert judge_click(el("Continue", submits=True, personal=2), "text=Continue") == "form"   # sends a form with personal details
    assert judge_click(el("Continue", submits=True), "text=Continue") == ""


def test_a_search_form_submit_only_searches():
    assert judge_click(el("Submit", submits=True, search_form=True), "button[type=submit]") == ""
    assert judge_click(el("검색", submits=True, search_form=True, personal=0), "text=검색") == ""
    assert judge_click(el("Submit", submits=True, personal=1), "button[type=submit]") == "send"
    assert judge_click(el("", type="submit", submits=True, message=1), "button[type=submit]") == "form"


def test_the_host_name_does_not_make_a_checkout_page():
    assert judge_click(el("OK", url="https://booking.example/hotels"), "text=OK") == ""


def test_selector_words_when_the_element_cannot_be_read():
    assert selector_words("text=결제하기") == "결제하기"
    assert selector_words("button:has-text('Place order')") == "Place order"
    assert selector_words("[aria-label='Close']") == "Close"
    assert judge_click(None, "#place-order") == "pay"
    assert judge_click(None, "text=Next") == ""
    assert judge_click(None, "text=Next", url="https://shop.example/checkout") == "pay"
    assert judge_click(None, "a#next") == ""


@pytest.mark.parametrize("info,selector,text", [
    (el(tag="input", type="search"), "#q", "shanghai disneyland ticket"),
    (el(tag="input", name="q"), "input[name=q]", "디즈니랜드"),
    (el(tag="input", role="combobox", attrs="destination"), "#dest", "Shanghai"),
    (el(tag="input", search_form=True, attrs="checkin"), "#checkin", "2026-12-25"),
    (el(tag="input", attrs="price-min"), "#min", "50000"),
    (None, "#search", "x"), (None, "input[name=q]", "x"), (None, "#from", "ICN"),
])
def test_typing_that_only_searches(info, selector, text):
    assert judge_type(info, selector, text) == ""


@pytest.mark.parametrize("info,selector,text", [
    (el(tag="input", type="email"), "#e", "x"),
    (el(tag="input", autocomplete="tel"), "#p", "1"),
    (el(tag="input", attrs="이름"), "#n", "홍길동"),
    (el(tag="input", attrs="passport_no"), "#pp", "M123"),
    (el(tag="input", search_form=True), "#q", "me@example.com"),   # the text itself is personal
    (el(tag="input"), "#x", "010-1234-5678"),
    (el(tag="input"), "#x", "4111 1111 1111 1111"),
    (None, "input[name=email]", "x"), (None, "input[name=birth_date]", "1990-01-01"),
    (el(tag="textarea"), "textarea", "call me at 010-1234-5678"),   # a message box holding a phone number
])
def test_typing_personal_details_or_a_message_asks(info, selector, text):
    assert judge_type(info, selector, text) == "personal"


@pytest.mark.parametrize("info,selector,text", [
    (el(tag="textarea"), "textarea", "Hello there"), (el(tag="div", editable=True), "div.editor", "hi"),
    (el(tag="input", attrs="comment"), "#c", "nice"), (el(tag="textarea", attrs="review 후기"), "#r", "소스가 맛있어요"), (None, "textarea", "hi"),
])
def test_typing_a_message_or_review_sends_nothing_so_it_does_not_ask(info, selector, text):
    # its post / 등록 button is the step that asks; the privacy guard still scans the text for the owner's details
    assert judge_type(info, selector, text) == ""


def test_typing_with_enter_into_a_form_holding_details_asks():
    assert judge_type(el(tag="input", attrs="coupon"), "#coupon", "SALE10", submit=True) == ""
    assert judge_type(el(tag="input", attrs="coupon", personal=2), "#coupon", "SALE10", submit=True) == "form"
    assert judge_type(el(tag="input", attrs="coupon", personal=2), "#coupon", "SALE10", submit=False) == ""


def test_dates_are_not_phone_numbers():
    assert judge_type(el(tag="input"), "#d", "2026-12-25 ~ 2026-12-27") == ""
    assert judge_type(el(tag="input"), "#d", "20261225") == ""


def test_a_purchase_review_or_question_is_not_a_payment():
    from assistant.tools.commits import judge_words, money_words
    for label in ("구매후기작성", "구매후기 작성", "구매평 쓰기", "주문 문의", "Write a purchase review"):
        assert judge_words(label) != "pay" and not money_words(label), label
    assert judge_words("구매후기 등록") == "send"
    for label in ("구매하기", "바로구매", "구매확정", "주문하기", "Buy now"):
        assert judge_words(label) == "pay", label
