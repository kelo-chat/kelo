"""A stop by Kelo always reports what was done, where it stopped and why, once (owner 2026-11-20, D3): the record is built from the
server's own records (the steps of the tasks, the approvals), the report goes through the delivered-once records (deliveries.py), and
when the model answers it writes the report from that record; when it cannot be reached the four plain lines are the report.
Fake models and an in-memory store only."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx

import assistant.server as S
from assistant import tool_labels
from assistant.activity import Activity
from assistant.deliveries import Deliveries
from assistant.stop_reports import StopReports
from assistant.store import Store
from assistant.turn_resume import model_wait_tick

from test_resume import AUTH, Model, Running, app_for, tasks, until


def _steps(*pairs):
    return json.dumps({"steps": [{"tool": tool, "ok": ok} for tool, ok in pairs], "outside": False})


def _chat_handler(store):
    async def deliver(t):
        p = t.payload
        store.add_message("assistant", p["text"], once_key=t.once_key)
        n = p.get("notify")
        if n:
            Activity(store).notify(n["type"], n["title"], n.get("body") or "", task_id=p.get("task_id"), once_key=t.once_key)
    return deliver


def _messages(store, key):
    return store._all("SELECT content FROM messages WHERE once_key = ?", (key,))


def test_the_report_names_the_done_steps_the_stop_point_and_the_reason(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    a = Activity(store)
    tid = a.start_task("Shop order", "chat")
    a.set_steps(tid, _steps(("browser_read", True), ("browser_read", True), ("browser_click", False)))
    sr = StopReports(store, Deliveries(store), None)
    key = sr.put(tid, "Stopped for good: the server restarted 4 times while this was running")
    assert key == f"task-stopped:{tid}"
    rec = store._all("SELECT payload FROM deliveries WHERE once_key = ?", (key,))[0]
    text = json.loads(rec["payload"])["text"]
    assert text.splitlines()[0] == "Kelo stopped this: Shop order"
    assert "Done so far: " + tool_labels.label("browser_read") in text      # the repeated step is named once
    assert text.count(tool_labels.label("browser_read")) == 1
    assert "Stopped at: " + tool_labels.label("browser_click") in text      # the step had no good result: it was cut mid-step
    assert "Why: Stopped for good: the server restarted 4 times" in text


def test_a_stop_before_any_step_says_nothing_was_done(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    tid = Activity(store).start_task("Mail", "chat")
    sr = StopReports(store, Deliveries(store), None)
    key = sr.put(tid, "Failed: the AI model could not be reached")
    text = json.loads(store._all("SELECT payload FROM deliveries WHERE once_key = ?", (key,))[0]["payload"])["text"]
    assert "No step had finished yet." in text and "Stopped at" not in text and "Stopped after" not in text


def test_approvals_of_the_chain_are_done_steps_too(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    a = Activity(store)
    first = a.start_task("Order", "chat")
    a.set_steps(first, _steps(("browser_read", True)))
    second = a.start_task("Order", "chat")
    a.set_resume(second, {"kind": "chat"}, restarts=1, resumed_from=first)
    a.set_steps(second, _steps(("browser_click", True)))
    store._run("INSERT INTO approval_log(id, task_id, tool, label, site, decision, asked_at) VALUES(?,?,?,?,?,?,?)",
               ("ap1", second, "browser_click", "Pay", "shop.example.com", "allowed_once", time.time()))
    sr = StopReports(store, Deliveries(store), None)
    facts = sr.facts(second)
    assert [c["title"] for c in sr.chain(second)] == ["Order", "Order"]
    assert facts.done == (tool_labels.label("browser_read"), tool_labels.label("browser_click"),
                          f"{tool_labels.label('browser_click')} · shop.example.com")


async def test_the_report_reaches_the_chat_once_across_a_restart(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    tid = Activity(store).start_task("Shop", "chat")
    key = StopReports(store, Deliveries(store), None).put(tid, "Why text")
    first = Deliveries(store)
    first.on("chat", _chat_handler(store))
    assert await first.drain() == 1 and await first.drain() == 0
    second = Deliveries(store, boot="other")   # a restart: the record is not taken again
    second.on("chat", _chat_handler(store))
    assert await second.drain() == 0
    assert len(_messages(store, key)) == 1
    assert len(store._all("SELECT id FROM notifications WHERE task_id = ?", (tid,))) == 1


async def test_a_model_that_answers_writes_the_report_from_the_record(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    tid = Activity(store).start_task("Shop", "chat")
    deliveries = Deliveries(store)
    deliveries.on("chat", _chat_handler(store))

    async def ask(system, user, **_):
        assert "done" in json.loads(user)["record"]
        return {"report": "Shop 주문을 멈췄어요."}

    sr = StopReports(store, deliveries, None, ask=ask)
    key = sr.put(tid, "Why text")
    sr.send_soon(key)
    await until(lambda: _messages(store, key))
    assert _messages(store, key)[0]["content"] == "Shop 주문을 멈췄어요."


async def test_a_model_that_cannot_be_reached_leaves_the_four_plain_lines(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    tid = Activity(store).start_task("Shop", "chat")
    deliveries = Deliveries(store)
    deliveries.on("chat", _chat_handler(store))

    async def ask(system, user, **_):
        return None

    sr = StopReports(store, deliveries, None, ask=ask)
    key = sr.put(tid, "Why text")
    sr.send_soon(key)
    await until(lambda: _messages(store, key))
    assert "Why: Why text" in _messages(store, key)[0]["content"]


async def test_the_restart_limit_stop_reports_once_in_the_chat(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    a = Activity(store)
    from assistant.goals import GOAL_MARK
    mid = store.add_message("user", GOAL_MARK + " work on goal #1")
    tid = a.start_task("Goal A", "handoff")
    a.set_resume(tid, {"kind": "handoff", "message_id": mid, "title": "Goal A", "head": "Your goal", "quiet": False,
                       "parent_task_id": None, "worker_id": ""}, restarts=3)
    m = Model("ok")   # the report's model answer is no JSON object: the four plain lines are the report
    async with Running(app_for(tmp_path, m)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await asyncio.sleep(0.3)
        key = f"task-stopped:{tid}"
        msgs = _messages(store, key)
        assert len(msgs) == 1
        assert "restarted 4 times" in msgs[0]["content"] and "Why:" in msgs[0]["content"]
        assert "Stopped at:" in msgs[0]["content"] or "No step had finished yet." in msgs[0]["content"]
        notes = [n for n in (await c.get("/api/notifications")).json()["items"] if n["type"] == "task_failed" and n["task_id"] == tid]
        assert len(notes) == 1
        assert (await tasks(c))[0]["status"] == "failed"
    assert len(_messages(store, key)) == 1


async def test_a_model_give_up_reports_what_was_done(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MODEL_WAIT_GIVE_UP", 60.0)
    store = Store(str(tmp_path / "a.db"))
    a = Activity(store)
    tid = a.start_task("Mail", "background", turn_id="t1")
    a.set_steps(tid, _steps(("mail_read", True)))
    a.set_resume(tid, {"kind": "background", "prompt": "x", "title": "Mail", "source": "background"})
    a.wait_for_model(tid, time.time() - 1)
    store._run("UPDATE tasks SET wait_since = ? WHERE id = ?", (time.time() - 3600, tid))
    deliveries = Deliveries(store)
    deliveries.on("chat", _chat_handler(store))
    sr = StopReports(store, deliveries, None)

    async def down():
        return False
    ctx = SimpleNamespace(activity=a, journal_model=lambda: None, limit_guard=SimpleNamespace(balance_out=lambda p: False,
                          until=lambda p: None), limit_notices=lambda w, n: None, limit_wait=lambda r: False, model_reachable=down,
                          resume_task=lambda r, why: "resumed", stop_reports=sr)
    out = await model_wait_tick(ctx, time.time())
    assert out == {tid: "failed"}
    await asyncio.sleep(0.05)
    await deliveries.drain()
    msgs = _messages(store, f"task-stopped:{tid}")
    assert len(msgs) == 1 and tool_labels.label("mail_read") in msgs[0]["content"]
    assert a.task(tid)["status"] == "failed"


async def test_a_balance_stop_reports_for_a_chat_too(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "a.db"))
    a = Activity(store)
    tid = a.start_task("Chat question", "chat", turn_id="t2")
    a.set_resume(tid, {"kind": "chat", "message_id": 0, "title": "Chat question"})
    a.wait_for_model(tid, time.time() - 1)
    deliveries = Deliveries(store)
    deliveries.on("chat", _chat_handler(store))
    sr = StopReports(store, deliveries, None)
    ctx = SimpleNamespace(activity=a, journal_model=lambda: ("p", "m"), limit_guard=SimpleNamespace(balance_out=lambda p: True,
                          until=lambda p: None), limit_notices=lambda w, n: None, limit_wait=lambda r: True,
                          model_reachable=None, resume_task=None, stop_reports=sr)
    out = await model_wait_tick(ctx, time.time())
    assert out == {tid: "failed"}
    await asyncio.sleep(0.05)
    await deliveries.drain()
    assert len(_messages(store, f"task-stopped:{tid}")) == 1


async def test_the_usage_limit_hold_that_runs_out_reports_for_a_chat_too(tmp_path):
    """The usage limit held the chat reply past its longest hold: the stop is reported once (decision 2 of S2.4, the usage-limit branch)."""
    from assistant import model_limits
    store = Store(str(tmp_path / "a.db"))
    a = Activity(store)
    tid = a.start_task("Chat question", "chat", turn_id="t3")
    a.set_resume(tid, {"kind": "chat", "message_id": 0, "title": "Chat question"})
    a.wait_for_model(tid, time.time() - 1)
    store._run("UPDATE tasks SET wait_since = ? WHERE id = ?", (time.time() - model_limits.MAX_HOLD - 60, tid))
    deliveries = Deliveries(store)
    deliveries.on("chat", _chat_handler(store))
    sr = StopReports(store, deliveries, None)
    ctx = SimpleNamespace(activity=a, journal_model=lambda: ("p", "m"), limit_guard=SimpleNamespace(balance_out=lambda p: False,
                          until=lambda p: None), limit_notices=lambda w, n: None, limit_wait=lambda r: True,
                          model_reachable=lambda: _no(), resume_task=None, stop_reports=sr)
    out = await model_wait_tick(ctx, time.time())
    assert out == {tid: "failed"}
    await asyncio.sleep(0.05)
    await deliveries.drain()
    assert len(_messages(store, f"task-stopped:{tid}")) == 1


async def _no():
    return False
