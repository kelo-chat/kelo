"""A browser step has one deadline; the user taking a tab over stops the running step and their taps are always answered."""

import asyncio

import pytest

from assistant.agent import Tool, ToolResult, run_agent
from assistant.agent.loop import ToolStopped
from assistant.providers import Done, TextDelta, ToolCall
from assistant.tools import live as live_mod
from assistant.tools.browser import STEP_SECONDS, TOOK_OVER_NOTE, BrowserControlled, BrowserSession, browser_owner, browser_tools, point_tools
from assistant.tools.live import LiveHub, map_point

from test_browser_live import META, FakeCDP, FakePage, make_session, settle


def scripted(*turns):
    it = iter(turns)

    async def fn(provider, request, **kw):
        for ev in next(it):
            yield ev

    return fn


async def run(tools):
    fn = scripted([ToolCall("c1", "slow", "{}"), Done("tool_calls")], [TextDelta("went on"), Done("stop")])
    msgs = [{"role": "user", "content": "go"}]
    return [e async for e in run_agent("p", "m", msgs, tools, stream_fn=fn)]


class Hang:
    """A DevTools session whose input calls never come back (a page whose main thread is blocked)."""
    def __init__(self, base: FakeCDP, hang=("Input.", "Runtime.evaluate")):
        self.base, self.hang = base, hang

    def on(self, *a):
        self.base.on(*a)

    async def send(self, method, params=None):
        if method.startswith(self.hang) and not (method == "Input.dispatchTouchEvent" and (params or {}).get("type") == "touchCancel"):
            self.base.sent.append((method, dict(params or {})))
            await asyncio.sleep(3600)
        return await self.base.send(method, params)

    async def detach(self):
        pass


# 1
async def test_step_deadline_returns_the_timeout_text_runs_the_cleanup_and_the_loop_goes_on():
    cleaned = []

    async def slow(args):
        await asyncio.sleep(30)
        return "never"

    async def on_timeout(args):
        cleaned.append(args)

    tool = Tool("slow", "s", {"type": "object"}, slow, deadline=0.2, on_timeout=on_timeout, timeout_text="[stopped after the deadline]")
    events = await run([tool])
    res = next(e for e in events if isinstance(e, ToolResult))
    assert "stopped after the deadline" in res.output and res.is_error and cleaned == [{}]
    assert any(getattr(e, "text", "") == "went on" for e in events)


async def test_browser_tools_all_carry_the_step_deadline():
    s = BrowserSession(connect=None, resolver=lambda *a, **k: [])
    tools = {t.name: t for t in browser_tools(s) + point_tools(s)}
    assert tools["browser_click"].deadline == STEP_SECONDS == 45.0 and tools["browser_open"].deadline == 60.0
    assert all(t.limiter is not None and t.on_timeout is not None for t in tools.values())


async def test_the_step_clock_stands_still_while_a_hand_over_waits():
    sess, _ = make_session()
    token = browser_owner.set("owner")
    try:
        async def step():
            with sess.pause_clock("owner"):
                await asyncio.sleep(0.5)   # the user solving a check: longer than the deadline
            await asyncio.sleep(0.05)
            return "done"
        assert await sess.run_step(step, 0.3) == "done"

        async def stuck():
            await asyncio.sleep(30)
        with pytest.raises(Exception) as e:
            await sess.run_step(stuck, 0.3)
        assert type(e.value).__name__ == "ToolTimeout"
    finally:
        browser_owner.reset(token)


# 2
async def test_a_navigation_that_never_answers_after_a_click_is_stopped_and_the_page_is_read_as_it_is():
    sess, cdps = make_session()
    token = browser_owner.set("owner")
    try:
        # a navigation that does not answer after the click: stopped, page read as it is, with a note
        class TimeoutError(Exception):   # the driver's own name
            pass

        class Stuck(FakePage):
            async def wait_for_load_state(self, *a, **k):
                raise TimeoutError()
        rescued = []
        sess.rescue = lambda key=None: rescued.append(key) or asyncio.sleep(0)
        await sess._loaded(Stuck(), "owner")
        assert rescued == ["owner"] and "still loading" in sess._notes["owner"][0]
    finally:
        browser_owner.reset(token)


# 4
async def test_taking_control_cancels_the_running_step_and_a_step_never_waits_for_the_owner_inside():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    token = browser_owner.set("owner")
    try:
        started = asyncio.Event()

        async def step():
            started.set()
            await asyncio.sleep(30)
        task = asyncio.ensure_future(sess.run_step(step, 45))
        await started.wait()
        assert sess.step_running("owner")
        hub.take("owner", "1")
        with pytest.raises(ToolStopped) as e:
            await task
        assert e.value.text == TOOK_OVER_NOTE and "took over this tab" in e.value.text
        await settle()
        assert not sess.step_running("owner")
        # a step that starts anyway (or one that was running) must not wait 300 s in page(): it fails at once
        async def inside():
            return await sess.page()
        step_task = asyncio.ensure_future(sess.run_step(inside, 45))
        with pytest.raises(BrowserControlled):
            await asyncio.wait_for(step_task, 2)
        # the hold still waits for the hand-back, and the next step runs after it
        waiter = asyncio.ensure_future(sess.wait_for_agent_turn("owner", 5))
        await settle()
        assert not waiter.done()
        await hub.give_back("owner", "1")
        assert await waiter is True
        assert await sess.run_step(lambda: asyncio.sleep(0, "ok"), 5) == "ok"
    finally:
        browser_owner.reset(token)


async def test_the_interrupt_cleans_up_the_page_before_the_owners_first_input():
    sess, cdps = make_session()
    base = FakeCDP()
    sess._tab_cdp["owner"] = base
    hub = LiveHub(sess, reaper=False)
    sess._steps["owner"] = asyncio.ensure_future(asyncio.sleep(30))   # an agent step is running: taking the tab stops it and cleans up
    hub.take("owner", "1")
    await sess.settled("owner")
    methods = [m for m, _ in base.sent]
    assert "Page.stopLoading" in methods
    assert any(m == "Input.dispatchTouchEvent" and p["type"] == "touchCancel" for m, p in base.sent)


# 5
async def test_the_owners_tap_on_a_blocked_page_is_answered_busy_in_time(monkeypatch):
    monkeypatch.setattr(live_mod, "INPUT_SECONDS", 0.3)
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "1")
    # a normal tap first: a mouse press at the point (the one view is the desktop)
    await hub.input("owner", "1", {"type": "tap", "x": 10, "y": 10})
    assert [p for p in cdps[0].methods("Input.dispatchMouseEvent") if p["type"] == "mousePressed"]
    assert not cdps[0].methods("Input.dispatchTouchEvent")
    hub.casts["owner"].cdp = hang = Hang(cdps[0])
    start = asyncio.get_running_loop().time()
    out = await hub.input("owner", "1", {"type": "tap", "x": 10, "y": 10})
    assert asyncio.get_running_loop().time() - start < 3
    assert out["ok"] is False and out["busy"] is True and "not responding" in out["note"]
    assert hub.health("owner")["page_busy"] is True
    n = len(cdps[0].sent)
    hub.casts["owner"].stale = True   # known blocked: nothing is sent at all (no late tap on another screen)
    out = await hub.input("owner", "1", {"type": "tap", "x": 10, "y": 10})
    assert out["busy"] is True and len(cdps[0].sent) == n


# 6
async def test_status_says_stale_when_the_picture_stopped_and_the_page_does_not_answer(monkeypatch):
    monkeypatch.setattr(live_mod, "STALE_AFTER", 0.1)
    monkeypatch.setattr(live_mod, "PROBE_SECONDS", 0.1)
    monkeypatch.setattr(live_mod, "WATCH_EVERY", 0.05)
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    changed = []
    hub.listeners.append(changed.append)
    cast = await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    cast.cdp = Hang(cdps[0])
    await asyncio.sleep(0.5)
    assert hub.health("owner") == {"page_busy": True, "stale": True} and changed
    cast.cdp = cdps[0]
    cdps[0].frame(sid=2)   # a new frame: the page is alive again
    await asyncio.sleep(0.25)
    assert hub.health("owner") == {"page_busy": False, "stale": False}
    await hub.close()


# 7
def test_a_720x1598_frame_of_the_412x915_phone_screen_maps_the_corner_x_to_css_pixels():
    meta = {**META, "deviceWidth": 412, "deviceHeight": 915}
    x, y = map_point(720 * 332 / 412, 1598 * 80 / 915, 720, 1598, meta)
    assert (round(x), round(y)) == (332, 80)
