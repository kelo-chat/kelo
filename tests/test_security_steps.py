"""Security steps (tools/commits.py "security"): changing how an account is protected or reached (app passwords, 2-step
verification, devices, keys, tokens) or issuing an official document always asks, and nothing covers them: no task grant, no
order, no purchase window, no sign-in shortcut (2026-11-12: another assistant made itself an app password to keep reading mail)."""

from __future__ import annotations

import pytest

from assistant.tools.browser import step_decider
from assistant.tools.commits import judge_click, judge_words, security_step
from assistant.tools.task_permission import NEVER

NAVER_SECURITY = "https://nid.naver.com/user2/help/myInfoV2?m=viewSecurity"


def button(url: str, text: str, **more) -> dict:
    return {"url": url, "title": "", "text": text, "tag": "button", "submits": True, "in_form": True, **more}


@pytest.mark.parametrize("url, text", [
    (NAVER_SECURITY, "애플리케이션 비밀번호 생성"),
    (NAVER_SECURITY, "생성하기"),                        # a changing verb alone on a security page
    (NAVER_SECURITY, "확인"),                            # a plain confirm there too
    ("https://myaccount.google.com/apppasswords", "Create"),
    ("https://myaccount.google.com/security", "Turn off 2-Step Verification"),
    ("https://nid.naver.com/user2/help/myInfoV2", "2단계 인증 해제"),
    ("https://github.com/settings/tokens", "Generate new token"),
    ("https://example.com/account", "Remove this device from trusted devices"),
    ("https://hometax.go.kr/websquare/websquare.html", "발급"),
    ("https://www.efamily.scourt.go.kr/", "가족관계증명서 발급"),
    ("https://www.gov.kr/mw/AA020", "주민등록등본 신청"),
    ("https://example.gov/records", "Print official certificate"),
    ("https://mail.google.com/mail/u/0/#settings/fwdandpop", "Add a forwarding address"),
    ("https://mail.google.com/mail/u/0/#settings/fwdandpop", "Forward a copy of incoming mail to"),
    ("https://mail.google.com/mail/u/0/#settings/fwdandpop", "Save Changes"),
    ("https://mail.google.com/mail/u/0/#settings/accounts", "Grant access to your account"),
    ("https://outlook.live.com/mail/0/options/mail/forwarding", "Enable forwarding"),
    ("https://mail.naver.com/", "전달 주소 추가"),
    ("https://mail.example.com/settings", "위임"),
])
def test_security_and_document_steps_are_their_own_kind(url, text):
    assert judge_click(button(url, text), "button") == "security"


@pytest.mark.parametrize("url, text, kind", [
    ("https://www.gov.kr/", "신청", "apply"),                      # an application alone stays "apply"
    ("https://shop.example.com/event", "쿠폰 발급받기", ""),          # a shop's coupon is not a document
    ("https://shop.example.com/event", "포인트 발급", ""),
    ("https://shop.example.com/goods/1", "장바구니 담기", ""),
    ("https://shop.example.com/mypage", "저장", "account"),           # an ordinary settings page as before
    ("https://news.example.com/article/1", "Post comment", "send"),
])
def test_ordinary_steps_keep_their_kind(url, text, kind):
    assert judge_click(button(url, text), "button") == kind


def test_forwarding_one_open_message_is_not_a_security_step():
    assert judge_click(button("https://mail.google.com/mail/u/0/#inbox/FMfcgzQ", "Forward"), "button") != "security"
    assert not security_step("Forward", "/mail/u/0/#inbox/FMfcgzQ Inbox")


def test_a_security_link_still_asks_and_unreadable_elements_judge_the_selector():
    link = {"url": "https://myaccount.google.com/security", "title": "", "text": "Turn off 2-Step Verification", "tag": "a",
            "href": "https://myaccount.google.com/signinoptions/two-step-verification/off", "submits": False, "in_form": False}
    assert judge_click(link, "a") == "security"
    assert judge_click(None, "button:has-text('애플리케이션 비밀번호 생성')") == "security"
    assert judge_click(None, "button:has-text('확인')", url=NAVER_SECURITY) == "security"
    assert judge_words("Generate app password") == "security" and security_step("발급") and not security_step("쿠폰 발급")


def test_no_task_permission_can_cover_it():
    assert "security" in NEVER


class _Session:
    """Every shortcut of the decider says yes; only the kind is "security"."""
    def __init__(self, retry: bool = False):
        self.retry = retry
    async def judge(self, tool, args): return "security"
    def retry_of_allowed(self, sel): return self.retry
    def sign_in_continue(self, sel): return True
    def use_purchase_action(self): return True
    def current_host(self): return "nid.naver.com"
    def current_url(self): return NAVER_SECURITY
    def judged_secret(self, sel): return False
    def judged_info(self, sel): return None
    def note(self, *a): pass


class _Yes:
    def __getattr__(self, name):
        return lambda *a, **k: True
    def current(self): return None


async def test_nothing_covers_a_security_step_but_a_retry_of_the_one_just_allowed():
    decide = step_decider(_Session(), "browser_click", grants=_Yes(), orders=_Yes())
    assert await decide({"selector": "e12"}) is False
    assert await step_decider(_Session(), "browser_type", grants=_Yes(), orders=_Yes())({"selector": "e12", "text": "x"}) is False
    assert await step_decider(_Session(retry=True), "browser_click", grants=_Yes(), orders=_Yes())({"selector": "e12"}) is True
