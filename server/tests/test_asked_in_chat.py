"""2026-11-18 (D1 yes): a change to Kelo's own settings that the owner asks for in the chat goes ahead without a card. It still asks when
a page, mail or message reached the run (outside text), or when nobody is in the chat (a background run). Offline: the tools' own
auto-approve rules are called with the turn's context set, as agent/loop.py sets it."""

from pathlib import Path

import pytest

from assistant.agent.loop import outside_seen
from assistant.server import Settings, create_app
from assistant.tools.connect_tools import asked_in_chat
from assistant.workers import current_turn

from test_server import TOKEN

CONTENT = Path(__file__).resolve().parents[2] / "server" / "content"
FAMILIES = {   # tool -> arguments that change a setting (not a read)
    "followup_snooze": {"item": "the trip", "minutes": 60},
    "followup_cancel": {"item": "the trip"},
    "schedule_create": {"name": "Morning", "prompt": "say hi", "every_minutes": 60},
    "schedule_update": {"id": 1, "name": "Morning"},
    "briefing": {"enabled": False},
    "big_event_notice": {"days": [3]},
    "event_reminders_set": {"item": "Before each event", "lead_minutes": [30]},
    "event_reminders_remove": {"id": 1},
}


@pytest.fixture()
def tools(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host="127.0.0.1", home_dir=str(tmp_path / "home"),
                              content_dir=str(CONTENT)))
    return {t.name: t for t in app.state.tools}


def _context(turn, seen):
    t_token = current_turn.set(turn)
    s_token = outside_seen.set([seen])
    return t_token, s_token


def _reset(tokens):
    current_turn.reset(tokens[0])
    outside_seen.reset(tokens[1])


def test_asked_in_chat_needs_a_chat_turn_with_no_outside_text_and_someone_in_the_chat():
    tokens = _context({"source": "chat"}, False)
    try:
        assert asked_in_chat() is True
    finally:
        _reset(tokens)
    for turn, seen in (({"source": "chat"}, True), ({"source": "chat", "unattended": True}, False), ({"source": "job"}, False)):
        tokens = _context(turn, seen)
        try:
            assert asked_in_chat() is False, (turn, seen)
        finally:
            _reset(tokens)


def test_the_owners_request_from_the_app_counts_like_one_asked_in_the_chat():
    for seen, want in ((False, True), (True, False)):
        tokens = _context({"source": "handoff", "unattended": True, "owner_asked": True}, seen)
        try:
            assert asked_in_chat() is want, seen
        finally:
            _reset(tokens)
    tokens = _context({"source": "handoff", "unattended": True}, False)
    try:
        assert asked_in_chat() is False
    finally:
        _reset(tokens)


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_a_settings_change_asked_in_chat_goes_ahead_and_asks_after_outside_text(tools, name):
    tool = tools[name]
    args = FAMILIES[name]
    assert tool.requires_approval and tool.auto_approve is not None, name
    tokens = _context({"source": "chat"}, False)
    try:
        assert tool.auto_approve(args) is True, name
    finally:
        _reset(tokens)
    tokens = _context({"source": "chat"}, True)   # a page, mail or message reached this run
    try:
        assert not tool.auto_approve(args), name
    finally:
        _reset(tokens)
    tokens = _context({"source": "chat", "unattended": True}, False)   # nobody in the chat
    try:
        assert not tool.auto_approve(args), name
    finally:
        _reset(tokens)


def test_a_change_the_owner_did_not_ask_for_in_chat_keeps_its_card(tools):
    tokens = _context({"source": "job"}, False)   # a background job of Kelo's own
    try:
        assert not tools["followup_snooze"].auto_approve({"item": "x"})
        assert not tools["schedule_create"].auto_approve({"name": "x", "prompt": "y"})
        assert not tools["event_reminders_set"].auto_approve({"item": "x", "lead_minutes": [30]})
    finally:
        _reset(tokens)
