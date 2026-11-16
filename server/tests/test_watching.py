"""Scheduling and watching (S2): quiet runs, self-pause, run history, interval and "around" schedules, archive and restore,
approvals kept for one scheduled job (never payments), goal-owned jobs, the periodic checklist, and watchers (fake clock, fake model,
fake pages)."""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant import i18n
from assistant.activity import Activity, schedule_rule_ok
from assistant.agent import Tool
from assistant.checklist import Checklist, checklist_items
from assistant.home import Home
from assistant.netguard import NetGuardError
from assistant.providers import Done, TextDelta, ToolCall
from assistant.schedules import ARCHIVE_DAYS, ScheduleError, Scheduler, Schedules, schedule_tools
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.watchers import (
    WATCH_MARK, Page, WatchError, Watchers, compile_selector, evaluate, parse_html, parse_number, public_get, select, text_of, watch_tools,
)

from test_server import AUTH, TOKEN, Running, follow

SEOUL = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[2]


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(t=None):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    clock = Clock(t or ts(2026, 11, 7, 8, 0))
    return store, clock, Schedules(store, clock)


def settings(tmp_path, **kw):
    return Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False, **kw)


def call(name, args=None, cid=None):
    return [ToolCall(cid or f"c-{name}", name, json.dumps(args or {}, ensure_ascii=False)), Done("tool_calls")]


def say(text):
    return [TextDelta(text), Done("stop")]


def model(script):
    """A fake model: script(request, n) -> events; n counts the calls."""
    calls = []

    async def fn(provider, request, **kw):
        calls.append(request)
        for ev in script(request, len(calls) - 1):
            yield ev
    fn.calls = calls
    return fn


async def until(cond, tries=300, pause=0.02):
    for _ in range(tries):
        got = cond()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        await asyncio.sleep(pause)
    raise AssertionError("never happened")


# ---------------------------------------------------------------------------------------------------------- kinds of "when"
def test_interval_schedules_run_every_n_minutes_from_their_start():
    _, clock, s = make()
    j = s.create("환율", "환율 확인", every_minutes=90)
    assert j["kind"] == "interval" and j["description"] == "Every 1 h 30 min" and j["every_minutes"] == 90
    assert j["next_run_ts"] == clock.t + 90 * 60
    assert s.create("a", "b", every_minutes=120)["description"] == "Every 2 hours"
    assert s.create("c", "d", every_minutes=30)["description"] == "Every 30 minutes"
    started = s.create("e", "f", every_minutes=60, run_at="2026-11-07T09:30")
    assert started["next_run"] == "2026-11-07T09:30+09:00" and started["start_at"] == "2026-11-07T09:30+09:00"
    with pytest.raises(ScheduleError, match="5 minutes"):
        s.create("x", "y", every_minutes=3)
    with pytest.raises(ScheduleError, match="not two"):
        s.create("x", "y", every_minutes=30, cron="0 9 * * *")
    r = s._row(j["id"])
    s.advance(r, clock.t + 90 * 60 + 5)                       # it came due and ran: the next one is one interval later
    assert s.get(j["id"])["next_run_ts"] == clock.t + 180 * 60
    off = s.update(j["id"], enabled=False)
    on = s.update(j["id"], enabled=True)
    assert off["next_run"] is None and on["next_run_ts"] == clock.t + 90 * 60   # back on the same grid


def test_window_schedules_spread_runs_around_a_time_and_stay_stable():
    _, clock, s = make()
    w = s.create("아침", "브리핑", cron="0 9 * * *", window_minutes=15)
    assert w["kind"] == "window" and w["description"] == "Every day at 9:00 AM, give or take 15 minutes" and w["window_minutes"] == 15
    center = ts(2026, 11, 7, 9, 0)
    assert center - 15 * 60 <= w["next_run_ts"] <= center + 15 * 60
    assert s._next(s._row(w["id"]), clock.t) == w["next_run_ts"]          # the same time every time it is worked out
    nxt = s._next(s._row(w["id"]), w["next_run_ts"])                       # after that run: around 9 the next day
    assert ts(2026, 11, 8, 8, 45) <= nxt <= ts(2026, 11, 8, 9, 15)
    offsets = {s.create(f"n{i}", "p", cron="0 9 * * *", window_minutes=30)["next_run_ts"] for i in range(8)}
    assert len(offsets) > 1                                                # many jobs at "9" do not all start at once
    with pytest.raises(ScheduleError, match="too wide"):
        s.create("x", "y", cron="0 * * * *", window_minutes=30)            # hourly runs ±30 min would overlap
    with pytest.raises(ScheduleError, match="goes with cron"):
        s.create("x", "y", run_at="2026-11-08T09:00", window_minutes=10)


async def test_the_tools_take_the_new_kinds_and_describe_them_in_korean():
    _, clock, s = make()
    tools = {t.name: t for t in schedule_tools(s)}
    args = {"name": "시세", "prompt": "중고 시세 확인", "every_minutes": 120}
    assert tools["schedule_create"].validate(args) is None
    assert "Every 2 hours" in await tools["schedule_create"].describe(args)
    assert "give or take 10 minutes" in await tools["schedule_create"].describe({"name": "a", "prompt": "b", "cron": "0 8 * * *", "window_minutes": 10})
    assert "5 minutes" in tools["schedule_create"].validate({**args, "every_minutes": 1})
    assert "#" in await tools["schedule_create"].handler(args)
    for t in tools.values():
        assert not re.search(r"\b[\w-]+\.(com|net|org|kr|io)\b", t.description)


# ---------------------------------------------------------------------------------------------------------- run history, pause
async def test_run_history_records_done_quiet_failed_skipped_and_paused_runs():
    _, clock, s = make()
    gate = asyncio.Event()
    outs = iter([{"task_id": 11, "status": "done", "result": "환율 1,380원"}, {"task_id": 12, "status": "done", "quiet": True},
                 {"task_id": 13, "status": "failed", "error": "site down"}])

    async def run(sched):
        if sched["name"] == "느림":
            await gate.wait()
            return {"task_id": 20, "status": "done", "result": "끝"}
        return next(outs)

    sch = Scheduler(s, run, clock=clock)
    j = s.create("환율", "환율 확인", cron="0 * * * *")
    for _ in range(3):
        assert sch.start_run(s.get(j["id"]))
        await asyncio.sleep(0.02)
    runs = s.runs(j["id"])
    assert [r["status"] for r in runs] == ["failed", "quiet", "done"]
    assert runs[2]["result"] == "환율 1,380원" and runs[2]["task_id"] == 11 and runs[0]["result"] == "site down"
    assert s.get(j["id"])["last_run"]["status"] == "failed" and s.get(j["id"])["failure_count"] == 1
    slow = s.create("느림", "오래 걸림", cron="*/10 * * * *")
    clock.t = ts(2026, 11, 7, 8, 10, 1)
    assert sch.tick() == [slow["id"]]
    clock.t = ts(2026, 11, 7, 8, 20, 1)
    sch.tick()                                                    # still running: this time is skipped, and the skip is logged
    assert [r["status"] for r in s.runs(slow["id"])] == ["skipped", "running"]
    gate.set()
    await asyncio.sleep(0.02)
    assert [r["status"] for r in s.runs(slow["id"])] == ["skipped", "done"]
    # a run that pauses its own schedule shows as paused, with the reason as its line
    p = s.create("로그인", "주문 확인", cron="0 9 * * *")

    async def pausing(sched):
        s.pause(sched["id"], "로그인이 만료됐어요: 다시 저장해 주세요")
        return {"task_id": 30, "status": "done", "result": "멈췄어요"}

    Scheduler(s, pausing, clock=clock).start_run(s.get(p["id"]))
    await asyncio.sleep(0.02)
    got = s.get(p["id"])
    assert got["paused"] and got["enabled"] is False and got["next_run"] is None and "만료" in got["paused_reason"]
    assert s.runs(p["id"])[0]["status"] == "paused" and "만료" in s.runs(p["id"])[0]["result"]
    back = s.update(p["id"], enabled=True)
    assert back["enabled"] and not back["paused"] and back["paused_reason"] is None


async def test_schedule_pause_tool_needs_a_reason_defaults_to_the_running_job_and_notifies():
    _, clock, s = make()
    paused = []
    s.on_paused = paused.append
    tools = {t.name: t for t in schedule_tools(s)}
    j = s.create("주문", "주문 상태 확인", cron="0 9 * * *")
    assert not tools["schedule_pause"].requires_approval
    assert "reason" in tools["schedule_pause"].validate({"id": j["id"]})
    from assistant.workers import current_turn
    tok = current_turn.set({"source": f"schedule:{j['id']}"})
    try:
        assert tools["schedule_pause"].validate({"reason": "계정이 잠겼어요"}) is None
        out = await tools["schedule_pause"].handler({"reason": "계정이 잠겼어요"})
    finally:
        current_turn.reset(tok)
    assert "Paused" in out and paused and paused[0]["id"] == j["id"] and paused[0]["paused_by"] == "agent"
    assert "paused (계정이 잠겼어요)" in await tools["schedule_list"].handler({})
    assert tools["schedule_runs"].read_only and "has not run" in await tools["schedule_runs"].handler({"id": j["id"]})


def test_missed_runs_are_logged_as_skipped():
    store, clock, s = make()
    j = s.create("아침", "a", cron="0 9 * * *")
    clock.t = ts(2026, 11, 8, 7, 0)                      # 09:00 yesterday is long gone
    Scheduler(s, None, clock=clock).tick()
    assert s.runs(j["id"])[0]["status"] == "skipped" and "server was off" in s.runs(j["id"])[0]["result"]


# ---------------------------------------------------------------------------------------------------------- archive, goals
def test_delete_archives_for_30_days_and_restore_brings_it_back():
    store, clock, s = make()
    j = s.create("주간", "정리", cron="0 9 * * 1")
    once = s.create("한 번", "알림", run_at="2026-11-07T10:00")
    assert s.delete(j["id"]) and s.delete(once["id"]) and not s.delete(j["id"])
    assert s.get(j["id"]) is None and s.list() == [] and s.due(ts(2026, 11, 9, 9, 0, 5)) == []
    arch = s.archived()
    assert {a["id"] for a in arch} == {j["id"], once["id"]} and arch[0]["purge_at"] == arch[0]["archived_at"] + ARCHIVE_DAYS * 86400
    clock.t = ts(2026, 11, 7, 11, 0)
    back = s.restore(j["id"])
    assert back["enabled"] and back["next_run"] == "2026-11-09T09:00+09:00"
    gone = s.restore(once["id"])                         # its time has passed: back, but off
    assert gone["enabled"] is False and "its time has passed" in gone["disabled_reason"]
    k = s.create("또", "x", cron="0 8 * * *")
    s.delete(k["id"])
    clock.t += 31 * 86400
    assert s.restore(k["id"]) is None
    assert s.purge_archived() == 1 and s.archived() == []


def test_schedules_can_belong_to_a_goal():
    store, clock, s = make()
    g = store.add_goal("마라톤 완주", "건강")
    j = s.create("훈련 알림", "오늘 훈련 계획", cron="0 7 * * *", goal_id=g["id"])
    s.create("다른 일", "x", cron="0 8 * * *")
    assert j["goal_id"] == g["id"] and [x["id"] for x in s.list(g["id"])] == [j["id"]] and len(s.list()) == 2
    with pytest.raises(ScheduleError, match="no goal"):
        s.create("x", "y", cron="0 9 * * *", goal_id=999)
    assert s.update(j["id"], goal_id=0)["goal_id"] is None


# ---------------------------------------------------------------------------------------------------------- scoped approvals
def test_rules_for_one_scheduled_job_never_cover_payments_and_only_that_job():
    a = Activity(Store())
    for never in ("confirm_purchase", "browser_fill_card", "wallet_cards", "schedule_update", "schedule_delete"):
        assert not schedule_rule_ok(never) and a.add_schedule_rule(1, never, "shop.example.com") is None
    r = a.add_schedule_rule(1, "email_send", "")
    assert r["tool"] == "email_send" and a.matching_schedule_rule(1, "email_send", "")["id"] == r["id"]
    assert a.matching_schedule_rule(2, "email_send", "") is None                 # another job
    assert a.matching_schedule_rule(None, "email_send", "") is None              # a chat turn
    site = a.add_schedule_rule(1, "browser_click", "https://www.shop.example.com/cart")
    assert site["site"] == "shop.example.com" and a.matching_schedule_rule(1, "browser_click", "m.shop.example.com")
    assert a.matching_schedule_rule(1, "browser_click", "example.com") is None
    assert a.delete_schedule_rule(site["id"], 1) and a.schedule_rules(1) == [a.schedule_rules()[0]]


async def test_keep_allowing_in_this_scheduled_job_skips_the_card_next_time_but_never_for_payments(tmp_path):
    sent = []

    async def send_note(args):
        sent.append(args)
        return "sent"

    async def pay(args):
        return "paid"

    tools = [Tool("send_note", "Send a note", {"type": "object", "properties": {"to": {"type": "string"}}}, send_note, requires_approval=True),
             Tool("confirm_purchase", "Pay", {"type": "object", "properties": {}}, pay, requires_approval=True)]

    def script(req, n):
        if req.messages[-1].get("role") == "tool":
            return say("보냈어요.")
        last = str(req.messages[-1].get("content"))
        return call("confirm_purchase", {}) if "결제" in last else call("send_note", {"to": "팀"})

    fn = model(script)
    async with Running(create_app(settings(tmp_path), tools=tools, stream_fn=fn, with_schedule_tools=True)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "주간 보고", "prompt": "팀에 메모 보내기", "cron": "0 9 * * 1"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        pending = await until(lambda: _pending(c))
        assert pending["schedule_id"] == s["id"] and pending["can_remember_schedule"] is True
        ans = (await c.post(f"/api/approvals/{pending['approval_id']}", json={"approve": True, "remember": "schedule"})).json()
        assert ans["decision"] == "allowed_always" and ans["rule"]["schedule_id"] == s["id"]
        await follow(c, run["turn_id"])
        assert (await c.get(f"/api/schedules/{s['id']}/rules")).json()["items"][0]["tool"] == "send_note"
        await until(lambda: _idle_sched(c, s["id"]))
        run2 = (await c.post(f"/api/schedules/{s['id']}/run")).json()     # the next run: no card
        events = await follow(c, run2["turn_id"])
        assert not any(e["type"] == "approval_request" for e in events) and len(sent) == 2
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["decision"] == "allowed_by_rule"
        # a payment in a scheduled run: the card never offers it, and asking for it anyway is an ordinary allow
        p = (await c.post("/api/schedules", json={"name": "결제", "prompt": "결제 진행", "cron": "0 9 * * 2"})).json()
        run3 = (await c.post(f"/api/schedules/{p['id']}/run")).json()
        pay_card = await until(lambda: _pending(c))
        assert pay_card["name"] == "confirm_purchase" and pay_card["can_remember_schedule"] is False
        ans = (await c.post(f"/api/approvals/{pay_card['approval_id']}", json={"approve": True, "remember": "schedule"})).json()
        assert ans["decision"] == "allowed_once" and ans["remembered"] is False
        await follow(c, run3["turn_id"])
        assert (await c.get(f"/api/schedules/{p['id']}/rules")).json()["items"] == []


async def _pending(c):
    items = (await c.get("/api/approvals/pending")).json()["items"]
    return items[0] if items else None


async def _idle_sched(c, sid):
    return not (await c.get(f"/api/schedules/{sid}")).json()["running"]


# ---------------------------------------------------------------------------------------------------------- quiet runs, self-pause, API
async def test_a_quiet_run_says_nothing_but_is_in_the_history(tmp_path):
    fn = model(lambda req, n: say("NO_REPLY"))
    async with Running(create_app(settings(tmp_path), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "가격 확인", "prompt": "가격이 내려갔는지 확인", "cron": "0 9 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        await follow(c, run["turn_id"])
        await until(lambda: _last_status(c, s["id"]))
        assert "NO_REPLY" in fn.calls[0].messages[-1]["content"] and "schedule_pause" in fn.calls[0].messages[-1]["content"]
        assert (await c.get("/api/messages")).json() == []                                   # nothing joins the chat
        assert (await c.get("/api/notifications")).json()["items"] == []                     # no notification, no push
        runs = (await c.get(f"/api/schedules/{s['id']}/runs")).json()["items"]
        assert runs[0]["status"] == "quiet" and runs[0]["task_id"] == run["task_id"]
        task = (await c.get(f"/api/tasks/{run['task_id']}")).json()
        assert task["status"] == "done" and task["result"] == "There was nothing to tell"
        assert (await c.get(f"/api/schedules/{s['id']}")).json()["last_run"]["status"] == "quiet"


async def _last_status(c, sid):
    got = (await c.get(f"/api/schedules/{sid}")).json()
    return got["last_run"] and got["last_run"]["status"] != "running"


async def test_a_run_that_hits_a_lasting_blocker_pauses_its_schedule_with_a_notification(tmp_path):
    def script(req, n):
        if req.messages[-1].get("role") == "tool":
            return say("로그인이 만료돼서 멈췄어요.")
        return call("schedule_pause", {"reason": "사이트 로그인이 만료됐어요. 앱에서 다시 저장해 주세요."})

    fn = model(script)
    async with Running(create_app(settings(tmp_path), tools=[], stream_fn=fn, with_schedule_tools=True)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "주문 확인", "prompt": "주문 상태 확인", "cron": "0 9 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        await follow(c, run["turn_id"])
        await until(lambda: _last_status(c, s["id"]))
        got = (await c.get(f"/api/schedules/{s['id']}")).json()
        assert got["paused"] and got["enabled"] is False and "만료" in got["paused_reason"]
        notes = (await c.get("/api/notifications")).json()["items"]
        assert any(n["type"] == "schedule_paused" and n["schedule_id"] == s["id"] and "만료" in n["body"] for n in notes)
        assert (await c.get(f"/api/schedules/{s['id']}/runs")).json()["items"][0]["status"] == "paused"
        back = (await c.post(f"/api/schedules/{s['id']}/resume")).json()
        assert back["enabled"] and not back["paused"]
        mine = (await c.post(f"/api/schedules/{s['id']}/pause", json={"reason": "휴가 중"})).json()
        assert mine["paused_reason"] == "휴가 중"
        assert sum(n["type"] == "schedule_paused" for n in (await c.get("/api/notifications")).json()["items"]) == 1   # not for my own


async def test_schedule_api_takes_intervals_windows_goals_and_archives_on_delete(tmp_path):
    async with Running(create_app(settings(tmp_path), tools=[], stream_fn=model(lambda r, n: say("ok")))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        g = (await c.post("/api/goals", json={"title": "영어 공부"})).json()
        a = (await c.post("/api/schedules", json={"name": "단어", "prompt": "단어 퀴즈", "every_minutes": 240, "goal_id": g["id"]})).json()
        assert a["kind"] == "interval" and a["description"] == "Every 4 hours" and a["goal_id"] == g["id"]
        b = (await c.post("/api/schedules", json={"name": "아침", "prompt": "브리핑", "cron": "0 8 * * *", "window_minutes": 20})).json()
        assert b["kind"] == "window" and "give or take 20 minutes" in b["description"]
        assert (await c.post("/api/schedules", json={"name": "x", "prompt": "y", "every_minutes": 2})).status_code == 422
        assert (await c.post("/api/schedules", json={"name": "x", "prompt": "y", "cron": "0 9 * * *", "goal_id": 77})).status_code == 422
        assert [x["id"] for x in (await c.get("/api/schedules", params={"goal_id": g["id"]})).json()["items"]] == [a["id"]]
        assert [x["id"] for x in (await c.get(f"/api/goals/{g['id']}/schedules")).json()["items"]] == [a["id"]]
        assert (await c.delete(f"/api/schedules/{b['id']}")).json() == {"ok": True}
        assert (await c.get(f"/api/schedules/{b['id']}")).status_code == 404
        arch = (await c.get("/api/schedules/archive")).json()
        assert arch["keep_days"] == 30 and [x["id"] for x in arch["items"]] == [b["id"]]
        back = (await c.post(f"/api/schedules/{b['id']}/restore")).json()
        assert back["id"] == b["id"] and back["enabled"] and back["archived_at"] is None
        assert (await c.post(f"/api/schedules/{b['id']}/restore")).status_code == 404
        assert (await c.get(f"/api/schedules/{b['id']}/runs")).json() == {"items": []}
        patched = (await c.patch(f"/api/schedules/{b['id']}", json={"every_minutes": 45})).json()
        assert patched["kind"] == "interval" and patched["every_minutes"] == 45



async def test_a_schedule_can_be_deleted_for_good_from_the_archive_or_at_once(tmp_path):
    async with Running(create_app(settings(tmp_path), tools=[], stream_fn=model(lambda r, n: say("ok")))) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        a = (await c.post("/api/schedules", json={"name": "로또", "prompt": "로또 5게임", "cron": "0 12 * * 1"})).json()
        b = (await c.post("/api/schedules", json={"name": "아침", "prompt": "브리핑", "cron": "0 8 * * *"})).json()
        # 보관함에 넣기, then 삭제 there: gone for good, not restorable
        assert (await c.delete(f"/api/schedules/{a['id']}")).json() == {"ok": True}
        assert (await c.delete(f"/api/schedules/archive/{b['id']}")).status_code == 404          # not archived: refused
        assert (await c.delete(f"/api/schedules/archive/{a['id']}")).json() == {"ok": True}
        assert (await c.get("/api/schedules/archive")).json()["items"] == []
        assert (await c.post(f"/api/schedules/{a['id']}/restore")).status_code == 404
        assert (await c.delete(f"/api/schedules/archive/{a['id']}")).status_code == 404
        # the dialog's 삭제: deleted at once, never in the archive
        assert (await c.delete(f"/api/schedules/{b['id']}", params={"forever": "true"})).json() == {"ok": True}
        assert (await c.get(f"/api/schedules/{b['id']}")).status_code == 404
        assert (await c.get("/api/schedules/archive")).json()["items"] == []
        assert (await c.get("/api/schedules")).json()["items"] == []


# ---------------------------------------------------------------------------------------------------------- the checklist
def test_an_untouched_korean_seed_becomes_the_english_template_and_an_edited_one_stays(tmp_path):
    from assistant.home import SIDE_TEMPLATES
    root = tmp_path / "home"
    root.mkdir()
    (root / "CHECKLIST.md").write_text("# CHECKLIST.md\n\n자주 확인할 짧은 일을 한 줄에 하나씩 \"- \"로 적어요. 비어 있으면 확인하지 않아요.\n",
                                       encoding="utf-8")
    (root / "NOTIFY_PREFS.md").write_text("# NOTIFY_PREFS.md\n\n먼저 알릴 때 지킬 것.\n\n## 알려 줘\n- 택배\n", encoding="utf-8")
    home = Home(root)
    home.ensure_side_files()
    assert (root / "CHECKLIST.md").read_text() == SIDE_TEMPLATES["CHECKLIST.md"]
    assert "택배" in (root / "NOTIFY_PREFS.md").read_text()                                   # the user's own edits stay
    (root / "CHECKLIST.md").write_text("# CHECKLIST.md\n\n자주 확인할 짧은 일\n- 우산\n", encoding="utf-8")
    home.ensure_side_files()
    assert "우산" in (root / "CHECKLIST.md").read_text()


def test_checklist_items_are_list_lines_with_words():
    text = "# CHECKLIST.md\n\n자주 확인할 짧은 일을 한 줄에 하나씩 \"- \"로 적어요.\n- \n- 오늘 일정 30분 전이면 알려 주기\n* [ ] 새 메일 중 답장 필요한 것\n1. 택배 도착"
    assert checklist_items(text) == ["오늘 일정 30분 전이면 알려 주기", "새 메일 중 답장 필요한 것", "택배 도착"]
    assert checklist_items("# CHECKLIST.md\n\n비어 있어요\n") == []


async def test_checklist_job_runs_only_when_on_non_empty_and_awake_and_records_quiet_or_done(tmp_path):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    home = Home(tmp_path / "home")
    clock = Clock(ts(2026, 11, 7, 10, 0))
    awake = {"on": True}
    seen, outs = [], iter([{"task_id": 1, "status": "done", "quiet": True}, {"task_id": 2, "status": "done", "quiet": False}])

    async def start(body):
        seen.append(body)
        return next(outs)

    switch, room, said = {"on": True}, {"ok": True}, []
    ch = Checklist(store, home, start, clock=clock, waking=lambda t: awake["on"], switch=lambda: switch["on"], room=lambda: room["ok"],
                   pace=lambda: 30 * 60.0, on_said=said.append)
    assert (home.root / "CHECKLIST.md").is_file() and (home.root / "NOTIFY_PREFS.md").is_file()   # made on start
    assert "proactive pass" in (home.root / "CHECKLIST.md").read_text()                          # the English template
    assert (await ch.tick())["status"] == "skipped" and seen == []                               # empty: no model call
    ch.update(text="# CHECKLIST.md\n\n- 새 메일 중 급한 것\n")
    awake["on"] = False
    assert (await ch.tick())["reason"] == "It is quiet time" and seen == []
    awake["on"] = True
    room["ok"] = False
    assert (await ch.tick())["reason"] == "Today's limit is reached" and seen == []
    room["ok"] = True
    assert (await ch.tick())["status"] == "quiet" and "새 메일 중 급한 것" in seen[0] and said == []
    assert (await ch.tick())["status"] == "done" and ch.last_run()["task_id"] == 2 and said == [2]
    switch["on"] = False                                                                          # the proactive switch
    assert (await ch.tick())["reason"] == "It is turned off" and len(seen) == 2
    assert ch.public()["enabled"] is False and ch.public()["every_minutes"] == 30
    ch.update(enabled=True, every_minutes=2)                                                      # retired fields: ignored
    assert ch.public()["enabled"] is False
    switch["on"] = True
    # the list alone can be replaced; the heading and notes stay
    ch.update(items=["  우산 챙기기 ", "", "택배 확인"])
    assert ch.items() == ["우산 챙기기", "택배 확인"] and ch.text().startswith("# CHECKLIST.md\n") and ch.public()["list"] == ch.items()
    ch.update(items=[])
    assert ch.items() == [] and ch.text().startswith("# CHECKLIST.md")
    # the scheduler runs it every period (the first period starts with the server)
    sched = Scheduler(Schedules(store, clock), None, clock=clock)
    runs = []

    async def tick():
        runs.append(clock.t)
    sched.add_every("checklist", ch.period, tick)
    sched.tick()
    clock.t += 29 * 60
    sched.tick()
    clock.t += 2 * 60
    sched.tick()
    await asyncio.sleep(0.01)
    assert len(runs) == 1


async def test_checklist_over_http_is_quiet_unless_something_needs_attention(tmp_path):
    answers = iter(["NO_REPLY", "택배가 도착했어요. 문 앞을 확인해 주세요."])
    fn = model(lambda req, n: say(next(answers)))
    s = settings(tmp_path, home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        got = (await c.get("/api/checklist")).json()
        assert not got["enabled"] and got["every_minutes"] == 120 and got["items"] == 0 and got["list"] == []   # proactive is off
        assert (await c.post("/api/checklist/run")).json()["status"] == "skipped" and fn.calls == []
        assert (await c.put("/api/checklist", json={"text": "- 택배 도착했는지 확인"})).json()["items"] == 1
        assert (await c.put("/api/checklist", json={"every_minutes": 5})).status_code == 200    # retired: ignored
        assert (await c.put("/api/proactive", json={"enabled": True})).status_code == 200
        assert (await c.get("/api/checklist")).json()["enabled"] is True
        r = (await c.post("/api/checklist/run")).json()
        assert r["status"] == "quiet" and "CHECKLIST.md" in fn.calls[0].messages[-1]["content"]
        assert (await c.get("/api/messages")).json() == [] and (await c.get("/api/notifications")).json()["items"] == []
        r = (await c.post("/api/checklist/run")).json()
        assert r["status"] == "done"
        msgs = (await c.get("/api/messages")).json()
        assert msgs[-1]["content"].startswith("[Recurring check]\n택배가 도착했어요")
        note = (await c.get("/api/notifications")).json()["items"][0]
        assert note["type"] == "task_done" and note["title"] == "Take a look: Recurring check"
        assert (await c.get(f"/api/tasks/{r['task_id']}")).json()["source"] == "checklist"
        assert (await c.get("/api/proactive")).json()["today"]["오늘"] == 1                    # counted toward the daily cap
        log = (await c.get("/api/proactive/log")).json()["items"]
        assert log[0]["source"] == "checklist" and log[0]["task_id"] == r["task_id"]
        listed = (await c.put("/api/checklist", json={"items": ["우산", "택배"]})).json()
        assert listed["list"] == ["우산", "택배"] and listed["items"] == 2


# ---------------------------------------------------------------------------------------------------------- reading pages
PAGE = """<html><head><title>중고 자전거 · 상세</title><style>.x{}</style><script>var price=1;</script></head><body>
<div id="main"><h1 class="title big">로드 자전거</h1>
<div class="price-box"><span class="label">가격</span> <span class="price" data-kind="now">1,250,000원</span></div>
<ul class="tags"><li>중고</li><li class="sold">판매중</li></ul>
<p>배송 <b>가능</b></p></div></body></html>"""


def test_a_small_css_subset_and_page_text_work_without_a_browser():
    root = parse_html(PAGE).root
    assert [text_of(n) for n in select(root, ".price")] == ["1,250,000원"]
    assert text_of(select(root, "#main > h1.title")[0]) == "로드 자전거"
    assert text_of(select(root, "div.price-box span[data-kind=now]")[0]) == "1,250,000원"
    assert text_of(select(root, "ul li.sold")[0]) == "판매중"
    assert select(root, "body > h1") == [] and len(select(root, "li, h1")) == 3
    assert text_of(select(root, "span[data-kind^=no]")[0]) == "1,250,000원"
    whole = text_of(root)
    assert "var price" not in whole and "로드 자전거" in whole and "배송 가능" in whole
    assert parse_number("가격 1,250,000원") == 1250000 and parse_number("−3.5도") == -3.5 and parse_number("없음") is None
    for bad in ("", "a:hover", "div::before"):
        with pytest.raises(WatchError):
            compile_selector(bad)


def test_conditions_read_text_numbers_and_changes():
    base = {"selector": "", "regex": "", "match": "", "threshold": None, "last_value": None}
    assert evaluate({**base, "mode": "text_appears", "match": "판매중"}, PAGE)["satisfied"]
    gone = evaluate({**base, "mode": "text_disappears", "match": "품절"}, PAGE)
    assert gone["satisfied"] and gone["detail"] == "'품절' is gone"
    tok = i18n.use("ko")                                          # the same finding in the Korean app
    try:
        assert evaluate({**base, "mode": "text_disappears", "match": "품절"}, PAGE)["detail"] == "'품절'이(가) 사라졌어요"
    finally:
        i18n.reset(tok)
    below = evaluate({**base, "mode": "number_below", "selector": ".price", "threshold": 1_300_000}, PAGE)
    assert below["satisfied"] and below["value"] == "1,250,000" and "1,300,000" in below["detail"]
    above = evaluate({**base, "mode": "number_above", "regex": r"가격</span>\s*<span[^>]*>([\d,]+)", "threshold": 2_000_000}, PAGE)
    assert above["ok"] is False                                   # regex runs on the page text, not the HTML
    above = evaluate({**base, "mode": "number_above", "regex": r"가격\s*([\d,]+)원", "threshold": 1_000_000}, PAGE)
    assert above["satisfied"] and above["value"] == "1,250,000"
    missing = evaluate({**base, "mode": "number_below", "selector": ".nope", "threshold": 1}, PAGE)
    assert missing["ok"] is False and "Found nothing" in missing["detail"]
    first = evaluate({**base, "mode": "changed", "selector": ".price"}, PAGE)
    assert first["ok"] and not first["satisfied"]
    same = evaluate({**base, "mode": "changed", "selector": ".price", "last_value": first["value"]}, PAGE)
    assert not same["satisfied"]
    moved = evaluate({**base, "mode": "changed", "selector": ".price", "last_value": first["value"]}, PAGE.replace("1,250,000", "990,000"))
    assert moved["satisfied"] and "990,000" in moved["detail"]


async def test_the_server_fetch_reaches_only_public_https_pages():
    public = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]   # noqa: E731
    private = lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))]       # noqa: E731
    for url, why in (("http://example.com/", "https"), ("https://127.0.0.1/", "public host"), ("https://router.local/", "public"),
                     ("https://u:p@example.com/", "credentials"), ("https://example.com:8443/", "port")):
        with pytest.raises(NetGuardError, match=why):
            await public_get(url, resolver=public)
    with pytest.raises(NetGuardError, match="non-public"):
        await public_get("https://example.com/", resolver=private)
    hops = []

    def handler(request):
        hops.append(str(request.url))
        if len(hops) == 1:
            return httpx.Response(301, headers={"location": "https://other.example.org/item"})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content="<p>재고 있음</p>".encode())

    page = await public_get("https://example.com/item", resolver=public, transport=httpx.MockTransport(handler))
    assert page.url == "https://other.example.org/item" and "재고 있음" in page.body and len(hops) == 2
    with pytest.raises(NetGuardError, match="non-public"):   # a redirect to an internal address is checked like the first hop
        calls = iter([public, private])
        await public_get("https://example.com/item", resolver=lambda *a, **k: next(calls)(*a, **k),
                         transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "https://inside.example.net/"})))


# ---------------------------------------------------------------------------------------------------------- watchers
class FakePages:
    def __init__(self, html=PAGE):
        self.html, self.fail, self.calls = html, None, 0

    async def __call__(self, url):
        self.calls += 1
        if self.fail:
            raise NetGuardError(self.fail)
        return Page(url, 200, "text/html", self.html)


def watch_setup():
    store = Store()
    clock = Clock(1_800_000_000.0)
    pages = FakePages()
    w = Watchers(store, fetch=pages, clock=clock)
    fired, offs = [], []
    w.on_fire = lambda wt, res: fired.append((wt, res))
    w.on_disabled = offs.append
    return store, clock, pages, w, fired, offs


async def test_a_watcher_starts_off_needs_a_dry_run_and_fires_on_a_change_into_its_condition():
    store, clock, pages, w, fired, offs = watch_setup()
    with pytest.raises(WatchError, match="https"):
        w.create(name="x", url="http://example.com", mode="changed")
    with pytest.raises(WatchError, match="selector"):
        w.create(name="x", url="https://example.com", mode="number_below", threshold=1)
    with pytest.raises(WatchError, match="5"):
        w.create(name="x", url="https://example.com", mode="changed", every_minutes=1)
    wt = w.create(name="자전거 가격", url="https://shop.example.com/bike", mode="number_below", selector=".price", threshold=1_000_000,
                  every_minutes=30, note="가격이 내려가면 알려 주기")
    assert wt["enabled"] is False and wt["state"] == "off" and wt["can_enable"] is False
    assert wt["description"] == "Check every 30 minutes · if the value goes below 1,000,000"
    with pytest.raises(WatchError, match="dry run"):
        w.enable(wt["id"])
    dry = await w.dry_run(wt["id"])
    assert dry["ok"] and dry["satisfied"] is False and dry["value"] == "1,250,000"
    on = w.enable(wt["id"])
    assert on["enabled"] and on["state"] == "watching" and on["next_check_at"]
    clock.t = on["next_check_at"] + 1
    assert await w.check_due() == [wt["id"]]
    await asyncio.sleep(0.01)
    assert fired == [] and w.get(wt["id"])["last_checked_at"]
    pages.html = PAGE.replace("1,250,000", "980,000")                # the price drops under the line
    clock.t += 31 * 60
    first = await w.check(wt["id"])
    assert first["confirming"] and fired == []                       # a hit is looked at once more before it fires
    assert w.get(wt["id"])["next_check_at"] == clock.t + 30
    clock.t += 31
    await w.check(wt["id"])
    assert len(fired) == 1 and fired[0][1]["value"] == "980,000" and w.get(wt["id"])["fire_count"] == 1
    clock.t += 31 * 60
    await w.check(wt["id"])                                          # still under the line: not again
    assert len(fired) == 1
    pages.html = PAGE
    await w.check(wt["id"])
    pages.html = PAGE.replace("1,250,000", "900,000")
    await w.check(wt["id"])                                          # back over, then under again: a new hit
    await w.check(wt["id"])
    assert len(fired) == 2
    kinds = [e["kind"] for e in w.events(wt["id"])]
    assert kinds[:2] == ["fired", "fired"] and "dry_run" in kinds and "enabled" in kinds
    w.update(wt["id"], threshold=500_000)                            # what it checks changed: off until a new dry run
    assert w.get(wt["id"])["enabled"] is False and w.get(wt["id"])["can_enable"] is False
    w.update(wt["id"], name="자전거")                                  # a new name is not a new condition
    assert w.get(wt["id"])["name"] == "자전거"


async def test_once_turns_a_watcher_off_after_its_first_hit_and_errors_turn_it_off_after_five():
    store, clock, pages, w, fired, offs = watch_setup()
    wt = w.create(name="재입고", url="https://shop.example.com/x", mode="text_appears", match="재입고", once=True)
    await w.dry_run(wt["id"])
    w.enable(wt["id"])
    pages.html = PAGE.replace("판매중", "재입고 완료")
    await w.check(wt["id"])
    await w.check(wt["id"])
    got = w.get(wt["id"])
    assert len(fired) == 1 and got["enabled"] is False and got["state"] == "done" and "telling you once" in got["disabled_reason"]
    ch = w.create(name="공지", url="https://shop.example.com/notice", mode="changed")
    await w.dry_run(ch["id"])
    w.enable(ch["id"])
    pages.fail = "the page did not answer in time"
    for _ in range(5):
        await w.check(ch["id"])
    got = w.get(ch["id"])
    assert got["enabled"] is False and "5 times in a row" in got["disabled_reason"] and offs and offs[0]["id"] == ch["id"]
    pages.fail = None
    bad = w.create(name="무효", url="https://shop.example.com/y", mode="number_above", selector=".none", threshold=1)
    res = await w.dry_run(bad["id"])
    assert res["ok"] is False and w.get(bad["id"])["can_enable"] is False      # a failed dry run does not let it on
    clock.t += 25 * 3600
    old = w.create(name="오래된 시험", url="https://shop.example.com/z", mode="changed")
    await w.dry_run(old["id"])
    clock.t += 25 * 3600                                                       # a dry run older than a day does not count
    with pytest.raises(WatchError):
        w.enable(old["id"])


async def test_a_script_watcher_runs_in_the_work_room_with_the_page_as_files():
    store = Store()
    got = {}

    async def runner(code, files):
        got["files"] = [f["name"] for f in files]
        return {"exit_code": 0, "stdout": 'checking\n{"hit": true, "value": "3 left"}\n', "stderr": ""}

    w = Watchers(store, fetch=FakePages(), script_runner=runner, clock=Clock(1_800_000_000.0))
    wt = w.create(name="재고", kind="script", url="https://shop.example.com/x", code="print('{\"hit\": true}')")
    res = await w.dry_run(wt["id"])
    assert res["ok"] and res["satisfied"] and res["value"] == "3 left" and got["files"] == ["page.html", "page.txt"]
    with pytest.raises(WatchError, match="code"):
        w.create(name="빈 스크립트", kind="script")
    nowr = Watchers(Store(), fetch=FakePages(), clock=Clock(1.0))
    s2 = nowr.create(name="x", kind="script", code="print(1)")
    assert "work room" in (await nowr.dry_run(s2["id"]))["error"]


async def test_watch_tools_make_the_agent_dry_run_and_the_user_approve_turning_on():
    store, clock, pages, w, fired, offs = watch_setup()
    tools = {t.name: t for t in watch_tools(w)}
    out = await tools["watch_add"].handler({"name": "품절 해제", "url": "https://shop.example.com/x", "mode": "text_disappears",
                                            "match": "품절"})
    assert "off" in out and "watch_dry_run" in out
    wid = w.list()[0]["id"]
    assert tools["watch_enable"].requires_approval and "watch_dry_run" in tools["watch_enable"].validate({"id": wid})
    dry = await tools["watch_dry_run"].handler({"id": wid})
    assert "TRUE right now" in dry and "Show this to the user" in dry
    assert tools["watch_enable"].validate({"id": wid}) is None
    assert "result of the check just now" in await tools["watch_enable"].describe({"id": wid})
    assert tools["watch_enable"].auto_approve({"id": wid, "enabled": False}) and not tools["watch_enable"].auto_approve({"id": wid})
    assert "Turned on" in await tools["watch_enable"].handler({"id": wid})
    assert "품절 해제" in await tools["watch_list"].handler({})
    assert await tools["watch_remove"].handler({"id": wid}) == "Removed." and w.list() == []
    for t in tools.values():
        assert not re.search(r"\b[\w-]+\.(com|net|org|kr|io)\b", t.description)


async def test_a_firing_watcher_notifies_and_wakes_the_agent_with_a_hidden_handoff(tmp_path):
    fn = model(lambda req, n: say("자전거 가격이 98만 원으로 내려갔어요."))
    async with Running(create_app(settings(tmp_path), tools=[], stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        pages = FakePages()
        srv_w = srv.server.config.app.state.watchers
        srv_w.fetch = pages
        made = await c.post("/api/watchers", json={"name": "자전거", "url": "https://shop.example.com/bike", "mode": "number_below",
                                                   "selector": ".price", "threshold": 1000000, "note": "내려가면 알려 줘"})
        assert made.status_code == 201 and made.json()["enabled"] is False
        wid = made.json()["id"]
        assert (await c.post("/api/watchers", json={"name": "x", "url": "https://shop.example.com", "mode": "nope"})).status_code == 422
        assert (await c.post(f"/api/watchers/{wid}/enable", json={"enabled": True})).status_code == 409
        dry = (await c.post(f"/api/watchers/{wid}/dry-run")).json()
        assert dry["result"]["ok"] and dry["result"]["value"] == "1,250,000" and dry["watcher"]["can_enable"]
        assert (await c.post(f"/api/watchers/{wid}/enable", json={"enabled": True})).json()["enabled"]
        pages.html = PAGE.replace("1,250,000", "980,000")
        await srv_w.check(wid)
        await srv_w.check(wid)
        note = await until(lambda: _note(c, "watch_fired"))
        assert note["title"] == "A watched condition came true: 자전거" and "980,000" in note["body"]
        msgs = await until(lambda: _assistant(c))
        assert msgs[-1]["content"] == "자전거 가격이 98만 원으로 내려갔어요."
        assert not any(WATCH_MARK in m["content"] for m in msgs)                       # the handoff is hidden
        sent = fn.calls[0].messages[-1]["content"]
        assert WATCH_MARK in sent and "980,000" in sent and "내려가면 알려 줘" in sent
        await asyncio.sleep(0.1)
        types = [n["type"] for n in (await c.get("/api/notifications")).json()["items"]]
        assert types.count("watch_fired") == 1 and "task_done" not in types           # one notification, not two
        events = (await c.get(f"/api/watchers/{wid}/events")).json()["items"]
        assert events[0]["kind"] == "fired"
        assert (await c.delete(f"/api/watchers/{wid}")).json() == {"ok": True}
        assert (await c.get(f"/api/watchers/{wid}")).status_code == 404


async def _note(c, kind):
    items = (await c.get("/api/notifications")).json()["items"]
    return next((n for n in items if n["type"] == kind), None)


async def _assistant(c):
    msgs = (await c.get("/api/messages")).json()
    return msgs if msgs and msgs[-1]["role"] == "assistant" else None


# ---------------------------------------------------------------------------------------------------------- our own words
def test_the_prompt_lines_are_few_and_generic():
    prompt = (ROOT / "server" / "content" / "prompts" / "system.md").read_text(encoding="utf-8")
    para = next(p for p in prompt.split("\n\n") if p.startswith("**Later and repeated work.**"))
    assert len(para) < 900 and "schedule_create" in para and "watch_add" in para
    assert "NOTIFY_PREFS.md" in prompt and "CHECKLIST.md" in prompt
    from assistant.server import SCHEDULE_NOTE
    assert "NO_REPLY" in SCHEDULE_NOTE and "schedule_pause" in SCHEDULE_NOTE     # what an automatic run must do: in its own note
    for name in ("schedules.py", "watchers.py", "checklist.py", "proactive.py"):
        code = (ROOT / "server" / "src" / "assistant" / name).read_text(encoding="utf-8")
        assert not re.search(r"heartbeat|lotto|로또|coupang|쿠팡|naver|네이버", code, re.I), name
    for text in (para,):
        assert not re.search(r"\b[\w-]+\.(com|net|org|kr|co|io)\b", text)


def test_schedule_create_says_to_ask_when_before_picking_a_time():
    """The user asked for a weekly check without a day or time: the agent asks with a weekday-and-time picker, never picks one."""
    from assistant.goals import QUESTION_KINDS
    _, _clock, s = make()
    desc = {t.name: t for t in schedule_tools(s)}["schedule_create"].description
    assert "never pick it yourself" in desc and "quick_replies" in desc and "1-2 sensible suggestions" in desc
    kind = re.search(r"kind (\w+)", desc).group(1)
    assert kind == "weekdays_time" and kind in QUESTION_KINDS
