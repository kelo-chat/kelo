"""Fake servers for the VPN connector tests: a SOCKS5 server (optionally with user/password) that records every CONNECT it gets
and answers like an upstream site (it echoes), and a tiny HTTP CONNECT proxy."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import struct


class FakeSocks5:
    def __init__(self, *, user: str | None = None, password: str | None = None, fail: int = 0, answer: bytes = b"",
                 backend: tuple[str, int] | None = None):
        self.user, self.password, self.fail, self.answer = user, password, fail, answer
        self.backend = backend                  # (host, port): every CONNECT is relayed there (a local web server standing in for the site)
        self.requests: list[dict] = []          # {"atyp", "host", "port", "auth": (user, password) | None, "raw": bytes}
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self, host: str = "127.0.0.1") -> "FakeSocks5":
        self.server = await asyncio.start_server(self.handle, host, 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def handle(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            ver, n = await r.readexactly(2)
            methods = await r.readexactly(n)
            auth = None
            if self.user is not None:
                if 2 not in methods:
                    w.write(b"\x05\xff")
                    return
                w.write(b"\x05\x02")
                await r.readexactly(1)
                u = await r.readexactly((await r.readexactly(1))[0])
                p = await r.readexactly((await r.readexactly(1))[0])
                auth = (u.decode(), p.decode())
                ok = auth == (self.user, self.password)
                w.write(b"\x01" + (b"\x00" if ok else b"\x01"))
                if not ok:
                    return
            else:
                w.write(b"\x05\x00")
            head = await r.readexactly(4)
            atyp = head[3]
            if atyp == 3:
                ln = (await r.readexactly(1))[0]
                host = (await r.readexactly(ln)).decode()
            elif atyp == 1:
                host = str(ipaddress.IPv4Address(await r.readexactly(4)))
            else:
                host = str(ipaddress.IPv6Address(await r.readexactly(16)))
            port = struct.unpack(">H", await r.readexactly(2))[0]
            self.requests.append({"atyp": atyp, "host": host, "port": port, "auth": auth, "cmd": head[1]})
            w.write(b"\x05" + bytes([self.fail]) + b"\x00\x01\x00\x00\x00\x00\x00\x00")
            await w.drain()
            if self.fail:
                return
            if self.backend:
                br, bw = await asyncio.open_connection(*self.backend)

                async def back():
                    while d := await br.read(65536):
                        w.write(d)
                        await w.drain()
                    w.close()

                task = asyncio.create_task(back())
                while data := await r.read(65536):
                    bw.write(data)
                    await bw.drain()
                bw.close()
                task.cancel()
                return
            if self.answer:
                w.write(self.answer)
            while data := await r.read(65536):
                w.write(b"echo:" + data)
                await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()


class FakeHttpProxy:
    def __init__(self, *, user: str | None = None, password: str | None = None):
        self.user, self.password = user, password
        self.requests: list[dict] = []
        self.server = None
        self.port = 0

    async def start(self) -> "FakeHttpProxy":
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def handle(self, r, w):
        try:
            head = (await r.readuntil(b"\r\n\r\n")).decode()
            line, *hdrs = head.split("\r\n")
            auth = next((h.split(": ", 1)[1] for h in hdrs if h.lower().startswith("proxy-authorization")), "")
            self.requests.append({"line": line, "auth": auth})
            if self.user is not None and auth != "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode():
                w.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
                return
            w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await w.drain()
            while data := await r.read(65536):
                w.write(b"echo:" + data)
                await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()
