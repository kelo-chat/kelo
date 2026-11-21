"""Urgency levels and the kinds of source (parity unit pipe-proactive-urgency, owner 2026-11-20): a declared read names its kind of
data (content/prompts/situation_kinds), the watch gives every proposal a level (regular / today / now), calendar changes (added, changed,
gone by time) wake the watch, the selection keeps item levels and never raises one, and a "now" message is handed over as "when, not
whether". No model is called: the scripted fake agent stands in for every turn."""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from situation_fake import SEOUL, FakeMail, Rig, label_of, mail, ts

from assistant import situation as S
from assistant.situation import Section


def owner_day(tmp_path, t, **kw) -> Rig:
    """The owner's case (as test_situation.py): contact hours 08:00-24:00, at most 3 a day, the briefing at 08:00 -> send times 12:00, 16:00, 20:00."""
    r = Rig(tmp_path, t, **kw)
    r.p.update(wake_start="08:00", wake_end="00:00")
    r.sit.briefing_at = lambda day: datetime(day.year, day.month, day.day, 8, 0, tzinfo=SEOUL).timestamp()
    r.store.set_meta(S.SEND_ATTEMPT, str(t - 60))
    return r


def kinds_of_passes(r: Rig) -> list[str]:
    return [p["kind"] for p in reversed(r.sit.passes())]


def rows(r: Rig, key: str) -> list[dict]:
    return [dict(x) for x in r.store._all("SELECT key, status, level, kinds, urgent, reason FROM help_items WHERE key = ? ORDER BY id", (key,))]


def calls(r: Rig, scope: str) -> list[dict]:
    return [c for c in r.agent.calls if c["scope"] == scope]


class Outside:
    """A SituationSource with outside = True: read by the hourly watch too."""
    outside = True

    def __init__(self, section: Section | None):
        self.section_value = section

    def section(self, now: float, since: float):
        return self.section_value


# ---------------------------------------------------------------------------------------------------------------- kinds of data
def test_a_declared_read_names_its_kinds_and_an_unknown_kind_is_refused():
    from assistant.situation_reads import parse_read
    assert parse_read({"tool": "connector_get", "arguments": {}, "content": ["finance"]}, where="x").content == ("finance",)
    assert parse_read({"tool": "connector_get", "arguments": {}, "content": "messages"}, where="x").content == ("messages",)
    assert parse_read({"tool": "connector_get", "arguments": {}, "content": ["mail", "mail"]}, where="x").content == ("mail",)
    assert parse_read({"tool": "connector_get", "arguments": {}}, where="x").content == ()
    for bad in (["nope"], [1]):
        with pytest.raises(ValueError):
            parse_read({"tool": "connector_get", "arguments": {}, "content": bad}, where="x")


def test_the_kinds_are_the_guidance_files_and_each_is_english():
    from assistant.situation_reads import content_kinds
    assert content_kinds() == {"calendar", "mail", "messages", "notifications", "finance", "encrypted_chat"}
    folder = S.prompts_dir() / "situation_kinds"
    for name in content_kinds():
        text = (folder / f"{name}.md").read_text(encoding="utf-8")
        assert text.strip() and not re.search(r"[가-힣]", text), name


def test_the_builtin_reads_declare_their_kinds():
    from assistant import oauth_connect, phone_data, selfhosted
    from assistant.situation_reads import parse_read
    assert phone_data.SITUATION["messages"]["content"] == ["messages"]
    assert phone_data.SITUATION["notifications"]["content"] == ["notifications"]
    assert parse_read(selfhosted.SITUATION_READS["firefly_iii"], where="x").content == ("finance",)
    for name in ("nextcloud", "gitea", "forgejo"):
        assert parse_read(selfhosted.SITUATION_READS[name], where="x").content == ("notifications",), name
    assert parse_read(selfhosted.SITUATION_READS["home_assistant"], where="x").content == ()
    provs = [p for p in oauth_connect.PROVIDERS.values() if p.situation]
    assert provs and all(parse_read(p.situation, where=p.id).content == ("notifications",) for p in provs)


def test_nest_situation_may_name_kinds():
    from assistant.nest_manifest import PackageError, parse_situation
    connect = {"type": "key", "base_url": "https://svc.example.org/api"}
    got = parse_situation({"request": {"method": "GET", "path": "/n"}, "mode": "state", "content": ["notifications"]}, connect=connect)
    assert got.content == ("notifications",)
    with pytest.raises(PackageError):
        parse_situation({"request": {"method": "GET", "path": "/n"}, "mode": "state", "content": ["nope"]}, connect=connect)


async def test_each_section_says_its_kind_and_the_watch_gets_only_the_guidance_of_the_kinds_present(tmp_path):
    r = owner_day(tmp_path, ts(2026, 11, 17, 9, 0), mail_box=FakeMail([mail("1", "Invoice")]))
    r.sit.sources.append(Outside(Section("Money", ["credit 30,000 KRW"], content=("finance",))))
    r.agent.on("situation", lambda tools, user: "")
    await r.p.tick()
    call = calls(r, "situation")[-1]
    assert "Kind of data: mail." in call["user"] and "Kind of data: finance." in call["user"]
    assert "# How to read each kind of source" in call["system"] and "## mail" in call["system"] and "## finance" in call["system"]
    assert "## messages" not in call["system"] and "## encrypted_chat" not in call["system"]


async def test_the_send_pass_research_gets_the_guidance_too(tmp_path):
    r = owner_day(tmp_path, ts(2026, 11, 17, 12, 0), mail_box=FakeMail([mail("1", "Invoice")]))
    r.agent.on("situation", lambda tools, user: "")
    await r.p.tick()
    assert kinds_of_passes(r) == ["send"]
    assert "## mail" in calls(r, "situation")[-1]["system"]


# ---------------------------------------------------------------------------------------------------------------- levels
async def test_every_watch_proposal_needs_a_level_and_urgent_is_gone(tmp_path):
    box = FakeMail([mail("1", "Invoice due")])
    r = owner_day(tmp_path, ts(2026, 11, 17, 9, 0), mail_box=box)
    seen: dict = {}

    async def watch(tools, user):
        seen["schema"] = tools["propose_help"].parameters
        h = tools["propose_help"].handler
        ev = [label_of(user, "Invoice due")]
        seen["none"] = await h({"key": "a", "title": "A", "prepared": "p", "evidence": ev})
        seen["ok"] = await h({"key": "a", "title": "A", "prepared": "p", "evidence": ev, "level": "today"})
    r.agent.on("situation", watch)
    await r.p.tick()
    props = seen["schema"]["properties"]
    assert props["level"]["enum"] == ["regular", "today", "now"] and "level" in seen["schema"]["required"]
    assert "urgent" not in props
    assert seen["none"].startswith("Error: level is required")
    assert seen["ok"].startswith("saved: a")
    assert [x["level"] for x in rows(r, "a")] == ["today"]

    box.items = [mail("2", "Other")]
    r.clock.t = ts(2026, 11, 17, 12, 0)

    async def send_pass(tools, user):
        seen["send"] = tools["propose_help"].parameters
    r.agent.on("situation", send_pass)
    await r.p.tick()
    assert kinds_of_passes(r)[-1] == "send"
    assert "level" not in seen["send"]["properties"] and "urgent" not in seen["send"]["properties"]


def test_the_watch_prompt_has_the_levels_and_the_unsure_default():
    text = (S.prompts_dir() / "situation_watch.md").read_text(encoding="utf-8")
    for phrase in ("even an hour later", 'choose "today"', "never from alarming words", "a deadline, bill or renewal that is not due today",
                   "not that it is sent"):
        assert phrase in text, phrase
    assert "waiting until the next send time would cost" not in text
    assert "a fraud or security alert from the service itself" in text   # owner 2026-11-20: a real fraud/security alert is "now"


async def test_level_now_goes_out_at_once_and_the_handoff_says_when_not_whether(tmp_path):
    box = FakeMail([mail("1", "오늘 14:00 미팅 취소")])
    r = owner_day(tmp_path, ts(2026, 11, 17, 13, 0), mail_box=box)
    r.agent.on("situation", lambda tools, user: tools["propose_help"].handler(
        {"key": "cancel", "title": "14:00 미팅 취소", "prepared": "취소됨.", "level": "now", "evidence": [label_of(user, "미팅 취소")]}))
    r.agent.on("situation_write", lambda tools, user: "14시 미팅이 취소됐어요.")
    await r.p.tick()
    assert [s["urgency"] for s in r.sent] == ["지금"]
    writes = calls(r, "situation_write")
    assert len(writes) == 1 and "The level decides when this reaches you, not that it must be sent" in writes[0]["user"]
    assert calls(r, "situation_select") == []

    box.items = [mail("2", "오늘 15:00 다른 일")]
    r.clock.t = ts(2026, 11, 17, 15, 0)
    r.agent.on("situation", lambda tools, user: tools["propose_help"].handler(
        {"key": "other", "title": "15:00 다른 일", "prepared": "확인함.", "level": "now", "evidence": [label_of(user, "다른 일")]}))
    r.agent.on("situation_write", lambda tools, user: "NO_REPLY 이미 알고 있음")
    await r.p.tick()
    assert len(r.sent) == 1 and [x["status"] for x in rows(r, "other")] == ["dropped"]


async def test_a_calendar_change_alone_wakes_the_watch_and_a_gone_event_is_listed(tmp_path):
    ev = [{"id": "e1", "title": "치과", "start": ts(2026, 11, 17, 15, 0)}, {"id": "e2", "title": "회의", "start": ts(2026, 11, 17, 17, 0)}]
    r = owner_day(tmp_path, ts(2026, 11, 17, 9, 0), events=ev)
    r.agent.on("situation", lambda tools, user: "")
    await r.p.tick()                                                          # the baseline: no model call
    assert calls(r, "situation") == [] and r.sit.passes()[0]["status"] == "no_change"

    r.events[0]["start"] = ts(2026, 11, 17, 15, 30)                           # moved
    r.clock.t = ts(2026, 11, 17, 10, 0)
    await r.p.tick()
    first = calls(r, "situation")
    assert len(first) == 1 and "Calendar: changed since the last look" in first[0]["user"]
    assert "치과 — NEW or CHANGED" in first[0]["user"]

    r.events[:] = [e for e in r.events if e["id"] != "e2"]                    # removed
    r.clock.t = ts(2026, 11, 17, 11, 0)
    await r.p.tick()
    second = calls(r, "situation")
    assert len(second) == 2 and "The event at 2026-11-17 17:00 is no longer on the calendar" in second[1]["user"]

    r.clock.t = ts(2026, 11, 17, 11, 30)                                      # nothing changed, not a send time
    assert await r.p.tick() is None
    assert len(calls(r, "situation")) == 2


async def test_a_today_item_goes_in_the_today_message_and_a_regular_one_is_not_raised(tmp_path):
    box = FakeMail([mail("1", "미팅 준비 요청"), mail("2", "자료 요청")])
    r = owner_day(tmp_path, ts(2026, 11, 17, 11, 0), mail_box=box)

    async def watch(tools, user):
        h = tools["propose_help"].handler
        await h({"key": "a", "title": "A", "prepared": "p", "level": "today", "evidence": [label_of(user, "미팅 준비")]})
        await h({"key": "b", "title": "B", "prepared": "p", "level": "regular", "evidence": [label_of(user, "자료 요청")]})
    r.agent.on("situation", watch)
    await r.p.tick()
    got: list[str] = []

    async def pick(tools, user):
        s = tools["select_help"].handler
        got.append(await s({"items": ["b"], "priority": "today", "handoff": "h"}))
        got.append(await s({"items": ["a"], "priority": "regular", "handoff": "h"}))
        got.append(await s({"items": ["a"], "priority": "today", "handoff": "h"}))
        got.append(await s({"items": ["b"], "priority": "regular", "handoff": "h"}))
        return ""
    r.agent.on("situation", lambda tools, user: "")
    r.agent.on("situation_select", pick)
    r.agent.on("situation_write", lambda tools, user: "확인해 주세요.")
    r.clock.t = ts(2026, 11, 17, 12, 0)
    await r.p.tick()
    assert got[0].startswith("Error: every item here came in as regular")
    assert got[1].startswith("Error: 'a' came in as today")
    assert got[2].startswith("selected 1") and got[3].startswith("selected 1")
    sel = calls(r, "situation_select")[-1]["user"]
    assert "Level: today (given by the hourly watch" in sel
    assert r.agent.count("situation_write") == 2


async def test_a_research_update_keeps_the_level_and_adds_kinds(tmp_path):
    box = FakeMail([mail("1", "미팅 준비 요청")])
    r = owner_day(tmp_path, ts(2026, 11, 17, 11, 0), mail_box=box)
    r.say("user", "무언가", ts(2026, 11, 17, 10, 55))
    r.agent.on("situation", lambda tools, user: tools["propose_help"].handler(
        {"key": "a", "title": "A", "prepared": "p", "level": "today", "evidence": [label_of(user, "미팅 준비")]}))
    await r.p.tick()
    assert rows(r, "a")[-1]["level"] == "today" and rows(r, "a")[-1]["kinds"] == '["mail"]'

    r.clock.t = ts(2026, 11, 17, 12, 0)
    r.agent.on("situation", lambda tools, user: tools["propose_help"].handler(
        {"key": "a", "title": "A2", "prepared": "p2", "evidence": [label_of(user, "무언가")]}))
    r.agent.on("situation_select", lambda tools, user: "")
    await r.p.tick()
    last = rows(r, "a")[-1]
    assert last["level"] == "today" and last["kinds"] == '["mail"]' and last["status"] == "prepared"


async def test_the_writer_gets_the_guidance_of_the_kinds_behind_its_items(tmp_path):
    r = owner_day(tmp_path, ts(2026, 11, 17, 13, 0))
    r.sit.sources.append(Outside(Section("Chats", ["a private matter comes up in a chat"], content=("encrypted_chat",))))

    async def watch(tools, user):
        return await tools["propose_help"].handler(
            {"key": "chat", "title": "Chat", "prepared": "Someone needs an answer within the hour.", "level": "now",
             "evidence": [label_of(user, "private matter")]})
    r.agent.on("situation", watch)
    r.agent.on("situation_write", lambda tools, user: "채팅에서 답이 필요한 일이 있어요.")
    await r.p.tick()
    write = calls(r, "situation_write")[-1]["user"]
    assert "## encrypted_chat" in write and "Never quote them" in write


def test_the_new_prompts_name_no_service():
    from assistant import catalog, mcp_oauth, oauth_connect, presets
    from assistant.situation_reads import content_kinds
    ids = {s.id.lower() for s in catalog.CATALOG} | {k.lower() for k in presets.KEYS} | {k.lower() for k in mcp_oauth.SERVICES} | {
        k.lower() for k in oauth_connect.PROVIDERS}
    # plain English words that are also a connector id here (a word, not a service, in these prompts)
    plain = {"close", "make", "health", "linear", "threads", "places", "monday", "wise", "ramp", "expo", "neon", "mercury", "sanity", "resend",
             "notion", "zoom", "lucid", "otter", "apollo", "framer", "contacts", "dialer", "messages", "notifications"}
    texts = [(S.prompts_dir() / "situation_watch.md").read_text(encoding="utf-8")]
    texts += [(S.prompts_dir() / "situation_kinds" / f"{k}.md").read_text(encoding="utf-8") for k in content_kinds()]
    words = {w.lower() for text in texts for w in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text)}
    assert (words & ids) - plain == set()


def test_looks_list_the_calendar_every_hour(tmp_path):
    r = owner_day(tmp_path, ts(2026, 11, 17, 10, 0), events=[])
    hourly = next(g for g in r.sit.looks(r.clock.t) if g["when"] == "hourly")
    assert {"id": "calendar", "name": "", "kind": "section"} in hourly["covers"]


# ---------------------------------------------------------------------------------------------------------------- owner decisions (2026-11-20 15:00)
def _pause(r: Rig) -> None:
    """Three proactive messages the owner did not answer (unanswered_limit 3): the no-answer pause (18 hours) starts now."""
    for _ in range(3):
        r.p._count_unanswered(r.clock.t - 600)
    assert r.p.hold_reason(r.clock.t) == "waiting_for_reply"


async def test_an_urgent_item_goes_out_during_the_no_answer_pause_and_an_ordinary_one_waits(tmp_path):
    box = FakeMail([mail("1", "오늘 14:00 미팅 취소")])
    r = owner_day(tmp_path, ts(2026, 11, 17, 13, 0), mail_box=box)
    _pause(r)
    r.agent.on("situation", lambda tools, user: tools["propose_help"].handler(
        {"key": "cancel", "title": "14:00 미팅 취소", "prepared": "취소됨.", "level": "now", "evidence": [label_of(user, "미팅 취소")]}))
    r.agent.on("situation_write", lambda tools, user: "14시 미팅이 취소됐어요.")
    await r.p.tick()
    assert [s["urgency"] for s in r.sent] == ["지금"]                       # the pause holds ordinary messages, not this one
    assert calls(r, "situation") and kinds_of_passes(r) == ["watch"]        # the watch went on during the pause

    box.items = [mail("2", "다음 주 일정 문의")]
    r.clock.t = ts(2026, 11, 17, 16, 0)                                      # a send time, still in the pause
    r.agent.on("situation", lambda tools, user: tools["propose_help"].handler(
        {"key": "ask", "title": "다음 주 일정 문의", "prepared": "답장이 필요함.", "level": "regular", "evidence": [label_of(user, "일정 문의")]}))
    r.agent.on("situation_write", lambda tools, user: "다음 주 일정 문의가 왔어요.")
    await r.p.tick()
    assert len(r.sent) == 1                                                  # no send pass in the pause: the ordinary item waits
    assert [i["key"] for i in r.items("prepared")] == ["ask"] and len(calls(r, "situation_write")) == 1   # only the urgent message was written


def test_the_prompts_let_an_urgent_item_override_a_dont_tell_me_rule(tmp_path):
    watch = (S.prompts_dir() / "situation_watch.md").read_text(encoding="utf-8")
    write = (S.prompts_dir() / "situation_write.md").read_text(encoding="utf-8")
    assert 'except for a "now" item' in watch and "filters routine news" in watch
    assert 'a rule against routine news does not rule out a "now" handoff' in write
    assert "rules it out" in write                                            # the other levels still obey the rule
