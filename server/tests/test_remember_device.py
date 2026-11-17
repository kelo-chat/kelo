"""A watcher whose page turned into a sign-in page is no match. (The keep-signed-in answer needs no word check any more: a step whose
judge flags are all false goes on, docs/click-judge.md; its tests are in test_click_judge.py.)"""

from __future__ import annotations


from assistant.signin_state import sign_in_address
from assistant.watchers import Page

from test_watch_levels import setup as watch_setup, Looker

# ---------------------------------------------------------------- a watcher that lands on a sign-in page
SHOP = "https://shop.example.com/p/1"
LOGIN = "https://id.example.com/nidlogin.login?url=https%3A%2F%2Fshop.example.com%2Fp%2F1"


def test_sign_in_address_is_read_by_structure_not_by_path_words():
    # (hc-pagefields: the path words "signin" / "login" decide nothing any more; the old case "/account/signin" is not a sign-in by itself)
    assert sign_in_address(LOGIN)                                                                  # the page to return to travels in the query
    assert sign_in_address("https://id.example.com/o/authorize?client_id=a1&response_type=code&redirect_uri=https%3A%2F%2Fapp.example.com%2Fcb")
    assert sign_in_address("https://id.example.com/start#next=https://shop.example.com/p/1")
    assert not sign_in_address("https://x.example.com/account/signin") and not sign_in_address("https://shop.example.com/login/p/1")
    assert not sign_in_address(SHOP) and not sign_in_address("https://login.example.com/products/1")


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
        assert res["error"] == "redirected to a sign-in page" and "shop.example.com" in res["detail"] and "재입고" in res["detail"]
    assert fired == [] and human == []
    assert w.get(wid)["fire_count"] == 0


async def test_a_redirect_to_a_page_with_a_password_box_is_a_sign_in_page_whatever_its_address_says():
    plain_address = "https://id.example.com/gate"
    form = "<html><body><form><input name=a><input type=password name=b></form></body></html>"
    store, clock, w, fired, human = watch_setup(Looker(Page(SHOP, 200, "text/html", "<html><body>sold out</body></html>"),
                                                        Page(plain_address, 200, "text/html", form)))
    wid = w.create(name="restock", url=SHOP, mode="text_disappears", match="sold out", every_minutes=10)["id"]
    await w.dry_run(wid)
    w.enable(wid)
    clock.t += 700
    res = await w.check(wid)
    assert res["ok"] is False and res["signed_out"] is True and fired == []


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
