"""How the day's sends are counted (owner 2026-11-19): one send counts once toward "at most N a day", however many messages or items it
carried; the day is the contact window's day (a window past midnight keeps its evening's day)."""

from __future__ import annotations

from situation_fake import Rig, label_of, ts

from assistant.proactive import SENT_KEEP, Candidate


def sent(r: Rig, key: str, urgency: str, delivery: str = "", at: float | None = None) -> None:
    if at is not None:
        r.clock.t = at
    r.p._record(Candidate(key=key, source="situation", title=key), "sent", "r", urgency=urgency, keep=SENT_KEEP, delivery=delivery)


def test_one_delivery_counts_once_whatever_it_carried(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 12, 0))
    sent(r, "a", "오늘", "situation:1")
    sent(r, "b", "보통", "situation:1")                                    # the same send: a second message
    sent(r, "c", "지금", "situation:2:now")
    sent(r, "d", "지금", "situation:2:now")
    assert r.p.today_counts() == {"보통": 0, "오늘": 1, "지금": 1}
    sent(r, "e", "보통", "situation:3")
    assert r.p.today_counts() == {"보통": 1, "오늘": 1, "지금": 1} and r.p.room_today()
    sent(r, "f", "보통")                                                   # no delivery id (candidate pass, checklist): one each, as before
    sent(r, "g", "보통")
    assert r.p.today_counts()["보통"] == 3 and not r.p.room_today()


def test_the_day_is_the_contact_windows_day(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 20, 0))
    r.p.update(wake_start="10:00", wake_end="02:00")
    assert r.p.window_day(ts(2026, 11, 18, 1, 30)) == "2026-11-17"         # past midnight: still the evening's day
    assert r.p.window_day(ts(2026, 11, 18, 3, 0)) == "2026-11-18"
    sent(r, "a", "보통", "situation:1", at=ts(2026, 11, 17, 20, 0))
    sent(r, "b", "보통", "situation:2", at=ts(2026, 11, 17, 23, 30))
    sent(r, "c", "오늘", "situation:3", at=ts(2026, 11, 18, 1, 0))
    assert r.p.today_counts() == {"보통": 2, "오늘": 1, "지금": 0} and not r.p.room_today()   # 01:00 belongs to the 17th's window
    r.clock.t = ts(2026, 11, 18, 10, 0)
    assert r.p.today_counts() == {"보통": 0, "오늘": 0, "지금": 0}


async def test_a_send_with_a_today_and_a_regular_message_counts_once(tmp_path):
    r = Rig(tmp_path, ts(2026, 11, 17, 10, 9))
    r.say("user", "회의 자료랑 택배 챙겨야 해", r.clock.t - 600)

    async def research(tools, user):
        for k in ("a", "b"):
            await tools["propose_help"].handler({"key": k, "title": k, "prepared": "p", "evidence": [label_of(user, "택배")],
                                                 "useful_until": "2026-11-19"})

    async def select(tools, user):
        await tools["select_help"].handler({"items": ["a"], "priority": "today", "handoff": "h"})
        await tools["select_help"].handler({"items": ["b"], "priority": "regular", "handoff": "h"})
    r.agent.on("situation", research)
    r.agent.on("situation_select", select)
    r.agent.on("situation_write", lambda tools, user: "메시지")
    r.p.update(daily_regular=1, daily_total=1)
    await r.p.run()
    assert [s["urgency"] for s in r.sent] == ["오늘", "보통"]                 # two messages, one send: the second joins it
    assert r.p.today_counts() == {"보통": 0, "오늘": 1, "지금": 0} and sum(r.p.public()["today"].values()) == 1
    assert r.p.guard()["unanswered"] == 1 and not r.p.room_today()
