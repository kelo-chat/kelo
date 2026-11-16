"""Page check source wrapping watchers.py's fetch and evaluate."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from ...watchers import evaluate, public_get
from ..checks import Reading, Source


class PageSource(Source):
    kind: ClassVar[str] = "page"

    def __init__(self, watchers: Any = None,
                 fetch_fn: Callable[[str], Awaitable[Any]] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.watchers = watchers
        self.fetch_fn = fetch_fn
        self.clock = clock

    def available(self) -> bool:
        return True

    def covers(self, refs: dict[str, str]) -> bool:
        return bool(refs.get("url"))

    async def read(self, refs: dict[str, str]) -> Reading:
        url = refs.get("url")
        if not url:
            return Reading(fields={}, at=self.clock(), ok=False, detail="no url in refs")

        spec = {
            "selector": refs.get("selector") or "",
            "regex": refs.get("regex") or "",
            "mode": refs.get("mode") or "changed",
            "match": refs.get("match") or "",
            "threshold": refs.get("threshold") or "0",
            "last_value": refs.get("last_value") or "",
        }

        try:
            html = await self._fetch_html(url)
            res = evaluate(spec, html)
            field_name = refs.get("field") or "value"
            if res.get("ok"):
                val = res.get("value")
                if isinstance(val, str) and len(val) > 17 and val[16] == ":":
                    val = val[17:]
                return Reading(
                    fields={field_name: val},
                    at=self.clock(),
                    ok=True,
                    detail=str(res.get("detail") or ""),
                )
            return Reading(
                fields={},
                at=self.clock(),
                ok=False,
                detail=str(res.get("error") or res.get("detail") or "evaluation failed"),
            )
        except Exception as e:
            return Reading(fields={}, at=self.clock(), ok=False, detail=str(e))

    async def _fetch_html(self, url: str) -> str:
        if self.fetch_fn is not None:
            res = await self.fetch_fn(url)
            if hasattr(res, "body"):
                return str(res.body)
            return str(res)

        if self.watchers is not None and hasattr(self.watchers, "fetch"):
            page = await self.watchers.fetch(url)
            return str(page.body if hasattr(page, "body") else page)

        page = await public_get(url)
        return str(page.body if hasattr(page, "body") else page)
