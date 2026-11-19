"""A page's own pop-ups (alert, confirm, prompt) during browser steps (owner report 2026-11-19; docs/browser.md "Page pop-ups"): only
the user (or what they allowed) says yes to a page's question, the model is told what was asked, what Cancel means and what to do next,
and the live view gets a line for it. Fake pages; the click itself on real Chromium is in test_browser_frames_popups.py."""

from __future__ import annotations

import time

from assistant.agent.fence import plain
from assistant.tools.browser import BrowserSession
from assistant.tools.page_dialogs import KEEP, SHOW_SECONDS, TabDialogs, answer_for, live_view

from test_purchase import shop


class Dialog:
    def __init__(self, kind: str, message: str):
        self.type, self.message, self.answer = kind, message, None

    async def accept(self):
        self.answer = "accept"

    async def dismiss(self):
        self.answer = "dismiss"


async def pop(page, kind: str, message: str) -> Dialog:
    d = Dialog(kind, message)
    await page.handlers["dialog"](d)
    return d


def test_only_the_user_or_what_they_allowed_says_yes_to_a_page_question():
    for kind in ("confirm", "prompt", "beforeunload", ""):
        assert answer_for(kind, by_user=False, in_purchase=False, in_order=False) == ("cancel", "")
    assert answer_for("alert", by_user=False, in_purchase=False, in_order=False) == ("closed", "")
    assert answer_for("confirm", by_user=True, in_purchase=False, in_order=False) == ("ok", "user")
    assert answer_for("confirm", by_user=False, in_purchase=True, in_order=False) == ("ok", "purchase")
    assert answer_for("confirm", by_user=False, in_purchase=False, in_order=True) == ("ok", "order")


async def test_a_step_never_says_yes_on_its_own_and_the_reading_says_what_was_asked_and_what_it_means():
    session, page = shop()
    await session.page()
    asked = [await pop(page, k, m) for k, m in (("confirm", "Sign in first. Continue?"), ("prompt", "Your name?"),
                                                ("beforeunload", "Leave?"), ("alert", "Pick a size first"))]
    assert [d.answer for d in asked] == ["dismiss"] * 4          # the browser gets exactly the calls it got before
    out = plain(await session.snapshot())
    assert "[The page asked a question in its own pop-up (confirm): Sign in first. Continue? Kelo answered Cancel for you" in out
    assert out.count("Kelo answered Cancel for you") == 3
    assert "the step that opened it was cancelled and did nothing" in out and "so do not repeat it" in out
    assert "[The page showed a message in its own pop-up (alert): Pick a size first It had only OK" in out
    assert "request_login" in out                                  # the chat, no saved login: the usual sign-in path
    assert "Kelo answered" not in plain(await session.snapshot())  # told once


async def test_the_next_step_is_the_usual_sign_in_path_and_a_repeat_is_counted(monkeypatch):
    session, page = shop()
    await session.page()
    session.login_for = lambda site: "shop-login" if site == "example.com" else ""
    await pop(page, "confirm", "Sign in first. Continue?")
    out = plain(await session.snapshot())
    assert "a saved login exists for example.com" in out and "browser_login(name='shop-login')" in out
    session.login_for = lambda site: ""
    monkeypatch.setattr(BrowserSession, "_is_job", staticmethod(lambda: True))
    await pop(page, "confirm", "Sign in first. Continue?")
    out = plain(await session.snapshot())
    assert "nobody can sign in during this job: report needs_input" in out and "request_login" not in out
    assert "this same pop-up 2 times" in out


async def test_the_user_at_the_controls_an_allowed_purchase_or_the_order_still_answer_ok():
    session, page = shop()
    await session.page()
    session._control["owner"] = "phone-1"                          # the owner holds the tab: the pop-up of their tap is theirs
    assert (await pop(page, "confirm", "Delete this item?")).answer == "accept"
    session._control.pop("owner")
    session.order_dialog = lambda tab, host, message: message == "Use this address?"
    assert (await pop(page, "confirm", "Use this address?")).answer == "accept"
    assert (await pop(page, "confirm", "Delete this item?")).answer == "dismiss"
    session.allow_purchase("shop.example.com")
    assert (await pop(page, "confirm", "Pay 24,000?")).answer == "accept"
    out = plain(await session.snapshot())
    assert "Kelo answered OK: accepted (the user was controlling the browser)" in out
    assert "Kelo answered OK: accepted (a step of the order the user asked for)" in out
    assert "Kelo answered OK: accepted (inside the purchase the user allowed)" in out
    seen = session.page_dialog("owner")
    assert seen["answer"] == "ok" and seen["text"] == "The page asked: “Pay 24,000?” It was answered OK."


def test_a_tab_keeps_its_newest_pop_up_for_the_live_view_and_counts_what_a_step_must_compare():
    tab = TabDialogs()
    now = time.time()
    start = tab.mark()
    assert tab.only_cancelled(start) is None                       # nothing came
    tab.add("confirm", "Sign in first?", "cancel", "", "https://a.example/x", now)
    tab.add("confirm", "Sign in first?", "cancel", "", "https://a.example/x", now + 5)
    assert tab.only_cancelled(start) is True
    d = tab.shown("https://a.example/x", now + 6)
    assert d is not None and d.count == 2
    assert live_view(d) == {"kind": "confirm", "message": "Sign in first?", "answer": "cancel", "count": 2, "at": round(now + 5, 3),
                            "text": "The page asked: “Sign in first?” Kelo answered Cancel."}
    assert tab.shown("https://a.example/other", now + 6) is None   # the tab went elsewhere
    assert tab.shown("https://a.example/x", now + 5 + SHOW_SECONDS + 1) is None
    tab.add("alert", "Saved", "closed", "", "https://a.example/x", now + 7)
    assert tab.only_cancelled(start) is False                      # a message was closed: the page may have acted
    assert live_view(tab.last)["text"] == "The page said: “Saved”" and tab.last.count == 1
    m = tab.mark()
    tab.add("confirm", "Pay?", "ok", "purchase", "https://a.example/x", now + 8)
    assert tab.only_cancelled(m) is False                          # an OK: the page acted
    assert [x.message for x in tab.take()] == ["Sign in first?", "Sign in first?", "Saved", "Pay?"] and tab.take() == []
    for i in range(KEEP + 4):
        tab.add("alert", str(i), "closed", "", "u", now)
    assert len(tab.take()) == KEEP                                 # the newest ones for the next reading
    assert tab.mark() == (4 + KEEP + 4, 2)                         # counts are never trimmed
