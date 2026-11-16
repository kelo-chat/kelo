"""Learning the user's preferences each night (reflection.py) with a fake model: skipping quiet nights, rules stored in the
database with their status, what goes into every turn's prompt, invalid output, the signals counted in code, the API, the migration
of an older written guide, and no names copied from another product."""

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant.activity import RESTART_RESULT, Activity
from assistant.compaction import est_tokens
from assistant.home import Home, HomeError
from assistant.prompt import PromptBuilder
from assistant.providers import Done, TextDelta
from assistant import i18n
from assistant.reflection import INPUT_TOKENS, PROMPT, Reflection, is_correction, parse_output, tidy_evidence, tidy_rule
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running

ROOT = Path(__file__).resolve().parents[2]
SEOUL = ZoneInfo("Asia/Seoul")


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


SAID = "앞으로 결론만 말해줘. 날짜는 달력 보고 확인해"   # the owner's own words both rules of reply() quote


def reply(new=(("결론부터 말하기", "앞으로 결론만 말해줘", 0.9), ("날짜는 달력에서 확인하기", "날짜는 달력 보고 확인해", 0.6)),
          update=(), drop=(), reasons=("새 규칙 두 개",)):
    return json.dumps({"new": [{"rule": r, "quote": q, "confidence": c} for r, q, c in new], "update": list(update),
                       "drop": list(drop), "reasons": list(reasons)}, ensure_ascii=False)


def answering(*texts):
    calls = []

    async def fn(provider, request, **kw):
        calls.append(request)
        text = texts[min(len(calls) - 1, len(texts) - 1)]
        if isinstance(text, Exception):
            raise text
        yield TextDelta(text)
        yield Done("stop")
    fn.calls = calls
    return fn


def setup(tmp_path, fn, now=ts(2026, 11, 8, 3, 50)):
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    home = Home(tmp_path / "home")
    clock = Clock(now)
    return store, home, Reflection(store, home, Activity(store, clock), fn, lambda: ("p", "m"), clock=clock), clock


def say(store, at, role, text):
    store._run("INSERT INTO messages(role, content, created_at) VALUES(?,?,?)", (role, text, at))


# ---------------------------------------------------------------------------------------------------------- the run
async def test_a_night_without_new_user_messages_is_skipped_without_a_model_call(tmp_path):
    fn = answering(reply())
    store, home, r, clock = setup(tmp_path, fn)
    out = await r.run()
    assert out["status"] == "skipped" and fn.calls == [] and r.last_run()["status"] == "skipped"
    say(store, ts(2026, 11, 7, 20, 0), "assistant", "[Automatic run · morning brief]\nSunny today.")   # a scheduled run's answer only
    say(store, ts(2026, 11, 7, 20, 1), "user", "[자동 실행 · 아침 브리핑]\n오늘 날씨")                      # a run saved by an older version
    assert (await r.run())["status"] == "skipped" and fn.calls == []


async def test_new_rules_are_proposed_and_only_sure_ones_reach_the_prompt(tmp_path):
    fn = answering(reply())
    store, home, r, clock = setup(tmp_path, fn)
    say(store, ts(2026, 11, 7, 21, 0), "user", "[Sat 2026-11-07 21:00:00 KST] [client_timezone=Asia/Seoul]\n" + SAID)
    out = await r.run()
    assert out["status"] == "done" and out["changed"] and out["reason"].startswith("2 new, 0 updated, 0 dropped")
    assert fn.calls[0].tools == [] and "client_timezone" not in fn.calls[0].messages[1]["content"]
    rules = r.rules()
    assert [(x["rule"], x["status"], x["confidence"], x["date"]) for x in rules] == [
        ("결론부터 말하기", "proposed", 0.9, "2026-11-08"), ("날짜는 달력에서 확인하기", "proposed", 0.6, "2026-11-08")]
    assert rules[0]["evidence"] == "“앞으로 결론만 말해줘”"                  # the owner's own words, as the reason
    section = r.prompt_section()
    assert section.startswith("## Learned Preferences") and "- 결론부터 말하기" in section and "달력" not in section   # 0.6 < 0.8
    assert (await r.run())["status"] == "skipped"                     # the same messages are not read twice


async def test_the_users_decision_is_final_and_the_model_can_only_tune_proposed_rules(tmp_path):
    store, home, r, clock = setup(tmp_path, answering(""))
    a = r.add_rule("짧게 답하기", "직접 말함", 0.9)
    b = r.add_rule("이모지 쓰기", "추측", 0.5)
    c = r.add_rule("표로 정리하기", "추측", 0.7)
    r.set_status(a, "accepted")
    r.set_status(b, "rejected")
    assert r.add_rule("이모지 쓰기!", "again") is None                  # a rejected rule is not proposed again
    added, updated, dropped = r.apply({"new": [{"rule": "짧게 답하기", "quote": "짧게 답해", "confidence": 0.9}],
                                       "update": [{"id": a, "confidence": 0.1}, {"id": c, "confidence": 0.85, "quote": "앞으로도 표로 줘"}],
                                       "drop": [a, b, 999]}, ["짧게 답해", "앞으로도 표로 줘!"])
    assert (added, updated, dropped) == (0, 1, 0)
    by = {x["id"]: x for x in r.rules()}
    assert by[a]["status"] == "accepted" and by[a]["confidence"] == 0.9 and by[b]["status"] == "rejected"
    assert by[c]["confidence"] == 0.85 and by[c]["evidence"] == "“앞으로도 표로 줘”"
    used = r.prompt_section()
    assert "짧게 답하기" in used and "표로 정리하기" in used and "이모지" not in used
    r.apply({"drop": [c]})
    assert c not in {x["id"] for x in r.rules()}
    with pytest.raises(HomeError):
        r.set_status(a, "maybe")
    with pytest.raises(KeyError):
        r.set_status(12345, "accepted")


@pytest.mark.parametrize("bad", ["no json at all", "[1, 2]", '{"guide_markdown": "## a"}', '{"new": "x"}'])
async def test_invalid_output_changes_nothing_and_reads_the_same_day_again(tmp_path, bad):
    fn = answering(bad)
    store, home, r, clock = setup(tmp_path, fn)
    say(store, ts(2026, 11, 7, 21, 0), "user", "내일 일정 정리해줘")
    out = await r.run()
    assert out["status"] == "failed" and r.rules() == []
    assert (await r.run())["status"] == "failed" and len(fn.calls) == 2       # not marked as seen


async def test_no_model_and_a_failing_model_change_nothing(tmp_path):
    store, home, r, clock = setup(tmp_path, answering(RuntimeError("boom")))
    say(store, ts(2026, 11, 7, 21, 0), "user", "안녕")
    assert (await r.run())["status"] == "failed" and r.rules() == []
    r.model = lambda: None
    assert (await r.run())["status"] == "pending"


async def test_the_list_stays_short(tmp_path):
    store, home, r, clock = setup(tmp_path, answering(""))
    for i in range(45):
        r.add_rule(f"규칙 번호 {i}", "", round(0.3 + i / 100, 2))
    r.apply({"new": []})
    assert len(r.rules()) == 40 and min(x["confidence"] for x in r.rules()) >= 0.35


async def test_the_request_stays_small_even_after_a_long_day(tmp_path):
    fn = answering(reply())
    store, home, r, clock = setup(tmp_path, fn)
    home.write("MEMORY.md", "# MEMORY.md\n" + "- 기억할 것 하나 둘 셋\n" * 3000)
    home.write("user.md", "# user.md\n" + "- 사용자 정보\n" * 3000)
    for i in range(600):
        say(store, clock.t - 80_000 + i * 60, "user" if i % 2 == 0 else "assistant", f"메시지 {i} " + "긴 내용 " * 300)
    messages, info = r.build_request(r.messages_since(r._since()), r._since())
    assert info["tokens"] <= INPUT_TOKENS and est_tokens(messages[0]["content"]) + est_tokens(messages[1]["content"]) <= INPUT_TOKENS
    assert "메시지 598" in messages[1]["content"] and "earlier messages left out" in messages[1]["content"]
    assert (await r.run())["status"] == "done"


# ---------------------------------------------------------------------------------------------------------- what the apps show
async def test_last_run_has_structured_fields_and_the_free_text_is_only_a_log(tmp_path):
    fn = answering(reply())
    store, home, r, clock = setup(tmp_path, fn)
    out = await r.run()
    assert (out["status"], out["code"], out["added"]) == ("skipped", "no_messages", 0)
    say(store, ts(2026, 11, 7, 21, 0), "user", SAID)
    out = await r.run()
    assert (out["status"], out["code"], out["added"], out["updated"], out["dropped"], out["held"]) == ("done", "", 2, 0, 0, 0)
    assert r.last_run() == out and set(out) >= {"at", "status", "code", "added", "updated", "dropped", "goals", "held", "changed", "reason"}
    say(store, ts(2026, 11, 8, 3, 0), "user", "하나 더")
    r.stream_fn = answering(RuntimeError("boom"))
    assert (await r.run())["code"] == "model_error"
    r.stream_fn = answering("no json")
    assert (await r.run())["code"] == "bad_output"
    r.model = lambda: None
    assert (await r.run())["code"] == "no_model"
    # a run an older server kept (status and an English reason only) gets the same fields
    store.set_meta("reflection:last_run", json.dumps({"at": 1.0, "status": "done", "changed": True,
                                                      "reason": "2 new, 0 updated, 1 dropped; 사용자가 승인을 3회 거절함"}))
    old = r.last_run()
    assert (old["code"], old["added"], old["updated"], old["dropped"], old["goals"], old["held"]) == ("", 2, 0, 1, 0, 0)
    store.set_meta("reflection:last_run", json.dumps({"at": 1.0, "status": "skipped", "reason": "no new messages from the user"}))
    assert r.last_run()["code"] == "no_messages"


def test_rules_read_as_preferences_not_orders():
    assert tidy_rule("캘린더에 일정을 직접 등록하지 마라") == "캘린더에 일정을 직접 등록하지 않기"
    assert tidy_rule("정기 점검 일정을 등록하지 마세요.") == "정기 점검 일정을 등록하지 않기"
    assert tidy_rule("결론부터 말하라") == "결론부터 말하기" and tidy_rule("답을 짧게 해라") == "답을 짧게 하기"
    assert tidy_rule("Don't add events to my calendar on its own") == "Don't add events to my calendar on its own"
    assert tidy_evidence("근거: 캘린더 일정 등록 허락을 3번 거절했어요") == "캘린더 일정 등록 허락을 3번 거절했어요"
    assert "imperative" not in PROMPT and "never give an order" in PROMPT and "캘린더에 일정을 직접 넣지 않기" in PROMPT


def test_suggestions_an_older_version_wrote_as_orders_are_reworded_at_start(tmp_path):
    store, home, r, clock = setup(tmp_path, answering(""))
    store._run("INSERT INTO learned_rules(rule, evidence, date, confidence, status, updated_at) VALUES(?,?,?,?,?,?)",
               ("캘린더에 일정을 직접 등록하지 마라", "", "2026-11-08", 0.6, "proposed", 0))
    store._run("INSERT INTO learned_rules(rule, evidence, date, confidence, status, updated_at) VALUES(?,?,?,?,?,?)",
               ("짧게 답해라", "", "2026-11-08", 0.9, "accepted", 0))
    Reflection(store, home, r.activity, r.stream_fn, lambda: ("p", "m"), clock=clock)
    assert {x["rule"] for x in r.rules()} == {"캘린더에 일정을 직접 등록하지 않기", "짧게 답해라"}   # the user's own choices stay as they are


async def test_the_request_names_the_app_language(tmp_path):
    fn = answering(reply())
    store, home, r, clock = setup(tmp_path, fn)
    say(store, ts(2026, 11, 7, 21, 0), "user", SAID)
    token = i18n.use("ko")
    try:
        await r.run()
    finally:
        i18n.reset(token)
    assert "Language for rules, evidence and goals: Korean" in fn.calls[0].messages[1]["content"]


# ---------------------------------------------------------------------------------------------------------- only the owner's own words
def deny(act, aid, label="캘린더 일정 등록", site="calendar.google.com"):
    act.log_approval(aid, task_id=None, tool="calendar_create", label=label, site=site, detail="웨이트 트레이닝")
    act.answer_approval(aid, "denied")


def guess(rule="캘린더에 일정을 직접 넣지 않기", quote="캘린더 일정 등록 허락을 3번 거절했어요", **extra):
    return json.dumps({"new": [{"rule": rule, "quote": quote, "evidence": quote, "said": True, "denied": ["D1"], "confidence": 0.9,
                                **extra}], "update": [], "drop": [], "reasons": []}, ensure_ascii=False)


async def test_denied_approvals_alone_never_make_a_rule(tmp_path):
    """The owner denied cards while trying things out: no rule, however sure the model claims to be, and the model is not even
    shown the denials."""
    fn = answering(guess())
    store, home, r, clock = setup(tmp_path, fn, now=ts(2026, 11, 8, 21, 0))
    for day in (8, 9, 10):
        clock.t = ts(2026, 11, day, 20, 0)
        deny(r.activity, f"a{day}")
    clock.t = ts(2026, 11, 10, 21, 0)
    say(store, ts(2026, 11, 10, 20, 30), "user", "운동 일정 정리해줘")
    out = await r.run()
    sent = fn.calls[0].messages[0]["content"] + fn.calls[0].messages[1]["content"]
    assert "캘린더 일정 등록" not in sent and "denied" not in fn.calls[0].messages[1]["content"]
    assert r.rules() == [] and (out["status"], out["added"]) == ("done", 0)   # the "quote" is the model's sentence, not the owner's


async def test_a_rule_stands_on_a_quote_found_in_the_owners_own_messages(tmp_path):
    fn = answering(json.dumps({"new": [
        {"rule": "캘린더에 일정을 직접 넣지 않기", "quote": "앞으로 캘린더에는 직접 넣지 마", "confidence": 0.9},
        {"rule": "답을 표로 정리하기", "quote": "표로 정리해 드릴게요", "confidence": 0.9},      # the assistant's words
        {"rule": "아침 알림 보내지 않기", "quote": "아침 알림은 보내지 말라고 했음", "confidence": 0.9},  # a paraphrase
        {"rule": "결론부터 말하기", "confidence": 0.9},                                           # no quote at all
        {"rule": "결론부터 쓰기", "quote": "응", "confidence": 0.9}], "update": [], "drop": []}, ensure_ascii=False))
    store, home, r, clock = setup(tmp_path, fn)
    say(store, ts(2026, 11, 7, 21, 0), "user", "[Sat 2026-11-07 21:00:00 KST]\n음… 앞으로 캘린더에는, 직접   넣지 마!")
    say(store, ts(2026, 11, 7, 21, 1), "assistant", "알겠어요. 표로 정리해 드릴게요")
    say(store, ts(2026, 11, 7, 21, 2), "user", "[Automatic run · 아침 알림]\n아침 알림은 보내지 말라고 했음")   # not typed by the owner
    say(store, ts(2026, 11, 7, 21, 3), "user", "응")
    out = await r.run()
    assert out["added"] == 1
    assert [(x["rule"], x["evidence"], x["confidence"]) for x in r.rules()] == [
        ("캘린더에 일정을 직접 넣지 않기", "“앞으로 캘린더에는 직접 넣지 마”", 0.9)]
    prompt = fn.calls[0].messages[0]["content"]
    assert '"quote"' in prompt and "from now on" in prompt and "declined" in prompt


def test_find_quote_ignores_punctuation_and_spacing_and_allows_an_ellipsis():
    from assistant.reflection import find_quote
    words = ["From now on, please   keep answers SHORT. Thanks!"]
    assert find_quote("“from now on please keep answers short”", words) == "from now on please keep answers short"
    assert find_quote("From now on … keep answers short", words) == "From now on … keep answers short"
    assert find_quote("from now on keep answers short", words) is None          # a word left out is not their words
    assert find_quote("ok", ["ok"]) is None and find_quote("", words) is None and find_quote(None, words) is None


def test_suggestions_an_older_version_inferred_from_behaviour_go_once_at_start(tmp_path):
    store = Store()
    home = Home(tmp_path / "h")
    rows = [("캘린더에 일정을 직접 등록하지 않기", "캘린더 일정 등록 허락을 3번 거절했어요", 0.7, "proposed"),
            ("정기 점검 일정을 등록하지 않기", "", 0.6, "proposed"),
            ("결론부터 말하기", "앞으로 결론만 말해", 0.9, "proposed"),
            ("이모지 쓰기", "", 0.5, "accepted"), ("표로 정리하기", "", 0.4, "rejected")]
    for rule, ev, conf, status in rows:
        store._run("INSERT INTO learned_rules(rule, evidence, date, confidence, status, updated_at) VALUES(?,?,?,?,?,?)",
                   (rule, ev, "2026-11-08", conf, status, 0))
    store.set_meta("reflection:held", json.dumps([{"id": 1, "rule": "x", "evidence": "", "days": ["2026-11-08"]}]))
    r = Reflection(store, home, None, answering(""), lambda: None)
    assert {x["rule"] for x in r.rules()} == {"결론부터 말하기", "이모지 쓰기", "표로 정리하기"}
    assert store.meta("reflection:held") == "[]"
    r.add_rule("새 제안", "", 0.5)
    Reflection(store, home, None, answering(""), lambda: None)          # once only: later suggestions stay
    assert "새 제안" in {x["rule"] for x in r.rules()}


# ---------------------------------------------------------------------------------------------------------- signals
def test_corrections_are_spotted_in_a_few_languages_and_ordinary_words_are_not():
    for said in ("아니 그게 아니라 금요일이야", "그게 아니고 내일", "틀렸어", "다시 찾아줘", "내가 말했잖아", "No, that's wrong", "no it's tuesday",
                 "That's not what I asked", "not what I meant", "違うよ", "不对"):
        assert is_correction(said), said
    for said in ("아니면 내일은 어때?", "안녕", "고마워 잘했어", "notes for tomorrow", "nothing else", "다시마 사야 해"):
        assert not is_correction(said), said


async def test_only_corrections_are_counted_not_denials_or_failed_tasks(tmp_path):
    fn = answering(reply())
    store, home, r, clock = setup(tmp_path, fn)
    act = r.activity
    say(store, clock.t - 5000, "user", "토요일 일정 알려줘")
    say(store, clock.t - 4900, "assistant", "토요일은 11월 9일이에요.")
    say(store, clock.t - 4800, "user", "아니 그게 아니라 11월 8일이잖아")
    say(store, clock.t - 4700, "assistant", "[Automatic run · 정리]\n아니요")                     # not the user's words
    act.log_approval("a1", task_id=None, tool="confirm_purchase", label="결제 확인 요청", site="shop.example.com", detail="책 1권 15,000원")
    act.answer_approval("a1", "denied")
    act.log_approval("a2", task_id=None, tool="browser_click", label="누르기", site="x.example.com", detail="ok")
    act.answer_approval("a2", "allowed_once")
    t1 = act.start_task("항공권 찾기")
    act.finish_task(t1, "failed", error="사이트가 열리지 않았어요")
    t2 = act.start_task("긴 보고서")
    act.finish_task(t2, "stopped", answer="")
    t3 = act.start_task("재시작된 일")
    store._run("UPDATE tasks SET status = 'stopped', result = ? WHERE id = ?", (RESTART_RESULT, t3))
    act.finish_task(act.start_task("잘 된 일"), "done", answer="끝")
    sig = r.signals(r.messages_since(r._since()), r._since())
    assert [c["user"] for c in sig["corrections"]] == ["아니 그게 아니라 11월 8일이잖아"] and "11월 9일" in sig["corrections"][0]["after"]
    assert set(sig) == {"corrections"}                                  # denials and failed tasks never make a rule
    await r.run()
    sent = fn.calls[0].messages[1]["content"]
    assert "correct the assistant (a word match; judge each yourself): 1" in sent and "결제 확인 요청" not in sent
    assert "항공권 찾기" not in sent


def test_replies_are_read_forgivingly():
    assert parse_output("여기요\n```json\n" + reply() + "\n```")["new"][0]["rule"] == "결론부터 말하기"
    assert parse_output("no json") is None and parse_output("[1, 2]") is None


# ---------------------------------------------------------------------------------------------------------- the prompt
def test_the_learned_rules_go_between_the_stable_and_the_per_turn_parts(tmp_path):
    store, home, r, clock = setup(tmp_path, answering(""))
    pb = PromptBuilder("You are {agent_name}.\n## Style\n", home, None)
    pb.learned = r.prompt_section
    assert "## Learned Preferences" not in pb.build(agent_name="Sage", tz_name="Asia/Seoul")
    r.set_status(r.add_rule("결론부터 말하기", "", 0.5), "accepted")
    a = pb.build(agent_name="Sage", goals=[{"title": "goal-a", "status": "tracking"}], tz_name="Asia/Seoul")
    b = pb.build(agent_name="Sage", goals=[{"title": "goal-b", "status": "tracking"}], tz_name="Asia/Seoul")
    assert a.index("## Style") < a.index("## Learned Preferences") < a.index("## Your Files") < a.index("## This Turn")
    assert a[:a.index("## This Turn")] == b[:b.index("## This Turn")]           # still one cacheable start


def test_an_older_written_guide_becomes_accepted_rules_and_is_kept_as_a_backup(tmp_path):
    home = Home(tmp_path / "h")
    (home.root / "reflection").mkdir()
    (home.root / "reflection" / "GUIDE.md").write_text("## 이렇게 하면 좋아함\n- 결론부터 말하기\n## 피할 것 (선)\n- 묻지 않은 긴 설명\n")
    (home.root / "reflection" / "2026-11-07.md").write_text("오늘은 잘 도왔어요.")
    store = Store()
    r = Reflection(store, home, None, answering(""), lambda: None)
    assert r.migrated == 2 and not (home.root / "reflection").exists()
    rules = r.rules()
    assert [x["rule"] for x in rules] == ["결론부터 말하기", "묻지 않은 긴 설명"] and {x["status"] for x in rules} == {"accepted"}
    assert rules[1]["evidence"] == "from the earlier working guide (피할 것 (선))"
    backup = next((home.root / ".backup").iterdir()) / "reflection"
    assert (backup / "GUIDE.md").is_file() and (backup / "2026-11-07.md").read_text() == "오늘은 잘 도왔어요."
    assert Reflection(store, home, None, answering(""), lambda: None).migrated == 0 and len(r.rules()) == 2


# ---------------------------------------------------------------------------------------------------------- the API
async def test_reflection_api_lists_rules_changes_their_status_and_runs(tmp_path):
    fn = answering(reply())
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False)
    async with Running(create_app(s, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/reflection")).json() == {"enabled": True, "rules": [], "last_run": None, "guide": "",
                                                            "memory": {"last_pass": None, "last_attempt": None}}
        assert (await c.post("/api/reflection/run")).json()["status"] == "skipped" and fn.calls == []
        store = Store(str(tmp_path / "a.db"))
        store.set_profile(timezone="Asia/Seoul")
        store.add_message("user", SAID)
        out = (await c.post("/api/reflection/run")).json()
        assert out["status"] == "done" and out["changed"] and len(fn.calls) == 1
        got = (await c.get("/api/reflection")).json()
        assert [x["rule"] for x in got["rules"]] == ["결론부터 말하기", "날짜는 달력에서 확인하기"] and got["guide"] == "- 결론부터 말하기"
        assert set(got["rules"][0]) == {"id", "rule", "evidence", "date", "confidence", "status"}
        assert got["last_run"]["status"] == "done" and set(got["last_run"]) >= {"at", "status", "reason", "changed"}
        rid = got["rules"][1]["id"]
        changed = (await c.patch(f"/api/reflection/rules/{rid}", json={"status": "accepted"})).json()
        assert changed["status"] == "accepted" and changed["id"] == rid
        assert (await c.get("/api/reflection")).json()["guide"] == "- 날짜는 달력에서 확인하기\n- 결론부터 말하기"
        assert (await c.patch(f"/api/reflection/rules/{rid}", json={"status": "maybe"})).status_code == 422
        assert (await c.patch("/api/reflection/rules/9999", json={"status": "rejected"})).status_code == 404
        assert (await c.put("/api/reflection/guide", json={"text": "x"})).status_code == 410
    s2 = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "b.db"), scheduler=False)
    async with Running(create_app(s2, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/reflection")).status_code == 404                # no agent files on this server


# ---------------------------------------------------------------------------------------------------------- our own words
def test_no_names_or_wording_from_the_reference_app():
    banned = re.compile(r"dream|alignment|ruptur|repair[\s_-]*thread", re.I)
    files = [ROOT / "server" / "content" / "prompts" / "system.md", *sorted((ROOT / "server" / "src" / "assistant").rglob("*.py"))]
    hits = [f"{f.relative_to(ROOT)}:{n}" for f in files for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
            if banned.search(line)]
    assert hits == []


# ---------------------------------------------------------------------------------------------------------- on/off and idle wait
async def test_the_nightly_run_can_be_switched_off_and_waits_until_the_app_is_left_alone(tmp_path):
    fn = answering(reply())
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    home = Home(tmp_path / "home")
    clock = Clock(ts(2026, 11, 8, 3, 50))
    used = [clock.t - 60]                      # someone used the app a minute ago
    slept = []

    async def sleep(s):
        slept.append(s)
        clock.t += s
        if len(slept) == 5:
            used[0] = clock.t - 700            # ...and then put the phone down (over ten minutes ago from here on)

    r = Reflection(store, home, Activity(store, clock), fn, lambda: ("p", "m"), clock=clock, last_use=lambda: used[0], sleep=sleep)
    say(store, ts(2026, 11, 7, 21, 0), "user", "내일 일정 정리해줘")
    assert r.enabled() and r.public()["enabled"] is True
    r.set_enabled(False)
    assert (await r.nightly())["status"] == "skipped" and r.last_run()["reason"] == "switched off" and fn.calls == []
    r.set_enabled(True)
    out = await r.nightly()
    assert len(slept) == 5 and out["status"] == "done" and len(fn.calls) == 1


async def test_a_night_the_app_stays_in_use_is_skipped(tmp_path):
    fn = answering(reply())
    store = Store()
    home = Home(tmp_path / "home")
    clock = Clock(ts(2026, 11, 8, 3, 50))

    async def sleep(s):
        clock.t += s

    r = Reflection(store, home, Activity(store, clock), fn, lambda: ("p", "m"), clock=clock, last_use=lambda: clock.t, sleep=sleep)
    say(store, ts(2026, 11, 7, 21, 0), "user", "안녕")
    out = await r.nightly()
    assert out["status"] == "skipped" and "in use" in out["reason"] and fn.calls == []


async def test_the_switch_over_http(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                              home_dir=str(tmp_path / "home")), tools=[], stream_fn=answering(reply()))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/reflection")).json()["enabled"] is True
        assert (await c.put("/api/reflection/enabled", json={"enabled": "no"})).status_code == 422
        assert (await c.put("/api/reflection/enabled", json={"enabled": False})).json()["enabled"] is False
        assert (await c.get("/api/reflection")).json()["enabled"] is False
