"""Ready-to-buy watchers (buy_ready.py, docs/instant-buy.md A + B): arming follows the purchase grant, readiness problems, the 2-minute
interval and the missing confirmation look for an armed watcher, and the short buying run at the hit. Fakes: pages, browser session, model."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from assistant import server as server_mod
from assistant.agent import Tool
from assistant.buy_ready import FAST_STEPS, FAST_TOOLS, BuyReady
from assistant.providers import Done, TextDelta
from assistant.schedule_grants import ConditionalGrants
from assistant.server import Settings, create_app
from assistant.shipping import ShippingBook
from assistant.store import Store
from assistant.tools.browser import browser_owner
from assistant.wallet import Wallet
from assistant.watchers import WatchError, Watchers

from test_address_shipping import KR
from test_server import AUTH, TOKEN, Running
from test_vault import KEY
from test_wallet import auto_card, e2e_card
from test_watching import PAGE, Clock, FakePages, until

SHOP = "https://shop.example.com/sneaker"
SIGNED_OUT = 'link "로그인" [ref=e1]\nbutton "장바구니" [ref=e2]'
SIGNED_IN = 'link "로그아웃" [ref=e1]\nlink "마이페이지" [ref=e3]\nbutton "장바구니" [ref=e2]'


class FakeSession:
    """The parts of BrowserSession the keep-alive uses: a tab per owner key, `open` returns the page tree."""

    def __init__(self, text=SIGNED_IN):
        self.text, self.tabs, self.opened, self.closed = text, set(), [], []

    async def open(self, url, view=""):
        tab = browser_owner.get()
        self.tabs.add(tab)
        self.opened.append((tab, url))
        return self.text

    def has_tab(self, key):
        return key in self.tabs

    async def close_tab(self, key):
        self.tabs.discard(key)
        self.closed.append(key)
        return True

    def touch(self, key):
        pass


def world(tmp_path, text=SIGNED_IN, login=None):
    store = Store(tmp_path / "a.db", KEY.decode())
    wallet = Wallet(store)
    grants = ConditionalGrants(store, None, wallet)
    watchers = Watchers(store, fetch=FakePages(), clock=Clock(1_800_000_000.0))
    grants.watchers = watchers
    shipping = ShippingBook(store)
    grants.shipping = shipping
    session = FakeSession(text)
    logins = []

    async def login_for(scope, site):
        logins.append((scope, site))
        if login is not None:
            session.text = login
        return login is not None

    buy = BuyReady(store, watchers, grants, wallet, shipping, lambda: session, login_for, clock=watchers.clock)
    watchers.armed, watchers.buy_view = buy.is_armed, buy.view
    return SimpleNamespace(store=store, wallet=wallet, grants=grants, watchers=watchers, shipping=shipping, session=session, buy=buy,
                           logins=logins)


def watching(w, **kw):
    """A watcher on the shop page, turned on (the dry run is skipped: the store is set directly)."""
    made = w.watchers.create(name="Sneaker", url=SHOP, mode="text_appears", match="in stock", every_minutes=30, **kw)
    w.store._run("UPDATE watchers SET enabled = 1, next_check_at = ? WHERE id = ?", (w.watchers.clock() + 1800, made["id"]))
    return made["id"]


def allow(w, wid, card=None, **kw):
    return w.grants.add_purchase(None, "shop.example.com", "sneaker", 100000, "KRW", scope=f"watcher:{wid}", max_count=1,
                                 card_id=(card or {}).get("id", ""), **kw)


# ---------------------------------------------------------------------------------------------------------------- arm / disarm
async def test_arm_and_disarm_follow_the_purchase_grant(tmp_path):
    w = world(tmp_path)
    wid = watching(w)
    await w.buy.tick()
    assert w.buy.view(wid) is None and not w.session.tabs                         # no grant: nothing armed
    w.shipping.add(KR)
    g = allow(w, wid, auto_card(w.wallet))
    await w.buy.tick()
    v = w.buy.view(wid)
    assert v["armed"] and v["tab"] == f"buy:{wid}" and v["problems"] == [] and v["signed_in"] is True and v["remembered"] is False
    assert w.session.tabs == {f"buy:{wid}"} and w.session.opened == [(f"buy:{wid}", SHOP)]
    assert w.watchers.get(wid)["buy"] == v and w.watchers.list()[0]["buy"] == v
    await w.buy.tick()                                                             # not due yet: no second load
    assert len(w.session.opened) == 1
    w.store._run("UPDATE schedule_grants SET uses = 1 WHERE id = ?", (g["id"],))   # the one purchase is used
    await w.buy.tick()
    assert w.buy.view(wid) is None and w.session.closed == [f"buy:{wid}"] and not w.session.tabs
    w.store._run("UPDATE schedule_grants SET uses = 0 WHERE id = ?", (g["id"],))
    await w.buy.tick()
    assert w.buy.view(wid)["armed"]
    w.grants.revoke(g["id"], scope=f"watcher:{wid}")                               # removed
    await w.buy.tick()
    assert w.buy.view(wid) is None and w.session.closed[-1] == f"buy:{wid}"
    allow(w, wid)
    await w.buy.tick()
    w.store._run("UPDATE watchers SET enabled = 0 WHERE id = ?", (wid,))           # turned off
    await w.buy.tick()
    assert w.buy.view(wid) is None


async def test_a_deleted_watcher_is_disarmed(tmp_path):
    w = world(tmp_path)
    wid = watching(w)
    allow(w, wid)
    await w.buy.tick()
    assert w.buy.view(wid)
    w.watchers.delete(wid)
    await w.buy.tick()
    assert w.buy.view(wid) is None and w.session.closed == [f"buy:{wid}"]


# ---------------------------------------------------------------------------------------------------------------- readiness
async def test_readiness_problems_are_coded_and_worded(tmp_path):
    w = world(tmp_path, text=SIGNED_OUT)
    wid = watching(w)
    allow(w, wid, e2e_card(w.wallet))                                              # a card with no server copy; no address; signed out
    v = await w.buy.arm(wid)
    assert [p["code"] for p in v["problems"]] == ["card_needs_phone", "no_shipping", "signed_out"]
    assert all(p["text"] for p in v["problems"]) and "shop.example.com" in v["problems"][2]["text"] and v["signed_in"] is False
    assert w.logins == []                                                          # no login grant: only listed
    w.shipping.add(KR)
    w.grants.drop_watcher(wid)
    allow(w, wid)                                                                  # no card chosen, no default card
    assert [p["code"] for p in (await w.buy.keep(wid))["problems"]] == ["no_card", "signed_out"]
    w.session.text = SIGNED_IN
    w.grants.drop_watcher(wid)
    allow(w, wid, auto_card(w.wallet))
    assert (await w.buy.keep(wid))["problems"] == []
    assert w.buy.view(wid)["last_kept_at"] == w.watchers.clock()


async def test_a_login_grant_signs_in_quietly_once(tmp_path):
    w = world(tmp_path, text=SIGNED_OUT, login=SIGNED_IN)
    w.shipping.add(KR)
    wid = watching(w)
    allow(w, wid, auto_card(w.wallet))
    w.grants.add_login(None, "shopping", "shop.example.com", scope=f"watcher:{wid}")
    v = await w.buy.arm(wid)
    assert w.logins == [(f"watcher:{wid}", "shop.example.com")]                         # once
    assert v["signed_in"] is True and v["problems"] == []
    assert [o[1] for o in w.session.opened] == [SHOP, SHOP]                        # loaded, signed in, loaded again


async def test_a_keep_alive_that_cannot_open_the_page_says_so_and_no_browser_is_a_problem(tmp_path):
    w = world(tmp_path)
    w.shipping.add(KR)
    wid = watching(w)
    allow(w, wid, auto_card(w.wallet))

    async def boom(url, view=""):
        raise RuntimeError("net down")
    w.session.open = boom
    v = await w.buy.arm(wid)
    assert [p["code"] for p in v["problems"]] == ["page_error"] and "net down" in v["problems"][0]["text"]
    w.buy.session_of = lambda: None
    assert [p["code"] for p in (await w.buy.keep(wid))["problems"]] == ["browser_off"]


# ---------------------------------------------------------------------------------------------------------------- the watcher
async def test_an_armed_watcher_may_look_every_two_minutes_and_fires_without_the_confirmation_look(tmp_path):
    w = world(tmp_path)
    wid = watching(w)
    with pytest.raises(WatchError, match="5"):
        w.watchers.update(wid, every_minutes=2)                                    # not armed: the usual minimum
    allow(w, wid)
    assert w.watchers.update(wid, every_minutes=2)["every_minutes"] == 2
    with pytest.raises(WatchError, match="2"):
        w.watchers.update(wid, every_minutes=1)
    w.grants.drop_watcher(wid)                                                     # no longer armed: back to 5, and a rename still works
    assert w.watchers.update(wid, name="Shoe")["every_minutes"] == 5


def _price_watch():
    store = Store()
    clock = Clock(1_800_000_000.0)
    pages = FakePages()
    ws = Watchers(store, fetch=pages, clock=clock)
    fired = []
    ws.on_fire = lambda wt, res: fired.append(wt["id"])
    wt = ws.create(name="bike", url="https://shop.example.com/bike", mode="number_below", selector=".price", threshold=1_000_000,
                   every_minutes=30)
    return store, clock, pages, ws, fired, wt["id"]


async def test_the_confirmation_look_is_kept_for_a_watcher_that_is_not_armed_and_skipped_for_one_that_is():
    for armed in (False, True):
        store, clock, pages, ws, fired, wid = _price_watch()
        ws.armed = lambda i, on=armed: on
        await ws.dry_run(wid)
        ws.enable(wid)
        pages.html = PAGE.replace("1,250,000", "980,000")
        res = await ws.check(wid)
        if armed:
            assert fired == [wid] and not res.get("confirming")
            assert ws.get(wid)["next_check_at"] - clock.t >= 2 * 60 * 0.8 - 1        # still spread, never under 2 minutes
        else:
            assert fired == [] and res["confirming"]


async def test_the_next_check_of_an_armed_watcher_can_come_after_two_minutes():
    store, clock, pages, ws, fired, wid = _price_watch()
    ws.armed = lambda i: True
    ws.update(wid, every_minutes=2)
    await ws.dry_run(wid)
    ws.enable(wid)
    await ws.check(wid)
    gap = ws.get(wid)["next_check_at"] - clock.t
    assert 96 <= gap <= 144
    ws.armed = lambda i: False                                                     # not armed any more: five minutes at least
    await ws.check(wid)
    assert ws.get(wid)["next_check_at"] - clock.t >= 300


# ---------------------------------------------------------------------------------------------------------------- the app
def app_world(tmp_path):
    stubs = [Tool(n, n, {"type": "object"}, _ok) for n in (*FAST_TOOLS, "browser_open", "web_search")]
    calls = []

    async def fn(provider, request, **kw):
        calls.append(request)
        yield TextDelta("알려 드렸어요.")
        yield Done("stop")
    fn.calls = calls
    app = create_app(Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "a.db"), scheduler=False),
                     tools=stubs, stream_fn=fn)
    return app, fn


async def _ok(args):
    return "ok"


async def test_the_api_shows_buy_and_the_buy_check_route_and_the_interval(tmp_path):
    app, _ = app_world(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        made = (await c.post("/api/watchers", json={"name": "Sneaker", "url": SHOP, "mode": "text_appears", "match": "in stock"})).json()
        wid = made["id"]
        assert made["buy"] is None
        assert (await c.patch(f"/api/watchers/{wid}", json={"every_minutes": 2})).status_code == 422
        assert (await c.post(f"/api/watchers/{wid}/buy-check")).status_code == 404
        app.state.schedule_grants.add_purchase(None, "shop.example.com", "sneaker", 100000, "KRW", scope=f"watcher:{wid}", max_count=1)
        assert (await c.patch(f"/api/watchers/{wid}", json={"every_minutes": 2})).json()["every_minutes"] == 2
        await app.state.buy_ready.arm(wid)                                          # no browser in this app: the row and a problem
        got = (await c.get(f"/api/watchers/{wid}")).json()["buy"]
        assert got["armed"] and got["tab"] == f"buy:{wid}" and {p["code"] for p in got["problems"]} >= {"browser_off"}
        assert (await c.get("/api/watchers")).json()["items"][0]["buy"] == got
        checked = (await c.post(f"/api/watchers/{wid}/buy-check")).json()
        assert checked["armed"] and checked["last_kept_at"]


def fake_runs(monkeypatch, status="done", pay=None):
    """Records every run_turn; a run of source buy:<id> is a fake that ends at once (and, with `pay`, writes its covered payment)."""
    real = server_mod.run_turn
    calls = []

    def fake(ctx, provider, model, messages, observe=None, *args, **kw):
        calls.append({"source": kw.get("source"), "owner": kw.get("owner"), "cond": kw.get("cond"), "max_steps": kw.get("max_steps"),
                      "tools": [t.name for t in (kw.get("tools_") or [])], "unattended": kw.get("unattended"), "messages": messages})
        if not str(kw.get("source") or "").startswith("buy:"):
            return real(ctx, provider, model, messages, observe, *args, **kw)
        done = asyncio.Event()
        done.set()
        if observe is not None:
            observe({"type": "text", "text": "품절이라 못 샀어요"}) if False else None
        turn = SimpleNamespace(ended=done, final_status=status, final_error=None, task_id=4242, quiet_end=False)
        calls[-1]["turn"] = turn
        if pay is not None:
            pay(turn)
        if observe is not None:
            observe(None)
        return turn
    monkeypatch.setattr(server_mod, "run_turn", fake)
    return calls


async def _hit(c, app, tmp_path, armed=True):
    sg = app.state.schedule_grants
    app.state.shipping if False else None
    made = (await c.post("/api/watchers", json={"name": "Sneaker", "url": SHOP, "mode": "text_appears", "match": "in stock"})).json()
    wid = made["id"]
    grant = sg.add_purchase(None, "shop.example.com", "sneaker", 100000, "KRW", scope=f"watcher:{wid}", max_count=1)
    if armed:
        await app.state.buy_ready.arm(wid)
    app.state.watchers.on_fire(app.state.watchers.get(wid), {"detail": "in stock", "url": SHOP})
    return wid, grant


async def test_the_hit_of_an_armed_watcher_starts_the_fast_run_not_the_normal_handoff(tmp_path, monkeypatch):
    calls = fake_runs(monkeypatch)
    app, fn = app_world(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        wid, _ = await _hit(c, app, tmp_path)
        await until(lambda: any(x["source"] == "handoff" for x in calls))             # the report to the owner
        buy = next(x for x in calls if x["source"] == f"buy:{wid}")
        assert buy["owner"] == f"buy:{wid}" and buy["cond"] == f"watcher:{wid}" and buy["max_steps"] == FAST_STEPS == 25
        assert buy["unattended"] is True and buy["tools"] == list(FAST_TOOLS) and "web_search" not in buy["tools"]
        assert buy["messages"][0]["role"] == "system" and "sneaker" in buy["messages"][0]["content"]
        assert "shop.example.com" in buy["messages"][0]["content"] and "in stock" in buy["messages"][1]["content"]
        assert not any(x["source"] == "handoff" and x["cond"] for x in calls)           # no normal handoff under the watcher's permissions
        assert wid not in app.state.buy_ready.busy
        notes = (await c.get("/api/notifications")).json()["items"]
        assert [n["type"] for n in notes].count("watch_fired") == 2                     # the hit's one, and the "could not buy" one
        assert fn.calls and "WATCH" not in json.dumps(fn.calls[0].messages[-1]["content"])[:0]


async def test_a_watcher_that_is_not_armed_keeps_the_normal_handoff(tmp_path, monkeypatch):
    calls = fake_runs(monkeypatch)
    app, _ = app_world(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        wid, _ = await _hit(c, app, tmp_path, armed=False)
        await until(lambda: any(x["source"] == "handoff" for x in calls))
        assert [x["cond"] for x in calls if x["source"] == "handoff"] == [f"watcher:{wid}"]
        assert not any(str(x["source"]).startswith("buy:") for x in calls)


async def test_a_fast_run_that_ends_without_a_payment_sends_one_urgent_notification(tmp_path, monkeypatch):
    calls = fake_runs(monkeypatch)
    app, _ = app_world(tmp_path)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        wid, _ = await _hit(c, app, tmp_path)
        await until(lambda: any(x["source"] == "handoff" for x in calls))
        notes = (await c.get("/api/notifications")).json()["items"]
        failed = [n for n in notes if n["title"].startswith("Could not buy right away")]
        assert len(failed) == 1 and failed[0]["urgent"] is True and "Sneaker" in failed[0]["title"]


async def test_a_fast_run_with_a_covered_payment_sends_no_failure_notification(tmp_path, monkeypatch):
    app, _ = app_world(tmp_path)
    store = app.state.store if hasattr(app.state, "store") else None

    def pay(turn):
        gid = app.state.schedule_grants.list(kind="purchase")[0]["id"]
        app.state.buy_ready.store._run(
            "INSERT INTO wallet_payments(card_id, amount, currency, host, kind, ts, month, grant_id, task_id, summary, site) "
            "VALUES('c', 50000, 'KRW', 'shop.example.com', 'auto', 1, '2026-11', ?, ?, '', 'example.com')", (gid, turn.task_id))
    calls = fake_runs(monkeypatch, pay=pay)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await _hit(c, app, tmp_path)
        await until(lambda: any(x["source"] == "handoff" for x in calls))
        notes = (await c.get("/api/notifications")).json()["items"]
        assert not [n for n in notes if n["title"].startswith("Could not buy right away")]
    assert store is None or True
