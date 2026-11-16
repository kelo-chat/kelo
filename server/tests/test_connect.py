import json
from pathlib import Path

import httpx
import pytest

from assistant.agent.fence import plain
from assistant.credentials import apply_secret, clean_placement, clean_secret, redact
from assistant.home import Home
from assistant.netguard import NetGuardError, Reply
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.connect_tools import connect_tools

from test_server import AUTH, TOKEN, Running

SECRET = "sk-live-ABCDEF123456"


class Fake:
    """Stands in for netguard.fetch and records what would have been sent."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def __call__(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r


def reply(body, status=200, ctype="application/json", **h):
    return Reply(status, {"content-type": ctype, **h}, body if isinstance(body, bytes) else body.encode(), False)


def tools_for(store, fake, home=None):
    return {t.name: t for t in connect_tools(store, home, fetcher=fake)}


def connected(store, placement=None, endpoint="", hosts=("api.acme.com",)):
    store.save_credential("acme", "Acme", list(hosts), placement or {"type": "bearer_header"}, endpoint)
    store.set_credential_secret("acme", SECRET)


# ---- placement rules
def test_each_placement_puts_the_key_in_exactly_one_place():
    assert apply_secret("https://h/x", {"type": "bearer_header"}, "KEY12345") == ("https://h/x", {"Authorization": "Bearer KEY12345"})
    assert apply_secret("https://h/x", {"type": "custom_header", "header": "X-Api-Key", "prefix": "Token "}, "KEY12345")[1] == {"X-Api-Key": "Token KEY12345"}
    assert apply_secret("https://h/x?a=1", {"type": "query_param", "param": "key"}, "K E/Y")[0] == "https://h/x?a=1&key=K%20E%2FY"
    assert apply_secret("https://h/bot{secret}/me", {"type": "url_path_segment"}, "12:AB/c")[0] == "https://h/bot12%3AAB%2Fc/me"


def test_the_marker_is_required_for_path_keys_and_refused_elsewhere():
    with pytest.raises(NetGuardError, match=r"\{secret\}"):
        apply_secret("https://h/x", {"type": "url_path_segment"}, "KEY12345")
    with pytest.raises(NetGuardError):
        apply_secret("https://h/x?k={secret}", {"type": "url_path_segment"}, "KEY12345")     # not in the query
    with pytest.raises(NetGuardError):
        apply_secret("https://h/{secret}", {"type": "bearer_header"}, "KEY12345")


@pytest.mark.parametrize("bad", [{"type": "cookie"}, {"type": "custom_header"}, {"type": "custom_header", "header": "Authorization"},
                                 {"type": "custom_header", "header": "Host"}, {"type": "custom_header", "header": "X Bad"},
                                 {"type": "query_param", "param": "a b"}, "nope", None])
def test_bad_placements_are_refused(bad):
    with pytest.raises(NetGuardError):
        clean_placement(bad)


def test_secrets_are_checked_and_redacted_in_every_form():
    for bad in ("short", "has space1234", "line\nbreak12345", ""):
        with pytest.raises(ValueError):
            clean_secret(bad)
    assert clean_secret("  sk-12345678  ") == "sk-12345678"
    s = "a/b c+d12"
    out = redact(f"echo {s} / a%2Fb%20c%2Bd12 / a/b%20c%2Bd12", s)
    assert "a/b" not in out and "a%2Fb" not in out and out.count("[redacted]") == 3


# ---- the store
def test_secrets_are_encrypted_and_dropped_when_the_approved_hosts_change(tmp_path):
    db = tmp_path / "a.db"
    st = Store(db)
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    assert st.credentials()[0]["has_secret"] is False and "secret" not in st.credentials()[0]
    assert st.set_credential_secret("acme", SECRET) and st.credential("acme")["secret"] == SECRET
    raw = st._all("SELECT secret FROM credentials")[0]["secret"]
    assert SECRET not in raw and SECRET not in db.read_bytes().decode("latin1")
    st.save_credential("acme", "Acme again", ["api.acme.com"], {"type": "bearer_header"})
    assert st.credential("acme")["secret"] == SECRET                      # same hosts and placement: the key stays
    st.save_credential("acme", "Acme", ["api.evil.example"], {"type": "bearer_header"})
    assert st.credential("acme")["secret"] is None                        # a new host was not approved with that key
    st.save_credential("acme", "Acme", ["api.evil.example"], {"type": "bearer_header"})
    assert st.set_credential_secret("nope", SECRET) is False and st.delete_credential("acme") and not st.delete_credential("acme")


# ---- the agent's tools
async def test_requesting_access_registers_without_a_secret_and_says_where_the_user_enters_it():
    st, fake = Store(), Fake(reply("{}"))
    t = tools_for(st, fake)
    out = await t["add_service_key"].handler({"name": "Acme", "label": "Acme", "hosts": ["API.acme.com"], "auth": {"type": "http", "scheme": "bearer"}})
    assert "fill-in card" in out and "below your message" in out and "notes/services/acme.md" in out
    assert st.credentials() == [{"name": "acme", "label": "Acme", "kind": "api", "hosts": ["api.acme.com"], "placement": {"type": "bearer_header"},
                                 "endpoint": "", "has_secret": False, "keyring": False, "auto_allow": False, "last_used": None, "last_result": None, "last_result_at": None,
                                 "last_result_detail": ""}]
    assert (await t["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/v1"})).startswith("error: the user has not entered the key")
    assert fake.calls == []
    assert t["add_service_key"].requires_approval and t["connector_send"].requires_approval and t["mcp_call_tool"].requires_approval
    assert not t["connector_get"].requires_approval and t["connector_get"].untrusted


async def test_bad_registrations_are_refused():
    st, t = Store(), tools_for(Store(), Fake(reply("{}")))
    for args in ({"name": "x", "hosts": ["a.example.com"], "auth": {"type": "http", "scheme": "bearer"}},
                 {"name": "acme", "hosts": ["127.0.0.1"], "auth": {"type": "http", "scheme": "bearer"}},
                 {"name": "acme", "hosts": ["localhost"], "auth": {"type": "http", "scheme": "bearer"}},
                 {"name": "acme", "hosts": [], "auth": {"type": "http", "scheme": "bearer"}},
                 {"name": "acme", "hosts": ["a.example.com"], "auth": {"type": "http", "scheme": "bearer"}, "mcp_endpoint": "https://other.example.com/mcp"},
                 {"name": "acme", "hosts": ["a.example.com"], "auth": {"type": "http", "scheme": "bearer"}, "mcp_endpoint": "http://a.example.com/mcp"}):
        assert (await t["add_service_key"].handler(args)).startswith("error:"), args
    for auth in ({"type": "oauth2"}, {"type": "http", "scheme": "basic"}, {"type": "apiKey", "in": "cookie", "name": "k"},
                 {"type": "apiKey", "in": "header", "name": "Host"}, {"type": "apiKey", "in": "query", "name": "a b"}, "bearer", None):
        assert (await t["add_service_key"].handler({"name": "acme", "hosts": ["a.example.com"], "auth": auth})).startswith("error:"), auth


def test_openapi_auth_becomes_the_stored_placement():
    from assistant.credentials import placement_from_auth as p
    assert p({"type": "http", "scheme": "bearer"}) == {"type": "bearer_header"}
    assert p({"type": "apiKey", "in": "header", "name": "X-Figma-Token"}) == {"type": "custom_header", "header": "X-Figma-Token", "prefix": ""}
    assert p({"type": "apiKey", "in": "header", "name": "X-Key", "prefix": "Token "}) == {"type": "custom_header", "header": "X-Key", "prefix": "Token "}
    assert p({"type": "apiKey", "in": "header", "name": "Authorization", "prefix": "Bearer"}) == {"type": "bearer_header"}
    assert p({"type": "apiKey", "in": "query", "name": "apikey"}) == {"type": "query_param", "param": "apikey"}
    assert p({"type": "apiKey", "in": "path"}) == {"type": "url_path_segment"}


async def test_a_get_sends_the_key_to_the_registered_host_and_hides_it_from_the_answer():
    st = Store()
    connected(st)
    fake = Fake(reply(f'{{"echo":"Bearer {SECRET}","items":[1,2]}}'))
    out = await tools_for(st, fake)["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/v1/items",
                                                              "headers": {"Accept": "application/json"}})
    c = fake.calls[0]
    assert c["method"] == "GET" and c["url"] == "https://api.acme.com/v1/items" and c["allowed_hosts"] == ["api.acme.com"]
    assert c["headers"] == {"Accept": "application/json", "Authorization": f"Bearer {SECRET}"}
    assert out.startswith("HTTP 200") and "items" in out and SECRET not in out and "[redacted]" in out


async def test_the_model_cannot_send_its_own_credentials_or_other_hosts():
    st = Store()
    connected(st)
    fake = Fake(reply("{}"))
    t = tools_for(st, fake)
    for args in ({"headers": {"Authorization": "Bearer stolen"}}, {"headers": {"Cookie": "a=b"}}, {"headers": {"Host": "evil.example.net"}},
                 {"url": "https://api.acme.com/x?token={secret}"}):
        out = await t["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/x", **args})
        assert out.startswith("error:"), args
    fake.replies = [NetGuardError("api.evil.example is not a registered host; registered: api.acme.com")]
    assert "not a registered host" in await t["connector_get"].handler({"name": "acme", "url": "https://api.evil.example/steal"})


async def test_a_write_sends_json_with_the_chosen_method_and_get_cannot_write():
    st = Store()
    connected(st, {"type": "custom_header", "header": "X-API-Key", "prefix": ""})
    fake = Fake(reply('{"id":7}', 201))
    t = tools_for(st, fake)
    out = await t["connector_send"].handler({"name": "acme", "method": "post", "url": "https://api.acme.com/v1/items", "json": {"title": "한글"}})
    c = fake.calls[0]
    assert out.startswith("HTTP 201") and c["method"] == "POST" and c["headers"]["X-API-Key"] == SECRET
    assert json.loads(c["content"]) == {"title": "한글"} and c["headers"]["Content-Type"] == "application/json"
    assert (await t["connector_send"].handler({"name": "acme", "method": "GET", "url": "https://api.acme.com/"})).startswith("error:")


async def test_a_path_key_is_filled_in_for_the_request_and_scrubbed_from_the_reply():
    st = Store()
    connected(st, {"type": "url_path_segment"})
    fake = Fake(reply(f'{{"url":"https://api.acme.com/bot{SECRET}/me"}}'))
    out = await tools_for(st, fake)["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/bot{secret}/me"})
    assert fake.calls[0]["url"] == f"https://api.acme.com/bot{SECRET}/me" and SECRET not in out


# ---- MCP
def rpc(result, rid, **h):
    return reply(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), **h)


async def test_mcp_lists_tools_after_the_handshake_and_keeps_the_session():
    st = Store()
    connected(st, endpoint="https://api.acme.com/mcp")
    fake = Fake(rpc({"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}, 1, **{"mcp-session-id": "sess-1"}),
                reply("", 202, "text/plain"),
                reply('event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"search"}]}}\n\n', ctype="text/event-stream"))
    out = await tools_for(st, fake)["mcp_list_tools"].handler({"name": "acme"})
    assert json.loads(plain(out)) == {"tools": [{"name": "search"}]}
    bodies = [json.loads(c["content"]) for c in fake.calls]
    assert [b["method"] for b in bodies] == ["initialize", "notifications/initialized", "tools/list"] and "id" not in bodies[1]
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {SECRET}" and "Mcp-Session-Id" not in fake.calls[0]["headers"]
    assert fake.calls[2]["headers"]["Mcp-Session-Id"] == "sess-1" and fake.calls[2]["headers"]["MCP-Protocol-Version"] == "2025-06-18"


async def test_mcp_call_passes_arguments_and_flags_tool_errors_and_never_leaks_the_key():
    st = Store()
    connected(st, endpoint="https://api.acme.com/mcp")
    fake = Fake(rpc({"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}}, 1), reply("", 202, "text/plain"),
                rpc({"isError": True, "content": [{"type": "text", "text": f"bad key {SECRET}"}]}, 2))
    out = await tools_for(st, fake)["mcp_call_tool"].handler({"name": "acme", "tool": "search", "arguments": {"q": "x"}})
    assert out.startswith("(the tool reported an error)") and SECRET not in out
    assert json.loads(fake.calls[2]["content"])["params"] == {"name": "search", "arguments": {"q": "x"}}


@pytest.mark.parametrize("replies,text", [
    ([rpc({"protocolVersion": "1999-01-01", "capabilities": {"tools": {}}}, 1)], "supported protocol"),
    ([rpc({"protocolVersion": "2025-06-18", "capabilities": {}}, 1)], "supported protocol"),
    ([reply("nope", 500)], "HTTP 500"),
    ([reply("<html>", ctype="text/html")], "unsupported content type"),
    ([reply(json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}}))], "matching result"),
    ([reply(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": SECRET}}))], "returned an error"),
])
async def test_mcp_failures_are_explained_without_echoing_the_server(replies, text):
    st = Store()
    connected(st, endpoint="https://api.acme.com/mcp")
    out = await tools_for(st, Fake(*replies))["mcp_list_tools"].handler({"name": "acme"})
    assert out.startswith("error:") and text in out and SECRET not in out


async def test_mcp_tools_refuse_a_rest_connection_and_bad_input():
    st = Store()
    connected(st)
    t = tools_for(st, Fake(reply("{}")))
    assert "no MCP endpoint" in await t["mcp_list_tools"].handler({"name": "acme"})
    assert (await t["mcp_call_tool"].handler({"name": "acme", "arguments": {}})).startswith("error:")


# ---- the app's API
async def test_credentials_api_is_write_only_for_the_secret(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db")))
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/credentials")).status_code == 401
        c.headers.update(AUTH)
        body = {"name": "acme", "label": "Acme", "hosts": ["api.acme.com"], "placement": {"type": "bearer_header"}}
        assert (await c.post("/api/credentials", json={**body, "hosts": ["10.0.0.1"]})).status_code == 422
        assert (await c.post("/api/credentials", json={**body, "placement": {"type": "x"}})).status_code == 422
        items = (await c.post("/api/credentials", json=body)).json()
        assert items[0]["has_secret"] is False
        assert (await c.put("/api/credentials/acme/secret", json={"secret": "short"})).status_code == 422
        assert (await c.put("/api/credentials/acme/secret", json={"secret": "has space in it"})).status_code == 422
        assert (await c.put("/api/credentials/ghost/secret", json={"secret": SECRET})).status_code == 404
        assert (await c.put("/api/credentials/acme/secret", json={"secret": SECRET})).status_code == 200
        listing = await c.get("/api/credentials")
        assert listing.json()[0]["has_secret"] is True and SECRET not in listing.text
        assert (await c.delete("/api/credentials/acme")).status_code == 200 and (await c.get("/api/credentials")).json() == []
        assert (await c.delete("/api/credentials/acme")).status_code == 404
