"""Looking things up without the browser: `web_search` (a search engine's result list) and `web_fetch` (one page as readable text).

Both run on this server for every model (a subscription sign-in, Antigravity, an API key, OpenRouter...), so no provider's own search is
needed, and neither needs a key. They only read, so they never show an approval card; what they bring back is marked untrusted like a
page in the browser.

Requests go out the way the server's other outward reads do (watchers.public_get, the rules of netguard.py): public http(s) only, the
standard port, no credentials in the URL, a host name that resolves to public addresses only (no loopback, LAN, Docker network, CGNAT,
link-local or cloud metadata), the connection pinned to the address that was checked, every redirect checked again (at most 3),
`FETCH_TIMEOUT` seconds and `MAX_BODY` bytes at most. The User-Agent says what it is (`USER_AGENT`); nothing pretends to be a browser.

Search: DuckDuckGo's HTML endpoint (html.duckduckgo.com, a form POST: no key, no JavaScript, a stable result markup), then Bing's RSS
results (www.bing.com/search?format=rss) when DuckDuckGo answers with its robot check or nothing. Requests to one engine are spaced
`SEARCH_SPACING` seconds apart, because rapid repeats are what makes a keyless endpoint refuse.

A page that needs JavaScript, a sign-in or a click to show its content, or that refuses plain requests, says so in the answer: that
is when the agent opens it in the browser instead.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlsplit

from ..agent import Ours, Tool
from ..agent.fence import data
from ..netguard import NetGuardError
from ..watchers import Node, Page, parse_html, public_get, text_of

USER_AGENT = "Mozilla/5.0 (compatible; Kelo/1.0; +https://kelo.chat)"
FETCH_TIMEOUT = 20.0
MAX_BODY = 2_000_000           # bytes read from one page
MAX_TEXT = 12_000              # characters of page text in one answer (the same as a browser page reading); `start` reads on
MAX_RESULTS = 10
SEARCH_SPACING = 1.0           # seconds between two requests to the same search engine
DDG_URL = "https://html.duckduckgo.com/html/"
BING_RSS = "https://www.bing.com/search?format=rss&q="

Fetch = Callable[..., Awaitable[Page]]


def _fetcher(transport: Any = None, resolver: Any = None, proxy: Callable[[], str | None] | None = None) -> Fetch:
    """`proxy`: asked at each read; a socks5h:// address while the VPN connector is on (vpn.py), else None (direct)."""
    async def fetch(url: str, **kw: Any) -> Page:
        return await public_get(url, transport=transport, resolver=resolver, max_body=MAX_BODY, user_agent=USER_AGENT,
                                allow_http=True, timeout=FETCH_TIMEOUT, proxy=proxy, **kw)
    return fetch


# ------------------------------------------------------------------------------------------------------------------- reading HTML
_SKIP = {"nav", "footer", "header", "aside", "form", "button", "select", "dialog"}


def _walk(node: Node):
    for c in node.children:
        if isinstance(c, Node):
            yield c
            yield from _walk(c)


def _classes(n: Node) -> set[str]:
    return set((n.attrs.get("class") or "").split())


def _main(root: Node) -> Node:
    """The page's main content when it marks one (<main>, <article>, role=main) with enough words in it, else the whole page."""
    for n in _walk(root):
        if n.tag in ("main", "article") or n.attrs.get("role") == "main":
            if len(text_of(n)) >= 400:
                return n
    return root


def _drop_chrome(node: Node) -> None:
    """Leave out menus, headers, footers and forms: the words a reader skips."""
    node.children = [c for c in node.children if not (isinstance(c, Node) and (c.tag in _SKIP or c.attrs.get("role") in
                                                                                  ("navigation", "banner", "contentinfo", "dialog")))]
    for c in node.children:
        if isinstance(c, Node):
            _drop_chrome(c)


def readable(body: str) -> tuple[str, str]:
    """(title, readable text) of an HTML page."""
    tree = parse_html(body)
    main = _main(tree.root)
    full = text_of(main)
    _drop_chrome(main)
    text = text_of(main)
    if len(text) < min(300, len(full) // 3):   # a page made mostly of what we dropped (e.g. a list of links): keep it all
        text = full
    return re.sub(r"\s+", " ", tree.title).strip(), text


def _needs_browser(body: str, text: str) -> bool:
    return len(text) < 200 and bool(re.search(r"<script\b", body[:200_000], re.I))


# ------------------------------------------------------------------------------------------------------------------- search engines
def parse_ddg(body: str) -> list[dict[str, str]]:
    """Results of DuckDuckGo's HTML page: [{title, url, snippet}], ads left out."""
    out: list[dict[str, str]] = []
    for n in _walk(parse_html(body).root):
        cls = _classes(n)
        if "result" not in cls or "result--ad" in cls or n.tag not in ("div", "article", "li"):
            continue
        link = next((a for a in _walk(n) if a.tag == "a" and "result__a" in _classes(a)), None)
        if link is None:
            continue
        href = link.attrs.get("href", "")
        if "uddg=" in href:
            href = parse_qs(urlsplit(href if "://" in href else "https:" + href).query).get("uddg", [""])[0]
        if not href.startswith(("http://", "https://")) or "duckduckgo.com/y.js" in href:
            continue
        snip = next((s for s in _walk(n) if "result__snippet" in _classes(s)), None)
        out.append({"title": text_of(link), "url": href, "snippet": text_of(snip) if snip is not None else ""})
    return out


def _robot_check(page: Page) -> bool:
    """DuckDuckGo's answer to too many requests: HTTP 202 with a robot check instead of results."""
    return page.status != 200 or "anomaly-modal" in page.body[:50_000]


_ITEM = re.compile(r"<item>(.*?)</item>", re.S)


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
    raw = m.group(1) if m else ""
    raw = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", raw.strip(), flags=re.S)
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw))   # XML escaping first, then the HTML inside it
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_rss(body: str) -> list[dict[str, str]]:
    """Items of an RSS result feed: [{title, url, snippet}]."""
    out = []
    for block in _ITEM.findall(body or ""):
        url = _tag(block, "link")
        if url.startswith(("http://", "https://")):
            out.append({"title": _tag(block, "title"), "url": url, "snippet": _tag(block, "description")})
    return out


class WebTools:
    def __init__(self, *, fetch: Fetch | None = None, language: Callable[[], str] = lambda: "",
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 proxy: Callable[[], str | None] | None = None):
        self.fetch = fetch or _fetcher(proxy=proxy)
        self.language = language      # e.g. "ko-KR": the user's language, sent as Accept-Language
        self.clock, self.sleep = clock, sleep
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _accept_language(self) -> str:
        lang = (self.language() or "").strip()
        base = lang.split("-")[0]
        if not lang:
            return "en;q=0.8"
        return f"{lang},{base};q=0.9,en;q=0.7" if base != lang else f"{lang},en;q=0.7"

    async def _spaced(self, engine: str, url: str, **kw: Any) -> Page:
        lock = self._locks.setdefault(engine, asyncio.Lock())
        async with lock:
            wait = self._last.get(engine, -1e9) + SEARCH_SPACING - self.clock()
            if wait > 0:
                await self.sleep(wait)
            try:
                return await self.fetch(url, language=self._accept_language(), **kw)
            finally:
                self._last[engine] = self.clock()

    async def search(self, query: str, count: int = 8) -> tuple[str, list[dict[str, str]]]:
        """(engine, results) from the first engine that answers with results; ("", []) when none did."""
        tried: list[str] = []
        try:
            page = await self._spaced("duckduckgo", DDG_URL, method="POST", data={"q": query, "b": "", "kl": ""})
            found = [] if _robot_check(page) else parse_ddg(page.body)
            if found:
                return "duckduckgo", found[:count]
            tried.append(f"duckduckgo (HTTP {page.status})")
        except NetGuardError as e:
            tried.append(f"duckduckgo ({e})")
        try:
            page = await self._spaced("bing", BING_RSS + quote_plus(query))
            found = parse_rss(page.body) if page.status == 200 else []
            if found:
                return "bing", found[:count]
            tried.append(f"bing (HTTP {page.status})")
        except NetGuardError as e:
            tried.append(f"bing ({e})")
        raise NetGuardError("no search engine answered: " + "; ".join(tried))

    # ------------------------------------------------------------------------------------------------------------------- tools
    async def web_search(self, args: dict[str, Any]) -> str:
        query = re.sub(r"\s+", " ", str(args.get("query") or "")).strip()[:400]
        if not query:
            return Ours("Error: query is empty.")
        count = max(1, min(MAX_RESULTS, int(args.get("count") or 8)))
        engine, found = await self.search(query, count)
        found_lines = [f"{i}. {r['title'][:200]}\n   {r['url']}" + (f"\n   {r['snippet'][:400]}" if r["snippet"] else "")
                       for i, r in enumerate(found, 1)]
        # the results are the sites' words (marked, so the loop fences them); the lines around them are ours
        return (f"Search results for {query!r} ({engine}):\n" + data("\n".join(found_lines))
                + "\nSnippets can be old or cut short: read the pages that matter with web_fetch before you give prices or facts.")

    async def web_fetch(self, args: dict[str, Any]) -> str:
        url = str(args.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.I):
            return Ours("Error: give a full http:// or https:// address.")
        start = max(0, int(args.get("start") or 0))
        page = await self.fetch(url, language=self._accept_language())
        ctype = (page.content_type or "").split(";")[0].strip().lower()
        if "html" in ctype or (not ctype and re.search(r"<\s*(html|body)\b", page.body[:5000], re.I)):
            title, text = readable(page.body)
        else:
            title, text = "", page.body.strip()
        notes = []
        if page.status >= 400:
            notes.append(f"[HTTP {page.status}: the site did not give this page to a plain request (it may refuse automated reading, "
                         "or the page is gone). Do not retry it right away; try another source, or open it in the browser if this page matters.]")
        elif _needs_browser(page.body, text):
            notes.append("[This page shows almost nothing without JavaScript. Open it in the browser (browser_open) to read it.]")
        total = len(text)
        part = text[start:start + MAX_TEXT]
        if start + MAX_TEXT < total:
            notes.append(f"[text cut at {start + MAX_TEXT} of {total} characters; call web_fetch again with start={start + MAX_TEXT} for more]")
        # the page's title and text are fenced; the address and the notes are ours, outside
        body = (f"Title: {title}\n\n" if title else "") + part
        return f"URL: {page.url}\n" + data(body) + "".join("\n" + n for n in notes)

    def tools(self) -> list[Tool]:
        return [
            Tool("web_search", "Search the web: results with title, address and snippet. Read the best ones with web_fetch. Results and articles are background: never offer a price, stock or link from them as something the user can buy now.",
                 {"type": "object", "properties": {"query": {"type": "string"},
                                                   "count": {"type": "integer", "description": f"results, 1-{MAX_RESULTS} (default 8)"}},
                  "required": ["query"]}, self.web_search, untrusted=True, source="web", read_only=True, trail="web"),
            Tool("web_fetch", "Read one public web page as plain text. A page that needs JavaScript or refuses: browser_open.",
                 {"type": "object", "properties": {"url": {"type": "string"},
                                                   "start": {"type": "integer", "description": "read on from this character"}},
                  "required": ["url"]},
                 self.web_fetch, untrusted=True, source="web", read_only=True, trail="web"),
        ]


def web_tools(*, language: Callable[[], str] = lambda: "", fetch: Fetch | None = None,
              proxy: Callable[[], str | None] | None = None) -> list[Tool]:
    return WebTools(fetch=fetch, language=language, proxy=proxy).tools()
