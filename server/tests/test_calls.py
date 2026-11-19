"""Call mode in the chat (calls.py, api/calls.py, tools/call_tools.py): a spoken message of a call is stored with via "call" and
the model gets the spoken-reply rules; the answered route starts Kelo's greeting from a hidden message; the call tool rings only
an app that can take a call; can_call reaches the running turn."""

import json

import httpx

from assistant import calls as C
from assistant.agent import Tool, ui_sink
from assistant.agent.toolbox import CORE_TOOLS
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.tool_labels import label
from assistant.tools.call_tools import call_tools
from assistant.workers import current_turn

from test_agent_profile import app_settings
from test_server import AUTH, Running, follow, scripted

CALL = "c0ffee12abcd"


def captured(sent: list, *rounds):
    """A scripted model that also records the messages of each request."""
    inner = scripted(*rounds)

    async def fn(provider, request, **kw):
        sent.append(request)
        async for ev in inner(provider, request, **kw):
            yield ev
    return fn


def last_user_text(request) -> str:
    msgs = request.messages if hasattr(request, "messages") else request["messages"] if isinstance(request, dict) else request
    return [m for m in msgs if m.get("role") == "user"][-1]["content"]


async def test_a_spoken_message_is_stored_as_a_call_and_the_model_gets_the_rules(tmp_path):
    sent: list = []
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=captured(sent, [TextDelta("네."), Done("stop")]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "hi", "call": CALL, "can_call": True})).json()["id"])
    store = app.state.appctx.store
    user = [m for m in store.messages() if m["role"] == "user"][-1]
    reply = [m for m in store.messages() if m["role"] == "assistant"][-1]
    assert user["content"] == "hi" and user["via"] == "call"
    assert reply["via"] == "call"
    assert user["call_id"] == CALL and reply["call_id"] == CALL
    model_text = last_user_text(sent[0])
    assert C.CALL_NOTE in model_text and model_text.startswith("hi")
    assert C.CALL_NOTE not in user["content"]


async def test_the_answered_route_starts_a_greeting_the_owner_does_not_see(tmp_path):
    sent: list = []
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=captured(sent, [TextDelta("안녕하세요."), Done("stop")]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post(f"/api/calls/{CALL}/answered", json={})).status_code == 428    # the AI company is not agreed to yet
        assert (await c.put("/api/ai-consent", json={"accepted": True})).status_code == 200
        r = await c.post(f"/api/calls/{CALL}/answered", json={"topic": "내일 회의 준비"})
        assert r.status_code == 200 and len(r.json()["id"]) == 32
        await follow(c, r.json()["id"])
        visible = (await c.get("/api/messages")).json()
        assert not [m for m in visible if (m.get("content") or "").startswith(C.CALL_MARK)]
    model_text = last_user_text(sent[0])
    assert model_text.startswith(C.CALL_MARK) and "내일 회의 준비" in model_text
    hidden = [m for m in app.state.appctx.store.messages() if m["content"].startswith(C.CALL_MARK)]
    assert hidden and hidden[0]["via"] == "call"


async def test_a_bad_call_id_is_refused(tmp_path):
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/calls/short/answered", json={})).status_code == 422
        assert (await c.post("/api/calls/has space here/answered", json={})).status_code in (404, 422)


def test_the_hidden_mark_is_left_out_of_search():
    from assistant import chat_search
    assert C.CALL_MARK in chat_search.NOT_THE_USERS_WORDS


class Sink:
    def __init__(self):
        self.items = []

    async def __call__(self, item):
        self.items.append(item)


def find_call_tool(current):
    return call_tools(current)[0]


async def test_the_tool_refuses_an_unattended_turn_and_an_app_that_cannot_take_a_call():
    sink = Sink()
    token = ui_sink.set(sink)
    try:
        tool = find_call_tool(lambda: {"turn_id": "t1", "unattended": True, "can_call": True})
        assert (await tool.handler({})).startswith("Error:")
        tool = find_call_tool(lambda: {"turn_id": "t1", "can_call": False})
        assert (await tool.handler({})).startswith("Error:")
        tool = find_call_tool(lambda: None)
        assert (await tool.handler({})).startswith("Error:")
        assert sink.items == []
    finally:
        ui_sink.reset(token)


async def test_the_tool_rings_once_with_a_32_hex_call_id_and_the_topic_cut_at_80():
    sink = Sink()
    token = ui_sink.set(sink)
    try:
        tool = find_call_tool(lambda: {"turn_id": "t1", "can_call": True})
        out = await tool.handler({"topic": "x" * 200})
        assert "ringing" in out
    finally:
        ui_sink.reset(token)
    assert len(sink.items) == 1
    item = sink.items[0]
    assert item["type"] == "call_offer" and len(item["call_id"]) == 32
    int(item["call_id"], 16)
    assert item["topic"] == "x" * 80


def test_the_tool_is_a_core_tool_and_has_a_label_in_every_locale():
    assert "start_voice_call" in CORE_TOOLS
    assert label("start_voice_call") == "Calling you in the app"
    import glob
    import json as _json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "assistant" / "locales"
    files = [f for f in glob.glob(str(root / "*.json"))]
    assert len(files) == 19
    for f in files:
        assert "Calling you in the app" in _json.loads(Path(f).read_text(encoding="utf-8")), f


async def test_can_call_reaches_the_running_turn(tmp_path):
    seen: list = []

    async def probe(a):
        seen.append(dict(current_turn.get() or {}))
        return "ok"

    probe_tool = Tool("probe_turn", "records the turn", {"type": "object"}, probe)
    app = create_app(app_settings(tmp_path), tools=[probe_tool],
                     stream_fn=scripted([ToolCall("c1", "probe_turn", "{}"), Done("tool_calls")], [TextDelta("ok"), Done("stop")]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "hi", "can_call": True})).json()["id"])
    assert seen and seen[0].get("can_call") is True


async def test_call_messages_carry_their_call_id_and_typed_ones_none(tmp_path):
    # owner 2026-11-20: the chat must show one card per call, so each message of a call says which call it belongs to
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=scripted([TextDelta("네."), Done("stop")], [TextDelta("응."), Done("stop")]))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "여보세요", "call": CALL, "can_call": True})).json()["id"])
        await follow(c, (await c.post("/api/turns", json={"text": "typed"})).json()["id"])
        rows = (await c.get("/api/messages")).json()
        around = (await c.get(f"/api/messages/around?id={rows[0]['id']}")).json()
    assert [r.get("call") for r in rows] == [CALL, CALL, None, None]
    assert all("call_id" not in r for r in rows)
    assert [r.get("call") for r in around] == [CALL, CALL, None, None]
    store = app.state.appctx.store
    assert [m["call_id"] for m in store.messages()] == [CALL, CALL, "", ""]


async def test_amend_replaces_the_calls_unanswered_words(tmp_path):
    # owner 2026-11-20 "나 목소리 너무 끊겨": the words were cut; the app takes the fragment back and sends it again with the rest
    sent: list = []
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=captured(sent, [TextDelta("네."), Done("stop")]))
    store = app.state.appctx.store
    store.add_message("user", "나 목소리", via="call", author="owner", call_id=CALL)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/turns", json={"text": "나 목소리 너무 끊겨", "call": CALL, "amend": True, "can_call": True})
        await follow(c, r.json()["id"])
    users = [m["content"] for m in store.messages() if m["role"] == "user"]
    assert users == ["나 목소리 너무 끊겨"]
    assert last_user_text(sent[0]).startswith("나 목소리 너무 끊겨")
    msgs = sent[0].messages if hasattr(sent[0], "messages") else sent[0]["messages"]
    assert not [m for m in msgs if m.get("role") == "user" and m.get("content") == "나 목소리"]   # the fragment is gone


def test_amend_never_drops_answered_words_another_calls_typed_or_hidden_rows(tmp_path):
    from assistant.store import Store
    s = Store(tmp_path / "a.db")
    s.add_message("user", "hi", via="call", call_id=CALL); s.add_message("assistant", "yes", via="call", call_id=CALL)
    assert s.drop_unanswered_call_words(CALL) is False                      # answered: the newest row is Kelo's
    s.add_message("user", "other", via="call", call_id="ffff0000ffff")
    assert s.drop_unanswered_call_words(CALL) is False                      # another call's words
    s.add_message("user", "typed")
    assert s.drop_unanswered_call_words(CALL) is False                      # typed words
    s.add_message("user", C.answered_text(""), via="call", call_id=CALL)
    assert s.drop_unanswered_call_words(CALL) is False                      # the hidden start message
    s.add_message("user", "mine", via="call", call_id=CALL)
    assert s.drop_unanswered_call_words(CALL) is True
    assert [m["content"] for m in s.messages()][-1] == C.answered_text("")


def test_call_messages_stored_before_call_ids_get_one_id_per_call_once(tmp_path):
    from assistant.store import CALL_IDS_META, Store, backfill_call_ids
    s = Store(tmp_path / "b.db")
    ids = [s.add_message("user", C.answered_text(""), via="call"), s.add_message("assistant", "여보세요", via="call"),
           s.add_message("user", "응", via="call"), s.add_message("assistant", "네", via="call"),
           s.add_message("user", "typed"), s.add_message("assistant", "ok"),
           s.add_message("user", C.answered_text(""), via="call"), s.add_message("assistant", "안녕", via="call")]
    s._run("DELETE FROM meta WHERE key = ?", (CALL_IDS_META,))
    assert backfill_call_ids(s._db) is True
    got = [m["call_id"] for m in s._all("SELECT call_id FROM messages ORDER BY id")]
    first, second = f"legacy-{ids[0]}", f"legacy-{ids[6]}"
    assert got == [first, first, first, first, "", "", second, second]
    assert backfill_call_ids(s._db) is False                                  # once per database


def test_the_call_note_answers_what_was_meant_not_how_it_was_written():
    # owner 2026-11-20: in a call Kelo corrected written form and blamed the owner for a recognition error
    note = C.CALL_NOTE
    assert "speech recognition" in note and "heard, not read" in note
    assert "respond to what they meant to say, never to how it was written down" in note
    assert "misheard" not in note
    assert len(note.encode("utf-8")) <= 700
