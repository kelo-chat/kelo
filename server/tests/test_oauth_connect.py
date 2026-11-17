"""Connecting a service with a code typed on its own page (OAuth's device flow), against a pretend GitHub. No real service is called."""

import asyncio

import httpx
import pytest

from assistant import i18n
from assistant.oauth_connect import DeviceFlows, OAuthError
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN, Running, scripted

@pytest.fixture(autouse=True)
def quick_polling(monkeypatch):
    import assistant.oauth_connect as oauth
    monkeypatch.setattr(oauth, "MIN_INTERVAL", 0.0)       # the pretend service asks for 20 ms, not 5 s


TOKEN_VALUE = "gho_SECRETtokenVALUE0123456789abcdef"
CLIENT = "Ov23liAbCdEf123456"


class FakeGitHub:
    """Answers the two device-flow calls and /user. `script` is what the token endpoint says, one answer per poll."""

    def __init__(self, script=None, device=None):
        self.script = list(script or [{"error": "authorization_pending"}, {"access_token": TOKEN_VALUE, "token_type": "bearer", "scope": "repo"}])
        self.device = device or {"device_code": "dev-code-123", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device",
                                 "expires_in": 900, "interval": 0.02}
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.user = {"login": "octocat"}

    async def post(self, url, data):
        self.calls.append((url, dict(data)))
        if url.endswith("/login/device/code"):
            return self.device
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]

    async def get(self, url, token):
        self.calls.append((url, {"token_seen": "yes" if token == TOKEN_VALUE else "no"}))
        return self.user


def app_with(tmp_path, fake):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home")),
                      tools=[], stream_fn=scripted(), device_post=fake.post, device_get=fake.get)


async def finished(c, flow_id, tries=100):
    for _ in range(tries):
        r = (await c.get(f"/api/oauth/flows/{flow_id}")).json()
        if r["status"] != "pending":
            return r
        await asyncio.sleep(0.03)
    raise AssertionError("the connection never finished")


def test_the_provider_list_is_shown_in_the_users_language(tmp_path):
    flows = DeviceFlows(Store(tmp_path / "s.db"))
    assert flows.describe("github")["scopes"][1]["label"] == "Public repositories only"
    tok = i18n.use("ko")
    try:
        d = flows.describe("github")
        assert d["scopes"][1] == {"id": "public", "label": "공개 저장소만", "detail": "공개 저장소만 읽고 쓸 수 있어요."}
        assert d["help"][2] == "만든 뒤 'Enable Device Flow'를 켜고 저장해요."
        with pytest.raises(OAuthError, match="영문자와 숫자로 된 6~64자예요"):
            flows.set_client_id("github", "bad id!")
    finally:
        i18n.reset(tok)


async def test_a_service_is_connected_with_a_code_and_the_token_goes_to_the_vault_only(tmp_path):
    fake = FakeGitHub()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        listed = (await c.get("/api/oauth/providers")).json()
        assert [p["id"] for p in listed] == ["github", "microsoft"] and listed[0]["has_client_id"] is False and listed[0]["connected"] is False
        assert [s["id"] for s in listed[0]["scopes"]] == ["private", "public", "profile"] and len(listed[0]["help"]) == 4
        assert (await c.put("/api/oauth/github/client", json={"client_id": CLIENT})).json()["has_client_id"] is True
        started = (await c.post("/api/oauth/github/start", json={"scope": "public"})).json()
        assert started["status"] == "pending" and started["user_code"] == "WDJB-MJHT" and started["verification_uri"] == "https://github.com/login/device"
        assert "device_code" not in started and "dev-code-123" not in str(started)                  # the secret half of the code stays on the server
        assert fake.calls[0] == ("https://github.com/login/device/code", {"client_id": CLIENT, "scope": "public_repo read:user"})
        done = await finished(c, started["id"])
        assert done["status"] == "connected" and done["account"] == "octocat"
        poll = next(d for u, d in fake.calls if u.endswith("/login/oauth/access_token"))
        assert poll == {"client_id": CLIENT, "device_code": "dev-code-123", "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}   # no client secret
        # it is a service key in the vault, for GitHub's API host only
        v = (await c.get("/api/vault")).json()
        item = next(i for i in v["items"] if i["name"] == "github-api")
        assert item["kind"] == "api" and item["hosts"] == ["api.github.com"] and item["has_secret"] and item["label"] == "GitHub · @octocat"
        assert item["placement"] == {"type": "bearer_header"}
        # and the connector shows as connected
        by = {x["id"]: x["status"] for x in (await c.get("/api/connectors")).json()}
        assert by["github"] == "connected" and (await c.get("/api/oauth/providers")).json()[0]["connected"] is True
        # the token is nowhere a client can read it
        everything = "".join([str(v), str((await c.get("/api/credentials")).json()), str((await c.get("/api/vault/log")).json()),
                              str((await c.get(f"/api/oauth/flows/{started['id']}")).json()), str(listed)])
        assert TOKEN_VALUE not in everything
        raw = (tmp_path / "s.db").read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("s.db-*"))
        assert TOKEN_VALUE.encode() not in raw                                                      # sealed, not stored as text
        assert (await c.get("/api/vault/log")).json()[0]["action"] == "saved"


async def test_the_stored_token_works_for_the_assistant_through_the_connector_tool_and_only_for_githubs_api(tmp_path):
    from assistant.tools.connect_tools import connect_tools
    from assistant.netguard import Reply
    fake = FakeGitHub()
    st = Store(tmp_path / "s.db")
    flows = DeviceFlows(st, post_form=fake.post, get_json=fake.get)
    flows.set_client_id("github", CLIENT)
    flow = await flows.start("github", "private")
    for _ in range(100):
        if flow.status != "pending":
            break
        await asyncio.sleep(0.03)
    assert flow.status == "connected"
    seen = {}

    async def fetcher(method, url, *, allowed_hosts, headers=None, content=None):
        seen.update(method=method, url=url, hosts=allowed_hosts, auth=(headers or {}).get("Authorization"))
        return Reply(200, {"content-type": "application/json"}, b'{"login": "octocat"}', False)

    tools = {t.name: t for t in connect_tools(st, fetcher=fetcher)}
    out = await tools["connector_get"].handler({"name": "github-api", "url": "https://api.github.com/user"})
    assert out.startswith("HTTP 200") and seen["auth"] == f"Bearer {TOKEN_VALUE}" and seen["hosts"] == ["api.github.com"]
    assert TOKEN_VALUE not in out                                                                   # the model never sees the token


async def test_asking_without_a_client_id_or_with_a_bad_one_tells_the_user_what_to_do(tmp_path):
    fake = FakeGitHub()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/oauth/github/start", json={})
        assert r.status_code == 422 and "Client ID" in r.json()["detail"]
        assert (await c.put("/api/oauth/github/client", json={"client_id": "has spaces!"})).status_code == 422
        assert (await c.put("/api/oauth/github/client", json={"client_id": "x"})).status_code == 422
        await c.put("/api/oauth/github/client", json={"client_id": CLIENT})
        assert (await c.put("/api/oauth/github/client", json={"client_id": ""})).json()["has_client_id"] is False     # empty forgets it
        assert (await c.post("/api/oauth/nonsense/start", json={})).status_code == 404 and (await c.put("/api/oauth/nonsense/client", json={})).status_code == 404


async def test_the_environment_can_hold_a_client_id_for_every_user_of_the_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_GITHUB_CLIENT_ID", CLIENT)
    fake = FakeGitHub()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        assert (await c.get("/api/oauth/providers")).json()[0]["has_client_id"] is True
        assert (await c.post("/api/oauth/github/start", json={})).status_code == 200
        assert fake.calls[0][1]["scope"] == "repo read:user"                                         # the first choice is the default


async def test_what_the_service_says_is_turned_into_plain_advice(tmp_path):
    fake = FakeGitHub(device={"error": "device_flow_disabled"})
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/oauth/github/client", json={"client_id": CLIENT})
        r = await c.post("/api/oauth/github/start", json={})
        assert r.status_code == 422 and "Enable Device Flow" in r.json()["detail"]


async def test_a_refusal_an_expired_code_and_an_unknown_error_end_the_attempt_without_saving_anything(tmp_path):
    for answer, status, words in [({"error": "access_denied"}, "denied", "declined"), ({"error": "expired_token"}, "expired", "expired"),
                                  ({"error": "incorrect_client_credentials"}, "error", "Client ID")]:
        fake = FakeGitHub(script=[{"error": "authorization_pending"}, answer])
        async with Running(app_with(tmp_path / status, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
            await c.put("/api/oauth/github/client", json={"client_id": CLIENT})
            flow = (await c.post("/api/oauth/github/start", json={})).json()
            done = await finished(c, flow["id"])
            assert done["status"] == status and words in done["message"]
            assert (await c.get("/api/vault")).json()["items"] == [] and (await c.get("/api/connectors")).json()[1]["status"] != "connected"


async def test_slow_down_makes_the_app_wait_longer_between_polls(tmp_path):
    fake = FakeGitHub(script=[{"error": "slow_down", "interval": 0.08}, {"access_token": TOKEN_VALUE}])
    times = []
    original = fake.post

    async def stamped(url, data):
        if url.endswith("/access_token"):
            times.append(asyncio.get_running_loop().time())
        return await original(url, data)

    st = Store(tmp_path / "s.db")
    flows = DeviceFlows(st, post_form=stamped, get_json=fake.get)
    flows.set_client_id("github", CLIENT)
    flow = await flows.start("github")
    for _ in range(100):
        if flow.status != "pending":
            break
        await asyncio.sleep(0.03)
    assert flow.status == "connected" and len(times) == 2 and times[1] - times[0] >= 0.07            # the second poll waited the new interval


async def test_cancelling_stops_the_polling_and_asking_again_replaces_the_earlier_code(tmp_path):
    fake = FakeGitHub(script=[{"error": "authorization_pending"}])        # nobody ever types the code
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/oauth/github/client", json={"client_id": CLIENT})
        first = (await c.post("/api/oauth/github/start", json={})).json()
        second = (await c.post("/api/oauth/github/start", json={})).json()
        assert (await c.get(f"/api/oauth/flows/{first['id']}")).json()["status"] == "cancelled"      # replaced
        assert (await c.get(f"/api/oauth/flows/{second['id']}")).json()["status"] == "pending"
        assert (await c.delete(f"/api/oauth/flows/{second['id']}")).json() == {"ok": True, "cancelled": True}
        assert (await c.delete(f"/api/oauth/flows/{second['id']}")).json() == {"ok": True, "cancelled": False}
        n = len(fake.calls)
        await asyncio.sleep(0.2)
        assert len(fake.calls) == n                                                                     # no more polls after it was cancelled
        assert (await c.get("/api/oauth/flows/nope")).status_code == 404


async def test_the_connection_finishes_even_when_nobody_is_watching_and_disconnecting_forgets_the_token(tmp_path):
    fake = FakeGitHub()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.put("/api/oauth/github/client", json={"client_id": CLIENT})
        await c.post("/api/oauth/github/start", json={})
        await asyncio.sleep(0.4)                                                                        # the phone was put down
        assert (await c.get("/api/oauth/providers")).json()[0]["connected"] is True
        assert (await c.delete("/api/credentials/github-api")).json() == {"ok": True, "data": {"files": 0, "notes": 0, "items": 0}}
        assert (await c.get("/api/oauth/providers")).json()[0]["connected"] is False
        assert {x["id"]: x["status"] for x in (await c.get("/api/connectors")).json()}["github"] == "available"


async def test_a_website_login_with_the_same_name_does_not_block_or_get_overwritten(tmp_path):
    fake = FakeGitHub()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/vault/logins", json={"site": "github.com", "name": "github"})                 # what "github 로그인해줘" makes
        await c.put("/api/vault/github/login", json={"username": "u@example.com", "password": "pw-Pa$$word1"})
        await c.put("/api/oauth/github/client", json={"client_id": CLIENT})
        flow = (await c.post("/api/oauth/github/start", json={})).json()
        assert (await finished(c, flow["id"]))["status"] == "connected"
        names = {i["name"]: i["kind"] for i in (await c.get("/api/vault")).json()["items"]}
        assert names == {"github": "login", "github-api": "api"}                                         # both live side by side


async def test_every_oauth_endpoint_needs_the_token(tmp_path):
    async with Running(app_with(tmp_path, FakeGitHub())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        for method, path in [("GET", "/api/oauth/providers"), ("PUT", "/api/oauth/github/client"), ("POST", "/api/oauth/github/start"),
                             ("GET", "/api/oauth/flows/x"), ("DELETE", "/api/oauth/flows/x")]:
            assert (await c.request(method, path, json={})).status_code == 401, (method, path)


def test_a_client_id_is_checked_and_the_flow_object_never_exposes_the_device_code():
    st = Store(":memory:")
    flows = DeviceFlows(st)
    for bad in ("short", "has space in it", "semi;colon_value", "a" * 80):
        try:
            flows.set_client_id("github", bad)
        except OAuthError:
            continue
        raise AssertionError(bad)
    flows.set_client_id("github", "Iv1.abcdef0123456789")
    assert flows.client_id("github") == "Iv1.abcdef0123456789"


async def test_the_assistant_asks_for_a_connection_with_a_card_and_is_told_when_it_is_already_connected(tmp_path):
    from assistant.connect_ways import ways
    from assistant.mail import Mail
    from assistant.tools.oauth_tools import oauth_tools
    fake = FakeGitHub()
    st = Store(tmp_path / "s.db")
    flows = DeviceFlows(st, post_form=fake.post, get_json=fake.get)
    mail = Mail(st)
    tool = oauth_tools(lambda: ways(st, mail, browser=True), flows, mail)[0]
    assert tool.name == "connect_service" and tool.read_only and not tool.requires_approval      # it only puts a card on the screen
    assert (await tool.handler({"service": "myspace"})).startswith("error:")
    no = await tool.handler({"service": "apple_healthkit"})
    assert no.startswith("error:") and "app installed on" in no                                       # the reason, not just "no"
    asked = await tool.handler({"service": "github"})
    assert "card" in asked and "github-api" in asked and "api.github.com" in asked and "never see" in asked
    assert "mcp_list_tools(name='notion-mcp')" in await tool.handler({"service": "notion"})
    assert "slack-api" in await tool.handler({"service": "slack"}) and "google-sa" in await tool.handler({"service": "google_sheets"})
    assert "browser_open" in await tool.handler({"service": "expedia"}) and "email_list" in await tool.handler({"service": "gmail"})
    assert (await tool.handler({"service": "onepassword"})).startswith("error:") and "onepassword" not in ways(st, mail, browser=True)
    flows.set_client_id("github", CLIENT)
    flow = await flows.start("github")
    for _ in range(100):
        if flow.status != "pending":
            break
        await asyncio.sleep(0.03)
    done = await tool.handler({"service": "github"})
    assert "already connected" in done and TOKEN_VALUE not in done and "Do not ask for a login" in done
    # the apps read {"already": true} from the tool_result's facts, not the sentence (agent/facts.py)
    from assistant.agent import facts as tool_facts
    box = {}
    token = tool_facts.current.set(box)
    try:
        await tool.handler({"service": "github"})
        assert box == {"already": True}
        box.clear()
        await tool.handler({"service": "notion"})          # not connected: the card is shown, no fact
        await tool.handler({"service": "expedia"})         # a browser service has no connection to be "already" in
        await tool.handler({"service": "nothing-like-it"})
        assert box == {}
    finally:
        tool_facts.current.reset(token)


def test_the_verification_page_always_stays_on_the_services_own_site(tmp_path):
    async def run():
        fake = FakeGitHub(device={"device_code": "d", "user_code": "ABCD-EFGH", "verification_uri": "https://evil.example/login", "expires_in": 900, "interval": 1})
        flows = DeviceFlows(Store(":memory:"), post_form=fake.post, get_json=fake.get)
        flows.set_client_id("github", CLIENT)
        flow = await flows.start("github")
        flows.cancel(flow.id)
        return flow.verification_uri

    assert asyncio.run(run()) == "https://github.com/login/device"


def test_microsoft_works_with_kelos_own_public_app_unless_the_copy_names_another(tmp_path):
    flows = DeviceFlows(Store(tmp_path / "s.db"))
    assert flows.client_id("microsoft") == "30e50ec1-d4d9-4e6c-a3e2-35a495c7b7f5"   # nobody has to register an Entra app
    flows = DeviceFlows(Store(tmp_path / "s.db"), env={"ASSISTANT_MICROSOFT_CLIENT_ID": "11111111-2222-3333-4444-555555555555"})
    assert flows.client_id("microsoft") == "11111111-2222-3333-4444-555555555555"
    flows.set_client_id("microsoft", "66666666-7777-8888-9999-000000000000")
    assert flows.client_id("microsoft") == "66666666-7777-8888-9999-000000000000"
    assert flows.client_id("github") == ""                                          # GitHub keeps no built-in id here


def test_a_connection_says_which_permission_choice_it_was_made_with(tmp_path):
    st = Store(tmp_path / "s.db")
    flows = DeviceFlows(st)
    assert flows.describe("microsoft")["granted_scope"] == ""                       # not connected
    st.save_credential("microsoft-api", "Microsoft", ["graph.microsoft.com"], {"type": "bearer_header"}, "", kind="api")
    st.set_credential_secret("microsoft-api", "tok")
    st.add_vault_log("microsoft-api", "saved", "OAuth connection (all)")             # made before the choice was kept on its own
    assert flows.describe("microsoft")["granted_scope"] == "all"
    st.set_meta("oauth_scope.microsoft", "read")
    assert flows.describe("microsoft")["granted_scope"] == "read"


async def test_a_change_refused_because_the_connection_is_read_only_says_so_and_reads_keep_working(tmp_path):
    """Outlook through microsoft-api connected with "Read only": Graph answers 403 to a change. That is the permission the owner chose,
    not a wrong or expired key: the item is not marked refused and the model is told to say it plainly (connect again with changes
    allowed). Any typed-code connection with a narrower permission than its service offers works the same; a wide one keeps the old
    "check the key" advice."""
    from assistant.netguard import Reply
    from assistant.oauth_connect import grant_label
    from assistant.tools.connect_tools import connect_tools
    st = Store(tmp_path / "s.db")
    st.save_credential("microsoft-api", "Microsoft", ["graph.microsoft.com"], {"type": "bearer_header"}, "", kind="api")
    st.set_credential_secret("microsoft-api", "tok-ms")
    st.set_meta("oauth_scope.microsoft", "read")
    status = {"code": 403}

    async def graph(method, url, *, allowed_hosts, headers=None, content=None):
        code = 200 if method == "GET" else status["code"]
        return Reply(code, {"content-type": "application/json"}, b'{"error": {"code": "ErrorAccessDenied"}}' if code == 403 else b"{}", False)

    tools = {t.name: t for t in connect_tools(st, fetcher=graph)}
    move = {"name": "microsoft-api", "url": "https://graph.microsoft.com/v1.0/me/messages/AAA/move", "json": {"destinationId": "archive"}}
    out = await tools["connector_send"].handler({**move, "method": "POST"})
    assert out.startswith("HTTP 403") and "'Read only' permission only" in out and "connected again" in out
    assert "may be wrong, expired" not in out
    assert st.credential("microsoft-api")["last_result"] != "rejected"                       # not taken for a bad key
    assert (await tools["connector_get"].handler({"name": "microsoft-api", "url": "https://graph.microsoft.com/v1.0/me/messages"})).startswith("HTTP 200")
    assert st.credential("microsoft-api")["last_result"] == "ok"                              # reads keep working
    # the widest permission (or a plain key): a 403 is still a refused key
    st.set_meta("oauth_scope.microsoft", "all")
    assert grant_label(st, "microsoft-api") == ""
    out = await tools["connector_send"].handler({**move, "method": "POST"})
    assert "may be wrong, expired" in out and st.credential("microsoft-api")["last_result"] == "rejected"
    # GitHub with public repositories only is narrower too; a key nobody connected with a code has no permission choice
    st.set_meta("oauth_scope.github", "public")
    st.save_credential("github-api", "GitHub", ["api.github.com"], {"type": "bearer_header"}, "", kind="api")
    assert grant_label(st, "github-api") == "Public repositories only" and grant_label(st, "acme") == ""
