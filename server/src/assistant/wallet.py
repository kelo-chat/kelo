"""The wallet: payment cards the user added in the phone app, the rules for paying without asking, and the payment history.

Each card is kept one of two ways, the user's choice per card:
- auto_pay OFF (end-to-end): the phone encrypts the card number and CVC with a key only the phone holds and uploads that ciphertext.
  The server stores the blob and cannot read it. For each payment the user taps Allow on the phone and passes biometrics; the phone
  then sends the card ONCE to a slot bound to that approval (`CardDeliveries`: memory only, about five minutes, single use).
- auto_pay ON: the phone sends the number and CVC once over TLS and the server seals them (AES-256-GCM with a key derived from the
  master key for cards only, bound to the card's id and last 4 digits). The agent may then pay without the phone, but only inside the
  rules (`Wallet.decide`): a per-payment and a monthly limit per currency, a site the user approved a payment at before, the global
  pause off and the vault not locked. Every such payment leaves a notification and a log entry.

What never happens: a full card number or CVC in an API answer, a log line, a tool result or the model's context. They go only from
the phone (or the sealed copy) straight into the checkout page (`BrowserSession.fill_card`). The model sees label, brand, last 4.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .i18n import N_, t
from .tools.sites import registrable

# Per-currency defaults: (per payment, per calendar month). Roughly KRW 30,000 / 200,000 in each currency, rounded to amounts people
# use. A currency that is not here (and was not added by the user) is never paid automatically.
DEFAULT_LIMITS: dict[str, tuple[float, float]] = {
    "KRW": (30_000, 200_000), "USD": (25, 150), "EUR": (25, 150), "GBP": (20, 120), "JPY": (3_000, 20_000), "CNY": (150, 1_000),
    "CAD": (35, 200), "AUD": (40, 230), "NZD": (40, 250), "HKD": (200, 1_200), "SGD": (35, 200), "TWD": (800, 5_000),
    "CHF": (25, 150), "SEK": (270, 1_600), "NOK": (280, 1_700), "DKK": (180, 1_100), "PLN": (100, 600), "INR": (2_000, 12_000),
    "IDR": (400_000, 2_400_000), "THB": (900, 5_400), "VND": (600_000, 3_800_000), "PHP": (1_400, 8_500), "MYR": (110, 700),
    "BRL": (140, 850), "MXN": (450, 2_700), "TRY": (1_000, 6_000), "AED": (90, 550), "SAR": (90, 550), "ZAR": (450, 2_700),
}
CARD_TTL = 300.0          # seconds a card the phone sent waits to be used (and a slot waits for the phone after Allow)
MAX_BLOB = 8_192          # characters of the phone's ciphertext
CURRENCY = re.compile(r"^[A-Z]{3}$")

REASONS = {
    "auto": N_("Within the auto-pay rules"),
    "not_auto": N_("Auto-pay is not allowed for this card"),
    "no_server_copy": N_("The server has no card details for auto-pay"),
    "paused": N_("Auto-pay is paused"),
    "keyguard_off": N_("Auto-pay needs Keyguard; this payment asks on the phone"),
    "vault_locked": N_("The vault is locked"),
    "currency": N_("No auto-pay limit is set for this currency"),
    "over_per_payment": N_("Over the auto-pay limit per payment"),
    "over_monthly": N_("Over this month's auto-pay limit"),
    "first_site": N_("First payment on this site"),
    # a run of a task the user allowed at setup to buy on its own (schedule_grants.py), and why it still asks
    "task_grant": N_("Allowed at setup for this task"),
    "phone_needed": N_("This card is kept on the phone only, so the phone must send it"),
    "task_other_site": N_("This task may buy only on another site"),
    "task_currency": N_("This task's permission is in another currency"),
    "task_over_cap": N_("Over the most allowed per time for this task"),
    "task_over_monthly": N_("Over this task's monthly cap"),
    "task_other_item": N_("Not what this task may buy"),
    "task_again": N_("This run already made the purchase allowed for it"),
    "task_used_up": N_("This task already made the purchases allowed for it"),
    "task_other_card": N_("This task may pay only with another card"),
    "order_again": N_("This order already made its payment"),
}

# The same reasons for the model, precise enough to repeat to the user (it must not guess about the user's settings).
WALLET_PATH = "Wallet (My page, then Payment cards)"
EXPLAIN = {
    "auto": "it is within the user's auto-pay rules",
    "not_auto": ("this card was saved to ask each time (auto-pay is off for it). The user can turn on auto-pay for this card in "
                 f"{WALLET_PATH}: open the card, switch on Auto-pay; that asks for the card number and CVC again"),
    "no_server_copy": ("auto-pay needs the server's sealed copy of this card and there is none. The user can turn auto-pay on again for "
                       f"the card in {WALLET_PATH}, which asks for the number and CVC again"),
    "paused": f"the user paused auto-pay for the whole wallet ({WALLET_PATH}, Auto-pay limits, Pause)",
    "keyguard_off": ("auto-pay works only while Keyguard is on (My page, then Security, then Keyguard: the user's phones hold the key "
                     "of passwords, keys and cards). It is off now, so every payment asks on the phone; the card's auto-pay comes back "
                     "by itself when the user turns Keyguard on"),
    "vault_locked": "the user locked the keychain, so nothing in it can be used automatically",
    "currency": f"no auto-pay limit is set for this currency; the user can add one in {WALLET_PATH}, Auto-pay limits",
    "over_per_payment": f"the total is over the user's per-payment auto-pay limit for this currency ({WALLET_PATH}, Auto-pay limits)",
    "over_monthly": f"this month's auto-pay limit for this currency would be passed ({WALLET_PATH}, Auto-pay limits)",
    "first_site": ("this is the first payment with a wallet card on this site: the first payment on a new site always asks once, even "
                   "with auto-pay on. Later payments here can go automatically within the limits"),
    "phone_needed": ("this card is kept on the phone only (end-to-end), so the phone has to send it for every payment and a payment with "
                     f"it can never go on its own. The user can turn on auto-pay for this card in {WALLET_PATH} (it asks for the number "
                     "and CVC again)"),
    "order_again": ("this order already made its one payment; another payment in the same order always needs the user's approval, "
                    "also with auto-pay"),
    "no_card": "no wallet card is used, and a payment without one always needs the user's approval (unless the task was allowed to buy)",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_cards (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, brand TEXT NOT NULL, last4 TEXT NOT NULL,
    exp_month INTEGER NOT NULL, exp_year INTEGER NOT NULL, holder TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, auto_pay INTEGER NOT NULL DEFAULT 0, sealed TEXT,
    e2e_blob TEXT, e2e_alg TEXT NOT NULL DEFAULT '', e2e_key_id TEXT NOT NULL DEFAULT '', last_used REAL);
CREATE TABLE IF NOT EXISTS wallet_limits (currency TEXT PRIMARY KEY, per_payment REAL NOT NULL, monthly REAL NOT NULL);
CREATE TABLE IF NOT EXISTS wallet_sites (site TEXT PRIMARY KEY, first_at REAL NOT NULL, payments INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS wallet_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, site TEXT NOT NULL, host TEXT NOT NULL,
    summary TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, month TEXT NOT NULL,
    card_id TEXT NOT NULL DEFAULT '', card_label TEXT NOT NULL DEFAULT '', seen INTEGER NOT NULL DEFAULT 0);
"""


class WalletError(Exception):
    """A refusal whose message is safe for the app and the model (it never contains a card number). `status` is the HTTP code."""

    def __init__(self, message: str, status: int = 422, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code   # a name the apps read ({"code": ...} in the answer), e.g. "keyguard_required"


# ---------------------------------------------------------------- card checks
def digits(value: Any) -> str:
    """The digits of a card number as typed (spaces and dashes allowed). Anything else is refused."""
    text = value if isinstance(value, str) else ""
    if not text or re.search(r"[^\d \-]", text):
        raise WalletError("the card number must be digits (spaces or dashes allowed)")
    return re.sub(r"\D", "", text)


def luhn_ok(number: str) -> bool:
    if not number.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def brand_of(number: str) -> str:
    n = number
    if n.startswith("4"):
        return "visa"
    if n[:2] in ("34", "37"):
        return "amex"
    if 51 <= int(n[:2] or 0) <= 55 or 2221 <= int(n[:4] or 0) <= 2720:
        return "mastercard"
    if 3528 <= int(n[:4] or 0) <= 3589:
        return "jcb"
    if n.startswith(("6011", "65")) or 644 <= int(n[:3] or 0) <= 649:
        return "discover"
    if n.startswith("62"):
        return "unionpay"
    if n.startswith(("36", "38", "39")) or 300 <= int(n[:3] or 0) <= 305:
        return "diners"
    return "other"


def check_number(value: Any) -> str:
    number = digits(value)
    if not 12 <= len(number) <= 19:
        raise WalletError("the card number must have 12 to 19 digits")
    if not luhn_ok(number):
        raise WalletError("the card number is not valid (check digit); check it and enter it again")
    return number


def check_cvc(value: Any) -> str:
    cvc = value if isinstance(value, str) else ""
    if not re.fullmatch(r"\d{3,4}", cvc):
        raise WalletError("the CVC must be 3 or 4 digits")
    return cvc


def check_expiry(month: Any, year: Any, now: datetime | None = None) -> tuple[int, int]:
    """(month, four-digit year) of a card that has not expired yet (a card is good through the last day of its month)."""
    try:
        m, y = int(month), int(year)
    except (TypeError, ValueError):
        raise WalletError("the expiry month and year must be numbers") from None
    if isinstance(month, bool) or isinstance(year, bool) or not 1 <= m <= 12:
        raise WalletError("the expiry month must be 1 to 12")
    if 0 <= y < 100:
        y += 2000
    now = now or datetime.now(timezone.utc)
    if (y, m) < (now.year, now.month):
        raise WalletError("this card has expired")
    if y > now.year + 25:
        raise WalletError("the expiry year is too far away")
    return m, y


def expired(month: int, year: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return (year, month) < (now.year, now.month)


def check_last4(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    if not re.fullmatch(r"\d{4}", text):
        raise WalletError("last4 must be the last 4 digits of the card")
    return text


def _text(value: Any, limit: int, what: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit or re.search(r"[\x00-\x1f]", value):
        raise WalletError(f"{what} must be text of at most {limit} characters")
    if re.search(r"(?:\d[ -]?){12,}", value):
        raise WalletError(f"{what} must not contain a card number")
    return value.strip()


def default_label(brand: str, last4: str) -> str:
    return f"{brand.capitalize() if brand != 'other' else t('Card')} ••{last4}"


@dataclass(slots=True)
class CardSecret:
    """A card number and CVC in memory, on their way into a checkout page. Never printed: repr/str show only the last 4."""
    number: str
    cvc: str

    def __repr__(self) -> str:
        return f"CardSecret(••{self.number[-4:]})"

    __str__ = __repr__

    def wipe(self) -> None:
        self.number = self.cvc = ""


# ---------------------------------------------------------------- sites
def site_key(host: str) -> str:
    """The registrable domain of a host ('pay.shop.example.co.kr' -> 'example.co.kr'): what "a site" means for the first-payment rule,
    payment PINs and conditional tasks. One site rule for all of Kelo: the Public Suffix List (tools/sites.registrable)."""
    return registrable(host)


# The site rule before 2026-11-10 (a heuristic without the list). Rows saved then (known payment sites, PIN sites, payment ways,
# task permissions) were keyed with it; for a few hosts it differs from the list (foo.blogspot.com, a.b.city.kawasaki.jp, ...).
# Lookups accept both keys (site_keys), and migrate_site_keys adds the new key next to the old one where the host is known, so
# nothing the owner approved stops matching. The old rows are kept until checked (SITE_RULE_META says when the copy moved).
_LEGACY_SHARED = {"github.io", "pages.dev", "vercel.app", "netlify.app", "herokuapp.com", "myshopify.com", "web.app", "firebaseapp.com",
                  "appspot.com", "blogspot.com", "azurewebsites.net", "cloudfront.net", "wixsite.com", "square.site", "carrd.co"}
_LEGACY_SECOND_LEVEL = {"co", "com", "net", "org", "or", "ne", "go", "ac", "gov", "edu", "re", "pe", "gr", "mil", "nom", "ltd", "plc"}
SITE_RULE_META = "site_rule"          # "psl" once migrate_site_keys ran (with the time and counts in site_rule_migration)


def legacy_site_key(host: str) -> str:
    """The pre-2026-11-10 site rule, only to match rows saved with it."""
    h = (host or "").lower().strip().strip(".").removeprefix("www.")
    if not h or re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", h):
        return h
    parts = h.split(".")
    if len(parts) <= 2:
        return h
    if ".".join(parts[-2:]) in _LEGACY_SHARED:
        return ".".join(parts[-3:])
    if len(parts[-1]) == 2 and parts[-2] in _LEGACY_SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def site_keys(host: str) -> list[str]:
    """The keys a stored row of this host's site may have: the list's (first), and the old rule's when it differs."""
    new = site_key(host)
    old = legacy_site_key((host or "").split(":", 1)[0] if host and host.count(":") == 1 else host)
    return [new] if not old or old == new else [new, old]


def migrate_site_keys(store: Any, clock: Any = time.time) -> dict[str, int]:
    """Once per database: every known payment site gets its key under the list's rule next to the old one, from the hosts of past
    payments (wallet_payments.host), so first-payment, auto-pay and PIN rules keep matching. Old keys stay (lookups accept both)."""
    if store.meta(SITE_RULE_META) == "psl":
        return {}
    added = 0
    hosts = [r["host"] for r in store._all("SELECT DISTINCT host FROM wallet_payments WHERE host != ''")]
    for host in hosts:
        keys = site_keys(host)
        if len(keys) < 2:
            continue
        new, old = keys
        row = store._all("SELECT first_at, payments FROM wallet_sites WHERE site = ?", (old,))
        if row and not store._all("SELECT 1 FROM wallet_sites WHERE site = ?", (new,)):
            store._run("INSERT INTO wallet_sites(site, first_at, payments) VALUES(?,?,?)", (new, row[0]["first_at"], row[0]["payments"]))
            added += 1
    old_keys = sum(1 for r in store._all("SELECT site FROM wallet_sites") if site_key(r["site"]) != r["site"])
    store.set_meta("site_rule_migration", json.dumps({"at": clock(), "added": added, "hosts": len(hosts), "old_only_keys": old_keys}))
    store.set_meta(SITE_RULE_META, "psl")
    return {"added": added, "hosts": len(hosts)}


@dataclass(slots=True)
class Decision:
    auto: bool
    reason: str

    @property
    def text(self) -> str:
        return t(REASONS[self.reason]) if self.reason in REASONS else self.reason


def money_text(amount: float, currency: str) -> str:
    cur = (currency or "KRW").upper()
    # ISO 4217 minor units, as Java's Currency.getDefaultFractionDigits gives them in the app: 0 for these, 2 for the rest
    whole = cur in {"KRW", "JPY", "VND", "CLP", "ISK", "UGX", "PYG", "XAF", "XOF", "XPF", "KMF", "RWF", "GNF", "BIF", "DJF", "VUV"}
    num = f"{amount:,.0f}" if whole else f"{amount:,.2f}"
    return t("{num} KRW", num=num) if cur == "KRW" else f"{num} {cur}"


class Wallet:
    def __init__(self, store: Any, clock: Any = time.time):
        self.store, self._clock = store, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            pay_cols = {r[1] for r in store._db.execute("PRAGMA table_info(wallet_payments)")}
            if "grant_id" not in pay_cols:
                # a purchase a task made under the permission the user gave it at setup (schedule_grants.py)
                store._db.execute("ALTER TABLE wallet_payments ADD COLUMN grant_id INTEGER")
            if "task_id" not in pay_cols:
                # the chat turn or job the payment was made in (activity task id, and the turn's source: chat, schedule:<id>, task:...)
                store._db.execute("ALTER TABLE wallet_payments ADD COLUMN task_id INTEGER")
                store._db.execute("ALTER TABLE wallet_payments ADD COLUMN source TEXT NOT NULL DEFAULT ''")
            if "is_default" not in {r[1] for r in store._db.execute("PRAGMA table_info(wallet_cards)")}:
                # the card the agent pays with unless the user or the task names another; the first card saved is the default
                store._db.execute("ALTER TABLE wallet_cards ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0")
                store._db.execute("UPDATE wallet_cards SET is_default = 1 WHERE id = (SELECT id FROM wallet_cards ORDER BY created_at, id LIMIT 1)")
            store._db.commit()
        migrate_site_keys(store, clock)   # once: the Public Suffix List's site keys next to the old rule's

    # ------------------------------------------------------------ cards
    @staticmethod
    def _public(r: dict[str, Any]) -> dict[str, Any]:
        server = bool(r["sealed"])
        e2e = bool(r["e2e_blob"])
        return {"id": r["id"], "label": r["label"], "brand": r["brand"], "last4": r["last4"], "exp_month": r["exp_month"],
                "exp_year": r["exp_year"], "holder": r["holder"], "postal_code": r["postal_code"], "created_at": r["created_at"],
                "auto_pay": bool(r["auto_pay"]) and server, "server_copy": server, "e2e": e2e, "e2e_alg": r["e2e_alg"],
                "e2e_key_id": r["e2e_key_id"], "usable": (server or e2e) and not expired(r["exp_month"], r["exp_year"]),
                "expired": expired(r["exp_month"], r["exp_year"]), "last_used": r["last_used"], "default": bool(r.get("is_default"))}

    def _row(self, card_id: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM wallet_cards WHERE id = ?", (card_id,))
        return rows[0] if rows else None

    def cards(self) -> list[dict[str, Any]]:
        return [self._with_state(self._public(r)) for r in self.store._all("SELECT * FROM wallet_cards ORDER BY created_at")]

    def default_card(self) -> dict[str, Any] | None:
        """The card the agent pays with unless the user or the task names another (None without cards)."""
        rows = self.store._all("SELECT id FROM wallet_cards WHERE is_default = 1 LIMIT 1")
        return self.card(rows[0]["id"]) if rows else None

    def _make_default(self, card_id: str) -> None:
        self.store._run("UPDATE wallet_cards SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (card_id,))

    def card(self, card_id: str) -> dict[str, Any] | None:
        r = self._row(card_id)
        return self._with_state(self._public(r)) if r else None

    def card_state(self, card: dict[str, Any]) -> Decision:
        """Whether this card pays automatically at all right now, before any amount or site is known: "auto", or the card-wide reason
        it asks (not_auto, no_server_copy, paused, vault_locked). Limits and the first payment on a new site may still ask."""
        if not card.get("auto_pay"):
            return Decision(False, "not_auto")
        if not card.get("server_copy"):
            return Decision(False, "no_server_copy")
        if not self.keyguard_on():   # the owner's rule: auto-pay only with Keyguard (the sealed copy is kept for when it is on)
            return Decision(False, "keyguard_off")
        if self.paused():
            return Decision(False, "paused")
        if self.store.vault_locked():
            return Decision(False, "vault_locked")
        return Decision(True, "auto")

    def _with_state(self, card: dict[str, Any]) -> dict[str, Any]:
        d = self.card_state(card)
        held = bool(card.get("auto_pay") and card.get("server_copy")) and not self.keyguard_on()
        return {**card, "pays_automatically": d.auto, "ask_reason": "" if d.auto else d.reason, "ask_reason_text": "" if d.auto else d.text,
                "auto_pay_paused_reason": "keyguard_off" if held else ""}

    def keyguard_on(self) -> bool:
        """Auto-pay is allowed only while Keyguard holds the key on the phones (keyguard.py, mode "phone")."""
        guard = getattr(self.store, "keyguard", None)
        return guard is not None and guard.mode == "phone"

    def require_keyguard(self) -> None:
        if not self.keyguard_on():
            raise WalletError("auto-pay needs Keyguard: turn it on in My, Security, Keyguard first (the phones then hold the key of "
                              "passwords, keys and cards)", 409, "keyguard_required")

    def auto_pay_paused(self) -> int:
        """How many auto-pay cards wait for Keyguard right now (0 while it is on)."""
        if self.keyguard_on():
            return 0
        return len(self.store._all("SELECT id FROM wallet_cards WHERE auto_pay = 1 AND sealed IS NOT NULL"))

    def blob(self, card_id: str) -> dict[str, str] | None:
        """The phone's own ciphertext of an end-to-end card (the server cannot read it; only the phone's key opens it)."""
        r = self._row(card_id)
        return {"blob": r["e2e_blob"], "alg": r["e2e_alg"], "key_id": r["e2e_key_id"]} if r and r["e2e_blob"] else None

    def find(self, ref: Any) -> dict[str, Any] | None:
        """A card by id, by label (any case), or by its last 4 digits ('1234', '••1234', 'visa 1234') when only one card has them."""
        text = str(ref or "").strip()
        if not text:
            return None
        cards = self.cards()
        for c in cards:
            if c["id"] == text:
                return c
        low = text.casefold()
        same = [c for c in cards if c["label"].casefold() == low]
        if len(same) == 1:
            return same[0]
        tail = re.findall(r"\d{4}", text)
        if tail:
            hits = [c for c in cards if c["last4"] == tail[-1]]
            if len(hits) > 1:
                words = re.sub(r"[\d•*·.\-]", " ", low).split()
                hits = [c for c in hits if all(w in c["label"].casefold() or w == c["brand"] for w in words)]
            if len(hits) == 1:
                return hits[0]
        part = [c for c in cards if low in c["label"].casefold()]
        return part[0] if len(part) == 1 else None

    def _aad(self, card_id: str, last4: str) -> str:
        return json.dumps([card_id, "card", last4], separators=(",", ":"))

    def _seal(self, card_id: str, last4: str, number: str, cvc: str) -> str:
        return self.store.card_sealer.seal(json.dumps({"n": number, "c": cvc}), self._aad(card_id, last4))

    @staticmethod
    def _e2e(value: Any) -> tuple[str, str, str]:
        if not isinstance(value, dict):
            raise WalletError("e2e must be an object: {blob, alg, key_id}")
        blob, alg, key_id = value.get("blob"), value.get("alg", ""), value.get("key_id", "")
        if not isinstance(blob, str) or not blob or len(blob) > MAX_BLOB or not re.fullmatch(r"[A-Za-z0-9+/=_\-.:]+", blob):
            raise WalletError(f"e2e.blob must be the phone's ciphertext as base64 text (at most {MAX_BLOB} characters)")
        if not isinstance(alg, str) or len(alg) > 40 or not isinstance(key_id, str) or len(key_id) > 120:
            raise WalletError("e2e.alg (at most 40 characters) and e2e.key_id (at most 120) must be text")
        return blob, alg, key_id

    def add_card(self, body: dict[str, Any]) -> dict[str, Any]:
        """A new card from the app. auto_pay=true: `number` and `cvc` (sealed here). auto_pay=false: `e2e` (the phone's ciphertext)
        and `last4` (+ `brand`); the server never sees the number, so the check digit is verified when the phone sends it for a payment."""
        auto = body.get("auto_pay") is True
        m, y = check_expiry(body.get("exp_month"), body.get("exp_year"))
        holder = _text(body.get("holder"), 80, "holder")
        postal = _text(body.get("postal_code"), 16, "postal_code")
        card_id = "card_" + secrets.token_hex(8)
        sealed = blob = None
        alg = key_id = ""
        if auto:
            self.require_keyguard()
            number = check_number(body.get("number"))
            cvc = check_cvc(body.get("cvc"))
            last4, brand = number[-4:], brand_of(number)
            if body.get("last4") not in (None, last4):
                raise WalletError("last4 does not match the card number")
            sealed = self._seal(card_id, last4, number, cvc)
            if body.get("e2e") is not None:      # the phone may keep its own copy too (used again if auto-pay is turned off)
                blob, alg, key_id = self._e2e(body["e2e"])
        else:
            if body.get("number") is not None or body.get("cvc") is not None:
                raise WalletError("a card without auto-pay is end-to-end: send only the phone's ciphertext (e2e), never the number or CVC")
            last4 = check_last4(body.get("last4"))
            brand = str(body.get("brand") or "other").lower()
            if brand not in ("visa", "mastercard", "amex", "jcb", "discover", "unionpay", "diners", "other"):
                brand = "other"
            blob, alg, key_id = self._e2e(body.get("e2e"))
        label = _text(body.get("label"), 60, "label") or default_label(brand, last4)
        first = not self.store._all("SELECT 1 FROM wallet_cards LIMIT 1")
        self.store._run("INSERT INTO wallet_cards(id,label,brand,last4,exp_month,exp_year,holder,postal_code,created_at,auto_pay,sealed,"
                        "e2e_blob,e2e_alg,e2e_key_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (card_id, label, brand, last4, m, y, holder, postal, self._clock(), int(auto), sealed, blob, alg, key_id))
        if first or body.get("default") is True:     # the first card saved is the default; a new one only when asked
            self._make_default(card_id)
        self.store.add_vault_log(label, "card-added", "auto-pay on (server copy)" if auto else "end-to-end (phone key only)")
        return self.card(card_id) or {}

    def update_card(self, card_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Change a card. auto_pay=true needs `number` + `cvc` (they must match the card's last 4). auto_pay=false wipes the server's
        readable copy at once; the card then pays through the phone with its end-to-end blob (a new one in `e2e`, or the one kept),
        or, with neither, keeps only its details until the phone uploads a blob."""
        r = self._row(card_id)
        if not r:
            raise WalletError("no such card", 404)
        sets: dict[str, Any] = {}
        if "label" in body:
            sets["label"] = _text(body.get("label"), 60, "label") or default_label(r["brand"], r["last4"])
        if "holder" in body:
            sets["holder"] = _text(body.get("holder"), 80, "holder")
        if "postal_code" in body:
            sets["postal_code"] = _text(body.get("postal_code"), 16, "postal_code")
        if "exp_month" in body or "exp_year" in body:
            sets["exp_month"], sets["exp_year"] = check_expiry(body.get("exp_month", r["exp_month"]), body.get("exp_year", r["exp_year"]))
        if body.get("e2e") is not None:
            sets["e2e_blob"], sets["e2e_alg"], sets["e2e_key_id"] = self._e2e(body["e2e"])
        want = body.get("auto_pay") if isinstance(body.get("auto_pay"), bool) else None
        if "auto_pay" in body and want is None:
            raise WalletError("auto_pay must be true or false")
        sending = "number" in body or "cvc" in body
        if sending and want is not True and not (want is None and r["auto_pay"]):
            raise WalletError("the number and CVC are sent only for auto-pay; an end-to-end card sends its ciphertext (e2e)")
        if want is True or (want is None and sending):
            self.require_keyguard()
            if sending or not r["sealed"]:
                number = check_number(body.get("number"))
                cvc = check_cvc(body.get("cvc"))
                if number[-4:] != r["last4"]:
                    raise WalletError("that number is not this card (the last 4 digits differ)")
                sets["sealed"] = self._seal(card_id, r["last4"], number, cvc)
            sets["auto_pay"] = 1
        elif want is False:
            sets["auto_pay"], sets["sealed"] = 0, None
        if "default" in body:
            if body.get("default") is not True:
                raise WalletError("a card stops being the default only when another card is made the default ({\"default\": true} on it)")
            if not r.get("is_default"):
                self._make_default(card_id)
                self.store.add_vault_log(sets.get("label", r["label"]), "card-default", "made the default card")
        if not sets:
            return self.card(card_id) or {}
        self.store._run(f"UPDATE wallet_cards SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), card_id))
        label = sets.get("label", r["label"])
        if "auto_pay" in sets and bool(sets["auto_pay"]) != bool(r["auto_pay"]):
            self.store.add_vault_log(label, "card-setting", "auto-pay on (server copy)" if sets["auto_pay"] else "auto-pay off: server copy wiped")
        elif sets.keys() - {"label", "holder", "postal_code"}:
            self.store.add_vault_log(label, "card-replaced", "card details updated")
        return self.card(card_id) or {}

    def delete_card(self, card_id: str, new_default: str | None = None) -> bool:
        """Remove a card. Removing the default card with more than one card left needs `new_default` (which card takes its place);
        with one card left, that one becomes the default."""
        r = self._row(card_id)
        if not r:
            return False
        nxt = None
        if r.get("is_default"):
            rest = [x["id"] for x in self.store._all("SELECT id FROM wallet_cards WHERE id != ? ORDER BY created_at, id", (card_id,))]
            if len(rest) == 1:
                nxt = rest[0]
            elif rest:
                if not new_default or new_default not in rest:
                    raise WalletError("this is the default card: say which card becomes the new default (new_default)", 409)
                nxt = new_default
        # overwrite first, then delete (the database also runs with secure_delete, so the old pages are zeroed)
        self.store._run("UPDATE wallet_cards SET sealed = NULL, e2e_blob = NULL WHERE id = ?", (card_id,))
        self.store._run("DELETE FROM wallet_cards WHERE id = ?", (card_id,))
        self.store.add_vault_log(r["label"], "card-deleted", "removed by the user")
        if nxt:
            self._make_default(nxt)
        return True

    def open_secret(self, card_id: str) -> CardSecret | None:
        """The sealed (auto-pay) copy of a card, opened for one fill. None when there is none or it no longer opens."""
        r = self._row(card_id)
        if not r or not r["sealed"]:
            return None
        plain = self.store.card_sealer.open(r["sealed"], self._aad(card_id, r["last4"]))
        if plain is None:
            return None
        try:
            data = json.loads(plain)
            return CardSecret(str(data["n"]), str(data["c"]))
        except (ValueError, KeyError):
            return None

    def touch(self, card_id: str) -> None:
        self.store._run("UPDATE wallet_cards SET last_used = ? WHERE id = ?", (self._clock(), card_id))

    # ------------------------------------------------------------ rules
    def paused(self) -> bool:
        return self.store.meta("wallet_paused") == "1"

    def set_paused(self, paused: bool) -> None:
        self.store.set_meta("wallet_paused", "1" if paused else "")
        self.store.add_vault_log("wallet", "auto-pay-paused" if paused else "auto-pay-resumed", "by the user")

    def _overrides(self) -> dict[str, tuple[float, float]]:
        return {r["currency"]: (r["per_payment"], r["monthly"]) for r in self.store._all("SELECT * FROM wallet_limits")}

    def limit_for(self, currency: str) -> tuple[float, float] | None:
        cur = (currency or "").upper()
        return self._overrides().get(cur) or DEFAULT_LIMITS.get(cur)

    def limits(self) -> list[dict[str, Any]]:
        over = self._overrides()
        month = self.month_key()
        out = []
        for cur in [*DEFAULT_LIMITS, *sorted(c for c in over if c not in DEFAULT_LIMITS)]:
            per, monthly = over.get(cur) or DEFAULT_LIMITS[cur]
            d = DEFAULT_LIMITS.get(cur)
            out.append({"currency": cur, "per_payment": per, "monthly": monthly, "default_per_payment": d[0] if d else None,
                        "default_monthly": d[1] if d else None, "custom": cur in over, "spent_this_month": self.spent(cur, month)})
        return out

    def set_limit(self, currency: str, per_payment: Any, monthly: Any) -> dict[str, Any]:
        cur = (currency or "").upper()
        if not CURRENCY.fullmatch(cur):
            raise WalletError("currency must be a 3-letter code such as KRW or USD")
        try:
            per, mon = float(per_payment), float(monthly)
        except (TypeError, ValueError):
            raise WalletError("per_payment and monthly must be numbers") from None
        if isinstance(per_payment, bool) or isinstance(monthly, bool) or not (0 <= per <= 1e12 and 0 <= mon <= 1e12):
            raise WalletError("limits must be 0 or more (0 = never pay automatically in this currency)")
        if per > mon:
            raise WalletError("the per-payment limit cannot be higher than the monthly limit")
        self.store._run("INSERT INTO wallet_limits(currency, per_payment, monthly) VALUES(?,?,?) ON CONFLICT(currency) DO UPDATE SET "
                        "per_payment = excluded.per_payment, monthly = excluded.monthly", (cur, per, mon))
        self.store.add_vault_log("wallet", "limit", f"{cur}: {money_text(per, cur)} per payment, {money_text(mon, cur)} per month")
        return next(x for x in self.limits() if x["currency"] == cur)

    def reset_limit(self, currency: str) -> bool:
        cur = (currency or "").upper()
        gone = self.store._run("DELETE FROM wallet_limits WHERE currency = ?", (cur,)).rowcount > 0
        if gone:
            self.store.add_vault_log("wallet", "limit", f"{cur}: back to the default" if cur in DEFAULT_LIMITS else f"{cur}: removed (never automatic)")
        return gone

    def timezone(self) -> str:
        name = (self.store.profile().get("timezone") or "").strip()
        try:
            ZoneInfo(name)
            return name or "UTC"
        except Exception:  # noqa: BLE001
            return "UTC"

    def month_key(self, ts: float | None = None) -> str:
        """The calendar month (YYYY-MM) of `ts` in the user's time zone (UTC when the server does not know it)."""
        return datetime.fromtimestamp(self._clock() if ts is None else ts, ZoneInfo(self.timezone())).strftime("%Y-%m")

    def spent(self, currency: str, month: str | None = None) -> float:
        """What was paid AUTOMATICALLY in `currency` this month (payments the user approved on the phone do not count)."""
        rows = self.store._all("SELECT COALESCE(SUM(amount), 0) AS s FROM wallet_payments WHERE kind = 'auto' AND grant_id IS NULL "
                               "AND currency = ? AND month = ?",
                               ((currency or "").upper(), month or self.month_key()))
        return float(rows[0]["s"])

    def known_site(self, host: str) -> bool:
        keys = site_keys(host)
        return bool(self.store._all(f"SELECT 1 FROM wallet_sites WHERE site IN ({','.join('?' * len(keys))})", tuple(keys)))

    def mark_site(self, host: str) -> None:
        self.store._run("INSERT INTO wallet_sites(site, first_at, payments) VALUES(?,?,1) ON CONFLICT(site) DO UPDATE SET payments = payments + 1",
                        (site_key(host), self._clock()))

    def decide(self, card: dict[str, Any] | None, host: str, amount: float, currency: str) -> Decision:
        """Whether this payment may go through without asking the phone, and if not, why. The first payment on a new site always asks
        here: an explicit grant (a task's or a schedule's purchase permission, checked by the caller before) is the only way past it;
        the owner's words are never read for it (docs/no-word-lists.md, 2026-11-16)."""
        cur = (currency or "KRW").upper()
        if not card:
            return Decision(False, "not_auto")
        state = self.card_state(card)
        if not state.auto:
            return state
        limit = self.limit_for(cur)
        if not limit:
            return Decision(False, "currency")
        per, monthly = limit
        if amount > per or per <= 0:
            return Decision(False, "over_per_payment")
        if self.spent(cur) + amount > monthly:
            return Decision(False, "over_monthly")
        if not self.known_site(host):
            return Decision(False, "first_site")
        return Decision(True, "auto")

    # ------------------------------------------------------------ history and notifications
    def record(self, kind: str, card: dict[str, Any] | None, host: str, summary: str, amount: float, currency: str,
               grant_id: int | None = None) -> dict[str, Any]:
        """One payment that went ahead: kind 'auto' (paid by the rules, or by a task's standing permission `grant_id`; also a
        notification) or 'approved' (the user allowed it). A task's purchases count against its own cap, not the wallet's limits."""
        now = self._clock()
        cur = (currency or "KRW").upper()
        from .workers import current_turn   # the chat turn or job this payment belongs to (for the card's history)
        turn = current_turn.get() or {}
        task = turn.get("task_id")
        pid = self.store._run("INSERT INTO wallet_payments(ts, kind, site, host, summary, amount, currency, month, card_id, card_label, seen, "
                              "grant_id, task_id, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (now, kind, site_key(host), host, summary[:400], amount, cur, self.month_key(now),
                               card["id"] if card else "", card["label"] if card else "", 0 if kind == "auto" else 1, grant_id,
                               task if isinstance(task, int) else None, str(turn.get("source") or "")[:80])).lastrowid
        self.mark_site(host)
        if card:
            self.touch(card["id"])
        what = ("bought by a task the user allowed at setup" if grant_id else "paid automatically") if kind == "auto" else "payment approved on the phone"
        self.store.add_vault_log(card["label"] if card else "wallet", "auto-paid" if kind == "auto" else "payment",
                                 f"{what}: {money_text(amount, cur)} on {site_key(host)}")
        return self.payment(int(pid or 0)) or {}

    @staticmethod
    def _payment(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["id"], "at": r["ts"], "kind": r["kind"], "site": r["site"], "host": r["host"], "summary": r["summary"],
                "amount": r["amount"], "currency": r["currency"], "amount_text": money_text(r["amount"], r["currency"]),
                "card_id": r["card_id"], "card_label": r["card_label"], "month": r["month"], "seen": bool(r["seen"]),
                "grant_id": r.get("grant_id"), "task_id": r.get("task_id"), "source": r.get("source") or ""}

    def payment(self, pid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM wallet_payments WHERE id = ?", (pid,))
        return self._payment(rows[0]) if rows else None

    def payments(self, limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
        return [self._payment(r) for r in self.store._all(
            "SELECT * FROM wallet_payments WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?", (before_id, before_id, max(1, min(limit, 500))))]

    def card_payments(self, card_id: str, limit: int = 30, before_id: int | None = None) -> dict[str, Any]:
        """The payments made with one card, newest first, a page at a time: {"items", "next_before_id" (null on the last page)}."""
        n = max(1, min(limit, 100))
        rows = self.store._all("SELECT * FROM wallet_payments WHERE card_id = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                               (card_id, before_id, before_id, n + 1))
        items = [self._payment(r) for r in rows[:n]]
        return {"items": items, "next_before_id": items[-1]["id"] if len(rows) > n else None}

    def notifications(self, after: int = 0, limit: int = 50) -> dict[str, Any]:
        rows = self.store._all("SELECT * FROM wallet_payments WHERE kind = 'auto' AND id > ? ORDER BY id DESC LIMIT ?", (after, max(1, min(limit, 200))))
        unseen = self.store._all("SELECT COUNT(*) AS n FROM wallet_payments WHERE kind = 'auto' AND seen = 0")[0]["n"]
        return {"items": [self._payment(r) for r in rows], "unseen": unseen}

    def mark_seen(self, up_to: int) -> int:
        return self.store._run("UPDATE wallet_payments SET seen = 1 WHERE kind = 'auto' AND id <= ? AND seen = 0", (up_to,)).rowcount


# ---------------------------------------------------------------- one-time card delivery (end-to-end cards)
@dataclass
class _Slot:
    card: dict[str, Any]
    info: dict[str, Any]
    expires: float
    secret: CardSecret | None = None
    arrived: asyncio.Event = field(default_factory=asyncio.Event)


class CardDeliveries:
    """Where the phone puts an end-to-end card for ONE approved payment. A slot exists only for an approval that asked for such a card;
    it takes one delivery, is emptied by the payment that waits for it, and is gone after `ttl` seconds or when the reply ends.
    Nothing here touches the disk."""

    def __init__(self, ttl: float = CARD_TTL, clock: Any = time.monotonic):
        self.ttl, self._clock = ttl, clock
        self._slots: dict[str, _Slot] = {}

    def _sweep(self) -> None:
        now = self._clock()
        for k in [k for k, s in self._slots.items() if s.expires < now]:
            self.discard(k)

    def expect(self, approval_id: str, card: dict[str, Any], info: dict[str, Any], wait: float | None = None) -> None:
        self._sweep()
        self._slots[approval_id] = _Slot(card, info, self._clock() + (self.ttl if wait is None else wait))

    def started(self, approval_id: str) -> float | None:
        """The user allowed the payment: the phone now has `ttl` seconds to send the card. Returns the wall-clock deadline."""
        slot = self._slots.get(approval_id)
        if slot is None:
            return None
        slot.expires = self._clock() + self.ttl
        return time.time() + self.ttl

    def has(self, approval_id: str) -> bool:
        self._sweep()
        return approval_id in self._slots

    def pending(self) -> list[dict[str, Any]]:
        self._sweep()
        now = self._clock()
        return [{"approval_id": k, "card": {f: s.card[f] for f in ("id", "label", "brand", "last4")}, **s.info,
                 "expires_in": max(0, round(s.expires - now))} for k, s in self._slots.items() if s.secret is None]

    def deliver(self, approval_id: str, body: dict[str, Any]) -> None:
        self._sweep()
        slot = self._slots.get(approval_id)
        if slot is None:
            raise WalletError("no card is waiting for that approval (it was used, denied, or timed out)", 404)
        if slot.secret is not None:
            raise WalletError("the card for that approval was already sent", 409)
        if body.get("card_id") not in (None, slot.card["id"]):
            raise WalletError("that approval is for another card", 422)
        number = check_number(body.get("number"))
        cvc = check_cvc(body.get("cvc"))
        if number[-4:] != slot.card["last4"]:
            raise WalletError("that number is not the approved card (the last 4 digits differ)", 422)
        slot.secret = CardSecret(number, cvc)
        slot.expires = self._clock() + self.ttl
        slot.arrived.set()

    async def wait(self, approval_id: str, timeout: float | None = None) -> CardSecret | None:
        """The delivered card, taken out of the slot (single use); None when it does not come in time."""
        slot = self._slots.get(approval_id)
        if slot is None:
            return None
        try:
            await asyncio.wait_for(slot.arrived.wait(), self.ttl if timeout is None else timeout)
        except asyncio.TimeoutError:
            self.discard(approval_id)
            return None
        secret = slot.secret if slot.secret is not None and slot.secret.number else None
        self._slots.pop(approval_id, None)
        return secret

    def discard(self, approval_id: str) -> None:
        slot = self._slots.pop(approval_id, None)
        if slot and slot.secret:
            slot.secret.wipe()
        if slot:
            slot.arrived.set()   # a payment still waiting wakes up and finds nothing
