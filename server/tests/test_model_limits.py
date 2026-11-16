"""When the AI model's usage runs out (model_limits.py): the limit error is read apart from an outage and from a bad request, its reset
time is found (the error's own words, Retry-After, the CLI's /usage windows, else a capped backoff), the provider is held (in the
store, so a restart keeps it) and nothing but chat reaches it until then; nearly used-up windows make the background jobs step aside.
Then each job: proactive skips quietly, the memory pass waits for retry_at, a background job pauses and goes on after the reset with
one notice inside the active hours, and the words the user sees in each language. Fake models only; no CLI is run (conftest)."""

import re
import asyncio
import time
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant import i18n, model_limits as ml
from assistant.activity import Activity
from assistant.home import Home
from assistant.memory_upkeep import IDLE_BEFORE_RUN, MemoryUpkeep
from assistant.proactive import Proactive
from assistant.providers import ChatRequest, Done, TextDelta
from assistant.providers.types import ProviderError, QuotaExhausted
from assistant.resume import is_outage
from assistant.server import create_app
from assistant.store import Store
from assistant.usage import metered

from test_agent_profile import app_settings
from test_server import AUTH, Running, follow
from test_workers import Brain, call, delegating, one_worker, say, until

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=SEOUL).timestamp()   # a Monday, 10:00 in Seoul


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def agy_used_up(after="2h3m10s"):
    return ProviderError(f"Antigravity model error: You have exhausted your capacity on this model. Your quota will reset after {after}.")


# ---------------------------------------------------------------------------------------------------------- reading the error
def test_limit_errors_are_told_apart_from_outages_and_bad_requests():
    assert ml.is_limit(agy_used_up())
    assert ml.is_limit(ProviderError("RESOURCE_EXHAUSTED (code 429) Individual quota reached"))
    assert ml.is_limit(ProviderError("You've hit your session limit · resets 3pm (Asia/Seoul)", status=429))
    assert ml.is_limit(ProviderError("insufficient_quota", status=402))
    assert ml.is_limit(ProviderError("Your credit balance is too low to access the API"))
    assert not ml.is_limit(ProviderError("prompt is too long: context window exceeded", status=429))
    assert not ml.is_limit(ProviderError("quota", status=401))                       # a sign-in problem, not a used-up limit
    assert not ml.is_limit(ProviderError("p: overloaded, try again later", status=529))
    assert not ml.is_limit(RuntimeError("HTTP 429 rate limited"))                    # only provider errors count
    # a used-up limit still counts as "worth waiting for" where a turn decides that (resume.py)
    assert is_outage(QuotaExhausted("x", provider="p", reset_at=NOW + 60))


def test_the_reset_time_comes_from_the_errors_own_words():
    assert ml.reset_from_error(agy_used_up("2h3m10s"), NOW) == NOW + 2 * 3600 + 3 * 60 + 10
    assert ml.reset_from_error(ProviderError("please try again in 5 minutes and 30 seconds"), NOW) == NOW + 330
    e = ProviderError("slow down", status=429)
    e.retry_after = 30
    assert ml.reset_from_error(e, NOW) == NOW + 30
    assert ml.reset_from_error(ProviderError('{"error": {"details": [{"retryDelay": "45s"}]}}'), NOW) == NOW + 45
    at = ml.reset_from_error(ProviderError("You've hit your session limit · resets 3pm (Asia/Seoul)"), NOW)
    assert datetime.fromtimestamp(at, SEOUL).strftime("%Y-%m-%d %H:%M") == "2026-11-09 15:00"
    at = ml.reset_from_error(ProviderError("weekly limit reached · resets 9am"), NOW, "Asia/Seoul")   # past today: tomorrow
    assert datetime.fromtimestamp(at, SEOUL).strftime("%Y-%m-%d %H:%M") == "2026-11-10 09:00"
    assert ml.reset_from_error(ProviderError("usage limit reached|1794196800"), 1794190000) == 1794196800
    assert ml.reset_from_error(ProviderError("quota reached, resets 5"), NOW) is None   # a bare number is not a time


def test_windows_say_when_a_used_up_or_nearly_used_up_limit_resets():
    iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    windows = [{"group": "Gemini Models", "label": "Five Hour", "used_percent": 100, "resets_at": iso(NOW + 3600)},
               {"group": "Gemini Models", "label": "Weekly", "used_percent": 40, "resets_at": iso(NOW + 5 * 86400)},
               {"group": "Claude and GPT models", "label": "Weekly", "used_percent": 92, "resets_at": iso(NOW + 7200)}]
    assert ml.full_until(windows, "gemini-3-pro", NOW) == NOW + 3600
    assert ml.full_until(windows, "claude-sonnet-4-5", NOW) is None
    assert ml.low_until(windows, "claude-sonnet-4-5", NOW) == NOW + 7200
    assert ml.low_until(windows, "gemini-3-pro", NOW) == NOW + 3600


# ---------------------------------------------------------------------------------------------------------- the hold
async def test_a_hit_holds_the_provider_for_everything_but_chat_until_the_reset_and_a_restart_keeps_it():
    store, clock = Store(), Clock(NOW)
    g = ml.QuotaGuard(store, clock=clock)
    hit = await g.hit("agy", "gemini-3-pro", agy_used_up("1h"))
    assert hit.reset_at == hit.until == NOW + 3600 + ml.RESET_MARGIN   # the first call goes out a little after the reset
    await g.before("agy", "gemini-3-pro", "chat")                     # the user's own reply is always tried
    for kind in ("schedule", "worker", "reflection", "proactive"):
        with pytest.raises(QuotaExhausted):
            await g.before("agy", "gemini-3-pro", kind)
    await g.before("claude", "claude-sonnet-4-5", "schedule")        # another provider is not held
    again = ml.QuotaGuard(store, clock=clock)                          # a restart: the hold is in the store
    assert again.until("agy") == NOW + 3600 + ml.RESET_MARGIN and set(again.held()) == {"agy"}
    clock.t = NOW + 3600 + ml.RESET_MARGIN + 1
    await again.before("agy", "gemini-3-pro", "schedule")             # over: calls go out again
    again.clear("agy")
    assert again.state("agy") is None


async def test_without_a_reset_time_the_windows_decide_else_a_capped_backoff():
    store, clock = Store(), Clock(NOW)
    asked = []

    async def windows(provider):
        asked.append(provider)
        return [{"group": "Claude", "used_percent": 100,
                 "resets_at": datetime.fromtimestamp(NOW + 5400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    g = ml.QuotaGuard(store, windows, clock=clock)
    hit = await g.hit("claude", "claude-sonnet-4-5", ProviderError("rate limited", status=429))
    assert asked == ["claude"] and hit.reset_at == NOW + 5400 + ml.RESET_MARGIN
    plain = ml.QuotaGuard(Store(), clock=clock)
    holds = [(await plain.hit("x", "m", ProviderError("too many requests", status=429))).until - NOW for _ in range(6)]
    assert holds == [*ml.BACKOFF, ml.BACKOFF[-1], ml.BACKOFF[-1]]
    assert all(e is None for e in [(await plain.classify("x", "m", ProviderError("bad tool schema", status=400)))])


async def test_a_nearly_used_up_window_makes_only_the_jobs_nobody_waits_for_step_aside():
    async def windows(provider):
        return [{"group": "", "used_percent": 95,
                 "resets_at": datetime.fromtimestamp(NOW + 7200, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    g = ml.QuotaGuard(Store(), windows, clock=Clock(NOW))
    with pytest.raises(QuotaExhausted) as ei:
        await g.before("agy", "m", "reflection")
    assert ei.value.deferred and ei.value.until == NOW + ml.DEFER_RECHECK
    await g.before("agy", "m", "chat")
    await g.before("agy", "m", "schedule")                            # what the user set up still runs
    assert g.blocked("agy", "memory") is not None and g.blocked("agy", "schedule") is None


async def test_the_metered_stream_turns_a_limit_into_quota_exhausted_and_then_spares_the_provider():
    calls = []

    async def model(provider, request, **kw):
        calls.append(provider)
        if len(calls) == 1:
            raise agy_used_up("30m")
        yield TextDelta("ok")
        yield Done("stop")
    g = ml.QuotaGuard(Store(), clock=Clock(NOW))
    counted = metered(model, lambda r: None, lambda: None, guard=g)
    req = ChatRequest(model="gemini-3-pro", messages=[{"role": "user", "content": "hi"}], tools=[], extra={})
    with pytest.raises(QuotaExhausted) as ei:
        [ev async for ev in counted("schedule")("agy", req)]
    assert ei.value.reset_at == NOW + 1800 + ml.RESET_MARGIN and isinstance(ei.value.__cause__, ProviderError)
    with pytest.raises(QuotaExhausted):
        [ev async for ev in counted("journal")("agy", req)]
    assert len(calls) == 1                                             # held: not called again for background work
    got = [ev async for ev in counted("chat")("agy", req)]             # chat still tries, and an answer lifts the hold
    assert any(isinstance(ev, TextDelta) for ev in got) and len(calls) == 2 and g.until("agy") is None


# ---------------------------------------------------------------------------------------------------------- words for the user
def test_the_line_says_when_in_the_users_language():
    e = QuotaExhausted("x", provider="agy", reset_at=datetime(2026, 11, 9, 15, 10, tzinfo=SEOUL).timestamp() - 30)
    assert ml.chat_line(e, "Asia/Seoul", NOW) == "The AI model's usage limit is used up. You can use it again at 3:10 PM."
    tok = i18n.use("ko")
    try:
        assert ml.chat_line(e, "Asia/Seoul", NOW) == "AI 모델 사용 한도를 다 썼어요. 오후 3:10에 다시 쓸 수 있어요."
        tomorrow = datetime(2026, 11, 10, 9, 0, tzinfo=SEOUL).timestamp()
        assert ml.paused_text(tomorrow, "Asia/Seoul", NOW) == "AI 모델 사용 한도를 다 써서 멈췄어요. 내일 오전 9:00에 알아서 이어서 해요."
        later = datetime(2026, 11, 12, 21, 5, tzinfo=SEOUL).timestamp()
        assert "11월 12일 오후 9:05에" in ml.delayed_text(later, "Asia/Seoul", NOW)
        assert ml.chat_line(QuotaExhausted("x"), "Asia/Seoul", NOW) == "AI 모델 사용 한도를 다 썼어요. 조금 뒤에 다시 해 주세요."
    finally:
        i18n.reset(tok)
    tok = i18n.use("de")
    try:
        assert ml.when_text(datetime(2026, 11, 9, 15, 10, tzinfo=SEOUL).timestamp() - 30, "Asia/Seoul", NOW) == "um 15:10"
    finally:
        i18n.reset(tok)


# ---------------------------------------------------------------------------------------------------------- proactive: a quiet skip
async def test_proactive_skips_quietly_on_the_limit_and_looks_at_the_items_next_time(tmp_path):
    from test_proactive import FakeMail, all_as, mails

    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    store.add_message("user", "안녕")
    clock = Clock(NOW)
    judge = all_as("오늘")
    fails = [True]

    async def model(provider, request, **kw):
        if fails[0]:
            raise QuotaExhausted("p: the AI model's usage limit is used up", provider="p", reset_at=NOW + 3600)
        async for ev in judge(provider, request, **kw):
            yield ev
    sent = []

    async def deliver(item):
        sent.append(item)
        return len(sent)
    p = Proactive(store, Home(tmp_path / "home"), stream_fn=model, model=lambda: ("p", "m"), deliver=deliver,
                  mail=FakeMail(mails("내일 회의 일정 변경")), clock=clock)
    p.update(enabled=True)
    out = await p.tick()
    assert out["status"] == "skipped" and out["reason_code"] == "model_limit" and out["reason"] == "The AI model's usage limit is used up"
    assert not sent and not store._all("SELECT * FROM proactive_log")   # nothing told, nothing dropped
    fails[0] = False
    clock.t += p.config()["every_minutes"] * 60
    out = await p.tick()
    assert out["status"] == "done" and len(sent) == 1


# ---------------------------------------------------------------------------------------------------------- the memory pass: retry_at
async def test_the_memory_pass_waits_for_retry_at_and_then_runs_without_the_usual_gap(tmp_path):
    from test_memory_upkeep import answer, answering, say as said

    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m", user_name="Mina")
    clock = Clock(NOW)
    fn = answering(QuotaExhausted("p: used up", provider="p", reset_at=NOW + 3600, until=NOW + 3630),
                   answer(memory_md="# Memory\n\n## Facts\n- Mina likes tea"))
    up = MemoryUpkeep(store, Home(tmp_path / "home"), Activity(store, clock), fn, lambda: ("p", "m"), enabled=lambda: True,
                      rules=lambda: [], clock=clock, last_use=lambda: NOW - IDLE_BEFORE_RUN - 1)
    said(store, NOW - 7200, "user", "나는 차를 좋아해")
    first = await up.run(trigger="nightly")
    assert first["status"] == "pending" and first["code"] == "model_limit" and first["retry_at"] == NOW + 3630
    assert up.public()["last_attempt"]["retry_at"] == NOW + 3630 and store.meta("memory:seen_until") in ("", None)
    clock.t = NOW + 1800
    assert await up.idle_tick() is None and len(fn.calls) == 1          # not before the hold is over
    clock.t = NOW + 3700
    up.last_use = lambda: clock.t - IDLE_BEFORE_RUN - 1
    done = await up.idle_tick()
    assert done["status"] == "done" and done["trigger"] == "retry" and len(fn.calls) == 2   # no IDLE_GAP / message count wait
    assert up.last_attempt()["code"] == ""


# ---------------------------------------------------------------------------------------------------------- a background job pauses
async def test_a_job_pauses_on_the_limit_tells_once_in_the_active_hours_and_goes_on_after_the_reset(tmp_path):
    def worker(req, n):
        if n == 0:
            raise agy_used_up("2h")
        return call("report", {"status": "done", "summary": "끝났어요."})
    brain = Brain(chat=delegating({"task": "가격을 비교해줘", "kind": "general"}), worker=worker, handoff=lambda r, n: say("다 했어요."))
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "가격 비교해줘"})).json()["id"])
        w = await one_worker(c, "interrupted")
        workers, guard = app.state.workers, app.state.limit_guard
        row = workers._row(w["id"])
        wait = json.loads(row["wait_on"])
        assert wait["kind"] == "model_limit" and not wait["noted"] and guard.until("p") == wait["until"]
        assert "usage limit is used up" in row["error"] and re.search(r"It goes on by itself (tomorrow )?at", row["error"])   # a known reset: says when
        assert not brain.calls["handoff"]                                # no report: answering it would need the model too
        # quiet time: no notice yet; inside the active hours: one, and only one
        app.state.proactive.awake = lambda ts=None: False
        workers.limit_tick(now=wait["until"] - 600)
        assert not [n for n in (await c.get("/api/notifications")).json()["items"] if n["type"] == "task_waiting"]
        app.state.proactive.awake = lambda ts=None: True
        workers.limit_tick(now=wait["until"] - 600)
        workers.limit_tick(now=wait["until"] - 300)
        notes = [n for n in (await c.get("/api/notifications")).json()["items"] if n["type"] == "task_waiting"]
        assert len(notes) == 1 and notes[0]["title"].startswith("Delayed until the AI model can be used again: ")
        assert notes[0]["task_id"] == w["parent_task_id"]
        # the hold is over: it goes on by itself and reports
        guard.clear("p")
        assert workers.limit_tick(now=wait["until"] + 1) == [w["id"]]
        await one_worker(c, "done")
        await until(lambda: brain.calls["handoff"])


async def test_a_job_paused_without_a_known_reset_does_not_promise_a_time(tmp_path):
    def worker(req, n):
        raise ProviderError("too many requests", status=429)
    brain = Brain(chat=delegating({"task": "정리해줘", "kind": "general"}), worker=worker)
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "정리해줘"})).json()["id"])
        w = await one_worker(c, "interrupted")
        row = app.state.workers._row(w["id"])
        assert row["error"] == ("Paused because the AI model's usage limit is used up. It goes on by itself once the model can be used "
                                "again.")
        assert app.state.workers.host.limit_until() == app.state.limit_guard.until("p") is not None
        # a provider held longer pushes the job's own wait back
        later = app.state.limit_guard.until("p") + 3600
        app.state.limit_guard.store.set_meta(ml.META + "p", json.dumps({"until": later, "known": False, "hits": 2}))
        app.state.proactive.awake = lambda ts=None: False
        app.state.workers.limit_tick(now=later - 7200)
        assert json.loads(app.state.workers._row(w["id"])["wait_on"])["until"] == later
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------------------------------------- a scheduled run waits
async def test_a_scheduled_run_waits_for_the_reset_tells_once_and_runs_then(tmp_path):
    from assistant.schedules import Schedules
    from test_resume import CHECK, app_for

    db = str(tmp_path / "a.db")
    s = Schedules(Store(db)).create("아침 확인", "새 메일을 확인해줘", cron="0 9 * * *")
    state = {"used_up": True, "seen": 0, "checks": 0}

    async def model(provider, request, **kw):
        if request.messages and request.messages[-1].get("content") == CHECK:
            state["checks"] += 1
            yield TextDelta("OK")
            yield Done("stop")
            return
        state["seen"] += 1
        if state["used_up"]:
            raise agy_used_up("1h")
        yield TextDelta("새 메일이 없어요.")
        yield Done("stop")
    app = app_for(tmp_path, model)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post(f"/api/schedules/{s['id']}/run")).status_code < 300

        async def waiting():
            items = (await c.get("/api/tasks")).json()["items"]
            return items[0] if items and items[0]["status"] == "waiting_model" else None
        t = await until(waiting)
        assert t["result"] == "The AI model's usage limit is used up"
        guard = app.state.limit_guard
        held = guard.until("p")
        row = Store(db)._all("SELECT retry_at, error FROM tasks WHERE id = ?", (t["id"],))[0]
        assert row["error"].startswith("QuotaExhausted") and row["retry_at"] == held   # first looked at when the hold ends
        tick = app.state.model_wait_tick
        app.state.proactive.awake = lambda ts=None: False
        assert await tick(time.time() + 60) == {}                                       # quiet time: not told yet, not due yet
        assert not (await c.get("/api/notifications")).json()["items"]
        app.state.proactive.awake = lambda ts=None: True
        await tick(time.time() + 60)
        await tick(time.time() + 120)
        notes = (await c.get("/api/notifications")).json()["items"]
        assert [n["title"] for n in notes] == ["Delayed until the AI model can be used again: 아침 확인"]
        assert "It will run by itself" in notes[0]["body"] and state["checks"] == 0   # "at" or "tomorrow at" (the clock); the provider is not called before the reset
        guard.clear("p")
        state["used_up"] = False
        assert await tick(held + 1) == {t["id"]: "resumed"}

        async def ran():
            items = (await c.get("/api/tasks")).json()["items"]
            return any(x["status"] == "done" for x in items)
        await until(ran)


async def test_a_balance_that_ran_out_says_top_up_with_no_reset_time_and_nothing_retries_on_a_timer():
    """A 402 'requires more credits' (any provider's payment-required or insufficient-balance answer) is not a timed limit: the words
    say the provider's balance ran out and ask for a top-up, no reset time is given or estimated, background calls stay held until a
    call answers again (the owner's next chat after the top-up), and chat is always tried."""
    store, clock = Store(), Clock(NOW)
    g = ml.QuotaGuard(store, clock=clock)
    err = ProviderError("openrouter: HTTP 402: This request requires more credits, or fewer max_tokens. You requested up to 16384 "
                        "tokens, but can only afford 300.", status=402)
    assert ml.is_limit(err) and ml.is_balance(err)
    for words in ("Insufficient Balance", "insufficient_quota: You exceeded your current quota, please check your plan and billing",
                  "Your credit balance is too low to access the API"):
        assert ml.is_balance(ProviderError(words, status=400 if "credit" in words else 429)), words
    assert not ml.is_balance(agy_used_up()) and not ml.is_balance(ProviderError("rate limit", status=429))
    hit = await g.classify("openrouter", "some/model", err)
    assert hit is not None and hit.balance and hit.reset_at is None and g.balance_out("openrouter")
    assert "until" not in str(hit) and "estimated" not in str(hit)
    await g.before("openrouter", "some/model", "chat")                 # the owner's own message is tried (after a top-up it works)
    for kind in ("schedule", "worker", "proactive"):
        with pytest.raises(QuotaExhausted) as e:
            await g.before("openrouter", "some/model", kind)
        assert e.value.balance
    clock.t = NOW + 3 * 86400
    with pytest.raises(QuotaExhausted):                                # no backoff that runs out and tries again by itself
        await g.before("openrouter", "some/model", "schedule")
    token = i18n.use("ko")
    try:
        line = ml.chat_line(hit, "Asia/Seoul", NOW)
    finally:
        i18n.reset(token)
    assert "OpenRouter" in line and "충전" in line and ":" not in line.replace("OpenRouter", "")
    assert ml.chat_line(hit, "Asia/Seoul", NOW) == ("Your OpenRouter balance has run out, so the AI model could not answer. Top it up on "
                                                     "OpenRouter's site, then send your message again.")
    g.clear("openrouter")                                              # a call answered: held no more
    assert not g.balance_out("openrouter")


async def test_a_job_pauses_while_the_model_is_busy_and_goes_on_by_itself(tmp_path):
    """2026-11-11: Gemini's "high traffic … No capacity available (503)" ended a 130-step job twice; now it waits and goes on."""
    def worker(req, n):
        if n == 0:
            raise ProviderError("antigravity-oauth: Antigravity model error: Our servers are experiencing high traffic right now, please "
                                "try again in a minute. (UNAVAILABLE (code 503): No capacity available for model g")
        return call("report", {"status": "done", "summary": "끝났어요."})
    brain = Brain(chat=delegating({"task": "가격을 비교해줘", "kind": "general"}), worker=worker, handoff=lambda r, n: say("다 했어요."))
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "가격 비교해줘"})).json()["id"])
        w = await one_worker(c, "interrupted")
        workers = app.state.workers
        wait = json.loads(workers._row(w["id"])["wait_on"])
        assert wait["kind"] == "model_busy" and wait["n"] == 1 and not brain.calls["handoff"]   # paused, nothing told as failed
        assert workers.limit_tick(now=wait["until"] - 1) == []
        assert workers.limit_tick(now=wait["until"] + 1) == [w["id"]]
        await one_worker(c, "done")
        await until(lambda: brain.calls["handoff"])
