"""One-time codes the model never sees (docs/one-time-codes.md).

A message the assistant reads (mail, a text on the phone) may carry a one-time sign-in code. Before the text reaches the model every such
code is cut out and held HERE, in memory; the model reads `[verification code #3]` instead. To use a code it hands the number to the
browser tool (find_login_code, `code_ref`); the owner approves on a card and the browser types it, once. A held code expires after
TTL_SECONDS and is gone after one use; nothing is written to disk, the vault, the memory or any log.

Codes are found by the SHAPE of the text, never by words (docs/no-word-lists.md): a token of 4-8 digits (or a short run of capitals and
digits) that stands alone on its line, the last-line `#code` form of web-origin messages, or the only number-like token of a short message. Masking
too much is cheap (the owner can read the message in the app); masking too little leaks a code, so a doubtful token is masked and
a failure while looking withholds the text (fail closed).

The shape rules only FIND CANDIDATES. Dates, prices, order numbers and the like have the same shape, so when there are candidates the
main model decides which are codes (code_judge.py, one request per message, indices only); no judge or a failed one masks them all.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Any

TTL_SECONDS = 600
MAX_HELD = 64
WITHHELD = "[The text was withheld: it could not be checked for one-time codes.]"

# a number of 4-8 digits (single spaces or hyphens between groups allowed) or 6-8 capitals+digits, or AB-1234; not part of a longer word
_TOKEN = re.compile(r"(?<![\w\-./:#@$₩€£¥])((?:\d[ \-]?){3,7}\d|[A-Z0-9]{1,4}-[A-Z0-9]{2,6}|(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})"
                    r"(?![\w\-/:%@]|\.\d|\s?[%₩€£¥$])")
_WEB_ORIGIN = re.compile(r"^\s*@?[\w.\-]+\.[a-z]{2,}\s+#([A-Za-z0-9\-]{4,12})\s*$", re.M)   # the last-line form: "@example.com #123456"


@dataclass(slots=True)
class Held:
    ref: int
    code: str
    channel: str            # "mail" | "text"
    sender: str             # the plain sender address or number (shown to the owner on the card)
    site: str               # registrable domain of the sender's address ("" for a phone number): the page it may be typed on
    at: float


@dataclass(slots=True)
class CodeShelf:
    clock: Callable[[], float] = time.time
    ttl: float = TTL_SECONDS
    _held: dict[int, Held] = field(default_factory=dict)
    _next: int = 1
    _lock: threading.Lock = field(default_factory=threading.Lock)
    judge: Any = None        # code_judge.CodeJudge; None = every candidate is masked (fail closed)

    def clear(self) -> None:
        with self._lock:
            self._held.clear()
            self._next = 1

    def _purge(self) -> None:
        now = self.clock()
        for k in [k for k, h in self._held.items() if now - h.at > self.ttl]:
            del self._held[k]

    def put(self, code: str, *, channel: str, sender: str = "") -> Held:
        """Hold `code` and give back its handle. The same code from the same sender while still held keeps its number."""
        sender = plain_sender(sender)
        site = _site_of(sender)
        with self._lock:
            self._purge()
            for h in self._held.values():
                if h.code == code and h.sender == sender and h.channel == channel:
                    h.at = self.clock()
                    return h
            while len(self._held) >= MAX_HELD:      # the oldest goes first
                del self._held[min(self._held)]
            h = Held(self._next, code, channel, sender, site, self.clock())
            self._next += 1
            self._held[h.ref] = h
            return h

    def get(self, ref: int) -> Held | None:
        with self._lock:
            self._purge()
            return self._held.get(int(ref))

    def forget(self, ref: int) -> None:
        """Single use: called once the browser typed the code."""
        with self._lock:
            self._held.pop(int(ref), None)

    async def mask(self, text: str, *, channel: str, sender: str = "", subject: str = "", key: str = "") -> str:
        """`text` with every one-time code replaced by its handle `[verification code #N]`. Fails closed."""
        return (await self.mask_many([(text, sender, subject)], channel=channel, key=key))[0]

    async def mask_many(self, items: list[tuple[str, str, str]], *, channel: str, key: str = "") -> list[str]:
        """Several texts (text, sender, subject) in ONE judge request. Candidates come from the text's shape (code_spans); with none there is
        no request. The judge (code_judge.py) names the ones that are one-time codes; a judge that is missing, fails or answers badly
        means every candidate is masked, and any other failure withholds the text."""
        try:
            return await self._mask_many(items, channel, key)
        except Exception:  # noqa: BLE001 - never let a checking failure release the text
            return [WITHHELD for _ in items]

    async def _mask_many(self, items: list[tuple[str, str, str]], channel: str, key: str) -> list[str]:
        spans = [code_spans(text or "") for text, _, _ in items]
        total = sum(len(sp) for sp in spans)
        if not total:
            return [text or "" for text, _, _ in items]
        chosen: set[int] | None = None
        if self.judge is not None:
            n, msgs, unseen = 0, [], set()
            room = max(800, 24_000 // max(1, sum(1 for sp in spans if sp)))     # characters of one message the judge reads
            for (text, sender, subject), sp in zip(items, spans):
                if not sp:
                    continue
                marked, last, size = [], 0, 0
                for start, end in sp:
                    piece = [text[last:start], f"[[#{n}: {text[start:end]}]]"]
                    size += sum(len(x) for x in piece)
                    if size > room:
                        unseen.add(n)                # beyond what the judge reads: masked (fail closed)
                    else:
                        marked += piece
                    last, n = end, n + 1
                marked.append(text[last:][: max(0, room - size)])
                msgs.append({"sender": plain_sender(sender), "subject": subject[:200], "text": "".join(marked)})
            chosen = await self.judge.codes(msgs, total, key)
            if chosen is not None:
                chosen = chosen | unseen
        out, n = [], 0
        for (text, sender, _), sp in zip(items, spans):
            parts, last = [], 0
            for start, end in sp:
                parts.append(text[last:start])
                if chosen is None or n in chosen:
                    ref = self.put(re.sub(r"[ ]", "", text[start:end]), channel=channel, sender=sender).ref
                    parts.append(f"[verification code #{ref}]")
                else:
                    parts.append(text[start:end])
                last, n = end, n + 1
            parts.append(text[last:])
            out.append("".join(parts))
        return out


SHELF = CodeShelf()    # one owner per server: the tools share this shelf


def plain_sender(sender: str) -> str:
    """The sender's address only (no display name: that is the sender's own words), or the phone number."""
    s = str(sender or "").strip()
    addr = parseaddr(s)[1] if "@" in s else s
    return re.sub(r"[^\w.+\-@]", "", addr)[:80]


def _site_of(sender: str) -> str:
    if "@" not in sender:
        return ""
    from .tools.sites import registrable
    try:
        return registrable(sender.rsplit("@", 1)[1]) or ""
    except Exception:  # noqa: BLE001
        return ""


def code_spans(text: str) -> list[tuple[int, int]]:
    """Where the CANDIDATES for one-time codes are in `text`, as (start, end), by shape only (see the module notes): every token of 4-8 digits
    (or a short run of capitals and digits) not beside a currency or percent sign, and the last-line `@site #code` form. The judge decides."""
    spans: list[tuple[int, int]] = []
    for m in _WEB_ORIGIN.finditer(text):
        spans.append((m.start(1), m.end(1)))
    for m in _TOKEN.finditer(text):
        token = m.group(1)
        if token.replace("-", "").replace(" ", "").isdigit() and len(re.sub(r"\D", "", token)) > 8:
            continue                                     # a long number (phone, account): not a code
        if not any(s <= m.start(1) < e for s, e in spans):
            spans.append((m.start(1), m.end(1)))
    return sorted(spans)
