"""The egress proxy's route (VPN connector): direct vs. through the vpn sidecar's SOCKS5, fail closed, the LAN block that stays, no local
DNS, and tunnels of an older route closed at once when the route changes."""

import asyncio
import json
import os

import pytest
from vpn_fakes import FakeSocks5

from assistant.egress import VPN_DOWN, Denied, Egress, RouteFile


def write_route(path, **obj):
    tmp = path.with_name("route.tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)                       # the way the server writes it


def make(tmp_path, **kw):
    path = tmp_path / "route.json"
    return path, Egress(route=RouteFile(path, every=0), **kw)


def no_dns(host, port, *a, **k):
    raise AssertionError(f"{host} was looked up locally")


async def echo_site():
    hits = []

    async def handle(r, w):
        hits.append(1)
        w.write(b"direct:" + await r.read(100))
        await w.drain()
        w.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1], hits


async def request(proxy_port, line, body=b""):
    r, w = await asyncio.open_connection("127.0.0.1", proxy_port)
    w.write(line.encode() + b"\r\n\r\n")
    await w.drain()
    head = await r.readuntil(b"\r\n\r\n")
    return r, w, head


# ------------------------------------------------------------------------------------------------------------------ the route file
def test_the_route_file_missing_means_direct_and_anything_unreadable_means_refuse(tmp_path):
    path = tmp_path / "route.json"
    rf = RouteFile(path, every=0)
    assert rf.refresh().mode == "direct"                                       # nothing was ever set up
    write_route(path, mode="socks5", host="vpn", port=1080, generation=3)
    st = rf.refresh()
    assert (st.mode, st.host, st.port) == ("socks5", "vpn", 1080)
    write_route(path, mode="direct", generation=4)
    assert rf.refresh().mode == "direct"
    for bad in ("{not json", json.dumps({"mode": "wireguard"}), json.dumps({"mode": "socks5"}), json.dumps({"mode": "socks5", "host": "vpn", "port": 0}),
                json.dumps([1])):
        path.write_text(bad)
        os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 7))
        assert rf.refresh().mode == "refuse", bad                              # fail closed: never direct on a file it cannot read


def test_the_route_file_is_looked_at_only_every_few_seconds(tmp_path):
    now = [100.0]
    path = tmp_path / "route.json"
    rf = RouteFile(path, every=2.0, clock=lambda: now[0])
    assert rf.refresh().mode == "direct"
    write_route(path, mode="socks5", host="vpn", port=1080, generation=1)
    now[0] += 1.0
    assert rf.refresh().mode == "direct"                                       # too soon
    now[0] += 1.5
    assert rf.refresh().mode == "socks5"


def test_a_new_generation_is_a_new_epoch_but_a_rewrite_of_the_same_content_is_not(tmp_path):
    path = tmp_path / "route.json"
    rf = RouteFile(path, every=0)
    write_route(path, mode="socks5", host="vpn", port=1080, generation=1)
    e1 = rf.refresh().epoch
    write_route(path, mode="socks5", host="vpn", port=1080, generation=1)
    assert rf.refresh().epoch == e1
    write_route(path, mode="socks5", host="vpn", port=1080, generation=2)
    assert rf.refresh().epoch == e1 + 1


# ---------------------------------------------------------------------------------------------------------------- through the VPN
async def test_through_the_vpn_a_name_goes_as_a_domain_and_is_never_looked_up_here(tmp_path):
    fake = await FakeSocks5().start()
    path, e = make(tmp_path, resolver=no_dns)
    write_route(path, mode="socks5", host="127.0.0.1", port=fake.port, generation=1)
    proxy = await asyncio.start_server(e.handle, "127.0.0.1", 0)
    port = proxy.sockets[0].getsockname()[1]
    try:
        r, w, head = await request(port, "CONNECT site.example.com:443 HTTP/1.1")
        assert head.startswith(b"HTTP/1.1 200")
        w.write(b"hello")
        await w.drain()
        assert await r.read(100) == b"echo:hello"
        w.close()
        r, w, head = await request(port, "CONNECT 93.184.216.34:8443 HTTP/1.1")   # an IP literal goes as the address (ATYP 1)
        assert head.startswith(b"HTTP/1.1 200")
        w.close()
        r, w, head = await request(port, "CONNECT [2606:4700::1111]:443 HTTP/1.1")
        assert head.startswith(b"HTTP/1.1 200")
        w.close()
        await asyncio.sleep(0.05)
    finally:
        proxy.close()
        await fake.close()
    got = [(q["atyp"], q["host"], q["port"], q["cmd"]) for q in fake.requests]
    assert got[:2] == [(3, "site.example.com", 443, 1), (1, "93.184.216.34", 8443, 1)]
    assert got[2][0] == 4 and got[2][2] == 443                                  # the IPv6 literal as an IPv6 address


@pytest.mark.parametrize("target", ["192.168.1.20:443", "10.0.0.1:80", "127.0.0.1:9223", "169.254.169.254:80", "100.64.0.1:443", "[::1]:80",
                                    "[fd00::1]:80", "server:8080", "nas.local:443", "printer.lan:80", "update.googleapis.com:443",
                                    "x.clients2.google.com:443"])
async def test_through_the_vpn_the_lan_block_and_the_deny_list_still_apply(tmp_path, target):
    fake = await FakeSocks5().start()
    path, e = make(tmp_path, resolver=no_dns)
    write_route(path, mode="socks5", host="127.0.0.1", port=fake.port, generation=1)
    proxy = await asyncio.start_server(e.handle, "127.0.0.1", 0)
    try:
        r, w, head = await request(proxy.sockets[0].getsockname()[1], f"CONNECT {target} HTTP/1.1")
        assert head.startswith(b"HTTP/1.1 403")
        w.close()
        await asyncio.sleep(0.05)
    finally:
        proxy.close()
        await fake.close()
    assert fake.requests == []                                                 # nothing reached the VPN


async def test_through_the_vpn_plain_http_goes_the_same_way(tmp_path):
    fake = await FakeSocks5().start()
    path, e = make(tmp_path, resolver=no_dns)
    write_route(path, mode="socks5", host="127.0.0.1", port=fake.port, generation=1)
    proxy = await asyncio.start_server(e.handle, "127.0.0.1", 0)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", proxy.sockets[0].getsockname()[1])
        w.write(b"GET http://site.example.com/x HTTP/1.1\r\nHost: site.example.com\r\n\r\n")
        await w.drain()
        got = await asyncio.wait_for(r.read(500), 5)
        assert got.startswith(b"echo:GET /x HTTP/1.1")
        w.close()
        r, w = await asyncio.open_connection("127.0.0.1", proxy.sockets[0].getsockname()[1])
        w.write(b"GET http://192.168.0.5/admin HTTP/1.1\r\nHost: 192.168.0.5\r\n\r\n")
        await w.drain()
        assert await r.read(500) == b""                                         # the LAN: closed without an answer, as before
        w.close()
    finally:
        proxy.close()
        await fake.close()
    assert [(q["atyp"], q["host"], q["port"]) for q in fake.requests] == [(3, "site.example.com", 80)]


# ----------------------------------------------------------------------------------------------------------------- fail closed
async def test_when_the_vpn_cannot_be_reached_nothing_is_sent_directly(tmp_path):
    site, site_port, hits = await echo_site()
    # the site IS reachable directly on this machine, and the resolver would send us there: it must stay untouched
    path, e = make(tmp_path, resolver=lambda h, p, *a, **k: [(2, 1, 6, "", ("127.0.0.1", p))], allow_ip=lambda ip: True)
    proxy = await asyncio.start_server(e.handle, "127.0.0.1", 0)
    port = proxy.sockets[0].getsockname()[1]
    try:
        for content in ({"mode": "socks5", "host": "127.0.0.1", "port": 1, "generation": 1},          # the sidecar is idle / not there
                        {"mode": "wireguard", "generation": 2}, "not json"):                             # an unknown or unreadable route
            if isinstance(content, dict):
                write_route(path, **content)
            else:
                path.write_text(content)
                os.utime(path, ns=(1, 3_000_000_000))
            r, w, head = await request(port, f"CONNECT site.example.com:{site_port} HTTP/1.1")
            assert head.startswith(b"HTTP/1.1 502") and VPN_DOWN.encode() in await r.read(500)
            w.close()
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(f"GET http://site.example.com:{site_port}/ HTTP/1.1\r\nHost: site.example.com\r\n\r\n".encode())
            await w.drain()
            assert (await r.read(500)).count(b"direct:") == 0
            w.close()
    finally:
        proxy.close()
        site.close()
    assert hits == []


async def test_a_vpn_that_answers_with_an_error_is_refused_not_retried_directly(tmp_path):
    site, site_port, hits = await echo_site()
    for code, text in ((1, VPN_DOWN), (2, VPN_DOWN), (4, "could not be reached through the VPN")):
        fake = await FakeSocks5(fail=code).start()
        path, e = make(tmp_path, resolver=lambda h, p, *a, **k: [(2, 1, 6, "", ("127.0.0.1", p))], allow_ip=lambda ip: True)
        write_route(path, mode="socks5", host="127.0.0.1", port=fake.port, generation=1)
        with pytest.raises(Denied) as err:
            await e.dial("site.example.com", "site.example.com", site_port)
        assert err.value.status == 502 and text in err.value.reason
        await fake.close()
    site.close()
    assert hits == []


async def test_a_start_with_the_vpn_on_and_the_sidecar_not_ready_refuses_browsing(tmp_path):
    # the situation after a restart behind Keyguard: route.json says socks5, but the files for the sidecar cannot be rebuilt yet
    path, e = make(tmp_path, resolver=no_dns)
    write_route(path, mode="socks5", host="127.0.0.1", port=1, generation=7)
    for host in ("site.example.com", "93.184.216.34"):
        with pytest.raises(Denied) as err:
            await e.dial(host, host, 443)
        assert err.value.status == 502 and err.value.reason == VPN_DOWN


# ------------------------------------------------------------------------------------------------- switching: old tunnels are closed
async def open_tunnel(proxy_port, target):
    r, w, head = await request(proxy_port, f"CONNECT {target} HTTP/1.1")
    assert head.startswith(b"HTTP/1.1 200")
    return r, w


async def test_a_tunnel_opened_direct_is_closed_when_the_route_changes_to_the_vpn_and_back(tmp_path):
    fake = await FakeSocks5().start()

    async def hold(r, w):          # a site that keeps the connection open and echoes
        while data := await r.read(100):
            w.write(b"direct:" + data)
            await w.drain()
        w.close()

    site = await asyncio.start_server(hold, "127.0.0.1", 0)
    site_port = site.sockets[0].getsockname()[1]
    path, e = make(tmp_path, resolver=lambda h, p, *a, **k: [(2, 1, 6, "", ("127.0.0.1", p))], allow_ip=lambda ip: True)
    proxy = await asyncio.start_server(e.handle, "127.0.0.1", 0)
    port = proxy.sockets[0].getsockname()[1]
    watcher = asyncio.create_task(e.watch(0.02))
    try:
        r1, w1 = await open_tunnel(port, f"site.example.com:{site_port}")                   # direct (no route file yet)
        w1.write(b"a")
        await w1.drain()
        assert await r1.read(100) == b"direct:a"
        write_route(path, mode="socks5", host="127.0.0.1", port=fake.port, generation=1)
        assert await asyncio.wait_for(r1.read(100), 3) == b""                          # closed by egress, with no new connection coming in
        r2, w2 = await open_tunnel(port, "site.example.com:443")                            # a new one goes through the VPN
        w2.write(b"b")
        await w2.drain()
        assert await r2.read(100) == b"echo:b"
        write_route(path, mode="direct", generation=2)
        assert await asyncio.wait_for(r2.read(100), 3) == b""                          # and the VPN tunnel is closed when it is turned off
        r3, w3 = await open_tunnel(port, f"site.example.com:{site_port}")
        w3.write(b"c")
        await w3.drain()
        assert await r3.read(100) == b"direct:c"
        assert e.dropped == 2
        for w in (w1, w2, w3):
            w.close()
    finally:
        watcher.cancel()
        proxy.close()
        site.close()
        await fake.close()


async def test_switching_from_one_vpn_to_another_also_closes_the_open_tunnels(tmp_path):
    fake = await FakeSocks5().start()
    path, e = make(tmp_path, resolver=no_dns)
    write_route(path, mode="socks5", host="127.0.0.1", port=fake.port, generation=1)
    proxy = await asyncio.start_server(e.handle, "127.0.0.1", 0)
    port = proxy.sockets[0].getsockname()[1]
    try:
        r1, w1 = await open_tunnel(port, "site.example.com:443")
        write_route(path, mode="socks5", host="127.0.0.1", port=fake.port, generation=2)    # same address, another VPN behind it
        r2, w2 = await open_tunnel(port, "site.example.com:443")                                  # the next new connection notices at once
        assert await asyncio.wait_for(r1.read(100), 3) == b""
        w2.write(b"x")
        await w2.drain()
        assert await r2.read(100) == b"echo:x"
        w1.close()
        w2.close()
    finally:
        proxy.close()
        await fake.close()


def test_the_socks5_bytes_on_the_wire():
    from assistant import socks5
    assert socks5.request_bytes("example.com", 443) == b"\x05\x01\x00\x03\x0bexample.com\x01\xbb"          # ATYP 0x03: the name
    assert socks5.request_bytes("93.184.216.34", 80) == b"\x05\x01\x00\x01\x5d\xb8\xd8\x22\x00\x50"            # ATYP 0x01: the IPv4 address
    assert socks5.request_bytes("::1", 80) == b"\x05\x01\x00\x04" + b"\x00" * 15 + b"\x01\x00\x50"             # ATYP 0x04: the IPv6 address
    with pytest.raises(socks5.Socks5Error):
        socks5.request_bytes("x" * 300 + ".example", 443)
