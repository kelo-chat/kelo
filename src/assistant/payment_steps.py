"""The "approve in another app" step of a checkout, as ONE card in the chat while the agent waits.

Many payments end with a step the page cannot finish by itself: the card company's app shows a number to confirm (often with a QR code
and a few minutes to do it), the bank sends a one-time code, a 3-D Secure push or a passkey / fingerprint prompt appears, or the payment
app asks for a confirmation. The agent finds the step on the page (tools/payment_step.py) and calls payment_step_wait; that opens one
card here, shown in the chat (a `payment_step` turn event, the same event again with each change) with what is being paid, the code
(large, tap to copy), the QR code cut out of the page, the time left, the page's instruction in one plain line, and the user's choices:

  approved   "I approved it" -> the agent checks the page and makes the final click that finishes the payment (inside the purchase
             window of the approval the user already gave);
  cancel     the payment is abandoned (no further click; the purchase window closes);
  new_code   after the time ran out ("the code expired, get a new one?") -> the agent asks the page for a new code;
  code       a one-time code the user received, typed into the card -> the agent types it into the page's code box;
  done       the user finished the step themselves (the PIN keypad shown in the card, or the live view).

The card is kept here until it is answered or the step ends, so it shows up again after a reload and on the owner's other devices
(GET /api/payment-steps); the app answers with POST /api/payment-steps/{id}. It is time-critical: the server leaves an urgent
notification (it may sound in quiet hours) for when the app is not on screen. Memory only: a restart ends the reply that waits anyway.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

ANSWERS = ("approved", "cancel", "new_code", "code", "done")
STATES = ("waiting", "expired", "renewing", "checking", "finishing", "approved", "done", "cancelled", "timeout", "closed")
OPEN_STATES = ("waiting", "expired", "renewing", "checking", "finishing")
KEEP_CLOSED = 900.0     # how long a step that ended is still listed (the card folds to its outcome on other devices)


class StepError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class Step:
    id: str
    event: dict[str, Any]
    opened: float
    answers: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    closed: float | None = None


class PaymentSteps:
    """The payment-step cards open now. `on_open(event)`: called once when a card opens (the server leaves the urgent notification);
    `on_close(event)`: when it ends (the notification is taken back)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._steps: dict[str, Step] = {}
        self.on_open: Callable[[dict[str, Any]], Any] | None = None
        self.on_close: Callable[[dict[str, Any]], Any] | None = None

    def _sweep(self) -> None:
        now = self.clock()
        for k in [k for k, s in self._steps.items() if s.closed is not None and now - s.closed > KEEP_CLOSED]:
            self._steps.pop(k, None)

    def open(self, event: dict[str, Any]) -> dict[str, Any]:
        self._sweep()
        sid = "ps_" + secrets.token_hex(8)
        ev = {**event, "type": "payment_step", "step_id": sid, "state": "waiting", "opened_at": self.clock()}
        self._steps[sid] = Step(sid, ev, self.clock())
        if self.on_open is not None:
            try:
                self.on_open(ev)
            except Exception:  # noqa: BLE001 - a notification problem never stops the card
                pass
        return dict(ev)

    def update(self, step_id: str, **fields: Any) -> dict[str, Any]:
        s = self._steps.get(step_id)
        if s is None:
            raise KeyError(step_id)
        s.event.update(fields)
        return dict(s.event)

    def get(self, step_id: str) -> dict[str, Any] | None:
        s = self._steps.get(step_id)
        return dict(s.event) if s else None

    def is_open(self, step_id: str) -> bool:
        s = self._steps.get(step_id)
        return s is not None and s.closed is None

    def close(self, step_id: str, state: str) -> dict[str, Any] | None:
        s = self._steps.get(step_id)
        if s is None or s.closed is not None:
            return dict(s.event) if s else None
        s.closed = self.clock()
        s.event.update(state=state, closed_at=s.closed)
        for k in ("code", "qr"):        # an ended step does not keep the code or its picture around
            s.event.pop(k, None)
        if self.on_close is not None:
            try:
                self.on_close(dict(s.event))
            except Exception:  # noqa: BLE001
                pass
        return dict(s.event)

    def pending(self) -> list[dict[str, Any]]:
        """Open cards, oldest first, and the ones that ended in the last minutes (so other devices fold them)."""
        self._sweep()
        return [dict(s.event) for s in sorted(self._steps.values(), key=lambda s: s.opened)]

    def open_for(self, owner: str) -> dict[str, Any] | None:
        for s in self._steps.values():
            if s.closed is None and s.event.get("tab") == owner:
                return dict(s.event)
        return None

    def answer(self, step_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """The user's answer from the card: {"answer": approved|cancel|new_code|code|done, "code"?}."""
        s = self._steps.get(step_id)
        if s is None or s.closed is not None:
            raise StepError("this payment step is no longer waiting (it ended, or the reply stopped)", 404)
        what = str(body.get("answer") or "")
        if what not in ANSWERS:
            raise StepError(f"answer must be one of {', '.join(ANSWERS)}")
        out: dict[str, Any] = {"answer": what}
        if what == "code":
            code = "".join(str(body.get("code") or "").split())
            if not code or len(code) > 12 or not code.isalnum():
                raise StepError("code must be the one-time code you received: letters and digits, at most 12")
            out["code"] = code
        s.answers.put_nowait(out)
        return {"ok": True, "step_id": step_id, "answer": what}

    async def next_answer(self, step_id: str, timeout: float) -> dict[str, Any] | None:
        s = self._steps.get(step_id)
        if s is None:
            return None
        try:
            return await asyncio.wait_for(s.answers.get(), timeout)
        except asyncio.TimeoutError:
            return None
