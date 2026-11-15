"""Goals as a workspace (goals.py) with fake models only: facts, plan, numbers, log and documents; the kickoff turn with tap-to-answer
buttons; the workspace API; suggestions from the nightly look-back and from chat; the per-turn prompt block; and nothing in the
instructions that is about one kind of goal."""

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from assistant.goals import GOAL_MARK, KICKOFF, RESUME, GoalBook, GoalError, asks_typing
from assistant.home import Home
from assistant.prompt import goals_block
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.store import Store

from test_reflection import answering, say, setup, ts
from test_server import AUTH, Running, settings

ROOT = Path(__file__).resolve().parents[1]


def book(tmp_path=None):
    store = Store()
    home = Home(tmp_path / "home") if tmp_path else None
    return store, GoalBook(store, home)


# ---------------------------------------------------------------------------------------------------------- the book
def test_history_lines_show_in_the_readers_language():
    from assistant import i18n
    store, b = book()
    g = store.add_goal("Goal A")
    tok = i18n.use("en")                  # written while some task ran in English...
    try:
        b.log_t(g["id"], "Learned: {facts}", "fact", facts=[["focus", "diet"], ["age", None]])
        b.log_t(g["id"], "Done: {title}", "step", suffix="went well", title="first")
    finally:
        i18n.reset(tok)
    b.log(g["id"], "Learned: old = line", "fact")   # an English line from before
    tok = i18n.use("ko")                  # ...and read in Korean
    try:
        texts = [r["text"] for r in b.logs(g["id"])]
    finally:
        i18n.reset(tok)
    assert texts == ["알게 된 것: old = line", i18n.t("Done: {title}", lang="ko", title="first") + " (went well)",
                     "알게 된 것: focus = diet; age (말하지 않음)"]
    tok = i18n.use("en")
    try:
        assert b.logs(g["id"])[-1]["text"] == "Learned: focus = diet; age (not said)"
    finally:
        i18n.reset(tok)


def test_facts_plan_numbers_log_and_documents(tmp_path):
    store, b = book(tmp_path)
    g = store.add_goal("Goal A")
    b.set_fact(g["id"], "starting point", "level 2")
    b.set_fact(g["id"], "private detail", skipped=True)
    with pytest.raises(GoalError):
        b.set_fact(g["id"], "empty", "")
    assert [(f["key"], f["value"], f["skipped"]) for f in b.facts(g["id"])] == [("starting point", "level 2", False), ("private detail", "", True)]

    steps = b.plan(g["id"], [{"title": "first", "owner": "user", "due": "2026-11-20"}, {"title": "research", "owner": "agent"}])
    assert [(s["title"], s["owner"], s["status"], s["due"]) for s in steps] == [("first", "user", "todo", "2026-11-20"), ("research", "agent", "todo", None)]
    b.plan(g["id"], [{"id": steps[1]["id"], "status": "doing"}, {"title": "third"}])
    assert [s["status"] for s in b.steps(g["id"])] == ["todo", "doing", "todo"]
    done = b.set_step_status(g["id"], steps[0]["id"], "done", "user")
    assert done["status"] == "done" and done["done_at"]
    rebuilt = b.plan(g["id"], [{"title": "new one", "owner": "agent"}], replace=True)
    assert [s["title"] for s in rebuilt] == ["first", "new one"]          # a done step survives a rebuild
    with pytest.raises(GoalError):
        b.plan(g["id"], [{"title": "x", "owner": "someone"}])
    with pytest.raises(GoalError):
        b.plan(g["id"], [{"title": "x", "due": "soon"}])

    m = b.metric_add(g["id"], "score", unit="pt", start=10, target=20)
    assert m["values"] == [] and m["target"] == 20
    m = b.metric_add(g["id"], "score", value=12)
    assert [v["value"] for v in m["values"]] == [12] and m["unit"] == "pt"
    with pytest.raises(GoalError):
        b.metric_add(g["id"], "score", value="lots")

    (tmp_path / "home" / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / "files" / "plan.pdf").write_bytes(b"%PDF")
    assert b.add_document(g["id"], "files/plan.pdf", "The plan")["title"] == "The plan"
    with pytest.raises(GoalError):
        b.add_document(g["id"], "files/missing.pdf")
    with pytest.raises(GoalError):
        b.add_document(g["id"], "../etc/passwd")

    w = b.workspace(g["id"])
    assert w["goal"]["plan"] == {"done": 1, "total": 2} and w["goal"]["next_step"] == "new one"
    assert [d["exists"] for d in w["documents"]] == [True]
    assert any("score: 12 pt" in r["text"] for r in w["log"]) and any(r["text"].startswith("Done: first") for r in w["log"])
    text = b.text(g["id"])
    assert "private detail = (the user chose not to say)" in text and "(you) new one" in text and "target 20" in text

    b.delete(g["id"])
    store.delete_goal(g["id"])
    assert b.facts(g["id"]) == [] and b.steps(g["id"]) == [] and b.metrics(g["id"]) == []


def test_prompt_block_is_compact_and_shows_progress_next_step_facts_and_waiting():
    store, b = book()
    g = store.add_goal("Goal A", note="by spring")
    store.add_goal("Finished", "")
    store.update_goal(store.goals()[0]["id"], status="done")
    b.set_fact(g["id"], "level", "beginner")
    b.set_fact(g["id"], "budget", skipped=True)
    b.plan(g["id"], [{"title": "look things up", "owner": "agent"}, {"title": "try it", "owner": "user"}])
    b.set_asking(g["id"], True)
    block = goals_block(b.for_prompt())
    assert block.startswith("Goals the user is working on")
    line = block.splitlines()[1]
    assert f"#{g['id']} Goal A" in line and "plan 0/2 done" in line and "next (you): look things up" in line
    assert "level=beginner" in line and "budget=(declined)" in line and "waiting for the user's answers" in line
    assert "Finished" not in block
    assert goals_block([]) == ""
    for i in range(30):
        store.add_goal(f"goal number {i} " + "x" * 150)
    assert len(goals_block(b.for_prompt())) < 2_600


def test_quick_replies_are_cleaned_and_shown_only_until_the_user_writes_again():
    store, b = book()
    qs = b.clean_questions([{"question": "Q", "options": ["a", "b", "a", "", "c"]}])
    assert qs == [{"question": "Q", "options": ["a", "b", "c"]}]
    with pytest.raises(GoalError):
        b.clean_questions([{"question": "Q", "options": ["only one"]}])
    with pytest.raises(GoalError):
        b.clean_questions([])
    store.add_message("user", "hi")
    mid = store.add_message("assistant", "question?")
    b.attach_replies(mid, qs, None)
    rows = b.replies_for(store.messages())
    assert rows[-1]["replies"] == {"questions": qs, "goal_id": None}
    store.add_message("user", f"{GOAL_MARK} hidden")          # not the user's words: the buttons stay
    assert "replies" in b.replies_for(store.messages())[-2]
    store.add_message("user", "a")
    assert all("replies" not in m for m in b.replies_for(store.messages()))


def test_answers_that_ask_for_typing_open_a_text_box():
    _, b = book()
    qs = b.clean_questions([
        {"question": "Weight (now -> target)", "options": ["I'll type it", "Rather not say"], "hint": "now and target weight"},
        {"question": "Diet", "options": ["None", "Some (I'll write)", "Rather not say"], "typed": ["Some (I'll write)", "not an option"]},
        {"question": "Sport", "options": ["Running", "Swimming", "Something"], "typed": ["Something"]},
        {"question": "When", "options": ["Today", "Tomorrow"], "hint": "dropped without a typed answer"}])
    assert qs[0] == {"question": "Weight (now -> target)", "options": ["I'll type it", "Rather not say"], "typed": ["I'll type it"],
                     "hint": "now and target weight"}
    assert qs[1]["typed"] == ["Some (I'll write)"] and "hint" not in qs[1]
    assert qs[2]["typed"] == ["Something"]                       # the model's own mark, words that say nothing about typing
    assert qs[3] == {"question": "When", "options": ["Today", "Tomorrow"]}
    for label in ("직접 입력할게요", "있어요 (직접 입력)", "Other", "기타", "Other (please specify)", "Something else…", "その他", "其他",
                  "Otro", "Свой вариант", "Tự nhập"):
        assert asks_typing(label), label
    for label in ("말하고 싶지 않아요", "없어요", "Rather not say", "Prefiero no decirlo", "Otra vez", "1개월", "Another time", "직접 할게요"):
        assert not asks_typing(label), label


def test_quick_replies_says_whole_sentence():
    _, b = book()
    # options ["A", "B"], says ["Go with A, the cheaper one", "B please"] → item["says"] == the same two
    qs = b.clean_questions([{"question": "Q", "options": ["A", "B"], "says": ["Go with A, the cheaper one", "B please"]}])
    assert qs[0]["says"] == ["Go with A, the cheaper one", "B please"]

    # says shorter than options → the missing ones are ""
    qs = b.clean_questions([{"question": "Q", "options": ["A", "B"], "says": ["Go with A, the cheaper one"]}])
    assert qs[0]["says"] == ["Go with A, the cheaper one", ""]

    # a duplicate option ["A", "A", "B"] with says ["x", "y", "z"] → options ["A", "B"], says ["x", "z"]
    qs = b.clean_questions([{"question": "Q", "options": ["A", "A", "B"], "says": ["x", "y", "z"]}])
    assert qs[0]["options"] == ["A", "B"]
    assert qs[0]["says"] == ["x", "z"]

    # a say equal to its label is dropped (""), and when no option has a sentence, there is no "says" key at all
    qs = b.clean_questions([{"question": "Q", "options": ["A", "B"], "says": ["A", "B"]}])
    assert "says" not in qs[0]
    qs = b.clean_questions([{"question": "Q", "options": ["A", "B"], "says": ["Go with A, the cheaper one", "B"]}])
    assert qs[0]["says"] == ["Go with A, the cheaper one", ""]

    # a typed option ("Other (write it)") gets ""
    qs = b.clean_questions([{"question": "Q", "options": ["A", "Other (write it)"], "says": ["Go with A, the cheaper one", "I'll type it"]}])
    assert qs[0]["says"] == ["Go with A, the cheaper one", ""]


def test_a_goal_shows_working_while_something_runs_for_it_and_waiting_only_after():
    store, b = book()
    g = store.add_goal("Goal A", "")
    assert b.public(store.goal(g["id"]))["asking"] is False and b.public(store.goal(g["id"]))["working"] is False
    b.set_asking(g["id"], True)
    assert b.public(store.goal(g["id"]))["asking"] is True
    running: set[int] = {g["id"]}
    b.busy = lambda: running
    p = b.public(store.goal(g["id"]))
    assert p["working"] is True and p["asking"] is False         # never "waiting for you" while Kelo works on it
    assert b.for_prompt()[0]["asking"] is True                   # the model still knows the answer is for this goal
    running.clear()
    store.add_message("user", f"{GOAL_MARK} hidden")               # not the user's words
    assert b.public(store.goal(g["id"]))["asking"] is True
    store.add_message("user", "80kg now, 70kg target")             # the user answered: the buttons are gone, nothing to wait for
    p = b.public(store.goal(g["id"]))
    assert p["asking"] is False and p["working"] is False
    assert "Waiting for the user's answers" in b.text(g["id"])     # still routed to the goal until facts or a plan are saved
    assert b.answering() == {g["id"]}                              # that first message is taken as the answer
    store.add_message("user", "something else entirely")
    assert b.answering() == set()                                  # later chat is not work on the goal


async def test_answering_a_goals_question_shows_the_goal_as_worked_on_until_the_reply_ends(tmp_path):
    gate = asyncio.Event()
    inner = kickoff_model()

    async def fn(provider, request, **kw):
        last = request.messages[-1]
        if last["role"] == "user" and last.get("content") == "Halfway":
            await gate.wait()
            yield TextDelta("Noted.")
            yield Done("stop")
            return
        async for x in inner(provider, request, **kw):
            yield x
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        g = (await c.post("/api/goals", json={"title": "Goal A"})).json()

        async def state(asking, working):
            g0 = (await c.get("/api/goals")).json()[0]
            return g0 if (g0["asking"], g0["working"]) == (asking, working) else None
        assert g["working"] is True and g["asking"] is False   # the kickoff was started (it may still wait its turn)
        await wait_for(lambda: state(True, False))
        task = asyncio.ensure_future(c.post("/api/talk", json={"text": "Halfway"}, timeout=10))
        await wait_for(lambda: state(False, True))
        gate.set()
        await task
        await wait_for(lambda: state(False, False))
        assert g["id"] == (await c.get("/api/goals")).json()[0]["id"]


def test_suggestions_never_repeat_goals_or_declined_ones():
    store, b = book()
    store.add_goal("Existing goal")
    assert b.suggest("existing goal!", "r") is None
    s1 = b.suggest("New aim", "you said so", "said it on Monday")
    assert s1 and b.suggest("new aim", "again") is None
    assert b.dismiss(s1) and not b.dismiss(s1)
    assert b.suggest("New aim", "third time") is None
    s2 = b.suggest("Other aim", "why")
    g = b.accept(s2)
    assert g["title"] == "Other aim" and g["origin"] == "suggestion" and g["note"] == "why"
    assert b.accept(s2) is None and b.suggestions() == []
    for i in range(10):
        b.suggest(f"aim {i}", "r")
    assert len(b.suggestions()) == 6


# ---------------------------------------------------------------------------------------------------------- instructions stay generic
def test_the_goal_instructions_name_no_kind_of_goal():
    """The kickoff, the resume note, the tool descriptions and the prompt section are about goals in general, never one kind. (Health,
    money and relationships appear only in the rule against guessing sensitive things; "job" there means a scheduled job.)"""
    store, b = book()
    words = re.compile(r"\b(diet|weight|exercise|fitness|meal|career|resume|cv|salary|study|exam|marathon|savings?|budget|"
                       r"workout|calorie|interview)\b", re.I)
    texts = [KICKOFF, RESUME, *(t.description for t in b.tools()), json.dumps([t.parameters for t in b.tools()])]
    system = (ROOT / "content" / "prompts" / "system.md").read_text(encoding="utf-8")
    texts.append(system[system.index("**Goals and reminders.**"):system.index("**Files and media.**")])
    from assistant.prompt import GOAL_RULES
    texts.append(GOAL_RULES)                       # how to work on goals: next to the goals in the per-turn block
    hits = [m.group(0) for t in texts for m in words.finditer(t)]
    assert hits == []


# ---------------------------------------------------------------------------------------------------------- the app
async def wait_for(fn, tries=200):
    for _ in range(tries):
        if (v := await fn()):
            return v
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


def kickoff_model():
    """Asks with buttons when it sees a goal kickoff; saves the facts and a plan when the user answers; plain otherwise."""
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request)
        last = request.messages[-1]
        text = last.get("content") if isinstance(last.get("content"), str) else ""
        gid = (re.search(r"#(\d+)", text) or re.search(r"#(\d+)", request.messages[0]["content"]) or [None, "1"])[1]
        if last["role"] == "user" and text.startswith(GOAL_MARK) and "just added" in text:
            yield ToolCall("q1", "quick_replies", json.dumps({"goal_id": int(gid), "questions": [
                {"question": "Where are you now?", "options": ["Just starting", "Halfway", "Rather not say"]}]}))
            yield Done("tool_calls")
        elif last["role"] == "user" and text == "Rather not say":
            yield ToolCall("f1", "goal_set_facts", json.dumps({"goal_id": int(gid), "facts": [{"key": "where now", "skipped": True}]}))
            yield ToolCall("p1", "goal_plan", json.dumps({"goal_id": int(gid), "steps": [{"title": "a first step", "owner": "user"},
                                                                                         {"title": "look into options", "owner": "agent"}]}))
            yield Done("tool_calls")
        elif last["role"] == "tool":
            yield TextDelta("Where are you now?" if last.get("name") == "quick_replies" or "buttons" in str(last.get("content")) else "Here is the plan.")
            yield Done("stop")
        else:
            yield TextDelta("ok")
            yield Done("stop")
    fn.seen = seen
    return fn


async def test_a_new_goal_starts_a_kickoff_with_buttons_and_the_answer_lands_on_the_goal(tmp_path):
    fn = kickoff_model()
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        g = (await c.post("/api/goals", json={"title": "Goal A"})).json()
        assert g["plan"] == {"done": 0, "total": 0} and g["origin"] == "user"

        async def asked():
            ms = (await c.get("/api/messages")).json()
            return ms if ms and ms[-1]["role"] == "assistant" and "replies" in ms[-1] else None
        ms = await wait_for(asked)
        assert [m["role"] for m in ms] == ["assistant"]            # the kickoff message itself stays hidden
        assert ms[-1]["replies"] == {"goal_id": g["id"], "questions": [
            {"question": "Where are you now?", "options": ["Just starting", "Halfway", "Rather not say"]}]}
        assert GOAL_MARK in fn.seen[0].messages[-1]["content"] and "Goal A" in fn.seen[0].messages[-1]["content"]

        async def waiting():   # the kickoff's turn is over: no longer worked on, waiting for the answer
            g0 = (await c.get("/api/goals")).json()[0]
            return g0 if g0["asking"] and not g0["working"] else None
        await wait_for(waiting)

        async with c.stream("POST", "/api/talk", json={"text": "Rather not say"}) as r:
            [json.loads(line[6:]) async for line in r.aiter_lines() if line.startswith("data: ")]
        said = lambda req: " ".join(m["content"] for m in req.messages if isinstance(m.get("content"), str))   # system text and the turn's note
        assert "waiting for the user's answers" in said(fn.seen[-2]) or "waiting for the user's answers" in said(fn.seen[-3])
        w = (await c.get(f"/api/goals/{g['id']}")).json()
        assert w["facts"] == [{"key": "where now", "value": "", "skipped": True, "source": "user", "updated_at": w["facts"][0]["updated_at"]}]
        assert [(s["title"], s["owner"]) for s in w["steps"]] == [("a first step", "user"), ("look into options", "agent")]
        assert w["goal"]["asking"] is False and w["goal"]["next_step"] == "a first step"
        assert all("replies" not in m for m in (await c.get("/api/messages")).json())   # answered: the buttons are gone

        # the user ticks their step; a fact and a number from the app
        sid = w["steps"][0]["id"]
        assert (await c.patch(f"/api/goals/{g['id']}/steps/{sid}", json={"status": "done"})).json()["status"] == "done"
        assert (await c.patch(f"/api/goals/{g['id']}/steps/{sid}", json={"status": "bogus"})).status_code == 422
        assert (await c.patch(f"/api/goals/{g['id']}/steps/9999", json={"status": "done"})).status_code == 404
        assert (await c.put(f"/api/goals/{g['id']}/facts/where now", json={"value": "halfway"})).json()["value"] == "halfway"
        assert (await c.delete(f"/api/goals/{g['id']}/facts/where now")).json() == {"ok": True}
        assert (await c.get("/api/goals/9999")).status_code == 404
        assert (await c.get("/api/goals")).json()[0]["plan"] == {"done": 1, "total": 2}


async def test_no_kickoff_when_asked_not_to_and_work_needs_a_model(tmp_path):
    fn = kickoff_model()
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        g = (await c.post("/api/goals", json={"title": "Quiet goal", "kickoff": False})).json()
        await asyncio.sleep(0.1)
        assert fn.seen == [] and (await c.get("/api/messages")).json() == []
        assert (await c.post(f"/api/goals/{g['id']}/work")).json() == {"ok": True}
        await wait_for(lambda: asyncio.sleep(0, result=bool(fn.seen)))
        assert "pick goal" in fn.seen[0].messages[-1]["content"] and GOAL_MARK in fn.seen[0].messages[-1]["content"]
    from assistant.server import Settings
    from test_server import TOKEN
    app = create_app(Settings(token=TOKEN, cdp_host=None, db_path=str(tmp_path / "b.db")), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        g = (await c.post("/api/goals", json={"title": "No model"})).json()
        assert (await c.post(f"/api/goals/{g['id']}/work")).status_code == 409


async def test_suggestion_api_accept_and_dismiss(tmp_path):
    fn = kickoff_model()
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        b = GoalBook(Store(str(tmp_path / "a.db")))
        s1, s2 = b.suggest("Aim one", "because", "you said"), b.suggest("Aim two", "why")
        items = (await c.get("/api/goals/suggestions")).json()["items"]
        assert [i["title"] for i in items] == ["Aim two", "Aim one"] and items[1]["evidence"] == "you said"
        assert (await c.post(f"/api/goals/suggestions/{s2}/dismiss")).json() == {"ok": True}
        g = (await c.post(f"/api/goals/suggestions/{s1}/accept")).json()
        assert g["title"] == "Aim one" and g["origin"] == "suggestion"
        assert (await c.get("/api/goals/suggestions")).json()["items"] == []
        assert (await c.post(f"/api/goals/suggestions/{s1}/accept")).status_code == 404
        await wait_for(lambda: asyncio.sleep(0, result=bool(fn.seen)))   # accepting starts the kickoff
        assert "Aim one" in fn.seen[0].messages[-1]["content"]


# ---------------------------------------------------------------------------------------------------------- the nightly look-back
async def test_the_nightly_run_suggests_grounded_goals_and_skips_taken_ones(tmp_path):
    out = json.dumps({"new": [], "update": [], "drop": [], "reasons": ["x"], "goals": [
        {"title": "Aim one", "reason": "you mentioned it twice", "evidence": "said on Friday"},
        {"title": "Existing goal", "reason": "dup"}, {"title": "Aim two", "reason": "r"}, {"title": "Aim three", "reason": "r"},
        {"title": "Aim four", "reason": "over the limit"}]})
    fn = answering(out)
    store, home, r, clock = setup(tmp_path, fn)
    b = GoalBook(store, home)
    r.goals = b
    store.add_goal("Existing goal")
    say(store, ts(2026, 11, 7, 20, 0), "user", "something I want to do")
    res = await r.run()
    assert res["status"] == "done" and "goal(s) suggested" in res["reason"]
    assert [s["title"] for s in b.suggestions()] == ["Aim two", "Aim one"]     # three looked at, the existing one left out
    assert "Existing goal" in fn.calls[0].messages[1]["content"] and "Goals and earlier suggestions" in fn.calls[0].messages[1]["content"]
    r.set_enabled(False)
    assert (await r.nightly())["status"] == "skipped"


# ---------------------------------------------------------------------------------------------------------- schedules the user saves
def schedule_check_model(answer: str):
    """Answers a schedule check with `answer` (NO_REPLY = all ready, or a question with buttons); "ok" otherwise."""
    from assistant.schedules import SCHEDULE_MARK
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request)
        last = request.messages[-1]
        text = last.get("content") if isinstance(last.get("content"), str) else ""
        if last["role"] == "user" and text.startswith(SCHEDULE_MARK) and answer != "NO_REPLY":
            yield ToolCall("q1", "quick_replies", json.dumps({"questions": [{"question": "Which account?", "options": ["Work", "Personal"]}]}))
            yield Done("tool_calls")
        elif last["role"] == "tool":
            yield TextDelta("Which account should I use?")
            yield Done("stop")
        elif last["role"] == "user" and text.startswith(SCHEDULE_MARK):
            yield TextDelta("NO_REPLY")
            yield Done("stop")
        else:
            yield TextDelta("ok")
            yield Done("stop")
    fn.seen = seen
    return fn


async def test_a_schedule_the_user_saves_is_checked_ahead_silently_when_all_is_ready(tmp_path):
    from assistant.schedules import SCHEDULE_CHECK, SCHEDULE_MARK
    fn = schedule_check_model("NO_REPLY")
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True, with_schedule_check=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "Morning", "prompt": "Tell me the weather", "cron": "0 9 * * *"})).json()
        await wait_for(lambda: asyncio.sleep(0, result=bool(fn.seen)))
        handed = fn.seen[0].messages[-1]["content"]
        assert handed.startswith(SCHEDULE_MARK) and "Morning" in handed and "Every day at 9:00 AM" in handed and "0 9 * * *" not in handed
        await asyncio.sleep(0.2)
        assert (await c.get("/api/messages")).json() == []                                  # silent: nothing in the chat
        assert all(n["type"] != "task_done" for n in (await c.get("/api/notifications")).json()["items"])
        # the switch alone is not handed over again; a new time is
        n = len(fn.seen)
        assert (await c.patch(f"/api/schedules/{s['id']}", json={"enabled": False})).status_code == 200
        assert (await c.patch(f"/api/schedules/{s['id']}", json={"enabled": True})).status_code == 200
        await asyncio.sleep(0.2)
        assert len(fn.seen) == n
        assert (await c.patch(f"/api/schedules/{s['id']}", json={"cron": "30 7 * * 1-5"})).status_code == 200
        await wait_for(lambda: asyncio.sleep(0, result=len(fn.seen) > n))
        assert "changed" in fn.seen[n].messages[-1]["content"] and "Weekdays at 7:30 AM" in fn.seen[n].messages[-1]["content"]
    # generic: nothing about one kind of job or site
    for word in ("lotto", "lottery", "bank", "coupang", "naver", "kakao"):
        assert word not in SCHEDULE_CHECK.lower()


async def test_a_schedule_check_asks_once_with_buttons_when_something_is_missing(tmp_path):
    fn = schedule_check_model("ask")
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True, with_schedule_check=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/schedules", json={"name": "Mail", "prompt": "Sort my mail", "run_at": "2030-01-02T08:00"})

        async def asked():
            ms = (await c.get("/api/messages")).json()
            return ms if ms and ms[-1]["role"] == "assistant" and "replies" in ms[-1] else None
        ms = await wait_for(asked)
        assert [m["role"] for m in ms] == ["assistant"]                                     # the hand-off itself stays hidden
        assert ms[-1]["content"] == "Which account should I use?"
        assert ms[-1]["replies"]["questions"] == [{"question": "Which account?", "options": ["Work", "Personal"]}]


# ---------------------------------------------------------------------------------------------------------- saying it twice
INTRO = ("'Goal A' 다짐을 같이 시작해 볼게요. 맞춤 계획을 세우려고 몇 가지만 여쭤볼게요.<message_break/>"
         "1. 지금 어느 정도인가요?\n2. 언제까지 하고 싶으세요?\n3. 하루에 쓸 수 있는 시간은요?")


def test_the_repeat_guard_keeps_one_copy_of_text_said_again_after_a_tool():
    from assistant.repeats import RepeatGuard, collapse_repeat
    g = RepeatGuard()
    out = []
    for ev in [{"type": "text", "text": INTRO[:40]}, {"type": "text", "text": INTRO[40:]}, {"type": "tool_started", "name": "quick_replies"},
               {"type": "tool_result"}, {"type": "text", "text": "\n" + INTRO[:30]}, {"type": "text", "text": INTRO[30:].replace("\n", " ")},
               {"type": "done"}]:
        out += g.feed(ev)
    assert "".join(e.get("text", "") for e in out) == INTRO and out[-1]["type"] == "done"
    g = RepeatGuard()                                              # a different answer after the tool goes through whole
    out = []
    for ev in [{"type": "text", "text": "Looking."}, {"type": "tool_started"}, {"type": "text", "text": "Look"}, {"type": "text", "text": "s fine."},
               {"type": "done"}]:
        out += g.feed(ev)
    assert "".join(e.get("text", "") for e in out) == "Looking.Looks fine."
    assert collapse_repeat(INTRO + "\n\n" + INTRO) == INTRO
    reworded = INTRO.replace("어느 정도인가요", "어느 정도예요").replace("여쭤볼게요", "물어볼게요")   # said again in slightly other words
    assert collapse_repeat(INTRO + "\n\n" + reworded) == INTRO
    g, out = RepeatGuard(), []
    for ev in [{"type": "text", "text": INTRO}, {"type": "tool_started"}, *[{"type": "text", "text": reworded[i:i + 12]} for i in range(0, len(reworded), 12)],
               {"type": "done"}]:
        out += g.feed(ev)
    assert "".join(e.get("text", "") for e in out) == INTRO
    g, out = RepeatGuard(), []                                     # a short answer that only starts alike is not lost
    for ev in [{"type": "text", "text": "Done with the first part."}, {"type": "tool_started"}, {"type": "text", "text": "Done."}, {"type": "done"}]:
        out += g.feed(ev)
    assert "".join(e.get("text", "") for e in out) == "Done with the first part.Done."
    assert collapse_repeat(INTRO) == INTRO and collapse_repeat("ok ok") == "ok ok"


async def test_a_kickoff_that_repeats_its_message_after_the_buttons_is_stored_once(tmp_path):
    calls = []

    async def fn(provider, request, **kw):
        calls.append(request)
        if len(calls) == 1:
            yield TextDelta(INTRO)
            yield ToolCall("q1", "quick_replies", json.dumps({"goal_id": 1, "questions": [{"question": "Q", "options": ["a", "b"]}]}))
            yield Done("tool_calls")
        else:
            yield TextDelta(INTRO)
            yield Done("stop")
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/goals", json={"title": "Goal A"})

        async def stored():
            ms = (await c.get("/api/messages")).json()
            return ms if ms and "replies" in ms[-1] else None
        await wait_for(stored)
        await asyncio.sleep(0.2)
        ms = (await c.get("/api/messages")).json()
        assert [m["content"] for m in ms] == [INTRO] and "replies" in ms[0]
        assert len(calls) == 1   # the message is written: quick_replies closes the reply (Tool.closes_reply), no second pass


# ---------------------------------------------------------------------------------------------------------- typed answer fields
def test_question_kinds_are_checked_and_choice_stays_the_default():
    _, b = book()
    qs = b.clean_questions([
        {"question": "A", "options": ["x", "y"]},
        {"question": "B", "kind": "date", "default": "2026-11-10"},
        {"question": "C", "kind": "TIME", "default": "9:05"},
        {"question": "D", "kind": "weekdays", "default": "wed, mon,mon"},
    ]) + b.clean_questions([
        {"question": "E", "kind": "weekdays_time", "default": "sun 21:30", "options": ["kept for older apps", "too"]},
        {"question": "F", "kind": "number", "min": 1, "max": "5", "unit": "pcs", "default": "3"},
    ])
    assert qs[0] == {"question": "A", "options": ["x", "y"]}                      # no kind written for a plain choice
    assert qs[1] == {"question": "B", "options": [], "kind": "date", "default": "2026-11-10"}
    assert qs[2]["default"] == "09:05" and qs[3]["default"] == "mon,wed"
    assert qs[4]["default"] == "sun 21:30" and qs[4]["options"] == ["kept for older apps", "too"]
    assert qs[5] == {"question": "F", "options": [], "kind": "number", "min": 1.0, "max": 5.0, "unit": "pcs", "default": "3"}
    text = b.clean_questions([{"question": "G", "kind": "text", "hint": "a few words"}])[0]
    assert text == {"question": "G", "options": [], "kind": "text", "hint": "a few words"}
    for bad in ({"question": "Q", "kind": "colour"}, {"question": "Q", "options": ["one"]},
                {"question": "Q", "kind": "date", "default": "10/11/2026"}, {"question": "Q", "kind": "time", "default": "24:00"},
                {"question": "Q", "kind": "weekdays", "default": "someday"}, {"question": "Q", "kind": "number", "min": 5, "max": 1},
                {"question": "Q", "kind": "number", "max": 3, "default": "4"}):
        with pytest.raises(GoalError):
            b.clean_questions([bad])


def test_every_answer_is_checked_against_its_kind():
    check = GoalBook.check_answer
    assert check({"kind": "datetime"}, "2026-01-02T07:00") == "2026-01-02 07:00"
    assert check({"kind": "weekdays_time"}, "fri,mon 7:00") == "mon,fri 07:00"
    assert check({"kind": "weekdays"}, "Sun Sat") == "sat,sun"
    assert check({"kind": "number", "min": 0}, "1,000") == "1000" and check({"kind": "number"}, "2.5") == "2.5"
    assert check({}, " anything  the user wrote ") == "anything the user wrote"   # choice: "Other" may be any words
    for q, v in (({"kind": "date"}, "2026-02-30"), ({"kind": "time"}, "noon"), ({"kind": "number"}, "nan"), ({"kind": "number", "min": 1}, "0"),
                 ({"kind": "weekdays_time"}, "mon"), ({}, "   ")):
        with pytest.raises(GoalError):
            check(q, v)


async def test_a_message_answering_typed_fields_is_checked_and_its_values_reach_the_model(tmp_path):
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request)
        last = request.messages[-1]
        if len(seen) == 1:
            yield TextDelta("Pick a few things.")
            yield ToolCall("q1", "quick_replies", json.dumps({"questions": [
                {"question": "Which", "options": ["x", "y"]}, {"question": "Days", "kind": "weekdays_time"}]}))
            yield Done("tool_calls")
        else:
            yield TextDelta("ok" if last["role"] == "user" else "")
            yield Done("stop")
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/turns", json={"text": "ask me"})

        async def stored():
            ms = (await c.get("/api/messages")).json()
            return ms if ms and "replies" in ms[-1] else None
        ms = await wait_for(stored)
        assert ms[-1]["replies"]["questions"][1]["kind"] == "weekdays_time"
        bad = await c.post("/api/turns", json={"text": "Which: x\nDays: Mon at 25:00", "answers": [{"question": 1, "value": "mon 25:00"}]})
        assert bad.status_code == 422 and "answer 2" in bad.json()["detail"]
        assert len((await c.get("/api/messages")).json()) == 2                            # nothing was saved
        ok = await c.post("/api/turns", json={"text": "Which: x\nDays: Mon, Wed at 12:00 PM",
                                              "answers": [{"question": 0, "value": "x"}, {"question": 1, "value": "wed,mon 12:00"}]})
        assert ok.status_code == 200
        await wait_for(lambda: asyncio.sleep(0, result=len(seen) >= 2))
        said = seen[-1].messages[-1]["content"]
        assert said.startswith("Which: x\nDays: Mon, Wed at 12:00 PM") and "Days = mon,wed 12:00 (weekdays_time)" in said
        assert "Which =" not in said                                                      # a choice says it in its own words
        stored_text = [m["content"] for m in (await c.get("/api/messages")).json() if m["role"] == "user"][-1]
        assert stored_text == "Which: x\nDays: Mon, Wed at 12:00 PM"                       # the chat keeps the user's words only
        late = await c.post("/api/turns", json={"text": "later", "answers": [{"question": 1, "value": "nonsense"}]})
        assert late.status_code == 200                                                    # nothing open any more: the words go as they are


def test_the_choice_tool_stays_general():
    _, b = book()
    desc = next(t for t in b.tools() if t.name == "quick_replies").description.lower()
    assert "never narrow the answer space" in desc
    for word in ("lotto", "lottery", "schedule", "monday", "saturday", "buy", "site"):
        assert word not in desc, word
