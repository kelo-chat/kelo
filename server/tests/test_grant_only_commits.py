"""A commit the owner did not grant asks, whatever their words say (docs/no-word-lists.md, 2026-11-16). The owner's words are never read
for permission: a task permission (request_task_permission, answered with Allow), an approval card answered with "remember" (a rule), or
a schedule's purchase permission covers a step; everything else asks. A first payment on a new site and an implicit order start only from
a schedule's permission. Real Chromium on local pages (skipped without it)."""

from __future__ import annotations

import time

import pytest

from assistant.tools import humanize
from assistant.tools.browser import BrowserSession, browser_tools, target_label
from assistant.tools.order import Orders, order_tools
from assistant.tools.task_permission import TaskGrants
from assistant.workers import current_turn

from fixtures.order_harness import public as PUBLIC
from fake_judge import FakeJudge, by_page_words

pw = pytest.importorskip("playwright.async_api")

TRIAL = "https://gift.example.com/trial"
OTHER = "https://other.example.net/trial"
OAUTH = "https://kauth.example.com/oauth/authorize"
POST = "onclick=\"fetch('/x',{method:'POST'})\""
PAGES = {
    TRIAL: f"<h1>Gift trial</h1><button id=apply {POST}>선물 체험단 신청하기</button><button id=cancel {POST}>신청 취소</button>"
           f"<button id=pay {POST}>결제하기</button>",
    OAUTH: f"<h1>Example Account</h1><p>Sign in to Gift with your account</p><button id=go {POST}>계속하기</button>"
           f"<button id=agree {POST}>동의</button>",
    OTHER: f"<h1>Trial</h1><button id=apply {POST}>Apply now</button>",
}
TITLES = {OAUTH: "Example 계정", TRIAL: "Gift", OTHER: "Trial"}   # 계정: an account page, its "continue" is part of signing in



@pytest.fixture
async def ctx():
    humanize.configure(enabled=False)
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(no_viewport=True)

        async def serve(route):
            url = route.request.url.split("?")[0]
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><title>{TITLES.get(url, 'x')}</title><body>{PAGES.get(url, '<p>ok</p>')}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


@pytest.fixture
def turn():
    """A chat turn of task 7 whose owner wrote the words of an apply: the words must not change any decision."""
    tok = current_turn.set({"task_id": 7, "turn_id": "t1", "source": "chat", "unattended": False,
                            "request": "카카오 체험단 신청해줘 and buy it"})
    yield
    current_turn.reset(tok)


async def setup(ctx, grants: TaskGrants | None = None):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    s.check_poll = 0.1
    s.settle_seconds = 0.1
    s.sign_in_wait = 0.1
    s.step_judge = FakeJudge(by_page_words)
    tools = {t.name: t for t in browser_tools(s, grants=grants, orders=Orders(scope=lambda: "turn:t1"))}
    return s, page, tools["browser_click"]


async def asks(click, selector: str) -> bool:
    got = click.auto_approve({"selector": selector})
    if hasattr(got, "__await__"):
        got = await got
    return not got


async def test_the_owners_words_alone_do_not_cover_an_apply_click(ctx, turn):
    s, _page, click = await setup(ctx)
    await s.open(TRIAL)
    assert await asks(click, "#apply")                              # no grant: the card, whatever the words say


async def test_a_task_permission_covers_its_apply_click_on_its_site(ctx, turn):
    grants = TaskGrants(alive=lambda s: s in {"turn:t1"})
    grants.add(site="gift.example.com", kind="apply", action="체험단 신청", max_count=5, task_id=7)
    s, _page, click = await setup(ctx, grants)
    await s.open(TRIAL)
    assert not await asks(click, "#apply")                          # the Allow the owner gave for this task covers it
    assert grants.active()[0]["used"] == 1


async def test_a_task_permission_does_not_cover_paying_or_another_site(ctx, turn):
    grants = TaskGrants(alive=lambda s: s in {"turn:t1"})
    grants.add(site="gift.example.com", kind="apply", action="체험단 신청", max_count=5, task_id=7)
    s, _page, click = await setup(ctx, grants)
    await s.open(TRIAL)
    await s.open(OTHER)
    assert await asks(click, "#apply")                              # another site than the one allowed


async def test_consent_for_a_third_party_asks_without_a_grant(ctx, turn):
    s, _page, click = await setup(ctx)
    s.sign_ins["owner"] = {"site": "example.com", "at": time.time(), "task": None}
    await s.open(OAUTH)
    assert await asks(click, "#agree")


async def test_a_plain_continue_inside_the_owners_own_sign_in_goes_on(ctx, turn):
    s, _page, click = await setup(ctx)
    s.sign_ins["owner"] = {"site": "example.com", "at": time.time(), "task": None}
    await s.open(OAUTH)
    assert not await asks(click, "#go")                             # "계속하기" on the same site's OAuth page: part of signing in


async def test_a_plain_continue_needs_no_card_with_or_without_a_sign_in(ctx, turn):
    s, _page, click = await setup(ctx)
    await s.open(OAUTH)
    assert not await asks(click, "#go")                             # the judge says it changes nothing that matters: no word, no sign-in state


def test_the_card_label_is_the_page_words_never_the_selector_text():
    sentence = "text=하단의 '선물 체험단 신청하기' 버튼을 눌러 신청을 완료하세요."
    assert target_label(None, sentence) == "" and target_label(None, "text='Save'") == ""
    assert target_label({"text": "선물 체험단 신청하기", "words": ""}, sentence) == "선물 체험단 신청하기"


def test_the_word_based_paths_are_gone():
    orders = Orders(scope=lambda: "turn:1")
    assert not hasattr(Orders, "covers_request") and not hasattr(orders, "request")
    import assistant.tools.order as order_module
    assert not hasattr(order_module, "requested_kinds") and not hasattr(order_module, "buy_request")


async def test_an_implicit_order_starts_only_from_a_schedule_purchase_permission(tmp_path, turn):
    from assistant.schedule_grants import ConditionalGrants
    from assistant.schedules import Schedules
    from assistant.shipping import ShippingBook
    from assistant.store import Store
    from assistant.wallet import Wallet
    from test_vault import KEY
    store = Store(tmp_path / "a.db", KEY.decode())
    wallet, schedules = Wallet(store), Schedules(store)
    grants = ConditionalGrants(store, schedules, wallet)
    poll = schedules.create("Gift card", "buy the gift card", every_minutes=5, created_by="agent")
    orders = Orders(scope=lambda: "turn:t1", owner=lambda: "tab:1")
    order_tools(orders, None, ShippingBook(store), grants=grants)
    assert orders.start_implicit("gift.example.com") is None        # a chat turn: the owner's words ("...and buy it") start nothing
    grants.add_purchase(poll["id"], "gift.example.com", "gift card", 30000, "KRW", max_count=1)
    tok = current_turn.set({"task_id": 9, "turn_id": "s1", "source": f"schedule:{poll['id']}", "unattended": True})
    try:
        o = orders.start_implicit("gift.example.com")
        assert o is not None and o.site == "example.com" and o.implicit is True
        assert orders.start_implicit("other.example.net") is None   # a permission covers its own site only
    finally:
        current_turn.reset(tok)


def test_a_first_payment_on_a_new_site_asks_without_a_grant(tmp_path):
    from assistant.store import Store
    from assistant.wallet import Wallet
    from test_vault import KEY
    from test_wallet import auto_card
    store = Store(tmp_path / "w.db", KEY.decode())
    wallet = Wallet(store)
    card = auto_card(wallet)
    assert wallet.decide(card, "shop.example.com", 10_000, "KRW").reason == "first_site"
    with pytest.raises(TypeError):                                  # the owner's words no longer reach the rule
        wallet.decide(card, "shop.example.com", 10_000, "KRW", asked_here=True)


async def test_the_permission_card_shows_for_every_request_whatever_the_owners_words(tmp_path):
    from assistant.tools.task_permission import task_permission_tools

    def must_not_read() -> str:
        raise AssertionError("the owner's words are never read for a permission's card")
    grants = TaskGrants(scope=lambda: "turn:t1")
    tool = task_permission_tools(grants, owner_request=must_not_read)[0]
    assert tool.auto_approve is None                                # no auto-approve from words: the card always shows
