"""One general flow (2026-11-10, "approval hell" on a Kakao trial application): the user's own request authorizes the commit it names
(apply / enter / sign up / send) on the site the task works on; a plain "continue" inside the owner's own sign-in (its OAuth pages
included) needs no card; paying, deleting, opting out, consent and anything the request does not name keep asking; and the card's
label is the element's words on the page, never the model's selector text. Real Chromium on local pages (skipped without it)."""

from __future__ import annotations

import time

import pytest

from assistant.tools import humanize
from assistant.tools.browser import BrowserSession, browser_tools, target_label
from assistant.tools.order import Orders, requested_kinds

from fixtures.order_harness import public as PUBLIC

pw = pytest.importorskip("playwright.async_api")

TRIAL = "https://gift.example.com/trial"
OAUTH = "https://kauth.example.com/oauth/authorize"
SETTINGS = "https://www.example.com/settings"
OTHER = "https://other.example.net/trial"
POST = "onclick=\"fetch('/x',{method:'POST'})\""
PAGES = {
    TRIAL: f"<h1>Gift trial</h1><p>하단의 '선물 체험단 신청하기' 버튼을 눌러 신청을 완료하세요.</p>"
           f"<button id=apply {POST}>선물 체험단 신청하기</button><button id=cancel {POST}>신청 취소</button>"
           f"<button id=pay {POST}>결제하기</button>",
    OAUTH: f"<h1>Example Account</h1><p>Sign in to Gift with your account</p><button id=go {POST}>계속하기</button>"
           f"<button id=agree {POST}>동의</button>",
    SETTINGS: f"<h1>Account settings</h1><button id=go {POST}>계속하기</button>",
    OTHER: f"<h1>Trial</h1><button id=apply {POST}>Apply now</button>",
}
TITLES = {OAUTH: "Example 계정", SETTINGS: "Account settings", TRIAL: "Gift", OTHER: "Trial"}   # 계정 / settings: a "continue" there is an account step


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


async def setup(ctx, request: str):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    s.check_poll = 0.1
    s.settle_seconds = 0.1
    orders = Orders(scope=lambda: "turn:1")
    orders.request = lambda: request
    used = []
    orders.on_request_use = lambda *a: used.append(a)
    tools = {t.name: t for t in browser_tools(s, orders=orders)}
    return s, page, tools["browser_click"], used


async def asks(click, selector: str) -> bool:
    got = click.auto_approve({"selector": selector})
    if hasattr(got, "__await__"):
        got = await got
    return not got


async def test_the_request_to_apply_covers_the_apply_click_on_its_site_and_nothing_else(ctx):
    s, _page, click, used = await setup(ctx, "카카오 체험단 신청해줘")
    await s.open(TRIAL)
    assert not await asks(click, "#apply")                         # the application the user asked for
    assert used and used[0][1] == "apply" and used[0][2] == "gift.example.com"
    assert await asks(click, "#pay")                               # paying keeps asking
    assert await asks(click, "#cancel")                            # cancelling (a delete) keeps asking
    await s.open(OTHER)
    assert await asks(click, "#apply")                             # another site than the one the task works on


async def test_a_request_that_let_steps_through_stands_on_its_site_through_follow_up_chat_until_a_stop(ctx):
    # 2026-11-12: "브라우저 하는애 자는거같은데" then "너가 하나 직접해보고 …" voided "카카오 체험단 신청해" and the next apply asked
    said = ["카카오 체험단 신청해"]
    stopped = [False]
    s, _page, click, _used = await setup(ctx, "")
    orders = Orders(scope=lambda: "turn:2")
    orders.request = lambda: said[-1]
    orders.stopped_since = lambda ts: stopped[0]
    click = {t.name: t for t in browser_tools(s, orders=orders)}["browser_click"]
    await s.open(TRIAL)
    assert not await asks(click, "#apply")                         # the request itself
    said.append("브라우저 하는애 자는거같은데")
    said.append("너가 하나 직접해보고 지침 다시써봐")
    assert not await asks(click, "#apply")                         # chat about the same work: the request still stands here
    assert await asks(click, "#pay")                               # never a payment
    stopped[0] = True                                              # "그만해"
    assert await asks(click, "#apply")
    stopped[0] = False
    assert await asks(click, "#apply")                             # a stop ended it for good


async def test_a_request_that_does_not_name_the_commit_keeps_the_card(ctx):
    for request in ("체험단 신청 방법 알려줘", "카카오 선물하기 페이지 열어줘", "don't apply, just look"):
        s, _page, click, _used = await setup(ctx, request)
        await s.open(TRIAL)
        assert await asks(click, "#apply"), request


def test_requested_kinds_in_several_languages():
    assert "apply" in requested_kinds("Apply for the gift trial") and "apply" in requested_kinds("体験団に応募して")
    assert "apply" in requested_kinds("이벤트 응모해 줘") and "send" in requested_kinds("이 댓글 올려줘")
    assert "account" in requested_kinds("sign me up for the newsletter site") and "account" in requested_kinds("회원가입 해줘")
    assert requested_kinds("신청 내역 보여줘") == set() and requested_kinds("가격 알려줘") == set()
    assert "pay" not in requested_kinds("결제까지 신청해 줘")


async def test_a_plain_continue_inside_the_owners_own_sign_in_does_not_ask_but_consent_does(ctx):
    s, _page, click, _used = await setup(ctx, "카카오 체험단 신청해줘")
    s.sign_ins["owner"] = {"site": "example.com", "at": time.time(), "task": None}
    await s.open(OAUTH)
    assert not await asks(click, "#go")                            # "계속하기" on the same site's OAuth page: part of signing in
    assert await asks(click, "#agree")                             # consent for a third party keeps its card
    await s.open(SETTINGS)
    assert await asks(click, "#go")                                # past the sign-in, a settings page's "continue" asks


async def test_without_a_sign_in_on_the_tab_the_oauth_continue_asks(ctx):
    s, _page, click, _used = await setup(ctx, "카카오 선물하기 열어줘")
    await s.open(OAUTH)
    assert await asks(click, "#go")


def test_the_card_label_is_the_page_words_never_the_selector_text():
    sentence = "text=하단의 '선물 체험단 신청하기' 버튼을 눌러 신청을 완료하세요."
    assert target_label(None, sentence) == "" and target_label(None, "text='Save'") == ""
    assert target_label({"text": "선물 체험단 신청하기", "words": ""}, sentence) == "선물 체험단 신청하기"


# ---------------------------------------------------------------- the owner's ongoing request across follow-ups (2026-11-10)
def test_a_follow_up_carries_the_request_on_and_a_new_subject_or_a_stop_ends_it():
    from assistant.tools.order import ongoing_request
    chain = ["pc웹으로 다시해봐", "알아서 신청해", "카카오 체험단 전부 신청해"]          # newest first
    assert "apply" in requested_kinds(ongoing_request(chain))
    assert ongoing_request(["pc웹으로 다시해봐", "오늘 날씨 어때", "카카오 체험단 전부 신청해"]) == ""   # a new subject in between
    assert ongoing_request(["신청하지 마", "카카오 체험단 전부 신청해"]) == ""                        # a stop
    assert ongoing_request(["다시 해봐", "신청하지 마", "카카오 체험단 전부 신청해"]) == ""
    assert ongoing_request(["try again", "Apply for the gift trial [Not from the user: a note]"]) != ""


def test_the_orders_request_reads_the_recent_user_messages(tmp_path):
    from assistant.shipping import ShippingBook
    from assistant.store import Store
    from assistant.tools.order import order_tools
    from assistant.workers import current_turn
    store = Store(tmp_path / "a.db")
    for text in ("카카오 체험단 전부 신청해", "알아서 신청해", "pc웹으로 다시해봐"):
        store.add_message("user", text)
        store.add_message("assistant", "네")
    orders = Orders(scope=lambda: "turn:3")
    order_tools(orders, None, ShippingBook(store))
    token = current_turn.set({"request": "pc웹으로 다시해봐", "task_id": 3})
    try:
        assert "apply" in requested_kinds(orders.request())
        store.add_message("user", "오늘 날씨 어때")
        store.add_message("user", "pc웹으로 다시해봐")
        assert orders.request() == ""
    finally:
        current_turn.reset(token)


# ---------------------------------------------------------------- a background job the chat handed the work to (2026-11-10, 0.9.0+8)
click_store: list = []
SENTENCE = "하단의 '선물 체험단 신청하기' 버튼을 눌러 신청을 완료하세요."
JOB_PAGE = "https://gift.example.com/job"


async def job_setup(ctx, tmp_path, chat: list[str]):
    import sqlite3

    from assistant.shipping import ShippingBook
    from assistant.store import Store
    from assistant.tools.order import order_tools
    from assistant.workers import SCHEMA as WORKERS_SCHEMA
    PAGES[JOB_PAGE] = (f"<h1>선물 체험단</h1><p>{SENTENCE}</p>"
                       f"<button id=apply {POST}>선물 체험단 신청하기</button><button id=pay {POST}>결제하기</button>")
    store = Store(tmp_path / "a.db")
    with store._lock:
        store._db.executescript(WORKERS_SCHEMA)
        store._db.commit()
    for text in chat:
        store.add_message("user", text)
    now = time.time()
    store._run("INSERT INTO workers(id, kind, title, task, status, parent_source, tab, created_at, updated_at, max_steps, deadline_at, "
               "minutes, token_cap) VALUES('wjob1','browser','apply','Apply to every trial on the list','running','chat','task:wjob1',?,?,"
               "80,?,30,100000)", (now, now, now + 1800))
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    orders = Orders()
    order_tools(orders, s, ShippingBook(store))
    tools = {t.name: t for t in browser_tools(s, orders=orders)}
    click_store[:] = [store]
    return s, tools["browser_click"]


async def test_a_job_the_chat_handed_off_carries_the_owners_request_for_every_item_and_plain_text_is_no_card(ctx, tmp_path):
    from assistant.workers import current_turn, current_worker
    s, click = await job_setup(ctx, tmp_path, ["카카오 체험단 전부 신청해"])
    t1, t2 = current_worker.set("wjob1"), current_turn.set({"request": "Apply to every trial on the list", "task_id": 9})
    try:
        await s.open(JOB_PAGE)
        assert not await asks(click, f"text={SENTENCE}")                      # the page's sentence: nothing to press, no card
        assert target_label(s.judged_info(f"text={SENTENCE}"), f"text={SENTENCE}") == ""   # and never a card label
        for _ in range(100):                                                   # no count limit on what the owner asked for
            assert not await asks(click, "#apply")
        assert await asks(click, "#pay")                                       # paying still asks
    finally:
        current_worker.reset(t1)
        current_turn.reset(t2)
        PAGES.pop(JOB_PAGE, None)


async def test_a_job_whose_chat_never_asked_for_it_asks(ctx, tmp_path):
    from assistant.workers import current_turn, current_worker
    s, click = await job_setup(ctx, tmp_path, ["오늘 날씨 어때"])                 # the brief says "apply"; the owner never did
    t1, t2 = current_worker.set("wjob1"), current_turn.set({"request": "Apply to every trial on the list", "task_id": 9})
    try:
        await s.open(JOB_PAGE)
        assert await asks(click, "#apply")
    finally:
        current_worker.reset(t1)
        current_turn.reset(t2)
        PAGES.pop(JOB_PAGE, None)


async def test_a_handoff_note_the_server_wrote_is_not_the_owners_stop(ctx, tmp_path):
    # 2026-11-11: the job's own handoff ("... blocked. Do not ...", stored as a user-role message) voided "카카오 체험단 신청해" and
    # every later click asked; then the owner's "ㅇㅇ" (go on) changes nothing
    from assistant.workers import HANDOFF_MARK, current_turn, current_worker
    s, click = await job_setup(ctx, tmp_path, ["카카오 체험단 신청해"])
    t1, t2 = current_worker.set("wjob1"), current_turn.set({"request": "Apply to every trial on the list", "task_id": 9})
    try:
        await s.open(JOB_PAGE)
        import time as _t
        store = click_store[0]
        store._run("INSERT INTO messages(role, content, created_at) VALUES('user', ?, ?)",
                   (HANDOFF_MARK + "\nThe background job wjob1 reported: blocked. Do not start it again; ask the user.", _t.time() + 1))
        store._run("INSERT INTO messages(role, content, created_at) VALUES('user', 'ㅇㅇ', ?)", (_t.time() + 2,))
        assert not await asks(click, "#apply")
    finally:
        current_worker.reset(t1)
        current_turn.reset(t2)
        PAGES.pop(JOB_PAGE, None)


async def test_a_stop_said_after_the_job_started_ends_the_owners_request(ctx, tmp_path):
    from assistant.workers import current_turn, current_worker
    s, click = await job_setup(ctx, tmp_path, ["카카오 체험단 신청해"])
    t1, t2 = current_worker.set("wjob1"), current_turn.set({"request": "Apply to every trial on the list", "task_id": 9})
    try:
        await s.open(JOB_PAGE)
        assert not await asks(click, "#apply")
        import time as _t
        store = click_store[0]
        store._run("INSERT INTO messages(role, content, created_at) VALUES('user', '그만 신청해', ?)", (_t.time() + 1,))
        assert await asks(click, "#apply")
    finally:
        current_worker.reset(t1)
        current_turn.reset(t2)
        PAGES.pop(JOB_PAGE, None)
