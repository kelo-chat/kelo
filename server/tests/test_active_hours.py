"""Learned contact hours (active_hours.py, proactive.py, followups.py): the window from the owner's own activity, past midnight too,
not enough data, the suggestion threshold and its 14-day pause after "keep current", auto-adjust on and off, and the live-awake signal
(fake clock; activity written straight into the store)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from assistant.active_hours import activity_days, circular_gap, differs, learn_window
from assistant.followups import Followups
from assistant.home import Home
from assistant.proactive import Proactive, in_waking_hours, wake_started_at
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running

SEOUL = ZoneInfo("Asia/Seoul")


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def slots(start: str, end: str) -> set[int]:
    """The 15-minute slots from start to end (end not included; past midnight wraps)."""
    a = int(start[:2]) * 4 + int(start[3:]) // 15
    b = int(end[:2]) * 4 + int(end[3:]) // 15
    n = (b - a) % 96 or 96
    return {(a + i) % 96 for i in range(n)}


def use(store: Store, day: datetime, active: set[int]) -> None:
    bits = ["0"] * 96
    for s in active:
        bits[s] = "1"
    store.set_meta("usage_slots:" + day.strftime("%Y-%m-%d"), "".join(bits))


def night_owl(store: Store, now: float, days: int, start="10:00", end="01:00") -> None:
    today = datetime.fromtimestamp(now, SEOUL)
    for i in range(1, days + 1):
        use(store, today - timedelta(days=i), slots(start, end))


def setup(tmp_path, t=None):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    store.add_message("user", "안녕")
    clock = Clock(t or ts(2026, 11, 9, 12, 0))
    p = Proactive(store, Home(tmp_path / "home"), stream_fn=None, model=lambda: None, clock=clock)
    return store, clock, p


# ---------------------------------------------------------------------------------------------------------- the window
def test_a_regular_day_and_a_day_past_midnight():
    assert learn_window({f"d{i}": slots("08:00", "21:00") for i in range(10)}) == {"wake_start": "08:00", "wake_end": "21:00",
                                                                                 "active_days": 10}
    got = learn_window({f"d{i}": slots("10:00", "02:00") for i in range(10)})
    assert got["wake_start"] == "10:00" and got["wake_end"] == "02:00"
    # a few odd hours do not move it: one 04:00 wake-up among 20 days
    days = {f"d{i}": slots("09:00", "23:00") for i in range(20)}
    days["d0"] |= {16}
    assert learn_window(days)["wake_start"] == "09:00"
    assert circular_gap(23 * 60 + 30, 30) == 60 and differs({"wake_start": "10:00", "wake_end": "01:00"}, "07:00", "22:00")
    assert not differs({"wake_start": "07:30", "wake_end": "22:30"}, "07:00", "22:00")


def test_not_enough_days_or_no_usable_window():
    assert learn_window({f"d{i}": slots("08:00", "21:00") for i in range(6)}) is None
    assert learn_window({f"d{i}": slots("08:00", "10:00") for i in range(10)}) is None          # too short to trust
    assert learn_window({f"d{i}": set(range(96)) for i in range(10)}) is None                  # around the clock


def test_windows_past_midnight_work_wherever_contact_hours_are_read():
    assert in_waking_hours(ts(2026, 11, 9, 0, 30), "10:00", "01:00", SEOUL)
    assert not in_waking_hours(ts(2026, 11, 9, 1, 0), "10:00", "01:00", SEOUL)
    assert in_waking_hours(ts(2026, 11, 9, 23, 30), "10:00", "01:00", SEOUL)
    assert wake_started_at(ts(2026, 11, 9, 0, 30), "10:00", SEOUL) == ts(2026, 11, 8, 10, 0)
    from assistant.followups import next_awake
    assert next_awake(ts(2026, 11, 9, 0, 30), "10:00", "01:00", SEOUL) == ts(2026, 11, 9, 0, 30)
    assert next_awake(ts(2026, 11, 9, 3, 0), "10:00", "01:00", SEOUL) == ts(2026, 11, 9, 10, 0)


def test_activity_comes_from_app_use_and_the_owners_own_messages(tmp_path):
    store, clock, p = setup(tmp_path)
    store._run("INSERT INTO messages(role, content, created_at) VALUES('user', 'late', ?)", (ts(2026, 11, 8, 23, 50),))
    store._run("INSERT INTO messages(role, content, created_at) VALUES('user', '[Handoff] x', ?)", (ts(2026, 11, 8, 4, 0),))
    use(store, datetime(2026, 11, 7, tzinfo=SEOUL), {40})
    days = activity_days(store, SEOUL, clock.t, ("[Handoff]",))
    assert 95 in days["2026-11-08"] and 16 not in days["2026-11-08"] and days["2026-11-07"] == {40}


# ---------------------------------------------------------------------------------------------------------- the suggestion
async def test_a_night_owl_gets_a_suggestion_and_keep_current_holds_it_for_14_days(tmp_path):
    store, clock, p = setup(tmp_path)
    night_owl(store, clock.t, 5)
    assert p.check_hours() is None                                    # 5 days: not enough
    night_owl(store, clock.t, 14)
    assert p.check_hours() is None                                    # already looked today
    sug = p.check_hours(force=True)
    assert sug["wake_start"] == "10:00" and sug["wake_end"] == "01:00" and sug["current_start"] == "07:00" and sug["active_days"] >= 7   # weekdays only
    assert p.public()["hours_suggestion"]["id"] == sug["id"] and p.config()["wake_start"] == "07:00"   # proposed, not changed
    p.answer_hours(sug["id"], accept=False)
    assert p.public()["hours_suggestion"] is None and p.config()["hours_source"] == "default"
    clock.t += 13 * 86400
    night_owl(store, clock.t, 14)
    assert await p.tick() is None and p.hours_suggestion() is None    # within 14 days of "keep current"
    clock.t += 2 * 86400
    night_owl(store, clock.t, 14)
    await p.tick()
    again = p.hours_suggestion()
    assert again and again["id"] != sug["id"]
    p.answer_hours(again["id"], accept=True)
    cfg = p.config()
    assert (cfg["wake_start"], cfg["wake_end"], cfg["hours_source"]) == ("10:00", "01:00", "learned")
    assert p.check_hours(force=True) is None                          # the setting fits now
    p.update(wake_end="02:00")
    assert p.config()["hours_source"] == "manual"
    p.update(wake_end="02:00", daily_regular=1)                       # the same hours sent again: still the owner's own
    assert p.config()["hours_source"] == "manual"


def test_a_small_difference_is_not_worth_asking(tmp_path):
    store, clock, p = setup(tmp_path)
    night_owl(store, clock.t, 14, "07:30", "22:30")
    assert p.check_hours(force=True) is None


def test_auto_adjust_applies_it_without_asking(tmp_path):
    store, clock, p = setup(tmp_path)
    p.update(auto_hours=True)
    night_owl(store, clock.t, 14)
    assert p.check_hours(force=True) is None
    cfg = p.config()
    assert (cfg["wake_start"], cfg["wake_end"], cfg["hours_source"]) == ("10:00", "01:00", "learned")
    assert p.state()["hours_auto_applied"]["from"] == ["07:00", "22:00"]


# ---------------------------------------------------------------------------------------------------------- live signal
def test_a_recent_chat_message_means_awake_now(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 9, 23, 30))
    assert not p.owner_live()                                          # "안녕" was written at the real time, not near the fake clock
    store._run("INSERT INTO messages(role, content, created_at) VALUES('user', 'hi', ?)", (clock.t - 20 * 60,))
    assert p.owner_live() and not p.awake()                           # awake by the live signal, still outside the contact hours
    clock.t += 15 * 60
    assert not p.owner_live()


def test_a_due_follow_up_goes_out_at_night_while_the_owner_is_active(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 9, 23, 30))
    f = Followups(store, clock=clock, live=lambda t: p.owner_live(t))
    assert f._when_awake(clock.t - 60) == ts(2026, 11, 10, 7, 0)       # nobody around: the next morning
    store._run("INSERT INTO messages(role, content, created_at) VALUES('user', 'hi', ?)", (clock.t - 5 * 60,))
    assert f._when_awake(clock.t - 60) == clock.t - 60                # the owner is up: now
    assert f._when_awake(clock.t + 3600) == ts(2026, 11, 10, 7, 0)     # not yet due: the contact hours decide


async def test_proactive_messages_keep_to_the_contact_hours_even_when_live(tmp_path):
    store, clock, p = setup(tmp_path, ts(2026, 11, 9, 23, 30))
    p.update(enabled=True)
    store._run("INSERT INTO messages(role, content, created_at) VALUES('user', 'hi', ?)", (clock.t - 60,))
    assert await p.tick() is None and p.public()["last_pass"]["reason_code"] == "outside_waking_hours"


# ---------------------------------------------------------------------------------------------------------- over HTTP
async def test_accept_and_keep_over_http(tmp_path):
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False,
                 home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[])) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/profile", json={"timezone": "Asia/Seoul"})
        pro = srv.server.config.app.state.proactive
        night_owl(pro.store, pro.clock(), 14)   # two weeks: enough weekdays whatever today is
        sug = pro.check_hours(force=True)
        got = (await c.get("/api/proactive")).json()
        assert got["hours_suggestion"]["id"] == sug["id"] and got["auto_hours"] is False and got["hours_source"] == "default"
        assert (await c.post("/api/proactive/hours/nope/accept")).status_code == 404
        r = (await c.post(f"/api/proactive/hours/{sug['id']}/accept")).json()
        assert r["hours_source"] == "learned" and r["hours_suggestion"] is None
        assert (await c.put("/api/proactive", json={"auto_hours": True})).json()["auto_hours"] is True


def test_changing_the_hours_by_hand_updates_or_closes_the_open_suggestion(tmp_path):
    store, clock, p = setup(tmp_path)
    night_owl(store, clock.t, 14)
    sug = p.check_hours(force=True)
    p.update(wake_start="08:00")                                      # still far from 10:00–01:00: the row shows the new current hours
    got = p.hours_suggestion()
    assert got["id"] == sug["id"] and (got["current_start"], got["current_end"]) == ("08:00", "22:00")
    p.update(wake_start="10:30", wake_end="01:00")                    # about the suggested hours: nothing left to ask
    assert p.hours_suggestion() is None and p.config()["hours_source"] == "manual"
