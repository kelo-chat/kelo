"""Proactive messages (S4): off by default, waking hours, daily caps, urgency rules, the ledger (nothing told twice), the user's
preferences file, and delivery as chat message + notification (fake model, fake clock, fake mail)."""

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant.home import Home
from assistant.proactive import NEVER_HEADINGS, Proactive, find_date, in_waking_hours, keywords, section
from assistant.providers import Done, TextDelta
from assistant.schedules import Schedules
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.watchers import Watchers

from test_server import AUTH, TOKEN, Running

SEOUL = ZoneInfo("Asia/Seoul")


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeMail:
    def __init__(self, items):
        self.items, self.calls, self.asked, self.fail = items, 0, [], False

    def accounts(self):
        return [{"name": "mail-gmail", "provider": "gmail", "label": "Gmail · me@example.org", "address": "me@example.org"}]

    async def arrivals(self, account, cursor, kind="inbox"):
        """Whatever is in `items` counts as arrived since the last look (the real cursor logic is tested in test_mail_arrivals.py)."""
        self.calls += 1
        self.asked.append((account, cursor, kind))
        if kind != "inbox":
            return None
        if self.fail:
            raise OSError("server down")
        return {"rows": [dict(m) for m in self.items], "cursor": {"t": self.calls}}


def mails(*subjects):
    return [{"uid": str(i + 1), "from": "보낸이 <a@example.org>", "subject": s, "date": "Sat, 7 Nov 2026 09:00:00 +0900", "unread": True}
            for i, s in enumerate(subjects)]


class Judge:
    """A fake main model: decide(items) -> [{"n", "urgency", "due"?, "reason", "message"}] for the numbered items it is shown."""

    def __init__(self, decide):
        self.decide, self.requests = decide, []

    async def __call__(self, provider, request, **kw):
        self.requests.append(request)
        body = request.messages[-1]["content"]
        items = [line.split(". ", 1) for line in body.split("Items:\n", 1)[1].splitlines() if ". " in line]
        out = self.decide([(int(n), text) for n, text in items])
        yield TextDelta(json.dumps({"items": out}, ensure_ascii=False))
        yield Done("stop")


def all_as(urgency, **extra):
    return Judge(lambda items: [{"n": n, "urgency": urgency, "reason": "테스트", "message": f"알림 {n}: {t[:20]}", **extra} for n, t in items])


def setup(tmp_path, judge, mail=None, t=None, **kw):
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    store.add_message("user", "안녕")          # the owner has written here once (nothing proactive runs before that)
    home = Home(tmp_path / "home")
    clock = Clock(t or ts(2026, 11, 7, 9, 0))
    sent = []

    async def deliver(item):
        sent.append(item)
        return len(sent)

    p = Proactive(store, home, stream_fn=judge, model=lambda: ("p", "m"), deliver=deliver, mail=mail, clock=clock, **kw)
    p.update(enabled=True)
    return store, home, clock, p, sent


# ---------------------------------------------------------------------------------------------------------- helpers
def test_waking_hours_dates_and_preference_sections():
    assert in_waking_hours(ts(2026, 11, 7, 7, 0), "07:00", "22:00", SEOUL)
    assert not in_waking_hours(ts(2026, 11, 7, 22, 0), "07:00", "22:00", SEOUL)
    assert not in_waking_hours(ts(2026, 11, 7, 6, 59), "07:00", "22:00", SEOUL)
    assert in_waking_hours(ts(2026, 11, 7, 1, 0), "22:00", "02:00", SEOUL) and not in_waking_hours(ts(2026, 11, 7, 3, 0), "22:00", "02:00", SEOUL)
    now = ts(2026, 11, 7, 9, 0)
    assert find_date("치과 예약 11월 9일 오후 3시", now, SEOUL) == ("2026-11-09", ts(2026, 11, 9, 15, 0))
    assert find_date("보고서 2026-11-08 마감", now, SEOUL) == ("2026-11-08", None)
    assert find_date("회의 2026-11-07 14:30", now, SEOUL) == ("2026-11-07", ts(2026, 11, 7, 14, 30))
    assert find_date("1월 3일 여행", now, SEOUL)[0] == "2027-01-03"            # a date without a year that has passed: next year
    assert find_date("날짜 없음", now, SEOUL) is None
    prefs = "# NOTIFY_PREFS.md\n\n## 알려 줘\n- 택배\n\n## 알리지 마\n- 광고\n- 뉴스레터는 절대 알려주지 말고 그냥 넘어가 줘 제발\n\n## 시간대\n"
    assert section(prefs, "알려 줘") == "- 택배" and keywords(section(prefs, "알리지 마")) == ["광고"]
    assert keywords(section(prefs, *NEVER_HEADINGS)) == ["광고"]                    # an older file with Korean headings
    english = "# NOTIFY_PREFS.md\n\n## Tell me\n- parcels\n\n## Do not tell me\n- ads\n\n## When\n\n## How\n"
    assert keywords(section(english, *NEVER_HEADINGS)) == ["ads"] and section(english, "Missing", "Tell me") == "- parcels"
    assert section(english, "Missing") == ""


# ---------------------------------------------------------------------------------------------------------- switch and hours
async def test_it_is_off_by_default_and_runs_only_in_waking_hours(tmp_path):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    store.add_message("user", "안녕")
    judge = all_as("보통")
    p = Proactive(store, Home(tmp_path / "home"), stream_fn=judge, model=lambda: ("p", "m"), mail=FakeMail(mails("a")),
                  clock=Clock(ts(2026, 11, 7, 9, 0)))
    assert p.config()["enabled"] is False and await p.tick() is None and judge.requests == []
    p.update(enabled=True, wake_start="08:00", wake_end="21:30")
    p.clock.t = ts(2026, 11, 7, 7, 30)
    assert await p.tick() is None and p.mail.calls == 0                         # asleep: not even the mailbox is opened
    p.clock.t = ts(2026, 11, 7, 8, 1)
    assert (await p.tick())["status"] == "done"
    p.clock.t += 30 * 60
    assert await p.tick() is None                                                # not due yet (every 120 minutes)
    p.clock.t += 95 * 60
    assert (await p.tick())["status"] == "nothing_new" and len(judge.requests) == 1   # the same mail again: no model call
    for bad in ({"wake_start": "7"}, {"every_minutes": 5}, {"daily_regular": 4, "daily_total": 3}, {"daily_now": 99}):
        with pytest.raises(ValueError):
            p.update(**bad)


async def test_the_first_pass_of_the_day_runs_at_wake_up(tmp_path):
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), FakeMail(mails("a")), t=ts(2026, 11, 7, 21, 0))
    assert (await p.tick())["status"] == "done"
    clock.t = ts(2026, 11, 8, 6, 59)
    assert await p.tick() is None
    clock.t = ts(2026, 11, 8, 7, 0)                     # 10 hours later but the period is 2 h anyway; wake-up starts a pass
    p.mail.items = mails("a", "b")
    assert (await p.tick())["status"] == "done"


# ---------------------------------------------------------------------------------------------------------- caps, urgency
async def test_daily_caps_two_regular_three_with_today_and_now_goes_out_at_once(tmp_path):
    mail = FakeMail(mails("m1", "m2", "m3", "m4", "m5"))
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    r = await p.run()
    assert r["sent"] == 2 and r["dropped"] == 3 and [s["urgency"] for s in sent] == ["보통"]   # the two items are one message
    log = p.log()
    assert [x["urgency"] for x in log if x["status"] == "sent"] == ["보통", "보통"]                 # limits count per item
    assert sum(x["status"] == "dropped" and "Over the number that can be sent today" in x["reason"] for x in log) == 3
    # an 오늘 item still fits (3 together), a second one does not
    mail.items = mails("m1", "m2", "m3", "m4", "m5") + [{"uid": "9", "from": "x", "subject": "오늘 답장", "date": "", "unread": True},
                                                        {"uid": "10", "from": "x", "subject": "오늘 또", "date": "", "unread": True}]
    p.stream_fn = all_as("오늘")
    clock.t += 3 * 3600
    r = await p.run()
    assert r["sent"] == 1 and p.today_counts() == {"보통": 2, "오늘": 1, "지금": 0}
    # 지금 with a concrete time within 3 hours goes out over the regular caps
    mail.items = mail.items + [{"uid": "11", "from": "x", "subject": "회의 장소 변경", "date": "", "unread": True}]
    clock.t += 3 * 3600
    p.stream_fn = all_as("지금", due=datetime.fromtimestamp(clock.t + 3600, SEOUL).strftime("%H:%M"))
    r = await p.run()
    assert r["sent"] == 1 and sent[-1]["urgency"] == "지금" and sent[-1]["title"] == "회의 장소 변경"
    # the next day the leftovers are looked at again
    clock.t = ts(2026, 11, 8, 9, 0)
    p.stream_fn = all_as("보통")
    r = await p.run()
    assert r["sent"] == 2 and p.today_counts()["보통"] == 2


async def test_wording_alone_never_makes_it_urgent_and_unclear_levels_become_today(tmp_path):
    mail = FakeMail(mails("[긴급!!!] 지금 바로 확인", "이상한 등급"))
    judge = Judge(lambda items: [{"n": 1, "urgency": "지금", "due": None, "reason": "제목이 긴급", "message": "긴급 메일이 왔어요."},
                                 {"n": 2, "urgency": "매우 중요", "reason": "?", "message": "확인해 보세요."}])
    store, home, clock, p, sent = setup(tmp_path, judge, mail)
    await p.run()
    assert [s["urgency"] for s in sent] == ["오늘"] and [x["urgency"] for x in p.log() if x["status"] == "sent"] == ["오늘", "오늘"]
    first = next(x for x in p.log() if "긴급" in x["title"])
    assert first["urgency"] == "오늘" and "no sign of a time soon" in first["reason"]
    rules = judge.requests[0].messages[0]["content"]
    assert "Wording alone never makes something urgent" in rules and "pick 오늘" in rules


async def test_a_late_due_time_is_not_now_and_now_has_its_own_cap(tmp_path):
    mail = FakeMail(mails("a", "b", "c"))
    later = datetime.fromtimestamp(ts(2026, 11, 7, 9, 0) + 6 * 3600, SEOUL).strftime("%H:%M")
    soon = datetime.fromtimestamp(ts(2026, 11, 7, 9, 0) + 1800, SEOUL).strftime("%H:%M")
    judge = Judge(lambda items: [{"n": 1, "urgency": "지금", "due": later, "reason": "", "message": "6시간 뒤"},
                                 {"n": 2, "urgency": "지금", "due": soon, "reason": "", "message": "30분 뒤"},
                                 {"n": 3, "urgency": "지금", "due": soon, "reason": "", "message": "또 30분 뒤"}])
    store, home, clock, p, sent = setup(tmp_path, judge, mail)
    p.update(daily_now=1)
    await p.run()
    by = {x["message"]: x["urgency"] for x in p.log() if x["status"] == "sent"}   # the message is one, the log has every item
    assert by["30분 뒤"] == "지금" and by["6시간 뒤"] == "오늘" and by.get("또 30분 뒤") == "오늘"


# ---------------------------------------------------------------------------------------------------------- dedupe, prefs, sources
async def test_nothing_is_told_twice_and_dropped_items_are_not_judged_again(tmp_path):
    mail = FakeMail(mails("세일 광고", "계약서 회신 요청"))
    judge = Judge(lambda items: [{"n": n, "urgency": "drop" if "세일" in t else "오늘", "reason": "광고" if "세일" in t else "회신",
                                  "message": "" if "세일" in t else "계약서 회신을 기다리고 있어요."} for n, t in items])
    store, home, clock, p, sent = setup(tmp_path, judge, mail)
    await p.run()
    assert [s["message"] for s in sent] == ["계약서 회신을 기다리고 있어요."]
    clock.t += 3 * 3600
    assert (await p.run())["status"] == "nothing_new" and len(judge.requests) == 1 and len(sent) == 1
    clock.t += 31 * 86400                                    # long after: the ledger forgot them, still unread -> judged again
    await p.run()
    assert len(judge.requests) == 2


async def test_the_users_preferences_drop_items_in_code_and_reach_the_model(tmp_path):
    mail = FakeMail(mails("주말 특가 광고 모음", "택배 도착 예정"))
    judge = all_as("보통")
    store, home, clock, p, sent = setup(tmp_path, judge, mail)
    (home.root / "NOTIFY_PREFS.md").write_text("# NOTIFY_PREFS.md\n\n## 알려 줘\n- 택배\n\n## 알리지 마\n- 광고\n\n## 시간대\n\n## 방식\n- 짧게\n",
                                               encoding="utf-8")
    await p.run()
    shown = judge.requests[0].messages[-1]["content"]
    assert "광고 모음" not in shown.split("Items:")[1] and "택배 도착 예정" in shown and "## 알려 줘" in shown
    assert [s["title"] for s in sent] == ["택배 도착 예정"]
    assert any(x["reason"] == "It is on your don't-tell-me list" for x in p.log())


async def test_goals_commitments_and_already_told_runs_are_candidates(tmp_path):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    clock = Clock(ts(2026, 11, 7, 9, 0))
    home = Home(tmp_path / "home")
    (home.root / "MEMORY.md").write_text("# MEMORY.md\n\n## Facts\n- 2026-11-08 생일 (작년)\n\n## Commitments\n"
                                         "- 11월 7일 오후 1시 엄마와 점심\n- 2026-12-25 여행\n", encoding="utf-8")
    store.add_goal("자격증 시험", "공부", "2026-11-09 시험")
    store.add_goal("먼 목표", "", "2027-05-01")
    sch = Schedules(store, clock)
    j = sch.create("시세", "확인", cron="0 9 * * *")
    sch.add_run(j["id"], "done", "시세 그대로")
    w = Watchers(store, clock=clock)
    judge = all_as("오늘")
    sent = []

    async def deliver(item):
        sent.append(item)
        return None

    p = Proactive(store, home, stream_fn=judge, model=lambda: ("p", "m"), deliver=deliver, schedules=sch, watchers=w, clock=clock)
    p.update(enabled=True)
    found, _ = await p.gather(clock.t - 3600)
    by = {c.source: c for c in found}
    assert {c.title for c in found if c.source == "memory"} == {"11월 7일 오후 1시 엄마와 점심"}
    assert by["memory"].due == ts(2026, 11, 7, 13, 0) and by["goal"].title == "자격증 시험" and by["goal"].day == "2026-11-09"
    assert by["schedule"].told
    await p.run()
    assert {x["source"] for x in p.log() if x["status"] == "sent"} == {"memory", "goal"} and len(sent) == 1   # one message for both
    told = next(x for x in p.log() if x["source"] == "schedule")
    assert told["status"] == "dropped" and "already told you" in told["reason"]
    assert "[schedule]" not in judge.requests[0].messages[-1]["content"]


async def test_a_running_chat_reply_or_a_bad_answer_does_not_lose_items(tmp_path):
    busy = {"on": True}
    mail = FakeMail(mails("a"))

    async def garbage(provider, request, **kw):
        yield TextDelta("잘 모르겠어요")
        yield Done("stop")

    store, home, clock, p, sent = setup(tmp_path, garbage, mail, busy=lambda: busy["on"])
    assert await p.tick() is None                          # the user is chatting: later
    busy["on"] = False
    assert (await p.tick())["status"] == "failed" and p.log() == []   # nothing ledgered: tried again next time
    p.stream_fn = all_as("보통")
    clock.t += 3 * 3600
    assert (await p.tick())["sent"] == 1


# ---------------------------------------------------------------------------------------------------------- over HTTP
async def test_api_switch_log_and_delivery_as_a_chat_message_with_a_notification(tmp_path):
    judge = all_as("오늘")
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False,
                 home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[], stream_fn=judge)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"timezone": "Asia/Seoul", "agent_name": "켈로"})
        got = (await c.get("/api/proactive")).json()
        assert got["enabled"] is False and got["wake_start"] == "07:00" and got["wake_end"] == "22:00" and got["daily_regular"] == 2
        assert got["daily_total"] == 3 and got["every_minutes"] == 120 and got["prefs_file"] == "NOTIFY_PREFS.md"
        assert (await c.post("/api/proactive/run")).status_code == 409                       # off
        assert (await c.put("/api/proactive", json={"wake_start": "25:00"})).status_code == 422
        pro = srv.server.config.app.state.proactive
        pro.mail = FakeMail(mails("계약서 회신 요청"))
        on = (await c.put("/api/proactive", json={"enabled": True, "daily_regular": 1, "daily_total": 2})).json()
        assert on["enabled"] and on["daily_total"] == 2
        r = (await c.post("/api/proactive/run")).json()
        assert r["status"] == "done" and r["sent"] == 1
        msgs = (await c.get("/api/messages")).json()
        assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"].startswith("알림 1")
        note = (await c.get("/api/notifications")).json()["items"][0]
        assert note["type"] == "proactive" and note["title"] == "New mail · 보낸이 · 계약서 회신 요청" and note["body"].startswith("알림 1") and note["task_id"]
        task = (await c.get(f"/api/tasks/{note['task_id']}")).json()
        assert task["source"] == "proactive" and task["status"] == "done"
        log = (await c.get("/api/proactive/log")).json()["items"]
        assert log[0]["status"] == "sent" and log[0]["urgency"] == "오늘" and log[0]["source"] == "mail" and log[0]["task_id"] == note["task_id"]
        assert (await c.get("/api/proactive")).json()["today"]["오늘"] == 1
        assert (await c.get("/api/files/NOTIFY_PREFS.md")).json()["content"].count("## ") == 4


# ---------------------------------------------------------------------------------------------------------- the unanswered guard
def one_new_mail(mail, n):
    mail.items = [{"uid": f"u{n}", "from": "x", "subject": f"새 소식 {n}", "date": "", "unread": True}]


async def test_nothing_runs_before_the_owners_first_chat_message(tmp_path):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    store.add_message("user", "[Handoff from a background job, not from the user] 보고")   # a hidden row is not the owner
    judge = all_as("보통")
    p = Proactive(store, Home(tmp_path / "home"), stream_fn=judge, model=lambda: ("p", "m"), mail=FakeMail(mails("a")),
                  clock=Clock(ts(2026, 11, 7, 9, 0)), owner_marks=("[Handoff from a background job, not from the user]",))
    p.update(enabled=True)
    assert await p.tick() is None and p.mail.calls == 0 and judge.requests == []
    last = p.public()["last_pass"]
    assert last["status"] == "skipped" and last["reason_code"] == "no_owner_message" and last["at"] == p.clock.t
    assert p.public()["guard"]["owner_spoke"] is False
    store.add_message("user", "안녕")
    assert (await p.tick())["status"] == "done" and p.public()["guard"]["owner_spoke"] is True


async def test_every_skip_leaves_one_record_with_a_code_and_a_time(tmp_path):
    busy = {"on": False}
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), FakeMail(mails("a")), busy=lambda: busy["on"])
    p.update(enabled=False)
    await p.tick()
    assert p.public()["last_pass"]["reason_code"] == "off"
    p.update(enabled=True)
    clock.t = ts(2026, 11, 7, 23, 0)
    await p.tick()
    assert p.public()["last_pass"]["reason_code"] == "outside_waking_hours"
    at = p.public()["last_pass"]["at"]
    clock.t += 60
    await p.tick()
    assert p.public()["last_pass"]["at"] == at          # the same skip a minute later is not written again
    clock.t, busy["on"] = ts(2026, 11, 8, 9, 0), True
    await p.tick()
    last = p.public()["last_pass"]
    assert last["reason_code"] == "chat_running" and last["reason"] and "backoff_level" in last and "next_allowed_at" in last
    busy["on"] = False
    assert (await p.tick())["reason_code"] == ""
    clock.t += 3 * 3600
    assert (await p.tick())["reason_code"] == "nothing_new"


async def test_unanswered_messages_stop_then_back_off_doubling_up_to_a_week(tmp_path):
    mail = FakeMail([])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    p.update(wake_start="00:00", wake_end="00:00", every_minutes=30, daily_regular=10, daily_total=20,   # awake all day, caps aside
             change_alerts=False)                                  # (with change alerts on, a held pass still reads mail: test_proactive_mail.py)
    n = 0

    async def pass_with_new_mail():
        nonlocal n
        n += 1
        one_new_mail(mail, n)
        clock.t += 31 * 60
        return await p.tick()

    for _ in range(3):
        assert (await pass_with_new_mail())["sent"] == 1
    g = p.public()["guard"]
    assert g["unanswered"] == 3 and g["backoff_level"] == 1 and g["next_allowed_at"] == clock.t + 18 * 3600
    assert await pass_with_new_mail() is None
    last = p.public()["last_pass"]
    assert last["reason_code"] == "waiting_for_reply" and last["backoff_level"] == 1 and last["next_allowed_at"] == clock.t - 31 * 60 + 18 * 3600
    # rounds: after each pause one message goes; unanswered, the next pause doubles: 18 h, 36 h, 72 h, 144 h, then 7 days at most
    pauses = []
    for _ in range(6):
        nxt = p.public()["guard"]["next_allowed_at"]
        start = clock.t
        clock.t = nxt - 60
        one_new_mail(mail, n := n + 1)
        assert await p.tick() is None                        # a minute before: still held back
        clock.t = nxt
        assert (await p.tick())["sent"] == 1 and len(sent) == 3 + len(pauses) + 1
        pauses.append(p.public()["guard"]["pause_hours"])
    assert pauses == [36, 72, 144, 168, 168, 168]
    # any chat message from the owner resets count, level and cap
    store.add_message("user", "고마워")
    g = p.public()["guard"]
    assert g["unanswered"] == 0 and g["backoff_level"] == 0 and g["next_allowed_at"] is None
    for _ in range(3):
        assert (await pass_with_new_mail())["sent"] == 1
    assert await pass_with_new_mail() is None


async def test_one_pass_sends_no_more_than_the_guard_allows_and_settings_change_it(tmp_path):
    mail = FakeMail(mails("a", "b", "c", "d", "e"))
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    p.update(daily_regular=10, daily_total=20, unanswered_limit=2, pause_hours=2, pause_max_days=1)
    r = await p.tick()
    assert r["sent"] == 2 and r["backoff_level"] == 1 and r["next_allowed_at"] == clock.t + 2 * 3600
    assert sum(x["reason"] == "Waiting for your reply to the earlier messages" for x in p.log()) == 3
    assert p.public()["unanswered_limit"] == 2 and p.public()["pause_hours"] == 2
    assert p.pause_seconds(5) == 86400                       # capped at pause_max_days
    for bad in ({"unanswered_limit": 0}, {"pause_hours": 0}, {"pause_max_days": 31}):
        with pytest.raises(ValueError):
            p.update(**bad)


async def test_checklist_runs_count_and_are_held_back_too(tmp_path):
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), FakeMail([]))
    p.update(daily_total=20, daily_regular=10)
    for i in range(3):
        p.checklist_said(i + 1)
    assert p.public()["guard"]["unanswered"] == 3 and p.hold_reason() == "waiting_for_reply"
    assert p.checklist_hold() == "Waiting for your reply to the earlier messages"
    from assistant.checklist import Checklist
    started = []

    async def start(body):
        started.append(body)
        return {"status": "done", "task_id": 9, "quiet": False}

    ck = Checklist(store, home, start, clock=clock, room=p.room_today, on_said=p.checklist_said, hold=p.checklist_hold)
    ck.update(items=["메일 확인"])
    r = await ck.tick()
    assert r["status"] == "skipped" and r["reason"] == "Waiting for your reply to the earlier messages" and started == []
    store.add_message("user", "응")
    assert (await ck.tick())["status"] == "done" and p.public()["guard"]["unanswered"] == 1


async def test_a_manual_run_is_not_held_back_but_counts(tmp_path):
    mail = FakeMail(mails("a"))
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    p.update(unanswered_limit=1)
    assert (await p.run())["sent"] == 1 and p.hold_reason() == "waiting_for_reply"
    one_new_mail(mail, 2)
    assert (await p.run())["sent"] == 1                      # the owner pressed "run now"
    assert p.public()["guard"]["backoff_level"] == 2
