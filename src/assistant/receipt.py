"""The receipt check after a payment (tools/purchase.py: purchase_result): what the confirmation page shows against what the owner
approved, and the cancel terms the page gives.

Pure functions, no I/O. The owner's rules (2026-11-12): a higher total, another item, option or quantity than approved is a mismatch;
a lower total is fine. A mismatch is undone at once only when cancelling is free right now (`Terms.free_now`); with a fee, or when
that is not known, the owner decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# currency words and symbols -> the ISO codes they may mean ("$" is any dollar, "¥" yen or yuan)
_DOLLARS = frozenset({"USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN", "TWD"})
_YEN = frozenset({"JPY", "CNY"})
_SYMBOLS: dict[str, frozenset[str]] = {
    "₩": frozenset({"KRW"}), "원": frozenset({"KRW"}), "us$": frozenset({"USD"}), "$": _DOLLARS, "€": frozenset({"EUR"}),
    "£": frozenset({"GBP"}), "¥": _YEN, "￥": _YEN, "円": frozenset({"JPY"}), "元": frozenset({"CNY"}), "₹": frozenset({"INR"}),
    "฿": frozenset({"THB"}), "₫": frozenset({"VND"}),
}
_ISO = frozenset("KRW USD EUR GBP JPY CNY CAD AUD NZD SGD HKD CHF SEK NOK DKK INR THB VND IDR MYR PHP TWD BRL MXN TRY PLN CZK AED RUB "
                 "UAH ZAR".split())
_NUMBER = re.compile(r"\d[\d.,'   ]*\d|\d")
_WORD = re.compile(r"[\w]+", re.UNICODE)
_QTY = re.compile(r"(?:[x×*]\s*(\d{1,4})\b)|(?:\b(\d{1,4})\s*(?:[x×]|개|ea|pcs|pc|units?|items?)(?![a-z]))", re.I)
_OPTION_NOISE = frozenset({"color", "colour", "size", "option", "options", "type", "qty", "quantity", "색상", "컬러", "사이즈", "옵션", "수량", "선택"})
_FREE_WORDS = re.compile(r"\bfree\b|full refund|100 ?% refund|no (cancel(l?ation)? )?(fee|charge)|without (a )?fee|전액\s*환불|무료\s*취소|무료|수수료\s*(없|면제)|"
                         r"취소\s*수수료\s*없", re.I)
_NOT_FREE = re.compile(r"\bnot free\b|non[- ]?refundable|no refund|환불\s*불가|취소\s*불가|유료", re.I)
_NO_FEE = re.compile(r"^\W*(0|none|no|nil|n/a|free|없음|무료)\W*(%|원|krw|usd|\$|€|won)?\W*$|^\W*[₩$€£¥]?\s*0(?:[.,]0+)?\W*(?:%|원|krw|usd|eur)?\W*$|"
                     r"no (cancel(l?ation)? )?(fee|charge)|without (a )?fee|수수료\s*(없|면제)|무료", re.I)


def parse_money(value: Any) -> tuple[float | None, frozenset[str]]:
    """(the number, the currencies the text may mean) of a price as a page shows it: "24,000원", "$12.99", "1.234,56 €", "KRW 24000".
    The number is None when there is none; the set is empty when no currency was named."""
    if isinstance(value, bool):
        return None, frozenset()
    if isinstance(value, (int, float)):
        return float(value), frozenset()
    text = str(value or "")
    found: frozenset[str] = frozenset()
    low = text.lower()
    for word in re.findall(r"[A-Za-z]{3}", text):
        if word.upper() in _ISO:
            found = frozenset({word.upper()})
            break
    if not found:
        for sym in sorted(_SYMBOLS, key=len, reverse=True):
            if sym in low:
                found = _SYMBOLS[sym]
                break
    m = _NUMBER.search(text)
    if m is None:
        return None, found
    tok = re.sub(r"[ '  ]", "", m.group(0))
    has_comma, has_dot = "," in tok, "." in tok
    dec = ""
    if has_comma and has_dot:
        dec = "," if tok.rfind(",") > tok.rfind(".") else "."
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        parts = tok.split(sep)
        if len(parts) == 2 and (len(parts[1]) != 3 or parts[0] in ("", "0")):
            dec = sep
    try:
        if dec:
            whole, _, frac = tok.rpartition(dec)
            return float(re.sub(r"[.,]", "", whole) + "." + frac), found
        return float(re.sub(r"[.,]", "", tok)), found
    except ValueError:
        return None, found


def currency_ok(approved: str, found: frozenset[str]) -> bool:
    return not found or not approved or approved.upper() in found


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(str(text or "").lower()) if w]


def _line(item: Any) -> tuple[str, str, int | None]:
    """A receipt line as (name, options, quantity or None): a dict {name, options, quantity} or a plain string."""
    if isinstance(item, dict):
        qty = item.get("quantity")
        try:
            quantity = int(qty) if qty not in (None, "") else None
        except (TypeError, ValueError):
            quantity = None
        name = str(item.get("name") or "")
        options = str(item.get("options") or "")
        if quantity is None:
            m = _QTY.search(name + " " + options)
            quantity = int(next(g for g in m.groups() if g)) if m else None
        return name, options, quantity
    text = str(item or "")
    m = _QTY.search(text)
    return text, "", int(next(g for g in m.groups() if g)) if m else None


@dataclass
class Check:
    status: str                                    # "match" | "lower" | "mismatch"
    charged_text: str = ""
    problems: list[str] = field(default_factory=list)
    difference: float | None = None                # charged - approved (same currency), None when not comparable
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != "mismatch"


def compare(approved_amount: float | None, approved_currency: str, approved_items: list[dict[str, Any]], charged_total: Any,
            charged_items: list[Any]) -> Check:
    """The receipt against the approval: total (a higher one, or another currency, is a mismatch; a lower one is fine) and items
    (every approved item's words on some receipt line, its options' words too, and the same quantity)."""
    charged, found = parse_money(charged_total)
    chk = Check("match", " ".join(str(charged_total or "").split())[:60])
    if charged is None:
        chk.status = "mismatch"
        chk.problems.append("the charged total could not be read")
        return chk
    if not currency_ok(approved_currency, found):
        chk.status = "mismatch"
        chk.problems.append(f"another currency ({'/'.join(sorted(found))} instead of {approved_currency})")
    elif approved_amount is not None:
        chk.difference = round(charged - approved_amount, 2)
        if chk.difference > 0:
            chk.status = "mismatch"
            chk.problems.append("a higher total")
        elif chk.difference < 0:
            chk.status = "lower"
    lines = [_line(i) for i in charged_items or []]
    for item in approved_items or []:
        name_t = _tokens(item.get("name", ""))
        opt_t = [w for w in _tokens(item.get("options", "")) if w not in _OPTION_NOISE]
        if not name_t:
            continue
        want = int(item.get("quantity") or 1)
        label = str(item.get("name") or "")
        seen_name = [ln for ln in lines if set(name_t) <= set(_tokens(ln[0] + " " + ln[1]))]
        if not seen_name:
            chk.problems.append(f"another item ({label} is not on the receipt)")
            continue
        with_options = [ln for ln in seen_name if set(opt_t) <= set(_tokens(ln[0] + " " + ln[1]))]
        if not with_options:
            chk.problems.append(f"another option for {label}")
            continue
        if not any(ln[2] is None or ln[2] == want for ln in with_options):
            got = next((ln[2] for ln in with_options if ln[2] is not None), want)
            chk.problems.append(f"quantity {got} instead of {want} for {label}")
    if chk.problems:
        chk.status = "mismatch"
    return chk


@dataclass
class Terms:
    """What the page says about cancelling the order."""
    free_now: bool = False         # cancelling is free (and refunds in full) right now, by the page's words or its deadline
    until: float | None = None     # unix time free (or cheaper) cancelling ends, when the page gave one that could be read
    until_text: str = ""
    fee: str = ""
    how: str = ""                  # the page's cancel button or link words
    known: bool = False


def parse_time(value: Any, tz: str = "", now: float = 0.0) -> float | None:
    """An ISO time (a plain date means the end of that day), in the owner's timezone when it names none."""
    text = str(value or "").strip()
    if not text or not re.match(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if len(text) <= 10:
        dt = dt.replace(hour=23, minute=59, second=59)
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tz) if tz else timezone.utc)
        except Exception:  # noqa: BLE001 - an unknown zone name: UTC
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def cancel_terms(cancel: Any, now: float, tz: str = "") -> Terms:
    """The terms the page gave (`free_until` ISO or words, `fee`, `how`). Free now only when the page says so in words, or gives a
    deadline still ahead, or says there is no fee; a fee with no deadline ahead, or anything unclear, is not free."""
    if not isinstance(cancel, dict):
        return Terms()
    free_until = " ".join(str(cancel.get("free_until") or "").split())[:80]
    fee = " ".join(str(cancel.get("fee") or "").split())[:80]
    how = " ".join(str(cancel.get("how") or "").split())[:80]
    t = Terms(until_text=free_until, fee=fee, how=how, known=bool(free_until or fee))
    when = parse_time(free_until, tz, now)
    if when is not None:
        t.until = when
        t.free_now = when > now
    elif free_until and _FREE_WORDS.search(free_until) and not _NOT_FREE.search(free_until):
        t.free_now = True
    elif fee and not free_until and _NO_FEE.search(fee):
        t.free_now = True
    return t
