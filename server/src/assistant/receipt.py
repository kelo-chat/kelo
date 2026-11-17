"""The receipt check after a payment (tools/purchase.py: purchase_result): what the confirmation page shows against what the owner
approved, and the cancel terms the page gives.

Pure functions, no I/O. The owner's rules (2026-11-12): a higher total, another item, option or quantity than approved is a mismatch;
a lower total is fine. A mismatch is undone at once only when cancelling is free right now (`Terms.free_now`); with a fee, or when
that is not known, the owner decides.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

@lru_cache(maxsize=1)
def _symbols() -> dict[str, frozenset[str]]:
    """symbol -> the ISO codes it may mean, from server/content/currency_symbols.json (ASSISTANT_CONTENT_DIR in the container)."""
    root = Path(os.environ.get("ASSISTANT_CONTENT_DIR") or Path(__file__).resolve().parents[2] / "content")
    try:
        raw = json.loads((root / "currency_symbols.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): frozenset(str(c) for c in v) for k, v in (raw.get("symbols") or {}).items()}


_ISO = frozenset("KRW USD EUR GBP JPY CNY CAD AUD NZD SGD HKD CHF SEK NOK DKK INR THB VND IDR MYR PHP TWD BRL MXN TRY PLN CZK AED RUB "
                 "UAH ZAR".split())
_NUMBER = re.compile(r"\d[\d.,'   ]*\d|\d")
_WORD = re.compile(r"[\w]+", re.UNICODE)
_QTY = re.compile(r"(?:[x×*]\s*(\d{1,4})\b)|(?:\b(\d{1,4})\s*[x×])", re.I)   # "x2", "×2", "2x": no unit word of any language


def _option_values(options: str) -> str:
    """The values of an options text: in "Color: Black / Size: 270" only what follows each label's colon (the labels are the shop's words
    for the kind of option and appear on no receipt line)."""
    pieces = re.split(r"[/,;|]", options)
    return " ".join(p.split(":", 1)[1] if ":" in p else p for p in pieces)


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
        symbols = _symbols()
        for sym in sorted(symbols, key=len, reverse=True):
            if sym in low:
                found = symbols[sym]
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
        opt_t = _tokens(_option_values(str(item.get("options", ""))))
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
    """The terms the page gave: `free_until` (an ISO time), `fee` (a number, or text holding one amount), `how`. Read from those fields
    only, never from words. Free now when `free_until` is an ISO time still ahead, or when `fee` is the number 0 and no `free_until`
    is given. Anything else (text that is not an ISO time, a fee that is not a number, a deadline past, no terms) is not free: the
    owner decides."""
    if not isinstance(cancel, dict):
        return Terms()
    free_until = " ".join(str(cancel.get("free_until") or "").split())[:80]
    raw_fee = cancel.get("fee")
    fee = "" if raw_fee is None or isinstance(raw_fee, bool) else " ".join(str(raw_fee).split())[:80]
    how = " ".join(str(cancel.get("how") or "").split())[:80]
    t = Terms(until_text=free_until, fee=fee, how=how, known=bool(free_until or fee))
    when = parse_time(free_until, tz, now)
    if when is not None:
        t.until = when
        t.free_now = when > now
    elif not free_until and fee:
        amount = parse_money(raw_fee)[0]
        t.free_now = amount is not None and amount == 0
    return t
