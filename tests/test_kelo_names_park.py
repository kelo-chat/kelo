"""Two-stage idle reclaim of free <name>.kelo.chat names: a name that connected before but has had no connector for 5 days is PARKED
(its tunnel and CNAME go, the slot is free and does not count toward max_names, the name stays reserved for its copy); its own copy
asking again gets a new tunnel and token under the same name, without counting against the daily cap or the breaker; after 30 days
without a connector the name is free for anyone. The copy side asks again on start and every few hours (connect.kelo_refresh).
Fake Cloudflare and fake broker; nothing leaves this machine."""

import httpx
import pytest

from assistant import connect
from assistant.cli import read_env
from assistant.kelo_names import IDLE_DAYS, PARK_DAYS, PER_IP_DAY, KeloNameError
from assistant.updater_service import kelo_loop

from test_connect_cli import make
from test_connect_ways import Broker, Reply
from test_kelo_names import FakeCloudflare, ready

A, B = "A" * 40, "B" * 40
T0 = 1_800_000_000.0      # 2027-01-15T08:00:00Z
DAY = 86400.0


def iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def cf():
    return FakeCloudflare()


async def parked_world(cf, **kw):
    """`alice` (copy A) connected once, at T0, then went quiet; the clock is PARK_DAYS + 1 days later, after a sweep."""
    now = [T0]
    names = await ready(cf, clock=lambda: now[0], **kw)
    got = await names.claim("alice", A, "203.0.113.7")
    t = next(t for t in cf.live() if t["name"] == "kelo-alice")
    t.update(status="down", conns_active_at=iso(T0), conns_inactive_at=iso(T0 + 600))
    await names.tick()                                          # seen connected
    now[0] = T0 + (PARK_DAYS + 1) * DAY
    assert await names.reclaim_idle() == [] and names.last_parked == ["alice"]
    return names, now, got


async def test_five_days_idle_parks_the_name_frees_the_slot_and_keeps_the_name(cf):
    names, now, _ = await parked_world(cf)
    assert cf.live() == [] and cf.records == {}                 # the tunnel and the CNAME are gone
    assert names.count() == 0 and names.parked() == 1 and names.config()["parked"] == 1
    assert names.check("alice")["reason"] == "taken"
    with pytest.raises(KeloNameError, match="taken") as e:
        await names.claim("alice", B, "198.51.100.2")           # nobody else gets a parked name
    assert e.value.status == 409
    assert await names.reclaim_idle() == []                     # a parked name stays parked until its 30 days are up


async def test_its_own_copy_gets_it_back_with_a_new_token_outside_the_cap_and_the_breaker(cf, monkeypatch):
    names, now, first = await parked_world(cf)
    for i in range(PER_IP_DAY):                                 # the copy's network has used its daily cap meanwhile
        await names.claim(f"other{i}", f"O{i}".ljust(40, "o"), "203.0.113.7")
    monkeypatch.setattr("assistant.kelo_names.PENDING_MAX", 1)  # and the breaker is tripped
    with pytest.raises(KeloNameError, match="busy"):
        await names.claim("newbie", "N" * 40, "198.51.100.9")
    back = await names.claim("alice", A, "203.0.113.7")
    assert back["hostname"] == "alice.kelo.chat" and back["tunnel_token"] != first["tunnel_token"]
    live = {t["name"]: t for t in cf.live()}
    assert "kelo-alice" in live and any(r["name"] == "alice.kelo.chat" for r in cf.records.values())
    assert cf.configs[live["kelo-alice"]["id"]]["ingress"][0]["hostname"] == "alice.kelo.chat"
    row = names.by_name("alice")
    assert row["parked_at"] == 0 and row["tunnel_id"] == live["kelo-alice"]["id"] and row["connected_at"] > 0
    assert names.parked() == 0 and names.count() == PER_IP_DAY + 1
    again = await names.claim("alice", A, "203.0.113.7")        # asked again while it runs: the same token, nothing made
    assert again["tunnel_token"] == back["tunnel_token"]


async def test_after_thirty_days_without_a_connector_the_name_is_free(cf):
    names, now, _ = await parked_world(cf)
    now[0] = T0 + 600 + IDLE_DAYS * DAY - 60
    assert await names.reclaim_idle() == [] and names.by_name("alice") is not None
    now[0] = T0 + 600 + IDLE_DAYS * DAY + 60                     # 30 days from the last time a connector was seen
    assert await names.reclaim_idle() == ["alice"]
    assert names.by_name("alice") is None and names.check("alice")["available"] is True
    await names.claim("alice", B, "198.51.100.2")


async def test_parked_names_do_not_count_toward_the_cap_but_need_a_slot_to_come_back(cf):
    names, now, _ = await parked_world(cf)
    await names.configure(api_token=None, account_id=None, zone_id=None, zone_name=None, max_names=1)
    await names.claim("bob", B, "198.51.100.2")                 # alice is parked: the one slot is free
    assert names.count() == 1 and names.check("carol")["reason"] == "full"
    with pytest.raises(KeloNameError) as e:
        await names.claim("alice", A, "203.0.113.7")            # its owner is back, but the pool is full now
    assert e.value.status == 503 and e.value.code == "full" and names.by_name("alice")["parked_at"] > 0


async def test_a_tunnel_deleted_by_hand_is_made_again_for_its_owner_without_counting(cf):
    now = [T0]
    names = await ready(cf, clock=lambda: now[0])
    for i in range(PER_IP_DAY):
        await names.claim(f"name{i}", f"I{i}".ljust(40, "i"), "203.0.113.7")
    tid = names.by_name("name0")["tunnel_id"]
    cf.tunnels[tid]["deleted_at"] = "2027-01-15T09:00:00Z"
    back = await names.claim("name0", "I0".ljust(40, "i"), "203.0.113.7")   # the daily cap is used, but it is its own name
    assert back["tunnel_token"] and names.by_name("name0")["tunnel_id"] != tid


# ---- the copy's side
def kelo_copy(tmp_path):
    m, run, msgs = make(tmp_path)
    m.create("alice")
    connect.kelo_connect(m, m.load("alice"), broker="https://kelo.chat", post=Broker())
    return m, run, msgs


def test_a_copy_asks_for_its_own_name_and_restarts_the_tunnel_only_when_the_token_changed(tmp_path):
    m, run, _ = kelo_copy(tmp_path)
    tok = tmp_path / "instances" / "alice" / "kelo" / "tunnel.env"
    same = Broker()
    n = len(run.calls)
    assert connect.kelo_refresh(m, m.load("alice"), post=same) is None and len(run.calls) == n
    assert same.calls[0][1] == {"name": "alice", "install_id": read_env(m.env_path("alice"), "KELO_INSTALL_ID")}
    new = Broker(claim=Reply(200, {"name": "alice", "hostname": "alice.kelo.chat", "tunnel_token": "eyJ-new-token-0123456789abcdefghijk"}))
    note = connect.kelo_refresh(m, m.load("alice"), post=new)
    assert "made again" in note and "eyJ" not in note
    assert read_env(tok, "TUNNEL_TOKEN") == "eyJ-new-token-0123456789abcdefghijk"
    assert any("--force-recreate" in c and "kelo-tunnel" in c for c in run.calls[n:])
    busy = Broker(claim=Reply(503, {"detail": {"code": "full", "message": "x"}, "code": "full"}))
    assert "all free Kelo addresses are taken" in connect.kelo_refresh(m, m.load("alice"), post=busy)
    assert read_env(tok, "TUNNEL_TOKEN") == "eyJ-new-token-0123456789abcdefghijk"   # kept when the broker says no
    assert connect.kelo_refresh(m, m.load(), post=same) is None                        # the main copy is not on a Kelo address


def test_starting_a_copy_and_the_updaters_rounds_ask_again(tmp_path):
    m, run, msgs = kelo_copy(tmp_path)
    seen = []
    m.on_start = lambda inst: seen.append(inst.name) or "the tunnel was made again"
    m.start("alice", build_if_missing=False)
    assert seen == ["alice"] and "the tunnel was made again" in msgs

    class Svc:
        def manager(self):
            return m
    asked = []
    kelo_loop(Svc(), refresh=lambda mm, inst: asked.append(inst.name), sleep=lambda s: None, rounds=2)
    assert asked == ["main", "alice", "main", "alice"]


def test_the_broker_wire_is_never_reached_by_the_refresh_in_tests(tmp_path, monkeypatch):
    m, run, _ = kelo_copy(tmp_path)
    monkeypatch.setattr(connect, "_post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline in tests")))
    assert "not answering" in connect.kelo_refresh(m, m.load("alice"))
