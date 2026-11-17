"""The first conversation (first_chat.py): the extra block in "This Turn" and the two tools exist only while it is open; a server that is
not new is marked done at start-up; the 30th owner message closes it; finish_first_chat closes it; set_owner_settings saves what was learned."""

from __future__ import annotations

import httpx

from assistant.first_chat import AUTO_FINISH_MESSAGES, BLOCK, FirstChat
from assistant.home import Home
from assistant.prompt import PromptBuilder, turn_lines
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running, read_events
from test_prompt import CONTENT, NOW, turn_text


def test_the_block_is_in_the_turn_lines_only_when_given_and_never_in_the_stable_part():
    assert BLOCK in turn_lines(tz_name="UTC", now=NOW, first_chat=BLOCK)
    assert BLOCK not in turn_lines(tz_name="UTC", now=NOW)
    stable, turn = PromptBuilder("You are {agent_name}.", None, None).build_parts(now=NOW, first_chat=BLOCK)
    assert "first conversation" not in stable and BLOCK in turn
    assert "finish_first_chat" in BLOCK and "set_owner_settings" in BLOCK


def test_a_new_server_is_open_and_the_block_follows_the_flag():
    store = Store()
    first = FirstChat(store)
    assert first.migrate() is False and not first.done() and first.block() == BLOCK
    assert first.hides("finish_first_chat") is False and first.version == 1
    first.finish()
    assert first.done() and first.block() == "" and first.hides("finish_first_chat") and first.hides("set_owner_settings")
    assert not first.hides("read_file") and first.version == 0


def test_migration_marks_an_owner_with_notes_or_a_long_chat_as_done(tmp_path):
    home = Home(tmp_path / "home")
    home.migrate()
    fresh = FirstChat(Store(), home)
    assert fresh.migrate() is False                                           # user.md is still its template

    home.write("user.md", "# user.md\n\nMinji, lives in Seoul, likes green tea.\n", by="system")
    store = Store()
    assert FirstChat(store, home).migrate() is True and store.meta("first_chat_done") == "1"

    long_chat = Store()
    for i in range(21):
        long_chat.add_message("user" if i % 2 == 0 else "assistant", f"m{i}")
    assert FirstChat(long_chat).migrate() is True
    short_chat = Store()
    for i in range(20):
        short_chat.add_message("user", f"m{i}")
    assert FirstChat(short_chat).migrate() is False                           # exactly 20: still new


def test_migration_decides_once_and_never_reopens_or_closes_later():
    store = Store()
    first = FirstChat(store)
    assert first.migrate() is False and store.meta("first_chat_done") == "0"
    for i in range(30):
        store.add_message("assistant", f"m{i}")
    assert first.migrate() is False                                           # already decided: a later restart keeps it
    store.set_meta("first_chat_done", "1")
    assert first.migrate() is True


def test_the_owners_30th_message_closes_it():
    store = Store()
    first = FirstChat(store)
    first.migrate()
    for _ in range(AUTO_FINISH_MESSAGES - 1):
        first.note_owner_message()
    assert not first.done() and first.block() == BLOCK
    first.note_owner_message()
    assert first.done() and first.block() == ""
    first.note_owner_message()                                                # after it is over nothing counts or reopens
    assert first.done()


def talk_app(tmp_path, seen, tools_seen, steps=None):
    replies = iter(steps or [])

    async def fn(provider, request, **k):
        seen.append(request.messages)
        tools_seen.append([t["function"]["name"] for t in request.tools])
        for ev in next(replies, [TextDelta("ok"), Done("stop")]):
            yield ev

    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home"), content_dir=str(CONTENT))
    return create_app(s, stream_fn=fn)


async def test_the_chat_carries_the_block_and_the_tools_only_while_it_is_open(tmp_path):
    seen, tools = [], []
    app = talk_app(tmp_path, seen, tools)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/profile")).json()["first_chat_done"] is False
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            await read_events(r)
        assert BLOCK in turn_text(seen[0]) and BLOCK not in seen[0][0]["content"]
        assert {"finish_first_chat", "set_owner_settings"} <= set(tools[0])
        app.state.first_chat.finish()
        assert (await c.get("/api/profile")).json()["first_chat_done"] is True
        async with c.stream("POST", "/api/talk", json={"text": "again"}) as r:
            await read_events(r)
        assert BLOCK not in turn_text(seen[1])
        assert not {"finish_first_chat", "set_owner_settings"} & set(tools[1])
        assert seen[0][0]["content"] == seen[1][0]["content"]                 # the cached system part never moved


async def test_finish_first_chat_sets_the_flag_and_the_tools_go_away(tmp_path):
    seen, tools = [], []
    steps = [[ToolCall("c1", "finish_first_chat", "{}"), Done("tool_calls")], [TextDelta("Nice to meet you."), Done("stop")]]
    app = talk_app(tmp_path, seen, tools, steps)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "hi"}) as r:
            events = await read_events(r)
        out = next(e for e in events if e["type"] == "tool_result" and e.get("name") == "finish_first_chat")["output"]
        assert "over" in out and not any(e["type"] == "approval_request" for e in events)
        assert (await c.get("/api/profile")).json()["first_chat_done"] is True
        assert "finish_first_chat" in tools[0] and "finish_first_chat" not in tools[-1]   # gone within the same turn's next step
        async with c.stream("POST", "/api/talk", json={"text": "next"}) as r:
            await read_events(r)
        assert BLOCK not in turn_text(seen[-1]) and "finish_first_chat" not in tools[-1]


async def test_the_30th_talk_message_closes_it_through_the_api(tmp_path):
    seen, tools = [], []
    async with Running(talk_app(tmp_path, seen, tools)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=60) as c:
        for i in range(AUTO_FINISH_MESSAGES):
            async with c.stream("POST", "/api/talk", json={"text": f"m{i}"}) as r:
                await read_events(r)
        assert (await c.get("/api/profile")).json()["first_chat_done"] is True
        assert BLOCK in turn_text(seen[0]) and BLOCK in turn_text(seen[AUTO_FINISH_MESSAGES - 2])
        assert "finish_first_chat" in tools[AUTO_FINISH_MESSAGES - 2]


async def test_set_owner_settings_saves_the_profile_and_the_proactive_settings(tmp_path):
    seen, tools = [], []
    steps = [[ToolCall("c1", "set_owner_settings", '{"user_name": "Minji", "timezone": "Asia/Seoul", "wake_start": "08:30", '
                                                   '"wake_end": "23:00", "proactive": true}'), Done("tool_calls")],
             [ToolCall("c2", "set_owner_settings", '{"timezone": "Mars/Base"}'), Done("tool_calls")],
             [ToolCall("c3", "set_owner_settings", '{}'), Done("tool_calls")],
             [TextDelta("done"), Done("stop")]]
    app = talk_app(tmp_path, seen, tools, steps)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "I am Minji"}) as r:
            events = await read_events(r)
        outs = [e["output"] for e in events if e["type"] == "tool_result" and e.get("name") == "set_owner_settings"]
        assert outs[0].startswith("Saved") and outs[1].startswith("Error") and outs[2].startswith("Nothing")
        prof = (await c.get("/api/profile")).json()
        assert prof["user_name"] == "Minji" and prof["timezone"] == "Asia/Seoul"
        cfg = (await c.get("/api/proactive")).json()
        assert cfg["enabled"] is True and cfg["wake_start"] == "08:30" and cfg["wake_end"] == "23:00"
    from assistant.workers import MAIN_ONLY
    assert {"set_owner_settings", "finish_first_chat"} <= set(MAIN_ONLY)       # a background job never runs the first conversation
