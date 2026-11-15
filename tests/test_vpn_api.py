"""The VPN / Proxy connectors through the app's API: listed in the category Network, saved with the usual card, "exit <ip>" on the row,
a change that waits for the browser and can be forced, and never shown to the model as a service."""

import asyncio
import base64
import json

import httpx

from assistant.server import Settings, create_app

from test_extract import CONTENT
from test_server import AUTH, TOKEN, Running, scripted
from test_vpn import CONF, PRIV

IDS = ("vpn_wireguard", "vpn_proxy")


def app_with(tmp_path, *, dirs=True, **kw):
    route, vpn_dir = tmp_path / "route", tmp_path / "vpn"
    if dirs:
        route.mkdir()
        vpn_dir.mkdir()

    async def probe():
        return True

    async def exit_ip():
        return "198.51.100.7"

    return create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "s.db"), home_dir=str(tmp_path / "home"),
                               content_dir=str(CONTENT), route_dir=str(route) if dirs else "", vpn_dir=str(vpn_dir) if dirs else ""),
                      tools=None, stream_fn=scripted(), vpn_probe=probe, vpn_exit=exit_ip, **kw), route, vpn_dir


async def test_the_rows_card_save_exit_address_and_disconnect(tmp_path):
    app, route, vpn_dir = app_with(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        rows = {r["id"]: r for r in (await c.get("/api/connectors")).json()}
        for cid in IDS:
            assert rows[cid]["category"] == "Network" and rows[cid]["connect"] == "key" and rows[cid]["status"] == "available"
        assert "VPN" in rows["vpn_wireguard"]["name"] and rows["vpn_proxy"]["name"] == "Proxy"
        assert rows["vpn_wireguard"]["limited"] is False
        assert (await c.get("/api/connectors/vpn_wireguard/permissions")).status_code == 404       # nothing to allow or ask
        card = (await c.get("/api/keys/vpn_wireguard")).json()
        assert [f["id"] for f in card["fields"]] == ["config"] and card["fields"][0]["secret"] and card["connected"] is False
        assert any("sign in again" in h for h in card["help"])                                 # said on the card once
        proxy_card = (await c.get("/api/keys/vpn_proxy")).json()
        assert [(f["id"], f["secret"]) for f in proxy_card["fields"]] == [("address", False), ("user", False), ("password", True)]
        assert proxy_card["fields"][0]["placeholder"] == "socks5://host:1080"
        bad = await c.post("/api/keys/vpn_wireguard", json={"values": {"config": "nonsense"}})
        assert bad.status_code == 422 and "WireGuard" in bad.json()["detail"]
        ok = await c.post("/api/keys/vpn_wireguard", json={"values": {"config": CONF.replace("\n", " ")}})
        body = ok.json()
        assert ok.status_code == 200 and body["vpn"]["state"] == "applied" and body["vpn"]["connected"] is True and body["item"] == "vpn-wireguard"
        assert (vpn_dir / "wg.conf").exists() and json.loads((route / "route.json").read_text())["mode"] == "socks5"
        rows = {r["id"]: r for r in (await c.get("/api/connectors")).json()}
        assert rows["vpn_wireguard"]["status"] == "connected" and rows["vpn_wireguard"]["account"] == "exit 198.51.100.7"
        assert rows["vpn_proxy"]["status"] == "available" and not rows["vpn_proxy"].get("account")
        st = (await c.get("/api/vpn")).json()
        assert st == {"available": True, "mode": "wireguard", "connected": True, "exit_ip": "198.51.100.7"}
        seen = str((await c.get("/api/vault")).json()) + str((await c.get("/api/keys/vpn_wireguard")).json()) + str((await c.get("/api/vault/log")).json())
        assert PRIV not in seen and "193.32.127.66" not in seen.replace("193.32.127.66:51820", "")      # the key never comes back
        # connecting the proxy turns the VPN off
        ok = await c.post("/api/keys/vpn_proxy", json={"values": {"address": "socks5://45.83.220.9:1080", "user": "u", "password": "p"}})
        assert ok.status_code == 200 and ok.json()["vpn"]["state"] == "applied"
        assert (vpn_dir / "upstream.json").exists() and not (vpn_dir / "wg.conf").exists()
        rows = {r["id"]: r for r in (await c.get("/api/connectors")).json()}
        assert rows["vpn_wireguard"]["status"] == "available" and rows["vpn_proxy"]["status"] == "connected"
        # disconnecting (deleting the vault item) returns to direct
        assert (await c.delete("/api/credentials/vpn-proxy")).status_code in (200, 204)
        for _ in range(50):
            if json.loads((route / "route.json").read_text())["mode"] == "direct":
                break
            await asyncio.sleep(0.05)
        assert json.loads((route / "route.json").read_text())["mode"] == "direct" and list(vpn_dir.iterdir()) == []
        assert (await c.get("/api/vpn")).json()["mode"] == "direct"


async def test_without_the_vpn_container_the_connectors_are_not_listed(tmp_path):
    app, route, vpn_dir = app_with(tmp_path, dirs=False)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        rows = {r["id"]: r for r in (await c.get("/api/connectors")).json()}
        assert not any(cid in rows for cid in IDS)
        r = await c.post("/api/keys/vpn_wireguard", json={"values": {"config": CONF}})
        assert r.status_code == 422 and "VPN" in r.json()["detail"]
        assert (await c.get("/api/vpn")).json()["available"] is False
        assert (await c.post("/api/vpn/apply", json={})).status_code == 404


async def test_a_change_waits_for_the_browser_and_force_makes_it_now(tmp_path):
    from assistant.tools.browser import BrowserSession

    class Open:
        def is_closed(self): return False

    session = BrowserSession()
    session._pages["turn:1"] = Open()
    session._used["turn:1"] = session.clock()                          # the agent is browsing right now
    app, route, vpn_dir = app_with(tmp_path, browser=session)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        ok = await c.post("/api/keys/vpn_wireguard", json={"values": {"config": CONF}})
        body = ok.json()
        assert ok.status_code == 200 and body["vpn"]["state"] == "queued" and "browsing finishes" in body["vpn"]["message"]
        assert not (vpn_dir / "wg.conf").exists() and not (route / "route.json").exists()
        assert (await c.get("/api/vpn")).json()["pending"] == "wireguard"
        forced = await c.post("/api/vpn/apply", json={"force": True})
        assert forced.status_code == 200 and forced.json()["state"] == "applied"
        assert (vpn_dir / "wg.conf").exists() and json.loads((route / "route.json").read_text())["mode"] == "socks5"
        # a second request, with force in the same call
        session._used["turn:1"] = session.clock()
        d = await c.post("/api/keys/vpn_proxy", json={"values": {"address": "socks5://45.83.220.9:1080"}, "force": True})
        assert d.json()["vpn"]["state"] == "applied" and (vpn_dir / "upstream.json").exists()


async def test_the_model_is_not_told_about_the_vpn(tmp_path):
    app, route, vpn_dir = app_with(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/keys/vpn_wireguard", json={"values": {"config": CONF}})
    # the table the prompt's "connected" list is built from leaves the network connectors out
    from assistant.presets import NETWORK_IDS
    assert NETWORK_IDS == set(IDS)
