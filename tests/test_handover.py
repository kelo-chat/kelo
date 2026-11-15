"""A human check handed to the user: when a browser step of a chat reply, a background job or a scheduled run finds a bot check on its
tab that the agent could not do in its own tries (at most 2; most tests here set them to 0 to look at the hand-over itself), the step
waits and a pending item of kind human_check is raised (the chat's live card, the Work page, one notification). It ends
by itself when the check leaves the page (re-read every few seconds and when the tab is handed back), when the user skips the site,
when it runs out, or when the history is cleared; the agent is told how it ended. Fake browser page whose probe flips; fake model."""

import asyncio

import httpx
import pytest

from assistant import server as server_mod
from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.tool_labels import ASKS, ask
from assistant.tools.browser import BrowserSession
from assistant.tools.challenge import handover_note

from test_agent_profile import app_settings, approval_tool
from test_browser_more import CheckPage, probe
from test_server import AUTH, Running, follow
from test_workers import Brain, call, delegating, one_worker, say, tool_results, until

CHECK = probe(title="Just a moment...")


def browsing(poll=0.02, tries=0):
    page = CheckPage(CHECK)

    async def connect():
        return page

    sess = BrowserSession(connect=connect, resolver=lambda h, p, *a, **k: [(2, 1, 6, "", ("93.184.216.34", p))])
    sess.check_poll = poll
    sess.check_tries = tries          # 0: handed over at the first reading (the hand-over itself is what these tests look at)

    async def read(args):
        return await sess.snapshot()

    return page, sess, Tool("browser_read", "read the page", {"type": "object"}, read, untrusted=True)


def scripted(*steps):
    """A fake model: each call gives the next step (the last again when it runs out); records the requests."""
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request)
        for ev in steps[min(len(seen) - 1, len(steps) - 1)]:
            yield ev
    fn.seen = seen
    return fn


READ_THEN_ANSWER = (call("browser_read", cid="r1"), say("다 봤어요."))


async def pending(c):
    return (await c.get("/api/approvals/pending")).json()


async def waiting_item(c):
    async def look():
        items = (await pending(c))["items"]
        return items[0] if items else None
    return await until(look)


def last_tool_output(fn):
    return [m["content"] for m in fn.seen[-1].messages if m.get("role") == "tool"][-1]


# ------------------------------------------------------------------------------------------------ a chat reply
async def test_a_check_pauses_the_step_raises_one_item_and_ends_by_itself_when_it_is_gone(tmp_path):
    page, sess, read = browsing()
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "디즈니랜드 입장권 최저가"})).json()["id"]
        item = await waiting_item(c)
        assert item["kind"] == "human_check" and item["site"] == "shop.example.com" and item["tab"] == "owner"
        assert item["check"]["vendor"] == "cloudflare" and item["source"] == "chat" and item["turn_id"] == tid
        assert "shop.example.com" in item["detail"] and "arguments" not in item and item["can_remember"] is False
        status = (await c.get("/api/agent/status")).json()
        assert status["pending_approvals"] == 1 and status["busy"]
        notes = [n for n in (await c.get("/api/notifications")).json()["items"] if n["approval_id"] == item["approval_id"]]
        assert len(notes) == 1 and notes[0]["type"] == "approval_waiting" and not notes[0]["seen"]    # one notification, not two
        assert not any(n["type"] == "browser_check" for n in (await c.get("/api/notifications")).json()["items"])
        await asyncio.sleep(0.1)
        assert (await c.get(f"/api/turns/{tid}")).json()["done"] is False     # still waiting while the check is there

        page.probe_value = probe()                                           # the user solved it
        events = await follow(c, tid)
        out = last_tool_output(fn)
        assert "is gone" in out and "Welcome" in out and "tries left" not in out
        checks = [e for e in events if e["type"] == "human_check"]
        assert [e["state"] for e in checks] == ["waiting", "solved"] and checks[0]["tab"] == "owner"
        assert not any(e["type"] == "approval_request" for e in events)      # the card is the pending item, shown once
        got = await pending(c)
        assert got["items"] == [] and got["resolved"][0]["outcome"] == "solved" and got["resolved"][0]["approval_id"] == item["approval_id"]
        assert (await c.get("/api/agent/status")).json()["pending_approvals"] == 0    # handled: the apps' red badge goes away
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["tool"] == "browser_check" and hist[0]["decision"] == "solved"
        assert all(n["seen"] for n in (await c.get("/api/notifications")).json()["items"] if n["approval_id"] == item["approval_id"])


async def test_skip_moves_on_without_the_site_and_the_same_site_is_not_handed_over_again(tmp_path):
    page, sess, read = browsing()
    fn = scripted(call("browser_read", cid="r1"), call("browser_read", cid="r2"), say("다른 곳에서 찾았어요."))
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"]
        item = await waiting_item(c)
        r = (await c.post(f"/api/approvals/{item['approval_id']}", json={"approve": False})).json()
        assert r["decision"] == "skipped"
        await follow(c, tid)
        outs = [m["content"] for m in fn.seen[-1].messages if m.get("role") == "tool"]
        assert "chose to skip shop.example.com" in outs[0] and "Do not open shop.example.com again" in outs[0]
        assert "already skipped" in outs[1]                                   # no second card, no loop on the blocked page
        got = await pending(c)
        assert got["items"] == [] and [x["outcome"] for x in got["resolved"]] == ["skipped"]
        assert (await c.post(f"/api/approvals/{item['approval_id']}", json={"approve": False})).status_code == 404   # answered elsewhere


async def test_nobody_solves_it_in_time(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "HANDOVER_SECONDS", 0.3)
    page, sess, read = browsing()
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"])
        out = last_tool_output(fn)
        assert "Nobody solved the human check on shop.example.com" in out and "could not be used" in out
        got = await pending(c)
        assert got["items"] == [] and got["resolved"][0]["outcome"] == "expired"
        assert (await c.get("/api/approvals/history")).json()["items"][0]["decision"] == "expired"


async def test_handing_the_tab_back_looks_again_at_once(tmp_path):
    page, sess, read = browsing(poll=60)                                      # no timed look would come in time
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"]
        await waiting_item(c)
        sess.take_control("owner", "phone-1")                                  # "Solve it": the live view with control taken
        page.probe_value = probe()
        await asyncio.sleep(0.1)
        assert (await pending(c))["items"]                                     # solved, but the user still holds the tab
        await sess.release_control("owner")
        await asyncio.wait_for(follow(c, tid), 5)
        assert "is gone" in last_tool_output(fn)


async def test_an_older_apps_allow_means_done_and_a_failed_reading_is_never_gone(tmp_path):
    page, sess, read = browsing()
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"]
        item = await waiting_item(c)
        page.probe_value = None                                                # the page is changing: no reading
        await asyncio.sleep(0.15)
        assert (await pending(c))["items"]
        page.probe_value = CHECK
        assert (await c.post(f"/api/approvals/{item['approval_id']}", json={"approve": True})).json()["decision"] == "solved"
        await follow(c, tid)
        out = last_tool_output(fn)
        assert "is gone" in out and "[Human check on the page" in out          # the check is still there: the plain rule again


async def test_clearing_the_history_ends_a_waiting_hand_over(tmp_path):
    page, sess, read = browsing()
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path, scheduler=False), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"]
        await waiting_item(c)
        out = (await c.delete("/api/messages")).json()
        assert out["stopped"]["turns"] >= 1
        got = await pending(c)
        assert got == {"items": [], "resolved": []}
        assert (await c.get(f"/api/turns/{tid}")).status_code == 404
        assert (await c.get("/api/approvals/history")).json()["items"] == []
        assert (await c.get("/api/notifications")).json()["items"] == []
        assert (await c.get("/api/agent/status")).json()["busy"] is False


async def test_a_waiting_chat_reply_is_not_stopped_for_want_of_a_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "TURN_IDLE_SECONDS", 0.05)
    page, sess, read = browsing()
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"]
        await waiting_item(c)
        await asyncio.sleep(0.2)                                               # the phone left (nobody asks for the reply)
        app.state.sweep_turns()
        await asyncio.sleep(0.05)
        assert (await pending(c))["items"] and (await c.get("/api/agent/status")).json()["busy"]
        page.probe_value = probe()
        events = await follow(c, tid)
        assert events[-1]["type"] == "done"


# ------------------------------------------------------------------------------------------------ a background job and a scheduled run
async def test_a_background_job_waits_in_place_and_goes_on_by_itself_once_solved(tmp_path):
    page, sess, read = browsing()

    def worker(req, n):
        outs = tool_results(req)
        if not outs:
            return call("browser_read", cid="w1")
        return call("report", {"status": "done", "summary": "최저가는 1,000원이에요." if "is gone" in outs[-1] else "실패"})

    brain = Brain(chat=delegating({"task": "입장권 최저가 찾아줘", "kind": "general"}), worker=worker)
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=brain, browser=sess, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "입장권 최저가 찾아줘"})).json()["id"])
        item = await waiting_item(c)
        job = await one_worker(c, "running")                                  # not needs_input: it waits in its step
        assert item["source"] == "worker" and item["worker_id"] == job["id"] and item["unattended"] is True
        assert item["tab"] == job["tab"] and item["tab"].startswith("task:")
        assert item["expires_at"] <= job["deadline_at"] - server_mod.HANDOVER_JOB_MARGIN + 1   # the job's own deadline stays the bound
        page.probe_value = probe()
        done = await one_worker(c, "done")
        assert "1,000원" in (await c.get(f"/api/workers/{done['id']}")).json()["report"]["summary"]
        assert len(brain.calls["worker"]) == 2                                # one step, then the report: no restart of the job


async def test_a_jobs_hand_over_ends_before_its_deadline(tmp_path):
    page, sess, read = browsing()

    def worker(req, n):
        outs = tool_results(req)
        return call("browser_read", cid="w1") if not outs else call("report", {"status": "done", "summary": "건너뛰었어요."})

    brain = Brain(chat=delegating({"task": "찾아줘", "kind": "general", "deadline_min": 3}), worker=worker)
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=brain, browser=sess, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"])
        item = await waiting_item(c)
        left = item["expires_at"] - item["asked_at"]
        assert 55 <= left <= 3 * 60 - server_mod.HANDOVER_JOB_MARGIN + 2
        await c.post(f"/api/approvals/{item['approval_id']}", json={"approve": False})
        await one_worker(c, "done")


async def test_a_scheduled_run_waits_the_unattended_time(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "HANDOVER_UNATTENDED_SECONDS", 0.3)
    page, sess, read = browsing()
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "가격 확인", "prompt": "확인해줘", "cron": "0 9 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        await follow(c, run["turn_id"])
        assert "Nobody solved" in last_tool_output(fn)
        assert (await pending(c))["resolved"][0]["outcome"] == "expired"


# ------------------------------------------------------------------------------------------------ notes, and the approval titles
@pytest.mark.parametrize("outcome,words", [("solved", "is gone"), ("skipped", "chose to skip"), ("repeat", "already skipped"),
                                           ("expired", "within 10 minutes"), ("closed", "was closed"), ("cancelled", "not solved")])
def test_every_ending_tells_the_agent_not_to_try_again(outcome, words):
    note = handover_note(outcome, "shop.example.com", 10)
    assert words in note
    if outcome not in ("solved", "done"):
        assert "do not try the check again yourself" in note and "say in your answer" in note


# ------------------------------------------------------------------------------------------------ the agent's own tries come first
async def test_the_agent_tries_twice_before_the_check_is_handed_over(tmp_path):
    page, sess, read = browsing(tries=2)
    fn = scripted(call("browser_read", cid="r1"), call("browser_read", cid="r2"), call("browser_read", cid="r3"), say("다 봤어요."))
    app = create_app(app_settings(tmp_path), tools=[read], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"]
        item = await waiting_item(c)                                          # raised only at the third reading
        outs = [m["content"] for m in fn.seen[-1].messages if m.get("role") == "tool"]
        assert len(outs) == 2 and "2 of 2 tries left" in outs[0] and "1 of 2 tries left" in outs[1]
        assert item["kind"] == "human_check" and item["site"] == "shop.example.com"
        page.probe_value = probe()
        await follow(c, tid)
        assert "is gone" in last_tool_output(fn)


async def test_a_check_the_agent_gets_past_itself_raises_nothing(tmp_path):
    page, sess, read = browsing(tries=2)

    async def solve_and_read(args):                                          # the agent's try worked: the check is gone
        page.probe_value = probe()
        return await sess.snapshot()

    fn = scripted(call("browser_read", cid="r1"), call("browser_try", cid="t1"), say("됐어요."))
    tried = Tool("browser_try", "try", {"type": "object"}, solve_and_read, untrusted=True)
    app = create_app(app_settings(tmp_path), tools=[read, tried], stream_fn=fn, browser=sess)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        events = await follow(c, (await c.post("/api/turns", json={"text": "찾아줘"})).json()["id"])
        assert "The human check is gone" in last_tool_output(fn)
        assert not any(e["type"] in ("human_check", "browser_check") for e in events)
        assert (await pending(c)) == {"items": [], "resolved": []}


async def test_an_approval_carries_its_question_as_a_whole_sentence(tmp_path):
    fn = scripted(call("browser_click", {"selector": "#next"}), say("눌렀어요."))
    app = create_app(app_settings(tmp_path), tools=[approval_tool("browser_click")], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        tid = (await c.post("/api/turns", json={"text": "다음"})).json()["id"]
        item = await waiting_item(c)
        assert item["ask"] == "페이지에서 이걸 누를까요?"
        await c.post(f"/api/approvals/{item['approval_id']}", json={"approve": True})
        events = await follow(c, tid)
        assert next(e for e in events if e["type"] == "approval_request")["ask"] == "페이지에서 이걸 누를까요?"
    assert ASKS["browser_login"] == "Sign in?" and ask("web_search") == ""
    assert all(v.endswith("?") for v in ASKS.values())


async def test_a_sign_in_verification_is_handed_over_with_its_own_words(tmp_path):
    """tools/verification.py hands over kind "verification" (a code to type, an approval on the phone): the card, the
    notification and the status line say it is a sign-in to confirm, not a check that a person is there."""
    from assistant.agent import handover
    done = asyncio.Event()

    async def sign_in(args):
        async def gone():
            await done.wait()
            return True
        return await handover.get()({"tab": "owner", "site": "login.example.com", "vendor": "verification", "kind": "verification",
                                     "name": "Approve the sign-in on your phone", "step": "approve"}, gone)

    tool = Tool("browser_read", "read the page", {"type": "object"}, sign_in)
    fn = scripted(*READ_THEN_ANSWER)
    app = create_app(app_settings(tmp_path), tools=[tool], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "로그인해 줘"})).json()["id"]
        item = await waiting_item(c)
        assert item["label"] == "Sign-in verification" and item["ask"] == "login.example.com asks you to confirm the sign-in"
        assert item["detail"] == "Approve the sign-in on your phone" and item["check"]["kind"] == "verification"
        assert item["check"]["step"] == "approve"   # the apps make "I approved / waiting" the main action for a push approval
        status = (await c.get("/api/agent/status")).json()
        assert status["status"] == "login.example.com asks you to confirm the sign-in"
        notes = [n for n in (await c.get("/api/notifications")).json()["items"] if n["approval_id"] == item["approval_id"]]
        assert notes[0]["title"] == "login.example.com asks you to confirm the sign-in"
        assert notes[0]["body"] == "Approve the sign-in on your phone"
        ko = (await c.get("/api/approvals/pending", headers={"Accept-Language": "ko"})).json()["items"][0]
        assert ko["label"] == "Sign-in verification"   # stored once, in the language of the reply that asked
        done.set()
        events = await follow(c, tid)
        assert [e["state"] for e in events if e["type"] == "human_check"] == ["waiting", "solved"]


def test_the_sign_in_words_are_in_every_language():
    import json
    from pathlib import Path
    keys = ["Sign-in verification", "{site} asks you to confirm the sign-in", "Approve the sign-in on your phone",
            "Enter the sign-in code on the page"]
    files = sorted((Path(__file__).resolve().parents[1] / "src" / "assistant" / "locales").glob("*.json"))
    assert len(files) == 19
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        assert all(d.get(k) for k in keys), f.name
        assert "{site}" in d["{site} asks you to confirm the sign-in"], f.name
