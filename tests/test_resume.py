"""Going on after a restart or a model outage (resume.py): a goal's kickoff turn and an automatic run go on by themselves after a
restart, from the saved conversation, told the server restarted; the user's own chat turn gets a Continue button instead and goes
on when it is tapped; a task restarted too often ends as failed; when every model is down a background task waits for the model and
goes on once a light check passes (a word to the user after hours), and a chat turn ends with the "connection dropped" line and a
Continue button and goes on by itself once the model is back unless the user wrote since. Fake models only."""

import asyncio
import json
import time

import httpx

from assistant.activity import Activity
from assistant.goals import GOAL_MARK
from assistant.providers import Done, ProviderError, TextDelta
from assistant.resume import is_outage
from assistant.schedules import Schedules
from assistant.server import create_app
from assistant.store import Store

from test_server import AUTH, Running, follow, settings

CHECK = "Reply with the single word OK."
RESTARTED = "server restarted in the middle of this turn"
MODEL_BACK = "AI model could not be reached for a while"


class Model:
    """A fake model. `mode`: "ok" answers `reply`, "block" never answers (the server goes down meanwhile), "down" raises a provider
    error. The light model check (CHECK) follows `up`. Every chat request is kept in `seen`, every check counted in `checks`."""

    def __init__(self, mode="ok", reply="이어서 했어요.", up=True):
        self.mode, self.reply, self.up = mode, reply, up
        self.seen, self.checks = [], 0
        self.started = asyncio.Event()

    async def __call__(self, provider, request, **kw):
        if request.messages and request.messages[-1].get("content") == CHECK:
            self.checks += 1
            if not self.up:
                raise ProviderError("p: service unavailable", status=503)
            yield TextDelta("OK")
            yield Done("stop")
            return
        self.seen.append(request)
        self.started.set()
        if self.mode == "block":
            await asyncio.Event().wait()
        if self.mode == "down":
            raise ProviderError("p: overloaded, try again later", status=529)
        yield TextDelta(self.reply)
        yield Done("stop")


def last_user(req):
    return next(m["content"] for m in reversed(req.messages) if m.get("role") == "user")


async def until(cond, tries=300, pause=0.02):
    for _ in range(tries):
        got = cond()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        await asyncio.sleep(pause)
    raise AssertionError("never happened")


async def tasks(c):
    return (await c.get("/api/tasks")).json()["items"]


def app_for(tmp_path, model, **kw):
    return create_app(settings(db_path=str(tmp_path / "a.db"), **kw), tools=[], stream_fn=model, with_goal_tools=True)


# ---------------------------------------------------------------------------------------------------------- restarts
async def test_a_goal_kickoff_cut_off_by_a_restart_goes_on_by_itself_from_the_saved_conversation(tmp_path):
    m1 = Model("block")
    async with Running(app_for(tmp_path, m1)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/goals", json={"title": "Goal A"})
        await until(lambda: m1.started.is_set())
        old = (await tasks(c))[0]
        assert old["source"] == "handoff" and old["status"] == "running"
    row = Store(str(tmp_path / "a.db"))._all("SELECT status, resume_state FROM tasks WHERE id = ?", (old["id"],))[0]
    assert row["status"] == "interrupted" and row["resume_state"] == "pending"   # marked as the server went down, not "stopped"

    m2 = Model("ok", reply="목표를 이어서 볼게요.")
    async with Running(app_for(tmp_path, m2)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await until(lambda: m2.seen)
        sent = last_user(m2.seen[0])
        assert GOAL_MARK in sent and "Goal A" in sent and RESTARTED in sent   # the same turn, told why it goes on
        items = await until(lambda: _done_resumed(c, old["id"]))
        first = next(t for t in items if t["id"] == old["id"])
        again = next(t for t in items if t["id"] == first["resumed_as"])
        assert first["status"] == "interrupted" and again["resumed_from"] == old["id"] and again["source"] == "handoff"
        store = Store(str(tmp_path / "a.db"))
        hidden = store._all("SELECT COUNT(*) AS n FROM messages WHERE role = 'user' AND content LIKE ?", (GOAL_MARK + "%",))[0]["n"]
        assert hidden == 1                                                       # the kickoff message is not stored twice
        assert [x["content"] for x in (await c.get("/api/messages")).json()] == ["목표를 이어서 볼게요."]


async def _done_resumed(c, old_id):
    items = await tasks(c)
    first = next((t for t in items if t["id"] == old_id), None)
    if not first or not first["resumed_as"]:
        return None
    again = next((t for t in items if t["id"] == first["resumed_as"]), None)
    return items if again and again["status"] == "done" else None


async def test_the_users_own_chat_turn_gets_a_continue_button_and_goes_on_when_tapped(tmp_path):
    m1 = Model("block")
    async with Running(app_for(tmp_path, m1)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/turns", json={"text": "항공권 세 곳 비교해줘"})
        await until(lambda: m1.started.is_set())
        old = (await tasks(c))[0]

    m2 = Model("ok", reply="비교를 이어서 할게요.")
    async with Running(app_for(tmp_path, m2)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        msgs = await until(lambda: _last_with_replies(c))
        # in the owner's language (no request at start-up): the same words as the task's result
        assert msgs[-1]["content"] in ("Stopped because the server restarted", "서버가 다시 시작되어 멈췄어요")
        label = msgs[-1]["replies"]["questions"][0]["options"][0]
        assert label in ("Continue", "이어서 하기")
        assert msgs[-1]["replies"] == {"questions": [{"question": "", "options": [label]}], "goal_id": None}
        await asyncio.sleep(0.1)
        assert m2.seen == []                                                     # not run again behind the user's back
        events = await follow(c, (await c.post("/api/turns", json={"text": label})).json()["id"])
        assert events[-1]["type"] == "done"
        req = m2.seen[0]
        assert label in last_user(req) and RESTARTED in last_user(req)
        assert any(m.get("content") == "항공권 세 곳 비교해줘" for m in req.messages)   # the conversation holds what was asked
        items = await tasks(c)
        first = next(t for t in items if t["id"] == old["id"])
        assert first["status"] == "interrupted" and first["resumed_as"] == items[0]["id"] and items[0]["resumed_from"] == old["id"]
        assert "replies" not in (await c.get("/api/messages")).json()[-1]


async def _last_with_replies(c):
    msgs = (await c.get("/api/messages")).json()
    return msgs if msgs and "replies" in msgs[-1] else None


async def test_a_chat_turn_after_which_the_user_wrote_again_gets_no_button(tmp_path):
    db = str(tmp_path / "a.db")
    store = Store(db)
    a = Activity(store)
    first = store.add_message("user", "첫 질문")
    tid = a.start_task("첫 질문")
    a.set_resume(tid, {"kind": "chat", "message_id": first})
    store.add_message("user", "다른 이야기")                                     # the user moved on (another phone)
    m = Model("ok")
    async with Running(app_for(tmp_path, m)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await asyncio.sleep(0.1)
        assert all("replies" not in x for x in (await c.get("/api/messages")).json()) and m.seen == []


async def test_an_automatic_run_goes_on_within_the_catch_up_window_and_not_after_it(tmp_path):
    db = str(tmp_path / "a.db")
    s = Schedules(Store(db)).create("아침 확인", "새 메일을 확인해줘", cron="0 9 * * *")
    m1 = Model("block")
    async with Running(app_for(tmp_path, m1)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post(f"/api/schedules/{s['id']}/run")).status_code < 300
        await until(lambda: m1.started.is_set())
        old = (await tasks(c))[0]
        assert old["source"] == f"schedule:{s['id']}"
    m2 = Model("ok", reply="새 메일이 없어요.")
    async with Running(app_for(tmp_path, m2)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await until(lambda: m2.seen)
        assert "server restarted" in last_user(m2.seen[0]) and "새 메일을 확인해줘" in last_user(m2.seen[0])
        items = await until(lambda: _done_resumed(c, old["id"]))
        assert next(t for t in items if t["id"] == old["id"])["status"] == "interrupted"

    # a run cut off three hours ago: too late, its next time comes as usual
    store = Store(db)
    a = Activity(store)
    tid = a.start_task("아침 확인", f"schedule:{s['id']}")
    a.set_resume(tid, {"kind": "schedule", "schedule_id": s["id"]})
    store._run("UPDATE tasks SET started_at = ? WHERE id = ?", (time.time() - 3 * 3600, tid))
    m3 = Model("ok")
    async with Running(app_for(tmp_path, m3)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await asyncio.sleep(0.1)
        assert m3.seen == []
        assert (await c.get(f"/api/tasks/{tid}")).json()["status"] == "interrupted"


async def test_a_task_restarted_more_than_three_times_ends_as_failed(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    a = Activity(store)
    mid = store.add_message("user", GOAL_MARK + " work on goal #1")
    tid = a.start_task("Goal A", "handoff")
    a.set_resume(tid, {"kind": "handoff", "message_id": mid, "title": "Goal A", "head": "Your goal", "quiet": False,
                       "parent_task_id": None, "worker_id": ""}, restarts=3)
    m = Model("ok")
    async with Running(app_for(tmp_path, m)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await asyncio.sleep(0.1)
        t = (await c.get(f"/api/tasks/{tid}")).json()
        assert t["status"] == "failed" and "restarted 4 times" in t["result"] and m.seen == []
        notes = (await c.get("/api/notifications")).json()["items"]
        assert notes[0]["type"] == "task_failed" and notes[0]["task_id"] == tid


async def test_resume_can_be_turned_off(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    a = Activity(store)
    mid = store.add_message("user", GOAL_MARK + " work on goal #1")
    tid = a.start_task("Goal A", "handoff")
    a.set_resume(tid, {"kind": "handoff", "message_id": mid, "title": "Goal A", "head": "", "quiet": False, "parent_task_id": None,
                       "worker_id": ""})
    m = Model("ok")
    async with Running(app_for(tmp_path, m, task_resume_on_start=False)):
        await asyncio.sleep(0.1)
    assert m.seen == []


# ---------------------------------------------------------------------------------------------------------- model outages
def test_only_provider_trouble_counts_as_an_outage():
    assert is_outage(ProviderError("x", status=503)) and is_outage(ProviderError("x", status=429))
    assert is_outage(httpx.ConnectError("refused")) and is_outage(asyncio.TimeoutError())
    assert is_outage(ProviderError("p: request timed out"))
    assert not is_outage(ProviderError("bad request", status=400)) and not is_outage(ProviderError("x", status=401))
    assert not is_outage(ProviderError("p: the prompt is too long for the context window"))
    assert not is_outage(RuntimeError("the model returned an empty reply"))


async def test_a_background_turn_waits_for_the_model_and_goes_on_once_it_is_back(tmp_path):
    m = Model("down", up=False)
    app = app_for(tmp_path, m)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/goals", json={"title": "Goal A"})
        t = await until(lambda: _status(c, "waiting_model"))
        assert t["source"] == "handoff" and t["result"] == "Waiting for the AI model to come back"
        assert (await c.get("/api/notifications")).json()["items"] == []        # no failure notice
        row = Store(str(tmp_path / "a.db"))._all("SELECT wait_since, retry_at FROM tasks WHERE id = ?", (t["id"],))[0]
        assert 50 < row["retry_at"] - row["wait_since"] < 70                      # the first check after a minute

        tick = app.state.model_wait_tick
        assert await tick(row["wait_since"] + 30) == {}                          # not due yet: no check
        assert await tick(row["wait_since"] + 61) == {t["id"]: "waiting"} and m.checks == 1
        later = Store(str(tmp_path / "a.db"))._all("SELECT retry_at, waits FROM tasks WHERE id = ?", (t["id"],))[0]
        assert later["waits"] == 1 and 290 < later["retry_at"] - (row["wait_since"] + 61) < 310   # then after 5 minutes
        assert await tick(row["wait_since"] + 7 * 3600) == {t["id"]: "waiting"}   # still down after hours: one word to the user
        notes = (await c.get("/api/notifications")).json()["items"]
        assert [n["type"] for n in notes] == ["task_waiting"]
        await tick(row["wait_since"] + 9 * 3600)
        assert len((await c.get("/api/notifications")).json()["items"]) == 1      # only once

        m.mode, m.up, m.seen = "ok", True, []
        assert await tick(row["wait_since"] + 10 * 3600) == {t["id"]: "resumed"}
        await until(lambda: m.seen)
        sent = last_user(m.seen[0])
        assert GOAL_MARK in sent and MODEL_BACK in sent
        items = await until(lambda: _done_resumed(c, t["id"]))
        first = next(x for x in items if x["id"] == t["id"])
        assert first["status"] == "interrupted" and first["result"] == "Went on once the AI model was back"


async def _status(c, status):
    items = await tasks(c)
    return items[0] if items and items[0]["status"] == status else None


async def test_a_chat_turn_stops_with_a_continue_button_and_goes_on_once_the_model_is_back(tmp_path):
    m = Model("down", up=False)
    app = app_for(tmp_path, m)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "날씨 알려줘"})).json()["id"])
        assert [e["type"] for e in events if e["type"] in ("text", "replies", "done", "error")] == ["text", "replies", "done"]
        msgs = (await c.get("/api/messages")).json()
        assert msgs[-1]["content"] == "The AI model connection dropped, so I stopped"
        assert msgs[-1]["replies"]["questions"] == [{"question": "", "options": ["Continue"]}]
        t = (await tasks(c))[0]
        assert t["status"] == "waiting_model"

        m.mode, m.up, m.seen = "ok", True, []
        assert await app.state.model_wait_tick(time.time() + 61) == {t["id"]: "resumed"}
        await until(lambda: m.seen)
        assert MODEL_BACK in last_user(m.seen[0])
        assert any(x.get("content") == "날씨 알려줘" for x in m.seen[0].messages)
        msgs = await until(lambda: _messages_end_with(c, "이어서 했어요."))
        assert "replies" not in msgs[-2]                                          # the button went with the turn


async def _messages_end_with(c, text):
    msgs = (await c.get("/api/messages")).json()
    return msgs if msgs and msgs[-1]["content"] == text else None


async def test_a_chat_turn_does_not_go_on_by_itself_when_the_user_wrote_since(tmp_path):
    m = Model("down", up=False)
    app = app_for(tmp_path, m)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "날씨 알려줘"})).json()["id"])
        t = (await tasks(c))[0]
        m.mode, m.reply = "ok", "다른 답"
        await follow(c, (await c.post("/api/turns", json={"text": "됐어, 다른 거 물어볼게"})).json()["id"])
        m.up, m.seen = True, []
        assert await app.state.model_wait_tick(time.time() + 61) == {t["id"]: "skipped"}
        await asyncio.sleep(0.05)
        assert m.seen == [] and (await c.get(f"/api/tasks/{t['id']}")).json()["status"] == "interrupted"


async def test_a_content_error_still_fails_at_once(tmp_path):
    async def bad(provider, request, **kw):
        raise ProviderError("bad request: invalid tool schema", status=400)
        yield  # pragma: no cover

    async with Running(app_for(tmp_path, bad)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "안녕"})).json()["id"])
        assert events[-1]["type"] == "error"
        assert (await tasks(c))[0]["status"] == "failed"
        assert json.dumps(events)
