"""The agent's picture: an uploaded JPEG / PNG / WebP (at most 2 MB), turned upright, cropped to a centred square, scaled to 512 px and
saved again without any metadata (EXIF, GPS, ICC, comments). Kept in the database, so it needs no extra volume."""

from __future__ import annotations

import hashlib
import io
import time
from typing import Any

MAX_BYTES = 2_000_000
SIZE = 512
MAX_PIXELS = 40_000_000
FORMATS = {"JPEG", "PNG", "WEBP"}

SCHEMA = "CREATE TABLE IF NOT EXISTS agent_avatar (id INTEGER PRIMARY KEY CHECK (id = 1), data BLOB NOT NULL, mime TEXT NOT NULL, " \
         "version INTEGER NOT NULL, etag TEXT NOT NULL);"


class AvatarError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def process(raw: bytes) -> tuple[bytes, str]:
    """(image bytes, media type). JPEG for opaque pictures, PNG when the picture has transparency."""
    from PIL import Image, ImageOps

    if len(raw) > MAX_BYTES:
        raise AvatarError("the picture is larger than 2 MB", 413)
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = img.format
        if fmt not in FORMATS:
            raise AvatarError("the picture must be JPEG, PNG or WebP")
        if img.width * img.height > MAX_PIXELS:
            raise AvatarError("the picture has too many pixels")
        img.load()
    except AvatarError:
        raise
    except Exception:  # noqa: BLE001 - not an image, or a broken one
        raise AvatarError("this is not a JPEG, PNG or WebP picture") from None
    img = ImageOps.exif_transpose(img)
    alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if alpha else "RGB")
    side = min(img.width, img.height)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    if side != SIZE:
        img = img.resize((SIZE, SIZE), Image.LANCZOS)
    clean = Image.new(img.mode, img.size)       # a fresh image: nothing of the original's info dict comes along
    clean.paste(img)
    buf = io.BytesIO()
    if alpha:
        clean.save(buf, "PNG", optimize=True)
        return buf.getvalue(), "image/png"
    clean.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue(), "image/jpeg"


class Avatar:
    def __init__(self, store: Any):
        self.store = store
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def get(self) -> dict[str, Any] | None:
        rows = self.store._all("SELECT data, mime, version, etag FROM agent_avatar WHERE id = 1")
        return rows[0] if rows else None

    def version(self) -> int:
        rows = self.store._all("SELECT version FROM agent_avatar WHERE id = 1")
        return int(rows[0]["version"]) if rows else 0

    def put(self, raw: bytes) -> dict[str, Any]:
        data, mime = process(raw)
        version = max(int(time.time() * 1000), self.version() + 1)
        etag = hashlib.sha256(data).hexdigest()[:24]
        self.store._run("INSERT INTO agent_avatar(id, data, mime, version, etag) VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "data = excluded.data, mime = excluded.mime, version = excluded.version, etag = excluded.etag", (data, mime, version, etag))
        return {"avatar_version": version, "mime": mime, "bytes": len(data), "width": SIZE, "height": SIZE}

    def delete(self) -> bool:
        return self.store._run("DELETE FROM agent_avatar WHERE id = 1").rowcount > 0
