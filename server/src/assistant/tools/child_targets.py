"""The frames and workers of a tab (docs/browser.md "Identity"): the language of their tab.

A tab's own DevTools session covers the tab's main frame only. Cross-site frames (out-of-process iframes), dedicated workers and shared or
service workers are separate DevTools targets, so a child of a tab in another language would send the browser's own language. This class
holds one DevTools connection to the browser, auto-attaches to every child target (paused on start, flattened, and again under each
child), gives a child of a tab in another language that tab's language, and always lets it run again (Runtime.runIfWaitingForDebugger),
even when a step fails. Pages are only passed through: the tab's own session does them.

It is the same attach loop as browser_identity.Identity (the Chromium builds' desktop identity), which it extends."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..browser_devices import accept_language
from ..browser_identity import Identity, identity_for as desktop_override

log = logging.getLogger(__name__)

# The target types a language change reaches; a page is never changed here (its own session does that).
FRAME_TYPE = "iframe"
WORKER_TYPES = ("worker", "shared_worker", "service_worker")


class ChildTargets(Identity):
    """`locale_for(target_info, parent_target_id)` gives the locale ("ko-KR"; "" = the browser's own) of a child's tab: the child
    then gets the browser's own identity with that tab's Accept-Language and locale (the tab's language, see browser.py)."""

    def __init__(self, ws: Any, locale_for: Callable[[dict[str, Any], str], str] | None = None) -> None:
        super().__init__(ws, {})
        self.locale_for = locale_for
        self._target_of: dict[str, str] = {}     # session id -> its target id (to name the parent of its children)
        self.failures: list[str] = []            # what a step failed on (kept for tests and for a log line, never the page)
        self._full = ""                          # the browser's full Chrome version, read once
        self._ua = ""                            # the browser's own User-Agent, read with it
        self._reader: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Begin reading the connection and auto-attach the browser's targets. Returns at once."""
        self._reader = asyncio.create_task(self._read())
        await self.auto_attach()

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001 - the browser went away: nothing to close
            pass

    async def attached(self, ev: dict[str, Any], parent: str | None = None) -> None:  # type: ignore[override]
        session = str(ev.get("sessionId") or "")
        info = ev.get("targetInfo") or {}
        kind = str(info.get("type") or "")
        parent_target = self._target_of.get(parent or "", "") if parent else ""
        self._target_of[session] = str(info.get("targetId") or "")
        try:
            await self._desktop_child(info, parent_target, session, kind)
            try:
                await self.auto_attach(session)   # the child's own frames and workers
            except Exception:  # noqa: BLE001 - a target without auto-attach (a worker): nothing below it
                pass
        finally:
            if ev.get("waitingForDebugger"):
                try:
                    await self.send("Runtime.runIfWaitingForDebugger", None, session)
                except Exception:  # noqa: BLE001 - the target went away
                    pass

    async def _desktop_child(self, info: dict[str, Any], parent: str, session: str, kind: str) -> None:
        """A cross-site frame or a worker of a tab in another language: the browser's own User-Agent and client hints (this
        Chrome's, as browser_identity.identity_for gives them) with that tab's Accept-Language and locale. A child whose tab has no
        language is left alone (only resumed)."""
        if self.locale_for is None or kind not in (FRAME_TYPE, *WORKER_TYPES):
            return
        try:
            locale = str(self.locale_for(info, parent) or "")
        except Exception:  # noqa: BLE001 - an error in the answer: the child keeps the browser's own language
            locale = ""
        if not locale:
            return
        try:
            ua, full = await self._browser_identity()
        except Exception:  # noqa: BLE001 - no version: the child keeps the browser's own language (a failure is recorded)
            self.failures.append("version")
            return
        method = "Emulation.setUserAgentOverride" if kind == FRAME_TYPE else "Network.setUserAgentOverride"
        params = {"userAgent": ua, "acceptLanguage": accept_language(locale),
                  "userAgentMetadata": desktop_override(full)["userAgentMetadata"]}
        await self._step("desktop ua", method, params, session)
        await self._step("desktop locale", "Emulation.setLocaleOverride", {"locale": locale}, session)

    async def _step(self, what: str, method: str, params: dict[str, Any] | None, session: str) -> None:
        try:
            await self.send(method, params, session)
        except Exception as e:  # noqa: BLE001 - one step failing does not stop the others or the child
            self.failures.append(f"{what}: {type(e).__name__}")

    async def _browser_identity(self) -> tuple[str, str]:
        """(the browser's own User-Agent, its full Chrome version), read once."""
        if not self._full:
            got = await self.send("Browser.getVersion", None)
            full = str((got or {}).get("product") or "").split("/")[-1]
            if not full[:1].isdigit():
                raise RuntimeError("no Chrome version")
            self._ua = str((got or {}).get("userAgent") or "")
            self._full = full
        return self._ua, self._full
