"""The checkout's last steps that need the user (tools/payment_step.py, payment_steps.py, payment_pins.py), against a fake shop served
from memory in a real Chromium (skipped without Playwright's browser), with a scripted model. No real site, money or model API.

1. An "approve in your card app" step with a code, a QR code and a countdown: ONE card event; "approved" -> the completion click goes
   through on the approval already given; the time runs out -> "get a new code" presses the page's own button and the card follows the
   new code; "cancel" -> nothing is paid.
2. A saved payment PIN: typed once on a keypad whose buttons say their digits (shuffled); never in an event, a tool result, the vault log
   or a log line; refused outside the purchase window, on another site and a second time. A keypad without digit text is shown in the
   card instead, and the user's taps there (forwarded to the same spots of the page) enter the PIN.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets
import time
from typing import Any
from urllib.parse import parse_qs

import pytest
import segno

from assistant.agent import ToolResult, current_approval, run_agent, ui_sink
from assistant.payment_pins import PaymentLedger, PaymentPaths, PaymentPins, PinDeliveries, PinError, pin_site
from assistant.payment_steps import PaymentSteps, StepError
from assistant.providers import Done, TextDelta, ToolCall
from assistant.store import Store
from assistant.tools import payment_step as ps
from assistant.tools.browser import BrowserSession, browser_owner, browser_tools
from assistant.tools.payment_step import classify, payment_step_tools, seconds_left
from assistant.tools.purchase import purchase_tools
from assistant.wallet import Wallet
from assistant.workers import current_turn

pw = pytest.importorskip("playwright.async_api")

SHOP = "https://www.mugshop.example"
OTHER = "https://www.othershop.example"
PIN = "731946"
QR = segno.make("https://pay.example/approve/ab12").png_data_uri(scale=5)


def public(host, port):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def page(body: str, title: str = "결제") -> str:
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8><title>{title}</title><style>body{{font-family:sans-serif}}"
            f"button{{font-size:15px;margin:3px}}</style></head><body>{body}</body></html>")


class Shop:
    def __init__(self) -> None:
        self.mode = "app"          # what follows the pay button: app | pin_text | pin_image
        self.code = "482913"
        self.seconds = 179
        self.app_ok = False        # the user approved in their (fake) card app
        self.orders: list[dict[str, Any]] = []
        self.pin_got: list[str] = []

    def approve_page(self) -> str:
        c = self.code
        return page(f"""<h1>카드사 앱 인증</h1><p>주문: 블루 머그 x2 · 총 결제 금액 24,000원</p>
<p>휴대폰의 카드사 앱에서 승인해 주세요. 앱에 표시된 승인번호가 아래 번호와 같은지 확인하세요.</p>
<div><div class="auth-code" style="font-size:34px;font-weight:bold">{c[:3]} {c[3:]}</div></div>
<img alt="QR 코드" src="{QR}" width="180" height="180">
<p class="timer">남은 시간 <span id="t"></span></p><p id="exp" style="display:none">인증 시간이 만료되었습니다. 다시 요청해 주세요.</p>
<form method="post" action="/resend"><button type="submit">인증번호 재요청</button></form>
<form method="post" action="/complete"><button type="submit">결제 완료</button></form>
<script>let left={self.seconds};const show=()=>{{const m=String(Math.floor(left/60)).padStart(2,'0'),s=String(left%60).padStart(2,'0');
document.getElementById('t').textContent=m+':'+s;if(left<=0){{document.getElementById('exp').style.display='block';}}}};show();
setInterval(()=>{{if(left>0)left--;show();}},1000);</script>""")

    def pin_text_page(self) -> str:
        ds = list("0123456789")
        random.shuffle(ds)
        keys = "".join(f"<button type=button class=k onclick=\"press('{d}')\">{d}</button>" for d in ds)
        return page(f"""<h1>결제 비밀번호</h1><p>결제 비밀번호 6자리를 입력해 주세요.</p><div id=dots></div>
<div id=pad style="display:grid;grid-template-columns:repeat(4,56px);gap:4px">{keys}<button type=button onclick="v=v.slice(0,-1);dots()">←</button></div>
<form id=f method=post action=/pin><input type=hidden name=v id=v></form>
<script>let v='';const dots=()=>document.getElementById('dots').textContent='●'.repeat(v.length);
function press(d){{v+=d;dots();if(v.length===6){{document.getElementById('v').value=v;document.getElementById('f').submit();}}}}</script>""")

    def pin_image_page(self) -> str:
        return page("""<h1>결제 비밀번호</h1><p>결제 비밀번호 6자리를 입력해 주세요.</p><div id=dots></div>
<div id=pad style="display:grid;grid-template-columns:repeat(4,64px);gap:6px;width:280px"></div>
<form id=f method=post action=/pin><input type=hidden name=v id=v></form>
<script>let v='';let order=[];
function draw(){const pad=document.getElementById('pad');pad.innerHTML='';const ds=[...'0123456789'];ds.sort(()=>Math.random()-.5);
 order=[...ds.slice(0,9),'x',ds[9],'y'];
 order.forEach((k)=>{const c=document.createElement('canvas');c.width=64;c.height=48;const g=c.getContext('2d');g.fillStyle='#334';
  g.fillRect(0,0,64,48);if(k.length===1&&k!=='x'&&k!=='y'){g.fillStyle='#fff';g.font='bold 26px sans-serif';g.textAlign='center';
  g.textBaseline='middle';g.fillText(k,32,25);}const im=document.createElement('img');im.src=c.toDataURL();im.style.cursor='pointer';
  im.dataset.k=k;im.onclick=()=>{if(k!=='x'&&k!=='y'){v+=k;document.getElementById('dots').textContent='●'.repeat(v.length);draw();
  if(v.length===6){document.getElementById('v').value=v;document.getElementById('f').submit();}}};pad.appendChild(im);});}
draw();
window.keyAt=(d)=>{const e=[...document.querySelectorAll('#pad img')].find((i)=>i.dataset.k===d);const r=e.getBoundingClientRect();
 return {x:r.left+r.width/2,y:r.top+r.height/2};};</script>""")

    def respond(self, method: str, path: str, form: dict[str, str]) -> str:
        if path == "/checkout":
            return page("<h1>결제</h1><p>블루 머그 x2 · 총 결제 금액 24,000원</p><form method=post action=/pay><button type=submit>결제하기</button></form>")
        if path == "/pay" and method == "POST":
            return {"app": self.approve_page, "pin_text": self.pin_text_page, "pin_image": self.pin_image_page}[self.mode]()
        if path == "/approve":
            return self.approve_page()
        if path == "/resend" and method == "POST":
            self.code, self.seconds = f"{random.randint(100000, 999999)}", 120
            return self.approve_page()
        if path == "/complete" and method == "POST":
            if not self.app_ok:
                return page("<p>아직 카드사 앱에서 승인되지 않았습니다.</p>")
            self.orders.append({"no": f"A{1001 + len(self.orders)}", "at": time.time()})
            return page(f"<h1>결제가 완료되었습니다</h1><p>주문번호 {self.orders[-1]['no']}</p>", "완료")
        if path == "/pin" and method == "POST":
            self.pin_got.append(form.get("v", ""))
            self.orders.append({"no": f"A{1001 + len(self.orders)}", "at": time.time()})
            return page(f"<h1>결제가 완료되었습니다</h1><p>주문번호 {self.orders[-1]['no']}</p>", "완료")
        return page("<p>other shop</p>")


@pytest.fixture
async def ctx():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context(locale="ko-KR")
        yield c
        await b.close()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ps, "POLL_SECONDS", 0.25)
    monkeypatch.setattr(ps, "WAIT_SECONDS", 40.0)


class Env:
    def __init__(self, ctx_page: Any, shop: Shop, store: Store | None = None):
        self.page, self.shop = ctx_page, shop
        self.store = store or Store(":memory:")

        async def connect():
            return ctx_page
        self.session = BrowserSession(connect=connect, resolver=public)
        self.wallet = Wallet(self.store)
        self.pins = PaymentPins(self.store)
        self.pin_deliveries = PinDeliveries()
        self.ledger = PaymentLedger()
        self.paths = PaymentPaths(self.store)
        self.steps = PaymentSteps()
        self.pads: dict[str, Any] = {}
        self.tools = [*browser_tools(self.session),
                      *purchase_tools(self.session, self.wallet, None, pins=self.pins, pin_deliveries=self.pin_deliveries,
                                      ledger=self.ledger, paths=self.paths),
                      *payment_step_tools(self.session, self.steps, self.ledger, pins=self.pins, pin_deliveries=self.pin_deliveries,
                                          paths=self.paths, vault_locked=self.store.vault_locked, pads=self.pads)]
        self.by_name = {t.name: t for t in self.tools}
        self.ui: list[dict[str, Any]] = []
        self.results: list[ToolResult] = []
        self.cards: list[str] = []
        self.on_step = lambda ev: None       # the user's side: answers the card
        self.phone_sends_pin = True

    async def route(self, route):
        req = route.request
        from urllib.parse import urlsplit
        u = urlsplit(req.url)
        form = {k: v[0] for k, v in parse_qs(req.post_data or "").items()} if req.method == "POST" else {}
        body = self.shop.respond(req.method, u.path, form) if u.hostname == "www.mugshop.example" else page("<p>other shop</p>")
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    async def run(self, steps: list[tuple[str, dict[str, Any]]], allow=lambda req: True) -> None:
        await self.page.context.route("https://**/*", self.route)
        loop = asyncio.get_running_loop()

        async def approve(req):
            ok = bool(allow(req))
            approval_id = "ap" + secrets.token_hex(4)
            current_approval.set((req.id, approval_id))
            tool = self.by_name.get(req.name)
            extra = dict(tool.approval_extra(approval_id, req.arguments) or {}) if tool and tool.approval_extra else {}
            self.cards.append(req.name)
            if ok and (extra.get("pin") or {}).get("needs_pin") and self.phone_sends_pin:   # the phone, after fingerprint
                loop.call_later(0.05, lambda: self.pin_deliveries.deliver(approval_id, {"pin_id": extra["pin"]["id"], "pin": PIN}))
            return ok

        async def sink(item):
            self.ui.append(json.loads(json.dumps(item)))
            if item.get("type") == "payment_step":
                self.on_step(item)

        turns = [[ToolCall(f"c{i}", n, json.dumps(a, ensure_ascii=False)), Done("tool_calls")] for i, (n, a) in enumerate(steps)]
        turns.append([TextDelta("done"), Done("stop")])
        it = iter(turns)

        async def model(provider, request, **kw):
            for ev in next(it, [TextDelta("."), Done("stop")]):
                yield ev
        current_turn.set({"turn_id": "t1", "task_id": 1, "source": "chat", "unattended": False})
        ui_sink.set(sink)
        async for ev in run_agent("p", "m", [{"role": "user", "content": "buy it"}], self.tools, approve=approve, max_steps=30,
                                  stream_fn=model):
            if isinstance(ev, ToolResult):
                self.results.append(ev)

    def result(self, name: str, nth: int = 0) -> ToolResult:
        return [r for r in self.results if r.name == name][nth]

    def step_events(self) -> list[dict[str, Any]]:
        return [e for e in self.ui if e.get("type") == "payment_step"]


ORDER = {"summary": "블루 머그 x2", "amount": 24000, "currency": "KRW", "site": "www.mugshop.example", "pay_with": "카드사 앱",
         "items": [{"name": "블루 머그", "quantity": 2, "price": "24,000원"}]}
TO_PAY = [("browser_open", {"url": f"{SHOP}/checkout"}), ("confirm_purchase", ORDER),
          ("browser_click", {"selector": "button:has-text('결제하기')"})]
FINISH = ("browser_click", {"selector": "button:has-text('결제 완료')"})


def save_pin(env: Env, site: str = "mugshop.example") -> dict[str, Any]:
    pin, _ = env.pins.save({"site": site, "label": "머그 가게 결제", "digits": 6, "e2e": {"blob": "QUJDREVGR0g=", "alg": "A256GCM-AKS-1",
                                                                                       "key_id": "android-k"}})
    return pin


# ---------------------------------------------------------------------------------------------------------------- pure helpers
def test_countdown_and_kind_readings():
    assert seconds_left(["남은 시간 02:59"]) == 179
    assert seconds_left(["유효시간 2분 30초"]) == 150
    assert seconds_left(["3분 이내에 승인해 주세요"]) == 180
    assert seconds_left(["Expires in 4 min 10 s"]) == 250
    assert seconds_left(["90 seconds left"]) == 90
    assert seconds_left(["오후 3:15까지"]) is None and seconds_left([]) is None
    assert classify({"text": "카드사 앱에서 승인해 주세요", "code": None, "qr": False, "timers": []}) == "app"
    assert classify({"text": "Confirm with your passkey", "code": None, "qr": False, "timers": []}) == "passkey"
    assert classify({"text": "Enter the verification code we sent", "otp_box": True, "code": None, "qr": False, "timers": []}) == "otp"
    assert classify({"text": "Thank you for your order", "code": None, "qr": False, "timers": []}) == ""


def test_pin_store_keeps_only_ciphertext_and_the_slot_is_single_use():
    store = Store(":memory:")
    pins = PaymentPins(store)
    with pytest.raises(PinError):
        pins.save({"site": "mugshop.example", "digits": 6, "pin": PIN, "e2e": {"blob": "QQ=="}})
    p, replaced = pins.save({"site": "https://pay.mugshop.example/x", "digits": 6, "e2e": {"blob": "QUJD", "alg": "a", "key_id": "k"}})
    assert p["site"] == "mugshop.example" and not replaced and pins.for_host("www.mugshop.example")["id"] == p["id"]
    assert pin_site("shop.example.co.kr") == "example.co.kr"
    d = PinDeliveries()
    d.expect("ap1", p, "www.mugshop.example")
    with pytest.raises(PinError):
        d.deliver("ap1", {"pin": "12a4"})
    with pytest.raises(PinError):
        d.deliver("ap1", {"pin": "1234"})                  # not the saved length
    d.deliver("ap1", {"pin": PIN})
    with pytest.raises(PinError) as e:
        d.deliver("ap1", {"pin": PIN})
    assert e.value.status == 409
    got = asyncio.run(d.wait("ap1", 1))
    assert got is not None and "".join(got.digits()) == PIN and PIN not in repr(got)
    assert asyncio.run(d.wait("ap1", 0.1)) is None             # once
    assert all(PIN not in json.dumps(r) for r in store.vault_log(50))


def test_steps_registry_answers():
    steps = PaymentSteps()
    ev = steps.open({"kind": "app", "site": "x", "code": "123456"})
    with pytest.raises(StepError):
        steps.answer(ev["step_id"], {"answer": "pay"})
    with pytest.raises(StepError):
        steps.answer(ev["step_id"], {"answer": "code", "code": "12 34 <x>"})
    assert steps.answer(ev["step_id"], {"answer": "approved"})["ok"]
    closed = steps.close(ev["step_id"], "approved")
    assert closed["state"] == "approved" and "code" not in closed
    with pytest.raises(StepError):
        steps.answer(ev["step_id"], {"answer": "cancel"})


# ---------------------------------------------------------------------------------------------------------------- 1. app approval
async def test_app_approval_card_then_the_completion_click(ctx):
    shop = Shop()
    env = Env(await ctx.new_page(), shop)

    def user(ev):
        if ev["state"] == "waiting" and ev.get("code"):
            shop.app_ok = True                                   # approved in the card app
            asyncio.get_running_loop().call_later(0.3, lambda: env.steps.answer(ev["step_id"], {"answer": "approved"}))
    env.on_step = user
    await env.run([*TO_PAY, ("payment_step_wait", {"instruction": "카드사 앱에서 결제를 승인해 주세요"}), FINISH])
    first = env.step_events()[0]
    assert first["kind"] == "app" and first["code"] == "482913" and first["code_shown"] == "482 913"
    assert first["qr"].startswith("data:image/png;base64,") and first["urgent"] is True
    assert 170 <= first["expires_at"] - time.time() <= 181 and first["seconds_total"] in (178, 179)
    assert first["instruction"] == "카드사 앱에서 결제를 승인해 주세요" and first["can_resend"] is True
    assert first["order"]["amount_text"] and first["order"]["site"] == "mugshop.example" and first["order"]["items"][0]["name"] == "블루 머그"
    assert env.step_events()[-1]["state"] == "approved"
    assert "approved the payment" in env.result("payment_step_wait").output
    assert env.cards == ["confirm_purchase"]                     # the completion click needed no second card
    assert len(shop.orders) == 1 and not env.result("browser_click", 1).is_error
    assert not env.steps.open_for("owner")


async def test_expired_code_gets_a_new_one_then_approval(ctx):
    shop = Shop()
    shop.seconds = 2
    env = Env(await ctx.new_page(), shop)
    seen: list[str] = []

    def user(ev):
        seen.append(ev["state"])
        if ev["state"] == "expired" and "asked" not in seen:
            seen.append("asked")
            env.steps.answer(ev["step_id"], {"answer": "new_code"})
        elif ev["state"] == "waiting" and ev.get("code") and ev["code"] != "482913" and "approved" not in seen:
            seen.append("approved")
            shop.app_ok = True
            env.steps.answer(ev["step_id"], {"answer": "approved"})
    env.on_step = user
    await env.run([*TO_PAY, ("payment_step_wait", {}), FINISH])
    assert "expired" in seen and "renewing" in seen and "approved" in seen
    renewed = [e for e in env.step_events() if e.get("code") not in (None, "482913")]
    assert renewed and renewed[0]["seconds_total"] in (119, 120)
    assert len(shop.orders) == 1 and env.cards == ["confirm_purchase"]


async def test_cancel_means_no_payment(ctx):
    shop = Shop()
    env = Env(await ctx.new_page(), shop)

    def user(ev):
        if ev["state"] == "waiting":
            shop.app_ok = True                                   # even approved in the app: the card's Cancel wins
            env.steps.answer(ev["step_id"], {"answer": "cancel"})
    env.on_step = user
    await env.run([*TO_PAY, ("payment_step_wait", {}), FINISH], allow=lambda req: req.name == "confirm_purchase")
    assert "CANCELLED" in env.result("payment_step_wait").output
    assert env.step_events()[-1]["state"] == "cancelled" and "code" not in env.step_events()[-1]
    assert env.cards == ["confirm_purchase", "browser_click"]  # the completion click asked again, and was denied
    assert shop.orders == []


async def test_step_needs_an_approved_payment_first(ctx):
    env = Env(await ctx.new_page(), Shop())
    await env.page.context.route("https://**/*", env.route)
    await (await env.session.page()).goto(f"{SHOP}/approve")
    assert "no approved payment" in env.by_name["payment_step_wait"].validate({})


# ---------------------------------------------------------------------------------------------------------------- 2. payment PIN
async def test_pin_typed_on_a_text_keypad_once_and_never_shown(ctx, caplog):
    caplog.set_level(logging.DEBUG)
    shop = Shop()
    shop.mode = "pin_text"
    env = Env(await ctx.new_page(), shop)
    save_pin(env)
    await env.run([*TO_PAY, ("browser_type_payment_pin", {}), ("browser_type_payment_pin", {}), ("browser_read", {})])
    assert shop.pin_got == [PIN] and len(shop.orders) == 1
    first, second = env.result("browser_type_payment_pin"), env.result("browser_type_payment_pin", 1)
    assert "Entered the saved payment PIN (6 digits)" in first.output and not first.is_error
    assert "already entered once" in second.output
    everything = json.dumps(env.ui, ensure_ascii=False) + "".join(r.output for r in env.results) + json.dumps(env.store.vault_log(100))
    everything += caplog.text
    assert PIN not in everything
    log = [r["action"] for r in env.store.vault_log(100)]
    assert "pin-typed" in log and "pin-saved" in log
    needed = [e for e in env.ui if e.get("type") == "pin_needed"]   # the row of the release that came with the approval's Allow
    assert len(needed) == 1 and needed[0]["urgent"] is False and needed[0]["pin"]["digits"] == 6


async def test_pin_refused_outside_the_window_on_another_site_and_without_release(ctx):
    shop = Shop()
    shop.mode = "pin_text"
    env = Env(await ctx.new_page(), shop)
    save_pin(env)
    env.phone_sends_pin = False
    await env.run(TO_PAY)
    tool = env.by_name["browser_type_payment_pin"]
    assert tool.validate({}) is None                              # released slot is waiting for the phone
    env.session.end_purchase()
    assert "ran out" in tool.validate({})
    env.session.allow_purchase("www.mugshop.example")
    await (await env.session.page()).goto(f"{OTHER}/checkout")
    assert "no approved payment" in tool.validate({})
    assert any(r["action"] == "pin-refused" for r in env.store.vault_log(50))


async def test_keypad_without_digit_text_is_entered_by_the_user_in_the_card(ctx):
    shop = Shop()
    shop.mode = "pin_image"
    env = Env(await ctx.new_page(), shop)
    save_pin(env)
    loop = asyncio.get_running_loop()

    async def user_taps(step_id: str):
        await asyncio.sleep(0.3)
        pad = env.pads[step_id]
        jpeg, w, h = await pad.shot()
        assert jpeg[:2] == b"\xff\xd8" and w > 100 and h > 100
        for d in PIN:
            p = env.page
            _frame, r = await pad._region(p)
            at = await p.evaluate("(d) => window.keyAt(d)", d)   # the person sees the digit on the picture and taps it
            await pad.tap((at["x"] - r["x"]) / r["width"], (at["y"] - r["y"]) / r["height"], "phone-1")
            await asyncio.sleep(0.15)

    def user(ev):
        if ev["kind"] == "pin_pad" and ev["state"] == "waiting" and ev.get("taps") == 0 and "tapping" not in env.__dict__:
            env.__dict__["tapping"] = loop.create_task(user_taps(ev["step_id"]))
    env.on_step = user
    await env.run([*TO_PAY, ("browser_type_payment_pin", {}), ("browser_read", {})])
    out = env.result("browser_type_payment_pin").output
    assert "moved past the PIN" in out and shop.pin_got == [PIN] and len(shop.orders) == 1
    states = [e["state"] for e in env.step_events()]
    assert states[0] == "waiting" and "finishing" in states
    assert PIN not in json.dumps(env.ui) + "".join(r.output for r in env.results)
    assert env.session.controlled_by("owner") is None           # the tab went back to the agent
    assert env.pads == {}
    assert "pin-handed" in [r["action"] for r in env.store.vault_log(50)]
    # next time on this site, the agent is told which way avoided the keypad
    env.paths.record("www.mugshop.example", "card_form", "smooth")
    assert "met a keypad" in env.paths.hint("mugshop.example") and "card form" in env.paths.hint("mugshop.example")


async def test_pad_taps_stay_inside_the_region(ctx):
    shop = Shop()
    shop.mode = "pin_image"
    env = Env(await ctx.new_page(), shop)
    await env.page.context.route("https://**/*", env.route)
    page_ = await env.session.page()
    await page_.goto(f"{SHOP}/checkout")
    await page_.click("text=결제하기")
    assert await ps.mark_pad(page_)
    pad = ps.PinPad(env.session, browser_owner.get(), "mugshop.example")
    with pytest.raises(Exception):
        await pad.tap(1.4, 0.5, "phone-1")
    await pad.close()
    with pytest.raises(Exception):
        await pad.shot()


# ---------------------------------------------------------------------------------------------------------------- tasks allowed at setup
class _Check:
    def __init__(self, grant):
        self.grant, self.reason, self.note = grant, "", ""


class FakeGrants:
    """A task's standing purchase permission (schedule_grants.ConditionalGrants' surface purchase_tools uses)."""

    def __init__(self, use_pin: bool):
        self.g = {"id": 7, "schedule_id": 3, "scope": "schedule", "scope_ref": 3, "task_name": "머그 재주문", "max_amount_text": "30,000원",
                  "use_pin": use_pin}

    def purchase_check(self, *a, **k):
        return _Check(self.g)

    def grant_card(self, g):
        return None

    def use_purchase(self, *a, **k):
        pass


async def run_grant(use_pin: bool, unattended: bool = True):
    store = Store(":memory:")
    pins, deliveries, ledger = PaymentPins(store), PinDeliveries(), PaymentLedger()
    pins.save({"site": "mugshop.example", "digits": 6, "e2e": {"blob": "QUJD", "alg": "a", "key_id": "k"}})
    asked: list[dict] = []
    deliveries.on_request = asked.append
    tools = {t.name: t for t in purchase_tools(None, None, None, grants=FakeGrants(use_pin), pins=pins, pin_deliveries=deliveries,
                                               ledger=ledger)}
    ui: list[dict] = []

    async def sink(item):
        ui.append(item)
    current_turn.set({"turn_id": "t9", "task_id": 9, "source": "schedule:3", "unattended": unattended})
    ui_sink.set(sink)
    t = tools["confirm_purchase"]
    assert t.auto_approve(ORDER) is True                    # the task's permission: no approval card
    out = await t.handler(ORDER)
    return out, ui, asked, ledger.last("owner", "www.mugshop.example"), deliveries


async def test_task_run_gets_the_pin_only_when_allowed_and_only_through_the_phone():
    out, ui, asked, paid, deliveries = await run_grant(use_pin=True)
    needed = [e for e in ui if e.get("type") == "pin_needed"]
    assert needed and needed[0]["urgent"] is True and asked and asked[0]["slot_id"] == paid.pin_slot
    assert "phone releases it" in out and not deliveries.arrived(paid.pin_slot)   # nothing usable until the phone releases it
    out, ui, asked, paid, _ = await run_grant(use_pin=False)
    assert not [e for e in ui if e.get("type") == "pin_needed"] and not asked and paid.pin_slot == ""
    assert "not released in this run" in out
