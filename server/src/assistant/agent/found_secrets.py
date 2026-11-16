"""Secrets the agent comes across: kept out of the model's context and out of its replies.

Two ways a key could otherwise reach the user's chat (or a stored message): the agent reads one in a tool result (a key it just created
on a provider's dashboard in the browser, one in an email, a file or a connector's answer), or the model writes one into its reply.

- Tool results: external text (an untrusted tool's result, or the parts marked with fence.data) is scanned with secret_guard before it is
  fenced for the model. Each key, token or private key found is replaced by `[secret #N: <kind>, hidden; save it with
  save_found_secret(ref="#N")]`. The real value stays in this module's per-run registry (server memory only, one hour at most) together
  with the page it came from; `save_found_secret` (tools/connect_tools.py) pulls it from there and puts it in the keychain. Cards and
  passwords-with-a-word are not hidden here: a checkout page legitimately shows card text the payment tools work with.
- Replies: `Masker` holds back the unfinished last word of the model's streamed text, hides what secret_guard finds ("[hidden]") and
  lets the rest through; `scrub` does the same for a whole text (job reports, notifications).

secret_guard's patterns are used as they are: URLs, e-mail addresses, UUIDs, hex hashes, order and phone numbers are not secrets here.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

from .. import secret_guard
from .fence import data, split

TTL = 3600.0
HIDDEN = "[hidden]"
TOOL_KINDS = ("api_key", "token", "private_key")   # what is hidden in tool results (see above)
SAVEABLE = ("api_key", "token")
_URL = re.compile(r"(?m)^URL:\s*(https?://\S+)")
_ANY_URL = re.compile(r"https?://[^\s\"'<>)\]]+")

# second-level suffixes under which the registrable domain has one more label (a small list: not the whole public suffix list)
_SECOND_LEVEL = {"co", "com", "net", "org", "ac", "go", "or", "ne", "gov", "edu"}


def registrable_domain(host: str) -> str:
    """"dash.openai.com" -> "openai.com"; "api.example.co.uk" -> "example.co.uk"; an IP address or a single label as it is."""
    host = (host or "").strip().lower().rstrip(".")
    labels = host.split(".")
    if len(labels) <= 2 or all(p.isdigit() for p in labels):
        return host
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


@dataclass(slots=True)
class Found:
    ref: str
    value: str
    kind: str
    label: str
    source: str       # the page or service it came from (a URL, a host or "")
    at: float


class FoundSecrets:
    """The secrets one run has come across. Values live here only; every entry is dropped after `TTL`."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._items: dict[str, Found] = {}
        self._by_value: dict[str, str] = {}
        self._n = 0

    def _sweep(self) -> None:
        now = self._clock()
        for ref in [r for r, f in self._items.items() if now - f.at > TTL]:
            self._by_value.pop(self._items.pop(ref).value, None)

    def add(self, value: str, kind: str, label: str = "", source: str = "") -> str:
        self._sweep()
        ref = self._by_value.get(value)
        if ref and ref in self._items:
            return ref
        self._n += 1
        ref = f"#{self._n}"
        self._items[ref] = Found(ref, value, kind, label, source, self._clock())
        self._by_value[value] = ref
        return ref

    def get(self, ref: str) -> Found | None:
        self._sweep()
        return self._items.get("#" + str(ref).strip().lstrip("#"))

    def forget(self, ref: str) -> None:
        f = self._items.pop("#" + str(ref).strip().lstrip("#"), None)
        if f:
            self._by_value.pop(f.value, None)

    def placeholder(self, ref: str, kind: str) -> str:
        save = f'; save it with save_found_secret(ref="{ref}")' if kind in SAVEABLE else ""
        return f"[secret {ref}: {kind}, hidden{save}]"

    def hide(self, text: str, source: str = "") -> str:
        """`text` with every key, token and private key replaced by its placeholder (the values are kept for save_found_secret)."""
        hits = [h for h in secret_guard.find(text) if h.kind in TOOL_KINDS]
        for h in sorted(hits, key=lambda h: h.start, reverse=True):
            ref = self.add(text[h.start:h.end], h.kind, h.label, source)
            text = text[:h.start] + self.placeholder(ref, h.kind) + text[h.end:]
        return text

    def hide_marked(self, text: str, source: str = "") -> str:
        """The same for a tool's own string that marks its external parts with fence.data(): only the marked parts are scanned."""
        return "".join(data(self.hide(part, source)) if is_data else part for is_data, part in split(text))


current: ContextVar[FoundSecrets | None] = ContextVar("found_secrets", default=None)


def source_of(text: str, arguments: dict[str, object] | None = None) -> str:
    """Where a tool result came from: the page address its own words give ("URL: ..."), else an address in the call's arguments."""
    m = _URL.search(text or "")
    if m:
        return m.group(1)
    for k in ("url", "site", "endpoint"):
        v = (arguments or {}).get(k)
        if isinstance(v, str) and host_of(v):
            return v
    m = _ANY_URL.search(text or "")
    return m.group(0) if m else ""


# ---- replies -----------------------------------------------------------------------------------------------------------------------
def scrub(text: str) -> str:
    """`text` with everything secret_guard finds replaced by "[hidden]" (a whole text: a job's report, a notification)."""
    if not text:
        return text
    return secret_guard.mask(text, secret_guard.find(text), HIDDEN)


_BEGIN = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
_END = re.compile(r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
CONTEXT = 80          # characters of what was already passed on, kept so a word like "password:" still gives its value away
MAX_HOLD = 8192       # a held private key block (or an endless word) is let go of at this size


_PREFIXES = ("sk", "pk", "rk", "gh", "github", "glpat", "xox", "akia", "asia", "aiza", "npm", "hf", "xai", "shp", "sg", "eyj", "bearer", "-----")


def _could_grow(word: str) -> bool:
    """The unfinished last word of a streamed reply: could more letters turn it into a key? (a digit, a dash or underscore, a very long
    word, or the start of a known key shape.) An ordinary word is not held back, so replies stream as before."""
    if len(word) >= 16 or re.search(r"[\d_\-]", word):
        return True
    low = word.lower()
    return any(low.startswith(p) or p.startswith(low) for p in _PREFIXES)


class Masker:
    """Streaming `scrub`: feed() takes the next piece of the model's text and returns what may be passed on now; the unfinished last
    word (and an unfinished private key block) is held back until the next piece, so a key split over several pieces is still found.
    finish() returns what is left."""

    def __init__(self) -> None:
        self._held = ""
        self._seen = ""

    def _emit(self, region: str) -> str:
        if not region:
            return ""
        ctx = self._seen
        full = ctx + region
        hits = [h for h in secret_guard.find(full) if h.end > len(ctx)]
        out = secret_guard.mask(full, [secret_guard.Hit(max(h.start, len(ctx)), h.end, h.kind) for h in hits], HIDDEN)[len(ctx):] if hits else region
        self._seen = (ctx + out)[-CONTEXT:]
        return out

    def feed(self, text: str) -> str:
        buf = self._held + text
        cut = max((i + 1 for i, ch in enumerate(buf) if ch.isspace()), default=0)
        begin = None
        for m in _BEGIN.finditer(buf):
            if not _END.search(buf, m.end()):
                begin = m.start()
        if begin is not None and len(buf) - begin <= MAX_HOLD:
            cut = min(cut, begin)
        if len(buf) - cut > MAX_HOLD:
            cut = len(buf)
        if cut < len(buf) and not _could_grow(buf[cut:]):
            cut = len(buf)   # an ordinary last word goes out at once: only what could be the start of a key waits
        self._held = buf[cut:]
        return self._emit(buf[:cut])

    def finish(self) -> str:
        rest, self._held = self._held, ""
        return self._emit(rest)
