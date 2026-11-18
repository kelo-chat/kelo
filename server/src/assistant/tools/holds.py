"""The owner's hold on a live screen (a browser tab or the phone). A button hold ("직접 조작") ends only when the owner hands it back;
a hand-over hold (the app took the screen because Kelo asked: a human check, a payment step, android_show) keeps the old ends.
Here: who holds what, of which kind, when the holder last touched it, and whether the idle question went out for this idle period.
Pure: no I/O, the clock is injected."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

IDLE_ASK_SECONDS = 600.0      # owner decision 2026-11-18: a button hold untouched this long -> Kelo asks (once per idle period)
HANDS_WAIT_SECONDS = 300.0    # an agent step waits at most this long for a held screen, then is told the owner is using it

BUTTON, HANDOVER = "button", "handover"


@dataclass(slots=True)
class Hold:
    device: str
    kind: str                        # "button" | "handover"
    touched_at: float                # clock() of the holder's last touch, of the take, or of a "keep" answer
    asked_for: float | None = None   # touched_at of the idle period the question went out for (None = not asked)
    fence: int | None = None         # frame seq: inputs aimed at a picture this old or older are dropped (a stale touch)
    inputs: int = 0                  # holder inputs during this hold (>0 -> the agent re-reads before its next acting step)
    gone: bool = False               # the screen itself went away (tab closed by a crash or restart) while the hold stays


class Holds:
    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self._held: dict[str, Hold] = {}

    def take(self, key: str, device: str, kind: str) -> Hold:
        """A new hold, or a move to another device (the kind and the inputs stay). The same device again changes nothing but the kind, which
        becomes "button" when the owner presses 직접 조작 (never back to a hand-over)."""
        now = self.clock()
        cur = self._held.get(key)
        if cur is None:
            cur = self._held[key] = Hold(device, kind if kind in (BUTTON, HANDOVER) else BUTTON, now)
        elif cur.device == device:
            if kind == BUTTON:          # the owner's own press makes a hand-over hold a button hold; an app's take never downgrades one
                cur.kind = BUTTON
        else:
            cur.device = device
            cur.touched_at = now
        return cur

    def get(self, key: str) -> Hold | None:
        return self._held.get(key)

    def touched(self, key: str, device: str) -> None:
        """The holder's input: the idle clock starts again."""
        cur = self._held.get(key)
        if cur is not None and cur.device == device:
            cur.inputs += 1
            cur.touched_at = self.clock()

    def keep(self, key: str) -> None:
        """The owner answered "keep": the idle clock starts again and the next question waits for the next idle period."""
        cur = self._held.get(key)
        if cur is not None:
            cur.touched_at = self.clock()
            cur.asked_for = None

    def due_question(self, key: str) -> bool:
        """A button hold whose owner has not asked-for-this-period yet and has been idle for IDLE_ASK_SECONDS (or whose screen is gone)."""
        cur = self._held.get(key)
        if cur is None or cur.kind != BUTTON or cur.asked_for == cur.touched_at:
            return False
        return cur.gone or self.clock() - cur.touched_at >= IDLE_ASK_SECONDS

    def asked(self, key: str) -> None:
        cur = self._held.get(key)
        if cur is not None:
            cur.asked_for = cur.touched_at

    def drop(self, key: str) -> Hold | None:
        return self._held.pop(key, None)

    def items(self) -> dict[str, Hold]:
        return dict(self._held)
