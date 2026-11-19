"""The morning briefing (briefing.py): when it goes out (the learned wake time per day type, the morning's first app open after a
night's rest, an offset, a fixed time, once a day, a missed day skipped), what the card holds (empty and full sections, quiet days),
the one model call a day and the card without it when the model is not there, the contact-hours line, and the API. Scripted model
and fake weather services only."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from assistant import i18n
from assistant.briefing import Briefing
from assistant.home import Home
from assistant.proactive import Proactive
from assistant.providers import TextDelta
from assistant.providers.types import QuotaExhausted
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.weather import Place, Weather

from test_server import AUTH, TOKEN, Running
from weather_fakes import SEOUL, FakeWeather, open_meteo, ts

SEOUL_PLACE = Place("서울특별시", 37.566, 126.978, "KR", "서울특별시", "Asia/Seoul")


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Followups:
    def __init__(self, items: list[dict[str, Any]] | None = None):
        self._items = items or []

    def items(self) -> list[dict[str, Any]]:
        return self._items


class Mail:
    def __init__(self, unread: list[dict[str, Any]] | None = None):
        self.unread = unread or []

    def accounts(self) -> list[dict[str, Any]]:
        return [{"name": "me@example.com"}] if self.unread else []

    async def list(self, name: str, unread: bool = True, limit: int = 10, skip_bulk: bool = False) -> list[dict[str, Any]]:
        return self.unread


class Model:
    """A scripted model: answers `reply` (or raises `error`) and counts its calls."""

    def __init__(self, reply: dict[str, Any] | None = None, error: Exception | None = None):
        self.reply, self.error, self.calls = reply or {"line": "Take an umbrella for the afternoon.", "important": []}, error, 0

    async def __call__(self, provider: str, req: Any, **_: Any):
        self.calls += 1
        if self.error is not None:
            raise self.error
        yield TextDelta(json.dumps(self.reply))


def setup(tmp_path, t: float, *, events: list[dict[str, Any]] | None = None, followups: list[dict[str, Any]] | None = None,
          unread: list[dict[str, Any]] | None = None, model: Model | None = None, home: bool = True, fake: FakeWeather | None = None):
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    store.add_message("user", "안녕")
    clock = Clock(t)
    h = Home(tmp_path / "home")
    p = Proactive(store, h, stream_fn=None, model=lambda: None, clock=clock)
    fake = fake or FakeWeather()
    w = Weather(store, fetcher=fake, clock=clock, language=lambda: "en")
    if home:
        w.set_here(SEOUL_PLACE.lat, SEOUL_PLACE.lon, SEOUL_PLACE.name, "KR")
    sent: list[tuple[dict[str, Any], str, bool]] = []

    async def deliver(card: dict[str, Any], text: str, push: bool) -> int:
        sent.append((card, text, push))
        return store.add_message("assistant", text)

    async def calendar() -> list[dict[str, Any]]:
        return list(events or [])
    m = model or Model()
    b = Briefing(store, w, p, home=h, followups=Followups(followups), mail=Mail(unread), calendar=calendar, stream_fn=m,
                 model=lambda: ("scripted", "m"), deliver=deliver, clock=clock, language=lambda: "en")
    return store, clock, p, b, sent, m, fake


def use(store: Store, day: datetime, start: str, end: str) -> None:
    """Mark the app as used on `day` from start to end (15-minute slots; end not included)."""
    bits = list(store.meta("usage_slots:" + day.strftime("%Y-%m-%d")) or "0" * 96)
    a = int(start[:2]) * 4 + int(start[3:]) // 15
    z = int(end[:2]) * 4 + int(end[3:]) // 15
    for s in range(a, z):
        bits[s] = "1"
    store.set_meta("usage_slots:" + day.strftime("%Y-%m-%d"), "".join(bits))


# ---------------------------------------------------------------------------------------------------------- when
async def test_once_a_day_at_the_start_of_the_contact_hours(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 29))   # a Monday
    p.update(wake_start="07:30")
    assert await b.tick() is None and not sent
    clock.t = ts(2026, 11, 9, 7, 30)
    assert await b.tick() is not None and len(sent) == 1 and sent[0][2] is True
    clock.t = ts(2026, 11, 9, 7, 31)
    assert await b.tick() is None and len(sent) == 1                       # once a day
    clock.t = ts(2026, 11, 10, 7, 30)
    assert await b.tick() is not None and len(sent) == 2
    assert b.public()["next_at"] is None and b.public()["last"]["day"] == "2026-11-10"


async def test_saturday_and_a_holiday_use_the_weekend_hours_monday_the_weekday_ones(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 14, 7, 30))  # a Saturday
    p.update(wake_start="07:30", weekend_start="09:30", weekend_end="23:00")
    assert b.due_at()[0] == ts(2026, 11, 14, 9, 30)
    assert await b.tick() is None
    clock.t = ts(2026, 11, 14, 9, 30)
    assert await b.tick() is not None
    clock.t = ts(2026, 11, 16, 7, 0)                                     # Monday
    assert b.due_at()[0] == ts(2026, 11, 16, 7, 30)
    clock.t = ts(2026, 12, 25, 7, 0)                                     # Christmas, a Friday: a public holiday in Korea
    assert b.due_at()[0] == ts(2026, 12, 25, 9, 30)
    clock.t = ts(2026, 9, 25, 7, 0)                                      # 추석
    assert b.due_at()[0] == ts(2026, 9, 25, 9, 30)


async def test_the_first_open_after_a_nights_rest_brings_it_early_but_never_before_the_contact_hours(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 10))
    p.update(wake_start="07:30")
    b.update(offset_min=30)                                               # due 08:00
    use(store, datetime(2026, 11, 8, tzinfo=SEOUL), "21:00", "23:00")    # last seen 23:00
    assert not b.early_open_counts(clock.t)                              # 07:10: before the contact hours (owner 2026-11-19)
    clock.t = ts(2026, 11, 9, 7, 40)
    assert b.early_open_counts(clock.t)
    assert await b.tick(clock.t, early=True) is not None and len(sent) == 1
    assert not b.early_open_counts(clock.t + 60)                          # sent today: no second one
    clock.t = ts(2026, 11, 9, 8, 0)
    assert await b.tick() is None and len(sent) == 1


async def test_a_late_night_open_never_brings_it(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 2, 0))
    p.update(wake_start="07:30")
    use(store, datetime(2026, 11, 8, tzinfo=SEOUL), "18:00", "20:00")
    assert not b.early_open_counts(clock.t)                              # 02:00 is far before 07:30
    assert await b.tick() is None
    clock.t = ts(2026, 11, 9, 6, 20)                                     # 70 minutes early: outside the hour before
    assert not b.early_open_counts(clock.t)
    clock.t = ts(2026, 11, 9, 7, 30)
    assert await b.tick() is not None and len(sent) == 1


async def test_up_all_night_gets_it_at_the_start_time(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 10))
    p.update(wake_start="07:30")
    use(store, datetime(2026, 11, 8, tzinfo=SEOUL), "20:00", "24:00")
    use(store, datetime(2026, 11, 9, tzinfo=SEOUL), "00:00", "07:00")
    assert not b.early_open_counts(clock.t, prev_use=ts(2026, 11, 9, 7, 5))   # no rest before this open
    clock.t = ts(2026, 11, 9, 7, 30)
    assert await b.tick() is not None


async def test_an_offset_after_the_start_and_early_open_can_be_turned_off(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30))
    p.update(wake_start="07:30")
    b.update(offset_min=20)
    assert b.due_at()[0] == ts(2026, 11, 9, 7, 50)
    assert await b.tick() is None
    clock.t = ts(2026, 11, 9, 7, 50)
    assert await b.tick() is not None
    b.update(early_open=False)
    clock.t = ts(2026, 11, 10, 7, 10)
    assert not b.early_open_counts(clock.t)


async def test_a_fixed_time_overrides_the_learned_time_and_the_early_open_and_is_never_before_the_contact_hours(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 6, 50))
    p.update(wake_start="07:30")
    with pytest.raises(ValueError, match="cannot be before the start of the contact hours"):
        b.update(time_mode="fixed", fixed_time="06:45")
    assert b.config()["time_mode"] == "auto" and b.public()["earliest"] == "07:30"   # nothing saved
    b.update(time_mode="fixed", fixed_time="07:45")
    use(store, datetime(2026, 11, 8, tzinfo=SEOUL), "20:00", "22:00")
    assert b.due_at() == (ts(2026, 11, 9, 7, 45), "fixed") and not b.early_open_counts(ts(2026, 11, 9, 7, 40))
    clock.t = ts(2026, 11, 9, 7, 45)
    assert await b.tick() is not None and sent[0][2] is True
    b.update(fixed_time="23:00")                                          # after the contact hours (22:00): allowed, no push
    clock.t = ts(2026, 11, 10, 23, 0)
    assert await b.tick() is not None and sent[1][2] is False


async def test_the_learned_wake_time_while_the_hours_are_untouched(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 8, 0))
    today = datetime(2026, 11, 9, tzinfo=SEOUL)
    for i in range(1, 22):
        use(store, today - timedelta(days=i), "09:00", "23:00")
    assert b.start_time() == ("09:00", "learned") and b.due_at()[0] == ts(2026, 11, 9, 9, 0)
    p.update(wake_start="07:30")                                         # set by hand: the hours decide
    assert b.start_time() == ("07:30", "hours")


async def test_a_day_the_server_missed_is_skipped_not_sent_late(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 14, 0))
    p.update(wake_start="07:30")
    assert await b.tick() is None and not sent and b.state()["last_skip"] == {"day": "2026-11-09", "reason": "missed"}


async def test_nothing_before_the_owners_first_message_and_not_while_a_reply_runs(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30))
    store._run("DELETE FROM messages")                                   # a new server: the owner has not written yet
    p.update(wake_start="07:30")
    assert await b.tick() is None
    store.add_message("user", "hi")
    b.busy = lambda: True
    assert await b.tick() is None
    b.busy = lambda: False
    assert await b.tick() is not None


# ---------------------------------------------------------------------------------------------------------- the card
async def test_a_quiet_day_is_short_or_nothing(tmp_path):
    tok = i18n.use("en")
    try:
        store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30))
        card = await b.build(clock.t, use_model=True)
        assert card["quiet"] and card["events"] == [] and card["missed"] == [] and card["line"] is None and m.calls == 0
        assert card["weather"]["headline"] == "Partly cloudy · High 11° · Low 4°" and card["summary"] == "Partly cloudy · High 11° · Low 4°"
        assert card["weather"]["attribution"] == "Weather data by Open-Meteo.com"
        b.update(quiet_days="skip")
        assert await b.build(clock.t) is None
        b.update(quiet_days="short")
        store.set_meta("weather:here", "")                               # no phone ever sent a location: no weather section
        assert await b.build(clock.t) is None                            # nothing at all to say
    finally:
        i18n.reset(tok)


async def test_a_full_card_and_its_one_line_summary(tmp_path):
    tok = i18n.use("en")
    try:
        fake = FakeWeather()
        fake.forecast = open_meteo(rain_from=15, rain_to=19)
        events = [{"id": "g1", "title": "Design review", "start": ts(2026, 11, 9, 10, 0), "where": "Gangnam station"},
                  {"id": "p1", "title": "Design review", "start": ts(2026, 11, 9, 10, 0), "where": "Gangnam station"},   # the same, from the phone
                  {"id": "g2", "title": "Dinner", "start": ts(2026, 11, 9, 19, 0), "where": ""},
                  {"id": "g3", "title": "Tomorrow's thing", "start": ts(2026, 11, 10, 9, 0), "where": ""}]
        followups = [{"id": 4, "kind": "reply", "title": "Which hotel?", "status": "open", "message_id": 77},
                     {"id": 5, "kind": "worker", "title": "Price watch", "status": "open", "worker_id": "w1", "task_id": 9},
                     {"id": 6, "kind": "login", "title": "muted", "status": "open", "suppressed": True}]
        unread = [{"uid": "11", "subject": "Contract to sign", "from": "Kim"}, {"uid": "12", "subject": "50% off", "from": "Shop"}]
        store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30), events=events, followups=followups, unread=unread,
                                                  model=Model({"line": "Rain after 3, so take an umbrella.", "important": [1]}), fake=fake)
        store.add_goal("Send the visa form by 2026-11-10")
        store._run("UPDATE goals SET status = 'tracking'")
        (b.home.root / "MEMORY.md").write_text("# Memory\n\n## Commitments\n- 2026-11-09 call the landlord back\n- 2026-12-01 later\n",
                                               encoding="utf-8")
        card = await b.build(clock.t, use_model=True)
        assert [n["text"] for n in card["weather"]["notes"]] == ["Rain from 15:00"]
        assert [e["title"] for e in card["events"]] == ["Design review", "Dinner"] and card["events"][0]["where"] == "Gangnam station"
        kinds = [x["kind"] for x in card["missed"]]
        assert kinds == ["followup", "job", "goal", "promise", "mail"]
        assert card["missed"][0]["open"] == {"kind": "followup", "id": 4, "message_id": 77}
        assert card["missed"][1]["open"] == {"kind": "job", "worker_id": "w1", "task_id": 9}
        assert card["missed"][3]["title"] == "call the landlord back" and card["missed"][3]["detail"] == "Today"
        assert card["missed"][4]["title"] == "Contract to sign" and card["missed"][4]["open"]["uid"] == "11"
        assert card["line"] == "Rain after 3, so take an umbrella."
        assert card["summary"] == "Rain this afternoon · 2 events · 2 waiting on you · 3 to check"
        text = b.plain(card)
        assert text.startswith("Morning briefing — Rain after 3") and "- 10:00 Design review (Gangnam station)" in text
        tok2 = i18n.use("ko")
        try:
            card = await b.build(clock.t)
            assert card["summary"] == "오후 비 · 일정 2개 · 답 기다리는 일 2개 · 확인할 것 2개"
            assert card["weather"]["notes"][0]["text"] == "15:00부터 비"
        finally:
            i18n.reset(tok2)
    finally:
        i18n.reset(tok)


async def test_mail_the_proactive_pass_judged_shows_without_the_model(tmp_path):
    unread = [{"uid": "11", "subject": "Contract to sign", "from": "Kim"}, {"uid": "12", "subject": "Newsletter", "from": "News"}]
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30), unread=unread, model=Model(error=QuotaExhausted("limit")))
    now = clock.t
    store._run("INSERT INTO proactive_log(key, source, title, urgency, status, decided_at, expires_at, day) VALUES(?,?,?,?,?,?,?,?)",
               ("mail:me@example.com:11", "mail", "Contract to sign", "오늘", "sent", now - 3600, now + 86400, "2026-11-09"))
    store._run("INSERT INTO proactive_log(key, source, title, urgency, status, decided_at, expires_at, day) VALUES(?,?,?,?,?,?,?,?)",
               ("mail:me@example.com:12", "mail", "Newsletter", None, "dropped", now - 3600, now + 86400, "2026-11-09"))
    card = await b.build(now, use_model=True)
    assert [x["title"] for x in card["missed"]] == ["Contract to sign"]


async def test_the_model_once_a_day_and_the_card_without_it_when_its_limit_is_used_up(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30),
                                              followups=[{"id": 1, "kind": "reply", "title": "Which hotel?", "status": "open"}],
                                              model=Model(error=QuotaExhausted("used up", provider="scripted")))
    p.update(wake_start="07:30")
    card = await b.tick()
    assert card is not None and card["line"] is None and card["missed"] and m.calls == 1 and len(sent) == 1
    m.error = None
    again, _mid = await b.run_now()                                      # the same day: no second model call
    assert again["line"] is None and m.calls == 1
    clock.t = ts(2026, 11, 10, 7, 30)
    assert (await b.tick())["line"] == "Take an umbrella for the afternoon." and m.calls == 2


async def test_no_model_set_still_sends_the_card(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30),
                                              followups=[{"id": 1, "kind": "reply", "title": "Which hotel?", "status": "open"}])
    b.model = lambda: None
    card = await b.build(clock.t, use_model=True)
    assert card["line"] is None and m.calls == 0 and card["missed"]


async def test_sections_can_be_turned_off(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30),
                                              events=[{"id": "e", "title": "Gym", "start": ts(2026, 11, 9, 18, 0), "where": ""}])
    b.update(sections={"weather": False, "calendar": True})
    card = await b.build(clock.t)
    assert card["weather"] is None and card["events"] and not fake.calls


async def test_a_pending_hours_suggestion_rides_on_the_card_until_answered(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 14, 10, 0),
                                              followups=[{"id": 1, "kind": "reply", "title": "Which hotel?", "status": "open"}])
    today = datetime(2026, 11, 14, tzinfo=SEOUL)
    for i in range(1, 29):
        d = today - timedelta(days=i)
        use(store, d, "10:00", "23:30") if d.weekday() >= 5 else use(store, d, "07:00", "22:00")
    p.check_hours(force=True)
    sug = p.suggestion("weekend")
    assert sug and sug["wake_start"] == "10:00" and p.hours_suggestion() is None
    card = await b.tick()
    assert card["hours"]["id"] == sug["id"] and card["hours"]["day_type"] == "weekend" and card["hours"]["wake_start"] == "10:00"
    rows = b.attach(store.messages())
    assert next(r for r in rows if r.get("briefing"))["briefing"]["hours"]["id"] == sug["id"]
    before = b.revision()
    assert sug["id"] in before
    p.answer_hours(sug["id"], accept=False)
    rows = b.attach(store.messages())
    assert next(r for r in rows if r.get("briefing"))["briefing"]["hours"] is None
    # what the other devices poll (GET /api/agent/status "briefing_rev"): it changed, so they ask for the card again
    assert b.revision() != before and sug["id"] not in b.revision()


# ---------------------------------------------------------------------------------------------------------- over HTTP
async def test_the_briefing_api(tmp_path):
    fake = FakeWeather()
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False,
                 home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[], weather_fetcher=fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        srv.server.config.app.state.proactive.store.set_profile(timezone="Asia/Seoul")
        got = (await c.get("/api/briefing")).json()
        assert got["enabled"] is True and got["time_mode"] == "auto" and got["offset_min"] == 0 and got["early_open"] is True
        assert got["weather"]["provider"] == "auto" and got["weather"]["here"] is None and got["weather"]["keys"] == {"kma": False, "weatherapi": False}
        assert (await c.put("/api/briefing", json={"offset_min": 90})).status_code == 422
        r = (await c.put("/api/briefing", json={"time_mode": "fixed", "fixed_time": "7:45", "sections": {"missed": False}})).json()
        assert r["fixed_time"] == "07:45" and r["sections"] == {"weather": True, "calendar": True, "missed": False}
        assert (await c.get("/api/briefing/places", params={"q": "부산"})).status_code in (404, 405)   # no place to choose any more
        assert (await c.put("/api/briefing/home", json={"name": "x", "lat": 1, "lon": 1})).status_code in (404, 405)
        assert (await c.post("/api/briefing/preview")).status_code == 405
        assert (await c.get("/api/briefing/preview")).json()["card"]["weather"] is None        # no location yet: no weather
        assert (await c.post("/api/briefing/location", json={"lat": 37.5, "lon": 127.0, "name": "Seoul"})).json() == {"ok": True}
        assert (await c.get("/api/briefing")).json()["weather"]["here"]["name"] == "Seoul"
        assert (await c.post("/api/briefing/keys/kma", json={"key": "bad-key-0000"})).status_code == 422
        assert (await c.post("/api/briefing/keys/kma", json={"key": "good-kma-key-123"})).json()["weather"]["keys"]["kma"] is True
        prev = (await c.get("/api/briefing/preview")).json()["card"]
        assert prev["weather"]["provider"] == "kma" and prev["place"] == {"name": "Seoul"}
        pro = srv.server.config.app.state.proactive
        awake = pro.awake
        pro.awake = lambda now=None: False                      # night: "Get one now" is turned down, saying when the next one comes
        night = await c.post("/api/briefing/run")
        assert night.status_code == 409 and night.json()["detail"].startswith("The briefing is not sent at night. The next one comes ")
        assert not [m for m in (await c.get("/api/messages")).json() if (m.get("content") or "").startswith("Morning briefing")]
        pro.awake = lambda now=None: True                       # inside the contact hours it goes
        out = (await c.post("/api/briefing/run")).json()
        pro.awake = awake
        msgs = (await c.get("/api/messages")).json()
        mine = next(m for m in msgs if m["id"] == out["message_id"])
        assert mine["briefing"]["day"] == out["card"]["day"] and mine["content"].startswith("Morning briefing")
        assert (await c.get("/api/agent/status")).json()["briefing_rev"] == "|"   # no hours line open: the cards stay as sent
        notes = (await c.get("/api/notifications")).json()["items"]
        assert any(n["type"] == "briefing" and n["title"] == "Morning briefing" and n["body"] == out["card"]["summary"] for n in notes)
        assert (await c.delete("/api/briefing/keys/kma")).json()["weather"]["keys"]["kma"] is False
        assert all(u.split("/")[2].endswith(("open-meteo.com", "data.go.kr")) for u in fake.calls)


# ---------------------------------------------------------------------------------------------------------- night, language, bulk mail
async def test_nothing_sends_it_at_night_a_restart_or_an_open_at_0108_included(tmp_path):
    """2026-11-10: a card went out at 01:08 KST with next_at 07:00. The automatic path never sends before its window (a restart's first
    tick, an app open at night), and "Get one now" is turned down outside the contact hours."""
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 10, 1, 8))
    p.update(wake_start="07:00")
    use(store, datetime(2026, 11, 9, tzinfo=SEOUL), "20:00", "21:00")    # away for hours: it would count as a rest
    assert await b.tick() is None and await b.tick(early=True) is None and not sent
    assert not b.early_open_counts(clock.t) and not b.app_opened(clock.t)
    assert b.night() == "The briefing is not sent at night. The next one comes at 7:00 AM."   # says no, and when the next one comes
    assert not sent and b.public()["next_at"] == ts(2026, 11, 10, 7, 0)
    clock.t = ts(2026, 11, 10, 7, 0)
    assert b.night() is None and await b.tick() is not None and len(sent) == 1


async def test_the_card_and_its_line_are_in_the_owners_language_whatever_request_started_it(tmp_path):
    """Background jobs speak the owner's language (ui_lang), not the language of the request that set them off; a model line in
    another language is asked for again once, by name, and left out if it still is not in it."""
    meeting = [{"title": "Standup", "start": ts(2026, 11, 9, 10, 0), "where": ""}]
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30), events=meeting)
    p.update(wake_start="07:30")
    i18n.set_fallback(lambda: "ko")
    b.language = lambda: "ko"
    calls: list[Any] = []

    async def model(provider: str, req: Any, **_: Any):
        calls.append(req)
        if len(calls) == 1:
            yield TextDelta(json.dumps({"line": "Today's clear weather with a low of 0°C.", "important": []}))
        else:
            yield TextDelta("오늘은 맑고 최저 0°C예요.")
    b.stream_fn = model
    token = i18n.use("en")   # as if an English app's request had started it
    try:
        card = await b.tick()
    finally:
        i18n.reset(token)
        i18n.set_fallback(None)
    assert card is not None and card["line"] == "오늘은 맑고 최저 0°C예요." and len(calls) == 2
    assert "Korean" in calls[1].messages[0]["content"]
    assert sent[0][1].startswith("아침 브리핑") and "최고" in card["weather"]["headline"]   # the card's own words in Korean too

    store2, clock2, p2, b2, sent2, m2, fake2 = setup(tmp_path / "b", ts(2026, 11, 9, 7, 30), events=meeting)
    p2.update(wake_start="07:30")
    b2.language = lambda: "ko"

    async def stubborn(provider: str, req: Any, **_: Any):
        yield TextDelta(json.dumps({"line": "Clear skies today, enjoy it.", "important": []}) if "JSON" in req.messages[0]["content"]
                        else "Still English here.")
    b2.stream_fn = stubborn
    assert (await b2.tick())["line"] is None                            # still not Korean: the card goes without the line


async def test_bulk_and_marketing_mail_never_counts_as_missed(tmp_path):
    """Promotions are left out of "may have missed" for every mailbox: the rows a mailbox marks as bulk (List-Unsubscribe, List-Id,
    Precedence, Auto-Submitted, the provider's own promo sorting) never reach the model or the card."""
    unread = [{"uid": "1", "from": "Ralph Lauren <news@e.ralphlauren.com>", "subject": "Ending Soon: The Fall Wardrobe Event", "bulk": True},
              {"uid": "2", "from": "민지 <minji@example.org>", "subject": "내일 회의 시간 바꿀 수 있을까요?"}]
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 30), unread=unread,
                                              model=Model({"line": "A question from 민지 is waiting.", "important": [1]}))
    p.update(wake_start="07:30")
    seen: list[str] = []
    inner = b.stream_fn

    async def watch(provider: str, req: Any, **kw: Any):
        seen.append(req.messages[-1]["content"])
        async for ev in inner(provider, req, **kw):
            yield ev
    b.stream_fn = watch
    card = await b.tick()
    assert [x["title"] for x in card["missed"] if x["kind"] == "mail"] == ["내일 회의 시간 바꿀 수 있을까요?"]
    assert "Wardrobe" not in seen[0]


async def test_until_a_language_is_picked_the_owners_language_follows_the_signed_in_apps(tmp_path):
    """The phone's language changed: the app's next request (a signed-in device) moves ui_lang with it; a pick in the app holds."""
    from test_push import pair
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False,
                 home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[])) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        store = srv.server.config.app.state.proactive.store
        _, phone = await pair(c)
        await c.get("/api/briefing", headers={**phone, "Accept-Language": "ko-KR,ko;q=0.9"})
        assert store.meta("ui_lang") == "ko"
        await c.get("/api/briefing", headers={**phone, "Accept-Language": "ja-JP,ja;q=0.9"})
        assert store.meta("ui_lang") == "ja" and store.meta("ui_lang_from") == "app"   # the phone now speaks Japanese
        await c.get("/api/briefing", headers={**AUTH, "Accept-Language": "en-US"})
        assert store.meta("ui_lang") == "ja"                                           # a script with the master token does not move it
        await c.put("/api/language", json={"language": "ko"}, headers=phone)
        await c.get("/api/briefing", headers={**phone, "Accept-Language": "ja-JP"})
        assert store.meta("ui_lang") == "ko"                                           # a pick in the app holds


async def test_the_owners_language_is_the_one_chosen_in_the_app_and_one_request_can_not_flip_it(tmp_path):
    """ui_lang (what background jobs speak) follows the owner's pick in the app (PUT /api/language); a request's Accept-Language only
    fills it while nothing is set, so a request from another device or app in English never turns the briefing English."""
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False,
                 home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[])) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        store = srv.server.config.app.state.proactive.store
        await c.get("/api/briefing", headers={"Accept-Language": "ko-KR,ko;q=0.9"})
        assert store.meta("ui_lang") == "ko"                               # the first app request fills it
        await c.get("/api/briefing", headers={"Accept-Language": "en-US,en;q=0.9"})
        assert store.meta("ui_lang") == "ko"                               # one request in English does not flip it
        got = (await c.get("/api/language")).json()
        assert got["language"] == "ko" and got["source"] == "app" and "ko" in got["available"]
        r = await c.put("/api/language", json={"language": "en-US"}, headers={"Accept-Language": "ko"})
        assert r.json()["language"] == "en" and r.json()["source"] == "picked" and store.meta("ui_lang") == "en"
        await c.get("/api/briefing", headers={"Accept-Language": "ko"})
        assert store.meta("ui_lang") == "en"                               # the pick holds
        assert (await c.put("/api/language", json={"language": "xx-YY"})).status_code == 422
        assert i18n.owner() == "en"


# ---------------------------------------------------------------------------------------------------------- the smarter briefing
def day_with_everything(tmp_path, model: Model, *, lang: str = "en", t: float | None = None):
    """A busy day: rain in the afternoon, two events back to back, a follow-up and two unread mails; the model scripted."""
    fake = FakeWeather()
    fake.forecast = open_meteo(rain_from=15, rain_to=19)
    events = [{"id": "g1", "title": "Design review", "start": ts(2026, 11, 9, 10, 0), "end": ts(2026, 11, 9, 11, 0), "where": "Gangnam station"},
              {"id": "g2", "title": "Dinner", "start": ts(2026, 11, 9, 18, 0), "where": "Hongdae"}]
    followups = [{"id": 4, "kind": "reply", "title": "Which hotel?", "status": "open", "message_id": 77}]
    unread = [{"uid": "11", "subject": "Contract to sign", "from": "Kim"}, {"uid": "12", "subject": "50% off", "from": "Shop"}]
    out = setup(tmp_path, t or ts(2026, 11, 9, 7, 30), events=events, followups=followups, unread=unread, model=model, fake=fake)
    out[3].language = lambda: lang
    return out


GOOD = {"headline": "Rain from 3 pm and a full evening: take an umbrella.",
        "connections": [{"text": "Dinner in Hongdae starts while the rain is on.", "refs": ["e2", "w"]},
                        {"text": "The contract mail may be about the design review.", "refs": ["u1", "e1"]}],
        "suggestions": [{"text": "Shall I draft a reply to Kim?", "refs": ["u1"]}, {"text": "Shall I remind you to take an umbrella?", "refs": []}],
        "important": [1]}


async def test_the_model_gets_a_digest_and_its_json_becomes_headline_notes_and_suggestions(tmp_path):
    seen: list[Any] = []

    class Spy(Model):
        async def __call__(self, provider, req, **kw):
            seen.append(req)
            async for x in super().__call__(provider, req, **kw):
                yield x
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Spy(GOOD))
    card = await b.build(clock.t, use_model=True)
    assert m.calls == 1 and len(seen) == 1
    digest = seen[0].messages[1]["content"]
    assert "[e1] 10:00–11:00 Design review at Gangnam station" in digest and "[e2] 18:00 Dinner at Hongdae" in digest
    assert "[w] Weather:" in digest and "by time of day:" in digest and "afternoon" in digest
    assert "The owner's phone is at: 서울특별시" in digest and "[m1] followup: Which hotel?" in digest
    assert "[u1] Contract to sign — from Kim" in digest and "[u2] 50% off — from Shop" in digest
    assert "Language: en" in digest and "JSON" in seen[0].messages[0]["content"]
    assert card["headline"] == card["line"] == GOOD["headline"]
    assert [x["id"] for x in card["events"]] == ["e1", "e2"] and card["weather"]["id"] == "w"
    mail = next(x for x in card["missed"] if x["kind"] == "mail")
    assert mail["id"] == "m2" and mail["title"] == "Contract to sign"                # picked mail got its id; u1 maps to it
    assert card["connections"] == [{"text": GOOD["connections"][0]["text"], "refs": ["e2", "w"]},
                                   {"text": GOOD["connections"][1]["text"], "refs": ["m2", "e1"]}]
    assert [s["text"] for s in card["suggestions"]] == ["Shall I draft a reply to Kim?", "Shall I remind you to take an umbrella?"]
    assert card["suggestions"][0]["refs"] == ["m2"] and card["suggestions"][1]["refs"] == []
    assert "· Dinner in Hongdae starts while the rain is on." in b.plain(card)
    assert json.loads(json.dumps(card)) == card                                       # plain JSON all the way (stored with the message)


async def test_nothing_of_the_model_reaches_the_log(tmp_path, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Model(GOOD))
    await b.build(clock.t, use_model=True)
    store2, clock2, p2, b2, sent2, m2, fake2 = day_with_everything(tmp_path / "x", Model(error=RuntimeError("Contract to sign Kim Hongdae")))
    await b2.build(clock2.t, use_model=True)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for word in ("Contract to sign", "Hongdae", "umbrella", "Design review", "Kim"):
        assert word not in logged


async def test_invalid_json_or_a_failure_sends_the_plain_card_and_uses_the_days_call(tmp_path):
    class Garbage(Model):
        async def __call__(self, provider, req, **kw):
            self.calls += 1
            yield TextDelta("Sorry, I cannot do that { not json")
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Garbage())
    card = await b.build(clock.t, use_model=True)
    assert m.calls == 1 and card["headline"] is None and card["line"] is None and card["connections"] == [] and card["suggestions"] == []
    assert [x["kind"] for x in card["missed"]] == ["followup"] and card["events"]            # the script parts are all there
    assert (await b.build(clock.t, use_model=True))["headline"] is None and m.calls == 1       # the day's call is spent
    # JSON of the wrong shape
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path / "w", Model({"headline": 5, "connections": "x", "suggestions": [1, None, {"text": 3}],
                                                                                   "important": "all"}))
    card = await b.build(clock.t, use_model=True)
    assert card["headline"] is None and card["connections"] == [] and card["suggestions"] == [{"text": "1", "refs": []}] or card["suggestions"] == []


async def test_a_reply_in_the_wrong_language_is_rewritten_once_or_left_out(tmp_path):
    calls: list[Any] = []

    async def model(provider: str, req: Any, **_: Any):
        calls.append(req)
        if len(calls) == 1:
            yield TextDelta(json.dumps(GOOD))
        else:
            assert "Korean" in req.messages[0]["content"]
            lines = [ln.split(". ", 1)[1] for ln in req.messages[1]["content"].splitlines()]
            yield TextDelta("\n".join(f"{i}. 한국어로 다시 쓴 문장 {i}입니다" for i, _ in enumerate(lines, 1)))
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Model(), lang="ko")
    b.stream_fn = model
    card = await b.build(clock.t, use_model=True)
    assert len(calls) == 2                                                   # one call and one rewrite, no more
    assert card["headline"] == "한국어로 다시 쓴 문장 1입니다"
    assert [c["text"] for c in card["connections"]] == ["한국어로 다시 쓴 문장 2입니다", "한국어로 다시 쓴 문장 3입니다"]
    assert [s["text"] for s in card["suggestions"]] == ["한국어로 다시 쓴 문장 4입니다", "한국어로 다시 쓴 문장 5입니다"]
    assert card["connections"][0]["refs"] == ["e2", "w"]                      # the ids stay with their notes

    async def stubborn(provider: str, req: Any, **_: Any):
        yield TextDelta(json.dumps(GOOD) if "JSON" in req.messages[0]["content"] else "Still English.\nStill English.")
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path / "s", Model(), lang="ko")
    b.stream_fn = stubborn
    card = await b.build(clock.t, use_model=True)
    assert card["headline"] is None and card["connections"] == [] and card["suggestions"] == []
    assert any(x["kind"] == "mail" for x in card["missed"])                   # the mail picks do not depend on the language


async def test_a_huge_reply_is_cut_and_capped(tmp_path):
    big = {"headline": "H" * 3000, "connections": [{"text": f"note {i} " + "n" * 600, "refs": ["e1"]} for i in range(10)],
           "suggestions": [{"text": f"step {i}? " + "s" * 900, "refs": []} for i in range(10)], "important": list(range(1, 40))}
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Model(big))
    card = await b.build(clock.t, use_model=True)
    from assistant import briefing as bm
    assert len(card["headline"]) <= bm.HEAD_CHARS
    assert len(card["connections"]) == bm.MAX_NOTES and all(len(c["text"]) <= bm.NOTE_CHARS for c in card["connections"])
    assert len(card["suggestions"]) == bm.MAX_STEPS and all(len(s["text"]) <= bm.STEP_CHARS for s in card["suggestions"])
    assert len([x for x in card["missed"] if x["kind"] == "mail"]) <= 2           # only mail that exists

    async def endless(provider: str, req: Any, **_: Any):
        for _i in range(100000):                                              # never stops: read only up to the cap
            yield TextDelta("x" * 1000)
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path / "e", Model())
    b.stream_fn = endless
    card = await asyncio.wait_for(b.build(clock.t, use_model=True), 20)
    assert card["headline"] is None and card["events"]


async def test_a_slow_model_does_not_hold_the_card_back(tmp_path, monkeypatch):
    from assistant import briefing as bm
    monkeypatch.setattr(bm, "MODEL_TIMEOUT", 0.2)

    async def slow(provider: str, req: Any, **_: Any):
        await asyncio.sleep(30)
        yield TextDelta(json.dumps(GOOD))
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Model())
    b.stream_fn = slow
    started = time.monotonic()
    card = await b.build(clock.t, use_model=True)
    assert time.monotonic() - started < 5 and card["headline"] is None and card["events"] and card["missed"]
    assert b.state().get("ai_day") == "2026-11-09"                             # a timeout uses the day's call like a failure


async def test_no_consent_no_model_call_and_the_call_stays_for_later(tmp_path):
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Model(GOOD))
    b.consent = lambda: False
    card = await b.build(clock.t, use_model=True)
    assert m.calls == 0 and card["headline"] is None and card["connections"] == [] and card["events"]
    assert b.state().get("ai_day") is None
    b.consent = lambda: True                                                   # agreed later the same day: the call still happens
    assert (await b.build(clock.t, use_model=True))["headline"] == GOOD["headline"] and m.calls == 1

    def broken() -> bool:
        raise RuntimeError("store gone")
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path / "b", Model(GOOD))
    b.consent = broken
    assert (await b.build(clock.t, use_model=True))["headline"] is None and m.calls == 0


async def test_no_model_or_used_up_usage_gives_the_plain_card(tmp_path):
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Model(GOOD))
    b.model = lambda: None
    card = await b.build(clock.t, use_model=True)
    assert m.calls == 0 and card["headline"] is None and card["suggestions"] == [] and card["weather"]["id"] == "w"
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path / "q", Model(error=QuotaExhausted("used up", provider="scripted")))
    card = await b.build(clock.t, use_model=True)
    assert m.calls == 1 and card["headline"] is None and card["connections"] == [] and card["events"]


async def test_ids_that_are_not_in_the_digest_are_dropped(tmp_path):
    odd = {"headline": "A day.", "connections": [{"text": "Made-up link.", "refs": ["e9", "zz", "u7"]},
                                                 {"text": "Real and made-up.", "refs": ["e1", "e9", 4]}],
           "suggestions": [{"text": "Shall I look into it?", "refs": ["x1", "m1", "m1"]}], "important": [1, 7, "a"]}
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Model(odd))
    card = await b.build(clock.t, use_model=True)
    assert card["connections"] == [{"text": "Real and made-up.", "refs": ["e1"]}]       # a note with no real item behind it is gone
    assert card["suggestions"] == [{"text": "Shall I look into it?", "refs": ["m1"]}]
    assert len([x for x in card["missed"] if x["kind"] == "mail"]) == 1             # 7 and "a" are no mail

    # a note about an unread mail the model did not pick has no item on the card: dropped
    pick_none = {"headline": "A day.", "connections": [{"text": "About the offer.", "refs": ["u2"]}], "important": [1]}
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path / "n", Model(pick_none))
    assert (await b.build(clock.t, use_model=True))["connections"] == []


def test_parse_result_validates_shape_and_lengths():
    from assistant.briefing import parse_result
    got = parse_result({"headline": "  **Hello**\n there ", "connections": [{"text": "a", "refs": ["e1", "e1", "q"]}, "b", {"text": ""}, 5],
                        "suggestions": [{"text": "c?"}], "important": [2, "1", 0, 9, None]}, {"e1"}, 3)
    assert got == {"headline": "Hello there", "connections": [{"text": "a", "refs": ["e1"]}, {"text": "b", "refs": []}],
                   "suggestions": [{"text": "c?", "refs": []}], "important": [0, 1]}
    assert parse_result({"line": "Old style."}, set(), 0)["headline"] == "Old style."
    assert parse_result({}, set(), 0) == {"headline": None, "connections": [], "suggestions": [], "important": []}


async def test_the_preview_and_a_quiet_day_never_call_the_model(tmp_path):
    store, clock, p, b, sent, m, fake = day_with_everything(tmp_path, Model(GOOD))
    card = await b.build(clock.t, use_model=False, force=True)
    assert m.calls == 0 and card["headline"] is None and card["connections"] == [] and card["suggestions"] == []
    store, clock, p, b, sent, m, fake = setup(tmp_path / "q", ts(2026, 11, 9, 7, 30), model=Model(GOOD))
    card = await b.build(clock.t, use_model=True)                                  # nothing to tell: no call for a headline
    assert m.calls == 0


async def test_the_server_asks_the_ai_consent_before_the_briefing_calls_the_model(tmp_path):
    calls: list[Any] = []

    async def fn(provider, request, **kw):
        calls.append(request)
        yield TextDelta(json.dumps({"headline": "Quiet morning.", "important": []}))
    s = Settings(token=TOKEN, provider="anthropic", model="m", cdp_host=None, db_path=str(tmp_path / "c.db"), scheduler=False,
                 home_dir=str(tmp_path / "home"))
    async with Running(create_app(s, tools=[], weather_fetcher=FakeWeather(), stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        app = srv.server.config.app
        app.state.proactive.store.set_profile(timezone="Asia/Seoul")
        app.state.proactive.awake = lambda now=None: True
        assert (await c.post("/api/briefing/location", json={"lat": 37.5, "lon": 127.0, "name": "Seoul"})).status_code == 200
        before = len(calls)
        first = (await c.post("/api/briefing/run")).json()["card"]                     # nothing agreed to yet: nothing is sent
        assert first["headline"] is None and len(calls) == before
        assert (await c.put("/api/ai-consent", json={"accepted": True})).json()["accepted"] is True
        second = (await c.get("/api/briefing/preview")).json()["card"]               # the preview never asks a model
        assert second["headline"] is None and len(calls) == before


# ---- the weather must not drop out of the morning card for one slow answer (owner 2026-11-17: "오늘 아침 브리핑엔 왜 날씨얘기가없지";
# the forecast request timed out once and the card went out without weather)

def _weather_down_then(fake: FakeWeather, up_after_sleeps: int | None):
    """Open-Meteo and MET Norway (the last fallback) both down; Open-Meteo comes back after `up_after_sleeps` waits."""
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)
        if up_after_sleeps is not None and len(waits) >= up_after_sleeps:
            fake.down.discard("api.open-meteo.com")
    fake.down |= {"api.open-meteo.com", "api.met.no"}
    return sleep, waits


async def test_open_meteo_down_alone_the_card_gets_its_weather_from_met_norway_without_waiting(tmp_path):
    fake = FakeWeather()
    store, clock, p, b, sent, m, _ = setup(tmp_path, ts(2026, 11, 17, 8, 0), fake=fake)
    fake.down.add("api.open-meteo.com")
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)
    b._sleep = sleep
    card = await b.build(clock.t)
    assert card is not None and card["weather"] is not None and card["weather"]["provider"] == "met_no"
    assert waits == []


async def test_a_forecast_that_fails_once_is_tried_again_and_the_card_has_the_weather(tmp_path):
    fake = FakeWeather()
    store, clock, p, b, sent, m, _ = setup(tmp_path, ts(2026, 11, 17, 8, 0), fake=fake)
    b._sleep, waits = _weather_down_then(fake, up_after_sleeps=1)
    card = await b.build(clock.t)
    assert card is not None and card["weather"] is not None and card["place"] is not None
    assert waits == [5]


async def test_a_service_still_down_after_the_tries_uses_this_mornings_earlier_forecast(tmp_path):
    fake = FakeWeather()
    store, clock, p, b, sent, m, _ = setup(tmp_path, ts(2026, 11, 17, 5, 0), fake=fake)
    await b.weather.forecast(b.weather.current_place(), 2)        # fetched at 05:00 (the agent asked, or an earlier card)
    clock.t = ts(2026, 11, 17, 8, 0)                             # 3 h later: past the 20-minute cache, within the 12-hour fallback
    b._sleep, waits = _weather_down_then(fake, up_after_sleeps=None)
    card = await b.build(clock.t)
    assert card is not None and card["weather"] is not None
    assert waits == [5, 20]


async def test_with_no_forecast_at_all_the_card_still_goes_out_after_three_tries(tmp_path):
    fake = FakeWeather()
    store, clock, p, b, sent, m, _ = setup(tmp_path, ts(2026, 11, 17, 8, 0), fake=fake)
    b._sleep, waits = _weather_down_then(fake, up_after_sleeps=None)
    forecasts_before = sum("/v1/forecast" in u for u in fake.calls)
    card = await b.build(clock.t)   # a quiet day without weather may send nothing; it must not wait forever or stop early
    assert card is None or card["weather"] is None
    # three tries, and each try asks Open-Meteo's normal route and then its other server (both down here)
    assert waits == [5, 20] and sum("/v1/forecast" in u for u in fake.calls) - forecasts_before == 6


class FakeCheck:
    def __init__(self, found=()):
        self.found, self.calls, self.recorded = list(found), 0, []

    async def check(self, now):
        self.calls += 1
        return list(self.found)

    def record(self, key, outcome, title=""):
        self.recorded.append((key, outcome))


class FakeCorrection:
    message_id, key, url = 7, "correction:7", "https://www.example.gov/n"

    def line(self):
        return "Now allowed with a prescription (The authority)"


async def test_the_morning_card_carries_corrections_and_records_them_told(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 0))
    b.corrections = FakeCheck([FakeCorrection()])
    card = await b.tick()
    assert card is not None and card["corrections"][0]["key"] == "correction:7"
    assert {"text": "Now allowed with a prescription (The authority)", "refs": []} in card["connections"]
    assert "Now allowed with a prescription" in sent[0][1] and b.corrections.recorded == [("correction:7", "told")]


async def test_get_one_now_and_the_preview_never_run_the_check(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 9, 0))
    b.corrections = FakeCheck([FakeCorrection()])
    await b.run_now()
    await b.build(clock.t)
    assert b.corrections.calls == 0


async def test_no_consent_no_check_and_a_failing_check_leaves_the_card(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 0))
    b.corrections, b.consent = FakeCheck([FakeCorrection()]), (lambda: False)
    card = await b.tick()
    assert card is not None and card["corrections"] == [] and b.corrections.calls == 0

    class Broken(FakeCheck):
        async def check(self, now):
            raise TimeoutError()
    store, clock, p, b, sent, m, fake = setup(tmp_path / "2", ts(2026, 11, 9, 7, 0))
    b.corrections = Broken()
    card = await b.tick()
    assert card is not None and card["corrections"] == [] and len(sent) == 1


async def test_the_briefing_waits_for_the_contact_hours_whatever_moved(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 6, 0))
    p.update(wake_start="07:30")
    b.update(time_mode="fixed", fixed_time="08:00")
    p.update(wake_start="09:00")                                          # the hours moved later after the time was set
    assert b.due_at()[0] == ts(2026, 11, 9, 9, 0) and b.public()["earliest"] == "09:00"
    p.update(weekend_start="06:00", weekend_end="23:00")
    assert b.public()["earliest"] == "06:00"                              # the earlier of the two windows
    b.update(fixed_time="06:30")                                          # fine on weekends; on weekdays it waits for 09:00
    assert b.due_at(ts(2026, 11, 14, 5, 0))[0] == ts(2026, 11, 14, 6, 30) and b.due_at()[0] == ts(2026, 11, 9, 9, 0)


async def test_a_learned_start_before_the_contact_hours_waits_for_them(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 5, 0))   # the hours are the untouched default, 07:00
    today = datetime(2026, 11, 9, tzinfo=SEOUL)
    for i in range(1, 22):
        use(store, today - timedelta(days=i), "06:00", "23:00")
    start, src = b.start_time()
    assert src == "learned" and start < "07:00"
    assert b.due_at()[0] == ts(2026, 11, 9, 7, 0)


def test_due_on_is_the_days_briefing_time_and_none_while_off(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 0))
    day = date(2026, 11, 10)
    assert b.due_on(day) == b.due_at(ts(2026, 11, 10, 12, 0))[0]
    b.update(time_mode="fixed", fixed_time="12:30")
    assert b.due_on(day) == ts(2026, 11, 10, 12, 30)
    b.update(enabled=False)
    assert b.due_on(day) is None


def test_covers_are_what_the_briefing_reads_for_the_looked_at_page(tmp_path):
    store, clock, p, b, sent, m, fake = setup(tmp_path, ts(2026, 11, 9, 7, 0))
    assert b.covers() == ["weather", "calendar", "missed"]                 # no correction check wired
    b.corrections = FakeCheck([])
    assert b.covers() == ["weather", "calendar", "missed", "corrections"]
    b.consent = lambda: False
    assert b.covers() == ["weather", "calendar", "missed"]                 # no AI consent yet: the check cannot run
    b.consent = lambda: True
    b.update(sections={"weather": False})
    assert b.covers() == ["calendar", "missed", "corrections"]
    b.update(enabled=False)
    assert b.covers() == []
