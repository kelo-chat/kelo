"""The vpn container (assistant/vpn_sidecar.py): the Proxy-mode forwarder with the owner's credentials, and which mode the files ask for."""

import asyncio
import json
import os
import stat
import sys

import pytest
from vpn_fakes import FakeHttpProxy, FakeSocks5

from assistant import socks5
from assistant.vpn_sidecar import Forwarder, Supervisor


async def via_forwarder(up, host, port, send=b"hi"):
    fw = Forwarder(up)
    server = await fw.serve("127.0.0.1", 0)
    try:
        r, w = await socks5.connect("127.0.0.1", server.sockets[0].getsockname()[1], host, port)
        w.write(send)
        await w.drain()
        got = await asyncio.wait_for(r.read(100), 5)
        w.close()
        return got
    finally:
        server.close()


async def test_the_forwarder_signs_in_to_a_socks5_upstream_with_the_owners_credentials_and_passes_the_name_on():
    up = await FakeSocks5(user="owner", password="s3cret").start()
    try:
        got = await via_forwarder({"kind": "socks5", "host": "127.0.0.1", "port": up.port, "user": "owner", "password": "s3cret"},
                                  "site.example", 443)
        assert got == b"echo:hi"
        assert up.requests == [{"atyp": 3, "host": "site.example", "port": 443, "auth": ("owner", "s3cret"), "cmd": 1}]   # the NAME, not an IP
    finally:
        await up.close()


async def test_a_socks5_upstream_without_a_password_and_with_a_wrong_one():
    up = await FakeSocks5().start()
    bad = await FakeSocks5(user="owner", password="right").start()
    try:
        assert await via_forwarder({"kind": "socks5", "host": "127.0.0.1", "port": up.port}, "1.1.1.1", 443) == b"echo:hi"
        assert up.requests[0]["atyp"] == 1 and up.requests[0]["auth"] is None
        with pytest.raises((socks5.Socks5Error, asyncio.IncompleteReadError, ConnectionError)):
            await via_forwarder({"kind": "socks5", "host": "127.0.0.1", "port": bad.port, "user": "owner", "password": "wrong"}, "site.example", 443)
    finally:
        await up.close()
        await bad.close()


async def test_the_forwarder_uses_http_connect_with_basic_auth_for_an_http_proxy():
    up = await FakeHttpProxy(user="u", password="p w").start()
    try:
        got = await via_forwarder({"kind": "http", "host": "127.0.0.1", "port": up.port, "user": "u", "password": "p w"}, "site.example", 443)
        assert got == b"echo:hi" and up.requests[0]["line"] == "CONNECT site.example:443 HTTP/1.1"
        with pytest.raises(socks5.Socks5Error) as e:
            await via_forwarder({"kind": "http", "host": "127.0.0.1", "port": up.port, "user": "u", "password": "no"}, "site.example", 443)
        assert e.value.code == socks5.NOT_ALLOWED
    finally:
        await up.close()


async def test_the_forwarder_refuses_private_literals_and_reports_an_unreachable_upstream():
    up = await FakeSocks5().start()
    try:
        for host in ("192.168.1.5", "127.0.0.1", "169.254.169.254", "10.0.0.1", "::1"):
            with pytest.raises(socks5.Socks5Error) as e:
                await via_forwarder({"kind": "socks5", "host": "127.0.0.1", "port": up.port}, host, 80)
            assert e.value.code == socks5.NOT_ALLOWED
        assert up.requests == []                                    # none of them even reached the upstream
        with pytest.raises(socks5.Socks5Error) as e:
            await via_forwarder({"kind": "socks5", "host": "127.0.0.1", "port": 1}, "site.example", 443)
        assert e.value.code == socks5.HOST_UNREACHABLE
    finally:
        await up.close()


async def test_the_supervisor_follows_the_files_idle_then_wireguard_then_proxy_then_idle(tmp_path):
    log = tmp_path / "args.txt"
    fake = tmp_path / "wireproxy"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexec sleep 30\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    d = tmp_path / "vpn"
    d.mkdir()
    up = await FakeSocks5().start()
    sup = Supervisor(d, wireproxy=str(fake), port=0)
    try:
        await sup.tick()
        assert sup.mode == "idle" and sup.proc is None and sup.server is None             # nothing listens: egress is refused (fail closed)
        (d / "wg.conf").write_text("[Interface]\n")
        await sup.tick()
        assert sup.mode == "wireguard" and sup.proc is not None
        for _ in range(50):
            if log.exists():
                break
            await asyncio.sleep(0.1)
        assert log.read_text().split() == ["-c", str(d / "wg.conf"), "-s"]
        pid = sup.proc.pid
        await sup.tick()                                                                    # unchanged: not restarted
        assert sup.proc.pid == pid
        os.utime(d / "wg.conf", ns=(1, 2_000_000_000))                                      # a new file: restarted
        await sup.tick()
        assert sup.proc.pid != pid
        (d / "wg.conf").unlink()
        (d / "upstream.json").write_text(json.dumps({"kind": "socks5", "host": "127.0.0.1", "port": up.port}))
        await sup.tick()
        assert sup.mode == "proxy" and sup.proc is None and sup.server is not None
        (d / "upstream.json").write_text("{not json")
        await sup.tick()
        assert sup.mode == "idle" and sup.server is None                                    # unusable file: idle, never open
        (d / "upstream.json").unlink()
        await sup.tick()
        assert sup.mode == "idle"
    finally:
        await sup.stop()
        await up.close()


async def test_a_wireproxy_that_keeps_exiting_is_not_restarted_every_poll(tmp_path):
    log = tmp_path / "starts.txt"
    fake = tmp_path / "wireproxy"
    fake.write_text(f"#!/bin/sh\necho start >> {log}\nexit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    d = tmp_path / "vpn"
    d.mkdir()
    (d / "wg.conf").write_text("x")
    now = [100.0]
    sup = Supervisor(d, wireproxy=str(fake), port=0, clock=lambda: now[0])
    try:
        for _ in range(5):
            await sup.tick()
            await asyncio.sleep(0.2)
        assert log.read_text().count("start") == 1                   # it died at once, but is not started again yet
        now[0] += 11
        await sup.tick()
        await asyncio.sleep(0.2)
        assert log.read_text().count("start") == 2
    finally:
        await sup.stop()
