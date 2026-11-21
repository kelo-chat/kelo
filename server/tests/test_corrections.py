"""The correction check (corrections.py): what it reads, what it keeps, silence, the ledger, and the two places that use it."""

from __future__ import annotations

import json
import re

from situation_fake import SEOUL, Rig, app_tools, ts

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
                                                          "now": "처방전이 있으면 반입할 수 있어요.", "sources": [gov()],
                                                          "meant": "해외에서 산 약은 어떤 경우에도 반입할 수 없다는 뜻",
                                                          "why": "약을 가져가려다 그만둘 수 있어요", **kw})
    return script


def test_the_prompt_says_the_owners_criterion_and_leaves_out_prices():
    text = (S.prompts_dir() / "correction_check.md").read_text(encoding="utf-8")
    assert ("Pick only what you told the owner that, if it turned out to be false, could put them in an awkward spot or cause them harm "
            "or a loss of any kind.") in text
    assert "Prices, stock and sellers of things to buy change all the time and are read again when buying: leave them." in text
    assert "an official source, or two independent ones" in text and "needs nothing" in text


def test_the_prompt_corrects_only_what_is_still_relied_on_and_reads_claims_in_their_own_frame():
    text = (S.prompts_dir() / "correction_check.md").read_text(encoding="utf-8")
    assert "Correct only what the owner may still act on or rely on now." in text
    assert "removed, replaced or set right afterwards, in the same answer or a later one, are left alone" in text
    assert "Read each claim in the frame it was said in" in text and "The same fact put in another frame, form or wording is not wrong" in text
    assert "`meant`" in text and "`why` is what the owner may still do or rely on now" in text
    assert not re.search(r"[가-힣]", text) and not re.search(r"\b\d{1,2}:\d{2}\b", text)   # general principles only: no owner words, no clock times


async def test_a_proposal_without_what_it_meant_or_what_the_owner_still_relies_on_is_refused(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 9, 9))
    mid = r.say("assistant", "그 규정에 따르면 해외에서 산 약은 반입할 수 없어요.", ts(2026, 11, 16, 20, 1))
    answers = []

    async def script(tools, user):
        h = tools["propose_correction"].handler
        base = {"message": mid, "quote": "반입할 수 없어요", "now": "처방전이 있으면 반입할 수 있어요.", "sources": [gov()]}
        answers.append(await h({**base, "why": "약을 두고 갈 수 있어요"}))                 # no meant
        answers.append(await h({**base, "meant": "어떤 경우에도 안 된다는 뜻"}))           # no why
        answers.append(await h({**base, "meant": "어떤 경우에도 안 된다는 뜻", "why": "약을 두고 갈 수 있어요"}))
    r.agent.on("recheck", script)
    found = await check_for(r).check(r.clock.t)
    assert answers[0].startswith("Error") and answers[1].startswith("Refused") and answers[2].startswith("Kept")
    assert len(found) == 1 and found[0].meant == "어떤 경우에도 안 된다는 뜻" and found[0].why == "약을 두고 갈 수 있어요"
    assert "(meaning: 어떤 경우에도 안 된다는 뜻)" in found[0].prepared("2026-11-16 20:01") and "may still rely on" in found[0].prepared("x")


async def test_the_check_reads_the_owners_messages_in_time_order_but_proposes_only_answers(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 9, 9))
    first = r.say("assistant", "그 규정에 따르면 해외에서 산 약은 반입할 수 없어요.", ts(2026, 11, 16, 20, 1))
    owner = r.say("user", "그럼 그 약은 빼고 갈게요", ts(2026, 11, 16, 20, 5))
    r.say("assistant", "네, 그 약은 목록에서 뺐어요.", ts(2026, 11, 16, 20, 6))
    got = []

    async def script(tools, user):
        got.append(await tools["propose_correction"].handler({"message": owner, "quote": "그 약은 빼고 갈게요", "meant": "m", "now": "n",
                                                               "why": "w", "sources": [gov()]}))
    r.agent.on("recheck", script)
    assert await check_for(r).check(r.clock.t) == []
    body = r.agent.calls[0]["user"]
    assert body.index(f"message {first}") < body.index("그럼 그 약은 빼고 갈게요") < body.index("목록에서 뺐어요")   # oldest first
    assert "owner · 2026-11-16 20:05" in body and f"message {owner}" not in body
    assert got[0].startswith("Error")                                                                      # the owner's words are never proposable


def test_the_card_line_says_what_kelo_said_and_when_then_what_is_right():
    from assistant import i18n
    from assistant.corrections import Correction
    c = Correction(7, ts(2026, 11, 16, 20, 1), "반입할 수 없어요", "처방전이 있으면 반입할 수 있어요.", sources=[gov()])
    tok = i18n.use("en")
    try:
        line = c.line(ts(2026, 11, 17, 7, 0), SEOUL)
    finally:
        i18n.reset(tok)
    assert line == "I told you “반입할 수 없어요” yesterday at 8:01 PM. Correction: 처방전이 있으면 반입할 수 있어요. (The authority)"
    tok = i18n.use("ko")
    try:
        ko = c.line(ts(2026, 11, 17, 7, 0), SEOUL)
        today = c.line(ts(2026, 11, 16, 23, 0), SEOUL)
    finally:
        i18n.reset(tok)
    assert ko.startswith("어제 오후 8:01에 “반입할 수 없어요”라고 말씀드렸는데") and "처방전이 있으면 반입할 수 있어요." in ko
    assert today.startswith("오늘 오후 8:01에")
    long = Correction(8, ts(2026, 11, 16, 20, 1), "가 " * 100, "n", sources=[gov()])
    shown = re.search(r"“(.*)”", long.line(ts(2026, 11, 17, 7, 0), SEOUL)).group(1)
    assert shown == " ".join(("가 " * 100).split())   # shown whole: no cut the owner did not decide


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


async def test_the_morning_card_line_quotes_what_kelo_said_and_when(tmp_path):
    r, b, cards, mid = both(tmp_path, briefing_on=True, proactive_on=False)
    await b.tick()
    text = cards[0]
    assert "“반입할 수 없어요”" in text and "yesterday at 8:01 PM" in text and "처방전이 있으면 반입할 수 있어요." in text
    lines = text.splitlines()
    note = next(i for i, ln in enumerate(lines) if "“반입할 수 없어요”" in ln)
    assert "Worth noticing:" in lines and lines.index("Worth noticing:") < note                # the chat can tell which part of the card it is
    row = r.store._all("SELECT card FROM briefings")[0]
    assert "“반입할 수 없어요”" in json.loads(row["card"])["corrections"][0]["text"]
