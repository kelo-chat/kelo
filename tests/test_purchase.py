"""The purchase path: confirm_purchase in front of the final click, the short purchase window after Allow, browser cards in the
chat, labels for tool calls, and the prompt/docs that tell the model to use all this. Sites here are neutral fakes."""

import io
import json
import re
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

from assistant import tool_labels
from assistant.agent.fence import plain
from assistant.agent import ApprovalRequest, ToolResult, run_agent, ui_sink
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.tools import browser as browser_mod
from assistant.tools.browser import BrowserSession, browser_tools, looks_final, shrink_jpeg
from assistant.tools.purchase import purchase_tools

from test_browser_tools import FakePage, fake_resolver
from test_server import AUTH, TOKEN, Running, scripted

CONTENT = Path(__file__).resolve().parents[1] / "content"
PUBLIC = fake_resolver({h: "93.184.216.34" for h in ("shop.example.com", "www.shop.example.com", "pay.shop.example.com",
                                                     "elsewhere.example.net", "other.example.org")})


def jpeg(w=1280, h=800, noise=False) -> bytes:
    img = Image.effect_noise((w, h), 100).convert("RGB") if noise else Image.new("RGB", (w, h), (240, 240, 250))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


class ShopPage(FakePage):
    """A neutral fake shop: every click is recorded; it can take a screenshot and raise a JS pop-up."""

    def __init__(self, url="https://shop.example.com/cart", shot=None):
        super().__init__()
        self.url, self.text, self.shot, self.handlers = url, "Cart: 2 x Blue mug, total 24,000", shot or jpeg(), {}

    async def title(self): return "Example Shop - Cart"
    async def screenshot(self, **kw): return self.shot
    def on(self, event, fn): self.handlers[event] = fn


def shop(page=None):
    page = page or ShopPage()

    async def connect():
        return page
    return BrowserSession(connect=connect, resolver=PUBLIC), page


def by_name(tools):
    return {t.name: t for t in tools}


ORDER = {"summary": "Blue mug x2", "amount": 24000, "currency": "KRW", "site": "shop.example.com", "pay_with": "site balance"}


# ---------------------------------------------------------------- confirm_purchase itself
async def test_confirm_purchase_refuses_bad_requests_before_any_card():
    session, _ = shop()
    await session.page()
    t = by_name(purchase_tools(session))["confirm_purchase"]
    assert t.requires_approval and t.auto_approve is None          # never allowed without asking
    assert t.validate(ORDER) is None
    assert "summary" in t.validate({**ORDER, "summary": " "})
    assert "amount" in t.validate({k: v for k, v in ORDER.items() if k != "amount"})
    assert "amount" in t.validate({**ORDER, "amount": "free?"})
    assert "site" in t.validate({**ORDER, "site": ""})
    assert "card number" in t.validate({**ORDER, "pay_with": "card 4111 1111 1111 1111"})
    assert "card number" in t.validate({**ORDER, "summary": "mug, pay with 4111-1111-1111-1111"})
    assert "not on other.example.org" in t.validate({**ORDER, "site": "https://other.example.org/pay"})
    assert t.validate({**ORDER, "site": "https://www.shop.example.com/checkout", "amount": "24,000원"}) is None


async def test_the_card_says_what_and_how_much_and_shows_the_page():
    session, _ = shop()
    await session.page()
    t = by_name(purchase_tools(session))["confirm_purchase"]
    shown = []

    async def sink(item):
        shown.append(item)
    tok = ui_sink.set(sink)
    try:
        detail = await t.describe(ORDER)
    finally:
        ui_sink.reset(tok)
    assert "shop.example.com" in detail and "Blue mug x2" in detail and "24,000 KRW" in detail and "site balance" in detail
    assert shown and shown[0]["type"] == "browser" and shown[0]["status"] == "Waiting for payment approval"


def agent_run(session, script, answers):
    """Run the loop with a scripted model; `answers` are the user's taps on the approval cards, in order."""
    tools = [*browser_tools(session), *purchase_tools(session)]
    asked: list[ApprovalRequest] = []
    taps = iter(answers)

    async def approve(req):
        asked.append(req)
        return next(taps, False)          # no answer = denied
    return run_agent("p", "m", [{"role": "user", "content": "buy it"}], tools, approve=approve, stream_fn=scripted(*script)), asked


PAY = [ToolCall("c2", "confirm_purchase", json.dumps(ORDER)), Done("tool_calls")]
CLICK_PAY = [ToolCall("c3", "browser_click", '{"selector": "text=결제하기"}'), Done("tool_calls")]
END = [TextDelta("done"), Done("stop")]


async def test_allow_then_the_pay_click_goes_through_without_a_second_card():
    session, page = shop()
    await session.page()
    agen, asked = agent_run(session, [PAY, CLICK_PAY, END], [True])
    results = [ev async for ev in agen if isinstance(ev, ToolResult)]
    assert [r.name for r in results] == ["confirm_purchase", "browser_click"]
    assert results[0].approved is True and "APPROVED" in results[0].output and "24,000 KRW" in results[0].output
    assert results[1].approved is None and not results[1].is_error        # covered by the purchase approval
    assert ("click", "text=결제하기") in page.calls
    assert [a.name for a in asked] == ["confirm_purchase"]


@pytest.mark.parametrize("answer", [False, None])   # Deny, or no answer at all
async def test_deny_or_no_answer_means_no_payment(answer):
    session, page = shop()
    await session.page()
    agen, asked = agent_run(session, [PAY, CLICK_PAY, END], [answer] if answer is not None else [])
    results = [ev async for ev in agen if isinstance(ev, ToolResult)]
    assert results[0].approved is False and "did NOT approve" in results[0].output and "Do NOT click pay" in results[0].output
    # a model that clicks pay anyway meets a card of its own (here denied too): nothing was clicked
    assert results[1].approved is False and ("click", "text=결제하기") not in page.calls
    assert [a.name for a in asked] == ["confirm_purchase", "browser_click"]
    assert "the button that confirms a payment, order or booking" in asked[1].detail


async def test_the_purchase_window_is_short_small_and_for_one_site(monkeypatch):
    session, page = shop()
    await session.page()
    session.allow_purchase("shop.example.com", seconds=60, actions=2)
    assert session.purchase_active() and session.use_purchase_action() and session.use_purchase_action()
    assert not session.use_purchase_action()                               # only a few clicks
    session.allow_purchase("shop.example.com", seconds=60, actions=2)
    page.url = "https://elsewhere.example.net/"
    assert not session.purchase_active()                                   # another site is not covered
    page.url = "https://pay.shop.example.com/step2"
    assert session.purchase_active()                                       # the shop's own sub-site is
    now = time.monotonic()
    monkeypatch.setattr(browser_mod.time, "monotonic", lambda: now + 61)
    assert not session.purchase_active()                                   # and only for a short while


async def test_a_js_popup_is_accepted_only_inside_an_allowed_purchase():
    session, page = shop()
    await session.page()

    class Dialog:
        def __init__(self, message): self.message, self.answer = message, None
        async def accept(self): self.answer = "accept"
        async def dismiss(self): self.answer = "dismiss"

    d1 = Dialog("Pay 24,000?")
    await page.handlers["dialog"](d1)
    assert d1.answer == "dismiss"
    session.allow_purchase("shop.example.com")
    d2 = Dialog("Pay 24,000?")
    await page.handlers["dialog"](d2)
    assert d2.answer == "accept"
    out = await session.snapshot()
    out = plain(out)
    assert "pop-up: 'Pay 24,000?' -> dismissed" in out and "accepted (inside the purchase the user allowed)" in out


# ---------------------------------------------------------------- browser cards
async def test_browser_cards_are_small_spaced_out_and_say_when_a_purchase_is_running(monkeypatch):
    session, page = shop()
    await session.page()
    shown = []

    async def sink(item):
        shown.append(item)
    tok = ui_sink.set(sink)
    try:
        assert await session.card("사이트 보는 중")
        assert await session.card("페이지 조작 중") is None                 # too soon after the last one
        now = time.monotonic()
        monkeypatch.setattr(browser_mod.time, "monotonic", lambda: now + 2.5)
        session.allow_purchase("shop.example.com")
        assert await session.card("페이지 조작 중")
    finally:
        ui_sink.reset(tok)
    first, second = shown
    assert first["type"] == "browser" and first["url"] == "https://shop.example.com/cart" and first["host"] == "shop.example.com"
    assert first["title"] == "Example Shop - Cart" and first["status"] == "사이트 보는 중"
    assert first["tab"] == "owner"   # the tab it shows: "보기" on the card opens exactly that one (a job's, a run's, this phone's)
    assert first["image"].startswith("data:image/jpeg;base64,") and 0 < first["bytes"] <= 120_000 and first["width"] <= 720
    assert second["status"] == "Purchase in progress"


async def test_a_card_names_the_tab_it_came_from_not_the_phone_that_reads_it():
    from assistant.tools.browser import browser_owner
    session, page = shop()
    shown = []

    async def sink(item):
        shown.append(item)
    tok, own = ui_sink.set(sink), browser_owner.set("task:w1a2b3")
    try:
        await session.page()
        assert await session.card("사이트 보는 중")
    finally:
        browser_owner.reset(own)
        ui_sink.reset(tok)
    assert shown[0]["tab"] == "task:w1a2b3"


async def test_no_card_without_a_listener_or_a_web_page():
    session, page = shop()
    await session.page()
    assert await session.card("x") is None                                 # nobody listening (tests, CLI)
    page.url = "about:blank"
    tok = ui_sink.set(lambda item: None)
    try:
        assert await session.card("x", force=True) is None
    finally:
        ui_sink.reset(tok)


def test_screenshots_are_scaled_and_capped():
    small, w, h = shrink_jpeg(jpeg(1920, 1080))
    assert small is not None and len(small) <= 120_000 and (w, h) == (720, 405)
    data, _, _ = shrink_jpeg(jpeg(1920, 4000, noise=True), cap=20_000)    # a page that never fits goes without a picture
    assert data is None or len(data) <= 20_000


def test_final_button_hint():
    assert looks_final("text=결제하기") and looks_final("#place-order") and looks_final("button:has-text('Book now')")
    assert not looks_final("a#next") and not looks_final("text=상세보기")


# ---------------------------------------------------------------- through the server: events the phone renders
async def test_the_phone_gets_labels_a_browser_card_and_a_purchase_card_over_http():
    session, page = shop()
    tools = [*browser_tools(session), *purchase_tools(session)]
    fn = scripted([ToolCall("c1", "browser_open", '{"url": "https://shop.example.com/cart"}'), Done("tool_calls")],
                  [ToolCall("c2", "confirm_purchase", json.dumps(ORDER)), Done("tool_calls")],
                  [TextDelta("not paid"), Done("stop")])
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host=None)
    async with Running(create_app(s, tools=tools, stream_fn=fn)) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        events = []
        async with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "buy 2 blue mugs"}]}, headers=AUTH) as r:
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    events.append(ev)
                    if ev["type"] == "approval_request":
                        await c.post(f"/api/approvals/{ev['approval_id']}", json={"approve": False}, headers=AUTH)
    started = [e for e in events if e["type"] == "tool_started"]
    assert [(e["name"], e["label"], e["hidden"]) for e in started] == [("browser_open", "Opening a site", False),
                                                                        ("confirm_purchase", "Asking to confirm a payment", False)]
    cards = [e for e in events if e["type"] == "browser"]
    assert cards[0]["id"] == started[0]["id"] and cards[0]["status"] == "Looking at the site" and cards[0]["image"]
    assert cards[1]["id"] == started[1]["id"] and cards[1]["status"] == "Waiting for payment approval"
    ask = next(e for e in events if e["type"] == "approval_request")
    assert ask["name"] == "confirm_purchase" and ask["label"] == "Asking to confirm a payment" and ask["id"] == started[1]["id"]
    assert "Blue mug x2" in ask["detail"] and "24,000 KRW" in ask["detail"]
    res = [e for e in events if e["type"] == "tool_result"]
    assert res[1]["approved"] is False and res[1]["label"] == "Asking to confirm a payment"
    assert not any(c == "click" for c, *_ in page.calls)


async def test_every_tool_the_app_offers_has_a_label(tmp_path):
    """Ask the real app for a tool that does not exist: its error names every tool it has."""
    fn = scripted([ToolCall("c1", "no_such_tool", "{}"), Done("tool_calls")], [TextDelta("ok"), Done("stop")])
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host="browser", home_dir=str(tmp_path / "home"), content_dir=str(CONTENT),
                 db_path=str(tmp_path / "a.db"))
    app = create_app(s, stream_fn=fn)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        async with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "x"}]}, headers=AUTH) as r:
            events = [json.loads(line[6:]) async for line in r.aiter_lines() if line.startswith("data: ")]
    out = next(e for e in events if e["type"] == "tool_result")["output"]
    shown = re.search(r"Available: (.*?)\.( find_tools|$)", out).group(1).split(", ")   # the tools shown now (tools on demand)
    assert "confirm_purchase" in shown and "browser_click" in shown and "find_tools" in shown
    names = [t.name for t in app.state.tools]                                                # and every tool, shown or loadable
    assert set(shown) <= set(names) and "wallet_cards" in names
    assert [n for n in names if n not in tool_labels.LABELS] == []
    assert tool_labels.hidden("read_file") and tool_labels.hidden("list_files") and not tool_labels.hidden("browser_open")
    assert tool_labels.label("something_new") == "Working"
    from assistant import i18n
    tok = i18n.use("ko")
    try:
        assert tool_labels.label("something_new") == "작업 중" and tool_labels.label("confirm_purchase") == "결제 확인 요청"
    finally:
        i18n.reset(tok)


# ---------------------------------------------------------------- what the model reads
def test_the_prompt_describes_this_apps_purchase_path_and_nothing_site_specific():
    text = (CONTENT / "prompts" / "system.md").read_text(encoding="utf-8")
    section = text.split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0]
    for word in ("browser_open", "confirm_purchase", "APPROVED", "one message", "wallet_cards", "browser_fill_card"):
        assert word in section, word
    keys = text.split("**Logins and keys", 1)[1].split("\n\n**", 1)[0]
    assert "card numbers never go through chat" in keys                   # said once, for every secret, not again per task
    assert len(section) < 1600                                             # kept short: a long prompt makes some models wander
    short = Settings(token=TOKEN).system_prompt
    assert "confirm_purchase" in short and "APPROVED" in short


def test_no_site_names_in_what_was_written_for_purchases():
    texts = [(CONTENT / "prompts" / "system.md").read_text(encoding="utf-8").split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0],
             (CONTENT / "skills" / "shopping-and-bookings" / "SKILL.md").read_text(encoding="utf-8"),
             Settings(token=TOKEN).system_prompt.split("Buying or booking", 1)[1],
             by_name(purchase_tools(None))["confirm_purchase"].description]
    for t in texts:
        assert not re.search(r"https?://|www\.|\.co\.kr|\.com\b|로또|복권|lotto|lottery", t, re.I), t[:80]
