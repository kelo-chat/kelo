"""`order_preapprove`: the owner approves ONE order in advance (docs/payments-v2.md, P3). The agent calls it only after the owner confirmed
the order's spec in chat; the owner answers ONE card (what, the all-in maximum, the expiry). Allow stores the spec for this task, or for
the schedule named in `schedule_id`; a new one for the same task or schedule revokes the old one. The agent has no tool to revoke:
the owner removes it in the permissions page. The tool only collects permission; the checkout tools (purchase.py) use it."""

from __future__ import annotations

import time
from datetime import datetime, timezone as _tz
from typing import Any
from zoneinfo import ZoneInfo

from ..agent import Tool, approval_of_current_call
from ..i18n import N_, t
from ..pay_preapprovals import DAY, MAX_DAYS, Preapprovals, clean_spec
from ..pay_terms import exponent, iso_currency, minor_units
from ..wallet import money_text

CARD_TEXT = N_("Approve one order in advance: {intent} · up to {amount} in total · until {date}")


def _owner_key(schedule_id: int | None) -> tuple[str, str]:
    from ..workers import current_turn
    if schedule_id:
        return "schedule", str(schedule_id)
    return "task", str((current_turn.get() or {}).get("task_id") or "")


def _expiry(value: Any, now: float, tz: str) -> tuple[float | None, str]:
    """The expiry as unix time: (value, "") or (None, what is wrong). Default and maximum: 30 days from now."""
    if value in (None, ""):
        return now + MAX_DAYS * DAY, ""
    try:
        if isinstance(value, (int, float)):
            at = float(value)
        else:
            d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            if d.tzinfo is None:
                try:
                    d = d.replace(tzinfo=ZoneInfo(tz) if tz else _tz.utc)
                except Exception:  # noqa: BLE001 - an unknown zone name
                    d = d.replace(tzinfo=_tz.utc)
            at = d.timestamp()
    except (ValueError, OverflowError):
        return None, "expires_at must be an ISO 8601 date-time (for example 2026-12-01T18:00:00), or left out for 30 days"
    if at <= now:
        return None, "expires_at is in the past"
    if at > now + MAX_DAYS * DAY + 60:
        return None, f"expires_at is more than {MAX_DAYS} days away; a pre-approval lasts at most {MAX_DAYS} days"
    return at, ""


def preapprove_tools(pre: Preapprovals, *, schedule_exists: Any = None, tz: Any = None, clock: Any = time.time) -> list[Tool]:
    """`schedule_exists(id)` -> bool. `tz()` -> the owner's timezone name (for an expiry written without an offset)."""

    def zone() -> str:
        try:
            return str(tz() or "") if tz is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def cap_of(args: dict[str, Any]) -> tuple[int | None, str]:
        m = args.get("max_total") if isinstance(args.get("max_total"), dict) else {}
        cur = iso_currency(m.get("currency"))
        return (minor_units(m.get("amount"), cur) if cur else None), cur

    def validate(args: dict[str, Any]) -> str | None:
        if not pre.enabled():
            return ("pre-approving an order is switched off in the owner's settings (Permissions). Do not ask again; the order's "
                    "payment will ask as usual.")
        from ..workers import current_turn
        sid = args.get("schedule_id")
        if sid is not None and (isinstance(sid, bool) or not isinstance(sid, int) or (schedule_exists is not None and not schedule_exists(sid))):
            return "schedule_id must be the id of an existing schedule (create the schedule first with its tool, then pass its id)"
        if (current_turn.get() or {}).get("unattended") and not sid:
            return "a run nobody watches cannot ask the owner for this; stop and say in the run's report that the order needs their approval"
        spec = clean_spec(args)
        if not spec["intent"]:
            return "intent is required: what the owner wants bought, in one sentence, as they confirmed it"
        if not spec["items"]:
            return "items is required: every thing the owner confirmed, [{description, qty}]"
        cap, cur = cap_of(args)
        if not cur:
            return "max_total.currency is required: the ISO 4217 code (EUR, USD, KRW); never guess it from a symbol"
        if cap is None or cap <= 0:
            return "max_total.amount is required: the most the whole order may cost in total, with tax and shipping, as a number above 0"
        _, why = _expiry(args.get("expires_at"), clock(), zone())
        return why or None

    async def describe(args: dict[str, Any]) -> str:
        spec = clean_spec(args)
        cap, cur = cap_of(args)
        at, _ = _expiry(args.get("expires_at"), clock(), zone())
        date = datetime.fromtimestamp(at or clock(), ZoneInfo(zone()) if zone() else _tz.utc).strftime("%Y-%m-%d")
        return t(CARD_TEXT, intent=spec["intent"][:120], amount=money_text((cap or 0) / 10 ** exponent(cur), cur), date=date)[:300]

    def approval_extra(_approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        cap, cur = cap_of(args)
        at, _ = _expiry(args.get("expires_at"), clock(), zone())
        return {"preapproval": {**clean_spec(args), "max_total": (cap or 0) / 10 ** exponent(cur), "currency": cur,
                                "max_total_text": money_text((cap or 0) / 10 ** exponent(cur), cur), "expires_at": int(at or 0),
                                "owner_kind": "schedule" if args.get("schedule_id") else "task"}}

    async def run(args: dict[str, Any]) -> str:
        cap, cur = cap_of(args)
        at, _ = _expiry(args.get("expires_at"), clock(), zone())
        assert cap is not None and at is not None
        kind, ref = _owner_key(args.get("schedule_id"))
        pid = pre.create(kind, ref, clean_spec(args), cap, cur, at, approval_of_current_call() or "")
        what = "for the schedule" if kind == "schedule" else "for this task"
        return (f"The owner approved this one order in advance {what} (pre-approval {pid}): up to {money_text(cap / 10 ** exponent(cur), cur)} "
                "in total, until the expiry. Nothing was bought. When you buy it, go through begin_order and checkout_review as usual: "
                "if the cart is exactly the approved order and the total is within the maximum, the payment needs no second card; "
                "anything else asks as usual. It covers one order only.")

    item = {"type": "object", "properties": {"description": {"type": "string"}, "qty": {"type": "integer"}}, "required": ["description"]}
    return [Tool(
        "order_preapprove",
        "Ask the owner to approve ONE order now that you will place later in this task or in a scheduled job. Call it once the order's details "
        "are settled (the owner's request and answers); the card is their approval, so do not ask it in chat too. The owner sees one card; it collects permission and buys nothing. It covers one order: "
        "the items and requirements below, within an all-in maximum (tax and shipping included). For a scheduled job create the schedule "
        "first and pass its id as schedule_id; without it the approval is for this task. If the owner declines, do not ask again.",
        {"type": "object", "properties": {
            "intent": {"type": "string", "description": "what the owner wants bought, in one sentence"},
            "items": {"type": "array", "description": "what to buy", "items": item},
            "requirements": {"type": "array", "items": {"type": "string"}, "description": "must-haves: size, colour, delivery date, brand"},
            "shipping": {"type": "string", "description": "where it goes and how fast, as the owner confirmed"},
            "merchant_policy": {"type": "string", "description": "which shops are fine (a named shop, or any shop that meets the requirements)"},
            "delegated_choices": {"type": "array", "items": {"type": "string"}, "description": "choices the owner left to you"},
            "max_total": {"type": "object", "description": "the most the whole order may cost, tax and shipping included",
                          "properties": {"amount": {"type": "number"}, "currency": {"type": "string", "description": "ISO 4217 code"}},
                          "required": ["amount", "currency"]},
            "expires_at": {"type": "string", "description": "ISO 8601 date-time the approval ends; default and maximum 30 days from now"},
            "schedule_id": {"type": "integer", "description": "the schedule this approval is for; leave out for the current task"}},
         "required": ["intent", "items", "max_total"]},
        run, requires_approval=True, validate=validate, describe=describe, approval_extra=approval_extra)]
