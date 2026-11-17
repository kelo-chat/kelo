"""Per-room rules of what Kelo may say in a messenger group (channels/rooms.py, docs/connector-rules.md part 2): stored per room, in the
group turn's prompt, a reply that goes against a `never` rule (or that could not be checked) is held and never said, the owner releases
it, repeated releases only SUGGEST a rule, and only the owner's side changes anything. A scripted judge stands in for the model."""

import json

import pytest

from assistant.channels.rooms import JUDGE_SYSTEM, RoomError, RoomJudge, Rooms
from assistant.providers import Done, TextDelta
from assistant.tools.channel_tools import channel_tools

from test_channels_groups import GROUP, added, group_tools, said, to_chat
from test_channels_telegram import Rig, until


# ---------------------------------------------------------------------------------------------------------------- the store
class Box:
    """A channel state with two rooms, as ChannelManager keeps it."""

    def __init__(self):
        self.st = {"groups": {"-1": {"title": "Family", "since": 1}, "-2": {"title": "Work", "since": 2}}}

    def state(self, cid):
        return json.loads(json.dumps(self.st))

    def update(self, cid, **kw):
        self.st.update(json.loads(json.dumps(kw)))


def rooms():
    box = Box()
    return Rooms(box.state, box.update, lambda: 5.0), box


def test_rules_are_kept_per_room_and_change_reports_the_exact_lines():
    r, box = rooms()
    ch = r.set_rule("t", "-1", "never", "the trip", "Do not mention where we are going")
    assert ch.added == ["Never share the trip: Do not mention where we are going"] and ch.removed == []
    assert r.rules("t", "-2") == [] and r.prompt_block("t", "-2") == ""                     # another room sees nothing
    block = r.prompt_block("t", "-1")
    assert "Never share the trip: Do not mention where we are going" in block and block.startswith("Rules of this room")
    again = r.set_rule("t", "-1", "never", "the trip", "Do not mention the dates")           # same kind and title: replaced, same id
    assert again.removed == ["Never share the trip: Do not mention where we are going"] and len(r.rules("t", "-1")) == 1
    assert r.set_rule("t", "-1", "never", "the trip", "Do not mention the dates").added == []     # nothing changed
    gone = r.remove_rule("t", "-1", r.rules("t", "-1")[0]["id"])
    assert gone.removed == ["Never share the trip: Do not mention the dates"] and r.rules("t", "-1") == []
    assert box.st["groups"]["-1"]["since"] == 1                                              # the room's own fields stay


def test_a_bad_rule_or_room_is_refused():
    r, _ = rooms()
    for args in [("sometimes", "t", "x"), ("never", " ", "x"), ("never", "t", "")]:
        with pytest.raises(RoomError):
            r.set_rule("t", "-1", *args)
    with pytest.raises(RoomError):
        r.set_rule("t", "-9", "never", "t", "x")
    with pytest.raises(RoomError):
        r.remove_rule("t", "-1", "nope")
    for i in range(20):
        r.set_rule("t", "-1", "allow", f"topic {i}", "fine")
    assert r.set_rule("t", "-1", "allow", "topic 3", "changed").added == ["Fine to share topic 3: changed"]   # replacing is not adding
    with pytest.raises(RoomError, match="at most"):
        r.set_rule("t", "-1", "allow", "one more", "fine")


def test_two_releases_for_the_same_reason_suggest_a_rule_and_one_does_not():
    r, _ = rooms()
    r.set_rule("t", "-1", "never", "the trip", "no trip talk")
    rid = r.rules("t", "-1")[0]["id"]
    h1 = r.hold("t", "-1", "-1", "We leave on Friday for the coast", rid, ["the trip"])
    _, first = r.release("t", "-1", h1)
    assert first is None and r.view("t", "-1")["suggestions"] == [] and r.view("t", "-1")["held"] == []
    h2 = r.hold("t", "-1", "-1", "We leave on Monday for the lake", rid, ["the trip"])
    _, second = r.release("t", "-1", h2)
    assert second["kind"] == "allow" and second["title"] == "the trip" and second["count"] == 2
    assert len(r.rules("t", "-1")) == 1                                                      # never automatic: still only the owner's rule
    assert r.view("t", "-2")["suggestions"] == []
    h3 = r.hold("t", "-1", "-1", "again", rid, ["the trip"])
    assert r.release("t", "-1", h3)[1] is None                                                # already suggested: not twice
    ch = r.accept("t", "-1", second["id"])
    assert ch.added == [f"Fine to share the trip: {second['text']}"] and r.view("t", "-1")["suggestions"] == []


def test_a_dismissed_suggestion_is_gone_and_an_unchecked_reply_never_suggests():
    r, _ = rooms()
    for _ in range(3):
        _, s = r.release("t", "-1", r.hold("t", "-1", "-1", "x", "unclear", []))
        assert s is None
    for _ in range(2):
        _, s = r.release("t", "-1", r.hold("t", "-1", "-1", "y", "rule", ["a"]))
    assert s is not None
    r.dismiss("t", "-1", s["id"])
    assert r.view("t", "-1")["suggestions"] == []


async def test_the_judge_answers_strictly_and_fails_closed():
    rules = [{"id": "r1", "kind": "never", "title": "t", "text": "x"}]
    seen = []

    def fake(answer):
        async def ask(system, user):
            seen.append((system, json.loads(user)))
            if isinstance(answer, Exception):
                raise answer
            return answer
        return RoomJudge(ask)
    assert await fake({"breaks": ["r1"]}).breaks(rules, "reply") == ["r1"]
    assert await fake({"breaks": []}).breaks(rules, "reply") == []
    assert seen[0][0] == JUDGE_SYSTEM and seen[0][1]["reply"] == "reply" and seen[0][1]["rules"][0]["id"] == "r1"
    for bad in (None, {"breaks": "r1"}, {"breaks": ["zz"]}, {"breaks": [1]}, {"no": 1}, "text", RuntimeError("x")):
        assert await fake(bad).breaks(rules, "reply") is None


# ---------------------------------------------------------------------------------------------------------------- in a room
class Judge:
    """Scripted RoomJudge: `answers` are what breaks() returns in turn (None = could not check); `calls` counts the questions."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    async def breaks(self, rules, reply):
        self.calls += 1
        return self.answers.pop(0) if self.answers else []


@pytest.fixture
def rig(tmp_path):
    def make(*steps, **kw):
        kw.setdefault("tools", group_tools())
        return Rig(tmp_path, *steps, **kw)
    return make


async def in_room(r, text="where are you going", update_id=4):
    await r.pair()
    r.fake.push(added(update_id=3))
    await until(lambda: r.mgr.state("telegram")["groups"])


def rid_of(r, title="the trip"):
    return next(x["id"] for x in r.mgr.rooms.rules("telegram", str(GROUP)) if x["title"] == title)


async def test_the_room_prompt_carries_its_rules_and_a_room_without_rules_makes_no_judge_call(rig):
    async with rig([TextDelta("Hi."), Done("stop")], [TextDelta("Hi."), Done("stop")]) as r:
        await in_room(r)
        r.mgr.judge = j = Judge()
        r.fake.push(said("hello", update_id=4, mid=21))
        await until(lambda: "Hi." in to_chat(r, GROUP))
        assert j.calls == 0 and "Rules of this room" not in json.dumps(r.seen[0].messages)
        r.mgr.rooms.set_rule("telegram", str(GROUP), "never", "the trip", "Do not say where we are going")
        r.fake.push(said("hello again", update_id=5, mid=22))
        await until(lambda: len(r.seen) == 2)
        system = r.seen[1].messages[0]["content"]
        assert "Never share the trip: Do not say where we are going" in system
        await until(lambda: j.calls == 1)


async def test_a_reply_that_breaks_a_never_rule_is_held_and_never_said(rig):
    spoken = "We are going to the coast on Friday."
    async with rig([TextDelta(spoken), Done("stop")]) as r:
        await in_room(r)
        r.mgr.rooms.set_rule("telegram", str(GROUP), "never", "the trip", "Do not say where we are going")
        r.mgr.judge = Judge([rid_of(r)])
        r.fake.push(said("where are you going", update_id=4))
        held = "That is private, so I'll show it to the owner in the app instead."
        await until(lambda: held in to_chat(r, GROUP))
        owner = await until(lambda: [x for x in to_chat(r, 7) if "held back" in x])
        assert owner == ['In "Family" I held back a reply that goes against the rules of that group: the trip. If it is fine, tell me to post it.']
        assert not any("coast" in x for x in to_chat(r, GROUP))
        view = r.mgr.rooms.view("telegram", str(GROUP))
        assert [h["text"] for h in view["held"]] == [spoken]
        # the owner lets it through: it is posted, and one release suggests nothing
        out = await r.mgr.release_held("telegram", "Family", view["held"][0]["id"])
        assert out == {"posted": True} and spoken in to_chat(r, GROUP)
        assert r.mgr.rooms.view("telegram", str(GROUP))["held"] == []


async def test_a_check_that_cannot_be_made_holds_the_reply(rig):
    async with rig([TextDelta("Some answer."), Done("stop")]) as r:
        await in_room(r)
        r.mgr.rooms.set_rule("telegram", str(GROUP), "never", "the trip", "x")
        r.mgr.judge = Judge(None)
        r.fake.push(said("hello", update_id=4))
        owner = await until(lambda: [x for x in to_chat(r, 7) if "could not check" in x])
        assert owner and "Some answer." not in to_chat(r, GROUP)
        assert r.mgr.rooms.view("telegram", str(GROUP))["held"][0]["reason"] == "unclear"


async def test_no_judge_at_all_holds_the_reply_too(rig):
    async with rig([TextDelta("Some answer."), Done("stop")]) as r2:
        await in_room(r2)
        r2.mgr.rooms.set_rule("telegram", str(GROUP), "never", "the trip", "x")
        r2.mgr.judge = None
        r2.fake.push(said("hello", update_id=4))
        await until(lambda: [x for x in to_chat(r2, 7) if "could not check" in x])
        assert "Some answer." not in to_chat(r2, GROUP)


async def test_a_clean_reply_is_said_and_the_group_turn_has_no_rule_tools(rig):
    async with rig([TextDelta("Lunch is at noon."), Done("stop")]) as r:
        await in_room(r)
        r.mgr.rooms.set_rule("telegram", str(GROUP), "never", "the trip", "x")
        r.mgr.judge = Judge([])
        r.fake.push(said("when is lunch", update_id=4))
        await until(lambda: "Lunch is at noon." in to_chat(r, GROUP))
        assert {(t.get("name") or t.get("function", {}).get("name")) for t in r.seen[0].tools} <= {"web_search", "web_fetch", "weather"}        # nothing to change a rule with


async def test_the_messenger_tool_changes_rules_and_asks_only_when_it_opens_something_up(rig):
    async with rig() as r:
        await in_room(r)
        tool = channel_tools(lambda: r.mgr)[0]
        g = str(GROUP)

        async def run(**a):
            return json.loads(await tool.handler({"channel": "telegram", "group": "Family", **a}))
        assert tool.auto_approve({"action": "room_rule_set", "rule_kind": "never"}) is True
        assert tool.auto_approve({"action": "room_rule_set", "rule_kind": "allow"}) is False
        for a in ("room_rule_remove", "room_suggestion_accept", "room_release"):
            assert tool.auto_approve({"action": a}) is False
        assert tool.auto_approve({"action": "room_rules"}) is True and tool.auto_approve({"action": "room_suggestion_dismiss"}) is True
        out = await run(action="room_rule_set", rule_kind="never", title="the trip", text="no trip talk")
        assert out == {"added": ["Never share the trip: no trip talk"], "removed": []}
        got = await run(action="room_rules")
        assert got["group"] == "Family" and got["rules"][0]["line"] == "Never share the trip: no trip talk"
        gone = await run(action="room_rule_remove", id=got["rules"][0]["id"])
        assert gone["removed"] == ["Never share the trip: no trip talk"]
        assert await tool.handler({"channel": "telegram", "group": "Nobody", "action": "room_rules"}) == "error: no such group (see the groups action)"
        assert (await tool.handler({"channel": "telegram", "group": "Family", "action": "room_rule_remove", "id": "x"})).startswith("error:")
        assert await tool.describe({"channel": "telegram", "group": "Family", "action": "room_release"}) == 'Post the held reply in the group "Family"?'
        assert g in r.mgr.state("telegram")["groups"]


async def test_two_releases_in_a_room_suggest_a_rule_the_owner_must_accept(rig):
    async with rig() as r:
        await in_room(r)
        tool = channel_tools(lambda: r.mgr)[0]
        g = str(GROUP)
        r.mgr.rooms.set_rule("telegram", g, "never", "the trip", "x")
        rid = rid_of(r)
        for n in range(2):
            hid = r.mgr.rooms.hold("telegram", g, g, f"We go to the coast {n}", rid, ["the trip"])
            out = json.loads(await tool.handler({"channel": "telegram", "group": "Family", "action": "room_release", "id": hid}))
        assert out["posted"] and out["suggestion"]["kind"] == "allow"
        assert len(r.mgr.rooms.rules("telegram", g)) == 1                                    # a suggestion is not a rule
        done = json.loads(await tool.handler({"channel": "telegram", "group": "Family", "action": "room_suggestion_accept", "id": out["suggestion"]["id"]}))
        assert done["added"][0].startswith("Fine to share the trip: ") and len(r.mgr.rooms.rules("telegram", g)) == 2


async def test_rules_survive_the_bot_being_reported_again_and_go_with_the_pairing(rig):
    async with rig() as r:
        await in_room(r)
        r.mgr.rooms.set_rule("telegram", str(GROUP), "never", "the trip", "x")
        r.fake.push(added(update_id=5))                                                      # reported again (Discord does at every connect)
        r.fake.push(said("hi", update_id=6, mention=False))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 7)
        assert len(r.mgr.rooms.rules("telegram", str(GROUP))) == 1
        r.mgr.unpair("telegram")
        assert r.mgr.state("telegram")["groups"] == {}
