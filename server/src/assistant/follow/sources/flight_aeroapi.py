"""FlightAware AeroAPI flight status source with monthly free allowance counter."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from ...keyring_client import PLACEHOLDER
from ...netguard import Reply, fetch
from ..checks import Reading, Source

AEROAPI_HOST = "aeroapi.flightaware.com"
FREE_PER_MONTH = 500  # placeholder constant: the owner checks FlightAware's current personal-tier allowance
ALLOWANCE_STOP = 0.9


class AeroApiFlights(Source):
    kind: ClassVar[str] = "flight_status"

    def __init__(self, key: str | None = None,
                 key_fn: Callable[[], str | None] | None = None,
                 store: Any = None,
                 follow_store: Any = None,
                 fetcher: Callable[..., Awaitable[Reply]] = fetch,
                 notify: Callable[[str, str, str], Any] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
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
            return fs.get_usage("flight_aeroapi", self._month())
        return 0

    def allowance_exceeded(self) -> bool:
        limit = int(FREE_PER_MONTH * ALLOWANCE_STOP)
        return self._get_usage() >= limit

    def available(self) -> bool:
        if not self._key():
            return False
        if self.allowance_exceeded():
            return False
        return True

    def covers(self, refs: dict[str, str]) -> bool:
        return bool(refs.get("flight"))

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
                calls = self.follow_store.count_usage("flight_aeroapi", self._month())
                if calls >= int(FREE_PER_MONTH * ALLOWANCE_STOP):
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
            if fs.notified_allowance("flight_aeroapi", month):
                return
            fs.set_notified_allowance("flight_aeroapi", month, True)

        if self.notify is not None:
            pct = int(ALLOWANCE_STOP * 100)
            self.notify(
                "follow_problem",
                "FlightAware",
                f"FlightAware call allowance reached {pct}%: stopped checking to avoid charges.",
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
        }
