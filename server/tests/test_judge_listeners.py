"""The click listeners of the element a step judges are read by DevTools through a CSS path, and no page global is set while they are
read (the card's addendum decision 9 / item 6: a page script must not see Kelo's reading). Runs on Playwright Chromium (skipped when absent)."""

import pytest

from assistant.tools.browser import BrowserSession
from assistant.tools.commits import TARGET_JS

pw = pytest.importorskip("playwright.async_api")

PAGE = """<!doctype html><html><body>
<button id="buy">Buy</button>
<script>
  function markerHandler() { return 'listener-source-marker'; }
  document.getElementById('buy').addEventListener('click', markerHandler);
</script></body></html>"""


class _Reader:
    """The part of BrowserSession _listener_code uses: a DevTools session on a page."""

    async def cdp_session(self, page):
        return await page.context.new_cdp_session(page)


async def test_the_judged_element_has_a_path_that_finds_only_it_and_its_listeners_are_read_without_a_page_global():
    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        try:
            page = await browser.new_page()
            await page.set_content(PAGE)
            got = await page.locator("#buy").evaluate(TARGET_JS)
            assert got["judge_path"] == "#buy"
            # nothing is left in the page: no global from the reading
            assert await page.evaluate("() => '__keloJudgeTarget' in window") is False
            code = await BrowserSession._listener_code(_Reader(), page, got["judge_path"])
            assert code is not None and "listener-source-marker" in code
            assert await page.evaluate("() => '__keloJudgeTarget' in window") is False
            # an empty path reads nothing
            assert await BrowserSession._listener_code(_Reader(), page, "") is None
        finally:
            await browser.close()


async def test_a_duplicate_id_is_not_used_for_the_path_so_the_path_finds_exactly_that_element():
    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        try:
            page = await browser.new_page()
            await page.set_content('<button id="x">A</button><button id="x">B</button>')
            got = await page.locator("button").nth(1).evaluate(TARGET_JS)
            assert got["judge_path"] and got["judge_path"] != "#x"
            assert await page.evaluate("(p) => document.querySelector(p).textContent", got["judge_path"]) == "B"
        finally:
            await browser.close()


async def test_the_takeover_journal_runs_in_its_own_world_so_the_page_does_not_see_it():
    from assistant.tools import takeover

    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        try:
            page = await browser.new_page()
            await page.set_content("<button>Go</button>")
            cdp = await page.context.new_cdp_session(page)
            tree = await cdp.send("Page.getFrameTree", {})
            frame_id = tree["frameTree"]["frame"]["id"]
            world = await cdp.send("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": takeover.WORLD})
            await cdp.send("Runtime.evaluate", {"expression": takeover.SCRIPT,
                                                "contextId": world["executionContextId"]})
            # the journal is set in the isolated world; the page's own world has no such global
            assert await page.evaluate("() => window.__keloJournal === undefined") is True
            await cdp.detach()
        finally:
            await browser.close()
