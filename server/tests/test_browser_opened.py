"""The address last opened in a tab is kept apart from its DevTools target (BrowserSession._targets is key -> (target id, context id)).
Before the fix both were written into the same dict: opening a URL under a tab's key made target_id() return the first letter of
the address ("h" for "https://...")."""

from assistant.tools.browser import BrowserSession


async def _connect():
    return None


def test_opening_an_address_does_not_change_the_target_of_the_tab():
    s = BrowserSession(connect=_connect)
    s._targets["probe_session_check"] = ("T-PROBE", "C")
    s._note_opened("probe_session_check", "https://m.local-check.invalid/")
    assert s.target_id("probe_session_check") == "T-PROBE"
    assert s._opened["probe_session_check"] == "https://m.local-check.invalid/"
    assert s.target_id("h") == ""


def test_a_tab_that_is_forgotten_forgets_the_address_it_opened():
    s = BrowserSession(connect=_connect)
    s._note_opened("owner", "https://shop.example.com/deal")
    s._forget_tab("owner")
    assert "owner" not in s._opened
    assert s.target_id("owner") == ""
