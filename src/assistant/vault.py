"""The keychain: passwords and API keys the user gives the assistant, kept apart from chats, memory and ordinary files.

What the vault promises (and the code below makes true):
- values are entered only by the user, in a form of the app, never in chat, and are never shown again (not to the screen, not to the model);
- they are encrypted with AES-256-GCM, and each one is bound to *where it may be used* (its name, kind, hosts and placement are part of the
  authenticated data), so changing the allowed host in the database makes the value undecryptable instead of sending it somewhere else;
- the model can only ask the server to *use* a value for a registered host; it gets back the result, never the value;
- every use, refusal and change is written to a log the model cannot read or erase;
- the user can lock the whole vault at once.
"""

from __future__ import annotations

from .i18n import N_, t

KEYCHAIN = N_("My → Keychain")   # where the user manages logins and keys in the app (named to the model in the user's language)

import base64
import json
import os
import time
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .netguard import NetGuardError

PREFIX = "v2:"
KINDS = ("api", "login")
LOG_KEEP = 1000
TRANSIENT_TTL = 600.0


class VaultError(NetGuardError):
    """The vault refuses (locked, nothing stored, wrong place). The message is safe to show the model and the user."""


class Sealer:
    """AES-256-GCM with the item's identity as associated data. The key is derived from the app's master key (no second secret to keep)."""

    def __init__(self, master_key: bytes, info: bytes = b"assistant/vault/v2"):
        # `info` separates key domains: the wallet's cards use their own derived key (see Store), never the vault's
        raw = base64.urlsafe_b64decode(master_key)
        self._aes = AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(raw))

    def seal(self, plaintext: str, aad: str) -> str:
        nonce = os.urandom(12)
        return PREFIX + base64.urlsafe_b64encode(nonce + self._aes.encrypt(nonce, plaintext.encode(), aad.encode())).decode()

    def open(self, sealed: str, aad: str) -> str | None:
        """The plaintext, or None when the data was altered, belongs to another item/host, or the master key changed."""
        if not sealed.startswith(PREFIX):
            return None
        try:
            blob = base64.urlsafe_b64decode(sealed[len(PREFIX):])
            return self._aes.decrypt(blob[:12], blob[12:], aad.encode()).decode()
        except (InvalidTag, ValueError):
            return None


def aad_for(name: str, kind: str, hosts: list[str], placement: dict[str, Any]) -> str:
    """What a stored value is bound to. Change any of it and the value no longer opens."""
    return json.dumps([name, kind, sorted(hosts), placement], sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def mask_username(username: str) -> str:
    """'na***@gmail.com': enough to tell two accounts apart, not enough to know the account."""
    u = username.strip()
    if not u:
        return ""
    local, at, domain = u.partition("@")
    return f"{local[:2] if len(local) > 2 else local[:1]}***{at}{domain}"


class Transient:
    """Logins the user typed but did not save outright: held in memory only, ten minutes at most.
    `promote=False`: for one use, gone after it. `promote=True`: kept only if the sign-in works (then it is saved), dropped if it does not."""

    def __init__(self, ttl: float = TRANSIENT_TTL, clock: Any = time.monotonic):
        self._items: dict[str, tuple[str, str, float, bool]] = {}
        self.ttl, self._clock = ttl, clock

    def put(self, name: str, username: str, password: str, promote: bool = False) -> None:
        self._items[name] = (username, password, self._clock() + self.ttl, promote)

    def has(self, name: str) -> bool:
        item = self._items.get(name)
        if item and item[2] < self._clock():
            del self._items[name]
            return False
        return item is not None

    def promotes(self, name: str) -> bool:
        """True while the held login waits for a successful sign-in before it is saved."""
        return self.has(name) and self._items[name][3]

    def peek(self, name: str) -> tuple[str, str] | None:
        """The (username, password) without using it up (a sign-in form split over two pages needs it twice)."""
        return (self._items[name][0], self._items[name][1]) if self.has(name) else None

    def take(self, name: str) -> tuple[str, str] | None:
        """The (username, password) once; afterwards it is gone."""
        got = self.peek(name)
        self._items.pop(name, None)
        return got

    def discard(self, name: str) -> None:
        self._items.pop(name, None)


def record_http(store: Any, name: str, status: int, tool: str = "", *, write: bool = False) -> str:
    """Remember what a service said to a stored key, and return a sentence for the model when it was refused.
    `write`: the request asked for a change (not a GET). A 403 on a change, through a connection the owner made with a narrower
    permission than the service offers (Microsoft "Read only", GitHub "Public repositories only"; oauth_connect.grant_label), is that
    permission, not a wrong or expired key: the item is not marked refused (reads keep working) and the model is told to say so."""
    if status == 403 and write:
        from .oauth_connect import grant_label   # here: oauth_connect imports this module
        if granted := grant_label(store, name):
            store.add_vault_log(name, "failed", f"{tool} HTTP 403: connected with the {granted!r} permission only", False)
            return (f"\n[The service refused this change (HTTP 403) because the connection was made with the {granted!r} permission "
                    "only, which does not allow this change. The connection itself is fine and reading still works. Do not retry. Tell "
                    f"the user plainly that the account was connected with the {granted!r} permission only and has to be connected again "
                    "in the Connectors tab, choosing the permission that allows changes, before this can be done.]")
    if status in (401, 403):
        store.set_credential_result(name, "rejected", f"HTTP {status}")
        store.add_vault_log(name, "rejected", f"{tool} HTTP {status}", False)
        return (f"\n[The service refused the key (HTTP {status}): it may be wrong, expired or lack permission. "
                f"Do not keep trying; tell the user and ask them to check it in {t(KEYCHAIN)}.]")
    if 200 <= status < 300:
        store.set_credential_result(name, "ok", f"HTTP {status}")
    return ""


def use_credential(store: Any, name: str, *, tool: str, kind: str = "api", detail: str = "") -> dict[str, Any]:
    """Open one vault item for one use by the agent. Refuses when the vault is locked, the item is missing, of another kind or empty,
    and writes every use and every refusal to the vault log (which the agent cannot read)."""
    if store.vault_locked():
        store.add_vault_log(name, "denied", f"{tool}: the vault is locked", False)
        raise VaultError(f"The user has locked the keychain, so nothing in it can be used right now. Tell them they can unlock it in {t(KEYCHAIN)}.")
    cred = store.credential(name)
    if not cred:
        store.add_vault_log(name, "denied", f"{tool}: no such item", False)
        known = ", ".join(c["name"] for c in store.credentials()) or "none"
        raise VaultError(f"nothing named {name!r} in the vault. Items: {known}. Ask the user to add it with "
                         f"{'request_login' if kind == 'login' else 'add_service_key'}.")
    if cred["kind"] != kind:
        store.add_vault_log(name, "denied", f"{tool}: wrong kind", False)
        raise VaultError(f"{name!r} is a {'website login (use browser_login)' if cred['kind'] == 'login' else 'service key (use connector_get / connector_send / mcp_*)'}, "
                         "not what this tool uses.")
    if not cred["secret"]:
        store.add_vault_log(name, "denied", f"{tool}: nothing entered yet", False)
        raise VaultError(f"the user has not entered the {'password' if kind == 'login' else 'key'} for {name!r} yet. Its fill-in card "
                         f"is waiting in the chat (and in {t(KEYCHAIN)}); you are told when it is saved. If they closed it, ask once "
                         f"with {'request_login' if kind == 'login' else 'add_service_key'}.")
    store.add_vault_log(name, "used", f"{tool} {detail}".strip())
    store.touch_credential(name)
    return cred
