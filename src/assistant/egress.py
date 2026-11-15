"""The browser's only way out: a small HTTP proxy that refuses private addresses (the network-level LAN block).

Why: the browser container sits on a Docker network marked `internal` (compose: browser-net). Such a network has no gateway, so the browser
has no route to the internet, the LAN, the host or a cloud metadata service at all — whatever a page or its scripts try (fetch, WebSocket,
WebRTC, a redirect, DNS rebinding). The browser is started with --proxy-server pointing here; this container ("egress") sits on browser-net
AND on an ordinary network, and forwards a request only when every address its host name resolves to is public:
- refused: loopback, RFC 1918 private, link-local (169.254/16, incl. cloud metadata 169.254.169.254), CGNAT 100.64/10, multicast,
  reserved, unspecified, IPv6 ULA / link-local, IPv4-mapped forms of those, and names that resolve to any of them (netguard.is_public);
- the connection goes to the address that was checked (resolved once here), so a DNS answer that changes in between cannot reach the LAN;
- a short list of Google background-service hosts the browser itself would call (component updates, variations, autofill and
  optimization hints) is refused too; pages never need them;
- HTTPS goes through CONNECT (end-to-end TLS; this proxy never sees inside), plain HTTP and ws:// are forwarded as they are. A refused
  CONNECT gets 403 (the browser reports a tunnel failure); a refused plain-http request is closed without an answer (a network error).
The server reaches the browser's DevTools over browser-net directly, never through here. Works the same on Docker Desktop (Mac, Windows)
and on Linux: it needs no iptables, no capabilities and no host networking.

Also relays the human viewer (noVNC) port to the browser (EGRESS_RELAY="6080=browser:6080"): a container on an internal network cannot
publish ports, so the viewer is published from this one.

Run: ROLE=egress (docker/entrypoint.sh) -> `python -m assistant.egress`. Env: EGRESS_PORT (3128), EGRESS_RELAY, EGRESS_MAX_CONNECTIONS (512).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from .netguard import is_public

log = logging.getLogger("egress")

# Google background services the browser calls on its own (also off by policy). Matched as the host or a subdomain of it.
DENY_HOSTS = (
    "update.googleapis.com", "clients2.google.com", "clients2.googleusercontent.com", "edgedl.me.gvt1.com", "redirector.gvt1.com",
    "dl.google.com", "clientservices.googleapis.com", "content-autofill.googleapis.com", "optimizationguide-pa.googleapis.com",
    "safebrowsingohttpgateway.googleapis.com", "chromewebstore.googleapis.com", "chromekanonymity-pa.googleapis.com",
)
HEAD_LIMIT = 64 * 1024
HEAD_TIMEOUT = 30.0
CONNECT_TIMEOUT = 15.0
CHUNK = 64 * 1024


class Denied(Exception):
    def __init__(self, status: int, reason: str, *, silent: bool = False):
        super().__init__(reason)
        self.status, self.reason, self.silent = status, reason, silent


def split_target(target: str, default_port: int) -> tuple[str, int]:
    """"host:port" / "[v6]:port" / "host" -> (host, port)."""
    t = target.strip()
    if t.startswith("["):
        host, _, rest = t[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = t.rpartition(":") if t.count(":") == 1 else (t, "", "")
    try:
        p = int(port) if port else default_port
    except ValueError:
        raise Denied(400, "bad port") from None
    if not host or not 0 < p < 65536:
        raise Denied(400, "bad target")
    return host.lower().rstrip("."), p


class Egress:
    def __init__(self, *, resolver: Callable[..., Any] | None = None, allow_ip: Callable[[str], bool] = is_public,
                 deny_hosts: tuple[str, ...] = DENY_HOSTS, max_connections: int = 512):
        self.resolver = resolver
        self.allow_ip = allow_ip
        self.deny_hosts = deny_hosts
        self.slots = asyncio.Semaphore(max_connections)
        self.refused = 0

    async def pick(self, host: str, port: int) -> str:
        """The address to connect to, or Denied. Every address the name has must be public."""
        if any(host == d or host.endswith("." + d) for d in self.deny_hosts):
            raise Denied(403, "a browser background service, not needed by pages")
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            try:
                if self.resolver is not None:
                    infos = await asyncio.to_thread(self.resolver, host, port)
                else:
                    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except OSError:
                raise Denied(502, "the name does not resolve") from None
            ips = sorted({str(i[4][0]).split("%")[0] for i in infos}, key=lambda x: (":" in x, x))
        if not ips:
            raise Denied(502, "the name has no address")
        bad = [ip for ip in ips if not self.allow_ip(ip)]
        if bad:
            raise Denied(403, "a private or local network address")
        return ips[0]

    # ------------------------------------------------------------------ one client connection
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.slots.locked():
            await self._answer(writer, 503, "too many connections")
            return
        async with self.slots:
            try:
                await self._handle(reader, writer)
            except Denied as e:
                self.refused += 1
                if not e.silent:
                    await self._answer(writer, e.status, e.reason)
            except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError, asyncio.LimitOverrunError, OSError):
                pass
            finally:
                await self._close(writer)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            raise Denied(400, "bad request line")
        method, target, version = parts
        if method.upper() == "CONNECT":
            host, port = split_target(target, 443)
            ip = await self._pick_logged(host, port)
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await self._pipe(reader, writer, up_r, up_w)
            return
        u = urlsplit(target)
        if u.scheme not in ("http", "ws") or not u.hostname:
            raise Denied(400, "only http:// requests and CONNECT tunnels are proxied")
        host, port = u.hostname.lower(), u.port or 80
        try:
            ip = await self._pick_logged(host, port)
        except Denied as e:
            # A plain-http answer from here would reach the page as if the LAN host had answered (an opaque response); closing
            # without a word makes it the network error it is (the agent's own navigations are refused earlier, in browser.check_url).
            raise Denied(e.status, e.reason, silent=True) from None
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        headers = [h for h in lines[1:] if h and not h.lower().startswith(("proxy-connection:", "proxy-authorization:"))]
        if not any(h.lower().startswith("upgrade:") for h in headers):   # one request per connection keeps the forwarding simple
            headers = [h for h in headers if not h.lower().startswith(("connection:", "keep-alive:"))] + ["Connection: close"]
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
        up_w.write((f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode("latin-1"))
        await self._pipe(reader, writer, up_r, up_w)

    async def _pick_logged(self, host: str, port: int) -> str:
        try:
            return await self.pick(host, port)
        except Denied as e:
            log.info("egress: refused %s:%d (%s)", host[:100], port, e.reason)
            raise

    @staticmethod
    async def _pipe(c_r: asyncio.StreamReader, c_w: asyncio.StreamWriter, u_r: asyncio.StreamReader, u_w: asyncio.StreamWriter) -> None:
        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(CHUNK):
                    dst.write(data)
                    await dst.drain()
                if dst.can_write_eof():
                    dst.write_eof()
            except (ConnectionError, OSError):
                pass

        try:
            await asyncio.gather(copy(c_r, u_w), copy(u_r, c_w))
        finally:
            await Egress._close(u_w)

    @staticmethod
    async def _answer(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        body = f"Blocked by Kelo's browser network guard: {reason}\n".encode()
        text = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway", 503: "Service Unavailable"}.get(status, "Error")
        try:
            writer.write(f"HTTP/1.1 {status} {text}\r\nContent-Type: text/plain; charset=utf-8\r\nX-Kelo-Egress: blocked\r\n"
                         f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except (ConnectionError, OSError):
            pass

    @staticmethod
    async def _close(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def relay(listen_port: int, host: str, port: int) -> asyncio.base_events.Server:
    """A plain TCP relay (the viewer port) to one fixed address."""

    async def one(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            await Egress._close(writer)
            return
        try:
            await Egress._pipe(reader, writer, up_r, up_w)
        finally:
            await Egress._close(writer)

    return await asyncio.start_server(one, "0.0.0.0", listen_port)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    port = int(os.environ.get("EGRESS_PORT", "3128"))
    egress = Egress(max_connections=int(os.environ.get("EGRESS_MAX_CONNECTIONS", "512")))
    server = await asyncio.start_server(egress.handle, "0.0.0.0", port, limit=HEAD_LIMIT)
    servers = [server]
    for spec in filter(None, (os.environ.get("EGRESS_RELAY") or "").split(",")):
        listen, _, target = spec.partition("=")
        host, tport = split_target(target, 0)
        servers.append(await relay(int(listen), host, tport))
    log.info("egress: proxy on :%d (public addresses only)%s", port, f", relays {os.environ['EGRESS_RELAY']}" if os.environ.get("EGRESS_RELAY") else "")
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
