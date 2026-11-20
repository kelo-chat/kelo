"""A repeat is judged on the page as it is now (done_once.py): the same control with the same words, state and part of the page is a
repeat, anything else is a new step (S1); a click that opened a pop-up stays done unless the job then declined that pop-up (S2); the
owner's own words reach a job once (S3); a job reports a forbidden required step at once (S4); a handoff says only what is new (S5);
a paused job is settled when the owner talks about it (S6). Example sites only."""

import pytest

from assistant.done_once import ActionFingerprint, PageStep
from assistant import effects


def step_fp(**kw):
    page = PageStep("send", "/p", "Apply", **kw.pop("page", {}))
    return ActionFingerprint.of(effects.effect_of("browser_click", {}, site="shop.example", commit="send"), "browser_click", {}, page)


# ------------------------------------------------------------------------------------------------------------- S1 the fingerprint
def test_the_same_button_on_a_changed_page_is_another_step():
    base = step_fp()
    assert base == step_fp()
    assert step_fp(page={"role": "button"}) != base
    assert step_fp(page={"role": "button", "state": "pressed=false"}) != step_fp(page={"role": "button", "state": "pressed=true"})
    assert step_fp(page={"scene": "a"}) != step_fp(page={"scene": "b"})
    same = {"role": "button", "state": "pressed=true", "scene": "a"}
    assert step_fp(page=same) == step_fp(page=dict(same))


def test_an_old_record_without_role_state_or_scene_keeps_its_fingerprint():
    body = ActionFingerprint._body(effects.effect_of("browser_click", {}, site="shop.example", commit="send"), "browser_click", {},
                                   PageStep("send", "/p", "Apply"))
    assert set(body) == {"commit", "path", "text"}
    assert "role" not in body and "state" not in body and "scene" not in body


def test_a_phone_step_keeps_its_old_fingerprint():
    body = ActionFingerprint._body(effects.effect_of("android_tap", {}, site="com.app", commit="send"), "android_tap", {},
                                   PageStep("send", "/x", "OK"))
    assert set(body) == {"commit", "path", "text"}


# ------------------------------------------------------------------------------------------------------------- S1 the element's state
pw = pytest.importorskip("playwright.async_api")
from assistant.tools.commits import TARGET_JS  # noqa: E402

SCENE_PAGE = """<!doctype html><html><body>
<h1>Trial</h1>
<button id="keep" aria-pressed="false" onclick="this.setAttribute('aria-pressed','true')">Keep</button>
<button id="apply">Apply</button>
<span role="button" id="likes">Likes 12</span>
<input id="note" type="text" placeholder="Note">
</body></html>"""


async def scene_of(page, selector):
    return await page.locator(selector).evaluate(TARGET_JS)


async def test_the_judged_element_reads_its_state_and_its_part_of_the_page_and_counters_do_not_change_it():
    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        try:
            page = await browser.new_page()
            await page.set_content(SCENE_PAGE)
            first = await scene_of(page, "#apply")
            assert first["state"] == "" and first["scene"]
            # another control's state is part of the scene
            await page.evaluate("() => document.getElementById('keep').setAttribute('aria-pressed', 'true')")
            second = await scene_of(page, "#apply")
            assert second["scene"] != first["scene"]
            # the element's own state
            assert (await scene_of(page, "#keep"))["state"] == "pressed=true"
            await page.evaluate("() => document.getElementById('keep').setAttribute('aria-pressed', 'false')")
            assert (await scene_of(page, "#keep"))["state"] == "pressed=false"
            assert (await scene_of(page, "#apply"))["scene"] == first["scene"]
            # a counter's number and typed text do not change the scene
            await page.evaluate("() => document.getElementById('likes').textContent = 'Likes 13'")
            await page.locator("#note").fill("hello there")
            assert (await scene_of(page, "#apply"))["scene"] == first["scene"]
        finally:
            await browser.close()


# ------------------------------------------------------------------------------------------------------------- S2 a pop-up the job declined
from assistant.done_once import (BROWSER_DONE_BLOCK, NO_CHANGE_DETAIL, OPENED_DETAIL, ResultReader, Outcome)  # noqa: E402
from assistant.tools.browser import DECLINED_NOTE, OPENED_NOTE  # noqa: E402
from assistant.tools.page_dialogs import DIALOG_ONLY_NOTE  # noqa: E402
from test_done_once import World  # noqa: E402

LABELS = {"e-apply": "Apply", "e-accept": "Accept", "e-cancel": "Cancel", "e-ok": "OK"}
COMMITS = {"e-apply": "send", "e-accept": "send"}   # the others only close or decline a pop-up


def pop_world(page):
    """A DoneOnce whose steps are these buttons on one page; `page["scene"]` is the page part around them (a changed one = a new step)."""
    async def step(tool, args):
        return COMMITS.get(str(args.get("selector")), ""), "shop.example"

    def where(tool, args):
        sel = str(args.get("selector"))
        return "/trial/7", LABELS[sel], "button", "", page["scene"]
    return World(browser_step=step, browser_page=where)


def test_the_result_reader_knows_the_opened_pop_up_and_the_cancelled_question():
    r = ResultReader()
    assert r.of("browser_click", OPENED_NOTE + "\n- dialog \"Keep it first\"") == Outcome("done", "", OPENED_DETAIL)
    assert r.of("browser_click", "- text: ok\n" + "x " + "[the click opened a pop-up on this page]").state == "done"
    assert r.of("browser_click", "Done. [data]") == Outcome("done", "")
    assert r.of("browser_click", DIALOG_ONLY_NOTE).state == "failed"
    assert r.of("browser_click", DECLINED_NOTE).state == "done"
    # the phrase quoted by the page (fenced as data) is the page's, not ours
    from assistant.agent.fence import data as fence
    assert r.of("browser_click", fence(OPENED_NOTE)) == Outcome("done", "")


async def test_a_cancelled_pop_up_is_no_change_and_the_same_click_runs_again():
    page = {"scene": "s0"}
    w = pop_world(page)
    text, att = await w.run("browser_click", {"selector": "e-apply"}, result=OPENED_NOTE)
    assert att.record_id == 1 and w.ledger.get(1).state == "done" and w.ledger.get(1).detail == OPENED_DETAIL
    text, att = await w.run("browser_click", {"selector": "e-cancel"}, result=DECLINED_NOTE)
    assert att.record_id == 0 and not att.block                                       # a plain click records nothing
    assert w.ledger.get(1).state == "failed" and w.ledger.get(1).detail == NO_CHANGE_DETAIL
    assert len(w.ledger.list(1)) == 1
    text, att = await w.run("browser_click", {"selector": "e-apply"}, result="Applied.")  # the incident: not blocked
    assert not att.block and att.record_id == 2 and text.startswith("Applied.")
    page["scene"] = "s1"                                                               # the page changed: a new step
    text, att = await w.run("browser_click", {"selector": "e-apply"}, result="Applied.")
    assert not att.block and att.record_id == 3
    text, att = await w.run("browser_click", {"selector": "e-apply"}, result="Applied.")
    assert att.block and att.block.startswith("Not run: this same click") and "browser_read" in att.block


async def test_a_success_pop_up_closed_with_ok_keeps_the_click_done_and_blocks_a_repeat():
    page = {"scene": "s0"}
    w = pop_world(page)
    await w.run("browser_click", {"selector": "e-apply"}, result=OPENED_NOTE)
    text, att = await w.run("browser_click", {"selector": "e-ok"}, result="Closed.")
    assert not att.block and w.ledger.get(1).state == "done"
    text, att = await w.run("browser_click", {"selector": "e-apply"}, result="Applied.")
    assert att.block and "opened a pop-up that was not declined" in att.block


async def test_an_accepted_pop_up_stays_done_and_a_later_decline_does_not_change_it():
    page = {"scene": "s0"}
    w = pop_world(page)
    await w.run("browser_click", {"selector": "e-apply"}, result=OPENED_NOTE)
    await w.run("browser_click", {"selector": "e-accept"}, result="Accepted.")
    assert w.once.opened == {}
    await w.run("browser_click", {"selector": "e-cancel"}, result=DECLINED_NOTE)
    assert w.ledger.get(1).state == "done"
    text, att = await w.run("browser_click", {"selector": "e-apply"}, result="Applied.")
    assert att.block


async def test_a_pop_up_left_open_or_closed_by_escape_keeps_the_click_done():
    page = {"scene": "s0"}
    w = pop_world(page)
    await w.run("browser_click", {"selector": "e-apply"}, result=OPENED_NOTE)
    assert (await w.run("browser_click", {"selector": "e-apply"}, result="Applied."))[1].block
    assert w.ledger.get(1).state == "done"


async def test_a_decline_in_a_later_run_does_not_change_the_earlier_click():
    page = {"scene": "s0"}
    w = pop_world(page)
    await w.run("browser_click", {"selector": "e-apply"}, result=OPENED_NOTE)
    w.new_turn(request="잘 갔어?")                                                       # a new message that asks for nothing
    await w.run("browser_click", {"selector": "e-cancel"}, result=DECLINED_NOTE)
    assert w.ledger.get(1).state == "done"
    text, att = await w.run("browser_click", {"selector": "e-apply"}, result="Applied.")
    assert att.block and text.startswith("Not run: this same click")


async def test_a_decline_text_inside_the_pages_own_data_does_not_count():
    from assistant.agent.fence import data as fence
    page = {"scene": "s0"}
    w = pop_world(page)
    await w.run("browser_click", {"selector": "e-apply"}, result=OPENED_NOTE)
    await w.run("browser_click", {"selector": "e-cancel"}, result="Closed. " + fence(DECLINED_NOTE))
    assert w.ledger.get(1).state == "done"


async def test_a_native_question_answered_cancel_is_no_change():
    page = {"scene": "s0"}
    w = pop_world(page)
    text, att = await w.run("browser_click", {"selector": "e-apply"}, result=DIALOG_ONLY_NOTE)
    assert w.ledger.get(1).state == "failed"
    assert not (await w.run("browser_click", {"selector": "e-apply"}, result="Applied."))[1].block


# ------------------------------------------------------------------------------------------------------------- S2 in real Chromium
from assistant.step_judge import StepVerdict  # noqa: E402
from assistant.tools.browser import DECLINED_NOTE as REAL_DECLINED, OPENED_NOTE as REAL_OPENED  # noqa: E402
import test_browser_frames_popups as frames  # noqa: E402
from test_browser_frames_popups import ctx, env_on  # noqa: E402,F401  (the Chromium fixtures)

APPLY = "https://www.ticketshop.example/apply"
SENT = "https://www.ticketshop.example/sent"
frames.PAGES[APPLY] = ("<h1>Trial</h1><button id=keep aria-pressed=false onclick=\"this.setAttribute('aria-pressed','true')\">Keep</button>"
                       "<button id=apply onclick=\"if(document.getElementById('keep').getAttribute('aria-pressed')!=='true'){document.getElementById('sheet').hidden=false}"
                       "else{document.getElementById('done').textContent='Done'}\">Apply</button>"
                       "<div id=sheet role=dialog aria-label='Keep it first' hidden><p>Keep it first</p>"
                       "<button id=no onclick=\"document.getElementById('sheet').hidden=true\">Cancel</button>"
                       "<button id=both onclick=\"document.getElementById('keep').setAttribute('aria-pressed','true');"
                       "document.getElementById('sheet').hidden=true;document.getElementById('done').textContent='Done'\">Keep and apply</button></div>"
                       "<p id=done></p>")
frames.PAGES[SENT] = ("<button id=send onclick=\"document.getElementById('ok-box').hidden=false\">Send</button>"
                      "<div id=ok-box role=dialog aria-label='Sent' hidden><p>Sent</p>"
                      "<button id=ok onclick=\"document.getElementById('ok-box').hidden=true\">OK</button></div>")


class _StepJudge:
    """A test judge: the words "apply" make a control an application; nothing else commits (test data, not product code)."""

    async def web(self, info):
        return StepVerdict(False, False, False, False, False, applies="apply" in str(info.get("text") or "").lower())


async def test_a_pop_up_declined_by_the_next_click_is_recorded_as_no_change_on_a_real_page(ctx):
    env = await env_on(ctx)
    env.session.step_judge = _StepJudge()
    run = await env.run([("browser_open", {"url": APPLY}), ("browser_click", {"selector": "#apply"}),
                         ("browser_click", {"selector": "#no"}), ("browser_click", {"selector": "#keep"}),
                         ("browser_click", {"selector": "#apply"}), ("browser_read", {})])
    from assistant.agent.fence import plain
    first, second = plain(run.result("browser_click", 0).output), plain(run.result("browser_click", 1).output)
    assert REAL_OPENED in first and REAL_DECLINED not in first
    assert REAL_DECLINED in second
    last = plain(run.result("browser_click", 3).output)
    assert REAL_OPENED not in last and "Done" in plain(run.result("browser_read").output)


async def test_a_success_notice_closed_with_ok_is_not_declined_on_a_real_page(ctx):
    env = await env_on(ctx)
    env.session.step_judge = _StepJudge()
    run = await env.run([("browser_open", {"url": SENT}), ("browser_click", {"selector": "#send"}), ("browser_click", {"selector": "#ok"})])
    from assistant.agent.fence import plain
    assert REAL_OPENED in plain(run.result("browser_click", 0).output)
    assert REAL_DECLINED not in plain(run.result("browser_click", 1).output)


# ------------------------------------------------------------------------------------------------------------- S3 the owner's "again" reaches a job
from assistant.done_once import RELAY_AGAIN_NOTE  # noqa: E402
from assistant.workers import current_turn  # noqa: E402
from test_done_once import MAIL, Judge  # noqa: E402

JOB = {"task_id": 5, "turn_id": "t5", "source": "worker", "unattended": True, "request": ""}


def _job_workers(status="blocked"):
    from test_workers import _bare_workers
    ws = _bare_workers()
    ws.store._run("INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, max_steps, deadline_at, minutes, token_cap) "
                  "VALUES('w1','browser','t','x',?,'task:w1',0,0,10,0,5,1000)", (status,))
    return ws


def _worker_message(ws):
    return next(t for t in ws.main_tools() if t.name == "worker_message")


async def test_only_the_owners_own_chat_words_are_passed_to_a_job_and_each_once(monkeypatch):
    ws = _job_workers()
    monkeypatch.setattr(ws, "resume", lambda *a, **k: (True, "resumed"))
    tool = _worker_message(ws)
    token = current_turn.set({"source": "chat", "unattended": False, "request": "press it again", "turn_id": "t1"})
    try:
        await tool.handler({"id": "w1", "text": "The owner wants the button pressed again"})
    finally:
        current_turn.reset(token)
    key, words = ws.owner_words("w1")
    assert words == "press it again"                                       # the owner's words, never the model's text
    ws.owner_words_used("w1", "other")
    assert ws.owner_words("w1") == (key, words)                            # another key does not use them
    ws.owner_words_used("w1", key)
    assert ws.owner_words("w1") is None                                    # used once
    token = current_turn.set({"source": "handoff", "unattended": True, "request": "send it again"})
    try:
        await tool.handler({"id": "w1", "text": "text from the main assistant"})
    finally:
        current_turn.reset(token)
    assert ws.owner_words("w1") is None                                    # not the owner's own turn: no words
    ws.message("w1", "x", owner_text="x")
    assert ws.owner_words("w1")[1] == "x"
    ws.message("w1", "y")                                                  # a message without the owner's words keeps none new
    assert ws.owner_words("w1")[1] == "x"


async def test_the_app_sends_the_owners_typed_words_and_a_stop_clears_them(monkeypatch):
    ws = _job_workers()
    monkeypatch.setattr(ws, "resume", lambda *a, **k: (True, "resumed"))
    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(ws.host, "emit", lambda *a, **k: nothing())
    ws.message("w1", "press again", owner_text="press again")
    assert ws.owner_words("w1")[1] == "press again"
    await ws.stop("w1")
    assert ws.owner_words("w1") is None


async def test_the_owners_again_sent_to_a_job_repeats_a_done_step_once():
    w = World()
    await w.run("email_send", MAIL)                                         # the chat sent it (task 1)
    words = {"value": ("k1", "send it again")}
    used = []
    w.once.relay, w.once.relay_used = lambda: words["value"], used.append
    w.turn = dict(JOB)
    text, att = await w.run("email_send", MAIL)
    assert not att.block and text.startswith("sent to kim@x.org")
    assert RELAY_AGAIN_NOTE.split("{")[0] in text and used == ["k1"]
    words["value"] = None                                                   # the words are used up: the next run is stopped
    w.new_turn(source="worker", unattended=True)
    assert (await w.run("email_send", MAIL))[1].block
    assert used == ["k1"]


async def test_words_that_do_not_ask_for_a_repeat_do_not_repeat_a_job_step():
    w = World()
    await w.run("email_send", MAIL)
    used = []
    w.once.relay, w.once.relay_used = lambda: ("k2", "did it go?"), used.append   # the judge says: no action asked
    w.turn = dict(JOB)
    assert (await w.run("email_send", MAIL))[1].block
    assert used == []


async def test_no_owner_words_leave_a_job_step_blocked_as_before():
    w = World()
    await w.run("email_send", MAIL)
    w.turn = dict(JOB)
    assert (await w.run("email_send", MAIL))[1].block


async def test_a_failing_relay_reads_as_no_words():
    w = World()
    await w.run("email_send", MAIL)

    def broken():
        raise RuntimeError("no")
    w.once.relay = broken
    w.turn = dict(JOB)
    assert (await w.run("email_send", MAIL))[1].block


# ------------------------------------------------------------------------------------------------------------- S4 the job brief
def test_the_job_brief_says_to_report_a_forbidden_required_step_at_once():
    from test_workers import _bare_workers
    brief = _bare_workers()._brief("w1", "buy the shirt", "browser", "https://shop.example/item", [], 10, 5)
    assert "A step the site requires before it goes on that your task forbids or does not mention is the user's choice" in brief
    assert "report needs_input at once" in brief and "try 2-3 other ways yourself" in brief


# ------------------------------------------------------------------------------------------------------------- S5 the handoff says what is new
def test_already_said_is_the_chats_own_replies_since_the_run_started():
    from assistant.workers import already_said
    msgs = [{"role": "assistant", "content": "Passed it on.", "created_at": 100}, {"role": "assistant", "content": "old", "created_at": 10},
            {"role": "user", "content": "hi", "created_at": 101}]
    assert already_said(msgs, 99) == "Passed it on."
    assert already_said(msgs, 0) == ""
    assert already_said([{"role": "assistant", "content": "x" * 900, "created_at": 100}], 99) == "x" * 800


def test_worker_message_results_ask_for_one_short_line_only():
    import asyncio
    from test_workers import _bare_workers
    ws = _bare_workers()
    tool = next(t for t in ws.main_tools() if t.name == "worker_message")
    ws.store._run("INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, max_steps, deadline_at, minutes, token_cap) "
                  "VALUES('w1','browser','t','x','running','task:w1',0,0,10,0,5,1000)")
    ws.message = lambda *a, **k: (True, "delivered")
    assert "one short line" in asyncio.run(tool.handler({"id": "w1", "text": "x"}))
    ws.message = lambda *a, **k: (True, "resumed")
    assert "promise nothing about the result" in asyncio.run(tool.handler({"id": "w1", "text": "x"}))
    ws.message = lambda *a, **k: (True, "queued")
    assert "in one short line" in asyncio.run(tool.handler({"id": "w1", "text": "x"}))


# ------------------------------------------------------------------------------------------------------------- S6 a paused job is settled
def test_a_blocked_job_without_a_wake_time_gets_a_question_id_that_answer_to_accepts(monkeypatch):
    from test_workers import _bare_workers
    ws = _bare_workers()
    ws.store._run("INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, max_steps, deadline_at, minutes, token_cap, "
                  "question_id) VALUES('w1','browser','t','x','blocked','task:w1',0,0,10,0,5,1000,'q_1')")
    calls = []
    monkeypatch.setattr(ws, "resume", lambda *a, **k: calls.append(a) or (True, "resumed"))
    assert ws.message("w1", "yes", "q_2") == (False, "wrong")
    assert ws.message("w1", "yes", "q_1") == (True, "resumed") and calls
    row = ws._row("w1")
    assert ws.public(row)["question_id"] == "q_1"
    text = ws._handoff_text(row, {"status": "blocked", "summary": "s", "details": ""}, {"kind": "user"})
    assert "worker_stop(id='w1')" in text and "answer_to='q_1'" in text


def test_a_blocked_job_with_a_wake_time_has_no_question_and_is_not_listed_for_the_owner():
    from test_workers import _bare_workers
    ws = _bare_workers()
    ws.store._run("INSERT INTO workers(id, kind, title, task, status, tab, created_at, updated_at, max_steps, deadline_at, minutes, token_cap, "
                  "wake_at) VALUES('w2','browser','t','x','blocked','task:w2',0,0,10,0,5,1000,4102444800)")
    row = ws._row("w2")
    assert "question_id" not in ws.public(row) and ws.public(row)["status"] == "waiting"
