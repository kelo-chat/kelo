"""Browser tab lifecycle: chat tabs close after an hour unused (from the end of their last turn), a schedule's or a handed-over task's
tab closes as soon as its run is over and stays listed as an "ended" entry with its last picture; a live viewer, the user's control,
a pending approval or a running turn postpone closing. Fake browser pages and a fake clock; the sweeper's pass is called directly."""

import asyncio

import httpx

from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.tools.browser import CHAT_TAB_IDLE_SECONDS, BrowserSession

from test_agent_profile import ShotPage, app_settings, approval_tool
from test_server import AUTH, Running, follow


class Tick:
    def __init__(self):
        self.t = 10_000.0

    def __call__(self):
        return self.t


def browsing():
    """A browser whose every new tab is a page on shop.example.com, a fake clock, and a tool that uses the asking owner's tab."""
    tick, made = Tick(), []

    async def connect():
        made.append(ShotPage("https://shop.example.com/cart"))
        return made[-1]

    sess = BrowserSession(connect=connect, clock=tick)

    async def use(args):
        await sess.page()
        return "opened"

    return sess, tick, made, Tool("use_tab", "x", {"type": "object"}, use, read_only=True)


def tab_then_answer(n=10):
    """Each turn: the model uses the tab once, then answers."""
    script = iter([[ToolCall(f"c{i}", "use_tab", "{}"), Done("tool_calls")] if i % 2 == 0 else [TextDelta("다 봤어요."), Done("stop")]
                   for i in range(2 * n)])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev
    return fn


async def until(cond, tries=200):
    for _ in range(tries):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_a_chat_tab_closes_an_hour_after_its_last_turn_not_before_and_control_postpones_it(tmp_path):
    sess, tick, made, tool = browsing()
    app = create_app(app_settings(tmp_path), tools=[tool], stream_fn=tab_then_answer(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        sweep = app.state.sweep_tabs
        tick.t += 500                                      # the turn starts a while after the tab was last used...
        await follow(c, (await c.post("/api/turns", json={"text": "장바구니 봐줘"})).json()["id"])
        assert sess.open_keys() == ["owner"]
        tick.t += CHAT_TAB_IDLE_SECONDS - 1                # ...and the hour counts from the end of the turn
        assert await sweep() == [] and not made[0].is_closed()
        tick.t += 2
        assert await sweep() == ["owner"] and made[0].is_closed()
        assert (await c.get("/api/browser/sessions")).json()["items"] == []      # a chat tab leaves no ended entry
        # a new turn opens a fresh tab in the same browser (the context with its sign-ins is untouched)
        await follow(c, (await c.post("/api/turns", json={"text": "다시"})).json()["id"])
        assert len(made) == 2 and sess.open_keys() == ["owner"] and sess._ctx is None   # connect() fake: no context was touched
        sess.take_control("owner", "7")                    # the user holds the tab: never closed, and the holding counts as use
        tick.t += CHAT_TAB_IDLE_SECONDS * 3
        assert await sweep() == [] and not made[1].is_closed()
        await sess.release_control("owner")
        tick.t += CHAT_TAB_IDLE_SECONDS - 60
        assert await sweep() == []
        tick.t += 61
        assert await sweep() == ["owner"]


async def test_a_live_viewer_or_a_pending_approval_keeps_a_chat_tab_open(tmp_path, monkeypatch):
    sess, tick, made, tool = browsing()
    gate = approval_tool("send")
    script = iter([[ToolCall("c0", "use_tab", "{}"), Done("tool_calls")], [ToolCall("c1", "send", "{}"), Done("tool_calls")],
                   [TextDelta("보냈어요."), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev

    app = create_app(app_settings(tmp_path), tools=[tool, gate], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        sweep = app.state.sweep_tabs
        tid = (await c.post("/api/turns", json={"text": "보내줘"})).json()["id"]
        for _ in range(200):
            pend = (await c.get("/api/approvals/pending")).json()["items"]
            if pend:
                break
            await asyncio.sleep(0.01)
        tick.t += CHAT_TAB_IDLE_SECONDS * 2                 # an approval waits on this tab's turn: it stays
        assert await sweep() == [] and not made[0].is_closed()
        await c.post(f"/api/approvals/{pend[0]['approval_id']}", json={"approve": True})
        await follow(c, tid)
        monkeypatch.setattr("assistant.tools.live.LiveHub.watching", lambda self, key: 1 if key == "owner" else 0)
        tick.t += CHAT_TAB_IDLE_SECONDS * 2                 # someone watches it live: it stays
        assert await sweep() == []
        monkeypatch.setattr("assistant.tools.live.LiveHub.watching", lambda self, key: 0)
        tick.t += CHAT_TAB_IDLE_SECONDS + 1                 # watching counted as use; an hour after they left it closes
        assert await sweep() == ["owner"]


async def test_a_schedule_tab_closes_right_after_its_run_and_stays_listed_as_ended(tmp_path):
    sess, tick, made, tool = browsing()
    app = create_app(app_settings(tmp_path), tools=[tool], stream_fn=tab_then_answer(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "장바구니 점검", "prompt": "확인해줘", "cron": "0 9 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        await follow(c, run["turn_id"])
        key = f"schedule:{s['id']}"
        assert await until(lambda: made and made[0].is_closed())          # no sweep needed: closed as the run ended
        items = (await c.get("/api/browser/sessions")).json()["items"]
        assert len(items) == 1
        e = items[0]
        assert e["id"] == key and e["status"] == "ended" and e["ended_at"] and e["kind"] == "schedule"
        assert e["label"] == "Automatic run · 장바구니 점검" and e["title"] == "가게 장바구니" and e["host"] == "shop.example.com"
        assert e["url"] == "https://shop.example.com/cart" and e["task_id"] == run["task_id"] and e["viewers"] == 0
        assert e["thumbnail"].startswith("data:image/jpeg;base64,") and len(e["thumbnail"]) * 3 / 4 <= 60_000 and "shot" not in e
        shot = await c.get(f"/api/browser/sessions/{key}/screenshot")
        assert shot.status_code == 200 and shot.headers["content-type"] == "image/jpeg" and len(shot.content) <= 300_000
        assert (await c.get("/api/browser/sessions", params={"thumbnails": False})).json()["items"][0]["thumbnail"] is None
        assert (await c.delete(f"/api/browser/sessions/{key}")).json() == {"ok": True}
        assert (await c.get("/api/browser/sessions")).json()["items"] == []
        assert (await c.get(f"/api/browser/sessions/{key}/screenshot")).status_code == 404


async def test_a_watched_task_tab_waits_for_the_viewer_then_the_sweep_retires_it_and_only_20_ended_entries_are_kept(tmp_path, monkeypatch):
    sess, tick, made, tool = browsing()
    app = create_app(app_settings(tmp_path), tools=[tool], stream_fn=tab_then_answer(30), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        monkeypatch.setattr("assistant.tools.live.LiveHub.watching", lambda self, key: 1 if key.startswith("task:") else 0)
        out = (await c.post("/api/browser/sessions", json={"prompt": "중고 자전거 시세 찾아줘"})).json()
        await follow(c, out["turn_id"])
        await asyncio.sleep(0.05)
        assert sess.open_keys() == [out["id"]] and not made[0].is_closed()      # still watched: kept open
        assert await app.state.sweep_tabs() == []
        monkeypatch.setattr("assistant.tools.live.LiveHub.watching", lambda self, key: 0)
        assert await app.state.sweep_tabs() == [out["id"]]                     # the next pass retires it
        e = (await c.get("/api/browser/sessions")).json()["items"][0]
        assert e["status"] == "ended" and e["kind"] == "task" and e["task_id"] == out["task_id"]
        # only the newest 20 ended entries are kept
        for i in range(21):
            await sess.close_tab(f"task:x{i}")
            sess._pages[f"task:x{i}"] = ShotPage("https://shop.example.com/x")
            assert await app.state.retire_tab(f"task:x{i}")
        ended = app.state.ended_tabs
        assert len(ended) == 20 and "task:x0" not in ended and out["id"] not in ended and "task:x20" in ended


async def test_a_running_turn_keeps_its_one_run_tab_open(tmp_path):
    sess, tick, made, tool = browsing()
    release = asyncio.Event()

    async def slow(args):
        await sess.page()
        await release.wait()
        return "done"

    script = iter([[ToolCall("c0", "slow", "{}"), Done("tool_calls")], [TextDelta("끝"), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev

    app = create_app(app_settings(tmp_path), tools=[Tool("slow", "x", {"type": "object"}, slow, read_only=True)], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        out = (await c.post("/api/browser/sessions", json={"prompt": "오래 걸리는 일"})).json()
        assert await until(lambda: bool(made))
        tick.t += CHAT_TAB_IDLE_SECONDS * 5
        assert await app.state.sweep_tabs() == [] and not made[0].is_closed()
        assert (await c.delete(f"/api/browser/sessions/{out['id']}")).status_code == 409
        release.set()
        await follow(c, out["turn_id"])
        assert await until(lambda: made[0].is_closed())
