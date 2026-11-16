"""The checkout's last steps that need the user: approving the payment in another app, and entering a payment PIN.

`payment_step_wait`: the page asks the payer to approve outside the page (a card company's or bank's app with a code to compare, often a
QR code and a time limit; a one-time code; a 3-D Secure push; a passkey or fingerprint prompt; a payment app's confirmation). The page
is read here, generically: the words that ask for it, a short code shown large, a QR-like square picture, a countdown or expiry time.
ONE card then shows in the chat (payment_steps.PaymentSteps: what is paid, the code, the QR cut out of the page, the time left counting
down, the page's instruction in one line in the user's language, [I approved it] / [Cancel]) and the call waits:
- "approved": the purchase window of the approval the user already gave is opened again for two clicks, and the agent is told to check the
  page and make the click that finishes the payment;
- "cancel": nothing more is clicked, the purchase window and the order close;
- the time ran out (the page's countdown, or its words): the card says so and offers a new code; "new_code" presses the page's own
  "send again" button when there is one (or tells the agent to), and the card follows the new code;
- "code": a one-time code the user typed into the card goes into the page's code box;
- the page moving on by itself (the approval reached the site): the card ends, the agent reads the page.

`browser_type_payment_pin`: the payment PIN the user saved for this site (payment_pins.py, released by the phone after the user approved
the payment and passed fingerprint / face) is entered once, only on that site, only inside the approved purchase window, into a PIN box
or a keypad with digit text (keypad.py). For any other keypad (`browser_pin_keypad_card`, or when typing finds one) the card shows that
keypad region of the page live and forwards the user's taps on it as clicks on the same spot of that region (`PinPad`); it counts the
taps and offers the page's own clear key. When the page moves past the keypad the card says the payment is being finished and the
agent goes on.

`PaymentPaths` (payment_pins.py) remembers per site which way of paying went through without the in-card keypad, and the agent is told
to prefer it next time when the page offers it.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import time
from collections.abc import Callable
from typing import Any

from ..agent import Tool, current_call, emit_ui
from ..agent.fence import data
from ..i18n import N_, t
from ..payment_pins import PaymentLedger, PaymentPaths, PaymentPins, PinDeliveries
from ..payment_steps import PaymentSteps
from ..wallet import site_key
from . import humanize as human
from . import keypad
from .browser import BrowserPolicyError, BrowserSession, browser_owner, extra_auth

POLL_SECONDS = 2.0           # how often the page is looked at while a card waits
WAIT_SECONDS = 600.0         # how long a card waits in a followed chat reply...
UNATTENDED_WAIT = 1800.0     # ...and in a run nobody watches (the urgent notification brings the user)
PIN_WAIT = 90.0              # how long typing waits for the phone to release the PIN
GONE_READS = 2               # readings without the step before the page counts as moved on
QR_MAX = 320                 # pixels of the QR picture on the card
MAX_INSTRUCTION = 160
PAD_MARGIN = 10              # CSS px around the keypad region shown in the card
PAD_SHOT_MAX = 520           # pixels (width) of the keypad picture

KINDS = ("app", "otp", "passkey", "pin_pad")
DEFAULT_LINE = {
    "app": N_("Approve the payment in your card or bank app, then come back here."),
    "otp": N_("Enter the one-time code you received, here or on the page."),
    "passkey": N_("Confirm the payment with your passkey or fingerprint when asked."),
    "pin_pad": N_("Enter your payment PIN on the keypad below."),
}

# Words of a page that asks for the payment to be approved outside it. Generic: no site, card company or payment app is named.
APP_WORDS = ("앱에서 승인", "앱에서 확인", "앱에서 인증", "앱을 열어", "앱 알림", "푸시 알림", "알림을 확인", "승인 요청", "인증 요청을 보냈",
             "휴대폰에서 승인", "휴대폰으로 전송", "승인번호", "인증번호", "확인 번호", "아래 번호", "qr코드", "qr 코드", "큐알",
             "approve in", "approve the payment", "approve this payment", "approve on your", "open your", "app to confirm", "app to approve",
             "push notification", "confirm in your", "confirm on your phone", "check your phone", "sent to your phone", "scan the qr",
             "scan this code", "verification number", "matching number", "number shown", "アプリで承認", "アプリで認証", "認証番号", "承認番号")
PASSKEY_WORDS = ("passkey", "패스키", "생체 인증", "생체인증", "지문", "fingerprint", "face id", "touch id", "biometric", "windows hello",
                 "security key", "보안키", "パスキー", "生体認証")
OTP_WORDS = ("one-time password", "one time password", "one-time code", "otp", "인증번호 입력", "인증번호를 입력", "verification code",
             "security code", "enter the code", "sms code", "문자로 받은", "ワンタイム")
EXPIRED = re.compile(r"만료|시간이\s*(초과|지났)|유효\s*시간이\s*지|expired|timed?\s*out|has run out|期限切れ|タイムアウト", re.I)

# The step as the page shows it, in one frame. Nothing is typed or clicked; the QR element and a "send again" button are only marked.
STEP_JS = r"""() => {
  document.querySelectorAll('[data-kelo-qr],[data-kelo-resend]').forEach((e) => { e.removeAttribute('data-kelo-qr'); e.removeAttribute('data-kelo-resend'); });
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none' && parseFloat(c.opacity || '1') > 0.05; };
  const text = (document.body ? document.body.innerText : '').slice(0, 6000);
  const codeCtx = /인증번호|승인번호|확인\s*번호|코드|번호|숫자|code|number|digits|番号|コード/i;
  const notCode = /주문|order|금액|total|합계|가격|price|전화|phone|tel|사업자|우편|zip|postal|날짜|date|카드번호|card number|会員|注文/i;
  let code = null;
  for (const e of document.querySelectorAll('body *')) {
    if (!shown(e) || e.closest('script,style,input,textarea,select,option,button,a')) continue;
    if ([...e.children].some((c) => (c.innerText || '').trim())) continue;
    const raw = (e.innerText || '').trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9 \-]{2,16}[A-Za-z0-9]$/.test(raw)) continue;
    if (/^\d{1,2}:\d{2}$/.test(raw) || /^\d{4}-\d{2}-\d{2}$/.test(raw) || /^\d{2,3}-\d{3,4}-\d{4}$/.test(raw)) continue;
    const compact = raw.replace(/[ \-]/g, '');
    if (compact.length < 2 || compact.length > 10 || !/\d/.test(compact) || /^[A-Za-z]+$/.test(compact)) continue;
    const ctx = ((e.parentElement && e.parentElement.parentElement ? e.parentElement.parentElement.innerText : '') || '').slice(0, 240);
    const own = (e.getAttribute('class') || '') + ' ' + (e.id || '');
    const fs = parseFloat(getComputedStyle(e).fontSize) || 0;
    let score = fs + (codeCtx.test(ctx) || /code|otp|auth|verify|confirm/i.test(own) ? 14 : 0) - (notCode.test(ctx) ? 30 : 0);
    if (fs < 18 && !codeCtx.test(ctx)) continue;
    if (!code || score > code.score) code = { text: compact, shown: raw, score };
  }
  let qr = null;
  const qrWords = /qr|큐알|二次元/i.test(text);
  for (const e of document.querySelectorAll('img,canvas,svg,[role=img]')) {
    if (!shown(e)) continue;
    const r = e.getBoundingClientRect();
    const ratio = r.width / r.height;
    if (r.width < 72 || ratio < 0.8 || ratio > 1.25) continue;
    const hint = [e.getAttribute('src'), e.getAttribute('alt'), e.getAttribute('class'), e.id, e.getAttribute('aria-label')].join(' ');
    if (!/qr/i.test(hint) && !qrWords) continue;
    if (!qr || r.width > qr.w) qr = { el: e, w: r.width };
  }
  if (qr) qr.el.setAttribute('data-kelo-qr', '1');
  const timers = [];
  const timeRe = /(\d{1,2}\s*:\s*\d{2})|(\d+\s*분\s*\d+\s*초)|(\d+\s*초)|(\d+\s*분)|(\d+\s*min(?:utes?)?(?:\s*\d+\s*s(?:ec(?:onds?)?)?)?)|(\d+\s*s(?:ec(?:onds?)?)?\b)/i;
  for (const e of document.querySelectorAll('body *')) {
    if (!shown(e) || [...e.children].some((c) => (c.innerText || '').trim())) continue;
    const raw = (e.innerText || '').trim();
    if (!raw || raw.length > 60 || !timeRe.test(raw)) continue;
    const own = ((e.getAttribute('class') || '') + ' ' + (e.id || '')).toLowerCase();
    const ctx = ((e.parentElement ? e.parentElement.innerText : '') || '').slice(0, 160);
    if (/timer|countdown|remain|expire|count|time/.test(own) || /남은|유효|이내|안에|까지|remaining|left|expire|within|valid|残り|有効/i.test(ctx + ' ' + raw))
      timers.push(ctx.length < 120 ? ctx : raw);
  }
  const clickable = [...document.querySelectorAll('button,[role=button],a,input[type=button],input[type=submit]')].filter(shown);
  const resend = clickable.find((e) => /재요청|재전송|재발급|다시\s*(받|요청|발급|보내|전송)|새로\s*받|resend|send again|new code|get a new|request again|refresh|retry|再送|再発行/i
    .test((e.innerText || e.value || e.getAttribute('aria-label') || '').trim()));
  if (resend) resend.setAttribute('data-kelo-resend', '1');
  const otpBox = [...document.querySelectorAll('input')].some((e) => shown(e) && !e.disabled && ((e.getAttribute('autocomplete') || '') === 'one-time-code'
    || /otp|one.?time|verif|auth.?code|security.?code|인증번호|인증 번호/i.test([e.name, e.id, e.getAttribute('placeholder'), e.getAttribute('aria-label')].join(' '))));
  return { text, code: code ? { text: code.text, shown: code.shown } : null, qr: !!qr, timers: timers.slice(0, 6), resend: !!resend, otp_box: otpBox };
}"""

UNMARK_STEP_JS = ("() => document.querySelectorAll('[data-kelo-qr],[data-kelo-resend]').forEach((e) => { e.removeAttribute('data-kelo-qr'); "
                  "e.removeAttribute('data-kelo-resend'); })")


def seconds_left(texts: list[str]) -> int | None:
    """The time left that a countdown or expiry text says ("02:59", "2분 59초", "180초", "3분 이내", "4 min 10 s", "90 seconds"), or None."""
    for raw in texts:
        s = " ".join(str(raw or "").split())
        if m := re.search(r"(\d{1,2})\s*:\s*(\d{2})(?!\s*:)", s):
            if not re.search(r"(오전|오후|am|pm|까지|until|by)\s*$", s[:m.start()].strip(), re.I) and not re.search(
                    r"^\s*(까지|am|pm)", s[m.end():], re.I):
                return int(m.group(1)) * 60 + int(m.group(2))
        if m := re.search(r"(\d+)\s*분\s*(\d+)\s*초", s):
            return int(m.group(1)) * 60 + int(m.group(2))
        if m := re.search(r"(\d+)\s*min(?:utes?)?\s*(\d+)\s*s", s, re.I):
            return int(m.group(1)) * 60 + int(m.group(2))
        if m := re.search(r"(\d+)\s*(?:초|s(?:ec(?:onds?)?)?\b|秒)", s, re.I):
            return int(m.group(1))
        if m := re.search(r"(\d+)\s*(?:분|min(?:utes?)?\b|分)", s, re.I):
            return int(m.group(1)) * 60
    return None


def classify(read: dict[str, Any]) -> str:
    """Which kind of step a page reading shows ("" = none): app | otp | passkey."""
    low = str(read.get("text") or "").lower()
    if any(w in low for w in PASSKEY_WORDS):
        return "passkey"
    app = any(w in low for w in APP_WORDS)
    if read.get("otp_box") and any(w in low for w in OTP_WORDS) and not (read.get("code") and app):
        return "otp"
    if app or read.get("qr") or (read.get("code") and (read.get("timers") or extra_auth(low))) or extra_auth(low):
        return "app"
    return ""


def page_line(text: str, kind: str) -> str:
    """The page's own sentence that asks for the step (for when the agent gave none), short."""
    words = {"app": APP_WORDS, "otp": OTP_WORDS, "passkey": PASSKEY_WORDS}.get(kind, APP_WORDS)
    for line in str(text or "").splitlines():
        low = line.lower().strip()
        if 8 <= len(low) <= 200 and any(w in low for w in words):
            return " ".join(line.split())[:MAX_INSTRUCTION]
    return ""


async def read_step(page: Any) -> tuple[dict[str, Any], Any]:
    """The step on the page or one of its frames: (reading with "kind", the frame it was found in or None)."""
    best: tuple[dict[str, Any], Any] | None = None
    texts: list[str] = []
    for frame in [page.main_frame, *[f for f in page.frames if f is not page.main_frame]]:
        try:
            got = await frame.evaluate(STEP_JS)
        except Exception:  # noqa: BLE001 - a frame that cannot be reached
            continue
        if not isinstance(got, dict):
            continue
        texts.append(str(got.get("text") or ""))
        kind = classify(got)
        if kind and (best is None or (got.get("code") and not best[0].get("code")) or (got.get("qr") and not best[0].get("qr"))):
            best = ({**got, "kind": kind}, frame)
    if best is None:
        return {"kind": "", "text": "\n".join(texts)[:6000], "code": None, "qr": False, "timers": [], "resend": False}, None
    return best


async def qr_picture(frame: Any) -> str | None:
    """The QR element cut out of the page, as a small PNG data URL (None when it cannot be taken)."""
    try:
        raw = await frame.locator('[data-kelo-qr="1"]').first.screenshot(timeout=4000, scale="css")
    except Exception:  # noqa: BLE001
        return None

    def shrink(b: bytes) -> str | None:
        from PIL import Image
        im = Image.open(io.BytesIO(b)).convert("L")
        if im.width > QR_MAX:
            im = im.resize((QR_MAX, round(im.height * QR_MAX / im.width)), Image.NEAREST)
        out = io.BytesIO()
        im.save(out, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode() if out.tell() <= 200_000 else None
    return await asyncio.to_thread(shrink, raw)


# ---------------------------------------------------------------------------------------------------------------- the in-card keypad
PAD_RECT_JS = r"""() => {
  const els = [...document.querySelectorAll('[data-kelo-pad]')];
  if (!els.length) return null;
  const rs = els.map((e) => e.getBoundingClientRect()).filter((r) => r.width > 0 && r.height > 0);
  if (!rs.length) return null;
  const x = Math.min(...rs.map((r) => r.left)), y = Math.min(...rs.map((r) => r.top));
  return { x, y, w: Math.max(...rs.map((r) => r.right)) - x, h: Math.max(...rs.map((r) => r.bottom)) - y,
           vw: window.innerWidth, vh: window.innerHeight };
}"""

# Marks the keypad region (its grid of keys, or its canvas) and the page's own clear / delete key next to it.
MARK_PAD_JS = r"""() => {
  document.querySelectorAll('[data-kelo-pad],[data-kelo-clear]').forEach((e) => { e.removeAttribute('data-kelo-pad'); e.removeAttribute('data-kelo-clear'); });
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width >= 8 && r.height >= 8 && c.visibility !== 'hidden' && c.display !== 'none'; };
  const clickable = (e) => e.matches('button,[role=button],a,input[type=button],input[type=image],[onclick],[tabindex]') || getComputedStyle(e).cursor === 'pointer';
  const keys = [...document.querySelectorAll('button,[role=button],a,input[type=button],input[type=image],td,li,span,div,img')]
    .filter((e) => shown(e) && clickable(e)).filter((e, _i, all) => !all.some((o) => o !== e && e.contains(o)));
  const sizes = {};
  for (const e of keys) { const r = e.getBoundingClientRect(); const k = Math.round(r.width / 8) + 'x' + Math.round(r.height / 8); (sizes[k] = sizes[k] || []).push(e); }
  let grid = null;
  for (const g of Object.values(sizes)) {
    if (g.length < 10 || g.length > 16) continue;
    if (new Set(g.map((e) => Math.round(e.getBoundingClientRect().top / 6))).size < 3) continue;
    if (!grid || g.length > grid.length) grid = g;
  }
  let marked = [];
  if (grid) marked = grid;
  else marked = [...document.querySelectorAll('canvas')].filter(shown).slice(0, 1);
  if (!marked.length) return false;
  marked.forEach((e) => e.setAttribute('data-kelo-pad', '1'));
  const near = [...document.querySelectorAll('button,[role=button],a,input[type=button],input[type=image],img,span,div,td')].filter(shown);
  const clear = near.find((e) => /^(지우기|전체\s*삭제|삭제|clear|delete|del|reset|←|⌫|取消|クリア)$/i
    .test((e.innerText || e.value || e.getAttribute('alt') || e.getAttribute('aria-label') || '').trim()));
  if (clear) { clear.setAttribute('data-kelo-clear', '1'); if (!clear.closest('[data-kelo-pad]')) clear.setAttribute('data-kelo-pad', '1'); }
  marked[0].scrollIntoView({ block: 'center', inline: 'center' });
  return true;
}"""


class PinPad:
    """A keypad region of one tab shown inside the payment-step card: a picture of just that region (`shot`), and the user's taps on
    it forwarded as clicks on the same spot (`tap`, fractions of the picture). Only inside that region, only on that page's site, only
    while the card is open. The tab is held for the user (BrowserSession.take_control) while they tap, so no agent step runs at the
    same time. Taps and pictures are not logged and do not go into the agent's context."""

    def __init__(self, session: BrowserSession, owner: str, site: str, frame_url: str = ""):
        self.session, self.owner, self.site = session, owner, site
        self.taps = 0
        self.has_clear = False
        self.open = True
        self.held_by: str | None = None
        self._lock = asyncio.Lock()

    def _page(self) -> Any:
        page = self.session.raw_page(self.owner)
        if page is None:
            raise BrowserPolicyError("the tab is gone")
        from urllib.parse import urlsplit
        if site_key((urlsplit(str(page.url or "")).hostname or "").lower()) != self.site:
            raise BrowserPolicyError("the page left the payment's site")
        return page

    async def _region(self, page: Any) -> tuple[Any, dict[str, float]]:
        """(frame, rect in page CSS px) of the marked keypad with a margin; marked again when the page drew it anew."""
        try:
            return await self._marked(page)
        except BrowserPolicyError:
            if not await mark_pad(page):
                raise
            return await self._marked(page)

    async def _marked(self, page: Any) -> tuple[Any, dict[str, float]]:
        for frame in [page.main_frame, *[f for f in page.frames if f is not page.main_frame]]:
            try:
                r = await frame.evaluate(PAD_RECT_JS)
            except Exception:  # noqa: BLE001
                continue
            if not r:
                continue
            dx = dy = 0.0
            if frame is not page.main_frame:
                try:
                    box = await (await frame.frame_element()).bounding_box()
                    dx, dy = (box or {}).get("x", 0.0), (box or {}).get("y", 0.0)
                except Exception:  # noqa: BLE001
                    pass
            x, y = max(0.0, r["x"] + dx - PAD_MARGIN), max(0.0, r["y"] + dy - PAD_MARGIN)
            w, h = r["w"] + 2 * PAD_MARGIN, r["h"] + 2 * PAD_MARGIN
            return frame, {"x": x, "y": y, "width": w, "height": h}
        raise BrowserPolicyError("the keypad is no longer on the page")

    async def present(self) -> bool:
        """The keypad is still on the page (marked again when the page drew it anew after a press)."""
        try:
            page = self._page()
        except BrowserPolicyError:
            return False
        try:
            await self._region(page)
            return True
        except BrowserPolicyError:
            pass
        _frame, got = await keypad.find(page)
        if got.get("mode") != "protected" or not await mark_pad(page):
            return False
        try:
            await self._region(page)
            return True
        except BrowserPolicyError:
            return False

    async def shot(self) -> tuple[bytes, int, int]:
        """A JPEG of the keypad region now (width, height in pixels)."""
        if not self.open:
            raise BrowserPolicyError("this keypad is closed")
        page = self._page()
        _frame, r = await self._region(page)
        raw = await self.session.shoot(page, self.owner, quality=70, clip=r, css_scale=True)   # keeps a phone view's screen

        def shrink(b: bytes) -> tuple[bytes, int, int]:
            from PIL import Image
            im = Image.open(io.BytesIO(b)).convert("RGB")
            if im.width > PAD_SHOT_MAX:
                im = im.resize((PAD_SHOT_MAX, round(im.height * PAD_SHOT_MAX / im.width)), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=72)
            return out.getvalue(), im.width, im.height
        return await asyncio.to_thread(shrink, raw)

    async def tap(self, fx: float, fy: float, device: str) -> int:
        """A tap at (fx, fy), fractions 0..1 of the keypad picture: a click on the same spot of the page. Returns the taps so far."""
        if not self.open:
            raise BrowserPolicyError("this keypad is closed")
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            raise BrowserPolicyError("the tap is outside the keypad")
        async with self._lock:
            page = self._page()
            _frame, r = await self._region(page)
            if self.held_by is None:
                self.session.take_control(self.owner, device)   # the user's hands are on this tab: the agent waits
                self.held_by = device
            await page.mouse.click(r["x"] + fx * r["width"], r["y"] + fy * r["height"])
            self.taps += 1
            return self.taps

    async def clear(self, device: str) -> int:
        """The page's own clear / delete key, pressed for the user. Returns the taps counted after it (0 after a clear)."""
        if not self.open:
            raise BrowserPolicyError("this keypad is closed")
        async with self._lock:
            page = self._page()
            for frame in [page.main_frame, *[f for f in page.frames if f is not page.main_frame]]:
                loc = frame.locator('[data-kelo-clear="1"]')
                try:
                    if await loc.count():
                        if self.held_by is None:
                            self.session.take_control(self.owner, device)
                            self.held_by = device
                        await loc.first.click(timeout=4000)
                        self.taps = 0
                        return self.taps
                except Exception:  # noqa: BLE001
                    continue
        raise BrowserPolicyError("the page has no clear key")

    async def close(self) -> None:
        self.open = False
        if self.held_by is not None:
            self.held_by = None
            await self.session.release_control(self.owner)


async def mark_pad(page: Any) -> bool:
    """Marks the protected keypad on the page (or a frame) for the in-card view; True when one was found."""
    for frame in [page.main_frame, *[f for f in page.frames if f is not page.main_frame]]:
        try:
            if await frame.evaluate(MARK_PAD_JS):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


# ---------------------------------------------------------------------------------------------------------------- the tools
def payment_step_tools(session: BrowserSession | None, steps: PaymentSteps | None, ledger: PaymentLedger | None, *,
                       pins: PaymentPins | None = None, pin_deliveries: PinDeliveries | None = None, paths: PaymentPaths | None = None,
                       orders: Any = None, vault_locked: Callable[[], bool] = lambda: False,
                       pads: dict[str, PinPad] | None = None) -> list[Tool]:
    """`steps`: the open cards (None: no app, the agent is told to ask in chat). `ledger`: the approved payments by tab. `pads`: step id
    -> the in-card keypad of that step (the server's endpoints use it)."""
    if session is None or ledger is None:
        return []
    pads = pads if pads is not None else {}

    def turn_info() -> dict[str, Any]:
        from ..workers import current_turn
        return dict(current_turn.get() or {})

    def unattended() -> bool:
        return bool(turn_info().get("unattended"))

    def approved_here() -> Any:
        return ledger.last(browser_owner.get(), session.current_host())

    def base_event(kind: str, payment: Any, instruction: str) -> dict[str, Any]:
        info = turn_info()
        return {"kind": kind, "site": payment.host, "order": payment.order(), "instruction": instruction or t(DEFAULT_LINE[kind]),
                "tab": browser_owner.get(), "task_id": info.get("task_id"), "turn_id": info.get("turn_id"), "id": current_call.get(),
                "urgent": True, "code": None, "code_shown": None, "qr": None, "expires_at": None, "seconds_total": None,
                "can_resend": False, "needs_code": kind == "otp", "taps": 0, "can_clear": False}

    async def show(ev: dict[str, Any]) -> bool:
        return await emit_ui({k: v for k, v in ev.items() if k != "id"} | {"id": ev.get("id") or current_call.get()})

    async def page_text() -> str:
        try:
            return await session.snapshot(handing=False)
        except Exception as e:  # noqa: BLE001
            return f"(the page could not be read: {type(e).__name__})"

    def note_path(payment: Any, path: str, outcome: str) -> None:
        if paths is not None and path:
            try:
                paths.record(payment.host, path, outcome)
            except Exception:  # noqa: BLE001
                pass

    def path_hint(host: str) -> str:
        return (" " + paths.hint(host)) if paths is not None and paths.hint(host) else ""

    async def wait_card(ev: dict[str, Any], payment: Any, *, frame: Any = None, pad: PinPad | None = None) -> tuple[str, dict[str, Any]]:
        """Show the card and follow it until it ends. Returns (outcome, last answer): approved | cancel | done | moved | timeout |
        new_code_manual | finishing."""
        assert steps is not None
        ev = steps.open(ev)
        sid = ev["step_id"]
        if pad is not None:
            pads[sid] = pad
        await show(ev)
        result: list[str] = ["closed"]
        try:
            outcome, ans = await follow(sid, ev, pad)
            result[0] = outcome
            return outcome, ans
        finally:
            if pad is not None:
                await pad.close()
                pads.pop(sid, None)
            if steps.is_open(sid):
                final = steps.close(sid, {"approved": "approved", "done": "done", "cancel": "cancelled", "moved": "done",
                                          "finishing": "finishing", "timeout": "timeout", "new_code_manual": "renewing"}.get(result[0], "closed"))
                if final:
                    try:
                        await show(final)
                    except Exception:  # noqa: BLE001 - a stopped reply has nobody to show it to
                        pass
            page = session.raw_page(browser_owner.get())
            if page is not None:
                for fr in page.frames:
                    try:
                        await fr.evaluate(UNMARK_STEP_JS)
                    except Exception:  # noqa: BLE001
                        pass

    async def follow(sid: str, ev: dict[str, Any], pad: PinPad | None) -> tuple[str, dict[str, Any]]:
        assert steps is not None
        limit = time.monotonic() + (UNATTENDED_WAIT if unattended() else WAIT_SECONDS)
        missing = 0
        state = "waiting"
        while time.monotonic() < limit:
            ans = await steps.next_answer(sid, POLL_SECONDS)
            if ans is not None:
                what = ans["answer"]
                if what in ("approved", "cancel", "done"):
                    return what, ans
                if what == "code" and ev.get("kind") == "otp":
                    try:
                        await session.fill_code(ans["code"], submit=True)
                    except BrowserPolicyError:
                        pass
                    ev = steps.update(sid, state="checking")
                    await show(ev)
                    continue
                if what == "new_code":
                    page = session.raw_page(browser_owner.get())
                    clicked = False
                    if page is not None:
                        for fr in [page.main_frame, *[f for f in page.frames if f is not page.main_frame]]:
                            try:
                                await fr.evaluate(STEP_JS)
                                loc = fr.locator('[data-kelo-resend="1"]')
                                if await loc.count():
                                    await loc.first.click(timeout=5000)
                                    clicked = True
                                    break
                            except Exception:  # noqa: BLE001
                                continue
                    if not clicked:
                        return "new_code_manual", ans
                    ev = steps.update(sid, state="renewing", code=None, code_shown=None, expires_at=None)
                    await show(ev)
                    await asyncio.sleep(1.0)
                    state = "renewing"
                    continue
            # look at the page again
            if pad is not None:
                if not await pad.present():
                    missing += 1
                    if missing >= GONE_READS:
                        ev = steps.update(sid, state="finishing")
                        await show(ev)
                        return "finishing", {}
                else:
                    missing = 0
                    if ev.get("taps") != pad.taps:
                        ev = steps.update(sid, taps=pad.taps)
                        await show(ev)
                continue
            page = session.raw_page(browser_owner.get())
            if page is None:
                continue
            read, fr = await read_step(page)
            if not read["kind"]:
                missing += 1
                if missing >= GONE_READS:
                    return "moved", {}
                continue
            missing = 0
            changes: dict[str, Any] = {}
            code = read.get("code") or {}
            if code.get("text") and code.get("text") != ev.get("code"):
                changes.update(code=code["text"], code_shown=code.get("shown"))
                if fr is not None and read.get("qr"):
                    changes["qr"] = await qr_picture(fr)
            secs = seconds_left(read.get("timers") or [])
            if secs is not None and (ev.get("expires_at") is None or changes.get("code") or state == "renewing"):
                changes.update(expires_at=int(time.time() + secs), seconds_total=secs)
            expired = bool(EXPIRED.search(str(read.get("text") or ""))) or (
                ev.get("expires_at") is not None and time.time() >= ev["expires_at"] and (secs in (None, 0) or secs == ev.get("seconds_total")))
            new_state = "expired" if expired and not changes.get("code") else "waiting"
            if new_state != ev.get("state"):
                changes["state"] = new_state
            if bool(read.get("resend")) != ev.get("can_resend"):
                changes["can_resend"] = bool(read.get("resend"))
            state = new_state
            if changes:
                ev = steps.update(sid, **changes)
                await show(ev)
        return "timeout", {}

    async def finish(sid_outcome: str, payment: Any, kind: str, note: str = "") -> str:
        host = payment.host
        if sid_outcome == "cancel":
            session.end_purchase()
            ended = orders.end() if orders is not None else None
            ledger.forget(browser_owner.get())
            if pin_deliveries is not None and payment.pin_slot:
                pin_deliveries.discard(payment.pin_slot)
            return ("The user CANCELLED this payment on the card. Do NOT click pay, confirm, approve or complete, and do not try another way "
                    "to pay. Leave the payment unfinished and tell the user plainly that nothing was paid"
                    + (f"; the order on {ended.site} is closed." if ended is not None else "."))
        if sid_outcome == "timeout":
            return ("The user did not answer the payment card in time. Do not click anything that pays. Tell the user the payment was not "
                    "finished and that they can ask again.\n\n" + await page_text())
        if sid_outcome == "new_code_manual":
            session.allow_purchase(host, actions=2)
            return ("The code ran out and the user asked for a new one, but no 'send again' button was found. Find the page's button that "
                    "sends or shows a new code and click it (covered for the next 2 clicks), then call payment_step_wait again.\n\n"
                    + await page_text())
        session.allow_purchase(host, actions=2)   # the user's answer finishes the payment they approved: two clicks on that site
        if kind in ("app", "otp", "passkey"):
            note_path(payment, "app_approval", "smooth")
        if sid_outcome == "approved":
            await asyncio.sleep(min(POLL_SECONDS, 1.5))
            head = ("The user says they approved the payment in the other app. If the page now shows a button that finishes the payment "
                    "(complete, confirm, done), click it now (covered by the approval for the next 2 clicks on this site). If the page "
                    "still shows the same approval step, the approval has not reached the site yet: call payment_step_wait again.")
        elif sid_outcome == "finishing":
            head = ("The page moved past the PIN the user entered on the keypad. The payment is being finished: if a button that "
                    "completes it is left, click it (covered for the next 2 clicks on this site).")
        else:
            head = "The approval step is no longer on the page (it went through, or the user finished it themselves)."
        return (f"{head}{note} Then read the page and tell the user the result with the order or receipt number copied from the page; "
                "if the page shows an error, say so and do not pay again." + "\n\n" + await page_text())

    # ---------------------------------------------------------------- payment_step_wait
    def step_validate(args: dict[str, Any]) -> str | None:
        if approved_here() is None:
            return ("no approved payment on this site: payment_step_wait belongs to a payment confirm_purchase approved on the site the "
                    "browser is on")
        if steps is not None and steps.open_for(browser_owner.get()):
            return "a payment card for this tab is already open; wait for it"
        if len(str(args.get("instruction") or "")) > MAX_INSTRUCTION * 2:
            return f"instruction must be one short line (at most {MAX_INSTRUCTION} characters)"
        return None

    async def step_wait(args: dict[str, Any]) -> str:
        payment = approved_here()
        assert payment is not None
        page = await session.page()
        read, frame = await read_step(page)
        if not read["kind"]:
            return ("No step that asks to approve the payment elsewhere was found on this page or its frames (no approval words, code, QR "
                    "or countdown). Read the page: maybe it already went through, or it needs a click first.\n\n" + await page_text())
        kind = read["kind"]
        line = " ".join(str(args.get("instruction") or "").split())[:MAX_INSTRUCTION] or page_line(read.get("text") or "", kind)
        if steps is None:
            return ("The page asks to approve the payment outside it, and no app is connected to show a card. Tell the user in one line what "
                    "to do (from the page), show any code the page shows, and wait for them to say it is done; do not click meanwhile.")
        ev = base_event(kind, payment, line)
        code = read.get("code") or {}
        if code.get("text"):
            ev.update(code=code["text"], code_shown=code.get("shown"))
        if read.get("qr") and frame is not None:
            ev["qr"] = await qr_picture(frame)
        secs = seconds_left(read.get("timers") or [])
        if secs is not None:
            ev.update(expires_at=int(time.time() + secs), seconds_total=secs)
        ev["can_resend"] = bool(read.get("resend"))
        await session.card(t("Waiting for you to approve the payment"), force=True)
        outcome, _ans = await wait_card(ev, payment, frame=frame)
        return await finish(outcome, payment, kind)

    # ---------------------------------------------------------------- browser_type_payment_pin
    def pin_validate(args: dict[str, Any]) -> str | None:
        if pins is None or pin_deliveries is None:
            return "this app keeps no payment PINs"
        payment = approved_here()
        host = session.current_host()
        if payment is None:
            return "no approved payment on this site: a payment PIN is entered only right after confirm_purchase approved paying here"
        pin = pins.for_host(host)
        if pin is None:
            return (f"no payment PIN is saved for {site_key(host)}. Ask the user to save one (request_payment_pin), or let them enter it "
                    "themselves (browser_pin_keypad_card shows the page's keypad in their card)")
        if vault_locked():
            pins.log(pin, "pin-refused", "the keychain is locked", False)
            return f"the user locked the keychain, so the payment PIN cannot be used now. Tell them they can unlock it in {t('My → Keychain')}"
        if not payment.pin_slot or pin_deliveries.slot_pin(payment.pin_slot) is None:
            pins.log(pin, "pin-refused", f"no release for this payment on {host}", False)
            if payment.pin_used:
                return "the payment PIN was already entered once for this approval; it is never entered twice. Call confirm_purchase again"
            return ("the payment PIN was not released for this payment (it is released only with the user's approval of a payment on this "
                    "site, or for a task allowed to use it, and once)")
        if site_key(host) != pin["site"] or site_key(payment.host) != pin["site"]:
            pins.log(pin, "pin-refused", f"other site: {host}", False)
            return f"the browser is on {host}, but the PIN belongs to {pin['site']}. It is entered only there"
        if not session.purchase_active(host):
            pins.log(pin, "pin-refused", "outside the approved purchase window", False)
            return "the approval for this payment ran out (time or steps). Call confirm_purchase again"
        return None

    async def type_pin(args: dict[str, Any]) -> str:
        assert pins is not None and pin_deliveries is not None
        payment = approved_here()
        host = session.current_host()
        pin = pins.for_host(host)
        assert payment is not None and pin is not None
        page = await session.page()
        frame, got = await keypad.find(page)
        if got.get("mode") == "protected":
            pin_deliveries.discard(payment.pin_slot)   # not used: the user enters it themselves
            return await hand_pad(payment, pin)
        if frame is None or not got.get("mode"):
            return ("No PIN box or keypad was found on this page. Nothing was typed (the PIN is still available for this payment until it "
                    "is used or the time runs out). Open the step of the checkout that asks for the PIN first.\n\n" + await page_text())
        if not pin_deliveries.arrived(payment.pin_slot):
            await session.card(t("Waiting for your phone to release the payment PIN"), force=True)
        secret = await pin_deliveries.wait(payment.pin_slot, PIN_WAIT)
        if secret is None:
            pins.log(pin, "pin-not-sent", f"payment on {host} approved, but the phone did not release the PIN", False)
            return ("The phone did not release the payment PIN in time (the user did not confirm with fingerprint or face). Nothing was "
                    "typed. Ask the user to tap the PIN card in the chat, or to enter the PIN themselves; do not try anything else.")
        payment.pin_used = True
        whole = "".join(secret.digits())
        try:
            async def click(loc: Any) -> None:
                await human.click(page, locator=loc, timeout_ms=5000, fallback=lambda ms: loc.click(timeout=ms))

            async def typer(loc: Any, ch: str) -> None:
                await human.type_text(page, ch, locator=loc, secret=True, timeout_ms=5000,
                                      fallback=lambda ms: loc.press_sequentially(ch, timeout=ms))
            try:
                done = await keypad.type_pin(page, secret.digits(), click=click, typer=typer)
            except keypad.KeypadError as e:
                pins.log(pin, "pin-stopped", f"{e.kind} on {host} after {e.pressed} of {len(secret)} digits", False)
                if e.kind == "protected":
                    return await hand_pad(payment, pin)
                return (f"The payment PIN could not be entered ({e}); {e.pressed} digit(s) were entered before it stopped. Do not try again "
                        "with the PIN. Hand the step to the user: call payment_step_wait if the page shows a step to finish, or tell the "
                        "user to enter the PIN on the page.")
        finally:
            secret.wipe()
            pin_deliveries.discard(payment.pin_slot)   # once per approval
        pins.used(pin["id"], host)
        note_path(payment, "pin_plain", "smooth")
        await session.card(t("Payment PIN entered"), force=True)
        text = (await page_text()).replace(whole, "[PIN hidden]")
        whole = ""
        where = "a PIN box" if done.mode == "input" else "the page's keypad"
        return (f"Entered the saved payment PIN ({done.pressed} digits) in {where}{' in ' + done.frame if done.frame else ''}. You never see "
                "it. Check the page: if a button that finishes the payment is left, click it (covered by the approval); then tell the "
                "user the result.\n\n" + text)

    async def hand_pad(payment: Any, pin: dict[str, Any] | None) -> str:
        """The user enters the PIN on a live picture of the page's keypad inside the card."""
        page = await session.page()
        if pin is not None and pins is not None:
            pins.log(pin, "pin-handed", f"the keypad on {payment.host} is protected; the user enters the PIN in the card")
        note_path(payment, payment.path or "site_method", "protected")
        if steps is None or not await mark_pad(page):
            return ("This PIN keypad is entered by the user. Nothing was typed; do not click its keys. Tell the user to enter their PIN "
                    "on the page (live view), and wait for them to say it is done.")
        pad = PinPad(session, browser_owner.get(), site_key(payment.host))
        try:
            pad.has_clear = await _has_clear(page)
        except Exception:  # noqa: BLE001
            pad.has_clear = False
        ev = base_event("pin_pad", payment, t(DEFAULT_LINE["pin_pad"]))
        ev.update(can_clear=pad.has_clear, needs_code=False)
        await session.card(t("Waiting for you to enter your payment PIN"), force=True)
        outcome, _ans = await wait_card(ev, payment, pad=pad)
        return await finish(outcome, payment, "pin_pad", path_hint(payment.host))

    async def _has_clear(page: Any) -> bool:
        for fr in page.frames:
            try:
                if await fr.locator('[data-kelo-clear="1"]').count():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def pin_pad(args: dict[str, Any]) -> str:
        payment = approved_here()
        assert payment is not None
        page = await session.page()
        _frame, got = await keypad.find(page)
        if got.get("mode") != "protected" and got.get("mode"):
            return ("The PIN entry on this page is ordinary (a PIN box or a keypad with digit buttons): use browser_type_payment_pin when a "
                    "PIN is saved, or let the user enter it in the live view.")
        return await hand_pad(payment, pins.for_host(session.current_host()) if pins is not None else None)

    def pad_validate(args: dict[str, Any]) -> str | None:
        if approved_here() is None:
            return "no approved payment on this site: the PIN keypad card belongs to a payment confirm_purchase approved here"
        if steps is not None and steps.open_for(browser_owner.get()):
            return "a payment card for this tab is already open; wait for it"
        return None

    out = [
        Tool("payment_step_wait",
             "After confirm_purchase approved a payment, when the page asks the payer to approve it outside the page (a card or bank "
             "app with a code or QR code and a time limit, a one-time code, a 3-D Secure or push confirmation, a passkey or fingerprint "
             "prompt): show the user ONE card with the code, the QR code, the time left and the page's instruction, and wait for their "
             "answer. Do not click anything meanwhile.",
             {"type": "object", "properties": {
                 "instruction": {"type": "string", "description": "what the page asks the user to do, as one plain short line in the "
                                                                  "user's language (no codes or numbers from the page)"}}, "required": []},
             step_wait, validate=step_validate, untrusted=True, source="page"),
        Tool("browser_pin_keypad_card",
             "After confirm_purchase approved a payment, when the page asks for a PIN on a keypad that hides or scrambles its digits "
             "(pictures, canvas, a security keyboard): this shows that keypad inside the user's chat card so they enter the PIN "
             "themselves (do not click its keys), and waits until the page moves on.",
             {"type": "object", "properties": {}, "required": []}, pin_pad, validate=pad_validate, untrusted=True, source="page"),
    ]
    if pins is not None and pin_deliveries is not None:
        out.append(Tool(
            "browser_type_payment_pin",
            "Enter the payment PIN the user saved for this site into the checkout's PIN box or plain digit keypad, once, only right after "
            "confirm_purchase approved this payment here. You never see the PIN. A protected keypad is shown to the user in a card "
            "instead.",
            {"type": "object", "properties": {}, "required": []}, type_pin, validate=pin_validate, untrusted=True, source="page"))
    return out


def request_pin_tools(pins: PaymentPins | None) -> list[Tool]:
    """request_payment_pin: a secure fill-in card in the chat where the user saves a payment PIN for one site (kept on the phone)."""
    if pins is None:
        return []
    from ..secret_requests import ask
    from ..payment_pins import PinError, pin_site

    async def request(args: dict[str, Any]) -> str:
        try:
            site = pin_site(args.get("site"))
        except PinError as e:
            return f"error: {e}"
        reason = " ".join(str(args.get("reason") or "").split())[:200]
        req, state = await ask("payment_pin", name=site, label=reason or site, hosts=[site], reason=reason)
        if state == "saved":
            return (f"The user saved a payment PIN for {site}; you never see it. It is entered only with browser_type_payment_pin, after a "
                    "payment there is approved and the phone releases it. Go on with the task now.")
        if state == "cancelled":
            return "The user closed the PIN entry without saving. Do not ask again unless they bring it up."
        if state in ("replaced", "expired"):
            return "This PIN entry was replaced by a newer one or ran out; nothing was saved."
        return ("A secure entry for the payment PIN is now shown in the chat below your message. Tell the user in one short sentence to save "
                "it there; never ask for a PIN in chat. Once they save it you are told and go on by yourself.")

    return [Tool("request_payment_pin",
                 "Ask the user to save the payment PIN a site's checkout asks for (a 4-8 digit number), in a secure entry in the chat. It "
                 "is kept on their phone and released only for a payment they approve there; you never see it. Never ask for a PIN in "
                 "chat.",
                 {"type": "object", "properties": {
                     "site": {"type": "string", "description": "the site whose checkout asks for the PIN"},
                     "reason": {"type": "string", "description": "one short line: what it is for"}}, "required": ["site"]},
                 request)]
