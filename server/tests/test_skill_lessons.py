"""Lessons the finished runs teach are looked for ONLY in the nightly look-back (reflection.py), never after each run
(skill_lessons.py, docs/memory-pipeline.md): the same request and answer, at most LESSONS_PER_DAY lessons a day, each kept as a skill
proposal or a note proposal (the owner approves), never a memory claim.

Checks: a night's lesson is one pending proposal; the same words twice add nothing and changed words replace the waiting one; more
than the daily cap -> only the cap; a run that read outside text marks its proposal; a lesson about a built-in skill is a note
proposal, not a new skill id; a run without tool steps is not shown to the model; the per-run hook is gone from the server; nothing a
run did is written into memory. The model is a scripted fake: no real call."""

import json
from pathlib import Path

import pytest

from assistant.activity import Activity
from assistant.custom_skills import SkillProposals, skill_title
from assistant.home import SKILL_NOTES_MAX, Home, HomeError
from assistant.packages import Registry
from assistant.providers import Done, TextDelta
from assistant.reflection import Reflection
from assistant.skill_lessons import LESSONS_PER_DAY, LessonExtractor, append_note, run_steps
from assistant.store import Store

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "server" / "content"
STEPS = ["Open the shop site and sign in again.", "Then add the item to the cart."]
NIGHT = 1_700_000_000.0


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make(tmp_path: Path):
    reg = Registry(builtin_skills=CONTENT / "skills", installed=tmp_path / "packages", custom=tmp_path / "custom_skills")
    notes: list[tuple[str, str]] = []
    clock = Clock(NIGHT)
    props = SkillProposals(reg, clock=clock, write_note=lambda sid, text: notes.append((sid, text)))
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    home = Home(tmp_path / "home")
    activity = Activity(store, clock)
    return store, home, activity, props, notes, clock


def scripted(*answers):
    it = iter(answers)
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request)
        yield TextDelta(json.dumps(next(it)))
        yield Done("stop")

    fn.seen = seen
    return fn


def reflection(store, home, activity, props, fn, clock):
    r = Reflection(store, home, activity, fn, lambda: ("p", "m"), clock=clock)
    r.lessons = LessonExtractor(store, props, clock=clock)
    return r


def finished_run(store, activity, clock, *, title="Buy the item at the shop", steps=None, outside=False, worker_id=""):
    """A finished task with tool steps (the way server.py's task_closed records them)."""
    tid = activity.start_task(title, "chat", turn_id=f"t{clock.now}")
    if steps is None:
        steps = [{"id": "1", "name": "browser_open", "ok": True}, {"id": "2", "name": "browser_click", "ok": False}]
    activity.set_steps(tid, json.dumps({"steps": [{"tool": s["name"], "ok": s["ok"]} for s in steps], "outside": outside}))
    if worker_id:
        activity.link_task(tid, None, worker_id)
    activity.finish_task(tid, "done", answer="Bought it.", seen=True)
    return tid


def waiting(props):
    """The proposals waiting for the owner, as the file keeps them (skill, kind, body, origin, outside_text)."""
    return props._read()


def owner_says(store, clock, text="buy the item at the shop"):
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,?)", ("user", text, clock.now - 60, "owner"))


def skill_lesson(run, sid="shop-sign-in-again", steps=STEPS, already_in="", description="Buy from the shop site when it logged us out."):
    return {"run": run, "kind": "skill", "id": sid, "name": skill_title(sid), "description": description,
            "when_to_use": "When the shop site asks for a sign-in during checkout.", "steps": list(steps),
            "pitfalls": ["The session ends after a few minutes."], "check": ["The cart shows the item."], "already_in": already_in}


def night(lessons):
    return {"new": [], "update": [], "drop": [], "goals": [], "reasons": ["checked"], "lessons": lessons}


# ---------------------------------------------------------------------------------------------------------------- the night
async def test_a_lesson_from_a_finished_run_is_one_pending_proposal_and_no_memory_claim(tmp_path):
    store, home, activity, props, notes, clock = make(tmp_path)
    tid = finished_run(store, activity, clock)
    owner_says(store, clock)
    fn = scripted(night([skill_lesson(tid)]))
    r = reflection(store, home, activity, props, fn, clock)
    out = await r.run()
    assert out["status"] == "done"
    pending = waiting(props)
    assert len(pending) == 1 and pending[0]["skill"] == "shop-sign-in-again" and pending[0]["kind"] == "new"
    assert pending[0]["origin"] == {"type": "chat", "task_id": tid}
    assert r.rules() == []   # no learned rule
    memory = home.root / "MEMORY.md"
    assert not memory.is_file() or "shop" not in memory.read_text(encoding="utf-8")   # no memory claim
    assert notes == []


async def test_the_same_lesson_twice_adds_no_second_proposal_and_a_changed_one_replaces_it(tmp_path):
    """Each finished run is read once; a later run that teaches the same words adds nothing, one that teaches changed words replaces
    the waiting proposal (the newer one)."""
    store, home, activity, props, _notes, clock = make(tmp_path)
    first = finished_run(store, activity, clock)
    owner_says(store, clock)
    fn = scripted(night([skill_lesson(first)]))
    r = reflection(store, home, activity, props, fn, clock)
    await r.run()
    clock.now += 3600
    second = finished_run(store, activity, clock, title="Buy the item again")
    owner_says(store, clock, "and once more")
    r.stream_fn = scripted(night([skill_lesson(second)]))   # the next night: the model answers this
    await r.run(force=True)
    assert len(waiting(props)) == 1
    clock.now += 3600
    third = finished_run(store, activity, clock, title="Buy the item a third time")
    owner_says(store, clock, "same, but sign in first")
    r.stream_fn = scripted(night([skill_lesson(third, steps=["Sign in first."])]))
    await r.run(force=True)
    pending = waiting(props)
    assert len(pending) == 1 and "Sign in first." in pending[0]["body"]


async def test_no_more_than_the_daily_cap_of_lesson_proposals(tmp_path):
    store, home, activity, props, _notes, clock = make(tmp_path)
    tids = [finished_run(store, activity, clock, title=f"Task {i}") for i in range(5)]
    owner_says(store, clock)
    lessons = [skill_lesson(t, sid=f"site-lesson-{i}") for i, t in enumerate(tids)]
    fn = scripted(night(lessons))
    await reflection(store, home, activity, props, fn, clock).run()
    assert LESSONS_PER_DAY == 3
    assert len(waiting(props)) == LESSONS_PER_DAY
    # a second night the same day (a forced run) adds nothing more
    owner_says(store, clock, "one more thing")
    fn2 = scripted(night([skill_lesson(tids[4], sid="site-lesson-9")]))
    await reflection(store, home, activity, props, fn2, clock).run(force=True)
    assert len(waiting(props)) == LESSONS_PER_DAY


async def test_a_run_that_read_outside_text_marks_its_proposal(tmp_path):
    store, home, activity, props, _notes, clock = make(tmp_path)
    tid = finished_run(store, activity, clock, outside=True)
    owner_says(store, clock)
    fn = scripted(night([skill_lesson(tid)]))
    await reflection(store, home, activity, props, fn, clock).run()
    assert waiting(props)[0]["outside_text"] is True


async def test_a_lesson_about_a_built_in_skill_is_a_note_not_a_new_skill(tmp_path):
    store, home, activity, props, notes, clock = make(tmp_path)
    tid = finished_run(store, activity, clock)
    owner_says(store, clock)
    note = {"run": tid, "kind": "note", "skill": "research", "note": "Check the date of each source before quoting it.", "already_in": ""}
    fn = scripted(night([note]))
    await reflection(store, home, activity, props, fn, clock).run()
    pending = waiting(props)
    assert len(pending) == 1 and pending[0]["kind"] == "note" and pending[0]["skill"] == "research"
    assert notes == []   # nothing is written until the owner approves


async def test_a_procedure_that_names_a_built_in_id_becomes_a_note(tmp_path):
    store, home, activity, props, _notes, clock = make(tmp_path)
    tid = finished_run(store, activity, clock)
    owner_says(store, clock)
    fn = scripted(night([skill_lesson(tid, sid="research", description="Check dates.", steps=["Read the date first."])]))
    await reflection(store, home, activity, props, fn, clock).run()
    pending = waiting(props)
    assert len(pending) == 1 and pending[0]["kind"] == "note" and pending[0]["skill"] == "research"


async def test_a_lesson_the_skills_already_hold_adds_nothing(tmp_path):
    store, home, activity, props, _notes, clock = make(tmp_path)
    tid = finished_run(store, activity, clock)
    owner_says(store, clock)
    fn = scripted(night([skill_lesson(tid, already_in="shop-sign-in-again")]))
    await reflection(store, home, activity, props, fn, clock).run()
    assert waiting(props) == []


async def test_a_run_without_tool_steps_is_not_shown_to_the_model(tmp_path):
    store, home, activity, props, _notes, clock = make(tmp_path)
    tid = activity.start_task("Chat about the weather", "chat", turn_id="t-plain")
    activity.finish_task(tid, "done", answer="Sunny.", seen=True)
    owner_says(store, clock)
    fn = scripted(night([]))
    await reflection(store, home, activity, props, fn, clock).run()
    sent = json.dumps([q.messages for q in fn.seen], ensure_ascii=False)
    assert "Chat about the weather" not in sent and "[run " not in sent   # the section names the runs; no run block is sent


async def test_a_lesson_from_a_background_job_names_the_job(tmp_path):
    store, home, activity, props, _notes, clock = make(tmp_path)
    tid = finished_run(store, activity, clock, worker_id="w-42")
    owner_says(store, clock)
    fn = scripted(night([skill_lesson(tid)]))
    await reflection(store, home, activity, props, fn, clock).run()
    assert waiting(props)[0]["origin"] == {"type": "job", "worker_id": "w-42"}


def test_the_per_run_lesson_call_is_gone_from_the_server():
    import assistant.server as server

    assert not hasattr(server, "learn_from_run") and not hasattr(server, "schedule_lesson")


def test_the_memory_pass_window_holds_no_task_or_work_record():
    """Work records ("Decided & done") stay in the job and activity history: the memory pass reads the owner's own messages only, so a
    finished run's task or approval is not in its window."""
    import dataclasses
    import inspect

    from assistant import memory_claims
    from assistant.memory_types import Window

    assert "tasks" not in {f.name for f in dataclasses.fields(Window)}
    assert "approvals" not in {f.name for f in dataclasses.fields(Window)}
    src = inspect.getsource(memory_claims.build_window)
    assert "FROM tasks" not in src and "FROM approval_log" not in src


def test_run_steps_keeps_names_and_outcomes_not_arguments_or_results():
    events = [{"type": "tool_started", "id": "a", "name": "browser_open", "arguments": {"url": "https://secret.example/?k=1"}},
              {"type": "tool_result", "id": "a", "name": "browser_open", "output": "ok", "is_error": False},
              {"type": "tool_started", "id": "b", "name": "browser_click", "arguments": {}},
              {"type": "tool_result", "id": "b", "name": "browser_click", "output": "timeout", "is_error": True}]
    out = json.loads(run_steps(events))
    assert out == {"steps": [{"tool": "browser_open", "ok": True}, {"tool": "browser_click", "ok": False}], "outside": False}
    assert "secret.example" not in run_steps(events)
    assert run_steps([{"type": "text", "text": "hi"}]) == ""


def test_an_approved_note_is_added_to_the_skills_notes_file(tmp_path):
    home = Home(tmp_path / "home")
    append_note(home, "research", "First note.")
    append_note(home, "research", "Second note.")
    text = home.read("notes/skills/research.md")
    assert "First note." in text and "Second note." in text
    with pytest.raises(HomeError):
        append_note(home, "research", "x" * SKILL_NOTES_MAX)
