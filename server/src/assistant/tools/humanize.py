"""Human-paced input for the agent's browser: how the pointer moves, how keys are typed, how a page is scrolled and how long a page
is looked at before the next step. Every click, typing step and page landing in tools/browser.py goes through here.

The agent works in the owner's own browser with the owner's own accounts, so it should handle pages the way a person at that
browser would rather than teleporting the pointer and filling fields in one instant:

- Pointer: each page keeps the last pointer position. A move to a target follows a cubic Bezier curve with randomised control
  points, eased in and out, with a little jitter; its step count and duration grow with the distance and shrink with the target size
  (Fitts' law); long moves sometimes overshoot slightly and come back. A click lands on a random point inside the element, biased
  toward the centre and kept off the edges, and the button is held a short random time. An element out of view is first scrolled
  to with the wheel.
- Keyboard: one key at a time with log-normal gaps, longer after spaces and punctuation and an occasional short pause. A rare typo
  fixed with Backspace exists for ordinary text but is off by default and never used for secrets (passwords, card numbers, CVC,
  one-time codes): those are typed exactly. Nothing typed is ever logged.
  Text of an input-method script (Korean; tools/ime.py) is typed the way Chrome with that input method produces it, per key:
  `Input.dispatchKeyEvent` keyDown "Process" (keyCode 229, the physical key's code), `Input.imeSetComposition` with the syllable as far as
  it is typed (ㅂ, 바, 박), keyUp, a human gap; after the last key of a syllable `Input.insertText` commits it. A page so sees keydown,
  compositionstart/update/end and input events as from a real keyboard, not one paste-like insert per syllable. If the composition call
  is refused (a frame, a page that refuses it), the same keys are sent with only the commit per syllable. Never for secrets. Needs the page's
  DevTools session (`cdp`, a coroutine function (method, params) -> result that bounds each call); without it the text is typed as before.
- Scrolling: wheel steps of uneven size with uneven pauses, sometimes a small step back.
- Dwell: after a page loads, a short random look (with a small pointer drift) before it is read or acted on.

What does not change: selectors, timeouts and error messages are those of the plain Playwright calls, which stay as the fallback
for anything that cannot be done this way (no bounding box: hidden, zero-size, a closed shadow root; a field that is not an input;
a driver that has no mouse). Nothing here gets around a human check: the agent's own try at one (challenge.py) uses the
same clicks and keys as any other step.

Settings: `ASSISTANT_HUMAN_INPUT=0` turns all of it off (plain instant calls, as before); `ASSISTANT_HUMAN_INPUT_SEED=<int>` makes
the randomness repeatable. Tests call `configure(enabled=False)` (server/tests/conftest.py) or `configure(enabled=True, seed=..., speed=0)`.
All timings are in `Timing` below; `speed` multiplies every wait (0 = no waiting at all).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import string
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from . import ime

log = logging.getLogger(__name__)

Point = tuple[float, float]


@dataclass(frozen=True)
class Timing:
    """Every number that shapes the input, in one place. Seconds unless the name says px or a share."""
    # pointer path
    move_min_steps: int = 8               # fewest pointer events in one move
    move_max_steps: int = 45              # most pointer events in one move
    move_px_per_step: float = 22.0        # one more event for this many pixels of distance
    curve_min: float = 0.06               # control points sit this share of the distance off the straight line, at least...
    curve_max: float = 0.28               # ...and at most
    curve_min_px: float = 4.0             # and never closer than this to it (short moves still bend)
    jitter_px: float = 1.2                # hand tremor: per-point noise (standard deviation)...
    jitter_max_px: float = 3.0            # ...never more than this
    overshoot_chance: float = 0.18        # how often a long move goes slightly past the target and comes back
    overshoot_min_distance_px: float = 140.0
    overshoot_px: tuple[float, float] = (4.0, 14.0)
    # pointer speed (Fitts: a + b * log2(1 + distance / width))
    fitts_a: float = 0.10
    fitts_b: float = 0.11
    move_min_s: float = 0.12
    move_max_s: float = 0.95
    # clicking
    edge_margin: float = 0.18             # share of the width/height kept clear on each side of the element
    hover_s: tuple[float, float] = (0.05, 0.22)    # rest on the target before pressing
    press_s: tuple[float, float] = (0.045, 0.14)   # button held down
    # keyboard
    key_median_s: float = 0.085           # typical gap between two keys (log-normal median)...
    key_sigma: float = 0.38               # ...its spread
    key_min_s: float = 0.028
    key_max_s: float = 0.40
    after_space_s: tuple[float, float] = (0.03, 0.16)
    after_punct_s: tuple[float, float] = (0.08, 0.30)
    think_chance: float = 0.025           # a short pause to think, per key
    think_s: tuple[float, float] = (0.30, 0.85)
    typo_chance: float = 0.0              # a wrong neighbouring key then Backspace, per letter of ordinary text (off; never for secrets)
    type_budget_s: float = 6.0            # the waits of one field add up to at most this (long text is typed faster, not slower)
    submit_pause_s: tuple[float, float] = (0.25, 0.70)  # before pressing Enter
    # scrolling
    wheel_px: tuple[float, float] = (70.0, 190.0)       # one wheel step
    wheel_pause_s: tuple[float, float] = (0.03, 0.16)
    scroll_back_chance: float = 0.12      # a small step back up (or down) on the way
    scroll_back_px: tuple[float, float] = (25.0, 80.0)
    scroll_max_steps: int = 40            # longer scrolls fall back to the browser's own scroll-into-view
    view_band: tuple[float, float] = (0.08, 0.90)       # an element whose centre is inside this band of the viewport height is "in view"
    scroll_settle_s: float = 0.15
    # looking at a page
    dwell_s: tuple[float, float] = (0.8, 2.2)           # after a page loads
    glance_s: tuple[float, float] = (0.25, 0.7)         # after a click that stayed on the page
    drift_px: tuple[float, float] = (25.0, 140.0)       # the small pointer movement while looking
    # waiting for the target (the plain call keeps its full timeout as the fallback)
    prep_timeout_ms: int = 2_000
    press_timeout_ms: int = 2_500


TIMING = Timing()


class _State:
    enabled: bool | None = None           # None: read ASSISTANT_HUMAN_INPUT
    speed: float = 1.0
    timing: Timing = TIMING
    rng: random.Random = random.Random()


_state = _State()


def _env_seed() -> int | None:
    raw = os.environ.get("ASSISTANT_HUMAN_INPUT_SEED", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


_state.rng = random.Random(_env_seed())


def configure(*, enabled: bool | None = None, seed: int | None = None, speed: float | None = None, timing: Timing | None = None,
              reset: bool = False) -> None:
    """Change the settings at run time (tests). `reset=True` goes back to the environment's settings first."""
    if reset:
        _state.enabled, _state.speed, _state.timing, _state.rng = None, 1.0, TIMING, random.Random(_env_seed())
        _pointers.clear()
    if enabled is not None:
        _state.enabled = enabled
    if seed is not None:
        _state.rng = random.Random(seed)
    if speed is not None:
        _state.speed = max(0.0, float(speed))
    if timing is not None:
        _state.timing = timing


def enabled() -> bool:
    if _state.enabled is not None:
        return _state.enabled
    return os.environ.get("ASSISTANT_HUMAN_INPUT", "1").strip().lower() not in ("0", "false", "no", "off", "")


def pace_factor() -> float:
    """How much of the per-site waits (tools/pacing.py) to keep: the `speed` setting; none when human input is off."""
    return _state.speed if enabled() else 0.0


# ---------------------------------------------------------------------------------------------------------------- pure generators

def _u(rng: random.Random, pair: tuple[float, float]) -> float:
    return rng.uniform(pair[0], pair[1])


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _ease(u: float) -> float:
    """Slow start, fast middle, slow end."""
    return 0.5 - 0.5 * math.cos(math.pi * u)


def _bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    m = 1 - t
    a, b, c, d = m * m * m, 3 * m * m * t, 3 * m * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def _curve(start: Point, end: Point, rng: random.Random, tm: Timing, steps: int) -> list[Point]:
    """`steps` points from just after `start` to exactly `end` on a randomised cubic Bezier, eased, with bounded jitter."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return [end]
    nx, ny = -dy / dist, dx / dist                          # unit normal of the straight line
    off1 = max(tm.curve_min_px, rng.uniform(tm.curve_min, tm.curve_max) * dist)
    off2 = max(tm.curve_min_px, rng.uniform(tm.curve_min, tm.curve_max) * dist)
    side = rng.choice((-1.0, 1.0))
    side2 = side if rng.random() < 0.75 else -side          # mostly a bow, sometimes a gentle S
    t1, t2 = rng.uniform(0.2, 0.4), rng.uniform(0.6, 0.8)
    c1 = (start[0] + dx * t1 + nx * off1 * side, start[1] + dy * t1 + ny * off1 * side)
    c2 = (start[0] + dx * t2 + nx * off2 * side2, start[1] + dy * t2 + ny * off2 * side2)
    pts: list[Point] = []
    for i in range(1, steps + 1):
        x, y = _bezier(start, c1, c2, end, _ease(i / steps))
        if i < steps:
            x += _clamp(rng.gauss(0, tm.jitter_px), -tm.jitter_max_px, tm.jitter_max_px)
            y += _clamp(rng.gauss(0, tm.jitter_px), -tm.jitter_max_px, tm.jitter_max_px)
        pts.append((x, y))
    return pts


def step_count(distance: float, tm: Timing = TIMING) -> int:
    return int(_clamp(tm.move_min_steps + distance / tm.move_px_per_step, tm.move_min_steps, tm.move_max_steps))


def move_seconds(distance: float, width: float, tm: Timing = TIMING) -> float:
    """How long a move takes (Fitts' law): longer for far and small targets."""
    return _clamp(tm.fitts_a + tm.fitts_b * math.log2(1 + distance / max(width, 1.0)), tm.move_min_s, tm.move_max_s)


def pointer_path(start: Point, end: Point, rng: random.Random, tm: Timing = TIMING,
                 bounds: tuple[float, float] | None = None) -> list[Point]:
    """The pointer positions of one move, ending exactly at `end`. Long moves sometimes go a little past and correct.
    `bounds` (viewport width, height) keeps every point on the page."""
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if dist < 1.0:
        return [end]
    if dist >= tm.overshoot_min_distance_px and rng.random() < tm.overshoot_chance:
        over = _u(rng, tm.overshoot_px)
        ux, uy = (end[0] - start[0]) / dist, (end[1] - start[1]) / dist
        past = (end[0] + ux * over + rng.uniform(-2, 2), end[1] + uy * over + rng.uniform(-2, 2))
        if bounds:
            past = (_clamp(past[0], 0, bounds[0] - 1), _clamp(past[1], 0, bounds[1] - 1))
        pts = _curve(start, past, rng, tm, step_count(dist, tm)) + _curve(past, end, rng, tm, max(3, tm.move_min_steps // 2))
    else:
        pts = _curve(start, end, rng, tm, step_count(dist, tm))
    if bounds:
        pts = [(_clamp(x, 0, bounds[0] - 1), _clamp(y, 0, bounds[1] - 1)) for x, y in pts[:-1]] + [pts[-1]]
    return pts


def click_point(box: dict[str, float], rng: random.Random, tm: Timing = TIMING) -> Point:
    """A point inside the box, near the centre more often than not, never within `edge_margin` of a side."""
    x, y, w, h = float(box["x"]), float(box["y"]), float(box["width"]), float(box["height"])
    cx, cy = x + w / 2, y + h / 2
    hx = w * (0.5 - tm.edge_margin) if w >= 4 else 0.0     # a box of a few pixels is clicked in its middle
    hy = h * (0.5 - tm.edge_margin) if h >= 4 else 0.0
    hx, hy = max(0.0, hx), max(0.0, hy)
    px = cx + _clamp(rng.gauss(0, hx / 2.2), -hx, hx) if hx > 0 else cx
    py = cy + _clamp(rng.gauss(0, hy / 2.2), -hy, hy) if hy > 0 else cy
    return (px, py)


_PUNCT = set(".,;:!?)]}…。、，！？")
_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")


def _neighbour(ch: str, rng: random.Random) -> str | None:
    low = ch.lower()
    for r, row in enumerate(_ROWS):
        i = row.find(low)
        if i < 0:
            continue
        near = [row[j] for j in (i - 1, i + 1) if 0 <= j < len(row)]
        for rr in (r - 1, r + 1):
            if 0 <= rr < len(_ROWS) and i < len(_ROWS[rr]):
                near.append(_ROWS[rr][i])
        out = rng.choice(near)
        return out.upper() if ch.isupper() else out
    return None


def key_gaps(text: str, rng: random.Random, tm: Timing = TIMING) -> list[float]:
    """The wait after each character: log-normal, longer after spaces and punctuation, sometimes a pause to think; all of them
    together at most `type_budget_s` (scaled down evenly when the text is long)."""
    mu = math.log(tm.key_median_s)
    gaps = []
    for ch in text:
        g = _clamp(rng.lognormvariate(mu, tm.key_sigma), tm.key_min_s, tm.key_max_s)
        if ch == " ":
            g += _u(rng, tm.after_space_s)
        elif ch in _PUNCT:
            g += _u(rng, tm.after_punct_s)
        if rng.random() < tm.think_chance:
            g += _u(rng, tm.think_s)
        gaps.append(g)
    total = sum(gaps)
    if total > tm.type_budget_s > 0:
        k = tm.type_budget_s / total
        gaps = [g * k for g in gaps]
    return gaps


def typing_plan(text: str, rng: random.Random, tm: Timing = TIMING, *, secret: bool = False) -> list[tuple[str, str, float]]:
    """What to press: ("type", char, wait) and, for ordinary text when typo_chance > 0, ("type", wrong, wait) + ("key", "Backspace",
    wait) before the right char. Secrets are always typed exactly, character by character."""
    plan: list[tuple[str, str, float]] = []
    for ch, gap in zip(text, key_gaps(text, rng, tm)):
        if not secret and tm.typo_chance > 0 and ch.isascii() and ch.isalpha() and rng.random() < tm.typo_chance:
            wrong = _neighbour(ch, rng)
            if wrong:
                plan.append(("type", wrong, _clamp(rng.lognormvariate(math.log(tm.key_median_s), tm.key_sigma), tm.key_min_s, tm.key_max_s)))
                plan.append(("key", "Backspace", _u(rng, tm.after_punct_s)))
        plan.append(("type", ch, gap))
    return plan


def wheel_steps(dy: float, rng: random.Random, tm: Timing = TIMING) -> list[tuple[float, float]]:
    """(wheel delta, pause after) steps that add up to `dy`: uneven sizes and pauses, sometimes a small step back."""
    if abs(dy) < 1:
        return []
    sign = 1.0 if dy > 0 else -1.0
    left = abs(dy)
    steps: list[tuple[float, float]] = []
    while left > 0.5 and len(steps) < tm.scroll_max_steps:
        chunk = min(left, _u(rng, tm.wheel_px))
        steps.append((sign * chunk, _u(rng, tm.wheel_pause_s)))
        left -= chunk
        if left > tm.wheel_px[1] and rng.random() < tm.scroll_back_chance:
            back = _u(rng, tm.scroll_back_px)
            steps.append((-sign * back, _u(rng, tm.wheel_pause_s) * 1.5))
            left += back
    if left > 0.5:
        steps.append((sign * left, _u(rng, tm.wheel_pause_s)))
    return [(round(d, 1), p) for d, p in steps]


def has_control_chars(text: str) -> bool:
    return any(ch in "\n\r\t" or (ord(ch) < 32) for ch in text)


def _norm(v: str) -> str:
    return "".join(ch for ch in v.casefold() if ch.isalnum())


# ---------------------------------------------------------------------------------------------------------------- driving a page

_pointers: Any = weakref.WeakKeyDictionary()
_pointers_by_id: dict[int, Point] = {}


def _get_pointer(page: Any) -> Point | None:
    try:
        return _pointers.get(page)
    except TypeError:
        return _pointers_by_id.get(id(page))


# Whoever draws the pointer (the live view, live.py) listens here: fn(page, x, y, down), x and y in CSS px of the page's viewport (the
# space of Mouse.move and of the live frames' css_w x css_h). Called on every pointer step, so a listener throttles; never raises out.
pointer_listeners: list[Callable[[Any, float, float, bool], None]] = []
_held: set[int] = set()     # id(page) of pages whose button (or finger) is down now


def _emit(page: Any, p: Point, down: bool) -> None:
    for fn in list(pointer_listeners):
        try:
            fn(page, float(p[0]), float(p[1]), down)
        except Exception:  # noqa: BLE001 - drawing the pointer must never stop the page work
            pass


def _set_pointer(page: Any, p: Point) -> None:
    try:
        _pointers[page] = p
    except TypeError:
        _pointers_by_id[id(page)] = p
    if pointer_listeners:
        _emit(page, p, id(page) in _held)


def set_pressed(page: Any, down: bool, at: Point | None = None) -> None:
    """The button went down or up at `at` (default: where the pointer is)."""
    if page is None:
        return
    (_held.add if down else _held.discard)(id(page))
    p = at or _get_pointer(page)
    if at is not None:
        _set_pointer(page, at)
    elif p is not None and pointer_listeners:
        _emit(page, p, down)


def last_pointer(page: Any) -> Point | None:
    """Where the pointer of this page was left (None: not moved yet)."""
    return _get_pointer(_mouse_page(page))


def _mouse_page(owner: Any) -> Any:
    """The page whose mouse moves for `owner` (a page, or a frame of one)."""
    if owner is None:
        return None
    if getattr(owner, "mouse", None) is not None:
        return owner
    try:
        page = getattr(owner, "page", None)
    except Exception:  # noqa: BLE001
        return None
    return page if getattr(page, "mouse", None) is not None else None


async def _sleep(seconds: float) -> None:
    s = seconds * _state.speed
    if s > 0:
        await asyncio.sleep(s)


async def _viewport(page: Any) -> tuple[float, float]:
    try:
        vs = page.viewport_size
        if vs and vs.get("width") and vs.get("height"):
            return float(vs["width"]), float(vs["height"])
    except Exception:  # noqa: BLE001
        pass
    try:
        w, h = await page.evaluate("() => [window.innerWidth, window.innerHeight]")
        if w and h:
            return float(w), float(h)
    except Exception:  # noqa: BLE001
        pass
    return 1280.0, 800.0


async def _start_point(page: Any, vw: float, vh: float) -> Point:
    p = _get_pointer(page)
    if p is None:
        rng = _state.rng
        p = (rng.uniform(0.3, 0.7) * vw, rng.uniform(0.25, 0.65) * vh)
        await page.mouse.move(p[0], p[1])
        _set_pointer(page, p)
    return p


async def move_to(page: Any, x: float, y: float, *, width: float = 40.0) -> None:
    """Move the pointer of `page` from where it was to (x, y) along a human path."""
    tm, rng = _state.timing, _state.rng
    vw, vh = await _viewport(page)
    start = await _start_point(page, vw, vh)
    pts = pointer_path(start, (x, y), rng, tm, bounds=(vw, vh))
    dist = math.hypot(x - start[0], y - start[1])
    per = move_seconds(dist, width, tm) / max(1, len(pts))
    for i, (px, py) in enumerate(pts):
        await page.mouse.move(px, py)
        _set_pointer(page, (px, py))
        u = (i + 0.5) / len(pts)
        await _sleep(per * (0.6 + 0.8 * (1 - math.sin(math.pi * u))))   # slower at both ends, faster mid-way
    _set_pointer(page, (x, y))


async def scroll_by(page: Any, dy: float) -> None:
    """Scroll with the mouse wheel in uneven steps."""
    mp = _mouse_page(page)
    if mp is None or not enabled():
        return
    vw, vh = await _viewport(mp)
    await _start_point(mp, vw, vh)
    for delta, pause in wheel_steps(dy, _state.rng, _state.timing):
        await mp.mouse.wheel(0, delta)
        await _sleep(pause)
    await _sleep(_state.timing.scroll_settle_s)


async def _box(loc: Any, timeout_ms: int) -> dict[str, float] | None:
    box = await loc.bounding_box(timeout=timeout_ms)
    if not box or box.get("width", 0) < 1 or box.get("height", 0) < 1:
        return None
    return box


async def _box_in_view(mp: Any, loc: Any, timeout_ms: int) -> dict[str, float] | None:
    """The element's box once it is in view: scrolled to with the wheel when it is above or below; the browser's own
    scroll-into-view when the wheel does not get there (an inner scroll area, a page that does not scroll)."""
    tm = _state.timing
    box = await _box(loc, timeout_ms)
    if box is None:
        return None
    vw, vh = await _viewport(mp)
    for _ in range(3):
        cy = box["y"] + box["height"] / 2
        in_x = box["x"] >= 0 and box["x"] + box["width"] <= vw
        if in_x and vh * tm.view_band[0] <= cy <= vh * tm.view_band[1]:
            return box
        if not in_x:
            break
        dy = cy - vh * _state.rng.uniform(0.3, 0.55)
        if abs(dy) > tm.wheel_px[1] * tm.scroll_max_steps:
            break
        await scroll_by(mp, dy)
        new = await _box(loc, tm.prep_timeout_ms)
        if new is None:
            return None
        moved = abs(new["y"] - box["y"]) > 2
        box = new
        if not moved:
            break
    cy = box["y"] + box["height"] / 2
    if 0 <= cy <= vh and box["x"] >= 0 and box["x"] + box["width"] <= vw:
        return box
    await loc.scroll_into_view_if_needed(timeout=tm.prep_timeout_ms)
    await _sleep(tm.scroll_settle_s)
    return await _box(loc, tm.prep_timeout_ms)


def _is_timeout(e: BaseException) -> bool:
    return type(e).__name__ == "TimeoutError" or isinstance(e, asyncio.TimeoutError)


async def _press_on(mp: Any, loc: Any) -> bool:
    """Bring the element into view, move there and click a human point of it. False: it could not be done this way (nothing was
    pressed), so the caller uses the plain call. Errors other than waiting too long are the driver's and propagate."""
    tm, rng = _state.timing, _state.rng
    try:
        box = await _box_in_view(mp, loc, tm.prep_timeout_ms)
        if box is None:
            return False
        x, y = click_point(box, rng, tm)
        await move_to(mp, x, y, width=min(box["width"], box["height"]))
        await _sleep(_u(rng, tm.hover_s))
    except Exception:  # noqa: BLE001 - not pressed yet: the plain call takes over
        return False
    set_pressed(mp, True)
    try:
        await loc.click(position={"x": x - box["x"], "y": y - box["y"]}, delay=_u(rng, tm.press_s) * 1000 * _state.speed,
                        timeout=tm.press_timeout_ms)
    except Exception as e:  # noqa: BLE001
        if _is_timeout(e):        # covered, disabled or moving: nothing was pressed; the plain call waits its full time
            return False
        raise
    finally:
        set_pressed(mp, False)
    return True


def _locator(owner: Any, selector: str | None, locator: Any) -> Any:
    if locator is not None:
        return locator
    return owner.locator(selector).first


async def click(owner: Any, selector: str | None = None, *, locator: Any = None, timeout_ms: int,
                fallback: Callable[[int], Awaitable[Any]]) -> None:
    """Click like a person; `fallback(timeout_ms)` is the plain click, used when that cannot be done."""
    mp = _mouse_page(owner)
    if not enabled() or mp is None:
        await fallback(timeout_ms)
        return
    try:
        loc = _locator(owner, selector, locator)
    except Exception:  # noqa: BLE001
        await fallback(timeout_ms)
        return
    if not await _press_on(mp, loc):
        await fallback(timeout_ms)


ImeSend = Callable[[str, dict[str, Any]], Awaitable[Any]]


def ime_plan(text: str, rng: random.Random, tm: Timing = TIMING) -> list[tuple[str, Any, Any]]:
    """Like typing_plan (no typos) for text that has characters of an input-method script: ("type", char, wait) for ordinary
    characters and ("ime", char, [wait after each key]) for one character typed as its keystrokes (tools/ime.py). The waits of all keys
    together stay within `type_budget_s`."""
    units = [(ch, (sc.keys(ch) if (sc := ime.script_for(ch)) else [])) for ch in text]
    gaps = key_gaps("".join("x" * len(keys) if keys else ch for ch, keys in units), rng, tm)
    plan: list[tuple[str, Any, Any]] = []
    i = 0
    for ch, keys in units:
        if keys:
            plan.append(("ime", ch, gaps[i:i + len(keys)]))
            i += len(keys)
        else:
            plan.append(("type", ch, gaps[i]))
            i += 1
    return plan


async def _ime_char(send: ImeSend, ch: str, keys: list[ime.ImeKey], waits: list[float], composing: bool) -> bool:
    """One character as an input method gets it: per key a keyDown "Process" (229), the composition so far, keyUp; then the commit.
    Returns whether composition calls still work (False after one was refused: the rest is sent as keys and commits only)."""
    for n, (k, wait) in enumerate(zip(keys, waits)):
        mods = 8 if k.shift else 0
        if k.shift:
            await send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Shift", "code": "ShiftLeft", "windowsVirtualKeyCode": 16, "modifiers": 8})
        await send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Process", "code": k.code, "windowsVirtualKeyCode": 229, "modifiers": mods})
        if composing:
            try:
                await send("Input.imeSetComposition", {"text": k.composed, "selectionStart": len(k.composed), "selectionEnd": len(k.composed)})
            except Exception as e:  # noqa: BLE001 - not supported here (a frame): keys and commits only
                if _is_timeout(e):
                    raise
                composing = False
                log.debug("type_text: composition refused (%s): committing per character", type(e).__name__)
        await send("Input.dispatchKeyEvent", {"type": "keyUp", "key": k.jamo, "code": k.code, "windowsVirtualKeyCode": k.vk, "modifiers": mods})
        if k.shift:
            await send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Shift", "code": "ShiftLeft", "windowsVirtualKeyCode": 16, "modifiers": 0})
        await _sleep(wait)
    await send("Input.insertText", {"text": ch})
    return composing


async def type_text(owner: Any, text: str, *, selector: str | None = None, locator: Any = None, timeout_ms: int,
                    fallback: Callable[[int], Awaitable[Any]], secret: bool = False, cdp: ImeSend | None = None) -> None:
    """Click into a field and type `text` key by key, replacing what it held; `fallback(timeout_ms)` is the plain fill, used when
    the field is not an ordinary input, the typing fails, or the field ends up holding something else than what was typed.
    `secret`: never a typo; the text is never put in a message or log. `cdp`: send(method, params) on the page's DevTools session
    (each call bounded): with it, Korean is typed through the input-method path (module docstring). The path taken is logged at
    debug level (never the text)."""
    mp = _mouse_page(owner)
    if not enabled() or mp is None or not text or has_control_chars(text):
        log.debug("type_text: plain fill (%s)", "off" if not enabled() else "no page mouse" if mp is None else "empty or control characters")
        await fallback(timeout_ms)
        return
    tm, rng = _state.timing, _state.rng
    path = "typed"
    try:
        loc = _locator(owner, selector, locator)
        if not await _press_on(mp, loc):
            log.debug("type_text: fallback: no box (or covered) to press")
            await fallback(timeout_ms)
            return
        current = await loc.input_value(timeout=tm.prep_timeout_ms)
        if current:
            await loc.fill("", timeout=timeout_ms)
        by_ime = cdp is not None and not secret and ime.uses_ime(text)
        if by_ime:
            path = "typed by input method"
            composing = True
            for kind, ch, wait in ime_plan(text, rng, tm):
                if kind == "ime":
                    composing = await _ime_char(cdp, ch, ime.script_for(ch).keys(ch), wait, composing)
                else:
                    await loc.press_sequentially(ch, timeout=timeout_ms)
                    await _sleep(wait)
            if not composing:
                path = "typed by input method (commits only)"
        else:
            for kind, key, wait in typing_plan(text, rng, tm, secret=secret):
                if kind == "type":
                    await loc.press_sequentially(key, timeout=timeout_ms)
                else:
                    await loc.press(key, timeout=timeout_ms)
                await _sleep(wait)
        ok = _norm(await loc.input_value(timeout=tm.prep_timeout_ms)) == _norm(text)
        if not ok:
            path = "fallback: value differs"
    except Exception as e:  # noqa: BLE001 - its message could quote the text: the plain fill decides (and says what failed)
        ok = False
        path = f"fallback: {type(e).__name__}"
    log.debug("type_text: %s", path)
    if not ok:
        await fallback(timeout_ms)


async def hover(owner: Any, locator: Any) -> None:
    """Move the pointer onto an element without clicking (before choosing in a list box). Never fails."""
    mp = _mouse_page(owner)
    if not enabled() or mp is None:
        return
    try:
        box = await _box_in_view(mp, locator, _state.timing.prep_timeout_ms)
        if box is not None:
            x, y = click_point(box, _state.rng, _state.timing)
            await move_to(mp, x, y, width=min(box["width"], box["height"]))
            await _sleep(_u(_state.rng, _state.timing.hover_s))
    except Exception:  # noqa: BLE001
        pass


async def click_at(owner: Any, x: float, y: float) -> None:
    """Click the point (x, y) of the page (CSS px) like a person: a human path there, a short hover, a held press. The plain mouse click
    when human input is off."""
    mp = _mouse_page(owner)
    if mp is None:
        raise RuntimeError("this page has no mouse")
    if not enabled():
        await mp.mouse.click(x, y)
        return
    tm, rng = _state.timing, _state.rng
    await move_to(mp, x, y, width=24.0)
    await _sleep(_u(rng, tm.hover_s))
    await mp.mouse.down()
    set_pressed(mp, True)
    await _sleep(_u(rng, tm.press_s))
    await mp.mouse.up()
    set_pressed(mp, False)


async def drag(owner: Any, points: list[Point]) -> None:
    """Press at the first point (CSS px), move through each next one along a human path, let go at the last. With human input off:
    straight mouse moves in a few steps."""
    mp = _mouse_page(owner)
    if mp is None:
        raise RuntimeError("this page has no mouse")
    if len(points) < 2:
        raise ValueError("a drag needs at least two points")
    if not enabled():
        await mp.mouse.move(*points[0])
        await mp.mouse.down()
        for p in points[1:]:
            await mp.mouse.move(p[0], p[1], steps=8)
        await mp.mouse.up()
        return
    tm, rng = _state.timing, _state.rng
    await move_to(mp, *points[0], width=24.0)
    await _sleep(_u(rng, tm.hover_s))
    await mp.mouse.down()
    set_pressed(mp, True)
    vw, vh = await _viewport(mp)
    here = points[0]
    for p in points[1:]:
        pts = pointer_path(here, p, rng, tm, bounds=(vw, vh))
        per = move_seconds(math.hypot(p[0] - here[0], p[1] - here[1]), 24.0, tm) / max(1, len(pts))
        for px, py in pts:
            await mp.mouse.move(px, py)
            await _sleep(per)
        await mp.mouse.move(p[0], p[1])
        _set_pointer(mp, p)
        here = p
    await _sleep(_u(rng, tm.press_s))
    await mp.mouse.up()
    set_pressed(mp, False)


async def type_keys(owner: Any, text: str) -> None:
    """Type `text` into whatever has the focus, key by key with human gaps (the plain keyboard when human input is off)."""
    mp = _mouse_page(owner)
    keyboard = getattr(mp, "keyboard", None) if mp is not None else getattr(owner, "keyboard", None)
    if keyboard is None:
        raise RuntimeError("this page has no keyboard")
    if not enabled():
        await keyboard.type(text)
        return
    tm, rng = _state.timing, _state.rng
    for kind, key, wait in typing_plan(text, rng, tm, secret=True):
        if kind == "type":
            await keyboard.type(key)
        else:
            await keyboard.press(key)
        await _sleep(wait)


async def pause_before_submit() -> None:
    if enabled():
        await _sleep(_u(_state.rng, _state.timing.submit_pause_s))


async def _look(owner: Any, span: tuple[float, float]) -> None:
    mp = _mouse_page(owner)
    if not enabled() or mp is None:
        return
    tm, rng = _state.timing, _state.rng
    total = _u(rng, span)
    started = time.monotonic()
    try:
        vw, vh = await _viewport(mp)
        x, y = await _start_point(mp, vw, vh)
        for _ in range(rng.randint(1, 2)):
            ang, r = rng.uniform(0, 2 * math.pi), _u(rng, tm.drift_px)
            nx, ny = _clamp(x + r * math.cos(ang), 5, vw - 5), _clamp(y + r * math.sin(ang), 5, vh - 5)
            await move_to(mp, nx, ny, width=200)
            x, y = nx, ny
            await _sleep(rng.uniform(0.05, 0.25))
    except Exception:  # noqa: BLE001 - looking never breaks a step
        pass
    spent = (time.monotonic() - started) / _state.speed if _state.speed else total
    await _sleep(max(0.0, total - spent))


async def dwell(owner: Any) -> None:
    """After a page loaded: look at it for a short random while (with a small pointer drift) before reading or acting."""
    await _look(owner, _state.timing.dwell_s)


async def glance(owner: Any) -> None:
    """After a click that stayed on the same page: a shorter look at what changed."""
    await _look(owner, _state.timing.glance_s)


async def bring_into_view(owner: Any, locator: Any, timeout_ms: int) -> dict[str, float] | None:
    """The element scrolled into view the way a person does it (the wheel, see _box_in_view), before anything reads whether it is
    covered. None when the page has no mouse to scroll with."""
    mp = _mouse_page(owner)
    if mp is None or not enabled():
        return None
    return await _box_in_view(mp, locator, timeout_ms)
