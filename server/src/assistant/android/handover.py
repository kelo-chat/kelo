"""The phone's screen handed to the owner (the `android_show` tool): the apps' PHONE live view (android/live.py, key `android`: portrait,
titled with the device's name, take control with Back / Home / Recents) instead of a browser tab. The card in the chat (a human_check item
of kind "device") opens that view and carries a "Done" button; the live view has one too (both answer the card: approve=true = done).

The hand-over also ends by itself when the owner is done, judged from the live source alone (never while they are still touching):
  - they held the phone and gave it back, or
  - they did something (an input, or the picture changed while they had the phone view open) and then for `IDLE_DONE` seconds there was no input and the picture stayed the same.
    Not while the owner's own button hold is on: that hold ends only when they hand the phone back (or answer the idle question).
While it waits it is a viewer of the phone's live view (so pictures flow), but its own viewer never counts as the owner: a screen that settles by itself
(a spinner, a slow app start) does not end the hand-over. On a physical phone with no app view open the owner presses Done."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..agent import handover
from ..live_source import DEVICE_KEY
from .live import DeviceLive

log = logging.getLogger(__name__)

IDLE_DONE = 20.0        # no input and an unchanged picture for this long after the owner did something: they are done
LIMIT_SECONDS = 600.0   # the hand-over ends after this long (the owner answers sooner)
POLL = 0.5
VIEWER = "handover"


class PhoneHandover:
    def __init__(self, live: DeviceLive, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, idle_done: float = IDLE_DONE, limit: float = LIMIT_SECONDS,
                 poll: float = POLL):
        self.live, self._clock, self._sleep = live, clock, sleep
        self.idle_done, self.limit, self.poll = idle_done, limit, poll
        self.end = ""      # how the last wait ended: done | timeout | error
        self.detail = ""

    async def _reachable(self) -> bool:
        try:
            await asyncio.wait_for(self.live.client.status(), 3.0)
            return True
        except Exception:  # noqa: BLE001 - the bridge is away
            return False

    async def watch(self) -> str:
        """Wait until the owner is done: "done", "timeout" or "error" (the device's pictures stopped)."""
        live = self.live
        started = self._clock()
        await live.join(DEVICE_KEY, VIEWER)
        had_control = live.holder(DEVICE_KEY) is not None
        seq: int | None = None         # the newest picture seen (None: none yet; the first one is not a change)
        active_at: float | None = None
        try:
            while True:
                now = self._clock()
                if live.cast is None or live.cast.ended:
                    if live.cast is None and now - started < self.limit:
                        await live.join(DEVICE_KEY, VIEWER)   # the view was ended by something else: a new one
                    if live.cast is None or live.cast.ended:
                        self.detail = "the phone's pictures stopped"
                        return self._finish("error")
                live.touch(DEVICE_KEY, VIEWER)
                cast = live.cast
                if cast.seq and seq is not None and cast.seq != seq and any(v != VIEWER for v in cast.viewers):
                    active_at = now      # the picture changed while the owner has the phone view open: they are using the phone
                if cast.seq:
                    seq = cast.seq
                if live.last_input is not None and live.last_input >= started:
                    active_at = max(active_at or 0.0, live.last_input)
                if live.holder(DEVICE_KEY) is not None:
                    had_control = True
                elif had_control:
                    return self._finish("done")   # they held the phone and gave it back
                if active_at is not None and now - active_at >= self.idle_done and not live.button_hold():
                    return self._finish("done")   # a button hold (the owner's own 직접 조작) ends only when it is handed back
                if now - started >= self.limit:
                    return self._finish("timeout")
                await self._sleep(self.poll)
        finally:
            await live.leave(DEVICE_KEY, VIEWER)

    def _finish(self, how: str) -> str:
        self.end = how
        return how

    async def show(self, reason: str, *, lang: str = "en") -> str:
        """Hand the phone's live view to the owner (a card and a notification, like a human check, when the app set `handover` for this
        turn) and wait until they press Done, are done by themselves, or it runs out. Returns done | timeout | error | skipped | expired |
        cancelled. `lang` is accepted for the tool's call (the card's words are the server's own, already in the owner's language)."""
        self.end, self.detail = "", ""
        name = await self.live.device_name()
        if not name and not await self._reachable():
            # never a browser tab instead (owner rule): no card, the run gets the error
            self.detail = "the phone screen could not be opened"
            return self._finish("error")
        hand = handover.get()
        if hand is None:   # tests, the CLI: nobody to ask
            return await self.watch()
        watching = asyncio.ensure_future(self.watch())

        async def gone() -> bool:
            return await watching == "done"
        try:
            outcome = str(await hand({"tab": DEVICE_KEY, "site": name, "vendor": "android", "name": reason[:200], "kind": "device"}, gone))
        finally:
            watching.cancel()
            await asyncio.gather(watching, return_exceptions=True)
        if self.end in ("done", "timeout", "error") and outcome in ("solved", "progressed", "closed", "cancelled"):
            return "done" if self.end == "done" else self.end
        return "done" if outcome in ("solved", "progressed") else outcome
