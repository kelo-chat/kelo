"""Kelo's browser sign-in records (store, brief B1): which sites a browser profile is signed in to. Sites and times only."""

import time

from assistant.store import Store


def test_a_sign_in_is_noted_per_profile_and_site(tmp_path):
    st = Store(tmp_path / "a.db")
    st.note_browser_sign_in("shop.example.com", "login")
    st.note_browser_sign_in("shop.example.com", "owner", profile="p_work01")
    rows = {(r["profile"], r["site"]): r for r in st.browser_sign_ins()}
    assert rows[("", "shop.example.com")]["how"] == "login" and rows[("p_work01", "shop.example.com")]["how"] == "owner"
    assert [r["site"] for r in st.browser_sign_ins("p_work01")] == ["shop.example.com"]
    assert st.browser_sign_ins("other") == []


def test_a_second_sign_in_updates_the_row_and_keeps_one(tmp_path):
    st = Store(tmp_path / "a.db")
    st.note_browser_sign_in("shop.example.com", "login")
    first = st.browser_sign_ins()[0]
    time.sleep(0.01)
    st.note_browser_sign_in("shop.example.com", "owner")
    rows = st.browser_sign_ins()
    assert len(rows) == 1 and rows[0]["how"] == "owner" and rows[0]["signed_at"] > first["signed_at"]


def test_touch_only_moves_last_used_of_an_existing_row(tmp_path):
    st = Store(tmp_path / "a.db")
    st.touch_browser_site("shop.example.com")          # no row: nothing is created
    assert st.browser_sign_ins() == []
    st.note_browser_sign_in("shop.example.com", "login")
    before = st.browser_sign_ins()[0]
    time.sleep(0.01)
    st.touch_browser_site("shop.example.com")
    after = st.browser_sign_ins()[0]
    assert after["signed_at"] == before["signed_at"] and after["last_used"] > before["last_used"]


def test_forget_removes_only_that_profile_and_site(tmp_path):
    st = Store(tmp_path / "a.db")
    st.note_browser_sign_in("shop.example.com", "login")
    st.note_browser_sign_in("shop.example.com", "login", profile="p_work01")
    assert st.forget_browser_sign_in("shop.example.com") is True
    assert st.forget_browser_sign_in("shop.example.com") is False
    assert [(r["profile"], r["site"]) for r in st.browser_sign_ins()] == [("p_work01", "shop.example.com")]


# ---- the writers: a sign-in typed by the owner, a site used, a site signed out (browser.py / takeover.py) ----------------------------
from assistant.tools.browser import BrowserSession, TOUCH_SECONDS
from assistant.tools.takeover import signed_in_by_hand


def test_a_password_typed_by_the_owner_then_submitted_is_a_sign_in_and_a_click_alone_is_not():
    typed = [{"k": "field", "label": "password", "secret": True, "value": None}, {"k": "submit", "label": "Sign in"}]
    assert signed_in_by_hand(typed) is True
    assert signed_in_by_hand([{"k": "field", "label": "search", "secret": False, "value": "x"}, {"k": "submit"}]) is False
    assert signed_in_by_hand([{"k": "click", "label": "Sign in"}, {"k": "nav", "url": "https://shop.example.com/"}]) is False


def test_a_hand_back_with_a_typed_sign_in_records_the_site_on_any_tab(tmp_path):
    seen = []
    s = BrowserSession(connect=None)
    s.on_owner_sign_in = seen.append
    page = type("P", (), {"url": "https://login.shop.example.com/session"})()
    s._pages["owner"] = page
    s.journal = type("J", (), {"signed_in": {"owner"}})()
    s.owner_handed_back("owner", "", "owner", "browser")
    assert seen == ["example.com"] and "owner" not in s.journal.signed_in
    s.owner_handed_back("owner", "", "owner", "browser")          # the flag is used up
    assert seen == ["example.com"]


def test_a_site_used_is_touched_at_most_once_per_ten_minutes():
    seen = []
    s = BrowserSession(connect=None)
    s.on_site_used = seen.append
    s._touch_site("shop.example.com", now=1000.0)
    s._touch_site("shop.example.com", now=1000.0 + TOUCH_SECONDS - 1)
    s._touch_site("shop.example.com", now=1000.0 + TOUCH_SECONDS)
    s._touch_site("news.example.org", now=1000.0)
    assert seen == ["shop.example.com", "shop.example.com", "news.example.org"]


def test_a_failing_store_hook_never_breaks_the_browser():
    s = BrowserSession(connect=None)
    def boom(site):
        raise RuntimeError("disk")
    s.on_site_used = boom
    s._touch_site("shop.example.com", now=5.0)   # no exception
