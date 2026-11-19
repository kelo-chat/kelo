"""The work browser (tools/work_browser.py): scheduled and background tabs run in a second browser, each in a context of its own, with a
copy of the owner's cookies when it opens and the sign-ins the job made copied back when it closes. Fakes only: no browser, no network."""

import logging
from types import SimpleNamespace

import pytest

from assistant.tools.browser import BrowserPolicyError, BrowserSession, browser_owner
from assistant.tools import work_browser
from assistant.tools.work_browser import DOWN_NOTE, WorkBrowser, work_tab

COOKIE = {"name": "sid", "value": "s3cret", "domain": ".shop.example.com", "path": "/", "expires": -1, "httpOnly": True,
          "secure": True, "sameSite": "Lax", "partitionKey": "https://top.example"}
OTHER = {"name": "other", "value": "x", "domain": "news.example.org", "path": "/", "extra": "dropped"}


class Ctx:
    """A browser context: cookies (replaced by name, domain and path, as Chrome does), init scripts and pages."""

    def __init__(self, cookies=None, cid=""):
        self._cookies = [dict(c) for c in cookies or []]
        self.added, self.scripts, self.pages, self.closed, self.cid = [], [], [], False, cid
        self.routes = []

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

    async def route(self, pattern, handler):
        self.routes.append(handler)

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
        main.pages.append(p)
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


async def test_a_work_tab_starts_with_a_copy_of_the_owners_cookies(monkeypatch):
    monkeypatch.setattr(work_browser, "SEED_PER_SITE", False)   # the whole-jar copy at open (the cold-start fallback, card A2)
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
    assert "task:w1" in caplog.text and "written back" in caplog.text
    assert "NEW" not in caplog.text and "shop.example.com" not in caplog.text   # counts only, never a cookie or a site


async def test_a_cookie_the_job_changed_is_written_back_even_while_a_main_tab_shows_the_site():
    """Card A4: the write-back is per value (compare-and-set), not refused because a main tab shows the site."""
    s, main, made = await main_with_owner([dict(COOKIE)])
    made[0].url = "https://shop.example.com/"
    work_on(s, Browser())
    await in_tab("task:w1", s.page)
    await in_tab("task:w1", lambda: s._seed_work_site("task:w1", "shop.example.com"))
    ctx = s.work.tab("task:w1").context
    await ctx.add_cookies([{"name": "sid", "value": "NEW", "domain": ".shop.example.com", "path": "/"}])
    await s.close_tab("task:w1")
    assert ("sid", "NEW") in {(c["name"], c["value"]) for c in await main.cookies()}


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
    await in_tab("schedule:3", lambda: s._seed_work_site("schedule:3", "shop.example.com"))
    await in_tab("schedule:3", lambda: s._seed_work_site("schedule:3", "shop.example.com"))
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


async def test_a_work_tab_starts_empty_and_a_site_is_seeded_once():
    """Per-site seeding (card A2): the owner's cookies are not copied at open; a site's sign-in is copied in once, when asked."""
    assert work_browser.SEED_PER_SITE is True
    s, main, _ = await main_with_owner([dict(COOKIE), dict(OTHER)])
    work_on(s, Browser())
    ctx = (await in_tab("schedule:3", s.page)).context
    assert ctx.added == []                                             # nothing copied at open
    wt = s.work.tab("schedule:3")
    assert await s.work.seed_site("schedule:3", "shop.example.com", [dict(COOKIE), dict(OTHER)], {}) == 1
    assert await s.work.seed_site("schedule:3", "shop.example.com", [dict(COOKIE)], {}) == 0   # done for this site
    assert [c["name"] for c in ctx.added] == ["sid"] and main.added == []
    assert ("example.com" in wt.seeded) and wt.seeded["example.com"][("sid", ".shop.example.com", "/")] == "s3cret"


async def test_seed_copies_only_that_sites_cookies():
    browser = Browser()
    work = WorkBrowser("work-browser", 9225, connect=connect_to(browser))
    await work.open_context("task:w1", [])
    n = await work.seed_site("task:w1", "news.example.org", [dict(COOKIE), dict(OTHER)], {})
    assert n == 1 and [c["name"] for c in work.tab("task:w1").context.added] == ["other"]


async def test_seeded_storage_is_set_for_each_origin_once():
    browser = Browser()
    work = WorkBrowser("work-browser", 9225, connect=connect_to(browser))
    ctx = await work.open_context("task:w1", [])
    await work.seed_site("task:w1", "shop.example.com", [], {"https://shop.example.com": {"cart": "2"}})
    await work.seed_site("task:w1", "shop.example.com", [], {"https://shop.example.com": {"cart": "3"}})
    assert len(ctx.scripts) == 1 and '"cart"' in ctx.scripts[0]


async def test_a_signed_out_tab_is_never_seeded():
    browser = Browser()
    work = WorkBrowser("work-browser", 9225, connect=connect_to(browser))
    ctx = await work.open_context("task:r1", [dict(COOKIE)], signed_out=True)
    assert ctx.added == [] and await work.seed_site("task:r1", "shop.example.com", [dict(COOKIE)], {"https://shop.example.com": {"a": "b"}}) == 0
    assert ctx.added == [] and ctx.scripts == []


async def test_note_touched_records_the_registrable_site():
    browser = Browser()
    work = WorkBrowser("work-browser", 9225, connect=connect_to(browser))
    await work.open_context("task:w1", [])
    work.note_touched("task:w1", "shop.example.com")
    work.note_touched("task:w1", "")
    assert work.tab("task:w1").touched == {"example.com"}   # the registrable site


class Req:
    def __init__(self, url, resource_type="document"):
        self.url, self.resource_type = url, resource_type


class Route:
    def __init__(self, ctx):
        self.ctx, self.fell = ctx, False

    async def fallback(self):
        self.fell = True


async def test_open_seeds_the_site_before_loading_it(monkeypatch):
    """Card A3: a work tab's first document request to a site is made with that site's sign-in (seeded before goto)."""
    s, main, made = await main_with_owner([dict(COOKIE), dict(OTHER)])
    work_on(s, Browser())
    seen = []
    real_goto = Page.goto

    async def goto(self, url, **kw):
        seen.append(len(self.context.added))
        return await real_goto(self, url, **kw)

    monkeypatch.setattr(Page, "goto", goto)
    s._resolver = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))]   # the public address of the test site
    await in_tab("schedule:3", lambda: s.open("https://shop.example.com/"))
    ctx = s.work.tab("schedule:3").context
    assert [c["name"] for c in ctx.added] == ["sid"] and seen and seen[0] == 1


async def test_storage_is_read_from_a_blank_main_page_and_no_page_is_opened():
    s, main, made = await main_with_owner()
    made[0].url = "about:blank"
    made[0].storage = [["cart", "2"]]
    work_on(s, Browser())
    ctx = (await in_tab("schedule:3", s.page)).context
    before = len(made)
    await in_tab("schedule:3", lambda: s._seed_work_site("schedule:3", "shop.example.com"))
    assert len(made) == before and len(ctx.scripts) == 0       # a blank page has no storage of that site: nothing copied, none opened


async def test_a_seed_failure_says_so_on_the_next_reading():
    from assistant.tools.work_browser import SEED_FAILED_NOTE

    s, main, _ = await main_with_owner()
    work = work_on(s, Browser())
    await in_tab("task:w1", s.page)

    async def broken(*a, **k):
        raise OSError("gone")

    work.seed_site = broken
    await in_tab("task:w1", lambda: s._seed_work_site("task:w1", "shop.example.com"))
    assert SEED_FAILED_NOTE in s._notes.get("task:w1", [])


async def test_a_navigation_marks_the_site_touched():
    s, main, _ = await main_with_owner()
    work = work_on(s, Browser())
    page = await in_tab("task:w1", s.page)
    assert "framenavigated" in page.handlers
    page.handlers["framenavigated"](SimpleNamespace(url="https://shop.example.com/cart", parent_frame=None))
    assert work.tab("task:w1").touched == {"example.com"}


async def test_a_clicked_document_request_is_seeded_first():
    """The route hook: a document request the tab makes later (a click, a popup, a redirect) gets the site's sign-in before it is sent."""
    s, main, _ = await main_with_owner([dict(COOKIE)])
    work_on(s, Browser())
    ctx = (await in_tab("task:w1", s.page)).context
    await in_tab("task:w1", s.page)
    assert ctx.routes, "the context has a route hook"
    route = Route(ctx)
    await ctx.routes[0](route, Req("https://shop.example.com/item/9"))
    assert route.fell and [c["name"] for c in ctx.added] == ["sid"]
    other = Route(ctx)
    await ctx.routes[0](other, Req("https://shop.example.com/logo.png", "image"))
    assert other.fell and len(ctx.added) == 1


async def _job_tab(s, main, name="task:w1"):
    work_on(s, Browser())
    await in_tab(name, s.page)                                   # the tab's window first, then its site is seeded
    await in_tab(name, lambda: s._seed_work_site(name, "shop.example.com"))
    return s.work.tab(name).context


async def test_changed_cookies_of_a_touched_site_go_back_when_the_owner_left_them():
    s, main, made = await main_with_owner([dict(COOKIE)])
    ctx = await _job_tab(s, main)
    await ctx.add_cookies([{"name": "sid", "value": "JOB", "domain": ".shop.example.com", "path": "/"}])
    await s.close_tab("task:w1")
    assert ("sid", "JOB") in {(c["name"], c["value"]) for c in await main.cookies()}


async def test_a_cookie_the_owner_changed_meanwhile_is_kept(caplog):
    caplog.set_level(logging.INFO, logger="assistant.browser")
    s, main, made = await main_with_owner([dict(COOKIE)])
    ctx = await _job_tab(s, main)
    await ctx.add_cookies([{"name": "sid", "value": "JOB", "domain": ".shop.example.com", "path": "/"}])
    await main.add_cookies([{"name": "sid", "value": "OWNER", "domain": ".shop.example.com", "path": "/"}])
    await s.close_tab("task:w1")
    assert ("sid", "OWNER") in {(c["name"], c["value"]) for c in await main.cookies()}
    assert "1 left" in caplog.text and "JOB" not in caplog.text and "OWNER" not in caplog.text


async def test_a_cookie_the_job_removed_stays_for_the_owner():
    s, main, made = await main_with_owner([dict(COOKIE)])
    ctx = await _job_tab(s, main)
    await ctx.clear_cookies()
    await s.close_tab("task:w1")
    assert ("sid", "s3cret") in {(c["name"], c["value"]) for c in await main.cookies()}


async def test_a_new_cookie_of_a_touched_site_is_added():
    s, main, made = await main_with_owner()
    ctx = await _job_tab(s, main)
    await ctx.add_cookies([{"name": "fresh", "value": "1", "domain": ".shop.example.com", "path": "/"}])
    await s.close_tab("task:w1")
    assert ("fresh", "1") in {(c["name"], c["value"]) for c in await main.cookies()}


async def test_a_site_the_job_never_touched_is_not_written():
    s, main, made = await main_with_owner([dict(COOKIE), dict(OTHER)])
    ctx = await _job_tab(s, main)
    await ctx.add_cookies([{"name": "other", "value": "JOB", "domain": "news.example.org", "path": "/"}])
    await s.close_tab("task:w1")
    assert ("other", "x") in {(c["name"], c["value"]) for c in await main.cookies()}


async def test_two_jobs_on_one_site_the_first_to_close_wins():
    s, main, made = await main_with_owner([dict(COOKIE)])
    work_on(s, Browser())
    for k in ("task:a", "task:b"):
        await in_tab(k, s.page)
        await in_tab(k, lambda k=k: s._seed_work_site(k, "shop.example.com"))
    await s.work.tab("task:a").context.add_cookies([{"name": "sid", "value": "A", "domain": ".shop.example.com", "path": "/"}])
    await s.work.tab("task:b").context.add_cookies([{"name": "sid", "value": "B", "domain": ".shop.example.com", "path": "/"}])
    await s.close_tab("task:a")
    await s.close_tab("task:b")
    assert ("sid", "A") in {(c["name"], c["value"]) for c in await main.cookies()}


async def test_storage_changes_follow_the_same_rule():
    s, main, made = await main_with_owner()
    cdp = BrowserCdp()

    async def get_cdp(page):
        return cdp

    s._cdp = get_cdp
    work_on(s, Browser())
    made[0].url = "https://shop.example.com/"
    made[0].storage = [["cart", "2"]]
    page = (await in_tab("task:w1", s.page))
    await in_tab("task:w1", lambda: s._seed_work_site("task:w1", "shop.example.com"))
    page.url = "https://shop.example.com/cart"
    page.storage = [["cart", "3"]]
    await s.close_tab("task:w1")
    sent = [(m, p) for m, p in cdp.sent if m == "DOMStorage.setDOMStorageItem"]
    assert sent and sent[-1][1]["key"] == "cart" and sent[-1][1]["value"] == "3"


async def test_the_owner_changed_a_storage_key_meanwhile_it_is_kept():
    s, main, made = await main_with_owner()
    cdp = BrowserCdp()

    async def get_cdp(page):
        return cdp

    s._cdp = get_cdp
    work_on(s, Browser())
    made[0].url = "https://shop.example.com/"
    made[0].storage = [["cart", "2"]]
    page = (await in_tab("task:w1", s.page))
    await in_tab("task:w1", lambda: s._seed_work_site("task:w1", "shop.example.com"))
    page.url = "https://shop.example.com/cart"
    page.storage = [["cart", "3"]]
    made[0].storage = [["cart", "OWNER"]]
    await s.close_tab("task:w1")
    assert not [p for m, p in cdp.sent if m == "DOMStorage.setDOMStorageItem"]


async def test_a_research_job_tab_starts_signed_out_and_writes_nothing_back():
    s, main, _ = await main_with_owner([dict(COOKIE)])
    work_on(s, Browser())
    s.signed_out_tab = lambda key: key == "task:r1"   # set by server.py for the tabs of research jobs
    ctx = (await in_tab("task:r1", s.page)).context
    assert await ctx.cookies() == []
    await ctx.add_cookies([{"name": "sid", "value": "JOB", "domain": ".shop.example.com", "path": "/"}])
    await s.close_tab("task:r1")
    assert [c["value"] for c in await main.cookies() if c["name"] == "sid"] == ["s3cret"]
    s2, main2, _ = await main_with_owner([dict(COOKIE)])
    work_on(s2, Browser())
    ctx2 = (await in_tab("task:w2", s2.page)).context   # a schedule or a browser job gets the owner's sign-in for a site it goes to
    await in_tab("task:w2", lambda: s2._seed_work_site("task:w2", "shop.example.com"))
    assert ("sid", "s3cret") in {(c["name"], c["value"]) for c in await ctx2.cookies()}


async def test_a_job_is_told_once_when_the_owners_browser_holds_nothing_for_the_site():
    s, main, _ = await main_with_owner([])
    work_on(s, Browser())
    await in_tab("task:w1", s.page)
    await in_tab("task:w1", lambda: s._seed_work_site("task:w1", "shop.example.com"))
    note = s._seed_note("task:w1", "https://shop.example.com/cart")
    assert note.startswith("[Not signed in to example.com:") and "holds nothing" in note and "request_login" in note
    assert s._seed_note("task:w1", "https://www.example.com/") == ""          # once per site and tab


async def test_cookies_without_a_sign_in_record_name_the_saved_login():
    s, main, _ = await main_with_owner([dict(COOKIE)])
    work_on(s, Browser())
    s.signed_in_site = lambda site: False
    s.login_for = lambda site: "shop"
    await in_tab("task:w1", s.page)
    await in_tab("task:w1", lambda: s._seed_work_site("task:w1", "shop.example.com"))
    note = s._seed_note("task:w1", "https://shop.example.com/")
    assert "no record of a sign-in" in note and "browser_login(name='shop', via=" in note
    assert COOKIE["name"] not in note and COOKIE["value"] not in note


async def test_a_signed_in_site_a_signed_out_tab_and_a_chat_tab_get_no_note():
    s, main, _ = await main_with_owner([dict(COOKIE)])
    work_on(s, Browser())
    s.signed_in_site = lambda site: True
    await in_tab("task:w1", s.page)
    await in_tab("task:w1", lambda: s._seed_work_site("task:w1", "shop.example.com"))
    assert s._seed_note("task:w1", "https://shop.example.com/") == ""
    s.signed_out_tab = lambda key: key == "task:w2"
    await in_tab("task:w2", s.page)
    await in_tab("task:w2", lambda: s._seed_work_site("task:w2", "shop.example.com"))
    assert s._seed_note("task:w2", "https://shop.example.com/") == ""
    assert s._seed_note("owner", "https://shop.example.com/") == ""


async def test_the_note_comes_with_the_first_reading_of_the_site():
    s, main, _ = await main_with_owner([])
    work_on(s, Browser())
    s._resolver = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))]
    out = await in_tab("task:w1", lambda: s.open("https://shop.example.com/"))
    assert "[Not signed in to example.com:" in str(out)
