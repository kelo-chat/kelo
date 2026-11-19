"""Pictures Kelo puts in the chat: a screenshot of its browser tab the owner asked to see ("스크린샷 찍어서 보내 줘").

The agent's `send_screenshot` tool takes the current tab (or one element of it, `selector`), covers what is typed into secure fields
(passwords, card numbers, security codes, one-time codes) with solid boxes before the picture is kept, stores it as a JPEG in the
server's own folder (never the owner's Files: the agent does not write uploads) and shows it in the chat at once (an `image` turn
event). When the reply is stored, the picture goes with that message, so the chat shows it again after a reload or on another device.

CHAT IMAGES API:
GET /api/chat-images/{id}     the JPEG (auth; no-store)
Turn event: {"type": "image", "image_id", "url": "/api/chat-images/{id}", "width", "height", "caption"}
GET /api/messages rows: "images": [{"id", "url", "width", "height", "caption"}] on the agent message a picture went with.
Pictures go with the chat history: clearing the chat removes them.
"""

from __future__ import annotations

import io
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from .agent import Tool, ToolOutput, emit_ui
from .netguard import NetGuardError, fetch as netguard_fetch
from .photos import view_bytes
from .tools.image_tools import process

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_images (
    id TEXT PRIMARY KEY, turn_id TEXT NOT NULL DEFAULT '', message_id INTEGER, width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0, caption TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS chat_images_message ON chat_images(message_id);
"""
MAX_WIDTH = 1280
MAX_BYTES = 900_000
PER_TURN = 6
MAX_CARD_PICTURES = 6              # card pictures of one question
MAX_URL_BYTES = 10 * 1024 * 1024   # a picture from a web address (show_picture url=)
CARD_SIDE = 480                    # a choice card's thumbnail: the longest side, JPEG 80
CARD_DEADLINE = 10.0               # seconds for all the pictures of one question together
CARD_SUFFIX = "#cards"             # turn_id of a turn's card thumbnails: they go with the message but are not its pictures
PICTURE_SIDE = 1600                # the picture in the chat: the longest side, JPEG 85 (as the library pictures always were)
NO_VISION = ("[The model you are running on cannot look at pictures: the picture is shown to the user but you did NOT see it. "
             "Do not describe it.]")

# The page's secure fields that are on screen, as rectangles in CSS pixels, and the viewport width (to scale them to the picture).
# By structure only (hc-final-a): the type, the autocomplete tokens, a masked rendering (-webkit-text-security), and every visible
# input or select of a card form (the form of a cc-* field, else the nearest box holding two or more fields). No field name or id is read.
SECURE_RECTS_JS = """() => {
  const sel = 'input[type=password], input[autocomplete~="cc-number"], input[autocomplete~="cc-csc"], input[autocomplete~="cc-exp"],'
    + ' input[autocomplete~="one-time-code"], input[autocomplete~="current-password"], input[autocomplete~="new-password"]';
  const picked = new Set(document.querySelectorAll(sel));
  const inputs = [...document.querySelectorAll('input')];
  for (const e of inputs) {
    const masked = (getComputedStyle(e).getPropertyValue('-webkit-text-security') || 'none').trim();
    if (masked !== 'none') picked.add(e);
  }
  for (const e of inputs) {
    if (!/(^|\\s)cc-/.test((e.getAttribute('autocomplete') || '').toLowerCase())) continue;
    let box = e.form || e.parentElement;
    while (box && box !== document.body && !e.form && box.querySelectorAll('input, select').length < 2) box = box.parentElement;
    if (!box) continue;
    for (const f of box.querySelectorAll('input:not([type=hidden]), select')) picked.add(f);
  }
  const out = [];
  for (const e of picked) {
    const r = e.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) out.push([r.left, r.top, r.width, r.height]);
  }
  return {rects: out, vw: window.innerWidth || document.documentElement.clientWidth || 0};
}"""


def cover(raw: bytes, rects: list[list[float]], viewport_width: float, crop: tuple[float, float, float, float] | None = None,
          max_width: int = MAX_WIDTH, cap: int = MAX_BYTES) -> tuple[bytes | None, int, int]:
    """The screenshot with every secure field covered by a solid box (rects in CSS pixels; the picture may be wider: device pixels),
    then cropped to `crop` (CSS pixels) when given, scaled to `max_width` and kept under `cap` bytes as JPEG. (None, 0, 0) if it never fits."""
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    scale = img.width / viewport_width if viewport_width else 1.0
    draw = ImageDraw.Draw(img)
    for x, y, w, h in rects:
        pad = 2
        draw.rectangle([max(0, (x - pad) * scale), max(0, (y - pad) * scale), (x + w + pad) * scale, (y + h + pad) * scale], fill=(40, 40, 40))
    if crop is not None:
        x, y, w, h = crop
        box = (max(0, int(x * scale)), max(0, int(y * scale)), min(img.width, int((x + w) * scale)), min(img.height, int((y + h) * scale)))
        if box[2] > box[0] and box[3] > box[1]:
            img = img.crop(box)
    if img.width > max_width:
        img = img.resize((max_width, max(1, round(img.height * max_width / img.width))))
    for quality in (80, 65, 50, 35):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() <= cap:
            return buf.getvalue(), img.width, img.height
    return None, 0, 0


GrabFn = Callable[[str], Awaitable[tuple[bytes | None, int, int, str]]]   # selector -> (jpeg, w, h, problem)


def browser_grab(session: Any) -> GrabFn:
    """Takes the running reply's own browser tab (tools/browser.py), its secure fields covered."""
    async def grab(selector: str) -> tuple[bytes | None, int, int, str]:
        from .tools.browser import browser_owner
        page = getattr(session, "_pages", {}).get(browser_owner.get())
        if page is None or page.is_closed() or not str(page.url or "").startswith(("http://", "https://")):
            return None, 0, 0, "no page is open in your browser tab: open the page first"
        try:
            info = await page.evaluate(SECURE_RECTS_JS)
            crop = None
            if selector:
                el = await page.query_selector(selector)
                if el is None:
                    return None, 0, 0, f"nothing on the page matches {selector!r}"
                await el.scroll_into_view_if_needed(timeout=3_000)
                info = await page.evaluate(SECURE_RECTS_JS)   # positions after the scroll
                box = await el.bounding_box()
                if box:
                    crop = (box["x"], box["y"], box["width"], box["height"])
            shoot = getattr(session, "shoot", None)   # a picture of the viewport (BrowserSession.shoot)
            raw = await (shoot(page, type="png", timeout=8_000) if shoot else page.screenshot(type="png", timeout=8_000))
        except Exception as e:  # noqa: BLE001 - the words go back to the agent
            return None, 0, 0, f"the screenshot failed: {str(e)[:160]}"
        import asyncio
        jpeg, w, h = await asyncio.to_thread(cover, raw, list(info.get("rects") or []), float(info.get("vw") or 0), crop)
        if jpeg is None:
            return None, 0, 0, "the picture was too large to send"
        return jpeg, w, h, ""
    return grab


def picture_jpeg(data: bytes) -> tuple[bytes, int, int]:
    """The picture as a JPEG (its EXIF turn applied, at most PICTURE_SIDE px) and its size. Raises when the bytes are no image."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue(), im.width, im.height


def card_thumbnail(data: bytes) -> tuple[bytes, int, int]:
    """A choice card's picture: a JPEG (EXIF turn applied) whose longest side is at most CARD_SIDE px. Raises when the bytes are no image."""
    from PIL import Image, ImageOps
    process(data, 0)   # JPG, PNG or WebP only, decoded
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((CARD_SIDE, CARD_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue(), im.width, im.height


def saved_bytes(name: str, library: Any, images: Any) -> bytes | None:
    """A library file (files/) called `name`, else a saved product or generated photo of the ImageStore; None when there is neither."""
    try:
        path = library.path(name)
    except Exception:  # noqa: BLE001 - not in the library: the saved photos may have it
        path = None
    if path is None and images is not None:
        path = images.path(name)
    if path is None:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


async def fetch_picture(url: str, fetch: Any) -> bytes | str:
    """The bytes at a public https picture address, or the words for the agent why not (only the picture's own host, no redirects)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        return "Error: only https:// picture addresses can be shown."
    host = (parts.hostname or "").lower()
    try:
        reply = await fetch("GET", url, allowed_hosts=[host], headers={"Accept": "image/png,image/jpeg,image/webp", "User-Agent": "Mozilla/5.0"},
                            timeout=15.0, max_body=MAX_URL_BYTES)
    except NetGuardError as e:
        return f"Error: {e}"
    if reply.status != 200:
        return f"Error: the picture server answered HTTP {reply.status}."
    if reply.truncated:
        return "Error: the picture is larger than 10 MB."
    return reply.body


class ChatImages:
    def __init__(self, store: Any, root: Path | None, *, clock: Callable[[], float] = time.time):
        self.store, self.clock = store, clock
        self.root = root
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def path(self, iid: str) -> Path | None:
        if self.root is None or not iid.replace("_", "").isalnum():
            return None
        p = self.root / f"{iid}.jpg"
        return p if p.is_file() else None

    def add(self, turn_id: str, jpeg: bytes, width: int, height: int, caption: str = "") -> dict[str, Any]:
        if self.root is None:
            raise RuntimeError("no folder for pictures")
        iid = "img_" + uuid.uuid4().hex[:16]
        (self.root / f"{iid}.jpg").write_bytes(jpeg)
        self.store._run("INSERT INTO chat_images(id, turn_id, width, height, caption, created_at) VALUES(?,?,?,?,?,?)",
                        (iid, turn_id, width, height, caption[:200], self.clock()))
        return self.public({"id": iid, "width": width, "height": height, "caption": caption[:200]})

    @staticmethod
    def public(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["id"], "url": f"/api/chat-images/{r['id']}", "width": int(r["width"]), "height": int(r["height"]),
                "caption": r.get("caption") or ""}

    def count_for(self, turn_id: str) -> int:
        return int(self.store._all("SELECT COUNT(*) AS n FROM chat_images WHERE turn_id = ?", (turn_id,))[0]["n"])

    def attach(self, turn_id: str, message_id: int) -> None:
        """The reply `turn_id` was stored as `message_id`: its pictures show with that message from now on."""
        self.store._run("UPDATE chat_images SET message_id = ? WHERE turn_id IN (?, ?) AND message_id IS NULL",
                        (message_id, turn_id, turn_id + CARD_SUFFIX))

    async def card_pictures(self, turn_id: str, urls: list[str], fetcher: Any = None) -> list[dict[str, Any] | None]:
        """Thumbnails for the pictures of one question's choice cards: each address is fetched like show_picture's (https, public host
        only, 10 MB), all at once under CARD_DEADLINE seconds in total, and stored as chat images of this turn that do not count toward
        PER_TURN and are not shown as the message's pictures (only the card carries them). One entry per address: the stored
        picture (public(): id, url, width, height) or None when it could not be had."""
        import asyncio

        async def one(url: str) -> dict[str, Any] | None:
            raw = await fetch_picture(url, fetcher or netguard_fetch)
            if isinstance(raw, str):
                return None
            try:
                jpeg, w, h = await asyncio.to_thread(card_thumbnail, raw)
            except Exception:  # noqa: BLE001 - a page or a script at the address is not a picture
                return None
            return self.add(turn_id + CARD_SUFFIX, jpeg, w, h)

        tasks = [asyncio.ensure_future(one(u)) for u in urls[:MAX_CARD_PICTURES]]
        done, pending = await asyncio.wait(tasks, timeout=CARD_DEADLINE) if tasks else (set(), set())
        for t in pending:
            t.cancel()
        out: list[dict[str, Any] | None] = []
        for t in tasks:
            try:
                out.append(t.result() if t in done and not t.cancelled() else None)
            except Exception:  # noqa: BLE001 - one bad picture never fails the question
                out.append(None)
        return out + [None] * (len(urls) - len(out))

    def for_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [m["id"] for m in rows if m.get("role") == "assistant"]
        if not ids:
            return rows
        marks = ",".join("?" * len(ids))
        found: dict[int, list[dict[str, Any]]] = {}
        for r in self.store._all(f"SELECT * FROM chat_images WHERE message_id IN ({marks}) AND turn_id NOT LIKE '%{CARD_SUFFIX}' "
                                 "ORDER BY created_at", tuple(ids)):
            found.setdefault(int(r["message_id"]), []).append(self.public(r))
        return [{**m, "images": found[m["id"]]} if m["id"] in found else m for m in rows]

    def clear(self) -> None:
        for r in self.store._all("SELECT id FROM chat_images"):
            p = self.path(r["id"])
            if p is not None:
                p.unlink(missing_ok=True)
        self.store._run("DELETE FROM chat_images")

    def library_tool(self, library: Any, current_turn: Callable[[], dict[str, Any] | None], *, images: Any = None,
                     fetcher: Any = None) -> Tool:
        """show_picture: a picture in the chat from the user's library (files/), a photo saved with save_product_image or generate_image
        (`images`, the ImageStore), or a public https picture address. A web picture is fetched for this reply only and kept nowhere.
        The result's own words are this app's, so the tool is not untrusted; the picture itself reaches the model only as an image."""
        async def show_picture(a: dict[str, Any]) -> ToolOutput | str:
            turn = current_turn() or {}
            tid = str(turn.get("turn_id") or "")
            if not tid:
                return "Error: a picture can only go into a chat reply."
            name, url = str(a.get("name") or "").strip(), str(a.get("url") or "").strip()
            if bool(name) == bool(url):
                return "Error: give name or url (exactly one of them)."
            if self.count_for(tid) >= PER_TURN:
                return f"Error: at most {PER_TURN} pictures in one reply."
            if url:
                raw = await fetch_picture(url, fetcher or netguard_fetch)
                if isinstance(raw, str):
                    return raw
                try:
                    process(raw, 0)   # JPG, PNG or WebP only, decoded
                    jpeg, w, h = picture_jpeg(raw)
                except Exception:  # noqa: BLE001 - a page or a script at the address is not a picture
                    return "Error: that address does not hold a JPG, PNG or WebP picture."
            else:
                raw = saved_bytes(name, library, images)
                if raw is None:
                    saved = images.names()[:20] if images is not None else []
                    return f"Error: no picture named {name} in the library or the saved photos. Saved photos: {saved}"
                try:
                    jpeg, w, h = picture_jpeg(raw)
                except Exception as e:  # noqa: BLE001 - a file that is not a picture
                    return f"Error: that is not a picture ({type(e).__name__})."
            shown = self.add(tid, jpeg, w, h, str(a.get("caption") or ""))
            await emit_ui({"type": "image", "image_id": shown["id"], **{k: shown[k] for k in ("url", "width", "height", "caption")}})
            view, *_ = view_bytes(jpeg)
            return ToolOutput(text="The picture is in the chat now, under your reply. Say in a few words what it shows.",
                              images=[view], no_vision=NO_VISION)
        return Tool("show_picture",
                    "Show the user a picture in the chat: a file in their library, a photo saved with save_product_image / generate_image "
                    "(name), or a picture on the web (url: its https image address, e.g. hotel rooms, places, products).",
                    {"type": "object", "properties": {
                        "name": {"type": "string", "description": "a file in the library, or the name of a saved photo"},
                        "url": {"type": "string", "description": "a public https address of a JPG, PNG or WebP picture"},
                        "caption": {"type": "string", "description": "a few words under the picture"}}},
                    show_picture)

    def tool(self, grab: GrabFn, current_turn: Callable[[], dict[str, Any] | None], *, name: str = "send_screenshot",
             description: str = "") -> Tool:
        async def send_screenshot(a: dict[str, Any]) -> str:
            turn = current_turn() or {}
            tid = str(turn.get("turn_id") or "")
            if not tid:
                return "Error: a screenshot can only go into a chat reply."
            if self.count_for(tid) >= PER_TURN:
                return f"Error: at most {PER_TURN} screenshots in one reply."
            jpeg, w, h, problem = await grab(str(a.get("selector") or "").strip())
            if jpeg is None:
                return f"Error: {problem}"
            shown = self.add(tid, jpeg, w, h, str(a.get("caption") or ""))
            await emit_ui({"type": "image", "image_id": shown["id"], **{k: shown[k] for k in ("url", "width", "height", "caption")}})
            return (f"The screenshot ({w}x{h}) is in the chat now, under your reply; secure fields on it are covered. Say in a few words "
                    "what it shows; do not describe it at length.")
        return Tool(name,
                    description or "Show the user a screenshot of your browser tab in the chat (when they ask to see the page, or a picture "
                    "helps). Optional selector: only that part of the page. Typed passwords, card numbers and codes are covered.",
                    {"type": "object", "properties": {"selector": {"type": "string", "description": "CSS selector of the part to show"},
                                                      "caption": {"type": "string", "description": "a few words under the picture"}}},
                    send_screenshot)


def chat_image_routes(app: FastAPI, images: ChatImages, auth: Callable[..., Any]) -> None:
    @app.get("/api/chat-images/{iid}", dependencies=[Depends(auth)])
    async def chat_image(iid: str) -> FileResponse:
        p = images.path(iid)
        if p is None:
            raise HTTPException(404, "no such picture")
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
