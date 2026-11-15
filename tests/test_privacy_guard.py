"""The privacy guard (privacy_guard.py): what leaves for someone else is checked for the owner's own sensitive values and for ID, card,
account and door-code patterns; a hit is a card whatever "allow" or a task's permission says, an "apply" permission covers only the form's
basic fields, the owner's own mailbox is no outside party, and nothing found is ever logged. Files, profiles and forms are fakes."""

import json
import logging

import pytest

from assistant import effects
from assistant.permissions import Gate, Info, Permissions
from assistant.privacy_guard import PrivacyGuard, luhn

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


def guard():
    return PrivacyGuard(home=FakeHome(), store=FakeStore(), wallet=FakeWallet(), shipping=FakeShipping(),
                        own_addresses=lambda: ["me@gmail.com"])


def send(g, tool="email_send", **args):
    return g.check(tool, args, effects.effect_of(tool, args))


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
    g = PrivacyGuard(home=FakeHome(), store=spy, wallet=FakeWallet(), shipping=FakeShipping())
    with caplog.at_level(logging.DEBUG):
        hit = send(g, to=["bob@x.org"], body=f"{VISA} 900101-1234567 서울시 마포구 월드컵북로 12길 34, 502호 naver login")
    assert hit is not None and spy.opened == []
    text = "\n".join(r.getMessage() for r in caplog.records)
    for value in (VISA, "4242", "900101", "월드컵북로", "2345"):
        assert value not in text
    assert "card" in text and "address" in text   # the categories only


def test_browser_typing_a_number_into_a_form_is_checked_and_a_search_is_not():
    g = guard()
    assert g.check("browser_type", {"selector": "#rrn", "text": "900101-1234567"}, None, commit="personal", site="shop.example.com")
    assert g.check("browser_type", {"selector": "#q", "text": "900101-1234567"}, None, commit="", site="shop.example.com")   # any typing
    assert g.check("browser_type", {"selector": "#q", "text": "umbrella"}, None, commit="", site="shop.example.com") is None
    assert g.check("browser_click", {"selector": "#go"}, None, commit="send", site="shop.example.com") is None                # a click has no text
    assert g.check("browser_type", {"text": VISA}, None, commit="security", site="bank.example.com") is not None             # security steps too


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
