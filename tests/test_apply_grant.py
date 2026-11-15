"""An "apply" permission for a task that applies on its own ("매일 체험단 확인하고 신청해"): set up once with task_allow (one site, what is
applied for, a most per run and optionally a total), a run applies on that site without a card while the counts last, every step is in
the approval log, and ONE notice after the run says how many and what. Another site, a payment, an account step and anything past the
count ask as before. Models, pages and the browser's judgement are fakes."""

import asyncio

import httpx

from assistant.schedule_grants import ConditionalGrants, task_allow_tools
from assistant.server import create_app
from assistant.tools.browser import BrowserSession
from assistant.workers import current_turn

from test_agent_profile import app_settings, approval_tool
from test_conditional_grants import Plan, in_scope, server_model, start, world
from test_server import AUTH, Running

SITE = "trial-reviews.co.kr"


def run(n):
    """Another run of the same task (its own task id): the per-run count starts again."""
    class _Run(in_scope):
        def __enter__(self):
            super().__enter__()
            cur = dict(current_turn.get())
            cur["task_id"] = n
            current_turn.set(cur)
    return _Run


# ---------------------------------------------------------------------------------------------------------------- the rules
def test_applies_on_its_site_up_to_the_count_per_run_and_in_all(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    scope = f"schedule:{poll['id']}"
    g = grants.add_apply(scope, f"https://www.{SITE}/campaigns", "선물 체험단", max_count=2, total_count=3, once=False)
    assert g["kind"] == "apply" and g["site"] == SITE and g["max_count"] == 2 and g["total_count"] == 3
    used = []
    grants.on_use = lambda g, tool, host, detail: used.append((tool, detail))
    with run(1)(scope):
        one = grants.action_check("browser_click", {"selector": "#apply-1"}, f"www.{SITE}", "apply")
        assert one.grant is not None and one.kind == "apply"
        assert grants.use_action(one.grant, "browser_click", SITE, "apply") is True
        typed = grants.action_check("browser_type", {"selector": "#phone", "text": "010"}, SITE, "personal")   # its form's details
        assert typed.grant is not None and grants.use_action(typed.grant, "browser_type", SITE, "personal") is False
        sent = grants.action_check("browser_click", {"selector": "#submit"}, SITE, "form")                  # and its form's submit
        assert sent.grant is not None and grants.use_action(sent.grant, "browser_click", SITE, "form") is False
        two = grants.action_check("browser_click", {"selector": "#apply-2"}, SITE, "apply")
        assert grants.use_action(two.grant, "browser_click", SITE, "apply") is True
        third = grants.action_check("browser_click", {"selector": "#apply-3"}, SITE, "apply")
        assert third.grant is None and third.reason == "task_run_used_up"                                   # past the run's count
        assert grants.action_check("browser_click", {"selector": "#submit"}, SITE, "form").reason == "task_run_used_up"
        other = grants.action_check("browser_click", {"selector": "#apply"}, "other.example.net", "apply")
        assert other.grant is None and other.reason == "task_other_site"
        assert grants.action_check("browser_click", {"selector": "#pay"}, SITE, "pay").grant is None          # payments: never here
        assert grants.action_check("browser_click", {"selector": "#join"}, SITE, "account").reason == "task_account"
        assert grants.action_check("browser_click", {"selector": "#del"}, SITE, "delete").grant is None
    with run(2)(scope):                                                                # the next day's run: its own count, the total left
        nxt = grants.action_check("browser_click", {"selector": "#apply-4"}, SITE, "apply")
        assert nxt.grant is not None and grants.use_action(nxt.grant, "browser_click", SITE, "apply") is True
        assert grants.action_check("browser_click", {"selector": "#apply-5"}, SITE, "apply").reason == "task_used_up"   # 3 in all
    with in_scope(""):                                                                 # a chat: nothing is covered
        assert grants.action_check("browser_click", {"selector": "#apply"}, SITE, "apply").grant is None
    counted = [d for tool, d in used if "(1/2)" in d or "(2/2)" in d]
    assert len(counted) == 3 and all("선물 체험단" in d and SITE in d for d in counted)
    assert len(used) == 5                                                              # every covered step is in the log
    assert "apply for '선물 체험단' on trial-reviews.co.kr, at most 2 per run, 0 of 3 left in all" in grants.summary(scope)
    assert grants.revoke(g["id"], scope=scope) and grants.list(scope=scope) == []      # revocable like the others


def test_the_setup_card_states_the_site_what_and_counts(tmp_path):
    store, wallet, schedules, grants, poll = world(tmp_path)
    tool = task_allow_tools(grants)[0]
    args = {"schedule_id": poll["id"], "trigger": "매일 아침", "action": "새 체험단을 확인하고 신청", "once": False,
            "apply": {"site": f"https://{SITE}", "what": "선물 체험단", "max_count": 3, "total": 30}}
    assert tool.validate(args) is None
    card = tool.approval_extra("a1", args)
    assert card["grant"]["apply"] == {"site": SITE, "what": "선물 체험단", "max_count": 3, "total": 30}
    lines = {line["label"]: line["value"] for line in card["lines"]}
    assert lines["Apply for"] == f"선물 체험단 · {SITE}" and "3 per run" in lines["Up to"] and "30 in total" in lines["Up to"]
    assert "never covers sign-ups" in tool.validate({**args, "apply": {"site": SITE, "what": "회원가입 이벤트"}})
    assert "apply.site" in tool.validate({**args, "apply": {"what": "x"}})
    out = asyncio.run(tool.handler(args))
    assert out.startswith("ALLOWED") and "at most 3 per run and 30 in all" in out
    assert grants.list(scope=f"schedule:{poll['id']}")[0]["kind"] == "apply"


# ---------------------------------------------------------------------------------------------------------------- a scheduled run
def judging_session(kinds):
    """A browser session whose judgement of each selector is given (the real one reads the element; tools/commits.py)."""
    async def connect():
        raise RuntimeError("no browser in this test")
    sess = BrowserSession(connect=connect)
    sess.judged = lambda sel: kinds.get(sel, "")
    sess.judged_secret = lambda sel: False
    return sess


async def test_a_daily_run_applies_without_a_card_up_to_the_count_and_tells_once(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    page = f"https://www.{SITE}/campaigns/"
    plan = Plan(("browser_click", {"selector": "#apply-1", "url": page + "1"}),
                ("browser_click", {"selector": "#submit", "url": page + "1"}),          # the application's form: part of it
                ("browser_click", {"selector": "#apply-2", "url": page + "2"}),
                ("browser_click", {"selector": "#apply-3", "url": page + "3"}))          # past the count: asks
    kinds = {"#apply-1": "apply", "#apply-2": "apply", "#apply-3": "apply", "#apply-x": "apply", "#submit": "form", "#buy": "pay"}
    ran: list = []
    app = create_app(app_settings(tmp_path), tools=[approval_tool("browser_click", ran)], stream_fn=server_model(plan),
                     browser=judging_session(kinds))
    grants: ConditionalGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, ref = await start(c, app, "schedule")
        grants.add_apply(scope, SITE, "선물 체험단", max_count=2, once=False, action="새 체험단 신청")
        await fire()
        assert [a["selector"] for a in ran] == ["#apply-1", "#submit", "#apply-2"]     # without a card; the third asked and ran out
        hist = (await c.get("/api/approvals/history", params={"limit": 50})).json()["items"]
        by_rule = [h for h in hist if h["decision"] == "allowed_by_rule"]
        assert len(by_rule) == 3 and all(h["tool"] == "browser_click" for h in by_rule)
        assert [(h["site"], h["decision"]) for h in hist if h["decision"] != "allowed_by_rule"] == [(SITE, "expired")]
        notes = (await c.get("/api/notifications")).json()["items"]
        told = [n for n in notes if n["type"] == "task_done" and "application" in n["title"]]
        assert len(told) == 1 and told[0]["title"] == "2 application(s) sent"           # one notice for the run, with the items
        assert SITE in told[0]["body"]
        assert not any(n["title"].startswith("Done as you allowed") for n in notes)      # not one per click
        # the next run: a payment on the same site and an application on another site both ask
        plan.reset(("browser_click", {"selector": "#buy", "url": page + "4"}),
                   ("browser_click", {"selector": "#apply-x", "url": "https://other.example.net/c/9"}))
        await fire()
        assert len(ran) == 3
        hist = (await c.get("/api/approvals/history", params={"limit": 50})).json()["items"]
        assert [(h["site"], h["decision"]) for h in hist[:2]] == [("other.example.net", "expired"), (SITE, "expired")]
        assert grants.list(scope=scope)[0]["used"] == 2


async def test_a_long_run_tells_once_how_far_it_got(tmp_path, monkeypatch):
    monkeypatch.setattr("assistant.run_progress.PROGRESS_AFTER", 0.0)
    monkeypatch.setattr("assistant.server.UNATTENDED_APPROVAL_SECONDS", 0.3)
    page = f"https://www.{SITE}/campaigns/"
    plan = Plan(("browser_click", {"selector": "#apply-1", "url": page + "1"}),
                ("browser_click", {"selector": "#submit", "url": page + "1"}),          # the application's form: part of it
                ("browser_click", {"selector": "#apply-2", "url": page + "2"}),
                ("browser_click", {"selector": "#apply-3", "url": page + "3"}))          # past the count: asks
    kinds = {"#apply-1": "apply", "#apply-2": "apply", "#apply-3": "apply", "#apply-x": "apply", "#submit": "form", "#buy": "pay"}
    ran: list = []
    app = create_app(app_settings(tmp_path), tools=[approval_tool("browser_click", ran)], stream_fn=server_model(plan),
                     browser=judging_session(kinds))
    grants: ConditionalGrants = app.state.schedule_grants
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        scope, fire, ref = await start(c, app, "schedule")
        grants.add_apply(scope, SITE, "선물 체험단", max_count=2, once=False, action="새 체험단 신청")
        await fire()
        notes = (await c.get("/api/notifications")).json()["items"]
        assert len([n for n in notes if n["title"] == "1 application(s) sent so far, still going"]) == 1
        assert len([n for n in notes if n["title"] == "2 application(s) sent"]) == 1
        assert [n["title"] for n in notes if "so far" in n["title"]] == ["1 application(s) sent so far, still going"]
