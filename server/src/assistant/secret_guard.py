"""Secrets pasted into chat: found before the message reaches the model or is stored.

The vault's promise is that passwords, keys, tokens and codes never go through chat. When the owner pastes one into a message anyway,
the server finds it here first (server.py, the chat endpoints): the message is neither sent to a model nor saved, and the app is told
where the secret sits (UTF-16 offsets, the unit the apps' strings use) so it can offer the Keychain instead, or send the message with
the secret replaced by a placeholder. The value itself is never logged, echoed or kept.

What counts (kept narrow on purpose, so ordinary text passes):
- known key and token shapes (sk-..., sk-ant-..., ghp_/github_pat_, xox?-, AKIA/ASIA, AIza, glpat-, Stripe live/test keys, JWTs, a
  "Bearer <token>" header, private key blocks) and the shapes installed key connectors declare (packages.py `key_pattern`);
- a card number: 13-19 digits on a known card prefix that pass the Luhn check;
- a long random-looking token: a whole message that is one high-entropy word, or a very long high-entropy word inside a message;
- CANDIDATES by shape (`candidates`: a token that mixes letters with digits or symbols, a standalone run of 4-8 digits, 3-8 digits after
  ":" or "=", a message that is one number) that the owner's main model judges (secret_judge.py, one cached request). No word of any
  language decides: no judge, an error or an unusable answer makes EVERY candidate a hit (the message is held back; the owner may mask
  it or send it as it is).
URLs, e-mail addresses, file paths, phone and order numbers, UUIDs, hex hashes and words in any script do not count.
"""

from __future__ import annotations

import asyncio
import math
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

KINDS = ("api_key", "token", "private_key", "card", "password")


@dataclass(frozen=True, slots=True)
class Hit:
    start: int          # code-point offsets into the text
    end: int
    kind: str           # one of KINDS
    target: tuple[str, str] = ("vault", "")   # where the app can save it: ("provider", id) | ("connector", id) | ("wallet", "") | ("vault", "")
    label: str = ""     # whose key it looks like ("GitHub", "AWS", a connector's name...), a guess for the Keychain form; "" = unknown


# ---- known shapes ------------------------------------------------------------------------------------------------------------------
_B = r"(?<![A-Za-z0-9_\-])"      # not glued to a longer word on the left
_E = r"(?![A-Za-z0-9_\-])"       # nor on the right
_KNOWN: list[tuple[re.Pattern[str], str, tuple[str, str], str]] = [
    (re.compile(_B + r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.S),
     "private_key", ("vault", ""), ""),
    (re.compile(_B + r"sk-ant-[A-Za-z0-9_\-]{20,}" + _E), "api_key", ("provider", "anthropic"), "Anthropic"),
    (re.compile(_B + r"sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}" + _E), "api_key", ("provider", "openai"), "OpenAI"),
    (re.compile(_B + r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}" + _E), "api_key", ("vault", ""), "Stripe"),
    (re.compile(_B + r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})" + _E), "token", ("vault", ""), "GitHub"),
    (re.compile(_B + r"glpat-[A-Za-z0-9_\-]{20,}" + _E), "token", ("vault", ""), "GitLab"),
    (re.compile(_B + r"(?:xox[abposr]-|xapp-)[A-Za-z0-9\-]{10,}" + _E), "token", ("connector", "slack"), "Slack"),
    (re.compile(_B + r"(?:AKIA|ASIA)[0-9A-Z]{16}" + _E), "api_key", ("vault", ""), "AWS"),
    (re.compile(_B + r"AIza[0-9A-Za-z_\-]{35}" + _E), "api_key", ("vault", ""), "Google"),
    (re.compile(_B + r"(?:npm_[A-Za-z0-9]{36}|hf_[A-Za-z]{30,}|xai-[A-Za-z0-9]{40,}|shp(?:at|ss|ca|pa)_[a-fA-F0-9]{32}|SG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,})" + _E),
     "api_key", ("vault", ""), ""),
    (re.compile(_B + r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}" + _E), "token", ("vault", ""), ""),
    (re.compile(r"(?i)(?<![A-Za-z])bearer\s+([A-Za-z0-9_\-.~+/]{20,}=*)" + r"(?![A-Za-z0-9_\-.~+/=])"), "token", ("vault", ""), ""),
]

# ---- card numbers ------------------------------------------------------------------------------------------------------------------
_CARD = re.compile(r"(?<![\d\w-])(\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,7}|\d{4}[ -]?\d{6}[ -]?\d{5})(?![\d\w])")
_CARD_PREFIX = re.compile(r"^(?:4\d{12}(?:\d{3}){0,2}|5[1-5]\d{14}|2(?:2[2-9]|[3-6]\d|7[01]|720)\d{12}|3[47]\d{13}|35(?:2[89]|[3-8]\d)\d{12,15}"
                          r"|6(?:011|5\d\d|4[4-9]\d)\d{12,15}|62\d{14,17}|9\d{15})$")

# ---- long random tokens -----------------------------------------------------------------------------------------------------------
_WORD = re.compile(r"\S+")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX = re.compile(r"^(?:0x)?[0-9a-fA-F]+$")
_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9_\-+/=.~]+$")
_EDGE = "\"'“”‘’`()[]{}<>,;:!?。、，"


def entropy(s: str) -> float:
    """Shannon entropy in bits per character."""
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _looks_random(tok: str, min_len: int, min_bits: float) -> bool:
    if len(tok) < min_len or len(tok) > 1024 or not _TOKEN_CHARS.match(tok):
        return False
    if "://" in tok or tok.lower().startswith("www.") or _UUID.match(tok) or _HEX.match(tok) or tok.isdigit():
        return False
    if re.search(r"\.[A-Za-z]{2,}(?:/|$)", tok) and not re.search(r"\d", tok.split(".")[-1]):   # a domain name or file name
        return False
    if tok.count("/") >= 1 and not tok.endswith("="):   # a path
        return False
    classes = sum(bool(re.search(p, tok)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
    if classes < 3 and not (classes == 2 and re.search(r"[_\-+=~]", tok)):
        return False
    if re.fullmatch(r"[A-Za-z]+(?:[_\-][A-Za-z]+)*\d{0,4}", tok):   # snake_case / kebab-case words, CamelCase identifiers
        return False
    if re.fullmatch(r"[A-Za-z]+\d{0,4}", tok):   # an identifier or a long word with a year or a count after it
        return False
    return entropy(tok) >= min_bits


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


# ---- installed connectors' own key shapes ------------------------------------------------------------------------------------------
# A package may declare `key_pattern`: a fixed prefix (3-40 of [A-Za-z0-9_.-], written as is) followed by one to four bounded character
# classes such as [A-Za-z0-9]{32} or [a-f0-9_-]{20,40}. Nothing else is accepted: no alternation, no nesting, no unbounded repeats,
# so a pattern from someone else's package can neither match ordinary words nor take long to run.
KEY_PATTERN_SHAPE = re.compile(r"^(?P<prefix>(?:[A-Za-z0-9_]|\\[.\-]|-){3,40})(?P<classes>(?:\[(?:[A-Za-z0-9_]|\\[-.+/=]|[A-Za-z0-9]-[A-Za-z0-9])+\]"
                               r"\{\d{1,3}(?:,\d{1,3})?\}){1,4})$")


def check_key_pattern(pattern: str) -> str:
    """The pattern when it has the allowed shape (above), else ValueError with the reason."""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("key_pattern must be a non-empty string")
    if len(pattern) > 200:
        raise ValueError("key_pattern is too long (at most 200 characters)")
    m = KEY_PATTERN_SHAPE.match(pattern)
    if not m:
        raise ValueError("key_pattern must be a fixed prefix of at least 3 characters followed by 1-4 bounded classes like [A-Za-z0-9]{32}")
    total_min = 0
    for lo, hi in re.findall(r"\{(\d+)(?:,(\d+))?\}", m.group("classes")):
        lo_i, hi_i = int(lo), int(hi or lo)
        if hi_i < lo_i or hi_i > 200:
            raise ValueError("key_pattern: each {min,max} must have min <= max <= 200")
        total_min += lo_i
    prefix_len = len(re.sub(r"\\", "", m.group("prefix")))
    if prefix_len + total_min < 16:
        raise ValueError("key_pattern: a key must be at least 16 characters long in all")
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"key_pattern does not compile: {e}") from None
    return pattern


# ---- finding ----------------------------------------------------------------------------------------------------------------------
ConnectorPatterns = Iterable[tuple[str, str]]   # (connector id, key_pattern)


def find(text: str, connector_patterns: ConnectorPatterns = (), names: Callable[[str], str] | None = None) -> list[Hit]:
    """Every secret in `text`, as non-overlapping hits in order (empty for ordinary text)."""
    if not text:
        return []
    hits: list[Hit] = []
    for cid, pat in connector_patterns:
        try:
            rx = re.compile(_B + "(?:" + check_key_pattern(pat) + ")" + _E)
        except ValueError:
            continue
        label = ""
        if names is not None:
            try:
                label = str(names(cid) or "")[:60]
            except Exception:  # noqa: BLE001 - only a guess for the form
                label = ""
        hits += [Hit(m.start(), m.end(), "api_key", ("connector", cid), label) for m in rx.finditer(text)]
    for rx, kind, target, label in _KNOWN:
        for m in rx.finditer(text):
            s, e = (m.start(1), m.end(1)) if rx.groups else (m.start(), m.end())
            hits.append(Hit(s, e, kind, target, label))
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if not (13 <= len(digits) <= 19 and _CARD_PREFIX.match(digits) and _luhn(digits)):
            continue
        hits.append(Hit(m.start(1), m.end(1), "card", ("wallet", "")))
    whole = text.strip()
    if _WORD.fullmatch(whole):   # the whole message is one word: a pasted token on its own
        tok = whole.strip(_EDGE).rstrip(".")   # a full stop after it ends the sentence, it is not part of the token
        if _looks_random(tok, 20, 3.4):
            s = text.index(tok)
            hits.append(Hit(s, s + len(tok), "token", ("vault", "")))
    for m in _WORD.finditer(text):
        tok = m.group(0).strip(_EDGE).rstrip(".")
        if _looks_random(tok, 32, 4.0):
            s = m.start() + m.group(0).index(tok)
            hits.append(Hit(s, s + len(tok), "token", ("vault", "")))
    return _merge(hits)


def find_all(text: str, connector_patterns: ConnectorPatterns = (), names: Callable[[str], str] | None = None) -> list[Hit]:
    """`find` plus EVERY candidate (no judge): for the places that cannot ask a model (text kept in a skill, a marked message read later)."""
    return _merge(find(text, connector_patterns, names) + candidates(text))


def key_hits(text: str) -> list[Hit]:
    """Only the known shapes of keys and tokens (provider prefixes, PEM private-key blocks, bearer tokens, JWTs with a signature).
    No words, codes or card numbers: the check for a package's files (packages.scan_secrets), which must not refuse ordinary text."""
    if not text:
        return []
    hits: list[Hit] = []
    for rx, kind, target, label in _KNOWN:
        for m in rx.finditer(text):
            s, e = (m.start(1), m.end(1)) if rx.groups else (m.start(), m.end())
            hits.append(Hit(s, e, kind, target, label))
    return _merge(hits)


# ---- candidates: shapes a secret may have, judged by the model (secret_judge.py) ------------------------------------------------------
_SEPARATOR = re.compile(r"[:=：]")
_FILE_OR_HOST = re.compile(r"[\w-]+(?:\.[\w-]+)+")
_MAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def password_shaped(tok: str) -> bool:
    """A password-shaped token: 6-64 printable ASCII characters with a letter and a digit or a symbol, no address, path, UUID or hash."""
    if not 6 <= len(tok) <= 64 or not all(33 <= ord(c) <= 126 for c in tok):
        return False
    if "://" in tok or tok.lower().startswith("www.") or _UUID.match(tok) or (len(tok) >= 16 and _HEX.match(tok)) or _MAIL.fullmatch(tok):   # a short hex word may be a password: judged
        return False
    if tok.startswith(("/", "~/", "./")) or tok.count("/") >= 2:
        return False
    if _FILE_OR_HOST.fullmatch(tok) and re.search(r"[A-Za-z]{2,}$", tok):   # a domain name or a file name
        return False
    return bool(re.search(r"[A-Za-z]", tok)) and bool(re.search(r"[\d!@#$%^&*+=~?|<>\[\]{}\\]", tok))   # a letter, and a digit or a symbol a word does not have


def candidates(text: str) -> list[Hit]:
    """Spans that MAY be a password, PIN or code (kind "password"), by shape only: a token that mixes letters with digits or symbols
    (`password_shaped`), a standalone run of 4-8 digits, 3-8 digits after ":" or "=", a message that is one number of 3-8 digits. The caller
    asks the judge (SecretJudge) which of them are secrets, and treats all of them as secrets when it cannot."""
    out: list[Hit] = []
    whole = text.strip()
    for m in _WORD.finditer(text):
        raw = m.group(0)
        start = m.start()
        after_sep = text[max(0, start - 3):start].rstrip().endswith((":", "=", "："))   # "cvc: 123": the separator ends the word before
        if "://" not in raw and (sep := list(_SEPARATOR.finditer(raw))):
            start, raw, after_sep = start + sep[-1].end(), raw[sep[-1].end():], True   # "cvc:123"
        tok = raw.strip(_EDGE).rstrip(".")
        if not tok:
            continue
        s = start + raw.index(tok)
        e = s + len(tok)
        if password_shaped(tok):
            out.append(Hit(s, e, "password"))
        elif tok.isascii() and tok.isdigit():
            lone = tok == whole
            if (4 <= len(tok) <= 8 or (3 <= len(tok) <= 8 and (after_sep or lone))) and not (s > 0 and unicodedata.category(text[s - 1]) == "Sc"):
                out.append(Hit(s, e, "password"))
    return out


_SYMBOL = r"[!@#$%^&*+=~?|<>\[\]{}\\]"
CHAT_TIMEOUT = 3.0   # seconds the judge may take for a chat message; unanswered = held (fail closed)


def _plain_token_ok(tok: str) -> bool:
    return all(33 <= ord(c) <= 126 for c in tok) and "://" not in tok and not tok.lower().startswith("www.") and not _UUID.match(tok) \
        and not (len(tok) >= 16 and _HEX.match(tok)) and not _MAIL.fullmatch(tok) and not tok.startswith(("/", "~/", "./")) and tok.count("/") < 2 \
        and not (_FILE_OR_HOST.fullmatch(tok) and re.search(r"[A-Za-z]{2,}$", tok))


def chat_candidates(text: str) -> list[Hit]:
    """The few spans of a CHAT message that may be a secret, so that ordinary messages never wait for the judge: (a) a token of 8+
    characters with at least three of the four classes (lower, upper, digit, symbol); (b) the value of a `key: value` or `key=value`
    pair when it holds a digit or a symbol; (c) the whole message being one 4-8 digit number. A digit run inside a sentence is none."""
    out: list[Hit] = []
    whole = text.strip()
    if whole.isascii() and whole.isdigit() and 4 <= len(whole) <= 8:
        s = text.index(whole)
        return [Hit(s, s + len(whole), "password")]
    for m in _WORD.finditer(text):
        raw, start = m.group(0), m.start()
        after_sep = text[max(0, start - 3):start].rstrip().endswith((":", "=", "\uff1a"))
        if "://" not in raw and (sep := list(_SEPARATOR.finditer(raw))) and re.search(r"[^\W\d_]", raw[:sep[-1].start()]):   # "key=value", not "12:00"
            start, raw, after_sep = start + sep[-1].end(), raw[sep[-1].end():], True
        tok = raw.strip(_EDGE).rstrip(".")
        if not tok or not _plain_token_ok(tok):
            continue
        s = start + raw.index(tok)
        classes = sum(bool(re.search(p, tok)) for p in (r"[a-z]", r"[A-Z]", r"\d", _SYMBOL))   # a symbol: punctuation a word or a name does not carry
        if (len(tok) >= 8 and classes >= 3) or (after_sep and re.search(rf"\d|{_SYMBOL}", tok)):
            out.append(Hit(s, s + len(tok), "password"))
    return out


async def screen(text: str, connector_patterns: ConnectorPatterns = (), names: Callable[[str], str] | None = None, judge: object = None,
                 chat: bool = False) -> list[Hit]:
    """`find` plus the candidates the judge names. No judge, or a judge that cannot answer: every candidate is a hit (fail closed).
    `chat`: a chat message, with the few `chat_candidates` and a CHAT_TIMEOUT for the judge (no candidate: no wait at all)."""
    hits = find(text, connector_patterns, names)
    cands = [c for c in (chat_candidates(text) if chat else candidates(text)) if not any(c.start < h.end and h.start < c.end for h in hits)]
    if not cands:
        return hits
    picked: set[int] | None = None
    if judge is not None:
        try:
            ask = judge.secrets(text, [(c.start, c.end) for c in cands])   # type: ignore[attr-defined]
            picked = await (asyncio.wait_for(ask, CHAT_TIMEOUT) if chat else ask)
        except Exception:  # noqa: BLE001 - fail closed
            picked = None
    if picked is not None:
        cands = [c for i, c in enumerate(cands) if i in picked]
    return _merge(hits + cands)


_PRIORITY = {"private_key": 0, "api_key": 1, "card": 2, "token": 3, "password": 4}


def _merge(hits: list[Hit]) -> list[Hit]:
    """Overlapping hits become one; the most specific kind and target win (a connector's or a known shape's over a guess)."""
    hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
    out: list[Hit] = []
    for h in hits:
        if out and h.start < out[-1].end:
            last = out[-1]
            best = last if (_PRIORITY[last.kind], last.target == ("vault", "")) <= (_PRIORITY[h.kind], h.target == ("vault", "")) else h
            out[-1] = Hit(last.start, max(last.end, h.end), best.kind, best.target, best.label or last.label or h.label)
        else:
            out.append(h)
    return out


def utf16(text: str, index: int) -> int:
    """A code-point offset as a UTF-16 code-unit offset (what Kotlin and Swift strings count)."""
    return index + sum(1 for ch in text[:index] if ord(ch) > 0xFFFF)


def mask(text: str, hits: list[Hit], placeholder: str) -> str:
    """`text` with every hit replaced by `placeholder`."""
    for h in sorted(hits, key=lambda h: h.start, reverse=True):
        text = text[:h.start] + placeholder + text[h.end:]
    return text


def target_of(hits: list[Hit]) -> dict[str, str]:
    """Where the app should offer to save it: the first hit with a specific home, else the Keychain."""
    for h in hits:
        if h.target != ("vault", ""):
            return {"type": h.target[0], "id": h.target[1]}
    return {"type": "vault", "id": ""}


def label_of(hits: list[Hit]) -> str:
    """A guess at whose key it is, for the Keychain form's label ("" when nothing says)."""
    first = next((h for h in hits if h.target != ("vault", "")), None)
    return (first.label if first is not None and first.label else next((h.label for h in hits if h.label), ""))


def describe(text: str, hits: list[Hit], placeholder: str) -> dict[str, object]:
    """The 409 detail the apps read (never the value itself)."""
    return {"code": "secret_in_message",
            "spans": [{"start": utf16(text, h.start), "end": utf16(text, h.end), "kind": h.kind} for h in hits],
            "placeholder": placeholder,
            "target": target_of(hits),
            "label": label_of(hits)}


def kinds(hits: list[Hit]) -> list[str]:
    """The kinds found, for a log line (never the value)."""
    return sorted({h.kind for h in hits})


PatternSource = Callable[[], ConnectorPatterns]


# ---- messages the owner chose to send with a secret in them ------------------------------------------------------------------------
# "Send as is" after the warning: the message is stored as written (it is the owner's own history), and its id is remembered so the
# passes that write lasting text from the conversation (journal, memory upkeep, reflection, compaction summaries) see it without the
# secret. Kept in the store's meta table; the newest MARKED_KEEP ids.
MARKED_KEY = "secret_message_ids"
MARKED_KEEP = 2000
REMOVED = "[removed]"


def mark(store: object, message_id: int) -> None:
    """Remember that message `message_id` was sent with a secret in it."""
    ids = sorted(marked(store) | {int(message_id)})[-MARKED_KEEP:]
    store.set_meta(MARKED_KEY, ",".join(str(i) for i in ids))   # type: ignore[attr-defined]


def marked(store: object) -> set[int]:
    try:
        raw = store.meta(MARKED_KEY)   # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - a store without meta: nothing is marked
        return set()
    return {int(x) for x in (raw or "").split(",") if x.strip().isdigit()}


def scrub_rows(store: object, rows: list[dict]) -> list[dict]:
    """`rows` (messages with "id" and "content") with the secrets of marked messages replaced by [removed]."""
    ids = marked(store)
    if not ids:
        return rows
    out = []
    for r in rows:
        if r.get("id") in ids and isinstance(r.get("content"), str):
            r = {**r, "content": mask(r["content"], find_all(r["content"]), REMOVED)}   # no judge here: every candidate goes
        out.append(r)
    return out
