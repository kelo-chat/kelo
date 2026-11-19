"""Runs Kelo starts on its own never ask (card p-quiet-unattended C1-C3): a step that needs the owner's OK is skipped and the run is told;
no card, no push, no chat line, no follow-up. A run the owner started keeps its cards."""

import asyncio
import json

import httpx

from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from test_server import AUTH, TOKEN, Running


def ok_tool(ran):
    async def handler(args):
        ran.append(args)
        return "page text"
    return Tool("web_fetch", "Read a page", {"type": "object", "properties": {"url": {"type": "string"}}}, handler, requires_approval=True)


def model(requests):
    steps = iter([[ToolCall("c1", "web_fetch", json.dumps({"url": "https://example.org/x"})), Done("tool_calls")],
                  [TextDelta("done"), Done("stop")]])

    async def fn(provider, request, **kw):
        requests.append(request)
        for ev in next(steps):
            yield ev
    return fn


def app_for(tmp_path, fn, tools):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False,
                               home_dir=str(tmp_path / "home")), stream_fn=fn, tools=tools)


def begin(app, source, unattended=True):
    ctx = app.state.appctx
    return ctx.start_turn("p", "m", [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}], None, source=source, title="t",
                          unattended=unattended, tools_=[t for t in ctx.tools if t.name == "web_fetch"], max_steps=4, tool_scope=source)


async def quiet_after(c, turn):
    for _ in range(400):
        if turn.ended.is_set():
            break
        assert (await c.get("/api/approvals/pending", headers=AUTH)).json()["items"] == []
        await asyncio.sleep(0.02)
    await asyncio.wait_for(turn.ended.wait(), 10)


async def test_a_self_started_run_skips_a_step_that_needs_an_ok_and_says_nothing(tmp_path):
    ran, requests = [], []
    app = app_for(tmp_path, model(requests), [ok_tool(ran)])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/ai-consent", json={"accepted": True})).status_code == 200
        turn = begin(app, "situation")
        assert turn.self_started is True
        await quiet_after(c, turn)
        assert ran == []                                                              # the step was skipped, never asked
        last = requests[-1].messages[-1]["content"]
        assert last.startswith("web_fetch was not run: it needs the owner's OK")      # the run is told, not "the user declined"
        msgs = (await c.get("/api/messages")).json()
        msgs = msgs["items"] if isinstance(msgs, dict) else msgs
        assert [m for m in msgs if m["role"] == "assistant"] == []
        kinds = [n["type"] for n in (await c.get("/api/notifications")).json()["items"]]
        assert "approval_waiting" not in kinds and not any(k.startswith("followup") for k in kinds)
        assert (await c.get("/api/followups")).json()["items"] == []


async def test_the_same_step_in_an_owner_started_background_run_still_asks(tmp_path):
    ran, requests = [], []
    app = app_for(tmp_path, model(requests), [ok_tool(ran)])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/ai-consent", json={"accepted": True})).status_code == 200
        turn = begin(app, "background")
        assert turn.self_started is False
        card = None
        for _ in range(300):
            items = (await c.get("/api/approvals/pending", headers=AUTH)).json()["items"]
            if items:
                card = items[0]
                break
            await asyncio.sleep(0.02)
        assert card is not None and card["name"] == "web_fetch"
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False}, headers=AUTH)
        await asyncio.wait_for(turn.ended.wait(), 10)


async def test_an_attended_turn_is_never_self_started(tmp_path):
    app = app_for(tmp_path, model([]), [ok_tool([])])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/ai-consent", json={"accepted": True})).status_code == 200
        turn = begin(app, "recheck", unattended=False)
        assert turn.self_started is False
        for _ in range(300):
            items = (await c.get("/api/approvals/pending", headers=AUTH)).json()["items"]
            if items:
                await c.post(f"/api/approvals/{items[0]['approval_id']}", json={"approve": False}, headers=AUTH)
                break
            await asyncio.sleep(0.02)
        await asyncio.wait_for(turn.ended.wait(), 10)


async def test_an_owner_jobs_card_that_runs_out_tells_the_run_nobody_answered(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    ran, requests = [], []
    app = app_for(tmp_path, model(requests), [ok_tool(ran)])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.put("/api/ai-consent", json={"accepted": True})).status_code == 200
        turn = begin(app, "background")
        await asyncio.wait_for(turn.ended.wait(), 10)
        assert ran == []
        assert requests[-1].messages[-1]["content"].startswith("web_fetch was not run: nobody answered")   # not "the user declined"
