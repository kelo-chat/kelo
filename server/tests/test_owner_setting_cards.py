"""Changes to the owner's own settings ask on a card (audit F7): the briefing and its sources, big-event notices, the messenger's notifications
and pairing codes, another job's pause, and the follow-up reminders. Views (show, status, lists) need no card; a job pausing itself
needs none either. The handlers are the same as before: only the approval differs."""

from assistant.schedules import schedule_tools
from assistant.tools.channel_tools import channel_tools
from assistant.workers import current_turn
from weather_fakes import ts as weather_ts

from test_agent_profile import make as schedule_make
from test_big_events import calendar
from test_briefing import setup as briefing_setup
from test_briefing_sources import FakeGoogle, build, full_store, mailbox
from test_followups import make as followups_make
from test_proactive import Judge, setup as proactive_setup, ts as proactive_ts


def test_the_briefing_shows_for_free_and_any_change_asks(tmp_path):
    _, _, _, b, _, _, _ = briefing_setup(tmp_path, weather_ts(2026, 11, 9, 5, 0))
    tool = b.tool()
    assert tool.requires_approval
    assert tool.auto_approve({}) is True
    assert tool.auto_approve({"enabled": False}) is False
    assert tool.auto_approve({"time": "07:30"}) is False
    assert tool.auto_approve({"offset_min": 20}) is False
    assert tool.auto_approve({"sections": {"weather": False}}) is False


async def test_briefing_sources_show_is_free_and_each_switch_asks(tmp_path):
    store = full_store(tmp_path)
    mailbox(store, "yahoo", "work@company.test")
    src, _, _ = build(store, google=FakeGoogle([("google-calendar", "me@gmail.test")]), phone=[])
    tool = src.tool()
    assert tool.auto_approve({"action": "show"}) is True
    assert tool.auto_approve({"action": "off", "accounts": ["work@company.test"]}) is False     # silences its heads-ups
    assert tool.auto_approve({"action": "on", "accounts": ["all"]}) is False                    # widens what is read
    assert "work@company.test" in await tool.describe({"action": "off", "accounts": ["work@company.test"]})


async def test_big_event_notices_show_for_free_and_a_change_asks(tmp_path):
    _, _, _, p, _ = proactive_setup(tmp_path, Judge(lambda items: []), t=proactive_ts(2026, 11, 9, 9), calendar=calendar([]))
    tool = p.ahead_tool()
    assert tool.requires_approval
    assert tool.auto_approve({}) is True
    assert tool.auto_approve({"days": [3]}) is False
    assert tool.auto_approve({"days": []}) is False
    assert await tool.describe({"days": [3]}) == "Change how early big events are mentioned?"


class FakeManager:
    names = {"telegram": "Telegram"}
    items = {"telegram": None}

    def state(self, cid):
        return {"paired": {"name": "Mina's chat"}}


def test_messenger_status_and_groups_are_free_and_every_change_asks():
    tool = channel_tools(lambda: None)[0]
    assert tool.auto_approve({"action": "status"}) is True
    assert tool.auto_approve({"action": "groups"}) is True
    for action in ("notify_on", "notify_off", "new_code", "unpair", "remove_group"):
        assert tool.auto_approve({"action": action}) is False, action


async def test_messenger_cards_say_which_change_is_asked():
    tool = channel_tools(lambda: FakeManager())[0]
    assert await tool.describe({"action": "notify_off"}) == "Turn Telegram notifications off?"
    assert await tool.describe({"action": "notify_on"}) == "Turn Telegram notifications on?"
    assert await tool.describe({"action": "new_code"}) == "Show a new pairing code for Telegram?"
    assert await tool.describe({"action": "unpair"}) == "Disconnect Telegram from Mina's chat?"


def test_pausing_another_job_asks_and_a_job_pausing_itself_does_not():
    _, _, s = schedule_make()
    pause = {t.name: t for t in schedule_tools(s)}["schedule_pause"]
    assert pause.requires_approval
    assert pause.auto_approve({"id": 5, "reason": "x"}) is False                 # another job, from the chat
    token = current_turn.set({"source": "schedule:5"})
    try:
        assert pause.auto_approve({"reason": "x"}) is True                       # its own job: the default id
        assert pause.auto_approve({"id": 5, "reason": "x"}) is True
        assert pause.auto_approve({"id": 6, "reason": "x"}) is False             # another job, from its run
    finally:
        current_turn.reset(token)


async def test_snoozing_and_stopping_reminders_always_asks():
    f, _, _, _, _ = followups_make()
    tools = {t.name: t for t in f.tools()}
    for name in ("followup_snooze", "followup_cancel"):
        assert tools[name].requires_approval and tools[name].auto_approve is None
    assert await tools["followup_cancel"].describe({"item": "login-tickets"}) == "Stop Kelo's reminders about login-tickets?"
    assert await tools["followup_snooze"].describe({"item": "login-tickets"}) == "Put off Kelo's reminders about login-tickets?"
