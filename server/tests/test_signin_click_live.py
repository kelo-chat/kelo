"""browser_login on a real page (card p-signin-gaps S5/S6): no form -> nothing typed; via clicks the site's own sign-in link; the form's
unticked options come back before anything is typed; keep ticks the chosen one and no other; nothing opens an address by itself."""

import re

import pytest
from cryptography.fernet import Fernet
from playwright import async_api as pw

from assistant.store import Store
from assistant.tools import humanize
from assistant.tools.browser import BrowserSession
from assistant.tools.vault_tools import vault_tools
from test_browser_tools import fake_resolver

KEY = Fernet.generate_key().decode()
PUBLIC = fake_resolver({h: "93.184.216.34" for h in ("gift.example.com", "accounts.example.com")})
POSTED: list[str] = []
GOTO: list[str] = []
PAGES = {
    "https://gift.example.com/home": '<p>Gifts</p><a id=signin href="https://accounts.example.com/login">Sign in</a>',
    "https://gift.example.com/search": '<form id=top action="https://gift.example.com/home"><input type=text id=q placeholder="Search"><button>Go</button></form>',
    "https://accounts.example.com/login": """<form method=post action="https://gift.example.com/home">
        <input name=id autocomplete=username><input type=password name=pw autocomplete=current-password>
        <label><input type=checkbox name=stay> Stay signed in</label><label><input type=checkbox name=offers> Send me offers</label>
        <button>Go</button></form>""",
}


@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context()

        async def serve(route):
            req = route.request
            if req.resource_type == "document":
                GOTO.append(req.url)
            if req.method == "POST":
                POSTED.append(req.post_data or "")
            body = PAGES.get(req.url.split("?")[0])
            await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><body>{body or 'not found'}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


async def test_the_sign_in_is_reached_by_a_click_and_keeps_the_browser_signed_in(ctx, tmp_path):
    POSTED.clear()
    GOTO.clear()
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.settle_seconds = 0
    s.sign_in_wait = 0
    st = Store(tmp_path / "a.db", KEY)
    st.save_credential("shop", "Shop", ["accounts.example.com"], {}, kind="login")
    st.set_login("shop", "user@example.com", "pw-123456")
    login = {t.name: t for t in vault_tools(st, s)}["browser_login"]
    await s.open("https://gift.example.com/home")
    out = await login.handler({"name": "shop"})
    assert out.startswith("[No sign-in form") and "Sign in" in out
    out = await login.handler({"name": "shop", "via": "#signin"})
    assert "unticked options" in out and await page.evaluate("() => document.querySelector('[name=id]').value") == ""
    sel = re.search(r"- `([^`]+)`: .*Stay signed in", out).group(1)
    assert login.auto_approve({"name": "shop", "keep": sel}) is True and login.auto_approve({"name": "shop"}) is False
    out = await login.handler({"name": "shop", "keep": sel})
    assert POSTED and "stay=on" in POSTED[-1] and "offers" not in POSTED[-1]
    assert GOTO[:2] == ["https://gift.example.com/home", "https://accounts.example.com/login"]   # one open, one click, no jump
    assert "pw-123456" not in out


async def test_a_page_with_only_a_search_field_gets_nothing_typed(ctx, tmp_path):
    """No password field and no field that takes an account name: the saved login's name must never go into a search box."""
    POSTED.clear()
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.settle_seconds = 0
    s.sign_in_wait = 0
    st = Store(tmp_path / "a.db", KEY)
    st.save_credential("shop", "Shop", ["accounts.example.com"], {}, kind="login")
    st.set_login("shop", "user@example.com", "pw-123456")
    login = {t.name: t for t in vault_tools(st, s)}["browser_login"]
    await s.open("https://gift.example.com/search")
    out = await login.handler({"name": "shop"})
    assert out.startswith("[No sign-in form")
    assert await page.evaluate("() => document.querySelector('#q').value") == ""
    assert not POSTED and "user@example.com" not in out
