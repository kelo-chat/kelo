"""Replay of real (redacted) conversations (tests/evals/golden; made by `assistant eval export`, reviewed by hand).

Each recorded tool call is checked against today's tool list (the real app's, built without running a turn): the tool must still exist
and the arguments must match its parameters (required keys, types). Then the reply is replayed through the app with a scripted model and
fake tools, so no real tool runs; the replay must finish without an error event. The replay of a hand-made sample checks the same logic
without any real thread. Skips when the golden folder is empty."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant.server import Settings, create_app
from evals.runner import TOKEN, run_scenario

GOLDEN = Path(__file__).parent / "evals" / "golden"
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def problems(args, schema) -> list[str]:
    """What is wrong with `args` for a tool's parameter schema (required keys and the declared types of top-level fields)."""
    if not isinstance(args, dict):
        return ["arguments are not an object"]
    bad = [f"missing {key!r}" for key in schema.get("required", []) if key not in args]
    for key, spec in schema.get("properties", {}).items():
        if key in args and "type" in spec and spec["type"] in TYPES:
            value = args[key]
            if spec["type"] in ("integer", "number") and isinstance(value, bool):
                bad.append(f"{key!r} is a boolean")
            elif not isinstance(value, TYPES[spec["type"]]):
                bad.append(f"{key!r} should be {spec['type']}")
    return bad


def today_tools(tmp_path: Path) -> dict:
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "schema.db")),
                     stream_fn=lambda *a, **k: None)
    return {t.name: t for t in app.state.tools}


def replay_steps(record: dict) -> list:
    steps = []
    for ev in record["events"]:
        if ev.get("type") == "tool_started":
            args = ev.get("arguments", {})
            steps.append([{"tool": ev["name"], "args": json.loads(args) if isinstance(args, str) else args}])
    steps.append([{"text": record.get("reply") or "(replayed)"}])
    return steps


def check_thread(record: dict, tools: dict) -> list[str]:
    bad = []
    for ev in record["events"]:
        if ev.get("type") != "tool_started":
            continue
        tool = tools.get(ev["name"])
        if tool is None:
            bad.append(f"tool {ev['name']!r} no longer exists")
            continue
        args = ev.get("arguments", {})
        args = json.loads(args) if isinstance(args, str) else args
        bad += [f"{ev['name']}: {p}" for p in problems(args, tool.parameters)]
    return bad


async def replay(record: dict, tmp_path: Path) -> list[str]:
    tools = {name: {"result": "replayed"} for name in {e["name"] for e in record["events"] if e.get("type") == "tool_started"}}
    sc = {"name": "replay", "user": [record["user"]], "model": replay_steps(record), "tools": tools, "expect": {}}
    outcome = await run_scenario(sc, tmp_path)
    bad = [] if outcome.statuses == [200] else [f"turn start answered {outcome.statuses}"]
    if any(e.get("type") == "error" for e in outcome.events):
        bad.append("the replay ended with an error event")
    if outcome.extra_model_calls:
        bad.append("the replay asked the model more than was recorded")
    return bad


def golden_files() -> list[Path]:
    return sorted(GOLDEN.glob("*.json"))


@pytest.mark.skipif(not golden_files(), reason="no reviewed golden threads in tests/evals/golden yet")
@pytest.mark.parametrize("path", golden_files(), ids=lambda p: p.stem)
async def test_golden_thread_still_replays(path, tmp_path):
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record.get("format") == 1
    assert check_thread(record, today_tools(tmp_path / "schema")) == []
    assert await replay(record, tmp_path / "run") == []


SAMPLE = {"format": 1, "turn_id": "sample-1", "status": "done", "day": "2026-11-01", "user": "Search for phone cases",
          "events": [{"type": "tool_started", "name": "web_search", "arguments": {"query": "phone case"}},
                     {"type": "tool_result", "name": "web_search", "is_error": False, "output": "3 results"},
                     {"type": "text", "text": "I found 3 results."}],
          "reply": "I found 3 results."}


async def test_the_replay_of_a_hand_made_sample_passes(tmp_path):
    record = json.loads(json.dumps(SAMPLE))
    assert check_thread(record, today_tools(tmp_path / "schema")) == []   # the real schema of today's web_search
    assert await replay(record, tmp_path / "run") == []


def test_a_removed_tool_or_a_changed_argument_is_reported():
    record = json.loads(json.dumps(SAMPLE))
    schema = {"type": "object", "required": ["query", "count"], "properties": {"query": {"type": "string"}, "count": {"type": "integer"}}}
    tool = SimpleNamespace(parameters=schema)
    assert check_thread(record, {}) == ["tool 'web_search' no longer exists"]
    assert check_thread(record, {"web_search": tool}) == ["web_search: missing 'count'"]
    record["events"][0]["arguments"] = {"query": 3, "count": True}
    assert check_thread(record, {"web_search": tool}) == ["web_search: 'query' should be string", "web_search: 'count' is a boolean"]
