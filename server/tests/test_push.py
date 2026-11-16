"""Push notifications: phone registration on each copy, the operator's Firebase key, the broker (/broker/push/send) and the copy's
sending, against a pretend Google (OAuth token endpoint + FCM HTTP v1). Nothing reaches a real service."""

import asyncio
import base64
import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from assistant import push as push_mod
from assistant.agent import Tool, emit_ui
from assistant.netguard import NetGuardError, Reply
from assistant.providers import Done, TextDelta, ToolCall
from assistant.push import FCM_SCOPE, Apns, Fcm, PushBroker, PushClient, PushError, check_data, parse_service_account
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running, follow

PROJECT = "kelo-push-demo"
EMAIL = "firebase-adminsdk-abcde@kelo-push-demo.iam.gserviceaccount.com"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
KEY_FILE = {"type": "service_account", "project_id": PROJECT, "private_key_id": "0123abcd", "private_key": PEM, "client_email": EMAIL,
            "client_id": "1234567890", "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://evil.example/token"}
BROKER = "https://broker.example.org"
# FCM registration tokens look like this: <instance id>:APA91b<…>
PHONE_A = "dA1b2C3d4E5:APA91bHUN-" + "a" * 140
PHONE_B = "eB1b2C3d4E5:APA91bHUN-" + "b" * 140
PHONE_C = "fC1b2C3d4E5:APA91bHUN-" + "c" * 140


def j(obj, status=200, headers=None):
    return Reply(status, {"content-type": "application/json", **(headers or {})}, json.dumps(obj).encode(), False)


def fcm_error(status, code, grpc):
    return j({"error": {"code": status, "message": "Requested entity was not found." if code == "UNREGISTERED" else "x", "status": grpc,
                        "details": [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code}]}}, status)


class FakeGoogle:
    """oauth2.googleapis.com/token (checks the signed JWT and its scope) and fcm.googleapis.com/v1/.../messages:send."""

    def __init__(self):
        self.mints = 0
        self.valid: set[str] = set()
        self.sent: list[dict] = []           # every FCM request body
        self.gone: set[str] = set()          # tokens FCM calls UNREGISTERED
        self.bad: set[str] = set()           # INVALID_ARGUMENT
        self.flaky: dict[str, int] = {}      # token -> how many 503s before it goes through
        self.refuse_key = False

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None, **kw):
        host = urlsplit(url).hostname
        assert host in allowed_hosts and url.startswith("https://")
        if url == "https://oauth2.googleapis.com/token":
            form = parse_qs(content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            head, claims, sig = form["assertion"][0].split(".")
            _KEY.public_key().verify(base64.urlsafe_b64decode(sig + "=="), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
            c = json.loads(base64.urlsafe_b64decode(claims + "=="))
            assert c["iss"] == EMAIL and c["scope"] == FCM_SCOPE and c["aud"] == "https://oauth2.googleapis.com/token"
            if self.refuse_key:
                return j({"error": "invalid_grant", "error_description": "Invalid JWT Signature."}, 400)
            self.mints += 1
            tok = f"ya29.fake-{self.mints}"
            self.valid.add(tok)
            return j({"access_token": tok, "expires_in": 3599, "token_type": "Bearer"})
        if url == f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send":
            assert method == "POST"
            if (headers or {}).get("Authorization", "").removeprefix("Bearer ") not in self.valid:
                return j({"error": {"code": 401, "message": "Request had invalid authentication credentials.", "status": "UNAUTHENTICATED"}}, 401)
            body = json.loads(content)
            self.sent.append(body)
            token = body["message"]["token"]
            if token in self.gone:
                return fcm_error(404, "UNREGISTERED", "NOT_FOUND")
            if token in self.bad:
                return fcm_error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT")
            if self.flaky.get(token):
                self.flaky[token] -= 1
                return fcm_error(503, "UNAVAILABLE", "UNAVAILABLE")
            return j({"name": f"projects/{PROJECT}/messages/0:{len(self.sent)}"})
        raise NetGuardError(f"{host} is not a registered host")


class Logs(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    def __enter__(self):
        for name in ("uvicorn.error", "uvicorn.access", "assistant"):
            lg = logging.getLogger(name)
            lg.addHandler(self)
            self._old = getattr(self, "_old", {})
            self._old[name] = lg.level
            lg.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        for name, level in self._old.items():
            logging.getLogger(name).removeHandler(self)
            logging.getLogger(name).setLevel(level)

    @property
    def text(self):
        return "\n".join(self.lines)


def answering(text):
    async def fn(provider, request, **kw):
        yield TextDelta(text)
        yield Done("stop")
    return fn


def make_app(tmp_path, google, *, name="a", broker="", stream_fn=None, tools=None, fetcher=None):
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / f"{name}.db"), push_broker=broker),
                     tools=tools or [], stream_fn=stream_fn or answering("다 했어요."), push_fetcher=fetcher or google.fetch)
    app.state.fcm.backoff = 0.0
    app.state.push.backoff = (0.0, 0.0, 0.0)
    return app


async def pair(c, name="phone"):
    code = (await c.post("/api/devices/pair", headers=AUTH, json={})).json()["code"]
    got = (await c.post("/api/pair", json={"code": code, "name": name})).json()
    return got["device_id"], {"Authorization": f"Bearer {got['token']}"}


async def until(cond, tries=200):
    for _ in range(tries):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


# ---------------------------------------------------------------------------------------------------------- registration
async def test_a_phone_registers_replaces_and_removes_its_token_and_a_removed_device_loses_it(tmp_path):
    google = FakeGoogle()
    app = make_app(tmp_path, google)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        push = app.state.push
        dev, phone = await pair(c)
        # no broker and no Firebase key: push is off, but the endpoints answer and the token is kept for later
        assert (await c.get("/api/push/status", headers=phone)).json() == {"enabled": False, "registered": False, "mode": "off"}
        r = await c.post("/api/push/register", headers=phone, json={"token": PHONE_A, "platform": "android"})
        assert r.json() == {"ok": True, "push": False}
        assert PHONE_A not in r.text
        assert (await c.get("/api/push/status", headers=phone)).json()["registered"] is True
        # the master token is not a phone; a bad token or platform is refused without echoing it
        assert (await c.post("/api/push/register", headers=AUTH, json={"token": PHONE_B, "platform": "android"})).status_code == 403
        bad = await c.post("/api/push/register", headers=phone, json={"token": "not a token <script>", "platform": "android"})
        assert bad.status_code == 422 and "<script>" not in bad.text
        assert (await c.post("/api/push/register", headers=phone, json={"token": PHONE_B, "platform": "ios"})).status_code == 422
        # a newer token replaces the device's older one
        assert (await c.post("/api/push/register", headers=phone, json={"token": PHONE_B, "platform": "android"})).json()["ok"] is True
        assert push.tokens() == [PHONE_B]
        # a second device; the same FCM token moving to a new pairing leaves the old one
        dev2, phone2 = await pair(c, "tablet")
        await c.post("/api/push/register", headers=phone2, json={"token": PHONE_B})
        assert push.tokens() == [PHONE_B] and push.registered(dev2) and not push.registered(dev)
        await c.post("/api/push/register", headers=phone, json={"token": PHONE_A})
        assert sorted(push.tokens()) == sorted([PHONE_A, PHONE_B])
        # DELETE /api/push/register: the current device only
        assert (await c.delete("/api/push/register", headers=phone)).json() == {"ok": True, "removed": True, "push": False}
        assert push.tokens() == [PHONE_B]
        # removing a device drops its token
        assert (await c.delete(f"/api/devices/{dev2}", headers=AUTH)).json() == {"ok": True}
        assert push.tokens() == [] and not push.registered(dev2)


# ---------------------------------------------------------------------------------------------------------- the operator's key
async def test_the_operator_uploads_the_firebase_key_which_is_checked_stored_encrypted_and_never_returned(tmp_path):
    google = FakeGoogle()
    app = make_app(tmp_path, google)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, phone = await pair(c)
        assert (await c.get("/api/operator/firebase", headers=AUTH)).json() == {"enabled": False, "project_id": "", "client_email": ""}
        assert (await c.get("/api/operator/firebase", headers=phone)).status_code == 200   # any signed-in device
        for broken in (b"hello", json.dumps({**KEY_FILE, "type": "authorized_user"}), json.dumps({**KEY_FILE, "project_id": "X"}),
                       json.dumps({**KEY_FILE, "client_email": "me@gmail.com"}), json.dumps({**KEY_FILE, "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"})):
            r = await c.put("/api/operator/firebase", headers=AUTH, content=broken)
            assert r.status_code == 422 and "AAAA" not in r.text and "PRIVATE KEY" not in r.text, r.text
        google.refuse_key = True                       # Google refuses it: nothing is saved
        r = await c.put("/api/operator/firebase", headers=AUTH, content=json.dumps(KEY_FILE))
        assert r.status_code == 422 and "Invalid JWT Signature" in r.json()["detail"]
        assert (await c.get("/api/operator/firebase", headers=AUTH)).json()["enabled"] is False
        google.refuse_key = False
        r = await c.put("/api/operator/firebase", headers=AUTH, content=json.dumps(KEY_FILE))
        assert r.json() == {"enabled": True, "project_id": PROJECT, "client_email": EMAIL}
        got = await c.get("/api/operator/firebase", headers=AUTH)
        assert got.json() == {"enabled": True, "project_id": PROJECT, "client_email": EMAIL}
        for text in (r.text, got.text):
            assert "PRIVATE KEY" not in text and "0123abcd" not in text
        # the app may wrap the file: {"service_account": <file text>}
        assert (await c.put("/api/operator/firebase", headers=AUTH, json={"service_account": json.dumps(KEY_FILE)})).json()["enabled"] is True
        raw = (tmp_path / "a.db").read_bytes()
        assert PEM.splitlines()[1].encode() not in raw and EMAIL.encode() not in raw      # encrypted at rest
        assert (await c.get("/api/push/status", headers=phone)).json()["mode"] == "local"
        assert (await c.get("/broker/push/status")).json() == {"push": True, "apns": False}
        assert (await c.delete("/api/operator/firebase", headers=AUTH)).json() == {"enabled": False, "project_id": "", "client_email": ""}
        assert (await c.get("/broker/push/status")).json() == {"push": False, "apns": False}


def test_the_key_file_check_names_fields_not_values():
    assert parse_service_account(json.dumps(KEY_FILE))["project_id"] == PROJECT
    with pytest.raises(PushError, match="client_email"):
        parse_service_account(json.dumps({**KEY_FILE, "client_email": None}))
    with pytest.raises(PushError) as e:
        parse_service_account(json.dumps({**KEY_FILE, "private_key": "-----BEGIN PRIVATE KEY-----\nsecretbits\n-----END PRIVATE KEY-----"}))
    assert "secretbits" not in str(e.value)


def test_a_push_carries_ids_only():
    assert check_data({"type": "task_done", "id": 3, "task_id": 9, "approval_id": None}) == {"type": "task_done", "id": "3", "task_id": "9"}
    for bad in ({"type": "task_done", "id": "1", "title": "끝났어요"}, {"type": "hello", "id": "1"}, {"type": "task_done"},
                {"type": "task_done", "id": "1 2"}, {"type": "task_done", "id": "1", "body": "x"}, "text"):
        with pytest.raises(PushError):
            check_data(bad)


# ---------------------------------------------------------------------------------------------------------- the operator's own copy
async def test_the_operators_copy_pushes_straight_to_fcm_only_for_replies_nobody_watched(tmp_path, monkeypatch):
    google = FakeGoogle()
    app = make_app(tmp_path, google, stream_fn=answering("비밀 결과: 계좌 잔액 1,234,567원"))
    running = Running(app)                      # uvicorn sets up its loggers here; listen to them afterwards
    with Logs() as logs:
        async with running as srv, httpx.AsyncClient(base_url=srv.url) as c:
            await c.put("/api/operator/firebase", headers=AUTH, content=json.dumps(KEY_FILE))
            _, phone = await pair(c)
            c.headers.update(phone)                     # the phone follows its replies (test_server.follow uses the client's headers)
            await c.post("/api/push/register", headers=phone, json={"token": PHONE_A, "platform": "android"})
            # a reply followed in the chat: no notification, no push
            await follow(c, (await c.post("/api/turns", headers=phone, json={"text": "내 계좌 잔액 알려줘"})).json()["id"])
            await asyncio.sleep(0.05)
            assert google.sent == []
            # a reply nobody followed: a notification, and a push with ids only
            monkeypatch.setattr("assistant.server.WATCHED_SECONDS", 0.0)
            await c.post("/api/turns", headers=phone, json={"text": "내 계좌 잔액 알려줘"})
            assert await until(lambda: len(google.sent) == 1)
            note = (await c.get("/api/notifications", headers=phone)).json()["items"][0]
            msg = google.sent[0]["message"]
            assert msg == {"token": PHONE_A, "android": {"priority": "HIGH"},
                           "data": {"type": "task_done", "id": str(note["id"]), "task_id": str(note["task_id"])}}
            assert "notification" not in msg and all(isinstance(v, str) for v in msg["data"].values())
            assert "잔액" not in json.dumps(google.sent, ensure_ascii=False)
            # the OAuth token is minted once and reused (~55 minutes)
            await c.post("/api/turns", headers=phone, json={"text": "한 번 더"})
            assert await until(lambda: len(google.sent) == 2)
            assert google.mints == 2           # one for checking the key on upload, one for sending; the second push reused it
    assert PHONE_A not in logs.text and "잔액" not in logs.text and "ya29." not in logs.text and "PRIVATE" not in logs.text
    assert "push: task_done to 1 device(s)" in logs.text


async def test_auto_payment_pushes_only_when_the_reply_is_not_followed(tmp_path, monkeypatch):
    async def pays(args):
        await emit_ui({"type": "payment_auto", "payment": {"id": 7, "amount_text": "12,000원", "site": "shop.example.com", "summary": "원두"},
                       "card": {"id": "c", "label": "생활비", "brand": "visa", "last4": "4242"}})
        return "paid"

    def script():
        it = iter([[ToolCall("c1", "pay_auto", "{}"), Done("tool_calls")], [TextDelta("결제했어요."), Done("stop")]] * 2)

        async def fn(provider, request, **kw):
            for ev in next(it):
                yield ev
        return fn

    google = FakeGoogle()
    app = make_app(tmp_path, google, stream_fn=script(), tools=[Tool("pay_auto", "x", {"type": "object"}, pays)])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        await c.put("/api/operator/firebase", headers=AUTH, content=json.dumps(KEY_FILE))
        _, phone = await pair(c)
        c.headers.update(phone)
        await c.post("/api/push/register", headers=phone, json={"token": PHONE_A})
        await follow(c, (await c.post("/api/turns", headers=phone, json={"text": "원두 사줘"})).json()["id"])
        await asyncio.sleep(0.05)
        assert (await c.get("/api/notifications", headers=phone)).json()["items"][0]["type"] == "payment_auto"   # still in the feed
        assert google.sent == []                                                                                # but no push
        monkeypatch.setattr("assistant.server.WATCHED_SECONDS", 0.0)
        await follow(c, (await c.post("/api/turns", headers=phone, json={"text": "원두 사줘"})).json()["id"])
        assert await until(lambda: any(m["message"]["data"]["type"] == "payment_auto" for m in google.sent))
        assert "원두" not in json.dumps(google.sent, ensure_ascii=False) and "12,000" not in json.dumps(google.sent)


# ---------------------------------------------------------------------------------------------------------- another copy, via the broker
def through(op_url, google):
    async def fetch(method, url, *, allowed_hosts, headers=None, content=None, **kw):
        assert urlsplit(url).hostname in allowed_hosts
        if url.startswith(BROKER + "/broker/"):
            async with httpx.AsyncClient(base_url=op_url) as hop:
                r = await hop.request(method, url.removeprefix(BROKER), headers=headers, content=content)
            return Reply(r.status_code, dict(r.headers), r.content, False)
        return await google.fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content, **kw)
    return fetch


async def test_another_copy_pushes_through_the_broker_and_drops_tokens_fcm_calls_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.WATCHED_SECONDS", 0.0)
    google = FakeGoogle()
    op_app = make_app(tmp_path, google, name="op")
    op_run = Running(op_app)
    friend = make_app(tmp_path, google, name="friend", broker=BROKER, fetcher=through(op_run.url, google))
    fr_run = Running(friend)
    with Logs() as logs:
        async with op_run as op, httpx.AsyncClient(base_url=op.url) as oc:
            await oc.put("/api/operator/firebase", headers=AUTH, content=json.dumps(KEY_FILE))
            async with fr_run as fr, httpx.AsyncClient(base_url=fr.url) as c:
                phones = []
                for tok in (PHONE_A, PHONE_B, PHONE_C):
                    _, h = await pair(c)
                    await c.post("/api/push/register", headers=h, json={"token": tok, "platform": "android"})
                    phones.append(h)
                st = (await c.get("/api/push/status", headers=phones[0])).json()
                assert st == {"enabled": True, "registered": True, "mode": "broker", "broker_reachable": True, "broker_push": True}
                google.gone.add(PHONE_B)
                google.bad.add(PHONE_C)
                google.flaky[PHONE_A] = 2                                  # FCM is busy twice; the broker retries
                await c.post("/api/turns", headers=phones[0], json={"text": "정리해줘"})
                assert await until(lambda: friend.state.push.tokens() == [PHONE_A])
                ok = [m for m in google.sent if m["message"]["token"] == PHONE_A]
                assert len(ok) == 3 and ok[-1]["message"]["data"]["type"] == "task_done"
                assert "정리" not in json.dumps(google.sent, ensure_ascii=False)
                # the broker kept only the hash of the copy's install secret, and nothing of the push
                install = friend.state.push.install_id()
                rows = op_app.state.push_broker.store._all("SELECT * FROM push_installs")
                assert len(rows) == 1 and rows[0]["owner"] != install and rows[0]["requests"] == 1
                assert install.encode() not in (tmp_path / "friend.db").read_bytes()          # sealed on the copy too
                assert install not in json.dumps(rows)
    assert "push: task_done to 3 device(s) (broker), 1 delivered, 2 stale token(s) dropped" in logs.text
    assert "push broker: task_done to 3 device(s), 1 delivered, 2 invalid" in logs.text
    for secret in (PHONE_A, PHONE_B, PHONE_C, install, "ya29."):
        assert secret not in logs.text


async def test_the_copy_retries_a_busy_broker_with_backoff_and_gives_up_quietly(tmp_path):
    google = FakeGoogle()
    st = Store(tmp_path / "c.db")
    calls, waits = [], []
    answers = [j({"detail": "busy"}, 503), j({"detail": "slow down"}, 429, {"Retry-After": "7"}), None]

    async def broker(method, url, *, allowed_hosts, headers=None, content=None):
        body = json.loads(content)
        calls.append(body)
        a = answers[len(calls) - 1] if len(calls) <= len(answers) else None
        if a is None:
            return j({"results": [{"ok": True, "invalid": False} for _ in body["tokens"]]})
        return a

    async def sleep(s):
        waits.append(s)

    client = PushClient(st, Fcm(st, fetcher=google.fetch), broker_url=BROKER, fetcher=broker, backoff=(1.0, 4.0, 16.0), sleep=sleep)
    tokens = []
    for i in range(12):                                   # 12 phones: two requests of at most 10 tokens
        st._run("INSERT INTO devices(name, token_hash, created_at) VALUES(?,?,0)", (f"p{i}", f"h{i}"))
        tok = f"tok{i:02d}:APA91b" + "x" * 30
        client.register(i + 1, tok, "android")
        tokens.append(tok)
    out = await client.deliver({"type": "schedule_disabled", "id": "5", "schedule_id": "2"})
    assert out == {"devices": 12, "delivered": 12, "dropped": 0}
    assert waits == [1.0, 7.0] and [len(c["tokens"]) for c in calls] == [10, 10, 10, 2]
    assert all(c["data"] == {"type": "schedule_disabled", "id": "5", "schedule_id": "2"} for c in calls)
    assert len({c["install_id"] for c in calls}) == 1
    # a broker that stays down: given up after the backoff steps, nothing dropped
    answers[:] = [j({}, 502)] * 10
    calls.clear(), waits.clear()
    assert (await client.deliver({"type": "task_done", "id": "6"}))["delivered"] == 0
    assert waits == [1.0, 4.0, 16.0] * 2 and len(client.tokens()) == 12
    # 404 = the broker does not send pushes: no retries
    answers[:] = [j({"detail": "off"}, 404)] * 10
    calls.clear(), waits.clear()
    await client.deliver({"type": "task_done", "id": "7"})
    assert len(calls) == 2 and waits == []           # one try per batch


# ---------------------------------------------------------------------------------------------------------- the broker's limits
class Tick:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def test_the_broker_limits_each_install_each_address_new_installs_and_tokens_per_request(tmp_path, monkeypatch):
    google = FakeGoogle()
    st = Store(tmp_path / "op.db")
    fcm = Fcm(st, fetcher=google.fetch, backoff=0)
    await fcm.configure(json.dumps(KEY_FILE))
    tick = Tick()
    b = PushBroker(st, fcm, clock=tick)
    monkeypatch.setattr(push_mod, "PER_INSTALL_HOUR", 3)
    monkeypatch.setattr(push_mod, "PER_IP_HOUR", 8)
    monkeypatch.setattr(push_mod, "NEW_INSTALLS_PER_IP_DAY", 2)
    data = {"type": "task_done", "id": "1"}
    one, two = "I" * 40, "J" * 40
    for _ in range(3):
        assert (await b.handle({"install_id": one, "tokens": [PHONE_A], "data": data}, "1.2.3.4"))["results"] == [{"ok": True, "invalid": False}]
    with pytest.raises(PushError) as e:
        await b.handle({"install_id": one, "tokens": [PHONE_A], "data": data}, "1.2.3.4")
    assert e.value.status == 429 and 0 < e.value.retry_after <= 3601
    assert (await b.handle({"install_id": two, "tokens": [PHONE_A], "data": data}, "1.2.3.4"))["results"][0]["ok"]   # another copy is fine
    with pytest.raises(PushError) as e:                    # a third new install from the same address in a day
        await b.handle({"install_id": "K" * 40, "tokens": [PHONE_A], "data": data}, "1.2.3.4")
    assert e.value.status == 429
    tick.t += 3601                                          # an hour later the install may send again
    assert (await b.handle({"install_id": one, "tokens": [PHONE_A], "data": data}, "1.2.3.4"))["results"][0]["ok"]
    for _ in range(7):                                      # the per-address limit counts every request, good or bad
        try:
            await b.handle({"install_id": "short", "tokens": [PHONE_A], "data": data}, "9.9.9.9")
        except PushError as err:
            assert err.status == 403
    with pytest.raises(PushError) as e:
        await b.handle({"install_id": "short", "tokens": [PHONE_A], "data": data}, "9.9.9.9")
    with pytest.raises(PushError) as e:
        await b.handle({"install_id": "short", "tokens": [PHONE_A], "data": data}, "9.9.9.9")
    assert e.value.status == 429
    with pytest.raises(PushError) as e:
        await b.handle({"install_id": two, "tokens": [PHONE_A] * 11, "data": data}, "5.5.5.5")
    assert e.value.status == 422 and "10" in str(e.value)
    with pytest.raises(PushError) as e:
        await b.handle({"install_id": two, "tokens": [PHONE_A], "data": {**data, "body": "잔액 1,000원"}}, "5.5.5.5")
    assert e.value.status == 422 and "잔액" not in str(e.value)


async def test_the_broker_over_http_answers_per_token_and_never_echoes_tokens(tmp_path):
    google = FakeGoogle()
    app = make_app(tmp_path, google, name="op")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        body = {"install_id": "Z" * 40, "tokens": [PHONE_A, PHONE_B, "bad token"], "data": {"type": "approval_waiting", "id": "4", "approval_id": "ab12"}}
        assert (await c.post("/broker/push/send", json=body)).status_code == 404                 # no key yet: push is off here
        await c.put("/api/operator/firebase", headers=AUTH, content=json.dumps(KEY_FILE))
        google.gone.add(PHONE_B)
        r = await c.post("/broker/push/send", json=body)
        assert r.json() == {"results": [{"ok": True, "invalid": False}, {"ok": False, "invalid": True, "error": "UNREGISTERED"},
                                        {"ok": False, "invalid": True, "error": "INVALID_ARGUMENT"}]}
        assert len(google.sent) == 2                                                          # a malformed token never reaches FCM
        bad = await c.post("/broker/push/send", json={**body, "install_id": "nope"})
        assert bad.status_code == 403 and PHONE_A not in bad.text
        bad = await c.post("/broker/push/send", json={**body, "tokens": [PHONE_A] * 11})
        assert bad.status_code == 422 and PHONE_A not in bad.text
        assert (await c.post("/broker/push/send", content=b"{not json")).status_code == 422
        # a revoked OAuth token is replaced once, then the push goes through
        google.valid.clear()
        r = await c.post("/broker/push/send", json={**body, "tokens": [PHONE_A]})
        assert r.json()["results"] == [{"ok": True, "invalid": False}]


async def test_without_a_broker_or_a_key_nothing_is_sent(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.WATCHED_SECONDS", 0.0)
    google = FakeGoogle()
    app = make_app(tmp_path, google)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        _, phone = await pair(c)
        assert (await c.post("/api/push/register", headers=phone, json={"token": PHONE_A})).json() == {"ok": True, "push": False}
        await c.post("/api/turns", headers=phone, json={"text": "정리해줘"})
        assert await until(lambda: True)
        await asyncio.sleep(0.2)
        assert (await c.get("/api/notifications", headers=phone)).json()["items"][0]["type"] == "task_done"
        assert google.sent == [] and google.mints == 0


def test_settings_take_the_push_broker_from_the_existing_broker_settings():
    base = {"ASSISTANT_TOKEN": "x" * 20}
    assert Settings.from_env(base).push_broker == "https://kelo.chat"            # nothing set: the operator, like the sign-in broker
    assert Settings.from_env({**base, "ASSISTANT_PUSH_BROKER": "off"}).push_broker == ""   # a copy that wants no operator push
    assert Settings.from_env(base).oauth_broker == "https://kelo.chat"
    assert Settings.from_env({**base, "ASSISTANT_OAUTH_BROKER": "https://a.example"}).push_broker == "https://a.example"
    assert Settings.from_env({**base, "ASSISTANT_OAUTH_BROKER": "https://a.example", "ASSISTANT_KELO_BROKER": "https://b.example"}).push_broker == "https://b.example"
    assert Settings.from_env({**base, "ASSISTANT_KELO_BROKER": "https://b.example", "ASSISTANT_PUSH_BROKER": "https://c.example"}).push_broker == "https://c.example"


# ---------------------------------------------------------------------------------------------------------- iPhones: direct APNs
APNS_KEY = ec.generate_private_key(ec.SECP256R1())
APNS_PEM = APNS_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
KEY_ID, TEAM_ID, TOPIC = "ABC123DEFG", "P6GD4DM9C5", "chat.kelo.app"
IPHONE_A = "a1" * 32
IPHONE_B = "b2" * 32
IPHONE_C = "c3" * 32


def apns_env(tmp_path, **over):
    f = tmp_path / "AuthKey_TEST.p8"
    f.write_bytes(APNS_PEM)
    env = {"KELO_APNS_KEY_FILE": str(f), "KELO_APNS_KEY_ID": KEY_ID, "KELO_APNS_TEAM_ID": TEAM_ID}
    env.update(over)
    return env


class FakeApple:
    """api.push.apple.com and api.sandbox.push.apple.com (HTTP/2 POST /3/device/<token>): checks the provider token's ES256 signature,
    the topic and headers, and answers like APNs does. Nothing reaches the real service."""

    def __init__(self):
        self.sent: list[dict] = []           # {host, token, headers, body}
        self.gone: set[str] = set()          # 410 Unregistered
        self.wrong_env: set[str] = set()     # 400 BadDeviceToken
        self.flaky: dict[str, tuple[int, int]] = {}   # token -> (status, how many times before it goes through)
        self.expired_once = False

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None, **kw):
        parts = urlsplit(url)
        assert parts.hostname in allowed_hosts and parts.hostname in (push_mod.APNS_HOST, push_mod.APNS_SANDBOX_HOST)
        assert kw.get("http2") is True and method == "POST" and parts.path.startswith("/3/device/")
        h = headers or {}
        assert h["apns-topic"] == TOPIC and h["apns-push-type"] == "alert" and h["apns-priority"] == "10"
        scheme, jwt = h["authorization"].split(" ")
        head, claims, sig = jwt.split(".")
        raw = base64.urlsafe_b64decode(sig + "==")
        APNS_KEY.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                                     f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
        assert scheme == "bearer" and json.loads(base64.urlsafe_b64decode(head + "==")) == {"alg": "ES256", "kid": KEY_ID}
        assert json.loads(base64.urlsafe_b64decode(claims + "=="))["iss"] == TEAM_ID
        token = parts.path.rsplit("/", 1)[1]
        if self.expired_once:
            self.expired_once = False
            return j({"reason": "ExpiredProviderToken"}, 403)
        self.sent.append({"host": parts.hostname, "token": token, "headers": h, "body": json.loads(content)})
        if token in self.gone:
            return j({"reason": "Unregistered"}, 410)
        if token in self.wrong_env:
            return j({"reason": "BadDeviceToken"}, 400)
        if self.flaky.get(token, (0, 0))[1]:
            status, n = self.flaky[token]
            self.flaky[token] = (status, n - 1)
            return j({"reason": "ServiceUnavailable" if status == 503 else "TooManyRequests"}, status, {"retry-after": "0"} if status == 429 else None)
        return Reply(200, {"apns-id": "1"}, b"", False)


def make_apns(tmp_path, apple, env=None, **kw):
    return Apns(fetcher=apple.fetch, env=lambda: env if env is not None else apns_env(tmp_path), backoff=0.0, **kw)


async def test_apns_sends_a_generic_alert_with_ids_only_to_the_right_host(tmp_path):
    apple, apns = FakeApple(), None
    apns = make_apns(tmp_path, apple)
    assert apns.enabled()
    got = await apns.send([(IPHONE_A, "production"), (IPHONE_B, "development")], {"type": "task_done", "id": "5", "task_id": "9"})
    assert got == [{"ok": True, "invalid": False}] * 2
    prod, dev = apple.sent
    assert prod["host"] == "api.push.apple.com" and dev["host"] == "api.sandbox.push.apple.com"
    assert prod["body"] == {"aps": {"alert": {"loc-key": "kelo_push_update"}, "sound": "default", "content-available": 1},
                            "type": "task_done", "id": "5", "task_id": "9"}
    assert prod["headers"]["apns-collapse-id"] == "kelo-5" and int(prod["headers"]["apns-expiration"]) > 0


async def test_apns_provider_token_is_cached_for_forty_minutes_then_minted_again(tmp_path):
    now = [1000.0]
    apple = FakeApple()
    apns = make_apns(tmp_path, apple, clock=lambda: now[0])
    seen = []
    for step in (0, 60, 40 * 60 - 1, 40 * 60 + 1):
        now[0] = 1000.0 + step
        await apns.send([(IPHONE_A, "production")], {"type": "task_done", "id": "1"})
        seen.append(apple.sent[-1]["headers"]["authorization"])
    assert seen[0] == seen[1] == seen[2] and seen[3] != seen[0]


async def test_apns_410_and_bad_device_token_are_reported_invalid_and_other_errors_are_not(tmp_path):
    apple = FakeApple()
    apple.gone.add(IPHONE_B)
    apple.wrong_env.add(IPHONE_C)
    apns = make_apns(tmp_path, apple)
    got = await apns.send([(IPHONE_A, "production"), (IPHONE_B, "production"), (IPHONE_C, "production"), ("not-hex", "production")],
                          {"type": "task_done", "id": "1"})
    assert [(r["ok"], r["invalid"]) for r in got] == [(True, False), (False, True), (False, True), (False, True)]
    assert got[1]["error"] == "Unregistered" and len(apple.sent) == 3        # a malformed token never leaves the server


async def test_apns_retries_429_and_5xx_with_backoff_and_gives_up_after_a_few_tries(tmp_path):
    apple = FakeApple()
    apple.flaky[IPHONE_A] = (503, 2)
    apple.flaky[IPHONE_B] = (429, 1)
    apple.flaky[IPHONE_C] = (503, 99)
    waits = []

    async def nap(s):
        waits.append(s)
    apns = make_apns(tmp_path, apple, sleep=nap)
    got = await apns.send([(IPHONE_A, "production"), (IPHONE_B, "production"), (IPHONE_C, "production")], {"type": "task_done", "id": "1"})
    assert [r["ok"] for r in got] == [True, True, False] and got[2] == {"ok": False, "invalid": False, "error": "ServiceUnavailable"}
    assert sum(1 for m in apple.sent if m["token"] == IPHONE_C) == 3 and waits      # 1 try + 2 retries


async def test_apns_mints_again_once_when_apple_calls_the_provider_token_expired(tmp_path):
    apple = FakeApple()
    apple.expired_once = True
    got = await make_apns(tmp_path, apple).send([(IPHONE_A, "production")], {"type": "task_done", "id": "1"})
    assert got == [{"ok": True, "invalid": False}]


async def test_apns_is_off_without_its_variables_or_with_a_missing_key_file(tmp_path):
    apple = FakeApple()
    for env in ({}, {"KELO_APNS_KEY_ID": KEY_ID, "KELO_APNS_TEAM_ID": TEAM_ID}, apns_env(tmp_path, KELO_APNS_TEAM_ID=""),
                apns_env(tmp_path, KELO_APNS_KEY_FILE=str(tmp_path / "nope.p8"))):
        apns = make_apns(tmp_path, apple, env=env)
        assert not apns.enabled()
        with pytest.raises(PushError) as e:
            await apns.send([(IPHONE_A, "production")], {"type": "task_done", "id": "1"})
        assert e.value.status == 404
    assert apple.sent == []
    # a file that is not a key is reported without its text
    bad = tmp_path / "bad.p8"
    bad.write_text("-----BEGIN PRIVATE KEY-----\nsecretbits\n-----END PRIVATE KEY-----\n")
    with pytest.raises(PushError) as e:
        await make_apns(tmp_path, apple, env=apns_env(tmp_path, KELO_APNS_KEY_FILE=str(bad))).send([(IPHONE_A, "production")], {"type": "task_done", "id": "1"})
    assert "secretbits" not in str(e.value)


async def test_an_iphone_registers_its_apns_token_and_unregisters(tmp_path):
    app = make_app(tmp_path, FakeGoogle())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        push = app.state.push
        dev, phone = await pair(c, "iPhone")
        up = IPHONE_A.upper()
        r = await c.post("/api/push/register", headers=phone, json={"token": up, "platform": "ios-apns", "environment": "development"})
        assert r.json() == {"ok": True, "push": False} and IPHONE_A not in r.text
        assert push.apns_tokens() == [(IPHONE_A, "development")] and push.tokens() == []      # lowercase hex, kept with its environment
        for bad in ({"token": "zz" * 32, "platform": "ios-apns"}, {"token": IPHONE_B, "platform": "ios-apns", "environment": "staging"},
                    {"token": IPHONE_B, "platform": "ios"}, {"token": PHONE_A, "platform": "ios-apns"}):
            assert (await c.post("/api/push/register", headers=phone, json=bad)).status_code == 422
        await c.post("/api/push/register", headers=phone, json={"token": IPHONE_B, "platform": "ios-apns"})      # token refresh replaces it
        assert push.apns_tokens() == [(IPHONE_B, "production")]
        assert (await c.delete("/api/push/register", headers=phone)).json()["removed"] is True
        assert push.apns_tokens() == []
        await c.post("/api/push/register", headers=phone, json={"token": IPHONE_C, "platform": "ios-apns"})
        assert (await c.delete(f"/api/devices/{dev}", headers=AUTH)).json() == {"ok": True}
        assert push.apns_tokens() == []                                                           # a removed device loses its token


async def test_the_operators_copy_pushes_iphones_without_text_and_drops_dead_tokens(tmp_path, monkeypatch):
    for k, v in apns_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("assistant.server.WATCHED_SECONDS", 0.0)
    apple = FakeApple()
    apple.gone.add(IPHONE_B)
    app = make_app(tmp_path, FakeGoogle(), stream_fn=answering("비밀 결과: 계좌 잔액 1,234,567원"), fetcher=apple.fetch)
    running = Running(app)
    with Logs() as logs:
        async with running as srv, httpx.AsyncClient(base_url=srv.url) as c:
            _, p1 = await pair(c, "iPhone 1")
            _, p2 = await pair(c, "iPhone 2")
            await c.post("/api/push/register", headers=p1, json={"token": IPHONE_A, "platform": "ios-apns", "environment": "development"})
            await c.post("/api/push/register", headers=p2, json={"token": IPHONE_B, "platform": "ios-apns"})
            assert (await c.get("/api/push/status", headers=p1)).json() == {"enabled": True, "registered": True, "mode": "local"}
            assert (await c.get("/broker/push/status")).json() == {"push": True, "apns": True}
            await c.post("/api/turns", headers=p1, json={"text": "내 계좌 잔액 알려줘"})
            assert await until(lambda: len(apple.sent) == 2)
            assert await until(lambda: app.state.push.apns_tokens() == [(IPHONE_A, "development")])      # 410: the dead token is gone
            assert {m["host"] for m in apple.sent} == {"api.push.apple.com", "api.sandbox.push.apple.com"}
            note = (await c.get("/api/notifications", headers=p1)).json()["items"][0]
            for m in apple.sent:
                assert set(m["body"]) == {"aps", "type", "id", "task_id"} and m["body"]["id"] == str(note["id"])
            assert "잔액" not in json.dumps(apple.sent, ensure_ascii=False)
    assert IPHONE_A not in logs.text and "BEGIN" not in logs.text and "잔액" not in logs.text and "eyJ" not in logs.text


async def test_another_copy_pushes_iphones_through_the_broker(tmp_path, monkeypatch):
    for k, v in apns_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("assistant.server.WATCHED_SECONDS", 0.0)
    apple, google = FakeApple(), FakeGoogle()
    fetch_apple = apple.fetch
    op_app = make_app(tmp_path, google, name="op", fetcher=fetch_apple)
    op_run = Running(op_app)
    friend = make_app(tmp_path, google, name="friend", broker=BROKER, fetcher=through(op_run.url, google))
    friend.state.apns.env = lambda: {}                   # only the operator's copy has the key (the variables are process-wide in a test)
    fr_run = Running(friend)
    async with op_run, fr_run as fr, httpx.AsyncClient(base_url=fr.url) as c:
        # the broker holds no Firebase key, only the APNs key: the friend's copy sees a broker that sends
        _, p1 = await pair(c, "iPhone")
        _, p2 = await pair(c, "old iPhone")
        await c.post("/api/push/register", headers=p1, json={"token": IPHONE_A, "platform": "ios-apns"})
        await c.post("/api/push/register", headers=p2, json={"token": IPHONE_B, "platform": "ios-apns", "environment": "development"})
        apple.gone.add(IPHONE_B)
        out = await friend.state.push.deliver({"type": "task_done", "id": "7", "task_id": "3"})
        assert out == {"devices": 2, "delivered": 1, "dropped": 1}
        assert {m["token"] for m in apple.sent} == {IPHONE_A, IPHONE_B}
        assert friend.state.push.apns_tokens() == [(IPHONE_A, "production")]
        # a broker without the APNs key says DISABLED: nothing is dropped, the iPhone keeps polling
        monkeypatch.delenv("KELO_APNS_KEY_FILE")
        await c.post("/api/push/register", headers=p2, json={"token": IPHONE_C, "platform": "ios-apns"})
        out = await friend.state.push.deliver({"type": "task_done", "id": "8"})
        assert out["delivered"] == 0 and out["dropped"] == 0 and len(friend.state.push.apns_tokens()) == 2


def test_every_notification_type_the_apps_know_can_be_pushed():
    """A notification type the apps know but the push refused never reached a locked phone (2026-11-14: tester sign-ups, updates)."""
    from assistant.push import NOTE_TYPES, WIRE_TYPES, check_data
    for kind in ("beta_request", "cli_update", "server_update", "keyguard_locked", "task_done", "approval_waiting", "storage"):
        assert kind in NOTE_TYPES or kind in WIRE_TYPES
        check_data({"type": WIRE_TYPES.get(kind, kind), "id": "1"})
