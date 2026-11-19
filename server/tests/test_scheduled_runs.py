"""Two-phase scheduled runs and the promise rule (docs/scheduled-runs.md): the working phase cannot message the owner, the closing phase
decides one outcome (code guards it by the run's tool evidence), and a reply may promise a later action only when something real will
do it. Fake models only."""

import asyncio
import json

import httpx
import pytest

from assistant import scheduled_runs as sr
from assistant.agent import Tool
from assistant.scheduled_runs import CLOSE_SYSTEM, PROMISE_SYSTEM, Closer, Decision, PromiseJudge, RunEvidence
from assistant.server import create_app

from test_server import AUTH, Running, follow
from test_watching import call, model, say, settings, until


def closing(request) -> bool:
    return request.messages[0].get("content") == CLOSE_SYSTEM


def judging(request) -> bool:
    return request.messages[0].get("content") == PROMISE_SYSTEM


# ------------------------------------------------------------------------------------------------------------------ the pieces
def test_a_decision_is_strict():
    assert sr.parse_decision({"outcome": "nothing"}) == Decision(False)
    assert sr.parse_decision({"outcome": "tell", "text": " 재고가 생겼어요 "}) == Decision(True, "재고가 생겼어요")
    for bad in ({"outcome": "tell"}, {"outcome": "tell", "text": "  "}, {"outcome": "maybe"}, {"text": "x"}, "tell", None, []):
        assert sr.parse_decision(bad) is None


def test_without_a_decision_the_report_decides():
    assert sr.from_report("NO_REPLY") == Decision(False, by="report")
    assert sr.from_report("  ") == Decision(False, by="report")
    assert sr.from_report("Found one") == Decision(True, "Found one", by="report")


def test_evidence_is_read_from_the_tool_events():
    keeper = Tool("schedule_create", "d", {}, None, keeps_promise=True)
    plain = Tool("web_fetch", "d", {}, None)
    ev = RunEvidence()
    ev.feed({"type": "tool_result", "name": "web_fetch", "is_error": False, "approved": None}, {"web_fetch": plain})
    assert ev.clean and ev.calls == 1 and not ev.kept_promise
    ev.feed({"type": "tool_result", "name": "schedule_create", "is_error": False, "approved": False}, {"schedule_create": keeper})
    assert ev.skipped == ["schedule_create"] and not ev.kept_promise and not ev.clean      # a declined card keeps nothing
    ev.feed({"type": "tool_result", "name": "web_fetch", "is_error": True, "approved": None}, {"web_fetch": plain})
    ev.feed({"type": "tool_result", "name": "web_fetch", "is_error": True, "approved": None}, {"web_fetch": plain})
    assert ev.failed == ["web_fetch"]
    ok = RunEvidence()
    ok.feed({"type": "tool_result", "name": "schedule_create", "is_error": False, "approved": True}, {"schedule_create": keeper})
    assert ok.kept_promise and ok.clean
    ok.feed({"type": "replies", "questions": []})
    assert ok.reached_owner
    job_msg = Tool("worker_message", "d", {}, None)
    sr.declare([job_msg])
    assert job_msg.keeps_promise is True            # the job's report comes back to the owner by itself


async def test_the_closer_uses_the_model_and_guards_nothing_after_a_failure():
    asked = []

    def asker(answer):
        async def ask(system, user):
            asked.append((system, json.loads(user)))
            return answer
        return ask

    clean = RunEvidence()
    assert await Closer(asker({"outcome": "nothing"})).close("n", "p", "all fine", clean) == Decision(False)
    assert (await Closer(asker({"outcome": "tell", "text": "hi"})).close("n", "p", "r", clean)).text == "hi"
    sent = asked[0][1]
    assert asked[0][0] == CLOSE_SYSTEM and sent["job_name"] == "n" and sent["report"] == "all fine" and sent["evidence"]["tool_calls"] == 0
    failed = RunEvidence(calls=2, failed=["web_fetch"])
    got = await Closer(asker({"outcome": "nothing"})).close("n", "p", "I could not load the page", failed)
    assert got == Decision(True, "I could not load the page", by="guard")                 # a failed step is never closed as nothing
    skipped = RunEvidence(calls=1, skipped=["send_email"])
    assert (await Closer(asker({"outcome": "nothing"})).close("n", "p", "NO_REPLY", skipped)).by == "guard"
    # no model / a bad answer / an exception: the report decides
    assert (await Closer(asker(None)).close("n", "p", "NO_REPLY", clean)) == Decision(False, by="report")
    assert (await Closer(asker({"outcome": "?"})).close("n", "p", "Found it", clean)) == Decision(True, "Found it", by="report")

    async def boom(system, user):
        raise RuntimeError("down")
    assert (await Closer(boom).close("n", "p", "Found it", clean)).by == "report"


def test_the_promise_answer_is_strict():
    assert sr.parse_promise({"promises_later": False, "offer": False, "quote": ""}) == ""
    assert sr.parse_promise({"promises_later": True, "offer": False, "quote": " I will tell you tomorrow. "}) == "I will tell you tomorrow."
    assert sr.parse_promise({"promises_later": True, "offer": True, "quote": "I can tell you tomorrow."}) == ""   # an offer is no promise
    for bad in ({"promises_later": True, "quote": ""}, {"promises_later": True}, {"promises_later": "yes", "quote": "x"}, {"quote": "x"},
                {"promises_later": True, "offer": "maybe", "quote": "x"}, None, "x"):
        assert sr.parse_promise(bad) is None


async def test_the_promise_judge_asks_only_about_a_real_reply(monkeypatch):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    asked = []

    async def ask(system, user):
        asked.append(user)
        return {"promises_later": True, "offer": False, "quote": "I will tell you tomorrow"}
    judge = PromiseJudge(ask)
    assert await judge.promised("I will tell you tomorrow.") == "I will tell you tomorrow"
    assert await judge.promised("") == "" and await judge.promised("NO_REPLY") == "" and len(asked) == 1

    async def none(system, user):
        return None
    assert await PromiseJudge(none).promised("later") == ""                  # no valid answer: nothing is said
    monkeypatch.setattr(sr, "PROMISE_CHECK", False)
    assert await judge.promised("I will tell you tomorrow.") == "" and len(asked) == 1


# The five lines of 2026-11-17 (a live server, messages 793-808): every one of them was an offer, a question or a statement of the state
# the reply was already in, and each got "I said ..., but nothing is set up". The texts below keep their shapes (the owner's own words are
# not kept in the repo). A judge that flags EVERYTHING is the worst case: the structure of the reply must keep these five quiet.
FALSE_ALARMS = [
    ("쿠팡 열어서 로켓와우 상품 보여줘", "쿠팡 첫 화면을 열었어요. 로켓배송 상품이 먼저 보여요. 원하시면 가격이 내려가면 알려드릴까요?"),
    ("장바구니에 담아줘", "장바구니에 담았어요. 결제는 제가 하지 않았고, 결제 단계로 넘어갈까요?"),
    ("그 페이지 닫혔어?", "아니요, 탭은 열려 있어요. 계속 열어둘게요. 다른 상품도 같이 비교해 드릴까요？"),
    ("Open the shop and add the blue one", "I added the blue one to the cart. I can do that now. Do you want me to look at shipping costs too?"),
    ("Is the page still open?", "Yes, it is open. I can check the other shop as well if you like (just say so)?"),
]


@pytest.mark.parametrize("user, reply", FALSE_ALARMS)
async def test_an_offer_or_a_question_is_never_called_a_promise(monkeypatch, user, reply):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    asked = []

    async def always_yes(system, text):   # the worst case: a judge that finds a promise in everything
        asked.append(text)
        return {"promises_later": True, "offer": False, "quote": reply}
    assert await PromiseJudge(always_yes).promised(reply, user) == "" and asked == []   # a reply that ends on a question is not even judged


def test_the_last_sentence_is_a_question():
    for q in ("Shall I?", "해드릴까요?", "해드릴까요？", "해드릴까요? 😊", "Really (are you sure?)", "**Want me to?**", "\"Done?\"", "ok?  \n"):
        assert sr.ends_with_question(q), q
    for s in ("I will tell you tomorrow.", "내일 알려드릴게요.", "Done! Want it later? No. Fine.", "", "   ", "a?b"):
        assert not sr.ends_with_question(s), s


async def test_the_judge_is_shown_what_the_user_wrote_and_what_counts_as_a_promise(monkeypatch):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    asked = []

    async def ask(system, text):
        asked.append((system, json.loads(text)))
        return {"promises_later": True, "offer": False, "quote": "I will check the price again tomorrow morning and tell you."}
    got = await PromiseJudge(ask).promised("I will check the price again tomorrow morning and tell you.", "what does it cost?")
    assert got == "I will check the price again tomorrow morning and tell you."   # a real promise with nothing set up still gets its line
    system, shown = asked[0]
    assert shown == {"user_message": "what does it cost?", "reply": "I will check the price again tomorrow morning and tell you."}
    # the prompt: a later action of the assistant, an offer is not a promise, the quote is copied from the reply
    assert "later" in system and "offer" in system and "user_message" in system and "exact words" in system


async def test_an_offer_is_no_promise_and_a_quote_that_is_not_in_the_reply_is_dropped(monkeypatch):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    reply = "The page is open. I can check it again later if you want, and I will also keep the tab open."

    async def offer(system, text):
        return {"promises_later": True, "offer": True, "quote": "I can check it again later if you want"}
    assert await PromiseJudge(offer).promised(reply) == ""                  # an offer waiting for the answer: no bubble

    async def made_up(system, text):
        return {"promises_later": True, "offer": False, "quote": "I will send you a report on Monday"}
    assert await PromiseJudge(made_up).promised(reply) == ""                # not the reply's own words: nothing is shown


async def test_a_source_note_after_the_last_sentence_does_not_hide_a_question(monkeypatch):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    asked = []

    async def ask(system, text):
        asked.append(text)
        return {"promises_later": True, "offer": False, "quote": "I will tell you tomorrow"}
    note = "Shall I look again tomorrow?\n(Source: example data service)"
    assert await PromiseJudge(ask).promised(note) == "" and asked == []     # the question is the last sentence before the note
    assert sr.ends_with_question("Shall I look again?\n*(data: example.com)*")
    assert sr.ends_with_question("Shall I look again?\n（출처: 예시）")
    real = "I will tell you tomorrow.\n(Source: example data service)"
    assert await PromiseJudge(ask).promised(real) == "I will tell you tomorrow" and len(asked) == 1
    assert not sr.ends_with_question("I will tell you tomorrow.\n(Source: example data service)")


def test_the_promise_line_quotes_the_reply_in_every_language():
    import json as _json
    from pathlib import Path

    from assistant import i18n
    assert "{quote}" in sr.PROMISE_LINE and "{what}" not in sr.PROMISE_LINE
    assert i18n.t(sr.PROMISE_LINE, lang="ko", quote="나중에 알려 드릴게요").startswith("\"나중에 알려 드릴게요\"")
    locales = Path(sr.__file__).parent / "locales"
    for f in locales.glob("*.json"):
        table = _json.loads(f.read_text(encoding="utf-8"))
        assert sr.PROMISE_LINE in table and "{quote}" in table[sr.PROMISE_LINE], f.name


def test_the_declared_tools_are_flagged():
    tools = [Tool(n, "d", {}, None) for n in ("quick_replies", "schedule_create", "web_fetch", "watch_add", "react", "delegate")]
    sr.declare(tools)
    assert {t.name for t in tools if t.reaches_owner} == {"quick_replies", "react"}
    assert {t.name for t in tools if t.keeps_promise} == {"schedule_create", "watch_add", "delegate"}


# ------------------------------------------------------------------------------------------------------------ the whole app
def owner_tool(ran):
    async def run(args):
        ran.append(args)
        return "ok"
    return Tool("quick_replies", "ask the owner", {"type": "object", "properties": {}}, run)


async def run_once(tmp_path, script, tools=()):
    fn = model(script)
    app = create_app(settings(tmp_path), tools=list(tools), stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "재고", "prompt": "재고 확인", "cron": "0 3 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        await follow(c, run["turn_id"])
        await until(lambda: _idle(c, s["id"]))
        out = {"messages": (await c.get("/api/messages")).json(), "notes": (await c.get("/api/notifications")).json()["items"],
               "runs": (await c.get(f"/api/schedules/{s['id']}/runs")).json()["items"],
               "tasks": (await c.get("/api/tasks")).json() if False else None}
    return fn, out


async def _idle(c, sid):
    return not (await c.get(f"/api/schedules/{sid}")).json()["running"]


async def test_nothing_to_tell_says_nothing_but_the_run_is_in_the_history(tmp_path):
    def script(req, n):
        if closing(req):
            return say('{"outcome": "nothing"}')
        return say("Checked: no change since yesterday.")
    fn, out = await run_once(tmp_path, script)
    assert out["messages"] == [] and out["notes"] == []
    assert out["runs"][0]["status"] == "quiet"
    assert sum(closing(r) for r in fn.calls) == 1


async def test_a_run_with_something_to_tell_says_it_once_after_the_closing_phase(tmp_path):
    def script(req, n):
        if closing(req):
            sent = json.loads(req.messages[1]["content"])
            assert sent["report"] == "Back in stock: 2 left" and sent["job_instructions"] == "재고 확인"
            return say(json.dumps({"outcome": "tell", "text": "재고가 2개 생겼어요."}, ensure_ascii=False))
        return say("Back in stock: 2 left")
    fn, out = await run_once(tmp_path, script)
    assert [m["content"].split("\n")[-1] for m in out["messages"]] == ["재고가 2개 생겼어요."]   # the report itself never reached the chat
    assert len(out["notes"]) == 1 and out["notes"][0]["type"] == "task_done"
    assert out["runs"][0]["status"] == "done"


async def test_a_failed_step_is_never_closed_as_nothing(tmp_path):
    async def broken(args):
        raise RuntimeError("site down")
    tools = [Tool("check_site", "look at the site", {"type": "object", "properties": {}}, broken)]

    def script(req, n):
        if closing(req):
            assert json.loads(req.messages[1]["content"])["evidence"]["failed_tools"] == ["check_site"]
            return say('{"outcome": "nothing"}')                      # the model is wrong: code overrules it
        if req.messages[-1].get("role") == "tool":
            return say("The site did not load, so I could not check.")
        return call("check_site")
    fn, out = await run_once(tmp_path, script, tools)
    assert [m["content"].split("\n")[-1] for m in out["messages"]] == ["The site did not load, so I could not check."]
    assert len(out["notes"]) == 1


async def test_without_a_decision_the_report_decides_as_before(tmp_path):
    def script(req, n):
        if closing(req):
            return say("not json")
        return say("NO_REPLY")
    fn, out = await run_once(tmp_path, script)
    assert out["messages"] == [] and out["notes"] == [] and out["runs"][0]["status"] == "quiet"

    def script2(req, n):
        return say("not json") if closing(req) else say("Price dropped to 9000")
    fn, out = await run_once(tmp_path, script2)
    assert [m["content"].split("\n")[-1] for m in out["messages"]] == ["Price dropped to 9000"]


async def test_the_working_phase_is_not_offered_the_tools_that_reach_the_owner(tmp_path):
    ran = []

    def script(req, n):
        if closing(req):
            return say('{"outcome": "nothing"}')
        return say("NO_REPLY")
    fn, out = await run_once(tmp_path, script, [owner_tool(ran)])
    work = [r for r in fn.calls if not closing(r)]
    assert work and all("quick_replies" not in [t["function"]["name"] for t in r.tools] for r in work)


async def test_a_chat_reply_that_promises_something_nothing_will_do_gets_one_correction(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)

    def script(req, n):
        if judging(req):
            return say('{"promises_later": true, "offer": false, "quote": "내일 알려드릴게요"}')
        return say("내일 알려드릴게요.")
    fn = model(script)
    app = create_app(settings(tmp_path), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "내일 알려줘"})).json()["id"])
        await until(lambda: _messages(c, 3))
        msgs = (await c.get("/api/messages")).json()
    assert msgs[1]["content"] == "내일 알려드릴게요." and "내일 알려드릴게요" in msgs[2]["content"] and "나중" not in msgs[1]["content"]
    assert sum(judging(r) for r in fn.calls) == 1


async def test_a_chat_reply_that_ends_on_an_offer_gets_no_correction(tmp_path, monkeypatch):
    """Owner report 2026-11-17: an offer ("shall I tell you tomorrow?") got "I said ..., but nothing is set up" under it."""
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)

    def script(req, n):
        if judging(req):
            return say('{"promises_later": true, "offer": true, "quote": "내일 알려드릴게요"}')   # a judge that would flag it
        return say("지금은 12,900원이에요. 내일 다시 확인해서 알려드릴까요?")
    fn = model(script)
    app = create_app(settings(tmp_path), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "가격 알려줘"})).json()["id"])
        await asyncio.sleep(0.5)
        msgs = (await c.get("/api/messages")).json()
    assert len(msgs) == 2 and msgs[1]["content"].endswith("알려드릴까요?") and not any(judging(r) for r in fn.calls)


async def _messages(c, n):
    return len((await c.get("/api/messages")).json()) >= n


async def test_a_reply_that_passes_the_work_to_a_running_job_keeps_its_promise(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    sent = []

    async def message(args):
        sent.append(args)
        return "sent"
    tools = [Tool("worker_message", "tell a job", {"type": "object", "properties": {}}, message)]

    def script(req, n):
        if judging(req):
            return say('{"promises_later": true, "what": "x"}')
        if req.messages[-1].get("role") == "tool":
            return say("끝나면 알려드릴게요.")
        return call("worker_message")
    fn = model(script)
    app = create_app(settings(tmp_path), tools=tools, stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "그 일 계속해줘"})).json()["id"])
        await asyncio.sleep(0.3)
        msgs = (await c.get("/api/messages")).json()
    assert sent and len(msgs) == 2 and not any(judging(r) for r in fn.calls)


async def test_a_reply_that_made_the_schedule_is_not_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    made = []

    async def create(args):
        made.append(args)
        return "saved"
    tools = [Tool("schedule_create", "save a job", {"type": "object", "properties": {}}, create)]

    def script(req, n):
        if judging(req):
            return say('{"promises_later": true, "what": "x"}')
        if req.messages[-1].get("role") == "tool":
            return say("매일 아침에 알려드릴게요.")
        return call("schedule_create")
    fn = model(script)
    app = create_app(settings(tmp_path), tools=tools, stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "매일 알려줘"})).json()["id"])
        await asyncio.sleep(0.3)
        msgs = (await c.get("/api/messages")).json()
    assert made and len(msgs) == 2 and not any(judging(r) for r in fn.calls)


async def test_the_judge_is_shown_the_reply_in_order_and_a_lead_in_followed_by_its_work_is_no_promise(monkeypatch):
    """The owner's case (2026-11-20): the lead-in "사진과 캘린더를 다시 확인할게요." was followed by the work; a judge that reads it as a promise
    must not put a correction under the reply. Also a real later promise (no work after it) still gets its line."""
    from assistant.reply_text import ReplyText
    monkeypatch.setattr(sr, "PROMISE_CHECK", True)
    r = ReplyText()
    for e in ({"type": "text", "text": "사진과 캘린더를 다시 확인할게요."},
              {"type": "tool_started", "id": "1", "name": "view_image", "arguments": "{}"},
              {"type": "tool_result", "id": "1", "name": "view_image", "output": "a photo", "is_error": False},
              {"type": "tool_started", "id": "2", "name": "gcal_create_event", "arguments": "{}"},
              {"type": "tool_result", "id": "2", "name": "gcal_create_event", "output": "Added.", "is_error": False},
              {"type": "text", "text": "정리했어요."}, {"type": "done"}):
        r.feed(e)
    reply = r.text()
    seen = []

    async def ask(system, text):
        seen.append(json.loads(text))
        return {"promises_later": True, "offer": False, "quote": "사진과 캘린더를 다시 확인할게요."}
    got = await PromiseJudge(ask).promised(reply, "정리해줘", steps=r.timeline())
    assert got == "사진과 캘린더를 다시 확인할게요." and r.lead_in_done(got)
    assert seen[0]["in_order"] and {"did" in e for e in seen[0]["in_order"]} == {True, False}
    assert "in_order" in PROMISE_SYSTEM and "not a promise" in PROMISE_SYSTEM
    later = ReplyText()
    for e in ({"type": "text", "text": "내일 알려드릴게요."}, {"type": "done"}):
        later.feed(e)
    assert not later.lead_in_done("내일 알려드릴게요.")   # no work follows it: a real promise, the line stays


def test_a_check_waits_for_an_open_card_of_the_reply():
    assert sr.promise_check_needed(0, [], "t1") is True
    assert sr.promise_check_needed(1, [], "t1") is False                                    # a card the turn is waiting on
    assert sr.promise_check_needed(0, [{"turn_id": "t1"}], "t1") is False                  # a card of this reply still pending
    assert sr.promise_check_needed(0, [{"turn_id": "other"}], "t1") is True
