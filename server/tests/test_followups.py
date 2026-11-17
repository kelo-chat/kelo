"""Follow-ups: reminders about what waits for the user (a login, a background job, an approval, the agent's own question).
Fake model, fake clock, fake jobs; timing 30 min -> 3 h -> on hold, waking hours, the deadline reminder, every way an item stops,
NOTIFY_PREFS.md, the push type and the API."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from assistant import i18n
from assistant.followups import LABELS, TEMPLATES, DEADLINE_NOTE, LAST_NOTE, PAYMENT_NOTE, Followups, parse_when
from assistant.home import Home, NOTIFY_PREFS
from assistant.owner_copies import OwnerCopies
from assistant.providers import Done, TextDelta
from assistant.push import NOTE_TYPES, check_data, note_data
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.workers import MAIN_ONLY

from notify_fake import FakeRules
from test_server import AUTH, TOKEN, Running

SEOUL = ZoneInfo("Asia/Seoul")
KO = json.loads((__import__("pathlib").Path(i18n.__file__).parent / "locales" / "ko.json").read_text(encoding="utf-8"))
MIN = 60.0
HOUR = 3600.0


def ts(*a) -> float:
    return datetime(*a, tzinfo=SEOUL).timestamp()


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Writer:
    """A fake main model that writes the follow-up: remembers what it was told, answers with a fixed line (or fails)."""

    def __init__(self, reply="계속하려면 로그인이 필요해요. 지금 넣을까요, 나중에 할까요?", fail=False):
        self.reply, self.fail, self.requests = reply, fail, []

    async def __call__(self, provider, request, **kw):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("model down")
        yield TextDelta(self.reply)
        yield Done("stop")


class FakeWorkers:
    def __init__(self):
        self.rows = {}

    def pause(self, wid, at, waiting_for="user", login=None, summary="Which seat do you want?", deadline=None, title="Concert tickets"):
        rep = {"status": "needs_input", "summary": summary, "details": "", "keep_tab": False, "at": at}
        if deadline:
            rep["deadline"] = deadline
        self.rows[wid] = {"id": wid, "title": title, "status": "needs_input", "waiting_for": waiting_for, "login": login, "reason": summary,
                          "task_id": 7, "updated_at": at, "report": rep}

    def list(self, limit=50, status=None, parent_task_id=None):
        return [dict(r) for r in self.rows.values() if status is None or r["status"] == status]

    def get(self, wid):
        r = self.rows.get(wid)
        return dict(r) if r else None


def make(tmp_path=None, *, t0=None, writer=None, prefs=None, rules=None, **kw):
    store = Store()
    store.set_profile(timezone="Asia/Seoul", provider="p", model="m")
    home = None
    if tmp_path is not None:
        home = Home(tmp_path / "home")
        home.owner_copies = OwnerCopies(store)   # the rules are the owner's saved copy (owner_copies.py), written as the owner's editor does
        home.ensure_side_files()
        if prefs is not None:
            home.write(NOTIFY_PREFS, prefs, by="user")
    clock = Clock(t0 or ts(2026, 11, 7, 10, 0))
    sent = []

    async def deliver(item, text):
        sent.append((item, text))
        return store.add_message("assistant", text)

    writer = writer if writer is not None else Writer()
    f = Followups(store, home=home, stream_fn=writer, model=lambda: ("p", "m"), deliver=deliver, clock=clock, **kw)
    if rules is not None:
        f.notify_block = rules.block   # the main model's reading of the owner's don't-tell rules (notify_block.py), faked
    return f, store, clock, sent, writer


def login_slot(store, name="login-tickets", host="tickets.example.com"):
    store.save_credential(name, "Tickets", [host], {}, "", kind="login")


# ---------------------------------------------------------------------------------------------------------------- timing
async def test_a_login_waits_30_minutes_then_3_hours_then_goes_on_hold():
    f, store, clock, sent, writer = make()
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com", task={"task_id": 3, "source": "chat"})
    assert f.get(fid)["stage"] == "first" and f.get(fid)["next_at"] == clock.t + 30 * MIN
    clock.t += 29 * MIN
    assert await f.tick() == 0 and sent == []
    clock.t += 1 * MIN
    assert await f.tick() == 1
    item, text = sent[0]
    assert text == writer.reply and item["kind"] == "login" and item["login"] == "login-tickets" and item["task_id"] == 3
    facts = writer.requests[0].messages[-1]["content"]
    assert "Tickets" in facts and "keychain" in facts and "last reminder" not in facts
    assert writer.requests[0].tools == []
    now = f.get(fid)
    assert now["stage"] == "second" and now["sent"] == 1 and now["next_at"] == clock.t + 3 * HOUR and now["message_id"]
    clock.t += 3 * HOUR - MIN
    assert await f.tick() == 0
    clock.t += MIN
    assert await f.tick() == 1
    assert "This is the last reminder." in writer.requests[1].messages[-1]["content"]
    held = f.get(fid)
    assert held["stage"] == "on_hold" and held["next_at"] is None and held["status"] == "open"
    clock.t += 24 * HOUR
    assert await f.tick() == 0 and len(sent) == 2
    assert [i["id"] for i in f.items()] == [fid]                       # on hold: still listed, actions still there
    assert {a["kind"] for a in held["actions"]} == {"open_login", "snooze", "drop"}


async def test_outside_waking_hours_it_waits_for_the_morning():
    f, store, clock, sent, _ = make(t0=ts(2026, 11, 7, 21, 50))
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    assert f.get(fid)["next_at"] == ts(2026, 11, 8, 7, 0)              # 22:20 is after wake_end (22:00)
    clock.t = ts(2026, 11, 7, 22, 30)
    assert await f.tick() == 0
    clock.t = ts(2026, 11, 8, 7, 0)
    assert await f.tick() == 1


async def test_custom_waking_hours_and_delays_are_used():
    f, store, clock, sent, _ = make(wake=lambda: ("09:00", "18:00"))
    f.update(first_delay_min=10, second_delay_min=20)
    login_slot(store)
    clock.t = ts(2026, 11, 7, 8, 0)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    assert f.get(fid)["next_at"] == ts(2026, 11, 7, 9, 0)
    with pytest.raises(ValueError):
        f.update(first_delay_min=1)
    with pytest.raises(ValueError):
        f.update(second_delay_min="soon")


async def test_one_extra_reminder_an_hour_before_a_deadline_even_on_hold():
    f, store, clock, sent, writer = make(writer=Writer(fail=True))
    tools = {t.name: t for t in f.tools(lambda: {"task_id": 9})}
    out = await tools["await_reply"].handler({"topic": "공연 좌석", "deadline": "2026-11-07T18:00"})
    assert out.startswith("Noted as f")
    fid = f.items()[0]["id"]
    assert f.get(fid)["deadline"] == ts(2026, 11, 7, 18, 0) and f.get(fid)["topic"] == "공연 좌석"
    for step in (30 * MIN, 3 * HOUR):                                   # 10:30 first, 13:30 second (the last of the ladder)
        clock.t += step
        assert await f.tick() == 1
    assert f.get(fid)["stage"] == "on_hold" and f.get(fid)["next_at"] == ts(2026, 11, 7, 17, 0)
    clock.t = ts(2026, 11, 7, 17, 0)
    assert await f.tick() == 1
    assert "18:00" in sent[-1][1] and f.get(fid)["deadline_sent"] is True and f.get(fid)["next_at"] is None
    clock.t += 5 * HOUR
    assert await f.tick() == 0 and len(sent) == 3


async def test_a_deadline_reminder_close_to_a_regular_one_is_merged():
    f, store, clock, sent, _ = make(writer=Writer(fail=True))
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com", "2026-11-07T11:40")
    clock.t = ts(2026, 11, 7, 10, 30)                                   # the first; the deadline one (10:40) is 10 min away
    assert await f.tick() == 1
    row = f.get(fid)
    assert row["deadline_sent"] is True and row["sent"] == 1 and "11:40" in sent[0][1]
    clock.t = ts(2026, 11, 7, 10, 45)
    assert await f.tick() == 0


async def test_a_deadline_already_near_when_the_stall_began_adds_nothing():
    f, store, clock, sent, _ = make()
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com", "2026-11-07T10:40")
    assert f.get(fid)["next_at"] == ts(2026, 11, 7, 10, 30)
    assert parse_when("10:40", SEOUL, ts(2026, 11, 7, 11, 0)) == ts(2026, 11, 8, 10, 40)
    assert parse_when("nonsense", SEOUL, clock.t) is None


async def test_a_schedules_next_run_is_the_deadline_of_its_stalled_approval():
    pending = [{"approval_id": "a1", "label": "Typing a saved login", "site": "tickets.example.com", "detail": "x", "task_id": 4,
                "asked_at": ts(2026, 11, 7, 10, 0), "unattended": True, "schedule_id": 5}]
    f, store, clock, sent, _ = make(pending_approvals=lambda: pending, schedule_next=lambda sid: ts(2026, 11, 7, 20, 0) if sid == 5 else None)
    f.reconcile()
    assert f.items()[0]["deadline"] == ts(2026, 11, 7, 20, 0)


# ---------------------------------------------------------------------------------------------------------------- each kind
async def test_a_paused_job_is_followed_up_and_closes_when_it_moves_on():
    jobs = FakeWorkers()
    f, store, clock, sent, writer = make(workers=jobs)
    jobs.pause("w1", clock.t, summary="Which seat do you want?")
    f.reconcile()
    item = f.items()[0]
    assert item["kind"] == "worker" and item["worker_id"] == "w1" and item["waiting_for"] == "user"
    assert [a["kind"] for a in item["actions"]] == ["resume", "snooze", "drop"]
    clock.t += 30 * MIN
    assert await f.tick() == 1
    assert "Which seat" in writer.requests[0].messages[-1]["content"]
    jobs.rows["w1"]["status"] = "running"                               # resumed from the app or by the agent
    f.reconcile()
    assert f.items() == [] and f.get(item["id"])["closed_why"] == "job moved on"
    jobs.pause("w1", clock.t + 60, summary="One more code please")      # it stops again later: a new item
    f.reconcile()
    assert len(f.items()) == 1 and f.items()[0]["id"] != item["id"]


async def test_a_job_waiting_for_a_requested_login_is_folded_into_the_login_item():
    jobs = FakeWorkers()
    f, store, clock, sent, _ = make(workers=jobs)
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com", task={"task_id": 3}, worker="w2")
    jobs.pause("w2", clock.t + 5, waiting_for="login", login="login-tickets", deadline="2026-11-07T20:00")
    f.reconcile()
    items = f.items()
    assert [i["id"] for i in items] == [fid] and items[0]["worker_id"] == "w2" and items[0]["deadline"] == ts(2026, 11, 7, 20, 0)
    store.set_login("login-tickets", "me", "pw")                        # saved: the item resolves (and the job goes on by itself)
    f.reconcile()
    assert f.items() == [] and f.get(fid)["closed_why"] == "login saved"


async def test_a_login_entered_or_closed_stops_the_follow_up():
    f, store, clock, sent, _ = make()
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    store.delete_draft("login-tickets")                                 # the user closed the sheet
    clock.t += 30 * MIN
    assert await f.tick() == 0 and f.get(fid)["status"] == "resolved"
    login_slot(store, "login-saved", "saved.example.com")
    store.set_login("login-saved", "me", "pw")
    assert f.login_requested("login-saved", "Saved", "saved.example.com") is None   # nothing to wait for


async def test_an_unanswered_unattended_approval_gets_buttons_and_closes_when_answered():
    pending = [{"approval_id": "ap1", "label": "Asking to confirm a payment", "site": "shop.example.com", "detail": "Mug x2 · 24,000 KRW",
                "task_id": 11, "asked_at": ts(2026, 11, 7, 10, 0), "unattended": True, "payment": {"amount_text": "24,000 KRW"}},
               {"approval_id": "ap2", "label": "Clicking on the page", "site": "a.example.com", "detail": "", "task_id": 12,
                "asked_at": ts(2026, 11, 7, 10, 0), "unattended": False}]   # in front of the user: not followed up
    f, store, clock, sent, writer = make(pending_approvals=lambda: pending)
    f.reconcile()
    items = f.items()
    assert len(items) == 1 and items[0]["approval_id"] == "ap1" and "24,000 KRW" in items[0]["title"]
    assert [(a["kind"], a.get("approval_id")) for a in items[0]["actions"][:2]] == [("approve", "ap1"), ("deny", "ap1")]
    clock.t += 30 * MIN
    assert await f.tick() == 1
    assert "It is a payment." in writer.requests[0].messages[-1]["content"] and sent[0][0]["approval_id"] == "ap1"
    pending.clear()
    f.reconcile()
    assert f.items() == []
    pending.append({**{"approval_id": "ap1", "unattended": True, "asked_at": clock.t}})
    f.reconcile()
    assert f.items() == []                                               # a closed approval is never followed up again


async def test_await_reply_closes_on_the_users_next_message():
    f, store, clock, sent, _ = make(user_marks=("[Handoff",))
    store.add_message("user", "표 사줘")
    tools = {t.name: t for t in f.tools(lambda: {"task_id": 2})}
    assert (await tools["await_reply"].handler({"topic": ""})).startswith("Error")
    assert (await tools["await_reply"].handler({"topic": "좌석", "deadline": "2020-01-01T00:00"})).startswith("Error")
    await tools["await_reply"].handler({"topic": "좌석 등급"})
    fid = f.items()[0]["id"]
    assert f.get(fid)["task_id"] == 2 and [a["kind"] for a in f.get(fid)["actions"]] == ["snooze", "drop"]
    store.add_message("user", "[Handoff from a background job, not from the user] ...")   # not the user's words
    f.reconcile()
    assert f.items()[0]["id"] == fid
    store.add_message("user", "R석으로")
    clock.t += 30 * MIN
    assert await f.tick() == 0 and f.get(fid)["closed_why"] == "user replied"


async def test_a_user_message_restarts_the_wait_of_a_blocking_item():
    f, store, clock, sent, _ = make()
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    clock.t += 20 * MIN
    store.add_message("user", "잠깐만, 다른 거 먼저")
    f.reconcile()
    assert f.get(fid)["status"] == "open" and f.get(fid)["next_at"] == clock.t + 30 * MIN
    clock.t += 10 * MIN                                                  # the original 30 min are up, but the user was just here
    assert await f.tick() == 0
    clock.t += 20 * MIN
    assert await f.tick() == 1


# ---------------------------------------------------------------------------------------------------------------- stopping
async def test_snooze_and_drop_from_the_agents_tools():
    f, store, clock, sent, _ = make()
    login_slot(store)
    a = f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    tools = {t.name: t for t in f.tools()}
    b = f.awaiting_reply("배송 주소", None, None)
    out = await tools["followup_snooze"].handler({"item": "login-tickets", "until": "2026-11-07T15:00"})
    assert "f%d" % a in out and f.get(a)["next_at"] == ts(2026, 11, 7, 15, 0)
    assert (await tools["followup_snooze"].handler({"item": "nothing like it", "minutes": 30})).startswith("Error: no open follow-up")
    assert "f%d" % b in (await tools["followup_snooze"].handler({"item": "nothing", "minutes": 30}))   # the listing names the open ones
    clock.t = ts(2026, 11, 7, 10, 30)
    assert await f.tick() == 1 and sent[0][0]["id"] == b                # the reply item is due; the login waits for 15:00
    out = await tools["followup_cancel"].handler({"item": f"f{b}"})
    assert "Stopped" in out and f.get(b)["status"] == "dropped"
    clock.t = ts(2026, 11, 7, 15, 0)
    assert await f.tick() == 1 and f.get(a)["stage"] == "second" and f.get(a)["snoozed_until"] is None
    await tools["followup_cancel"].handler({"item": "all"})
    assert f.items() == []


async def test_notify_prefs_keep_an_item_but_never_remind(tmp_path):
    prefs = "# NOTIFY_PREFS.md\n\n## Tell me\n\n## Do not tell me\n- tickets.example\n\n## When\n\n## How\n"
    rules = FakeRules("tickets.example")
    f, store, clock, sent, _ = make(tmp_path, prefs=prefs, rules=rules)
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    clock.t += 30 * MIN
    assert await f.tick() == 0 and sent == []
    item = f.get(fid)
    assert item["suppressed"] is True and item["status"] == "open" and item["next_at"] is None
    assert len(rules.calls) == 1 and "tickets.example" in rules.calls[0]["rules"]


async def test_rules_nobody_can_read_hold_the_item_back_without_marking_it(tmp_path):
    prefs = "# NOTIFY_PREFS.md\n\n## Do not tell me\n- anything about the gym\n"
    for rules in (FakeRules(down=True), None):                   # the model is away / no reader wired
        f, store, clock, sent, _ = make(tmp_path / ("a" if rules else "b"), prefs=prefs, rules=rules)
        login_slot(store)
        fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com")
        clock.t += 30 * MIN
        assert await f.tick() == 0 and sent == []
        assert f.get(fid)["suppressed"] is False                 # not marked: asked again next pass
    ok = FakeRules("gym")
    f, store, clock, sent, _ = make(tmp_path / "c", prefs=prefs, rules=ok)
    login_slot(store)
    f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    clock.t += 30 * MIN
    assert await f.tick() == 1 and len(ok.calls) == 1            # the rules are read and the item is told
    clock.t += 30 * MIN
    await f.tick()
    assert len(ok.calls) == 1                                    # the same item and rules: answered from the cache


async def test_a_rule_the_agent_writes_into_notify_prefs_is_not_applied(tmp_path):
    f, store, clock, sent, _ = make(tmp_path, prefs="# NOTIFY_PREFS.md\n\n## Tell me\n\n## Do not tell me\n\n## When\n\n## How\n")
    f.home.write(NOTIFY_PREFS, "# NOTIFY_PREFS.md\n\n## Do not tell me\n- tickets.example\n", by="agent")   # a note of the agent's, not a rule
    login_slot(store)
    fid = f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    clock.t += 30 * MIN
    assert await f.tick() == 1 and f.get(fid)["suppressed"] is False                                       # the owner's rules are the saved copy


async def test_off_or_busy_means_no_follow_up():
    busy = [True]
    f, store, clock, sent, _ = make(busy=lambda: busy[0])
    login_slot(store)
    f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    clock.t += 30 * MIN
    assert await f.tick() == 0                                            # a chat reply is running
    f.update(enabled=False)
    busy[0] = False
    assert await f.tick() == 0
    f.update(enabled=True)
    assert await f.tick() == 1


async def test_the_template_is_used_when_the_model_fails_in_the_apps_language():
    f, store, clock, sent, _ = make(writer=Writer(fail=True))
    login_slot(store)
    f.login_requested("login-tickets", "Tickets", "tickets.example.com")
    clock.t += 30 * MIN
    tok = i18n.use("ko")
    try:
        assert await f.tick() == 1
    finally:
        i18n.reset(tok)
    assert sent[0][1] == KO[TEMPLATES["login"]].format(label="Tickets")


# ---------------------------------------------------------------------------------------------------------------- catalog, push, tools
def test_every_follow_up_text_has_a_korean_entry():
    for key in [*TEMPLATES.values(), DEADLINE_NOTE, LAST_NOTE, PAYMENT_NOTE, *LABELS.values(), "Waiting for your answer", "Putting off a reminder",
                "Stopping a reminder"]:
        assert key in KO, key


def test_the_push_type_followup_is_allowed_and_carries_ids_only():
    assert "followup" in NOTE_TYPES
    data = note_data({"type": "followup", "id": 5, "task_id": 3, "approval_id": None, "schedule_id": None})
    assert check_data(data) == {"type": "followup", "id": "5", "task_id": "3"}


def test_background_jobs_do_not_get_the_follow_up_tools():
    assert {"await_reply", "followup_snooze", "followup_cancel"} <= set(MAIN_ONLY)


# ---------------------------------------------------------------------------------------------------------------- through the server
async def test_the_api_and_the_notification(tmp_path):
    async def fn(provider, request, **kw):
        yield TextDelta("좌석 등급을 아직 못 정하셨어요. 지금 고를까요, 나중에 할까요?")
        yield Done("stop")

    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home"), db_path=str(tmp_path / "a.db"))
    app = create_app(s, stream_fn=fn)
    f = app.state.followups
    clock = Clock(ts(2026, 11, 7, 10, 0))
    f.clock = clock
    f.store.set_profile(timezone="Asia/Seoul")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/followups/settings")).json()["enabled"] is True
        assert (await c.put("/api/followups/settings", json={"first_delay_min": 2})).status_code == 422
        got = (await c.put("/api/followups/settings", json={"second_delay_min": 120})).json()
        assert got["second_delay_min"] == 120 and got["wake_start"] == "07:00" and got["timezone"] == "Asia/Seoul"
        fid = f.awaiting_reply("좌석 등급", None, None)
        item = (await c.get("/api/followups")).json()["items"][0]
        assert item["id"] == fid and item["stage"] == "first" and item["kind"] == "reply"
        clock.t += 30 * MIN
        assert await f.tick() == 1
        notes = (await c.get("/api/notifications")).json()["items"]
        assert notes[0]["type"] == "followup" and notes[0]["followup_id"] == fid and "좌석" in notes[0]["body"]
        msgs = (await c.get("/api/messages")).json()
        item = (await c.get("/api/followups")).json()["items"][0]
        assert msgs[-1]["role"] == "assistant" and msgs[-1]["id"] == item["message_id"] and item["stage"] == "second"
        assert (await c.get("/api/agent/status")).json()["open_followups"] == 1
        assert (await c.post(f"/api/followups/{fid}/snooze", json={"minutes": 1})).status_code == 422
        r = (await c.post(f"/api/followups/{fid}/snooze", json={"minutes": 90})).json()
        assert r["ok"] and r["item"]["snoozed_until"] == clock.t + 90 * MIN and r["item"]["next_at"] == clock.t + 90 * MIN
        r = (await c.post(f"/api/followups/{fid}/drop")).json()
        assert r["item"]["status"] == "dropped"
        assert (await c.get("/api/agent/status")).json()["open_followups"] == 0       # handled: the apps' red badge goes away
        assert (await c.post(f"/api/followups/{fid}/drop")).status_code == 404
        assert (await c.post("/api/followups/9999/snooze", json={"minutes": 30})).status_code == 404
        assert (await c.get("/api/followups")).json()["items"] == []
        assert (await c.get("/api/followups?closed=true")).json()["items"][0]["id"] == fid


async def test_a_chat_turn_starts_login_and_reply_items(tmp_path):
    from assistant.providers import ToolCall
    from test_server import read_events, scripted

    fn = scripted([ToolCall("c1", "request_login", json.dumps({"site": "https://tickets.example.com/login", "name": "tickets"})),
                   Done("tool_calls")],
                  [ToolCall("c2", "await_reply", json.dumps({"topic": "seat class"})), Done("tool_calls")],
                  [TextDelta("Which seat class?"), Done("stop")])
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None, home_dir=str(tmp_path / "home"), db_path=str(tmp_path / "a.db"))
    app = create_app(s, stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/talk", json={"text": "buy concert tickets"}) as r:
            events = await read_events(r)
        ev = next(e for e in events if e["type"] == "followup")
        assert ev["kind"] == "reply" and ev["status"] == "open" and ev["id"] == "c2"
        items = {i["kind"]: i for i in (await c.get("/api/followups")).json()["items"]}
        assert items["login"]["login"] == "tickets" and items["login"]["task_id"] and items["reply"]["topic"] == "seat class"
        assert items["login"]["task_id"] == items["reply"]["task_id"]


async def test_an_approval_items_own_reminder_never_lands_at_or_after_the_cards_end():
    pending = [{"approval_id": "ap9", "label": "Clicking on the page", "site": "a.example.com", "detail": "", "task_id": 1,
                "asked_at": ts(2026, 11, 7, 10, 0), "expires_at": ts(2026, 11, 7, 10, 30), "unattended": True}]
    f, store, clock, sent, _ = make(pending_approvals=lambda: pending)
    f.reconcile()
    assert f.items()[0]["next_at"] is None                        # 30 min = the card's end: the expired item takes over there
    clock.t += 30 * MIN
    assert await f.tick() == 0
    f.update(first_delay_min=29)                                  # one minute before the end: no two-minute margin any more
    assert f.items()[0]["next_at"] == ts(2026, 11, 7, 10, 29)


# ---------------------------------------------------------------------------------------------------------------- expired approvals
CARD = {"approval_id": "ap1", "label": "Sending a message", "site": "mail.example.com", "detail": "to bob", "task_id": 21,
        "asked_at": ts(2026, 11, 7, 9, 30), "expires_at": ts(2026, 11, 7, 10, 0), "unattended": True}


async def test_an_expired_card_is_followed_up_at_once_then_after_3_hours_then_on_hold():
    pending = [dict(CARD)]
    f, store, clock, sent, writer = make(pending_approvals=lambda: pending)
    f.reconcile()
    approval = f.items()[0]
    assert approval["kind"] == "approval"
    fid = f.approval_expired(pending.pop(), {"kind": "worker", "worker_id": "w1"})
    assert f.get(approval["id"])["status"] == "resolved" and f.get(approval["id"])["closed_why"] == "approval expired"
    item = f.get(fid)
    assert item["kind"] == "approval_expired" and item["next_at"] == clock.t and item["origin"] == "worker"
    assert item["approval_id"] is None and item["worker_id"] == "w1" and item["title"] == "Sending a message · mail.example.com"
    assert [a["kind"] for a in item["actions"]] == ["retry", "snooze", "drop"]
    assert item["actions"][0] == {"kind": "retry", "followup_id": fid, "label": "Try again"}
    assert await f.tick() == 1                                     # right at expiry: the user was away the whole 30 minutes
    facts = writer.requests[0].messages[-1]["content"]
    assert "ran out unanswered at 10:00" in facts and "try again" in facts and "last reminder" not in facts
    assert f.get(fid)["stage"] == "second" and f.get(fid)["next_at"] == clock.t + 3 * HOUR
    clock.t += 3 * HOUR
    assert await f.tick() == 1 and "This is the last reminder." in writer.requests[1].messages[-1]["content"]
    assert f.get(fid)["stage"] == "on_hold" and f.get(fid)["next_at"] is None and f.get(fid)["status"] == "open"
    assert f.approval_expired({**CARD}, None) == fid                # the same card again: nothing new


async def test_an_expired_card_at_night_waits_for_the_morning_and_attended_cards_are_ignored():
    f, store, clock, sent, _ = make(t0=ts(2026, 11, 7, 23, 0))
    assert f.approval_expired({**CARD, "unattended": False}, {"kind": "schedule", "schedule_id": 3}) is None
    fid = f.approval_expired(dict(CARD), {"kind": "schedule", "schedule_id": 3})
    assert f.get(fid)["next_at"] == ts(2026, 11, 8, 7, 0) and f.get(fid)["schedule_id"] == 3
    assert await f.tick() == 0


async def test_the_expired_template_in_korean_with_the_payment_note_and_cards_of_one_run_make_one_item():
    f, store, clock, sent, _ = make(writer=Writer(fail=True))
    tok = i18n.use("ko")
    fid = f.approval_expired(dict(CARD), {"kind": "worker", "worker_id": "w1"})
    pay = {**CARD, "approval_id": "ap2", "label": "결제 확인", "site": "shop.example.com", "payment": {"amount_text": "24,000원"}}
    assert f.approval_expired(pay, {"kind": "worker", "worker_id": "w1"}) == fid
    assert f.get(fid)["title"] == "Sending a message · mail.example.com; 결제 확인 · 24,000원 · shop.example.com"
    assert await f.tick() == 1
    text = sent[0][1]
    assert text.startswith("자리를 비우신 사이에 확인 요청(") and "다시 시도할까요?" in text and "확인 없이는 결제되지 않아요" in text
    assert f.get(fid)["actions"][0]["label"] == "다시 시도"
    i18n.reset(tok)


async def test_a_job_that_pauses_after_its_card_ran_out_is_folded_and_the_item_closes_when_the_job_goes_on():
    jobs = FakeWorkers()
    jobs.rows["w1"] = {"id": "w1", "title": "Send", "status": "running", "task_id": 21, "updated_at": 0, "report": {}}
    f, store, clock, sent, _ = make(workers=jobs)
    fid = f.approval_expired(dict(CARD), {"kind": "worker", "worker_id": "w1"})
    jobs.pause("w1", clock.t, summary="The send step was not approved")
    f.reconcile()
    assert [i["id"] for i in f.items()] == [fid]                   # one item: the retry covers the paused job
    folded = [i for i in f.items(closed=True) if i["kind"] == "worker"]
    assert folded and folded[0]["closed_why"] == f"folded into follow-up {fid}"
    jobs.rows["w1"]["status"] = "running"                          # resumed some other way
    f.reconcile()
    assert f.get(fid)["status"] == "resolved" and f.get(fid)["closed_why"] == "job moved on"
    fid2 = f.approval_expired({**CARD, "approval_id": "ap3", "task_id": 22}, {"kind": "worker", "worker_id": "gone"})
    f.reconcile()
    assert f.get(fid2)["closed_why"] == "job gone"


async def test_an_expired_schedule_card_closes_when_the_schedule_runs_again_or_is_deleted():
    state = {3: ""}
    f, store, clock, sent, _ = make(schedule_since=lambda sid, since: state[sid])
    fid = f.approval_expired(dict(CARD), {"kind": "schedule", "schedule_id": 3})
    f.reconcile()
    assert f.get(fid)["status"] == "open"
    state[3] = "ran"
    f.reconcile()
    assert f.get(fid)["closed_why"] == "schedule ran again"
    state[3] = "gone"
    fid = f.approval_expired({**CARD, "approval_id": "ap4", "task_id": 30}, {"kind": "schedule", "schedule_id": 3})
    f.reconcile()
    assert f.get(fid)["closed_why"] == "schedule deleted"


async def test_retry_hands_the_run_its_note_and_closes_the_item():
    from assistant.followups import RetryError
    asked = []

    async def retry(item):
        asked.append(item)
        if item["origin"]["kind"] == "schedule":
            raise ValueError("this automatic run is running right now")
        return {"how": "worker_resumed", "worker_id": item["origin"]["worker_id"]}

    f, store, clock, sent, _ = make(retry=retry)
    pay = {**CARD, "payment": {"amount_text": "24,000 KRW"}}
    fid = f.approval_expired(pay, {"kind": "worker", "worker_id": "w1"})
    out = await f.retry(fid)
    assert out["ok"] and out["how"] == "worker_resumed" and out["item"]["status"] == "resolved" and out["item"]["closed_why"] == "tried again"
    note = asked[0]["note"]
    assert "Sending a message" in note and "try it again" in note and "never paid without their OK" in note
    assert out["item"]["actions"] == []                            # closed: no buttons
    assert await f.retry(fid) is None                              # closed: 404 in the API
    busy = f.approval_expired({**CARD, "approval_id": "ap5", "task_id": 40}, {"kind": "schedule", "schedule_id": 2})
    with pytest.raises(RetryError, match="running right now"):
        await f.retry(busy)
    assert f.get(busy)["status"] == "open"                         # still open: the user can try again later
    bare = f.approval_expired({**CARD, "approval_id": "ap6", "task_id": 41}, None)
    assert [a["kind"] for a in f.get(bare)["actions"]] == ["snooze", "drop"]
    with pytest.raises(RetryError):
        await f.retry(bare)
    login = f.awaiting_reply("seat", None, None)
    with pytest.raises(RetryError):
        await f.retry(login)


# ---------------------------------------------------------------------------------------------------------------- expired, through the server
def _writer_or(script):
    """A fake model: follow-up messages get a fixed line, everything else goes to `script(request)`."""
    async def fn(provider, request, **kw):
        if any("short follow-up message" in str(m.get("content")) for m in request.messages[:1]):
            yield TextDelta("자리를 비우신 사이 보내기 확인이 만료됐어요. 다시 시도할까요?")
            yield Done("stop")
            return
        for ev in script(request):
            yield ev
    return fn


def _in_ko(f, clock):
    f.clock = clock
    f.store.set_profile(timezone="Asia/Seoul")


async def test_a_schedules_expired_card_is_followed_up_at_once_and_retry_runs_the_schedule_again(tmp_path, monkeypatch):
    from assistant.providers import ToolCall
    from test_agent_profile import app_settings, approval_tool
    from test_server import follow

    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    ran, prompts = [], []

    def script(req):
        last = req.messages[-1]
        if last.get("role") == "tool":
            return [TextDelta("끝났어요."), Done("stop")]
        prompts.append(str(last.get("content")))
        return [ToolCall("c1", "send", "{}"), Done("tool_calls")]

    app = create_app(app_settings(tmp_path), tools=[approval_tool(ran=ran)], stream_fn=_writer_or(script))
    f = app.state.followups
    clock = Clock(ts(2030, 1, 8, 10, 0))   # a fake morning later than the real runs (a run that began after the expiry closes the item)
    _in_ko(f, clock)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        s = (await c.post("/api/schedules", json={"name": "보내기", "prompt": "보내줘", "cron": "0 9 * * *"})).json()
        run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
        await follow(c, run["turn_id"])
        assert ran == []
        item = None
        for _ in range(100):
            items = (await c.get("/api/followups")).json()["items"]
            if items and items[0]["sent"] == 1:
                item = items[0]
                break
            await __import__("asyncio").sleep(0.02)
        assert item and item["kind"] == "approval_expired" and item["origin"] == "schedule" and item["schedule_id"] == s["id"]
        assert item["actions"][0] == {"kind": "retry", "followup_id": item["id"], "label": "다시 시도"}
        assert item["next_at"] == clock.t + 3 * HOUR
        note = next(n for n in (await c.get("/api/notifications")).json()["items"] if n["type"] == "followup")
        assert note["followup_id"] == item["id"] and "다시 시도할까요?" in note["body"] and not note.get("approval_id")
        msgs = (await c.get("/api/messages")).json()
        assert item["message_id"] in [m["id"] for m in msgs]   # the run's closing message (scheduled_runs.py) comes after it
        for _ in range(100):
            if not (await c.get(f"/api/schedules/{s['id']}")).json()["running"]:
                break
            await __import__("asyncio").sleep(0.02)
        assert (await c.post("/api/followups/99999/retry")).status_code == 404
        got = (await c.post(f"/api/followups/{item['id']}/retry")).json()
        assert got["ok"] and got["how"] == "schedule_started" and got["schedule_id"] == s["id"] and got["turn_id"]
        assert got["item"]["status"] == "resolved" and got["item"]["closed_why"] == "tried again"
        assert "try it again" in prompts[-1] and "보내줘" in prompts[-1]
        pend = None
        for _ in range(100):
            pend = (await c.get("/api/approvals/pending")).json()["items"]
            if pend:
                break
            await __import__("asyncio").sleep(0.02)
        assert pend                                                # it asks again: nothing is approved by trying again
        await c.post(f"/api/approvals/{pend[0]['approval_id']}", json={"approve": True})
        await follow(c, got["turn_id"])
        assert ran == [{}]
        assert (await c.post(f"/api/followups/{item['id']}/retry")).status_code == 404


async def test_a_jobs_expired_card_is_retried_by_resuming_the_job(tmp_path, monkeypatch):
    from test_agent_profile import app_settings, approval_tool
    from test_workers import Brain, call, delegating, one_worker, say, until

    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    ran = []

    def worker(req, n):
        last = str(req.messages[-1].get("content"))
        if req.messages[-1].get("role") == "tool" and last == "done":
            return call("report", {"status": "done", "summary": "보냈어요."}, cid=f"c-end{n}")
        if req.messages[-1].get("role") == "tool":
            return call("report", {"status": "needs_input", "summary": "승인이 없어 못 보냈어요."}, cid=f"c-wait{n}")
        return call("send", {"to": "bob"}, cid=f"c-send{n}")

    def chat(req, n):
        if any("short follow-up message" in str(m.get("content")) for m in req.messages[:1]):
            return say("보내기 확인이 만료됐어요. 다시 시도할까요?")
        return delegating({"task": "bob에게 보내줘"})(req, n)

    brain = Brain(chat=chat, worker=worker, handoff=lambda r, n: say("확인이 필요해요."))
    app = create_app(app_settings(tmp_path), tools=[approval_tool("send", ran)], stream_fn=brain, with_worker_tools=True)
    f = app.state.followups
    _in_ko(f, Clock(ts(2030, 1, 8, 10, 0)))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        from test_server import follow
        await follow(c, (await c.post("/api/turns", json={"text": "보내줘"})).json()["id"])
        w = await one_worker(c, "needs_input")

        async def expired_item():
            items = (await c.get("/api/followups")).json()["items"]
            return items[0] if items and items[0]["kind"] == "approval_expired" and items[0]["sent"] == 1 else None
        item = await until(expired_item)
        assert item["origin"] == "worker" and item["worker_id"] == w["id"]
        assert [i["kind"] for i in (await c.get("/api/followups")).json()["items"]] == ["approval_expired"]   # the paused job is folded in
        # the card the resumed job raises must stay open until the test answers it: with the 0.3 s used above to let the first one
        # expire, a busy machine could miss it between two polls (the test's own timing, not the server's)
        monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 60)
        got = (await c.post(f"/api/followups/{item['id']}/retry")).json()
        assert got["how"] == "worker_resumed" and got["worker_id"] == w["id"]

        async def pending():
            return (await c.get("/api/approvals/pending")).json()["items"]
        pend = await until(pending)
        resumed = brain.calls["worker"][-1].messages
        assert any("try it again" in str(m.get("content")) for m in resumed)
        await c.post(f"/api/approvals/{pend[0]['approval_id']}", json={"approve": True})
        await one_worker(c, "done")
        assert ran == [{"to": "bob"}]


async def test_a_background_tasks_expired_card_is_retried_as_a_new_run(tmp_path):
    from test_agent_profile import answering, app_settings

    fn = answering("다시 해 봤어요.")
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=fn)
    f = app.state.followups
    _in_ko(f, Clock(ts(2030, 1, 8, 10, 0)))
    origin = {"kind": "background", "prompt": "장바구니 정리해줘", "title": "장바구니", "source": "background", "owner": "task:ab12",
              "note": "[The user handed you this task]", "saved_as": "[Task handed over · 장바구니]"}
    fid = f.approval_expired(dict(CARD), origin)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        from test_server import follow
        got = (await c.post(f"/api/followups/{fid}/retry")).json()
        assert got["how"] == "background_started" and got["task_id"]
        await follow(c, got["turn_id"])
        sent = fn.calls[-1].messages[-1]["content"]
        assert "장바구니 정리해줘" in sent and "try it again" in sent and "[The user handed you this task]" in sent
        task = (await c.get(f"/api/tasks/{got['task_id']}")).json()
        assert task["source"] == "background" and task["title"] == "장바구니"
        assert (await c.post(f"/api/followups/{fid}/retry")).status_code == 404
        other = f.awaiting_reply("seat", None, None)
        assert (await c.post(f"/api/followups/{other}/retry")).status_code == 409


def test_await_reply_closes_the_reply_once_its_question_is_written():
    """After await_reply the model only adds fillers ("답변을 기다릴게요.") glued to the question: the loop stops there."""
    f, *_ = make()
    tools = {t.name: t for t in f.tools(lambda: {"task_id": 2})}
    assert tools["await_reply"].closes_reply and not tools["await_reply"].ends_turn
