"""Auto-pay that is easy to read (each card says whether it pays on its own and why not, and the model is told the exact reason a
payment asked), and the permissions a repeating job gets ONCE at setup: a saved login and a purchase its runs may do without a card,
scoped to that schedule, that site and (for a purchase) that cap. Sites here are neutral fakes."""

import inspect
import json
import uuid

import httpx
import pytest

from assistant import server as server_mod
from assistant.agent import ToolResult, current_approval, run_agent, ui_sink
from assistant.providers import Done, TextDelta, ToolCall
from assistant.schedule_grants import ScheduleGrants, task_allow_tools
from assistant.schedules import Schedules
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools.browser import browser_tools
from assistant.tools.purchase import purchase_tools
from assistant.tools.vault_tools import vault_tools
from assistant.wallet import CardDeliveries, Wallet
from assistant.workers import current_turn

from test_server import AUTH, TOKEN, Running, scripted
from test_vault import KEY, PASSWORD, USER, Page, session_for
from test_wallet import auto_card, checkout, e2e_card, no_secrets

ORDER = {"summary": "Weekly coffee beans 1kg", "amount": 5000, "currency": "KRW", "site": "shop.example.com"}


def world(tmp_path):
    store = Store(tmp_path / "a.db", KEY.decode())
    wallet = Wallet(store)
    schedules = Schedules(store)
    grants = ScheduleGrants(store, schedules, wallet)
    schedules.on_deleted = grants.drop_schedule
    used = []
    grants.on_use = lambda g, tool, host, detail: used.append((g["id"], tool, host, detail))
    weekly = schedules.create("Weekly beans", "buy the usual beans", cron="0 9 * * 1", created_by="agent")
    daily = schedules.create("Daily check", "check the account page", cron="0 13 * * *", created_by="agent")
    store.save_credential("shop-login", "Shop", ["www.shop.example.com"], {}, kind="login")
    store.set_login("shop-login", USER, PASSWORD)
    return store, wallet, schedules, grants, weekly, daily, used


class in_run:
    """The code below runs as part of a turn: a scheduled run of `sid`, or a chat turn (sid None)."""

    def __init__(self, sid=None):
        self.src = f"schedule:{sid}" if sid else "chat"

    def __enter__(self):
        self.tok = current_turn.set({"task_id": 3, "turn_id": "t1", "source": self.src, "unattended": self.src != "chat"})

    def __exit__(self, *a):
        current_turn.reset(self.tok)


async def decided(value):
    return await value if inspect.isawaitable(value) else value


# ---------------------------------------------------------------- A: auto-pay is said plainly
def test_each_card_says_whether_it_pays_automatically_and_why_not(tmp_path):
    store, wallet, *_ = world(tmp_path)
    auto, e2e = auto_card(wallet), e2e_card(wallet)
    by = {c["id"]: c for c in wallet.cards()}
    assert by[auto["id"]]["pays_automatically"] is True and by[auto["id"]]["ask_reason"] == ""
    assert by[e2e["id"]]["pays_automatically"] is False and by[e2e["id"]]["ask_reason"] == "not_auto"
    assert by[e2e["id"]]["ask_reason_text"] == "Auto-pay is not allowed for this card"
    wallet.set_paused(True)
    assert wallet.card(auto["id"])["ask_reason"] == "paused"
    wallet.set_paused(False)
    store.set_vault_locked(True)
    assert wallet.card(auto["id"])["ask_reason"] == "vault_locked"


def purchase_agent(session, wallet, script, taps, grants=None):
    tools = [*browser_tools(session), *purchase_tools(session, wallet, CardDeliveries(), card_wait=0.2, grants=grants)]
    by = {t.name: t for t in tools}
    asked, shown = [], []
    taps = iter(taps)

    async def approve(req):
        aid = uuid.uuid4().hex
        current_approval.set((req.id, aid))
        extra = by[req.name].approval_extra(aid, req.arguments) if by[req.name].approval_extra else {}
        asked.append((req, extra))
        return next(taps, False)

    async def run():
        async def sink(item):
            shown.append(item)
        tok = ui_sink.set(sink)
        try:
            return [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "buy"}], tools, approve=approve,
                                                 stream_fn=scripted(*script)) if isinstance(ev, ToolResult)]
        finally:
            ui_sink.reset(tok)
    return run, asked, shown


def call(i, name, args):
    return [ToolCall(f"c{i}", name, json.dumps(args, ensure_ascii=False)), Done("tool_calls")]


END = [TextDelta("done"), Done("stop")]


async def test_the_model_is_told_the_exact_reason_a_card_payment_asked(tmp_path):
    _, wallet, *_ = world(tmp_path)
    card = e2e_card(wallet)
    session, _ = checkout()
    await session.page()
    run, asked, _ = purchase_agent(session, wallet, [call(1, "confirm_purchase", {**ORDER, "card": card["label"]}), END], [False])
    (res,) = await run()
    assert asked[0][1]["card"]["reason"] == "not_auto" and asked[0][1]["reason"] == "not_auto"
    assert "[not_auto]" in res.output and "saved to ask each time" in res.output and "Wallet" in res.output
    assert "nothing else about the user's settings" in res.output


async def test_a_first_payment_on_a_new_site_says_that_rule(tmp_path):
    _, wallet, *_ = world(tmp_path)
    card = auto_card(wallet)
    session, _ = checkout()
    await session.page()
    run, asked, _ = purchase_agent(session, wallet, [call(1, "confirm_purchase", {**ORDER, "card": card["label"]}), END], [True])
    (res,) = await run()
    assert asked[0][1]["card"]["reason"] == "first_site" and res.approved is True
    assert "[first_site]" in res.output and "first payment on a new site always asks once" in res.output


async def test_wallet_cards_repeats_only_what_the_settings_say(tmp_path):
    _, wallet, *_ = world(tmp_path)
    auto_card(wallet)
    e2e_card(wallet)
    tool = {t.name: t for t in purchase_tools(None, wallet, CardDeliveries())}["wallet_cards"]
    out = await tool.handler({})
    assert "pays automatically" in out and "first payment on a new site" in out
    assert "ASKS EACH TIME [not_auto]" in out and "turn on auto-pay for this card" in out and "never claim anything else" in out
    no_secrets(out)


# ---------------------------------------------------------------- B: asked once at setup
async def test_task_allow_is_one_card_at_setup_and_never_in_a_run(tmp_path):
    store, wallet, schedules, grants, weekly, daily, _ = world(tmp_path)
    tool = {t.name: t for t in task_allow_tools(grants, wallet)}["task_allow"]
    assert tool.requires_approval and tool.auto_approve is None
    args = {"schedule_id": weekly["id"], "login": "shop-login", "trigger": "every Monday at 9", "action": "buy the usual beans",
            "purchase": {"site": "https://shop.example.com/", "what": "coffee beans", "max_amount": 5000, "currency": "KRW"}}
    assert tool.validate(args) is None
    with in_run(weekly["id"]):
        assert "never inside a run" in tool.validate(args)
    assert "no schedule" in tool.validate({**args, "schedule_id": 999})
    assert "trigger is required" in tool.validate({**args, "trigger": ""})
    assert "no website login" in tool.validate({**args, "login": "nope"})
    assert "account" in tool.validate({**args, "purchase": {**args["purchase"], "what": "change the account password"}})
    assert "max_amount" in tool.validate({**args, "purchase": {**args["purchase"], "max_amount": 0}})
    card = (await decided(tool.describe(args)))
    assert "every Monday at 9" in card and "Shop" in card and "buy coffee beans on example.com" in card and "5,000 KRW" in card
    lines = tool.approval_extra("a1", args)["lines"]
    assert [x["label"] for x in lines] == ["Task", "When", "Will do", "Login", "Purchase", "Up to"]
    out = await tool.handler(args)
    assert "ALLOWED" in out and "on its own" in out
    items = grants.list(weekly["id"])
    login = next(g for g in items if g["kind"] == "login")
    buy = next(g for g in items if g["kind"] == "purchase")
    assert login["login"] == "shop-login" and login["site"] == "example.com"
    runs = schedules.runs_per_month(weekly["id"])
    assert 4 <= runs <= 5 and buy["monthly_cap"] == 5000 * runs and buy["what"] == "coffee beans"
    assert grants.list(daily["id"]) == []
    # the setup card itself is never one that could be "remembered" for later runs
    from assistant.activity import rule_kind, schedule_rule_ok
    assert rule_kind("task_allow") == "" and not schedule_rule_ok("task_allow")


async def test_a_login_grant_signs_in_only_in_that_schedules_runs_on_that_site(tmp_path):
    store, wallet, schedules, grants, weekly, daily, used = world(tmp_path)
    g = grants.add_login(weekly["id"], "shop-login", "www.shop.example.com")
    tools = {t.name: t for t in vault_tools(store, session_for(Page(url="https://www.shop.example.com/login")), grants=grants)}
    tool = tools["browser_login"]
    assert await decided(tool.auto_approve({"name": "shop-login"})) is False           # a chat follows the login's own setting
    with in_run(daily["id"]):
        assert await decided(tool.auto_approve({"name": "shop-login"})) is False       # another schedule
    with in_run(weekly["id"]):
        assert await decided(tool.auto_approve({"name": "shop-login"})) is True        # this schedule's run: no card
    assert used == [(g["id"], "browser_login", "www.shop.example.com", used[0][3])] and "Shop" in used[0][3]
    assert grants.get(g["id"])["uses"] == 1
    other = {t.name: t for t in vault_tools(store, session_for(Page(url="https://evil.example.net/login")), grants=grants)}["browser_login"]
    with in_run(weekly["id"]):
        assert await decided(other.auto_approve({"name": "shop-login"})) is False      # another site
    store.set_vault_locked(True)
    with in_run(weekly["id"]):
        assert await decided(tool.auto_approve({"name": "shop-login"})) is False       # the lock still wins
    store.set_vault_locked(False)
    status = await tools["vault_status"].handler({})
    assert f"schedule #{weekly['id']}" in status and "asks the user each time" in status
    assert store.credential("shop-login")["auto_allow"] is False                       # never a global flip


def test_grants_are_revoked_and_removed_with_the_schedule_or_the_login(tmp_path):
    store, wallet, schedules, grants, weekly, daily, _ = world(tmp_path)
    a = grants.add_login(weekly["id"], "shop-login", "shop.example.com")
    b = grants.add_purchase(weekly["id"], "shop.example.com", "coffee beans", 5000, "KRW")
    c = grants.add_login(daily["id"], "shop-login", "shop.example.com")
    assert grants.revoke(a["id"], daily["id"]) is False                                 # not that schedule's
    assert grants.revoke(a["id"], weekly["id"]) is True and grants.get(a["id"]) is None
    assert schedules.delete(weekly["id"]) and grants.get(b["id"]) is None              # deleted with the schedule
    assert grants.get(c["id"]) is not None
    assert grants.drop_login("shop-login") == 1 and grants.list() == []
    actions = [e["action"] for e in store.vault_log(20)]
    assert "schedule-allowed" in actions and "schedule-revoked" in actions


async def test_a_purchase_grant_buys_within_its_cap_in_that_schedules_runs_and_notifies(tmp_path):
    store, wallet, schedules, grants, weekly, daily, used = world(tmp_path)
    g = grants.add_purchase(weekly["id"], "shop.example.com", "coffee beans", 5000, "KRW")
    session, page = checkout()
    await session.page()
    run, asked, shown = purchase_agent(session, wallet, [call(1, "confirm_purchase", {**ORDER, "pay_with": "site balance"}), END], [],
                                       grants=grants)
    with in_run(weekly["id"]):
        (res,) = await run()
    assert asked == [] and res.approved is None and "standing permission" in res.output
    pay = wallet.payments()[0]
    assert pay["kind"] == "auto" and pay["grant_id"] == g["id"] and pay["amount"] == 5000
    ev = next(e for e in shown if e.get("type") == "payment_auto")
    assert ev["schedule_grant"]["id"] == g["id"] and ev["schedule_grant"]["via"] == "site balance"
    assert used and used[0][1] == "confirm_purchase" and "coffee beans" in used[0][3]
    assert wallet.spent("KRW") == 0 and grants.spent(g["id"]) == 5000                    # its own cap, not the wallet's limits
    assert session.purchase_active("shop.example.com")


@pytest.mark.parametrize("change, reason", [
    ({"amount": 5001}, "task_over_cap"),
    ({"currency": "USD", "amount": 3}, "task_currency"),
    ({"summary": "Gift card 50,000"}, "task_other_item"),
    ({"summary": "coffee beans and change the account password"}, "task_other_item"),
])
async def test_outside_the_cap_or_the_item_it_asks_with_the_reason(tmp_path, change, reason):
    store, wallet, schedules, grants, weekly, *_ = world(tmp_path)
    grants.add_purchase(weekly["id"], "shop.example.com", "coffee beans", 5000, "KRW")
    session, _ = checkout()
    await session.page()
    run, asked, shown = purchase_agent(session, wallet, [call(1, "confirm_purchase", {**ORDER, **change}), END], [False], grants=grants)
    with in_run(weekly["id"]):
        (res,) = await run()
    assert asked and asked[0][1]["reason"] == reason and f"[{reason}]" in res.output
    assert not [e for e in shown if e.get("type") == "payment_auto"] and wallet.payments() == []


def test_the_monthly_cap_and_other_sites_are_checked(tmp_path):
    store, wallet, schedules, grants, weekly, *_ = world(tmp_path)
    g = grants.add_purchase(weekly["id"], "shop.example.com", "coffee beans", 5000, "KRW")
    for _ in range(g["runs_per_month"]):
        wallet.record("auto", None, "shop.example.com", "coffee beans", 5000, "KRW", grant_id=g["id"])
    check = grants.purchase_check("shop.example.com", 5000, "KRW", "coffee beans", schedule_id=weekly["id"])
    assert check.grant is None and check.reason == "task_over_monthly"
    check = grants.purchase_check("other.example.org", 10, "KRW", "coffee beans", schedule_id=weekly["id"])
    assert check.grant is None and check.reason == "task_other_site"


async def test_one_covered_purchase_per_run_a_second_one_or_a_changed_total_asks(tmp_path):
    store, wallet, schedules, grants, weekly, *_ = world(tmp_path)
    grants.add_purchase(weekly["id"], "shop.example.com", "coffee beans", 5000, "KRW")
    session, _ = checkout()
    await session.page()
    run, asked, _ = purchase_agent(session, wallet, [call(1, "confirm_purchase", {**ORDER, "amount": 3000}),
                                                     call(2, "confirm_purchase", {**ORDER, "amount": 1500}), END], [False],
                                   grants=grants)
    with in_run(weekly["id"]):
        first, second = await run()
    assert "standing permission" in first.output
    assert len(asked) == 1 and asked[0][1]["reason"] == "task_again" and "[task_again]" in second.output
    assert [p["amount"] for p in wallet.payments()] == [3000]
    tok = current_turn.set({"task_id": 4, "turn_id": "t2", "source": f"schedule:{weekly['id']}", "unattended": True})
    try:                                                                                  # the next run is covered again
        assert grants.purchase_check("shop.example.com", 3000, "KRW", "coffee beans").grant is not None
    finally:
        current_turn.reset(tok)


async def test_outside_the_schedule_or_with_a_phone_only_card_it_asks(tmp_path):
    store, wallet, schedules, grants, weekly, daily, _ = world(tmp_path)
    grants.add_purchase(weekly["id"], "shop.example.com", "coffee beans", 5000, "KRW")
    session, _ = checkout()
    await session.page()
    for ctx in (in_run(None), in_run(daily["id"])):                                     # a chat, another schedule
        run, asked, _ = purchase_agent(session, wallet, [call(1, "confirm_purchase", ORDER), END], [False], grants=grants)
        with ctx:
            await run()
        assert len(asked) == 1
    card = e2e_card(wallet)
    run, asked, _ = purchase_agent(session, wallet, [call(1, "confirm_purchase", {**ORDER, "card": card["label"]}), END], [False],
                                   grants=grants)
    with in_run(weekly["id"]):
        (res,) = await run()
    assert asked[0][1]["reason"] == "phone_needed" and "[phone_needed]" in res.output and "kept on the phone only" in res.output


async def test_a_login_grant_never_covers_a_payment(tmp_path):
    store, wallet, schedules, grants, weekly, *_ = world(tmp_path)
    grants.add_login(weekly["id"], "shop-login", "shop.example.com")
    session, _ = checkout()
    await session.page()
    run, asked, _ = purchase_agent(session, wallet, [call(1, "confirm_purchase", ORDER), END], [False], grants=grants)
    with in_run(weekly["id"]):
        await run()
    assert len(asked) == 1 and wallet.payments() == []


# ---------------------------------------------------------------- over HTTP
async def test_grants_over_http_schedule_vault_wallet_and_the_approval_log(tmp_path):
    app = create_app(Settings(token=TOKEN, provider="p", model="m", db_path=str(tmp_path / "a.db")), stream_fn=scripted())
    grants: ScheduleGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        s = (await c.post("/api/schedules", json={"name": "Weekly beans", "prompt": "buy beans", "cron": "0 9 * * 1"})).json()
        r = await c.post("/api/vault/logins", json={"site": "https://shop.example.com", "name": "shop-login", "label": "Shop"})
        assert r.status_code == 200
        lg = grants.add_login(s["id"], "shop-login", "shop.example.com")
        buy = grants.add_purchase(s["id"], "shop.example.com", "coffee beans", 5000, "KRW")
        items = (await c.get(f"/api/schedules/{s['id']}/grants")).json()["items"]
        assert {g["kind"] for g in items} == {"login", "purchase"}
        vault = (await c.get("/api/vault")).json()
        entry = next(x for x in vault["pending"] + vault["items"] if x["name"] == "shop-login")
        assert [g["id"] for g in entry["schedule_grants"]] == [lg["id"]] and entry["schedule_grants"][0]["schedule_name"] == "Weekly beans"
        w = (await c.get("/api/wallet")).json()
        assert w["auto_cards"] == 0 and [g["id"] for g in w["schedule_grants"]] == [buy["id"]]
        # a covered sign-in is logged as allowed, inside the schedule's run
        tok = current_turn.set({"task_id": None, "turn_id": "t", "source": f"schedule:{s['id']}", "unattended": True})
        try:
            grants.use_login(grants.login_cover("shop-login", "shop.example.com"), "shop.example.com")
        finally:
            current_turn.reset(tok)
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["decision"] == "allowed_by_rule" and hist[0]["tool"] == "browser_login" and "Allowed at setup" in hist[0]["detail"]
        assert (await c.delete(f"/api/schedules/{s['id']}/grants/{lg['id']}")).json() == {"ok": True}
        assert (await c.delete(f"/api/schedules/{s['id']}/grants/{lg['id']}")).status_code == 404
        assert (await c.delete(f"/api/schedules/{s['id']}")).status_code == 200
        assert grants.list() == []
