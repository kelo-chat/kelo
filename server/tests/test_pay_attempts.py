"""Payments v2 phase P2 (docs/payments-v2.md rules 6 and 7): the attempt journal, the unknown outcome, the later checks at +10 and +30
minutes, the blocks while an order may exist, and the step judge on point presses. Real Chromium (skipped when not installed)."""

from __future__ import annotations

import json
import weakref

import pytest

from assistant.agent import ToolResult
from assistant.order_checks import OrderChecks
from assistant.payment_pins import PaymentLedger
from assistant.pay_terms import FINAL_CHECK_SECONDS, FIRST_CHECK_SECONDS
from assistant.step_judge import StepVerdict
from assistant.tools.browser import BrowserPolicyError, browser_owner, browser_tools, point_tools
from assistant.tools.checkout import CHECKING, REFUSAL

import test_checkout as tc
from test_checkout import ORDER, approved, call, END, ctx, ledger_of, on, results, submit, the_owners_request  # noqa: F401

PAGES = tc.PAGES
PAGES["https://shop.example.com/done"] = "<h1>Thank you</h1><p>Order 4711 confirmed</p>"


_orig_run, PAYMENTS = tc.run, weakref.WeakKeyDictionary()


def _run(session, script, answers, ask=None, **kw):
    kw.setdefault("ledger", PAYMENTS.setdefault(session, PaymentLedger()))   # the older payment ledger, shared by one server's tools
    return _orig_run(session, script, answers, ask, **kw)


tc.run = run = _run


async def result_of(session, args):
    out = await results(run(session, [call("purchase_result", args), END], [True])[0])
    return out[0].output


async def pressed(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    rid = await approved(session)
    await submit(session, rid)
    return session, page, ledger_of(session), rid


def only_attempt(ledger):
    return ledger.store._all("SELECT * FROM pay_attempts")[0]


async def test_the_journal_row_is_reserved_before_the_press_and_pressed_after(ctx):
    session, page = await on(ctx, "https://shop.example.com/co")
    rid = await approved(session)
    ledger, seen = ledger_of(session), []
    real = session.click

    async def spy(sel):
        seen.append(only_attempt(ledger)["state"])   # the row exists while the control is being pressed
        return await real(sel)
    session.click = spy
    await submit(session, rid)
    assert seen == ["reserved"]
    row = only_attempt(ledger)
    assert row["state"] == "pressed" and row["pressed_at"] and row["approval_id"] == rid and row["id"].startswith("pt_")


async def test_a_second_submit_a_ready_review_and_a_reload_are_refused_while_the_order_may_exist(ctx):
    session, page, ledger, rid = await pressed(ctx)
    agen, asked = run(session, [call("checkout_review", {**ORDER, "status": "ready"}), END], [True])
    out = (await results(agen))[0].output
    assert "may already have gone through" in out and asked == []
    out = await submit(session, rid)
    assert "may already have gone through" in out or "already submitted" in out
    tools = {t.name: t for t in browser_tools(session)}
    assert "may already have gone through" in (await tools["browser_open"].hold({"url": "https://shop.example.com/done"}))
    assert await page.evaluate("window.bought || 0") == 1
    tok = CHECKING.set(True)                     # the later check's own run may read and open the tab
    try:
        assert await tools["browser_open"].hold({"url": "https://shop.example.com/done"}) is None
    finally:
        CHECKING.reset(tok)


async def test_unknown_creates_the_plus_10_job_then_the_final_one_then_tells_the_owner_and_stays_unknown(ctx):
    session, page, ledger, rid = await pressed(ctx)
    out = await result_of(session, {"outcome": "unknown", "evidence": "the page shows a spinner"})
    assert "UNKNOWN" in out
    row = only_attempt(ledger)
    jobs = json.loads(row["check_jobs"])
    assert row["state"] == "unknown" and [j["kind"] for j in jobs] == ["first"]
    assert jobs[0]["at"] == pytest.approx(ledger.clock() + FIRST_CHECK_SECONDS)
    out = (await results(run(session, [call("checkout_review", {**ORDER, "status": "ready"}), END], [True])[0]))[0].output
    assert "outcome is unknown" in out and "may already have gone through" in out        # no second approval, no second press
    assert "already submitted" in await submit(session, rid)
    runs, told = [], []

    async def look(system, user, attempt):
        runs.append(attempt["id"])
        return json.dumps({"found": False, "evidence": "no order in the list"})
    checks = OrderChecks(ledger, look, lambda *a: told.append(a))
    assert await checks.tick() == 0                       # not due yet
    ledger.clock.now += FIRST_CHECK_SECONDS + 1
    assert await checks.tick() == 1 and told == []
    jobs = json.loads(only_attempt(ledger)["check_jobs"])
    assert [(j["kind"], j["done"]) for j in jobs] == [("first", True), ("final", False)]
    assert jobs[1]["at"] == pytest.approx(ledger.clock() + FINAL_CHECK_SECONDS)
    ledger.clock.now += FINAL_CHECK_SECONDS + 1
    assert await checks.tick() == 1 and len(told) == 1 and "mail" in told[0][1] or "email" in told[0][1]
    assert only_attempt(ledger)["state"] == "unknown" and await checks.tick() == 0


async def test_a_later_check_that_finds_the_order_with_the_shops_words_makes_it_placed(ctx):
    session, page, ledger, rid = await pressed(ctx)
    await result_of(session, {"outcome": "unknown", "evidence": "no page after the press"})
    seen = []

    async def look(system, user, attempt):
        seen.append(system)
        return json.dumps({"found": True, "evidence": "Order 4711 confirmed"})
    ledger.clock.now += FIRST_CHECK_SECONDS + 1
    await OrderChecks(ledger, look, lambda *a: None).tick()
    assert only_attempt(ledger)["state"] == "placed" and "4711" in only_attempt(ledger)["evidence"]
    assert "cannot pay" in seen[0]
    bare = OrderChecks(ledger, look, lambda *a: None)
    from assistant.order_checks import parse
    assert parse({"found": True, "evidence": ""}) == (False, "") and parse("no json") is None and bare


async def test_the_check_run_has_no_pay_tool():
    from assistant.order_checks import ORDER_CHECK_TOOLS
    assert not {"checkout_review", "checkout_submit", "purchase_result", "confirm_purchase", "browser_click", "browser_type",
                "browser_fill_card"} & set(ORDER_CHECK_TOOLS)


async def test_a_retry_only_after_not_placed_with_its_evidence_is_recorded(ctx):
    session, page, ledger, rid = await pressed(ctx)
    bad = await result_of(session, {"outcome": "not_placed"})
    assert "evidence is required" in bad and only_attempt(ledger)["state"] == "pressed"
    out = await result_of(session, {"outcome": "not_placed", "evidence": "the cart is still full and an error is shown"})
    assert "NOT placed" in out
    row = only_attempt(ledger)
    assert row["state"] == "not_placed" and "cart is still full" in row["evidence"]
    await tc.draft(session)
    rid2, out, asked = await tc.ready(session)
    assert rid2 is not None and len(asked) == 1                  # a retry asks for a new payment approval
    assert "Pressed" in await submit(session, rid2) and len(ledger.store._all("SELECT * FROM pay_attempts")) == 2


async def test_placed_needs_the_shops_confirmation_words_as_evidence(ctx):
    session, page, ledger, rid = await pressed(ctx)
    args = {"order_no": "4711", "charged_total": "24,000 KRW", "items": [{"name": "Blue mug", "quantity": 2}]}
    assert "evidence is required" in await result_of(session, args)
    assert only_attempt(ledger)["state"] == "pressed"
    await result_of(session, {**args, "evidence": "Thank you. Order 4711 confirmed"})
    row = only_attempt(ledger)
    assert row["state"] == "placed" and "4711" in row["receipt_text"] and len(row["receipt_digest"]) == 64


# ---------------------------------------------------------------- point presses on a page without checkout structure
class FakeJudge:
    def __init__(self, verdict):
        self.verdict, self.calls = verdict, 0

    async def web(self, info):
        self.calls += 1
        return self.verdict


async def at_button(ctx, url="https://shop.example.com/form"):
    session, page = await on(ctx, url)
    box = await page.locator("button").first.bounding_box()
    owner = browser_owner.get()
    session._shot_scale[owner], session._look_url[owner] = 1.0, page.url
    return session, page, (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


async def test_a_point_press_on_a_submit_control_is_judged_and_spends_money_is_refused(ctx):
    session, page, (x, y) = await at_button(ctx)
    judge = session.step_judge = FakeJudge(StepVerdict(True, False, False, False, False))
    tools = {t.name: t for t in point_tools(session)}
    assert (await tc.read_page(session)).is_checkout is False
    assert await tools["browser_click_at"].hold({"x": x, "y": y}) == REFUSAL
    assert await tools["browser_drag"].hold({"path": [{"x": x, "y": y}, {"x": x + 1, "y": y}]}) == REFUSAL
    assert await page.evaluate("window.sent || 0") == 0 and judge.calls == 2


async def test_a_harmless_point_press_goes_ahead_and_the_judge_being_down_makes_it_a_card(ctx):
    session, page, (x, y) = await at_button(ctx)
    session.step_judge = FakeJudge(StepVerdict(False, False, False, False, False))
    tools = {t.name: t for t in point_tools(session)}
    assert await tools["browser_click_at"].hold({"x": x, "y": y}) is None
    assert await tools["browser_click_at"].auto_approve({"x": x, "y": y}) is True
    session.step_judge = FakeJudge(None)
    assert await tools["browser_click_at"].hold({"x": x, "y": y}) is None             # not refused ...
    assert await tools["browser_click_at"].auto_approve({"x": x, "y": y}) is False    # ... but a card


async def test_a_point_press_outside_any_form_or_submit_is_not_judged(ctx):
    session, page, (x, y) = await at_button(ctx, "https://shop.example.com/plain")
    judge = session.step_judge = FakeJudge(StepVerdict(True, False, False, False, False))
    tools = {t.name: t for t in point_tools(session)}
    assert await tools["browser_click_at"].hold({"x": x, "y": y}) is None and judge.calls == 0
