"""The browser's own INFO lines (logger assistant.browser) reach the server's container log, and the per-site copy line of a job's tab
says the tab, the site, the counts and the sign-in state, never a cookie name or value (card p-signin-gaps S2)."""

import logging

from assistant import server
from test_work_browser import COOKIE, Browser, in_tab, main_with_owner, work_on


def test_the_browser_logger_gets_one_handler_at_info():
    own = logging.getLogger("assistant.browser")
    before = list(own.handlers)
    try:
        server._show_browser_logs()
        server._show_browser_logs()
        mine = [h for h in own.handlers if getattr(h, "_kelo_browser", False)]
        assert len(mine) == 1 and own.level == logging.INFO
    finally:
        for h in list(own.handlers):
            if h not in before:
                own.removeHandler(h)


async def test_the_copy_line_says_counts_and_state_and_no_cookie(caplog):
    s, main, _ = await main_with_owner([dict(COOKIE)])
    work_on(s, Browser())
    await in_tab("task:w1", s.page)
    s.signed_in_site = lambda site: False
    with caplog.at_level(logging.INFO, logger="assistant.browser"):
        await in_tab("task:w1", lambda: s._seed_work_site("task:w1", "shop.example.com"))
    line = next(r.getMessage() for r in caplog.records if "site example.com" in r.getMessage())
    assert "task:w1" in line and "1 cookies copied of 1 in the source" in line and "no sign-in" in line
    assert COOKIE["name"] not in caplog.text and COOKIE["value"] not in caplog.text
