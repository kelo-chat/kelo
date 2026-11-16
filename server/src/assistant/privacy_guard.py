"""The privacy guard: a check right before anything leaves for someone else (a message, a mail, a form, an HTTP body).

It looks in the outgoing text for the owner's own sensitive values (what Kelo knows from user.md / MEMORY.md / memory/people, the
shipping profiles, the wallet, the names of vault items) and for patterns of the kinds a person never wants passed on by accident:
ID numbers, passport numbers, card numbers (Luhn), bank accounts, door and entry codes. A hit makes the step ask for a card (loop.py,
through permissions.Gate), whatever the owner's "allow" setting or a task's permission says; the card line says what goes where
("Sends to <recipient>: home address, phone number"), never the values themselves.

Rules the code keeps: matching is on a normalised form (digits only for numbers, whitespace and case folded for addresses); the vault
is read by item NAME only (never decrypted); nothing found is ever logged (only the category words); the owner's own mailboxes are
no outside recipient. Feelings, health and routines are caught where the owner wrote them down as a labelled value (a line such as
"health: ...") and by the door/entry code pattern; the rest is the prompt's rule (system.md): when unsure, leave it out and ask.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit

from .i18n import N_, t

log = logging.getLogger(__name__)

CARD_LINE = N_("Sends to {where}: {what}")
LEAK_REFUSAL = N_("this would send Kelo's private marker outside; stopped")
URL_TOOLS = frozenset({"web_fetch", "browser_open"})   # a GET carries data in its path and query: checked like a send (without host and port)
CATEGORY_LABELS = {
    "address": N_("home address"),
    "phone": N_("phone number"),
    "contact": N_("someone's contact details"),
    "card": N_("card number"),
    "account": N_("bank account"),
    "id": N_("ID or passport number"),
    "code": N_("door or access code"),
    "health": N_("health details"),
    "vault": N_("a saved login"),
}
# what an "apply" permission (schedule_grants.APPLY_STEPS) covers on its own: the form's basic fields; any other category still asks
BASIC = frozenset({"address", "phone"})
CHECKED_COMMITS = frozenset({"form", "personal", "apply", "security"})   # browser steps (tools/commits.py) that put the owner's data on a site
# arguments that name the recipient or the route of a call, not what is said: never scanned
ROUTE_KEYS = frozenset({"to", "cc", "bcc", "account", "method", "url", "name", "tool", "site", "selector", "package", "provider", "id",
                        "headers", "path", "action", "ref"})
MIN_VALUE = 6   # a stored value shorter than this is too common to match on its own
CACHE_SECONDS = 15
LABELLED = {   # "label: value" lines of user.md / MEMORY.md: the value is the owner's own and matched whole
    "address": re.compile(r"^\W*(?:home\s+|my\s+)?(?:address|주소|집\s*주소|자택|거주지)\W*[:：]\s*(.+)$", re.I),
    "health": re.compile(r"^\W*(?:health|allerg\w*|medication|condition|diagnosis|건강|알레르기|복용|약|병력|질환|진단)\W*[:：]\s*(.+)$", re.I),
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\d])\+?\d[\d\s().-]{6,17}\d(?![\d])")
# A date or a time span is not a phone number ("2026-11-12" in a note or in a site's address, "2026.11.12 10:30")
_DATE = re.compile(r"\s*(?:19|20)\d{2}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}(?:[\sT]+\d{1,2}[.:]\d{2})?\s*")
# A number in the owner's notes counts as their phone when it is written like one (+82 …, 010-…) or its line says it is one
_PHONE_WORD = re.compile(r"phone|mobile|\btel\b|전화|휴대폰|핸드폰|연락처", re.I)
_CARD = re.compile(r"(?<![\d])\d(?:[ -]?\d){12,18}(?![\d])")
_RRN = re.compile(r"(?<!\d)(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[ -]?([1-8])\d{6}(?!\d)")
_SSN = re.compile(r"(?<!\d)(?!000|666|9)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)")
_PASSPORT = re.compile(r"(?:passport|여권)\D{0,24}?\b([A-Z]{1,2}\d{7,8})\b", re.I)
_ACCOUNT_GROUPS = re.compile(r"(?<!\d)\d{2,6}(?:-\d{2,8}){2,3}(?!\d)")
_ACCOUNT_WORD = re.compile(r"(?:계좌|account\s*(?:no|number|#)?|iban)\D{0,12}?(\d[\d -]{8,22}\d)", re.I)
_CODE = re.compile(r"(?:비밀번호|도어락|현관|공동현관|출입(?:문|코드|번호)?|door\s*(?:lock\s*)?(?:code|pin)?|passcode|gate\s*code|entry\s*code|access\s*code)"
                   r"[^\d\n]{0,14}([\d*#]{4,8})(?!\d)", re.I)
_CARD_TAIL = re.compile(r"(?:card|카드|ending|끝자리|••|\*{2,})\D{0,12}(\d{4})(?!\d)", re.I)


def luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total, alt = total + d, not alt
    return len(digits) >= 12 and total % 10 == 0


def digits_of(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def fold(text: str) -> str:
    """Whitespace-folded, lower-case, punctuation-light: addresses written a little differently compare the same."""
    return re.sub(r"[\s,.\-()/]+", " ", str(text or "").casefold()).strip()


@dataclass(slots=True)
class Hit:
    categories: tuple[str, ...]
    line: str
    basic_only: bool   # only the form's basic fields (address, phone): an "apply" permission covers them
    leak: bool = False  # the text carries Kelo's private marker (injection_watch.LeakMarker): the step is refused, not asked


@dataclass(slots=True)
class Known:
    """What the owner's own data holds, in comparable forms (built from the files, shipping profiles, wallet and vault names)."""
    addresses: set[str]
    phones: set[str]
    others: set[str]       # emails and phone tails of the people in memory/people
    labelled: dict[str, set[str]]
    last4: set[str]
    cards: set[str]
    vault_names: set[str]
    own_emails: set[str]


def _flat(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if str(k).casefold() not in ROUTE_KEYS:
                _flat(v, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _flat(v, out, depth + 1)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out.append(str(value))


def outgoing_text(args: dict[str, Any], extra: str = "") -> str:
    """Everything a call says (message, subject, typed value, form fields, body), without who or where it goes."""
    parts: list[str] = []
    _flat(args, parts)
    if extra and extra not in parts:
        parts.append(extra)
    return "\n".join(parts)


class PrivacyGuard:
    def __init__(self, *, home: Any = None, store: Any = None, wallet: Any = None, shipping: Any = None,
                 own_addresses: Callable[[], Iterable[str]] | None = None, clock: Callable[[], float] = time.monotonic,
                 marker: Any = None, on_leak: Callable[[str, str], None] | None = None):
        """`marker`: injection_watch.LeakMarker (anything outgoing that carries its token is a leak); `on_leak(tool, where)`: told when
        one is stopped."""
        self.home, self.store, self.wallet, self.shipping = home, store, wallet, shipping
        self.own_addresses = own_addresses
        self.marker, self.on_leak = marker, on_leak
        self._clock = clock
        self._known: Known | None = None
        self._built = -1e9

    # ------------------------------------------------------------------ what the owner's data holds
    def known(self) -> Known:
        if self._known is not None and self._clock() - self._built < CACHE_SECONDS:
            return self._known
        k = Known(set(), set(), set(), {c: set() for c in LABELLED}, set(), set(), set(), set())
        try:
            self._from_home(k)
        except Exception:  # noqa: BLE001 - a file that cannot be read leaves out its values; the patterns still work
            log.warning("privacy guard: could not read the home files")
        try:
            for p in (self.shipping.profiles() if self.shipping is not None else []):
                line = " ".join(str(p.get(f) or "") for f in ("address_line1", "address_line2"))
                if len(fold(line)) >= MIN_VALUE:
                    k.addresses.add(fold(line))
                if len(digits_of(str(p.get("phone") or ""))) >= 7:
                    k.phones.add(digits_of(str(p["phone"]))[-10:])
        except Exception:  # noqa: BLE001
            log.warning("privacy guard: could not read the shipping profiles")
        try:
            for c in (self.wallet.cards() if self.wallet is not None else []):
                if re.fullmatch(r"\d{4}", str(c.get("last4") or "")):
                    k.last4.add(str(c["last4"]))
        except Exception:  # noqa: BLE001
            log.warning("privacy guard: could not read the wallet")
        try:
            for c in (self.store.credentials() if self.store is not None else []):
                name = " ".join(str(c.get("name") or "").split())
                if len(name) >= 3:
                    k.vault_names.add(name.casefold())   # the NAME only: the value is never opened here
        except Exception:  # noqa: BLE001
            log.warning("privacy guard: could not read the vault names")
        if self.own_addresses is not None:
            try:
                k.own_emails = {str(a).strip().casefold() for a in self.own_addresses() if a}
            except Exception:  # noqa: BLE001
                pass
        self._known, self._built = k, self._clock()
        return k

    def _from_home(self, k: Known) -> None:
        if self.home is None:
            return
        from .home import PEOPLE_DIR
        texts: list[tuple[str, bool]] = []
        for name in ("user.md", "MEMORY.md"):
            try:
                texts.append((self.home.read(name), False))
            except Exception:  # noqa: BLE001 - a missing file
                continue
        for p in self.home.people():
            try:
                texts.append((self.home.read(p["path"]), True))
            except Exception:  # noqa: BLE001
                continue
        for text, person in texts:
            for line in text.splitlines():
                for cat, rx in LABELLED.items():
                    m = rx.match(line.strip())
                    if m and not person and len(fold(m.group(1))) >= MIN_VALUE:
                        k.labelled[cat].add(fold(m.group(1)))
                for e in _EMAIL.findall(line):
                    if person:
                        k.others.add(e.casefold())
                for ph in _PHONE.findall(line):
                    d = digits_of(ph)
                    if not (8 <= len(d) <= 15) or _CARD.fullmatch(ph.strip()) or _DATE.fullmatch(ph):
                        continue
                    if ph.lstrip().startswith(("+", "0")) or _PHONE_WORD.search(line):
                        (k.others if person else k.phones).add(d[-10:])

    # ------------------------------------------------------------------ the check
    def scan(self, text: str) -> list[str]:
        """The categories found in `text`, in a fixed order (no values)."""
        found: set[str] = set()
        k = self.known()
        folded = fold(text)
        for a in k.addresses | k.labelled["address"]:
            if a in folded:
                found.add("address")
        for h in k.labelled["health"]:
            if h in folded:
                found.add("health")
        for m in _CARD.finditer(text):
            d = digits_of(m.group())
            if 13 <= len(d) <= 19 and luhn(d):
                found.add("card")
        if k.last4 and any(m.group(1) in k.last4 for m in _CARD_TAIL.finditer(text)):
            found.add("card")
        for m in _PHONE.finditer(text):
            d = digits_of(m.group())
            if 8 <= len(d) <= 15 and not _CARD.fullmatch(m.group().strip()) and not _DATE.fullmatch(m.group()):
                if d[-10:] in k.phones:
                    found.add("phone")
                elif d[-10:] in k.others:
                    found.add("contact")
        for e in _EMAIL.findall(text):
            if e.casefold() in k.others:
                found.add("contact")
        if _RRN.search(text) or _SSN.search(text) or _PASSPORT.search(text):
            found.add("id")
        phone_spans = [m.span() for m in _PHONE.finditer(text) if m.group().lstrip().startswith(("+", "0"))]   # +82 10-..., 010-...
        for m in _ACCOUNT_GROUPS.finditer(text):
            d = digits_of(m.group())
            if any(a <= m.start() and m.end() <= b for a, b in phone_spans) or d[-10:] in k.phones | k.others:
                continue
            if 10 <= len(d) <= 16 and not luhn(d):
                found.add("account")
        if _ACCOUNT_WORD.search(text):
            found.add("account")
        if _CODE.search(text):
            found.add("code")
        low = text.casefold()
        if any(re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", low) for n in k.vault_names):
            found.add("vault")
        return [c for c in CATEGORY_LABELS if c in found]

    def own_only(self, recipients: list[str]) -> bool:
        own = self.known().own_emails
        return bool(recipients) and bool(own) and all(r.strip().casefold() in own for r in recipients)

    def is_own(self, target: str) -> bool:
        """A recipient that is the owner's own (a mailbox of theirs, or a phone number of theirs from user.md): no outside party."""
        target = str(target or "").strip().casefold()
        k = self.known()
        return bool(target) and (target in k.own_emails or (target.isdigit() and target[-10:] in k.phones))

    def _leak(self, name: str, where: str, text: str) -> Hit | None:
        """A Hit when `text` carries the private marker; the watch is told (it records the event and tells the owner)."""
        if self.marker is None or not self.marker.in_text(text):
            return None
        log.warning("privacy guard: %s carried the private marker; stopped", name)   # the marker itself is never logged
        if self.on_leak is not None:
            try:
                self.on_leak(name, where)
            except Exception:  # noqa: BLE001 - telling the owner must not let the step through
                log.warning("privacy guard: could not report a leak")
        return Hit((), t(LEAK_REFUSAL), False, leak=True)

    def check_url(self, tool: Any, args: dict[str, Any]) -> Hit | None:
        """web_fetch / browser_open: the address is scanned like an outgoing text, without the parts that only route it (scheme, host,
        port, login): its path, query and fragment may carry the owner's values or the private marker (a GET is a way to send data)."""
        name = str(getattr(tool, "name", tool))
        url = str((args or {}).get("url") or "").strip()
        if not url:
            return None
        try:
            parts = urlsplit(url if "://" in url else "https://" + url)
            host = (parts.hostname or "")[:80]
        except ValueError:
            return None
        whole = unquote(unquote(url))
        if (hit := self._leak(name, host, url + "\n" + whole)) is not None:
            return hit
        text = unquote(unquote(" ".join(p for p in (parts.path, parts.query, parts.fragment) if p)))
        cats = self.scan(text)
        if not cats:
            return None
        log.info("privacy guard: %s asks (%s)", name, ", ".join(cats))
        return Hit(tuple(cats), t(CARD_LINE).format(where=host, what=", ".join(t(CATEGORY_LABELS[c]) for c in cats)), False)

    def check(self, tool: Any, args: dict[str, Any], effect: Any = None, *, commit: str = "", site: str = "",
              basic_ok: bool = False) -> Hit | None:
        """A Hit when what this call sends holds the owner's sensitive values, else None. `effect`: effects.effect_of's answer for a
        message, mail or HTTP call; `commit` / `site`: a browser step's class and place (only form, personal, apply and security count).
        `basic_ok`: an "apply" permission covers this site, so a hit of the form's basic fields only is not one. A Hit with `leak` set:
        the call carries Kelo's private marker (any step that sends, changes or deletes, or types into a form): refused."""
        name = str(getattr(tool, "name", tool))
        args = args if isinstance(args, dict) else {}
        sends = effect is not None and effect.kind == "send"
        if effect is not None and effect.kind in ("send", "change", "delete"):
            where = ", ".join(x.removeprefix("site:").removeprefix("connector:") for x in effect.targets if x) or (effect.connector or name)
            text = outgoing_text(args, effect.text)
        elif (commit in CHECKED_COMMITS or (name in ("browser_type", "android_type") and commit != "unknown")) and site:
            where = site
            text = outgoing_text(args)
            sends = True
        else:
            return None
        if (leak := self._leak(name, where[:80], text + "\n" + json.dumps(args, ensure_ascii=False, default=str))) is not None:
            return leak
        if not sends:
            return None
        if effect is not None:
            recipients = [str(x) for key in ("to", "cc", "bcc") for x in ([args.get(key)] if isinstance(args.get(key), str) else args.get(key) or [])]
            if self.own_only(recipients):
                return None   # the owner's own mailbox is no outside party
        cats = self.scan(text)
        if not cats:
            return None
        basic_only = all(c in BASIC for c in cats)
        if basic_ok and basic_only:
            return None
        log.info("privacy guard: %s asks (%s)", name, ", ".join(cats))   # the categories only, never a value
        what = ", ".join(t(CATEGORY_LABELS[c]) for c in cats)
        return Hit(tuple(cats), t(CARD_LINE).format(where=where[:80], what=what), basic_only)
