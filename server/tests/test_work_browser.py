"""The work browser (tools/work_browser.py): scheduled and background tabs run in a second browser, each in a context of its own, with a
copy of the owner's cookies when it opens and the sign-ins the job made copied back when it closes. Fakes only: no browser, no network."""

import logging
from types import SimpleNamespace

import pytest

from assistant.tools.browser import BrowserPolicyError, BrowserSession, browser_owner
from assistant.tools.work_browser import DOWN_NOTE, WorkBrowser, work_tab

COOKIE = {"name": "sid", "value": "s3cret", "domain": ".shop.example.com", "path": "/", "expires": -1, "httpOnly": True,
          "secure": True, "sameSite": "Lax", "partitionKey": "https://top.example"}
OTHER = {"name": "other", "value": "x", "domain": "news.example.org", "path": "/", "extra": "dropped"}


class Ctx:
    """A browser context: cookies (replaced by name, domain and path, as Chrome does), init scripts and pages."""

    def __init__(self, cookies=None, cid=""):
        self._cookies = [dict(c) for c in cookies or []]
        self.added, self.scripts, self.pages, self.closed, self.cid = [], [], [], False, cid

    async def cookies(self):
        return [dict(c) for c in self._cookies]

    async def add_cookies(self, cookies):
        for c in cookies:
            self.added.append(dict(c))
            self._cookies = [x for x in self._cookies if (x["name"], x["domain"], x.get("path")) != (c["name"], c["domain"], c.get("path"))]
            self._cookies.append(dict(c))

    async def clear_cookies(self, **kw):
        self._cookies = []

    async def add_init_script(self, script=None):
        self.scripts.append(script)

    async def new_page(self):
        p = Page("about:blank", self)
        self.pages.append(p)
        return p

    async def close(self):
        self.closed = True


class Page:
    def __init__(self, url, context, storage=None):
        self.url, self.context, self.storage, self._closed, self.handlers = url, context, storage or [], False, {}

    def is_closed(self):
        return self._closed

    async def title(self):
        return "Title"

    def on(self, ev, fn):
        self.handlers[ev] = fn

    async def evaluate(self, js, arg=None):
        return self.storage

    async def goto(self, url, **kw):
        self.url = url

    async def close(self):
        self._closed = True


class BrowserCdp:
    def __init__(self):
        self.sent, self.handlers = [], {}

    def on(self, ev, fn):
        self.handlers[ev] = fn

    async def send(self, method, params=None):
        self.sent.append((method, params or {}))
        return {}


class Browser:
    """The work browser as Playwright sees a connected Chrome: its default context and the contexts made since."""

    def __init__(self, name="work"):
        self.name = name
        self.contexts = [Ctx(cid=f"{name}-default")]
        self.connected, self.made, self.cdp = True, 0, BrowserCdp()

    def is_connected(self):
        return self.connected

    async def new_context(self, **kw):
        self.made += 1
        c = Ctx(cid=f"{self.name}-ctx-{self.made}")
        self.contexts.append(c)
        return c

    async def new_browser_cdp_session(self):
        return self.cdp


def connect_to(*browsers):
    queue = list(browsers)

    async def connect():
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return connect


async def main_with_owner(cookies=()):
    """A session whose owner tab is open in the main browser (its context holds `cookies`)."""
    main = Ctx(cookies, cid="main")
    made = []

    async def connect():
        p = Page("about:blank", main)
        made.append(p)
        return p

    s = BrowserSession(connect=connect)
    await in_tab("owner", s.page)
    return s, main, made


async def in_tab(key, make):
    token = browser_owner.set(key)
    try:
        return await make()
    finally:
        browser_owner.reset(token)


def work_on(s, browser):
    s.work = WorkBrowser("work-browser", 9225, connect=connect_to(browser))
    return s.work


async def test_schedule_and_job_tabs_open_in_the_work_browser_and_chat_tabs_do_not():
    assert all(work_tab(k) for k in ("schedule:3", "task:w1", "task:checklist", "task:watch-7", "task:watch-site:shop.example.com",
                                     "task:ab12cd34"))
    assert not any(work_tab(k) for k in ("owner", "16", "buy:w1"))
    s, main, _ = await main_with_owner()
    browser = Browser()
    work_on(s, browser)
    for key in ("owner", "16", "buy:w1"):
        assert (await in_tab(key, s.page)).context is main
    contexts = [(await in_tab(k, s.page)).context for k in ("schedule:3", "task:w1", "task:checklist", "task:watch-7",
                                                            "task:watch-site:shop.example.com", "task:ab12cd34")]
    assert all(c is not main and c in browser.contexts for c in contexts)
    assert len({id(c) for c in contexts}) == 6   # one context per work tab


async def test_a_work_tab_starts_with_a_copy_of_the_owners_cookies():
    s, main, _ = await main_with_owner([dict(COOKIE), dict(OTHER)])
    work_on(s, Browser())
    ctx = (await in_tab("schedule:3", s.page)).context
    assert ctx.added == [{k: COOKIE[k] for k in COOKIE}, {"name": "other", "value": "x", "domain": "news.example.org", "path": "/"}]
    assert main.added == []   # the copy is one way: the owner's context is not changed


async def test_closing_a_work_tab_closes_its_context_and_keeps_nothing():
    s, main, _ = await main_with_owner()
    browser = Browser()
    work = work_on(s, browser)
    ctx = (await in_tab("schedule:3", s.page)).context
    await s.close_tab("schedule:3")
    assert ctx.closed and work.tab("schedule:3") is None
    assert main.added == [] and not s.sign_ins


async def test_a_sign_in_the_job_made_is_carried_back_when_its_tab_closes(caplog):
    caplog.set_level(logging.INFO, logger="assistant.browser")
    s, main, _ = await main_with_owner()
    work_on(s, Browser())
    ctx = (await in_tab("task:w1", s.page)).context
    await ctx.add_cookies([{"name": "sid", "value": "NEW", "domain": ".shop.example.com", "path": "/"},
                           {"name": "news", "value": "n", "domain": "news.example.org", "path": "/"}])
    s.sign_ins["task:w1"] = {"site": "shop.example.com", "at": 0.0, "task": "t"}
    await s.close_tab("task:w1")
    got = {(c["name"], c["value"]) for c in await main.cookies()}
    assert ("sid", "NEW") in got and ("news", "n") not in got
    assert "task:w1" in caplog.text and "carried back" in caplog.text
    assert "NEW" not in caplog.text and "shop.example.com" not in caplog.text   # counts only, never a cookie or a site


async def test_no_carry_back_while_a_main_tab_shows_that_site():
    s, main, made = await main_with_owner()
    made[0].url = "https://shop.example.com/"
    work_on(s, Browser())
    ctx = (await in_tab("task:w1", s.page)).context
    await ctx.add_cookies([{"name": "sid", "value": "NEW", "domain": ".shop.example.com", "path": "/"}])
    s.sign_ins["task:w1"] = {"site": "shop.example.com", "at": 0.0, "task": "t"}
    await s.close_tab("task:w1")
    assert main.added == []


async def test_the_owners_hands_on_a_work_tab_mark_that_site_signed():
    s, main, made = await main_with_owner()
    work_on(s, Browser())
    ctx = (await in_tab("task:w1", s.page)).context
    made_page = ctx.pages[0]
    made_page.url = "https://shop.example.com/account"
    await ctx.add_cookies([{"name": "sid", "value": "BY-HAND", "domain": ".shop.example.com", "path": "/"}])
    s.owner_handed_back("task:w1", "signed in to the shop", "owner", "browser")   # what handback_listeners calls
    await s.close_tab("task:w1")
    assert ("sid", "BY-HAND") in {(c["name"], c["value"]) for c in await main.cookies()}


async def test_cookies_a_job_changes_never_reach_the_owner_while_it_runs():
    s, main, _ = await main_with_owner([dict(COOKIE)])
    work_on(s, Browser())
    ctx = (await in_tab("task:w1", s.page)).context
    await ctx.add_cookies([{"name": "sid", "value": "job", "domain": ".shop.example.com", "path": "/"}])
    assert ("sid", "s3cret") in {(c["name"], c["value"]) for c in await main.cookies()}
    assert main.added == []
    assert [c["value"] for c in await main.cookies() if c["name"] == "sid"] == ["s3cret"]


async def test_the_work_browser_down_gives_the_down_note_and_never_uses_the_main_browser():
    s, main, made = await main_with_owner([dict(COOKIE)])
    before = len(made)

    async def down():
        raise OSError("connection refused")

    s.work = WorkBrowser("work-browser", 9225, connect=down)
    with pytest.raises(BrowserPolicyError) as e:
        await in_tab("schedule:3", s.page)
    assert str(e.value) == DOWN_NOTE and "work browser" in str(e.value)
    assert len(made) == before and s.work.tab("schedule:3") is None


async def test_without_a_work_browser_every_tab_is_in_the_main_browser_as_before():
    s, main, made = await main_with_owner()
    assert s.work is None
    assert (await in_tab("schedule:3", s.page)).context is main and len(made) == 2


async def test_a_new_connection_closes_leftover_contexts_and_forgets_old_work_pages():
    s, main, _ = await main_with_owner()
    first, second = Browser("w1"), Browser("w2")
    leftover = Ctx(cid="leftover")
    first.contexts.append(leftover)
    s.work = WorkBrowser("work-browser", 9225, connect=connect_to(first, second))
    p1 = await in_tab("schedule:3", s.page)
    assert leftover.closed and s.work.generation == 1
    first.connected = False
    p2 = await in_tab("schedule:3", s.page)
    assert s.work.generation == 2 and p2 is not p1 and p2.context in second.contexts


async def test_the_main_browser_restarting_forgets_only_main_tabs():
    s, main, _ = await main_with_owner()
    work_on(s, Browser())
    await in_tab("schedule:3", s.page)
    s._forget_main_tabs()
    assert "owner" not in s._pages and "schedule:3" in s._pages


async def test_local_storage_of_a_site_is_copied_once_when_a_work_tab_opens_it_without_opening_a_main_page():
    s, main, made = await main_with_owner()
    made[0].url = "https://shop.example.com/"
    made[0].storage = [["cart", "2"]]
    work_on(s, Browser())
    ctx = (await in_tab("schedule:3", s.page)).context
    await in_tab("schedule:3", lambda: s._copy_work_storage("shop.example.com"))
    await in_tab("schedule:3", lambda: s._copy_work_storage("shop.example.com"))
    assert len(ctx.scripts) == 1 and '"cart"' in ctx.scripts[0]
    assert len(made) == 1   # only the owner's tab: no page was opened to read the storage


async def test_forget_site_also_clears_open_work_contexts():
    s, main, _ = await main_with_owner([dict(COOKIE)])
    work_on(s, Browser())
    ctx = (await in_tab("task:w1", s.page)).context
    await s.work.forget("shop.example.com")
    assert [c for c in await ctx.cookies() if "example.com" in c["domain"]] == []
    assert [c for c in await main.cookies() if "example.com" in c["domain"]] != []


async def test_private_on_a_work_tab_stays_in_the_work_browser():
    s, main, _ = await main_with_owner()
    browser = Browser()
    work_on(s, browser)
    await in_tab("schedule:3", lambda: s.set_private(True))
    priv = s._private["schedule:3"]
    assert priv in browser.contexts and priv is not main
    assert (await in_tab("schedule:3", s.page)).context is priv


async def test_a_work_tab_download_uses_the_work_browsers_own_session_and_its_context_id():
    s, main, _ = await main_with_owner()
    browser = Browser()
    work_on(s, browser)
    await in_tab("schedule:3", s.page)
    s.downloads = SimpleNamespace(browser_dir="/downloads")
    s._targets["schedule:3"] = ("frame-1", "ctx-x")
    await in_tab("schedule:3", lambda: s._route_downloads("schedule:3"))
    method, params = browser.cdp.sent[-1]
    assert method == "Browser.setDownloadBehavior" and params["browserContextId"] == "ctx-x" and params["downloadPath"] == "/downloads"
    assert s._dl_cdp is None   # the main browser's session is not used


async def test_tab_list_says_which_browser_each_tab_is_in():
    s, main, _ = await main_with_owner()
    work_on(s, Browser())
    await in_tab("schedule:3", s.page)
    by_key = {t["key"]: t["browser"] for t in await s.tabs()}
    assert by_key == {"owner": "main", "schedule:3": "work"}


async def test_without_a_work_browser_the_tab_list_says_main():
    s, main, _ = await main_with_owner()
    await in_tab("schedule:3", s.page)
    assert {t["key"]: t["browser"] for t in await s.tabs()} == {"owner": "main", "schedule:3": "main"}
