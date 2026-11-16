"""Hosted MCP servers connected with OAuth (discovery, automatic registration, PKCE, callback, renewal), against a pretend service.
No real service is contacted."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from assistant import i18n
from assistant.mcp_oauth import McpConnections, McpOAuthError, clean_origin, open_token
from assistant.netguard import Reply
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.connect_tools import connect_tools

from test_extract import CONTENT
from test_server import AUTH, TOKEN, Running, scripted

MCP = "https://mcp.notion.com/mcp"


class FakeService:
    def __init__(self, *, dcr=True, renew_ok=True):
        self.dcr, self.renew_ok = dcr, renew_ok
        self.challenge = ""
        self.registered: list[dict] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.tokens = {"at-1", "at-2"}
        self.issued = 0

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None):
        headers = headers or {}
        host = urlsplit(url).hostname
        assert host in allowed_hosts, (host, allowed_hosts)            # every call is limited to the host it goes to
        self.calls.append((method, url, headers))
        j = lambda obj, status=200, h=None: Reply(status, {"content-type": "application/json", **(h or {})}, json.dumps(obj).encode(), False)  # noqa: E731
        if url == MCP:
            auth = headers.get("Authorization", "")
            if auth.removeprefix("Bearer ") not in self.tokens:
                return j({"error": "unauthorized"}, 401, {"www-authenticate": 'Bearer resource_metadata="https://mcp.notion.com/.well-known/oauth-protected-resource/mcp", scope="read write"'})
            msg = json.loads(content)
            if "id" not in msg:
                return Reply(202, {}, b"", False)
            if msg["method"] == "initialize":
                return j({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}})
            return j({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "search", "description": f"seen with {auth[-4:]}"}]}})
        if url == "https://mcp.notion.com/.well-known/oauth-protected-resource/mcp":
            return j({"resource": MCP, "authorization_servers": ["https://auth.notion.example"], "scopes_supported": ["read"]})
        if url == "https://auth.notion.example/.well-known/oauth-authorization-server":
            meta = {"issuer": "https://auth.notion.example", "authorization_endpoint": "https://auth.notion.example/authorize",
                    "token_endpoint": "https://auth.notion.example/token", "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"]}
            if self.dcr:
                meta["registration_endpoint"] = "https://auth.notion.example/register"
            return j(meta)
        if url == "https://auth.notion.example/register":
            body = json.loads(content)
            self.registered.append(body)
            return j({"client_id": "client-123", **body}, 201)
        if url == "https://auth.notion.example/token":
            form = parse_qs(content.decode())
            if form["grant_type"] == ["authorization_code"]:
                verifier = form["code_verifier"][0]
                ok = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == self.challenge
                if not ok or form["code"] != ["good-code"] or form["client_id"] != ["client-123"]:
                    return j({"error": "invalid_grant"}, 400)
                return j({"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "token_type": "Bearer"})
            if form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["rt-1"] and self.renew_ok:
                return j({"access_token": "at-2", "expires_in": 3600})
            return j({"error": "invalid_grant"}, 400)
        return j({}, 404)


def app_with(tmp_path, fake):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"),
                                     content_dir=str(CONTENT)),
                      tools=None, stream_fn=scripted(), mcp_fetcher=fake.fetch)


async def sign_in(c, fake, origin="https://abc.trycloudflare.com", code="good-code"):
    started = await c.post("/api/mcp-oauth/notion/start", json={"origin": origin})
    assert started.status_code == 200, started.text
    q = parse_qs(urlsplit(started.json()["url"]).query)
    fake.challenge = q["code_challenge"][0]
    return q, await c.get("/oauth/callback", params={"state": q["state"][0], "code": code}, headers={"Authorization": ""})


async def test_a_hosted_mcp_server_is_connected_by_signing_in_on_its_own_page(tmp_path):
    fake = FakeService()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        q, back = await sign_in(c, fake)
        # the sign-in page: the service's own address, with PKCE and the resource, sent back to this app's own address
        assert q["redirect_uri"] == ["https://abc.trycloudflare.com/oauth/callback"] and q["code_challenge_method"] == ["S256"]
        assert q["resource"] == [MCP] and q["scope"] == ["read write"] and q["client_id"] == ["client-123"]
        assert fake.registered[0]["redirect_uris"] == ["https://abc.trycloudflare.com/oauth/callback"] and fake.registered[0]["token_endpoint_auth_method"] == "none"
        # coming back: a plain page, then into the vault
        assert back.status_code == 200 and "Notion connected" in back.text and "package=chat.kelo.app" in back.text
        item = next(i for i in (await c.get("/api/vault")).json()["items"] if i["name"] == "notion-mcp")
        assert item["kind"] == "api" and item["hosts"] == ["mcp.notion.com"] and item["endpoint"] == MCP and item["placement"]["via"] == "oauth"
        by = {x["id"]: x for x in (await c.get("/api/connectors")).json()}
        assert by["notion"]["status"] == "connected" and by["linear"]["status"] == "available"
        everything = str((await c.get("/api/vault")).json()) + str((await c.get("/api/credentials")).json()) + str((await c.get("/api/vault/log")).json()) + back.text
        for secret in ("at-1", "rt-1", fake.challenge):
            assert secret not in everything
        raw = (tmp_path / "s.db").read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("s.db-*"))
        assert b"at-1" not in raw and b"rt-1" not in raw
        # the same state cannot be used twice
        again = await c.get("/oauth/callback", params={"state": q["state"][0], "code": "good-code"})
        assert "expired" in again.text


async def test_the_assistant_uses_it_through_the_mcp_tools_and_the_sign_in_is_renewed_when_it_runs_out(tmp_path):
    fake = FakeService()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await sign_in(c, fake)
    st = Store(tmp_path / "s.db")
    tools = {t.name: t for t in connect_tools(st, fetcher=fake.fetch)}
    out = await tools["mcp_list_tools"].handler({"name": "notion-mcp"})
    assert "search" in out and "at-1" not in out and "seen with" in out
    sent = [h.get("Authorization") for m, u, h in fake.calls if u == MCP and h.get("Authorization")]
    assert sent[-1] == "Bearer at-1"                                         # the access token, not the stored bundle
    # it runs out: renewed with the refresh token before the next call, and the new one is kept
    cred = st.credential("notion-mcp")
    b = json.loads(cred["secret"])
    b["expires_at"] = time.time() - 5
    st.set_credential_secret("notion-mcp", json.dumps(b))
    await tools["mcp_list_tools"].handler({"name": "notion-mcp"})
    assert [h.get("Authorization") for m, u, h in fake.calls if u == MCP][-1] == "Bearer at-2"
    assert json.loads(st.credential("notion-mcp")["secret"])["refresh_token"] == "rt-1"   # kept when the service does not send a new one


async def test_a_refused_renewal_asks_for_a_new_connection_instead_of_failing_silently(tmp_path):
    fake = FakeService(renew_ok=False)
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await sign_in(c, fake)
    st = Store(tmp_path / "s.db")
    b = json.loads(st.credential("notion-mcp")["secret"])
    b["expires_at"] = time.time() - 5
    st.set_credential_secret("notion-mcp", json.dumps(b))
    out = await {t.name: t for t in connect_tools(st, fetcher=fake.fetch)}["mcp_list_tools"].handler({"name": "notion-mcp"})
    assert out.startswith("error:") and "connect it again" in out
    assert st.credentials()[0]["last_result"] == "rejected"


async def test_a_cancelled_or_failed_sign_in_saves_nothing(tmp_path):
    fake = FakeService()
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        started = (await c.post("/api/mcp-oauth/notion/start", json={"origin": "https://abc.trycloudflare.com"})).json()
        state = parse_qs(urlsplit(started["url"]).query)["state"][0]
        denied = await c.get("/oauth/callback", params={"state": state, "error": "access_denied"}, headers={"Accept-Language": "ko-KR,ko;q=0.9"})
        assert "연결 취소" in denied.text and "Notion 연결을 취소했어요. 아무것도 저장하지 않았어요." in denied.text   # the page in the browser's language
        _, bad = await sign_in(c, fake, code="wrong-code")
        assert "get a token" in bad.text
        assert (await c.get("/api/vault")).json()["items"] == []


async def test_services_without_automatic_registration_and_bad_addresses_are_refused(tmp_path):
    fake = FakeService(dcr=False)
    async with Running(app_with(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/mcp-oauth/notion/start", json={"origin": "https://abc.trycloudflare.com"})
        assert r.status_code == 422 and "automatic sign-up" in r.json()["detail"]
        assert (await c.post("/api/mcp-oauth/slack/start", json={"origin": "https://x.example"})).status_code == 404
    for bad in ("http://evil.example", "https://x.example/path", "javascript:alert(1)", "https://u:p@x.example", ""):
        try:
            clean_origin(bad)
        except McpOAuthError:
            continue
        raise AssertionError(bad)
    assert clean_origin("http://localhost:8080/") == "http://localhost:8080" and clean_origin("https://a.b.com") == "https://a.b.com"


async def test_the_start_needs_the_token_but_the_callback_does_not(tmp_path):
    async with Running(app_with(tmp_path, FakeService())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.post("/api/mcp-oauth/notion/start", json={"origin": "https://a.b.com"})).status_code == 401
        r = await c.get("/oauth/callback", params={"state": "made-up", "code": "x"})
        assert r.status_code == 200 and "expired" in r.text                       # a guessed state gets nowhere


def test_a_plain_key_is_left_as_it_is():
    import asyncio
    cred = {"name": "acme", "label": "Acme", "secret": "sk-plain-key-1234"}
    assert asyncio.run(open_token(Store(":memory:"), cred)) is cred


def test_the_catalog_names_vault_items_that_do_not_clash_with_logins():
    from assistant.mcp_oauth import SERVICES
    items = [s.item for s in SERVICES.values()]
    assert len(items) == len(set(items)) == 104 and all(i.endswith("-mcp") for i in items)
    assert all(s.url.startswith("https://") for s in SERVICES.values())
    assert McpConnections(Store(":memory:")).pending == {}
    assert "figma" not in SERVICES and "canva" not in SERVICES                  # Figma refuses unapproved clients; Canva refuses the return address
    import json
    import pathlib
    from assistant.catalog import BY_ID
    assert set(SERVICES) <= set(BY_ID)                                           # the Connectors tab lists only ids in the catalog


async def test_a_service_that_only_takes_approved_apps_says_so_plainly(tmp_path):
    fake = FakeService()
    original = fake.fetch

    async def refusing(method, url, **kw):
        if url == "https://auth.notion.example/register":
            return Reply(403, {"content-type": "application/json"}, b'{"error": "forbidden"}', False)
        return await original(method, url, **kw)

    conn = McpConnections(Store(":memory:"), fetcher=refusing)
    tok = i18n.use("ko")
    try:
        await conn.start("notion", "https://abc.trycloudflare.com")
    except McpOAuthError as e:
        assert str(e) == "Notion가 미리 승인한 앱만 연결을 받아 줘서, 이 앱으로는 연결할 수 없어요."
    else:
        raise AssertionError("a refused registration must be reported")
    finally:
        i18n.reset(tok)


# ---- one fixed return address shared by every copy of the app (the relay page)

async def test_with_a_relay_the_service_returns_to_the_shared_page_which_sends_the_user_on_to_the_right_copy(tmp_path):
    from assistant.mcp_oauth import relay_target
    fake = FakeService()
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"),
                              content_dir=str(CONTENT), oauth_relay="https://me.duckdns.org/oauth/relay"),
                     tools=None, stream_fn=scripted(), mcp_fetcher=fake.fetch)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        started = (await c.post("/api/mcp-oauth/notion/start", json={"origin": "https://friend.trycloudflare.com"})).json()
        q = parse_qs(urlsplit(started["url"]).query)
        assert q["redirect_uri"] == ["https://me.duckdns.org/oauth/relay"]                       # one fixed address for every copy
        assert fake.registered[0]["redirect_uris"] == ["https://me.duckdns.org/oauth/relay"]
        assert relay_target(q["state"][0]) == "https://friend.trycloudflare.com"               # the copy that started it travels in state
        fake.challenge = q["code_challenge"][0]
        # the service sends the browser to the relay; the relay sends it on, unchanged, to that copy's own callback
        hop = await c.get("/oauth/relay", params={"state": q["state"][0], "code": "good-code"}, headers={"Authorization": ""}, follow_redirects=False)
        assert hop.status_code == 302 and hop.headers["location"].startswith("https://friend.trycloudflare.com/oauth/callback?")
        back = parse_qs(urlsplit(hop.headers["location"]).query)
        assert back == {"state": q["state"], "code": ["good-code"]}
        done = await c.get("/oauth/callback", params={"state": q["state"][0], "code": "good-code"})
        assert "Notion connected" in done.text


async def test_the_relay_refuses_states_that_name_no_acceptable_address(tmp_path):
    import base64
    from assistant.mcp_oauth import relay_state, relay_target
    async with Running(app_with(tmp_path, FakeService())) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        for bad in ("", "nonce-only", "abc." + base64.urlsafe_b64encode(b"http://evil.example").decode().rstrip("="),
                    "abc." + base64.urlsafe_b64encode(b"https://x.example/steal?").decode().rstrip("="), "abc.!!!"):
            r = await c.get("/oauth/relay", params={"state": bad, "code": "c"}, follow_redirects=False)
            assert r.status_code == 400 and "location" not in r.headers, bad
    assert relay_target(relay_state("n0nce", "http://localhost:8080")) == "http://localhost:8080"


def test_one_time_sign_in_codes_are_kept_out_of_the_access_log():
    import logging
    from assistant.server import _NoSignInCodes
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", "/oauth/callback?state=s&code=SECRET", "1.1", 200), None)
    _NoSignInCodes().filter(rec)
    assert "SECRET" not in rec.getMessage() and "/oauth/callback?…" in rec.getMessage()
    other = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", "/api/messages?limit=1", "1.1", 200), None)
    _NoSignInCodes().filter(other)
    assert "limit=1" in other.getMessage()


# ---- Client ID Metadata Documents: the client_id is the address of Kelo's public metadata document on the relay host

RELAY = "https://me.duckdns.org/oauth/relay"
DOC = "https://me.duckdns.org/oauth/client-metadata.json"


class FakeCimdService(FakeService):
    """An authorization server that reads metadata documents (cimd), takes registrations (dcr), or both."""

    def __init__(self, *, cimd=True, dcr=False, **kw):
        super().__init__(dcr=dcr, **kw)
        self.cimd = cimd

    async def fetch(self, method, url, *, allowed_hosts, headers=None, content=None):
        if url == "https://auth.notion.example/.well-known/oauth-authorization-server":
            r = await super().fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content)
            meta = json.loads(r.body)
            if self.cimd:
                meta["client_id_metadata_document_supported"] = True
            return Reply(200, {"content-type": "application/json"}, json.dumps(meta).encode(), False)
        if url == "https://auth.notion.example/token" and self.cimd:
            form = parse_qs(content.decode())
            if form["client_id"] == [DOC]:
                assert "client_secret" not in form and "Authorization" not in (headers or {})     # a public client: no secret anywhere
                if form["grant_type"] == ["authorization_code"]:
                    verifier = form["code_verifier"][0]
                    ok = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == self.challenge
                    if ok and form["code"] == ["good-code"] and form["redirect_uri"] == [RELAY]:
                        return Reply(200, {}, json.dumps({"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}).encode(), False)
                if form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["rt-1"]:
                    return Reply(200, {}, json.dumps({"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600}).encode(), False)
                return Reply(400, {}, b'{"error": "invalid_grant"}', False)
        return await super().fetch(method, url, allowed_hosts=allowed_hosts, headers=headers, content=content)


def relay_app(tmp_path, fake, **kw):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"),
                              content_dir=str(CONTENT), oauth_relay=RELAY, **kw), tools=None, stream_fn=scripted(), mcp_fetcher=fake.fetch)


async def test_a_service_that_reads_metadata_documents_gets_the_document_address_as_client_id_and_no_registration(tmp_path):
    fake = FakeCimdService(cimd=True, dcr=False)
    async with Running(relay_app(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        started = await c.post("/api/mcp-oauth/notion/start", json={"origin": "https://friend.trycloudflare.com"})
        assert started.status_code == 200, started.text
        q = parse_qs(urlsplit(started.json()["url"]).query)
        assert q["client_id"] == [DOC] and q["redirect_uri"] == [RELAY] and q["code_challenge_method"] == ["S256"] and q["resource"] == [MCP]
        assert fake.registered == [] and not any(u.endswith("/register") for _, u, _ in fake.calls)
        fake.challenge = q["code_challenge"][0]
        done = await c.get("/oauth/callback", params={"state": q["state"][0], "code": "good-code"})
        assert "Notion connected" in done.text
    st = Store(tmp_path / "s.db")
    b = json.loads(st.credential("notion-mcp")["secret"])
    assert b["registration"] == "cimd" and b["client_id"] == DOC and b["client_secret"] == "" and b["auth_method"] == "none"
    # renewal as a public client too (no secret), keeping the new refresh token the service rotated in
    b["expires_at"] = time.time() - 5
    st.set_credential_secret("notion-mcp", json.dumps(b))
    out = await {t.name: t for t in connect_tools(st, fetcher=fake.fetch)}["mcp_list_tools"].handler({"name": "notion-mcp"})
    assert "search" in out
    renewed = json.loads(st.credential("notion-mcp")["secret"])
    assert renewed["access_token"] == "at-2" and renewed["refresh_token"] == "rt-2" and renewed["registration"] == "cimd"


async def test_a_service_that_offers_both_gets_the_metadata_document_first(tmp_path):
    fake = FakeCimdService(cimd=True, dcr=True)
    conn = McpConnections(Store(":memory:"), fetcher=fake.fetch, relay=RELAY)
    q = parse_qs(urlsplit(await conn.start("notion", "https://friend.trycloudflare.com")).query)
    assert q["client_id"] == [DOC] and fake.registered == []
    assert next(iter(conn.pending.values()))["registration"] == "cimd"
    # without a relay there is no document this copy can name, so it registers as before
    fake2 = FakeCimdService(cimd=True, dcr=True)
    conn2 = McpConnections(Store(":memory:"), fetcher=fake2.fetch)
    q2 = parse_qs(urlsplit(await conn2.start("notion", "https://abc.trycloudflare.com")).query)
    assert q2["client_id"] == ["client-123"] and len(fake2.registered) == 1
    assert next(iter(conn2.pending.values()))["registration"] == "dcr"


async def test_a_service_with_registration_only_is_registered_as_before_and_the_bundle_says_so(tmp_path):
    fake = FakeCimdService(cimd=False, dcr=True)
    async with Running(relay_app(tmp_path, fake)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        q = parse_qs(urlsplit((await c.post("/api/mcp-oauth/notion/start", json={"origin": "https://friend.trycloudflare.com"})).json()["url"]).query)
        assert q["client_id"] == ["client-123"] and fake.registered[0]["redirect_uris"] == [RELAY]
        fake.challenge = q["code_challenge"][0]
        assert "Notion connected" in (await c.get("/oauth/callback", params={"state": q["state"][0], "code": "good-code"})).text
    assert json.loads(Store(tmp_path / "s.db").credential("notion-mcp")["secret"])["registration"] == "dcr"


async def test_a_metadata_document_only_service_needs_a_relay(tmp_path):
    conn = McpConnections(Store(":memory:"), fetcher=FakeCimdService(cimd=True, dcr=False).fetch)
    tok = i18n.use("ko")
    try:
        await conn.start("notion", "https://abc.trycloudflare.com")
    except McpOAuthError as e:
        assert "릴레이" in str(e)
    else:
        raise AssertionError("a metadata-document-only service cannot work without a relay")
    finally:
        i18n.reset(tok)


async def test_the_operator_publishes_the_metadata_document_without_a_token_and_other_copies_do_not(tmp_path):
    from assistant.mcp_oauth import client_metadata_url
    async with Running(relay_app(tmp_path, FakeService(), public_url="https://me.duckdns.org")) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        r = await c.get("/oauth/client-metadata.json")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        doc = r.json()
        assert doc["client_id"] == DOC and doc["client_name"] == "Kelo" and doc["redirect_uris"] == [RELAY]
        assert doc["token_endpoint_auth_method"] == "none" and doc["grant_types"] == ["authorization_code", "refresh_token"]
        assert doc["response_types"] == ["code"] and doc["client_uri"] == "https://me.duckdns.org"
        assert not {"client_secret", "client_secret_expires_at", "jwks"} & set(doc)
    # a copy whose own address is elsewhere uses the operator's document but does not serve one itself
    async with Running(relay_app(tmp_path / "b", FakeService(), public_url="https://friend.example")) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/oauth/client-metadata.json")).status_code == 404
    assert client_metadata_url("https://Me.duckdns.org/oauth/relay") == DOC and client_metadata_url("http://me.example/oauth/relay") == ""
    assert client_metadata_url("") == ""


def test_atlassian_is_named_plainly_everywhere():
    from assistant.catalog import BY_ID
    from assistant.mcp_oauth import SERVICES
    assert BY_ID["atlassian"].name == "Atlassian" and SERVICES["atlassian"].name == "Atlassian"
    assert not any("Jira·Confluence" in v for lang in i18n.available() for v in i18n.catalog(lang).values())
