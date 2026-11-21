"""A job's report waits for the chat reply that is running, with no time limit (owner 2026-11-20, D5): it is put into the conversation
only after that reply ends, however long the reply takes. Fake clock (sleep counted), no model."""

import asyncio
from types import SimpleNamespace

import assistant.server as S
from assistant import turn_workers


async def test_a_job_report_waits_for_a_long_chat_reply_without_a_time_limit(monkeypatch):
    chat_turns = [900]   # the reply runs for 900 one-second waits (the old limit was 600)
    waits = []

    def running_turns():
        return [SimpleNamespace(source="chat")] if chat_turns[0] > 0 else []

    async def fake_sleep(seconds, *a, **kw):
        waits.append(seconds)
        chat_turns[0] -= 1

    class Reached(Exception):
        pass

    def resolve_model(*a, **kw):   # the report goes on only after the wait: stop there
        raise Reached()

    monkeypatch.setattr(S.asyncio, "sleep", fake_sleep)
    ctx = SimpleNamespace(TurnContext=None, activity=None, handoff_lock=asyncio.Lock(), keep_resume=None, reactions=None,
                          resolve_model=resolve_model, resume_context=None, running_turns=running_turns, start_turn=None,
                          stopping=[False], store=None, workers=None)
    try:
        await turn_workers.worker_handoff(ctx, {"id": "w1", "title": "Job", "parent_task_id": None}, "text", "head")
    except Reached:
        pass
    assert len(waits) == 900 and chat_turns[0] == 0
