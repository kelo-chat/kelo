"""A step with several tool calls where one returns a picture: every tool result must follow the assistant's calls directly, and
the picture message comes after all of them (a message between two results made the next request fail: 2026-11-19, 'tool_use ids
were found without tool_result blocks immediately after')."""

import pytest

from assistant.agent import Tool, run_agent
from assistant.agent.loop import ToolOutput
from assistant.providers import Done, TextDelta, ToolCall

from test_toolbox import drain, scripted

PNG = "data:image/png;base64,iVBORw0KGgo="


def tools():
    async def see(args):
        return ToolOutput("a photo", images=[PNG])

    async def cal(args):
        return "no events"
    return [Tool("see_photo", "Sees a photo.", {"type": "object", "properties": {}}, see, read_only=True),
            Tool("calendar", "Reads the calendar.", {"type": "object", "properties": {}}, cal, read_only=True)]


@pytest.mark.parametrize("first", ["see_photo", "calendar"])
async def test_every_tool_result_follows_its_calls_and_the_picture_comes_after(first):
    second = "calendar" if first == "see_photo" else "see_photo"
    fn = scripted([ToolCall("c1", first, "{}"), ToolCall("c2", second, "{}"), Done("tool_calls")],
                  [TextDelta("done"), Done("stop")])
    await drain(run_agent("p", "m", [{"role": "user", "content": "look"}], tools(), stream_fn=fn, images_ok=True))
    msgs = fn.seen[1].messages
    i = next(n for n, m in enumerate(msgs) if m.get("role") == "assistant" and m.get("tool_calls"))
    assert [m.get("role") for m in msgs[i + 1:i + 3]] == ["tool", "tool"]
    assert {m["tool_call_id"] for m in msgs[i + 1:i + 3]} == {"c1", "c2"}
    pic = msgs[i + 3]
    assert pic["role"] == "user" and any(p.get("type") == "image_url" for p in pic["content"])
