"""Kelo's work browser: a second browser that runs every scheduled and background tab, so a job never touches a tab the owner is looking at
or working in (owner decision 2026-11-18). Each work tab gets a context of its own there, filled with a copy of the main browser's cookies
when it opens (the sign-in copy) and closed with the tab; sign-ins the job made are copied back to the main browser when the tab closes
(the sign-in carry-back). Cookie values never leave the server's memory: never logged, never shown to the model.

No Playwright import at module level: the connection is made by the caller or by `ensure()` on first use, and tests pass fakes."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .sites import registrable

log = logging.getLogger("assistant.browser")

WORK_PREFIXES = ("schedule:", "task:")   # D2: schedules, watchers, checklist and Work-page jobs; chat tabs ("owner", a device id) and "buy:" stay in the main browser
COOKIE_KEYS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite", "partitionKey")
DOWN_NOTE = ("[The work browser (the separate browser Kelo uses for scheduled and background tabs) is not answering, so this step was not "
             "done. Do not use the owner's chat tab instead. Report the job as blocked and say the work browser is not running.]")


def work_tab(key: str) -> bool:
    """A tab that runs in the work browser (D2)."""
    return key.startswith(WORK_PREFIXES)


class WorkBrowserDown(Exception):
    """The work browser is configured but does not answer: the tab fails with DOWN_NOTE, never falls back to the main browser."""


# Card p-browser-signin-shared, spike S1 (tests/test_work_seed_live.py): a cookie put into a context before a document request is sent
# with that request, so a site's sign-in is copied in right before the tab's first document request to that site (True). False keeps
# the whole-jar copy at open, the reference's own cold-start fallback.
SEED_PER_SITE = True
SEED_FAILED_NOTE = ("[Kelo's browser could not load the owner's saved sign-in for this site, so the page may show you signed out, and "
                    "changes made here may not be kept.]")


def seed_signed_out_note(site: str, found: int, login: str) -> str:
    """What a job's tab is told on its first reading of `site` when the sign-in copy found no sign-in for it in the owner's browser:
    nothing at all (`found` 0), or cookies but no record of a sign-in there. `login`: a saved login for the site ("" = none)."""
    why = (f"the owner's browser holds nothing for {site}" if not found else
           f"{found} cookies were copied from the owner's browser, but there is no record of a sign-in to {site} there")
    how = (f"sign in with the saved login: click the site's own sign-in link or button, or call browser_login(name={login!r}, "
           "via=<the ref of that link>)" if login else
           "no login is saved for it: ask the owner for one (request_login), or as your instructions say for a missing sign-in")
    return f"[Not signed in to {site}: {why}. If this task needs an account on {site}, {how}. Do not guess sign-in addresses.]"


@dataclass(slots=True)
class WorkTab:
    context: Any
    signed: set[str] = field(default_factory=set)    # registrable domains the job signed in to (vault login, the owner's hands)
    seeded: dict[str, dict[tuple[str, str, str], str]] = field(default_factory=dict)   # site -> (name, domain, path) -> value as copied in
    seeded_storage: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)  # site -> origin -> {key: value} as copied in
    touched: set[str] = field(default_factory=set)   # registrable domains the tab went to (the write-back looks at them)
    signed_out: bool = False                         # research jobs: nothing is copied in, nothing is written back
    owner_had: dict[str, int] = field(default_factory=dict)   # site -> cookies the owner's browser held for it at the first copy (S3)
    told: set[str] = field(default_factory=set)               # sites whose "not signed in" note this tab was given (S3)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)   # one seed at a time: a second request waits for the first's cookies


def _cookie(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c[k] for k in COOKIE_KEYS if k in c}


def _domain_site(domain: str) -> str:
    return registrable(str(domain or "").lstrip(".").lower())


async def _quietly(make: Callable[[], Awaitable[Any]] | None) -> None:
    if make is None:
        return
    try:
        await make()
    except Exception:  # noqa: BLE001 - already closed or gone
        pass


async def seed_tab(wt: WorkTab | None, site: str, cookies: list[dict[str, Any]], storage: dict[str, dict[str, str]]) -> int:
    """seed_site for any WorkTab (a work-browser tab, or a named profile's own context in the main browser): one site's cookies and local
    storage into the tab's context, once per site. 0 when there is no tab, it is signed out, or the site is done."""
    site = registrable(str(site or "").lower())
    if wt is None or wt.signed_out or not site:
        return 0
    async with wt.lock:
        return await _seed_locked(wt, site, cookies, storage)


async def _seed_locked(wt: WorkTab, site: str, cookies: list[dict[str, Any]], storage: dict[str, dict[str, str]]) -> int:
    n = 0
    if site not in wt.seeded:
        wt.seeded[site] = {}
        mine = [_cookie(c) for c in cookies if c.get("name") and c.get("domain") and _domain_site(str(c["domain"])) == site]
        try:
            if mine:
                await wt.context.add_cookies(mine)
        except Exception:
            wt.seeded.pop(site, None)
            raise
        for c in mine:
            wt.seeded[site][(c["name"], c["domain"], c.get("path", ""))] = str(c.get("value", ""))
        n = len(mine)
    if site not in wt.seeded_storage:
        wt.seeded_storage[site] = {origin: dict(items) for origin, items in storage.items() if items}
        for origin, items in wt.seeded_storage[site].items():
            pairs = [[str(k), str(v)] for k, v in items.items()]
            await wt.context.add_init_script(
                script="(() => { if (location.origin !== %s) return; for (const [k, v] of %s) { try { if (localStorage.getItem(k) === null) "
                       "localStorage.setItem(k, v); } catch (e) {} } })();" % (_js(origin), _js(pairs)))
    return n


async def collect_changes(wt: WorkTab, read_storage: Callable[[Any, str], Awaitable[dict[str, dict[str, str]]]] | None = None
                          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """What the tab changed on the sites it went to (cookies, storage, sites), for a write-back: a cookie is listed when it is new or its
    value differs from the copy the tab started with ({"cookie": ..., "seeded": value or None}); a storage key likewise. Deletions are
    never listed. Nothing for a signed-out tab. The context stays open (the caller closes it)."""
    sites = set(wt.touched) | set(wt.signed)
    cookies: list[dict[str, Any]] = []
    storage: list[dict[str, Any]] = []
    try:
        if sites and not wt.signed_out:
            for c in await wt.context.cookies():
                site = _domain_site(str(c.get("domain") or ""))
                if site not in sites:
                    continue
                seeded = wt.seeded.get(site, {}).get((c.get("name"), c.get("domain"), c.get("path", "")))
                if seeded != str(c.get("value", "")):
                    cookies.append({"cookie": _cookie(c), "seeded": seeded})
            if read_storage is not None:
                for site in sites:
                    now = await read_storage(wt.context, site)
                    for origin, items in now.items():
                        before = wt.seeded_storage.get(site, {}).get(origin, {})
                        for k, v in items.items():
                            if before.get(k) != v:
                                storage.append({"site": site, "origin": origin, "key": k, "value": v, "seeded": before.get(k)})
    except Exception:  # noqa: BLE001 - nothing carried back then
        cookies, storage = [], []
    return cookies, storage, sites


class WorkBrowser:
    """The work browser's connection and its work tabs' contexts. One connection at a time; every new connection starts a new
    `generation` (the session forgets the work pages of an older one)."""

    def __init__(self, host: str, port: int, *, connect: Callable[[], Awaitable[Any]] | None = None,
                 new_context: Callable[[], Awaitable[Any]] | None = None):
        self.host, self.port = host, port
        self._connect = connect                 # -> the Playwright Browser over CDP (default: connect_over_cdp to host:port)
        self._new_context = new_context         # tests only; default: browser.new_context(no_viewport=True, accept_downloads=True)
        self.on_new_page: Callable[[str], Awaitable[Any]] | None = None   # set by server.py: VncScreens.work_window_opened
        self.on_connected: Callable[[str, int], Awaitable[Any]] | None = None   # set by server.py: BrowserSession.work_connected
        self.generation = 0
        self._pw: Any = None
        self._browser: Any = None
        self._cdp: Any = None
        self._tabs: dict[str, WorkTab] = {}

    def connected(self) -> bool:
        return self._browser is not None and bool(self._browser.is_connected())

    async def ensure(self) -> bool:
        """Connected (after a restart of the work browser it reconnects). False = down. On a NEW connection: every context but the
        default one and every page but one is closed (leftovers of an earlier server run), and the generation moves on."""
        if self.connected():
            return True
        try:
            if self._connect is not None:
                browser = await self._connect()
            else:
                from playwright.async_api import async_playwright   # the real connection only

                if self._pw is None:
                    self._pw = await async_playwright().start()
                ip = await asyncio.to_thread(socket.gethostbyname, self.host)   # Chrome's DevTools rejects other Host headers
                browser = await self._pw.chromium.connect_over_cdp(f"http://{ip}:{self.port}")
        except Exception as e:  # noqa: BLE001 - down is down, whatever the reason
            log.info("work browser: not answering (%s)", type(e).__name__)
            return False
        self._browser, self._cdp, self._tabs = browser, None, {}
        contexts = list(getattr(browser, "contexts", None) or [])
        for ctx in contexts[1:]:
            await _quietly(ctx.close)
        default = contexts[0] if contexts else None
        for page in list(getattr(default, "pages", None) or [])[1:]:
            await _quietly(page.close)
        self.generation += 1
        if self.on_connected is not None:
            try:
                await self.on_connected(self.host, self.port)   # its frames and workers (their tab's language), its own connection
            except Exception:  # noqa: BLE001 - the work browser works without it
                log.warning("work browser: its frames and workers are not covered")
        return True

    def tab(self, key: str) -> WorkTab | None:
        return self._tabs.get(key)

    async def open_context(self, key: str, main_cookies: list[dict[str, Any]], *, signed_out: bool = False) -> Any:
        """A context of the work browser for this tab. With SEED_PER_SITE it starts empty and seed_site fills it per site; otherwise it
        gets a copy of the owner's cookies now (the whole-jar copy). signed_out: nothing is copied, ever. A tab that already has one keeps
        it. WorkBrowserDown when the work browser does not answer."""
        if not await self.ensure():
            raise WorkBrowserDown(DOWN_NOTE)
        if (wt := self._tabs.get(key)) is not None:
            return wt.context
        ctx = await (self._new_context() if self._new_context is not None else self._browser.new_context(no_viewport=True, accept_downloads=True))
        wt = WorkTab(context=ctx, signed_out=signed_out)
        self._tabs[key] = wt
        if SEED_PER_SITE or signed_out:
            return ctx
        cookies = [_cookie(c) for c in main_cookies if c.get("name") and c.get("domain")]
        if cookies:
            await ctx.add_cookies(cookies)
        for c in cookies:
            wt.seeded.setdefault(_domain_site(c["domain"]), {})[(c["name"], c["domain"], c.get("path", ""))] = str(c.get("value", ""))
        return ctx

    async def seed_site(self, key: str, site: str, cookies: list[dict[str, Any]], storage: dict[str, dict[str, str]]) -> int:
        """Copies one site's sign-in into this tab's context: its cookies, and its local storage (only keys the page does not have yet,
        when the page of that origin loads). Once per site and tab; 0 when the tab is unknown, signed out, or the site is done. Returns
        the number of cookies copied in."""
        return await seed_tab(self._tabs.get(key), site, cookies, storage)

    def note_touched(self, key: str, site: str) -> None:
        """The tab went to `site` (a registrable domain): its sign-in is written back when the tab closes (when it was seeded)."""
        wt = self._tabs.get(key)
        if wt is not None and site:
            wt.touched.add(registrable(site.lower()))

    async def new_private_context(self) -> Any:
        """A private context in the work browser (private browsing on a work tab: nothing kept after it closes)."""
        if not await self.ensure():
            raise WorkBrowserDown(DOWN_NOTE)
        ctx = await (self._new_context() if self._new_context is not None else self._browser.new_context(no_viewport=True, accept_downloads=True))
        return ctx

    def note_signed(self, key: str, site: str) -> None:
        """The job signed in to `site` in this tab: its cookies go back to the main browser when the tab closes."""
        wt = self._tabs.get(key)
        if wt is not None and site:
            wt.signed.add(registrable(site.lower()))

    async def close(self, key: str, read_storage: Callable[[Any, str], Awaitable[dict[str, dict[str, str]]]] | None = None
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
        """Close this tab's context. Returns what the job changed on the sites it went to, for the write-back of card A4:
        (cookies, storage, sites). A cookie is listed when it is new or its value differs from the copy the tab started with
        ({"cookie": ..., "seeded": value or None}); a storage key likewise ({"site","origin","key","value","seeded"}). Deletions are never
        listed. `read_storage(context, site)` gives the local storage of the site as the context holds it now. A signed-out tab returns
        nothing. The context is closed in any case."""
        wt = self._tabs.pop(key, None)
        if wt is None:
            return [], [], set()
        try:
            cookies, storage, sites = await collect_changes(wt, read_storage)
        finally:
            await _quietly(wt.context.close)
        return cookies, storage, sites

    async def forget(self, site: str) -> None:
        """Clear one site's cookies in every open work context (browser_forget_site)."""
        reg = registrable(site.lower())
        for wt in self._tabs.values():
            try:
                kept = [c for c in await wt.context.cookies() if _domain_site(str(c.get("domain") or "")) != reg]
                await wt.context.clear_cookies()
                await wt.context.add_cookies([_cookie(c) for c in kept if c.get("name") and c.get("domain")])
            except Exception:  # noqa: BLE001 - a context that went away
                pass

    async def browser_cdp(self) -> Any:
        """The work browser's own browser-wide DevTools session (downloads)."""
        if not await self.ensure():
            raise WorkBrowserDown(DOWN_NOTE)
        if self._cdp is None:
            self._cdp = await self._browser.new_browser_cdp_session()
        return self._cdp

    async def shutdown(self) -> None:
        if self._pw is not None:
            await _quietly(self._pw.stop)
        self._pw = self._browser = self._cdp = None
        self._tabs = {}


def _js(value: Any) -> str:
    import json

    return json.dumps(value)
