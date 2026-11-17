"""Connector rules in data (connector_rules.py, data/connector_rules.json, docs/connector-rules.md): the shipped data is valid, a call is
matched by its declared shape, the recipients come from the declared place of the call, a declaration can only tighten, and the three
places that could skip a card (connector setting, per-recipient allow, task permission) respect the declared policy."""

import json
from pathlib import Path

import pytest

from assistant import connector_rules as cr
from assistant import effects
from assistant.catalog import CATALOG
from assistant.connector_rules import ConnectorRules, RulesError, parse_rules
from assistant.effects import effect_of
from assistant.packages import PackageError
from assistant.permissions import Info, Permissions
from assistant.schedule_grants import ConditionalGrants
from assistant.schedules import Schedules
from assistant.store import Store

from test_conditional_grants import in_scope, world
from test_data_trail import BODY, go, mail_read_tool, rig, send_tool
from test_permissions import fake_tools

SLACK_POST = {"name": "slack-api", "method": "POST", "url": "/chat.postMessage", "json": {"channel": "C0123", "text": "hi"}}


def test_the_shipped_data_is_valid_and_names_real_connectors():
    data = cr.load_builtin()
    ids = {s.id for s in CATALOG}
    assert set(data) == {"slack", "telegram", "discord"} and set(data) <= ids
    raw = json.loads(cr.DATA.read_text(encoding="utf-8"))
    assert json.dumps(raw, indent=1, sort_keys=True, ensure_ascii=False) + "\n" == cr.DATA.read_text(encoding="utf-8")   # sorted keys, indent 1
    assert all(m.policy in cr.POLICIES for ms in data.values() for m in ms)


def test_a_slack_send_is_bound_to_its_channel_and_the_policy_is_task():
    e = effect_of("connector_send", SLACK_POST)
    assert (e.kind, e.connector, e.policy, e.action) == ("send", "slack", "task", "connector_send")
    assert e.targets == ["connector:slack-api#c0123"]
    again = effect_of("connector_send", {**SLACK_POST, "json": json.dumps({"channel": "C0123"})})   # a body given as JSON text
    assert again.targets == e.targets


def test_a_telegram_send_with_the_token_in_the_path_and_a_discord_send_with_the_channel_in_the_path():
    tg = effect_of("connector_send", {"name": "telegram-bot", "method": "POST", "url": "https://api.telegram.org/bot123:ABC/sendMessage",
                                      "json": {"chat_id": -100123, "text": "x"}})
    assert tg.targets == ["connector:telegram-bot#-100123"] and tg.policy == "task"
    dc = effect_of("connector_send", {"name": "discord-bot", "method": "POST", "url": "/channels/998877/messages", "json": {"content": "x"}})
    assert dc.targets == ["connector:discord-bot#998877"] and dc.kind == "send"
    gone = effect_of("connector_send", {"name": "discord-bot", "method": "DELETE", "url": "/channels/998877/messages/555"})
    assert (gone.kind, gone.action, gone.policy, gone.targets) == ("delete", "connector_delete", "fresh", ["connector:discord-bot#998877"])
    fwd = effect_of("connector_send", {"name": "telegram-bot", "method": "POST", "url": "/forwardMessage", "json": {"chat_id": 5, "from_chat_id": 6}})
    assert fwd.policy == "fresh" and fwd.targets == ["connector:telegram-bot#5"]


def test_a_call_that_matches_no_declaration_is_as_before():
    e = effect_of("connector_send", {"name": "slack-api", "method": "POST", "url": "/reactions.add", "json": {"channel": "C1"}})
    assert (e.kind, e.policy, e.targets) == ("send", "", ["connector:slack-api"])
    asana = effect_of("connector_send", {"name": "asana-api", "method": "POST", "url": "/chat.postMessage", "json": {"channel": "C1"}})
    assert (asana.kind, asana.policy, asana.targets) == ("change", "", ["connector:asana-api"])


def test_a_declared_recipient_that_is_missing_leaves_the_connector_whole():
    e = effect_of("connector_send", {"name": "slack-api", "method": "POST", "url": "/chat.postMessage", "json": {"text": "hi"}})
    assert e.targets == ["connector:slack-api"] and e.policy == "task"


def test_a_declaration_can_only_make_a_call_stricter():
    rows = parse_rules({"notion": [{"id": "x", "via": "tool", "tool": "search_pages", "kind": "send", "recipient": "arguments.to"}]})
    r = ConnectorRules(rows)
    assert r.for_tool("notion", "search_pages", {"arguments": {"to": ["A@x.org", "b@x.org"]}}).recipients == ["a@x.org", "b@x.org"]
    old = cr.rules().builtin
    try:
        cr.rules().builtin = {**old, **rows}
        assert effect_of("mcp_call_tool", {"name": "notion-mcp", "tool": "search_pages", "arguments": {"to": "a@x.org"}}).kind == "send"   # was read
        lax = parse_rules({"notion": [{"id": "y", "via": "tool", "tool": "delete_page", "kind": "change"}]})
        cr.rules().builtin = {**old, **lax}
        effects.remember_mcp_tools("notion-mcp", [{"name": "delete_page", "annotations": {"destructiveHint": True}}])   # the server's own hint
        assert effect_of("mcp_call_tool", {"name": "notion-mcp", "tool": "delete_page"}).kind == "delete"      # a "change" cannot lift it
    finally:
        cr.rules().builtin = old


def test_a_package_gives_its_rules_through_the_source():
    rows = parse_rules({"fastmail": [{"id": "s", "via": "tool", "tool": "send_mail", "kind": "send", "policy": "fresh",
                                      "recipient": ["arguments.to", "arguments.cc"]}]})
    r = ConnectorRules({}, lambda cid: rows if cid == "fastmail" else None)
    f = r.for_tool("fastmail", "send_mail", {"arguments": {"to": "Kim <kim@x.org>, lee@x.org", "cc": ["kim@x.org", "zed@x.org"]}})
    assert f.policy == "fresh" and f.recipients == ["kim@x.org", "lee@x.org", "zed@x.org"]
    assert r.for_tool("other", "send_mail", {}) is None
    assert ConnectorRules({}, lambda cid: 1 / 0).for_tool("x", "y", {}) is None                      # a broken source never fails the call


@pytest.mark.parametrize("bad", [
    {"x": [{"id": "a", "via": "tool", "tool": "t", "kind": "read"}]},                                  # kind
    {"x": [{"id": "a", "via": "tool", "tool": "t", "kind": "send", "policy": "always"}]},               # policy
    {"x": [{"id": "a", "via": "carrier", "tool": "t", "kind": "send"}]},                               # via
    {"x": [{"id": "a", "via": "request", "http": "GET", "path": "p", "kind": "send"}]},                # http
    {"x": [{"id": "a", "via": "request", "http": "POST", "path": "a/b?c", "kind": "send"}]},             # query in path
    {"x": [{"id": "a", "via": "request", "http": "POST", "path": "a", "kind": "send", "recipient": "path"}]},   # no segment
    {"x": [{"id": "a", "via": "tool", "tool": "t", "kind": "send", "recipient": "json."}]},             # empty key
    {"x": [{"id": "a", "via": "tool", "tool": "t", "kind": "send", "recipient": "body.x"}]},            # unknown place
    {"x": [{"id": "a", "via": "tool", "kind": "send"}]},                                               # no tool
    {"x": [{"id": "a", "via": "tool", "tool": "t", "kind": "send"}, {"id": "a", "via": "tool", "tool": "u", "kind": "send"}]},
    {"x": []},
])
def test_a_bad_declaration_is_refused(bad):
    with pytest.raises(RulesError):
        parse_rules(bad)


def test_a_package_manifest_with_rules_is_read_and_a_bad_one_refused(tmp_path):
    from assistant.packages import load_package
    from test_nest import EXAMPLES, manifest, write_manifest
    import copy
    m = manifest(EXAMPLES / "example_tasks")
    m["rules"] = [{"id": "share", "via": "tool", "tool": "share_task", "kind": "send", "policy": "fresh", "recipient": "arguments.with"}]
    p = load_package(write_manifest(tmp_path / "ok" / "example_tasks", m))
    assert [(x.id, x.policy, x.where) for x in p.rules] == [("share", "fresh", ("arguments.with",))]
    bad = copy.deepcopy(m)
    bad["rules"][0]["policy"] = "whenever"
    with pytest.raises(PackageError, match="policy"):
        load_package(write_manifest(tmp_path / "bad" / "example_tasks", bad))
    plain = manifest(EXAMPLES / "example_tasks")
    assert load_package(write_manifest(tmp_path / "plain" / "example_tasks", plain)).rules == ()


# ---------------------------------------------------------------------------------------------------------------- the three places
def make_perms(**modes):
    store = Store()
    tools = fake_tools()
    p = Permissions(store, tools=lambda: tools)
    p.put(Info("slack", "Slack", "key", sends=True), modes)
    return p, tools


def test_a_connector_wide_allow_does_not_skip_the_card_of_a_task_or_fresh_method():
    p, tools = make_perms(send="allow")
    send = next(t for t in tools if t.name == "connector_send")
    assert p.verdict(send, SLACK_POST).action == "ask"                                       # declared "task": the setting does not skip it
    effects.remember_judged("slack-api", "POST /reactions.add", "change", False)   # the tool judge's reading of the undeclared call
    other = p.verdict(send, {"name": "slack-api", "method": "POST", "url": "/reactions.add", "json": {"channel": "C1"}})
    assert other.action == "allow"                                                           # not declared: the setting works as before


def test_the_task_permission_naming_one_room_covers_only_that_room(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    scope = f"schedule:{poll['id']}"
    grants.add_action(scope, "change", "tell the team", ["connector:slack-api#c0123"], max_count=5)
    with in_scope(scope):
        assert grants.action_check("connector_send", SLACK_POST).grant is not None
        elsewhere = grants.action_check("connector_send", {**SLACK_POST, "json": {"channel": "C9999", "text": "hi"}})
        assert elsewhere.grant is None and elsewhere.reason == "task_other_target"
        nameless = grants.action_check("connector_send", {**SLACK_POST, "json": {"text": "hi"}})
        assert nameless.grant is None                                                         # recipient not readable: it asks
    scope2 = f"schedule:{schedules.create('b', 'b', every_minutes=5, created_by='agent')['id']}"
    grants.add_action(scope2, "change", "any room", ["connector:slack-api"], max_count=5)      # the connector named whole, as before
    with in_scope(scope2):
        assert grants.action_check("connector_send", {**SLACK_POST, "json": {"channel": "C9999"}}).grant is not None


def test_a_fresh_method_is_never_covered_by_a_task_permission(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    scope = f"schedule:{poll['id']}"
    grants.add_action(scope, "change", "clean up", ["connector:slack-api"], max_count=5, allow_delete=True)
    with in_scope(scope):
        gone = grants.action_check("connector_send", {"name": "slack-api", "method": "POST", "url": "/chat.delete", "json": {"channel": "C1", "ts": "1"}})
        assert gone.grant is None and gone.reason == "task_fresh_only"


FWD = {"name": "slack-api", "method": "POST", "url": "/chat.postMessage", "json": {"channel": "C0123", "text": BODY[:90]}}


async def test_the_owner_recipient_allow_does_not_cover_a_task_method_and_offers_no_button():
    store, ts, _, gate, _ = rig()
    ts.add_allow("connector:slack-api#c0123", "mail", "a1")
    out, asked = await go(gate, [mail_read_tool(), send_tool([], "connector_send")], ("email_read", {}), ("connector_send", {**FWD, "json": {"channel": "C0123", "text": BODY[:90]}}))
    assert len(asked) == 1 and asked[0].trail == []                                          # it asks, and offers no "always for this recipient"


async def test_a_task_permission_naming_the_room_passes_the_trail_but_not_a_fresh_method():
    _, _, _, gate, _ = rig(granted={"connector:slack-api#c0123"})
    out, asked = await go(gate, [mail_read_tool(), send_tool([], "connector_send")], ("email_read", {}), ("connector_send", FWD))
    assert asked == []
    _, _, _, gate2, _ = rig(granted={"connector:telegram-bot#5"})
    fwd = {"name": "telegram-bot", "method": "POST", "url": "/forwardMessage", "json": {"chat_id": 5, "text": BODY[:90]}}
    out, asked = await go(gate2, [mail_read_tool(), send_tool([], "connector_send")], ("email_read", {}), ("connector_send", fwd))
    assert len(asked) == 1                                                                   # "fresh": the task permission does not cover it
    _, _, _, gate3, _ = rig(granted={"connector:telegram-bot"})                              # the connector named whole covers a "task" method
    send = {**fwd, "url": "/sendMessage"}
    out, asked = await go(gate3, [mail_read_tool(), send_tool([], "connector_send")], ("email_read", {}), ("connector_send", send))
    assert asked == []
