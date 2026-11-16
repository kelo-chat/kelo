"""One live interface, two sources: what the live-view routes (api/browser.py) use of whatever they show and the user takes over.

A source is the browser's tabs (`tools/live.LiveHub`) or the Android device (`android/live.DeviceLive`). The routes never ask which
kind it is: `LiveSources.get(key)` hands them the one that owns the key (the key `android` is the device, every other key is a browser
tab) and they call only what `LiveSource` lists: a ticket, joining and leaving as a viewer, the newest frame, take / give back control
and input from the controlling device.

`LiveError`, `LiveEnded`, `Frame` and `Tickets` are shared by both sources and live in `tools/live.py` (the older module); the
protocol is the contract, the browser hub implements it unchanged in behaviour.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol

from .tools.live import Frame

DEVICE_KEY = "android"


class LiveCast(Protocol):
    """What joining a source gives back: the picture settings the viewers share (max_width, quality, fps, every_nth_frame)."""

    settings: dict[str, int]


class LiveSource(Protocol):
    """Frames of one or more things to watch (by key) and the user's hands on them."""

    kind: str                                   # "browser" | "device"
    listeners: list[Callable[[str], Any]]       # key -> something about it changed (control, end): the status line is sent again

    def ticket(self, key: str, device: str) -> str: ...
    def redeem(self, ticket: str, key: str) -> str | None: ...
    def available(self, key: str) -> bool: ...                       # is there something to watch under this key now
    async def join(self, key: str, viewer: str, **quality: Any) -> LiveCast: ...   # LiveError 404/502
    async def leave(self, key: str, viewer: str) -> None: ...
    def touch(self, key: str, viewer: str) -> None: ...
    def watching(self, key: str) -> int: ...
    def end_reason(self, key: str, viewer: str, cast: LiveCast) -> str: ...   # "" = going on, "tab_closed", "no_ping"
    async def configure(self, key: str, max_width: Any = None, quality: Any = None, fps: Any = None,
                        every_nth_frame: Any = None) -> dict[str, int]: ...
    async def wait_frame(self, key: str, after: int, timeout: float) -> Frame | None: ...   # LiveEnded
    def latest(self, key: str) -> Frame | None: ...
    def connected(self, key: str, device: str, delta: int) -> None: ...
    def seen(self, key: str, device: str) -> None: ...
    def holder(self, key: str) -> str | None: ...                    # the device that holds control now, or None (the agent)
    def take(self, key: str, device: str) -> None: ...
    async def give_back(self, key: str, device: str) -> None: ...
    async def input(self, key: str, device: str, ev: dict[str, Any]) -> dict[str, Any]: ...
    def agent_turn(self, key: str, turns: Iterable[Any]) -> Any | None: ...   # the running turn working with this key, if any


class LiveSources:
    """Which source owns a key. The device is only there when the server has one set up (ANDROID_DEVICE)."""

    def __init__(self, browser: LiveSource | None = None, device: LiveSource | None = None):
        self.browser, self.device = browser, device

    def get(self, key: str) -> LiveSource | None:
        """The source for `key`, or None (no browser on this server; no device set up)."""
        return self.device if key == DEVICE_KEY else self.browser
