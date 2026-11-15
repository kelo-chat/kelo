"""Reactions on the owner's messages (reactions.py): 👀 when Kelo starts on a message, one emoji (the model's `react`, else ✅) when
the work is over, ⚠️ when the reply failed or was stopped; 👀 stays while a background job or a question to the user carries the
work on. Stored with the message (survives a restart, goes with clear history), in GET /api/messages and in the reply's events.
Fake model only."""

import asyncio

import httpx
import pytest

from assistant.reactions import DONE, FAILED, READ, Reactions, emoji
from assistant.server import create_app
from assistant.store import Store

from test_agent_profile import app_settings
from test_server import AUTH, Running, follow
from test_workers import Brain, call, one_worker, say, until

GOLF = "\U0001F3CC️"                                             # 🏌️
RUNNER = "\U0001F3C3‍♂️"                               # 🏃‍♂️
FAMILY = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"  # 👨‍👩‍👧‍👦
THUMB_DARK = "\U0001F44D\U0001F3FF"                                   # 👍🏿
FLAG_KR = "\U0001F1F0\U0001F1F7"                                      # 🇰🇷
ENGLAND = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
KEYCAP = "1️⃣"


@pytest.mark.parametrize("ok", [GOLF, RUNNER, FAMILY, THUMB_DARK, FLAG_KR, ENGLAND, KEYCAP, READ, DONE, FAILED, "❤️",
                                "\U0001F600", "❤", "  \U0001F389 "])
def test_one_emoji_of_any_shape_is_accepted(ok):
    assert emoji(ok) == ok.strip()


@pytest.mark.parametrize("bad", ["", "   ", "ok", "골프", "\U0001F600\U0001F600", "\U0001F600 \U0001F389", "\U0001F600!", "??", "1",
                                 "\U0001F3FF", "\U0001F1F0", "\U0001F1F0\U0001F1F7\U0001F1FA\U0001F1F8", "\U0001F468‍",
                                 "‍\U0001F468", "\U0001F3F4\U000E0067\U000E0062", "\U0001F600" * 30, "<b>\U0001F600</b>", None])
def test_anything_but_one_emoji_is_refused(bad):
    with pytest.raises(ValueError):
        emoji(bad)


def _reactions(tmp_path):
    store = Store(tmp_path / "r.db")
    return store, Reactions(store)


def _emoji(store, mid):
    return store._all("SELECT reaction FROM messages WHERE id = ?", (mid,))[0]["reaction"]


def test_lifecycle_read_then_the_chosen_emoji_or_done_and_failure(tmp_path):
    store, r = _reactions(tmp_path)
    a, b, c = (store.add_message("user", t) for t in ("golf?", "hi", "broken"))
    assert r.read(a) == {"type": "reaction", "message_id": a, "emoji": READ} and _emoji(store, a) == READ
    assert r.finish(a, "done", GOLF) == [{"type": "reaction", "message_id": a, "emoji": GOLF}]
    r.read(b)
    assert r.finish(b, "done") == [{"type": "reaction", "message_id": b, "emoji": DONE}]   # nothing chosen: neutral ✅
    r.read(c)
    assert r.finish(c, "failed", GOLF)[0]["emoji"] == FAILED and r.finish(c, "stopped")[0]["emoji"] == FAILED
    assert store.add_message("assistant", "x") and r.finish(999, "done") == []


def test_a_question_back_keeps_read_until_a_turn_on_the_answer_finishes(tmp_path):
    store, r = _reactions(tmp_path)
    q = store.add_message("user", "book a table")
    r.read(q)
    assert r.finish(q, "done", GOLF, asked=True) == [] and _emoji(store, q) == READ
    answer = store.add_message("user", "7pm")
    r.read(answer)
    out = r.finish(answer, "done", RUNNER)
    assert {e["message_id"]: e["emoji"] for e in out} == {q: RUNNER, answer: RUNNER}


def test_handed_on_work_keeps_read_until_every_job_ends(tmp_path):
    store, r = _reactions(tmp_path)
    m = store.add_message("user", "compare prices")
    r.read(m)
    assert r.finish(m, "done", GOLF, jobs=["w1", "w2"]) == [] and _emoji(store, m) == READ
    assert r.job_ended("w1", "needs_input") == [] and _emoji(store, m) == READ       # waiting for the user is not the end
    assert r.job_ended("w1", "done") == [] and _emoji(store, m) == READ              # w2 still runs
    assert r.job_ended("w2", "done") == [{"type": "reaction", "message_id": m, "emoji": GOLF}]
    m2 = store.add_message("user", "again")
    r.read(m2)
    r.finish(m2, "done", "", jobs=["w3", "w4"])
    r.job_ended("w3", "failed")
    assert r.job_ended("w4", "done", GOLF)[0]["emoji"] == FAILED                      # one job failed: ⚠️ at the end
    m3 = store.add_message("user", "third")
    r.read(m3)
    r.finish(m3, "done", "", jobs=["w5"])
    assert r.job_ended("w5", "stopped")[0]["emoji"] == FAILED
    m4 = store.add_message("user", "fourth")
    r.read(m4)
    r.finish(m4, "done", "", jobs=["w6"])
    assert r.job_ended("w6", "done", "")[0]["emoji"] == DONE


def test_it_survives_a_restart_and_clear_history_removes_it(tmp_path):
    store, r = _reactions(tmp_path)
    m = store.add_message("user", "golf")
    r.read(m)
    r.finish(m, "done", GOLF)
    store._db.close()
    again = Store(tmp_path / "r.db")
    assert again.messages()[-1]["reaction"] == GOLF
    again.clear_messages()
    assert again.messages() == []
    n = again.add_message("user", "new")
    assert again.messages()[-1] == {**again.messages()[-1], "id": n, "reaction": ""}


def reacting(emoji_text, answer="좋아요, 골프 얘기 해요."):
    """A chat that reacts in its first step and answers in its second."""
    def chat(req, n):
        if req.messages[-1].get("role") == "tool":
            return say(answer)
        return call("react", {"emoji": emoji_text})
    return chat


async def test_a_reply_starts_with_read_and_ends_with_the_chosen_emoji_in_events_and_history(tmp_path):
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=Brain(chat=reacting(GOLF)), with_react_tool=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "주말에 골프 칠까?"})).json()["id"], reactions=True)
        mine = [m for m in (await c.get("/api/messages")).json() if m["role"] == "user"][-1]
        reacts = [e for e in events if e["type"] == "reaction"]
        assert events[0] == {"type": "reaction", "message_id": mine["id"], "emoji": READ}
        assert reacts[-1] == {"type": "reaction", "message_id": mine["id"], "emoji": GOLF}
        assert mine["reaction"] == GOLF
        hidden = next(e for e in events if e["type"] == "tool_started" and e["name"] == "react")
        assert hidden["hidden"] is True
        # a reply that never reacts ends on ✅; the agent's own messages carry none
        await follow(c, (await c.post("/api/turns", json={"text": "고마워"})).json()["id"], reactions=True)
        rows = (await c.get("/api/messages")).json()
        assert rows[-2]["reaction"] in (GOLF, DONE) and all(m["reaction"] == "" for m in rows if m["role"] == "assistant")


async def test_a_bad_emoji_is_refused_and_the_reply_falls_back_to_done(tmp_path):
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=Brain(chat=reacting("골프 ⛳")), with_react_tool=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "골프"})).json()["id"], reactions=True)
        result = next(e for e in events if e["type"] == "tool_result" and e["name"] == "react")
        assert result["output"].startswith("Error: one emoji only")
        assert [m for m in (await c.get("/api/messages")).json() if m["role"] == "user"][-1]["reaction"] == DONE


async def test_a_failed_reply_ends_on_the_warning_sign(tmp_path):
    def chat(req, n):
        raise RuntimeError("the model broke")
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=Brain(chat=chat), with_react_tool=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "hi"})).json()["id"], reactions=True)
        assert [e["emoji"] for e in events if e["type"] == "reaction"] == [FAILED]   # the model never answered: no 👀 first


async def test_read_waits_until_the_model_starts_answering(tmp_path):
    gate = asyncio.Event()

    async def chat(req, n):
        await gate.wait()   # the reply is waiting for the model
        return say("네, 여기 있어요.")
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=Brain(chat=chat), with_react_tool=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "안녕"})).json()["id"]
        await asyncio.sleep(0.2)
        rows = (await c.get("/api/messages")).json()
        assert [m for m in rows if m["role"] == "user"][-1]["reaction"] == ""          # no 👀 while it waits for the model
        gate.set()
        events = await follow(c, tid, reactions=True)
        kinds = [e["type"] for e in events]
        first_react = kinds.index("reaction")
        assert events[first_react]["emoji"] == READ
        assert first_react < kinds.index("text") and all(k not in ("text", "thinking") for k in kinds[:first_react])


async def test_a_delegated_job_keeps_read_until_it_reports_then_the_emoji_and_clear_history_drops_it(tmp_path):
    gate = asyncio.Event()

    async def worker(req, n):
        await gate.wait()
        return call("report", {"status": "done", "summary": "done"})

    def chat(req, n):
        last = req.messages[-1]
        if last.get("role") == "tool":
            return say("맡겼어요.") if "Handed off" in str(last.get("content")) else call("delegate", {"task": "세 곳 가격 비교", "kind": "general"}, cid="c-d")
        return call("react", {"emoji": RUNNER})

    brain = Brain(chat=chat, worker=worker, handoff=lambda r, n: say("비교 끝났어요."))
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=brain, with_worker_tools=True, with_react_tool=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "가격 비교해줘"})).json()["id"], reactions=True)
        assert [e["emoji"] for e in events if e["type"] == "reaction"] == [READ]   # the turn ended, the work did not
        mine = lambda rows: [m for m in rows if m["role"] == "user"][0]  # noqa: E731
        assert mine((await c.get("/api/messages")).json())["reaction"] == READ
        gate.set()
        await one_worker(c, "done")

        async def reacted():
            return mine((await c.get("/api/messages")).json())["reaction"] == RUNNER
        await until(reacted)
        assert (await c.delete("/api/messages")).json()["ok"]
        assert (await c.get("/api/messages")).json() == []
        assert app.state.workers.store._all("SELECT COUNT(*) AS n FROM messages WHERE reaction != ''")[0]["n"] == 0


async def test_a_question_back_keeps_read_until_the_answer_is_handled(tmp_path):
    def chat(req, n):
        if req.messages[-1].get("role") == "tool":
            return say("몇 시에 갈까요?") if n == 1 else say("예약했어요.")
        if n == 0:
            return call("await_reply", {"topic": "시간"})
        return call("react", {"emoji": "\U0001F37D️"})

    app = create_app(app_settings(tmp_path), tools=[], stream_fn=Brain(chat=chat), with_react_tool=True, with_followup_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        first = await follow(c, (await c.post("/api/turns", json={"text": "저녁 예약해줘"})).json()["id"], reactions=True)
        assert [e["emoji"] for e in first if e["type"] == "reaction"] == [READ]
        second = await follow(c, (await c.post("/api/turns", json={"text": "7시"})).json()["id"], reactions=True)
        users = [m for m in (await c.get("/api/messages")).json() if m["role"] == "user"]
        assert [m["reaction"] for m in users] == ["\U0001F37D️", "\U0001F37D️"]
        assert {e["message_id"] for e in second if e["type"] == "reaction" and e["emoji"] != READ} == {u["id"] for u in users}


def test_the_prompt_states_one_general_principle_and_no_cases():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "content" / "prompts" / "system.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "`react`" in ln)
    assert "one emoji that fits its topic or outcome" in line
    for word in ("golf", "running", "골프", "e.g.", "for example"):
        assert word not in line.lower()


def test_the_react_tool_is_always_loaded():
    from assistant.agent.toolbox import CORE_TOOLS
    assert "react" in CORE_TOOLS
