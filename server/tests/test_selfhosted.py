"""Apps on the owner's own network: the address rules, fetch_local, the presets against pretend apps, sign-in tokens, downloads and
the approval rule for the owner's device app. No real app or network is contacted."""

import base64
import json
import socket
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from assistant import local_login
from assistant.agent.loop import outside_seen
from assistant.home import Home
from assistant.keyconnect import KeyConnections, KeyError_
from assistant.netguard import NetGuardError, Reply, fetch_local, parse_local_address
from assistant.presets import KEYS, SELF_HOSTED, SELF_HOSTED_IDS
from assistant.store import Store
from assistant.tools.connect_tools import connect_tools
from assistant.workers import current_turn


@pytest.fixture(autouse=True)
def fresh():
    local_login._sessions.clear()


def resolver_for(*ips):
    def resolve(host, port, family, kind):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, kind, 6, "", (ip, port)) for ip in ips]
    return resolve


def mock(handler):
    return httpx.MockTransport(handler)


def ok_handler(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})
    return handler


# ---- the address the owner types
@pytest.mark.parametrize("text,origin,base", [
    ("192.168.0.10:8123", "http://192.168.0.10:8123", ""),
    ("http://homeassistant.local:8123/", "http://homeassistant.local:8123", ""),
    ("https://nas.tail1234.ts.net/nextcloud/", "https://nas.tail1234.ts.net", "/nextcloud"),
    ("http://host.docker.internal:2283", "http://host.docker.internal:2283", ""),
    ("http://[fd00::5]:8080", "http://[fd00::5]:8080", ""),
    ("http://100.101.102.103", "http://100.101.102.103", ""),
])
def test_addresses_that_are_accepted(text, origin, base):
    got = parse_local_address(text)
    assert got[:2] == (origin, base)


@pytest.mark.parametrize("text", [
    "http://127.0.0.1:8080", "http://localhost:8080", "http://x.localhost", "http://[::1]/", "http://169.254.169.254/", "http://[fe80::1]/",
    "http://0.0.0.0:80", "http://224.0.0.1", "ftp://192.168.0.1", "http://user:pw@192.168.0.1", "http://192.168.0.1/?a=1", "http://192.168.0.1/#x",
    "http://192.168.0.1:99999", "", "http://a b", "http:///path",
])
def test_addresses_that_are_refused(text):
    with pytest.raises(NetGuardError):
        parse_local_address(text)


# ---- fetch_local
@pytest.mark.parametrize("ip", ["192.168.0.10", "10.1.2.3", "172.16.5.5", "100.64.0.9", "fd12::1", "8.8.8.8"])
async def test_private_cgnat_ula_and_public_addresses_are_reached(ip):
    seen = []
    origin = f"http://[{ip}]:8123" if ":" in ip else f"http://{ip}:8123"
    r = await fetch_local("GET", origin + "/api/", origin=origin, transport=mock(ok_handler(seen)))
    assert r.status == 200 and seen[0].headers["host"] == urlsplit(origin).netloc


@pytest.mark.parametrize("ip", ["127.0.0.1", "127.5.5.5", "169.254.169.254", "::1", "fe80::1", "0.0.0.0", "224.0.0.5", "::ffff:127.0.0.1"])
async def test_loopback_link_local_multicast_and_unspecified_are_refused(ip):
    seen = []
    origin = f"http://[{ip}]" if ":" in ip else f"http://{ip}"
    with pytest.raises(NetGuardError):
        await fetch_local("GET", origin + "/", origin=origin, transport=mock(ok_handler(seen)))
    assert seen == []


async def test_a_name_is_resolved_once_and_that_address_is_used():
    seen = []
    r = await fetch_local("GET", "http://nas.lan:8080/x?a=1", origin="http://nas.lan:8080", resolver=resolver_for("192.168.0.20"),
                          transport=mock(ok_handler(seen)))
    assert r.status == 200
    assert str(seen[0].url) == "http://192.168.0.20:8080/x?a=1" and seen[0].headers["host"] == "nas.lan:8080"


@pytest.mark.parametrize("answers", [("127.0.0.1",), ("192.168.0.2", "127.0.0.1"), ("169.254.169.254",), ("::1",), ("fe80::1",)])
async def test_a_name_that_answers_loopback_or_metadata_is_refused(answers):
    seen = []
    with pytest.raises(NetGuardError, match="refused"):
        await fetch_local("GET", "http://rebind.example.net/", origin="http://rebind.example.net", resolver=resolver_for(*answers),
                          transport=mock(ok_handler(seen)))
    assert seen == []


async def test_a_redirect_is_not_followed():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    with pytest.raises(NetGuardError, match="redirected"):
        await fetch_local("GET", "http://192.168.0.5/", origin="http://192.168.0.5", transport=mock(handler))


@pytest.mark.parametrize("url", ["http://192.168.0.6/", "https://192.168.0.5/", "http://192.168.0.5:81/", "http://other.lan/", "http://u:p@192.168.0.5/",
                                 "file:///etc/passwd"])
async def test_a_url_other_than_the_stored_origin_is_refused(url):
    seen = []
    with pytest.raises(NetGuardError):
        await fetch_local("GET", url, origin="http://192.168.0.5", transport=mock(ok_handler(seen)))
    assert seen == []


async def test_the_body_is_cut_at_the_limit():
    def handler(request):
        return httpx.Response(200, content=b"x" * 5000)
    r = await fetch_local("GET", "http://192.168.0.5/", origin="http://192.168.0.5", transport=mock(handler), max_body=1000)
    assert r.truncated and len(r.body) == 1000


# ---- the connections, against pretend apps
def j(obj, status=200, **h):
    return Reply(status, {"content-type": "application/json", **h}, json.dumps(obj).encode(), False)


class FakeLan:
    """Stands in for fetch_local: records the call, answers like the apps below."""

    def __init__(self):
        self.calls = []
        self.pi_sid = "sid-1"

    async def __call__(self, method, url, *, origin, headers=None, content=None, **kw):
        headers = headers or {}
        self.calls.append((method, url, origin, headers, content, kw))
        path = urlsplit(url).path.removeprefix("/nextcloud")
        if not url.startswith(origin):   # what the real fetch_local does
            raise NetGuardError("this connection only reaches " + origin)
        a = headers.get("Authorization", "")
        if path == "/api/" and "8123" in origin:
            return j({"message": "API running."}) if a == "Bearer ha-token-123" else j({}, 401)
        if path in ("/api/mcp", "/mcp/abc-def"):
            if a not in ("Bearer ha-token-123", "Bearer n8n-bearer-1"):
                return j({}, 401)
            body = json.loads(content)
            if body["method"] == "initialize":
                return j({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}})
            if body["method"].startswith("notifications/"):
                return Reply(202, {}, b"", False)
            return j({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "HassTurnOn"}]}})
        if path == "/api/version":
            return Reply(200, {"content-type": "text/plain"}, b"0.16.0", False)
        if path == "/api/users/me":
            return j({"id": 1}) if headers.get("x-api-key") == "immich-key-123" else j({}, 401)
        if path == "/api/documents/":
            return j({"count": 0}) if a == "Token paperless-tok-1" else j({}, 401)
        if path == "/v1/me":
            return j({"id": 1}) if headers.get("X-Auth-Token") == "mini-token-1" else j({}, 401)
        if path == "/api/v1/user" and "3000" in origin:
            return j({"id": 1}) if a == "token gitea-token-1" else j({}, 401)
        if path == "/ocs/v2.php/cloud/user":
            assert headers.get("OCS-APIRequest") == "true"
            return j({"ocs": {}}) if a == "Basic " + base64.b64encode(b"anna:app-pass-1").decode() else j({}, 401)
        if path == "/metrics":
            return Reply(200, {"content-type": "text/plain"}, b"monitor_status 1", False) if a == "Basic " + base64.b64encode(b":kuma-key-1").decode() else j({}, 401)
        if path == "/control/status":
            return j({"running": True}) if a == "Basic " + base64.b64encode(b"admin:adg-pass").decode() else j({}, 401)
        if path == "/api/v1/workflows":
            return j({"data": []}) if headers.get("X-N8N-API-KEY") == "n8n-key-123" else j({}, 401)
        if path == "/api/v1/about/user":
            assert headers.get("Accept") == "application/vnd.api+json"
            return j({"data": {}}) if a == "Bearer firefly-token-1" else j({}, 401)
        if path in ("/api/users/self", "/api/recipe/", "/api/v1/collections", "/api/v1/users/me"):
            return j({}) if a == "Bearer generic-token-1" else j({}, 401)
        if path == "/api/system/info":
            return j({}) if headers.get("GROCY-API-KEY") == "grocy-key-123" else j({}, 401)
        # sign-in apps
        if path == "/api/auth":
            ok = json.loads(content) == {"password": "pi-pass"}
            return j({"session": {"valid": True, "sid": self.pi_sid, "validity": 300}}) if ok else j({"session": {"valid": False}}, 401)
        if path == "/api/dns/blocking":
            return j({"blocking": "enabled"}) if headers.get("X-FTL-SID") == self.pi_sid else j({}, 401)
        if path == "/api/collections/users/auth-with-password":
            ok = json.loads(content) == {"identity": "me@example.com", "password": "bz-pass"}
            return j({"token": "pb-jwt"}) if ok else j({}, 400)
        if path == "/api/collections/systems/records":
            return j({"items": []}) if a == "pb-jwt" else j({}, 401)
        if path == "/api/greader.php/accounts/ClientLogin":
            ok = parse_qs(content.decode()) == {"Email": ["alice"], "Passwd": ["api-pass"]}
            return Reply(200, {}, b"SID=alice/abc\nAuth=alice/abc\n", False) if ok else Reply(401, {}, b"Error=BadAuthentication", False)
        if path == "/api/greader.php/reader/api/0/user-info":
            return j({"userId": "alice"}) if a == "GoogleLogin auth=alice/abc" else j({}, 401)
        if path == "/oauth/v2/token":
            ok = parse_qs(content.decode()) == {"grant_type": ["password"], "client_id": ["cid"], "client_secret": ["csecret"], "username": ["bob"], "password": ["wb-pass"]}
            return j({"access_token": "wb-access", "expires_in": 3600, "refresh_token": "r"}) if ok else j({"error": "invalid_grant"}, 400)
        if path == "/api/entries.json":
            return j({"_embedded": {"items": []}}) if a == "Bearer wb-access" else j({}, 401)
        return j({}, 404)


ADDR = {"home_assistant": "http://192.168.0.10:8123", "frigate": "http://192.168.0.10:5000", "immich": "http://192.168.0.10:2283",
        "paperless": "http://192.168.0.10:8000", "mealie": "http://192.168.0.10:9925", "tandoor": "http://192.168.0.10:8080",
        "grocy": "http://192.168.0.10:9283", "actual_budget": "http://192.168.0.10:5007", "firefly_iii": "http://192.168.0.10:8081",
        "linkwarden": "http://192.168.0.10:3001", "karakeep": "http://192.168.0.10:3002", "wallabag": "http://192.168.0.10:8082",
        "miniflux": "http://192.168.0.10:8083", "freshrss": "http://192.168.0.10:8084", "n8n": "http://192.168.0.10:5678",
        "n8n_mcp": "", "uptime_kuma": "http://192.168.0.10:3003", "beszel": "http://192.168.0.10:8090", "adguard_home": "http://192.168.0.10:3004",
        "pihole": "http://192.168.0.10", "nextcloud": "http://192.168.0.10:8085", "gitea": "http://192.168.0.10:3000", "forgejo": "http://192.168.0.10:3000"}
VALUES = {
    "home_assistant": {"token": "ha-token-123"}, "frigate": {}, "immich": {"token": "immich-key-123"}, "paperless": {"token": "paperless-tok-1"},
    "mealie": {"token": "generic-token-1"}, "tandoor": {"token": "generic-token-1"}, "grocy": {"token": "grocy-key-123"},
    "actual_budget": {"token": "actual-key-123"}, "firefly_iii": {"token": "firefly-token-1"}, "linkwarden": {"token": "generic-token-1"},
    "karakeep": {"token": "generic-token-1"}, "wallabag": {"client_id": "cid", "client_secret": "csecret", "username": "bob", "password": "wb-pass"},
    "miniflux": {"token": "mini-token-1"}, "freshrss": {"username": "alice", "password": "api-pass"}, "n8n": {"token": "n8n-key-123"},
    "n8n_mcp": {"mcp_url": "http://192.168.0.10:5678/mcp/abc-def", "token": "n8n-bearer-1"},
    "uptime_kuma": {"token": "kuma-key-1"}, "beszel": {"username": "me@example.com", "password": "bz-pass"},
    "adguard_home": {"username": "admin", "password": "adg-pass"}, "pihole": {"password": "pi-pass"},
    "nextcloud": {"username": "anna", "password": "app-pass-1"}, "gitea": {"token": "gitea-token-1"}, "forgejo": {"token": "gitea-token-1"},
}


def test_every_self_hosted_app_has_test_values():
    assert set(VALUES) == SELF_HOSTED_IDS == {p.id for p in SELF_HOSTED} and set(ADDR) == set(VALUES)
    assert all(KEYS[i].local for i in SELF_HOSTED_IDS)


@pytest.mark.parametrize("pid", sorted(VALUES))
async def test_each_app_connects_after_a_real_check_and_keeps_its_address_with_the_key(pid):
    st, lan = Store(), FakeLan()
    keys = KeyConnections(st, local_fetcher=lan)
    p = KEYS[pid]
    values = dict(VALUES[pid])
    if ADDR[pid]:
        values["address"] = ADDR[pid]
    out = await keys.connect(pid, values)
    assert out["item"] == p.item and out["checked"] == bool(p.verify or p.endpoint_field or p.login)
    c = st.credential(p.item)
    origin = urlsplit(ADDR[pid] or VALUES[pid]["mcp_url"]).scheme + "://" + urlsplit(ADDR[pid] or VALUES[pid]["mcp_url"]).netloc
    assert c["placement"]["origin"] == origin and c["secret"] and c["has_secret"]
    assert c["hosts"] == [urlsplit(origin).netloc]
    if p.verify or p.endpoint_field:
        assert all(call[2] == origin for call in lan.calls)
    # a wrong key is refused and nothing is saved
    st2 = Store()
    bad = {k: ("wrong-value-xx" if k in ("token", "password", "client_secret") else v) for k, v in values.items()}
    if pid not in ("frigate", "actual_budget"):   # no key to get wrong / saved without a test request
        with pytest.raises(KeyError_):
            await KeyConnections(st2, local_fetcher=FakeLan()).connect(pid, bad)
        assert st2.credential(p.item) is None


async def test_home_assistant_keeps_the_mcp_address_and_marks_itself_a_device_app():
    st = Store()
    await KeyConnections(st, local_fetcher=FakeLan()).connect("home_assistant", {"address": "http://192.168.0.10:8123/", "token": "ha-token-123"})
    c = st.credential("home-assistant-api")
    assert c["endpoint"] == "http://192.168.0.10:8123/api/mcp" and c["placement"]["device"] is True


@pytest.mark.parametrize("bad", ["http://127.0.0.1:8123", "http://localhost:8123", "http://169.254.169.254", "javascript:alert(1)", ""])
async def test_the_form_refuses_addresses_that_can_never_be_reached(bad):
    st, lan = Store(), FakeLan()
    with pytest.raises(KeyError_):
        await KeyConnections(st, local_fetcher=lan).connect("home_assistant", {"address": bad, "token": "ha-token-123"})
    assert lan.calls == [] and st.credentials() == []


async def test_n8n_mcp_needs_a_full_url():
    with pytest.raises(KeyError_):
        await KeyConnections(Store(), local_fetcher=FakeLan()).connect("n8n_mcp", {"mcp_url": "http://192.168.0.10:5678", "token": "n8n-bearer-1"})


# ---- the agent can never make or change a local address
async def test_the_agent_cannot_register_a_local_origin():
    st = Store()
    t = {x.name: x for x in connect_tools(st)}
    for hosts in (["192.168.0.10"], ["localhost"], ["nas.lan"], ["127.0.0.1"], ["169.254.169.254"]):
        out = await t["add_service_key"].handler({"name": "lanapp", "hosts": hosts, "auth": {"type": "http", "scheme": "bearer"}})
        assert out.startswith("error:"), hosts
    assert t["add_service_key"].parameters["properties"].get("origin") is None
    # an extra "origin" in auth or the call is ignored: the stored placement is built fresh
    await t["add_service_key"].handler({"name": "lanapp", "hosts": ["api.example.com"], "auth": {"type": "http", "scheme": "bearer", "origin": "http://192.168.0.5"},
                                        "origin": "http://192.168.0.5"})
    assert "origin" not in st.credential("lanapp")["placement"]
    for kind in ({"type": "basic_auth"}, {"type": "none"}):
        from assistant.credentials import clean_placement
        with pytest.raises(NetGuardError):
            clean_placement(kind)


async def test_the_agent_cannot_move_a_stored_local_connection_without_asking_and_it_loses_the_key():
    st = Store()
    await KeyConnections(st, local_fetcher=FakeLan()).connect("home_assistant", {"address": "http://192.168.0.10:8123", "token": "ha-token-123"})
    t = {x.name: x for x in connect_tools(st)}
    args = {"name": "home-assistant-api", "hosts": ["192.168.0.10"], "auth": {"type": "http", "scheme": "bearer"}}
    assert (await t["add_service_key"].handler(args)).startswith("error:")    # an address is not a host name
    args = {"name": "home-assistant-api", "hosts": ["evil.example.com"], "auth": {"type": "http", "scheme": "bearer"}}
    assert t["add_service_key"].auto_approve(args) is False                   # it would drop the stored key: the owner is asked first
    # the same host and placement the owner's form stored is not something the agent can state (no origin in its placements)
    same = {"name": "home-assistant-api", "hosts": ["192.168.0.10:8123"], "auth": {"type": "http", "scheme": "bearer"}}
    assert (await t["add_service_key"].handler(same)).startswith("error:")


async def test_a_normal_connection_is_never_sent_to_fetch_local():
    st = Store()
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    st.set_credential_secret("acme", "sk-live-ABCDEF123456")
    calls = []

    async def normal(method, url, **kw):
        calls.append(url)
        return j({"ok": 1})

    async def lan(*a, **k):
        raise AssertionError("fetch_local must not be used")
    t = {x.name: x for x in connect_tools(st, fetcher=normal, local_fetcher=lan)}
    assert "HTTP 200" in await t["connector_get"].handler({"name": "acme", "url": "https://api.acme.com/v1"})
    await t["connector_get"].handler({"name": "acme", "url": "http://192.168.0.5/x"})   # goes to the normal fetch, whose rules refuse it
    assert calls == ["https://api.acme.com/v1", "http://192.168.0.5/x"]


# ---- using a connection
async def connected(pid, home=None, lan=None):
    st, lan = Store(), lan or FakeLan()
    values = dict(VALUES[pid])
    if ADDR[pid]:
        values["address"] = ADDR[pid]
    await KeyConnections(st, local_fetcher=lan).connect(pid, values)
    lan.calls.clear()
    return st, lan, {x.name: x for x in connect_tools(st, home, local_fetcher=lan)}


async def test_a_path_or_the_full_url_on_the_address_works_and_another_address_does_not():
    st, lan, t = await connected("immich")
    assert "HTTP 200" in await t["connector_get"].handler({"name": "immich-api", "url": "/api/users/me"})
    assert "HTTP 200" in await t["connector_get"].handler({"name": "immich-api", "url": "http://192.168.0.10:2283/api/users/me"})
    assert lan.calls[0][3]["x-api-key"] == "immich-key-123"
    out = await t["connector_get"].handler({"name": "immich-api", "url": "http://192.168.0.99:2283/api/users/me"})
    assert out.startswith("error:")
    assert (await t["connector_get"].handler({"name": "immich-api", "url": "//evil.example/x"})).startswith("error:")


async def test_the_base_path_is_added_to_a_path():
    st = Store()
    lan = FakeLan()
    await KeyConnections(st, local_fetcher=lan).connect("nextcloud", {"address": "http://192.168.0.10:8085/nextcloud", "username": "anna", "password": "app-pass-1"})
    lan.calls.clear()
    t = {x.name: x for x in connect_tools(st, local_fetcher=lan)}
    await t["connector_get"].handler({"name": "nextcloud-api", "url": "/ocs/v2.php/cloud/user"})
    assert lan.calls[0][1] == "http://192.168.0.10:8085/nextcloud/ocs/v2.php/cloud/user"


async def test_mcp_goes_to_the_stored_address_over_http():
    st, lan, t = await connected("home_assistant")
    out = await t["mcp_list_tools"].handler({"name": "home-assistant-api"})
    assert "HassTurnOn" in out
    assert {c[1] for c in lan.calls} == {"http://192.168.0.10:8123/api/mcp"} and all(c[3]["Authorization"] == "Bearer ha-token-123" for c in lan.calls)


async def test_n8n_mcp_uses_its_own_url():
    st = Store()
    await KeyConnections(st, local_fetcher=FakeLan()).connect("n8n_mcp", {"mcp_url": "http://192.168.0.10:5678/mcp/abc-def", "token": "n8n-bearer-1"})
    assert st.credential("n8n-mcp-api")["endpoint"] == "http://192.168.0.10:5678/mcp/abc-def"


@pytest.mark.parametrize("pid,path,header,expect", [
    ("pihole", "/api/dns/blocking", "X-FTL-SID", "sid-1"),
    ("beszel", "/api/collections/systems/records", "Authorization", "pb-jwt"),
    ("freshrss", "/api/greader.php/reader/api/0/user-info?output=json", "Authorization", "GoogleLogin auth=alice/abc"),
    ("wallabag", "/api/entries.json?perPage=1", "Authorization", "Bearer wb-access"),
])
async def test_sign_in_apps_use_a_session_token_and_never_the_password(pid, path, header, expect):
    st, lan, t = await connected(pid)
    name = KEYS[pid].item
    out = await t["connector_get"].handler({"name": name, "url": path})
    assert "HTTP 200" in out
    last = lan.calls[-1]
    assert last[3][header] == expect
    for c in lan.calls:
        assert "pi-pass" not in str(c[3]) and "bz-pass" not in str(c[3])
    n = len(lan.calls)
    await t["connector_get"].handler({"name": name, "url": path})
    assert len(lan.calls) == n + 1                    # the token was kept: no second sign-in


async def test_a_refused_session_signs_in_again_once():
    st, lan, t = await connected("pihole")
    await t["connector_get"].handler({"name": "pihole-api", "url": "/api/dns/blocking"})
    lan.pi_sid = "sid-2"                              # the app forgot the session
    out = await t["connector_get"].handler({"name": "pihole-api", "url": "/api/dns/blocking"})
    assert "HTTP 200" in out
    assert [c[3].get("X-FTL-SID") for c in lan.calls if c[1].endswith("blocking")][-2:] == ["sid-1", "sid-2"]


async def test_a_wrong_password_is_refused_at_connect_time():
    with pytest.raises(KeyError_):
        await KeyConnections(Store(), local_fetcher=FakeLan()).connect("pihole", {"address": "http://192.168.0.10", "password": "nope-nope"})


# ---- pictures and files
class PicLan:
    def __init__(self, body, ctype="image/jpeg", status=200):
        self.body, self.ctype, self.status, self.kw = body, ctype, status, {}

    async def __call__(self, method, url, *, origin, headers=None, content=None, **kw):
        self.kw = kw
        return Reply(self.status, {"content-type": self.ctype}, self.body, False)


async def test_a_picture_is_saved_to_the_library(tmp_path):
    home = Home(tmp_path / "home")
    st = Store()
    st.save_credential("frigate-api", "Frigate", ["192.168.0.10:5000"], {"type": "none", "origin": "http://192.168.0.10:5000"})
    st.set_credential_secret("frigate-api", "no-key-needed")
    pic = PicLan(b"\xff\xd8\xff\xe0" + b"jpeg" * 20)
    t = {x.name: x for x in connect_tools(st, home, local_fetcher=pic)}
    out = await t["connector_download"].handler({"name": "frigate-api", "url": "/api/front_door/latest.jpg", "save_as": "door"})
    assert "files/door.jpg" in out
    assert (home.root / "files" / "door.jpg").read_bytes().startswith(b"\xff\xd8")
    assert pic.kw["max_body"] > 1_000_000
    assert not t["connector_download"].requires_approval


async def test_downloads_refuse_programs_errors_and_connections_without_a_library(tmp_path):
    home = Home(tmp_path / "home")
    st = Store()
    st.save_credential("frigate-api", "Frigate", ["192.168.0.10:5000"], {"type": "none", "origin": "http://192.168.0.10:5000"})
    st.set_credential_secret("frigate-api", "no-key-needed")
    ask = lambda p, **kw: {x.name: x for x in connect_tools(st, home, local_fetcher=p)}["connector_download"].handler(  # noqa: E731
        {"name": "frigate-api", "url": "/x", **kw})
    assert "programs" in await ask(PicLan(b"MZ\x90\x00" + b"x" * 40, "image/jpeg"), save_as="a.jpg")
    assert "programs" in await ask(PicLan(b"abc" * 10, "image/png"), save_as="run.exe")
    assert "HTTP 404" in await ask(PicLan(b"", status=404))
    t = {x.name: x for x in connect_tools(st, None, local_fetcher=PicLan(b"x"))}
    assert "no library" in await t["connector_download"].handler({"name": "frigate-api", "url": "/x"})


# ---- the approval rule for the owner's device app
def turn(source="chat", unattended=False):
    return current_turn.set({"source": source, "unattended": unattended})


async def test_a_change_the_owner_asked_for_in_chat_needs_no_card_for_a_device_app():
    st, lan, t = await connected("home_assistant")
    send, call = t["connector_send"], t["mcp_call_tool"]
    args = {"name": "home-assistant-api", "method": "POST", "url": "/api/services/lock/unlock", "json": {"entity_id": "lock.front"}}
    tok = turn()
    seen = outside_seen.set([False])
    try:
        assert send.auto_approve(args) is True and call.auto_approve({"name": "home-assistant-api", "tool": "HassTurnOn"}) is True
        # reading the device app's own state does not count as outside text
        assert t["connector_get"].own_data({"name": "home-assistant-api"}) is True
        outside_seen.get()[0] = True                      # a page, mail or message reached the run
        assert send.auto_approve(args) is False and call.auto_approve({"name": "home-assistant-api", "tool": "HassTurnOn"}) is False
        outside_seen.get()[0] = False
    finally:
        outside_seen.reset(seen)
        current_turn.reset(tok)


@pytest.mark.parametrize("source,unattended", [("schedule:3", True), ("worker", True), ("handoff", True), ("watcher:1", True), ("chat", True)])
async def test_a_step_kelo_takes_on_its_own_still_asks(source, unattended):
    st, lan, t = await connected("home_assistant")
    tok = turn(source, unattended)
    seen = outside_seen.set([False])
    try:
        assert t["connector_send"].auto_approve({"name": "home-assistant-api", "method": "POST", "url": "/api/services/lock/unlock"}) is False
    finally:
        outside_seen.reset(seen)
        current_turn.reset(tok)


async def test_other_apps_and_normal_services_keep_asking_and_no_turn_means_ask():
    st, lan, t = await connected("gitea")
    tok = turn()
    seen = outside_seen.set([False])
    try:
        assert t["connector_send"].auto_approve({"name": "gitea-api", "method": "POST", "url": "/api/v1/repos/a/b/issues"}) is False
        assert t["connector_send"].auto_approve({"name": "nope", "method": "POST", "url": "/x"}) is False
    finally:
        outside_seen.reset(seen)
        current_turn.reset(tok)
    st2, lan2, t2 = await connected("home_assistant")
    assert t2["connector_send"].auto_approve({"name": "home-assistant-api"}) is False    # no running turn


async def test_a_saved_picture_is_shown_in_the_chat(tmp_path):
    import io
    from PIL import Image
    from assistant.chat_images import ChatImages
    from assistant.deliverables import Deliverables
    home = Home(tmp_path / "home")
    lib = Deliverables(home)
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, "PNG")
    name = lib.save("door.png", buf.getvalue())
    lib.save("note.txt", b"hello")
    st = Store()
    imgs = ChatImages(st, tmp_path / "ci")
    tool = imgs.library_tool(lib, lambda: {"turn_id": "t1"})
    out = await tool.handler({"name": name, "caption": "front door"})
    assert "in the chat" in out.text and imgs.count_for("t1") == 1
    assert (await tool.handler({"name": "note.txt"})).startswith("Error:")
    assert (await tool.handler({"name": "nope.png"})).startswith("Error:")


# ---- checked against the real apps (2026-11-11, one throw-away instance each on a Docker host)
def test_api_text_matches_what_the_real_apps_answer():
    api = {p.id: p.api for p in SELF_HOSTED}
    assert "machine-learning" in api["immich"]                       # smart search answers 400 without Immich's ML container
    assert "GET /v1/budgets lists" in api["actual_budget"]           # the list needs no key but gives the sync id (groupId)
    assert "/api/docs/ page" in api["pihole"]                        # /api/docs without the slash is a redirect, which is not followed
    assert "Personal settings" in " ".join(KEYS["nextcloud"].help)


# ---- Home Assistant services that run code are never called, the ones that manage HA's own system always ask (forbidden.py; 2026-11-12)
@pytest.mark.parametrize("url", ["/api/services/shell_command/backup", "/api/services/command_line/x", "/api/services/python_script/run",
                                 "/api/services/pyscript/go", "/api/services/rest_command/call",
                                 "/api/services/Shell_Command/x", "/api/services/shell%5Fcommand/x", "//api//services/shell_command/x/",
                                 "http://192.168.0.10:8123/api/services/shell_command/x"])
async def test_home_assistant_code_services_are_never_called(url):
    st, lan = Store(), FakeLan()
    await KeyConnections(st, local_fetcher=lan).connect("home_assistant", {"address": "http://192.168.0.10:8123", "token": "ha-token-123"})
    lan.calls.clear()
    t = {x.name: x for x in connect_tools(st, local_fetcher=lan)}
    out = await t["connector_send"].handler({"name": "home-assistant-api", "method": "POST", "url": url, "json": {}})
    assert "not run" in str(out) and lan.calls == []


@pytest.mark.parametrize("url", ["/api/services/light/turn_on", "/api/services/homeassistant/turn_off", "/api/services/script/turn_on",
                                 "/api/services/climate/set_temperature"])
async def test_ordinary_home_assistant_services_are_not_refused(url):
    st, lan = Store(), FakeLan()
    await KeyConnections(st, local_fetcher=lan).connect("home_assistant", {"address": "http://192.168.0.10:8123", "token": "ha-token-123"})
    t = {x.name: x for x in connect_tools(st, local_fetcher=lan)}
    out = await t["connector_send"].handler({"name": "home-assistant-api", "method": "POST", "url": url, "json": {"entity_id": "light.kitchen"}})
    assert "not run" not in str(out)


@pytest.mark.parametrize("url", ["/api/services/hassio/host_reboot", "/api/services/homeassistant/restart", "/api/services/homeassistant/stop",
                                 "/api/hassio/addons/x/start", "/api/hassio_ingress/x"])
async def test_home_assistant_system_services_always_ask_even_for_a_request_in_the_owners_own_chat(url):
    from assistant.permissions import Gate, Permissions
    st, lan = Store(), FakeLan()
    await KeyConnections(st, local_fetcher=lan).connect("home_assistant", {"address": "http://192.168.0.10:8123", "token": "ha-token-123"})
    t = {x.name: x for x in connect_tools(st, local_fetcher=lan)}
    args = {"name": "home-assistant-api", "method": "POST", "url": url, "json": {}}
    verdict = await Gate(Permissions(st)).check(t["connector_send"], args)
    assert verdict.action == "ask" and verdict.card
    light = await Gate(Permissions(st)).check(t["connector_send"], {**args, "url": "/api/services/light/turn_on"})
    assert light.action == "pass"
