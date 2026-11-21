"""Goal records are kept whole and read whole (owner 2026-11-20, D8, D9, D10, D13, D16): no entry is ever trimmed, the apps read the
entries a page at a time, Kelo reads every entry and value, there are no count or length caps on a goal's texts, the nightly look-back
adds at most two goal candidates, and the goal notes are hidden from the apps' file screens. Fake models only; in-memory store."""

import json

import httpx
import pytest

from assistant import goals as G
from assistant.goals import GoalBook, GoalError
from assistant import home as home_mod
from assistant.home import Home
from assistant.reflection import GOALS_PER_NIGHT, PROMPT, Reflection
from assistant.server import create_app
from assistant.store import Store

from test_server import AUTH, Running, settings


def fresh(tmp_path=None):
    store = Store()
    home = Home(tmp_path / "home") if tmp_path else None
    return store, GoalBook(store, home)


def test_no_goal_entry_is_ever_trimmed():
    store, b = fresh()
    g = b.create("Goal A")
    for i in range(350):
        b.log(g["id"], f"line {i}")
    for i in range(50):
        b.entry(g["id"], f"t{i}", "", f"entry {i}")
    assert len(b.logs(g["id"], None)) == 400


async def test_the_goal_screen_reads_entries_a_page_at_a_time(tmp_path):
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=None)
    b = app.state.goal_book
    g = b.create("Goal A")
    for i in range(150):
        b.log(g["id"], f"line {i}")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        ws = (await c.get(f"/api/goals/{g['id']}")).json()
        assert len(ws["log"]) == G.LOG_PAGE == 100 and ws["log_next"] == 100
        page = (await c.get(f"/api/goals/{g['id']}/log", params={"offset": 100})).json()
        assert len(page["items"]) == 50 and page["next"] is None
        assert len((await c.get(f"/api/goals/{g['id']}/log", params={"limit": 500})).json()["items"]) == 100
        assert (await c.get(f"/api/goals/{g['id']}/log", params={"limit": "x"})).status_code == 422
        assert (await c.get("/api/goals/99999/log")).status_code == 404


def test_kelo_reads_every_entry_and_value_in_full():
    store, b = fresh()
    g = b.create("Goal A")
    for i in range(12):
        b.entry(g["id"], f"title {i}", "", f"{i}-" + "x" * 500)
    for i in range(20):
        b.metric_add(g["id"], "steps walked", value=i + 1, unit="k")
    text = b.text(g["id"])
    for i in range(12):
        assert f"{i}-" + "x" * 500 in text
    assert "Entries:" in text and "Recent entries:" not in text
    assert all(f": {i + 1} k" in text or f"{i + 1}k" in text or str(i + 1) in text for i in range(20))


def test_a_goal_keeps_any_number_of_steps_facts_numbers_and_values():
    store, b = fresh()
    g = b.create("Goal A")
    b.plan(g["id"], [{"title": f"step {i}"} for i in range(31)])
    for i in range(31):
        b.set_fact(g["id"], f"fact {i}", "v")
    for i in range(7):
        b.metric_add(g["id"], f"number {i}", value=1)
    for i in range(60):                                    # with the one above: 61 values
        b.metric_add(g["id"], "number 0", value=i)
    assert len(b.steps(g["id"])) == 31 and len(b.facts(g["id"])) == 31
    assert len(b.metrics(g["id"])) == 7 and len(next(m for m in b.metrics(g["id"]) if m["name"] == "number 0")["values"]) == 61


def test_goal_texts_are_kept_whole():
    store, b = fresh()
    g = b.create("Goal A", category="c" * 60, note="n" * 2500)
    assert len(g["note"]) == 2500 and len(store.goal(g["id"])["category"]) == 60
    long_title = "t" * 300
    got = b.create(long_title)
    assert store.goal(got["id"])["title"] == long_title
    b.entry(g["id"], "e", "", "x" * 1500)
    assert len(b.logs(g["id"], None)[0]["text"]) == 1500
    b.plan(g["id"], [{"title": "s", "detail": "d" * 600}])
    assert len(b.steps(g["id"])[0]["detail"]) == 600
    b.set_fact(g["id"], "k" * 80, "v" * 400)
    f = b.facts(g["id"])[0]
    assert len(f["key"]) == 80 and len(f["value"]) == 400
    m = b.metric_add(g["id"], "m" * 80, value=1, unit="u" * 30, note="n" * 300)
    assert len(m["name"]) == 80 and len(m["unit"]) == 30 and len(m["values"][0]["note"]) == 300


def test_search_still_needs_two_letters_and_lists_stop_at_100():
    store, b = fresh()
    for i in range(120):
        b.create(f"goal number {i}")
    with pytest.raises(GoalError):
        b.search("goal", "a")
    items, _ = b.page("goal", 500)
    assert len(items) == 100


def test_the_nightly_pass_adds_at_most_two_new_candidates():
    assert GOALS_PER_NIGHT == 2 and "at most 2 goals" in PROMPT   # the nightly prompt says it with the same number

    class FakeGoals:
        def __init__(self):
            self.seen = []

        def suggest(self, title, reason, evidence, source):
            self.seen.append(title)
            return None if title == "repeat" else len(self.seen)

    r = Reflection.__new__(Reflection)
    r.goals = FakeGoals()
    added = r.apply_goals({"goals": [{"title": "repeat"}, {"title": "a"}, {"title": "b"}, {"title": "c"}]})
    assert added == 2 and r.goals.seen == ["repeat", "a", "b"]


def test_proposed_candidates_are_never_dropped_to_make_room():
    store, b = fresh()
    for i in range(9):
        b.suggest(f"candidate {i}", "why", "ev", "chat")
    assert len(b.suggestions()) == 9 and all(s["status"] == "proposed" for s in b.suggestions())


def test_a_nightly_candidate_nobody_answers_disappears_after_seven_days():
    store, b = fresh()
    clock = [1000.0]
    b.clock = lambda: clock[0]
    b.suggest("old one", "why", "ev", "nightly")
    b.suggest("chat one", "why", "ev", "chat")
    clock[0] += 8 * 86400
    titles = [s["title"] for s in b.suggestions()]
    assert titles == ["chat one"]


def test_goal_notes_are_hidden_from_the_apps_file_screen(tmp_path):
    home = Home(tmp_path / "home")
    assert G.NOTES_DIR in home_mod.APP_HIDDEN   # the apps' file screens leave these out (home.py)
    home.write(f"{G.NOTES_DIR}/1/notes.md", "my notes")
    (tmp_path / "home" / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / "files" / "plan.md").write_text("x")
    app_names = [n["path"] for n in home.tree(folders=True, for_app=True)]
    assert not any(p.startswith("notes/goals") for p in app_names) and "files/plan.md" in app_names
    assert any(p.startswith("notes/goals") for p in [n["path"] for n in home.tree(folders=True)])
    assert any("notes.md" in str(x) for x in home.walk("notes"))


def test_only_asked_for_documents_show_on_a_goal(tmp_path):
    store, b = fresh(tmp_path)
    g = b.create("Goal A")
    (tmp_path / "home" / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / "files" / "plan.pdf").write_bytes(b"%PDF")
    with pytest.raises(GoalError):
        b.add_document(g["id"], f"{G.NOTES_DIR}/{g['id']}/notes.md")
    store._run("INSERT INTO goal_docs(goal_id, path, title, added_at) VALUES(?,?,?,?)", (g["id"], f"{G.NOTES_DIR}/{g['id']}/old.md", "old", 0.0))
    GoalBook(store, b.home)   # a start: the notes folder's old rows go
    assert store._all("SELECT 1 FROM goal_docs WHERE path LIKE 'notes/goals/%'") == []
    b.add_document(g["id"], "files/plan.pdf")
    assert [d["path"] for d in b.documents(g["id"])] == ["files/plan.pdf"]
