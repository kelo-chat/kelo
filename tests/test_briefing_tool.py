"""The agent's `briefing` tool: the next briefing time exactly as the scheduler (Briefing.tick) will send it, changing it in the
chat, and the prompt and search that lead a question about it to this tool, not to a schedule. Fake clock, scripted model."""

from __future__ import annotations

from datetime import datetime

from assistant.agent.toolbox import search

from test_briefing import setup
from weather_fakes import SEOUL, ts


async def first_tick_after(b, clock, start: float, limit: float = 3 * 86400) -> float | None:
    """The first minute at or after `start` at which the scheduler sends a briefing (None within `limit`)."""
    t = start - start % 60
    while t < start + limit:
        clock.t = t
        if await b.tick(t) is not None:
            return t
        t += 60
    return None


async def test_show_gives_the_time_the_scheduler_sends_it(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 5, 0))   # a Monday, before the 07:00 start
    tool = b.tool()
    out = await tool.handler({})
    assert "Morning briefing: on" in out and "Next one: Mon 2026-11-09 07:00 (Asia/Seoul)" in out and "never make a schedule" in out
    at, _src, happened = b.next_briefing()
    assert at == await first_tick_after(b, clock, clock.t) and happened == ""
    # after today's went out: tomorrow's, and the tool says today's is done
    out = await tool.handler({})
    assert "today's already went out" in out.lower() and "Next one: Tue 2026-11-10 07:00" in out
    # a fixed time with an offset-free change in the chat, and the scheduler follows it
    out = await tool.handler({"time": "08:15"})
    assert out.startswith("Saved.") and "fixed time 08:15" in out and "Next one: Tue 2026-11-10 08:15" in out
    at, _src, _h = b.next_briefing()
    assert at == await first_tick_after(b, clock, clock.t + 60)
    assert b.config()["time_mode"] == "fixed" and b.public()["fixed_time"] == "08:15"   # what the apps' settings page reads


async def test_auto_time_follows_the_weekend_hours_and_an_offset(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 13, 22, 0))   # Friday night: the next one is Saturday's
    p.update(weekend_start="09:30", weekend_end="23:00")
    out = await b.tool().handler({"time": "auto", "offset_min": 20})
    assert "plus 20 min" in out and "Next one: Sat 2026-11-14 09:50" in out
    at, _src, _h = b.next_briefing()
    assert datetime.fromtimestamp(at, SEOUL).strftime("%H:%M") == "09:50"
    assert at == await first_tick_after(b, clock, clock.t)


async def test_sections_and_off_and_bad_values(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 5, 0))
    tool = b.tool()
    out = await tool.handler({"sections": {"weather": False}})
    assert "weather off" in out and b.config()["sections"]["weather"] is False
    out = await tool.handler({"enabled": False})
    assert "Morning briefing: off" in out and "Next one" not in out and b.next_briefing()[0] is None
    assert (await tool.handler({"time": "25:99"})).startswith("Error:")


def test_a_question_about_the_briefing_finds_this_tool_not_a_schedule(tmp_path):
    from test_toolbox import full_app, scripted
    from assistant.providers import Done, TextDelta
    from assistant.agent.toolbox import CORE_TOOLS
    from pathlib import Path
    app = full_app(tmp_path, scripted([TextDelta("ok"), Done("stop")]))
    hidden = [t for t in app.state.tools if t.name not in CORE_TOOLS]
    assert search(hidden, "morning briefing time")[0].name == "briefing"
    assert "schedule_create" not in [t.name for t in search(hidden, "briefing")[:3]]
    system = (Path(__file__).parents[1] / "content" / "prompts" / "system.md").read_text()
    assert "The morning briefing is built in" in system and "never make a schedule to imitate it" in system
