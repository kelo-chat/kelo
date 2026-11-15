"""Every reply's events are written down as they happen (turn_log.py, backlog 73): read back after the memory let go of it, after a
restart (ends "interrupted"), over the WebSocket GET /api/turns/{id}/ws numbered from `after`, and gone when the chat is cleared."""

import asyncio
import json

import httpx
import pytest
import websockets

from assistant.providers import Done, TextDelta, Usage
from assistant.server import create_app
from assistant.store import Store
from assistant.turn_log import INTERRUPTED, KEEP_HOURS, TurnLog

from test_server import AUTH, Running, follow, scripted, settings


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def ws_url(srv, path):
    return srv.url.replace("http://", "ws://") + path


async def read_socket(url, headers=AUTH):
    """Every frame the server sends until it closes: (the frames, the close code)."""
    out = []
    async with websockets.connect(url, additional_headers=headers) as ws:
        try:
            async for msg in ws:
                out.append(json.loads(msg))
        except websockets.exceptions.ConnectionClosed as e:
            return out, e.rcvd.code if e.rcvd else None
        return out, ws.close_code


# ---- the log itself

def test_a_reply_is_written_in_order_and_can_be_read_from_any_place():
    log = TurnLog(Store(), clock=FakeClock())
    log.started("t1")
    for i, ev in enumerate([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}, {"type": "done"}]):
        log.add("t1", i, ev)
    log.finished("t1", "done")
    assert log.events("t1", 0) == [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}, {"type": "done"}]
    assert log.events("t1", 2) == [{"type": "done"}]
    assert log.status("t1") == "done"
    assert log.events("nope") is None


def test_a_reply_the_server_stopped_under_ends_with_interrupted():
    log = TurnLog(Store(), clock=FakeClock())
    log.started("running")
    log.add("running", 0, {"type": "text", "text": "half"})
    log.started("finished")
    log.finished("finished", "done")
    assert log.interrupt_running() == ["running"]
    assert log.events("running") == [{"type": "text", "text": "half"}, INTERRUPTED]
    assert log.status("running") == "interrupted"
    assert log.status("finished") == "done"
    assert log.interrupt_running() == []


def test_a_reply_that_ended_long_ago_is_forgotten_with_its_events():
    clock = FakeClock()
    log = TurnLog(Store(), clock=clock)
    log.started("old")
    log.add("old", 0, {"type": "text", "text": "x"})
    log.finished("old", "done")
    clock.t += KEEP_HOURS * 3600 + 3600
    log.started("new")
    log.finished("new", "done")
    log.prune()
    assert log.events("old") is None
    assert log.events("new") == []


# ---- through the app

async def test_a_finished_reply_is_still_read_when_the_memory_has_let_it_go():
    app = create_app(settings(), tools=[], stream_fn=scripted([TextDelta("hel"), TextDelta("lo"), Usage(4, 2), Done("stop")]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        assert [e["type"] for e in await follow(c, tid)] == ["text", "text", "done"]
        before = (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 0})).json()
        assert before["done"] is True and [e["type"] for e in before["events"]][-1] == "done"
        app.state.appctx.registry.turns.pop(tid)                                    # the server forgot it (memory only)
        after = (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 0})).json()
        assert after == before                                                       # the same events, and done
        tail = (await c.get(f"/api/turns/{tid}", params={"after": 2, "wait": 0})).json()
        assert tail["events"] == before["events"][2:] and tail["next"] == len(before["events"])
        assert (await c.delete(f"/api/turns/{tid}")).status_code == 404              # stopping a reply that is only in the log: gone
        assert (await c.get("/api/turns/nope", params={"wait": 0})).status_code == 404


async def test_clearing_the_chat_forgets_the_reply_log_too():
    app = create_app(settings(), tools=[], stream_fn=scripted([TextDelta("hi"), Done("stop")]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        await follow(c, tid)
        app.state.appctx.registry.turns.pop(tid)
        assert (await c.delete("/api/messages")).json()["ok"] is True
        assert (await c.get(f"/api/turns/{tid}", params={"wait": 0})).status_code == 404


async def test_a_reply_cut_off_by_a_restart_is_read_back_as_interrupted(tmp_path):
    db = str(tmp_path / "assistant.db")
    gate = asyncio.Event()

    async def blocked(provider, request, **kw):
        yield TextDelta("half")
        await gate.wait()
        yield Done("stop")

    app = create_app(settings(db_path=db), tools=[], stream_fn=blocked)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 2})            # the first words arrived; the model is still going
    gate.set()   # the cut-off reply's task may finish; nothing it writes is kept any more

    app2 = create_app(settings(db_path=db), tools=[], stream_fn=scripted())
    async with Running(app2) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 0})).json()
        assert r["done"] is True and r["events"][-1] == INTERRUPTED
        assert [e["type"] for e in r["events"]].count("text") == 1                  # what was said before the restart, once
        assert "interrupted" in [e["type"] for e in r["events"]]


# ---- WebSocket

async def test_a_socket_gives_the_numbered_events_of_a_finished_reply_then_done():
    app = create_app(settings(), tools=[], stream_fn=scripted([TextDelta("a"), TextDelta("b"), TextDelta("c"), Done("stop")]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        await follow(c, tid)
        raw = (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 0})).json()["events"]
        frames, code = await read_socket(ws_url(srv, f"/api/turns/{tid}/ws"))
        assert code == 1000
        assert frames[:-1] == [{"seq": i, "event": ev} for i, ev in enumerate(raw)]
        assert frames[-1] == {"done": True, "next": len(raw)}
        frames, _ = await read_socket(ws_url(srv, f"/api/turns/{tid}/ws?after=2"))
        assert frames[0] == {"seq": 2, "event": raw[2]} and frames[-1] == {"done": True, "next": len(raw)}


async def test_a_socket_without_the_token_is_closed_4401():
    app = create_app(settings(), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        frames, code = await read_socket(ws_url(srv, f"/api/turns/{tid}/ws"), headers=None)
        assert frames == [] and code == 4401


async def test_an_unknown_reply_is_gone_and_closed_4404():
    app = create_app(settings(), tools=[], stream_fn=scripted())
    async with Running(app) as srv:
        frames, code = await read_socket(ws_url(srv, "/api/turns/nope/ws"))
        assert frames == [{"type": "gone"}] and code == 4404


async def test_a_socket_follows_a_running_reply_as_the_model_goes():
    gate = asyncio.Event()

    async def model(provider, request, **kw):
        yield TextDelta("one")
        await gate.wait()
        yield TextDelta("two")
        yield Done("stop")

    app = create_app(settings(), tools=[], stream_fn=model)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "hi"})).json()["id"]
        got = []
        async with websockets.connect(ws_url(srv, f"/api/turns/{tid}/ws"), additional_headers=AUTH) as ws:
            while not any(f.get("event", {}).get("type") == "text" for f in got):   # the first words come before the model goes on
                got.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))
            gate.set()
            while "done" not in got[-1]:
                got.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))
        numbered = [f for f in got if "seq" in f]
        assert [f["seq"] for f in numbered] == list(range(len(numbered)))
        assert [f["event"]["text"] for f in numbered if f["event"]["type"] == "text"] == ["one", "two"]
        assert got[-1] == {"done": True, "next": len(numbered)}
