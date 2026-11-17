"""FlightAware AeroAPI flight status source with a monthly call allowance.

The allowance is a setting (Settings.aeroapi_allowance, env ASSISTANT_AEROAPI_ALLOWANCE) whose default, FREE_PER_MONTH, is the free
amount of FlightAware's personal tier as published when this was written (500 result sets a month); an owner on another plan sets
their own number. Calls are counted per month in follow_usage ("flight_aeroapi"). At ALLOWANCE_STOP (90 %) of the allowance the source
stops: `paused()` says so, the runner uses the next flight source that covers the flight (the Incheon one), and a check nobody else
covers waits for the next month and shows that it is paused. The owner is told once per month (follow_problem). Nothing is billed by
surprise: the other 10 % absorbs calls that were already under way when the limit was reached.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from ...i18n import t
from ...keyring_client import PLACEHOLDER
from ...netguard import Reply, fetch
from ..checks import Reading, Source

AEROAPI_HOST = "aeroapi.flightaware.com"
USAGE_NAME = "flight_aeroapi"
FREE_PER_MONTH = 500   # the default allowance (see above)
ALLOWANCE_STOP = 0.9


def stop_at(allowance: int) -> int:
    """The number of calls in a month at which lookups stop (90 % of the allowance)."""
    return int(allowance * ALLOWANCE_STOP + 1e-9)


class AeroApiFlights(Source):
    kind: ClassVar[str] = "flight_status"

    def __init__(self, key: str | None = None,
                 key_fn: Callable[[], str | None] | None = None,
                 store: Any = None,
                 follow_store: Any = None,
                 fetcher: Callable[..., Awaitable[Reply]] = fetch,
                 notify: Callable[[str, str, str], Any] | None = None,
                 clock: Callable[[], float] = time.time,
                 allowance: int | Callable[[], int] = FREE_PER_MONTH) -> None:
        self._allowance = allowance
        self._explicit_key = key
        self.key_fn = key_fn
        self.store = store
        self.follow_store = follow_store
        self.fetcher = fetcher
        self.notify = notify
        self.clock = clock

    def _key(self) -> str | None:
        if self._explicit_key:
            return self._explicit_key
        if self.key_fn:
            k = self.key_fn()
            if k:
                return k
        if self.store is not None:
            if hasattr(self.store, "credential"):
                c = self.store.credential("flightaware")
                if c and c.get("secret") and c["secret"] != PLACEHOLDER:
                    return str(c["secret"])
            if hasattr(self.store, "key"):
                k = self.store.key("flightaware")
                if k:
                    return str(k)
        return None

    def _month(self) -> str:
        return time.strftime("%Y-%m", time.gmtime(self.clock()))

    def _get_usage(self) -> int:
        fs = self.follow_store
        if fs is not None and hasattr(fs, "get_usage"):
            return fs.get_usage(USAGE_NAME, self._month())
        return 0

    def allowance(self) -> int:
        """This month's allowance: the setting, or the default when it is missing or not a number."""
        try:
            value = self._allowance() if callable(self._allowance) else self._allowance
            return max(0, int(value))
        except (TypeError, ValueError):
            return FREE_PER_MONTH

    def allowance_exceeded(self) -> bool:
        return self._get_usage() >= stop_at(self.allowance())

    def available(self) -> bool:
        return bool(self._key())   # a used-up allowance is `paused`, not unavailable: plans made now still hold next month

    def covers(self, refs: dict[str, str]) -> bool:
        return bool(refs.get("flight"))

    def paused(self, refs: dict[str, str]) -> str:
        """The reason lookups are stopped for the month (90 % of the allowance used), else "". Tells the owner once per month."""
        if not self.covers(refs) or not self._key() or not self.allowance_exceeded():
            return ""
        self._tell_allowance_once()
        return t("Flight lookups are paused until next month ({used} of {allowance} calls used).",
                 used=self._get_usage(), allowance=self.allowance())

    def usage(self) -> dict[str, Any] | None:
        if self.follow_store is None:
            return None
        allowance = self.allowance()
        return {"source": USAGE_NAME, "month": self._month(), "used": self._get_usage(), "allowance": allowance,
                "stop_at": stop_at(allowance), "paused": self.allowance_exceeded()}

    async def read(self, refs: dict[str, str]) -> Reading:
        key = self._key()
        if not key:
            return Reading(fields={}, at=self.clock(), ok=False, detail="flightaware key not available")

        if self.allowance_exceeded():
            self._tell_allowance_once()
            return Reading(fields={}, at=self.clock(), ok=False,
                           detail=f"FlightAware call allowance reached {int(ALLOWANCE_STOP * 100)}%")

        flight_id = str(refs.get("flight") or "").strip().upper()
        if not flight_id:
            return Reading(fields={}, at=self.clock(), ok=False, detail="no flight identifier in refs")

        url = f"https://{AEROAPI_HOST}/aeroapi/flights/{flight_id}"
        try:
            r = await self.fetcher("GET", url, allowed_hosts=[AEROAPI_HOST],
                                   headers={"x-apikey": key, "Accept": "application/json"})
            if self.follow_store is not None and hasattr(self.follow_store, "count_usage"):
                calls = self.follow_store.count_usage(USAGE_NAME, self._month())
                if calls >= stop_at(self.allowance()):
                    self._tell_allowance_once()

            if r.status != 200:
                return Reading(fields={}, at=self.clock(), ok=False, detail=f"HTTP {r.status} from FlightAware")
            data = json.loads(r.body)
            fields = self._parse_flight_data(data, flight_id)
            if fields is None:
                return Reading(fields={}, at=self.clock(), ok=False, detail=f"Flight {flight_id} not found in AeroAPI")
            return Reading(fields=fields, at=self.clock(), ok=True)
        except Exception as e:
            return Reading(fields={}, at=self.clock(), ok=False, detail=str(e))

    def _tell_allowance_once(self) -> None:
        fs = self.follow_store
        month = self._month()
        if fs is not None and hasattr(fs, "notified_allowance"):
            if fs.notified_allowance(USAGE_NAME, month):
                return
            fs.set_notified_allowance(USAGE_NAME, month, True)

        if self.notify is not None:
            self.notify(
                "follow_problem",
                "FlightAware",
                t("{pct}% of this month's FlightAware allowance is used: flight lookups with it are paused until next month, so nothing is billed.",
                  pct=int(ALLOWANCE_STOP * 100)),
            )

    def _parse_flight_data(self, data: Any, flight_id: str) -> dict[str, Any] | None:
        flights = data.get("flights") if isinstance(data, dict) else data
        if not isinstance(flights, list) or not flights:
            if isinstance(data, dict) and ("gate" in data or "gate_origin" in data or "status" in data):
                flights = [data]
            else:
                return None

        # Take the most relevant flight
        f = flights[0]
        if not isinstance(f, dict):
            return None

        gate = f.get("gate_origin") or f.get("gate") or ""
        terminal = f.get("terminal_origin") or f.get("terminal") or ""
        dep_time = f.get("estimated_out") or f.get("scheduled_out") or f.get("departure_time") or ""
        status = f.get("status") or ""

        return {
            "gate": str(gate),
            "terminal": str(terminal),
            "departure_time": str(dep_time),
            "status": str(status),
            "cancelled": f.get("cancelled") is True,   # AeroAPI's own boolean
        }
