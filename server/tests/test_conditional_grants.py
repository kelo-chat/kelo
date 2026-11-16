"""One approval process for every "when X happens, do Y" task, whatever runs it (a schedule that polls, a watcher's hit, a job):
ONE card at setup (task_allow) names the trigger, the action and the limits; when it fires, what is inside runs without a card and the
user is told right after; anything outside stops and asks (urgent when time matters); a one-shot task ends after its count, so nothing
happens twice; grants are listed, revocable and removed with the task. Models are scripted; sites, people and services are fakes."""

import asyncio
import json

import httpx
import pytest

from assistant.agent import ToolResult, current_approval, run_agent, ui_sink
from assistant.providers import Done, TextDelta, ToolCall
from assistant.schedule_grants import ConditionalGrants, action_of, norm_target, task_allow_tools, template_ok
from assistant.schedules import Schedules
from assistant.server import create_app
from assistant.store import Store
from assistant.tools.purchase import purchase_tools
from assistant.wallet import CardDeliveries, Wallet
from assistant.workers import current_turn, current_worker

from test_agent_profile import app_settings, approval_tool
from test_server import AUTH, Running, follow
from test_vault import KEY
from test_wallet import auto_card, e2e_card

TICKET = {"summary": "Concert ticket, cancelled seat R12", "amount": 50000, "currency": "KRW", "site": "tickets.example.com"}


# ---------------------------------------------------------------------------------------------------------------- helpers
class in_scope:
    """The code below runs as part of a turn of a conditional task: a schedule's run, a watcher's hit, a job; or a plain chat."""

    def __init__(self, scope=""):
        self.scope = scope

    def __enter__(self):
        kind, _, ref = self.scope.partition(":")
        turn = {"task_id": 7, "turn_id": "t-" + self.scope, "source": "chat", "unattended": bool(self.scope)}
        if kind == "schedule":
            turn["source"] = self.scope
        elif kind == "watcher":
            turn.update(source="handoff", cond=self.scope)
        self.tok = current_turn.set(turn)
        self.wtok = current_worker.set(ref if kind == "job" else "")

    def __exit__(self, *a):
        current_turn.reset(self.tok)
        current_worker.reset(self.wtok)


def world(tmp_path):
    store = Store(tmp_path / "a.db", KEY.decode())
    wallet = Wallet(store)
    schedules = Schedules(store)
    grants = ConditionalGrants(store, schedules, wallet)
    poll = schedules.create("Seat poll", "look for a cancelled seat and buy it", every_minutes=5, created_by="agent")
    return store, wallet, schedules, grants, poll


def call(i, name, args):
    return [ToolCall(f"c{i}", name, json.dumps(args, ensure_ascii=False)), Done("tool_calls")]


END = [TextDelta("done"), Done("stop")]


def scripted(*turns):
    it = iter(turns)

    async def fn(provider, request, **kw):
        for ev in next(it, END):
            yield ev
    return fn


def buyer(wallet, script, taps, grants):
    """confirm_purchase with a scripted model; approvals answered from `taps` (False: no answer / deny)."""
    tools = purchase_tools(None, wallet, CardDeliveries(), card_wait=0.2, grants=grants)
    by = {t.name: t for t in tools}
    asked, shown = [], []
    taps = iter(taps)

    async def approve(req):
        current_approval.set((req.id, "a" + req.id))
        extra = by[req.name].approval_extra("a" + req.id, req.arguments) if by[req.name].approval_extra else {}
        asked.append((req, extra))
        return next(taps, False)

    async def run():
        async def sink(item):
            shown.append(item)
        tok = ui_sink.set(sink)
        try:
            return [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "go"}], tools, approve=approve,
                                                 stream_fn=scripted(*script)) if isinstance(ev, ToolResult)]
        finally:
            ui_sink.reset(tok)
    return run, asked, shown


async def until(cond, tries=400, pause=0.02):
    for _ in range(tries):
        got = cond()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        await asyncio.sleep(pause)
    raise AssertionError("never happened")


# ---------------------------------------------------------------------------------------------------------------- the scenario
@pytest.mark.parametrize("scope_kind", ["schedule", "watcher"])
async def test_a_cancelled_ticket_over_the_cards_auto_pay_limit_is_bought_once_without_a_card(tmp_path, scope_kind):
    """The card pays on its own up to 30,000 KRW; the ticket is 50,000. Allowed 50,000 at setup: bought at once, no card, once."""
    store, wallet, schedules, grants, poll = world(tmp_path)
    card = auto_card(wallet)
    assert wallet.limit_for("KRW")[0] == 30000 and wallet.decide(card, "tickets.example.com", 50000, "KRW").reason == "over_per_payment"
    if scope_kind == "watcher":
        from assistant.watchers import Watchers
        watchers = Watchers(store)
        grants.watchers = watchers
        w = watchers.create("agent", name="Seat watch", url="https://tickets.example.com/show", mode="text_appears", match="available")
        scope = f"watcher:{w['id']}"
    else:
        scope = f"schedule:{poll['id']}"
    finished = []
    grants.on_finished = lambda s, g: finished.append(s)
    g = grants.add_purchase(None, "tickets.example.com", "concert ticket", 50000, "KRW", scope=scope, max_count=1, card_id=card["id"],
                            once=True, trigger="a cancelled seat shows up", action="buy it with the usual card")
    run, asked, shown = buyer(wallet, [call(1, "confirm_purchase", TICKET), call(2, "confirm_purchase", TICKET), END], [False], grants)
    with in_scope(scope):
        first, second = await run()
    assert "standing permission" in first.output and first.approved is None
    pay = wallet.payments()[-1]
    assert pay["kind"] == "auto" and pay["grant_id"] == g["id"] and pay["amount"] == 50000 and pay["card_id"] == card["id"]
    ev = next(e for e in shown if e.get("type") == "payment_auto")
    assert ev["grant"]["scope"] == scope_kind and ev["grant"]["id"] == g["id"]
    assert finished == [scope]                                                    # one-shot: the task ends after its one purchase
    assert len(asked) == 1 and asked[0][1]["reason"] in ("task_again", "task_used_up")   # never bought twice
    assert len(wallet.payments()) == 1
    assert grants.settle([scope]) == 1 and grants.list(scope=scope) == []         # its grant goes when the run ends


async def test_allowed_only_up_to_the_cards_own_limit_the_ticket_asks(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    card = auto_card(wallet)
    grants.add_purchase(poll["id"], "tickets.example.com", "concert ticket", 30000, "KRW", max_count=1, card_id=card["id"], once=True)
    run, asked, shown = buyer(wallet, [call(1, "confirm_purchase", TICKET), END], [False], grants)
    with in_scope(f"schedule:{poll['id']}"):
        (res,) = await run()
    assert len(asked) == 1 and asked[0][1]["reason"] == "task_over_cap" and "[task_over_cap]" in res.output
    assert wallet.payments() == [] and not [e for e in shown if e.get("type") == "payment_auto"]


async def test_the_setup_card_shows_the_cards_auto_pay_limit_next_to_the_amount_asked_for(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    card = auto_card(wallet)
    tool = {t.name: t for t in task_allow_tools(grants, wallet)}["task_allow"]
    args = {"schedule_id": poll["id"], "trigger": "a cancelled seat shows up", "action": "buy one ticket", "urgent": True,
            "missed": "the seat goes to someone else",
            "purchase": {"site": "tickets.example.com", "what": "concert ticket", "max_amount": 50000, "card": card["label"]}}
    assert tool.validate(args) is None
    extra = tool.approval_extra("a1", args)
    v = extra["grant"]
    assert v["scope"] == "schedule" and v["trigger"] == "a cancelled seat shows up" and v["once"] is True and v["urgent"] is True
    p = v["purchase"]
    assert p["max_count"] == 1 and p["max_amount_text"] == "50,000 KRW" and p["monthly_cap"] == 50000
    assert p["card"]["auto_limit_text"] == "30,000 KRW" and p["card"]["auto_pay"] is True
    assert p["card_note"] == "This card's auto-pay limit is 30,000 KRW. Allow up to 50,000 KRW for this task?"
    labels = [x["label"] for x in extra["lines"]]
    assert labels[:3] == ["Task", "When", "Will do"] and "Card" in labels
    out = await tool.handler(args)
    assert "ALLOWED" in out and "one-shot" in out and "auto-pay limit" in out
    (g,) = grants.list(poll["id"])
    assert g["max_count"] == 1 and g["once"] and g["urgent"] and g["card_id"] == card["id"] and g["missed"] == "the seat goes to someone else"
    # a phone-only card can never pay on its own: the card says so
    e2e = e2e_card(wallet)
    v = tool.approval_extra("a2", {**args, "purchase": {**args["purchase"], "card": e2e["label"]}})["grant"]
    assert v["purchase"]["card"]["phone_only"] and "each payment will still ask" in v["purchase"]["card_note"]


def test_the_setup_question_is_never_asked_inside_a_run_and_needs_a_real_task(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    tool = {t.name: t for t in task_allow_tools(grants, wallet)}["task_allow"]
    args = {"watcher_id": 99, "trigger": "x", "action": "y", "send": {"to": ["a@example.com"]}}
    assert "no watcher #99" in tool.validate(args)
    assert "exactly one" in tool.validate({**args, "schedule_id": poll["id"]})
    with in_scope(f"schedule:{poll['id']}"):
        assert "never inside a run" in tool.validate({**args, "watcher_id": None, "schedule_id": poll["id"]})
    assert "action is required" in tool.validate({"schedule_id": poll["id"], "trigger": "x", "send": {"to": ["a@example.com"]}})
    assert "change.where" in tool.validate({"schedule_id": poll["id"], "trigger": "x", "action": "y", "change": {"what": "z"}})
    assert "account" in tool.validate({"schedule_id": poll["id"], "trigger": "x", "action": "y",
                                       "change": {"what": "change the account password", "where": ["calendar"]}})


# ---------------------------------------------------------------------------------------------------------------- what a grant covers
def test_steps_are_matched_by_kind_target_count_wording_and_deletion(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    scope = f"schedule:{poll['id']}"
    grants.add_action(scope, "send", "tell my wife", ["wife@example.com", "+82 10-1234-5678"], max_count=2,
                      template="The package arrived: {item}", once=False)
    grants.add_action(scope, "change", "move my run", ["calendar", "notion"], max_count=1)
    with in_scope(scope):
        ok = grants.action_check("email_send", {"to": ["Wife@Example.com"], "subject": "s", "body": "Hi! The package arrived: shoes"})
        assert ok.grant is not None and ok.kind == "send"
        assert grants.action_check("phone_send_text", {"to": "010-1234-5678", "text": "The package arrived: a lamp"}).grant is not None
        other = grants.action_check("email_send", {"to": ["boss@example.com"], "body": "The package arrived: x"})
        assert other.grant is None and other.reason == "task_other_target"
        assert grants.action_check("email_send", {"to": ["wife@example.com"], "body": "Send me money"}).reason == "task_other_text"
        assert grants.action_check("gcal_update_event", {"event_id": "e1"}).grant is not None
        assert grants.action_check("mcp_call_tool", {"name": "Notion", "tool": "update_page"}).grant is not None   # a connector named whole
        assert grants.action_check("mcp_call_tool", {"name": "slack", "tool": "post"}).reason == "task_other_target"
        assert grants.action_check("gcal_delete_event", {"event_id": "e1"}).reason == "task_no_delete"
        assert grants.action_check("browser_click", {"selector": "#signup"}, "shop.example.com", "account").reason == "task_account"
        assert grants.action_check("browser_click", {"selector": "#pay"}, "shop.example.com", "pay").grant is None   # payments: never here
        g = grants.action_check("gcal_update_event", {"event_id": "e1"}).grant
        grants.use_action(g, "gcal_update_event")
        assert grants.action_check("gcal_update_event", {"event_id": "e1"}).reason == "task_used_up"
    with in_scope(""):                                                                # a chat: nothing is covered
        assert grants.action_check("gcal_update_event", {"event_id": "e1"}).grant is None
    assert template_ok("Arrived: {x}", "arrived: socks") and not template_ok("Arrived: {x}", "hello")
    assert norm_target("+82 10-1234-5678") == norm_target("010 1234 5678")
    assert action_of("browser_type", {"text": "Kim"}, "shop.example.com", "personal")[0] == "send"
    assert action_of("workroom_run", {"code": "print(1)"}) is None                   # running code never asks, so nothing to cover


def test_typing_details_on_the_granted_site_is_covered_but_not_counted(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    scope = f"schedule:{poll['id']}"
    grants.add_action(scope, "send", "apply for the class", ["site:classes.example.org"], max_count=1)
    used = []
    grants.on_use = lambda g, tool, host, detail: used.append((tool, detail))
    with in_scope(scope):
        typed = grants.action_check("browser_type", {"selector": "#name", "text": "Kim"}, "www.classes.example.org", "personal")
        assert typed.grant is not None
        grants.use_action(typed.grant, "browser_type", "classes.example.org", "personal")
        sent = grants.action_check("browser_click", {"selector": "#apply"}, "classes.example.org", "apply")
        grants.use_action(sent.grant, "browser_click", "classes.example.org", "apply")
        assert grants.action_check("browser_click", {"selector": "#apply"}, "classes.example.org", "apply").reason == "task_used_up"
        assert grants.action_check("browser_click", {"selector": "#x"}, "other.example.net", "send").reason == "task_other_target"
    assert [u[0] for u in used] == ["browser_type", "browser_click"] and "(1/1)" in used[1][1]


async def test_a_login_grant_of_a_watcher_signs_in_only_in_that_watchers_runs(tmp_path):
    from assistant.tools.vault_tools import vault_tools
    from test_vault import PASSWORD, USER, Page, session_for
    store, wallet, schedules, grants, poll = world(tmp_path)
    store.save_credential("tix", "Tickets", ["tickets.example.com"], {}, kind="login")
    store.set_login("tix", USER, PASSWORD)
    grants.add_login(None, "tix", "tickets.example.com", scope="watcher:4")
    tool = {t.name: t for t in vault_tools(store, session_for(Page(url="https://tickets.example.com/login")), grants=grants)}["browser_login"]

    async def auto():
        got = tool.auto_approve({"name": "tix"})
        return await got if asyncio.iscoroutine(got) else got
    with in_scope("watcher:4"):
        assert await auto() is True
    with in_scope("watcher:5"):
        assert await auto() is False
    with in_scope(f"schedule:{poll['id']}"):
        assert await auto() is False
    store.set_vault_locked(True)
    with in_scope("watcher:4"):
        assert await auto() is False                                                  # the lock still wins


@pytest.mark.parametrize("stop", ["paused", "vault_locked"])
async def test_the_wallets_pause_and_the_vault_lock_still_stop_a_granted_purchase(tmp_path, stop):
    store, wallet, schedules, grants, poll = world(tmp_path)
    card = auto_card(wallet)
    grants.add_purchase(None, "tickets.example.com", "concert ticket", 50000, "KRW", scope="watcher:3", max_count=1, card_id=card["id"])
    if stop == "paused":
        wallet.set_paused(True)
    else:
        store.set_vault_locked(True)
    for pay_with in ({"card": card["label"]}, {"pay_with": "site points"}):
        run, asked, _ = buyer(wallet, [call(1, "confirm_purchase", {**TICKET, **pay_with}), END], [False], grants)
        with in_scope("watcher:3"):
            (res,) = await run()
        assert len(asked) == 1 and asked[0][1]["reason"] == stop and f"[{stop}]" in res.output
    assert wallet.payments() == []


async def test_another_card_or_a_job_handed_off_from_the_task(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    card, other = auto_card(wallet), auto_card(wallet, number="5555555555554444", label="Spare")
    grants.add_purchase(None, "tickets.example.com", "concert ticket", 50000, "KRW", scope="watcher:3", max_count=2, card_id=card["id"])
    run, asked, _ = buyer(wallet, [call(1, "confirm_purchase", {**TICKET, "card": "Spare"}), END], [False], grants)
    with in_scope("watcher:3"):
        await run()
    assert asked[0][1]["reason"] == "task_other_card"
    grants.link_job("j1", "watcher:3")                                                # a job the watcher's run handed off
    run, asked, _ = buyer(wallet, [call(1, "confirm_purchase", TICKET), END], [], grants)
    with in_scope("job:j1"):
        (res,) = await run()
    assert asked == [] and "standing permission" in res.output and wallet.payments()[0]["card_id"] == card["id"]   # the task's own card
    assert grants.drop_job("j1") == 0 and grants.list(scope="watcher:3")             # the job's end leaves its parent's grants


def test_grants_are_revoked_and_cleaned_up_with_their_task(tmp_path):
    from assistant.watchers import Watchers
    store, wallet, schedules, grants, poll = world(tmp_path)
    watchers = Watchers(store)
    grants.watchers = watchers
    schedules.on_deleted, watchers.on_deleted = grants.drop_schedule, grants.drop_watcher
    w = watchers.create("agent", name="Seat watch", url="https://tickets.example.com/show", mode="text_appears", match="available")
    a = grants.add_action(f"watcher:{w['id']}", "send", "tell my wife", ["wife@example.com"], max_count=1)
    b = grants.add_purchase(poll["id"], "tickets.example.com", "ticket", 50000, "KRW", max_count=1)
    c = grants.add_action("job:j9", "change", "move it", ["calendar"])
    assert a["task_name"] == "Seat watch" and a["scope"] == "watcher" and b["scope"] == "schedule"
    assert grants.revoke(a["id"], scope="watcher:999") is False and grants.revoke(a["id"], scope=f"watcher:{w['id']}") is True
    a = grants.add_action(f"watcher:{w['id']}", "send", "tell my wife", ["wife@example.com"], max_count=1)
    assert watchers.delete(w["id"]) and grants.get(a["id"]) is None
    assert schedules.delete(poll["id"]) and grants.get(b["id"]) is None
    assert grants.drop_job("j9") == 1 and grants.get(c["id"]) is None


# ---------------------------------------------------------------------------------------------------------------- through the server
def server_model(plan):
    """A fake model for the server: `plan(request)` gives the next tool call (name, args) or None to finish with a short line."""
    async def fn(provider, request, **kw):
        last = request.messages[-1]
        nxt = plan(request) if last.get("role") != "tool" or plan.again(request) else None
        if nxt is None:
            yield TextDelta("done")
            yield Done("stop")
            return
        yield ToolCall(f"c{len(request.messages)}", nxt[0], json.dumps(nxt[1], ensure_ascii=False))
        yield Done("tool_calls")
    return fn


class Plan:
    """The tool calls a run makes, one after another (each after the last one's result)."""

    def __init__(self, *calls):
        self.calls = list(calls)
        self.i = 0

    def __call__(self, request):
        if self.i >= len(self.calls):
            return None
        self.i += 1
        return self.calls[self.i - 1]

    def again(self, request):
        return self.i < len(self.calls)

    def reset(self, *calls):
        self.calls, self.i = list(calls), 0


KINDS = {
    # kind: (fake tool name, its call, the grant)
    "email": ("email_send", {"to": ["wife@example.com"], "subject": "Package", "body": "Your package arrived: a lamp"},
              ("send", ["wife@example.com"], {"template": "Your package arrived: {item}"})),
    "text": ("phone_send_text", {"to": "010-1234-5678", "text": "Package is here"}, ("send", ["+82 10 1234 5678"], {})),
    "calendar": ("gcal_update_event", {"event_id": "run1", "start": "2026-11-10T19:00"}, ("change", ["calendar"], {})),
    "delete": ("gcal_delete_event", {"event_id": "run1"}, ("change", ["calendar"], {"allow_delete": True})),
    "connector": ("mcp_call_tool", {"name": "notion", "tool": "create_page", "arguments": {"title": "x"}}, ("change", ["notion"], {})),
}
OUTSIDE = {
    "email": {"to": ["boss@example.com"], "subject": "Package", "body": "Your package arrived: a lamp"},
    "text": {"to": "010-9999-0000", "text": "Package is here"},
    "calendar": None, "delete": None,
    "connector": {"name": "slack", "tool": "post_message", "arguments": {}},
}


async def start(c, app, mechanism):
    """A task of the given mechanism; returns (scope, fire) where fire() starts one run of it and waits for its end."""
    if mechanism == "schedule":
        s = (await c.post("/api/schedules", json={"name": "Package check", "prompt": "check the package", "every_minutes": 30})).json()

        async def fire():
            run = (await c.post(f"/api/schedules/{s['id']}/run")).json()
            await follow(c, run["turn_id"])
        return f"schedule:{s['id']}", fire, s["id"]
    w = (await c.post("/api/watchers", json={"name": "Package check", "url": "https://parcel.example.com/track", "mode": "text_appears",
                                            "match": "delivered"})).json()
    watchers = app.state.watchers

    async def fire():
        seen = len(await _handoff_done(c))
        watchers.on_fire(watchers.get(w["id"]), {"detail": "delivered", "url": "https://parcel.example.com/track"})
        await until(lambda: _more(c, seen), tries=600)
    return f"watcher:{w['id']}", fire, w["id"]


async def _more(c, seen):
    return len(await _handoff_done(c)) > seen


async def _handoff_done(c):
    items = (await c.get("/api/tasks", params={"limit": 50})).json()
    rows = items["items"] if isinstance(items, dict) else items
    return [r for r in rows if r.get("source") == "handoff" and r.get("status") not in ("running", "waiting_approval")]


@pytest.mark.parametrize("mechanism", ["schedule", "watcher"])
@pytest.mark.parametrize("kind", list(KINDS))
async def test_inside_the_grant_it_runs_without_a_card_notifies_and_a_one_shot_task_ends(tmp_path, monkeypatch, mechanism, kind):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.5)
    name, args, (gkind, targets, extra) = KINDS[kind]
    ran: list = []
    plan = Plan((name, args))
    app = create_app(app_settings(tmp_path), tools=[approval_tool(name, ran)], stream_fn=server_model(plan))
    grants: ConditionalGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, ref = await start(c, app, mechanism)
        g = grants.add_action(scope, gkind, "tell / change", targets, max_count=1, once=True, action="do the thing", **extra)
        listed = (await c.get(f"/api/{'schedules' if mechanism == 'schedule' else 'watchers'}/{ref}/grants")).json()["items"]
        assert [x["id"] for x in listed] == [g["id"]] and listed[0]["scope"] == mechanism
        await fire()
        assert ran == [args]                                                          # it ran, without a card
        assert (await c.get("/api/approvals/pending")).json()["items"] == []
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["decision"] == "allowed_by_rule" and hist[0]["tool"] == name and "Allowed at setup" in hist[0]["detail"]
        notes = (await c.get("/api/notifications")).json()["items"]
        done = next(n for n in notes if n["type"] == "task_done" and n["title"].startswith("Done as you allowed"))
        assert done["task_id"]
        off = "schedule_disabled" if mechanism == "schedule" else "watch_disabled"
        ended = next(n for n in notes if n["type"] == off)                            # one-shot: done once, then off
        assert ended["title"].startswith("Finished and turned off")
        if mechanism == "schedule":
            s = (await c.get(f"/api/schedules/{ref}")).json()
            assert s["enabled"] is False and "Done what it was set up for" in s["disabled_reason"]
        else:
            w = (await c.get(f"/api/watchers/{ref}")).json()
            assert w["enabled"] is False and "Done what it was set up for" in w["disabled_reason"]
        assert grants.list(scope=scope) == []                                         # its grant went with the run


@pytest.mark.parametrize("mechanism", ["schedule", "watcher"])
@pytest.mark.parametrize("kind", [k for k in KINDS if OUTSIDE[k] is not None])
async def test_outside_the_grant_it_asks_with_an_urgent_card_saying_what_is_missed(tmp_path, monkeypatch, mechanism, kind):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 3)
    name, args, (gkind, targets, extra) = KINDS[kind]
    ran: list = []
    plan = Plan((name, OUTSIDE[kind]))
    app = create_app(app_settings(tmp_path), tools=[approval_tool(name, ran)], stream_fn=server_model(plan))
    grants: ConditionalGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, ref = await start(c, app, mechanism)
        grants.add_action(scope, gkind, "tell / change", targets, max_count=1, once=True, urgent=True, missed="the slot is gone", **extra)
        running = asyncio.ensure_future(fire())
        pend = await until(lambda: _pending(c), tries=400)
        card = pend[0]
        assert card["urgent"] is True and card["missed"] == "the slot is gone"
        assert any(line["label"] == "Why it asks" for line in card["lines"])
        note = next(n for n in (await c.get("/api/notifications")).json()["items"] if n["type"] == "approval_waiting")
        assert note["urgent"] is True and note["title"].startswith("Urgent:") and "the slot is gone" in note["body"]
        await c.post(f"/api/approvals/{card['approval_id']}", json={"approve": False})
        await running
        assert ran == []
        assert len(grants.list(scope=scope)) == 1                                     # nothing used: the task and its grant stay


async def _pending(c):
    return (await c.get("/api/approvals/pending")).json()["items"]


async def test_deleting_not_allowed_and_a_second_time_ask_and_a_revoked_grant_asks_again(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    ran: list = []
    plan = Plan(("gcal_update_event", {"event_id": "a"}), ("gcal_update_event", {"event_id": "b"}), ("gcal_delete_event", {"event_id": "c"}))
    app = create_app(app_settings(tmp_path), tools=[approval_tool("gcal_update_event", ran), approval_tool("gcal_delete_event", ran)],
                     stream_fn=server_model(plan))
    grants: ConditionalGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, sid = await start(c, app, "schedule")
        g = grants.add_action(scope, "change", "move my run", ["calendar"], max_count=1, once=False)
        await fire()
        assert ran == [{"event_id": "a"}]                                            # the second change and the deletion asked, unanswered
        reasons = [x["detail"] for x in (await c.get("/api/approvals/history")).json()["items"]]
        assert sum("Allowed at setup" in r for r in reasons) == 1
        assert (await c.get(f"/api/schedules/{sid}")).json()["enabled"] is True      # not one-shot: it keeps running
        assert (await c.delete(f"/api/schedules/{sid}/grants/{g['id']}")).json() == {"ok": True}
        assert (await c.delete(f"/api/schedules/{sid}/grants/{g['id']}")).status_code == 404
        plan.reset(("gcal_update_event", {"event_id": "d"}))
        await fire()
        assert ran == [{"event_id": "a"}]                                            # revoked: it asks (and nobody answered)


async def test_a_purchase_through_a_watchers_hit_is_bought_notified_and_the_watcher_ends(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    plan = Plan(("confirm_purchase", TICKET), ("confirm_purchase", TICKET))   # the second one asks (nobody answers): never twice
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=server_model(plan))
    grants: ConditionalGrants = app.state.schedule_grants
    app.state.tools.extend(t for t in purchase_tools(None, grants.wallet, CardDeliveries(), grants=grants) if t.name == "confirm_purchase")
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        card = auto_card(grants.wallet)
        scope, fire, wid = await start(c, app, "watcher")
        grants.add_purchase(None, "tickets.example.com", "concert ticket", 50000, "KRW", scope=scope, max_count=1, card_id=card["id"],
                            once=True, trigger="a cancelled seat shows up", action="buy it")
        await fire()
        pays = grants.wallet.payments()
        assert len(pays) == 1 and pays[0]["amount"] == 50000 and pays[0]["kind"] == "auto"
        notes = (await c.get("/api/notifications")).json()["items"]
        paid = next(n for n in notes if n["type"] == "payment_auto")
        assert "50,000" in paid["title"] and paid["task_id"]
        assert any(n["type"] == "watch_disabled" for n in notes)
        assert (await c.get(f"/api/watchers/{wid}")).json()["enabled"] is False
        assert grants.list(scope=scope) == [] and (await c.get(f"/api/watchers/{wid}/grants")).json()["items"] == []
        assert (await c.get("/api/task-grants")).json()["items"] == []


async def test_a_watchers_expired_card_tried_again_still_works_under_the_watchers_grant(tmp_path, monkeypatch):
    """A watcher's hit asks for a step outside what was allowed, nobody answers, and the owner taps "Try again" on the follow-up: the
    run that starts again still works for the watcher, so the step the owner allowed at setup runs without a card, as it would have on
    the first run (it once came back as a plain background task that asked for everything)."""
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    name, args, (gkind, targets, extra) = KINDS["email"]
    ran: list = []
    plan = Plan((name, OUTSIDE["email"]))
    app = create_app(app_settings(tmp_path), tools=[approval_tool(name, ran)], stream_fn=server_model(plan))
    grants: ConditionalGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, _ = await start(c, app, "watcher")
        g = grants.add_action(scope, gkind, "tell my wife", targets, max_count=2, once=False, action="tell her", **extra)
        await fire()
        assert ran == []                                                              # outside the grant: it asked, nobody answered

        async def expired():
            items = (await c.get("/api/followups")).json()["items"]
            return next((i for i in items if i["kind"] == "approval_expired"), None)
        item = await until(expired)
        plan.reset((name, args))                                                       # this time the step the grant covers
        got = (await c.post(f"/api/followups/{item['id']}/retry")).json()
        assert got["ok"] and got["how"] == "background_started"
        await follow(c, got["turn_id"])
        assert ran == [args]                                                           # covered: it ran without a card
        assert (await c.get("/api/approvals/pending")).json()["items"] == []
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert hist[0]["decision"] == "allowed_by_rule" and "Allowed at setup" in hist[0]["detail"]
        assert grants.get(g["id"])["used"] == 1                                        # counted for the watcher's grant


async def test_the_runs_instructions_say_what_was_allowed(tmp_path):
    seen = []

    async def fn(provider, request, **kw):
        seen.append(request.messages)
        yield TextDelta("NO_REPLY")
        yield Done("stop")
    app = create_app(app_settings(tmp_path), tools=[], stream_fn=fn)
    grants: ConditionalGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, _ = await start(c, app, "schedule")
        grants.add_action(scope, "send", "tell my wife", ["wife@example.com"], max_count=1, once=True, trigger="the package is delivered",
                          action="text my wife")
        await fire()
    text = json.dumps(seen[-1], ensure_ascii=False)
    assert "Allowed by the user when this task was set up" in text and "the package is delivered" in text and "wife@example.com" in text
