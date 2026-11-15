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
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..agent import Tool, approval_of_current_call, approval_timeout, current_box, current_call, emit_ui
from ..agent.fence import data
from ..i18n import t
from ..wallet import CARD_TTL, EXPLAIN, CardDeliveries, CardSecret, Decision, Wallet, WalletError, money_text
from .browser import EXTRA_AUTH_NOTE, PURCHASE_WINDOW_SECONDS, BrowserPolicyError, BrowserSession, browser_owner
from .sites import registrable as site_key, same_site

MAX_ITEMS = 20

CARD_NUMBER = re.compile(r"(?:\d[ -]?){13,19}")   # a run of 13-19 digits (spaces/dashes allowed) looks like a card number
MAX_SUMMARY = 400
APPROVAL_WAIT = 300.0   # how long the app's approval card waits (server.APPROVAL_TIMEOUT_SECONDS); a card slot lives that long plus CARD_TTL
DECLINED = ("The user did NOT approve this payment (they said no, or did not answer in time). Do NOT click pay, order, book or confirm, "
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
                   ledger: Any = None, paths: Any = None, notify: Any = None, timezone: Any = None, clock: Any = time.time) -> list[Tool]:
    """`grants`: the standing permissions of conditional tasks (schedule_grants.ConditionalGrants): a run of a task (a schedule, a
    watcher's hit, a job) the user allowed at setup to buy on its own completes a purchase within its limits without a card.
    `orders`: the order going on (tools/order.Orders). `pins` / `pin_deliveries` (payment_pins.py): a payment PIN saved for the site is
    released by the phone for an approved payment (with the approval's Allow, or a `pin_needed` card when there was none) and kept in
    `pin_deliveries` for one use. `ledger`: the approved payment of each tab (the payment-step card shows it). `paths`: PaymentPaths. `notify(title, body, task_id)`: an urgent notice to the owner (purchase_result's wrong charge). `timezone()`: the
    owner's timezone name (a cancel deadline the page gave without one)."""
    arms: dict[str, _Arm] = {}               # device -> the card of the payment approved last on its tab
    # (device, tool call id) -> (the decision taken for it, the covering schedule grant, the reason in words): read back by the handler
    decided: dict[tuple[str, str], tuple[Decision, Any, str]] = {}

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

    def asked_to_buy_here(order: Any, host: str) -> bool:
        """The owner's own words (never page or mail text: Orders.request reads only their messages) ask to buy, and the order the
        agent runs for them is on this payment's site."""
        if order is None or orders is None or not host or site_key(host.lower()) != order.site or orders.request is None:
            return False
        from .order import buy_request
        try:
            return buy_request(" ".join(str(orders.request() or "").split()))
        except Exception:  # noqa: BLE001 - when in doubt the first payment on a site asks
            return False

    def judge(args: dict[str, Any]) -> tuple[Decision, Any, str]:
        """Whether this payment goes ahead without a card, and if not, why: first a task's standing permission (only in a run of that
        task, only within its limits; a card kept on the phone only still needs the phone; the wallet's pause and the vault lock
        still stop it), then the wallet's auto-pay rules."""
        card = card_of(args)
        host, amount, cur = money_args(args)
        order = orders.current() if orders is not None else None
        if order is not None and order.paid:
            return Decision(False, "order_again"), None, ""   # one payment per order: another one always asks
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
        return wallet.decide(card, host, amount, cur, asked_here=asked_to_buy_here(order, host)), None, ""

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
        after = ("Do not add or change anything: if the total on the page is not exactly this, stop and call confirm_purchase again with "
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
                    "number yourself), then make the final pay click." if card else
                    " Now make the final pay / order click on that site (it needs no second card for the next few minutes).")
            return (f"APPROVED by the standing permission the user gave this task at setup (within its cap of "
                    f"{grant['max_amount_text']} per time): {summary}, total {amount}, on {host}. The user is notified.{fill} "
                    "If anything differs from what was expected (another total, new terms, an extra step that asks for money or an "
                    f"account change), stop and call confirm_purchase again or ask the user. {after}" + await after_ok(args, card, grant))
        if wallet and card:
            fill = (f"If the page asks for card details, fill them with browser_fill_card(card={card['label']!r}) (never type a card "
                    "number yourself), then make the final pay click.")
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
        return (f"APPROVED by the user: {summary}, total {amount}, on {host}.{why} Now make the final pay / order / book "
                f"click on that site (it needs no second card for the next few minutes). {after}" + await after_ok(args, None, None))

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
    if ledger is not None and session is not None:
        tools.append(_result_tool(session, ledger, notify=notify, timezone=timezone, clock=clock))
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
        return (head + "\n".join(lines) + f"\n{which}. Pass its nickname as confirm_purchase(card=...) (leaving card and pay_with out "
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
            return (f"no approved payment with '{card['label']}' is open. Call confirm_purchase with card={card['label']!r} first; "
                    "the card can be typed only right after that")
        if not session.purchase_active(a.host):
            return "the approval for this payment ran out (time or steps). Call confirm_purchase again"
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
            return ("error: the card details are no longer here (a card the phone sends is typed once). Call confirm_purchase again "
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
            head += " Check the page, then make the final pay click (covered by the approval)."
        if result.auth:
            head += "\n" + EXTRA_AUTH_NOTE.format(words=result.auth)
        return head + "\n\n" + data(result.text)   # only the page is data (fenced by the loop); the lines above are this app's own

    tools.append(Tool("wallet_cards", "List the cards in the user's wallet by nickname and last 4 digits (never a number), which one "
                      "is the default, and whether each pays automatically or needs approval.",
                      {"type": "object", "properties": {}}, wallet_cards, read_only=True))
    tools.append(Tool("browser_fill_card", "Type a wallet card into the checkout form of the page you are on, only right after "
                      "confirm_purchase approved paying with that card on that site. You never see the values.",
                      {"type": "object", "properties": {"card": {"type": "string", "description": "the card's nickname"}}, "required": ["card"]},
                      fill_card, validate=fill_validate))
    return tools


def _same_site(a: str, b: str) -> bool:
    """One site: the same registrable domain by the Public Suffix List (sites.py), so 'pay.example.co.kr' is the site of
    'www.example.co.kr' and 'badexample.co.kr' is not."""
    return same_site(a, b)


# ---------------------------------------------------------------- purchase_result: the receipt against what was approved
RESULT_CHECKED = "Checked: matches what was approved."


def _result_tool(session: BrowserSession, ledger: Any, *, notify: Any = None, timezone: Any = None, clock: Any = time.time) -> Tool:
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

    def validate(args: dict[str, Any]) -> str | None:
        p = pay()
        if p is None:
            return ("no approved payment here: purchase_result checks the receipt of a payment that confirm_purchase approved in this "
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
        return judge(args)[0] != "ask"

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
        p = pay()
        kind, data = judge(args)
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
                       "properties": {"free_until": {"type": "string", "description": "until when cancelling is free: ISO time, or the page's words"},
                                      "fee": {"type": "string", "description": "the cancel fee as the page says it"},
                                      "how": {"type": "string", "description": "the words of the page's cancel button or link"}}},
            "cancelled": {"type": "boolean", "description": "true on the second call, after the order was cancelled"}},
         "required": ["order_no"]},
        result, requires_approval=True, describe=describe, validate=validate, declined=declined, auto_approve=auto,
        approval_extra=approval_extra,
    )
