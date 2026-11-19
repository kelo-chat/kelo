"""The app's "What Kelo looked at" API (situation.py): the pass list and the pass detail carry counts, sources and item events, never the
pack, the prepared text, the evidence, the handoff or an error text. Red first on main: these routes did not have these fields."""

from __future__ import annotations

import asyncio
import json

from situation_fake import ts
from test_situation import rig

from assistant.situation import situation_routes

SECRET_MARK = "PREPARED-SECRET-MARK-7731"


def _client(r):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    situation_routes(app, r.sit, lambda: None)
    return TestClient(app)


async def _one_pass_with_a_proposal(r) -> None:
    r.say("user", "회의 자료 좀 정리해줘", r.clock.t - 600)

    async def research(tools, user):
        return await tools["propose_help"].handler({
            "key": "k-api", "title": "Prepare the meeting notes",
            "prepared": f"Why it matters: the meeting is soon. {SECRET_MARK}. Checked: the chat.",
            "evidence": ["source:1"], "suggested_action": "Offer to draft the notes"})
    r.agent.on("situation", research)
    r.agent.on("situation_select", lambda tools, user: "")
    await r.p.run()


def test_the_pass_list_has_counts_and_sources_and_never_the_prepared_text(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    asyncio.run(_one_pass_with_a_proposal(r))
    body = _client(r).get("/api/proactive/research").json()
    assert body["keep_days"] == 90 and "looked_at" in body and body["passes"]
    first = body["passes"][0]
    assert first["counts"]["proposed"] == 1 and isinstance(first["sources"], list)
    assert "note" not in first
    dumped = json.dumps(body, ensure_ascii=False)
    assert SECRET_MARK not in dumped and "Checked: the chat" not in dumped and '"evidence"' not in dumped


def test_the_pass_detail_lists_what_happened_to_each_item_with_no_content(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    asyncio.run(_one_pass_with_a_proposal(r))
    pass_id = _client(r).get("/api/proactive/research").json()["passes"][0]["id"]
    got = _client(r).get(f"/api/proactive/research/passes/{pass_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["pass"]["id"] == pass_id and "sources" in body and "sources" not in body["pass"]
    statuses = [e["status"] for e in body["events"]]
    assert "proposed" in statuses
    event = next(e for e in body["events"] if e["status"] == "proposed")
    assert event["title"] == "Prepare the meeting notes" and event["item_id"] and event["at"]
    dumped = json.dumps(body, ensure_ascii=False)
    assert SECRET_MARK not in dumped and '"prepared"' not in dumped and '"handoff"' not in dumped
    assert _client(r).get("/api/proactive/research/passes/999999").status_code == 404


def test_a_dropped_item_carries_the_reason_as_its_event(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))

    async def research(tools, user):
        return await tools["propose_help"].handler({
            "key": "k-drop", "title": "A nudge", "prepared": "Something small.", "evidence": ["source:1"]})

    async def select(tools, user):
        return await tools["drop_help"].handler({"item": "k-drop", "why": "not worth a message today"})
    r.say("user", "hello", r.clock.t - 600)
    r.agent.on("situation", research)
    r.agent.on("situation_select", select)
    asyncio.run(r.p.run())
    pass_id = _client(r).get("/api/proactive/research").json()["passes"][0]["id"]
    events = _client(r).get(f"/api/proactive/research/passes/{pass_id}").json()["events"]
    dropped = [e for e in events if e["status"] == "dropped"]
    assert dropped and dropped[0]["why"] == "not worth a message today" and dropped[0]["title"] == "A nudge"


def test_the_pass_list_pages_from_the_newest_with_before(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    now = r.clock.t
    for i in range(25):
        r.sit.store._run("INSERT INTO situation_passes(at, status, sources_json) VALUES(?,?,?)", (now + i, "nothing", "[]"))
    client = _client(r)
    first = client.get("/api/proactive/research?limit=10").json()
    second = client.get(f"/api/proactive/research?limit=10&before={first['next_before']}").json()
    third = client.get(f"/api/proactive/research?limit=10&before={second['next_before']}").json()
    ids = [p["id"] for p in first["passes"] + second["passes"] + third["passes"]]
    assert len(first["passes"]) == 10 and len(third["passes"]) == 5 and third["next_before"] is None
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 25


def test_a_pass_still_going_shows_as_running_and_a_stopped_one_is_closed(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    row = r.sit.store._all("SELECT COUNT(*) AS n FROM situation_passes WHERE status = 'running'")[0]["n"]
    assert row == 0
    st = r.sit._start_row(r.clock.t)
    assert r.sit.store._all("SELECT status FROM situation_passes WHERE id = ?", (st,))[0]["status"] == "running"
    r.sit.store._run("UPDATE situation_passes SET status = 'running' WHERE id = ?", (st,))
    # a new SituationResearch on the same store closes the rows a stopped server left behind
    from assistant.situation import SituationResearch
    SituationResearch(r.p, run_turn=r.agent.run_turn, tools=lambda: [])
    assert r.sit.store._all("SELECT status FROM situation_passes WHERE id = ?", (st,))[0]["status"] == "failed"


def test_the_server_gives_the_look_the_briefings_time(tmp_path):
    from datetime import date

    from assistant.providers import Done
    from assistant.server import Settings, create_app

    async def fn(*a, **kw):
        yield Done("stop")

    app = create_app(Settings(token="t" * 40, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home")), stream_fn=fn)
    d = date(2026, 11, 20)
    assert app.state.situation.briefing_at(d) == app.state.briefing.due_on(d)


def test_the_page_gets_when_each_thing_is_looked_at(tmp_path):
    """GET /api/proactive/research "looks" (2026-11-19 schedule): every hour new mail and the connected reads; at each send time the
    rest of the day; at the briefing its own sections; the rest only when needed. `looked_at` stays as released apps read it."""
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    rows = [
        {"id": "conversation", "name": "", "kind": "section", "hourly": True},
        {"id": "mail", "name": "", "kind": "section", "hourly": True},
        {"id": "home", "name": "Home", "kind": "connector", "hourly": True},
        {"id": "notes", "name": "Notes", "kind": "connector", "hourly": False},
        {"id": "phone:photos", "name": "Phone · Photos", "kind": "phone", "hourly": True},
    ]
    r.sit.looked_at = lambda: [dict(x) for x in rows]
    r.sit.corrections = object()                                           # the correction check is wired
    r.sit.briefing_at = lambda d: ts(d.year, d.month, d.day, 7, 0)
    r.sit.briefing_covers = lambda: ["weather", "calendar", "missed", "corrections"]
    body = _client(r).get("/api/proactive/research").json()
    assert body["looked_at"] == rows                                       # released apps read it as before
    assert [g["when"] for g in body["looks"]] == ["hourly", "send", "briefing", "needed"]
    looks = {g["when"]: g for g in body["looks"]}
    assert looks["hourly"] == {"when": "hourly", "times": [], "covers": [
        {"id": "mail", "name": "", "kind": "section"}, {"id": "home", "name": "Home", "kind": "connector"},
        {"id": "phone:photos", "name": "Phone · Photos", "kind": "phone"}]}
    assert [c["id"] for c in looks["send"]["covers"]] == ["conversation", "calendar", "goals", "schedules", "memory", "corrections", "held"]
    assert looks["send"]["times"] == ["10:45", "14:30", "18:15"]           # 07:00-22:00 in four parts, the first is the briefing's
    assert looks["briefing"] == {"when": "briefing", "times": ["07:00"], "covers": [
        {"id": c, "name": "", "kind": "section"} for c in ("weather", "calendar", "missed", "corrections")]}
    assert looks["needed"] == {"when": "needed", "times": [], "covers": [{"id": "notes", "name": "Notes", "kind": "connector"}]}


def test_without_mail_a_briefing_or_reads_those_groups_are_left_out(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    r.p.update(mail_check=False)
    body = _client(r).get("/api/proactive/research").json()
    assert [g["when"] for g in body["looks"]] == ["send"]                  # no mail, nothing connected, no briefing
    assert body["looks"][0]["times"] == ["07:00", "12:00", "17:00"]
    assert "corrections" not in [c["id"] for c in body["looks"][0]["covers"]]   # no correction check wired
    r.p.update(research=False)
    assert _client(r).get("/api/proactive/research").json()["looks"] == []


def test_the_server_gives_the_look_what_the_briefing_reads(tmp_path):
    from assistant.providers import Done
    from assistant.server import Settings, create_app

    async def fn(*a, **kw):
        yield Done("stop")

    app = create_app(Settings(token="t" * 40, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home")), stream_fn=fn)
    got = app.state.situation.briefing_covers()
    assert got == app.state.briefing.covers()
    assert [c for c in got if c != "corrections"] == ["weather", "calendar", "missed"]
