"""Compaction: context windows (provider / table / learned / override / default), the 70 % trigger, the rolling summary (head and
tail kept, incremental, failure-safe), the memory step before a fold, tool-result digests inside a turn, the agy split, and the API.
Every model here is a fake: no request leaves the machine."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from assistant import compaction, windows
from assistant.agent import Tool, run_agent
from assistant.agent.loop import DIGEST_PREFIX, digest_of, digest_tool_results
from assistant.compaction import (
    Compactor, ContextOverflow, est_tokens, is_context_error, message_tokens, request_tokens, select_history, summary_message,
)
from assistant.models import _windows
from assistant.providers import Done, TextDelta, ToolCall, Usage, cli_agents
from assistant.providers.types import CONVERSATION_SUMMARY_HEAD, ProviderError
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.windows import Window

from test_server import AUTH, TOKEN, Running, follow

SUMMARY_SYS = "You keep the running summary"
FLUSH_SYS = "Older messages of your conversation"


def msg_text(i: int) -> str:
    return f"m{i:03d} " + "가" * 95          # about 100 tokens each


def seed(store: Store, n: int, start: int = 0) -> list[int]:
    return [store.add_message("user" if i % 2 == 0 else "assistant", msg_text(i)) for i in range(start, start + n)]


def system_of(request) -> str:
    m = request.messages[0] if request.messages else {}
    return m.get("content", "") if m.get("role") == "system" else ""


class Fake:
    """A scripted model: summary requests get `summaries` in turn (an Exception raises), memory steps write MEMORY.md once then say
    NO_REPLY, everything else answers `answer`. `fail_turn`: errors the turn's first request(s)."""

    def __init__(self, summaries=("요약 1",), answer="네", fail_turn=None):
        self.summaries, self.answer, self.fail_turn = list(summaries), answer, list(fail_turn or [])
        self.calls: list[tuple[str, object]] = []

    def of(self, kind):
        return [r for p, r in self.calls if self.kind(r) == kind]

    @staticmethod
    def kind(request) -> str:
        s = system_of(request)
        return "summary" if s.startswith(SUMMARY_SYS) else "flush" if s.startswith(FLUSH_SYS) else "turn"

    async def __call__(self, provider, request, **kw):
        from assistant.server import TURN_NOTE
        notes = [i for i, m in enumerate(request.messages) if isinstance(m.get("content"), str) and m["content"].startswith(TURN_NOTE)]
        assert len(notes) <= 1                                     # the turn's own lines: once, right before the turn's message
        # kept without that note, so the order of system, summary, history and message reads as before
        request = replace(request, messages=[m for i, m in enumerate(request.messages) if i not in notes])
        self.calls.append((provider, request))
        kind = self.kind(request)
        if kind == "summary":
            out = self.summaries.pop(0) if len(self.summaries) > 1 else self.summaries[0]
            if isinstance(out, Exception):
                raise out
            yield TextDelta(out)
        elif kind == "flush":
            if not any(m.get("role") == "tool" for m in request.messages):
                yield ToolCall("c1", "write_file", json.dumps({"path": "MEMORY.md", "content": "# MEMORY.md\n\n## Facts\n- 제주 여행 3월\n"}))
            else:
                yield TextDelta("NO_REPLY")
        else:
            if self.fail_turn:
                e = self.fail_turn.pop(0)
                if e is not None:
                    raise e
            yield TextDelta(self.answer)
            yield Usage(3, 1)
        yield Done("stop")


# ------------------------------------------------------------------------------------------------------------- context windows
def test_the_table_knows_common_families_and_unknown_models_get_150k():
    assert windows.table_window("claude-opus-5-5") == (1_000_000, 128_000) and windows.table_window("claude-haiku-4-5")[0] == 200_000 and windows.table_window("sonnet")[0] == 200_000
    assert windows.table_window("gemini-3.8-flash-low")[0] == 1_048_576          # agy's model names
    assert windows.table_window("gpt-5-mini") == (400_000, 128_000) and windows.table_window("gpt-4o-mini")[0] == 128_000
    assert windows.table_window("anthropic/claude-3.5-haiku") == (200_000, 8192)  # OpenRouter-style ids
    assert windows.table_window("llama-3.3-70b-versatile")[0] == 131_072 and windows.table_window("o3-mini")[0] == 200_000
    assert windows.table_window("totally-new-model") is None
    w = windows.lookup("someprov", "totally-new-model")
    assert (w.context, w.source) == (150_000, "default") and windows.default_context() == 150_000


def test_the_providers_model_list_gives_the_window_and_wins_over_the_table():
    listing = {"data": [{"id": "openai/gpt-4o-mini", "context_length": 64_000, "top_provider": {"max_completion_tokens": 9_000}},
                        {"id": "llama-3.3-70b", "context_window": 32_768},
                        {"name": "models/gemini-x", "inputTokenLimit": 1_000_000, "outputTokenLimit": 65_536},
                        {"id": "no-window"}]}
    found = _windows(listing)
    assert found == {"openai/gpt-4o-mini": (64_000, 9_000), "llama-3.3-70b": (32_768, None), "gemini-x": (1_000_000, 65_536)}
    windows.remember_provider_windows("orx", found)
    assert windows.lookup("orx", "openai/gpt-4o-mini") == Window(64_000, 9_000, "provider")
    assert windows.lookup("orx2", "llama-3.3-70b", stored={"llama-3.3-70b": [32_768, None]}) == Window(32_768, None, "provider")
    assert windows.lookup("orx2", "llama-3.3-70b").source == "table"


def test_the_owners_override_wins_and_learned_limits_hold_until_the_provider_says_otherwise():
    assert windows.parse_overrides("gpt-4o-mini=64000, p/m = 32k, bad, x=") == {"gpt-4o-mini": 64_000, "p/m": 32_000}
    assert windows.lookup("p", "m", overrides={"p/m": 32_000}) == Window(32_000, None, "override")
    learned = {"context": 50_000, "base_context": 150_000, "base_source": "default"}
    assert windows.lookup("p", "new-model", learned=learned) == Window(50_000, None, "learned")
    assert windows.lookup("p", "new-model", learned={**learned, "context": 200_000}).source == "default"   # never raised by learning
    assert windows.lookup("p", "new-model", learned=learned, overrides={"new-model": 90_000}).source == "override"
    # learned against the provider's 64k: stays while the provider says 64k, dropped when its list reports something else
    windows.remember_provider_windows("prv", {"mm": (64_000, None)})
    on_provider = {"context": 40_000, "base_context": 64_000, "base_source": "provider"}
    assert windows.lookup("prv", "mm", learned=on_provider).source == "learned"
    windows.remember_provider_windows("prv", {"mm": (128_000, None)})
    assert windows.lookup("prv", "mm", learned=on_provider) == Window(128_000, None, "provider")


def test_context_length_errors_are_recognised():
    assert is_context_error(ProviderError("anything", status=400, code="context_length_exceeded"))
    assert is_context_error(ProviderError("prompt is too long", status=400), 199_000, 200_000)       # a 400 for a request that big
    assert not is_context_error(ProviderError("prompt is too long", status=400), 20_000, 200_000)    # its words decide nothing
    assert is_context_error(ProviderError("anything", status=413))
    assert not is_context_error(ProviderError("invalid api key", status=401))
    assert not is_context_error(ProviderError("rate limited: too many requests", status=429))


# ------------------------------------------------------------------------------------------------------------- selecting history
def test_everything_fits_or_the_newest_are_kept_with_at_least_two_and_shortened_on_tiny_budgets():
    cands = [{"id": i, "role": "user", "content": msg_text(i)} for i in range(20)]
    every = select_history(cands, "", 10_000)
    assert len(every.kept) == 20 and every.dropped == []
    some = select_history(cands, "", 1_000)
    assert [m["id"] for m in some.kept] == list(range(11, 20)) and len(some.dropped) == 11 and some.tokens <= 1_000
    tiny = select_history([{**c, "content": "긴 " * 600} for c in cands], "요약 " * 400, 300)
    assert len(tiny.kept) == compaction.MIN_KEEP == 2 and tiny.shortened and tiny.kept[0]["content"].endswith(compaction.SHORTENED)
    assert len(tiny.summary) < len("요약 " * 400)


def test_estimates_are_bytes_over_three():
    assert est_tokens("abc") == 1 and est_tokens("가나다") == 3 and est_tokens("") == 0
    assert message_tokens({"role": "user", "content": "가나다"}) == 3 + compaction.MESSAGE_OVERHEAD


# ------------------------------------------------------------------------------------------------------------- the compactor
W = Window(6_000, None, "override")   # threshold 4,200 tokens, target 2,700


async def test_under_the_threshold_everything_is_sent_with_no_model_call():
    store, fake = Store(), Fake()
    seed(store, 10)
    c = Compactor(store, fake)
    a = c.assemble(W, 500)
    assert not a.over and len(a.messages) == 10 and a.head and not a.summary_msgs
    assert (await c.compact("p", "m", W, 500))["status"] == "nothing" and fake.calls == []


async def test_a_fold_keeps_head_and_tail_word_for_word_and_summarizes_only_the_middle():
    store, fake = Store(), Fake(summaries=["요약 A"])
    ids = seed(store, 40)
    c = Compactor(store, fake)
    assert c.needs_fold(W, 500)
    res = await c.compact("p", "m", W, 500)
    assert res["status"] == "done" and res["folded"] > 0
    bodies = "\n".join(r.messages[1]["content"] for r in fake.of("summary"))
    for i in range(4):                                     # the head is never summarized
        assert f"m{i:03d}" not in bodies
    assert "m004" in bodies and "(none)" in fake.of("summary")[0].messages[1]["content"]
    sys_prompt = system_of(fake.of("summary")[0])
    assert "## Goals and requests" in sys_prompt and "## User preferences" in sys_prompt and "## Decided against" in sys_prompt
    assert "## Standing rules" in sys_prompt and "## Commitments" in sys_prompt and "language the conversation is in" in sys_prompt
    cur = store.conversation_summary()
    assert cur["summary"] == "요약 A" and cur["up_to_message_id"] < ids[-2] and cur["messages_folded"] == res["folded"] and cur["compactions"] >= 1
    a = c.assemble(W, 500)
    assert not a.over
    assert [m["content"] for m in a.head] == [msg_text(i) for i in range(4)]          # head verbatim
    assert a.summary_msgs == [summary_message("요약 A")] and a.messages[4]["content"].startswith(CONVERSATION_SUMMARY_HEAD)
    assert [m["content"] for m in a.history[-2:]] == [msg_text(i) for i in range(38, 40)]   # tail verbatim
    assert a.tokens < W.context * c.threshold


async def test_the_summary_grows_incrementally_from_the_previous_one_and_only_new_messages():
    store, fake = Store(), Fake(summaries=["요약 A"])
    seed(store, 40)
    c = Compactor(store, fake)
    await c.compact("p", "m", W, 500)
    first_up_to = store.conversation_summary()["up_to_message_id"]
    n_first = len(fake.of("summary"))
    fake.summaries = ["요약 B"]
    seed(store, 50, start=40)
    assert (await c.compact("p", "m", W, 500))["status"] == "done"
    later = fake.of("summary")[n_first:]
    body = later[0].messages[1]["content"]
    assert body.startswith("Previous summary:\n요약 A")
    old = {f"m{i:03d}" for i in range(4, first_up_to)}     # ids are 1-based: message i has id i+1
    assert not any(tag in r.messages[1]["content"] for r in later for tag in old)
    assert store.conversation_summary()["summary"] == "요약 B"


async def test_a_failed_fold_keeps_the_old_summary_and_the_next_turn_tries_again():
    store, fake = Store(), Fake(summaries=["요약 A"])
    seed(store, 40)
    c = Compactor(store, fake)
    await c.compact("p", "m", W, 500)
    before = store.conversation_summary()
    fake.summaries = [RuntimeError("model down"), "요약 C"]
    seed(store, 40, start=40)
    res = await c.compact("p", "m", W, 500)
    assert res["status"] == "failed" and "model down" in res["reason"] and c.last_error
    assert store.conversation_summary() == before
    a = c.assemble(W, 500)                                 # still over: this request is cut to the target anyway
    assert a.over and a.left_out > 0 and a.tokens - 500 <= W.context * c.target + 50
    assert (await c.compact("p", "m", W, 500))["status"] == "done" and store.conversation_summary()["summary"] == "요약 C"
    assert c.last_error == ""


async def test_clearing_the_chat_forgets_the_summary_and_a_fold_racing_a_clear_is_dropped():
    store, fake = Store(), Fake()
    seed(store, 40)
    await Compactor(store, fake).compact("p", "m", W, 500)
    assert store.conversation_summary()["summary"]
    store.clear_messages()
    assert store.conversation_summary()["summary"] == "" and store.conversation_summary()["up_to_message_id"] == 0
    seed(store, 3)
    assert not store.save_conversation_summary("x", 2, 1, expect_up_to=5)            # changed meanwhile
    assert not store.save_conversation_summary("x", 999, 1, expect_up_to=0)          # its messages are gone


async def test_a_fold_has_no_memory_step_the_memory_pass_reads_the_messages_themselves(tmp_path):
    """Nothing is saved before a fold any more (memory-owner-only): the memory pass reads the owner's messages from the table, which a
    fold never deletes, so the memory files stay as they are and no memory step runs."""
    from assistant.home import Home
    home = Home(tmp_path / "home")
    home.write("MEMORY.md", "# MEMORY.md\n\n## About the user\n- Likes tea\n", by="system")
    store, fake = Store(), Fake(summaries=["요약 A"])
    seed(store, 60)
    before, count = (tmp_path / "home" / "MEMORY.md").read_text(), store.message_count()
    c = Compactor(store, fake)
    res = await c.compact("p", "m", Window(4_000, None, "override"), 400)
    assert res["status"] in ("done", "partial") and len(fake.of("summary")) >= 2
    assert (tmp_path / "home" / "MEMORY.md").read_text() == before and not fake.of("flush")
    assert store.conversation_summary()["memory_flushes"] == 0 and store.message_count() == count


# ------------------------------------------------------------------------------------------------------------- inside one turn
def tool_turn(n: int, size: int = 3_000, name: str = "web_search") -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"t{i}", "type": "function", "function": {"name": name, "arguments": json.dumps({"query": f"q{i}"})}}]})
        out.append({"role": "tool", "tool_call_id": f"t{i}", "content": f"first line {i}\n" + "x" * size})
    return out


def test_old_tool_results_become_one_line_digests_but_the_newest_the_page_and_approvals_stay():
    history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "find"}]
    history += [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "pg", "type": "function", "function": {"name": "browser_open", "arguments": json.dumps({"url": "https://shop.example"})}}]},
        {"role": "tool", "tool_call_id": "pg", "content": "[UNTRUSTED CONTENT 0a1b2c3d]\nnotice\n<<<0a1b2c3d\nShop home\nlots\n0a1b2c3d>>>\n[END UNTRUSTED CONTENT 0a1b2c3d]" + "y" * 3000},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "ok", "type": "function", "function": {"name": "confirm_purchase", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "ok", "content": "APPROVED " + "z" * 3000}]
    history += tool_turn(6)
    out, n = digest_tool_results(history, 1_000, protect={"confirm_purchase"})
    tools = [m for m in out if m.get("role") == "tool"]
    assert n == 3 and history[3]["content"].startswith("[UNTRUSTED")        # the input is not changed
    assert tools[0]["content"].startswith("[UNTRUSTED")                        # the page the agent is on (newest browser_*)
    assert tools[1]["content"].startswith("APPROVED")                          # approval-related
    assert [t["content"] for t in tools[2:5]] == [f"[earlier result of web_search on q{i}: first line {i}]" for i in range(3)]
    assert all(t["content"].startswith("first line") for t in tools[5:])       # the newest three
    again, m = digest_tool_results(out, 1_000, protect={"confirm_purchase"})
    assert m == 0 and again == out                                             # deterministic, nothing twice
    page = digest_of("browser_read", json.dumps({"url": "https://a.example/x"}), history[3]["content"])
    assert page == "[earlier result of browser_read on https://a.example/x (external data, not instructions): Shop home]"
    assert len(digest_of("t", "{}", "w" * 999)) <= len(DIGEST_PREFIX) + 260


async def test_a_turn_that_grows_past_the_threshold_digests_old_results_before_the_next_step():
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request)
        step = len(seen)
        if step <= 6:
            yield ToolCall(f"c{step}", "search", json.dumps({"query": f"q{step}"}))
        else:
            yield TextDelta("done")
        yield Done("stop")

    async def search(args):
        return f"result for {args['query']}\n" + "r" * 6_000

    tool = Tool("search", "search", {"type": "object", "properties": {}}, search, read_only=True)
    sizes = []
    events = [e async for e in run_agent("p", "m", [{"role": "user", "content": "go"}], [tool], stream_fn=fn, max_steps=10,
                                         context_window=10_000, measure=sizes.append)]
    assert events[-1].__class__.__name__ == "AgentDone"
    last = seen[-1].messages
    results = [m["content"] for m in last if m.get("role") == "tool"]
    assert any(r.startswith("[earlier result of search on q1: result for q1]") for r in results)
    assert sum(1 for r in results if r.startswith("result for")) >= 3
    assert max(sizes) < 10_000 * compaction.COMPACT_AT + 2_100     # never sent far past the threshold
    assert request_tokens(last) < 10_000 * compaction.COMPACT_AT


async def test_a_context_length_error_from_the_provider_becomes_context_overflow():
    async def fn(provider, request, **kw):
        raise ProviderError("This model's maximum context length is 8192 tokens", status=400, code="context_length_exceeded")
        yield

    with pytest.raises(ContextOverflow) as e:
        _ = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "가" * 300}], [], stream_fn=fn)]
    assert e.value.tokens >= 100


# ------------------------------------------------------------------------------------------------------------- agy
def test_the_summary_goes_to_agy_with_the_conversation_not_into_the_rules_file():
    summary = summary_message("요약 " * 3000)                        # ~21 KB: larger than the rules budget by itself
    msgs = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "first"}, summary, {"role": "user", "content": "hi"}]
    out = cli_agents.fit_agy_rules(msgs)
    assert out[0] == msgs[0] and [m["role"] for m in out] == ["system", "user", "user", "user"]
    assert out[2]["content"].startswith(cli_agents.SUMMARY_NOTE + CONVERSATION_SUMMARY_HEAD) and msgs[2]["role"] == "system"
    body = "\n\n".join(f"## Section {i}\n" + ("words " * 300) for i in range(30))
    long = [{"role": "system", "content": body}, summary, {"role": "user", "content": "go"}]
    split = cli_agents.fit_agy_rules(long)
    systems = [m for m in split if m["role"] == "system"]
    assert len(systems) == 1 and len(systems[0]["content"].encode()) <= cli_agents.AGY_RULES_BUDGET
    assert len(systems[0]["content"].encode()) > cli_agents.AGY_RULES_BUDGET - 3_000      # the summary took none of the rules budget
    assert split[1]["content"].startswith(cli_agents.PART_TWO) and split[2]["content"].startswith(cli_agents.SUMMARY_NOTE)


# ------------------------------------------------------------------------------------------------------------- through the app
def app_settings(tmp_path, **kw):
    kw.setdefault("context_overrides", {"m": 6_000})
    return Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), **kw)


async def talk(c, text="안녕"):
    started = await c.post("/api/turns", json={"text": text})
    assert started.status_code == 200, started.text
    return await follow(c, started.json()["id"])


async def test_a_turn_over_the_threshold_folds_first_then_sends_head_summary_tail_in_order(tmp_path):
    fake = Fake(summaries=["요약: 제주 여행"])
    async with Running(create_app(app_settings(tmp_path, scheduler=False), tools=[], stream_fn=fake)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        store = Store(str(tmp_path / "a.db"))
        seed(store, 40)
        events = await talk(c, "다음 할 일은?")
        assert [e["type"] for e in events][-1] == "done"
        kinds = [Fake.kind(r) for _, r in fake.calls]
        assert kinds.index("summary") < kinds.index("turn")                # folded before the reply's request
        turn = fake.of("turn")[0].messages
        roles = [(m["role"], m["content"][:4]) for m in turn]
        assert roles[0][0] == "system" and turn[1]["content"] == msg_text(0) and turn[4]["content"] == msg_text(3)
        assert turn[5]["role"] == "system" and turn[5]["content"] == f"{CONVERSATION_SUMMARY_HEAD} 요약: 제주 여행"
        assert turn[-1]["content"] == "다음 할 일은?" and turn[-2]["content"] == msg_text(39)
        assert request_tokens(turn) < 6_000 * 0.75
        info = (await c.get("/api/conversation/summary")).json()
        assert info["summary"] == "요약: 제주 여행" and info["context_window"] == 6_000 and info["source"] == "override"
        assert info["threshold"] == 0.75 and info["last_request_tokens"] > 0 and info["compactions"] >= 1 and info["model"] == "m"
        assert info["messages_folded"] > 0 and info["up_to_message_id"] > 4
        assert (await c.delete("/api/conversation/summary")).json() == {"ok": True}
        assert (await c.get("/api/conversation/summary")).json()["summary"] == ""
        await talk(c)
        assert (await c.delete("/api/messages")).json()["ok"] is True
        cleared = (await c.get("/api/conversation/summary")).json()
        assert cleared["summary"] == "" and cleared["messages_folded"] == 0


async def test_under_the_threshold_nothing_is_folded_and_after_a_turn_a_background_fold_prepares_the_next(tmp_path):
    fake = Fake(summaries=["배경 요약"])
    async with Running(create_app(app_settings(tmp_path, scheduler=False), tools=[], stream_fn=fake)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        store = Store(str(tmp_path / "a.db"))
        seed(store, 4)
        await talk(c)
        assert fake.of("summary") == [] and len(fake.of("turn")[0].messages) == 6      # system + 4 + the message
        seed(store, 22, start=6)                                                          # now close to the threshold
        fake.answer = "가" * 600                                                          # a long answer tips it over
        await talk(c)
        assert fake.of("summary") == [] or Fake.kind(fake.calls[-1][1]) == "summary"     # not before the reply
        for _ in range(100):
            if store.conversation_summary()["summary"]:
                break
            await asyncio.sleep(0.02)
        assert store.conversation_summary()["summary"] == "배경 요약"                    # folded after the turn, in the background
        n = len(fake.of("summary"))
        fake.answer = "네"
        await talk(c)
        assert len(fake.of("summary")) == n                                               # the next turn starts without folding


async def test_the_fallback_model_gets_a_conversation_rebuilt_for_its_own_smaller_window(tmp_path):
    fake = Fake(summaries=["요약 F"], fail_turn=[RuntimeError("main model down")])
    s = app_settings(tmp_path, scheduler=False, context_overrides={"small": 6_000})
    async with Running(create_app(s, tools=[], stream_fn=fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"fallback_provider": "q", "fallback_model": "small"})
        store = Store(str(tmp_path / "a.db"))
        seed(store, 40)
        events = await talk(c)
        assert [e["type"] for e in events if e["type"] in ("fallback", "done")] == ["fallback", "done"]
        turns = [(p, r) for p, r in fake.calls if Fake.kind(r) == "turn"]
        main, fb = turns[0][1].messages, turns[1][1].messages
        assert turns[0][0] == "p" and turns[1][0] == "q"
        assert len(main) == 42 and not any(m["content"].startswith(CONVERSATION_SUMMARY_HEAD) for m in main)   # 150k: all of it
        assert any(m["content"].startswith(CONVERSATION_SUMMARY_HEAD) for m in fb) and len(fb) < 30          # 6k: compacted
        assert {p for p, r in fake.calls if Fake.kind(r) == "summary"} == {"q"}           # folded by the model of the turn


async def test_a_context_length_error_lowers_the_window_retries_once_compacted_and_is_remembered(tmp_path):
    err = ProviderError("This model's maximum context length is 3000 tokens. However, your messages resulted in 4200 tokens", status=400, code="context_length_exceeded")
    fake = Fake(summaries=["요약 L"], fail_turn=[err])
    s = app_settings(tmp_path, scheduler=False, context_overrides={})
    async with Running(create_app(s, tools=[], stream_fn=fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        store = Store(str(tmp_path / "a.db"))
        seed(store, 40)                          # ~4,000 tokens: fine for an unknown model's 150k, too much for this one
        events = await talk(c)                   # (the learned window is 0.9 x the whole request, the prompt's turn lines included)
        assert [e["type"] for e in events if e["type"] in ("retry", "done", "error")] == ["retry", "done"]
        learned = json.loads(store.meta("learned_window:p/m"))
        assert learned["base_source"] == "default" and learned["base_context"] == 150_000 and 3_000 < learned["context"] < 4_800
        info = (await c.get("/api/conversation/summary")).json()
        assert info["source"] == "learned" and info["context_window"] == learned["context"]
        first, second = fake.of("turn")[0].messages, fake.of("turn")[1].messages
        assert len(second) < len(first) and any(m["content"].startswith(CONVERSATION_SUMMARY_HEAD) for m in second)
        await talk(c)                            # a success does not raise it again
        assert json.loads(store.meta("learned_window:p/m"))["context"] == learned["context"]
        assert (await c.get("/api/conversation/summary")).json()["source"] == "learned"


async def test_the_app_fold_writes_no_memory_and_says_nothing_in_the_chat(tmp_path):
    fake = Fake(summaries=["요약 M"])
    s = app_settings(tmp_path, scheduler=False, home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[], stream_fn=fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        store = Store(str(tmp_path / "a.db"))
        seed(store, 40)
        events = await talk(c)
        assert "".join(e.get("text", "") for e in events if e["type"] == "text") == "네"
        assert fake.of("flush") == []                                  # no memory step before a fold
        kinds = [Fake.kind(r) for _, r in fake.calls]
        assert kinds.index("summary") < kinds.index("turn")
        assert "제주 여행 3월" not in ((tmp_path / "home" / "MEMORY.md").read_text() if (tmp_path / "home" / "MEMORY.md").exists() else "")
        contents = [m["content"] for m in (await c.get("/api/messages", params={"limit": 500})).json()]
        assert contents[-1] == "네" and not any("NO_REPLY" in x for x in contents)
        assert (await c.get("/api/conversation/summary")).json()["memory_flushes"] == 0


async def test_scheduled_runs_read_the_summary_but_do_not_start_a_fold_afterwards(tmp_path):
    fake = Fake()
    async with Running(create_app(app_settings(tmp_path, scheduler=False), tools=[], stream_fn=fake)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        store = Store(str(tmp_path / "a.db"))
        ids = seed(store, 10)
        assert store.save_conversation_summary("예전 요약", ids[5], 2, expect_up_to=0)
        made = await c.post("/api/schedules", json={"name": "시세", "prompt": "중고 자전거 시세 찾아줘", "cron": "0 9 * * 1"})
        assert made.status_code == 201, made.text
        run = (await c.post(f"/api/schedules/{made.json()['id']}/run")).json()
        await follow(c, run["turn_id"])
        sent = fake.of("turn")[0].messages
        assert sent[5]["content"] == f"{CONVERSATION_SUMMARY_HEAD} 예전 요약" and "중고 자전거" in sent[-1]["content"]
        assert [m["content"] for m in sent[6:-1]] == [msg_text(i) for i in range(6, 10)]     # after the summary's last message
        await asyncio.sleep(0.05)
        assert fake.of("summary") == []


# ------------------------------------------------------------------------------------------------------ absolute dates in a fold


def stamped(store: Store, ids: list[int], utc: str) -> None:
    from datetime import datetime, timezone
    t = datetime.fromisoformat(utc).replace(tzinfo=timezone.utc).timestamp()
    for n, i in enumerate(ids):
        store._run("UPDATE messages SET created_at = ? WHERE id = ?", (t + 60 * n, i))


async def test_a_fold_input_carries_each_messages_send_time_in_the_profile_timezone_and_the_prompt_asks_for_absolute_dates():
    store, fake = Store(), Fake(summaries=["요약 A"])
    store.set_profile(timezone="Asia/Seoul")
    ids = seed(store, 40)
    stamped(store, ids, "2026-11-17T06:00:00")             # 15:00 in Seoul, one minute apart
    res = await Compactor(store, fake).compact("p", "m", W, 500)
    assert res["status"] == "done"
    body = fake.of("summary")[0].messages[1]["content"]
    assert "[2026-11-17 15:04] User: m004" in body and "[2026-11-17 15:05] Assistant: m005" in body     # m004 is the first folded one
    assert "absolute date and time (for example 2026-11-17 15:00), never 'today', 'tomorrow' or 'next week'" in system_of(fake.of("summary")[0])
    assert "work them out from the time each message was sent" in compaction.SUMMARY_PROMPT


def test_a_fold_line_without_a_timezone_uses_utc_and_one_without_a_time_has_no_stamp():
    from assistant.compaction import _line
    assert _line({"role": "user", "content": "hi", "created_at": 0}) == "[1970-01-01 00:00] User: hi"
    assert _line({"role": "assistant", "content": "ok"}) == "Assistant: ok"
