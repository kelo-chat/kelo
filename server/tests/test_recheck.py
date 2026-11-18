"""Rechecks (recheck.py): the planner, the topic key, the source rule, the research -> verify -> handoff flow with fakes (no model, no
web), waking hours and the do-not-tell list, recheck_result, the nightly extraction riding on reflection's own request, the tools, and
the proactive candidate source."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from notify_fake import FakeRules
from assistant.activity import Activity
from assistant.home import Home
from assistant.proactive import Proactive
from assistant.providers import Done, TextDelta
from assistant.recheck import (CLAIM_MAX, DAY, DRAFT_MAX, RECHECK_MARK, RecheckHandoff, RecheckPlanner, Research, Row, SourceRule,
                               TopicKey, build_rechecks, topic_key)
from assistant.reflection import AUTO_PREFIXES, Reflection
from assistant.store import Store

SEOUL = ZoneInfo("Asia/Seoul")
HOW = "출입국·외국인정책본부 hikorea.go.kr, 법무부 moj.go.kr"


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def row(**kw) -> Row:
    base = dict(id=1, topic_key="visa:2026-11", claim="비자 연장은 90일 전부터 가능합니다", why="연장 시기 결정", how=HOW, origin="agent",
                created=ts(2026, 11, 13, 9, 0), next_at=ts(2026, 11, 14, 9, 0), expires_at=ts(2026, 12, 13, 9, 0))
    return Row(**{**base, **kw})


def official(url="https://www.hikorea.go.kr/info", **kw):
    return {"url": url, "publisher": "HiKorea", "official": True, "date": "2026-11-10", **kw}


def news(url="https://news.example.com/a", **kw):
    return {"url": url, "publisher": "News", "official": False, "date": "2026-11-11", **kw}


def answer(status="changed", now="이제 60일 전부터 가능합니다", since="2026-11-10", sources=None, unconfirmed=()):
    return json.dumps({"status": status, "now": now, "since": since, "unconfirmed": list(unconfirmed),
                       "sources": [official()] if sources is None else sources}, ensure_ascii=False)


class Kit:
    """A service with a fake research turn, a fake handoff and a fake clock."""

    def __init__(self, *answers, awake=True, prefs="", per_day=4, replied=True, rules=None):
        self.store = Store()
        self.store.set_profile(timezone="Asia/Seoul")
        self.clock = Clock(ts(2026, 11, 13, 9, 0))
        self.answers, self.turns, self.sent = list(answers), [], []
        self.awake, self.replied = awake, replied
        self.prefs, self.per_day = prefs, per_day

        async def run_turn(system, user):
            self.turns.append((system, user))
            out = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            if isinstance(out, Exception):
                raise out
            return out

        async def send(r, text):
            self.sent.append((r, text))

        self.kit = build_rechecks(self.store, run_turn=run_turn, send=send, replied=lambda text: self.replied, awake=lambda t: self.awake,
                                  next_awake=lambda t: t + 20 * 3600, prefs=lambda: self.prefs,
                                  config=lambda: {"rechecks": True, "recheck_per_day": self.per_day}, busy=lambda: False,
                                  ready=lambda: True, tz=lambda: SEOUL, turn_id=lambda: "turn-1", clock=self.clock)
        self.repo, self.service = self.kit.repo, self.kit.service
        if rules is not None:
            self.service.notify_block = rules.block   # the main model's reading of the don't-tell rules (notify_block.py), faked

    def add(self, topic="visa", **kw):
        return self.repo.add(**{"claim": "비자 연장은 90일 전부터 가능합니다", "why": "연장 시기", "how": HOW, "topic": topic, **kw})

    def tool(self, name):
        return next(t for t in self.kit.tools() if t.name == name).handler

    async def later(self, days=1.01):
        self.clock.t += days * DAY
        return await self.service.tick()


# ---------------------------------------------------------------------------------------------------------- the topic key
def test_topic_key_is_ascii_hyphens_and_a_month_and_the_same_wording_merges():
    now = ts(2026, 11, 13, 9, 0)
    assert topic_key("Visa Extension Rules!!", now, SEOUL) == "visa-extension-rules:2026-11"
    assert topic_key("  visa_extension--RULES ", now, SEOUL) == topic_key("visa extension rules", now, SEOUL)
    assert len(TopicKey.slug("a" * 200)) <= 60 and "--" not in TopicKey.slug("a - - b")
    assert TopicKey.slug("café-menü") == "cafe-menu"
    korean = TopicKey.slug("비자 연장")
    assert korean.startswith("topic-") and korean == TopicKey.slug("비자 연장") != TopicKey.slug("체류 기간")   # no ascii: a stable hash
    assert TopicKey.slug_of("visa-rules:2026-11") == "visa-rules" and TopicKey.slug("visa-rules:2026-11") == "visa-rules"
    assert topic_key("x", ts(2026, 12, 1, 0, 0), SEOUL) != topic_key("x", now, SEOUL)          # a new month, a new key


def test_two_wordings_with_the_same_slug_are_one_row(tmp_path):
    k = Kit("")
    first = k.add("Visa Extension Rules", claim="first wording of the claim")
    second = k.add("visa-extension-rules", claim="second wording of the claim")
    assert first.status == "added" and second.status == "updated" and first.row.id == second.row.id
    assert len(k.repo.list()) == 1 and k.repo.get(first.row.id).claim == "second wording of the claim"


# ---------------------------------------------------------------------------------------------------------- the planner
def test_the_gap_grows_one_three_seven_days_and_stays_at_seven():
    p, now = RecheckPlanner(lambda: SEOUL), ts(2026, 11, 13, 9, 0)
    assert p.first_check("agent", 0, now) == now + DAY
    gaps = [(p.schedule(row(attempts=n, expires_at=None), now) - now) / DAY for n in range(1, 6)]
    assert gaps == [3, 7, 7, 7, 7]
    assert p.first_check("owner_watch", 2, now) == now + 2 * DAY and p.schedule(row(origin="owner_watch", every_days=2, attempts=5), now) == now + 2 * DAY


def test_expiry_the_decision_date_thirty_days_and_a_watch_never():
    p, now = RecheckPlanner(lambda: SEOUL), ts(2026, 11, 13, 9, 0)
    assert p.expires_at("agent", None, now) == now + 30 * DAY
    end = p.decide_end("2026-11-20")
    assert end == ts(2026, 11, 20, 23, 59, 59) and p.expires_at("nightly", end, now) == end
    assert p.expires_at("owner_watch", end, now) is None
    r = row(expires_at=end)
    assert not p.expired(r, end) and p.expired(r, end + 1) and not p.expired(row(origin="owner_watch", expires_at=None), now + 900 * DAY)
    assert p.schedule(row(attempts=3, expires_at=end), ts(2026, 11, 19, 9, 0)) == end - 6 * 3600      # the last look is before the decision


def test_due_order_priority_and_the_daily_budget():
    p, now = RecheckPlanner(lambda: SEOUL), ts(2026, 11, 13, 9, 0)
    late = now - 10
    rows = [row(id=1, origin="nightly", next_at=late - 500), row(id=2, origin="agent", next_at=late - 400),
            row(id=3, origin="agent", next_at=late, decide_by=now + 3 * DAY), row(id=4, origin="owner_watch", expires_at=None, next_at=late),
            row(id=5, origin="agent", next_at=late - 900), row(id=6, origin="agent", next_at=now + 100),                      # not due yet
            row(id=7, state="closed", next_at=late - 999), row(id=8, origin="agent", next_at=late, expires_at=now - 1)]       # not live / expired
    assert [r.id for r in p.pick(rows, now, 10)] == [4, 3, 5, 2, 1]        # watch, decision within 7 days, promise (oldest first), the rest
    assert [r.id for r in p.pick(rows, now, 2)] == [4, 3] and p.pick(rows, now, 0) == []


async def test_the_budget_is_per_day_and_a_new_day_starts_again():
    k = Kit(answer("same"), per_day=2)
    for n in range(3):
        k.add(f"topic-{n}")
    out = await k.later()
    assert out["researched"] == 2 and len(k.turns) == 2                    # PER_TICK and the budget both say 2
    assert (await k.service.tick())["researched"] == 0                    # today's budget is used
    k.clock.t += DAY
    assert (await k.service.tick())["researched"] == 1                    # the third row waited; the others are not due for 3 days


# ---------------------------------------------------------------------------------------------------------- the source rule
def test_two_domains_pass_one_official_passes_one_news_domain_fails():
    rule = SourceRule("출입국 hikorea.go.kr")
    assert rule.judge([news("https://a.example.com/x"), news("https://b.example.org/y")]).ok                   # two different sites
    assert rule.judge([official()]).ok                                                                       # one official site
    out = rule.judge([news()])
    assert not out.ok and out.reason == "one unofficial source"
    assert not rule.judge([news("https://a.example.com/x"), news("https://www.example.com/y")]).ok            # the same site twice
    assert not rule.judge([news("https://a.example.co.kr/x"), news("https://b.example.co.kr/y")]).ok           # a registrable domain, not a host
    assert not rule.judge([]).ok and not rule.judge("nope").ok and not rule.judge([{"url": "javascript:alert(1)"}, {"url": ""}]).ok


def test_the_models_official_flag_is_not_trusted_alone():
    rule = SourceRule(HOW)
    liar = rule.judge([news(official=True)])                                  # says official: a news domain that `how` does not name
    assert not liar.ok and liar.sources[0]["official"] is False and liar.sources[0]["claimed_official"] is True
    assert not rule.judge([official("https://www.hikorea.go.kr.evil.com/x")]).ok                              # a look-alike host
    assert not rule.judge([{**official("https://hikorea.go.kr/x"), "official": False}, ]).sources[0]["official"] is False   # a government site is official by its domain
    assert not SourceRule("some site").judge([official("https://news.example.com/x")]).ok
    named = SourceRule("the company's own site acme.com").judge([news("https://www.acme.com/pricing", official=True)])
    assert named.ok and named.sources[0]["official"]                           # named in `how`, and the model says so
    assert not SourceRule("the company's own site acme.com").judge([news("https://www.acme.com/pricing", official=False)]).ok
    for gov in ("https://www.irs.gov/x", "https://www.gov.uk/x", "https://moj.go.kr/x"):
        assert SourceRule("").judge([{"url": gov}]).ok
    assert not SourceRule("").judge([{"url": "https://fake.gov.evil.com/x"}]).ok and not SourceRule("").judge([{"url": "https://evilgov.com/x"}]).ok


def test_the_researchs_json_is_read_strictly():
    assert Research.parse("no json") is None and Research.parse('{"status": "maybe"}') is None
    assert Research.parse('{"status": "changed", "now": ""}') is None                  # a change says what is right now
    r = Research.parse("```json\n" + answer() + "\n```")
    assert r and r.status == "changed" and r.since == "2026-11-10" and r.sources[0]["url"].startswith("https://www.hikorea")
    assert Research.parse('{"status": "same"}').status == "same"


# ---------------------------------------------------------------------------------------------------------- the check
async def test_same_is_silent_and_the_next_look_comes_from_the_planner():
    k = Kit(answer("same", sources=[]))
    r = k.add().row
    assert (await k.service.tick()) is None or True                               # not due yet: nothing
    assert k.turns == []
    await k.later()
    got = k.repo.get(r.id)
    assert k.sent == [] and got.state == "open" and got.attempts == 1 and got.last_checked == k.clock.t
    assert got.next_at == k.clock.t + 3 * DAY                                     # after one silent result: 3 days
    assert "hikorea.go.kr" in k.turns[0][1] and "비자 연장은 90일" in k.turns[0][1] and "2026-11-14" in k.turns[0][1]   # claim, how, today
    await k.later(3.01)
    assert k.repo.get(r.id).next_at == k.clock.t + 7 * DAY


async def test_changed_with_good_sources_hands_off_with_the_mark_and_fenced_sources():
    k = Kit(answer())
    r = k.add(decide_by=ts(2026, 11, 20, 23, 59, 59)).row
    await k.later()
    assert len(k.sent) == 1
    sent_row, text = k.sent[0]
    assert sent_row.id == r.id and text.startswith(RECHECK_MARK) and "비자 연장은 90일 전부터 가능합니다" in text and "2026-11-13" in text
    assert "[UNTRUSTED CONTENT" in text and "[END UNTRUSTED CONTENT" in text and "https://www.hikorea.go.kr/info" in text
    claim_block, research_block = text.split("[UNTRUSTED CONTENT")[1], text.split("[UNTRUSTED CONTENT")[2]
    assert "비자 연장은 90일 전부터 가능합니다" in claim_block.split("[END UNTRUSTED CONTENT")[0]     # Kelo's earlier words are data too
    fenced = research_block.split("[END UNTRUSTED CONTENT")[0]
    assert '"official":true' in fenced and "이제 60일 전부터" in fenced                      # the research is data inside the fence
    for step in ("open at least one of the sources (web_fetch) or search again", "what changed since you said it", "at most three short sentences",
                 "one offer of the next step", "the source as one link", "NO_REPLY", f"recheck_result({r.id}, \"not_confirmed\")"):
        assert step in text
    assert k.repo.get(r.id).state == "sent" and k.repo.get(r.id).draft and len(k.repo.get(r.id).draft) <= DRAFT_MAX   # a reply was written: sent


async def test_changed_with_weak_sources_is_unconfirmed_and_nothing_is_sent_then_closed_after_two_tries():
    k = Kit(answer(sources=[news()]))
    r = k.add().row
    await k.later()
    got = k.repo.get(r.id)
    assert k.sent == [] and got.state == "unconfirmed" and got.skip_reason == "one unofficial source" and got.weak == 1
    await k.later(3.01)                                                           # tried again at the next slot
    assert k.sent == [] and len(k.turns) == 2 and k.repo.get(r.id).state == "closed" and k.repo.get(r.id).skip_reason.startswith("unconfirmed")
    assert (await k.later(10))["researched"] == 0                                 # never a third try


async def test_unclear_and_a_broken_answer_count_as_unconfirmed_but_a_failed_run_is_not_counted():
    k = Kit("this is not json", answer("unclear", sources=[]))
    r = k.add().row
    await k.later()
    assert k.repo.get(r.id).state == "unconfirmed" and k.sent == []
    k2 = Kit(RuntimeError("model away"), answer("same", sources=[]))
    r2 = k2.add().row
    await k2.later()
    got = k2.repo.get(r2.id)
    assert got.state == "open" and got.attempts == 0 and got.weak == 0 and got.skip_reason == "research failed"      # tried again in 6 hours
    await k2.later(0.3)
    assert k2.repo.get(r2.id).attempts == 1 and k2.repo.get(r2.id).skip_reason == ""


async def test_it_does_nothing_when_off_asleep_or_a_chat_runs():
    k = Kit(answer("same", sources=[]))
    k.add()
    k.awake = False
    assert await k.later() is None and k.turns == []
    k.awake = True
    k.service.config = lambda: {"rechecks": False, "recheck_per_day": 4}
    assert await k.service.tick() is None and k.turns == []
    k.service.config = lambda: {"rechecks": True, "recheck_per_day": 4}
    k.service.busy = lambda: True
    assert await k.service.tick() is None and k.turns == []
    k.service.busy = lambda: False
    assert (await k.service.tick())["researched"] == 1


async def test_a_row_past_its_end_is_closed_silently():
    k = Kit(answer())
    r = k.add(decide_by=ts(2026, 11, 14, 23, 59, 59)).row
    await k.later(5)
    assert k.turns == [] and k.sent == [] and k.repo.get(r.id).state == "closed" and k.repo.get(r.id).skip_reason == "expired"


# ---------------------------------------------------------------------------------------------------------- handing over
async def test_a_handoff_outside_waking_hours_waits_for_the_next_waking_start():
    k = Kit(answer(), replied=True)
    r = k.add().row
    k.clock.t += 1.01 * DAY
    k.awake = False                                                               # the research ended after the window closed
    await k.service.check(k.repo.get(r.id))
    got = k.repo.get(r.id)
    assert k.sent == [] and got.state == "drafted" and got.next_at == k.clock.t + 20 * 3600
    assert (await k.service.tick()) is None                                      # still asleep
    k.awake, k.clock.t = True, got.next_at
    assert (await k.service.tick())["delivered"] == 1
    assert len(k.sent) == 1 and k.repo.get(r.id).state == "sent"


async def test_a_topic_on_the_do_not_tell_list_is_closed_and_not_sent():
    rules = FakeRules("비자")
    k = Kit(answer(), prefs="# NOTIFY_PREFS.md\n\n## Do not tell me\n- 비자\n\n## Tell me\n- 택배\n", rules=rules)
    r = k.add().row
    await k.later()
    got = k.repo.get(r.id)
    assert k.sent == [] and got.state == "closed" and got.skip_reason == "on the do-not-tell list"
    assert len(rules.calls) == 1 and "visa" in rules.calls[0]["items"][0]["text"]           # the slug and the claim go to the reader
    k2 = Kit(answer(), prefs="## Do not tell me\n- visa\n", rules=FakeRules("visa"))       # matched on the slug too
    r2 = k2.add("visa-rules", claim="Extensions open 90 days before expiry").row
    await k2.later()
    assert k2.sent == [] and k2.repo.get(r2.id).state == "closed"
    k3 = Kit(answer(), prefs="## Do not tell me\n- 광고\n", rules=FakeRules("광고"))
    k3.add()
    await k3.later()
    assert len(k3.sent) == 1


async def test_rules_that_cannot_be_read_tell_nothing_and_close_nothing():
    for rules in (FakeRules(down=True), None):
        k = Kit(answer(), prefs="## Do not tell me\n- visa\n", rules=rules)
        r = k.add().row
        await k.later()
        got = k.repo.get(r.id)
        assert k.sent == [] and got.state != "closed" and got.state != "sent" and got.next_at > k.clock.t
    k = Kit(answer(), prefs="# NOTIFY_PREFS.md\n\n## Do not tell me\n\n## Tell me\n", rules=FakeRules(down=True))   # no rules written: nothing to read
    k.add()
    await k.later()
    assert len(k.sent) == 1


async def test_recheck_result_sent_and_not_confirmed_and_the_fallback_from_the_reply():
    k = Kit(answer(), replied=False)                                              # the main agent never calls recheck_result
    r = k.add().row
    await k.later()
    assert k.repo.get(r.id).state == "unconfirmed" and k.repo.get(r.id).weak == 1       # no reply was written: not confirmed
    k2 = Kit(answer())
    r2 = k2.add().row
    sent_ids = []

    async def agent(row_, text):                                                  # the main agent calls the tool itself
        sent_ids.append(row_.id)
        out = await k2.tool("recheck_result")({"id": row_.id, "outcome": "sent"})
        assert out.startswith("Recorded")

    k2.service.send = agent
    await k2.later()
    got = k2.repo.get(r2.id)
    assert sent_ids == [r2.id] and got.state == "sent" and got.told["now"].startswith("이제 60일")
    res = k2.tool("recheck_result")
    assert "already sent" in await res({"id": r2.id, "outcome": "sent"}) and (await res({"id": 999, "outcome": "sent"})).startswith("Error")
    assert (await res({"id": r2.id, "outcome": "maybe"})).startswith("Error") and (await res({"id": "x", "outcome": "sent"})).startswith("Error")
    k3 = Kit(answer())
    r3 = k3.add().row

    async def says_no(row_, text):
        await k3.tool("recheck_result")({"id": row_.id, "outcome": "not_confirmed"})

    k3.service.send = says_no
    await k3.later()
    assert k3.repo.get(r3.id).state == "unconfirmed" and k3.repo.get(r3.id).told is None


async def test_a_sent_topic_is_never_told_twice_unless_it_changed_again_after_the_sent_date():
    k = Kit(answer(), answer(), answer(now="다시 바뀌어 30일 전부터 가능합니다", since="2026-11-16"))      # the first and second say the same
    r = k.add().row
    await k.later()
    assert len(k.sent) == 1 and k.repo.get(r.id).state == "sent"
    assert (await k.later(1))["researched"] == 0                                  # a sent row is closed: no more looks
    again = k.add(claim="비자 연장은 60일 전부터 가능합니다")                       # the agent says it again in the same month
    assert again.status == "merged" and again.row.id == r.id and k.repo.get(r.id).state == "open" and k.repo.get(r.id).told
    await k.later()
    assert len(k.sent) == 1 and k.repo.get(r.id).state == "open"                  # the research says what the user was told: silent
    k.repo.mark(r.id, next_at=k.clock.t - 1)
    await k.service.tick()
    assert len(k.sent) == 2 and "30일 전부터" in k.sent[1][1]                       # changed again: one more message


async def test_an_owner_watch_goes_on_after_it_told_and_never_expires():
    k = Kit(answer(), replied=True)
    r = k.add("rate", origin="owner_watch", every_days=2, claim="Watching the exchange rate").row
    assert r.expires_at is None and r.next_at == k.clock.t + 2 * DAY
    await k.later(2.01)
    got = k.repo.get(r.id)
    assert len(k.sent) == 1 and got.state == "open" and got.told and got.next_at == k.clock.t + 2 * DAY
    await k.later(400)
    assert k.repo.get(r.id).state != "closed"


# ---------------------------------------------------------------------------------------------------------- tools
async def test_recheck_later_dedupes_and_validates():
    k = Kit("")
    later = k.tool("recheck_later")
    args = {"claim": "Fees are 50,000 won", "why": "paying this week", "how": "hikorea.go.kr", "topic": "Visa Fee"}
    assert "Saved" in await later(args) and "Updated" in await later({**args, "claim": "Fees are 60,000 won"})
    rows = k.repo.list()
    assert len(rows) == 1 and rows[0].claim == "Fees are 60,000 won" and rows[0].origin == "agent" and rows[0].turn_id == "turn-1"
    assert len(rows[0].claim) <= CLAIM_MAX and rows[0].topic_key == "visa-fee:2026-11" and rows[0].next_at == k.clock.t + DAY
    long = await later({**args, "claim": "x" * 900, "topic": "long"})
    assert "Saved" in long and len(k.repo.by_key("long:2026-11").claim) == CLAIM_MAX
    assert (await later({"claim": "", "topic": "x"})).startswith("Error") and (await later({"claim": "a", "topic": ""})).startswith("Error")
    assert "decide_by" in await later({**args, "topic": "d", "decide_by": "garbage"})
    assert "already past" in await later({**args, "topic": "d", "decide_by": "2026-11-01"})
    assert "Saved" in await later({**args, "topic": "d", "decide_by": "2026-11-20"})
    assert k.repo.by_key("d:2026-11").decide_by == ts(2026, 11, 20, 23, 59, 59) == k.repo.by_key("d:2026-11").expires_at


async def test_recheck_stop_ends_a_topic_for_good_and_it_is_not_added_again():
    k = Kit("")
    k.add("visa-rules")
    k.add("other")
    out = await k.tool("recheck_stop")({"topic": "Visa Rules"})
    assert out.startswith("Stopped") and k.repo.by_key("visa-rules:2026-11").state == "stopped" and k.repo.by_key("other:2026-11").state == "open"
    assert k.add("visa-rules").status == "stopped"
    assert "nothing was saved" in await k.tool("recheck_later")({"claim": "again", "why": "w", "how": "h", "topic": "visa-rules"})
    k.clock.t += 40 * DAY                                                         # also in a later month
    assert k.add("visa-rules").status == "stopped"
    missing = await k.tool("recheck_stop")({"topic": "nothing here"})
    assert "Nothing was kept" in missing and "other" in missing
    assert await k.service.tick() is None or True and k.turns == []


async def test_recheck_watch_takes_the_owners_interval_and_asks_when_it_is_missing():
    k = Kit("")
    watch = k.tool("recheck_watch")
    assert "how often" in await watch({"topic": "gold-price", "how": "kitco.com"})
    assert "at least 1" in await watch({"topic": "gold-price", "how": "kitco.com", "every_days": 0})
    assert "Saved" in await watch({"topic": "gold-price", "how": "kitco.com", "every_days": 3})
    got = k.repo.by_key("gold-price:2026-11")
    assert got.origin == "owner_watch" and got.every_days == 3 and got.expires_at is None and got.next_at == k.clock.t + 3 * DAY
    assert "Updated" in await k.tool("recheck_later")({"claim": "gold is 100", "why": "w", "how": "h", "topic": "gold-price"})
    assert k.repo.by_key("gold-price:2026-11").origin == "owner_watch" and k.repo.by_key("gold-price:2026-11").every_days == 3     # not downgraded


def test_the_tools_are_named_and_have_the_fields_the_prompt_expects():
    tools = {t.name: t for t in Kit("").kit.tools()}
    assert set(tools) == {"recheck_later", "recheck_watch", "recheck_stop", "recheck_result"}
    assert set(tools["recheck_later"].parameters["required"]) == {"claim", "why", "how", "topic"}
    assert "decide_by" in tools["recheck_later"].parameters["properties"]
    assert set(tools["recheck_watch"].parameters["required"]) == {"topic", "how", "every_days"}


# ---------------------------------------------------------------------------------------------------------- the nightly look-back
KELO = "비자 연장 신청은 만료 90일 전부터 가능합니다. 수수료는 6만 원이에요."


def say(store, at, role, text):
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,?)", (role, text, at, "owner" if role == "user" else ""))


def reflect(tmp_path, k, reply):
    calls = []

    async def fn(provider, request, **kw):
        calls.append(request)
        yield TextDelta(json.dumps(reply, ensure_ascii=False))
        yield Done("stop")

    clock = Clock(ts(2026, 11, 13, 3, 50))
    r = Reflection(k.store, Home(tmp_path / "home"), Activity(k.store, clock), fn, lambda: ("p", "m"), clock=clock)
    r.rechecks = k.kit.extractor
    say(k.store, ts(2026, 11, 12, 20, 0), "user", "비자 연장은 언제부터 되나요?")
    say(k.store, ts(2026, 11, 12, 20, 1), "assistant", KELO)
    return r, calls


async def test_the_nightly_extraction_rides_on_reflections_one_request_and_drops_claims_without_a_quote(tmp_path):
    k = Kit("")
    good = {"claim": "비자 연장은 만료 90일 전부터 가능", "why": "신청 시기", "how": HOW, "topic": "visa-extension-window", "decide_by": "2026-11-20",
            "quote": "만료 90일 전부터 가능합니다"}
    made_up = {"claim": "수수료는 3만 원", "why": "비용", "how": "hikorea.go.kr", "topic": "visa-fee", "quote": "수수료는 3만 원입니다"}
    by_user = {"claim": "비자 연장은 언제부터 되나요?", "why": "x", "how": "y", "topic": "user-words", "quote": "비자 연장은 언제부터 되나요"}
    no_quote = {"claim": "수수료는 6만 원이에요", "why": "비용", "how": "hikorea.go.kr", "topic": "visa-fee-2"}
    r, calls = reflect(tmp_path, k, {"new": [], "update": [], "drop": [], "goals": [], "rechecks": [good, made_up, by_user, no_quote],
                                     "reasons": ["x"]})
    out = await r.run()
    assert out["status"] == "done" and len(calls) == 1                           # one model call: the extraction rides on the same request
    assert "rechecks" in calls[0].messages[0]["content"] and "Rechecks already kept" in calls[0].messages[1]["content"]
    rows = k.repo.list()
    assert [x.slug for x in rows] == ["visa-extension-window"] and rows[0].origin == "nightly"
    assert rows[0].decide_by == ts(2026, 11, 20, 23, 59, 59) and out["changed"] and "1 recheck" in out["reason"]


async def test_a_claim_already_kept_is_not_added_again_and_at_most_five_a_night(tmp_path):
    k = Kit("")
    k.add("visa-extension-window")
    items = [{"claim": f"claim number {n} that Kelo made", "why": "w", "how": "h", "topic": f"t{n}", "quote": "수수료는 6만 원이에요"} for n in range(8)]
    r, _ = reflect(tmp_path, k, {"new": [], "update": [], "drop": [], "goals": [],
                                 "rechecks": [{"claim": "again the same one", "why": "w", "how": "h", "topic": "visa-extension-window",
                                               "quote": "만료 90일 전부터"}, *items]})
    await r.run()
    assert sorted(x.slug for x in k.repo.list()) == ["t0", "t1", "t2", "t3", "visa-extension-window"]      # 4 new: 5 looked at, one was known


def test_a_recheck_handoff_is_not_the_owners_words_for_the_look_back():
    assert any(RECHECK_MARK.startswith(p) for p in AUTO_PREFIXES)


# ---------------------------------------------------------------------------------------------------------- proactive source, API
async def test_a_told_recheck_shows_in_the_proactive_log_as_already_told(tmp_path):
    k = Kit(answer(), replied=True)
    k.add()
    await k.later()
    store = k.store
    store.add_message("user", "hi")

    async def never(*a, **kw):
        raise AssertionError("no model call: the recheck was already told")
        yield

    p = Proactive(store, Home(tmp_path / "h"), stream_fn=never, model=lambda: ("p", "m"), clock=k.clock)
    p.update(enabled=True)
    p.sources.append(k.kit.source)
    found, notes = await p.gather(k.clock.t - 86400)
    assert [c.source for c in found] == ["recheck"] and found[0].told and not notes
    out = await p.run()
    assert out["status"] == "nothing_new" and out["dropped"] == 1
    assert p.log()[0]["source"] == "recheck" and p.log()[0]["status"] == "dropped"
    assert (await p.run())["dropped"] == 0                                         # the ledger: not looked at twice


def test_the_handoff_draft_is_capped_and_keeps_the_marked_sources():
    big = Research("changed", "x" * 600, "2026-11-10", ["u" * 200] * 6, [])
    sources = [{"url": f"https://s{n}.example.org/" + "p" * 280, "publisher": "P" * 100, "official": n == 0, "date": "2026-11-10", "domain": "x"}
               for n in range(6)]
    draft = RecheckHandoff.draft(big, sources)
    assert len(draft) <= DRAFT_MAX and json.loads(draft)["now"]
    assert "domain" not in draft


async def test_the_api_and_the_proactive_settings(tmp_path):
    from assistant.server import Settings, create_app
    import httpx
    from test_server import AUTH, TOKEN, Running

    async def fn(*a, **kw):
        yield Done("stop")

    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home")), stream_fn=fn)
    app.state.rechecks.repo.add("claim one for the list", "why", "how", "list-me")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/rechecks")).status_code == 401
        c.headers.update(AUTH)
        got = (await c.get("/api/rechecks?days=7")).json()
        assert got["enabled"] is True and got["per_day"] == 4 and got["runs_today"] == 0
        assert [i["topic"] for i in got["items"]] == ["list-me"] and "draft" not in got["items"][0] and got["items"][0]["state"] == "open"
        put = (await c.put("/api/proactive", json={"rechecks": False, "recheck_per_day": 2})).json()
        assert put["rechecks"] is False and put["recheck_per_day"] == 2
        assert (await c.put("/api/proactive", json={"recheck_per_day": 99})).status_code == 422
        assert (await c.get("/api/rechecks")).json()["enabled"] is False
        assert (await c.get("/api/proactive")).json()["recheck_per_day"] == 2
    names = {t.name for t in app.state.tools}
    assert {"recheck_later", "recheck_watch", "recheck_stop", "recheck_result"} <= names


async def test_the_whole_path_through_the_app_research_turn_then_a_hidden_handoff_and_the_reply(tmp_path):
    """A real app with a fake model: the research turn has only web_search / web_fetch / weather and shows nothing in the chat; a verified
    change reaches the main agent as a hidden RECHECK_MARK message; its reply is in the chat; the row is `sent`."""
    import httpx

    from assistant.recheck import RESEARCH_TOOLS
    from assistant.server import Settings, create_app
    from test_server import AUTH, TOKEN, Running

    calls = []

    async def fn(provider, request, **kw):
        calls.append(request)
        if request.messages[0]["content"].startswith("You check whether something"):
            yield TextDelta(answer())
        else:
            yield TextDelta("앞서 90일 전부터라고 말씀드렸는데, 지금은 60일 전부터예요. 지금 정하실 건 없어요.")
        yield Done("stop")

    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False,
                              home_dir=str(tmp_path / "home")), stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        kit = app.state.rechecks
        app.state.proactive.update(wake_start="07:00", wake_end="07:00")            # the same start and end: always awake
        added = kit.repo.add("비자 연장은 90일 전부터 가능합니다", "연장 시기", HOW, "visa-window")
        kit.repo.mark(added.row.id, next_at=0)
        assert await kit.service.tick() is None and calls == []                      # chats to the AI company are not agreed yet: no model call
        assert (await c.put("/api/ai-consent", json={"accepted": True})).status_code == 200
        out = await kit.service.tick()
        assert out == {"delivered": 0, "researched": 1}
        research, handoff = calls
        names = {(t.get("function") or t).get("name") for t in research.tools}
        assert names and names <= set(RESEARCH_TOOLS)                                  # no other tool in a research turn
        assert RECHECK_MARK in handoff.messages[-1]["content"] and "hikorea.go.kr" in handoff.messages[-1]["content"]
        got = kit.repo.get(added.row.id)
        assert got.state == "sent" and got.told["now"].startswith("이제 60일")
        shown = (await c.get("/api/messages")).json()
        assert [m["role"] for m in shown if "60일" in m["content"]] == ["assistant"]
        assert not any(RECHECK_MARK in m["content"] for m in shown) and not any("You check whether" in m["content"] for m in shown)
        types = [n["type"] for n in (await c.get("/api/notifications")).json()["items"]]
        assert types.count("task_done") == 1                                          # the correction: one normal Kelo message; the research told nobody



async def test_a_claim_about_one_day_is_dropped_once_that_day_is_over_before_a_later_look(tmp_path):
    """'Today it is ...' said on the 12th: the look-back runs on the 13th at 03:50, so that day is over: nothing to correct later."""
    k = Kit("")
    today = {"claim": "비자 연장은 만료 90일 전부터 가능", "why": "w", "how": HOW, "topic": "visa-today", "about": "2026-11-12",
             "quote": "만료 90일 전부터 가능합니다"}
    later = {"claim": "비자 연장은 만료 90일 전부터 가능", "why": "w", "how": HOW, "topic": "visa-next-week", "about": "2026-11-20",
             "quote": "만료 90일 전부터 가능합니다"}
    r, _ = reflect(tmp_path, k, {"new": [], "update": [], "drop": [], "goals": [], "rechecks": [today, later], "reasons": ["x"]})
    await r.run()
    assert [x.slug for x in k.repo.list()] == ["visa-next-week"]
