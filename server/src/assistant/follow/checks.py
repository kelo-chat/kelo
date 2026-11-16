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
