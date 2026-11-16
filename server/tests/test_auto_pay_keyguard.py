"""Auto-pay is allowed only when Keyguard is on (owner's rule, 2026-11-10): turning it on needs Keyguard (409 keyguard_required),
existing auto-pay cards ask on the phone while Keyguard is off (keyguard_off) and keep their sealed copy, and come back when it is on."""

import base64
import os

import httpx
import pytest

from assistant import keyguard as kg_mod
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.wallet import EXPLAIN, Wallet

from test_server import AUTH, TOKEN, Running
from test_wallet import VISA, CVC, future

pytestmark = pytest.mark.keyguard_real
BLOB = base64.b64encode(b"\x01" + os.urandom(60)).decode()


@pytest.fixture(autouse=True)
def quick_argon(monkeypatch):
    monkeypatch.setattr(kg_mod, "ARGON", {"iterations": 1, "lanes": 1, "memory_cost": 8 * 1024})


def card_body(**kw):
    m, y = future()
    return {"label": "Visa", "exp_month": m, "exp_year": y, "holder": "HONG GILDONG", "auto_pay": True, "number": VISA, "cvc": CVC, **kw}


def keyguard_on(store):
    got = store.keyguard.start_enroll(1)
    store.keyguard.finish_enroll(store, 1, got["enrollment_id"], 1, BLOB)
    return got


async def test_turning_auto_pay_on_needs_keyguard(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")), tools=[])
    store = app.state.secret_requests.store
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        r = await c.post("/api/wallet/cards", json=card_body())
        assert r.status_code == 409 and r.json()["code"] == "keyguard_required" and "Keyguard" in r.json()["detail"]["message"]
        e2e = (await c.post("/api/wallet/cards", json=card_body(auto_pay=False, number=None, cvc=None, last4="4242",
                                                                e2e={"blob": base64.b64encode(b"x" * 32).decode()}))).json()
        r = await c.patch(f"/api/wallet/cards/{e2e['id']}", json={"auto_pay": True, "number": VISA, "cvc": CVC})
        assert r.status_code == 409 and r.json()["code"] == "keyguard_required"
        keyguard_on(store)
        r = await c.post("/api/wallet/cards", json=card_body())
        assert r.status_code == 201 and r.json()["pays_automatically"] and r.json()["auto_pay_paused_reason"] == ""


async def test_auto_pay_cards_wait_while_keyguard_is_off_and_resume_when_on(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")), tools=[])
    store = app.state.secret_requests.store
    keyguard_on(store)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH, timeout=30) as c:
        card = (await c.post("/api/wallet/cards", json=card_body())).json()
        off = (await c.post("/api/keyguard/disable", headers=AUTH)).json()   # allowed; the answer says what now waits
        assert off["mode"] == "server" and off["auto_pay_paused"] == 1
        assert (await c.get("/api/keyguard")).json()["auto_pay_paused"] == 1
        got = (await c.get(f"/api/wallet/cards/{card['id']}")).json()
        assert got["auto_pay"] and got["server_copy"] and not got["pays_automatically"]   # the sealed copy is kept
        assert got["ask_reason"] == "keyguard_off" and got["auto_pay_paused_reason"] == "keyguard_off"
        assert "Keyguard" in got["ask_reason_text"]
        assert (await c.get("/api/wallet")).json()["auto_pay_paused"] == 1
        wallet = Wallet(store)
        d = wallet.decide(wallet.card(card["id"]), "shop.example.com", 1000, "KRW")
        assert (d.auto, d.reason) == (False, "keyguard_off") and "Keyguard" in EXPLAIN["keyguard_off"]
        assert wallet.open_secret(card["id"]) is not None
        keyguard_on(store)                                                # on again: the card pays automatically as before
        got = (await c.get(f"/api/wallet/cards/{card['id']}")).json()
        assert got["pays_automatically"] and got["auto_pay_paused_reason"] == "" and (await c.get("/api/wallet")).json()["auto_pay_paused"] == 0
        assert (await c.get("/api/keyguard")).json()["auto_pay_paused"] == 0


def test_the_disable_answer_counts_the_waiting_cards(tmp_path):
    store = Store(tmp_path / "s.db")
    keyguard_on(store)
    wallet = Wallet(store)
    wallet.add_card(card_body())
    store.keyguard.disable()
    assert wallet.auto_pay_paused() == 1
