"""Scheduled runs (owner 2026-11-20): a failed run is tried once more five minutes later (D1); a run that ends on a job block is tried
10 minutes later and then 1 hour after that, then waits for its next time (D19); a run never pauses, turns off or deletes its own job; a
run that was missed while the server was off runs once, for the newest missed time, and a one-time job whose time passed runs at once
(D2). Fake clock, fake runs, no model."""

import asyncio

from assistant.agent.toolbox import SCHEDULE_RUN_TOOLS
from assistant.run_blocks import Block
from assistant.schedules import Scheduler, schedule_tools
from assistant.server import SCHEDULE_NOTE
from assistant.workers import current_turn

from test_watching import make, ts


async def settle(sch, clock, at=None):
    """Lets the started runs finish (the fake run functions return at once)."""
    if at is not None:
        clock.t = at
    await asyncio.sleep(0.02)


# ------------------------------------------------------------------------------------------------ D1: a failed run is tried once more
async def test_a_failed_run_is_tried_once_more_five_minutes_later():
    _, clock, s = make(ts(2026, 11, 7, 8, 59))
    j = s.create("매일", "check", cron="0 9 * * *")
    notes, plan = [], iter(["failed", "failed"])

    async def run(sched):
        notes.append(sched.get("retry_note"))
        return {"task_id": len(notes), "status": next(plan)}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 9, 0, 5)
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    assert s._row(j["id"])["retry_at"] == ts(2026, 11, 7, 9, 5, 5)
    clock.t = ts(2026, 11, 7, 9, 5, 4)
    assert sch.tick() == []                                   # not yet
    clock.t = ts(2026, 11, 7, 9, 5, 5)
    assert sch.tick() == [j["id"]]                            # the one automatic try again
    await settle(sch, clock)
    assert notes[0] is None and "one automatic try again" in notes[1]
    row = s._row(j["id"])
    assert row["retry_at"] is None and row["retry_kind"] == "" and row["retries"] == 0   # it failed too: no further try
    clock.t = ts(2026, 11, 8, 9, 0, 5)
    assert sch.tick() == [j["id"]]                            # the next regular time runs
    await settle(sch, clock)
    assert notes[2] is None


async def test_a_failed_retry_that_works_clears_the_stretch():
    _, clock, s = make(ts(2026, 11, 7, 8, 59))
    j = s.create("매일", "check", cron="0 9 * * *")
    plan = iter(["failed", "done"])

    async def run(sched):
        return {"task_id": 1, "status": next(plan)}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 9, 0, 5)
    sch.tick()
    await settle(sch, clock)
    clock.t = ts(2026, 11, 7, 9, 5, 6)
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    row = s._row(j["id"])
    assert row["retry_kind"] == "" and row["retries"] == 0 and row["retry_first_at"] is None and row["retry_at"] is None


async def test_a_regular_time_before_the_retry_replaces_it():
    _, clock, s = make(ts(2026, 11, 7, 9, 0, 0))
    j = s.create("5분마다", "check", cron="*/5 * * * *")
    started = []

    async def run(sched):
        started.append(sched.get("retry_note"))
        return {"task_id": 1, "status": "failed"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 9, 5, 1)
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    assert s._row(j["id"])["retry_at"] == ts(2026, 11, 7, 9, 10, 1)
    clock.t = ts(2026, 11, 7, 9, 10, 2)                        # the regular 09:10 run and the retry are both due
    assert sch.tick() == [j["id"]]                             # one run, not two
    await settle(sch, clock)
    assert len(started) == 2 and started[1] is None                          # the 09:10 run failed too: its own try is planned
    assert s._row(j["id"])["retry_kind"] == "failed" and s._row(j["id"])["retry_at"] == ts(2026, 11, 7, 9, 15, 2)


# ------------------------------------------------------------------------------------ D19: a blocked job is tried again on a schedule
async def test_a_blocked_job_is_tried_ten_minutes_then_one_hour_later_then_waits():
    _, clock, s = make(ts(2026, 11, 7, 8, 59))
    j = s.create("매일", "check", cron="0 9 * * *")
    notes, plan = [], iter(["blocked", "blocked", "blocked"])

    async def run(sched):
        notes.append(sched.get("retry_note"))
        s.set_block(j["id"], Block("connect_missing", "gmail"))   # the closing phase reported the block (finish_closed)
        return {"task_id": len(notes), "status": next(plan)}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 9, 0, 5)
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    assert s._row(j["id"])["retry_at"] == ts(2026, 11, 7, 9, 10, 5)
    clock.t = ts(2026, 11, 7, 9, 10, 5)
    assert sch.tick() == [j["id"]]                             # 10 minutes later
    await settle(sch, clock)
    end = clock.t
    assert s._row(j["id"])["retry_at"] == end + 3600           # then 1 hour after that
    clock.t = end + 3600
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    assert s._row(j["id"])["retry_at"] is None                 # no more
    assert "could not be done: connect_missing on gmail" in notes[1]
    clock.t = ts(2026, 11, 8, 9, 0, 5)
    assert sch.tick() == [j["id"]]                             # the next regular time
    await settle(sch, clock)
    assert len(notes) == 4 and notes[3] is None


async def test_a_reconnect_still_releases_a_blocked_job_at_once():
    _, clock, s = make(ts(2026, 11, 7, 8, 59))
    j = s.create("매일", "check", cron="0 9 * * *")

    async def run(sched):
        s.set_block(j["id"], Block("connect_missing", "gmail"))
        return {"task_id": 1, "status": "blocked"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 9, 0, 5)
    sch.tick()
    await settle(sch, clock)
    assert s._row(j["id"])["retry_at"] == ts(2026, 11, 7, 9, 10, 5)
    clock.t = ts(2026, 11, 7, 9, 2, 0)
    s.release_blocks({"gmail"})
    assert s._row(j["id"])["block_reason"] == "" and s._row(j["id"])["retry_at"] == clock.t
    assert sch.tick() == [j["id"]]                             # at the next tick, not at 09:10


# ------------------------------------------------------------------------------------------- a run cannot pause its own job
async def test_a_run_cannot_pause_turn_off_or_delete_its_own_job():
    _, clock, s = make()
    j = s.create("매일", "check", cron="0 9 * * *")
    own = j["id"]
    tools = {t.name: t for t in schedule_tools(s)}
    tok = current_turn.set({"source": f"schedule:{own}", "task_id": 1})
    try:
        assert "cannot pause" in tools["schedule_pause"].validate({"id": own, "reason": "login gone"})
        assert "cannot pause" in tools["schedule_pause"].validate({"reason": "login gone"})
        assert "cannot turn its own job off" in tools["schedule_update"].validate({"id": own, "enabled": False})
        assert "cannot delete its own job" in tools["schedule_delete"].validate({"id": own})
        assert tools["schedule_update"].validate({"id": own, "enabled": True}) is None
    finally:
        current_turn.reset(tok)
    assert s.get(own)["enabled"] is True and s.get(own)["paused"] is False


def test_outside_a_run_pausing_needs_an_id_and_asks_the_user():
    _, clock, s = make()
    j = s.create("매일", "check", cron="0 9 * * *")
    pause = {t.name: t for t in schedule_tools(s)}["schedule_pause"]
    assert pause.validate({"reason": "login gone"}) is not None          # no id: nothing to pause
    assert pause.validate({"id": j["id"], "reason": "login gone"}) is None
    assert pause.requires_approval is True


def test_a_scheduled_run_loads_no_pause_or_retry_tool():
    _, clock, s = make()
    assert SCHEDULE_RUN_TOOLS == ()
    names = [t.name for t in schedule_tools(s)]
    assert "schedule_retry" not in names
    assert "schedule_pause" in names                                       # a chat can still pause a job (with the user's yes)
    assert "schedule_pause" not in SCHEDULE_NOTE and "schedule_retry" not in SCHEDULE_NOTE
    assert "the user decides" in SCHEDULE_NOTE


# -------------------------------------------------------------------------------------------- D2: a missed time runs once
async def test_a_one_time_job_whose_time_passed_runs_at_once_however_late():
    _, clock, s = make(ts(2026, 11, 7, 8, 0))
    j = s.create("한 번", "check", run_at="2026-11-07T08:10")
    started = []

    async def run(sched):
        started.append(sched["id"])
        return {"task_id": 1, "status": "done"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 10, 8, 0)                           # the server was off for three days
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    assert started == [j["id"]] and s.get(j["id"])["enabled"] is False
    clock.t += 3600
    assert sch.tick() == []                                    # spent: it does not run again


async def test_a_repeating_job_runs_once_for_the_newest_missed_time():
    _, clock, s = make(ts(2026, 11, 7, 8, 0))
    j = s.create("매일 아침", "check", cron="0 9 * * *")
    started = []

    async def run(sched):
        started.append(sched["id"])
        return {"task_id": 1, "status": "done"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 10, 8, 0)                           # off for three days: the 11-07, 11-08 and 11-09 times were missed
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    assert started == [j["id"]]
    assert s._row(j["id"])["next_run"] == ts(2026, 11, 10, 9, 0)
    assert [r["status"] for r in s.runs(j["id"])] == ["done"]  # no "skipped" row for the missed days


async def test_a_retry_whose_time_passed_while_the_server_was_off_runs_at_once():
    _, clock, s = make(ts(2026, 11, 7, 8, 59))
    j = s.create("매일", "check", cron="0 9 * * *")

    async def run(sched):
        return {"task_id": 1, "status": "failed"}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 9, 0, 5)
    sch.tick()
    await settle(sch, clock)
    retry_at = s._row(j["id"])["retry_at"]
    clock.t = retry_at + 5 * 3600                              # the server was off for five hours
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    assert s._row(j["id"])["retry_at"] is None


# ------------------------------------------------------------------------------------------------ a site's own sign-in stop (owner 2026-11-21: the reference's way)
async def test_a_site_sign_in_stop_gets_no_timed_try_and_runs_at_its_next_time():
    _, clock, s = make(ts(2026, 11, 7, 8, 59))
    j = s.create("매일", "check", cron="0 9 * * *")
    plan = iter(["blocked", "done"])

    async def run(sched):
        return {"task_id": 1, "status": next(plan)}

    sch = Scheduler(s, run, clock=clock)
    clock.t = ts(2026, 11, 7, 9, 0, 5)
    assert sch.tick() == [j["id"]]
    await settle(sch, clock)
    assert s._row(j["id"])["retry_at"] is None                 # no 10-minute try, no 1-hour try
    clock.t = ts(2026, 11, 7, 10, 5, 5)
    assert sch.tick() == []                                    # nothing waits for the owner
    clock.t = ts(2026, 11, 8, 9, 0, 5)
    assert sch.tick() == [j["id"]]                             # the next scheduled time
    await settle(sch, clock)
