"""Pictures, videos and sound the user uploaded: what kind each file is, small previews and how long a clip runs.

- `kind_of(name)`: image | video | audio | document, from the file's suffix.
- `MediaCache(dir)`: previews (JPEG) and facts (duration, size in pixels), made on first use and kept in `dir` (the home's hidden
  .thumbs/ folder). A cached item is named after the file's name, size and modification time, so a changed or replaced file never
  shows an old preview; previews of deleted files are dropped by `forget`.
- A photo preview is turned upright by its EXIF orientation. HEIC is read when pillow-heif is installed (uploads already arrive
  as JPEG; this is for older files). A video preview is one frame about a second in (or a tenth of the way through a short
  clip), turned by the video's display matrix, read with PyAV when it is installed; without it a video has no preview but still plays.
- Durations come from PyAV, else from mutagen (MP4/M4A/MOV/MP3/OGG/FLAC/WAV).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .photos import IMAGE_SUFFIXES, MAX_PIXELS, heic_supported

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm", ".3gp", ".mkv"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".amr"}
MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".3gp": "video/3gpp",
    ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".opus": "audio/ogg", ".flac": "audio/flac", ".amr": "audio/amr",
    ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
}
THUMB_SIZES = (128, 256, 512, 1024, 2048)
THUMB_QUALITY = 82
CACHE_KEEP = 2_000          # previews and facts kept at most; the oldest go first


class MediaError(ValueError):
    """No preview can be made; the message is safe to show."""


def kind_of(name: str) -> str:
    s = Path(name).suffix.lower()
    if s in IMAGE_SUFFIXES:
        return "image"
    if s in VIDEO_SUFFIXES:
        return "video"
    if s in AUDIO_SUFFIXES:
        return "audio"
    return "document"


def mime_of(name: str) -> str:
    return MIME.get(Path(name).suffix.lower(), "application/octet-stream")


def thumb_size(asked: int | None) -> int:
    """The nearest kept size at or above what was asked (so a few sizes cover every screen)."""
    n = int(asked or 256)
    return next((s for s in THUMB_SIZES if s >= n), THUMB_SIZES[-1])


def av_available() -> bool:
    try:
        import av  # noqa: F401 - only whether it is installed
    except ImportError:
        return False
    return True


def _jpeg(img: Image.Image, size: int, out: Path) -> None:
    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    tmp = out.with_name(out.name + ".tmp")
    img.save(tmp, "JPEG", quality=THUMB_QUALITY, optimize=True)
    tmp.replace(out)


def image_thumb(src: Path, size: int, out: Path) -> None:
    if src.suffix.lower() in (".heic", ".heif"):
        if not heic_supported():
            raise MediaError("HEIC pictures cannot be read on this server")
        import pillow_heif
        pillow_heif.register_heif_opener()
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(src) as img:
            img.draft("RGB", (size, size))          # a JPEG is decoded at a fraction of its size: fast and light on memory
            upright = ImageOps.exif_transpose(img)
            _jpeg(upright, size, out)
    except MediaError:
        raise
    except Exception:  # noqa: BLE001 - anything Pillow cannot read has no preview
        raise MediaError("this picture cannot be read") from None


def video_frame(src: Path) -> Image.Image:
    """One frame about a second in (a tenth of the way through a short clip), upright."""
    if not av_available():
        raise MediaError("video previews need PyAV on this server")
    import av
    try:
        with av.open(str(src)) as box:
            if not box.streams.video:
                raise MediaError("this file has no picture")
            stream = box.streams.video[0]
            stream.thread_type = "AUTO"
            length = (box.duration or 0) / 1_000_000
            at = min(1.0, length / 10) if length else 0.0
            if at > 0 and stream.time_base:
                try:
                    box.seek(int(at / stream.time_base), stream=stream, any_frame=False, backward=True)
                except av.error.FFmpegError:
                    box.seek(0)
            for frame in box.decode(stream):
                img = frame.to_image()
                turn = int(getattr(frame, "rotation", 0) or 0)   # counterclockwise degrees, as the display matrix says
                return img.rotate(turn, expand=True) if turn % 360 else img
    except MediaError:
        raise
    except Exception:  # noqa: BLE001 - a damaged or unknown video has no preview
        raise MediaError("this video cannot be read") from None
    raise MediaError("this video has no frames")


def probe(src: Path) -> dict[str, Any]:
    """{"duration": seconds or None, "width", "height"} of a video or sound file (None where unknown)."""
    out: dict[str, Any] = {"duration": None, "width": None, "height": None}
    if av_available():
        import av
        try:
            with av.open(str(src)) as box:
                if box.duration:
                    out["duration"] = round(box.duration / 1_000_000, 2)
                if box.streams.video:
                    v = box.streams.video[0]
                    w, h = v.codec_context.width or None, v.codec_context.height or None
                    out["width"], out["height"] = w, h
                    if out["duration"] is None and v.duration and v.time_base:
                        out["duration"] = round(float(v.duration * v.time_base), 2)
            return out
        except Exception:  # noqa: BLE001 - fall back to mutagen
            pass
    try:
        import mutagen
        f = mutagen.File(str(src))
        if f is not None and getattr(f, "info", None) is not None and getattr(f.info, "length", None):
            out["duration"] = round(float(f.info.length), 2)
    except Exception:  # noqa: BLE001 - unknown length is fine
        pass
    return out


class MediaCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _key(self, src: Path, what: str) -> str:
        st = src.stat()
        raw = f"{src.name}\0{st.st_size}\0{st.st_mtime_ns}\0{what}".encode()
        return hashlib.sha256(raw).hexdigest()[:32]

    def _ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def thumb(self, src: Path, size: int) -> Path:
        """The cached JPEG preview of a picture or video at most `size` pixels on its long side. Raises MediaError."""
        kind = kind_of(src.name)
        if kind not in ("image", "video"):
            raise MediaError("only pictures and videos have previews")
        size = thumb_size(size)
        out = self.root / f"{self._key(src, f'thumb{size}')}.jpg"
        if out.is_file():
            return out
        self._ready()
        if kind == "image":
            image_thumb(src, size, out)
        else:
            frame = video_frame(src)
            _jpeg(frame, size, out)
        self._trim()
        return out

    def info(self, src: Path) -> dict[str, Any]:
        """Duration and pixel size of a video or sound file, read once and kept."""
        if kind_of(src.name) not in ("video", "audio"):
            return {}
        p = self.root / f"{self._key(src, 'info')}.json"
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            pass
        facts = probe(src)
        try:
            self._ready()
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(facts))
            tmp.replace(p)
        except OSError:
            pass
        return facts

    def _trim(self) -> None:
        """Keep at most CACHE_KEEP items: the least recently used go first."""
        try:
            items = [p for p in self.root.iterdir() if p.is_file()]
            if len(items) <= CACHE_KEEP:
                return
            items.sort(key=lambda p: p.stat().st_atime)
        except OSError:
            return
        for p in items[:len(items) - CACHE_KEEP]:
            try:
                p.unlink()
            except OSError:
                pass

    def clear_stale(self, live: list[Path]) -> int:
        """Remove previews and facts of files that are gone. Returns how many were removed."""
        keep: set[str] = set()
        for src in live:
            try:
                for what in (*(f"thumb{s}" for s in THUMB_SIZES), "info"):
                    keep.add(self._key(src, what))
            except OSError:
                continue
        n = 0
        try:
            for p in self.root.iterdir():
                if p.is_file() and p.name.split(".", 1)[0] not in keep:
                    try:
                        p.unlink()
                        n += 1
                    except OSError:
                        pass
        except OSError:
            pass
        return n


def disk_free(path: str | Path) -> int:
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except (OSError, AttributeError):
        return 1 << 62


__all__ = ["AUDIO_SUFFIXES", "VIDEO_SUFFIXES", "MediaCache", "MediaError", "kind_of", "mime_of", "thumb_size", "disk_free"]
