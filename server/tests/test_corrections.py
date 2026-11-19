"""The correction check (corrections.py): what it reads, what it keeps, silence, the ledger, and the two places that use it."""

from __future__ import annotations

import re

from situation_fake import Rig, app_tools, ts

from assistant import situation as S
from assistant.corrections import CorrectionCheck


def check_for(r: Rig) -> CorrectionCheck:
    return CorrectionCheck(r.p, run_turn=r.agent.run_turn, tools=app_tools)


def gov(url="https://www.example.gov/notice/1"):
    return {"url": url, "publisher": "The authority", "official": True}


def proposes(mid_of, **kw):
    """The check's script: one correction for the message `mid_of()` returns."""
    async def script(tools, user):
        return await tools["propose_correction"].handler({"message": mid_of(), "quote": "반입할 수 없어요",
                                                          "now": "처방전이 있으면 반입할 수 있어요.", "sources": [gov()], **kw})
    return script


def test_the_prompt_says_the_owners_criterion_and_leaves_out_prices():
    text = (S.prompts_dir() / "correction_check.md").read_text(encoding="utf-8")
    assert ("Pick only what you told the owner that, if it turned out to be false, could put them in an awkward spot or cause them harm "
            "or a loss of any kind.") in text
    assert "Prices, stock and sellers of things to buy change all the time and are read again when buying: leave them." in text
    assert "an official source, or two independent ones" in text and "needs nothing" in text


async def test_no_answer_of_kelos_no_model_call(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 9, 9))
    assert await check_for(r).check(r.clock.t) == [] and r.agent.count("recheck") == 0


async def test_a_backed_correction_is_kept_with_its_quote_and_sources(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 9, 9))
    mid = r.say("assistant", "그 규정에 따르면 해외에서 산 약은 반입할 수 없어요.", ts(2026, 11, 16, 20, 1))
    r.agent.on("recheck", proposes(lambda: mid))
    found = await check_for(r).check(r.clock.t)
    assert [c.key for c in found] == [f"correction:{mid}"] and found[0].quote == "반입할 수 없어요" and found[0].url.startswith("https://www.example.gov")
    call = r.agent.calls[0]
    assert call["scope"] == "recheck" and "propose_correction" in call["tools"] and "web_search" in call["tools"]
    assert not set(call["tools"]) & {"email_send", "schedule_create", "browser_click"}     # read-only tools only
    assert f"message {mid}" in call["user"]


async def test_weak_sources_a_made_up_quote_or_an_unknown_message_are_refused(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 9, 9))
    mid = r.say("assistant", "그 규정에 따르면 해외에서 산 약은 반입할 수 없어요.", ts(2026, 11, 16, 20, 1))
    answers = []

    async def script(tools, user):
        h = tools["propose_correction"].handler
        answers.append(await h({"message": mid, "quote": "반입할 수 없어요", "now": "x", "sources": [{"url": "https://news.example.com/a"}]}))
        answers.append(await h({"message": mid, "quote": "지어낸 말", "now": "x", "sources": [gov()]}))
        answers.append(await h({"message": 99999, "quote": "반입할 수 없어요", "now": "x", "sources": [gov()]}))
    r.agent.on("recheck", script)
    assert await check_for(r).check(r.clock.t) == []
    assert answers[0].startswith("Refused") and answers[1].startswith("Refused") and answers[2].startswith("Error")


async def test_what_still_holds_is_silent_and_records_nothing(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 9, 9))
    r.say("assistant", "그 규정에 따르면 해외에서 산 약은 반입할 수 없어요.", ts(2026, 11, 16, 20, 1))
    r.agent.on("recheck", lambda tools, user: "Checked one answer; it still holds. Proposed nothing.")
    assert await check_for(r).check(r.clock.t) == []
    assert not r.store._all("SELECT 1 FROM proactive_log WHERE key LIKE 'correction:%'")


async def test_a_recorded_correction_leaves_its_answer_out_of_later_checks(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 9, 9))
    mid = r.say("assistant", "그 규정에 따르면 해외에서 산 약은 반입할 수 없어요.", ts(2026, 11, 16, 20, 1))
    c = check_for(r)
    c.record(f"correction:{mid}", "told", "처방전이 있으면 반입할 수 있어요.")
    assert c.answers(r.clock.t) == []                                       # nothing left: no model call
    assert await c.check(r.clock.t) == [] and r.agent.count("recheck") == 0
    row = r.store._all("SELECT status, source FROM proactive_log WHERE key = ?", (f"correction:{mid}",))[0]
    assert row["status"] == "dropped" and row["source"] == "correction"    # never counted against the daily limits
    assert r.p.today_counts() == {"보통": 0, "오늘": 0, "지금": 0}


async def test_answers_older_than_the_window_are_not_read(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 9, 9))
    r.say("assistant", "오래된 답", r.clock.t - S.WINDOW_HOURS * 3600 - 60)
    assert check_for(r).answers(r.clock.t) == []


from assistant.briefing import Briefing
from assistant.weather import Weather
from weather_fakes import FakeWeather


def both(tmp_path, *, briefing_on: bool, proactive_on: bool):
    """The owner's case on one server: Kelo answered at 20:01 yesterday; the briefing is due at 07:00, the hourly look runs at 07:09."""
    r = Rig(tmp_path, ts(2026, 11, 17, 7, 0))
    mid = r.say("assistant", "그 규정에 따르면 해외에서 산 약은 반입할 수 없어요.", ts(2026, 11, 16, 20, 1))
    check = CorrectionCheck(r.p, run_turn=r.agent.run_turn, tools=app_tools)
    r.sit.corrections = check
    w = Weather(r.store, fetcher=FakeWeather(), clock=r.clock, language=lambda: "en")
    w.set_here(37.566, 126.978, "서울특별시", "KR")
    cards: list[str] = []

    async def deliver(card, text, push):
        cards.append(text)
        return r.store.add_message("assistant", text)
    b = Briefing(r.store, w, r.p, home=r.home, deliver=deliver, clock=r.clock, language=lambda: "en")
    b.corrections = check
    b.update(enabled=briefing_on)
    r.p.update(enabled=proactive_on)
    r.agent.on("recheck", proposes(lambda: mid))
    r.agent.on("situation_select", lambda tools, user: tools["select_help"].handler(
        {"items": [f"correction:{mid}"], "priority": "regular", "handoff": "Correct yesterday's answer."}) if f"correction:{mid}" in user else "")
    r.agent.on("situation_write", lambda tools, user: "어제 반입할 수 없다고 했는데, 처방전이 있으면 가능해요.")
    return r, b, cards, mid


async def morning_then_hour(r, b):
    await b.tick()
    r.advance(9 * 60)
    await r.p.tick()


def told(r, mid) -> int:
    return len(r.store._all("SELECT 1 FROM proactive_log WHERE key = ?", (f"correction:{mid}",)))


async def test_both_on_the_briefing_tells_it_and_the_hourly_look_never_again(tmp_path):
    r, b, cards, mid = both(tmp_path, briefing_on=True, proactive_on=True)
    await morning_then_hour(r, b)
    assert len(cards) == 1 and "처방전이 있으면 반입할 수 있어요." in cards[0]
    assert r.sent == [] and told(r, mid) == 1                                 # the hourly look left the answer out: told once


async def test_briefing_on_proactive_off_only_the_briefing_runs_it(tmp_path):
    r, b, cards, mid = both(tmp_path, briefing_on=True, proactive_on=False)
    await morning_then_hour(r, b)
    assert len(cards) == 1 and "처방전" in cards[0] and r.sent == [] and r.agent.count("recheck") == 1 and told(r, mid) == 1


async def test_briefing_off_proactive_on_only_the_hourly_look_runs_it(tmp_path):
    r, b, cards, mid = both(tmp_path, briefing_on=False, proactive_on=True)
    await morning_then_hour(r, b)
    assert cards == [] and len(r.sent) == 1 and "처방전" in r.sent[0]["message"] and told(r, mid) == 1
    r.advance(3600)
    r.say("user", "고마워", r.clock.t - 60)                                  # something new: the next pass runs, the check again
    await r.p.tick()
    assert len(r.sent) == 1                                                   # never told twice


async def test_both_off_the_check_never_runs(tmp_path):
    r, b, cards, mid = both(tmp_path, briefing_on=False, proactive_on=False)
    await morning_then_hour(r, b)
    assert cards == [] and r.sent == [] and r.agent.count("recheck") == 0 and told(r, mid) == 0

