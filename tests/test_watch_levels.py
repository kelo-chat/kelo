"""Watchers look lightly first (S68): light / tab / page, escalation when blocked, the human-check pause, the spread of checks, the
confirmation of a hit, a watcher's own request (POST, headers, signed in) and the browser's site tab. Fakes only, no network."""

import random

import httpx
import pytest

from assistant import watchers as watchers_mod
from assistant.browser_identity import user_agent as chrome_user_agent
from assistant.netguard import VPN_DOWN, NetGuardError
from assistant.store import Store
from assistant.tools.browser import SITE_TAB_IDLE_SECONDS, SITE_TABS_MAX, BrowserPolicyError, BrowserSession, is_site_tab, one_run_tab
from assistant.tools.challenge import Challenge
from assistant.watchers import MAX_ERRORS, Page, WatchError, Watchers, action_word, clean_headers, public_get, watch_tools

from test_browser_tools import PUBLIC, FakePage as BrowserFakePage

PRICE = "<html><body><span class='seats'>%s</span></body></html>"
URL = "https://shop.example.com/seats"


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Looker:
    """A fake way of looking: answers from a list (or one answer), records its calls."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def __call__(self, *args, **kw):
        self.calls.append((args, kw))
        got = self.answers[0] if len(self.answers) == 1 else self.answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got


def ok(seats="5", **kw):
    return Page(URL, 200, "text/html", PRICE % seats, **kw)


def setup(light=None, tab=None, page=None, **kw):
    store, clock = Store(), Clock()
    w = Watchers(store, fetch=light, tab_fetch=tab, browser_fetch=page, clock=clock, rng=random.Random(7), **kw)
    fired, human = [], []
    w.on_fire = lambda wt, res: fired.append((wt, res))
    w.on_human_check = lambda wt, words: human.append(words)
    return store, clock, w, fired, human


def make(w, **kw):
    f = {"name": "seats", "url": URL, "mode": "number_above", "selector": ".seats", "threshold": 0, "every_minutes": 10}
    return w.create(**{**f, **kw})


# ---------------------------------------------------------------------------------------------------- the starting level
async def test_the_starting_level_follows_the_fields():
    store, clock, w, *_ = setup(Looker(ok()), Looker(ok()), Looker(ok()))
    start = lambda **kw: w.start_level(w._row(make(w, **kw)["id"]))   # noqa: E731
    assert start() == "light"
    assert start(use_browser=True) == "page"
    assert start(signed_in=True) == "tab"
    assert start(method="POST", body='{"q":"seat"}') == "tab"
    assert start(signed_in=True, use_browser=True) == "tab"       # signed in wins: the owner's data needs the owner's sign-in
    got = {}
    for name, kw in (("light", {}), ("tab", {"signed_in": True}), ("page", {"use_browser": True})):
        lk, tb, pg = Looker(ok()), Looker(ok()), Looker(ok())
        _, _, w2, *_ = setup(lk, tb, pg)
        wid = make(w2, **kw)["id"]
        dry = await w2.dry_run(wid)
        got[name] = (len(lk.calls), len(tb.calls), len(pg.calls), dry["level"])
    assert got == {"light": (1, 0, 0, "light"), "tab": (0, 1, 0, "tab"), "page": (0, 0, 1, "page")}


async def test_a_tab_look_gets_the_method_headers_body_and_the_site_key():
    tb = Looker(ok())
    _, _, w, *_ = setup(Looker(ok()), tb)
    wid = make(w, url="https://api.shop.example.co.uk/v1/seats/search", method="POST", body='{"day":"2026-12-01"}',
               headers={"Accept": "application/json", "X-Requested-With": "fetch"}, signed_in=True)["id"]
    await w.dry_run(wid)
    (url, method, headers, body, key), _ = tb.calls[0]
    assert (method, body, headers) == ("POST", '{"day":"2026-12-01"}', {"Accept": "application/json", "X-Requested-With": "fetch"})
    assert key == "task:watch-site:example.co.uk" and url.endswith("/seats/search")


# ---------------------------------------------------------------------------------------------------- escalation
async def test_a_blocked_light_look_goes_up_to_the_tab_and_the_level_is_kept_for_a_day():
    light, tab = Looker(Page(URL, 403, "text/html", "no")), Looker(ok("3"))
    store, clock, w, fired, human = setup(light, tab)
    wid = make(w)["id"]
    dry = await w.dry_run(wid)
    assert dry["ok"] and dry["level"] == "tab" and dry["value"] == "3"
    assert w.get(wid)["level"] == "tab" and w.get(wid)["level_until"] == clock.t + 24 * 3600
    w.enable(wid)
    for _ in range(MAX_ERRORS + 2):                              # a block a higher level got past is no error
        clock.t += 700
        await w.check(wid)
    got = w.get(wid)
    assert got["error_count"] == 0 and got["state"] == "watching" and got["enabled"]
    assert len(light.calls) == 1 and len(tab.calls) == MAX_ERRORS + 3     # the dry run tried light; later looks go straight to the tab
    clock.t += 25 * 3600                                          # a day later it tries its starting level again
    assert w.get(wid)["level"] is None
    await w.check(wid)
    assert len(light.calls) == 2 and w.get(wid)["level"] == "tab"


@pytest.mark.parametrize("body,headers", [
    ("<html><head><title>Just a moment...</title></head></html>", {}),
    ("<script src='https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/orchestrate'></script>", {}),
    ("<html></html>", {"cf-mitigated": "challenge"}),
    ("<html></html>", {"x-amzn-waf-action": "captcha"}),
])
async def test_a_check_page_escalates_even_with_status_200(body, headers):
    light, tab = Looker(Page(URL, 200, "text/html", body, headers)), Looker(ok())
    _, _, w, *_ = setup(light, tab)
    dry = await w.dry_run(make(w)["id"])
    assert dry["ok"] and dry["level"] == "tab" and len(tab.calls) == 1


async def test_light_then_tab_then_page_and_a_post_stops_at_the_tab():
    light, tab, page = Looker(Page(URL, 429, "", "")), Looker(Page(URL, 403, "", "")), Looker(ok("2"))
    _, _, w, *_ = setup(light, tab, page)
    dry = await w.dry_run(make(w)["id"])
    assert dry["ok"] and dry["level"] == "page" and [len(x.calls) for x in (light, tab, page)] == [1, 1, 1]
    tab2, page2 = Looker(Page(URL, 403, "", "")), Looker(ok())
    _, _, w2, *_ = setup(Looker(ok()), tab2, page2)
    dry = await w2.dry_run(make(w2, method="POST", body="{}")["id"])
    assert not dry["ok"] and "403" in dry["error"] and page2.calls == []      # a page load cannot be a POST


async def test_an_unavailable_level_is_skipped_and_a_plain_error_does_not_escalate():
    _, _, w, *_ = setup(Looker(Page(URL, 403, "", "")), None, Looker(ok()))
    assert (await w.dry_run(make(w)["id"]))["level"] == "page"                 # no tab here: straight to the page
    tab = Looker(ok())
    _, _, w2, *_ = setup(Looker(NetGuardError("the page did not answer in time")), tab)
    dry = await w2.dry_run(make(w2)["id"])
    assert not dry["ok"] and tab.calls == []
    _, _, w3, *_ = setup(Looker(Page(URL, 404, "", "")), tab)
    assert not (await w3.dry_run(make(w3)["id"]))["ok"] and tab.calls == []     # 404 is not a block


async def test_a_vpn_that_is_down_is_an_error_and_never_escalates():
    tab, page = Looker(ok()), Looker(ok())
    store, clock, w, *_ = setup(Looker(NetGuardError(VPN_DOWN)), tab, page)
    wid = make(w)["id"]
    dry = await w.dry_run(wid)
    assert not dry["ok"] and VPN_DOWN in dry["error"] and tab.calls == [] and page.calls == []
    w2 = setup(Looker(Page(URL, 403, "", "")), Looker(WatchError(VPN_DOWN)), page)[2]
    dry = await w2.dry_run(make(w2)["id"])                                       # a tab read that finds the VPN down stops there too
    assert not dry["ok"] and VPN_DOWN in dry["error"] and page.calls == []


# ---------------------------------------------------------------------------------------------------- the human check
async def test_a_human_check_pauses_without_an_error_and_tells_once_a_day():
    check = Challenge("cloudflare", "interstitial")
    pg = Looker(Page(URL, 403, "text/html", "<html></html>", {}, check))
    store, clock, w, fired, human = setup(Looker(ok()), None, pg)
    wid = make(w, use_browser=True, name="좌석")["id"]
    await w.dry_run(wid)
    store._run("UPDATE watchers SET dry_run = ? WHERE id = ?", ('{"ok": true, "satisfied": false}', wid))
    w.enable(wid)
    gaps = []
    for _ in range(4):
        before = clock.t
        res = await w.check(wid)
        assert res["human"] and not res["ok"]
        gaps.append((w.get(wid)["next_check_at"] - before) / 600)
        clock.t = w.get(wid)["next_check_at"]
    assert w.get(wid)["error_count"] == 0 and w.get(wid)["state"] == "watching" and w.get(wid)["enabled"]
    assert 1.6 <= gaps[0] <= 2.4 and 3.2 <= gaps[1] <= 4.8 and 3.2 <= gaps[2] <= 4.8 and 3.2 <= gaps[3] <= 4.8     # x2, then x4 at most
    assert len(human) == 1 and "shop.example.com asks for a human check" in human[0] and "'좌석'" in human[0]
    clock.t += 24 * 3600 + 10
    await w.check(wid)
    assert len(human) == 2                                                      # one more a day later
    pg.answers = [ok("4")]                                                      # a clean look: back to normal
    clock.t = w.get(wid)["next_check_at"]
    await w.check(wid)
    assert w._row(wid)["backoff"] == 1 and w.get(wid)["last_error"] is None
    clock.t = w.get(wid)["next_check_at"]
    before = clock.t
    await w.check(wid)
    assert 0.8 * 600 <= w.get(wid)["next_check_at"] - before <= 1.2 * 600


# ---------------------------------------------------------------------------------------------------- the spread
async def test_the_next_check_is_spread_and_never_under_five_minutes():
    store, clock, w, *_ = setup(Looker(ok()))
    wid = make(w, every_minutes=60)["id"]
    await w.dry_run(wid)
    w.enable(wid)
    gaps = []
    for _ in range(40):
        t0 = clock.t
        await w.check(wid)
        gaps.append(w.get(wid)["next_check_at"] - t0)
    assert all(0.8 * 3600 <= g <= 1.2 * 3600 for g in gaps) and len({round(g) for g in gaps}) > 20
    assert max(gaps) - min(gaps) > 0.3 * 3600
    five = make(w, name="five", every_minutes=5)["id"]
    await w.dry_run(five)
    w.enable(five)
    for _ in range(30):
        t0 = clock.t
        await w.check(five)
        assert 300 <= w.get(five)["next_check_at"] - t0 <= 360
    a, b = random.Random(3), random.Random(3)                                    # a seeded rng gives the same spread
    assert [Watchers(Store(), rng=a)._gap(3600) for _ in range(3)] == [Watchers(Store(), rng=b)._gap(3600) for _ in range(3)]


# ---------------------------------------------------------------------------------------------------- confirming a hit
async def test_a_hit_is_confirmed_by_one_more_look_30_seconds_later():
    light = Looker(ok("0"))
    store, clock, w, fired, _ = setup(light)
    wid = make(w, note="tell me")["id"]
    await w.dry_run(wid)
    w.enable(wid)
    light.answers = [ok("7")]
    first = await w.check(wid)
    assert first["confirming"] and fired == [] and w.get(wid)["next_check_at"] == clock.t + 30
    assert w.get(wid)["fire_count"] == 0
    clock.t += 31
    light.answers = [ok("9")]                                                   # the second look agrees: it fires with the second value
    await w.check(wid)
    assert len(fired) == 1 and fired[0][1]["value"] == "9" and w.get(wid)["last_value"] == "9" and w.get(wid)["fire_count"] == 1
    assert 0.8 * 600 <= w.get(wid)["next_check_at"] - clock.t <= 1.2 * 600


async def test_a_glitch_that_the_second_look_does_not_repeat_never_wakes_anyone():
    light = Looker(ok("4"))
    store, clock, w, fired, _ = setup(light)
    wid = make(w)["id"]
    await w.dry_run(wid)
    w.enable(wid)
    light.answers = [ok("4")]                                                   # still satisfied since the dry run: no change, no hit
    await w.check(wid)
    assert fired == []
    store._run("UPDATE watchers SET last_hit = 0, last_value = '0' WHERE id = ?", (wid,))
    light.answers = [ok("6")]
    assert (await w.check(wid))["confirming"]
    light.answers = [ok("0")]                                                   # a half-loaded page, a momentary seat: gone again
    clock.t += 31
    res = await w.check(wid)
    assert fired == [] and not res["satisfied"] and w._row(wid)["confirming"] == 0 and w.get(wid)["fire_count"] == 0
    light.answers = [ok("6")]                                                   # a real change later starts over: look, confirm, fire
    clock.t += 700
    await w.check(wid)
    clock.t += 31
    await w.check(wid)
    assert len(fired) == 1


async def test_the_dry_run_does_not_confirm_and_a_failed_second_look_is_an_error_not_a_hit():
    light = Looker(ok("6"))
    store, clock, w, fired, _ = setup(light)
    wid = make(w)["id"]
    dry = await w.dry_run(wid)
    assert dry["satisfied"] and len(light.calls) == 1 and "confirming" not in dry
    store._run("UPDATE watchers SET last_hit = 0 WHERE id = ?", (wid,))
    w.enable(wid)
    store._run("UPDATE watchers SET last_hit = 0 WHERE id = ?", (wid,))
    await w.check(wid)
    light.answers = [NetGuardError("the page did not answer in time")]
    clock.t += 31
    await w.check(wid)
    assert fired == [] and w.get(wid)["error_count"] == 1 and w._row(wid)["confirming"] == 0


# ---------------------------------------------------------------------------------------------------- a watcher only reads
def test_a_post_that_names_an_action_is_refused_and_a_read_is_not():
    _, _, w, *_ = setup()
    body = '{"day": "2026-12-01"}'
    for path in ("/api/reserve", "/api/v1/booking/create", "/ticket/placeOrder", "/api/orders", "/api/cancel-ticket", "/pay/", "/checkout"):
        with pytest.raises(WatchError, match="only reads"):
            make(w, url=f"https://shop.example.com{path}", method="POST", body=body)
    for bad in ('{"action": "submit"}', "op=confirm&id=1", '{"cmd":"deleteItem"}'):
        with pytest.raises(WatchError, match="only reads"):
            make(w, method="POST", body=bad)
    for path in ("/예약/조회", "/api/결제", "/주문"):
        with pytest.raises(WatchError, match="only reads"):
            make(w, url="https://shop.example.com" + path, method="POST", body=body)
    for path in ("/api/search", "/api/seats/list", "/query", "/status", "/seat/inquiry", "/조회", "/api/검색", "/boarding", "/예약조회", "/좌석조회"):
        got = make(w, name=path, url=f"https://shop.example.com{path}", method="POST", body=body)
        assert got["method"] == "POST" and got["body"] == body
    assert make(w, name="get", url="https://shop.example.com/order/cancel", method="GET")["method"] == "GET"   # GET stays allowed
    assert action_word("/api/order-status") == "order" and action_word("/reservations/") == "reservation" and action_word("/ordering") is None
    with pytest.raises(WatchError, match="at most 4096"):
        make(w, method="POST", body="x" * 5000)
    with pytest.raises(WatchError, match="GET or POST"):
        make(w, method="DELETE")
    with pytest.raises(WatchError, match="cannot POST"):
        make(w, method="POST", body="{}", use_browser=True)


def test_headers_are_limited_and_never_carry_cookies_or_logins():
    ok_ = {"Accept": "application/json", "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "Referer": "https://a.example/",
           "Origin": "https://a.example", "X-Anything": "1"}
    assert clean_headers(ok_) == ok_ and clean_headers(None) == {} and clean_headers("") == {}
    for bad in ({"Cookie": "a=b"}, {"cookie": "a=b"}, {"Authorization": "Bearer x"}, {"User-Agent": "x"}, {"Host": "x"},
                {"X-HTTP-Method-Override": "DELETE"}, {"X-A": "a\r\nB: c"}, {"X-A": "a" * 201}, {f"X-{i}": "1" for i in range(9)}):
        with pytest.raises(WatchError):
            clean_headers(bad)
    _, _, w, *_ = setup()
    with pytest.raises(WatchError, match="Cookie"):
        make(w, headers={"Cookie": "session=abc"})
    got = make(w, name="h", headers={"X-Token": "t"})
    assert got["headers"] == {"X-Token": "t"} and make(w, name="n")["headers"] is None


def test_changing_the_request_turns_the_watcher_off_until_a_new_dry_run():
    store, clock, w, *_ = setup(Looker(ok()))
    wid = make(w, signed_in=True)["id"]
    for field, value in (("method", "POST"), ("body", "{}"), ("headers", {"X-A": "1"}), ("signed_in", False)):
        store._run("UPDATE watchers SET enabled = 1, dry_run_at = ?, dry_run = '{\"ok\": true}', config_at = 1 WHERE id = ?", (clock.t, wid))
        clock.t += 1
        w.update(wid, **{field: value})
        got = w.get(wid)
        assert got["enabled"] is False and got["can_enable"] is False, field
    w.update(wid, name="renamed")
    assert w.get(wid)["name"] == "renamed"


# ---------------------------------------------------------------------------------------------------- the light request
async def test_the_light_look_takes_the_browsers_identity_and_the_owners_language(monkeypatch):
    seen = []

    async def fake_get(url, **kw):
        seen.append(kw)
        return ok()

    monkeypatch.setattr(watchers_mod, "public_get", fake_get)

    async def version():
        return "131.0.6778.85"

    w = Watchers(Store(), browser_version=version, language=lambda: "ko-KR", proxy="socks5h://vpn:1080", clock=Clock())
    await w._light(URL)
    await w._light(URL, method="POST", body="{}", headers={"X-A": "1"})
    assert seen[0]["user_agent"] == chrome_user_agent(131) and "Chrome/131.0.0.0" in seen[0]["user_agent"] and "KeloWatcher" not in seen[0]["user_agent"]
    assert seen[0]["language"] == "ko-KR,ko;q=0.9,en;q=0.7" and seen[0]["proxy"] == "socks5h://vpn:1080"
    assert seen[1]["method"] == "POST" and seen[1]["body"] == "{}" and seen[1]["extra_headers"] == {"X-A": "1"}

    calls = []

    async def version2():
        calls.append(1)
        return "130.0.1.2"

    w2 = Watchers(Store(), browser_version=version2, clock=Clock())
    await w2.user_agent()
    await w2.user_agent()
    assert calls == [1]                                                          # read once, then cached

    async def no_browser():
        raise RuntimeError("no browser")

    clock = Clock()
    w3 = Watchers(Store(), browser_version=no_browser, clock=clock)
    assert "KeloWatcher" in await w3.user_agent() and "KeloWatcher" in await Watchers(Store()).user_agent()
    w3.browser_version = version
    assert "KeloWatcher" in await w3.user_agent()                                # it asks again only after a while
    clock.t += 601
    assert "Chrome/131" in await w3.user_agent()


async def test_public_get_sends_a_post_body_and_the_allowed_headers_but_never_a_cookie():
    seen = {}

    def handler(request):
        seen.update(method=request.method, body=request.content, headers=dict(request.headers))
        return httpx.Response(200, headers={"content-type": "application/json", "server": "cloudflare"}, content=b'{"seats": 3}')

    pub = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]   # noqa: E731
    page = await public_get("https://shop.example.com/api/seats", resolver=pub, transport=httpx.MockTransport(handler), method="POST",
                            body='{"q": 1}', extra_headers={"Accept": "application/json", "Cookie": "a=b", "authorization": "x", "X-A": "1"})
    assert seen["method"] == "POST" and seen["body"] == b'{"q": 1}' and seen["headers"]["content-type"] == "application/json"
    assert seen["headers"]["accept"] == "application/json" and seen["headers"]["x-a"] == "1"
    assert "cookie" not in seen["headers"] and "authorization" not in seen["headers"]
    assert page.headers == {"server": "cloudflare"} and "3" in page.body


# ---------------------------------------------------------------------------------------------------- the tool text
async def test_the_approval_card_shows_the_method_the_address_and_the_body():
    store, clock, w, *_ = setup(Looker(ok()), Looker(ok()))
    tools = {t.name: t for t in watch_tools(w)}
    out = await tools["watch_add"].handler({"name": "좌석", "url": "https://shop.example.com/api/seats/search", "method": "POST",
                                            "body": '{"day": "2026-12-01"}', "signed_in": True, "mode": "number_above", "selector": ".seats",
                                            "threshold": 0, "headers": {"Accept": "application/json"}})
    assert "error" not in out
    wid = w.list()[0]["id"]
    dry = await tools["watch_dry_run"].handler({"id": wid})
    assert "looked via tab" in dry and "inside the browser" in dry
    card = await tools["watch_enable"].describe({"id": wid})
    assert "POST https://shop.example.com/api/seats/search" in card and '{"day": "2026-12-01"}' in card
    bad = await tools["watch_add"].handler({"name": "x", "url": "https://shop.example.com/api/reserve", "method": "POST", "mode": "changed"})
    assert bad.startswith("error: a watcher only reads")
    props = tools["watch_add"].parameters["properties"]
    assert {"signed_in", "method", "body", "headers"} <= set(props)
    assert "signed_in" in props["url"]["description"]


def test_an_older_database_gets_the_new_columns():
    store = Store()
    with store._lock:
        store._db.executescript("CREATE TABLE watchers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'page', "
                                "url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '', match TEXT NOT NULL DEFAULT '', selector TEXT NOT NULL "
                                "DEFAULT '', regex TEXT NOT NULL DEFAULT '', threshold REAL, use_browser INTEGER NOT NULL DEFAULT 0, code TEXT NOT "
                                "NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', every_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, "
                                "once INTEGER NOT NULL DEFAULT 0, last_value TEXT, last_hit INTEGER, last_checked_at REAL, next_check_at REAL, "
                                "last_fired_at REAL, fire_count INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0, last_error TEXT "
                                "NOT NULL DEFAULT '', dry_run TEXT NOT NULL DEFAULT '', dry_run_at REAL, config_at REAL NOT NULL, disabled_reason "
                                "TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL, created_by TEXT NOT NULL DEFAULT 'user');"
                                "INSERT INTO watchers(name, url, mode, every_seconds, config_at, created_at, updated_at) "
                                "VALUES('old', 'https://a.example.com/', 'changed', 3600, 1, 1, 1);")
        store._db.commit()
    w = Watchers(store)
    got = w.get(1)
    assert got["method"] == "GET" and got["signed_in"] is False and got["body"] is None and got["headers"] is None and got["level"] is None
    Watchers(store)                                                              # starting again changes nothing


# ---------------------------------------------------------------------------------------------------- the browser's site tab
class SitePage(BrowserFakePage):
    def __init__(self, answer=None):
        super().__init__()
        self.answer, self.fetches = answer, []

    async def evaluate(self, js, arg=None):
        self.fetches.append(arg)
        return self.answer or {"status": 200, "contentType": "application/json", "text": '{"seats": 3}', "headers": {"server": "x"},
                               "url": arg["url"]}


ANY = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))]   # noqa: E731


def site_session(clock=None, resolver=ANY):
    pages = []

    async def connect():
        p = SitePage()
        pages.append(p)
        return p

    s = BrowserSession(connect=connect, resolver=resolver, clock=clock or Clock(1000.0))
    return s, pages


async def test_site_fetch_runs_the_request_inside_one_tab_per_site_and_keeps_no_cookies():
    s, pages = site_session()
    a = await s.site_fetch("https://shop.example.com/api/seats", method="POST", headers={"X-A": "1"}, body="{}", key="task:watch-site:example.com")
    assert a["status"] == 200 and a["text"] == '{"seats": 3}' and a["content_type"] == "application/json"
    await s.site_fetch("https://shop.example.com/api/seats", key="task:watch-site:example.com")
    assert len(pages) == 1                                                       # one tab per site, opened once (at the root for a POST)
    assert pages[0].calls == [("goto", "https://shop.example.com/")]
    assert pages[0].fetches[0] == {"url": "https://shop.example.com/api/seats", "method": "POST", "headers": {"X-A": "1"}, "body": "{}",
                                   "cap": 2_000_000, "timeout": 20_000}
    assert "cookie" not in str(pages[0].fetches).lower() and s.has_tab("task:watch-site:example.com")
    assert is_site_tab("task:watch-site:example.com") and not one_run_tab("task:watch-site:example.com") and one_run_tab("task:watch-7")


async def test_site_fetch_stays_on_one_origin():
    s, pages = site_session()
    key = "task:watch-site:example.com"
    pages_first = SitePage({"status": 200, "contentType": "", "text": "", "headers": {}, "url": "https://other.example.net/x"})

    async def connect():
        return pages_first
    s._connect = connect
    with pytest.raises(BrowserPolicyError, match="another address"):                # the answer came from another origin
        await s.site_fetch("https://shop.example.com/api/seats", key=key)
    s2, _ = site_session()
    page = SitePage()
    page.next_url = "https://login.example.net/"                                    # the site sends the tab to another origin
    s2._connect = lambda: _async(page)
    with pytest.raises(BrowserPolicyError, match="another address"):
        await s2.site_fetch("https://shop.example.com/api/seats", key=key)
    s3, _ = site_session(resolver=PUBLIC)
    with pytest.raises(BrowserPolicyError):                                         # internal addresses are refused before any tab opens
        await s3.site_fetch("http://lan.test/api", key=key)
    assert not s3.open_keys()
    # a tab on another origin of the same site is moved to the requested origin, never asked cross-origin
    s4, pages4 = site_session()
    await s4.site_fetch("https://www.example.com/a", key=key)
    pages4[0].answer = {"status": 200, "contentType": "", "text": "x", "headers": {}, "url": "https://api.example.com/b"}
    await s4.site_fetch("https://api.example.com/b", key=key)
    assert [c[1] for c in pages4[0].calls if c[0] == "goto"] == ["https://www.example.com/a", "https://api.example.com/b"]


async def _async(v):
    return v


async def test_site_tabs_close_when_idle_and_at_most_three_stay():
    clock = Clock(1000.0)
    s, pages = site_session(clock)
    keys = [f"task:watch-site:site{i}.com" for i in range(5)]
    for i, k in enumerate(keys):
        await s.site_fetch(f"https://www.site{i}.com/api", key=k)
        clock.t += 5
    assert len([k for k in s.open_keys() if is_site_tab(k)]) == SITE_TABS_MAX == 3
    assert set(s.open_keys()) == set(keys[2:])                                      # the least recently used ones made room
    clock.t += SITE_TAB_IDLE_SECONDS - 6
    await s.site_fetch("https://www.site4.com/api", key=keys[4])                    # the newest is used again; the idle ones close
    assert s.open_keys() == [keys[4]]
    assert await s.chrome_version() == ""                                           # a fake without a real browser


async def test_page_state_reads_the_status_and_the_check_of_the_current_tab():
    s, _ = site_session()
    from assistant.tools.browser import browser_owner
    tok = browser_owner.set("task:watch-9")
    try:
        assert await s.page_state() == (None, {}, None)
        page = await s.page()
        page.evaluate = lambda js, arg=None: _async({"kelo_check": 1, "title": "Just a moment...", "frames": [], "marks": {}, "scripts": []})
        s._doc["task:watch-9"] = (403, {"cf-mitigated": "challenge"})
        status, headers, found = await s.page_state()
    finally:
        browser_owner.reset(tok)
    assert status == 403 and headers == {"cf-mitigated": "challenge"} and found.vendor == "cloudflare"
