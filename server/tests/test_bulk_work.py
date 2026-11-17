"""Long work the user asked for never just dies at a step budget, and a reply never shows an exception's text.

- a chat reply that runs out of steps while it gets somewhere hands the rest to a background job, which goes on in stretches (each
  with a fresh budget) until the whole 150-item job is done, with no action from the user;
- a run going in circles (the same failing step again) stops with a plain line, in the chat and in a job;
- a model that ran tools but never wrote an answer ends with a translated line and a Continue button; a crash shows the user's words.
Fake model only."""

import asyncio

import httpx

from assistant import i18n
from assistant.agent import Tool
from assistant.providers import Done, TextDelta
from assistant.server import NO_ANSWER_LINE, STEP_LIMIT_BACKGROUND, STEP_LIMIT_STOPPED, create_app
from assistant.workers import NO_PROGRESS

from test_agent_profile import app_settings
from test_server import AUTH, Running, follow
from test_workers import Brain, call, one_worker, say, until, worker_list

ITEMS = 150


def no_tools(req):
    return not req.tools


async def test_a_150_item_job_runs_to_the_end_across_stretches_with_no_user_action(tmp_path):
    handled: list[int] = []

    async def handle(args):
        i = int(args["item"])
        if i not in handled:
            handled.append(i)
        return f"item {i} done"

    def chat(req, n):
        if no_tools(req):   # the reply's budget is used up: its note on what is done and what is left
            return say(f"{len(handled)}개 했고 나머지는 남았어요.")
        return call("handle_item", {"item": len(handled)}, cid=f"c{n}")

    def worker(req, n):
        if len(handled) >= ITEMS:
            return call("report", {"status": "done", "summary": f"{ITEMS}개 모두 했어요."}, cid=f"r{n}")
        return call("handle_item", {"item": len(handled)}, cid=f"w{n}")

    brain = Brain(chat=chat, worker=worker, handoff=lambda r, n: say("모두 끝났어요."))
    tools = [Tool("handle_item", "x", {"type": "object"}, handle)]
    app = create_app(app_settings(tmp_path, max_steps=5, worker_steps=40), tools=tools, stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "150개 전부 처리해줘"})).json()["id"])
        limit = next(e for e in events if e["type"] == "step_limit")
        assert limit["next"] == "background" and limit["job_id"] and limit["progress"] >= 2
        text = "".join(e["text"] for e in events if e["type"] == "text")
        assert "나머지는 남았어요" in text and i18n.t(STEP_LIMIT_BACKGROUND, lang="ko") in text
        assert not [e for e in events if e["type"] == "error"] and events[-1]["type"] == "done"
        assert not [e for e in events if e["type"] == "replies"]          # nothing to press: it goes on by itself
        w = await one_worker(c)
        assert w["id"] == limit["job_id"]
        done = await until(lambda: one_worker(c, "done"), tries=3000)
        assert sorted(handled) == list(range(ITEMS))
        detail = (await c.get(f"/api/workers/{done['id']}")).json()
        assert detail["segments"] > 3                                     # several stretches, each with a fresh budget
        assert detail["report"]["summary"] == f"{ITEMS}개 모두 했어요."
        assert not (await c.get("/api/approvals/pending")).json().get("items")


async def test_a_reply_going_in_circles_stops_with_a_plain_line_and_continue(tmp_path):
    async def broken(args):
        raise RuntimeError("the button is not there")

    def chat(req, n):
        return say("같은 버튼이 계속 안 눌려요.") if no_tools(req) else call("press", {"what": "same"}, cid=f"c{n}")

    tools = [Tool("press", "x", {"type": "object"}, broken)]
    app = create_app(app_settings(tmp_path, max_steps=4), tools=tools, stream_fn=Brain(chat=chat), with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "눌러줘"})).json()["id"])
        limit = next(e for e in events if e["type"] == "step_limit")
        assert limit["next"] == "stopped" and limit["progress"] == 0 and "job_id" not in limit
        text = "".join(e["text"] for e in events if e["type"] == "text")
        assert i18n.t(STEP_LIMIT_STOPPED, lang="ko") in text
        assert [e for e in events if e["type"] == "replies"][0]["questions"][0]["options"] == [i18n.t("Continue", lang="ko")]
        assert not [e for e in events if e["type"] == "error"]
        assert (await c.get("/api/workers")).json()["items"] == []


async def test_a_job_going_in_circles_stops_with_a_clear_message(tmp_path):
    async def broken(args):
        raise RuntimeError("the button is not there")

    def chat(req, n):
        return say("맡겼어요.") if req.messages[-1].get("role") == "tool" else call("delegate", {"task": "버튼을 눌러줘", "kind": "general"})

    def worker(req, n):
        return call("press", {"what": "same"}, cid=f"w{n}")

    tools = [Tool("press", "x", {"type": "object"}, broken)]
    app = create_app(app_settings(tmp_path, worker_steps=40), tools=tools, stream_fn=Brain(chat=chat, worker=worker), with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "눌러줘"})).json()["id"])
        w = await until(lambda: one_worker(c, "blocked"), tries=1500)
        assert w["reason"] == i18n.t(NO_PROGRESS)
        assert (await c.get(f"/api/workers/{w['id']}")).json()["segments"] == 1     # no fresh budget for a loop


async def test_tools_but_no_answer_ends_with_a_plain_line_and_a_crash_shows_no_exception_text(tmp_path):
    async def look(args):
        return "ok"

    def chat(req, n):
        return call("look", {"n": n}, cid=f"c{n}") if n == 0 else [Done("stop")]

    app = create_app(app_settings(tmp_path), tools=[Tool("look", "x", {"type": "object"}, look)], stream_fn=Brain(chat=chat))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "봐줘"})).json()["id"])
        text = "".join(e["text"] for e in events if e["type"] == "text")
        assert text == i18n.t(NO_ANSWER_LINE, lang="ko")
        assert [e for e in events if e["type"] == "replies"] and events[-1]["type"] == "done"
        assert not any("RuntimeError" in str(e) or "gave no answer" in str(e) for e in events)

    async def crash(provider, request, **kw):
        raise RuntimeError("internal detail 42")
        yield  # pragma: no cover

    app = create_app(app_settings(tmp_path / "b"), tools=[], stream_fn=crash)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "안녕"})).json()["id"])
        err = next(e for e in events if e["type"] == "error")
        assert "RuntimeError" not in err["message"] and "42" not in err["message"] and err["message"].strip()
        assert err["message"] != "Something went wrong on the server, so I stopped. Please try again in a moment."   # translated



# ---------------------------------------------------------------------------------------- one request never becomes two jobs
def long_chat(first_call=None):
    """A chat that (optionally) hands a job over first, then works step after step with distinct calls until its budget is gone."""
    def chat(req, n):
        if no_tools(req):
            return say("여기까지 했어요.")
        if n == 0 and first_call is not None:
            return call(*first_call)
        return call("handle_item", {"item": n}, cid=f"c{n}")
    return chat


async def _handle(args):
    return f"item {args['item']} done"


def _jobs_app(tmp_path, chat, **kw):
    gate_worker = lambda req, n: call("press_nothing", {}, cid=f"w{n}") if False else say("")   # noqa: E731 - never reached
    brain = Brain(chat=chat, worker=kw.pop("worker", gate_worker))
    tools = [Tool("handle_item", "x", {"type": "object"}, _handle)]
    return create_app(app_settings(tmp_path, max_steps=6), tools=tools, stream_fn=brain, with_worker_tools=True), brain


async def _hold(req, n):
    await asyncio.sleep(30)   # the job stays open for the whole test
    return say("")


async def test_a_turn_that_delegated_and_hits_the_step_limit_starts_no_second_job(tmp_path):
    app, _ = _jobs_app(tmp_path, long_chat(("delegate", {"task": "log in to the shop app", "kind": "general"})), worker=_hold)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "쇼핑 앱에 로그인해줘"})).json()["id"])
        limit = next(e for e in events if e["type"] == "step_limit")
        jobs = await worker_list(c)
        assert len(jobs) == 1 and limit["next"] == "background" and limit["job_id"] == jobs[0]["id"]
        assert i18n.t(STEP_LIMIT_BACKGROUND, lang="ko") in "".join(e["text"] for e in events if e["type"] == "text")
        await app.state.workers.stop(jobs[0]["id"])


async def test_an_open_job_on_the_phone_blocks_the_continuation(tmp_path):
    app, _ = _jobs_app(tmp_path, long_chat(), worker=_hold)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        out = await app.state.workers.delegate({"task": "hold the phone", "kind": "general"})
        wid = out.split("(job ")[1].split(":")[0]
        app.state.workers.store._run("UPDATE workers SET status = 'needs_input', wait_on = ? WHERE id = ?", ('{"kind": "device"}', wid))
        events = await follow(c, (await c.post("/api/turns", json={"text": "계속 해줘"})).json()["id"])
        limit = next(e for e in events if e["type"] == "step_limit")
        assert limit["next"] == "background" and limit["job_id"] == wid and len(await worker_list(c)) == 1
        await app.state.workers.stop(wid)


async def test_an_open_job_in_the_replys_own_tab_blocks_the_continuation(tmp_path):
    app, _ = _jobs_app(tmp_path, long_chat(), worker=_hold)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        out = await app.state.workers.delegate({"task": "work in that tab", "kind": "general"})
        wid = out.split("(job ")[1].split(":")[0]
        app.state.workers.store._run("UPDATE workers SET tab = 'owner' WHERE id = ?", (wid,))   # a chat reply's tab key
        events = await follow(c, (await c.post("/api/turns", json={"text": "계속 해줘"})).json()["id"])
        limit = next(e for e in events if e["type"] == "step_limit")
        assert limit["job_id"] == wid and len(await worker_list(c)) == 1
        await app.state.workers.stop(wid)


async def test_without_an_open_job_the_continuation_is_created_with_a_title_in_the_owners_language(tmp_path):
    for lang, expect in (("ko", "이어서 진행: "), ("en", "Carrying on: ")):
        (tmp_path / lang).mkdir()
        app, _ = _jobs_app(tmp_path / lang, long_chat(), worker=_hold)
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": lang}) as c:
            asked = "아주 긴 요청입니다 " * 10 if lang == "ko" else "a very long request about many things " * 5
            events = await follow(c, (await c.post("/api/turns", json={"text": asked})).json()["id"])
            limit = next(e for e in events if e["type"] == "step_limit")
            jobs = await worker_list(c)
            assert len(jobs) == 1 and limit["job_id"] == jobs[0]["id"]
            title = jobs[0]["title"]
            assert title.startswith(expect) and len(title) <= len(expect) + 45
            assert "Carry on with the user's request" not in title and "chat reply ran out" not in title
            await app.state.workers.stop(jobs[0]["id"])
