"""A long run tells once how far it got (run_progress.py, backlog 66)."""

import asyncio
import httpx
import pytest

from assistant import i18n, run_progress
from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.run_progress import RunProgress
from assistant.schedule_grants import ACK_LINE, PROGRESS_NOTICE
from assistant.server import create_app
from test_server import AUTH, Running, read_events, scripted, settings


def test_once_per_run_after_the_wait(monkeypatch):
    now = [0.0]
    p = RunProgress(clock=lambda: now[0])
    k = p.key("t1", None)
    p.begin(k)
    assert p.add(k) == 1 and not p.due(k)           # too early
    now[0] = run_progress.PROGRESS_AFTER + 1
    assert p.add(k) == 2 and p.due(k)
    assert p.add(k) == 3 and not p.due(k)           # never twice
    p.end(k)
    assert not p.due(k)


def test_due_after_10_minutes_any_run(monkeypatch):
    now = [0.0]
    p = RunProgress(clock=lambda: now[0])
    k = p.key("t1", None)
    p.begin(k)
    now[0] = 300
    assert not p.due(k)                             # under 10 minutes: no notice
    now[0] = run_progress.PROGRESS_AFTER + 1
    assert p.due(k)                                 # at 10 minutes: due once
    now[0] = run_progress.PROGRESS_AFTER + 10
    assert not p.due(k)                             # within 10 minutes: not due again
    now[0] = run_progress.PROGRESS_AFTER * 2 + 2
    assert p.due(k)                                 # at 20 minutes: due once per 10 min


def test_a_job_is_one_run_over_its_segments():
    now = [0.0]
    p = RunProgress(clock=lambda: now[0])
    k = p.key("turn-a", "w1")
    assert k == p.key("turn-b", "w1") == "job:w1"
    p.begin(k)
    p.add(k)
    now[0] = 300
    p.begin(k)                                       # the next segment keeps the first segment's clock
    p.add(k)
    now[0] = run_progress.PROGRESS_AFTER + 1
    assert p.add(k) == 3 and p.due(k)


def test_the_wait_is_read_at_each_call(monkeypatch):
    monkeypatch.setattr(run_progress, "PROGRESS_AFTER", 0.0)
    p = RunProgress(clock=lambda: 5.0)
    k = p.key("t", None)
    p.begin(k)
    p.add(k)
    assert p.due(k)


# ---------------------------------------------------------------------------------------------------------------- end-to-end integration tests
BODY = {"messages": [{"role": "user", "content": "hi"}]}


def make_slow_tool(name="browser_open", delay=0.06):
    async def handler(args):
        await asyncio.sleep(delay)
        return "browser done"
    return Tool(name, name, {"type": "object"}, handler)


def make_fast_tool(name="calc", delay=0.06):
    async def handler(args):
        await asyncio.sleep(delay)
        return "calc done"
    return Tool(name, name, {"type": "object"}, handler)


async def test_ack_emitted_for_slow_chat_turn(monkeypatch):
    monkeypatch.setattr("assistant.turn_engine.ACK_SECONDS", 0.02)
    fn = scripted([ToolCall("c1", "web_search", '{"url":"https://example.com"}'), Done("tool_calls")],
                  [TextDelta("Page loaded"), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("web_search", 0.06)], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    assert any("Got it, on it now<message_break/>" in t for t in text_events)
    assert any("Page loaded" in t for t in text_events)


async def test_ack_emitted_in_korean_for_slow_chat_turn(monkeypatch):
    monkeypatch.setattr("assistant.turn_engine.ACK_SECONDS", 0.02)
    fn = scripted([ToolCall("c1", "delegate", '{"task":"search"}'), Done("tool_calls")],
                  [TextDelta("완료했어요"), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("delegate", 0.06)], stream_fn=fn)
    ko_headers = {**AUTH, "Accept-Language": "ko"}
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=ko_headers) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=ko_headers) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    assert any("알겠어요, 지금 할게요<message_break/>" in t for t in text_events)
    assert any("완료했어요" in t for t in text_events)


async def test_no_ack_for_quick_answer(monkeypatch):
    monkeypatch.setattr("assistant.turn_engine.ACK_SECONDS", 0.02)
    fn = scripted([TextDelta("Immediate text answer"), Done("stop")])
    app = create_app(settings(), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    assert not any("Got it, on it now" in t for t in text_events)
    assert any("Immediate text answer" in t for t in text_events)


async def test_no_ack_for_fast_tool(monkeypatch):
    monkeypatch.setattr("assistant.turn_engine.ACK_SECONDS", 0.02)
    fn = scripted([ToolCall("c1", "calc", '{"expr":"1+1"}'), Done("tool_calls")],
                  [TextDelta("Result is 2"), Done("stop")])
    app = create_app(settings(), tools=[make_fast_tool("calc", 0.06)], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    assert not any("Got it, on it now" in t for t in text_events)
    assert "Result is 2" in "".join(text_events)


async def test_no_ack_when_model_produced_text_first(monkeypatch):
    monkeypatch.setattr("assistant.turn_engine.ACK_SECONDS", 0.02)
    fn = scripted([TextDelta("I will open the browser now: "), ToolCall("c1", "browser_open", '{}'), Done("tool_calls")],
                  [TextDelta("Opened."), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("browser_open", 0.06)], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    assert not any("Got it, on it now" in t for t in text_events)


async def test_progress_notice_once_for_long_chat_turn(monkeypatch):
    monkeypatch.setattr("assistant.run_progress.PROGRESS_AFTER", 0.035)
    monkeypatch.setattr("assistant.turn_engine.PROGRESS_MONITOR_INTERVAL", 0.01)
    fn = scripted([ToolCall("c1", "web_search", '{}'), Done("tool_calls")],
                  [TextDelta("All done."), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("web_search", 0.05)], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    progress_notices = [t for t in text_events if "Still working on it" in t]
    assert len(progress_notices) == 1


async def test_no_progress_notice_under_10_minutes(monkeypatch):
    monkeypatch.setattr("assistant.run_progress.PROGRESS_AFTER", 600.0)
    monkeypatch.setattr("assistant.turn_engine.PROGRESS_MONITOR_INTERVAL", 0.01)
    fn = scripted([ToolCall("c1", "browser_open", '{}'), Done("tool_calls")],
                  [TextDelta("Done quickly."), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("browser_open", 0.03)], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    assert not any("Still working on it" in t for t in text_events)


async def test_quiet_hours_background_suppressed_chat_sent(monkeypatch):
    monkeypatch.setattr("assistant.run_progress.PROGRESS_AFTER", 0.035)
    monkeypatch.setattr("assistant.turn_engine.PROGRESS_MONITOR_INTERVAL", 0.01)

    # 1. Chat turn during quiet hours still sends progress notice
    fn_chat = scripted([ToolCall("c1", "web_search", '{}'), Done("tool_calls")],
                       [TextDelta("Chat reply."), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("web_search", 0.05)], stream_fn=fn_chat)
    monkeypatch.setattr(app.state.proactive, "awake", lambda ts=None: False)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    assert any("Still working on it" in t for t in text_events)

    # 2. Background run during quiet hours does NOT send progress notice
    from test_conditional_grants import start
    app2 = create_app(settings(), tools=[make_slow_tool("web_search", 0.05)], stream_fn=fn_chat)
    monkeypatch.setattr(app2.state.proactive, "awake", lambda ts=None: False)
    async with Running(app2) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, ref = await start(c, app2, "schedule")
        await fire()
        notes = (await c.get("/api/notifications", headers=AUTH)).json()["items"]
    assert not any("Still working on it" in n.get("title", "") for n in notes)


# ---------------------------------------------------------------------------------------------------------------- one card per site per reply
async def _turn_answering(c, text, approve=True):
    """A chat turn over /api/turns; every approval card it asks is answered at once (the owner's tap)."""
    tid = (await c.post("/api/turns", json={"text": text})).json()["id"]
    events, after = [], 0
    for _ in range(200):
        r = (await c.get(f"/api/turns/{tid}", params={"after": after, "wait": 2})).json()
        events += r["events"]
        after = r["next"]
        for ev in r["events"]:
            if ev["type"] == "approval_request":
                await c.post(f"/api/approvals/{ev['approval_id']}", json={"approve": approve})
        if r["done"]:
            return events
    raise AssertionError("the turn did not finish")


async def test_two_opens_of_one_site_in_one_reply_ask_once(tmp_path):
    """Owner's Coupang case (2026-11-18): the owner's tap covers the site for that reply; a second identical card is not asked."""
    from assistant.store import Store
    Store(tmp_path / "a.db", None).save_credential("example", "Example", ["example.com"], {}, kind="login")   # a vault item named "example"
    fn = scripted([ToolCall("c1", "browser_open", '{"url":"https://shop.test.org/?x=example"}'), Done("tool_calls")],
                  [ToolCall("c2", "browser_open", '{"url":"https://shop.test.org/list?q=example"}'), Done("tool_calls")],
                  [TextDelta("done"), Done("stop")])
    app = create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False), tools=[make_slow_tool("browser_open", 0.01)],
                     stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await _turn_answering(c, "open the shop")
    cards = [e for e in events if e["type"] == "approval_request"]
    assert len(cards) == 1 and cards[0]["site"] == "shop.test.org"
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 2 and not any(r.get("is_error") for r in results)


# ---------------------------------------------------------------------------------------- the ack is shown, never stored (2026-11-18)
async def test_the_ack_is_shown_but_the_saved_reply_is_only_what_was_said(tmp_path, monkeypatch):
    """Owner's Coupang case: "알겠어요, 지금 할게요" was stored as the answer of a reply that never did the step."""
    monkeypatch.setattr("assistant.turn_engine.ACK_SECONDS", 0.02)
    fn = scripted([ToolCall("c1", "browser_open", '{"url":"https://example.com"}'), Done("tool_calls")],
                  [TextDelta("<message_break/>Page loaded"), Done("stop")])
    app = create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False), tools=[make_slow_tool("browser_open", 0.06)],
                     stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await _turn_answering(c, "open example")
        messages = (await c.get("/api/messages", headers=AUTH)).json()
    shown = "".join(e["text"] for e in events if e.get("type") == "text")
    assert "Got it, on it now" in shown                                   # the live screen still shows the line
    assert "<message_break/><message_break/>" not in shown                # and not two breaks in a row
    saved = [m["content"] for m in messages if m["role"] == "assistant"]
    assert saved and not any("Got it, on it now" in t for t in saved)     # the saved reply is not the ack
    assert saved[-1] == "Page loaded"


async def test_a_stopped_reply_leaves_its_unfinished_step_for_the_next_turn(tmp_path, monkeypatch):
    """Owner's Coupang case: a stopped reply's tool call never ran; the next turn must know that the request is still open."""
    seen: list[str] = []
    stalled = asyncio.Event()

    async def hang(args):
        stalled.set()
        await asyncio.sleep(60)
        return "never"

    script = iter([[ToolCall("c1", "slow_lookup", '{"query":"weather"}'), Done("tool_calls")],
                   [TextDelta("the weather is not in yet"), Done("stop")]])

    async def fn(provider, request, **kw):
        seen.append(" ".join(str(m.get("content")) for m in request.messages))
        for ev in next(script):
            yield ev

    app = create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False),
                     tools=[Tool("slow_lookup", "slow_lookup", {"type": "object"}, hang)], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "weather please"})).json()["id"]
        await asyncio.wait_for(stalled.wait(), 10)
        await c.delete(f"/api/turns/{tid}", headers=AUTH)                       # the owner stops the reply
        for _ in range(100):
            if (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 1})).json()["done"]:
                break
        await _turn_answering(c, "what are you doing")
    assert len(seen) == 2 and "still open" in seen[-1] and "slow_lookup" in seen[-1] and "weather" in seen[-1]


async def test_no_ack_while_the_owner_holds_the_tab(tmp_path, monkeypatch):
    """The owner pressed 직접 조작: "on it" would promise a step the tab does not let Kelo take; the step itself says that it waits."""
    from assistant.tools.browser import BrowserSession
    monkeypatch.setattr("assistant.turn_engine.ACK_SECONDS", 0.02)

    async def connect():
        raise RuntimeError("no browser in this test")
    sess = BrowserSession(connect=connect)
    sess.take_control("owner", "1")
    fn = scripted([ToolCall("c1", "browser_open", '{"url":"https://example.com"}'), Done("tool_calls")],
                  [TextDelta("I am waiting for you to hand the browser back"), Done("stop")])
    app = create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False), tools=[make_slow_tool("browser_open", 0.1)],
                     stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await _turn_answering(c, "open example")
    shown = "".join(e["text"] for e in events if e.get("type") == "text")
    assert "Got it, on it now" not in shown and "waiting for you" in shown
