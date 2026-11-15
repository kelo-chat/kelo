"""Kelo's web browsing through the route (vpn.py): web_fetch / web_search page reads through the sidecar's SOCKS5 (names resolved there),
the browser tool's guard, "the agent is browsing" for the switch, and the compose file's walls."""

import asyncio
import re
import time
from pathlib import Path

import pytest
from vpn_fakes import FakeSocks5

from assistant.netguard import VPN_DOWN, NetGuardError
from assistant.tools.browser import BrowserPolicyError, BrowserSession, check_url
from assistant.tools.order import Orders
from assistant.tools.web import WebTools
from assistant.watchers import public_get

pytest.importorskip("socksio")      # httpx[socks] (pyproject); a plain dev environment without it skips these


def no_dns(host, port, *a, **k):
    if re.fullmatch(r"[0-9a-f:.]+", host):      # an IP literal is not a lookup
        return [(2, 1, 6, "", (host, port))]
    raise AssertionError(f"{host} was looked up locally")


async def web_site(body=b"<html><head><title>Hi</title></head><body><p>Hello through the VPN</p></body></html>"):
    seen = []

    async def handle(r, w):
        head = await r.readuntil(b"\r\n\r\n")
        seen.append(head.decode())
        w.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
        await w.drain()
        w.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1], seen


# ----------------------------------------------------------------------------------------------------------------------- web reads
async def test_a_page_read_goes_through_the_socks_proxy_with_the_name_and_no_local_lookup():
    site, site_port, seen = await web_site()
    fake = await FakeSocks5(backend=("127.0.0.1", site_port)).start()
    try:
        page = await public_get("http://news.example.com/story?id=1", allow_http=True, resolver=no_dns, proxy=f"socks5h://127.0.0.1:{fake.port}")
        assert page.status == 200 and "Hello through the VPN" in page.body
        assert [(q["atyp"], q["host"], q["port"]) for q in fake.requests] == [(3, "news.example.com", 80)]    # the NAME: the proxy resolves it
        assert seen[0].startswith("GET /story?id=1 HTTP/1.1") and "Host: news.example.com" in seen[0]
        # a proxy given as a function (web tools ask at every read): None means direct, which here would need a lookup
        page = await public_get("http://news.example.com/", allow_http=True, resolver=no_dns, proxy=lambda: f"socks5h://127.0.0.1:{fake.port}")
        assert page.status == 200
    finally:
        site.close()
        await fake.close()


async def test_through_the_proxy_the_other_netguard_rules_still_apply():
    fake = await FakeSocks5().start()
    via = f"socks5h://127.0.0.1:{fake.port}"
    try:
        for url in ("http://192.168.0.5/admin", "http://127.0.0.1/", "http://localhost/", "http://nas.local/", "http://server/", "ftp://example.com/",
                    "https://user:pw@example.com/", "http://example.com:8080/", "http://[::1]/"):
            with pytest.raises(NetGuardError):
                await public_get(url, allow_http=True, resolver=no_dns, proxy=via)
        assert fake.requests == []                                                # none of them left this machine
    finally:
        await fake.close()


async def test_a_page_read_fails_closed_when_the_vpn_cannot_be_reached():
    site, site_port, seen = await web_site()
    fake = await FakeSocks5().start()
    port = fake.port
    await fake.close()                                                           # nothing listens on the sidecar's port
    try:
        # the site IS reachable directly (the resolver would send us there): the read must not go there
        with pytest.raises(NetGuardError) as e:
            await public_get(f"http://news.example.com/", allow_http=True, proxy=f"socks5h://127.0.0.1:{port}",
                             resolver=lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", site_port))])
        assert VPN_DOWN in str(e.value) and seen == []
        bad = await FakeSocks5(fail=1).start()                                   # the sidecar answers "failure"
        with pytest.raises(NetGuardError) as e:
            await public_get("http://news.example.com/", allow_http=True, proxy=f"socks5h://127.0.0.1:{bad.port}")
        assert VPN_DOWN in str(e.value) and seen == []
        await bad.close()
    finally:
        site.close()


async def test_web_fetch_asks_for_the_route_at_every_read():
    site, site_port, seen = await web_site()
    fake = await FakeSocks5(backend=("127.0.0.1", site_port)).start()
    route = {"url": f"socks5h://127.0.0.1:{fake.port}"}
    w = WebTools(proxy=lambda: route["url"])
    try:
        out = await w.web_fetch({"url": "http://news.example.com/"})
        assert "Hello through the VPN" in str(out) and len(fake.requests) == 1
        route["url"] = None                                                      # the VPN was turned off: the next read is direct again
        with pytest.raises(NetGuardError):
            await w.web_fetch({"url": "http://192.168.0.5/"})                   # (and the LAN is still refused)
        assert len(fake.requests) == 1
    finally:
        site.close()
        await fake.close()


# ------------------------------------------------------------------------------------------------------------ the browser tool's guard
async def test_with_the_vpn_on_the_browser_guard_does_not_look_names_up_but_keeps_its_other_checks():
    await check_url("https://news.example.com/", resolver=no_dns, lookup=False)
    for url in ("http://192.168.0.1/", "http://127.0.0.1/", "http://[::1]/", "http://localhost/", "http://server/", "ftp://example.com/",
                "http://169.254.169.254/latest/meta-data"):
        with pytest.raises(BrowserPolicyError):
            await check_url(url, resolver=no_dns, internal_names=frozenset({"server", "browser"}), lookup=False)


async def test_a_session_follows_the_route_for_name_lookups():
    on = {"v": False}
    looked = []

    def resolver(host, port, *a, **k):
        looked.append(host)
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    class Page:
        url = "about:blank"
        def is_closed(self): return False
        async def goto(self, url, **kw): self.url = url
        async def title(self): return "T"
        async def evaluate(self, js): return "text"
        async def wait_for_load_state(self, *a, **kw): pass

    async def connect(): return Page()

    s = BrowserSession(connect=connect, resolver=resolver)
    s.remote_dns = lambda: on["v"]
    await s._guard("https://a.example.com/")
    on["v"] = True
    await s._guard("https://b.example.com/")
    assert looked == ["a.example.com"]


async def test_a_page_that_cannot_load_because_the_vpn_is_down_says_so():
    class Page:
        url = "about:blank"
        def is_closed(self): return False
        async def goto(self, url, **kw): raise RuntimeError("Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://a.example.com/")
        async def wait_for_load_state(self, *a, **kw): pass

    async def connect(): return Page()

    async def down(): return "The VPN is not connected right now. Browsing is paused."

    s = BrowserSession(connect=connect, resolver=lambda h, p, *a, **k: [(2, 1, 6, "", ("93.184.216.34", p))])
    with pytest.raises(RuntimeError):                                            # no VPN: the plain error as before
        await s.open("https://a.example.com/")
    s.vpn_note = down
    with pytest.raises(BrowserPolicyError) as e:
        await s.open("https://a.example.com/")
    assert "VPN is not connected" in str(e.value)

    async def fine(): return ""
    s.vpn_note = fine                                                            # the VPN works: the real error shows
    with pytest.raises(RuntimeError):
        await s.open("https://a.example.com/")


# ------------------------------------------------------------------------------------------------ "the agent is browsing"
def test_the_browser_is_busy_when_a_tab_was_used_in_the_last_two_minutes():
    now = [1000.0]
    s = BrowserSession(clock=lambda: now[0])
    assert not s.busy()
    class Open:
        def is_closed(self): return False
    s._pages["turn:1"] = Open()
    s._used["turn:1"] = 950.0
    assert s.busy()
    now[0] = 1075.0                                                              # 125 seconds after the last use
    assert not s.busy()
    s._used["gone"] = now[0]                                                     # a tab that is not open does not count
    assert not s.busy()
    s.touch("turn:1")
    assert s.busy()


def test_a_purchase_window_or_an_order_counts_as_paying():
    s = BrowserSession()
    assert not s.purchase_open()
    s.allow_purchase("shop.example", seconds=60)
    assert s.purchase_open()
    s._purchase.clear()
    now = [1000.0]
    o = Orders(scope=lambda: "turn:1", owner=lambda: "tab1", clock=lambda: now[0])
    assert not o.open_now()
    o.start("shop.example", "buy a thing")
    assert o.open_now()
    now[0] += 3600 * 3
    assert not o.open_now()
    o.start("shop.example", "again")
    o.alive = lambda scope: False                                                # its task is over
    assert not o.open_now()


# ------------------------------------------------------------------------------------------------------------ the compose file's walls
COMPOSE = (Path(__file__).resolve().parents[1] / "compose.yml").read_text()


def service(name: str) -> str:
    m = re.search(rf"^  {name}:[^\n]*\n(.*?)(?=^  \S|^networks:|\Z)", COMPOSE, re.M | re.S)
    assert m, name
    return m.group(1)


def test_the_vpn_sidecar_has_no_privileges_and_its_own_files_only():
    v = service("vpn")
    assert "ROLE: vpn" in v and "<<: *hardening" in v and "read_only: true" in v and "mem_limit: ${VPN_MEMORY:-64m}" in v
    assert "cap_add" not in v and "devices" not in v and "/dev/net/tun" not in v and "NET_ADMIN" not in v and "privileged" not in v
    assert "env_file" not in v and "ports:" not in v
    assert "networks: [egress-out, vpn-net]" in v and "browser-net" not in v
    assert 'volumes: ["kelo-vpn:/run/kelo-vpn:ro"]' in v and "kelo-route" not in v


def test_egress_sees_the_route_file_but_never_the_keys_and_the_browser_is_not_on_the_vpn_network():
    e = service("egress")
    assert "kelo-route:/run/kelo-route:ro" in e and "kelo-vpn" not in e and "vpn-net" in e
    b = service("browser")
    assert "vpn-net" not in b and "kelo-vpn" not in b and "kelo-route" not in b and "networks: [browser-net]" in b
    s = service("server")
    assert "kelo-route:/run/kelo-route" in s and "kelo-vpn:/run/kelo-vpn" in s and ":ro" not in s.split("kelo-route")[1].split(",")[0]
    assert "vpn-net" in s and "KELO_VPN_DIR: /run/kelo-vpn" in s
    for other in ("workroom", "claude-cli", "agy-cli", "updater", "cloudflared", "kelo-tunnel", "caddy"):
        assert "vpn-net" not in service(other) and "kelo-vpn" not in service(other)
    assert re.search(r"^  vpn-net: \{internal: true\}", COMPOSE, re.M)
    assert re.search(r"^  kelo-route: \{\}", COMPOSE, re.M) and re.search(r"^  kelo-vpn: \{\}", COMPOSE, re.M)


def test_wireproxy_is_pinned_with_a_hash_per_architecture_and_checked_in_the_build():
    lock = (Path(__file__).resolve().parents[1] / "docker/wireproxy.lock").read_text()
    assert re.search(r"^WIREPROXY_VERSION=\d+\.\d+\.\d+$", lock, re.M)
    assert len(re.findall(r"^WIREPROXY_SHA256_(AMD64|ARM64)=[0-9a-f]{64}$", lock, re.M)) == 2
    docker = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "sha256sum -c" in docker and "wireproxy.lock" in docker and "amd64" in docker and "arm64" in docker
    assert 'ROLE: vpn' in COMPOSE and "vpn)" in (Path(__file__).resolve().parents[1] / "docker/entrypoint.sh").read_text()
