"""confirm_purchase: the one gate in front of anything that spends the user's money or commits them on a website
(an order, a booking, a sign-up with a fee, a payment). Plus the wallet tools: wallet_cards and browser_fill_card.

The agent prepares the order in the browser up to the last step, then calls this tool with exactly what will be bought and the total.
The user sees an Allow/Deny card (the app's approval broker) with those words and a fresh picture of the page; the call waits for
the answer. Deny, or no answer in time, means no payment. Only after Allow does the final pay/confirm click go through without a
second card (see BrowserSession.allow_purchase), for a few clicks on that same site and for a few minutes.

Paying with a card from the user's wallet (`card`, see assistant.wallet):
- a card with auto-pay ON whose payment fits the user's rules (limits, a site paid at before, not paused) is approved without asking;
  the chat still shows it (`payment_auto` event) and the app gets a notification;
- otherwise the usual approval card asks, naming the card; for an end-to-end card the Allow also waits for the phone to send that card
  once (`card_needed` event; the phone posts it to /api/wallet/deliveries/{approval_id}).
Then browser_fill_card types the card into the checkout, only on the approved site and only inside that purchase window.
The model never sees a card number: tool texts name the card by label and last 4 digits only.

Inside an order the user asked for (tools/order.py) this is the ONE card of the order, and it shows everything at once: the items with
their options and quantity (`items`), the total, the address it goes to (the order's shipping profile, or the address the site account
already had: `ship_to`), the coupon, the card and the site, with a fresh picture of the page. Once it is approved the order covers
nothing more; a second payment in the same order always asks (reason "order_again"), even with auto-pay. A Deny ends the order.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..agent import Ours, Tool, ToolOutput, approval_of_current_call, approval_timeout, current_box, current_call, emit_ui
from ..agent.fence import data
from ..i18n import N_, t
from ..wallet import CARD_TTL, EXPLAIN, CardDeliveries, CardSecret, Decision, Wallet, WalletError, money_text
from .browser import EXTRA_AUTH_NOTE, PURCHASE_WINDOW_SECONDS, BrowserPolicyError, BrowserSession, browser_owner
from .. import order_check, pay_terms
from ..pay_preapprovals import COVER_SOURCES, SOURCE_ALLOW, SOURCE_PREAPPROVAL, Preapprovals
from .checkout import CHECKING, DESCRIBE_JS, Reviews, amount_over, origin_of, read_page, total_shown
from .page_tree import ref_selector
from .sites import registrable as site_key, same_site

MAX_ITEMS = 20

CARD_NUMBER = re.compile(r"(?:\d[ -]?){13,19}")   # a run of 13-19 digits (spaces/dashes allowed) looks like a card number
MAX_SUMMARY = 400
APPROVAL_WAIT = 300.0   # how long the app's approval card waits (server.APPROVAL_TIMEOUT_SECONDS); a card slot lives that long plus CARD_TTL
DECLINED = ("The user did NOT approve this payment (they said no, or did not answer in time). Do NOT click pay, order, book or confirm and do not call checkout_submit, "
            "and do not try another way to pay. Leave the order unpaid and tell the user plainly; ask what they want to change if anything.")
NO_CARD_SENT = ("The user allowed the payment, but their phone did not send the card in time, so nothing can be paid with it. Do NOT click "
                "pay and do not try another way to pay. Tell the user and ask them to try again (Allow, then confirm with fingerprint/face "
                "on the phone).")


def _host(site: str) -> str:
    text = (site or "").strip()
    host = urlsplit(text if "://" in text else "https://" + text).hostname or ""
    return host.lower().removeprefix("www.")


def _amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.]", "", value)
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    return None


money = money_text


def _brief(card: dict[str, Any]) -> dict[str, Any]:
    return {"id": card["id"], "label": card["label"], "brand": card["brand"], "last4": card["last4"]}


@dataclass
class _Arm:
    """The card of a payment the user (or the auto-pay rules) just approved, ready to be typed on that site for a few minutes.
    An end-to-end card's details sit here in memory only until they are typed once or the time runs out."""
    card_id: str
    host: str
    until: float
    source: str                     # "server" (the sealed auto-pay copy) | "phone" (sent once by the phone)
    secret: CardSecret | None = None

    def live(self) -> bool:
        if time.monotonic() <= self.until:
            return True
        self.wipe()
        return False

    def wipe(self) -> None:
        if self.secret is not None:
            self.secret.wipe()
            self.secret = None


def why_text(decision: Decision | None, note: str = "") -> str:
    """Why a payment asked the user, in words the model can repeat as they are (it must not guess about the user's settings)."""
    if decision is None or decision.auto:
        return ""
    words = note or EXPLAIN.get(decision.reason, decision.text)
    return (f" It needed the user's approval because [{decision.reason}]: {words}. If you explain why it asked, say exactly this "
            "reason and nothing else about the user's settings.")


def clean_items(raw: Any) -> list[dict[str, Any]]:
    """The order's lines as the card shows them: [{"name", "options", "quantity", "price_text"}] (bad entries are left out)."""
    out: list[dict[str, Any]] = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            continue
        try:
            qty = int(it.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        price = it.get("price")
        out.append({"name": " ".join(str(it["name"]).split())[:120], "options": " ".join(str(it.get("options") or "").split())[:160],
                    "quantity": max(1, min(qty, 9999)), "price_text": " ".join(str(price).split())[:40] if price not in (None, "") else ""})
        if len(out) >= MAX_ITEMS:
            break
    return out


def purchase_tools(session: BrowserSession | None, wallet: Wallet | None = None, deliveries: CardDeliveries | None = None,
                   *, card_wait: float = CARD_TTL, grants: Any = None, orders: Any = None, pins: Any = None, pin_deliveries: Any = None,
                   ledger: Any = None, paths: Any = None, notify: Any = None, timezone: Any = None, clock: Any = time.time,
                   reviews: Reviews | None = None, pay_ledger: pay_terms.PayLedger | None = None, ask: Any = None,
                   preapprovals: Preapprovals | None = None) -> list[Tool]:
    """`grants`: the standing permissions of conditional tasks (schedule_grants.ConditionalGrants): a run of a task (a schedule, a
    watcher's hit, a job) the user allowed at setup to buy on its own completes a purchase within its limits without a card.
    `orders`: the order going on (tools/order.Orders). `pins` / `pin_deliveries` (payment_pins.py): a payment PIN saved for the site is
    released by the phone for an approved payment (with the approval's Allow, or a `pin_needed` card when there was none) and kept in
    `pin_deliveries` for one use. `ledger`: the approved payment of each tab (the payment-step card shows it). `paths`: PaymentPaths. `notify(title, body, task_id)`: an urgent notice to the owner (purchase_result's wrong charge). `timezone()`: the
    owner's timezone name (a cancel deadline the page gave without one). `pay_ledger`: the order terms and payment approvals
    (pay_terms.py; an in-memory one when none is given). `ask(system, user)` -> dict | None: the order check's one model request
    (model_json.ModelJson.ask; None = no model: every order check ends in open_choice). `preapprovals`: phase P3 (pay_preapprovals.py):
    the owner's one-order pre-approval and a schedule's payment allow cover a payment with no card."""
    arms: dict[str, _Arm] = {}               # device -> the card of the payment approved last on its tab
    # (device, tool call id) -> (the decision taken for it, the covering schedule grant, the reason in words): read back by the handler
    decided: dict[tuple[str, str], tuple[Decision, Any, str]] = {}
    # (device, call id) -> the P3 cover found for the checkout_review being decided: (source, preapproval / allow id)
    covers: dict[tuple[str, str], tuple[str, str]] = {}

    def card_of(args: dict[str, Any]) -> dict[str, Any] | None:
        """The card named in `card`; with neither a card nor another payment way named, the card a task's purchase permission
        names (in its run), else the owner's default card (for a payment)."""
        ref = str(args.get("card") or "").strip()
        if not wallet:
            return None
        if ref:
            return wallet.find(ref)
        if grants is not None and not str(args.get("pay_with") or "").strip() and "summary" in args:
            g = grants.purchase_check(_host(str(args.get("site") or "")), _amount(args.get("amount")) or 0.0,
                                      str(args.get("currency") or "KRW"), str(args.get("summary") or "")).grant
            if (named := grants.grant_card(g)) is not None and named["usable"] and not named["expired"]:
                return named
        if not str(args.get("pay_with") or "").strip() and (_amount(args.get("amount")) or 0) > 0 and "summary" in args:
            d = wallet.default_card()
            return d if d and d["usable"] and not d["expired"] else None
        return None

    def names() -> str:
        return ", ".join(f"'{c['label']}'" for c in wallet.cards()) if wallet else ""

    def money_args(args: dict[str, Any]) -> tuple[str, float, str]:
        return _host(str(args.get("site") or "")), _amount(args.get("amount")) or 0.0, str(args.get("currency") or "KRW").upper()

    def validate(args: dict[str, Any]) -> str | None:
        summary = str(args.get("summary") or "").strip()
        if not summary:
            return "summary is required: exactly what will be bought or booked (items, options, quantity, date)"
        if len(summary) > MAX_SUMMARY:
            return f"summary is too long; keep it under {MAX_SUMMARY} characters"
        amount = _amount(args.get("amount"))
        if amount is None or amount < 0:
            return "amount is required: the total the page shows, as a number (0 if it is free)"
        if not _host(str(args.get("site") or "")):
            return "site is required: the address of the site where the payment happens"
        if any(CARD_NUMBER.search(str(args.get(k) or "")) for k in ("summary", "pay_with", "site", "card", "ship_to", "coupon", "items")):
            return "it contains what looks like a card number. Never put card numbers in a request; name the payment way in words"
        if args.get("items") is not None and not isinstance(args.get("items"), list):
            return "items must be a list of {name, options, quantity, price}"
        if len(str(args.get("ship_to") or "")) > 300:
            return "ship_to is too long; give the address the page shows, at most 300 characters"
        order = orders.current() if orders is not None else None
        if order is not None and site_key(_host(str(args["site"]))) != order.site:
            return (f"the order going on is on {order.site}; a payment on another site is not part of it. Pay there (or end_order "
                    "first if the user wants something else)")
        page_host = session.current_host() if session else ""
        if session and page_host and not _same_site(page_host, _host(str(args["site"]))):
            return f"the browser is on {page_host}, not on {_host(str(args['site']))}. Open the order page of that site first"
        if str(args.get("card") or "").strip():
            if wallet is None:
                return "this app has no wallet; leave out card and pay with what the site holds"
            card = card_of(args)
            if card is None:
                return f"no card {str(args['card'])[:40]!r} in the wallet. Cards: {names() or 'none'} (see wallet_cards). Use one of these labels"
            if card["expired"]:
                return f"the card '{card['label']}' has expired; tell the user (they can update it in the app) or use another card"
            if not card["usable"]:
                return f"the card '{card['label']}' has no stored details yet; ask the user to finish adding it in the app, or use another card"
        return None

    def judge(args: dict[str, Any]) -> tuple[Decision, Any, str]:
        """Whether this payment goes ahead without a card, and if not, why: first a task's standing permission (only in a run of that
        task, only within its limits; a card kept on the phone only still needs the phone; the wallet's pause and the vault lock
        still stop it), then the wallet's auto-pay rules."""
        card = card_of(args)
        host, amount, cur = money_args(args)
        order = orders.current() if orders is not None else None
        if order is not None and order.paid:
            return Decision(False, "order_again"), None, ""   # one payment per order: another one always asks
        cover = covers.get((browser_owner.get(), current_call.get()))
        if cover is not None:   # the owner's pre-approval or schedule allow (phase P3): the same stops as a task's permission
            if card is not None and not card["server_copy"]:
                return Decision(False, "phone_needed"), None, ""
            if wallet is not None and wallet.paused():
                return Decision(False, "paused"), None, ""
            if wallet is not None and wallet.store.vault_locked():
                return Decision(False, "vault_locked"), None, ""
            return Decision(True, cover[0]), None, ""
        check = grants.purchase_check(host, amount, cur, str(args.get("summary") or ""), card=card) if grants is not None else None
        if check is not None and check.grant is not None:
            if card is not None and not card["server_copy"]:
                return Decision(False, "phone_needed"), None, ""
            if wallet is not None and wallet.paused():
                return Decision(False, "paused"), None, ""     # the wallet's pause and the vault lock stop a task's purchase too
            if wallet is not None and wallet.store.vault_locked():
                return Decision(False, "vault_locked"), None, ""
            return Decision(True, "task_grant"), check.grant, ""
        if check is not None and check.reason:
            return Decision(False, check.reason), None, check.note
        if not wallet or card is None:
            return Decision(False, "no_card"), None, ""
        return wallet.decide(card, host, amount, cur), None, ""

    def auto(args: dict[str, Any]) -> bool:
        """Approved without a card only by the user's own rules: a task's standing permission, or auto-pay for this card."""
        verdict = judge(args)
        if len(decided) > 50:      # entries of calls the user then declined (their handler never ran)
            decided.clear()
        decided[(browser_owner.get(), current_call.get())] = verdict
        return verdict[0].auto

    def declined(args: dict[str, Any]) -> str:
        verdict = decided.get((browser_owner.get(), current_call.get()))
        ended = orders.end() if orders is not None else None   # a Deny ends the order: its steps ask again
        return DECLINED + (why_text(verdict[0], verdict[2]) if verdict else "") + (
            f" The order on {ended.site} is closed." if ended is not None else "")

    def shipping_of(args: dict[str, Any]) -> dict[str, Any] | None:
        """Where the order goes, as the card shows it: the order's address (a shipping profile or one given in the form), else the
        address the site account already had (`ship_to`, read from the page)."""
        order = orders.current() if orders is not None else None
        prof = order.profile if order is not None else None
        given = " ".join(str(args.get("ship_to") or "").split())[:300]
        if prof:
            from ..address import formatted
            return {"source": order.source or "profile", "label": prof.get("label", ""), "name": prof.get("name", ""),
                    "phone": prof.get("phone", ""), "formatted": formatted(prof, with_name=False), "country": prof.get("country", "")}
        if given:
            return {"source": "site", "label": "", "name": "", "phone": "", "formatted": given, "country": ""}
        return None

    def approval_extra(approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """What the approval card shows besides the words: the card, why it asks, and whether the phone must send the card on Allow."""
        host, amount, cur = money_args(args)
        order = orders.current() if orders is not None else None
        out: dict[str, Any] = {"payment": {"site": host, "summary": str(args.get("summary") or "").strip()[:MAX_SUMMARY], "amount": amount,
                                           "currency": cur, "amount_text": money(amount, cur), "items": clean_items(args.get("items")),
                                           "shipping": shipping_of(args), "coupon": " ".join(str(args.get("coupon") or "").split())[:60],
                                           "order": {"what": order.what} if order is not None else None}}
        card = card_of(args)
        decision = judge(args)[0]
        if not decision.auto and decision.reason != "no_card":
            out["reason"], out["reason_text"] = decision.reason, decision.text
        pin = pin_for(host)
        if pin is not None and pin_deliveries is not None:   # Allow on the phone also releases the site's payment PIN (fingerprint / face)
            pin_deliveries.expect(approval_id, pin, host, {"amount_text": out["payment"]["amount_text"]},
                                  wait=max(APPROVAL_WAIT, approval_timeout.get()) + pin_deliveries.ttl)
            out["pin"] = {"id": pin["id"], "label": pin["label"], "site": pin["site"], "digits": pin["digits"], "needs_pin": True}
        if wallet and card:
            needs = not card["server_copy"]
            if needs and deliveries is not None:
                deliveries.expect(approval_id, card, out["payment"], wait=max(APPROVAL_WAIT, approval_timeout.get()) + deliveries.ttl)
            out["card"] = {**_brief(card), "auto_pay": card["auto_pay"], "needs_card": needs, "reason": decision.reason,
                           "reason_text": decision.text}
        return out

    async def describe(args: dict[str, Any]) -> str:
        if session:
            await session.card(t("Waiting for payment approval"), force=True)   # the user sees the prepared order page next to the card
        host, amount, cur = money_args(args)
        pay = str(args.get("pay_with") or "").strip()[:60]
        line = t("Pay on {host}: {summary} · Total {amount}", host=host, summary=str(args.get('summary')).strip(), amount=money(amount, cur))
        card = card_of(args)
        if wallet and card:
            line += " · " + t("Card: {card}", card=card['label'])
            line += " " + t("(no auto-pay: {reason})", reason=judge(args)[0].text)
            if not card["server_copy"]:
                line += " · " + t("If you allow it, your phone sends the card details once")
        elif pay:
            line += " · " + t("Payment method: {method}", method=pay)
        ship = shipping_of(args)
        if ship:   # the card's text is kept in the approval history: the nickname, or only the start of the address
            first = ship["formatted"].split("\n")[0]
            line += " · " + t("Ship to: {address}", address=ship["label"] or (first[:40] + ("…" if len(first) > 40 else "")))
        return line[:300]

    def arm(card: dict[str, Any], host: str, source: str, secret: CardSecret | None) -> None:
        key = browser_owner.get()
        old = arms.pop(key, None)
        if old:
            old.wipe()
        a = arms[key] = _Arm(card["id"], host, time.monotonic() + PURCHASE_WINDOW_SECONDS, source, secret)
        try:   # an unused card does not outlive its window, even if nobody asks again
            asyncio.get_running_loop().call_later(PURCHASE_WINDOW_SECONDS + 1, a.live)
        except RuntimeError:
            pass

    def pin_for(host: str) -> dict[str, Any] | None:
        if pins is None or pin_deliveries is None:
            return None
        if wallet is not None and wallet.store.vault_locked():
            return None
        return pins.for_host(host)

    async def after_ok(args: dict[str, Any], card: dict[str, Any] | None, grant: Any) -> str:
        """An approved payment: remembered for this tab (the payment-step card, the PIN), and the site's payment PIN released when
        allowed: with the approval's Allow, or (no card was asked) the phone asks the user to confirm with fingerprint / face. A run
        nobody watches gets the PIN only when its task was allowed at setup to use it, and then the phone still has to release it."""
        if ledger is None:
            return ""
        from ..payment_pins import ApprovedPayment
        from ..workers import current_turn
        host, amount_value, cur = money_args(args)
        owner = browser_owner.get()
        unattended = bool((current_turn.get() or {}).get("unattended"))
        pin = pin_for(host)
        slot, say = "", ""
        if pin is not None:
            approval = approval_of_current_call()
            if approval and pin_deliveries.has(approval):
                slot = approval
            elif (grant is not None and grant.get("use_pin")) or (grant is None and not approval and not unattended):
                import secrets as _s
                slot = "pr_" + _s.token_hex(6)
                pin_deliveries.expect(slot, pin, host, {"amount_text": money(amount_value, cur)})
            if slot:
                deadline = pin_deliveries.started(slot) or time.time() + pin_deliveries.ttl
                request = {"type": "pin_needed", "slot_id": slot, "approval_id": slot, "site": host, "amount_text": money(amount_value, cur),
                           "pin": {"id": pin["id"], "label": pin["label"], "site": pin["site"], "digits": pin["digits"]},
                           "expires_at": int(deadline), "urgent": unattended or slot != approval}
                await emit_ui(request)
                if getattr(pin_deliveries, "on_request", None) is not None and slot != approval:
                    try:
                        pin_deliveries.on_request(request)   # the server's urgent notification: the phone has to confirm it
                    except Exception:  # noqa: BLE001
                        pass
                say = (" A payment PIN is saved for this site and the user's phone releases it for this payment: if the checkout asks for "
                       "it, use browser_type_payment_pin (never type a PIN yourself).")
            else:
                say = (" A payment PIN is saved for this site but is not released in this run: if the checkout asks for it, the user "
                       "enters it (browser_pin_keypad_card shows the page's keypad in their card).")
        pay_with = card["label"] if card else str(args.get("pay_with") or "").strip()[:60]
        ledger.record(ApprovedPayment(owner, host, str(args.get("summary") or "").strip()[:MAX_SUMMARY], money(amount_value, cur),
                                      clean_items(args.get("items")), pay_with, time.time(), slot, path="card_form" if card else "site_method",
                                      amount=amount_value, currency=cur, task_id=(current_turn.get() or {}).get("task_id")))
        hint = paths.hint(host) if paths is not None else ""
        return (say + (" " + hint if hint else "") + " If the page then asks to approve the payment outside it (a code, a QR code, a "
                "countdown, a push, a passkey) call payment_step_wait; if it asks for a PIN on a keypad that hides or scrambles its "
                "digits, never press it: call browser_pin_keypad_card.")

    async def confirm(args: dict[str, Any]) -> str:
        out = await confirm_inner(args)
        if orders is not None:
            orders.mark_paid()   # the order's one payment: from here on its steps are covered by this approval only
        cur = current_box.get()   # the receipt check the result asks for is loaded right away (no find_tools round trip)
        if cur is not None and cur[0] is not None:
            cur[0].activate(["purchase_result"], [t.name for t in cur[1]])
        return out

    def order_extra(args: dict[str, Any]) -> dict[str, Any]:
        return {"items": clean_items(args.get("items")), "shipping": shipping_of(args)}

    async def confirm_inner(args: dict[str, Any]) -> str:
        host, amount_value, cur = money_args(args)
        amount = money(amount_value, cur)
        summary = str(args["summary"]).strip()
        card = card_of(args)
        verdict = decided.pop((browser_owner.get(), current_call.get()), None)
        decision, grant, note = verdict if verdict else (None, None, "")
        why = why_text(decision, note)
        after = ("Do not add or change anything: if the total on the page is not exactly this, stop and call checkout_review again with "
                 "the new total. Afterwards read the confirmation page and call purchase_result with what it shows (order number, charged "
                 "total, items, cancel terms); then tell the user.")
        if decision is not None and decision.auto and grant is not None:
            # a run of a task the user allowed at setup to buy this, within its limits: no card; recorded, notified, logged
            payment = wallet.record("auto", card, host, summary, amount_value, cur, grant_id=grant["id"]) if wallet else {}
            via = card["label"] if card else (str(args.get("pay_with") or "").strip()[:60] or host)
            granted = {"id": grant["id"], "schedule_id": grant["schedule_id"], "scope": grant["scope"], "scope_ref": grant["scope_ref"],
                       "task_name": grant["task_name"], "via": via}
            await emit_ui({"type": "payment_auto", "payment": {**payment, **order_extra(args)}, **({"card": _brief(card)} if card else {}),
                           "schedule_grant": granted, "grant": granted})
            grants.use_purchase(grant, host, amount_value, cur)
            if card:
                arm(card, host, "server", None)
            if session:
                session.allow_purchase(host)
            fill = (f" If the page asks for card details, fill them with browser_fill_card(card={card['label']!r}) (never type a card "
                    "number yourself), then call checkout_submit with the review_id." if card else
                    " Now call checkout_submit with the review_id (it needs no second card).")
            return (f"APPROVED by the standing permission the user gave this task at setup (within its cap of "
                    f"{grant['max_amount_text']} per time): {summary}, total {amount}, on {host}. The user is notified.{fill} "
                    "If anything differs from what was expected (another total, new terms, an extra step that asks for money or an "
                    f"account change), stop and call checkout_review again or ask the user. {after}" + await after_ok(args, card, grant))
        if decision is not None and decision.auto and decision.reason in COVER_SOURCES:
            # the owner's pre-approval of this one order, or the allow they gave this schedule's payments: no card now
            payment = wallet.record("approved", card, host, summary, amount_value, cur) if wallet else {}
            via = card["label"] if card else (str(args.get("pay_with") or "").strip()[:60] or host)
            await emit_ui({"type": "payment_auto", "payment": {**payment, **order_extra(args)}, **({"card": _brief(card)} if card else {}),
                           "preapproval": {"kind": decision.reason, "via": via}})
            if card:
                arm(card, host, "server", None)
            if session:
                session.allow_purchase(host)
            why_cover = ("the order the user approved in advance (one order, within its maximum)" if decision.reason == SOURCE_PREAPPROVAL
                         else "the Allow the user gave this schedule's payments (same shop, same payment method)")
            fill = (f" If the page asks for card details, fill them with browser_fill_card(card={card['label']!r}) (never type a card "
                    "number yourself), then call checkout_submit with the review_id." if card else
                    " Now call checkout_submit with the review_id (it needs no second card).")
            return (f"APPROVED by {why_cover}: {summary}, total {amount}, on {host}. The user is notified.{fill} If anything differs from "
                    f"what was approved (another total, new terms, an extra step that asks for money), stop and call checkout_review "
                    f"again. {after}" + await after_ok(args, card, None))
        if wallet and card:
            fill = (f"If the page asks for card details, fill them with browser_fill_card(card={card['label']!r}) (never type a card "
                    "number yourself), then call checkout_submit with the review_id.")
            if decision is not None and decision.auto:
                payment = wallet.record("auto", card, host, summary, amount_value, cur)
                await emit_ui({"type": "payment_auto", "payment": {**payment, **order_extra(args)}, "card": _brief(card)})
                arm(card, host, "server", None)
                if session:
                    session.allow_purchase(host)
                return (f"APPROVED automatically by the user's auto-pay rules (within their limits, on a site they paid at before): "
                        f"{summary}, total {amount}, on {host}, card '{card['label']}'. The user is notified. {fill} {after}"
                        + await after_ok(args, card, None))
            secret: CardSecret | None = None
            source = "server"
            if not card["server_copy"]:
                approval = approval_of_current_call()
                if deliveries is None or not approval or not deliveries.has(approval):
                    raise WalletError(NO_CARD_SENT)
                deadline = deliveries.started(approval)
                await emit_ui({"type": "card_needed", "approval_id": approval, "card": _brief(card), "site": host, "amount": amount_value,
                               "currency": cur, "amount_text": amount, "expires_at": int(deadline or time.time() + card_wait)})
                if session:
                    await session.card(t("Waiting for card details"), force=True)
                secret = await deliveries.wait(approval, card_wait)
                if secret is None:
                    wallet.store.add_vault_log(card["label"], "card-not-sent", f"payment on {host} allowed, but the phone did not send the card", False)
                    raise WalletError(NO_CARD_SENT)
                source = "phone"
            wallet.record("approved", card, host, summary, amount_value, cur)
            arm(card, host, source, secret)
            if session:
                session.allow_purchase(host)
            return (f"APPROVED by the user: {summary}, total {amount}, on {host}, card '{card['label']}'.{why} {fill} {after}"
                    + await after_ok(args, card, None))
        if wallet:
            wallet.record("approved", None, host, summary, amount_value, cur)
        if session:
            session.allow_purchase(host)
        return (f"APPROVED by the user: {summary}, total {amount}, on {host}.{why} Now call checkout_submit "
                f"with the review_id (it needs no second card). {after}" + await after_ok(args, None, None))

    def pay_label(args: dict[str, Any]) -> str:
        """The payment method of a checkout_review in words: the wallet card's nickname, else the payment way the page showed."""
        card = card_of(args)
        return card["label"] if card else " ".join(str(args.get("pay_with") or "").split())[:60]

    def source_of() -> str:
        """What covers the payment being approved now: the task's grant, the auto-pay rule, or the owner's card answer."""
        verdict = decided.get((browser_owner.get(), current_call.get()))
        if verdict is not None and verdict[0].auto:
            if verdict[0].reason in COVER_SOURCES:
                return verdict[0].reason
            return "task_grant" if verdict[1] is not None else "auto_rule"
        return "card"

    tools = [Tool(
        "confirm_purchase",
        "Required right before the final click that pays, orders, books or signs up for something with a cost. Shows the user an "
        "Allow/Deny card with exactly what will be bought and the total. Only when it returns APPROVED may you make that click; if the "
        "user declines or does not answer, never pay.",
        {"type": "object", "properties": {
            "summary": {"type": "string", "description": "what is bought with its options, in the user's language"},
            "amount": {"type": "number", "description": "the total the page shows"}, "currency": {"type": "string", "description": "ISO code"},
            "site": {"type": "string", "description": "the shop's address"},
            "pay_with": {"type": "string", "description": "the payment way in words, never a card number"},
            "items": {"type": "array", "description": "each line of the order as the page shows it", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "options": {"type": "string"},
                "quantity": {"type": "integer"}, "price": {"type": "string"}}}},
            "ship_to": {"type": "string", "description": "the address the order goes to as the page shows it, when the site account's "
                                                         "saved address is used (leave out after browser_fill_shipping)"},
            "coupon": {"type": "string", "description": "a coupon or code applied, if any"},
            **({"card": {"type": "string", "description": "a wallet card's nickname (default card if neither card nor pay_with)"}} if wallet else {})},
         "required": ["summary", "amount", "site"]},
        confirm, requires_approval=True, describe=describe, validate=validate, declined=declined,
        auto_approve=auto if (wallet or grants is not None) else None, approval_extra=approval_extra,
    )]
    if session is not None:
        if pay_ledger is None:
            from ..store import Store
            pay_ledger = pay_terms.PayLedger(Store(":memory:"), clock)
        tools.extend(_checkout_tools(session, reviews if reviews is not None else Reviews(), confirm=confirm, validate=validate, auto=auto,
                                     describe=describe, declined=declined, approval_extra=approval_extra, money_args=money_args,
                                     ledger=pay_ledger, ask=ask, source_of=source_of, preapprovals=preapprovals, covers=covers,
                                     pay_label=pay_label))
    if ledger is not None and session is not None:
        tools.append(_result_tool(session, ledger, notify=notify, timezone=timezone, clock=clock, attempts=pay_ledger.attempts))
    if wallet is None:
        return tools

    async def wallet_cards(args: dict[str, Any]) -> str:
        cards = wallet.cards()
        if not cards:
            return ("The user's wallet has no cards. Pay with what the site holds for the user, or ask for a card with request_card: a "
                    "secure card entry appears in the chat below your message. Never ask for card numbers in chat.")
        lines = []
        for c in cards:
            if c["expired"]:
                state = "EXPIRED: cannot pay"
            elif not c["usable"]:
                state = "details not finished in the app yet: cannot pay"
            elif c["pays_automatically"]:
                state = ("pays automatically within the user's auto-pay limits; it still asks for the first payment on a new site and "
                         "for a payment over a limit")
            else:
                state = f"ASKS EACH TIME [{c['ask_reason']}]: {EXPLAIN.get(c['ask_reason'], c['ask_reason_text'])}"
            mark = " [DEFAULT card]" if c.get("default") else ""
            lines.append(f"- '{c['label']}' ({c['brand']}, ends {c['last4']}, expires {c['exp_month']:02d}/{c['exp_year']}){mark}: {state}")
        head = "Cards in the user's wallet (by the user's nickname; you never see card numbers):\n"
        default = next((c for c in cards if c.get("default")), None)
        which = (f"Pay with the default card '{default['label']}' unless the user or the task names another card"
                 if default else "Use the card the user named; if they named none and there is more than one, ask which")
        return (head + "\n".join(lines) + f"\n{which}. Pass its nickname as checkout_review(card=...) (leaving card and pay_with out "
                "uses the default card). When you tell the user whether a card pays automatically, repeat only what this list says; "
                "never claim anything else about their settings.")

    def fill_validate(args: dict[str, Any]) -> str | None:
        if CARD_NUMBER.search(str(args.get("card") or "")):
            return "never put a card number in a request; name the card by its label from wallet_cards"
        if session is None:
            return "there is no browser"
        card = card_of(args)
        if card is None:
            return f"no card {str(args.get('card') or '')[:40]!r} in the wallet. Cards: {names() or 'none'}"
        a = arms.get(browser_owner.get())
        if a is None or a.card_id != card["id"] or not a.live():
            return (f"no approved payment with '{card['label']}' is open. Call checkout_review with card={card['label']!r} first; "
                    "the card can be typed only right after that")
        if not session.purchase_active(a.host):
            return "the approval for this payment ran out (time or steps). Call checkout_review again"
        here = session.current_host()
        if not _same_site(here, a.host):
            return f"the browser is on {here or 'a blank page'}, but the payment was approved on {a.host}. Card details go only to that site"
        return None

    async def fill_card(args: dict[str, Any]) -> str:
        card = card_of(args)
        a = arms.get(browser_owner.get())
        assert card is not None and a is not None and session is not None   # checked by fill_validate
        secret = a.secret if a.source == "phone" else wallet.open_secret(card["id"])
        if secret is None or not secret.number:
            return ("error: the card details are no longer here (a card the phone sends is typed once). Call checkout_review again "
                    "if the payment still needs them")
        try:
            result = await session.fill_card(secret.number, secret.cvc, card["exp_month"], card["exp_year"], card["holder"], card["postal_code"])
        except BrowserPolicyError as e:
            return f"error: {e}"
        finally:
            if a.source == "server":
                secret.wipe()
        typed_number = any(f.startswith("number") for f in result.filled)
        if typed_number and a.source == "phone":
            a.wipe()
        if result.filled:
            if paths is not None and typed_number:
                paths.record(a.host, "card_form", "smooth")   # a protected keypad met later in this payment overrides it
            wallet.touch(card["id"])
            wallet.store.add_vault_log(card["label"], "card-filled", f"typed into the checkout on {session.current_host()}")
        await session.card(t("Card details entered") if result.filled else t("Looking for the card fields"), force=True)
        if not result.filled:
            head = (f"No card fields were found on this page{' or its frames' if result.unreachable else ''}"
                    f"{' (could not reach: ' + ', '.join(result.unreachable) + ')' if result.unreachable else ''}. Nothing was typed. "
                    "Choose card payment on the page first if there is such a choice. If the site sends you to its own payment app or a "
                    "separate window, tell the user what is left for them to do.")
        else:
            head = f"Filled in card ••{card['last4']}: {', '.join(result.filled)}."
            if result.missing:
                head += f" Not found: {', '.join(result.missing)} (they may come on the next step, or the site may not need them)."
            if result.unreachable:
                head += f" Frames that could not be reached: {', '.join(result.unreachable)}."
            head += " Check the page, then call checkout_submit with the review_id."
        if result.auth:
            head += "\n" + EXTRA_AUTH_NOTE.format(words=result.auth)
        return head + "\n\n" + data(result.text)   # only the page is data (fenced by the loop); the lines above are this app's own

    tools.append(Tool("wallet_cards", "List the cards in the user's wallet by nickname and last 4 digits (never a number), which one "
                      "is the default, and whether each pays automatically or needs approval.",
                      {"type": "object", "properties": {}}, wallet_cards, read_only=True))
    tools.append(Tool("browser_fill_card", "Type a wallet card into the checkout form of the page you are on, only right after "
                      "checkout_review approved paying with that card on that site. You never see the values.",
                      {"type": "object", "properties": {"card": {"type": "string", "description": "the card's nickname"}}, "required": ["card"]},
                      fill_card, validate=fill_validate))
    return tools


def _same_site(a: str, b: str) -> bool:
    """One site: the same registrable domain by the Public Suffix List (sites.py), so 'pay.example.co.kr' is the site of
    'www.example.co.kr' and 'badexample.co.kr' is not."""
    return same_site(a, b)


# ---------------------------------------------------------------- checkout_review / checkout_submit (tools/checkout.py, pay_terms.py)
def _confirm_args(args: dict[str, Any]) -> dict[str, Any]:
    """checkout_review's arguments in the shape the approval machinery above reads (summary, amount, pay_with, items with quantity)."""
    raw = [{"name": it.get("name"), "options": ", ".join(str(v) for v in it.get("variants") or []) if isinstance(it.get("variants"), list) else it.get("options"),
            "quantity": it.get("qty", it.get("quantity")), "price": it.get("unit_price", it.get("price"))}
           for it in (args.get("items") if isinstance(args.get("items"), list) else []) if isinstance(it, dict)]
    merchant = " ".join(str(args.get("merchant") or "").split())[:80]
    lines = ", ".join(f"{i['name']} x{i['quantity']}" for i in clean_items(raw))
    out: dict[str, Any] = {"summary": ": ".join(x for x in (merchant, lines) if x)[:MAX_SUMMARY], "amount": args.get("total"),
                           "currency": args.get("currency"), "site": args.get("site"), "pay_with": args.get("payment_method"), "items": raw}
    for key in ("card", "ship_to", "coupon"):
        if key in args:
            out[key] = args[key]
    return out


def current_turn_unattended() -> bool:
    """The run going on is one nobody watches (a schedule, a job)."""
    from ..workers import current_turn
    return bool((current_turn.get() or {}).get("unattended"))


def _task_key() -> str:
    from ..workers import current_turn
    return str((current_turn.get() or {}).get("task_id") or "")


def _job_tab() -> bool:
    """A background job's own tab: its attempts are one checkout across all its runs (pick-ups included)."""
    return browser_owner.get().startswith("task:w")


def _owner_request() -> str:
    """The owner's own request words for this task (its request, or a saved job's request): never outside text."""
    from ..workers import current_turn
    return str((current_turn.get() or {}).get("request") or "")


# the line a payment card of a scheduled run adds: what its Allow also covers (phase P3)
SCHEDULE_ALLOW_NOTE = N_("Allow also covers this schedule's later orders from this shop with this payment method")


# what the model is told for each way the press is refused: nothing was paid, and what to do
PRESS_REFUSALS = {
    pay_terms.ORIGIN: "Nothing was paid: the page is on another site than the one the order was approved for. Go back to the approved shop "
                      "and call checkout_review again.",
    pay_terms.AMOUNT: "Nothing was paid: the amount due on the page is higher than the approved total. Read the page, find out why, and "
                      "call checkout_review again with the total the page really shows (never state a higher total to get it through; "
                      "the owner approves it again).",
    pay_terms.CURRENCY_CHANGED: "Nothing was paid: the page's currency is not the approved one. Call checkout_review again with what the "
                                "page shows now.",
    pay_terms.CONDITIONAL: "Nothing was paid: a conditional charge on the page is higher than approved. Call checkout_review again.",
    pay_terms.EXPIRED: "Nothing was paid: the approval has expired. Call checkout_review again (status ready) to ask again.",
    pay_terms.STATE: "Nothing was paid: that approval is no longer valid (used, declined or revoked). Call checkout_review again.",
    pay_terms.SUPERSEDED: "Nothing was paid: the order terms were changed after the approval. Call checkout_review with status draft for "
                          "the current terms, then ready.",
    pay_terms.DIGEST: "Nothing was paid: the order terms differ from the approved ones. Review the order again.",
    pay_terms.PREAPPROVAL: "Nothing was paid: the owner's advance approval for this order was removed or switched off. Call checkout_review "
                           "again (status ready) to ask for the payment as usual.",
}


# the blocks while an order of this task may have gone through (a press happened and its outcome is not settled)
ORDER_MAY_EXIST = ("An order may already have gone through: a pay control of this task was pressed and its outcome is {state}. "
                   "Find it first (the confirmation page, the shop's order list, the owner's mail), then call purchase_result with the "
                   "outcome and the evidence you saw. ")
BLOCK_REVIEW = ORDER_MAY_EXIST + "No new payment approval is asked until it is recorded as not placed, with its evidence."
BLOCK_SUBMIT = ORDER_MAY_EXIST + "Nothing was pressed. A second press or a replacement purchase waits for not_placed with its evidence."
BLOCK_NAVIGATE = ORDER_MAY_EXIST + "This tab is not reloaded or navigated meanwhile: the confirmation may be on it. Read it with browser_read."


def _checkout_tools(session: BrowserSession, store: Reviews, *, confirm: Any, validate: Any, auto: Any, describe: Any, declined: Any,
                    approval_extra: Any, money_args: Any, ledger: pay_terms.PayLedger, ask: Any, source_of: Any,
                    preapprovals: Preapprovals | None = None, covers: dict[tuple[str, str], tuple[str, str]] | None = None,
                    pay_label: Any = None) -> list[Tool]:
    """The two tools that replace the final pay click.

    checkout_review(status=draft) records the order terms and runs the ORDER CHECK (order_check.py); no card. checkout_review(status=ready)
    needs a passed check on the same terms (or a change of only the total after one), then finds cover (the owner's task grant or
    auto-pay rule, else the approval card: a payment card, signed on the phone) and writes the payment approval; its id is the review_id.
    Phase P3 (`preapprovals`, pay_preapprovals.py): a one-order pre-approval the order check matched, or a schedule's payment allow
    (same origin, same payment method), covers the payment with no card; `covers` hands that to the purchase judge.
    checkout_submit re-reads the live page and refuses (nothing pressed) when the approval no longer covers it, then presses the control
    recorded at the review. P2 hook: the attempt journal row is written between the re-check and the press."""
    found: dict[tuple[str, str], tuple[Any, str, str]] = {}   # (device, call id) -> (page probe, control role, control label)
    attempts = ledger.attempts
    covers = covers if covers is not None else {}

    def open_attempt() -> dict[str, Any] | None:
        return attempts.open(browser_owner.get(), _task_key(), any_task=_job_tab())

    def nav_block() -> str | None:
        if CHECKING.get():   # the later check of an unknown order reads and opens the tab itself
            return None
        a = open_attempt()
        return BLOCK_NAVIGATE.format(state=a["state"]) if a is not None else None

    session.nav_block = nav_block

    def key() -> tuple[str, str]:
        return browser_owner.get(), current_call.get()

    async def describe_ref(ref: str) -> dict[str, str] | None:
        try:
            page = await session.page()
            owner = await session.frame_for(page, ref)
            got = await owner.locator(ref_selector(ref)).first.evaluate(DESCRIBE_JS, timeout=3000)
        except Exception:  # noqa: BLE001 - a ref that cannot be read is refused below
            return None
        return got if isinstance(got, dict) and got.get("label") else None

    def is_ready(args: dict[str, Any]) -> bool:
        return str(args.get("status") or "draft") == "ready"

    def schedule_run() -> int | None:
        from ..schedules import current_schedule_id
        return current_schedule_id()

    def my_spec() -> dict[str, Any] | None:
        """The owner's active one-order pre-approval for this schedule's run, or for this task (None: none, or the switch is off)."""
        if preapprovals is None or not preapprovals.enabled():
            return None
        sid = schedule_run()
        return preapprovals.active("schedule", str(sid)) if sid else preapprovals.active("task", _task_key())

    def cover_for(args: dict[str, Any]) -> tuple[str, str] | None:
        """(source, id) when the owner's pre-approval or this schedule's payment allow covers these terms with no card, else None."""
        if preapprovals is None or not preapprovals.enabled() or key() not in found:
            return None
        terms, _ = terms_of(args)
        if terms is None:
            return None
        spec = my_spec()
        if spec is not None and preapprovals.covers(spec, terms):
            return SOURCE_PREAPPROVAL, spec["id"]
        sid = schedule_run()
        if sid and pay_label is not None:
            allow = preapprovals.allow_for(sid, terms.merchant_origin, pay_label(_confirm_args(args)), terms.currency)
            if allow is not None:
                return SOURCE_ALLOW, allow["id"]
        return None

    def set_cover(args: dict[str, Any]) -> None:
        if len(covers) > 50:
            covers.clear()
        found_cover = cover_for(args)
        if found_cover is None:
            covers.pop(key(), None)
        else:
            covers[key()] = found_cover

    async def review_hold(args: dict[str, Any]) -> str | None:
        held = await session.hold_for_user(args)
        if held:
            return held
        total = _amount(args.get("total"))
        if total is None or total < 0:
            return None   # validate names the missing total
        probe = await read_page(session)
        if probe is None:
            return "The page could not be read. Open the checkout page and read it, then call checkout_review again."
        if total and not total_shown(probe.text, total):
            return (f"The page shows no total of {args.get('total')}. Read the page and call checkout_review with the total it shows "
                    "(copy it as written), or stop if the page does not show the order's total yet.")
        if not is_ready(args):   # a draft records the terms; no control is needed yet
            if len(found) > 50:
                found.clear()
            found[key()] = (probe, "", "")
            return None
        ref = str(args.get("ref") or "").strip()
        if ref:
            chosen = await describe_ref(ref)
            if chosen is None:
                return f"The control {ref!r} could not be read on this page. Read the page again and pass the ref of the pay control."
        else:
            submits = [c for c in probe.controls if c.get("submit") and c.get("pay_form")] or [c for c in probe.controls if c.get("submit")] \
                or probe.controls
            if not submits:
                return ("No pay control was found by the page's structure (a submit button of the checkout form, or a button next to the "
                        "order total). Pass `ref`: the ref of the pay control from your last reading. If the pay button is inside the "
                        "payment provider's frame, the user finishes the payment themselves.")
            chosen = submits[-1]
        want = {"role": str(chosen.get("role") or ""), "label": str(chosen.get("label") or "")}
        pinned = await read_page(session, want)
        if pinned is None or pinned.marked is not True:
            return ("The pay control could not be pinned down: more than one control has the same role and name, or it is not "
                    "visible. Pass `ref` of the one that pays, after reading the page again.")
        if len(found) > 50:
            found.clear()
        found[key()] = (probe, want["role"], want["label"])
        return None

    def terms_of(args: dict[str, Any]) -> tuple[pay_terms.Terms | None, str]:
        probe = found[key()][0]
        return pay_terms.terms_from_args(args, origin_of(probe.url))

    def review_validate(args: dict[str, Any]) -> str | None:
        if str(args.get("status") or "draft") not in ("draft", "ready"):
            return "status must be draft (record the terms and run the order check) or ready (ask for the payment approval)"
        if _amount(args.get("total")) is None or _amount(args.get("total")) < 0:   # type: ignore[operator]
            return "total is required: the order's total the page shows, as a number (0 if it is free)"
        if not isinstance(args.get("items"), list) or not args["items"]:
            return "items is required: every line of the order as the page shows it, [{name, qty, unit_price, variants, obligations}]"
        if args.get("merchant") is not None and not str(args.get("merchant")).strip():
            return "merchant must name the shop or leave it out"
        if key() not in found:
            return "the page was not checked: open the checkout page, read it, and call checkout_review again"
        terms, why = terms_of(args)
        if terms is None:
            return why
        if (problem := validate(_confirm_args(args))) is not None:
            return problem
        if is_ready(args):
            if (a := open_attempt()) is not None:
                return BLOCK_REVIEW.format(state=a["state"])
            sid = schedule_run()
            if (preapprovals is not None and sid and preapprovals.enabled() and preapprovals.lapsed("schedule", str(sid))
                    and (current_turn_unattended()) and cover_for(args) is None):
                return ("The owner's pre-approval for this schedule's order has expired and nothing else covers this payment. Nothing was "
                        "paid. Stop here and say so in the run's report; a scheduled run never asks for a new approval.")
            latest = ledger.latest(browser_owner.get(), _task_key())
            if pay_terms.check_needed(latest, terms):
                return ("the order check has not passed for these terms (items, variants, quantities, delivery, dates, address, "
                        "cancellation, other obligations). Call checkout_review with status draft first, act on its answer, then call "
                        "it with status ready and the same terms")
        return None

    async def draft(args: dict[str, Any]) -> str:
        terms, _ = terms_of(args)
        assert terms is not None
        found.pop(key(), None)
        owner, task = browser_owner.get(), _task_key()
        latest = ledger.latest(owner, task)
        spec = my_spec()
        unmatched = spec is not None and (spec["matched_digest"] != terms.digest or spec["matched_origin"] != terms.merchant_origin)
        if not pay_terms.check_needed(latest, terms) and not unmatched:   # only the total (or the payment way) changed after a passed check: a terms update
            ledger.add_terms(owner, task, terms, "draft", float(latest["checked_at"]))   # type: ignore[index]
            return json.dumps({"status": "draft", "verdict": "confirm", "reason": "terms update: only the total changed after a passed "
                               "order check"}) + "\nNow call checkout_review with status ready and the same terms to ask for the payment approval."
        tid = ledger.add_terms(owner, task, terms, "draft", None)
        verdict = await order_check.run(ask, _owner_request(), terms, spec["spec"] if spec is not None else None)
        if verdict["verdict"] == "confirm":
            ledger.mark_checked(tid)
            if spec is not None and verdict.get("within_spec") is True:   # the one place a pre-approved spec is compared to a cart
                preapprovals.matched(spec["id"], terms.digest, terms.merchant_origin)   # type: ignore[union-attr]
            nxt = ("Now call checkout_review with status ready and the same terms to ask for the payment approval. Nothing is paid or "
                   "approved yet.")
        elif verdict["verdict"] == "change":
            nxt = ("The cart does not match the request. Fix the cart as described in `change` (never pay it as it is), read the page, "
                   "and call checkout_review with status draft again.")
        else:
            nxt = ("The request leaves a choice open that only the owner decides: ask the owner in the chat and wait for the answer; in a "
                   "run nobody watches, stop here, leave the order unpaid and name the open choice in your report. Do not pick for them.")
        return json.dumps({"status": "draft", **verdict}, ensure_ascii=False) + "\n" + nxt

    async def ready(args: dict[str, Any]) -> str:
        terms, _ = terms_of(args)
        assert terms is not None
        prior = prior_cover(args)
        probe, role, label = found.pop(key())
        owner, task = browser_owner.get(), _task_key()
        latest = ledger.latest(owner, task)
        if prior is not None:   # only the total (or the payment way) changed and it is within what the owner approved: no new ask
            out = "The approval you already have covers this total; the owner is not asked again."
            tid = ledger.add_terms(owner, task, terms, "ready", float(latest["checked_at"]))   # type: ignore[index]
            pid = ledger.add_approval(terms, tid, owner, task, prior["source"], prior["approval_id"], bool(prior["signed"]),
                                      float(prior["expires_at"]), prior["approved_total_minor"], prior["approved_conditional_minor"])
            ledger.set_state(prior["id"], "revoked")   # one live approval per order: the new row replaces it
        else:
            source = source_of()   # task_grant / auto_rule / card, or a P3 cover (preapproval, schedule_allow)
            cover = covers.pop(key(), None) if source in COVER_SOURCES else None
            covers.pop(key(), None)
            out = await confirm(_confirm_args(args))
            tid = ledger.add_terms(owner, task, terms, "ready", float(latest["checked_at"]))   # type: ignore[index]
            card = approval_of_current_call() or None
            expires = ledger.clock() + pay_terms.APPROVAL_SECONDS
            if cover is not None:   # the approval carries the cover's id; a pre-approval lives until the spec's expiry
                card = cover[1]
                if source == SOURCE_PREAPPROVAL and (row := preapprovals.get(cover[1])) is not None:   # type: ignore[union-attr]
                    expires = float(row["expires_at"])
            pid = ledger.add_approval(terms, tid, owner, task, source, card, bool(card) and source == "card", expires)
            sid = schedule_run()
            if (source == "card" and sid and preapprovals is not None and preapprovals.enabled() and pay_label is not None):
                # the owner's Allow on a scheduled task's payment card also covers its later purchases: same shop, same payment method
                preapprovals.allow_add(sid, terms.merchant_origin, pay_label(_confirm_args(args)), terms.currency, card or "")
        host, total, cur = money_args(_confirm_args(args))
        store.add(owner, host, probe, role, label, total, cur, rid=pid).approved = True
        return (json.dumps({"review_id": pid, "approved": True}) + f"\nRecorded pay control: {role} {label!r} on {probe.path}. "
                f"Pay with checkout_submit(review_id={pid!r}); never with a plain click. " + out)

    async def review(args: dict[str, Any]) -> str:
        return await (ready(args) if is_ready(args) else draft(args))

    def prior_cover(args: dict[str, Any]) -> dict[str, Any] | None:
        """An approval of this task that still covers these (ready) terms: same order, same site and currency, total within it."""
        if key() not in found:
            return None
        terms, _ = terms_of(args)
        prior = ledger.live_approval(browser_owner.get(), _task_key())
        return prior if terms is not None and prior is not None and pay_terms.covers_terms(prior, terms, ledger.clock()) else None

    def gate_args(fn: Any) -> Any:
        # a draft asks for nothing (the approval comes with ready); a ready inside an earlier approval's amounts asks nothing again;
        # a ready the owner's pre-approval or this schedule's allow covers is decided by the purchase judge (covers)
        def gate(args: dict[str, Any]) -> bool:
            if not is_ready(args) or prior_cover(args) is not None:
                return True
            set_cover(args)
            return bool(fn(_confirm_args(args)))
        return gate

    async def describe_review(args: dict[str, Any]) -> str:
        line = await describe(_confirm_args(args))
        if preapprovals is not None and schedule_run() and preapprovals.enabled():   # the owner is told what Allow also covers
            line += " · " + t(SCHEDULE_ALLOW_NOTE)
        return line

    def review_extra(approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        return approval_extra(approval_id, _confirm_args(args))

    item = {"type": "object", "properties": {
        "name": {"type": "string"}, "qty": {"type": "integer"}, "unit_price": {"type": "string"},
        "variants": {"type": "array", "items": {"type": "string"}, "description": "size, colour, plan: every choice made for the line"},
        "obligations": {"type": "array", "items": {"type": "string"}, "description": "what the line commits to (a subscription, a deposit)"}},
        "required": ["name"]}
    review_tool = Tool(
        "checkout_review",
        "Required before paying, ordering or booking anything with a cost, in two calls with the same terms. status draft (default): "
        "records the order terms and checks the cart against what the owner asked; answers confirm, change or open_choice; asks nobody. "
        "status ready (only after confirm): asks for the payment approval unless the owner's standing permission or auto-pay covers it; "
        "returns {review_id, approved: true}; then pay with checkout_submit. The page must show the total. A change of items, variants, "
        "quantities, delivery, dates, address or cancellation terms needs a new draft; a higher total, another currency or another site "
        "needs a new approval. On a checkout page the plain click and press tools refuse.",
        {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["draft", "ready"]},
            "site": {"type": "string", "description": "the shop's address"},
            "merchant": {"type": "string", "description": "the shop's name"},
            "items": {"type": "array", "description": "each line of the order as the page shows it", "items": item},
            "total": {"type": "number", "description": "the total the page shows (what is due now, with shipping and tax)"},
            "currency": {"type": "string", "description": "ISO 4217 code of the page's currency (never guessed from a symbol)"},
            "conditional_total": {"type": "number", "description": "the most that may be charged later under conditions (a deposit, a fee), if any"},
            "conditional_terms": {"type": "string", "description": "when that conditional charge applies"},
            "delivery": {"type": "string", "description": "the delivery way and speed as the page shows it"},
            "dates": {"type": "string", "description": "dates of the order (delivery, stay, event)"},
            "cancellation": {"type": "string", "description": "the cancellation or return terms as the page shows them"},
            "other_obligations": {"type": "array", "items": {"type": "string"}, "description": "any other commitment the order makes"},
            "payment_method": {"type": "string", "description": "the payment way in words, never a card number"},
            "card": {"type": "string", "description": "a wallet card's nickname (default card if neither card nor payment_method)"},
            "ship_to": {"type": "string", "description": "the address the order goes to as the page shows it (kept only as a digest)"},
            "coupon": {"type": "string", "description": "a coupon or code applied, if any"},
            "ref": {"type": "string", "description": "only for ready, when the pay control is not the checkout form's submit button: its ref"}},
         "required": ["site", "items", "total", "currency"]},
        review, requires_approval=True, hold=review_hold, validate=review_validate, describe=describe_review, declined=declined_review(declined),
        auto_approve=gate_args(auto), approval_extra=review_extra)

    def submit_validate(args: dict[str, Any]) -> str | None:
        rv = store.get(str(args.get("review_id") or ""))
        if rv is None:
            return "no such review (or it is older than 20 minutes): call checkout_review again"
        if rv.owner != browser_owner.get():
            return "that review belongs to another browser tab"
        if not rv.approved:
            return "that review was not approved"
        if rv.used:
            return "that review was already submitted: read the confirmation page and call purchase_result"
        if (a := open_attempt()) is not None:
            return BLOCK_SUBMIT.format(state=a["state"])
        return None

    def refused(code: str) -> Any:
        return Ours(PRESS_REFUSALS[code])

    async def submit(args: dict[str, Any]) -> Any:
        rv = store.get(str(args["review_id"]))
        assert rv is not None
        approval = ledger.approval(rv.id)
        if approval is None:
            return Ours("Nothing was paid: no payment approval is recorded for that review. Call checkout_review again.")
        changed = Ours("Nothing was paid: the page changed: review again (call checkout_review once more with what the page shows now)")
        probe = await read_page(session, {"role": rv.role, "label": rv.label})
        if probe is None:
            return changed
        origin = origin_of(probe.url)
        cur = pay_terms.iso_currency(probe.currency)
        over = amount_over(probe.totals, approval["approved_total_minor"] / 10 ** pay_terms.exponent(approval["currency"]))
        amount = pay_terms.minor_units(over, approval["currency"]) if over is not None else None
        code = pay_terms.refusal_code(approval, ledger.terms(approval["terms_id"]), origin=origin, amount_minor=amount, currency=cur,
                                      conditional_minor=None, now=ledger.clock())   # no page shows a conditional charge in its structure
        if code is not None:
            return refused(code)
        if preapprovals is not None and not preapprovals.still_valid(approval):   # removed, revoked or switched off since the review
            return refused(pay_terms.PREAPPROVAL)
        if probe.path != rv.path or probe.marked is not True or (rv.total and not total_shown(probe.text, rv.total) and over is None):
            return changed
        aid = attempts.reserve(approval, browser_owner.get(), _task_key())   # the journal row comes before the press
        if preapprovals is not None and approval["source"] == SOURCE_PREAPPROVAL:
            preapprovals.mark_used(approval["approval_id"])   # one order
        try:
            text = await session.click(probe.target)
        except BaseException:
            attempts.settle(aid, "unknown", "the press was started and then failed or was cut off: nobody saw whether it went through")
            rv.used = True
            ledger.set_state(approval["id"], "used")
            raise
        attempts.pressed(aid)
        rv.used = True
        ledger.set_state(approval["id"], "used")
        await session.card(t("Placing the order"), force=True)
        return ToolOutput(text=text, head=f"Pressed the recorded pay control ({rv.role} {rv.label!r}). ",
                          tail=" Read the confirmation page and call purchase_result with the outcome (placed only when the shop's own confirmation shows "
                               "the order; not_placed with what you saw; unknown when neither is clear), then tell the user.")

    submit_tool = Tool(
        "checkout_submit", "Press the pay control recorded by an approved checkout_review (status ready). Before it presses, it re-reads the "
        "page and refuses (nothing is paid) when the site, the amount due, the currency or the order terms differ from what was approved, "
        "or the approval expired. Returns the page after the press.",
        {"type": "object", "properties": {"review_id": {"type": "string"}}, "required": ["review_id"]},
        submit, untrusted=True, source="page", validate=submit_validate, hold=session.hold_for_user)
    return [review_tool, *session.with_deadline([submit_tool])]


def declined_review(declined: Any) -> Any:
    return lambda args: declined(_confirm_args(args))


# ---------------------------------------------------------------- purchase_result: the receipt against what was approved
RESULT_CHECKED = "Checked: matches what was approved."


def _result_tool(session: BrowserSession, ledger: Any, *, notify: Any = None, timezone: Any = None, clock: Any = time.time,
                 attempts: Any = None) -> Tool:
    """After a payment: the confirmation page's order number, total, items and cancel terms, checked against what was approved
    (assistant.receipt). Owner's rules: a charge that differs (higher total, another item, option or quantity) is cancelled at once when
    cancelling is free now (the cancel window of BrowserSession.allow_cancel opens, the owner gets an urgent notice); with a fee, or when
    that is unknown, the owner is asked first (an approval card, Allow = cancel the order, Deny = keep it) and nothing is cancelled
    before the answer. Every report states the approved condition and the real charge plainly, and the next option."""
    from ..receipt import cancel_terms, compare

    def pay() -> Any:
        return ledger.last(browser_owner.get())

    def current_task() -> int | None:
        from ..workers import current_turn
        return (current_turn.get() or {}).get("task_id")

    def tz() -> str:
        try:
            return str(timezone() or "") if timezone is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def outcome_of(args: dict[str, Any]) -> str:
        return str(args.get("outcome") or "placed")

    def journal_open() -> dict[str, Any] | None:
        """The attempt of this owner/task that still waits for its outcome (pressed or unknown), None when there is none. A job's tab
        counts every run of the job: a picked-up run sees the press of the run it was cut from (job_cuts.py)."""
        return attempts.open(browser_owner.get(), _task_key(), any_task=_job_tab()) if attempts is not None else None

    def validate(args: dict[str, Any]) -> str | None:
        out = outcome_of(args)
        if out not in ("placed", "not_placed", "unknown"):
            return "outcome must be placed, not_placed or unknown"
        if out != "placed" and journal_open() is None:
            return "no pressed or unknown attempt of this task is waiting for an outcome"
        if out == "placed" and not str(args.get("order_no") or "").strip() and args.get("cancelled") is not True:
            return "order_no is required: the order, booking or receipt number as the page shows it"
        if out != "placed" or (journal_open() is not None and args.get("cancelled") is not True):
            evidence = " ".join(str(args.get("evidence") or "").split())
            if not evidence:
                return ("evidence is required: for placed, the words of the SHOP's own confirmation (an order number or confirmation "
                        "text on the shop's page; a spinner, an accepted card or a payment provider's success page is not one); for "
                        "not_placed, what you saw that shows nothing was ordered (e.g. the cart is still full, an error on the page); "
                        "for unknown, what you saw")
            if CARD_NUMBER.search(evidence):
                return "it contains what looks like a card number. Never put card numbers in a request"
        if out != "placed":
            return None
        p = pay()
        if p is None:
            return ("no approved payment here: purchase_result checks the receipt of a payment that checkout_review approved in this "
                    "task (within the last half hour), on this browser tab")
        if any(CARD_NUMBER.search(str(args.get(k) or "")) for k in ("order_no", "charged_total", "items", "cancel")):
            return "it contains what looks like a card number. Never put card numbers in a request"
        if args.get("cancelled") is True or p.state == "closed":
            return None
        from ..receipt import parse_money
        if parse_money(args.get("charged_total"))[0] is None:
            return "charged_total is required: the total the confirmation page shows, as the page writes it (number and currency)"
        if args.get("items") is not None and not isinstance(args.get("items"), list):
            return "items must be a list of {name, options, quantity}"
        if p.items and not clean_items(args.get("items")):
            return "items is required: every line of the receipt (name, options, quantity), copied from the confirmation page"
        if args.get("cancel") is not None and not isinstance(args.get("cancel"), dict):
            return "cancel must be an object {free_until, fee, how} copied from the page, or null when the page gives no cancel terms"
        return None

    def judge(args: dict[str, Any]) -> tuple[str, Any]:
        """(kind, details): cancelled | closed | match | lower | free (cancel now, free) | ask (the owner decides)."""
        p = pay()
        if args.get("cancelled") is True:
            return "cancelled", None
        if p.state == "closed":
            return "closed", None
        chk = compare(p.amount, p.currency, p.items, args.get("charged_total"), clean_items(args.get("items")))
        if chk.ok:
            return ("match" if chk.status == "match" else "lower"), chk
        terms = cancel_terms(args.get("cancel"), clock(), tz())
        # free now by the page's own words / deadline; or the owner already chose to cancel (state "cancel")
        return ("free" if terms.free_now or p.state == "cancel" else "ask"), (chk, terms)

    def auto(args: dict[str, Any]) -> bool:
        return outcome_of(args) != "placed" or judge(args)[0] != "ask"

    def settle(args: dict[str, Any], state: str) -> None:
        """Record the outcome of the open attempt with its evidence (the merchant's confirmation for placed)."""
        a = journal_open()
        if a is None or attempts is None:
            return
        text = " ".join(str(args.get("evidence") or "").split())
        digest = hashlib.sha256(f"{args.get('order_no') or ''}|{args.get('charged_total') or ''}|{text}".encode()).hexdigest()
        attempts.settle(a["id"], state, text, receipt=text, digest=digest)

    def _diff_text(p: Any, chk: Any) -> str:
        parts = list(chk.problems)
        if chk.difference:
            parts.insert(0, f"{'+' if chk.difference > 0 else '-'}{money(abs(chk.difference), p.currency)}")
        return "; ".join(parts) or "-"

    def _until_text(terms: Any) -> str:
        if terms.until is not None:
            return str(terms.until_text or time.strftime("%Y-%m-%d %H:%M", time.localtime(terms.until)))
        return terms.until_text or t("not stated on the page")

    def title_of(p: Any, chk: Any) -> str:
        return t("Charged {charged} instead of {approved} at {site}", charged=chk.charged_text, approved=p.amount_text, site=p.host)

    def approval_extra(approval_id: str, args: dict[str, Any]) -> dict[str, Any]:
        p = pay()
        if p is None or (got := judge(args))[0] != "ask":
            return {}
        chk, terms = got[1]
        lines = [{"label": t("Difference"), "value": _diff_text(p, chk)},
                 {"label": t("Cancel fee"), "value": terms.fee or t("not stated on the page")},
                 {"label": t("Free or cheaper until"), "value": _until_text(terms)},
                 {"label": t("Order number"), "value": " ".join(str(args.get("order_no") or "").split())[:60] or t("not shown")}]
        return {"ask": title_of(p, chk), "notice_title": title_of(p, chk), "lines": lines, "urgent": True,
                "deadline_at": terms.until, "charge_check": {"site": p.host, "approved": p.amount_text, "charged": chk.charged_text,
                                                             "allow": t("Cancel the order"), "deny": t("Keep")}}

    async def describe(args: dict[str, Any]) -> str:
        p = pay()
        if p is None or (got := judge(args))[0] != "ask":
            return t("Checking the receipt at {site}", site=p.host if p else "")
        chk, terms = got[1]
        return t("{title} · Difference: {difference} · Cancel fee: {fee} · Free or cheaper until: {until} · Allow = cancel the order, Deny = keep it",
                 title=title_of(p, chk), difference=_diff_text(p, chk), fee=terms.fee or t("not stated on the page"),
                 until=_until_text(terms))[:300]

    def declined(args: dict[str, Any]) -> str:
        p = pay()
        if p is None:
            return "The owner did not choose to cancel."
        p.state = "closed"
        session.end_cancel()
        return ("The owner chose to KEEP the order (or did not answer in time). Do NOT cancel it. Tell the owner plainly: approved "
                f"{p.amount_text} ({p.summary}), charged {args.get('charged_total')}, what differs, and that they can still cancel it "
                "themselves or tell you to. Do not buy again.")

    def sums(p: Any, chk: Any, args: dict[str, Any]) -> str:
        return f"approved {p.amount_text} ({p.summary}), charged {chk.charged_text}; {_diff_text(p, chk)}"

    async def result(args: dict[str, Any]) -> str:
        if (out := outcome_of(args)) != "placed":
            a = journal_open()
            settle(args, out)
            if out == "not_placed":
                return ("Recorded: the order was NOT placed (evidence kept). A retry is allowed now: call checkout_review again, "
                        "a new payment approval is asked for. Tell the owner what happened.")
            if a is not None and attempts.get(a["id"])["state"] == "unknown":
                return ("Recorded: the outcome is UNKNOWN. Do NOT press again, do NOT buy again and do NOT reload the tab. Kelo looks for "
                        "the order in this browser task in about 10 minutes and again later; tell the owner it is being checked.")
            return "Recorded."
        p = pay()
        kind, data = judge(args)
        if kind not in ("cancelled", "closed"):
            settle(args, "placed")
        order_no = " ".join(str(args.get("order_no") or "").split())[:60]
        if kind == "cancelled":
            p.state = "closed"
            session.end_cancel()
            return ("Closed: the cancel is done. Tell the owner plainly, without excuses: what they approved, what was really charged, "
                    "that you cancelled the order, and the refund state the page shows (amount, when). Then the next option (buy again "
                    "at the right price, only if they ask). Do not buy again on your own.")
        if kind == "closed":
            return "This payment's receipt was already checked and closed. Do not check it again; tell the owner the result."
        if kind == "match":
            p.state = "closed"
            return RESULT_CHECKED + (f" Order {order_no}." if order_no else "") + " Tell the owner the result with the order number."
        if kind == "lower":
            p.state = "closed"
            return (f"Checked: the charge {data.charged_text} is lower than the approved {p.amount_text}; everything else matches. "
                    "That is fine: say so plainly with the order number.")
        chk, terms = data
        first = p.state != "cancel"
        p.state = "cancel"
        session.allow_cancel(p.host, order_no)
        until = f" (free until {_until_text(terms)})" if terms.until_text or terms.until else ""
        how = f" Its cancel control on the page: {terms.how}." if terms.how else ""
        if kind == "free" and first and notify is not None:
            try:
                notify(t("Kelo was charged {charged} instead of {approved} at {site}; it is cancelling it now (free).",
                         charged=chk.charged_text, approved=p.amount_text, site=p.host), _diff_text(p, chk)
                       + (f" · {order_no}" if order_no else ""), current_task())
            except Exception:  # noqa: BLE001 - the notice never stops the cancel
                pass
        who = "The owner chose to cancel it." if kind == "ask" else "Cancelling is free now" + until + "."
        return ("It does NOT match what was approved (" + sums(p, chk, args) + "). " + who + " Cancel this order at once on the page" +
                (f" (order {order_no})" if order_no else "") + f".{how} The cancel click needs no card for the next few minutes, on "
                f"{p.host} only; click nothing else. Then read the page and call purchase_result again with cancelled=true (order_no). "
                "Do not buy again. Afterwards tell the owner plainly what was approved, what was really charged, that it is cancelled "
                "and the refund the page shows.")

    return Tool(
        "purchase_result",
        "Right after a payment went through: copy what the confirmation page shows (order number, charged total, items, cancel terms) "
        "and the server checks it against what the owner approved. If the charge differs and cancelling is free, it opens the cancel "
        "for you; with a fee, the owner decides. Call it again with cancelled=true after you cancelled.",
        {"type": "object", "properties": {
            "order_no": {"type": "string", "description": "the order, booking or receipt number as the page shows it"},
            "charged_total": {"type": "string", "description": "the total charged, as the page writes it (number and currency)"},
            "items": {"type": "array", "description": "each line of the receipt as the page shows it", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "options": {"type": "string"}, "quantity": {"type": "integer"}}}},
            "cancel": {"type": ["object", "null"], "description": "the cancel terms the page gives, null if none",
                       "properties": {"free_until": {"type": "string", "description": "until when cancelling is free, as an ISO 8601 time (a date alone is fine); leave out when the page gives no deadline"},
                                      "fee": {"type": ["number", "string"], "description": "the cancel fee as an amount, 0 when the page says cancelling costs nothing; leave out when unknown"},
                                      "how": {"type": "string", "description": "the words of the page's cancel button or link"}}},
            "cancelled": {"type": "boolean", "description": "true on the second call, after the order was cancelled"},
            "outcome": {"type": "string", "enum": ["placed", "not_placed", "unknown"],
                        "description": "after checkout_submit: placed only when the shop's own confirmation shows the order; not_placed when "
                                       "you saw it was not ordered; unknown when neither is clear (default placed)"},
            "evidence": {"type": "string", "description": "what shows the outcome: the shop's confirmation words, or what you saw"}},
         "required": []},
        result, requires_approval=True, describe=describe, validate=validate, declined=declined, auto_approve=auto,
        approval_extra=approval_extra,
    )
