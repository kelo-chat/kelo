"""Shipping profiles ("배송 정보"): addresses the user saved in Kelo for orders, next to the wallet's cards.

Each profile is an address of any country (address.py: structured fields + country code, the phone as E.164), a delivery note, a
nickname ("Home", "Office") and one of them is the default. The agent may read these values (they are not card secrets) to fill a
checkout's shipping form during an order the user asked for (tools/order.py); the vault log names a profile by its nickname only, never
the address. The apps add, edit, delete and pick the default one (SHIPPING API in server.py).
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from . import address
from .address import AddressError
from .i18n import N_, t

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipping_profiles (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, country TEXT NOT NULL, name TEXT NOT NULL, organization TEXT NOT NULL DEFAULT '',
    address_line1 TEXT NOT NULL DEFAULT '', address_line2 TEXT NOT NULL DEFAULT '', dependent_locality TEXT NOT NULL DEFAULT '',
    locality TEXT NOT NULL DEFAULT '', admin_area TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL DEFAULT '',
    sorting_code TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, last_used REAL);
"""
MAX_LABEL, MAX_NOTE, MAX_PROFILES = 40, 200, 30
DEFAULT_LABEL = N_("Home")


class ShippingError(Exception):
    def __init__(self, message: str, status: int = 422, field: str = ""):
        super().__init__(message)
        self.status, self.field = status, field


def _plain(value: Any, limit: int, what: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ShippingError(f"{what} must be text", field=what)
    v = " ".join(value.split())
    if len(v) > limit:
        raise ShippingError(f"{what} must be at most {limit} characters", field=what)
    if address.CARD_NUMBER.search(v):
        raise ShippingError(f"{what} must not contain a card number", field=what)
    return v


class ShippingBook:
    def __init__(self, store: Any, clock: Any = time.time):
        self.store, self._clock = store, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    @staticmethod
    def public(r: dict[str, Any]) -> dict[str, Any]:
        out = {k: r[k] for k in ("id", "label", "country", *address.FIELDS, "phone", "note", "created_at", "last_used")}
        out["default"] = bool(r.get("is_default"))
        out["formatted"] = address.formatted(out)
        out["one_line"] = address.one_line(out)
        return out

    def profiles(self) -> list[dict[str, Any]]:
        return [self.public(r) for r in self.store._all("SELECT * FROM shipping_profiles ORDER BY is_default DESC, created_at, id")]

    def get(self, pid: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM shipping_profiles WHERE id = ?", (pid,))
        return self.public(rows[0]) if rows else None

    def default(self) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM shipping_profiles ORDER BY is_default DESC, created_at, id LIMIT 1")
        return self.public(rows[0]) if rows else None

    def find(self, ref: Any) -> dict[str, Any] | None:
        """A profile by id or nickname (any case); a unique part of a nickname too. Empty: the default one."""
        text = str(ref or "").strip()
        if not text:
            return self.default()
        items = self.profiles()
        for p in items:
            if p["id"] == text:
                return p
        low = text.casefold()
        same = [p for p in items if p["label"].casefold() == low]
        if len(same) == 1:
            return same[0]
        part = [p for p in items if low in p["label"].casefold()]
        return part[0] if len(part) == 1 else None

    def last_country(self) -> str:
        rows = self.store._all("SELECT country FROM shipping_profiles ORDER BY COALESCE(last_used, created_at) DESC LIMIT 1")
        return rows[0]["country"] if rows else ""

    def _check(self, body: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            addr = address.clean(body, partial=old)
        except AddressError as e:
            raise ShippingError(str(e), field=e.field) from None
        label = _plain(body["label"], MAX_LABEL, "label") if "label" in body else (old or {}).get("label", "")
        note = _plain(body["note"], MAX_NOTE, "note") if "note" in body else (old or {}).get("note", "")
        return {**addr, "label": label or t(DEFAULT_LABEL), "note": note}

    def add(self, body: dict[str, Any]) -> dict[str, Any]:
        if len(self.store._all("SELECT id FROM shipping_profiles")) >= MAX_PROFILES:
            raise ShippingError(f"at most {MAX_PROFILES} shipping profiles")
        v = self._check(body)
        pid = "ship_" + secrets.token_hex(6)
        first = not self.store._all("SELECT 1 FROM shipping_profiles LIMIT 1")
        cols = ["id", "label", "country", *address.FIELDS, "phone", "note", "created_at"]
        self.store._run(f"INSERT INTO shipping_profiles({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                        (pid, v["label"], v["country"], *(v[k] for k in address.FIELDS), v["phone"], v["note"], self._clock()))
        if first or body.get("default") is True:
            self._make_default(pid)
        self.store.add_vault_log(v["label"], "shipping-added", "shipping profile saved")
        return self.get(pid) or {}

    def update(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        old = self.get(pid)
        if old is None:
            raise ShippingError("no such shipping profile", 404)
        v = self._check(body, old)
        sets = {k: v[k] for k in ("label", "country", *address.FIELDS, "phone", "note")}
        self.store._run(f"UPDATE shipping_profiles SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), pid))
        if body.get("default") is True and not old["default"]:
            self._make_default(pid)
        elif "default" in body and body.get("default") is not True:
            raise ShippingError("a profile stops being the default only when another one is made the default ({\"default\": true} on it)")
        self.store.add_vault_log(v["label"], "shipping-changed", "shipping profile changed")
        return self.get(pid) or {}

    def delete(self, pid: str) -> bool:
        old = self.get(pid)
        if old is None:
            return False
        self.store._run("DELETE FROM shipping_profiles WHERE id = ?", (pid,))
        if old["default"]:   # the oldest one left takes its place
            rest = self.store._all("SELECT id FROM shipping_profiles ORDER BY created_at, id LIMIT 1")
            if rest:
                self._make_default(rest[0]["id"])
        self.store.add_vault_log(old["label"], "shipping-deleted", "removed by the user")
        return True

    def touch(self, pid: str) -> None:
        self.store._run("UPDATE shipping_profiles SET last_used = ? WHERE id = ?", (self._clock(), pid))

    def _make_default(self, pid: str) -> None:
        self.store._run("UPDATE shipping_profiles SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (pid,))

    def checked(self, body: dict[str, Any]) -> dict[str, Any]:
        """An address checked like a saved one but not stored (an address for one order only)."""
        v = self._check(body)
        out = {"id": "", **v, "default": False, "created_at": self._clock(), "last_used": None}
        out["formatted"], out["one_line"] = address.formatted(out), address.one_line(out)
        return out
