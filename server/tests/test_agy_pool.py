"""agy sessions kept between model calls (providers/agy_pool.py), checked against a fake `agy` that speaks the same stream-json."""

import json
import os
import sys
import textwrap
import time

import pytest

from assistant.providers import ChatRequest, TextDelta, ToolCall, agy_pool, cli_agents
from assistant.providers.types import TURN_NOTE

FAKE_AGY = textwrap.dedent('''\
    import json, os, sys, time
    LOG = os.environ["FAKE_AGY_LOG"]
    def note(**kw):
        with open(LOG, "a") as f:
            f.write(json.dumps({"pid": os.getpid(), **kw}) + "\\n")
    worker = "--input-format" in sys.argv
    time.sleep(float(os.environ.get("FAKE_AGY_START", "0.05")))
    note(event="spawn", worker=worker)
    state = {"rounds": 0, "seen": 0}
    def emit(o):
        sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    def answer(payload):
        note(event="msg", payload=payload)
        i = payload.rfind("[[tools:")
        if i >= 0:
            state["rounds"] = int(payload[i + 8:payload.index("]]", i)]); state["seen"] = payload[i:].count("Tool Result (")
        else:
            state["seen"] += payload.count("Tool Result (")
        time.sleep(float(os.environ.get("FAKE_AGY_TURN", "0.01")))
        conv = "c%d" % os.getpid()
        emit({"event": "init", "init": {"conversation_id": conv}})
        if "[[native]]" in payload and state["seen"] == 0:   # agy tries a tool of its own: the plugin stops this process
            emit({"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "list_dir", "step_index": 1,
                  "tool_info": {"name": "list_dir", "parameters": {"DirectoryPath": "/tmp/x"}}}})
            time.sleep(30)
            return
        if "[[slow]]" in payload:
            for k in range(50):
                emit({"event": "step_update", "step_update": {"text_delta": "w%d " % k}}); time.sleep(0.1)
        if state["seen"] < state["rounds"]:
            call = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": json.dumps({"n": state["seen"] + 1})}}
            text = "<tool_call>%s</tool_call>" % json.dumps(call)
            if "[[parallel]]" in payload:
                call2 = dict(call, function={"name": "lookup", "arguments": json.dumps({"n": 99})})
                text += "<tool_call>%s</tool_call>" % json.dumps(call2)
        else:
            text = "done after %d results (%d)" % (state["seen"], os.getpid())
        emit({"event": "step_update", "step_update": {"text_delta": text}})
        emit({"event": "result", "result": {"conversation_id": conv, "status": "SUCCESS", "usage": {"input_tokens": 10, "output_tokens": 2}}})
    if worker:
        for line in sys.stdin:
            if line.strip():
                answer(json.loads(line)["message"]["content"])
    else:
        answer(sys.stdin.read())
''')

TOOLS = [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
         for n in ("lookup", "list_files")]


@pytest.fixture
def agy(monkeypatch, tmp_path):
    """cli_agents' real agy path (pool, plugin, stream) running the fake binary; yields a helper that reads the fake's log."""
    exe = tmp_path / "agy"
    exe.write_text(f"#!{sys.executable}\n{FAKE_AGY}")
    exe.chmod(0o755)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "antigravity-oauth-token").write_text("x" * 32)
    log = tmp_path / "agy.log"
    monkeypatch.setenv("AGY_CLI_PATH", str(exe))
    monkeypatch.setenv("ANTIGRAVITY_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    monkeypatch.setattr(cli_agents, "_clients", {})

    class Helper:
        def events(self, kind=None):
            if not log.exists():
                return []
            rows = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
            return [r for r in rows if kind is None or r["event"] == kind]

        def wait_spawns(self, n, deadline=10.0):
            end = time.monotonic() + deadline
            while time.monotonic() < end:
                if len(self.events("spawn")) >= n:
                    return True
                time.sleep(0.02)
            return False

        @property
        def pool(self):
            return cli_agents._clients[cli_agents.AGY]
    h = Helper()
    yield h
    pool = cli_agents._clients.get(cli_agents.AGY)
    if pool is not None:
        pool.close()


async def run(messages, tools=TOOLS, model="gemini-test-low"):
    events = [e async for e in cli_agents.stream(cli_agents.AGY, ChatRequest(model=model, messages=messages, tools=tools))]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    calls = [e for e in events if isinstance(e, ToolCall)]
    return text, calls


def tool_round(messages, calls, n):
    out = [*messages, {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"{c.id}-{n}", "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls]}]
    return [*out, *({"role": "tool", "tool_call_id": f"{c.id}-{n}", "content": f"result {n}"} for c in calls)]


SYSTEM = {"role": "system", "content": "You are Kelo. Be brief."}


def turn(history, note, user):
    return [SYSTEM, *history, {"role": "user", "content": TURN_NOTE + note}, {"role": "user", "content": user}]


async def test_three_tool_rounds_and_the_next_turn_use_one_agy_process(agy):
    msgs = turn([], "clock 10:00", "look it up [[tools:3]]")
    text, calls = await run(msgs)
    for n in (1, 2, 3):
        assert calls and not text
        msgs = tool_round(msgs, calls, n)
        text, calls = await run(msgs)
    assert text.startswith("done after 3 results") and not calls
    # the next turn, as the server builds it: stored history (user + final text), a new turn note, the new message
    nxt = turn([{"role": "user", "content": "look it up [[tools:3]]"}, {"role": "assistant", "content": text}], "clock 10:05",
               "thanks, one more [[tools:1]]")
    text2, calls2 = await run(nxt)
    assert calls2 and not text2
    text3, _ = await run(tool_round(nxt, calls2, 1))
    assert text3.startswith("done after 1 results")
    spawns, msgs_seen = agy.events("spawn"), agy.events("msg")
    assert len(spawns) == 1 and len({m["pid"] for m in msgs_seen}) == 1   # 6 model calls, one process
    first, *rest = (m["payload"] for m in msgs_seen)
    assert "look it up" in first and "lookup" in first                        # the session's first message: tools + conversation
    assert all("You are Kelo" not in p and '"description"' not in p for p in rest)  # then only what is new (no tool list again)
    assert "clock 10:05" in rest[3] and "thanks, one more" in rest[3] and "look it up" not in rest[3]


async def test_a_call_with_another_system_prompt_does_not_stop_the_chat_session(agy):
    msgs = turn([], "clock", "go [[tools:2]]")
    _, calls = await run(msgs)
    msgs = tool_round(msgs, calls, 1)
    other = [{"role": "system", "content": "Write three short follow-up suggestions."}, {"role": "user", "content": "the chat so far"}]
    side, _ = await run(other, tools=[])            # a follow-up job between two rounds of the chat
    assert side.startswith("done")
    _, calls = await run(msgs)
    text, _ = await run(tool_round(msgs, calls, 2))
    assert text.startswith("done after 2 results")
    assert len(agy.events("spawn")) == 2 and len(agy.pool.clients) == 2      # one process per prompt, the chat's never restarted


async def test_two_calls_at_once_with_the_same_prompt_get_two_sessions(agy, monkeypatch):
    import asyncio
    monkeypatch.setenv("FAKE_AGY_TURN", "0.3")
    a = turn([], "n", "first [[tools:0]]")
    b = turn([], "n", "second [[tools:0]]")
    (ta, _), (tb, _) = await asyncio.gather(run(a), run(b))
    assert ta.startswith("done") and tb.startswith("done") and len(agy.pool.clients) == 2
    assert all(m["payload"].count("first") + m["payload"].count("second") == 1 for m in agy.events("msg"))


async def test_agy_trying_its_own_tool_is_answered_by_ours_and_the_next_round_finds_a_started_process(agy):
    msgs = turn([], "clock", "what is in my files [[native]] [[tools:0]]")
    text, calls = await run(msgs)
    assert [c.name for c in calls] == ["list_files"] and json.loads(calls[0].arguments) == {"path": "/tmp/x"}
    assert agy.wait_spawns(2)                    # the replacement started right away, before the next call
    pool_client = agy.pool.clients[0]
    text, calls = await run(tool_round(msgs, calls, 1))
    assert text.startswith("done after 1 results") and not calls
    assert len(agy.events("spawn")) == 2 and pool_client.spawns == 2    # no third process: the next call used the warm one
    assert "You are Kelo" not in agy.events("msg")[-1]["payload"] and "what is in my files" in agy.events("msg")[-1]["payload"]


async def test_no_warm_replacement_when_turned_off(agy, monkeypatch):
    monkeypatch.setenv("ASSISTANT_AGY_WARM", "0")
    msgs = turn([], "clock", "files [[native]] [[tools:0]]")
    await run(msgs)
    time.sleep(0.3)
    assert len(agy.events("spawn")) == 1


async def test_a_session_that_has_grown_too_long_starts_over_from_the_servers_conversation(agy, monkeypatch):
    msgs = turn([], "clock", "go [[tools:1]]")
    _, calls = await run(msgs)
    text, _ = await run(tool_round(msgs, calls, 1))
    monkeypatch.setattr(agy_pool, "SESSION_MAX_CHARS", 50)      # what the session has seen is now "too long"
    nxt = turn([{"role": "user", "content": "go [[tools:1]]"}, {"role": "assistant", "content": text}], "clock 2", "again [[tools:0]]")
    text2, _ = await run(nxt)
    assert text2.startswith("done") and len(agy.events("spawn")) == 2
    last = agy.events("msg")[-1]
    assert "go [[tools:1]]" in last["payload"] and last["pid"] != agy.events("msg")[0]["pid"]   # a new process, the whole conversation


async def test_tools_loaded_during_a_session_are_told_to_it(agy):
    msgs = turn([], "clock", "go [[tools:1]]")
    _, calls = await run(msgs, tools=TOOLS[:1])
    more = [*TOOLS, {"type": "function", "function": {"name": "email_list", "description": "mail", "parameters": {"type": "object"}}}]
    text, _ = await run(tool_round(msgs, calls, 1), tools=more)
    assert text.startswith("done")
    last = agy.events("msg")[-1]["payload"]
    assert agy_pool.TOOLS_NOTE.strip()[:40] in last and "email_list" in last and len(agy.events("spawn")) == 1
    text, _ = await run(tool_round(tool_round(msgs, calls, 1), calls, 2), tools=more)   # unchanged list: not repeated
    assert agy_pool.TOOLS_NOTE.strip()[:40] not in agy.events("msg")[-1]["payload"]


async def test_two_calls_in_one_reply_stay_two(agy):
    msgs = turn([], "clock", "both [[tools:1]] [[parallel]]")
    _, calls = await run(msgs)
    assert [json.loads(c.arguments)["n"] for c in calls] == [1, 99]


async def test_a_cancelled_reply_stops_agy_and_the_next_call_starts_the_session_over(agy):
    import asyncio
    msgs = turn([], "clock", "talk [[slow]]")
    got = asyncio.Event()

    async def consume():
        async for e in cli_agents.stream(cli_agents.AGY, ChatRequest(model="m", messages=msgs, tools=TOOLS)):
            if isinstance(e, TextDelta):
                got.set()
    task = asyncio.create_task(consume())
    await asyncio.wait_for(got.wait(), 10)
    task.cancel()                                   # the phone went away / the user pressed stop
    with pytest.raises(asyncio.CancelledError):
        await task
    client = agy.pool.clients[0]
    assert not client.kelo_busy and client.kelo_req is None
    assert agy.wait_spawns(2)                                       # stopped, and a fresh process is getting ready
    nxt = turn([{"role": "user", "content": "talk [[slow]]"}], "clock 2", "short please [[tools:0]]")
    text, _ = await run(nxt)
    assert "done after" in text and "talk" in agy.events("msg")[-1]["payload"]   # the whole conversation again


async def test_closing_the_pool_ends_every_agy_process(agy):
    await run(turn([], "c", "hi [[tools:0]]"))
    await run([{"role": "system", "content": "other"}, {"role": "user", "content": "x"}], tools=[])
    procs = [c._worker_proc for c in agy.pool.clients]
    agy.pool.close()
    assert all(p.poll() is not None for p in procs if p is not None)


# ---------------------------------------------------------------- continuation (pure)

def _hist():
    base = [SYSTEM, {"role": "user", "content": "old"}, {"role": "assistant", "content": "old answer"}]
    prev = [*base, {"role": "user", "content": TURN_NOTE + "t1"}, {"role": "user", "content": "ask"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "r"}]
    return base, prev


def test_the_next_tool_round_continues():
    _, prev = _hist()
    inc = [*prev, {"role": "assistant", "content": "x"}]
    assert agy_pool.continuation(prev, prev, inc) == (inc, False)


def test_the_next_turn_continues_with_only_the_new_turn():
    base, prev = _hist()
    inc = [*base, {"role": "user", "content": "ask"}, {"role": "assistant", "content": "Here it is: 42."},
           {"role": "user", "content": TURN_NOTE + "t2"}, {"role": "user", "content": "thanks"}]
    sent, new_turn = agy_pool.continuation(prev, prev, inc, said="Here it is: 42.")
    assert new_turn and sent == [*prev, *inc[-2:]]


@pytest.mark.parametrize("change", ["summary", "other_user", "foreign_assistant", "no_answer", "no_note"])
def test_anything_else_starts_over(change):
    base, prev = _hist()
    mid = [{"role": "user", "content": "ask"}, {"role": "assistant", "content": "Here it is: 42."}]
    if change == "summary":
        base = [SYSTEM, {"role": "user", "content": "a new summary"}, *base[2:]]
    elif change == "other_user":
        mid = [{"role": "user", "content": "asked something else"}, mid[1]]
    elif change == "foreign_assistant":   # a message another job added: this session never saw it
        mid = [*mid, {"role": "assistant", "content": "Your parcel has arrived."}]
    elif change == "no_answer":
        mid = mid[:1]
    tail = [{"role": "user", "content": "thanks"}] if change == "no_note" else [{"role": "user", "content": TURN_NOTE + "t2"},
                                                                               {"role": "user", "content": "thanks"}]
    assert agy_pool.continuation(prev, prev, [*base, *mid, *tail], said="Here it is: 42.") is None


def test_a_shortened_older_tool_result_still_continues_the_same_turn():
    """The agent loop turns an older tool result into one line to keep a request small; the live session still holds the full one."""
    _, prev = _hist()
    short = {"role": "tool", "tool_call_id": "a", "content": agy_pool.DIGEST_PREFIX + "f on -: r]"}
    nxt = {"role": "assistant", "content": "x"}
    sent, new_turn = agy_pool.continuation(prev, prev, [*prev[:-1], short, nxt])
    assert not new_turn and sent == [*prev, nxt]   # the session is handed what it has seen, not the short form
    edited = {"role": "tool", "tool_call_id": "a", "content": "r, but different"}
    assert agy_pool.continuation(prev, prev, [*prev[:-1], edited, nxt]) is None   # a result that really changed is not the same
    other_call = {"role": "tool", "tool_call_id": "b", "content": agy_pool.DIGEST_PREFIX + "f on -: r]"}
    assert agy_pool.continuation(prev, prev, [*prev[:-1], other_call, nxt]) is None
    assert agy_pool.why_not_continued(prev, [*prev[:-1], edited, nxt]).startswith("same turn changed at 6/7 (tool)")


def test_an_older_picture_replaced_by_its_note_still_continues_the_same_turn():
    base, prev = _hist()
    picture = {"role": "user", "content": [{"type": "text", "text": "[Not from the user: the picture(s) of tool f]"},
                                           {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}
    seen = [*prev, picture]
    gone = {"role": "user", "content": agy_pool.OLD_PICTURE.format(name="f")}
    nxt = {"role": "assistant", "content": "x"}
    assert agy_pool.continuation(seen, seen, [*prev, gone, nxt]) == ([*seen, nxt], False)
    assert agy_pool.continuation(seen, seen, [*prev, {"role": "user", "content": "just text"}, nxt]) is None


def test_the_stored_reply_may_leave_out_lines_the_session_wrote():
    """reply_text.py keeps a finished reply's opener and answer, not the short lines before the middle tool steps."""
    base, prev = _hist()
    said = "Looking now.\nSearching the site.\nOne more place.\nIt opens at 9."
    stored = "Looking now.\n\nIt opens at 9."
    inc = [*base, {"role": "user", "content": "ask"}, {"role": "assistant", "content": stored},
           {"role": "user", "content": TURN_NOTE + "t2"}, {"role": "user", "content": "thanks"}]
    assert agy_pool.continuation(prev, prev, inc, said=said) is not None
    glued = [*inc[:-3], {"role": "assistant", "content": "Looking now.It opens at 9."}, *inc[-2:]]   # kept as written: no space between
    assert agy_pool.continuation(prev, prev, glued, said="Looking now.\nIt opens at 9.") is not None
    breaks = [*inc[:-3], {"role": "assistant", "content": "Looking now.<message_break/>It opens at 9."}, *inc[-2:]]
    assert agy_pool.continuation(prev, prev, breaks, said=said) is not None
    foreign = [*inc[:-3], {"role": "assistant", "content": stored + "\n\nYour parcel has arrived."}, *inc[-2:]]
    assert agy_pool.continuation(prev, prev, foreign, said=said) is None   # a paragraph the session never wrote
    assert agy_pool.why_not_continued(prev, foreign, said) == "reply in history differs from what the session wrote"
    assert agy_pool.continuation(prev, prev, inc, said="") is None


def test_the_users_message_may_have_gone_out_with_the_apps_own_lines():
    base, _ = _hist()
    sent = "ask\n\n[Not from the user: a note for this turn only.]"
    prev = [*base, {"role": "user", "content": TURN_NOTE + "t1"}, {"role": "user", "content": sent}]
    inc = [*base, {"role": "user", "content": "ask"}, {"role": "assistant", "content": "Here it is."},
           {"role": "user", "content": TURN_NOTE + "t2"}, {"role": "user", "content": "thanks"}]
    assert agy_pool.continuation(prev, prev, inc, said="Here it is.") is not None
    other = [*inc[:3], {"role": "user", "content": "asked"}, *inc[3:]]   # not the stored message the session was sent
    assert agy_pool.continuation(prev, prev, [*base, {"role": "user", "content": "asked"}, inc[3], *inc[4:]], said="Here it is.") is None
    assert other


def test_a_system_note_in_the_middle_goes_to_the_conversation_not_the_rules():
    msgs = [SYSTEM, {"role": "user", "content": "hi"}, {"role": "system", "content": "Answer now."}]
    out = cli_agents.fit_agy_rules(msgs)
    assert out[0] == SYSTEM and out[2] == {"role": "user", "content": cli_agents.APP_NOTE + "Answer now."}
    assert agy_pool.rules_key(out) == agy_pool.rules_key([SYSTEM])


# ---------------------------------------------------------------- choosing a session (fake clients)

class FakeClient:
    def __init__(self):
        import threading
        self.is_closed, self.closed = False, False
        self.kelo_key, self.kelo_busy, self.kelo_used = "", False, 0.0
        self.kelo_req = self.kelo_sent = self.kelo_tools = None
        self.kelo_said = ""
        self._lock = threading.RLock()
        self._worker_history = []

    def kelo_alive(self, model, effort):
        return True

    def close(self):
        self.is_closed = self.closed = True


def test_the_least_recently_used_session_is_closed_when_the_pool_is_full():
    now = [0.0]
    pool = agy_pool.AgyPool(FakeClient, max_clients=2, clock=lambda: now[0])
    leases = []
    for k in range(3):
        now[0] += 1
        lease = pool.lease([{"role": "system", "content": f"p{k}"}, {"role": "user", "content": "x"}], [])
        lease.release(True)
        leases.append(lease)
    assert len(pool.clients) == 2 and leases[0].client not in pool.clients
    deadline = time.monotonic() + 5
    while not leases[0].client.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert leases[0].client.closed


def test_an_idle_session_is_closed_but_the_newest_stays():
    now = [0.0]
    pool = agy_pool.AgyPool(FakeClient, max_clients=3, idle_close=60, clock=lambda: now[0])
    a = pool.lease([{"role": "system", "content": "a"}], [], purpose="chat")
    a.release(True)
    now[0] = 10
    b = pool.lease([{"role": "system", "content": "b"}], [], purpose="chat")
    b.release(True)
    now[0] = 500
    c = pool.lease([{"role": "system", "content": "b"}], [], purpose="chat")
    assert c.client is b.client and a.client not in pool.clients
    c.release(True)
    now[0] = 5000
    d = pool.lease([{"role": "system", "content": "b"}], [], purpose="chat")
    assert d.client is b.client                     # the newest one is kept however long it waited


def test_every_session_busy_and_no_room_falls_back_to_a_one_off_run():
    pool = agy_pool.AgyPool(FakeClient, max_clients=1)
    a = pool.lease([{"role": "system", "content": "a"}], [])
    b = pool.lease([{"role": "system", "content": "a"}], [])
    assert b.client is a.client and not b.reserved and a.reserved
    b.release(True)
    assert a.client.kelo_busy                       # the one-off's end does not free the session the other call holds
    a.release(True)
    assert not a.client.kelo_busy


def test_a_full_pool_never_lends_another_prompts_busy_session():
    pool = agy_pool.AgyPool(FakeClient, max_clients=1)
    a = pool.lease([{"role": "system", "content": "a"}], [])
    b = pool.lease([{"role": "system", "content": "b"}], [])
    assert b.client is not a.client and b.transient and b.client not in pool.clients
    b.release(True)
    deadline = time.monotonic() + 5
    while not b.client.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert b.client.closed and not a.client.closed


def test_pool_size_from_the_environment(monkeypatch):
    monkeypatch.setenv("ASSISTANT_AGY_SESSIONS", "5")
    assert agy_pool.max_sessions() == 5
    monkeypatch.setenv("ASSISTANT_AGY_SESSIONS", "x")
    assert agy_pool.max_sessions(None) == 3
    assert os.environ.get("ASSISTANT_AGY_WARM") is None and agy_pool.warm_enabled()


def test_pool_size_fits_the_containers_memory(monkeypatch):
    monkeypatch.delenv("ASSISTANT_AGY_SESSIONS", raising=False)
    mb = 1024 * 1024
    assert agy_pool.max_sessions(None) == 3                 # no cap
    assert agy_pool.max_sessions(204 * mb) == 1             # the smallest size: one session, never zero
    assert agy_pool.max_sessions(227 * mb) == 1
    assert agy_pool.max_sessions(455 * mb) == 1
    assert agy_pool.max_sessions(600 * mb) == 2
    assert agy_pool.max_sessions(4096 * mb) == 3            # never more than the default
    monkeypatch.setenv("ASSISTANT_AGY_SESSIONS", "2")
    assert agy_pool.max_sessions(204 * mb) == 2             # the owner's own number wins


# ---------------------------------------------------------------- purpose: one-off calls stay off the conversation's sessions

def chat_prompt(name):
    return [{"role": "system", "content": name}, {"role": "user", "content": "x"}]


def test_a_one_off_call_never_evicts_the_chats_session_when_the_pool_is_full():
    pool = agy_pool.AgyPool(FakeClient, max_clients=1)
    chat = pool.lease(chat_prompt("chat"), [], purpose="chat")
    chat.release(True)
    memo = pool.lease(chat_prompt("memory"), [], purpose="memory")
    assert memo.transient and memo.client is not chat.client and pool.clients == [chat.client]
    assert memo.reason.startswith("pool full, one-off kept off the chat session")
    again = pool.lease(chat_prompt("chat"), [], purpose="chat")
    assert again.client is chat.client and not chat.client.closed


def test_a_one_off_session_is_evicted_before_a_conversation_one():
    now = [0.0]
    pool = agy_pool.AgyPool(FakeClient, max_clients=2, clock=lambda: now[0])
    chat = pool.lease(chat_prompt("chat"), [], purpose="chat")
    chat.release(True)
    now[0] = 1
    memo = pool.lease(chat_prompt("memory"), [], purpose="memory")
    memo.release(True)
    now[0] = 2
    worker = pool.lease(chat_prompt("worker"), [], purpose="worker")   # chat is the older one, the one-off still goes first
    assert worker.client not in (chat.client, memo.client) and chat.client in pool.clients and memo.client not in pool.clients
    worker.release(True)
    now[0] = 3
    task = pool.lease(chat_prompt("task"), [], purpose="task")        # no one-off left: the oldest conversation session goes
    assert chat.client not in pool.clients and worker.client in pool.clients and task.client in pool.clients


def test_one_off_sessions_close_after_two_minutes_and_the_chat_stays():
    now = [0.0]
    pool = agy_pool.AgyPool(FakeClient, max_clients=3, clock=lambda: now[0])
    chat = pool.lease(chat_prompt("chat"), [], purpose="chat")
    chat.release(True)
    memo = pool.lease(chat_prompt("memory"), [], purpose="memory")
    memo.release(True)
    now[0] = agy_pool.ONE_OFF_IDLE_SECONDS - 1
    pool.lease(chat_prompt("briefing"), [], purpose="briefing").release(True)
    assert memo.client in pool.clients
    now[0] = agy_pool.ONE_OFF_IDLE_SECONDS + 5
    pool.lease(chat_prompt("chat"), [], purpose="chat").release(True)
    assert memo.client not in pool.clients and chat.client in pool.clients
    now[0] = 10 * agy_pool.ONE_OFF_IDLE_SECONDS          # still far below the conversation idle limit
    assert pool.lease(chat_prompt("chat"), [], purpose="chat").client is chat.client


def test_every_lease_logs_its_purpose_mode_and_reason(caplog):
    caplog.set_level("INFO", logger=agy_pool.log.name)
    pool = agy_pool.AgyPool(FakeClient, max_clients=1)
    a = pool.lease(chat_prompt("chat"), [], purpose="chat")
    pool.lease(chat_prompt("memory"), [], purpose="memory")
    pool.lease(chat_prompt("chat"), [])
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == "agy session chat: new (no session for this prompt)"
    assert lines[1] == "agy session memory: new (pool full, one-off kept off the chat session (transient))"
    assert lines[2] == "agy session unknown: new (all sessions busy (one-off process))"
    a.release(True)
    pool.lease(chat_prompt("chat"), [], purpose="chat")
    assert caplog.records[-1].getMessage().startswith("agy session chat: new (conversation changed")
    assert not any("content" in x or "chat'" in x for x in lines)   # never the messages themselves


def test_a_dead_worker_is_named_as_the_reason(caplog):
    caplog.set_level("INFO", logger=agy_pool.log.name)
    pool = agy_pool.AgyPool(FakeClient, max_clients=1)
    pool.lease(chat_prompt("chat"), [], purpose="chat").release(True)
    pool.clients[0].kelo_alive = lambda m, e: False
    pool.lease(chat_prompt("chat"), [], purpose="chat")
    assert caplog.records[-1].getMessage() == "agy session chat: new (worker not alive)"


# ---------------------------------------------------------------- purpose flows from the job kind to the pool

async def test_metered_fills_the_purpose_from_the_kind():
    from assistant import usage as usage_meter
    seen = []

    async def fake(provider, request, **kw):
        seen.append(request.purpose)
        return
        yield

    counted = usage_meter.metered(fake, lambda r: None, lambda: {"source": "schedule:4", "task_id": 1})
    for kind, req in ((None, ChatRequest(model="m", messages=[])), ("memory", ChatRequest(model="m", messages=[])),
                      ("memory", ChatRequest(model="m", messages=[], purpose="worker"))):
        async for _ in counted(kind)("p", req):
            pass
    assert seen == ["schedule", "memory", "worker"]


def test_purpose_round_trips_to_the_sidecar():
    from assistant.providers import cli_remote
    req = ChatRequest(model="m", messages=[], purpose="chat")
    assert cli_remote.request_from_json(cli_remote.request_to_json(req)).purpose == "chat"
    assert cli_remote.request_from_json({"model": "m"}).purpose == ""


async def test_cli_agents_hands_the_purpose_to_the_lease(monkeypatch):
    got = []

    class Boom(Exception):
        pass

    class Pool:
        def lease(self, messages, tools, model, effort, purpose=""):
            got.append(purpose)
            raise Boom

    monkeypatch.setattr(cli_agents, "_clients", {cli_agents.AGY: Pool()})
    with pytest.raises(Exception):
        async for _ in cli_agents.stream(cli_agents.AGY, ChatRequest(model="m", messages=[{"role": "user", "content": "x"}], purpose="memory")):
            pass
    assert got == ["memory"]


@pytest.mark.parametrize("provider", ["anthropic", "openrouter", "openai"])
async def test_the_purpose_never_reaches_a_providers_http_body(provider):
    import httpx
    from assistant.providers import stream_chat
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    async def body_for(purpose):
        bodies.clear()
        req = ChatRequest(model="m", messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}], purpose=purpose)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            try:
                _ = [e async for e in stream_chat(provider, req, api_key="k", client=c)]
            except Exception:  # noqa: BLE001 - only the request body matters here
                pass
        return bodies[0]

    assert await body_for("chat") == await body_for("")


# ---------------------------------------------------------------- warming the chat's session (no model request)

async def test_warming_spawns_once_sends_no_model_request_and_the_next_chat_call_uses_it(agy):
    system = [SYSTEM]
    req = ChatRequest(model="gemini-test-low", messages=system, purpose="chat")
    assert await cli_agents.warm(cli_agents.AGY, req) is True
    assert agy.wait_spawns(1)
    assert await cli_agents.warm(cli_agents.AGY, req) is True       # again: the same worker, nothing new
    time.sleep(0.3)
    assert len(agy.events("spawn")) == 1 and agy.events("msg") == []
    (client,) = agy.pool.clients
    assert client.kelo_req is None and not client.kelo_busy
    text, _ = await run(turn([], "clock 10:00", "hello"))
    assert text.startswith("done after 0 results")
    assert len(agy.events("spawn")) == 1 and len(agy.events("msg")) == 1 and agy.pool.clients == [client]


# ---------------------------------------------------------------- warming when the app is opened

@pytest.fixture
def opened(monkeypatch, tmp_path):
    """Runs a server whose main model is `provider`; returns (calls of the warm, open(path) that makes one signed-in request)."""
    import contextlib
    import asyncio
    import httpx
    from assistant.server import Settings
    from assistant.providers import cli_manage, cli_remote
    from assistant.server import create_app
    from test_server import AUTH, TOKEN, Running

    @contextlib.asynccontextmanager
    async def go(provider):
        now = [1e12]
        calls = []

        async def fake_warm(name, req, **kw):
            calls.append((name, req.purpose))
            return True

        monkeypatch.setattr(cli_remote, "warm", fake_warm)
        app = create_app(Settings(token=TOKEN, provider=provider, model="gemini-x", cdp_host=None), tools=[])
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            await asyncio.sleep(0.2)
            monkeypatch.setattr("assistant.server.warm_clock", lambda: now[0])   # opens made while starting are not counted
            calls.clear()   # the start-up warm is not what is tested here

            async def hit(path="/api/profile"):
                await c.get(path)
                for _ in range(100):   # the warm runs as a background task of the server
                    await asyncio.sleep(0.05)
                    if calls:
                        break
                await asyncio.sleep(0.3)
            yield calls, hit, now
    return go


async def test_a_foreground_request_warms_once_a_minute(opened):
    from assistant.providers import cli_manage
    async with opened(cli_manage.AGY) as (calls, hit, now):
        await hit()
        assert calls == [(cli_manage.AGY, "chat")]
        now[0] += 30
        await hit()
        assert len(calls) == 1
        now[0] += 31
        await hit()
        assert len(calls) == 2


async def test_a_notifications_poll_does_not_warm(opened):
    from assistant.providers import cli_manage
    async with opened(cli_manage.AGY) as (calls, hit, now):
        await hit("/api/notifications")
        assert calls == []


async def test_a_model_that_is_not_agy_does_not_warm(opened):
    async with opened("p") as (calls, hit, now):
        await hit()
        assert calls == []


async def test_a_malformed_tool_call_from_gemini_is_asked_again_before_any_output(monkeypatch):
    """2026-11-11: agy gave up with "improperly formatted function call … Retries remaining: 3" and a 471-item job failed twice."""
    from assistant.providers import client as pclient
    from assistant.providers.types import ProviderError, TextDelta, Usage
    calls = []

    async def flaky(name, request):
        calls.append(name)
        if len(calls) < 3:
            yield Usage(input_tokens=1, output_tokens=0)
            raise ProviderError("antigravity-oauth: Antigravity model error: Your previous response contained an improperly formatted "
                                "function call\nPlease retry with a properly formatted function call\nRetries remaining: 3")
        yield TextDelta("ok")

    monkeypatch.setattr(pclient.cli_remote, "remote_url", lambda name: None)
    monkeypatch.setattr(pclient.cli_agents, "stream", flaky)
    got = [ev async for ev in pclient.stream_chat("antigravity-oauth", ChatRequest(model="m", messages=[{"role": "user", "content": "x"}]))]
    assert len(calls) == 3 and any(isinstance(e, TextDelta) for e in got)

    calls.clear()

    async def after_text(name, request):
        calls.append(name)
        yield TextDelta("half")
        raise ProviderError("improperly formatted function call")
    monkeypatch.setattr(pclient.cli_agents, "stream", after_text)
    with pytest.raises(ProviderError):                       # something was already said: not asked again
        async for _ in pclient.stream_chat("antigravity-oauth", ChatRequest(model="m", messages=[{"role": "user", "content": "x"}])):
            pass
    assert len(calls) == 1

    calls.clear()

    async def other(name, request):
        calls.append(name)
        raise ProviderError("antigravity-oauth: quota reached")
        yield  # noqa
    monkeypatch.setattr(pclient.cli_agents, "stream", other)
    with pytest.raises(ProviderError):                       # any other error goes on as before
        async for _ in pclient.stream_chat("antigravity-oauth", ChatRequest(model="m", messages=[{"role": "user", "content": "x"}])):
            pass
    assert len(calls) == 1


def test_an_emptied_sign_in_token_is_put_back_from_the_kept_copy(tmp_path, monkeypatch):
    """2026-11-11 22:30: two agy starts 0.3 s apart left the token file empty and every reply failed with "not authenticated"."""
    from assistant.providers import agy_pool
    monkeypatch.setenv("ANTIGRAVITY_CONFIG_DIR", str(tmp_path))
    token = tmp_path / "antigravity-oauth-token"
    token.write_text("x" * 64)
    assert agy_pool.guard_token() == "kept"
    keep = tmp_path / ("antigravity-oauth-token" + agy_pool.BACKUP_SUFFIX)
    assert keep.read_text() == "x" * 64 and oct(keep.stat().st_mode & 0o777) == "0o600"
    assert agy_pool.guard_token() == ""                        # nothing changed: nothing to do
    token.write_text("")                                       # truncated by two starts at once
    assert agy_pool.guard_token() == "restored" and token.read_text() == "x" * 64
    token.write_text("y" * 64)                                 # a refreshed token is the new copy
    assert agy_pool.guard_token() == "kept" and keep.read_text() == "y" * 64


def test_agy_starts_are_one_at_a_time(monkeypatch):
    import threading as th
    import time as tm
    from assistant.providers import agy_pool
    monkeypatch.setattr(agy_pool, "SPAWN_GAP_SECONDS", 0.2)
    monkeypatch.setattr(agy_pool, "guard_token", lambda: "")
    starts, active, peak = [], [0], [0]

    def fake_start():
        with agy_pool._spawn_lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            starts.append(tm.monotonic())
            tm.sleep(agy_pool.SPAWN_GAP_SECONDS)
            active[0] -= 1
    ts = [th.Thread(target=fake_start) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1 and max(starts) - min(starts) >= 0.39


async def test_a_busy_gemini_is_waited_out_a_few_times(monkeypatch):
    """2026-11-11 23:20: "high traffic … No capacity available (UNAVAILABLE 503)" ended a 123-step job."""
    from assistant.providers import client as pclient
    from assistant.providers.types import ProviderError, TextDelta
    waits, calls = [], []

    async def no_sleep(s):
        waits.append(s)
    monkeypatch.setattr(pclient.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(pclient.cli_remote, "remote_url", lambda name: None)

    async def busy_twice(name, request):
        calls.append(1)
        if len(calls) <= 2:
            raise ProviderError("antigravity-oauth: Antigravity model error: Our servers are experiencing high traffic right now, please "
                                "try again in a minute. (UNAVAILABLE (code 503): No capacity available for model g")
        yield TextDelta("ok")
    monkeypatch.setattr(pclient.cli_agents, "stream", busy_twice)
    got = [e async for e in pclient.stream_chat("antigravity-oauth", ChatRequest(model="m", messages=[{"role": "user", "content": "x"}]))]
    assert any(isinstance(e, TextDelta) for e in got) and waits == list(pclient.BUSY_WAITS[:2])

    calls.clear(); waits.clear()

    async def always_busy(name, request):
        calls.append(1)
        raise ProviderError("UNAVAILABLE (code 503): No capacity available")
        yield  # noqa
    monkeypatch.setattr(pclient.cli_agents, "stream", always_busy)
    with pytest.raises(ProviderError):
        async for _ in pclient.stream_chat("antigravity-oauth", ChatRequest(model="m", messages=[{"role": "user", "content": "x"}])):
            pass
    assert len(calls) == len(pclient.BUSY_WAITS) + 1


def test_the_usage_note_says_where_a_conversation_parted_from_its_session():
    sys_m = {"role": "system", "content": "rules"}
    note = lambda n: {"role": "user", "content": f"{agy_pool.TURN_NOTE} {n}"}   # noqa: E731
    prev = [sys_m, note(1), {"role": "user", "content": "q1"}]
    assert agy_pool.why_not_continued(None, prev) == "no earlier request"
    changed = [{"role": "system", "content": "rules v2"}, {"role": "user", "content": "q1"},
               {"role": "assistant", "content": "a1"}, note(2), {"role": "user", "content": "q2"}]
    assert agy_pool.why_not_continued(prev, changed).startswith("base changed at 0/1 (system)")
    other_reply = [sys_m, {"role": "user", "content": "q1"}, {"role": "assistant", "content": "edited a1"}, note(2),
                   {"role": "user", "content": "q2"}]
    assert agy_pool.why_not_continued(prev, other_reply, said="a1") == "reply in history differs from what the session wrote"
    moved = [sys_m, {"role": "user", "content": "q1 changed"}, {"role": "assistant", "content": "a1"}, note(2),
             {"role": "user", "content": "q2"}]
    assert agy_pool.why_not_continued(prev, moved, said="a1") == "earlier question not where it was"
