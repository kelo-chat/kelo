"""Finding a one-time sign-in code that a site just mailed to the user (find_login_code, and the automatic path).

`find_code(messages, site)` looks through recent messages (newest first) for one that comes from that site (the sender's address or name,
or the subject, names it) and pulls the code out of it by its shape: digits or a short code of capitals and digits standing alone on
its line, or in a short mail (no code words in any language are looked for). Phone numbers and amounts (a currency
symbol before it) are not codes, and a year-like number counts only when it stands alone. That loose match is only ever shown to the user on an approval card.

`pick_auto_code(messages, site, asked_at, now)` is the strict match used to type a code WITHOUT asking, during a sign-in the agent
itself started on that site: the mail arrived from AUTO_EARLY_SECONDS before the code was asked for up to AUTO_WINDOW_SECONDS after;
its sender is the site (the registrable domain of the From address, or of a DKIM signature d= or the Return-Path the mail carries,
each dropped when the receiving server reported it failing); the main model (code_judge) names it as a sign-in code among the code-shaped tokens; and there is exactly one such code. Without a judge nothing is typed.
Anything else is "none" or "ambiguous" and the normal flow (ask the user) takes over.

The code is never stored or logged: it is kept in memory only between finding it and typing it (or the user saying no), and the model
only ever sees who sent it and how long it is.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from email.utils import parseaddr

_CANDIDATE = re.compile(r"(?<![\w\-./:#@$₩€£])((?:\d[ \-]?){3,7}\d|[A-Z0-9]{1,4}-[A-Z0-9]{2,6}|(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})(?![\w\-/:%@]|\.\d)")
SHORT_MAIL_LINES = 12   # every code in a mail this short counts (two of them make it ambiguous); no code word is looked for


@dataclass(slots=True)
class FoundCode:
    code: str
    sender: str          # "GitHub <noreply@github.com>" as the mail showed it (shown to the user only)
    subject: str
    at: float
    account: str = ""
    ref: int = 0         # the handle number when the code came from the shelf (code_shelf.py): forgotten once typed


def site_words(site: str) -> list[str]:
    """The distinctive name of a site: its registrable domain's first label ("accounts.example.co.kr" -> ["example"], "github.com" ->
    ["github"]); nothing for a label shorter than 3 letters. The Public Suffix List decides what is suffix (sites.py)."""
    from .sites import registrable

    reg = registrable(re.sub(r"^[a-z]+://", "", (site or "").lower()).split("/")[0].split(":")[0])
    label = reg.split(".")[0] if "." in reg else ""
    return [label] if len(label) >= 3 else []


def base_domain(host: str) -> str:
    """The registrable domain of a host ("login.example.co.kr" -> "example.co.kr"), by the Public Suffix List."""
    from .sites import registrable

    return registrable(re.sub(r"^[a-z]+://", "", (host or "").lower()).split("/")[0].split(":")[0])


def from_site(msg: dict, site: str) -> bool:
    """Whether a message comes from `site`: the sender's address is on the site's domain, or the sender's name or the subject names it."""
    words = site_words(site)
    if not words:
        return False
    name, addr = parseaddr(str(msg.get("from") or ""))
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    base = base_domain(site)
    if domain and (domain == base or domain.endswith("." + base)):
        return True
    said = re.split(r"[\W_]+", " ".join([name.lower(), str(msg.get("subject") or "").lower()]))
    return any(w in said for w in words)


def _plausible(raw: str, context: str, alone: bool) -> str | None:
    code = re.sub(r"[ ]", "", raw)
    digits = re.sub(r"\D", "", code)
    if code.isdigit():
        if not 4 <= len(code) <= 8:
            return None
        if len(code) == 4 and re.fullmatch(r"(19|20)\d\d", code) and not alone:
            return None       # a year
        tail = context.rstrip()[-1:]
        if tail and unicodedata.category(tail) == "Sc":
            return None       # an amount: a currency symbol right before it
    elif "-" in code:
        if "-" in raw and raw.replace("-", "").isdigit() and len(digits) > 8:
            return None       # a phone number
        if not re.search(r"\d", code):
            return None
    return code


def _candidates(text: str) -> list[tuple[int, str, bool]]:
    """Every plausible code in one message's text: (score, code, clear), in reading order. A code is clear when it stands alone on its
    line or sits in a short mail."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    found: list[tuple[str, bool]] = []
    for i, line in enumerate(lines[:200]):
        for m in _CANDIDATE.finditer(line):
            alone = line.replace(m.group(1), "").strip(" :.-()[]") == ""
            code = _plausible(m.group(1), line[: m.start()], alone)
            if code is not None:
                found.append((code, alone))
    short = len(lines) <= SHORT_MAIL_LINES
    out: list[tuple[int, str, bool]] = []
    for code, alone in found:
        clear = alone or short
        score = (2 if clear else 0) + (2 if alone else 0) + (1 if code.isdigit() and len(code) in (4, 6, 8) else 0)
        if score >= 2:
            out.append((score, code, clear))
    return out


def extract(text: str) -> str | None:
    """The code in one message's text, or None. The best-scoring clear candidate wins."""
    best: tuple[int, str] | None = None
    for score, code, _ in _candidates(text):
        if best is None or score > best[0]:
            best = (score, code)
    return best[1] if best else None


def candidate_codes(text: str) -> list[str]:
    """Every code-shaped token of one message, distinct, in reading order (shape only: nothing here says it IS a sign-in code)."""
    out: list[str] = []
    for ln in (text or "").splitlines()[:200]:
        line = ln.strip()
        for m in _CANDIDATE.finditer(line):
            alone = line.replace(m.group(1), "").strip(" :.-()[]") == ""
            code = _plausible(m.group(1), line[: m.start()], alone)
            if code is not None and code not in out:
                out.append(code)
    return out


def find_code(messages: list[dict], site: str) -> FoundCode | None:
    """The newest code mailed by `site` among `messages` (newest first)."""
    for msg in messages:
        if not from_site(msg, site):
            continue
        code = extract(str(msg.get("subject") or "") + "\n" + str(msg.get("text") or ""))
        if code:
            return FoundCode(code, str(msg.get("from") or "")[:120], str(msg.get("subject") or "")[:120], float(msg.get("at") or 0),
                             str(msg.get("account") or ""))
    return None


# ---------------------------------------------------------------------------------------------------- the strict, automatic match
AUTO_EARLY_SECONDS = 60      # a mail that arrived this long before the code was asked for still counts (clocks, a quick site)
AUTO_WINDOW_SECONDS = 600    # ...and up to this long after; after that nothing is typed without asking


@dataclass(slots=True)
class AutoCode:
    code: str
    sender: str      # the sender's plain address ("no-reply@example.com"): what the model and the owner are told
    matched: str     # which header showed it is the site's mail: "From example.com" | "DKIM example.com" | "Return-Path example.com"
    at: float


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].strip().strip(">").lower() if "@" in (addr or "") else ""


def plain_address(sender: str) -> str:
    """The sender's address only, in safe characters (no display name: that is the sender's own words)."""
    addr = parseaddr(str(sender or ""))[1]
    return re.sub(r"[^\w.+\-@]", "", addr)[:80]


def sender_match(msg: dict, site: str) -> str | None:
    """How the message shows it comes from `site` (its registrable domain), or None. From address first, then a DKIM signature's
    d=, then the Return-Path; mail.recent leaves out a DKIM domain or Return-Path the receiving server reported failing, and marks
    a From that failed DMARC (`from_failed`)."""
    from .sites import registrable as site_key

    want = site_key(site)
    if not want or "." not in want:
        return None
    frm = _domain(parseaddr(str(msg.get("from") or ""))[1])
    if frm and site_key(frm) == want and not msg.get("from_failed"):
        return f"From {frm}"
    for d in msg.get("dkim") or []:
        if site_key(str(d)) == want:
            return f"DKIM {str(d).lower()}"
    rp = _domain(str(msg.get("return_path") or ""))
    if rp and site_key(rp) == want:
        return f"Return-Path {rp}"
    return None


async def pick_auto_code(messages: list[dict], site: str, asked_at: float, now: float, judge: Any = None) -> tuple[AutoCode | None, str]:
    """The one code the site mailed for this sign-in, or (None, "none" | "ambiguous" | "late"). Messages are chosen by sender and time
    (structure); the code-shaped tokens of those messages are put to the code judge (code_judge.CodeJudge: ONE main-model request that
    names which are one-time sign-in codes). No judge, or one that cannot tell, types nothing: "none" (the owner's card still shows the
    candidates through find_code)."""
    if now - asked_at > AUTO_WINDOW_SECONDS:
        return None, "late"
    chosen: list[tuple[dict, str, float, list[str]]] = []
    for msg in messages:
        at = float(msg.get("arrived") or msg.get("at") or 0)
        if not at or at < asked_at - AUTO_EARLY_SECONDS or at > asked_at + AUTO_WINDOW_SECONDS or at > now + AUTO_EARLY_SECONDS:
            continue
        how = sender_match(msg, site)
        if how is None:
            continue
        text = str(msg.get("subject") or "") + "\n" + str(msg.get("text") or "")
        cands = candidate_codes(text)
        if cands:
            chosen.append((msg, how, at, cands))
    if not chosen:
        return None, "none"
    if judge is None:
        return None, "none"
    marked, n = [], 0
    for msg, _how, _at, cands in chosen:
        text = str(msg.get("subject") or "") + "\n" + str(msg.get("text") or "")
        for c in cands:
            text = text.replace(c, f"[[#{n}: {c}]]", 1)
            n += 1
        marked.append({"sender": plain_address(str(msg.get("from") or "")), "subject": "", "text": text[:4000]})
    yes = await judge.codes(marked, n)
    if yes is None:
        return None, "none"
    picks: list[AutoCode] = []
    i = 0
    for msg, how, at, cands in chosen:
        mine = [c for k, c in enumerate(cands) if i + k in yes]
        i += len(cands)
        if len(mine) > 1:
            return None, "ambiguous"
        if mine:
            picks.append(AutoCode(mine[0], plain_address(str(msg.get("from") or "")), how, at))
    if not picks:
        return None, "none"
    if len({p.code for p in picks}) > 1:
        return None, "ambiguous"
    return max(picks, key=lambda p: p.at), ""
