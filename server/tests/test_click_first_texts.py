"""Click-first (owner decision 2026-11-19): every text that tells the agent how to reach a page says to open an address only to get
onto a site and to move within it by its own links; none tells it to reopen or build an address (card p-signin-gaps S7)."""

from pathlib import Path

from assistant import job_cuts
from assistant.buy_ready import LOGIN_PROMPT
from assistant.tools.browser import BROWSER_LOST_NOTE, LOST_ACT_NOTE, BrowserSession, browser_tools
from assistant.tools.challenge import signed_out_note

ROOT = Path(__file__).resolve().parents[2]


def test_browser_open_says_to_enter_a_site_by_its_home_page_and_move_by_clicks():
    async def connect():
        return None
    text = {t.name: t for t in browser_tools(BrowserSession(connect=connect))}["browser_open"].description
    assert "Use it only to get onto a site" in text and "click its links, menus and buttons" in text
    assert "never type or build an address of that site" in text and "never one you made up" in text
    assert "Open the address you were given directly" not in text


def test_recovery_texts_go_back_through_the_sites_own_links():
    for text in (BROWSER_LOST_NOTE, LOST_ACT_NOTE, job_cuts.TAB_NEW):
        assert "home page" in text and "own links" in text, text
        assert "Open it again" not in text and "Open {url} again" not in text
    assert "via=<its ref>" in LOGIN_PROMPT
    note = signed_out_note("example.com", "shop", job=True, minutes=15)
    assert "follow the site's own sign-in link" in note and "via" in note


def test_the_job_start_address_is_the_home_page_or_the_users():
    text = (ROOT / "prompts" / "tools" / "delegate.md").read_text()
    assert "the site's home page, or the exact address the user gave" in text


def test_a_checkout_job_starts_at_the_shops_home_page_and_clicks_to_the_cart():
    """Owner answer 2026-11-19 (S7b): a checkout job never opens the cart or checkout address the chat gives; it enters the shop at
    its home page and clicks to the cart like a person."""
    from types import SimpleNamespace

    from assistant.workers import Workers

    fake = SimpleNamespace(host=SimpleNamespace(browser_split=True, pay_split=True, phone_split=True), _notes=lambda: "")
    text = Workers._brief(fake, "w1", "buy the shirt", "checkout", "https://shop.example.com/cart?x=1", [], 10, 5)
    assert "Start at: https://shop.example.com/ (the shop" in text
    assert "https://shop.example.com/cart" not in text
    assert "Start on the shop's home page" in text and "click to the cart" in text
