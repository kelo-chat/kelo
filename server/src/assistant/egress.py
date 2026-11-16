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

The route (assistant/vpn.py, the "VPN (WireGuard)" / "Proxy" connectors): /run/kelo-route/route.json says `{"mode": "direct", ...}` or
`{"mode": "socks5", "host": "vpn", "port": 1080, ...}`, always with a "generation" the server raises on every change. Direct is what is
described above. Through the VPN, connections go to the vpn sidecar as SOCKS5 CONNECT with the host NAME (remote DNS: no name is
resolved here, so none leaks to the local resolver) or the IP literal; the LAN block above still applies to IP literals and the deny
list, and a name that cannot be a public one (no dot, .local/.lan/...) is refused. FAIL CLOSED: when the sidecar cannot be reached, the
answer is 502 "the VPN is not connected right now" and nothing is sent directly; a route file that cannot be read refuses everything.
Every open tunnel is remembered with the route it was made under; as soon as the route changes (checked every 2 s by a watcher and on
every new connection) all tunnels of older routes are closed on both sides, so the browser reconnects through the new route (Chrome
keeps tunnels open and reuses them, which would otherwise let traffic keep going the old way).

Also relays the human viewer (noVNC) port to the browser (EGRESS_RELAY="6080=browser:6080"): a container on an internal network cannot
publish ports, so the viewer is published from this one.

Run: ROLE=egress (docker/entrypoint.sh) -> `python -m assistant.egress`. Env: EGRESS_PORT (3128), EGRESS_RELAY, EGRESS_MAX_CONNECTIONS (512), EGRESS_ROUTE_FILE (/run/kelo-route/route.json).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import socks5
from .hosting import paths
from .netguard import VPN_DOWN, is_public

log = logging.getLogger("egress")

# Google background services the browser calls on its own (also off by policy). Matched as the host or a subdomain of it.
DENY_HOSTS = (
    "update.googleapis.com", "clients2.google.com", "clients2.googleusercontent.com", "edgedl.me.gvt1.com", "redirector.gvt1.com",
    "dl.google.com", "clientservices.googleapis.com", "content-autofill.googleapis.com", "optimizationguide-pa.googleapis.com",
    "safebrowsingohttpgateway.googleapis.com", "chromewebstore.googleapis.com", "chromekanonymity-pa.googleapis.com",
)
LOCAL_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".corp", ".intranet", ".home.arpa", ".localdomain")
ROUTE_EVERY = 2.0
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


@dataclass(frozen=True)
class RouteState:
    mode: str = "direct"      # direct | socks5 | refuse (the file could not be read or says something unknown)
    host: str = ""
    port: int = 0
    epoch: int = 0            # raised here whenever the file's content changes: tunnels of an older epoch are closed


class RouteFile:
    """The route file the server writes (read-only here). Missing = direct (nothing was ever set up); present but unreadable or
    unknown = refuse (fail closed). Looked at most every `every` seconds, and parsed only when the file changed."""

    def __init__(self, path: Path | str | None, *, every: float = ROUTE_EVERY, clock: Callable[[], float] = time.monotonic):
        self.path = Path(path) if path else None
        self.every, self.clock = every, clock
        self.state = RouteState()
        self._at = float("-inf")
        self._stamp: Any = "unset"
        self._content: tuple[Any, ...] | None = None

    def refresh(self) -> RouteState:
        if self.path is None or self.clock() - self._at < self.every:
            return self.state
        self._at = self.clock()
        try:
            st = self.path.stat()
            stamp: Any = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            stamp = None
        except OSError:
            stamp = "error"
        if stamp == self._stamp:
            return self.state
        self._stamp = stamp
        content, mode, host, port = self._read(stamp)
        if content != self._content:   # a rewritten file with the same content is not a change
            self._content = content
            self.state = RouteState(mode, host, port, self.state.epoch + 1)
            log.info("egress: route is now %s%s", mode, f" via {host}:{port}" if mode == "socks5" else "")
        return self.state

    def _read(self, stamp: Any) -> tuple[tuple[Any, ...], str, str, int]:
        if stamp is None:
            return ("direct",), "direct", "", 0
        try:
            data = json.loads(self.path.read_text())  # type: ignore[union-attr]
            mode = data["mode"]
            if mode == "direct":
                return ("direct", data.get("generation")), "direct", "", 0
            if mode == "socks5":
                host, port = str(data["host"]), int(data["port"])
                if host and 0 < port < 65536:
                    return ("socks5", host, port, data.get("generation")), "socks5", host, port
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return ("refuse",), "refuse", "", 0


def local_name(host: str) -> bool:
    """A name that cannot be a public one: no dot, or a suffix used on home and office networks."""
    return "." not in host or host.endswith(LOCAL_SUFFIXES)


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class Egress:
    def __init__(self, *, resolver: Callable[..., Any] | None = None, allow_ip: Callable[[str], bool] = is_public,
                 deny_hosts: tuple[str, ...] = DENY_HOSTS, max_connections: int = 512, route: RouteFile | None = None):
        self.resolver = resolver
        self.allow_ip = allow_ip
        self.deny_hosts = deny_hosts
        self.slots = asyncio.Semaphore(max_connections)
        self.refused = 0
        self.route = route or RouteFile(None)
        self.live: dict[object, tuple[int, tuple[asyncio.StreamWriter, ...]]] = {}   # open tunnels and the route epoch they were made under
        self.dropped = 0                                                              # tunnels closed because the route changed
        self._seen = self.route.state.epoch

    # ------------------------------------------------------------------ the route
    def current(self) -> RouteState:
        """The route now (the file is looked at every few seconds). A new route closes every tunnel of an older one."""
        st = self.route.refresh()
        if st.epoch != self._seen:
            self._seen = st.epoch
            self.drop_older(st.epoch)
        return st

    def drop_older(self, epoch: int) -> int:
        n = 0
        for key, (e, writers) in list(self.live.items()):
            if e < epoch:
                self.live.pop(key, None)
                for w in writers:
                    with contextlib.suppress(Exception):
                        w.transport.abort()
                n += 1
        self.dropped += n
        if n:
            log.info("egress: closed %d open connections of the previous route", n)
        return n

    async def watch(self, every: float = ROUTE_EVERY) -> None:
        """Looks at the route file on its own, so open tunnels do not outlive a route change while the browser is quiet."""
        while True:
            self.current()
            await asyncio.sleep(every)

    async def dial(self, host: str, ip: str, port: int, state: RouteState | None = None) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """The connection to the site: straight to the checked address, or through the VPN sidecar (SOCKS5 CONNECT with the name, or the
        IP literal). Through the VPN a failure never falls back to direct: Denied(502)."""
        st = state or self.current()
        if st.mode == "direct":
            if not is_ip(ip):
                raise Denied(502, VPN_DOWN)   # the route changed to direct between the check and now: the browser tries again
            return await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
        if st.mode == "socks5":
            try:
                return await socks5.connect(st.host, st.port, host, port, timeout=CONNECT_TIMEOUT)
            except socks5.Socks5Error as e:
                if e.code in (socks5.NET_UNREACHABLE, socks5.HOST_UNREACHABLE, socks5.REFUSED, socks5.TTL):
                    raise Denied(502, "the site could not be reached through the VPN") from None
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                pass
        raise Denied(502, VPN_DOWN)

    async def pick(self, host: str, port: int) -> str:
        """The address to connect to, or Denied. Every address the name has must be public. Through the VPN a name is not looked up here:
        it is returned as it is, and the VPN resolves it."""
        if self.current().mode == "refuse":
            raise Denied(502, VPN_DOWN)
        if any(host == d or host.endswith("." + d) for d in self.deny_hosts):
            raise Denied(403, "a browser background service, not needed by pages")
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            if self.current().mode == "socks5":
                # remote DNS: the VPN resolves the name, nothing is looked up here (it would tell the local resolver every site visited)
                if local_name(host):
                    raise Denied(403, "a private or local network address")
                return host
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
            st = self.current()
            up_r, up_w = await self.dial(host, ip, port, st)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await self._tunnel(reader, writer, up_r, up_w, st.epoch)
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
        st = self.current()
        up_r, up_w = await self.dial(host, ip, port, st)
        up_w.write((f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode("latin-1"))
        await self._tunnel(reader, writer, up_r, up_w, st.epoch)

    async def _pick_logged(self, host: str, port: int) -> str:
        try:
            return await self.pick(host, port)
        except Denied as e:
            log.info("egress: refused %s:%d (%s)", host[:100], port, e.reason)
            raise

    async def _tunnel(self, c_r: asyncio.StreamReader, c_w: asyncio.StreamWriter, u_r: asyncio.StreamReader, u_w: asyncio.StreamWriter,
                      epoch: int) -> None:
        """_pipe, remembered under the route it was made with (closed by drop_older when the route changes)."""
        key = object()
        self.live[key] = (epoch, (c_w, u_w))
        if epoch < self.current().epoch:      # the route changed while this connection was being made
            self.drop_older(self._seen)
        try:
            await self._pipe(c_r, c_w, u_r, u_w)
        finally:
            self.live.pop(key, None)

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
    egress = Egress(max_connections=int(os.environ.get("EGRESS_MAX_CONNECTIONS", "512")),
                    route=RouteFile(paths.default_route_file()))
    server = await asyncio.start_server(egress.handle, "0.0.0.0", port, limit=HEAD_LIMIT)
    servers = [server]
    for spec in filter(None, (os.environ.get("EGRESS_RELAY") or "").split(",")):
        listen, _, target = spec.partition("=")
        host, tport = split_target(target, 0)
        servers.append(await relay(int(listen), host, tport))
    log.info("egress: proxy on :%d (public addresses only)%s", port, f", relays {os.environ['EGRESS_RELAY']}" if os.environ.get("EGRESS_RELAY") else "")
    await asyncio.gather(egress.watch(), *(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
