"""Payments v2, phase P3 (docs/payments-v2.md, "P3 build notes"): the owner's advance cover for a payment.

Two kinds, both kept as rows and both switched off together by one setting:
- a ONE-ORDER PRE-APPROVAL (`order_preapprovals`): the owner approved one order spec (what, requirements, shipping, an all-in maximum
  with currency, an expiry of at most 30 days) for one task or one schedule. Which order the spec covers is judged once, by the order
  check (the main model), and recorded as `matched_digest` + `matched_origin`; covering is then pure structure (`covers`);
- a SCHEDULE PAYMENT ALLOW (`schedule_pay_allows`): the owner's Allow on a scheduled task's payment card also covers that schedule's
  later purchases from the same merchant origin with the same payment method (and currency), until the schedule is deleted or the
  owner removes it.

No word of any language decides anything here: ids, digests, origins, numbers and dates only.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from .pay_terms import Terms, exponent

MAX_DAYS = 30
DAY = 86400.0
SWITCH = "preapprove_off"          # store meta key: "1" = pre-approvals and schedule allows cover nothing
SOURCE_PREAPPROVAL, SOURCE_ALLOW = "preapproval", "schedule_allow"
COVER_SOURCES = (SOURCE_PREAPPROVAL, SOURCE_ALLOW)


def _text(v: Any, n: int = 300) -> str:
    return " ".join(str(v or "").split())[:n]


def _lines(v: Any, n: int = 12, width: int = 200) -> list[str]:
    return [_text(x, width) for x in (v if isinstance(v, list) else []) if _text(x, width)][:n]


def clean_spec(raw: dict[str, Any]) -> dict[str, Any]:
    """The tool's arguments -> the spec as it is stored and shown (bad entries left out, long texts cut)."""
    items = []
    for it in raw.get("items") if isinstance(raw.get("items"), list) else []:
        if isinstance(it, dict) and _text(it.get("description"), 200):
            try:
                qty = max(1, int(it.get("qty") or 1))
            except (TypeError, ValueError):
                qty = 1
            items.append({"description": _text(it.get("description"), 200), "qty": qty})
    return {"intent": _text(raw.get("intent"), 300), "items": items[:20], "requirements": _lines(raw.get("requirements")),
            "shipping": _text(raw.get("shipping"), 300), "merchant_policy": _text(raw.get("merchant_policy"), 300),
            "delegated_choices": _lines(raw.get("delegated_choices"))}


class Preapprovals:
    """`order_preapprovals` and `schedule_pay_allows` (tables made in store.SCHEMA) plus the owner's switch."""

    def __init__(self, store: Any, clock: Any = time.time, schedule_name: Any = None):
        self.store, self.clock = store, clock
        self.schedule_name = schedule_name or (lambda _sid: "")   # (schedule id) -> its name, for the permissions list

    # ------------------------------------------------------------------ the switch
    def enabled(self) -> bool:
        return self.store.meta(SWITCH) != "1"

    def set_enabled(self, on: bool) -> None:
        self.store.set_meta(SWITCH, "" if on else "1")

    # ------------------------------------------------------------------ one-order pre-approvals
    def create(self, owner_kind: str, owner_id: str, spec: dict[str, Any], max_total_minor: int, currency: str, expires_at: float,
              approval_id: str = "") -> str:
        """Store an approved spec; the earlier active one of the same task or schedule is revoked (one at a time)."""
        self.store._run("UPDATE order_preapprovals SET state = 'revoked' WHERE owner_kind = ? AND owner_id = ? AND state = 'active'",
                        (owner_kind, str(owner_id)))
        pid = "op_" + secrets.token_hex(6)
        self.store._run("INSERT INTO order_preapprovals(id, owner_kind, owner_id, spec, max_total_minor, currency, expires_at, state, "
                        "approval_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (pid, owner_kind, str(owner_id), json.dumps(spec, ensure_ascii=False), int(max_total_minor), currency, expires_at,
                         "active", approval_id, self.clock()))
        return pid

    def _expire(self) -> None:
        self.store._run("UPDATE order_preapprovals SET state = 'expired' WHERE state = 'active' AND expires_at < ?", (self.clock(),))

    def get(self, pid: str) -> dict[str, Any] | None:
        self._expire()
        rows = self.store._all("SELECT * FROM order_preapprovals WHERE id = ?", (pid,))
        return _spec_row(rows[0]) if rows else None

    def active(self, owner_kind: str, owner_id: str) -> dict[str, Any] | None:
        self._expire()
        rows = self.store._all("SELECT * FROM order_preapprovals WHERE owner_kind = ? AND owner_id = ? AND state = 'active' "
                               "ORDER BY created_at DESC, rowid DESC LIMIT 1", (owner_kind, str(owner_id)))
        return _spec_row(rows[0]) if rows else None

    def lapsed(self, owner_kind: str, owner_id: str) -> bool:
        """The owner approved an order for this task or schedule once, and nothing of it is active now (expired, used or revoked)."""
        self._expire()
        rows = self.store._all("SELECT state FROM order_preapprovals WHERE owner_kind = ? AND owner_id = ? ORDER BY created_at DESC, "
                               "rowid DESC LIMIT 1", (owner_kind, str(owner_id)))
        return bool(rows) and rows[0]["state"] == "expired"

    def list(self) -> list[dict[str, Any]]:
        self._expire()
        return [_spec_row(r) for r in self.store._all("SELECT * FROM order_preapprovals WHERE state = 'active' ORDER BY created_at DESC")]

    def revoke(self, pid: str) -> bool:
        self._expire()
        n = self.store._run("UPDATE order_preapprovals SET state = 'revoked' WHERE id = ? AND state = 'active'", (pid,)).rowcount
        return bool(n)

    def mark_used(self, pid: str) -> None:
        self.store._run("UPDATE order_preapprovals SET state = 'used' WHERE id = ? AND state = 'active'", (pid,))

    def drop_schedule_specs(self, schedule_id: int) -> None:
        self.store._run("UPDATE order_preapprovals SET state = 'revoked' WHERE owner_kind = 'schedule' AND owner_id = ? AND state = 'active'",
                        (str(schedule_id),))

    def matched(self, pid: str, digest: str, origin: str) -> None:
        """The order check found the cart with this digest at this origin inside the spec (the only place a spec is compared to a cart)."""
        self.store._run("UPDATE order_preapprovals SET matched_digest = ?, matched_origin = ? WHERE id = ? AND state = 'active'",
                        (digest, origin, pid))

    def covers(self, spec: dict[str, Any] | None, terms: Terms) -> bool:
        """True when the active spec `spec` covers these terms: matched by the order check (same digest and origin), the same currency,
        and the all-in total (due now plus any conditional charge) within the maximum. Structure only."""
        if spec is None or not self.enabled() or spec["state"] != "active" or self.clock() > spec["expires_at"]:
            return False
        return (bool(spec["matched_digest"]) and spec["matched_digest"] == terms.digest and spec["matched_origin"] == terms.merchant_origin
                and spec["currency"] == terms.currency and terms.total_minor + terms.conditional_minor <= spec["max_total_minor"])

    # ------------------------------------------------------------------ schedule payment allows
    def allow_add(self, schedule_id: int, origin: str, payment_label: str, currency: str, approval_id: str = "") -> str:
        existing = self.allow_for(schedule_id, origin, payment_label, currency, ignore_switch=True)
        if existing is not None:
            return existing["id"]
        aid = "sa_" + secrets.token_hex(6)
        self.store._run("INSERT INTO schedule_pay_allows(id, schedule_id, merchant_origin, payment_label, currency, approval_id, created_at) "
                        "VALUES(?,?,?,?,?,?,?)", (aid, int(schedule_id), origin, _text(payment_label, 60), currency, approval_id, self.clock()))
        return aid

    def allow_for(self, schedule_id: int, origin: str, payment_label: str, currency: str, *, ignore_switch: bool = False) -> dict[str, Any] | None:
        if not ignore_switch and not self.enabled():
            return None
        rows = self.store._all("SELECT * FROM schedule_pay_allows WHERE schedule_id = ? AND merchant_origin = ? AND payment_label = ? "
                               "AND currency = ?", (int(schedule_id), origin, _text(payment_label, 60), currency))
        return rows[0] if rows else None

    def allow(self, aid: str) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM schedule_pay_allows WHERE id = ?", (aid,))
        return rows[0] if rows else None

    def allows(self) -> list[dict[str, Any]]:
        out = []
        for r in self.store._all("SELECT * FROM schedule_pay_allows ORDER BY created_at DESC"):
            out.append({**r, "schedule_name": self.schedule_name(r["schedule_id"])})
        return out

    def allow_remove(self, aid: str) -> bool:
        return bool(self.store._run("DELETE FROM schedule_pay_allows WHERE id = ?", (aid,)).rowcount)

    def drop_schedule(self, schedule_id: int) -> None:
        """The schedule was deleted or its task changed: its allow and its spec end (a new task is asked again)."""
        self.store._run("DELETE FROM schedule_pay_allows WHERE schedule_id = ?", (int(schedule_id),))
        self.drop_schedule_specs(schedule_id)

    # ------------------------------------------------------------------ is an approval's cover still there (at the press)
    def still_valid(self, approval: dict[str, Any]) -> bool:
        """For a payment approval whose source is preapproval / schedule_allow: the spec is still active (not revoked, not expired) or the
        allow still exists. Other sources are always valid here."""
        src, ref = approval.get("source"), approval.get("approval_id") or ""
        if src == SOURCE_PREAPPROVAL:
            row = self.get(ref)
            return row is not None and row["state"] == "active" and self.enabled()
        if src == SOURCE_ALLOW:
            return self.allow(ref) is not None and self.enabled()
        return True

    # ------------------------------------------------------------------ what the apps show
    def public_specs(self) -> list[dict[str, Any]]:
        out = []
        for r in self.list():
            name = self.schedule_name(int(r["owner_id"])) if r["owner_kind"] == "schedule" and str(r["owner_id"]).isdigit() else ""
            out.append({"id": r["id"], "owner_kind": r["owner_kind"], "owner_id": r["owner_id"], "owner_name": name, **r["spec"],
                        "max_total": r["max_total_minor"] / 10 ** exponent(r["currency"]), "currency": r["currency"],
                        "expires_at": int(r["expires_at"]), "state": r["state"], "created_at": int(r["created_at"])})
        return out


def _spec_row(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    try:
        out["spec"] = json.loads(out.get("spec") or "{}")
    except ValueError:
        out["spec"] = {}
    return out
