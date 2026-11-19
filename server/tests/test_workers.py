"""Background jobs (workers.py): delegate answers at once, the job runs as turns of its own and ends with report; the report reaches the
conversation as a hidden handoff that the main agent answers; needs_input on a missing login and going on by itself once it is saved;
a restart keeps the transcript and resumes; the cap on jobs running at once; one level only; stop and message; approvals from a job;
the larger chat step budget; and no site-specific words. Fake model, fake browser pages, no real model calls."""

import asyncio
import json
import re
from pathlib import Path

import httpx

from assistant.agent import Tool, emit_ui
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.browser import BrowserSession, browser_owner
from assistant.tools.vault_tools import vault_tools
from assistant.workers import HANDOFF_MARK, Limits, current_worker

from test_agent_profile import ShotPage, app_settings, approval_tool
from test_server import AUTH, Running, follow

ROOT = Path(__file__).resolve().parents[2]
JOB_MARK = "you are a background job"


def role_of(request):
    msgs = request.messages
    if any(isinstance(m.get("content"), str) and JOB_MARK in m["content"] for m in msgs):
        return "worker"
    users = [m for m in msgs if m.get("role") == "user"]
    if users and HANDOFF_MARK in str(users[-1].get("content")):
        return "handoff"
    return "chat"


class Brain:
    """A fake model with one script per kind of run: chat, worker (a background job), handoff. Each script is a function
    (request, n) -> events (or an awaitable of them); n counts the calls of that kind."""

    def __init__(self, chat=None, worker=None, handoff=None):
        self.scripts = {"chat": chat, "worker": worker, "handoff": handoff or (lambda r, n: [TextDelta("알려 드릴게요."), Done("stop")])}
        self.calls = {"chat": [], "worker": [], "handoff": []}

    async def __call__(self, provider, request, **kw):
        kind = role_of(request)
        self.calls[kind].append(request)
        out = self.scripts[kind](request, len(self.calls[kind]) - 1)
        if asyncio.iscoroutine(out):
            out = await out
        for ev in out:
            yield ev


def call(name, args=None, cid=None):
    return [ToolCall(cid or f"c-{name}", name, json.dumps(args or {}, ensure_ascii=False)), Done("tool_calls")]


def say(text):
    return [TextDelta(text), Done("stop")]


def delegating(args, then="맡겼어요. 끝나면 알려 드릴게요."):
    """A chat that hands the job over in its first step and answers in its second."""
    def chat(req, n):
        if req.messages[-1].get("role") == "tool":
            return say(then)
        return call("delegate", args)
    return chat


async def until(cond, tries=400, pause=0.02):
    for _ in range(tries):
        got = cond()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        await asyncio.sleep(pause)
    raise AssertionError("never happened")


async def worker_list(c):
    return (await c.get("/api/workers")).json()["items"]


async def one_worker(c, status=None):
    async def look():
        items = await worker_list(c)
        return items[0] if items and (status is None or items[0]["status"] == status) else None
    return await until(look)


def tool_results(req):
    return [m["content"] for m in req.messages if m.get("role") == "tool"]


# ---------------------------------------------------------------------------------------------------------- delegate, run, handoff
async def test_delegate_answers_at_once_the_job_runs_to_done_and_the_main_agent_gets_a_hidden_handoff(tmp_path):
    gate = asyncio.Event()

    async def worker(req, n):
        await gate.wait()   # the job is slow: the chat must not wait for it
        names = [t["function"]["name"] for t in req.tools]
        assert "report" in names and "delegate" not in names and "workers_list" not in names   # one level only
        return call("report", {"status": "done", "summary": "가장 싼 곳은 1,000원이에요.", "details": "세 곳을 봤어요."})

    brain = Brain(chat=delegating({"task": "세 곳의 가격을 비교해줘", "kind": "general"}), worker=worker,
                  handoff=lambda r, n: say("비교 끝났어요: 가장 싼 곳은 1,000원이에요."))
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "세 곳 가격 비교해줘"})).json()["id"])
        started = [e for e in events if e["type"] == "worker"]
        assert started and started[0]["status"] == "running" and started[0]["title"]
        result = next(e for e in events if e["type"] == "tool_result" and e["name"] == "delegate")
        assert "Handed off; you will hear back when it is done" in result["output"] and "Do not check on it" in result["output"]
        assert events[-1]["type"] == "done"                        # the chat turn is over while the job still runs
        w = await one_worker(c)
        assert w["status"] == "running" and w["kind"] == "general" and w["tab"] == f"task:{w['id']}" and w["max_steps"] == 60
        chat_task = (await c.get("/api/tasks", params={"source": "chat"})).json()["items"][0]
        assert w["parent_task_id"] == chat_task["id"]
        gate.set()
        done = await one_worker(c, "done")
        detail = (await c.get(f"/api/workers/{done['id']}")).json()
        assert detail["report"]["summary"] == "가장 싼 곳은 1,000원이에요." and detail["report"]["details"] == "세 곳을 봤어요."
        assert detail["steps"] >= 1 and detail["segments"] == 1
        await until(lambda: brain.calls["handoff"])
        msgs = await until(lambda: _messages_with(c, "비교 끝났어요"))
        assert [m["content"] for m in msgs] == ["세 곳 가격 비교해줘", "맡겼어요. 끝나면 알려 드릴게요.", "비교 끝났어요: 가장 싼 곳은 1,000원이에요."]
        handoff = brain.calls["handoff"][0].messages[-1]["content"]
        assert HANDOFF_MARK in handoff and "가장 싼 곳은 1,000원이에요." in handoff and done["id"] in handoff
        assert "Progress (server record): " in handoff and "no step that commits something went through" in handoff
        # 활동: the job's run and the handoff sit under the chat task; only the handoff speaks up with a notification
        tasks = (await c.get("/api/tasks")).json()["items"]
        kids = [t for t in tasks if t["parent_id"] == chat_task["id"]]
        assert {t["source"] for t in kids} == {"worker", "handoff"} and all(t["worker_id"] == done["id"] for t in kids)
        notes = (await c.get("/api/notifications")).json()["items"]
        assert [n["title"] for n in notes] == [f"Finished: {done['title']}"]


async def _messages_with(c, text):
    msgs = (await c.get("/api/messages")).json()
    return msgs if any(text in m["content"] for m in msgs) else None


async def test_a_quiet_run_with_nothing_to_say_leaves_no_message_and_no_notification(tmp_path):
    brain = Brain(chat=delegating({"task": "확인만 해줘"}), worker=lambda r, n: call("report", {"status": "done", "summary": "변화 없음"}),
                  handoff=lambda r, n: say("NO_REPLY"))
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "확인해줘"})).json()["id"])
        await one_worker(c, "done")
        await until(lambda: brain.calls["handoff"])
        await until(lambda: _handoff_over(c))
        assert [m["role"] for m in (await c.get("/api/messages")).json()] == ["user", "assistant"]
        assert (await c.get("/api/notifications")).json()["items"] == []


async def _handoff_over(c):
    items = (await c.get("/api/tasks", params={"source": "handoff"})).json()["items"]
    return items and items[0]["status"] != "running"


# ---------------------------------------------------------------------------------------------------------- the login it needs
async def test_needs_input_on_a_missing_login_then_it_goes_on_by_itself_once_the_login_is_saved(tmp_path):
    settings = app_settings(tmp_path)
    vault = vault_tools(Store(settings.db_path), None)        # the real request_login, on the app's own database
    seen_resume = []

    def worker(req, n):
        last = req.messages[-1]
        if n == 0:
            return call("request_login", {"site": "https://shop.example.com/login", "name": "shop"})
        if last.get("role") == "tool":
            assert "fill-in card" in last["content"]
            return call("report", {"status": "needs_input", "summary": "로그인이 필요해요."}, cid="c-r1")
        seen_resume.append(last["content"])
        return call("report", {"status": "done", "summary": "로그인하고 장바구니를 확인했어요."}, cid="c-r2")

    brain = Brain(chat=delegating({"task": "장바구니 확인해줘", "kind": "general", "url": "shop.example.com"}), worker=worker)
    app = create_app(settings, tools=list(vault), stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "장바구니 확인해줘"})).json()["id"])
        # request_login shows the sign-in sheet without an approval card; the job then waits for the login
        w = await one_worker(c, "needs_input")
        assert w["waiting_for"] == "login" and w["login"] == "shop" and w["reason"] == "로그인이 필요해요."
        assert (await c.get(f"/api/workers/{w['id']}")).json()["url"] == "https://shop.example.com"
        await until(lambda: brain.calls["handoff"])
        assert "saves the login 'shop'" in brain.calls["handoff"][0].messages[-1]["content"]
        r = await c.put("/api/vault/shop/login", json={"username": "me", "password": "pw-123", "mode": "now"})
        assert r.status_code == 200
        done = await one_worker(c, "done")
        assert done["id"] == w["id"] and "saved the login 'shop'" in seen_resume[0]
        detail = (await c.get(f"/api/workers/{w['id']}")).json()
        assert detail["segments"] == 2 and detail["report"]["summary"].startswith("로그인하고")
        # the second run read the same transcript: the first run's steps were in its request
        assert any("fill-in card" in t for t in tool_results(brain.calls["worker"][-1]))
        assert "pw-123" not in json.dumps(detail, ensure_ascii=False)


async def _pending(c):
    return (await c.get("/api/approvals/pending")).json()["items"] or None


# ---------------------------------------------------------------------------------------------------------- browser tab, hand-back
async def test_a_browser_job_has_its_own_tab_and_a_blocked_job_goes_on_when_the_user_hands_the_tab_back(tmp_path):
    made = []

    async def connect():
        made.append(ShotPage("https://shop.example.com/cart"))
        return made[-1]

    sess = BrowserSession(connect=connect)
    owners = []

    async def use(args):
        owners.append(browser_owner.get())
        await sess.page()
        await emit_ui({"type": "browser", "url": "https://shop.example.com/cart", "status": "사이트 보는 중"})
        return "the cart page"

    look = Tool("browser_read", "read the page", {"type": "object"}, use, read_only=True)

    def worker(req, n):
        last = req.messages[-1]
        if n == 0:
            return call("browser_read", {}, cid="c-look")
        if n == 1:
            return call("report", {"status": "blocked", "summary": "로봇 확인이 있어요."}, cid="c-r1")
        assert "handed the browser back" in last["content"]
        return call("report", {"status": "done", "summary": "확인했어요.", "keep_tab": True}, cid="c-r2")

    brain = Brain(chat=delegating({"task": "장바구니 봐줘", "kind": "browser"}), worker=worker)
    app = create_app(app_settings(tmp_path), tools=[look], stream_fn=brain, browser=sess, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "장바구니 봐줘"})).json()["id"])
        w = await one_worker(c, "blocked")
        key = f"task:{w['id']}"
        assert owners == [key] and sess.open_keys() == [key] and w["max_steps"] == 200
        assert w["last_url"] == "https://shop.example.com/cart" and w["thumbnail"] == f"/api/browser/sessions/{key}/screenshot"
        assert await app.state.sweep_tabs() == [] and not made[0].is_closed()   # a paused job keeps its tab for the user
        sess.take_control(key, "7")
        await sess.release_control(key)                                        # the user solved it on the page and handed it back
        done = await one_worker(c, "done")
        assert done["id"] == w["id"] and await app.state.sweep_tabs() == [] and not made[0].is_closed()   # keep_tab


# ---------------------------------------------------------------------------------------------------------- restart
async def test_a_restart_keeps_the_transcript_marks_the_job_interrupted_and_resumes_it_in_a_new_tab(tmp_path):
    settings = app_settings(tmp_path)
    stuck = asyncio.Event()

    async def note(args):
        await emit_ui({"type": "browser", "url": "https://shop.example.com/step2", "status": "사이트 보는 중"})
        return "first page read: 3 items"

    tool = Tool("browser_read", "read", {"type": "object"}, note, read_only=True)

    async def worker1(req, n):
        if n == 0:
            return call("browser_read", {}, cid="c-first")
        stuck.set()
        await asyncio.Event().wait()     # the server goes down in the middle of this step
        return []

    brain1 = Brain(chat=delegating({"task": "여러 페이지를 읽어줘", "kind": "general"}), worker=worker1)
    async with Running(create_app(settings, tools=[tool], stream_fn=brain1, with_worker_tools=True)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "읽어줘"})).json()["id"])
        await until(lambda: stuck.is_set())
        wid = (await one_worker(c))["id"]
    row = Store(settings.db_path)._all("SELECT status, error FROM workers WHERE id = ?", (wid,))[0]
    assert row["status"] == "interrupted" and row["error"] == "Stopped because the server restarted"

    seen = []

    def worker2(req, n):
        seen.append(req)
        return call("report", {"status": "done", "summary": "다 읽었어요."}, cid="c-end")

    brain2 = Brain(worker=worker2)
    async with Running(create_app(settings, tools=[tool], stream_fn=brain2, with_worker_tools=True)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        done = await one_worker(c, "done")
        assert done["id"] == wid and (await c.get(f"/api/workers/{wid}")).json()["segments"] == 2
        req = seen[0]
        assert "first page read: 3 items" in json.dumps(tool_results(req), ensure_ascii=False)   # the history from before the restart
        last = req.messages[-1]["content"]
        assert "server restarted" in last and "Your last page was https://shop.example.com/step2" in last   # the pick-up note's tab line (click-first, S7)


# ---------------------------------------------------------------------------------------------------------- limits
async def test_at_most_n_jobs_run_at_once_and_a_job_cannot_start_another(tmp_path):
    gate = asyncio.Event()

    def chat(req, n):
        if req.messages[-1].get("role") == "tool":
            return say("하나만 맡겼어요.")
        return [ToolCall("d1", "delegate", '{"task": "첫 번째 일"}'), ToolCall("d2", "delegate", '{"task": "두 번째 일"}'), Done("tool_calls")]

    async def worker(req, n):
        if n == 0:
            return call("delegate", {"task": "더 맡기기"}, cid="c-deeper")      # not in its tool list
        assert "unknown tool 'delegate'" in req.messages[-1]["content"]
        await gate.wait()
        return call("report", {"status": "done", "summary": "끝"}, cid="c-end")

    brain = Brain(chat=chat, worker=worker)
    app = create_app(app_settings(tmp_path, workers_max=1), tools=[], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "두 개 맡겨줘"})).json()["id"])
        outs = [e["output"] for e in events if e["type"] == "tool_result"]
        assert outs[0].startswith("Handed off") and outs[1].startswith("Error: 1 background jobs are running already")
        assert len(await worker_list(c)) == 1
        assert (await c.get("/api/workers")).json()["running"] == 1
        gate.set()
        await one_worker(c, "done")
    # and a job's own context refuses delegate even if it were offered
    token = current_worker.set("w000000")
    try:
        assert (await app.state.workers.delegate({"task": "x"})).startswith("Error: you are a background job yourself")
    finally:
        current_worker.reset(token)


def test_budgets_by_kind_and_the_larger_chat_step_budget():
    lim = Limits(steps=200, minutes=60)
    assert lim.budget("browser") == (200, 60) and lim.budget("research") == (100, 30) and lim.budget("general") == (60, 30)
    assert lim.budget("browser", 500)[1] == 240 and lim.budget("general", 5)[1] == 5
    assert Settings(token="x" * 16).max_steps == 30
    env = {"ASSISTANT_TOKEN": "t" * 20}
    s = Settings.from_env(env)
    assert (s.max_steps, s.workers_max, s.worker_steps, s.worker_minutes) == (30, 3, 200, 60)
    s = Settings.from_env({**env, "ASSISTANT_WORKERS_MAX": "5", "ASSISTANT_WORKER_STEPS": "300", "ASSISTANT_MAX_STEPS": "40"})
    assert (s.workers_max, s.worker_steps, s.max_steps) == (5, 300, 40)


async def test_a_chat_turn_may_take_more_than_twelve_steps_now(tmp_path):
    async def look(args):
        return "ok"

    tools = [Tool(f"look_{i}", "x", {"type": "object"}, look) for i in range(25)]

    def chat(req, n):
        return call(f"look_{n}", {}, cid=f"c{n}") if n < 20 else say("다 했어요.")

    app = create_app(app_settings(tmp_path), tools=tools, stream_fn=Brain(chat=chat))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "많이 봐줘"})).json()["id"])
        assert not any(e["type"] == "step_limit" for e in events) and events[-1]["type"] == "done" and events[-1]["steps"] == 21


# ---------------------------------------------------------------------------------------------------------- stop, message
async def test_a_message_reaches_a_running_job_before_its_next_step_and_stop_ends_it_without_a_handoff(tmp_path):
    gates, entered, reads = [asyncio.Event(), asyncio.Event()], asyncio.Event(), []

    async def slow(args):
        reads.append(1)
        entered.set()
        await gates[min(len(reads) - 1, 1)].wait()   # the first read waits until the message is sent; the second one for ever
        return "page one"

    tool = Tool("slow_read", "x", {"type": "object"}, slow)

    async def worker(req, n):
        if n == 0:
            return call("slow_read", {}, cid="c-slow")
        assert "a message for you from the main assistant: 파란색으로 골라줘" in req.messages[-1]["content"]
        return call("slow_read", {}, cid=f"c-slow{n}")

    brain = Brain(chat=delegating({"task": "옷 골라줘"}), worker=worker)
    app = create_app(app_settings(tmp_path), tools=[tool], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "옷 골라줘"})).json()["id"])
        await until(lambda: entered.is_set())
        w = await one_worker(c)
        r = (await c.post(f"/api/workers/{w['id']}/message", json={"text": "파란색으로 골라줘"})).json()
        assert r["delivered"] == "delivered"
        gates[0].set()
        await until(lambda: len(reads) == 2)
        assert len(brain.calls["worker"]) == 2
        r = (await c.post(f"/api/workers/{w['id']}/stop")).json()
        assert r["ok"] and r["worker"]["status"] == "stopped"
        assert (await c.get(f"/api/workers/{w['id']}")).json()["finished_at"]
        await asyncio.sleep(0.2)
        assert brain.calls["handoff"] == []          # nothing to hand off: whoever stopped it knows
        assert (await c.post(f"/api/workers/{w['id']}/stop")).status_code == 200
        assert (await c.post("/api/workers/wnope00/stop")).status_code == 404
        assert (await c.post("/api/workers/wnope00/resume", json={})).status_code == 404


async def test_a_chat_turn_after_the_user_stopped_a_job_is_told_it_is_not_running(tmp_path):
    """The chat still says "it runs in the background" and a stop sends no message: the turn's lines carry the job's state now."""
    entered, hold = asyncio.Event(), asyncio.Event()

    async def slow(args):
        entered.set()
        await hold.wait()
        return "page"

    def chat(req, n):
        if req.messages[-1].get("role") == "tool":
            return say("백그라운드에서 진행 중이에요.")
        return call("delegate", {"task": "체험단 전부 신청해"}) if n == 0 else say("네.")

    brain = Brain(chat=chat, worker=lambda req, n: call("slow_read", {}, cid=f"c-{n}"))
    app = create_app(app_settings(tmp_path), tools=[Tool("slow_read", "x", {"type": "object"}, slow)], stream_fn=brain,
                     with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "체험단 신청해"})).json()["id"])
        await until(lambda: entered.is_set())
        w = await one_worker(c)
        assert (await c.post(f"/api/workers/{w['id']}/stop")).json()["worker"]["status"] == "stopped"
        await follow(c, (await c.post("/api/turns", json={"text": "다른 얘기"})).json()["id"])
        sent = "\n".join(str(m.get("content")) for m in brain.calls["chat"][-1].messages)
        line = next(x for x in sent.splitlines() if x.startswith(f"- {w['id']} "))
        assert "stopped" in line and "not running" in line


async def test_the_main_agent_resumes_a_paused_job_with_the_users_answer_and_lists_it(tmp_path):
    def worker(req, n):
        if n == 0:
            return call("report", {"status": "needs_input", "summary": "색상을 골라 주세요: 빨강 또는 파랑."}, cid="c-ask")
        assert "Message for you: 파랑" in req.messages[-1]["content"]
        return call("report", {"status": "done", "summary": "파랑으로 담았어요."}, cid="c-done")

    def chat(req, n):
        last = req.messages[-1]
        if n == 0:
            return call("delegate", {"task": "옷 담아줘"})
        if n == 1:
            return say("맡겼어요.")
        if n == 2:
            return call("workers_list", {}, cid="c-list")
        if n == 3:
            wid = re.search(r"- (w[0-9a-f]{6}) ·", last["content"]).group(1)
            assert "needs_input" in last["content"] and "waiting for user" in last["content"]
            return call("resume", {"worker_id": wid, "note": "파랑"}, cid="c-resume")
        return say("이어서 하라고 했어요.")

    brain = Brain(chat=chat, worker=worker)
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "옷 담아줘"})).json()["id"])
        w = await one_worker(c, "needs_input")
        assert w["waiting_for"] == "user"
        await until(lambda: _handoff_over(c))
        events = await follow(c, (await c.post("/api/turns", json={"text": "파랑으로 해줘"})).json()["id"])
        assert any(e["type"] == "tool_result" and e["name"] == "resume" and "goes on" in e["output"] for e in events)
        await one_worker(c, "done")


# ---------------------------------------------------------------------------------------------------------- approvals
async def test_an_approval_inside_a_job_reaches_the_user_like_an_unattended_run_and_the_job_goes_on(tmp_path):
    ran = []
    send = approval_tool("send", ran)

    def worker(req, n):
        if n == 0:
            return call("send", {"to": "bob"}, cid="c-send")
        assert req.messages[-1]["content"] == "done"
        return call("report", {"status": "done", "summary": "보냈어요."}, cid="c-end")

    brain = Brain(chat=delegating({"task": "bob에게 보내줘"}), worker=worker)
    app = create_app(app_settings(tmp_path), tools=[send], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "보내줘"})).json()["id"])
        pend = await until(lambda: _pending(c))
        w = await one_worker(c)
        assert pend[0]["unattended"] is True and pend[0]["task_id"] == w["task_id"]
        # what asked, so the chat can show the card and say it came from the job
        assert pend[0]["source"] == "worker" and pend[0]["worker_id"] == w["id"] and pend[0]["title"] == w["title"]
        notes = (await c.get("/api/notifications")).json()["items"]
        assert [n["type"] for n in notes] == ["approval_waiting"]
        await c.post(f"/api/approvals/{pend[0]['approval_id']}", json={"approve": True})
        await one_worker(c, "done")
        assert ran == [{"to": "bob"}]


# ---------------------------------------------------------------------------------------------------------- our own words
def test_no_site_specific_or_borrowed_words_in_the_jobs_code_tools_or_prompt():
    code = (ROOT / "server" / "src" / "assistant" / "workers.py").read_text(encoding="utf-8")
    from assistant.workers import WorkerHost, Workers
    host = WorkerHost(start_turn=lambda *a: None, cancel_turn=lambda t: None, handoff=None, emit=None, tools=lambda: [])
    para = next(t for t in Workers(Store(), host, Limits()).main_tools() if t.name == "delegate").description   # how to hand off long work
    for text in (code, para):
        assert not re.search(r"\b[\w-]+\.(com|net|org|kr|co|io)\b", text)       # no web addresses
        assert not re.search(r"lotto|로또|복권|coupang|쿠팡|naver|네이버|amazon|subagent", text, re.I)
    assert len(para) < 780                                                      # a few short lines (600 before the checkout and phone kinds)


async def test_job_progress_shows_how_far_a_long_job_is_on_the_job_card(tmp_path):
    steps = [call("job_progress", {"done": 300, "total": 1545, "label": "댓글 삭제"}),
             call("echo_item", {}), call("report", {"status": "done", "summary": "끝났어요."})]

    def worker(req, n):
        return steps[n]

    async def echo(args):
        return "ok"
    echo_tool = Tool("echo_item", "one item", {"type": "object", "properties": {}}, echo)
    brain = Brain(chat=delegating({"task": "댓글을 전부 지워줘", "kind": "general"}), worker=worker)
    app = create_app(app_settings(tmp_path), tools=[echo_tool], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "댓글 전부 지워줘"})).json()["id"])
        done = await one_worker(c, "done")
        assert done["status_text"] == "300 / 1,545 · 댓글 삭제"          # the next tool call did not overwrite it
        assert (await worker_list(c))[0]["status_text"] == "300 / 1,545 · 댓글 삭제"
        names = [t["function"]["name"] for t in brain.calls["worker"][0].tools]
        assert "job_progress" in names
        brief = next(m["content"] for m in brain.calls["worker"][0].messages if JOB_MARK in str(m.get("content")))
        assert "job_progress(done, total, label)" in brief and "2-3 other ways" in brief


def test_the_progress_line_counts_what_went_through_from_the_servers_own_records(tmp_path):
    """2026-11-11: a job stopped by a model error after 30+ applications was told to the user as "only the first one was done"."""
    import sqlite3
    from assistant.workers import Workers

    class S:
        def __init__(self):
            self.db = sqlite3.connect(":memory:")
            self.db.row_factory = sqlite3.Row
            self.db.executescript("CREATE TABLE tasks (id INTEGER PRIMARY KEY, worker_id TEXT);"
                                  "CREATE TABLE approval_log (id TEXT, task_id INTEGER, site TEXT, decision TEXT);")

        def _all(self, q, a=()):
            return [dict(r) for r in self.db.execute(q, a)]
    st = S()
    st.db.execute("INSERT INTO tasks VALUES (220, 'wjob'), (221, 'other')")
    st.db.executemany("INSERT INTO approval_log VALUES (?,?,?,?)", [(f"a{i}", 220, "gift.kakao.com", "allowed_by_rule") for i in range(14)]
                      + [("d", 220, "gift.kakao.com", "denied"), ("o", 221, "x.example", "allowed_once")])
    w = Workers.__new__(Workers)
    w.store = st
    line = w._progress_line({"id": "wjob", "steps": 123, "last_url": "https://gift.kakao.com/giftbox/trial-campaign/x"})
    assert line.startswith("Progress (server record): 123 steps") and "14 on gift.kakao.com" in line and "trial-campaign" in line
    assert "x.example" not in line


# ---------------------------------------------------------------------------------------------------------- hc-final-a: typed need
def _bare_workers():
    from assistant.workers import WorkerHost, Workers
    host = WorkerHost(start_turn=lambda *a: None, cancel_turn=lambda t: None, handoff=None, emit=None, tools=lambda: [])
    return Workers(Store(), host, Limits())


def test_a_needs_input_with_need_site_data_reset_offers_the_clear_and_retry_choice_and_a_word_does_not():
    from assistant.workers import _Live, current_worker
    ws = _bare_workers()
    row = {"id": "w-need", "title": "shop", "kind": "general", "parent_source": "chat"}
    wait = {"kind": "user"}
    typed = {"status": "needs_input", "summary": "The shop keeps refusing me.", "details": "", "need": "site_data_reset"}
    assert "quick_replies" in ws._handoff_text(row, typed, wait)
    # the same words in the summary, with no typed need, do not decide anything
    words_only = {"status": "needs_input", "summary": "cookie problem, 쿠키 문제", "details": ""}
    assert "quick_replies" not in ws._handoff_text(row, words_only, wait)
    # the report tool keeps only the two typed values; anything else is dropped, not read from the summary
    ws._live["w-need"] = _Live()
    token = current_worker.set("w-need")
    try:
        tool = ws.report_tool()
        import asyncio
        asyncio.run(tool.handler({"status": "needs_input", "summary": "cookie problem", "need": "site_data_reset"}))
        assert ws._live["w-need"].report["need"] == "site_data_reset"
        asyncio.run(tool.handler({"status": "needs_input", "summary": "cookie problem", "need": "cookies"}))
        assert "need" not in ws._live["w-need"].report
    finally:
        current_worker.reset(token)


def test_a_research_job_is_told_its_browser_is_not_signed_in_and_its_tab_kind_is_known(monkeypatch):
    ws = _bare_workers()
    monkeypatch.setattr(ws, "_row", lambda wid: {"kind": "research"} if wid == "rjob" else {"kind": "browser"} if wid == "bjob" else None)
    assert ws.kind_of("rjob") == "research" and ws.kind_of("bjob") == "browser" and ws.kind_of("none") == ""
    brief = ws._brief("rjob", "compare prices", "research", "", [], 10, 5)
    assert ("This browser is not signed in to any site. When a step needs a sign-in, skip it and say in your report what you "
            "could not check.") in brief
    assert "not signed in" not in ws._brief("bjob", "compare prices", "browser", "", [], 10, 5)
