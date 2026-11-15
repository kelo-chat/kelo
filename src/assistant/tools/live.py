"""Live view of one browser tab for the phone app, and the user's own hands on it ("take control").

Frames: Chrome DevTools `Page.startScreencast` (JPEG) on the tab, one screencast per tab however many phones watch it. Every frame is
acknowledged (`Page.screencastFrameAck`); acknowledgements are spaced 1/fps apart, which is what paces Chrome (it sends the next frame
only after an acknowledgement), so a busy page never costs more than the asked frame rate. `everyNthFrame` stays 1 by default:
Chrome counts *painted* frames, not screen refreshes, so a larger value makes a slowly changing page look frozen. A page that paints
nothing after the start (a still page) gets one picture taken with `Page.captureScreenshot` so a new viewer never stares at nothing.

Viewers are counted per tab: the screencast stops when the last one leaves, and a viewer that sent nothing for
`VIEWER_TIMEOUT` seconds (no ping, no poll) counts as gone. Each viewer only ever gets the newest frame: a slow phone skips frames,
nothing queues up behind it (at most one frame waits, and it is replaced by a newer one).

Control: a device takes a tab (`take`); only that device may send input; it keeps the tab while it has a live-view connection open
or has sent something in the last `CONTROL_GRACE` seconds, else the tab goes back to the agent. Coordinates come in frame pixels
(the JPEG the phone shows) and are turned into CSS pixels of the viewport with the frame's own metadata (see `map_point`).
Typed text is never logged or kept.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .browser import BrowserSession

log = logging.getLogger("uvicorn.error")

DEFAULT_MAX_WIDTH, DEFAULT_QUALITY, DEFAULT_FPS = 720, 55, 10
MIN_WIDTH, MAX_WIDTH = 360, 1080
MIN_QUALITY, MAX_QUALITY = 40, 80
MAX_FPS = 15
VIEWER_TIMEOUT = 60.0      # a viewer silent this long is dropped (and the screencast stops when it was the last one)
CONTROL_GRACE = 30.0       # a controlling device with no live connection and no request for this long hands the tab back
TICKET_SECONDS = 30
SEED_AFTER = 0.5           # no frame this long after a start: take one picture instead
LONG_PRESS_MS = 700
MAX_TEXT = 2000            # characters per text event
REAP_EVERY = 1.0

# key -> (windowsVirtualKeyCode, text it types)
KEYS: dict[str, tuple[int, str]] = {"Enter": (13, "\r"), "Backspace": (8, ""), "Tab": (9, ""), "Escape": (27, ""),
                                    "ArrowLeft": (37, ""), "ArrowUp": (38, ""), "ArrowRight": (39, ""), "ArrowDown": (40, ""),
                                    "Delete": (46, ""), "Home": (36, ""), "End": (35, ""), "PageUp": (33, ""), "PageDown": (34, "")}
INPUT_TYPES = ("tap", "long_press", "scroll", "text", "key", "back", "forward", "reload")


class LiveError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class LiveEnded(Exception):
    """The tab is gone (closed, or the browser restarted): the live view is over."""


@dataclass(slots=True)
class Frame:
    seq: int
    jpeg: bytes
    w: int            # pixels of the JPEG
    h: int
    ts: float         # unix seconds
    url: str
    title: str
    meta: dict[str, Any]

    def header(self) -> dict[str, Any]:
        m = self.meta
        return {"type": "frame", "seq": self.seq, "w": self.w, "h": self.h, "ts": round(self.ts, 3), "url": self.url, "title": self.title,
                "bytes": len(self.jpeg), "css_w": m.get("deviceWidth"), "css_h": m.get("deviceHeight")}


def settings_from(max_width: Any = None, quality: Any = None, fps: Any = None, every_nth_frame: Any = None,
                  base: dict[str, int] | None = None) -> dict[str, int]:
    """Clamp a quality request: max_width 360..1080 (720 and 1080 are the two the app offers), quality 40..80, fps 1..15."""
    out = dict(base or {"max_width": DEFAULT_MAX_WIDTH, "quality": DEFAULT_QUALITY, "fps": DEFAULT_FPS, "every_nth_frame": 1})

    def num(v: Any) -> float | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        return float(v)

    if (v := num(max_width)) is not None:
        out["max_width"] = int(min(MAX_WIDTH, max(MIN_WIDTH, v)))
    if (v := num(quality)) is not None:
        out["quality"] = int(min(MAX_QUALITY, max(MIN_QUALITY, v)))
    if (v := num(fps)) is not None:
        out["fps"] = int(min(MAX_FPS, max(1, v)))
    if (v := num(every_nth_frame)) is not None:
        out["every_nth_frame"] = int(min(10, max(1, v)))
    return out


def map_point(x: float, y: float, frame_w: int, frame_h: int, meta: dict[str, Any]) -> tuple[float, float]:
    """A point of the frame picture (pixels of the JPEG, top-left 0,0) -> CSS pixels of the viewport, which is what
    Input.dispatchMouseEvent takes. The picture shows the whole device screen (deviceWidth x deviceHeight CSS px, scaled down to the
    JPEG's size); the page area starts `offsetTop` CSS px below its top. pageScaleFactor and scrollOffset do not enter: input
    coordinates are relative to the visible viewport, whatever its zoom and scroll (see `page_point` for document coordinates).
    The result is kept inside the viewport."""
    dw, dh = float(meta.get("deviceWidth") or frame_w), float(meta.get("deviceHeight") or frame_h)
    top = float(meta.get("offsetTop") or 0.0)
    sx, sy = dw / max(1, frame_w), dh / max(1, frame_h)
    cx = min(max(x * sx, 0.0), dw - 1)
    cy = min(max(y * sy - top, 0.0), max(0.0, dh - top - 1))
    return round(cx, 1), round(cy, 1)


def page_point(cx: float, cy: float, meta: dict[str, Any]) -> tuple[float, float]:
    """CSS viewport point -> document coordinates: scrollOffset + point / pageScaleFactor (for reports; input does not need it)."""
    scale = float(meta.get("pageScaleFactor") or 1.0) or 1.0
    return (round(float(meta.get("scrollOffsetX") or 0) + cx / scale, 1), round(float(meta.get("scrollOffsetY") or 0) + cy / scale, 1))


def map_delta(dx: float, dy: float, frame_w: int, frame_h: int, meta: dict[str, Any]) -> tuple[float, float]:
    """A scroll distance in frame pixels -> CSS pixels (wheel deltas)."""
    dw, dh = float(meta.get("deviceWidth") or frame_w), float(meta.get("deviceHeight") or frame_h)
    lim = 20_000.0
    return (round(max(-lim, min(lim, dx * dw / max(1, frame_w))), 1), round(max(-lim, min(lim, dy * dh / max(1, frame_h))), 1))


def jpeg_size(data: bytes) -> tuple[int, int]:
    from PIL import Image

    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:  # noqa: BLE001 - a frame we cannot read the size of: unknown
        return 0, 0


class Tickets:
    """One-time tickets for opening the live view (a WebSocket cannot carry the bearer header from every client): 30 s, one use,
    bound to the tab and the device that asked."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._t: dict[str, tuple[str, str, float]] = {}

    def issue(self, key: str, device: str) -> str:
        now = self.clock()
        for t in [t for t, (_, _, exp) in self._t.items() if exp <= now]:
            self._t.pop(t, None)
        ticket = secrets.token_urlsafe(24)
        self._t[ticket] = (key, device, now + TICKET_SECONDS)
        return ticket

    def redeem(self, ticket: str, key: str) -> str | None:
        """The device the ticket was issued to, or None (unknown, used, expired, or for another tab). Used up either way."""
        got = self._t.pop(ticket or "", None)
        if got is None:
            return None
        k, device, exp = got
        return device if k == key and self.clock() < exp else None


@dataclass(eq=False)
class Cast:
    key: str
    page: Any
    cdp: Any
    settings: dict[str, int]
    viewers: dict[str, float] = field(default_factory=dict)   # viewer id -> last sign of life (clock)
    frame: Frame | None = None
    seq: int = 0
    running: bool = False
    ended: bool = False
    started_at: float = 0.0
    frames_since_start: int = 0
    next_ack: float = 0.0
    title: str = ""
    title_url: str = ""
    title_at: float = 0.0
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)


class LiveHub:
    """Screencasts and control of the browser's tabs, for the phone's live view."""

    def __init__(self, session: BrowserSession, clock: Callable[[], float] = time.monotonic, *, reaper: bool = True):
        self.session = session
        self.clock = clock
        self.casts: dict[str, Cast] = {}
        self._cdps: dict[str, tuple[Any, Any]] = {}          # tab -> (page, CDP session)
        self._conns: dict[tuple[str, str], int] = {}          # (tab, device) -> live connections open
        self._seen: dict[tuple[str, str], float] = {}         # (tab, device) -> last request
        self._reaper: asyncio.Task[None] | None = None
        self._use_reaper = reaper
        self.listeners: list[Callable[[str], Any]] = []       # tab -> something about it changed (control, end)
        session.control_listeners.append(lambda key, _dev: self._changed(key))

    # ------------------------------------------------------------------ CDP
    async def _cdp_for(self, key: str, page: Any) -> Any:
        got = self._cdps.get(key)
        if got and got[0] is page:
            return got[1]
        if got:
            await self._detach(got[1])
        cdp = await self.session.cdp_session(page)

        def on_frame(params: dict[str, Any]) -> None:
            cast = self.casts.get(key)
            if cast is None or cast.cdp is not cdp:
                return
            t = asyncio.ensure_future(self._on_frame(cast, params))
            cast.tasks.add(t)
            t.add_done_callback(cast.tasks.discard)

        cdp.on("Page.screencastFrame", on_frame)
        self._cdps[key] = (page, cdp)
        return cdp

    @staticmethod
    async def _detach(cdp: Any) -> None:
        try:
            await cdp.detach()
        except Exception:  # noqa: BLE001 - the tab may be gone already
            pass

    async def _drop_cdp(self, key: str) -> None:
        if key in self.casts or self.session.controlled_by(key) is not None:
            return
        got = self._cdps.pop(key, None)
        if got:
            await self._detach(got[1])

    # ------------------------------------------------------------------ screencast
    async def _start(self, cast: Cast) -> None:
        s = cast.settings
        cast.started_at, cast.frames_since_start, cast.next_ack = time.monotonic(), 0, 0.0
        cast.running = True   # before the request: the first frame may arrive before its answer, and must be acknowledged
        try:
            await cast.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": s["quality"], "maxWidth": s["max_width"],
                                                         "maxHeight": s["max_width"] * 2, "everyNthFrame": s["every_nth_frame"]})
        except Exception:
            cast.running = False
            raise
        t = asyncio.ensure_future(self._seed(cast, cast.started_at))
        cast.tasks.add(t)
        t.add_done_callback(cast.tasks.discard)

    async def _stop(self, cast: Cast) -> None:
        if cast.running:
            cast.running = False
            try:
                await cast.cdp.send("Page.stopScreencast", {})
            except Exception:  # noqa: BLE001 - a closed tab has nothing to stop
                pass

    async def _follow(self, cast: Cast, page: Any) -> None:
        """The tab's page was replaced (closed and opened again): carry the screencast over to the new one."""
        await self._stop(cast)
        cast.page, cast.cdp = page, await self._cdp_for(cast.key, page)
        try:
            await self._start(cast)
        except Exception:  # noqa: BLE001
            await self._end(cast)

    async def _seed(self, cast: Cast, started: float) -> None:
        """A still page paints nothing after the start: take one picture so the viewer sees it."""
        await asyncio.sleep(SEED_AFTER)
        if not cast.running or cast.started_at != started or cast.frames_since_start:
            return
        try:
            m = await cast.cdp.send("Page.getLayoutMetrics", {})
            vv = m.get("cssVisualViewport") or m.get("visualViewport") or {}
            cw, ch = float(vv.get("clientWidth") or 0), float(vv.get("clientHeight") or 0)
            if cw <= 0 or ch <= 0:
                return
            scale = min(1.0, cast.settings["max_width"] / cw)
            shot = await cast.cdp.send("Page.captureScreenshot", {
                "format": "jpeg", "quality": cast.settings["quality"],
                "clip": {"x": float(vv.get("pageX") or 0), "y": float(vv.get("pageY") or 0), "width": cw, "height": ch, "scale": scale}})
        except Exception:  # noqa: BLE001 - no picture this time; the next painted frame will do
            return
        if cast.frames_since_start or not cast.running:
            return
        meta = {"offsetTop": 0, "pageScaleFactor": float(vv.get("scale") or 1), "deviceWidth": cw, "deviceHeight": ch,
                "scrollOffsetX": float(vv.get("pageX") or 0), "scrollOffsetY": float(vv.get("pageY") or 0), "seed": True}
        await self._publish(cast, base64.b64decode(shot.get("data") or b""), meta)

    async def _on_frame(self, cast: Cast, params: dict[str, Any]) -> None:
        cast.frames_since_start += 1
        try:
            data = base64.b64decode(params.get("data") or "")
        except Exception:  # noqa: BLE001
            data = b""
        await self._publish(cast, data, dict(params.get("metadata") or {}))
        # acknowledgements spaced 1/fps apart: Chrome waits for them before the next frame, so this keeps it at the asked rate
        now = time.monotonic()
        due = max(now, cast.next_ack)
        cast.next_ack = due + 1.0 / max(1, cast.settings["fps"])
        if due > now:
            await asyncio.sleep(due - now)
        if cast.running and not cast.ended:
            try:
                await cast.cdp.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})
            except Exception:  # noqa: BLE001 - the tab went away
                pass

    async def _publish(self, cast: Cast, data: bytes, meta: dict[str, Any]) -> None:
        if not data or cast.ended:
            return
        w, h = jpeg_size(data)
        if not w:
            return
        url = ""
        try:
            url = str(cast.page.url or "")
        except Exception:  # noqa: BLE001
            pass
        now = time.monotonic()
        if (url != cast.title_url or now - cast.title_at > 2.0) and not any(t.get_name() == "title" for t in cast.tasks):
            cast.title_url, cast.title_at = url, now
            t = asyncio.ensure_future(self._refresh_title(cast))
            t.set_name("title")
            cast.tasks.add(t)
            t.add_done_callback(cast.tasks.discard)
        async with cast.cond:
            cast.seq += 1
            cast.frame = Frame(cast.seq, data, w, h, time.time(), url, cast.title, meta)
            cast.cond.notify_all()

    @staticmethod
    async def _refresh_title(cast: Cast) -> None:
        try:
            cast.title = str(await asyncio.wait_for(cast.page.title(), 2.0))[:200]
        except Exception:  # noqa: BLE001 - the old title will do
            pass

    # ------------------------------------------------------------------ viewers
    async def join(self, key: str, viewer: str, **quality: Any) -> Cast:
        """Start watching a tab (the screencast starts with the first viewer). LiveError 404 when there is no such tab."""
        page = self.session.raw_page(key)
        if page is None:
            raise LiveError(404, "no_tab", "no such tab")
        cast = self.casts.get(key)
        if cast is not None and cast.page is not page:
            await self._follow(cast, page)
        if cast is None:
            cdp = await self._cdp_for(key, page)
            cast = self.casts[key] = Cast(key, page, cdp, settings_from(**quality))
            cast.viewers[viewer] = self.clock()
            try:
                await self._start(cast)
            except Exception as e:  # noqa: BLE001
                self.casts.pop(key, None)
                await self._drop_cdp(key)
                raise LiveError(502, "browser", f"the browser did not start the live view ({type(e).__name__})") from None
        else:
            cast.viewers[viewer] = self.clock()
            if quality:
                await self.configure(key, **quality)
        self._ensure_reaper()
        return cast

    def touch(self, key: str, viewer: str) -> None:
        cast = self.casts.get(key)
        if cast is not None and viewer in cast.viewers:
            cast.viewers[viewer] = self.clock()

    def watching(self, key: str) -> int:
        cast = self.casts.get(key)
        return len(cast.viewers) if cast else 0

    async def leave(self, key: str, viewer: str) -> None:
        cast = self.casts.get(key)
        if cast is None:
            return
        cast.viewers.pop(viewer, None)
        if not cast.viewers:
            await self._end(cast, notify=False)

    async def _end(self, cast: Cast, *, notify: bool = True) -> None:
        if self.casts.get(cast.key) is cast:
            self.casts.pop(cast.key, None)
        await self._stop(cast)
        cast.ended = True
        for t in list(cast.tasks):
            t.cancel()
        async with cast.cond:
            cast.cond.notify_all()
        await self._drop_cdp(cast.key)
        if notify:
            self._changed(cast.key)

    async def configure(self, key: str, max_width: Any = None, quality: Any = None, fps: Any = None, every_nth_frame: Any = None) -> dict[str, int]:
        """Change the picture of a tab's live view (shared by everyone watching it: the last request wins)."""
        cast = self.casts.get(key)
        if cast is None:
            raise LiveError(404, "not_watching", "nobody is watching this tab")
        new = settings_from(max_width, quality, fps, every_nth_frame, base=cast.settings)
        restart = (new["max_width"], new["quality"], new["every_nth_frame"]) != (cast.settings["max_width"], cast.settings["quality"],
                                                                                cast.settings["every_nth_frame"])
        cast.settings = new
        if restart and cast.running:
            await self._stop(cast)
            await self._start(cast)
        return dict(new)

    async def wait_frame(self, key: str, after: int, timeout: float) -> Frame | None:
        """The newest frame when its number is above `after` (at once if there is one), else wait up to `timeout`; None when none came.
        LiveEnded when the tab's live view is over."""
        cast = self.casts.get(key)
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
        cast = self.casts.get(key)
        return cast.frame if cast else None

    # ------------------------------------------------------------------ control
    def connected(self, key: str, device: str, delta: int) -> None:
        """A live connection of `device` on `key` opened (+1) or closed (-1). The grace time starts at the close."""
        k = (key, device)
        self._conns[k] = max(0, self._conns.get(k, 0) + delta)
        if not self._conns[k]:
            self._conns.pop(k, None)
        self._seen[k] = self.clock()

    def seen(self, key: str, device: str) -> None:
        self._seen[(key, device)] = self.clock()

    def controller(self, key: str) -> str:
        return "user" if self.session.controlled_by(key) is not None else "agent"

    def take(self, key: str, device: str) -> None:
        if self.session.raw_page(key) is None:
            raise LiveError(404, "no_tab", "no such tab")
        holder = self.session.controlled_by(key)
        if holder is not None and holder != device:
            raise LiveError(409, "held", "another device is controlling this tab")
        self.seen(key, device)
        self.session.take_control(key, device)
        self._ensure_reaper()

    async def give_back(self, key: str, device: str) -> None:
        holder = self.session.controlled_by(key)
        if holder is None:
            return
        if holder != device:
            raise LiveError(409, "held", "another device is controlling this tab")
        await self.session.release_control(key)
        await self._drop_cdp(key)

    async def input(self, key: str, device: str, ev: dict[str, Any]) -> dict[str, Any]:
        """One input event from the controlling device. Raises LiveError (404 no tab, 409 not the controller / no frame yet,
        422 a bad event). The text of a `text` event is typed and never kept or logged."""
        kind = str(ev.get("type") or "")
        if kind not in INPUT_TYPES:
            raise LiveError(422, "bad_event", f"unknown input type (one of: {', '.join(INPUT_TYPES)})")
        page = self.session.raw_page(key)
        if page is None:
            raise LiveError(404, "no_tab", "no such tab")
        if self.session.controlled_by(key) != device:
            raise LiveError(409, "not_controller", "take control of this tab first")
        self.seen(key, device)
        cast = self.casts.get(key)
        cdp = cast.cdp if cast is not None and cast.page is page else await self._cdp_for(key, page)
        out: dict[str, Any] = {"ok": True, "type": kind}

        def num(name: str, default: float | None = None) -> float:
            v = ev.get(name, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise LiveError(422, "bad_event", f"{name} must be a number")
            return float(v)

        def where() -> tuple[float, float, dict[str, Any], int, int]:
            frame = cast.frame if cast is not None else None
            if frame is None:
                raise LiveError(409, "no_frame", "no frame yet: open the live view first")
            fw = int(num("frame_w", frame.w)) or frame.w
            fh = int(num("frame_h", frame.h)) or frame.h
            return num("x"), num("y"), frame.meta, fw, fh

        mouse = lambda typ, x, y, **kw: cdp.send("Input.dispatchMouseEvent", {"type": typ, "x": x, "y": y, **kw})  # noqa: E731
        if kind in ("tap", "long_press") and self.session.view(key) == "mobile":
            # the phone view emulates a touch screen: a phone site may listen to touch only, so the user's tap arrives as a touch
            # (2026-11-10: taps in "take control" of a phone-view tab clicked nothing). Chrome makes the click out of it, as on a phone.
            x, y, meta, fw, fh = where()
            cx, cy = map_point(x, y, fw, fh, meta)
            point = {"x": cx, "y": cy, "radiusX": 4, "radiusY": 4, "force": 1, "id": 1}
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
            if kind == "long_press":
                hold = num("ms", LONG_PRESS_MS)
                await asyncio.sleep(min(3000.0, max(300.0, hold)) / 1000)
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            out.update(x=cx, y=cy, page=list(page_point(cx, cy, meta)), touch=True)
        elif kind in ("tap", "long_press"):
            x, y, meta, fw, fh = where()
            cx, cy = map_point(x, y, fw, fh, meta)
            await mouse("mouseMoved", cx, cy)
            await mouse("mousePressed", cx, cy, button="left", buttons=1, clickCount=1)
            if kind == "long_press":
                hold = num("ms", LONG_PRESS_MS)
                await asyncio.sleep(min(3000.0, max(300.0, hold)) / 1000)
            await mouse("mouseReleased", cx, cy, button="left", buttons=0, clickCount=1)
            out.update(x=cx, y=cy, page=list(page_point(cx, cy, meta)))
        elif kind == "scroll":
            frame = cast.frame if cast is not None else None
            if frame is None:
                raise LiveError(409, "no_frame", "no frame yet: open the live view first")
            fw = int(num("frame_w", frame.w)) or frame.w
            fh = int(num("frame_h", frame.h)) or frame.h
            x, y = num("x", fw / 2), num("y", fh / 2)
            cx, cy = map_point(x, y, fw, fh, frame.meta)
            dx, dy = map_delta(num("dx", 0), num("dy", 0), fw, fh, frame.meta)
            await mouse("mouseWheel", cx, cy, deltaX=dx, deltaY=dy)
            out.update(x=cx, y=cy, dx=dx, dy=dy)
        elif kind == "text":
            text = ev.get("text")
            if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
                raise LiveError(422, "bad_event", f"text must be 1..{MAX_TEXT} characters")
            await cdp.send("Input.insertText", {"text": text})
            out["chars"] = len(text)
        elif kind == "key":
            name = str(ev.get("key") or "")
            if name not in KEYS:
                raise LiveError(422, "bad_event", f"key must be one of: {', '.join(KEYS)}")
            code, text = KEYS[name]
            base = {"key": name, "code": name, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown" if text else "rawKeyDown", **base, **({"text": text} if text else {})})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", **base})
            out["key"] = name
        elif kind == "reload":
            await cdp.send("Page.reload", {})
        else:   # back / forward
            hist = await cdp.send("Page.getNavigationHistory", {})
            i = int(hist.get("currentIndex", 0)) + (-1 if kind == "back" else 1)
            entries = hist.get("entries") or []
            if 0 <= i < len(entries):
                await cdp.send("Page.navigateToHistoryEntry", {"entryId": entries[i]["id"]})
            else:
                out["ok"], out["note"] = False, "nothing to go " + ("back to" if kind == "back" else "forward to")
        return out

    # ------------------------------------------------------------------ housekeeping
    def _changed(self, key: str) -> None:
        for fn in list(self.listeners):
            try:
                fn(key)
            except Exception:  # noqa: BLE001
                pass

    def _ensure_reaper(self) -> None:
        if not self._use_reaper or (self._reaper is not None and not self._reaper.done()):
            return

        async def loop() -> None:
            while self.casts or self.session.controls():
                await asyncio.sleep(REAP_EVERY)
                try:
                    await self.reap()
                except Exception:  # noqa: BLE001 - housekeeping must keep going
                    log.exception("live view housekeeping failed")

        self._reaper = asyncio.ensure_future(loop())

    async def reap(self) -> list[tuple[str, str]]:
        """Drop silent viewers (and stop screencasts nobody watches), end the live view of tabs that went away, follow a tab whose
        page was replaced, and hand back tabs whose controller is gone. Returns the viewers dropped as (tab, viewer)."""
        now = self.clock()
        dropped: list[tuple[str, str]] = []
        for key, cast in list(self.casts.items()):
            page = self.session.raw_page(key)
            if page is None:
                await self._end(cast)
                continue
            for viewer, at in list(cast.viewers.items()):
                if now - at > VIEWER_TIMEOUT:
                    cast.viewers.pop(viewer, None)
                    dropped.append((key, viewer))
            if not cast.viewers:
                await self._end(cast, notify=False)
            elif page is not cast.page:   # the agent's tab was closed and opened again: watch the new one
                await self._follow(cast, page)
        for key, device in self.session.controls().items():
            k = (key, device)
            if self.session.raw_page(key) is None:
                await self.session.release_control(key)
            elif not self._conns.get(k) and now - self._seen.get(k, now) > CONTROL_GRACE:
                await self.session.release_control(key)
                await self._drop_cdp(key)
        return dropped

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        for cast in list(self.casts.values()):
            await self._end(cast, notify=False)


async def pump_frames(hub: LiveHub, key: str, send: Callable[[Frame], Awaitable[None]], *, fps: Callable[[], int],
                      idle: Callable[[], Awaitable[None]] | None = None, after: int = 0) -> None:
    """Send the newest frame of a tab to one viewer, at most `fps()` a second, until the live view ends (LiveEnded). Only the newest
    frame is ever sent: while `send` is slow, frames that come and go meanwhile are skipped, never queued. `idle` runs about once
    a second when there is nothing new."""
    last_sent = 0.0
    while True:
        frame = await hub.wait_frame(key, after, 1.0)
        if frame is None:
            if idle is not None:
                await idle()
            continue
        gap = 1.0 / max(1, fps()) - (time.monotonic() - last_sent)
        if gap > 0:
            await asyncio.sleep(gap)
            frame = hub.latest(key) or frame   # whatever is newest after the pause
        await send(frame)
        last_sent = time.monotonic()
        after = frame.seq
