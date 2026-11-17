"""The payment split (docs/browser-workers.md, tool_roles.py): the agent that finds things has no paying tool; a job of kind
"checkout" has the paying tools and the browser's page tools and nothing to search with; a shop gets one checkout job at a time.
Fake model, no real browser, no model calls."""

from pathlib import Path

import httpx

from assistant import tool_roles
from assistant.agent import Tool
from assistant.server import create_app
from assistant.tools.browser import BrowserSession
from assistant.tool_roles import PAY_TOOLS, checkout_tools, finder_tools, for_kind
from assistant.workers import MAIN_ONLY

from test_agent_profile import ShotPage, app_settings
from test_server import AUTH, Running, follow
from test_workers import Brain, call, delegating, one_worker, say, until


async def _noop(args):
    return "ok"


def fake(*names):
    return [Tool(n, n, {"type": "object"}, _noop) for n in names]


APP_TOOLS = ("checkout_review", "checkout_submit", "purchase_result", "browser_fill_card", "wallet_cards", "browser_read", "browser_click",
             "browser_fill_shipping", "browser_forget_site", "web_search", "web_fetch", "read_file", "request_login", "begin_order")


def names(tools):
    return {t.name for t in tools}


def test_the_finder_has_no_paying_tool_and_the_specialist_has_no_search():
    tools = fake(*APP_TOOLS)
    assert not names(finder_tools(tools)) & PAY_TOOLS
    assert {"browser_read", "web_search", "read_file", "begin_order"} <= names(finder_tools(tools))
    spec = names(checkout_tools(tools))
    assert {"checkout_review", "checkout_submit", "purchase_result", "browser_fill_card", "browser_read", "browser_fill_shipping",
            "request_login", "begin_order"} <= spec
    assert not spec & {"web_search", "web_fetch", "read_file", "browser_forget_site"}
    assert names(for_kind(tools, "checkout")) == spec and names(for_kind(tools, "browser")) == names(finder_tools(tools))


def test_every_paying_tool_is_either_the_specialists_or_declared_main_only_never_both_ways_open():
    assert not PAY_TOOLS & set(MAIN_ONLY)
    assert tool_roles.CHECKOUT_DENIED <= set(MAIN_ONLY) | {"browser_forget_site"}


def _app(tmp_path, brain, extra=(), **kw):
    async def connect():
        return ShotPage("https://shop.example.com/cart")

    return create_app(app_settings(tmp_path, **{"payment_specialist": True, **kw}), tools=fake(*extra or APP_TOOLS), stream_fn=brain, with_worker_tools=True,
                      browser=BrowserSession(connect=connect))


async def test_the_chat_and_a_browser_job_cannot_pay_but_a_checkout_job_can_and_cannot_search(tmp_path):
    seen = {}

    def worker(req, n):
        seen.setdefault("worker", names_of(req))
        return call("report", {"status": "done", "summary": "s"})

    def names_of(req):
        return {t["function"]["name"] for t in req.tools}

    def chat(req, n):
        seen.setdefault("chat", names_of(req))
        if req.messages[-1].get("role") == "tool":
            return say("맡겼어요.")
        return call("delegate", {"task": "장바구니를 만들어줘", "kind": "browser"})

    brain = Brain(chat=chat, worker=worker)
    app = _app(tmp_path, brain)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "사줘"})).json()["id"])
        await one_worker(c, "done")
    assert "browser_read" in seen["chat"] and "delegate" in seen["chat"] and not seen["chat"] & PAY_TOOLS
    assert "browser_read" in seen["worker"] and not seen["worker"] & PAY_TOOLS


async def test_a_checkout_job_gets_the_paying_tools_a_brief_and_one_window_per_shop(tmp_path):
    seen = {}

    def worker(req, n):
        seen["tools"] = {t["function"]["name"] for t in req.tools}
        seen["brief"] = str(req.messages[1].get("content") if req.messages[0].get("role") == "system" else req.messages[0].get("content"))
        return call("report", {"status": "needs_input", "summary": "the total changed"})

    def chat(req, n):
        last = req.messages[-1]
        if last.get("role") == "tool":
            seen.setdefault("results", []).append(last["content"])
            return say("ok")
        text = [str(m.get("content")) for m in req.messages if m.get("role") == "user"][-1]
        if "one" in text:
            return call("delegate", {"task": "pay for the cart", "kind": "checkout"}, cid="a")          # no url
        if "two" in text:
            return call("delegate", {"task": "pay for the cart", "kind": "checkout", "url": "https://shop.example.com/cart"}, cid="b")
        return call("delegate", {"task": "pay again", "kind": "checkout", "url": "https://www.shop.example.com/checkout"}, cid="c")

    brain = Brain(chat=chat, worker=worker)
    app = _app(tmp_path, brain)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "one"})).json()["id"])
        assert "needs url" in seen["results"][0]
        await follow(c, (await c.post("/api/turns", json={"text": "two"})).json()["id"])
        w = await one_worker(c, "needs_input")
        assert w["kind"] == "checkout"
        await follow(c, (await c.post("/api/turns", json={"text": "three"})).json()["id"])
        assert "still open" in seen["results"][-1] and w["id"] in seen["results"][-1]
    t = seen["tools"]
    assert {"checkout_review", "checkout_submit", "purchase_result", "browser_read", "report"} <= t
    assert not t & {"web_search", "web_fetch", "read_file", "delegate"}
    assert "payment specialist" in seen["brief"]


async def test_with_the_split_switched_off_everything_works_as_before(tmp_path):
    seen = {}

    def chat(req, n):
        seen["chat"] = {t["function"]["name"] for t in req.tools}
        return say("ok")

    app = create_app(app_settings(tmp_path, payment_specialist=False), tools=fake(*APP_TOOLS), stream_fn=Brain(chat=chat), with_worker_tools=True)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "hi"})).json()["id"])
    assert {"checkout_review", "checkout_submit"} <= seen["chat"]


def test_every_name_in_the_role_sets_is_a_tool_the_app_really_has(tmp_path):
    """tool_roles names tools by string: a renamed or removed tool must not leave a dead name (and a specialist without its tool)."""
    from assistant.settings import Settings

    app = create_app(Settings(token="t" * 32, provider="p", model="m", cdp_host="127.0.0.1", db_path=str(tmp_path / "a.db"),
                              home_dir=str(tmp_path / "home")), with_worker_tools=True)
    have = names(app.state.tools)
    own_to_the_job = {"report", "job_progress"}      # built by the job itself (workers.py), not part of the chat's list
    assert not (PAY_TOOLS | tool_roles.CHECKOUT_HELPERS) - own_to_the_job - have
    assert not own_to_the_job & PAY_TOOLS


def test_the_prompt_hands_a_purchase_to_the_checkout_job_without_the_chat_checking_the_cart():
    """Real-model check 2026-11-16: told "do it yourself in the browser", the chat model read the cart itself, kept clicking or asked the
    user, and never delegated (3 of 4 live scenarios). The buying section now says the chat prepares and hands over at once, that the
    specialist reads the cart and returns a difference as a question, and that a shop has one checkout job."""
    from assistant.prompt import PromptBuilder
    content = Path(__file__).resolve().parents[1] / "content"
    text = PromptBuilder.load(content, None, None, pay_split=True).base
    section = text.split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0]
    assert "Do it yourself in the browser" not in section
    assert "your very next call is `delegate` kind checkout" in section and "`delegate` kind checkout" in section
    assert "Do not read the cart" in section and "sends any difference back as a question" in section
    assert "One checkout job per shop" in section


async def test_with_the_switch_on_a_page_read_is_wrapped_and_with_it_off_the_tools_are_the_plain_ones(tmp_path):
    """The hand-over is built into the page tools only with the split on; off, the app's tools are exactly what they were."""
    async def connect():
        return ShotPage("https://shop.example.com/cart")

    plain = create_app(app_settings(tmp_path / "a", payment_specialist=False), tools=fake(*APP_TOOLS), with_worker_tools=True,
                       browser=BrowserSession(connect=connect))
    split = create_app(app_settings(tmp_path / "b", payment_specialist=True), tools=fake(*APP_TOOLS), with_worker_tools=True,
                       browser=BrowserSession(connect=connect))
    own = {t.name: t.handler for t in fake(*APP_TOOLS)}
    assert all(t.handler is own.get(t.name) for t in plain.state.tools if t.name in own)
    assert any(t.handler is not own[t.name] for t in split.state.tools if t.name == "browser_read")


def test_the_buying_paragraph_follows_the_switch_and_the_rest_of_the_prompt_is_the_same():
    from assistant.prompt import PromptBuilder
    content = Path(__file__).resolve().parents[1] / "content"
    on, off = (PromptBuilder.load(content, None, None, pay_split=v).base for v in (True, False))
    section = lambda t: t.split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0]   # noqa: E731
    assert "Do it yourself in the browser" in section(off) and "checkout_submit(review_id)" in section(off)
    assert "delegate" not in section(off) and "You cannot pay" not in section(off)
    assert "You cannot pay" in section(on) and "checkout_submit(review_id)" not in section(on)
    assert on.split("**Buying, ordering, booking.**")[0] == off.split("**Buying, ordering, booking.**")[0]
    assert on.split("\n\n**Later and repeated work.**", 1)[1] == off.split("\n\n**Later and repeated work.**", 1)[1]
    assert off == (content / "prompts" / "system.md").read_text(encoding="utf-8")


def test_the_switch_is_on_unless_turned_off(monkeypatch):
    """Owner 2026-11-17: the payment specialist is on by default (a checkout job pays, the chat does not); 0 turns it off."""
    from assistant.settings import Settings
    monkeypatch.delenv("ASSISTANT_PAYMENT_SPECIALIST", raising=False)
    assert Settings(token="t" * 32).payment_specialist is True
    assert Settings.from_env({"ASSISTANT_TOKEN": "t" * 32}).payment_specialist is True
    assert Settings.from_env({"ASSISTANT_TOKEN": "t" * 32, "ASSISTANT_PAYMENT_SPECIALIST": "0"}).payment_specialist is False
