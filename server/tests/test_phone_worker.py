"""The phone split (docs/phone-worker.md, tool_roles.py): only a job of kind "phone" drives the Android device; every other run (the
chat, other jobs) is shown no phone tool; one open phone job at a time. Fake model, no device, no model calls."""

import httpx

from assistant import tool_roles
from assistant.agent import Tool
from assistant.providers import Done, TextDelta
from assistant.server import create_app
from assistant.tool_roles import PAY_TOOLS, PHONE_CHAT_TOOLS, PHONE_HELPERS, PHONE_TOOLS, finder_tools, for_kind, is_phone_tool, phone_tools
from assistant.workers import KINDS, MAIN_ONLY

from test_agent_profile import app_settings
from test_android_tools import TOKEN
from test_server import AUTH, Running, follow
from test_toolbox import scripted
from test_workers import Brain, call, one_worker, say


async def _noop(args):
    return "ok"


def fake(*names):
    return [Tool(n, n, {"type": "object"}, _noop) for n in names]


def names(tools):
    return {t.name for t in tools}


PHONE = sorted(PHONE_TOOLS)
APP_TOOLS = (*PHONE, "android_send_screenshot", "checkout_review", "browser_read", "browser_click", "web_search", "web_fetch", "read_file",
             "request_login", "vault_status", "find_login_code", "read_skill", "action_result", "begin_order", "request_task_permission")


def test_the_phone_tools_are_taken_by_prefix_and_the_chat_keeps_only_its_own_picture_tool():
    assert PHONE_TOOLS == {n for n in PHONE_TOOLS if n.startswith("android_")} and len(PHONE_TOOLS) == 8
    assert PHONE_CHAT_TOOLS == {"android_send_screenshot", "android_show"} and not PHONE_CHAT_TOOLS & PHONE_TOOLS
    assert is_phone_tool("android_anything_new") and not is_phone_tool("android_send_screenshot") and not is_phone_tool("android_show")
    assert not is_phone_tool("browser_read")
    assert "phone" in KINDS and tool_roles.PHONE_KIND == "phone"


def test_every_other_run_has_no_phone_tool_and_the_phone_job_has_only_its_own_set():
    tools = fake(*APP_TOOLS)
    for kind in (None, "browser", "research", "general", "checkout"):
        shown = names(for_kind(tools, kind))
        assert not shown & PHONE_TOOLS, kind
        assert "android_send_screenshot" in shown or kind == "checkout"
    assert {"browser_read", "web_search", "read_file"} <= names(for_kind(tools, "browser")) and not names(for_kind(tools, None)) & PAY_TOOLS
    phone = names(for_kind(tools, "phone"))
    assert phone == names(phone_tools(tools)) == PHONE_TOOLS | (PHONE_HELPERS & names(tools))
    assert not phone & PAY_TOOLS and not any(n.startswith("browser_") for n in phone)
    assert not phone & {"web_search", "web_fetch", "read_file", "delegate", "begin_order", "android_send_screenshot", "request_task_permission"}
    assert {"vault_status", "request_login", "find_login_code", "read_skill", "action_result"} <= phone


def test_the_two_splits_are_two_switches_of_the_one_function():
    tools = fake(*APP_TOOLS)
    assert names(for_kind(tools, None, phone_split=False)) >= PHONE_TOOLS and not names(for_kind(tools, None, phone_split=False)) & PAY_TOOLS
    assert names(for_kind(tools, None, pay_split=False)) >= {"checkout_review"} and not names(for_kind(tools, None, pay_split=False)) & PHONE_TOOLS
    assert names(for_kind(tools, "browser", pay_split=False, phone_split=False)) == names(tools)
    assert names(finder_tools(tools)) == names(for_kind(tools, "general"))
    assert names(for_kind(tools, "phone", phone_split=False)) == names(phone_tools(tools))   # the phone job keeps its role


def test_the_phone_job_never_has_a_tool_that_steers_jobs():
    assert not PHONE_TOOLS & set(MAIN_ONLY) and not PHONE_HELPERS & (set(MAIN_ONLY) - {"report"})


def test_every_name_of_the_role_sets_is_a_real_tool_of_the_app(tmp_path):
    on = {"android_url": "http://android:8090", "android_token": TOKEN, "android_device": "192.168.1.50:5555"}
    app = create_app(app_settings(tmp_path, home_dir=str(tmp_path / "home"), **on), stream_fn=scripted([TextDelta("ok"), Done("stop")]),
                     with_schedule_check=False)
    real = names(app.state.tools)
    assert PHONE_TOOLS <= real and PHONE_CHAT_TOOLS <= real
    assert {n for n in real if n.startswith("android_")} == PHONE_TOOLS | PHONE_CHAT_TOOLS      # a new android_* tool joins the phone job by itself
    assert PHONE_HELPERS - {"find_login_code", "report", "job_progress"} <= real
    finder = next(t for t in app.state.tools if t.name == "find_tools")
    # the chat is pointed only at its own two phone tools (the screenshot in the reply, the hand-over); "android_*" in the map means those
    assert "android_tap" not in finder.description and "android_look" not in finder.description


def _names_of(req):
    return {t["function"]["name"] for t in req.tools}


def _app(tmp_path, brain, tools=APP_TOOLS, **kw):
    return create_app(app_settings(tmp_path, **kw), tools=fake(*tools), stream_fn=brain, with_worker_tools=True)


def _delegating(seen, task="open the Shop app and look up the order"):
    def chat(req, n):
        seen.setdefault("chat", _names_of(req))
        last = req.messages[-1]
        if last.get("role") == "tool":
            seen.setdefault("results", []).append(last["content"])
            return say("ok")
        return call("delegate", {"task": task, "kind": "phone"})
    return chat


async def test_the_chat_cannot_drive_the_phone_but_a_phone_job_can_and_gets_a_brief(tmp_path):
    seen = {}

    def worker(req, n):
        seen["tools"] = _names_of(req)
        seen["brief"] = str(next(m["content"] for m in req.messages if m.get("role") == "user"))
        return call("report", {"status": "needs_input", "summary": "which size?"})

    app = _app(tmp_path, Brain(chat=_delegating(seen), worker=worker))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "check my order in the Shop app"})).json()["id"])
        w = await one_worker(c, "needs_input")
    assert w["kind"] == "phone"
    assert "delegate" in seen["chat"] and "android_send_screenshot" in seen["chat"] and not seen["chat"] & PHONE_TOOLS
    t = seen["tools"]
    assert PHONE_TOOLS <= t and {"report", "job_progress"} <= t
    assert not t & PAY_TOOLS and not any(n.startswith("browser_") for n in t)
    assert not t & {"web_search", "web_fetch", "read_file", "delegate", "begin_order", "request_task_permission", "android_send_screenshot"}
    assert "phone worker" in seen["brief"] and "needs_input" in seen["brief"] and "open the Shop app" in seen["brief"]
    assert "on the Android device" in seen["results"][0]


async def test_a_research_job_is_told_it_has_no_phone_tools_and_has_none(tmp_path):
    seen = {}

    def worker(req, n):
        seen["tools"] = _names_of(req)
        seen["brief"] = str(next(m["content"] for m in req.messages if m.get("role") == "user"))
        return call("report", {"status": "done", "summary": "s"})

    def research_chat(req, n):
        if req.messages[-1].get("role") == "tool":
            return say("ok")
        return call("delegate", {"task": "find the opening hours", "kind": "research"})

    app = _app(tmp_path, Brain(chat=research_chat, worker=worker))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "go"})).json()["id"])
        await one_worker(c, "done")
    assert not seen["tools"] & PHONE_TOOLS and "web_search" in seen["tools"]
    assert "You have no phone tools" in seen["brief"]


async def test_only_one_phone_job_is_open_and_the_refusal_names_it(tmp_path):
    seen = {}

    def worker(req, n):
        return call("report", {"status": "needs_input", "summary": "which size?"})

    app = _app(tmp_path, Brain(chat=_delegating(seen), worker=worker))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "one"})).json()["id"])
        w = await one_worker(c, "needs_input")
        await follow(c, (await c.post("/api/turns", json={"text": "two"})).json()["id"])
    refused = seen["results"][-1]
    assert refused.startswith("Error:") and "phone job" in refused and w["id"] in refused and "worker_message" in refused and "resume" in refused


async def test_a_phone_job_needs_a_device_on_this_copy(tmp_path):
    seen = {}
    app = _app(tmp_path, Brain(chat=_delegating(seen)), tools=("browser_read", "web_search", "read_file"))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "one"})).json()["id"])
    assert seen["results"][0].startswith("Error:") and "no Android device" in seen["results"][0]


async def test_with_the_kill_switch_off_every_run_has_the_phone_tools_again(tmp_path):
    seen = {}

    def chat(req, n):
        seen["chat"] = _names_of(req)
        return say("ok")

    # the payment specialist on (its default now): the chat holds no checkout tools, so only the phone tools are checked against it
    app = _app(tmp_path, Brain(chat=chat), phone_worker=False, payment_specialist=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "hi"})).json()["id"])
    assert PHONE_TOOLS <= seen["chat"] and "checkout_review" not in seen["chat"]


def test_handing_the_phone_to_the_owner_stays_with_the_chat_because_a_job_has_no_reply_for_its_card():
    """Review 2026-11-16: only turn_engine sets `handover`; a job calling android_show waited ten minutes with no card for the owner."""
    from types import SimpleNamespace
    from assistant import tool_roles
    tools = [SimpleNamespace(name=n) for n in ("android_show", "android_tap", "android_send_screenshot", "web_search")]
    assert [t.name for t in tool_roles.phone_tools(tools)] == ["android_tap"]
    assert {t.name for t in tool_roles.finder_tools(tools)} == {"android_show", "android_send_screenshot", "web_search"}
