"""Weekday and weekend contact hours (day_types.py, active_hours.learn_windows, proactive.py): which days are the weekend (public
holidays included, Korea first), the day a late-night moment belongs to, learning two windows from mixed activity, the weekday fallback
while weekend data is thin, hand-set hours per day type (never changed by Kelo, auto adjust or not), passes and follow-ups on a
Saturday vs a Monday, the nightly quiet slot per kind of night, and the API for old and new apps (fake clock, activity written
straight into the store)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from assistant.active_hours import learn_windows
from assistant.day_types import WEEKDAY, WEEKEND, country_for, country_of_zone, day_type, logical_date, type_at, upcoming
from assistant.followups import Followups
from assistant.home import Home
from assistant.proactive import Proactive
from assistant.quiet_hours import QuietHours
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running
from weather_fakes import SEOUL, ts


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def slots(start: str, end: str) -> set[int]:
    a = int(start[:2]) * 4 + int(start[3:]) // 15
    b = int(end[:2]) * 4 + int(end[3:]) // 15
    n = (b - a) % 96 or 96
    return {(a + i) % 96 for i in range(n)}


def use(store: Store, d: date, active: set[int]) -> None:
    bits = ["0"] * 96
    for s in active:
        bits[s] = "1"
    store.set_meta("usage_slots:" + d.isoformat(), "".join(bits))


def mixed(store: Store, today: date, days: int = 28, weekday=("07:00", "22:00"), weekend=("10:00", "23:30"), skip_weekends: int = 0) -> None:
    """Weekdays from `weekday`, weekends and Korean holidays from `weekend`; the first `skip_weekends` weekend days left empty."""
    left = skip_weekends
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        if day_type(d, "KR") == WEEKEND:
            if left > 0:
                left -= 1
                continue
            use(store, d, slots(*weekend))
        else:
            use(store, d, slots(*weekday))


def setup(tmp_path, t: float):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    store.add_message("user", "안녕")
    clock = Clock(t)
    return store, clock, Proactive(store, Home(tmp_path / "home"), stream_fn=None, model=lambda: None, clock=clock)


# ---------------------------------------------------------------------------------------------------------- which days
def test_weekends_and_public_holidays():
    assert day_type(date(2026, 11, 9), "KR") == WEEKDAY            # Monday
    assert day_type(date(2026, 11, 14), "KR") == WEEKEND           # Saturday
    assert day_type(date(2026, 9, 25), "KR") == WEEKEND            # 추석 (a Friday)
    assert day_type(date(2026, 10, 9), "KR") == WEEKEND            # 한글날
    assert day_type(date(2026, 3, 2), "KR") == WEEKEND             # 삼일절 substitute holiday (a Monday)
    assert day_type(date(2026, 11, 13), "IL") == WEEKEND           # Friday: Israel's weekend
    assert day_type(date(2026, 11, 15), "IL") == WEEKDAY           # Sunday there
    assert day_type(date(2026, 12, 25), "") == WEEKDAY             # no country known: only Sat/Sun
    assert day_type(date(2026, 12, 25), "ZZ") == WEEKDAY           # a country the calendar does not know
    assert country_of_zone("Asia/Seoul") == "KR" and country_of_zone("Nowhere/City") == ""


def test_a_late_night_belongs_to_the_evening_before():
    assert logical_date(ts(2026, 11, 14, 1, 0), SEOUL) == date(2026, 11, 13)       # 01:00 Saturday: still Friday night
    assert type_at(ts(2026, 11, 14, 1, 0), SEOUL, "KR") == WEEKDAY
    assert type_at(ts(2026, 11, 14, 5, 0), SEOUL, "KR") == WEEKEND
    got = upcoming(ts(2026, 11, 13, 12, 0), SEOUL, "KR")
    assert got["2026-11-12"] == WEEKDAY and got["2026-11-14"] == WEEKEND and len(got) == 9


def test_the_holiday_country_is_the_time_zones(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 9, 12, 0))
    assert country_for(store) == "KR"
    store.set_meta("weather:home", '{"name": "Tel Aviv", "lat": 32.08, "lon": 34.78, "country_code": "IL"}')   # an older server's home area
    assert country_for(store) == "KR"
    store.set_profile(timezone="Asia/Jerusalem")
    assert country_for(store) == "IL" and p.public()["holiday_country"] == "IL"


# ---------------------------------------------------------------------------------------------------------- learning
def test_two_windows_from_mixed_weekday_and_weekend_activity(tmp_path):
    store = Store()
    mixed(store, date(2026, 11, 9))
    days = {d: s for d, s in ((k.split(":", 1)[1], {i for i, b in enumerate(v) if b == "1"})
                              for k, v in ((f"usage_slots:{(date(2026, 11, 9) - timedelta(days=i)).isoformat()}",
                                            store.meta(f"usage_slots:{(date(2026, 11, 9) - timedelta(days=i)).isoformat()}") or "")
                                           for i in range(1, 29))) if s}
    got = learn_windows(days, "KR")
    assert (got[WEEKDAY]["wake_start"], got[WEEKDAY]["wake_end"]) == ("07:00", "22:00")
    assert (got[WEEKEND]["wake_start"], got[WEEKEND]["wake_end"]) == ("10:00", "23:30")
    # a public holiday on a weekday counts with the weekends: 한글날 (Fri 2026-10-09) with a late start goes to the weekend window
    assert day_type(date(2026, 10, 9), "KR") == WEEKEND


def test_thin_weekend_data_means_weekends_follow_the_weekday_hours(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 9, 12, 0))
    mixed(store, date(2026, 11, 9), days=28, skip_weekends=6)     # only a few weekend days are left
    learned = p.learned_windows()
    assert learned[WEEKDAY] is not None and learned[WEEKEND] is None
    p.check_hours(force=True)
    assert p.suggestion(WEEKEND) is None
    assert p.window(WEEKEND) == ("07:00", "22:00", "default")
    assert p.public()["weekend_source"] == "default" and p.public()["weekend_start"] == p.public()["wake_start"]


def test_suggestions_per_day_type_and_accepting_one(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 9, 12, 0))
    mixed(store, date(2026, 11, 9), weekday=("07:00", "22:00"), weekend=("10:00", "23:30"))
    assert p.check_hours(force=True) is None                       # weekdays fit 07:00–22:00 already
    sug = p.suggestion(WEEKEND)
    assert sug["day_type"] == WEEKEND and (sug["wake_start"], sug["wake_end"]) == ("10:00", "23:30")
    p.answer_hours(sug["id"], accept=True)
    assert p.window(WEEKEND) == ("10:00", "23:30", "learned") and p.window(WEEKDAY) == ("07:00", "22:00", "default")


def test_manual_hours_per_day_type(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 9, 12, 0))
    p.update(weekend_start="09:00")
    assert p.window(WEEKEND) == ("09:00", "22:00", "manual") and p.window(WEEKDAY) == ("07:00", "22:00", "default")
    p.update(wake_start="06:30")
    assert p.window(WEEKDAY)[2] == "manual" and p.window(WEEKEND)[0] == "09:00"
    p.update(weekend_start="", weekend_end="")                     # weekends follow the weekdays again
    assert p.window(WEEKEND) == ("06:30", "22:00", "default")


def test_auto_adjust_never_changes_hours_set_by_hand(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 9, 12, 0))
    mixed(store, date(2026, 11, 9), weekday=("09:00", "23:00"), weekend=("10:00", "23:30"))
    p.update(auto_hours=True, wake_start="07:00", wake_end="22:00")   # sent by the owner: manual, even though equal to the default
    p.update(wake_start="07:30")
    p.check_hours(force=True)
    assert p.window(WEEKDAY) == ("07:30", "22:00", "manual")        # not changed...
    assert p.suggestion(WEEKDAY)["wake_start"] == "09:00"            # ...only offered
    assert p.window(WEEKEND) == ("10:00", "23:30", "learned")       # the weekend was untouched: auto adjust applied it
    assert p.public()["learned"][WEEKDAY]["wake_start"] == "09:00" and p.public()["learned"][WEEKEND] is None


# ---------------------------------------------------------------------------------------------------------- following the day type
async def test_proactive_passes_and_follow_ups_on_a_saturday_vs_a_monday(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 14, 8, 0))       # Saturday 08:00
    p.update(enabled=True, weekend_start="09:30", weekend_end="23:30")
    assert not p.awake() and p.day_type_at() == WEEKEND
    assert await p.tick() is None and p.public()["last_pass"]["reason_code"] == "outside_waking_hours"
    assert p.wake_start_today() == ts(2026, 11, 13, 7, 0)            # still before today's 09:30: Friday's start
    clock.t = ts(2026, 11, 16, 8, 0)                                  # Monday 08:00
    assert p.awake() and p.day_type_at() == WEEKDAY and p.wake_start_today() == ts(2026, 11, 16, 7, 0)
    clock.t = ts(2026, 11, 13, 23, 0)                                 # Friday 23:00: weekday hours ended at 22:00
    f = Followups(store, clock=clock, next_wake=p.next_awake)
    assert f._when_awake(clock.t) == ts(2026, 11, 14, 9, 30)          # Saturday's start, not 07:00
    assert p.next_awake(ts(2026, 11, 15, 23, 45)) == ts(2026, 11, 16, 7, 0)   # Sunday night -> Monday morning
    assert p.public()["today_start"] == "07:00" and p.public()["day_type"] == WEEKDAY


def test_the_quiet_slot_of_a_night_before_a_weekend():
    """Up until 04:00 before weekend mornings; before weekday mornings up until 01:00 and again from 04:30: tonight (a Friday) is
    judged from the nights before weekends only."""
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    today = date(2026, 11, 13)
    for i in range(0, 29):
        d = today - timedelta(days=i)
        bits = ["0"] * 96
        bits[48] = "1"                                               # noon: every day is recorded
        weekend_morning = day_type(d, "KR") == WEEKEND
        for s in (range(0, 16) if weekend_morning else [*range(0, 4), *range(18, 32)]):
            bits[s] = "1"                                            # this morning's part of last night
        for s in range(88, 96):
            bits[s] = "1"                                            # every evening 22:00-24:00
        store.set_meta("usage_slots:" + d.isoformat(), "".join(bits))
    kind = lambda s: day_type(date.fromisoformat(s), "KR")  # noqa: E731
    friday = QuietHours(store, lambda: "Asia/Seoul", clock=lambda: ts(2026, 11, 13, 12, 0), kind_of=kind)
    monday = QuietHours(store, lambda: "Asia/Seoul", clock=lambda: ts(2026, 11, 9, 12, 0), kind_of=kind)
    plain = QuietHours(store, lambda: "Asia/Seoul", clock=lambda: ts(2026, 11, 13, 12, 0))
    assert friday.quiet_slot(ts(2026, 11, 13, 12, 0))[:2] == (4, 0)
    assert monday.quiet_slot(ts(2026, 11, 9, 12, 0))[:2] == (3, 0)
    assert plain.quiet_slot(ts(2026, 11, 13, 12, 0))[:2] != (4, 0)


# ---------------------------------------------------------------------------------------------------------- the API
async def test_old_apps_see_the_weekday_hours_and_new_apps_both(tmp_path):
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False,
                 home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[])) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        srv.server.config.app.state.proactive.store.set_profile(timezone="Asia/Seoul")
        got = (await c.get("/api/proactive")).json()
        assert got["wake_start"] == "07:00" and got["weekend_start"] == "07:00" and got["weekend_source"] == "default"
        assert got["holiday_country"] == "KR" and len(got["day_types"]) == 9 and got["day_type"] in (WEEKDAY, WEEKEND)
        r = (await c.put("/api/proactive", json={"wake_start": "06:30"})).json()      # an old app: weekday only
        assert r["wake_start"] == "06:30" and r["hours_source"] == "manual" and r["weekend_start"] == "06:30" and r["weekend_source"] == "default"
        r = (await c.put("/api/proactive", json={"weekend_start": "09:00", "weekend_end": "23:30"})).json()
        assert (r["weekend_start"], r["weekend_end"], r["weekend_source"]) == ("09:00", "23:30", "manual") and r["wake_start"] == "06:30"
        assert (await c.put("/api/proactive", json={"weekend_start": "9"})).status_code == 422
        assert (await c.post("/api/proactive/hours/learned/weekend/apply")).status_code == 404
        pro = srv.server.config.app.state.proactive
        today = datetime.fromtimestamp(pro.clock(), SEOUL).date()
        mixed(pro.store, today, weekday=("09:00", "23:00"), weekend=("10:00", "23:30"))
        got = (await c.get("/api/proactive")).json()
        assert got["learned"]["weekday"]["wake_start"] == "09:00"
        r = (await c.post("/api/proactive/hours/learned/weekday/apply")).json()
        assert r["wake_start"] == "09:00" and r["hours_source"] == "learned" and r["learned"]["weekday"] is None
