"""Photos the user uploads from the phone (JPEG, PNG, WebP, HEIC): checked, cleaned of location data, and shown to the model on request.

- A file is decoded as an image before it is kept, so something else under an image name is refused.
- GPS position (the EXIF GPS block, and an XMP packet that carries GPS fields) is removed; the picture itself is not re-compressed
  unless something had to be removed (a JPEG then keeps its own quantisation tables).
- HEIC/HEIF is converted to JPEG when the pillow-heif library is installed (it is in the Docker image); without it HEIC is refused with
  a message that says how to send the photo instead.
- `view_image` gives the model a copy scaled down to at most VIEW_MAX_SIDE pixels, as a JPEG data: URL (see agent.ToolOutput).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_IMAGE = 15_000_000
MAX_PIXELS = 60_000_000
VIEW_MAX_SIDE = 1568           # what vision models use at full detail; larger only costs tokens
VIEW_MAX_BYTES = 1_500_000
GPS_IFD = 0x8825
FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "HEIF": ".heic"}
NO_HEIC = ("HEIC photos cannot be read on this server. Send the photo as JPEG instead (on the phone: share it as JPEG, or set the "
           "camera to save JPEG / 'most compatible').")


class PhotoError(ValueError):
    """Refused photo; the message is safe to show the user."""


def heic_supported() -> bool:
    try:
        import pillow_heif  # noqa: F401 - only whether it is installed
    except ImportError:
        return False
    return True


def _open(data: bytes) -> Image.Image:
    if heic_supported():
        import pillow_heif
        pillow_heif.register_heif_opener()
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Image.DecompressionBombError:
        raise PhotoError("the photo has too many pixels") from None
    except Exception:  # noqa: BLE001 - anything Pillow cannot decode is not a photo we keep
        raise PhotoError("the file is not a readable JPEG, PNG, WebP or HEIC image") from None
    return img


def is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_SUFFIXES


def prepare_upload(name: str, data: bytes) -> tuple[str, bytes]:
    """(file name to keep, bytes to keep) for an uploaded photo: decoded, GPS removed, HEIC turned into JPEG. Raises PhotoError."""
    suffix = Path(name).suffix.lower()
    if suffix in (".heic", ".heif") and not heic_supported():
        raise PhotoError(NO_HEIC)
    if not data or len(data) > MAX_IMAGE:
        raise PhotoError(f"a photo must be between 1 byte and {MAX_IMAGE // 1_000_000} MB")
    img = _open(data)
    fmt = img.format or ""
    if fmt not in FORMATS:
        raise PhotoError("only JPEG, PNG, WebP and HEIC photos can be uploaded")
    if fmt == "HEIF" and not heic_supported():
        raise PhotoError(NO_HEIC)
    exif = img.getexif()
    xmp = img.info.get("xmp") or b""
    has_gps = GPS_IFD in exif or (b"GPS" in xmp if isinstance(xmp, bytes) else "GPS" in str(xmp))
    stem = Path(name).stem
    if fmt == "HEIF":   # to JPEG, upright, without GPS
        exif.pop(GPS_IFD, None)
        upright = ImageOps.exif_transpose(img).convert("RGB")
        exif[0x0112] = 1
        buf = io.BytesIO()
        upright.save(buf, "JPEG", quality=90, exif=exif.tobytes(), icc_profile=img.info.get("icc_profile"))
        return stem + ".jpg", buf.getvalue()
    keep_name = stem + (".jpg" if fmt == "JPEG" else FORMATS[fmt])
    if not has_gps:
        return keep_name, data
    exif.pop(GPS_IFD, None)
    buf = io.BytesIO()
    extra = {"exif": exif.tobytes()}
    if img.info.get("icc_profile"):
        extra["icc_profile"] = img.info["icc_profile"]
    if fmt == "JPEG":
        img.save(buf, "JPEG", quality="keep", subsampling="keep", **extra)
    elif fmt == "PNG":
        img.save(buf, "PNG", **extra)
    else:
        img.save(buf, "WEBP", lossless=bool(img.info.get("lossless")), quality=95, **extra)
    return keep_name, buf.getvalue()


def view_copy(path: Path) -> tuple[str, int, int, int, int]:
    """(data: URL of a JPEG at most VIEW_MAX_SIDE px, original width, height, shown width, height)."""
    return view_bytes(path.read_bytes())


def view_bytes(data: bytes) -> tuple[str, int, int, int, int]:
    """view_copy for bytes already in memory (raises PhotoError when they are not a picture)."""
    img = _open(data)
    w, h = img.size
    shown = ImageOps.exif_transpose(img).convert("RGB")
    shown.thumbnail((VIEW_MAX_SIDE, VIEW_MAX_SIDE))
    for quality in (85, 70, 55):
        buf = io.BytesIO()
        shown.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() <= VIEW_MAX_BYTES:
            break
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), w, h, shown.width, shown.height
