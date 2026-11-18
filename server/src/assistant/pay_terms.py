"""Payments v2, phase P1 (docs/payments-v2.md): order terms and payment approvals.

Two separate things: the ORDER CHECK (does the cart match what the owner asked) and the PAYMENT APPROVAL (the owner's money: a card
answer, a grant, an auto-pay rule). Everything that decides here is a pure function of numbers, ids and digests, so it is tested
without a browser; the one class with SQL (`PayLedger`) only keeps rows. No word of any language decides anything.

Rules (design rules 2, 3, 5):
- a payment approval covers the same checkout while the origin (scheme + exact host) and the currency are the same and the total and
  any conditional charge stay within the approved amounts; anything higher, another currency or another origin needs a new approval;
- a change of items, variants, quantities, delivery, dates, address, cancellation terms or other obligations needs a new order check
  (the terms digest differs); a change of only the total after a passed check does not.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

APPROVAL_SECONDS = 20 * 60          # a card approval lives this long (checkout.REVIEW_SECONDS)
ZERO_DECIMAL = frozenset({"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF"})
THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})
CURRENCY = re.compile(r"^[A-Z]{3}$")
MAX_ITEMS = 40

# the refusal codes of the re-check at the press (purchase.py gives each its own message)
ORIGIN, AMOUNT, CURRENCY_CHANGED, CONDITIONAL, EXPIRED, STATE, SUPERSEDED, DIGEST, PREAPPROVAL = (
    "origin", "amount", "currency", "conditional", "expired", "state", "superseded", "digest", "preapproval")


def exponent(currency: str) -> int:
    c = (currency or "").upper()
    return 0 if c in ZERO_DECIMAL else 3 if c in THREE_DECIMAL else 2


def minor_units(value: Any, currency: str) -> int | None:
    """An amount (a number the model read from the page) in the currency's minor units, None when it is not a usable amount."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.]", "", value)
        if not cleaned:
            return None
        try:
            value = float(cleaned)
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or value < 0 or value != value:
        return None
    return int(round(float(value) * 10 ** exponent(currency)))


def iso_currency(value: Any) -> str:
    """The ISO 4217 code as given (3 letters), "" otherwise. Never guessed from a symbol or a country."""
    code = str(value or "").strip().upper()
    return code if CURRENCY.match(code) else ""


def _text(v: Any, n: int = 200) -> str:
    return " ".join(str(v or "").split())[:n]


def _list(v: Any, n: int = 12) -> list[str]:
    return [_text(x, 120) for x in (v if isinstance(v, list) else []) if _text(x, 120)][:n]


def digest_of(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass
class Terms:
    merchant_origin: str
    merchant_name: str
    items: list[dict[str, Any]]
    obligations: dict[str, Any]
    currency: str
    total_minor: int
    conditional_minor: int = 0
    conditional_terms: str = ""
    payment_label: str = ""
    ship_to_shown: str = ""          # shown on the card, never stored (only its digest is in `obligations`)
    id: str = ""
    owner: str = ""
    task: str = ""
    status: str = "draft"
    checked_at: float | None = None
    created_at: float = 0.0
    digest: str = field(default="")

    def __post_init__(self) -> None:
        if not self.digest:
            self.digest = terms_digest(self.items, self.obligations)


def terms_digest(items: list[dict[str, Any]], obligations: dict[str, Any]) -> str:
    """sha256 over the order's items and obligations, in a fixed order. Prices are not part of it (a price change alone is a total
    change); quantities, variants and every obligation are. An address is already a digest in `obligations`."""
    lines = sorted(({"name": i["name"].lower(), "qty": i["qty"], "variants": sorted(v.lower() for v in i["variants"]),
                     "obligations": sorted(o.lower() for o in i["obligations"])} for i in items),
                   key=lambda d: json.dumps(d, sort_keys=True))
    ob = {k: (sorted(x.lower() for x in v) if isinstance(v, list) else str(v).lower()) for k, v in obligations.items()}
    return digest_of({"items": lines, "obligations": ob})


def terms_from_args(args: dict[str, Any], origin: str) -> tuple[Terms | None, str]:
    """checkout_review's arguments -> (Terms, "") or (None, what is wrong). `origin` is the page's scheme + exact host."""
    cur = iso_currency(args.get("currency"))
    if not cur:
        return None, "currency is required: the ISO 4217 code of the page's currency (EUR, USD, KRW), never guessed from a symbol"
    total = minor_units(args.get("total"), cur)
    if total is None:
        return None, "total is required: the order's total the page shows, as a number (0 if it is free)"
    raw = args.get("items")
    if not isinstance(raw, list) or not raw:
        return None, "items is required: every line of the order as the page shows it, [{name, qty, unit_price, variants, obligations}]"
    items: list[dict[str, Any]] = []
    for it in raw[:MAX_ITEMS]:
        if not isinstance(it, dict) or not _text(it.get("name")):
            continue
        try:
            qty = max(1, int(it.get("qty", it.get("quantity")) or 1))
        except (TypeError, ValueError):
            qty = 1
        unit = minor_units(it.get("unit_price", it.get("price")), cur)
        items.append({"name": _text(it.get("name"), 120), "qty": qty, "unit_minor": unit or 0, "line_minor": (unit or 0) * qty,
                      "variants": _list(it.get("variants")), "obligations": _list(it.get("obligations"))})
    if not items:
        return None, "items is required: every line needs a name"
    cond = minor_units(args.get("conditional_total"), cur) or 0
    ship = _text(args.get("ship_to"), 300)
    ob = {"delivery": _text(args.get("delivery")), "dates": _text(args.get("dates")), "cancellation": _text(args.get("cancellation")),
          "address_digest": hashlib.sha256(ship.lower().encode("utf-8")).hexdigest() if ship else "",
          "other": _list(args.get("other_obligations"))}
    return Terms(origin, _text(args.get("merchant"), 80), items, ob, cur, total, cond, _text(args.get("conditional_terms"), 300),
                 _text(args.get("payment_method") or args.get("card"), 60), ship), ""


# ---------------------------------------------------------------- order check: which terms need one
def check_needed(latest: dict[str, Any] | None, terms: Terms) -> bool:
    """True when `terms` need a (new) passed order check before they can be made ready: no earlier terms, the earlier ones were never
    confirmed, or the items / obligations differ. A change of only the total or the payment way after a passed check needs none."""
    return (latest is None or not latest.get("checked_at") or latest.get("terms_digest") != terms.digest
            or latest.get("merchant_origin") != terms.merchant_origin)


def parse_verdict(raw: Any) -> dict[str, Any]:
    """The model's answer to the order check, strict: {"verdict": confirm|change|open_choice, "reason", "change"?, "choice"?}.
    Anything else (no object, another verdict word) is open_choice: the check fails closed."""
    if not isinstance(raw, dict) or raw.get("verdict") not in ("confirm", "change", "open_choice"):
        return {"verdict": "open_choice", "reason": "the order check gave no usable answer"}
    out = {"verdict": raw["verdict"], "reason": _text(raw.get("reason"), 400)}
    for k in ("change", "choice"):
        if _text(raw.get(k), 400):
            out[k] = _text(raw.get(k), 400)
    if raw.get("within_spec") is True:   # only an explicit true counts (phase P3: the cart is inside the owner's pre-approved order)
        out["within_spec"] = True
    return out


# ---------------------------------------------------------------- what an approval covers, and the re-check at the press
def refusal_code(approval: dict[str, Any], terms: dict[str, Any] | None, *, origin: str, amount_minor: int | None, currency: str,
                 conditional_minor: int | None, now: float) -> str | None:
    """Why the press must be refused (a code), None when the approval still covers it. `amount_minor` / `conditional_minor`: what the
    live page shows (None = not read, which is not a reason by itself: the page's own total is checked by the caller)."""
    if approval.get("state") != "approved":
        return STATE
    if terms is None or terms.get("status") != "ready" or terms.get("id") != approval.get("terms_id"):
        return SUPERSEDED
    if terms.get("terms_digest") != approval.get("terms_digest"):
        return DIGEST
    if now > float(approval.get("expires_at") or 0):
        return EXPIRED
    if origin != approval.get("merchant_origin"):
        return ORIGIN
    if currency and currency != approval.get("currency"):
        return CURRENCY_CHANGED
    if amount_minor is not None and amount_minor > int(approval.get("approved_total_minor") or 0):
        return AMOUNT
    if conditional_minor is not None and conditional_minor > int(approval.get("approved_conditional_minor") or 0):
        return CONDITIONAL
    return None


def covers_terms(approval: dict[str, Any], terms: Terms, now: float) -> bool:
    """True when an earlier payment approval still covers these terms with no new ask: approved, not expired, the same merchant origin
    and currency, the same order (terms digest), and a total and conditional charge within the approved amounts (rules 2 and 3)."""
    return (approval.get("state") == "approved" and now <= float(approval.get("expires_at") or 0)
            and approval.get("merchant_origin") == terms.merchant_origin and approval.get("currency") == terms.currency
            and approval.get("terms_digest") == terms.digest and terms.total_minor <= int(approval.get("approved_total_minor") or 0)
            and terms.conditional_minor <= int(approval.get("approved_conditional_minor") or 0))


# ---------------------------------------------------------------- the rows
def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    for k in ("items", "obligations"):
        if k in out and isinstance(out[k], str):
            out[k] = json.loads(out[k] or "null")
    return out


class PayLedger:
    """order_terms and pay_approvals (tables made in store.SCHEMA)."""

    def __init__(self, store: Any, clock: Any = time.time):
        self.store, self.clock = store, clock
        self.attempts = AttemptJournal(store, clock)   # pay_attempts (phase P2)

    def latest(self, owner: str, task: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM order_terms WHERE owner = ? AND task = ? AND status != 'superseded' "
                               "ORDER BY created_at DESC, rowid DESC LIMIT 1", (owner, task))
        return _row(rows[0]) if rows else None

    def terms(self, terms_id: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM order_terms WHERE id = ?", (terms_id,))
        return _row(rows[0]) if rows else None

    def add_terms(self, owner: str, task: str, t: Terms, status: str, checked_at: float | None) -> str:
        """Record `t` as the owner's latest terms for the task; every earlier terms of the task become superseded (a later draft
        replaces ready terms)."""
        tid = "ot_" + secrets.token_hex(6)
        self.store._run("UPDATE order_terms SET status = 'superseded' WHERE owner = ? AND task = ? AND status != 'superseded'", (owner, task))
        self.store._run(
            "INSERT INTO order_terms(id, owner, task, status, merchant_origin, merchant_name, items, obligations, currency, total_minor, "
            "conditional_minor, conditional_terms, payment_label, terms_digest, checked_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, owner, task, status, t.merchant_origin, t.merchant_name, json.dumps(t.items, ensure_ascii=False),
             json.dumps(t.obligations, ensure_ascii=False), t.currency, t.total_minor, t.conditional_minor, t.conditional_terms,
             t.payment_label, t.digest, checked_at, self.clock()))
        return tid

    def mark_checked(self, terms_id: str) -> None:
        self.store._run("UPDATE order_terms SET checked_at = ? WHERE id = ?", (self.clock(), terms_id))

    def live_approval(self, owner: str, task: str) -> dict[str, Any] | None:
        """The owner's newest payment approval of this task that is still in state approved (used / expired ones do not count)."""
        rows = self.store._all("SELECT * FROM pay_approvals WHERE owner = ? AND task = ? AND state = 'approved' "
                               "ORDER BY created_at DESC, rowid DESC LIMIT 1", (owner, task))
        return rows[0] if rows else None

    def add_approval(self, t: Terms, terms_id: str, owner: str, task: str, source: str, approval_id: str | None, signed: bool,
                     expires_at: float, total_minor: int | None = None, conditional_minor: int | None = None) -> str:
        pid = "pa_" + secrets.token_hex(6)
        self.store._run(
            "INSERT INTO pay_approvals(id, terms_id, owner, task, merchant_origin, approved_total_minor, approved_conditional_minor, "
            "currency, terms_digest, source, approval_id, signed, expires_at, state, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, terms_id, owner, task, t.merchant_origin, t.total_minor if total_minor is None else total_minor,
             t.conditional_minor if conditional_minor is None else conditional_minor, t.currency, t.digest, source, approval_id,
             1 if signed else 0, expires_at, "approved", self.clock()))
        return pid

    def approval(self, pid: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM pay_approvals WHERE id = ?", (pid,))
        return rows[0] if rows else None

    def set_state(self, pid: str, state: str) -> None:
        self.store._run("UPDATE pay_approvals SET state = ? WHERE id = ?", (state, pid))


# ---------------------------------------------------------------- the attempt journal (phase P2, rules 6 and 7)
FIRST_CHECK_SECONDS = 10 * 60       # an unknown outcome is looked at again this long after
FINAL_CHECK_SECONDS = 30 * 60       # and once more this long after the first look found nothing
OPEN_STATES = ("pressed", "unknown")   # an attempt in one of these blocks a new press, a new ready review and a reload of the tab


class AttemptJournal:
    """`pay_attempts`: one row per press of a pay control. Written as reserved BEFORE the press, pressed after it; then placed /
    not_placed / unknown with evidence. While a row of an owner/task is pressed or unknown nothing is pressed or bought again for it."""

    def __init__(self, store: Any, clock: Any = time.time):
        self.store, self.clock = store, clock
        self.inflight: set[str] = set()   # attempts reserved and not yet pressed or settled: busy() is true while one is

    def busy(self) -> bool:
        return bool(self.inflight)

    def cut_open(self, before: float) -> int:
        """At a start: a press reserved before the server's start (`before`) was cut before the press went through or not: nobody saw
        whether it did, so it is unknown now (with the first later check). Returns how many."""
        rows = self.store._all("SELECT id FROM pay_attempts WHERE state = 'reserved' AND created_at < ?", (before,))
        for r in rows:
            self.settle(r["id"], "unknown", "the Kelo server stopped during the press: nobody saw whether it went through")
        return len(rows)

    def reserve(self, approval: dict[str, Any], owner: str, task: str) -> str:
        aid, now = "pt_" + secrets.token_hex(6), self.clock()
        self.store._run("INSERT INTO pay_attempts(id, approval_id, terms_id, owner, task, merchant_origin, state, created_at, updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (aid, approval["id"], approval["terms_id"], owner, task, approval["merchant_origin"],
                                                      "reserved", now, now))
        self.inflight.add(aid)
        return aid

    def _set(self, aid: str, **fields: Any) -> None:
        fields["updated_at"] = self.clock()
        self.store._run(f"UPDATE pay_attempts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), aid))

    def pressed(self, aid: str) -> None:
        self.inflight.discard(aid)
        self._set(aid, state="pressed", pressed_at=self.clock())

    def get(self, aid: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM pay_attempts WHERE id = ?", (aid,))
        return _attempt(rows[0]) if rows else None

    def open(self, owner: str, task: str, *, any_task: bool = False) -> dict[str, Any] | None:
        """The newest attempt of this owner/task that is pressed or unknown (None: a press, a review or a reload is allowed).
        `any_task`: every attempt of this owner counts (a job's tab is one checkout across its runs)."""
        if any_task:
            rows = self.store._all("SELECT * FROM pay_attempts WHERE owner = ? AND state IN ('pressed', 'unknown') "
                                   "ORDER BY created_at DESC, rowid DESC LIMIT 1", (owner,))
        else:
            rows = self.store._all("SELECT * FROM pay_attempts WHERE owner = ? AND task = ? AND state IN ('pressed', 'unknown') "
                                   "ORDER BY created_at DESC, rowid DESC LIMIT 1", (owner, task))
        return _attempt(rows[0]) if rows else None

    def open_for_tab(self, owner: str) -> dict[str, Any] | None:
        """The newest pressed or unknown attempt of this tab, whichever run made it."""
        return self.open(owner, "", any_task=True)

    def settle(self, aid: str, state: str, evidence: str, receipt: str = "", digest: str = "") -> bool:
        """placed / not_placed / unknown, with the evidence (the merchant's own words or what was seen). Only a reserved, pressed or
        unknown attempt can be settled; a placed or not_placed one stays as it is. Unknown schedules the first later check."""
        row = self.get(aid)
        self.inflight.discard(aid)
        if row is None or state not in ("placed", "not_placed", "unknown") or row["state"] in ("placed", "not_placed"):
            return False
        fields: dict[str, Any] = {"state": state, "evidence": " ".join(str(evidence or "").split())[:600]}
        if state == "placed":
            fields.update(receipt_text=str(receipt or "")[:2000], receipt_digest=digest)
        self._set(aid, **fields)
        if state == "unknown" and not row["check_jobs"]:
            self.add_job(aid, "first", self.clock() + FIRST_CHECK_SECONDS)
        return True

    def add_job(self, aid: str, kind: str, at: float) -> str:
        row = self.get(aid)
        assert row is not None
        jid = "cj_" + secrets.token_hex(5)
        self._set(aid, check_jobs=json.dumps([*row["check_jobs"], {"id": jid, "kind": kind, "at": at, "done": False}]))
        return jid

    def finish_job(self, aid: str, jid: str) -> None:
        row = self.get(aid)
        if row is not None:
            self._set(aid, check_jobs=json.dumps([{**j, "done": True} if j["id"] == jid else j for j in row["check_jobs"]]))

    def due_jobs(self, now: float | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """(attempt, job) of every unknown attempt whose next later check is due."""
        now = self.clock() if now is None else now
        out = []
        for r in self.store._all("SELECT * FROM pay_attempts WHERE state = 'unknown'"):
            a = _attempt(r)
            out += [(a, j) for j in a["check_jobs"] if not j["done"] and j["at"] <= now]
        return out


def _attempt(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    try:
        out["check_jobs"] = json.loads(out.get("check_jobs") or "[]")
    except ValueError:
        out["check_jobs"] = []
    return out
