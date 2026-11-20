"""Calls and the promise rule (scheduled_runs.py, turn_engine.promise_round) and what Kelo said it will keep doing in a call
(call_keep.py). Fake models only."""

import asyncio

import httpx

from assistant import scheduled_runs as sr
from assistant.call_keep import CALLS_MAX, CallBook, keep_note
from assistant.scheduled_runs import PROMISE_SYSTEM
from assistant.server import create_app

from test_server import AUTH, Running, follow
from test_watching import model, say, settings

CALL = "c0ffee12abcd"
OTHER = "beef0000beef"
ROUND_HEAD = "[Not from the user: your reply says"


def judging(req) -> bool:
    return req.messages[0].get("content") == PROMISE_SYSTEM


def in_round(req) -> bool:
    return any(str(m.get("content") or "").startswith(ROUND_HEAD) for m in req.messages)


def last_user(req) -> str:
    return [m for m in req.messages if m.get("role") == "user"][-1]["content"]


async def test_in_a_call_an_unbacked_promise_is_set_up_and_heard_in_the_call(tmp_path, monkeypatch):
    # owner 2026-11-20: a task given by voice is still checked; what is set up is said in the call, not in a separate chat bubble
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)

    def script(req, n):
        if judging(req):
            return say('{"promises_later": true, "offer": false, "in_conversation": false, "quote": "내일 아침에 알려드릴게요"}')
        if in_round(req):
            return say("내일 아침 8시 알림을 만들었어요.")
        return say("내일 아침에 알려드릴게요.")
    fn = model(script)
    app = create_app(settings(tmp_path), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "내일 아침에 알려줘", "call": CALL, "can_call": True})).json()["id"])
        await asyncio.sleep(0.3)
    said = [i for i, e in enumerate(events) if e["type"] == "text" and "알림을 만들었어요" in e["text"]]
    assert said and said[-1] < [e["type"] for e in events].index("done")
    replies = [m for m in app.state.appctx.store.messages() if m["role"] == "assistant"]
    assert len(replies) == 1 and replies[0]["via"] == "call" and "알림을 만들었어요" in replies[0]["content"]
    assert last_user([r for r in fn.calls if in_round(r)][0]).startswith("내일 아침에 알려줘")   # the round keeps the call's rules


async def test_in_a_call_saying_how_kelo_will_respond_gets_no_round(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)

    def script(req, n):
        if judging(req):
            return say('{"promises_later": true, "offer": false, "in_conversation": true, "quote": "지금부터 한 단어로만 답할게요"}')
        return say("네, 지금부터 한 단어로만 답할게요.")
    fn = model(script)
    app = create_app(settings(tmp_path), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "앞으로 한 단어로만 대답해", "call": CALL, "can_call": True})).json()["id"])
        await asyncio.sleep(0.3)
    rows = app.state.appctx.store.messages()
    assert [m["via"] for m in rows] == ["call", "call"] and not any(in_round(r) for r in fn.calls)


def test_the_book_keeps_the_newest_few_per_call_and_forgets_old_calls():
    b = CallBook()
    for q in ("a1 a1 a1", "b2 b2 b2", "c3 c3 c3", "d4 d4 d4"):
        b.keep("call-1", q)
    assert b.kept("call-1") == ["b2 b2 b2", "c3 c3 c3", "d4 d4 d4"]
    b.keep("call-1", "B2 b2 b2!")                       # the same words again move to the end, never twice
    assert b.kept("call-1") == ["c3 c3 c3", "d4 d4 d4", "B2 b2 b2!"]
    for i in range(CALLS_MAX):
        b.keep(f"other-{i}", "x x x x")
    assert b.kept("call-1") == []                       # the oldest call is forgotten
    b.keep("", "x x x x")
    b.keep("c", "   ")
    assert b.kept("") == [] and b.kept("c") == []


def test_the_call_note_is_general_and_short():
    note = keep_note(["Only one word from now on."])
    assert note.startswith("[Not from the user:") and "\"Only one word from now on.\"" in note and note.endswith("]")
    assert keep_note([]) == ""
    for word in ("interpret", "translat", "language"):
        assert word not in note.lower()
    assert len(keep_note(["x" * 160] * 3).encode("utf-8")) <= 900


async def test_in_a_call_how_kelo_said_it_will_respond_is_carried_on_the_calls_next_turns(tmp_path, monkeypatch):
    # owner 2026-11-20: a way of responding set in a call drifted away one turn later
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    words = "지금부터 한 단어로만 답할게요"

    def script(req, n):
        if judging(req):
            if words in req.messages[1]["content"]:
                return say('{"promises_later": false, "offer": false, "in_conversation": true, "quote": "' + words + '"}')
            return say('{"promises_later": false, "offer": false, "in_conversation": false, "quote": ""}')
        if last_user(req).startswith("앞으로"):
            return say(f"네, {words}.")
        return say("네.")
    fn = model(script)
    app = create_app(settings(tmp_path), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for text, extra in (("앞으로 한 단어로만 대답해", {"call": CALL}), ("오늘 어때", {"call": CALL}), ("오늘 어때", {}),
                            ("안녕", {"call": OTHER}), ("그리고", {"call": CALL})):
            await follow(c, (await c.post("/api/turns", json={"text": text, "can_call": True, **extra})).json()["id"])
    asked = [last_user(r) for r in fn.calls if not judging(r)]
    note = keep_note([words])
    assert note not in asked[0]                 # the turn that set it answers it itself
    assert note in asked[1] and note in asked[4]   # every later turn of that call carries it
    assert "earlier in this call" not in asked[2] and "earlier in this call" not in asked[3]   # a typed turn, another call: never
