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

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from .agent import Tool, emit_ui

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_images (
    id TEXT PRIMARY KEY, turn_id TEXT NOT NULL DEFAULT '', message_id INTEGER, width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0, caption TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS chat_images_message ON chat_images(message_id);
"""
MAX_WIDTH = 1280
MAX_BYTES = 900_000
PER_TURN = 6

# The page's secure fields that are on screen, as rectangles in CSS pixels, and the viewport width (to scale them to the picture).
SECURE_RECTS_JS = """() => {
  const sel = 'input[type=password], input[autocomplete~="cc-number"], input[autocomplete~="cc-csc"], input[autocomplete~="cc-exp"],'
    + ' input[autocomplete~="one-time-code"], input[autocomplete~="current-password"], input[autocomplete~="new-password"],'
    + ' input[name*="card" i], input[name*="cvc" i], input[name*="cvv" i], input[name*="pin" i], input[id*="cvc" i], input[id*="cvv" i]';
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
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
            shoot = getattr(session, "shoot", None)   # the phone view keeps its screen (BrowserSession.shoot)
            raw = await (shoot(page, type="png", timeout=8_000) if shoot else page.screenshot(type="png", timeout=8_000))
        except Exception as e:  # noqa: BLE001 - the words go back to the agent
            return None, 0, 0, f"the screenshot failed: {str(e)[:160]}"
        import asyncio
        jpeg, w, h = await asyncio.to_thread(cover, raw, list(info.get("rects") or []), float(info.get("vw") or 0), crop)
        if jpeg is None:
            return None, 0, 0, "the picture was too large to send"
        return jpeg, w, h, ""
    return grab


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
        self.store._run("UPDATE chat_images SET message_id = ? WHERE turn_id = ? AND message_id IS NULL", (message_id, turn_id))

    def for_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [m["id"] for m in rows if m.get("role") == "assistant"]
        if not ids:
            return rows
        marks = ",".join("?" * len(ids))
        found: dict[int, list[dict[str, Any]]] = {}
        for r in self.store._all(f"SELECT * FROM chat_images WHERE message_id IN ({marks}) ORDER BY created_at", tuple(ids)):
            found.setdefault(int(r["message_id"]), []).append(self.public(r))
        return [{**m, "images": found[m["id"]]} if m["id"] in found else m for m in rows]

    def clear(self) -> None:
        for r in self.store._all("SELECT id FROM chat_images"):
            p = self.path(r["id"])
            if p is not None:
                p.unlink(missing_ok=True)
        self.store._run("DELETE FROM chat_images")

    def library_tool(self, library: Any, current_turn: Callable[[], dict[str, Any] | None]) -> Tool:
        """show_picture: a picture of the user's library (a camera snapshot or photo saved with connector_download) shown in the chat."""
        async def show_picture(a: dict[str, Any]) -> str:
            from PIL import Image, ImageOps
            turn = current_turn() or {}
            tid = str(turn.get("turn_id") or "")
            if not tid:
                return "Error: a picture can only go into a chat reply."
            if self.count_for(tid) >= PER_TURN:
                return f"Error: at most {PER_TURN} pictures in one reply."
            try:
                path = library.path(str(a.get("name") or ""))
                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((1600, 1600))
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=85)
                    w, h = im.size
            except Exception as e:  # noqa: BLE001 - a missing file, or one that is not a picture
                return f"Error: that is not a picture in the library ({type(e).__name__})."
            shown = self.add(tid, buf.getvalue(), w, h, str(a.get("caption") or ""))
            await emit_ui({"type": "image", "image_id": shown["id"], **{k: shown[k] for k in ("url", "width", "height", "caption")}})
            return "The picture is in the chat now, under your reply. Say in a few words what it shows."
        return Tool("show_picture", "Show the user a picture from their library (files/) in the chat, for example a camera snapshot you saved "
                    "with connector_download.",
                    {"type": "object", "properties": {"name": {"type": "string", "description": "file name in the library"},
                                                      "caption": {"type": "string", "description": "a few words under the picture"}},
                     "required": ["name"]}, show_picture)

    def tool(self, grab: GrabFn, current_turn: Callable[[], dict[str, Any] | None]) -> Tool:
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
        return Tool("send_screenshot",
                    "Show the user a screenshot of your browser tab in the chat (when they ask to see the page, or a picture helps). "
                    "Optional selector: only that part of the page. Typed passwords, card numbers and codes are covered.",
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
