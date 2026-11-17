"""The hourly situation research (situation.py): tick timing, the research switch, no change = no model call, the context pack, the read-only
tool role, proposals (dedupe, told / dropped refused), selection limits, expiry."""

from __future__ import annotations

import json

import pytest
from situation_fake import ACTING, READS, DeviceSource, FakeMail, Rig, app_tools, label_of, mail, ts

from assistant import tool_roles
from assistant.effects import TOOL_FAMILY
from assistant.situation import MAX_SELECTED, Section


def rig(tmp_path, t=None, **kw) -> Rig:
    return Rig(tmp_path, t or ts(2026, 11, 17, 9, 9), **kw)


async def propose(r: Rig, key="k1", **kw):
    """The research script of a pass: one proposal, citing the first conversation entry."""
    async def script(tools, user):
        return await tools["propose_help"].handler({"key": key, "title": "T " + key, "prepared": "Why now. Checked X. Unknown Y.",
                                                   "evidence": kw.pop("evidence", ["source:1"]), **kw})
    r.agent.on("situation", script)


# ---------------------------------------------------------------------------------------------------------------- timing
async def test_the_pass_runs_at_minute_9_of_each_hour_inside_waking_hours_and_once_at_wake_start(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    assert (await r.p.tick())["status"] == "done"                           # due at 09:09 (the first pass researches)
    r.advance(20 * 60)
    assert await r.p.tick() is None                                         # 09:29: not due
    r.clock.t = ts(2026, 11, 17, 10, 8)
    assert await r.p.tick() is None                                         # 10:08: the 09:09 slot is done, 10:09 not yet
    r.clock.t = ts(2026, 11, 17, 10, 9)
    assert await r.p.tick() is not None                                     # 10:09
    r.clock.t = ts(2026, 11, 17, 22, 9)
    assert await r.p.tick() is None and r.p.state()["last_pass"]["reason_code"] == "outside_waking_hours"   # asleep
    assert r.sit.due(ts(2026, 11, 18, 6, 59)) is True                       # the slot at 06:09 is due, but not awake yet
    r.clock.t = ts(2026, 11, 18, 6, 59)
    assert await r.p.tick() is None
    r.clock.t = ts(2026, 11, 18, 7, 0)
    assert await r.p.tick() is not None                                     # the wake-start pass
    assert r.sit.slot_start(ts(2026, 11, 17, 9, 8)) == ts(2026, 11, 17, 8, 9)
    assert r.sit.slot_start(ts(2026, 11, 17, 0, 5)) == ts(2026, 11, 16, 23, 9)


async def test_the_pace_is_a_setting(tmp_path):
    r = rig(tmp_path)
    r.p.update(research_every_minutes=120)
    assert r.sit.slot_start(ts(2026, 11, 17, 11, 20)) == ts(2026, 11, 17, 10, 9)
    for bad in (5, 5000):
        with pytest.raises(ValueError):
            r.p.update(research_every_minutes=bad)
    assert r.p.config()["research"] is True and r.p.config()["research_every_minutes"] == 120


async def test_research_on_replaces_the_old_pass_and_off_brings_it_back(tmp_path):
    r = rig(tmp_path, mail_box=FakeMail([mail("1", "Hello")]))
    assert (await r.p.tick())["status"] == "done"                           # on: the research pass runs, no old classification (its model was not asked)
    assert r.judged == 0 and r.agent.count("situation") == 1
    r.p.update(research=False)
    r.mail.items = [mail("2", "Again")]
    r.advance(3 * 3600)
    out = await r.p.tick()
    assert out["status"] == "done" and r.judged == 1                        # off: the old pass reads the mail and asks its model


# ---------------------------------------------------------------------------------------------------------------- no change
async def test_the_same_pack_twice_makes_no_second_model_call(tmp_path):
    r = rig(tmp_path)
    r.say("user", "회의 준비해야 해", r.clock.t - 600)
    r.agent.on("situation", lambda tools, user: "nothing to propose")
    first = await r.p.run()
    assert first["status"] == "done" and r.agent.count() == 1
    r.advance(3600)
    second = await r.p.run()
    assert second["status"] == "nothing_new" and r.agent.count() == 1       # the same pack: no model call
    assert [p["status"] for p in r.sit.passes()] == ["no_change", "nothing"]
    r.advance(3600)
    r.say("user", "아, 그리고 우유 사야 해", r.clock.t - 60)
    assert (await r.p.run())["status"] == "done" and r.agent.count() == 2   # something changed: researched again


async def test_an_open_item_and_an_unchanged_pack_do_not_research_again(tmp_path):
    r = rig(tmp_path)
    r.say("user", "내일 일정 있어", r.clock.t - 600)
    await propose(r, useful_until="2026-11-19")
    await r.p.run()
    assert r.agent.count("situation") == 1 and r.agent.count("situation_select") == 1 and len(r.items("prepared")) == 1
    r.advance(3600)
    await r.p.run()
    r.advance(3600)
    await r.p.run()
    assert r.agent.count("situation") == 1          # the open item is not a change: the pack is the same, no research
    assert r.agent.count("situation_select") == 1   # nothing changed for the selector either: it is not asked again
    r.advance(3600)
    r.say("user", "새 소식이 있어", r.clock.t - 60)
    await r.p.run()
    assert r.agent.count("situation") == 2 and r.agent.count("situation_select") == 2   # something new: both look again


# ---------------------------------------------------------------------------------------------------------------- the pack
async def test_the_pack_has_every_section_split_at_the_checkpoint_with_unique_labels_and_the_owners_rules_inline(tmp_path):
    ev = [{"id": "e1", "title": "치과", "start": ts(2026, 11, 18, 15, 0), "where": "역삼"}]
    r = rig(tmp_path, mail_box=FakeMail([mail("7", "Invoice due")]), events=ev)
    r.sit.sources.append(DeviceSource(["Desktop PC: on"]))
    r.home.write("NOTIFY_PREFS.md", "# NOTIFY_PREFS.md\n\n## Do not tell me\n- 광고\n", by="user")
    r.store.add_goal("영어 공부", "study", "매일 30분")
    r.say("user", "어제 한 말", r.clock.t - 5 * 3600)
    r.say("assistant", "알겠어요", r.clock.t - 5 * 3600 + 5)
    r.say("user", "[Handoff from a job, not from the user] secret", r.clock.t - 4000)
    r.store.set_meta("situation:checkpoint", str(r.clock.t - 3600))
    r.say("user", "방금 한 말", r.clock.t - 600)
    r.p.owner_marks = ("[Handoff from a job, not from the user]",)
    pack = await r.sit.build_pack(r.clock.t, r.clock.t - 3600, r.clock.t - 3600)
    text = pack.render()
    for head in ("## Clock", "## Conversation: new since the checkpoint", "## Conversation: earlier in the last 24 hours", "## Mail: new since the checkpoint",
                 "## Calendar: next 7 days", "## Goals and follow-through", "## Schedules and watchers", "## Home devices",
                 "## Memory: added or revised since the checkpoint", "## Told:", "## Dropped:", "## Prepared:", "## Preferences"):
        assert head in text, head
    new = text.split("## Conversation: new", 1)[1].split("## Conversation: earlier", 1)[0]
    old = text.split("## Conversation: earlier", 1)[1].split("## Mail:", 1)[0]
    assert "방금 한 말" in new and "어제 한 말" not in new                  # New = after the checkpoint
    assert "어제 한 말" in old and "알겠어요" in old and "secret" not in text   # earlier ones as context; a hidden job row is left out
    assert "Invoice due" in text and "치과" in text and "Desktop PC: on" in text
    assert "- 광고" in text and "law" in text                               # the owner's saved rules, inline and marked as theirs
    labels = [int(n) for n in __import__("re").findall(r"\[source:(\d+)\]", text)]
    assert labels == sorted(set(labels)) and set(labels) == set(pack.index)   # unique, and every one is in the index


async def test_a_section_is_capped_and_the_newest_conversation_is_kept(tmp_path):
    r = rig(tmp_path)
    for i in range(60):
        r.say("user", f"message {i} " + "x" * 1400, r.clock.t - 3000 + i)
    pack = await r.sit.build_pack(r.clock.t, r.clock.t - 3600, None)
    text = pack.render()
    conv = text.split("## Conversation: new", 1)[1].split("## Conversation: earlier", 1)[0]
    assert len(conv) < 31_500 and "message 59 " in conv and "message 0 " not in conv


async def test_changed_calendar_events_are_marked_after_the_first_pass(tmp_path):
    ev = [{"id": "e1", "title": "치과", "start": ts(2026, 11, 18, 15, 0)}]
    r = rig(tmp_path, events=ev)
    r.agent.on("situation", lambda tools, user: "")
    await r.p.run()
    assert "NEW or CHANGED" not in (await r.sit.build_pack(r.clock.t, 0, 1)).render()
    r.events.append({"id": "e2", "title": "동창회", "start": ts(2026, 11, 19, 19, 0)})
    r.events[0]["start"] = ts(2026, 11, 18, 16, 0)
    text = (await r.sit.build_pack(r.clock.t, 0, 1)).render()
    assert text.count("NEW or CHANGED") == 2


async def test_memory_goals_schedules_and_the_told_ledger_are_in_the_pack(tmp_path):
    r = rig(tmp_path)
    r.store._run("INSERT INTO memory_claims(claim_id, run_id, kind, salience, text, quote, speaker, handle, status, source_path, source_line, "
                 "created_at, updated_at) VALUES('c1','r','fact','high','The owner is allergic to peanuts','q','user','h','active','memory/x.md',1,?,?)",
                 (r.clock.t - 60, r.clock.t - 60))
    r.store.add_goal("영어 공부", "study", "매일 30분")
    r.p._record(__import__("assistant.proactive", fromlist=["Candidate"]).Candidate("k", "mail", "Parcel"), "sent", "r", urgency="보통",
                message="택배가 도착했어요", keep=86400)
    text = (await r.sit.build_pack(r.clock.t, r.clock.t - 3600, r.clock.t - 3600)).render()
    assert "allergic to peanuts" in text and "택배가 도착했어요" in text
    assert "영어 공부" in text and "CHANGED since the checkpoint" in text


# ---------------------------------------------------------------------------------------------------------------- the research role
def test_the_research_role_has_no_acting_tool():
    got = {t.name for t in tool_roles.research_tools(app_tools())}
    assert got and got <= set(READS)
    for name in ACTING:
        assert name not in got and name not in tool_roles.RESEARCH_TOOLS, name
    for name in tool_roles.RESEARCH_TOOLS:   # every connector tool in it reads, by the effect kinds (not by words)
        if name in TOOL_FAMILY:
            assert TOOL_FAMILY[name][1] == ("read",), name
    for name in ("email_read", "email_list", "gcal_events", "web_search", "read_earlier_messages", "memory_explain"):
        assert name in tool_roles.RESEARCH_TOOLS
    assert not any(n.startswith(("android_", "phone_", "browser_")) for n in tool_roles.RESEARCH_TOOLS)
    assert not tool_roles.RESEARCH_TOOLS & tool_roles.PAY_TOOLS
    assert set(tool_roles.RESEARCH_OWN) == {"propose_help", "record_owner_preference"} and set(tool_roles.SELECTION_OWN) == {"select_help", "drop_help"}


async def test_the_research_turn_is_given_reads_and_its_own_tools_and_a_budget(tmp_path):
    r = rig(tmp_path)
    r.say("user", "무언가", r.clock.t - 60)
    r.agent.on("situation", lambda tools, user: "")
    await r.p.run()
    call = r.agent.calls[0]
    assert set(call["tools"]) == set(READS) | {"propose_help", "record_owner_preference"}
    assert not set(call["tools"]) & set(ACTING)
    assert call["steps"] == 20 and call["seconds"] == 240 and call["scope"] == "situation"
    assert "propose nothing" in call["system"] and "[source:" in call["user"]


# ---------------------------------------------------------------------------------------------------------------- propose_help
async def test_propose_help_dedupes_by_key_and_checks_evidence(tmp_path):
    r = rig(tmp_path)
    r.say("user", "무언가", r.clock.t - 60)
    seen = []

    async def script(tools, user):
        h = tools["propose_help"].handler
        seen.append(await h({"key": "a", "title": "A", "prepared": "p1", "evidence": ["source:1"], "useful_until": "2026-11-18 12:00"}))
        seen.append(await h({"key": "a", "title": "A2", "prepared": "p2", "evidence": ["[source:1]"]}))     # the same key: updated
        seen.append(await h({"key": "b", "title": "B", "prepared": "p", "evidence": []}))                   # no evidence
        seen.append(await h({"key": "b", "title": "B", "prepared": "p", "evidence": ["source:999"]}))       # a label not in this pack
        seen.append(await h({"key": "b", "title": "B", "prepared": "p", "evidence": ["source:1"], "useful_until": "2026-11-01"}))   # past
        seen.append(await h({"key": "b", "title": "B", "prepared": "p", "evidence": ["source:1"], "useful_until": "soon"}))         # unreadable
        seen.append(await h({"key": "c", "title": "C", "prepared": "p", "evidence": ["https://example.org/x"]}))
    r.agent.on("situation", script)
    await r.p.run()
    assert seen[0] == "saved: a" and seen[1].startswith("updated: a")
    assert all(s.startswith("Error:") for s in seen[2:6]) and seen[6].startswith("saved: c")
    rows = r.items("prepared")
    assert sorted(i["key"] for i in rows) == ["a", "c"]
    a = next(i for i in rows if i["key"] == "a")
    assert a["title"] == "A2" and a["evidence"] == ["source:1"] and a["useful_until"] == ts(2026, 11, 18, 12, 0)   # updated in place, no duplicate
    c = next(i for i in rows if i["key"] == "c")
    assert c["useful_until"] is None and c["expires_at"] == r.clock.t + 48 * 3600


async def test_a_told_or_dropped_key_is_refused_without_a_new_fact(tmp_path):
    r = rig(tmp_path)
    r.say("user", "무언가", r.clock.t - 60)
    out = []

    async def first(tools, user):
        await tools["propose_help"].handler({"key": "a", "title": "A", "prepared": "p", "evidence": ["source:1"]})
        await tools["propose_help"].handler({"key": "b", "title": "B", "prepared": "p", "evidence": ["source:1"]})
    r.agent.on("situation", first)
    r.agent.on("situation_select", lambda tools, user: tools["drop_help"].handler({"item": "a", "why": "already handled"}))
    await r.p.run()
    r.sit.store._run("UPDATE help_items SET status = 'told', reason = 'told as regular' WHERE key = 'b'")
    assert [i["status"] for i in r.items() if i["key"] == "a"] == ["dropped"]
    r.say("user", "또 무언가", r.clock.t + 1)
    r.advance(3600)

    async def again(tools, user):
        h = tools["propose_help"].handler
        out.append(await h({"key": "a", "title": "A", "prepared": "p", "evidence": ["source:1"]}))
        out.append(await h({"key": "b", "title": "B", "prepared": "p", "evidence": ["source:1"]}))
        out.append(await h({"key": "a", "title": "A", "prepared": "p", "evidence": ["source:1"], "new_fact": "the owner said the clinic moved it"}))
    r.agent.on("situation", again)
    r.agent.on("situation_select", lambda tools, user: "")
    await r.p.run()
    assert out[0].startswith("Refused") and "dropped" in out[0] and "already handled" in out[0]
    assert out[1].startswith("Refused") and "told" in out[1]
    assert out[2].startswith("saved: a")                                           # a new fact reopens it
    assert [i["key"] for i in r.items("prepared")] == ["a"]
    # after 7 days a told key may be proposed again
    r.advance(8 * 86400)
    r.say("user", "한참 뒤", r.clock.t - 60)
    out.clear()
    r.agent.on("situation", lambda tools, user: tools["propose_help"].handler({"key": "b", "title": "B", "prepared": "p", "evidence": ["source:1"]}))
    await r.p.run()
    assert [i["key"] for i in r.items("prepared")] == ["b"]


async def test_the_owners_preference_is_written_only_from_their_own_words(tmp_path):
    r = rig(tmp_path)
    r.say("user", "택배 알림은 보내지 마", r.clock.t - 120)
    seen = []

    async def script(tools, user):
        t = tools["record_owner_preference"].handler
        seen.append(await t({"line": "Never tell me about parcels", "owner_words": "택배 알림은 보내지 마"}))
        seen.append(await t({"line": "Never tell me about news", "owner_words": "뉴스도 보내지 마"}))     # the owner did not say this
        seen.append(await t({"line": "Never tell me about parcels", "owner_words": "택배 알림은 보내지 마"}))   # twice: once
    r.agent.on("situation", script)
    await r.p.run()
    assert seen[0] == "noted" and seen[1].startswith("Error") and seen[2] == "Already noted."
    text = r.home.read("NOTIFY_PREFS.md")
    assert text.count("parcels") == 1 and "news" not in text
    assert "parcels" not in r.p.prefs()      # the owner's saved copy is only theirs: Kelo's note is read as a note


# ---------------------------------------------------------------------------------------------------------------- selection
async def _two_open(r: Rig, n: int = 2):
    r.say("user", "무언가", r.clock.t - 60)

    async def script(tools, user):
        for i in range(n):
            await tools["propose_help"].handler({"key": f"k{i}", "title": f"T{i}", "prepared": "p", "evidence": ["source:1"], "useful_until": "2026-11-20"})
    r.agent.on("situation", script)


async def test_selection_takes_at_most_one_today_and_one_regular_and_at_most_eight_items(tmp_path):
    r = rig(tmp_path)
    await _two_open(r, 10)
    seen = []

    async def pick(tools, user):
        s = tools["select_help"].handler
        seen.append(await s({"items": ["k0"], "priority": "today", "handoff": "h"}))
        seen.append(await s({"items": ["k1"], "priority": "today", "handoff": "h"}))                      # a second today message
        seen.append(await s({"items": [f"k{i}" for i in range(2, 10)], "priority": "regular", "handoff": "h"}))   # 8 + 1 > 8
        seen.append(await s({"items": [f"k{i}" for i in range(2, 9)], "priority": "regular", "handoff": "h"}))   # 7 + 1 = 8
        seen.append(await s({"items": ["k9"], "priority": "regular", "handoff": "h"}))                    # a second regular
        seen.append(await s({"items": ["nope"], "priority": "bogus", "handoff": "h"}))
        seen.append(await s({"items": ["k9"], "priority": "regular", "handoff": ""}))
    r.agent.on("situation_select", pick)
    r.agent.on("situation_write", lambda tools, user: "NO_REPLY not now")
    await r.p.run()
    assert seen[0].startswith("selected 1") and seen[1].startswith("Error") and "at most one" in seen[1]
    assert seen[2].startswith("Error") and "at most 8" in seen[2] and seen[3].startswith("selected 7") and MAX_SELECTED == 8
    assert all(s.startswith("Error") for s in seen[4:])
    assert r.agent.count("situation_write") == 2                              # one message per priority
    assert len(r.items("dropped")) == 8 and len(r.items("prepared")) == 2     # the writer dropped the 8; k1 and k9 stay open


async def test_drop_records_the_reason_and_unselected_items_expire_at_useful_until(tmp_path):
    r = rig(tmp_path)
    await _two_open(r, 2)
    r.agent.on("situation_select", lambda tools, user: tools["drop_help"].handler({"item": "k0", "why": "the owner already did it"}))
    await r.p.run()
    dropped = r.items("dropped")
    assert [d["key"] for d in dropped] == ["k0"] and dropped[0]["reason"] == "the owner already did it"
    assert [x["status"] for x in r.p.log() if x["key"] == "situation:k0"] == ["dropped"]   # in the ledger too, with its reason
    assert [i["key"] for i in r.items("prepared")] == ["k1"]                               # unselected: stays open
    r.clock.t = ts(2026, 11, 20, 12, 0)                                                    # past its useful_until (2026-11-20 end of day) is later
    assert r.sit.expire(r.clock.t) == 0
    r.clock.t = ts(2026, 11, 21, 0, 1)
    assert r.sit.expire(r.clock.t) == 1 and [i["key"] for i in r.items("expired")] == ["k1"] and r.sit.open_items() == []
    assert "Expired" in [x for x in r.p.log() if x["key"] == "situation:k1"][0]["reason"]
    # the pack's "prepared" section no longer lists it
    assert "key k1" not in (await r.sit.build_pack(r.clock.t, 0, None)).render().split("## Prepared:", 1)[1].split("## Preferences", 1)[0]


def test_the_delivery_estimate_names_the_next_opening(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 23, 30))
    est = r.sit.estimate(r.clock.t)
    assert "today: earliest opening 2026-11-18 07:00" in est and "outside waking hours" in est
    r.clock.t = ts(2026, 11, 17, 10, 0)
    assert "a message could go out now" in r.sit.estimate(r.clock.t)


def test_pack_fingerprint_ignores_the_clock_and_the_split():
    from assistant.situation import Pack
    a, b = Pack(), Pack()
    a.add("Clock", ["Now: 10:00"], hashed=False, labelled=False)
    b.add("Clock", ["Now: 11:00"], hashed=False, labelled=False)
    a.add("Mail", ["m1"])
    b.add("Mail", ["m1"])
    assert a.fingerprint() == b.fingerprint()
    b.add("More", ["x"])
    assert a.fingerprint() != b.fingerprint()
    json.dumps(a.index)
    assert Section("t", ["x"]).fingerprint is None


def test_label_helper_finds_a_labelled_entry():
    assert label_of("## S\n[source:3] hello world\n\n[source:4] other", "other") == "source:4"


# ---------------------------------------------------------------------------------------------------------------- delivery and the owner's example
async def test_a_selected_regular_message_goes_through_deliver_as_regular_and_waits_for_the_daily_cap(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 10, 9))
    r.say("user", "계약서 회신 기한을 알려줘", r.clock.t - 600)
    r.p.update(daily_regular=0)                                              # no regular message room today
    await propose(r, "k1", evidence=["source:1"])
    r.agent.on("situation_select", lambda tools, user: tools["select_help"].handler({"items": ["k1"], "priority": "regular", "handoff": "The deadline is tomorrow."}))
    r.agent.on("situation_write", lambda tools, user: "계약서 회신 기한은 내일입니다.")
    await r.p.run()
    assert r.sent == [] and r.agent.count("situation_write") == 0                 # over the cap: nothing written, nothing sent
    assert r.items("prepared")[0]["reason"] == "waiting: over_cap"
    r.p.update(daily_regular=1)
    r.advance(3600)
    await r.p.run()                                                         # the next pass: the room is there, the item goes out as regular
    assert len(r.sent) == 1 and r.sent[0]["urgency"] == "보통" and r.sent[0]["key"] == "situation:k1"
    assert r.sent[0]["message"] == "계약서 회신 기한은 내일입니다." and r.agent.count("situation_write") == 1
    assert [x["status"] for x in r.p.log() if x["key"] == "situation:k1"] == ["sent"]
    assert [i["key"] for i in r.items("told")] == ["k1"]
    pack = (await r.sit.build_pack(r.clock.t, r.clock.t - 3600, r.sit.checkpoint())).render()
    assert "계약서 회신 기한은 내일입니다." in pack.split("## Told:", 1)[1].split("## Dropped", 1)[0]   # the next pass sees it as told


async def test_the_owners_example_a_device_left_on_when_they_go_out_is_found_proposed_chosen_and_told_once(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    r.sit.sources.append(DeviceSource(["Desktop PC: on"]))                  # a connected source: what the smart-home source will later add
    r.say("user", "나 나간다", ts(2026, 11, 17, 9, 5))                      # the owner's words in the New section
    seen: dict[str, str] = {}

    async def research(tools, user):
        seen["user"] = user
        return await tools["propose_help"].handler({
            "key": "desktop-on-while-away", "title": "The desktop is on while the owner is out",
            "prepared": "The owner said they are going out and the desktop is on. Checked: the chat and the device line. Unknown: whether they want it off.",
            "evidence": [label_of(user, "나 나간다"), label_of(user, "Desktop PC")], "suggested_action": "Ask whether to turn the desktop off",
            "useful_until": "2026-11-17 18:00"})
    r.agent.on("situation", research)
    r.agent.on("situation_select", lambda tools, user: tools["select_help"].handler(
        {"items": ["desktop-on-while-away"], "priority": "regular", "handoff": "The owner is out and the desktop is on. Say it once; do nothing."}))
    r.agent.on("situation_write", lambda tools, user: "나가신 것 같은데 데스크톱 PC가 켜져 있어요. 끌까요?")
    await r.p.run()
    assert "나 나간다" in seen["user"] and "Desktop PC: on" in seen["user"] and "## Home devices" in seen["user"]   # the research saw both
    assert len(r.sent) == 1 and r.sent[0]["urgency"] == "보통" and "끌까요" in r.sent[0]["message"]
    assert r.agent.count("situation") == 1

    r.advance(3600)                                                          # an hour later, nothing new: no model call at all
    await r.p.run()
    assert r.agent.count("situation") == 1 and len(r.sent) == 1

    r.advance(600)
    r.say("user", "회의 자료 좀 정리해줘", r.clock.t - 60)                   # something new (after the last pass): the research runs again and must not repeat it
    again: list[str] = []

    async def research_again(tools, user):
        again.append(await tools["propose_help"].handler({"key": "desktop-on-while-away", "title": "The desktop is on",
                                                         "prepared": "Same as before.", "evidence": [label_of(user, "Desktop PC")]}))
    r.agent.on("situation", research_again)
    await r.p.run()
    assert r.agent.count("situation") == 2 and again[0].startswith("Refused") and "told" in again[0]
    assert len(r.sent) == 1


async def test_the_old_pass_does_not_tell_the_device_case(tmp_path):
    r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
    r.p.update(research=False)                                              # the candidate pass of proactive.py, as before
    r.sit.sources.append(DeviceSource(["Desktop PC: on"]))
    r.say("user", "나 나간다", ts(2026, 11, 17, 9, 5))
    await r.p.run()
    assert r.sent == [] and r.agent.count("situation") == 0


def test_the_research_route_shows_the_config_the_passes_and_the_open_items(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from assistant.situation import situation_routes

    async def scenario() -> dict:
        r = rig(tmp_path, ts(2026, 11, 17, 9, 9))
        r.say("user", "회의 자료 좀 정리해줘", r.clock.t - 600)   # the entry the proposal cites (source:1)
        await propose(r, "k1")
        r.agent.on("situation_select", lambda tools, user: "")
        await r.p.run()
        app = FastAPI()
        situation_routes(app, r.sit, lambda: None)
        return TestClient(app).get("/api/proactive/research").json()

    import asyncio
    body = asyncio.run(scenario())
    assert body["research"] is True and body["every_minutes"] == 60 and body["checkpoint"]
    assert body["passes"][0]["status"] in ("selected", "proposed") and body["passes"][0]["proposed"] == 1
    assert [i["key"] for i in body["open"]] == ["k1"] and body["open"][0]["evidence"] == ["source:1"]


def test_the_prompts_come_from_the_content_dir_the_server_image_sets(tmp_path, monkeypatch):
    """2026-11-17: the deployed server (content at /app/content, ASSISTANT_CONTENT_DIR) looked for the prompts in the checkout path and
    every hourly pass failed with FileNotFoundError."""
    from assistant import situation
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "situation_research.md").write_text("from the image", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_CONTENT_DIR", str(tmp_path))
    assert situation.prompts_dir() == tmp_path / "prompts"
    assert (situation.prompts_dir() / "situation_research.md").read_text(encoding="utf-8") == "from the image"
    monkeypatch.delenv("ASSISTANT_CONTENT_DIR")
    assert (situation.prompts_dir() / "situation_research.md").is_file()   # the checkout's own copy
