"""The app language reaches the Claude CLI, and a stopped reply never keeps a line that only led into a tool step as its answer.

Seen on main 0.9.0+6 (2026-11-10, Sonnet through the Claude subscription): a reply's stored answer was "Direct login URL gives an
error; try the base." — English, terse, the model's note before a browser step. The reply had stopped during a hand-over; what the
conversation kept was that note. reply_text.py says what a reply keeps; turn_engine.close_cards ends the open cards of a stopped
reply for the apps. The CLI is the FakeCli stub of test_late_notes.py; nothing leaves this machine."""

import asyncio
import json
from types import SimpleNamespace as NS

import httpx

from assistant.agent import Tool
from assistant.prompt import language_line
from assistant.providers import Done, TextDelta, ToolCall
from assistant.providers import cli_agents
from assistant.reply_text import ReplyText
from assistant.server import Settings, create_app

from test_agent_profile import app_settings
from test_browser_more import probe
from test_handover import browsing, pending, scripted, waiting_item
from test_late_notes import FakeCli, directsdk
from test_server import AUTH, TOKEN, Running, follow

SCRATCH = "Direct login URL gives an error; try the base."


def step_then_read(cid="r1"):
    return [TextDelta(SCRATCH), ToolCall(cid, "browser_read", "{}"), Done("tool_calls")]


async def assistant_texts(c):
    return [m["content"] for m in (await c.get("/api/messages")).json() if m["role"] == "assistant"]


# ------------------------------------------------------------------------------------------------ what a reply keeps
def test_a_reply_that_ended_keeps_all_its_text_a_stopped_one_only_what_came_after_its_last_step():
    events = [{"type": "text", "text": "Let me look. "}, {"type": "tool_started", "name": "browser_read"},
              {"type": "tool_result", "name": "browser_read"}, {"type": "text", "text": "찾았어요: "}, {"type": "text", "text": "3만 원"}]
    stopped = ReplyText()
    for e in events:
        stopped.feed(e)
    assert stopped.text() == "찾았어요: 3만 원"      # the answer being written when it stopped
    done = ReplyText()
    for e in [*events, {"type": "done"}]:
        done.feed(e)
    assert done.text() == "Let me look. 찾았어요: 3만 원"
    only_step = ReplyText()
    for e in events[:2]:
        only_step.feed(e)
    assert not only_step and only_step.text() == ""
    plain = ReplyText()
    plain.feed({"type": "text", "text": "partial answer"})
    assert plain.text() == "partial answer"         # no tool step: what it wrote is its answer, stopped or not


# ------------------------------------------------------------------------------------------------ the Claude CLI path
async def test_the_language_line_reaches_the_claude_cli(tmp_path, monkeypatch):
    class Recording(FakeCli):
        def __init__(self):
            super().__init__()
            self.parsed = []

        def create(self, **kw):
            self.parsed.append(directsdk().prepare_history(kw["messages"]))   # what the CLI is handed (system prompt file, frames)
            return iter([NS(choices=[NS(delta=NS(content="네, 확인했어요."), finish_reason="stop")], usage=None)])

    fake = Recording()
    monkeypatch.setitem(cli_agents._clients, cli_agents.CLAUDE, fake)
    app = create_app(Settings(token=TOKEN, provider=cli_agents.CLAUDE, model="sonnet", cdp_host=None,
                              db_path=str(tmp_path / "a.db"), unofficial_providers=True), tools=[])   # the real stream_chat
    headers = {**AUTH, "Accept-Language": "ko-KR,ko;q=0.9"}
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=headers) as c:
        tid = (await c.post("/api/turns", json={"text": "로또 사줘"})).json()["id"]
        events = await follow(c, tid)
        assert events[-1]["type"] == "done", events
    assert fake.parsed, "the CLI was never called"
    system, frames = fake.parsed[0]
    sent = system + json.dumps([f["message"]["content"] for f in frames], ensure_ascii=False)
    assert language_line("ko") in sent and "Korean" in language_line("ko")
    assert "로또 사줘" in sent


# ------------------------------------------------------------------------------------------------ a reply stopped in a hand-over
async def test_a_reply_stopped_during_a_hand_over_keeps_no_step_line_and_closes_its_card(tmp_path):
    page, sess, read = browsing()
    fn = scripted(step_then_read(), [TextDelta("다 봤어요."), Done("stop")])
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "로그인해서 확인해줘"})).json()["id"]
        item = await waiting_item(c)
        assert (await c.delete(f"/api/turns/{tid}")).json() == {"ok": True}
        events = await follow(c, tid)
        assert any(e["type"] == "text" and e["text"] == SCRATCH for e in events)   # shown live, as it came
        checks = [e for e in events if e["type"] == "human_check"]
        assert [e["state"] for e in checks] == ["waiting", "cancelled"] and checks[-1]["approval_id"] == item["approval_id"]
        got = await pending(c)
        assert got["items"] == []
        assert [(r["approval_id"], r["outcome"]) for r in got["resolved"]] == [(item["approval_id"], "cancelled")]
        assert (await c.get("/api/agent/status")).json()["pending_approvals"] == 0
        assert SCRATCH not in "".join(await assistant_texts(c))                      # never the reply's answer
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["tool"] == "browser_check" and hist[0]["decision"] == "cancelled"


async def test_a_card_whose_wait_runs_outside_the_reply_is_closed_when_the_reply_ends(tmp_path):
    """The hand-over's wait in another task than the reply's (a tool that waits in a task of its own): stopping the reply does not
    cancel it, and the card still closes for the apps once, with the reply."""
    page, sess, _ = browsing()

    async def read(args):
        return await asyncio.shield(asyncio.ensure_future(sess.snapshot()))   # the wait goes on in its own task

    tool = Tool("browser_read", "read the page", {"type": "object"}, read, untrusted=True)
    fn = scripted(step_then_read(), [TextDelta("다 봤어요."), Done("stop")])
    app = create_app(app_settings(tmp_path), tools=[tool], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "확인해줘"})).json()["id"]
        item = await waiting_item(c)
        await c.delete(f"/api/turns/{tid}")
        events = await follow(c, tid)
        await asyncio.sleep(0.1)   # the hand-over's own task wakes (its card was discarded) and ends
        checks = [e for e in events if e["type"] == "human_check"]
        assert [e["state"] for e in checks] == ["waiting", "cancelled"]
        again = (await c.get(f"/api/turns/{tid}", params={"after": 0, "wait": 0})).json()["events"]
        assert [e["state"] for e in again if e["type"] == "human_check"] == ["waiting", "cancelled"]   # closed once, not twice
        got = await pending(c)
        assert got["items"] == [] and got["resolved"][0]["approval_id"] == item["approval_id"]
        assert SCRATCH not in "".join(await assistant_texts(c))


async def test_a_reply_that_finishes_keeps_its_step_lines_with_the_answer(tmp_path):
    page, sess, read = browsing()
    fn = scripted(step_then_read(), [TextDelta("다 봤어요."), Done("stop")])
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "확인해줘"})).json()["id"]
        await waiting_item(c)
        page.probe_value = probe()   # the user solved it
        events = await follow(c, tid)
        assert events[-1]["type"] == "done"
        texts = await assistant_texts(c)
        assert len(texts) == 1 and texts[0].startswith(SCRATCH) and texts[0].endswith("다 봤어요.")
