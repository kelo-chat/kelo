"""One Allow for a repeated step of one task (tools/task_permission.py) and how it covers the browser's committing steps."""

import pytest

from assistant import tool_labels
from assistant.agent import current_approval
from assistant.tools.browser import BrowserSession, browser_tools
from assistant.tools.task_permission import STEPS_PER_ITEM, TaskGrants, task_permission_tools
from assistant.workers import current_turn, current_worker


class Page:
    def __init__(self, url):
        self.url, self.calls = url, []

    def is_closed(self): return False
    async def title(self): return "체험단"
    async def evaluate(self, js): return "text"
    async def goto(self, url, **kw): self.url = url
    async def click(self, sel, **kw): self.calls.append(sel)
    async def wait_for_load_state(self, *a, **kw): pass


async def setup(url="https://www.musinsa.com/campaign", alive=None):
    page = Page(url)

    async def connect(): return page
    sess = BrowserSession(connect=connect)
    await sess.page()
    live = {"turn:t1", "turn:t2", "job:w1"} if alive is None else alive
    grants = TaskGrants(alive=lambda s: s in live)
    tools = {t.name: t for t in browser_tools(sess, grants=grants)}
    return page, sess, grants, tools, live


@pytest.fixture
def in_turn():
    tok = current_turn.set({"task_id": 7, "turn_id": "t1", "source": "chat", "unattended": False})
    yield
    current_turn.reset(tok)


async def test_covered_steps_go_through_without_a_card_and_payments_still_ask(in_turn):
    page, sess, grants, tools, _ = await setup()
    click, type_ = tools["browser_click"].auto_approve, tools["browser_type"].auto_approve
    assert not await click({"selector": "text=신청하기"})                       # nothing allowed yet: a card
    used = []
    grants.on_use = lambda g, tool, kind, host: used.append((tool, kind, host))
    grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=30)
    assert await click({"selector": "text=신청하기"})
    assert await click({"selector": "text=제출"})                               # the form's own submit is part of applying
    assert await type_({"selector": "#phone", "text": "010-1234-5678"})        # the applicant's details
    assert used[0] == ("browser_click", "apply", "www.musinsa.com")
    # never covered: paying, signing up, account changes, passwords, deleting
    assert not await click({"selector": "text=결제하기"})
    assert not await click({"selector": "text=회원가입"})
    assert not await click({"selector": "text=비밀번호 변경"})
    assert not await click({"selector": "text=삭제"})
    assert not await type_({"selector": "input[name=password]", "text": "hunter22"})
    assert grants.active()[0]["used"] == 2


async def test_the_count_is_the_limit(in_turn):
    _, _, grants, tools, _ = await setup()
    grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=2)
    click = tools["browser_click"].auto_approve
    assert await click({"selector": "text=신청하기"}) and await click({"selector": "text=신청하기"})
    assert not await click({"selector": "text=신청하기"})                       # the third asks again
    assert await tools["browser_type"].auto_approve({"selector": "#name", "text": "a@b.co"})   # details still fill in


async def test_every_covered_step_counts_towards_a_hard_cap(in_turn):
    _, _, grants, tools, _ = await setup()
    grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=1)
    type_ = tools["browser_type"].auto_approve
    for _ in range(STEPS_PER_ITEM):
        assert await type_({"selector": "#email", "text": "a@b.co"})
    assert not await type_({"selector": "#email", "text": "a@b.co"})


async def test_another_site_asks(in_turn):
    page, _, grants, tools, _ = await setup()
    grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=30)
    assert await tools["browser_click"].auto_approve({"selector": "text=신청하기"})
    page.url = "https://store.musinsa.com/x"                                   # the same registrable domain
    assert await tools["browser_click"].auto_approve({"selector": "text=신청하기"})
    page.url = "https://other-shop.example/campaign"
    assert not await tools["browser_click"].auto_approve({"selector": "text=신청하기"})


async def test_it_ends_with_its_task_and_does_not_reach_other_tasks(in_turn):
    _, _, grants, tools, live = await setup()
    grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=30)
    click = tools["browser_click"].auto_approve
    tok = current_turn.set({"task_id": 8, "turn_id": "t2"})
    try:
        assert not await click({"selector": "text=신청하기"})                   # another reply: not covered
    finally:
        current_turn.reset(tok)
    assert await click({"selector": "text=신청하기"})
    grants.end("turn:t1")                                                      # the reply is over
    assert grants.active() == []
    assert not await click({"selector": "text=신청하기"})


async def test_a_finished_task_is_noticed_without_being_told(in_turn):
    _, _, grants, tools, live = await setup()
    grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=30)
    live.discard("turn:t1")
    assert not await tools["browser_click"].auto_approve({"selector": "text=신청하기"})


async def test_a_job_started_by_the_task_inherits_it_and_keeps_it_after_the_reply_ends(in_turn):
    _, _, grants, tools, live = await setup()
    g = grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=3)
    assert grants.inherit("turn:t1", "job:w1") == 1
    grants.end("turn:t1")
    tok = current_worker.set("w1")
    try:
        assert await tools["browser_click"].auto_approve({"selector": "text=신청하기"})
        assert grants.active()[0]["jobs"] == ["w1"]
        live.discard("job:w1")                                                 # the job finished
        assert not await tools["browser_click"].auto_approve({"selector": "text=신청하기"})
    finally:
        current_worker.reset(tok)
    assert g.used == 1


async def test_revoking_stops_it_at_once(in_turn):
    _, _, grants, tools, _ = await setup()
    g = grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=30)
    assert grants.revoke(g.id) and not grants.revoke(g.id)
    assert not await tools["browser_click"].auto_approve({"selector": "text=신청하기"})


async def test_the_tool_asks_once_and_refuses_payments(in_turn):
    page, sess, grants, _, _ = await setup()
    tool = task_permission_tools(grants, sess.current_host, lambda: 7)[0]
    assert tool.requires_approval and tool.auto_approve is None               # always a card
    ok = {"site": "musinsa.com", "kind": "apply", "action": "체험단 신청", "max_count": 30}
    assert tool.validate(ok) is None
    assert "confirm_purchase" in tool.validate({**ok, "kind": "pay"})
    assert "never covers paying" in tool.validate({**ok, "action": "상품 구매"})
    assert "never covers paying" in tool.validate({**ok, "action": "회원가입"})
    assert "max_count" in tool.validate({**ok, "max_count": 0})
    assert "not on other-shop.example" in tool.validate({**ok, "site": "other-shop.example"})
    tok = current_approval.set(("", ""))
    try:
        out = await tool.handler(ok)
    finally:
        current_approval.reset(tok)
    assert "ALLOWED" in out and grants.active()[0]["task_id"] == 7
    assert "30" in await tool.describe(ok)


def test_the_card_asks_a_whole_question_with_the_plan():
    from assistant import i18n
    args = {"site": "https://www.musinsa.com/", "kind": "apply", "action": "체험단 신청", "max_count": 30}
    tok = i18n.use("ko")
    try:
        assert tool_labels.ask("request_task_permission", args) == "이 작업 동안 musinsa.com에서 체험단 신청 최대 30건, 해도 될까요?"
    finally:
        i18n.reset(tok)
    assert tool_labels.ask("request_task_permission", args) == "During this task, 체험단 신청 on musinsa.com, up to 30 times. Go ahead?"
    assert tool_labels.ask("request_task_permission") == ""
    assert tool_labels.ask("browser_click") == "Click this on the page?"


# ---- through the server: the card's question, the list, the end of the task
async def test_the_server_asks_the_plan_lists_it_while_the_reply_runs_and_drops_it_after(tmp_path):
    import httpx

    from assistant.agent import Tool
    from assistant.server import create_app
    from test_agent_profile import app_settings
    from test_handover import scripted, waiting_item
    from test_server import AUTH, Running, follow
    from test_workers import call, say

    holder, seen = {}, []

    async def allow(args):
        holder["app"].state.task_grants.add(site=args["site"], kind=args["kind"], action=args["action"], max_count=args["max_count"])
        return "ALLOWED"

    async def peek(args):
        seen.append(holder["app"].state.task_grants.active())
        return "ok"

    plan = {"site": "musinsa.com", "kind": "apply", "action": "체험단 신청", "max_count": 30}
    fn = scripted(call("request_task_permission", plan, cid="p1"), call("peek", {}, cid="k1"), say("신청할게요."))
    tools = [Tool("request_task_permission", "x", {"type": "object"}, allow, requires_approval=True),
             Tool("peek", "x", {"type": "object"}, peek)]
    app = holder["app"] = create_app(app_settings(tmp_path), tools=tools, stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers={**AUTH, "Accept-Language": "ko"}) as c:
        tid = (await c.post("/api/turns", json={"text": "무신사 체험단 다 신청해줘"})).json()["id"]
        item = await waiting_item(c)
        assert item["ask"] == "이 작업 동안 musinsa.com에서 체험단 신청 최대 30건, 해도 될까요?"
        await c.post(f"/api/approvals/{item['approval_id']}", json={"approve": True})
        await follow(c, tid)
        assert seen and seen[0][0]["site"] == "musinsa.com" and seen[0][0]["max_count"] == 30
        assert (await c.get("/api/task-permissions")).json() == {"items": []}      # the reply is over: so is its permission
        hist = (await c.get("/api/approvals/history")).json()["items"]
        assert any(h["tool"] == "request_task_permission" and h["decision"].startswith("allowed") for h in hist)
        assert (await c.delete("/api/task-permissions/tpnope")).status_code == 404
        g = app.state.task_grants.add(site="musinsa.com", kind="apply", action="x", max_count=1, scope="turn:gone")
        assert (await c.delete(f"/api/task-permissions/{g.id}")).json() == {"ok": True}


async def test_a_job_the_reply_hands_the_work_to_keeps_the_permission_after_the_reply_ends(tmp_path):
    import asyncio

    import httpx

    from assistant.agent import Tool
    from assistant.server import create_app
    from test_agent_profile import app_settings
    from test_server import AUTH, Running, follow
    from test_workers import Brain, call, one_worker, say

    holder, seen = {}, []

    async def allow(args):
        holder["app"].state.task_grants.add(site="musinsa.com", kind="apply", action="체험단 신청", max_count=30)
        return "ALLOWED"

    async def peek(args):
        seen.append(holder["app"].state.task_grants.active())
        return "ok"

    def chat(req, n):
        return [call("request_task_permission", {}, cid="p1"), call("delegate", {"task": "체험단 신청", "kind": "general"}, cid="d1"),
                say("맡겼어요.")][min(n, 2)]

    gate = asyncio.Event()

    async def worker(req, n):
        if n == 0:
            await gate.wait()
            return call("peek", {}, cid="k1")
        return call("report", {"status": "done", "summary": "다 했어요."})

    brain = Brain(chat=chat, worker=worker)
    tools = [Tool("request_task_permission", "x", {"type": "object"}, allow), Tool("peek", "x", {"type": "object"}, peek)]
    app = holder["app"] = create_app(app_settings(tmp_path), tools=tools, stream_fn=brain, with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "체험단 다 신청해줘"})).json()["id"])
        w = await one_worker(c)
        gate.set()                                       # the chat reply is over; the job now works under the permission
        await one_worker(c, "done")
        assert seen and seen[0][0]["jobs"] == [w["id"]] and seen[0][0]["action"] == "체험단 신청"
        assert app.state.task_grants.active() == []      # the job is done: so is the permission


# ---- many items on many sites: one card with the real count and the list ("all of them" is never approved in chunks)
class ReadablePage(Page):
    """A page whose elements can be read (commits.TARGET_JS): selector -> what the element is."""

    def __init__(self, url, elements):
        super().__init__(url)
        self.elements = elements

    def locator(self, selector):
        page = self

        class First:
            async def evaluate(self, js, timeout=None):
                info = page.elements.get(selector)
                if info is None:
                    raise RuntimeError("no such element")
                return {"url": page.url, "title": "", "tag": "button", "type": "", "role": "", "words": "", "attrs": "", "href": "",
                        "in_form": False, "submits": False, **info}

        class Loc:
            first = First()
        return Loc()


async def readable_setup(url, elements):
    page = ReadablePage(url, elements)

    async def connect(): return page
    sess = BrowserSession(connect=connect)
    await sess.page()
    grants = TaskGrants(alive=lambda s: s in {"turn:t1", "job:w1"})
    return page, sess, grants, {t.name: t for t in browser_tools(sess, grants=grants)}


async def test_opting_out_is_its_own_low_risk_kind_judged_the_same_on_every_retry():
    from assistant.tools.commits import judge_click, judge_words
    assert judge_words("Unsubscribe from all deal emails") == "optout"
    assert judge_words("수신 거부") == "optout" and judge_words("配信停止") == "optout" and judge_words("Turn off notifications") == "optout"
    assert judge_words("Delete my account") == "delete" and judge_words("Cancel subscription") == "delete"   # paid plans stay deletions
    # a plain "Submit" on a page made for opting out is the opt-out itself, not "sending something in your name"
    info = {"url": "https://news.example/email-preferences", "title": "", "text": "Submit", "tag": "button", "submits": True, "in_form": True}
    assert judge_click(info, "button") == "optout"
    assert judge_click(None, "button, input[type=submit], a:has-text('Unsubscribe from all emails')") == "optout"
    assert judge_click({**info, "url": "https://news.example/contact"}, "button") == "send"  # elsewhere "Submit" sends a form


async def test_one_permission_covers_the_same_step_across_many_sites_with_the_real_count(in_turn):
    page, sess, grants, tools = await readable_setup("https://a-news.example/unsubscribe", {"#go": {"text": "Unsubscribe"}})
    click = tools["browser_click"].auto_approve
    assert not await click({"selector": "#go"})                                    # nothing allowed: a card
    targets = [f"Sender {i} <news@site{i}.example>" for i in range(150)]
    sites = ["a-news.example", "b-news.example", *[f"site{i}.example" for i in range(148)]]
    g = grants.add(kind="optout", action="구독 취소", max_count=20, sites=sites, all_found=True, targets=targets, what="홍보 메일 보낸 곳")
    assert g.max_count == 150                                                      # all of them: the number found, not a cap of 20
    assert await click({"selector": "#go"}) and await click({"selector": "#go"})   # a confirm step on the same site
    assert g.used == 1                                                             # one item per site
    page.url = "https://b-news.example/optout"
    assert await click({"selector": "#go"}) and g.used == 2
    page.url = "https://unknown.example/unsubscribe"
    assert not await click({"selector": "#go"})                                    # a site that is not in the list asks
    assert grants.progress("turn:t1") == (2, 150)


async def test_another_kind_follows_the_same_flow_across_sites_and_stops_at_its_count(in_turn):
    page, sess, grants, tools = await readable_setup("https://one.example/invite", {"#no": {"text": "Delete"}})
    click = tools["browser_click"].auto_approve
    g = grants.add(kind="delete", action="초대 삭제", max_count=2, sites=["one.example", "two.example", "three.example"],
                   targets=["one", "two", "three"])
    assert g.max_count == 2                                                        # not "all": the count asked for
    assert await click({"selector": "#no"})
    page.url = "https://two.example/invite"
    assert await click({"selector": "#no"})
    page.url = "https://three.example/invite"
    assert not await click({"selector": "#no"})                                    # the count is used up: asks again
    assert not grants.add(kind="delete", action="x", max_count=5, sites=["one.example"], from_links=True, all_found=True,
                          targets=["a"]).from_links                                # links and growth only for low-risk kinds


async def test_from_links_covers_sites_the_users_own_data_links_to_and_more_found_while_doing_it(in_turn):
    page, sess, grants, tools = await readable_setup("https://c-news.example/u", {"#go": {"text": "Unsubscribe"}})
    click = tools["browser_click"].auto_approve
    g = grants.add(kind="optout", action="opt out", max_count=1, from_links=True, all_found=True, targets=["c"])
    assert not await click({"selector": "#go"})                                    # no link read leads there yet
    assert grants.note_links("From: x\n\nTo stop: https://c-news.example/u?id=1 or https://d-news.example/x") == 2
    assert await click({"selector": "#go"})
    page.url = "https://d-news.example/x"
    assert await click({"selector": "#go"}) and g.max_count == 2                   # one more of the same kind turned up: covered
    grants.end("turn:t1")
    assert grants.active() == []


async def test_the_card_names_what_the_user_sees_never_the_selector_and_warns_by_kind(in_turn):
    from assistant import i18n
    page, sess, grants, tools = await readable_setup("https://deals.example/prefs", {
        "text=Unsubscribe from all deal emails": {"text": "Unsubscribe from all deal emails", "tag": "label"},
        "label:has-text('Unsubscribe from all deal emails')": {"text": "Unsubscribe from all deal emails", "tag": "label"},
    })
    click = tools["browser_click"]
    sel = "text=Unsubscribe from all deal emails"
    assert not await click.auto_approve({"selector": sel})
    tok = i18n.use("ko")
    try:
        text = await click.describe({"selector": sel})
    finally:
        i18n.reset(tok)
    assert "text=" not in text and "Unsubscribe from all deal emails" in text and "deals.example" in text
    assert "삭제" not in text and "보내거나" not in text                            # an opt-out, said as one
    first = await click.describe({"selector": sel})
    other = "label:has-text('Unsubscribe from all deal emails')"
    assert not await click.auto_approve({"selector": other})
    assert (await click.describe({"selector": other})) == first                   # the same words for the same element
    # a selector that cannot be read and quotes nothing from the page: "a button", never the selector itself
    soup = "button, input[type=submit], div > span:nth-child(2)"
    await click.auto_approve({"selector": soup})
    assert "nth-child" not in await click.describe({"selector": soup}) and "input[" not in await click.describe({"selector": soup})


async def test_a_failed_click_tried_again_with_another_selector_for_the_same_element_does_not_ask_again(in_turn):
    page, sess, grants, tools = await readable_setup("https://deals.example/prefs", {
        "text=Unsubscribe": {"text": "Unsubscribe"}, "button:has-text('Unsubscribe')": {"text": "Unsubscribe"},
        "text=Delete account": {"text": "Delete account"},
    })
    click = tools["browser_click"]
    assert not await click.auto_approve({"selector": "text=Unsubscribe"})          # the card: the user allows it

    async def fail(selector):
        raise RuntimeError("element is not visible")
    sess.click = fail
    try:
        await click.handler({"selector": "text=Unsubscribe"})
    except RuntimeError:
        pass
    assert await click.auto_approve({"selector": "button:has-text('Unsubscribe')"})   # the same button: no second card
    assert not await click.auto_approve({"selector": "text=Delete account"})          # another element still asks


def test_the_card_for_many_items_asks_with_the_real_count_and_shows_the_list():
    from assistant import i18n
    args = {"kind": "optout", "action": "구독 취소", "max_count": 5, "sites": ["a.example", "b.example"], "all": True,
            "targets": [f"보낸 곳 {i}" for i in range(37)], "what": "홍보 메일 보낸 곳"}
    tok = i18n.use("ko")
    try:
        ask = tool_labels.ask("request_task_permission", args)
    finally:
        i18n.reset(tok)
    assert "37" in ask and "홍보 메일 보낸 곳" in ask and "구독 취소" in ask
    assert tool_labels.ask("request_task_permission", args) == "All 37 홍보 메일 보낸 곳: 구독 취소. Go ahead?"
    grants = TaskGrants(scope=lambda: "turn:t1")
    tool = task_permission_tools(grants)[0]
    extra = tool.approval_extra("a1", args)
    assert len(extra["items"]) == 37 and extra["lines"][0]["value"].startswith("a.example")
    assert tool.validate(args) is None
    assert "targets" in tool.validate({**args, "targets": []})                     # "all" needs the list found first
    assert "from_links" in tool.validate({**args, "kind": "delete", "from_links": True})
    assert "at most 100" in tool.validate({**args, "all": False, "max_count": 300})


async def test_a_permission_for_many_items_gives_the_reply_more_steps(in_turn):
    grants = TaskGrants(alive=lambda s: True)
    assert grants.step_bonus() == 0
    grants.add(kind="optout", action="x", max_count=10, sites=["a.example"])
    assert grants.step_bonus() == 30
