"""There are no owner devices: every signed-in device has the same full rights. The one exception is a review copy (ASSISTANT_REVIEW_MODE),
whose reviewers (access-code devices) never reach operator settings or Keyguard (the wallet works as in the product)."""

import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from assistant import connect, manage
from assistant.store import Store

from test_connect_cli import Resp, make
from test_devices import add_phone, app_for, bearer
from test_server import AUTH, Running

FORMER_OWNER_ONLY = ("/api/operator/firebase", "/api/operator/cloudflare", "/api/operator/beta", "/api/operator/reports",
                     "/api/operator/status", "/api/oauth-apps/google", "/api/oauth-apps/github", "/api/keyguard",
                     "/api/server-update", "/api/storage/cleanup")


async def pair(c, name, **body):
    r = await c.post("/api/devices/pair", headers=AUTH, json=body)
    assert r.status_code == 200
    made = (await c.post("/api/pair", json={"code": r.json()["code"], "name": name})).json()
    return r.json(), made["device_id"], made["token"]


async def reviewer(c, st_path):
    st = Store(st_path)
    _, code = st.new_access_code("review-apple", uses=3)
    st._db.close()
    made = (await c.post("/api/pair", json={"code": code, "name": "reviewer"})).json()
    return made["device_id"], made["token"]


async def test_every_device_passes_the_former_owner_checks_flagged_or_not(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        start, own_id, own = await pair(c, "mine", owner=True)       # the old fields are accepted and ignored
        _, kid_id, kid = await pair(c, "kid")
        assert start["owner"] is True and (await pair(c, "x", first_owner=True))[0]["owner"] is True
        for path in FORMER_OWNER_ONLY:
            for tok in (own, kid):
                assert (await c.get(path, headers=bearer(tok))).status_code == 200, path
            assert (await c.get(path, headers=AUTH)).status_code == 200, path
        listing = (await c.get("/api/devices", headers=bearer(kid))).json()
        assert {d["id"]: d["owner"] for d in listing["devices"]} == {own_id: True, kid_id: True, listing["devices"][-1]["id"]: True}
        assert listing["current"] == kid_id                      # it keeps its own device id (its own browser tab, push, phone data)
        assert (await c.get("/api/operator/status", headers=bearer(kid))).json()["owner"] is True


async def test_any_device_on_an_extra_copy_can_use_keyguard_without_a_flag(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, _, tok = await pair(c, "plain phone")                 # no owner flag of any kind
        r = await c.get("/api/keyguard", headers=bearer(tok))
        assert r.status_code == 200 and r.json()["mode"] == "server"
        # the enroll call gets as far as the end-to-end rule (not the old 403 "only the owner's token")
        r = await c.post("/api/keyguard/enroll", headers=bearer(tok))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "e2e_required"


async def test_the_old_owner_endpoint_and_flag_stay_for_old_tools_but_change_nothing(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, kid_id, kid = await pair(c, "kid")
        assert (await c.put(f"/api/devices/{kid_id}/owner", headers=bearer(kid), json={"owner": False})).status_code == 403   # master only
        r = await c.put(f"/api/devices/{kid_id}/owner", headers=AUTH, json={"owner": False})
        assert r.status_code == 200 and r.json()["owner"] is True
        assert (await c.get("/api/operator/beta", headers=bearer(kid))).status_code == 200
        assert (await c.put("/api/devices/999/owner", headers=AUTH, json={"owner": True})).status_code == 404


# ------------------------------------------------------------------------------------------------ review mode
async def test_a_review_copy_keeps_keyguard_and_operator_settings_from_the_reviewers(tmp_path):
    async with Running(app_for(tmp_path, review_mode=True, operator=True)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, _, plain = await pair(c, "operator's phone")          # a device paired on the server machine is no different here
        _, rtok = await reviewer(c, tmp_path / "s.db")
        for tok in (rtok, plain):
            h = bearer(tok)
            for path in FORMER_OWNER_ONLY:
                assert (await c.get(path, headers=h)).status_code == 403, path
            assert (await c.post("/api/keyguard/enroll", headers=h)).status_code == 403
            assert (await c.post("/api/keyguard/disable", headers=h)).status_code == 403
            assert (await c.get("/api/wallet", headers=h)).status_code == 200          # the wallet itself still works
            assert (await c.get("/api/profile", headers=h)).status_code == 200
            listing = (await c.get("/api/devices", headers=h)).json()["devices"]
            assert listing and not any(d["owner"] for d in listing)                    # reports what review mode allows
        assert (await c.get("/api/operator/status", headers=AUTH)).json() == {"owner": True, "operator": False}   # the access key is unchanged


@pytest.mark.keyguard_real
async def test_a_review_copy_lets_reviewers_add_cards_but_auto_pay_stays_off_because_it_needs_keyguard(tmp_path):
    async with Running(app_for(tmp_path, review_mode=True)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, rtok = await reviewer(c, tmp_path / "s.db")
        card = {"auto_pay": False, "last4": "4242", "brand": "visa", "exp_month": 1, "exp_year": 2040,
                "e2e": {"blob": "QUJD", "alg": "x", "key_id": "k"}}
        r = await c.post("/api/wallet/cards", headers=bearer(rtok), json=card)
        assert r.status_code == 201, r.text
        body = {"auto_pay": True, "number": "4242424242424242", "cvc": "123", "exp_month": 1, "exp_year": 2040}
        r2 = await c.post("/api/wallet/cards", headers=bearer(rtok), json=body)
        assert r2.status_code == 409 and r2.json()["detail"]["code"] == "keyguard_required"


async def test_operator_routes_that_need_the_operators_server_still_404_elsewhere(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, _, tok = await pair(c, "plain phone")
        for h in (AUTH, bearer(tok)):
            assert (await c.get("/api/operator/status", headers=h)).json()["operator"] is False
            assert (await c.delete("/api/operator/reports/1", headers=h)).status_code == 404
            assert (await c.delete("/api/operator/beta/1", headers=h)).status_code == 404


def test_an_old_database_with_the_owner_columns_still_opens_and_its_phones_have_no_special_field(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,"
                      " created_at REAL NOT NULL, last_seen REAL, owner INTEGER NOT NULL DEFAULT 0);"
                      "CREATE TABLE pairings (code_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL, owner INTEGER NOT NULL DEFAULT 0);"
                      "INSERT INTO devices(name, token_hash, created_at, owner) VALUES('old phone', 'h', 1, 1);")
    con.commit()
    con.close()
    st = Store(db)
    assert [d["name"] for d in st.devices()] == ["old phone"] and "owner" not in st.devices()[0]
    dev, tok = st.redeem_pairing(st.new_pairing(owner=True), "new")      # the argument is ignored
    assert st.device_for_token(tok) == {"id": dev, "name": "new", "e2e_only": False}
    Store(db)                                                           # opening it again changes nothing


# ------------------------------------------------------------------------------------------------ the commands
def test_phone_add_asks_only_for_a_link(tmp_path):
    m, _, _ = make(tmp_path)
    inst = m.load(None)
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen["body"] = json
        return Resp(200, {"link": "https://me.example.com/#pair=CODE", "owner": True})

    link, qr = connect.phone_add(m, inst, address="https://me.example.com", post=post)
    assert seen["body"] == {"origin": "https://me.example.com"} and link.endswith("#pair=CODE")


def test_the_cli_still_parses_the_old_owner_options_and_hides_them_from_help(monkeypatch, capsys):
    from assistant import cli
    got = []
    monkeypatch.setattr(manage, "run_command", lambda args, **k: got.append(args) or 0)
    cli.main(["phone", "add", "--owner", "--instance", "alice"])
    cli.main(["phone", "add"])
    cli.main(["phone", "owner", "--grant", "Pixel 9"])
    cli.main(["phone", "owner"])
    assert (got[0].pcmd, got[0].instance) == ("add", "alice") and got[1].pcmd == "add" and got[2].pcmd == "owner"
    with pytest.raises(SystemExit):
        cli.main(["phone", "add", "--help"])
    assert "--owner" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["phone", "owner", "--help"])
    assert "--grant" not in capsys.readouterr().out


def test_phone_owner_says_the_concept_is_gone_and_exits_zero(tmp_path, monkeypatch):
    m, _, msgs = make(tmp_path)
    monkeypatch.setattr(connect, "devices", lambda *a, **k: pytest.fail("it must not call the server"))
    args = SimpleNamespace(cmd="phone", pcmd="owner", instance=None, url=None, owner=False, grant="Pixel 9", revoke=None)
    assert manage.run_command(args, manager=m) == 0
    assert any("no owner devices" in x and "same full rights" in x for x in msgs)


def test_the_setup_phone_step_asks_for_a_plain_link_on_every_copy(tmp_path):
    from test_wizard import FakeUI

    from assistant.cli import write_env
    from assistant.wizard import phone_step
    m, _, _ = make(tmp_path)
    m.create("alice")
    for name in (None, "alice"):
        write_env(m.load(name).env, {"CONNECT_MODE": "proxy", "CONNECT_URL": "https://x.example.com"})
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return Resp(200, {"link": "https://x.example.com/#pair=C", "owner": True})

    for name in (None, "alice"):
        ui = FakeUI("k", True)
        assert phone_step(ui, m, name, post=post)
        assert sent[-1] == {"origin": "https://x.example.com"} and not any("owner" in s for s in ui.shown)
