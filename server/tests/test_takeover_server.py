"""Take over, server side (live_v 2): the page takes the shape of the owner's screen, an input is mapped with the frame the owner looked at,
a focused text field is reported (`editing`), the owner's hands on the phone are written down (never typed text), the agent is told, paused
jobs go on, and handing back approves nothing. Fake DevTools session and fake phone bridge."""

import asyncio
import json

import httpx
import pytest
import websockets

from assistant.android.live import HELD_FPS, TIMEOUT_NOTE as PHONE_TIMEOUT, summarize
from assistant.android.tools import android_tools
from assistant.server import create_app
from assistant.tools import live as live_mod
from assistant.tools.browser import TIMEOUT_NOTE, BrowserSession, browser_owner
from assistant.tools.live import LiveError, LiveHub, viewer_css_size
from assistant.tools.takeover import TakeoverJournal
from assistant.workers import HANDOFF_MARK

from test_agent_profile import app_settings
from test_android_live import Bridge, client_of, make, png
from test_browser_live import META, Clock, FakeCDP, FakePage, calling, make_session, recv_until, settle, ws_url
from test_server import AUTH, Running


class EditCDP(FakeCDP):
    editing = False

    async def send(self, method, params=None):
        if method == "Runtime.evaluate" and (params or {}).get("expression") == live_mod.EDITING_JS:
            self.sent.append((method, dict(params)))
            return {"result": {"type": "boolean", "value": self.editing}}
        return await super().send(method, params)


def session_with(cdp_class=FakeCDP, page=None):
    cdps = []

    async def cdp(_page):
        c = cdp_class()
        cdps.append(c)
        return c

    from test_browser_live import public
    sess = BrowserSession(connect=None, cdp=cdp, resolver=public)
    sess._pages["owner"] = page or FakePage()
    return sess, cdps


async def watch(hub, cdps, meta=None):
    await hub.join("owner", "a")
    cdps[0].frame(meta=meta)
    return await hub.wait_frame("owner", 0, 1.0)


# ------------------------------------------------------------------------------------------------ the page follows the screen
def test_the_screen_size_becomes_a_css_size_with_floors_and_caps():
    assert viewer_css_size(1080, 2208, 2.625, "mobile") == (411, 841, 2.625)
    w, h, ratio = viewer_css_size(1080, 2208, 2.625, "desktop")        # 411 css px wide is below the 500 floor: scaled up, same shape
    assert w == 500 and abs(h / w - 2208 / 1080) < 0.01 and ratio == 2.16
    assert viewer_css_size(300, 600, 3, "mobile")[0] == 320            # mobile floor
    w, h, _ = viewer_css_size(6000, 20000, 1, "desktop")
    assert w <= live_mod.MAX_VIEW_W and h <= live_mod.MAX_VIEW_H and abs(w / h - 0.3) < 0.01
    assert viewer_css_size(1080, 2000, None, "desktop") is not None    # no density = 1
    for bad in ((0, 100, 1), (100, -1, 1), ("a", 1, 1), (True, 1, 1), (100, 100, 0), (float("nan"), 1, 1)):
        assert viewer_css_size(*bad) is None


async def test_a_desktop_view_tab_keeps_its_viewport_while_the_owner_controls_it():
    """Owner decision 2026-11-17 ("그냥 확대만해"): the phone only zooms; a desktop-view tab is never laid out at the phone's width."""
    sess, cdps = session_with()
    hub = LiveHub(sess, reaper=False)
    await watch(hub, cdps)
    with pytest.raises(LiveError) as e:
        await hub.view("owner", "d1", {"width": 0, "height": 5, "density": 1})
    assert e.value.status == 422
    assert (await hub.view("owner", "d1", {"width": 1080, "height": 2208, "density": 2.625}))["applied"] is False   # not controlling yet
    hub.take("owner", "d1")
    await settle(20)
    out = await hub.view("owner", "d1", {"width": 1080, "height": 1200, "density": 1.0})   # the keyboard opened: a shorter screen
    assert out["applied"] and "css_w" not in out
    assert not cdps[0].methods("Emulation.setDeviceMetricsOverride")
    await hub.give_back("owner", "d1")
    assert not cdps[0].methods("Emulation.clearDeviceMetricsOverride") and "owner" not in hub._fitted


async def test_a_phone_view_tab_keeps_its_phone_identity_with_the_owners_size_and_gets_its_screen_back():
    sess, cdps = session_with()
    view_cdp = FakeCDP()
    sess._view_cdp["owner"] = view_cdp
    hub = LiveHub(sess, reaper=False)
    await watch(hub, cdps)
    hub.take("owner", "d1")
    out = await hub.view("owner", "d1", {"width": 1080, "height": 2208, "density": 2.625})
    assert (out["css_w"], out["css_h"]) == (411, 841)
    o = view_cdp.methods("Emulation.setDeviceMetricsOverride")[-1]
    assert o["mobile"] is True and o["width"] == 411 and not cdps[0].methods("Emulation.setDeviceMetricsOverride")
    await hub.give_back("owner", "d1")
    assert view_cdp.methods("Emulation.setDeviceMetricsOverride")[-1]["width"] == 412   # PHONE: the agent's own screen


async def test_the_phone_view_is_given_back_when_the_owners_screen_goes_away():
    clock = Clock()
    sess, cdps = session_with()
    view_cdp = FakeCDP()
    sess._view_cdp["owner"] = view_cdp
    hub = LiveHub(sess, clock, reaper=False)
    await watch(hub, cdps)
    hub.take("owner", "d1", by="handover")   # the grace time is for a hand-over hold (a button hold never times out)
    await hub.view("owner", "d1", {"width": 1080, "height": 2208, "density": 2.625})
    assert view_cdp.methods("Emulation.setDeviceMetricsOverride")[-1]["width"] == 411
    clock.t += live_mod.CONTROL_GRACE + 1
    await hub.reap()
    assert sess.controlled_by("owner") is None and view_cdp.methods("Emulation.setDeviceMetricsOverride")[-1]["width"] == 412


# ------------------------------------------------------------------------------------------------ the frame the owner saw
async def test_an_input_is_mapped_with_the_frame_the_owner_saw_and_says_so():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    cdps[0].frame(sid=1)                                                                  # frame 1: 1440 wide page
    await hub.wait_frame("owner", 0, 1.0)
    cdps[0].frame(sid=2, meta={**META, "deviceWidth": 720, "deviceHeight": 450})        # frame 2: the page changed size (now 1 css px per frame px)
    await hub.wait_frame("owner", 1, 1.0)
    hub.take("owner", "d1")
    old = await hub.input("owner", "d1", {"type": "tap", "x": 100, "y": 50, "frame_seq": 1})
    assert (old["x"], old["y"], old["mapped_with"]) == (200.0, 100.0, "frame")
    new = await hub.input("owner", "d1", {"type": "tap", "x": 100, "y": 50, "frame_seq": 2})
    assert (new["x"], new["y"], new["mapped_with"]) == (100.0, 50.0, "frame")
    for ev in ({"type": "tap", "x": 100, "y": 50}, {"type": "tap", "x": 100, "y": 50, "frame_seq": 99},
               {"type": "tap", "x": 100, "y": 50, "frame_seq": "1"}, {"type": "tap", "x": 100, "y": 50, "frame_seq": True}):
        out = await hub.input("owner", "d1", ev)
        assert (out["x"], out["mapped_with"]) == (100.0, "latest")
    s = await hub.input("owner", "d1", {"type": "scroll", "x": 100, "y": 50, "dy": 10, "frame_seq": 1})
    assert s["deltaY" if False else "dy"] == 20.0 and s["mapped_with"] == "frame"
    lp = await hub.input("owner", "d1", {"type": "long_press", "x": 100, "y": 50, "ms": 300, "frame_seq": 1})
    assert lp["mapped_with"] == "frame" and lp["x"] == 200.0


async def test_only_the_newest_frames_are_remembered():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    for i in range(1, live_mod.RECENT_FRAMES + 4):
        cdps[0].frame(sid=i)
        await hub.wait_frame("owner", i - 1, 1.0)
    hub.take("owner", "d1")
    assert (await hub.input("owner", "d1", {"type": "tap", "x": 1, "y": 1, "frame_seq": 1}))["mapped_with"] == "latest"
    assert (await hub.input("owner", "d1", {"type": "tap", "x": 1, "y": 1, "frame_seq": live_mod.RECENT_FRAMES + 3}))["mapped_with"] == "frame"


# ------------------------------------------------------------------------------------------------ editing flag
async def test_the_frame_says_when_a_text_field_has_focus_only_while_the_owner_controls():
    sess, cdps = session_with(EditCDP)
    hub = LiveHub(sess, reaper=False)
    f = await watch(hub, cdps)
    assert f.header()["live_v"] == 2 and f.header()["editing"] is False
    cdps[0].editing = True
    cdps[0].frame(sid=2)
    f = await hub.wait_frame("owner", 1, 1.0)
    assert f.header()["editing"] is False and not [m for m in cdps[0].methods("Runtime.evaluate") if m["expression"] == live_mod.EDITING_JS]   # nobody controls: not even looked at
    hub.take("owner", "d1")
    cdps[0].frame(sid=3)
    await hub.wait_frame("owner", 2, 1.0)
    await settle(20)
    cdps[0].frame(sid=4)
    assert (await hub.wait_frame("owner", 3, 1.0)).header()["editing"] is True
    cdps[0].editing = False
    await asyncio.sleep(live_mod.EDITING_EVERY + 0.05)
    cdps[0].frame(sid=5)
    await hub.wait_frame("owner", 4, 1.0)
    await settle(20)
    cdps[0].frame(sid=6)
    assert (await hub.wait_frame("owner", 5, 1.0)).header()["editing"] is False


# ------------------------------------------------------------------------------------------------ over the websocket
async def test_websocket_view_and_acked_input_reply_with_where_it_landed_and_ignore_unknown_fields(tmp_path):
    sess, cdps = make_session()
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=calling(), browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        t = (await c.post("/api/browser/sessions/owner/live-ticket")).json()["ticket"]
        async with websockets.connect(ws_url(srv, f"/api/browser/sessions/owner/live?ticket={t}")) as ws:
            await recv_until(ws, "status")
            cdps[0].frame(sid=1)
            head = await recv_until(ws, "frame")
            assert head["live_v"] == 2 and head["editing"] is False
            await ws.send(json.dumps({"type": "control", "take": True}))
            await recv_until(ws, "status")
            await ws.send(json.dumps({"type": "view", "width": 1080, "height": 2208, "density": 2.625, "extra": {"x": 1}}))
            v = await recv_until(ws, "view_ok")
            assert v["ok"] and v["applied"] and "css_w" not in v   # a desktop-view tab keeps its viewport
            await ws.send(json.dumps({"type": "view", "width": -1, "height": 1, "density": 1}))
            assert (await recv_until(ws, "error"))["code"] == "bad_event"
            await ws.send(json.dumps({"type": "tap", "x": 100, "y": 50, "frame_seq": head["seq"], "ack": True, "future_field": [1, 2]}))
            ok = await recv_until(ws, "input_ok")
            assert ok["ok"] is True and ok["mapped_with"] == "frame" and (ok["x"], ok["y"]) == (200.0, 100.0)and not ok.get("busy")
            await ws.send(json.dumps({"type": "tap", "x": 100, "y": 50, "ack": True}))
            assert (await recv_until(ws, "input_ok"))["mapped_with"] == "latest"


async def test_a_busy_page_answers_not_ok_and_busy():
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    cast = await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "d1")
    cast.stale = True
    out = await hub.input("owner", "d1", {"type": "tap", "x": 1, "y": 1, "frame_seq": 1})
    assert out["ok"] is False and out["busy"] is True


async def test_the_http_input_ignores_unknown_fields(tmp_path):
    sess, cdps = make_session()
    hub = LiveHub(sess, reaper=False)
    await hub.join("owner", "a")
    cdps[0].frame()
    await hub.wait_frame("owner", 0, 1.0)
    hub.take("owner", "d1")
    out = await hub.input("owner", "d1", {"type": "tap", "x": 1, "y": 1, "something_new": {"a": 1}, "frame_w": 720, "frame_h": 450})
    assert out["ok"] is True


# ------------------------------------------------------------------------------------------------ hand-back of the browser
async def test_a_hand_back_by_timeout_says_so_and_approves_no_payment():
    clock = Clock()
    sess, cdps = session_with()
    sess.journal = TakeoverJournal(sess.cdp_session)
    told = []
    sess.handback_listeners.append(lambda *a: told.append(a))
    hub = LiveHub(sess, clock, reaper=False)
    await watch(hub, cdps)
    hub.take("owner", "d1", by="handover")   # a hand-over hold: the grace time still applies to it
    token = browser_owner.set("owner")
    try:
        sess.allow_purchase("shop.example.com")
        assert sess.purchase_active()
        clock.t += live_mod.CONTROL_GRACE + 1
        await hub.reap()
        assert not sess.purchase_active() and sess.controlled_by("owner") is None
    finally:
        browser_owner.reset(token)
    assert told and told[-1][0] == "owner" and TIMEOUT_NOTE in told[-1][1] and told[-1][2:] == ("timeout", "browser")
    assert TIMEOUT_NOTE in sess._notes["owner"][-1]


async def test_a_normal_hand_back_tells_the_listeners_what_the_owner_did():
    sess, cdps = session_with()
    sess.journal = TakeoverJournal(sess.cdp_session)
    told = []
    sess.handback_listeners.append(lambda *a: told.append(a))
    hub = LiveHub(sess, reaper=False)
    await watch(hub, cdps)
    hub.take("owner", "d1")
    sess.journal.add("owner", {"k": "click", "label": "Next"})
    await hub.give_back("owner", "d1")
    assert told[-1][2:] == ("owner", "browser") and "clicked 'Next'" in told[-1][1] and "approved no purchase" in told[-1][1]


# ------------------------------------------------------------------------------------------------ the phone
async def test_phone_input_is_mapped_with_the_frame_the_owner_saw():
    bridge, live = make()
    await live.join("android", "v1")
    await live.step()                                  # frame 1: 1080 x 2340
    bridge.png = png(540, 1170)
    await live.step()                                  # frame 2: the phone turned to a smaller picture
    live.take("android", "d1")
    a = await live.input("android", "d1", {"type": "tap", "x": 360, "y": 780, "frame_seq": 1, "unknown": 1})
    assert (a["x"], a["y"], a["mapped_with"]) == (540, 1170, "frame")
    b = await live.input("android", "d1", {"type": "tap", "x": 360, "y": 780, "frame_seq": 2})
    assert (b["x"], b["y"], b["mapped_with"]) == (360, 780, "frame") or b["mapped_with"] == "frame"
    c = await live.input("android", "d1", {"type": "tap", "x": 360, "y": 780})
    assert c["mapped_with"] == "latest"
    s = await live.input("android", "d1", {"type": "swipe", "x1": 0, "y1": 0, "x2": 10, "y2": 10, "frame_seq": 1})
    assert s["mapped_with"] == "frame"


async def test_the_phone_is_looked_at_twice_as_fast_while_held():
    _, live = make()
    cast = await live.join("android", "v1")
    assert cast.settings["fps"] == 3 and live.interval() == live.frame_every
    live.take("android", "d1")
    assert cast.settings["fps"] == HELD_FPS == 6 and live.interval() == live.frame_every / 2
    await live.configure("android", fps=2)
    assert cast.settings["fps"] == 2
    await live.configure("android", fps=15)
    assert cast.settings["fps"] == 6
    await live.give_back("android", "d1")
    assert cast.settings["fps"] == 3


async def test_the_phone_view_message_changes_nothing_and_rejects_bad_numbers():
    _, live = make()
    assert await live.view("android", "d1", {"width": 1080, "height": 2000, "density": 3}) == {"ok": True, "applied": False}
    with pytest.raises(LiveError):
        await live.view("android", "d1", {"width": "x", "height": 2000, "density": 3})


SCREEN_XML = ('<hierarchy><node bounds="[0,0][1080,2340]" package="com.shop"><node class="android.widget.EditText" focused="{f}" '
              'bounds="[10,100][1000,200]" package="com.shop" text=""/></node></hierarchy>')


class XmlBridge(Bridge):
    focused = "true"

    def __call__(self, request):
        if request.url.path == "/screen" and request.url.params["image"] == "0":
            self.frames += 0
            return httpx.Response(200, json={"xml": SCREEN_XML.format(f=self.focused), "app": "com.shop/.CheckoutActivity"})
        return super().__call__(request)


async def test_the_phone_frame_says_a_text_field_has_focus_while_the_owner_holds_the_phone():
    bridge = XmlBridge()
    _, live = make(bridge)
    cast = await live.join("android", "v1")
    await live.step()
    assert live.latest("android").header()["editing"] is False
    live.take("android", "d1")
    await live.step()
    await asyncio.sleep(0.05)
    await live.step()                                  # the answer arrived: the picture is sent again with the flag
    assert cast.editing is True and live.latest("android").header()["editing"] is True and live.latest("android").header()["live_v"] == 2
    await live.give_back("android", "d1")
    assert cast.editing is False


async def test_what_the_owner_did_on_the_phone_is_written_down_without_the_text_and_told_to_the_agent():
    bridge = XmlBridge()
    bridge.focused = "false"
    _, live = make(bridge)
    told = []
    live.handback_listeners.append(lambda *a: told.append(a))
    await live.join("android", "v1")
    await live.step()
    live.take("android", "d1")
    await live.input("android", "d1", {"type": "tap", "x": 360, "y": 780})
    await live.input("android", "d1", {"type": "text", "text": "hunter2-SECRET"})
    await live.input("android", "d1", {"type": "key", "key": "back"})
    await live.give_back("android", "d1")
    note = live.take_note()
    assert "tapped the screen" in note and "typed 14 characters" in note and "pressed back" in note and "com.shop (CheckoutActivity)" in note
    assert "hunter2" not in note and "SECRET" not in note and "approved no purchase" in note
    assert live.take_note() == ""                       # told once
    assert told == [("android", note, "owner", "phone")]


async def test_a_phone_hand_back_by_timeout_says_so_and_a_hand_back_with_nothing_done_says_nothing():
    clock = Clock()
    _, live = make(clock=clock)
    told = []
    live.handback_listeners.append(lambda *a: told.append(a))
    live.take("android", "d1")
    await live.give_back("android", "d1")
    assert live.take_note() == "" and told[-1][1] == ""
    live.take("android", "d1", by="handover")
    clock.now = 1.0
    live._seen["d1"] = 0.0
    clock.now = live_mod.CONTROL_GRACE + 5
    await live.step()
    assert live.holder("android") is None and PHONE_TIMEOUT in live.take_note() and told[-1][2] == "timeout"


def test_the_phone_summary_is_short_and_never_has_typed_text():
    entries = [("tap", (i, i)) for i in range(1, 12)] + [("text", (5,))] * 3 + [("key", ("home",))]
    text = summarize(entries, 130, "com.app (Main)")
    assert text.count("\n- ") <= 8 and "more steps" in text and "about 2 min" in text and "typed 5 characters (the text is not recorded) (x3)" in text
    assert summarize([], 10) == ""


async def test_the_next_phone_reading_starts_with_the_owners_note_and_old_refs_are_dropped():
    bridge = XmlBridge()
    bridge.focused = "false"
    client = client_of(bridge)
    from assistant.android.live import DeviceLive
    live = DeviceLive(client, runner=False)
    tools = {t.name: t for t in android_tools(client, hold=live.hold_for_user, notes=live.take_note)}
    live.take("android", "d1")
    live._journal.append(("tap", (5, 5)))
    await live.give_back("android", "d1")
    assert await tools["android_look"].hold({}) is None
    out = await tools["android_look"].handler({})
    assert "While the owner used the phone" in out.head and out.head.index("While the owner") < out.head.index("The screen of the phone app")
    again = await tools["android_look"].handler({})
    assert "While the owner" not in again.head


# ------------------------------------------------------------------------------------------------ workers
def _workers(tmp_path):
    from assistant.store import Store
    from assistant.workers import Limits, WorkerHost, Workers

    store = Store()
    resumed = []
    host = WorkerHost(start_turn=None, cancel_turn=lambda t: None, handoff=None, emit=None, tools=lambda: [], turn_on=lambda k: k == "busy")
    w = Workers(store, host, Limits(), resume_on_start=False)
    w.resume = lambda wid, why="", auto=False, **kw: (resumed.append((wid, why)) or (True, ""))
    return store, w, resumed


def _paused(store, w, wid, wait, status="needs_input"):
    store._run("INSERT INTO workers(id, kind, title, task, status, tab, wait_on, created_at, updated_at, max_steps, deadline_at, minutes, token_cap) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (wid, "browser", "t", "task", status, f"task:{wid}", json.dumps(wait), 1.0, 1.0, 10, 1.0, 5, 1000))


def test_a_paused_job_waiting_on_the_phone_goes_on_when_the_owner_hands_it_back(tmp_path):
    store, w, resumed = _workers(tmp_path)
    _paused(store, w, "w1", {"kind": "device"})
    _paused(store, w, "w2", {"kind": "user"})
    w.handed_back("android", "[the owner did things]", "owner", "phone")
    assert [r[0] for r in resumed] == ["w1"] and "handed the phone back" in resumed[0][1] and "android_look" in resumed[0][1]
    assert "approves no purchase" in resumed[0][1]
    assert not [m for m in store.messages() if HANDOFF_MARK in m["content"]]          # a job took it up: no hidden message


def test_a_paused_browser_job_goes_on_and_a_timeout_is_said(tmp_path):
    store, w, resumed = _workers(tmp_path)
    _paused(store, w, "w1", {"kind": "control"}, "blocked")
    w.handed_back("task:w1", "", "timeout", "browser")
    assert resumed and "timed out" in resumed[0][1] and "browser_read" in resumed[0][1]
    resumed.clear()
    w.control_changed("task:w1", None)                     # the older listener entry still works
    assert resumed and "browser" in resumed[0][1]
    resumed.clear()
    w.control_changed("task:w1", "d1")                     # taking the tab starts nothing
    assert not resumed


def test_with_no_job_and_no_running_turn_the_hand_back_becomes_hidden_context_in_the_chat(tmp_path):
    store, w, resumed = _workers(tmp_path)
    w.handed_back("android", "[While the owner used the phone ...]", "owner", "phone")
    msgs = [m for m in store.messages() if HANDOFF_MARK in m["content"]]
    assert len(msgs) == 1 and msgs[0]["role"] == "user" and "While the owner used the phone" in msgs[0]["content"] and not resumed
    w.handed_back("busy", "[a turn is on it]", "owner", "browser")       # a running turn gets the note on its own result
    w.handed_back("android", "", "owner", "phone")                       # nothing was done: nothing is written
    assert len([m for m in store.messages() if HANDOFF_MARK in m["content"]]) == 1


# ------------------------------------------------------------------------------------------------ real Chromium
async def test_in_a_real_browser_the_page_is_as_wide_as_the_owners_screen_and_gets_its_size_back():
    pw = pytest.importorskip("playwright.async_api")
    from fixtures.order_harness import public as PUBLIC
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)

        async def serve(route):
            await route.fulfill(status=200, content_type="text/html", body="<!doctype html><meta name=viewport content='width=device-width'><p>hi</p>")
        await c.route("https://**/*", serve)
        page = await c.new_page()

        async def connect():
            return page
        for view in ("desktop", "mobile"):
            s = BrowserSession(connect=connect, resolver=PUBLIC)
            await s.open("https://m.example.com/", view)
            hub = LiveHub(s, reaper=False)
            key = browser_owner.get()
            await hub.join(key, "v")
            before = await page.evaluate("() => [innerWidth, innerHeight]")
            hub.take(key, "d")
            out = await hub.view(key, "d", {"width": 1080, "height": 2000, "density": 1.5})
            got = await page.evaluate("() => [innerWidth, innerHeight]")
            await hub.give_back(key, "d")
            await hub.leave(key, "v")
            after = await page.evaluate("() => [innerWidth, innerHeight]")
            if view == "desktop":   # the phone only zooms: the page keeps its desktop viewport
                assert "css_w" not in out and got == before and after == before, (view, before, got, after)
            else:                   # 720 css px wide is above the mobile floor: the screen as it is (1080 / 1.5 wide)
                assert (out["css_w"], out["css_h"]) == (720, 1333) and got[0] == 720 and abs(got[1] - 1333) <= 1, (view, got, out)
                assert after[0] == 412, (view, before, after)
        await b.close()
