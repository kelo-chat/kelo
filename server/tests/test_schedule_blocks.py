"""A scheduled run that is blocked (a connected service missing or needing a reconnect, a site that needs the owner's own sign-in or
check, a device that was offline) ends "blocked" with the typed reason and a stable id (run_blocks.py). The job keeps running; its next
try is its next regular time; a reconnect of that service releases it; a success or an edit clears the block; a failed run keeps it.
Fake clock, fake runs, fake models only."""

import asyncio
import json
import time
from datetime import datetime

import httpx
import pytest

from assistant import run_blocks
from assistant.agent import Tool
from assistant.server import create_app
from assistant.run_blocks import Block
from assistant.schedules import Schedules
from assistant.scheduled_runs import Closer, Decision, RunEvidence, parse_decision
from assistant.schedules import Scheduler

from test_scheduled_runs import _idle, closing
from test_server import AUTH, Running, follow
from test_watching import call, make, model, say, settings, ts, until


# ------------------------------------------------------------------------------------------------------------ the typed pieces
def test_a_block_id_is_1_to_160_characters_and_the_reason_is_closed():
    assert Block.parse({"reason": "connect_missing", "on": "gmail"}) == Block("connect_missing", "gmail")
    assert Block.parse({"reason": "owner_step", "on": "site:example.com"}) == Block("owner_step", "site:example.com")
    assert Block.parse({"reason": "device_away", "on": "device:abc"}) == Block("device_away", "device:abc")
    assert Block.parse({"reason": "connect_missing", "on": ""}) is None
    assert Block.parse({"reason": "connect_missing", "on": "g" * 161}) is None
    assert Block.parse({"reason": "connect_missing", "on": "g" * 160}) is not None
    assert Block.parse({"reason": "something_else", "on": "gmail"}) is None
    assert Block.parse({"reason": "connect_missing", "on": "Gmail Prose"}) is None
    assert Block.parse("gmail") is None


def test_a_tool_that_hit_a_blocked_condition_notes_it_in_the_running_turn(monkeypatch):
    from assistant.workers import current_turn
    evidence = []
    tok = current_turn.set({"source": "schedule:1", "blocks": evidence, "unattended": True})
    try:
        run_blocks.note("connect_expired", "gmail")
        run_blocks.note("connect_expired", "gmail")               # a duplicate is kept once
        run_blocks.note("nope", "gmail")                           # not a typed reason: nothing
    finally:
        current_turn.reset(tok)
    assert evidence == [Block("connect_expired", "gmail")]
    run_blocks.note("connect_missing", "gmail")                    # outside a turn: nothing happens, no error


def test_a_blocked_outcome_needs_a_blocker_the_tools_reported():
    ev = RunEvidence()
    ev.blocks.append(Block("connect_missing", "gmail"))
    ok = parse_decision({"outcome": "blocked", "reason": "connect_missing", "on": "gmail", "text": "Gmail is not connected."})
    assert ok == Decision(True, "Gmail is not connected.", block=Block("connect_missing", "gmail"))
    assert parse_decision({"outcome": "blocked", "reason": "connect_missing", "on": "gmail", "text": " "}) is None
    assert parse_decision({"outcome": "blocked", "reason": "bogus", "on": "gmail", "text": "x"}) is None


async def test_the_closer_keeps_the_text_and_drops_a_block_the_tools_did_not_report():
    async def ask(system, user):
        return {"outcome": "blocked", "reason": "connect_expired", "on": "notion", "text": "Notion needs a reconnect."}
    ev = RunEvidence()
    ev.blocks.append(Block("connect_missing", "gmail"))
    d = await Closer(ask).close("job", "check", "report", ev)
    assert d.text == "Notion needs a reconnect." and d.block is None and d.tell


# ------------------------------------------------------------------------------------------------------------- the job's side
def test_a_run_ended_blocked_keeps_the_job_on_and_records_why():
    store, clock, s = make()
    j = s.create("메일", "check mail", cron="0 9 * * *")
    s.record_run(j["id"], 11, "failed", "x")                       # one failure counted before
    assert s._row(j["id"])["failure_count"] == 1
    s.set_block(j["id"], Block("connect_missing", "gmail"))
    row = s._row(j["id"])
    assert row["enabled"] == 1 and row["block_reason"] == "connect_missing" and row["block_on"] == "gmail"
    assert row["block_since"] is not None and row["block_try_at"] == row["next_run"]
    s.record_run(j["id"], 12, "blocked", "")
    row = s._row(j["id"])   # owner 2026-11-20: a blocked run counts in the streak too
    assert row["enabled"] == 1 and row["failure_count"] == 2 and row["last_status"] == "blocked"


def test_the_same_block_again_is_reported_as_the_same():
    store, clock, s = make()
    j = s.create("메일", "check mail", cron="0 9 * * *")
    assert s.set_block(j["id"], Block("connect_missing", "gmail")) is False
    assert s.set_block(j["id"], Block("connect_missing", "gmail")) is True
    assert s.set_block(j["id"], Block("connect_expired", "gmail")) is False


async def test_reconnecting_releases_only_the_jobs_blocked_on_that_connector():
    store, clock, s = make()
    a = s.create("메일", "a", cron="0 9 * * *")
    b = s.create("메일2", "b", cron="0 10 * * *")
    c = s.create("노션", "c", cron="0 11 * * *")
    d = s.create("사이트", "d", cron="0 12 * * *")
    s.set_block(a["id"], Block("connect_expired", "gmail"))
    s.set_block(b["id"], Block("site_check", "gmail"))            # another reason on the same id: not released
    s.set_block(c["id"], Block("connect_expired", "notion"))
    s.set_block(d["id"], Block("owner_step", "site:x.com"))
    assert s.release_blocks({"gmail"}) == [a["id"]]
    assert s._row(a["id"])["block_reason"] == "" and s._row(b["id"])["block_reason"] == "site_check"
    assert s._row(c["id"])["block_reason"] == "connect_expired" and s._row(d["id"])["block_reason"] == "owner_step"
    assert s.release_blocks(set()) == []


async def test_a_released_job_that_missed_its_time_runs_at_the_next_tick():
    store, clock, s = make()
    j = s.create("메일", "check", cron="0 9 * * *")
    clock.t = ts(2026, 11, 7, 9, 0, 5)                               # its regular time passed while blocked
    s.set_block(j["id"], Block("connect_expired", "gmail"))
    s.store._run("UPDATE schedules SET block_try_at = ? WHERE id = ?", (ts(2026, 11, 8, 9, 0), j["id"]))   # next try is tomorrow
    assert s.due(clock.t) == []                                      # held by the try time
    s.release_blocks({"gmail"})
    started = []

    async def run(sched):
        started.append(sched["id"])
        return {"task_id": 5, "status": "done"}

    sch = Scheduler(s, run, clock=clock)
    assert sch.tick() == [j["id"]]
    await asyncio.sleep(0.02)
    assert started == [j["id"]] and s._row(j["id"])["failure_count"] == 0


async def test_a_success_or_an_edit_clears_the_block_and_a_failed_run_keeps_it():
    store, clock, s = make()
    j = s.create("메일", "check", cron="0 9 * * *")
    s.set_block(j["id"], Block("connect_expired", "gmail"))
    s.record_run(j["id"], 1, "failed", "boom")
    assert s._row(j["id"])["block_reason"] == "connect_expired"
    s.record_run(j["id"], 2, "done", "")
    assert s._row(j["id"])["block_reason"] == ""
    s.set_block(j["id"], Block("connect_expired", "gmail"))
    s.update(j["id"], prompt="check again")
    assert s._row(j["id"])["block_reason"] == ""


def test_a_site_sign_in_stop_is_not_a_job_block():
    store, clock, s = make()
    j = s.create("사이트", "check", cron="0 9 * * *")
    before = s._row(j["id"])["next_run"]
    assert run_blocks.Block("owner_step", "site:example.com").reason == "owner_step"   # the closing phase drops it (scheduled.finish)
    assert s._row(j["id"])["block_reason"] == "" and s._row(j["id"])["next_run"] == before


# ------------------------------------------------------------------------------------------------------------ the whole app

def service_tool(name: str, reason: str, on: str):
    """A tool whose call hits a typed blocked condition (what the real tools do through run_blocks.note)."""
    async def run(args):
        run_blocks.note(reason, on)
        return "not available"
    return Tool(name, "use the service", {"type": "object", "properties": {}}, run)


async def job_runs(tmp_path, script, tools=(), runs=1, prompt="check the service"):
    fn = model(script)
    app = create_app(settings(tmp_path), tools=list(tools), stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "서비스", "prompt": prompt, "cron": "0 3 * * *"})).json()
        for _ in range(runs):
            run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
            await follow(c, run["turn_id"])
            await until(lambda: _idle(c, s["id"]))
        out = {"messages": (await c.get("/api/messages")).json(),
               "notes": (await c.get("/api/notifications")).json()["items"],
               "sched": (await c.get(f"/api/schedules/{s['id']}")).json()}
    return app, fn, out


async def test_a_rejected_key_in_a_run_is_noted_as_a_blocker_with_the_connector_id(tmp_path):
    holder, seen = {}, []

    async def use(args):
        store = holder["app"].state.store
        store.set_credential_secret("slack-api", "xoxp-test-value")
        store.set_credential_result("slack-api", "rejected")
        return "the service refused the key"
    tools = [Tool("use_slack", "use slack", {"type": "object", "properties": {}}, use)]

    def script(req, n):
        if closing(req):
            seen.append(json.loads(req.messages[1]["content"])["evidence"]["blockers"])
            return say(json.dumps({"outcome": "blocked", "reason": "connect_expired", "on": "slack",
                                   "text": "Slack needs to be connected again."}))
        if req.messages[-1].get("role") == "tool":
            return say("The service refused the key.")
        return call("use_slack")
    app, fn, out = await job_runs_with(tmp_path, holder, script, tools)
    assert seen == [[{"reason": "connect_expired", "on": "slack"}]]
    assert out["sched"]["block"]["reason"] == "connect_expired" and out["sched"]["block"]["on"] == "slack"
    assert out["sched"]["enabled"] is True
    assert [m["content"].split("\n")[-1] for m in out["messages"]] == ["Slack needs to be connected again."]


async def job_runs_with(tmp_path, holder, script, tools):
    """job_runs, but the app is handed to the tools (holder["app"]) before the run starts."""
    fn = model(script)
    app = create_app(settings(tmp_path), tools=list(tools), stream_fn=fn)
    holder["app"] = app
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "서비스", "prompt": "check", "cron": "0 3 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        await follow(c, run["turn_id"])
        await until(lambda: _idle(c, s["id"]))
        out = {"messages": (await c.get("/api/messages")).json(),
               "notes": (await c.get("/api/notifications")).json()["items"],
               "sched": (await c.get(f"/api/schedules/{s['id']}")).json()}
    return app, fn, out


async def test_a_site_sign_in_stop_fails_only_that_run_and_the_job_keeps_its_time(tmp_path):
    def script(req, n):
        if closing(req):
            return say(json.dumps({"outcome": "blocked", "reason": "owner_step", "on": "site:example.com",
                                   "text": "The site asks for your own sign-in."}))
        if req.messages[-1].get("role") == "tool":
            return say("The site asked for a sign-in.")
        return call("open_site")
    tools = [service_tool("open_site", "owner_step", "site:example.com")]
    _, _, out = await job_runs(tmp_path, script, tools)
    assert out["sched"]["enabled"] is True and out["sched"]["block"] is None
    assert out["sched"]["next_run"] is not None
    assert [m["content"].split("\n")[-1] for m in out["messages"]] == ["The site asks for your own sign-in."]


async def test_a_site_sign_in_stop_is_not_released_by_a_later_login(tmp_path):
    """the reference's way (owner 2026-11-21): a site's sign-in stop waits for nothing. The owner signing in at that site later does not run the
    job early: it keeps its next scheduled time."""
    holder = {}

    def script(req, n):
        if closing(req):
            return say(json.dumps({"outcome": "blocked", "reason": "owner_step", "on": "site:example.com",
                                   "text": "The site asks for your own sign-in."}))
        if req.messages[-1].get("role") == "tool":
            return say("The site asked for a sign-in.")
        return call("open_site")
    tools = [service_tool("open_site", "owner_step", "site:example.com")]
    app, _, out = await job_runs_with(tmp_path, holder, script, tools)
    store = app.state.store
    sid = out["sched"]["id"]
    stopped = Schedules(store)._row(sid)["next_run"]
    store.save_credential("example.com", "Example", ["example.com"], {}, kind="api")
    store.set_credential_secret("example.com", "the-owner-login")
    s = Schedules(store)
    row = s._row(sid)
    assert row["next_run"] == stopped                                     # not pulled to now by the login
    when = datetime.fromtimestamp(row["next_run"], s.tz_of(row))
    assert (when.hour, when.minute) == (3, 0) and row["next_run"] > time.time()   # the job's next scheduled time (cron 0 3 * * *)


async def test_a_site_sign_in_stop_tells_the_owner_once_and_the_job_keeps_its_time(tmp_path):
    def script(req, n):
        if closing(req):
            return say(json.dumps({"outcome": "blocked", "reason": "owner_step", "on": "site:example.com",
                                   "text": "The site asks for your own sign-in."}))
        if req.messages[-1].get("role") == "tool":
            return say("The site asked for a sign-in.")
        return call("open_site")
    tools = [service_tool("open_site", "owner_step", "site:example.com")]
    app, _, out = await job_runs(tmp_path, script, tools)
    told = [m for m in out["messages"] if "The site asks for your own sign-in." in m["content"]]
    assert len(told) == 1                                                  # one message for the stop, not one per step
    assert len(out["notes"]) <= 1
    assert out["sched"]["enabled"] is True and out["sched"]["block"] is None
    s = Schedules(app.state.store)
    assert s._row(out["sched"]["id"])["retry_at"] is None                 # no timed try after a sign-in stop


async def test_the_same_block_again_is_not_told_twice(tmp_path):
    def script(req, n):
        if closing(req):
            return say(json.dumps({"outcome": "blocked", "reason": "connect_missing", "on": "gmail",
                                   "text": "Gmail is not connected."}))
        if req.messages[-1].get("role") == "tool":
            return say("Gmail is not connected.")
        return call("check_mail")
    tools = [service_tool("check_mail", "connect_missing", "gmail")]
    _, _, out = await job_runs(tmp_path, script, tools, runs=2)
    assert [m["content"].split("\n")[-1] for m in out["messages"]] == ["Gmail is not connected."]
    assert len(out["notes"]) <= 1


async def test_a_scheduled_result_survives_a_restart_during_the_closing_phase(tmp_path):
    """The working phase ended and its result was held in the run's row; the process stopped before the closing step answered. The next
    start asks the closing step again for that held result, delivers it once, and the run is not shown as stopped."""
    from assistant.activity import Activity
    from assistant.store import Store

    store = Store(str(tmp_path / "a.db"))
    s = Schedules(store)
    job = s.create("재고", "재고 확인", cron="0 3 * * *")
    tid = Activity(store).start_task("재고", f"schedule:{job['id']}")
    run_id = s.add_run(job["id"], "running")
    evidence = {"tool_calls": 1, "failed_tools": [], "skipped_steps": [], "blockers": []}
    s.hold_result(run_id, {"task_id": tid, "status": "done", "error": "", "report": "Back in stock: 2 left", "evidence": evidence,
                           "title": "재고", "note_head": ""})

    def script(req, n):
        if closing(req):
            return say(json.dumps({"outcome": "tell", "text": "재고가 2개 생겼어요."}, ensure_ascii=False))
        return say("NO_REPLY")
    _, _, out = await job_runs_on(tmp_path, script)
    texts = [m["content"].split("\n")[-1] for m in out["messages"]]
    assert texts.count("재고가 2개 생겼어요.") == 1
    row = Store(str(tmp_path / "a.db"))._all("SELECT status, held_result FROM schedule_runs WHERE id = ?", (run_id,))[0]
    assert row["status"] != "stopped" and row["held_result"] is None


async def job_runs_on(tmp_path, script):
    """Start the app on the database the test prepared (a restart) and wait until the held result is out."""
    fn = model(script)
    app = create_app(settings(tmp_path), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await until(lambda: _has_text(c, "재고가 2개 생겼어요."))
        out = {"messages": (await c.get("/api/messages")).json()}
    return app, fn, out


async def _has_text(c, text):
    msgs = (await c.get("/api/messages")).json()
    return any(m["content"].endswith(text) for m in msgs)


# ------------------------------------------------------------------ owner decision 2026-11-20 16:00: three failures ask, they do not switch off
def test_three_failures_in_a_row_ask_the_owner_and_the_job_keeps_running():
    store, clock, s = make()
    asked = []
    s.on_failures_asked = lambda job, error: asked.append((job["id"], error))
    j = s.create("점검", "check", cron="0 * * * *")
    for n in (1, 2, 3):
        assert s.record_run(j["id"], 10 + n, "failed", f"error {n}") is None
    row = s._row(j["id"])
    assert row["enabled"] == 1 and row["next_run"] is not None and row["failure_count"] == 3 and row["disabled_reason"] == ""
    assert asked == [(j["id"], "error 3")]                                    # asked once for this streak
    s.record_run(j["id"], 14, "failed", "error 4")
    assert asked == [(j["id"], "error 3")] and s._row(j["id"])["enabled"] == 1   # still running, not asked again


def test_a_blocked_run_counts_in_the_streak_and_the_owner_is_asked_at_three():
    # owner 2026-11-20: blocked runs count like failed ones; at three in a row the owner is asked, the job keeps running
    store, clock, s = make()
    asked = []
    s.on_failures_asked = lambda job, error: asked.append(job["id"])
    j = s.create("점검", "check", cron="0 * * * *")
    s.record_run(j["id"], 1, "failed", "e")
    s.record_run(j["id"], 2, "blocked", "")
    assert s._row(j["id"])["failure_count"] == 2 and asked == []
    s.record_run(j["id"], 3, "blocked", "")
    row = s._row(j["id"])
    assert row["failure_count"] == 3 and asked == [j["id"]] and row["enabled"] == 1


def test_keep_going_starts_the_count_again_and_a_success_does_too():
    store, clock, s = make()
    asked = []
    s.on_failures_asked = lambda job, error: asked.append(job["id"])
    j = s.create("점검", "check", cron="0 * * * *")
    for n in (1, 2, 3):
        s.record_run(j["id"], n, "failed", "e")
    assert asked == [j["id"]]
    s.keep_going(j["id"])
    assert s._row(j["id"])["failure_count"] == 0 and s._row(j["id"])["enabled"] == 1
    for n in (4, 5, 6):
        s.record_run(j["id"], n, "failed", "e")
    assert asked == [j["id"], j["id"]]                                        # a new streak asks again
    s.record_run(j["id"], 7, "done", "")
    assert s._row(j["id"])["failure_count"] == 0


def test_the_owner_can_stop_the_job_after_being_asked():
    store, clock, s = make()
    j = s.create("점검", "check", cron="0 * * * *")
    s.on_failures_asked = lambda job, error: None
    for n in (1, 2, 3):
        s.record_run(j["id"], n, "failed", "e")
    got = s.pause(j["id"], "the owner asked to stop it", by="user")
    assert got["enabled"] is False and got["paused"] is True


async def test_a_new_value_for_a_connector_releases_only_its_jobs_through_the_vault(tmp_path):
    """Test 4 and 6 of the brief, through the vault: saving a new value for the slack item releases the job blocked on slack only; a
    routine value for an unrelated item releases nothing."""
    app = create_app(settings(tmp_path), tools=[], stream_fn=model(lambda req, n: say("NO_REPLY")))
    store = app.state.store
    s = Schedules(store)
    a = s.create("슬랙", "a", cron="0 9 * * *")
    b = s.create("노션", "b", cron="0 10 * * *")
    s.set_block(a["id"], Block("connect_expired", "slack"))
    s.set_block(b["id"], Block("connect_expired", "notion"))
    store.save_credential("some-unrelated-item", "other", ["other.example"], {}, kind="api")
    store.save_credential("slack-api", "Slack", ["slack.com"], {}, kind="api")
    store.set_credential_secret("some-unrelated-item", "value")
    assert s._row(a["id"])["block_reason"] == "connect_expired" and s._row(b["id"])["block_reason"] == "connect_expired"
    store.set_credential_secret("slack-api", "xoxp-new-value")
    assert s._row(a["id"])["block_reason"] == "" and s._row(b["id"])["block_reason"] == "connect_expired"
