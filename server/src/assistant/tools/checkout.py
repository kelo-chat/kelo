"""Checkout steps by page STRUCTURE (owner decision 2026-11-16, docs/no-word-lists.md): no word of any language decides here.

A page is a final checkout step when its structure says so:
- visible payment inputs (`autocomplete="cc-*"`), or numeric inputs (`inputmode="numeric"`) inside a form that also holds an
  amount element (schema.org `totalPaymentDue` / `totalPrice`, `data-total`-style attributes, `<output>`) and a submit control;
- the same inside ANY frame of the page, cross-origin ones too (each frame is read through the browser driver, with a short timeout;
  a frame that cannot be read counts as unknown, not as payment; no origin or provider list);
- an order total element near a submit control (inside one form, or one of its first ancestors).

On such a page the generic click / type-with-Enter / point tools refuse: the way through is `checkout_review` (what is bought and the
total; the owner's grant or a card) and `checkout_submit` (clicks the control recorded at the review, only on the same page).

Pure helpers plus the page probe; the two tools themselves live in purchase.py next to the approval they reuse.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .page_paths import with_path

REFUSAL = ("this is a checkout step: use checkout_review / checkout_submit. checkout_review states what is bought and the total shown "
           "on the page (the owner's card or their standing permission covers it); checkout_submit then presses the pay control "
           "recorded at the review. Other steps of the checkout (lists, fields, shipping) work with browser_type and the fill tools. If you have no "
           "checkout_review tool you cannot pay: the main assistant hands the checkout to a payment specialist (delegate with kind checkout, "
           "url = this page); a background job reports done with a checkout brief in details instead.")

REVIEW_SECONDS = 20 * 60        # a review that was approved stays usable this long
# True inside the run of a later check of an unknown order (order_checks.py): it may open and read the tab, which is otherwise
# blocked while an attempt of that task is pressed or unknown. It has no pay tools.
CHECKING: ContextVar[bool] = ContextVar("order_check_run", default=False)
MIN_FRAME = (100, 30)           # a frame smaller than this (width, height) is a hidden helper frame, not a form


FRAME_CAP = 12                  # frames of one page that are read (the main frame is read by PROBE_JS)
FRAME_SECONDS = 2.0             # time one frame gets to answer; a frame that does not answer counts as unknown, not as payment


def origin_of(url: str) -> str:
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


# What the page is, read in the page itself. `want` = {role, label} of the control checkout_submit may press, else null; its CSS path
# comes back as `target` (page_paths: nothing is written into the page).
PROBE_JS = with_path("""(want) => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const vis = e => { const r = e.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false;
                     const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const TOTAL = '[itemprop~="totalPaymentDue"],[itemprop~="totalPrice"],[data-total],[data-order-total],[data-checkout-total],output';
  const CONTROL = 'button, input[type=submit], input[type=button], input[type=image], [role=button]';
  const roleOf = e => { const r = (e.getAttribute('role') || '').toLowerCase(); return r || (e.tagName.toLowerCase() === 'a' ? 'link' : 'button'); };
  const labelOf = e => clean(e.getAttribute('aria-label') || e.innerText || e.value || e.getAttribute('alt') || e.getAttribute('title') || '').slice(0, 80);
  const submitType = e => (e.tagName === 'BUTTON' && (!e.getAttribute('type') || e.getAttribute('type').toLowerCase() === 'submit')) ||
                          (e.tagName === 'INPUT' && ['submit', 'image'].includes((e.getAttribute('type') || '').toLowerCase()));
  const hasNumber = e => /\\d/.test(e.innerText || e.textContent || e.value || '');
  const totals = Array.from(document.querySelectorAll(TOTAL)).filter(e => vis(e) && hasNumber(e));
  const cc = Array.from(document.querySelectorAll('input[autocomplete^="cc-"]')).filter(vis);
  const controls = Array.from(document.querySelectorAll(CONTROL)).filter(e => vis(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true');
  const near = c => { let n = c; for (let i = 0; n && i < 5; i++, n = n.parentElement) { if (n === document.body) return false; if (totals.some(t => n.contains(t))) return true; } return false; };
  const formOf = c => c.form || c.closest('form');
  const numeric = Array.from(document.querySelectorAll('input[inputmode=numeric]')).filter(e => {
    const f = e.form; return vis(e) && f && Array.from(f.querySelectorAll(TOTAL)).some(hasNumber) && Array.from(f.querySelectorAll(CONTROL)).some(submitType); });
  const payForms = new Set([...cc, ...numeric].map(e => e.form).filter(Boolean));
  const totalForms = new Set(totals.map(t => t.closest('form')).filter(Boolean));
  const cand = controls.filter(c => (submitType(c) && formOf(c) && (payForms.has(formOf(c)) || totalForms.has(formOf(c)))) || near(c));
  const candidate = c => cand.includes(c);
  let marked = false, target = '';
  if (want) {
    const hits = controls.filter(c => roleOf(c) === want.role && labelOf(c) === want.label);
    const pick = hits.length === 1 ? hits[0] : (hits.filter(candidate).length === 1 ? hits.filter(candidate)[0] : null);
    if (pick) { target = keloPath(pick); marked = true; }
    else if (hits.length > 1) marked = 'ambiguous';
  }
  const describe = c => ({role: roleOf(c), label: labelOf(c), submit: submitType(c), pay_form: !!(formOf(c) && payForms.has(formOf(c)))});
  const curEl = document.querySelector('[itemprop~="priceCurrency"],meta[property="og:price:currency"],[data-currency]');
  const currency = curEl ? clean(curEl.getAttribute('content') || curEl.getAttribute('data-currency') || curEl.textContent).toUpperCase().slice(0, 3) : '';
  return {url: location.href, currency, text: (document.body ? document.body.innerText : '').slice(0, 40000), payment_inputs: cc.length + numeric.length,
          totals: totals.map(t => clean(t.innerText || t.textContent || t.value).slice(0, 60)).slice(0, 5),
          controls: cand.map(describe).slice(0, 12), marked, target};
}""")

# What one child frame holds (read in that frame): visible card inputs, or numeric inputs in a form with an amount and a submit.
FRAME_JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false;
                     const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const TOTAL = '[itemprop~="totalPaymentDue"],[itemprop~="totalPrice"],[data-total],[data-order-total],[data-checkout-total],output';
  const SUBMIT = 'button:not([type]), button[type=submit], input[type=submit], input[type=image]';
  const hasNumber = e => /\\d/.test(e.innerText || e.textContent || e.value || '');
  const cc = Array.from(document.querySelectorAll('input[autocomplete^="cc-"]')).filter(vis);
  const numeric = Array.from(document.querySelectorAll('input[inputmode=numeric]')).filter(e => {
    const f = e.form; return vis(e) && f && Array.from(f.querySelectorAll(TOTAL)).some(hasNumber) && f.querySelector(SUBMIT); });
  return cc.length + numeric.length;
}"""


async def frame_payment_forms(page: Any) -> list[str]:
    """The addresses of the page's child frames (cross-origin ones too) that hold a visible payment form. Frames that cannot be read
    or do not answer in time are skipped (unknown)."""
    found: list[str] = []
    for frame in list(page.frames)[1:FRAME_CAP + 1]:
        try:
            box = await asyncio.wait_for((await frame.frame_element()).bounding_box(), FRAME_SECONDS)
            if not box or box["width"] < MIN_FRAME[0] or box["height"] < MIN_FRAME[1]:
                continue
            if await asyncio.wait_for(frame.evaluate(FRAME_JS), FRAME_SECONDS):
                found.append(frame.url or "frame")
        except Exception:  # noqa: BLE001 - an unreadable frame is unknown, not payment
            continue
    return found


# Describes one element (a ref the model named): its role and label as PROBE_JS reads them.
DESCRIBE_JS = """(e) => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const r = (e.getAttribute('role') || '').toLowerCase();
  return {role: r || (e.tagName.toLowerCase() === 'a' ? 'link' : 'button'),
          label: clean(e.getAttribute('aria-label') || e.innerText || e.value || e.getAttribute('alt') || e.getAttribute('title') || '').slice(0, 80)};
}"""


@dataclass
class Probe:
    url: str
    text: str
    payment_inputs: int
    totals: list[str]
    frames: list[str]            # child frames that hold a visible payment form
    controls: list[dict[str, Any]]
    marked: Any
    currency: str = ""           # the ISO code the page itself declares (schema.org priceCurrency, data-currency), "" when it declares none
    target: str = ""            # the CSS path of the control `want` named (when marked is True)

    @property
    def path(self) -> str:
        return urlsplit(self.url).path or "/"

    @property
    def is_checkout(self) -> bool:
        """The page's structure is a final checkout step: payment inputs (in the page or any of its frames), or an order total near a submit."""
        return bool(self.payment_inputs or self.frames or (self.totals and self.controls))


def parse_probe(raw: Any) -> Probe | None:
    if not isinstance(raw, dict):
        return None
    return Probe(str(raw.get("url") or ""), str(raw.get("text") or ""), int(raw.get("payment_inputs") or 0),
                 [str(x) for x in raw.get("totals") or []], [str(x) for x in raw.get("frames") or []],
                 [c for c in raw.get("controls") or [] if isinstance(c, dict)], raw.get("marked"),
                 str(raw.get("currency") or ""), str(raw.get("target") or ""))


async def read_page(session: Any, want: dict[str, str] | None = None) -> Probe | None:
    """Probe the tab's current page (the main frame and every child frame). None when there is no page or it cannot be read."""
    try:
        page = await session.page()
        raw = await page.evaluate(PROBE_JS, want)
        if isinstance(raw, dict):
            raw["frames"] = await frame_payment_forms(page)
        return parse_probe(raw)
    except Exception:  # noqa: BLE001 - a page that cannot be read is not judged a checkout step
        return None


async def refusal(session: Any) -> str | None:
    """REFUSAL when the tab's page is a final checkout step by structure, else None."""
    probe = await read_page(session)
    return REFUSAL if probe is not None and probe.is_checkout else None


def guarded(session: Any, inner: Any = None, *, only_submit: bool = False, tool: str = "") -> Any:
    """A tool's `hold`: first `inner` (waiting while the user controls the browser), then the checkout-step refusal.
    `only_submit`: a typing step refuses only when it presses Enter (the form is submitted by it). `tool`: the step's own name
    (a point press is judged by what is at its point)."""
    async def hold(args: dict[str, Any]) -> str | None:
        held = await inner(args) if inner is not None else None
        if held:
            return held
        if only_submit and not args.get("submit"):
            return None
        if (said := await refusal(session)) is not None:
            return said
        net = getattr(session, "submit_refusal", None)   # a submit control the step judge says spends money (no checkout structure here)
        return await net(tool or ("browser_type" if only_submit else "browser_click"), args) if net is not None else None
    return hold


# ---------------------------------------------------------------- the total shown on the page
_NUMBER = re.compile(r"\d(?:[\d.,\u00a0\u202f]*\d)?")


def numbers_in(text: str) -> set[float]:
    """Every amount the text could be read as (both "1.234,56" and "1,234.56" styles; a separator followed by exactly three digits as
    thousands). Used only to see whether the total the model states is on the page."""
    found: set[float] = set()
    for m in _NUMBER.finditer(text or ""):
        tok = re.sub(r"[\u00a0\u202f]", "", m.group(0))
        flat = tok
        # thousands separators: "1,234" / "1.234" / "1,234,567"; a decimal: the last separator with 1-2 digits after it
        digits = re.sub(r"[.,]", "", flat)
        if digits.isdigit():
            found.add(float(digits))
        last = max(flat.rfind(","), flat.rfind("."))
        if last > 0 and 1 <= len(flat) - last - 1 <= 2:
            head = re.sub(r"[.,]", "", flat[:last])
            if head.isdigit() and flat[last + 1:].isdigit():
                found.add(float(f"{head}.{flat[last + 1:]}"))
    return found


def total_shown(text: str, total: float) -> bool:
    return any(abs(n - total) < 0.005 for n in numbers_in(text))


def amount_over(totals: list[str], approved: float) -> float | None:
    """The first amount the page's order total elements show that is above `approved` (major units), None when every total element
    shows the approved amount or less, or none can be read. An element that holds the approved amount is fine; otherwise its amount is
    the smallest way to read it ("1.234,56" is 1234.56, not 123456)."""
    for text in totals:
        nums = numbers_in(text)
        if not nums or any(abs(n - approved) < 0.005 for n in nums):
            continue
        if min(nums) > approved + 0.005:
            return min(nums)
    return None


# ---------------------------------------------------------------- reviews
@dataclass
class Review:
    id: str
    owner: str
    host: str
    url: str
    path: str
    role: str
    label: str
    total: float
    currency: str
    at: float
    approved: bool = False
    used: bool = False


class Reviews:
    """The reviews of the running server, by id. A review is used once, on the page it was made on."""

    def __init__(self, clock: Any = time.monotonic):
        self._items: dict[str, Review] = {}
        self._clock = clock

    def add(self, owner: str, host: str, probe: Probe, role: str, label: str, total: float, currency: str, rid: str = "") -> Review:
        now = self._clock()
        for k in [k for k, r in self._items.items() if now - r.at > REVIEW_SECONDS]:
            del self._items[k]
        rid = rid or "rv_" + secrets.token_hex(5)
        r = self._items[rid] = Review(rid, owner, host, probe.url, probe.path, role, label, total, currency, now)
        return r

    def get(self, rid: str) -> Review | None:
        r = self._items.get(str(rid or ""))
        if r is not None and self._clock() - r.at > REVIEW_SECONDS:
            del self._items[r.id]
            return None
        return r
