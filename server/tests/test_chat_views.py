"""The browser tabs and the phone the chat shows (chat_views.py): a card in a reply makes a view that stays until the main agent closes it
(chat_view tool), the owner closes it (DELETE), or it is untouched for VIEW_IDLE_SECONDS. A view the owner asked to open is closed by the
owner (or idle) only. Fake pages and a fake clock, as in test_tab_lifecycle.py."""

import json

import httpx

from assistant.agent import Tool
from assistant.chat_views import VIEW_IDLE_SECONDS, ChatViews, ViewControl, chat_view_tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.tools.browser import CHAT_TAB_IDLE_SECONDS
from assistant.workers import MAIN_ONLY

from test_agent_profile import app_settings
from test_server import AUTH, Running, follow
from test_tab_lifecycle import Tick, browsing


class Clock:
    def __init__(self):
        self.t = 1_000.0

    def __call__(self):
        return self.t


def registry(newest=5):
    clock = Clock()
    return ChatViews(newest_id=lambda: newest, clock=clock), clock


# ---- the registry -----------------------------------------------------------------------------------------------------------------

def test_a_card_opens_a_view_anchored_after_the_newest_message_and_touch_does_not_change_the_version():
    views, clock = registry(newest=7)
    v = views.card("owner", "browser", {"title": "장바구니", "url": "https://shop.example.com/cart", "host": "shop.example.com",
                                       "status": "열림", "image": "data:image/jpeg;base64,AAA", "width": 360, "height": 200},
                   turn_id="t1")
    assert views.get("owner") is v and v.anchor_id == 7 and v.title == "장바구니" and v.kind == "browser"
    before = views.version
    views.touch("owner")
    assert views.version == before
    assert views.items()[0].key == "owner" and views.close("owner", "done") is v and views.get("owner") is None
    assert views.version == before + 1


def test_an_idle_view_is_listed_only_after_ten_minutes_untouched_and_not_while_something_works_in_it():
    views, clock = registry()
    views.open("owner", "browser")
    views.open("android", "phone")
    clock.t += VIEW_IDLE_SECONDS - 1
    assert views.idle(lambda k: False) == []
    clock.t += 1
    assert sorted(views.idle(lambda k: k == "android")) == ["owner"]
    views.touch("owner")
    assert views.idle(lambda k: False) == ["android"]     # the phone was not touched: it is the one left


def test_owner_asked_is_never_taken_back_and_a_later_card_keeps_it():
    views, clock = registry()
    views.open("owner", "browser", owner_asked=True)
    views.open("owner", "browser", owner_asked=False)
    views.card("owner", "browser", {"title": "t"})
    assert views.get("owner").owner_asked is True


# ---- the chat_view tool (the main agent's decision) -----------------------------------------------------------------------------

def control(shown=("owner",), open_ok=True):
    ctl = ViewControl()
    ctl.resolve = lambda target: {"browser": "owner", "phone": "android", "w1": "task:w1"}.get(target)
    ctl.tab_shown = lambda key: key in shown
    closed = []

    async def close_tab(key):
        closed.append(key)
        return True

    async def can_open(key):
        return open_ok

    async def refresh(key):
        return None

    ctl.close_tab, ctl.can_open, ctl.refresh = close_tab, can_open, refresh
    return ctl, closed


async def call(tool, **args):
    return await tool.handler(args)


async def test_the_main_agent_closes_a_view_when_the_work_is_done_but_not_one_the_owner_asked_for(monkeypatch):
    views, _ = registry()
    ctl, closed = control()
    tool = chat_view_tool(views, ctl)
    views.card("owner", "browser", {"title": "장바구니"})
    assert await call(tool, action="close", target="browser", reason="done") == "Closed in the chat."
    assert views.get("owner") is None and closed == ["owner"]
    views.card("owner", "browser", {"title": "장바구니"})
    assert "Shown in the chat" in await call(tool, action="open", target="browser", owner_asked=True)
    text = await call(tool, action="close", target="browser", reason="done")
    assert text.startswith("Not closed") and views.get("owner").owner_asked and closed == ["owner"]
    assert await call(tool, action="close", target="browser", reason="owner") == "Closed in the chat."
    assert views.get("owner") is None


async def test_the_phone_and_a_job_tab_are_targets_and_bad_input_is_refused():
    views, _ = registry()
    ctl, closed = control()
    tool = chat_view_tool(views, ctl)
    assert (await call(tool, action="open", target="phone")).startswith("Shown")
    assert views.get("android").kind == "phone"
    assert (await call(tool, action="open", target="nowhere")).startswith("Error: no such browser tab or phone")
    assert (await call(tool, action="close", target="w1", reason="done")) == "Not shown in the chat, nothing to close."
    assert (await call(tool, action="close", target="phone", reason="bogus")).startswith("Error: reason must be")
    ctl2, _ = control(open_ok=False)
    assert (await call(chat_view_tool(registry()[0], ctl2), action="open", target="phone")).startswith("Error: nothing to show")


def test_only_the_main_agent_has_the_view_tool():
    assert "chat_view" in MAIN_ONLY


# ---- through the server: cards in a reply, the sweeper, the owner, the list -----------------------------------------------------

def showing(sess):
    async def show(args):
        await sess.page()                      # the tab (opened again when the owner closed it)
        await sess.card("열림", force=True)
        return "shown"
    return Tool("show_tab", "x", {"type": "object"}, show, read_only=False)


def replies(*rounds):
    """Each round is one model request: a list of events, e.g. [ToolCall(...), Done("tool_calls")]."""
    script = iter(rounds)

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev
    return fn


async def test_a_card_in_the_chat_keeps_its_tab_until_ten_idle_minutes_then_closes_both(tmp_path):
    sess, tick, made, _ = browsing()
    app = create_app(app_settings(tmp_path), tools=[showing(sess)],
                     stream_fn=replies([ToolCall("c1", "show_tab", "{}"), Done("tool_calls")], [TextDelta("열었어요."), Done("stop")]),
                     browser=sess)
    app.state.chat_views.clock = tick
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "보여줘"})).json()["id"])
        assert any(e.get("type") == "browser" and e.get("tab") == "owner" for e in events)
        view = app.state.chat_views.get("owner")
        assert view is not None and view.kind == "browser" and view.url == "https://shop.example.com/cart"
        tick.t += CHAT_TAB_IDLE_SECONDS                     # an hour is not the rule for a shown tab: ten minutes is
        assert await app.state.sweep_tabs() == ["owner"] and made[0].is_closed()
        assert app.state.chat_views.get("owner") is None
        assert (await c.get("/api/browser/sessions")).json()["items"] == []


async def test_the_owner_watching_the_tab_keeps_its_view_and_the_views_list_answers_by_version(tmp_path, monkeypatch):
    sess, tick, made, _ = browsing()
    app = create_app(app_settings(tmp_path), tools=[showing(sess)],
                     stream_fn=replies([ToolCall("c1", "show_tab", "{}"), Done("tool_calls")], [TextDelta("열었어요."), Done("stop")]),
                     browser=sess)
    app.state.chat_views.clock = tick
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "보여줘"})).json()["id"])
        monkeypatch.setattr("assistant.tools.live.LiveHub.watching", lambda self, key: 1 if key == "owner" else 0)
        tick.t += VIEW_IDLE_SECONDS * 3
        assert await app.state.sweep_tabs() == [] and not made[0].is_closed()
        monkeypatch.setattr("assistant.tools.live.LiveHub.watching", lambda self, key: 0)
        listing = (await c.get("/api/chat/views")).json()
        assert [v["key"] for v in listing["items"]] == ["owner"] and listing["items"][0]["closes_at"] > tick.t
        again = (await c.get("/api/chat/views", params={"since": listing["version"]})).json()
        assert again == {"version": listing["version"], "unchanged": True, "at": again["at"]}


async def test_the_main_agent_hides_the_shown_tab_with_the_view_tool_and_the_owner_can_close_it_by_hand(tmp_path):
    sess, tick, made, _ = browsing()
    app = create_app(app_settings(tmp_path), tools=[showing(sess)], browser=sess, stream_fn=replies(
        [ToolCall("c1", "show_tab", "{}"), Done("tool_calls")], [TextDelta("열었어요."), Done("stop")],
        [ToolCall("c2", "chat_view", json.dumps({"action": "close", "target": "browser", "reason": "done"})), Done("tool_calls")],
        [TextDelta("닫았어요."), Done("stop")],
        [ToolCall("c3", "show_tab", "{}"), Done("tool_calls")], [TextDelta("다시 열었어요."), Done("stop")]))
    app.state.chat_views.clock = tick
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "보여줘"})).json()["id"])
        await follow(c, (await c.post("/api/turns", json={"text": "그만 보여줘"})).json()["id"])
        assert app.state.chat_views.get("owner") is None and not made[0].is_closed()   # its tab was in use by that reply: the sweeper closes it when free
        assert await app.state.sweep_tabs() == ["owner"] and made[0].is_closed()
        await follow(c, (await c.post("/api/turns", json={"text": "다시"})).json()["id"])
        assert app.state.chat_views.get("owner") is not None
        assert (await c.delete("/api/browser/sessions/owner")).json() == {"ok": True}
        assert app.state.chat_views.get("owner") is None


async def test_a_browser_job_a_chat_delegates_shows_its_tab_in_the_chat_from_the_start(tmp_path):
    from test_browser_workers import _app
    from test_workers import Brain, call, one_worker, say

    def worker(req, n):
        return call("report", {"status": "done", "summary": "s"})

    def chat(req, n):
        if req.messages[-1].get("role") == "tool":
            return say("맡겼어요.")
        return call("delegate", {"task": "장바구니를 만들어줘", "kind": "browser"})

    app = _app(tmp_path, Brain(chat=chat, worker=worker))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "장바구니 만들어줘"})).json()["id"])
        wid = (await one_worker(c, "done"))["id"]
    view = app.state.chat_views.get(f"task:{wid}")
    assert view is not None and view.kind == "browser" and view.worker_id == wid
