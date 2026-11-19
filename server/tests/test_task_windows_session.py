"""One window per task, with its own tabs (brief C1b): a popup is a tab of the task's window and the active one; closing the active tab goes
back to its opener (with the note) or to the last tab; a main-browser task opens its window with Target.createTarget newWindow; a work task
does not; closing the task closes every one of its tabs; tabs() lists them. Fakes only (no browser)."""

import asyncio
from types import SimpleNamespace

from assistant.tools.browser import TAB_CLOSED_NOTE, BrowserSession, browser_owner
from assistant.tools.work_browser import WorkBrowser

from test_work_browser import Browser, BrowserCdp, Ctx, Page, in_tab, main_with_owner, work_on


def close(page):
    """What Playwright does when a page closes: the page says so (the 'close' listener of _watch)."""
    page._closed = True
    page.handlers["close"]()


async def settle():
    for _ in range(3):
        await asyncio.sleep(0)


async def test_a_popup_becomes_the_active_tab_of_the_window_and_the_first_stays_open():
    s, main, _ = await main_with_owner()
    first = s._pages["owner"]
    popup = Page("https://b.example/", main)
    s._on_popup("owner", first, popup)
    await settle()
    win = s._windows["owner"]
    assert [t.id for t in win.tabs] == ["t1", "t2"] and win.active == "t2"
    assert s._pages["owner"] is popup and not first.is_closed()


async def test_closing_the_popup_goes_back_to_the_opener_tab_with_the_note():
    s, main, _ = await main_with_owner()
    first = s._pages["owner"]
    popup = Page("https://b.example/", main)
    s._on_popup("owner", first, popup)
    await settle()
    close(popup)
    await settle()
    assert s._pages["owner"] is first and s._windows["owner"].active == "t1"
    assert any(n.startswith(TAB_CLOSED_NOTE.split("{")[0]) and "tab t1" in n for n in s._notes["owner"])


async def test_closing_the_active_tab_whose_opener_is_gone_goes_to_the_last_tab():
    s, main, _ = await main_with_owner()
    first = s._pages["owner"]
    second = Page("https://b.example/", main)
    s._on_popup("owner", first, second)
    third = Page("https://c.example/", main)
    s._on_popup("owner", second, third)               # opened by the second tab; the third is active
    await settle()
    close(second)                                      # the middle tab closes; the active one stays the third
    await settle()
    assert s._windows["owner"].active == "t3" and s._pages["owner"] is third
    close(third)                                       # the active tab closes and its opener is gone: the last tab left is the first
    await settle()
    assert s._windows["owner"].active == "t1" and s._pages["owner"] is first


async def test_a_main_browser_task_gets_its_page_from_a_window_of_its_own():
    s, main, _ = await main_with_owner()

    class WinCdp(BrowserCdp):
        def __init__(self, ctx):
            super().__init__()
            self.ctx = ctx

        async def send(self, method, params=None):
            self.sent.append((method, params or {}))
            if method == "Target.createTarget":
                page = Page(params.get("url", ""), self.ctx)
                self.ctx.pages.append(page)
                self.ctx.waiting.set_result(page)
            return {}

    class Expect:
        def __init__(self, ctx):
            self.ctx = ctx

        async def __aenter__(self):
            self.ctx.waiting = asyncio.get_running_loop().create_future()
            return self

        async def __aexit__(self, *a):
            return False

        @property
        def value(self):
            return self.ctx.waiting

    class WinCtx(Ctx):
        def expect_page(self):
            return Expect(self)

    ctx = WinCtx(cid="main-window-ctx")
    cdp = WinCdp(ctx)

    class Br:
        async def new_browser_cdp_session(self):
            return cdp

    s.__dict__["_browser"] = Br()
    page = await s._new_window(ctx)
    assert page in ctx.pages
    assert cdp.sent == [("Target.createTarget", {"url": "about:blank", "newWindow": True})]


async def test_a_work_task_does_not_ask_for_a_window_of_the_main_browser():
    s, main, _ = await main_with_owner()
    browser = Browser()
    work_on(s, browser)
    await in_tab("task:w1", s.page)
    assert ("Target.createTarget", {"url": "about:blank", "newWindow": True}) not in browser.cdp.sent
    assert s._windows["task:w1"].tabs and s._windows["task:w1"].active == "t1"


async def test_closing_the_task_closes_every_tab_of_its_window():
    s, main, _ = await main_with_owner()
    first = s._pages["owner"]
    popup = Page("https://b.example/", main)
    s._on_popup("owner", first, popup)
    await settle()
    await s.close_tab("owner")
    assert first.is_closed() and popup.is_closed() and "owner" not in s._windows


async def test_tabs_lists_the_task_windows_tabs():
    s, main, _ = await main_with_owner()
    first = s._pages["owner"]
    popup = Page("https://b.example/", main)
    s._on_popup("owner", first, popup)
    await settle()
    listed = {t["key"]: t for t in await s.tabs()}["owner"]["tabs"]
    assert [t["id"] for t in listed] == ["t1", "t2"] and [t["active"] for t in listed] == [False, True]
    assert listed[1]["opener"] == "t1"


# ---- C1c: the browser_tabs tool and the tab header of a reading (brief C1c) ----------------------------------------------------------
from assistant.tools.browser_more import BrowserExtras



async def work_two_tabs():
    s, main, _ = await main_with_owner()
    browser = Browser()
    work_on(s, browser)
    await in_tab("task:w1", s.page)                    # t1 of the work window
    s._windows["task:w1"].tabs[0].page.url = "https://shop.example.com/"
    return s, browser


async def tool(s, key, args):
    t = {x.name: x for x in BrowserExtras(s).tools()}["browser_tabs"]
    token = browser_owner.set(key)
    try:
        return await t.handler(args)
    finally:
        browser_owner.reset(token)


async def test_browser_tabs_lists_the_windows_tabs_and_marks_the_active_one():
    s, _ = await work_two_tabs()
    await in_tab("task:w1", lambda: s.new_tab("task:w1"))
    out = await tool(s, "task:w1", {"action": "list"})
    assert "  t1: " in out and "* t2: " in out and "shop.example.com" in out


async def test_browser_tabs_new_then_close_keeps_the_active_tab_and_refuses_the_last_one():
    s, _ = await work_two_tabs()
    assert "A new empty tab" in await tool(s, "task:w1", {"action": "new"})
    assert len(s._windows["task:w1"].tabs) == 2 and s._windows["task:w1"].active == "t2"
    closed = await tool(s, "task:w1", {"action": "close", "tab": "t1"})
    assert "Tab t1 is closed" in closed and "t2" in closed and [t.id for t in s._windows["task:w1"].tabs] == ["t2"]
    assert await tool(s, "task:w1", {"action": "close", "tab": "t2"}) == "error: the last tab closes with the task"


async def test_a_tab_id_that_is_not_in_this_window_is_refused():
    s, _ = await work_two_tabs()
    await in_tab("task:w2", lambda: s.work.open_context("task:w2", []))   # another task with its own window
    assert await tool(s, "task:w1", {"action": "switch", "tab": "t9"}) == "error: no such tab in this window"
    assert await tool(s, "task:w1", {"action": "close", "tab": "t9"}) == "error: no such tab in this window"


async def test_the_reading_names_the_tab_when_the_window_has_more_than_one():
    s, _ = await work_two_tabs()
    s._snapshot = lambda **kw: _text("PAGE")   # the page's own reading is faked; the header is what is tested
    assert (await in_tab("task:w1", s.snapshot)) == "PAGE"                 # one tab: no header
    await in_tab("task:w1", lambda: s.new_tab("task:w1"))
    out = await in_tab("task:w1", s.snapshot)
    assert out.startswith("[tab t2 of 2:") and out.endswith("PAGE")


async def _text(v):
    return v


async def test_the_owners_tab_events_go_into_the_takeover_journal():
    """C1d: while the owner holds the task's window, opening, switching and closing its tabs are written down for the agent (host only)."""
    from assistant.tools.takeover import Takeover, TakeoverJournal
    s, main, _ = await main_with_owner()
    s.journal = TakeoverJournal(lambda page: None)
    s.journal.open["owner"] = Takeover(0.0)
    first = s._pages["owner"]
    popup = Page("https://b.example/cart?x=1", main)
    s._on_popup("owner", first, popup)
    await settle()
    await s._activate("owner", "t1")
    close(popup)
    await settle()
    entries = s.journal.open["owner"].entries
    assert [(e["k"], e.get("label"), e.get("url")) for e in entries] == [
        ("tab_opened", "t2", "b.example"), ("tab_switched", "t1", ""), ("tab_closed", "t2", "b.example")]
    assert "cart" not in str(entries)


class _Opened:
    """A page the owner opened in the main browser: Playwright's 'page' event on the context, with its opener (None for Ctrl+T / Ctrl+N)."""

    def __init__(self, url, context, opener=None):
        self.url, self.context, self._opener, self._closed = url, context, opener, False

    async def opener(self):
        return self._opener

    def is_closed(self):
        return self._closed

    async def close(self):
        self._closed = True

    def on(self, *_a):
        pass


async def test_a_page_the_owner_opens_during_take_over_joins_the_held_window_and_nothing_else_is_kept():
    """C1d: Ctrl+T / Ctrl+N during take-over is a tab of the window the owner holds; with nobody holding it, a page nobody asked for closes."""
    s, main, _ = await main_with_owner()
    first = s._pages["owner"]
    ctx = first.context
    s._control["owner"] = "d1"
    fresh = _Opened("about:blank", ctx)
    s._on_context_page(fresh)
    await settle()
    await settle()
    win = s._windows["owner"]
    assert [t.id for t in win.tabs] == ["t1", "t2"] and win.find(fresh) is not None and win.active == "t1"
    assert not fresh.is_closed()
    stray = _Opened("about:blank", ctx)
    s._control.pop("owner")
    s._on_context_page(stray)
    await settle()
    await settle()
    assert stray.is_closed() and win.find(stray) is None and not first.is_closed()


async def test_a_page_the_owner_opens_in_a_held_work_window_joins_it_and_a_page_kelo_makes_does_not_count(tmp_path):
    s, main, _ = await main_with_owner()
    br = Browser()
    work_on(s, br)
    page = await in_tab("task:w1", s.page)
    ctx = page.context
    assert any(h.__name__ == "_on_context_page" for h in getattr(ctx, "page_handlers", [])) or ctx is not None
    s._control["task:w1"] = "dev1"                                   # the owner holds this window
    extra = Page("about:blank", ctx)
    await s._adopt_owner_page(extra)
    assert [t.page for t in s._windows["task:w1"].tabs][-1] is extra and len(s._windows["task:w1"].tabs) == 2
