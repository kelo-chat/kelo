"""A visible delivery (a chat message, its task, its notification) is recorded once under an immutable key and reaches the chat exactly
once across restarts (deliveries.py). Fake clock, a fresh store per test, no model."""

import asyncio
import time

from assistant.deliveries import SEND_AGAIN_SECONDS, Deliveries
from assistant.store import Store

from test_watching import Clock, ts


def fresh():
    store = Store()
    clock = Clock(ts(2026, 11, 7, 8, 0))
    return store, clock


def count(store, sql, args=()):
    return next(iter(store._all(sql, args)[0].values()))


async def test_putting_the_same_key_twice_delivers_once():
    store, clock = fresh()
    seen = []

    async def chat(t):
        seen.append(t.payload["text"])
        store.add_message("assistant", t.payload["text"], once_key=t.once_key)

    d = Deliveries(store, clock=clock, boot="b1")
    d.on("chat", chat)
    assert d.put("k1", "chat", "x", {"text": "hi"}) is True
    assert d.put("k1", "chat", "x", {"text": "hi again"}) is False
    assert await d.send("k1") is True and await d.send("k1") is False
    assert seen == ["hi"]
    assert count(store, "SELECT COUNT(*) FROM messages WHERE once_key = 'k1'") == 1


async def test_a_stop_after_the_message_before_delivered_does_not_repeat_it():
    store, clock = fresh()
    d1 = Deliveries(store, clock=clock, boot="b1")

    async def chat_then_die(t):
        store.add_message("assistant", t.payload["text"], once_key=t.once_key)
        raise RuntimeError("the process stops here")          # before the record is marked delivered

    d1.on("chat", chat_then_die)
    d1.put("k2", "chat", "x", {"text": "report"})
    assert await d1.send("k2") is False

    d2 = Deliveries(store, clock=clock, boot="b2")          # a restart: a new boot id

    async def chat(t):
        store.add_message("assistant", t.payload["text"], once_key=t.once_key)

    d2.on("chat", chat)
    assert await d2.drain() == 1
    assert count(store, "SELECT COUNT(*) FROM messages WHERE once_key = 'k2'") == 1


def test_a_record_left_sending_by_an_earlier_boot_is_taken_at_once_and_one_of_this_boot_waits():
    store, clock = fresh()
    old = Deliveries(store, clock=clock, boot="old")
    old.put("k3", "chat", "x", {"text": "a"})
    assert old.take("k3") is not None                       # left "sending" by the earlier boot
    new = Deliveries(store, clock=clock, boot="new")
    assert "k3" in new.due()                                 # another boot: due at once
    got = new.take("k3")
    assert got is not None and got.again is True
    same = Deliveries(store, clock=clock, boot="new")
    same.put("k4", "chat", "x", {"text": "b"})
    same.take("k4")                                          # sending by this boot now
    assert "k4" not in same.due()                            # this boot waits for its own try
    assert same.take("k4") is None                           # and nobody else of this boot takes it either


def test_a_failed_try_comes_back_within_the_hour():
    store, clock = fresh()
    d = Deliveries(store, clock=clock, boot="b1")
    d.put("k5", "chat", "x", {"text": "a"})
    t = d.take("k5")
    d.later(t, "no chat yet")
    row = store._all("SELECT state, send_at FROM deliveries WHERE once_key = 'k5'")[0]
    assert row["state"] == "waiting" and row["send_at"] == clock.t + SEND_AGAIN_SECONDS == clock.t + 3600
    clock.t += 3600
    assert "k5" in d.due()


def test_a_redelivered_notification_wakes_the_phone_again_with_the_same_id():
    store, clock = fresh()
    woke = []
    from assistant.activity import Activity
    act = Activity(store)
    act.on_notify = lambda n: woke.append(n["id"])
    first = act.notify("task_done", "t", "b", once_key="nk1")
    again = act.notify("task_done", "t", "b", once_key="nk1", push_again=True)
    assert first == again and woke == [first, first]
    assert count(store, "SELECT COUNT(*) FROM notifications WHERE once_key = 'nk1'") == 1


async def test_a_proactive_message_is_not_sent_twice_after_a_stop_before_the_log_is_written(tmp_path):
    """The message and its log rows are one delivery: the chat takes them in one write. A stop after that write (before the pass
    returns) leaves them logged, so the next pass sends nothing new for those items."""
    from assistant.deliveries import Deliveries as Log
    from assistant.proactive import Proactive

    from test_proactive import all_as, setup
    from test_proactive_mail import Mailbox, mail

    box = Mailbox([mail(1, "a", "A <a@x.org>"), mail(2, "b", "B <b@x.org>")])
    store, home, clock, p, sent = setup(tmp_path, all_as("보통"), box)
    log = Log(store, clock=clock, boot="b1")
    texts: list[str] = []

    async def deliver(item):
        once = item["once_key"]
        log.put(once, "chat", "proactive", {"text": item["message"], "task": None, "notify": None}, also=item["also"])
        texts.append(item["message"])
        raise RuntimeError("the process stops before the pass returns")

    p.deliver = deliver
    with_error = await p.run()
    assert with_error["status"] == "failed" and len(texts) == 1
    assert len([x for x in p.log() if x["status"] == "sent"]) == 2           # both items logged with the delivery

    async def again(item):
        texts.append(item["message"])
        return 1
    p.deliver = again
    await p.run()
    assert len(texts) == 1                                                  # the next pass sent nothing for them


def _put_delivered(store, key, *, delivered_at=None, state="delivered", updated_at=None):
    now = time.time()
    store._run("INSERT INTO deliveries(once_key, kind, ref, payload, state, boot, tries, send_at, created_at, updated_at, delivered_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (key, "chat", "", "{}", state, "b1", 1, now, now, updated_at if updated_at is not None else now, delivered_at))


def test_a_delivered_record_goes_a_year_after_its_delivery():
    store, _ = fresh()
    d = Deliveries(store, boot="b1")
    now = time.time()
    day = 86400
    _put_delivered(store, "old", delivered_at=now - 366 * day)
    _put_delivered(store, "young", delivered_at=now - 364 * day)
    _put_delivered(store, "waiting", state="waiting", updated_at=now - 800 * day)
    _put_delivered(store, "sending", state="sending", updated_at=now - 800 * day)
    _put_delivered(store, "dropped-old", state="dropped", updated_at=now - 366 * day)   # a dropped record: its time is when it was dropped
    _put_delivered(store, "dropped-young", state="dropped", updated_at=now - 364 * day)
    assert d.purge(now) == 2
    left = {r["once_key"] for r in store._all("SELECT once_key FROM deliveries")}
    assert left == {"young", "waiting", "sending", "dropped-young"}


async def test_the_daily_cleanup_purges_old_deliveries(tmp_path):
    from assistant.server import create_app
    from test_resume import Model
    from test_server import settings

    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=Model("ok"), with_goal_tools=True)
    store = app.state.store
    _put_delivered(store, "ancient", delivered_at=time.time() - 400 * 86400)
    _put_delivered(store, "recent", delivered_at=time.time() - 10 * 86400)
    job = next(j for name, _h, _m, j in app.state.scheduler.daily if name == "schedule_archive")
    await job()
    left = {r["once_key"] for r in store._all("SELECT once_key FROM deliveries")}
    assert "ancient" not in left and "recent" in left
