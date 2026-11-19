"""What a phone shares from itself (contacts, texts, calls, notifications, health) and what it does after an approval (send a text, open
the dialer, change its calendar): sealed per phone, replaced, wiped when turned off or the phone is removed; the work is queued for the
phone, which picks it up and says how it went."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from assistant.phone_calendar import PhoneCalendars
from assistant.phone_data import PhoneData, phone_calendar_write_tools, phone_tools, same_number
from assistant.server import Settings, create_app
from assistant.store import Store

from test_devices import add_phone, bearer
from test_server import AUTH, TOKEN, Running, scripted

SEOUL = ZoneInfo("Asia/Seoul")


def ts(text):
    return datetime.fromisoformat(text).replace(tzinfo=SEOUL).timestamp()


NOW = ts("2026-11-08T09:00")
MOM = {"name": "엄마", "phones": [{"number": "010-1234-5678", "label": "mobile"}], "emails": ["mom@example.com"], "birthday": "--05-08"}
KIM = {"name": "김민수", "phones": [{"number": "+82 10-2222-3333", "label": "휴대전화"}], "org": "케이랩", "title": "팀장"}
KIM2 = {"name": "김민지", "phones": [{"number": "010-9999-0000", "label": "mobile"}]}


def app_for(tmp_path):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")), tools=None,
                      stream_fn=scripted())


def tools_by_name(data, cals=None):
    out = {t.name: t for t in phone_tools(data, lambda: "Asia/Seoul")}
    if cals is not None:
        out.update({t.name: t for t in phone_calendar_write_tools(data, cals, lambda: "Asia/Seoul")})
    return out


async def test_each_kind_is_sealed_per_phone_replaced_wiped_and_goes_with_a_removed_phone(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.put("/api/phone/contacts", json={"items": []})).status_code == 401
        _, r = await add_phone(c, "Pixel")
        phone = bearer(r.json()["token"])
        assert (await c.put("/api/phone/contacts", json={"device_name": "Pixel", "items": [MOM, KIM, {"name": ""}]}, headers=phone)).json()["count"] == 2
        sms = {"address": "010-1234-5678", "body": "저녁 7시에 봐요", "at": NOW - 600, "box": "inbox"}
        assert (await c.put("/api/phone/messages", json={"items": [sms], "can_send": True}, headers=phone)).json()["count"] == 1
        assert (await c.put("/api/phone/call-log", json={"items": [{"number": "010-1234-5678", "type": "missed", "at": NOW}]}, headers=phone)).json()["count"] == 1
        assert (await c.put("/api/phone/health", json={"types": ["steps"], "days": [{"day": "2026-11-07", "steps": 8123}]}, headers=phone)).json()["count"] == 1
        assert (await c.put("/api/phone/dialer", json={}, headers=phone)).json()["ok"]
        assert (await c.put("/api/phone/nonsense", json={}, headers=phone)).status_code == 404
        assert (await c.put("/api/phone/contacts", json={"items": "x"}, headers=phone)).status_code == 422
        assert (await c.put("/api/phone/notifications", json={"items": []}, headers=phone)).status_code == 405
        now = __import__("time").time()
        note = {"app": "은행", "package": "com.bank", "title": "입금 50,000원", "text": "홍길동님이 보냈어요", "at": now - 60}
        add = (await c.post("/api/phone/notifications", json={"items": [note, note, {"title": "old", "at": now - 8 * 86400}]}, headers=phone)).json()
        assert add["added"] == 1 and add["count"] == 1                                   # the same one twice is kept once; old ones are not
        assert (await c.post("/api/phone/notifications", json={"items": [note]}, headers=phone)).json()["added"] == 0
        kinds = (await c.get("/api/phone", headers=phone)).json()["kinds"]
        assert all(kinds[k]["shared"] for k in ("contacts", "messages", "call_log", "health", "dialer", "notifications"))
        assert (await c.get("/api/phone/contacts", headers=AUTH)).json()["shared"] is False   # the access key is another "phone"
        raw = (tmp_path / "s.db").read_bytes()
        for word in ("엄마", "저녁 7시", "홍길동", "mom@example.com", "010-1234-5678"):
            assert word.encode() not in raw                                                   # sealed at rest
        await c.put("/api/phone/contacts", json={"items": [KIM]}, headers=phone)                # a new upload replaces the old one
        assert (await c.get("/api/phone/contacts", headers=phone)).json()["count"] == 1
        assert (await c.delete("/api/phone/notifications", headers=phone)).json() == {"ok": True}
        assert (await c.get("/api/phone/notifications", headers=phone)).json()["shared"] is False
        dev = next(d for d in (await c.get("/api/devices", headers=AUTH)).json()["devices"] if d["name"] == "Pixel")
        await c.delete(f"/api/devices/{dev['id']}", headers=AUTH)
        st = Store(tmp_path / "s.db")
        assert st._all("SELECT COUNT(*) AS n FROM phone_data")[0]["n"] == 0                    # nothing of the removed phone stays


async def test_read_tools_search_contacts_messages_calls_notifications_and_health(tmp_path):
    now = [NOW]
    data = PhoneData(Store(tmp_path / "s.db"), clock=lambda: now[0])
    tools = tools_by_name(data)
    for name in ("phone_contacts", "phone_messages", "phone_call_log", "phone_notifications", "phone_health"):
        assert "not shared" in await tools[name].handler({})
    data.put("1", "contacts", {"device_name": "Pixel", "items": [MOM, KIM]})
    data.put("1", "messages", {"device_name": "Pixel", "window_days": 30, "items": [
        {"address": "01012345678", "body": "저녁 7시에 봐요", "at": ts("2026-11-07T18:00"), "box": "inbox"},
        {"address": "010-1234-5678", "body": "네 좋아요", "at": ts("2026-11-07T18:05"), "box": "sent"},
        {"address": "1588-0000", "body": "[택배] 오늘 도착", "at": ts("2026-10-01T10:00"), "box": "inbox"}]})
    data.put("1", "call_log", {"device_name": "Pixel", "items": [{"number": "+82 10-2222-3333", "type": "missed", "at": ts("2026-11-08T08:00")},
                                                                  {"number": "010-1234-5678", "type": "outgoing", "at": ts("2026-11-06T20:00"), "duration": 125}]})
    data.put("1", "health", {"device_name": "Pixel", "types": ["steps", "sleep"], "days": [
        {"day": "2026-11-07", "steps": 8123, "sleep_minutes": 430, "heart_avg": 70, "weight_kg": 60}]})
    data.add_notes("1", {"device_name": "Pixel", "items": [{"app": "은행", "package": "com.bank", "title": "입금 50,000원", "text": "홍길동", "at": NOW - 3600}]})
    now[0] += 120
    out = await tools["phone_contacts"].handler({"query": "케이랩"})
    assert "From Pixel, updated 2 minutes ago." in out and "- 김민수: +82 10-2222-3333 (휴대전화) · 팀장 · 케이랩" in out
    assert "엄마" in await tools["phone_contacts"].handler({"query": "5678"})
    out = await tools["phone_messages"].handler({"with": "엄마"})
    assert "- 2026-11-07 18:05 to 엄마 (010-1234-5678): 네 좋아요" in out and "from 엄마 (01012345678): 저녁 7시에 봐요" in out
    assert "택배" not in out
    assert "택배" in await tools["phone_messages"].handler({"days": 60, "query": "택배"})
    assert "only the last 30 days" in await tools["phone_messages"].handler({"days": 60})
    out = await tools["phone_call_log"].handler({})
    assert "missed · 김민수 (+82 10-2222-3333)" in out and "outgoing · 엄마 (010-1234-5678), 2 min 5 s" in out
    assert "[은행] 입금 50,000원: 홍길동" in await tools["phone_notifications"].handler({"app": "은행"})
    out = await tools["phone_health"].handler({"start": "2026-11-01", "end": "2026-11-08", "metrics": ["steps", "sleep", "weight"]})
    assert "- 2026-11-07: 8,123 steps · slept 7 h 10 min" in out and "Not shared (the user did not tick it in the Kelo app): weight." in out
    assert "weight 60" not in out                                                     # only what the user chose to share is shown
    assert same_number("+82 10-1234-5678", "010-1234-5678") and not same_number("010-1234-5678", "010-1234-5679")


async def phone_does(data, owner, ok=True, outcome="sent", error="", seen=None):
    """A fake phone: picks up its tasks and reports."""
    for _ in range(200):
        items = data.claim(owner)
        if items:
            for it in items:
                if seen is not None:
                    seen.append(it)
                data.result(owner, it["id"], {"ok": ok, "outcome": outcome, "error": error})
            return
        await asyncio.sleep(0.01)


async def test_a_text_is_approved_with_the_whole_message_then_sent_by_the_phone(tmp_path):
    data = PhoneData(Store(tmp_path / "s.db"))
    woke = []
    data.on_task = woke.append
    tools = tools_by_name(data)
    send = tools["phone_send_text"]
    assert send.requires_approval and "no phone can send texts" in send.validate({"to": "010-1234-5678", "text": "hi"})
    data.put("1", "contacts", {"items": [MOM, KIM, KIM2]})
    data.put("1", "messages", {"device_name": "Pixel", "items": [], "can_send": False})
    assert send.validate({"to": "엄마", "text": "hi"}) is not None                   # shared, but sending not allowed on the phone
    data.put("1", "messages", {"device_name": "Pixel", "items": [], "can_send": True})
    assert send.validate({"to": "엄마", "text": ""}) == "the text is empty"
    assert "more than one number" in send.validate({"to": "김민", "text": "hi"})
    assert "short numbers" in send.validate({"to": "1234", "text": "hi"})
    assert send.validate({"to": "엄마", "text": "x" * 1700}).startswith("the text is too long")
    text = "엄마, 오늘 저녁 7시에 도착해요. 역 앞에서 기다릴게요. " * 4
    args = {"to": "엄마", "text": text}
    assert send.validate(args) is None
    lines = send.approval_extra("a1", args)["lines"]
    assert lines[0]["value"] == "엄마 · 010-1234-5678" and lines[1] == {"label": "Message", "value": text.strip(), "full": True}
    assert lines[2]["value"] == "Pixel"
    assert (await send.describe(args)).startswith("Text to 엄마 (010-1234-5678): “엄마, 오늘")
    seen = []
    out, _ = await asyncio.gather(send.handler(args), phone_does(data, "1", seen=seen))
    assert out == "Sent the text to 엄마 (010-1234-5678) from Pixel." and woke
    assert seen[0]["kind"] == "sms" and seen[0]["number"] == "010-1234-5678" and seen[0]["text"] == text.strip()
    assert data.claim("1") == []                                                     # handed out once
    out, _ = await asyncio.gather(send.handler(args), phone_does(data, "1", ok=False, error="no signal"))
    assert "could not send the text" in out and "no signal" in out


async def test_a_text_waits_for_an_offline_phone_and_is_dropped_when_messages_are_turned_off(tmp_path):
    data = PhoneData(Store(tmp_path / "s.db"))
    data.put("1", "messages", {"device_name": "Pixel", "items": [], "can_send": True})
    send = tools_by_name(data)["phone_send_text"]

    async def quick_wait(task_id, timeout=0.05):
        return await PhoneData.wait(data, task_id, 0.05)
    data.wait = quick_wait
    out = await send.handler({"to": "010-1234-5678", "text": "곧 도착"})
    assert "waiting for Pixel to pick it up" in out
    data.delete("1", "messages")
    assert data.claim("1") == []                                                     # turned off: the waiting text is not sent


async def test_a_call_only_opens_the_dialer_after_approval(tmp_path):
    data = PhoneData(Store(tmp_path / "s.db"))
    call = tools_by_name(data)["phone_call"]
    assert "dialer turned on" in call.validate({"to": "010-1234-5678"})
    data.put("1", "contacts", {"items": [MOM]})
    data.put("1", "dialer", {"device_name": "Pixel"})
    args = {"to": "엄마", "reason": "저녁 약속 확인"}
    assert call.requires_approval and call.validate(args) is None
    lines = call.approval_extra("a", args)["lines"]
    assert [x["label"] for x in lines] == ["Call", "Why", "On", "Note"] and lines[0]["value"] == "엄마 · 010-1234-5678"
    seen = []
    out, _ = await asyncio.gather(call.handler(args), phone_does(data, "1", outcome="notified", seen=seen))
    assert seen[0]["kind"] == "dial" and "text" not in seen[0]
    assert "showed a notification to open the dialer" in out and "places the call themselves" in out


async def test_phone_calendar_changes_are_approved_and_done_on_the_phone(tmp_path):
    st = Store(tmp_path / "s.db")
    cals, data = PhoneCalendars(st), PhoneData(st)
    tools = tools_by_name(data, cals)
    add, upd, rm = tools["phone_calendar_add"], tools["phone_calendar_update"], tools["phone_calendar_delete"]
    dentist = {"id": 41, "title": "치과", "start": ts("2026-11-09T15:00"), "end": ts("2026-11-09T16:00"), "calendar": "개인"}
    cals.put("1", {"device_name": "Pixel", "events": [dentist]})
    assert all(t.requires_approval for t in (add, upd, rm))
    assert "allows Kelo to change" in add.validate({"title": "회의", "start": "2026-11-10T10:00"})   # read-only by default
    assert "no such event" in rm.validate({"event_id": 41})
    cals.put("1", {"device_name": "Pixel", "events": [dentist], "can_write": True, "write_calendar": {"id": 3, "name": "개인"}})
    args = {"title": "팀 회의", "start": "2026-11-10T10:00", "location": "3층"}
    assert add.validate(args) is None
    assert await add.describe(args) == "Add to the phone calendar: 팀 회의 · 2026-11-10 10:00–11:00"
    assert [x["value"] for x in add.approval_extra("a", args)["lines"]] == ["팀 회의", "2026-11-10 10:00–11:00", "3층", "개인 · Pixel"]
    seen = []
    out, _ = await asyncio.gather(add.handler(args), phone_does(data, "1", outcome="done", seen=seen))
    assert out.startswith("Done on the phone: added '팀 회의'") and seen[0]["calendar_id"] == 3 and seen[0]["start"] == ts("2026-11-10T10:00")
    out, _ = await asyncio.gather(add.handler({"title": "제주", "start": "2026-11-20", "end": "2026-11-22"}),
                                  phone_does(data, "1", outcome="done", seen=seen))
    assert seen[1]["all_day"] and seen[1]["day"] == "2026-11-20" and seen[1]["last_day"] == "2026-11-22"
    move = {"event_id": 41, "start": "2026-11-09T17:00"}
    assert upd.validate(move) is None and upd.validate({"event_id": 41}) is not None
    assert [x["value"] for x in upd.approval_extra("a", move)["lines"]] == ["치과", "2026-11-09 15:00–16:00", "2026-11-09 17:00–18:00"]
    out, _ = await asyncio.gather(upd.handler(move), phone_does(data, "1", outcome="done", seen=seen))
    assert seen[2]["event_id"] == 41 and seen[2]["end"] - seen[2]["start"] == 3600 and "title" not in seen[2]
    assert await rm.describe({"event_id": 41}) == "Delete from the phone calendar: 치과 · 2026-11-09 15:00–16:00"
    out, _ = await asyncio.gather(rm.handler({"event_id": 41}), phone_does(data, "1", ok=False, outcome="", error="read-only calendar"))
    assert "could not do it" in out and "read-only calendar" in out


async def test_the_phone_claims_its_tasks_and_reports_over_http(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, r = await add_phone(c, "Pixel")
        phone, dev = bearer(r.json()["token"]), str(r.json()["device_id"])
        _, r2 = await add_phone(c, "Tab")
        other = bearer(r2.json()["token"])
        await c.put("/api/phone/messages", json={"items": [], "can_send": True, "device_name": "Pixel"}, headers=phone)
        st = Store(tmp_path / "s.db")
        pd = PhoneData(st)
        task_id = pd.queue(dev, "sms", {"number": "010-1234-5678", "name": "", "text": "hi"})
        assert (await c.post("/api/phone/tasks/claim", headers=other)).json()["items"] == []     # only for the phone it was queued for
        items = (await c.post("/api/phone/tasks/claim", headers=phone)).json()["items"]
        assert [(i["id"], i["kind"], i["text"]) for i in items] == [(task_id, "sms", "hi")]
        assert (await c.post(f"/api/phone/tasks/{task_id}/result", json={"ok": True}, headers=other)).status_code == 404
        assert (await c.post(f"/api/phone/tasks/{task_id}/result", json={"ok": True, "outcome": "sent"}, headers=phone)).json() == {"ok": True}
        assert pd.task(task_id)["state"] == "done"

