"""Outlook calendar events for the proactive pass and the morning briefing (Microsoft Graph, the same sign-in as the Outlook connector).

Reads `GET /v1.0/me/calendarView` for a window with the connection the owner made in the Connectors tab (vault item "microsoft-api",
its access token renewed first when it has run out). Read only; the Graph scopes of the connection decide whether it works ("Mail,
calendar and contacts" and "Read only" both include calendar reads). Events come back in the same shape as GoogleCalendar.upcoming:
[{"id", "title", "start": unix, "where"}], all-day events left out.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from .mcp_oauth import open_token
from .netguard import NetGuardError, fetch
from .store import Store
from .vault import VaultError, use_credential

log = logging.getLogger(__name__)

ITEM = "microsoft-api"
HOST = "graph.microsoft.com"
MAX_EVENTS = 50
PAGE_EVENTS = 250     # per page when a long span is searched (calendar_events with query)


class MicrosoftCalendarError(Exception):
    """The Outlook calendar could not be read (not connected, signed out, refused). The message is safe to show."""


class MicrosoftCalendar:
    def __init__(self, store: Store, *, fetcher: Callable[..., Any] = fetch):
        self.store, self.fetcher = store, fetcher

    def connected(self) -> bool:
        item = self.store.credential(ITEM)
        return bool(item and item.get("has_secret") and item.get("kind") == "api")

    async def upcoming(self, hours: float = 24) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        return await self.between(now, now + timedelta(hours=hours))

    async def between(self, start: datetime, end: datetime, *, all_day: bool = False, tool: str = "proactive",
                      pages: int = 1) -> list[dict[str, Any]]:
        """Events overlapping [start, end) (all-day ones only with `all_day`), in the shape of upcoming() plus "end" and "all_day";
        `pages` > 1 follows Graph's next pages (a long search span), PAGE_EVENTS each."""
        if not self.connected():
            return []
        query = urlencode({
            "startDateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$select": "id,subject,start,end,location,isAllDay,isCancelled",
            "$orderby": "start/dateTime",
            "$top": str(MAX_EVENTS if pages <= 1 else PAGE_EVENTS),
        })
        url = f"https://{HOST}/v1.0/me/calendarView?{query}"
        rows: list[Any] = []
        for _ in range(max(1, pages)):
            try:
                cred = await open_token(self.store, use_credential(self.store, ITEM, tool=tool, kind="api", detail="calendar events"), self.fetcher)
                reply = await self.fetcher("GET", url, allowed_hosts=[HOST],
                                           headers={"Authorization": f"Bearer {cred['secret']}", "Prefer": 'outlook.timezone="UTC"',
                                                    "Accept": "application/json"})
            except (VaultError, NetGuardError) as e:
                raise MicrosoftCalendarError(str(e)) from None
            if reply.status != 200:
                raise MicrosoftCalendarError(f"Outlook calendar answered HTTP {reply.status}")
            try:
                answer = json.loads(reply.body)
            except ValueError:
                break
            rows += answer.get("value", []) if isinstance(answer.get("value"), list) else []
            nxt = str(answer.get("@odata.nextLink") or "")
            if not nxt.startswith(f"https://{HOST}/"):
                break
            url = nxt
        return events_from(rows, all_day=all_day)


def events_from(rows: list[dict[str, Any]], *, all_day: bool = False) -> list[dict[str, Any]]:
    """Graph rows as [{"id", "title", "start", "end"?, "where", "all_day", "day"? (an all-day event's date)}]."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("isCancelled") or (r.get("isAllDay") and not all_day):
            continue
        start = str((r.get("start") or {}).get("dateTime") or "")
        try:
            when = datetime.fromisoformat(start.split(".")[0]).replace(tzinfo=timezone.utc)   # asked for UTC (Prefer header)
        except ValueError:
            continue
        try:
            stop = datetime.fromisoformat(str((r.get("end") or {}).get("dateTime") or "").split(".")[0]).replace(tzinfo=timezone.utc)
        except ValueError:
            stop = None
        place = (r.get("location") or {}).get("displayName") or ""
        out.append({"id": f"outlook:{r.get('id', '')}", "title": str(r.get("subject") or "")[:200], "start": when.timestamp(), "where": str(place)[:200],
                    "all_day": bool(r.get("isAllDay")), **({"end": stop.timestamp()} if stop and stop >= when else {}),
                    **({"day": start[:10]} if r.get("isAllDay") else {})})
    return out
