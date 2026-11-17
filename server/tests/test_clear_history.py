"""Clearing the chat history (DELETE /api/messages, chat_clear.py): the conversation and everything made from it goes (task log,
background jobs and their transcripts, approvals, notifications, follow-ups, day summaries, scheduled runs' history, quick replies,
pictures of browser steps on disk, the server's memory of turns); goals, files, schedules, watchers, rules, the vault, the profile,
devices and usage stay. Running work is stopped first: a background job the clean way, a chat reply waiting for an approval is
cancelled and its approval denied. Deleted rows are overwritten in the file (secure_delete). Fake model only."""

import asyncio
import time

import httpx

from assistant.browser_history import ActionArchive
from assistant.chat_clear import clear_tables
from assistant.server import create_app
from assistant.store import Store

from test_agent_profile import app_settings, approval_tool
from test_server import AUTH, Running, follow
from test_workers import Brain, call, delegating, one_worker, say, until

MARK = "ZQXJ-secret-words-seven"   # stands for what the user said; must not survive anywhere in the database file


def count(store, table, where="1"):
    return store._all(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}")[0]["n"]


def seed(store, now):
    """One row in every table the clear deletes or empties, each carrying MARK where it holds words."""
    run = store._run
    run("INSERT INTO conversation_summary(id, summary, up_to_message_id, updated_at) VALUES(1, ?, 1, ?)", (f"summary {MARK}", now))
    store.add_message("user", f"hidden {MARK}")
    run("INSERT INTO tasks(title, status, answer, started_at) VALUES(?, 'done', ?, ?)", (f"task {MARK}", f"answer {MARK}", now))
    run("INSERT INTO approval_log(id, task_id, tool, label, detail, decision, asked_at) VALUES('a1', 1, 'send', 'Send', ?, 'allowed_once', ?)",
        (f"detail {MARK}", now))
    run("INSERT INTO notifications(type, title, body, created_at) VALUES('task_done', ?, ?, ?)", (f"note {MARK}", MARK, now))
    run("INSERT INTO resume_offers(message_id, task_id, why, label, created_at) VALUES(1, 1, 'restart', 'Continue', ?)", (now,))
    run("INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, max_steps, deadline_at, minutes, token_cap, report) "
        "VALUES('wdone', 'general', ?, ?, 'done', 'task:wdone', ?, ?, 10, ?, 5, 1000, ?)",
        (f"job {MARK}", f"brief {MARK}", now, now, now + 60, f'{{"summary": "{MARK}"}}'))
    run("INSERT INTO worker_messages(worker_id, seq, message) VALUES('wdone', 0, ?)", (f'{{"role": "user", "content": "{MARK}"}}',))
    run("INSERT INTO followups(kind, ref, title, detail, began_at, created_at, updated_at) VALUES('question', 'q1', ?, ?, ?, ?, ?)",
        (f"ask {MARK}", MARK, now, now, now))
    run("INSERT INTO journal(date, title, summary, created_at) VALUES('2026-11-01', ?, ?, ?)", (f"day {MARK}", MARK, now))
    run("INSERT INTO journal_days(date, status, reason, updated_at) VALUES('2026-11-01', 'done', '', ?)", (now,))
    run("INSERT INTO message_replies(message_id, questions, created_at) VALUES(1, ?, ?)", (f'[{{"q": "{MARK}"}}]', now))
    run("INSERT INTO goal_suggestions(title, norm, reason, evidence, status, created_at, updated_at) VALUES('Run more', 'run more', ?, ?, 'proposed', ?, ?)",
        (MARK, f"you said {MARK}", now, now))
    run("INSERT INTO goal_suggestions(title, norm, reason, evidence, status, created_at, updated_at) VALUES('Sleep early', 'sleep early', ?, ?, 'dismissed', ?, ?)",
        (MARK, f"you said {MARK}", now, now))
    run("INSERT INTO proactive_log(key, source, title, status, reason, message, decided_at, expires_at, day, task_id) "
        "VALUES('cal:1', 'calendar', 'Dentist', 'sent', ?, ?, ?, ?, '2026-11-08', 1)", (MARK, f"reminder {MARK}", now, now + 86400))


def seed_kept(store, now, goal_id):
    run = store._run
    run("INSERT INTO goal_steps(goal_id, title, created_at, updated_at) VALUES(?, 'Buy shoes', ?, ?)", (goal_id, now, now))
    run("INSERT INTO goal_log(goal_id, at, text) VALUES(?, ?, 'ran 3 km')", (goal_id, now))
    run("INSERT INTO goal_suggestions(title, norm, reason, evidence, status, goal_id, created_at, updated_at) "
        "VALUES('Walk daily', 'walk daily', 'r', 'e', 'accepted', ?, ?, ?)", (goal_id, now, now))
    run("INSERT INTO watchers(name, url, every_seconds, config_at, created_at, updated_at) VALUES('price', 'https://example.com', 600, ?, ?, ?)",
        (now, now, now))
    run("INSERT INTO approval_rules(kind, site, created_at) VALUES('browser_action', 'example.com', ?)", (now,))
    run("INSERT INTO schedule_rules(schedule_id, tool, site, created_at) VALUES(1, 'browser_click', 'example.com', ?)", (now,))
    run("INSERT INTO learned_rules(rule, date) VALUES('Answers short', '2026-11-01')")
    run("INSERT INTO devices(name, token_hash, created_at) VALUES('Pixel', 'h1', ?)", (now,))
    run("INSERT INTO credentials(name, label, hosts, placement, created_at) VALUES('shop', 'Shop', '[]', 'header', ?)", (now,))
    run("INSERT INTO vault_log(ts, item, action) VALUES(?, 'shop', 'saved')", (now,))


async def test_clearing_deletes_the_conversation_and_what_came_of_it_and_keeps_the_rest(tmp_path):
    archive = ActionArchive(tmp_path / "browser-history")
    archive.record("task:w1", b"\xff\xd8jpeg", url="https://example.com", title="Shop", action="browser_click")
    archive.record("schedule:3", b"\xff\xd8jpeg", url="https://example.com", title="Shop", action="browser_open")
    (tmp_path / "keep.txt").write_text("a file next to the database")
    app = create_app(app_settings(tmp_path, scheduler=False), tools=[], stream_fn=Brain(chat=lambda r, n: say(f"answer {MARK}")))
    store = app.state.workers.store
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"user_name": "Mina"})
        goal = (await c.post("/api/goals", json={"title": "Run a 10k", "kickoff": False})).json()
        sched = (await c.post("/api/schedules", json={"name": "Morning", "prompt": "news", "cron": "0 9 * * *"})).json()
        await follow(c, (await c.post("/api/turns", json={"text": f"hello {MARK}"})).json()["id"])
        now = time.time()
        seed(store, now)
        seed_kept(store, now, goal["id"])
        store._run("INSERT INTO schedule_runs(schedule_id, started_at, status, result, task_id) VALUES(?, ?, 'done', ?, 1)", (sched["id"], now, MARK))
        store._run("UPDATE schedules SET last_task_id = 1 WHERE id = ?", (sched["id"],))
        app.state.ended_tabs["task:w1"] = {"id": "task:w1", "shot": b"jpeg"}
        usage_before = (await c.get("/api/usage")).json()["today"]
        assert MARK.encode() in (tmp_path / "a.db").read_bytes()

        out = (await c.delete("/api/messages")).json()

        assert out["ok"] is True and out["stopped"] == {"jobs": 0, "turns": 0}
        got = out["cleared"]
        assert got["messages"] == 3 and got["tasks"] == 2 and got["workers"] == 1 and got["worker_messages"] == 1
        assert got["approvals"] == 1 and got["notifications"] == 1 and got["followups"] == 1 and got["journal"] == 1
        assert got["journal_days"] == 1 and got["schedule_runs"] == 1 and got["resume_offers"] == 1 and got["message_replies"] == 1
        assert got["goal_suggestions"] == 1 and got["conversation_summary"] == 1 and got["browser_pictures"] == 2
        # deleted
        for table in ("messages", "conversation_summary", "tasks", "approval_log", "notifications", "resume_offers", "workers",
                      "worker_messages", "followups", "journal", "journal_days", "schedule_runs", "message_replies"):
            assert count(store, table) == 0, table
        assert (await c.get("/api/messages")).json() == []
        assert (await c.get("/api/tasks")).json()["items"] == []
        assert (await c.get("/api/notifications")).json()["items"] == []
        assert (await c.get("/api/workers")).json()["items"] == []
        assert (await c.get("/api/approvals/history")).json()["items"] == []
        assert app.state.ended_tabs == {}
        assert not any((tmp_path / "browser-history").iterdir())
        # emptied: the row stays, the words go
        assert store._all("SELECT message, reason, task_id FROM proactive_log") == [{"message": "", "reason": "", "task_id": None}]
        assert store._all("SELECT title, evidence, reason FROM goal_suggestions WHERE status = 'dismissed'") == [
            {"title": "Sleep early", "evidence": "", "reason": ""}]
        assert store._all("SELECT last_task_id FROM schedules")[0]["last_task_id"] is None
        # kept
        assert (await c.get("/api/profile")).json()["user_name"] == "Mina"
        assert [g["title"] for g in (await c.get("/api/goals")).json()] == ["Run a 10k"]
        assert count(store, "goal_steps") == 1 and count(store, "goal_log") >= 1
        assert count(store, "goal_suggestions", "status = 'accepted'") == 1
        assert [s["name"] for s in (await c.get("/api/schedules")).json()["items"]] == ["Morning"]
        for table in ("watchers", "approval_rules", "schedule_rules", "learned_rules", "devices", "credentials", "vault_log"):
            assert count(store, table) == 1, table
        assert (await c.get("/api/usage")).json()["today"] == usage_before
        assert (tmp_path / "keep.txt").read_text() == "a file next to the database"
        # a new conversation starts as usual
        await follow(c, (await c.post("/api/turns", json={"text": "again"})).json()["id"])
        assert [m["content"] for m in (await c.get("/api/messages")).json()] == ["again", f"answer {MARK}"]
        await c.delete("/api/messages")
    assert MARK.encode() not in (tmp_path / "a.db").read_bytes()   # overwritten in the file, not just unlinked (secure_delete)


async def test_a_running_job_and_a_reply_waiting_for_approval_are_stopped_before_the_clear(tmp_path):
    ran = []
    job_started = asyncio.Event()

    async def worker(req, n):
        job_started.set()
        await asyncio.Event().wait()   # a slow job: still running when the history is cleared
        return say("never")

    def chat(req, n):
        last_user = next(m["content"] for m in reversed(req.messages) if m.get("role") == "user")
        if "bob" in str(last_user):
            return say("보냈어요.") if req.messages[-1].get("role") == "tool" else call("send", {"to": "bob"})
        return delegating({"task": "세 곳의 가격을 비교해줘", "kind": "general"})(req, n)

    brain = Brain(chat=chat, worker=worker)
    app = create_app(app_settings(tmp_path, scheduler=False), tools=[approval_tool(ran=ran)], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "가격 비교해줘"})).json()["id"])
        job = await one_worker(c, "running")
        await asyncio.wait_for(job_started.wait(), 5)
        waiting = (await c.post("/api/turns", json={"text": "bob에게 보내줘"})).json()["id"]
        pending = await until(lambda: _pending(c))
        assert pending[0]["turn_id"] == waiting

        out = (await c.delete("/api/messages")).json()

        assert out["stopped"]["jobs"] == 1 and out["stopped"]["turns"] >= 1
        assert ran == []                                                      # the approval was denied, not left hanging
        assert (await c.get("/api/approvals/pending")).json()["items"] == []
        assert (await c.get(f"/api/turns/{waiting}")).status_code == 404       # a phone cannot fetch the deleted reply again
        assert (await c.get(f"/api/workers/{job['id']}")).status_code == 404
        assert app.state.workers._live == {} and app.state.workers._queue == []
        await asyncio.sleep(0.3)                                              # nothing comes back late (a handoff, a task, a note)
        assert (await c.get("/api/messages")).json() == []
        assert (await c.get("/api/tasks")).json()["items"] == []
        assert (await c.get("/api/workers")).json()["items"] == []
        assert (await c.get("/api/notifications")).json()["items"] == []
        assert (await c.get("/api/agent/status")).json()["busy"] is False


async def test_a_paused_job_is_stopped_and_its_follow_up_goes(tmp_path):
    def worker(req, n):
        return call("report", {"status": "needs_input", "summary": "어느 색으로 할까요?"})

    brain = Brain(chat=delegating({"task": "신발 골라줘", "kind": "general"}), worker=worker)
    app = create_app(app_settings(tmp_path, scheduler=False), tools=[], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "신발 골라줘"})).json()["id"])
        await one_worker(c, "needs_input")
        await until(lambda: brain.calls["handoff"])
        assert (await c.get("/api/followups")).json()["items"]      # the job's question waits as a follow-up
        out = (await c.delete("/api/messages")).json()
        assert out["stopped"]["jobs"] == 1 and out["cleared"]["workers"] == 1 and out["cleared"]["followups"] >= 1
        assert (await c.get("/api/workers")).json()["items"] == []
        assert (await c.get("/api/followups", params={"closed": True})).json()["items"] == []


async def _pending(c):
    return (await c.get("/api/approvals/pending")).json()["items"]


def test_clear_tables_tolerates_a_store_without_the_other_modules_tables():
    store = Store()
    store.add_message("user", "hi")
    out = clear_tables(store)
    assert out["messages"] == 1 and out["tasks"] == 0 and store.messages() == []


def test_the_archive_clear_removes_every_tab_folder(tmp_path):
    a = ActionArchive(tmp_path)
    for key in ("task:w1", "schedule:2"):
        a.record(key, b"jpeg", url="https://example.com", title="t", action="browser_click")
        a.record(key, None, url="https://example.com", title="t", action="browser_read")
    assert a.clear() == 2
    assert list(tmp_path.iterdir()) == []
    assert a.history("task:w1")["items"] == [] and a.clear() == 0
    assert ActionArchive(tmp_path / "missing").clear() == 0
