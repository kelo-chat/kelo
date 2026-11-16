"""The Android device inside Kelo's own browser view (docs/android-device.md, "Seeing and using the screen through Kelo's browser view").

When the owner must look at the device (a human check, an app's first screens, a step Kelo cannot read), a tab of Kelo's browser shows
it and the apps' existing "take control" view lets the owner tap on it. The browser never talks to the bridge: this server is the
middle. The tab is written over DevTools (Page.setDocumentContent: inline HTML, CSS and script, no network, no outside resource);
the server pushes each new screenshot into it (Runtime.evaluate, the picture as a data URL), at most FRAME_EVERY apart and only when
the picture changed (hash of the PNG), and every DRAIN_EVERY seconds takes what the owner did in the page (a tap in image pixels,
typed text, a key, Done) from the page's queue and sends it to the bridge. Typed text goes straight to the bridge and is never
logged. What the owner does here is their own hand: no approval card (the same as taking control of the browser)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import struct
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..agent import handover
from ..i18n import t
from ..tools.browser import browser_owner
from .device import DeviceClient, DeviceError

log = logging.getLogger(__name__)

FRAME_EVERY = 0.5       # at most 2 pictures a second
DRAIN_EVERY = 0.3       # how often what the owner did in the page is taken
LIMIT_SECONDS = 600.0   # the mirror ends after this long (the owner presses Done sooner)
MAX_ERRORS = 3          # calls to the bridge that fail one after the other before the mirror stops
MAX_TEXT = 2000
KEYS = ("back", "home", "enter", "recent", "delete", "tab")

# The page. Placeholders {{...}} are the owner's words (escaped); the script is idempotent (it may be run by the page and again over DevTools).
SCRIPT = """(() => {
  if (window.__keloMirror) return;
  window.__keloMirror = true;
  const q = window.__keloQueue = [];
  const img = document.getElementById('screen');
  window.__keloFrame = (url) => { img.src = url; };
  img.addEventListener('click', (e) => {
    const r = img.getBoundingClientRect();
    if (!img.naturalWidth || !r.width || !r.height) return;
    q.push({type: 'tap', x: Math.round((e.clientX - r.left) * img.naturalWidth / r.width),
            y: Math.round((e.clientY - r.top) * img.naturalHeight / r.height)});
  });
  for (const b of document.querySelectorAll('[data-key]')) b.addEventListener('click', () => q.push({type: 'key', key: b.dataset.key}));
  const box = document.getElementById('text');
  const send = () => { if (box.value) { q.push({type: 'text', value: box.value}); box.value = ''; } };
  document.getElementById('send').addEventListener('click', send);
  box.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
  document.getElementById('done').addEventListener('click', () => q.push({type: 'done'}));
})();"""

PAGE = """<!doctype html>
<html lang="{{lang}}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<style>
html, body { margin: 0; height: 100%; background: #111; color: #eee; font: 16px/1.3 system-ui, sans-serif; }
body { display: flex; flex-direction: column; }
#bar { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px; background: #1e1e1e; }
button { font: inherit; padding: 8px 14px; border: 0; border-radius: 8px; background: #3a3a3a; color: #fff; }
button#done { background: #2563eb; margin-left: auto; }
input { flex: 1 1 120px; min-width: 0; font: inherit; padding: 8px 10px; border: 1px solid #444; border-radius: 8px; background: #000; color: #fff; }
#view { flex: 1; min-height: 0; overflow: auto; display: flex; justify-content: center; align-items: flex-start; }
#screen { width: 100%; height: auto; max-width: 720px; display: block; cursor: pointer; background: #000; }
</style></head><body>
<div id="bar">
<button data-key="back">{{back}}</button><button data-key="home">{{home}}</button><button data-key="enter">{{enter}}</button>
<input id="text" type="text" placeholder="{{type_here}}" autocomplete="off"><button id="send">{{send}}</button>
<button id="done">{{done}}</button>
</div>
<div id="view"><img id="screen" alt=""></div>
<script>{{script}}</script>
</body></html>"""


def page_html(lang: str = "en") -> str:
    """The mirror page in the owner's language (i18n.t); every word is escaped, the script is ours."""
    words = {"title": t("Phone screen"), "back": t("Back"), "home": t("Home screen"), "enter": t("Enter"), "type_here": t("Type here"),
             "send": t("Send"), "done": t("I'm done"), "lang": lang}
    out = PAGE
    for key, value in words.items():
        out = out.replace("{{" + key + "}}", html.escape(str(value), quote=True))
    return out.replace("{{script}}", SCRIPT)


def png_size(b64: str) -> tuple[int, int]:
    """(width, height) of a base64 PNG from its header, (0, 0) when it is not one."""
    try:
        head = base64.b64decode(b64[:48] + "=" * (-len(b64[:48]) % 4))
    except ValueError:
        return 0, 0
    if not head.startswith(b"\x89PNG") or len(head) < 24:
        return 0, 0
    w, h = struct.unpack(">II", head[16:24])
    return int(w), int(h)


def to_device(x: float, y: float, image: tuple[int, int], device: tuple[int, int]) -> tuple[int, int]:
    """A point in the picture's pixels -> the device's pixels (the same when the picture is the device's own size)."""
    (iw, ih), (dw, dh) = image, device
    if not (iw and ih and dw and dh):
        return round(x), round(y)
    return round(x * dw / iw), round(y * dh / ih)


class AndroidMirror:
    """One mirror at a time per call of `show`. `session` is the BrowserSession (page(), cdp_session(page), raw_page(key), close_tab(key))."""

    def __init__(self, client: DeviceClient, session: Any, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, frame_every: float = FRAME_EVERY,
                 drain_every: float = DRAIN_EVERY, limit: float = LIMIT_SECONDS):
        self.client, self.session = client, session
        self._clock, self._sleep = clock, sleep
        self.frame_every, self.drain_every, self.limit = frame_every, drain_every, limit
        self.pushed = 0                 # pictures put into the page
        self.end = ""                   # how the last mirror ended: done | timeout | closed | error
        self.detail = ""                # for "error": what the bridge said (never what was typed)
        self._ended = asyncio.Event()

    # ------------------------------------------------------------------ the page
    @staticmethod
    def tab_key() -> str:
        """The mirror's own tab of this owner: the agent's own browser page stays as it is."""
        return f"{browser_owner.get()}:phone"

    async def _evaluate(self, cdp: Any, expression: str) -> Any:
        got = await cdp.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if isinstance(got, dict) and got.get("exceptionDetails"):
            raise RuntimeError("the page did not take it")
        return ((got or {}).get("result") or {}).get("value")

    async def _write(self, cdp: Any, lang: str) -> None:
        tree = await cdp.send("Page.getFrameTree")
        frame = ((tree or {}).get("frameTree") or {}).get("frame") or {}
        await cdp.send("Page.setDocumentContent", {"frameId": frame.get("id", ""), "html": page_html(lang)})
        await self._evaluate(cdp, SCRIPT)   # a page filled in over DevTools may not run its own scripts: the script is idempotent

    # ------------------------------------------------------------------ the loop
    async def _pump(self, cdp: Any, key: str) -> None:
        started = self._clock()
        last_frame, last_hash = -1e9, ""
        errors = 0
        device: tuple[int, int] = (0, 0)
        image: tuple[int, int] = (0, 0)
        try:
            size = (await self.client.status()).get("size") or [0, 0]
            device = (int(size[0]), int(size[1]))
        except (DeviceError, TypeError, ValueError, IndexError):
            pass   # the picture's own size then stands for the device's
        while True:
            now = self._clock()
            if now - started >= self.limit:
                return self._finish("timeout")
            if self.session.raw_page(key) is None:
                return self._finish("closed")
            try:
                for event in await self._drain(cdp):
                    if event.get("type") == "done":
                        return self._finish("done")
                    await self._apply(event, image, device)
                    errors = 0
                if now - last_frame >= self.frame_every:
                    last_frame = now
                    png = str((await self.client.frame()).get("png") or "")
                    if not png:
                        raise DeviceError("the device sent no picture")
                    errors = 0   # only a call that worked clears the count: a bridge that fails every picture ends it
                    digest = hashlib.sha1(png.encode()).hexdigest()
                    if digest != last_hash:
                        last_hash, image = digest, png_size(png) or image
                        await self._evaluate(cdp, f"window.__keloFrame({json.dumps('data:image/png;base64,' + png)})")
                        self.pushed += 1
            except DeviceError as e:
                errors += 1
                self.detail = str(e)[:200]
                if errors >= MAX_ERRORS:
                    return self._finish("error")
            except Exception as e:  # noqa: BLE001 - a page that went away or a DevTools failure: the mirror is over
                log.info("the phone mirror stopped: %s", type(e).__name__)
                return self._finish("closed")
            await self._sleep(self.drain_every)

    def _finish(self, how: str) -> None:
        self.end = how
        self._ended.set()

    async def _drain(self, cdp: Any) -> list[dict[str, Any]]:
        """What the owner did in the page since the last time (the page's queue, emptied)."""
        raw = await self._evaluate(cdp, "JSON.stringify((window.__keloQueue || []).splice(0))")
        try:
            events = json.loads(raw or "[]")
        except ValueError:
            return []
        return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []

    async def _apply(self, event: dict[str, Any], image: tuple[int, int], device: tuple[int, int]) -> None:
        kind = event.get("type")
        if kind == "tap":
            x, y = event.get("x"), event.get("y")
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
                await self.client.tap(*to_device(x, y, image, device))
        elif kind == "text":
            value = event.get("value")
            if isinstance(value, str) and value:
                try:
                    await self.client.text(value[:MAX_TEXT])   # never logged, never in a result
                except DeviceError as e:
                    raise DeviceError(str(e).replace(value, "[hidden]")) from None
        elif kind == "key" and event.get("key") in KEYS:
            await self.client.key(str(event["key"]))

    # ------------------------------------------------------------------ the whole thing
    async def show(self, reason: str, *, lang: str = "en") -> str:
        """Open the mirror tab, hand it to the owner (a card in the chat and a notification, like a human check, when the app set
        `handover` for this turn) and wait until they press Done, the mirror runs out (LIMIT_SECONDS) or the bridge fails. Closes the
        tab. Returns how it ended: done | timeout | error | closed | skipped | expired | cancelled."""
        self.end, self.detail, self.pushed = "", "", 0
        self._ended = asyncio.Event()
        key = self.tab_key()
        token = browser_owner.set(key)
        try:
            page = await self.session.page()
        finally:
            browser_owner.reset(token)
        cdp = await self.session.cdp_session(page)
        pump: asyncio.Future[Any] | None = None
        outcome = "closed"
        try:
            await self._write(cdp, lang)
            pump = asyncio.ensure_future(self._pump(cdp, key))
            hand = handover.get()
            if hand is None:   # tests, the CLI: nobody to ask; the mirror ends by itself
                await self._ended.wait()
            else:
                async def gone() -> bool:
                    await self._ended.wait()
                    return self.end == "done"

                outcome = str(await hand({"tab": key, "site": "phone", "vendor": "android", "name": reason[:200], "kind": "device"}, gone))
            if self._ended.is_set():
                outcome = self.end
            elif outcome in ("solved", "progressed"):
                outcome = "done"
            return outcome
        finally:
            if pump is not None:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
            detach = getattr(cdp, "detach", None)
            if detach is not None:
                try:
                    await detach()
                except Exception:  # noqa: BLE001 - the tab is closed next anyway
                    pass
            try:
                await self.session.close_tab(key)
            except Exception:  # noqa: BLE001
                log.info("the phone mirror's tab could not be closed")
