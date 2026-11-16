"""Outbound HTTPS for credentialed requests, with the protections that make that safe.

* https only, a fixed list of registered hosts, no redirects;
* the host must resolve to public addresses only (no loopback, private, link-local, metadata, multicast);
* the connection goes to the address that was checked (the name is resolved once), so a DNS answer that changes
  between the check and the connect cannot reach an internal host;
* bounded time and size.

`fetch_local` below is the one exception, for the owner's own self-hosted apps (Home Assistant, Immich, ...): the origin (scheme, host,
port) is typed by the owner in the app and stored with the credential; no agent tool can set or change it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

VPN_DOWN = "the VPN is not connected right now"   # what a refused read or connection says while the VPN connector is on (vpn.py)
MAX_BODY = 1_000_000
TIMEOUT = 30.0
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class NetGuardError(Exception):
    """A request we refuse to make. The message is safe to show the agent and the user."""


def valid_host(name: str) -> str:
    """A bare lower-case DNS name (no scheme, port, path, wildcard or IP literal)."""
    h = (name or "").strip().lower()
    if not _HOST.match(h) or h.endswith((".local", ".internal", ".localhost", ".lan", ".home", ".corp")):
        raise NetGuardError(f"{name!r} is not a bare public host name (example: api.example.com)")
    try:
        ipaddress.ip_address(h)
    except ValueError:
        return h
    raise NetGuardError("IP addresses are not accepted; register a host name")


def is_public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


async def resolve_public(host: str, resolver: Callable[..., list] | None = None) -> str:
    """One public address for `host`, or a refusal when ANY address it resolves to is not public."""
    resolver = resolver or socket.getaddrinfo
    try:
        infos = await asyncio.to_thread(resolver, host, 443, 0, socket.SOCK_STREAM)
    except OSError:
        raise NetGuardError(f"could not resolve {host!r}") from None
    ips = sorted({i[4][0] for i in infos}, key=lambda x: (":" in x, x))
    if not ips:
        raise NetGuardError(f"{host!r} has no address")
    bad = [ip for ip in ips if not is_public(ip)]
    if bad:
        raise NetGuardError(f"{host!r} resolves to a non-public address ({bad[0]}); refused")
    return ips[0]


@dataclass(slots=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes
    truncated: bool


async def fetch(method: str, url: str, *, allowed_hosts: list[str], headers: dict[str, str] | None = None,
                content: bytes | None = None, resolver: Callable[..., list] | None = None,
                transport: httpx.AsyncBaseTransport | None = None, timeout: float = TIMEOUT,
                max_body: int = MAX_BODY, http2: bool = False) -> Reply:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise NetGuardError("only https:// URLs are allowed")
    if parts.username or parts.password:
        raise NetGuardError("credentials in the URL are not allowed")
    if parts.port not in (None, 443):
        raise NetGuardError("only the standard https port is allowed")
    if host not in {h.lower() for h in allowed_hosts}:
        raise NetGuardError(f"{host or '<no host>'} is not a registered host; registered: {', '.join(sorted(allowed_hosts)) or '(none)'}")
    ip = await resolve_public(host, resolver)
    target = f"https://{('[' + ip + ']') if ':' in ip else ip}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
    h = {k: v for k, v in (headers or {}).items() if k.lower() not in ("host", "content-length", "transfer-encoding", "connection")}
    h["Host"] = host
    async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=timeout, trust_env=False, http2=http2) as client:
        try:
            async with client.stream(method, target, headers=h, content=content, extensions={"sni_hostname": host}) as r:
                if 300 <= r.status_code < 400:
                    raise NetGuardError(f"the server redirected (HTTP {r.status_code}); redirects are not followed. Check the endpoint.")
                chunks, size, truncated = [], 0, False
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > max_body:
                        chunks.append(chunk[: max_body - (size - len(chunk))])
                        truncated = True
                        break
                    chunks.append(chunk)
                return Reply(r.status_code, {k.lower(): v for k, v in r.headers.items()}, b"".join(chunks), truncated)
        except httpx.TimeoutException:
            raise NetGuardError("the request timed out; if it was a write, check the state before retrying") from None
        except httpx.HTTPError as e:
            raise NetGuardError(f"the request did not complete ({type(e).__name__}); if it was a write, check the state before retrying") from None


# ---- the owner's own apps on their network (see the module note above)
def _ip_ok_local(ip: str) -> bool:
    """A private, CGNAT (Tailscale), ULA or public address. Never:
    - loopback (127.0.0.0/8, ::1): inside the container that is Kelo's own server (and the browser's DevTools next to it), so a
      connection pointed there could let outside text steer Kelo into its own admin API;
    - link-local (169.254.0.0/16, fe80::/10): 169.254.169.254 is the cloud metadata service, which on a cloud VM hands out the
      machine's credentials;
    - multicast, unspecified (0.0.0.0, ::) and reserved addresses."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return not (a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified or a.is_reserved)


def parse_local_address(text: str) -> tuple[str, str, str]:
    """(origin, base path, host) from what the owner typed: http(s)://host[:port][/base]. The host is an IP address or a name; names
    that cannot be on a network (localhost, bare IPs of loopback/link-local) are refused here already. No user info, query or fragment."""
    raw = (text or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    if not raw.isprintable() or any(c.isspace() for c in raw):
        raise NetGuardError("the address must be one line like http://192.168.0.10:8123")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise NetGuardError("the address must start with http:// or https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise NetGuardError("the address must have no user name, password, query or #part")
    host = (parts.hostname or "").lower()
    if not host:
        raise NetGuardError("the address has no host")
    try:
        port = parts.port
    except ValueError:
        raise NetGuardError("the port in the address is not valid") from None
    try:
        ipaddress.ip_address(host)
        if not _ip_ok_local(host):
            raise NetGuardError(f"{host} cannot be used: loopback, link-local, multicast and unspecified addresses are refused")
    except ValueError:
        if host == "localhost" or host.endswith(".localhost") or not re.fullmatch(r"[a-z0-9]([a-z0-9._-]{0,251}[a-z0-9])?", host):
            raise NetGuardError(f"{host!r} cannot be used as a server address") from None
    shown = f"[{host}]" if ":" in host else host
    origin = f"{parts.scheme}://{shown}" + (f":{port}" if port else "")
    base = parts.path.rstrip("/")
    if base and not re.fullmatch(r"/[A-Za-z0-9._~\-/%]*", base):
        raise NetGuardError("the path in the address has unusual characters")
    return origin, base, host


def origin_key(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise NetGuardError("the port in the address is not valid") from None
    return scheme, (parts.hostname or "").lower(), port


async def resolve_local(host: str, port: int, resolver: Callable[..., list] | None = None) -> str:
    """One address for `host`, or a refusal when ANY address it resolves to is loopback, link-local, multicast or unspecified."""
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        resolver = resolver or socket.getaddrinfo
        try:
            infos = await asyncio.to_thread(resolver, host, port, 0, socket.SOCK_STREAM)
        except OSError:
            raise NetGuardError(f"could not find {host!r} on the network") from None
        ips = sorted({i[4][0] for i in infos}, key=lambda x: (":" in x, x))
    if not ips:
        raise NetGuardError(f"{host!r} has no address")
    bad = [ip for ip in ips if not _ip_ok_local(ip)]
    if bad:
        raise NetGuardError(f"{host!r} resolves to {bad[0]}, which is refused (loopback, link-local, multicast or unspecified)")
    return ips[0]


async def fetch_local(method: str, url: str, *, origin: str, headers: dict[str, str] | None = None, content: bytes | None = None,
                      resolver: Callable[..., list] | None = None, transport: httpx.AsyncBaseTransport | None = None,
                      timeout: float = TIMEOUT, max_body: int = MAX_BODY, **_: Any) -> Reply:
    """A request to the owner's own app. `url` must have exactly the stored origin (scheme, host, port); the name is resolved once
    and that address is connected to; no redirects."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise NetGuardError("credentials in the URL are not allowed")
    want = origin_key(origin)
    if parts.scheme not in ("http", "https") or origin_key(url) != want:
        raise NetGuardError(f"this connection only reaches {origin}; the URL does not match it")
    scheme, host, port = want
    ip = await resolve_local(host, port, resolver)
    target = f"{scheme}://{('[' + ip + ']') if ':' in ip else ip}:{port}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
    h = {k: v for k, v in (headers or {}).items() if k.lower() not in ("host", "content-length", "transfer-encoding", "connection")}
    h["Host"] = urlsplit(origin).netloc
    ext = {"sni_hostname": host} if scheme == "https" and not _is_ip(host) else {}
    async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=timeout, trust_env=False) as client:
        try:
            async with client.stream(method, target, headers=h, content=content, extensions=ext) as r:
                if 300 <= r.status_code < 400:
                    raise NetGuardError(f"the server redirected (HTTP {r.status_code}); redirects are not followed. Check the address.")
                chunks, size, truncated = [], 0, False
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > max_body:
                        chunks.append(chunk[: max_body - (size - len(chunk))])
                        truncated = True
                        break
                    chunks.append(chunk)
                return Reply(r.status_code, {k.lower(): v for k, v in r.headers.items()}, b"".join(chunks), truncated)
        except httpx.TimeoutException:
            raise NetGuardError("the request timed out; if it was a write, check the state before retrying") from None
        except httpx.HTTPError as e:
            raise NetGuardError(f"the request did not complete ({type(e).__name__}); if it was a write, check the state before retrying") from None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
