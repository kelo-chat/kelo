"""One order, one card (tools/order.py): Kelo's real tools against a fake shop served on this machine (tests/fixtures/fake_shop.py),
driven by a scripted model. Each scenario counts the Allow/Deny cards the user would see. No real site, no real money, no model API.

Skipped when Playwright's Chromium is not installed."""

from __future__ import annotations

import time

import pytest

from assistant.schedule_grants import ScheduleGrants
from assistant.store import Store
from assistant.tools import order as order_mod

from fixtures.fake_shop import Shop, ShopServer, browser_args
from fixtures.order_harness import (CVC, KR, KR_ITEMS, KR_NEXT, KR_SAVED, KR_SAVED_TEXT, OTHER, US, VISA, Env, kr_pay, kr_to_checkout,
                                    sign_in_directly, us_pay, us_to_checkout, US_NEXT)

pw = pytest.importorskip("playwright.async_api")

SAVED = {"label": "집", "text": "홍길동 010-1234-5678 서울특별시 강남구 테헤란로 152 10층"}
HOME_KR = {"country": "KR", "label": "집", "name": "홍길동", "phone": "010-1234-5678", "postal_code": "06236",
           "address_line1": "서울특별시 강남구 테헤란로 152", "address_line2": "10층 1004호", "note": "문 앞에 놓아 주세요"}
OFFICE_US = {"country": "US", "label": "Office", "name": "Jane Roe", "phone": "(650) 253-0000", "address_line1": "1600 Amphitheatre Pkwy",
             "address_line2": "Bldg 43", "locality": "Mountain View", "admin_area": "California", "postal_code": "94043"}


@pytest.fixture(scope="module")
def shop():
    s = Shop()
    server = ShopServer(s).start()
    s.port = server.port
    yield s
    server.stop()


@pytest.fixture
async def ctx(shop):
    shop.reset()
    shop.readonly_postcode = False
    shop.accounts.clear()
    shop.accounts["kim"] = {"password": "pw-kim-1", "address": dict(SAVED)}
    shop.accounts["lee"] = {"password": "pw-lee-1", "address": None}
    shop.accounts["jane"] = {"password": "pw-jane-1", "address": None}
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True, args=browser_args(shop.port))
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(locale="ko-KR", ignore_https_errors=True)
        yield c
        await b.close()


def e2e_card(wallet, label="생활비 카드"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return wallet.add_card({"label": label, "exp_month": now.month, "exp_year": now.year + 3, "holder": "HONG GILDONG", "auto_pay": False,
                            "last4": "4242", "brand": "visa", "e2e": {"blob": "QUJDREVGR0hJSktMTU5PUA==", "alg": "AES-256-GCM", "key_id": "k1"}})


def auto_card(wallet, label="생활비 카드"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return wallet.add_card({"label": label, "exp_month": now.month, "exp_year": now.year + 3, "holder": "HONG GILDONG", "auto_pay": True,
                            "number": VISA, "cvc": CVC})


async def env_for(ctx, *, signed_in: str = "", base: str = KR, **kw) -> Env:
    store = Store(":memory:")
    page = await ctx.new_page()
    if signed_in:
        await sign_in_directly(page, base, signed_in, {"kim": "pw-kim-1", "lee": "pw-lee-1", "jane": "pw-jane-1"}[signed_in])
    return Env(store, page, **kw)


def placed(shop, host="www.mugshop.example"):
    return [o for o in shop.orders if o["host"] == host]


COUNTS: dict[str, int] = {}


# ================================================================ the scenarios of the card-count table
async def test_signed_in_saved_address_auto_pay_off_is_one_card(ctx, shop):
    env = await env_for(ctx, signed_in="kim")
    e2e_card(env.wallet)
    run = await env.run([*kr_to_checkout("after"), KR_SAVED, KR_NEXT, *kr_pay("after", ship_to=KR_SAVED_TEXT)])
    assert run.card_tools() == ["confirm_purchase"]
    COUNTS["kr_saved_autopay_off"] = len(run.cards)
    card = run.cards[0].extra
    pay = card["payment"]
    # the one card shows it all: items with options and quantity, total, the address, the card, the site
    assert pay["items"] == [{"name": "블루 머그", "options": "색상: 화이트", "quantity": 2, "price_text": "24,000원"}]
    assert pay["amount_text"] in ("24,000원", "24,000 KRW") and pay["site"] == "mugshop.example"
    assert pay["shipping"]["source"] == "site" and "테헤란로" in pay["shipping"]["formatted"]
    assert card["card"]["label"] == "생활비 카드" and card["card"]["last4"] == "4242"
    assert "Ship to" in run.cards[0].detail or "배송지" in run.cards[0].detail
    [o] = placed(shop)
    assert o["cart"] == [{"item": "블루 머그", "color": "화이트", "qty": 2}] and o["shipping"] == {"saved": SAVED["text"]}
    assert o["card_last4"] == "4242"
    # the steps that only prepared the order went through as part of it and were logged
    assert ("browser_click", "pay", "www.mugshop.example") in env.logged
    assert all(not r.is_error for r in run.results), [(r.name, r.output[:200]) for r in run.results if r.is_error]


async def test_auto_pay_within_limits_is_no_card_only_a_notice(ctx, shop):
    env = await env_for(ctx, signed_in="kim")
    auto_card(env.wallet)
    env.wallet.mark_site("www.mugshop.example")   # paid here before: auto-pay may go on its own within the limits
    run = await env.run([*kr_to_checkout("after"), KR_SAVED, KR_NEXT, *kr_pay("after", ship_to=KR_SAVED_TEXT)])
    assert run.cards == []
    COUNTS["kr_saved_autopay_on"] = 0
    notice = [u for u in run.ui if u.get("type") == "payment_auto"]
    assert len(notice) == 1 and notice[0]["payment"]["items"][0]["quantity"] == 2
    assert notice[0]["payment"]["shipping"]["source"] == "site"
    assert len(placed(shop)) == 1


async def test_new_site_address_typed_from_the_profile_is_one_card(ctx, shop):
    env = await env_for(ctx, signed_in="lee")
    e2e_card(env.wallet)
    env.book.add(HOME_KR)
    run = await env.run([*kr_to_checkout("after"), ("browser_fill_shipping", {}), KR_NEXT, *kr_pay("after")])
    assert run.card_tools() == ["confirm_purchase"]
    COUNTS["kr_profile_typed"] = len(run.cards)
    ship = run.cards[0].extra["payment"]["shipping"]
    assert ship["source"] == "profile" and ship["label"] == "집" and ship["name"] == "홍길동" and ship["phone"] == "+821012345678"
    assert ship["formatted"].splitlines()[0] == "서울특별시 강남구 테헤란로 152"
    [o] = placed(shop)
    assert o["shipping"] == {"receiver": "홍길동", "phone": "010-1234-5678", "zipcode": "06236", "addr1": "서울특별시 강남구 테헤란로 152",
                             "addr2": "10층 1004호", "memo": "문 앞에 놓아 주세요"}
    filled = run.result("browser_fill_shipping").output
    assert "name" in filled and "postal code" in filled and "phone" in filled


async def test_not_signed_in_with_a_saved_login_that_asks(ctx, shop):
    env = await env_for(ctx)
    e2e_card(env.wallet)
    env.book.add(HOME_KR)
    env.store.save_credential("mugshop", "머그 가게", ["www.mugshop.example"], {}, "", kind="login")
    env.store.set_login("mugshop", "kim", "pw-kim-1")
    run = await env.run([*kr_to_checkout("after"), ("browser_login", {"name": "mugshop"}), KR_SAVED, KR_NEXT,
                         *kr_pay("after", ship_to=KR_SAVED_TEXT)])
    assert run.card_tools() == ["browser_login", "confirm_purchase"]
    COUNTS["kr_login_asks"] = len(run.cards)
    assert "your order" in run.cards[0].detail or "주문" in run.cards[0].detail   # the login card says it is for this order
    assert len(placed(shop)) == 1


async def test_not_signed_in_with_an_auto_allowed_login_is_one_card(ctx, shop):
    env = await env_for(ctx)
    e2e_card(env.wallet)
    env.store.save_credential("mugshop", "머그 가게", ["www.mugshop.example"], {}, "", kind="login")
    env.store.set_login("mugshop", "kim", "pw-kim-1")
    env.store.set_auto_allow("mugshop", True)
    run = await env.run([*kr_to_checkout("after"), ("browser_login", {"name": "mugshop"}), KR_SAVED, KR_NEXT,
                         *kr_pay("after", ship_to=KR_SAVED_TEXT)])
    assert run.card_tools() == ["confirm_purchase"]
    COUNTS["kr_login_auto"] = len(run.cards)


async def test_a_sign_up_still_asks_for_its_own_steps(ctx, shop):
    env = await env_for(ctx)
    e2e_card(env.wallet)
    env.book.add(HOME_KR)
    signup = [("browser_click", {"selector": "a:has-text('회원가입')"}),
              ("browser_type", {"selector": "input[name=email]", "text": "new.user@example.org"}),
              ("browser_type", {"selector": "input[name=password]", "text": "S3cret-pass"}),
              ("browser_click", {"selector": "button:has-text('가입하기')"})]
    run = await env.run([*kr_to_checkout("after"), *signup, ("browser_fill_shipping", {}), KR_NEXT, *kr_pay("after")])
    assert run.card_tools() == ["browser_type", "browser_type", "browser_click", "confirm_purchase"]
    COUNTS["kr_signup"] = len(run.cards)
    assert shop.signups == ["new.user@example.org"] and len(placed(shop)) == 1


def form_answer(save=True, label="집", payment=None, address=None):
    def answer(ev):
        return {"address": address or {k: v for k, v in HOME_KR.items() if k != "label"}, "save": save, "label": label, "payment": payment}
    return answer


async def test_no_address_anywhere_asks_once_with_a_form_then_one_card(ctx, shop):
    env = await env_for(ctx, signed_in="lee")
    e2e_card(env.wallet)
    env.form_answer = form_answer()
    remembered = {"name": "홍길동", "phone": "010-1234-5678", "address_line1": "서울특별시 강남구 테헤란로 152"}
    run = await env.run([*kr_to_checkout("after"), ("ask_shipping", {"country": "KR", "prefill": remembered}),
                         ("browser_fill_shipping", {}), KR_NEXT, *kr_pay("after")])
    forms = [u for u in run.ui if u.get("type") == "shipping_form"]
    assert len(forms) == 1 and run.card_tools() == ["confirm_purchase"]
    COUNTS["kr_form_then_card"] = len(run.cards)
    f = forms[0]
    assert f["country"] == "KR" and f["prefill"] == remembered and f["save"] is True and f["label"] in ("Home", "집")
    assert f["site"] == "mugshop.example" and f["ask_payment"] is False
    assert "saved as the shipping profile '집'" in run.result("ask_shipping").output
    assert [p["label"] for p in env.book.profiles()] == ["집"]              # saved for next orders (the box was on)
    assert run.cards[0].extra["payment"]["shipping"]["source"] == "profile"
    assert placed(shop)[0]["shipping"]["addr1"] == "서울특별시 강남구 테헤란로 152"


async def test_no_address_and_no_card_one_form_asks_both_then_one_card(ctx, shop):
    env = await env_for(ctx, signed_in="lee")
    env.form_answer = form_answer(save=False, payment="site")
    run = await env.run([*kr_to_checkout("after"), ("ask_shipping", {"country": "KR"}), ("browser_fill_shipping", {}), KR_NEXT,
                         *kr_pay("after", card=None, pay_with="사이트 결제 (무통장 입금)")])
    [f] = [u for u in run.ui if u.get("type") == "shipping_form"]
    assert f["ask_payment"] is True                                     # no usable wallet card: the same form asks how to pay
    assert run.card_tools() == ["confirm_purchase"]
    COUNTS["kr_form_no_card"] = len(run.cards)
    assert "site's own payment way" in run.result("ask_shipping").output
    assert env.book.profiles() == []                                     # "use for next orders" was off: not saved
    assert run.cards[0].extra["payment"]["shipping"]["source"] == "once"


async def test_us_checkout_filled_from_a_us_profile_is_one_card(ctx, shop):
    env = await env_for(ctx, signed_in="jane", base=US)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    env.wallet.add_card({"label": "Everyday card", "exp_month": now.month, "exp_year": now.year + 3, "auto_pay": False, "last4": "4242",
                         "brand": "visa", "e2e": {"blob": "QUJDREVGR0hJSktMTU5PUA==", "alg": "AES-256-GCM", "key_id": "k1"}})
    env.book.add(OFFICE_US)
    run = await env.run([*us_to_checkout("after"), ("browser_click", {"selector": "#apply-coupon"}), ("browser_fill_shipping", {}),
                         US_NEXT, *us_pay("after")])
    assert run.card_tools() == ["confirm_purchase"], [(c.tool, c.detail) for c in run.cards]
    COUNTS["us_profile_typed"] = len(run.cards)
    [o] = placed(shop, "www.usmugs.example")
    assert o["shipping"] == {"ship_name": "Jane Roe", "ship_a1": "1600 Amphitheatre Pkwy", "ship_a2": "Bldg 43", "ship_city": "Mountain View",
                             "ship_state": "CA", "ship_zip": "94043", "ship_country": "US", "ship_phone": "6502530000"}
    ship = run.cards[0].extra["payment"]["shipping"]
    assert ship["formatted"].splitlines()[-1] == "MOUNTAIN VIEW, CA 94043" and ship["phone"] == "+16502530000"


async def test_a_schedule_run_buys_with_the_shipping_profile_chosen_at_setup(ctx, shop):
    store = Store(":memory:")
    grants = ScheduleGrants(store)
    page = await ctx.new_page()
    await sign_in_directly(page, KR, "lee", "pw-lee-1")
    env = Env(store, page, grants=grants, source="schedule:7")
    auto_card(env.wallet)
    prof = env.book.add(HOME_KR)
    g = grants.add_purchase(7, "www.mugshop.example", "블루 머그", 30000, "KRW", shipping_id=prof["id"])
    assert grants.get(g["id"])["shipping_label"] == "집"
    run = await env.run([*kr_to_checkout("after"), ("browser_fill_shipping", {}), KR_NEXT, *kr_pay("after")])
    assert run.cards == []                                                # allowed once at setup: the run asks nothing
    COUNTS["schedule_run"] = 0
    assert "Ship to the profile '집'" in run.result("begin_order").output
    assert placed(shop)[0]["shipping"]["receiver"] == "홍길동"
    auto = [u for u in run.ui if u.get("type") == "payment_auto"]
    assert auto and auto[0]["schedule_grant"]["schedule_id"] == 7


async def test_read_only_postcode_fields_get_their_value_set(ctx, shop):
    shop.readonly_postcode = True
    env = await env_for(ctx, signed_in="lee")
    e2e_card(env.wallet)
    env.book.add(HOME_KR)
    run = await env.run([*kr_to_checkout("after"), ("browser_fill_shipping", {}), KR_NEXT, *kr_pay("after")])
    assert run.card_tools() == ["confirm_purchase"]
    assert placed(shop)[0]["shipping"]["zipcode"] == "06236" and placed(shop)[0]["shipping"]["addr1"].startswith("서울특별시")


# ================================================================ what still asks during an order
def deny_all(req):
    return False


async def test_a_pay_click_on_another_site_during_an_order_asks(ctx, shop):
    env = await env_for(ctx, signed_in="kim")
    run = await env.run([*kr_to_checkout("after"), ("browser_open", {"url": f"{OTHER}/product/1"}),
                         ("browser_click", {"selector": "button:has-text('결제하기')"})], answer=deny_all)
    assert run.card_tools() == ["browser_click"] and not run.cards[0].allowed
    assert placed(shop, "www.othershop.example") == []


async def test_deleting_and_reviewing_and_account_changes_ask(ctx, shop):
    env = await env_for(ctx, signed_in="kim")
    run = await env.run([("browser_open", {"url": f"{KR}/product/1"}), ("begin_order", {"site": "www.mugshop.example", "what": "머그"}),
                         ("browser_type", {"selector": "textarea[name=review]", "text": "좋아요"}),
                         ("browser_click", {"selector": "button:has-text('리뷰 등록')"}),
                         ("browser_click", {"selector": "#buy"}),
                         ("browser_click", {"selector": "button:has-text('삭제')"}),
                         ("browser_open", {"url": f"{KR}/account"}),
                         ("browser_click", {"selector": "button:has-text('정보 수정')"})], answer=deny_all)
    assert run.card_tools() == ["browser_type", "browser_click", "browser_click", "browser_click"]
    assert shop.reviews == []


async def test_typing_a_password_or_an_address_that_is_not_the_orders_asks(ctx, shop):
    env = await env_for(ctx, signed_in="lee")
    env.book.add(HOME_KR)
    run = await env.run([*kr_to_checkout("after"), ("browser_type", {"selector": "input[name=receiver]", "text": "홍길동"}),
                         ("browser_type", {"selector": "input[name=addr1]", "text": "부산광역시 해운대구 센텀로 1"}),
                         ("browser_open", {"url": f"{KR}/login"}),
                         ("browser_type", {"selector": "input[name=password]", "text": "pw-lee-1"})], answer=deny_all)
    # the name is the saved profile's (covered); an address Kelo only remembers and a password ask
    assert run.card_tools() == ["browser_type", "browser_type"]
    assert "부산" in run.cards[0].args["text"] and run.cards[1].args["selector"] == "input[name=password]"


async def test_a_second_payment_in_the_same_order_asks_even_with_auto_pay(ctx, shop):
    env = await env_for(ctx, signed_in="kim")
    auto_card(env.wallet)
    env.wallet.mark_site("www.mugshop.example")
    run = await env.run([*kr_to_checkout("after"), KR_SAVED, KR_NEXT, *kr_pay("after", ship_to=KR_SAVED_TEXT),
                         ("confirm_purchase", {"summary": "블루 머그 1개 더", "amount": 12000, "currency": "KRW", "site": "www.mugshop.example",
                                               "card": "생활비 카드", "items": KR_ITEMS})], answer=deny_all)
    assert run.card_tools() == ["confirm_purchase"]
    assert run.cards[0].extra["card"]["reason"] == "order_again"
    assert len(placed(shop)) == 1


async def test_the_order_ends_after_payment(ctx, shop):
    env = await env_for(ctx, signed_in="kim")
    auto_card(env.wallet)
    env.wallet.mark_site("www.mugshop.example")
    after = [("browser_open", {"url": f"{KR}/product/1"}), ("browser_click", {"selector": "#buy"})]
    run = await env.run([*kr_to_checkout("after"), KR_SAVED, KR_NEXT, *kr_pay("after", ship_to=KR_SAVED_TEXT), *after], answer=deny_all)
    assert run.card_tools() == ["browser_click"]                         # paid: "구매하기" asks again


async def test_the_order_ends_when_the_payment_is_denied(ctx, shop):
    env = await env_for(ctx, signed_in="kim")
    e2e_card(env.wallet)
    again = [("browser_open", {"url": f"{KR}/product/1"}), ("browser_click", {"selector": "#buy"})]
    run = await env.run([*kr_to_checkout("after"), KR_SAVED, KR_NEXT, ("browser_read", {}),
                         ("confirm_purchase", {"summary": "블루 머그 x2", "amount": 24000, "currency": "KRW", "site": "www.mugshop.example",
                                               "card": "생활비 카드"}), *again], answer=deny_all)
    assert run.card_tools() == ["confirm_purchase", "browser_click"]
    assert "order on mugshop.example is closed" in run.result("confirm_purchase").output
    assert placed(shop) == []


async def test_the_order_ends_after_its_time(ctx, shop, monkeypatch):
    env = await env_for(ctx, signed_in="kim")
    start = time.time()
    env.orders.clock = lambda: start
    first = await env.run([("browser_open", {"url": f"{KR}/product/1"}), ("begin_order", {"site": "www.mugshop.example", "what": "머그"}),
                           ("browser_click", {"selector": "#buy"})], turn_id="same")
    assert first.cards == []
    env.orders.clock = lambda: start + order_mod.ORDER_MINUTES * 60 + 1
    later = await env.run([("browser_click", {"selector": "button:has-text('주문하기')"})], answer=deny_all, turn_id="same")
    assert later.card_tools() == ["browser_click"]


async def test_the_order_belongs_to_its_task(ctx, shop):
    env = await env_for(ctx, signed_in="kim")
    await env.run([("browser_open", {"url": f"{KR}/product/1"}), ("begin_order", {"site": "www.mugshop.example", "what": "머그"})],
                  turn_id="one")
    other = await env.run([("browser_click", {"selector": "#buy"})], answer=deny_all, turn_id="two")
    assert other.card_tools() == ["browser_click"]                       # another reply does not ride on that order


async def test_begin_order_refuses_another_site_than_the_page(ctx, shop):
    env = await env_for(ctx)
    run = await env.run([("browser_open", {"url": f"{KR}/product/1"}), ("begin_order", {"site": "www.othershop.example", "what": "x"})])
    assert run.result("begin_order").is_error and "not on othershop.example" in run.result("begin_order").output


def test_card_count_table_is_complete():
    """Runs last in this file: every scenario of the table was measured (the numbers are printed with -s)."""
    want = {"kr_saved_autopay_off": 1, "kr_saved_autopay_on": 0, "kr_profile_typed": 1, "kr_login_asks": 2, "kr_login_auto": 1,
            "kr_signup": 4, "kr_form_then_card": 1, "kr_form_no_card": 1, "us_profile_typed": 1, "schedule_run": 0}
    if not COUNTS:
        pytest.skip("the browser scenarios did not run")
    print("\ncard counts:", COUNTS)
    assert COUNTS == want
