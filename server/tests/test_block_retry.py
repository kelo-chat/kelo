"""Tests for blocked site handling: retry once after 10 minutes, then offer to clear cookies.

Brief requirements:
1. First block in a job: reports blocked with retry_after_min (clamped 5-30). Worker store keeps wake_at.
   Timer in Workers resumes automatically at wake_at with note:
   "The site blocked the browser earlier; this is the one automatic retry. Go back to the site's home page and continue like a person."
   At most ONE automatic retry per job.
   Handoff mentions retry local time.
   API exposes wake_at and blocked job with wake_at shows status "waiting".
2. Second block in same job: reports needs_input asking whether to clear site cookies and retry.
3. MAIN tool browser_forget_site: removes cookies of site's registrable domain (all subdomains) from default context,
   clears site data for https origins via Storage.clearDataForOrigin (bounded 5s each), drops SITE_STATE.
   Never touches other sites. Refuses without user agreement.
4. blocked_note wording for first / second block in a job and in a main turn.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx
import pytest

from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.store import Store
from assistant.tools.browser import BrowserSession, browser_owner, browser_tools, site_of
from assistant.tools.browser_more import BrowserExtras
from assistant.tools.challenge import blocked_note
from assistant.workers import HANDOFF_MARK, Limits, WorkerHost, Workers, _Live, current_worker

from test_agent_profile import app_settings
from test_server import AUTH, Running, follow


class FakeClock:
    def __init__(self, start: float = 1700000000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeCtx:
    def __init__(self, cookies: list[dict[str, Any]] | None = None):
        self._cookies = list(cookies or [])
        self.pages: list[Any] = []

    async def cookies(self) -> list[dict[str, Any]]:
        return list(self._cookies)

    async def clear_cookies(self, *, domain: Any = None, name: Any = None, path: Any = None) -> None:
        if domain is not None:
            if isinstance(domain, re.Pattern):
                self._cookies = [c for c in self._cookies if not domain.search(str(c.get("domain") or ""))]
            else:
                self._cookies = [c for c in self._cookies if str(c.get("domain") or "") != str(domain)]
        else:
            self._cookies.clear()


class FakePage:
    def __init__(self, url: str = "about:blank", ctx: FakeCtx | None = None):
        self.url = url
        self.context = ctx or FakeCtx()
        self.context.pages.append(self)
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def title(self) -> str:
        return "Test Page"


class FakeCdp:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, dict(params or {})))
        return {}


# ------------------------------------------------------------------ 1. Worker retry & timer
def test_worker_report_blocked_retry_stores_wake_at_and_timer_resumes_once(tmp_path):
    store = Store(str(tmp_path / "kelo.db"))
    clock = FakeClock()
    handoffs = []

    async def handoff(row, msg, head):
        handoffs.append((row, msg, head))

    host = WorkerHost(
        start_turn=lambda *a: None,
        cancel_turn=lambda t: None,
        handoff=handoff,
        emit=lambda *a: asyncio.sleep(0),
        tools=lambda: [],
    )
    workers = Workers(store, host, Limits(), clock=clock)

    wid = "w0001"
    now = clock()
    store._run(
        "INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, deadline_at, minutes, max_steps, token_cap) "
        "VALUES(?, 'browser', 'Test job', 'Buy item', 'running', 'task:w0001', ?, ?, ?, 60, 200, 1000000)",
        (wid, now, now, now + 3600),
    )

    token = current_worker.set(wid)
    try:
        workers._live[wid] = _Live()
        report_tool = workers.report_tool()

        # Test out-of-range clamping:
        # A) retry_after_min=2 clamped to 5
        res = asyncio.run(report_tool.handler({"status": "blocked", "summary": "Access denied", "retry_after_min": 2}))
        assert "Report received" in res
        assert workers._live[wid].report["retry_after_min"] == 5

        # B) retry_after_min=50 clamped to 30
        res = asyncio.run(report_tool.handler({"status": "blocked", "summary": "Access denied", "retry_after_min": 50}))
        assert "Report received" in res
        assert workers._live[wid].report["retry_after_min"] == 30

        # C) Valid 10 minutes
        res = asyncio.run(report_tool.handler({"status": "blocked", "summary": "Access denied", "retry_after_min": 10}))
        assert "Report received" in res
        assert workers._live[wid].report["retry_after_min"] == 10

        # Now settle the blocked worker
        asyncio.run(workers._settle(wid, ("blocked", "Access denied", "", False), None))

        # Check wake_at stored
        row = workers._row(wid)
        assert row["status"] == "blocked"
        assert row["wake_at"] == pytest.approx(now + 600.0)
        assert row["retries"] == 0

        # Public API shows status "waiting" and wake_at float
        pub = workers.get(wid)
        assert pub["status"] == "waiting"
        assert pub["wake_at"] == pytest.approx(now + 600.0)

        # Timer checks: before wake_at, nothing resumes
        clock.advance(300.0)
        resumed = workers.wake_tick()
        assert resumed == []
        assert workers._row(wid)["status"] == "blocked"

        # Timer checks: at or after wake_at, resumes exactly once
        clock.advance(305.0)  # now is 605s after start
        resumed = workers.wake_tick()
        assert resumed == [wid]

        # Worker is resumed, retries count incremented, wake_at cleared to 0
        row_after = workers._row(wid)
        assert row_after["status"] == "running"
        assert row_after["wake_at"] == 0.0
        assert row_after["retries"] == 1

        # Calling timer again does NOT resume again (resumes exactly once)
        clock.advance(100.0)
        assert workers.wake_tick() == []

        # Second blocked report: does NOT schedule another retry because retries >= 1
        workers._live[wid] = _Live()
        asyncio.run(report_tool.handler({"status": "blocked", "summary": "Blocked again", "retry_after_min": 10}))
        asyncio.run(workers._settle(wid, ("blocked", "Blocked again", "", False), None))

        row_second = workers._row(wid)
        assert row_second["status"] == "blocked"
        assert row_second["wake_at"] == 0.0  # NOT scheduled!
        assert row_second["retries"] == 1
    finally:
        current_worker.reset(token)


# ------------------------------------------------------------------ 2. Handoff mentions retry time
def test_handoff_mentions_retry_time(tmp_path):
    store = Store(str(tmp_path / "kelo.db"))
    clock = FakeClock()
    handoffs = []

    async def handoff(row, msg, head):
        handoffs.append((row, msg, head))

    host = WorkerHost(
        start_turn=lambda *a: None,
        cancel_turn=lambda t: None,
        handoff=handoff,
        emit=lambda *a: asyncio.sleep(0),
        tools=lambda: [],
    )
    workers = Workers(store, host, Limits(), clock=clock)

    wid = "w0002"
    now = clock()
    store._run(
        "INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, deadline_at, minutes, max_steps, token_cap) "
        "VALUES(?, 'browser', 'Shop job', 'Buy gift', 'running', 'task:w0002', ?, ?, ?, 60, 200, 1000000)",
        (wid, now, now, now + 3600),
    )

    token = current_worker.set(wid)
    try:
        workers._live[wid] = _Live()
        report_tool = workers.report_tool()
        asyncio.run(report_tool.handler({"status": "blocked", "summary": "Site returned HTTP 403", "retry_after_min": 10}))
        asyncio.run(workers._settle(wid, ("blocked", "Site returned HTTP 403", "", False), None))

        assert len(handoffs) == 1
        row, msg, head = handoffs[0]
        assert HANDOFF_MARK in msg
        # Handoff must mention that the job will retry by itself at HH:MM
        expected_time = workers._format_time(now + 600.0)
        assert f"retry by itself at {expected_time}" in msg
        assert f"try again at {expected_time}" in msg

        # Second block handoff (needs_input with cookies) mentions quick_replies
        handoffs.clear()
        workers._live[wid] = _Live()
        asyncio.run(report_tool.handler({"status": "needs_input", "summary": "Site keeps blocking. Clear cookies?"}))
        asyncio.run(workers._settle(wid, ("needs_input", "Site keeps blocking. Clear cookies?", "", False), None))
        assert len(handoffs) == 1
        _, msg2, _ = handoffs[0]
        assert "quick_replies" in msg2
        assert "browser_forget_site" in msg2

        # Third block handoff (blocked without retry) mentions live view / another shop
        handoffs.clear()
        workers._live[wid] = _Live()
        asyncio.run(report_tool.handler({"status": "blocked", "summary": "Blocked again permanently"}))
        asyncio.run(workers._settle(wid, ("blocked", "Blocked again permanently", "", False), None))
        assert len(handoffs) == 1
        _, msg3, _ = handoffs[0]
        assert "site does not let Kelo's browser in right now" in msg3
        assert "live view" in msg3
    finally:
        current_worker.reset(token)


# ------------------------------------------------------------------ 3. browser_forget_site
async def test_browser_forget_site_removes_registrable_cookies_storage_and_refuses_without_consent():
    ctx = FakeCtx([
        {"name": "c1", "value": "1", "domain": "example.test", "path": "/"},
        {"name": "c2", "value": "2", "domain": "www.example.test", "path": "/"},
        {"name": "c3", "value": "3", "domain": "m.example.test", "path": "/"},
        {"name": "c4", "value": "4", "domain": "other.test", "path": "/"},
    ])
    page = FakePage("https://www.example.test/cart", ctx)
    cdp = FakeCdp()

    async def connect():
        return page

    sess = BrowserSession(connect=connect)
    sess._browser = None  # use page.context
    sess.cdp_session = lambda p: asyncio.sleep(0, result=cdp)

    # Set up SITE_STATE
    sess._site_states["example.test"] = ({"cookies": [], "origins": []}, time.monotonic())
    sess._site_states["other.test"] = ({"cookies": [], "origins": []}, time.monotonic())

    # Case A: Refuses without user agreement
    # 1) Empty agreement
    out1 = await sess.forget_site("example.test", user_said="")
    assert out1.startswith("Error:") and "user agreement is required" in out1
    assert len(await ctx.cookies()) == 4
    assert "example.test" in sess._site_states

    # 2) Refusal / stop words
    out2 = await sess.forget_site("example.test", user_said="그만해")
    assert out2.startswith("Error:") and ("declined" in out2 or "stop" in out2)
    out3 = await sess.forget_site("example.test", user_said="no, stop")
    assert out3.startswith("Error:") and ("declined" in out3 or "stop" in out3)
    assert len(await ctx.cookies()) == 4
    assert "example.test" in sess._site_states

    # Case B: With user agreement
    out4 = await sess.forget_site("https://www.example.test/shop", user_said="쿠키 지우고 다시 시도해줘")
    assert "Removed 3 cookies for example.test" in out4
    assert "cleared its site data" in out4
    assert "browser_login" in out4

    # Verify only example.test registrable domain cookies were removed:
    remaining_cookies = await ctx.cookies()
    assert len(remaining_cookies) == 1
    assert remaining_cookies[0]["domain"] == "other.test"

    # Verify SITE_STATE for example.test was dropped, but other.test preserved
    assert "example.test" not in sess._site_states
    assert "other.test" in sess._site_states

    # Verify Storage.clearDataForOrigin was called for the https origins of example.test
    storage_calls = [p for m, p in cdp.calls if m == "Storage.clearDataForOrigin"]
    cleared_origins = {c["origin"] for c in storage_calls}
    assert "https://example.test" in cleared_origins
    assert "https://www.example.test" in cleared_origins
    assert "https://m.example.test" in cleared_origins
    assert not any("other.test" in o for o in cleared_origins)

    # Verify tool exposed via BrowserExtras
    extras = BrowserExtras(sess)
    tools = {t.name: t for t in extras.tools()}
    assert "browser_forget_site" in tools
    tool = tools["browser_forget_site"]
    res_tool = await tool.handler({"site": "example.test", "user_said": ""})
    assert res_tool.startswith("Error:") and "user agreement is required" in res_tool


# ------------------------------------------------------------------ 4. blocked_note wording
def test_blocked_note_wording():
    # 1. First block in a job
    note1 = blocked_note("example.test", 'HTTP 403 "Access Denied"', job=True, count=1)
    assert "is blocking Kelo's browser" in note1
    assert 'HTTP 403 "Access Denied"' in note1
    assert "Do not retry" in note1
    assert "report(status='blocked'" in note1
    assert "retry_after_min=10" in note1

    # 2. Second block in a job
    note2 = blocked_note("example.test", 'HTTP 403 "Access Denied"', job=True, count=2)
    assert "is blocking Kelo's browser" in note2
    assert "keeps blocking" in note2
    assert 'HTTP 403 "Access Denied"' in note2
    assert "report(status='needs_input'" in note2
    assert "clear that site's cookies" in note2

    # 3. Main turn (not a job)
    note3 = blocked_note("example.test", 'HTTP 403 "Access Denied"', job=False)
    assert "is blocking Kelo's browser" in note3
    assert 'HTTP 403 "Access Denied"' in note3
    assert "Do not retry" in note3
    assert "retry in 10 minutes as a background job" in note3

    # 4. Third block in a job
    note4 = blocked_note("example.test", 'HTTP 403 "Access Denied"', job=True, count=3)
    assert "is blocking Kelo's browser" in note4
    assert 'HTTP 403 "Access Denied"' in note4
    assert "Do not retry" in note4
    assert "report(status='blocked'" in note4
    assert "without retry_after_min" in note4


# ------------------------------------------------------------------ 5. Browser block limits and short-circuit
def test_browser_block_limits_and_short_circuit():
    sess = BrowserSession(connect=lambda: None)
    # When not a job: limit is 2
    assert not sess._is_job()
    assert sess._block_limit() == 2

    # When in a job: limit is 3
    token = current_worker.set("w1234")
    try:
        assert sess._is_job()
        assert sess._block_limit() == 3

        # Simulate blocks in job
        host = "example.test"
        t_key = sess._task()
        now = sess.clock()

        # Block 1
        sess._blocked[(t_key, host)] = (1, now)
        assert sess.blocked_site("https://example.test") == ""

        # Block 2
        sess._blocked[(t_key, host)] = (2, now)
        assert sess.blocked_site("https://example.test") == ""

        # Block 3: short-circuits
        sess._blocked[(t_key, host)] = (3, now)
        short = sess.blocked_site("https://example.test")
        assert "Blocked again" in short
        assert "example.test" in short
    finally:
        current_worker.reset(token)


# ------------------------------------------------------------------ 5. API exposes wake_at and waiting status
async def test_api_exposes_wake_at_and_waiting_status(tmp_path):
    settings = app_settings(tmp_path)
    app = create_app(settings, tools=[], stream_fn=lambda *a: [], with_worker_tools=True)
    store = Store(settings.db_path)
    now = time.time()
    store._run(
        "INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, deadline_at, minutes, max_steps, token_cap, wake_at) "
        "VALUES('wtest1', 'browser', 'Waiting job', 'task', 'blocked', 'task:wtest1', ?, ?, ?, 60, 200, 1000000, ?)",
        (now, now, now + 3600, now + 600.0),
    )

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        # GET /api/workers
        res = await c.get("/api/workers")
        assert res.status_code == 200
        items = res.json()["items"]
        assert len(items) == 1
        w = items[0]
        assert w["id"] == "wtest1"
        assert w["status"] == "waiting"
        assert w["wake_at"] == pytest.approx(now + 600.0)

        # GET /api/workers/{id}
        res_one = await c.get("/api/workers/wtest1")
        assert res_one.status_code == 200
        w_detail = res_one.json()
        assert w_detail["status"] == "waiting"
        assert w_detail["wake_at"] == pytest.approx(now + 600.0)

        # Filter by status=waiting
        res_wait = await c.get("/api/workers", params={"status": "waiting"})
        assert len(res_wait.json()["items"]) == 1

        # Filter by status=blocked still finds it in DB
        res_block = await c.get("/api/workers", params={"status": "blocked"})
        assert len(res_block.json()["items"]) == 1
