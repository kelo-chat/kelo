"""The Kelo address pieces that need nothing but the standard library: the broker address, the tunnel folder, the broker's error
words, and the refresh that asks the broker for a copy's own name again. The updater container (docker/updater.Dockerfile has no
third-party Python packages) runs that refresh every few hours, so it must not import connect.py (httpx, segno). connect.py
re-exports these names and passes httpx as its transport."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .cli import read_env, write_env
from .instances import DOMAIN, Instance, InstanceError, Instances

KELO_BROKER = "https://kelo.chat"


class ConnectError(InstanceError):
    """A step that did not work; the message says what to do."""


def kelo_broker(inst: Instance, given: str | None = None) -> str:
    url = (given or read_env(inst.env, "ASSISTANT_KELO_BROKER") or read_env(inst.env, "ASSISTANT_OAUTH_BROKER") or KELO_BROKER).strip().rstrip("/")
    parts = urlsplit(url)
    if not (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"))) or not parts.hostname:
        raise ConnectError(f"the Kelo address service must be an https address, not {url!r}")
    return url


def kelo_dir(m: Instances, inst: Instance) -> Path:
    return m.root / "connect" / "kelo" if inst.is_main else m.dir / inst.name / "kelo"


def broker_error(r: Any, host: str) -> ConnectError:
    try:
        body = r.json() or {}
    except ValueError:
        body = {}
    raw = body.get("detail") if isinstance(body, dict) else None
    code = str((body.get("code") if isinstance(body, dict) else "") or (raw.get("code") if isinstance(raw, dict) else "") or "")
    detail = str(raw.get("message") or "") if isinstance(raw, dict) else str(raw or "")
    if r.status_code == 404:
        return ConnectError(f"{host} does not hand out Kelo addresses (yet). Use Tailscale or your own domain instead.")
    if code == "full":
        return ConnectError("Sorry, no Kelo addresses are available right now. The operator has been told and is fixing it. Please "
                            "try again later, or use Tailscale or your own domain now (assistant connect tailscale | assistant connect "
                            "domain).")
    if code == "daily_cap":
        return ConnectError("this network has already taken the most new Kelo addresses allowed in a day. Try again tomorrow, or use "
                            "Tailscale or your own domain now (assistant connect tailscale | assistant connect domain).")
    if code == "busy":
        try:
            wait = int(r.headers.get("retry-after") or (raw.get("retry_after") if isinstance(raw, dict) else 0) or 0)
        except ValueError:
            wait = 0
        minutes = max(1, round(wait / 60)) if wait else 0
        when = f"about {minutes} minute{'s' if minutes != 1 else ''}" if minutes else "a while"
        return ConnectError(f"the Kelo address service is taking no new names right now (too many are waiting to connect). Try again "
                            f"in {when}, or use Tailscale or your own domain now (assistant connect tailscale | assistant connect domain).")
    if r.status_code == 429:
        return ConnectError("too many requests to the Kelo address service from this network; try again in 10 minutes")
    return ConnectError(detail or f"the Kelo address service answered {r.status_code}")


class Reply:
    """What the refresh reads from an answer: status_code, headers, json() (the same three things an httpx response has)."""

    def __init__(self, status: int, headers: Any, body: bytes):
        self.status_code, self.headers, self._body = status, headers, body

    def json(self) -> Any:
        return json.loads(self._body.decode("utf-8", "replace"))


def _post(url: str, *, json: Any = None, timeout: float = 60) -> Reply:   # noqa: A002 - same keywords as httpx.post
    import json as _json
    req = urllib.request.Request(url, data=_json.dumps(json).encode(), method="POST",   # noqa: S310 - https (or loopback) only, see kelo_broker
                                 headers={"content-type": "application/json", "user-agent": "kelo-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310
            return Reply(r.status, r.headers, r.read())
    except urllib.error.HTTPError as e:
        return Reply(e.code, e.headers, e.read())


def kelo_refresh(m: Instances, inst: Instance, *, post: Callable[..., Any] | None = None, restart: bool = True,
                 net_errors: tuple[type[BaseException], ...] = (OSError,)) -> str | None:
    """A copy on a Kelo address asks for its own name again (the broker hands the same copy its tunnel's token, or makes the tunnel
    and the CNAME again when the name was parked after days without a connector, or the tunnel is gone). When the token changed it
    is written and the tunnel container is recreated with it. Same address, so the phones need nothing. Owner re-claims never
    count against the broker's daily cap or its breaker. Returns a note for the user, or None when nothing changed."""
    if inst.connect_mode != "kelo":
        return None
    post = post or _post
    name, install = read_env(inst.env, "KELO_NAME"), read_env(inst.env, "KELO_INSTALL_ID")
    if not name or len(install) < 32:
        return None
    base = kelo_broker(inst)
    try:
        r = post(f"{base}/broker/tunnel/claim", json={"name": name, "install_id": install}, timeout=60)
    except net_errors:
        return f"the Kelo address service ({base}) is not answering; the address tunnel keeps its token for now"
    if r.status_code != 200:
        return f"could not check the Kelo address {name}: {broker_error(r, urlsplit(base).hostname or base)}"
    try:
        got = r.json()
    except ValueError:
        return None
    host, token = str(got.get("hostname", "")).lower(), str(got.get("tunnel_token", ""))
    if not DOMAIN.match(host) or not host.startswith(name + ".") or len(token) < 20:
        return None
    env = kelo_dir(m, inst) / "tunnel.env"
    if env.is_file() and read_env(env, "TUNNEL_TOKEN") == token:
        return None
    env.parent.mkdir(parents=True, exist_ok=True)
    env.parent.chmod(0o700)
    write_env(env, {"TUNNEL_TOKEN": token})
    if restart:
        m.run(m.compose(inst, "--profile", "kelo", "up", "-d", "--no-deps", "--force-recreate", "kelo-tunnel"), cwd=m.root,
              capture_output=True, env=m.compose_env(inst))
    return f"the tunnel of https://{host} was made again (it had been idle); the address is the same"
