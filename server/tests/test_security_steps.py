"""Security steps (tools/commits.py "security", the step judge's account_security flag): changing how an account is protected or reached
(app passwords, 2-step verification, devices, keys, tokens) always asks, and nothing covers them: no task grant, no
order, no purchase window, no sign-in shortcut (2026-11-12: another assistant made itself an app password to keep reading mail)."""

from __future__ import annotations

from assistant.tools.browser import step_decider
from assistant.tools.task_permission import NEVER

NAVER_SECURITY = "https://nid.naver.com/user2/help/myInfoV2?m=viewSecurity"


def test_no_task_permission_can_cover_it():
    assert "security" in NEVER


class _Session:
    """Every shortcut of the decider says yes; only the kind is "security"."""
    def __init__(self, retry: bool = False):
        self.retry = retry
    async def judge(self, tool, args): return "security"
    def retry_of_allowed(self, sel): return self.retry
    def sign_in_continue(self, sel): return True
    def sign_in_remember(self, sel): return False
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
