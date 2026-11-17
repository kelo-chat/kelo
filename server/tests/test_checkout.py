"""checkout_review / checkout_submit and the structure-only refusal of plain clicks on a final checkout step (tools/checkout.py), in a
real Chromium (skipped when Playwright's browser is not installed). Pages are served from memory under fake https addresses; nothing
goes to the network. No word of any language decides anything here: the pages differ in structure, not in their labels."""

from __future__ import annotations

import json
import weakref

import pytest

from assistant import approval_sign
from assistant.agent import ApprovalRequest, ToolResult, run_agent
from assistant.providers import Done, TextDelta, ToolCall
from assistant.tools import checkout
from assistant.tools.browser import BrowserSession, browser_tools
from assistant.pay_terms import PayLedger
from assistant.store import Store
from assistant.step_judge import StepVerdict
from assistant.tools.checkout import REFUSAL, Reviews, read_page
from assistant.workers import current_turn
from assistant.tools.purchase import purchase_tools

from test_purchase import PUBLIC
from test_server import scripted

pw = pytest.importorskip("playwright.async_api")

BUY = """<button id=buy type=submit onclick="document.title='bought';window.bought=(window.bought||0)+1;return false">%s</button>"""
PAGES = {
    # a card field by its autocomplete token, an order total element, a submit
    "https://shop.example.com/co": "<h1>Order</h1><form id=f><input autocomplete=cc-number><span data-total>24,000</span>" + BUY % "Zahlen" + "</form>",
    # no card field, but an order total next to a submit control inside one form
    "https://shop.example.com/co-total": "<form><p>Total <output>24,000</output></p>" + BUY % "ok" + "</form>",
    # an order total beside a button that is not in any form (a script-driven checkout)
    "https://shop.example.com/co-js": "<div><span itemprop=totalPaymentDue>24,000</span><div role=button id=buy onclick=\"window.bought=1\">go</div></div>",
    # the payment fields live in a cross-origin frame (a second origin), found by the card inputs in it
    "https://shop.example.com/co-frame": "<p>Total 24,000</p><iframe src='https://frames.example.net/card' width=400 height=300></iframe>"
                                         "<button id=buy>x</button>",
    "https://frames.example.net/card": "<form><input autocomplete=cc-number><input autocomplete=cc-exp><input autocomplete=cc-csc></form>",
    "https://frames.example.net/plain": "<form><input name=q><input type=text name=n></form>",
    "https://frames.example.net/numeric": "<form><input inputmode=numeric><span data-total>24,000</span><button>x</button></form>",
    # numeric input inside a form that holds an amount element and a submit
    "https://shop.example.com/co-numeric": "<form><input inputmode=numeric><span data-total>24,000</span><button>x</button></form>",
    "https://pay.shop.example.com/co": "<h1>Order</h1><form id=f><input autocomplete=cc-number><span data-total>24,000</span>" + BUY % "Zahlen" + "</form>",
    # not checkout steps
    "https://shop.example.com/product": "<h1>Mug</h1><form><span itemprop=price>12,000</span><input type=number value=1><button>Add</button></form>",
    "https://shop.example.com/search": "<form><input inputmode=numeric><button>Search</button></form><p>Total results 24,000</p>",
    "https://shop.example.com/hidden-frame": "<p>Mug</p><iframe src='https://frames.example.net/card' width=1 height=1></iframe>",
    "https://shop.example.com/other-frame": "<iframe src='https://frames.example.net/plain' width=400 height=300></iframe>",
    "https://shop.example.com/numeric-frame": "<iframe src='https://frames.example.net/numeric' width=400 height=300></iframe>",
    "https://shop.example.com/slow-frame": "<iframe src='https://frames.example.net/slow' width=400 height=300></iframe>",
    # a submit control inside a form, with no checkout structure (no payment inputs, no total)
    "https://shop.example.com/form": "<form><input name=q2><button id=go type=submit onclick=\"window.sent=(window.sent||0)+1;return false\">Go</button></form>",
    "https://shop.example.com/plain": "<h1>Hello</h1><button>Pay</button><a href='/x'>Buy now</a>",
}
ORDER = {"site": "shop.example.com", "merchant": "Example Shop", "items": [{"name": "Blue mug", "qty": 2, "unit_price": "12,000"}],
         "total": 24000, "currency": "KRW", "payment_method": "site balance", "delivery": "standard", "cancellation": "free until shipped"}


@pytest.fixture
async def ctx():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context()

        async def serve(route):
            body = PAGES.get(route.request.url.split("?")[0])
            await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><title>t</title><body>{body or 'not found'}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()


async def on(ctx, url):
    page = await ctx.new_page()

    async def connect():
        return page
    session = BrowserSession(connect=connect, resolver=PUBLIC)
    await (await session.page()).goto(url)
    return session, page


# ---------------------------------------------------------------- the structure
@pytest.mark.parametrize("path,is_step", [
    ("co", True), ("co-total", True), ("co-js", True), ("co-frame", True), ("co-numeric", True), ("numeric-frame", True),
    ("product", False), ("search", False), ("hidden-frame", False), ("other-frame", False), ("slow-frame", False), ("plain", False),
])
async def test_a_page_is_a_final_checkout_step_by_its_structure_only(ctx, path, is_step):
    session, _ = await on(ctx, "https://shop.example.com/" + path)
    probe = await read_page(session)
    assert probe is not None and probe.is_checkout is is_step


async def test_no_origin_or_provider_list_exists_in_code_or_content():
    from pathlib import Path
    src = Path(checkout.__file__).read_text(encoding="utf-8").lower()
    assert "stripe" not in src and "paypal" not in src and "payment_frames" not in src
    assert not (Path(checkout.__file__).resolve().parents[3] / "content" / "payment_frames.json").exists()


async def test_a_frame_that_cannot_be_read_is_unknown_not_payment(ctx):
    session, _ = await on(ctx, "https://shop.example.com/slow-frame")
    probe = await read_page(session)
    assert probe is not None and probe.frames == [] and not probe.is_checkout


async def test_plain_clicks_and_enter_are_refused_on_a_checkout_step_but_not_elsewhere(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    tools = {t.name: t for t in browser_tools(session)}
    assert await tools["browser_click"].hold({"selector": "#buy"}) == REFUSAL
    assert "checkout_review / checkout_submit" in REFUSAL
    assert await tools["browser_type"].hold({"selector": "input", "text": "x", "submit": True}) == REFUSAL
    assert await tools["browser_type"].hold({"selector": "input", "text": "x"}) is None      # typing alone is not a submit
    assert await page.evaluate("window.bought || 0") == 0
    plain, _ = await on(ctx, "https://shop.example.com/plain")
    other = {t.name: t for t in browser_tools(plain)}
    assert await other["browser_click"].hold({"selector": "button"}) is None                  # a "Pay" label decides nothing


# ---------------------------------------------------------------- review and submit (docs/payments-v2.md, phase P1)
STORES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()   # session -> (Reviews, PayLedger); by object, never by id (an id is reused once a session is freed)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def fake_check(verdict="confirm", **extra):
    """The order check's one model request: records every call, answers `verdict`."""
    calls: list[tuple[str, str]] = []

    async def ask(system, user):
        calls.append((system, user))
        return {"verdict": verdict, "reason": "fine", **extra} if verdict else None
    ask.calls = calls
    return ask


def run(session, script, answers, ask=None, **kw):
    reviews, ledger = STORES.setdefault(session, (Reviews(), PayLedger(Store(":memory:"), Clock())))   # one server keeps its state
    kw.setdefault("reviews", reviews)
    kw.setdefault("pay_ledger", ledger)
    kw.setdefault("ask", ask or fake_check())
    tools = [*browser_tools(session), *purchase_tools(session, **kw)]
    asked: list[ApprovalRequest] = []
    taps = iter(answers)

    async def approve(req):
        asked.append(req)
        return next(taps, False)
    return run_agent("p", "m", [{"role": "user", "content": "buy it"}], tools, approve=approve, stream_fn=scripted(*script)), asked


def ledger_of(session) -> PayLedger:
    return STORES[session][1]


def call(name, args, cid="c1"):
    return [ToolCall(cid, name, json.dumps(args)), Done("tool_calls")]


END = [TextDelta("done"), Done("stop")]


@pytest.fixture(autouse=True)
def the_owners_request():
    token = current_turn.set({"task_id": 7, "request": "buy two blue mugs", "unattended": False})
    yield
    current_turn.reset(token)


async def results(agen):
    return [ev async for ev in agen if isinstance(ev, ToolResult)]


async def draft(session, ask=None, **over):
    out = await results(run(session, [call("checkout_review", {**ORDER, "status": "draft", **over}), END], [], ask=ask)[0])
    return out[0].output


async def ready(session, answers=(True,), ask=None, **over):
    agen, asked = run(session, [call("checkout_review", {**ORDER, "status": "ready", **over}), END], list(answers), ask=ask)
    out = (await results(agen))[0].output
    first = out.splitlines()[0]
    return (json.loads(first)["review_id"] if first.startswith("{") and "review_id" in first else None), out, asked


async def approved(session, ask=None, **over):
    await draft(session, ask, **over)
    rid, out, asked = await ready(session, ask=ask, **over)
    assert rid is not None, out
    return rid


async def submit(session, rid):
    return (await results(run(session, [call("checkout_submit", {"review_id": rid}), END], [])[0]))[0].output


async def test_draft_asks_nobody_runs_the_order_check_then_ready_asks_once_and_submit_presses(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    ask = fake_check()
    agen, asked = run(session, [call("checkout_review", {**ORDER, "status": "draft"}), END], [], ask=ask)
    out = (await results(agen))[0].output
    assert asked == [] and json.loads(out.splitlines()[0])["verdict"] == "confirm" and "status ready" in out
    assert len(ask.calls) == 1 and "buy two blue mugs" in ask.calls[0][1] and "Blue mug" in ask.calls[0][1]   # the owner's words, the terms
    rid, out, asked = await ready(session, ask=ask)
    assert len(asked) == 1 and asked[0].name == "checkout_review" and "Blue mug" in asked[0].detail and "Zahlen" in out
    assert len(ask.calls) == 1                                  # the ready step did not ask the model again
    row = ledger_of(session).approval(rid)
    assert (row["approved_total_minor"], row["currency"], row["state"], row["source"]) == (24000, "KRW", "approved", "card")
    assert row["merchant_origin"] == "https://shop.example.com"
    done = await submit(session, rid)
    assert "Pressed the recorded pay control" in done and await page.evaluate("window.bought || 0") == 1
    assert ledger_of(session).approval(rid)["state"] == "used"
    assert "already submitted" in await submit(session, rid) and await page.evaluate("window.bought || 0") == 1


async def test_ready_needs_a_passed_order_check_on_the_same_terms(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    rid, out, asked = await ready(session)                          # no draft at all
    assert rid is None and "order check has not passed" in out and asked == []
    await draft(session, fake_check("change", change="one mug only"))
    rid, out, asked = await ready(session)
    assert rid is None and "order check has not passed" in out and asked == []
    await draft(session, fake_check("open_choice", choice="which colour"))
    rid, out, _ = await ready(session)
    assert rid is None and "order check has not passed" in out


async def test_the_order_check_answers_change_and_open_choice_and_fails_closed(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    out = await draft(session, fake_check("change", change="one mug only"))
    assert json.loads(out.splitlines()[0])["change"] == "one mug only" and "does not match" in out
    out = await draft(session, fake_check("open_choice", choice="which colour"))
    verdict = json.loads(out.splitlines()[0])
    assert verdict["verdict"] == "open_choice" and verdict["choice"] == "which colour" and "name the open choice" in out
    for broken in (fake_check(None), fake_check("maybe")):         # no answer / an unknown verdict word
        assert json.loads((await draft(session, broken)).splitlines()[0])["verdict"] == "open_choice"
    no_model = json.loads((await draft(session, None, items=[{"name": "Other mug", "qty": 1}])).splitlines()[0])   # no ask wired: a fake with nothing
    assert no_model["verdict"] == "confirm" or no_model["verdict"] == "open_choice"
    token = current_turn.set({"task_id": 8, "request": "", "unattended": True})     # no request words of the owner: nothing to check against
    try:
        ask = fake_check()
        assert json.loads((await draft(session, ask)).splitlines()[0])["verdict"] == "open_choice" and ask.calls == []
    finally:
        current_turn.reset(token)


async def test_a_higher_total_after_the_approval_is_refused_and_nothing_is_pressed(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    rid = await approved(session)
    await page.evaluate("document.querySelector('[data-total]').textContent = '99,000'")
    out = await submit(session, rid)
    assert "Nothing was paid" in out and "higher than the approved total" in out and await page.evaluate("window.bought || 0") == 0


async def test_another_currency_or_another_subdomain_is_refused(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    rid = await approved(session)
    await page.evaluate("document.body.insertAdjacentHTML('beforeend', '<meta itemprop=priceCurrency content=USD>')")
    out = await submit(session, rid)
    assert "Nothing was paid" in out and "currency" in out and await page.evaluate("window.bought || 0") == 0
    await page.goto("https://pay.shop.example.com/co")                  # another host is another site
    out = await submit(session, rid)
    assert "Nothing was paid" in out and "another site" in out and await page.evaluate("window.bought || 0") == 0


async def test_an_expired_approval_is_refused(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    rid = await approved(session)
    ledger_of(session).clock.now += 21 * 60
    out = await submit(session, rid)
    assert "Nothing was paid" in out and "expired" in out and await page.evaluate("window.bought || 0") == 0


async def test_a_changed_quantity_needs_a_new_order_check_even_with_a_lower_total(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    ask = fake_check()
    await approved(session, ask)
    await page.evaluate("document.querySelector('[data-total]').textContent = '12,000'")
    one = [{"name": "Blue mug", "qty": 1, "unit_price": "12,000"}]
    rid, out, asked = await ready(session, ask=ask, items=one, total=12000)
    assert rid is None and "order check has not passed" in out and asked == []
    await draft(session, ask, items=one, total=12000)
    assert len(ask.calls) == 2                                           # a new order check was run


async def test_a_change_of_only_the_total_needs_a_new_approval_only_when_it_is_above_the_approved_one(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    ask = fake_check()
    await approved(session, ask)
    await page.evaluate("document.querySelector('[data-total]').textContent = '20,000'")
    out = await draft(session, ask, total=20000)
    assert "terms update" in out and len(ask.calls) == 1                 # no new order check
    rid, out, asked = await ready(session, ask=ask, total=20000)
    assert rid is not None and asked == []                               # within the approved total: no new ask
    await page.evaluate("document.querySelector('[data-total]').textContent = '30,000'")
    await draft(session, ask, total=30000)
    assert len(ask.calls) == 1
    rid2, _, asked = await ready(session, ask=ask, total=30000)
    assert rid2 is not None and len(asked) == 1                          # above the approved total: the owner is asked again
    assert ledger_of(session).approval(rid2)["approved_total_minor"] == 30000


async def test_a_draft_after_ready_replaces_the_ready_terms_so_submit_is_refused(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    rid = await approved(session)
    await draft(session)
    out = await submit(session, rid)
    assert "Nothing was paid" in out and "terms were changed" in out and await page.evaluate("window.bought || 0") == 0


async def test_the_review_card_is_a_payment_card_and_asks_even_with_a_remembered_rule(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    await draft(session)
    rid, out, asked = await ready(session, answers=(False,))
    assert rid is None and len(asked) == 1 and asked[0].name == "checkout_review" and "did NOT approve" in out
    assert approval_sign.is_payment({"name": "checkout_review"})
    from assistant.activity import NEVER_REMEMBER
    assert "checkout_review" in NEVER_REMEMBER and "checkout_submit" in NEVER_REMEMBER
    tool = {t.name: t for t in purchase_tools(session)}["checkout_review"]
    assert tool.requires_approval and (tool.auto_approve is None or tool.auto_approve({**ORDER, "status": "ready"}) is False)
    assert tool.auto_approve({**ORDER, "status": "draft"}) is True      # only a draft asks nobody


async def test_a_review_needs_the_total_on_the_page_and_a_control_to_press(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    out = await results(run(session, [call("checkout_review", {**ORDER, "total": 99999}), END], [True])[0])
    assert "shows no total of 99999" in out[0].output
    plain, _ = await on(ctx, "https://shop.example.com/plain")
    out = await results(run(plain, [call("checkout_review", {**ORDER, "total": 0, "status": "ready"}), END], [True])[0])
    assert "No pay control was found" in out[0].output
    bad = await results(run(session, [call("checkout_review", {**ORDER, "currency": "$"}), END], [])[0])
    assert "currency is required" in bad[0].output


async def test_submit_refuses_when_the_page_changed_since_the_review(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    rid = await approved(session)
    await page.evaluate("document.querySelector('#buy').textContent = 'Other'")                  # the control's name changed
    assert "the page changed: review again" in await submit(session, rid)
    await page.evaluate("document.querySelector('#buy').textContent = 'Zahlen'")
    await page.goto("https://shop.example.com/co-total")                                         # another page
    assert "the page changed: review again" in await submit(session, rid)
    assert await page.evaluate("window.bought || 0") == 0


async def test_submit_only_for_a_known_approved_review_of_this_tab(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    tool = {t.name: t for t in purchase_tools(session)}["checkout_submit"]
    assert "no such review" in tool.validate({"review_id": "rv_nope"})
    store = Reviews()
    probe = await read_page(session)
    rv = store.add("owner", "shop.example.com", probe, "button", "Zahlen", 24000, "KRW")
    assert not rv.approved and store.get(rv.id) is rv
    other = {t.name: t for t in purchase_tools(session, reviews=store)}["checkout_submit"]
    assert "not approved" in other.validate({"review_id": rv.id})
    rv.approved = True
    assert other.validate({"review_id": rv.id}) is None
    rv.used = True
    assert "already submitted" in other.validate({"review_id": rv.id})


async def test_the_standing_rules_of_the_owner_skip_the_card_but_not_the_page_checks(ctx):
    from assistant.wallet import CardDeliveries, Wallet
    from test_wallet import auto_card
    from cryptography.fernet import Fernet
    import tempfile
    from pathlib import Path
    store = Store(Path(tempfile.mkdtemp()) / "w.db", Fernet.generate_key().decode())
    wallet = Wallet(store)
    card = auto_card(wallet)
    session, _ = await on(ctx, "https://shop.example.com/co")
    tool = {t.name: t for t in purchase_tools(session, wallet, CardDeliveries())}["checkout_review"]
    args = {**ORDER, "status": "ready", "card": card["label"], "payment_method": None}
    held = await tool.hold({**args, "total": 99999})
    assert "shows no total" in held                              # the page is read first
    assert await tool.hold(args) is None
    assert tool.auto_approve(args) is False                      # a first payment on a site asks, as before
    wallet.record("approved", card, "shop.example.com", "Blue mug", 12000, "KRW")
    assert tool.auto_approve(args) is True                       # the auto-pay rule covers a site paid at before, within its limits


# ---------------------------------------------------------------- the safety net: a submit control without checkout structure
class FakeJudge:
    def __init__(self, verdict):
        self.verdict, self.calls = verdict, 0

    async def web(self, info):
        self.calls += 1
        return self.verdict


async def test_a_submit_on_a_page_without_checkout_structure_is_judged_and_spends_money_is_refused(ctx):
    session, page = await on(ctx, "https://shop.example.com/form")
    session.step_judge = FakeJudge(StepVerdict(True, False, False, False, False))
    tools = {t.name: t for t in browser_tools(session)}
    assert (await read_page(session)).is_checkout is False
    assert await tools["browser_click"].hold({"selector": "#go"}) == REFUSAL
    assert await page.evaluate("window.sent || 0") == 0
    assert await tools["browser_type"].hold({"selector": "input", "text": "x", "submit": True}) == REFUSAL
    assert await tools["browser_type"].hold({"selector": "input", "text": "x"}) is None


async def test_a_harmless_submit_goes_ahead_and_the_verdict_is_asked_once_per_control(ctx):
    session, _ = await on(ctx, "https://shop.example.com/form")
    judge = session.step_judge = FakeJudge(StepVerdict(False, False, False, False, False))
    tools = {t.name: t for t in browser_tools(session)}
    assert await tools["browser_click"].hold({"selector": "#go"}) is None
    assert await tools["browser_click"].auto_approve({"selector": "#go"}) is True


async def test_the_judge_being_down_asks_the_owner_and_no_judge_wired_changes_nothing(ctx):
    session, _ = await on(ctx, "https://shop.example.com/form")
    session.step_judge = FakeJudge(None)
    tools = {t.name: t for t in browser_tools(session)}
    assert await tools["browser_click"].hold({"selector": "#go"}) is None            # not refused ...
    assert await tools["browser_click"].auto_approve({"selector": "#go"}) is False   # ... but a card
    session.step_judge = None
    other, _ = await on(ctx, "https://shop.example.com/form")
    assert await {t.name: t for t in browser_tools(other)}["browser_click"].hold({"selector": "#go"}) is None


async def test_a_send_found_by_the_judge_asks_a_card(ctx):
    session, _ = await on(ctx, "https://shop.example.com/form")
    session.step_judge = FakeJudge(StepVerdict(False, True, False, False, False))
    tools = {t.name: t for t in browser_tools(session)}
    assert await tools["browser_click"].hold({"selector": "#go"}) is None
    assert await tools["browser_click"].auto_approve({"selector": "#go"}) is False
