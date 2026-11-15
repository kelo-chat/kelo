"""Watchers: cheap condition checks that wake the agent only when the condition holds.

A watcher looks at one thing every `every_minutes` (at least 5; 2 while it is ready to buy, below) without a model call:

  kind "page"    a public https page, fetched by the server (public addresses only, at most 3 redirects, each hop checked again; the
                 rules of netguard.py) or, with use_browser, opened in a tab of the agent's browser for pages that need JavaScript.
                 mode  text_appears / text_disappears   `match` (words, case-insensitive) is / is no longer on the page
                       number_above / number_below       the number found by `selector` (CSS, a small subset) or `regex` (its first
                                                         group) is above / below `threshold`
                       changed                           the page text (or the part `selector` / `regex` picks) changed
  
How it looks (`level`, cheapest first; the watcher keeps the level that works):
  light   the server asks for the page itself, without cookies, as the agent browser's own Chrome identity (its user agent, the owner's
          language) and through the VPN route when one is on
  tab     the same request made INSIDE the agent's browser (one background tab per site, `fetch` with the owner's sign-ins; cookies never
          leave the browser; same origin only), through the browser's own route
  page    a full page load in a tab of the agent's browser (JavaScript runs); the real status and the browser's bot-check reading
A watcher starts at `tab` with signed_in or method POST, at `page` with use_browser, else at `light`. A look that is blocked (HTTP 401 / 403 /
429 / 503, or a bot-check page) is repeated at once one level up; the level that worked is kept for 24 h. A block a higher level got past
is not an error. A human check that the browser itself cannot pass pauses nothing and counts no error: the next checks come twice, then four
times as late, and the owner is told once a day to open the page in Kelo's browser view. No solving service, ever. The next check is
`every_minutes` x a random 0.8..1.2 (never under 5 minutes). A hit is confirmed by one more look 30 s later; only then it fires.
A watcher that is READY TO BUY (it has a purchase permission with count and money left, buy_ready.py, docs/instant-buy.md) may look
every 2 minutes (ARMED_MIN_MINUTES) and its hit fires at once, without the 30 s look: at the hit a short buying run starts on its parked tab.
Internal APIs: `method` GET|POST, `body` (POST, at most 4 KB), `headers` (at most 8: Accept, Content-Type, X-Requested-With, Referer, Origin or
X-...; never Cookie or Authorization), `signed_in`. A watcher only reads: a POST whose address or body names an action (order, pay, book,
cancel, ...) is refused.

kind "script"  a short Python snippet run in the work room (no network, no secrets): it gets the fetched page (when `url` is set) as
                 page.html and page.txt in its folder and prints a last line of JSON {"hit": true|false, "value": "..."}.

Each watcher starts off. A dry run (`watch_dry_run`, POST /api/watchers/{id}/dry-run) checks it once and shows what it found; the agent
shows that to the user, and only a watcher with a successful dry run since its condition last changed (and within DRY_RUN_FRESH) can be
turned on (`watch_enable`, which the user approves). The dry run's finding is the starting point: the watcher fires on the next change
into the condition (text that appears, a number that crosses the line, any change), not again while the condition simply stays true.

When it fires: a notification (watch_fired, with a push) and a hidden handoff to the main agent (like a background job's report, never
shown as the user's words), which tells the user in a short message. `once` turns the watcher off after its first hit.
MAX_ERRORS failed checks in a row turn it off with a notification (watch_disabled).

WATCHERS API (contracts also next to serialize() in server.py):
Watcher: {"id", "name", "kind": page|script, "url", "mode", "match", "selector", "regex", "threshold", "use_browser", "signed_in", "method"
  (GET|POST), "body", "headers" ({name: value} or null), "level" (light|tab|page, the raised level now in force, else null), "level_until"
  (when that ends), "code" (script only, else null), "note" (what to do when it fires), "every_minutes", "enabled", "once", "state": off|watching|error|done,
  "description" (Korean words), "last_value", "last_checked_at", "next_check_at", "last_fired_at", "fire_count", "error_count",
  "last_error", "disabled_reason", "dry_run": DryRun|null, "can_enable": bool, "created_at", "updated_at", "created_by": user|agent,
  "buy": null | {"armed": true, "tab": "buy:<id>", "last_kept_at", "signed_in": bool|null, "problems": [{"code", "text"}], "remembered": bool}
  (ready to buy: a purchase permission with count and money left; problem codes card_needs_phone, no_card, no_shipping, signed_out, cap_used, browser_off)}
DryRun: {"at", "ok", "satisfied", "value", "detail" (Korean line), "error", "url", "title", "level": light|tab|page|null (how it looked)}
GET    /api/watchers                    {"items": [Watcher], "limit": MAX_WATCHERS}
POST   /api/watchers                    201 Watcher (always off). 422 {"detail"} for a bad url / mode / selector / regex / interval
GET/PATCH/DELETE /api/watchers/{id}     PATCH: any POST field; changing what is checked turns it off until a new dry run
POST   /api/watchers/{id}/buy-check     the ready-to-buy keep-alive now -> the `buy` object; 404 when the watcher is not ready to buy
POST   /api/watchers/{id}/dry-run       {"result": DryRun, "watcher": Watcher}
POST   /api/watchers/{id}/enable        {"enabled": bool} -> Watcher; 409 {"detail"} when turning on without a fresh successful dry run
GET    /api/watchers/{id}/events        {"items": [{"id", "at", "kind": fired|error|dry_run|enabled|disabled, "detail", "value"}]}
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote_plus, urljoin, urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import Ours, Tool
from .agent.fence import data, fence
from .browser_identity import user_agent as chrome_user_agent
from .i18n import N_, t
from .netguard import VPN_DOWN, NetGuardError, resolve_public, valid_host
from .tools.browser import site_tab_key
from .tools.challenge import detect

log = logging.getLogger("uvicorn.error")

MODES = ("text_appears", "text_disappears", "number_above", "number_below", "changed")
KINDS = ("page", "script")
MIN_EVERY_MINUTES, DEFAULT_EVERY_MINUTES, MAX_EVERY_MINUTES = 5, 60, 7 * 24 * 60
ARMED_MIN_MINUTES = 2          # a watcher that is ready to buy (buy_ready.py) may look this often
MAX_WATCHERS = 30
MAX_ERRORS = 5                 # failed checks in a row before a watcher turns itself off
DRY_RUN_FRESH = 24 * 3600.0    # a dry run older than this does not count for turning a watcher on
MAX_PAGE = 2_000_000           # bytes read from a page
MAX_TEXT = 300_000             # characters of page text a condition looks at
MAX_REDIRECTS = 3
FETCH_TIMEOUT = 20.0
MAX_CODE = 10_000
SCRIPT_TIMEOUT = 30
CONCURRENT_CHECKS = 3
KEEP_EVENTS = 50
WATCH_MARK = "[Handoff from a watcher, not from the user]"   # leads the hidden message; GET /api/messages leaves these out
USER_AGENT = "Mozilla/5.0 (compatible; KeloWatcher/1.0)"   # only when there is no browser to take the identity from
LEVELS = ("light", "tab", "page")
BLOCK_STATUS = (401, 403, 429, 503)
LEVEL_KEEP = 24 * 3600.0       # a level that got past a block is kept this long
CONFIRM_DELAY = 30.0           # a hit is looked at once more this many seconds later
SPREAD = (0.8, 1.2)            # the next check comes after every_minutes x a random factor in this range
MAX_BACKOFF = 4                # a human check doubles the wait between checks, up to this many times the normal one
HUMAN_NOTICE_EVERY = 24 * 3600.0
MAX_BODY, MAX_HEADERS, MAX_HEADER_VALUE = 4096, 8, 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'page', url TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT '', match TEXT NOT NULL DEFAULT '', selector TEXT NOT NULL DEFAULT '', regex TEXT NOT NULL DEFAULT '',
    threshold REAL, use_browser INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    every_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, once INTEGER NOT NULL DEFAULT 0,
    last_value TEXT, last_hit INTEGER, last_checked_at REAL, next_check_at REAL, last_fired_at REAL,
    fire_count INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
    dry_run TEXT NOT NULL DEFAULT '', dry_run_at REAL, config_at REAL NOT NULL, disabled_reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, created_by TEXT NOT NULL DEFAULT 'user');
CREATE TABLE IF NOT EXISTS watch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, watch_id INTEGER NOT NULL, at REAL NOT NULL, kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', value TEXT);
CREATE INDEX IF NOT EXISTS watch_events_by_watch ON watch_events(watch_id, id);
"""
CONDITION_FIELDS = ("kind", "url", "mode", "match", "selector", "regex", "threshold", "use_browser", "code", "signed_in", "method", "body",
                    "headers")
# columns added after the first release (CREATE TABLE above has the first ones): added to an older database when the module starts
NEW_COLUMNS = (("signed_in", "INTEGER NOT NULL DEFAULT 0"), ("method", "TEXT NOT NULL DEFAULT 'GET'"), ("body", "TEXT NOT NULL DEFAULT ''"),
               ("headers", "TEXT NOT NULL DEFAULT ''"), ("level", "TEXT NOT NULL DEFAULT ''"), ("level_until", "REAL"),
               ("backoff", "INTEGER NOT NULL DEFAULT 1"), ("human_notice_at", "REAL"), ("confirming", "INTEGER NOT NULL DEFAULT 0"))

ACTION_WORDS = frozenset("order pay payment checkout purchase buy book booking reserve reservation cancel delete remove apply submit send "
                         "confirm".split())
ACTION_KO = ("결제", "주문", "구매", "예약", "예매", "신청", "취소", "삭제", "발송")
READ_KO = ("조회", "검색", "목록")
HEADER_OK = frozenset({"accept", "content-type", "x-requested-with", "referer", "origin"})
HEADER_NEVER = frozenset({"cookie", "authorization", "proxy-authorization", "x-http-method-override", "x-method-override"})
CHECK_MARKS = ("challenges.cloudflare.com", "_cf_chl_opt", "cf-browser-verification", "captcha-delivery.com", "px-captcha")


class WatchError(ValueError):
    """A watcher setting we refuse, or a check that could not be done. The message is safe to show."""


# ---------------------------------------------------------------------------------------------------------------- fetching
@dataclass(slots=True)
class Page:
    url: str
    status: int
    content_type: str
    body: str
    headers: dict[str, str] = field(default_factory=dict)   # the response headers a bot check shows itself in (lower-case names)
    challenge: Any = None                                    # page level: the bot check the browser's own reading found (tools/challenge.py)


def _charset(content_type: str, head: bytes) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", head[:4096], re.I)
    if m:
        name = m.group(1) if isinstance(m.group(1), str) else m.group(1).decode("ascii", "ignore")
        try:
            "".encode(name)
            return name
        except LookupError:
            pass
    return "utf-8"


async def public_get(url: str, *, resolver: Callable[..., list] | None = None, transport: httpx.AsyncBaseTransport | None = None,
                     max_body: int = MAX_PAGE, method: str = "GET", data: dict[str, str] | None = None, user_agent: str = USER_AGENT,
                     language: str = "ko,en;q=0.8", allow_http: bool = False, timeout: float = FETCH_TIMEOUT,
                     proxy: Callable[[], str | None] | str | None = None, body: str | None = None,
                     extra_headers: dict[str, str] | None = None) -> Page:
    """GET (or POST a small form to) a public https page: no credentials, the standard port, a host name (no IP literal, no internal
    names) that resolves to public addresses only, the connection pinned to the address that was checked, at most MAX_REDIRECTS
    redirects (each checked the same way; a redirect after a POST is followed with GET), bounded time and size. allow_http: plain
    http:// on port 80 too (the agent's web_fetch; watchers stay https only).
    proxy: the VPN connector (vpn.py) is on (a socks5h:// address, or a function returning one or None): the page is fetched through it.
    The name is then NOT looked up here (the proxy resolves it: a local lookup would tell the local resolver every page read) and the
    connection is not pinned to a checked address; every other rule above stays, and the VPN's exit is outside the home network. If the
    proxy cannot be reached the read fails ("the VPN is not connected right now"); it is never done directly instead.
    body / extra_headers: a watcher's own request (a POST body as text; the headers its settings allow, never cookies)."""
    via = proxy() if callable(proxy) else proxy
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme != "https" and not (allow_http and scheme == "http"):
            raise NetGuardError("only public http(s):// pages can be read" if allow_http else "only public https:// pages can be watched")
        if parts.username or parts.password:
            raise NetGuardError("credentials in the URL are not allowed")
        if parts.port not in (None, 443 if scheme == "https" else 80):
            raise NetGuardError(f"only the standard {scheme} port is allowed")
        host = valid_host(parts.hostname or "")
        if via:
            target = f"{scheme}://{host}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
            ext: dict[str, Any] = {}
        else:
            ip = await resolve_public(host, resolver)
            target = f"{scheme}://{('[' + ip + ']') if ':' in ip else ip}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
            ext = {"sni_hostname": host} if scheme == "https" else {}
        headers = {"Host": host, "User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
                   "Accept-Language": language}
        if extra_headers:
            for k, v in extra_headers.items():
                if k.lower() not in ("host", "user-agent", "cookie", "authorization"):
                    for old in [h for h in headers if h.lower() == k.lower()]:
                        del headers[old]
                    headers[k] = v
        content = None
        if body is not None and method != "GET":
            content = body.encode()
            if not any(k.lower() == "content-type" for k in headers):
                headers["Content-Type"] = default_content_type(body)
        try:
            client_ctx = httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=timeout, trust_env=False,
                                           **({"proxy": via} if via else {}))
        except ImportError:   # socksio is not installed
            raise NetGuardError(VPN_DOWN) from None
        down = (httpx.ProxyError, httpx.ConnectError) if via else ()   # the VPN cannot be reached: refused, never read directly
        async with client_ctx as client:
            try:
                async with client.stream(method, target, headers=headers, data=data, content=content, extensions=ext) as r:
                    if 300 <= r.status_code < 400 and r.headers.get("location"):
                        url = urljoin(url, r.headers["location"])
                        method, data, body, extra_headers = "GET", None, None, None
                        continue
                    ctype = r.headers.get("content-type", "")
                    if ctype and not re.search(r"text/|json|xml|javascript", ctype, re.I):
                        raise NetGuardError(f"that address is not a text page ({ctype.split(';')[0]})")
                    chunks, size = [], 0
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        chunks.append(chunk)
                        if size >= max_body:
                            break
                    raw = b"".join(chunks)[:max_body]
                    seen = {k.lower(): v[:200] for k, v in r.headers.items()
                            if k.lower() in ("server", "cf-mitigated", "x-amzn-waf-action") or k.lower().startswith(("x-px", "x-datadome", "x-dd-"))}
                    return Page(url, r.status_code, ctype, raw.decode(_charset(ctype, raw), "replace"), seen)
            except httpx.TimeoutException:
                raise NetGuardError("the page did not answer in time") from None
            except down:
                raise NetGuardError(VPN_DOWN) from None
            except httpx.HTTPError as e:
                raise NetGuardError(f"the page could not be loaded ({type(e).__name__})") from None
    raise NetGuardError(f"more than {MAX_REDIRECTS} redirects")


def default_content_type(body: str) -> str:
    return "application/json" if body.lstrip()[:1] in ("{", "[") else "application/x-www-form-urlencoded"


# ---------------------------------------------------------------------------------------------------------------- what a watcher may send
def action_word(text: str) -> str | None:
    """The first word in `text` (a url path or a body) that names an action (order, book, cancel, 결제, ...), else None. Whole words only:
    words split at anything but letters and digits and at camelCase; a plural counts; a Korean run counts when it holds an action word and
    no reading word (조회, 검색, 목록)."""
    spread = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", unquote_plus(text or ""))
    for tok in re.findall(r"[A-Za-z]+|[가-힣]+", spread):
        low = tok.lower()
        if low.isascii():
            if low in ACTION_WORDS:
                return low
            if low.endswith("s") and low[:-1] in ACTION_WORDS:
                return low[:-1]
        elif not any(r in tok for r in READ_KO):
            for a in ACTION_KO:
                if a in tok:
                    return a
    return None


def clean_headers(raw: Any) -> dict[str, str]:
    """A watcher's own headers: at most MAX_HEADERS, only the names the allow-list gives, never Cookie or Authorization."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError:
            raise WatchError("headers must be an object of name: value") from None
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise WatchError("headers must be an object of name: value")
    if len(raw) > MAX_HEADERS:
        raise WatchError(f"at most {MAX_HEADERS} headers")
    out: dict[str, str] = {}
    for k, v in raw.items():
        name, low = str(k).strip(), str(k).strip().lower()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,60}", name):
            raise WatchError(f"header name {name[:30]!r} is not valid")
        if low in HEADER_NEVER:
            raise WatchError(f"a watcher never sends {name}: the browser's own sign-in is used with signed_in")
        if low not in HEADER_OK and not low.startswith("x-"):
            raise WatchError(f"header {name} is not allowed (only Accept, Content-Type, X-Requested-With, Referer, Origin and X-... names)")
        value = str(v)
        if len(value) > MAX_HEADER_VALUE or re.search(r"[\r\n]", value):
            raise WatchError(f"the value of header {name} is too long or has a line break (at most {MAX_HEADER_VALUE} characters)")
        out[name] = value
    return out


# ---------------------------------------------------------------------------------------------------------------- reading HTML
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "param"}
NO_TEXT = {"script", "style", "noscript", "template", "svg", "head"}
BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "td", "th", "dd", "dt", "ul", "ol",
         "table", "header", "footer", "main", "nav", "aside", "form", "label", "option", "button"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def elements(self) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node)]


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.stack = [self.root]
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if node.tag not in VOID:
            self.stack.append(node)
        self._in_title = node.tag == "title"

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
        self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        self.stack[-1].children.append(data)


def parse_html(html: str) -> _Tree:
    t = _Tree()
    try:
        t.feed(html or "")
        t.close()
    except Exception:  # noqa: BLE001 - a broken page still gives what was read
        pass
    return t


def text_of(node: Node) -> str:
    out: list[str] = []

    def walk(n: Node) -> None:
        for c in n.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag not in NO_TEXT:
                if c.tag in BLOCK:
                    out.append("\n")
                walk(c)
                if c.tag in BLOCK:
                    out.append("\n")
    walk(node)
    text = re.sub(r"[ \t\r\f\v ]+", " ", "".join(out))
    return re.sub(r"\s*\n\s*", "\n", text).strip()


_COMPOUND = re.compile(r"(?P<tag>\*|[a-zA-Z][\w-]*)?(?P<rest>(?:#[\w-]+|\.[\w-]+|\[[^\]]*\])*)")
_PIECE = re.compile(r"#([\w-]+)|\.([\w-]+)|\[\s*([\w:-]+)\s*(?:([~^$*|]?=)\s*(\"[^\"]*\"|'[^']*'|[^\]\s]*)\s*)?\]")


def _compile_compound(text: str) -> dict[str, Any]:
    m = _COMPOUND.fullmatch(text)
    if not m or not text:
        raise WatchError(f"CSS selector part {text!r} is not supported (use tag, #id, .class, [attr=value], spaces and >)")
    comp: dict[str, Any] = {"tag": (m.group("tag") or "*").lower(), "id": None, "classes": [], "attrs": []}
    for p in _PIECE.finditer(m.group("rest") or ""):
        if p.group(1):
            comp["id"] = p.group(1)
        elif p.group(2):
            comp["classes"].append(p.group(2))
        else:
            val = p.group(5)
            if val and val[0] in "\"'":
                val = val[1:-1]
            comp["attrs"].append((p.group(3).lower(), p.group(4), val))
    return comp


def compile_selector(selector: str) -> list[list[tuple[str, dict[str, Any]]]]:
    """A small CSS subset: tag, #id, .class, [attr], [attr=v], [attr~=v], [attr^=v], [attr$=v], [attr*=v], descendant (space) and child
    (>) combinators, and comma lists. Returns, per comma part, [(combinator, compound)] left to right."""
    sel = (selector or "").strip()
    if not sel or len(sel) > 300:
        raise WatchError("selector is empty or too long")
    groups = []
    for part in sel.split(","):
        tokens = re.findall(r"\[[^\]]*\]|>|\s+|[^\s>\[]+", part.strip())
        chain: list[tuple[str, dict[str, Any]]] = []
        comb, cur = " ", ""
        for tok in tokens + [" "]:
            if tok == ">" or tok.isspace():
                if cur:
                    chain.append((comb, _compile_compound(cur)))
                    cur, comb = "", " "
                if tok == ">":
                    comb = ">"
            else:
                cur += tok
        if not chain:
            raise WatchError(f"CSS selector {part!r} is empty")
        groups.append(chain)
    return groups


def _matches_compound(n: Node, c: dict[str, Any]) -> bool:
    if c["tag"] != "*" and n.tag != c["tag"]:
        return False
    if c["id"] is not None and n.attrs.get("id") != c["id"]:
        return False
    if c["classes"] and not set(c["classes"]) <= set(n.attrs.get("class", "").split()):
        return False
    for name, op, val in c["attrs"]:
        if name not in n.attrs:
            return False
        have = n.attrs[name]
        if op is None:
            continue
        if not ((op == "=" and have == val) or (op == "~=" and val in have.split()) or (op == "^=" and have.startswith(val or "\0"))
                or (op == "$=" and have.endswith(val or "\0")) or (op == "*=" and (val or "\0") in have)
                or (op == "|=" and (have == val or have.startswith(f"{val}-")))):
            return False
    return True


def _matches_chain(n: Node, chain: list[tuple[str, dict[str, Any]]], i: int) -> bool:
    comb, comp = chain[i]
    if not _matches_compound(n, comp):
        return False
    if i == 0:
        return True
    up = n.parent
    if comb == ">":
        return up is not None and up.tag != "#root" and _matches_chain(up, chain, i - 1)
    while up is not None and up.tag != "#root":
        if _matches_chain(up, chain, i - 1):
            return True
        up = up.parent
    return False


def select(root: Node, selector: str) -> list[Node]:
    groups = compile_selector(selector)
    found: list[Node] = []

    def walk(n: Node) -> None:
        for c in n.elements():
            if any(_matches_chain(c, g, len(g) - 1) for g in groups):
                found.append(c)
            walk(c)
    walk(root)
    return found


_NUMBER = re.compile(r"[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?")


def parse_number(text: str) -> float | None:
    m = _NUMBER.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "").replace("−", "-"))
    except ValueError:
        return None


def fmt_number(v: float) -> str:
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,.4f}".rstrip("0").rstrip(".")


def _excerpt(text: str, at: int = 0, width: int = 120) -> str:
    start = max(0, at - width // 3)
    s = re.sub(r"\s+", " ", text[start:start + width]).strip()
    return ("…" if start else "") + s + ("…" if start + width < len(text) else "")


# ---------------------------------------------------------------------------------------------------------------- conditions
def check_regex(pattern: str) -> re.Pattern[str]:
    if len(pattern) > 200:
        raise WatchError("regex is too long (at most 200 characters)")
    try:
        return re.compile(pattern, re.I | re.S)
    except re.error as e:
        raise WatchError(f"regex does not compile: {e}") from None


def evaluate(w: dict[str, Any], html: str) -> dict[str, Any]:
    """Applies a page watcher's condition to a page. Returns {"ok", "satisfied", "value", "detail", "error", "title"}; "satisfied" for
    "changed" means: differs from w["last_value"] (no last value = nothing to compare with yet)."""
    tree = parse_html(html)
    title = re.sub(r"\s+", " ", tree.title).strip()[:200]
    looks_html = bool(re.search(r"<\s*(html|body|div|p|span|table|head)\b", html[:20000] or "", re.I))
    whole = text_of(tree.root) if looks_html else (html or "")
    scope = whole
    if w["selector"]:
        nodes = select(tree.root, w["selector"])
        if not nodes:
            return {"ok": False, "satisfied": False, "value": None, "title": title, "error": "selector found nothing",
                    "detail": t("Found nothing on the page that matches '{selector}'", selector=w["selector"])}
        scope = "\n".join(text_of(n) for n in nodes[:20])
    scope = scope[:MAX_TEXT]
    if w["regex"]:
        m = check_regex(w["regex"]).search(scope)
        if not m and w["mode"] in ("number_above", "number_below"):
            return {"ok": False, "satisfied": False, "value": None, "title": title, "error": "regex found nothing",
                    "detail": t("Found nothing on the page that matches the regular expression")}
        if m:
            scope = m.group(1) if m.groups() and m.group(1) is not None else m.group(0)
        elif w["mode"] == "changed":
            scope = ""
    mode = w["mode"]
    if mode in ("text_appears", "text_disappears"):
        needle = w["match"].strip().lower()
        at = scope.lower().find(needle)
        present = at >= 0
        value = _excerpt(scope, at) if present else None
        sat = present if mode == "text_appears" else not present
        if mode == "text_appears":
            detail = (t("'{match}' is there: {value}", match=w["match"], value=value) if present else
                      t("'{match}' is not there yet", match=w["match"]))
        else:
            detail = (t("'{match}' is gone", match=w["match"]) if not present else
                      t("'{match}' is still there: {value}", match=w["match"], value=value))
        return {"ok": True, "satisfied": sat, "value": value, "title": title, "error": None, "detail": detail}
    if mode in ("number_above", "number_below"):
        v = parse_number(scope)
        if v is None:
            return {"ok": False, "satisfied": False, "value": None, "title": title, "error": "no number found",
                    "detail": t("Found no number in the chosen part: {text}", text=_excerpt(scope))}
        limit = float(w["threshold"])
        sat = v > limit if mode == "number_above" else v < limit
        if mode == "number_above":
            template = N_("Now {value} — above the limit of {limit}") if sat else N_("Now {value} — not above the limit of {limit} yet")
        else:
            template = N_("Now {value} — below the limit of {limit}") if sat else N_("Now {value} — not below the limit of {limit} yet")
        detail = t(template, value=fmt_number(v), limit=fmt_number(limit))
        return {"ok": True, "satisfied": sat, "value": fmt_number(v), "title": title, "error": None, "detail": detail}
    # changed
    digest = hashlib.sha256(re.sub(r"\s+", " ", scope).strip().encode()).hexdigest()[:16]
    value = f"{digest}:{_excerpt(scope, 0, 100)}"
    last = w.get("last_value") or ""
    if not last:
        return {"ok": True, "satisfied": False, "value": value, "title": title, "error": None,
                "detail": t("Using what is there now as the starting point: {text}", text=_excerpt(scope, 0, 100))}
    changed = last.split(":", 1)[0] != digest
    return {"ok": True, "satisfied": changed, "value": value, "title": title, "error": None,
            "detail": t("The content changed: {text}", text=_excerpt(scope, 0, 100)) if changed else t("Nothing has changed")}


def script_result(out: dict[str, Any]) -> dict[str, Any]:
    """A script watcher's run -> the same shape as evaluate()."""
    if out.get("error"):
        return {"ok": False, "satisfied": False, "value": None, "title": "", "error": str(out["error"])[:200],
                "detail": t("Could not run the script: {error}", error=str(out["error"])[:120])}
    if out.get("timed_out") or out.get("exit_code") not in (0, None):
        err = (out.get("stderr") or "").strip().splitlines()[-1:] or [f"exit code {out.get('exit_code')}"]
        return {"ok": False, "satisfied": False, "value": None, "title": "", "error": err[0][:200],
                "detail": t("The script failed: {error}", error=err[0][:120])}
    lines = [ln for ln in (out.get("stdout") or "").strip().splitlines() if ln.strip()]
    data: Any = None
    if lines:
        try:
            data = json.loads(lines[-1])
        except ValueError:
            data = None
    if not isinstance(data, dict) or not isinstance(data.get("hit"), bool):
        return {"ok": False, "satisfied": False, "value": None, "title": "", "error": "no JSON result",
                "detail": t("The script's last line is not in the form {form}", form='{"hit": true|false, "value": ...}')}
    value = None if data.get("value") is None else str(data["value"])[:300]
    return {"ok": True, "satisfied": data["hit"], "value": value, "title": "", "error": None,
            "detail": (t("The script condition is met: {value}", value=value) if data["hit"] else
                       t("The script condition is not met yet: {value}", value=value)) if value else
            (t("The script condition is met") if data["hit"] else t("The script condition is not met yet"))}


def describe(w: dict[str, Any]) -> str:
    every = int(w["every_seconds"]) // 60
    if every % 60 == 0:
        when = t("every hour") if every == 60 else t("every {n} hours", n=every // 60)
    else:
        when = t("every minute") if every == 1 else t("every {n} minutes", n=every)
    mode = w["mode"]
    if w["kind"] == "script":
        what = t("if the script condition is met")
    elif mode == "text_appears":
        what = t("if '{match}' appears", match=w["match"])
    elif mode == "text_disappears":
        what = t("if '{match}' disappears", match=w["match"])
    elif mode == "number_above":
        what = t("if the value goes above {limit}", limit=fmt_number(float(w["threshold"])))
    elif mode == "number_below":
        what = t("if the value goes below {limit}", limit=fmt_number(float(w["threshold"])))
    else:
        what = t("if the content changes")
    return t("Check {when} · {what}", when=when, what=what) + (t(" · once only") if w["once"] else "")


# ---------------------------------------------------------------------------------------------------------------- the store
FetchFn = Callable[[str], Awaitable[Page]]
BrowserFetchFn = Callable[[str, str], Awaitable[Page]]                     # (url, tab key) -> the page as the browser shows it
TabFetchFn = Callable[[str, str, dict[str, str], "str | None", str], Awaitable[Page]]   # (url, method, headers, body, site tab key) -> the answer
ScriptFn = Callable[[str, list[dict[str, str]]], Awaitable[dict[str, Any]]]   # (code, files [{"name", "b64"}]) -> the work room's answer


class Watchers:
    def __init__(self, store: Any, *, fetch: FetchFn | None = None, browser_fetch: BrowserFetchFn | None = None,
                 tab_fetch: TabFetchFn | None = None, script_runner: ScriptFn | None = None, clock: Callable[[], float] = time.time,
                 rng: random.Random | None = None, browser_version: Callable[[], Awaitable[str]] | None = None,
                 language: Callable[[], str] = lambda: "", proxy: Callable[[], str | None] | str | None = None):
        """fetch: the light look (url, **options) -> Page (default: public_get as the browser's identity); tab_fetch: the same request inside
        the agent's browser; browser_fetch: a full page load; browser_version: the running browser's version, read once, for the user
        agent of the light look; language: the owner's language (Accept-Language); proxy: the VPN route (vpn.py); rng: the spread of the
        next check (tests seed it)."""
        self.store, self.clock = store, clock
        self.rng = rng or random.Random()
        self.browser_version, self.language, self.proxy = browser_version, language, proxy
        self._ua: tuple[str, float] | None = None   # (user agent, when to read it again) - the browser's own, read once
        self.fetch = fetch or self._light
        self.browser_fetch, self.tab_fetch, self.script_runner = browser_fetch, tab_fetch, script_runner
        self.on_fire: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None     # (watcher, result): notification + handoff
        self.on_human_check: Callable[[dict[str, Any], str], Any] | None = None   # (watcher, words): a page asks for a human; at most daily
        self.on_disabled: Callable[[dict[str, Any]], Any] | None = None                 # turned off after too many errors
        self.armed: Callable[[int], bool] | None = None   # (watcher id): ready to buy (buy_ready.BuyReady.is_armed): looks every 2 min, no 30 s look
        self.buy_view: Callable[[int], dict[str, Any] | None] | None = None   # (watcher id): the `buy` object of the API
        self.on_deleted: Callable[[int], Any] | None = None   # removed: what the user allowed it to do at setup goes too (schedule_grants.py)
        self._checking: dict[int, asyncio.Task[Any]] = {}
        self._sem = asyncio.Semaphore(CONCURRENT_CHECKS)
        with store._lock:
            store._db.executescript(SCHEMA)
            have = {r[1] for r in store._db.execute("PRAGMA table_info(watchers)")}
            for col, ddl in NEW_COLUMNS:
                if col not in have:
                    store._db.execute(f"ALTER TABLE watchers ADD COLUMN {col} {ddl}")
            store._db.commit()

    # ------------------------------------------------------------------ the light look
    async def user_agent(self) -> str:
        """The agent browser's own Chrome identity (its running version, read once and kept); KeloWatcher when there is no browser."""
        if self._ua is not None and (self._ua[0] != USER_AGENT or self.clock() < self._ua[1]):
            return self._ua[0]
        ua = USER_AGENT
        if self.browser_version is not None:
            try:
                ua = chrome_user_agent(int(str(await self.browser_version()).split(".")[0]))
            except Exception:  # noqa: BLE001 - no browser (yet): the plain name, tried again in ten minutes
                ua = USER_AGENT
        self._ua = (ua, self.clock() + 600)
        return ua

    def accept_language(self) -> str:
        lang = (self.language() or "").strip()
        base = lang.split("-")[0]
        if not lang:
            return "en;q=0.8"
        return f"{lang},{base};q=0.9,en;q=0.7" if base != lang else f"{lang},en;q=0.7"

    async def _light(self, url: str, **options: Any) -> Page:
        return await public_get(url, user_agent=await self.user_agent(), language=self.accept_language(), proxy=self.proxy,
                                method=options.get("method", "GET"), body=options.get("body"), extra_headers=options.get("headers"))

    # ------------------------------------------------------------------ reading
    def _row(self, wid: int) -> dict[str, Any] | None:
        rows = self.store._all("SELECT * FROM watchers WHERE id = ?", (wid,))
        return rows[0] if rows else None

    def _public(self, r: dict[str, Any]) -> dict[str, Any]:
        dry = None
        if r["dry_run"]:
            try:
                dry = json.loads(r["dry_run"])
            except ValueError:
                dry = None
        if r["enabled"]:
            state = "error" if r["error_count"] else "watching"
        else:
            state = "done" if (r["once"] and r["fire_count"]) else "off"
        kept = r["level"] in LEVELS and (r["level_until"] or 0) > self.clock()
        return {"id": r["id"], "name": r["name"], "kind": r["kind"], "url": r["url"] or None, "mode": r["mode"], "match": r["match"] or None,
                "selector": r["selector"] or None, "regex": r["regex"] or None, "threshold": r["threshold"],
                "use_browser": bool(r["use_browser"]), "signed_in": bool(r["signed_in"]), "method": r["method"] or "GET",
                "body": r["body"] or None, "headers": clean_headers(r["headers"]) or None,
                "level": r["level"] if kept else None, "level_until": r["level_until"] if kept else None, "code": r["code"] if r["kind"] == "script" else None, "note": r["note"] or None,
                "every_minutes": int(r["every_seconds"]) // 60, "enabled": bool(r["enabled"]), "once": bool(r["once"]), "state": state,
                "description": describe(r), "last_value": _shown_value(r["last_value"]), "last_checked_at": r["last_checked_at"],
                "next_check_at": r["next_check_at"] if r["enabled"] else None, "last_fired_at": r["last_fired_at"],
                "fire_count": r["fire_count"], "error_count": r["error_count"], "last_error": r["last_error"] or None,
                "disabled_reason": r["disabled_reason"] or None, "dry_run": dry, "can_enable": self._fresh(r),
                "created_at": r["created_at"], "updated_at": r["updated_at"], "created_by": r["created_by"],
                "buy": self.buy_view(r["id"]) if self.buy_view is not None else None}

    def _is_armed(self, wid: int | None) -> bool:
        if not wid or self.armed is None:
            return False
        try:
            return bool(self.armed(int(wid)))
        except Exception:  # noqa: BLE001 - a problem in the permission check never makes a watcher look more often
            log.exception("watcher %s: ready-to-buy check failed", wid)
            return False

    def _fresh(self, r: dict[str, Any]) -> bool:
        if not r["dry_run_at"] or r["dry_run_at"] < r["config_at"] or self.clock() - r["dry_run_at"] > DRY_RUN_FRESH:
            return False
        try:
            return bool(json.loads(r["dry_run"]).get("ok"))
        except ValueError:
            return False

    def get(self, wid: int) -> dict[str, Any] | None:
        r = self._row(wid)
        return self._public(r) if r else None

    def list(self) -> list[dict[str, Any]]:
        return [self._public(r) for r in self.store._all("SELECT * FROM watchers ORDER BY enabled DESC, id")]

    def events(self, wid: int, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT * FROM watch_events WHERE watch_id = ? ORDER BY id DESC LIMIT ?", (wid, max(1, min(limit, KEEP_EVENTS))))
        return [{"id": r["id"], "at": r["at"], "kind": r["kind"], "detail": r["detail"], "value": _shown_value(r["value"])} for r in rows]

    def fired_since(self, since: float) -> list[dict[str, Any]]:
        rows = self.store._all("SELECT e.*, w.name FROM watch_events e JOIN watchers w ON w.id = e.watch_id "
                               "WHERE e.kind = 'fired' AND e.at > ? ORDER BY e.id", (since,))
        return [dict(r) for r in rows]

    def _event(self, wid: int, kind: str, detail: str, value: str | None = None) -> None:
        self.store._run("INSERT INTO watch_events(watch_id, at, kind, detail, value) VALUES(?,?,?,?,?)",
                        (wid, self.clock(), kind, detail[:400], value))
        self.store._run("DELETE FROM watch_events WHERE watch_id = ? AND id NOT IN (SELECT id FROM watch_events WHERE watch_id = ? "
                        "ORDER BY id DESC LIMIT ?)", (wid, wid, KEEP_EVENTS))

    # ------------------------------------------------------------------ writing
    def _clean(self, f: dict[str, Any], armed: bool = False) -> dict[str, Any]:
        """Checks a full set of fields; returns them normalised. Raises WatchError."""
        name = str(f.get("name") or "").strip()
        if not name or len(name) > 60:
            raise WatchError("name is required (at most 60 characters)")
        kind = str(f.get("kind") or "page")
        if kind not in KINDS:
            raise WatchError(f"kind must be one of {', '.join(KINDS)}")
        url = str(f.get("url") or "").strip()
        if url:
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                raise WatchError("url must be a public https:// address")
            try:
                valid_host(parts.hostname)
            except NetGuardError as e:
                raise WatchError(str(e)) from None
            if parts.username or parts.password or parts.port not in (None, 443):
                raise WatchError("url must not carry a login or a port")
            if len(url) > 2000:
                raise WatchError("url is too long")
        mode = str(f.get("mode") or ("script" if kind == "script" else ""))
        match = str(f.get("match") or "").strip()[:200]
        selector = str(f.get("selector") or "").strip()
        regex = str(f.get("regex") or "").strip()
        threshold = f.get("threshold")
        code = str(f.get("code") or "")
        if kind == "page":
            if not url:
                raise WatchError("a page watcher needs url")
            if mode not in MODES:
                raise WatchError(f"mode must be one of {', '.join(MODES)}")
            if mode in ("text_appears", "text_disappears") and not match:
                raise WatchError("this mode needs match: the words to look for")
            if mode in ("number_above", "number_below"):
                if threshold in (None, ""):
                    raise WatchError("this mode needs threshold: the number to compare with")
                try:
                    threshold = float(threshold)
                except (TypeError, ValueError):
                    raise WatchError("threshold must be a number") from None
                if not selector and not regex:
                    raise WatchError("this mode needs selector (CSS) or regex to find the number on the page")
            else:
                threshold = None
            if selector:
                compile_selector(selector)
            if regex:
                check_regex(regex)
            code = ""
        else:
            mode = "script"
            if not code.strip() or len(code) > MAX_CODE:
                raise WatchError(f"a script watcher needs code (Python, at most {MAX_CODE} characters) that prints "
                                 "{\"hit\": true|false, \"value\": ...} as its last line")
            match, selector, regex, threshold = "", "", "", None
        try:
            every = int(float(f.get("every_minutes") or DEFAULT_EVERY_MINUTES))
        except (TypeError, ValueError):
            raise WatchError("every_minutes must be a number") from None
        least = ARMED_MIN_MINUTES if armed else MIN_EVERY_MINUTES
        if not least <= every <= MAX_EVERY_MINUTES:
            raise WatchError(f"every_minutes must be between {least} and {MAX_EVERY_MINUTES}")
        note = str(f.get("note") or "").strip()[:1000]
        use_browser = int(bool(f.get("use_browser"))) if kind == "page" else 0
        method, body, headers, signed_in = "GET", "", {}, int(bool(f.get("signed_in")))
        if url:
            method = str(f.get("method") or "GET").strip().upper()
            if method not in ("GET", "POST"):
                raise WatchError("method must be GET or POST")
            headers = clean_headers(f.get("headers"))
            if method == "POST":
                body = str(f.get("body") or "")
                if len(body) > MAX_BODY:
                    raise WatchError(f"body is too long (at most {MAX_BODY} characters)")
                if use_browser:
                    raise WatchError("use_browser loads the page itself, so it cannot POST; use method GET, or drop use_browser")
                if word := action_word(urlsplit(url).path) or action_word(body):
                    raise WatchError(f"a watcher only reads; that address looks like it does something ('{word}')")
        else:
            signed_in = 0
        return {"name": name, "kind": kind, "url": url, "mode": mode, "match": match, "selector": selector, "regex": regex,
                "threshold": threshold, "use_browser": use_browser, "code": code, "signed_in": signed_in, "method": method, "body": body,
                "headers": json.dumps(headers, sort_keys=True, ensure_ascii=False) if headers else "",
                "note": note, "every_seconds": every * 60, "once": int(bool(f.get("once")))}

    def create(self, created_by: str = "user", **fields: Any) -> dict[str, Any]:
        if self.store._all("SELECT COUNT(*) AS n FROM watchers")[0]["n"] >= MAX_WATCHERS:
            raise WatchError(f"at most {MAX_WATCHERS} watchers; remove one first")
        f = self._clean(fields)
        now = self.clock()
        cols = list(f)
        wid = int(self.store._run(f"INSERT INTO watchers({', '.join(cols)}, enabled, config_at, created_at, updated_at, created_by) "
                                  f"VALUES({', '.join('?' * len(cols))}, 0, ?, ?, ?, ?)",
                                  (*f.values(), now, now, now, created_by)).lastrowid or 0)
        return self.get(wid) or {}

    def update(self, wid: int, **changes: Any) -> dict[str, Any] | None:
        r = self._row(wid)
        if r is None:
            return None
        armed = self._is_armed(wid)
        cur = {**r, "every_minutes": int(r["every_seconds"]) // 60}
        if not armed:   # it was ready to buy and looked often, and no longer is: back to the usual minimum
            cur["every_minutes"] = max(MIN_EVERY_MINUTES, cur["every_minutes"])
        merged = {**cur, **{k: v for k, v in changes.items() if v is not None}}
        f = self._clean(merged, armed)
        condition_changed = any(f[k] != r[k] for k in CONDITION_FIELDS)
        now = self.clock()
        sets = ", ".join(f"{k} = ?" for k in f)
        extra = ""
        args: list[Any] = [*f.values(), now]
        if condition_changed:   # what is checked changed: off until a new dry run, and no old finding to compare with
            extra = (", enabled = 0, next_check_at = NULL, last_value = NULL, last_hit = NULL, config_at = ?, error_count = 0, last_error = '', "
                     "level = '', level_until = NULL, backoff = 1, human_notice_at = NULL, confirming = 0")
            args.append(now)
        elif f["every_seconds"] != r["every_seconds"] and r["enabled"]:
            extra = ", next_check_at = ?"
            args.append(now + f["every_seconds"])
        self.store._run(f"UPDATE watchers SET {sets}, updated_at = ?{extra} WHERE id = ?", (*args, wid))
        return self.get(wid)

    def delete(self, wid: int) -> bool:
        t = self._checking.pop(wid, None)
        if t is not None and not t.done():
            t.cancel()
        self.store._run("DELETE FROM watch_events WHERE watch_id = ?", (wid,))
        gone = self.store._run("DELETE FROM watchers WHERE id = ?", (wid,)).rowcount > 0
        if gone and self.on_deleted is not None:
            try:
                self.on_deleted(wid)
            except Exception:  # noqa: BLE001
                log.exception("watcher %s: removing its permissions failed", wid)
        return gone

    def finish(self, wid: int, reason: str) -> dict[str, Any] | None:
        """Off for good because its task is done (a one-shot task used what it was allowed): shows as done with the reason."""
        r = self._row(wid)
        if r is None:
            return None
        now = self.clock()
        self.store._run("UPDATE watchers SET enabled = 0, next_check_at = NULL, disabled_reason = ?, updated_at = ? WHERE id = ?",
                        (reason[:300], now, wid))
        self._event(wid, "disabled", reason[:300])
        return self.get(wid)

    def enable(self, wid: int, on: bool = True) -> dict[str, Any] | None:
        r = self._row(wid)
        if r is None:
            return None
        now = self.clock()
        if not on:
            self.store._run("UPDATE watchers SET enabled = 0, next_check_at = NULL, updated_at = ? WHERE id = ?", (now, wid))
            if r["enabled"]:
                self._event(wid, "disabled", t("Turned off"))
            return self.get(wid)
        if not self._fresh(r):
            raise WatchError("run a dry run first (watch_dry_run) and show the user its result; a watcher can be turned on only after a "
                             "successful dry run of its current settings within the last 24 hours")
        dry = json.loads(r["dry_run"])
        # the dry run's finding is the starting point: only a change into the condition fires
        self.store._run("UPDATE watchers SET enabled = 1, next_check_at = ?, last_value = ?, last_hit = ?, error_count = 0, last_error = '', "
                        "disabled_reason = '', backoff = 1, confirming = 0, updated_at = ? WHERE id = ?",
                        (now + min(r["every_seconds"], 300), dry.get("raw_value"), int(bool(dry.get("satisfied"))) if r["mode"] != "changed" else 0,
                         now, wid))
        self._event(wid, "enabled", t("Turned on"))
        return self.get(wid)

    # ------------------------------------------------------------------ checking
    @staticmethod
    def start_level(r: dict[str, Any]) -> str:
        """Where a watcher begins: its own sign-in or a POST needs the browser's tab; JavaScript needs the page; the rest is light."""
        if r.get("signed_in") or (r.get("method") or "GET") == "POST":
            return "tab"
        return "page" if r.get("use_browser") else "light"

    def level_now(self, r: dict[str, Any]) -> str:
        """The level to look at now: a raised level that got past a block stays for LEVEL_KEEP, else the starting one."""
        start = self.start_level(r)
        kept = r.get("level")
        if kept in LEVELS and (r.get("level_until") or 0) > self.clock() and LEVELS.index(kept) > LEVELS.index(start):
            return kept
        return start

    def _available(self, level: str) -> bool:
        return level == "light" or (level == "tab" and self.tab_fetch is not None) or (level == "page" and self.browser_fetch is not None)

    async def _fetch(self, r: dict[str, Any], level: str) -> Page:
        url, method, body = r["url"], r.get("method") or "GET", r.get("body") or None
        headers = clean_headers(r.get("headers"))
        if level == "light":
            opts: dict[str, Any] = {}
            if method != "GET":
                opts["method"], opts["body"] = method, body
            if headers:
                opts["headers"] = headers
            return await self.fetch(url, **opts)
        if level == "tab":
            if self.tab_fetch is None:
                raise WatchError("the browser is not available on this server, so a signed-in or POST watcher cannot look")
            return await self.tab_fetch(url, method, headers, body, site_tab_key(url))
        if self.browser_fetch is None:
            raise WatchError("the browser is not available on this server; turn use_browser off")
        return await self.browser_fetch(url, f"task:watch-{r['id']}")

    @staticmethod
    def blocked(page: Page, level: str) -> str:
        """Why this answer is a block ("" = it is not): a status that means "no" for a visitor like this one, or a bot-check page."""
        if page.status in BLOCK_STATUS:
            return f"HTTP {page.status}"
        if level == "page":
            return page.challenge.name if page.challenge is not None else ""
        found = detect(None, status=page.status, headers=page.headers)
        if found is not None:
            return found.name
        body = (page.body or "")[:200_000].lower()
        if re.search(r"<title>\s*just a moment\.{0,3}\s*</title>", body) or any(m in body for m in CHECK_MARKS):
            return "a bot check"
        return ""

    async def _look(self, r: dict[str, Any]) -> dict[str, Any]:
        """One look at the watcher's subject. Never raises: problems come back as ok False. A blocked look is repeated one level up at
        once; the result says the level that answered ("level") and whether it had to go up ("escalated"). A human check at the top
        comes back with "human" set (not an error)."""
        level = first = self.level_now(r)
        try:
            page: Page | None = None
            if r["url"]:
                top = "tab" if (r.get("method") or "GET") == "POST" else "page"   # a POST cannot be repeated as a page load
                while True:
                    page = await self._fetch(r, level)
                    why = self.blocked(page, level)
                    if not why:
                        break
                    up = next((lv for lv in LEVELS[LEVELS.index(level) + 1:LEVELS.index(top) + 1] if self._available(lv)), None)
                    if up is not None:
                        level = up
                        continue
                    if level == "page" and page.challenge is not None:
                        return self._human(r, page, why, level, first)
                    if page.status >= 400:
                        raise WatchError(f"the page answered HTTP {page.status}")
                    raise WatchError(f"the site asks for a check that a background look cannot pass ({why})")
                if page.status >= 400:
                    raise WatchError(f"the page answered HTTP {page.status}")
            if r["kind"] == "script":
                if self.script_runner is None:
                    raise WatchError("the work room is not running on this server, so a script watcher cannot run")
                files = []
                if page is not None:
                    files = [{"name": "page.html", "b64": base64.b64encode(page.body.encode()).decode()},
                             {"name": "page.txt", "b64": base64.b64encode(text_of(parse_html(page.body).root).encode()).decode()}]
                res = script_result(await self.script_runner(r["code"], files))
            else:
                assert page is not None
                res = evaluate(r, page.body)
            res["url"] = page.url if page else None
            res["level"], res["escalated"] = (level if page else None), (level != first)
            return res
        except (WatchError, NetGuardError) as e:   # a VPN that is down is such an error too: it never sends the look up a level
            return {"ok": False, "satisfied": False, "value": None, "title": "", "error": str(e), "detail": t("Could not check: {error}", error=e),
                    "url": r["url"] or None, "level": level if r["url"] else None, "escalated": False}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("watcher %s: check failed: %s", r["id"], e)
            return {"ok": False, "satisfied": False, "value": None, "title": "", "error": f"{type(e).__name__}: {str(e)[:160]}",
                    "detail": t("Could not check"), "url": r["url"] or None, "level": level if r["url"] else None, "escalated": False}

    @staticmethod
    def _site(r: dict[str, Any]) -> str:
        host = (urlsplit(r.get("url") or "").hostname or "").lower()
        return host[4:] if host.startswith("www.") else host

    def _human(self, r: dict[str, Any], page: Page, why: str, level: str, first: str) -> dict[str, Any]:
        words = t("{site} asks for a human check, so watching '{name}' is paused there. Open the page in Kelo's browser view once to pass it.",
                  site=self._site(r), name=r.get("name", ""))
        return {"ok": False, "satisfied": False, "value": None, "title": "", "error": f"a human check ({why})", "detail": words,
                "url": page.url, "level": level, "escalated": level != first, "human": words}

    def _remember_level(self, wid: int, res: dict[str, Any]) -> None:
        if res.get("escalated") and res.get("level") in LEVELS:   # what got past the block is where it looks for the next day
            self.store._run("UPDATE watchers SET level = ?, level_until = ? WHERE id = ?", (res["level"], self.clock() + LEVEL_KEEP, wid))

    def _gap(self, every_seconds: float, backoff: int = 1, armed: bool = False) -> float:
        """Seconds to the next check: the interval (x backoff) spread by a random 0.8..1.2, never under the minimum (2 minutes when
        the watcher is ready to buy)."""
        least = ARMED_MIN_MINUTES if armed else MIN_EVERY_MINUTES
        return max(least * 60.0, every_seconds * backoff * self.rng.uniform(*SPREAD))

    async def dry_run(self, wid: int) -> dict[str, Any] | None:
        r = self._row(wid)
        if r is None:
            return None
        base = {**r, "last_value": None}
        res = await self._look(base)
        self._remember_level(wid, res)
        now = self.clock()
        shown = {"at": now, "ok": res["ok"], "satisfied": res["satisfied"], "value": _shown_value(res.get("value")), "raw_value": res.get("value"),
                 "detail": res["detail"], "error": res.get("error"), "url": res.get("url"), "title": res.get("title") or "", "level": res.get("level")}
        self.store._run("UPDATE watchers SET dry_run = ?, dry_run_at = ? WHERE id = ?", (json.dumps(shown, ensure_ascii=False), now, wid))
        self._event(wid, "dry_run", res["detail"], res.get("value"))
        return {k: v for k, v in shown.items() if k != "raw_value"}

    async def check(self, wid: int) -> dict[str, Any] | None:
        """One scheduled check: records it, fires on a change into the condition (after one more look 30 s later), counts errors."""
        r = self._row(wid)
        if r is None or not r["enabled"]:
            return None
        res = await self._look(r)
        now = self.clock()
        armed = self._is_armed(wid)
        r2 = self._row(wid)
        if r2 is None or not r2["enabled"] or r2["config_at"] != r["config_at"]:
            return res   # turned off or changed while it was being checked
        self._remember_level(wid, res)
        if res.get("human"):   # a page the browser cannot pass by loading: no error; the checks come further apart and the owner hears once a day
            backoff = min(max(1, r["backoff"]) * 2, MAX_BACKOFF)
            self.store._run("UPDATE watchers SET last_checked_at = ?, next_check_at = ?, backoff = ?, last_error = ?, confirming = 0 WHERE id = ?",
                            (now, now + self._gap(r["every_seconds"], backoff, armed), backoff, str(res["error"])[:300], wid))
            if not r["human_notice_at"] or now - r["human_notice_at"] >= HUMAN_NOTICE_EVERY:
                self.store._run("UPDATE watchers SET human_notice_at = ? WHERE id = ?", (now, wid))
                self._event(wid, "error", res["human"])
                if self.on_human_check:
                    _call(self.on_human_check, self.get(wid) or {}, res["human"])
            return res
        nxt = now + self._gap(r["every_seconds"], armed=armed)
        if not res["ok"]:
            errors = r["error_count"] + 1
            self.store._run("UPDATE watchers SET last_checked_at = ?, next_check_at = ?, error_count = ?, last_error = ?, confirming = 0 WHERE id = ?",
                            (now, nxt, errors, str(res.get("error") or "")[:300], wid))
            if errors == 1 or errors >= MAX_ERRORS:
                self._event(wid, "error", res["detail"])
            if errors >= MAX_ERRORS:
                why = t("Turned off after failing to check {n} times in a row: {error}", n=MAX_ERRORS, error=str(res.get("error") or "")[:160])
                self.store._run("UPDATE watchers SET enabled = 0, next_check_at = NULL, disabled_reason = ? WHERE id = ?", (why, wid))
                self._event(wid, "disabled", why)
                if self.on_disabled:
                    _call(self.on_disabled, self.get(wid) or {})
            return res
        fires = res["satisfied"] and (r["mode"] == "changed" or not r["last_hit"])
        if fires and not r["confirming"] and not armed:   # a hit: look once more in 30 s (the scheduler comes back) before it wakes the owner; a watcher ready to buy fires at once (the buying run looks at the page itself)
            self.store._run("UPDATE watchers SET last_checked_at = ?, next_check_at = ?, error_count = 0, last_error = '', backoff = 1, "
                            "confirming = 1 WHERE id = ?", (now, now + CONFIRM_DELAY, wid))
            return {**res, "confirming": True}
        if r["mode"] == "changed":
            last_value = res["value"] if (res["satisfied"] or not r["last_value"]) else r["last_value"]
        else:
            last_value = res["value"]
        self.store._run("UPDATE watchers SET last_checked_at = ?, next_check_at = ?, error_count = 0, last_error = '', last_value = ?, "
                        "last_hit = ?, backoff = 1, confirming = 0 WHERE id = ?", (now, nxt, last_value, int(bool(res["satisfied"])), wid))
        if fires:
            self.store._run("UPDATE watchers SET fire_count = fire_count + 1, last_fired_at = ? WHERE id = ?", (now, wid))
            self._event(wid, "fired", res["detail"], res.get("value"))
            if r["once"]:
                self.store._run("UPDATE watchers SET enabled = 0, next_check_at = NULL, disabled_reason = ? WHERE id = ?",
                                (t("Turned off after telling you once"), wid))
            if self.on_fire:
                _call(self.on_fire, self.get(wid) or {}, res)
        return res

    def due(self, now: float) -> list[int]:
        return [r["id"] for r in self.store._all("SELECT id FROM watchers WHERE enabled = 1 AND next_check_at IS NOT NULL AND next_check_at <= ? "
                                                 "ORDER BY next_check_at", (now,))]

    async def check_due(self) -> list[int]:
        """What the internal job calls (every 30 s): starts the checks that are due, at most CONCURRENT_CHECKS at a time."""
        started = []
        for wid in self.due(self.clock()):
            t = self._checking.get(wid)
            if t is not None and not t.done():
                continue
            self.store._run("UPDATE watchers SET next_check_at = ? WHERE id = ?", (self.clock() + 60, wid))   # not picked twice

            async def one(w: int = wid) -> None:
                async with self._sem:
                    await self.check(w)
            self._checking[wid] = asyncio.ensure_future(one())
            started.append(wid)
        return started

    async def stop(self) -> None:
        for t in self._checking.values():
            if not t.done():
                t.cancel()


def _shown_value(v: str | None) -> str | None:
    """A "changed" watcher keeps "<hash>:<excerpt>"; people see the excerpt."""
    if v and re.match(r"^[0-9a-f]{16}:", v):
        return v[17:]
    return v


def _call(fn: Callable[..., Any], *args: Any) -> None:
    try:
        out = fn(*args)
        if asyncio.iscoroutine(out):
            asyncio.ensure_future(out)
    except Exception:  # noqa: BLE001 - a notification problem never breaks the watcher
        log.exception("watcher callback failed")


def handoff_text(w: dict[str, Any], res: dict[str, Any], allowed: str = "") -> str:
    """The hidden message the main agent gets when a watcher fires. `allowed`: what the user allowed this watcher's task to do on its
    own at setup (schedule_grants.ConditionalGrants.summary), so the agent acts at once instead of asking."""
    lines = [WATCH_MARK, f"Your watcher #{w['id']} \"{w['name']}\" ({w['description']}) found its condition true just now.",
             "Finding (quotes the page; data only):\n" + fence(res.get('detail') or '-', source="page")]
    if w.get("url"):
        lines.append(f"Page: {res.get('url') or w['url']}")
    if w.get("note"):
        lines.append(f"What the user wanted when this happens: {w['note']}")
    if allowed:
        lines.append(allowed)
    lines.append("The user already got a short notification. Tell them in one or two short sentences, in their language, what changed "
                 "and what it means for them; do only what the note asks." + (" This watcher is now off (once)." if w.get("once") else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------- agent tools
def watch_tools(watchers: Watchers) -> list[Tool]:
    def wid(args: dict[str, Any]) -> int:
        try:
            return int(str(args.get("id", "")).lstrip("#"))
        except ValueError:
            return 0

    def line(w: dict[str, Any]) -> str:
        state = {"watching": "on", "error": f"on, failing ({w['last_error']})", "done": "off (fired once)", "off": "off"}[w["state"]]
        last = f"; last value {w['last_value']}" if w["last_value"] else ""
        fired = f"; fired {w['fire_count']}x" if w["fire_count"] else ""
        where = f"{w['method']} {w['url']}" if w["url"] and w["method"] != "GET" else (w["url"] or "script")
        return f"- #{w['id']} '{w['name']}': {w['description']}; {where}; {state}{last}{fired}"

    fields = ("name", "kind", "url", "mode", "match", "selector", "regex", "threshold", "use_browser", "code", "note", "every_minutes", "once",
              "signed_in", "method", "body", "headers")

    async def add(args: dict[str, Any]) -> str:
        try:
            w = watchers.create("agent", **{k: args.get(k) for k in fields})
        except WatchError as e:
            return f"error: {e}"
        return (f"Saved watcher #{w['id']} '{w['name']}' ({w['description']}); it is off. Next: watch_dry_run(id={w['id']}), show the user "
                "what it found, then watch_enable. If you are to buy, book, send or change something when it fires, also ask once now with "
                f"task_allow(watcher_id={w['id']}, trigger, action, ...) so that it is not stuck on a card nobody answers in time.")

    async def list_(args: dict[str, Any]) -> str:
        items = watchers.list()
        # the last findings quote the pages: the list is marked as external data (fenced by the loop)
        return ("Watchers:\n" + data("\n".join(line(w) for w in items))) if items else Ours("There are no watchers.")

    def validate_id(args: dict[str, Any]) -> str | None:
        return None if watchers.get(wid(args)) else f"no watcher #{args.get('id')}; see watch_list"

    async def update(args: dict[str, Any]) -> str:
        try:
            w = watchers.update(wid(args), **{k: args.get(k) for k in fields if k in args})
        except WatchError as e:
            return f"error: {e}"
        if w is None:
            return "error: no such watcher"
        return "Updated:\n" + line(w) + ("" if w["enabled"] else "\nIt is off: run watch_dry_run, show the result, then watch_enable.")

    async def remove(args: dict[str, Any]) -> str:
        return "Removed." if watchers.delete(wid(args)) else "error: no such watcher"

    async def dry_run(args: dict[str, Any]) -> str:
        res = await watchers.dry_run(wid(args))
        if res is None:
            return Ours("error: no such watcher")
        if not res["ok"]:
            return f"Dry run failed: {data(str(res['detail']) + ' (' + str(res['error']) + ')')}. Fix the watcher (watch_update) and try again."
        now = "the condition is TRUE right now" if res["satisfied"] else "the condition is not true right now"
        found = str(res['detail']) + (f"; value: {res['value']}" if res["value"] else "")   # quotes the page: marked, fenced by the loop
        how = {"light": "a plain request from the server", "tab": "a request made inside the browser (with the owner's sign-ins)",
               "page": "a full page load in the browser"}.get(res.get("level") or "", "")
        return (f"Dry run, {now}" + (f" (looked via {res['level']}: {how})" if how else "") + ": " + data(found) + "\nShow this to the user "
                "before turning it on. Once on, it tells them on the next change into the condition (not about the current state, so "
                "if it is already true, say so now).")

    def validate_enable(args: dict[str, Any]) -> str | None:
        if problem := validate_id(args):
            return problem
        if args.get("enabled", True) is False:
            return None
        w = watchers.get(wid(args)) or {}
        if not w.get("can_enable"):
            return "run watch_dry_run first and show the user its result; then call watch_enable"
        return None

    async def describe_enable(args: dict[str, Any]) -> str:
        w = watchers.get(wid(args)) or {}
        dry = w.get("dry_run") or {}
        request = ""
        if w.get("url"):   # what it sends: the method and the address, and the body of a POST
            request = (f"{w['method']} {w['url']}" + (" · " + t("body: {body}", body=w["body"][:200]) if w.get("method") == "POST" and w.get("body") else "") +
                       (" · " + t("signed in") if w.get("signed_in") else ""))
        return t("Turn on watcher '{name}' · {description} · {request} · result of the check just now: {detail}", name=w.get("name", ""),
                 description=w.get("description", ""), request=request or "-", detail=str(dry.get("detail", "-"))[:150])[:700]

    async def enable(args: dict[str, Any]) -> str:
        on = args.get("enabled", True) is not False
        try:
            w = watchers.enable(wid(args), on)
        except WatchError as e:
            return f"error: {e}"
        if w is None:
            return "error: no such watcher"
        return ("Turned on: " if on else "Turned off: ") + line(w)

    props = {"name": {"type": "string", "description": "short name the user will recognise"},
             "url": {"type": "string", "description": "public https page; or the site's own JSON address the page calls, found in the browser "
                                                      "(set signed_in when it needs your sign-in)"},
             "mode": {"type": "string", "enum": list(MODES), "description": "what counts as a hit"},
             "match": {"type": "string", "description": "the words, for text modes"},
             "selector": {"type": "string", "description": "simple CSS selector of the part to look at"},
             "regex": {"type": "string", "description": "or a regular expression; its first group is the value"},
             "threshold": {"type": "number", "description": "the line, for number modes"},
             "use_browser": {"type": "boolean", "description": "the page needs JavaScript"},
             "signed_in": {"type": "boolean", "description": "the page or address shows the owner's own data and needs their sign-in "
                                                             "(looked at inside the browser)"},
             "method": {"type": "string", "enum": ["GET", "POST"], "description": "GET (default) or POST to a site's own JSON address that "
                                                                                  "only reads (search, list, status); an address that orders, "
                                                                                  "books, pays, cancels... is refused"},
             "body": {"type": "string", "description": "POST only: JSON or form text, at most 4096 characters"},
             "headers": {"type": "object", "additionalProperties": {"type": "string"},
                         "description": "at most 8: Accept, Content-Type, X-Requested-With, Referer, Origin, X-...; never Cookie or Authorization"},
             "kind": {"type": "string", "enum": list(KINDS), "description": "page (default) or script"},
             "code": {"type": "string", "description": "script: Python without network; reads page.html/page.txt; last line prints "
                                                       "{\"hit\": true|false, \"value\": ...}"},
             "note": {"type": "string", "description": "what to tell the user or do when it fires"},
             "every_minutes": {"type": "integer", "description": f"at least {MIN_EVERY_MINUTES} (default 60)"},
             "once": {"type": "boolean", "description": "turn off after the first hit"}}
    bare = {k: {kk: vv for kk, vv in v.items() if kk != "description"} for k, v in props.items()}   # watch_update: same fields, described once
    return [
        Tool("watch_add", "Set up a watcher: a cheap repeated check of a page, the site's own JSON address, or a small script that wakes you only when its "
             "condition holds. For waiting on a condition, rather than a schedule; every_minutes as often as the request needs (5 for "
             "something that sells out). It starts off: watch_dry_run next. If you will act when it fires, ask task_allow at setup.",
             {"type": "object", "properties": props, "required": ["name"]}, add),
        Tool("watch_list", "List the watchers with their state and last finding.", {"type": "object", "properties": {}}, list_, read_only=True,
             untrusted=True, source="page"),
        Tool("watch_update", "Change a watcher (fields as in watch_add). Changing what it checks turns it off until a new dry run.",
             {"type": "object", "properties": {"id": {"type": "integer"}, **bare}, "required": ["id"]}, update, validate=validate_id),
        Tool("watch_remove", "Remove a watcher.", {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}, remove,
             validate=validate_id),
        Tool("watch_dry_run", "Check a watcher once now without turning it on, and show the user what it found.",
             {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}, dry_run, validate=validate_id,
             untrusted=True, source="page"),
        Tool("watch_enable", "Turn a watcher on after a dry run the user saw (they approve), or off.",
             {"type": "object", "properties": {"id": {"type": "integer"}, "enabled": {"type": "boolean"}}, "required": ["id"]}, enable,
             requires_approval=True, validate=validate_enable, describe=describe_enable,
             auto_approve=lambda a: a.get("enabled", True) is False),
    ]


# ---------------------------------------------------------------------------------------------------------------- API
class WatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: str = "page"
    url: str | None = Field(default=None, max_length=2000)
    mode: str | None = None
    match: str | None = Field(default=None, max_length=200)
    selector: str | None = Field(default=None, max_length=300)
    regex: str | None = Field(default=None, max_length=200)
    threshold: float | None = None
    use_browser: bool = False
    signed_in: bool = False
    method: str = "GET"
    body: str | None = Field(default=None, max_length=MAX_BODY)
    headers: dict[str, str] | None = None
    code: str | None = Field(default=None, max_length=MAX_CODE)
    note: str | None = Field(default=None, max_length=1000)
    every_minutes: int = DEFAULT_EVERY_MINUTES
    once: bool = False


class WatchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    kind: str | None = None
    url: str | None = Field(default=None, max_length=2000)
    mode: str | None = None
    match: str | None = Field(default=None, max_length=200)
    selector: str | None = Field(default=None, max_length=300)
    regex: str | None = Field(default=None, max_length=200)
    threshold: float | None = None
    use_browser: bool | None = None
    signed_in: bool | None = None
    method: str | None = None
    body: str | None = Field(default=None, max_length=MAX_BODY)
    headers: dict[str, str] | None = None
    code: str | None = Field(default=None, max_length=MAX_CODE)
    note: str | None = Field(default=None, max_length=1000)
    every_minutes: int | None = None
    once: bool | None = None


class WatchEnableIn(BaseModel):
    enabled: bool = True


def watcher_routes(app: FastAPI, watchers: Watchers, auth: Callable[..., Any],
                   buy_check: Callable[[int], Awaitable[dict[str, Any] | None]] | None = None) -> None:
    """The WATCHERS API (contracts in this module's docstring and next to serialize())."""

    def need(wid: int) -> dict[str, Any]:
        w = watchers.get(wid)
        if w is None:
            raise HTTPException(404, "no such watcher")
        return w

    @app.get("/api/watchers", dependencies=[Depends(auth)])
    async def list_watchers() -> dict[str, Any]:
        return {"items": watchers.list(), "limit": MAX_WATCHERS}

    @app.post("/api/watchers", dependencies=[Depends(auth)], status_code=201)
    async def add_watcher(body: WatchIn) -> dict[str, Any]:
        try:
            return watchers.create("user", **body.model_dump())
        except WatchError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/watchers/{wid}", dependencies=[Depends(auth)])
    async def get_watcher(wid: int) -> dict[str, Any]:
        return need(wid)

    @app.patch("/api/watchers/{wid}", dependencies=[Depends(auth)])
    async def patch_watcher(wid: int, body: WatchPatch) -> dict[str, Any]:
        need(wid)
        try:
            return watchers.update(wid, **body.model_dump(exclude_unset=True)) or {}
        except WatchError as e:
            raise HTTPException(422, str(e)) from None

    @app.delete("/api/watchers/{wid}", dependencies=[Depends(auth)])
    async def delete_watcher(wid: int) -> dict[str, bool]:
        need(wid)
        watchers.delete(wid)
        return {"ok": True}

    @app.post("/api/watchers/{wid}/dry-run", dependencies=[Depends(auth)])
    async def dry_run_watcher(wid: int) -> dict[str, Any]:
        need(wid)
        res = await watchers.dry_run(wid)
        return {"result": res, "watcher": watchers.get(wid)}

    @app.post("/api/watchers/{wid}/enable", dependencies=[Depends(auth)])
    async def enable_watcher(wid: int, body: WatchEnableIn) -> dict[str, Any]:
        need(wid)
        try:
            return watchers.enable(wid, body.enabled) or {}
        except WatchError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/watchers/{wid}/buy-check", dependencies=[Depends(auth)])
    async def buy_check_watcher(wid: int) -> dict[str, Any]:
        need(wid)
        if buy_check is None:
            raise HTTPException(404, "this watcher is not ready to buy")
        got = await buy_check(wid)
        if got is None:
            raise HTTPException(404, "this watcher is not ready to buy")
        return got

    @app.get("/api/watchers/{wid}/events", dependencies=[Depends(auth)])
    async def watcher_events(wid: int, limit: int = 30) -> dict[str, Any]:
        need(wid)
        return {"items": watchers.events(wid, limit)}
