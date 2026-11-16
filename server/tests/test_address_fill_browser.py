"""browser_fill_shipping's field finder in a real Chromium (skipped without Playwright's browser): checkout forms shaped the way shops
in Japan, the UK and Germany ask (the US and Korean shapes run end to end in test_order_flow.py). Pages are served from memory under
fake https addresses; nothing goes to the network."""

import pytest

from assistant import address
from assistant.tools.browser import BrowserSession
from assistant.tools.order import fill_values

from test_purchase import PUBLIC

pw = pytest.importorskip("playwright.async_api")

PREFS = "".join(f"<option value='{k}'>{k}</option>" for k in ("北海道", "東京都", "大阪府"))
PAGES = {
    # family / given name boxes, a postal code in two boxes, a prefecture list, a phone in three boxes
    "https://shop.example.com/jp": f"""<form><label>姓 <input name="sei"></label><label>名 <input name="mei"></label>
        <label>郵便番号 <input name="zip1" maxlength="3"></label> - <input name="zip2" maxlength="4" aria-label="郵便番号 下4桁">
        <label>都道府県 <select name="pref"><option value="">選択</option>{PREFS}</select></label>
        <label>市区町村 <input name="city"></label><label>番地 <input name="banchi"></label><label>建物名 <input name="building"></label>
        <label>電話番号 <input name="tel1" maxlength="3"></label><input name="tel2" maxlength="4" aria-label="電話番号2">
        <input name="tel3" maxlength="4" aria-label="電話番号3"><label>メールアドレス <input type="email" name="mail"></label></form>""",
    # one name box, county optional, a phone box that wants the international form, a country list
    "https://shop.example.com/uk": """<form><label>Full name <input id="fn"></label><label>Address line 1 <input id="l1"></label>
        <label>Address line 2 <input id="l2"></label><label>Town/City <input id="town"></label><label>County (optional) <input id="county"></label>
        <label>Postcode <input id="pc"></label><label>Country <select id="ctry"><option value="">Choose</option>
        <option value="GB">United Kingdom</option><option value="IE">Ireland</option></select></label>
        <label>Phone <input id="ph" type="tel" placeholder="+44 7700 900000"></label></form>""",
    # Vorname / Nachname, street with number, PLZ before Ort, a phone with a separate country code list
    "https://shop.example.com/de": """<form><label>Vorname <input name="vorname"></label><label>Nachname <input name="nachname"></label>
        <label>Straße und Hausnummer <input name="strasse"></label><label>Adresszusatz <input name="zusatz"></label>
        <label>PLZ <input name="plz"></label><label>Ort <input name="ort"></label>
        <label>Vorwahl <select name="cc" autocomplete="tel-country-code"><option value="+49">+49</option><option value="+43">+43</option></select></label>
        <label>Telefon <input name="telefon" type="tel"></label><label>Hinweis für den Boten <textarea name="hinweis"></textarea></label></form>""",
}


@pytest.fixture
async def ctx():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context()

        async def serve(route):
            body = PAGES.get(route.request.url.split("?")[0])
            await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8",
                                body=f"<!doctype html><meta charset=utf-8><body>{body or 'not found'}</body>")
        await c.route("https://**/*", serve)
        yield c
        await b.close()


async def filled(ctx, url, prof):
    page = await ctx.new_page()

    async def connect():
        return page
    session = BrowserSession(connect=connect, resolver=PUBLIC)
    await (await session.page()).goto(url)
    result = await session.fill_address(fill_values({**address.clean(prof), "note": prof.get("note", "")}))
    values = dict(await page.eval_on_selector_all("input, select, textarea", "els => els.map(e => [e.name || e.id, e.value])"))
    return result, values


async def test_a_japanese_checkout(ctx):
    result, v = await filled(ctx, "https://shop.example.com/jp", {
        "country": "JP", "name": "山田 太郎", "postal_code": "154-0023", "admin_area": "東京都", "locality": "世田谷区",
        "address_line1": "若林1-2-3", "address_line2": "ハイツ101", "phone": "090-1234-5678"})
    assert (v["sei"], v["mei"]) == ("山田", "太郎")                        # family name first in a CJK name
    assert (v["zip1"], v["zip2"], v["pref"], v["city"]) == ("154", "0023", "東京都", "世田谷区")
    assert (v["banchi"], v["building"]) == ("若林1-2-3", "ハイツ101")
    assert (v["tel1"], v["tel2"], v["tel3"]) == ("090", "1234", "5678") and v["mail"] == ""
    assert not result.missing


async def test_a_uk_checkout(ctx):
    result, v = await filled(ctx, "https://shop.example.com/uk", {
        "country": "GB", "name": "Ann Smith", "address_line1": "1 High Street", "address_line2": "Flat 2", "locality": "London",
        "postal_code": "ec1y 8sy", "phone": "07700 900123"})
    assert (v["fn"], v["l1"], v["l2"], v["town"], v["pc"], v["ctry"]) == ("Ann Smith", "1 High Street", "Flat 2", "London", "EC1Y 8SY", "GB")
    assert v["county"] == "" and v["ph"] == "+447700900123"                # the placeholder asks for the international form


async def test_a_german_checkout(ctx):
    result, v = await filled(ctx, "https://shop.example.com/de", {
        "country": "DE", "name": "Max Muster", "address_line1": "Hauptstraße 1", "address_line2": "3. OG", "locality": "Berlin",
        "postal_code": "10115", "phone": "030 1234567", "note": "Bitte beim Nachbarn abgeben"})
    assert (v["vorname"], v["nachname"], v["strasse"], v["zusatz"], v["plz"], v["ort"]) == (
        "Max", "Muster", "Hauptstraße 1", "3. OG", "10115", "Berlin")
    assert (v["cc"], v["telefon"]) == ("+49", "301234567") and v["hinweis"] == "Bitte beim Nachbarn abgeben"
    assert "delivery note" in result.filled
