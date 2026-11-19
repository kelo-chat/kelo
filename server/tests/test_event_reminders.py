"""Reminders before calendar events for a checklist item (owner decision 2026-11-19, card p-checklist-event-reminders): one reminder
per event at its start minus the lead, made by code from the calendars Kelo reads (no model call per event, no polling job), in step
with new, moved and cancelled events, and only while a checklist item links the job. Fake calendars, fake clock, fake model."""

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant import checklist as checklist_mod
from assistant.activity import rule_kind, schedule_rule_ok
from assistant.checklist import CHECKLIST_MARK, JOB_KINDS
from assistant.event_reminders import KIND, EventReminders, ReminderError, lead_words
from assistant.store import Store

from test_checklist_own_pace import app_for, setup_model, wait_for
from test_server import AUTH, Running

ROOT = Path(__file__).resolve().parents[2]
SEOUL = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 11, 20, 9, 0, tzinfo=SEOUL).timestamp()   # 09:00


def ev(id_, minutes, title="Team meeting", where="", source="phone", all_day=False):
    """An event `minutes` after T0."""
    return {"id": id_, "title": title, "start": T0 + minutes * 60, "end": T0 + minutes * 60 + 3600, "where": where,
            "all_day": all_day, "source_id": source}


class Cal:
    """Every calendar Kelo reads: events by id; `failed` = source ids that could not be read this time; `reads` counts the reads."""

    def __init__(self, *events):
        self.events = {e["id"]: e for e in events}
        self.failed = []
        self.reads = 0

    async def __call__(self, start, end):
        self.reads += 1
        got = [dict(e) for e in self.events.values()                                  # copies: a later change is not seen until read
               if start.timestamp() <= e["start"] < end.timestamp() and e["source_id"] not in self.failed]
        return sorted(got, key=lambda e: e["start"]), list(self.failed)


class Rig:
    def __init__(self, *events, awake=True):
        self.now = T0
        self.cal = Cal(*events)
        self.sent = []
        self.linked = set()
        self.is_awake = awake
        self.r = EventReminders(Store(), self.cal, notify=lambda kind, title, body: self.sent.append((kind, title, body)),
                                linked=lambda: set(self.linked), awake=lambda ts: self.is_awake, tz=lambda: SEOUL,
                                clock=lambda: self.now)

    def job(self, item="Before each event", lead_minutes=(30,), **kw):
        j = self.r.create(item, list(lead_minutes), **kw)
        self.linked.add(j["id"])
        return j

    async def at(self, minutes):
        self.now = T0 + minutes * 60
        return await self.r.tick()


async def test_one_reminder_per_event_at_its_start_minus_the_lead():
    g = Rig(ev("a", 60), ev("b", 180, title="Dentist", where="Main St"))
    g.job()
    assert await g.at(0) == 0 and g.sent == []
    assert await g.at(29) == 0
    assert await g.at(30) == 1 and g.sent == [("follow_reminder", "Team meeting", "Team meeting at 10:00")]
    assert await g.at(31) == 0                                                       # once
    assert await g.at(150) == 1 and g.sent[-1] == ("follow_reminder", "Dentist", "Dentist at 12:00 (Main St)")
    assert await g.at(200) == 0 and len(g.sent) == 2


async def test_new_moved_and_cancelled_events_are_followed_with_a_fresh_read_before_telling():
    g = Rig(ev("a", 60), ev("b", 60, title="Lunch"))
    g.job()
    assert await g.at(20) == 0
    reads = g.cal.reads
    g.cal.events["a"]["start"] = T0 + 240 * 60                                       # moved later
    del g.cal.events["b"]                                                            # cancelled
    g.cal.events["c"] = ev("c", 100, title="New one")                                # added
    assert await g.at(30) == 0 and g.cal.reads == reads + 1                          # due under the last read: read again first
    assert await g.at(70) == 1 and g.sent[-1][1] == "New one"
    assert await g.at(210) == 1 and g.sent[-1][2] == "Team meeting at 13:00"
    assert [s[1] for s in g.sent] == ["New one", "Team meeting"]


async def test_an_event_moved_after_its_reminder_is_reminded_again_at_its_new_time():
    g = Rig(ev("a", 60))
    g.job()
    assert await g.at(30) == 1
    g.cal.events["a"]["start"] = T0 + 120 * 60
    assert await g.at(60) == 0
    assert await g.at(90) == 1 and g.sent[-1][2] == "Team meeting at 11:00"


async def test_a_calendar_that_could_not_be_read_keeps_its_events():
    g = Rig(ev("a", 50, source="google:x"))
    g.job()
    assert await g.at(0) == 0
    g.cal.failed = ["google:x"]
    assert await g.at(20) == 1                                                       # a failed read is not a cancelled event


async def test_only_the_events_the_job_asks_for_and_never_all_day_ones():
    g = Rig(ev("a", 60, title="Standup"), ev("b", 60, title="Lunch", where="Cafe"), ev("c", 60, title="Review", source="google:w"),
            ev("d", 60, title="Standup party", all_day=True), ev("e", 60, title="Other"))
    g.job(words=["cafe", "STANDUP"])
    g.job(item="Work", lead_minutes=(10,), calendars=["google:w"])
    assert await g.at(30) == 2 and sorted(s[1] for s in g.sent) == ["Lunch", "Standup"]
    assert await g.at(50) == 1 and g.sent[-1][1] == "Review" and len(g.sent) == 3


async def test_several_leads_and_an_event_found_late_make_one_reminder_each_time():
    g = Rig(ev("a", 24 * 60 + 60))                                                   # tomorrow 10:00
    g.job(lead_minutes=(30, 1440))
    assert await g.at(60) == 1 and g.sent[-1][2] == "Team meeting at 2026-11-21 10:00"   # a day before
    assert await g.at(24 * 60 + 30) == 1 and g.sent[-1][2] == "Team meeting at 10:00"
    g.cal.events["late"] = ev("late", 24 * 60 + 90, title="Call")                     # found 15 minutes before it starts
    assert await g.at(24 * 60 + 75) == 1 and g.sent[-1][1] == "Call"                  # both leads have passed: one reminder
    assert await g.at(24 * 60 + 80) == 0


async def test_quiet_hours_hold_a_reminder_until_waking_unless_the_event_is_close():
    g = Rig(ev("a", 400), awake=False)
    g.job(lead_minutes=(300,))
    assert await g.at(100) == 0                                                      # due, but quiet hours and more than 3 hours away
    g.is_awake = True
    assert await g.at(101) == 1
    close = Rig(ev("a", 120), awake=False)
    close.job()
    assert await close.at(90) == 1                                                   # starts within 3 hours: told anyway


async def test_a_job_runs_only_while_an_item_links_it_and_nothing_is_read_without_one():
    g = Rig(ev("a", 60))
    assert await g.at(30) == 0 and g.cal.reads == 0                                  # no job: no calendar read at all
    j = g.job()
    g.linked.clear()                                                                 # its item is gone
    assert await g.at(30) == 0 and g.sent == [] and g.cal.reads == 0 and g.r.get(j["id"]) is None


async def test_sent_rows_go_a_day_after_their_event():
    g = Rig(ev("a", 60))
    g.job()
    await g.at(30)
    count = lambda: g.r.store._all("SELECT COUNT(*) AS n FROM event_reminders_sent")[0]["n"]
    assert count() == 1
    await g.at(60 + 24 * 60 + 5)
    assert count() == 0


def test_the_timing_in_words_and_what_a_job_accepts():
    assert lead_words([30]) == "30 min before each event"
    assert lead_words([60, 1440, 90]) == "1 d, 90 min, 1 h before each event"
    g = Rig()
    for bad in ([], [0], [10081], [10, 20, 30, 40], ["x"], [True]):
        with pytest.raises(ReminderError):
            g.r.create("x", bad)
    with pytest.raises(ReminderError):
        g.r.create("x", [30], note="n" * 201)
    j = g.r.create("x", [30, 30, "15"], words=["  Cafe ", "cafe"])
    assert j["leads"] == [30, 15] and j["words"] == ["cafe"]
    assert g.r.public(j["id"]) == {"name": "30 min, 15 min before each event", "description": "30 min, 15 min before each event · cafe",
                                   "on": True}
    assert g.r.update(j["id"], name="Before work things")["name"] == "Before work things" and g.r.get(j["id"])["leads"] == [30, 15]
    assert g.r.delete(j["id"]) and g.r.public(j["id"]) is None and not g.r.delete(j["id"])


def test_the_kind_is_a_checklist_job_kind_and_its_approvals_never_become_rules():
    assert KIND == "event_reminder" and KIND in JOB_KINDS
    for name in ("event_reminders_set", "event_reminders_remove"):
        assert rule_kind(name) == "" and not schedule_rule_ok(name)
    src = (ROOT / "server" / "src" / "assistant" / "server.py").read_text(encoding="utf-8")
    assert 'scheduler.add_every("event_reminders", 60, event_reminders.tick)' in src


# ------------------------------------------------------------------------------------------------------------ the tools and the setup
async def test_the_tools_need_the_item_on_the_list_link_it_and_unlink_it(tmp_path):
    app = app_for(tmp_path, setup_model("ok"))
    tools = {t.name: t for t in app.state.tools}
    ck = app.state.checklist
    ck.home.write("CHECKLIST.md", "- Before each event\n", by="agent")
    setter = tools["event_reminders_set"]
    assert setter.requires_approval and tools["event_reminders_remove"].requires_approval
    assert "Before each event" in setter.validate({"item": "Not there", "lead_minutes": [30]})
    assert "google:none" in setter.validate({"item": "Before each event", "lead_minutes": [30], "calendars": ["google:none"]})
    assert "lead" in setter.validate({"item": "Before each event"})
    assert setter.validate({"item": "Before each event", "lead_minutes": [30]}) is None
    out = await setter.handler({"item": "before each  EVENT", "lead_minutes": [30], "note": "Bring the badge"})
    assert out.startswith("Saved event reminder #1")
    e = ck.entries()[0]
    assert e["state"] == "own" and e["job"]["kind"] == "event_reminder" and e["job"]["id"] == 1
    assert e["job"]["name"] == "30 min before each event"
    listed = await tools["event_reminders_list"].handler({})
    assert "#1" in listed and "Bring the badge" in listed and "Before each event" in listed
    out = await setter.handler({"item": "Before each event", "id": 1, "lead_minutes": [10]})
    assert out.startswith("Saved event reminder #1") and app.state.event_reminders.get(1)["leads"] == [10]
    assert (await tools["event_reminders_remove"].handler({"id": 1})) == "Removed."
    assert ck.entries()[0]["state"] == "send_times"                                   # its job is gone: the send times again
    assert (await tools["event_reminders_remove"].handler({"id": 1})).startswith("error")


async def test_the_owners_item_gets_its_event_reminders_without_a_card_and_loses_them_with_the_item(tmp_path, monkeypatch):
    monkeypatch.setattr(checklist_mod, "SETUP_DELAY", 0.0)
    fn = setup_model(("event_reminders_set", {"item": "Before each event", "lead_minutes": [30]}),
                     "I will remind you 30 minutes before each event.")
    app = app_for(tmp_path, fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        await c.put("/api/checklist", json={"items": ["Before each event"]})

        async def linked():
            e = (await c.get("/api/checklist")).json()["entries"]
            return e if e and e[0]["state"] == "own" else None
        e = await wait_for(linked)                                                    # a card would have stopped it here
        assert e[0]["job"]["kind"] == "event_reminder" and e[0]["job"]["name"] == "30 min before each event"
        first = next(r for r in fn.seen if any(isinstance(m.get("content"), str) and m["content"].startswith(CHECKLIST_MARK)
                                               for m in r.messages))
        assert {"checklist_link", "event_reminders_set"} <= {t["function"]["name"] for t in first.tools}   # shown up front
        assert [j["item"] for j in app.state.event_reminders.list()] == ["Before each event"]
        await c.put("/api/checklist", json={"items": []})
        assert app.state.event_reminders.list() == []                                 # the owner took the item off: its job went too
        await asyncio.sleep(0.1)
