"""The phone's own calendar: one sealed snapshot per phone, replaced on upload, wiped when turned off or the phone is removed, and read
by the agent as one list that says how fresh each phone's copy is."""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from assistant.agent import Ours
from assistant.agent.fence import marked, plain, split
from assistant.phone_calendar import PhoneCalendars, phone_calendar_tools
from assistant.server import Settings, create_app
from assistant.store import Store

from test_devices import add_phone, bearer
from test_server import AUTH, TOKEN, Running, scripted

SEOUL = ZoneInfo("Asia/Seoul")


def ts(text):
    return datetime.fromisoformat(text).replace(tzinfo=SEOUL).timestamp()


def snapshot(*events, name="Pixel 8 · Kelo"):
    return {"device_name": name, "timezone": "Asia/Seoul", "window_start": ts("2026-11-01T00:00"), "window_end": ts("2027-01-07T00:00"),
            "events": list(events)}


DENTIST = {"title": "치과 예약", "start": ts("2026-11-09T15:00"), "end": ts("2026-11-09T16:00"), "location": "강남역", "calendar": "개인",
           "attendees": 0}
TRIP = {"title": "제주 여행", "start": ts("2026-11-20T00:00"), "end": ts("2026-11-23T00:00"), "all_day": True, "day": "2026-11-20",
        "last_day": "2026-11-22", "calendar": "가족", "attendees": 3}


async def read(tool, args):
    """The tool's text without the fence's marks (the loop fences the marked parts; these tests check the words)."""
    return plain(await tool.handler(args))


def data_parts(text):
    return [p for is_data, p in split(text) if is_data]


def app_for(tmp_path):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")), tools=None,
                      stream_fn=scripted())


async def test_each_phone_keeps_one_sealed_snapshot_that_is_replaced_and_wiped(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.put("/api/phone-calendar", json=snapshot())).status_code == 401
        _, r = await add_phone(c, "Pixel")
        phone = bearer(r.json()["token"])
        assert (await c.get("/api/phone-calendar", headers=phone)).json()["shared"] is False
        put = (await c.put("/api/phone-calendar", json=snapshot(DENTIST, TRIP, {"title": "no start"}), headers=phone)).json()
        assert put["ok"] and put["count"] == 2
        assert (await c.put("/api/phone-calendar", json=snapshot(DENTIST), headers=AUTH)).json()["count"] == 1   # the access key is its own "phone"
        st = (await c.get("/api/phone-calendar", headers=phone)).json()
        assert st["shared"] and st["count"] == 2 and len(st["devices"]) == 2 and sum(d["this"] for d in st["devices"]) == 1
        assert (await c.put("/api/phone-calendar", json={"events": "nope"}, headers=phone)).status_code == 422
        raw = (tmp_path / "s.db").read_bytes()
        assert "치과".encode() not in raw and "강남역".encode() not in raw                # sealed at rest
        await c.put("/api/phone-calendar", json=snapshot(TRIP), headers=phone)              # a new upload replaces the old one
        assert (await c.get("/api/phone-calendar", headers=phone)).json()["count"] == 1
        assert (await c.delete("/api/phone-calendar", headers=phone)).json() == {"ok": True}
        assert (await c.get("/api/phone-calendar", headers=phone)).json()["shared"] is False
        await c.put("/api/phone-calendar", json=snapshot(TRIP), headers=phone)
        dev = next(d for d in (await c.get("/api/devices", headers=AUTH)).json()["devices"] if d["name"] == "Pixel")
        await c.delete(f"/api/devices/{dev['id']}", headers=AUTH)                           # a removed phone's calendar goes with it
        assert len((await c.get("/api/phone-calendar", headers=AUTH)).json()["devices"]) == 1


async def test_the_agent_reads_every_phone_together_and_hears_how_fresh_it_is(tmp_path):
    now = [ts("2026-11-08T09:00")]
    st = Store(tmp_path / "s.db")
    cals = PhoneCalendars(st, clock=lambda: now[0])
    tool = phone_calendar_tools(cals, lambda: "Asia/Seoul")[0]
    assert "not shared" in await read(tool, {})
    cals.put("1", snapshot(DENTIST, TRIP))
    now[0] += 1800
    cals.put("2", snapshot(DENTIST, {**DENTIST, "title": "Standup", "start": ts("2026-11-10T09:00"), "end": ts("2026-11-10T09:15")},
                           name="Galaxy Tab"))
    now[0] += 600
    out = await read(tool, {"start": "2026-11-09", "end": "2026-11-30"})
    assert "Snapshot from Pixel 8 · Kelo, updated 40 minutes ago, covers 2026-11-01 to 2027-01-07." in out
    assert "Snapshot from Galaxy Tab, updated 10 minutes ago" in out
    assert out.count("치과 예약") == 1                                                 # the same event on two phones is listed once
    assert "- 2026-11-09 15:00–16:00 · 치과 예약 · at 강남역 · 개인" in out
    assert "- 2026-11-20 – 2026-11-22, all day · 제주 여행 · 3 people · 가족" in out
    assert "Standup · at 강남역 · 개인 · on Galaxy Tab" in out
    assert "제주" in await read(tool, {"start": "2026-11-01", "end": "2026-12-31", "query": "제주"})
    assert "치과" not in await read(tool, {"start": "2026-11-01", "end": "2026-12-31", "query": "제주"})
    assert "outside it" in await read(tool, {"start": "2027-02-01", "end": "2027-02-02"})
    assert [e["title"] for e in cals.upcoming(24 * 30)] == ["치과 예약", "Standup"]       # timed events still ahead, for the heads-up pass
    now[0] += 3 * 86400
    assert "old, the phone has not sent an update" in await read(tool, {"start": "2026-11-09", "end": "2026-11-10"})


async def test_a_search_with_several_words_finds_any_of_them_and_an_empty_search_says_it_was_filtered(tmp_path):
    now = [ts("2026-11-08T09:00")]
    cals = PhoneCalendars(Store(tmp_path / "s.db"), clock=lambda: now[0])
    tool = phone_calendar_tools(cals, lambda: "Asia/Seoul")[0]
    flight = {"title": "Flight to Testville (XX 123)", "start": ts("2026-11-12T10:00"), "end": ts("2026-11-12T12:00"), "location": "",
              "calendar": "", "attendees": 0, "notes": "booking ref QQ9"}
    cals.put("1", snapshot(flight, DENTIST, TRIP))
    window = {"start": "2026-11-09", "end": "2026-11-30"}
    assert "Flight to Testville" in await read(tool, {**window, "query": ["nowhere", "testville"]})
    assert "Flight to Testville" in await read(tool, {**window, "query": '["nowhere","TESTVILLE"]'})
    assert "Flight to Testville" in await read(tool, {**window, "query": "nowhere, xx"})
    empty = await read(tool, {**window, "query": "nowhere zilch"})
    assert 'No events matching "nowhere" or "zilch"' in empty
    assert "holds 3 events" in empty and "without query" in empty
    assert "Flight to Testville" in await read(tool, {**window, "query": ["qq9"]})   # found in the notes
    cals.put("2", snapshot({**DENTIST, "title": "Dentist again", "start": ts("2026-11-14T15:00"), "end": ts("2026-11-14T16:00"),
                            "account": "me@example.test", "account_type": "com.google"}, name="Galaxy"))
    out = await read(tool, {**window, "query": ["dentist"]})
    assert "account me@example.test (Google)" in out


def test_clean_event_keeps_the_new_fields_clipped_and_old_snapshots_still_read():
    from assistant.phone_calendar import clean_event
    e = clean_event({"title": "t", "start": ts("2026-11-09T15:00"), "end": ts("2026-11-09T16:00"), "notes": " n " * 400,
                     "account": "a" * 200, "account_type": "com.google"})
    assert len(e["notes"]) == 500 and len(e["account"]) == 120 and e["account_type"] == "com.google"
    old = clean_event({"title": "t", "start": ts("2026-11-09T15:00")})
    assert not {"notes", "account", "account_type"} & set(old)


async def test_an_event_the_app_does_not_mark_as_the_owners_own_is_others_text(tmp_path):
    """Only `own: true` makes an event the owner's own: the title, place and calendar of any other event are data (fenced by the loop);
    the owner's own event is the app's words (a result of Ours when nothing else is marked)."""
    now = [ts("2026-11-08T09:00")]
    cals = PhoneCalendars(Store(tmp_path / "s.db"), clock=lambda: now[0])
    tool = phone_calendar_tools(cals, lambda: "Asia/Seoul")[0]
    cals.put("1", snapshot({**DENTIST, "own": True}))
    own = await tool.handler({"start": "2026-11-09", "end": "2026-11-09"})
    assert isinstance(own, Ours) and not marked(own) and "치과 예약 · at 강남역 · 개인" in own
    cals.put("1", snapshot({**DENTIST, "own": True}, {**TRIP, "own": "yes"}))
    mixed = await tool.handler({"start": "2026-11-09", "end": "2026-11-22"})
    assert marked(mixed) and "치과 예약" not in data_parts(mixed) and "제주 여행" in data_parts(mixed)
    assert "강남역" not in data_parts(mixed) and "가족" in data_parts(mixed)


def test_clean_event_keeps_own_only_when_the_app_says_true():
    from assistant.phone_calendar import clean_event
    assert clean_event({"title": "t", "start": ts("2026-11-09T15:00"), "own": True})["own"] is True
    assert clean_event({"title": "t", "start": ts("2026-11-09T15:00"), "own": "yes"})["own"] is False
    assert clean_event({"title": "t", "start": ts("2026-11-09T15:00")})["own"] is False
