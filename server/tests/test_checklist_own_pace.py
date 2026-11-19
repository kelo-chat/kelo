"""The checklist's own pace (owner 2026-11-19, card p-checklist-own-pace): an item that needs timing of its own gets its own job, made by
Kelo when the item is added; the rest ride the proactive send times. No 2-hourly pass. Fake model, fake jobs."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from assistant import checklist as checklist_mod
from assistant.checklist import CHECKLIST_MARK, JOB_KINDS, SETUP, Checklist, JobKind, item_key, setup_text
from assistant.home import Home
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running

ROOT = Path(__file__).resolve().parents[2]


class Jobs:
    """Fake schedules: id -> name; remove() records what was removed."""

    def __init__(self, **names):
        self.names = {int(k[1:]): v for k, v in names.items()}   # s7="Before events" -> 7
        self.removed = []

    def kind(self) -> JobKind:
        return JobKind(get=lambda i: {"name": self.names[i], "description": "Every 15 minutes", "on": True} if i in self.names else None,
                       remove=self.removed.append)


def make(tmp_path, jobs=None):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    ck = Checklist(store, Home(tmp_path / "home"), None)
    if jobs is not None:
        ck.jobs = {"schedule": jobs.kind()}
    news = []
    ck.on_new = lambda: news.append(1)
    return ck, news


def test_an_item_is_the_same_whatever_its_case_and_spacing():
    assert item_key("  Tell me   when X ") == item_key("tell me when x") != item_key("tell me when y")


def test_the_owners_new_items_wait_for_their_setup_and_the_agents_own_do_not(tmp_path):
    ck, news = make(tmp_path)
    ck.update(items=["Look over A"])                                                  # the app: the owner's words
    assert ck.state_of("Look over A") == "new" and news == [1]
    ck.home.write("CHECKLIST.md", ck.text() + "- Look over B\n", by="agent")        # the agent in the chat links its own items itself
    assert ck.state_of("Look over B") == "send_times" and news == [1]
    assert ck.take_pending() == (["Look over A"], []) and ck.state_of("Look over A") == "asked"
    assert ck.take_pending() == ([], [])
    assert ck.send_time_items() == ["Look over A", "Look over B"]                     # not linked yet: the send times look at them


def test_items_from_before_this_version_are_set_up_once(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    (root / "CHECKLIST.md").write_text("# CHECKLIST.md\n\n- Look over A\n", encoding="utf-8")
    store = Store()
    ck = Checklist(store, Home(root), None)
    assert ck.state_of("Look over A") == "old" and ck.take_pending() == ([], ["Look over A"])
    again = Checklist(store, Home(root), None)                                        # the next start: nothing new
    assert again.take_pending() == ([], [])


def test_link_records_the_job_and_the_send_times_leave_it_out(tmp_path):
    jobs = Jobs(s7="Before events")
    ck, _ = make(tmp_path, jobs)
    ck.update(items=["Look over A", "Look over B"])
    assert ck.link("Look over A", "schedule:7").startswith("Linked")
    assert ck.link("look over  b", "").startswith("Linked")                          # case and spacing do not matter
    missing = ck.link("Look over C", "")
    assert missing.startswith("error") and "Look over A" in missing
    assert ck.link("Look over B", "schedule:8").startswith("error") and ck.link("Look over B", "nothing:1").startswith("error")
    assert ck.entries() == [
        {"text": "Look over A", "state": "own", "job": {"kind": "schedule", "id": 7, "name": "Before events", "description": "Every 15 minutes", "on": True}},
        {"text": "Look over B", "state": "send_times", "job": None}]
    assert ck.send_time_items() == ["Look over B"] and ck.public()["entries"] == ck.entries()
    jobs.names.clear()                                                                 # the owner deleted the job: back to the send times
    assert ck.send_time_items() == ["Look over A", "Look over B"]


def test_the_owner_taking_an_item_off_takes_its_job_along_and_the_agent_does_not(tmp_path):
    jobs = Jobs(s7="Before events", s9="Shared")
    ck, _ = make(tmp_path, jobs)
    ck.update(items=["A", "B", "C", "D"])
    ck.link("A", "schedule:7")
    ck.link("B", "schedule:9")
    ck.link("C", "schedule:9")
    ck.update(items=["B", "C", "D"])
    assert jobs.removed == [7] and ck.state_of("A") == ""
    ck.update(items=["C", "D"])
    assert jobs.removed == [7]                                                         # another item still uses that job
    ck.update(items=["D"])
    assert jobs.removed == [7, 9]
    ck.link("D", "schedule:7")
    ck.home.write("CHECKLIST.md", "# CHECKLIST.md\n", by="agent")
    assert jobs.removed == [7, 9] and ck.state_of("D") == ""                           # the agent took it off: the job stays


def test_the_setup_message_is_one_general_principle():
    text = setup_text(["Look over A"], "12:00, 16:00, 20:00", added=True)
    assert text.startswith(CHECKLIST_MARK) and "- Look over A" in text and "12:00, 16:00, 20:00" in text and "just added" in text
    assert "checklist_link" in text and "NO_REPLY" in text and "already keeps" in setup_text(["x"], "", added=False)
    for word in ("calendar", "mail", "weather", "parcel", "minutes before", "30"):
        assert word not in SETUP.lower()


def test_there_is_no_checklist_pace_any_more():
    src = (ROOT / "server" / "src" / "assistant" / "server.py").read_text(encoding="utf-8")
    assert 'add_every("checklist"' not in src and not hasattr(Checklist, "tick")


# ---------------------------------------------------------------------------------------------------------- the setup turn over HTTP
def setup_model(*steps):
    """Answers the checklist hand-off: one tool call per step ((name, args)), then the last step's text; plain "ok" otherwise."""
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request)
        msgs = request.messages
        at = [i for i, m in enumerate(msgs) if isinstance(m.get("content"), str) and m["content"].startswith(CHECKLIST_MARK)]
        if not at:
            yield TextDelta("ok")
            yield Done("stop")
            return
        done = sum(1 for m in msgs[at[-1]:] if m["role"] == "tool")
        if done < len(steps) - 1:
            name, args = steps[done]
            yield ToolCall(f"c{done}", name, json.dumps(args))
            yield Done("tool_calls")
        else:
            yield TextDelta(steps[-1])
            yield Done("stop")
    fn.seen = seen
    return fn


def app_for(tmp_path, fn):
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False)
    return create_app(s, tools=[], stream_fn=fn, with_schedule_tools=True, with_checklist_setup=True)


async def wait_for(fn, tries=300):
    for _ in range(tries):
        if (v := await fn()):
            return v
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


def test_the_server_knows_every_kind_of_job_an_item_can_have(tmp_path):
    app = app_for(tmp_path, setup_model("ok"))
    assert set(app.state.checklist.jobs) == set(JOB_KINDS)
    assert "checklist_link" in {t.name for t in app.state.tools}


async def test_an_item_added_in_the_app_rides_the_send_times_when_kelo_judges_so(tmp_path, monkeypatch):
    monkeypatch.setattr(checklist_mod, "SETUP_DELAY", 0.0)
    fn = setup_model(("checklist_link", {"item": "Look over A", "job": ""}), "NO_REPLY")
    app = app_for(tmp_path, fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        got = (await c.put("/api/checklist", json={"items": ["Look over A"]})).json()
        assert got["entries"] == [{"text": "Look over A", "state": "send_times", "job": None}]
        await wait_for(lambda: asyncio.sleep(0, result=app.state.checklist.state_of("Look over A") == "send_times"))
        first = next(r for r in fn.seen if any(isinstance(m.get("content"), str) and m["content"].startswith(CHECKLIST_MARK) for m in r.messages))
        handed = next(m["content"] for m in first.messages if isinstance(m.get("content"), str) and m["content"].startswith(CHECKLIST_MARK))
        assert "- Look over A" in handed and "just added" in handed
        assert "checklist_link" in [t["function"]["name"] for t in first.tools]               # shown up front, no search first
        await asyncio.sleep(0.2)
        assert (await c.get("/api/messages")).json() == []                                   # the hand-off is hidden, the answer quiet


async def test_an_item_that_needs_its_own_timing_gets_its_job_without_a_card_and_loses_it_with_the_item(tmp_path, monkeypatch):
    monkeypatch.setattr(checklist_mod, "SETUP_DELAY", 0.0)
    fn = setup_model(("schedule_create", {"name": "Own check", "prompt": "Look at it; say something only when it needs the owner.",
                                          "every_minutes": 30}),
                     ("checklist_link", {"item": "Look over B", "job": "schedule:1"}),
                     "I set up a check every 30 minutes for it.")
    app = app_for(tmp_path, fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        await c.put("/api/checklist", json={"items": ["Look over B"]})

        async def linked():
            e = (await c.get("/api/checklist")).json()["entries"]
            return e if e and e[0]["state"] == "own" else None
        e = await wait_for(linked)                                                            # a card would have stopped it here
        assert e[0]["job"]["kind"] == "schedule" and e[0]["job"]["id"] == 1 and e[0]["job"]["name"] == "Own check"
        s = (await c.get("/api/schedules")).json()["items"]
        assert [x["name"] for x in s] == ["Own check"] and s[0]["created_by"] == "agent"

        async def told():
            ms = (await c.get("/api/messages")).json()
            return ms if ms and ms[-1]["role"] == "assistant" else None
        ms = await wait_for(told)
        assert [m["role"] for m in ms] == ["assistant"] and ms[-1]["content"] == "I set up a check every 30 minutes for it."
        assert (await c.put("/api/checklist", json={"items": []})).json()["entries"] == []
        assert (await c.get("/api/schedules")).json()["items"] == []                         # the owner took the item off: its job went too
