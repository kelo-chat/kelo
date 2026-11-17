"""What the owner lets Kelo do with each connector (permissions.py): the settings and their defaults (computed from the tools), the API,
the gate in the agent loop for chat, schedule and background runs (off / ask / allow; payments keep their own checks), tools hidden from
the model when every target is off, and the "Owner's limits" line in This Turn."""

import asyncio
import json

import httpx
import pytest

from assistant import effects
from assistant.agent import Tool, ToolResult, run_agent
from assistant.permissions import Gate, Info, Permissions, PermissionsError, connected_by_family, flat_body
from assistant.prompt import turn_lines
from assistant.providers import Done, TextDelta, ToolCall
from assistant.schedule_grants import ConditionalGrants
from assistant.server import create_app
from assistant.store import Store

from test_agent_profile import app_settings, approval_tool
from test_conditional_grants import Plan, _pending, server_model, start, until
from test_server import AUTH, Running, follow


def fake_tools(**asks):
    """The tools Permissions reads the defaults from: name -> requires_approval."""
    async def handler(args):
        return "done"
    names = {"email_list": False, "email_read": False, "email_send": True, "email_update": True, "gcal_events": False, "gcal_create_event": True,
            "gcal_update_event": True, "gcal_delete_event": True, "phone_send_text": True, "phone_contacts": False,
            "connector_get": False, "connector_send": True, "mcp_call_tool": True, "mcp_list_tools": False, **asks}
    return [Tool(n, n, {"type": "object"}, handler, requires_approval=a) for n, a in names.items()]


def perms(tmp_path=None, **kw):
    store = Store()
    tools = fake_tools()
    p = Permissions(store, tools=lambda: tools, names=lambda cid: {"gmail": "Gmail", "google_calendar": "Calendar", "slack": "Slack"}.get(cid, cid), **kw)
    return store, p


GMAIL, CAL = Info("gmail", "Gmail", "mail"), Info("google_calendar", "Calendar", "calendar")
CALDAV, PHONE = Info("caldav_calendar", "CalDAV", "calendar_read"), Info("phone", "Phone", "phone")
SLACK, MCP = Info("slack", "Slack", "key", sends=True), Info("notion", "Notion", "mcp")


# ---------------------------------------------------------------------------------------------------------- the settings
def test_the_defaults_are_computed_from_what_asks_today_and_nothing_is_limited():
    _, p = perms()
    d = {k["kind"]: k for k in p.describe(GMAIL)["kinds"]}
    assert list(d) == ["read", "change", "send", "delete"]
    assert (d["read"]["mode"], d["read"]["default"]) == ("allow", "allow")
    assert d["change"]["default"] == "allow" and d["change"]["actions"][0]["id"] == "email_update"     # only the trash asks
    assert d["send"]["default"] == "ask" and d["delete"]["default"] == "ask" and d["delete"]["actions"][0]["id"] == "email_trash"
    assert p.limited("gmail", GMAIL) is False and p.limits_line() == ""
    assert [k["kind"] for k in p.describe(CAL)["kinds"]] == ["read", "change", "delete"]
    assert [k["kind"] for k in p.describe(CALDAV)["kinds"]] == ["read"]                                  # read-only calendars show only "read"
    assert [k["kind"] for k in p.describe(SLACK)["kinds"]] == ["read", "send", "delete"]                 # a messenger's changes are sends
    assert [k["kind"] for k in p.describe(MCP)["kinds"]] == ["read", "change", "send", "delete"]
    assert p.describe(PHONE)["kinds"][2]["actions"][0]["label"] == "Send text messages"


def test_a_default_follows_the_tool_when_it_stops_asking():
    store = Store()
    tools = fake_tools(gcal_delete_event=False)
    p = Permissions(store, tools=lambda: tools)
    assert [k for k in p.describe(CAL)["kinds"] if k["kind"] == "delete"][0]["default"] == "allow"


def test_an_action_beats_its_kind_and_a_partial_put_changes_only_what_it_names():
    store, p = perms()
    p.put(GMAIL, {"send": "off"})
    p.put(GMAIL, {"actions": {"email_send": "allow"}})
    assert p.mode_of("gmail", "send", "email_send") == "allow" and p.mode_of("gmail", "delete", "email_trash") is None
    p.put(GMAIL, {"delete": "ask"})
    assert p.explicit_config("gmail") == {"send": "off", "delete": "ask", "actions": {"email_send": "allow"}}
    p.put(GMAIL, {"send": None, "actions": {"email_send": None}})
    assert p.explicit_config("gmail") == {"delete": "ask"}
    assert json.loads(store.meta("permissions:gmail")) == {"delete": "ask"}                              # saved in meta
    p.reset("gmail")
    assert store.meta("permissions:gmail") == "" and p.limited("gmail", GMAIL) is False


@pytest.mark.parametrize("info,body", [
    (CALDAV, {"send": "off"}), (CAL, {"send": "off"}), (GMAIL, {"read": "never"}), (GMAIL, {"read": True}), (GMAIL, {"actions": {"nope": "off"}}),
    (GMAIL, {"actions": []}), (GMAIL, {"bogus": "off"}), (Info("spotify", "Spotify", None), {"read": "off"}),
])
def test_unknown_kinds_actions_and_modes_are_refused(info, body):
    _, p = perms()
    with pytest.raises(PermissionsError):
        p.put(info, body)


def test_putting_the_get_shape_back_unchanged_changes_nothing_and_a_changed_mode_in_it_counts():
    _, p = perms()
    got = p.describe(GMAIL)
    p.put(GMAIL, flat_body(got, got))
    assert p.explicit_config("gmail") == {}
    got["kinds"][2]["mode"] = "off"
    got["kinds"][2]["actions"][0]["mode"] = "allow"
    p.put(GMAIL, flat_body(got, p.describe(GMAIL)))
    assert p.explicit_config("gmail") == {"send": "off", "actions": {"email_send": "allow"}}
    with pytest.raises(PermissionsError):
        flat_body({"kinds": [{"kind": "wat"}]}, got)
    with pytest.raises(PermissionsError):
        flat_body({"kinds": [{"kind": "send", "mode": "off"}]}, p.describe(CALDAV))


def test_limited_means_off_ask_or_an_allow_that_differs_from_the_default():
    _, p = perms()
    p.put(GMAIL, {"read": "allow"})
    assert p.limited("gmail", GMAIL) is False                                                          # allow where it was allow
    p.put(GMAIL, {"send": "allow"})
    assert p.limited("gmail", GMAIL) is True                                                           # allow where it asked
    p.reset("gmail")
    p.put(GMAIL, {"actions": {"email_list": "ask"}})
    assert p.limited("gmail", GMAIL) is True


def test_the_owners_limits_line_is_short_and_only_when_something_is_off_or_ask():
    _, p = perms()
    assert p.limits_line() == ""
    p.put(GMAIL, {"send": "off"})
    p.put(CAL, {"delete": "ask"})
    assert p.limits_line() == "Owner's limits: Gmail: no sending; Calendar: deleting asks."
    p.put(GMAIL, {"actions": {"email_trash": "off"}})
    assert "trash" in p.limits_line().lower()
    for cid in ("a", "b", "c", "d", "e", "f", "g", "h"):
        p.put(Info(cid, "A very long connector name " + cid, "mail"), {"read": "off", "send": "off", "change": "off", "delete": "off"})
    assert len(p.limits_line()) <= 200 and p.limits_line().startswith("Owner's limits:")
    lines = turn_lines(tz_name="Asia/Seoul", limits=p.limits_line())
    assert any(ln.startswith("Owner's limits:") for ln in lines)
    assert not any("Owner's limits" in ln for ln in turn_lines(tz_name="Asia/Seoul"))                   # omitted when nothing is set


# ---------------------------------------------------------------------------------------------------------- the gate in the loop
async def run_calls(tools, gate, *calls, approve=None):
    """Run the scripted calls through the real loop; returns (results, cards asked, the tool names the model saw in its first request)."""
    asked, seen = [], []
    it = iter(calls)

    async def model(provider, request, **kw):
        seen.append([t["function"]["name"] for t in request.tools])
        if request.messages[-1].get("role") != "tool":
            nxt = next(it, None)
        else:
            nxt = next(it, None)
        if nxt is None:
            yield TextDelta("done")
            yield Done("stop")
            return
        yield ToolCall(f"c{len(seen)}", nxt[0], json.dumps(nxt[1]))
        yield Done("tool_calls")

    async def yes(req):
        asked.append(req)
        return True

    out = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "go"}], tools, approve=approve or yes, stream_fn=model, gate=gate)
           if isinstance(ev, ToolResult)]
    return out, asked, seen


def counting(name, ran, requires_approval=True, **kw):
    async def handler(args):
        ran.append(args)
        return "done"
    return Tool(name, name, {"type": "object"}, handler, requires_approval=requires_approval, **kw)


async def test_off_does_not_run_the_call_and_tells_the_model_not_to_try_another_way():
    _, p = perms()
    p.put(CAL, {"delete": "off"})
    ran = []
    res, asked, _ = await run_calls([counting("gcal_delete_event", ran)], Gate(p), ("gcal_delete_event", {"event_id": "1"}))
    assert ran == [] and asked == [] and res[0].is_error
    assert res[0].output == ("The owner turned this off for Calendar in the app (Connectors → Calendar → What Kelo may do). "
                             "Tell them if it matters; do not try another way.")


async def test_ask_is_a_card_every_time_even_for_a_tool_that_needs_none_and_allow_is_no_card():
    _, p = perms()
    p.put(CAL, {"change": "ask", "delete": "allow"})
    ran = []
    tools = [counting("gcal_update_event", ran, requires_approval=True, auto_approve=lambda a: True), counting("gcal_delete_event", ran),
             counting("gcal_events", ran, requires_approval=False)]
    res, asked, _ = await run_calls(tools, Gate(p), ("gcal_update_event", {"a": 1}), ("gcal_delete_event", {"a": 2}), ("gcal_events", {}))
    assert [r.name for r in asked] == ["gcal_update_event"] and asked[0].force is True                  # the auto-approve was overridden
    assert ran == [{"a": 1}, {"a": 2}, {}]                                                             # allow: the delete ran without a card
    p.put(CAL, {"read": "ask"})
    res, asked, _ = await run_calls(tools, Gate(p), ("gcal_events", {}))
    assert [r.name for r in asked] == ["gcal_events"]                                                  # even a read that never asked


async def test_a_declined_ask_card_stops_the_call():
    _, p = perms()
    p.put(CAL, {"change": "ask"})
    ran = []

    async def no(req):
        return False
    res, _, _ = await run_calls([counting("gcal_update_event", ran, requires_approval=False)], Gate(p), ("gcal_update_event", {}), approve=no)
    assert ran == [] and res[0].approved is False


async def test_payments_and_account_steps_keep_their_own_checks_with_allow():
    _, p = perms()
    p.put(Info("notion", "Notion", "mcp"), {"read": "allow", "change": "allow", "send": "allow", "delete": "allow"})
    effects.forget_mcp_tools()
    effects.remember_judged("notion", "create_page", "change", False)           # what the tool judge said about each tool's description
    effects.remember_judged("notion", "create_payment_link", "change", True)
    effects.remember_judged("notion", "sign_up_user", "change", True)
    ran = []
    tool = counting("mcp_call_tool", ran)
    calls = [("mcp_call_tool", {"name": "notion", "tool": "create_page"}), ("mcp_call_tool", {"name": "notion", "tool": "create_payment_link"}),
             ("mcp_call_tool", {"name": "notion", "tool": "sign_up_user"})]
    res, asked, _ = await run_calls([tool], Gate(p), *calls)
    assert [r.arguments["tool"] for r in asked] == ["create_payment_link", "sign_up_user"] and len(ran) == 3
    assert all(r.force is False for r in asked)                                                        # their own card, rules and grants apply
    effects.forget_mcp_tools()
    _, asked, _ = await run_calls([counting("mcp_call_tool", ran)], Gate(p), ("mcp_call_tool", {"name": "notion", "tool": "create_page"}))
    assert [r.arguments["tool"] for r in asked] == ["create_page"]                                      # a tool nothing judged yet asks too


async def test_a_tool_is_not_offered_when_every_target_of_it_is_off_but_is_when_one_target_is_not():
    store = Store()
    tools = fake_tools()
    p = Permissions(store, tools=lambda: tools, connected=lambda: {"mail": ["gmail", "outlook_mail"], "calendar": ["google_calendar"]})
    names = lambda: [t["function"]["name"] for t in []]  # noqa: E731
    ran = []
    real = [counting(n, ran, requires_approval=False) for n in ("email_send", "email_update", "gcal_delete_event", "gcal_events")]
    p.put(Info("gmail", "Gmail", "mail"), {"send": "off"})
    _, _, seen = await run_calls(real, Gate(p))
    assert "email_send" in seen[0]                                                                      # Outlook can still send
    p.put(Info("outlook_mail", "Outlook", "mail"), {"actions": {"email_send": "off"}})
    _, _, seen = await run_calls(real, Gate(p))
    assert "email_send" not in seen[0] and "email_update" in seen[0] and "gcal_events" in seen[0]
    p.put(Info("gmail", "Gmail", "mail"), {"change": "off", "delete": "off"})
    p.put(Info("outlook_mail", "Outlook", "mail"), {"actions": {"email_update": "off", "email_trash": "off"}})
    _, _, seen = await run_calls(real, Gate(p))
    assert "email_update" not in seen[0]
    p.put(Info("google_calendar", "Calendar", "calendar"), {"delete": "off"})
    _, _, seen = await run_calls(real, Gate(p))
    assert "gcal_delete_event" not in seen[0] and "gcal_events" in seen[0]
    p.put(Info("google_calendar", "Calendar", "calendar"), {"delete": None})
    _, _, seen = await run_calls(real, Gate(p))
    assert "gcal_delete_event" in seen[0]


def test_connected_by_family_reads_the_connect_table_and_the_mailboxes():
    table = {"gmail": {"connect": "mail", "connected": True}, "slack": {"connect": "key", "connected": True},
             "notion": {"connect": "mcp", "connected": False}, "home_assistant": {"connect": "key", "connected": True},
             "google_calendar": {"connect": "google", "connected": True}}
    got = connected_by_family(table, [{"name": "mail-gmail"}, {"name": "outlook"}])
    assert got["mail"] == ["gmail", "outlook_mail"] and got["key"] == ["slack", "home_assistant"] and got["calendar"] == ["google_calendar"]
    assert "mcp" not in got and got["phone"] == ["phone"]


# ---------------------------------------------------------------------------------------------------------- through the server
async def put(c, cid, body):
    r = await c.put(f"/api/connectors/{cid}/permissions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_api_lists_only_the_kinds_a_connector_has_and_takes_partial_changes(tmp_path):
    asking = [approval_tool(n) for n in ("email_send", "email_update", "gcal_delete_event", "phone_send_text", "connector_send", "mcp_call_tool")]
    async with Running(create_app(app_settings(tmp_path), tools=asking, stream_fn=server_model(Plan()))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        def kinds(body):
            return [k["kind"] for k in body["kinds"]]
        gm = (await c.get("/api/connectors/gmail/permissions")).json()
        assert gm["connector"] == "gmail" and gm["name"] == "Gmail" and kinds(gm) == ["read", "change", "send", "delete"]
        assert {a["id"] for k in gm["kinds"] for a in k["actions"]} == {"email_list", "email_read", "email_update", "email_send", "email_trash"}
        assert set(gm["kinds"][0]) == {"kind", "mode", "default", "actions"} and set(gm["kinds"][0]["actions"][0]) == {"id", "label", "mode", "default"}
        assert kinds((await c.get("/api/connectors/caldav_calendar/permissions")).json()) == ["read"]
        assert kinds((await c.get("/api/connectors/google_calendar/permissions")).json()) == ["read", "change", "delete"]
        assert kinds((await c.get("/api/connectors/phone/permissions")).json()) == ["read", "change", "send", "delete"]
        assert kinds((await c.get("/api/connectors/slack/permissions")).json()) == ["read", "send", "delete"]
        assert kinds((await c.get("/api/connectors/home_assistant/permissions")).json()) == ["read", "change", "delete"]
        assert kinds((await c.get("/api/connectors/notion/permissions")).json()) == ["read", "change", "send", "delete"]
        assert (await c.get("/api/connectors/spotify/permissions")).json()["kinds"] == []                # a site the browser opens: nothing to set
        assert (await c.get("/api/connectors/nonsense/permissions")).status_code == 404
        # partial changes, an action beating its kind, null clearing
        got = await put(c, "gmail", {"send": "off", "actions": {"email_send": "ask"}})
        send = got["kinds"][2]
        assert send["mode"] == "off" and send["default"] == "ask" and send["actions"][0]["mode"] == "ask"
        assert (await put(c, "gmail", {"actions": {"email_send": None}}))["kinds"][2]["actions"][0]["mode"] == "off"   # the kind's mode now
        rows = {r["id"]: r for r in (await c.get("/api/connectors")).json()}
        assert rows["gmail"]["limited"] is True and rows["google_calendar"]["limited"] is False and rows["slack"]["limited"] is False
        # the GET shape goes back unchanged: nothing new is set
        before = (await c.get("/api/connectors/google_calendar/permissions")).json()
        await put(c, "google_calendar", before)
        assert (await c.get("/api/connectors/google_calendar/permissions")).json() == before
        assert {r["id"]: r["limited"] for r in (await c.get("/api/connectors")).json()}["google_calendar"] is False
        # 422 for what makes no sense
        for cid, body in (("caldav_calendar", {"send": "off"}), ("gmail", {"read": "sometimes"}), ("gmail", {"actions": {"nope": "off"}}),
                          ("gmail", {"nope": "off"}), ("spotify", {"read": "off"}), ("gmail", {"kinds": [{"kind": "bad"}]})):
            r = await c.put(f"/api/connectors/{cid}/permissions", json=body)
            assert r.status_code == 422, (cid, body, r.text)
        assert (await c.put("/api/connectors/nonsense/permissions", json={"read": "off"})).status_code == 404
        back = (await c.delete("/api/connectors/gmail/permissions")).json()
        assert back["kinds"][2]["mode"] == "ask" and {r["id"]: r["limited"] for r in (await c.get("/api/connectors")).json()}["gmail"] is False


async def test_an_mcp_services_own_tools_appear_as_it_lists_them_and_their_kinds_follow_the_server(tmp_path):
    effects.forget_mcp_tools()
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=server_model(Plan()))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        effects.remember_mcp_tools("notion-mcp", [{"name": "search_pages", "annotations": {"readOnlyHint": True}}, {"name": "send_comment"},
                                                  {"name": "archive_page", "annotations": {"destructiveHint": True}}])
        effects.remember_judged("notion-mcp", "send_comment", "send", False)   # the tool judge's reading of its description
        got = (await c.get("/api/connectors/notion/permissions")).json()
        by_kind = {k["kind"]: [a["id"] for a in k["actions"]] for k in got["kinds"]}
        assert "search_pages" in by_kind["read"] and by_kind["send"] == ["send_comment"] and by_kind["delete"] == ["archive_page"]
        assert next(a for k in got["kinds"] for a in k["actions"] if a["id"] == "send_comment")["label"] == "Send comment"
        await put(c, "notion", {"actions": {"send_comment": "off"}})
        assert (await c.put("/api/connectors/notion/permissions", json={"actions": {"unknown_tool": "off"}})).status_code == 422
    effects.forget_mcp_tools()


async def test_chat_and_schedule_runs_obey_off_ask_and_allow_at_the_same_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 3)
    ran: list = []
    plan = Plan(("gcal_delete_event", {"event_id": "a"}))
    app = create_app(app_settings(tmp_path), tools=[approval_tool("gcal_delete_event", ran), approval_tool("gcal_update_event", ran)],
                     stream_fn=server_model(plan))
    grants: ConditionalGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async def chat():
            plan.reset(("gcal_delete_event", {"event_id": "a"}))
            tid = (await c.post("/api/turns", json={"text": "delete it"})).json()["id"]
            return tid

        # off, in chat: not run, no card, the model hears why
        await put(c, "google_calendar", {"delete": "off"})
        events = await follow(c, await chat())
        out = next(e for e in events if e["type"] == "tool_result")
        assert ran == [] and out["is_error"] and "turned this off for Google Calendar" in out["output"] and (await _pending(c)) == []
        # the model line: only while something is off
        assert "Owner's limits: Google Calendar: no deleting." in app.state.permissions.limits_line()
        # allow, in chat: runs with no card although the tool asks
        await put(c, "google_calendar", {"delete": "allow"})
        events = await follow(c, await chat())
        assert ran == [{"event_id": "a"}] and (await _pending(c)) == []
        # ask, in a schedule run, although a task permission covers the step: a card
        await put(c, "google_calendar", {"delete": "ask"})
        scope, fire, ref = await start(c, app, "schedule")
        grants.add_action(scope, "change", "change events", ["calendar"], max_count=5, allow_delete=True, action="tidy up")
        plan.reset(("gcal_delete_event", {"event_id": "b"}))
        running = asyncio.ensure_future(fire())
        card = (await until(lambda: _pending(c), tries=400))[0]
        assert card["name"] == "gcal_delete_event" and card["can_remember"] is False and card["can_remember_schedule"] is False
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False})
        await running
        assert ran == [{"event_id": "a"}]
        # without the setting the same grant covers the step: no card (today's behaviour)
        await c.delete("/api/connectors/google_calendar/permissions")
        plan.reset(("gcal_delete_event", {"event_id": "c"}))
        await fire()
        assert ran[-1] == {"event_id": "c"} and (await _pending(c)) == []
        # off, in a schedule run
        await put(c, "google_calendar", {"actions": {"gcal_delete_event": "off"}})
        plan.reset(("gcal_delete_event", {"event_id": "d"}))
        await fire()
        assert ran[-1] == {"event_id": "c"}


async def test_the_chats_calendar_read_skips_a_calendar_the_owner_turned_reading_off_for():
    from assistant.briefing_sources import Acct, Sources

    class Cals:
        def accounts(self):
            return [type("A", (), {"id": "caldav:x", "provider": "caldav", "name": "CalDAV", "address": "a@b.org", "item": "x"})()]

        async def read(self, start, end, *, wanted, **kw):
            return [{"title": "t", "start": 1, "end": 2, "source_id": a.id} for a in self.accounts() if wanted(a)]

    _, p = perms()
    s = Sources(Store(), Cals(), type("M", (), {"accounts": lambda self: []})())
    s.owner_allows = lambda a: p.mode_of("caldav_calendar", "read", "calendar_events") != "off"
    from datetime import datetime
    got = await s.events(datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert len(got[0]) == 1
    p.put(CALDAV, {"read": "off"})
    got = await s.events(datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert got[0] == [] and len(got[2]) == 1
