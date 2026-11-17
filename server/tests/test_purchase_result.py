"""purchase_result: after a payment, the confirmation page is checked against what was approved; a wrong charge is cancelled at once
only when that is free, else the owner decides. Neutral fake shop."""

import json
import time

from assistant.agent import ToolResult, run_agent
from assistant.payment_pins import PaymentLedger
from assistant.providers import Done, ToolCall
from assistant.receipt import cancel_terms, compare, parse_money
from assistant.tools.browser import browser_owner, browser_tools
from assistant.tools.purchase import RESULT_CHECKED, purchase_tools
from assistant.workers import current_turn

from test_purchase import END, ORDER, PAY, by_name, shop
from test_server import scripted

ITEMS = [{"name": "Blue mug", "quantity": 2}]
PAY2 = [ToolCall("c2", "confirm_purchase", json.dumps({**ORDER, "items": ITEMS})), Done("tool_calls")]
GOOD = {"order_no": "A-1", "charged_total": "24,000원", "items": [{"name": "Blue mug", "quantity": 2}]}
FREE = {"free_until": "2999-01-01T00:00:00Z", "how": "Cancel order"}
CANCEL_CLICK = [ToolCall("c5", "browser_click", '{"selector": "text=Cancel order"}'), Done("tool_calls")]


def result_call(args, cid="c4"):
    return [ToolCall(cid, "purchase_result", json.dumps(args)), Done("tool_calls")]


async def paid(task_id=7):
    session, page = shop()
    ledger, notes = PaymentLedger(), []
    current_turn.set({"task_id": task_id})
    tools = [*browser_tools(session, approve_actions=True), *purchase_tools(session, ledger=ledger, notify=lambda *a: notes.append(a))]
    await session.page()
    session.use_purchase_action = lambda: False     # the purchase's own click window is over (browser_open ends it): only the cancel window counts
    return session, page, ledger, tools, notes


async def drive(tools, script, answers):
    asked, taps = [], iter(answers)

    async def approve(req):
        asked.append(req)
        return next(taps, False)
    results = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "buy"}], tools, approve=approve,
                                            stream_fn=scripted(*script)) if isinstance(ev, ToolResult)]
    return results, asked


def only(ledger):
    return next(iter(ledger._last.values()))


async def test_matching_receipt_closes_the_check():
    _, _, ledger, tools, _ = await paid()
    res, asked = await drive(tools, [PAY2, result_call(GOOD), END], [True])
    assert RESULT_CHECKED in res[-1].output and "A-1" in res[-1].output
    assert [a.name for a in asked] == ["confirm_purchase"]          # no card for a matching receipt
    assert only(ledger).state == "closed" and not ledger.unchecked(7)


async def test_a_lower_total_is_fine_and_said():
    _, _, _, tools, _ = await paid()
    res, _ = await drive(tools, [PAY2, result_call({**GOOD, "charged_total": "20,000원"}), END], [True])
    assert "lower" in res[-1].output and "fine" in res[-1].output


async def test_a_higher_total_with_free_cancel_opens_the_window_notifies_and_logs():
    s, page, ledger, tools, notes = await paid()
    logged = []
    s.on_cancel_click = lambda host, words, no: logged.append((host, words, no))
    bad = {**GOOD, "charged_total": "48,000원", "cancel": FREE}
    other = [ToolCall("c6", "browser_click", '{"selector": "text=Delete account"}'), Done("tool_calls")]
    res, asked = await drive(tools, [PAY2, result_call(bad), CANCEL_CLICK, other, END], [True, False])
    out = res[1].output
    assert "does NOT match" in out and "free" in out and "Cancel this order at once" in out
    # the window covers a few clicks the step judge says only delete something (docs/click-judge.md); closing the account is also
    # "opts out" (a membership), so it asks even inside the window, and the owner said no
    assert [a.name for a in asked] == ["confirm_purchase", "browser_click"]
    assert ("click", "text=Cancel order") in page.calls and ("click", "text=Delete account") not in page.calls
    assert len(notes) == 1 and "48,000" in notes[0][0] and "free" in notes[0][0]
    assert logged and logged[0][0] == "shop.example.com" and logged[0][2] == "A-1"


async def test_the_cancel_window_is_for_that_site_only_and_can_be_ended():
    s, page, *_ = await paid()
    s.allow_cancel("shop.example.com", "A-1")
    assert s.cancel_active()
    page.url = "https://elsewhere.example.net/"
    assert not s.cancel_active()
    page.url = "https://shop.example.com/o"
    s.end_cancel()
    assert not s.cancel_active()


async def test_no_window_without_a_wrong_charge():
    s, _, _, tools, _ = await paid()
    res, asked = await drive(tools, [PAY2, result_call(GOOD), CANCEL_CLICK, END], [True, False])
    assert [a.name for a in asked] == ["confirm_purchase", "browser_click"]   # the cancel click asks as usual


async def test_a_fee_asks_the_owner_first_with_the_facts_and_no_window():
    s, _, ledger, tools, notes = await paid()
    s_tools = by_name(tools)
    await drive(tools, [PAY2, END], [True])
    p = only(ledger)
    t = s_tools["purchase_result"]
    bad = {**GOOD, "charged_total": "48,000원", "cancel": {"free_until": "2000-01-01", "fee": "3,000원", "how": "Cancel order"}}
    tok = browser_owner.set(p.owner)
    try:
        assert t.auto_approve(bad) is False
        extra = t.approval_extra("a1", bad)
        assert extra["urgent"] and "48,000" in extra["ask"] and "24,000" in extra["ask"]
        vals = " ".join(x["value"] for x in extra["lines"])
        assert "3,000원" in vals and "A-1" in vals and "+24,000" in vals
        assert not s.cancel_active() and not notes
        assert "KEEP" in t.declined(bad) and p.state == "closed"      # Keep closes it
    finally:
        browser_owner.reset(tok)


async def test_fee_then_cancel_opens_the_window_and_the_cancelled_call_closes():
    _, page, ledger, tools, _ = await paid()
    bad = {**GOOD, "charged_total": "48,000원", "cancel": {"fee": "3,000원", "how": "Cancel order"}}
    done = result_call({"order_no": "A-1", "cancelled": True}, "c7")
    res, asked = await drive(tools, [PAY2, result_call(bad), CANCEL_CLICK, done, END], [True, True])
    assert [a.name for a in asked] == ["confirm_purchase", "purchase_result"]
    assert "The owner chose to cancel" in res[1].output and ("click", "text=Cancel order") in page.calls
    assert "Closed" in res[3].output and not ledger.unchecked(7)


async def test_fee_keep_closes_and_does_not_cancel():
    s, _, ledger, tools, _ = await paid()
    bad = {**GOOD, "charged_total": "48,000원", "cancel": {"fee": "3,000원"}}
    res, _ = await drive(tools, [PAY2, result_call(bad), END], [True, False])
    assert "KEEP" in res[1].output and not s.cancel_active() and not ledger.unchecked(7)


def test_quantity_item_and_total_checks():
    chk = compare(24000, "KRW", ITEMS, "24,000원", [{"name": "Blue mug", "quantity": 3}])
    assert chk.status == "mismatch" and "quantity" in chk.problems[0]
    assert compare(24000, "KRW", ITEMS, "24,000원", [{"name": "Red lamp", "quantity": 2}]).status == "mismatch"
    assert compare(24000, "KRW", ITEMS, "24,000원", [{"name": "Blue mug", "quantity": 2}]).status == "match"
    assert compare(24000, "KRW", ITEMS, "24,001원", [{"name": "Blue mug", "quantity": 2}]).status == "mismatch"


def test_money_and_terms_parsing():
    assert parse_money("$12.99")[0] == 12.99 and parse_money("1.234,56 €")[0] == 1234.56 and parse_money("24,000원")[0] == 24000
    now = time.time()
    # (a free-cancel phrase in words used to count; now only an ISO deadline ahead or a fee that is the number 0 does)
    assert cancel_terms({"free_until": "2999-01-01T00:00:00Z"}, now).free_now
    assert not cancel_terms({"free_until": "2000-01-01"}, now).free_now
    assert not cancel_terms({"free_until": "전액 환불 가능"}, now).free_now
    assert not cancel_terms({"free_until": "free cancellation"}, now).free_now
    assert cancel_terms({"fee": 0}, now).free_now
    assert cancel_terms({"fee": "0원"}, now).free_now
    assert not cancel_terms({"fee": "수수료 없음"}, now).free_now
    assert not cancel_terms({"fee": "no fee"}, now).free_now
    assert not cancel_terms({"fee": "3,000원"}, now).free_now
    assert not cancel_terms({"fee": 0, "free_until": "soon"}, now).free_now
    assert not cancel_terms(None, now).free_now


async def test_without_a_payment_it_is_an_error():
    _, _, _, tools, _ = await paid()
    assert "no approved payment" in by_name(tools)["purchase_result"].validate(GOOD)


async def test_a_payment_never_checked_is_found_at_the_end_of_its_task():
    _, _, ledger, tools, _ = await paid(task_id=9)
    await drive(tools, [PAY, END], [True])
    assert len(ledger.unchecked(9)) == 1 and not ledger.unchecked(8) and not ledger.unchecked(None)


async def test_a_payment_records_its_numbers_for_the_check_whoever_paid():
    _, _, ledger, tools, _ = await paid(task_id=11)       # a grant (unattended) payment records the same ApprovedPayment
    await drive(tools, [PAY2, END], [True])
    p = ledger.unchecked(11)[0]
    assert p.amount == 24000 and p.currency == "KRW" and p.state == "open"


def test_confirm_purchase_loads_purchase_result_into_the_running_box():
    from assistant.agent import ToolBox, current_box
    from assistant.agent.loop import Tool

    async def noop(_a):
        return ""
    tools = [Tool(n, n, {"type": "object"}, noop) for n in ("confirm_purchase", "purchase_result")]
    box = ToolBox(["confirm_purchase"])
    token = current_box.set((box, tools))
    try:
        assert not box.is_shown("purchase_result")
        box.activate(["purchase_result"], [t.name for t in tools])   # what confirm() does after confirm_inner
        assert box.is_shown("purchase_result")
    finally:
        current_box.reset(token)
