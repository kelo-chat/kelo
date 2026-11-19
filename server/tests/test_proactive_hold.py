"""Proactive messages that cannot go out now are held until their deadline, judged again when they are released, and bundled one message per pass, with no daily count of messages (docs/proactive.md). Fake model, fake clock, fake mail."""

import json
from datetime import datetime

from assistant.proactive import HELD_NOTE, Proactive, _until_from
from assistant.providers import Done, TextDelta

from test_proactive import SEOUL, FakeMail, Judge, all_as, mails, setup, ts


def one(mail, n, subject=None):
    mail.items = [{"uid": f"u{n}", "from": "x", "subject": subject or f"새 소식 {n}", "date": "", "unread": True}]


async def test_an_item_over_the_cap_is_held_then_told_next_day_after_being_judged_again(tmp_path):
    mail = FakeMail(mails("m1", "m2", "m3"))
    judge = all_as("보통")
    store, home, clock, p, sent = setup(tmp_path, judge, mail)
    r = await p.run()
    assert r["sent"] == 2 and r["held"] == 1 and p.public()["held"] == 1
    held = p.held()[0]
    assert held["why"] == "over_cap" and held["title"] == "m3" and held["expires_at"] > clock.t
    store.add_message("user", "m3 는 이미 처리했어")               # (recent chat is shown with the held item)
    clock.t = ts(2026, 11, 8, 9, 0)
    mail.items = []
    r = await p.run()
    again = judge.requests[-1].messages
    assert HELD_NOTE.split("\n")[0] in again[-1]["content"] and "m3 는 이미 처리했어" in again[-1]["content"]
    assert r["sent"] == 1 and p.held() == [] and sent[-1]["title"] == "m3"        # a pass with nothing new still tells the held one
    assert store._all("SELECT COUNT(*) AS n FROM proactive_held")[0]["n"] == 0


async def test_a_held_item_the_model_drops_when_looked_at_again_is_dropped_for_good(tmp_path):
    mail = FakeMail(mails("a", "b", "c"))
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    await p.run()
    assert len(p.held()) == 1
    p.stream_fn = all_as("drop")
    clock.t = ts(2026, 11, 8, 9, 0)
    mail.items = []
    r = await p.run()
    assert r["sent"] == 0 and p.held() == [] and r["dropped"] == 1
    assert any(x["status"] == "dropped" and x["title"] == "c" for x in p.log())


async def test_past_its_deadline_it_is_missed_not_held(tmp_path):
    mail = FakeMail(mails("a", "b", "c"))
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    await p.run()
    assert len(p.held()) == 1
    clock.t = ts(2026, 11, 10, 9, 0)                                  # default deadline: the end of the next day (11-08)
    assert p.expire_held() == 1 and p.held() == []
    miss = next(x for x in p.log() if x["title"] == "c")
    assert miss["status"] == "dropped" and miss["reason"] == "Expired before it could go out"
    p.stream_fn = Judge(lambda items: (_ for _ in ()).throw(AssertionError("no model call for nothing")))
    mail.items = []
    assert (await p.run())["status"] == "nothing_new"


async def test_the_models_useful_until_and_the_items_own_time_set_the_deadline(tmp_path):
    mail = FakeMail(mails("a", "b", "c"))
    judge = Judge(lambda items: [{"n": n, "urgency": "보통", "reason": "r", "message": f"M{n}", "useful_until": "2026-11-12"} for n, _ in items])
    store, home, clock, p, sent = setup(tmp_path, judge, mail)
    await p.run()
    assert p.held()[0]["expires_at"] == ts(2026, 11, 12, 23, 59, 59)
    assert _until_from("2026-11-12 18:30", SEOUL) == ts(2026, 11, 12, 18, 30) and _until_from("soon", SEOUL) is None
    # a calendar-like candidate keeps its own time as the deadline, and one already past is missed
    from assistant.proactive import Candidate
    now = clock.t
    c = Candidate(key="cal:x", source="calendar", title="Dentist", due=now + 4 * 3600)
    assert p._hold(c, {}, "오늘", "r", "m", "over_cap", now, p.tz()) and next(h for h in p.held() if h["key"] == "cal:x")["expires_at"] == now + 4 * 3600
    gone = Candidate(key="cal:y", source="calendar", title="Old", due=now - 60)
    assert not p._hold(gone, {}, "오늘", "r", "m", "over_cap", now, p.tz())
    assert any(x["title"] == "Old" and x["status"] == "dropped" for x in p.log())
    long = Candidate(key="cal:z", source="goal", title="Far", due=now + 90 * 86400)
    p._hold(long, {}, "보통", "r", "m", "over_cap", now, p.tz())
    assert next(h for h in p.held() if h["key"] == "cal:z")["expires_at"] == now + 14 * 86400


async def test_a_held_item_is_not_judged_twice_when_its_source_shows_it_again(tmp_path):
    mail = FakeMail(mails("a", "b", "c"))
    judge = all_as("보통")
    store, home, clock, p, sent = setup(tmp_path, judge, mail)
    await p.run()
    n = len(judge.requests)
    clock.t += 3 * 3600                                                # the same three mails again (and the held one is released)
    await p.run()
    new_items = judge.requests[-1].messages[-1]["content"].split("Items:\n")[1].splitlines()
    assert len(judge.requests) == n + 1 and len(new_items) == 1         # only the held one is asked about, not the mails seen before


async def test_there_is_no_daily_count_of_messages_only_the_daily_cap(tmp_path):
    mail = FakeMail([])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    p.update(daily_regular=10, daily_total=20, every_minutes=30, wake_start="00:00", wake_end="00:00")
    assert "daily_deliveries" not in p.config() and "today_deliveries" not in p.public()
    for n in (1, 2, 3, 4):                                             # four passes, one message each: none waits for a "slot"
        one(mail, n)
        clock.t += 3600
        assert (await p.run())["sent"] == 1
    assert len(sent) == 4 and p.held() == []


async def test_a_stored_message_count_from_before_is_ignored_and_dropped_on_the_next_save(tmp_path):
    mail = FakeMail([])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    cfg = json.loads(store.meta("proactive:config"))
    store.set_meta("proactive:config", json.dumps({**cfg, "daily_deliveries": 0, "daily_regular": 10, "daily_total": 20}))
    assert "daily_deliveries" not in p.config()
    one(mail, 1)
    assert (await p.run())["sent"] == 1 and p.held() == []             # 0 used to mean "only what cannot wait"
    p.update(daily_deliveries=5)                                       # an unknown key: ignored, no error
    assert "daily_deliveries" not in json.loads(store.meta("proactive:config"))


def test_held_rows_that_waited_for_a_slot_wait_as_over_the_cap(tmp_path):
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), FakeMail([]))
    store._run("INSERT INTO proactive_held(key, source, title, urgency, reason, message, cand, held_at, expires_at, why) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)", ("mail:a:1", "mail", "a", "보통", "r", "m", "{}", clock.t, clock.t + 3600, "slots"))
    Proactive(store, home, stream_fn=all_as("보통"), model=lambda: ("p", "m"), clock=clock)   # the server starts again
    assert [h["why"] for h in p.held()] == ["over_cap"]


async def test_the_unanswered_guard_holds_instead_of_dropping(tmp_path):
    mail = FakeMail(mails("a", "b", "c"))
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    p.update(daily_regular=10, daily_total=20, unanswered_limit=2)
    r = await p.tick()
    assert r["sent"] == 2 and r["held"] == 1 and p.held()[0]["why"] == "waiting_for_reply"


async def test_api_and_settings(tmp_path):
    mail = FakeMail(mails("a", "b", "c"))
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    await p.run()
    pub = p.public()
    assert "daily_deliveries" not in pub and "today_deliveries" not in pub and pub["held"] == 1
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from assistant.proactive import proactive_routes
    app = FastAPI()
    proactive_routes(app, p, lambda: None)
    c = TestClient(app)
    items = c.get("/api/proactive/held").json()["items"]
    assert [i["title"] for i in items] == ["c"] and set(items[0]) == {"key", "source", "title", "urgency", "message", "why", "held_at", "expires_at"}
    r = c.put("/api/proactive", json={"daily_deliveries": 3})       # released apps may still send it: accepted, ignored
    assert r.status_code == 200 and "daily_deliveries" not in r.json() and "daily_deliveries" not in p.config()
    assert c.put("/api/proactive", json={"daily_deliveries": 99}).status_code == 200


async def test_forgetting_a_connector_deletes_its_held_items(tmp_path):
    from assistant.connector_data import ConnectorData
    mail = FakeMail(mails("a", "b", "c"))
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), mail)
    await p.run()
    key = p.held()[0]["key"]
    cd = ConnectorData(store, None)
    cid = key.split(":")[1]
    assert cd.summary(cid)["items"] >= 1
    cd.forget(cid)
    assert p.held() == []
