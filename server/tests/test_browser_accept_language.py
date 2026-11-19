"""A tab's Accept-Language follows the user's language (addendum decision 4 of the browser-identity card): the list a Chrome of that
language sends, set per tab through the User-Agent override that also carries the browser's own client hints (so no header changes
except the language). The pure part is tested here; the live header and navigator.languages were measured on Chromium (2026-11-18)."""

from assistant.browser_devices import accept_language


def test_the_list_is_the_locale_its_language_and_english():
    assert accept_language("ko-KR") == "ko-KR,ko,en-US,en"
    assert accept_language("ja-JP") == "ja-JP,ja,en-US,en"
    assert accept_language("en-US") == "en-US,en"
    assert accept_language("ko") == "ko,en-US,en"


def test_no_locale_gives_no_list():
    assert accept_language("") == ""


import pytest  # noqa: E402

from assistant.tools.browser import BrowserSession  # noqa: E402


class FakePage:
    def __init__(self, ua):
        self.ua = ua

    async def evaluate(self, js, arg=None):
        return self.ua


async def test_the_desktop_identity_is_the_browser_own_agent_with_the_list_and_the_hints_of_this_chrome():
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.8127.36 Safari/537.36"
    ident = await BrowserSession._desktop_identity(FakePage(ua), "ko-KR,ko,en-US,en")
    assert ident["userAgent"] == ua and ident["acceptLanguage"] == "ko-KR,ko,en-US,en"
    meta = ident["userAgentMetadata"]
    assert meta["platform"] == "Linux" and meta["mobile"] is False and meta["formFactors"] == ["Desktop"] and meta["model"] == ""
    assert meta["fullVersion"] == "158.0.8127.36" and meta["platformVersion"] == ""   # the machine's own OS (Linux) by default


async def test_the_desktop_identity_states_chrome_os_with_the_switch_on(monkeypatch):
    from assistant import browser_identity
    monkeypatch.setattr(browser_identity, "STATED_OS", "chromeos")
    ua = "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.8127.36 Safari/537.36"
    meta = (await BrowserSession._desktop_identity(FakePage(ua), "ko-KR,ko"))["userAgentMetadata"]
    assert meta["platform"] == "Chrome OS" and meta["platformVersion"] == "16868.13.0"   # the Chrome OS row of Chrome 158


async def test_no_chrome_version_in_the_agent_means_no_override_at_all():
    assert await BrowserSession._desktop_identity(FakePage("Mozilla/5.0 something"), "ko-KR,ko") is None


pw = pytest.importorskip("playwright.async_api")


async def test_on_chromium_the_list_reaches_the_request_and_the_page_and_the_hints_are_still_sent():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        try:
            ctx = await b.new_context()
            page = await ctx.new_page()
            seen = []

            async def answer(route):   # a secure context (localhost) answered here, nothing leaves the machine
                seen.append(dict(route.request.headers))
                await route.fulfill(body="<!doctype html><p>x</p>", content_type="text/html")
            await page.route("http://kelo-identity.localhost/**", answer)
            await page.goto("http://kelo-identity.localhost/")
            cdp = await ctx.new_cdp_session(page)
            ident = await BrowserSession._desktop_identity(page, "ko-KR,ko,en-US,en")
            assert ident is not None and ident["userAgentMetadata"]["mobile"] is False
            await cdp.send("Emulation.setLocaleOverride", {"locale": "ko-KR"})
            await cdp.send("Emulation.setUserAgentOverride", ident)
            await page.goto("http://kelo-identity.localhost/?again")
            assert seen[-1]["accept-language"].startswith("ko-KR,ko")
            assert "sec-ch-ua" in seen[-1] and "sec-ch-ua-platform" in seen[-1]   # the hints are still sent
            assert await page.evaluate("() => navigator.languages.join(',')") == "ko-KR,ko,en-US,en"
            assert await page.evaluate("() => Intl.DateTimeFormat().resolvedOptions().locale") == "ko-KR"
        finally:
            await b.close()
