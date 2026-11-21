"""A reply is stored as one message per part the chat showed as its own bubble (ReplyText.parts / rows). A part ends only where a
visible tool step came between two kept segments, so the stored messages never outnumber the bubbles the apps streamed."""

from assistant import tool_labels
from assistant.reply_text import BOOKKEEPING, ReplyText
from assistant.repeats import collapse_repeat
from test_repeats import P1, P1_AGAIN, P2, P2_AGAIN
from test_reply_text import ANSWER


def said(*events, done=True):
    r = ReplyText()
    for e in events:
        if isinstance(e, str):
            e = {"type": "text", "text": e}
        elif isinstance(e, tuple):
            e = {"type": "tool_started", "name": e[0]}
        r.feed(e)
    if done:
        r.feed({"type": "done"})
    return r


PART = "Flights found: " + "a, " * 80


def test_an_opener_and_the_answer_after_a_shown_tool_step_are_two_rows():
    r = said("Let me check the timetable.", ("web_search",), "\n\nThe last train leaves at 23:40.")
    assert r.parts() == r.rows() == ["Let me check the timetable.", "The last train leaves at 23:40."]
    assert r.text() == "Let me check the timetable.\n\nThe last train leaves at 23:40."


def test_a_hidden_tool_step_never_cuts_a_row():
    assert not tool_labels.hidden("web_search")
    assert tool_labels.hidden("read_file") and "read_file" not in BOOKKEEPING
    assert tool_labels.hidden("react") and tool_labels.hidden("kelo_help")
    for name in ("read_file", "react", "kelo_help"):
        r = said("Let me check.", (name,), "\n\nThe answer.")
        assert r.rows() == [r.text()], name


def test_a_hidden_step_followed_by_a_shown_one_cuts_once():
    r = said("A text.", ("read_file",), ("web_search",), "B text.")
    assert r.rows() == ["A text.", "B text."]


def test_dropped_lead_ins_still_cut_where_their_chips_were():
    events = ["Let me look into Sapporo for you."]
    for line in ("I'll check the official timetable.", "Now the flight prices.", "Looking at hotels near the station.",
                 "Checking the weather for those dates.", "One more look at the train.", "Last, the snow report.\n"):
        events += [("browser_open",), ("browser_read",), line]
    events += [("web_search",), ANSWER]
    assert said(*events).rows() == ["Let me look into Sapporo for you.", ANSWER]


def test_a_long_middle_result_is_its_own_row():
    assert len(PART) > 160
    assert said("Checking.", ("web_search",), PART, ("browser_open",), "Done.").rows() == ["Checking.", PART.strip(), "Done."]


def test_model_breaks_stay_inside_their_row():
    r = said("Opening.<message_break/>", ("web_search",), "Checking this.<message_break/>", ("web_search",),
             "Part one.<message_break/>Part two.")
    assert r.rows() == ["Opening.", "Part one.<message_break/>Part two."]


def test_a_stopped_reply_is_one_row():
    assert said("I'll check.", ("web_search",), "Half an answ", done=False).rows() == ["Half an answ"]
    assert said("I'll check.", ("web_search",), done=False).rows() == []


def test_one_row_when_asked_not_to_split():
    r = said("Let me check the timetable.", ("web_search",), "\n\nThe last train leaves at 23:40.")
    assert r.rows(split=False) == [collapse_repeat(r.text())]


def test_a_later_part_that_restates_an_earlier_one_is_not_stored_again():
    r = said(P1 + "\n\n" + P2, ("web_search",), "\n\n" + P1_AGAIN + "\n\n" + P2_AGAIN)
    assert r.rows() == [P1 + "\n\n" + P2]


def test_no_row_holds_the_text_of_another_row():
    cases = [
        said("Let me check the timetable.", ("web_search",), "\n\nThe last train leaves at 23:40."),
        said("Let me look into Sapporo for you.", ("browser_open",), "Done looking.", ("web_search",), ANSWER),
        said("Checking.", ("web_search",), PART, ("browser_open",), "Done."),
        said("Opening.<message_break/>", ("web_search",), "Checking this.<message_break/>", ("web_search",),
             "Part one.<message_break/>Part two."),
    ]
    for r in cases:
        rows = r.rows()
        for i, a in enumerate(rows):
            for j, b in enumerate(rows):
                if i != j:
                    assert a not in b, (a, b)
    sapporo = said("Let me look into Sapporo for you.", ("browser_open",), "Checking more.", ("web_search",), ANSWER)
    assert "".join(sapporo.rows()).count("Let me look into Sapporo for you.") == 1


def test_one_part_is_exactly_the_old_text():
    for events in (["Hello ", "there.\n\nSecond paragraph."], ["Hi.", ("react",), " Bye."],
                   ["A", ("react",), "B " + "x" * 200], ["Short answer."]):
        r = said(*events)
        assert r.rows() == [collapse_repeat(r.text())], events


def test_the_hidden_steps_used_here_are_hidden_and_not_bookkeeping():
    assert tool_labels.hidden("react") and "react" in BOOKKEEPING


# ---------------------------------------------------------------------------------------------------------- the stored messages (S2)
import asyncio
import json
import sqlite3

import httpx

from assistant.agent import Tool
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from test_server import AUTH, Running, follow, scripted, settings

OPENER = "Let me check that again before I answer."
ANSWER = "It is correct: the flight leaves on Friday at 15:50."


async def lookup_handler(args):
    return "found 5"


LOOKUP = Tool("lookup", "look something up", {"type": "object"}, lookup_handler)


def stored(tmp_path):
    """(content, steps, via, call_id) of the assistant rows, read from the database file."""
    con = sqlite3.connect(tmp_path / "a.db")
    try:
        return con.execute("SELECT content, steps, via, call_id FROM messages WHERE role='assistant' ORDER BY id").fetchall()
    finally:
        con.close()


async def test_a_reloaded_conversation_has_the_opener_and_the_answer_as_two_messages_with_no_repeated_text(tmp_path):
    fn = scripted([TextDelta(OPENER), ToolCall("c1", "lookup", "{}"), Done("tool_calls")], [TextDelta(ANSWER), Done("stop")])
    async with Running(create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False), tools=[LOOKUP], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "q"})).json()["id"]
        await follow(c, tid)
        ms = (await c.get("/api/messages")).json()
        contents = [m["content"] for m in ms if m["role"] == "assistant"]
        assert contents == [OPENER, ANSWER]
        assert "".join(contents).count(OPENER) == 1
        assert [(row[0], row[1]) for row in stored(tmp_path)] == [(OPENER, ""), (ANSWER, "lookup -> found 5")]
        async with httpx.AsyncClient(base_url=srv.url, headers=AUTH) as other_device:
            other = [m["content"] for m in (await other_device.get(f"{srv.url}/api/messages")).json() if m["role"] == "assistant"]
        assert other == [OPENER, ANSWER]


async def test_the_stored_text_never_holds_the_opener_twice(tmp_path):
    fn = scripted([TextDelta(OPENER), ToolCall("c1", "lookup", "{}"), Done("tool_calls")],
                  [TextDelta(OPENER + "\n\n" + ANSWER), Done("stop")])
    async with Running(create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False), tools=[LOOKUP], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "q"})).json()["id"]
        events = await follow(c, tid)
    assert [content for content, *_ in stored(tmp_path)] == [OPENER, ANSWER]
    assert "".join(e["text"] for e in events if e["type"] == "text").count(OPENER) == 1


async def test_a_call_reply_stays_one_message(tmp_path):
    fn = scripted([TextDelta(OPENER), ToolCall("c1", "lookup", "{}"), Done("tool_calls")], [TextDelta(ANSWER), Done("stop")])
    async with Running(create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False), tools=[LOOKUP], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        tid = (await c.post("/api/turns", json={"text": "q", "call": "c0ffee12abcd", "can_call": True})).json()["id"]
        await follow(c, tid)
    rows = stored(tmp_path)
    assert len(rows) == 1
    assert rows[0][0] == OPENER + "\n\n" + ANSWER and rows[0][3] == "c0ffee12abcd"


async def test_buttons_and_steps_go_with_the_answer(tmp_path):
    fn = scripted([TextDelta(OPENER), ToolCall("c1", "lookup", "{}"), Done("tool_calls")],
                  [TextDelta(ANSWER), ToolCall("q1", "quick_replies", json.dumps({"goal_id": 1, "questions": [
                      {"question": "Change it?", "options": ["Yes", "No"]}]})), Done("tool_calls")],
                  [Done("stop")])
    app = create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False), tools=[LOOKUP], stream_fn=fn, with_goal_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/goals", json={"title": "Goal A", "kickoff": False})
        tid = (await c.post("/api/turns", json={"text": "q"})).json()["id"]
        await follow(c, tid)
        ms = [m for m in (await c.get("/api/messages")).json() if m["role"] == "assistant"]
    assert [m["content"] for m in ms] == [OPENER, ANSWER]
    assert not ms[0].get("replies") and ms[1].get("replies")
    assert [row[1] for row in stored(tmp_path)] == ["", "lookup -> found 5"]


# ---------------------------------------------------------------------------------------------------------- an exact copy (S4)
from assistant.repeats import RepeatGuard


def passed_through(*items):
    guard = RepeatGuard()
    out = []
    for item in [*items, {"type": "done"}]:
        out += guard.feed(item)
    return "".join(str(i.get("text", "")) for i in out if i.get("type") == "text")


def text(s):
    return {"type": "text", "text": s}


STEP = {"type": "tool_started", "name": "web_search", "id": "t1"}
RESULT = {"type": "tool_result", "id": "t1", "output": "found"}


def test_the_guard_drops_an_exact_copy_of_the_opener_and_keeps_what_follows():
    assert passed_through(text(OPENER[:20]), text(OPENER[20:]), STEP, RESULT, text("\n\n" + OPENER[:20]),
                          text(OPENER[20:] + "\n\n" + ANSWER[:10]), text(ANSWER[10:])) == OPENER + "\n\n" + ANSWER


def test_the_guard_keeps_text_that_only_starts_like_an_earlier_run():
    assert passed_through(text("I'll check the calendar."), STEP, RESULT, text("I'll check the calendars of both of you.")) == \
        "I'll check the calendar.I'll check the calendars of both of you."
    assert passed_through(text("Looking."), STEP, RESULT, text("Looks fine.")) == "Looking.Looks fine."
    assert passed_through(text("1."), STEP, RESULT, text("1. First item")) == "1.1. First item"


def test_the_guard_never_cuts_inside_a_word():
    assert passed_through(text("Check the price"), STEP, RESULT, text("Check the prices now.")) == "Check the priceCheck the prices now."


# ---------------------------------------------------------------------------------------------------------- one notification (S3)
from assistant.activity import result_line


async def test_one_notification_per_reply_and_it_quotes_the_answer(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.WATCHED_SECONDS", 0.0)
    fn = scripted([TextDelta(OPENER), ToolCall("c1", "lookup", "{}"), Done("tool_calls")], [TextDelta(ANSWER), Done("stop")])
    async with Running(create_app(settings(db_path=str(tmp_path / "a.db"), browser_worker=False), tools=[LOOKUP], stream_fn=fn)) as srv, \
            httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/turns", json={"text": "정리해줘"})
        notes = []
        for _ in range(100):
            await asyncio.sleep(0.05)
            notes = (await c.get("/api/notifications")).json()["items"]
            if notes:
                break
        await asyncio.sleep(0.3)
        notes = (await c.get("/api/notifications")).json()["items"]
    assert [n["type"] for n in notes] == ["task_done"]
    assert notes[0]["body"] == result_line(ANSWER) and notes[0]["body"] != result_line(OPENER)
