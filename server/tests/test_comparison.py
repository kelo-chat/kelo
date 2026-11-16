import asyncio
import json
from pathlib import Path

import httpx
import pytest

from assistant.comparison import ComparisonError, Comparisons, clean, comparison_tools
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.store import Store

from test_server import AUTH, Running, settings


def test_clean_validations():
    # 1 option -> error
    with pytest.raises(ComparisonError, match="2-4 options"):
        clean({"rows": ["Price"], "options": [{"name": "A", "values": ["10"]}]})

    # no rows -> error
    with pytest.raises(ComparisonError, match="1-6 short labels"):
        clean({"rows": [], "options": [{"name": "A", "values": ["10"]}, {"name": "B", "values": ["20"]}]})

    # 5 options -> 4 kept
    res = clean({"rows": ["Price"], "options": [{"name": f"Opt{i}", "values": [str(i)]} for i in range(5)]})
    assert len(res["options"]) == 4
    assert [o["name"] for o in res["options"]] == ["Opt0", "Opt1", "Opt2", "Opt3"]

    # values padded/cut to the row count
    res = clean({"rows": ["R1", "R2", "R3"], "options": [
        {"name": "A", "values": ["v1"]},
        {"name": "B", "values": ["v1", "v2", "v3", "v4"]},
    ]})
    assert res["options"][0]["values"] == ["v1", "", ""]
    assert res["options"][1]["values"] == ["v1", "v2", "v3"]

    # two "recommended" -> only the first stays
    res = clean({"rows": ["R1"], "options": [
        {"name": "A", "values": ["v1"], "recommended": True},
        {"name": "B", "values": ["v2"], "recommended": True},
    ]})
    assert res["options"][0]["recommended"] is True
    assert res["options"][1]["recommended"] is False

    # option without name -> error
    with pytest.raises(ComparisonError, match="needs a name"):
        clean({"rows": ["R1"], "options": [{"name": "", "values": ["v1"]}, {"name": "B", "values": ["v2"]}]})


def test_comparisons_attach_and_for_messages():
    store = Store()
    comp = Comparisons(store)
    card = clean({
        "title": "Compare",
        "basis": "Basis",
        "rows": ["Cost"],
        "options": [
            {"name": "A", "values": ["100"], "says": "Choose A", "recommended": True},
            {"name": "B", "values": ["200"], "says": "Choose B"}
        ],
        "note": "Check first"
    })
    store.add_message("user", "Compare these for me")
    a1 = store.add_message("assistant", "Here is the comparison")
    comp.attach(a1, card)

    # Before newer user message: open is True
    messages = comp.for_messages(store.messages())
    a_msg = next(m for m in messages if m["id"] == a1)
    assert "comparison" in a_msg
    assert a_msg["comparison"]["open"] is True
    assert a_msg["comparison"]["title"] == "Compare"
    assert a_msg["comparison"]["options"][0]["name"] == "A"

    # After newer user message: open is False
    store.add_message("user", "Thanks")
    messages2 = comp.for_messages(store.messages())
    a_msg2 = next(m for m in messages2 if m["id"] == a1)
    assert a_msg2["comparison"]["open"] is False


def test_comparison_tool():
    pending = {}
    curr_turn = None
    emitted = []

    async def fake_emit_ui(item):
        emitted.append(item)

    tools = comparison_tools(pending, lambda: curr_turn, fake_emit_ui)
    tool = tools[0]
    assert tool.name == "compare_options"
    assert not getattr(tool, "closes_reply", False)

    # Outside a chat turn -> error text
    res = asyncio.run(tool.handler({"rows": ["Cost"], "options": [{"name": "A", "values": ["10"]}, {"name": "B", "values": ["20"]}]}))
    assert "Error: a comparison card can only go with a chat reply." in res

    # Inside a chat turn -> stored in pending["t1"]
    curr_turn = {"turn_id": "t1"}
    res2 = asyncio.run(tool.handler({"rows": ["Cost"], "options": [{"name": "A", "values": ["10"]}, {"name": "B", "values": ["20"]}]}))
    assert "Error" not in res2
    assert "t1" in pending
    assert pending["t1"]["rows"] == ["Cost"]
    assert len(pending["t1"]["options"]) == 2
    assert emitted == [{"type": "comparison"}]


async def test_comparison_e2e_with_app(tmp_path):
    async def wait_for(cond, tries=200):
        for _ in range(tries):
            v = cond()
            if asyncio.iscoroutine(v):
                v = await v
            if v:
                return v
            await asyncio.sleep(0.02)
        raise AssertionError("timed out")

    seen = []

    async def model_fn(provider, request, **kw):
        seen.append(request)
        last = request.messages[-1]
        if len(seen) == 1:
            yield ToolCall("c1", "compare_options", json.dumps({
                "title": "Internet Plans",
                "basis": "3-year contract",
                "rows": ["Monthly", "Gift"],
                "options": [
                    {"name": "KT", "values": ["38,500", "450,000"], "says": "Go with KT", "recommended": True},
                    {"name": "SK", "values": ["37,400", "400,000"], "says": "Go with SK", "recommended": False}
                ],
                "note": "Check phone bundle"
            }))
            yield Done("tool_calls")
        elif last["role"] == "tool":
            yield TextDelta("Here is the comparison between KT and SK.")
            yield Done("stop")
        else:
            yield TextDelta("ok")
            yield Done("stop")

    app = create_app(settings(db_path=str(tmp_path / "c.db")), tools=[], stream_fn=model_fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/turns", json={"text": "compare KT and SK"})
        assert r.status_code == 200

        async def stored():
            ms = (await c.get("/api/messages")).json()
            return ms if ms and any("comparison" in m for m in ms) else None

        ms = await wait_for(stored)
        assistant_msg = next(m for m in ms if m["role"] == "assistant")
        assert "comparison" in assistant_msg
        card = assistant_msg["comparison"]
        assert card["title"] == "Internet Plans"
        assert card["options"][0]["name"] == "KT"
        assert card["options"][0]["recommended"] is True
        assert card["open"] is True
