"""Tool calls a model writes into its reply text (agent/textcalls.py, used by agent/loop.py for every provider)."""

import json

import pytest

from assistant.agent import AgentDone, AssistantText, Tool, ToolStarted, run_agent
from assistant.agent.loop import TEXT_CALL_NUDGE
from assistant.agent.textcalls import TextCallFilter, read_blocks, strip_text_calls
from assistant.providers import Done, TextDelta

# seen on the owner's phone (Gemini 3.8 Flash Medium through agy): the JSON even ends in `" function}}`
PHONE_SAMPLE = ('<tool_call>{"id": "call_add_service_key_telegram", "type": "function", "function": {"name": "add_service_key", '
                '"arguments": "{\\"name\\": \\"telegram\\", ...}" function}}</tool_call>')
# the same damage, with arguments that can be read
MENDABLE = ('<tool_call>{"id": "call_add_service_key_telegram", "type": "function", "function": {"name": "add_service_key", '
            '"arguments": "{\\"name\\": \\"telegram\\"}" function}}</tool_call>')
# the full reply stored on the main server (2026-11-09 03:55 KST, Gemini 3.8 Flash through agy, after "지금보낼게"): the arguments string
# is whole and only ` function}}` is stray after it, so the call is recovered as it was meant
REAL_REPLY = ('<tool_call>{"id": "call_add_service_key_telegram", "type": "function", "function": {"name": "add_service_key", '
              '"arguments": "{\\"name\\": \\"telegram\\", \\"label\\": \\"텔레그램 봇 토큰\\", \\"hosts\\": [\\"api.telegram.org\\"], '
              '\\"auth\\": {\\"type\\": \\"apiKey\\", \\"in\\": \\"path\\", \\"name\\": \\"token\\"}}" function}}</tool_call>')
REAL_ARGS = {"name": "telegram", "label": "텔레그램 봇 토큰", "hosts": ["api.telegram.org"], "auth": {"type": "apiKey", "in": "path", "name": "token"}}
WELL_FORMED = '<tool_call>{"id": "c9", "type": "function", "function": {"name": "echo", "arguments": "{\\"x\\": \\"A\\"}"}}</tool_call>'
KNOWN = frozenset({"add_service_key", "echo", "web_search"})


def through(text, size=1):
    f = TextCallFilter()
    shown = "".join(f.feed(text[i:i + size]) for i in range(0, len(text), size)) + f.finish()
    return shown, read_blocks(f.blocks, KNOWN)


@pytest.mark.parametrize("size", [1, 3, 7, 1000])
def test_the_phone_sample_is_never_shown_and_cannot_be_read(size):
    shown, read = through("I'll add it.\n" + PHONE_SAMPLE, size)
    assert shown == "I'll add it.\n" and not read.calls and read.unreadable == 1   # "..." is not JSON: no arguments are made up


@pytest.mark.parametrize("size", [1, 5, 1000])
def test_light_damage_is_mended_and_well_formed_blocks_are_read(size):
    shown, read = through(MENDABLE + "\n" + WELL_FORMED, size)
    assert shown.strip() == ""
    assert read.calls == [("call_add_service_key_telegram", "add_service_key", '{"name": "telegram"}'), ("c9", "echo", '{"x": "A"}')]


@pytest.mark.parametrize("size", [1, 7, 1000])
def test_the_real_stored_reply_is_recovered_as_its_call_and_never_shown(size):
    shown, read = through(REAL_REPLY, size)
    assert shown == "" and read.unreadable == 0 and not read.unknown
    assert [(i, n, json.loads(a)) for i, n, a in read.calls] == [("call_add_service_key_telegram", "add_service_key", REAL_ARGS)]
    assert strip_text_calls("알겠어요.\n" + REAL_REPLY) == "알겠어요."


@pytest.mark.parametrize("text, call", [
    ('<function_call>{"name": "web_search", "arguments": {"q": "x"}}</function_call>', ("web_search", {"q": "x"})),
    ('<function=web_search>{"q": "x"}</function>', ("web_search", {"q": "x"})),
    ('```json\n{"name": "web_search", "arguments": {"q": "x"}}\n```', ("web_search", {"q": "x"})),
    ('<tool_call>\n{"name": "web_search", "parameters": {"q": "x"},}\n', ("web_search", {"q": "x"})),   # no closing tag, trailing comma
    ('<tool_use>{"tool": "web_search", "input": {"q": "x"}}</tool_use>', ("web_search", {"q": "x"})),
])
def test_other_wrappers_models_use(text, call):
    shown, read = through("Looking. " + text, 2)
    assert shown == "Looking. " and [(n, json.loads(a)) for _, n, a in read.calls] == [call]


@pytest.mark.parametrize("text", [
    "The tool_call format is described in the docs; a function_call is similar.",
    "a < b and c > d, use <b>bold</b> or <br>.",
    "I write <tool_call> tags when I call a tool.",
    "Here is JSON you asked for:\n```json\n{\"a\": 1, \"b\": [2, 3]}\n```\nDone.",
    "```python\nprint('<tool_call>')\n```\nThat prints a tag.",
    "Inline `code` and ``more`` stay.",
])
def test_ordinary_text_is_shown_unchanged(text):
    for size in (1, 4, 1000):
        shown, read = through(text, size)
        assert shown == text and not read.calls and not read.unreadable


def test_an_unknown_tool_is_not_run_and_not_shown():
    shown, read = through('<tool_call>{"name": "rm_rf", "arguments": {}}</tool_call>')
    assert shown == "" and read.unknown == ["rm_rf"] and not read.calls


def test_stored_text_loses_the_markup():
    assert strip_text_calls("Okay.\n\n" + PHONE_SAMPLE + "\n\nDone.") == "Okay.\n\nDone."
    assert strip_text_calls("plain <b>text</b>") == "plain <b>text</b>"


# ---------------------------------------------------------------- in the agent loop, for any provider

def scripted(*turns):
    seen = []
    it = iter(turns)

    async def fn(provider, request, **kw):
        seen.append(request)
        for ev in next(it):
            yield ev
    fn.seen = seen
    return fn


def echo_tool():
    async def handler(args):
        return f"echo:{args.get('x')}"
    return Tool("echo", "echo x", {"type": "object", "properties": {"x": {"type": "string"}}}, handler)


def chunks(text, size=4):
    return [TextDelta(text[i:i + size]) for i in range(0, len(text), size)]


async def run(fn, messages=None):
    msgs = messages or [{"role": "user", "content": "do it"}]
    return [e async for e in run_agent("p", "m", msgs, [echo_tool()], stream_fn=fn)]


def said(events):
    return "".join(e.text for e in events if isinstance(e, AssistantText))


async def test_a_call_written_as_text_runs_as_a_real_call_and_the_markup_is_never_shown():
    fn = scripted([*chunks("On it.\n" + WELL_FORMED), Done("stop")], [TextDelta("echo said A"), Done("stop")])
    events = await run(fn)
    started = [e for e in events if isinstance(e, ToolStarted)]
    assert [(e.name, e.arguments) for e in started] == [("echo", {"x": "A"})]
    assert "<tool_call" not in said(events) and said(events).startswith("On it.")
    done = events[-1]
    assert isinstance(done, AgentDone) and all("<tool_call" not in str(m.get("content")) for m in done.new_messages)
    assert fn.seen[1].messages[1]["tool_calls"][0]["function"]["name"] == "echo"


async def test_the_real_stored_reply_runs_its_tool_in_the_loop_and_nothing_raw_is_shown_or_stored():
    got = []

    async def handler(args):
        got.append(args)
        return "saved"
    tool = Tool("add_service_key", "add a key", {"type": "object", "properties": {}}, handler)
    fn = scripted([*chunks(REAL_REPLY), Done("stop")], [TextDelta("등록했어요"), Done("stop")])
    events = [e async for e in run_agent("p", "m", [{"role": "user", "content": "지금보낼게"}], [tool], stream_fn=fn)]
    assert got == [REAL_ARGS] and said(events) == "등록했어요"
    done = events[-1]
    assert isinstance(done, AgentDone) and all("<tool_call" not in str(m.get("content")) for m in done.new_messages)


async def test_an_unreadable_call_is_hidden_and_the_model_is_told_once_then_it_retries():
    fn = scripted([*chunks(PHONE_SAMPLE), Done("stop")], [*chunks(WELL_FORMED), Done("stop")], [TextDelta("done"), Done("stop")])
    events = await run(fn)
    assert "<tool_call" not in said(events) and said(events) == "done"
    assert any(m.get("content", "").startswith(TEXT_CALL_NUDGE) for m in fn.seen[1].messages if isinstance(m.get("content"), str))
    assert [e.name for e in events if isinstance(e, ToolStarted)] == ["echo"]


async def test_a_second_unreadable_call_ends_with_a_short_error_not_markup():
    fn = scripted([*chunks(PHONE_SAMPLE), Done("stop")], [*chunks(PHONE_SAMPLE), Done("stop")])
    events = await run(fn)
    text = said(events)
    assert "<tool_call" not in text and "could not be read" in text and len(fn.seen) == 2   # told once, not twice
    done = events[-1]
    assert isinstance(done, AgentDone) and all("<tool_call" not in str(m.get("content")) for m in done.new_messages)


async def test_markup_in_earlier_replies_is_not_sent_to_the_model_again():
    fn = scripted([TextDelta("ok"), Done("stop")])
    history = [{"role": "user", "content": "add telegram"}, {"role": "assistant", "content": "Sure.\n" + PHONE_SAMPLE},
               {"role": "user", "content": "and?"}]
    await run(fn, history)
    assert fn.seen[0].messages[1] == {"role": "assistant", "content": "Sure."} and history[1]["content"].endswith("</tool_call>")



@pytest.mark.parametrize("size", [1, 4, 1000])
def test_parts_split_where_a_block_was_taken_out(size):
    """feed_parts: the words before and after a call written into the text come apart (agent/loop.py makes them two paragraphs)."""
    text = "Asking now." + WELL_FORMED + "Waiting. " + '```json\n{"a": 1}\n```'
    f = TextCallFilter()
    calls = [f.feed_parts(text[i:i + size]) for i in range(0, len(text), size)] + [f.finish_parts()]
    assert "".join(p for parts in calls for p in parts) == 'Asking now.Waiting. ```json\n{"a": 1}\n```'   # an ordinary block stays
    assert sum(len(parts) - 1 for parts in calls) == 1 and len(f.blocks) == 1   # one cut, where the call was
    f = TextCallFilter()
    assert f.feed_parts("Asking now." + WELL_FORMED + "Waiting.") == ["Asking now.", "Waiting."]
    assert TextCallFilter().feed_parts("a\x00b") == ["ab"]   # the model's own text never makes a cut
