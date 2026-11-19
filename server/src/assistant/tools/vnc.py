"""The live view of a browser tab as the browser's real X screen, streamed over RFB (VNC) to the phone's own viewer.

The browser container runs an X server that also speaks RFB (docker/entrypoint.sh: TigerVNC's Xvnc, republished on :5901 for the server
like DevTools on :9223). The route `/api/browser/sessions/{key}/vnc` (api/browser.py) relays the viewer's bytes to it and back. This
module is what the route stands on, with no I/O of its own except `XScreen`:

- `ClientFilter`: reads the viewer's RFB bytes message by message and drops the ones that act on the browser (pointer, key, clipboard)
  unless the viewer holds the tab. Pure; no I/O.
- `XScreen`: asks the X server for another screen size over a short RFB connection of its own (SetDesktopSize, RFB's ExtendedDesktopSize).
- `VncScreen`: the arbiter. One X screen shows one tab at a time; viewers of that tab share it; a second tab is told it is busy and the
  app shows the picture stream (tools/live.py) instead. It brings the tab forward and puts everything back when the last viewer
  leaves. The screen keeps its desktop size (the page is not laid out again) and the viewer scales the whole picture down, zooming
  in when the owner pinches.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import struct
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("uvicorn.error")

DEFAULT_SIZE = (1440, 900)      # the screen the browser container starts with (docker image env SCREEN), and what a screen goes back to
VNC_PORT = 5901                 # the browser container's RFB, on the private browser network only (never published)
WORK_VNC_PORT = 5903           # the work browser container's RFB (work-browser in compose), same network rules
RESIZE_SECONDS = 5.0
CONNECT_SECONDS = 5.0           # the viewer's own connection to the X server
STEP_SECONDS = 3.0              # one DevTools call while a screen is set up or put back
ENC_RAW, ENC_DESKTOP_SIZE, ENC_LAST_RECT, ENC_QEMU_KEY, ENC_EXT_DESKTOP_SIZE = 0, -223, -224, -258, -308

# Client message types the server drops from a viewer that does not hold the tab: PointerEvent, KeyEvent, ClientCutText.
# (The QEMU extended key event, type 255 sub-type 0, is a key event too: noVNC sends it when the server offers it.)
TYPE_KEY, TYPE_POINTER, TYPE_CUT_TEXT, TYPE_QEMU = 4, 5, 6, 255
INPUT_TYPES = frozenset({TYPE_KEY, TYPE_POINTER, TYPE_CUT_TEXT})
WHOLE_SIZE = {TYPE_KEY: 8, TYPE_POINTER: 6}   # the whole of a KeyEvent / PointerEvent: judged when all of it is in


class VncError(Exception):
    """RFB bytes that make no sense, or an X server that does not answer or refuses."""


class VncUnavailable(VncError):
    """The screen could not be set up for a tab (the server has no X screen, or it did not answer): the app shows pictures instead."""


def _u16(b: bytes, at: int) -> int:
    return int.from_bytes(b[at:at + 2], "big")


def _u32(b: bytes) -> int:
    return int.from_bytes(b[:4], "big")


def _i32(b: bytes, at: int) -> int:
    return int.from_bytes(b[at:at + 4], "big", signed=True)


def _qemu_total(head: bytes) -> int:
    if head[1] == 0:    # extended key event: type, sub-type, down flag u16, keysym u32, keycode u32
        return 12
    if head[1] == 1:    # audio: type, sub-type, operation u16
        return 4
    raise VncError(f"unknown QEMU message {head[1]}")


# message type -> (the bytes needed to know its length, the whole length from those bytes). Only what a client sends.
RFB_CLIENT_SIZES: dict[int, tuple[int, Callable[[bytes], int]]] = {
    0: (1, lambda h: 20),                           # SetPixelFormat
    2: (4, lambda h: 4 + 4 * _u16(h, 2)),           # SetEncodings: n encodings of 4 bytes
    3: (1, lambda h: 10),                           # FramebufferUpdateRequest
    TYPE_KEY: (1, lambda h: 8),                     # KeyEvent
    TYPE_POINTER: (1, lambda h: 6),                 # PointerEvent
    TYPE_CUT_TEXT: (8, lambda h: 8 + abs(_i32(h, 4))),   # ClientCutText: the text (a negative length is the extended clipboard's)
    150: (1, lambda h: 10),                         # EnableContinuousUpdates
    248: (9, lambda h: 9 + h[8]),                   # Fence
    250: (1, lambda h: 4),                          # xvp
    251: (8, lambda h: 8 + 16 * h[6]),              # SetDesktopSize: n screens of 16 bytes
    TYPE_QEMU: (2, _qemu_total),                    # QEMU extended key event / audio
}

_VERSION = re.compile(rb"RFB 00\d\.00\d\n")


class ClientFilter:
    """What the server forwards of one viewer's RFB bytes. `feed(data)` returns the bytes to send on to the X server.

    The first bytes are the handshake (RFB 3.8, security None): the 12-byte version, one byte for the chosen security type and the one-byte
    ClientInit (the version 3.3 has no security choice). They pass as they are. After that every message is read by its type; the ones
    in INPUT_TYPES (and the QEMU key event) are dropped while `can_input()` is False, asked once per message. Messages may arrive
    split anywhere: a message is judged when its first bytes are in and the rest is passed or dropped as it comes, so nothing is
    buffered beyond a few header bytes (a clipboard is never held in memory). An unknown message type raises VncError: the relay closes."""

    def __init__(self, can_input: Callable[..., bool], on_release: Callable[[], None] | None = None):
        self.can_input = _asks_active(can_input)
        self.on_release = on_release          # called when a kept PointerEvent lets go of the last button (pages focus a field on mouseup)
        self._buf = bytearray()
        self._version = bytearray()           # the viewer's version string while it comes in
        self._handshake = 12                  # handshake bytes still to pass: the version first, then the choice and ClientInit
        self._tail = 0                        # bytes of the current message still to come
        self._keep = True                     # ...and whether they are passed on
        self.mask = 0                         # the button mask the X server last got (kept messages only)
        self.x = self.y = 0                   # ...and where the pointer was
        self.keys_down: set[int] = set()      # keysyms pressed and not yet released, as the X server got them

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        self._buf += data
        while self._buf:
            if self._handshake:
                n = min(self._handshake, len(self._buf))
                out += self._buf[:n]
                reading_version = len(self._version) < 12
                if reading_version:
                    self._version += self._buf[:n]
                self._handshake -= n
                del self._buf[:n]
                if reading_version and len(self._version) == 12:
                    if not _VERSION.fullmatch(bytes(self._version)):
                        raise VncError("not an RFB version")
                    self._handshake = 1 if self._version == b"RFB 003.003\n" else 2   # 3.3: no security choice, only ClientInit
                continue
            if self._tail:
                n = min(self._tail, len(self._buf))
                if self._keep:
                    out += self._buf[:n]
                del self._buf[:n]
                self._tail -= n
                continue
            rule = RFB_CLIENT_SIZES.get(self._buf[0])
            if rule is None:
                raise VncError(f"unknown RFB client message {self._buf[0]}")
            head, total_of = rule
            kind = self._buf[0]
            if kind in WHOLE_SIZE and len(self._buf) < WHOLE_SIZE[kind]:
                break                          # a pointer or key event is judged once it is whole (its button mask / key is read)
            if len(self._buf) < head:
                break
            total = total_of(bytes(self._buf[:head]))
            drops = kind in INPUT_TYPES or (kind == TYPE_QEMU and self._buf[1] == 0)
            active = True
            if kind == TYPE_POINTER:
                mask = self._buf[1]
                x, y = struct.unpack(">HH", bytes(self._buf[2:6]))
                active = bool(mask) or mask != self.mask   # a press, a drag or a release: not a bare move
            self._keep = not drops or bool(self.can_input(active))
            take = min(total, len(self._buf))
            if self._keep:
                out += self._buf[:take]
                if kind == TYPE_POINTER:
                    released = bool(self.mask) and not mask
                    self.mask, self.x, self.y = mask, x, y
                    if released and self.on_release is not None:
                        try:
                            self.on_release()
                        except Exception:  # noqa: BLE001 - a look at the focus must never break the relay
                            pass
                elif kind == TYPE_KEY:
                    _, down, key = struct.unpack(">BBxxI", bytes(self._buf[:8]))
                    (self.keys_down.add if down else self.keys_down.discard)(key)
            del self._buf[:take]
            self._tail = total - take
        return bytes(out)

    def lift(self) -> bytes:
        """What lets go of the X server's buttons and keys when the viewer's hold ends or the viewer leaves mid-press: a PointerEvent with
        no button at the last place, and a KeyEvent up for every key still down. b"" when nothing is held."""
        out = b""
        if self.mask:
            out += struct.pack(">BBHH", TYPE_POINTER, 0, self.x, self.y)
            self.mask = 0
        for key in sorted(self.keys_down):
            out += struct.pack(">BBxxI", TYPE_KEY, 0, key)
        self.keys_down.clear()
        return out


def _asks_active(can_input: Callable[..., bool]) -> Callable[[bool], bool]:
    """A `can_input` that takes the `active` flag gets it; one that takes no argument (the old form) is called without it."""
    try:
        takes = any(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL) for p in inspect.signature(can_input).parameters.values())
    except (TypeError, ValueError):
        takes = True
    return (lambda active: can_input(active)) if takes else (lambda active: can_input())


class _Conn:
    """One short RFB connection to the X server, after its handshake."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer
        self.size = (0, 0)
        self.bpp = 32


class XScreen:
    """The size of the browser container's X screen, asked over short RFB connections of its own (never the viewer's)."""

    def __init__(self, host: str, port: int = VNC_PORT, *, timeout: float = RESIZE_SECONDS):
        self.host, self.port, self.timeout = host, port, timeout

    async def size(self) -> tuple[int, int]:
        """The screen's size now (ServerInit)."""
        async def read(conn: _Conn) -> tuple[int, int]:
            return conn.size
        return await self._run(read)

    async def resize(self, width: int, height: int) -> None:
        """Make the X screen `width` x `height` (VncError when the server refuses or does not answer in `timeout` seconds)."""
        await self._run(lambda conn: self._set_size(conn, int(width), int(height)))

    async def _run(self, work: Callable[[_Conn], Awaitable[Any]]) -> Any:
        writer: asyncio.StreamWriter | None = None

        async def go() -> Any:
            nonlocal writer
            reader, writer = await asyncio.open_connection(self.host, self.port)
            conn = _Conn(reader, writer)
            await self._handshake(conn)
            return await work(conn)

        try:
            return await asyncio.wait_for(go(), self.timeout)
        except VncError:
            raise
        except (asyncio.TimeoutError, OSError, asyncio.IncompleteReadError) as e:
            raise VncError(f"the X screen did not answer ({type(e).__name__})") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    @staticmethod
    async def _handshake(conn: _Conn) -> None:
        """RFB 3.8, security None, shared; remembers ServerInit's size and pixel size."""
        reader, writer = conn.reader, conn.writer
        version = await reader.readexactly(12)
        if version != b"RFB 003.008\n":
            raise VncError(f"the X screen speaks {version!r}, not RFB 3.8")
        writer.write(b"RFB 003.008\n")
        count = (await reader.readexactly(1))[0]
        if count == 0:
            raise VncError("the X screen refused the connection")
        if 1 not in await reader.readexactly(count):
            raise VncError("the X screen wants a password")
        writer.write(b"\x01")
        if _u32(await reader.readexactly(4)) != 0:
            raise VncError("the X screen refused the connection")
        writer.write(b"\x01")   # ClientInit: shared, so nobody else is disconnected
        conn.size = struct.unpack(">HH", await reader.readexactly(4))
        conn.bpp = (await reader.readexactly(16))[0]
        await reader.readexactly(_u32(await reader.readexactly(4)))   # the desktop's name

    async def _set_size(self, conn: _Conn, width: int, height: int) -> None:
        if conn.size == (width, height):
            return
        writer = conn.writer
        writer.write(struct.pack(">BxHii", 2, 2, ENC_RAW, ENC_EXT_DESKTOP_SIZE))      # SetEncodings: Raw, ExtendedDesktopSize
        writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))                           # FramebufferUpdateRequest: not incremental, 1 x 1
        await writer.drain()
        screens = (await self._until_extended_size(conn, None))[1]
        screen_id, flags = (screens[0][0], screens[0][5]) if screens else (0, 0)
        writer.write(struct.pack(">BxHHBx", 251, width, height, 1) + struct.pack(">IHHHHI", screen_id, 0, 0, width, height, flags))
        writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))   # the answer rides on the next update, and the server sends one only when asked
        await writer.drain()
        status = (await self._until_extended_size(conn, 1))[0]
        if status != 0:
            raise VncError(f"the X screen refused {width}x{height} (code {status})")

    @staticmethod
    async def _until_extended_size(conn: _Conn, reason: int | None) -> tuple[int, list[tuple[int, ...]]]:
        """Read server messages until an ExtendedDesktopSize rectangle (of this `reason`, when given): (its status, its screens)."""
        reader = conn.reader
        while True:
            kind = (await reader.readexactly(1))[0]
            if kind == 0:         # FramebufferUpdate
                count = _u16(await reader.readexactly(3), 1)
                for _ in range(count):
                    x, y, w, h, enc = struct.unpack(">HHHHi", await reader.readexactly(12))
                    if enc == ENC_RAW:
                        await reader.readexactly(w * h * conn.bpp // 8)
                    elif enc == ENC_EXT_DESKTOP_SIZE:
                        head = await reader.readexactly(4)
                        screens = [struct.unpack(">IHHHHI", await reader.readexactly(16)) for _ in range(head[0])]
                        if reason is None or x == reason:
                            return y, screens
                    elif enc == ENC_LAST_RECT:
                        break
                    elif enc in (ENC_DESKTOP_SIZE, ENC_QEMU_KEY):
                        pass
                    else:
                        raise VncError(f"the X screen sent an encoding that was not asked for ({enc})")
            elif kind == 1:       # SetColourMapEntries
                n = _u16(await reader.readexactly(5), 3)
                await reader.readexactly(6 * n)
            elif kind == 2:       # Bell
                pass
            elif kind == 3:       # ServerCutText
                await reader.readexactly(_u32((await reader.readexactly(7))[3:7]))
            else:
                raise VncError(f"unknown RFB server message {kind}")


class VncScreen:
    """Who owns the X screen. One tab key at a time; more devices on the same key share it.

    `acquire(key, device)` is True when the screen shows `key` now, False when another tab holds it (busy), and raises VncUnavailable
    when the screen could not be set up. `release(key, device)` after it. The last release (or the tab closing: `tab_closed`) puts the
    screen back."""

    def __init__(self, session: Any, xscreen: XScreen, *, default: tuple[int, int] = DEFAULT_SIZE,
                 refit: Callable[[str], Awaitable[Any]] | None = None):
        self.session = session
        self.xscreen = xscreen
        self.default = default
        self.refit = refit                      # key -> the live view's own tab size again (LiveHub.refit) once the screen is released
        self.key: str | None = None
        self.size: tuple[int, int] = default
        self._devices: dict[str, int] = {}      # device -> its open screens of this tab
        self._lock = asyncio.Lock()
        self.gone: dict[str, asyncio.Event] = {}  # key -> set when the tab closed under its viewers

    @property
    def upstream(self) -> tuple[str, int]:
        """Where the viewers' RFB bytes go: the X server's address (host, port)."""
        return self.xscreen.host, self.xscreen.port

    def upstream_of(self, key: str) -> tuple[str, int]:
        """Where the viewers of this tab's X screen go (the same for every key of this screen)."""
        return self.upstream

    def holds(self, key: str) -> bool:
        return self.key == key

    def viewing(self, key: str) -> bool:
        """Whether someone is on this tab's X screen right now."""
        return self.key == key and bool(self._devices)

    def size_of(self, key: str) -> tuple[int, int] | None:
        return self.size if self.key == key else None

    async def acquire(self, key: str, device: str) -> bool:
        async with self._lock:
            if self.key is not None and self.key != key:
                return False
            if self.key == key:
                self._devices[device] = self._devices.get(device, 0) + 1
                return True
            try:
                size = await self._set_up(key)
            except Exception as e:  # noqa: BLE001 - whatever failed, the screen is put back and the viewer is told it is unavailable
                await self._tear_down(key)
                log.info("live view: the screen could not be set up (%s)", type(e).__name__)
                raise VncUnavailable(str(e) or type(e).__name__) from None
            self.key, self.size = key, size
            self._devices = {device: 1}
            self.gone[key] = asyncio.Event()
            return True

    async def release(self, key: str, device: str) -> None:
        async with self._lock:
            if self.key != key or device not in self._devices:
                return
            self._devices[device] -= 1
            if self._devices[device] <= 0:
                del self._devices[device]
            if not self._devices:
                await self._free(key)

    async def tab_closed(self, key: str) -> None:
        """The tab is gone: its viewers' sockets are told (`gone`) and the screen goes back."""
        async with self._lock:
            if self.key != key:
                return
            event = self.gone.get(key)
            if event is not None:
                event.set()
            self._devices.clear()
            await self._free(key, tab_alive=False)

    async def tab_switched(self, key: str) -> None:
        """The held tab's window changed its active tab (the owner switched tabs): the new one is brought forward."""
        if self.key == key:
            await self.refront()

    async def refront(self) -> None:
        """Bring the held tab forward again (a new window opened on this screen took the front). Best effort, under the lock."""
        async with self._lock:
            key = self.key
            if key is None:
                return
            try:
                cdp, temporary = await self._cdp(key)
            except Exception as e:  # noqa: BLE001 - the tab is gone: nothing to bring forward
                log.debug("live view: refront found no tab (%s)", type(e).__name__)
                return
            try:
                target = await self._target(key, cdp)
                await self._try(cdp, "Target.activateTarget", {"targetId": target})
                await self._try(cdp, "Page.bringToFront")
                await self._fullscreen(cdp, target)
            finally:
                if temporary:
                    await self._quietly(cdp.detach)

    async def _free(self, key: str, tab_alive: bool = True) -> None:
        self.key, self.size = None, self.default
        self.gone.pop(key, None)
        await self._tear_down(key, tab_alive=tab_alive)

    # ------------------------------------------------------------------ the tab and the screen
    async def _cdp(self, key: str) -> tuple[Any, bool]:
        """A DevTools session on the tab, and whether it is a temporary one to detach."""
        cdp = self.session.tab_cdp(key)
        if cdp is not None:
            return cdp, False
        page = self.session.raw_page(key)
        if page is None:
            raise VncError("no such tab")
        return await self.session.cdp_session(page), True

    async def _send(self, cdp: Any, method: str, params: dict[str, Any] | None = None) -> Any:
        return await asyncio.wait_for(cdp.send(method, params or {}), STEP_SECONDS)

    async def _set_up(self, key: str) -> tuple[int, int]:
        """Bring the tab forward and make the screen right for it; the screen's size now. The screen keeps its desktop size: the page
        is laid out as wide as a desktop browser and the viewer scales the picture down."""
        cdp, temporary = await self._cdp(key)
        try:
            target = await self._target(key, cdp)
            await self._try(cdp, "Target.activateTarget", {"targetId": target})
            await self._try(cdp, "Page.bringToFront")
            size = await self._desktop_screen()
            await self._fullscreen(cdp, target)
            return size
        finally:
            if temporary:
                await self._quietly(cdp.detach)

    async def _desktop_screen(self) -> tuple[int, int]:
        """The screen a tab is shown on: as it is. One narrower than the desktop default (a size a crashed server left
        behind) is put back to the default first, so a desktop page is never laid out narrow."""
        size = await self.xscreen.size()
        if size[0] < self.default[0]:
            await self.xscreen.resize(*self.default)
            return self.default
        return size

    async def _tear_down(self, key: str, *, tab_alive: bool = True) -> None:
        """Let the tab go: its live view is laid out again (each step on its own: a failed one stops no other)."""
        if not tab_alive or self.session.raw_page(key) is None:
            return
        if self.refit is not None:
            await self._quietly(lambda: self.refit(key))

    async def _target(self, key: str, cdp: Any) -> str:
        known = self.session.target_id(key)
        if known:
            return known
        info = (await self._send(cdp, "Target.getTargetInfo")).get("targetInfo") or {}
        return str(info.get("targetId") or "")

    async def _fullscreen(self, cdp: Any, target: str) -> None:
        try:
            window = (await self._send(cdp, "Browser.getWindowForTarget", {"targetId": target} if target else {}))["windowId"]
            await self._send(cdp, "Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "fullscreen"}})
        except Exception as e:  # noqa: BLE001 - the kiosk window already fills the screen
            log.debug("live view: the window could not be set fullscreen (%s)", type(e).__name__)

    async def _try(self, cdp: Any, method: str, params: dict[str, Any] | None = None) -> None:
        try:
            await self._send(cdp, method, params)
        except Exception as e:  # noqa: BLE001 - bringing the tab forward is best effort: the screen is still sized
            log.debug("live view: %s failed (%s)", method, type(e).__name__)

    @staticmethod
    async def _quietly(make: Callable[[], Awaitable[Any]]) -> None:
        try:
            await make()
        except Exception as e:  # noqa: BLE001 - putting back: each step on its own
            log.debug("live view: put-back step failed (%s)", type(e).__name__)


class VncScreens:
    """The main browser's X screen and the work browser's, chosen by tab key (tools/work_browser.work_tab). Same calls as one VncScreen, so
    the routes and the live hub do not care which screen a tab is on: watching or holding a work tab never moves the owner's screen, and
    the owner's tab never makes a work tab "busy" (owner decision 2026-11-18, brief section 2 point 3)."""

    def __init__(self, main: VncScreen, work: VncScreen | None, pick: Callable[[str], bool]):
        self.main, self.work, self.pick = main, work, pick

    def screen(self, key: str) -> VncScreen:
        return self.work if self.work is not None and self.pick(key) else self.main

    def upstream_of(self, key: str) -> tuple[str, int]:
        return self.screen(key).upstream_of(key)

    async def acquire(self, key: str, device: str) -> bool:
        return await self.screen(key).acquire(key, device)

    async def release(self, key: str, device: str) -> None:
        await self.screen(key).release(key, device)

    async def tab_closed(self, key: str) -> None:
        for screen in self._screens():
            await screen.tab_closed(key)

    def holds(self, key: str) -> bool:
        return self.screen(key).holds(key)

    def viewing(self, key: str) -> bool:
        return self.screen(key).viewing(key)

    def size_of(self, key: str) -> tuple[int, int] | None:
        return self.screen(key).size_of(key)

    @property
    def gone(self) -> dict[str, asyncio.Event]:
        return {k: v for screen in self._screens() for k, v in screen.gone.items()}

    @property
    def refit(self) -> Callable[[str], Awaitable[Any]] | None:
        return self.main.refit

    @refit.setter
    def refit(self, value: Callable[[str], Awaitable[Any]] | None) -> None:
        for screen in self._screens():
            screen.refit = value

    async def tab_switched(self, key: str) -> None:
        """A tab of a held window was switched to: that screen brings the new active tab forward (the other screen is not touched)."""
        await self.screen(key).tab_switched(key)

    async def work_window_opened(self, key: str) -> None:
        """A new window of a work tab appeared: when a work tab is held on the work screen, that one is brought forward again."""
        if self.work is not None and self.work.key is not None:
            await self.work.refront()

    def _screens(self) -> list[VncScreen]:
        return [self.main] if self.work is None else [self.main, self.work]

