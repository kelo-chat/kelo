"""Things said in chat become follow items (follow/upcoming.py): the nightly look-back (reflection.py) adds dated things from the
conversation only with a quote found in its messages, not past or too far, at most five a night, not twice; the runner plans them
when they enter the look-ahead window; the stop path works for them; the briefing names the ones made since yesterday."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from assistant.activity import Activity
from assistant.briefing import Briefing
from assistant.follow.items import ItemFinder
from assistant.follow.plan import FollowPlan
from assistant.follow.runner import FollowRunner
from assistant.follow.store import FollowStore
from assistant.follow.upcoming import PER_NIGHT, UpcomingExtractor
from assistant.home import Home
from assistant.proactive import Proactive
from assistant.providers import Done, TextDelta
from assistant.reflection import Reflection
from assistant.store import Store
from assistant.weather import Weather

SEOUL = ZoneInfo("Asia/Seoul")
ASSISTANT_SAID = "예약 창은 2월 11일에 열려요. 그날 바로 잡아 두세요."   # the assistant's own words in the conversation
NOW = datetime(2026, 11, 16, 3, 50, tzinfo=SEOUL).timestamp()


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


class NoWeather(Weather):
    def __init__(self):
        pass

    def current_place(self):
        return None


class StubPlanner:
    """The planner without a model: every item gets a lead reminder the day before."""

    def __init__(self):
        self.planned: list[str] = []

    async def plan(self, item, prefs=""):
        self.planned.append(item.key)
        return FollowPlan(checks=[], prep=[], tell={"lead": ["-1d"], "on_change": True})


def night(tmp_path, reply, clock_t=NOW):
    """Reflection with the upcoming extractor wired as the server does; the model answers `reply` (a dict)."""
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    clock = Clock(clock_t)
    follow = FollowStore(store, clock=clock)
    calls: list[Any] = []

    async def fn(provider, request, **kw):
        calls.append(request)
        yield TextDelta(json.dumps(reply, ensure_ascii=False))
        yield Done("stop")

    r = Reflection(store, Home(tmp_path / "home"), Activity(store, clock), fn, lambda: ("p", "m"), clock=clock)
    r.upcoming = UpcomingExtractor(follow, tz=lambda: SEOUL, clock=clock)
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,'owner')", ("user", "2월 11일에 버스 예약 열리면 알려줘", ts(2026, 11, 15, 20, 0)))
    store._run("INSERT INTO messages(role, content, created_at) VALUES(?,?,?)", ("assistant", ASSISTANT_SAID, ts(2026, 11, 15, 20, 1)))
    return store, follow, r, calls, clock


def bus(**kw) -> dict[str, Any]:
    return {"title": "버스 예약 창", "date": "2027-02-11", "what": "bus booking window opens; book the seat", "quote": "2월 11일에 버스 예약 열리면",
            **kw}


def reply(*items) -> dict[str, Any]:
    return {"new": [], "update": [], "drop": [], "goals": [], "reasons": ["one dated thing"], "upcoming": list(items)}


async def test_a_booking_that_opens_later_becomes_one_chat_item_with_its_quote(tmp_path):
    store, follow, r, calls, _ = night(tmp_path, reply(bus()))
    await r.run()
    items = follow.list_items()
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "chat" and it["state"] == "later" and it["title"] == "버스 예약 창"
    assert it["key"] == "chat:2027-02-11:버스-예약-창"
    assert it["refs"]["quote"] == "2월 11일에 버스 예약 열리면" and it["refs"]["what"].startswith("bus booking window")
    assert datetime.fromtimestamp(it["starts_at"], SEOUL).date().isoformat() == "2027-02-11"
    assert "Already kept" in calls[0].messages[1]["content"] and "Today: 2026-11-16" in calls[0].messages[1]["content"]
    assert "upcoming" in calls[0].messages[0]["content"]


async def test_a_made_up_quote_a_past_date_and_a_far_date_are_left_out(tmp_path):
    made_up = bus(title="항공권 세일", date="2027-03-01", quote="세일은 3월 1일에 시작한다고 했어요")
    past = bus(title="지난 행사", date="2026-10-01")
    far = bus(title="먼 미래 행사", date="2029-06-01")
    store, follow, r, _, _ = night(tmp_path, reply(made_up, past, far, bus(date="not-a-date", title="날짜 틀림")))
    await r.run()
    assert follow.list_items() == []


async def test_at_most_five_a_night_and_a_repeat_is_not_added_twice(tmp_path):
    items = [bus(title=f"예약 창 {i}", date=f"2027-02-{10 + i:02d}") for i in range(PER_NIGHT + 2)]
    store, follow, r, _, clock = night(tmp_path, reply(*items))
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,'owner')", ("user", "2월 10일, 11일, 12일, 13일, 14일, 15일, 16일 예약 창", ts(2026, 11, 15, 20, 2)))
    await r.run()
    assert len(follow.list_items()) == PER_NIGHT
    # a second night names the same ones again (and one new): only the new one is added
    store._run("INSERT INTO messages(role, content, created_at) VALUES(?,?,?)", ("assistant", ASSISTANT_SAID, ts(2026, 11, 16, 20, 0)))
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,'owner')", ("user", "예약 창 얘기 계속, 3월 5일에 하나 더", ts(2026, 11, 16, 20, 1)))
    clock.t = ts(2026, 11, 17, 3, 50)
    r.stream_fn = _answer(reply(items[0], bus(title="예약 창 9", date="2027-03-05", quote="3월 5일에 하나 더")))   # the same window named again, plus one new
    await r.run()
    titles = sorted(it["title"] for it in follow.list_items())
    assert "예약 창 9" in titles and len(titles) == PER_NIGHT + 1


def _answer(reply_dict):
    async def fn(provider, request, **kw):
        yield TextDelta(json.dumps(reply_dict, ensure_ascii=False))
        yield Done("stop")
    return fn


async def test_a_title_already_followed_on_the_same_day_from_a_calendar_is_not_added(tmp_path):
    store, follow, r, _, _ = night(tmp_path, reply(bus(title="버스 예약 창", date="2027-02-11")))
    from assistant.follow.items import UpcomingItem
    follow.save_item(UpcomingItem(key="cal:bus", kind="other", title="버스 예약 창", starts_at=ts(2027, 2, 11, 9), ends_at=None,
                                  place="", refs={}, source="calendar"), state="watching")
    await r.run()
    assert [it["source"] for it in follow.list_items()] == ["calendar"]


async def test_a_chat_item_waits_until_its_window_then_is_planned_and_a_stop_keeps_it_out(tmp_path):
    store, follow, r, _, clock = night(tmp_path, reply(bus()))
    await r.run()
    key = follow.list_items()[0]["key"]
    planner = StubPlanner()
    runner = FollowRunner(follow, ItemFinder(calendar=lambda: [], tz=SEOUL), planner, [], activity=None, jitter=False, clock=clock)
    await runner.tick(NOW)
    assert follow.get_item(key)["state"] == "later" and planner.planned == []   # 2027-02-11 is months ahead
    await runner.tick(ts(2027, 1, 30, 9))                                       # 12 days ahead: inside the 14-day look-ahead window
    saved = follow.get_item(key)
    assert saved["state"] == "watching" and planner.planned == [key]
    assert saved["plan"]["tell"]["lead"] == ["-1d"]
    await runner.tick(ts(2027, 2, 12, 12))                                      # after its end (starts + 2 h): done
    assert follow.get_item(key)["state"] == "done"

    # a stopped chat item (the owner said it is not needed; follow_stop or the API) is not planned when its window comes
    store2, follow2, r2, _, clock2 = night(tmp_path / "b", reply(bus()))
    await r2.run()
    key2 = follow2.list_items()[0]["key"]
    assert follow2.stop_item(key2) is True
    planner2 = StubPlanner()
    runner2 = FollowRunner(follow2, ItemFinder(calendar=lambda: [], tz=SEOUL), planner2, [], activity=None, jitter=False, clock=clock2)
    await runner2.tick(ts(2027, 1, 30, 9))
    assert follow2.get_item(key2)["state"] == "stopped" and planner2.planned == []


async def test_the_briefing_names_the_chat_items_made_lately_in_one_line(tmp_path):
    store, follow, r, _, clock = night(tmp_path, reply(bus()))
    await r.run()
    extractor = r.upcoming
    h = Home(tmp_path / "bh")
    p = Proactive(store, h, stream_fn=None, model=lambda: None, clock=clock)
    b = Briefing(store, NoWeather(), p, calendar=None, clock=clock, language=lambda: "en", looking_after=extractor.recent_titles)
    card = await b.build(NOW, force=True)
    assert card["looking_after"] == ["버스 예약 창"]
    assert "From yesterday's chats, Kelo will look after: 버스 예약 창" in b.plain(card)
    assert await b.build(NOW) is not None   # a day with only this line is not quiet

    # nothing new since: no line
    clock.t = NOW + 3 * 86400
    card2 = await b.build(clock.t, force=True)
    assert card2["looking_after"] == [] and "will look after" not in b.plain(card2)


async def test_a_quote_only_kelo_said_is_not_taken_so_its_date_is_not_either(tmp_path):
    # the date and the quote come from the assistant's own answer, the owner never wrote them: nothing is kept
    said = bus(title="예약 창", date="2027-02-11", quote="예약 창은 2월 11일에 열려요")
    store, follow, r, _, _ = night(tmp_path, reply(said))
    await r.run()
    assert follow.list_items() == []


async def test_a_day_the_owner_never_wrote_is_left_out_even_with_a_quote_of_theirs(tmp_path):
    # the owner asked about a trip and gave no date; a date the model took from elsewhere (day 19) is not kept
    store, follow, r, _, _ = night(tmp_path, reply(bus(title="여행 날", date="2027-02-19", quote="버스 날씨 알려줘")))
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,'owner')", ("user", "버스 날씨 알려줘", ts(2026, 11, 15, 20, 3)))
    await r.run()
    assert follow.list_items() == []


async def test_a_thing_already_in_the_list_under_another_source_is_linked_not_added(tmp_path):
    from assistant.follow.items import UpcomingItem
    store, follow, r, _, _ = night(tmp_path, reply(bus(title="항공편 출발", date="2027-02-11", same_as="cal:flight-9c")))
    follow.save_item(UpcomingItem(key="cal:flight-9c", kind="flight", title="Flight to 상하이 (9C 7050)", starts_at=ts(2027, 2, 11, 14, 55),
                                  ends_at=None, place="", refs={"flight": "9C7050", "date": "2027-02-11"}, source="calendar"), state="watching")
    await r.run()
    assert [it["key"] for it in follow.list_items()] == ["cal:flight-9c"]


async def test_the_model_sees_the_calendar_items_it_must_not_repeat(tmp_path):
    from assistant.follow.items import UpcomingItem
    store, follow, r, calls, _ = night(tmp_path, reply())
    follow.save_item(UpcomingItem(key="cal:flight-9c", kind="flight", title="Flight to 상하이 (9C 7050)", starts_at=ts(2026, 11, 29, 14, 55),
                                  ends_at=None, place="", refs={"flight": "9C7050"}, source="calendar"), state="watching")
    await r.run()
    shown = calls[0].messages[1]["content"]
    assert "cal:flight-9c" in shown and "Flight to 상하이 (9C 7050) on 2026-11-29" in shown
    assert "same_as" in calls[0].messages[0]["content"] and "upcoming_drop" in calls[0].messages[0]["content"]


async def test_an_item_the_owner_corrected_is_removed_but_a_calendar_item_is_never_touched(tmp_path):
    from assistant.follow.items import UpcomingItem
    store, follow, r, _, clock = night(tmp_path, reply(bus()))
    await r.run()
    key = follow.list_items()[0]["key"]
    follow.save_item(UpcomingItem(key="cal:keep", kind="other", title="Dinner", starts_at=ts(2026, 12, 1, 19), ends_at=None,
                                  place="", refs={}, source="calendar"), state="watching")
    store._run("INSERT INTO messages(role, content, created_at, author) VALUES(?,?,?,'owner')", ("user", "그 버스는 취소했어", ts(2026, 11, 16, 9, 0)))
    clock.t = ts(2026, 11, 16, 3, 50) + 86400
    r.stream_fn = _answer({"new": [], "update": [], "drop": [], "goals": [], "reasons": ["corrected"],
                           "upcoming_drop": [key, "cal:keep"]})
    await r.run()
    assert [it["key"] for it in follow.list_items()] == ["cal:keep"]


def test_a_chat_item_without_a_time_is_all_day_not_nine_oclock():
    from assistant.follow.items import make_chat_item
    day = make_chat_item("여행 날", "2027-02-11", None, tz=SEOUL)
    assert day.refs.get("all_day") == "true" and "time" not in day.refs
    assert datetime.fromtimestamp(day.starts_at, SEOUL).strftime("%Y-%m-%d %H:%M") == "2027-02-11 00:00"
    timed = make_chat_item("여행 날", "2027-02-11", "07:30", tz=SEOUL)
    assert "all_day" not in timed.refs and datetime.fromtimestamp(timed.starts_at, SEOUL).strftime("%H:%M") == "07:30"


async def test_a_chat_item_the_owner_removed_is_not_added_again_by_a_later_look(tmp_path):
    store, follow, r, _, clock = night(tmp_path, reply(bus()))
    await r.run()
    key = follow.list_items()[0]["key"]
    follow.remove_item(key)
    assert follow.list_items() == []
    clock.t = ts(2026, 11, 16, 3, 50) + 86400
    r.stream_fn = reply(bus())
    await r.run()
    assert follow.list_items() == []
