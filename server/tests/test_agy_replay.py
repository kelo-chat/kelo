"""Two ordinary consecutive chat turns, built by the real server (system prompt, stored history, turn note, tool rounds), handed to
the agy pool's `continuation` the way the agy adapter hands them: the session must go on and send only the new turn.
Only the model is fake; what it says is what the pool's `said` carries."""

import json
import time

import httpx
import pytest

from assistant.agent.loop import Tool
from assistant.providers import Done, TextDelta, ToolCall, agy_pool
from assistant.providers.cli_agents import fit_agy_rules
from assistant.providers.types import late_notes_as_user
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running, read_events


def as_agy_gets(messages):
    """What providers.client.stream_chat hands the agy adapter for a request (late notes as user text, long rules split)."""
    return fit_agy_rules(late_notes_as_user(list(messages)))


class Session:
    """What AgyPool keeps of a live session between calls, driven the way Lease.release records it."""

    def __init__(self):
        self.req = self.sent = None
        self.said = ""

    def call(self, request_messages, said_text):
        """One model call. Returns (mode, reason): "round" / "turn" when the session went on, else "new" + why."""
        incoming = as_agy_gets(request_messages)
        got = agy_pool.continuation(self.req, self.sent, incoming, self.said)
        if got is None:
            mode, why, sent = "new", agy_pool.why_not_continued(self.req, incoming, self.said), incoming
            self.said = said_text
        else:
            sent, new_turn = got
            mode, why = ("turn" if new_turn else "round"), ""
            self.said = said_text if new_turn else self.said + "\n" + said_text
        self.req, self.sent = incoming, sent
        return mode, why


def look_tool():
    async def handler(args):
        return "the page says: open from 9 to 5"
    return Tool("look", "look something up", {"type": "object"}, handler, read_only=True)


def make_app(tmp_path, replies, seen):
    """A server whose model answers from `replies` (a list of event lists per model call) and notes every request it gets."""
    it = iter(replies)

    async def fn(provider, request, **kw):
        if request.purpose != "chat":   # the server's own side calls (a task's name ...) are other sessions in the pool
            yield TextDelta("Library")
            yield Done("stop")
            return
        seen.append(request.messages)
        for ev in next(it):
            yield ev
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), home_dir=str(tmp_path / "home"),
                 scheduler=False)
    return create_app(s, tools=[look_tool()], stream_fn=fn)


async def talk(client, text, **body):
    async with client.stream("POST", "/api/talk", json={"text": text, **body}) as r:
        return await read_events(r)


def said_in(replies):
    return "".join(e.text for e in replies if isinstance(e, TextDelta))


async def replay(tmp_path, turns, **body):
    """`turns`: per user turn [(user text, [model calls' event lists])]. Returns the session's (mode, reason) for every model call."""
    seen: list = []
    app = make_app(tmp_path, [call for _text, calls in turns for call in calls], seen)
    session, results, n = Session(), [], 0
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        for text, calls in turns:
            await talk(c, text, **body)
            for call in calls:
                results.append(session.call(seen[n], said_in(call)))
                n += 1
    return results, seen


def reply(text):
    return [TextDelta(text), Done("stop")]


def call_look(call_id="c1"):
    return [ToolCall(id=call_id, name="look", arguments=json.dumps({"q": call_id})), Done("tool_calls")]


async def test_the_next_plain_turn_continues_the_session(tmp_path):
    results, _ = await replay(tmp_path, [("When does the library open?", [reply("From 9 to 5.")]),
                                        ("And on Sunday?", [reply("Closed on Sunday.")]),
                                        ("Thanks!", [reply("You are welcome.")])])
    assert [m for m, _ in results] == ["new", "turn", "turn"], results


async def test_the_next_turn_after_tool_rounds_continues_the_session(tmp_path):
    results, _ = await replay(tmp_path, [
        ("Look up when the library opens", [call_look("c1"), call_look("c2"), reply("Open from 9 to 5.\n\nWant more?")]),
        ("And on Sunday?", [call_look("c3"), reply("Closed on Sunday.")]),
        ("Thanks!", [reply("You are welcome.")])])
    assert [m for m, _ in results] == ["new", "round", "round", "turn", "round", "turn"], results


async def test_a_reply_that_wrote_text_before_its_tool_call_continues_the_session(tmp_path):
    lead = [TextDelta("Let me check that for you."), *call_look("c1")]
    results, _ = await replay(tmp_path, [("Look it up", [lead, reply("It opens at 9.")]), ("Thanks", [reply("Sure.")])])
    assert [m for m, _ in results] == ["new", "round", "turn"], results


async def test_a_reply_whose_lead_in_lines_were_not_stored_continues_the_session(tmp_path):
    """The conversation keeps a finished reply's opener and answer, not the short line before each middle tool step (reply_text.py):
    the stored text is shorter than what the session wrote, and the next turn must still be recognised as its own."""
    def step(text, n):
        return [TextDelta(text), *call_look(n)]
    results, _ = await replay(tmp_path, [
        ("Find the opening hours", [step("I will look for the hours.", "c1"), step("Now checking the weekend.", "c2"),
                                    step("One more place to look.", "c3"), reply("It opens from 9 to 5 on weekdays.")]),
        ("And on Sunday?", [reply("Closed on Sunday.")])])
    assert [m for m, _ in results] == ["new", "round", "round", "round", "turn"], results


async def test_a_turn_whose_message_got_the_apps_own_lines_continues_the_session(tmp_path):
    """The user taps a next step from a briefing card: the model gets the message plus the app's note after a blank line, the
    conversation stores the user's words only. The next turn is still the session's."""
    seen: list = []
    app = make_app(tmp_path, [reply("Done."), reply("Anything else?")], seen)
    store = Store(str(tmp_path / "a.db"))
    store._run("INSERT INTO briefings(day, message_id, card, created_at) VALUES(?,?,?,?)",
               ("2026-11-16", None, json.dumps({"day": "2026-11-16", "suggestions": [{"text": "Book the dentist"}]}), time.time()))
    session = Session()
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await talk(c, "Book the dentist")
        first = session.call(seen[0], "Done.")
        await talk(c, "Thanks")
        second = session.call(seen[1], "Anything else?")
    sent = next(m["content"] for m in seen[0] if m.get("role") == "user" and m["content"].startswith("Book the dentist"))
    assert sent.startswith("Book the dentist\n\n[Not from the user:"), sent   # the note really went with it
    assert [first[0], second[0]] == ["new", "turn"], (first, second)


async def test_a_new_conversation_starts_a_new_session(tmp_path):
    """/new: the model's conversation starts over, so the session must too (the stored history after it is empty)."""
    seen: list = []
    app = make_app(tmp_path, [reply("One."), reply("Two."), reply("Three.")], seen)
    session = Session()
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await talk(c, "first")
        session.call(seen[0], "One.")
        await talk(c, "/new")
        after_new = session.call(seen[1], "Two.")
        await talk(c, "third")
        third = session.call(seen[2], "Three.")
    assert after_new[0] == "new" and third[0] == "new", (after_new, third)


@pytest.mark.parametrize("change", ["system", "earlier_message", "extra_message"])
def test_a_conversation_that_really_changed_starts_over(change):
    note = lambda n: {"role": "user", "content": f"{agy_pool.TURN_NOTE}{n}"}   # noqa: E731
    system = {"role": "system", "content": "rules"}
    first = [system, {"role": "user", "content": "h1"}, {"role": "assistant", "content": "r1"}, note(1), {"role": "user", "content": "q1"}]
    session = Session()
    assert session.call(first, "a1")[0] == "new"
    nxt = {
        "system": [{"role": "system", "content": "other rules"}, *first[1:3], {"role": "user", "content": "q1"},
                   {"role": "assistant", "content": "a1"}, note(2), {"role": "user", "content": "q2"}],
        "earlier_message": [system, {"role": "user", "content": "h1 edited"}, first[2], {"role": "user", "content": "q1"},
                            {"role": "assistant", "content": "a1"}, note(2), {"role": "user", "content": "q2"}],
        "extra_message": [system, *first[1:3], {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
                          {"role": "user", "content": "something added by another job"}, note(2), {"role": "user", "content": "q2"}],
    }[change]
    mode, why = session.call(nxt, "a2")
    assert mode == "new" and why
