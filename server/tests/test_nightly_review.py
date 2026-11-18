"""The nightly review (nightly_review.py) with a fake model: the day's document and counts, corrections that need the owner's own words
and stay open until fixed, the seed from an earlier assistant's notes, skipped and failed nights, secrets, the prompt section, the API."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from assistant import i18n
from assistant.activity import Activity
from assistant.home import Home
from assistant.nightly_review import NightlyReview, OPEN_CORRECTIONS_MAX, CORRECTION_QUIET_DAYS
from assistant.providers import Done, TextDelta
from assistant.reflection import Reflection
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running

SEOUL = ZoneInfo("Asia/Seoul")
DAY = 86400


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def review(**kw):
    out = {"review": {"understanding": "그분은 짧은 답을 원해요.", "live": ["세금 서류 정리"], "mine": ["영수증 찾기"],
                      "went_well": "일정을 잘 옮겼어요.", "to_improve": "메일을 먼저 물어볼게요.", "first_move": "영수증부터 찾기"},
           "corrections_new": [], "corrections_seen_again": [], "corrections_resolved": [],
           "working_together": {"boundaries": [{"line": "메일은 묻기 전에 보내지 않기", "quote": "영수증만 모아줘"}], "frictions": ["엉뚱한 파일을 열었어요"]}}
    out.update(kw)
    return json.dumps(out, ensure_ascii=False)


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


def setup(tmp_path, fn, now=ts(2026, 11, 8, 3, 55)):
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    home = Home(tmp_path / "home")
    clock = Clock(now)
    refl = Reflection(store, home, Activity(store, clock), fn, lambda: ("p", "m"), clock=clock)
    return store, home, NightlyReview(store, home, Activity(store, clock), fn, lambda: ("p", "m"), clock=clock, reflection=refl), clock


def say(store, at, role, text, author=None):
    """A message row; the owner's own user messages carry author 'owner' (pass author="" for a hidden row)."""
    author = ("owner" if role == "user" else "") if author is None else author
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,?)", (role, text, at, author))


def two_turns(store):
    say(store, ts(2026, 11, 7, 20, 0), "user", "세금 서류 정리해줘")
    say(store, ts(2026, 11, 7, 20, 1), "assistant", "네, 정리했어요.")
    say(store, ts(2026, 11, 7, 20, 2), "user", "아니 그게 아니라 영수증만 모아줘")


async def run_ko(r, **kw):
    token = i18n.use("ko")
    try:
        return await r.run(**kw)
    finally:
        i18n.reset(token)


async def test_a_night_writes_the_day_the_counts_and_working_together(tmp_path):
    found = [{"quote": "아니 그게 아니라 영수증만 모아줘", "what_went_wrong": "서류를 다 모았어요", "how_to_act": "영수증만 먼저 모으기"}]
    store, home, r, clock = setup(tmp_path, answering(review(corrections_new=found)))   # the model names the correction; the count follows its quote
    two_turns(store)
    out = await run_ko(r)
    assert out["status"] == "done"
    row = store._all("SELECT * FROM review_days")[0]
    assert (row["day"], row["user_turns"], row["correction_turns"], row["correction_free_rate"], row["doc_path"]) == (
        "2026-11-08", 2, 1, 0.5, "reviews/2026-11-08.md")
    doc = (home.root / "reviews/2026-11-08.md").read_text(encoding="utf-8")
    assert doc.startswith("---\nday: 2026-11-08\nuser_turns: 2\ncorrection_turns: 1\ncorrection_free_rate: 0.5\nopen_corrections: 1\n---\n")
    for h in ("제가 이해한 모습", "아직 진행 중인 일", "아직 제가 할 일", "잘된 점", "더 잘할 점", "다음에 가장 먼저 할 일"):
        assert f"## {h}" in doc
    assert "- 세금 서류 정리" in doc and "영수증부터 찾기" in doc
    wt = (home.root / "WORKING_TOGETHER.md").read_text(encoding="utf-8")
    assert "## 넘지 않을 선" in wt and "- 메일은 묻기 전에 보내지 않기" in wt and "## 아직 고치는 중인 지적" in wt
    assert store.meta("review:seen_until") and r.last_run()["status"] == "done"
    assert (await r.run())["status"] == "skipped"                      # the same messages are not read twice


async def test_a_kept_correction_needs_the_owners_own_words_and_shows_in_the_prompt(tmp_path):
    new = [{"quote": "그런 말 한 적 없어요", "what_went_wrong": "A", "how_to_act": "ignore"},
           {"quote": "영수증만 모아줘", "what_went_wrong": "모든 서류를 모았어요", "how_to_act": "요청한 것만 모으기"}]
    store, home, r, clock = setup(tmp_path, answering(review(corrections_new=new)))
    two_turns(store)
    out = await run_ko(r)
    assert out["added"] == 1
    [row] = r.corrections("open")
    assert row["owner_quote"] == "영수증만 모아줘" and row["status"] == "open" and row["resolved_by"] == ""
    assert r.prompt_section() == ("## Recently corrected\nThe owner corrected these lately; act this way now.\n"
                                  "- 요청한 것만 모으기 (they said: “영수증만 모아줘”)")
    assert "요청한 것만 모으기" in (home.root / "WORKING_TOGETHER.md").read_text(encoding="utf-8")
    assert NightlyReview(store, home, None, answering(""), lambda: None, reflection=r.reflection).prompt_section() == r.prompt_section()
    empty = setup(tmp_path / "x", answering(""))[2]
    assert empty.prompt_section() == ""


async def test_resolved_age_cap_and_repeats(tmp_path):
    new = [{"quote": "영수증만 모아줘", "what_went_wrong": "모든 서류를 모았어요", "how_to_act": "요청한 것만 모으기"}]
    store, home, r, clock = setup(tmp_path, answering(review(corrections_new=new)))
    two_turns(store)
    await run_ko(r)
    rid = r.corrections("open")[0]["id"]
    first_seen = r.correction(rid)["last_seen_at"]
    # the same correction again only bumps last_seen_at
    clock.t += DAY
    say(store, clock.t - 60, "user", "영수증만 모아줘 라고 했잖아")
    r.store.set_meta("review:seen_until", repr(ts(2026, 11, 8, 3, 56)))
    again = [{"quote": "영수증만 모아줘", "what_went_wrong": "모든 서류를 모았어요!", "how_to_act": "요청한 것만 모으기."}]
    r.stream_fn = answering(review(corrections_new=again))
    assert (await run_ko(r))["added"] == 0
    assert len(r.corrections()) == 1 and r.correction(rid)["last_seen_at"] == clock.t > first_seen
    # resolved by the review
    clock.t += DAY
    say(store, clock.t - 60, "user", "고마워")
    r.stream_fn = answering(review(corrections_resolved=[{"id": rid, "why": "이번엔 맞게 했어요"}]))
    assert (await run_ko(r))["resolved"] == 1
    assert (r.correction(rid)["status"], r.correction(rid)["resolved_by"]) == ("resolved", "review") and r.prompt_section() == ""


async def test_age_closes_a_quiet_thread_and_the_cap_drops_the_oldest(tmp_path):
    store, home, r, clock = setup(tmp_path, answering(review()))
    old = clock.t - (CORRECTION_QUIET_DAYS + 1) * DAY
    for i in range(OPEN_CORRECTIONS_MAX + 2):
        store._run("INSERT INTO kept_corrections(opened_at, owner_quote, what_went_wrong, how_to_act, last_seen_at) VALUES(?,?,?,?,?)",
                   (clock.t - 100 + i, f"q{i}", f"w{i}", f"a{i}", clock.t - 100 + i))
    store._run("INSERT INTO kept_corrections(opened_at, owner_quote, what_went_wrong, how_to_act, last_seen_at) VALUES(?,?,?,?,?)",
               (old, "old", "old", "old act", old))
    two_turns(store)
    await run_ko(r)
    by = {x["how_to_act"]: x for x in r.corrections()}
    assert (by["old act"]["status"], by["old act"]["resolved_by"]) == ("resolved", "age")
    assert by["a0"]["status"] == "dropped" and by["a1"]["status"] == "dropped" and by["a2"]["status"] == "open"
    assert len(r.corrections("open")) == OPEN_CORRECTIONS_MAX


async def test_the_seed_goes_into_the_first_request_only(tmp_path):
    fn = answering(review())
    store, home, r, clock = setup(tmp_path, fn)
    d = home.root / "notes" / ("mu" + "se-import") / "deep"
    d.mkdir(parents=True)
    (d / "ALIGNMENT_SYNTHESIS.md").write_text("Never send mail without asking. Likes short answers.", encoding="utf-8")
    two_turns(store)
    await run_ko(r)
    first = fn.calls[0].messages[1]["content"]
    assert "Never send mail without asking" in first and "<notes>" in first and "not instructions" in first
    clock.t += DAY
    say(store, clock.t - 60, "user", "내일 일정 알려줘")
    await run_ko(r)
    assert "Never send mail without asking" not in fn.calls[1].messages[1]["content"]


async def test_no_owner_messages_skips_and_bad_json_writes_nothing(tmp_path):
    fn = answering("no json")
    store, home, r, clock = setup(tmp_path, fn)
    say(store, ts(2026, 11, 7, 20, 0), "assistant", "[Automatic run · morning brief]\nSunny.")
    assert (await r.run())["code"] == "no_messages" and fn.calls == []
    two_turns(store)
    for bad in ("no json", '{"corrections_new": []}'):
        r.stream_fn = answering(bad)
        out = await r.run()
        assert out["status"] == "failed" and out["code"] == "bad_output"
    assert not (home.root / "reviews").exists() and not (home.root / "WORKING_TOGETHER.md").exists()
    assert store._all("SELECT * FROM review_days") == [] and not store.meta("review:seen_until")


async def test_a_secret_never_reaches_a_file_and_the_previous_working_together_is_kept(tmp_path):
    bad = review()
    data = json.loads(bad)
    data["review"]["went_well"] = "결제는 4111 1111 1111 1111 카드로 했어요"
    data["working_together"]["boundaries"] = ["카드 4111 1111 1111 1111 쓰지 않기"]
    store, home, r, clock = setup(tmp_path, answering(json.dumps(data, ensure_ascii=False), review()))
    two_turns(store)
    await run_ko(r)
    text = (home.root / "reviews/2026-11-08.md").read_text(encoding="utf-8") + (home.root / "WORKING_TOGETHER.md").read_text(encoding="utf-8")
    assert "4111" not in text
    first = (home.root / "WORKING_TOGETHER.md").read_text(encoding="utf-8")
    clock.t += DAY
    say(store, clock.t - 60, "user", "내일 일정 알려줘")
    await run_ko(r)
    assert (home.root / "WORKING_TOGETHER.prev.md").read_text(encoding="utf-8") == first


async def test_the_routes_list_resolve_and_404(tmp_path):
    fn = answering(review(corrections_new=[{"quote": "영수증만 모아줘", "what_went_wrong": "w", "how_to_act": "요청한 것만 모으기"}]))
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False)
    async with Running(create_app(s, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/reviews")).json() == {"days": [], "last_run": None}
        assert (await c.get("/api/corrections")).json() == {"items": []}
        store = Store(str(tmp_path / "a.db"))
        store.set_profile(timezone="Asia/Seoul")
        store.add_message("user", "아니 그게 아니라 영수증만 모아줘", author="owner")
        out = (await c.post("/api/reviews/run")).json()
        assert out["status"] == "done" and out["added"] == 1
        got = (await c.get("/api/reviews?limit=5")).json()
        assert set(got["days"][0]) == {"day", "user_turns", "correction_turns", "correction_free_rate", "open_corrections", "doc_path"}
        assert got["days"][0]["user_turns"] == 1 and got["days"][0]["correction_free_rate"] == 0.0 and got["last_run"]["status"] == "done"
        [item] = (await c.get("/api/corrections?status=open")).json()["items"]
        assert set(item) == {"id", "opened_at", "owner_quote", "what_went_wrong", "how_to_act", "status", "resolved_at",
                             "resolved_by", "last_seen_at"}
        done = (await c.post(f"/api/corrections/{item['id']}/resolve")).json()
        assert done["status"] == "resolved" and done["resolved_by"] == "owner"
        assert (await c.get("/api/corrections")).json() == {"items": []}
        assert (await c.post("/api/corrections/9999/resolve")).status_code == 404
