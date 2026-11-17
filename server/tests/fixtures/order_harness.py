"""Drives Kelo's real browser, order and payment tools against the fake shop (fake_shop.py) with a scripted model, and counts the
Allow/Deny cards the user would see. Used by server/tests/test_order_flow.py (the flow as built) and by a measurement of the same scenarios
on the code from before the order flow (`mode="before"`: no begin_order, the address typed field by field)."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable

from assistant.agent import ToolResult, current_approval, run_agent, ui_sink
from assistant.page_judge import PageJudge
from assistant.providers import Done, TextDelta, ToolCall
from assistant.tools.browser import BrowserSession, browser_tools
from assistant.tools.purchase import purchase_tools
from assistant.tools.vault_tools import vault_tools
from assistant.vault import Transient
from assistant.wallet import CardDeliveries, Wallet
from assistant.workers import current_turn

from fake_page_judge import FakePageJudge

VISA, CVC = "4242424242424242", "123"
KR, US, OTHER = "https://www.mugshop.example", "https://www.usmugs.example", "https://www.othershop.example"


def public(host: str, port: int) -> list[tuple]:
    """Every made-up shop address resolves to a public address (the requests themselves go to the local shop through a route)."""
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def scripted(steps: list[tuple[str, dict[str, Any]]]):
    """The model's calls in order. An argument "$review" is the review_id of the last checkout_review in the conversation so far."""
    expanded: list[tuple[str, dict[str, Any]]] = []
    for name, args in steps:
        if name == "checkout_review" and args.get("status") == "ready":
            expanded.append((name, {**args, "status": "draft"}))
        expanded.append((name, args))
    it = iter(enumerate(expanded))
    done = [TextDelta("done"), Done("stop")]

    def review_id(request) -> str:
        found = re.findall(r'"review_id": "((?:rv|pa)_[0-9a-f]+)"', " ".join(str(getattr(m, "content", "") or "") if not isinstance(m, dict)
                                                                       else str(m.get("content") or "") for m in request.messages))
        return found[-1] if found else "rv_none"

    async def fn(provider, request, **kw):
        got = next(it, None)
        if got is None:
            events = [TextDelta("."), Done("stop")]
        else:
            i, (name, args) = got
            args = {k: (review_id(request) if v == "$review" else v) for k, v in args.items()}
            events = [ToolCall(f"c{i}", name, json.dumps(args, ensure_ascii=False)), Done("tool_calls")]
        for ev in events:
            yield ev
    return fn


def review(summary: str, amount: float, currency: str, site: str, *, card: str | None = None, pay_with: str = "",
           items: list[dict[str, Any]] | None = None, ship_to: str = "") -> tuple[str, dict[str, Any]]:
    """A checkout_review call of the scripts (the review tool's own argument names). It is the ready step: Env.run puts the draft (the
    order check, which asks nobody) in front of it and keeps the draft's result out of Run.results."""
    args: dict[str, Any] = {"site": site, "merchant": summary, "total": amount, "currency": currency, "status": "ready",
                            "items": items or [{"name": summary, "qty": 1}]}
    if card:
        args["card"] = card
    if pay_with:
        args["payment_method"] = pay_with
    if ship_to:
        args["ship_to"] = ship_to
    return "checkout_review", args


SUBMIT = ("checkout_submit", {"review_id": "$review"})


@dataclass
class Card:
    tool: str
    detail: str
    args: dict[str, Any]
    extra: dict[str, Any]
    allowed: bool


@dataclass
class Run:
    cards: list[Card] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    ui: list[dict[str, Any]] = field(default_factory=list)

    def card_tools(self) -> list[str]:
        return [c.tool for c in self.cards]

    def result(self, name: str, nth: int = 0) -> ToolResult:
        return [r for r in self.results if r.name == name][nth]


async def confirm_order(system: str, user: str) -> dict[str, Any]:
    """The order check's model request, answered: the cart matches (the checks of this module's tests are about cards, not the check)."""
    return {"verdict": "confirm", "reason": "matches"}


class Env:
    """One test's Kelo side: the store, wallet, shipping book, orders and the tools, on one browser page."""

    def __init__(self, store: Any, page: Any, *, mode: str = "after", grants: Any = None, source: str = "chat"):
        self.store, self.page, self.mode, self.source = store, page, mode, source

        async def connect():
            return page
        self.session = BrowserSession(connect=connect, resolver=public)
        self.session.page_judge = PageJudge(FakePageJudge().ask)   # stands in for the owner's main model: it reads the labels of fields without autocomplete
        self.wallet = Wallet(store)
        self.deliveries = CardDeliveries()
        self.transient = Transient()
        self.grants = grants
        self.form_answer: Callable[[dict[str, Any]], dict[str, Any] | None] = lambda ev: None
        if mode == "after":
            from assistant.shipping import ShippingBook
            from assistant.tools.order import Orders, ShippingForms, order_tools
            self.book = ShippingBook(store)
            if grants is not None:
                grants.shipping = self.book
            self.orders = Orders()
            self.forms = ShippingForms(self.book)
            self.session.order_dialog = self.orders.dialog_ok
            self.logged: list[tuple[str, str, str]] = []
            self.orders.on_use = lambda o, tool, kind, host: self.logged.append((tool, kind, host))
            self.tools = [*browser_tools(self.session, orders=self.orders),
                          *purchase_tools(self.session, self.wallet, self.deliveries, grants=grants, orders=self.orders, ask=confirm_order),
                          *vault_tools(store, self.session, self.transient, grants=grants, orders=self.orders),
                          *order_tools(self.orders, self.session, self.book, self.forms, grants=grants, wallet=self.wallet)]
        else:
            self.tools = [*browser_tools(self.session), *purchase_tools(self.session, self.wallet, self.deliveries, grants=grants, ask=confirm_order),
                          *vault_tools(store, self.session, self.transient, grants=grants)]
        self.by_name = {t.name: t for t in self.tools}

    async def run(self, steps: list[tuple[str, dict[str, Any]]], answer: Callable[[Any], bool] = lambda req: True,
                  turn_id: str | None = None) -> Run:
        out = Run()
        loop = asyncio.get_running_loop()

        async def approve(req):
            allowed = bool(answer(req))
            approval_id = "ap" + secrets.token_hex(4)
            current_approval.set((req.id, approval_id))
            tool = self.by_name.get(req.name)
            extra: dict[str, Any] = {}
            if tool is not None and tool.approval_extra is not None:
                extra = dict(tool.approval_extra(approval_id, req.arguments) or {})
            out.cards.append(Card(req.name, req.detail, req.arguments, extra, allowed))
            if allowed and (extra.get("card") or {}).get("needs_card"):   # the phone sends the end-to-end card after Allow
                loop.call_later(0.05, lambda: self.deliveries.deliver(approval_id, {"number": VISA, "cvc": CVC}))
            return allowed

        async def sink(item):
            out.ui.append(item)
            if item.get("type") == "shipping_form":
                body = self.form_answer(item)
                if body is not None:
                    loop.call_later(0.05, lambda: self.forms.answer(item["form_id"], body))

        current_turn.set({"turn_id": turn_id or "t" + secrets.token_hex(3), "task_id": 1, "source": self.source,
                          "unattended": self.source.startswith("schedule:"), "request": "order it"})
        ui_sink.set(sink)
        agen = run_agent("p", "m", [{"role": "user", "content": "order it"}], self.tools, approve=approve, max_steps=60,
                         stream_fn=scripted(steps))
        async for ev in agen:
            if isinstance(ev, ToolResult) and not (ev.name == "checkout_review" and '"status": "draft"' in ev.output):   # the order check's answer
                out.results.append(ev)
        return out


async def sign_in_directly(page: Any, base: str, user: str, password: str) -> None:
    """The user's browser already holds a session on the shop (signed in earlier, outside this order)."""
    await page.goto(f"{base}/login?next=/")
    await page.fill("input[name=username]", user)
    await page.fill("input[name=password]", password)
    await page.click("button[type=submit]")
    await page.wait_for_load_state("domcontentloaded")


# ---------------------------------------------------------------------------------------------------------------- the scripts
KR_ITEMS = [{"name": "블루 머그", "options": "색상: 화이트", "quantity": 2, "price": "24,000원"}]
US_ITEMS = [{"name": "Blue mug", "options": "Color: White", "quantity": 2, "price": "$24.00"}]


def kr_to_checkout(mode: str, what: str = "블루 머그 화이트 2개") -> list[tuple[str, dict[str, Any]]]:
    steps: list[tuple[str, dict[str, Any]]] = [("browser_open", {"url": f"{KR}/product/1"})]
    if mode == "after":
        steps.append(("begin_order", {"site": "www.mugshop.example", "what": what}))
    steps += [("browser_click", {"selector": "label:has-text('화이트')"}),
              ("browser_type", {"selector": "input[name=qty]", "text": "2"}),
              ("browser_click", {"selector": "#buy"}),
              ("browser_click", {"selector": "button:has-text('주문하기')"})]
    return steps


def kr_pay(mode: str, *, card: str | None = "생활비 카드", ship_to: str = "", amount: int = 24000, pay_with: str = "",
           fill_card: bool = True) -> list[tuple[str, dict[str, Any]]]:
    steps: list[tuple[str, dict[str, Any]]] = [("browser_read", {}), review("블루 머그 화이트 x2", amount, "KRW", "www.mugshop.example", card=card,
                                                                         pay_with=pay_with, ship_to=ship_to if mode == "after" else "",
                                                                         items=KR_ITEMS if mode == "after" else None)]
    if card and fill_card:
        steps.append(("browser_fill_card", {"card": card}))
    steps.append(SUBMIT)
    return steps


KR_TYPED = [("browser_type", {"selector": "input[name=receiver]", "text": "홍길동"}),
            ("browser_type", {"selector": "input[name=phone]", "text": "010-1234-5678"}),
            ("browser_type", {"selector": "input[name=zipcode]", "text": "06236"}),
            ("browser_type", {"selector": "input[name=addr1]", "text": "서울특별시 강남구 테헤란로 152"}),
            ("browser_type", {"selector": "input[name=addr2]", "text": "10층 1004호"})]
KR_NEXT = ("browser_click", {"selector": "button:has-text('다음')"})
KR_SAVED = ("browser_click", {"selector": "input[value=saved]"})
KR_SAVED_TEXT = "집 (홍길동 010-1234-5678 서울특별시 강남구 테헤란로 152 10층)"


def us_to_checkout(mode: str) -> list[tuple[str, dict[str, Any]]]:
    steps: list[tuple[str, dict[str, Any]]] = [("browser_open", {"url": f"{US}/product/1"})]
    if mode == "after":
        steps.append(("begin_order", {"site": "www.usmugs.example", "what": "two white blue mugs"}))
    steps += [("browser_click", {"selector": "label:has-text('White')"}),
              ("browser_type", {"selector": "input[name=qty]", "text": "2"}),
              ("browser_click", {"selector": "#buy"}),
              ("browser_click", {"selector": "button:has-text('Checkout')"})]
    return steps


def us_pay(mode: str, card: str = "Everyday card", amount: float = 24.0) -> list[tuple[str, dict[str, Any]]]:
    return [("browser_read", {}), review("Blue mug, white, x2", amount, "USD", "www.usmugs.example", card=card,
                                         items=US_ITEMS if mode == "after" else None), ("browser_fill_card", {"card": card}), SUBMIT]


US_TYPED = [("browser_type", {"selector": "input[name=ship_name]", "text": "Jane Roe"}),
            ("browser_type", {"selector": "input[name=ship_a1]", "text": "1600 Amphitheatre Pkwy"}),
            ("browser_type", {"selector": "input[name=ship_a2]", "text": "Bldg 43"}),
            ("browser_type", {"selector": "input[name=ship_city]", "text": "Mountain View"}),
            ("browser_type", {"selector": "input[name=ship_zip]", "text": "94043"}),
            ("browser_type", {"selector": "input[name=ship_phone]", "text": "(650) 253-0000"})]
US_NEXT = ("browser_click", {"selector": "button:has-text('Continue to payment')"})
