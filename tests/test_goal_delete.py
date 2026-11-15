"""Deleting a goal (DELETE /api/goals/{id}): the work started for it stops (its kickoff turn, the job that turn handed off, a waiting
approval ends as cancelled), a chat reply the user wrote keeps running, and the linked schedules go to the archive with it
(schedules=delete, a run in progress stops too; older apps send pause for the same answer) or keep running off the goal (keep, the default). Fake model, no real model calls."""

import asyncio
import json

import httpx

from assistant.agent import Tool
from assistant.goals import GOAL_MARK
from assistant.providers import Done, TextDelta, ToolCall

from test_agent_profile import app_settings, approval_tool
from test_server import AUTH, Running
from test_workers import JOB_MARK, until


def call(name, args=None, cid=None):
    return [ToolCall(cid or f"c-{name}", name, json.dumps(args or {})), Done("tool_calls")]


def model(blocked: asyncio.Event):
    """Kickoff: hand off a job, then ask to send (an approval that waits). The job and a scheduled run read a page that never loads.
    A chat message "slow" waits on `blocked`."""
    async def fn(provider, request, **kw):
        msgs = request.messages
        text = "\n".join(str(m.get("content")) for m in msgs if isinstance(m.get("content"), str))
        last = msgs[-1]
        if JOB_MARK in text:
            out = call("slow_read", cid=f"c-job{len(msgs)}")
        elif "check the morning prices" in text and last.get("role") == "user":
            out = call("slow_read", cid="c-run")
        elif last.get("role") == "user" and str(last.get("content")).startswith(GOAL_MARK):
            out = call("delegate", {"task": "look at three shops", "kind": "general"})
        elif last.get("role") == "tool" and str(last.get("content")).startswith("Handed off"):
            out = call("send", {"to": "shop"})
        elif last.get("role") == "user" and str(last.get("content")).endswith("slow"):
            await blocked.wait()
            out = [TextDelta("done slowly"), Done("stop")]
        else:
            out = [TextDelta("ok"), Done("stop")]
        for ev in out:
            yield ev
    return fn


def slow_tool():
    async def slow(args):
        await asyncio.Event().wait()
        return "never"
    return Tool("slow_read", "read a page", {"type": "object"}, slow)


async def test_deleting_a_goal_stops_its_work_resolves_its_approval_and_deletes_its_schedules(tmp_path):
    blocked = asyncio.Event()
    app = create(tmp_path, blocked)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        g = (await c.post("/api/goals", json={"title": "Goal A"})).json()
        s1 = (await c.post("/api/schedules", json={"name": "Morning prices", "prompt": "check the morning prices", "cron": "0 8 * * *",
                                                   "goal_id": g["id"]})).json()
        s2 = (await c.post("/api/schedules", json={"name": "Weekly", "prompt": "a weekly look", "cron": "0 9 * * 1",
                                                   "goal_id": g["id"]})).json()
        other = (await c.post("/api/schedules", json={"name": "Unrelated", "prompt": "something else", "cron": "0 7 * * *"})).json()

        async def asking():
            items = (await c.get("/api/approvals/pending")).json()["items"]
            return items[0] if items else None
        ask = await until(asking)
        job = await until(lambda: _running_job(c))
        run = (await c.post(f"/api/schedules/{s1['id']}/run")).json()
        assert run["turn_id"]
        # a chat reply the user wrote, running at the same time: not started for the goal, so it is left alone
        chat = (await c.post("/api/turns", json={"text": "slow"})).json()
        listed = (await c.get("/api/goals")).json()[0]
        assert listed["working"] is True and listed["schedules_on"] == 2      # the apps ask about these two before deleting

        r = await c.delete(f"/api/goals/{g['id']}", params={"schedules": "delete"})
        assert r.status_code == 200
        out = r.json()
        assert out["ok"] and out["stopped_turns"] == 2 and out["stopped_jobs"] == 1      # the kickoff + the schedule's run; the job
        assert sorted(out["deleted_schedules"]) == sorted([s1["id"], s2["id"]]) and out["kept_schedules"] == []
        assert (await c.get("/api/approvals/pending")).json()["items"] == []
        hist = (await c.get("/api/approvals/history", params={"task_id": ask["task_id"]})).json()["items"]
        assert hist[0]["decision"] == "cancelled"
        assert (await c.get(f"/api/workers/{job['id']}")).json()["status"] == "stopped"
        assert (await c.get(f"/api/turns/{run['turn_id']}", params={"wait": 0})).json()["done"] is True
        listed_ids = [s["id"] for s in (await c.get("/api/schedules")).json()["items"]]
        archived = [s["id"] for s in (await c.get("/api/schedules/archive")).json()["items"]]
        for sid in (s1["id"], s2["id"]):
            assert sid not in listed_ids and sid in archived           # out of the list, restorable from the archive
        assert (await c.get(f"/api/schedules/{other['id']}")).json()["enabled"] is True
        assert (await c.get(f"/api/turns/{chat['id']}", params={"wait": 0})).json()["done"] is False   # still running
        blocked.set()
        await until(lambda: _turn_done(c, chat["id"]))
        assert (await c.get(f"/api/goals/{g['id']}")).status_code == 404
        assert (await c.delete(f"/api/goals/{g['id']}")).status_code == 404


async def test_older_apps_pause_answer_deletes_the_schedules_too_also_the_ones_already_off(tmp_path):
    app = create(tmp_path, asyncio.Event())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        g = (await c.post("/api/goals", json={"title": "Goal C", "kickoff": False})).json()
        on = (await c.post("/api/schedules", json={"name": "Daily", "prompt": "a daily look", "cron": "0 8 * * *", "goal_id": g["id"]})).json()
        off = (await c.post("/api/schedules", json={"name": "Weekly", "prompt": "a weekly look", "cron": "0 9 * * 1", "goal_id": g["id"]})).json()
        assert (await c.patch(f"/api/schedules/{off['id']}", json={"enabled": False})).status_code == 200
        out = (await c.delete(f"/api/goals/{g['id']}", params={"schedules": "pause"})).json()
        assert sorted(out["deleted_schedules"]) == sorted([on["id"], off["id"]]) and out["kept_schedules"] == []
        assert (await c.get("/api/schedules")).json()["items"] == []


async def test_keep_is_the_default_and_a_bad_option_is_refused(tmp_path):
    app = create(tmp_path, asyncio.Event())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        g = (await c.post("/api/goals", json={"title": "Goal B", "kickoff": False})).json()
        s = (await c.post("/api/schedules", json={"name": "Daily", "prompt": "a daily look", "cron": "0 8 * * *",
                                                  "goal_id": g["id"]})).json()
        assert (await c.get("/api/goals")).json()[0]["schedules_on"] == 1
        assert (await c.delete(f"/api/goals/{g['id']}", params={"schedules": "stop"})).status_code == 422
        assert (await c.get(f"/api/goals/{g['id']}")).status_code == 200    # refused: nothing was deleted
        out = (await c.delete(f"/api/goals/{g['id']}")).json()
        assert out == {"ok": True, "stopped_turns": 0, "stopped_jobs": 0, "deleted_schedules": [], "kept_schedules": [s["id"]]}
        kept = (await c.get(f"/api/schedules/{s['id']}")).json()
        assert kept["enabled"] is True and kept["paused"] is False and kept["goal_id"] is None


def create(tmp_path, blocked):
    from assistant.server import create_app
    return create_app(app_settings(tmp_path), tools=[slow_tool(), approval_tool("send")], stream_fn=model(blocked),
                      with_goal_tools=True, with_worker_tools=True)


async def _running_job(c):
    items = (await c.get("/api/workers")).json()["items"]
    return items[0] if items and items[0]["status"] == "running" else None


async def _turn_done(c, tid):
    return (await c.get(f"/api/turns/{tid}", params={"wait": 0})).json()["done"]
