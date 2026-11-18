"""Declared situation reads (situation_reads.py): one code path for every connector and phone item, the owner's rules, budgets, snapshots.

The declarations are data next to each connector; these tests read them and check the rules the owner set on 2026-11-18: no code names a
connector, every built-in declaration parses and is a read, a connector without one is left out of the pack, only reads run.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from assistant import situation_reads as sr
from assistant.agent import Ours, Tool, ToolOutput
from assistant.effects import TOOL_FAMILY, Effect
from assistant.permissions import Verdict
from assistant.situation import Pack, SituationResearch
from assistant.situation_reads import Declared, SituationRead, SourceReader, fill, parse_read, project

SRC = Path(__file__).resolve().parents[1] / "src" / "assistant"


class FakeStore:
    """The two calls the reader makes: meta values (the snapshots) and set_meta."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def meta(self, key: str) -> str | None:
        return self.values.get(key)

    def set_meta(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeTool(Tool):
    """A read-only tool that records each call and answers with `answer` (a string, a ToolOutput or an exception to raise)."""

    def __init__(self, name: str, answer: Any = "[]"):
        self.calls: list[dict[str, Any]] = []
        self.answer = answer
        super().__init__(name, name, {"type": "object", "properties": {}}, self._run)

    async def _run(self, args: dict[str, Any]) -> Any:
        self.calls.append(dict(args))
        if isinstance(self.answer, BaseException):
            raise self.answer
        if callable(self.answer):
            return await self.answer(args)
        return self.answer


class FakeGate:
    def __init__(self, action: str = "pass") -> None:
        self.action, self.seen = action, []

    async def check(self, tool: Any, args: dict[str, Any]) -> Verdict:
        self.seen.append(getattr(tool, "name", tool))
        return Verdict(self.action)


class FakePermissions:
    def __init__(self, kind: str = "read") -> None:
        self.kind = kind

    def effect(self, tool: Any, args: dict[str, Any]) -> Effect:
        return Effect(self.kind, "some-service")


def reader_for(decl: list[Declared], tools: list[Any], *, store: FakeStore | None = None, gate: Any = None,
               permissions: Any = None, names: dict[str, str] | None = None) -> SourceReader:
    names = names or {}
    return SourceReader(declared=lambda: decl, tools=lambda: tools, gate=gate or FakeGate(), permissions=permissions or FakePermissions(),
                        names=lambda cid: names.get(cid, cid), store=store or FakeStore(), tz=lambda: __import__("zoneinfo").ZoneInfo("Asia/Seoul"),
                        clock=lambda: 0.0)


def read(tool: str = "connector_get", **kw: Any) -> SituationRead:
    raw = {"tool": tool, "arguments": {"name": "{{connection}}", "url": "/api/things"}, **kw}
    return parse_read(raw, where="test")


NOW = 1_800_000_000.0
SINCE = NOW - 3600


# ------------------------------------------------------------------------------------------------------------- the declaration
def test_parse_read_refuses_what_the_shape_does_not_allow():
    bad = [
        {"tool": "Not A Tool", "arguments": {}},
        {"tool": "connector_get", "arguments": {"url": "{{nope}}"}},
        {"tool": "connector_get", "mode": "changes", "arguments": {}},                  # changes needs id or lines
        {"tool": "connector_get", "arguments": {}, "max_rows": 101},
        {"tool": "connector_get", "arguments": {}, "max_chars": 50},
        {"tool": "connector_get", "arguments": {}, "every_minutes": 30},
        {"tool": "connector_get", "arguments": {}, "lines": "("},
        {"tool": "connector_get", "arguments": {}, "mode": "live"},
    ]
    for raw in bad:
        with pytest.raises(ValueError):
            parse_read(raw, where="x")


def test_fill_keeps_a_whole_binding_typed_and_encodes_it_inside_a_text():
    r = read(arguments={"name": "{{connection}}", "url": "/api/logbook/{{since}}?limit={{since_unix}}"}, mode="changes", id=["id"])
    values = {"connection": "ha-api", "since": "2026-11-18T01:00:00Z", "since_unix": 1800000000}
    args = fill(r, values)
    assert args["name"] == "ha-api"
    assert args["url"] == "/api/logbook/2026-11-18T01%3A00%3A00Z?limit=1800000000"


def test_project_takes_the_rows_at_the_select_path_with_fields_and_stable_ids():
    r = read(select="items", fields={"title": "subject.title", "when": "updated"}, id=["id"], mode="changes", max_rows=2)
    answer = json.dumps({"items": [{"id": 7, "subject": {"title": "A"}, "updated": "t1"}, {"id": 8, "subject": {"title": "B"}, "updated": "t2"},
                                  {"id": 9, "subject": {"title": "C"}, "updated": "t3"}]})
    got = project(r, answer)   # no fence marks here: the whole text is the answer
    assert got.rows == ["title: A · when: t1", "title: B · when: t2"]
    assert got.ids == ["7", "8"]
    assert got.more == 1


def test_project_of_a_text_answer_keeps_only_the_matching_lines_so_a_freshness_line_does_not_change_the_fingerprint():
    r = read(lines="^- ", mode="changes")
    a = project(r, "Last sync 10:00\n- item one\n- item two")
    b = project(r, "Last sync 10:07\n- item one\n- item two")
    assert a.rows == ["- item one", "- item two"]
    assert a.fingerprint == b.fingerprint


def test_a_failed_or_empty_answer_is_a_note_not_rows():
    assert project(read(select="items"), "").note
    assert project(read(select="items"), json.dumps({"other": []})).note


# ------------------------------------------------------------------------------------------------------------- one code path
@pytest.mark.asyncio
async def test_a_declared_read_of_every_kind_goes_through_one_path():
    answers = {"svc-api": {"items": [{"id": "a", "title": "Door open"}]}, "svc-device-api": {"items": [{"id": "b", "title": "Presence"}]}}

    async def by_connection(args: dict[str, Any]) -> str:
        return json.dumps(answers[args["name"]])
    keyed = device = FakeTool("connector_get", by_connection)   # both services use the one connector_get tool
    phone = FakeTool("phone_messages", "- 10:01 Kim: hi")
    decl = [
        Declared("svc_key", "svc_key", "", "svc-api", read(select="items", fields={"title": "title"}, id=["id"], mode="changes")),
        Declared("svc_device", "svc_device", "", "svc-device-api", read(select="items", fields={"title": "title"}, id=["id"], mode="changes")),
        Declared("phone/messages", "phone", "Text messages", "", parse_read({"tool": "phone_messages", "arguments": {"days": 1},
                                                                             "mode": "changes", "lines": "^- "}, where="p"), kind="phone"),
    ]
    tools = [keyed, phone]
    names = {"svc_key": "Service A", "svc_device": "Service B", "phone": "Phone"}
    rd = reader_for(decl, tools, names=names)
    secs = await rd.sections(NOW, SINCE)
    assert [s.sid for s in secs] == ["svc_key", "svc_device", "phone/messages"]   # the order given (declared() sorts by sid)
    assert keyed.calls == [{"name": "svc-api", "url": "/api/things"}, {"name": "svc-device-api", "url": "/api/things"}]
    assert phone.calls == [{"days": 1}]
    assert secs[0].title == "Service A" and secs[0].outcome == "new" and secs[0].rows == ["title: Door open"]
    assert secs[1].title == "Service B" and secs[1].rows == ["title: Presence"]
    assert secs[2].kind == "phone" and secs[2].title == "Phone · Text messages"


@pytest.mark.asyncio
async def test_only_reads_run_and_the_owners_off_or_ask_stops_a_read():
    tool = FakeTool("mcp_call_tool", "[]")
    decl = [Declared("svc", "svc", "", "svc-api", read("mcp_call_tool"))]
    rd = reader_for(decl, [tool], permissions=FakePermissions("change"))
    secs = await rd.sections(NOW, SINCE)
    assert tool.calls == [] and secs[0].outcome == "failed" and secs[0].note == "not_a_read"

    for action in ("off", "ask"):
        tool = FakeTool("connector_get", "[]")
        rd = reader_for([Declared("svc", "svc", "", "svc-api", read())], [tool], gate=FakeGate(action))
        secs = await rd.sections(NOW, SINCE)
        assert tool.calls == [] and secs[0].outcome == "off"


@pytest.mark.asyncio
async def test_one_source_failing_or_slow_never_stops_the_pass(monkeypatch):
    monkeypatch.setattr(sr, "READ_SECONDS", 0.05)

    async def slow(args: dict[str, Any]) -> str:
        await asyncio.sleep(1)
        return "[]"

    tools = [FakeTool("boom", RuntimeError("the service said: secret-token-123")), FakeTool("slow", slow),
             FakeTool("ours", Ours("error: could not reach the server")), FakeTool("works", json.dumps({"items": [{"id": 1, "title": "x"}]}))]
    decl = [Declared("a_boom", "a_boom", "", "i", read("boom", select="items", id=["id"], mode="changes")),
            Declared("b_slow", "b_slow", "", "i", read("slow", select="items", id=["id"], mode="changes")),
            Declared("c_ours", "c_ours", "", "i", read("ours", select="items", id=["id"], mode="changes")),
            Declared("d_works", "d_works", "", "i", read("works", select="items", fields={"title": "title"}, id=["id"], mode="changes"))]
    secs = await reader_for(decl, tools).sections(NOW, SINCE)
    outcomes = {s.sid: (s.outcome, s.note) for s in secs}
    assert outcomes["a_boom"] == ("failed", "RuntimeError")
    assert outcomes["b_slow"][0] == "timeout"
    assert outcomes["c_ours"] == ("failed", "error")
    assert outcomes["d_works"][0] == "new"
    assert "secret-token-123" not in repr([s.note for s in secs])


@pytest.mark.asyncio
async def test_state_unchanged_is_compact_and_is_not_a_change():
    store = FakeStore()
    tool = FakeTool("connector_get", json.dumps({"status": "up"}))
    decl = [Declared("svc", "svc", "", "i", read(fields={"status": "status"}))]
    first = reader_for(decl, [tool], store=store)
    secs = await first.sections(NOW, SINCE)
    first.commit()
    assert secs[0].outcome == "new" and not secs[0].compact
    second = reader_for(decl, [tool], store=store)
    secs2 = await second.sections(NOW + 3600, NOW)
    assert secs2[0].outcome == "same" and secs2[0].compact and secs2[0].rows == []
    assert secs2[0].since == secs[0].since                                # "No change since" the first time it was seen
    # the pack: a compact state section keeps the same fingerprint, so the pack digest does not move
    pack_a, pack_b = Pack(), Pack()
    _add(pack_a, secs[0])
    _add(pack_b, secs2[0])
    assert not Pack.changed(pack_b.snapshot(), pack_a.snapshot())


@pytest.mark.asyncio
async def test_changes_sources_count_only_new_ids_and_a_non_waking_one_never_starts_a_turn_alone():
    store = FakeStore()
    rows = [{"id": 1, "title": "old"}]
    tool = FakeTool("connector_get", lambda a: json.dumps({"items": rows}))

    async def answer(args: dict[str, Any]) -> str:
        return json.dumps({"items": rows})
    tool.answer = answer
    decl = [Declared("svc", "svc", "", "i", read(select="items", fields={"title": "title"}, id=["id"], mode="changes", wakes=False))]
    r1 = reader_for(decl, [tool], store=store)
    s1 = (await r1.sections(NOW, SINCE))[0]
    r1.commit()
    assert s1.outcome == "new" and s1.wakes is False
    rows.append({"id": 2, "title": "new"})
    s2 = (await reader_for(decl, [tool], store=store).sections(NOW + 3600, NOW))[0]
    assert s2.outcome == "changed" and s2.rows == ["title: new"] and s2.ids == ["1", "2"]
    pack = Pack()
    _add(pack, s2)
    assert all(s["hashed"] is False for s in pack.sections)                 # the change of a non-waking source is not hashed


@pytest.mark.asyncio
async def test_budgets_cap_the_rows_and_the_sources_past_the_pack_budget_are_left_out_in_the_same_order(monkeypatch):
    monkeypatch.setattr(sr, "SOURCES_CHARS", 300)
    big = json.dumps({"items": [{"id": i, "title": "x" * 40} for i in range(30)]})
    decl = [Declared(f"s{i}", f"s{i}", "", "i", read(select="items", fields={"title": "title"}, id=["id"], mode="changes", max_rows=5))
            for i in range(3)]
    secs = await reader_for(decl, [FakeTool("connector_get", big)]).sections(NOW, SINCE)
    assert [s.outcome for s in secs] == ["new", "skipped", "skipped"]
    assert len(secs[0].rows) == 5 and secs[0].note == "25 more not shown"
    again = await reader_for(decl, [FakeTool("connector_get", big)]).sections(NOW, SINCE)
    assert [s.outcome for s in again] == [s.outcome for s in secs]


@pytest.mark.asyncio
async def test_the_snapshot_is_saved_only_when_the_pass_got_through_and_holds_no_content():
    store = FakeStore()
    tool = FakeTool("connector_get", json.dumps({"items": [{"id": "secret-looking-id-42", "title": "private text"}]}))
    decl = [Declared("svc", "svc", "", "i", read(select="items", fields={"title": "title"}, id=["id"], mode="changes"))]
    rd = reader_for(decl, [tool], store=store)
    await rd.sections(NOW, SINCE)
    assert store.values == {}                                              # nothing saved until the pass is through
    rd.commit()
    saved = json.loads(store.values["situation:source:svc"])
    assert set(saved) == {"fp", "ids", "since", "read_at"}
    assert "private text" not in json.dumps(saved)


def test_every_declared_read_is_one_of_kelos_read_tools_and_none_reads_mail_or_calendar():
    from assistant.oauth_connect import PROVIDERS
    from assistant.phone_data import SITUATION
    from assistant.presets import KEYS
    from assistant.selfhosted import SITUATION_READS

    raws = [p.situation for p in KEYS.values() if p.situation is not None] + [p.situation for p in PROVIDERS.values() if p.situation]
    raws += list(SITUATION_READS.values()) + list(SITUATION.values())
    assert raws
    for raw in raws:
        r = parse_read(raw, where="builtin")
        family = TOOL_FAMILY.get(r.tool, ("", ()))[0]
        assert "mail" not in family and "calendar" not in family, r.tool
        assert not r.tool.startswith(("email_", "gcal_")) and r.tool not in ("calendar_events", "phone_calendar_events")


def test_every_builtin_definition_declares_a_read_or_says_why():
    from assistant.oauth_connect import PROVIDERS
    from assistant.presets import KEYS, NO_SITUATION

    for pid, preset in KEYS.items():
        assert preset.situation is not None or pid in NO_SITUATION, pid
    device_src = (SRC / "oauth_connect.py").read_text(encoding="utf-8")
    for pid, provider in PROVIDERS.items():
        if provider.situation is not None:
            continue
        before = device_src.split(f'"{pid}": DeviceProvider(')[0].rstrip().splitlines()[-1]
        assert "situation: none" in before, pid


def test_the_situation_code_names_no_connector():
    """The owner's rule as a test: the situation code reads the declarations and never names a connector, a connector id, a phone kind or
    a service's path (words in its strings and comparisons)."""
    from assistant import catalog, mcp_oauth, oauth_connect, phone_data, presets

    ids = {s.id for s in catalog.CATALOG} | set(presets.KEYS) | set(mcp_oauth.SERVICES) | set(oauth_connect.PROVIDERS) | set(phone_data.KINDS)
    # "messages" is also the conversation table's name and a plain word in the pack's text; the comparison check below still catches any
    # branch on a phone kind
    ids = {i for i in ids if len(i) >= 4 and i != "messages"}
    for name in ("situation.py", "situation_reads.py"):
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                words = set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", node.value))
                hit = sorted(words & ids)
                assert not hit, f"{name} names {hit}"
            if isinstance(node, ast.Compare) and isinstance(node.left, (ast.Name, ast.Attribute)):
                left = node.left.id if isinstance(node.left, ast.Name) else node.left.attr
                literal = [c for c in node.comparators if isinstance(c, ast.Constant) and c.value is not None]
                identity = all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops)
                if left in ("sid", "connector", "kind", "tool", "cid", "item") and literal and not identity:
                    raise AssertionError(f"{name}:{node.lineno} compares {left} to a literal")


def test_the_phone_items_are_declared_only_for_the_kinds_the_owner_shares():
    from assistant.phone_data import declared_items

    assert declared_items({"messages"}) and [d.sid for d in declared_items({"messages"})] == ["phone/messages"]
    assert declared_items(set()) == []
    assert [d.sid for d in declared_items({"health", "contacts"})] == ["phone/health"]   # contacts has no read


def test_a_connector_without_a_declaration_is_left_out_of_the_declared_list():
    from assistant.situation_reads import declared

    ways = {"asana": {"connect": "key", "via": "asana", "item": "asana-api", "connected": True},
            "home_assistant": {"connect": "key", "via": "home_assistant", "item": "ha-api", "connected": True},
            "frigate": {"connect": "key", "via": "frigate", "item": "frigate-api", "connected": False}}
    registry = SimpleNamespace(installed_connector=lambda via: None)
    got = declared(ways, registry)
    assert [(d.sid, d.item) for d in got] == [("home_assistant", "ha-api")]   # asana has no read; frigate is not connected


def _add(pack: Pack, s: Any) -> None:
    """The pack line build_pack adds for a declared read (SituationResearch._add_read, without the rest of the research)."""
    SituationResearch._add_read(SimpleNamespace(_local=lambda ts: "2026-11-18 09:00"), pack, s)


# ------------------------------------------------------------------------------------------------------------- Nest packages
def test_nest_manifest_situation_accepts_the_doc_examples_and_refuses_the_rest():
    from assistant import nest_manifest as nm
    from assistant.packages import PackageError

    key = {"type": "key", "base_url": "https://svc.example.org/api", "hosts": ["svc.example.org"]}
    mcp = {"type": "mcp", "url": "https://svc.example.org/mcp", "hosts": ["svc.example.org"]}
    req = nm.parse_situation({"request": {"method": "GET", "path": "/notifications?unread=true"}, "mode": "changes", "select": "items",
                              "fields": {"title": "subject"}, "id": "id", "max_rows": 20}, connect=key)
    assert req.tool == "connector_get"
    assert req.arguments == {"name": "{{connection}}", "url": "https://svc.example.org/api/notifications?unread=true"}
    tool = nm.parse_situation({"tool": "list_open_tickets", "arguments": {"limit": 20}, "mode": "state", "select": "tickets",
                               "fields": {"title": "title"}}, connect=mcp)
    assert tool.tool == "mcp_call_tool" and tool.arguments["tool"] == "list_open_tickets"
    refused = [({"request": {"method": "POST", "path": "/x"}}, key),
               ({"request": {"method": "GET", "path": "/x"}}, mcp),
               ({"tool": "list_items"}, key),
               ({"request": {"method": "GET", "path": "/x"}, "arguments": {}}, key),
               ({"request": {"method": "GET", "path": "/x/{{nope}}"}}, key),
               ({"request": {"method": "GET", "path": "/x"}, "mode": "changes"}, key),
               ({"request": {"method": "GET", "path": "/x"}, "max_rows": 500}, key)]
    for raw, conn in refused:
        with pytest.raises(PackageError):
            nm.parse_situation(raw, connect=conn)
    implied = nm.implied_permissions(mcp, {}, {}, None, situation=tool)
    assert "list_open_tickets" in implied["mcp_tools"]


def test_a_connector_draft_without_a_situation_read_is_warned_and_with_one_is_not(tmp_path):
    from assistant.nest_drafts import DraftManager
    from assistant.packages import Registry
    from assistant.store import Store

    drafts_dir = tmp_path / "package-drafts"
    reg = Registry(drafts=drafts_dir)
    mgr = DraftManager(drafts_dir=drafts_dir, registry=reg, store=Store(str(tmp_path / "store.db")))
    mgr.create("connector", "my_svc", "My Service", "Reads my service")
    reg.refresh()
    first = mgr.check("my_svc")
    assert any("no situation read" in w for w in first["warnings"]), first
    manifest = json.loads((drafts_dir / "my_svc" / "kelo-package.json").read_text(encoding="utf-8"))
    manifest["connector"]["situation"] = {"tool": "list_items", "arguments": {"limit": 20}, "mode": "changes", "id": ["id"],
                                          "select": "items", "fields": {"title": "title"}}
    mgr.write("my_svc", "kelo-package.json", json.dumps(manifest))
    reg.refresh()
    second = mgr.check("my_svc")
    assert second["valid"] and not any("no situation read" in w for w in second["warnings"]), second
