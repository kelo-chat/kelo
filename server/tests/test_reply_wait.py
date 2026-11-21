"""Results and messages Kelo starts itself wait for the chat reply that is running, with no time limit (owner 2026-11-20, D5/D8): a
job's report (turn_workers.worker_handoff), a scheduled run's result and a proactive message all use wait_for_chat_reply. Fake clock
(each wait counted), no model."""

import asyncio
from types import SimpleNamespace

import assistant.server as S
from assistant import turn_workers


async def test_a_scheduled_result_or_proactive_message_waits_for_a_long_chat_reply(monkeypatch):
    chat_turns = [900]   # the reply runs for 900 one-second waits (the old limit was 600)
    waits = []

    def running_turns():
        return [SimpleNamespace(source="chat")] if chat_turns[0] > 0 else []

    async def fake_sleep(seconds, *a, **kw):
        waits.append(seconds)
        chat_turns[0] -= 1

    monkeypatch.setattr(S.asyncio, "sleep", fake_sleep)
    await turn_workers.wait_for_chat_reply(running_turns)
    assert len(waits) == 900 and chat_turns[0] == 0


async def test_a_background_run_or_none_running_does_not_wait(monkeypatch):
    waits = []

    async def fake_sleep(seconds, *a, **kw):
        waits.append(seconds)

    monkeypatch.setattr(S.asyncio, "sleep", fake_sleep)
    await turn_workers.wait_for_chat_reply(lambda: [SimpleNamespace(source="schedule:3")])
    await turn_workers.wait_for_chat_reply(lambda: [])
    assert waits == []
