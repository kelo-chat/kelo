"""Entering a saved payment PIN on a checkout page: a PIN box (one input, or one input per digit), or a keypad whose buttons carry
their digit as text. `find` reports which of these the page (or one of its frames) has; a keypad of another kind (picture or canvas
keys, keys without digit text, a page asking for a keyboard plug-in) is reported as "protected" and handled by the payment-step card
(tools/payment_step.PinPad). Nothing here logs, returns or raises a digit of the PIN.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .page_paths import NO_ELEMENT, with_path

PRESS_PAUSE = 0.12      # seconds between two presses (pages animate the press)

class KeypadError(Exception):
    """Why the PIN was not typed. `kind`: none | protected | gone | failed. The message names no digit."""

    def __init__(self, kind: str, message: str, pressed: int = 0):
        super().__init__(message)
        self.kind, self.pressed = kind, pressed


# Finds where the PIN goes in this frame, by STRUCTURE only (docs/no-word-lists.md): no label, placeholder or page word is read.
# Returns {"mode": "input"|"text"|"protected"|"", "boxes": n, "paths": [...], "keys": {digit: path}}: CSS paths of the elements (page_paths).
# - "input": paths[0..n-1] of the PIN box(es): one password input with a PIN's shape (maxlength 4-8, or numeric input mode),
#   or one password / numeric box per digit (4-8 one-character boxes). Fields that say what they are by autocomplete (a user name, a
#   card field, a one-time code, an address, the current password of a sign-in) are never taken. More than one candidate group, or a
#   single field with no PIN shape: not typed ("" = the owner enters it themselves);
# - "text": keys["0".."9"] on the buttons whose own visible words are exactly one digit, all ten close together;
# - "protected": a keypad whose keys carry no digit text (pictures, empty buttons in a grid of at least three rows), a canvas keypad
#   next to a password box, a plug-in or embedded object that draws the keypad. The user enters the PIN there (payment_step.PinPad).
FIND_JS = with_path(r"""() => {
  const shown = (e) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width >= 8 && r.height >= 8 && c.visibility !== 'hidden' && c.display !== 'none' && parseFloat(c.opacity || '1') > 0.05; };
  const taken = /(^|\s)(username|email|tel[a-z-]*|cc-[a-z-]+|one-time-code|current-password|name|given-name|family-name|street-address|address-[a-z0-9-]+|postal-code|country[a-z-]*|bday[a-z-]*|organization[a-z-]*)(\s|$)/;
  const kind = (e) => (e.getAttribute('type') || '').toLowerCase();
  const numeric = (e) => /numeric|decimal|tel/.test((e.getAttribute('inputmode') || '').toLowerCase());
  const inputs = [...document.querySelectorAll('input')].filter((e) => shown(e) && !e.disabled && !e.readOnly
    && ['password', 'tel', 'number', 'text', ''].includes(kind(e)) && !taken.test((e.getAttribute('autocomplete') || '').toLowerCase()));
  const singles = inputs.filter((e) => e.maxLength === 1 && kind(e) === 'password');   // one masked box per digit (an open row of boxes is a one-time code, not a PIN)
  const shaped = inputs.filter((e) => e.maxLength !== 1 && kind(e) === 'password' && ((e.maxLength >= 4 && e.maxLength <= 8) || numeric(e)));
  if (singles.length >= 4 && singles.length <= 8 && shaped.length === 0) {
    return { mode: 'input', boxes: singles.length, paths: singles.map((e) => keloPath(e)) };
  }
  if (shaped.length === 1 && singles.length < 4) return { mode: 'input', boxes: 1, paths: [keloPath(shaped[0])] };
  const clickable = (e) => e.matches('button,[role=button],a,input[type=button],input[type=image],[onclick],[tabindex]') || getComputedStyle(e).cursor === 'pointer';
  const keys = [...document.querySelectorAll('button,[role=button],a,input[type=button],input[type=image],td,li,span,div,img')]
    .filter((e) => shown(e) && clickable(e));
  const own = (e) => (e.tagName === 'INPUT' ? (e.value || '') : (e.innerText || '')).trim();
  const byDigit = {};
  for (const e of keys) {
    const w = own(e);
    if (!/^\d$/.test(w)) continue;
    const r = e.getBoundingClientRect();
    if (!byDigit[w] || r.width * r.height < byDigit[w].getBoundingClientRect().width * byDigit[w].getBoundingClientRect().height) byDigit[w] = e;
  }
  if (Object.keys(byDigit).length === 10) {
    const rs = Object.values(byDigit).map((e) => e.getBoundingClientRect());
    const w = Math.max(...rs.map((r) => r.right)) - Math.min(...rs.map((r) => r.left));
    const h = Math.max(...rs.map((r) => r.bottom)) - Math.min(...rs.map((r) => r.top));
    if (w <= 900 && h <= 900) {
      const keyPaths = {};
      for (const [d, e] of Object.entries(byDigit)) keyPaths[d] = keloPath(e);
      return { mode: 'text', boxes: 0, keys: keyPaths };
    }
  }
  // a keypad whose digits are not words: 10+ like-sized clickable keys in 3+ rows without digit words, a canvas next to a password box,
  // or a plug-in / embedded object that draws it
  const blank = keys.filter((e) => !/\d/.test(own(e)) && !keys.some((o) => o !== e && e.contains(o)));
  const sizes = {};
  for (const e of blank) { const r = e.getBoundingClientRect(); const k = Math.round(r.width / 8) + 'x' + Math.round(r.height / 8); (sizes[k] = sizes[k] || []).push(e); }
  const grid = Object.values(sizes).some((g) => g.length >= 10 && new Set(g.map((e) => Math.round(e.getBoundingClientRect().top / 6))).size >= 3);
  const secretBox = [...document.querySelectorAll('input')].some((e) => shown(e) && kind(e) === 'password');
  const canvas = secretBox && [...document.querySelectorAll('canvas')].some((e) => shown(e));
  const plugin = [...document.querySelectorAll('object,embed')].some((e) => shown(e));
  if (grid || canvas || plugin) return { mode: 'protected', boxes: 0 };
  return { mode: '', boxes: 0 };
}""")


@dataclass
class TypedPin:
    mode: str           # input | text
    frame: str          # the frame's host when the PIN went into a frame, else ""
    pressed: int


async def find(page: Any) -> tuple[Any, dict[str, Any]]:
    """The frame where the PIN goes (the page first, then its frames) and what FIND_JS said there. A protected keypad anywhere wins:
    then nothing is typed at all."""
    found: tuple[Any, dict[str, Any]] | None = None
    first: dict[str, Any] = {}
    for frame in [page.main_frame, *[f for f in page.frames if f is not page.main_frame]]:
        try:
            got = await frame.evaluate(FIND_JS)
        except Exception:  # noqa: BLE001 - a frame that cannot be reached or went away
            continue
        if not isinstance(got, dict):
            continue
        if not first:
            first = got
        if got.get("mode") == "protected":
            return frame, {**got, "mode": "protected"}
        if got.get("mode") and found is None:
            found = (frame, got)
    return found if found is not None else (None, first)


async def type_pin(page: Any, digits: list[str], *, click: Any = None, typer: Any = None) -> TypedPin:
    """Enter `digits` in the PIN box or on the text keypad of `page` (or one of its frames). `click(locator)` / `typer(locator, ch)`:
    how a key is clicked / a character typed (default: plain Playwright calls). Raises KeypadError; never returns or raises a digit."""
    pressed = 0
    mode, where = "", ""
    try:
        frame, got = await find(page)
        if frame is None or not got.get("mode"):
            raise KeypadError("none", "no PIN box or keypad with plain digit buttons was found on this page")
        if got["mode"] == "protected":
            raise KeypadError("protected", "the page's PIN keypad has no digit text (picture, canvas or plug-in keypad)")
        mode = str(got["mode"])
        if frame is not page.main_frame:
            from urllib.parse import urlsplit
            where = urlsplit(frame.url).hostname or ""
        if mode == "input":
            boxes = int(got.get("boxes") or 1)
            if boxes > 1 and boxes != len(digits):
                raise KeypadError("none", "the page's PIN boxes do not match the saved PIN's length")
            for i, ch in enumerate(digits):
                box = frame.locator(got["paths"][i if boxes > 1 else 0]).first
                try:
                    if typer is not None:
                        await typer(box, ch)
                    else:
                        await box.press_sequentially(ch, timeout=5000)
                except Exception as e:  # noqa: BLE001 - a driver message could quote what was typed
                    raise KeypadError("failed", f"the PIN box did not take the input ({type(e).__name__})", pressed) from None
                pressed += 1
            return TypedPin(mode, where, pressed)
        for i, d in enumerate(digits):
            if i:
                frame, got = await find(page)
                if frame is None or got.get("mode") != "text":
                    raise KeypadError("protected" if got.get("mode") == "protected" else "gone",
                                      "the keypad changed or went away before the PIN was finished", pressed)
            target = frame.locator((got.get("keys") or {}).get(d) or NO_ELEMENT).first
            try:
                if click is not None:
                    await click(target)
                else:
                    await target.click(timeout=5000)
            except Exception as e:  # noqa: BLE001 - a driver message could name the key: say only what kind of failure it was
                raise KeypadError("failed", f"a key of the keypad could not be pressed ({type(e).__name__})", pressed) from None
            pressed += 1
            await asyncio.sleep(PRESS_PAUSE)
        return TypedPin(mode, where, pressed)
    except KeypadError as e:
        e.pressed = pressed
        raise
