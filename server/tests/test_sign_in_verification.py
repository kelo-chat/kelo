"""A sign-in verification page (two-step sign-in: approve on the phone, or type a code), for any site and language: told apart by
structure and words (tools/verification.py), then the user is told plainly what to do through the app's hand-over, the browser step
waits with its time limit, and goes on when the page moves on. Real Chromium on local pages (skipped without Playwright's browser)."""

from __future__ import annotations

import asyncio

import pytest

from assistant.agent import handover
from assistant.agent.fence import plain
from assistant.tools import humanize
from assistant.tools.browser import BrowserSession
from assistant.tools.verification import verification_step

from fixtures.order_harness import public as PUBLIC

pw = pytest.importorskip("playwright.async_api")


# ---------------------------------------------------------------- the reading, without a browser
def probe(text, *, url="https://id.example.com/login/verify", title="", otp=0, short_inputs=0, secret_box=False):
    return {"kelo_check": 1, "text": text, "text_len": len(text), "url": url, "title": title, "otp": otp,
            "short_inputs": short_inputs, "secret_box": secret_box}


@pytest.mark.parametrize("text", [
    "2단계 인증\n카카오톡으로 로그인 확인 요청을 보냈습니다. 휴대폰에서 확인해 주세요.",
    "2-Step Verification\nCheck your phone. We sent a notification to your Pixel. Tap Yes to sign in.",
    "2段階認証\nスマートフォンでログインを承認してください。",
    "Zwei-Faktor-Authentifizierung: Bestätigen Sie die Anmeldung in der App.",
    "两步验证：请在手机上确认登录。",
])
def test_an_approve_on_your_phone_page_in_any_language(text):
    v = verification_step(probe(text))
    assert v is not None and v.step == "approve"


def test_a_code_page_by_its_boxes_or_by_a_short_input_next_to_the_words():
    assert verification_step(probe("Enter the 6-digit code we sent to your phone", otp=1)).step == "code"
    assert verification_step(probe("인증번호를 입력해 주세요. 문자로 보냈어요.", short_inputs=1)).step == "code"
    assert verification_step(probe("Código de verificación", short_inputs=1, url="https://x.example/auth")).step == "code"


def test_pages_that_only_talk_about_it_are_not_taken_for_one():
    article = "How two-step verification protects you. " * 300            # long: an article
    assert verification_step(probe(article)) is None
    assert verification_step(probe("Two-step verification is on", url="https://shop.example.com/products/1", title="Mug")) is None
    assert verification_step(probe("Enter the code", secret_box=True)) is None   # a password box: a sign-in form, not this
    assert verification_step(probe("Welcome back, Kim", otp=0)) is None
    # a sign-in the agent started on this tab makes a short page with the words count even on a plain address
    assert verification_step(probe("We sent a code to your email", url="https://shop.example.com/x", short_inputs=1), signing_in=True).step == "code"


# ---------------------------------------------------------------- on a real page
LOGIN = "https://id.example.com/login"
VERIFY = "https://id.example.com/login/verify"
HOME = "https://id.example.com/home"

PAGES = {
    LOGIN: """<form action="/login/verify" method=get><input name=u><input name=p type=password><button>Sign in</button></form>""",
    VERIFY: """<h1>2-Step Verification</h1><p>We sent a notification to your phone. Tap Yes to sign in.</p>""",
    HOME: """<h1>Welcome</h1><a href="/logout">Sign out</a>""",
}


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
                                body=f"<!doctype html><meta charset=utf-8><title>Account</title><body>{PAGES.get(url, '<p>x</p>')}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()
    humanize.configure(reset=True)


async def session_on(ctx):
    page = await ctx.new_page()

    async def connect():
        return page
    s = BrowserSession(connect=connect, resolver=PUBLIC)
    s.overlay_pause = 0.05
    s.check_poll = 0.1
    s.settle_seconds = 0.1
    return s, page


async def test_a_sign_in_that_needs_approval_on_the_phone_asks_the_user_and_goes_on_when_the_page_moves(ctx):
    s, page = await session_on(ctx)
    await s.open(LOGIN)
    asked = []

    async def hand(info, gone):
        asked.append(info)
        await asyncio.sleep(0.2)
        await page.goto(HOME)          # the user approved on their phone: the site moves on by itself
        assert await asyncio.wait_for(gone(), 5)
        return "solved"
    token = handover.set(hand)
    try:
        result = await s.fill_login("kim", "secret-pw")
    finally:
        handover.reset(token)
    assert [i["kind"] for i in asked] == ["verification"] and asked[0]["step"] == "approve" and asked[0]["site"] == "id.example.com"
    assert asked[0]["name"] == "Approve the sign-in on your phone"
    assert result.outcome == "success" and "past signing in" in result.detail
    text = plain(result.text)
    assert "verification on id.example.com is done" in text and "Welcome" in text


async def test_without_an_app_the_agent_is_told_to_tell_the_user_plainly(ctx):
    s, _page = await session_on(ctx)
    await s.open(LOGIN)
    result = await s.fill_login("kim", "secret-pw")
    assert result.outcome == "code_needed" and "approve the sign-in on their phone" in result.detail
    text = plain(result.text)
    assert "Tell the user plainly now" in text and "approve the sign-in on their phone" in text


async def test_a_verification_left_unanswered_is_not_handed_over_again_on_the_same_page(ctx):
    s, _page = await session_on(ctx)
    await s.open(LOGIN)
    asked = []

    async def hand(info, gone):
        asked.append(info)
        return "expired"
    token = handover.set(hand)
    try:
        first = plain((await s.fill_login("kim", "secret-pw")).text)
        again = plain(await s.snapshot(clear=True))
    finally:
        handover.reset(token)
    assert len(asked) == 1
    assert "Nobody completed the sign-in verification" in first and "waiting for them to approve the sign-in on their phone" in first
    assert "Tell the user plainly now" in again


async def test_a_code_page_found_while_browsing_asks_for_the_code_and_waits_for_the_page_to_move(ctx):
    s, page = await session_on(ctx)
    PAGES["https://id.example.com/otp"] = ("<h1>인증번호 입력</h1><p>문자로 받은 인증번호 6자리를 입력하세요</p>"
                                          "<input autocomplete=one-time-code maxlength=6><button>확인</button>")
    await s.open(HOME)
    asked = []

    async def hand(info, gone):
        asked.append(info)
        await page.goto(HOME)          # the user typed the code in the live view and the site moved on
        assert await asyncio.wait_for(gone(), 5)
        return "solved"
    token = handover.set(hand)
    try:
        out = plain(await s.open("https://id.example.com/otp"))
    finally:
        handover.reset(token)
        PAGES.pop("https://id.example.com/otp", None)
    assert [(i["kind"], i["step"], i["name"]) for i in asked] == [("verification", "code", "Enter the sign-in code on the page")]
    assert "verification on id.example.com is done" in out and "Welcome" in out


# ---------------------------------------------------------------- a verification that moves to another verification step (2026-11-10)
STEP2 = "https://id.example.com/login/verify/phone"


async def test_the_same_verification_step_still_shown_keeps_the_hand_over_open_and_signed_in_ends_it_solved(ctx):
    s, page = await session_on(ctx)
    await s.open(LOGIN)
    seen = {}

    async def hand(info, gone):
        watch = asyncio.ensure_future(gone())
        await asyncio.sleep(1.0)                     # ten readings at check_poll 0.1 s, the same step on screen
        seen["open"] = not watch.done()
        await page.goto(HOME)                        # the user approved: the site signs in
        seen["got"] = await asyncio.wait_for(watch, 5)
        return "solved"
    token = handover.set(hand)
    try:
        result = await s.fill_login("kim", "secret-pw")
    finally:
        handover.reset(token)
    assert seen == {"open": True, "got": True}
    assert result.outcome == "success"


REMEMBER_PAGE = "https://id.example.com/login/remember"


async def test_after_an_approval_a_follow_up_sign_in_page_gives_control_back_once_it_settles(ctx):
    # approved in the app; the site shows "stay signed in on this browser?": not signed in yet, but not the user's step any more
    s, page = await session_on(ctx)
    PAGES[REMEMBER_PAGE] = "<h1>Stay signed in on this browser?</h1><label><input type=checkbox> Yes</label><button>Continue</button>"
    await s.open(LOGIN)
    seen = {}

    async def hand(info, gone):
        await page.goto(REMEMBER_PAGE)
        t0 = asyncio.get_running_loop().time()
        seen["got"] = await asyncio.wait_for(gone(), 5)
        seen["fast"] = asyncio.get_running_loop().time() - t0 < 3.5     # settled (SETTLE_QUIET 2.5 s without change), then back
        return seen["got"]
    token = handover.set(hand)
    try:
        result = await s.fill_login("kim", "secret-pw")
    finally:
        handover.reset(token)
        PAGES.pop(REMEMBER_PAGE, None)
    assert seen == {"got": "progressed", "fast": True}
    assert result.outcome == "unknown" and "NOT finished" in result.detail
    text = plain(result.text)
    assert "sign-in is NOT finished yet" in text and "Stay signed in on this browser?" in text and "is done (" not in text


async def test_another_page_of_the_same_step_kind_is_the_same_request_no_second_card(ctx):
    # approve -> another "approve on your phone" page: one card, the wait goes on until signed in
    s, page = await session_on(ctx)
    PAGES[STEP2] = "<h1>2단계 인증</h1><p>카카오톡으로 로그인 확인 요청을 보냈습니다. 휴대폰에서 확인해 주세요.</p>"
    await s.open(LOGIN)
    asked, got = [], []

    async def hand(info, gone):
        asked.append(info["step"])
        watch = asyncio.ensure_future(gone())
        await page.goto(STEP2)
        await asyncio.sleep(1.0)
        got.append(watch.done())
        await page.goto(HOME)
        got.append(await asyncio.wait_for(watch, 5))
        return "solved"
    token = handover.set(hand)
    try:
        result = await s.fill_login("kim", "secret-pw")
    finally:
        handover.reset(token)
        PAGES.pop(STEP2, None)
    assert asked == ["approve"] and got == [False, True] and result.outcome == "success"


async def test_a_step_of_another_kind_after_the_approval_is_handed_over_again(ctx):
    # approve -> a code page: the user's approval is done ("progressed"), the code is a new step and gets its own card
    s, page = await session_on(ctx)
    CODE_PAGE = "https://id.example.com/login/otp"
    PAGES[CODE_PAGE] = "<h1>Enter the code</h1><p>Enter the 6-digit code we sent by text</p><input autocomplete=one-time-code maxlength=6>"
    await s.open(LOGIN)
    asked, got = [], []

    async def hand(info, gone):
        asked.append(info["step"])
        await page.goto(CODE_PAGE if len(asked) == 1 else HOME)
        got.append(await asyncio.wait_for(gone(), 8))
        return "progressed" if got[-1] == "progressed" else "solved"
    token = handover.set(hand)
    try:
        result = await s.fill_login("kim", "secret-pw")
    finally:
        handover.reset(token)
        PAGES.pop(CODE_PAGE, None)
    assert asked == ["approve", "code"] and got == ["progressed", True]
    assert result.outcome == "success"


def test_the_notes_that_ask_to_tell_the_user_name_the_users_language():
    from assistant import i18n
    from assistant.tools.vault_tools import not_signed_in
    from assistant.tools.verification import Verification, verification_note, waiting_note
    v = Verification("approve", "check your phone")
    token = i18n.use("ko")
    try:
        notes = [waiting_note(v, "id.example.com"), not_signed_in()] + [verification_note(o, v, "id.example.com")
                                                                        for o in ("progressed", "pending", "expired", "skipped", "closed")]
    finally:
        i18n.reset(token)
    assert all("in Korean, the user's language" in n for n in notes)
    assert "no live browser needed" in waiting_note(v, "x.example") and "Take control" in waiting_note(Verification("code", ""), "x")


async def test_a_hand_over_that_ends_while_the_page_still_verifies_is_not_a_sign_in(ctx):
    # the user tapped "done" (an older app), but the site still waits on a verification step: the sign-in is not finished
    s, page = await session_on(ctx)
    PAGES[STEP2] = "<h1>2-Step Verification</h1><p>Check your phone. We sent a notification. Tap Yes to sign in.</p>"
    await s.open(LOGIN)

    async def hand(info, gone):
        await page.goto(STEP2)
        return "done"
    token = handover.set(hand)
    try:
        result = await s.fill_login("kim", "secret-pw")
    finally:
        handover.reset(token)
        PAGES.pop(STEP2, None)
    assert result.outcome == "code_needed"
    assert "is NOT finished" in plain(result.text) and "verification on id.example.com is done" not in plain(result.text)


def test_the_verification_notes_carry_the_remember_this_browser_rule():
    from assistant.tools.verification import REMEMBER, Verification, verification_note, waiting_note
    v = Verification("approve", "check your phone")
    assert REMEMBER in waiting_note(v, "id.example.com") and REMEMBER in verification_note("solved", v, "id.example.com")
    assert "own account" in REMEMBER and "password" in REMEMBER and len(REMEMBER) < 340


# ---------------------------------------------------------------- an option on the verification page itself (2026-11-10)
REMEMBER_HERE = "https://id.example.com/login/verify-remember"


async def test_the_card_goes_out_at_once_and_the_agent_ticks_the_remember_option_while_the_user_approves(ctx):
    """"Don't ask again on this browser" stands on the waiting page and must be chosen before the approval lands (the page moves on
    right after): the card goes out at detection, the agent gets the page's options in the same result, its click on one ticks it,
    and the wait goes on from that click."""
    import time as _time
    from assistant.tools.browser import browser_tools
    s, page = await session_on(ctx)
    PAGES[LOGIN] = PAGES[LOGIN].replace("/login/verify", "/login/verify-remember")
    PAGES[REMEMBER_HERE] = ("<h1>2단계 인증</h1><p>카카오톡으로 로그인 확인 요청을 보냈습니다. 휴대폰에서 확인해 주세요.</p>"
                            "<label><input type=checkbox id=keep> 이 브라우저에서 2단계 인증 사용 안 함</label>")
    await s.open(LOGIN)
    s.sign_ins["owner"] = {"site": "example.com", "at": _time.time(), "task": None}
    asked, ticked = [], []

    async def hand(info, gone):
        asked.append(info["kind"])
        watch = asyncio.ensure_future(gone())
        for _ in range(100):                         # the user opens their phone; the approval lands once the box is ticked
            if await page.is_checked("#keep"):
                break
            await asyncio.sleep(0.05)
        ticked.append(await page.is_checked("#keep"))
        await page.goto(HOME)
        return "solved" if await asyncio.wait_for(watch, 5) is True else "closed"
    token = handover.set(hand)
    try:
        first = await s.fill_login("kim", "secret-pw")
        assert asked == ["verification"] and first.outcome == "code_needed" and first.step == "approve"   # the card is out already
        text = plain(first.text)
        assert "has just been asked" in text and "이 브라우저에서 2단계 인증 사용 안 함" in text and "There is NO code" in text
        sel = '[data-kelo-opt="1"]'
        assert sel in text and s.approve_pending("id.example.com")
        click = {t.name: t for t in browser_tools(s)}["browser_click"]
        allowed = click.auto_approve({"selector": sel})
        assert (await allowed if hasattr(allowed, "__await__") else allowed) is True   # ticking a box of the own sign-in: no card
        after = plain(await s.click(sel))
    finally:
        handover.reset(token)
        PAGES[LOGIN] = PAGES[LOGIN].replace("/login/verify-remember", "/login/verify")
        PAGES.pop(REMEMBER_HERE, None)
    assert asked == ["verification"] and ticked == [True]
    assert "verification on id.example.com is done" in after


async def test_a_verification_page_without_options_is_handed_over_at_once(ctx):
    s, page = await session_on(ctx)
    await s.open(LOGIN)
    asked = []

    async def hand(info, gone):
        asked.append(info["kind"])
        await page.goto(HOME)
        return "solved" if await asyncio.wait_for(gone(), 5) is True else "closed"
    token = handover.set(hand)
    try:
        result = await s.fill_login("kim", "secret-pw")
    finally:
        handover.reset(token)
    assert asked == ["verification"] and result.outcome == "success" and "options" not in plain(result.text)
