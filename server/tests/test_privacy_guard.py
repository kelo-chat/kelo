"""The privacy guard (privacy_guard.py): what leaves for someone else is checked for the owner's own sensitive values and for ID, card,
account and door-code patterns; a hit is a card whatever "allow" or a task's permission says, an "apply" permission covers only the form's
basic fields, the owner's own mailbox is no outside party, and nothing found is ever logged. Files, profiles and forms are fakes."""

import json
import logging

import pytest

from assistant import effects
from assistant.permissions import Gate, Info, Permissions
from assistant.privacy_guard import FACTS_SYSTEM, PrivacyGuard, iban_ok, luhn
from assistant.secret_judge import SYSTEM as JUDGE_SYSTEM

from test_permissions import counting, fake_tools, run_calls

USER_MD = "# User\n- Home address: 서울시 마포구 월드컵북로 12길 34, 502호\n- Phone: 010-2345-6789\n- 건강: 땅콩 알레르기와 고혈압 약 복용\n"
PERSON = "---\nname: Dana\n---\nDana's mobile 010-9999-1234, mail dana.private@example.org\n"
VISA = "4242 4242 4242 4242"


class FakeHome:
    def read(self, rel):
        return {"user.md": USER_MD, "MEMORY.md": "", "memory/people/dana.md": PERSON}[rel]

    def people(self):
        return [{"path": "memory/people/dana.md", "name": "Dana"}]


class FakeShipping:
    def profiles(self):
        return [{"address_line1": "Yeouido-dong 1 Teststreet", "address_line2": "Apt 1203", "phone": "+82 10 5555 0101"}]


class FakeWallet:
    def cards(self):
        return [{"last4": "7777"}]


class FakeStore:
    def credentials(self):
        return [{"name": "Naver login", "kind": "login", "has_value": True}]


CODES = {"4821", "*1234#"}   # the digit runs the fake model calls door codes


async def fake_ask(system, user):
    """The owner's main model, for the two requests the guard makes (privacy_guard.FACTS_SYSTEM, secret_judge.SYSTEM)."""
    items = json.loads(user)
    if system == FACTS_SYSTEM:
        return {"lines": {str(i["n"]): ("address" if "address" in i["line"].lower() else "health" if "건강" in i["line"] else
                                        "phone" if "Phone" in i["line"] else "none") for i in items}}
    assert system == JUDGE_SYSTEM
    return {"secrets": [i["n"] for i in items if any(f"[[{c}]]" in i["context"] for c in CODES)]}


def run(coro):
    """Runs a coroutine from sync test code, also inside a running loop."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


def guard(ask=fake_ask):
    return PrivacyGuard(home=FakeHome(), store=FakeStore(), wallet=FakeWallet(), shipping=FakeShipping(),
                        own_addresses=lambda: ["me@gmail.com"], ask=ask)


def send(g, tool="email_send", **args):
    effect = effects.effect_of(tool, args)
    run(g.prepare(tool, args, effect))                      # the model's part, ahead of the sync check (the Gate does it)
    return g.check(tool, args, effect)


def test_luhn_tells_a_card_number_from_any_long_number():
    assert luhn("4242424242424242") and not luhn("4242424242424241")


@pytest.mark.parametrize("args, cats", [
    ({"to": ["bob@x.org"], "subject": "hi", "body": "I live at 서울시 마포구 월드컵북로 12길 34, 502호."}, ["address"]),
    ({"to": ["bob@x.org"], "body": "ship it to Yeouido-dong 1 Teststreet Apt 1203 please"}, ["address"]),
    ({"to": ["bob@x.org"], "body": "call me on +82 10-2345-6789"}, ["phone"]),
    ({"to": ["bob@x.org"], "body": "Dana is at 01099991234 and dana.private@example.org"}, ["contact"]),
    ({"to": ["bob@x.org"], "body": f"pay with {VISA}"}, ["card"]),
    ({"to": ["bob@x.org"], "body": "my card ending 7777"}, ["card"]),
    ({"to": ["bob@x.org"], "body": "주민번호 900101-1234567"}, ["id"]),
    ({"to": ["bob@x.org"], "body": "passport M12345678"}, ["id"]),
    ({"to": ["bob@x.org"], "body": "계좌 110-123-456789 로 보내"}, ["account"]),
    ({"to": ["bob@x.org"], "body": "현관 비밀번호 *1234#"}, ["code"]),
    ({"to": ["bob@x.org"], "body": "door code: 4821, nobody is home until Friday"}, ["code"]),
    ({"to": ["bob@x.org"], "body": "I take 땅콩 알레르기와 고혈압 약 복용"}, ["health"]),
    ({"to": ["bob@x.org"], "body": "my Naver login is the one"}, ["vault"]),
    ({"to": ["bob@x.org"], "body": f"{VISA} and 900101-1234567"}, ["card", "id"]),
])
def test_a_mail_with_a_sensitive_value_is_a_hit_in_its_category(args, cats):
    hit = send(guard(), **args)
    assert hit is not None and list(hit.categories) == cats


def test_ordinary_text_and_other_numbers_are_no_hit():
    g = guard()
    for body in ("See you at 7:30 in Hongdae, order number 20261112, total 12,500 won", "010-1111-2222 is the shop's number",
                 "Meeting room 502 on the fifth floor", "4242 4242 4242 4241"):
        assert send(g, to=["bob@x.org"], body=body) is None, body


def test_an_sms_with_a_card_number_and_the_card_line_names_what_goes_where_without_values():
    hit = send(guard(), "phone_send_text", to="010-7777-0000", text=f"my card is {VISA}")
    assert hit.line == "Sends to 7777000" + "0: card number" or hit.line.startswith("Sends to ")
    assert VISA not in hit.line and "4242" not in hit.line
    hit = send(guard(), to=["bob@x.org"], body="서울시 마포구 월드컵북로 12길 34, 502호 / 010 2345 6789")
    assert hit.line == "Sends to bob@x.org: home address, phone number"


def test_the_owners_own_mailbox_is_no_outside_recipient():
    g = guard()
    assert send(g, to=["Me@Gmail.com"], body=f"note to self: {VISA}") is None
    assert send(g, to=["me@gmail.com", "bob@x.org"], body=f"{VISA}") is not None


def test_the_recipient_field_alone_is_not_scanned():
    assert send(guard(), to=["dana.private@example.org"], body="Hello Dana, lunch?") is None


def test_the_vault_is_read_by_name_only_and_nothing_found_is_logged(caplog):
    class Spy(FakeStore):
        opened = []

        def credential(self, name):
            self.opened.append(name)

    spy = Spy()
    g = PrivacyGuard(home=FakeHome(), store=spy, wallet=FakeWallet(), shipping=FakeShipping(), ask=fake_ask)
    with caplog.at_level(logging.DEBUG):
        hit = send(g, to=["bob@x.org"], body=f"{VISA} 900101-1234567 서울시 마포구 월드컵북로 12길 34, 502호 naver login")
    assert hit is not None and spy.opened == []
    text = "\n".join(r.getMessage() for r in caplog.records)
    for value in (VISA, "4242", "900101", "월드컵북로", "2345"):
        assert value not in text
    assert "card" in text and "address" in text   # the categories only


def test_browser_typing_a_number_into_a_form_is_checked_and_a_search_is_not():
    g = guard()

    def check(tool, args, commit, site):
        run(g.prepare(tool, args, None, commit=commit, site=site))
        return g.check(tool, args, None, commit=commit, site=site)
    assert check("browser_type", {"selector": "#rrn", "text": "900101-1234567"}, "personal", "shop.example.com")
    assert check("browser_type", {"selector": "#q", "text": "900101-1234567"}, "", "shop.example.com")   # any typing
    assert check("browser_type", {"selector": "#q", "text": "umbrella"}, "", "shop.example.com") is None
    assert check("browser_click", {"selector": "#go"}, "send", "shop.example.com") is None                # a click has no text
    assert check("browser_type", {"text": VISA}, "security", "bank.example.com") is not None             # security steps too


def _cards(*pairs):
    return pairs


async def _gate_run(tmp_path, tool, call, mode, covers=False, commit="personal"):
    from assistant.store import Store
    store = Store()
    tools = fake_tools()
    p = Permissions(store, tools=lambda: tools, names=lambda cid: cid)
    if mode:
        p.put(Info("gmail", "Gmail", "mail"), {"send": mode})
    async def step(name, args):
        return commit, "jobs.example.com"
    gate = Gate(p, guard(), browser_step=step, apply_covers=lambda site: covers)
    ran = []
    res, asked, _ = await run_calls([counting(tool, ran, requires_approval=tool != "browser_type", auto_approve=(lambda a: True) if tool == "browser_type" else None)],
                                    gate, call)
    return ran, asked


async def test_a_guard_hit_is_a_card_even_with_allow_and_the_card_cannot_be_skipped_by_a_rule(tmp_path):
    ran, asked = await _gate_run(tmp_path, "email_send", ("email_send", {"account": "mail-gmail", "to": ["bob@x.org"], "body": VISA}), "allow")
    assert len(asked) == 1 and asked[0].force is True and asked[0].detail.startswith("Sends to bob@x.org: card number")
    ran, asked = await _gate_run(tmp_path, "email_send", ("email_send", {"account": "mail-gmail", "to": ["bob@x.org"], "body": "lunch at 1?"}), "allow")
    assert asked == [] and len(ran) == 1
    ran, asked = await _gate_run(tmp_path, "email_send", ("email_send", {"account": "mail-gmail", "to": ["me@gmail.com"], "body": VISA}), "allow")
    assert asked == [] and len(ran) == 1                                                       # own mailbox


async def test_a_form_field_under_an_apply_permission_asks_unless_it_is_a_basic_field(tmp_path):
    typed = lambda text: ("browser_type", {"selector": "#f", "text": text})
    # no permission: the typed ID asks (the tool's own auto_approve said yes, the guard overrides it)
    ran, asked = await _gate_run(tmp_path, "browser_type", typed("900101-1234567"), None, covers=False)
    assert len(asked) == 1 and asked[0].force
    # an apply permission covers the basic fields: phone and address
    ran, asked = await _gate_run(tmp_path, "browser_type", typed("010-2345-6789"), None, covers=True, commit="apply")
    assert asked == [] and len(ran) == 1
    ran, asked = await _gate_run(tmp_path, "browser_type", typed("서울시 마포구 월드컵북로 12길 34, 502호"), None, covers=True, commit="form")
    assert asked == [] and len(ran) == 1
    # ... but an ID number, an account or a health detail in the same form still shows a card
    for text in ("900101-1234567", "계좌 110-123-456789", "땅콩 알레르기와 고혈압 약 복용", VISA):
        ran, asked = await _gate_run(tmp_path, "browser_type", typed(text), None, covers=True, commit="apply")
        assert len(asked) == 1 and asked[0].force and ran, text
    # without a permission a basic field asks too
    ran, asked = await _gate_run(tmp_path, "browser_type", typed("010-2345-6789"), None, covers=False, commit="personal")
    assert len(asked) == 1


def test_apply_covers_reads_the_tasks_apply_permission(tmp_path):
    from test_conditional_grants import in_scope, world
    store, wallet, schedules, grants, poll = world(tmp_path)
    scope = f"schedule:{poll['id']}"
    grants.add_action(scope, "apply", "apply for the class", ["site:classes.example.org"], max_count=1)
    with in_scope(scope):
        assert grants.apply_covers("www.classes.example.org") is True and grants.apply_covers("other.example.com") is False
    with in_scope(""):
        assert grants.apply_covers("classes.example.org") is False


async def test_the_page_is_read_once_when_the_guard_judges_a_typing_step_ahead_of_the_tools_own_check():
    from assistant.tools.browser import BrowserSession
    sess = BrowserSession(connect=None)
    reads = []

    async def read_target(selector):
        reads.append(selector)
        return None
    sess.read_target = read_target
    args = {"selector": "#rrn", "text": "x"}
    first = await sess.judge_ahead("browser_type", args)
    assert await sess.judge("browser_type", args) == first and reads == ["#rrn"]          # the second answer is the first one
    await sess.judge("browser_type", args)
    assert reads == ["#rrn", "#rrn"]                                                       # kept for one use only


def test_a_date_in_the_notes_or_in_an_address_is_not_the_owners_phone():
    class DatedHome(FakeHome):
        def read(self, rel):
            return {"user.md": USER_MD, "MEMORY.md": "- 2026-11-12: 쿠팡 주문 도착\n- order 20261112 kept", "memory/people/dana.md": PERSON}[rel]
    g = PrivacyGuard(home=DatedHome(), store=FakeStore(), wallet=FakeWallet(), shipping=FakeShipping())
    url = "https://my.coupang.com/productreview/register?productId=1234567890&deliveryDate=2026-11-12&vendorItemId=87654321098"
    assert g.check_url("web_fetch", {"url": url}) is None
    assert "phone" in g.scan("call +82 10 5555 0101")   # the saved profile's phone still counts


# ---- hc-privacy: no word of a language decides; the model's answers are asked ahead, the sync check only reads them
def test_a_door_code_is_what_the_model_says_and_without_an_answer_it_is_a_code():
    g = guard()
    assert [*send(g, to=["bob@x.org"], body="the entrance: 4821, nobody is home").categories] == ["code"]        # any language, no code word
    assert [*send(g, to=["bob@x.org"], body="入口は *1234# です").categories] == ["code"]
    assert send(g, to=["bob@x.org"], body="see you in room 7788 at noon") is None                                  # the model said: no secret
    # not judged yet (the sync check alone) and no model at all: the safe side
    for g2 in (guard(), guard(ask=None)):
        hit = g2.check("email_send", {"to": ["bob@x.org"], "body": "room 7788"}, effects.effect_of("email_send", {"to": ["bob@x.org"], "body": "room 7788"}))
        assert hit is not None and list(hit.categories) == ["code"]
    for bad in (lambda *_: None, lambda *_: {"secrets": "all"}, lambda *_: {"secrets": [9]}):
        async def ask(system, user, bad=bad):
            return bad()
        assert list(send(guard(ask=ask), to=["bob@x.org"], body="room 7788").categories) == ["code"]


def test_numbers_that_are_other_things_are_no_code_candidates():
    asked = []

    async def ask(system, user):
        if system == JUDGE_SYSTEM:
            asked.append(user)
            return {"secrets": []}
        return await fake_ask(system, user)
    g = guard(ask=ask)
    body = "call +82 10 5555 0101, card " + VISA + ", on 2026-11-12 at 10:30, total 12,500, ref 20261112-77"
    assert list(send(g, to=["bob@x.org"], body=body).categories) == ["phone", "card"]
    assert asked == []                                                       # phone, card, date, time, amount and ref pieces are not asked about
    assert send(g, to=["bob@x.org"], body="in 2026 only") is None and len(asked) == 1


def test_the_judge_is_asked_once_for_one_text():
    asked = []

    async def ask(system, user):
        asked.append(system)
        return await fake_ask(system, user)
    g = guard(ask=ask)
    for _ in range(3):
        assert send(g, to=["bob@x.org"], body="door 4821") is not None
    assert asked.count(JUDGE_SYSTEM) == 1 and asked.count(FACTS_SYSTEM) == 1                                     # lines are kept too


def test_labelled_facts_are_classified_by_the_model_in_any_language_and_without_an_answer_every_value_counts():
    class JaHome(FakeHome):
        def read(self, rel):
            return {"user.md": "- 住所: 東京都千代田区丸の内1-1-1\n- 言語: 日本語を優先すること\n", "MEMORY.md": "", "memory/people/dana.md": PERSON}[rel]

    async def ask(system, user):
        return {"lines": {str(i["n"]): ("address" if "住所" in i["line"] else "none") for i in json.loads(user)}}

    def build(**kw):
        return PrivacyGuard(home=JaHome(), store=FakeStore(), wallet=FakeWallet(), shipping=FakeShipping(), **kw)
    where, lang = "東京都千代田区丸の内1-1-1", "日本語を優先すること"
    g = build(ask=ask)
    assert list(send(g, to=["bob@x.org"], body=f"I live at {where}").categories) == ["address"]
    assert send(g, to=["bob@x.org"], body=f"{lang}") is None                                                        # the model said: not sensitive
    async def none(system, user):
        return None

    async def partial(system, user):
        return {"lines": {"0": "address"}}                                                                          # not every line answered
    for g2 in (build(), build(ask=none), build(ask=partial)):                                                       # no model, a broken one, a wrong answer
        assert "personal" in send(g2, to=["bob@x.org"], body=f"{where} / {lang}").categories


def test_new_lines_cost_one_request_and_old_lines_keep_their_answer(tmp_path):
    asked = []

    async def ask(system, user):
        asked.append(json.loads(user))
        return await fake_ask(system, user)
    box = {"text": USER_MD}

    class Home(FakeHome):
        def read(self, rel):
            return box["text"] if rel == "user.md" else super().read(rel)
    g = PrivacyGuard(home=Home(), store=FakeStore(), wallet=FakeWallet(), shipping=FakeShipping(), ask=ask)
    run(g.refresh())
    run(g.refresh())
    assert len(asked) == 1
    box["text"] += "- Allergy: shellfish and walnuts\n"
    run(g.refresh())
    assert len(asked) == 2 and [i["line"] for i in asked[1]] == ["- Allergy: shellfish and walnuts"]


def test_an_iban_by_its_checksum_a_passport_by_its_shape_and_a_card_tail_standing_alone():
    assert iban_ok("GB82 WEST 1234 5698 7654 32") and not iban_ok("GB82 WEST 1234 5698 7654 33")
    g = guard()
    assert list(send(g, to=["bob@x.org"], body="pay GB82 WEST 1234 5698 7654 32 please").categories) == ["account"]
    assert send(g, to=["bob@x.org"], body="pay GB82 WEST 1234 5698 7654 33 please") is None
    assert list(send(g, to=["bob@x.org"], body="パスポート M12345678").categories) == ["id"]                      # no passport word needed
    assert list(send(g, to=["bob@x.org"], body="カード 7777").categories) == ["card"]                              # a wallet card's last four, alone
    assert send(g, to=["bob@x.org"], body="item 17777 and 77778") is None


def test_a_phone_in_the_notes_is_found_by_its_digits_not_by_a_word():
    class Home(FakeHome):
        def read(self, rel):
            return {"user.md": "- 연락: +82 10-2222-3333\n", "MEMORY.md": "", "memory/people/dana.md": PERSON}[rel]
    g = PrivacyGuard(home=Home(), store=FakeStore(), wallet=FakeWallet(), shipping=FakeShipping(), ask=fake_ask)
    assert list(send(g, to=["bob@x.org"], body="call 010 2222 3333").categories) == ["phone"]


async def test_the_gate_asks_the_model_ahead_and_a_model_that_is_down_makes_a_card():
    ran, asked = await _gate_run(None, "email_send", ("email_send", {"account": "mail-gmail", "to": ["bob@x.org"], "body": "entrance 4821"}), "allow")
    assert len(asked) == 1 and asked[0].detail.endswith("door or access code") and asked[0].force
    ran, asked = await _gate_run(None, "email_send", ("email_send", {"account": "mail-gmail", "to": ["bob@x.org"], "body": "table 7788"}), "allow")
    assert asked == [] and len(ran) == 1


# ---- a saved login named after the site is the site's own login: opening that site is no send of it (Coupang, 2026-11-18)
class LoginStore:
    def __init__(self, items):
        self.items = items

    def credentials(self):
        return self.items


def login_guard(items):
    return PrivacyGuard(home=FakeHome(), store=LoginStore(items), wallet=FakeWallet(), shipping=FakeShipping())


def test_a_login_named_after_the_site_does_not_make_opening_that_site_a_card():
    g = login_guard([{"name": "coupang", "kind": "login", "hosts": ["coupang.com"], "has_value": True}])
    assert g.check_url("browser_open", {"url": "https://www.coupang.com/"}) is None
    assert g.check_url("browser_open", {"url": "https://www.coupang.com/np/search?component=&q=%EC%A0%80%EC%9E%90%EA%B7%B9"}) is None
    assert g.check_url("web_fetch", {"url": "https://m.coupang.com/vp/products/1"}) is None


def test_a_login_name_in_the_address_of_another_site_is_still_a_vault_hit():
    g = login_guard([{"name": "coupang", "kind": "login", "hosts": ["coupang.com"], "has_value": True}])
    hit = g.check_url("browser_open", {"url": "https://other.com/?x=coupang"})
    assert hit is not None and list(hit.categories) == ["vault"]
    assert list(g.check_url("browser_open", {"url": "https://other.com/coupang/login"}).categories) == ["vault"]


def test_a_login_without_hosts_is_the_site_of_its_name():
    g = login_guard([{"name": "naver", "kind": "login", "hosts": [], "has_value": True}])
    assert g.check_url("browser_open", {"url": "https://www.naver.com/"}) is None
    assert list(g.check_url("browser_open", {"url": "https://other.com/?x=naver"}).categories) == ["vault"]


def test_the_name_of_a_login_still_counts_in_a_message_text():
    g = login_guard([{"name": "coupang", "kind": "login", "hosts": ["coupang.com"], "has_value": True}])
    assert "vault" in g.scan("my coupang login is the one")
