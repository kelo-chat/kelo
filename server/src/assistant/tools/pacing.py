"""How fast the agent may go back to one site. Some sites block a visitor that searches several times in a few seconds, however
human each single search looks; so each host (its registrable domain, without "www.") has two earliest-next times, kept in memory:

- a form submit or search (Enter after typing, a click on a form's submit button that follows typing or belongs to a search form):
  the next one on the same host waits until a random 12-25 s have passed since the last one;
- any other navigation (opening an address, following a link) waits for a random 2-5 s since the last request to that host.

The gaps are drawn when the last request is recorded (so the check before typing and the wait before pressing Enter agree) and are
multiplied by humanize's `speed` (0, or human input off: no waiting at all). The wait is part of the step, so it counts on the step
clock (BrowserSession.run_step); when it would not leave the step time to act, nothing is submitted and the result says how long to
wait (BrowserSession._pace_note). No site is named here: it holds for every host.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from .sites import registrable

SUBMIT_GAP_S = (12.0, 25.0)
NAV_GAP_S = (2.0, 5.0)


class SitePacer:
    def __init__(self, clock: Callable[[], float], rng: random.Random | None = None):
        self.clock = clock
        self.rng = rng or random.Random()
        self._submit_at: dict[str, float] = {}   # host -> earliest time of the next submit
        self._nav_at: dict[str, float] = {}      # host -> earliest time of the next navigation

    @staticmethod
    def host_of(host: str) -> str:
        return registrable(host or "")

    def need(self, host: str, kind: str) -> float:
        """Seconds still to wait before a `kind` ("submit" | "nav") request to `host`."""
        h = self.host_of(host)
        if not h:
            return 0.0
        until = (self._submit_at if kind == "submit" else self._nav_at).get(h, 0.0)
        return max(0.0, until - self.clock())

    def record(self, host: str, kind: str, factor: float) -> None:
        """A `kind` request to `host` is going out now; `factor` scales the gaps (humanize speed)."""
        h = self.host_of(host)
        if not h:
            return
        now = self.clock()
        self._nav_at[h] = now + self.rng.uniform(*NAV_GAP_S) * factor
        if kind == "submit":
            self._submit_at[h] = now + self.rng.uniform(*SUBMIT_GAP_S) * factor
        if len(self._nav_at) > 500:               # a long-running server: keep the newest
            for d in (self._nav_at, self._submit_at):
                for k in sorted(d, key=d.get)[:100]:
                    d.pop(k, None)
