"""Learning the quiet time of night from the user's own use, and running the nightly jobs once a night at that time."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from assistant.quiet_hours import DEFAULT, NightlyRunner, QuietHours
from assistant.store import Store

SEOUL = ZoneInfo("Asia/Seoul")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=SEOUL).timestamp()


def test_default_time_until_a_week_of_use_is_known():
    store = Store()
    q = QuietHours(store, lambda: "Asia/Seoul", Clock(at(2026, 11, 10, 12)))
    q.record(at(2026, 11, 9, 23, 0))
    assert q.quiet_slot()[:2] == DEFAULT and q.quiet_slot()[2] == "default"


def test_the_quietest_night_stretch_is_learned():
    store = Store()
    clock = Clock(at(2026, 11, 20, 12))
    q = QuietHours(store, lambda: "Asia/Seoul", clock)
    for day in range(1, 19):                     # every night the user is on the phone until 2 am and again from 6 am
        base = datetime(2026, 11, day, tzinfo=SEOUL)
        for minutes in list(range(22 * 60, 26 * 60, 10)) + list(range(30 * 60, 32 * 60, 10)):
            q.record((base + timedelta(minutes=minutes)).timestamp())
    h, m, source = q.quiet_slot()
    assert source == "learned" and (2, 0) <= (h, m) <= (4, 30)


async def test_the_runner_runs_once_a_night_at_the_planned_time_even_after_a_restart():
    store = Store()
    clock = Clock(at(2026, 11, 10, 21, 0))
    ran = []

    async def job():
        ran.append(clock.t)
    q = QuietHours(store, lambda: "Asia/Seoul", clock)
    r = NightlyRunner(store, q, [job], clock)
    assert not await r.tick()                    # 21:00: not night yet
    clock.t = at(2026, 11, 10, 23)
    assert not await r.tick() and store.meta("nightly:plan").startswith("2026-11-10 03:50")
    clock.t = at(2026, 11, 11, 3, 55)
    r2 = NightlyRunner(store, QuietHours(store, lambda: "Asia/Seoul", clock), [job], clock)   # a restart in between
    assert await r2.tick() and len(ran) == 1
    assert not await r2.tick() and len(ran) == 1   # once a night
    clock.t = at(2026, 11, 11, 9)
    assert not await r2.tick()
