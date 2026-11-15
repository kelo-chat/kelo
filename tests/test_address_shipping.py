"""Addresses of any country from open metadata (address.py), shipping profiles (shipping.py) and their API, the address form of an
order (POST /api/shipping-forms), and the pieces of the one-card order judgement (commits.order_step, order.from_address)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from assistant import address, i18n
from assistant.address import AddressError
from assistant.server import create_app
from assistant.shipping import ShippingBook, ShippingError
from assistant.store import Store
from assistant.tools.commits import order_step
from assistant.tools.order import from_address, national_phone

from test_server import AUTH, Running, scripted, settings

ROOT = Path(__file__).resolve().parents[1]


def keys(form):
    return [f["key"] for f in form["fields"]]


def labels(form):
    return [f["label"] for f in form["fields"]]


# ---------------------------------------------------------------- the forms follow the country, labels the user's language
def test_us_form():
    f = address.form("US", "en")
    assert keys(f) == ["name", "address_line1", "address_line2", "locality", "admin_area", "postal_code"]
    assert labels(f) == ["Full name", "Address line 1", "Address line 2 (apt, suite, unit)", "City", "State", "ZIP code"]
    by = {x["key"]: x for x in f["fields"]}
    assert by["admin_area"]["required"] and {"key": "CA", "name": "California"} in by["admin_area"]["options"]
    assert not by["address_line2"]["required"]
    zp = re.compile(by["postal_code"]["pattern"])
    assert zp.match("94043") and zp.match("94043-1351") and not zp.match("9404") and not zp.match("SW1A 1AA")
    assert by["postal_code"]["numeric"] and f["phone"]["calling_code"] == "+1"


def test_kr_form():
    f = address.form("KR", "ko")
    assert keys(f) == ["name", "postal_code", "address_line1", "address_line2"]
    assert labels(f) == ["이름", "우편번호", "주소", "상세주소"]
    assert f["phone"]["calling_code"] == "+82" and f["phone"]["label"] == "전화번호"
    assert [x["required"] for x in f["fields"]] == [True, True, True, False]


def test_jp_form():
    f = address.form("JP", "ja")
    assert keys(f) == ["name", "postal_code", "admin_area", "locality", "address_line1", "address_line2"]
    assert labels(f) == ["氏名", "〒 郵便番号", "都道府県", "市区町村", "番地", "建物名・部屋番号"]
    pref = f["fields"][2]["options"]
    assert len(pref) == 47 and pref[0] == {"key": "北海道", "name": "北海道"}
    assert address.form("JP", "en")["fields"][2]["options"][0] == {"key": "北海道", "name": "Hokkaido"}   # latin names for others
    assert f["phone"]["calling_code"] == "+81"


def test_uk_form():
    f = address.form("GB", "en")
    assert keys(f) == ["name", "address_line1", "address_line2", "locality", "admin_area", "postal_code"]
    by = {x["key"]: x for x in f["fields"]}
    assert by["postal_code"]["label"] == "Postcode" and by["locality"]["label"] == "Town/City"
    assert by["admin_area"]["label"] == "County" and not by["admin_area"]["required"] and "options" not in by["admin_area"]


def test_de_form_puts_the_postal_code_before_the_city():
    f = address.form("DE", "de")
    assert keys(f) == ["name", "address_line1", "address_line2", "postal_code", "locality"]
    assert labels(f)[3:] == ["Postleitzahl", "Stadt"] and f["phone"]["calling_code"] == "+49"


def test_every_country_has_a_form_with_known_labels():
    for c in address.countries():
        f = address.form(c["code"], "en")
        assert f["fields"][0]["key"] == "name" and all(x["label"] for x in f["fields"])
    with pytest.raises(AddressError):
        address.form("ZZZ")


@pytest.mark.parametrize("lang", sorted(i18n.available()))
def test_the_labels_exist_in_every_language(lang):
    for cc in ("US", "KR", "JP", "GB", "DE"):
        f = address.form(cc, lang)
        assert all(x["label"] for x in f["fields"]) and f["labels"]["optional"]


def test_default_country():
    assert address.default_country(timezone="Asia/Seoul") == "KR"
    assert address.default_country(timezone="America/New_York") == "US"
    assert address.default_country(timezone="Europe/Berlin", previous="JP") == "JP"
    assert address.default_country(lang="pt-BR") == "BR" and address.default_country() == "US"


# ---------------------------------------------------------------- checking and writing an address
US = {"country": "US", "name": "Jane Roe", "address_line1": "1600 Amphitheatre Pkwy", "address_line2": "Bldg 43",
      "locality": "Mountain View", "admin_area": "california", "postal_code": "94043", "phone": "(650) 253-0000"}
KR = {"country": "KR", "name": "홍길동", "postal_code": "06236", "address_line1": "서울특별시 강남구 테헤란로 152", "address_line2": "10층",
      "phone": "010-1234-5678"}
JP = {"country": "JP", "name": "山田太郎", "postal_code": "154-0023", "admin_area": "Tokyo", "locality": "世田谷区",
      "address_line1": "若林1-2-3", "address_line2": "ハイツ101", "phone": "090-1234-5678"}
GB = {"country": "GB", "name": "Ann Smith", "address_line1": "1 High Street", "locality": "London", "postal_code": "ec1y 8sy",
      "phone": "020 7946 0000"}
DE = {"country": "DE", "name": "Max Muster", "address_line1": "Hauptstraße 1", "locality": "Berlin", "postal_code": "10115",
      "phone": "030 1234567"}


def test_clean_and_format_in_each_countrys_own_way():
    us = address.clean(US)
    assert us["admin_area"] == "CA" and us["phone"] == "+16502530000" and us["locality"] == "Mountain View"
    assert address.formatted(us).splitlines() == ["Jane Roe", "1600 Amphitheatre Pkwy", "Bldg 43", "MOUNTAIN VIEW, CA 94043"]
    kr = address.clean(KR)
    assert kr["phone"] == "+821012345678"
    assert address.formatted(kr).splitlines() == ["서울특별시 강남구 테헤란로 152", "10층", "홍길동", "06236"]
    jp = address.clean(JP)
    assert jp["admin_area"] == "東京都" and jp["phone"] == "+819012345678"
    assert address.formatted(jp).splitlines() == ["〒154-0023", "東京都世田谷区", "若林1-2-3", "ハイツ101", "山田太郎"]
    gb = address.clean(GB)
    assert gb["postal_code"] == "EC1Y 8SY" and address.formatted(gb, with_name=False).splitlines() == ["1 High Street", "LONDON", "EC1Y 8SY"]
    de = address.clean(DE)
    assert address.formatted(de).splitlines() == ["Max Muster", "Hauptstraße 1", "10115 BERLIN"] and de["phone"] == "+49301234567"
    assert address.one_line(kr) == "서울특별시 강남구 테헤란로 152, 10층, 06236"


@pytest.mark.parametrize("bad,field", [
    ({**US, "postal_code": "9404"}, "postal_code"), ({**US, "admin_area": "Narnia"}, "admin_area"), ({**US, "locality": ""}, "locality"),
    ({**KR, "postal_code": "1234"}, "postal_code"), ({**KR, "address_line1": ""}, "address_line1"), ({**JP, "admin_area": "Atlantis"}, "admin_area"),
    ({**GB, "postal_code": "12345"}, "postal_code"), ({**DE, "postal_code": "1011"}, "postal_code"), ({**DE, "phone": "12"}, "phone"),
    ({**KR, "country": "XX"}, "country"), ({**KR, "address_line2": "card 4111 1111 1111 1111"}, "address_line2"), ({**KR, "phone": ""}, "phone"),
])
def test_bad_addresses_name_their_field(bad, field):
    with pytest.raises(AddressError) as e:
        address.clean(bad)
    assert e.value.field == field


def test_phone_forms():
    assert address.phone_e164("+44 20 7946 0000", "KR") == "+442079460000"
    assert address.phone_e164("0044 20 7946 0000", "KR") == "+442079460000"
    assert national_phone("+821012345678", "KR") == "01012345678" and national_phone("+16502530000", "US") == "6502530000"
    assert national_phone("+819012345678", "JP") == "09012345678"


# ---------------------------------------------------------------- shipping profiles
def test_profiles_default_nicknames_and_logs():
    store = Store(":memory:")
    book = ShippingBook(store)
    home = book.add({**KR, "label": "집", "note": "문 앞"})
    office = book.add({**US, "label": "Office"})
    assert home["default"] and not office["default"] and home["formatted"].startswith("서울특별시")
    assert book.find("") == home and book.find("office") == office and book.find("집") == home and book.find("nope") is None
    assert book.update(office["id"], {"default": True})["default"] and not book.get(home["id"])["default"]
    with pytest.raises(ShippingError):
        book.update(office["id"], {"default": False})
    assert book.update(home["id"], {"postal_code": "06100"})["postal_code"] == "06100"
    with pytest.raises(ShippingError) as e:
        book.update(home["id"], {"postal_code": "abc"})
    assert e.value.field == "postal_code"
    assert book.delete(office["id"]) and book.get(home["id"])["default"]                # the one left takes over
    assert book.last_country() == "KR"
    log = json.dumps(store.vault_log(50), ensure_ascii=False)
    assert "shipping-added" in log and "테헤란로" not in log and "1600" not in log     # nicknames only, never the address


async def test_shipping_api(tmp_path):
    app = create_app(settings(db_path=str(tmp_path / "a.db")), tools=[], stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.get("/api/address/countries")
        assert r.status_code == 200 and {"code": "KR", "name": "South Korea"} in r.json()["countries"] and r.json()["default"]
        r = await c.get("/api/address/form/KR", headers={"Accept-Language": "ko"})
        assert [x["label"] for x in r.json()["fields"]] == ["이름", "우편번호", "주소", "상세주소"]
        r = await c.get("/api/address/form/US", headers={"Accept-Language": "en"})
        assert r.json()["fields"][4]["label"] == "State"
        assert (await c.get("/api/address/form/QQ")).status_code == 404
        r = await c.post("/api/wallet/shipping", json={**KR, "label": "집"})
        assert r.status_code == 201 and r.json()["default"] and r.json()["phone"] == "+821012345678"
        pid = r.json()["id"]
        bad = await c.post("/api/wallet/shipping", json={**US, "postal_code": "x"})
        assert bad.status_code == 422 and "postal_code" in bad.json()["detail"]
        r = await c.post("/api/wallet/shipping", json={**JP, "label": "実家"})
        jp = r.json()["id"]
        assert (await c.patch(f"/api/wallet/shipping/{jp}", json={"default": True})).json()["default"]
        assert [p["label"] for p in (await c.get("/api/wallet/shipping")).json()["profiles"]] == ["実家", "집"]
        assert [p["label"] for p in (await c.get("/api/wallet")).json()["shipping"]] == ["実家", "집"]
        assert (await c.delete(f"/api/wallet/shipping/{jp}")).json() == {"ok": True}
        assert (await c.get(f"/api/wallet/shipping/{pid}")).json()["default"]
        assert (await c.get("/api/wallet/shipping/nope")).status_code == 404
        assert (await c.post("/api/shipping-forms/sf_nope", json={"cancel": True})).status_code == 404
        assert (await c.get("/api/shipping-forms")).json() == {"items": []}


# ---------------------------------------------------------------- the judgement inside an order
@pytest.mark.parametrize("label,url,info,step", [
    ("구매하기", "https://s.example/product/1", {}, True),
    ("주문하기", "https://s.example/cart", {"cart_page": True}, True),
    ("Checkout", "https://s.example/cart", {"cart_page": True}, True),
    ("Buy now", "https://s.example/p/9", {}, True),
    ("다음", "https://s.example/checkout", {}, True),
    ("Continue to payment", "https://s.example/checkout", {}, True),
    ("선택상품 결제하기", "https://s.example/cart", {"cart_page": True}, True),    # opens the checkout from a cart
    ("결제하기", "https://s.example/checkout", {"pay_page": True}, False),
    ("결제하기", "https://s.example/order/form", {}, False),
    ("Place order", "https://s.example/checkout/review", {}, False),
    ("Pay now", "https://s.example/cart", {"cart_page": True}, False),
    ("주문하기", "https://s.example/order", {"pay_page": True}, False),       # the order form asks how to pay: final
    ("Continue", "https://s.example/checkout", {"pay_page": True}, False),
    ("Buy with 1-Click", "https://s.example/p/9", {}, False),
    ("Next", "https://s.example/pay/step2", {}, False),                       # a payment address
    ("Subscribe", "https://s.example/plans", {}, False),
    ("예약하기", "https://s.example/hotel/1", {}, False),                      # bookings always go through confirm_purchase
])
def test_order_step_or_final_payment(label, url, info, step):
    full = {"text": label, "words": "", "url": url, "title": "", **info}
    assert order_step(full, "", url=url) is step


def test_typed_values_must_come_from_the_orders_address():
    prof = {**address.clean(KR), "label": "집", "note": "문 앞에 놓아 주세요"}
    for ok in ("홍길동", "010", "1234", "5678", "010-1234-5678", "01012345678", "06236", "10층", "서울특별시 강남구 테헤란로 152", "문 앞에 놓아 주세요"):
        assert from_address(ok, prof), ok
    for bad in ("부산광역시 해운대구", "someone@example.org", "01099998888", "S3cret-pass"):
        assert not from_address(bad, prof), bad
    assert not from_address("홍길동", None)


def test_the_prompt_names_the_order_tools_and_no_site():
    text = (ROOT / "content" / "prompts" / "system.md").read_text(encoding="utf-8")
    section = text.split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0]
    for word in ("begin_order", "browser_fill_shipping", "ask_shipping", "confirm_purchase"):
        assert word in section
    skill = (ROOT / "content" / "skills" / "shopping-and-bookings" / "SKILL.md").read_text(encoding="utf-8")
    from assistant.tools.order import order_tools, Orders
    tools = order_tools(Orders(owner=lambda: "owner"), None, ShippingBook(Store(":memory:")), object())
    texts = [section, skill, *(t.description for t in tools)]
    for t in texts:
        assert not re.search(r"https?://|www\.|\.co\.kr|\.com\b|coupang|amazon|naver|11st|gmarket", t, re.I), t[:80]
