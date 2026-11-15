"""How far a long run got (backlog 66): a run that is still sending applications after PROGRESS_AFTER seconds tells the owner once,
never once per item. A chat or schedule reply is one run ("turn:<id>"); a background job is one run over all its segments
("job:<worker id>"). Memory only: a restart starts the clocks again, which at worst means the notice comes later."""

from __future__ import annotations

import time
from typing import Callable

PROGRESS_AFTER = 600.0   # seconds a run has been going before it says how far it got (read at each call: tests change it)


class RunProgress:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start: dict[str, float] = {}
        self._count: dict[str, int] = {}
        self._told: set[str] = set()

    @staticmethod
    def key(turn_id: str, worker_id: str | None) -> str:
        return f"job:{worker_id}" if worker_id else f"turn:{turn_id}"

    def begin(self, key: str) -> None:
        """A run (or the next segment of a job) started; a job keeps the clock of its first segment."""
        self._start.setdefault(key, self._clock())

    def add(self, key: str) -> int:
        """One more application in this run; returns the run's count so far."""
        self._count[key] = self._count.get(key, 0) + 1
        return self._count[key]

    def due(self, key: str) -> bool:
        """True once per run: it has applied at least once and has been going for PROGRESS_AFTER seconds."""
        if key in self._told or not self._count.get(key) or key not in self._start:
            return False
        if self._clock() - self._start[key] < PROGRESS_AFTER:
            return False
        self._told.add(key)
        return True

    def end(self, key: str) -> None:
        self._start.pop(key, None)
        self._count.pop(key, None)
        self._told.discard(key)
