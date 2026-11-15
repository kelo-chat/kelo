"""Abuse limits of the free <name>.kelo.chat broker, against the fake Cloudflare of test_kelo_names.py: a name whose tunnel never had a
connector is released an hour after its claim (a connected one stays), one network gets at most 3 new names a day, 30 names waiting for
their first connector trip a breaker (503 busy with the real time left, the operator told once) that lifts as soon as fewer wait, and a
full pool answers 503 full with the operator told at 50, 70 and 90% and at full, once per fill. The CLI explains every refusal. Nothing reaches
the real Cloudflare API."""

import httpx
import pytest

from assistant import connect
from assistant.api.operator import claim_ip
from assistant.kelo_names import PENDING_MAX, PER_IP_DAY, UNCONNECTED_SECONDS, KeloNameError

from test_kelo_names import ACC, CF_TOKEN, ZONE, FakeCloudflare, app_for, ready
from test_server import AUTH


def install(i):
    return f"install{i:04d}".ljust(40, "x")


@pytest.fixture
def cf():
    return FakeCloudflare()


# ---- 1. names that never connected
async def test_a_name_that_never_connected_goes_after_an_hour_and_a_connected_one_stays(cf):
    now = [1_800_000_000.0]
    names = await ready(cf, clock=lambda: now[0])
    await names.claim("ghost", install(1))
    await names.claim("real", install(2))
    await names.claim("blip", install(3))
    assert names.unconnected() == 3
    by = {t["name"]: t for t in cf.live()}
    by["kelo-real"].update(status="healthy", conns_active_at="2027-01-15T08:00:00Z")      # its copy started cloudflared
    now[0] += 20 * 60
    assert await names.tick() == []                                                    # within the hour: nothing goes
    assert names.unconnected() == 2 and names.by_name("real")["connected_at"] > 0
    by["kelo-blip"].update(status="down", conns_inactive_at="2027-01-15T08:10:00Z")     # connected once, then went away
    now[0] += UNCONNECTED_SECONDS
    assert await names.tick() == ["ghost"]
    assert {t["name"] for t in cf.live()} == {"kelo-real", "kelo-blip"} and len(cf.records) == 2   # tunnel and CNAME gone
    assert names.by_name("ghost") is None and names.unconnected() == 0
    n = len(cf.calls)
    now[0] += 600
    assert await names.tick() == [] and len(cf.calls) == n                             # nothing waits: no Cloudflare call until 6 h
    assert names.by_name("blip") is not None                                           # the 30-day idle rule still applies to it


# ---- 2. per network, per day
async def test_the_fourth_new_name_from_one_network_in_a_day_is_refused(cf):
    now = [1_800_000_000.0]
    names = await ready(cf, clock=lambda: now[0])
    for i in range(PER_IP_DAY):
        await names.claim(f"name{i}", install(i), "203.0.113.7")
    again = await names.claim("name0", install(0), "203.0.113.7")                      # a copy's own name again: not counted
    assert again["hostname"] == "name0.kelo.chat"
    with pytest.raises(KeloNameError) as e:
        await names.claim("name9", install(9), "203.0.113.7")
    assert e.value.status == 429 and e.value.code == "daily_cap" and names.by_name("name9") is None
    await names.claim("other", install(10), "198.51.100.2")                            # another network is not held up
    now[0] += 86400 + 1
    await names.claim("name9", install(9), "203.0.113.7")                              # the next day


def test_the_client_address_is_believed_only_from_a_local_hop():
    def req(peer, header=None):
        scope = {"type": "http", "client": (peer, 1234), "headers": [(b"cf-connecting-ip", header.encode())] if header else []}
        from starlette.requests import Request
        return Request(scope)
    assert claim_ip(req("127.0.0.1", "203.0.113.7")) == "203.0.113.7"                 # the tunnel's connector on this machine
    assert claim_ip(req("172.18.0.3", "203.0.113.7")) == "203.0.113.7"                # a container / proxy on the local network
    assert claim_ip(req("8.8.4.4", "203.0.113.7")) == "8.8.4.4"            # straight from the internet: no made-up header
    assert claim_ip(req("127.0.0.1", "not an ip")) == "127.0.0.1"
    assert claim_ip(req("127.0.0.1", "2001:db8:1:2:3:4:5:6")) == "2001:db8:1:2::/64"  # IPv6 counts by its /64


# ---- 3. the breaker on names waiting for their first connector
def net(i):
    return {"cf-connecting-ip": f"203.0.{i // 200}.{i % 200 + 1}"}


async def configured(c):
    await c.put("/api/operator/cloudflare", headers=AUTH,
                json={"api_token": CF_TOKEN, "account_id": ACC, "zone_id": ZONE, "zone_name": "kelo.chat"})


async def test_thirty_names_waiting_to_connect_trip_the_breaker_with_the_real_time_left(cf):
    app = app_for(cf)
    names = app.state.kelo_names
    now = [1_800_000_000.0]
    names.clock = lambda: now[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test") as c:
        await configured(c)
        for i in range(PENDING_MAX):
            r = await c.post("/broker/tunnel/claim", json={"name": f"n{i:03d}", "install_id": install(i)}, headers=net(i))
            assert r.status_code == 200, r.text
            now[0] += 60
        r = await c.post("/broker/tunnel/claim", json={"name": "late", "install_id": install(99)}, headers=net(99))
        left = int(1_800_000_000.0 + UNCONNECTED_SECONDS - now[0])                     # the oldest waiting name's grace: 30 min left
        assert r.status_code == 503 and r.json()["code"] == "busy" and r.headers["retry-after"] == str(left)
        assert r.json()["detail"]["retry_after"] == left and "about 30 minute" in r.json()["detail"]["message"]
        r = await c.post("/broker/tunnel/claim", json={"name": "later", "install_id": install(98)}, headers=net(98))
        assert r.status_code == 503
        again = await c.post("/broker/tunnel/claim", json={"name": "n000", "install_id": install(0)}, headers=net(0))
        assert again.status_code == 200                                                # a copy's own name is still handed out
        notes = [n for n in (await c.get("/api/notifications", headers=AUTH)).json()["items"] if n["type"] == "operator_busy"]
        assert len(notes) == 1 and str(PENDING_MAX) in notes[0]["body"]               # once per trip

        # three copies start cloudflared: fewer wait, the breaker lifts at once (the refusal asked Cloudflare again)
        for t in [t for t in cf.live() if t["name"] in ("kelo-n001", "kelo-n002", "kelo-n003")]:
            t.update(status="healthy", conns_active_at="2027-01-15T08:00:00Z")
        now[0] += 61
        r = await c.post("/broker/tunnel/claim", json={"name": "late", "install_id": install(99)}, headers=net(99))
        assert r.status_code == 200
        # full again: a new trip is told again; it lifts when the oldest waiting names' grace ends (released by the sweep)
        for i in range(2):
            assert (await c.post("/broker/tunnel/claim", json={"name": f"mm{i}", "install_id": install(200 + i)},
                                 headers=net(200 + i))).status_code == 200
        r = await c.post("/broker/tunnel/claim", json={"name": "mm9", "install_id": install(209)}, headers=net(209))
        assert r.status_code == 503 and r.json()["code"] == "busy"
        assert len([n for n in (await c.get("/api/notifications", headers=AUTH)).json()["items"] if n["type"] == "operator_busy"]) == 2
        now[0] = 1_800_000_000.0 + UNCONNECTED_SECONDS + 1
        assert "n000" in await names.tick()                                             # the fast release
        r = await c.post("/broker/tunnel/claim", json={"name": "mm9", "install_id": install(209)}, headers=net(209))
        assert r.status_code == 200


async def test_a_burst_of_names_that_connect_does_not_trip_the_breaker(cf):
    now = [1_800_000_000.0]
    names = await ready(cf, clock=lambda: now[0])
    told = []
    names.on_busy = lambda n, until: told.append(n)
    for i in range(PENDING_MAX * 2):
        await names.claim(f"n{i:03d}", install(i))
        next(t for t in cf.live() if t["name"] == f"kelo-n{i:03d}").update(status="healthy", conns_active_at="2027-01-15T08:00:00Z")
        now[0] += 5
        if i % 10 == 9:
            await names.tick()                                                         # the sweep sees them connected
    assert names.unconnected() == 0 and names.count() == PENDING_MAX * 2 and told == []


# ---- 4. a full pool
async def test_a_full_pool_answers_full_and_the_operator_hears_at_50_70_90_percent_and_at_full_once_per_fill(cf):
    app = app_for(cf)
    names = app.state.kelo_names
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kelo.test") as c:
        await configured(c)
        await c.put("/api/operator/cloudflare", headers=AUTH, json={"max_names": 10})

        async def pool_notes():
            return [n["title"] for n in (await c.get("/api/notifications", headers=AUTH)).json()["items"] if n["type"] == "operator_pool"]
        for i in range(4):
            assert (await c.post("/broker/tunnel/claim", json={"name": f"n{i:03d}", "install_id": install(i)}, headers=net(i))).status_code == 200
        assert await pool_notes() == []
        for i in range(4, 8):
            assert (await c.post("/broker/tunnel/claim", json={"name": f"n{i:03d}", "install_id": install(i)}, headers=net(i))).status_code == 200
        assert await pool_notes() == ["Kelo addresses: 70% used (7/10)", "Kelo addresses: 50% used (5/10)"]   # newest first
        await c.post("/broker/tunnel/claim", json={"name": "n008", "install_id": install(8)}, headers=net(8))      # 9/10: 90%
        assert (await pool_notes())[0] == "Kelo addresses are almost all taken (9/10)"
        await c.post("/broker/tunnel/claim", json={"name": "n009", "install_id": install(9)}, headers=net(9))      # 10/10
        assert (await pool_notes())[0] == "Kelo addresses are all taken (10/10)"
        for i in (10, 11):
            r = await c.post("/broker/tunnel/claim", json={"name": f"n{i:03d}", "install_id": install(i)}, headers=net(i))
            assert r.status_code == 503 and r.json()["code"] == "full"
            assert "no Kelo addresses are available" in str(connect._broker_error(r, "kelo.chat"))
        assert len(await pool_notes()) == 4                                            # once per fill
        assert (await c.post("/broker/tunnel/release", json={"install_id": install(0)})).json() == {"released": "n000"}
        assert (await c.post("/broker/tunnel/claim", json={"name": "n010", "install_id": install(10)}, headers=net(10))).status_code == 200
        assert len(await pool_notes()) == 5                                            # below the cap and full again: told again
        assert (await pool_notes())[0] == "Kelo addresses are all taken (10/10)"
    assert names.count() == 10


# ---- 5. what the CLI says
def test_the_cli_explains_every_refusal():
    full = connect._broker_error(httpx.Response(503, json={"detail": {"code": "full", "message": "x"}, "code": "full"}), "kelo.chat")
    assert str(full).startswith("Sorry, no Kelo addresses are available right now. The operator has been told and is fixing it.")
    assert "assistant connect tailscale | assistant connect domain" in str(full)
    cap = connect._broker_error(httpx.Response(429, json={"detail": {"code": "daily_cap", "message": "x"}, "code": "daily_cap"}), "k")
    assert "most new Kelo addresses allowed in a day" in str(cap)
    busy = connect._broker_error(httpx.Response(503, headers={"Retry-After": "1500"},
                                                json={"detail": {"code": "busy", "message": "x", "retry_after": 1500}, "code": "busy"}), "k")
    assert "Try again in about 25 minutes" in str(busy)
    one = connect._broker_error(httpx.Response(503, headers={"Retry-After": "20"}, json={"detail": {"code": "busy"}, "code": "busy"}), "k")
    assert "about 1 minute," in str(one)
    plain = connect._broker_error(httpx.Response(429, json={"detail": "too many requests"}), "kelo.chat")
    assert "try again in 10 minutes" in str(plain)
