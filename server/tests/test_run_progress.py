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
    fn = scripted([ToolCall("c1", "browser_open", '{"url":"https://example.com"}'), Done("tool_calls")],
                  [TextDelta("Page loaded"), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("browser_open", 0.06)], stream_fn=fn)
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
    fn = scripted([ToolCall("c1", "browser_open", '{}'), Done("tool_calls")],
                  [TextDelta("All done."), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("browser_open", 0.05)], stream_fn=fn)
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
    fn_chat = scripted([ToolCall("c1", "browser_open", '{}'), Done("tool_calls")],
                       [TextDelta("Chat reply."), Done("stop")])
    app = create_app(settings(), tools=[make_slow_tool("browser_open", 0.05)], stream_fn=fn_chat)
    monkeypatch.setattr(app.state.proactive, "awake", lambda ts=None: False)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    text_events = [e["text"] for e in events if e.get("type") == "text"]
    assert any("Still working on it" in t for t in text_events)

    # 2. Background run during quiet hours does NOT send progress notice
    from test_conditional_grants import start
    app2 = create_app(settings(), tools=[make_slow_tool("browser_open", 0.05)], stream_fn=fn_chat)
    monkeypatch.setattr(app2.state.proactive, "awake", lambda ts=None: False)
    async with Running(app2) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, ref = await start(c, app2, "schedule")
        await fire()
        notes = (await c.get("/api/notifications", headers=AUTH)).json()["items"]
    assert not any("Still working on it" in n.get("title", "") for n in notes)
