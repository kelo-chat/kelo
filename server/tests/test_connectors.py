"""The Connectors screen rows (connectors.py): the catalog in code, featured worldwide services, and regional services featured for
devices in their region."""

from assistant import i18n
from assistant.catalog import BY_ID, CATALOG
from assistant.connect_ways import ways
from assistant.connectors import FEATURED, connector_rows, list_connectors, regions_for
from assistant.mail import Mail
from assistant.presets import BROWSER, UNSUPPORTED
from assistant.store import Store

REMOVED = {"onepassword", "haveibeenpwned", "kahoot", "padlet", "oreilly", "splice", "tomorrow_io", "turkish_airlines", "coindesk", "morningstar",
           "craft", "whimsical", "cal_com", "roam_research", "sonos", "tally"}
KOREAN = {"naver", "kakao", "coupang", "baemin", "toss", "daangn"}


def table(browser=True):
    s = Store()
    return ways(s, Mail(s), browser=browser)


def test_every_catalog_service_has_a_way_to_connect_or_a_reason_and_an_english_line():
    t = table()
    assert set(BY_ID) == set(t) and len(CATALOG) == len(BY_ID) == 184
    assert not REMOVED & set(BY_ID) and KOREAN <= set(BY_ID) and KOREAN <= set(BROWSER)
    assert all(s.description and len(s.description) <= 80 and not s.description.endswith(".") for s in CATALOG)
    assert len(FEATURED) == 38 and set(FEATURED) <= set(BY_ID)


def test_regions_come_from_the_header_or_the_stored_language():
    assert regions_for("ko-KR,ko;q=0.9,en-US;q=0.8") == {"KR", "US"}
    assert regions_for("ko") == {"KR"} and regions_for("en") == set() and regions_for("de-AT") == {"AT", "DE"}
    tok = i18n.use("ko")
    try:
        assert regions_for(None) == {"KR"}
    finally:
        i18n.reset(tok)
    assert regions_for(None) == set()


def test_worldwide_featured_first_then_the_devices_region():
    rows = {r["id"]: r for r in connector_rows(table(), regions=set())}
    assert not set(UNSUPPORTED) & set(rows)
    assert rows["gmail"]["featured"] and rows["gmail"]["rank"] == 0 and rows["deepl"]["rank"] == 37 and rows["imap_email"]["rank"] == 5
    assert not KOREAN & set(rows)   # sites the agent only opens in its browser are not shown (catalog.HIDDEN_WAYS)
    kr = {r["id"]: r for r in connector_rows(table(), regions={"KR"})}
    assert not KOREAN & set(kr)
    assert "kakao_playmcp" not in kr and "kakao_playmcp" not in rows   # hidden (catalog.HIDDEN): Kakao only accepts approved apps
    assert kr["gmail"]["rank"] == 0
    assert "dropbox" not in kr and "grab_mcp" not in kr and "shopback_mcp" not in kr   # catalog.HIDDEN


def test_regional_names_and_lines_are_translated():
    tok = i18n.use("ko")
    try:
        kr = {r["id"]: r for r in connector_rows(table(), regions={"KR"})}
        names = {r["id"]: r for r in list_connectors()}
    finally:
        i18n.reset(tok)
    assert names["daangn"]["name"] == "당근" and names["baemin"]["name"] == "배달의민족" and names["naver"]["description"].startswith("검색, 메일")
    assert kr["github"]["name"] == "GitHub" and kr["github"]["category"] == "서비스"


def test_without_the_browser_the_website_services_are_not_listed():
    rows = {r["id"] for r in connector_rows(table(browser=False), regions={"KR"})}
    assert not KOREAN & rows and "gmail" in rows
