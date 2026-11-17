"""A reply that ends with a payment whose receipt no purchase_result closed gets one more round first (turn_engine.receipt_rounds):
the model is told, once, to open the order page and check it. Only when the payment is still unchecked after that round does the
reply end with the line that says so. Scripted model and fake payment tools; the ledger is the app's own (appctx.payment_ledger)."""

import asyncio
import time

import httpx

from assistant import i18n
from assistant.agent import Tool
from assistant.payment_pins import ApprovedPayment
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import create_app
from assistant.tools.browser import browser_owner
from assistant.workers import current_turn

from test_server import AUTH, BODY, Running, follow, read_events, settings

HOST = "ol.example.test"
WARNING = i18n.t("Kelo did not check the order after paying at {site}; please look at the order yourself.", site=HOST)
NOTE_START = "[Not from the user: you paid on ol.example.test"


def payment_tools(ledger_of):
    """`confirm_purchase` records an approved payment of this task (as the real tool does); `purchase_result` closes its check."""
    async def pay(args):
        ledger_of().record(ApprovedPayment(owner=browser_owner.get(), host=HOST, summary="weekly lotto", amount_text="5,000원",
                                           items=[], pay_with="card", at=time.time(), amount=5000, currency="KRW",
                                           task_id=(current_turn.get() or {}).get("task_id")))
        return "approved"

    async def check(args):
        for p in ledger_of()._last.values():
            p.state = "closed"
        return "Checked: matches what was approved."
    return [Tool("confirm_purchase", "pay", {"type": "object"}, pay),
            Tool("purchase_result", "check the receipt", {"type": "object"}, check)]


def scripted(*steps):
    """A fake model: each request (one step of the run) gets the next list of events; records the requests it was sent."""
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request)
        for ev in steps[min(len(seen) - 1, len(steps) - 1)]:
            yield ev
    fn.seen = seen
    return fn


PAY_AND_SAY_IT_IS_DONE = [ToolCall("c1", "confirm_purchase", "{}"), Done("tool_calls")], [TextDelta("정상 결제되었어요."), Done("stop")]


def note_seen(request):
    return any(m.get("role") == "system" and str(m.get("content", "")).startswith(NOTE_START) for m in request.messages)


async def run(steps):
    """One chat reply with the scripted model; returns (its events, the requests sent, the app's payment ledger)."""
    box = {}
    fn = scripted(*steps)
    app = create_app(settings(payment_specialist=False), tools=payment_tools(lambda: box["ledger"]), stream_fn=fn)   # the chat pays (switch off)
    box["ledger"] = app.state.appctx.payment_ledger
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        async with c.stream("POST", "/api/chat", json=BODY) as r:
            ev = await read_events(r)
    return ev, fn.seen, box["ledger"]


def said(ev):
    return "".join(e["text"] for e in ev if e["type"] == "text")


async def run_in_job(worker_steps):
    """The payment split (the default): the chat hands the payment to a checkout job, and the job's own reply gets the receipt round.
    `worker_steps`: the job's events per request (a list, the last one again when it runs out). Returns (the job's requests, the
    app's payment ledger, the job's worker row status)."""
    from split_pay import SHOP
    from test_workers import Brain, delegating, role_of   # noqa: F401 - role_of: the job's requests are told apart by its prompt
    from test_workers import one_worker
    from assistant.tools.browser import BrowserSession
    from test_agent_profile import ShotPage
    box = {}

    async def connect():
        return ShotPage(SHOP)
    seen_worker = []

    def worker(req, n):
        seen_worker.append(req)
        return worker_steps[min(len(seen_worker) - 1, len(worker_steps) - 1)]
    brain = Brain(chat=delegating({"task": "weekly lotto", "kind": "checkout", "url": SHOP}, then="결제 끝났어요."), worker=worker)
    app = create_app(settings(payment_specialist=True), tools=payment_tools(lambda: box["ledger"]), stream_fn=brain, with_worker_tools=True,
                     browser=BrowserSession(connect=connect))
    box["ledger"] = app.state.appctx.payment_ledger
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "복권 사줘"}, headers=AUTH)).json()["id"])
        w = await one_worker(c)
        for _ in range(400):
            cur = (await c.get("/api/workers", headers=AUTH)).json()["items"]
            if cur and cur[0]["status"] not in ("running",):
                break
            await asyncio.sleep(0.02)
    return seen_worker, box["ledger"], w


async def test_the_model_checks_the_receipt_in_the_extra_round_and_no_warning_is_said():
    ev, seen, ledger = await run([
        *PAY_AND_SAY_IT_IS_DONE,
        [ToolCall("r1", "purchase_result", '{"order_no": "A-1"}'), Done("tool_calls")],
        [TextDelta("주문 번호 A-1을 확인했어요."), Done("stop")],
    ])
    assert len(seen) == 4 and not note_seen(seen[0]) and not note_seen(seen[1])
    assert note_seen(seen[2]) and note_seen(seen[3])                       # the extra round, and its own second step
    assert any(t["function"]["name"] == "purchase_result" for t in seen[2].tools)     # loaded for that round
    assert "확인했어요" in said(ev) and WARNING not in said(ev)
    assert ev[-1]["type"] == "done" and "error" not in [e["type"] for e in ev]
    assert ledger._last and all(p.state == "closed" for p in ledger._last.values())


async def test_when_the_receipt_is_still_unchecked_after_the_extra_round_the_reply_says_so_once():
    ev, seen, _ = await run([
        *PAY_AND_SAY_IT_IS_DONE,
        [TextDelta("끝났어요."), Done("stop")],
    ])
    assert len(seen) == 3 and sum(note_seen(r) for r in seen) == 1          # one extra round, never a second one
    assert said(ev).endswith(WARNING) and said(ev).count(WARNING) == 1
    assert "done" in [e["type"] for e in ev]     # the line is said at the end of the reply, as before (finish)


async def test_without_a_payment_there_is_no_extra_round():
    ev, seen, _ = await run([[TextDelta("안녕하세요."), Done("stop")]])
    assert len(seen) == 1 and not note_seen(seen[0])
    assert WARNING not in said(ev)


async def test_a_checkout_job_that_pays_gets_the_receipt_round_inside_its_own_reply():
    """The same receipt rule for a payment made by a checkout job (the split, the default): the job's reply that ends with the payment
    gets one more round with the note, loads purchase_result, and the receipt is closed."""
    from assistant.providers import Done as _D   # noqa: F401
    from test_workers import call, say
    steps = [call("confirm_purchase"), say("정상 결제되었어요."),
             call("purchase_result", {"order_no": "A-1"}), say("주문 번호 A-1을 확인했어요."),
             call("report", {"status": "done", "summary": "주문 번호 A-1을 확인했어요."})]
    seen, ledger, _ = await run_in_job(steps)
    assert len(seen) >= 3 and not note_seen(seen[0]) and not note_seen(seen[1]) and note_seen(seen[2])   # the round after the payment
    assert sum(note_seen(r) for r in seen) <= 2                       # that one extra round (its own second step), never a second round
    assert any(t["function"]["name"] == "purchase_result" for t in seen[2].tools)                  # loaded for that round
    assert ledger._last and all(p.state == "closed" for p in ledger._last.values())
