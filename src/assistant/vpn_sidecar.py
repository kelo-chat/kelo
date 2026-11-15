"""The `vpn` container (ROLE=vpn): the one place the owner's VPN or proxy lives. It offers a SOCKS5 server on :1080 on the private network
`vpn-net`, which only egress (the browser's way out) and the server (web_fetch) can reach.

Two modes, chosen by the files the server writes into /run/kelo-vpn (read-only here), checked every 2 s; a change restarts the mode:
* wg.conf        -> `wireproxy -c wg.conf`: WireGuard in user space (no tun device, no NET_ADMIN), with a [Socks5] section on :1080;
* upstream.json  -> a forwarder in this file: SOCKS5 on :1080 that forwards each CONNECT to the owner's SOCKS5 or HTTP proxy with its
                    user name and password, which therefore never leave this container (egress's route file holds no secrets);
* neither        -> idle: nothing listens on :1080, so a connection from egress is refused (browsing is refused too: fail closed).

upstream.json: {"kind": "socks5" | "http" | "https", "host", "port", "user"?, "password"?}. The forwarder refuses private and local IP
literals as targets as well (egress already does; this is the second wall).
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import os
import ssl
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import socks5
from .netguard import is_public

log = logging.getLogger("vpn")
PORT = 1080
POLL = 2.0
RESTART_AFTER_CRASH = 10.0       # a wireproxy that exits (a bad file, a network error) is started again after this long, not every poll
CONNECT_TIMEOUT = 15.0
CHUNK = 64 * 1024


async def _http_connect(up: dict[str, Any], host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    tls = ssl.create_default_context() if up["kind"] == "https" else None
    r, w = await asyncio.wait_for(asyncio.open_connection(up["host"], int(up["port"]), ssl=tls, server_hostname=up["host"] if tls else None),
                                  CONNECT_TIMEOUT)
    try:
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        head = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
        if up.get("user"):
            token = base64.b64encode(f"{up['user']}:{up.get('password') or ''}".encode()).decode()
            head += f"Proxy-Authorization: Basic {token}\r\n"
        w.write((head + "\r\n").encode())
        raw = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), CONNECT_TIMEOUT)
        status = raw.split(b"\r\n", 1)[0].split(b" ")
        if len(status) < 2 or status[1] != b"200":
            code = status[1].decode("latin-1", "replace")[:3] if len(status) > 1 else "?"
            raise socks5.Socks5Error(f"the proxy answered {code}", socks5.NOT_ALLOWED if code in ("401", "403", "407") else socks5.HOST_UNREACHABLE)
        return r, w
    except BaseException:
        w.close()
        raise


async def open_upstream(up: dict[str, Any], host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if up["kind"] == "socks5":
        user = up.get("user") or None
        return await socks5.connect(up["host"], int(up["port"]), host, port, user=user, password=(up.get("password") or "") if user else None,
                                    timeout=CONNECT_TIMEOUT)
    return await _http_connect(up, host, port)


async def _copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while data := await src.read(CHUNK):
            dst.write(data)
            await dst.drain()
        if dst.can_write_eof():
            dst.write_eof()
    except (ConnectionError, OSError):
        pass


class Forwarder:
    """SOCKS5 server (no authentication: it is only on the private network) that forwards to one upstream proxy."""

    def __init__(self, upstream: dict[str, Any], *, opener: Callable[..., Any] = open_upstream):
        self.up, self.opener = upstream, opener

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up_w = None
        try:
            try:
                host, port = await asyncio.wait_for(socks5.accept(reader, writer), CONNECT_TIMEOUT)
            except socks5.Socks5Error:
                return
            if _literal(host) and not is_public(host):
                await socks5.reply(writer, socks5.NOT_ALLOWED)
                return
            try:
                up_r, up_w = await self.opener(self.up, host, port)
            except socks5.Socks5Error as e:
                await socks5.reply(writer, e.code if e.code else socks5.GENERAL)
                return
            except (OSError, asyncio.TimeoutError):
                await socks5.reply(writer, socks5.HOST_UNREACHABLE)
                return
            await socks5.reply(writer, socks5.OK)
            await asyncio.gather(_copy(reader, up_w), _copy(up_r, writer))
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
            pass
        finally:
            for w in (up_w, writer):
                if w is not None:
                    try:
                        w.close()
                    except OSError:
                        pass

    async def serve(self, host: str = "0.0.0.0", port: int = PORT) -> asyncio.base_events.Server:
        return await asyncio.start_server(self.handle, host, port)


def _literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _stamp(path: Path) -> float | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class Supervisor:
    """Keeps exactly what the files in `vpn_dir` ask for running."""

    def __init__(self, vpn_dir: Path, *, wireproxy: str = "wireproxy", port: int = PORT, clock: Callable[[], float] = time.monotonic):
        self.dir, self.wireproxy, self.port, self.clock = vpn_dir, wireproxy, port, clock
        self.crashed_at: float | None = None
        self.mode = "idle"
        self.stamp: tuple[Any, ...] = ()
        self.proc: asyncio.subprocess.Process | None = None
        self.server: asyncio.base_events.Server | None = None

    def wanted(self) -> tuple[str, tuple[Any, ...]]:
        wg, up = self.dir / "wg.conf", self.dir / "upstream.json"
        if (s := _stamp(wg)) is not None:
            return "wireguard", (s,)
        if (s := _stamp(up)) is not None:
            return "proxy", (s,)
        return "idle", ()

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self.proc is not None:
            if self.proc.returncode is None:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 5)
                except asyncio.TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
            self.proc = None
        self.mode = "idle"

    async def start(self, mode: str) -> None:
        if mode == "wireguard":
            self.proc = await asyncio.create_subprocess_exec(self.wireproxy, "-c", str(self.dir / "wg.conf"), "-s")
        elif mode == "proxy":
            try:
                up = json.loads((self.dir / "upstream.json").read_text())
                if up["kind"] not in ("socks5", "http", "https") or not up["host"] or not 0 < int(up["port"]) < 65536:
                    raise ValueError("bad upstream")
            except (OSError, ValueError, KeyError, TypeError):
                log.warning("vpn: upstream.json is not usable; staying idle")
                return
            self.server = await Forwarder(up).serve(port=self.port)
        self.mode = mode

    async def tick(self) -> None:
        mode, stamp = self.wanted()
        crashed = self.proc is not None and self.proc.returncode is not None
        if crashed and (mode, stamp) == (self.mode, self.stamp):
            if self.crashed_at is None:
                self.crashed_at = self.clock()
                log.warning("vpn: wireproxy stopped (exit %s); trying again in %d s", self.proc.returncode, RESTART_AFTER_CRASH)  # type: ignore[union-attr]
            if self.clock() - self.crashed_at < RESTART_AFTER_CRASH:
                return
        elif (mode, stamp) == (self.mode, self.stamp):
            return
        self.crashed_at = None
        await self.stop()
        self.stamp = stamp
        if mode != "idle":
            log.info("vpn: starting %s", mode)   # never the content: it holds keys and passwords
            try:
                await self.start(mode)
            except OSError as e:
                log.warning("vpn: could not start %s (%s)", mode, type(e).__name__)
                self.stamp = ()   # try again on the next tick
        else:
            log.info("vpn: idle (no configuration)")

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001 - the supervisor never dies
                log.warning("vpn: %s", type(e).__name__)
                self.stamp = ()
            await asyncio.sleep(POLL)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    await Supervisor(Path(os.environ.get("KELO_VPN_DIR", "/run/kelo-vpn"))).run()


if __name__ == "__main__":
    asyncio.run(main())
