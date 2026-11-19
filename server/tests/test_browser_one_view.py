"""The browser has one view, the desktop one (owner 2026-11-19: "view_mobile인가? 그거 아예 빼버려"): no phone view tool, no view argument, no
phone identity, and no tool or prompt text that speaks of a mobile or phone view. Pure: no browser."""

import re
from pathlib import Path

from assistant.tools.browser import BrowserSession, browser_tools

CONTENT = Path(__file__).resolve().parents[1] / "content"
PHONE_VIEW_WORDS = re.compile(r"mobile view|phone view|phone-view|mobile \(|the phone's view|desktop / phone|PC/모바일|모바일 보기", re.I)


def test_the_browser_tools_have_no_view_tool_and_no_view_argument():
    tools = {t.name: t for t in browser_tools(BrowserSession(connect=lambda: None))}
    assert "browser_view" not in tools
    assert "view" not in tools["browser_open"].parameters["properties"]


def test_no_browser_tool_text_speaks_of_a_mobile_or_phone_view():
    for t in browser_tools(BrowserSession(connect=lambda: None)):
        text = " ".join([t.description, str(t.parameters)])
        assert not PHONE_VIEW_WORDS.search(text), (t.name, text[:200])


def test_the_session_has_no_view_state_left():
    s = BrowserSession(connect=lambda: None)
    for name in ("view", "set_view", "switch_view", "phone_cdp", "keep_view", "remember_view", "_phone", "_tap", "_touch_points"):
        assert not hasattr(s, name), name


def test_the_prompts_speak_of_no_mobile_or_phone_view():
    for path in (CONTENT / "prompts").glob("*.md"):
        assert not PHONE_VIEW_WORDS.search(path.read_text(encoding="utf-8")), path.name


def test_a_stored_per_site_view_is_dropped_when_the_server_starts(tmp_path):
    from cryptography.fernet import Fernet

    from assistant.server import create_app
    from assistant.settings import Settings
    from assistant.store import Store

    db, key = tmp_path / "a.db", Fernet.generate_key().decode()
    Store(db, key).set_meta("browser.site_views", '{"coupang.com": "mobile"}')   # what the old server kept
    create_app(Settings(token="t" * 32, provider="p", model="m", cdp_host="127.0.0.1", db_path=str(db), browser_worker=False))
    assert Store(db, key).meta("browser.site_views") == ""
