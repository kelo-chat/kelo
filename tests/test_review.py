"""App store review copies: multi-use access codes (store + API), review mode (no subscription sign-ins, no operator settings, the
server's model counts as chosen, everything else as normal), the offline reset, and `assistant review ...` (no Docker: fake runs)."""

import argparse
import json
import sqlite3
import stat
import subprocess
import time

import httpx
import pytest

from assistant import connect, review, review_cli
from assistant.cli import main as cli_main, read_env, write_env
from assistant.instances import InstanceError, Instances
from assistant.providers import Done, ProviderError, TextDelta
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running, read_events

UNOFFICIAL = ("claude-subscription-directsdk-experimental", "antigravity-oauth", "openai-codex", "qwen-oauth", "minimax-oauth")


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _quiet(provider, request, **k):
    yield TextDelta("ok")
    yield Done("stop")


def app_for(tmp_path, *, stream_fn=_quiet, **kw):
    kw.setdefault("provider", "anthropic")
    kw.setdefault("model", "claude-test")
    return create_app(Settings(token=TOKEN, cdp_host=None, db_path=str(tmp_path / "s.db"), **kw), tools=[], stream_fn=stream_fn)


# ------------------------------------------------------------------------------------------------------------- access codes: store
def test_a_code_signs_in_several_phones_up_to_its_limit_and_only_its_hash_is_kept(tmp_path):
    st = Store(tmp_path / "a.db")
    cid, code = st.new_access_code("review-apple", days=30, uses=2)
    assert len(code) == 19 and code.count("-") == 3 and code.replace("-", "").isalnum() and code == code.upper()
    assert not set(code) & set("ILO01")                                      # nothing that reads as something else
    typed = code.replace("-", "").lower()                                    # typed on a phone: lower case, no dashes
    a = st.redeem_pairing(typed, "iPad of the reviewer")
    b = st.redeem_pairing(f" {code} ".strip(), "iPhone")
    assert a and b and a[1] != b[1]
    assert st.redeem_pairing(code, "third") is None                          # used up
    devs = st.devices()
    assert [d["code"] for d in devs] == ["review-apple"] * 2 and not any(d["owner"] for d in devs)
    assert st.device_for_token(a[1])["owner"] is False
    row = st.access_codes()[0]
    assert row["id"] == cid and row["uses"] == 2 and row["devices"] == 2 and row["state"] == "used_up" and "code" not in row
    st._db.commit()
    raw = (tmp_path / "a.db").read_bytes()
    assert code.encode() not in raw and code.replace("-", "").encode() not in raw and typed.encode() not in raw
    stored = st._all("SELECT code_hash FROM access_codes")[0]["code_hash"]
    assert len(stored) == 64 and code not in stored


def test_a_code_stops_when_it_expires_but_its_phones_stay_signed_in(tmp_path):
    st = Store(tmp_path / "a.db")
    cid, code = st.new_access_code("review-google", days=1, uses=5)
    dev = st.redeem_pairing(code, "Pixel")
    st._run("UPDATE access_codes SET expires_at = ? WHERE id = ?", (time.time() - 1, cid))
    assert st.redeem_pairing(code, "late") is None
    assert st.access_codes()[0]["state"] == "expired"
    assert st.device_for_token(dev[1]) is not None                           # expiry ends new sign-ins only


def test_revoking_a_code_signs_out_its_phones_only(tmp_path):
    st = Store(tmp_path / "a.db")
    own = st.redeem_pairing(st.new_pairing(), "owner's phone")
    c1, code1 = st.new_access_code("review-ms", uses=5)
    c2, code2 = st.new_access_code("review-ms", uses=5)
    r1, r2 = st.redeem_pairing(code1, "Surface"), st.redeem_pairing(code1, "Laptop")
    other = st.redeem_pairing(code2, "Desktop")
    assert sorted(st.revoke_access_code(c1)) == sorted([r1[0], r2[0]])
    assert st.device_for_token(r1[1]) is None and st.device_for_token(r2[1]) is None
    assert st.device_for_token(other[1]) and st.device_for_token(own[1])
    assert st.redeem_pairing(code1, "again") is None and st.access_codes()[0]["state"] == "revoked"
    assert st.revoke_access_code(999) is None


def test_bad_code_settings_are_refused(tmp_path):
    st = Store(tmp_path / "a.db")
    for kw in ({"days": 0}, {"days": 400}, {"uses": 0}, {"uses": 5000}):
        with pytest.raises(ValueError):
            st.new_access_code("x", **kw)
    with pytest.raises(ValueError):
        st.new_access_code("   ")


def test_an_older_database_gets_the_code_column(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, "
               "created_at REAL NOT NULL, last_seen REAL, owner INTEGER NOT NULL DEFAULT 0)")
    db.execute("INSERT INTO devices(name, token_hash, created_at) VALUES('old', 'h', 1)")
    db.commit()
    db.close()
    st = Store(tmp_path / "old.db")
    assert st.devices()[0]["code"] == "" and st.redeem_pairing(st.new_access_code("r")[1], "new")


# ------------------------------------------------------------------------------------------------------------- access codes: API
async def test_codes_are_made_and_revoked_with_the_master_token_only(tmp_path):
    async with Running(app_for(tmp_path)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        one = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
        phone = (await c.post("/api/pair", json={"code": one, "name": "owner phone"})).json()["token"]
        assert (await c.post("/api/devices/codes", json={"label": "review-apple"}, headers=bearer(phone))).status_code == 403
        assert (await c.get("/api/devices/codes", headers=bearer(phone))).status_code == 403
        assert (await c.post("/api/devices/codes", json={"label": "x", "days": 0})).status_code == 401
        assert (await c.post("/api/devices/codes", json={"label": "x", "days": 0}, headers=AUTH)).status_code == 422
        made = (await c.post("/api/devices/codes", json={"label": "review-apple", "days": 30, "uses": 3}, headers=AUTH)).json()
        assert made["max_uses"] == 3 and made["label"] == "review-apple" and made["expires_at"] > time.time() + 29 * 86400
        tokens = []
        for i in range(3):
            r = await c.post("/api/pair", json={"code": made["code"].lower(), "name": f"reviewer {i}"})
            assert r.status_code == 200
            tokens.append(r.json()["token"])
        r = await c.post("/api/pair", json={"code": made["code"], "name": "fourth"})
        assert r.status_code == 400 and "withdrawn" in r.json()["detail"]
        listed = (await c.get("/api/devices/codes", headers=AUTH)).json()["codes"]
        assert listed[0]["devices"] == 3 and listed[0]["state"] == "used_up" and made["code"] not in json.dumps(listed)
        devs = (await c.get("/api/devices", headers=bearer(tokens[0]))).json()["devices"]
        assert sorted(d["code"] for d in devs) == ["", "review-apple", "review-apple", "review-apple"]
        # a reviewer phone is an ordinary device: no operator settings
        assert (await c.get("/api/operator/status", headers=bearer(tokens[0]))).status_code == 403
        assert (await c.delete(f"/api/devices/codes/{made['id']}", headers=bearer(tokens[0]))).status_code == 403
        gone = (await c.delete(f"/api/devices/codes/{made['id']}", headers=AUTH)).json()
        assert gone == {"ok": True, "signed_out": 3}
        for t in tokens:
            assert (await c.get("/api/profile", headers=bearer(t))).status_code == 401
        assert (await c.get("/api/profile", headers=bearer(phone))).status_code == 200      # the owner's phone stays
        assert (await c.delete("/api/devices/codes/999", headers=AUTH)).status_code == 404


# ------------------------------------------------------------------------------------------------------------- review mode
def test_review_mode_comes_from_the_env_and_keeps_subscription_sign_ins_off():
    base = {"ASSISTANT_TOKEN": TOKEN}
    assert Settings.from_env(base).review_mode is False
    s = Settings.from_env({**base, "ASSISTANT_REVIEW_MODE": "1", "ASSISTANT_UNOFFICIAL_PROVIDERS": "1"})
    assert s.review_mode is True and s.unofficial_providers is False


async def test_review_mode_hides_and_refuses_subscription_sign_ins_even_when_switched_on(tmp_path):
    Store(tmp_path / "s.db").set_profile(provider="antigravity-oauth", model="g", fallback_provider="openai-codex", fallback_model="x")
    app = app_for(tmp_path, review_mode=True, unofficial_providers=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        prof = (await c.get("/api/profile")).json()
        assert prof["fallback_provider"] == "" and prof["provider"] == "anthropic" and prof["model"] == "claude-test"
        names = {p["name"] for p in (await c.get("/api/providers")).json()}
        assert "anthropic" in names and not names & set(UNOFFICIAL)
        assert (await c.put("/api/providers/agy/consent", json={"accepted": True})).status_code == 200
        r = await c.put("/api/profile", json={"provider": "antigravity-oauth", "model": "g"})
        assert r.status_code == 403 and r.json()["detail"].startswith("unofficial_disabled:")
        assert (await c.post("/api/providers/antigravity-oauth/login/start", json={})).status_code == 403
        assert (await c.put("/api/profile", json={"provider": "anthropic", "model": "claude-other"})).status_code == 200


async def test_a_subscription_provider_named_by_the_environment_is_never_called(tmp_path):
    called = []

    async def fn(provider, request, **kw):
        called.append(provider)
        yield TextDelta("should not happen")
        yield Done("stop")
    guarded = review.official_only(fn, lambda p: p == "antigravity-oauth")
    with pytest.raises(ProviderError) as e:
        async for _ in guarded("antigravity-oauth", None):
            pass
    assert e.value.status == 403 and not called
    assert [ev async for ev in guarded("anthropic", None)][0].text == "should not happen"
    called.clear()
    app = app_for(tmp_path, stream_fn=fn, review_mode=True, provider="antigravity-oauth", model="g")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/profile", headers=AUTH)).json()["provider"] == ""   # never written into the profile
        async with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=AUTH) as r:
            events = await read_events(r)
    assert not called and any(ev.get("type") == "error" for ev in events)


async def test_the_server_model_counts_as_chosen_and_is_shown_by_name(tmp_path):
    async with Running(app_for(tmp_path, review_mode=True)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        prof = (await c.get("/api/profile")).json()
        assert prof["model_ready"] is True and (prof["provider"], prof["model"]) == ("anthropic", "claude-test")
    # a model chosen earlier in the app stays (the CLI sets the profile when the operator changes the model)
    st = Store(tmp_path / "s.db")
    st.set_profile(provider="gemini", model="gem")
    async with Running(app_for(tmp_path, review_mode=True)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/profile")).json()["model"] == "gem"
    # a normal copy keeps the server default as the default (empty in the profile)
    async with Running(app_for(tmp_path / "n")) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        prof = (await c.get("/api/profile")).json()
        assert prof["provider"] == "" and prof["model_ready"] is True


async def test_review_copies_keep_every_other_feature_and_never_claim_to_be_the_operator(tmp_path):
    async with Running(app_for(tmp_path, review_mode=True, operator=True)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/operator/status")).json() == {"owner": True, "operator": False}
        assert (await c.get("/api/wallet")).status_code == 200                # payments are not turned off
        assert (await c.get("/api/connectors")).status_code == 200
        assert (await c.get("/api/schedules")).status_code == 200
    async with Running(app_for(tmp_path / "op", operator=True)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/operator/status")).json()["operator"] is True


# ------------------------------------------------------------------------------------------------------------- reset
def test_reset_keeps_sign_ins_and_keys_and_drops_everything_else(tmp_path):
    state = tmp_path / "state"
    st = Store(state / "assistant.db")
    st.set_profile(user_name="Reviewer", provider="gemini", model="gem", timezone="Asia/Seoul")
    st.add_message("user", "secret plans")
    st.set_key("anthropic", "sk-ant-test-key")
    st.set_meta("base_url:lmstudio", "http://x")
    st.set_meta("ui_lang", "ko")
    st.save_credential("github", "GitHub", ["github.com"], {"in": "header"})
    tok = st.redeem_pairing(st.new_pairing(), "phone")[1]
    cid, code = st.new_access_code("review-apple", uses=5)
    rtok = st.redeem_pairing(code, "reviewer")[1]
    st._db.execute("CREATE TABLE schedules (id INTEGER PRIMARY KEY, name TEXT)")
    st._db.execute("INSERT INTO schedules(name) VALUES('every morning')")
    st._db.commit()
    last_device = st.devices()[-1]["id"]
    st._db.close()
    (state / "home").mkdir()
    (state / "home" / "MEMORY.md").write_text("the reviewer likes tea")
    (state / "uploads.bin").write_bytes(b"x")
    done = review.reset_state(state)
    assert done["removed"] == 2 and sorted(p.name for p in state.iterdir()) == ["assistant.db", "master.key"]
    assert stat.S_IMODE((state / "assistant.db").stat().st_mode) == 0o600
    st2 = Store(state / "assistant.db")
    assert st2.messages() == [] and st2.credentials() == []
    prof = st2.profile()
    assert prof["user_name"] == "" and prof["provider"] == "" and prof["timezone"] == "Asia/Seoul"
    assert st2.key("anthropic") == "sk-ant-test-key" and st2.meta("base_url:lmstudio") == "http://x" and st2.meta("ui_lang") == ""
    assert st2.device_for_token(tok) and st2.device_for_token(rtok)["name"] == "reviewer"
    assert st2.access_codes()[0]["uses"] == 1 and st2.redeem_pairing(code, "another")
    assert st2.devices()[-1]["id"] == last_device + 1                       # ids are never handed out twice
    assert "schedules" not in {r["name"] for r in st2._all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert review.main(["nope"]) == 64


# ------------------------------------------------------------------------------------------------------------- CLI
class FakeRun:
    def __init__(self, running="server\nbrowser\n"):
        self.calls, self.running = [], running

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=self.running if "ps" in cmd else "", stderr="")


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def make(tmp_path, **kw):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("BROWSER=chromium\nASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    (tmp_path / "docs" / "review").mkdir(parents=True)
    for s in ("apple", "google", "ms"):
        (tmp_path / "docs" / "review" / f"notes-{s}.txt").write_text("Address: {address}\nCode: {code}\nLink: {link}\n"
                                                                       "Until {expires}, {uses} devices, {store}\n")
    out, run = [], FakeRun(**kw)
    return Instances(tmp_path, run=run, port_free=lambda p: True, out=out.append), run, out


def ns(rcmd, store=None, **kw):
    return argparse.Namespace(rcmd=rcmd, store=store, **kw)


def test_the_cli_parses_every_review_command():
    import assistant.review_cli as rc
    seen = []
    orig = rc.run_command
    rc.run_command = lambda args: seen.append(args) or 0
    try:
        assert cli_main(["review", "code", "apple", "--days", "10", "--uses", "3"]) == 0
        assert cli_main(["review", "reset", "ms", "--yes"]) == 0
        assert cli_main(["review", "create", "google", "--address"]) == 0
    finally:
        rc.run_command = orig
    assert (seen[0].rcmd, seen[0].store, seen[0].days, seen[0].uses) == ("code", "apple", 10, 3)
    assert seen[1].yes and seen[2].address
    with pytest.raises(SystemExit):
        cli_main(["review", "code", "samsung"])


def test_create_makes_a_recommended_review_copy_and_only_prints_the_address_step(tmp_path, monkeypatch):
    m, run, out = make(tmp_path)
    monkeypatch.setattr(connect, "_post", lambda *a, **k: pytest.fail("the address service must not be asked"))
    assert review_cli.run_command(ns("create", "apple", no_start=False, address=False), m) == 0
    env = m.env_path("review-apple")
    assert read_env(env, "ASSISTANT_REVIEW_MODE") == "1" and read_env(env, "ASSISTANT_UNOFFICIAL_PROVIDERS") == "0"
    assert read_env(env, "SERVER_MEMORY")                                    # the recommended size
    assert any("up" in c for c in run.calls)                                 # started
    text = "\n".join(out)
    assert "assistant connect kelo --instance review-apple" in text and "https://review-apple.kelo.chat" in text
    assert review_cli.run_command(ns("create", "apple", no_start=True, address=False), m) == 1   # already there
    assert "already exists" in out[-1]


def test_create_with_address_claims_the_kelo_name(tmp_path, monkeypatch):
    m, _, out = make(tmp_path)
    asked = []

    def post(url, json=None, **k):
        asked.append((url, json["name"]))
        return Resp(200, {"hostname": "review-google.kelo.chat", "tunnel_token": "t" * 40})
    monkeypatch.setattr(connect, "_post", post)
    assert review_cli.run_command(ns("create", "google", no_start=True, address=True), m) == 0
    assert asked == [("https://kelo.chat/broker/tunnel/claim", "review-google")]
    assert m.load("review-google").connect_url == "https://review-google.kelo.chat"


def test_code_writes_a_private_notes_file_and_never_prints_the_code(tmp_path, monkeypatch):
    m, _, out = make(tmp_path)
    review_cli.run_command(ns("create", "ms", no_start=True, address=False), m)
    write_env(m.env_path("review-ms"), {"CONNECT_MODE": "kelo", "CONNECT_URL": "https://review-ms.kelo.chat"})
    code = "ABCD-EFGH-JKMN-PQRS"
    sent = []

    def post(url, json=None, headers=None, **k):
        sent.append((url, json, headers))
        return Resp(200, {"id": 4, "code": code, "label": "review-ms", "expires_at": time.time() + 30 * 86400, "max_uses": 20})
    monkeypatch.setattr(connect, "_post", post)
    out.clear()
    assert review_cli.run_command(ns("code", "ms", days=30, uses=20), m) == 0
    path = tmp_path / "instances" / "review-ms" / "review-notes.txt"
    assert out[0] == str(path) and code not in "\n".join(out) and code.replace("-", "") not in "\n".join(out)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    body = path.read_text()
    assert f"Code: {code}" in body and "Address: https://review-ms.kelo.chat" in body
    assert f"Link: https://review-ms.kelo.chat/#pair={code}" in body and "20 devices" in body and "Microsoft Store" in body
    url, payload, headers = sent[0]
    assert url.endswith("/api/devices/codes") and payload == {"label": "review-ms", "days": 30, "uses": 20}
    assert headers["Authorization"] == "Bearer " + m.token("review-ms")
    assert review_cli.run_command(ns("code", "ms", days=0, uses=20), m) == 1


def test_code_without_an_address_warns_and_review_commands_need_a_review_copy(tmp_path, monkeypatch):
    m, _, out = make(tmp_path)
    review_cli.run_command(ns("create", "apple", no_start=True, address=False), m)
    monkeypatch.setattr(connect, "_post", lambda *a, **k: Resp(200, {"id": 1, "code": "WXYZ-WXYZ-WXYZ-WXYZ", "label": "review-apple",
                                                                     "expires_at": time.time() + 86400, "max_uses": 2}))
    assert review_cli.run_command(ns("code", "apple", days=1, uses=2), m) == 0
    assert "no kelo.chat address yet" in out[-1] and "WXYZ" not in "\n".join(out)
    assert review_cli.run_command(ns("revoke", "google"), m) == 1 and "assistant review create google" in out[-1]
    m.create("review-google")                                                # made by hand, without review mode
    assert review_cli.run_command(ns("revoke", "google"), m) == 1 and "not in review mode" in out[-1]


def test_model_saves_an_api_key_provider_hidden_and_refuses_sign_ins(tmp_path, monkeypatch):
    m, run, out = make(tmp_path)
    review_cli.run_command(ns("create", "google", no_start=True, address=False), m)
    put = []
    monkeypatch.setattr(connect, "_put", lambda url, json=None, **k: put.append((url, json)) or Resp(200, {}))
    monkeypatch.setattr(connect, "_get", lambda url, **k: Resp(200, {"ok": True}))
    monkeypatch.delenv(review_cli.KEY_ENV, raising=False)
    key = "sk-ant-api03-secret-value"
    args = ns("model", "google", provider="anthropic", model="claude-x")
    assert review_cli.run_command(args, m, secret=lambda prompt: key) == 0
    env = m.env_path("review-google")
    assert read_env(env, "ASSISTANT_PROVIDER") == "anthropic" and read_env(env, "ASSISTANT_MODEL") == "claude-x"
    assert read_env(env, "ANTHROPIC_API_KEY") == key and key not in "\n".join(out)
    assert put == [(f"{m.load('review-google').local_url}/api/profile", {"provider": "anthropic", "model": "claude-x"})]
    for name in ("antigravity-oauth", "claude-subscription-directsdk-experimental", "openai-codex"):
        assert review_cli.run_command(ns("model", "google", provider=name, model="m"), m, secret=lambda p: key) == 1
        assert "does not take an API key" in out[-1]
    assert review_cli.run_command(ns("model", "google", provider="anthropic", model="m"), m, secret=lambda p: "short") == 1


def test_status_and_revoke_talk_to_the_server_without_showing_secrets(tmp_path, monkeypatch):
    m, _, out = make(tmp_path)
    review_cli.run_command(ns("create", "apple", no_start=True, address=False), m)
    codes = [{"id": 1, "label": "review-apple", "state": "active", "uses": 2, "max_uses": 20, "devices": 2,
              "expires_at": time.time() + 86400, "created_at": 0, "revoked_at": None},
             {"id": 2, "label": "review-apple", "state": "revoked", "uses": 1, "max_uses": 20, "devices": 0,
              "expires_at": time.time() + 86400, "created_at": 0, "revoked_at": 1}]
    monkeypatch.setattr(connect, "_get", lambda *a, **k: Resp(200, {"codes": codes}))
    deleted = []
    monkeypatch.setattr(review_cli, "_delete", lambda url, **k: deleted.append(url) or Resp(200, {"ok": True, "signed_out": 2}))
    notes = tmp_path / "instances" / "review-apple" / "review-notes.txt"
    notes.write_text("old code")
    out.clear()
    assert review_cli.run_command(ns("status", None), m) == 0
    text = "\n".join(out)
    assert "review-apple" in text and "code #1: active, 2/20 used, 2 signed in" in text and "not set up" in text
    out.clear()
    assert review_cli.run_command(ns("revoke", "apple"), m) == 0
    assert [u.rsplit("/", 1)[1] for u in deleted] == ["1"] and not notes.exists()
    assert "1 code revoked, 2 phones signed out" in out[0]


def test_reset_stops_wipes_in_a_one_off_container_and_starts_again(tmp_path):
    m, run, out = make(tmp_path)
    review_cli.run_command(ns("create", "ms", no_start=True, address=False), m)
    run.calls.clear()
    assert review_cli.run_command(ns("reset", "ms", yes=False), m, ask=lambda p: "nope") == 1
    assert run.calls == []
    assert review_cli.run_command(ns("reset", "ms", yes=False), m, ask=lambda p: "review-ms") == 0
    joined = [" ".join(c) for c in run.calls]
    i_stop = next(i for i, c in enumerate(joined) if c.endswith(" stop"))
    i_run = next(i for i, c in enumerate(joined) if " run --rm --no-deps -T --entrypoint python server -m assistant.review reset-state /state" in c)
    i_down = next(i for i, c in enumerate(joined) if " down --remove-orphans" in c)
    i_vol = next(i for i, c in enumerate(joined) if c.startswith("docker volume rm -f assistant-u-review-ms_browser-data"))
    i_up = max(i for i, c in enumerate(joined) if " up -d" in c)
    assert i_stop < i_run < i_down < i_vol < i_up
    assert all("assistant-u-review-ms" in c for c in joined if c.startswith("docker compose"))
    assert not any("--volumes" in c for c in joined)                         # the state volume (codes, devices, keys) stays


def test_delete_goes_through_instance_delete_and_enable_oauth_is_refused(tmp_path):
    from assistant.manage import _oauth
    m, run, out = make(tmp_path)
    review_cli.run_command(ns("create", "apple", no_start=True, address=False), m)
    with pytest.raises(InstanceError, match="review copy"):
        _oauth(m, "review-apple", on=True)
    assert read_env(m.env_path("review-apple"), "ASSISTANT_UNOFFICIAL_PROVIDERS") == "0"
    assert review_cli.run_command(ns("delete", "apple", yes=True), m) == 0
    assert "review-apple" not in m.names() and any("down" in c and "--volumes" in c for c in run.calls)
