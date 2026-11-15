"""No provider gets a system message after the conversation started (providers/types.late_notes_as_user).

A real turn on the Claude subscription failed with "System/developer messages must precede conversation history": the agent loop
adds notes ("steps are running out", "answer now", "your tool call came back as text") mid-history. Every wire is checked here with
such a note, and the agent loop end to end through the real stream_chat."""

import importlib
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from assistant.agent.loop import FINISH_NUDGE, LIMIT_NOTE, Tool, run_agent
from assistant.providers import ChatRequest, custom_provider, stream_chat
from assistant.providers import cli_agents
from assistant.providers.types import LATE_NOTE, late_notes_as_user

MID = [{"role": "system", "content": "rules"}, {"role": "user", "content": "buy it"},
       {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                                                               "function": {"name": "look", "arguments": "{}"}}]},
       {"role": "tool", "tool_call_id": "c1", "content": "page"},
       {"role": "system", "content": FINISH_NUDGE}]


def no_late_system(messages):
    talk = next((i for i, m in enumerate(messages) if m.get("role") not in ("system", "developer")), len(messages))
    return not any(m.get("role") in ("system", "developer") for m in messages[talk:])


def test_the_helper_keeps_the_leading_system_text_and_tags_later_notes():
    out = late_notes_as_user(MID)
    assert out[0] == {"role": "system", "content": "rules"} and no_late_system(out)
    assert out[-1] == {"role": "user", "content": LATE_NOTE + FINISH_NUDGE}
    tagged = late_notes_as_user([*MID[:4], {"role": "system", "content": LIMIT_NOTE}])
    assert tagged[-1]["content"] == LIMIT_NOTE   # already says it is not from the user: no second tag
    lead = [{"role": "system", "content": "a"}, {"role": "system", "content": "summary"}, {"role": "user", "content": "x"}]
    assert late_notes_as_user(lead) is lead   # nothing late: the same list
    parts = late_notes_as_user([{"role": "user", "content": "x"}, {"role": "developer", "content": [{"type": "text", "text": "n"}]}])
    assert parts[-1]["role"] == "user" and parts[-1]["content"][1] == {"type": "text", "text": "n"}


def sse(*events):
    out = []
    for name, data in events:
        if name:
            out.append(f"event: {name}")
        out.append("data: " + (data if isinstance(data, str) else json.dumps(data)))
        out.append("")
    return ("\n".join(out) + "\n").encode()


def recorder(seen, body):
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_compatible_wire_gets_the_note_as_user_text():
    seen = []
    await_ = [e async for e in stream_chat(custom_provider("http://localhost:1/v1"), ChatRequest(model="m", messages=MID),
                                           client=recorder(seen, sse((None, "[DONE]"))))]
    assert await_ is not None
    msgs = seen[0]["messages"]
    assert msgs[0]["role"] == "system" and no_late_system(msgs) and msgs[-1]["content"].startswith("[Not from the user")


async def test_responses_wire_keeps_the_note_in_place_not_in_the_instructions():
    seen = []
    [e async for e in stream_chat("xai", ChatRequest(model="m", messages=MID), api_key="k",
                                  client=recorder(seen, sse(("response.completed", {"response": {"usage": {}}}))))]
    assert seen[0]["instructions"] == "rules"
    last = seen[0]["input"][-1]
    assert last["role"] == "user" and FINISH_NUDGE in last["content"]


async def test_anthropic_wire_has_only_the_leading_system_text():
    seen = []
    body = sse(("message_stop", {"type": "message_stop"}))
    [e async for e in stream_chat("anthropic", ChatRequest(model="claude-x", messages=MID), api_key="k", client=recorder(seen, body))]
    assert seen[0]["system"][0]["text"] == "rules"
    last = seen[0]["messages"][-1]
    assert last["role"] == "user" and last["content"][0]["type"] == "tool_result"
    assert FINISH_NUDGE in json.dumps(last["content"][1:])


async def test_bedrock_wire_has_only_the_leading_system_text():
    class Fake:
        kwargs = None

        def converse_stream(self, **kw):
            Fake.kwargs = kw
            return {"stream": iter([{"messageStop": {"stopReason": "end_turn"}}])}
    [e async for e in stream_chat("bedrock", ChatRequest(model="anthropic.claude-x", messages=MID), aws_client=Fake())]
    assert Fake.kwargs["system"][0] == {"text": "rules"}
    assert FINISH_NUDGE in json.dumps(Fake.kwargs["messages"][-1])


class FakeCli:
    def __init__(self):
        self.kwargs = None
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.kwargs = kw
        return iter([NS(choices=[NS(delta=NS(content="ok"), finish_reason="stop")], usage=None)])

    def close(self):
        pass


def directsdk():
    cli_agents._install_shims()
    return importlib.import_module("assistant.providers.vendor.claude_directsdk.directsdk")


def test_the_vendored_claude_plugin_refuses_a_late_system_message():
    # the plugin itself is unmodified: the fix is that it never gets one
    with pytest.raises(ValueError, match="must precede"):
        directsdk().prepare_history(MID)


@pytest.mark.parametrize("provider", [cli_agents.CLAUDE, cli_agents.AGY])
async def test_cli_wires_never_get_a_late_system_message(monkeypatch, provider):
    fake = FakeCli()
    monkeypatch.setitem(cli_agents._clients, provider, fake)
    [e async for e in stream_chat(provider, ChatRequest(model="sonnet", messages=MID))]
    assert no_late_system(fake.kwargs["messages"])
    if provider == cli_agents.CLAUDE:
        system, frames = directsdk().prepare_history(fake.kwargs["messages"])   # what failed on the main server
        assert system == "rules" and FINISH_NUDGE in json.dumps(frames[-1]["message"]["content"])


async def test_the_sidecar_path_fixes_the_request_too(monkeypatch):
    # cli_runner calls cli_agents.stream directly: the request may come from a server that did not convert it
    fake = FakeCli()
    monkeypatch.setitem(cli_agents._clients, cli_agents.CLAUDE, fake)
    [e async for e in cli_agents.stream(cli_agents.CLAUDE, ChatRequest(model="sonnet", messages=MID))]
    directsdk().prepare_history(fake.kwargs["messages"])


async def test_agent_loop_notes_reach_the_claude_plugin_as_user_text(monkeypatch):
    """The failing run, end to end: a tool step, the step budget runs out (LIMIT_NOTE), the plugin parses every request."""
    class Scripted(FakeCli):
        def __init__(self):
            super().__init__()
            self.parsed = []

        def create(self, **kw):
            self.parsed.append(directsdk().prepare_history(kw["messages"]))   # raises exactly like the real plugin
            n = len(self.parsed)
            if n == 1:
                tc = NS(index=0, id="c1", function=NS(name="look", arguments="{}"))
                return iter([NS(choices=[NS(delta=NS(content=None, tool_calls=[tc]), finish_reason="tool_calls")], usage=None)])
            return iter([NS(choices=[NS(delta=NS(content="Done so far: 1 of 2."), finish_reason="stop")], usage=None)])

    fake = Scripted()
    monkeypatch.setitem(cli_agents._clients, cli_agents.CLAUDE, fake)

    async def look(_):
        return "page text"
    tools = [Tool("look", "look", {"type": "object", "properties": {}}, look)]
    events = [e async for e in run_agent(cli_agents.CLAUDE, "sonnet", [{"role": "system", "content": "rules"},
                                                                       {"role": "user", "content": "buy it"}], tools, max_steps=1)]
    assert len(fake.parsed) == 2
    assert "step budget is used up" in json.dumps(fake.parsed[1][1][-1]["message"]["content"])
    assert type(events[-1]).__name__ == "AgentDone"
