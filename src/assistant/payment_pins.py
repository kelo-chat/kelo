"""Payment PINs: a short number (4-8 digits) some checkouts ask for on an on-page keypad, saved by the user for one site.

Kept like an end-to-end wallet card (wallet.py), never like a vault value:
- the phone encrypts the PIN with its own Keystore key (usable only right after the owner's fingerprint, face or screen lock) and
  uploads only that ciphertext; the server stores the blob and cannot read it;
- for a payment the user approved, the phone opens it after the owner's check and sends it ONCE to a slot bound to that approval
  (`PinDeliveries`: memory only, a few minutes, single use);
- the agent can only have it typed (tools/payment_step.py, browser_type_payment_pin) on that site's checkout, inside the purchase window
  that approval opened, once; the model hears only that a PIN is saved / was typed, never a digit.

Every save, change, release and use is written to the vault log (which the agent cannot read). The keychain lock stops every use.

A task the user allowed at setup to buy on its own (schedule_grants.py) may use the PIN only when the setup said so (`use_pin`), and
even then the phone must release it for that run: the run asks the phone (an urgent `pin_needed` card and notification), the owner
confirms with fingerprint or face, and the PIN goes to that run's slot only. The server never keeps a copy it could reuse for later runs.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .wallet import MAX_BLOB, site_key, site_keys

MIN_DIGITS, MAX_DIGITS = 4, 8
PIN_TTL = 300.0          # seconds a released PIN waits to be typed (and a slot waits for the phone after Allow)

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_pins (
    id TEXT PRIMARY KEY, site TEXT NOT NULL UNIQUE, label TEXT NOT NULL DEFAULT '', digits INTEGER NOT NULL,
    e2e_blob TEXT NOT NULL, e2e_alg TEXT NOT NULL DEFAULT '', e2e_key_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used REAL, uses INTEGER NOT NULL DEFAULT 0);
"""


class PinError(Exception):
    """A refusal whose message is safe for the app and the model (it never contains a PIN). `status` is the HTTP code."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def pin_site(value: Any) -> str:
    """The registrable domain a PIN belongs to, from a URL or a host ("https://pay.shop.example.co.kr/x" -> "example.co.kr")."""
    text = str(value or "").strip().lower()
    if "://" in text:
        from urllib.parse import urlsplit
        text = urlsplit(text).hostname or ""
    text = text.split("/")[0].split(":")[0].strip(".")
    if not text or "." not in text or not re.fullmatch(r"[a-z0-9.\-]+", text) or re.fullmatch(r"[\d.]+", text):
        raise PinError("site must be the site's address, e.g. shop.example.com")
    return site_key(text)


def check_pin(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    if not re.fullmatch(r"\d{%d,%d}" % (MIN_DIGITS, MAX_DIGITS), text):
        raise PinError(f"the payment PIN must be {MIN_DIGITS} to {MAX_DIGITS} digits")
    return text


class PinSecret:
    """A PIN in memory on its way onto a keypad. Never printed; wiped once typed."""
    __slots__ = ("_digits",)

    def __init__(self, digits: str):
        self._digits = list(digits)

    def __repr__(self) -> str:
        return f"PinSecret({len(self._digits)} digits)"

    __str__ = __repr__

    def __len__(self) -> int:
        return len(self._digits)

    def digits(self) -> list[str]:
        return list(self._digits)

    def wipe(self) -> None:
        for i in range(len(self._digits)):
            self._digits[i] = "0"
        self._digits = []

    @property
    def empty(self) -> bool:
        return not self._digits


class PaymentPins:
    """The saved PINs, one per site (registrable domain). Only ciphertext made on the phone is kept."""

    def __init__(self, store: Any, clock: Any = time.time):
        self.store, self._clock = store, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    @staticmethod
    def _public(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["id"], "site": r["site"], "label": r["label"] or r["site"], "digits": int(r["digits"]),
                "e2e_alg": r["e2e_alg"], "e2e_key_id": r["e2e_key_id"], "created_at": r["created_at"], "updated_at": r["updated_at"],
                "last_used": r["last_used"], "uses": int(r["uses"] or 0)}

    def _row(self, pin_id: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM payment_pins WHERE id = ?", (pin_id,))
        return rows[0] if rows else None

    def list(self) -> list[dict[str, Any]]:
        return [self._public(r) for r in self.store._all("SELECT * FROM payment_pins ORDER BY site")]

    def get(self, pin_id: str) -> dict[str, Any] | None:
        r = self._row(pin_id)
        return self._public(r) if r else None

    def for_host(self, host: str) -> dict[str, Any] | None:
        """The PIN saved for the site of `host` (its registrable domain), or None."""
        keys = [k for k in site_keys((host or "").lower()) if k]
        if not keys:
            return None
        rows = self.store._all(f"SELECT * FROM payment_pins WHERE site IN ({','.join('?' * len(keys))})", tuple(keys))
        rows.sort(key=lambda r: r["site"] != keys[0])   # the list's key first; one saved under the old rule still matches
        return self._public(rows[0]) if rows else None

    def blob(self, pin_id: str) -> dict[str, str] | None:
        r = self._row(pin_id)
        return {"blob": r["e2e_blob"], "alg": r["e2e_alg"], "key_id": r["e2e_key_id"]} if r else None

    @staticmethod
    def _e2e(value: Any) -> tuple[str, str, str]:
        if not isinstance(value, dict):
            raise PinError("e2e must be an object: {blob, alg, key_id} (the phone's ciphertext; never the PIN itself)")
        blob, alg, key_id = value.get("blob"), value.get("alg", ""), value.get("key_id", "")
        if not isinstance(blob, str) or not blob or len(blob) > MAX_BLOB or not re.fullmatch(r"[A-Za-z0-9+/=_\-.:]+", blob):
            raise PinError(f"e2e.blob must be the phone's ciphertext as base64 text (at most {MAX_BLOB} characters)")
        if not isinstance(alg, str) or len(alg) > 40 or not isinstance(key_id, str) or len(key_id) > 120:
            raise PinError("e2e.alg (at most 40 characters) and e2e.key_id (at most 120) must be text")
        return blob, alg, key_id

    @staticmethod
    def _digits(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not MIN_DIGITS <= value <= MAX_DIGITS:
            raise PinError(f"digits must be the PIN's length, {MIN_DIGITS} to {MAX_DIGITS}")
        return value

    @staticmethod
    def _label(value: Any) -> str:
        text = " ".join(str(value or "").split())[:60]
        if re.search(r"\d{4,}", text):
            raise PinError("the name must not contain digits that could be the PIN")
        return text

    def save(self, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Save (or replace) the PIN of a site: {site, label?, digits, e2e}. Returns (the PIN, replaced)."""
        if any(k in body for k in ("pin", "number", "value")):
            raise PinError("send only the phone's ciphertext (e2e), never the PIN itself")
        site = pin_site(body.get("site"))
        digits = self._digits(body.get("digits"))
        blob, alg, key_id = self._e2e(body.get("e2e"))
        label = self._label(body.get("label"))
        now = self._clock()
        old = self.store._all("SELECT id FROM payment_pins WHERE site = ?", (site,))
        if old:
            pid = old[0]["id"]
            self.store._run("UPDATE payment_pins SET label = ?, digits = ?, e2e_blob = ?, e2e_alg = ?, e2e_key_id = ?, updated_at = ? "
                            "WHERE id = ?", (label, digits, blob, alg, key_id, now, pid))
        else:
            pid = "pin_" + secrets.token_hex(8)
            self.store._run("INSERT INTO payment_pins(id, site, label, digits, e2e_blob, e2e_alg, e2e_key_id, created_at, updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?)", (pid, site, label, digits, blob, alg, key_id, now, now))
        self.store.add_vault_log(label or site, "pin-replaced" if old else "pin-saved",
                                 f"payment PIN for {site} ({digits} digits, kept on the phone only)")
        return self.get(pid) or {}, bool(old)

    def update(self, pin_id: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._row(pin_id)
        if r is None:
            raise PinError("no such payment PIN", 404)
        if any(k in body for k in ("pin", "number", "value")):
            raise PinError("send only the phone's ciphertext (e2e), never the PIN itself")
        sets: dict[str, Any] = {}
        if "label" in body:
            sets["label"] = self._label(body.get("label"))
        if "e2e" in body:
            sets["e2e_blob"], sets["e2e_alg"], sets["e2e_key_id"] = self._e2e(body.get("e2e"))
            sets["digits"] = self._digits(body.get("digits"))
        elif "digits" in body:
            raise PinError("a new length comes with a new PIN (e2e)")
        if not sets:
            return self._public(r)
        sets["updated_at"] = self._clock()
        self.store._run(f"UPDATE payment_pins SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), pin_id))
        self.store.add_vault_log(sets.get("label", r["label"]) or r["site"], "pin-replaced" if "e2e_blob" in sets else "pin-renamed",
                                 f"payment PIN for {r['site']}")
        return self.get(pin_id) or {}

    def delete(self, pin_id: str) -> bool:
        r = self._row(pin_id)
        if r is None:
            return False
        self.store._run("DELETE FROM payment_pins WHERE id = ?", (pin_id,))
        self.store.add_vault_log(r["label"] or r["site"], "pin-deleted", f"payment PIN for {r['site']}")
        return True

    def used(self, pin_id: str, host: str) -> None:
        r = self._row(pin_id)
        if r is None:
            return
        self.store._run("UPDATE payment_pins SET last_used = ?, uses = uses + 1 WHERE id = ?", (self._clock(), pin_id))
        self.store.add_vault_log(r["label"] or r["site"], "pin-typed", f"typed on the keypad of the checkout on {host}")

    def log(self, pin: dict[str, Any], action: str, detail: str, ok: bool = True) -> None:
        self.store.add_vault_log(pin.get("label") or pin.get("site") or "payment PIN", action, detail, ok)


# ---------------------------------------------------------------- one-time release from the phone
@dataclass
class _Slot:
    pin: dict[str, Any]
    host: str
    info: dict[str, Any]
    expires: float
    secret: PinSecret | None = None
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    taken: bool = False


class PinDeliveries:
    """Where the phone puts a payment PIN for ONE approved payment (or one run of a task allowed to use it). A slot exists only for an
    approval that named a site with a saved PIN; it takes one delivery and one use, and is gone after `ttl` seconds, when the reply
    ends, or when the payment is denied. Nothing here touches the disk."""

    def __init__(self, ttl: float = PIN_TTL, clock: Any = time.monotonic):
        self.ttl, self._clock = ttl, clock
        self._slots: dict[str, _Slot] = {}
        self.on_request: Any = None    # (pin_needed event) -> None: the server's urgent notification for a release nobody asked on a card

    def _sweep(self) -> None:
        now = self._clock()
        for k in [k for k, s in self._slots.items() if s.expires < now]:
            self.discard(k)

    def expect(self, slot_id: str, pin: dict[str, Any], host: str, info: dict[str, Any] | None = None, wait: float | None = None) -> None:
        self._sweep()
        self._slots[slot_id] = _Slot(pin, host, dict(info or {}), self._clock() + (self.ttl if wait is None else wait))

    def started(self, slot_id: str) -> float | None:
        """The payment was approved: the phone now has `ttl` seconds to send the PIN. Returns the wall-clock deadline."""
        slot = self._slots.get(slot_id)
        if slot is None:
            return None
        slot.expires = self._clock() + self.ttl
        return time.time() + self.ttl

    def has(self, slot_id: str) -> bool:
        self._sweep()
        return slot_id in self._slots

    def slot_pin(self, slot_id: str) -> dict[str, Any] | None:
        s = self._slots.get(slot_id)
        return s.pin if s else None

    def arrived(self, slot_id: str) -> bool:
        s = self._slots.get(slot_id)
        return bool(s and s.secret is not None and not s.secret.empty)

    def pending(self) -> list[dict[str, Any]]:
        """Slots still waiting for the phone (what GET /api/payment-pins/deliveries lists): never a PIN."""
        self._sweep()
        now = self._clock()
        return [{"slot_id": k, "approval_id": k, "pin": {f: s.pin.get(f) for f in ("id", "site", "label", "digits")}, "site": s.host,
                 **s.info, "expires_in": max(0, round(s.expires - now))}
                for k, s in self._slots.items() if s.secret is None and not s.taken]

    def deliver(self, slot_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._sweep()
        slot = self._slots.get(slot_id)
        if slot is None or slot.taken:
            raise PinError("no payment PIN is waiting for that approval (it was used, denied, or timed out)", 404)
        if slot.secret is not None:
            raise PinError("the payment PIN for that approval was already sent", 409)
        if body.get("pin_id") not in (None, slot.pin.get("id")):
            raise PinError("that approval is for another payment PIN", 422)
        pin = check_pin(body.get("pin"))
        if len(pin) != int(slot.pin.get("digits") or len(pin)):
            raise PinError("that PIN does not have the saved length", 422)
        slot.secret = PinSecret(pin)
        del pin
        slot.expires = self._clock() + self.ttl
        slot.arrived.set()
        return slot.pin

    async def wait(self, slot_id: str, timeout: float | None = None) -> PinSecret | None:
        """The delivered PIN, taken out of the slot (single use); None when it does not come in time or was used already."""
        slot = self._slots.get(slot_id)
        if slot is None or slot.taken:
            return None
        if slot.secret is None:
            try:
                await asyncio.wait_for(slot.arrived.wait(), self.ttl if timeout is None else timeout)
            except asyncio.TimeoutError:
                return None
        slot = self._slots.get(slot_id)
        if slot is None or slot.taken or slot.secret is None or slot.secret.empty:
            return None
        slot.taken = True
        secret, slot.secret = slot.secret, None
        return secret

    def discard(self, slot_id: str) -> None:
        slot = self._slots.pop(slot_id, None)
        if slot and slot.secret:
            slot.secret.wipe()
        if slot:
            slot.arrived.set()   # a typing step still waiting wakes up and finds nothing


# ---------------------------------------------------------------- the payment the user approved last, per browser tab
@dataclass
class ApprovedPayment:
    """What the payment-step card shows as the order: written by confirm_purchase when a payment is approved."""
    owner: str
    host: str
    summary: str
    amount_text: str
    items: list[dict[str, Any]]
    pay_with: str
    at: float
    pin_slot: str = ""             # the PinDeliveries slot of this payment ("" when no PIN is saved for the site)
    pin_used: bool = False
    path: str = ""                 # how it is paid, for PaymentPaths: card_form | site_method (the card is typed, or the site's own way)
    amount: float | None = None    # the approved total as a number (amount_text is for showing) and its ISO currency, for purchase_result
    currency: str = ""
    task_id: int | None = None     # the task (reply or run) that paid; its end warns when the receipt was never checked
    state: str = "open"            # purchase_result's check: open (not checked) | closed (checked, kept, or cancelled) | cancel (waiting for the cancel)
    warned: bool = False           # the end of the task already said the receipt was not confirmed

    def order(self) -> dict[str, Any]:
        return {"site": self.host, "summary": self.summary, "amount_text": self.amount_text, "items": self.items, "pay_with": self.pay_with}


class PaymentLedger:
    """The last approved payment of each browser tab (memory only), so a later step of that same checkout knows what it belongs to."""
    KEEP = 1800.0

    def __init__(self, clock: Any = time.time):
        self.clock = clock
        self._last: dict[str, ApprovedPayment] = {}

    def record(self, p: ApprovedPayment) -> None:
        self._last[p.owner] = p

    def last(self, owner: str, host: str = "") -> ApprovedPayment | None:
        p = self._last.get(owner)
        if p is None or self.clock() - p.at > self.KEEP:
            return None
        if host and not set(site_keys(host.lower())) & set(site_keys(p.host.lower())):
            return None
        return p

    def forget(self, owner: str) -> None:
        self._last.pop(owner, None)

    def unchecked(self, task_id: int | None) -> list[ApprovedPayment]:
        """Payments of that task whose receipt purchase_result never closed, not yet warned about (none without a task)."""
        if not task_id:
            return []
        return [p for p in self._last.values() if p.state != "closed" and not p.warned and p.task_id == task_id]


# ---------------------------------------------------------------- which way of paying went through on a site
PATH_WORDS = {
    "card_form": "a wallet card typed into the page's card form (browser_fill_card)",
    "app_approval": "approving the payment in the card or bank app (payment_step_wait)",
    "pin_plain": "the saved payment PIN in a plain PIN box or digit keypad",
    "site_method": "the site's own payment method with its PIN keypad",
}
PATHS_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_paths (
    site TEXT NOT NULL, path TEXT NOT NULL, outcome TEXT NOT NULL, times INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL,
    PRIMARY KEY (site, path));
"""


class PaymentPaths:
    """Per site (registrable domain), which ways of paying went through without a protected keypad ("smooth") and which met one
    ("protected"), the latest outcome per way. Generic: the ways are kinds of payment, never a site's or card company's own names."""

    def __init__(self, store: Any, clock: Any = time.time):
        self.store, self._clock = store, clock
        with store._lock:
            store._db.executescript(PATHS_SCHEMA)
            store._db.commit()

    def record(self, host: str, path: str, outcome: str) -> None:
        if path not in PATH_WORDS or outcome not in ("smooth", "protected"):
            return
        site = site_key((host or "").lower())
        if not site:
            return
        self.store._run("INSERT INTO payment_paths(site, path, outcome, updated_at) VALUES(?,?,?,?) ON CONFLICT(site, path) DO UPDATE SET "
                        "times = CASE WHEN payment_paths.outcome = excluded.outcome THEN payment_paths.times + 1 ELSE 1 END, "
                        "outcome = excluded.outcome, updated_at = excluded.updated_at", (site, path, outcome, self._clock()))

    def for_site(self, host: str) -> list[dict[str, Any]]:
        keys = site_keys((host or "").lower())
        return self.store._all(f"SELECT path, outcome, times, updated_at FROM payment_paths WHERE site IN ({','.join('?' * len(keys))}) "
                               "ORDER BY updated_at DESC", tuple(keys))

    def hint(self, host: str) -> str:
        """One line for the agent: which way to prefer on this site next time, or ""."""
        rows = self.for_site(host)
        good = [PATH_WORDS[r["path"]] for r in rows if r["outcome"] == "smooth"]
        bad = [PATH_WORDS[r["path"]] for r in rows if r["outcome"] == "protected"]
        if not bad:
            return ""
        site = site_key((host or "").lower())
        line = f"[On {site} before: {'; '.join(bad)} met a keypad the user had to enter by hand."
        if good:
            line += f" What went through without it: {'; '.join(good)}. Prefer that way when the page offers it."
        else:
            line += " If the page offers another way (for example a wallet card in a normal card form, or approval in the card app), prefer it."
        return line + "]"
