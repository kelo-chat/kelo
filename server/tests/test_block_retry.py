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
def test_worker_report_blocked_site_refused_has_no_retry_and_slowdown_keeps_wake_at(tmp_path):
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

        # On a blocked site (site_refused), no retry_after_min is passed -> stops at once, wake_at=0
        res = asyncio.run(report_tool.handler({
            "status": "blocked",
            "reason": "site_refused",
            "summary": "Access denied by example.test",
        }))
        assert "Report received" in res
        assert workers._live[wid].report.get("reason") == "site_refused"
        assert workers._live[wid].report.get("retry_after_min") is None

        # Settle
        asyncio.run(workers._settle(wid, ("blocked", "Access denied by example.test", "", False), None))
        row = workers._row(wid)
        assert row["status"] == "blocked"
        assert row["wake_at"] == 0.0
        assert row["retries"] == 0

        # Public API shows status "blocked" (not "waiting")
        pub = workers.get(wid)
        assert pub["status"] == "blocked"
        assert not pub.get("wake_at")

        # Timer checks: nothing to wake
        clock.advance(1000.0)
        assert workers.wake_tick() == []

        # But when retry_after_min IS passed (e.g. connector slowdown), clamping and wake_at still work
        wid2 = "w0002"
        now2 = clock()
        store._run(
            "INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, deadline_at, minutes, max_steps, token_cap) "
            "VALUES(?, 'general', 'Connector job', 'Sync', 'running', 'task:w0002', ?, ?, ?, 60, 200, 1000000)",
            (wid2, now2, now2, now2 + 3600),
        )
        current_worker.set(wid2)
        workers._live[wid2] = _Live()
        res2 = asyncio.run(report_tool.handler({"status": "blocked", "summary": "Rate limited", "retry_after_min": 15}))
        assert workers._live[wid2].report["retry_after_min"] == 15
        asyncio.run(workers._settle(wid2, ("blocked", "Rate limited", "", False), None))
        row2 = workers._row(wid2)
        assert row2["status"] == "blocked"
        assert row2["wake_at"] == pytest.approx(now2 + 900.0)
        pub2 = workers.get(wid2)
        assert pub2["status"] == "waiting"
    finally:
        current_worker.reset(token)


# ------------------------------------------------------------------ 2. Handoff on site refusal
def test_handoff_on_site_refusal_gives_takeover_and_no_retry_time(tmp_path):
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
        "INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, deadline_at, minutes, max_steps, token_cap, last_url) "
        "VALUES(?, 'browser', 'Shop job', 'Buy gift', 'running', 'task:w0002', ?, ?, ?, 60, 200, 1000000, 'https://shop.example/item')",
        (wid, now, now, now + 3600),
    )

    token = current_worker.set(wid)
    try:
        workers._live[wid] = _Live()
        report_tool = workers.report_tool()
        asyncio.run(report_tool.handler({
            "status": "blocked",
            "reason": "site_refused",
            "summary": "Site returned HTTP 403",
        }))
        asyncio.run(workers._settle(wid, ("blocked", "Site returned HTTP 403", "", False), None))

        assert len(handoffs) == 1
        row, msg, head = handoffs[0]
        assert HANDOFF_MARK in msg
        assert "did not let Kelo's browser in" in msg
        assert "take over the browser" in msg
        assert "retry by itself" not in msg
    finally:
        current_worker.reset(token)


# ------------------------------------------------------------------ 3. browser_forget_site
async def test_browser_forget_site_removes_registrable_cookies_storage_and_asks_by_card():
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

    # The owner's yes is the tool's approval card (requires_approval), so forget_site itself just clears.
    out4 = await sess.forget_site("https://www.example.test/shop")
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
    assert tool.requires_approval and "user_said" not in tool.parameters["properties"]


# ------------------------------------------------------------------ 4. blocked_note wording
def test_blocked_note_wording():
    # 1. Job variant: any count -> one text
    for count in (1, 2, 3):
        note = blocked_note("example.test", 'HTTP 403 "Access Denied"', job=True, count=count)
        assert "is blocking Kelo's browser: it answered HTTP 403 \"Access Denied\"" in note
        assert "Stop on this site: do not retry, open another address of it or switch to its mobile site." in note
        assert "Call report(status='blocked', reason='site_refused', summary=...)" in note
        assert "the user can take over the browser to sign in or get past it." in note
        assert "retry_after_min" not in note
        assert "clear that site's cookies" not in note

    # 2. Main turn (chat variant)
    note_chat = blocked_note("example.test", 'HTTP 403 "Access Denied"', job=False)
    assert "is blocking Kelo's browser: it answered HTTP 403 \"Access Denied\"" in note_chat
    assert "Do not retry." in note_chat
    assert "Tell the user plainly that it refused Kelo's browser and that they can take over the browser to sign in or get past it." in note_chat
    assert "retry in 10 minutes" not in note_chat


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
