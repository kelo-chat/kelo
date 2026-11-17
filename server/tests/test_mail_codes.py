"""A sign-in code from the owner's mailbox, typed without asking: only during a sign-in the agent itself started in this task, on that
site; only mail that arrived around the time the code was asked for (a minute before, up to 10 minutes after); only from the site's own
sender (its domain in From, a DKIM signature or the Return-Path); only one clear code; only into that site's page; never logged or kept;
and only while the owner's setting "Use codes from my mail automatically" is on. Anything else falls back to asking the owner.
Fake mailbox, fake pages."""

import logging
import re
import time
from email.message import EmailMessage
from email.utils import formatdate

import httpx
import pytest
from cryptography.fernet import Fernet

from assistant.server import create_app
from assistant.store import Store
from assistant.tools.browser_more import BrowserExtras
from assistant.tools.login_code import AUTO_EARLY_SECONDS, candidate_codes, pick_auto_code, sender_match
from assistant.tools.vault_tools import vault_tools
from assistant.vault import Transient

from test_browser_more import FakeMail, FormPage, run_code_tool, session_for
from test_server import AUTH, Running
from test_agent_profile import app_settings
from test_vault import PASSWORD, USER, Page
from test_vault import session_for as vault_session

KEY = Fernet.generate_key().decode()
CODE = "482913"
NOW = 1_800_000_000.0


class FakeJudge:
    """Stands for the main model (code_judge.CodeJudge): a marked token is a sign-in code when the words before it say "code"."""

    def __init__(self, fail=False):
        self.fail, self.asked = fail, 0

    async def codes(self, messages, count, key=""):
        self.asked += 1
        if self.fail:
            return None
        out = set()
        for m in messages:
            for hit in re.finditer(r"([^\[]{0,40})\[\[#(\d+): ", m["text"]):
                if "code" in hit.group(1).lower():
                    out.add(int(hit.group(2)))
        return out


JUDGE = FakeJudge()


def mail(sender="Example <no-reply@example.com>", text=f"Your verification code is {CODE}.", at=NOW, **kw):
    return {"from": sender, "subject": "Sign-in code", "text": text, "at": at, "arrived": at, "dkim": [], "return_path": "", **kw}


# ------------------------------------------------------------------------------------------------ the strict match itself
@pytest.mark.parametrize("arrived,ok", [(NOW - AUTO_EARLY_SECONDS - 5, False), (NOW - 30, True), (NOW + 60, True), (NOW + 590, True),
                                        (NOW + 700, False)])
async def test_only_mail_that_arrived_around_the_request_counts(arrived, ok):
    found, why = await pick_auto_code([mail(at=arrived)], "www.example.com", NOW, now=max(NOW + 1, min(arrived, NOW + 599)), judge=JUDGE)
    assert (found is not None) == ok and (found.code == CODE if ok else why == "none")


async def test_nothing_is_typed_without_asking_once_ten_minutes_have_passed():
    assert await pick_auto_code([mail()], "example.com", NOW, now=NOW + 601, judge=JUDGE) == (None, "late")


async def test_the_sender_must_be_the_site_and_the_header_that_showed_it_is_recorded():
    assert sender_match(mail(), "login.example.com") == "From example.com"
    assert sender_match(mail(sender="Accounts <a@accounts.example.co.kr>"), "www.example.co.kr") == "From accounts.example.co.kr"
    sent_by_a_service = mail(sender="Example <noreply@mailer.example-mail.net>", dkim=["example.com"])
    assert sender_match(sent_by_a_service, "example.com") == "DKIM example.com"
    bounced = mail(sender="Example <noreply@mailer.example-mail.net>", return_path="bounce+x@send.example.com")
    assert sender_match(bounced, "example.com") == "Return-Path send.example.com"
    assert sender_match(mail(sender="Example Support <help@examp1e.com>"), "example.com") is None          # a look-alike
    assert sender_match(mail(sender="example.com <x@other.net>"), "example.com") is None                   # the name is not the sender
    assert sender_match(mail(from_failed=True), "example.com") is None                                     # From failed DMARC
    assert sender_match(mail(), "github.com") is None
    found, _ = await pick_auto_code([sent_by_a_service], "example.com", NOW, NOW + 5, JUDGE)
    assert found.matched == "DKIM example.com" and found.sender == "noreply@mailer.example-mail.net"


async def test_mail_of_another_site_is_never_used():
    assert await pick_auto_code([mail(sender="Shop <no-reply@othershop.com>")], "example.com", NOW, NOW + 5, JUDGE) == (None, "none")


@pytest.mark.parametrize("msgs", [
    [mail(text="Your code is 111222"), mail(text="Your code is 333444", at=NOW + 30)],      # two mails, two codes (a resend)
    [mail(text="Verification code: 111222\nBackup code: 333444")],                         # one mail, two equally likely codes
])
async def test_an_unclear_match_is_left_to_the_owner(msgs):
    assert await pick_auto_code(msgs, "example.com", NOW, NOW + 60, JUDGE) == (None, "ambiguous")


async def test_the_same_code_mailed_twice_is_still_clear_and_codes_need_a_clear_place():
    found, _ = await pick_auto_code([mail(), mail(at=NOW + 20)], "example.com", NOW, NOW + 60, JUDGE)
    assert found.code == CODE


async def test_the_judge_decides_what_is_a_code_and_without_it_nothing_is_typed_unasked():
    order = mail(text="Order 123456 shipped. Thanks for your order.", subject="Shipping")
    assert await pick_auto_code([order], "example.com", NOW, NOW + 5, JUDGE) == (None, "none")           # shaped like a code, judged not to be
    assert await pick_auto_code([mail()], "example.com", NOW, NOW + 5, FakeJudge(fail=True)) == (None, "none")   # judge cannot tell: fail closed
    assert await pick_auto_code([mail()], "example.com", NOW, NOW + 5) == (None, "none")                  # no judge at all
    other = mail(sender="Shop <no-reply@othershop.com>")
    judge = FakeJudge()
    await pick_auto_code([other], "example.com", NOW, NOW + 5, judge)
    assert judge.asked == 0                                                                                # sender and time first: no request for strangers
    for said, want in (("Ihr Bestätigungscode lautet 552901", ["552901"]), ("Ваш код: 552901", ["552901"]), ("인증번호 [552901]", ["552901"]),
                       ("Order 123456 and 77 items", ["123456"])):
        assert candidate_codes(said) == want                                                               # candidates by shape only


def test_mail_headers_give_dkim_and_return_path_only_when_the_receiving_server_did_not_report_them_failing():
    from assistant.mail import _sender_headers

    m = EmailMessage()
    m["Authentication-Results"] = "mx.google.com; dkim=pass header.i=@example.com header.s=s1; spf=pass smtp.mailfrom=send.example.com; dmarc=pass"
    m["Authentication-Results"] = "fake.example; dkim=pass header.d=evil.example"            # further down: came with the message
    m["DKIM-Signature"] = "v=1; a=rsa-sha256; d=example.com; s=s1; b=x"
    m["DKIM-Signature"] = "v=1; a=rsa-sha256; d=evil.example; s=s1; b=x"
    m["Return-Path"] = "<bounce@send.example.com>"
    assert _sender_headers(m) == {"dkim": ["example.com"], "return_path": "bounce@send.example.com", "from_failed": False}
    bad = EmailMessage()
    bad["Authentication-Results"] = "mx; dkim=fail header.d=example.com; spf=fail smtp.mailfrom=example.com; dmarc=fail header.from=example.com"
    bad["DKIM-Signature"] = "v=1; d=example.com; b=x"
    bad["Return-Path"] = "<x@example.com>"
    assert _sender_headers(bad) == {"dkim": [], "return_path": "", "from_failed": True}


async def test_recent_mail_carries_when_it_arrived_and_the_sender_headers(tmp_path):
    from assistant.mail import Mail
    from test_mail import ADDRESS, PASSWORD as MAIL_PW, FakeMailServer

    fake = FakeMailServer()
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = "Example <no-reply@example.com>", ADDRESS, "Your sign-in code", formatdate(time.time() - 60)
    m["DKIM-Signature"] = "v=1; d=example.com; b=x"
    m.set_content(f"Your verification code is {CODE}.")
    fake.box["2"] = m.as_bytes()
    box = Mail(Store(tmp_path / "s.db"), imap=fake.imap, smtp=fake.smtp)
    await box.connect("gmail", ADDRESS, MAIL_PW)
    got = await box.recent(12)
    assert got[0]["dkim"] == ["example.com"] and got[0]["arrived"] == 0.0 and got[0]["at"] > 0      # the fake sends no INTERNALDATE


# ------------------------------------------------------------------------------------------------ typing it, through the tools
class CodeLoginPage(Page):
    """A sign-in page (test_vault.Page) that asks for a code after the password, with one code box."""

    def __init__(self, url="https://www.example.com/login"):
        super().__init__(url=url, state={"pw": False, "text": "Enter the verification code we sent to your email", "otp": 6},
                         body="Enter the verification code we sent to your email. Welcome nahyun.kim@example.com")

    async def evaluate(self, js, arg=None):
        if "data-kelo-otp" in js:
            return {"boxes": 1} if "boxes" in js else None
        return await super().evaluate(js)


def login_setup(tmp_path, msgs, *, setting=True, url="https://www.example.com/login", accounts=True):
    st = Store(tmp_path / "a.db", KEY)
    st.save_credential("example", "Example", ["www.example.com"], {}, kind="login")
    st.set_login("example", USER, PASSWORD)
    st.save_credential("mail-gmail", "Gmail", ["imap.gmail.com"], {}, kind="email")
    st.set_credential_secret("mail-gmail", "app-password")
    st.set_mail_codes_auto(setting)
    page = CodeLoginPage(url)
    session = vault_session(page)
    box = FakeMail(msgs, accounts=accounts)
    extras = BrowserExtras(session, mail=box, auto_codes=st.mail_codes_auto, vault_log=st.add_vault_log)
    extras.code_wait = extras.find_wait = 0
    extras.code_judge = JUDGE
    tools = {x.name: x for x in [*vault_tools(st, session, Transient()), *extras.tools()]}
    return st, page, session, extras, tools, box


def fresh(sender="Example <no-reply@example.com>", **kw):
    return mail(sender=sender, at=time.time() + 2, **kw)


async def test_a_code_from_the_sites_own_mail_is_typed_without_asking_and_never_kept(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    st, page, session, extras, tools, box = login_setup(tmp_path, [fresh()])
    out = await tools["browser_login"].handler({"name": "example"})
    assert out.startswith("[sign-in result: verification_pending") and page.filled[-1] == ('[data-kelo-otp="0"]', CODE)
    assert "typed the sign-in code from no-reply@example.com mail" in out and "From example.com" in out
    assert CODE not in out and "A verification step is open (codes" not in out
    row = st.vault_log(1)[0]
    assert row["action"] == "code-typed" and "no-reply@example.com" in row["detail"] and "From example.com" in row["detail"]
    assert all(CODE not in r["detail"] for r in st.vault_log(100))                                          # nothing logged
    assert CODE not in caplog.text and CODE.encode() not in (tmp_path / "a.db").read_bytes()
    assert session.sign_ins == {}                                                                            # one code per sign-in


async def test_no_mail_or_an_unclear_one_falls_back_to_asking(tmp_path):
    st, page, session, extras, tools, box = login_setup(tmp_path, [])
    out = await tools["browser_login"].handler({"name": "example"})
    assert "follow what it says" in out and "find_login_code" in out and len(page.filled) == 2              # nothing typed
    box.msgs = [fresh(text="Your code is 111222"), fresh(text="Your code is 333444")]
    held, card, extra = await run_code_tool(tools["find_login_code"], {})
    assert card is not None and extra["login_code"]["code"] in ("111222", "333444")                         # the owner's card
    assert "after the user allowed it" in held


async def test_find_login_code_types_without_a_card_during_the_agents_own_sign_in(tmp_path):
    st, page, session, extras, tools, box = login_setup(tmp_path, [])
    await tools["browser_login"].handler({"name": "example"})                                              # no mail yet: asked
    box.msgs = [fresh()]                                                                                     # it arrives
    out, card, extra = await run_code_tool(tools["find_login_code"], {"site": "example.com"})
    assert card is None and extra is None and "without asking" in out and page.filled[-1] == ('[data-kelo-otp="0"]', CODE)


async def test_a_wrong_sender_or_another_site_is_never_typed_unasked(tmp_path):
    st, page, session, extras, tools, box = login_setup(tmp_path, [fresh(sender="Example <no-reply@examp1e.com>")])
    out = await tools["browser_login"].handler({"name": "example"})
    assert "follow what it says" in out and len(page.filled) == 2
    st2, page2, session2, extras2, tools2, box2 = login_setup(tmp_path / "b", [])
    await tools2["browser_login"].handler({"name": "example"})
    page2.filled.clear()
    box2.msgs = [fresh()]
    page2.url = "https://shop.other.com/verify"                                                             # the tab moved to another site
    assert await extras2.auto_code("shop.other.com") is None and await extras2.auto_code("example.com") is None
    assert page2.filled == []


async def test_without_a_sign_in_the_agent_started_nothing_is_typed_unasked(tmp_path):
    st, page, session, extras, tools, box = login_setup(tmp_path, [fresh()])
    held, card, extra = await run_code_tool(tools["find_login_code"], {"site": "example.com"})
    assert card is not None and card.startswith("Enter this code?")                                         # the owner's card, as before
    session.sign_ins["owner"] = {"site": "example.com", "at": time.time(), "task": "another-task"}
    assert await extras.auto_code("example.com") is None                                                     # another task's sign-in
    session.sign_ins["owner"] = {"site": "example.com", "at": time.time() - 700, "task": None}
    assert await extras.auto_code("example.com") is None                                                     # too long ago


async def test_the_setting_off_keeps_todays_flow(tmp_path):
    st, page, session, extras, tools, box = login_setup(tmp_path, [fresh()], setting=False)
    out = await tools["browser_login"].handler({"name": "example"})
    assert "follow what it says" in out and len(page.filled) == 2 and box.asked == 0
    held, card, extra = await run_code_tool(tools["find_login_code"], {})
    assert card is not None and extra["login_code"]["code"] == CODE                                         # shown to the owner first


async def test_the_setting_is_on_by_default_and_the_owner_can_turn_it_off(tmp_path):
    app = create_app(app_settings(tmp_path))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/vault")).json()["mail_codes_auto"] is True
        assert (await c.put("/api/vault/settings", json={"mail_codes_auto": False})).json() == {"mail_codes_auto": False}
        assert (await c.get("/api/vault")).json()["mail_codes_auto"] is False
        assert (await c.get("/api/vault/log")).json()[0]["action"] == "setting"
        assert (await c.put("/api/vault/settings", json={})).json() == {"mail_codes_auto": False}
