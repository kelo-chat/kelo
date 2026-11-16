"""A mailbox connected with an app password over IMAP (read) and SMTP (send): Gmail, Naver, Daum, iCloud. No OAuth app, no review by the
mail provider, no central server. The address and the app password are sealed in the vault as one item of kind "email"; the assistant
reads and sends through the tools below and never sees the password.

arrivals() reads what came in since a cursor (UIDVALIDITY + last UID) for the proactive pass: inbox, spam folder or Gmail promotions.

Only the servers written here are ever contacted (the user cannot point the app at another host).
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import email
import email.policy
import email.utils
import imaplib
import json
import re
import smtplib
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parseaddr
from html import unescape
from typing import Any

from .i18n import N_, t
from .store import Store
from .vault import VaultError

TIMEOUT = 25
BODY_CAP = 12_000
UPDATE_TIMEOUT = 600   # a change over thousands of messages: many STORE/MOVE commands in one sign-in
CHUNK = 500            # UIDs per STORE/MOVE command (keeps each command line a few KB)
ACTIONS = ("read", "unread", "flag", "unflag", "archive", "move", "trash")
# folder names to fall back on when a server marks no special-use folders (RFC 6154): compared case-insensitively, last segment
TRASH_NAMES = ("trash", "deleted messages", "deleted items", "deleted", "bin", "휴지통")
ARCHIVE_NAMES = ("archive", "archives", "보관함")
JUNK_NAMES = ("spam", "junk", "junk email", "junk e-mail", "bulk mail", "스팸", "스팸메일함", "스팸 메일함")
ARRIVALS_WAIT = 300    # what arrived since the last look (a long gap can be thousands of messages, read in chunks)
FETCH_CHUNK = 100      # UIDs per FETCH of those
SNIPPET_BYTES = 2000   # how much of a body is fetched for the snippet
SNIPPET_CHARS = 300


@dataclass(frozen=True)
class MailProvider:
    id: str
    name: str
    imap: str
    smtp: str
    smtp_port: int          # 465 = TLS from the start, 587 = STARTTLS
    connectors: tuple[str, ...]
    help: tuple[str, ...]


PROVIDERS: dict[str, MailProvider] = {p.id: p for p in [
    MailProvider("gmail", "Gmail", "imap.gmail.com", "smtp.gmail.com", 465, ("gmail", "imap_email"),
                 (N_("Turn on 2-Step Verification in your Google account (myaccount.google.com → Security)."),
                  N_("Create an app password at myaccount.google.com/apppasswords (any name works)."),
                  N_("Paste the 16-character password below. It is not your usual Google password."))),
    MailProvider("icloud", N_("iCloud Mail"), "imap.mail.me.com", "smtp.mail.me.com", 587, ("imap_email",),
                 (N_("Create a password at account.apple.com → Sign-In and Security → App-Specific Passwords."),
                  N_("Enter your @icloud.com address."))),
    MailProvider("yahoo", N_("Yahoo Mail"), "imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465, ("imap_email",),
                 (N_("Turn on 2-step verification in your Yahoo account (login.yahoo.com → Account security)."),
                  N_("In Account security, choose Generate app password (any name works)."),
                  N_("Paste that password below. It is not your usual Yahoo password."))),
    MailProvider("naver", N_("Naver Mail"), "imap.naver.com", "smtp.naver.com", 465, ("imap_email",),
                 (N_("In Naver Mail → Settings → POP3/IMAP settings, turn on IMAP/SMTP."),
                  N_("If you use 2-step verification, create an application password in Naver security settings and enter it below. If not, use your usual password."))),
    MailProvider("daum", N_("Daum Mail"), "imap.daum.net", "smtp.daum.net", 465, ("imap_email",),
                 (N_("In Daum Mail → Settings → IMAP/POP3, turn on IMAP."),
                  N_("If you use 2-step verification, create an app password for your Kakao account and enter it below."))),
]}

ImapFactory = Callable[[str], Any]
SmtpFactory = Callable[[str, int], Any]


def _imap(host: str) -> imaplib.IMAP4_SSL:
    return imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)


def _smtp(host: str, port: int) -> smtplib.SMTP:
    if port == 465:
        return smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=TIMEOUT)
    s = smtplib.SMTP(host, port, timeout=TIMEOUT)
    s.starttls(context=ssl.create_default_context())
    return s


class MailError(Exception):
    """Shown to the user or the assistant as it is."""


class XOAuth2:
    """A mailbox signed in with an OAuth token instead of a password."""
    def __init__(self, token: str):
        self.token = token


def _imap_signin(conn: Any, user: str, secret: str | XOAuth2) -> None:
    if isinstance(secret, XOAuth2):
        conn.authenticate("XOAUTH2", lambda _: f"user={user}\x01auth=Bearer {secret.token}\x01\x01".encode())
    else:
        conn.login(user, secret)


def _smtp_signin(conn: Any, user: str, secret: str | XOAuth2) -> None:
    if isinstance(secret, XOAuth2):
        conn.ehlo_or_helo_if_needed()   # the server answers AUTH with 503 until it was greeted (login() does this itself, docmd does not)
        raw = base64.b64encode(f"user={user}\x01auth=Bearer {secret.token}\x01\x01".encode()).decode()
        code, reply = conn.docmd("AUTH", "XOAUTH2 " + raw)
        if code != 235:
            raise smtplib.SMTPAuthenticationError(code, reply)
    else:
        conn.login(user, secret)

TokenSource = Callable[[str], Awaitable[dict[str, Any]]]   # refresh token -> {"access_token", "expires_in"}


def item_name(provider: str) -> str:
    return f"mail-{provider}"


def _text_of(msg: email.message.Message) -> str:
    plain = html = ""
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            continue
        if ctype == "text/plain" and not plain:
            plain = str(content)
        elif ctype == "text/html" and not html:
            html = str(content)
    if not plain and html:
        plain = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html)))
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", plain)).strip()


def _arrived(head: Any) -> float:
    """When the mail server received a message (its INTERNALDATE in a FETCH answer), or 0 when the answer has none."""
    try:
        tt = imaplib.Internaldate2tuple(head if isinstance(head, bytes) else str(head).encode())
        return time.mktime(tt) if tt else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


_AUTH_RESULT = re.compile(r"\b(dkim|spf|dmarc)\s*=\s*(\w+)([^;]*)", re.I)


def _sender_headers(msg: email.message.Message) -> dict[str, Any]:
    """What a message says about who sent it, beyond From: the d= of its DKIM signatures and its Return-Path domain, minus any the
    receiving server reported failing (Authentication-Results), and whether From failed DMARC there. Used only to tell a site's own
    mail apart (tools/login_code.sender_match); nothing here is shown to the model."""
    failed: dict[str, set[str]] = {"dkim": set(), "spf": set(), "dmarc": set()}
    passed_dkim: set[str] = set()
    # only the topmost one: the receiving server adds it last; ones further down came with the message and prove nothing
    results = [str(v) for v in (msg.get_all("authentication-results") or [])][:1]
    for res in results:
        for kind, verdict, rest in _AUTH_RESULT.findall(res):
            dom = re.search(r"(?:header\.d|header\.i|smtp\.mailfrom|header\.from)\s*=\s*@?([\w.\-@]+)", rest, re.I)
            name = (dom.group(1).rsplit("@", 1)[-1].lower() if dom else "*")
            if verdict.lower() in ("fail", "softfail", "permerror", "policy"):
                failed[kind.lower()].add(name)
            elif kind.lower() == "dkim" and verdict.lower() == "pass":
                passed_dkim.add(name)
    dkim: list[str] = []
    for sig in msg.get_all("dkim-signature") or []:
        m = re.search(r"(?:^|;)\s*d\s*=\s*([\w.\-]+)", str(sig), re.I)
        d = m.group(1).lower() if m else ""
        if d and d not in failed["dkim"] and "*" not in failed["dkim"] and (not results or d in passed_dkim) and d not in dkim:
            dkim.append(d)
    rp = parseaddr(str(msg.get("return-path", "")))[1]
    rp_dom = rp.rsplit("@", 1)[-1].lower() if "@" in rp else ""
    if rp_dom and (rp_dom in failed["spf"] or "*" in failed["spf"]):
        rp = ""
    return {"dkim": dkim, "return_path": rp, "from_failed": bool(failed["dmarc"])}


# Marketing and bulk mail, for every mailbox: the standard headers a mailing list, a newsletter or an automated sender puts on its mail
# (RFC 2369 List-Unsubscribe, RFC 2919 List-Id, Precedence: bulk/list/junk, RFC 3834 Auto-Submitted other than "no"). The briefing's
# "may have missed" and the heads-ups leave such mail out (list(skip_bulk=True)); the chat's email_list shows everything.
BULK_HEADERS = ("LIST-UNSUBSCRIBE", "LIST-ID", "PRECEDENCE", "AUTO-SUBMITTED")
# Gmail sorts promotions, social updates and forums into categories of its own (searched with X-GM-RAW); those never count as missed.
GMAIL_NOT_PERSONAL = "-category:promotions -category:social -category:forums"


def is_bulk(head: Any) -> bool:
    """Whether a message's headers mark it as list, bulk or automated mail (any mapping or email.message with .get)."""
    def get(name: str) -> str:
        try:
            v = head.get(name)
        except Exception:  # noqa: BLE001 - a broken header is not a signal
            return ""
        return str(v or "").strip().lower()
    if get("list-unsubscribe") or get("list-id"):
        return True
    if get("precedence") in ("bulk", "list", "junk"):
        return True
    auto = get("auto-submitted")
    return bool(auto) and auto != "no"


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mutf7_encode(name: str) -> str:
    """A folder name as IMAP writes it (modified UTF-7, RFC 3501 5.1.3): "Archive" stays, "영수증" -> "&xjjI2KdE-"."""
    out, run = [], []

    def flush() -> None:
        if run:
            raw = "".join(run).encode("utf-16-be")
            out.append("&" + base64.b64encode(raw).decode().rstrip("=").replace("/", ",") + "-")
            run.clear()
    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            run.append(ch)
    flush()
    return "".join(out)


def mutf7_decode(name: str) -> str:
    def one(m: re.Match[str]) -> str:
        body = m.group(1)
        if not body:
            return "&"
        raw = body.replace(",", "/")
        try:
            return base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-16-be")
        except ValueError:
            return m.group(0)
    return re.sub(r"&([^-]*)-", one, name)


@dataclass(frozen=True)
class Folder:
    raw: str              # as the server writes it (modified UTF-7): what SELECT and MOVE take
    name: str             # readable
    flags: frozenset[str]  # lower case: \trash, \archive, \all, \noselect ...
    delim: str = "/"

    @property
    def leaf(self) -> str:
        return self.name.rsplit(self.delim, 1)[-1] if self.delim else self.name


_LIST_ROW = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.*)$', re.I)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return re.sub(r"\\(.)", r"\1", text[1:-1])
    return text


def _folders(conn: Any) -> list[Folder]:
    typ, rows = conn.list()
    if typ != "OK":
        return []
    out = []
    for row in rows or []:
        literal = None
        if isinstance(row, tuple):
            row, literal = row[0], row[1]
        if not isinstance(row, (bytes, str)):
            continue
        line = row.decode(errors="replace") if isinstance(row, bytes) else row
        m = _LIST_ROW.match(line.strip())
        if not m:
            continue
        raw = (literal.decode(errors="replace") if isinstance(literal, bytes) else str(literal)) if literal is not None else _unquote(m["name"])
        delim = "" if m["delim"].upper() == "NIL" else _unquote(m["delim"])
        out.append(Folder(raw, mutf7_decode(raw), frozenset(f.lower() for f in m["flags"].split()), delim))
    return out


def _find_folder(folders: list[Folder], wanted: str) -> Folder | None:
    """The folder the user named: its exact name, else the same name in any case, else a unique last segment ("Trash" for
    "[Gmail]/Trash")."""
    w = wanted.strip()
    if w.upper() == "INBOX":
        return next((f for f in folders if f.raw.upper() == "INBOX"), Folder("INBOX", "INBOX", frozenset()))
    for test in (lambda f: f.name == w, lambda f: f.name.casefold() == w.casefold(), lambda f: f.leaf.casefold() == w.casefold()):
        hits = [f for f in folders if test(f) and "\\noselect" not in f.flags]
        if len(hits) == 1:
            return hits[0]
    return None


def _special(folders: list[Folder], flag: str, names: tuple[str, ...]) -> Folder | None:
    """The server's folder for a use (\\Trash, \\Archive, \\All), by its RFC 6154 mark, else by a usual name."""
    marked = [f for f in folders if flag in f.flags]
    if marked:
        return marked[0]
    for n in names:
        hits = [f for f in folders if f.leaf.casefold() == n and "\\noselect" not in f.flags]
        if hits:
            return hits[0]
    return None


def _caps(conn: Any) -> set[str]:
    """What the server can do once signed in (Gmail adds MOVE and more only after the sign-in)."""
    try:
        typ, data = conn.capability()
        if typ == "OK" and data:
            return {c.upper() for c in b" ".join(d for d in data if isinstance(d, bytes)).decode(errors="replace").split()}
    except (AttributeError, imaplib.IMAP4.error):
        pass
    return {str(c).upper() for c in getattr(conn, "capabilities", ())}


def _uid_state(conn: Any) -> tuple[Any, int | None]:
    """(UIDVALIDITY, UIDNEXT) of the folder just selected; None for what the server did not say."""
    def one(code: str) -> int | None:
        try:
            _typ, data = conn.response(code)
            vals = [d for d in (data or []) if d]
            return int(vals[-1]) if vals else None
        except (AttributeError, TypeError, ValueError, IndexError, imaplib.IMAP4.error):
            return None
    return one("UIDVALIDITY"), one("UIDNEXT")


def uid_sets(uids: list[int], size: int = CHUNK) -> list[tuple[str, int]]:
    """UIDs in chunks of `size`, each as a compact IMAP set ("3:9,12,40:41") with how many it holds."""
    out: list[tuple[str, int]] = []
    ordered = sorted(set(uids))
    for i in range(0, len(ordered), size):
        part = ordered[i:i + size]
        ranges: list[str] = []
        start = prev = part[0]
        for u in part[1:] + [None]:   # type: ignore[list-item]
            if u is not None and u == prev + 1:
                prev = u
                continue
            ranges.append(str(start) if start == prev else f"{start}:{prev}")
            if u is not None:
                start = prev = u
        out.append((",".join(ranges), len(part)))
    return out


def _search(conn: Any, gmail: bool, query: str, criteria: list[str]) -> list[bytes]:
    """One UID SEARCH: `criteria`, plus the query in the server's own search words (Gmail's X-GM-RAW: from:, category:promotions,
    newer_than:2d ...) or as text. A query with non-ASCII words goes as a UTF-8 literal."""
    key = "X-GM-RAW" if gmail else "TEXT"
    if query and not query.isascii():
        conn.literal = query.encode("utf-8")
        typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *criteria, key)
    else:
        crit = criteria + ([key, _quote(query)] if query else [])
        typ, data = conn.uid("SEARCH", *(crit or ["ALL"]))
    if typ != "OK":
        raise MailError("the mail server did not accept that search")
    return (data[0] or b"").split() if data else []


@dataclass(frozen=True)
class Selection:
    """Which messages a change is for: uids from email_list, or a search in email_list's form (every match, however many)."""
    folder: str = "INBOX"
    uids: tuple[str, ...] = ()
    query: str = ""
    unread: bool = False
    every: bool = False     # the whole folder, when nothing else narrows it

    @classmethod
    def of(cls, folder: str = "INBOX", uids: Any = (), query: str = "", unread: bool = False, every: bool = False) -> "Selection":
        ids = [str(u).strip() for u in (uids if isinstance(uids, (list, tuple)) else [uids] if uids else [])]
        ids = [u for u in ids if u]
        if any(not re.fullmatch(r"\d{1,12}", u) for u in ids):
            raise MailError("uids must be the numbers email_list gave")
        if len(ids) > 1000:   # one SEARCH line; a search covers any number
            raise MailError("at most 1000 uids at once; a search (query, unread) covers any number")
        query = " ".join(str(query or "").split())[:500]
        if not (ids or query or unread or every):
            raise MailError("say which messages: uids from email_list, or a search (query and/or unread), or all=true for the whole folder")
        return cls(str(folder or "INBOX").strip() or "INBOX", tuple(ids), query, bool(unread), bool(every))

    def criteria(self) -> list[str]:
        out = ["UNSEEN"] if self.unread else []
        if self.uids:
            out += ["UID", ",".join(c for c, _ in uid_sets([int(u) for u in self.uids], size=len(self.uids)))]
        return out


class Mail:
    def __init__(self, store: Store, *, imap: ImapFactory = _imap, smtp: SmtpFactory = _smtp, renew: TokenSource | None = None):
        self.store, self.imap, self.smtp, self.renew = store, imap, smtp, renew

    # ---- accounts
    def accounts(self) -> list[dict[str, Any]]:
        out = []
        for c in self.store.credentials():
            if c["kind"] == "email" and c["has_secret"]:
                pid = c["name"].removeprefix("mail-")
                if pid in PROVIDERS:
                    out.append({"name": c["name"], "provider": pid, "label": c["label"], "address": c["label"].split(" · ")[-1]})
        return out

    def _login_sync(self, provider: MailProvider, address: str, password: str) -> None:
        conn = self.imap(provider.imap)
        try:
            conn.login(address, password)
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - already closed
                pass

    async def connect(self, provider: str, address: str, password: str) -> dict[str, Any]:
        """Try the address and password on the provider's server first; only a mailbox that lets us in is saved."""
        p = PROVIDERS.get(provider)
        if p is None:
            raise MailError(t("This mail service is not supported."))
        address, password = address.strip(), password.replace(" ", "").strip() if provider == "gmail" else password.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address) or len(address) > 200:
            raise MailError(t("Please check the email address."))
        if not password or len(password) > 200 or any(c in password for c in "\r\n\x00"):
            raise MailError(t("Please check the password."))
        try:
            await asyncio.wait_for(asyncio.to_thread(self._login_sync, p, address, password), TIMEOUT + 5)
        except imaplib.IMAP4.error:
            hint = (t("Gmail needs an app password, not your usual password.") if provider == "gmail"
                    else t("Please check that IMAP is turned on and whether an app password is needed."))
            raise MailError(t("The sign-in was refused. Nothing was saved.") + " " + hint) from None
        except (OSError, asyncio.TimeoutError):
            raise MailError(t("Could not reach the {name} server. Please try again in a moment.", name=t(p.name))) from None
        name = item_name(provider)
        self.store.delete_credential(name)
        self.store.save_credential(name, f"{t(p.name)} · {address}", [p.imap, p.smtp], {"type": "mailbox"}, "", kind="email")
        self.store.set_credential_secret(name, json.dumps({"u": address, "p": password}))
        self.store.set_credential_result(name, "ok", "signed in when it was added")
        self.store.add_vault_log(name, "saved", "mailbox, checked with a sign-in")
        return {"name": name, "provider": provider, "label": f"{t(p.name)} · {address}", "address": address}

    async def connect_oauth(self, provider: str, address: str, tok: dict[str, Any]) -> dict[str, Any]:
        """A mailbox signed in on the provider's own page. Like the password path, it is tried once before it is saved."""
        p = PROVIDERS[provider]
        if not tok.get("refresh_token") or not tok.get("access_token") or not address:
            raise MailError(t("Google did not grant mail access. Connect again and allow Gmail access."))
        try:
            await asyncio.wait_for(asyncio.to_thread(self._signin_check, p, address, XOAuth2(str(tok["access_token"]))), TIMEOUT + 5)
        except imaplib.IMAP4.error:
            raise MailError(t("Gmail did not accept this sign-in. Nothing was saved.")) from None
        except (OSError, asyncio.TimeoutError):
            raise MailError(t("Could not reach the {name} server. Please try again in a moment.", name=t(p.name))) from None
        name = item_name(provider)
        self.store.delete_credential(name)
        self.store.save_credential(name, f"{t(p.name)} · {address}", [p.imap, p.smtp], {"type": "mailbox", "via": "oauth"}, "", kind="email")
        self.store.set_credential_secret(name, json.dumps({"u": address, "oauth": {
            "access_token": str(tok["access_token"]), "refresh_token": str(tok["refresh_token"]),
            "expires_at": time.time() + float(tok.get("expires_in") or 3600)}}))
        self.store.set_credential_result(name, "ok", "signed in when it was added")
        self.store.add_vault_log(name, "saved", "mailbox, Google sign-in, checked")
        return {"name": name, "provider": provider, "label": f"{t(p.name)} · {address}", "address": address}

    def _signin_check(self, p: MailProvider, user: str, secret: str | XOAuth2) -> None:
        conn = self.imap(p.imap)
        try:
            _imap_signin(conn, user, secret)
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    async def _fresh(self, name: str, data: dict[str, Any]) -> XOAuth2:
        o = data["oauth"]
        if time.time() < float(o.get("expires_at") or 0) - 60:
            return XOAuth2(o["access_token"])
        if self.renew is None:
            raise MailError("the Google sign-in ran out and this server cannot renew it; ask the user to connect Gmail again")
        try:
            tok = await self.renew(o["refresh_token"])
        except Exception as e:  # noqa: BLE001 - reported as a reconnect request
            self.store.set_credential_result(name, "rejected", "Google did not renew the sign-in")
            raise MailError(f"Google did not renew the sign-in ({str(e)[:80]}); ask the user to connect Gmail again in the Connectors tab") from None
        if not tok.get("access_token"):
            self.store.set_credential_result(name, "rejected", "Google did not renew the sign-in")
            raise MailError("Google did not renew the sign-in; ask the user to connect Gmail again in the Connectors tab")
        o = {**o, "access_token": str(tok["access_token"]), "expires_at": time.time() + float(tok.get("expires_in") or 3600),
             **({"refresh_token": str(tok["refresh_token"])} if tok.get("refresh_token") else {})}
        self.store.set_credential_secret(name, json.dumps({**data, "oauth": o}))
        return XOAuth2(o["access_token"])

    def _open(self, account: str, tool: str, detail: str = "") -> tuple[MailProvider, str, str, str]:
        from .vault import use_credential
        mine = self.accounts()
        if not mine:
            raise VaultError("no mailbox is connected. Ask the user to connect one with connect_service(service='gmail').")
        name = account.strip() or (mine[0]["name"] if len(mine) == 1 else "")
        if not name:
            raise VaultError("several mailboxes are connected; say which with account: " + ", ".join(a["name"] for a in mine))
        if not name.startswith("mail-"):
            name = item_name(name)
        cred = use_credential(self.store, name, tool=tool, kind="email", detail=detail)
        data = json.loads(cred["secret"])
        return PROVIDERS[name.removeprefix("mail-")], name, data["u"], data.get("p") or json.dumps(data)

    async def _signin(self, account: str, tool: str, detail: str = "") -> tuple[MailProvider, str, str, str | XOAuth2]:
        p, name, user, secret = self._open(account, tool, detail)
        if secret.startswith("{"):
            return p, name, user, await self._fresh(name, json.loads(secret))
        return p, name, user, secret

    # ---- reading
    def _list_sync(self, p: MailProvider, user: str, pw: str, folder: str, query: str, limit: int, unread: bool,
                   skip_bulk: bool = False) -> list[dict[str, Any]]:
        conn = self.imap(p.imap)
        try:
            _imap_signin(conn, user, pw)
            typ, _ = conn.select(_quote(mutf7_encode(folder)), readonly=True)
            if typ != "OK":
                raise MailError(f"there is no folder {folder!r}")
            if skip_bulk and p.id == "gmail":   # Gmail's own sorting first: promotions, social and forums are left out by the search
                query = f"{query} {GMAIL_NOT_PERSONAL}".strip()
            want = limit
            if skip_bulk:
                limit = min(30, limit * 3)   # some of the newest may be bulk mail: look at a few more, keep `want`
            uids = _search(conn, p.id == "gmail", query, ["UNSEEN"] if unread else [])[-limit:][::-1]
            out = []
            if uids:
                fields = " ".join(("FROM", "SUBJECT", "DATE", *BULK_HEADERS))
                typ, rows = conn.uid("FETCH", b",".join(uids).decode(), f"(UID FLAGS BODY.PEEK[HEADER.FIELDS ({fields})])")
                for row in rows or []:
                    if not isinstance(row, tuple):
                        continue
                    meta = row[0].decode(errors="replace")
                    uid = re.search(r"UID (\d+)", meta)
                    head = email.message_from_bytes(row[1], policy=email.policy.default)
                    out.append({"uid": uid.group(1) if uid else "", "from": str(head.get("from", "")), "subject": str(head.get("subject", "")),
                                "date": str(head.get("date", "")), "unread": "\\Seen" not in meta, "bulk": is_bulk(head)})
            order = {u.decode(): i for i, u in enumerate(uids)}
            out = sorted(out, key=lambda m: order.get(m["uid"], 999))
            return [m for m in out if not m["bulk"]][:want] if skip_bulk else out
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _read_sync(self, p: MailProvider, user: str, pw: str, folder: str, uid: str) -> dict[str, Any]:
        conn = self.imap(p.imap)
        try:
            _imap_signin(conn, user, pw)
            conn.select(_quote(mutf7_encode(folder)), readonly=True)
            typ, rows = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((r[1] for r in rows or [] if isinstance(r, tuple)), None)
            if typ != "OK" or raw is None:
                raise MailError(f"no message with uid {uid} in {folder}")
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            files = [part.get_filename() for part in msg.walk() if part.get_content_disposition() == "attachment" and part.get_filename()]
            return {"uid": uid, "from": str(msg.get("from", "")), "to": str(msg.get("to", "")), "cc": str(msg.get("cc", "")),
                    "subject": str(msg.get("subject", "")), "date": str(msg.get("date", "")), "message_id": str(msg.get("message-id", "")),
                    "references": str(msg.get("references", "")), "text": _text_of(msg), "attachments": files}
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    async def _run(self, fn: Callable[..., Any], *args: Any, wait: float = TIMEOUT + 5) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), wait)
        except imaplib.IMAP4.error:
            raise MailError("the mail server refused the sign-in; ask the user to connect the mailbox again in the Connectors tab") from None
        except smtplib.SMTPAuthenticationError:
            raise MailError("the mail server refused the sign-in for sending; ask the user to connect the mailbox again") from None
        except (OSError, asyncio.TimeoutError, smtplib.SMTPException) as e:
            raise MailError(f"the mail server could not be reached ({type(e).__name__}); nothing was repeated") from None

    async def list(self, account: str = "", folder: str = "INBOX", query: str = "", limit: int = 10, unread: bool = False,
                   skip_bulk: bool = False) -> list[dict[str, Any]]:
        """Newest first: [{"uid", "from", "subject", "date", "unread", "bulk"}]. `skip_bulk`: leave out list, bulk and automated mail
        (is_bulk) and, on Gmail, its promotions, social and forums categories (the briefing and the heads-ups)."""
        p, _, user, pw = await self._signin(account, "email_list", f"{folder} {query}".strip())
        return await self._run(self._list_sync, p, user, pw, folder or "INBOX", query, max(1, min(int(limit or 10), 30)), unread, skip_bulk)

    # ---- what arrived since the last look (proactive.py)
    def _arrivals_sync(self, p: MailProvider, user: str, pw: str | XOAuth2, kind: str, cursor: dict[str, Any] | None,
                       now: float) -> dict[str, Any] | None:
        """Every message that came in after `cursor` ({"v": UIDVALIDITY, "uid": the last UID looked at, "t": time}), oldest first, with a
        short text snippet; the new cursor with them. kind "inbox": unread, not bulk (Gmail's promotions/social/forums left out);
        "promo": Gmail's promotions/social/forums categories, unread; "junk": the spam folder (its \\Junk mark, else a usual name), read or
        not. No cursor (a mailbox seen for the first time): nothing, and a cursor at "now". None when the provider has no such folder."""
        conn = self.imap(p.imap)
        try:
            _imap_signin(conn, user, pw)
            gmail = p.id == "gmail"
            raw = "INBOX"
            if kind == "junk":
                found = _special(_folders(conn), "\\junk", JUNK_NAMES)
                if found is None:
                    return None
                raw = found.raw
            elif kind == "promo" and not gmail:
                return None
            typ, _ = conn.select(_quote(raw), readonly=True)
            if typ != "OK":
                if kind == "junk":
                    return None
                raise MailError("the inbox could not be opened")
            validity, nxt = _uid_state(conn)
            hi = nxt - 1 if nxt else None
            if cursor is None or not isinstance(cursor.get("uid"), int):
                top = hi if hi is not None else max([int(u) for u in _search(conn, False, "", [])] or [0])
                return {"rows": [], "cursor": {"v": validity, "uid": top, "t": now}}
            last, same = int(cursor["uid"]), cursor.get("v") == validity
            query = {"inbox": GMAIL_NOT_PERSONAL if gmail else "", "promo": "{category:promotions category:social category:forums}",
                     "junk": ""}[kind]
            crit = ["UNSEEN"] if kind != "junk" else []
            if same:
                if hi is not None and hi <= last:
                    return {"rows": [], "cursor": {**cursor, "t": now}}
                crit += ["UID", f"{last + 1}:{hi if hi is not None else '*'}"]
            else:   # the server renumbered the folder: by date instead (a day early; filtered below by when each one arrived)
                crit += ["SINCE", time.strftime("%d-%b-%Y", time.gmtime(float(cursor.get("t") or now) - 86400))]
            uids = sorted(int(u) for u in _search(conn, gmail, query, crit))
            if same:
                uids = [u for u in uids if u > last and (hi is None or u <= hi)]
            rows = self._arrival_rows(conn, uids, kind)
            if not same:
                since = float(cursor.get("t") or 0)
                rows = [r for r in rows if not r["arrived"] or r["arrived"] >= since]
            new = hi if hi is not None else max([last, *uids])
            if not same and hi is None:
                new = max([int(u) for u in _search(conn, False, "", [])] or [0])
            return {"rows": rows, "cursor": {"v": validity, "uid": new, "t": now}}
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _arrival_rows(conn: Any, uids: list[int], kind: str) -> list[dict[str, Any]]:
        fields = " ".join(("FROM", "SUBJECT", "DATE", "CONTENT-TYPE", "CONTENT-TRANSFER-ENCODING", "MIME-VERSION", *BULK_HEADERS))
        spec = f"(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS ({fields})] BODY.PEEK[TEXT]<0.{SNIPPET_BYTES}>)"
        out: dict[int, dict[str, Any]] = {}
        for which, _n in uid_sets(uids, size=FETCH_CHUNK):
            typ, rows = conn.uid("FETCH", which, spec)
            if typ != "OK":
                raise MailError("the mail server did not give the new messages")
            cur: dict[str, Any] | None = None
            for row in rows or []:
                meta = (row[0] if isinstance(row, tuple) else row)
                meta = meta.decode(errors="replace") if isinstance(meta, bytes) else str(meta or "")
                m = re.search(r"UID (\d+)", meta)
                if m and (isinstance(row, tuple) or cur is None or int(m.group(1)) != cur["uid"]):
                    cur = {"uid": int(m.group(1)), "meta": "", "head": b"", "text": b""}
                    out[cur["uid"]] = cur
                if cur is None:
                    continue
                cur["meta"] += " " + meta
                if isinstance(row, tuple):
                    which_part = "head" if "HEADER.FIELDS" in meta else "text"
                    cur[which_part] = row[1] if isinstance(row[1], bytes) else b""
        result = []
        for uid in sorted(out):
            r = out[uid]
            msg = email.message_from_bytes(r["head"].rstrip() + b"\r\n\r\n" + r["text"], policy=email.policy.default)
            bulk = is_bulk(msg)
            if kind == "inbox" and bulk:
                continue
            if kind != "junk" and "\\Seen" in r["meta"]:
                continue
            try:
                snippet = " ".join(_text_of(msg).split())[:SNIPPET_CHARS]
            except Exception:  # noqa: BLE001 - a body that does not decode gives no snippet, never an error
                snippet = ""
            result.append({"uid": str(uid), "from": str(msg.get("from", "")), "subject": str(msg.get("subject", "")),
                           "date": str(msg.get("date", "")), "unread": "\\Seen" not in r["meta"], "bulk": bulk,
                           "arrived": _arrived(r["meta"].encode()), "snippet": snippet})
        return result

    async def arrivals(self, account: str, cursor: dict[str, Any] | None, kind: str = "inbox") -> dict[str, Any] | None:
        """{"rows": [...oldest first], "cursor": {...}} of what came in after `cursor` (see _arrivals_sync); raises MailError when the
        mailbox can not be read (the caller keeps its old cursor)."""
        p, _, user, pw = await self._signin(account, "proactive", f"new mail ({kind})")
        return await self._run(self._arrivals_sync, p, user, pw, kind, cursor, time.time(), wait=ARRIVALS_WAIT)

    def _recent_sync(self, p: MailProvider, user: str, pw: str | XOAuth2, since: float, limit: int) -> list[dict[str, Any]]:
        """The newest messages of INBOX that arrived after `since` (unix time), with their text. Nothing is marked as read."""
        conn = self.imap(p.imap)
        try:
            _imap_signin(conn, user, pw)
            conn.select(_quote("INBOX"), readonly=True)
            day = time.strftime("%d-%b-%Y", time.gmtime(since - 86400))   # IMAP SINCE counts whole days: a day early, filtered below
            typ, data = conn.uid("SEARCH", "SINCE", day)
            if typ != "OK":
                return []
            out = []
            for uid in (data[0] or b"").split()[-limit:][::-1]:
                typ, rows = conn.uid("FETCH", uid.decode(), "(INTERNALDATE BODY.PEEK[])")
                got = next((r for r in rows or [] if isinstance(r, tuple)), None)
                if typ != "OK" or got is None or got[1] is None:
                    continue
                raw = got[1]
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                try:
                    when = email.utils.parsedate_to_datetime(str(msg.get("date", "")))
                    at = (when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)).timestamp()   # "-0000" = UTC, unsaid
                except (TypeError, ValueError, IndexError):
                    at = 0.0
                arrived = _arrived(got[0])
                if (arrived or at) and (arrived or at) < since:
                    continue
                out.append({"from": str(msg.get("from", "")), "subject": str(msg.get("subject", "")), "at": at, "arrived": arrived,
                            "text": _text_of(msg)[:6000], **_sender_headers(msg)})
            return out
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    async def recent(self, minutes: float = 15, limit: int = 12, tool: str = "find_login_code") -> list[dict[str, Any]]:
        """Messages of the last `minutes` from every connected mailbox, newest first: {"from", "subject", "at" (its Date), "arrived" (when
        the mail server got it, 0 if unknown), "text", "dkim", "return_path", "from_failed", "account"}."""
        since = time.time() - minutes * 60
        out: list[dict[str, Any]] = []
        for a in self.accounts():
            p, _, user, pw = await self._signin(a["name"], tool, f"messages of the last {int(minutes)} minutes")
            for m in await self._run(self._recent_sync, p, user, pw, since, max(1, min(limit, 30))):
                out.append({**m, "account": a["name"]})
        return sorted(out, key=lambda m: -(m.get("arrived") or m["at"] or 0))

    async def read(self, account: str, uid: str, folder: str = "INBOX") -> dict[str, Any]:
        if not re.fullmatch(r"\d{1,12}", uid or ""):
            raise MailError("uid must be the number email_list gave")
        p, _, user, pw = await self._signin(account, "email_read", f"{folder} {uid}")
        return await self._run(self._read_sync, p, user, pw, folder or "INBOX", uid)

    # ---- changing messages: flags and folders. Nothing is ever deleted for good (no EXPUNGE, no \Deleted): "trash" moves to the Trash.
    def _prepare(self, conn: Any, action: str, sel: Selection, to: str) -> tuple[set[str], bool, Folder, Folder | None]:
        """After the sign-in: (capabilities, Gmail extensions?, the folder the messages are in, where they go or None)."""
        caps = _caps(conn)
        gm = "X-GM-EXT-1" in caps
        folders = _folders(conn)
        names = ", ".join(f.name for f in folders if "\\noselect" not in f.flags)[:600]
        src = _find_folder(folders, sel.folder)
        if src is None:
            raise MailError(f"there is no folder {sel.folder!r}; the folders are: {names}")
        dest: Folder | None = None
        if action == "trash":
            dest = _special(folders, "\\trash", TRASH_NAMES)
            if dest is None:
                raise MailError("this mailbox has no Trash folder, so nothing was moved (messages are never deleted for good)")
        elif action == "archive" and not gm:
            dest = _special(folders, "\\archive", ARCHIVE_NAMES)
            if dest is None:
                raise MailError("this mailbox has no Archive folder, so archiving is not available here; moving them to a folder "
                                f"works instead. The folders are: {names}")
        elif action == "move":
            dest = _find_folder(folders, to) if to.strip() else None
            if dest is None:
                raise MailError(f"there is no folder or label {to!r} (none is created); the folders are: {names}")
        if dest is not None and dest.raw == src.raw:
            raise MailError(f"these messages are already in {dest.name}")
        if dest is not None and not self._labels_only(gm, src, action) and "MOVE" not in caps:
            raise MailError("this mail server cannot move messages (no IMAP MOVE), so nothing was moved; marking read, unread and "
                            "flags works here")
        return caps, gm, src, dest

    @staticmethod
    def _labels_only(gm: bool, src: Folder, action: str) -> bool:
        """Gmail's All Mail holds every message: a move from there adds the label instead (nothing can leave All Mail but to Trash)."""
        return gm and action == "move" and "\\all" in src.flags

    @staticmethod
    def _todo(conn: Any, gm: bool, action: str, sel: Selection, src: Folder) -> tuple[list[int], int]:
        """The UIDs to change, and how many of the chosen ones were already that way (only counted for messages chosen by uid)."""
        state = {"read": ["UNSEEN"], "unread": ["SEEN"], "flag": ["UNFLAGGED"], "unflag": ["FLAGGED"]}.get(action, [])
        if action == "archive" and gm and src.raw.upper() != "INBOX":
            state = ["X-GM-LABELS", "\\Inbox"]   # archiving on Gmail = taking the Inbox label off; only those that have it count
        todo = [int(u) for u in _search(conn, gm, sel.query, sel.criteria() + state) if u.isdigit()]
        already = len(_search(conn, gm, sel.query, sel.criteria())) - len(todo) if sel.uids and state else 0
        return todo, max(0, already)

    def _plan_sync(self, p: MailProvider, user: str, pw: str | XOAuth2, action: str, sel: Selection, to: str) -> dict[str, Any]:
        """What a change would touch, without changing anything (the trash card shows it): the UIDs, the folder, a few subjects."""
        conn = self.imap(p.imap)
        try:
            _imap_signin(conn, user, pw)
            try:
                _, gm, src, dest = self._prepare(conn, action, sel, to)
                typ, _ = conn.select(_quote(src.raw), readonly=True)
                if typ != "OK":
                    raise MailError(f"could not open the folder {src.name!r}")
                todo, _ = self._todo(conn, gm, action, sel, src)
                subjects: list[str] = []
                if 0 < len(todo) <= 5:
                    typ, rows = conn.uid("FETCH", ",".join(map(str, todo)), "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                    for row in rows or []:
                        if isinstance(row, tuple) and isinstance(row[1], bytes):
                            subj = str(email.message_from_bytes(row[1], policy=email.policy.default).get("subject", "")).strip()
                            subjects.append(" ".join(subj.split())[:60])
            except imaplib.IMAP4.error as e:   # signed in: a command the server refused, not the sign-in
                raise MailError(f"the mail server refused this ({str(e)[:160]})") from None
            return {"uids": todo, "folder": src.name, "dest": dest.name if dest else "", "subjects": subjects}
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def _update_sync(self, p: MailProvider, user: str, pw: str | XOAuth2, action: str, sel: Selection, to: str,
                     approved: list[int] | None) -> dict[str, Any]:
        conn = self.imap(p.imap)
        try:
            _imap_signin(conn, user, pw)
            try:
                _, gm, src, dest = self._prepare(conn, action, sel, to)
                typ, _ = conn.select(_quote(src.raw))   # read-write: this is the one place a mailbox is changed
                if typ != "OK":
                    raise MailError(f"could not open the folder {src.name!r}")
                todo, already = self._todo(conn, gm, action, sel, src)
            except imaplib.IMAP4.error as e:
                raise MailError(f"the mail server refused this ({str(e)[:160]})") from None
            if approved is not None:   # the trash card showed these: never more than the user saw (new mail since then stays)
                todo = sorted(set(todo) & set(approved))
            flags = {"read": ("+", "\\Seen"), "unread": ("-", "\\Seen"), "flag": ("+", "\\Flagged"), "unflag": ("-", "\\Flagged")}
            done, stopped = 0, ""
            for chunk, n in uid_sets(todo):
                try:
                    if action in flags:
                        sign, flag = flags[action]
                        typ, data = conn.uid("STORE", chunk, f"{sign}FLAGS.SILENT", f"({flag})")
                    elif action == "archive" and gm:
                        typ, data = conn.uid("STORE", chunk, "-X-GM-LABELS", "(\\Inbox)")
                    elif dest is not None and self._labels_only(gm, src, action):
                        label = "\\Inbox" if dest.raw.upper() == "INBOX" else _quote(dest.raw)
                        typ, data = conn.uid("STORE", chunk, "+X-GM-LABELS", f"({label})")
                    elif dest is not None:
                        typ, data = conn.uid("MOVE", chunk, _quote(dest.raw))
                    else:
                        raise MailError(f"unknown action {action!r}")
                except imaplib.IMAP4.error as e:
                    stopped = str(e)[:160]
                    break
                if typ != "OK":
                    stopped = b" ".join(d for d in (data or []) if isinstance(d, bytes)).decode(errors="replace")[:160] or str(typ)
                    break
                done += n
            return {"done": done, "total": len(todo), "already": already, "folder": src.name, "dest": dest.name if dest else "",
                    "gmail": gm, "labels_only": self._labels_only(gm, src, action), "stopped": stopped}
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    async def plan(self, account: str, action: str, sel: Selection, to: str = "") -> dict[str, Any]:
        """What `update` would change (nothing is changed): {"uids", "folder", "dest", "subjects", "account"}."""
        if action not in ACTIONS:
            raise MailError(f"action must be one of {', '.join(ACTIONS)}")
        p, name, user, pw = await self._signin(account, "email_update", f"{action} {sel.folder} (count)")
        got = await self._run(self._plan_sync, p, user, pw, action, sel, to)
        return {**got, "account": name}

    async def update(self, account: str, action: str, sel: Selection, to: str = "", approved: list[int] | None = None) -> dict[str, Any]:
        """Mark read/unread, flag/unflag, archive, move or trash the chosen messages, many per command. Returns {"done", "total",
        "already", "folder", "dest", "gmail", "labels_only", "stopped"}."""
        if action not in ACTIONS:
            raise MailError(f"action must be one of {', '.join(ACTIONS)}")
        p, name, user, pw = await self._signin(account, "email_update", f"{action} {sel.folder} {sel.query}".strip())
        got = await self._run(self._update_sync, p, user, pw, action, sel, to, approved, wait=UPDATE_TIMEOUT)
        if got["done"]:
            self.store.add_vault_log(name, "used", f"{action}: {got['done']} message(s)")
        return got

    # ---- sending
    def _send_sync(self, p: MailProvider, user: str, pw: str | XOAuth2, msg: EmailMessage) -> None:
        conn = self.smtp(p.smtp, p.smtp_port)
        try:
            _smtp_signin(conn, user, pw)
            conn.send_message(msg)
        finally:
            try:
                conn.quit()
            except Exception:  # noqa: BLE001
                pass

    async def send(self, account: str, to: list[str], subject: str, body: str, cc: list[str] | None = None, reply_to_uid: str = "",
                   folder: str = "INBOX", reply_to: str = "") -> str:
        """`reply_to`: an address answers should go to instead of the sender (the beta mails: support@kelo.chat)."""
        people = [a for _, a in getaddresses([", ".join(to)] + ([", ".join(cc)] if cc else []))]
        if not to or not all(re.fullmatch(r"[^@\s,<>]+@[^@\s,<>]+\.[^@\s,<>]+", a) for a in people):
            raise MailError("every recipient must be a plain email address")
        if len(people) > 20:
            raise MailError("at most 20 recipients")
        p, name, user, pw = await self._signin(account, "email_send", f"to {len(people)} recipient(s)")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), subject[:300]
        if cc:
            msg["Cc"] = ", ".join(cc)
        if reply_to and re.fullmatch(r"[^@\s,<>]+@[^@\s,<>]+\.[^@\s,<>]+", reply_to):
            msg["Reply-To"] = reply_to
        msg["Message-ID"] = make_msgid(domain=parseaddr(user)[1].split("@")[-1] or None)
        if reply_to_uid:
            orig = await self.read(account or name, reply_to_uid, folder)
            if orig["message_id"]:
                msg["In-Reply-To"] = orig["message_id"]
                msg["References"] = (orig["references"] + " " + orig["message_id"]).strip()
        msg.set_content(body[:50_000])
        await self._run(self._send_sync, p, user, pw, msg)
        self.store.add_vault_log(name, "used", f"email sent to {len(people)} recipient(s)")
        return f"sent to {', '.join(people)}"
