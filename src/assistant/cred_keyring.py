"""Connector API keys in the key container (docs/keyring.md, backlog 61c). A credential that qualifies (keyring_ok) is moved there:
the vault row keeps its name, hosts and placement with secret NULL and keyring = 1, `store.credential()` gives the placeholder as its
secret, and the request goes through the key container (via()), which puts the key only into that credential's own header or
parameter, only for its hosts, and redacts it from the answer."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .channels.discord import Discord
from .channels.slack import Slack
from .channels.telegram import Telegram
from .credentials import local_origin
from .keyguard import Locked
from .keyring_client import KeyringClient, KeyringError, KeyringTransport
from .keyring_service import _valid_host
from .local_login import is_login
from .mcp_oauth import is_bundle
from .vpn import ITEMS as VPN_ITEMS
from .weather import KEYS as WEATHER_KEYS

log = logging.getLogger("uvicorn.error")

ENTRY = "cred:{name}"
MOVABLE = ("bearer_header", "custom_header", "query_param")

RESERVED: frozenset[str] = frozenset({
    *(spec.item for spec in WEATHER_KEYS.values()),
    *VPN_ITEMS,
    Telegram.item,
    Discord.item,
    Slack.item,
})

_BG_TASKS: set[asyncio.Task[Any]] = set()


def entry_name(name: str) -> str:
    return ENTRY.format(name=name)


def slot_of(placement: dict) -> dict | None:
    """bearer_header -> {"header": "authorization"}; custom_header -> {"header": lower(placement["header"])}; query_param ->
    {"param": placement["param"]}; else None."""
    if not isinstance(placement, dict):
        return None
    ptype = placement.get("type")
    if ptype == "bearer_header":
        return {"header": "authorization"}
    if ptype == "custom_header":
        h = str(placement.get("header") or "").strip().lower()
        return {"header": h} if h else None
    if ptype == "query_param":
        p = str(placement.get("param") or "").strip()
        return {"param": p} if p else None
    return None


def keyring_ok(cred: dict, secret: str | None) -> bool:
    """kind "api"; a placement in MOVABLE; no local_origin; 1-5 hosts that keyring_service would accept; the secret is a plain one-line
    string (not JSON — mail/calendar/MCP bundles —, not keyconnect.is_minted, not local_login.is_login, not mcp_oauth.is_bundle); and the
    name is not one of RESERVED."""
    if not isinstance(cred, dict):
        return False
    if cred.get("kind") != "api":
        return False
    name = cred.get("name")
    if not name or name in RESERVED:
        return False
    placement = cred.get("placement") or {}
    if not isinstance(placement, dict) or placement.get("type") not in MOVABLE:
        return False
    if local_origin(cred):
        return False
    hosts = cred.get("hosts")
    if not isinstance(hosts, list) or not (1 <= len(hosts) <= 5) or not all(_valid_host(h) for h in hosts):
        return False
    if not isinstance(secret, str) or not secret or "\n" in secret or "\r" in secret or len(secret) > 4096:
        return False
    from .keyconnect import is_minted
    if is_minted(secret) or is_login(secret) or is_bundle(secret):
        return False
    try:
        data = json.loads(secret)
        if isinstance(data, (dict, list)):
            return False
    except (ValueError, TypeError):
        pass
    return True


def via(cred: dict, fetcher: Any, keyring: Any) -> Any:
    """The fetcher to send this credential's request with: unchanged for a vault-held key; for one in the key container, the same
    fetcher with transport=KeyringTransport(keyring, entry_name(cred["name"]))."""
    if not (cred and cred.get("keyring") and keyring and getattr(keyring, "enabled", False)):
        return fetcher
    name = cred.get("name", "")
    t = KeyringTransport(keyring, entry_name(name))

    def wrapped(method: str, url: str, **kw: Any) -> Any:
        kw["transport"] = t
        return fetcher(method, url, **kw)

    return wrapped


async def sync(store: Any, keyring: Any) -> dict[str, int]:
    """Move every qualifying vault key with a secret into the key container (put with kind "connector", hosts, local False, hint = last
    4, slot), then mark the row (store.credential_to_keyring); and delete container entries `cred:*` whose row is gone or no longer
    marked. A key that fails to go stays in the vault and is tried again next time. Returns {"moved", "deleted"}. Never raises; does
    nothing when the keyring is off or locked."""
    out = {"moved": 0, "deleted": 0}
    if not keyring or not getattr(keyring, "enabled", False):
        return out
    try:
        if keyring.ring_key() is None:
            return out
    except Exception:
        return out

    try:
        # Move qualifying keys from vault to keyring
        for c in store.credentials():
            if c.get("keyring"):
                continue
            try:
                item = store.credential(c["name"])
            except Locked:
                return out
            except Exception:
                continue
            if not item or not item.get("secret"):
                continue
            secret = item["secret"]
            if not keyring_ok(item, secret):
                continue
            slot = slot_of(item.get("placement") or {})
            if not slot:
                continue
            ename = entry_name(item["name"])
            try:
                await keyring.put(ename, secret, hosts=item["hosts"], local=False, hint=secret[-4:], kind="connector", slot=slot)
                if store.credential_to_keyring(item["name"], secret):
                    out["moved"] += 1
            except (KeyringError, Exception) as e:
                log.warning("keyring: moving key %s failed (%s)", item["name"], getattr(e, "code", type(e).__name__))

        # Delete container entries cred:* whose row is gone or no longer marked
        try:
            await keyring.refresh()
        except Exception:
            pass
        cred_entries = {n for n in getattr(keyring, "known", set()) if n.startswith("cred:")}
        marked = {entry_name(c["name"]) for c in store.credentials() if c.get("keyring")}
        for ename in sorted(cred_entries - marked):
            try:
                if await keyring.delete(ename):
                    out["deleted"] += 1
            except Exception:
                pass
    except Exception:
        log.warning("cred_keyring: sync failed", exc_info=False)
    return out


def spawn_sync(store: Any, keyring: Any) -> asyncio.Task[Any] | None:
    if not keyring or not getattr(keyring, "enabled", False):
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(sync(store, keyring))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task
