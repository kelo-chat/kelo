"""The per-connector switch "Use when I'm not chatting" (permissions.py, key `unattended`): in a turn nobody chats in (schedules, background
jobs, follow-ups, proactive runs, watchers) a connector switched off is refused and hidden; the owner's own chats are never affected."""

import httpx
import pytest

from assistant import workers
from assistant.permissions import Gate, Info, Permissions, PermissionsError, flat_body
from assistant.server import create_app
from assistant.store import Store

from test_agent_profile import app_settings, approval_tool
from test_conditional_grants import Plan, server_model
from test_permissions import counting, fake_tools, put, run_calls
from test_server import AUTH, Running

GMAIL, CAL = Info("gmail", "Gmail", "mail"), Info("google_calendar", "Calendar", "calendar")


def perms(**kw):
    store = Store()
    tools = fake_tools()
    return store, Permissions(store, tools=lambda: tools, names=lambda cid: {"gmail": "Gmail", "google_calendar": "Calendar"}.get(cid, cid),
                              connected=lambda: {"mail": ["gmail"], "calendar": ["google_calendar"], "phone": ["phone"]}, **kw)


@pytest.fixture
def quiet():
    """Run the test as a turn nobody is chatting in (what the server sets for a schedule or background job)."""
    token = workers.current_turn.set({"turn_id": "t1", "source": "schedule:1", "unattended": True})
    yield
    workers.current_turn.reset(token)


@pytest.fixture
def chatting():
    token = workers.current_turn.set({"turn_id": "t2", "source": "chat", "unattended": False})
    yield
    workers.current_turn.reset(token)


async def test_an_unattended_turn_is_refused_and_does_not_see_the_connector_when_it_is_off(quiet):
    _, p = perms()
    p.put(CAL, {"unattended": "off"})
    ran = []
    tools = [counting("gcal_events", ran, requires_approval=False), counting("phone_contacts", ran, requires_approval=False)]
    res, asked, seen = await run_calls(tools, Gate(p), ("phone_contacts", {}))
    assert "gcal_events" not in seen[0] and "phone_contacts" in seen[0]                   # hidden like a fully-off connector
    assert ran == [{}] and asked == []                                                    # the phone was not switched off
    v = p.verdict(tools[0], {})
    assert v.action == "off" and v.connector == "google_calendar"
    assert v.message == "The owner turned off using Calendar when they are not chatting. Do not try another way; say so in the result."
    ran.clear()
    res, _, _ = await run_calls([counting("gcal_events", ran, requires_approval=False)], Gate(p), ("gcal_events", {}))   # a model that calls it anyway
    assert ran == [] and res[0].is_error and "when they are not chatting" in res[0].output


async def test_the_owners_own_chat_turn_still_uses_the_connector(chatting):
    _, p = perms()
    p.put(CAL, {"unattended": "off"})
    ran = []
    res, asked, seen = await run_calls([counting("gcal_events", ran, requires_approval=False)], Gate(p), ("gcal_events", {}))
    assert "gcal_events" in seen[0] and ran == [{}] and not res[0].is_error


async def test_a_background_job_counts_as_unattended_too():
    _, p = perms()
    p.put(CAL, {"unattended": "off"})
    token = workers.current_worker.set("w1")
    try:
        ran = []
        _, _, seen = await run_calls([counting("gcal_events", ran, requires_approval=False)], Gate(p), ("gcal_events", {}))
        assert "gcal_events" not in seen[0] and ran == []
    finally:
        workers.current_worker.reset(token)


async def test_nothing_set_is_todays_behaviour_in_every_kind_of_turn(quiet):
    _, p = perms()
    ran = []
    _, _, seen = await run_calls([counting("gcal_events", ran, requires_approval=False)], Gate(p), ("gcal_events", {}))
    assert "gcal_events" in seen[0] and ran == [{}]
    p.put(CAL, {"unattended": "allow"})
    assert p.explicit_config("google_calendar") == {"unattended": "allow"} and p.limited("google_calendar", CAL) is False
    ran.clear()
    await run_calls([counting("gcal_events", ran, requires_approval=False)], Gate(p), ("gcal_events", {}))
    assert ran == [{}]


def test_the_setting_is_stored_cleared_and_validated():
    store, p = perms()
    assert p.describe(GMAIL)["unattended"] == {"mode": "allow", "default": "allow"}
    p.put(GMAIL, {"unattended": "off"})
    assert p.describe(GMAIL)["unattended"] == {"mode": "off", "default": "allow"} and p.limited("gmail", GMAIL) is True
    assert store.meta("permissions:gmail") == '{"unattended": "off"}'
    p.put(GMAIL, {"unattended": None})
    assert p.explicit_config("gmail") == {} and p.limited("gmail", GMAIL) is False
    for bad in ("ask", "sometimes", 1):
        with pytest.raises(PermissionsError):
            p.put(GMAIL, {"unattended": bad})
    # the GET shape put back unchanged sets nothing; a changed mode is taken
    now = p.describe(GMAIL)
    assert "unattended" not in flat_body(now, now)
    now["unattended"]["mode"] = "off"
    assert flat_body(now, p.describe(GMAIL))["unattended"] == "off"


async def test_one_connector_off_does_not_hide_a_tool_another_connector_still_offers(quiet):
    store = Store()
    tools = fake_tools()
    p = Permissions(store, tools=lambda: tools, connected=lambda: {"mail": ["gmail", "outlook_mail"]})
    p.put(GMAIL, {"unattended": "off"})
    assert p.hides("email_list") is False
    p.put(Info("outlook_mail", "Outlook", "mail"), {"unattended": "off"})
    assert p.hides("email_list") is True


async def test_the_api_round_trip(tmp_path):
    asking = [approval_tool(n) for n in ("email_send", "email_update", "gcal_delete_event")]
    async with Running(create_app(app_settings(tmp_path), tools=asking, stream_fn=server_model(Plan()))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/connectors/gmail/permissions")).json()["unattended"] == {"mode": "allow", "default": "allow"}
        got = await put(c, "gmail", {"unattended": "off"})
        assert got["unattended"] == {"mode": "off", "default": "allow"}
        assert (await c.get("/api/connectors/gmail/permissions")).json()["unattended"]["mode"] == "off"
        rows = {r["id"]: r for r in (await c.get("/api/connectors")).json()}
        assert rows["gmail"]["limited"] is True and rows["google_calendar"]["limited"] is False
        r = await c.put("/api/connectors/gmail/permissions", json={"unattended": "ask"})
        assert r.status_code == 422
        assert (await put(c, "gmail", {"unattended": None}))["unattended"]["mode"] == "allow"
        await put(c, "gmail", {"unattended": "off"})
        assert (await c.delete("/api/connectors/gmail/permissions")).json()["unattended"]["mode"] == "allow"
