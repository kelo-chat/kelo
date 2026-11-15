"""The "VPN (WireGuard)" and "Proxy" connectors: Kelo's web browsing (its browser, and the server's web_fetch / web_search page reads) goes out
through the owner's VPN or proxy. AI model calls, other connectors, mail, push and the phone connection stay direct.

How it is wired (docs/vpn.md for owners; compose.yml for the containers):
* the owner's WireGuard file or proxy is sealed in the vault (items `vpn-wireguard` / `vpn-proxy`, the usual key-connector card);
* the SERVER writes the files the other containers read: `route.json` (volume kelo-route, read by egress; no secrets) and, for the `vpn`
  sidecar only, `wg.conf` (wireproxy, WireGuard in user space) or `upstream.json` (the Proxy connector's address + user + password) in kelo-vpn;
* egress sends the browser's connections to the sidecar (SOCKS5 on vpn:1080, names resolved there); web_fetch uses socks5h://vpn:1080;
* FAIL CLOSED: while a VPN is on, browsing that cannot go through it is refused, never sent directly.

Switching (apply): a change is not made while the agent is browsing (a tab used by a tool in the last 2 minutes): it is queued and made as
soon as the browser is quiet (checked every 10 s, at most 30 minutes, then it is made anyway); `force` makes it at once. An open purchase
window is never interrupted, not even with `force`: the change waits for it. Every change raises `generation` in route.json, which makes
egress close all open tunnels so the browser reconnects through the new route (cookies and sign-ins stay).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from . import socks5
from .i18n import N_, t
from .keyguard import Locked
from .netguard import NetGuardError, is_public, resolve_public
from .store import Store

log = logging.getLogger("assistant.vpn")

WG_ITEM = "vpn-wireguard"
PROXY_ITEM = "vpn-proxy"
ITEMS = (WG_ITEM, PROXY_ITEM)
SIDECAR_HOST = "vpn"
SIDECAR_PORT = 1080
PROXY_URL = f"socks5h://{SIDECAR_HOST}:{SIDECAR_PORT}"
BUSY_SECONDS = 120.0           # the browser counts as in use when a tool used a tab in the last two minutes
WAIT_POLL = 10.0               # a queued change looks again this often ...
WAIT_MAX = 30 * 60.0           # ... for at most this long, then it is made anyway (unless a purchase window is open)
PROBE_HOST, PROBE_PORT, PROBE_SECONDS = "1.1.1.1", 443, 5.0
STATUS_TTL = 30.0              # the "connected" check is repeated after this long
EXIT_TTL = 600.0               # the exit address is looked up again after this long
EXIT_URL = "https://api.ipify.org"
DEFAULT_DNS = "1.1.1.1"        # used inside the tunnel when the pasted file has no DNS line (wireproxy needs one)
DEFAULT_ALLOWED = "0.0.0.0/0, ::/0"
NOTE_SIGNIN = N_("After a switch, sites that tie a session to the address may ask you to sign in again or show a check. Cookies and sign-ins are kept.")


class VpnError(Exception):
    """Shown to the owner as it is."""


# ----------------------------------------------------------------------------------------------------------------- WireGuard file
@dataclass(frozen=True)
class WgConfig:
    private_key: str
    addresses: tuple[str, ...]
    dns: tuple[str, ...]
    public_key: str
    preshared_key: str
    endpoint: str                   # host:port or [v6]:port
    allowed_ips: tuple[str, ...]

    def render(self) -> str:
        """wireproxy's file, built only from the fields read above (nothing else of what was pasted is carried over)."""
        lines = ["[Interface]", f"PrivateKey = {self.private_key}", f"Address = {', '.join(self.addresses)}", f"DNS = {', '.join(self.dns)}",
                 "", "[Peer]", f"PublicKey = {self.public_key}"]
        if self.preshared_key:
            lines.append(f"PresharedKey = {self.preshared_key}")
        lines += [f"Endpoint = {self.endpoint}", f"AllowedIPs = {', '.join(self.allowed_ips)}", "PersistentKeepalive = 25", "",
                  "[Socks5]", f"BindAddress = 0.0.0.0:{SIDECAR_PORT}", ""]
        return "\n".join(lines)


_KEY = r"(?<![A-Za-z])"
_IP = r"[0-9A-Fa-f:.]+"
_CIDR_LIST = rf"{_IP}/\d{{1,3}}(?:\s*,\s*{_IP}/\d{{1,3}})*"
_IP_LIST = rf"{_IP}(?:\s*,\s*[A-Za-z0-9.-]+)*"      # wg-quick lets DNS hold search domains too; only the addresses are kept
_RE = {
    "PrivateKey": re.compile(rf"{_KEY}PrivateKey\s*=\s*(\S+)", re.I),
    "PublicKey": re.compile(rf"{_KEY}PublicKey\s*=\s*(\S+)", re.I),
    "PresharedKey": re.compile(rf"{_KEY}PresharedKey\s*=\s*(\S+)", re.I),
    "Address": re.compile(rf"{_KEY}Address\s*=\s*({_CIDR_LIST})", re.I),
    "DNS": re.compile(rf"{_KEY}DNS\s*=\s*({_IP_LIST})", re.I),
    "Endpoint": re.compile(rf"{_KEY}Endpoint\s*=\s*(\S+)", re.I),
    "AllowedIPs": re.compile(rf"{_KEY}AllowedIPs\s*=\s*({_CIDR_LIST})", re.I),
}
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def _key(value: str, what: str) -> str:
    try:
        ok = len(value) == 44 and len(base64.b64decode(value, validate=True)) == 32
    except (binascii.Error, ValueError):
        ok = False
    if not ok:
        raise VpnError(t("The {what} in the WireGuard file is not valid. It must be a 44-character key ending in '='.", what=what))
    return value


def _cidrs(values: str, what: str, *, host_bits: bool) -> tuple[str, ...]:
    out = []
    for part in [p.strip() for p in values.split(",") if p.strip()]:
        try:
            net = ipaddress.ip_interface(part) if host_bits else ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise VpnError(t("The {what} in the WireGuard file is not valid.", what=what)) from None
        out.append(str(net))
    if not out:
        raise VpnError(t("The WireGuard file has no {what}.", what=what))
    return tuple(out)


def parse_wireguard(text: str) -> WgConfig:
    """The fields wireproxy needs, read tolerantly from what the owner pasted: by key name over the whole text, so a file whose
    line breaks turned into spaces still works. Each value is validated; nothing else from the text is used."""
    text = text or ""
    if len(text) > 20_000:
        raise VpnError(t("This is not a WireGuard file."))
    found = {k: (m.group(1) if (m := rx.search(text)) else "") for k, rx in _RE.items()}
    if not found["PrivateKey"] and not found["PublicKey"]:
        raise VpnError(t("This does not look like a WireGuard file. Paste the whole file your VPN provider gave you."))
    for k, label in (("PrivateKey", "PrivateKey"), ("Address", "Address"), ("PublicKey", "PublicKey"), ("Endpoint", "Endpoint")):
        if not found[k]:
            raise VpnError(t("The WireGuard file has no {what} line.", what=label))
    private, public = _key(found["PrivateKey"], "PrivateKey"), _key(found["PublicKey"], "PublicKey")
    preshared = _key(found["PresharedKey"], "PresharedKey") if found["PresharedKey"] else ""
    addresses = _cidrs(found["Address"], "Address", host_bits=True)
    dns = []
    for part in [p.strip() for p in found["DNS"].split(",") if p.strip()]:
        try:
            dns.append(str(ipaddress.ip_address(part)))
        except ValueError:
            continue                                  # a search domain: not needed
    allowed = _cidrs(found["AllowedIPs"] or DEFAULT_ALLOWED, "AllowedIPs", host_bits=False)
    host, port = _endpoint(found["Endpoint"])
    shown = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return WgConfig(private, addresses, tuple(dns) or (DEFAULT_DNS,), public, preshared, shown, allowed)


def _endpoint(value: str) -> tuple[str, int]:
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\]:(\d{1,5})|([^\s\[\]:]+):(\d{1,5})", value.strip())
    if not m:
        raise VpnError(t("The Endpoint in the WireGuard file must look like vpn.example.com:51820."))
    host, port = (m.group(1), int(m.group(2))) if m.group(1) else (m.group(3), int(m.group(4)))
    if not 0 < port < 65536:
        raise VpnError(t("The Endpoint in the WireGuard file must look like vpn.example.com:51820."))
    try:
        if not is_public(str(ipaddress.ip_address(host))):
            raise VpnError(t("The Endpoint is a private or local address. Kelo only uses a VPN that is outside your home network."))
    except ValueError:
        if not _HOSTNAME.match(host) or host.lower().endswith((".local", ".lan", ".internal", ".localhost", ".home", ".corp")):
            raise VpnError(t("The Endpoint in the WireGuard file must look like vpn.example.com:51820.")) from None
    return host.lower(), port


# ---------------------------------------------------------------------------------------------------------------------- proxy
@dataclass(frozen=True)
class ProxyConfig:
    kind: str                       # socks5 | http | https
    host: str
    port: int
    user: str
    password: str

    def upstream(self) -> dict[str, Any]:
        return {"kind": self.kind, "host": self.host, "port": self.port, **({"user": self.user, "password": self.password} if self.user else {})}


async def parse_proxy(url: str, user: str = "", password: str = "", *, resolver: Callable[..., list] | None = None) -> ProxyConfig:
    raw = (url or "").strip()
    if not raw or any(c.isspace() for c in raw) or not raw.isprintable():
        raise VpnError(t("The proxy address must be one line like socks5://host:1080."))
    if "://" not in raw:
        raise VpnError(t("The proxy address must start with socks5:// , http:// or https:// (example: socks5://host:1080)."))
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    kind = {"socks5": "socks5", "socks5h": "socks5", "http": "http", "https": "https"}.get(scheme)
    if kind is None:
        raise VpnError(t("The proxy address must start with socks5:// , http:// or https:// (example: socks5://host:1080)."))
    if parts.path.strip("/") or parts.query or parts.fragment:
        raise VpnError(t("The proxy address must be only the host and port, like socks5://host:1080."))
    try:
        port = parts.port or {"socks5": 1080, "http": 8080, "https": 443}[kind]
    except ValueError:
        raise VpnError(t("The port in the proxy address is not valid.")) from None
    host = (parts.hostname or "").lower()
    if not host:
        raise VpnError(t("The proxy address has no host."))
    user = (user or "").strip() or unquote(parts.username or "")
    password = (password or "") if (user and password) else unquote(parts.password or "") if user else ""
    if len(user) > 200 or len(password) > 200 or ":" in user or not user.isprintable() or not password.isprintable():
        raise VpnError(t("Please check the proxy user name and password."))
    try:
        if not is_public(str(ipaddress.ip_address(host))):
            raise VpnError(t("The proxy is a private or local address. Kelo only uses a proxy that is outside your home network."))
    except ValueError:
        try:
            await resolve_public(host, resolver)
        except NetGuardError as e:
            raise VpnError(t("Could not use this proxy address: {error}", error=e)) from None
    return ProxyConfig(kind, host, port, user, password)


# ------------------------------------------------------------------------------------------------------------------ files and route
def _write_private(path: Path, text: str, mode: int) -> None:
    """Write atomically (a reader never sees half a file) with the given mode from the first byte."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)


@dataclass
class Change:
    """What the route should be: direct, a WireGuard file, or a proxy. `sig` tells two changes apart without keeping the secret."""
    kind: str                                   # direct | wireguard | proxy
    sig: str = ""
    wg: WgConfig | None = None
    proxy: ProxyConfig | None = None
    item: str = ""
    queued_at: float = field(default=0.0, compare=False)


def signature(kind: str, secret: str) -> str:
    return hashlib.sha256(f"{kind}\0{secret}".encode()).hexdigest()[:32]


class VpnRoute:
    """Owns the route files, the pending change, and what the connector shows. One per server."""

    def __init__(self, store: Store, route_dir: Path | str | None, vpn_dir: Path | str | None, *,
                 browsing: Callable[[], bool] = lambda: False, paying: Callable[[], bool] = lambda: False,
                 probe: Callable[[], Awaitable[bool]] | None = None, exit_lookup: Callable[[], Awaitable[str]] | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 resolver: Callable[..., list] | None = None, wait_poll: float = WAIT_POLL, wait_max: float = WAIT_MAX):
        self.store = store
        self._quiet: set[str] = set()      # items this class changes itself (their watcher call is not a new request)
        self.route_dir = Path(route_dir) if route_dir else None
        self.vpn_dir = Path(vpn_dir) if vpn_dir else None
        self.browsing, self.paying = browsing, paying
        self._probe, self._exit_lookup = probe or self._default_probe, exit_lookup or self._default_exit
        self.clock, self.sleep, self.resolver = clock, sleep, resolver
        self.wait_poll, self.wait_max = wait_poll, wait_max
        self.pending: Change | None = None
        self._lock = asyncio.Lock()
        self._waiter: asyncio.Task[None] | None = None
        self._status: dict[str, Any] = {"connected": False, "at": 0.0}
        self._exit: tuple[str, float] = ("", 0.0)
        self.on_change: Callable[[], None] | None = None           # e.g. the connector table is rebuilt
        store.credential_watchers.append(self._credential_changed)
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------ state
    @property
    def available(self) -> bool:
        return bool(self.route_dir and self.vpn_dir and self.route_dir.is_dir() and self.vpn_dir.is_dir())

    def state(self) -> dict[str, Any]:
        try:
            got = json.loads(self.store.meta("vpn:state") or "{}")
        except ValueError:
            got = {}
        return {"kind": "direct", "sig": "", "generation": 0, **(got if isinstance(got, dict) else {})}

    def _save_state(self, **changes: Any) -> dict[str, Any]:
        st = {**self.state(), **changes}
        self.store.set_meta("vpn:state", json.dumps(st))
        return st

    @property
    def kind(self) -> str:
        """direct | wireguard | proxy: the route that is in effect now (a queued change is not)."""
        return str(self.state()["kind"])

    def active(self) -> bool:
        return self.kind != "direct"

    def proxy_url(self) -> str | None:
        """For the server's own web reads (web_fetch / web_search): the sidecar as a SOCKS5 proxy with remote DNS, or None for direct."""
        return PROXY_URL if self.active() else None

    # ------------------------------------------------------------------ writing the files
    def _write_route(self, mode: str, generation: int) -> None:
        assert self.route_dir is not None
        body = {"mode": "direct"} if mode == "direct" else {"mode": "socks5", "host": SIDECAR_HOST, "port": SIDECAR_PORT}
        _write_private(self.route_dir / "route.json", json.dumps({**body, "generation": generation}), 0o644)   # no secrets in it

    def _write(self, change: Change) -> None:
        assert self.route_dir is not None and self.vpn_dir is not None
        gen = int(self.state()["generation"]) + 1
        wg, up = self.vpn_dir / "wg.conf", self.vpn_dir / "upstream.json"
        if change.kind == "wireguard" and change.wg:
            _write_private(wg, change.wg.render(), 0o600)
            _remove(up)
            self._write_route("socks5", gen)
        elif change.kind == "proxy" and change.proxy:
            _write_private(up, json.dumps(change.proxy.upstream()), 0o600)
            _remove(wg)
            self._write_route("socks5", gen)
        else:                                   # back to direct: the route first, then the files go
            self._write_route("direct", gen)
            _remove(wg)
            _remove(up)
        self._save_state(kind=change.kind, sig=change.sig, generation=gen)
        self._exit = ("", 0.0)
        self._status = {"connected": False, "at": 0.0}
        what = {"direct": "browsing goes out directly again", "wireguard": "browsing goes through the WireGuard VPN",
                "proxy": "browsing goes through the proxy"}[change.kind]
        self.store.add_vault_log(change.item or "vpn", "route", what)
        log.info("vpn: route changed: %s (generation %d)", what, gen)
        if self.on_change:
            self.on_change()

    def _in_place(self, change: Change) -> bool:
        """The files on disk are what `change` needs (a lost or edited volume is made again at the next start)."""
        assert self.route_dir is not None and self.vpn_dir is not None
        try:
            mode = json.loads((self.route_dir / "route.json").read_text()).get("mode")
        except (OSError, ValueError, AttributeError):
            mode = None
        wg, up = self.vpn_dir / "wg.conf", self.vpn_dir / "upstream.json"
        try:
            if change.kind == "wireguard" and change.wg:
                return mode == "socks5" and wg.read_text() == change.wg.render() and not up.exists()
            if change.kind == "proxy" and change.proxy:
                return mode == "socks5" and json.loads(up.read_text()) == change.proxy.upstream() and not wg.exists()
        except (OSError, ValueError):
            return False
        return mode in (None, "direct") and not wg.exists() and not up.exists()

    # ------------------------------------------------------------------ applying a change (queued while the browser is in use)
    async def apply(self, change: Change, *, force: bool = False) -> dict[str, Any]:
        """Makes `change` now, or queues it. {"state": "applied" | "queued", "message": ...}"""
        if not self.available:
            raise VpnError(t("This installation has no VPN support (the vpn container is not part of it). Update the compose file."))
        async with self._lock:
            if change.kind == self.kind and change.sig == self.state()["sig"] and self._in_place(change):
                self.pending = None
                return {"state": "applied", "message": ""}
            if self.paying() or (not force and self.browsing()):
                change.queued_at = self.clock()
                self.pending = change
                self._start_waiter()
                why = (t("A purchase is open in the browser; the change is made when it is finished.") if self.paying()
                       else t("The change applies when the current browsing finishes. To switch now, ask again with force."))
                self.store.add_vault_log(change.item or "vpn", "route", "change waits for the browser to be quiet")
                return {"state": "queued", "message": why}
            self.pending = None
            self._write(change)
            return {"state": "applied", "message": t(NOTE_SIGNIN)}

    def _start_waiter(self) -> None:
        if self._waiter is None or self._waiter.done():
            self._waiter = asyncio.get_running_loop().create_task(self._wait_for_quiet())

    async def _wait_for_quiet(self) -> None:
        while True:
            ch = self.pending
            if ch is None:
                return
            waited = self.clock() - ch.queued_at
            if not self.paying() and (not self.browsing() or waited >= self.wait_max):
                async with self._lock:
                    if self.pending is ch and not self.paying():
                        self.pending = None
                        self._write(ch)
                        return
            await self.sleep(self.wait_poll)

    async def settle(self) -> None:
        """Waits for a queued change to be made (tests, shutdown)."""
        if self._waiter is not None:
            await asyncio.shield(self._waiter)

    async def close(self) -> None:
        for task in [self._waiter, *self._tasks]:
            if task is not None and not task.done():
                task.cancel()

    # ------------------------------------------------------------------ connecting and disconnecting
    def _remember(self, item: str, label: str, secret: str) -> None:
        """Seals the owner's file or proxy in the vault (the other connector's item goes: at most one is active)."""
        other = PROXY_ITEM if item == WG_ITEM else WG_ITEM
        old = self.store.credential(item)
        if old and old["kind"] != "api":
            raise VpnError(t("The vault has another item named '{item}'. Delete that item and try again.", item=item))
        self._quiet.add(item)
        self._quiet.add(other)
        try:
            if self.store.credential(other):
                self.store.delete_credential(other)
            self.store.delete_credential(item)
            self.store.save_credential(item, label, [], {"type": "none"}, "", kind="api")
            self.store.set_credential_secret(item, secret)
        finally:
            self._quiet.discard(item)
            self._quiet.discard(other)
        self.store.add_vault_log(item, "saved", label)

    async def connect_wireguard(self, text: str, *, force: bool = False, save: bool = True) -> dict[str, Any]:
        cfg = parse_wireguard(text)
        secret = text.strip()
        change = Change("wireguard", signature("wireguard", cfg.render()), wg=cfg, item=WG_ITEM)
        if save:
            self._remember(WG_ITEM, t("VPN (WireGuard)") + f" · {cfg.endpoint}", secret)
        return {**await self.apply(change, force=force), "account": cfg.endpoint}

    async def connect_proxy(self, url: str, user: str = "", password: str = "", *, force: bool = False, save: bool = True) -> dict[str, Any]:
        cfg = await parse_proxy(url, user, password, resolver=self.resolver)
        change = Change("proxy", signature("proxy", json.dumps(cfg.upstream(), sort_keys=True)), proxy=cfg, item=PROXY_ITEM)
        shown = f"{cfg.kind}://{cfg.host}:{cfg.port}"
        if save:
            self._remember(PROXY_ITEM, t("Proxy") + f" · {shown}",
                           json.dumps({"kind": cfg.kind, "host": cfg.host, "port": cfg.port, "user": cfg.user, "password": cfg.password}))
        return {**await self.apply(change, force=force), "account": shown}

    async def disconnect(self, *, force: bool = False) -> dict[str, Any]:
        """Browsing goes out directly again; the vault items go too."""
        self._quiet.update(ITEMS)
        try:
            for item in ITEMS:
                self.store.delete_credential(item)
        finally:
            self._quiet.difference_update(ITEMS)
        return await self.apply(Change("direct", item="vpn"), force=force)

    # ------------------------------------------------------------------ following the vault
    def _credential_changed(self, name: str, removed: bool) -> None:
        """The vault's VPN item was saved, replaced or deleted (Store.credential_watchers): the route follows."""
        if name in ITEMS and name not in self._quiet:
            self._spawn(self.reconcile())

    def _spawn(self, coro: Awaitable[Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:                      # a thread without a loop (not expected): the next start catches up
            if asyncio.iscoroutine(coro):
                coro.close()
            return
        task = loop.create_task(coro)             # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def desired(self) -> Change | None:
        """The route the vault asks for, or None when the vault cannot be read yet (Keyguard is locked)."""
        names = {c["name"] for c in self.store.credentials() if c["has_secret"]}
        for item in ITEMS:
            if item not in names:
                continue
            try:
                cred = self.store.credential(item)
            except Locked:
                return None
            secret = (cred or {}).get("secret")
            if not secret:
                return None
            try:
                if item == WG_ITEM:
                    cfg = parse_wireguard(secret)
                    return Change("wireguard", signature("wireguard", cfg.render()), wg=cfg, item=item)
                d = json.loads(secret)
                pc = ProxyConfig(d["kind"], d["host"], int(d["port"]), d.get("user", ""), d.get("password", ""))
                return Change("proxy", signature("proxy", json.dumps(pc.upstream(), sort_keys=True)), proxy=pc, item=item)
            except (VpnError, ValueError, KeyError, TypeError):
                log.warning("vpn: the stored %s cannot be read; browsing stays refused until it is connected again", item)
                return None
        return Change("direct", item="vpn")

    def has_item(self) -> bool:
        return any(c["name"] in ITEMS and c["has_secret"] for c in self.store.credentials())

    async def reconcile(self, *, force: bool = False) -> dict[str, Any] | None:
        """Makes the files and the route match the vault. Used after a change in the vault and at start (after the unlock)."""
        if not self.available:
            return None
        want = self.desired()
        if want is None:
            self.safe_state()
            return None
        try:
            return await self.apply(want, force=force)
        except VpnError as e:
            log.warning("vpn: %s", e)
            return None

    def safe_state(self) -> None:
        """The vault has a VPN item that cannot be opened (Keyguard is locked after a restart, or it is damaged): the route must say
        'through the VPN' already, so browsing is refused until the files can be made, and is never sent directly."""
        if not self.available or not self.has_item():
            return
        try:
            cur = json.loads((self.route_dir / "route.json").read_text())  # type: ignore[union-attr]
        except (OSError, ValueError):
            cur = {}
        if cur.get("mode") == "socks5":
            return
        gen = int(self.state()["generation"]) + 1
        self._write_route("socks5", gen)
        self._save_state(generation=gen)
        log.info("vpn: locked start: browsing is refused until the VPN files can be rebuilt")

    async def start(self, keyguard: Any = None) -> None:
        """At server start: the safe state at once, the real files once the vault can be read."""
        if not self.available:
            return
        try:
            self.safe_state()
            if keyguard is not None and keyguard.locked:
                keyguard.after_unlock(self.reconcile)
            else:
                await self.reconcile(force=True)     # nothing is browsing at start: a restart cut every turn off
        except Exception as e:  # noqa: BLE001 - the server starts either way; browsing stays as the files say
            log.warning("vpn: start failed (%s)", type(e).__name__)

    # ------------------------------------------------------------------ what the connector shows
    async def _default_probe(self) -> bool:
        try:
            r, w = await socks5.connect(SIDECAR_HOST, SIDECAR_PORT, PROBE_HOST, PROBE_PORT, timeout=PROBE_SECONDS)
        except (socks5.Socks5Error, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            return False
        w.close()
        return True

    async def _default_exit(self) -> str:
        import httpx
        async with httpx.AsyncClient(proxy=PROXY_URL, timeout=PROBE_SECONDS * 2, trust_env=False) as client:
            r = await client.get(EXIT_URL)
        return r.text.strip()

    async def wait_connected(self, seconds: float = 12.0) -> bool:
        """After a switch: True as soon as the VPN answers (the sidecar needs a few seconds to start), False if it does not within `seconds`."""
        for _ in range(int(seconds) + 1):
            if (await self.status(fresh=True))["connected"]:
                return True
            await self.sleep(1.0)
        return False

    async def status(self, *, fresh: bool = False) -> dict[str, Any]:
        """{"mode": direct|wireguard|proxy, "connected": bool, "exit_ip"?: str, "pending"?: str}. `connected`: a SOCKS5 connection through the
        sidecar to 1.1.1.1:443 succeeds within 5 seconds (checked at most every 30 s); the exit address is looked up once per 10 minutes."""
        mode = self.kind
        out: dict[str, Any] = {"mode": mode, "connected": False}
        if self.pending is not None:
            out["pending"] = self.pending.kind
        if mode == "direct":
            return out
        now = self.clock()
        if fresh or now - self._status["at"] > STATUS_TTL:
            ok = False
            try:
                ok = bool(await asyncio.wait_for(self._probe(), PROBE_SECONDS + 1))
            except (asyncio.TimeoutError, OSError):
                ok = False
            self._status = {"connected": ok, "at": now}
        out["connected"] = bool(self._status["connected"])
        if out["connected"]:
            ip, at = self._exit
            if not ip or now - at > EXIT_TTL:
                try:
                    ip = str(ipaddress.ip_address((await self._exit_lookup()).strip()))
                    self._exit = (ip, now)
                except Exception:   # noqa: BLE001 - the exit address is a nicety, never an error
                    ip = ""
            if ip:
                out["exit_ip"] = ip
        return out

    def account(self) -> str:
        """For the connector's row (sync): 'exit <ip>' once known, else ''."""
        ip, _ = self._exit
        return t("exit {ip}", ip=ip) if ip and self.active() and self._status.get("connected") else ""

    async def down_note(self) -> str:
        """For a browser tool that failed: the text that says the VPN is the reason, or '' when the route is direct or the VPN works."""
        if not self.active() or (await self.status(fresh=True))["connected"]:
            return ""
        return t("The VPN is not connected right now. Browsing is paused (it is never sent around the VPN). Tell the user and try again later.")
