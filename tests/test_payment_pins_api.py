"""The payment PIN and payment-step endpoints: only the phone's ciphertext is stored, a PIN is released once for a waiting payment and
never shows up in an answer or the vault log, the fill-in card is answered by saving, and a payment-step card is listed, answered and
announced with an urgent notification."""

import json

import httpx

from assistant.server import Settings, create_app

from test_server import AUTH, TOKEN, Running

PIN = "582047"
E2E = {"blob": "QUJDREVGR0hJSktM", "alg": "A256GCM-AKS-1", "key_id": "android-1"}


def app_for(tmp_path):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"),
                               home_dir=str(tmp_path / "home")), tools=[])


async def test_pins_are_ciphertext_only_and_released_once(tmp_path):
    app = app_for(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/payment-pins")).status_code == 401
        c.headers.update(AUTH)
        assert (await c.post("/api/payment-pins", json={"site": "mugshop.example", "digits": 6, "pin": PIN, "e2e": E2E})).status_code == 422
        assert (await c.post("/api/payment-pins", json={"site": "mugshop.example", "digits": 3, "e2e": E2E})).status_code == 422
        req = app.state.secret_requests.create("payment_pin", name="mugshop.example", label="머그 가게", hosts=["mugshop.example"])
        r = await c.post("/api/payment-pins", json={"site": "https://www.mugshop.example/pay", "label": "머그 가게 결제", "digits": 6,
                                                    "e2e": E2E, "request_id": req["id"]})
        assert r.status_code == 200, r.text
        pin = r.json()["pin"]
        assert pin["site"] == "mugshop.example" and pin["digits"] == 6 and "blob" not in pin
        assert app.state.secret_requests.get(req["id"])["state"] == "saved"
        assert (await c.get("/api/payment-pins")).json()["items"][0]["id"] == pin["id"]
        assert (await c.get(f"/api/payment-pins/{pin['id']}/blob")).json()["blob"] == E2E["blob"]
        assert (await c.patch(f"/api/payment-pins/{pin['id']}", json={"label": "생활 결제"})).json()["label"] == "생활 결제"
        assert (await c.post("/api/payment-pins/deliveries/ap_none", json={"pin": PIN})).status_code == 404
        app.state.pin_deliveries.expect("ap_1", pin, "www.mugshop.example", {"amount_text": "24,000원"})
        waiting = (await c.get("/api/payment-pins/deliveries")).json()["items"]
        assert waiting[0]["slot_id"] == "ap_1" and waiting[0]["pin"]["digits"] == 6
        assert (await c.put("/api/vault/lock", json={"locked": True})).status_code == 200
        assert (await c.post("/api/payment-pins/deliveries/ap_1", json={"pin": PIN})).status_code == 423
        await c.put("/api/vault/lock", json={"locked": False})
        assert (await c.post("/api/payment-pins/deliveries/ap_1", json={"pin_id": pin["id"], "pin": PIN})).json() == {"ok": True}
        assert (await c.post("/api/payment-pins/deliveries/ap_1", json={"pin": PIN})).status_code == 409
        log = (await c.get("/api/vault/log")).json()
        assert "pin-released" in [x["action"] for x in log] and PIN not in json.dumps(log)
        assert (await c.delete(f"/api/payment-pins/{pin['id']}")).json() == {"ok": True}
        assert (await c.get("/api/payment-pins")).json()["items"] == []


async def test_payment_step_cards_are_listed_answered_and_urgent(tmp_path):
    app = app_for(tmp_path)
    steps = app.state.payment_steps
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        ev = steps.open({"kind": "app", "site": "mugshop.example", "code": "482913", "instruction": "카드 앱에서 승인해 주세요",
                         "task_id": None, "tab": "owner"})
        items = (await c.get("/api/payment-steps")).json()["items"]
        assert items[0]["step_id"] == ev["step_id"] and items[0]["code"] == "482913" and items[0]["state"] == "waiting"
        notes = (await c.get("/api/notifications")).json()["items"]
        note = next(n for n in notes if n["approval_id"] == ev["step_id"])
        assert note["urgent"] is True and "482913" not in json.dumps(note)
        assert (await c.post(f"/api/payment-steps/{ev['step_id']}", json={"answer": "pay"})).status_code == 422
        assert (await c.post(f"/api/payment-steps/{ev['step_id']}", json={"answer": "approved"})).json()["ok"]
        assert (await steps.next_answer(ev["step_id"], 1))["answer"] == "approved"
        steps.close(ev["step_id"], "approved")
        assert (await c.post(f"/api/payment-steps/{ev['step_id']}", json={"answer": "cancel"})).status_code == 404
        assert (await c.get(f"/api/payment-steps/{ev['step_id']}/pad")).status_code == 404
