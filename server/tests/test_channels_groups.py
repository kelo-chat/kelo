"""Messenger groups (channels/manager.py _on_group / _group_turn), with a fake Telegram and a scripted model. No network."""

import asyncio
import json

import pytest

from assistant.agent import Tool
from assistant.channels import Telegram
from assistant.channels.base import Incoming, Membership
from assistant.providers import Done, TextDelta, ToolCall
from assistant.tools.channel_tools import channel_tools

from test_channels_telegram import FakeTG, Rig, TG_TOKEN, msg, until

GROUP = -100
BOT_ID = 42


def added(uid=7, chat=GROUP, title="Family", update_id=10, status="member", old="left", kind="supergroup"):
    return {"update_id": update_id, "my_chat_member": {"chat": {"id": chat, "type": kind, "title": title}, "from": {"id": uid},
                                                       "old_chat_member": {"status": old}, "new_chat_member": {"status": status, "user": {"id": BOT_ID}}}}


def said(text, *, uid=8, first="Bob", mid=20, update_id=11, mention=True, chat=GROUP, title="Family", **extra):
    body = f"@kelo_bot {text}" if mention else text
    ents = [{"type": "mention", "offset": 0, "length": 9}] if mention else []
    return {"update_id": update_id, "message": {"message_id": mid, "from": {"id": uid, "first_name": first},
                                                "chat": {"id": chat, "type": "supergroup", "title": title}, "text": body,
                                                **({"entities": ents} if ents else {}), **extra}}


def public(name, **kw):
    async def handler(args):
        return f"{name} result"
    return Tool(name, name, {"type": "object"}, handler, read_only=True, **kw)


def group_tools(extra=()):
    return [public("web_search"), public("web_fetch"), public("weather"), *extra]


def to_chat(r, chat):
    return [b["text"] for b in r.fake.of("sendMessage") if str(b["chat_id"]) == str(chat)]


@pytest.fixture
def rig(tmp_path):
    def make(*steps, **kw):
        kw.setdefault("tools", group_tools())
        return Rig(tmp_path, *steps, **kw)
    return make


async def test_owner_added_group_is_allowed_and_owner_told(rig):
    async with rig() as r:
        await r.pair()
        before = len(r.store.messages())
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        g = r.mgr.state("telegram")["groups"][str(GROUP)]
        assert g["title"] == "Family" and g["added_by"] == "7" and g["since"]
        line = 'I can now be called in the group "Family": mention me or reply to me there.'
        await until(lambda: line in to_chat(r, 7))
        assert to_chat(r, GROUP) == [] and len(r.store.messages()) == before        # nothing in the group, nothing in the app chat
        r.fake.push(added(update_id=4, status="administrator", old="member"))        # promoted: nothing changed, no second notice
        r.fake.push(added(update_id=5))                                              # reported again (Discord does at each connect)
        r.fake.push(msg("x", uid=99, chat=99, update_id=6))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 7)
        assert to_chat(r, 7).count(line) == 1


async def test_group_added_by_someone_else_is_ignored(rig, caplog):
    import logging
    caplog.set_level(logging.INFO)
    async with rig() as r:
        await r.pair()
        r.fake.push(added(uid=99, update_id=3))
        r.fake.push(said("hello?", update_id=4))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 5)
        assert r.mgr.state("telegram")["groups"] == {} and not r.seen
        assert to_chat(r, GROUP) == [] and to_chat(r, 99) == []                    # no question, no reply
        channel_log = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("assistant.channels"))   # not httpx: its URLs carry a random port
    assert "added to a group by someone other than the owner" in channel_log and "99" not in channel_log


async def test_unmentioned_group_message_is_kept_as_context_but_not_answered(rig):
    async with rig([TextDelta("Noted."), Done("stop")]) as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.fake.push(said("we should meet at the corner cafe", first="Ann", uid=9, mention=False, update_id=4, mid=30))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 5)
        assert not r.seen and to_chat(r, GROUP) == []                              # not called: no turn, no reply
        assert [x["text"] for x in r.mgr.state("telegram")["group_log"][str(GROUP)]] == ["we should meet at the corner cafe"]
        r.fake.push(said("which cafe was it?", update_id=5, mid=31))
        await until(lambda: "Noted." in to_chat(r, GROUP))
        msgs = [(m["role"], m["content"]) for m in r.seen[0].messages if m["role"] != "system"]
        assert msgs == [("user", "Ann: we should meet at the corner cafe"), ("user", "Bob: @kelo_bot which cafe was it?")]
        assert "not meant for you" in r.seen[0].messages[0]["content"]


async def test_unmentioned_files_in_a_group_get_no_answer(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.fake.push(said("look", mention=False, update_id=4, document={"file_id": "d", "file_name": "a.pdf", "file_size": 5}))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 5)
        assert not r.seen and to_chat(r, GROUP) == [] and r.mgr.state("telegram")["group_log"] == {}


async def test_message_in_a_group_not_added_by_the_owner_is_not_kept(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(said("we should meet at the secret cafe", mention=False, update_id=4))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 5)
        st = r.mgr.state("telegram")
        assert not r.seen and st["group_log"] == {} and "secret cafe" not in json.dumps(st)


async def test_mention_and_reply_to_bot_start_a_group_turn(rig):
    async with rig([TextDelta("Sunny."), Done("stop")]) as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.fake.push(said("how is the weather", update_id=4, mid=21))
        await until(lambda: "Sunny." in to_chat(r, GROUP))
        sent = [b for b in r.fake.of("sendMessage") if str(b["chat_id"]) == str(GROUP)][-1]
        assert sent["reply_parameters"]["message_id"] == 21                          # a reply to the caller's message
        assert r.seen[0].messages[-1] == {"role": "user", "content": "Bob: @kelo_bot how is the weather"}
        reply = {"message_id": 5, "from": {"id": BOT_ID, "is_bot": True}, "text": "Sunny."}
        r.fake.push(said("and tomorrow?", mention=False, update_id=5, mid=22, reply_to_message=reply))
        await until(lambda: len(r.seen) >= 2)
        await until(lambda: to_chat(r, GROUP).count("Sunny.") == 2)
        msgs = [(m["role"], m["content"]) for m in r.seen[1].messages if m["role"] != "system"]
        assert msgs == [("user", "Bob: @kelo_bot how is the weather"), ("assistant", "Sunny."), ("user", "Bob: and tomorrow?")]
        # a reply to somebody else's message is not a call
        r.fake.push(said("sure", mention=False, update_id=6, mid=23, reply_to_message={"message_id": 4, "from": {"id": 8}}))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 7)
        assert len(r.seen) == 2


async def test_group_turn_has_only_public_tools_and_no_owner_conversation(rig, tmp_path):
    async with rig([TextDelta("Hi."), Done("stop")], tools=group_tools([public("read_file"), public("remember")])) as r:
        (tmp_path / "home").mkdir(exist_ok=True)
        (tmp_path / "home" / "user.md").write_text("# user.md\nOWNER-PRIVATE-NOTE lives at Elm Street\n")
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        calls = []
        real = r.mgr.start_turn

        def spy(*a, **kw):
            calls.append((a, kw))
            return real(*a, **kw)
        r.mgr.start_turn = spy
        before = len(r.store.messages())
        r.fake.push(said("hello", update_id=4))
        await until(lambda: "Hi." in to_chat(r, GROUP))
        (a, kw), = calls
        assert {t.name for t in kw["tools_"]} == {"web_search", "web_fetch", "weather"} and kw["max_steps"] == 6
        assert kw["source"] == "group:telegram" and kw["tool_scope"] == "group:telegram" and kw["unattended"] is False
        assert kw["title"] == "Telegram group" and "context" not in kw
        names = {t["function"]["name"] for t in r.seen[0].tools}
        assert names <= {"web_search", "web_fetch", "weather", "find_tools"} and "read_file" not in names and "remember" not in names
        system = r.seen[0].messages[0]
        assert system["role"] == "system" and 'Telegram group "Family"' in system["content"] and len(system["content"]) < 1100
        everything = json.dumps(r.seen[0].messages)
        assert "OWNER-PRIVATE-NOTE" not in everything and "Elm Street" not in everything
        assert len(r.store.messages()) == before                                     # nothing in the messages table
        assert not [m for m in r.store.messages() if "hello" in m["content"] or m["content"] == "Hi."]
    from assistant.channels.manager import GROUP_SYSTEM
    assert len(GROUP_SYSTEM) <= 900


async def test_group_reply_with_private_category_is_held_back_and_owner_told(rig, tmp_path):
    spoken = "The owner lives at 12 Elm Street Springfield."
    async with rig([TextDelta(spoken), Done("stop")]) as r:
        (tmp_path / "home").mkdir(exist_ok=True)
        (tmp_path / "home" / "user.md").write_text("# user.md\nAddress: 12 Elm Street Springfield\n")
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.fake.push(said("where does the owner live", update_id=4))
        held = "That is private, so I'll show it to the owner in the app instead."
        await until(lambda: held in to_chat(r, GROUP))
        owner = await until(lambda: [x for x in to_chat(r, 7) if "held back" in x])
        # (hc-privacy: the old expectation said "home address", read from the label word "Address:". Here the model's answers are the
        # spoken reply itself, no usable classification, so the labelled value counts as "a personal detail": the safe side)
        assert owner == ['In "Family" I held back a reply that held: a personal detail.']
        assert not any("Elm Street" in x for x in r.sent()) and "Elm Street" not in json.dumps(r.mgr.state("telegram"))
        assert to_chat(r, GROUP) == [held]
        assert [x["text"] for x in r.mgr.state("telegram")["group_log"][str(GROUP)] if x["bot"]] == [held]


async def test_cards_never_go_to_a_group(rig):
    ran = []

    async def handler(args):
        ran.append(args)
        return "fetched"
    risky = Tool("web_fetch", "fetch", {"type": "object"}, handler, requires_approval=True)
    steps = ([ToolCall("c1", "web_fetch", '{"url":"https://x.example"}'), Done("tool_calls")], [TextDelta("I could not."), Done("stop")])
    async with rig(*steps, tools=[public("web_search"), risky, public("weather")]) as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.fake.push(said("open that page", update_id=4))
        await until(lambda: "I could not." in to_chat(r, GROUP))
        line = 'Someone in "Family" asked for something only you can allow; I did not do it.'
        assert await until(lambda: line in to_chat(r, 7))
        assert ran == []                                                              # denied at once
        assert not [b for b in r.fake.of("sendMessage") if b.get("reply_markup")]    # no buttons anywhere
        assert to_chat(r, GROUP) == ["I could not."]


async def test_group_log_keeps_20_lines(rig):
    async with rig([TextDelta("ok"), Done("stop")]) as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        for i in range(25):
            r.mgr._log_line("telegram", str(GROUP), str(GROUP), "Bob", f"line {i} " + "x" * 600, bot=i % 2 == 1, mid=str(i))
        log = r.mgr.state("telegram")["group_log"][str(GROUP)]
        assert len(log) == 20 and log[0]["text"].startswith("line 5 ") and log[-1]["text"].startswith("line 24 ")
        assert all(len(x["text"]) == 500 for x in log) and set(log[0]) >= {"name", "text", "bot", "at"}
        r.fake.push(said("now you", update_id=4, mid=99))
        await until(lambda: "ok" in to_chat(r, GROUP))
        users = [m for m in r.seen[0].messages if m["role"] != "system"]
        assert len(users) == 20 and users[-1]["content"] == "Bob: @kelo_bot now you" and users[0]["content"].startswith("Bob: line 6")
        assert len(r.mgr.state("telegram")["group_log"][str(GROUP)]) == 20


async def test_bot_removed_drops_group(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.mgr._log_line("telegram", str(GROUP), str(GROUP), "Bob", "hi", bot=False, mid="1")
        r.fake.push(added(uid=7, update_id=4, status="kicked", old="member"))
        await until(lambda: not r.mgr.state("telegram")["groups"])
        assert r.mgr.state("telegram")["group_log"] == {}
        r.fake.push(said("hello again", update_id=5))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 6)
        assert not r.seen


async def test_other_members_can_call_kelo_in_an_allowed_group(rig):
    async with rig([TextDelta("Hello Eve."), Done("stop")]) as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.fake.push(said("who are you", uid=555, first="Eve", update_id=4))
        await until(lambda: "Hello Eve." in to_chat(r, GROUP))
        assert r.seen[0].messages[-1]["content"] == "Eve: @kelo_bot who are you"
        # and in a private chat the same stranger still gets nothing
        r.fake.push(msg("hi", uid=555, chat=555, update_id=5))
        await until(lambda: r.mgr.state("telegram")["offset"] >= 6)
        assert to_chat(r, 555) == []


async def test_files_in_a_group_get_the_text_only_line(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.fake.push(said("see this", update_id=4, document={"file_id": "d", "file_name": "a.pdf", "file_size": 5}))
        await until(lambda: "In groups I only read text." in to_chat(r, GROUP))
        assert not r.seen and r.mgr.state("telegram")["group_log"] == {}


async def test_unpair_forgets_the_groups(rig):
    async with rig() as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.mgr.unpair("telegram")
        assert r.mgr.state("telegram")["groups"] == {}
        r.fake.push(added(update_id=4))
        await asyncio.sleep(0.1)
        assert r.mgr.state("telegram")["groups"] == {}                              # nobody is paired: not the owner's group
        r.mgr.begin_pairing("telegram", reset=True)
        assert r.mgr.state("telegram")["groups"] == {} and r.mgr.state("telegram")["group_log"] == {}


async def test_messenger_tool_groups_and_remove_group(rig):
    holder = []
    tool = channel_tools(lambda: holder[0])[0]
    steps = ([ToolCall("c1", "messenger", '{"channel": "telegram", "action": "remove_group", "group": "Family"}'), Done("tool_calls")],
             [TextDelta("Done."), Done("stop")])
    async with rig(*steps, tools=[tool]) as r:
        holder.append(r.mgr)
        await r.pair()
        assert json.loads(await tool.handler({"action": "groups"})) == []
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        listed = json.loads(await tool.handler({"action": "groups"}))
        assert listed == [{"chat_id": str(GROUP), "title": "Family", "since": listed[0]["since"]}] and listed[0]["since"]
        assert tool.auto_approve({"action": "groups"}) and not tool.auto_approve({"action": "remove_group"})
        assert await tool.describe({"action": "remove_group", "group": "Family"}) == 'Stop answering in the group "Family"?'
        assert (await tool.handler({"action": "remove_group", "group": "Nope"})).startswith("error")
        assert (await r.c.post("/api/turns", json={"text": "stop answering in Family"})).status_code == 200
        pending = await until(lambda: _pending(r))
        assert pending["name"] == "messenger" and r.mgr.state("telegram")["groups"]
        assert (await r.c.post(f"/api/approvals/{pending['approval_id']}", json={"approve": True})).status_code == 200
        await until(lambda: not r.mgr.state("telegram")["groups"])
        assert (await tool.handler({"action": "remove_group", "group": str(GROUP)})).startswith("error")


async def _pending(r):
    items = (await r.c.get("/api/approvals/pending")).json()["items"]
    return items[0] if items else None


# ---- the parser (no app)
def parser():
    tg = Telegram(lambda: TG_TOKEN, fetch=FakeTG().fetch)
    tg.bot_id, tg.bot_name = str(BOT_ID), "Kelo_Bot"
    return tg


def test_parse_mentions_replies_and_membership():
    tg = parser()
    a = tg.parse(said("hi"))
    assert isinstance(a, Incoming) and not a.private and a.chat_title == "Family" and a.mentioned and not a.reply_to_bot
    b = tg.parse(said("hi", mention=False))
    assert not b.mentioned
    other = said("hi", mention=False)
    other["message"]["text"] = "@someone_else hi"
    other["message"]["entities"] = [{"type": "mention", "offset": 0, "length": 13}]
    assert not tg.parse(other).mentioned
    emoji = said("x", mention=False)           # an emoji before the mention: offsets count UTF-16 units (the emoji is two)
    emoji["message"]["text"] = "😀 @kelo_bot hi"
    emoji["message"]["entities"] = [{"type": "mention", "offset": 3, "length": 9}]
    assert tg.parse(emoji).mentioned
    text_mention = said("x", mention=False)
    text_mention["message"]["entities"] = [{"type": "text_mention", "offset": 0, "length": 3, "user": {"id": BOT_ID}}]
    assert tg.parse(text_mention).mentioned
    cmd = said("x", mention=False)
    cmd["message"]["text"] = "/ask@kelo_bot what"
    assert tg.parse(cmd).mentioned
    cmd["message"]["text"] = "/ask@other_bot what"
    assert not tg.parse(cmd).mentioned
    r = said("x", mention=False, reply_to_message={"message_id": 1, "from": {"id": BOT_ID}})
    assert tg.parse(r).reply_to_bot
    m = tg.parse(added(status="administrator", old="left"))
    assert m == Membership(str(GROUP), "Family", "7", True, 10)
    gone = tg.parse(added(status="left", old="member"))
    assert isinstance(gone, Membership) and gone.joined is False
    assert tg.parse(added(status="kicked", old="administrator")).joined is False
    assert tg.parse(added(status="administrator", old="member")) is None
    assert tg.parse(added(kind="private")) is None


async def test_telegram_reply_parameters_only_on_the_first_part():
    fake = FakeTG()
    tg = Telegram(lambda: TG_TOKEN, fetch=fake.fetch)
    text = "\n\n".join(f"paragraph {i} " + "word " * 100 for i in range(18))
    await tg.send("-100", text, None, "21")
    sent = fake.of("sendMessage")
    assert sent[0]["reply_parameters"]["message_id"] == 21 and all("reply_parameters" not in s for s in sent[1:])
    await tg.send("7", "plain")
    assert "reply_parameters" not in fake.of("sendMessage")[-1]


# ---- other bots in a group (bot-to-bot mode): their messages are read like anyone's
def other_bot(text, *, uid=900, mid=40, update_id=40, mention=True, **kw):
    m = said(text, uid=uid, first="PickleBot", mid=mid, update_id=update_id, mention=mention, **kw)
    m["message"]["from"]["is_bot"] = True
    return m


def test_group_prompt_allows_at_mention_but_not_messages_elsewhere():
    from assistant.channels.manager import GROUP_SYSTEM
    low = GROUP_SYSTEM.casefold()
    assert "@name" in low and "ask another bot" in low and "posted in this group" in low
    assert "no messages to other chats or people outside this group" in low and "purchases" in low


async def test_message_from_another_bot_is_not_dropped(rig):
    async with rig([TextDelta("Hello bot."), Done("stop")]) as r:
        await r.pair()
        r.fake.push(added(update_id=3))
        await until(lambda: r.mgr.state("telegram")["groups"])
        r.fake.push(other_bot("here is my answer", update_id=5, mid=22))
        await until(lambda: "Hello bot." in to_chat(r, GROUP))
        r.fake.push(other_bot("just chatter", update_id=6, mid=23, mention=False))
        await until(lambda: len(r.mgr.state("telegram")["group_log"][str(GROUP)]) >= 3)
        assert "just chatter" in [x["text"] for x in r.mgr.state("telegram")["group_log"][str(GROUP)]]
