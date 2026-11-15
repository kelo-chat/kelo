"""Big events ahead (big_events.py): trips, flights, stays and several-day events found in code; the proactive pass mentions each
once at each lead time (7 days and 1 day before by default, the owner can change them), with the model writing the message only
then; the briefing's short "coming up" line. Fake model, fake clock, scripted calendar."""

from datetime import date, datetime

import pytest

from assistant import big_events
from assistant.briefing import Briefing
from assistant.weather import Weather

from test_proactive import SEOUL, Judge, setup, ts


def ev(title, start, end=None, *, all_day=False, where="", day=None):
    e = {"id": title, "title": title, "start": start, "where": where, "all_day": all_day}
    if end is not None:
        e["end"] = end
    if all_day:
        e["day"] = day or datetime.fromtimestamp(start, SEOUL).date().isoformat()
    return e


def test_what_counts_as_big():
    k = lambda e: big_events.kind(e, SEOUL)   # noqa: E731
    assert k(ev("상하이 여행", ts(2026, 11, 16), ts(2026, 11, 19), all_day=True)) == "trip"
    assert k(ev("KE893 ICN-PVG", ts(2026, 11, 16, 9), ts(2026, 11, 16, 11))) == "flight"
    assert k(ev("대한항공 KE893", ts(2026, 11, 16, 9), ts(2026, 11, 16, 11))) == "flight"
    assert k(ev("Check in", ts(2026, 11, 16, 15), ts(2026, 11, 16, 16), where="Hotel Nikko")) == "stay"
    assert k(ev("숙소 체크인", ts(2026, 11, 16, 15), ts(2026, 11, 16, 16))) == "stay"
    assert k(ev("Workshop", ts(2026, 11, 16), ts(2026, 11, 18), all_day=True)) == "days"          # two all-day days
    assert k(ev("Camp", ts(2026, 11, 13, 18), ts(2026, 11, 15, 12))) == "days"                    # Friday evening to Sunday
    assert k(ev("Late dinner", ts(2026, 11, 13, 22), ts(2026, 11, 14, 1))) is None                # past midnight, but short
    assert k(ev("Dentist", ts(2026, 11, 16, 10), ts(2026, 11, 16, 11))) is None
    assert k(ev("Holiday", ts(2026, 11, 16), ts(2026, 11, 17), all_day=True)) is None             # one all-day day
    assert k(ev("Semester", ts(2026, 9, 1), ts(2026, 12, 20), all_day=True)) is None              # a period, not an event
    assert k(ev("CS101 lecture", ts(2026, 11, 16, 10), ts(2026, 11, 16, 11))) is None             # a course code is not a flight
    assert k(ev("Parking lot cleanup", ts(2026, 11, 16, 10), ts(2026, 11, 16, 11))) is None
    assert big_events.lead_for(5, [7, 1]) == 7 and big_events.lead_for(1, [7, 1]) == 1 and big_events.lead_for(9, [7, 1]) is None


def calendar(events):
    async def read():
        return [dict(e) for e in events]
    return read


async def test_a_trip_two_weeks_away_is_told_once_at_seven_days_and_once_the_day_before(tmp_path):
    judge = Judge(lambda items: [{"n": n, "urgency": "보통", "reason": "trip", "message": f"상하이 여행 준비: 여권 확인 ({n})"} for n, _ in items])
    trip = ev("상하이 여행", ts(2026, 11, 16), ts(2026, 11, 20), all_day=True)
    dentist = ev("Dentist", ts(2026, 11, 7, 15), ts(2026, 11, 7, 16))
    store, home, clock, p, sent = setup(tmp_path, judge, t=ts(2026, 11, 2, 9), calendar=calendar([trip, dentist]))
    assert p.config()["ahead_days"] == [7, 1]
    # 14 days ahead it is seen, but not yet at a lead time: no candidate, no model call
    assert (await p.run())["status"] == "nothing_new" and not judge.requests
    # 7 days before: told once, the item says "coming up" and asks what to prepare
    clock.t = ts(2026, 11, 9, 9)
    assert (await p.run())["sent"] == 1
    shown = judge.requests[-1].messages[-1]["content"]
    assert "coming up: a trip, in 7 days (2026-11-16 to 2026-11-19)" in shown and "what to prepare" in shown
    assert '"coming up"' in judge.requests[-1].messages[0]["content"]
    assert sent[-1]["message"].startswith("상하이 여행 준비")
    # the next passes and days before the day before: nothing again, no model call
    n = len(judge.requests)
    for day in (9, 10, 12, 14):
        clock.t = ts(2026, 11, day, 13)
        assert (await p.run())["sent"] == 0
    assert len(judge.requests) == n
    # the day before: told once more
    clock.t = ts(2026, 11, 15, 9)
    assert (await p.run())["sent"] == 1 and "tomorrow" in judge.requests[-1].messages[-1]["content"]
    clock.t = ts(2026, 11, 15, 18)
    assert (await p.run())["sent"] == 0
    keys = [r["key"] for r in p.log() if r["status"] == "sent"]
    assert sum(k.startswith("ahead:") and k.endswith(":7") for k in keys) == 1 and sum(k.endswith(":1") for k in keys) == 1


async def test_a_flight_added_late_is_told_at_the_nearest_lead_and_counts_toward_the_daily_limit(tmp_path):
    judge = Judge(lambda items: [{"n": n, "urgency": "보통", "reason": "x", "message": f"m{n}"} for n, _ in items])
    flight = ev("Flight KE893 ICN-PVG", ts(2026, 11, 12, 9), ts(2026, 11, 12, 11))
    store, home, clock, p, sent = setup(tmp_path, judge, t=ts(2026, 11, 9, 9), calendar=calendar([flight]))
    p.update(daily_regular=0)       # no room for a 보통 message today
    assert (await p.run())["sent"] == 0
    assert any(r["key"].startswith("ahead:") and r["status"] == "dropped" and "Over the number" in r["reason"] for r in p.log())
    p.update(daily_regular=2)
    clock.t = ts(2026, 11, 10, 9)   # the next day it may go: still at the 7-day lead (2 days away)
    assert (await p.run())["sent"] == 1 and "a flight, in 2 days" in judge.requests[-1].messages[-1]["content"]


async def test_the_flight_is_not_also_a_timed_alert_the_day_before(tmp_path):
    judge = Judge(lambda items: [{"n": n, "urgency": "오늘", "reason": "x", "message": f"m{n}"} for n, _ in items])
    flight = ev("Flight to Shanghai", ts(2026, 11, 10, 8), ts(2026, 11, 10, 10))
    store, home, clock, p, sent = setup(tmp_path, judge, t=ts(2026, 11, 9, 12), calendar=calendar([flight]))
    assert (await p.run())["sent"] == 1
    assert [r["key"].split(":")[0] for r in p.log()] == ["ahead"]       # one message, not a second one for the same flight


async def test_the_owner_changes_the_lead_times_by_asking_and_in_the_settings(tmp_path):
    judge = Judge(lambda items: [{"n": n, "urgency": "보통", "reason": "x", "message": f"m{n}"} for n, _ in items])
    trip = ev("Tokyo trip", ts(2026, 11, 19), ts(2026, 11, 22), all_day=True)
    store, home, clock, p, sent = setup(tmp_path, judge, t=ts(2026, 11, 9, 9), calendar=calendar([trip]))
    tool = p.ahead_tool()
    assert "7, 1" in await tool.handler({})
    assert "10, 3" in await tool.handler({"days": [3, 10, 3]})
    assert p.config()["ahead_days"] == [10, 3]
    assert (await tool.handler({"days": [30]})).startswith("Error:")
    assert (await p.run())["sent"] == 1 and "in 10 days" in judge.requests[-1].messages[-1]["content"]
    assert "no longer" in await tool.handler({"days": []})
    clock.t = ts(2026, 11, 16, 9)
    assert (await p.run())["status"] == "nothing_new"
    with pytest.raises(ValueError):
        p.update(ahead_days=[1, 2, 3, 4, 5])


class NoWeather(Weather):
    def __init__(self):
        pass

    def current_place(self):
        return None


async def test_the_briefing_has_a_short_coming_up_line_for_this_week(tmp_path):
    from assistant.store import Store
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    now = ts(2026, 11, 9, 7, 30)
    _, _, _, p, _ = setup(tmp_path, Judge(lambda items: []), t=now)
    evs = [ev("Standup", ts(2026, 11, 9, 10), ts(2026, 11, 9, 10, 15)),
           ev("상하이 여행", ts(2026, 11, 12), ts(2026, 11, 15), all_day=True),
           ev("Company holiday", ts(2026, 11, 9), ts(2026, 11, 10), all_day=True),
           ev("Flight to Osaka", ts(2026, 11, 20, 9), ts(2026, 11, 20, 11))]          # past this week: not in the line
    b = Briefing(store, NoWeather(), p, calendar=calendar(evs), clock=lambda: now)
    card = await b.build(now)
    assert [e["title"] for e in card["events"]] == ["Standup"]                          # today's timed events only
    assert [(a["title"], a["kind"], a["first"], a["last"]) for a in card["ahead"]] == [("상하이 여행", "trip", "2026-11-12", "2026-11-14")]
    assert "Coming up: 상하이 여행 (11-12–11-14)" in b.plain(card)
    assert date.fromisoformat(card["ahead"][0]["first"]) > datetime.fromtimestamp(now, SEOUL).date()
