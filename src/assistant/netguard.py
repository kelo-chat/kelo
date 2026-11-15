"""Outbound HTTPS for credentialed requests, with the protections that make that safe.

* https only, a fixed list of registered hosts, no redirects;
* the host must resolve to public addresses only (no loopback, private, link-local, metadata, multicast);
* the connection goes to the address that was checked (the name is resolved once), so a DNS answer that changes
  between the check and the connect cannot reach an internal host;
* bounded time and size.
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
