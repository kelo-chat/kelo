"""Tests for GET /api/did unified timeline (actions + task log), merge, dedupe, paging, and i18n."""

from __future__ import annotations

import httpx
import pytest

from assistant import i18n
from assistant.done_once import ActionLedger
from test_prompt import talk_app
from test_server import AUTH, Running


async def test_did_validation_and_auth(tmp_path):
    app = talk_app(tmp_path, [])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as client:
        # Defaults
        r = await client.get("/api/did")
        assert r.status_code == 200
        assert r.json() == {"items": [], "next_before": None}

        # Days validation
        assert (await client.get("/api/did?days=0")).status_code == 422
        assert (await client.get("/api/did?days=31")).status_code == 422

    # Auth check
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as unauth_client:
        assert (await unauth_client.get("/api/did")).status_code in (401, 403)


async def test_did_merge_and_classification(tmp_path):
    app = talk_app(tmp_path, [])
    ledger = app.state.done_once.ledger
    store = ledger.store
    act = app.state.appctx.activity

    now = 1794744000.0
    ledger.clock = lambda: now
    act._clock = lambda: now

    # 1. Action from ledger
    ledger.record(
        run_id="r1",
        task_id=None,
        turn_id="t1",
        source="chat",
        tool="email_send",
        kind="send",
        connector="gmail",
        targets=["alice@example.com"],
        fingerprint="fp1",
        state="done",
    )

    # 2. Action with site target from ledger
    ledger.record(
        run_id="r2",
        task_id=None,
        turn_id="t2",
        source="schedule:5",
        tool="browser_click",
        kind="send",
        connector="",
        targets=["site:shop.com"],
        fingerprint="fp2",
        state="done",
    )

    # 3. Finished chat reply from task log (without browser, without actions)
    tid_chat = act.start_task("오늘 날씨 알려줘", source="chat")
    act.finish_task(tid_chat, "done", answer="맑고 화창해요.")

    # 4. Browser task from task log (has approval with site and browser tool)
    tid_browser = act.start_task("항공권 검색", source="background")
    act.log_approval("app_1", task_id=tid_browser, tool="browser_click", label="검색", site="airline.com", detail="click")
    act.finish_task(tid_browser, "done", answer="검색 완료")

    # 5. Read/job task from task log (schedule run)
    tid_sched = act.start_task("아침 브리핑", source="schedule:10")
    act.finish_task(tid_sched, "done", answer="브리핑 완료")

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as client:
        r = await client.get("/api/did?days=7", headers={"Accept-Language": "ko-KR,ko;q=0.9"})
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 5

        kinds = {it["kind"] for it in items}
        assert kinds == {"action", "reply", "browser", "task"}

        # Check action rows
        action_alice = next(it for it in items if "alice@example.com" in it["title"])
        assert action_alice["kind"] == "action"
        assert action_alice["source"] == "chat"
        assert action_alice["connector"] == "gmail"
        assert action_alice["outcome"] == "done"

        action_shop = next(it for it in items if "shop.com" in it["title"])
        assert action_shop["kind"] == "action"
        assert action_shop["source"] == "schedule"
        assert action_shop["site"] == "shop.com"
        assert action_shop["outcome"] == "done"

        # Check reply row
        reply_row = next(it for it in items if it["kind"] == "reply")
        assert reply_row["source"] == "chat"
        assert reply_row["title"] == "오늘 날씨 알려줘"
        assert reply_row["detail"] == "맑고 화창해요."

        # Check browser row
        browser_row = next(it for it in items if it["kind"] == "browser")
        assert browser_row["source"] == "job"
        assert browser_row["site"] == "airline.com"
        assert browser_row["title"] == "항공권 검색"

        # Check task row
        task_row = next(it for it in items if it["kind"] == "task")
        assert task_row["source"] == "schedule"
        assert task_row["title"] == "아침 브리핑"


async def test_did_deduplication(tmp_path):
    app = talk_app(tmp_path, [])
    ledger = app.state.done_once.ledger
    act = app.state.appctx.activity

    t0 = 1000.0
    act._clock = lambda: t0
    ledger.clock = lambda: t0

    # Case A: Task and Action have SAME task_id and are within 5 s
    tid_dup = act.start_task("Send email to Bob", source="chat")
    # Action happens at t0 + 2.0s
    ledger.clock = lambda: t0 + 2.0
    act._clock = lambda: t0 + 3.0
    ledger.record(
        run_id="r_dup",
        task_id=tid_dup,
        turn_id="t_dup",
        source="chat",
        tool="email_send",
        kind="send",
        connector="gmail",
        targets=["bob@example.com"],
        fingerprint="fp_dup",
        state="done",
    )
    act.finish_task(tid_dup, "done", answer="Sent email")

    # Case B: Task and Action have SAME task_id but are > 5 s apart (e.g. 20s)
    tid_nodup = act.start_task("Long background run", source="worker")
    ledger.clock = lambda: t0 + 30.0
    act._clock = lambda: t0 + 30.0
    ledger.record(
        run_id="r_nodup",
        task_id=tid_nodup,
        turn_id="t_nodup",
        source="worker",
        tool="email_send",
        kind="send",
        connector="gmail",
        targets=["carol@example.com"],
        fingerprint="fp_nodup",
        state="done",
    )
    # Task started at t0, finished at t0 + 50.0s
    act._clock = lambda: t0 + 50.0
    act.finish_task(tid_nodup, "done", answer="Job completed")

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as client:
        # Request with wide days window so all fit
        ledger.clock = lambda: t0 + 60.0
        r = await client.get("/api/did?days=7")
        assert r.status_code == 200
        items = r.json()["items"]

        # For Case A (tid_dup): the task row was dropped, only the action row remains
        dup_items = [it for it in items if it.get("task_id") == tid_dup]
        assert len(dup_items) == 1
        assert dup_items[0]["kind"] == "action"

        # For Case B (tid_nodup): both the action row and the task row remain
        nodup_items = [it for it in items if it.get("task_id") == tid_nodup]
        assert len(nodup_items) == 2
        kinds = {it["kind"] for it in nodup_items}
        assert kinds == {"action", "task"}


async def test_did_paging_and_ordering(tmp_path):
    app = talk_app(tmp_path, [])
    ledger = app.state.done_once.ledger
    act = app.state.appctx.activity

    base = 1000.0
    # Create 5 distinct events at t = 1000, 1010, 1020, 1030, 1040
    for i in range(5):
        t = base + i * 10.0
        act._clock = lambda t=t: t
        tid = act.start_task(f"Task {i}", source="chat")
        act.finish_task(tid, "done", answer=f"Result {i}")

    ledger.clock = lambda: base + 100.0
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as client:
        # Page 1: limit 2
        r1 = await client.get("/api/did?days=7&limit=2")
        assert r1.status_code == 200
        data1 = r1.json()
        assert len(data1["items"]) == 2
        assert data1["items"][0]["title"] == "Task 4"
        assert data1["items"][1]["title"] == "Task 3"
        assert data1["next_before"] == data1["items"][1]["at"]

        # Page 2: before = next_before, limit 2
        r2 = await client.get(f"/api/did?days=7&limit=2&before={data1['next_before']}")
        assert r2.status_code == 200
        data2 = r2.json()
        assert len(data2["items"]) == 2
        assert data2["items"][0]["title"] == "Task 2"
        assert data2["items"][1]["title"] == "Task 1"
        assert data2["next_before"] == data2["items"][1]["at"]

        # Page 3: before = next_before, limit 2 (last page)
        r3 = await client.get(f"/api/did?days=7&limit=2&before={data2['next_before']}")
        assert r3.status_code == 200
        data3 = r3.json()
        assert len(data3["items"]) == 1
        assert data3["items"][0]["title"] == "Task 0"
        assert data3["next_before"] is None
