"""Input on the browser's one (desktop) view is the mouse, like a person's, and nothing scrolls the page before a person's scroll
(addendum 8). The Chromium tests (skipped without Playwright's browser) check what a page sees."""

import pytest

from assistant.tools import humanize
from assistant.tools.browser import COVERED_JS, BrowserSession

pw = pytest.importorskip("playwright.async_api")


class FakeCDP:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, method, params=None):
        self.sent.append((method, dict(params or {})))
        return {}


class FakeLocator:
    """A locator whose box moves when the page is scrolled (the first box is below the view's middle)."""
    def __init__(self, boxes):
        self.boxes = list(boxes)
        self.first = self

    async def bounding_box(self, timeout=None):
        return self.boxes[0] if len(self.boxes) == 1 else self.boxes.pop(0)


class FakePage:
    async def evaluate(self, js, arg=None):
        return [1440, 900]


class FakeOwner:
    def __init__(self, loc):
        self._loc = loc

    def locator(self, selector):
        return self._loc


def test_the_covered_check_does_not_scroll_the_page():
    # the element is brought into view by a person's scroll first (BrowserSession._bring_into_view), never by the check itself
    assert "scrollIntoView" not in COVERED_JS


def test_the_browser_has_no_touch_input_left():
    # one view, the desktop one: a tap, a swipe or a finger is never sent by the browser
    assert not hasattr(BrowserSession, "_touch_points") and not hasattr(BrowserSession, "_tap")
    assert not hasattr(humanize, "idle")


async def test_bringing_an_element_into_view_sends_no_swipe_or_touch():
    s = BrowserSession(connect=lambda: None)
    cdp = FakeCDP()
    s._tab_cdp["owner"] = cdp
    loc = FakeLocator([{"x": 10, "y": 700, "width": 200, "height": 40}])   # below the middle: a person would wheel down
    await s._bring_into_view(FakePage(), FakeOwner(loc), "#buy", "owner")
    assert not [m for m, _ in cdp.sent if m.startswith("Input.synthesize") or m.startswith("Input.dispatchTouch")]


@pytest.fixture
async def page():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(viewport={"width": 1280, "height": 800})
        pg = await c.new_page()
        await pg.set_content(
            "<!doctype html><body style='height:4000px'>"
            "<script>window.moves = 0; addEventListener('mousemove', () => window.moves++);</script></body>")
        yield pg
        await b.close()


async def test_a_desktop_look_moves_the_pointer(page):
    humanize.configure(enabled=True, seed=3, speed=0.01)
    try:
        await humanize.dwell(page)
        assert await page.evaluate("window.moves") > 0
    finally:
        humanize.configure(reset=True)


async def test_covered_check_leaves_the_scroll_position_alone(page):
    await page.evaluate("window.scrollTo(0, 1500)")
    top = await page.evaluate("scrollY")
    await page.evaluate(COVERED_JS, await page.evaluate_handle("document.body"))
    assert await page.evaluate("scrollY") == top
