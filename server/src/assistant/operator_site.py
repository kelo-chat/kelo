"""The main copy's link to the site copy (site_only.py, docs/site-only.md): the operator's lists and answers are read and changed on
the site copy through the operator link key (KELO_OPERATOR_SITE_KEY, kept only in this server's .env), and the site's events are
polled so the operator hears about each one once, in the owner's language.

`OperatorSite.call` builds the request itself from a fixed path (no caller passes a path), over https to the one host set in
KELO_OPERATOR_SITE_URL. `OperatorSiteWatch.tick` runs every minute (server.py) and only when the link is set.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from . import i18n, netguard
from .netguard import NetGuardError, Reply
from .operator_notices import render
from .store import Store

log = logging.getLogger("uvicorn.error")
CURSOR = "operator_site:cursor"      # the id of the last site event this server has heard about (store meta)
PAGE = 100
TIMEOUT = 15.0


class OperatorSiteDown(Exception):
    """The site copy cannot be reached, or it refused this server's key (`code` says which)."""

    def __init__(self, code: str, status: int = 0) -> None:
        super().__init__(code)
        self.code, self.status = code, status


class OperatorSite:
    def __init__(self, url: str, key: str, *, fetcher: Callable[..., Any] = netguard.fetch) -> None:
        self.url, self.key, self.fetcher = (url or "").rstrip("/"), key or "", fetcher

    def linked(self) -> bool:
        return bool(self.url and self.key)

    async def call(self, method: str, path: str, *, query: dict[str, str] | None = None, body: Any = None) -> tuple[int, Any]:
        """(status, JSON answer) of the site copy. Raises OperatorSiteDown when it cannot be reached or refuses the key (401/403)."""
        if not self.linked():
            raise OperatorSiteDown("operator_site_down")
        full = self.url + path + (f"?{urlencode(query)}" if query else "")
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json", "Accept-Language": i18n.current()}
        content = None
        if body is not None:
            content = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            reply: Reply = await self.fetcher(method, full, allowed_hosts=[urlsplit(self.url).hostname or ""], headers=headers,
                                              content=content, timeout=TIMEOUT)
        except NetGuardError as e:
            raise OperatorSiteDown("operator_site_down") from e
        except Exception as e:  # noqa: BLE001 - a network error is "down", whatever its class
            raise OperatorSiteDown("operator_site_down") from e
        if reply.status in (401, 403):
            raise OperatorSiteDown("operator_site_key", reply.status)
        try:
            data = json.loads(reply.body) if reply.body else None
        except ValueError:
            data = None
        return reply.status, data


class OperatorSiteWatch:
    """Each site event becomes one notification here, in this server's owner language; the cursor is saved after each one, so a
    restart never repeats a notice. The first run only sets the cursor (the site's old sign-ups are not announced)."""

    def __init__(self, site: OperatorSite, store: Store, activity: Any, tz: Callable[[], str],
                 lang: Callable[[], str | None] = i18n.owner_lang) -> None:
        self.site, self.store, self.activity, self.tz, self.lang = site, store, activity, tz, lang

    async def tick(self) -> int:
        """Notifications made in this run."""
        if not self.site.linked():
            return 0
        try:
            return await self._catch_up()
        except OperatorSiteDown as e:
            log.warning("operator site: %s", e.code)   # the next tick asks again from the same cursor
            return 0

    async def _catch_up(self) -> int:
        text = self.store.meta(CURSOR)
        if not text:                                       # first run: the cursor is the site's newest event; nothing is announced
            _, body = await self.site.call("GET", "/api/operator/events", query={"after": "0", "limit": "1"})
            self.store.set_meta(CURSOR, str(int((body or {}).get("last", 0)) if isinstance(body, dict) else 0))
            return 0
        cursor, made = int(text), 0
        while True:
            _, body = await self.site.call("GET", "/api/operator/events", query={"after": str(cursor), "limit": str(PAGE)})
            if not isinstance(body, dict):
                return made
            last = int(body.get("last", 0) or 0)
            if last < cursor:                              # the site's database came back older: start again from its newest
                self.store.set_meta(CURSOR, str(last))
                return made
            events = body.get("events") or []
            for e in events:
                cursor = int(e["id"])
                made += self._tell(e)
                self.store.set_meta(CURSOR, str(cursor))   # saved after each one: a crash never repeats a notice
            if len(events) < PAGE:
                return made

    def _tell(self, event: dict[str, Any]) -> int:
        rendered = render(str(event.get("kind", "")), event.get("data") or {}, tz=self.tz(), lang=self.lang())
        if rendered is None:                               # a kind this server does not know: skipped (the cursor moves on)
            return 0
        kind, title, body = rendered
        try:
            self.activity.notify(kind, title, body)
        except Exception as e:  # noqa: BLE001 - a failed notice is logged by type; the cursor still moves on
            log.warning("operator site: a notice failed (%s)", type(e).__name__)
            return 0
        return 1
