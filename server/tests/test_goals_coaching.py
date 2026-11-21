"""Goals kept as the owner asked (the notes file, the one-line summary, areas, smaller goals, entries that can be fixed
and removed, status with a reason, search and paging, the due line, work items kept apart from goals, and the per-turn block.
Fake models only."""

import asyncio
import json
import re
import time
from pathlib import Path

import httpx
import pytest

from assistant import i18n
from assistant.goals import GOAL_MARK, GoalBook, GoalError
from assistant.home import Home
from assistant.prompt import GOAL_RULES, goals_block
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.store import Store

from test_goals import kickoff_model, wait_for
from test_server import AUTH, Running, settings

ROOT = Path(__file__).resolve().parents[2]


def book(tmp_path, clock=None):
    store = Store()
    home = Home(tmp_path / "home")
    return store, GoalBook(store, home, clock=clock or time.time)


def ticking():
    n = [1_000_000.0]

    def clock():
        n[0] += 1.0
        return n[0]
    return clock


async def call(b, name, **args):
    tool = next(t for t in b.tools() if t.name == name)
    return await tool.handler(args)


def english(fn):
    tok = i18n.use("en")
    try:
        return fn()
    finally:
        i18n.reset(tok)


NOTES = "notes/goals/{id}/notes.md"


# ---------------------------------------------------------------------------------------------------------- 1
async def test_goal_create_saves_summary_description_area_and_seeds_notes_once(tmp_path):
    store, b = book(tmp_path)
    out = await call(b, "goal_create", title="Walk more", summary="Walking twice a week", description="Two walks a week. Keep it easy.",
                     area="wellbeing")
    assert out == 'Saved goal #1 "Walk more". Notes file: notes/goals/1/notes.md (write your setup notes there before this turn ends).'
    g = store.goal(1)
    assert (g["kind"], g["origin"], g["area"], g["summary"]) == ("goal", "agent", "wellbeing", "Walking twice a week")
    assert g["description"] == "Two walks a week. Keep it easy."
    text = b.home.read(NOTES.format(id=1))
    assert text.startswith("# Walk more\nGoal 1 · area wellbeing · saved ")
    assert "\n\n## About\nTwo walks a week. Keep it easy." in text

    b.home.write(NOTES.format(id=1), "my own notes", by="agent")          # the agent rewrites its notes
    b.update(1, "goal", title="Walk three times")                         # a rename does not touch the file
    assert b.home.read(NOTES.format(id=1)) == "my own notes"
    b.seed_notes(store.goal(1))                                           # seeded once: a second call writes nothing
    assert b.home.read(NOTES.format(id=1)) == "my own notes"


# ---------------------------------------------------------------------------------------------------------- 2
async def test_goal_get_searches_ids_titles_summaries_descriptions_including_done(tmp_path):
    store, b = book(tmp_path, ticking())
    a = b.create("Sleep better", summary="Bed by eleven", description="Regular bedtime.", area="wellbeing")
    c = b.create("Bake bread", summary="Sleep on the dough", area="hobbies")
    d = b.create("Old sleep plan", description="Bed time routine")
    b.set_status(d["id"], "goal", "done", "Kept it for a year")
    rows, total = b.search("goal", "sleep bed")
    assert total == 3
    assert [r["id"] for r in rows] == [d["id"], a["id"], c["id"]]        # more matching words first, then the newest update
    rows, total = b.search("goal", "sleep", limit=1)
    assert (len(rows), total) == (1, 3)
    rows, _ = b.search("goal", "dough")
    assert [r["id"] for r in rows] == [c["id"]]
    with pytest.raises(GoalError):
        b.search("goal", "s")
    with pytest.raises(GoalError):
        b.search("goal", "   ")
    b.create("Work thing", kind="work", summary="sleep")
    rows, total = b.search("goal", "sleep")
    assert total == 3                                                     # work items are not goals

    for i in range(104):
        b.create(f"Goal {i}")
    rows, nxt = b.page("goal")
    assert len(rows) == 100 and nxt == 100
    rows, nxt = b.page("goal", offset=100)
    assert len(rows) == 7 and nxt is None
    assert len(b.page("goal", limit=500)[0]) == 100
    assert all(r["kind"] == "goal" for r in rows)
    assert len(b.search("goal", "goal", limit=500)[0]) == 100            # search most 100


# ---------------------------------------------------------------------------------------------------------- 3
async def test_goal_update_changes_only_given_fields_and_moves_the_due_line(tmp_path):
    from assistant.goals import note_meta, note_with_meta
    assert note_meta("x\n[due:2026-11-20 progress:40]") == ("x", "2026-11-20", 40)
    assert note_meta("[progress:5]") == ("", None, 5)
    assert note_meta("plain") == ("plain", None, None)
    assert note_meta("x\n[due:2026-13-40 progress:250]") == ("x", None, 100)
    assert note_meta("[due:2026-11-20] tail") == ("[due:2026-11-20] tail", None, None)
    assert note_with_meta("x", "2026-11-20", 40) == "x\n[due:2026-11-20 progress:40]"
    assert note_with_meta("", None, 0) == "[progress:0]"
    assert note_with_meta("plain", None, None) == "plain"

    store, b = book(tmp_path)
    g = b.create("Run", note="x\n[due:2026-11-20 progress:40]")
    b.update(g["id"], "goal", due="2026-12-01")
    assert note_meta(store.goal(g["id"])["note"]) == ("x", "2026-12-01", 40)
    b.update(g["id"], "goal", due="")                                     # "" clears the due date, progress stays
    assert store.goal(g["id"])["note"] == "x\n[progress:40]"
    b.update(g["id"], "goal", title="Run more")
    assert store.goal(g["id"])["note"] == "x\n[progress:40]" and store.goal(g["id"])["title"] == "Run more"
    with pytest.raises(GoalError):
        b.update(g["id"], "work", title="x")
    w = b.create("Parcel", kind="work")
    with pytest.raises(GoalError):
        b.update(w["id"], "goal", title="x")
    with pytest.raises(GoalError):
        b.update(g["id"], "goal", area="sports")
    with pytest.raises(GoalError):
        b.update(g["id"], "goal", due="next week")
    out = b.update(g["id"], "goal", summary="Three runs a week")
    assert out["summary"] == "Three runs a week"
    assert "Three runs" in b.text(g["id"])


# ---------------------------------------------------------------------------------------------------------- 4
async def test_goal_set_status_needs_a_reason_logs_it_and_reopens_without_a_new_goal(tmp_path):
    store, b = book(tmp_path)
    g = b.create("Read more")
    with pytest.raises(GoalError):
        b.set_status(g["id"], "goal", "done", "   ")
    with pytest.raises(GoalError):
        b.set_status(g["id"], "goal", "paused", "later")
    row, changed = b.set_status(g["id"], "goal", "done", "Finished the list")
    assert changed and row["status"] == "done" and row["done_reason"] == "Finished the list"
    assert english(lambda: b.logs(g["id"])[0]["text"]) == "Marked done: Finished the list"
    row, changed = b.set_status(g["id"], "goal", "done", "again")
    assert not changed and len(b.logs(g["id"])) == 1
    row, changed = b.set_status(g["id"], "goal", "tracking", "They want to restart it")
    assert changed and row["status"] == "tracking"
    assert english(lambda: b.logs(g["id"])[0]["text"]) == "Opened again: They want to restart it"
    assert len(store.goals()) == 1


# ---------------------------------------------------------------------------------------------------------- 5
async def test_goal_log_entries_take_title_outcome_and_an_earlier_day_and_can_be_fixed_or_removed(tmp_path):
    store, b = book(tmp_path)
    g = b.create("Run")
    e = b.entry(g["id"], "Ran 5k", "Personal best", "Ran five kilometres.", happened="2026-11-10")
    row = b.logs(g["id"])[0]
    assert (row["title"], row["outcome"], row["text"], row["kind"]) == ("Ran 5k", "Personal best", "Ran five kilometres.", "note")
    assert abs(row["happened_at"] - time.mktime((2026, 11, 10, 0, 0, 0, 0, 0, -1))) < 1
    assert row["from_goal"] is None
    calls = []
    b.summarize = lambda *a: calls.append(a) or asyncio.sleep(0, result=None)
    b.fix_entry(g["id"], e["id"], text="Ran five kilometres in 25 minutes.")
    assert b.logs(g["id"])[0]["text"] == "Ran five kilometres in 25 minutes." and calls == []
    with pytest.raises(GoalError):
        b.fix_entry(g["id"], e["id"])
    with pytest.raises(GoalError):
        b.entry(g["id"], "", "", "")
    with pytest.raises(GoalError):
        b.entry(g["id"], "x", "", "y", happened="next week")
    with pytest.raises(GoalError):
        b.fix_entry(g["id"], 9999, text="x")
    b.remove_entry(g["id"], e["id"])
    assert b.logs(g["id"]) == []
    with pytest.raises(GoalError):
        b.remove_entry(g["id"], e["id"])


# ---------------------------------------------------------------------------------------------------------- 6
async def test_the_summary_follows_new_entries_and_keeps_its_line_on_keep_or_no_model(tmp_path):
    store, b = book(tmp_path)
    g = b.create("Dentist", summary="Check-up due in May")
    answers = iter(["Booked for Friday", None, "  "])
    seen = []

    async def summarize(goal, row):
        seen.append(row["text"])
        return next(answers)
    b.summarize = summarize
    b.entry(g["id"], "Booked", "", "Booked the dentist for Friday.")
    await asyncio.gather(*b.summary_tasks)
    assert store.goal(g["id"])["summary"] == "Booked for Friday"
    b.entry(g["id"], "", "", "Called back about the form.")                # None: the line is kept
    await asyncio.gather(*b.summary_tasks)
    assert store.goal(g["id"])["summary"] == "Booked for Friday"
    b.entry(g["id"], "", "", "Nothing new.")                                # blank: kept
    await asyncio.gather(*b.summary_tasks)
    assert store.goal(g["id"])["summary"] == "Booked for Friday"
    assert seen == ["Booked the dentist for Friday.", "Called back about the form.", "Nothing new."]
    b.summarize = None
    b.entry(g["id"], "", "", "No model now.")
    assert store.goal(g["id"])["summary"] == "Booked for Friday" and not b.summary_tasks


async def test_the_server_rolls_a_new_entry_into_the_summary(tmp_path):
    """Through the app: the journal model writes the one line (SUMMARY_PROMPT) and KEEP leaves the old one."""
    answer = ["NEW: Booked for Friday"]

    async def fn(provider, request, **kw):
        if request.messages[0]["content"].startswith("You keep the one-line summary"):
            yield TextDelta(answer[0])
        else:
            yield TextDelta("ok")
        yield Done("stop")
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    book_ = app.state.goal_book
    g = book_.create("Dentist", summary="Check-up due in May")
    book_.entry(g["id"], "Booked", "", "Booked the dentist for Friday.")
    await asyncio.gather(*book_.summary_tasks)
    assert book_.store.goal(g["id"])["summary"] == "Booked for Friday"
    answer[0] = "KEEP"
    book_.entry(g["id"], "", "", "Nothing new.")
    await asyncio.gather(*book_.summary_tasks)
    assert book_.store.goal(g["id"])["summary"] == "Booked for Friday"


# ---------------------------------------------------------------------------------------------------------- 7
async def test_smaller_goals_go_one_level_deep_and_their_entries_show_on_the_parent(tmp_path):
    store, b = book(tmp_path)
    parent = b.create("Fitness", area="wellbeing")
    child = b.create("Run 5k", parent_id=parent["id"], area="wellbeing")
    assert child["parent_id"] == parent["id"] and b.public(child)["children"] == 0 and b.public(parent)["children"] == 1
    with pytest.raises(GoalError, match="top-level goal"):
        b.create("Too deep", parent_id=child["id"])
    other = b.create("Reading")
    with pytest.raises(GoalError, match="cannot go under another one"):
        b.update(parent["id"], "goal", parent_id=other["id"])
    w = b.create("Parcel", kind="work")
    with pytest.raises(GoalError, match="top-level work item"):
        b.create("Other", kind="work", parent_id=parent["id"])
    with pytest.raises(GoalError, match="top-level"):
        b.update(other["id"], "goal", parent_id=w["id"])
    b.entry(child["id"], "Ran", "", "Ran two kilometres.")
    up = b.logs(parent["id"])
    assert [r["text"] for r in up] == ["Ran two kilometres."]
    assert up[0]["from_goal"] == {"id": child["id"], "title": "Run 5k"}
    assert b.logs(child["id"])[0]["from_goal"] is None
    b.update(child["id"], "goal", parent_id=0)                            # 0 takes it back to the top
    assert store.goal(child["id"])["parent_id"] is None


# ---------------------------------------------------------------------------------------------------------- 8
async def test_deleting_a_goal_removes_its_smaller_goals_and_stops_their_work(tmp_path):
    fn = kickoff_model()
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        book_ = app.state.goal_book
        parent = (await c.post("/api/goals", json={"title": "Parent", "kickoff": False})).json()
        child = book_.create("Child", parent_id=parent["id"])
        other = (await c.post("/api/goals", json={"title": "Unrelated", "kickoff": False})).json()
        r = (await c.delete(f"/api/goals/{parent['id']}")).json()
        assert r["ok"] is True and r["deleted_goals"] == [child["id"]]
        ids = [g["id"] for g in (await c.get("/api/goals")).json()]
        assert ids == [other["id"]]
        assert book_.store.goal(child["id"]) is None


# ---------------------------------------------------------------------------------------------------------- 9
async def test_work_items_stay_apart_from_goals(tmp_path):
    fn = kickoff_model()
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        b = app.state.goal_book
        g = b.create("A goal")
        w = await call(b, "work_create", title="Dentist booking", summary="Booked for Friday", description="The visit is on Friday.")
        wid = int(w.split("#", 1)[1].split(" ", 1)[0])
        assert w.startswith(f'Keeping work item #{wid} "Dentist booking".')
        assert "use the work_ tools" in await call(b, "goal_log", goal_id=wid, text="x")
        assert "use the goal_ tools" in await call(b, "work_log", goal_id=g["id"], text="x")
        listed = [x["id"] for x in (await c.get("/api/goals")).json()]
        assert listed == [g["id"]]                                        # no work rows without include
        with_work = {x["id"]: x for x in (await c.get("/api/goals?include=work")).json()}
        assert set(with_work) == {g["id"], wid} and with_work[wid]["kind"] == "work"
        assert (await c.get("/api/goals?include=x")).status_code == 422


# ---------------------------------------------------------------------------------------------------------- 10
async def test_work_close_needs_a_reason_and_an_entry_never_closes_an_item(tmp_path):
    store, b = book(tmp_path)
    w = b.create("Parcel", kind="work")
    await call(b, "work_log", goal_id=w["id"], text="Left the depot.")
    assert store.goal(w["id"])["status"] == "tracking"                    # an entry never closes the item
    assert (await call(b, "work_close", goal_id=w["id"], status="done", reason="")).startswith("Error: ")
    out = await call(b, "work_close", goal_id=w["id"], status="done", reason="Delivered to the door")
    assert out == f"Work item #{w['id']} is now done."
    assert store.goal(w["id"])["done_reason"] == "Delivered to the door"
    assert await call(b, "work_close", goal_id=w["id"], status="done", reason="again") == \
        f"Work item #{w['id']} already was done; nothing changed."
    out = await call(b, "work_close", goal_id=w["id"], status="tracking", reason="The delivery came back")
    assert out == f"Work item #{w['id']} is now tracking."
    assert store.goal(w["id"])["status"] == "tracking"
    with pytest.raises(GoalError):
        b.set_status(w["id"], "work", "done", "")


# ---------------------------------------------------------------------------------------------------------- 11
def test_the_turn_block_shows_summary_area_due_children_and_open_work():
    from assistant.goals import feature_name
    rows = [
        {"id": 1, "title": "Run", "kind": "goal", "status": "tracking", "area": "wellbeing", "summary": "Three runs a week",
         "note": "Mornings only", "due": "2026-12-01", "parent_id": None, "plan": {"done": 1, "total": 2}, "next_step": "Buy shoes",
         "next_owner": "user", "facts": [], "asking": False},
        {"id": 2, "title": "Run 5k", "kind": "goal", "status": "tracking", "area": "wellbeing", "summary": "", "note": "",
         "due": None, "parent_id": 1, "facts": [], "asking": False},
        {"id": 3, "title": "Dentist booking", "kind": "work", "status": "tracking", "area": "", "summary": "Booked for Friday",
         "note": "", "due": None, "parent_id": None, "facts": [], "asking": False},
    ]
    expected = (f'Goals the user is working on (the app calls them "{feature_name()}"; use that word with the user; read one in full with '
                "goal_get):\n"
                "- #1 Run · area wellbeing · Three runs a week · note: Mornings only · due 2026-12-01 · plan 1/2 done · next (user): Buy shoes\n"
                "  - #2 Run 5k · area wellbeing\n"
                "Work you carry for the user (close each with work_close once it is over):\n"
                "- #3 Dentist booking · Booked for Friday\n" + GOAL_RULES)
    assert goals_block(rows) == expected
    assert goals_block([{**rows[2], "status": "done"}]) == ""
    assert "waiting" not in goals_block(rows)


# ---------------------------------------------------------------------------------------------------------- the schema of the tools
def test_the_goal_tools_are_the_full_set():
    b = GoalBook(Store())
    names = {t.name for t in b.tools()}
    assert {"goal_create", "goal_update", "goal_set_status", "work_create", "work_log", "work_close", "work_list"} <= names
    assert {"goal_set_facts", "goal_plan", "goal_step_done", "goal_metric_add", "goal_link_schedule", "goal_add_document",
            "goal_suggest", "quick_replies"} <= names
    assert json.dumps([t.parameters for t in b.tools()])
    assert GOAL_MARK


# ---------------------------------------------------------------------------------------------------------- the setup guide (skill)
SKILL = ROOT / "server" / "content" / "skills" / "goal-coaching"


def guided_book(tmp_path=None):
    store, b = book(tmp_path)
    b.guides_dir = SKILL
    return store, b


# ---------------------------------------------------------------------------------------------------------- 12
def test_a_new_goal_kickoff_carries_the_setup_guide_and_its_area_guide(tmp_path):
    store, b = guided_book(tmp_path)
    g = b.create("Walk more", area="wellbeing")
    text = b.kickoff_text(g)
    assert b.guide("start.md") in text and b.guide("area-wellbeing.md") in text
    assert "{max_q}" not in text and "one question" in text and "approves it on a card" not in text
    assert "Walk more" in text and f"goal_id {g['id']}" in text
    bare = b.create("Something else")
    text2 = b.kickoff_text(bare)
    assert b.guide("start.md") in text2 and "This goal has no area yet. Pick the one it belongs to (wellbeing, people, finances, job, " \
        "hobbies, workload, other; other when none fits)" in text2
    assert "area-<area>.md" in text2 and b.guide("area-wellbeing.md") not in text2


# ---------------------------------------------------------------------------------------------------------- 13
def test_picking_a_goal_up_again_never_reruns_the_setup_guide(tmp_path):
    store, b = guided_book(tmp_path)
    g = b.create("Walk more", area="wellbeing")
    text = b.resume_text(g)
    assert "read_skill goal-coaching and its file area-wellbeing.md" in text and "Do not read start.md" in text
    assert b.guide("start.md") not in text
    bare = b.create("Other")
    text2 = b.resume_text(bare)
    assert "area-" not in text2 and "that file alone" in text2


# ---------------------------------------------------------------------------------------------------------- 14
AREA_HEADS = ["## What it can mean", "## What to note when setting it up", "## Sources, and asking first", "## Safety", "## Helping over time"]


def test_every_area_has_a_guide_with_its_parts():
    from assistant.goals import AREAS
    for name in ("SKILL.md", "start.md", *(f"area-{a}.md" for a in AREAS)):
        assert (SKILL / name).is_file(), name
    for a in AREAS:
        heads = [line for line in (SKILL / f"area-{a}.md").read_text(encoding="utf-8").splitlines() if line.startswith("## ")]
        assert heads == AREA_HEADS, a
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    line = next(x for x in skill.splitlines() if x.startswith("Areas: "))
    assert re.findall(r"(\w+) \(", line) == list(AREAS)
    assert "## Asking" in (SKILL / "start.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: Goal coaching\n")


# ---------------------------------------------------------------------------------------------------------- 15
def test_area_guides_ask_before_reading_private_sources():
    text = {a: (SKILL / f"area-{a}.md").read_text(encoding="utf-8") for a in ("workload", "job", "people", "wellbeing", "finances")}
    assert "ask before reading private calendars, mail or work documents" in text["workload"]
    assert "Ask before reading private work context" in text["job"]
    assert "Ask before reading private messages or group chats" in text["people"]
    assert "Ask before reading clinical or medical records" in text["wellbeing"]
    assert "Ask before you move money" in text["finances"]


# ---------------------------------------------------------------------------------------------------------- 16
async def test_a_check_in_for_a_goal_is_asked_in_the_chat_and_saved_after_the_yes_without_a_card(tmp_path):
    state = {"gid": None}

    async def fn(provider, request, **kw):
        last = request.messages[-1]
        text = last.get("content") if isinstance(last.get("content"), str) else ""
        if last["role"] == "user" and text.startswith(GOAL_MARK) and "just added" in text:
            state["gid"] = int(re.search(r"goal_id (\d+)", text).group(1))
            yield ToolCall("q1", "quick_replies", json.dumps({"goal_id": state["gid"], "questions": [
                {"question": "Would a weekly check-in help?", "options": ["Sundays 20:00", "Mondays 08:00"]}]}))
            yield Done("tool_calls")
        elif last["role"] == "user" and text == "Sundays 20:00":
            state["tapped"] = True
            yield ToolCall("f1", "find_tools", json.dumps({"names": ["schedule_create"]}))   # the schedule tool is loaded by name first
            yield Done("tool_calls")
        elif last["role"] == "tool" and state.get("tapped") and not state.get("created"):
            state["created"] = True
            yield ToolCall("s1", "schedule_create", json.dumps({"name": "Weekly check-in", "prompt": "Ask how the walks went this week.",
                                                                "cron": "0 20 * * 0", "goal_id": state["gid"]}))
            yield Done("tool_calls")
        elif last["role"] == "tool" and "buttons" in str(last.get("content")):
            yield TextDelta("Would a weekly check-in help?")
            yield Done("stop")
        elif last["role"] == "tool":
            yield TextDelta("It is set for Sundays at 20:00.")
            yield Done("stop")
        else:
            yield TextDelta("ok")
            yield Done("stop")
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True, with_schedule_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        g = (await c.post("/api/goals", json={"title": "Walk more"})).json()

        async def asked():
            ms = (await c.get("/api/messages")).json()
            return ms if ms and "replies" in ms[-1] else None
        await wait_for(asked)
        async with c.stream("POST", "/api/talk", json={"text": "Sundays 20:00"}) as r:
            [json.loads(line[6:]) async for line in r.aiter_lines() if line.startswith("data: ")]
        items = (await c.get("/api/schedules")).json()["items"]
        assert [s["goal_id"] for s in items] == [g["id"]]
        assert (await c.get("/api/approvals/pending")).json()["items"] == []
