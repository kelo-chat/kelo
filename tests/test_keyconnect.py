"""Keys and tokens made on a service, Zoom server-to-server apps, Google service accounts and Microsoft (Outlook) connections, against
pretend services. No real service is contacted."""

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import assistant.keyconnect as keyconnect
from assistant.keyconnect import KeyConnections, KeyError_
from assistant.netguard import Reply
from assistant.oauth_connect import DeviceFlows
from assistant.presets import KEYS, KeyPreset
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.connect_tools import connect_tools

from test_extract import CONTENT
from test_server import AUTH, TOKEN, Running, scripted

GOOD = "slack-xoxp-GOODtoken123"


@pytest.fixture(autouse=True)
def fresh_cache():
    keyconnect._minted.clear()


def j(obj, status=200):
    return Reply(status, {"content-type": "application/json"}, json.dumps(obj).encode(), False)


class FakeApis:
    def __init__(self):
        self.calls = []
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None):
        headers = headers or {}
        host = urlsplit(url).hostname
        assert host in allowed_hosts, (host, allowed_hosts)
        self.calls.append((method, url, headers, content))
        auth = headers.get("Authorization", "")
        if host == "app.asana.com":
            return j({"data": {"name": "me"}}) if auth == "Bearer asana-good-token" else j({"errors": []}, 401)
        if host == "slack.com":
            return j({"ok": auth == f"Bearer {GOOD}"})                         # Slack says 200 either way
        if host == "api.acme-travel.com":
            assert headers.get("Acme-Version") == "v2"                          # the header the service needs is added by the server
            return j({"data": []})
        if host == "acme.myshopify.com":
            return j({"shop": {}}) if headers.get("X-Shopify-Access-Token") == "shpat_good123" else j({}, 401)
        if host == "zoom.us":
            q = parse_qs(urlsplit(url).query)
            ok = auth == "Basic " + base64.b64encode(b"cid-1:zsecret-1").decode() and q == {"grant_type": ["account_credentials"], "account_id": ["acct-1"]}
            return j({"access_token": "zoom-short", "expires_in": 3600}) if ok else j({"reason": "Invalid client"}, 400)
        if host == "api.zoom.us":
            return j({"id": "me"}) if auth == "Bearer zoom-short" else j({}, 401)
        if host == "oauth2.googleapis.com":
            form = parse_qs(content.decode())
            head, claims, sig = form["assertion"][0].split(".")
            pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
            self.key.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
            c = json.loads(base64.urlsafe_b64decode(pad(claims)))
            assert c["iss"] == "bot@proj-1.iam.gserviceaccount.com" and c["aud"] == "https://oauth2.googleapis.com/token" and "calendar" in c["scope"]
            return j({"access_token": "google-short", "expires_in": 3600})
        if host in ("www.googleapis.com", "sheets.googleapis.com"):
            return j({"items": []}) if auth == "Bearer google-short" else j({}, 401)
        return j({}, 404)

    def sa_json(self):
        pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        return json.dumps({"type": "service_account", "client_email": "bot@proj-1.iam.gserviceaccount.com", "private_key": pem,
                           "token_uri": "https://evil.example/token"})


def app_with(tmp_path, fake):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"),
                               content_dir=str(CONTENT)), tools=None, stream_fn=scripted(), mcp_fetcher=fake.fetch)


async def test_a_key_is_checked_before_it_is_saved_and_never_comes_back(tmp_path):
    fake = FakeApis()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        form = (await c.get("/api/keys/asana")).json()
        assert form["fields"][0]["secret"] and form["help"] and form["checked"] and form["connected"] is False
        bad = await c.post("/api/keys/asana", json={"values": {"token": "asana-bad-token"}})
        assert bad.status_code == 422 and "Nothing was saved" in bad.json()["detail"]
        assert (await c.get("/api/vault")).json()["items"] == []
        ok = await c.post("/api/keys/asana", json={"values": {"token": "asana-good-token"}})
        assert ok.status_code == 200 and ok.json() == {"item": "asana-api", "label": "Asana", "checked": True, "account": ""}
        by = {x["id"]: x for x in (await c.get("/api/connectors")).json()}
        assert by["asana"]["status"] == "connected" and by["asana"]["item"] == "asana-api"
        seen = str((await c.get("/api/vault")).json()) + str((await c.get("/api/keys/asana")).json()) + str((await c.get("/api/vault/log")).json())
        assert "asana-good-token" not in seen
        assert (await c.post("/api/keys/nonsense", json={"values": {}})).status_code == 404
        assert (await c.post("/api/keys/asana", json={"values": {"token": "has a space"}})).status_code == 422


async def test_slack_answers_200_even_for_a_bad_token_so_its_ok_field_decides(tmp_path):
    fake = FakeApis()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/keys/slack", json={"values": {"token": "slack-xoxp-BADtoken123"}})).status_code == 422
        assert (await c.post("/api/keys/slack", json={"values": {"token": GOOD}})).status_code == 200


async def test_fixed_headers_go_with_every_call_and_a_shopify_key_is_bound_to_the_users_own_store(tmp_path, monkeypatch):
    monkeypatch.setitem(KEYS, "acme_travel", KeyPreset(
        "acme_travel", "Acme Travel", "acme-travel-api", ("api.acme-travel.com",), {"type": "bearer_header", "fixed_headers": {"Acme-Version": "v2"}},
        ("help",), "blurb", "api", verify="https://api.acme-travel.com/me"))
    fake = FakeApis()
    st = Store(tmp_path / "s.db")
    k = KeyConnections(st, fetcher=fake.fetch)
    await k.connect("acme_travel", {"token": "acme_test_abc123"})
    tools = {t.name: t for t in connect_tools(st, fetcher=fake.fetch)}
    out = await tools["connector_get"].handler({"name": "acme-travel-api", "url": "https://api.acme-travel.com/air/airlines"})
    assert out.startswith("HTTP 200")
    with pytest.raises(KeyError_):
        await k.connect("shopify", {"store": "evil.example.com", "token": "shpat_good123"})
    with pytest.raises(KeyError_):
        await k.connect("shopify", {"store": "acme.myshopify.com", "token": "shpat_bad1234"})
    made = await k.connect("shopify", {"store": "https://ACME.myshopify.com/", "token": "shpat_good123"})
    assert made["account"] == "acme.myshopify.com" and st.credential("shopify-api")["hosts"] == ["acme.myshopify.com"]


async def test_a_zoom_app_is_exchanged_for_short_tokens_and_the_app_secret_never_leaves_the_server(tmp_path):
    fake = FakeApis()
    st = Store(tmp_path / "s.db")
    k = KeyConnections(st, fetcher=fake.fetch)
    with pytest.raises(KeyError_):
        await k.connect("zoom", {"account_id": "acct-1", "client_id": "cid-1", "client_secret": "wrong-secret"})
    assert st.credentials() == []
    assert (await k.connect("zoom", {"account_id": "acct-1", "client_id": "cid-1", "client_secret": "zsecret-1"}))["checked"]
    keyconnect._minted.clear()
    out = await {t.name: t for t in connect_tools(st, fetcher=fake.fetch)}["connector_get"].handler({"name": "zoom-api", "url": "https://api.zoom.us/v2/users/me"})
    assert out.startswith("HTTP 200") and "zsecret-1" not in out
    sent = [h.get("Authorization") for m, u, h, c in fake.calls if urlsplit(u).hostname == "api.zoom.us"]
    assert sent[-1] == "Bearer zoom-short"                                       # the short token, never the app secret


async def test_a_google_service_account_signs_its_own_tokens_and_the_token_address_in_the_file_is_ignored(tmp_path):
    fake = FakeApis()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.post("/api/keys/google", json={"values": {"key": "{\"type\": \"user\"}"}})).status_code == 422
        r = await c.post("/api/keys/google", json={"values": {"key": fake.sa_json()}})
        assert r.status_code == 200 and r.json()["account"] == "bot@proj-1.iam.gserviceaccount.com"     # the address to share calendars and files with
        assert not any("evil.example" in u for m, u, h, c in fake.calls)
        by = {x["id"]: x["status"] for x in (await c.get("/api/connectors")).json()}
        assert by["google_calendar"] == by["google_sheets"] == by["google_drive"] == "connected"
        assert "google_contacts" not in by                                       # no browser in this test, and a service account cannot read contacts: not listed
        raw = (tmp_path / "s.db").read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("s.db-*"))
        assert b"PRIVATE KEY" not in raw
    st = Store(tmp_path / "s.db")
    keyconnect._minted.clear()
    out = await {t.name: t for t in connect_tools(st, fetcher=fake.fetch)}["connector_get"].handler(
        {"name": "google-sa", "url": "https://www.googleapis.com/calendar/v3/users/me/calendarList"})
    assert out.startswith("HTTP 200") and "PRIVATE" not in out


async def test_outlook_is_connected_with_a_code_and_its_hour_long_token_is_renewed(tmp_path, monkeypatch):
    import time
    import assistant.oauth_connect as oauth
    monkeypatch.setattr(oauth, "MIN_INTERVAL", 0.0)
    calls = []

    async def post(url, data):
        calls.append((url, dict(data)))
        if url.endswith("/devicecode"):
            return {"device_code": "ms-dev", "user_code": "ABCD1234", "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900, "interval": 0.01}
        return {"access_token": "ms-at-1", "refresh_token": "ms-rt-1", "expires_in": 3600, "token_type": "Bearer"}

    async def get(url, token):
        return {"userPrincipalName": "me@outlook.com"}

    st = Store(tmp_path / "s.db")
    flows = DeviceFlows(st, post_form=post, get_json=get)
    flows.set_client_id("microsoft", "11111111-2222-3333-4444-555555555555")
    flow = await flows.start("microsoft")
    assert flow.verification_uri == "https://microsoft.com/devicelogin" and "Mail.ReadWrite" in calls[0][1]["scope"]
    for _ in range(100):
        if flow.status != "pending":
            break
        await asyncio.sleep(0.02)
    assert flow.status == "connected"
    item = st.credential("microsoft-api")
    assert item["label"] == "Microsoft · me@outlook.com" and item["hosts"] == ["graph.microsoft.com"] and "ms-rt-1" in item["secret"]
    renewed = []

    async def graph(method, url, *, allowed_hosts, headers=None, content=None):
        if "login.microsoftonline.com" in url:
            renewed.append(parse_qs(content.decode()))
            return j({"access_token": "ms-at-2", "expires_in": 3600})
        return j({"value": []}) if (headers or {}).get("Authorization") == "Bearer ms-at-2" else j({}, 401)

    b = json.loads(item["secret"])
    b["expires_at"] = time.time() - 1
    st.set_credential_secret("microsoft-api", json.dumps(b))
    out = await {t.name: t for t in connect_tools(st, fetcher=graph)}["connector_get"].handler({"name": "microsoft-api", "url": "https://graph.microsoft.com/v1.0/me/messages"})
    assert out.startswith("HTTP 200") and renewed[0]["refresh_token"] == ["ms-rt-1"] and "resource" not in renewed[0]
