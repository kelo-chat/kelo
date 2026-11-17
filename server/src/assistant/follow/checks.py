"""Check definition and Source ABC for follow-through."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class Reading:
    fields: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)
    ok: bool = True
    detail: str = ""
    done: bool = False      # the source says the item is over (a parcel delivered): the item is done

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.fields[key]


class Source(ABC):
    kind: ClassVar[str]

    @abstractmethod
    def available(self) -> bool:
        """True if the source is free/reachable or the required key is configured."""
        ...

    @abstractmethod
    def covers(self, refs: dict[str, str]) -> bool:
        """True if this source can inspect the entity identified by refs."""
        ...

    @abstractmethod
    async def read(self, refs: dict[str, str]) -> Reading:
        """Read the watched subject; returns Reading(fields={...}, at=ts, ok=bool, detail=str)."""
        ...

    default_fields: ClassVar[tuple[str, ...]] = ("value",)   # the fields a check watches when the plan names none

    def paused(self, refs: dict[str, str]) -> str:
        """Why this source would cover `refs` but is stopped for now (a monthly allowance used up), else "". The runner then tries
        the next source of the kind, and tells the check is paused when none is left."""
        return ""

    def significant(self, field: str, old: Any, new: Any) -> bool:
        """True if a changed field is worth telling (a source with a continuous reading, like a forecast, ignores small moves).
        An ignored change is not stored either, so slow drift adds up against the last told value."""
        return True

    def usage(self) -> dict[str, Any] | None:
        """This month's calls for the apps and the operator page: {"source", "month", "used", "allowance" | None, "stop_at" | None,
        "paused"}; None for a source that is not counted."""
        return None


@dataclass
class Check:
    item_key: str
    kind: str
    refs: dict[str, str]
    fields: list[str]
    from_offset: str
    until_offset: str
    window_start: float
    window_end: float
    every_minutes: int
    next_at: float
    id: int = 0
    last_at: float | None = None
    state: str = "active"
    consecutive_errors: int = 0
    last_error: str = ""
