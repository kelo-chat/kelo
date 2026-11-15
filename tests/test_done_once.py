"""A step that sends, changes or deletes is done once (done_once.py): the fingerprint and the result reader (pure, tables), the policy over
the ledger (a done repeat inside one run blocked unless the owner asked for it more than once, a new request from the owner runs, the model
redoing it on its own is blocked, an unknown one blocked until it is checked, a recurring schedule's next run allowed), the whole path through the real loop with a fake model, the follow-up for an unattended run that ends unknown, and the API."""

import asyncio
import json

import httpx
import pytest

from assistant import effects
from assistant.agent import Tool, ToolResult, run_agent
from assistant.agent.fence import data
from assistant.agent.loop import Ours, ToolOutput
from assistant.done_once import (ActionFingerprint, ActionLedger, DoneOnce, DuplicateRule, Outcome, PageStep, OwnerAsk, RepeatWords, ResultReader)
from assistant.effects import Effect
from assistant.permissions import Gate
from assistant.providers import Done, TextDelta, ToolCall
from assistant.store import Store

from test_followups import Writer, make as make_followups
from test_permissions import fake_tools, perms
from test_server import AUTH, Running

NOW = 1_800_000_000.0
HOUR = 3600.0
MAIL = {"to": ["Kim@X.org"], "subject": "Lunch", "body": "Are you free  at noon?"}


def fp_of(tool, args, **kw):
    return ActionFingerprint.of(effects.effect_of(tool, args, **kw), tool, args, kw.pop("page", None))


def mail_fp(**over):
    args = {**MAIL, **over}
    return ActionFingerprint.of(effects.effect_of("email_send", args), "email_send", args)


# ---------------------------------------------------------------------------------------------------------------- the fingerprint
def test_the_same_mail_worded_differently_is_one_fingerprint():
    a = {"to": ["kim@x.org", "lee@x.org"], "cc": [], "subject": "Lunch", "body": "Are you free at noon?"}
    b = {"body": "  Are you   free at\nnoon? ", "subject": "Lunch ", "to": ["LEE@x.org", "Kim@x.org"], "account": ""}
    assert ActionFingerprint.of(effects.effect_of("email_send", a), "email_send", a) == ActionFingerprint.of(effects.effect_of("email_send", b), "email_send", b)


def test_another_recipient_subject_body_or_attachment_is_another_fingerprint():
    base = mail_fp()
    assert len({base, mail_fp(to=["lee@x.org"]), mail_fp(subject="Dinner"), mail_fp(body="Are you free at one?"), mail_fp(cc=["a@b.org"])}) == 5
    with_file = mail_fp(attachments=[{"name": "plan.pdf"}])
    assert with_file != base and with_file == mail_fp(attachments=["/tmp/x/plan.pdf"])     # attachments count by name
    assert with_file != mail_fp(attachments=["other.pdf"])
    assert mail_fp(body="Hello") != mail_fp(body="hello")                                  # case is kept


def test_a_browser_step_counts_by_site_class_page_path_and_the_words():
    def step(tool="browser_click", site="shop.example", commit="send", path="/contact", label="Send message", **args):
        e = effects.effect_of(tool, args, site=site, commit=commit)
        return ActionFingerprint.of(e, tool, args, PageStep(commit, path, label))
    base = step()
    assert base == step(selector="e12") == step(selector="e99", label="Send  message")     # the ref is volatile; spaces do not count
    assert len({base, step(site="other.example"), step(commit="form"), step(path="/other"), step(label="Send copy")}) == 5
    typed = step(tool="browser_type", commit="form", text="Hello  there", selector="e3")
    assert typed == step(tool="browser_type", commit="form", text="Hello there", selector="e7", label="x")
    assert typed != step(tool="browser_type", commit="form", text="Hello there!")


def test_mcp_arguments_are_compared_by_content_not_by_order_or_spaces():
    def call(**kw):
        args = {"name": "notion", "tool": "create_page", "arguments": {"title": "Plan", "tags": ["b", "a"], "meta": {"x": 1, "y": 2}}, **kw}
        return ActionFingerprint.of(effects.effect_of("mcp_call_tool", args), "mcp_call_tool", args)
    base = call()
    assert base == call(arguments={"meta": {"y": 2, "x": 1}, "tags": ["b", "a"], "title": " Plan "}) == call(arguments={"title": "Plan", "tags": ["b", "a"], "meta": {"x": 1, "y": 2}, "request_id": "r-1"})
    assert base != call(arguments={"title": "Plan B", "tags": ["b", "a"], "meta": {"x": 1, "y": 2}}) and base != call(tool="update_page")


def test_a_service_call_ignores_headers_and_the_order_of_the_query():
    def send(url, **kw):
        args = {"name": "slack", "method": "POST", "url": url, "json": {"text": "hi"}, **kw}
        return ActionFingerprint.of(effects.effect_of("connector_send", args), "connector_send", args)
    assert send("https://api.example.com/v1/post?a=1&b=2") == send("https://API.example.com/v1/post/?b=2&a=1", headers={"X-Req": "9"})
    assert send("https://api.example.com/v1/post") != send("https://api.example.com/v1/other")


# ---------------------------------------------------------------------------------------------------------------- the result reader
R = ResultReader()
TIMEOUT = asyncio.TimeoutError()


class HTTPTimeout(Exception):   # shaped like httpx.ReadTimeout: the class name says it
    pass


HTTPTimeout.__name__ = "ReadTimeout"


class WithStatus(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


class BrowserPolicyError(Exception):
    pass


@pytest.mark.parametrize("tool,result,expected", [
    # mail
    ("email_send", "sent to kim@x.org", Outcome("done", "")),
    ("email_send", "error: the mail server could not be reached (TimeoutError); nothing was repeated", Outcome("unknown")),
    ("email_send", "error: the mail server could not be reached (ConnectionResetError); nothing was repeated", Outcome("unknown")),
    ("email_send", "error: every recipient must be a plain email address", Outcome("failed")),
    ("email_send", "error: the mail server refused the sign-in for sending; ask the user to connect the mailbox again", Outcome("failed")),
    ("email_send", "error: could not send (SMTPRecipientsRefused)", Outcome("failed")),
    ("email_send", "error: something odd happened", Outcome("unknown")),                       # not clear: it may have gone out
    # a service connected with a key
    ("connector_send", "HTTP 201 · application/json\n" + data('{"id": "msg_42", "ok": true}'), Outcome("done", "msg_42")),
    ("connector_send", "HTTP 200 · text/plain\n" + data("ok"), Outcome("done", "")),
    ("connector_send", "HTTP 422 · application/json\n" + data('{"error": "bad"}'), Outcome("failed")),
    ("connector_send", "HTTP 404 · text/html\n" + data("nope"), Outcome("failed")),
    ("connector_send", "HTTP 408 · text/plain\n" + data("slow"), Outcome("unknown")),
    ("connector_send", "HTTP 429 · text/plain\n" + data("slow down"), Outcome("unknown")),
    ("connector_send", "HTTP 503 · text/html\n" + data("maintenance"), Outcome("unknown")),
    ("connector_send", Ours("error: the host is not registered for this connection"), Outcome("failed")),
    ("connector_send", "HTTP 200 · text/plain\n" + data("a body that says error: HTTP 500"), Outcome("done", "")),   # the service's words are data
    # MCP
    ("mcp_call_tool", data('{"content": [{"type": "text", "text": "{\\"id\\": \\"pg_9\\"}"}], "isError": false}'), Outcome("done", "pg_9")),
    ("mcp_call_tool", "(the tool reported an error)\n" + data('{"content": []}'), Outcome("failed")),
    ("mcp_call_tool", "error: " + data("HTTP 502 from the server"), Outcome("unknown")),
    ("mcp_call_tool", "error: " + data("HTTP 400 bad arguments"), Outcome("failed")),
    # the phone, calendars, apps
    ("phone_send_text", "Text message sent to 01012345678.", Outcome("done", "")),
    ("phone_send_text", "Error: the phone did not respond", Outcome("unknown")),
    ("gcal_create_event", "Created the event (id: ev8f3k2a1).", Outcome("done", "ev8f3k2a1")),
    ("gcal_delete_event", "Error: event not found", Outcome("failed")),
    # the browser
    ("browser_click", "Thanks! Your order number: KR-20481 is confirmed.", Outcome("done", "KR-20481")),
    ("browser_click", "주문번호: 2026110912 주문이 완료되었습니다", Outcome("done", "2026110912")),
    ("browser_click", "Message sent. Back to the inbox.", Outcome("done", "")),
    ("browser_click", "Email is required. Please enter your email.", Outcome("failed")),
    ("browser_click", "[The click went through, but nothing changed: same address, same page content]", Outcome("failed")),
    ("browser_click", "Error: navigation timeout while the page loaded", Outcome("unknown")),
])
def test_the_result_reader_by_tool_kind(tool, result, expected):
    assert R.of(tool, result) == expected


@pytest.mark.parametrize("exc,expected", [
    (TIMEOUT, "unknown"), (TimeoutError("x"), "unknown"), (HTTPTimeout("read"), "unknown"), (ConnectionResetError("reset"), "unknown"),
    (WithStatus(500), "unknown"), (WithStatus(408), "unknown"), (WithStatus(404), "failed"), (WithStatus(400), "failed"),
    (ValueError("bad"), "failed"), (KeyError("x"), "failed"), (BrowserPolicyError("not allowed"), "failed"),
    (RuntimeError("who knows"), "unknown"),
])
def test_the_result_reader_on_exceptions(exc, expected):
    assert R.of("email_send", exc).state == expected


def test_the_reader_reads_a_tool_output_and_keeps_the_record_detail_short_and_clean():
    assert R.of("connector_send", ToolOutput(text="HTTP 204")).state == "done"
    key = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    line = R.detail(RuntimeError(f"failed with {key} " + "x" * 400))
    assert len(line) <= 200 and key not in line and line.startswith("RuntimeError")
    assert R.detail("error: first line\nsecond") == "error: first line"


# ---------------------------------------------------------------------------------------------------------------- the owner's words
@pytest.mark.parametrize("request_text,asked", [
    ("두 번 보내줘", True), ("메일 두 번 보내", True), ("send it twice", True), ("send the mail 3 times to Kim", True), ("메일 3번 보내", True),
    ("다시 보내줘", True), ("send it again", True), ("Please resend the mail", True), ("send the mail once more to Kim", True),
    ("send the mail to Kim", False), ("check the weather again", False), ("다시 한 번 날씨 알려줘", False), ("", False),
])
def test_repeat_words_next_to_the_action(request_text, asked):
    e = Effect("send", "gmail", ["kim@x.org"], "hi", "email_send")
    assert RepeatWords().asked({"source": "chat", "unattended": False, "request": request_text}, e) is asked


def test_only_the_owners_own_chat_turn_can_ask_for_more_than_once():
    e = Effect("send", "gmail", ["kim@x.org"], "hi", "email_send")
    words = "send it twice"
    assert RepeatWords().asked({"source": "api", "request": words}, e)
    assert not RepeatWords().asked({"source": "schedule:3", "unattended": True, "request": words}, e)
    assert not RepeatWords().asked({"source": "chat", "unattended": True, "request": words}, e)
    assert not RepeatWords().asked({"source": "group:telegram:1", "request": words}, e)
    assert not RepeatWords().asked(None, e)


@pytest.mark.parametrize("kind,request_text,asked", [
    ("send", "보내줘", True), ("send", "김에게 답장해줘", True), ("send", "메일 전송", True), ("send", "send it", True), ("send", "Reply to Kim", True),
    ("send", "post this", True), ("send", "mail Kim", True), ("send", "text Kim", True), ("send", "she sends it", True),
    ("send", "잘 갔어?", False), ("send", "did it go?", False), ("send", "what is the weather", False), ("send", "context", False),
    ("change", "불 켜줘", True), ("change", "에어컨 꺼줘", True), ("change", "문 잠가", True), ("change", "온도 올려", True), ("change", "블라인드 내려줘", True),
    ("change", "창문 열어", True), ("change", "알람 바꿔줘", True), ("change", "turn on the light", True), ("change", "set a timer", True),
    ("change", "open the door", True), ("change", "lock it", True), ("change", "add an event", True), ("change", "move it to 5", True),
    ("change", "update the event", True), ("change", "is it on?", False), ("change", "불 어때?", False),
    ("delete", "지워줘", True), ("delete", "삭제해", True), ("delete", "delete it", True), ("delete", "remove the event", True),
    ("delete", "보내줘", False), ("send", "지워줘", False), ("change", "delete it", False), ("send", "", False),
])
def test_the_owners_words_ask_for_a_kind_of_action(kind, request_text, asked):
    e = Effect(kind, "gmail", ["kim@x.org"], "hi", "email_send")
    assert OwnerAsk().asked({"source": "chat", "unattended": False, "request": request_text}, e) is asked


def test_only_the_owners_own_chat_or_api_turn_has_words_that_ask():
    e = Effect("send", "gmail", ["kim@x.org"], "hi", "email_send")
    assert OwnerAsk().asked({"source": "chat", "request": "보내줘"}, e) and OwnerAsk().asked({"source": "api", "request": "send it"}, e)
    assert not OwnerAsk().asked({"source": "schedule:3", "unattended": True, "request": "send it"}, e)
    assert not OwnerAsk().asked({"source": "chat", "unattended": True, "request": "send it"}, e)
    assert not OwnerAsk().asked({"source": "group:telegram:1", "request": "send it"}, e)
    assert not OwnerAsk().asked({"source": "background", "request": "send it"}, e)
    assert not OwnerAsk().asked(None, e)


# ---------------------------------------------------------------------------------------------------------------- the policy
class World:
    """A DoneOnce over a real ledger with a clock and a turn we can change."""

    def __init__(self, **kw):
        self.store = Store()
        self.t = NOW
        self.turn = {"task_id": 1, "turn_id": "t1", "source": "chat", "unattended": False, "request": "mail Kim"}
        self.ledger = ActionLedger(self.store, clock=lambda: self.t)
        self.told = []
        self.once = DoneOnce(self.ledger, effect=lambda tool, args: effects.effect_of(tool, args), turn=lambda: self.turn, clock=lambda: self.t,
                             tz=lambda: "Asia/Seoul", on_unresolved=self.told.append, **kw)

    def new_turn(self, **over):
        self.turn = {**self.turn, "task_id": self.turn["task_id"] + 1, "turn_id": f"t{self.turn['task_id'] + 1}", **over}

    async def run(self, tool, args, result="sent to kim@x.org", approved=None):
        """What loop.py does around a handler: returns (text the model gets, the attempt)."""
        att = await self.once.begin(Tool(tool, tool, {}, None), args, approved=approved)
        if att is None:
            return result, None
        if att.block:
            return att.block, att
        return att.settle(result, str(result)), att


async def test_a_retry_after_success_in_the_same_turn_is_blocked_with_the_reason():
    w = World()
    text, att = await w.run("email_send", MAIL)
    assert text == "sent to kim@x.org" and att.record_id and not att.block
    text, att = await w.run("email_send", {**MAIL, "body": "Are you free at   noon?"})      # worded a little differently
    assert att.block and text.startswith("Not run: the same email send on kim@x.org already went out at ")
    assert "(id 1)" in text and "approval" not in text
    assert len(w.ledger.list(1)) == 1                                                      # a block records nothing
    text, _ = await w.run("email_send", MAIL, approved=True)
    assert text.endswith("The owner's approval of the card does not override this.")        # the card was approved, the block says so


async def test_a_new_request_to_send_runs_again_with_a_note_for_the_model():
    w = World()
    await w.run("email_send", MAIL)
    w.new_turn(request="보내줘")
    text, att = await w.run("email_send", MAIL)
    assert not att.block and att.record_id == 2
    assert text.startswith("sent to kim@x.org") and text.endswith(
        f"Note: the same step was done at {w.once._when(NOW)} (id 1); it ran again because the owner asked for it in this message.")
    assert [r.state for r in w.ledger.list(1)] == ["done", "done"]
    w.new_turn(request="send it again")                                                      # an API turn counts as the owner too
    w.turn["source"] = "api"
    assert not (await w.run("email_send", MAIL))[1].block


async def test_the_morning_and_the_evening_light_both_run():
    w = World()
    light = {"entity": "light.living_room", "state": "on"}
    morning = await w.run("connector_send", {"name": "ha", "method": "POST", "url": "https://ha.local/api/services/light/turn_on", "json": light}, result="HTTP 200 ok")
    w.t += 11 * HOUR
    w.new_turn(request="불 켜줘")
    evening = await w.run("connector_send", {"name": "ha", "method": "POST", "url": "https://ha.local/api/services/light/turn_on", "json": light}, result="HTTP 200 ok")
    assert morning[1].record_id == 1 and not morning[1].block
    assert not evening[1].block and evening[1].record_id == 2 and "it ran again because the owner asked for it" in evening[0]
    assert [r.state for r in w.ledger.list(1)] == ["done", "done"]


async def test_a_new_turn_that_does_not_ask_for_it_is_blocked_when_the_model_redoes_it():
    w = World()
    await w.run("email_send", MAIL)
    w.new_turn(request="잘 갔어?")                                                           # "did it go?": the model re-sends on its own
    text, att = await w.run("email_send", MAIL)
    assert att.block and text.startswith("Not run: the same email send on kim@x.org already went out at ") and "(id 1)" in text
    assert len(w.ledger.list(1)) == 1
    w.new_turn(request="did it go through?")
    assert (await w.run("email_send", MAIL))[1].block


async def test_the_owner_who_asks_for_it_twice_gets_both_in_one_turn():
    w = World()
    w.turn["request"] = "두 번 보내"
    first = await w.run("email_send", MAIL)
    second = await w.run("email_send", MAIL)
    assert not first[1].block and not second[1].block and second[0] == "sent to kim@x.org"       # no note: it is the same message's own count
    w.turn["request"] = "send it once"
    other = {**MAIL, "subject": "Dinner"}
    await w.run("email_send", other)
    assert (await w.run("email_send", other))[1].block


async def test_a_change_that_sets_a_state_follows_the_same_rules():
    w = World()
    event = {"id": "e1", "summary": "Lunch", "start": "2026-11-14T12:00"}
    await w.run("gcal_update_event", event)
    assert (await w.run("gcal_update_event", event))[1].block                                  # the same turn: a retry
    w.new_turn(request="일정 바꿔줘")
    assert not (await w.run("gcal_update_event", event))[1].block                              # asked for: runs
    w.new_turn(request="일정 어때?")
    assert (await w.run("gcal_update_event", event))[1].block                                  # not asked for: blocked


async def test_an_unknown_result_blocks_until_it_is_checked_and_not_done_lets_a_retry_run():
    w = World()
    text, att = await w.run("email_send", MAIL, result=TIMEOUT)
    assert "The result is unknown (id 1)" in text and "action_result(1, done|not_done)" in text and "Do not repeat it before you know" in text
    assert w.ledger.get(1).state == "unknown" and "TimeoutError" in w.ledger.get(1).detail
    text, att = await w.run("email_send", MAIL)
    assert att.block and "has an unknown result (id 1)" in text and "email_list" in text and "action_result(1, done|not_done)" in text
    w.new_turn(request="send it again")                                                      # even the owner's "again" does not skip an unknown
    assert (await w.run("email_send", MAIL))[1].block
    tool = w.once.tools()[0]
    assert (await tool.handler({"id": 1, "outcome": "not_done"})).startswith("Noted: record 1 did not happen")
    rec = w.ledger.get(1)
    assert (rec.state, rec.detail) == ("failed", "checked: not sent") and rec.checked_at == w.t
    text, att = await w.run("email_send", MAIL)
    assert not att.block and text == "sent to kim@x.org"


async def test_action_result_done_keeps_the_step_blocked_and_only_marks_an_unknown_record():
    w = World()
    await w.run("email_send", MAIL, result=TIMEOUT)
    tool = w.once.tools()[0]
    assert (await tool.handler({"id": 1, "outcome": "done", "outside_id": "<m1@x>"})).startswith("Noted: record 1 is done")
    rec = w.ledger.get(1)
    assert (rec.state, rec.outside_id) == ("done", "<m1@x>")
    w.new_turn(request="잘 갔어?")
    assert (await w.run("email_send", MAIL))[1].block                                        # now a known done: the model may not redo it
    assert (await tool.handler({"id": 1, "outcome": "not_done"})).startswith("Error: record 1 is already done")
    assert (await tool.handler({"id": 99, "outcome": "done"})).startswith("Error: no record 99")
    assert (await tool.handler({"id": 1, "outcome": "maybe"})).startswith("Error: outcome is")
    assert (await tool.handler({"id": "x", "outcome": "done"})).startswith("Error: id is")


async def test_the_tool_is_offered_only_while_a_result_is_unknown():
    w = World()
    assert w.once.hides("action_result") and not w.once.hides("email_send") and w.once.version == 0
    await w.run("email_send", MAIL, result=TIMEOUT)
    assert not w.once.hides("action_result") and w.once.version == 1
    await w.once.tools()[0].handler({"id": 1, "outcome": "done"})
    assert w.once.hides("action_result")
    await w.run("email_send", {**MAIL, "to": ["lee@x.org"]}, result=TIMEOUT)
    w.t += 25 * HOUR                                                                       # past the day the step is blocked for
    assert w.once.hides("action_result")


async def test_a_recurring_schedules_next_run_may_repeat_but_its_own_run_may_not():
    w = World()
    w.turn = {"task_id": 10, "turn_id": "a", "source": "schedule:4", "unattended": True, "request": ""}
    await w.run("email_send", MAIL)
    assert (await w.run("email_send", MAIL))[1].block                                       # the same run: never twice
    w.new_turn()                                                                           # the next run of the same schedule
    text, att = await w.run("email_send", MAIL)
    assert not att.block and "ran again because the owner asked" not in text                # no owner words here: no note either
    w.new_turn(source="schedule:9")                                                        # another schedule, or a plain background task
    assert (await w.run("email_send", MAIL))[1].block
    w.new_turn(source="background")
    assert (await w.run("email_send", MAIL))[1].block
    w.new_turn(source="chat", unattended=False, request="잘 갔어?")                         # a chat turn that does not ask for it
    assert (await w.run("email_send", MAIL))[1].block


async def test_a_background_run_cannot_use_asking_words_in_its_own_prompt():
    w = World()
    await w.run("email_send", MAIL)
    w.new_turn(source="background", unattended=True, request="send it again")
    assert (await w.run("email_send", MAIL))[1].block
    w.new_turn(source="chat", unattended=True, request="보내줘")                              # an unattended chat run has no owner words either
    assert (await w.run("email_send", MAIL))[1].block


async def test_a_failed_step_never_blocks_and_a_done_one_stops_blocking_after_a_day():
    w = World()
    text, _ = await w.run("email_send", MAIL, result="error: every recipient must be a plain email address")
    assert w.ledger.get(1).state == "failed" and "unknown" not in text
    assert not (await w.run("email_send", MAIL, result="sent to kim@x.org"))[1].block
    assert (await w.run("email_send", MAIL))[1].block
    w.t += 24 * HOUR + 60
    assert not (await w.run("email_send", MAIL))[1].block


async def test_reads_and_other_tools_are_never_recorded():
    w = World()
    for tool, args in [("email_list", {"folder": "Sent"}), ("email_read", {"uid": "1"}), ("gcal_events", {}), ("web_search", {"q": "x"}),
                       ("read_file", {"path": "a"}), ("connector_get", {"name": "slack", "url": "https://x"}), ("mcp_call_tool", {"name": "n", "tool": "list_pages"})]:
        assert (await w.run(tool, args, result="ok"))[1] is None, tool
    assert w.ledger.list(1) == []


async def test_the_purchase_tools_and_the_pay_click_are_left_alone():
    async def step(tool, args):
        return ("pay", "shop.example")
    w = World(browser_step=step, browser_page=lambda tool, args: ("/checkout", "Pay now"))
    for tool, args in [("confirm_purchase", {"site": "shop.example"}), ("purchase_result", {"order_no": "A1"}), ("begin_order", {}),
                       ("browser_click", {"selector": "e5"})]:
        assert (await w.run(tool, args, result="ok"))[1] is None, tool
    assert w.ledger.list(1) == []


async def test_browser_steps_are_recorded_by_class_and_typing_without_submit_is_not_a_send():
    async def step(tool, args):
        return ("send", "shop.example")
    w = World(browser_step=step, browser_page=lambda tool, args: ("/contact", "Send message"))
    text, att = await w.run("browser_click", {"selector": "e12"}, result="Thanks! Your request number: RQ-7781 was received.")
    assert w.ledger.get(1).state == "done" and w.ledger.get(1).outside_id == "RQ-7781" and w.ledger.get(1).targets == ("site:shop.example",)
    assert (await w.run("browser_click", {"selector": "e31"}))[1].block                     # the same button on the same page: the ref is volatile
    assert (await w.run("browser_type", {"selector": "e3", "text": "hello"}))[1] is None     # typing alone sends nothing
    typed = await w.run("browser_type", {"selector": "e3", "text": "hello", "submit": True}, result="Message sent.")
    assert typed[1].record_id and not typed[1].block


async def test_the_ledger_lists_by_days_keeps_thirty_and_shows_no_bodies():
    w = World()
    await w.run("email_send", MAIL)
    row = w.ledger.list(7)[0].public()
    assert set(row) == {"id", "ts", "run_id", "task_id", "turn_id", "source", "tool", "kind", "connector", "targets", "state", "outside_id", "detail", "checked_at"}
    assert row["targets"] == ["kim@x.org"] and row["state"] == "done" and "noon" not in json.dumps(row)
    w.t += 20 * 86400
    assert len(w.ledger.list(30)) == 1 and w.ledger.list(7) == []
    w.t += 11 * 86400
    w.ledger.prune()
    assert w.ledger.list(30) == []


def test_the_duplicate_rule_alone():
    e = Effect("send", "gmail", ["kim@x.org"], "hi", "email_send")
    rule = DuplicateRule()
    assert rule.verdict(e, [], {}) is None
    from assistant.done_once import Record
    mk = lambda i, state, source="chat", run="1": Record(i, NOW, run, 1, "t", source, "email_send", "send", "gmail", ("kim@x.org",), "f", state, "", "", None)
    assert rule.verdict(e, [mk(3, "failed")], {}) is None
    assert rule.verdict(e, [mk(3, "failed"), mk(2, "done")], {"source": "chat", "task_id": 1})[0] == "done"                       # same run
    assert rule.verdict(e, [mk(3, "done")], {"source": "chat", "task_id": 1, "request": "send it twice"}) is None
    assert rule.verdict(e, [mk(3, "done")], {"source": "chat", "task_id": 2, "request": "send it"})[0] == "again"               # an earlier run, asked for
    assert rule.verdict(e, [mk(3, "done")], {"source": "chat", "task_id": 2, "request": "did it go?"})[0] == "done"             # ... not asked for
    assert rule.verdict(e, [mk(3, "done")], {"source": "background", "unattended": True, "task_id": 2, "request": "send it"})[0] == "done"
    assert rule.verdict(e, [mk(3, "done", source="schedule:4")], {"source": "schedule:4", "unattended": True, "task_id": 2}) is None
    assert rule.verdict(e, [mk(3, "done"), mk(2, "unknown")], {"source": "chat", "request": "send it twice"})[0] == "unknown"


# ---------------------------------------------------------------------------------------------------------------- through the real loop
class Script:
    """A fake model: one scripted call per step, and a note of the tool names it was offered each time."""

    def __init__(self, *calls):
        self.calls, self.offered, self.results = list(calls), [], []

    async def __call__(self, provider, request, **kw):
        self.offered.append([t["function"]["name"] for t in request.tools])
        if request.messages[-1].get("role") == "tool":
            self.results.append(request.messages[-1]["content"])
        if not self.calls:
            yield TextDelta("done")
            yield Done("stop")
            return
        name, args = self.calls.pop(0)
        yield ToolCall(f"c{len(self.offered)}", name, json.dumps(args))
        yield Done("tool_calls")


def loop_world(*, first):
    """email_send fails once with `first`, then works; email_list and the done-once tool; Gate over the real permissions."""
    w = World()
    sent, listed = [], []

    async def email_send(args):
        sent.append(args)
        if len(sent) == 1 and first is not None:
            if isinstance(first, BaseException):
                raise first
            return first
        return "sent to " + ", ".join(args["to"])

    async def email_list(args):
        listed.append(args)
        return "1 message in Sent: 'Lunch' to kim@x.org"

    _, p = perms()
    tools = [Tool("email_send", "send", {"type": "object"}, email_send, requires_approval=True),
             Tool("email_list", "list", {"type": "object"}, email_list, read_only=True), *w.once.tools()]
    return w, tools, Gate(p, once=w.once), sent, listed


async def drive(w, tools, gate, script):
    async def yes(req):
        return True
    return [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "mail Kim"}], tools, approve=yes, stream_fn=script, gate=gate,
                                         done_once=w.once, max_steps=12) if isinstance(ev, ToolResult)]


async def test_a_timeout_then_a_retry_is_blocked_and_the_model_is_told_to_check():
    w, tools, gate, sent, listed = loop_world(first=asyncio.TimeoutError())
    script = Script(("email_send", MAIL), ("email_send", MAIL))
    res = await drive(w, tools, gate, script)
    assert len(sent) == 1                                                                 # the second call never reached the handler
    assert res[0].is_error and "The result is unknown (id 1)" in res[0].output and "TimeoutError" in res[0].output
    assert res[1].is_error and "has an unknown result (id 1)" in res[1].output and "email_list" in res[1].output
    assert "action_result" not in script.offered[0] and "action_result" in script.offered[1]   # offered only once something is unknown


async def test_checking_then_action_result_done_sends_nothing_twice():
    w, tools, gate, sent, listed = loop_world(first="error: the mail server could not be reached (TimeoutError); nothing was repeated")
    script = Script(("email_send", MAIL), ("email_list", {"folder": "Sent"}), ("action_result", {"id": 1, "outcome": "done"}), ("email_send", MAIL))
    res = await drive(w, tools, gate, script)
    assert len(sent) == 1 and len(listed) == 1
    assert "The result is unknown (id 1)" in res[0].output and not res[0].is_error     # the tool answered with a text; the line is added
    assert res[2].output.startswith("Noted: record 1 is done") and w.ledger.get(1).state == "done"
    assert res[3].is_error and "already went out" in res[3].output                         # a known done: blocked
    assert "action_result" not in script.offered[-1]                                      # settled: the tool goes away again


async def test_not_done_lets_the_retry_through_the_loop():
    w, tools, gate, sent, listed = loop_world(first=asyncio.TimeoutError())
    script = Script(("email_send", MAIL), ("email_list", {"folder": "Sent"}), ("action_result", {"id": 1, "outcome": "not_done"}), ("email_send", MAIL))
    res = await drive(w, tools, gate, script)
    assert len(sent) == 2 and res[3].output == "sent to Kim@X.org" and not res[3].is_error
    assert [r.state for r in reversed(w.ledger.list(1))] == ["failed", "done"]


async def test_a_card_the_owner_approved_does_not_override_a_duplicate():
    w, tools, gate, sent, _ = loop_world(first=None)
    asked = []

    async def yes(req):
        asked.append(req.name)
        return True
    script = Script(("email_send", MAIL), ("email_send", MAIL))
    res = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "mail Kim"}], tools, approve=yes, stream_fn=script, gate=gate,
                                        done_once=w.once) if isinstance(ev, ToolResult)]
    assert len(sent) == 1 and asked == ["email_send", "email_send"]                        # the card came first, then the block
    assert res[1].is_error and res[1].output.endswith("does not override this.")


async def test_the_loop_without_done_once_is_as_before():
    w, tools, gate, sent, _ = loop_world(first=None)
    res = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "x"}], tools, approve=lambda r: asyncio.sleep(0, True),
                                        stream_fn=Script(("email_send", MAIL), ("email_send", MAIL))) if isinstance(ev, ToolResult)]
    assert len(sent) == 2 and not any(r.is_error for r in res)


# ---------------------------------------------------------------------------------------------------------------- the follow-up
async def test_an_unattended_run_that_ends_unknown_gets_one_follow_up_once():
    w = World()
    f, store, clock, sent, writer = make_followups(writer=Writer(fail=True), action_state=w.ledger.state_of)
    w.once.on_unresolved = f.unknown_result
    w.turn = {"task_id": 21, "turn_id": "u1", "source": "schedule:5", "unattended": True, "request": ""}
    await w.run("email_send", MAIL, result=TIMEOUT)
    await w.run("email_send", {**MAIL, "to": ["lee@x.org"]})                                  # a known one: nothing to say
    assert w.once.run_ended({"task_id": 99, "turn_id": "x", "unattended": True}) == 0          # another run
    assert w.once.run_ended({"task_id": 21, "turn_id": "u1", "unattended": False}) == 0        # a watched run tells the owner itself
    assert w.once.run_ended({"task_id": 21, "turn_id": "u1", "unattended": True}) == 1
    assert w.once.run_ended({"task_id": 21, "turn_id": "u1", "unattended": True}) == 1         # (told again: the item is the same one)
    items = f.items()
    assert len(items) == 1 and items[0]["kind"] == "unknown_result" and items[0]["schedule_id"] == 5 and items[0]["task_id"] == 21
    assert items[0]["next_at"] == clock.t and {a["kind"] for a in items[0]["actions"]} == {"snooze", "drop"}
    assert await f.tick() == 1
    text = sent[0][1]
    assert text == f"Kelo did a mail send on kim@x.org at {f._when(NOW)} but could not confirm it went through; please check."
    assert f.get(items[0]["id"])["stage"] == "on_hold" and f.get(items[0]["id"])["next_at"] is None
    clock.t += 10 * HOUR
    assert await f.tick() == 0 and len(sent) == 1                                              # at most once


async def test_the_follow_up_waits_for_waking_hours_and_closes_when_the_record_is_settled():
    from test_followups import ts
    w = World()
    f, store, clock, sent, writer = make_followups(t0=ts(2026, 11, 7, 23, 30), action_state=w.ledger.state_of)
    w.once.on_unresolved = f.unknown_result
    w.turn = {"task_id": 21, "turn_id": "u1", "source": "background", "unattended": True, "request": ""}
    await w.run("email_send", MAIL, result=TIMEOUT)
    w.once.run_ended({"task_id": 21, "turn_id": "u1", "unattended": True})
    assert await f.tick() == 0 and f.items()[0]["next_at"] == ts(2026, 11, 8, 7, 0)             # the night: nothing goes out
    await w.once.tools()[0].handler({"id": 1, "outcome": "done"})                               # checked meanwhile
    clock.t = ts(2026, 11, 8, 7, 0)
    assert await f.tick() == 0 and f.items() == [] and sent == []


def test_the_follow_up_text_is_in_korean_too(monkeypatch):
    from assistant import i18n
    from assistant.followups import TEMPLATES
    ko = json.loads((__import__("pathlib").Path(i18n.__file__).parent / "locales" / "ko.json").read_text(encoding="utf-8"))
    assert TEMPLATES["unknown_result"] in ko and ko["a mail send"] and ko["a step on a site"]


# ---------------------------------------------------------------------------------------------------------------- the API
async def test_the_server_wires_done_once_into_the_gate_the_tool_list_and_the_api(tmp_path):
    from assistant.agent.toolbox import CORE_TOOLS
    from test_prompt import talk_app
    app = talk_app(tmp_path, [])
    once = app.state.done_once
    assert "action_result" in CORE_TOOLS and "action_result" in {t.name for t in app.state.tools}
    assert once.hides("action_result")                                                      # nothing unknown yet: the tool stays out of the list
    rid = once.ledger.record(run_id="7", task_id=7, turn_id="t", source="chat", tool="email_send", kind="send", connector="gmail",
                             targets=["kim@x.org"], fingerprint="f", state="unknown", detail="TimeoutError")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.get("/api/actions?days=7")
        assert r.status_code == 200
        [row] = r.json()["items"]
        assert row["id"] == rid and row["tool"] == "email_send" and row["state"] == "unknown" and row["targets"] == ["kim@x.org"]
        assert row["detail"] == "TimeoutError" and "body" not in row
        assert (await c.get("/api/actions?days=0")).status_code == 422 and (await c.get("/api/actions?days=31")).status_code == 422
        assert (await httpx.AsyncClient(base_url=srv.url).get("/api/actions")).status_code in (401, 403)
    assert not once.hides("action_result")                                                  # an unknown record: the tool is offered


def test_a_question_about_the_result_is_not_a_request():
    from assistant.done_once import OwnerAsk
    from assistant.effects import Effect
    ask = OwnerAsk()
    on = Effect("change", "home_assistant", ["connector:ha"], "", "x")
    send = Effect("send", "gmail", ["kim@x.org"], "hi", "email_send")
    turn = lambda text: {"source": "chat", "request": text}
    assert ask.asked(turn("불 켜줘"), on) and ask.asked(turn("거실 불 다시 켜"), on)
    assert not ask.asked(turn("불 켜졌어?"), on) and not ask.asked(turn("불 꺼졌나?"), on)
    assert ask.asked(turn("김씨한테 메일 보내줘"), send) and not ask.asked(turn("메일 전송됐어?"), send)
    assert ask.asked(turn("send it to Kim"), send) and not ask.asked(turn("did you send it?"), send)
    assert not ask.asked(turn("is the light on?"), on)
