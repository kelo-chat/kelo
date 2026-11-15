"""A paired phone the owner marked as theirs on the server machine may open the operator settings; nothing else changes for it."""

import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from assistant import connect, manage
from assistant.store import Store

from test_connect_cli import Resp, make
from test_devices import add_phone, app_for, bearer
from test_server import AUTH, Running

OWNER_ONLY = ("/api/operator/firebase", "/api/operator/cloudflare", "/api/operator/beta", "/api/operator/reports",
              "/api/oauth-apps/google", "/api/oauth-apps/github")


async def pair(c, name, **body):
    r = await c.post("/api/devices/pair", headers=AUTH, json=body)
    assert r.status_code == 200
    made = (await c.post("/api/pair", json={"code": r.json()["code"], "name": name})).json()
    return r.json(), made["device_id"], made["token"]


async def test_an_owner_phone_passes_the_operator_checks_and_an_ordinary_phone_still_gets_403(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        start, own_id, own = await pair(c, "mine", owner=True)
        assert start["owner"] is True
        _, r = await add_phone(c, "kid")
        kid_id, kid = r.json()["device_id"], r.json()["token"]
        for path in OWNER_ONLY:
            assert (await c.get(path, headers=bearer(own))).status_code == 200, path
            assert (await c.get(path, headers=bearer(kid))).status_code == 403, path
            assert (await c.get(path, headers=AUTH)).status_code == 200, path
        listing = (await c.get("/api/devices", headers=bearer(own))).json()
        assert {d["id"]: d["owner"] for d in listing["devices"]} == {own_id: True, kid_id: False}
        assert listing["current"] == own_id          # it keeps its own device id (its own browser tab, push, phone data), not "owner"


async def test_only_the_master_token_hands_out_or_takes_back_owner_rights(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, own_id, own = await pair(c, "mine", owner=True)
        _, kid_id, kid = await pair(c, "kid")
        # no phone, not even an owner phone, can make an owner QR or change anyone's rights (its own included)
        for tok in (own, kid):
            for body in ({"owner": True}, {"first_owner": True}):
                assert (await c.post("/api/devices/pair", headers=bearer(tok), json=body)).status_code == 403
            for dev in (own_id, kid_id):
                assert (await c.put(f"/api/devices/{dev}/owner", headers=bearer(tok), json={"owner": True})).status_code == 403
        # an ordinary QR made on a phone still works and pairs an ordinary phone
        r = await c.post("/api/devices/pair", headers=bearer(own), json={})
        assert r.status_code == 200 and r.json()["owner"] is False
        assert (await c.put(f"/api/devices/{kid_id}/owner", headers=AUTH, json={"owner": True})).json()["owner"] is True
        assert (await c.get("/api/operator/beta", headers=bearer(kid))).status_code == 200
        assert (await c.put(f"/api/devices/{kid_id}/owner", headers=AUTH, json={"owner": False})).status_code == 200
        assert (await c.get("/api/operator/beta", headers=bearer(kid))).status_code == 403          # revoked at once, still signed in
        assert (await c.get("/api/profile", headers=bearer(kid))).status_code == 200
        assert (await c.put("/api/devices/999/owner", headers=AUTH, json={"owner": True})).status_code == 404


async def test_first_owner_pairs_as_owner_only_while_nobody_holds_the_rights(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        first, _, a = await pair(c, "first", first_owner=True)
        second, _, b = await pair(c, "second", first_owner=True)
        assert first["owner"] is True and second["owner"] is False
        assert (await c.get("/api/operator/firebase", headers=bearer(a))).status_code == 200
        assert (await c.get("/api/operator/firebase", headers=bearer(b))).status_code == 403


def test_an_old_database_gets_the_owner_columns_and_its_phones_stay_ordinary(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,"
                      " created_at REAL NOT NULL, last_seen REAL);"
                      "CREATE TABLE pairings (code_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL);"
                      "INSERT INTO devices(name, token_hash, created_at) VALUES('old phone', 'h', 1);")
    con.commit()
    con.close()
    st = Store(db)
    assert st.devices()[0]["owner"] is False
    assert st.set_device_owner(1, True) and st.devices()[0]["owner"] is True
    dev, tok = st.redeem_pairing(st.new_pairing(owner=True), "new")
    assert st.device_for_token(tok) == {"id": dev, "name": "new", "owner": True, "e2e_only": False}
    assert not st.set_device_owner(42, True)
    Store(db)                                          # opening it again changes nothing


# ------------------------------------------------------------------------------------------------ the commands
def test_phone_add_owner_asks_for_an_owner_qr_and_refuses_an_old_server(tmp_path):
    m, _, _ = make(tmp_path)
    inst = m.load(None)
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen["body"] = json
        return Resp(200, {"link": "https://me.example.com/#pair=CODE", "owner": bool(json.get("owner"))})

    link, qr = connect.phone_add(m, inst, address="https://me.example.com", owner=True, post=post)
    assert seen["body"] == {"origin": "https://me.example.com", "owner": True} and link.endswith("#pair=CODE")
    connect.phone_add(m, inst, address="https://me.example.com", post=post)
    assert seen["body"] == {"origin": "https://me.example.com"}          # without --owner nothing extra is asked
    with pytest.raises(connect.ConnectError, match="update it first"):   # a server that ignores the field: no silently-ordinary QR
        connect.phone_add(m, inst, owner=True, post=lambda *a, **k: Resp(200, {"link": "https://x/#pair=C"}))


def test_the_cli_parses_phone_add_owner_and_phone_owner(monkeypatch):
    from assistant import cli
    got = []
    monkeypatch.setattr(manage, "run_command", lambda args, **k: got.append(args) or 0)
    cli.main(["phone", "add", "--owner", "--instance", "alice"])
    cli.main(["phone", "add"])
    cli.main(["phone", "owner", "--grant", "Pixel 9"])
    cli.main(["phone", "owner", "--revoke", "3"])
    cli.main(["phone", "owner"])
    assert (got[0].pcmd, got[0].owner, got[0].instance) == ("add", True, "alice") and got[1].owner is False
    assert (got[2].pcmd, got[2].grant, got[2].revoke) == ("owner", "Pixel 9", None)
    assert (got[3].grant, got[3].revoke) == (None, "3") and (got[4].grant, got[4].revoke) == (None, None)
    with pytest.raises(SystemExit):
        cli.main(["phone", "owner", "--grant", "1", "--revoke", "2"])


class FakeServer:
    def __init__(self, devices):
        self.devices, self.changes, self.pairs = devices, [], []

    def install(self, monkeypatch):
        monkeypatch.setattr(connect, "devices", lambda m, inst: [dict(d) for d in self.devices])
        monkeypatch.setattr(connect, "set_device_owner", lambda m, inst, dev, owner: self.changes.append((dev, owner)))

        def phone_add(m, inst, address=None, owner=False):
            self.pairs.append(owner)
            return "https://me.example.com/#pair=CODE", "QR\n"
        monkeypatch.setattr(connect, "phone_add", phone_add)
        return self


def args(pcmd, **kw):
    return SimpleNamespace(cmd="phone", pcmd=pcmd, instance=None, url=None, owner=False, grant=None, revoke=None, **kw)


DEVICES = [{"id": 1, "name": "Pixel 9", "owner": False}, {"id": 2, "name": "iPad", "owner": True}, {"id": 3, "name": "pixel 9 old", "owner": False}]


def test_phone_owner_grants_and_revokes_by_number_or_name(tmp_path, monkeypatch):
    m, _, msgs = make(tmp_path)
    fake = FakeServer(DEVICES).install(monkeypatch)
    assert manage.run_command(SimpleNamespace(**{**vars(args("owner")), "grant": "pixel 9"}), manager=m) == 0   # exact name first
    assert manage.run_command(SimpleNamespace(**{**vars(args("owner")), "revoke": "2"}), manager=m) == 0
    assert fake.changes == [(1, True), (2, False)]
    assert any("now has the owner's rights" in x for x in msgs) and any("no longer has" in x for x in msgs)
    assert manage.run_command(SimpleNamespace(**{**vars(args("owner")), "grant": "iPad"}), manager=m) == 0
    assert fake.changes == [(1, True), (2, False)] and "already has" in msgs[-1]
    assert manage.run_command(SimpleNamespace(**{**vars(args("owner")), "grant": "Nokia"}), manager=m) == 1
    assert "no signed-in device" in msgs[-1]


def test_phone_owner_in_a_terminal_lists_and_toggles_the_picked_device(tmp_path, monkeypatch):
    m, _, msgs = make(tmp_path)
    fake = FakeServer(DEVICES).install(monkeypatch)
    answers = iter(["3", "y"])
    assert manage.run_command(args("owner"), manager=m, ask=lambda q: next(answers)) == 0
    assert fake.changes == [(3, True)] and any("[owner]" in x and "iPad" in x for x in msgs)
    answers = iter(["2", "n"])
    assert manage.run_command(args("owner"), manager=m, ask=lambda q: next(answers)) == 0
    assert fake.changes == [(3, True)] and msgs[-1] == "Nothing was changed."
    answers = iter([""])
    assert manage.run_command(args("owner"), manager=m, ask=lambda q: next(answers)) == 0
    assert fake.changes == [(3, True)]


def test_phone_add_owner_says_clearly_what_the_qr_gives(tmp_path, monkeypatch):
    m, _, msgs = make(tmp_path)
    fake = FakeServer([]).install(monkeypatch)
    assert manage.run_command(SimpleNamespace(**{**vars(args("add")), "owner": True}), manager=m) == 0
    assert fake.pairs == [True] and any(x.startswith("OWNER QR") and "assistant phone owner --revoke" in x for x in msgs)
    msgs.clear()
    assert manage.run_command(args("add"), manager=m) == 0
    assert fake.pairs == [True, False] and not any("OWNER" in x for x in msgs)


def test_the_setup_phone_step_pairs_the_main_owners_first_phone_as_owner_and_extra_copies_as_ordinary(tmp_path):
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
        return Resp(200, {"link": "https://x.example.com/#pair=C", "owner": bool(json.get("first_owner"))})

    ui = FakeUI("k", True)                              # keep the address, sign a phone in now
    assert phone_step(ui, m, None, post=post, start=False) is False   # start=False: no QR at all
    ui = FakeUI("k", True)
    assert phone_step(ui, m, None, post=post)
    assert sent[-1] == {"origin": "https://x.example.com", "first_owner": True} and any("owner's rights" in s for s in ui.shown)
    ui = FakeUI("k", True)
    assert phone_step(ui, m, "alice", post=post)
    assert sent[-1] == {"origin": "https://x.example.com"} and not any("owner's rights" in s for s in ui.shown)
