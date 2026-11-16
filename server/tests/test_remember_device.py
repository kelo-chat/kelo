"""2026-11-15 (Naver): after Kelo's own sign-in the site asked "remember this device?"; Kelo picked "don't register" by itself, the owner
declined the card, and Kelo offered the same answer again. Now: the keep-signed-in answer goes without a card (after browser_login, on
that site only), a declined click offers the page's other choices, and a watcher whose page turned into a sign-in page is no match."""

from __future__ import annotations

import time

import pytest

from assistant.signin_state import sign_in_address
from assistant.tools.browser import browser_tools
from assistant.tools.commits import keep_signed_in
from assistant.watchers import Page

from test_requested_commits import PAGES, TITLES, asks, ctx, setup  # noqa: F401  (ctx is the real-Chromium fixture)
from test_watch_levels import setup as watch_setup, Looker

ASK = "자주 사용하는 기기로 등록하시겠습니까?"


# ---------------------------------------------------------------- keep_signed_in
@pytest.mark.parametrize("label", [
    "등록", "기기 등록", "자주 사용하는 기기 등록", "이 기기 기억", "로그인 상태 유지", "저장",
    "Remember this device", "remember me", "Trust this browser", "Don't ask again on this device", "Don't ask again",
    "Stay signed in", "Keep me signed in", "Angemeldet bleiben", "Rester connecté", "このデバイスを記憶する",
])
def test_words_that_keep_the_owner_signed_in(label):
    assert keep_signed_in(label)


@pytest.mark.parametrize("label", [
    "등록안함", "등록 안 함", "안함", "안 함", "나중에", "아니요", "No", "Not now", "Don't remember", "Don't register", "Skip", "Later", "취소",
    "Don't remember this device", "No thanks", "Nein", "いいえ", "",
    "2단계 인증 등록", "비밀번호 저장", "Save password", "Register phone number", "Set up 2-step verification", "Add recovery email",
    "Register payment card", "앱 비밀번호 등록",
])
def test_negative_words_and_other_objects_never_count(label):
    assert not keep_signed_in(label)
    assert not keep_signed_in(label, ASK)


@pytest.mark.parametrize("yes", ["예", "네", "Yes", "OK", "확인"])
def test_a_plain_yes_counts_only_when_the_page_asks_to_remember(yes):
    assert not keep_signed_in(yes) and not keep_signed_in(yes, "Welcome back to the shop")
    assert keep_signed_in(yes, ASK)
    assert keep_signed_in(yes, "Remember this browser?") and keep_signed_in(yes, "Do you want to stay signed in?")
    assert keep_signed_in(yes, "Möchten Sie angemeldet bleiben?")


# ---------------------------------------------------------------- the click decision
REGISTER = "https://id.example.com/device/register"
REGISTER_OTHER = "https://id.example.net/device/register"
_BUTTONS = ("<button id=reg onclick=\"fetch('/x',{method:'POST'})\">등록</button><button id=no onclick=\"fetch('/x',{method:'POST'})\">등록안함</button>"
            "<button id=tfa onclick=\"fetch('/x',{method:'POST'})\">2단계 인증 등록</button>")
PAGES[REGISTER] = f"<h1>{ASK}</h1><p>이 기기에서는 다음에 로그인을 다시 묻지 않아요.</p>{_BUTTONS}"
PAGES[REGISTER_OTHER] = PAGES[REGISTER]
TITLES[REGISTER] = TITLES[REGISTER_OTHER] = "기기 등록"


async def test_remember_this_device_goes_without_a_card_only_after_the_owners_own_sign_in(ctx):
    s, _page, click, _used = await setup(ctx, "네이버에서 상품 알려줘")
    s.sign_ins["owner"] = {"site": "example.com", "at": time.time(), "task": None}
    await s.open(REGISTER)
    assert not await asks(click, "#reg")                           # "등록" on the page that asks it: keeps the owner signed in
    assert await asks(click, "#no")                                # the negative answer keeps today's rules
    assert await asks(click, "#tfa")                               # 2-step verification registration is a security step
    await s.open(REGISTER_OTHER)
    assert await asks(click, "#reg")                               # another site than the one signed in to
    s.sign_ins["owner"] = {"site": "example.com", "at": time.time() - 3600, "task": None}
    await s.open(REGISTER)
    assert await asks(click, "#reg")                               # the sign-in was long ago


async def test_without_a_sign_in_on_the_tab_remember_this_device_asks(ctx):
    s, _page, click, _used = await setup(ctx, "네이버에서 상품 알려줘")
    await s.open(REGISTER)
    assert await asks(click, "#reg")


async def test_a_declined_click_offers_the_pages_other_choices(ctx):
    s, _page, click, _used = await setup(ctx, "네이버에서 상품 알려줘")
    await s.open(REGISTER)
    assert await asks(click, "#no")                                # judged: the card asks
    said = click.declined({"selector": "#no"})
    assert "declined clicking '등록안함'" in said and "quick_replies" in said and "FIRST options" in said
    assert "do not offer '등록안함' again" in said
    assert "Do not retry it; ask what they would like instead" not in said


# ---------------------------------------------------------------- a watcher that lands on a sign-in page
SHOP = "https://shop.example/p/1"
LOGIN = "https://id.example/nidlogin.login?url=https%3A%2F%2Fshop.example%2Fp%2F1"


def test_sign_in_address_reads_the_path_and_query_not_the_host():
    assert sign_in_address(LOGIN) and sign_in_address("https://x.example/account/signin")
    assert not sign_in_address(SHOP) and not sign_in_address("https://login.example/products/1")


async def test_a_watcher_redirected_to_a_sign_in_page_reports_it_and_does_not_fire():
    gone_text = "<html><body>상품 정보</body></html>"          # the login page has no '품절되었습니다'
    store, clock, w, fired, human = watch_setup(Looker(Page(SHOP, 200, "text/html", "<html><body>품절되었습니다</body></html>"),
                                                        Page(LOGIN, 200, "text/html", gone_text)))
    wid = w.create(name="재입고", url=SHOP, mode="text_disappears", match="품절되었습니다", every_minutes=10)["id"]
    await w.dry_run(wid)
    w.enable(wid)
    for _ in range(3):
        clock.t += 700
        res = await w.check(wid)
        assert res["ok"] is False and res["satisfied"] is False and res["signed_out"] is True
        assert res["error"] == "redirected to a sign-in page" and "shop.example" in res["detail"] and "재입고" in res["detail"]
    assert fired == [] and human == []
    assert w.get(wid)["fire_count"] == 0


async def test_the_same_page_without_a_redirect_still_evaluates():
    sold_out, back = "<html><body>품절되었습니다</body></html>", "<html><body>구매하기</body></html>"
    store, clock, w, fired, _human = watch_setup(Looker(Page(SHOP, 200, "text/html", sold_out), Page(SHOP, 200, "text/html", sold_out),
                                                        Page(SHOP, 200, "text/html", back), Page(SHOP, 200, "text/html", back)))
    wid = w.create(name="재입고", url=SHOP, mode="text_disappears", match="품절되었습니다", every_minutes=10)["id"]
    await w.dry_run(wid)
    w.enable(wid)
    clock.t += 700
    first = await w.check(wid)
    assert first["ok"] is True and not first.get("signed_out") and first["satisfied"] is False
    clock.t += 700
    second = await w.check(wid)
    assert second["ok"] is True and second["satisfied"] is True


async def test_a_watcher_whose_own_address_is_a_sign_in_page_still_evaluates():
    store, clock, w, fired, _human = watch_setup(Looker(Page(LOGIN, 200, "text/html", "<html><body>로그인</body></html>")))
    wid = w.create(name="x", url=LOGIN, mode="text_appears", match="로그인", every_minutes=10)["id"]
    res = await w.dry_run(wid)
    assert res["ok"] is True
