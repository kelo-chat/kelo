"""The day's send times (send_times.py): window / (daily_total + 1 with the briefing), whole minutes, the briefing's part."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from assistant.send_times import plan

SEOUL = ZoneInfo("Asia/Seoul")
D = date(2026, 11, 17)


def at(h: int, m: int, day: date = D) -> float:
    return datetime(day.year, day.month, day.day, h, m, tzinfo=SEOUL).timestamp()


def hhmm(p) -> list[str]:
    return [datetime.fromtimestamp(t, SEOUL).strftime("%H:%M") for t in p.times]


def test_the_owners_case_four_parts_of_four_hours_the_first_is_the_briefings():
    p = plan(D, "08:00", "00:00", 3, at(8, 0), SEOUL)
    assert hhmm(p) == ["12:00", "16:00", "20:00"] and p.briefing == at(8, 0)
    assert p.start == at(8, 0) and p.end == at(0, 0, date(2026, 11, 18)) and p.day == D


def test_the_briefing_off_three_parts_from_the_window_start():
    p = plan(D, "08:00", "00:00", 3, None, SEOUL)
    assert hhmm(p) == ["08:00", "13:20", "18:40"] and p.briefing is None


def test_a_later_briefing_takes_the_nearest_part_and_a_tie_goes_to_the_earlier():
    assert hhmm(plan(D, "08:00", "00:00", 3, at(12, 0), SEOUL)) == ["08:00", "16:00", "20:00"]    # fixed at noon
    assert hhmm(plan(D, "08:00", "00:00", 3, at(8, 30), SEOUL)) == ["12:00", "16:00", "20:00"]    # an offset: still the first
    assert hhmm(plan(D, "08:00", "00:00", 3, at(10, 0), SEOUL)) == ["12:00", "16:00", "20:00"]    # half way: the earlier part
    assert hhmm(plan(D, "08:00", "00:00", 3, at(23, 30), SEOUL)) == ["08:00", "12:00", "16:00"]   # late: the last


def test_whole_minutes_rounded_down():
    assert hhmm(plan(D, "08:00", "23:00", 7, None, SEOUL)) == ["08:00", "10:08", "12:17", "14:25", "16:34", "18:42", "20:51"]


def test_a_window_past_midnight_keeps_its_times_on_the_evenings_day():
    p = plan(D, "18:00", "04:00", 3, None, SEOUL)
    assert p.times == (at(18, 0), at(21, 20), at(0, 40, date(2026, 11, 18))) and p.day == D


def test_a_whole_day_window_and_no_messages_a_day():
    assert hhmm(plan(D, "00:00", "00:00", 3, None, SEOUL)) == ["00:00", "08:00", "16:00"]
    assert plan(D, "08:00", "00:00", 0, None, SEOUL).times == ()
    p = plan(D, "08:00", "00:00", 0, at(8, 0), SEOUL)
    assert p.times == () and p.briefing == at(8, 0)
