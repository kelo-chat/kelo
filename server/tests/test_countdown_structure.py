"""hc-final-a (docs/no-word-lists.md): tools/payment_step.STEP_JS reads a countdown by structure only: an ARIA timer or a <time>
element, or a leaf element whose whole text is a clock shape (m:ss, mm:ss, h:mm:ss). The words or class names around it decide nothing.
Real Chromium on local fixture pages (skipped without Playwright's browser); no real site."""

from __future__ import annotations

import pytest

from assistant.tools.payment_step import STEP_JS

pw = pytest.importorskip("playwright.async_api")


@pytest.fixture
async def page():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:   # no browser installed
            pytest.skip(f"no chromium: {e}")
        pg = await b.new_page()
        yield pg
        await b.close()


async def timers(page, body: str) -> list[str]:
    await page.set_content(f"<!doctype html><body>{body}</body>")
    return (await page.evaluate(STEP_JS))["timers"]


async def test_a_class_named_countdown_with_words_is_not_a_timer(page):
    assert await timers(page, "<span class='countdown'>Expires soon</span>") == []


async def test_a_clock_shaped_leaf_element_is_a_timer_whatever_it_is_called(page):
    assert await timers(page, "<div>04:59</div>") == ["04:59"]
    assert await timers(page, "<span class=anything>1:02:03</span>") == ["1:02:03"]
    assert await timers(page, "<div>04:59 left</div>") == []          # the whole text must be the clock


async def test_an_aria_timer_is_a_timer_whatever_its_text_says(page):
    assert await timers(page, "<span role=timer>4분 59초</span>") == ["4분 59초"]


async def test_a_time_element_is_a_timer(page):
    assert await timers(page, "<time>12:00</time>") == ["12:00"]


async def test_a_clock_shape_with_element_children_is_not_a_leaf(page):
    assert await timers(page, "<p><b>04:59</b></p>") == ["04:59"]          # the leaf <b> carries it
    assert await timers(page, "<div><span>04:59</span> <span>tail</span></div>") == ["04:59"]


async def test_a_hidden_clock_is_not_a_timer(page):
    assert await timers(page, "<div style='display:none'>04:59</div>") == []
