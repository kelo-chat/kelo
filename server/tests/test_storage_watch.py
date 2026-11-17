"""Storage warnings (storage_watch.py): 80% / 95% of the cap once per crossing with hysteresis, quiet hours for those two, the
server machine's disk critically low at once, and a full disk answered in words instead of a bare 500."""

from __future__ import annotations

import errno
import json
import sqlite3
from collections import namedtuple

import httpx
import pytest

from assistant.quota import Quota
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.storage_watch import (HOST_MIN_FREE, META, StorageWatch, disk_full, display_level, host_low, next_level)

from test_server import AUTH, BODY, TOKEN, Running, read_events, scripted

GB = 1024**3
Usage = namedtuple("Usage", "total used free")


# ------------------------------------------------------------------------------------------------ the rules
def test_levels_climb_at_80_and_95_and_hold_until_the_clear_marks():
    assert next_level(0, 0.5) == 0 and next_level(0, 0.79) == 0
    assert next_level(0, 0.80) == 80 and next_level(0, 0.95) == 95
    assert next_level(80, 0.77) == 80          # under 80% but over the 75% clear mark: still the same crossing
    assert next_level(80, 0.749) == 0          # under 75%: re-armed
    assert next_level(95, 0.92) == 95          # under 95% but over 90%: holds
    assert next_level(95, 0.89) == 80          # under 90%: back to the 80% level (already told)
    assert next_level(95, 0.70) == 0
    assert next_level(0, None) == 0 and next_level(95, None) == 0    # no cap: no cap warnings


def test_the_host_disk_is_low_under_1gb_or_5_percent_of_the_disk_up_to_5gb_with_hysteresis():
    assert host_low(HOST_MIN_FREE - 1, 2000 * GB)                       # under 1 GB, whatever the disk
    assert host_low(4 * GB, 2000 * GB)                                  # 0.2% of 2 TB and under 5 GB: low
    assert not host_low(6 * GB, 2000 * GB)                              # 5% of a big disk is not "low": over 5 GB is fine
    assert host_low(int(1.5 * GB), 40 * GB)                             # 3.75% of a 40 GB disk
    assert not host_low(3 * GB, 40 * GB)                                # 7.5%: fine
    assert not host_low(int(1.2 * GB), 10 * GB)                         # 12% of a small disk and over 1 GB
    assert host_low(int(1.2 * GB), 10 * GB, was_low=True)               # once low, it needs 1.5 GB before it is armed again
    assert host_low(6 * GB, 2000 * GB, was_low=True)                    # ... and 7.5 GB on a big disk
    assert not host_low(8 * GB, 2000 * GB, was_low=True)
    assert not host_low(None, None) and not host_low(10, 0)


def test_what_the_app_shows_follows_the_plain_numbers():
    assert display_level(None) == "ok" and display_level(0.5) == "ok"
    assert display_level(0.8) == "warn" and display_level(0.949) == "warn" and display_level(0.95) == "critical"


def test_a_full_disk_is_recognized_in_sqlite_and_os_errors_and_their_causes():
    assert disk_full(sqlite3.OperationalError("database or disk is full"))
    assert disk_full(OSError(errno.ENOSPC, "No space left on device"))
    assert not disk_full(sqlite3.OperationalError("database is locked")) and not disk_full(OSError(errno.EACCES, "denied"))
    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError as inner:
            raise RuntimeError("could not save") from inner
    except RuntimeError as outer:
        assert disk_full(outer)
    assert not disk_full(None)


# ------------------------------------------------------------------------------------------------ the watcher
class Box:
    def __init__(self, tmp_path, limit=1000, free=500 * GB, total=1000 * GB):
        self.root = tmp_path / "state"
        (self.root / "home" / "uploads").mkdir(parents=True)
        self.store = Store()
        self.quota = Quota(self.root, limit, ttl=0)
        self.sent: list[tuple[str, str, str]] = []
        self.awake = True
        self.now = 1_000_000.0
        self.usage = Usage(total, total - free, free)
        self.watch = StorageWatch(self.store, self.quota, lambda typ, title, body="", **k: self.sent.append((typ, title, body)),
                                  awake=lambda ts: self.awake, clock=lambda: self.now, disk_usage=lambda p: self.usage)
        self.quota.listeners.append(self.watch.mark_dirty)

    def fill(self, n, name="a.pdf", part="uploads"):
        (self.root / "home" / part).mkdir(parents=True, exist_ok=True)
        (self.root / "home" / part / name).write_bytes(b"x" * n)
        self.quota.wrote()

    def check(self):
        self.now += 60
        return self.watch.check()


def test_each_crossing_is_told_once_and_again_only_after_dropping_under_the_clear_mark(tmp_path):
    b = Box(tmp_path)
    b.fill(500)
    assert b.check() == []
    b.fill(820)
    assert b.check() == ["warn"]
    title, body = b.sent[-1][1], b.sent[-1][2]
    assert b.sent[-1][0] == "storage" and "82%" in title and "820B of 1000B" in body and "uploaded documents" in body
    assert b.check() == []                                  # still over 80%: not again
    b.fill(780)
    assert b.check() == []                                  # 78%: same crossing (over the 75% clear mark)
    b.fill(960)
    assert b.check() == ["critical"] and "96%" in b.sent[-1][1]
    b.fill(920)
    assert b.check() == []                                  # 92%: holds at 95
    b.fill(880)
    assert b.check() == []                                  # 88%: back to the 80% level, which was told already
    b.fill(960)
    assert b.check() == ["critical"]                        # a new 95% crossing
    b.fill(700)
    assert b.check() == []
    b.fill(810)
    assert b.check() == ["warn"]                            # dropped under 75%, so 80% is told again
    assert [s[0] for s in b.sent] == ["storage"] * 4


def test_cap_warnings_wait_for_waking_hours_and_send_only_the_latest_level(tmp_path):
    b = Box(tmp_path)
    b.awake = False
    b.fill(850)
    assert b.check() == []
    b.fill(970)
    assert b.check() == []                                  # nothing at night
    b.awake = True
    assert b.check() == ["critical"] and len(b.sent) == 1   # one message, at the level it reached
    b.awake = False
    b.fill(100)
    b.check()
    b.awake = True
    assert b.check() == []                                  # it dropped before morning: nothing left to say


def test_a_critically_low_disk_is_told_at_once_even_at_night_and_once_per_crossing(tmp_path):
    b = Box(tmp_path, limit=0, free=int(0.5 * GB), total=500 * GB)
    b.awake = False
    b.fill(10, name="db", part="notes")
    (b.root / "assistant.db").write_bytes(b"d" * 5000)
    b.quota.wrote()
    assert b.check() == ["host"]
    typ, title, body = b.sent[-1]
    assert "disk is almost full" in title and "512MB" in body
    assert "Kelo's own data is only" in body and "docker image prune" in body   # Kelo's few MB are not what fills that disk
    assert b.check() == []
    b.usage = Usage(500 * GB, 498 * GB, int(1.2 * GB))
    assert b.check() == []                                   # 1.2 GB: better, but not past the 1.5x clear mark
    b.usage = Usage(500 * GB, 400 * GB, 100 * GB)
    assert b.check() == []
    b.usage = Usage(500 * GB, 499 * GB, int(0.4 * GB))
    assert b.check() == ["host"]                             # a new crossing
    st = json.loads(b.store.meta(META))
    assert st["host_low"] and st["host_told"] and st["level"] == 0   # no cap: no cap levels


def test_a_low_but_not_critical_disk_is_told_only_when_it_stays_low(tmp_path):
    # 2026-11-11: building Kelo's own update dipped an 84 GB disk to ~3 GB (under 5%) for a few minutes, and every update pushed a
    # "disk almost full" warning though the build's images were pruned right after
    from assistant.storage_watch import HOST_LOW_HOLD
    b = Box(tmp_path, limit=0, free=3 * GB, total=84 * GB)
    assert b.check() == []                                   # low, but only just now
    b.usage = Usage(84 * GB, 75 * GB, 9 * GB)                # the build is done and pruned: back up
    assert b.check() == []
    b.usage = Usage(84 * GB, 81 * GB, 3 * GB)
    for _ in range(HOST_LOW_HOLD // 60):                   # the first low minute, then 14 more
        assert b.check() == []
    assert b.check() == ["host"]                             # low for the whole hold: told once
    assert b.check() == []


def test_the_public_answer_says_the_level_the_biggest_part_and_the_host_state(tmp_path):
    b = Box(tmp_path)
    b.fill(300, name="x.md", part="notes")
    b.fill(600, name="a.pdf")
    pub = b.watch.public(b.watch.numbers(fresh=True))
    assert pub == {"level": "warn", "percent": 90, "host_low": False, "top": "uploads"}


def test_the_cap_numbers_are_looked_at_again_only_after_a_write_or_every_ten_minutes(tmp_path):
    b = Box(tmp_path)
    b.fill(100)
    b.watch.numbers()
    (b.root / "home" / "uploads" / "sneaky.pdf").write_bytes(b"x" * 500)     # written behind the quota's back
    assert b.watch.numbers()["used"] == 100                                    # cached
    b.now += 601
    assert b.watch.numbers()["used"] == 600                                    # ten minutes later it looks again
    b.fill(10, name="c.pdf")                                                  # a write through the quota marks it dirty
    assert b.watch.numbers()["used"] == 610


def test_a_notification_that_cannot_be_saved_is_tried_again_next_time(tmp_path):
    b = Box(tmp_path)
    calls = []

    def broken(*a, **k):
        calls.append(a)
        raise sqlite3.OperationalError("database or disk is full")
    b.watch.notify = broken
    b.fill(900)
    assert b.check() == [] and len(calls) == 1
    b.watch.notify = lambda typ, title, body="", **k: b.sent.append((typ, title, body))
    assert b.check() == ["warn"]


# ------------------------------------------------------------------------------------------------ the app
def app_for(tmp_path, **kw):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "state" / "assistant.db"),
                               home_dir=str(tmp_path / "state" / "home"), **kw), tools=[])


async def test_resources_carry_the_warning_and_the_cleanup_switch(tmp_path):
    app = app_for(tmp_path, disk_limit=1_500_000)   # well above the empty database (~0.85 MB of tables, 2026-11-16), so it starts below 80%
    app.state.storage_watch._usage = lambda p: Usage(500 * GB, 100 * GB, 400 * GB)   # not this test machine's own disk
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=20) as c:
        res = (await c.get("/api/resources")).json()
        assert res["alert"]["level"] == "ok" and res["alert"]["host_low"] is False and res["can_clean"] is True
        room = res["disk"]["limit"] - res["disk"]["used"]
        assert (await c.put("/api/uploads/big.pdf", content=b"%PDF" + b"0" * (room - 30_000))).status_code == 200
        res = (await c.get("/api/resources")).json()
        assert res["alert"]["level"] == "critical" and res["alert"]["percent"] >= 95
        parts = res["disk"]["parts"]
        assert parts["database"] > 0 and parts["uploads"] > 0           # the database counts in what is shown and in the warning
        assert res["alert"]["top"] == max(parts, key=parts.get)
        app.state.storage_watch.awake = lambda ts: False
        assert app.state.storage_watch.check() == []                    # outside the owner's waking hours: waits
        app.state.storage_watch.awake = lambda ts: True
        assert app.state.storage_watch.check() == ["critical"]
        notes = (await c.get("/api/notifications")).json()["items"]
        assert notes[0]["type"] == "storage" and "almost full" in notes[0]["title"]


async def test_a_full_disk_answers_507_with_words_and_a_code_instead_of_a_bare_500(tmp_path):
    app = app_for(tmp_path)
    store = app.state.storage_watch.store

    def full(**values):
        raise sqlite3.OperationalError("database or disk is full")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}, timeout=20) as c:
        store.set_profile = full
        r = await c.put("/api/profile", json={"name": "A"})
        assert r.status_code == 507 and r.json()["code"] == "disk_full"
        assert "디스크가 가득" in r.json()["detail"]
        store.set_profile = lambda **v: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked"))
        assert (await c.put("/api/profile", json={"name": "A"})).status_code == 500     # anything else stays a server error


async def test_a_chat_reply_that_hits_a_full_disk_ends_with_the_disk_full_words(tmp_path):
    fn = scripted([sqlite3.OperationalError("database or disk is full")])
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None), tools=[], stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        async with c.stream("POST", "/api/chat", json=BODY, headers=AUTH) as r:
            events = await read_events(r)
    assert events[-1]["type"] == "error" and events[-1]["code"] == "disk_full" and "disk is full" in events[-1]["message"]


@pytest.mark.parametrize("lang", ["en", "ko"])
async def test_the_warning_is_written_in_the_owners_language(tmp_path, lang):
    from assistant import i18n
    b = Box(tmp_path)
    token = i18n.use(lang)
    try:
        b.fill(900)
        b.check()
    finally:
        i18n.reset(token)
    assert ("저장 공간" in b.sent[-1][1]) == (lang == "ko")
