"""`assistant operator link|unlink|status <site copy>` (docs/site-only.md): the one-time link between the main copy and a site copy.

`link` makes a random operator link key (256 bits, never printed). The site copy keeps only its SHA-256 (KELO_OPERATOR_LINK_SHA256 in
its settings); the main copy keeps the key itself in its own .env (KELO_OPERATOR_SITE_KEY, next to ASSISTANT_TOKEN) with the site's
address (KELO_OPERATOR_SITE_URL). The key then opens the site's /api/operator/* lists only (api/auth.py require_operator). Both
copies must be restarted to use the new settings; this module prints the commands and does not restart anything itself.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from typing import Any

from .cli import read_env, remove_env, write_env
from .instances import MAIN, Instances, InstanceError

SITE_LINE = "KELO_OPERATOR_LINK_SHA256"
MAIN_LINES = ("KELO_OPERATOR_SITE_URL", "KELO_OPERATOR_SITE_KEY")


def link(m: Instances, name: str, *, url: str | None = None, out: Callable[[str], Any] = print) -> None:
    if name == MAIN:
        raise InstanceError("name the site copy (for example: assistant operator link operator)")
    inst = m.load(name)
    if read_env(inst.env, "ASSISTANT_SITE_ONLY") != "1" and read_env(inst.env, "ASSISTANT_OPERATOR") != "1":
        raise InstanceError(f"{name} is not a site copy: run `assistant instance site-only {name} on` first")
    site = (url or read_env(inst.env, "ASSISTANT_PUBLIC_URL") or "").strip().rstrip("/")
    if not site.startswith("https://") or "/" in site[len("https://"):] or len(site) <= len("https://"):
        raise InstanceError("the site's address must be https://<host>, with no path (set ASSISTANT_PUBLIC_URL or pass --url)")
    key = secrets.token_urlsafe(32)
    write_env(inst.env, {SITE_LINE: hashlib.sha256(key.encode()).hexdigest()})
    write_env(m.env_path(MAIN), {"KELO_OPERATOR_SITE_URL": site, "KELO_OPERATOR_SITE_KEY": key})
    out(f"Linked {name} at {site}. The key is kept in this server's .env and is not shown; the site keeps only its hash.")
    out(f"Restart both copies to use it: assistant instance start {name}, then the main server.")


def status(m: Instances, *, out: Callable[[str], Any] = print) -> bool:
    """The main copy's link: asks the site copy whether this server's key is accepted (never prints the key). True when it is."""
    import asyncio
    from .operator_site import OperatorSite, OperatorSiteDown
    main = m.env_path(MAIN)
    url, key = read_env(main, "KELO_OPERATOR_SITE_URL"), read_env(main, "KELO_OPERATOR_SITE_KEY")
    if not (url and key):
        out("not linked: no site copy is set (assistant operator link <name> makes the link)")
        return False
    try:
        code, body = asyncio.run(OperatorSite(url, key).call("GET", "/api/operator/status"))
    except OperatorSiteDown as e:
        out(f"linked to {url}: not ok ({e.code})")
        return False
    if code == 200 and isinstance(body, dict) and body.get("operator") is True:
        out(f"linked: ok (operator: true) at {url}")
        return True
    out(f"linked to {url}: not ok (HTTP {code})")
    return False


def unlink(m: Instances, name: str, *, out: Callable[[str], Any] = print) -> None:
    if name == MAIN:
        raise InstanceError("name the site copy (for example: assistant operator unlink operator)")
    inst = m.load(name)
    remove_env(inst.env, [SITE_LINE])
    remove_env(m.env_path(MAIN), list(MAIN_LINES))
    out(f"Unlinked {name}. Restart both copies to apply it; no data was deleted.")
