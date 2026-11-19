"""Live view of one browser tab for the phone app, and the user's own hands on it ("take control").

Frames: Chrome DevTools `Page.startScreencast` (JPEG) on the tab, one screencast per tab however many phones watch it. Every frame is
acknowledged (`Page.screencastFrameAck`); acknowledgements are spaced 1/fps apart, which is what paces Chrome (it sends the next frame
only after an acknowledgement), so a busy page never costs more than the asked frame rate. While a device holds the tab (takes control)
the rate is HELD_FPS (30) instead, so the owner's own screen moves smoothly; a tab that only people watch keeps its asked fps (at most
MAX_FPS). After the owner's last input a sharper picture (SETTLE_QUALITY) is taken SETTLE_AFTER seconds later, once the page is still.
`everyNthFrame` stays 1 by default:
Chrome counts *painted* frames, not screen refreshes, so a larger value makes a slowly changing page look frozen. A page that paints
nothing after the start (a still page) gets one picture taken with `Page.captureScreenshot` so a new viewer never stares at nothing.

Viewers are counted per tab: the screencast stops when the last one leaves, and a viewer that sent nothing for
`VIEWER_TIMEOUT` seconds (no ping, no poll) counts as gone. Each viewer only ever gets the newest frame: a slow phone skips frames,
nothing queues up behind it (at most one frame waits, and it is replaced by a newer one).

Control: a device takes a tab (`take`); only that device may send input; it keeps the tab while it has a live-view connection open
or has sent something in the last `CONTROL_GRACE` seconds, else the tab goes back to the agent. Coordinates come in frame pixels
(the JPEG the phone shows) and are turned into CSS pixels of the viewport with the frame's own metadata (see `map_point`).
Typed text is never logged or kept. Live typing (LIVE_V 3): the `edit` input turns the owner's keyboard changes (composition, text, Backspace,
keys) into Input.imeSetComposition / insertText / key events on the tab, so the page shows each keystroke and each syllable as it forms.
"""

from __future__ import annotations

import asyncio
import base64
import io
import hashlib
import logging
import math
import os
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from urllib.parse import urlsplit

from . import humanize as human
from .browser import BrowserSession, cdp_send
from .holds import BUTTON, HANDOVER, Holds

log = logging.getLogger("uvicorn.error")

DEFAULT_MAX_WIDTH, DEFAULT_QUALITY, DEFAULT_FPS = 720, 55, 10
MIN_WIDTH, MAX_WIDTH = 360, 1080
MIN_QUALITY, MAX_QUALITY = 40, 80
MAX_FPS = 15               # the most a viewer who only watches may ask for
HELD_FPS = 30              # the frame rate while a device holds the tab (LiveHub.rate): the owner's own screen
VIEWER_TIMEOUT = 60.0      # a viewer silent this long is dropped (and the screencast stops when it was the last one)
CONTROL_GRACE = 30.0       # a hand-over hold (the app took the tab because Kelo asked) with no live connection for this long hands it back;
                           # a button hold ("직접 조작") never times out: only the owner hands it back (tools/holds.py)
TICKET_SECONDS = 30
SEED_AFTER = 0.5           # no frame this long after a start: take one picture instead
LONG_PRESS_MS = 700
MAX_TEXT = 2000            # characters per text event
REAP_EVERY = 1.0
INPUT_SECONDS = 3.0        # one DevTools call for the user's input: a page that does not take it in this time is busy (answered, not waited for)
STALE_AFTER = 2.0          # a running screencast with no new frame this long is looked at: does the page still answer?
PROBE_SECONDS = 3.0        # ...a page that does not answer a script call in this time has a blocked main thread: its picture is stale
WATCH_EVERY = 1.0
SECURE_EVERY = 1.0         # a browser tab is looked at for a password or card field at most this often (Cast.secure is kept between)
BUSY_NOTE = "the page is not responding"
POINTER_EVERY = 0.05       # the agent's pointer goes out at most 20 times a second (PointerThrottle); a press or release always at once
LIVE_V = 3                 # the live protocol version in every frame header (see docs/versioning.md): 2 = `view`, `frame_seq`, `editing`;
                           # 3 = live typing: the `edit` input, `kind` and `field` of the focused field
RECENT_FRAMES = 8          # frames whose size and metadata are kept, so an input is mapped with the frame the owner was looking at
FLOOR_DESKTOP = 500          # narrowest page (CSS px) the owner's screen may ask of a tab: narrower is scaled up
MAX_VIEW_W, MAX_VIEW_H = 1920, 4000      # widest and tallest page (CSS px) the owner's screen may ask of a tab
EDITING_EVERY = 0.5        # a controlled tab is looked at for a focused text field at most this often (Cast.editing is kept between)
SETTLE_AFTER = 0.25        # seconds without input after which the controlled tab gets a sharp picture (see LiveHub._settle)
SETTLE_QUALITY = 85        # JPEG quality of that sharp picture (the live view itself uses the viewer's quality, 40..80)
PRESS_AFTER_MOVE = 0.025   # a tap moves the pointer, waits this long, then presses: pages that react to the hover first see the press
# key -> (windowsVirtualKeyCode, text it types)
KEYS: dict[str, tuple[int, str]] = {"Enter": (13, "\r"), "Backspace": (8, ""), "Tab": (9, ""), "Escape": (27, ""),
                                    "ArrowLeft": (37, ""), "ArrowUp": (38, ""), "ArrowRight": (39, ""), "ArrowDown": (40, ""),
                                    "Delete": (46, ""), "Home": (36, ""), "End": (35, ""), "PageUp": (33, ""), "PageDown": (34, "")}
INPUT_TYPES = ("tap", "long_press", "scroll", "text", "key", "back", "forward", "reload", "edit")
POKE_FIRST, POKE_SECOND = 0.06, 0.25   # looks at the focused field after a pointer release (poke_editing)
EDITING_KINDS = ("secret", "number", "tel", "email", "url", "search", "multiline", "text")
EDIT_MAX_STEPS = 64        # steps in one `edit` event
EDIT_MAX_BACK = 200        # Backspaces in one `back` step
COMPOSE_MAX = 200          # characters of one composition
# KELO_LIVE_COMPOSE=0 turns every `compose` step into delete-and-retype (committed text and Backspace), for a site that misbehaves with a
# real composition (Input.imeSetComposition is marked experimental in the DevTools protocol)
COMPOSE_ON = os.environ.get("KELO_LIVE_COMPOSE", "1").strip() != "0"


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
    secure: bool | None = None   # a secret field (password, card, one-time code) shows: None = not said (old frame)
    editing: bool = False        # a text field has focus on the page (the app may raise the keyboard)
    kind: str = "text"           # what keyboard it fits (see EDITING_JS)
    field_id: str = ""           # an opaque id of the focused field (8 hex), "" when none
    live_v: int = LIVE_V         # a phone-device take-over says 2: its typing is the device's own (no `edit` input there)

    def header(self) -> dict[str, Any]:
        m = self.meta
        out = {"type": "frame", "seq": self.seq, "w": self.w, "h": self.h, "ts": round(self.ts, 3), "url": self.url, "title": self.title,
               "bytes": len(self.jpeg), "css_w": m.get("deviceWidth"), "css_h": m.get("deviceHeight"), "live_v": self.live_v,
               "editing": self.editing, "kind": self.kind, "field": self.field_id}
        if self.secure is not None:
            out["secure"] = self.secure   # new and optional (see LIVE VIEW API in server.py)
        return out


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
    """A point of the frame picture (pixels of the JPEG, top-left 0,0) -> the CSS pixels of the page's viewport, which is what
    Input.dispatchMouseEvent / dispatchTouchEvent take. The picture shows the whole device screen (deviceWidth x deviceHeight screen
    units, scaled down to the JPEG's size); the page area starts `offsetTop` screen units below its top. A page that is zoomed out
    (a phone page with no viewport meta, or content wider than the screen: pageScaleFactor 0.42) shows `1 / pageScaleFactor` CSS
    pixels per screen unit, and Chrome reads the input point in those CSS pixels (2026-11-16: taps landed 2.4 times too far in on such
    pages). Scrolling does not enter: input is relative to the viewport. The result is kept inside the viewport."""
    dw, dh = float(meta.get("deviceWidth") or frame_w), float(meta.get("deviceHeight") or frame_h)
    top = float(meta.get("offsetTop") or 0.0)
    scale = float(meta.get("pageScaleFactor") or 1.0) or 1.0
    sx, sy = dw / max(1, frame_w) / scale, dh / max(1, frame_h) / scale
    cx = min(max(x * sx, 0.0), dw / scale - 1)
    cy = min(max(y * sy - top / scale, 0.0), max(0.0, (dh - top) / scale - 1))
    return round(cx, 1), round(cy, 1)


def page_point(cx: float, cy: float, meta: dict[str, Any]) -> tuple[float, float]:
    """CSS viewport point -> document coordinates: scrollOffset + point (for reports; input does not need it)."""
    return (round(float(meta.get("scrollOffsetX") or 0) + cx, 1), round(float(meta.get("scrollOffsetY") or 0) + cy, 1))


def map_delta(dx: float, dy: float, frame_w: int, frame_h: int, meta: dict[str, Any]) -> tuple[float, float]:
    """A scroll distance in frame pixels -> CSS pixels (wheel deltas)."""
    dw, dh = float(meta.get("deviceWidth") or frame_w), float(meta.get("deviceHeight") or frame_h)
    scale = float(meta.get("pageScaleFactor") or 1.0) or 1.0
    lim = 20_000.0
    return (round(max(-lim, min(lim, dx * dw / max(1, frame_w) / scale)), 1),
            round(max(-lim, min(lim, dy * dh / max(1, frame_h) / scale)), 1))


def jpeg_size(data: bytes) -> tuple[int, int]:
    from PIL import Image

    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:  # noqa: BLE001 - a frame we cannot read the size of: unknown
        return 0, 0


def viewer_css_size(width: Any, height: Any, density: Any) -> tuple[int, int, float] | None:
    """What the owner's screen asks of a tab: (CSS width, CSS height, device pixel ratio) for a live area of `width` x `height` physical
    pixels at `density` (the screen's pixels per CSS pixel), or None when the numbers are not usable. The page is laid out as wide as
    the screen is in CSS pixels (physical / density), so one picture pixel is about one dp and a finger covers a finger-sized target.
    A screen narrower than the floor (FLOOR_DESKTOP CSS px) is scaled up to it, keeping its shape; one wider or taller
    than MAX_VIEW_W x MAX_VIEW_H is scaled down. The ratio keeps the picture as sharp as the screen is."""
    def num(v: Any) -> float | None:
        return float(v) if not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v > 0 else None

    w, h, d = num(width), num(height), num(density if density is not None else 1.0)
    if w is None or h is None or d is None:
        return None
    d = min(8.0, d)
    cw, ch = w / d, h / d
    if cw < FLOOR_DESKTOP:
        ch, cw = ch * FLOOR_DESKTOP / cw, float(FLOOR_DESKTOP)
    over = max(cw / MAX_VIEW_W, ch / MAX_VIEW_H, 1.0)
    cw, ch = cw / over, ch / over
    return max(1, round(cw)), max(1, round(ch)), round(w / cw, 3)


class PointerThrottle:
    """What one live-view socket sends of the agent's pointer: {"type":"pointer","x","y","down"} (x, y in CSS px of the viewport, the
    space of the frames' css_w x css_h). Moves are kept to one per POINTER_EVERY (the newest wins); a press or release (`down`
    changes) goes out at once, so a tap is always seen as down true, then false."""

    def __init__(self) -> None:
        self.down = False
        self.last = -1e9
        self.pending: dict[str, Any] | None = None
        self.ready: list[dict[str, Any]] = []

    def offer(self, x: float, y: float, down: bool, now: float) -> None:
        msg = {"type": "pointer", "x": round(x, 1), "y": round(y, 1), "down": bool(down)}
        if bool(down) != self.down:
            self.down = bool(down)
            self.pending = None
            self.ready.append(msg)
            self.last = now
        else:
            self.pending = msg

    def take(self, now: float) -> list[dict[str, Any]]:
        out, self.ready = self.ready, []
        if self.pending is not None and now - self.last >= POINTER_EVERY:
            out.append(self.pending)
            self.pending, self.last = None, now
        return out

    def wait(self, now: float) -> float | None:
        """Seconds until something is due (0: now), None when nothing waits."""
        if self.ready:
            return 0.0
        return max(0.0, POINTER_EVERY - (now - self.last)) if self.pending is not None else None


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
    last_frame: float = 0.0       # time.monotonic() of the newest frame (or of the start)
    stale: bool = False           # the page's main thread does not answer: the picture shown is an old one
    busy_at: float = 0.0          # time.monotonic() of the last user input the page did not take in INPUT_SECONDS
    watcher: Any = None
    secure: bool = True           # a secret field shows in the viewport (true until the first answer)
    secure_at: float | None = None   # clock() of the last look (None = never looked)
    secure_busy: bool = False     # a look is in flight
    recent: dict[int, tuple[int, int, dict[str, Any]]] = field(default_factory=dict)   # frame seq -> (w, h, meta) of the newest RECENT_FRAMES
    editing: bool = False         # a text field has focus (looked at only while a device controls the tab)
    editing_at: float | None = None
    editing_busy: bool = False
    kind: str = "text"            # what keyboard the focused field fits (EDITING_JS), and an opaque id of the field
    field_id: str = ""
    editing_wake: asyncio.Event = field(default_factory=asyncio.Event)   # set whenever editing, kind or field change (the meta pump waits on it)
    composing: str = ""          # the composition the owner's keyboard shows in the page now (memory only, never logged): committed when the hold ends
    settle_task: asyncio.Task[Any] | None = None   # the sharp picture due SETTLE_AFTER the owner's last input (one at a time)


# A page's secret fields: a password, a card number or a one-time code in the viewport or focused. One small script, by value.
SECURE_JS = """(() => {
  const secret = (el) => {
    if (!el || el.tagName !== 'INPUT') return false;
    if (el.type === 'password') return true;
    const ac = (el.getAttribute('autocomplete') || '').trim().toLowerCase();
    return ac.startsWith('cc-') || ac === 'one-time-code';
  };
  if (secret(document.activeElement)) return true;
  const vw = window.innerWidth, vh = window.innerHeight;
  for (const el of document.querySelectorAll('input')) {
    if (!secret(el)) continue;
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width <= 0 || r.height <= 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.right <= 0 || r.bottom <= 0 || r.left >= vw || r.top >= vh) continue;
    return true;
  }
  return false;
})()"""


# A focused text field: an input that takes text (not a button, box or slider), a textarea or an editable region. Answers
# {editing, kind, field}: `kind` is what keyboard fits (secret | number | tel | email | url | search | multiline | text) and `field` an
# opaque description of the field (tag, type, name, id, autocomplete, place) that the server hashes: the app tells a move to another field
# from it. Never the value. A focused frame of another site cannot be looked into: it counts as a text field (better a keyboard too many
# than a card form with none). No global, no property on a page object, no change to the page.
EDITING_JS = """(() => {
  const no = {editing: false, kind: 'text', field: ''};
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round).join(','); };
  let el = document.activeElement;
  for (let n = 0; n < 8 && el; n++) {
    if (el.shadowRoot && el.shadowRoot.activeElement) { el = el.shadowRoot.activeElement; continue; }
    if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
      let d = null;
      try { d = el.contentDocument; } catch (e) { d = null; }
      if (d && d.activeElement) { el = d.activeElement; continue; }
      if (!d) return {editing: true, kind: 'text', field: 'frame:' + rect(el)};
    }
    break;
  }
  if (!el) return no;
  const attr = (n) => (el.getAttribute && el.getAttribute(n) || '').trim().toLowerCase();
  const t = (el.type || 'text').toLowerCase();
  let kind = 'text';
  if (el.isContentEditable) kind = 'multiline';
  else if (el.tagName === 'TEXTAREA') { if (el.readOnly || el.disabled) return no; kind = 'multiline'; }
  else if (el.tagName === 'INPUT') {
    if (['button', 'submit', 'reset', 'checkbox', 'radio', 'file', 'image', 'range', 'color', 'hidden'].includes(t)) return no;
    if (el.readOnly || el.disabled) return no;
    const ac = attr('autocomplete'), im = attr('inputmode');
    if (t === 'password' || ac.startsWith('cc-') || ac === 'one-time-code') kind = 'secret';
    else if (t === 'number' || im === 'numeric' || im === 'decimal') kind = 'number';
    else if (t === 'tel' || im === 'tel') kind = 'tel';
    else if (t === 'email' || im === 'email') kind = 'email';
    else if (t === 'url' || im === 'url') kind = 'url';
    else if (t === 'search' || im === 'search') kind = 'search';
  } else return no;
  return {editing: true, kind: kind, field: [el.tagName, t, attr('name'), el.id || '', attr('autocomplete'), rect(el)].join('|')};
})()"""


async def _key(send: Callable[[str, dict[str, Any]], Awaitable[Any]], name: str) -> None:
    """One named key, pressed and released (Enter types its carriage return)."""
    code, text = KEYS[name]
    base = {"key": name, "code": name, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
    await send("Input.dispatchKeyEvent", {"type": "keyDown" if text else "rawKeyDown", **base, **({"text": text} if text else {})})
    await send("Input.dispatchKeyEvent", {"type": "keyUp", **base})


def _bad(message: str) -> LiveError:
    return LiveError(422, "bad_event", message)


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_edit(ev: dict[str, Any]) -> list[tuple[Any, ...]]:
    """The steps of an `edit` event, all checked before any runs: ("text", s) ("compose", s, cursor) ("back", n) ("key", name).
    Never puts typed text into an error message."""
    raw = ev.get("steps")
    if not isinstance(raw, list) or not raw or len(raw) > EDIT_MAX_STEPS:
        raise _bad(f"steps must be a list of 1..{EDIT_MAX_STEPS} steps")
    out: list[tuple[Any, ...]] = []
    for st in raw:
        if not isinstance(st, list) or not st or not isinstance(st[0], str):
            raise _bad("a step is a list that starts with its name")
        op = st[0]
        if op == "text" and len(st) == 2:
            t = st[1]
            if not isinstance(t, str) or not 1 <= len(t) <= MAX_TEXT or any(ord(c) < 32 for c in t):
                raise _bad(f"text must be 1..{MAX_TEXT} characters without control characters")
            out.append(("text", t))
        elif op == "compose" and len(st) in (2, 3):
            t = st[1]
            if not isinstance(t, str) or len(t) > COMPOSE_MAX or any(ord(c) < 32 for c in t):
                raise _bad(f"compose must be 0..{COMPOSE_MAX} characters without control characters")
            n16 = len(t.encode("utf-16-le")) // 2
            cur = st[2] if len(st) == 3 else n16
            if not _whole(cur) or not 0 <= cur <= n16:
                raise _bad("the compose cursor must be inside the text")
            out.append(("compose", t, cur))
        elif op == "back" and len(st) == 2:
            if not _whole(st[1]) or not 1 <= st[1] <= EDIT_MAX_BACK:
                raise _bad(f"back must be 1..{EDIT_MAX_BACK}")
            out.append(("back", st[1]))
        elif op == "key" and len(st) == 2:
            if st[1] not in KEYS:
                raise _bad(f"key must be one of: {', '.join(KEYS)}")
            out.append(("key", st[1]))
        else:
            raise _bad("unknown step (text, compose, back or key)")
    return out


class LiveHub:
    """Screencasts and control of the browser's tabs, for the phone's live view."""

    kind = "browser"

    def __init__(self, session: BrowserSession, clock: Callable[[], float] = time.monotonic, *, reaper: bool = True,
                 tickets: Tickets | None = None):
        self.session = session
        self.clock = clock
        self.tickets = tickets or Tickets()
        self.casts: dict[str, Cast] = {}
        self._cdps: dict[str, tuple[Any, Any]] = {}          # tab -> (page, CDP session)
        self._conns: dict[tuple[str, str], int] = {}          # (tab, device) -> live connections open
        self._seen: dict[tuple[str, str], float] = {}         # (tab, device) -> last request
        self._screens: dict[tuple[str, str], tuple[Any, Any, Any]] = {}   # (tab, device) -> (width, height, density) the device last said (`view`)
        self._fit_tasks: set[asyncio.Task[Any]] = set()
        self.holds = Holds(clock)                             # who holds each tab, of what kind (tools/holds.py)
        self.book: Any = None                                 # hold_asks.HoldBook: the button holds, saved over a restart (set by server.py)
        self.asker: Any = None                                # hold_asks.HoldAsker: the idle question (set by server.py)
        self._hosts: dict[str, str] = {}                      # tab -> the host it was on when taken (named in the question when the page is gone)
        self._reaper: asyncio.Task[None] | None = None
        self._use_reaper = reaper
        self.listeners: list[Callable[[str], Any]] = []       # tab -> something about it changed (control, end)
        self.vnc: Any = None                                  # the VncScreen (tools/vnc.py) when the browser has an X screen to stream
        session.control_listeners.append(lambda key, _dev: self._changed(key))
        self.pointers: dict[str, dict[str, Any]] = {}         # tab -> {"x","y","down"}: where the agent's pointer is (humanize.pointer_listeners)
        self.pointer_listeners: list[Callable[[str, float, float, bool], Any]] = []   # (tab, x, y, down) for each live-view socket
        human.pointer_listeners.append(self._on_pointer)

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
        cast.last_frame = time.monotonic()
        t = asyncio.ensure_future(self._seed(cast, cast.started_at))
        cast.tasks.add(t)
        t.add_done_callback(cast.tasks.discard)
        if cast.watcher is None or cast.watcher.done():
            cast.watcher = asyncio.ensure_future(self._watch(cast))
            cast.tasks.add(cast.watcher)
            cast.watcher.add_done_callback(cast.tasks.discard)

    async def _watch(self, cast: Cast) -> None:
        """While the screencast runs and no frame has come for STALE_AFTER s, ask the page something small (bounded): when it does not
        answer, its main thread is blocked and the picture the user sees is old (`stale`, in the live status). The first frame or
        answer clears it. A still page answers, so it is never flagged."""
        while not cast.ended:
            await asyncio.sleep(WATCH_EVERY)
            if not cast.running or time.monotonic() - cast.last_frame < STALE_AFTER:
                continue
            seen = cast.last_frame
            try:
                await asyncio.wait_for(cast.cdp.send("Runtime.evaluate", {"expression": "1"}), PROBE_SECONDS)
                stale = False
            except Exception:  # noqa: BLE001 - no answer in time (or the tab is going away)
                stale = True
            if cast.last_frame != seen:
                continue   # a frame came while the page was asked: it is alive
            if stale != cast.stale:
                cast.stale = stale
                self._changed(cast.key)

    def health(self, key: str) -> dict[str, Any]:
        """`page_busy` (a user input was not taken by the page just now, or the page does not answer) and `stale` (the picture is an
        old one: the page's main thread is blocked), for the live status."""
        cast = self.casts.get(key)
        if cast is None:
            return {"page_busy": False, "stale": False}
        recent = time.monotonic() - cast.busy_at < 10.0 if cast.busy_at else False
        return {"page_busy": bool(cast.stale or recent), "stale": cast.stale}

    @staticmethod
    def _cancel_settle(cast: Cast) -> None:
        if cast.settle_task is not None:
            cast.settle_task.cancel()
            cast.settle_task = None

    async def _stop(self, cast: Cast) -> None:
        self._cancel_settle(cast)
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

    async def tab_switched(self, key: str) -> None:
        """The active tab of this key changed (take-over): a running picture moves to the new tab at once, not at the next reap."""
        cast = self.casts.get(key)
        if cast is not None:
            await self._follow(cast, self.session.raw_page(key))

    async def _seed(self, cast: Cast, started: float) -> None:
        """A still page paints nothing after the start: take one picture so the viewer sees it."""
        await asyncio.sleep(SEED_AFTER)
        if not cast.running or cast.started_at != started or cast.frames_since_start:
            return
        await self._still(cast, cast.settings["quality"], guard_frames=True)

    async def _still(self, cast: Cast, quality: int, *, guard_frames: bool = False) -> None:
        """One picture of the tab's visible area, published as a frame (`seed`). With `guard_frames` it is dropped when a screencast frame
        came meanwhile (a seed never overwrites a newer frame); otherwise it is the newest picture by definition (see _settle)."""
        try:
            m = await cast.cdp.send("Page.getLayoutMetrics", {})
            vv = m.get("cssVisualViewport") or m.get("visualViewport") or {}
            cw, ch = float(vv.get("clientWidth") or 0), float(vv.get("clientHeight") or 0)
            if cw <= 0 or ch <= 0:
                return
            scale = min(1.0, cast.settings["max_width"] / cw)
            shot = await cast.cdp.send("Page.captureScreenshot", {
                "format": "jpeg", "quality": quality,
                "clip": {"x": float(vv.get("pageX") or 0), "y": float(vv.get("pageY") or 0), "width": cw, "height": ch, "scale": scale}})
        except Exception:  # noqa: BLE001 - no picture this time; the next painted frame will do
            return
        if (guard_frames and cast.frames_since_start) or not cast.running:
            return
        scale = float(vv.get("scale") or 1) or 1.0   # page sizes are in CSS pixels; the frame size is in screen units
        meta = {"offsetTop": 0, "pageScaleFactor": scale, "deviceWidth": round(cw * scale, 2), "deviceHeight": round(ch * scale, 2),
                "scrollOffsetX": float(vv.get("pageX") or 0), "scrollOffsetY": float(vv.get("pageY") or 0), "seed": True}
        await self._publish(cast, base64.b64decode(shot.get("data") or b""), meta)

    async def _settle(self, cast: Cast) -> None:
        """The owner stopped for SETTLE_AFTER: the page is still, so its picture is taken again, sharper than the moving frames."""
        await asyncio.sleep(SETTLE_AFTER)
        await self._still(cast, SETTLE_QUALITY)

    def _settle_later(self, cast: Cast | None) -> None:
        """After an input: restart the wait for a sharp picture (one pending at a time, so three quick inputs make one picture)."""
        if cast is None or not cast.running or cast.ended:
            return
        self._cancel_settle(cast)
        t = asyncio.ensure_future(self._settle(cast))
        cast.settle_task = t
        cast.tasks.add(t)
        t.add_done_callback(cast.tasks.discard)

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
        cast.next_ack = due + 1.0 / max(1, self.rate(cast.key))
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
        cast.last_frame = time.monotonic()
        if cast.stale or cast.busy_at:
            cast.stale, cast.busy_at = False, 0.0
            self._changed(cast.key)
        self._look_for_secure(cast)
        self._look_for_editing(cast)
        async with cast.cond:
            cast.seq += 1
            cast.frame = Frame(cast.seq, data, w, h, time.time(), url, cast.title, meta, secure=cast.secure, editing=cast.editing, kind=cast.kind, field_id=cast.field_id)
            cast.recent[cast.seq] = (w, h, meta)
            for old in [q for q in cast.recent if q <= cast.seq - RECENT_FRAMES]:
                cast.recent.pop(old, None)
            cast.cond.notify_all()

    def _look_for_secure(self, cast: Cast) -> None:
        """At most one look a second, in the background: the frames carry the answer kept from the last look."""
        now = self.clock()
        if cast.secure_busy or (cast.secure_at is not None and now - cast.secure_at < SECURE_EVERY):
            return
        cast.secure_busy, cast.secure_at = True, now
        t = asyncio.ensure_future(self._refresh_secure(cast))
        cast.tasks.add(t)
        t.add_done_callback(cast.tasks.discard)

    @staticmethod
    async def _refresh_secure(cast: Cast) -> None:
        try:
            r = await asyncio.wait_for(cast.cdp.send("Runtime.evaluate", {"expression": SECURE_JS, "returnByValue": True}), PROBE_SECONDS)
            value = (r.get("result") or {}).get("value")
            if not r.get("exceptionDetails") and isinstance(value, bool):
                cast.secure = value
        except Exception:  # noqa: BLE001 - no answer (or the tab is going away): the last answer stands
            pass
        finally:
            cast.secure_busy = False

    def _look_for_editing(self, cast: Cast) -> None:
        """While a device controls the tab: is a text field focused? (the app raises its keyboard on it). At most one look per EDITING_EVERY."""
        if self.session.controlled_by(cast.key) is None:
            cast.editing, cast.field_id = False, ""
            return
        now = self.clock()
        if cast.editing_busy or (cast.editing_at is not None and now - cast.editing_at < EDITING_EVERY):
            return
        cast.editing_busy, cast.editing_at = True, now
        t = asyncio.ensure_future(self._refresh_editing(cast))
        cast.tasks.add(t)
        t.add_done_callback(cast.tasks.discard)

    @staticmethod
    async def _refresh_editing(cast: Cast) -> None:
        try:
            r = await asyncio.wait_for(cast.cdp.send("Runtime.evaluate", {"expression": EDITING_JS, "returnByValue": True}), PROBE_SECONDS)
            value = (r.get("result") or {}).get("value")
            if r.get("exceptionDetails"):
                return
            if isinstance(value, bool):   # an older answer: only whether
                look = (value, cast.kind, cast.field_id)
            elif isinstance(value, dict) and isinstance(value.get("editing"), bool):
                raw = str(value.get("field") or "")
                kind = str(value.get("kind") or "text")
                look = (value["editing"], kind if kind in EDITING_KINDS else "text",
                        hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:8] if raw else "")
            else:
                return
            if look != (cast.editing, cast.kind, cast.field_id):
                cast.editing, cast.kind, cast.field_id = look
                cast.editing_wake.set()
        except Exception:  # noqa: BLE001 - no answer: the last answer stands
            pass
        finally:
            cast.editing_busy = False

    def poke_editing(self, key: str) -> None:
        """The owner's pointer was released (or tapped) on the tab: pages focus a field on mouseup or click, some a little later, so the
        focus is looked at again 60 ms and 250 ms from now, and a change wakes the meta pump at once (the 0.5 s poll stays the fallback)."""
        cast = self.casts.get(key)
        if cast is None or cast.ended or self.session.controlled_by(key) is None:
            return

        async def look() -> None:
            for wait in (POKE_FIRST, POKE_SECOND - POKE_FIRST):
                await asyncio.sleep(wait)
                if cast.ended:
                    return
                if not cast.editing_busy:
                    cast.editing_busy, cast.editing_at = True, self.clock()
                    await self._refresh_editing(cast)

        t = asyncio.ensure_future(look())
        cast.tasks.add(t)
        t.add_done_callback(cast.tasks.discard)

    async def editing_changed(self, key: str, timeout: float) -> None:
        """Wait until the focused field changes (or `timeout`): what lets the meta pump answer a focus move at once."""
        cast = self.casts.get(key)
        if cast is None:
            await asyncio.sleep(timeout)
            return
        cast.editing_wake.clear()
        try:
            await asyncio.wait_for(cast.editing_wake.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    async def _refresh_title(cast: Cast) -> None:
        try:
            cast.title = str(await asyncio.wait_for(cast.page.title(), 2.0))[:200]
        except Exception:  # noqa: BLE001 - the old title will do
            pass

    async def _editing_now(self, cast: Cast) -> bool:
        """Is a text field focused, as of now? For a viewer whose frames are off: the frame path that normally keeps `cast.editing` fresh
        is not what it reads. Looks at most once per EDITING_EVERY (the answer kept between), and only while a device controls the tab."""
        if self.session.controlled_by(cast.key) is None:
            cast.editing, cast.field_id = False, ""
            return False
        now = self.clock()
        if not cast.editing_busy and (cast.editing_at is None or now - cast.editing_at >= EDITING_EVERY):
            cast.editing_busy, cast.editing_at = True, now
            await self._refresh_editing(cast)
        return cast.editing

    async def meta(self, key: str) -> dict[str, Any] | None:
        """The tab's address, title and whether a text field has focus, as they are now (None: no live view of it). What a viewer whose
        frames are off shows in its header and keys on: the frame headers carry them otherwise, and they are not sent then. Nothing of
        the field's value, and a secret field counts like any other (the app's own type line hides what is typed)."""
        cast = self.casts.get(key)
        if cast is None or cast.ended:
            return None
        try:
            url = str(cast.page.url or "")
        except Exception:  # noqa: BLE001
            url = ""
        await self._refresh_title(cast)
        editing = await self._editing_now(cast)
        return {"url": url, "title": cast.title, "editing": editing, "kind": cast.kind, "field": cast.field_id, "live_v": LIVE_V}

    # ------------------------------------------------------------------ the LiveSource surface (live_source.py)
    def ticket(self, key: str, device: str) -> str:
        return self.tickets.issue(key, device)

    def redeem(self, ticket: str, key: str) -> str | None:
        return self.tickets.redeem(ticket, key)

    def available(self, key: str) -> bool:
        return self.session.raw_page(key) is not None

    def end_reason(self, key: str, viewer: str, cast: Cast) -> str:
        """Why this viewer's live view is over: "tab_closed", "no_ping" (nothing heard from it for a minute), or "" (going on)."""
        if self.casts.get(key) is not cast or cast.ended:
            return "tab_closed"
        return "" if viewer in cast.viewers else "no_ping"

    def holder(self, key: str) -> str | None:
        return self.session.controlled_by(key)

    def rate(self, key: str) -> int:
        """Frames a second the tab's acknowledgements allow: HELD_FPS while a device holds the tab, else the asked fps of the live view
        (DEFAULT_FPS when nobody watches it)."""
        if self.holder(key) is not None:
            return HELD_FPS
        cast = self.casts.get(key)
        return int(cast.settings["fps"]) if cast is not None else DEFAULT_FPS

    @staticmethod
    def agent_turn(key: str, turns: Iterable[Any]) -> Any | None:
        return next((t for t in turns if t.owner == key), None)

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
        self._cancel_settle(cast)
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

    def take(self, key: str, device: str, by: str = "button") -> None:
        """The device takes the tab. `by` "handover": the app took it because Kelo asked (a hand-over hold); anything else is the owner's
        own press (a button hold). A tab held by a device with no live connection moves to this one (the kind and inputs stay)."""
        if self.session.raw_page(key) is None:
            raise LiveError(404, "no_tab", "no such tab")
        kind = HANDOVER if by == "handover" else BUTTON
        holder = self.session.controlled_by(key)
        self.seen(key, device)
        if holder is not None and holder != device:
            if self._conns.get((key, holder)):
                raise LiveError(409, "held", "another device is controlling this tab")
            self.session.move_control(key, device)   # Kelo never gets the tab in between
            self.holds.take(key, device, kind)
        elif holder is None:
            cancelled = self.session.take_control(key, device)
            self.holds.take(key, device, kind)
            if cancelled:
                self._fence_after_settle(key, device)
        else:
            self.holds.take(key, device, kind)   # the holder again (an app's take, or the owner's press)
        self._hosts[key] = self.where(key, refresh=True)
        self._save(key)
        self._ensure_reaper()
        if (key, device) in self._screens:   # the screen size was told before the tab was taken
            t = asyncio.ensure_future(self._fit_quietly(key, device))
            self._fit_tasks.add(t)
            t.add_done_callback(self._fit_tasks.discard)

    async def _fit_quietly(self, key: str, device: str) -> None:
        try:
            await self.fit(key, device)
        except Exception:  # noqa: BLE001 - the tab keeps its own size; the next `view` tries again
            pass

    def _fence_after_settle(self, key: str, device: str) -> None:
        """A running agent step was stopped by this take: the owner's taps on pictures taken before the step's last move are dropped (the
        fence is the newest frame once the clean-up is done; input() reads it)."""
        async def go() -> None:
            await self.session.settled(key)
            hold, cast = self.holds.get(key), self.casts.get(key)
            if hold is not None and hold.device == device and cast is not None:
                hold.fence = cast.frame.seq if cast.frame is not None else None

        try:
            t = asyncio.ensure_future(go())
        except RuntimeError:   # no running loop (a plain test)
            return
        self._fit_tasks.add(t)
        t.add_done_callback(self._fit_tasks.discard)

    def _save(self, key: str) -> None:
        hold = self.holds.get(key)
        if self.book is not None and hold is not None and hold.kind == BUTTON:
            self.book.save(key, hold)

    def _ended(self, key: str) -> None:
        """The hold of the tab is over: the book forgets it, and its open question goes."""
        self.holds.drop(key)
        if self.book is not None:
            self.book.forget(key)
        if self.asker is not None:
            self.asker.close(key)

    def where(self, key: str, refresh: bool = False) -> str:
        """The host of the tab's page (what the owner's question names), or the words for a tab with no address."""
        page = self.session.raw_page(key)
        if page is not None:
            try:
                host = urlsplit(str(page.url or "")).hostname or ""
            except ValueError:
                host = ""
            if host:
                self._hosts[key] = host
                return host
        return self._hosts.get(key) or "a browser tab"

    def restore(self, key: str, device: str) -> None:
        """After a restart: a button hold saved before it stays (the browser's pages are not kept over a restart, so the screen is gone:
        the question is asked at once)."""
        self.session.restore_control(key, device)
        self.holds.take(key, device, BUTTON).gone = True

    async def give_back(self, key: str, device: str, *, reason: str = "owner") -> None:
        holder = self.session.controlled_by(key)
        if holder is None:
            return
        if holder != device:
            raise LiveError(409, "held", "another device is controlling this tab")
        await self._hand_back(key, reason)

    async def release(self, key: str, reason: str = "owner") -> bool:
        """The same as giving the tab back, whichever device holds it (the owner's answer to the idle question)."""
        if self.session.controlled_by(key) is None:
            return False
        await self._hand_back(key, reason)
        return True

    async def _hand_back(self, key: str, reason: str) -> None:
        await self._commit_composing(key)
        hold = self.holds.get(key)
        moved = bool(hold is not None and hold.inputs)
        self._ended(key)
        await self.session.release_control(key, reason=reason, moved=moved)
        await self._drop_cdp(key)

    def touched(self, key: str, device: str) -> None:
        """The holder's input (also one the VNC relay passed): the idle clock starts again and an open question goes."""
        if self.session.controlled_by(key) != device:
            return
        self.holds.touched(key, device)
        if self.asker is not None:
            self.asker.close(key)

    def hold_view(self, key: str, device: str) -> dict[str, Any]:
        """`hold` ("button" | "handover" | "") and `holder_away` (the holder has no live connection, so another device may take it)."""
        holder = self.session.controlled_by(key)
        if holder is None:
            return {"hold": "", "holder_away": False}
        hold = self.holds.get(key)
        return {"hold": hold.kind if hold is not None else BUTTON, "holder_away": not self._conns.get((key, holder))}

    # ------------------------------------------------------------------ the page takes the shape of the owner's screen
    async def view(self, key: str, device: str, ev: dict[str, Any]) -> dict[str, Any]:
        """`{"type": "view", "width", "height", "density"}`: the physical size of the owner's live area and its density, sent when the
        owner starts controlling, on rotation and when the soft keyboard opens or closes. While `device` controls the tab, the tab's
        viewport follows (viewer_css_size); a `view` that comes before the control (or from a device that does not hold it) is
        remembered and applied when it takes the tab."""
        dims = (ev.get("width"), ev.get("height"), ev.get("density"))
        if viewer_css_size(*dims) is None:
            raise LiveError(422, "bad_event", "width, height and density must be positive numbers")
        self.seen(key, device)
        self._screens[(key, device)] = dims
        if self.session.raw_page(key) is None:
            raise LiveError(404, "no_tab", "no such tab")
        if self.session.controlled_by(key) != device:
            return {"ok": True, "applied": False}
        return {"ok": True, "applied": True, **(await self.fit(key, device))}

    async def fit(self, key: str, device: str) -> dict[str, Any]:
        """Set the tab's viewport to what `device` last asked (nothing asked: nothing done). The tab keeps its own viewport and
        screen: only the picture is scaled for the viewer."""
        dims = self._screens.get((key, device))
        page = self.session.raw_page(key)
        got = viewer_css_size(*dims) if dims is not None else None
        if got is None or page is None:
            return {}
        if self.vnc is not None and self.vnc.holds(key):   # the X screen is the size: an override at another pixel ratio would break it
            cw, ch = self.vnc.size_of(key)
            return {"css_w": cw, "css_h": ch}
        return {}

    async def refit(self, key: str) -> None:
        """The tab takes the shape of the holder's screen again (the X screen let go of it): `fit` for the device that holds the tab."""
        holder = self.holder(key)
        if holder is not None:
            await self.fit(key, holder)

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
        await self.session.settled(key)   # what the agent's stopped step left half done is cleaned up first
        self.touched(key, device)         # every accepted input is the owner's hands, a stale one too
        hold = self.holds.get(key)
        seq = ev.get("frame_seq")
        if hold is not None and hold.fence is not None and cast is not None and cast.frame is not None and isinstance(seq, int) \
                and not isinstance(seq, bool):
            if seq <= hold.fence and cast.frame.seq > seq:   # a picture from before the stopped step's last move: not a tap on what is there now
                return {"ok": False, "stale": True, "type": kind}
            if seq > hold.fence:
                hold.fence = None
        if cast is not None and cast.stale:   # the page's main thread is blocked: an event now would land on another screen later
            return {"ok": False, "busy": True, "type": kind, "note": BUSY_NOTE}
        try:
            result = await self._input(cast, cdp, page, key, kind, ev, out)
        except asyncio.TimeoutError:
            if cast is not None:
                cast.busy_at = time.monotonic()
                self._changed(key)
            await self.session._quietly(lambda: cdp_send(cdp, "Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []}, 1.0))
            return {"ok": False, "busy": True, "type": kind, "note": BUSY_NOTE}
        self._settle_later(cast)   # the input went through: the picture is sharpened once the owner stops
        return result

    async def _input(self, cast: Cast | None, cdp: Any, page: Any, key: str, kind: str, ev: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
        send = lambda method, params: cdp_send(cdp, method, params, INPUT_SECONDS)  # noqa: E731

        def num(name: str, default: float | None = None) -> float:
            v = ev.get(name, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise LiveError(422, "bad_event", f"{name} must be a number")
            return float(v)

        def seen_frame() -> tuple[int, int, dict[str, Any]]:
            """(width, height, metadata) of the frame the owner was looking at (`frame_seq`), else of the newest one (`mapped_with`)."""
            frame = cast.frame if cast is not None else None
            if frame is None:
                raise LiveError(409, "no_frame", "no frame yet: open the live view first")
            seq = ev.get("frame_seq")
            known = cast.recent.get(seq) if cast is not None and isinstance(seq, int) and not isinstance(seq, bool) else None
            out["mapped_with"] = "frame" if known is not None else "latest"
            return known if known is not None else (frame.w, frame.h, frame.meta)

        def where() -> tuple[float, float, dict[str, Any], int, int]:
            w, h, meta = seen_frame()
            return num("x"), num("y"), meta, int(num("frame_w", w)) or w, int(num("frame_h", h)) or h

        mouse = lambda typ, x, y, **kw: send("Input.dispatchMouseEvent", {"type": typ, "x": x, "y": y, **kw})  # noqa: E731
        if kind in ("tap", "long_press"):
            x, y, meta, fw, fh = where()
            cx, cy = map_point(x, y, fw, fh, meta)
            await mouse("mouseMoved", cx, cy)
            await asyncio.sleep(PRESS_AFTER_MOVE)
            await mouse("mousePressed", cx, cy, button="left", buttons=1, clickCount=1)
            if kind == "long_press":
                hold = num("ms", LONG_PRESS_MS)
                await asyncio.sleep(min(3000.0, max(300.0, hold)) / 1000)
            await mouse("mouseReleased", cx, cy, button="left", buttons=0, clickCount=1)
            out.update(x=cx, y=cy, page=list(page_point(cx, cy, meta)))
            self.poke_editing(key)
        elif kind == "scroll":
            w, h, meta = seen_frame()
            fw, fh = int(num("frame_w", w)) or w, int(num("frame_h", h)) or h
            x, y = num("x", fw / 2), num("y", fh / 2)
            cx, cy = map_point(x, y, fw, fh, meta)
            dx, dy = map_delta(num("dx", 0), num("dy", 0), fw, fh, meta)
            await mouse("mouseWheel", cx, cy, deltaX=dx, deltaY=dy)
            out.update(x=cx, y=cy, dx=dx, dy=dy)
        elif kind == "text":
            text = ev.get("text")
            if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
                raise LiveError(422, "bad_event", f"text must be 1..{MAX_TEXT} characters")
            await send("Input.insertText", {"text": text})
            out["chars"] = len(text)
        elif kind == "key":
            name = str(ev.get("key") or "")
            if name not in KEYS:
                raise LiveError(422, "bad_event", f"key must be one of: {', '.join(KEYS)}")
            await _key(send, name)
            out["key"] = name
        elif kind == "edit":
            steps = parse_edit(ev)
            began = time.monotonic()
            await self._edit(cast, send, steps)
            log.debug("live edit: %d steps, %.1f ms of DevTools calls", len(steps), (time.monotonic() - began) * 1000)   # never the text
            if any(st[0] == "key" and st[1] in ("Tab", "Enter") for st in steps):
                self.poke_editing(key)
            out["steps"] = len(steps)
        elif kind == "reload":
            await send("Page.reload", {})
        else:   # back / forward
            hist = await send("Page.getNavigationHistory", {})
            i = int(hist.get("currentIndex", 0)) + (-1 if kind == "back" else 1)
            entries = hist.get("entries") or []
            if 0 <= i < len(entries):
                await send("Page.navigateToHistoryEntry", {"entryId": entries[i]["id"]})
            else:
                out["ok"], out["note"] = False, "nothing to go " + ("back to" if kind == "back" else "forward to")
        return out

    @staticmethod
    async def _edit(cast: Cast | None, send: Callable[[str, dict[str, Any]], Awaitable[Any]], steps: list[tuple[Any, ...]]) -> None:
        """Run the steps of one `edit` in order. `cast.composing` follows what the page shows of a composition; a step that fails leaves
        it unknown (cleared), and the app starts its buffer again."""
        mem = cast if cast is not None else Cast(key="", page=None, cdp=None, settings={})
        try:
            for step in steps:
                op = step[0]
                if op == "compose":
                    text, cursor = step[1], step[2]
                    if COMPOSE_ON:
                        await send("Input.imeSetComposition", {"text": text, "selectionStart": cursor, "selectionEnd": cursor})
                    else:   # delete what the last composition put in the page, type the new one as plain text
                        for _ in range(len(mem.composing)):
                            await _key(send, "Backspace")
                        if text:
                            await send("Input.insertText", {"text": text})
                    mem.composing = text
                elif op == "text":
                    if not COMPOSE_ON:   # the composition was typed as text: replace it
                        for _ in range(len(mem.composing)):
                            await _key(send, "Backspace")
                    await send("Input.insertText", {"text": step[1]})
                    mem.composing = ""
                elif op == "back":
                    for _ in range(step[1]):
                        await _key(send, "Backspace")
                    mem.composing = ""
                else:   # key
                    await _key(send, step[1])
                    mem.composing = ""
        except BaseException:
            mem.composing = ""
            raise

    async def _commit_composing(self, key: str) -> None:
        """The hold ends while the owner's keyboard shows a half-typed syllable: what was seen stays in the page (best effort)."""
        cast = self.casts.get(key)
        if cast is None or not cast.composing:
            return
        text, cast.composing = cast.composing, ""
        if not COMPOSE_ON:
            return   # it was typed as plain text already
        cdp = cast.cdp
        await self.session._quietly(lambda: cdp_send(cdp, "Input.insertText", {"text": text}, INPUT_SECONDS))

    # ------------------------------------------------------------------ the agent's pointer
    def _on_pointer(self, page: Any, x: float, y: float, down: bool) -> None:
        key = next((k for k, p in list(self.session._pages.items()) if p is page), None)
        if key is None:
            return
        self.pointers[key] = {"x": round(x, 1), "y": round(y, 1), "down": bool(down)}
        for fn in list(self.pointer_listeners):
            try:
                fn(key, x, y, down)
            except Exception:  # noqa: BLE001
                pass

    def pointer(self, key: str) -> dict[str, Any] | None:
        """The last place of the agent's pointer in this tab (viewport CSS px), for the status message; None before it moved."""
        return self.pointers.get(key)

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
            hold = self.holds.get(key)
            kind = hold.kind if hold is not None else BUTTON
            if self.session.raw_page(key) is None:
                if kind == BUTTON and hold is not None:
                    hold.gone = True          # a button hold stays; the owner is asked at once (hold_asks)
                    continue
                self._ended(key)
                await self.session.release_control(key)
            elif hold is not None:
                hold.gone = False
                if kind == HANDOVER and not self._conns.get(k) and now - self._seen.get(k, now) > CONTROL_GRACE:
                    await self._commit_composing(key)
                    self._ended(key)
                    await self.session.release_control(key, reason="timeout")
                    await self._drop_cdp(key)
        return dropped

    async def close(self) -> None:
        if self._on_pointer in human.pointer_listeners:
            human.pointer_listeners.remove(self._on_pointer)
        if self._reaper is not None:
            self._reaper.cancel()
        for cast in list(self.casts.values()):
            await self._end(cast, notify=False)


class FrameGate:
    """One viewer's switch for pictures (`{"type":"frames","on":false|true}` on the live socket): while it is off the viewer shows the
    browser's X screen (tools/vnc.py) and is sent no frames. Turning it on again makes the newest frame go out at once."""

    def __init__(self) -> None:
        self.enabled = True
        self.resend = False        # on again after off: the pump sends the newest frame whether or not it is new
        self.version = 0           # counts every change
        self._woke = asyncio.Event()

    def set(self, on: bool) -> None:
        if on and not self.enabled:
            self.resend = True
        if on != self.enabled:
            self.enabled = on
            self.version += 1
            self._woke.set()

    async def changed(self, seen: int) -> None:
        """Wait until the gate is not at version `seen` any more."""
        while self.version == seen:
            self._woke.clear()
            await self._woke.wait()


async def _frame_or_switch(hub: Any, key: str, after: int, gate: FrameGate | None, seen: int) -> Frame | None:
    """`hub.wait_frame`, cut short (None) when `gate` is switched meanwhile."""
    if gate is None:
        return await hub.wait_frame(key, after, 1.0)
    waiting = asyncio.ensure_future(hub.wait_frame(key, after, 1.0))
    switched = asyncio.ensure_future(gate.changed(seen))
    try:
        await asyncio.wait({waiting, switched}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        switched.cancel()
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, switched, return_exceptions=True)
    return waiting.result() if waiting.done() and not waiting.cancelled() else None


async def pump_frames(hub: Any, key: str, send: Callable[[Frame], Awaitable[None]], *, fps: Callable[[], int],
                      idle: Callable[[], Awaitable[None]] | None = None, after: int = 0, gate: FrameGate | None = None) -> None:
    """Send the newest frame of a tab to one viewer, at most `fps()` a second, until the live view ends (LiveEnded). Only the newest
    frame is ever sent: while `send` is slow, frames that come and go meanwhile are skipped, never queued. `idle` runs about once
    a second when there is nothing new. While `gate` is given and off nothing is sent; when it is on again the newest frame goes
    out at once, even when it is the one sent before."""
    last_sent = 0.0
    while True:
        seen = gate.version if gate is not None else 0
        if gate is not None and not gate.enabled:
            await gate.changed(seen)
            continue
        if gate is not None and gate.resend:
            gate.resend = False
            frame = hub.latest(key)
            if frame is not None:
                await send(frame)
                last_sent = time.monotonic()
                after = frame.seq
            continue
        frame = await _frame_or_switch(hub, key, after, gate, seen)
        if frame is None:
            if idle is not None and (gate is None or gate.version == seen):
                await idle()
            continue
        if gate is not None and not gate.enabled:   # switched off while this frame was awaited: it is not sent
            continue
        gap = 1.0 / max(1, fps()) - (time.monotonic() - last_sent)
        if gap > 0:
            await asyncio.sleep(gap)
            frame = hub.latest(key) or frame   # whatever is newest after the pause
        await send(frame)
        last_sent = time.monotonic()
        after = frame.seq


META_EVERY = 0.5   # seconds between two looks at the tab's address, title and focused field while a viewer's frames are off


async def pump_meta(hub: Any, key: str, send: Callable[[dict[str, Any]], Awaitable[None]], gate: FrameGate, *, every: float = META_EVERY) -> None:
    """While `gate` (the viewer's frame switch) is off, tell it the tab's address and title, and whether a text field has focus
    ({"type":"meta","url","title","editing","kind","field","live_v"}), whenever any of them changes: the picture headers that normally carry them are not sent
    then, so its header would show the page it had when the X screen began and its keyboard would never come up. The first look after
    the switch goes off is sent at once. Nothing is sent while the gate is on (the frames carry it)."""
    last: dict[str, Any] | None = None
    while True:
        seen = gate.version
        if gate.enabled:
            last = None
            await gate.changed(seen)
            continue
        meta = await hub.meta(key)
        if meta is not None and meta != last:
            last = meta
            await send({"type": "meta", **meta})
        try:
            if hasattr(hub, "editing_changed"):   # a focus move wakes it at once; the gate's own changes too
                woke = [asyncio.ensure_future(gate.changed(seen)), asyncio.ensure_future(hub.editing_changed(key, every))]
                try:
                    await asyncio.wait(woke, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for w in woke:
                        w.cancel()
            else:
                await asyncio.wait_for(gate.changed(seen), every)
        except asyncio.TimeoutError:
            pass
