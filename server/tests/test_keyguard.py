"""Keyguard (keyguard.py, spec section 2): the master key of the SECRETS (vault, provider keys, connector tokens, auto-pay cards)
held by the owner's phones. Personal data is not under it and stays readable while locked."""

import asyncio
import base64
import json
import os

import httpx
import pytest

from assistant import keyguard as kg_mod
from assistant.keyguard import KeyGuard, KeyGuardError, Locked
from assistant.phone_calendar import PhoneCalendars
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.wallet import Wallet

from e2e_client import E2EClient
from test_e2e_wire import call
from test_phone_calendar import DENTIST, snapshot
from test_server import AUTH, TOKEN, Running
from test_wallet import auto_card

BLOB = base64.b64encode(b"\x01" + os.urandom(60)).decode()   # what an Android phone sends back (the server never opens it)
BLOB2 = base64.b64encode(b"\x02" + os.urandom(90)).decode()


@pytest.fixture(autouse=True)
def quick_argon(monkeypatch):
    monkeypatch.setattr(kg_mod, "ARGON", {"iterations": 1, "lanes": 1, "memory_cost": 8 * 1024})


def filled(tmp_path):
    """A server's store with one of each secret and some personal data."""
    store = Store(tmp_path / "s.db")
    store.set_key("openai", "sk-provider-secret")
    store.save_credential("shop", "Shop", ["shop.example.com"], {}, kind="login")
    store.set_login("shop", "me@example.com", "pw-secret")
    wallet = Wallet(store)
    card = auto_card(wallet)
    PhoneCalendars(store).put("1", snapshot(DENTIST))
    return store, wallet, card


def enable(store, device_id=1, blob=BLOB):
    got = store.keyguard.start_enroll(device_id)
    store.keyguard.finish_enroll(store, device_id, got["enrollment_id"], 1 if blob == BLOB else 2, blob)
    return got


def test_turning_it_on_reseals_the_secrets_and_takes_the_key_off_the_disk(tmp_path):
    store, wallet, card = filled(tmp_path)
    old = (tmp_path / "master.key").read_bytes().strip()
    got = enable(store)
    assert got["enabling"] and len(got["recovery_code"]) == 24 and got["key"] != old.decode()
    assert not (tmp_path / "master.key").exists() and (tmp_path / "data.key").exists()
    state = json.loads((tmp_path / "keyguard.json").read_text())
    assert state["mode"] == "phone" and state["phones"][0]["blob"] == BLOB
    assert got["key"] not in (tmp_path / "keyguard.json").read_text() and got["recovery_code"] not in (tmp_path / "keyguard.json").read_text()
    # still unlocked in this process: every secret opens with the new key
    assert store.key("openai") == "sk-provider-secret" and store.credential("shop")["secret"] == "pw-secret"
    assert wallet.open_secret(card["id"]) is not None
    assert PhoneCalendars(store).events(0, 2e9)[0][0]["title"] == "치과 예약"


def test_after_a_restart_secrets_wait_for_the_phone_and_personal_data_does_not(tmp_path):
    store, _, card = filled(tmp_path)
    got = enable(store)
    store2 = Store(tmp_path / "s.db")                              # the server restarted
    assert store2.keyguard.locked and store2.keyguard.mode == "phone"
    with pytest.raises(Locked):
        store2.key("openai")
    with pytest.raises(Locked):
        Wallet(store2).open_secret(card["id"])
    assert [c["name"] for c in store2.credentials()] == ["shop"]    # the list still shows (without the user name hint)
    assert PhoneCalendars(store2).events(0, 2e9)[0][0]["title"] == "치과 예약"   # personal data: not under Keyguard
    with pytest.raises(KeyGuardError):
        store2.keyguard.unlock(base64.urlsafe_b64encode(os.urandom(32)).decode())   # a wrong key is told apart
    assert store2.keyguard.blob_for(1) == {"alg": 1, "blob": BLOB}
    store2.keyguard.unlock(got["key"])                             # the phone unwrapped its copy and sent the key
    assert store2.key("openai") == "sk-provider-secret" and store2.credential("shop")["secret"] == "pw-secret"


def test_the_recovery_code_opens_it_without_a_phone(tmp_path):
    store, _, _ = filled(tmp_path)
    got = enable(store)
    store2 = Store(tmp_path / "s.db")
    with pytest.raises(KeyGuardError):
        store2.keyguard.recover("A" * 24)
    store2.keyguard.recover("-".join(got["recovery_code"][i:i + 4] for i in range(0, 24, 4)).lower())   # dashes, case: fine
    assert store2.key("openai") == "sk-provider-secret"


def test_more_phones_removing_one_and_turning_it_off(tmp_path):
    store, _, _ = filled(tmp_path)
    first = enable(store)
    with pytest.raises(KeyGuardError, match="last phone"):
        store.keyguard.remove_phone(1)
    second = store.keyguard.start_enroll(2)                         # another owner phone, while unlocked: the same key
    assert second["key"] == first["key"] and not second["enabling"] and "recovery_code" not in second
    store.keyguard.finish_enroll(store, 2, second["enrollment_id"], 2, BLOB2)
    with pytest.raises(KeyGuardError):
        store.keyguard.start_enroll(2)                              # already holds it
    assert [p["device_id"] for p in store.keyguard.view()["phones"]] == [1, 2]
    store.keyguard.remove_phone(1)
    for bad in ((3, BLOB), (1, "not base64!"), (1, base64.b64encode(b"\x02" + os.urandom(40)).decode())):
        with pytest.raises(KeyGuardError):
            kg_mod._check_blob(*bad)
    store.keyguard.disable()
    assert (tmp_path / "master.key").read_bytes().strip() == first["key"].encode()
    store2 = Store(tmp_path / "s.db")
    assert not store2.keyguard.locked and store2.key("openai") == "sk-provider-secret"


def test_the_key_in_the_env_must_go_first_and_a_stale_enrollment_is_refused(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    store = Store(tmp_path / "s.db", Fernet.generate_key().decode())
    with pytest.raises(KeyGuardError) as e:
        store.keyguard.start_enroll(1)
    assert e.value.code == "env_key"
    store = Store(tmp_path / "t" / "s.db")
    got = store.keyguard.start_enroll(1)
    with pytest.raises(KeyGuardError):
        store.keyguard.finish_enroll(store, 2, got["enrollment_id"], 1, BLOB)   # not the phone that started it
    monkeypatch.setattr(kg_mod, "ENROLL_SECONDS", -1)
    got = store.keyguard.start_enroll(1)
    with pytest.raises(KeyGuardError):
        store.keyguard.finish_enroll(store, 1, got["enrollment_id"], 1, BLOB)


async def test_the_api_over_ke2e_and_423_while_locked(tmp_path):
    def app():
        return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "state" / "s.db")), tools=[])
    async with Running(app()) as srv, httpx.AsyncClient(base_url=srv.url, timeout=30) as http:
        info = (await http.get("/api/e2e/info")).json()
        client = E2EClient(base64.urlsafe_b64decode(info["pk"] + "=" * (-len(info["pk"]) % 4)))
        code = (await http.post("/api/devices/pair", headers=AUTH, json={"owner": True})).json()["code"]
        head, body, _ = await call(http, client, "POST", "/api/pair", {"content-type": "application/json"},
                                   json.dumps({"code": code, "name": "Owner phone"}).encode())
        phone = {"authorization": f"Bearer {json.loads(body)['token']}", "content-type": "application/json"}
        assert (await http.put("/api/providers/openai/key", headers=AUTH, json={"api_key": "sk-x-123456789"})).status_code == 200
        # key material never in plaintext
        assert (await http.post("/api/keyguard/enroll", headers=AUTH)).json()["code"] == "e2e_required"
        head, body, _ = await call(http, client, "POST", "/api/keyguard/enroll", phone)
        got = json.loads(body)
        assert head["s"] == 200 and got["enabling"] and len(got["recovery_code"]) == 24
        head, body, _ = await call(http, client, "POST", "/api/keyguard/enroll/finish", phone,
                                   json.dumps({"enrollment_id": got["enrollment_id"], "alg": 1, "blob": BLOB}).encode())
        assert head["s"] == 200 and json.loads(body)["mode"] == "phone" and json.loads(body)["phones"][0]["name"] == "Owner phone"
    async with Running(app()) as srv, httpx.AsyncClient(base_url=srv.url, timeout=30) as http:   # restarted: locked
        info = (await http.get("/api/e2e/info")).json()
        client = E2EClient(base64.urlsafe_b64decode(info["pk"] + "=" * (-len(info["pk"]) % 4)))
        r = await http.put("/api/providers/openai/key", headers=AUTH, json={"api_key": "sk-y-123456789"})
        assert r.status_code == 423 and r.json()["code"] == "locked"
        assert (await http.get("/api/agent/status", headers=AUTH)).json()["locked"] is True
        assert (await http.get("/api/keyguard", headers=AUTH)).json()["locked"] is True
        notes = (await http.get("/api/notifications", headers=AUTH)).json()["items"]
        assert any(n["type"] == "keyguard_locked" for n in notes)
        head, body, _ = await call(http, client, "GET", "/api/keyguard/blob", phone)
        assert json.loads(body) == {"alg": 1, "blob": BLOB}
        head, body, _ = await call(http, client, "POST", "/api/keyguard/unlock", phone, json.dumps({"key": got["key"]}).encode())
        assert head["s"] == 200 and json.loads(body)["locked"] is False
        assert (await http.put("/api/providers/openai/key", headers=AUTH, json={"api_key": "sk-y-123456789"})).status_code == 200
        assert (await http.get("/api/agent/status", headers=AUTH)).json()["locked"] is False


def test_the_cli(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from assistant import cli, connect, manage
    from test_connect_cli import make
    got = []
    monkeypatch.setattr(manage, "run_command", lambda args, **k: got.append(args) or 0)
    cli.main(["keyguard", "status"])
    cli.main(["keyguard", "recover", "--code", "ABCD", "--instance", "alice"])
    assert got[0].kcmd == "status" and (got[1].kcmd, got[1].code, got[1].instance) == ("recover", "ABCD", "alice")
    monkeypatch.undo()
    m, _, msgs = make(tmp_path)
    monkeypatch.setattr(connect, "keyguard_status", lambda m, inst: {"mode": "phone", "locked": True, "phones": [{"device_id": 3, "name": "Pixel"}]})
    sent = []
    monkeypatch.setattr(connect, "keyguard_recover", lambda m, inst, code: sent.append(code) or {"ok": True})
    assert manage.run_command(SimpleNamespace(cmd="keyguard", kcmd="status", instance=None), manager=m) == 0
    assert "the phones" in msgs[-2] and "Locked" in msgs[-2] and "Pixel" in msgs[-1]
    assert manage.run_command(SimpleNamespace(cmd="keyguard", kcmd="recover", instance=None, code=None), manager=m,
                              ask=lambda q: " CODE24 ") == 0
    assert sent == ["CODE24"] and "Unlocked" in msgs[-1]


async def test_removing_a_device_takes_its_copy_and_the_last_copy_is_refused(tmp_path):
    def app():
        return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")), tools=[])
    a = app()
    store = a.state.secret_requests.store
    async with Running(a) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as http:
        ids = []
        for name in ("A", "B"):
            code = (await http.post("/api/devices/pair", json={"owner": True})).json()["code"]
            ids.append((await http.post("/api/pair", json={"code": code, "name": name})).json()["device_id"])
        enable(store, ids[0])
        got = store.keyguard.start_enroll(ids[1])
        store.keyguard.finish_enroll(store, ids[1], got["enrollment_id"], 2, BLOB2)
        assert (await http.delete(f"/api/devices/{ids[0]}")).status_code == 200
        assert [p["device_id"] for p in store.keyguard.view()["phones"]] == [ids[1]]
        r = await http.delete(f"/api/devices/{ids[1]}")
        assert r.status_code == 409 and r.json()["code"] == "last_keyguard_phone"
        assert any(d["id"] == ids[1] for d in (await http.get("/api/devices")).json()["devices"])   # still signed in
        assert (await http.delete("/api/devices/9999")).status_code == 404


async def test_a_reply_while_locked_says_so_in_the_users_language(tmp_path):
    from assistant.providers import Done, TextDelta
    store = Store(tmp_path / "s.db")
    store.set_key("openai", "sk-provider-secret")
    enable(store)

    async def model(provider, request, **kw):
        yield TextDelta("never")
        yield Done("stop")
    a = create_app(Settings(token=TOKEN, provider="openai", model="gpt-x", cdp_host=None, db_path=str(tmp_path / "s.db")), tools=[],
                   stream_fn=model)
    from test_server import follow
    async with Running(a) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}, timeout=30) as http:
        tid = (await http.post("/api/turns", json={"text": "안녕"})).json()["id"]
        events = await follow(http, tid)
        text = json.dumps(events, ensure_ascii=False)
        assert "Kelo가 잠겨 있어요" in text and "never" not in text


async def test_a_scheduled_run_waits_for_the_unlock_and_says_it_waited(tmp_path, monkeypatch):
    from assistant.providers import Done, TextDelta
    store = Store(tmp_path / "s.db")
    store.set_key("openai", "sk-provider-secret")
    got = enable(store)
    seen = []

    async def model(provider, request, **kw):
        seen.append(json.dumps(request.messages, ensure_ascii=False))
        yield TextDelta("확인했어요.")
        yield Done("stop")
    a = create_app(Settings(token=TOKEN, provider="openai", model="gpt-x", cdp_host=None, db_path=str(tmp_path / "s.db"),
                            scheduler=False), tools=[], stream_fn=model)
    guard = a.state.secret_requests.store.keyguard
    assert guard.locked
    async with Running(a) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as http:
        sid = (await http.post("/api/schedules", json={"name": "아침 확인", "prompt": "메일 확인", "cron": "0 8 * * *"})).json()["id"]
        r = asyncio.ensure_future(http.post(f"/api/schedules/{sid}/run"))
        await asyncio.sleep(0.5)
        assert not seen                                              # waiting: nothing ran while locked
        guard.unlock(got["key"])
        await r
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.1)
        assert seen and "because Kelo was locked" in seen[-1]


def test_cli_enable_moves_the_env_key_out_safely(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from assistant import connect, manage
    from test_connect_cli import make
    m, _, msgs = make(tmp_path)
    env = tmp_path / ".env"
    env.write_text(env.read_text() + "ASSISTANT_SECRET_KEY=the-secret-key-value\n")
    calls = []
    monkeypatch.setattr(connect, "keyguard_prepare", lambda m, inst: calls.append("prepare") or {"ok": True, "written": True})
    monkeypatch.setattr(m, "running", lambda name=None: ["server"])
    monkeypatch.setattr(m, "run", lambda cmd, **kw: calls.append(cmd))
    assert manage.run_command(SimpleNamespace(cmd="keyguard", kcmd="enable", instance=None, no_restart=False), manager=m) == 0
    assert calls[0] == "prepare" and "up" in calls[1]                 # the server keeps the key first, then restarts
    assert "ASSISTANT_SECRET_KEY" not in env.read_text() and "ASSISTANT_TOKEN" in env.read_text()
    backups = list(tmp_path.glob(".env.bak-keyguard-*"))
    assert len(backups) == 1 and "the-secret-key-value" in backups[0].read_text() and oct(backups[0].stat().st_mode)[-3:] == "600"
    assert not any("the-secret-key-value" in x for x in msgs) and "the-secret-key-value" not in capsys.readouterr().out
    assert "My -> Security -> Keyguard" in msgs[-1]
    # not running: nothing changes
    env.write_text(env.read_text() + "ASSISTANT_SECRET_KEY=again\n")
    monkeypatch.setattr(m, "running", lambda name=None: [])
    assert manage.run_command(SimpleNamespace(cmd="keyguard", kcmd="enable", instance=None, no_restart=False), manager=m) == 1
    assert "ASSISTANT_SECRET_KEY=again" in env.read_text()


async def test_prepare_writes_the_env_key_into_master_key(tmp_path):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    a = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), secret_key=key), tools=[])
    a.state.secret_requests.store.set_key("openai", "sk-1")
    async with Running(a) as srv, httpx.AsyncClient(base_url=srv.url, timeout=30) as http:
        r = (await http.post("/api/keyguard/prepare", headers=AUTH)).json()
        assert r == {"ok": True, "written": True, "env_key": True} and key not in json.dumps(r)
    assert (tmp_path / "master.key").read_text() == key
    assert Store(tmp_path / "s.db").key("openai") == "sk-1"           # without the env var now: the same key from master.key
