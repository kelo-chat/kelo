"""Finding a one-time sign-in code that a site just mailed to the user (find_login_code, and the automatic path).

`find_code(messages, site)` looks through recent messages (newest first) for one that comes from that site (the sender's address or name,
or the subject, names it) and pulls the code out of it: digits or a short code of capitals and digits next to words like "code",
"verification" (and the same words in many languages), or standing alone on a line. Years, phone numbers, amounts and dates are not
codes. That loose match is only ever shown to the user on an approval card.

`pick_auto_code(messages, site, asked_at, now)` is the strict match used to type a code WITHOUT asking, during a sign-in the agent
itself started on that site: the mail arrived from AUTO_EARLY_SECONDS before the code was asked for up to AUTO_WINDOW_SECONDS after;
its sender is the site (the registrable domain of the From address, or of a DKIM signature d= or the Return-Path the mail carries,
each dropped when the receiving server reported it failing); the code sits next to a code word; and there is exactly one such code.
Anything else is "none" or "ambiguous" and the normal flow (ask the user) takes over.

The code is never stored or logged: it is kept in memory only between finding it and typing it (or the user saying no), and the model
only ever sees who sent it and how long it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr

_KEYWORDS = re.compile(r"(code|verification|verify|one[-\s]?time|otp|passcode|security|sign[-\s]?in|log[-\s]?in|confirm|pin|인증|코드|확인\s*번호|"
                       r"일회용|보안|로그인|認証|確認コード|コード|验证码|驗證碼|校验码|代码|código|codigo|codice|kode|kod|kodu|код|"
                       r"bestätigung|verifica|vérification|verificación|verificação|weryfikac|doğrulama|подтвержд|mã|xác minh|รหัส|"
                       r"رمز|التحقق|कोड|सत्यापन)", re.I)
_CANDIDATE = re.compile(r"(?<![\w\-./:#@$₩€£])((?:\d[ \-]?){3,7}\d|[A-Z0-9]{1,4}-[A-Z0-9]{2,6}|(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,8})(?![\w\-/:%@]|\.\d)")
_GENERIC_LABELS = {"www", "com", "net", "org", "co", "kr", "jp", "io", "app", "mail", "accounts", "account", "login", "auth", "id", "secure",
                   "my", "m", "no-reply", "noreply"}


@dataclass(slots=True)
class FoundCode:
    code: str
    sender: str          # "GitHub <noreply@github.com>" as the mail showed it (shown to the user only)
    subject: str
    at: float
    account: str = ""


def site_words(site: str) -> list[str]:
    """The distinctive names of a site: "accounts.example.co.kr" -> ["example"]; "github.com" -> ["github"]."""
    host = re.sub(r"^[a-z]+://", "", (site or "").lower()).split("/")[0].split(":")[0]
    parts = [p for p in host.split(".") if p and p not in _GENERIC_LABELS and len(p) >= 3]
    return parts[-1:] if parts else []


def base_domain(host: str) -> str:
    """The registrable part of a host, roughly: "login.example.co.kr" -> "example.co.kr", "www.example.com" -> "example.com"."""
    labels = [p for p in re.sub(r"^[a-z]+://", "", (host or "").lower()).split("/")[0].split(":")[0].split(".") if p]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "or", "go", "ac", "ne", "re", "com", "net", "org", "gov", "edu"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


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
    said = re.split(r"[^a-z0-9가-힣]+", " ".join([name.lower(), str(msg.get("subject") or "").lower()]))
    return any(w in said for w in words)


def _plausible(raw: str, context: str) -> str | None:
    code = re.sub(r"[ ]", "", raw)
    digits = re.sub(r"\D", "", code)
    if code.isdigit():
        if not 4 <= len(code) <= 8:
            return None
        if len(code) == 4 and re.fullmatch(r"(19|20)\d\d", code) and not _KEYWORDS.search(context):
            return None       # a year
        if re.search(r"[₩$€£]\s*$|원|krw|usd", context[-12:], re.I):
            return None
    elif "-" in code:
        if "-" in raw and raw.replace("-", "").isdigit() and len(digits) > 8:
            return None       # a phone number
        if not re.search(r"\d", code):
            return None
    return code


def _candidates(text: str) -> list[tuple[int, str, bool]]:
    """Every plausible code in one message's text: (score, code, next to a code word), in reading order."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out: list[tuple[int, str, bool]] = []
    for i, line in enumerate(lines[:200]):
        around = " ".join(lines[max(0, i - 2): i + 1])
        for m in _CANDIDATE.finditer(line):
            ctx = (around[: around.rfind(line)] if line in around else "") + line[: m.start()]
            code = _plausible(m.group(1), ctx)
            if code is None:
                continue
            near = bool(_KEYWORDS.search(line) or _KEYWORDS.search(around))
            alone = line.replace(m.group(1), "").strip(" :.-()[]") == ""
            score = (2 if near else 0) + (2 if alone else 0) + (1 if code.isdigit() and len(code) in (4, 6, 8) else 0)
            if score >= 2:
                out.append((score, code, near))
    return out


def extract(text: str) -> str | None:
    """The code in one message's text, or None. A candidate next to a code word wins; else one alone on its line."""
    best: tuple[int, str] | None = None
    for score, code, _ in _candidates(text):
        if best is None or score > best[0]:
            best = (score, code)
    return best[1] if best else None


def strict_codes(text: str) -> set[str]:
    """The best-scoring codes of one message that sit next to a code word (one element when the message is clear)."""
    found = [(score, code) for score, code, near in _candidates(text) if near]
    top = max((score for score, _ in found), default=0)
    return {code for score, code in found if score == top}


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


def pick_auto_code(messages: list[dict], site: str, asked_at: float, now: float) -> tuple[AutoCode | None, str]:
    """The one code the site mailed for this sign-in, or (None, "none" | "ambiguous" | "late"). See the module notes."""
    if now - asked_at > AUTO_WINDOW_SECONDS:
        return None, "late"
    picks: list[AutoCode] = []
    for msg in messages:
        at = float(msg.get("arrived") or msg.get("at") or 0)
        if not at or at < asked_at - AUTO_EARLY_SECONDS or at > asked_at + AUTO_WINDOW_SECONDS or at > now + AUTO_EARLY_SECONDS:
            continue
        how = sender_match(msg, site)
        if how is None:
            continue
        codes = strict_codes(str(msg.get("subject") or "") + "\n" + str(msg.get("text") or ""))
        if len(codes) > 1:
            return None, "ambiguous"
        if codes:
            picks.append(AutoCode(codes.pop(), plain_address(str(msg.get("from") or "")), how, at))
    if not picks:
        return None, "none"
    if len({p.code for p in picks}) > 1:
        return None, "ambiguous"
    return max(picks, key=lambda p: p.at), ""
