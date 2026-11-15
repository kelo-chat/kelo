"""A small fake web shop for the order tests: no real site, no real money.

Served for real over https on 127.0.0.1 (uvicorn in a thread with a throw-away certificate, `ShopServer`); the test browser reaches it
under made-up https addresses (`browser_args` maps the names to it), so the agent's browser guards see ordinary public https sites. The Host decides the shop:

  www.mugshop.example     a Korean-style shop (ko): 구매하기 -> 장바구니 -> 주문하기 -> 로그인 -> 배송지 (받는 분, 연락처, 우편번호 + 우편번호 찾기,
                          주소, 상세주소, 배송 메모) and a coupon -> 다음 -> 결제 수단 + 카드 -> 결제하기 -> 주문 완료
  www.usmugs.example      a US-style shop (en): Buy now -> Cart -> Checkout -> Sign in -> shipping form with autocomplete tokens (full name,
                          address line 1/2, city, state list, ZIP, country list, phone) and a coupon -> Continue to payment -> Payment
                          method + card -> Place order -> Order complete
  www.othershop.example   another shop with a "결제하기" button on its product page (a pay click on another site)

Each shop has accounts (`Shop.accounts`: user -> {"password", "address" (a saved address or None)}), sign-up, an account settings page,
a review form, deleting a cart line, and it records the orders placed (`Shop.orders`) so a test can check that exactly one was placed.
"""

from __future__ import annotations

import html
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

PRICE = {"mug": 12000, "mug_us": 12.0}
US_STATES = [("CA", "California"), ("NY", "New York"), ("TX", "Texas"), ("WA", "Washington")]


@dataclass
class Session:
    user: str = ""
    cart: list[dict[str, Any]] = field(default_factory=list)
    shipping: dict[str, str] = field(default_factory=dict)
    coupon: str = ""


@dataclass
class Shop:
    port: int = 0   # where ShopServer serves it (a free port picked at start)
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)
    signups: list[str] = field(default_factory=list)
    readonly_postcode: bool = False      # the Korean shop's postal code and address fields are filled by its postcode search only

    def reset(self) -> None:
        self.sessions.clear()
        self.orders.clear()
        self.reviews.clear()
        self.signups.clear()


def _e(s: Any) -> str:
    return html.escape(str(s))


def _page(lang: str, title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'><title>{_e(title)}</title>"
                        "<style>body{font-family:sans-serif;max-width:640px;margin:20px auto} label{display:block;margin:6px 0}"
                        "input,select,textarea{font-size:15px;padding:4px} button{font-size:15px;padding:6px 12px;margin:4px}</style>"
                        f"</head><body>{body}</body></html>")


def make_app(shop: Shop) -> Starlette:
    def host(request: Request) -> str:
        return (request.headers.get("x-shop-host") or request.url.hostname or "").lower()

    def kr(request: Request) -> bool:
        return "mugshop" in host(request) or "othershop" in host(request)

    def sess(request: Request) -> tuple[str, Session]:
        sid = request.cookies.get("sid") or ""
        if sid not in shop.sessions:
            sid = secrets.token_hex(8)
            shop.sessions[sid] = Session()
        return sid, shop.sessions[sid]

    def done(resp: Response, sid: str) -> Response:
        resp.set_cookie("sid", sid, httponly=True, samesite="lax", secure=True)
        return resp

    def go(path: str, sid: str) -> Response:
        return done(RedirectResponse(path, status_code=303), sid)

    async def form(request: Request) -> dict[str, str]:
        raw = (await request.body()).decode("utf-8", "replace")
        return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    async def index(request: Request) -> Response:
        sid, _ = sess(request)
        if "othershop" in host(request):
            return done(_page("ko", "다른 가게", "<h1>다른 가게</h1><a href='/product/1'>머그컵 보기</a>"), sid)
        title = "머그 가게" if kr(request) else "Mug Store"
        link = "블루 머그 보기" if kr(request) else "See the blue mug"
        return done(_page("ko" if kr(request) else "en", title, f"<h1>{title}</h1><a href='/product/1'>{link}</a>"), sid)

    async def product(request: Request) -> Response:
        sid, _ = sess(request)
        if "othershop" in host(request):
            return done(_page("ko", "머그컵 - 다른 가게", "<h1>머그컵</h1><p>9,900원</p><form method='post' action='/buy'>"
                                                       "<button type='submit'>바로 결제하기</button></form>"), sid)
        if kr(request):
            body = ("<h1>블루 머그</h1><p>12,000원</p><form method='post' action='/cart/add'>"
                    "<fieldset><legend>색상</legend><label><input type='radio' name='color' value='네이비' checked> 네이비</label>"
                    "<label><input type='radio' name='color' value='화이트'> 화이트</label></fieldset>"
                    "<label>수량 <input type='number' name='qty' value='1' min='1'></label>"
                    "<button type='submit' id='buy'>구매하기</button></form>"
                    "<h2>리뷰</h2><form method='post' action='/review'><textarea name='review'></textarea>"
                    "<button type='submit'>리뷰 등록</button></form>")
            return done(_page("ko", "블루 머그 - 머그 가게", body), sid)
        body = ("<h1>Blue mug</h1><p>$12.00</p><form method='post' action='/cart/add'>"
                "<fieldset><legend>Color</legend><label><input type='radio' name='color' value='Navy' checked> Navy</label>"
                "<label><input type='radio' name='color' value='White'> White</label></fieldset>"
                "<label>Quantity <input type='number' name='qty' value='1' min='1'></label>"
                "<button type='submit' id='buy'>Buy now</button></form>")
        return done(_page("en", "Blue mug - Mug Store", body), sid)

    async def cart_add(request: Request) -> Response:
        sid, s = sess(request)
        f = await form(request)
        s.cart.append({"item": "블루 머그" if kr(request) else "Blue mug", "color": f.get("color", ""), "qty": max(1, int(f.get("qty") or 1))})
        return go("/cart", sid)

    async def cart(request: Request) -> Response:
        sid, s = sess(request)
        rows = "".join(f"<li>{_e(c['item'])} ({_e(c['color'])}) x {c['qty']} "
                       f"<form method='post' action='/cart/delete' style='display:inline'><input type='hidden' name='i' value='{i}'>"
                       f"<button type='submit'>{'삭제' if kr(request) else 'Remove'}</button></form></li>" for i, c in enumerate(s.cart))
        if kr(request):
            body = f"<h1>장바구니</h1><ul>{rows}</ul><form method='post' action='/checkout/start'><button type='submit'>주문하기</button></form>"
            return done(_page("ko", "장바구니 - 머그 가게", body), sid)
        body = f"<h1>Your cart</h1><ul>{rows}</ul><form method='post' action='/checkout/start'><button type='submit'>Checkout</button></form>"
        return done(_page("en", "Cart - Mug Store", body), sid)

    async def cart_delete(request: Request) -> Response:
        sid, s = sess(request)
        f = await form(request)
        i = int(f.get("i") or 0)
        if 0 <= i < len(s.cart):
            s.cart.pop(i)
        return go("/cart", sid)

    async def checkout_start(request: Request) -> Response:
        sid, s = sess(request)
        return go("/checkout" if s.user else "/login?next=/checkout", sid)

    async def login_page(request: Request) -> Response:
        sid, _ = sess(request)
        nxt = _e(request.query_params.get("next") or "/")
        if kr(request):
            body = (f"<h1>로그인</h1><form method='post' action='/login?next={nxt}'><label>아이디 <input name='username' autocomplete='username'></label>"
                    "<label>비밀번호 <input type='password' name='password' autocomplete='current-password'></label>"
                    "<button type='submit'>로그인</button></form><a href='/signup'>회원가입</a>")
            return done(_page("ko", "로그인 - 머그 가게", body), sid)
        body = (f"<h1>Sign in</h1><form method='post' action='/login?next={nxt}'><label>Username <input name='username' autocomplete='username'></label>"
                "<label>Password <input type='password' name='password' autocomplete='current-password'></label>"
                "<button type='submit'>Sign in</button></form><a href='/signup'>Create account</a>")
        return done(_page("en", "Sign in - Mug Store", body), sid)

    async def login(request: Request) -> Response:
        sid, s = sess(request)
        f = await form(request)
        acct = shop.accounts.get(f.get("username", ""))
        if acct and acct["password"] == f.get("password"):
            s.user = f["username"]
            nxt = request.query_params.get("next") or "/"
            return go(nxt if nxt.startswith("/") else "/", sid)
        msg = "아이디 또는 비밀번호가 잘못되었습니다" if kr(request) else "Wrong username or password"
        return done(_page("ko" if kr(request) else "en", "Sign in", f"<p>{msg}</p><a href='/login'>back</a>"), sid)

    async def signup_page(request: Request) -> Response:
        sid, _ = sess(request)
        if kr(request):
            body = ("<h1>회원가입</h1><form method='post' action='/signup'><label>이메일 <input type='email' name='email'></label>"
                    "<label>비밀번호 <input type='password' name='password'></label><button type='submit'>가입하기</button></form>")
            return done(_page("ko", "회원가입 - 머그 가게", body), sid)
        body = ("<h1>Create account</h1><form method='post' action='/signup'><label>Email <input type='email' name='email'></label>"
                "<label>Password <input type='password' name='password'></label><button type='submit'>Create account</button></form>")
        return done(_page("en", "Create account - Mug Store", body), sid)

    async def signup(request: Request) -> Response:
        sid, s = sess(request)
        f = await form(request)
        shop.signups.append(f.get("email", ""))
        shop.accounts[f.get("email", "")] = {"password": f.get("password", ""), "address": None}
        s.user = f.get("email", "")
        return go("/checkout" if s.cart else "/", sid)

    async def checkout(request: Request) -> Response:
        sid, s = sess(request)
        if not s.user:
            return go("/login?next=/checkout", sid)
        saved = (shop.accounts.get(s.user) or {}).get("address")
        lines = "".join(f"<li>{_e(c['item'])} ({_e(c['color'])}) x {c['qty']}</li>" for c in s.cart)
        if kr(request):
            total = sum(c["qty"] for c in s.cart) * PRICE["mug"] - (3000 if s.coupon else 0)
            ro = " readonly" if shop.readonly_postcode else ""
            pick = (f"<label><input type='radio' name='addr_choice' value='saved'> 기본 배송지: {_e(saved['label'])} ({_e(saved['text'])})</label>"
                    "<label><input type='radio' name='addr_choice' value='new' checked> 새 배송지</label>") if saved else \
                "<input type='hidden' name='addr_choice' value='new'>"
            body = (f"<h1>주문서 작성</h1><ul>{lines}</ul><p>결제 예정 금액 {total:,}원</p>"
                    "<form method='post' action='/checkout/coupon'><label>쿠폰 코드 <input name='coupon_code'></label>"
                    "<button type='submit' id='apply-coupon'>쿠폰 적용</button></form>"
                    f"<form method='post' action='/checkout/shipping'><h2>배송지</h2>{pick}"
                    "<label>받는 분 <input name='receiver'></label>"
                    "<label>연락처 <input name='phone' placeholder='010-0000-0000'></label>"
                    f"<label>우편번호 <input name='zipcode'{ro}></label><button type='button'>우편번호 찾기</button>"
                    f"<label>주소 <input name='addr1'{ro}></label>"
                    "<label>상세주소 <input name='addr2'></label>"
                    "<label>배송 메모 <input name='memo'></label>"
                    "<button type='submit'>다음</button></form>")
            return done(_page("ko", "주문서 - 머그 가게", body), sid)
        total = sum(c["qty"] for c in s.cart) * PRICE["mug_us"] - (3.0 if s.coupon else 0)
        pick = (f"<label><input type='radio' name='addr_choice' value='saved'> Saved address: {_e(saved['label'])} ({_e(saved['text'])})</label>"
                "<label><input type='radio' name='addr_choice' value='new' checked> New address</label>") if saved else \
            "<input type='hidden' name='addr_choice' value='new'>"
        states = "".join(f"<option value='{k}'>{n}</option>" for k, n in US_STATES)
        body = (f"<h1>Checkout</h1><ul>{lines}</ul><p>Subtotal ${total:,.2f}</p>"
                "<form method='post' action='/checkout/coupon'><label>Promo code <input name='promo' id='promo'></label>"
                "<button type='submit' id='apply-coupon'>Apply</button></form>"
                f"<form method='post' action='/checkout/shipping'><h2>Shipping address</h2>{pick}"
                "<label>Full name <input name='ship_name' autocomplete='shipping name'></label>"
                "<label>Address <input name='ship_a1' autocomplete='shipping address-line1'></label>"
                "<label>Apt, suite <input name='ship_a2' autocomplete='shipping address-line2'></label>"
                "<label>City <input name='ship_city' autocomplete='shipping address-level2'></label>"
                f"<label>State <select name='ship_state' autocomplete='shipping address-level1'><option value=''>Select</option>{states}</select></label>"
                "<label>ZIP code <input name='ship_zip' autocomplete='shipping postal-code'></label>"
                "<label>Country <select name='ship_country' autocomplete='shipping country'><option value='US'>United States</option>"
                "<option value='CA'>Canada</option></select></label>"
                "<label>Phone <input type='tel' name='ship_phone' autocomplete='shipping tel'></label>"
                "<button type='submit'>Continue to payment</button></form>")
        return done(_page("en", "Checkout - Mug Store", body), sid)

    async def coupon(request: Request) -> Response:
        sid, s = sess(request)
        f = await form(request)
        s.coupon = f.get("coupon_code") or f.get("promo") or ""
        return go("/checkout", sid)

    async def shipping(request: Request) -> Response:
        sid, s = sess(request)
        f = await form(request)
        if f.get("addr_choice") == "saved":
            saved = (shop.accounts.get(s.user) or {}).get("address") or {}
            s.shipping = {"saved": saved.get("text", "")}
        else:
            s.shipping = {k: v for k, v in f.items() if k != "addr_choice"}
        return go("/payment", sid)

    async def payment(request: Request) -> Response:
        sid, s = sess(request)
        lines = "".join(f"<li>{_e(c['item'])} ({_e(c['color'])}) x {c['qty']}</li>" for c in s.cart)
        if kr(request):
            total = sum(c["qty"] for c in s.cart) * PRICE["mug"] - (3000 if s.coupon else 0)
            body = (f"<h1>결제</h1><ul>{lines}</ul><p>배송지: {_e(' '.join(s.shipping.values()))}</p><p>총 결제 금액 {total:,}원</p>"
                    "<form method='post' action='/payment'><h2>결제 수단</h2>"
                    "<label>카드 번호 <input autocomplete='cc-number' name='cardno'></label>"
                    "<label>유효기간 <input autocomplete='cc-exp' placeholder='MM/YY' name='exp'></label>"
                    "<label>CVC <input autocomplete='cc-csc' name='cvc'></label>"
                    "<button type='submit'>결제하기</button></form>")
            return done(_page("ko", "결제 - 머그 가게", body), sid)
        total = sum(c["qty"] for c in s.cart) * PRICE["mug_us"] - (3.0 if s.coupon else 0)
        body = (f"<h1>Payment</h1><ul>{lines}</ul><p>Ship to: {_e(' '.join(s.shipping.values()))}</p><p>Total ${total:,.2f}</p>"
                "<form method='post' action='/payment'><h2>Payment method</h2>"
                "<label>Card number <input autocomplete='cc-number' name='cardno'></label>"
                "<label>Expiry <input autocomplete='cc-exp' placeholder='MM / YY' name='exp'></label>"
                "<label>CVC <input autocomplete='cc-csc' name='cvc'></label>"
                "<button type='submit'>Place order</button></form>")
        return done(_page("en", "Payment - Mug Store", body), sid)

    async def pay(request: Request) -> Response:
        sid, s = sess(request)
        f = await form(request)
        no = f"A{len(shop.orders) + 1001}"
        shop.orders.append({"no": no, "host": host(request), "user": s.user, "cart": list(s.cart), "shipping": dict(s.shipping),
                            "coupon": s.coupon, "card_last4": (f.get("cardno") or "")[-4:], "at": time.time()})
        s.cart = []
        return go(f"/complete/{no}", sid)

    async def complete(request: Request) -> Response:
        sid, _ = sess(request)
        no = _e(request.path_params["no"])
        if kr(request):
            return done(_page("ko", "주문 완료 - 머그 가게", f"<h1>주문이 완료되었습니다</h1><p>주문번호 {no}</p>"), sid)
        return done(_page("en", "Order complete - Mug Store", f"<h1>Thank you, your order is placed</h1><p>Order number {no}</p>"), sid)

    async def review(request: Request) -> Response:
        sid, _ = sess(request)
        f = await form(request)
        shop.reviews.append(f.get("review", ""))
        return go("/product/1", sid)

    async def account(request: Request) -> Response:
        sid, _ = sess(request)
        return done(_page("ko", "회원 정보 - 머그 가게", "<h1>회원 정보</h1><form method='post' action='/account'>"
                                                     "<label>이름 <input name='name'></label><button type='submit'>정보 수정</button></form>"), sid)

    async def other_buy(request: Request) -> Response:
        sid, _ = sess(request)
        shop.orders.append({"no": "X1", "host": host(request), "user": "", "cart": [], "shipping": {}, "coupon": "", "card_last4": "",
                            "at": time.time()})
        return done(_page("ko", "결제 완료", "<h1>결제 완료</h1>"), sid)

    return Starlette(routes=[
        Route("/", index), Route("/product/1", product), Route("/cart/add", cart_add, methods=["POST"]), Route("/cart", cart),
        Route("/cart/delete", cart_delete, methods=["POST"]), Route("/checkout/start", checkout_start, methods=["POST"]),
        Route("/login", login_page), Route("/login", login, methods=["POST"]), Route("/signup", signup_page),
        Route("/signup", signup, methods=["POST"]), Route("/checkout", checkout), Route("/checkout/coupon", coupon, methods=["POST"]),
        Route("/checkout/shipping", shipping, methods=["POST"]), Route("/payment", payment), Route("/payment", pay, methods=["POST"]),
        Route("/complete/{no}", complete), Route("/review", review, methods=["POST"]), Route("/account", account),
        Route("/account", account, methods=["POST"]), Route("/buy", other_buy, methods=["POST"]),
    ])


PORT = 0   # 0 = a free port the system picks when the shop starts (ShopServer.port then holds the real one); never a fixed port,
# so two test runs on the same machine (several agents) never collide
HOSTS = ("www.mugshop.example", "www.usmugs.example", "www.othershop.example")


def make_cert(folder: str) -> tuple[str, str]:
    """A self-signed certificate for the made-up shop names (the test browser ignores certificate errors)."""
    import datetime
    from pathlib import Path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]), critical=False).sign(key, hashes.SHA256()))
    kp, cp = Path(folder) / "shop-key.pem", Path(folder) / "shop-cert.pem"
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(kp), str(cp)


class ShopServer:
    """The shop over https on 127.0.0.1:`port` in a thread of this process; stopped with `stop()` (nothing outlives the test). The test
    browser maps the shop names to it (`browser_args`), so redirects, cookies and forms behave as on a real site."""

    def __init__(self, shop: Shop, port: int = PORT):
        import tempfile
        # the listening socket is bound here (port 0: a free one), so `port` is known before the browser is launched with it
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", port))
        self.shop, self.port = shop, self._sock.getsockname()[1]
        self._dir = tempfile.TemporaryDirectory(prefix="fake-shop-")
        key, cert = make_cert(self._dir.name)
        self.server = uvicorn.Server(uvicorn.Config(make_app(shop), host="127.0.0.1", port=self.port, log_level="warning", lifespan="off",
                                                    ssl_keyfile=key, ssl_certfile=cert))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self._sock]), daemon=True)

    def start(self) -> "ShopServer":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("the fake shop did not start")
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)
        self._sock.close()
        self._dir.cleanup()


def browser_args(port: int) -> list[str]:
    """Chromium flags that send the made-up shop names to the local shop (launch with these, and a context with ignore_https_errors)."""
    return ["--host-resolver-rules=" + ", ".join(f"MAP {h} 127.0.0.1:{port}" for h in HOSTS)]
