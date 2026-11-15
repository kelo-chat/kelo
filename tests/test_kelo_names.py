"""Free <name>.kelo.chat addresses: the operator's broker, against a fake Cloudflare (nothing reaches the real API)."""

import json
import re
import uuid

import httpx
import pytest

from assistant import ddns
from assistant.kelo_names import DEFAULT_MAX, IDLE_DAYS, KeloNameError, KeloNames, name_problem, owner_of
from assistant.server import Settings, create_app
from assistant.store import Store

from test_server import AUTH, TOKEN

CF_TOKEN = "cf-token-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
ACC, ZONE = "a" * 32, "b" * 32
INSTALL_A, INSTALL_B = "A" * 40, "B" * 40


class FakeCloudflare:
    """Just enough of api.cloudflare.com/client/v4 for tunnels and DNS records, with the same envelopes."""

    def __init__(self):
        self.tunnels, self.records, self.configs, self.calls = {}, {}, {}, []
        self.fail: dict[str, tuple[int, str]] = {}          # "METHOD path-regex" -> (status, message)
        self.token_in_create = True

    def ok(self, result, status=200):
        return httpx.Response(status, json={"success": True, "errors": [], "messages": [], "result": result})

    def err(self, status, msg, code=1000):
        return httpx.Response(status, json={"success": False, "errors": [{"code": code, "message": msg}], "messages": [], "result": None})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path.removeprefix("/client/v4"), req.method
        self.calls.append((m, path, dict(req.url.params)))
        if req.headers.get("authorization") != f"Bearer {CF_TOKEN}":
            return self.err(403, "Authentication error", 10000)
        for key, (status, msg) in self.fail.items():
            fm, fp = key.split(" ", 1)
            if fm == m and re.search(fp, path):
                return self.err(status, msg)
        body = json.loads(req.content) if req.content else {}
        t = re.fullmatch(rf"/accounts/{ACC}/cfd_tunnel(?:/([^/]+))?(?:/(configurations|token|connections))?", path)
        if t:
            tid, sub = t.groups()
            if m == "POST" and not tid:
                if any(x["name"] == body["name"] and not x["deleted_at"] for x in self.tunnels.values()):
                    return self.err(409, "tunnel with name already exists", 1013)
                tid = str(uuid.uuid4())
                self.tunnels[tid] = {"id": tid, "name": body["name"], "status": "inactive", "created_at": "2026-11-01T00:00:00Z",
                                     "conns_active_at": None, "conns_inactive_at": None, "deleted_at": None, "secret": uuid.uuid4().hex}
                out = dict(self.tunnels[tid])
                if self.token_in_create:
                    out["token"] = "eyJ" + out["secret"]
                return self.ok(out)
            if m == "GET" and not tid:
                q = req.url.params
                rows = [x for x in self.tunnels.values() if not x["deleted_at"]]
                if q.get("name"):
                    rows = [x for x in rows if x["name"] == q["name"]]
                if q.get("include_prefix"):
                    rows = [x for x in rows if x["name"].startswith(q["include_prefix"])]
                return self.ok(rows)
            if tid not in self.tunnels:
                return self.err(404, "tunnel not found", 1003)
            if not sub and m == "GET":
                return self.ok(self.tunnels[tid])                    # a deleted tunnel still answers, with deleted_at set
            if self.tunnels[tid]["deleted_at"]:
                return self.err(404, "tunnel not found", 1003)
            if sub == "configurations" and m == "PUT":
                self.configs[tid] = body["config"]
                return self.ok({"tunnel_id": tid, "config": body["config"]})
            if sub == "token" and m == "GET":
                return self.ok("eyJ" + self.tunnels[tid]["secret"])
            if sub == "connections" and m == "DELETE":
                return self.ok(None)
            if not sub and m == "DELETE":
                self.tunnels[tid]["deleted_at"] = "2026-11-07T00:00:00Z"
                return self.ok(self.tunnels[tid])
        d = re.fullmatch(rf"/zones/{ZONE}/dns_records(?:/([^/]+))?", path)
        if d:
            rid = d.group(1)
            if m == "GET" and not rid:
                q = req.url.params
                rows = [r for r in self.records.values() if (not q.get("name.exact") or r["name"] == q["name.exact"])
                        and (not q.get("type") or r["type"] == q["type"])]
                return self.ok(rows)
            if m == "POST":
                rid = uuid.uuid4().hex
                self.records[rid] = {**body, "id": rid}
                return self.ok(self.records[rid])
            if rid not in self.records:
                return self.err(404, "Record does not exist", 81044)
            if m == "PATCH":
                self.records[rid].update(body)
                return self.ok(self.records[rid])
            if m == "DELETE":
                del self.records[rid]
                return self.ok({"id": rid})
        return self.err(404, "no route")

    def live(self):
        return [t for t in self.tunnels.values() if not t["deleted_at"]]


@pytest.fixture
def cf():
    return FakeCloudflare()


async def ready(cf, **kw):
    names = KeloNames(Store(), transport=httpx.MockTransport(cf), **kw)
    await names.configure(api_token=CF_TOKEN, account_id=ACC, zone_id=ZONE, zone_name="kelo.chat", max_names=None)
    return names


def test_names_follow_the_rules():
    for good in ("abc", "alice", "my-kelo-2", "a" * 30, "123"):
        assert name_problem(good) is None, good
    for bad in ("ab", "a" * 31, "-abc", "abc-", "ABC", "a_b", "a.b", "a--b", "www", "api", "admin", "mail", "kelo", "broker", "relay", ""):
        assert name_problem(bad), bad


async def test_a_claim_makes_one_tunnel_routed_to_the_server_and_a_proxied_cname(cf):
    names = await ready(cf)
    out = await names.claim("alice", INSTALL_A)
    assert out["hostname"] == "alice.kelo.chat" and out["tunnel_token"].startswith("eyJ")
    (t,) = cf.live()
    assert t["name"] == "kelo-alice"
    assert cf.configs[t["id"]]["ingress"] == [{"hostname": "alice.kelo.chat", "service": "http://server:8080"}, {"service": "http_status:404"}]
    (r,) = cf.records.values()
    assert (r["type"], r["name"], r["content"], r["proxied"]) == ("CNAME", "alice.kelo.chat", f"{t['id']}.cfargotunnel.com", True)
    row = names.by_name("alice")
    assert row["owner"] == owner_of(INSTALL_A) and INSTALL_A not in json.dumps(row)      # only the hash is kept


async def test_the_token_endpoint_is_used_when_create_does_not_return_one(cf):
    cf.token_in_create = False
    names = await ready(cf)
    out = await names.claim("alice", INSTALL_A)
    assert out["tunnel_token"] == "eyJ" + cf.live()[0]["secret"] and any(c[1].endswith("/token") for c in cf.calls)


async def test_only_the_same_copy_gets_the_name_again_and_one_name_per_copy(cf):
    names = await ready(cf)
    first = await names.claim("alice", INSTALL_A)
    again = await names.claim("alice", INSTALL_A)                 # e.g. a reinstall that kept its install id
    assert again["tunnel_token"] == first["tunnel_token"] and len(cf.live()) == 1 and len(cf.records) == 1
    with pytest.raises(KeloNameError, match="taken") as e:
        await names.claim("alice", INSTALL_B)
    assert e.value.status == 409
    with pytest.raises(KeloNameError, match="already has alice") as e:
        await names.claim("bob", INSTALL_A)
    assert e.value.status == 409
    for bad in ("short", "x" * 31 + "!", ""):
        with pytest.raises(KeloNameError, match="install_id"):
            await names.claim("carol", bad)


async def test_a_tunnel_deleted_by_hand_is_made_again_for_its_owner(cf):
    names = await ready(cf)
    await names.claim("alice", INSTALL_A)
    gone = cf.live()[0]["id"]
    cf.tunnels[gone]["deleted_at"] = "2026-11-07T00:00:00Z"
    out = await names.claim("alice", INSTALL_A)
    (t,) = cf.live()
    assert t["id"] != gone and out["tunnel_token"] == "eyJ" + t["secret"] and names.by_name("alice")["tunnel_id"] == t["id"]
    assert [r["content"] for r in cf.records.values()] == [f"{t['id']}.cfargotunnel.com"]


async def test_release_needs_the_secret_and_frees_everything(cf):
    names = await ready(cf)
    await names.claim("alice", INSTALL_A)
    assert await names.release(INSTALL_B) is None and names.by_name("alice")
    assert await names.release(INSTALL_A) == "alice"
    assert cf.live() == [] and cf.records == {} and names.count() == 0
    assert any(c[0] == "DELETE" and c[1].endswith("/connections") for c in cf.calls)      # live connectors are cut first
    assert (await names.claim("alice", INSTALL_B))["hostname"] == "alice.kelo.chat"       # free for someone else now


async def test_a_stale_tunnel_from_an_earlier_holder_is_replaced_so_its_old_token_dies(cf):
    names = await ready(cf)
    await names.claim("alice", INSTALL_A)
    old_id = cf.live()[0]["id"]
    names.store._run("DELETE FROM kelo_names")                    # e.g. the broker's database was restored from an old backup
    await names.claim("alice", INSTALL_B)
    assert cf.tunnels[old_id]["deleted_at"] and len(cf.live()) == 1 and len(cf.records) == 1     # the CNAME was re-pointed, not doubled
    assert next(iter(cf.records.values()))["content"].startswith(cf.live()[0]["id"])


async def test_the_cap_and_cloudflare_limits_give_a_clear_full_error(cf):
    names = await ready(cf)
    assert names.max_names() == DEFAULT_MAX == 900
    await names.configure(api_token=None, account_id=None, zone_id=None, zone_name=None, max_names=1)
    await names.claim("alice", INSTALL_A)
    assert names.check("bob") == {"name": "bob", "available": False, "reason": "full"}
    with pytest.raises(KeloNameError, match="no free Kelo addresses") as e:
        await names.claim("bob", INSTALL_B)
    assert e.value.status == 503 and len(cf.live()) == 1
    await names.configure(api_token=None, account_id=None, zone_id=None, zone_name=None, max_names=10)
    cf.fail["POST /dns_records$"] = (400, "DNS record quota exceeded")
    with pytest.raises(KeloNameError, match="DNS record limit") as e:
        await names.claim("bob", INSTALL_B)
    assert e.value.status == 503 and len(cf.live()) == 1 and names.by_name("bob") is None    # the half-made tunnel was removed
    cf.fail = {"POST /cfd_tunnel$": (400, "You have reached the tunnel limit for this account")}
    with pytest.raises(KeloNameError, match="tunnel limit"):
        await names.claim("bob", INSTALL_B)


async def test_check_answers_without_touching_cloudflare(cf):
    names = await ready(cf)
    await names.claim("alice", INSTALL_A)
    n = len(cf.calls)
    assert names.check("alice")["reason"] == "taken" and names.check("www")["reason"].endswith("reserved; pick another")
    assert names.check("Bob") == {"name": "bob", "available": True, "hostname": "bob.kelo.chat"} and len(cf.calls) == n


async def test_idle_names_are_reclaimed_after_30_days_without_a_connector(cf):
    now = [1_800_000_000.0]
    names = await ready(cf, clock=lambda: now[0])
    for name, install in (("idle", "I" * 40), ("used", "U" * 40), ("fresh", "F" * 40), ("never", "N" * 40)):
        await names.claim(name, install)
    by = {t["name"]: t for t in cf.live()}
    long_ago = "2026-01-01T00:00:00Z"
    by["kelo-idle"].update(status="down", conns_active_at=long_ago, conns_inactive_at=long_ago, created_at=long_ago)
    by["kelo-used"].update(status="healthy", conns_active_at=long_ago, created_at=long_ago)
    by["kelo-never"].update(created_at=long_ago)
    assert await names.reclaim_idle() == []                       # all were claimed just now
    now[0] += (IDLE_DAYS + 1) * 86400
    by["kelo-fresh"].update(status="down", conns_inactive_at="2027-02-13T00:00:00Z")   # seen 2 days ago (fake time; 5 days parks it)
    names.clock = lambda: 1_800_000_000.0 + (IDLE_DAYS + 1) * 86400
    gone = await names.reclaim_idle()
    assert set(gone) == {"idle", "never"} and {r["name"] for r in names.store._all("SELECT name FROM kelo_names")} == {"used", "fresh"}
    assert {t["name"] for t in cf.live()} == {"kelo-used", "kelo-fresh"} and len(cf.records) == 2


async def test_configure_verifies_before_saving_and_never_returns_the_token(cf):
    names = KeloNames(Store(), transport=httpx.MockTransport(cf))
    assert not names.enabled()
    with pytest.raises(KeloNameError, match="does not hand out"):
        names.check("alice")
    with pytest.raises(KeloNameError, match="Authentication error"):
        await names.configure(api_token="wrong-token-0123456789abcdef", account_id=ACC, zone_id=ZONE, zone_name="kelo.chat", max_names=None)
    assert not names.store.key("operator:cloudflare")
    for bad in ({"account_id": "nope"}, {"zone_name": "not a domain"}, {"max_names": 0}, {"api_token": "short"}):
        args = {"api_token": None, "account_id": None, "zone_id": None, "zone_name": None, "max_names": None, **bad}
        with pytest.raises(KeloNameError):
            await names.configure(**args)
    cfg = await names.configure(api_token=CF_TOKEN, account_id=ACC, zone_id=ZONE, zone_name="Kelo.Chat", max_names=190)
    assert cfg["enabled"] and cfg["has_token"] and cfg["zone_name"] == "kelo.chat" and cfg["max_names"] == 190
    assert CF_TOKEN not in json.dumps(cfg)
    cfg = await names.configure(api_token="", account_id=None, zone_id=None, zone_name=None, max_names=None)
    assert not cfg["enabled"] and not cfg["has_token"]


# ---- through the HTTP API
def app_for(cf):
    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, scheduler=False), tools=[],
                      cloudflare_transport=httpx.MockTransport(cf))


async def test_the_operator_api_is_owner_only_and_the_broker_endpoints_work(cf):
    app = app_for(cf)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test") as c:
        assert (await c.post("/broker/tunnel/claim", json={"name": "alice", "install_id": INSTALL_A})).status_code == 404
        assert (await c.get("/api/operator/cloudflare")).status_code == 401
        code = (await c.post("/api/devices/pair", headers=AUTH)).json()["code"]
        phone = {"Authorization": "Bearer " + (await c.post("/api/pair", json={"code": code, "name": "phone"})).json()["token"]}
        assert (await c.put("/api/operator/cloudflare", headers=phone, json={"api_token": CF_TOKEN})).status_code == 403
        r = await c.put("/api/operator/cloudflare", headers=AUTH,
                        json={"api_token": CF_TOKEN, "account_id": ACC, "zone_id": ZONE, "zone_name": "kelo.chat"})
        assert r.status_code == 200 and CF_TOKEN not in r.text and r.json()["enabled"]
        assert CF_TOKEN not in (await c.get("/api/operator/cloudflare", headers=AUTH)).text

        assert (await c.get("/broker/tunnel/check", params={"name": "alice"})).json()["available"] is True
        r = await c.post("/broker/tunnel/claim", json={"name": "alice", "install_id": INSTALL_A})
        assert r.status_code == 200 and r.json()["hostname"] == "alice.kelo.chat" and r.json()["tunnel_token"]
        r = await c.post("/broker/tunnel/claim", json={"name": "alice", "install_id": INSTALL_B})
        assert r.status_code == 409 and "taken" in r.json()["detail"]
        assert (await c.post("/broker/tunnel/claim", json={"name": "www", "install_id": INSTALL_B})).status_code == 422
        assert (await c.post("/broker/tunnel/release", json={"install_id": INSTALL_A})).json() == {"released": "alice"}
        assert (await c.post("/api/operator/cloudflare/reclaim", headers=AUTH)).json() == {"released": []}
        for _ in range(10):
            last = await c.post("/broker/tunnel/release", json={"install_id": INSTALL_B})
        assert last.status_code == 429                              # 10 claims/releases per 10 minutes per address


# ---- DuckDNS helper
def test_duckdns_labels_and_sealing():
    from cryptography.fernet import Fernet

    assert ddns.label_of("me.duckdns.org") == "me" and ddns.label_of("kelo.me.duckdns.org") == "me" and ddns.label_of("example.com") is None
    key = Fernet.generate_key().decode()
    tok = "1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f"
    sealed = ddns.seal(key, tok)
    assert tok not in sealed and ddns.unseal(key, sealed) == tok and ddns.unseal(Fernet.generate_key().decode(), sealed) is None
    with pytest.raises(ValueError):
        ddns.seal(key, "not-a-token")


async def test_the_duckdns_updater_runs_only_when_asked_and_sends_the_token_only_to_duckdns():
    import asyncio

    from cryptography.fernet import Fernet

    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(200, text="OK")

    key = Fernet.generate_key().decode()
    assert ddns.start(key, env={}) is None
    task = ddns.start(key, env={"DUCKDNS_DOMAIN": "me", "DUCKDNS_TOKEN_ENC": ddns.seal(key, "1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f")},
                      transport=httpx.MockTransport(handler), every=3600)
    for _ in range(50):
        if seen:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert seen and seen[0].host == "www.duckdns.org" and seen[0].params["domains"] == "me"
