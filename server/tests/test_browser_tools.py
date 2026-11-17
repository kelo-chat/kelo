import socket

import pytest

from assistant.agent.fence import plain
from assistant.tools.browser import BrowserPolicyError, BrowserSession, browser_tools, check_url


def fake_resolver(mapping):
    def resolve(host, port):
        if host not in mapping:
            raise OSError("nxdomain")
        return [(2, 1, 6, "", (mapping[host], port))]
    return resolve


PUBLIC = fake_resolver({"example.com": "93.184.216.34", "evil.test": "10.0.0.5", "meta.test": "169.254.169.254",
                        "lan.test": "192.168.0.1", "loop.test": "127.0.0.1", "cgnat.test": "100.64.0.1"})


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi", "chrome://settings", "ftp://example.com", "example.com", "http://",
])
async def test_non_http_urls_rejected(url):
    with pytest.raises(BrowserPolicyError):
        await check_url(url, resolver=PUBLIC)


@pytest.mark.parametrize("url", [
    "http://localhost:8080", "http://host.docker.internal/", "http://server:8080/", "http://browser:9223/json",
    "http://127.0.0.1/", "http://[::1]/", "http://192.168.1.50/", "http://10.1.2.3/", "http://169.254.169.254/latest/meta-data",
    "http://evil.test/", "http://meta.test/", "http://lan.test/", "http://loop.test/", "http://cgnat.test/", "http://nonexistent.test/",
])
async def test_internal_and_private_targets_rejected(url):
    with pytest.raises(BrowserPolicyError):
        await check_url(url, resolver=PUBLIC, internal_names=frozenset({"server", "browser"}))


async def test_public_urls_allowed_and_private_override():
    await check_url("https://example.com/path?q=1", resolver=PUBLIC)
    await check_url("http://127.0.0.1:3000/", allow_private=True, resolver=PUBLIC)  # explicit opt-in only


class FakePage:
    def __init__(self):
        self.url, self.calls, self._closed = "about:blank", [], False
        self.next_url, self.text = None, "Hello world"

    def is_closed(self): return self._closed
    async def title(self): return "Title"
    async def evaluate(self, js): return self.text
    async def goto(self, url, **kw):
        self.calls.append(("goto", url))
        self.url = self.next_url or url
    async def click(self, sel, **kw): self.calls.append(("click", sel))
    async def fill(self, sel, text, **kw): self.calls.append(("fill", sel, text))
    async def press(self, sel, key): self.calls.append(("press", sel, key))
    async def wait_for_load_state(self, *a, **kw): pass


def session_with(page):
    async def connect(): return page
    return BrowserSession(connect=connect, resolver=PUBLIC)


async def test_open_returns_title_url_and_text():
    page = FakePage()
    out = await session_with(page).open("https://example.com")
    assert plain(out).startswith("URL: https://example.com\nTitle: Title\n\nHello world")
    assert ("goto", "https://example.com") in page.calls


async def test_open_blocks_before_navigating():
    page = FakePage()
    with pytest.raises(BrowserPolicyError):
        await session_with(page).open("http://lan.test/")
    assert page.calls == []


async def test_redirect_into_private_network_is_caught_and_page_reset():
    page = FakePage()
    page.next_url = "http://192.168.0.1/admin"
    with pytest.raises(BrowserPolicyError):
        await session_with(page).open("https://example.com")
    assert page.calls[-1] == ("goto", "about:blank")


async def test_long_text_truncated():
    page = FakePage()
    page.text = "x" * 20_000
    out = await session_with(page).snapshot()
    assert "page text truncated, 8000 more chars" in out


async def test_tool_flags_untrusted_and_only_committing_steps_need_approval():
    tools = {t.name: t for t in browser_tools(session_with(FakePage()))}
    assert set(tools) == {"browser_open", "browser_read", "browser_view", "browser_click", "browser_type"}
    assert all(t.untrusted for t in tools.values())
    assert not tools["browser_open"].requires_approval and not tools["browser_read"].requires_approval
    assert not tools["browser_view"].requires_approval   # switching desktop / phone view only looks
    assert tools["browser_click"].requires_approval and tools["browser_type"].requires_approval
    click, type_ = tools["browser_click"].auto_approve, tools["browser_type"].auto_approve
    # the default: looking things up goes through without a card...
    assert await click({"selector": "a#next"}) and await click({"selector": "text=상하이 디즈니랜드 입장권"})
    assert await click({"selector": "text=82,524"}) and await click({"selector": "text=예약 가능 날짜 보기"})
    assert await type_({"selector": "#q", "text": "shanghai disneyland"})
    # ...but a step that commits something in the user's name still gets one
    assert await click({"selector": "text=결제하기"}) and await click({"selector": "button:has-text('Place order')"})   # words decide no payment
    assert not await click({"selector": "text=Send"}) and not await click({"selector": "text=회원가입"})
    assert await type_({"selector": "input[name=email]", "text": "x"})   # a field's name decides nothing; its value and type do
    assert await type_({"selector": "#q", "text": "me@example.com"})              # an e-mail address typed asks nothing (ask-less)
    assert not await type_({"selector": "#q", "text": "4111 1111 1111 1111"})     # a card number by value still asks
    # the owner can still have every click and typing step ask
    strict = {t.name: t for t in browser_tools(session_with(FakePage()), approve_actions=True)}
    assert not await strict["browser_click"].auto_approve({"selector": "a#next"})
    assert not await strict["browser_type"].auto_approve({"selector": "#q", "text": "x"})


async def test_the_card_says_what_kind_of_commit_it_is():
    from assistant.tools.browser import browser_owner

    page = FakePage()
    page.url = "https://shop.example/item"
    sess = session_with(page)
    await sess.page()
    tools = {t.name: t for t in browser_tools(sess)}
    token = browser_owner.set("owner")
    try:
        assert not await tools["browser_click"].auto_approve({"selector": "text=Delete my post"})
        assert "delete or cancel" in await tools["browser_click"].describe({"selector": "text=Delete my post"})
        assert await tools["browser_click"].auto_approve({"selector": "text=Reviews"})
        assert "This may" not in await tools["browser_click"].describe({"selector": "text=Reviews"})
    finally:
        browser_owner.reset(token)


async def test_an_allowed_purchase_covers_the_committing_steps_that_finish_it_but_not_looking_around():
    from assistant.tools.browser import browser_owner

    page = FakePage()
    page.url = "https://shop.example/checkout"
    sess = session_with(page)
    await sess.page()
    tools = {t.name: t for t in browser_tools(sess)}
    sess.allow_purchase("shop.example", actions=1)
    assert await tools["browser_click"].auto_approve({"selector": "text=Details"})   # looking: no card, and the window is not used up
    assert await tools["browser_click"].auto_approve({"selector": "text=Send"})      # the one allowed committing step
    assert not await tools["browser_click"].auto_approve({"selector": "text=Send"})
    assert browser_owner.get() == "owner"


async def test_type_with_submit_fills_then_presses_enter():
    page = FakePage()
    page.url = "https://example.com/search"
    await session_with(page).type("input[name=q]", "hello", submit=True)
    assert page.calls == [("fill", "input[name=q]", "hello"), ("press", "input[name=q]", "Enter")]


async def test_each_device_gets_its_own_tab_and_unused_tabs_are_closed_without_limiting_how_many_devices_there_are():
    from assistant.tools import browser as b

    class Tab:
        def __init__(self):
            self.closed = False

        def is_closed(self):
            return self.closed

        async def close(self):
            self.closed = True

    made = []

    async def connect():
        made.append(Tab())
        return made[-1]

    s = BrowserSession(connect=connect, resolver=PUBLIC)
    seen = {}
    for who in ["alice-phone", "bob-phone", "alice-phone"] + [f"phone{i}" for i in range(50)]:
        token = b.browser_owner.set(who)
        seen.setdefault(who, []).append(await s.page())
        b.browser_owner.reset(token)
    assert seen["alice-phone"][0] is seen["alice-phone"][1] and seen["alice-phone"][0] is not seen["bob-phone"][0]
    assert len(made) == 52                                   # 52 devices, 52 tabs: nothing caps the number
    for key in s._used:
        s._used[key] -= b.CHAT_TAB_IDLE_SECONDS + 1          # everyone has been away a long time
    token = b.browser_owner.set("alice-phone")
    await s.page()
    b.browser_owner.reset(token)
    # using one tab never closes the others any more (the server's sweeper does that, see test_tab_lifecycle.py); it only says how long
    assert sum(t.closed for t in made) == 0 and len(s.open_keys()) == 52
    assert s.idle_for("alice-phone") < 5 and s.idle_for("bob-phone") > b.CHAT_TAB_IDLE_SECONDS
