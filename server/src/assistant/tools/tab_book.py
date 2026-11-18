"""Which page of the browser each tab key had (its DevTools target id), kept in the store so a restarted Kelo server finds the same pages
again in a browser that kept running (job_cuts.py: a cut job goes on in its own tab). Private tabs are not kept: their context ends with
the connection. Stored as one JSON value in the store's meta table."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger("uvicorn.error")


class TabBook:
    META = "browser_tabs"   # store meta, JSON {key: {"target": str, "url": str, "at": float}}

    def __init__(self, store: Any, clock: Any = time.time):
        self.store, self.clock = store, clock

    def all(self) -> dict[str, dict[str, Any]]:
        try:
            got = json.loads(self.store.meta(self.META) or "{}")
        except ValueError:
            return {}
        return {str(k): dict(v) for k, v in got.items() if isinstance(v, dict)} if isinstance(got, dict) else {}

    def _write(self, book: dict[str, dict[str, Any]]) -> None:
        self.store.set_meta(self.META, json.dumps(book, ensure_ascii=False) if book else "")

    def save(self, key: str, target: str, url: str = "") -> None:
        if not key or not target:
            return
        book = self.all()
        book[key] = {"target": str(target), "url": str(url or "")[:500], "at": self.clock()}
        try:
            self._write(book)
        except Exception:  # noqa: BLE001 - the book is a help for a restart; a tab never fails because of it
            log.exception("could not keep the tab book entry of %s", key)

    def forget(self, key: str) -> None:
        book = self.all()
        if key in book:
            del book[key]
            try:
                self._write(book)
            except Exception:  # noqa: BLE001
                log.exception("could not forget the tab book entry of %s", key)
