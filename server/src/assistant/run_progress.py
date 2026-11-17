"""How far a long run got (backlog 66): any chat turn, background job or schedule run that is still working after
PROGRESS_AFTER seconds tells the owner once per 10 minutes, built from the live status and tool steps already tracked.
A chat or schedule reply is one run ("turn:<id>"); a background job is one run over all its segments ("job:<worker id>").
Memory only: a restart starts the clocks again, which at worst means the notice comes later."""

from __future__ import annotations

import time
from typing import Callable

PROGRESS_AFTER = 600.0   # seconds a run has been going before it says how far it got (read at each call: tests change it)


class RunProgress:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start: dict[str, float] = {}
        self._count: dict[str, int] = {}
        self._last_told: dict[str, float] = {}
        self._told: set[str] = set()

    @staticmethod
    def key(turn_id: str, worker_id: str | None) -> str:
        return f"job:{worker_id}" if worker_id else f"turn:{turn_id}"

    def begin(self, key: str) -> None:
        """A run (or the next segment of a job) started; a job keeps the clock of its first segment."""
        self._start.setdefault(key, self._clock())

    def add(self, key: str, n: int = 1) -> int:
        """One more step or application in this run; returns the run's count so far."""
        self._count[key] = self._count.get(key, 0) + n
        return self._count[key]

    def count(self, key: str) -> int:
        return self._count.get(key, 0)

    def set_count(self, key: str, n: int) -> None:
        self._count[key] = max(self._count.get(key, 0), n)

    def due(self, key: str) -> bool:
        """True at most once per PROGRESS_AFTER seconds (10 min) for any run still working."""
        if key not in self._start:
            return False
        if PROGRESS_AFTER <= 0.0:
            if key in self._told:
                return False
            self._last_told[key] = self._clock()
            self._told.add(key)
            return True
        now = self._clock()
        last = self._last_told.get(key, self._start[key])
        if now - last < PROGRESS_AFTER:
            return False
        self._last_told[key] = now
        self._told.add(key)
        return True

    def end(self, key: str) -> None:
        self._start.pop(key, None)
        self._count.pop(key, None)
        self._last_told.pop(key, None)
        self._told.discard(key)
