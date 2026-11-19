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

from ..agent.fence import data as fenced
from ..i18n import t
from ..live_source import DEVICE_KEY
from ..hold_asks import PHONE_WHERE
from ..tools.holds import BUTTON, HANDOVER, Holds
from ..tools.holds import HANDS_WAIT_SECONDS
from ..tools.live import (
    CONTROL_GRACE, DEFAULT_FPS, DEFAULT_MAX_WIDTH, DEFAULT_QUALITY, MAX_FPS as BROWSER_MAX_FPS, MAX_TEXT, RECENT_FRAMES, VIEWER_TIMEOUT, Frame,
    LiveEnded,
    LiveError, Tickets, settings_from, viewer_css_size,
)
from .device import DeviceClient, DeviceError
from .screen import ScreenReader

log = logging.getLogger(__name__)

MAX_FPS = 3                    # frames a second, however many viewers
HELD_FPS = 6                   # ...while the owner holds the phone (their taps should show at once)
EDITING_EVERY = 1.5            # while held, the screen is read for a focused text field at most this often
MAX_LINES = 8                  # lines in the summary of what the owner did
TIMEOUT_NOTE = ("[The owner's control of the phone timed out (their screen went away), so it came back to you; they may not have finished "
                "what they were doing.]")
CONTROL_WAIT = HANDS_WAIT_SECONDS   # an android_* step waits this long for the owner to hand the phone back (tools/holds.py)
PHONE_HELD_NOTE = ("[Not done: the owner took over the phone and has not handed it back for {minutes} minutes. Do not use android tools "
                   "until they do: tell the user you will carry on when they hand the phone back, and stop here.]")
PHONE_MOVED_NOTE = ("[Not done: the owner used the phone by hand before handing it back. Look at the screen again with android_look and "
                    "decide the next step from what it shows now.]")
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
    asked_fps: int = BROWSER_MAX_FPS                          # the rate a viewer asked for (the rate in force is capped by MAX_FPS / HELD_FPS)
    recent: dict[int, tuple[int, int, tuple[int, int]]] = field(default_factory=dict)   # frame seq -> (w, h, device size), the newest RECENT_FRAMES
    editing: bool = False                                     # the screen's focused node is a text field (read only while the owner holds the phone)
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
                 frame_every: float = 1.0 / MAX_FPS, control_wait: float = CONTROL_WAIT, runner: bool = True,
                 reader: ScreenReader | None = None):
        self.client, self.clock, self._sleep = client, clock, sleep
        self.reader = reader or ScreenReader()
        self.tickets = tickets or Tickets()
        self.frame_every, self.control_wait = frame_every, control_wait
        self.cast: DeviceCast | None = None
        self.listeners: list[Callable[[str], Any]] = []
        self._holder: str | None = None
        self.holds = Holds(lambda: self.clock())   # the owner's hold on the phone: kind, touches (tools/holds.py); the clock is read per use

        self.book: Any = None                     # hold_asks.HoldBook (set by server.py)
        self.asker: Any = None                    # hold_asks.HoldAsker (set by server.py)
        self._owner_moved = False                 # the owner used the phone by hand before handing it back (PHONE_MOVED_NOTE, once)
        self._conns: dict[str, int] = {}          # device -> live connections open
        self._seen: dict[str, float] = {}         # device -> last request
        self._handed_back = asyncio.Event()
        self._handed_back.set()
        self._runner: asyncio.Task[None] | None = None
        self._use_runner = runner
        self.fetched = 0                          # pictures asked of the bridge (tests, diagnostics)
        self.last_input: float | None = None      # clock() of the owner's last input event (a handover reads it to tell they are done)
        self.name = ""                            # the device's own name from the bridge ("Pixel 8"), once known
        self.control_listeners: list[Callable[[str, str | None], Any]] = []   # (key, device or None) on every change of hands
        self.handback_listeners: list[Callable[[str, str, str, str], Any]] = []   # (key, what the owner did, reason, "phone") on every hand-back
        self._journal: list[tuple[str, tuple[Any, ...]]] = []   # what the owner did while holding the phone (never typed text)
        self._took_at = 0.0
        self._note = ""                           # what the next android_* screen reading tells the agent (take_note)
        self._editing_at: float | None = None
        self._editing_busy = False
        self._tasks: set[asyncio.Task[Any]] = set()

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
        name = await self.device_name()
        holder, busy = self._holder, self.agent_turn(DEVICE_KEY, turns) is not None
        return {"id": DEVICE_KEY, "key": DEVICE_KEY, "kind": "device", "source": "device", "label": name or TITLE, "title": name or TITLE, "width": w,
                "height": h, "url": "", "host": "", "status": "working" if busy else "idle", "status_text": "", "last_activity": None,
                "task_id": None, "controller": "user" if holder else "agent", "controlled_by": holder, "viewers": self.watching(DEVICE_KEY),
                "thumbnail": None, "thumbnail_width": 0, "thumbnail_height": 0, "ended_at": None}

    async def device_name(self) -> str:
        """The device's own name (the bridge's `device`), kept once read; "" when the bridge does not say or is away."""
        if not self.name:
            try:
                self.name = str((await asyncio.wait_for(self.client.status(), 3.0)).get("device") or "").strip()[:80]
            except Exception:  # noqa: BLE001 - the bridge is away: no name yet
                return ""
        return self.name

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
        self._apply_fps(cast)
        self._ensure_runner()
        return cast

    def fps_cap(self) -> int:
        return HELD_FPS if self._holder is not None else MAX_FPS

    def _apply_fps(self, cast: DeviceCast) -> None:
        cast.settings["fps"] = min(cast.asked_fps, self.fps_cap())

    def interval(self) -> float:
        """Seconds between two pictures: `frame_every` normally, twice as often while the owner holds the phone."""
        return self.frame_every * MAX_FPS / HELD_FPS if self._holder is not None else self.frame_every

    def touch(self, key: str, viewer: str) -> None:
        if self.cast is not None and viewer in self.cast.viewers:
            self.cast.viewers[viewer] = self.clock()

    def rate(self, key: str) -> int:
        """Frames a second the frame pump may send (the api's live view calls it on every tab, the browser's LiveHub.rate too): the fps in
        force for the phone's cast (capped to HELD_FPS while the owner holds the phone), DEFAULT_FPS when nobody watches it. Missing
        before 2026-11-17: the pump raised AttributeError on every phone WebSocket (commit 7a238aa0)."""
        return int(self.cast.settings["fps"]) if self.cast is not None else DEFAULT_FPS

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
        new = settings_from(max_width, quality, fps, every_nth_frame, base={**cast.settings, "fps": cast.asked_fps})
        cast.asked_fps = new["fps"]
        cast.settings = new
        self._apply_fps(cast)
        return dict(cast.settings)

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
            cast.frame = Frame(cast.seq, jpeg, w, h, time.time(), "", TITLE, {"deviceWidth": dw, "deviceHeight": dh}, live_v=2, secure=True,
                               editing=cast.editing)
            cast.recent[cast.seq] = (w, h, (dw, dh))
            for old in [q for q in cast.recent if q <= cast.seq - RECENT_FRAMES]:
                cast.recent.pop(old, None)
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
            await self._sleep(max(0.0, self.interval() - (self.clock() - started)))

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
        hold = self.holds.get(DEVICE_KEY) if self._holder is not None else None
        if (self._holder is not None and hold is not None and hold.kind == HANDOVER and not self._conns.get(self._holder)
                and now - self._seen.get(self._holder, now) > CONTROL_GRACE):   # only a hand-over hold has this grace (tools/holds.py)
            await self.give_back(DEVICE_KEY, self._holder, reason="timeout")
        if cast is None or cast.ended:
            return
        self._look_for_editing(cast)
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

    def take(self, key: str, device: str, by: str = "button") -> None:
        """The device takes the phone. `by` "handover": the app took it because Kelo asked (a hand-over hold); else the owner's own press.
        A phone held by a device with no live connection moves to this one (the kind and the inputs stay)."""
        if not self.available(key):
            raise LiveError(404, "no_tab", "no such device")
        kind = HANDOVER if by == "handover" else BUTTON
        if self._holder is not None and self._holder != device:
            if self._conns.get(self._holder):
                raise LiveError(409, "held", "another device is controlling the phone")
            self.seen(key, device)
            self._holder = device
            self.holds.take(key, device, kind)
            self._changed(DEVICE_KEY)
            self._tell(device)
            self._save()
            return
        self.seen(key, device)
        if self._holder == device:
            self.holds.take(key, device, kind)
            self._save()
            return
        self._holder = device
        self.holds.take(key, device, kind)
        self._handed_back.clear()
        self._journal.clear()
        self._took_at = self.clock()
        if self.cast is not None:
            self._apply_fps(self.cast)
        self._ensure_runner()
        self._changed(DEVICE_KEY)
        self._tell(device)
        self._save()

    def _save(self) -> None:
        hold = self.holds.get(DEVICE_KEY) if self._holder is not None else None
        if self.book is not None and hold is not None and hold.kind == BUTTON:
            self.book.save(DEVICE_KEY, hold)

    def restore(self, device: str) -> None:
        """After a restart: the owner's button hold on the phone is the owner's again (the phone itself is still there)."""
        self._holder = device
        self._handed_back.clear()
        self.holds.take(DEVICE_KEY, device, BUTTON)
        self._ensure_runner()
        self._tell(device)

    def button_hold(self) -> bool:
        """The owner pressed 직접 조작 on the phone and has not handed it back (a hand-over's idle rule must not end it)."""
        hold = self.holds.get(DEVICE_KEY) if self._holder is not None else None
        return hold is not None and hold.kind == BUTTON

    def hold_view(self, key: str, device: str) -> dict[str, Any]:
        if self._holder is None:
            return {"hold": "", "holder_away": False}
        hold = self.holds.get(DEVICE_KEY)
        return {"hold": hold.kind if hold is not None else BUTTON, "holder_away": not self._conns.get(self._holder)}

    def where(self, key: str) -> str:
        return t(PHONE_WHERE)

    async def release(self, key: str, reason: str = "owner") -> bool:
        """The owner's hand back whichever device holds the phone (the answer to the idle question)."""
        if self._holder is None:
            return False
        await self.give_back(key, self._holder, reason=reason)
        return True

    async def give_back(self, key: str, device: str, *, reason: str = "owner") -> None:
        """The owner hands the phone back (reason "owner") or its holder went away ("timeout"). What they did is written down for the
        agent (`take_note`: the next android_* reading carries it) and told to `handback_listeners` (paused jobs go on)."""
        if self._holder is None:
            return
        if self._holder != device:
            raise LiveError(409, "held", "another device is controlling the phone")
        entries, seconds = list(self._journal), self.clock() - self._took_at
        hold = self.holds.drop(DEVICE_KEY)
        if hold is not None and hold.inputs:
            self._owner_moved = True
        if self.book is not None:
            self.book.forget(DEVICE_KEY)
        if self.asker is not None:
            self.asker.close(DEVICE_KEY)
        self._journal.clear()
        if self.cast is not None:
            self.cast.editing = False
        self._holder = None
        if self.cast is not None:
            self._apply_fps(self.cast)
        summary = summarize(entries, seconds, await self._app_now() if entries or reason == "timeout" else "")
        if reason == "timeout":
            summary = (TIMEOUT_NOTE + "\n" + summary) if summary else TIMEOUT_NOTE
        if summary:
            self._note = summary
        self._handed_back.set()
        self._changed(DEVICE_KEY)
        self._tell(None)
        for fn in list(self.handback_listeners):
            try:
                fn(DEVICE_KEY, summary, reason, "phone")
            except Exception:  # noqa: BLE001 - a listener never breaks handing back
                pass

    def take_note(self) -> str:
        """What the owner did by hand since the agent last looked, once (the android tools put it in front of their next screen reading)."""
        note, self._note = self._note, ""
        self._owner_moved = False
        return note

    async def _app_now(self) -> str:
        """The app the phone shows now ("package (Activity)"), "" when it cannot be read in a moment."""
        try:
            got = await asyncio.wait_for(self.client.screen(False), 3.0)
            screen = self.reader.parse(str(got.get("xml") or ""), str(got.get("app") or ""))
        except Exception:  # noqa: BLE001 - the phone is busy or away: no app name
            return ""
        return screen.package + (f" ({screen.activity})" if screen.activity else "")

    def _tell(self, device: str | None) -> None:
        for fn in list(self.control_listeners):
            try:
                fn(DEVICE_KEY, device)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ a text field has focus (the app raises its keyboard)
    def _look_for_editing(self, cast: DeviceCast) -> None:
        """While the owner holds the phone: is the focused node of the screen a text field? One read (the screen's items) at most every
        EDITING_EVERY seconds, in the background."""
        if self._holder is None or self._editing_busy:
            return
        now = self.clock()
        if self._editing_at is not None and now - self._editing_at < EDITING_EVERY:
            return
        self._editing_at, self._editing_busy = now, True
        t = asyncio.ensure_future(self._refresh_editing(cast))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _refresh_editing(self, cast: DeviceCast) -> None:
        try:
            got = await asyncio.wait_for(self.client.screen(False), 3.0)
            screen = self.reader.parse(str(got.get("xml") or ""), str(got.get("app") or ""))
            editing = any(i.focused and i.editable for i in screen.items)
        except Exception:  # noqa: BLE001 - not readable now: the last answer stands
            return
        finally:
            self._editing_busy = False
        if editing != cast.editing and self._holder is not None:
            cast.editing = editing
            cast.last = None   # the next round sends a frame again, so the new answer reaches the app even when the picture did not change

    async def view(self, key: str, device: str, ev: dict[str, Any]) -> dict[str, Any]:
        """`{"type": "view", ...}`: the phone keeps its own screen, so the size of the owner's live area changes nothing here."""
        if not self.available(key):
            raise LiveError(404, "no_tab", "no such device")
        if viewer_css_size(ev.get("width"), ev.get("height"), ev.get("density")) is None:
            raise LiveError(422, "bad_event", "width, height and density must be positive numbers")
        self.seen(key, device)
        return {"ok": True, "applied": False}

    async def hold_for_user(self, _args: dict[str, Any] | None = None) -> str | None:
        """An android_* tool's `hold`: wait while the owner has the phone (at most `control_wait`). None = go on, else the words the
        agent gets instead of a result (the job pauses until the owner hands the phone back)."""
        if self._holder is None:
            return None
        try:
            await asyncio.wait_for(self._handed_back.wait(), self.control_wait)
        except asyncio.TimeoutError:
            pass
        if self._holder is None:
            return None
        return PHONE_HELD_NOTE.format(minutes=max(1, round(self.control_wait / 60)))

    async def hold_to_act(self, args: dict[str, Any] | None = None) -> str | None:
        """The `hold` of the acting android steps: hold_for_user, then PHONE_MOVED_NOTE once when the owner changed the phone by hand."""
        held = await self.hold_for_user(args)
        if held:
            return held
        return PHONE_MOVED_NOTE if self._owner_moved else None

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
        self.holds.touched(DEVICE_KEY, device)   # every accepted input is the owner's hands: the idle clock starts again
        if self.asker is not None:
            self.asker.close(DEVICE_KEY)
        self.last_input = self.clock()
        out: dict[str, Any] = {"ok": True, "type": kind}

        def num(name: str, default: float | None = None) -> float:
            v = ev.get(name, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise LiveError(422, "bad_event", f"{name} must be a number")
            return float(v)

        def mapping() -> tuple[tuple[int, int], tuple[int, int]]:
            """(the picture's size, the device's size) of the frame the owner was looking at (`frame_seq`), else of the newest one."""
            cast = self.cast
            frame = cast.frame if cast is not None else None
            if cast is None or frame is None or not all(cast.device):
                raise LiveError(409, "no_frame", "no frame yet: open the live view first")
            seq = ev.get("frame_seq")
            known = cast.recent.get(seq) if isinstance(seq, int) and not isinstance(seq, bool) else None
            out["mapped_with"] = "frame" if known is not None else "latest"
            w, h, dev = known if known is not None else (frame.w, frame.h, cast.device)
            return (int(num("frame_w", w)) or w, int(num("frame_h", h)) or h), dev

        try:
            if kind == "tap":
                size, dev = mapping()
                x, y = to_device(num("x"), num("y"), size, dev)
                await self.client.tap(x, y)
                out.update(x=x, y=y)
                self._journal.append(("tap", (x, y)))
            elif kind == "swipe":
                size, dev = mapping()
                x1, y1 = to_device(num("x1"), num("y1"), size, dev)
                x2, y2 = to_device(num("x2"), num("y2"), size, dev)
                ms = int(min(3000.0, max(50.0, num("ms", 300))))
                await self.client.swipe(x1, y1, x2, y2, ms)
                out.update(x1=x1, y1=y1, x2=x2, y2=y2)
                self._journal.append(("swipe", (x1, y1, x2, y2)))
            elif kind == "text":
                text = ev.get("text")
                if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
                    raise LiveError(422, "bad_event", f"text must be 1..{MAX_TEXT} characters")
                try:
                    await self.client.text(text)
                except DeviceError as e:
                    raise DeviceError(str(e).replace(text, "[hidden]")) from None
                out["chars"] = len(text)
                self._journal.append(("text", (len(text),)))   # the length only, never the text
            else:
                name = KEYS.get(str(ev.get("key") or ""))
                if name is None:
                    raise LiveError(422, "bad_event", "key must be one of: back, home, enter, recents")
                await self.client.key(name)
                out["key"] = "recents" if name == "recent" else name
                self._journal.append(("key", (out["key"],)))
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
        for t in list(self._tasks):
            t.cancel()
        if self._runner is not None:
            self._runner.cancel()
        if self.cast is not None:
            await self._end(self.cast, notify=False)


def summarize(entries: list[tuple[str, tuple[Any, ...]]], seconds: float, app: str = "") -> str:
    """A few plain lines for the agent: what the owner did on the phone and where it stands now. Typed text is never in `entries`, only its
    length; the app name comes from the device, so the lines are fenced as outside text. "" when the owner did nothing."""
    if not entries:
        return ""
    lines: list[str] = []
    for kind, a in entries:
        if kind == "tap":
            lines.append(f"- tapped the screen at ({a[0]}, {a[1]})")
        elif kind == "swipe":
            lines.append(f"- swiped from ({a[0]}, {a[1]}) to ({a[2]}, {a[3]})")
        elif kind == "text":
            lines.append(f"- typed {a[0]} characters (the text is not recorded)")
        elif kind == "key":
            lines.append(f"- pressed {a[0]}")
    merged: list[str] = []
    for line in lines:   # a run of the same line once, with its count
        if merged and merged[-1][0] == line:
            merged[-1][1] += 1
        else:
            merged.append([line, 1])
    out = [l if n == 1 else f"{l} (x{n})" for l, n in merged]
    if len(out) > MAX_LINES - 1:
        out = out[:2] + [f"- ... {len(out) - (MAX_LINES - 4)} more steps ..."] + out[-(MAX_LINES - 4):]
    if app:
        out.append(f"- the phone now shows {app}")
    mins = max(1, round(seconds / 60))
    return (f"[While the owner used the phone (about {mins} min) they did this themselves:\n" + fenced("\n".join(out))
            + "\nThe screen has changed and refs from before no longer apply: look at it first and carry on from there; do not redo these steps. "
              "Handing the phone back approved no purchase and no sign-in step.]")
