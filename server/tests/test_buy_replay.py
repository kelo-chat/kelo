"""Tests for remembered checkout steps (buy_replay.py, docs/instant-buy.md C)."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from assistant import server as server_mod
from assistant.agent import AssistantText, Tool
from assistant.agent.loop import run_agent
from assistant.buy_ready import FAST_STEPS, FAST_TOOLS
from assistant.buy_replay import (
    DONE_LINE,
    STOP_NOTE,
    Plans,
    Recorder,
    ReplayStream,
    find_ref,
    purchase_total,
)
from assistant.i18n import t
from assistant.providers import Done, TextDelta, ToolCall
from assistant.schedule_grants import PurchaseCheck
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.wallet import money_text

from test_server import AUTH, TOKEN, Running
from test_vault import KEY
from test_watching import until

SHOP = "https://shop.example.com/goods/123"


# --------------------------------------------------------------------------- purchase_total
def test_purchase_total():
    # "총 결제금액 12,900원" -> 12900
    assert purchase_total("총 결제금액 12,900원", "KRW") == 12900.0

    # a label line "총 결제금액" then the next line "12,900원" -> 12900
    assert purchase_total("총 결제금액\n12,900원", "KRW") == 12900.0

    # "Subtotal $10.00 / Order total $12.50" (USD) -> 12.5
    assert purchase_total("Subtotal $10.00\nOrder total $12.50", "USD") == 12.5

    # "합계 9,000원" alone -> 9000
    assert purchase_total("합계 9,000원", "KRW") == 9000.0

    # strong 12,900원 + weak 9,000원 -> 12900
    assert purchase_total("합계 9,000원\n총 결제금액 12,900원", "KRW") == 12900.0

    # two strong labels with 12,900원 and 13,900원 -> None
    assert purchase_total("총 결제금액 12,900원\n최종 결제 금액 13,900원", "KRW") is None

    # no label -> None
    assert purchase_total("상품 가격 12,900원", "KRW") is None


# --------------------------------------------------------------------------- find_ref
def test_find_ref():
    words = {
        "e1": 'button "구매하기"',
        "e2": 'button "장바구니 담기"',
        "e3": 'link "구매하기"',
    }
    # exact single match
    assert find_ref(words, "button", "구매하기") == "e1"

    # another role ignored
    assert find_ref(words, "link", "장바구니 담기") is None
    assert find_ref(words, "link", "구매하기") == "e3"

    # exact twice -> None
    words_dup = {
        "e1": 'button "구매하기"',
        "e2": 'button "구매하기"',
    }
    assert find_ref(words_dup, "button", "구매하기") is None

    # contains-fallback single
    assert find_ref(words, "button", "장바구니") == "e2"

    # contains twice -> None
    words_contains = {
        "e1": 'button "바로 구매하기"',
        "e2": 'button "선물 구매하기"',
    }
    assert find_ref(words_contains, "button", "구매하기") is None


# --------------------------------------------------------------------------- Plans
def test_plans(tmp_path):
    store = Store(tmp_path / "plans.db", KEY.decode())
    clock_val = [1000.0]
    plans = Plans(store, clock=lambda: clock_val[0])

    steps = [{"do": "open", "url": "https://shop.example.com/goods/123"}]
    plans.save("https://shop.example.com/goods/123", steps)

    # exact find
    found = plans.find("https://shop.example.com/goods/123")
    assert found is not None
    assert found["product_path"] == "/goods/123"
    assert found["steps"] == steps
    assert found["used_count"] == 0
    assert found["fails"] == 0

    # pattern find: /goods/123 -> /goods/456
    found_pat = plans.find("https://shop.example.com/goods/456")
    assert found_pat is not None
    assert found_pat["id"] == found["id"]

    # ok -> used_count 1, fails reset
    clock_val[0] = 2000.0
    plans.ok(found["id"])
    found2 = plans.find("https://shop.example.com/goods/123")
    assert found2["used_count"] == 1
    assert found2["fails"] == 0
    assert found2["last_ok_at"] == 2000.0

    # failed twice -> dropped (find -> None)
    assert not plans.failed(found["id"])  # 1 fail: not dropped
    assert plans.find("https://shop.example.com/goods/123") is not None
    assert plans.failed(found["id"])      # 2 fails: dropped
    assert plans.find("https://shop.example.com/goods/123") is None


# --------------------------------------------------------------------------- Recorder
class FakeRecorderSession:
    def __init__(self, url=SHOP, changed=True, purchase=False, ref_words=None):
        self._url = url
        self._changed = changed
        self._purchase = purchase
        self._ref_words = ref_words or {}

    def current_url(self):
        return self._url

    def ref_words_of(self, tab):
        return dict(self._ref_words.get(tab, {}))

    def last_click_changed_nothing(self, tab):
        return not self._changed

    def purchase_active(self):
        return self._purchase


@pytest.mark.asyncio
async def test_recorder():
    tab = "buy:1"
    session = FakeRecorderSession(
        url="https://shop.example.com/goods/123",
        changed=True,
        purchase=False,
        ref_words={tab: {
            "e1": 'button "구매하기"',
            "e2": 'spinbutton "수량"',
            "e3": 'textbox "받는 분 이름"',
            "e4": 'button ""',
        }}
    )
    recorder = Recorder(session, tab)

    async def h_click(args):
        return "clicked"

    async def h_type(args):
        return "typed"

    async def h_open(args):
        return "opened"

    async def h_fill_shipping(args):
        return "shipping"

    async def h_fill_card(args):
        return "card"

    async def h_confirm(args):
        session._purchase = True
        return "confirmed"

    async def h_login(args):
        return "logged in"

    async def h_err(args):
        raise RuntimeError("boom")

    tools = [
        Tool("browser_open", "open", {}, h_open),
        Tool("browser_click", "click", {}, h_click),
        Tool("browser_type", "type", {}, h_type),
        Tool("browser_fill_shipping", "ship", {}, h_fill_shipping),
        Tool("browser_fill_card", "card", {}, h_fill_card),
        Tool("confirm_purchase", "confirm", {}, h_confirm),
        Tool("browser_login", "login", {}, h_login),
        Tool("browser_click_err", "err", {}, h_err),
    ]

    wrapped = {t.name: t for t in recorder.wrap(tools)}

    # open kept
    await wrapped["browser_open"].handler({"url": "https://shop.example.com/goods/123?ref=test"})
    assert recorder.steps[-1] == {"do": "open", "url": "https://shop.example.com/goods/123"}

    # click kept with role, name, path
    await wrapped["browser_click"].handler({"selector": "e1"})
    assert recorder.steps[-1] == {"do": "click", "role": "button", "name": "구매하기", "path": "/goods/123"}

    # click that changed nothing is not kept
    session._changed = False
    await wrapped["browser_click"].handler({"selector": "e1"})
    assert recorder.steps[-1]["role"] == "button"  # did not add another click
    session._changed = True

    # typing "2" into spinbutton "수량" is kept
    await wrapped["browser_type"].handler({"selector": "e2", "text": "2"})
    assert recorder.steps[-1] == {"do": "type", "role": "spinbutton", "name": "수량", "text": "2"}

    # fill shipping kept
    await wrapped["browser_fill_shipping"].handler({"profile": "집"})
    assert recorder.steps[-1] == {"do": "fill_shipping", "profile": "집"}

    # confirm kept when purchase_active
    await wrapped["confirm_purchase"].handler({})
    assert recorder.steps[-1] == {"do": "confirm", "path": "/goods/123"}

    # usable needs confirm followed by another step
    assert not recorder.usable()  # confirm is currently the last step

    # fill card kept
    await wrapped["browser_fill_card"].handler({"card": "신한"})
    assert recorder.steps[-1] == {"do": "fill_card", "card": "신한"}

    assert recorder.usable()  # now confirm is followed by fill_card

    # typing into textbox "받는 분 이름" makes it unusable
    await wrapped["browser_type"].handler({"selector": "e3", "text": "홍길동"})
    assert not recorder.usable()
    assert "personal or unnamed" in recorder.unusable

    # browser_login makes it unusable
    rec2 = Recorder(session, tab)
    wrapped2 = {t.name: t for t in rec2.wrap(tools)}
    await wrapped2["browser_login"].handler({})
    assert not rec2.usable()
    assert "browser_login cannot be replayed" in rec2.unusable

    # a handler that raises keeps nothing
    rec3 = Recorder(session, tab)
    wrapped3 = {t.name: t for t in rec3.wrap([Tool("browser_click", "click", {}, h_err)])}
    with pytest.raises(RuntimeError):
        await wrapped3["browser_click"].handler({"selector": "e1"})
    assert rec3.steps == []


# --------------------------------------------------------------------------- ReplayStream in agent loop
class FakeLoopSession:
    def __init__(self, tab="buy:1", page_text="에어팟 프로\n총 결제금액 12,900원"):
        self.tab = tab
        self.page_text = page_text
        self.ref_words = {
            tab: {
                "e1": 'button "구매하기"',
                "e2": 'button "결제하기"',
            }
        }
        self._changed = True
        self._purchase = False
        self.clicks = []

    def current_url(self):
        return "https://shop.example.com/goods/123"

    def current_host(self):
        return "shop.example.com"

    def ref_words_of(self, tab):
        return dict(self.ref_words.get(tab, {}))

    def last_click_changed_nothing(self, tab):
        return not self._changed

    def purchase_active(self):
        return self._purchase


class FakeGrants:
    def __init__(self, check_result=None):
        self._chk = check_result or PurchaseCheck(grant={"id": 1, "max_amount": 20000})

    def purchase_check(self, host, amount, currency, summary):
        return self._chk


def replay_tools(session):
    tool_calls_made = []

    async def h_read(args):
        tool_calls_made.append(("browser_read", args))
        return session.page_text

    async def h_click(args):
        tool_calls_made.append(("browser_click", args))
        session.clicks.append(args.get("selector"))
        return "clicked"

    async def h_shipping(args):
        tool_calls_made.append(("browser_fill_shipping", args))
        return "shipping done"

    async def h_card(args):
        tool_calls_made.append(("browser_fill_card", args))
        return "card done"

    async def h_confirm(args):
        tool_calls_made.append(("confirm_purchase", args))
        session._purchase = True
        return "APPROVED"

    async def h_end(args):
        tool_calls_made.append(("end_order", args))
        return "order ended"

    tools = [
        Tool("browser_read", "read", {}, h_read),
        Tool("browser_click", "click", {}, h_click),
        Tool("browser_fill_shipping", "ship", {}, h_shipping),
        Tool("browser_fill_card", "card", {}, h_card),
        Tool("confirm_purchase", "confirm", {}, h_confirm),
        Tool("end_order", "end", {}, h_end),
    ]
    return tools, tool_calls_made


@pytest.mark.asyncio
async def test_replay_stream_success_path():
    tab = "buy:1"
    session = FakeLoopSession(tab)
    tools, calls_made = replay_tools(session)
    grant = {"what": "에어팟", "currency": "KRW", "max_amount": 20000}
    grants = FakeGrants()
    plan = {
        "id": 1,
        "steps": [
            {"do": "click", "role": "button", "name": "구매하기"},
            {"do": "fill_shipping", "profile": ""},
            {"do": "confirm", "path": "/checkout"},
            {"do": "fill_card", "card": ""},
            {"do": "click", "role": "button", "name": "결제하기"},
        ]
    }
    replay = ReplayStream(plan, session, tab, grant, grants)

    real_called = []

    async def real(provider, request, **kw):
        real_called.append(request)
        yield TextDelta("real model should not be called")
        yield Done("stop")

    wrapped_stream = replay.wrap(real)
    events = [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "buy"}], tools, stream_fn=wrapped_stream)]

    assert not real_called
    assert replay.finished
    assert not replay.why

    expected_call_names = [
        "browser_click",
        "browser_fill_shipping",
        "browser_read",
        "confirm_purchase",
        "browser_fill_card",
        "browser_click",
        "end_order",
    ]
    assert [name for name, _ in calls_made] == expected_call_names
    assert calls_made[0][1] == {"selector": "e1"}
    assert calls_made[3][1] == {"site": "shop.example.com", "summary": "에어팟", "amount": 12900.0, "currency": "KRW"}
    assert calls_made[5][1] == {"selector": "e2"}

    text_evs = [ev.text for ev in events if isinstance(ev, AssistantText)]
    done_text = "".join(text_evs)
    expected_done = t(DONE_LINE, what="에어팟", amount=money_text(12900.0, "KRW"))
    assert expected_done in done_text


@pytest.mark.asyncio
async def test_replay_stream_missing_element_hands_over_to_model():
    tab = "buy:1"
    session = FakeLoopSession(tab)
    # Remove "결제하기" button from refs
    session.ref_words[tab] = {"e1": 'button "구매하기"'}
    tools, calls_made = replay_tools(session)
    grant = {"what": "에어팟", "currency": "KRW"}
    grants = FakeGrants()
    plan = {
        "id": 1,
        "steps": [
            {"do": "click", "role": "button", "name": "구매하기"},
            {"do": "confirm", "path": "/checkout"},
            {"do": "click", "role": "button", "name": "결제하기"},
        ]
    }
    replay = ReplayStream(plan, session, tab, grant, grants)

    real_requests = []

    async def real(provider, request, **kw):
        real_requests.append(request)
        yield TextDelta("Handed over OK")
        yield Done("stop")

    wrapped_stream = replay.wrap(real)
    [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "buy"}], tools, stream_fn=wrapped_stream)]

    assert len(real_requests) == 1
    req = real_requests[0]
    # Check that STOP_NOTE was inserted into messages
    user_msgs = [m for m in req.messages if m.get("role") == "user" and "[Kelo]" in str(m.get("content") or "")]
    assert len(user_msgs) == 1
    note_content = user_msgs[0]["content"]
    assert 'click button "구매하기"' in note_content
    assert 'no single button named "결제하기" on the page' in note_content
    assert replay.why
    assert [name for name, _ in calls_made].count("confirm_purchase") == 1


@pytest.mark.asyncio
async def test_replay_stream_ambiguous_totals_stops_before_confirm():
    tab = "buy:1"
    session = FakeLoopSession(tab, page_text="에어팟 프로\n총 결제금액 12,900원\n최종 결제 금액 15,900원")
    tools, calls_made = replay_tools(session)
    grant = {"what": "에어팟", "currency": "KRW"}
    grants = FakeGrants()
    plan = {
        "id": 1,
        "steps": [
            {"do": "confirm", "path": "/checkout"},
        ]
    }
    replay = ReplayStream(plan, session, tab, grant, grants)

    real_requests = []

    async def real(provider, request, **kw):
        real_requests.append(request)
        yield TextDelta("Handed over OK")
        yield Done("stop")

    wrapped_stream = replay.wrap(real)
    [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "buy"}], tools, stream_fn=wrapped_stream)]

    assert len(real_requests) == 1
    assert not any(name == "confirm_purchase" for name, _ in calls_made)
    assert "the total on the page is not clear" in replay.why


@pytest.mark.asyncio
async def test_replay_stream_purchase_check_refusal():
    tab = "buy:1"
    session = FakeLoopSession(tab)
    tools, calls_made = replay_tools(session)
    grant = {"what": "에어팟", "currency": "KRW"}
    grants = FakeGrants(check_result=PurchaseCheck(None, "task_over_cap", "over cap limit"))
    plan = {
        "id": 1,
        "steps": [
            {"do": "confirm", "path": "/checkout"},
        ]
    }
    replay = ReplayStream(plan, session, tab, grant, grants)

    real_requests = []

    async def real(provider, request, **kw):
        real_requests.append(request)
        yield TextDelta("Handed over OK")
        yield Done("stop")

    wrapped_stream = replay.wrap(real)
    [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "buy"}], tools, stream_fn=wrapped_stream)]

    assert len(real_requests) == 1
    assert not any(name == "confirm_purchase" for name, _ in calls_made)
    assert "over cap limit" in replay.why


# --------------------------------------------------------------------------- Wiring tests
class FakeServerSession:
    def __init__(self, tab="buy:1"):
        self.tab = tab
        self._url = SHOP
        self._changed = True
        self._purchase = False
        self._ref_words = {
            tab: {
                "e1": 'button "구매하기"',
                "e2": 'button "결제하기"',
            }
        }
        self.tabs = {tab}
        self.control_listeners = []

    def current_url(self):
        return self._url

    def current_host(self):
        return "shop.example.com"

    def ref_words_of(self, tab):
        return dict(self._ref_words.get(tab, {}))

    def last_click_changed_nothing(self, tab):
        return not self._changed

    def purchase_active(self):
        return self._purchase

    def has_tab(self, key):
        return key in self.tabs

    async def close_tab(self, key):
        self.tabs.discard(key)
        return True

    async def close(self):
        pass

    def touch(self, key):
        pass

    async def open(self, url, view=""):
        return 'link "로그아웃" [ref=e1]\nlink "마이페이지" [ref=e2]'

    def hold_for_user(self):
        pass


@pytest.mark.asyncio
async def test_start_fast_buy_wiring_stream_wrap_and_save_plan(tmp_path, monkeypatch):
    tab = "buy:1"
    server_session = FakeServerSession(tab)

    async def _stub(a):
        return "ok"

    stubs = [
        Tool("browser_open", "open", {}, _stub),
        Tool("browser_click", "click", {}, _stub),
        Tool("browser_type", "type", {}, _stub),
        Tool("browser_fill_shipping", "ship", {}, _stub),
        Tool("browser_fill_card", "card", {}, _stub),
        Tool("browser_login", "login", {}, _stub),
        Tool("confirm_purchase", "confirm", {}, _stub),
        Tool("end_order", "end", {}, _stub),
    ]

    async def fn(provider, request, **kw):
        yield TextDelta("ok")
        yield Done("stop")

    app = create_app(
        Settings(token=TOKEN, provider="p", model="m", cdp_host=None, db_path=str(tmp_path / "w.db"), scheduler=False),
        tools=stubs,
        stream_fn=fn,
        browser=server_session,
    )

    real = server_mod.run_turn
    calls = []
    simulate_actions = [True]

    def run_turn_fake(ctx, provider, model, messages, observe=None, *args, **kw):
        calls.append({
            "source": kw.get("source"),
            "owner": kw.get("owner"),
            "cond": kw.get("cond"),
            "stream_wrap": kw.get("stream_wrap"),
            "tools": kw.get("tools_") or [],
            "messages": messages,
        })
        if not str(kw.get("source") or "").startswith("buy:"):
            return real(ctx, provider, model, messages, observe, *args, **kw)

        done = asyncio.Event()
        turn = SimpleNamespace(ended=done, final_status="done", final_error=None, task_id=7777, quiet_end=False)
        calls[-1]["turn"] = turn

        async def worker():
            if simulate_actions[0]:
                by_name = {t.name: t for t in (kw.get("tools_") or [])}
                await by_name["browser_click"].handler({"selector": "e1"})
                server_session._purchase = True
                await by_name["confirm_purchase"].handler({})
                await by_name["browser_click"].handler({"selector": "e2"})

            stream_wrap = kw.get("stream_wrap")
            if stream_wrap is not None:
                replay_inst = getattr(stream_wrap, "__self__", None)
                if replay_inst is not None and not simulate_actions[0]:
                    replay_inst.why = "something stopped"

            gid = app.state.schedule_grants.list(kind="purchase")[0]["id"]
            app.state.buy_ready.store._run(
                "INSERT INTO wallet_payments(card_id, amount, currency, host, kind, ts, month, grant_id, task_id, summary, site) "
                "VALUES('c', 50000, 'KRW', 'shop.example.com', 'auto', 1, '2026-11', ?, ?, '', 'example.com')",
                (gid, turn.task_id))

            if observe is not None:
                observe(None)
            done.set()

        asyncio.create_task(worker())
        return turn

    monkeypatch.setattr(server_mod, "run_turn", run_turn_fake)

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        made = (await c.post("/api/watchers", json={"name": "Sneaker", "url": SHOP, "mode": "text_appears", "match": "in stock"})).json()
        wid = made["id"]
        server_session.tab = f"buy:{wid}"
        server_session._ref_words = {f"buy:{wid}": {"e1": 'button "구매하기"', "e2": 'button "결제하기"'}}

        app.state.schedule_grants.add_purchase(None, "shop.example.com", "sneaker", 100000, "KRW", scope=f"watcher:{wid}", max_count=5)
        await app.state.buy_ready.arm(wid)

        # 1. First run without plan: stream_wrap must be None
        app.state.watchers.on_fire(app.state.watchers.get(wid), {"detail": "in stock", "url": SHOP})
        await until(lambda: any(x["source"] == f"buy:{wid}" for x in calls))
        first_call = next(x for x in calls if x["source"] == f"buy:{wid}")
        assert first_call["stream_wrap"] is None

        # Because the run paid and recorder was usable, plan must now be saved!
        plans = app.state.buy_ready.plans
        saved = plans.find(SHOP)
        assert saved is not None
        assert len(saved["steps"]) == 3

        # The watcher's buy.remembered is now True
        view = app.state.buy_ready.view(wid)
        assert view["remembered"] is True

        # 2. Second run: plan exists -> stream_wrap is passed to run_turn
        calls.clear()
        app.state.watchers.on_fire(app.state.watchers.get(wid), {"detail": "in stock", "url": SHOP})
        await until(lambda: any(x["source"] == f"buy:{wid}" for x in calls))
        second_call = next(x for x in calls if x["source"] == f"buy:{wid}")
        assert second_call["stream_wrap"] is not None

        # 3. Third run: replay that stopped counts a fail
        calls.clear()
        simulate_actions[0] = False  # replay will stop
        app.state.watchers.on_fire(app.state.watchers.get(wid), {"detail": "in stock", "url": SHOP})
        await until(lambda: any(x["source"] == f"buy:{wid}" for x in calls))
        assert plans.find(SHOP)["fails"] == 1
