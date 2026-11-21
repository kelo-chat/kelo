"""iPhone Live Activities (live_activity.py, backlog 64b): the sealed step words, the state of a turn, the follow loop, the APNs
"liveactivity" push, the broker's /broker/push/live and the phone's API. Against a fake Apple and a fake sender; nothing reaches a
real service. The words must never be readable by Apple or the broker: only the phone's key opens them."""

import asyncio
import base64
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from assistant import push as push_mod
from assistant.live_activity import LiveActivities, seal, state_of, steps_of
from assistant.netguard import NetGuardError, Reply
from assistant.providers import Done, TextDelta
from assistant.push import Apns, Fcm, PushBroker, PushClient, PushError, check_live
from assistant.store import Store
from assistant.turn_engine import Turn

from test_server import Running
from test_push import apns_env, make_app, pair, FakeGoogle

BROKER = "https://broker.example.org"
TOKEN = "ab" * 32                       # an APNs push token of an activity (64 hex)
IPHONE_A = "a1" * 32
KEY = os.urandom(32)                    # the phone's key for one activity
KEY_B64 = base64.b64encode(KEY).decode()


def opened(key: bytes, content: dict) -> dict:
    """What the phone's widget extension does: open the sealed state with the activity's key."""
    raw = base64.b64decode(content["sealed"])
    return json.loads(ChaCha20Poly1305(key).decrypt(raw[:12], raw[12:], b"kelo-live v1"))


def fake_turn(status_text="", events=None, waiting=0):
    return SimpleNamespace(id="turn-1", status_text=status_text, events=list(events or []), ended=asyncio.Event(),
                           final_status="", waiting=waiting)


async def until(cond, tries=300):
    for _ in range(tries):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


class Sender:
    """Stands for PushClient.live: records every update and answers with `reply`."""

    def __init__(self):
        self.calls: list[dict] = []
        self.reply = {"ok": True, "invalid": False}

    async def __call__(self, token, env, content, event, ts):
        self.calls.append({"token": token, "env": env, "content": content, "event": event, "ts": ts})
        return self.reply


class FakeLiveApple:
    """api.push.apple.com and api.sandbox.push.apple.com (HTTP/2 POST /3/device/<token>): records each push, answers 200 (or 410
    for a token in `gone`). Other hosts are refused."""

    def __init__(self):
        self.sent: list[dict] = []
        self.gone: set[str] = set()

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None, **kw):
        parts = urlsplit(url)
        if parts.hostname not in allowed_hosts or parts.hostname not in (push_mod.APNS_HOST, push_mod.APNS_SANDBOX_HOST):
            raise NetGuardError(f"{parts.hostname} is not a registered host")
        assert kw.get("http2") is True and method == "POST" and parts.path.startswith("/3/device/")
        token = parts.path.rsplit("/", 1)[1]
        self.sent.append({"host": parts.hostname, "token": token, "headers": headers or {}, "body": json.loads(content)})
        if token in self.gone:
            return Reply(410, {}, json.dumps({"reason": "Unregistered"}).encode(), False)
        return Reply(200, {"apns-id": "1"}, b"", False)


# ---------------------------------------------------------------------------------------------------------------- sealing
def test_the_words_are_sealed_and_open_only_with_the_phones_key():
    key = os.urandom(32)
    plain = {"step": "사이트 여는 중", "site": "bank.example", "state": "working"}
    sealed = seal(key, plain)
    raw = base64.b64decode(sealed)
    assert b"bank.example" not in raw and "사이트".encode() not in raw
    assert json.loads(ChaCha20Poly1305(key).decrypt(raw[:12], raw[12:], b"kelo-live v1")) == plain
    with pytest.raises(InvalidTag):
        ChaCha20Poly1305(os.urandom(32)).decrypt(raw[:12], raw[12:], b"kelo-live v1")      # another key
    with pytest.raises(InvalidTag):
        ChaCha20Poly1305(key).decrypt(raw[:12], raw[12:], b"kelo-live v2")                 # another associated data
    assert seal(key, plain) != sealed                                                       # a fresh nonce every time


# ---------------------------------------------------------------------------------------------------------------- the turn's state
def test_the_lock_screen_state_follows_a_turn_from_working_to_waiting_to_done():
    t = fake_turn(status_text="사이트 여는 중")
    assert state_of(t) == {"step": "사이트 여는 중", "site": "", "state": "working"}
    t.waiting = 1
    assert state_of(t)["state"] == "waiting"
    t.waiting = 0
    t.final_status = "done"
    t.ended.set()
    assert state_of(t) == {"step": "사이트 여는 중", "site": "", "state": "done"}


def test_a_turn_that_failed_or_was_stopped_says_so():
    failed = fake_turn()
    failed.final_status = "failed"
    failed.ended.set()
    stopped = fake_turn()
    stopped.final_status = "stopped"
    stopped.ended.set()
    assert state_of(failed)["state"] == "failed"
    assert state_of(stopped)["state"] == "stopped"


def test_the_site_is_the_newest_browser_picture_and_the_step_is_cut_short():
    events = [{"type": "browser", "host": "a.example"}, {"type": "text"}, {"type": "browser", "host": "b.example"}, {"type": "browser"}]
    assert state_of(fake_turn(events=events))["site"] == "b.example"
    assert state_of(fake_turn(status_text="x" * 200))["step"] == "x" * 80


def test_hidden_tool_calls_are_not_counted_as_steps():
    t = fake_turn(events=[{"type": "tool_started"}, {"type": "tool_started", "hidden": True}, {"type": "text"}, {"type": "tool_started"}])
    assert steps_of(t) == 2


def test_the_state_reads_a_real_turn():
    t = Turn()
    t.status_text = "정리하는 중"
    t.events.append({"type": "tool_started"})
    assert state_of(t) == {"step": "정리하는 중", "site": "", "state": "working"}
    assert steps_of(t) == 1


# ---------------------------------------------------------------------------------------------------------------- the follow loop
async def test_an_activity_gets_an_update_at_once_then_only_on_a_change_then_an_end():
    sender = Sender()
    la = LiveActivities(sender, every=0.01)
    turn = fake_turn(status_text="생각하는 중")
    try:
        la.register(turn, TOKEN.upper(), "production", KEY_B64)
        assert await until(lambda: len(sender.calls) == 1)
        first = sender.calls[0]
        assert first["event"] == "update" and first["token"] == TOKEN and first["env"] == "production"
        assert set(first["content"]) == {"v", "n", "sealed"} and first["content"]["n"] == 0
        assert opened(KEY, first["content"]) == {"step": "생각하는 중", "site": "", "state": "working"}
        await asyncio.sleep(0.1)
        assert len(sender.calls) == 1                                    # the same state again: nothing new
        turn.status_text = "사이트 여는 중"
        assert await until(lambda: len(sender.calls) == 2)
        assert opened(KEY, sender.calls[1]["content"])["step"] == "사이트 여는 중"
        turn.final_status = "done"
        turn.ended.set()
        assert await until(lambda: len(sender.calls) == 3)
        end = sender.calls[2]
        assert end["event"] == "end" and opened(KEY, end["content"])["state"] == "done"
        assert await until(lambda: not la.items)                         # the activity is gone afterwards
    finally:
        await la.close()


async def test_a_reply_on_the_lock_screen_counts_as_read_so_it_is_not_stopped_while_the_app_sleeps():
    sender = Sender()
    la = LiveActivities(sender, every=0.01)
    turn = fake_turn(status_text="사이트 여는 중")
    turn.touched = 0.0
    try:
        la.register(turn, TOKEN, "production", KEY_B64)
        assert await until(lambda: turn.touched > 0)                     # TurnRegistry.sweep sees a reader
    finally:
        await la.close()


async def test_a_token_apple_calls_gone_stops_the_activity_at_once():
    sender = Sender()
    sender.reply = {"ok": False, "invalid": True, "error": "Unregistered"}
    la = LiveActivities(sender, every=0.01)
    try:
        la.register(fake_turn(status_text="x"), TOKEN, "production", KEY_B64)
        assert await until(lambda: not la.items)
        await asyncio.sleep(0.05)
        assert len(sender.calls) == 1
    finally:
        await la.close()


async def test_a_bad_token_or_key_is_refused_before_anything_starts():
    la = LiveActivities(Sender(), every=0.01)
    turn = fake_turn()
    bad = [("zz" * 32, KEY_B64), ("ab" * 10, KEY_B64), (TOKEN, base64.b64encode(os.urandom(16)).decode()),
           (TOKEN, "!!not base64!!"), (TOKEN, "")]
    for token, key in bad:
        with pytest.raises(ValueError):
            la.register(turn, token, "production", key)
    assert la.items == {}


# ---------------------------------------------------------------------------------------------------------------- the push's checks
def test_a_live_update_carries_only_a_step_count_and_the_sealed_words():
    good = {"v": 1, "n": 2, "sealed": "AAECAw=="}
    assert check_live(good, "update") == (good, "update")
    assert check_live(good, "end") == (good, "end")
    bad = [{**good, "step": "사이트 열기"}, {"v": 2, "n": 2, "sealed": "AAECAw=="}, {"v": 1, "n": True, "sealed": "AA=="},
           {"v": 1, "n": 2, "sealed": "not base64!"}, {"v": 1, "n": 2}]
    for content in bad:
        with pytest.raises(PushError):
            check_live(content, "update")
    with pytest.raises(PushError):
        check_live(good, "start")


# ---------------------------------------------------------------------------------------------------------------- Apple
async def test_apple_gets_a_live_update_and_an_end_on_the_liveactivity_topic_with_the_sealed_state(tmp_path):
    apple = FakeLiveApple()
    apns = Apns(fetcher=apple.fetch, env=lambda: apns_env(tmp_path), backoff=0.0, clock=lambda: 1000.0)
    content = {"v": 1, "n": 2, "sealed": "AAECAw=="}
    assert await apns.send_live(TOKEN, "production", content, "update", 1000) == {"ok": True, "invalid": False}
    assert await apns.send_live(TOKEN, "development", content, "end", 1000) == {"ok": True, "invalid": False}
    upd, end = apple.sent
    assert upd["host"] == "api.push.apple.com" and end["host"] == "api.sandbox.push.apple.com"
    for m in (upd, end):
        assert m["headers"]["apns-push-type"] == "liveactivity"
        assert m["headers"]["apns-topic"] == "chat.kelo.app.push-type.liveactivity"
        assert "apns-collapse-id" not in m["headers"]
    assert upd["headers"]["apns-priority"] == "5" and end["headers"]["apns-priority"] == "10"
    assert upd["body"] == {"aps": {"timestamp": 1000, "event": "update", "content-state": content, "stale-date": 1300}}
    assert end["body"] == {"aps": {"timestamp": 1000, "event": "end", "content-state": content, "dismissal-date": 1600}}


async def test_an_alert_still_carries_its_collapse_id_and_the_alert_headers(tmp_path):
    apple = FakeLiveApple()
    apns = Apns(fetcher=apple.fetch, env=lambda: apns_env(tmp_path), backoff=0.0)
    await apns.send([(IPHONE_A, "production")], {"type": "task_done", "id": "5"})
    h = apple.sent[0]["headers"]
    assert h["apns-push-type"] == "alert" and h["apns-priority"] == "10" and h["apns-collapse-id"] == "kelo-5"
    assert h["apns-topic"] == "chat.kelo.app"


# ---------------------------------------------------------------------------------------------------------------- the broker
async def test_the_broker_passes_one_live_update_to_apple_and_refuses_anything_else(tmp_path):
    apple = FakeLiveApple()
    st = Store(tmp_path / "op.db")
    apns = Apns(fetcher=apple.fetch, env=lambda: apns_env(tmp_path), backoff=0.0, clock=lambda: 1000.0)
    broker = PushBroker(st, Fcm(st, fetcher=apple.fetch), apns, wall=lambda: 1000.0)
    content = {"v": 1, "n": 2, "sealed": "AAECAw=="}
    body = {"install_id": "I" * 40, "token": TOKEN, "environment": "production", "content": content, "event": "update", "timestamp": 1000}
    assert await broker.handle_live(body, "1.2.3.4") == {"ok": True, "invalid": False}
    assert apple.sent[0]["headers"]["apns-push-type"] == "liveactivity"
    refused = [{**body, "content": {**content, "step": "잔액 1,000원"}}, {**body, "token": "nope"}, {**body, "environment": "staging"},
               {**body, "event": "start"}, {**body, "timestamp": 1000 - 2 * 86400}, {**body, "timestamp": "1000"},
               {**body, "install_id": "short"}]
    for bad in refused:
        with pytest.raises(PushError) as e:
            await broker.handle_live(bad, "1.2.3.4")
        assert e.value.status in (403, 422) and "잔액" not in str(e.value)
    with pytest.raises(PushError) as e:
        await broker.handle_live("not an object", "1.2.3.4")
    assert e.value.status == 422
    assert len(apple.sent) == 1                                          # nothing refused reached Apple


async def test_a_broker_without_an_apns_key_answers_404_for_a_live_update(tmp_path):
    st = Store(tmp_path / "op.db")
    broker = PushBroker(st, Fcm(st), None, wall=lambda: 1000.0)
    body = {"install_id": "I" * 40, "token": TOKEN, "environment": "production", "content": {"v": 1, "n": 0, "sealed": "AA=="},
            "event": "update", "timestamp": 1000}
    with pytest.raises(PushError) as e:
        await broker.handle_live(body, "1.2.3.4")
    assert e.value.status == 404


async def test_a_copy_without_the_key_sends_its_update_through_the_broker_and_gives_up_quietly(tmp_path):
    calls: list[tuple[str, dict]] = []

    async def fetch(method, url, *, allowed_hosts, headers=None, content=None, **kw):
        calls.append((url, json.loads(content)))
        if len(calls) == 1:
            return Reply(200, {}, json.dumps({"ok": True, "invalid": False}).encode(), False)
        if len(calls) == 2:
            return Reply(503, {}, b"", False)
        raise NetGuardError("broker not reachable")

    st = Store(tmp_path / "friend.db")
    client = PushClient(st, Fcm(st), None, broker_url=BROKER, fetcher=fetch)
    content = {"v": 1, "n": 1, "sealed": "AAECAw=="}
    assert await client.live(TOKEN, "production", content, "update", 1000) == {"ok": True, "invalid": False}
    url, sent = calls[0]
    assert url == f"{BROKER}/broker/push/live"
    assert set(sent) == {"install_id", "token", "environment", "content", "event", "timestamp"} and sent["content"] == content
    assert await client.live(TOKEN, "production", content, "update", 1000) == {"ok": False, "invalid": False}   # 503: one try only
    assert await client.live(TOKEN, "production", content, "update", 1000) == {"ok": False, "invalid": False}   # unreachable
    assert len(calls) == 3


async def test_a_copy_with_no_way_to_send_answers_quietly(tmp_path):
    st = Store(tmp_path / "solo.db")
    client = PushClient(st, Fcm(st), None)
    assert await client.live(TOKEN, "production", {"v": 1, "n": 0, "sealed": "AA=="}, "update", 1000) == {"ok": False, "invalid": False}


# ---------------------------------------------------------------------------------------------------------------- the phone's API
async def test_the_phone_starts_and_ends_its_live_activity_for_a_running_reply(tmp_path, monkeypatch):
    for k, v in apns_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    gate = asyncio.Event()

    async def slow(provider, request, **kw):
        await gate.wait()
        yield TextDelta("다 했어요.")
        yield Done("stop")

    apple = FakeLiveApple()
    app = make_app(tmp_path, FakeGoogle(), stream_fn=slow, fetcher=apple.fetch)
    try:
        async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
            _, phone = await pair(c, "iPhone")
            await c.post("/api/push/register", headers=phone, json={"token": IPHONE_A, "platform": "ios-apns"})
            turn_id = (await c.post("/api/turns", headers=phone, json={"text": "정리해줘"})).json()["id"]
            body = {"turn_id": turn_id, "token": IPHONE_A, "environment": "production", "key": KEY_B64}
            app.state.apns.env = lambda: {}                                           # this server cannot send iPhone pushes
            assert (await c.post("/api/live-activity", headers=phone, json=body)).status_code == 404
            app.state.apns.env = lambda: apns_env(tmp_path)
            assert (await c.post("/api/live-activity", headers=phone, json={**body, "turn_id": "nope"})).status_code == 404
            short = base64.b64encode(b"short").decode()
            assert (await c.post("/api/live-activity", headers=phone, json={**body, "key": short})).status_code == 422
            assert (await c.post("/api/live-activity", headers=phone, json={**body, "token": "zz"})).status_code == 422
            assert (await c.post("/api/live-activity", headers=phone, json=body)).json() == {"ok": True}
            assert await until(lambda: apple.sent)
            first = apple.sent[0]
            assert first["token"] == IPHONE_A and first["headers"]["apns-push-type"] == "liveactivity"
            assert opened(KEY, first["body"]["aps"]["content-state"])["state"] == "working"
            assert "정리" not in json.dumps(apple.sent, ensure_ascii=False)
            assert (await c.delete(f"/api/live-activity/{turn_id}", headers=phone)).json() == {"ok": True}
            assert app.state.appctx.live_activities.items == {}
    finally:
        gate.set()
