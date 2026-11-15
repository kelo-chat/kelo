"""Server 69: a scheduled run blocked for a while (maintenance, a site down, sold out right now) asks to be tried again later
(schedule_retry): the same schedule runs once more, under the same permissions, its regular times untouched, and the run that asked is a
quiet "skipped" row, not a failure. Fake clock, fake runs, a fake model."""

import asyncio
import time

import httpx

from assistant.schedules import MAX_RETRIES, Scheduler, schedule_tools
from assistant.server import create_app
from assistant.workers import current_turn

from test_schedule_grants import world
from test_server import AUTH, Running, follow
from test_watching import call, make, model, say, settings, ts, until

ASK = {"why": "site maintenance"}


class as_run:
    """The code below runs as the scheduled run of `sid` (task `task`)."""

    def __init__(self, sid, task=7):
        self.cur = {"source": f"schedule:{sid}", "task_id": task, "turn_id": f"t{task}", "unattended": True}

    def __enter__(self):
        self.tok = current_turn.set(self.cur)

    def __exit__(self, *a):
        current_turn.reset(self.tok)


def local(*a):
    d = ts(*a)
    return d


def iso(*a) -> str:
    return "%04d-%02d-%02dT%02d:%02d" % a


# ---------------------------------------------------------------------------------------------------------- the tool
async def test_the_tool_works_only_inside_a_scheduled_run():
    _, clock, s = make()
    j = s.create("주문", "check", cron="0 9 * * 1")
    tool = {t.name: t for t in schedule_tools(s)}["schedule_retry"]
    assert not tool.requires_approval
    out = await tool.handler({"at": iso(2026, 11, 7, 10, 0), **ASK})
    assert out == "error: only a scheduled run can do this"
    tok = current_turn.set({"source": "chat", "task_id": 1})   # a chat turn is no run either
    try:
        assert (await tool.handler({"at": iso(2026, 11, 7, 10, 0), **ASK})).startswith("error: only a scheduled run")
    finally:
        current_turn.reset(tok)
    assert s.get(j["id"])["retry_at"] is None


async def test_the_time_and_the_count_are_checked_and_a_good_ask_is_noted():
    _, clock, s = make()                                  # Saturday 2026-11-07 08:00 (Seoul)
    daily = s.create("매일", "check", cron="0 9 * * *")   # next regular run: 09:00
    weekly = s.create("주간", "check", cron="0 9 * * 1")  # next regular run: Monday 09:00
    tool = {t.name: t for t in schedule_tools(s)}["schedule_retry"]

    async def ask(sid, at, why="site maintenance"):
        with as_run(sid):
            return await tool.handler({"at": at, "why": why})

    assert "too soon" in await ask(daily["id"], iso(2026, 11, 7, 8, 3))
    assert "too far" in await ask(weekly["id"], iso(2026, 11, 8, 8, 30))                  # more than 24 hours away
    assert "next regular run" in await ask(daily["id"], iso(2026, 11, 7, 9, 30))          # after the 09:00 run
    assert "next regular run" in await ask(daily["id"], iso(2026, 11, 7, 9, 0))           # not strictly before it either
    assert "`at` must be" in await ask(daily["id"], "soon")
    assert "say why" in await ask(daily["id"], iso(2026, 11, 7, 8, 30), " ")
    assert "too long" in await ask(daily["id"], iso(2026, 11, 7, 8, 30), "x" * 161)
    assert s.get(daily["id"])["retry_at"] is None and s.get(weekly["id"])["retry_at"] is None   # nothing was noted
    out = await ask(daily["id"], iso(2026, 11, 7, 8, 30))
    assert out.startswith("Will try again at 2026-11-07 08:30. End this run now: reply exactly NO_REPLY")
    got = s.get(daily["id"])
    assert got["retry_at"] == ts(2026, 11, 7, 8, 30) and got["retry_why"] == "site maintenance"
    assert got["next_run"] == "2026-11-07T09:00+09:00"                                  # the regular time stays
    # at most MAX_RETRIES since the last regular run: the weekly job has room all day Sunday morning
    for k in range(MAX_RETRIES):
        assert (await ask(weekly["id"], iso(2026, 11, 8, 7, 0 + k * 5))).startswith("Will try again")
    assert "already tried again 3 times" in await ask(weekly["id"], iso(2026, 11, 8, 7, 30))
    # a one-time job has no next regular run to stay before
    once = s.create("한 번", "check", run_at=iso(2026, 11, 7, 8, 10))
    s.advance(s._row(once["id"]), clock.t)
    assert (await ask(once["id"], iso(2026, 11, 7, 20, 0))).startswith("Will try again")


# ---------------------------------------------------------------------------------------------------------- the run that asked
async def test_the_run_that_asked_is_skipped_not_failed_and_the_retry_starts_with_a_note():
    _, clock, s = make(ts(2026, 11, 8, 8, 0))
    j = s.create("재고", "check stock", cron="0 9 * * *")
    tool = {t.name: t for t in schedule_tools(s)}["schedule_retry"]
    seen, plan = [], iter(["ask", "fine"])
    tasks = iter(range(100, 200))

    async def run(sched):
        seen.append(sched.get("retry_note"))
        tid = next(tasks)
        if next(plan) == "ask":
            with as_run(sched["id"], tid):
                await tool.handler({"at": iso(2026, 11, 8, 9, 30), "why": "점검 중이에요"})
            return {"task_id": tid, "status": "done", "result": "NO_REPLY?", "quiet": False}
        return {"task_id": tid, "status": "done", "result": "재고 있어요"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 8, 9, 0, 5)                    # the regular 09:00 run
    assert sch.tick() == [j["id"]]
    await asyncio.sleep(0.02)
    nxt = s.get(j["id"])["next_run"]
    assert nxt == "2026-11-09T09:00+09:00"
    row = s.runs(j["id"])[0]
    assert row["status"] == "skipped" and row["result"] == "Will try again at 2026-11-08 09:30: 점검 중이에요" and row["error"] is None
    got = s.get(j["id"])
    assert got["failure_count"] == 0 and got["last_run"]["status"] == "skipped" and got["retry_at"] == ts(2026, 11, 8, 9, 30)
    assert s.retried_tasks == {}                           # the mark was used up
    clock.t = ts(2026, 11, 8, 9, 29)
    assert sch.tick() == []                                # not yet
    clock.t = ts(2026, 11, 8, 9, 30, 1)
    assert sch.tick() == [j["id"]]                         # the retry
    await asyncio.sleep(0.02)
    assert seen[0] is None and seen[1] == ("[Retry 1 of this job: the run at 2026-11-08 09:00 stopped because: 점검 중이에요. Do the job "
                                           "again. In your report say that the earlier try could not be done and why.]")
    got = s.get(j["id"])
    assert got["next_run"] == nxt and got["retry_at"] is None and got["retry_why"] is None   # next_run was not advanced by the retry
    assert [r["status"] for r in s.runs(j["id"])] == ["done", "skipped"]
    assert got["last_run"]["status"] == "done" and got["failure_count"] == 0


async def test_a_retry_may_ask_again_and_the_note_names_the_first_run():
    _, clock, s = make(ts(2026, 11, 9, 8, 0))
    j = s.create("재고", "check", cron="0 9 * * 1")
    tool = {t.name: t for t in schedule_tools(s)}["schedule_retry"]
    notes, tasks = [], iter(range(100, 200))

    async def run(sched):
        tid = next(tasks)
        notes.append(sched.get("retry_note"))
        with as_run(sched["id"], tid):
            out = await tool.handler({"at": s.clock() + 3600, "why": f"down {len(notes)}"})
        assert out.startswith("Will try again")
        return {"task_id": tid, "status": "done"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 9, 9, 0, 5)
    sch.tick()
    await asyncio.sleep(0.02)
    for _ in range(2):
        clock.t += 3600 + 1
        assert sch.tick() == [j["id"]]
        await asyncio.sleep(0.02)
    assert notes[1].startswith("[Retry 1 of this job: the run at 2026-11-09 09:00 stopped because: down 1.")
    assert notes[2].startswith("[Retry 2 of this job: the run at 2026-11-09 09:00 stopped because: down 2.")   # the first run's time
    assert [r["status"] for r in s.runs(j["id"])] == ["skipped"] * 3 and s.get(j["id"])["failure_count"] == 0


async def test_a_busy_schedule_moves_its_retry_five_minutes_later():
    _, clock, s = make()
    j = s.create("느림", "slow", cron="0 9 * * 1")
    gate = asyncio.Event()

    async def run(sched):
        await gate.wait()
        return {"task_id": 1, "status": "done"}

    sch = Scheduler(s, run, clock=clock)
    s.set_retry(j["id"], ts(2026, 11, 7, 9, 0), "down")
    clock.t = ts(2026, 11, 7, 8, 0)
    assert sch.start_run(s.get(j["id"]))                    # still running ...
    s.set_retry(j["id"], ts(2026, 11, 7, 8, 30), "down")
    clock.t = ts(2026, 11, 7, 8, 30, 10)
    assert sch.tick() == []                                  # ... when its retry comes: moved, not skipped
    assert s._row(j["id"])["retry_at"] == clock.t + 300
    clock.t += 301
    gate.set()
    await asyncio.sleep(0.02)
    assert sch.tick() == [j["id"]]


async def test_a_regular_run_drops_a_pending_retry_and_the_count_starts_again():
    _, clock, s = make()
    j = s.create("매일", "check", cron="0 9 * * *")
    s.set_retry(j["id"], ts(2026, 11, 7, 8, 40), "down")
    assert s._row(j["id"])["retries"] == 1
    started = []

    async def run(sched):
        started.append(sched.get("retry_note"))
        return {"task_id": 5, "status": "done"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 9, 0, 5)                       # its own time comes first (the server was off before the retry)
    assert sch.tick() == [j["id"]]
    await asyncio.sleep(0.02)
    row = s._row(j["id"])
    assert started == [None] and row["retry_at"] is None and row["retries"] == 0 and row["retry_first_at"] is None
    assert s.get(j["id"])["retry_why"] is None
    # a run the user starts by hand is a regular run too
    s.set_retry(j["id"], clock.t + 600, "down again")
    assert sch.start_run(s.get(j["id"]))
    await asyncio.sleep(0.02)
    assert s._row(j["id"])["retry_at"] is None and s._row(j["id"])["retries"] == 0


async def test_a_retry_that_fails_without_asking_again_counts_as_a_failure():
    _, clock, s = make()
    j = s.create("매일", "check", cron="0 9 * * *")
    s.set_retry(j["id"], ts(2026, 11, 7, 8, 30), "down")

    async def run(sched):
        return {"task_id": 9, "status": "failed", "error": "still down"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 8, 31)
    assert sch.tick() == [j["id"]]
    await asyncio.sleep(0.02)
    got = s.get(j["id"])
    assert got["failure_count"] == 1 and s.runs(j["id"])[0]["status"] == "failed" and got["retry_at"] is None
    clock.t += 3600
    assert sch.tick() == [j["id"]]                           # nothing is retried again by itself: this is the 09:00 run
    assert s.runs(j["id"])[0]["status"] in ("running", "failed")


def test_pausing_or_switching_off_or_deleting_clears_the_pending_retry():
    _, clock, s = make()
    a, b, c = (s.create(n, "check", cron="0 9 * * *") for n in "abc")
    for j in (a, b, c):
        s.set_retry(j["id"], ts(2026, 11, 7, 8, 30), "down")
    s.pause(a["id"], "login gone")
    s.update(b["id"], enabled=False)
    s.delete(c["id"])
    assert s._row(a["id"])["retry_at"] is None and s._row(b["id"])["retry_at"] is None and s._row(c["id"], archived=True)["retry_at"] is None
    assert s.retry_due(ts(2026, 11, 8)) == [] and s.get(a["id"])["retry_why"] is None


def test_a_retry_long_overdue_is_dropped_and_logged():
    _, clock, s = make()
    j = s.create("매일", "check", cron="0 9 * * *")
    s.set_retry(j["id"], ts(2026, 11, 7, 8, 30), "down")
    clock.t = ts(2026, 11, 7, 8, 30) + 3 * 3600 - 1
    s.update(j["id"], enabled=True)
    sch = Scheduler(s, None, clock=clock)
    clock.t = ts(2026, 11, 7, 8, 30) + 3 * 3600
    assert sch.tick() == [] and s._row(j["id"])["retry_at"] is None
    assert s.runs(j["id"])[0]["status"] == "skipped"


# ---------------------------------------------------------------------------------------------------------- permissions
def test_the_standing_permissions_of_the_schedule_apply_unchanged_in_the_retry(tmp_path):
    store, wallet, schedules, grants, weekly, daily, used = world(tmp_path)
    g = grants.add_purchase(weekly["id"], "shop.example.com", "coffee beans", 5000, "KRW")
    one = grants.add_purchase(daily["id"], "shop.example.com", "coffee beans", 5000, "KRW", max_count=1)

    def buy(sid, task, grant):
        with as_run(sid, task):
            check = grants.purchase_check("shop.example.com", 5000, "KRW", "coffee beans")
            if check.grant:
                grants.use_purchase(check.grant, "shop.example.com", 5000, "KRW")
                wallet.record("auto", None, "shop.example.com", "coffee beans", 5000, "KRW", grant_id=grant["id"])
            return check

    # the weekly run (task 3) was blocked and asked for a retry; the retry (task 4) buys on its own, once; the next regular run too
    assert schedules.set_retry(weekly["id"], schedules.clock() + 3600, "maintenance") > 0
    assert buy(weekly["id"], 4, g).grant["id"] == g["id"]                                # auto in the retry: no card
    assert buy(weekly["id"], 4, g).reason == "task_again"                                # one covered purchase per run, as ever
    assert buy(weekly["id"], 5, g).grant["id"] == g["id"]                                # the next regular run has its own count
    assert grants.spent(g["id"]) == 10000                                                # both purchases count towards the monthly cap
    # a permission for ONE purchase: the retry used it, so the next regular run asks (the grant's own rule)
    assert buy(daily["id"], 6, one).grant["id"] == one["id"]
    assert buy(daily["id"], 7, one).reason == "task_used_up"


# ---------------------------------------------------------------------------------------------------------- the whole app
async def test_over_http_the_run_that_asked_says_nothing_and_the_retry_does(tmp_path):
    at = time.time() + 3600

    def script(req, n):
        last = str(req.messages[-1].get("content"))
        if req.messages[-1].get("role") == "tool":
            return say("점검 중이라서 못 했어요")                 # not NO_REPLY: still nothing may be sent for this run
        if "Retry 1 of this job" in last:
            return say("다시 해 보니 됐어요: 재고 있음")
        return call("schedule_retry", {"at": at, "why": "점검 중"})

    fn = model(script)
    app = create_app(settings(tmp_path), tools=[], stream_fn=fn, with_schedule_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        # the regular time is 12 hours from now, so a retry in an hour is always before it (a fixed "0 9" failed when run 08:00-09:00 UTC)
        hour = time.gmtime(time.time() + 12 * 3600).tm_hour
        s = (await c.post("/api/schedules", json={"name": "재고", "prompt": "재고 확인", "cron": f"0 {hour} * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        await follow(c, run["turn_id"])
        await until(lambda: _idle(c, s["id"]))
        got = (await c.get(f"/api/schedules/{s['id']}")).json()
        assert got["retry_at"] == at and got["retry_why"] == "점검 중"
        assert (await c.get("/api/messages")).json() == [] and (await c.get("/api/notifications")).json()["items"] == []
        rows = (await c.get(f"/api/schedules/{s['id']}/runs")).json()["items"]
        assert rows[0]["status"] == "skipped" and rows[0]["result"].endswith(": 점검 중") and got["failure_count"] == 0
        assert "schedule_retry" in fn.calls[0].messages[-1]["content"]                    # the run note names the tool
        # its time comes: the same schedule runs again with the note, and this one reports
        ctx = app.state.appctx
        assert ctx.scheduler.tick(at + 1) == [s["id"]]
        await until(lambda: _idle(c, s["id"]))
        retry_call = next(r for r in fn.calls if "Retry 1 of this job" in str(r.messages))
        assert "stopped because: 점검 중" in str(retry_call.messages)
        again = (await c.get(f"/api/schedules/{s['id']}")).json()
        assert again["retry_at"] is None and again["next_run_ts"] == got["next_run_ts"]
        notes = (await c.get("/api/notifications")).json()["items"]
        assert [n["type"] for n in notes] == ["task_done"] and "재고 있음" in notes[0]["body"]
        assert (await c.get(f"/api/schedules/{s['id']}/runs")).json()["items"][0]["status"] == "done"


async def _idle(c, sid):
    return not (await c.get(f"/api/schedules/{sid}")).json()["running"]
