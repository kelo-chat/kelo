"""Incheon airport passenger flight status source using data.go.kr."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from ...keyring_client import PLACEHOLDER
from ...netguard import Reply, fetch
from ..checks import Reading, Source

ICN_HOST = "apis.data.go.kr"
DEPARTURES_URL = f"https://{ICN_HOST}/B551177/StatusOfPassengerFlightsDfl/getPassengerDeparturesDfl"
ARRIVALS_URL = f"https://{ICN_HOST}/B551177/StatusOfPassengerFlightsDfl/getPassengerArrivalsDfl"


class IncheonFlights(Source):
    kind: ClassVar[str] = "flight_status"

    def __init__(self, key: str | None = None,
                 key_fn: Callable[[], str | None] | None = None,
                 store: Any = None,
                 fetcher: Callable[..., Awaitable[Reply]] = fetch,
                 clock: Callable[[], float] = time.time) -> None:
        self._explicit_key = key
        self.key_fn = key_fn
        self.store = store
        self.fetcher = fetcher
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
                c = self.store.credential("data_go_kr")
                if c and c.get("secret") and c["secret"] != PLACEHOLDER:
                    return str(c["secret"])
            if hasattr(self.store, "key"):
                k = self.store.key("data_go_kr")
                if k:
                    return str(k)
        return None

    def available(self) -> bool:
        return bool(self._key())

    def covers(self, refs: dict[str, str]) -> bool:
        if not refs.get("flight"):
            return False
        airport = str(refs.get("airport") or refs.get("origin") or "").strip().upper()
        if airport and airport not in ("ICN", "INCHEON", "RKSI", "인천"):
            return False
        return True

    async def read(self, refs: dict[str, str]) -> Reading:
        key = self._key()
        if not key:
            return Reading(fields={}, at=self.clock(), ok=False, detail="data.go.kr key not available")

        flight_id = str(refs.get("flight") or "").strip().upper()
        if not flight_id:
            return Reading(fields={}, at=self.clock(), ok=False, detail="no flight identifier in refs")

        url = f"{DEPARTURES_URL}?serviceKey={key}&flight_id={flight_id}&type=json"
        try:
            r = await self.fetcher("GET", url, allowed_hosts=[ICN_HOST], headers={"Accept": "application/json"})
            if r.status != 200:
                return Reading(fields={}, at=self.clock(), ok=False, detail=f"HTTP {r.status} from data.go.kr")
            data = json.loads(r.body)
            fields = self._parse_flight_data(data, flight_id)
            if fields is None:
                return Reading(fields={}, at=self.clock(), ok=False, detail=f"Flight {flight_id} not found in response")
            return Reading(fields=fields, at=self.clock(), ok=True)
        except Exception as e:
            return Reading(fields={}, at=self.clock(), ok=False, detail=str(e))

    def _parse_flight_data(self, data: Any, flight_id: str) -> dict[str, Any] | None:
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Navigate standard data.go.kr JSON structure
            response = data.get("response") or data
            body = response.get("body") if isinstance(response, dict) else data
            items_obj = body.get("items") if isinstance(body, dict) else None
            if isinstance(items_obj, dict):
                items = items_obj.get("item") or []
                if isinstance(items, dict):
                    items = [items]
            elif isinstance(items_obj, list):
                items = items_obj
            elif "flights" in data:
                items = data["flights"]
            else:
                items = [data]
        else:
            return None

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("flightId") or item.get("flight_id") or item.get("flight") or item.get("ident") or "").upper()
            if not item_id or item_id == flight_id:
                gate = item.get("gatenumber") or item.get("gateNumber") or item.get("gate") or ""
                terminal = item.get("terminalid") or item.get("terminalId") or item.get("terminal") or ""
                dep_time = item.get("estimatedDateTime") or item.get("scheduleDateTime") or item.get("departure_time") or ""
                status = item.get("remark") or item.get("status") or ""
                return {
                    "gate": str(gate),
                    "terminal": str(terminal),
                    "departure_time": str(dep_time),
                    "status": str(status),
                }
        return None
