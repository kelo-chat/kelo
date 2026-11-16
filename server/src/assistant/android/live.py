"""The Android device as a live source for the apps' phone card (docs/android-device.md, phase 2). `DeviceLive` implements
`live_source.LiveSource` like the browser's `LiveHub`, so the same routes (api/browser.py, key `android`) serve both.

Frames: the bridge's `GET /screen?image=only` (a PNG) -> JPEG (Pillow, at most 720 px wide, quality 55), at most `MAX_FPS` (3) a second
and ONLY while at least one viewer is joined: with nobody watching the bridge is never asked for a picture. A picture identical to the
last one (same PNG hash, same settings) is not encoded again and no new frame goes out. A viewer silent for `VIEWER_TIMEOUT` seconds
counts as gone (same rule as the browser's live view); the loop ends with the last viewer.

Control: a device takes the phone (`take`); only it may send input; it keeps it while it has a live connection open or has sent
something in the last `CONTROL_GRACE` seconds, else the phone goes back to the agent. While it holds the phone the `android_*` tools
wait (`hold_for_user`, the tools' `hold`) up to `CONTROL_WAIT` seconds and then say the owner is using the phone, like the browser's
tools do for a taken tab.

Input events (coordinates in frame pixels, the JPEG the app shows, turned into device pixels by the frame/device ratio):
{"type":"tap","x","y"}, {"type":"swipe","x1","y1","x2","y2","ms"?}, {"type":"text","text"} (typed; NEVER logged or kept) and
{"type":"key","key":"back|home|enter|recents"}. What the owner does by hand is their own action: no approval card.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import math
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..live_source import DEVICE_KEY
from ..tools.live import (
    CONTROL_GRACE, DEFAULT_MAX_WIDTH, DEFAULT_QUALITY, MAX_TEXT, VIEWER_TIMEOUT, Frame, LiveEnded, LiveError, Tickets, settings_from,
)
from .device import DeviceClient, DeviceError

log = logging.getLogger(__name__)

MAX_FPS = 3                    # frames a second, however many viewers
CONTROL_WAIT = 30.0            # an android_* step waits this long for the owner to hand the phone back
OWNER_USING = "the owner is using the phone; try again in a moment"
MAX_ERRORS = 5                 # pictures that fail one after the other before the live view ends
INPUT_TYPES = ("tap", "swipe", "text", "key")
KEYS = {"back": "back", "home": "home", "enter": "enter", "recents": "recent", "recent": "recent"}
TITLE = "Android"


@dataclass(eq=False)
class DeviceCast:
    settings: dict[str, int]
    viewers: dict[str, float] = field(default_factory=dict)   # viewer id -> last sign of life (clock)
    frame: Frame | None = None
    device: tuple[int, int] = (0, 0)                          # the device's own pixels (the size of its screenshot)
    seq: int = 0
    ended: bool = False
    last: tuple[str, int, int] | None = None                 # (PNG hash, max_width, quality) of the picture shown
    errors: int = 0
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)


def encode(png_b64: str, max_width: int, quality: int) -> tuple[bytes, int, int, int, int]:
    """(JPEG, its width, its height, the device's width, the device's height) from the bridge's base64 PNG. ValueError when it is not a picture."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(base64.b64decode(png_b64))).convert("RGB")
    except Exception:  # noqa: BLE001 - not a picture
        raise ValueError("the device sent a picture that could not be read") from None
    dw, dh = img.size
    if img.width > max_width:
        img = img.resize((max_width, max(1, round(img.height * max_width / img.width))))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue(), img.width, img.height, dw, dh


def to_device(x: float, y: float, frame: tuple[int, int], device: tuple[int, int]) -> tuple[int, int]:
    """A point of the frame (JPEG pixels) -> the device's pixels by the frame/device ratio, kept on the screen."""
    (fw, fh), (dw, dh) = frame, device
    return (min(max(round(x * dw / max(1, fw)), 0), max(0, dw - 1)), min(max(round(y * dh / max(1, fh)), 0), max(0, dh - 1)))


class DeviceLive:
    kind = "device"

    def __init__(self, client: DeviceClient, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, tickets: Tickets | None = None,
                 frame_every: float = 1.0 / MAX_FPS, control_wait: float = CONTROL_WAIT, runner: bool = True):
        self.client, self.clock, self._sleep = client, clock, sleep
        self.tickets = tickets or Tickets()
        self.frame_every, self.control_wait = frame_every, control_wait
        self.cast: DeviceCast | None = None
        self.listeners: list[Callable[[str], Any]] = []
        self._holder: str | None = None
        self._conns: dict[str, int] = {}          # device -> live connections open
        self._seen: dict[str, float] = {}         # device -> last request
        self._handed_back = asyncio.Event()
        self._handed_back.set()
        self._runner: asyncio.Task[None] | None = None
        self._use_runner = runner
        self.fetched = 0                          # pictures asked of the bridge (tests, diagnostics)

    # ------------------------------------------------------------------ tickets, availability
    def ticket(self, key: str, device: str) -> str:
        return self.tickets.issue(key, device)

    def redeem(self, ticket: str, key: str) -> str | None:
        return self.tickets.redeem(ticket, key)

    def available(self, key: str) -> bool:
        return key == DEVICE_KEY

    @staticmethod
    def agent_turn(key: str, turns: Iterable[Any]) -> Any | None:
        """The running turn that is using the phone: the one that has put a phone card in its chat."""
        return next((t for t in turns if any(e.get("type") == "device" for e in list(getattr(t, "events", ())))), None)

    async def listing(self, turns: Iterable[Any]) -> dict[str, Any]:
        """The device as an item of the apps' list of live sources (GET /api/browser/sessions): size from the bridge (0 x 0 when it is away)."""
        w, h = self.cast.device if self.cast is not None else (0, 0)
        if not (w and h):
            try:
                size = (await asyncio.wait_for(self.client.status(), 3.0)).get("size") or [0, 0]
                w, h = int(size[0]), int(size[1])
            except Exception:  # noqa: BLE001 - the bridge is away: the size is unknown
                w, h = 0, 0
        holder, busy = self._holder, self.agent_turn(DEVICE_KEY, turns) is not None
        return {"id": DEVICE_KEY, "key": DEVICE_KEY, "kind": "device", "source": "device", "label": TITLE, "title": TITLE, "width": w,
                "height": h, "url": "", "host": "", "status": "working" if busy else "idle", "status_text": "", "last_activity": None,
                "task_id": None, "controller": "user" if holder else "agent", "controlled_by": holder, "viewers": self.watching(DEVICE_KEY),
                "thumbnail": None, "thumbnail_width": 0, "thumbnail_height": 0, "ended_at": None}

    # ------------------------------------------------------------------ viewers
    async def join(self, key: str, viewer: str, **quality: Any) -> DeviceCast:
        if not self.available(key):
            raise LiveError(404, "no_tab", "no such device")
        cast = self.cast
        if cast is None or cast.ended:
            cast = self.cast = DeviceCast(self._settings(**quality))
        elif quality:
            await self.configure(key, **quality)
        cast.viewers[viewer] = self.clock()
        self._ensure_runner()
        return cast

    def touch(self, key: str, viewer: str) -> None:
        if self.cast is not None and viewer in self.cast.viewers:
            self.cast.viewers[viewer] = self.clock()

    def watching(self, key: str) -> int:
        return len(self.cast.viewers) if self.cast is not None else 0

    async def leave(self, key: str, viewer: str) -> None:
        cast = self.cast
        if cast is None:
            return
        cast.viewers.pop(viewer, None)
        if not cast.viewers:
            await self._end(cast, notify=False)

    async def _end(self, cast: DeviceCast, *, notify: bool = True) -> None:
        if self.cast is cast:
            self.cast = None
        cast.ended = True
        async with cast.cond:
            cast.cond.notify_all()
        if notify:
            self._changed(DEVICE_KEY)

    def end_reason(self, key: str, viewer: str, cast: DeviceCast) -> str:
        if self.cast is not cast or cast.ended:
            return "tab_closed"
        return "" if viewer in cast.viewers else "no_ping"

    @staticmethod
    def _settings(**quality: Any) -> dict[str, int]:
        s = settings_from(quality.get("max_width"), quality.get("quality"), quality.get("fps"), quality.get("every_nth_frame"),
                          base={"max_width": DEFAULT_MAX_WIDTH, "quality": DEFAULT_QUALITY, "fps": MAX_FPS, "every_nth_frame": 1})
        s["fps"] = min(s["fps"], MAX_FPS)
        return s

    async def configure(self, key: str, max_width: Any = None, quality: Any = None, fps: Any = None, every_nth_frame: Any = None) -> dict[str, int]:
        cast = self.cast
        if cast is None:
            raise LiveError(404, "not_watching", "nobody is watching the device")
        new = settings_from(max_width, quality, fps, every_nth_frame, base=cast.settings)
        new["fps"] = min(new["fps"], MAX_FPS)
        cast.settings = new
        return dict(new)

    # ------------------------------------------------------------------ frames
    async def wait_frame(self, key: str, after: int, timeout: float) -> Frame | None:
        cast = self.cast
        if cast is None or cast.ended:
            raise LiveEnded(key)
        deadline = time.monotonic() + timeout
        async with cast.cond:
            while not cast.ended and (cast.frame is None or cast.frame.seq <= after):
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    await asyncio.wait_for(cast.cond.wait(), left)
                except asyncio.TimeoutError:
                    break
            if cast.ended:
                raise LiveEnded(key)
            return cast.frame if cast.frame is not None and cast.frame.seq > after else None

    def latest(self, key: str) -> Frame | None:
        return self.cast.frame if self.cast is not None else None

    async def _fetch(self, cast: DeviceCast) -> None:
        """One picture from the bridge, shown when it differs from the last."""
        self.fetched += 1
        png = str((await self.client.frame()).get("png") or "")
        if not png:
            raise DeviceError("the device sent no picture")
        s = cast.settings
        mark = (hashlib.sha1(png.encode()).hexdigest(), s["max_width"], s["quality"])
        if mark == cast.last:
            return                                   # the same picture: nothing to encode, nothing to send
        try:
            jpeg, w, h, dw, dh = await asyncio.to_thread(encode, png, s["max_width"], s["quality"])
        except ValueError as e:
            raise DeviceError(str(e)) from None
        if cast.ended:
            return
        cast.last, cast.device = mark, (dw, dh)
        async with cast.cond:
            cast.seq += 1
            cast.frame = Frame(cast.seq, jpeg, w, h, time.time(), "", TITLE, {"deviceWidth": dw, "deviceHeight": dh}, secure=True)
            cast.cond.notify_all()

    # ------------------------------------------------------------------ the loop (frames while watched; control grace)
    def _ensure_runner(self) -> None:
        if not self._use_runner or (self._runner is not None and not self._runner.done()):
            return
        self._runner = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        while (self.cast is not None and not self.cast.ended) or self._holder is not None:
            started = self.clock()
            try:
                await self.step()
            except Exception:  # noqa: BLE001 - housekeeping keeps going
                log.exception("phone live view housekeeping failed")
            await self._sleep(max(0.0, self.frame_every - (self.clock() - started)))

    async def step(self) -> None:
        """One round: drop silent viewers, hand the phone back when its controller is gone, and (only with a viewer) fetch a picture."""
        now = self.clock()
        cast = self.cast
        if cast is not None and not cast.ended:
            for viewer, at in list(cast.viewers.items()):
                if now - at > VIEWER_TIMEOUT:
                    cast.viewers.pop(viewer, None)
            if not cast.viewers:
                await self._end(cast, notify=False)
                cast = None
        if self._holder is not None and not self._conns.get(self._holder) and now - self._seen.get(self._holder, now) > CONTROL_GRACE:
            await self.give_back(DEVICE_KEY, self._holder)
        if cast is None or cast.ended:
            return
        try:
            await self._fetch(cast)
            cast.errors = 0
        except DeviceError as e:
            cast.errors += 1
            if cast.errors >= MAX_ERRORS:
                log.info("the phone live view ended: %s", str(e)[:100])
                await self._end(cast)

    # ------------------------------------------------------------------ control
    def connected(self, key: str, device: str, delta: int) -> None:
        self._conns[device] = max(0, self._conns.get(device, 0) + delta)
        if not self._conns[device]:
            self._conns.pop(device, None)
        self._seen[device] = self.clock()

    def seen(self, key: str, device: str) -> None:
        self._seen[device] = self.clock()

    def holder(self, key: str) -> str | None:
        return self._holder

    def take(self, key: str, device: str) -> None:
        if not self.available(key):
            raise LiveError(404, "no_tab", "no such device")
        if self._holder is not None and self._holder != device:
            raise LiveError(409, "held", "another device is controlling the phone")
        self.seen(key, device)
        if self._holder == device:
            return
        self._holder = device
        self._handed_back.clear()
        self._ensure_runner()
        self._changed(DEVICE_KEY)

    async def give_back(self, key: str, device: str) -> None:
        if self._holder is None:
            return
        if self._holder != device:
            raise LiveError(409, "held", "another device is controlling the phone")
        self._holder = None
        self._handed_back.set()
        self._changed(DEVICE_KEY)

    async def hold_for_user(self, _args: dict[str, Any] | None = None) -> str | None:
        """An android_* tool's `hold`: wait while the owner has the phone (at most `control_wait`). None = go on, else the words the
        agent gets instead of a result."""
        if self._holder is None:
            return None
        try:
            await asyncio.wait_for(self._handed_back.wait(), self.control_wait)
        except asyncio.TimeoutError:
            pass
        return None if self._holder is None else OWNER_USING

    # ------------------------------------------------------------------ input
    async def input(self, key: str, device: str, ev: dict[str, Any]) -> dict[str, Any]:
        """One input event from the controlling device (LiveError 404 / 409 not the controller / 409 no frame / 422 bad event / 502
        the device refused). The text of a `text` event is typed and never kept, logged or echoed."""
        kind = str(ev.get("type") or "")
        if kind not in INPUT_TYPES:
            raise LiveError(422, "bad_event", f"unknown input type (one of: {', '.join(INPUT_TYPES)})")
        if not self.available(key):
            raise LiveError(404, "no_tab", "no such device")
        if self._holder != device:
            raise LiveError(409, "not_controller", "take control of the phone first")
        self.seen(key, device)
        out: dict[str, Any] = {"ok": True, "type": kind}

        def num(name: str, default: float | None = None) -> float:
            v = ev.get(name, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise LiveError(422, "bad_event", f"{name} must be a number")
            return float(v)

        def mapping() -> tuple[tuple[int, int], tuple[int, int]]:
            cast = self.cast
            frame = cast.frame if cast is not None else None
            if cast is None or frame is None or not all(cast.device):
                raise LiveError(409, "no_frame", "no frame yet: open the live view first")
            return (int(num("frame_w", frame.w)) or frame.w, int(num("frame_h", frame.h)) or frame.h), cast.device

        try:
            if kind == "tap":
                size, dev = mapping()
                x, y = to_device(num("x"), num("y"), size, dev)
                await self.client.tap(x, y)
                out.update(x=x, y=y)
            elif kind == "swipe":
                size, dev = mapping()
                x1, y1 = to_device(num("x1"), num("y1"), size, dev)
                x2, y2 = to_device(num("x2"), num("y2"), size, dev)
                ms = int(min(3000.0, max(50.0, num("ms", 300))))
                await self.client.swipe(x1, y1, x2, y2, ms)
                out.update(x1=x1, y1=y1, x2=x2, y2=y2)
            elif kind == "text":
                text = ev.get("text")
                if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
                    raise LiveError(422, "bad_event", f"text must be 1..{MAX_TEXT} characters")
                try:
                    await self.client.text(text)
                except DeviceError as e:
                    raise DeviceError(str(e).replace(text, "[hidden]")) from None
                out["chars"] = len(text)
            else:
                name = KEYS.get(str(ev.get("key") or ""))
                if name is None:
                    raise LiveError(422, "bad_event", "key must be one of: back, home, enter, recents")
                await self.client.key(name)
                out["key"] = "recents" if name == "recent" else name
        except DeviceError as e:
            raise LiveError(502, "device", f"the phone could not do that ({str(e)[:160]})") from None
        return out

    # ------------------------------------------------------------------ housekeeping
    def _changed(self, key: str) -> None:
        for fn in list(self.listeners):
            try:
                fn(key)
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
        if self.cast is not None:
            await self._end(self.cast, notify=False)
