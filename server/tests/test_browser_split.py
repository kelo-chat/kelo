"""The browser split (docs/browser-workers.md, tool_roles.py): the chat does not click or type on websites; web work goes to a `delegate`
job of kind browser, which has every browser tool; the chat keeps the tools that read or show a page. A job that asks (needs_input)
carries a question id, and worker_message(answer_to=id) answers exactly that question. Fake model, no real browser, no model calls."""

import re
from pathlib import Path

import httpx

from assistant import tool_roles
from assistant.agent import Tool
from assistant.providers import Done, TextDelta
from assistant.server import create_app
from assistant.settings import Settings
from assistant.tool_roles import (BROWSER_CHAT_KEEPS, PAY_TOOLS, PHONE_TOOLS, checkout_tools, finder_tools, for_kind,
                                  is_browser_acting_tool, phone_tools)
from assistant.tools.browser import BrowserSession

from test_agent_profile import ShotPage, app_settings
from test_server import AUTH, Running, follow
from test_toolbox import scripted
from test_workers import Brain, call, one_worker, say


async def _noop(args):
    return "ok"


def fake(*names):
    return [Tool(n, n, {"type": "object"}, _noop) for n in names]


def names(tools):
    return {t.name for t in tools}


ACTING = ("browser_click", "browser_click_at", "browser_type", "browser_drag", "browser_upload", "browser_login", "browser_look",
          "browser_human_check", "browser_private", "browser_fill_shipping", "find_login_code")
KEPT = ("browser_site_state", "browser_forget_site")   # the chat's data controls only (owner 2026-11-19 03:10: no web work in the chat)
JOB_WEB = ("browser_open", "browser_read", "browser_downloads", "browser_save_download", "browser_save_pdf")
OTHERS = ("web_search", "web_fetch", "read_file", "delegate", "begin_order", "request_login", "vault_status", "checkout_review",
          "android_tap", "android_send_screenshot")
APP_TOOLS = (*ACTING, *KEPT, *JOB_WEB, *OTHERS)


# ---------------------------------------------------------------------------------------------------------- tool sets
def test_the_chat_keeps_the_reading_tools_and_loses_every_acting_one():
    tools = fake(*APP_TOOLS)
    chat = names(for_kind(tools, None))
    assert not chat & set(ACTING)
    assert set(KEPT) <= chat and {"web_search", "web_fetch", "delegate", "begin_order", "request_login"} <= chat
    assert not chat & set(JOB_WEB)                                                   # the chat opens and reads no page: a job does
    assert names(for_kind(tools, None, browser_split=False)) >= set(ACTING)        # the kill switch: the old chat


def test_a_job_of_kind_browser_general_or_research_keeps_every_browser_tool():
    tools = fake(*APP_TOOLS)
    for kind in ("browser", "general", "research"):
        assert set(ACTING) | set(KEPT) | set(JOB_WEB) <= names(for_kind(tools, kind, browser_split=True)), kind


def test_the_checkout_and_phone_sets_do_not_change_with_the_browser_split():
    tools = fake(*APP_TOOLS)
    assert names(for_kind(tools, "checkout", browser_split=True)) == names(for_kind(tools, "checkout", browser_split=False)) == names(checkout_tools(tools))
    assert names(for_kind(tools, "phone", browser_split=True)) == names(phone_tools(tools))
    assert names(finder_tools(tools)) == names(for_kind(tools, "general"))          # finder_tools alone does not know the split
    assert not names(for_kind(tools, None)) & PAY_TOOLS and not names(for_kind(tools, None)) & PHONE_TOOLS - {"android_send_screenshot"}


def test_a_new_browser_tool_is_a_jobs_until_it_is_listed_for_the_chat():
    assert is_browser_acting_tool("browser_something_new") and is_browser_acting_tool("find_login_code")
    assert not any(is_browser_acting_tool(n) for n in BROWSER_CHAT_KEEPS) and not is_browser_acting_tool("web_fetch")


async def test_every_browser_tool_of_the_real_app_is_either_kept_by_the_chat_or_declared_acting(tmp_path):
    settings = app_settings(tmp_path, home_dir=str(tmp_path / "home"), browser_worker=True)
    settings.cdp_host = "127.0.0.1"   # the browser tools are made; nothing connects until one runs
    app = create_app(settings, stream_fn=scripted([TextDelta("ok"), Done("stop")]), with_schedule_check=False)
    real = {t.name for t in app.state.tools if t.name.startswith("browser_")}
    assert BROWSER_CHAT_KEEPS <= real                                               # every kept name is a real tool
    chat = names(for_kind(app.state.tools, None))
    assert not {n for n in chat if is_browser_acting_tool(n)}
    assert {n for n in chat if n.startswith("browser_")} == BROWSER_CHAT_KEEPS | ({"browser_fill_card", "browser_pin_keypad_card"} & chat)
    assert {"browser_click", "browser_type", "browser_login", "browser_look"} <= real - chat
    finder = next(t for t in app.state.tools if t.name == "find_tools")
    # the chat's own tool search is never pointed at a tool it cannot call
    assert "browser_click_at" not in finder.description and "find_login_code" not in finder.description


def test_the_setting_is_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("ASSISTANT_BROWSER_WORKER", raising=False)
    assert Settings(token="t" * 32).browser_worker is True
    assert Settings.from_env({"ASSISTANT_TOKEN": "t" * 32}).browser_worker is True
    assert Settings.from_env({"ASSISTANT_TOKEN": "t" * 32, "ASSISTANT_BROWSER_WORKER": "0"}).browser_worker is False
    assert Settings.from_env({"ASSISTANT_TOKEN": "t" * 32, "ASSISTANT_BROWSER_WORKER": "1"}).browser_worker is True


# ---------------------------------------------------------------------------------------------------------- a turn
def _names_of(req):
    return {t["function"]["name"] for t in req.tools}


def _app(tmp_path, brain, **kw):
    async def connect():
        return ShotPage("https://shop.example.com/cart")

    return create_app(app_settings(tmp_path, **{"browser_worker": True, **kw}), tools=fake(*APP_TOOLS), stream_fn=brain, with_worker_tools=True,
                      browser=BrowserSession(connect=connect))


async def test_asked_to_book_on_a_site_the_chat_delegates_a_browser_job_and_cannot_click(tmp_path):
    seen = {"clicks": 0}

    def chat(req, n):
        seen.setdefault("chat", _names_of(req))
        if req.messages[-1].get("role") == "tool":
            seen.setdefault("result", req.messages[-1]["content"])
            return say("Started; I will tell you when it is done.")
        return call("delegate", {"task": "Book a table for 2 at 19:00 on Friday at the restaurant on https://tables.example.com; "
                                         "the user wants the window side and no more than 60 EUR.", "kind": "browser"})

    def worker(req, n):
        seen["job"] = _names_of(req)
        seen["brief"] = str(next(m["content"] for m in req.messages if m.get("role") == "user"))
        return call("report", {"status": "done", "summary": "Booked."})

    app = _app(tmp_path, Brain(chat=chat, worker=worker))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "book a table for two on Friday at tables.example.com"})).json()["id"])
        w = await one_worker(c, "done")
    assert w["kind"] == "browser"
    assert "delegate" in seen["chat"] and "browser_read" not in seen["chat"] and not seen["chat"] & (set(ACTING) | set(JOB_WEB))   # the chat cannot read or click a page
    assert set(ACTING) <= seen["job"] and set(KEPT) - {"browser_forget_site"} <= seen["job"]    # forgetting a site's data is the chat's alone (MAIN_ONLY)
    assert "you are that job" in seen["brief"] and "window side" in seen["brief"]


async def test_with_the_kill_switch_off_the_chat_has_the_browser_tools_again(tmp_path):
    seen = {}

    def chat(req, n):
        seen["chat"] = _names_of(req)
        return say("ok")

    app = _app(tmp_path, Brain(chat=chat), browser_worker=False)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "hi"})).json()["id"])
    assert {"browser_click", "browser_type", "browser_login", "browser_look"} <= seen["chat"]


def test_the_prompt_follows_the_browser_switch_and_only_in_the_websites_and_buying_paragraphs():
    from assistant.prompt import PromptBuilder
    content = Path(__file__).resolve().parents[1] / "content"
    texts = {(p, b): PromptBuilder.load(content, None, None, pay_split=p, browser_split=b).base for p in (False, True) for b in (False, True)}
    buying = lambda t: t.split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0]   # noqa: E731
    for pay in (False, True):
        on, off = texts[(pay, True)], texts[(pay, False)]
        assert "**Websites.** You do not click or type on websites" in on and "**Websites.**" not in off
        assert "`delegate` job of kind browser" in on and "self-contained `task`" in on and "answer_to" in on and "`web_fetch`" in on
        assert "Do it yourself in the browser" not in on and "begin_order" in buying(on)
        assert on.split("**Websites.**")[0] == off.split("**Buying, ordering, booking.**")[0]
        assert on.split("\n\n**Later and repeated work.**", 1)[1] == off.split("\n\n**Later and repeated work.**", 1)[1]
    assert "delegate` kind checkout" in buying(texts[(True, True)]) and "delegate` kind checkout" not in buying(texts[(False, True)])
    assert texts[(False, False)] == (content / "prompts" / "system.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------- question ids
QID = re.compile(r"q_[0-9a-f]{16}")


def _asking_app(tmp_path, seen, steps):
    """A chat that delegates a browser job, then runs `steps` (one tool call per later turn); the job asks once, then finishes."""
    def chat(req, n):
        last = req.messages[-1]
        text = str(next((m["content"] for m in reversed(req.messages) if m.get("role") == "user"), ""))
        if last.get("role") == "tool":
            seen.setdefault("results", []).append(last["content"])
            return say("ok")
        if text == "start":
            return call("delegate", {"task": "order the blue one", "kind": "browser"})
        return steps(text, req)

    def worker(req, n):
        seen.setdefault("worker_requests", []).append(req)
        if n == 0:
            return call("report", {"status": "needs_input", "summary": "Which size: S or M?"})
        return call("report", {"status": "done", "summary": "ordered"})

    return _app(tmp_path, Brain(chat=chat, worker=worker))


async def _start(c):
    await follow(c, (await c.post("/api/turns", json={"text": "start"})).json()["id"])
    return await one_worker(c, "needs_input")


async def test_a_needs_input_report_stores_a_question_id_that_the_chat_is_shown(tmp_path):
    seen = {}

    def steps(text, req):
        return call("worker_status", {"id": text})

    app = _asking_app(tmp_path, seen, steps)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        w = await _start(c)
        qid = w["question_id"]
        assert QID.fullmatch(qid)
        assert (await c.get(f"/api/workers/{w['id']}")).json()["question_id"] == qid
        await follow(c, (await c.post("/api/turns", json={"text": w["id"]})).json()["id"])
    assert qid in seen["results"][-1] and "answer_to" in seen["results"][-1]


async def test_worker_message_with_the_open_question_id_answers_it(tmp_path):
    seen = {}
    holder = {}

    def steps(text, req):
        return call("worker_message", {"id": holder["id"], "text": "M", "answer_to": holder["q"]})

    app = _asking_app(tmp_path, seen, steps)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        w = await _start(c)
        holder.update(id=w["id"], q=w["question_id"])
        await follow(c, (await c.post("/api/turns", json={"text": "answer"})).json()["id"])
        done = await one_worker(c, "done")
    assert "goes on now" in seen["results"][-1] and not seen["results"][-1].startswith("Error")
    assert "Message for you: M" in str(seen["worker_requests"][-1].messages[-1]["content"])
    assert "question_id" not in done


async def test_a_wrong_question_id_is_refused_and_the_job_stays_waiting(tmp_path):
    seen = {}
    holder = {}

    def steps(text, req):
        return call("worker_message", {"id": holder["id"], "text": "M", "answer_to": "q_0000000000000000"})

    app = _asking_app(tmp_path, seen, steps)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        w = await _start(c)
        holder["id"] = w["id"]
        await follow(c, (await c.post("/api/turns", json={"text": "answer"})).json()["id"])
        still = await one_worker(c, "needs_input")
    result = seen["results"][-1]
    assert result.startswith("Error:") and w["question_id"] in result and "q_0000000000000000" in result
    assert still["question_id"] == w["question_id"] and len(seen["worker_requests"]) == 1


async def test_a_stale_question_id_is_refused_once_the_job_went_on(tmp_path):
    seen = {}
    holder = {}

    def steps(text, req):
        if text == "answer":
            return call("worker_message", {"id": holder["id"], "text": "M", "answer_to": holder["q"]})
        return call("worker_message", {"id": holder["id"], "text": "again", "answer_to": holder["q"]})

    app = _asking_app(tmp_path, seen, steps)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        w = await _start(c)
        holder.update(id=w["id"], q=w["question_id"])
        await follow(c, (await c.post("/api/turns", json={"text": "answer"})).json()["id"])
        await one_worker(c, "done")
        await follow(c, (await c.post("/api/turns", json={"text": "again"})).json()["id"])
    assert seen["results"][-1].startswith("Error:") and "no open question" in seen["results"][-1]
    assert len(seen["worker_requests"]) == 2


async def test_a_message_without_answer_to_is_an_update_as_before(tmp_path):
    seen = {}
    holder = {}

    def steps(text, req):
        return call("worker_message", {"id": holder["id"], "text": "a note"})

    app = _asking_app(tmp_path, seen, steps)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        w = await _start(c)
        holder["id"] = w["id"]
        await follow(c, (await c.post("/api/turns", json={"text": "update"})).json()["id"])
        await one_worker(c, "done")
    assert "goes on now" in seen["results"][-1] and "Message for you: a note" in str(seen["worker_requests"][-1].messages[-1]["content"])


def test_the_handoff_names_the_question_id_and_the_answer_call():
    from assistant.workers import WorkerHost, Workers, new_question_id
    from assistant.store import Store
    from assistant.workers import Limits
    ws = Workers(Store(), WorkerHost(start_turn=lambda *a: None, cancel_turn=lambda t: None, handoff=None, emit=None, tools=lambda: []), Limits())
    qid = new_question_id()
    assert QID.fullmatch(qid) and qid != new_question_id()
    row = {"id": "w1", "title": "shop", "kind": "browser", "parent_source": "chat", "question_id": qid}
    text = ws._handoff_text(row, {"status": "needs_input", "summary": "size?", "details": ""}, {"kind": "user"})
    assert f"Question id: {qid}" in text and f"answer_to='{qid}'" in text


def test_an_old_database_gets_the_question_id_column():
    from assistant.store import Store
    from assistant.workers import WorkerHost, Workers
    store = Store()
    store._db.execute("CREATE TABLE workers (id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, task TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', "
                      "files TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, parent_task_id INTEGER, parent_source TEXT NOT NULL DEFAULT 'chat', "
                      "task_id INTEGER, turn_id TEXT NOT NULL DEFAULT '', tab TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
                      "finished_at REAL, steps INTEGER NOT NULL DEFAULT 0, max_steps INTEGER NOT NULL, deadline_at REAL NOT NULL, minutes INTEGER NOT NULL, "
                      "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, token_cap INTEGER NOT NULL, "
                      "report TEXT NOT NULL DEFAULT '', wait_on TEXT NOT NULL DEFAULT '', login TEXT NOT NULL DEFAULT '', last_url TEXT NOT NULL DEFAULT '', "
                      "status_text TEXT NOT NULL DEFAULT '', lines TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '', keep_tab INTEGER NOT NULL DEFAULT 0, "
                      "segments INTEGER NOT NULL DEFAULT 0, auto_resume INTEGER NOT NULL DEFAULT 0)")
    Workers(store, WorkerHost(start_turn=lambda *a: None, cancel_turn=lambda t: None, handoff=None, emit=None, tools=lambda: []))
    assert "question_id" in {r[1] for r in store._db.execute("PRAGMA table_info(workers)")}
