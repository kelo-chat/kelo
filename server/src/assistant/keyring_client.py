"""The Kelo server's side of the key container (docs/keyring.md, backlog 61): stores and removes AI-provider keys there, unlocks it
with the ring key, and routes provider calls through it. Without KEYRING_URL and KEYRING_TOKEN it is off and keys stay in the
database as before."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .keyguard import Locked
from .netguard import is_public
from .tools.sites import in_public_dns

log = logging.getLogger("uvicorn.error")

PLACEHOLDER = "KELO_KEYRING_SECRET"
INFO = b"kelo-keyring v1"
TOKEN_HEADER = "x-keyring-token"
UPSTREAM_HEADER = "x-keyring-upstream"


class KeyringError(Exception):
    """The key container refused or could not be reached. `code` is its code ("unreachable" when there was no answer)."""

    def __init__(self, code: str, status: int = 0):
        super().__init__(f"keyring: {code} ({status})" if status else f"keyring: {code}")
        self.code, self.status = code, status


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class KeyringTransport(httpx.AsyncBaseTransport):
    """Sends every request to the keyring's proxy: https://api.openai.com/v1/x?y=1 becomes {url}/fwd/{name}/v1/x?y=1 with the
    headers x-keyring-token and x-keyring-upstream (scheme://host[:port])."""

    def __init__(self, client: KeyringClient, name: str, inner: httpx.AsyncBaseTransport | None = None):
        self.client, self.name = client, name
        self.inner = inner or client.inner_transport()

    def _forward(self, request: httpx.Request, body: bytes) -> httpx.Request:
        u = request.url
        host = request.extensions.get("sni_hostname")   # netguard.fetch connects to a checked address and names the host here
        if isinstance(host, bytes):
            host = host.decode()
        host = host or u.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        default = 443 if u.scheme == "https" else 80
        port = f":{u.port}" if u.port and u.port != default and not request.extensions.get("sni_hostname") else ""
        base = self.client.url.rstrip("/")
        target = f"{base}/fwd/{self.name}{u.raw_path.decode()}"
        headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() not in ("host", "content-length", "transfer-encoding")]
        headers += [(TOKEN_HEADER, self.client.token), (UPSTREAM_HEADER, f"{u.scheme}://{host}{port}")]
        return httpx.Request(request.method, target, headers=headers, content=body)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        resp = await self.inner.handle_async_request(self._forward(request, body))
        if resp.status_code == 423:   # the keyring restarted: send the ring key again, then once more
            await resp.aclose()
            if await self.client.unlock():
                resp = await self.inner.handle_async_request(self._forward(request, body))
        return resp

    async def aclose(self) -> None:
        pass   # the inner transport belongs to the client


class KeyringClient:
    def __init__(self, url: str, token: str, ring_key: Callable[[], bytes | None], *, transport: httpx.AsyncBaseTransport | None = None):
        self.url, self.token, self.ring_key = (url or "").rstrip("/"), token or "", ring_key
        self._transport = transport
        self._inner: httpx.AsyncBaseTransport | None = None
        self.known: set[str] = set()
        self.hints: dict[str, str] = {}
        self.hosts: dict[str, list[str]] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._http = httpx.AsyncClient(base_url=self.url, transport=transport, timeout=httpx.Timeout(20.0, connect=5.0),
                                       trust_env=False) if (self.url and self.token) else None

    @property
    def enabled(self) -> bool:
        return bool(self.url and len(self.token) >= 32)

    def inner_transport(self) -> httpx.AsyncBaseTransport:
        if self._inner is None:
            self._inner = self._transport or httpx.AsyncHTTPTransport()
        return self._inner

    # ---- control calls ------------------------------------------------------------------------------------------------------------
    async def _send(self, method: str, path: str, json: Any = None) -> httpx.Response:
        if self._http is None:
            raise KeyringError("off")
        try:
            return await self._http.request(method, path, json=json, headers={TOKEN_HEADER: self.token})
        except httpx.HTTPError:
            raise KeyringError("unreachable") from None

    async def _call(self, method: str, path: str, json: Any = None) -> httpx.Response:
        r = await self._send(method, path, json)
        if r.status_code == 423:
            if not await self.unlock():
                raise KeyringError("locked", 423)
            r = await self._send(method, path, json)
        if not 200 <= r.status_code < 300:
            try:
                code = str(r.json().get("code") or "error")
            except Exception:  # noqa: BLE001
                code = "error"
            raise KeyringError(code, r.status_code)
        return r

    async def unlock(self) -> bool:
        """Send the ring key. False while Keyguard is locked, or when the keyring refuses or is away."""
        k = self.ring_key()
        if k is None:
            return False
        try:
            r = await self._send("POST", "/unlock", {"key": _b64(k)})
        except KeyringError:
            return False
        if r.status_code != 200:
            log.warning("keyring: unlock refused (%s)", r.status_code)
            return False
        await self.refresh()
        return True

    async def refresh(self) -> None:
        r = await self._call("GET", "/entries")
        entries = r.json().get("entries") or []
        self.known = {e["name"] for e in entries}
        self.hints = {e["name"]: str(e.get("hint") or "") for e in entries}
        self.hosts = {e["name"]: list(e.get("hosts") or []) for e in entries}

    async def put(self, name: str, secret: str, hosts: list[str], local: bool, hint: str, kind: str = "chat",
                  paths: tuple[str, ...] | list[str] = (), slot: dict[str, str] | None = None) -> None:
        body: dict[str, Any] = {"secret": secret, "kind": kind, "hosts": list(hosts), "local": bool(local),
                                "paths": list(paths), "hint": hint}
        if slot is not None:
            body["slot"] = slot
        await self._call("PUT", f"/entries/{name}", body)
        self.known.add(name)
        self.hints[name] = hint
        self.hosts[name] = list(hosts)

    async def delete(self, name: str) -> bool:
        r = await self._call("DELETE", f"/entries/{name}")
        self.known.discard(name)
        self.hints.pop(name, None)
        self.hosts.pop(name, None)
        return bool(r.json().get("removed"))

    async def rekey(self, old: bytes, new: bytes) -> None:
        await self._call("POST", "/rekey", {"old": _b64(old), "new": _b64(new)})

    def has(self, name: str) -> bool:
        return self.enabled and name in self.known

    def client_for(self, name: str, timeout: httpx.Timeout) -> httpx.AsyncClient:
        c = self._clients.get(name)
        if c is None or c.is_closed:
            c = self._clients[name] = httpx.AsyncClient(transport=KeyringTransport(self, name), timeout=timeout, trust_env=False)
        return c

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()
        if self._http is not None:
            await self._http.aclose()


# ---- the ring key and the entries ------------------------------------------------------------------------------------------------
def ring_key_from(keyguard: Any) -> Callable[[], bytes | None]:
    """HKDF-SHA256(salt=None, info=INFO, 32 bytes) over the key the personal-data Fernet is made from; None while Keyguard is locked."""
    def ring() -> bytes | None:
        try:
            return ring_of(keyguard.key())
        except Locked:
            return None
    return ring


def ring_of(master: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO).derive(master)


def host_entry(base_url: str) -> tuple[list[str], bool] | None:
    """The host of a base URL (lower case) and whether it is private (`local`): RFC 1918, loopback, link-local, CGNAT, a name that is
    not in the public DNS (sites.in_public_dns), or a name that resolves to such an address. None when there is no host."""
    try:
        host = (urlsplit(base_url or "").hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return [host], not is_public(host)
    except ValueError:
        pass
    if not in_public_dns(host):
        return [host], True
    try:
        ips = {i[4][0] for i in socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM)}
    except OSError:
        return [host], False
    return [host], any(not is_public(ip) for ip in ips)


async def move_keys(store: Any, keyring: KeyringClient) -> int:
    """Every provider key in the database (names without ":") into the keyring, then out of the database. A failure leaves that key
    where it was (tried again at the next start). Returns how many moved; never logs a key."""
    from .providers import get_provider
    moved = 0
    for name in sorted(n for n in store.key_providers() if ":" not in n):
        try:
            prov = get_provider(name)
            base = prov.resolve_base_url()
            entry = await asyncio.to_thread(host_entry, base) if base else None
            if entry is None or prov.name != name:
                continue
            key = store.key(name)
            if not key:
                continue
            await keyring.put(name, key, entry[0], entry[1], hint=key[-4:])
            store.delete_key(name)
            moved += 1
        except Locked:
            break
        except (KeyError, KeyringError) as e:
            log.warning("keyring: the key of %s stays in the database (%s)", name, getattr(e, "code", type(e).__name__))
        except Exception:  # noqa: BLE001 - one key never stops the others
            log.warning("keyring: the key of %s stays in the database", name, exc_info=False)
    if moved:
        log.info("keyring: %d provider key(s) moved out of the database", moved)
    return moved


async def start_up(store: Any, keyring: KeyringClient) -> None:
    """Unlock the keyring and move the old keys; also run right after a Keyguard unlock. Never raises."""
    if not keyring.enabled:
        return
    try:
        for wait in (0, 3, 10, 30, 60):   # the keyring may still be starting
            await asyncio.sleep(wait)
            if keyring.ring_key() is None:
                return   # Keyguard is locked: the unlock runs this again
            if await keyring.unlock():
                await move_keys(store, keyring)
                from .cred_keyring import sync as sync_cred
                await sync_cred(store, keyring)
                return
    except Exception:  # noqa: BLE001
        log.warning("keyring: start-up failed", exc_info=False)
