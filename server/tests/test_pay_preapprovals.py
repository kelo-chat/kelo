"""Payments v2 phase P3 (docs/payments-v2.md, "P3 build notes"): the one-order pre-approval (`order_preapprove`), the schedule payment
allow, the settings switch and the permissions API. Real Chromium for the checkout parts (skipped when not installed); the spec is
compared to a cart only by the (fake) order check, everything else is structure."""

from __future__ import annotations

import json
import weakref
from types import SimpleNamespace

import pytest

from assistant.agent import ApprovalRequest, run_agent
from assistant.pay_preapprovals import DAY, SWITCH, Preapprovals
from assistant.store import Store
from assistant.tools.browser import browser_tools
from assistant.tools.preapprove import preapprove_tools
from assistant.tools.purchase import purchase_tools
from assistant.workers import current_turn

import test_checkout as tc
from test_checkout import END, ORDER, call, ctx, fake_check, ledger_of, on, results, the_owners_request  # noqa: F401
from test_server import scripted

SPEC = {"intent": "two blue mugs", "items": [{"description": "Blue mug", "qty": 2}], "requirements": ["free returns"],
        "shipping": "home, standard", "merchant_policy": "any shop that meets the requirements", "delegated_choices": ["which shop"],
        "max_total": {"amount": 30000, "currency": "KRW"}}
PRE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()   # session -> Preapprovals (by object: an id is reused once a session is freed)


def stores(session):
    return tc.STORES.setdefault(session, (tc.Reviews(), tc.PayLedger(Store(":memory:"), tc.Clock())))


def pre_of(session) -> Preapprovals:
    led = stores(session)[1]
    return PRE.setdefault(session, Preapprovals(led.store, led.clock))


def run(session, script, answers, ask=None, **kw):
    """Like test_checkout.run, with the P3 tools and the session's Preapprovals."""
    reviews, ledger = stores(session)
    pre = pre_of(session)
    kw.setdefault("reviews", reviews)
    kw.setdefault("pay_ledger", ledger)
    kw.setdefault("ask", ask or fake_check("confirm", within_spec=True))
    kw.setdefault("preapprovals", pre)
    tools = [*browser_tools(session), *purchase_tools(session, **kw), *preapprove_tools(pre, schedule_exists=lambda sid: sid in (5, 6),
                                                                                         tz=lambda: "Asia/Seoul", clock=ledger.clock)]
    asked: list[ApprovalRequest] = []
    taps = iter(answers)

    async def approve(req):
        asked.append(req)
        return next(taps, False)
    return run_agent("p", "m", [{"role": "user", "content": "buy it"}], tools, approve=approve, stream_fn=scripted(*script)), asked


async def preapprove(session, answers=(True,), **over):
    agen, asked = run(session, [call("order_preapprove", {**SPEC, **over}), END], list(answers))
    return (await results(agen))[0].output, asked


async def review(session, status, ask=None, answers=(True,), **over):
    agen, asked = run(session, [call("checkout_review", {**ORDER, "status": status, **over}), END], list(answers), ask=ask)
    out = (await results(agen))[0].output
    first = out.splitlines()[0]
    return (json.loads(first)["review_id"] if first.startswith("{") and "review_id" in first else None), out, asked


async def covered(session, ask=None, **over):
    """draft + ready: (review id, output, the approval cards asked)."""
    await review(session, "draft", ask, (), **over)
    return await review(session, "ready", ask, (True,), **over)


async def submit(session, rid):
    return (await results(run(session, [call("checkout_submit", {"review_id": rid}), END], [])[0]))[0].output


def source_of(session, rid):
    return ledger_of(session).approval(rid)["source"]


@pytest.fixture
def as_schedule():
    """A scheduled run nobody watches: task id and schedule 5 (set again with `again()` for the next run)."""
    def make(task, sid=5):
        return current_turn.set({"task_id": task, "request": "buy two blue mugs", "source": f"schedule:{sid}", "unattended": True})
    token = make(71)

    class Runs:
        again = staticmethod(lambda task, sid=5: current_turn.set({"task_id": task, "request": "buy two blue mugs", "source": f"schedule:{sid}",
                                                                   "unattended": True}))
    yield Runs
    current_turn.reset(token)


# ---------------------------------------------------------------- the tool
async def test_order_preapprove_shows_one_card_stores_the_spec_and_a_second_one_revokes_the_first(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    out, asked = await preapprove(session)
    assert len(asked) == 1 and "two blue mugs" in asked[0].detail and "up to" in asked[0].detail and "30,000" in asked[0].detail
    tool = {t.name: t for t in preapprove_tools(pre_of(session))}["order_preapprove"]
    extra = tool.approval_extra("a1", SPEC)["preapproval"]
    assert extra["max_total"] == 30000 and extra["currency"] == "KRW" and extra["intent"] == "two blue mugs" and extra["owner_kind"] == "task"
    pre = pre_of(session)
    first = pre.active("task", "7")
    assert first is not None and first["max_total_minor"] == 30000 and first["spec"]["items"] == [{"description": "Blue mug", "qty": 2}]
    assert first["expires_at"] == pytest.approx(pre.clock() + 30 * DAY)
    await preapprove(session, max_total={"amount": 20000, "currency": "KRW"})
    second = pre.active("task", "7")
    assert second["id"] != first["id"] and second["max_total_minor"] == 20000
    assert pre.get(first["id"])["state"] == "revoked" and len(pre.list()) == 1


async def test_a_declined_card_stores_nothing(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    out, asked = await preapprove(session, answers=(False,))
    assert pre_of(session).list() == [] and len(asked) == 1


@pytest.mark.parametrize("over,why", [
    ({"max_total": {"amount": 100, "currency": ""}}, "currency"),
    ({"max_total": {"amount": 0, "currency": "KRW"}}, "above 0"),
    ({"items": []}, "items is required"),
    ({"expires_at": 999_000}, "in the past"),
    ({"expires_at": 1_000_000 + 40 * 86400}, "at most 30 days"),
    ({"expires_at": "not a date"}, "ISO 8601"),
    ({"schedule_id": 99}, "existing schedule"),
])
async def test_order_preapprove_refuses_a_bad_spec_without_asking(ctx, over, why):
    session, _ = await on(ctx, "https://shop.example.com/co")
    out, asked = await preapprove(session, **over)
    assert why in out and asked == [] and pre_of(session).list() == []


async def test_an_expiry_inside_30_days_is_kept_and_a_schedule_id_binds_it_to_the_schedule(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    pre = pre_of(session)
    await preapprove(session, expires_at=pre.clock() + 3 * DAY, schedule_id=5)
    row = pre.active("schedule", "5")
    assert row is not None and row["expires_at"] == pytest.approx(pre.clock() + 3 * DAY) and pre.active("task", "7") is None


async def test_a_run_nobody_watches_cannot_ask_for_a_preapproval_unless_it_names_the_schedule(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    token = current_turn.set({"task_id": 9, "request": "x", "unattended": True})
    try:
        out, asked = await preapprove(session)
    finally:
        current_turn.reset(token)
    assert "nobody watches" in out and asked == []


async def test_the_switch_off_refuses_the_tool_and_covers_nothing(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    pre = pre_of(session)
    await preapprove(session)
    await review(session, "draft", None, ())
    pre.set_enabled(False)
    assert pre.store.meta(SWITCH) == "1"
    out, asked = await preapprove(session)
    assert "switched off" in out and asked == []
    rid, out, asked = await review(session, "ready")
    assert rid is not None and len(asked) == 1 and source_of(session, rid) == "card"      # the card, as without a pre-approval


# ---------------------------------------------------------------- covering one order
async def test_a_matched_order_within_the_all_in_maximum_needs_no_card_and_the_spec_is_used_up(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    await preapprove(session)
    ask = fake_check("confirm", within_spec=True)
    rid, out, asked = await covered(session, ask)
    assert rid is not None and asked == [] and source_of(session, rid) == "preapproval"
    assert "approved in advance" in out
    system = ask.calls[0][0]
    assert "within_spec" in system and "Blue mug" in ask.calls[0][1] and "free returns" in ask.calls[0][1]
    pre = pre_of(session)
    spec = pre.active("task", "7")
    assert spec["matched_digest"] and spec["matched_origin"] == "https://shop.example.com"
    await submit(session, rid)
    assert await page.evaluate("window.bought || 0") == 1
    assert pre.active("task", "7") is None and pre.get(spec["id"])["state"] == "used"
    again, out, asked = await review(session, "ready")
    assert again is None or source_of(session, again) != "preapproval"                 # one order only: a second one is no longer covered


@pytest.mark.parametrize("spec_over,ask", [
    ({"max_total": {"amount": 23999, "currency": "KRW"}}, fake_check("confirm", within_spec=True)),    # the page's 24,000 is above the cap
    ({}, fake_check("confirm", within_spec=False)),                                                    # the check says: not this order
    ({}, fake_check("confirm")),                                                                       # no within_spec at all
    ({"max_total": {"amount": 300, "currency": "USD"}}, fake_check("confirm", within_spec=True)),     # another currency
])
async def test_above_the_maximum_another_currency_or_not_matched_asks_the_card(ctx, spec_over, ask):
    session, _ = await on(ctx, "https://shop.example.com/co")
    await preapprove(session, **spec_over)
    rid, out, asked = await covered(session, ask)
    assert rid is not None and len(asked) == 1 and source_of(session, rid) == "card"
    assert pre_of(session).active("task", "7") is not None                                            # not used up by a card payment


async def test_a_conditional_charge_counts_in_the_all_in_maximum(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    await preapprove(session, max_total={"amount": 25000, "currency": "KRW"})
    rid, out, asked = await covered(session, fake_check("confirm", within_spec=True), conditional_total=2000, conditional_terms="late fee")
    assert len(asked) == 1 and source_of(session, rid) == "card"


async def test_other_items_change_the_digest_and_need_a_new_match(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    await preapprove(session)
    await review(session, "draft", fake_check("confirm", within_spec=True), ())
    other = {"items": [{"name": "Red mug", "qty": 2, "unit_price": "12,000"}]}
    await review(session, "draft", fake_check("confirm", within_spec=False), (), **other)      # the check says: not the spec
    rid, out, asked = await review(session, "ready", None, (True,), **other)
    assert len(asked) == 1 and source_of(session, rid) == "card"


async def test_a_spec_removed_after_the_review_stops_the_press_and_nothing_is_pressed(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    await preapprove(session)
    rid, out, asked = await covered(session, fake_check("confirm", within_spec=True))
    assert asked == [] and source_of(session, rid) == "preapproval"
    pre = pre_of(session)
    assert pre.revoke(pre.active("task", "7")["id"])
    out = await submit(session, rid)
    assert "Nothing was paid" in out and "advance approval" in out
    assert await page.evaluate("window.bought || 0") == 0


async def test_an_expired_spec_covers_nothing(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    pre = pre_of(session)
    await preapprove(session, expires_at=pre.clock() + 3600)
    await review(session, "draft", fake_check("confirm", within_spec=True), ())
    pre.clock.now += 7200
    rid, out, asked = await review(session, "ready")
    assert len(asked) == 1 and source_of(session, rid) == "card"


async def test_an_expired_spec_in_an_unattended_schedule_run_stops_it_and_never_asks(ctx, as_schedule):
    session, _ = await on(ctx, "https://shop.example.com/co")
    pre = pre_of(session)
    await preapprove(session, expires_at=pre.clock() + 3600, schedule_id=5)
    pre.clock.now += 7200
    assert pre.lapsed("schedule", "5")
    await review(session, "draft", fake_check("confirm", within_spec=True), ())
    rid, out, asked = await review(session, "ready")
    assert rid is None and "expired" in out and "never asks for a new approval" in out and asked == []


async def test_the_spec_of_a_schedule_covers_that_schedules_run(ctx, as_schedule):
    session, _ = await on(ctx, "https://shop.example.com/co")
    await preapprove(session, schedule_id=5)
    rid, out, asked = await covered(session, fake_check("confirm", within_spec=True))
    assert asked == [] and source_of(session, rid) == "preapproval"
    assert pre_of(session).get(ledger_of(session).approval(rid)["approval_id"])["owner_kind"] == "schedule"


# ---------------------------------------------------------------- the schedule payment allow
async def test_an_allow_on_a_scheduled_runs_card_covers_later_runs_of_the_same_shop_and_payment_method(ctx, as_schedule):
    session, _ = await on(ctx, "https://shop.example.com/co")
    pre = pre_of(session)
    plain = fake_check("confirm")
    rid, out, asked = await covered(session, plain)
    assert len(asked) == 1 and source_of(session, rid) == "card"
    assert "later orders" in asked[0].detail                                       # the card says what Allow also covers
    rows = pre.allows()
    assert len(rows) == 1 and rows[0]["merchant_origin"] == "https://shop.example.com" and rows[0]["payment_label"] == "site balance"
    assert rows[0]["schedule_id"] == 5 and rows[0]["currency"] == "KRW"
    tc_ledger = ledger_of(session)
    tc_ledger.store._run("UPDATE pay_attempts SET state = 'not_placed'")               # the first run ended; nothing is open
    as_schedule.again(72)
    rid2, out, asked = await covered(session, plain)
    assert rid2 is not None and asked == [] and source_of(session, rid2) == "schedule_allow"
    assert "Allow the user gave this schedule" in out
    as_schedule.again(73)
    rid3, out, asked = await covered(session, plain, payment_method="another method")      # another payment method asks
    assert len(asked) == 1 and source_of(session, rid3) == "card"
    as_schedule.again(74, 6)
    rid4, out, asked = await covered(session, plain)                                         # another schedule asks
    assert len(asked) == 1 and source_of(session, rid4) == "card"


async def test_an_allow_of_another_shop_or_currency_covers_nothing_here(ctx, as_schedule):
    session, _ = await on(ctx, "https://shop.example.com/co")
    pre = pre_of(session)
    pre.allow_add(5, "https://other.example.com", "site balance", "KRW")                       # another shop
    pre.allow_add(5, "https://shop.example.com", "site balance", "EUR")                        # another currency
    rid, out, asked = await covered(session)
    assert len(asked) == 1 and source_of(session, rid) == "card"


async def test_an_allow_covers_nothing_in_a_chat(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    pre_of(session).allow_add(5, "https://shop.example.com", "site balance", "KRW")
    rid, out, asked = await covered(session)                                                     # the autouse fixture: an attended chat
    assert len(asked) == 1 and source_of(session, rid) == "card"
    assert len(pre_of(session).allows()) == 1                                                    # and a chat card makes no allow


async def test_an_allow_is_deleted_with_its_schedule_and_by_the_owner_and_the_switch_stops_it(ctx):
    session, _ = await on(ctx, "https://shop.example.com/co")
    pre = pre_of(session)
    a = pre.allow_add(5, "https://shop.example.com", "site balance", "KRW")
    assert pre.allow_add(5, "https://shop.example.com", "site balance", "KRW") == a            # one row per shop + method
    assert pre.allow_for(5, "https://shop.example.com", "site balance", "KRW") is not None
    pre.set_enabled(False)
    assert pre.allow_for(5, "https://shop.example.com", "site balance", "KRW") is None
    pre.set_enabled(True)
    pre.drop_schedule(5)
    assert pre.allows() == []
    b = pre.allow_add(5, "https://shop.example.com", "site balance", "KRW")
    assert pre.allow_remove(b) and not pre.allow_remove(b)


async def test_an_allow_removed_after_the_review_stops_the_press(ctx, as_schedule):
    session, page = await on(ctx, "https://shop.example.com/co")
    pre = pre_of(session)
    pre.allow_add(5, "https://shop.example.com", "site balance", "KRW")
    rid, out, asked = await covered(session)
    assert asked == [] and source_of(session, rid) == "schedule_allow"
    pre.allow_remove(pre.allows()[0]["id"])
    assert "Nothing was paid" in await submit(session, rid)
    assert await page.evaluate("window.bought || 0") == 0


# ---------------------------------------------------------------- the pure rules and the API
def test_covers_is_structure_only():
    from assistant.pay_terms import Terms
    store = Store(":memory:")
    clock = tc.Clock()
    pre = Preapprovals(store, clock)
    terms = Terms("https://a.example.com", "A", [{"name": "x", "qty": 1, "unit_minor": 100, "line_minor": 100, "variants": [], "obligations": []}],
                  {}, "EUR", 5000, 500)
    pid = pre.create("task", "1", {"intent": "x"}, 5500, "EUR", clock() + 100)
    assert not pre.covers(pre.active("task", "1"), terms)                                       # not matched yet
    pre.matched(pid, terms.digest, "https://a.example.com")
    spec = pre.active("task", "1")
    assert pre.covers(spec, terms)
    assert not pre.covers(spec, Terms(terms.merchant_origin, "A", terms.items, {}, "EUR", 5001, 500))     # 5501 > 5500
    assert not pre.covers(spec, Terms("https://b.example.com", "A", terms.items, {}, "EUR", 5000, 0))
    assert not pre.covers(spec, Terms(terms.merchant_origin, "A", terms.items, {}, "USD", 5000, 0))
    clock.now += 200
    assert not pre.covers(spec, terms) and pre.active("task", "1") is None and pre.lapsed("task", "1")


def api(pre):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from assistant.api import grants
    ctx = SimpleNamespace(require_auth=lambda: None, schedule_grants=None, watchers=None, workers=None, preapprovals=pre)
    app = FastAPI()
    app.include_router(grants.router(ctx))
    return TestClient(app)


def test_the_permissions_api_lists_removes_and_switches():
    store, clock = Store(":memory:"), tc.Clock()
    pre = Preapprovals(store, clock, schedule_name=lambda sid: {5: "Mugs every Monday"}.get(sid, ""))
    c = api(pre)
    pid = pre.create("schedule", "5", {"intent": "two mugs", "items": [{"description": "mug", "qty": 2}], "requirements": [], "shipping": "home",
                                       "merchant_policy": "", "delegated_choices": []}, 30000, "KRW", clock() + DAY)
    pre.create("task", "9", {"intent": "gone"}, 100, "KRW", clock() + DAY)
    pre.revoke(pre.active("task", "9")["id"])
    aid = pre.allow_add(5, "https://shop.example.com", "site balance", "KRW")
    got = c.get("/api/permissions/preapprovals").json()
    assert got["enabled"] is True and [i["id"] for i in got["items"]] == [pid]
    item = got["items"][0]
    assert item["owner_kind"] == "schedule" and item["owner_name"] == "Mugs every Monday" and item["max_total"] == 30000 and item["intent"] == "two mugs"
    allows = c.get("/api/permissions/pay-allows").json()["items"]
    assert allows == [{"id": aid, "schedule_id": 5, "schedule_name": "Mugs every Monday", "merchant_origin": "https://shop.example.com",
                       "payment_label": "site balance", "currency": "KRW", "created_at": int(clock())}]
    assert c.put("/api/permissions/payment-cover", json={"enabled": False}).json() == {"enabled": False}
    assert c.get("/api/permissions/payment-cover").json() == {"enabled": False} and c.get("/api/permissions/preapprovals").json()["enabled"] is False
    assert c.put("/api/permissions/payment-cover", json={"enabled": "no"}).status_code == 422
    assert c.delete(f"/api/permissions/preapprovals/{pid}").json() == {"ok": True} and c.delete(f"/api/permissions/preapprovals/{pid}").status_code == 404
    assert c.delete(f"/api/permissions/pay-allows/{aid}").json() == {"ok": True} and c.delete(f"/api/permissions/pay-allows/{aid}").status_code == 404
    assert c.get("/api/permissions/preapprovals").json()["items"] == [] and c.get("/api/permissions/pay-allows").json()["items"] == []
