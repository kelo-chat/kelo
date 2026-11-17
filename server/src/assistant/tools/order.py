"""One order, one card: the agent buys what the user asked for in chat ("<site>에서 <item> 주문해줘") and the user answers ONE
Allow/Deny card, at the end, that shows everything at once.

`begin_order(site, what)` starts an order for the browser tab and the task it runs in (it needs no card: the payment is what asks). While the order is open, on that site only (its registrable domain), the steps that only prepare it go through without
a card and are written to the approval log as part of the order (decision allowed_by_rule):

- going to the cart and the checkout and to the next step of the checkout (plain clicks: the final checkout step itself, by the page's
  structure, refuses them and goes through checkout_review / checkout_submit);
- choosing options, quantity and delivery, applying a coupon or points ("Apply coupon");
- sending the order form's details (a form with the shipping details) and filling the shipping form from a shipping profile
  (`browser_fill_shipping`), or typing the values of the order's address one by one;
- a pop-up of the site that only asks to go on ("장바구니로 이동할까요?").

Still asking, even during an order: typing into a password field (browser_login keeps its own rules: auto-allow and schedule grants),
signing up or changing the account, anything on another site, sending messages or reviews, deleting or cancelling, typing personal
details that are not the order's address (an e-mail, an address Kelo only remembers), and anything that may spend money: the final
payment goes through `checkout_review` (one card, or none for an auto-pay card within the user's rules), and after it the order covers
nothing more (a second payment in the same order asks again, also with auto-pay). The order ends when the user denies the payment, when
the task (the chat reply or the job) ends, after ORDER_MINUTES, or with `end_order`.

The address: the user's shipping profiles (shipping.py) by nickname; or, when the site account has none saved and Kelo has no profile,
`ask_shipping` shows ONE form in the chat (country-aware fields, prefilled with what Kelo remembers for the user to check, "use for next
orders" on by default) and waits for it; the answer is saved as a profile when the user left that on, and is the order's address
either way. An address the site account already holds is just selected on the page (no form, no typing); `checkout_review(ship_to=...)`
then names it on the card.

A run of a task the user allowed to buy at setup (schedule_grants.py: a schedule, a watcher's hit, a job) may start an order only on the
site of that permission, with the shipping profile chosen at setup.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .. import address
from ..agent import Tool, approval_timeout, current_call, emit_ui
from ..i18n import N_, t
from .sites import registrable as site_key
from .commits import coupon_box
from .task_permission import current_scope

ORDER_MINUTES = 45.0
MAX_STEPS = 80                 # covered steps in one order (a runaway loop stops asking for nothing)
FORM_SECONDS = 900.0           # how long the address form waits in a chat
MAX_WHAT = 120
STEP_LOGGED = N_("Part of your order: {what}")
FILLED = N_("Shipping details filled in")


def _site(value: str) -> str:
    text = (value or "").strip()
    host = urlsplit(text if "://" in text else "https://" + text).hostname or ""
    return site_key(host.lower())


IMPLICIT_NOTE = ("[Not from the user: you are buying on {site} without begin_order, so the order was started for you from the "
                 "purchase permission of the schedule: {what!r}. The steps that only prepare it go through without a card; the payment "
                 "itself is ONE checkout_review at the end (items, amount, ship_to, card). Next time call begin_order first.]")


@dataclass
class Order:
    id: str
    owner: str               # the browser tab (browser_owner)
    scope: str               # the task: "turn:<id>" / "job:<id>"
    site: str                # registrable domain
    what: str                # the user's words
    started: float
    until: float
    profile: dict[str, Any] | None = None     # the order's address (a saved profile, or one given once in the form)
    source: str = ""                          # profile | once
    grant: dict[str, Any] | None = None       # the schedule's purchase permission this run works under
    paid: bool = False
    steps: int = 0
    known: list[dict[str, Any]] = field(default_factory=list)   # the user's saved profiles: their values may be typed one by one
    asked: bool = False                       # the address form was answered (asked once per order)
    note: str = ""                            # the user's answer about paying, from the form
    implicit: bool = False                    # started from the user's request when the agent began buying without begin_order

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "site": self.site, "what": self.what, "paid": self.paid, "steps": self.steps,
                "shipping": self.profile.get("label") if self.profile else None}


class Orders:
    """The orders going on, one per browser tab (memory only: a restart ends every task and its order)."""

    def __init__(self, *, scope: Callable[[], str] = current_scope, alive: Callable[[str], bool] = lambda _s: True,
                 clock: Callable[[], float] = time.time, owner: Callable[[], str] | None = None):
        if owner is None:
            from .browser import browser_owner
            owner = browser_owner.get
        self.scope, self.alive, self.clock, self.owner = scope, alive, clock, owner
        self.on_use: Callable[[Order, str, str, str], None] | None = None   # (order, tool, commit kind, host): the server logs it
        # (host) -> the order started from the user's own request when the agent acts on a shop without begin_order (order_tools sets
        # it; None = no request to buy here, or a run that may not buy on that site)
        self.implicit: Callable[[str], Order | None] | None = None
        self._orders: dict[str, Order] = {}

    def start(self, site: str, what: str, *, grant: dict[str, Any] | None = None) -> Order:
        now = self.clock()
        o = Order("or" + secrets.token_hex(4), self.owner(), self.scope(), _site(site), " ".join(what.split())[:MAX_WHAT], now,
                  now + ORDER_MINUTES * 60, grant=grant)
        self._orders[o.owner] = o
        return o

    def open_now(self) -> bool:
        """An order is open on any tab (not run out, its task still going): a purchase may be in progress. The VPN connector never changes
        the route in the middle of one (vpn.py)."""
        now = self.clock()
        return any(now < o.until and self._alive(o.scope) for o in list(self._orders.values()))

    def current(self) -> Order | None:
        """The order of this tab in the task running now (None when there is none, it ran out or its task is over)."""
        o = self._orders.get(self.owner())
        if o is None:
            return None
        if self.clock() >= o.until or not self._alive(o.scope):
            self._orders.pop(o.owner, None)
            return None
        return o if o.scope == self.scope() else None

    def _alive(self, scope: str) -> bool:
        try:
            return bool(self.alive(scope))
        except Exception:  # noqa: BLE001 - when in doubt the order is over
            return False

    def start_implicit(self, host: str) -> Order | None:
        """No order is open on this tab, and the agent is about to take a buying step on `host`: open the order the user's request
        asked for (when it asked to buy, order or book), as begin_order would. None when there is nothing to start."""
        if self.implicit is None or not host or self.current() is not None:
            return None
        try:
            return self.implicit(host)
        except Exception:  # noqa: BLE001 - when in doubt each step asks as usual
            return None

    def end(self, owner: str | None = None) -> Order | None:
        return self._orders.pop(self.owner() if owner is None else owner, None)

    def end_scope(self, scope: str) -> None:
        for k in [k for k, o in self._orders.items() if o.scope == scope]:
            self._orders.pop(k, None)

    def paid_here(self, host: str) -> bool:
        """The order of this tab in this task was paid, on this site (its next "buy" on a product page or cart is a new purchase)."""
        o = self.current()
        return o is not None and o.paid and bool(host) and site_key(host.lower()) == o.site

    def mark_paid(self) -> None:
        o = self.current()
        if o is not None:
            o.paid = True

    def active(self) -> list[dict[str, Any]]:
        return [o.public() for o in self._orders.values() if self.clock() < o.until and self._alive(o.scope)]

    # ------------------------------------------------------------------ what an open order covers
    def covers(self, kind: str, host: str, *, tool: str, info: dict[str, Any] | None, selector: str, url: str, text: str = "",
               secret: bool = False) -> bool:
        """True = this committing step only prepares the order going on here, so it needs no card (it is counted and logged).
        False = ask as usual."""
        o = self.current()
        if kind == "unknown" or o is None or o.paid or secret or o.steps >= MAX_STEPS or not host or site_key(host.lower()) != o.site:
            return False   # (unknown: a step whose target could not be read is never covered)
        if info and info.get("signup_form"):
            return False   # a sign-up form (a new-password field): its steps ask
        if tool == "browser_click":
            ok = (kind == "apply" and coupon_box(info)) or (kind == "form" and not (info and info.get("pay_page")))
        else:
            src = o.profile if from_address(text, o.profile) else next((p for p in o.known if from_address(text, p)), None)
            ok = kind in ("personal", "form") and src is not None
            if ok and o.profile is None:
                o.profile, o.source = src, "profile"   # the order goes to the saved profile whose values are being typed
        if ok:
            self.count(o, tool, kind, host)
        return ok

    def count(self, o: Order, tool: str, kind: str, host: str) -> None:
        o.steps += 1
        if self.on_use is not None:
            try:
                self.on_use(o, tool, kind, host)
            except Exception:  # noqa: BLE001 - the log never blocks the step
                pass

    def dialog_ok(self, owner: str, host: str, message: str) -> bool:
        """A pop-up on the order's site while the order is prepared (before any payment): accepted. Paying itself is never a plain
        click (checkout_submit, inside its purchase window, accepts its own pop-ups)."""
        o = self._orders.get(owner)
        if o is None or o.paid or self.clock() >= o.until or site_key((host or "").lower()) != o.site:
            return False
        return True


def _norm(s: str) -> str:
    return " ".join((s or "").casefold().split())


def address_values(profile: dict[str, Any] | None) -> list[str]:
    if not profile:
        return []
    vals = [str(profile.get(k) or "") for k in (*address.FIELDS, "note")]
    vals += str(profile.get("name") or "").split()
    vals.append(" ".join(x for x in (profile.get("address_line1"), profile.get("address_line2")) if x))
    vals.append(str(profile.get("formatted") or "").replace("\n", " "))
    return [_norm(v) for v in vals if v and v.strip()]


def from_address(text: str, profile: dict[str, Any] | None) -> bool:
    """Whether typed text comes from the order's address (a whole value or a piece of it: a name split in two boxes, a phone in three)."""
    if not profile:
        return False
    raw = (text or "").strip()
    if not raw:
        return True
    digits = re.sub(r"\D", "", raw)
    if digits and not re.sub(r"[\d\s\-().+]", "", raw):
        cc = str(profile.get("country") or "")
        phones = {re.sub(r"\D", "", str(profile.get("phone") or "")), address.phone_national(str(profile.get("phone") or ""), cc),
                  national_phone(str(profile.get("phone") or ""), cc)}
        postal = {re.sub(r"\D", "", str(profile.get(k) or "")) for k in ("postal_code", "sorting_code")}
        if digits in postal or any(len(digits) >= 3 and p and digits in p for p in phones):
            return True
    t_ = _norm(raw)
    return len(t_) >= 2 and any(t_ == v or t_ in v for v in address_values(profile))


def national_phone(e164: str, cc: str) -> str:
    """How the phone is written inside its own country: the trunk prefix kept ("010...", "090...") except where it is the calling code
    itself (+1: "650...")."""
    c = address._data()["calling"].get((cc or "").upper()) or {}
    digits = re.sub(r"\D", "", e164 or "")
    code, prefix = c.get("code", ""), c.get("prefix", "")
    if code and digits.startswith(code):
        rest = digits[len(code):]
        return (prefix + rest) if prefix and prefix != code else rest
    return digits


# ---------------------------------------------------------------------------------------------------------------- the address form
@dataclass
class _Form:
    id: str
    order: Order
    event: dict[str, Any]
    expires: float
    done: asyncio.Future[dict[str, Any]] = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class ShippingForms:
    """The address forms waiting in a chat (one per order). POST /api/shipping-forms/{id} answers one (`answer`)."""

    def __init__(self, book: Any, clock: Callable[[], float] = time.time):
        self.book, self.clock = book, clock
        self._forms: dict[str, _Form] = {}

    def open(self, order: Order, event: dict[str, Any], seconds: float) -> _Form:
        f = _Form("sf" + secrets.token_hex(6), order, event, self.clock() + seconds)
        self._forms[f.id] = f
        return f

    def pending(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [f.event for f in self._forms.values() if f.expires > now and not f.done.done()]

    def answer(self, form_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Check and take the user's answer: {"ok", "profile" (saved or None), "formatted"}. Raises KeyError (gone) or ShippingError."""
        f = self._forms.get(form_id)
        if f is None or f.done.done() or f.expires <= self.clock():
            self._forms.pop(form_id, None)
            raise KeyError(form_id)
        if body.get("cancel") is True:
            f.done.set_result({"cancel": True})
            self._forms.pop(form_id, None)
            return {"ok": True, "profile": None, "formatted": ""}
        addr = body.get("address")
        if not isinstance(addr, dict):
            from ..shipping import ShippingError
            raise ShippingError("address must be an object: {country, name, ..., phone, note}")
        save = body.get("save") is not False
        label = str(body.get("label") or "").strip()
        given = {k: addr.get(k) for k in ("country", *address.FIELDS, "phone", "note") if k in addr}
        if save:
            profile = self.book.add({**given, "label": label})
        else:
            profile = self.book.checked({**given, "label": label})
        payment = body.get("payment") if body.get("payment") in ("card", "site") else None
        f.done.set_result({"profile": profile, "saved": save, "payment": payment})
        self._forms.pop(form_id, None)
        return {"ok": True, "profile": profile if save else None, "formatted": profile["formatted"]}

    def drop(self, form_id: str) -> None:
        f = self._forms.pop(form_id, None)
        if f is not None and not f.done.done():
            f.done.cancel()


def _clip(value: Any, n: int = 200) -> str:
    return " ".join(str(value or "").split())[:n] if isinstance(value, (str, int, float)) else ""


def order_tools(orders: Orders, session: Any, book: Any, forms: ShippingForms | None = None, *, grants: Any = None,
                timezone: Callable[[], str] = lambda: "", wallet: Any = None, paths: Any = None) -> list[Tool]:
    """`session`: the BrowserSession (None: no browser). `book`: shipping.ShippingBook. `grants`: schedule_grants.ConditionalGrants (a
    run of a task with purchase permissions may start an order only where it may buy)."""

    def schedule_grant(site: str) -> tuple[bool, dict[str, Any] | None]:
        """(in a run that must stay inside its purchase permissions, its permission for this site or None); (False, None) elsewhere.
        A schedule's run always is; a watcher's hit or a job only when its task holds a purchase permission at all."""
        if grants is None:
            return False, None
        from ..schedule_grants import current_schedule_id
        scopes = grants.scopes()
        mine = [g for s in scopes for g in grants.list(scope=s, kind="purchase")]
        if not current_schedule_id() and not mine:
            return False, None
        here = [g for g in mine if g["site"] == site]
        return True, (here[0] if here else None)

    def page_site() -> str:
        return site_key(session.current_host()) if session is not None and session.current_host() else ""

    def profiles_text() -> str:
        items = book.profiles()
        if not items:
            return "none"
        return "; ".join(f"'{p['label']}'{' [DEFAULT]' if p['default'] else ''} ({p['country']}): {p['one_line']}" for p in items)

    # ---------------------------------------------------------------- begin_order
    def begin_validate(args: dict[str, Any]) -> str | None:
        site, what = _site(str(args.get("site") or "")), _clip(args.get("what"), MAX_WHAT)
        if not site:
            return "site is required: the shop's address (the site the user named)"
        if not what:
            return "what is required: what the user asked to buy, in their words (items, options, quantity)"
        if not orders.scope():
            return "an order can only be started inside a chat reply or a job"
        here = page_site()
        if here and here != site:
            return f"the browser is on {here}, not on {site}. Open {site} first (or start the order for the site the user named)"
        in_run, g = schedule_grant(site)
        if in_run and g is None:
            return ("this run has no permission to buy on that site (task_allow at setup); go on without an order: each step asks, "
                    "and tell the user in the run's report")
        ref = str(args.get("shipping") or "").strip()
        if ref and book.find(ref) is None:
            return f"no shipping profile {ref[:40]!r}. Profiles: {profiles_text()}"
        return None

    async def begin(args: dict[str, Any]) -> str:
        site, what = _site(str(args["site"])), _clip(args.get("what"), MAX_WHAT)
        _in_run, g = schedule_grant(site)
        o = orders.start(site, what, grant=g)
        ref = str(args.get("shipping") or "").strip() or (str(g.get("shipping_id") or "") if g else "")
        named = book.find(ref) if ref else None
        o.known = book.profiles()
        if named is not None:
            o.profile, o.source = named, "profile"
        if session is not None:
            await session.card(t("Preparing your order"), force=True)
        lines = [f"ORDER STARTED on {site}: {what}. Until you pay, the steps that only prepare this order on {site} go through without "
                 "asking the user: going to the cart and the checkout and the next checkout step, choosing options, quantity and "
                 "delivery, applying a coupon, and filling the shipping form with browser_fill_shipping. Still asking: passwords, "
                 "sign-up or account changes, other sites, messages or reviews, deletions, personal details that are not the order's "
                 "address, and any payment."]
        if named is not None:
            lines.append(f"Ship to the profile '{named['label']}' ({named['country']}): {named['one_line']}. If the site account already "
                         "has this address saved, select it; otherwise browser_fill_shipping.")
        else:
            lines.append(f"Shipping profiles: {profiles_text()}. If the site account already has a saved address, just select it on the "
                         "page (no typing) and pass it as ship_to to checkout_review. Else use a profile with browser_fill_shipping "
                         "(the default one unless the user named another). With no profile and no saved address on the site, call "
                         "ask_shipping once (a form in the chat; prefill what you remember of the user's name, phone and address so they "
                         "can check it). Never type an address you only remember without that form.")
        if wallet is not None and not [c for c in wallet.cards() if c["usable"] and not c["expired"]]:
            lines.append("The wallet has no usable card: pay with what the site holds for the user, or ask in the same ask_shipping "
                         "form (ask_payment=true).")
        if paths is not None and (hint := paths.hint(site)):   # payment_pins.PaymentPaths: the way of paying that went through here
            lines.append(hint)
        lines.append("When the order is ready (item, options, quantity, address, total on the page), call checkout_review with status draft "
                     "(items, variants, total, currency, delivery, ship_to when the site's saved address is used, card), then with status "
                     "ready once the check confirms: ready is the one card the user answers. "
                     "If the site asks you to sign in, use browser_login (its own setting decides whether it asks).")
        return " ".join(lines)

    # ---------------------------------------------------------------- an order the agent did not start itself
    def implicit(host: str) -> Order | None:
        """The agent takes a buying step on a site with no order open: only a schedule's (or a job's) purchase permission for this site
        starts the order, as begin_order would. The owner's words are never read for it (docs/no-word-lists.md, 2026-11-16)."""
        if not orders.scope():
            return None
        site = site_key(host.lower())
        in_run, g = schedule_grant(site)
        if not in_run or g is None:
            return None   # no permission here: each step asks
        o = orders.start(site, str(g.get("what") or "")[:MAX_WHAT], grant=g)
        o.known = book.profiles()
        o.implicit = True
        ref = str(g.get("shipping_id") or "")
        if ref and (named := book.find(ref)) is not None:
            o.profile, o.source = named, "profile"
        return o

    orders.implicit = implicit

    # ---------------------------------------------------------------- the profiles
    async def list_profiles(_args: dict[str, Any]) -> str:
        items = book.profiles()
        if not items:
            return ("The user has no shipping profiles. During an order with no address saved on the site either, call ask_shipping "
                    "(one form in the chat; the user can save it as a profile there).")
        out = ["Shipping profiles (the user's own, in Kelo):"]
        for p in items:
            out.append(f"- '{p['label']}'{' [DEFAULT]' if p['default'] else ''}, {p['country']}: {p['name']}, {p['phone']}, "
                       f"{p['one_line']}{', note: ' + p['note'] if p['note'] else ''}")
        return "\n".join(out)

    # ---------------------------------------------------------------- ask_shipping
    def ask_validate(args: dict[str, Any]) -> str | None:
        if forms is None:
            return "no app is connected to show the form; ask the user in chat"
        o = orders.current()
        if o is None:
            return "start the order first (begin_order); the address form belongs to an order"
        if o.asked:
            return "the user already answered the address form for this order; use that address (browser_fill_shipping)"
        from ..workers import current_turn
        if (current_turn.get() or {}).get("unattended"):   # a run nobody watches: nobody is there to fill in a form
            return "nobody is watching this run to fill in a form; stop and tell the user in the run's report"
        cc = str(args.get("country") or "").strip().upper()
        if cc and not address.known(cc):
            return f"country must be a two-letter code (e.g. US, KR, JP), not {cc!r}"
        return None

    async def ask(args: dict[str, Any]) -> str:
        assert forms is not None
        o = orders.current()
        assert o is not None
        cc = str(args.get("country") or "").strip().upper() or address.default_country(
            timezone=timezone(), lang=str(args.get("lang") or ""), previous=book.last_country())
        pre_in = args.get("prefill") if isinstance(args.get("prefill"), dict) else {}
        prefill = {k: _clip(pre_in.get(k)) for k in (*address.FIELDS, "phone", "note") if _clip(pre_in.get(k))}
        prefill = {k: v for k, v in prefill.items() if not address.CARD_NUMBER.search(v) or k == "phone"}
        no_card = wallet is not None and not [c for c in wallet.cards() if c["usable"] and not c["expired"]]
        seconds = max(FORM_SECONDS, approval_timeout.get())
        event = {"type": "shipping_form", "id": current_call.get(), "site": o.site, "what": o.what, "country": cc, "prefill": prefill,
                 "label": _clip(args.get("label"), 40) or t("Home"), "save": True,
                 "ask_payment": bool(args.get("ask_payment")) or no_card, "expires_at": int(time.time() + seconds)}
        f = forms.open(o, event, seconds)
        event["form_id"] = f.id
        if not await emit_ui(event):
            forms.drop(f.id)
            return "No app is showing this chat right now, so the form could not be shown. Ask the user in chat and stop."
        try:
            got = await asyncio.wait_for(asyncio.shield(f.done), seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            forms.drop(f.id)
            return "The user did not fill in the address form in time. Nothing was typed. Tell the user the order waits for an address."
        if got.get("cancel"):
            o.asked = True   # asked once: a closed form is an answer too
            return ("The user closed the address form without an address. Nothing was typed. Ask in one sentence how they want to go on; "
                    "do not ask for the address again in chat.")
        o.asked = True
        prof = got["profile"]
        o.profile, o.source = prof, "profile" if got["saved"] else "once"
        pay = {"card": " For paying they chose to add a card in the wallet now: before checkout_review, check wallet_cards; if there is "
                       "still no card, ask once whether to wait or pay on the site.",
               "site": " For paying they chose the site's own payment way: use pay_with in checkout_review (no wallet card)."}.get(
            got.get("payment") or "", "")
        o.note = got.get("payment") or ""
        saved = (f"saved as the shipping profile '{prof['label']}' for next orders" if got["saved"] else "for this order only (not saved)")
        return (f"The user filled in the address ({saved}): {prof['name']}, {prof['phone']}, {prof['one_line']} ({prof['country']}). "
                f"Now fill the checkout's shipping form with browser_fill_shipping (no card needed).{pay}")

    # ---------------------------------------------------------------- browser_fill_shipping
    def which(args: dict[str, Any]) -> dict[str, Any] | None:
        ref = str(args.get("profile") or "").strip()
        o = orders.current()
        if ref:
            return book.find(ref)
        if o is not None and o.profile is not None:
            return o.profile
        return book.default()

    def fill_validate(args: dict[str, Any]) -> str | None:
        if session is None:
            return "there is no browser"
        o = orders.current()
        if o is None:
            return "shipping details are filled in only during an order the user asked for: call begin_order first"
        if o.paid:
            return "this order is already paid; nothing more is filled in"
        here = page_site()
        if here != o.site:
            return f"the browser is on {here or 'a blank page'}, but the order is on {o.site}. Shipping details go only to that site"
        if which(args) is None:
            ref = str(args.get("profile") or "").strip()
            return (f"no shipping profile {ref[:40]!r}. Profiles: {profiles_text()}" if ref else
                    "there is no address for this order yet: if the site account has one saved, select it; else call ask_shipping")
        return None

    async def fill(args: dict[str, Any]) -> str:
        o = orders.current()
        prof = which(args)
        assert o is not None and prof is not None and session is not None
        if o.profile is None or (prof.get("id") and prof.get("id") != o.profile.get("id")):
            o.profile, o.source = prof, "profile" if prof.get("id") else "once"
        result = await session.fill_address(fill_values(prof))
        host = session.current_host()
        orders.count(o, "browser_fill_shipping", "personal", host)
        if prof.get("id"):
            book.touch(prof["id"])
            book.store.add_vault_log(prof["label"], "shipping-filled", f"shipping details typed into the checkout on {site_key(host)}")
        await session.card(t(FILLED) if result.filled else t("Preparing your order"), force=True)
        if not result.filled:
            head = ("No shipping fields were found on this page. Nothing was typed. Open the checkout's address step (or its 'new address' "
                    "form) first; if the account has a saved address, select it instead.")
        else:
            head = f"Filled the shipping form from '{prof['label']}': {', '.join(result.filled)}."
            if result.missing:
                head += f" Not found on the page: {', '.join(result.missing)}."
            head += " Check the page; then go on with the order."
        from ..agent.fence import data
        return head + "\n\n" + data(result.text)

    async def end(args: dict[str, Any]) -> str:
        o = orders.end()
        return f"The order on {o.site} is closed; its steps ask again." if o else "There was no order going on."

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    out = [
        Tool("begin_order", "Start the order the user asked for in chat, on the site where it is bought, before going to the cart. Until "
             "you pay, the steps that only prepare it (cart, checkout, options, coupon, shipping form) need no card; the payment itself "
             "is ONE checkout_review at the end.",
             obj({"site": {"type": "string", "description": "the shop's address"},
                  "what": {"type": "string", "description": "what the user asked to buy, in their words"},
                  "shipping": {"type": "string", "description": "a shipping profile's nickname, if the user named one"}}, ["site", "what"]),
             begin, validate=begin_validate),
        Tool("shipping_profiles", "List the user's saved shipping addresses (nickname, country, name, phone, address, which is the default).",
             obj({}, []), list_profiles, read_only=True),
        Tool("browser_fill_shipping", "During an order, fill the checkout's shipping form on the page with a shipping profile (the order's "
             "address or the default one). Only on the order's site; no card.",
             obj({"profile": {"type": "string", "description": "a shipping profile's nickname; leave out for the order's address"}}, []),
             fill, validate=fill_validate, untrusted=True, source="page", hold=session.hold_for_user if session is not None else None),
        Tool("end_order", "Close the order going on (the user changed their mind, or it cannot be done): its steps ask again.",
             obj({}, []), end),
    ]
    if forms is not None:
        out.append(Tool(
            "ask_shipping", "During an order with no address (none saved on the site, no shipping profile): show ONE address form in the "
            "chat and wait for it. Prefill what you remember (the user checks it). Saved as a profile when they keep that on.",
            obj({"country": {"type": "string", "description": "two-letter country code of the address, if known"},
                 "prefill": {"type": "object", "description": "what you remember: name, phone, postal_code, address_line1, address_line2, "
                             "locality, admin_area, note", "properties": {k: {"type": "string"} for k in (*address.FIELDS, "phone", "note")}},
                 "label": {"type": "string", "description": "nickname to save it under, e.g. home"},
                 "ask_payment": {"type": "boolean", "description": "also ask how to pay (no usable wallet card)"}}, []),
            ask, validate=ask_validate))
    return out


def fill_values(prof: dict[str, Any]) -> dict[str, Any]:
    """What BrowserSession.fill_address needs: the address plus the phone as dialled at home, the calling code, the region's name."""
    cc = str(prof.get("country") or "")
    return {**prof, "phone_national": national_phone(str(prof.get("phone") or ""), cc), "calling_code": address.calling_code(cc),
            "admin_area_name": region_name(prof)}


def region_name(profile: dict[str, Any]) -> str:
    """The admin area's name as the region list writes it (a page's list may show the name rather than the code)."""
    cc, key = str(profile.get("country") or ""), str(profile.get("admin_area") or "")
    if not key:
        return ""
    rec = address.meta(cc)
    keys = [k for k in str(rec.get("sub_keys") or "").split("~") if k]
    for lst in (str(rec.get("sub_names") or "").split("~"), str(rec.get("sub_lnames") or "").split("~")):
        if len(lst) == len(keys) and key in keys and lst[keys.index(key)]:
            return lst[keys.index(key)]
    return key
