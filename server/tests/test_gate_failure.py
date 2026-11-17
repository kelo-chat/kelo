"""A permission gate that raises does not let a call through unasked (agent/loop.py): the owner gets a card no rule or grant skips,
and a call the owner had turned off is still refused by the gate when it works."""

import json

from assistant.agent import Tool, ToolResult, run_agent
from assistant.agent.loop import GateBroken
from assistant.providers import Done, TextDelta, ToolCall


class BrokenGate:
    async def check(self, tool, args):
        raise ValueError("a malformed argument")

    def hides(self, name):
        return False

    def hidden_key(self):
        return (0,)


def scripted(*calls):
    it = iter(calls)

    async def model(provider, request, **kw):
        nxt = next(it, None)
        if nxt is None:
            yield TextDelta("done")
            yield Done("stop")
            return
        yield ToolCall("c1", nxt[0], json.dumps(nxt[1]))
        yield Done("tool_calls")
    return model


def tool(name, ran, *, requires_approval):
    async def handler(args):
        ran.append(args)
        return "done"
    return Tool(name, name, {"type": "object"}, handler, requires_approval=requires_approval)


async def run(tools, gate, approve, *calls):
    return [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "go"}], tools, approve=approve, stream_fn=scripted(*calls),
                                          gate=gate) if isinstance(ev, ToolResult)]


def test_a_broken_gate_answers_ask_with_the_shape_of_a_verdict():
    v = GateBroken()
    assert (v.action, v.message, v.card) == ("ask", "", "")


async def test_a_read_tool_is_asked_when_the_gate_raises():
    ran, asked = [], []

    async def approve(req):
        asked.append(req)
        return False
    tools = [tool("file_read", ran, requires_approval=False)]
    results = await run(tools, BrokenGate(), approve, ("file_read", {"path": "x"}))
    assert len(asked) == 1 and asked[0].force is True and asked[0].name == "file_read"
    assert ran == [] and results[0].approved is False


async def test_a_broken_gate_runs_the_call_only_after_the_owner_says_yes():
    ran, asked = [], []

    async def approve(req):
        asked.append(req)
        return True
    tools = [tool("email_send", ran, requires_approval=True)]
    results = await run(tools, BrokenGate(), approve, ("email_send", {"to": "a@b.c"}))
    assert len(asked) == 1 and ran == [{"to": "a@b.c"}] and results[0].approved is True
