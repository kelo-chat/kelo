"""The model's reading of what structure leaves (event_kinds.py): single-day trips and stays named only by words, and the booking
reference of a mail as a field of the structured answer. A scripted `ask` stands for the main model."""

from zoneinfo import ZoneInfo

from assistant import big_events
from assistant.event_kinds import EventKinds, MailBookings
from assistant.follow.items import ItemFinder

from test_big_events import ev, calendar
from test_proactive import SEOUL, Judge, setup, ts

UTC = ZoneInfo("UTC")


def asker(answer, calls=None):
    async def ask(system, user):
        if calls is not None:
            calls.append(user)
        if isinstance(answer, Exception):
            raise answer
        return answer(user) if callable(answer) else answer
    return ask


async def test_one_batched_request_gives_kinds_and_is_cached_per_event_and_update_time():
    calls = []
    trip = ev("Tokyo trip", ts(2026, 11, 12, 9), ts(2026, 11, 12, 20))
    dentist = ev("Dentist", ts(2026, 11, 12, 10), ts(2026, 11, 12, 11))
    k = EventKinds(asker({"kinds": {"0": "trip", "1": "appointment"}}, calls))
    got = await k.classify([trip, dentist], SEOUL)
    assert got == {big_events.event_key(trip): "trip", big_events.event_key(dentist): "appointment"} and len(calls) == 1
    await k.classify([trip, dentist], SEOUL)
    assert len(calls) == 1                                                       # cached
    edited = {**trip, "updated": "2026-11-09T10:00:00Z"}
    await k.classify([edited], SEOUL)
    assert len(calls) == 2                                                       # an edited event is judged again


async def test_a_failure_or_a_wrong_shape_is_an_empty_answer_structure_only():
    e = ev("Tokyo trip", ts(2026, 11, 12, 9), ts(2026, 11, 12, 20))
    for bad in (None, {"kinds": {"0": "vacation"}}, {"kinds": "x"}, RuntimeError("down")):
        assert await EventKinds(asker(bad)).classify([e], SEOUL) == {}
    assert big_events.kind(e, SEOUL) is None and big_events.kind(e, SEOUL, {}) is None


def test_the_model_kind_adds_single_day_stays_and_trips_after_structure():
    e = ev("Tokyo trip", ts(2026, 11, 12, 9), ts(2026, 11, 12, 20))
    key = big_events.event_key(e)
    assert big_events.kind(e, SEOUL, {key: "trip"}) == "trip" and big_events.kind(e, SEOUL, {key: "stay"}) == "stay"
    assert big_events.kind(e, SEOUL, {key: "appointment"}) is None and big_events.kind(e, SEOUL, {key: "other"}) is None
    multi = ev("Camp", ts(2026, 11, 13, 18), ts(2026, 11, 15, 12))
    assert big_events.kind(multi, SEOUL, {big_events.event_key(multi): "other"}) == "days"      # structure wins
    assert big_events.unclassified([e, multi], ts(2026, 11, 9, 9), SEOUL) == [e]


async def test_follow_through_picks_up_a_single_day_trip_the_model_named():
    now = ts(2026, 11, 9, 9)
    e = ev("Trip to Jeju", ts(2026, 11, 12, 9), ts(2026, 11, 12, 20))
    plain = await ItemFinder(calendar=lambda: [e], tz=UTC).find(now)
    assert plain == []                                                           # no model: structure only
    finder = ItemFinder(calendar=lambda: [e], tz=UTC, kinds=EventKinds(asker({"kinds": {"0": "trip"}})))
    items = await finder.find(now)
    assert [(i.kind, i.title) for i in items] == [("trip", "Trip to Jeju")]


async def test_proactive_tells_a_single_day_trip_at_its_lead_time_when_the_model_names_it(tmp_path):
    judge = Judge(lambda items: [{"n": n, "urgency": "보통", "reason": "x", "message": f"m{n}"} for n, _ in items])
    trip = ev("Tokyo trip", ts(2026, 11, 16, 9), ts(2026, 11, 16, 20))
    store, home, clock, p, sent = setup(tmp_path, judge, t=ts(2026, 11, 9, 9), calendar=calendar([trip]))
    p.kinds = EventKinds(asker({"kinds": {"0": "trip"}}))
    assert (await p.run())["sent"] == 1
    assert "coming up: a trip, in 7 days" in judge.requests[-1].messages[-1]["content"]


MAIL = {"id": "m1", "subject": "Your booking", "body": "Confirmation number AB12CD34 for your stay.", "starts_at": ts(2026, 11, 12, 9)}


async def test_the_booking_reference_is_a_field_of_the_model_answer_and_must_be_in_the_mail():
    now = ts(2026, 11, 9, 9)
    good = {"mails": {"0": {"kind": "stay", "booking": "AB12CD34", "flight": ""}}}
    items = await ItemFinder(mail=lambda: [MAIL], tz=UTC, bookings=MailBookings(asker(good))).find(now)
    assert [(i.kind, i.refs.get("booking")) for i in items] == [("stay", "AB12CD34")]
    made_up = {"mails": {"0": {"kind": "stay", "booking": "ZZ99ZZ99", "flight": ""}}}
    assert await ItemFinder(mail=lambda: [MAIL], tz=UTC, bookings=MailBookings(asker(made_up))).find(now) == []   # not in the mail: dropped
    assert await ItemFinder(mail=lambda: [MAIL], tz=UTC).find(now) == []                                           # no model: flight numbers only
    other = {"mails": {"0": {"kind": "other", "booking": "AB12CD34", "flight": ""}}}
    assert await ItemFinder(mail=lambda: [MAIL], tz=UTC, bookings=MailBookings(asker(other))).find(now) == []
    assert await ItemFinder(mail=lambda: [MAIL], tz=UTC, bookings=MailBookings(asker(RuntimeError("down")))).find(now) == []
