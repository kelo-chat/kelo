"""The wallet: card checks, how cards are kept (end-to-end vs auto-pay), the auto-pay rules (limits, monthly totals, first payment at a
site, pause), the one-time card delivery, paying with a card through confirm_purchase + browser_fill_card, the HTTP API and turn events,
and that no card number or CVC ever shows up in an answer, an event, a tool result, a log line or the database file.
Test numbers only (4242 4242 4242 4242 and friends); sites are neutral fakes."""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from assistant import i18n, server as server_mod
from assistant.agent import ToolResult, current_approval, run_agent, ui_sink
from assistant.providers import Done, TextDelta, ToolCall
from assistant.server import Settings, create_app
from assistant.store import Store
from assistant.tools import browser as browser_mod
from assistant.tools.browser import BrowserSession, browser_tools
from assistant.tools.purchase import purchase_tools
from assistant.wallet import (
    DEFAULT_LIMITS, CardDeliveries, Wallet, WalletError, brand_of, check_cvc, check_expiry, check_number, luhn_ok, money_text, site_key,
)
from assistant.workers import current_turn

from test_purchase import PUBLIC, ShopPage
from test_server import AUTH, TOKEN, Running, scripted

VISA, MC, AMEX = "4242424242424242", "5555555555554444", "378282246310005"
SECRETS = (VISA, "4242 4242 4242 4242", "4242-4242-4242-4242", MC, "5555 5555 5555 4444", AMEX)
CVC = "987"


def no_secrets(*texts):
    for t in texts:
        s = t if isinstance(t, str) else json.dumps(t, ensure_ascii=False, default=str)
        for secret in SECRETS:
            assert secret not in s, f"card number leaked: {s[:200]}"
        assert f'"{CVC}"' not in s and f"cvc {CVC}" not in s.lower() and f"'{CVC}'" not in s, f"CVC leaked: {s[:200]}"


def future(years=3):
    now = datetime.now(timezone.utc)
    return now.month, now.year + years


def new_wallet(clock=time.time):
    store = Store(":memory:")
    return store, Wallet(store, clock=clock)


def auto_card(wallet, number=VISA, label="Test Visa ••4242", **kw):
    m, y = future()
    return wallet.add_card({"label": label, "exp_month": m, "exp_year": y, "holder": "HONG GILDONG", "postal_code": "04524",
                            "auto_pay": True, "number": number, "cvc": CVC, **kw})


def e2e_card(wallet, last4="4444", label="Test MC ••4444", blob="QUJDREVGR0hJSktMTU5PUA==", **kw):
    m, y = future()
    return wallet.add_card({"label": label, "exp_month": m, "exp_year": y, "holder": "HONG GILDONG", "auto_pay": False, "last4": last4,
                            "brand": "mastercard", "e2e": {"blob": blob, "alg": "AES-256-GCM", "key_id": "phone-key-1"}, **kw})


# ---------------------------------------------------------------- card checks
def test_luhn_brand_expiry_and_cvc_checks():
    assert luhn_ok(VISA) and luhn_ok(MC) and luhn_ok(AMEX) and not luhn_ok("4242424242424241")
    assert [brand_of(n) for n in (VISA, MC, AMEX, "3530111333300000", "6011111111111117", "2223003122003222")] == \
        ["visa", "mastercard", "amex", "jcb", "discover", "mastercard"]
    assert check_number("4242 4242-4242 4242") == VISA
    for bad in ("4242424242424241", "4242", "4242x4242x4242x4242", 4242424242424242, "", None):
        with pytest.raises(WalletError) as e:
            check_number(bad)
        no_secrets(str(e.value))
    assert check_cvc("123") == "123" and check_cvc("1234") == "1234"
    for bad in ("12", "12345", "abc", 123, None):
        with pytest.raises(WalletError):
            check_cvc(bad)
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    assert check_expiry(11, 26, now) == (11, 2026)                 # good through the end of its month
    assert check_expiry("3", "2030", now) == (3, 2030)
    for m, y in ((10, 2026), (13, 2030), (0, 2030), ("x", 2030), (1, 2060), (True, 2030)):
        with pytest.raises(WalletError):
            check_expiry(m, y, now)


def test_site_key_is_the_registrable_domain():
    assert site_key("pay.shop.example.com") == "example.com"
    assert site_key("www.example.co.kr") == "example.co.kr" and site_key("m.store.example.co.kr") == "example.co.kr"
    assert site_key("checkout.example.co.uk") == "example.co.uk" and site_key("shop.example.com.au") == "example.com.au"
    assert site_key("alice.github.io") == "alice.github.io" != site_key("bob.github.io")
    assert site_key("example.com") == "example.com" and site_key("93.184.216.34") == "93.184.216.34"


def test_site_key_is_the_one_site_rule_of_the_public_suffix_list():
    """wallet, payment PINs and conditional tasks use tools/sites.registrable (the Public Suffix List): one site rule in Kelo."""
    from assistant.tools.sites import registrable
    for host in ("pay.shop.example.com", "www.example.co.kr", "a.b.example.co.jp", "foo.blogspot.com", "x.y.myshopify.com",
                 "shop.example.com.au", "login.city.kawasaki.jp", "EXAMPLE.COM.", "shop.example.com:8443", "localhost"):
        assert site_key(host) == registrable(host)
    assert site_key("a.b.example.co.jp") == "example.co.jp" and site_key("shop.example.com:8443") == "example.com"
    assert site_key("foo.blogspot.com") == "foo.blogspot.com" != site_key("bar.blogspot.com")   # private suffixes of the list too


# ---------------------------------------------------------------- keeping cards
def test_auto_pay_card_is_sealed_and_never_shown(tmp_path):
    store = Store(tmp_path / "a.db")
    wallet = Wallet(store)
    card = auto_card(wallet)
    assert card["brand"] == "visa" and card["last4"] == "4242" and card["auto_pay"] and card["server_copy"] and card["usable"]
    no_secrets(card, wallet.cards(), store.vault_log())
    assert wallet.open_secret(card["id"]).number == VISA and wallet.open_secret(card["id"]).cvc == CVC
    assert repr(wallet.open_secret(card["id"])) == "CardSecret(••4242)" and VISA not in str(wallet.open_secret(card["id"]))
    raw = (tmp_path / "a.db").read_bytes()
    assert VISA.encode() not in raw and b"4242 4242" not in raw                         # sealed in the file
    # bound to the card: moved to another card's row it no longer opens
    other = auto_card(wallet, number=MC, label="Other")
    sealed = store._all("SELECT sealed FROM wallet_cards WHERE id = ?", (card["id"],))[0]["sealed"]
    store._run("UPDATE wallet_cards SET sealed = ? WHERE id = ?", (sealed, other["id"]))
    assert wallet.open_secret(other["id"]) is None
    assert wallet.delete_card(card["id"]) and wallet.card(card["id"]) is None and wallet.open_secret(card["id"]) is None
    assert not wallet.delete_card(card["id"]) and wallet.delete_card(other["id"])     # (the swapped copy went with the other card)
    assert sealed.encode() not in (tmp_path / "a.db").read_bytes()                      # wiped, not just unlinked (secure_delete)


def test_end_to_end_card_keeps_only_the_phones_ciphertext():
    _, wallet = new_wallet()
    card = e2e_card(wallet)
    assert not card["auto_pay"] and not card["server_copy"] and card["e2e"] and card["usable"] and card["e2e_key_id"] == "phone-key-1"
    assert wallet.open_secret(card["id"]) is None
    assert wallet.blob(card["id"]) == {"blob": "QUJDREVGR0hJSktMTU5PUA==", "alg": "AES-256-GCM", "key_id": "phone-key-1"}
    m, y = future()
    with pytest.raises(WalletError, match="end-to-end"):              # an E2E card never sends its number
        wallet.add_card({"exp_month": m, "exp_year": y, "auto_pay": False, "last4": "4444", "number": MC, "cvc": CVC,
                         "e2e": {"blob": "QUJD"}})
    for bad in ({"blob": ""}, {"blob": "x" * 9000}, {"blob": "not base64 <script>"}, "QUJD"):
        with pytest.raises(WalletError):
            wallet.add_card({"exp_month": m, "exp_year": y, "auto_pay": False, "last4": "4444", "e2e": bad})
    with pytest.raises(WalletError):
        wallet.add_card({"exp_month": m, "exp_year": y, "auto_pay": False, "last4": "44", "e2e": {"blob": "QUJD"}})
    with pytest.raises(WalletError, match="card number"):             # no number hidden in a label
        wallet.add_card({"label": "my 4242 4242 4242 4242", "exp_month": m, "exp_year": y, "auto_pay": False, "last4": "4444",
                         "e2e": {"blob": "QUJD"}})


def test_turning_auto_pay_on_and_off():
    store, wallet = new_wallet()
    card = e2e_card(wallet, last4="4242", label="Visa")
    with pytest.raises(WalletError, match="number"):
        wallet.update_card(card["id"], {"auto_pay": True})                       # ON needs the card itself
    with pytest.raises(WalletError, match="last 4"):
        wallet.update_card(card["id"], {"auto_pay": True, "number": MC, "cvc": CVC})
    on = wallet.update_card(card["id"], {"auto_pay": True, "number": VISA, "cvc": CVC})
    assert on["auto_pay"] and on["server_copy"] and on["e2e"]                    # the phone's own blob is kept too
    off = wallet.update_card(card["id"], {"auto_pay": False})
    assert not off["auto_pay"] and not off["server_copy"] and off["e2e"] and off["usable"]   # pays end-to-end again with the kept blob
    assert wallet.open_secret(card["id"]) is None
    bare = auto_card(wallet, number=MC, label="MC")
    off = wallet.update_card(bare["id"], {"auto_pay": False})
    assert not off["server_copy"] and not off["e2e"] and not off["usable"]      # only details until the phone sends a blob
    again = wallet.update_card(bare["id"], {"e2e": {"blob": "QUJDRA=="}})
    assert again["usable"] and again["e2e"]
    with pytest.raises(WalletError):
        wallet.update_card(bare["id"], {"number": MC, "cvc": CVC})               # E2E card: no number
    assert wallet.update_card(bare["id"], {"label": "Groceries"})["label"] == "Groceries"
    with pytest.raises(WalletError) as e:
        wallet.update_card("nope", {})
    assert e.value.status == 404
    no_secrets(store.vault_log(), wallet.cards())


def test_find_cards_by_id_label_or_last4():
    _, wallet = new_wallet()
    a = auto_card(wallet, label="신한 체크 ••4242")
    b = e2e_card(wallet, label="현대 ••4444")
    c = e2e_card(wallet, last4="4242", label="Work card ••4242")
    assert wallet.find(a["id"]) == a and wallet.find("현대 ••4444")["id"] == b["id"] and wallet.find("현대")["id"] == b["id"]
    assert wallet.find("4444")["id"] == b["id"] and wallet.find("신한 4242")["id"] == a["id"] and wallet.find("work 4242")["id"] == c["id"]
    assert wallet.find("4242") is None and wallet.find("") is None and wallet.find("nothing") is None   # ambiguous / unknown


# ---------------------------------------------------------------- rules
def test_default_limits_cover_the_major_currencies():
    want = {"KRW": (30_000, 200_000), "USD": (25, 150), "EUR": (25, 150), "GBP": (20, 120), "JPY": (3_000, 20_000), "CNY": (150, 1_000)}
    for cur, lim in want.items():
        assert DEFAULT_LIMITS[cur] == lim
    for cur in "CAD AUD HKD SGD TWD CHF SEK NOK DKK PLN INR IDR THB VND PHP MYR BRL MXN TRY AED SAR ZAR NZD".split():
        per, month = DEFAULT_LIMITS[cur]
        assert 0 < per < month
    _, wallet = new_wallet()
    rows = {r["currency"]: r for r in wallet.limits()}
    assert rows["KRW"]["per_payment"] == 30_000 and not rows["KRW"]["custom"] and rows["KRW"]["spent_this_month"] == 0


def test_limits_are_editable_and_validated():
    _, wallet = new_wallet()
    assert wallet.set_limit("krw", 50_000, 300_000)["custom"]
    assert wallet.limit_for("KRW") == (50_000, 300_000)
    for args in (("KRW", 10, 5), ("KRW", -1, 5), ("KRWX", 1, 2), ("KRW", "a", 2), ("KRW", True, 2)):
        with pytest.raises(WalletError):
            wallet.set_limit(*args)
    assert wallet.reset_limit("KRW") and wallet.limit_for("KRW") == (30_000, 200_000)
    assert wallet.limit_for("XAU") is None                      # not in the table: never automatic
    wallet.set_limit("XAU", 1, 2)
    assert wallet.limit_for("XAU") == (1, 2) and wallet.reset_limit("XAU") and wallet.limit_for("XAU") is None


def test_monthly_total_is_per_currency_per_calendar_month_in_the_users_time_zone():
    now = [datetime(2026, 11, 30, 14, 30, tzinfo=timezone.utc).timestamp()]   # 23:30 in Seoul, still November
    store, wallet = new_wallet(clock=lambda: now[0])
    card = auto_card(wallet)
    store.set_profile(timezone="Asia/Seoul")
    wallet.record("auto", card, "shop.example.com", "a", 20_000, "KRW")
    wallet.record("approved", card, "shop.example.com", "b", 99_000, "KRW")    # approved on the phone: not counted
    wallet.record("auto", card, "shop.example.com", "c", 10, "USD")
    assert wallet.spent("KRW") == 20_000 and wallet.spent("USD") == 10 and wallet.month_key() == "2026-11"
    now[0] += 3600                                                           # 00:30 on 1 December in Seoul (still Nov in UTC)
    assert wallet.month_key() == "2026-12" and wallet.spent("KRW") == 0
    store.set_profile(timezone="")
    assert wallet.timezone() == "UTC" and wallet.month_key() == "2026-11" and wallet.spent("KRW") == 20_000


def test_auto_pay_decision():
    store, wallet = new_wallet()
    card = auto_card(wallet)
    e2e = e2e_card(wallet)
    host = "pay.shop.example.com"
    assert wallet.decide(card, host, 10_000, "KRW").reason == "first_site"        # never paid here: the phone asks
    assert wallet.decide(card, host, 10_000, "KRW", asked_here=True).auto         # ...unless the owner asked to buy there
    assert wallet.decide(card, host, 999_999, "KRW", asked_here=True).reason == "over_per_payment"   # the limits still decide
    assert not wallet.decide(e2e, host, 10_000, "KRW", asked_here=True).auto       # and a card without auto-pay still asks
    wallet.mark_site("shop.example.com")
    d = wallet.decide(card, host, 10_000, "KRW")
    assert d.auto and d.reason == "auto"                                          # a subdomain of a known site is the same site
    assert d.text == "Within the auto-pay rules" and money_text(24_000, "KRW") == "24,000 KRW" and money_text(9.5, "usd") == "9.50 USD"
    tok = i18n.use("ko")                                                          # the reason and the amount in the user's language
    try:
        assert d.text == "자동결제 규칙 안이에요" and money_text(24_000, "KRW") == "24,000원" and money_text(9.5, "usd") == "9.50 USD"
    finally:
        i18n.reset(tok)
    assert wallet.decide(card, host, 30_000, "KRW").auto                          # the limit itself is allowed
    assert wallet.decide(card, host, 30_001, "KRW").reason == "over_per_payment"
    assert wallet.decide(card, host, 10, "XAU").reason == "currency"
    assert wallet.decide(card, host, 26, "USD").reason == "over_per_payment" and wallet.decide(card, host, 25, "usd").auto
    for _ in range(6):
        wallet.record("auto", card, host, "x", 30_000, "KRW")                   # 180,000 this month
    assert wallet.decide(card, host, 20_000, "KRW").auto and wallet.decide(card, host, 20_001, "KRW").reason == "over_monthly"
    wallet.set_paused(True)
    assert wallet.decide(card, host, 1, "KRW").reason == "paused"
    wallet.set_paused(False)
    store.set_vault_locked(True)
    assert wallet.decide(card, host, 1, "KRW").reason == "vault_locked"
    store.set_vault_locked(False)
    assert wallet.decide(e2e, host, 1, "KRW").reason == "not_auto"
    wallet.set_limit("KRW", 0, 0)
    assert wallet.decide(card, host, 1, "KRW").reason == "over_per_payment"     # 0 = never automatic


def test_history_and_notifications():
    store, wallet = new_wallet()
    card = auto_card(wallet)
    p1 = wallet.record("auto", card, "shop.example.com", "Blue mug x2", 24_000, "KRW")
    wallet.record("approved", card, "shop.example.com", "Red mug", 12_000, "KRW")
    assert p1["kind"] == "auto" and p1["card_label"] == card["label"] and p1["amount_text"] == "24,000 KRW" and not p1["seen"]
    assert [p["kind"] for p in wallet.payments()] == ["approved", "auto"]
    n = wallet.notifications()
    assert n["unseen"] == 1 and [p["summary"] for p in n["items"]] == ["Blue mug x2"]
    assert wallet.notifications(after=p1["id"])["items"] == []
    assert wallet.mark_seen(p1["id"]) == 1 and wallet.notifications()["unseen"] == 0
    logged = [e for e in store.vault_log() if e["action"] == "auto-paid"]
    assert logged and "24,000 KRW" in logged[0]["detail"] and logged[0]["item"] == card["label"]


# ---------------------------------------------------------------- one-time delivery
async def test_card_delivery_is_single_use_checked_and_expires():
    now = [1000.0]
    d = CardDeliveries(ttl=300, clock=lambda: now[0])
    card = {"id": "card_1", "label": "MC", "brand": "mastercard", "last4": "4444"}
    with pytest.raises(WalletError) as e:
        d.deliver("nope", {"number": MC, "cvc": CVC})
    assert e.value.status == 404
    d.expect("a1", card, {"site": "shop.example.com"})
    assert [p["approval_id"] for p in d.pending()] == ["a1"]
    for body in ({"number": VISA, "cvc": CVC}, {"number": "5555555555554445", "cvc": CVC}, {"number": MC, "cvc": "1"},
                 {"number": MC, "cvc": CVC, "card_id": "card_2"}):
        with pytest.raises(WalletError) as e:
            d.deliver("a1", body)
        assert e.value.status == 422
        no_secrets(str(e.value))
    d.deliver("a1", {"number": MC, "cvc": CVC, "card_id": "card_1"})
    with pytest.raises(WalletError) as e:
        d.deliver("a1", {"number": MC, "cvc": CVC})
    assert e.value.status == 409
    got = await d.wait("a1", 1)
    assert got.number == MC and got.cvc == CVC and repr(got) == "CardSecret(••4444)"
    assert await d.wait("a1", 0.01) is None and not d.has("a1")              # taken once; gone
    with pytest.raises(WalletError):
        d.deliver("a1", {"number": MC, "cvc": CVC})
    d.expect("a2", card, {})
    now[0] += 301
    assert not d.has("a2") and d.pending() == []                              # expired
    d.expect("a3", card, {})
    d.deliver("a3", {"number": MC, "cvc": CVC})
    held = d._slots["a3"].secret
    d.discard("a3")
    assert held.number == "" and held.cvc == ""                               # a discarded card is wiped from memory
    d.expect("a4", card, {})
    assert await d.wait("a4", 0.01) is None and not d.has("a4")               # nothing came in time


# ---------------------------------------------------------------- paying with a card: the agent side
class Locator:
    def __init__(self, frame, sel):
        self.frame, self.sel = frame, sel

    async def fill(self, value, **kw):
        self.frame.typed.append((self.sel, value))

    async def select_option(self, value=None, **kw):
        self.frame.typed.append((self.sel, f"select:{value}"))


class CardFrame:
    """A checkout frame: answers the field-marking script with what it 'has' and records what is typed into each marked field."""

    def __init__(self, url, found=None, broken=False):
        self.url, self.found, self.broken, self.typed = url, found, broken, []

    async def evaluate(self, js):
        if self.broken:
            raise RuntimeError("frame was detached")
        return self.found if "kindOf" in js else None

    def locator(self, sel):
        return Locator(self, sel)


FORM = {"any": True, "card": True, "number": [0], "exp": {"max": 5, "placeholder": "mm / yy", "options": None}, "exp_month": None,
        "exp_year": None, "cvc": True, "name": True, "postal": True}


class CheckoutPage(ShopPage):
    def __init__(self, frames=None, url="https://shop.example.com/checkout", text="Checkout · Blue mug x2 · total 24,000"):
        super().__init__(url)
        self.text = text
        self.frames = frames if frames is not None else [CardFrame(url, FORM)]


def checkout(page=None):
    page = page or CheckoutPage()

    async def connect():
        return page
    return BrowserSession(connect=connect, resolver=PUBLIC), page


ORDER = {"summary": "Blue mug x2", "amount": 24000, "currency": "KRW", "site": "shop.example.com"}


def agent(session, wallet, deliveries, script, taps, *, on_ask=None, card_wait=1.0):
    """The agent loop with a scripted model; approvals answered like the app does (approval id, extra card fields, slot cleanup)."""
    tools = [*browser_tools(session), *purchase_tools(session, wallet, deliveries, card_wait=card_wait)]
    by = {t.name: t for t in tools}
    asked, shown = [], []
    taps = iter(taps)

    async def approve(req):
        aid = uuid.uuid4().hex
        current_approval.set((req.id, aid))
        extra = by[req.name].approval_extra(aid, req.arguments) if by[req.name].approval_extra else {}
        asked.append((req, aid, extra))
        if on_ask:
            on_ask(aid, extra)
        ok = next(taps, False)
        if not ok:
            deliveries.discard(aid)
        return ok

    async def run():
        async def sink(item):
            shown.append(item)
        tok = ui_sink.set(sink)
        try:
            return [ev async for ev in run_agent("p", "m", [{"role": "user", "content": "buy it with my card"}], tools, approve=approve,
                                                 stream_fn=scripted(*script)) if isinstance(ev, ToolResult)]
        finally:
            ui_sink.reset(tok)
    return run, asked, shown


def call(i, name, args):
    return [ToolCall(f"c{i}", name, json.dumps(args, ensure_ascii=False)), Done("tool_calls")]


END = [TextDelta("done"), Done("stop")]


async def test_auto_pay_inside_the_rules_pays_without_asking_but_shows_and_notifies():
    store, wallet = new_wallet()
    card = auto_card(wallet)
    wallet.mark_site("shop.example.com")
    session, page = checkout()
    await session.page()
    run, asked, shown = agent(session, wallet, CardDeliveries(), [
        call(1, "wallet_cards", {}), call(2, "confirm_purchase", {**ORDER, "card": card["label"]}),
        call(3, "browser_fill_card", {"card": card["label"]}), call(4, "browser_click", {"selector": "text=Pay now"}), END], [])
    results = await run()
    assert asked == []                                                                     # nobody was asked
    listing, approved, filled, clicked = results
    assert "Test Visa ••4242" in listing.output and "pays automatically" in listing.output and "first payment on a new site" in listing.output
    assert approved.approved is None and "APPROVED automatically" in approved.output and "browser_fill_card" in approved.output
    assert filled.output.startswith("Filled in card ••4242: number, expiry, cvc, holder, postal code.") and not filled.is_error
    assert not clicked.is_error and ("click", "text=Pay now") in page.calls                # the pay click is inside the window
    typed = dict(page.frames[0].typed)
    assert typed['[data-kelo-cc="number-0"]'] == VISA and typed['[data-kelo-cc="cvc"]'] == CVC   # typed into the page...
    assert typed['[data-kelo-cc="exp"]'] == f"{future()[0]:02d} / {future()[1] % 100:02d}"
    assert typed['[data-kelo-cc="name"]'] == "HONG GILDONG" and typed['[data-kelo-cc="postal"]'] == "04524"
    no_secrets([r.output for r in results], shown, store.vault_log())                     # ...and nowhere else
    auto = [e for e in shown if e.get("type") == "payment_auto"]
    assert len(auto) == 1 and auto[0]["payment"]["amount"] == 24000 and auto[0]["card"]["last4"] == "4242" and auto[0]["id"] == "c2"
    assert wallet.notifications()["unseen"] == 1 and wallet.spent("KRW") == 24000
    assert any(e["action"] == "card-filled" for e in store.vault_log())


async def test_first_payment_at_a_site_asks_then_the_site_is_known():
    _, wallet = new_wallet()
    card = auto_card(wallet)
    session, page = checkout()
    await session.page()
    run, asked, shown = agent(session, wallet, CardDeliveries(), [
        call(1, "confirm_purchase", {**ORDER, "card": card["label"]}), call(2, "browser_fill_card", {"card": card["label"]}),
        call(3, "confirm_purchase", {**ORDER, "summary": "Blue mug x1", "amount": 12000, "card": card["label"]}), END], [True])
    first, fill, second = await run()
    assert len(asked) == 1 and asked[0][2]["card"]["reason"] == "first_site" and asked[0][2]["card"]["needs_card"] is False
    assert "First payment on this site" in asked[0][0].detail and "Test Visa ••4242" in asked[0][0].detail
    assert first.approved is True and "APPROVED by the user" in first.output and not fill.is_error
    assert second.approved is None and "APPROVED automatically" in second.output              # known now
    assert [p["kind"] for p in wallet.payments()] == ["auto", "approved"] and wallet.spent("KRW") == 12000


@pytest.mark.parametrize("setup, reason", [
    (lambda w, c: w.set_paused(True), "paused"),
    (lambda w, c: w.set_limit("KRW", 10_000, 100_000), "over_per_payment"),
    (lambda w, c: [w.record("auto", c, "shop.example.com", "x", 30_000, "KRW") for _ in range(6)], "over_monthly"),
])
async def test_outside_the_rules_the_phone_is_asked(setup, reason):
    _, wallet = new_wallet()
    card = auto_card(wallet)
    wallet.mark_site("shop.example.com")
    setup(wallet, card)
    session, _ = checkout()
    await session.page()
    run, asked, shown = agent(session, wallet, CardDeliveries(), [call(1, "confirm_purchase", {**ORDER, "card": card["label"]}), END], [False])
    (res,) = await run()
    assert asked[0][2]["card"]["reason"] == reason and res.approved is False and "did NOT approve" in res.output
    assert not [e for e in shown if e.get("type") == "payment_auto"]


async def test_end_to_end_card_needs_allow_and_the_phones_card_once():
    store, wallet = new_wallet()
    card = e2e_card(wallet, last4="4444")
    deliveries = CardDeliveries()
    session, page = checkout()
    await session.page()

    def phone(aid, extra):        # the phone sends the card right with its Allow (the other order is tested over HTTP)
        assert extra["card"]["needs_card"] is True and deliveries.has(aid)
        deliveries.deliver(aid, {"card_id": card["id"], "number": MC, "cvc": CVC})

    run, asked, shown = agent(session, wallet, deliveries, [
        call(1, "confirm_purchase", {**ORDER, "card": "4444"}), call(2, "browser_fill_card", {"card": "4444"}),
        call(3, "browser_fill_card", {"card": "4444"}), END], [True], on_ask=phone)
    approved, filled, again = await run()
    assert approved.approved is True and "APPROVED by the user" in approved.output
    assert "your phone sends the card details once" in asked[0][0].detail
    need = [e for e in shown if e.get("type") == "card_needed"]
    assert need and need[0]["approval_id"] == asked[0][1] and need[0]["card"]["last4"] == "4444" and need[0]["expires_at"] > time.time()
    assert filled.output.startswith("Filled in card ••4444") and dict(page.frames[0].typed)['[data-kelo-cc="number-0"]'] == MC
    assert "no longer here" in again.output                                                  # the phone's card is typed once
    assert deliveries.pending() == []
    no_secrets([r.output for r in (approved, filled, again)], shown, store.vault_log())


async def test_end_to_end_card_not_sent_in_time_means_no_payment():
    store, wallet = new_wallet()
    card = e2e_card(wallet)
    deliveries = CardDeliveries()
    session, page = checkout()
    await session.page()
    run, asked, shown = agent(session, wallet, deliveries, [
        call(1, "confirm_purchase", {**ORDER, "card": card["label"]}), call(2, "browser_fill_card", {"card": card["label"]}),
        call(3, "browser_click", {"selector": "text=Pay now"}), END], [True, False], card_wait=0.05)
    approved, fill, click = await run()
    assert approved.is_error and "did not send the card" in approved.output and "Do NOT click pay" in approved.output
    assert fill.is_error and "no approved payment" in fill.output
    assert click.approved is False and ("click", "text=Pay now") not in page.calls             # no purchase window was opened
    assert not session.purchase_active() and wallet.payments() == [] and deliveries.pending() == []
    assert any(e["action"] == "card-not-sent" for e in store.vault_log())


async def test_deny_drops_a_card_the_phone_already_sent():
    _, wallet = new_wallet()
    card = e2e_card(wallet)
    deliveries = CardDeliveries()
    session, _ = checkout()
    await session.page()
    sent = []

    def phone(aid, extra):
        deliveries.deliver(aid, {"number": MC, "cvc": CVC})
        sent.append((aid, deliveries._slots[aid].secret))

    run, asked, shown = agent(session, wallet, deliveries, [call(1, "confirm_purchase", {**ORDER, "card": card["label"]}), END], [False], on_ask=phone)
    (res,) = await run()
    assert res.approved is False and not deliveries.has(sent[0][0]) and sent[0][1].number == ""
    with pytest.raises(WalletError):
        deliveries.deliver(sent[0][0], {"number": MC, "cvc": CVC})


async def test_fill_card_only_on_the_approved_site_inside_the_window(monkeypatch):
    _, wallet = new_wallet()
    card = auto_card(wallet)
    wallet.mark_site("shop.example.com")
    session, page = checkout()
    await session.page()
    tools = {t.name: t for t in purchase_tools(session, wallet, CardDeliveries())}
    fill = tools["browser_fill_card"]
    assert "no approved payment" in fill.validate({"card": card["label"]})                      # not before confirm_purchase
    assert "card number" in fill.validate({"card": VISA})
    assert "no card" in fill.validate({"card": "nothing"})
    assert tools["confirm_purchase"].auto_approve({**ORDER, "card": card["label"]})       # what the loop does: rules first, then the handler
    assert "APPROVED automatically" in await tools["confirm_purchase"].handler({**ORDER, "card": card["label"]})
    assert fill.validate({"card": card["label"]}) is None
    other = auto_card(wallet, number=MC, label="MC")
    assert "no approved payment with 'MC'" in fill.validate({"card": "MC"})                    # another card is not approved
    page.url = "https://elsewhere.example.net/pay"
    assert "elsewhere.example.net" in fill.validate({"card": card["label"]})                   # another site never gets it
    page.url = "https://pay.shop.example.com/step2"
    assert fill.validate({"card": card["label"]}) is None                                      # the shop's own payment page does
    now = time.monotonic()
    monkeypatch.setattr(browser_mod.time, "monotonic", lambda: now + 200)
    import assistant.tools.purchase as purchase_mod
    monkeypatch.setattr(purchase_mod.time, "monotonic", lambda: now + 200)
    assert "no approved payment" in fill.validate({"card": card["label"]})                     # the window ran out
    assert other


async def test_fill_card_reports_split_fields_frames_it_cannot_reach_and_extra_authentication():
    _, wallet = new_wallet()
    card = auto_card(wallet, number=AMEX, label="Amex")
    wallet.mark_site("shop.example.com")
    split = {"any": True, "card": True, "number": [4, 6, 5], "exp": None, "cvc": True, "name": False, "postal": False,
             "exp_month": {"max": 0, "placeholder": "", "options": [["", "월"], ["01", "1월"], [f"{future()[0]:02d}", f"{future()[0]}월"]]},
             "exp_year": {"max": 0, "placeholder": "", "options": [[str(future()[1]), str(future()[1])]]}}
    frames = [CardFrame("https://shop.example.com/checkout", None), CardFrame("https://pay.provider.example/form", split),
              CardFrame("https://ads.example.org/x", broken=True)]
    page = CheckoutPage(frames, text="Please complete 3-D Secure verification in your card app")
    session, _ = checkout(page)
    await session.page()
    run, *_ = agent(session, wallet, CardDeliveries(), [
        call(1, "confirm_purchase", {**ORDER, "card": "Amex"}), call(2, "browser_fill_card", {"card": "Amex"}), END], [])
    _, filled = await run()
    out = filled.output
    assert "number (pay.provider.example)" in out and "cvc (pay.provider.example)" in out and "expiry (pay.provider.example)" in out
    assert "could not be reached: ads.example.org" in out
    assert "extra authentication" in out and "WAIT" in out and "3-d secure" in out
    typed = frames[1].typed
    assert [v for s, v in typed if s.startswith('[data-kelo-cc="number')] == ["3782", "822463", "10005"]
    assert ('[data-kelo-cc="exp_month"]', f"select:{future()[0]:02d}") in typed
    assert ('[data-kelo-cc="exp_year"]', f"select:{future()[1]}") in typed
    no_secrets(out)
    assert "378282246310005" not in out


async def test_no_card_form_found_says_so_and_types_nothing():
    _, wallet = new_wallet()
    card = auto_card(wallet)
    wallet.mark_site("shop.example.com")
    page = CheckoutPage([CardFrame("https://shop.example.com/checkout", {"any": False})])
    session, _ = checkout(page)
    await session.page()
    run, *_ = agent(session, wallet, CardDeliveries(), [
        call(1, "confirm_purchase", {**ORDER, "card": card["label"]}), call(2, "browser_fill_card", {"card": card["label"]}), END], [])
    _, filled = await run()
    assert "No card fields were found" in filled.output and "Nothing was typed" in filled.output and page.frames[0].typed == []


async def test_a_click_that_leads_to_card_authentication_tells_the_agent_to_wait():
    session, page = checkout(CheckoutPage(text="카드사 앱에서 결제를 승인해 주세요"))
    await session.page()
    session.allow_purchase("shop.example.com")
    click = {t.name: t for t in browser_tools(session)}["browser_click"]
    out = await click.handler({"selector": "text=결제하기"})
    assert "extra authentication" in out and "WAIT" in out


def test_confirm_purchase_card_checks():
    _, wallet = new_wallet()
    card = auto_card(wallet)
    t = {x.name: x for x in purchase_tools(None, wallet, CardDeliveries())}["confirm_purchase"]
    assert t.validate({**ORDER, "card": card["label"]}) is None
    assert "no card 'Diners'" in t.validate({**ORDER, "card": "Diners"}) and card["label"] in t.validate({**ORDER, "card": "Diners"})
    assert "card number" in t.validate({**ORDER, "card": "4242 4242 4242 4242"})
    m, y = future()
    bare = auto_card(wallet, number=MC, label="Bare")
    wallet.update_card(bare["id"], {"auto_pay": False})
    assert "no stored details" in t.validate({**ORDER, "card": "Bare"})
    wallet.store._run("UPDATE wallet_cards SET exp_year = 2020 WHERE id = ?", (card["id"],))
    assert "expired" in t.validate({**ORDER, "card": card["label"]})
    nowallet = purchase_tools(None)[0]
    assert "no wallet" in nowallet.validate({**ORDER, "card": "x"}) and "card" not in nowallet.parameters["properties"]


async def test_wallet_cards_lists_labels_only_and_says_when_paused():
    _, wallet = new_wallet()
    tool = {x.name: x for x in purchase_tools(None, wallet, CardDeliveries())}["wallet_cards"]
    assert "no cards" in await tool.handler({})
    auto_card(wallet)
    e2e_card(wallet)
    wallet.set_paused(True)
    out = await tool.handler({})
    assert "'Test Visa ••4242' (visa, ends 4242" in out and "'Test MC ••4444'" in out
    assert "ASKS EACH TIME [paused]" in out and "ASKS EACH TIME [not_auto]" in out and "saved to ask each time" in out and tool.read_only
    no_secrets(out)


# ---------------------------------------------------------------- the HTTP API and the turn stream
class Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def all_logs():
    h = Collect()
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access", "assistant"]
    olds = [(logging.getLogger(n), logging.getLogger(n).level) for n in names]
    for lg, _ in olds:
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)
    yield h
    for lg, level in olds:
        lg.removeHandler(h)
        lg.setLevel(level)


def browser_app(tmp_path, monkeypatch, script, page=None):
    session, page = checkout(page)
    monkeypatch.setattr(server_mod, "BrowserSession", lambda *a, **k: session)
    s = Settings(token=TOKEN, provider="p", model="m", cdp_host="browser", db_path=str(tmp_path / "a.db"))
    return create_app(s, stream_fn=scripted(*script)), page


async def test_wallet_api_end_to_end_without_ever_returning_a_secret(tmp_path, monkeypatch, all_logs):
    app, _ = browser_app(tmp_path, monkeypatch, [])
    answers = []
    m, y = future()
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/wallet/cards")).status_code == 401
        assert (await c.post("/api/wallet/cards", json={"auto_pay": True, "number": VISA, "cvc": CVC})).status_code == 401
        c.headers.update(AUTH)

        async def go(method, url, **kw):
            r = await c.request(method, url, **kw)
            answers.append(r.text)
            return r

        r = await go("POST", "/api/wallet/cards", json={"label": "신한 체크 ••4242", "exp_month": m, "exp_year": y, "holder": "HONG",
                                                        "postal_code": "04524", "auto_pay": True, "number": "4242 4242 4242 4242", "cvc": CVC})
        assert r.status_code == 201
        visa = r.json()
        assert visa["last4"] == "4242" and visa["auto_pay"] and visa["brand"] == "visa" and "number" not in visa and "cvc" not in visa
        r = await go("POST", "/api/wallet/cards", json={"exp_month": m, "exp_year": y, "auto_pay": False, "last4": "4444", "brand": "mastercard",
                                                        "e2e": {"blob": "QUJDREVG", "alg": "AES-256-GCM", "key_id": "k1"}})
        e2e = r.json()
        assert r.status_code == 201 and e2e["label"] == "Mastercard ••4444" and e2e["e2e"] and not e2e["server_copy"]
        # refusals never echo what was sent
        for body in ({"auto_pay": True, "number": "4242424242424241", "cvc": CVC, "exp_month": m, "exp_year": y},
                     {"auto_pay": True, "number": VISA, "cvc": "12", "exp_month": m, "exp_year": y},
                     {"auto_pay": True, "number": int(VISA), "cvc": CVC, "exp_month": m, "exp_year": y},
                     {"auto_pay": True, "number": VISA, "cvc": CVC, "exp_month": 1, "exp_year": 2020},
                     {"auto_pay": False, "number": MC, "cvc": CVC, "last4": "4444", "exp_month": m, "exp_year": y, "e2e": {"blob": "QUJD"}}):
            r = await go("POST", "/api/wallet/cards", json=body)
            assert r.status_code == 422, r.text
        r = await go("POST", "/api/wallet/cards", content=b'{"number": "4242424242424242", ')
        assert r.status_code == 400 and "4242" not in r.text
        r = await go("POST", "/api/wallet/cards", json=[VISA])
        assert r.status_code == 400
        assert (await go("GET", "/api/wallet/cards")).json() == {"paused": False, "cards": [visa, e2e]}
        assert (await go("GET", f"/api/wallet/cards/{visa['id']}")).json() == visa
        assert (await go("GET", f"/api/wallet/cards/{e2e['id']}/blob")).json() == {"blob": "QUJDREVG", "alg": "AES-256-GCM", "key_id": "k1"}
        assert (await go("GET", f"/api/wallet/cards/{visa['id']}/blob")).status_code == 404
        whole = (await go("GET", "/api/wallet")).json()
        assert whole["timezone"] == "UTC" and len(whole["month"]) == 7 and len(whole["cards"]) == 2
        # auto-pay off and on
        off = (await go("PATCH", f"/api/wallet/cards/{visa['id']}", json={"auto_pay": False})).json()
        assert not off["auto_pay"] and not off["server_copy"] and not off["usable"]
        assert (await go("PATCH", f"/api/wallet/cards/{visa['id']}", json={"auto_pay": True})).status_code == 422
        assert (await go("PATCH", f"/api/wallet/cards/{visa['id']}", json={"auto_pay": True, "number": MC, "cvc": CVC})).status_code == 422
        on = (await go("PATCH", f"/api/wallet/cards/{visa['id']}", json={"auto_pay": True, "number": VISA, "cvc": CVC})).json()
        assert on["auto_pay"] and on["server_copy"]
        assert (await go("PATCH", "/api/wallet/cards/nope", json={"label": "x"})).status_code == 404
        # limits
        limits = (await go("GET", "/api/wallet/limits")).json()
        krw = next(x for x in limits["limits"] if x["currency"] == "KRW")
        assert krw == {"currency": "KRW", "per_payment": 30000, "monthly": 200000, "default_per_payment": 30000, "default_monthly": 200000,
                       "custom": False, "spent_this_month": 0}
        assert (await go("PUT", "/api/wallet/limits/USD", json={"per_payment": 50, "monthly": 300})).json()["custom"]
        assert (await go("PUT", "/api/wallet/limits/USD", json={"per_payment": 500, "monthly": 300})).status_code == 422
        assert (await go("PUT", "/api/wallet/limits/US", json={"per_payment": 5, "monthly": 30})).status_code == 422
        assert (await go("DELETE", "/api/wallet/limits/USD")).json() == {"ok": True}
        # pause
        assert (await go("PUT", "/api/wallet/pause", json={"paused": True})).json() == {"paused": True}
        assert (await go("PUT", "/api/wallet/pause", json={"paused": "yes"})).status_code == 422
        assert (await go("GET", "/api/wallet/cards")).json()["paused"] is True
        await go("PUT", "/api/wallet/pause", json={"paused": False})
        # history, notifications, deliveries (nothing yet)
        assert (await go("GET", "/api/wallet/payments")).json() == {"items": []}
        assert (await go("GET", "/api/wallet/notifications")).json() == {"items": [], "unseen": 0}
        assert (await go("POST", "/api/wallet/notifications/seen", json={"up_to": 5})).json() == {"ok": True, "marked": 0}
        assert (await go("GET", "/api/wallet/deliveries")).json() == {"items": []}
        r = await go("POST", "/api/wallet/deliveries/nope", json={"number": VISA, "cvc": CVC})
        assert r.status_code == 404
        # the vault log tells what happened to cards, never the card
        log = (await go("GET", "/api/vault/log")).json()
        assert {"card-added", "card-setting", "auto-pay-paused", "limit"} <= {e["action"] for e in log}
        assert (await go("DELETE", f"/api/wallet/cards/{visa['id']}")).json() == {"ok": True}
        assert (await go("DELETE", f"/api/wallet/cards/{visa['id']}")).status_code == 404
    no_secrets(*answers, *all_logs.lines)
    assert VISA.encode() not in (tmp_path / "a.db").read_bytes()


async def test_paying_over_http_first_site_then_auto_with_notification(tmp_path, monkeypatch, all_logs):
    m, y = future()
    script = [call(1, "browser_open", {"url": "https://shop.example.com/checkout"}),
              call(2, "confirm_purchase", {**ORDER, "card": "Visa"}), call(3, "browser_fill_card", {"card": "Visa"}),
              call(4, "confirm_purchase", {**ORDER, "summary": "Blue mug x1", "amount": 12000, "card": "Visa"}),
              call(5, "browser_fill_card", {"card": "Visa"}), END]
    app, page = browser_app(tmp_path, monkeypatch, script)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        r = await c.post("/api/wallet/cards", json={"label": "Visa", "exp_month": m, "exp_year": y, "auto_pay": True, "number": VISA, "cvc": CVC})
        assert r.status_code == 201
        events = []
        async with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "buy it with my visa"}]}) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    events.append(ev)
                    if ev["type"] == "approval_request":
                        await c.post(f"/api/approvals/{ev['approval_id']}", json={"approve": True})
        asks = [e for e in events if e["type"] == "approval_request"]
        assert len(asks) == 1 and asks[0]["card"] == {"id": r.json()["id"], "label": "Visa", "brand": "visa", "last4": "4242", "auto_pay": True,
                                                       "needs_card": False, "reason": "first_site", "reason_text": "First payment on this site"}
        assert asks[0]["payment"] == {"site": "shop.example.com", "summary": "Blue mug x2", "amount": 24000, "currency": "KRW", "amount_text": "24,000 KRW",
                                      "items": [], "shipping": None, "coupon": "", "order": None}   # no order going on: no lines, no address
        auto = [e for e in events if e["type"] == "payment_auto"]
        assert len(auto) == 1 and auto[0]["payment"]["summary"] == "Blue mug x1" and auto[0]["payment"]["kind"] == "auto"
        results = [e for e in events if e["type"] == "tool_result"]
        fills = [e for e in results if e["name"] == "browser_fill_card"]
        assert len(fills) == 2 and all(f["output"].startswith("Filled in card ••4242") for f in fills)
        assert [e["label"] for e in events if e["type"] == "tool_started" and e["name"] == "browser_fill_card"] == ["Entering card details"] * 2
        notes = (await c.get("/api/wallet/notifications")).json()
        assert notes["unseen"] == 1 and notes["items"][0]["amount_text"] == "12,000 KRW" and notes["items"][0]["card_label"] == "Visa"
        hist = (await c.get("/api/wallet/payments")).json()["items"]
        assert [p["kind"] for p in hist] == ["auto", "approved"]
        assert (await c.get("/api/wallet/limits")).json()["limits"][0]["spent_this_month"] == 12000
        answers = [json.dumps(events, ensure_ascii=False), (await c.get("/api/wallet")).text, (await c.get("/api/vault/log")).text]
    assert [v for s, v in page.frames[0].typed if s.endswith('"number-0"]')] == [VISA, VISA]
    no_secrets(*answers, *all_logs.lines)


async def test_end_to_end_card_over_http_allow_then_phone_delivery(tmp_path, monkeypatch, all_logs):
    m, y = future()
    script = [call(0, "browser_open", {"url": "https://shop.example.com/checkout"}),
              call(1, "confirm_purchase", {**ORDER, "card": "MC"}), call(2, "browser_fill_card", {"card": "MC"}),
              call(3, "browser_click", {"selector": "text=Pay now"}), END]
    app, page = browser_app(tmp_path, monkeypatch, script)
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.get("/api/wallet")
        r = await c.post("/api/wallet/cards", json={"label": "MC", "exp_month": m, "exp_year": y, "auto_pay": False, "last4": "4444",
                                                    "e2e": {"blob": "QUJDREVG"}})
        card_id = r.json()["id"]
        events, posted = [], []
        async with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "pay with MC"}]}) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                events.append(ev)
                if ev["type"] == "approval_request" and ev["name"] == "confirm_purchase":
                    assert ev["card"]["needs_card"] is True and ev["card"]["reason"] == "not_auto"
                    pending = (await c.get("/api/wallet/deliveries")).json()["items"]
                    assert [p["approval_id"] for p in pending] == [ev["approval_id"]] and pending[0]["card"]["last4"] == "4444"
                    await c.post(f"/api/approvals/{ev['approval_id']}", json={"approve": True})
                elif ev["type"] == "card_needed":
                    bad = await c.post(f"/api/wallet/deliveries/{ev['approval_id']}", json={"number": VISA, "cvc": CVC})
                    posted.append(bad.text)
                    assert bad.status_code == 422                                       # not the approved card
                    ok = await c.post(f"/api/wallet/deliveries/{ev['approval_id']}", json={"card_id": card_id, "number": MC, "cvc": CVC})
                    posted.append(ok.text)
                    assert ok.json() == {"ok": True}
                    again = await c.post(f"/api/wallet/deliveries/{ev['approval_id']}", json={"number": MC, "cvc": CVC})
                    posted.append(again.text)
                    assert again.status_code in (404, 409)                              # once only
                elif ev["type"] == "approval_request":
                    await c.post(f"/api/approvals/{ev['approval_id']}", json={"approve": False})
        need = next(e for e in events if e["type"] == "card_needed")
        assert need["card"]["label"] == "MC" and need["amount"] == 24000 and need["id"] == "c1"
        results = {e["name"]: e for e in events if e["type"] == "tool_result"}
        assert results["confirm_purchase"]["approved"] is True and results["browser_fill_card"]["output"].startswith("Filled in card ••4444")
        assert results["browser_click"]["approved"] is None and ("click", "text=Pay now") in page.calls
        assert (await c.get("/api/wallet/deliveries")).json() == {"items": []}
        answers = [json.dumps(events, ensure_ascii=False), *posted]
    assert dict(page.frames[0].typed)['[data-kelo-cc="number-0"]'] == MC
    no_secrets(*answers, *all_logs.lines)
    raw = (tmp_path / "a.db").read_bytes()
    assert MC.encode() not in raw and CVC.encode() + b'"' not in raw


async def test_stopping_a_reply_drops_a_waiting_card_slot(tmp_path, monkeypatch):
    m, y = future()
    app, _ = browser_app(tmp_path, monkeypatch, [call(1, "confirm_purchase", {**ORDER, "card": "MC"}), END])
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/wallet/cards", json={"label": "MC", "exp_month": m, "exp_year": y, "auto_pay": False, "last4": "4444",
                                                "e2e": {"blob": "QUJDREVG"}})
        events = []
        async with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "pay"}]}) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    events.append(ev)
                    if ev["type"] == "approval_request":
                        assert len((await c.get("/api/wallet/deliveries")).json()["items"]) == 1
                        break                                                            # the phone goes away: the stream closes
        for _ in range(50):
            if not (await c.get("/api/wallet/deliveries")).json()["items"]:
                break
            await asyncio.sleep(0.05)
        assert (await c.get("/api/wallet/deliveries")).json() == {"items": []}


def test_the_prompt_tells_how_to_pay_with_a_card_without_naming_any_site():
    from pathlib import Path
    import re as _re
    text = (Path(__file__).resolve().parents[2] / "server" / "content" / "prompts" / "system.md").read_text(encoding="utf-8")
    section = text.split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0]
    for word in ("wallet_cards", "browser_fill_card", "card=", "Extra verification", "phone"):
        assert word in section, word
    assert "card numbers never go through chat" in text.split("**Logins and keys", 1)[1].split("\n\n**", 1)[0]
    assert not _re.search(r"https?://|www\.|\.co\.kr|\.com\b", section)
    short = Settings(token=TOKEN).system_prompt
    assert "wallet_cards" in short and "browser_fill_card" in short


# ---------------------------------------------------------------- nickname, default card, a card's own history
def test_the_first_card_is_the_default_and_another_can_take_its_place():
    _, wallet = new_wallet()
    a = auto_card(wallet, label="Everyday")
    b = e2e_card(wallet, label="Work")
    assert a["default"] and not b["default"] and wallet.default_card()["id"] == a["id"]
    wallet.update_card(b["id"], {"default": True})
    assert [c["label"] for c in wallet.cards() if c["default"]] == ["Work"]
    with pytest.raises(WalletError):
        wallet.update_card(b["id"], {"default": False})       # stops being the default only when another one takes over
    c = auto_card(wallet, number=MC, label="New", default=True)
    assert wallet.default_card()["id"] == c["id"] and sum(x["default"] for x in wallet.cards()) == 1
    assert wallet.update_card(c["id"], {"label": "Groceries"})["label"] == "Groceries"   # the nickname is the label


def test_deleting_the_default_asks_which_card_takes_over_unless_one_is_left():
    _, wallet = new_wallet()
    a = auto_card(wallet, label="A")
    b = e2e_card(wallet, label="B")
    c = e2e_card(wallet, last4="1111", label="C")
    with pytest.raises(WalletError) as e:
        wallet.delete_card(a["id"])
    assert e.value.status == 409 and wallet.card(a["id"]) is not None
    with pytest.raises(WalletError):
        wallet.delete_card(a["id"], new_default="card_unknown")
    assert wallet.delete_card(a["id"], new_default=c["id"]) and wallet.default_card()["id"] == c["id"]
    assert wallet.delete_card(b["id"]) and wallet.default_card()["id"] == c["id"]     # not the default: nothing to choose
    d = e2e_card(wallet, last4="2222", label="D")
    assert wallet.delete_card(c["id"]) and wallet.default_card()["id"] == d["id"]     # the only one left takes over
    assert wallet.delete_card(d["id"]) and wallet.default_card() is None


def test_an_older_wallet_gets_its_first_card_as_the_default(tmp_path):
    store = Store(tmp_path / "w.db")
    Wallet(store)
    m, y = future()
    for i, last4 in enumerate(("4444", "1111")):
        store._run("INSERT INTO wallet_cards(id,label,brand,last4,exp_month,exp_year,created_at,e2e_blob) VALUES(?,?,?,?,?,?,?,?)",
                   (f"card_{i}", f"Card {i}", "visa", last4, m, y, 100 + i, "QUJD"))
    with store._lock:
        store._db.execute("ALTER TABLE wallet_cards DROP COLUMN is_default")
        store._db.commit()
    assert [c["id"] for c in Wallet(store).cards() if c["default"]] == ["card_0"]


async def test_wallet_cards_names_the_default_and_a_payment_without_a_card_uses_it():
    _, wallet = new_wallet()
    auto_card(wallet, label="Groceries")
    e2e_card(wallet, label="Work", default=True)
    tool = {x.name: x for x in purchase_tools(None, wallet, CardDeliveries())}
    out = await tool["wallet_cards"].handler({})
    assert "'Work' (mastercard, ends 4444" in out and "[DEFAULT card]" in out and "default card 'Work'" in out
    assert VISA not in out
    cp = tool["confirm_purchase"]
    extra = cp.approval_extra("ap1", {"summary": "Coffee beans", "amount": 5000, "currency": "KRW", "site": "shop.example.com"})
    assert extra["card"]["label"] == "Work"
    other = cp.approval_extra("ap2", {"summary": "Coffee beans", "amount": 5000, "currency": "KRW", "site": "shop.example.com",
                                      "pay_with": "site points"})
    assert "card" not in other                                                        # another payment way named: no card


async def test_a_cards_history_over_http_newest_first_paged_with_the_chat_or_job(tmp_path, monkeypatch):
    app, _ = browser_app(tmp_path, monkeypatch, [])
    w = app.state.schedule_grants.wallet
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/wallet/cards/x/payments")).status_code == 401
        c.headers.update(AUTH)
        m, y = future()
        card = (await c.post("/api/wallet/cards", json={"label": "Everyday", "exp_month": m, "exp_year": y, "auto_pay": True,
                                                         "number": VISA, "cvc": CVC})).json()
        other = (await c.post("/api/wallet/cards", json={"label": "Work", "exp_month": m, "exp_year": y, "auto_pay": False,
                                                          "last4": "4444", "e2e": {"blob": "QUJD"}})).json()
        assert card["default"] and not other["default"]
        assert (await c.get(f"/api/wallet/cards/{card['id']}/payments")).json() == {"items": [], "next_before_id": None}
        assert (await c.get("/api/wallet/cards/nope/payments")).status_code == 404
        tok = current_turn.set({"task_id": 7, "turn_id": "t", "source": "schedule:3", "unattended": True})
        try:
            for i in range(5):
                w.record("approved" if i % 2 else "auto", w.card(card["id"]), "shop.example.com", f"order {i}", 1000 + i, "KRW")
        finally:
            current_turn.reset(tok)
        w.record("approved", w.card(other["id"]), "shop.example.com", "not this card", 9, "KRW")
        page = (await c.get(f"/api/wallet/cards/{card['id']}/payments", params={"limit": 3})).json()
        assert [p["summary"] for p in page["items"]] == ["order 4", "order 3", "order 2"] and page["next_before_id"]
        assert page["items"][0]["task_id"] == 7 and page["items"][0]["source"] == "schedule:3" and page["items"][0]["kind"] == "auto"
        rest = (await c.get(f"/api/wallet/cards/{card['id']}/payments", params={"limit": 3, "before_id": page["next_before_id"]})).json()
        assert [p["summary"] for p in rest["items"]] == ["order 1", "order 0"] and rest["next_before_id"] is None
        r = await c.delete(f"/api/wallet/cards/{card['id']}")
        assert r.status_code == 200 and (await c.get(f"/api/wallet/cards/{other['id']}")).json()["default"]
