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


@dataclass(slots=True)
class WorkTab:
    context: Any
    signed: set[str] = field(default_factory=set)    # registrable domains the job signed in to (vault login, the owner's hands)
    stored: set[str] = field(default_factory=set)    # registrable domains whose local storage was already copied in


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


class WorkBrowser:
    """The work browser's connection and its work tabs' contexts. One connection at a time; every new connection starts a new
    `generation` (the session forgets the work pages of an older one)."""

    def __init__(self, host: str, port: int, *, connect: Callable[[], Awaitable[Any]] | None = None,
                 new_context: Callable[[], Awaitable[Any]] | None = None,
                 gpu: Callable[[Any], Awaitable[None]] | None = None):
        self.host, self.port = host, port
        self._connect = connect                 # -> the Playwright Browser over CDP (default: connect_over_cdp to host:port)
        self._new_context = new_context         # tests only; default: browser.new_context(no_viewport=True, accept_downloads=True)
        self._gpu = gpu                         # BrowserSession._install_gpu: the same GPU report on every work context
        self.on_new_page: Callable[[str], Awaitable[Any]] | None = None   # set by server.py: VncScreens.work_window_opened
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
        return True

    def tab(self, key: str) -> WorkTab | None:
        return self._tabs.get(key)

    async def open_context(self, key: str, main_cookies: list[dict[str, Any]]) -> Any:
        """A context of the work browser for this tab, with a copy of the owner's cookies (the sign-in copy). A tab that already has one
        keeps it. WorkBrowserDown when the work browser does not answer."""
        if not await self.ensure():
            raise WorkBrowserDown(DOWN_NOTE)
        if (wt := self._tabs.get(key)) is not None:
            return wt.context
        ctx = await (self._new_context() if self._new_context is not None else self._browser.new_context(no_viewport=True, accept_downloads=True))
        if self._gpu is not None:
            await self._gpu(ctx)
        cookies = [_cookie(c) for c in main_cookies if c.get("name") and c.get("domain")]
        if cookies:
            await ctx.add_cookies(cookies)
        self._tabs[key] = WorkTab(context=ctx)
        return ctx

    async def new_private_context(self) -> Any:
        """A private context in the work browser (private browsing on a work tab: nothing kept after it closes)."""
        if not await self.ensure():
            raise WorkBrowserDown(DOWN_NOTE)
        ctx = await (self._new_context() if self._new_context is not None else self._browser.new_context(no_viewport=True, accept_downloads=True))
        if self._gpu is not None:
            await self._gpu(ctx)
        return ctx

    def note_signed(self, key: str, site: str) -> None:
        """The job signed in to `site` in this tab: its cookies go back to the main browser when the tab closes."""
        wt = self._tabs.get(key)
        if wt is not None and site:
            wt.signed.add(registrable(site.lower()))

    async def copy_storage(self, key: str, state: dict[str, Any]) -> None:
        """A site's local storage (from the main browser) for this tab's context, once per site: it is set when the page of that origin
        loads, and only keys the page does not have yet. Cookies are not copied here (open_context did that)."""
        wt = self._tabs.get(key)
        site = registrable(str(state.get("host") or "").lower())
        if wt is None or not site or site in wt.stored:
            return
        wt.stored.add(site)
        for o in state.get("origins") or []:
            items = [[str(k), str(v)] for k, v in o.get("localStorage") or []]
            if not items:
                continue
            await wt.context.add_init_script(
                script="(() => { if (location.origin !== %s) return; for (const [k, v] of %s) { try { if (localStorage.getItem(k) === null) "
                       "localStorage.setItem(k, v); } catch (e) {} } })();" % (_js(o["origin"]), _js(items)))

    async def close(self, key: str) -> tuple[list[dict[str, Any]], set[str]]:
        """Close this tab's context. Returns (the cookies of the sites the job signed in to, those sites). Reading the cookies may fail;
        the context is closed in any case."""
        wt = self._tabs.pop(key, None)
        if wt is None:
            return [], set()
        cookies: list[dict[str, Any]] = []
        sites = set(wt.signed)
        try:
            if sites:
                cookies = [c for c in await wt.context.cookies() if _domain_site(str(c.get("domain") or "")) in sites]
        except Exception:  # noqa: BLE001 - nothing carried back then
            cookies = []
        finally:
            await _quietly(wt.context.close)
        return [_cookie(c) for c in cookies], sites

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
