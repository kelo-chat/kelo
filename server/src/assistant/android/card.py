"""The phone card in the chat: a `device` turn event after the android_* steps (docs/android-device.md, phase 2).

`{"type": "device", "key": "android", "status": <the step in the owner's language>, "image": <data:image/jpeg;base64 or null>, "width",
"height", "bytes", "at"}`: built like the browser's card (`BrowserSession.card`): at most one per `CARD_MIN_INTERVAL` seconds per owner,
a JPEG never bigger than `CARD_MAX_BYTES` and at most `CARD_MAX_WIDTH` pixels wide (else the card goes without a picture)."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Callable
from typing import Any

from ..agent import emit_ui, ui_sink
from ..live_source import DEVICE_KEY
from ..tools.browser import CARD_MIN_INTERVAL, browser_owner, shrink_jpeg
from .device import DeviceClient


class DeviceCard:
    def __init__(self, client: DeviceClient, clock: Callable[[], float] = time.monotonic, interval: float = CARD_MIN_INTERVAL):
        self.client, self.clock, self.interval = client, clock, interval
        self._last: dict[str, float] = {}      # owner -> when its last phone card went out

    async def show(self, status: str, *, force: bool = False) -> dict[str, Any] | None:
        """Show the phone in the chat. Returns what was sent, or None (too soon, or nobody listening)."""
        if ui_sink.get() is None:
            return None
        owner, now = browser_owner.get(), self.clock()
        if not force and now - self._last.get(owner, -1e9) < self.interval:
            return None
        self._last[owner] = now   # claimed before the picture is taken: steps that follow at once do not each fetch one
        image, size, w, h = None, 0, 0, 0
        try:
            raw = base64.b64decode(str((await self.client.frame()).get("png") or ""))
            jpeg, w, h = await asyncio.to_thread(shrink_jpeg, raw)
            if jpeg is not None:
                image, size = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(), len(jpeg)
        except Exception:  # noqa: BLE001 - the bridge away or not a picture: a card without a picture is still a card
            image = None
        item = {"type": "device", "key": DEVICE_KEY, "status": status, "image": image, "width": w, "height": h, "bytes": size,
                "at": int(time.time())}
        if not await emit_ui(item):
            self._last.pop(owner, None)
            return None
        return item
