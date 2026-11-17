"""Reach a copy of the assistant from a phone anywhere, and sign phones in.

Ways to the outside, your choice per copy (docs/connect.md compares them):
- Kelo address: https://<name>.kelo.chat, offered for now, from the operator's broker, run by a tunnel container next to the server
  (traffic passes through Cloudflare, TLS ends there).
- Tailscale Funnel: public https on <machine>.<tailnet>.ts.net (ports 443/8443/10000 only), no domain, TLS ends on this computer.
- Own domain straight to this computer (bundled Caddy on 80/443) or through a reverse proxy you already run.
The two original ones:
- Tailscale: private, no domain, nothing public. Your phone needs the Tailscale app on the same account. Each copy gets its
  own HTTPS port on this computer's Tailscale name, so there is no cap on how many copies one computer can serve.
- Cloudflare: your own domain. Each copy gets its own tunnel and its own host name (name.your-domain), run by a small
  container next to the server, so there is no cap either.
Phones are then added with a QR code (`phone_add`): any number per copy, each with a token of its own.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import segno

from .cli import read_env, remove_env, write_env
from .instances import DOMAIN, Instance, InstanceError, Instances
from .kelo_address import KELO_BROKER, ConnectError, kelo_broker, kelo_dir  # noqa: F401 - re-exported: the stdlib-only half, also run by the updater
from .kelo_address import broker_error as _broker_error
from .kelo_address import kelo_refresh as _kelo_refresh

TAILSCALE_DOWNLOAD = "https://tailscale.com/download"
CLOUDFLARED_DOWNLOAD = "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
TAILSCALE_TIMEOUT = 60
CF_TIMEOUT = 120


def _text(proc: Any) -> str:
    return ((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")).strip()


def _run(m: Instances, cmd: list[str], *, timeout: float) -> Any:
    try:
        return m.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ConnectError(f"`{' '.join(cmd[:3])}` did not finish in {int(timeout)}s; it may be waiting for you to approve something "
                           "in a browser or in the admin console. Do that, then run this again.") from None


def _save(inst: Instance, **values: str) -> None:
    write_env(inst.env, values)


def _forget(inst: Instance, *keys: str) -> None:
    remove_env(inst.env, keys)


# ---------------------------------------------------------------------------------------------------------- Tailscale
def tailscale_connect(m: Instances, inst: Instance, *, https_port: int | None = None, force: bool = False,
                      which: Callable[[str], str | None] | None = None) -> str:
    dns = _tailscale_name(m, which)
    port = https_port or (443 if inst.is_main else inst.server_port)
    target = f"http://127.0.0.1:{inst.server_port}"
    if not force:
        cur = _run(m, ["tailscale", "serve", "status", "--json"], timeout=TAILSCALE_TIMEOUT)
        try:
            web = (json.loads(cur.stdout or "{}") or {}).get("Web") or {}
        except ValueError:
            web = {}
        for hostport, cfg in web.items():
            if str(hostport).rsplit(":", 1)[-1] == str(port):
                proxy = ((cfg.get("Handlers") or {}).get("/") or {}).get("Proxy")
                if proxy and proxy.rstrip("/") != target:
                    raise ConnectError(f"HTTPS port {port} on this Tailscale name already serves something else ({proxy}). "
                                       f"Pick another with --https-port, or add --force to replace it.")
    r = _run(m, ["tailscale", "serve", "--bg", f"--https={port}", target], timeout=TAILSCALE_TIMEOUT)
    if r.returncode != 0:
        raise ConnectError(f"`tailscale serve` failed: {_text(r) or 'no output'}. If it mentions HTTPS certificates, enable them "
                           "in the Tailscale admin console (DNS page) and run this again.")
    url = f"https://{dns}" + ("" if port == 443 else f":{port}")
    _save(inst, CONNECT_MODE="tailscale", CONNECT_URL=url, CONNECT_HOST=url)
    return url


def tailscale_off(m: Instances, inst: Instance) -> None:
    port = urlsplit(inst.connect_url).port or 443
    r = _run(m, ["tailscale", "serve", f"--https={port}", "off"], timeout=TAILSCALE_TIMEOUT)
    if r.returncode != 0:
        raise ConnectError(f"`tailscale serve ... off` failed: {_text(r) or 'no output'}")
    _forget(inst, "CONNECT_MODE", "CONNECT_URL", "CONNECT_HOST")


# ---------------------------------------------------------------------------------------------------------- Cloudflare
def cf_dir(m: Instances, inst: Instance) -> Path:
    return m.root / "connect" / "cloudflared" if inst.is_main else m.dir / inst.name / "cloudflared"


def cloudflare_connect(m: Instances, inst: Instance, domain: str, *, hostname: str | None = None,
                       which: Callable[[str], str | None] | None = None, cf_home: Path | None = None) -> str:
    if not DOMAIN.match(domain or ""):
        raise ConnectError(f"{domain!r} is not a domain name (example: example.com)")
    host = (hostname or f"{'assistant' if inst.is_main else inst.name}.{domain}").lower()
    if not DOMAIN.match(host) or not (host == domain or host.endswith("." + domain)):
        raise ConnectError(f"{host!r} must be a host name under {domain}")
    if not (which or shutil.which)("cloudflared"):
        raise ConnectError(f"cloudflared is not installed on this computer ({CLOUDFLARED_DOWNLOAD}). Install it, then run "
                           f"`cloudflared tunnel login` once and choose {domain}.")
    home = cf_home or Path.home() / ".cloudflared"
    if not (home / "cert.pem").is_file():
        raise ConnectError(f"cloudflared is not signed in to your Cloudflare account. Run `cloudflared tunnel login` once and choose {domain}.")
    tunnel = f"assistant-{inst.name}"

    def find() -> dict[str, Any] | None:
        r = _run(m, ["cloudflared", "tunnel", "list", "--name", tunnel, "--output", "json"], timeout=CF_TIMEOUT)
        try:
            found = json.loads(r.stdout or "[]") or []
        except ValueError:
            found = []
        # cloudflared writes a zero date ("0001-01-01T00:00:00Z") for tunnels that were never deleted
        return next((t for t in found if t.get("name") == tunnel and not str(t.get("deleted_at") or "").replace("0001-01-01T00:00:00Z", "")), None)

    info = find()
    if not info:
        made = _run(m, ["cloudflared", "tunnel", "create", tunnel], timeout=CF_TIMEOUT)
        if made.returncode != 0:
            raise ConnectError(f"could not create the tunnel: {_text(made) or 'no output'}")
        info = find()
    if not info or not info.get("id"):
        raise ConnectError("the tunnel was created but could not be read back; run `cloudflared tunnel list` to check")
    creds = home / f"{info['id']}.json"
    if not creds.is_file():
        raise ConnectError(f"the tunnel exists but its credentials file {creds} is not on this computer (it was made on another one). "
                           f"Delete the tunnel (`cloudflared tunnel delete {tunnel}`) and run this again.")
    dns = _run(m, ["cloudflared", "tunnel", "route", "dns", tunnel, host], timeout=CF_TIMEOUT)
    if dns.returncode != 0 and "already exist" not in _text(dns).lower():
        raise ConnectError(f"could not point {host} at the tunnel: {_text(dns) or 'no output'}")

    d = cf_dir(m, inst)
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    (d / "credentials.json").write_bytes(creds.read_bytes())
    (d / "credentials.json").chmod(0o600)
    (d / "config.yml").write_text(f"tunnel: {info['id']}\ncredentials-file: /etc/cloudflared/credentials.json\n"
                                  f"ingress:\n  - hostname: {host}\n    service: http://server:8080\n  - service: http_status:404\n")
    profiles = [p for p in read_env(inst.env, "COMPOSE_PROFILES").split(",") if p]
    if "cloudflare" not in profiles:
        profiles.append("cloudflare")
    url = f"https://{host}"
    updates = {"CONNECT_MODE": "cloudflare", "CONNECT_URL": url, "CONNECT_HOST": host, "ASSISTANT_PUBLIC_URL": url, "COMPOSE_PROFILES": ",".join(profiles),
               "CLOUDFLARED_DIR": "./" + str(d.relative_to(m.root))}   # "./" so Docker reads it as a folder, not a volume name
    if hasattr(os, "getuid"):   # Docker-only; moves into DockerHost in phase 1
        updates["CLOUDFLARED_USER"] = f"{os.getuid()}:{os.getgid()}"   # so the container can read the credentials on Linux too
    _save(inst, **updates)
    return url


def cloudflare_off(m: Instances, inst: Instance) -> None:
    profiles = [p for p in read_env(inst.env, "COMPOSE_PROFILES").split(",") if p and p != "cloudflare"]
    m.run(m.compose(inst, "--profile", "cloudflare", "rm", "-sf", "cloudflared"), cwd=m.root, capture_output=True)
    shutil.rmtree(cf_dir(m, inst), ignore_errors=True)
    _forget(inst, "CONNECT_MODE", "CONNECT_URL", "CONNECT_HOST", "CLOUDFLARED_DIR", "CLOUDFLARED_USER", "ASSISTANT_PUBLIC_URL")
    _save(inst, COMPOSE_PROFILES=",".join(profiles))


# ---------------------------------------------------------------------------------------------------------- phones
def _server_call(inst: Instance, call: Callable[..., Any], path: str, **kw: Any) -> Any:
    """One request to the running server with the copy's access key (from its settings file: only someone at this computer has it)."""
    token = read_env(inst.env, "ASSISTANT_TOKEN")
    try:
        r = call(f"{inst.local_url}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=15, **kw)
    except httpx.HTTPError:
        raise ConnectError(f"the assistant is not answering at {inst.local_url}. Start it first "
                           f"({'docker compose up -d' if inst.is_main else f'assistant instance start {inst.name}'}).") from None
    if r.status_code == 401:
        raise ConnectError("the access token in the settings file is not accepted by the running server (was it changed? restart the server)")
    if r.status_code == 404 and path.endswith("/owner"):
        raise ConnectError("no such device")
    if r.status_code != 200:
        raise ConnectError(f"the server refused ({r.status_code}): {r.text[:200]}")
    return r.json()


def phone_add(m: Instances, inst: Instance, *, address: str | None = None, post: Callable[..., Any] = httpx.post) -> tuple[str, str]:
    """Ask the running server for a one-time sign-in link. Returns (link, QR drawn in text). Every device that signs in has the same
    rights (there are no owner devices any more), so the request carries nothing about rights."""
    origin = (address or inst.address).rstrip("/")
    out = _server_call(inst, post, "/api/devices/pair", json={"origin": origin})
    link = out["link"]
    return link, qr_text(link)


def qr_matrix(link: str) -> list[list[bool]]:
    """The QR code's modules (True = dark): the smallest regular QR that holds the link (lowest error correction, so a phone
    link is version 3-4), with a quiet zone of 2 modules."""
    q = segno.make_qr(link, error="l", boost_error=False)
    return [[bool(v) for v in row] for row in q.matrix_iter(scale=1, border=2)]


def qr_lines(link: str, *, color: bool = False) -> list[str]:
    """The QR code in half blocks: two module rows per text line, one character per module (about 17-19 lines for a phone link).
    color=True paints black on white with ANSI codes (right on dark and light terminals alike). Without colour, light modules
    are the drawn blocks, so it reads right on the usual dark terminal."""
    rows = qr_matrix(link)
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))
    out = []
    for top, bottom in zip(rows[0::2], rows[1::2], strict=True):
        if color:      # upper half: foreground colour; lower half: background colour
            line = "".join(f"\x1b[{30 if a else 97};{40 if b else 107}m▀" for a, b in zip(top, bottom, strict=True)) + "\x1b[0m"
        else:
            line = "".join(" ▄▀█"[(not a) * 2 + (not b)] for a, b in zip(top, bottom, strict=True))
        out.append(line)
    return out


def qr_text(link: str, *, color: bool = False) -> str:
    return "\n".join(qr_lines(link, color=color)) + "\n"


def devices(m: Instances, inst: Instance, *, get: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """The copy's signed-in devices: [{"id", "name", "created_at", "last_seen"}]."""
    return list(_server_call(inst, get or _get, "/api/devices").get("devices") or [])


def e2e_info(m: Instances, inst: Instance, *, get: Callable[..., Any] | None = None) -> dict[str, Any]:
    """The copy's KE2E key as the phones see it: {"v", "kid", "pk", "fingerprint"} (GET /api/e2e/info)."""
    return dict(_server_call(inst, get or _get, "/api/e2e/info"))


def e2e_rotate(m: Instances, inst: Instance, *, post: Callable[..., Any] | None = None) -> dict[str, Any]:
    """A new KE2E key pair for the copy (master token): every phone must pair again."""
    return dict(_server_call(inst, post or _post, "/api/e2e/rotate"))


def keyguard_status(m: Instances, inst: Instance, *, get: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Keyguard's state on the copy (GET /api/keyguard): mode, locked, phones."""
    return dict(_server_call(inst, get or _get, "/api/keyguard"))


def keyguard_prepare(m: Instances, inst: Instance, *, post: Callable[..., Any] | None = None) -> dict[str, Any]:
    """The running copy writes its env key into master.key (so ASSISTANT_SECRET_KEY can leave .env without losing the secrets)."""
    return dict(_server_call(inst, post or _post, "/api/keyguard/prepare"))


def keyguard_recover(m: Instances, inst: Instance, code: str, *, post: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Unlock the copy with the recovery code (the master token, on this computer)."""
    return dict(_server_call(inst, post or _post, "/api/keyguard/recover", json={"code": code}))


def local_only(address: str) -> bool:
    host = urlsplit(address).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1") or bool(re.match(r"^(10|192\.168)\.", host))


# ---------------------------------------------------------------------------------------------------------- every way, one place
def _get(*a: Any, **k: Any) -> Any:      # looked up when called, so tests can stand in a fake
    return httpx.get(*a, **k)


def _post(*a: Any, **k: Any) -> Any:
    return httpx.post(*a, **k)


def _put(*a: Any, **k: Any) -> Any:
    return httpx.put(*a, **k)


MODES = {
    "kelo": "Kelo address (offered for now; through Cloudflare)",
    "tailscale": "Tailscale, private",
    "funnel": "Tailscale Funnel, public",
    "cloudflare": "own domain, your Cloudflare account",
    "direct": "own domain, straight to this computer (Caddy)",
    "proxy": "own domain, through your own reverse proxy",
}
CONNECT_KEYS = ("CONNECT_MODE", "CONNECT_URL", "CONNECT_HOST")


def _profiles(inst: Instance, add: str | None = None, drop: str | None = None) -> str:
    profiles = [p for p in read_env(inst.env, "COMPOSE_PROFILES").split(",") if p and p != drop]
    if add and add not in profiles:
        profiles.append(add)
    return ",".join(profiles)


def _tailscale_name(m: Instances, which: Callable[[str], str | None] | None) -> str:
    if not (which or shutil.which)("tailscale"):
        raise ConnectError(f"Tailscale is not installed on this computer. Install it ({TAILSCALE_DOWNLOAD}), sign in, then run this again. "
                           "Your phone needs the Tailscale app signed in to the same account (not for Funnel).")
    st = _run(m, ["tailscale", "status", "--json"], timeout=TAILSCALE_TIMEOUT)
    try:
        status = json.loads(st.stdout or "{}")
    except ValueError:
        status = {}
    if st.returncode != 0 or status.get("BackendState") != "Running":
        raise ConnectError("Tailscale is installed but not signed in or not running. Run `tailscale up`, then try again.")
    dns = str((status.get("Self") or {}).get("DNSName", "")).rstrip(".")
    if not dns:
        raise ConnectError("this computer has no Tailscale name (MagicDNS). Turn on MagicDNS and HTTPS certificates in the Tailscale admin console (DNS page).")
    return dns


# ---------------------------------------------------------------------------------------------------------- Tailscale Funnel
FUNNEL_PORTS = (443, 8443, 10000)       # the only ports Funnel listens on (tailscale.com/kb/1223/funnel)


def funnel_connect(m: Instances, inst: Instance, *, https_port: int | None = None,
                   which: Callable[[str], str | None] | None = None) -> str:
    """Public https on <machine>.<tailnet>.ts.net[:port], no domain. TLS ends on this computer: Tailscale's relays cannot read it."""
    dns = _tailscale_name(m, which)
    if https_port is not None and https_port not in FUNNEL_PORTS:
        raise ConnectError("Funnel can only use HTTPS port 443, 8443 or 10000")
    target = f"http://127.0.0.1:{inst.server_port}"
    cur = _run(m, ["tailscale", "funnel", "status", "--json"], timeout=TAILSCALE_TIMEOUT)
    try:
        cfg = json.loads(cur.stdout or "{}") or {}
    except ValueError:
        cfg = {}
    used: dict[int, str] = {}
    for hostport, web in (cfg.get("Web") or {}).items():
        port = int(str(hostport).rsplit(":", 1)[-1]) if str(hostport).rsplit(":", 1)[-1].isdigit() else 443
        proxy = ((web.get("Handlers") or {}).get("/") or {}).get("Proxy") or "something else"
        used[port] = str(proxy).rstrip("/")
    for port in (cfg.get("TCP") or {}):
        if str(port).isdigit() and int(port) not in used:
            used[int(port)] = "a TCP forward"
    free = [p for p in FUNNEL_PORTS if used.get(p) in (None, target)]
    if https_port is not None:
        if https_port not in free:
            raise ConnectError(f"HTTPS port {https_port} on this Tailscale name already serves {used[https_port]}. "
                               f"Free Funnel ports: {', '.join(map(str, free)) or 'none'}.")
        port = https_port
    elif not free:
        taken = ", ".join(f"{p} -> {used[p]}" for p in FUNNEL_PORTS)
        raise ConnectError(f"Funnel can only use ports 443, 8443 and 10000, and all three already serve something on this computer ({taken}).\n"
                           "Use one of these instead:\n    assistant connect tailscale\n    assistant connect domain --host <address> --proxy")
    else:
        port = next((p for p in free if used.get(p) == target), free[0])
    r = _run(m, ["tailscale", "funnel", "--bg", f"--https={port}", target], timeout=TAILSCALE_TIMEOUT)
    if r.returncode != 0:
        raise ConnectError(f"`tailscale funnel` failed: {_text(r) or 'no output'}. Funnel needs HTTPS certificates and the `funnel` "
                           "attribute for this computer in the Tailscale admin console (the command prints a link to turn it on).")
    url = f"https://{dns}" + ("" if port == 443 else f":{port}")
    _save(inst, CONNECT_MODE="funnel", CONNECT_URL=url, CONNECT_HOST=url, ASSISTANT_PUBLIC_URL=url)
    return url


def funnel_off(m: Instances, inst: Instance) -> None:
    port = urlsplit(inst.connect_url).port or 443
    r = _run(m, ["tailscale", "funnel", f"--https={port}", "off"], timeout=TAILSCALE_TIMEOUT)
    if r.returncode != 0:
        raise ConnectError(f"`tailscale funnel ... off` failed: {_text(r) or 'no output'}")
    _forget(inst, *CONNECT_KEYS, "ASSISTANT_PUBLIC_URL")


# ---------------------------------------------------------------------------------------------------------- Kelo address


def kelo_connect(m: Instances, inst: Instance, name: str | None = None, *, broker: str | None = None,
                 post: Callable[..., Any] | None = None) -> str:
    post = post or _post
    from .kelo_names import name_problem

    name = (name or ("" if inst.is_main else inst.name)).strip().lower()
    if not name:
        raise ConnectError("choose the name for your address: assistant connect kelo --name <name>  (gives https://<name>.kelo.chat)")
    if problem := name_problem(name):
        raise ConnectError(problem)
    base = kelo_broker(inst, broker)
    install = read_env(inst.env, "KELO_INSTALL_ID")
    if len(install) < 32:
        install = secrets.token_urlsafe(32)          # this copy's own proof of ownership; only its hash leaves this computer
        _save(inst, KELO_INSTALL_ID=install)
    if inst.connect_mode == "kelo" and read_env(inst.env, "KELO_NAME") not in ("", name):
        kelo_release(inst, base, post=post)          # one name per copy: give the old one back before asking for the new one
    try:
        r = post(f"{base}/broker/tunnel/claim", json={"name": name, "install_id": install}, timeout=60)
    except httpx.HTTPError:
        raise ConnectError(f"the Kelo address service ({base}) is not answering; try again later") from None
    if r.status_code != 200:
        raise _broker_error(r, urlsplit(base).hostname or base)
    got = r.json()
    host, token = str(got.get("hostname", "")).lower(), str(got.get("tunnel_token", ""))
    if not DOMAIN.match(host) or not host.startswith(name + ".") or len(token) < 20:
        raise ConnectError("the Kelo address service gave an answer that does not fit; nothing was changed")
    d = kelo_dir(m, inst)
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    write_env(d / "tunnel.env", {"TUNNEL_TOKEN": token})    # read only by the tunnel container, not by the server
    url = f"https://{host}"
    _save(inst, CONNECT_MODE="kelo", CONNECT_URL=url, CONNECT_HOST=host, ASSISTANT_PUBLIC_URL=url, KELO_NAME=name,
          KELO_ENV_FILE="./" + str((d / "tunnel.env").relative_to(m.root)), COMPOSE_PROFILES=_profiles(inst, add="kelo"))
    if broker:
        _save(inst, ASSISTANT_KELO_BROKER=base)
    return url


def kelo_refresh(m: Instances, inst: Instance, *, post: Callable[..., Any] | None = None, restart: bool = True) -> str | None:
    """A copy on a Kelo address asks for its own name again (kelo_address.kelo_refresh, here over httpx)."""
    return _kelo_refresh(m, inst, post=post or _post, restart=restart, net_errors=(httpx.HTTPError,))


def kelo_release(inst: Instance, base: str, *, post: Callable[..., Any] | None = None) -> str | None:
    """Give the name back. Returns a note when that could not be done (the name is then freed after 30 idle days)."""
    post = post or _post
    install = read_env(inst.env, "KELO_INSTALL_ID")
    if not install:
        return None
    try:
        r = post(f"{base}/broker/tunnel/release", json={"install_id": install}, timeout=60)
    except httpx.HTTPError:
        return f"the Kelo address service ({base}) did not answer, so the name is still held; it is freed after 30 days without use"
    if r.status_code != 200:
        return f"the Kelo address service could not release the name ({_broker_error(r, base)}); it is freed after 30 days without use"
    return None


def kelo_off(m: Instances, inst: Instance, *, post: Callable[..., Any] | None = None) -> str | None:
    post = post or _post
    note = kelo_release(inst, kelo_broker(inst), post=post)
    m.run(m.compose(inst, "--profile", "kelo", "rm", "-sf", "kelo-tunnel"), cwd=m.root, capture_output=True)
    shutil.rmtree(kelo_dir(m, inst), ignore_errors=True)
    profiles = _profiles(inst, drop="kelo")
    _forget(inst, *CONNECT_KEYS, "ASSISTANT_PUBLIC_URL", "KELO_NAME", "KELO_ENV_FILE")
    _save(inst, COMPOSE_PROFILES=profiles)
    return note


# ---------------------------------------------------------------------------------------------------------- own domain
def parse_address(text: str) -> tuple[str, int | None, str]:
    """`me.duckdns.org`, `kelo.me.duckdns.org`, `me.example.com:8443` or `https://me.duckdns.org:8443` -> (host, port, https URL)."""
    raw = (text or "").strip()
    if raw.lower().startswith("http://"):
        raise ConnectError("the address must be https (phones send the sign-in over it). Give the host, e.g. me.duckdns.org")
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    try:
        port = parts.port
    except ValueError:
        raise ConnectError(f"{raw!r}: the port must be a number from 1 to 65535") from None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or parts.path not in ("", "/") or parts.query or parts.username or not DOMAIN.match(host):
        raise ConnectError(f"{raw!r} is not an address. Examples: me.duckdns.org, kelo.me.duckdns.org, https://me.example.com:8443")
    if port == 0:
        raise ConnectError(f"{raw!r}: the port must be a number from 1 to 65535")
    return host, port, f"https://{host}" + ("" if port in (None, 443) else f":{port}")


def is_bare(host: str) -> bool:
    """A name that is probably already the front page of something: example.com or me.duckdns.org (not kelo.me.duckdns.org)."""
    from .ddns import label_of

    label = label_of(host)
    return host == f"{label}.duckdns.org" if label else host.count(".") == 1


def _port_open(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.7):
            return True
    except OSError:
        return False


def _resolve(host: str) -> set[str]:
    import socket

    try:
        return {str(a[4][0]) for a in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    except OSError:
        return set()


def _public_ip(get: Callable[..., Any] | None = None) -> str | None:
    get = get or _get
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = get(url, timeout=8)
        except httpx.HTTPError:
            continue
        ip = (r.text or "").strip()
        if r.status_code == 200 and re.match(r"^[0-9a-fA-F:.]{3,45}$", ip):
            return ip
    return None


def lan_ip() -> str:
    """This computer's address on the home network (the one a proxy elsewhere in the house uses). Nothing is sent."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))      # a documentation address: picks the outgoing interface without sending a packet
            return str(s.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def _lan_ip() -> str:     # looked up when called, so tests can stand in a fixed address
    return lan_ip()


def _private(ip: str) -> bool:
    import ipaddress

    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def _duckdns(inst: Instance, host: str, token: str | None, get: Callable[..., Any]) -> None:
    """Seal a DuckDNS token into the copy's settings so its server keeps the name pointed here, and update once now."""
    if not token:
        return
    from . import ddns

    label = ddns.label_of(host)
    if not label:
        raise ConnectError("--duckdns only works with a name that ends in .duckdns.org")
    secret_key = read_env(inst.env, "ASSISTANT_SECRET_KEY")
    if not secret_key:
        raise ConnectError("this copy has no ASSISTANT_SECRET_KEY to keep the DuckDNS token sealed with; run `assistant setup` first "
                           "(or keep updating the name the way you do now and leave out --duckdns)")
    try:
        sealed = ddns.seal(secret_key, token)
    except ValueError as e:
        raise ConnectError(str(e)) from None
    try:
        r = get(ddns.UPDATE, params={"domains": label, "token": token.strip().lower(), "ip": ""}, timeout=20)
        ok = r.status_code == 200 and (r.text or "").strip().startswith("OK")
    except httpx.HTTPError:
        ok = False
    if not ok:
        raise ConnectError(f"DuckDNS did not accept the update for {label}.duckdns.org; check the token and that the name is yours")
    _save(inst, DUCKDNS_DOMAIN=label, DUCKDNS_TOKEN_ENC=sealed)


def _others_direct(m: Instances, inst: Instance) -> str | None:
    from .instances import MAIN

    for n in [MAIN, *m.names()]:
        if n == inst.name:
            continue
        try:
            other = m.load(n)
        except InstanceError:
            continue
        if other.connect_mode == "direct":
            return n
    return None


def domain_direct(m: Instances, inst: Instance, address: str, *, check_dns: bool = True, duckdns_token: str | None = None,
                  port_open: Callable[[str, int], bool] | None = None, resolve: Callable[[str], set[str]] | None = None,
                  public_ip: Callable[[], str | None] | None = None, get: Callable[..., Any] | None = None) -> tuple[str, list[str]]:
    """The bundled Caddy answers on ports 80/443 of THIS computer and gets a Let's Encrypt certificate for the host by itself.
    Refuses when anything already uses port 80 or 443 here: other services are never taken over."""
    get = get or _get
    port_open, resolve, public_ip = port_open or _port_open, resolve or _resolve, public_ip or _public_ip
    host, port, url = parse_address(address)
    if port not in (None, 443):
        raise ConnectError("--direct answers on ports 80 and 443 of this computer (the certificate needs them). For another port, "
                           "use --proxy with your own reverse proxy.")
    if (other := _others_direct(m, inst)):
        raise ConnectError(f"only one copy per computer can use --direct (ports 80 and 443), and {other!r} already does. "
                           "Use a sub-name through your own proxy (--proxy), a Kelo address, or Tailscale for this one.")
    if inst.connect_mode != "direct":
        for p in (80, 443):
            if port_open("127.0.0.1", p):
                raise ConnectError(f"port {p} is already in use on this computer (another web server or proxy). Kelo will not take it over. "
                                   f"If that is your reverse proxy, use  --proxy  and add {host} there; otherwise stop that service first.")
    notes: list[str] = []
    _duckdns(inst, host, duckdns_token, get)
    ips, mine = resolve(host), public_ip()
    if check_dns:
        if not ips:
            raise ConnectError(f"{host} does not resolve yet. Create the DNS record (A record to this computer's public address"
                               f"{' ' + mine if mine else ''}) and run this again.")
        if mine and mine not in ips:
            local = " (that is an address inside your home network, so it only works at home)" if all(_private(i) for i in ips) else ""
            raise ConnectError(f"{host} points to {', '.join(sorted(ips))}{local}, but this computer reaches the internet as {mine}. "
                               f"Point the record at {mine} (DuckDNS: update the name, or add --duckdns), wait a minute, and run this again. "
                               "If your setup is unusual, add --skip-dns-check.")
        if not mine:
            notes.append("could not learn this computer's public address, so the DNS record was not compared with it")
    _save(inst, DIRECT_HOST=host, CONNECT_MODE="direct", CONNECT_URL=url, CONNECT_HOST=address.strip(), ASSISTANT_PUBLIC_URL=url,
          COMPOSE_PROFILES=_profiles(inst, add="direct"))
    notes += [f"On your router, forward TCP ports 80 and 443 to this computer ({_lan_ip()}). Caddy gets the https certificate by itself "
              "the first time someone opens the address (it needs port 80 or 443 reachable from the internet for that).",
              "Some internet providers block incoming port 80/443 at home; then use a Kelo address or Tailscale Funnel instead."]
    return url, notes


def proxy_settings(host: str, url: str, target: str) -> str:
    return "\n".join([
        f"In your reverse proxy, add {host}:",
        f"  forward to            {target}   (scheme http)",
        "  websockets            on   (the live view of the agent's browser; in Nginx Proxy Manager: 'Websockets Support')",
        "  response buffering    off  (replies are streamed)",
        "  timeouts              at least 120 s (300 s is safer)",
        "  upload size           no limit (Kelo checks the size and the storage cap itself; large files come in 90 MB parts)",
        "  https                 a certificate for the host (Let's Encrypt), force https",
        "Nginx / Nginx Proxy Manager (Advanced tab, custom config):",
        "  proxy_buffering off; proxy_request_buffering off; proxy_cache off;",
        "  proxy_read_timeout 300s; proxy_send_timeout 300s; client_max_body_size 0;",
        "Caddy:",
        f"  {host} {{ reverse_proxy {target.removeprefix('http://')} {{ flush_interval -1 }} }}",
        "Traefik: a router for Host(`" + host + "`) with a service at " + target + " (Traefik streams by default; raise",
        "  respondingTimeouts on the entry point to 300s if you changed them).",
        f"The phone app then uses {url}.",
    ])


def domain_proxy(m: Instances, inst: Instance, address: str, *, bind: str = "0.0.0.0", lan: str | None = None,
                 duckdns_token: str | None = None, get: Callable[..., Any] | None = None) -> tuple[str, str, list[str]]:
    """You already run a reverse proxy (Nginx Proxy Manager, Traefik, Caddy...): Kelo listens on the home network and you route
    the host to it. Returns (url, proxy settings to enter, notes)."""
    get = get or _get
    host, port, url = parse_address(address)
    if bind != "0.0.0.0" and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", bind):
        raise ConnectError("--bind is an IPv4 address of this computer, e.g. 192.168.1.20 (or 0.0.0.0 for all)")
    _duckdns(inst, host, duckdns_token, get)
    lan = lan or (bind if bind != "0.0.0.0" else _lan_ip())
    target = f"http://{lan}:{inst.server_port}"
    notes: list[str] = []
    found = probe(url, get=get)
    if found == "kelo":
        notes.append(f"{url} already reaches a Kelo server.")
    elif found.startswith("other") and is_bare(host):
        notes.append(f"{url} already answers with something else. If that is another app behind your proxy, give Kelo its own "
                     f"sub-name instead (DuckDNS answers for every sub-name of yours, e.g. kelo.{host}) or another port "
                     f"(https://{host}:8443), and route that one to Kelo.")
    elif found.startswith("other"):
        notes.append(f"{url} already answers with something else; point that host (or a new sub-name) at Kelo in your proxy.")
    old_bind = read_env(inst.env, "SERVER_BIND")
    if inst.connect_mode != "proxy":
        _save(inst, CONNECT_PREV_BIND=old_bind or "-")
    _save(inst, SERVER_BIND=bind, CONNECT_MODE="proxy", CONNECT_URL=url, CONNECT_HOST=address.strip(), ASSISTANT_PUBLIC_URL=url)
    notes.append(f"Kelo now listens on {lan}:{inst.server_port} on your home network (plain http, every request needs the token). "
                 "Let only your proxy reach that port if you can (firewall).")
    return url, proxy_settings(host, url, target), notes


def domain_off(m: Instances, inst: Instance) -> None:
    if inst.connect_mode == "direct":
        m.run(m.compose(inst, "--profile", "direct", "rm", "-sf", "caddy"), cwd=m.root, capture_output=True)
        profiles = _profiles(inst, drop="direct")
        _forget(inst, "DIRECT_HOST")
        _save(inst, COMPOSE_PROFILES=profiles)
    if inst.connect_mode == "proxy":
        prev = read_env(inst.env, "CONNECT_PREV_BIND")
        if prev and prev != "-":
            _save(inst, SERVER_BIND=prev)
        else:
            _forget(inst, "SERVER_BIND")
        _forget(inst, "CONNECT_PREV_BIND")
    _forget(inst, *CONNECT_KEYS, "ASSISTANT_PUBLIC_URL", "DUCKDNS_DOMAIN", "DUCKDNS_TOKEN_ENC")


# ---------------------------------------------------------------------------------------------------------- status and switching
def probe(url: str, *, get: Callable[..., Any] | None = None) -> str:
    """GET <url>/healthz from here: "kelo" (a Kelo answers), "other: HTTP n" (something else answers) or "none: why"."""
    get = get or _get
    try:
        r = get(url.rstrip("/") + "/healthz", timeout=8, follow_redirects=False)
    except httpx.HTTPError as e:
        return f"none: {type(e).__name__}"
    try:
        if r.status_code == 200 and (r.json() or {}).get("ok") is True:
            return "kelo"
    except ValueError:
        pass
    return f"other: HTTP {r.status_code}"


def status(m: Instances, inst: Instance, *, get: Callable[..., Any] | None = None, check: bool = True) -> dict[str, str]:
    get = get or _get
    mode = inst.connect_mode
    if not mode and inst.public_url:          # an address written into the settings by hand (before `connect domain` existed)
        return {"mode": "manual", "method": "address set by hand (ASSISTANT_PUBLIC_URL)", "address": inst.public_url,
                "host": inst.public_url, "health": _health(probe(inst.public_url, get=get)) if check else "not checked"}
    if not mode:
        return {"mode": "", "method": "not open to the outside (only this computer)", "address": inst.local_url, "host": "", "health": ""}
    url = inst.connect_url
    return {"mode": mode, "method": MODES.get(mode, mode), "address": url, "host": read_env(inst.env, "CONNECT_HOST") or url,
            "health": _health(probe(url, get=get)) if check else "not checked"}


def _health(result: str) -> str:
    if result == "kelo":
        return "ok (GET /healthz answered through this address)"
    if result.startswith("other"):
        return f"something else answers ({result.split(': ', 1)[1]}): check the proxy / DNS"
    return f"not reachable from this computer ({result.split(': ', 1)[1]})"


def disconnect(m: Instances, inst: Instance, *, post: Callable[..., Any] | None = None) -> list[str]:
    """Undo whatever way this copy is reached now (release the Kelo name, stop Funnel or Serve, stop the tunnel or Caddy)."""
    post = post or _post
    notes: list[str] = []
    mode = inst.connect_mode
    if mode == "tailscale":
        tailscale_off(m, inst)
    elif mode == "funnel":
        funnel_off(m, inst)
    elif mode == "cloudflare":
        cloudflare_off(m, inst)
        notes.append("Your DNS record and tunnel stay in your Cloudflare account; delete them there if you no longer want them.")
    elif mode == "kelo":
        if note := kelo_off(m, inst, post=post):
            notes.append(note)
    elif mode in ("direct", "proxy"):
        domain_off(m, inst)
    return notes
