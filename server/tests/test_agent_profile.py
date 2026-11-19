"""The agent screen's server side: task log, live status, schedules (fake clock), approval history and site rules, browser tabs,
the journal (fake model), the agent's picture and the notification feed."""

import asyncio
import io
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from PIL import Image

from assistant import i18n
from assistant.activity import Activity, result_line, rule_kind, title_from
from assistant.agent import Tool, emit_ui
from assistant.avatar import AvatarError, process
from assistant.journal import Journal, journal_tools, parse_entries
from assistant.providers import Done, TextDelta, ToolCall
from assistant.schedules import (
    CATCH_UP_SECONDS, ScheduleError, Scheduler, Schedules, describe_cron, parse_cron, schedule_tools,
)
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.browser import BrowserSession

from test_server import AUTH, TOKEN, Running, follow

SEOUL = ZoneInfo("Asia/Seoul")


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def app_settings(tmp_path, **kw):
    """Most tests drive the chat through the browser tools themselves, so they run with the browser split off (browser_worker=False);
    server/tests/test_browser_split.py (and any test of the split) passes browser_worker=True."""
    kw.setdefault("browser_worker", False)
    return Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), **kw)


def answering(*texts):
    """A fake model that answers each call with the next text (the last one again when it runs out)."""
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


# ---------------------------------------------------------------------------------------------------------- helpers and words
def test_titles_and_result_lines_are_short_and_clean():
    assert title_from("[Fri 2026-11-06 10:42:07 KST] [client_timezone=Asia/Seoul]\n내일 서울 날씨 알려줘") == "내일 서울 날씨 알려줘"
    long = title_from("이번 주말에 가족이랑 갈 만한 조용한 캠핑장을 경기도 근처에서 세 군데만 찾아서 가격이랑 같이 알려줘")
    assert long.endswith("…") and len(long) <= 41
    assert title_from("") == "Conversation"
    assert result_line("**예약했어요.** 확인 번호는 1234예요.\n\n- 다음 단계") == "예약했어요."
    assert result_line("- 첫째 항목입니다\n- 둘째") == "첫째 항목입니다"
    assert result_line("[링크](https://x.y) 를 열었어요<message_break/>그리고") == "링크 를 열었어요"


def test_payments_and_schedules_can_never_become_site_rules():
    assert rule_kind("browser_click") == rule_kind("browser_type") == "browser_action" and rule_kind("browser_login") == "login"
    for never in ("confirm_purchase", "browser_fill_card", "wallet_cards", "schedule_create", "schedule_delete", "email_send", "request_login"):
        assert rule_kind(never) == ""
    store = Store()
    a = Activity(store)
    assert a.add_rule("confirm_purchase", "shop.example.com") is None and a.add_rule("browser_click", "") is None
    r = a.add_rule("browser_click", "https://www.shop.example.com/cart")
    assert r["site"] == "shop.example.com" and r["kind"] == "browser_action"
    assert a.matching_rule("browser_type", "m.shop.example.com")["id"] == r["id"]       # a subdomain of the site
    assert a.matching_rule("browser_click", "example.com") is None                       # never the parent site
    assert a.matching_rule("browser_login", "shop.example.com") is None                  # another kind
    assert a.matching_rule("confirm_purchase", "shop.example.com") is None


# ---------------------------------------------------------------------------------------------------------- cron
@pytest.mark.parametrize("expr,words", [
    ("0 9 * * 1", "매주 월요일 오전 9:00"), ("15 6 * * *", "매일 오전 6:15"), ("0 */1 * * *", "1시간마다"), ("0 */3 * * *", "3시간마다"),
    ("*/30 * * * *", "30분마다"), ("0 9 * * 1-5", "평일 오전 9:00"), ("0 10 * * sat,sun", "주말 오전 10:00"),
    ("30 21 * * 1,3,5", "매주 월·수·금요일 오후 9:30"), ("0 12 1 * *", "매월 1일 오후 12:00"), ("0 0 * * 0", "매주 일요일 오전 12:00"),
    ("0 9,18 * * *", "매일 오전 9:00, 오후 6:00"), ("@daily", "매일 오전 12:00"), ("0 8 25 12 *", "매년 12월 25일 오전 8:00"),
])
def test_cron_is_described_in_plain_korean(expr, words):
    tok = i18n.use("ko")
    try:
        assert describe_cron(expr) == words
    finally:
        i18n.reset(tok)


@pytest.mark.parametrize("expr,words", [
    ("0 9 * * 1", "Every Mon at 9:00 AM"), ("15 6 * * *", "Every day at 6:15 AM"), ("0 */1 * * *", "Every hour"),
    ("0 */3 * * *", "Every 3 hours"), ("*/30 * * * *", "Every 30 minutes"), ("0 9 * * 1-5", "Weekdays at 9:00 AM"),
    ("30 21 * * 1,3,5", "Every Mon·Wed·Fri at 9:30 PM"), ("0 12 1 * *", "Every month on day 1 at 12:00 PM"),
    ("0 8 25 12 *", "Every year in month 12, day 25 at 8:00 AM"),
])
def test_cron_is_described_in_plain_english_by_default(expr, words):
    assert describe_cron(expr) == words


@pytest.mark.parametrize("expr", ["0 9-17 * * 1-5", "5,10,15,20,25 * * * *", "0 9 1-7 * 1", "nonsense", "0 9 */2 * *"])
def test_an_unusual_cron_is_never_shown_as_the_expression(expr):
    for lang in ("en", "ko"):
        tok = i18n.use(lang)
        try:
            words = describe_cron(expr)
        finally:
            i18n.reset(tok)
        assert "*" not in words and expr not in words and "/" not in words and words.strip(), words
    assert describe_cron("nonsense") == "Repeats on a set rule"


async def test_schedule_approval_cards_say_the_time_in_words():
    _, clock, s = make()
    tools = {t.name: t for t in schedule_tools(s)}
    create = tools["schedule_create"]
    args = {"name": "아침", "prompt": "날씨 알려줘", "cron": "30 7 * * 1-5", "ends_on": "2026-12-31"}
    lines = create.approval_extra("a1", args)["lines"]
    assert {"label": "When", "value": "Weekdays at 7:30 AM, until 2026-12-31"} in lines
    assert "* *" not in await create.describe(args)
    sid = s.create("주간", "정리", cron="0 9 * * 1")["id"]
    upd = tools["schedule_update"]
    text = await upd.describe({"id": sid, "cron": "0 9-17 * * *"})
    assert "cron" not in text and "*" not in text and "Repeats on a set rule" in text and "(time)" in text
    assert upd.approval_extra("a2", {"id": sid, "cron": "0 8 * * *"})["lines"][1] == {"label": "When", "value": "Every day at 8:00 AM"}


def test_cron_next_runs_follow_local_time_and_day_rules():
    c = parse_cron("0 9 * * 1")
    after = ts(2026, 11, 7, 12, 0)                      # a Saturday
    assert datetime.fromtimestamp(c.next_after(after, SEOUL), SEOUL) == datetime(2026, 11, 9, 9, 0, tzinfo=SEOUL)
    assert parse_cron("0 9 * * 7").weekdays == frozenset({0})              # 7 is Sunday too
    both = parse_cron("0 0 13 * 5")                                        # the 13th OR a Friday, as classic cron does
    first = datetime.fromtimestamp(both.next_after(after, SEOUL), SEOUL)
    assert first == datetime(2026, 11, 13, 0, 0, tzinfo=SEOUL)
    ny = ZoneInfo("America/New_York")                  # daylight saving ends 2026-11-01: 09:00 stays 09:00 local
    d = parse_cron("0 9 * * *")
    t1 = d.next_after(datetime(2026, 10, 31, 8, 0, tzinfo=ny).timestamp(), ny)
    t2 = d.next_after(t1, ny)
    assert datetime.fromtimestamp(t2, ny).hour == 9 and t2 - t1 == 25 * 3600
    for bad in ("", "* * * *", "61 * * * *", "0 9 * * mon-", "0 9 32 * *", "a b c d e", "5-1 * * * *"):
        with pytest.raises(ScheduleError):
            parse_cron(bad)


# ---------------------------------------------------------------------------------------------------------- schedules (fake clock)
def make(tmp_path=None, t=None):
    store = Store(str(tmp_path / "s.db")) if tmp_path else Store()
    store.set_profile(timezone="Asia/Seoul")
    clock = Clock(t or ts(2026, 11, 7, 8, 0))
    return store, clock, Schedules(store, clock)


def test_schedules_are_checked_and_get_words_and_next_run():
    _, clock, s = make()
    one = s.create("주간 정리", "지난주 메일을 정리해서 알려줘", cron="0 9 * * 1")
    assert one["description"] == "Every Mon at 9:00 AM" and one["next_run"] == "2026-11-09T09:00+09:00" and one["timezone"] == "Asia/Seoul"
    once = s.create("병원 알림", "병원 예약 시간 알려줘", run_at="2026-11-07T10:30")
    assert once["kind"] == "once" and once["next_run"] == "2026-11-07T10:30+09:00" and once["description"].endswith("at 10:30 AM")
    for kw, why in ((dict(cron="* * * * *"), "apart"), (dict(run_at="2026-11-06T09:00"), "past"), (dict(), "either"),
                    (dict(cron="0 9 * * 1", run_at="2026-11-09T09:00"), "either"), (dict(cron="0 9 * * 1", timezone="Mars/Base"), "time zone")):
        with pytest.raises(ScheduleError, match=why):
            s.create("x", "y", **kw)
    off = s.update(one["id"], enabled=False)
    assert off["enabled"] is False and off["next_run"] is None
    on = s.update(one["id"], enabled=True, cron="15 6 * * *")
    assert on["next_run"] == "2026-11-08T06:15+09:00" and on["description"] == "Every day at 6:15 AM"


def test_repeating_schedules_keep_to_their_first_and_last_day():
    _, clock, s = make()                                  # now: Sat 2026-11-07 08:00 Seoul
    later = s.create("월간", "정리", cron="0 9 * * *", starts_on="2026-11-20", ends_on="2026-11-22")
    assert later["next_run"] == "2026-11-20T09:00+09:00" and later["starts_on"] == "2026-11-20" and later["ends_on"] == "2026-11-22"
    assert later["description"] == "Every day at 9:00 AM, starting 2026-11-20, until 2026-11-22"
    tok = i18n.use("ko")
    try:
        assert "까지" in s.get(later["id"])["description"]
    finally:
        i18n.reset(tok)
    for kw, why in ((dict(ends_on="2026-11-01"), "passed"), (dict(starts_on="2026-11-10", ends_on="2026-11-09"), "before"),
                    (dict(ends_on="nope"), "date"), (dict(cron="0 9 * * 1", ends_on="2026-11-08"), "never runs")):
        with pytest.raises(ScheduleError, match=why):
            s.create("x", "y", **{"cron": "0 9 * * *", **kw})
    once = s.create("한 번", "알림", run_at="2026-11-08T10:00", ends_on="2026-11-30")
    assert once["ends_on"] is None and once["starts_on"] is None     # a one-time job has no first or last day
    # the last run turns it off as finished
    sch = Scheduler(s, clock=clock)
    r = s._row(later["id"])
    clock.t = ts(2026, 11, 22, 9, 0, 5)
    s.advance(r, clock.t)
    done = s.get(later["id"])
    assert done["enabled"] is False and done["next_run"] is None and "last day" in done["disabled_reason"]
    assert sch is not None
    # a change clears the end with "" and keeps it with None
    clock.t = ts(2026, 11, 7, 8, 0)
    d = s.create("매일", "브리핑", cron="0 9 * * *", ends_on="2026-11-30")
    assert s.update(d["id"], name="아침")["ends_on"] == "2026-11-30"
    assert s.update(d["id"], ends_on="")["ends_on"] is None
    assert s.update(d["id"], run_at="2026-11-09T09:00")["ends_on"] is None   # now a one-time job


async def test_due_runs_start_once_and_one_time_jobs_are_spent(tmp_path):
    _, clock, s = make()
    ran, gate = [], asyncio.Event()

    async def run(sched):
        ran.append(sched["id"])
        await gate.wait()
        return {"task_id": 1, "status": "done"}

    sch = Scheduler(s, run, clock=clock)
    daily = s.create("매일", "아침 브리핑", cron="0 9 * * *")
    once = s.create("한 번", "알림", run_at="2026-11-07T09:00")
    clock.t = ts(2026, 11, 7, 8, 59)
    assert sch.tick() == []
    clock.t = ts(2026, 11, 7, 9, 0, 5)
    assert sorted(sch.tick()) == sorted([daily["id"], once["id"]])
    assert s.get(once["id"])["enabled"] is False and s.get(daily["id"])["next_run"] == "2026-11-08T09:00+09:00"
    clock.t = ts(2026, 11, 8, 9, 0, 5)              # the first run is still going: this time is skipped, never two at once
    assert sch.tick() == [] and not sch.start_run(s.get(daily["id"]))
    gate.set()
    await asyncio.sleep(0.05)
    assert s.get(daily["id"])["last_run"]["status"] == "done" and ran.count(daily["id"]) == 1
    clock.t = ts(2026, 11, 9, 9, 0, 5)
    assert sch.tick() == [daily["id"]]
    await asyncio.sleep(0.05)


async def test_after_a_restart_a_recent_missed_run_happens_once_and_an_old_one_is_skipped(tmp_path):
    store, clock, s = make(tmp_path, ts(2026, 11, 7, 8, 0))
    recent = s.create("최근", "a", cron="0 9 * * *")
    old = s.create("오래됨", "b", cron="0 5 * * *")
    once_old = s.create("놓친 한 번", "c", run_at="2026-11-07T08:30")
    del s, store
    # the server comes back at 07:01 the next day: 09:00 yesterday is 22 h late, 05:00 today 2 h 1 min late, 08:30 yesterday long gone
    store2 = Store(str(tmp_path / "s.db"))
    clock2 = Clock(ts(2026, 11, 8, 7, 1))
    s2 = Schedules(store2, clock2)
    ran = []

    async def run(sched):
        ran.append(sched["name"])
        return {"task_id": None, "status": "done"}

    started = Scheduler(s2, run, clock=clock2).tick()
    await asyncio.sleep(0.05)
    assert started == [] and ran == []
    assert s2.get(recent["id"])["next_run"] == "2026-11-08T09:00+09:00" and s2.get(recent["id"])["last_run"] is None
    assert s2.get(old["id"])["next_run"] == "2026-11-09T05:00+09:00"
    gone = s2.get(once_old["id"])
    assert gone["enabled"] is False and "Could not run at the set time" in gone["disabled_reason"]
    clock2.t = ts(2026, 11, 8, 10, 59)                # down 09:00 -> 10:59: 1 h 59 min late, inside the window
    assert Scheduler(s2, run, clock=clock2).tick() == [recent["id"]]
    await asyncio.sleep(0.05)
    assert ran == ["최근"] and CATCH_UP_SECONDS == 7200


async def test_three_failures_in_a_row_turn_a_schedule_off_and_say_why():
    _, clock, s = make()
    offs = []
    results = iter([{"task_id": 1, "status": "failed", "error": "e1"}, {"task_id": 2, "status": "done"},
                    {"task_id": 3, "status": "failed", "error": "e"}, {"task_id": 4, "status": "failed", "error": "e"},
                    {"task_id": 5, "status": "failed", "error": "site is down"}])

    async def run(sched):
        return next(results)

    sch = Scheduler(s, run, clock=clock, on_disabled=offs.append)
    job = s.create("점검", "사이트 상태 확인", cron="0 * * * *")
    for _ in range(5):
        assert sch.start_run(s.get(job["id"]))
        await asyncio.sleep(0.02)
    got = s.get(job["id"])
    assert got["enabled"] is False and got["failure_count"] == 3 and "failing 3 times in a row" in got["disabled_reason"] and "site is down" in got["disabled_reason"]
    assert [o["id"] for o in offs] == [job["id"]]
    back = s.update(job["id"], enabled=True)
    assert back["enabled"] and back["failure_count"] == 0 and back["disabled_reason"] is None


async def test_a_run_that_cannot_start_counts_as_a_failure():
    _, clock, s = make()

    async def run(sched):
        raise RuntimeError("no model")

    sch = Scheduler(s, run, clock=clock)
    job = s.create("x", "y", cron="0 * * * *")
    sch.start_run(s.get(job["id"]))
    await asyncio.sleep(0.02)
    assert s.get(job["id"])["last_run"]["status"] == "failed" and s.get(job["id"])["failure_count"] == 1


async def test_the_daily_job_runs_once_per_local_day_after_its_time():
    _, clock, s = make()
    sch = Scheduler(s, None, clock=clock)
    runs = []

    async def job():
        runs.append(clock.t)

    sch.add_daily("journal", 3, 30, job)
    clock.t = ts(2026, 11, 7, 9, 0)
    sch.tick()                                        # the first start ever: nothing at once (no surprise model call on an upgrade)
    clock.t = ts(2026, 11, 8, 3, 29)
    sch.tick()
    clock.t = ts(2026, 11, 8, 3, 31)
    sch.tick()
    sch.tick()
    await asyncio.sleep(0.01)
    assert len(runs) == 1
    again = Scheduler(s, None, clock=clock)           # a restart later that day does not run it twice
    again.add_daily("journal", 3, 30, job)
    clock.t = ts(2026, 11, 8, 9, 0)
    again.tick()
    clock.t = ts(2026, 11, 10, 12, 0)                 # down over a whole day: it catches up once on start
    again.tick()
    again.tick()
    await asyncio.sleep(0.01)
    assert len(runs) == 2


async def test_schedule_tools_ask_before_creating_and_deleting_but_not_before_pausing():
    _, clock, s = make()
    tools = {t.name: t for t in schedule_tools(s)}
    assert tools["schedule_create"].requires_approval and tools["schedule_delete"].requires_approval
    assert tools["schedule_list"].read_only and not tools["schedule_list"].requires_approval
    args = {"name": "아침 날씨", "prompt": "오늘 날씨 알려줘", "cron": "0 7 * * *"}
    assert tools["schedule_create"].validate(args) is None
    assert "apart" in tools["schedule_create"].validate({**args, "cron": "* * * * *"})
    assert (await tools["schedule_create"].describe(args)).startswith("Create a schedule: '아침 날씨' · Every day at 7:00 AM")
    out = await tools["schedule_create"].handler(args)
    sid = s.list()[0]["id"]
    assert f"#{sid}" in out and s.list()[0]["created_by"] == "agent"
    assert tools["schedule_update"].auto_approve({"id": sid, "enabled": False})
    assert not tools["schedule_update"].auto_approve({"id": sid, "enabled": False, "prompt": "다른 일"})
    assert not tools["schedule_update"].auto_approve({"id": sid, "enabled": True})
    assert "아침 날씨" in await tools["schedule_list"].handler({})
    assert tools["schedule_delete"].validate({"id": 999})
    assert await tools["schedule_delete"].handler({"id": sid}) == "Deleted." and s.list() == []
    for t in tools.values():   # generic: no site names in the tool texts
        assert "www." not in t.description and ".com" not in t.description


# ---------------------------------------------------------------------------------------------------------- task log over HTTP
async def test_every_reply_becomes_a_task_with_title_result_and_answer(tmp_path):
    fn = answering("오늘은 맑아요. 최고 기온은 15도예요.", RuntimeError("provider down"))
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "오늘 서울 날씨 어때?"})).json()["id"]
        await follow(c, tid)
        await follow(c, (await c.post("/api/turns", json={"text": "두 번째 질문"})).json()["id"])
        page = (await c.get("/api/tasks")).json()
        failed, done = page["items"]
        assert done["title"] == "오늘 서울 날씨 어때?" and done["status"] == "done" and done["result"] == "오늘은 맑아요."
        assert done["source"] == "chat" and done["turn_id"] == tid and done["seen"] is True and done["day"]
        assert failed["status"] == "failed" and failed["result"].startswith("Failed: RuntimeError: provider down")
        full = (await c.get(f"/api/tasks/{done['id']}")).json()
        assert full["answer"] == "오늘은 맑아요. 최고 기온은 15도예요." and full["approvals"] == [] and full["turn_available"] is True
        assert (await c.get("/api/tasks", params={"before": failed["id"]})).json()["items"][0]["id"] == done["id"]
        assert (await c.get("/api/tasks/999")).status_code == 404
        status = (await c.get("/api/agent/status")).json()
        assert status["busy"] is False and status["status"] is None and status["pending_approvals"] == 0
        assert status["last_reply_failed"] is (failed["source"] == "chat")   # the newest chat reply is the failed one here
        assert (await c.get("/api/notifications")).json()["items"] == []      # both were watched in the chat: no notification


async def test_a_reply_nobody_watched_leaves_an_unread_task_and_a_notification(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.WATCHED_SECONDS", 0.0)
    fn = answering("다 했어요.")
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/turns", json={"text": "정리해줘"})
        await asyncio.sleep(0.3)
        page = (await c.get("/api/tasks")).json()
        assert page["unseen"] == 1 and page["items"][0]["seen"] is False
        notes = (await c.get("/api/notifications")).json()
        assert notes["unseen"] == 1 and notes["items"][0]["type"] == "task_done" and notes["items"][0]["task_id"] == page["items"][0]["id"]
        assert notes["items"][0]["title"] == "Finished: 정리해줘" and notes["items"][0]["body"] == "다 했어요."
        assert (await c.post("/api/tasks/seen", json={"up_to": page["items"][0]["id"]})).json()["unseen"] == 0
        # looking at the task already cleared its notification
        assert (await c.post("/api/notifications/seen", json={"up_to": notes["last_id"]})).json() == {"ok": True, "marked": 0, "unseen": 0}


def approval_tool(name="send", ran=None, **kw):
    async def handler(args):
        if ran is not None:
            ran.append(args)
        return "done"
    return Tool(name, name, {"type": "object"}, handler, requires_approval=True, **kw)


async def test_live_status_and_pending_approvals_while_a_reply_waits(tmp_path):
    fn_turns = iter([[ToolCall("c1", "send", '{"to":"bob"}'), Done("tool_calls")], [TextDelta("보냈어요."), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(fn_turns):
            yield ev

    async def describe(args):
        return "bob에게 보내요"

    tool = approval_tool(describe=describe)
    async with Running(create_app(app_settings(tmp_path), tools=[tool], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "bob에게 보내줘"})).json()["id"]
        for _ in range(100):
            pend = (await c.get("/api/approvals/pending")).json()["items"]
            if pend:
                break
            await asyncio.sleep(0.02)
        p = pend[0]
        assert p["name"] == "send" and p["detail"] == "bob에게 보내요" and p["turn_id"] == tid and "arguments" not in p and p["can_remember"] is False
        assert p["source"] == "chat" and p["worker_id"] is None and p["unattended"] is False   # a chat reply's own card
        status = (await c.get("/api/agent/status")).json()
        assert status["busy"] and status["status"] == "Waiting for approval" and status["running"][0]["waiting_approval"] is True
        assert (await c.get("/api/tasks")).json()["items"][0]["status"] == "waiting_approval"
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["decision"] == "pending" and hist[0]["detail"] == "bob에게 보내요"
        r = (await c.post(f"/api/approvals/{p['approval_id']}", json={"approve": True, "remember": "site"})).json()
        assert r == {"ok": True, "decision": "allowed_once", "remembered": False, "rule": None}   # no site, not a rememberable kind
        await follow(c, tid)
        assert (await c.get("/api/approvals/pending")).json()["items"] == []
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["decision"] == "allowed_once" and hist[0]["answered_at"] and hist[0]["task_id"]
        assert (await c.get(f"/api/tasks/{hist[0]['task_id']}")).json()["approvals"][0]["id"] == p["approval_id"]
        assert (await c.post("/api/approvals/x", json={"approve": True, "remember": "always"})).status_code == 422


class ShotPage:
    """A tab the server can read the title of, take a picture of and close."""

    def __init__(self, url):
        self.url, self._closed = url, False

    def is_closed(self):
        return self._closed

    async def title(self):
        return "가게 장바구니"

    async def screenshot(self, **kw):
        buf = io.BytesIO()
        Image.new("RGB", (1280, 900), (200, 120, 40)).save(buf, "JPEG")
        return buf.getvalue()

    async def close(self):
        self._closed = True


async def test_allow_on_this_site_skips_the_card_next_time_but_never_for_payments_or_the_final_click(tmp_path):
    sess = BrowserSession(connect=None)
    sess._pages["owner"] = ShotPage("https://www.shop.example.com/cart")
    ran = []
    click = approval_tool("browser_click", ran)
    pay = approval_tool("confirm_purchase", ran)
    script = iter([
        [ToolCall("c1", "browser_click", '{"selector":"#next"}'), Done("tool_calls")], [TextDelta("ok"), Done("stop")],
        [ToolCall("c2", "browser_click", '{"selector":"#more"}'), Done("tool_calls")], [TextDelta("ok"), Done("stop")],
        [ToolCall("c4", "confirm_purchase", '{"site":"shop.example.com","summary":"x","amount":1000}'), Done("tool_calls")],
        [TextDelta("ok"), Done("stop")],
    ])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev

    async def turn(c, text, answer=None):
        tid = (await c.post("/api/turns", json={"text": text})).json()["id"]
        events, after = [], 0
        for _ in range(200):
            r = (await c.get(f"/api/turns/{tid}", params={"after": after, "wait": 2})).json()
            events += r["events"]
            after = r["next"]
            for ev in r["events"]:
                if ev["type"] == "approval_request" and answer is not None:
                    got = (await c.post(f"/api/approvals/{ev['approval_id']}", json=answer)).json()
                    events.append({"type": "answered", **got})
            if r["done"]:
                return events
        raise AssertionError

    # the chat itself makes the final payment here: the switch-off path (with the split on, a checkout job pays; test_approval_sign)
    async with Running(create_app(app_settings(tmp_path, payment_specialist=False), tools=[click, pay], stream_fn=fn, browser=sess)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        first = await turn(c, "다음 눌러", {"approve": True, "remember": "site"})
        card = next(e for e in first if e["type"] == "approval_request")
        assert card["site"] == "shop.example.com" and card["can_remember"] is True
        answered = next(e for e in first if e["type"] == "answered")
        assert answered["decision"] == "allowed_always" and answered["rule"]["site"] == "shop.example.com"
        second = await turn(c, "더보기 눌러")
        assert not any(e["type"] == "approval_request" for e in second)                        # allowed by the rule: no card
        assert next(e for e in second if e["type"] == "tool_result")["approved"] is True
        fourth = await turn(c, "결제해", {"approve": True, "remember": "site"})
        pay_card = next(e for e in fourth if e["type"] == "approval_request")
        assert pay_card["can_remember"] is False and next(e for e in fourth if e["type"] == "answered")["remembered"] is False
        decisions = [h["decision"] for h in (await c.get("/api/approvals/history")).json()["items"]]
        assert decisions == ["allowed_once", "allowed_by_rule", "allowed_always"]
        rules = (await c.get("/api/approvals/rules")).json()["items"]
        assert [(r["kind"], r["site"], r["uses"]) for r in rules] == [("browser_action", "shop.example.com", 1)]
        assert rules[0]["kind_label"]
        assert (await c.delete(f"/api/approvals/rules/{rules[0]['id']}")).json() == {"ok": True}
        assert (await c.delete(f"/api/approvals/rules/{rules[0]['id']}")).status_code == 404
        assert ran == [{"selector": "#next"}, {"selector": "#more"}, {"site": "shop.example.com", "summary": "x", "amount": 1000}]


# ---------------------------------------------------------------------------------------------------------- schedules over HTTP
async def test_schedules_api_runs_now_as_an_unattended_turn_and_saves_the_report(tmp_path):
    fn = answering("오늘 일정은 두 개예요.")
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={})
        made = await c.post("/api/schedules", json={"name": "아침 브리핑", "prompt": "오늘 일정 정리해줘", "cron": "0 7 * * *", "timezone": "Asia/Seoul"})
        assert made.status_code == 201
        s = made.json()
        assert s["description"] == "Every day at 7:00 AM" and s["next_run"].endswith("+09:00") and s["enabled"] and s["running"] is False
        assert (await c.post("/api/schedules", json={"name": "x", "prompt": "y", "cron": "* * * * *"})).status_code == 422
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        assert run["ok"] and run["task_id"]
        await follow(c, run["turn_id"])
        await asyncio.sleep(0.1)
        task = (await c.get(f"/api/tasks/{run['task_id']}")).json()
        assert task["source"] == f"schedule:{s['id']}" and task["schedule_id"] == s["id"] and task["title"] == "아침 브리핑" and task["status"] == "done"
        assert task["seen"] is False
        sent = fn.calls[0].messages[-1]["content"]
        assert "Nobody is watching" in sent and "오늘 일정 정리해줘" in sent
        msgs = (await c.get("/api/messages")).json()
        assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"] == "[Automatic run · 아침 브리핑]\n오늘 일정은 두 개예요."
        got = (await c.get(f"/api/schedules/{s['id']}")).json()
        assert got["last_run"]["status"] == "done" and got["last_run"]["task_id"] == run["task_id"]
        note = (await c.get("/api/notifications")).json()["items"][0]
        assert note["type"] == "task_done" and note["schedule_id"] == s["id"]
        patched = (await c.patch(f"/api/schedules/{s['id']}", json={"enabled": False})).json()
        assert patched["enabled"] is False and patched["next_run"] is None
        assert (await c.get("/api/schedules")).json()["items"][0]["id"] == s["id"]
        assert (await c.delete(f"/api/schedules/{s['id']}")).json() == {"ok": True}
        assert (await c.get(f"/api/schedules/{s['id']}")).status_code == 404


async def test_a_schedule_that_keeps_failing_is_turned_off_with_a_notification(tmp_path):
    fn = answering(RuntimeError("bad gateway"))
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "점검", "prompt": "확인해줘", "cron": "0 * * * *"})).json()
        for _ in range(3):
            run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
            await follow(c, run["turn_id"])
            for _ in range(50):
                if not (await c.get(f"/api/schedules/{s['id']}")).json()["running"]:
                    break
                await asyncio.sleep(0.02)
        got = (await c.get(f"/api/schedules/{s['id']}")).json()
        assert got["enabled"] is False and got["failure_count"] == 3 and "bad gateway" in got["disabled_reason"]
        types = [n["type"] for n in (await c.get("/api/notifications")).json()["items"]]
        assert types[0] == "schedule_disabled" and types.count("task_failed") == 3


async def test_an_unattended_approval_notifies_waits_and_then_skips(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    ran = []
    script = iter([[ToolCall("c1", "send", "{}"), Done("tool_calls")], [TextDelta("승인이 없어서 건너뛰었어요."), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev

    async with Running(create_app(app_settings(tmp_path), tools=[approval_tool(ran=ran)], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "보내기", "prompt": "보내줘", "cron": "0 9 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        for _ in range(100):
            notes = (await c.get("/api/notifications")).json()
            if notes["items"]:
                break
            await asyncio.sleep(0.02)
        assert notes["items"][0]["type"] == "approval_waiting" and notes["items"][0]["approval_id"]
        assert notes["items"][0]["title"] == "Please confirm: Working"
        pend = (await c.get("/api/approvals/pending")).json()["items"]
        assert pend[0]["unattended"] is True and pend[0]["expires_at"]
        await follow(c, run["turn_id"])
        assert ran == []
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["decision"] == "expired" and hist[0]["unattended"] is True
        notes = (await c.get("/api/notifications")).json()["items"]
        assert next(n for n in notes if n["type"] == "approval_waiting")["seen"] is True      # nothing left to answer


# ---------------------------------------------------------------------------------------------------------- browser tabs
async def test_browser_tabs_are_listed_with_small_pictures_shown_larger_and_closed(tmp_path):
    sess = BrowserSession(connect=None)
    sess._pages["owner"] = ShotPage("https://shop.example.com/cart")
    sess._pages["schedule:4"] = ShotPage("about:blank")
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=answering("x"), browser=sess)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        out = (await c.get("/api/browser/sessions")).json()
        assert out["available"] is True
        tabs = {t["id"]: t for t in out["items"]}
        main = tabs["owner"]
        assert main["host"] == "shop.example.com" and main["title"] == "가게 장바구니" and main["status"] == "idle" and main["kind"] == "owner"
        assert main["thumbnail"].startswith("data:image/jpeg;base64,") and main["thumbnail_width"] == 360
        assert len(main["thumbnail"]) * 3 / 4 <= 60_000
        assert tabs["schedule:4"]["thumbnail"] is None and tabs["schedule:4"]["kind"] == "schedule"
        shot = await c.get("/api/browser/sessions/owner/screenshot")
        assert shot.headers["content-type"] == "image/jpeg" and len(shot.content) <= 300_000
        assert Image.open(io.BytesIO(shot.content)).width == 1080
        assert (await c.get("/api/browser/sessions/schedule:4/screenshot")).status_code == 404
        assert (await c.delete("/api/browser/sessions/owner")).json() == {"ok": True}
        assert (await c.delete("/api/browser/sessions/owner")).status_code == 404
        assert [t["id"] for t in (await c.get("/api/browser/sessions")).json()["items"]] == ["schedule:4"]
    async with Running(create_app(app_settings(tmp_path / "b"), tools=[], stream_fn=answering("x"))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/browser/sessions")).json() == {"available": False, "items": []}
        assert (await c.post("/api/browser/sessions", json={"prompt": "x"})).status_code == 404


async def test_a_job_tab_waiting_for_the_owner_says_waiting_not_idle(tmp_path):
    # owner report 2026-11-19: a job that ended asking the owner for something showed its tab as resting
    sess = BrowserSession(connect=None)
    sess._pages["owner"] = ShotPage("https://shop.example.com/cart")
    sess._pages["task:w0wait"] = ShotPage("https://shop.example.com/list")
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=answering("x"), browser=sess)
    now = time.time()
    app.state.workers.store._run(
        "INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, deadline_at, minutes, max_steps, token_cap, report) "
        "VALUES('w0wait', 'browser', 'Job', 'Look', 'needs_input', 'task:w0wait', ?, ?, ?, 60, 200, 1000000, ?)",
        (now, now, now + 3600, json.dumps({"status": "needs_input", "summary": "Sign in, then it goes on."})))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tabs = {t["id"]: t for t in (await c.get("/api/browser/sessions?thumbnails=false")).json()["items"]}
    assert tabs["task:w0wait"]["status"] == "waiting" and tabs["task:w0wait"]["status_text"] == "Needs your help"
    assert tabs["owner"]["status"] == "idle"
    assert app.state.workers.waits_for_owner("task:w0wait") is True
    assert app.state.workers.waits_for_owner("owner") is False and app.state.workers.waits_for_owner("task:w0none") is False


async def test_a_task_handed_over_from_the_agent_screen_runs_in_its_own_tab(tmp_path):
    sess = BrowserSession(connect=None)
    fn = answering("찾아봤어요.")
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=fn, browser=sess)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/browser/sessions", json={"prompt": "중고 자전거 시세 찾아줘", "url": "https://market.example.com"})
        assert r.status_code == 201
        out = r.json()
        assert out["id"].startswith("task:") and out["task_id"]
        await follow(c, out["turn_id"])
        await asyncio.sleep(0.05)
        task = (await c.get(f"/api/tasks/{out['task_id']}")).json()
        assert task["source"] == "background" and task["title"] == "중고 자전거 시세 찾아줘" and task["status"] == "done"
        assert "Start at: https://market.example.com" in fn.calls[0].messages[-1]["content"]


# ---------------------------------------------------------------------------------------------------------- journal (fake model)
def day_messages(store, day, texts):
    base = datetime.fromisoformat(day).replace(hour=10, tzinfo=SEOUL).timestamp()
    for i, (role, text) in enumerate(texts):
        store._run("INSERT INTO messages(role, content, created_at) VALUES(?,?,?)", (role, text, base + i * 60))


async def test_the_journal_writes_each_earlier_day_with_the_main_model():
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    day_messages(store, "2026-11-05", [("user", "[Thu 2026-11-05 10:00:00 KST] [client_timezone=Asia/Seoul]\n다음 주 제주 여행 숙소 찾아줘"),
                                       ("assistant", "세 곳을 찾았어요.")])
    day_messages(store, "2026-11-07", [("user", "오늘 것")])
    seen = []

    async def fn(provider, request, **kw):
        seen.append((provider, request))
        yield TextDelta('여기요: [{"title": "제주 여행 준비", "summary": "다음 주 제주 숙소를 함께 골랐어요."}]')
        yield Done("stop")

    j = Journal(store, fn, lambda: ("p", "m"), clock=Clock(ts(2026, 11, 7, 3, 30)))
    out = await j.daily()
    assert [o["date"] for o in out if o["status"] == "done"] == ["2026-11-05"]
    assert len(seen) == 1 and seen[0][0] == "p" and seen[0][1].tools == []
    sent = seen[0][1].messages[1]["content"]
    assert "제주 여행 숙소" in sent and "client_timezone" not in sent and "오늘 것" not in sent
    items = j.entries(month="2026-11")
    assert [(i["date"], i["title"]) for i in items] == [("2026-11-05", "제주 여행 준비")]
    assert j.day_status("2026-11-04") == "empty"
    assert await j.daily() == []                                 # nothing left to write
    assert j.delete(items[0]["id"]) and j.entries(month="2026-11") == []


async def test_the_journal_is_written_with_whatever_main_model_the_user_chose_openai_too():
    # the owner's "no chat messages to OpenAI" rule is about developer test probes, not the product: a fake model stands in here
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    day_messages(store, "2026-11-05", [("user", "안녕")])
    day_messages(store, "2026-11-06", [("user", "또 안녕")])
    fn = answering('[{"title": "인사", "summary": "인사를 나눴어요."}]')
    j = Journal(store, fn, lambda: ("openai", "gpt-x"), clock=Clock(ts(2026, 11, 7, 4, 0)))
    out = await j.daily()
    assert [o["date"] for o in out if o["status"] == "done"] == ["2026-11-05", "2026-11-06"] and len(fn.calls) == 2
    assert j.pending("2026-11") == [] and (await j.write_day("2026-11-05"))["status"] == "done"


async def test_journal_search_finds_days_by_range_and_keyword():
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    j = Journal(store, answering("x"), lambda: ("p", "m"), clock=Clock(ts(2026, 11, 7, 12, 0)))
    for date, title, summary in [("2026-10-28", "제주 준비", "제주 숙소를 골랐어요."), ("2026-11-02", "산책", "한강을 걸었어요."),
                                 ("2026-11-04", "100%_완료", "보고서를 끝냈어요."), ("2026-11-05", "제주 출발", "비행기표를 샀어요.")]:
        store._run("INSERT INTO journal(date, title, summary, created_at) VALUES(?,?,?,?)", (date, title, summary, 0))
    assert [i["date"] for i in j.search(start="2026-11-01", end="2026-11-07")] == ["2026-11-02", "2026-11-04", "2026-11-05"]
    assert [i["title"] for i in j.search(keyword="제주")] == ["제주 준비", "제주 출발"]
    assert [i["title"] for i in j.search(keyword="%_")] == ["100%_완료"]          # LIKE wildcards are matched literally
    with pytest.raises(ValueError):
        j.search(start="last week")
    tool = journal_tools(j)[0]
    assert tool.name == "journal_search" and tool.read_only and not tool.requires_approval
    out = await tool.handler({"from": "2026-11-01", "to": "2026-11-07"})
    assert out.splitlines() == ["- 2026-11-02 · 산책: 한강을 걸었어요.", "- 2026-11-04 · 100%_완료: 보고서를 끝냈어요.", "- 2026-11-05 · 제주 출발: 비행기표를 샀어요."]
    assert "산책" in await tool.handler({"date": "2026-11-02"}) and "제주" not in await tool.handler({"date": "2026-11-02"})
    assert "No journal entries" in await tool.handler({"date": "2026-11-07"}) and "2026-11-07" in await tool.handler({"date": "2026-11-07"})
    assert (await tool.handler({"date": "어제"})).startswith("error")
    from assistant import tool_labels
    assert tool_labels.label("journal_search") == "Searching the journal" and tool_labels.hidden("journal_search") is False


def test_journal_replies_are_read_forgivingly():
    assert parse_entries('[{"title":"a","summary":"b"},{"title":"c","summary":"d"},{"title":"e","summary":"f"},{"title":"g","summary":"h"}]')[2] == {"title": "e", "summary": "f"}
    assert len(parse_entries('[{"title":"a","summary":"b"}]*4')) == 1
    assert parse_entries("그냥 문장으로 답했어요.") == [{"title": "A day", "summary": "그냥 문장으로 답했어요."}]
    assert parse_entries("") == []


async def test_journal_api_rebuilds_lists_and_deletes(tmp_path):
    fn = answering('[{"title": "산책", "summary": "한강을 걸었어요."}]')
    async with Running(create_app(app_settings(tmp_path, scheduler=False), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={})
        store = Store(str(tmp_path / "a.db"))
        store.set_profile(timezone="Asia/Seoul")
        day_messages(store, "2026-10-30", [("user", "한강 산책 다녀왔어")])
        r = (await c.post("/api/journal/rebuild", params={"date": "2026-10-30"})).json()
        assert r["status"] == "done" and r["items"][0]["title"] == "산책"
        assert (await c.post("/api/journal/rebuild", params={"date": "2026-10-29"})).json()["status"] == "empty"
        got = (await c.get("/api/journal", params={"month": "2026-10"})).json()
        assert got["month"] == "2026-10" and [i["summary"] for i in got["items"]] == ["한강을 걸었어요."] and got["pending"] == []
        assert (await c.get("/api/journal", params={"month": "2026-13"})).status_code == 422
        assert (await c.post("/api/journal/rebuild", params={"date": "2026-02-30"})).status_code == 422
        assert (await c.delete(f"/api/journal/{got['items'][0]['id']}")).json() == {"ok": True}
        assert (await c.delete(f"/api/journal/{got['items'][0]['id']}")).status_code == 404
        await c.put("/api/profile", json={"provider": "openai", "model": "gpt-x"})   # a fake model answers; no real call is made
        again = await c.post("/api/journal/rebuild", params={"date": "2026-10-30"})
        assert again.status_code == 200 and again.json()["status"] == "done"
        assert len(fn.calls) == 2


# ---------------------------------------------------------------------------------------------------------- avatar
def picture(fmt, size=(800, 600), mode="RGB", exif=False):
    img = Image.new(mode, size, (10, 200, 30, 128) if mode == "RGBA" else (10, 200, 30))
    buf = io.BytesIO()
    kw = {}
    if exif:
        e = Image.Exif()
        e[0x010F] = "SecretCam"
        e[0x0112] = 6        # rotated: stored sideways
        kw["exif"] = e.tobytes()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def test_avatar_pictures_are_squared_scaled_and_stripped():
    data, mime = process(picture("JPEG", (800, 600), exif=True))
    img = Image.open(io.BytesIO(data))
    assert mime == "image/jpeg" and img.size == (512, 512) and not img.getexif() and b"SecretCam" not in data
    data, mime = process(picture("PNG", (300, 900), "RGBA"))
    assert mime == "image/png" and Image.open(io.BytesIO(data)).size == (512, 512) and Image.open(io.BytesIO(data)).mode == "RGBA"
    assert process(picture("WEBP", (512, 512)))[1] == "image/jpeg"
    for bad in (b"not an image", picture("GIF", (10, 10), "P"), picture("BMP", (10, 10))):
        with pytest.raises(AvatarError):
            process(bad)
    with pytest.raises(AvatarError) as e:
        process(b"\0" * 2_000_001)
    assert e.value.status == 413


async def test_avatar_api_sets_serves_with_etag_and_removes(tmp_path):
    async with Running(create_app(app_settings(tmp_path), tools=[], stream_fn=answering("x"))) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/agent/avatar")).status_code == 404
        assert (await c.get("/api/profile")).json()["avatar_version"] == 0
        put = await c.put("/api/agent/avatar", content=picture("PNG", (640, 480)), headers={"Content-Type": "image/png"})
        assert put.status_code == 200 and put.json()["width"] == 512
        version = put.json()["avatar_version"]
        assert (await c.get("/api/profile")).json()["avatar_version"] == version > 0
        assert (await c.get("/api/agent/status")).json()["avatar_version"] == version
        got = await c.get("/api/agent/avatar")
        assert got.headers["content-type"] == "image/jpeg" and got.headers["etag"]
        assert (await c.get("/api/agent/avatar", headers={"If-None-Match": got.headers["etag"]})).status_code == 304
        again = (await c.put("/api/agent/avatar", content=picture("JPEG", (100, 100)))).json()
        assert again["avatar_version"] > version
        assert (await c.put("/api/agent/avatar", content=b"x" * 2_100_000)).status_code == 413
        assert (await c.put("/api/agent/avatar", content=b"hello")).status_code == 422
        assert (await c.delete("/api/agent/avatar")).json() == {"ok": True, "avatar_version": 0}
        assert (await c.get("/api/agent/avatar")).status_code == 404


# ---------------------------------------------------------------------------------------------------------- notifications
async def test_auto_payments_reach_the_notification_feed_and_seen_marks_both_lists(tmp_path):
    async def pays(args):
        await emit_ui({"type": "payment_auto", "payment": {"id": 7, "amount_text": "12,000원", "site": "shop.example.com", "summary": "커피 원두"},
                       "card": {"id": "card_1", "label": "생활비", "brand": "visa", "last4": "4242"}})
        return "paid"

    tool = Tool("pay_auto", "x", {"type": "object"}, pays)
    script = iter([[ToolCall("c1", "pay_auto", "{}"), Done("tool_calls")], [TextDelta("결제했어요."), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev

    async with Running(create_app(app_settings(tmp_path), tools=[tool], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        # the turn runs in the language of the phone that asked, so its notification is saved in Korean
        await follow(c, (await c.post("/api/turns", json={"text": "원두 사줘"}, headers={"Accept-Language": "ko-KR,ko;q=0.9"})).json()["id"])
        feed = (await c.get("/api/notifications")).json()
        n = feed["items"][0]
        assert n["type"] == "payment_auto" and n["payment_id"] == 7 and n["title"] == "자동으로 결제했어요: 12,000원"
        assert n["body"] == "shop.example.com · 커피 원두" and feed["unseen"] == 1
        assert (await c.get("/api/notifications", params={"after": feed["last_id"]})).json()["items"] == []
        assert (await c.post("/api/wallet/notifications/seen", json={"up_to": 7})).json()["ok"] is True
        assert (await c.get("/api/notifications")).json()["unseen"] == 0      # the wallet's "seen" counts in the feed too
        assert (await c.get("/api/wallet/notifications")).status_code == 200   # the wallet's own list still works


def test_a_restart_ends_running_tasks_and_waiting_approvals(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    a = Activity(store)
    tid = a.start_task("긴 작업")
    a.log_approval("ap1", task_id=tid, tool="send", label="작업 중", site="", detail="d")
    a2 = Activity(Store(str(tmp_path / "r.db")))
    t = a2.task(tid)
    assert t["status"] == "interrupted" and "server restarted" in t["result"]   # not "stopped": the user did not stop it (resume.py)
    assert a2.approval("ap1")["decision"] == "cancelled"
    assert json.dumps(a2.approvals())   # serialisable


async def test_profile_says_whether_a_main_model_is_chosen(tmp_path):
    """The terminal setup no longer asks for a model: the app leads a new owner to its model settings while this is false."""
    s = app_settings(tmp_path)
    s.provider, s.model = "", ""
    async with Running(create_app(s, tools=[], stream_fn=answering("x"))) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/profile")).json()["model_ready"] is False
        put = await c.put("/api/profile", json={"provider": "openrouter", "model": "some/model"})
        assert put.status_code == 200 and put.json()["model_ready"] is True
        assert (await c.get("/api/profile")).json()["model_ready"] is True
    async with Running(create_app(app_settings(tmp_path / "b"), tools=[], stream_fn=answering("x"))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/profile")).json()["model_ready"] is True      # the server's default (ASSISTANT_PROVIDER/MODEL)


async def test_several_calendar_changes_of_one_chat_step_share_one_card_over_http(tmp_path):
    """One step with a delete and two adds, asked for in the owner's chat without a card each: the apps get ONE approval_request whose
    `lines` list the changes (and `steps` for each), one answer runs all of them; a second run declined by the owner runs none."""
    from assistant.agent import Tool
    from assistant.providers import Done, TextDelta, ToolCall
    ran = []

    def tool(name):
        async def handler(args):
            ran.append((name, args))
            return f"{name} done"
        return Tool(name, name, {"type": "object"}, handler, requires_approval=True, shares_card=True,
                    describe=lambda args: f"{name} {args.get('title') or args.get('event_id')}")
    tools = [tool("gcal_delete_event"), tool("gcal_create_event")]
    script = iter([
        [ToolCall("d", "gcal_delete_event", '{"event_id":"e1"}'), ToolCall("a", "gcal_create_event", '{"title":"Lunch"}'),
         ToolCall("b", "gcal_create_event", '{"title":"Dinner"}'), Done("tool_calls")], [TextDelta("ok"), Done("stop")],
        [ToolCall("d2", "gcal_delete_event", '{"event_id":"e2"}'), ToolCall("a2", "gcal_create_event", '{"title":"Tea"}'), Done("tool_calls")],
        [TextDelta("ok"), Done("stop")]])

    async def fn(provider, request, **kw):
        for ev in next(script):
            yield ev

    async def turn(c, text, answer):
        tid = (await c.post("/api/turns", json={"text": text})).json()["id"]
        events, after = [], 0
        for _ in range(200):
            r = (await c.get(f"/api/turns/{tid}", params={"after": after, "wait": 2})).json()
            events += r["events"]
            after = r["next"]
            for ev in r["events"]:
                if ev["type"] == "approval_request":
                    got = (await c.post(f"/api/approvals/{ev['approval_id']}", json=answer)).json()
                    events.append({"type": "answered", **got})
            if r["done"]:
                return events
        raise AssertionError("the reply did not end")

    async with Running(create_app(app_settings(tmp_path, payment_specialist=False), tools=tools, stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        first = await turn(c, "정리해줘", {"approve": True})
        cards = [e for e in first if e["type"] == "approval_request"]
        assert len(cards) == 1
        card = cards[0]
        assert card["ask"] and [s["name"] for s in card["steps"]] == ["gcal_delete_event", "gcal_create_event", "gcal_create_event"]
        assert len(card["lines"]) == 3 and card["lines"][0]["label"] == "1"
        assert [e["output"] for e in first if e["type"] == "tool_result"] == ["gcal_delete_event done", "gcal_create_event done",
                                                                              "gcal_create_event done"]
        assert len(ran) == 3
        second = await turn(c, "하나 더", {"approve": False})
        assert len([e for e in second if e["type"] == "approval_request"]) == 1
        assert [e["is_error"] for e in second if e["type"] == "tool_result"] == [True, True]
        assert len(ran) == 3
