"""Save a product photo from a web page (for style try-on and shopping comparisons), optionally cropping away the top.

The download is an ordinary public fetch: https only, public addresses only, one host, no redirects, size limit. The bytes are
decoded as an image before anything is kept, so a page or script saved under an image name is refused.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from ..agent import Tool
from ..credentials import apply_secret, clean_name, redact
from ..netguard import NetGuardError, fetch
from ..quota import QuotaError
from ..store import Store
from ..vault import record_http, use_credential

MAX_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 60_000_000
MAX_CROP_PERCENT = 60
TYPES = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MEDIA = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}\.(jpg|png|webp)$")
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageStore:
    quota: Any = None   # set by the app (see assistant.quota)

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def save(self, name: str, data: bytes) -> None:
        """Keep an image under `name`; raises QuotaError when it would pass the storage cap."""
        if self.quota:
            old = self.root / name
            self.quota.check(len(data), old.stat().st_size if old.is_file() else 0)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(self.root / name)
        if self.quota:
            self.quota.wrote()

    def delete(self, name: str) -> bool:
        p = self.path(name)
        if not p:
            return False
        p.unlink()
        if self.quota:
            self.quota.wrote()
        return True

    def path(self, name: str) -> Path | None:
        p = self.root / name
        return p if NAME.match(name) and p.is_file() else None

    def names(self) -> list[str]:
        return sorted(p.name for p in self.root.glob("*") if NAME.match(p.name)) if self.root.is_dir() else []


def stem(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")[:70] or "product"


def process(data: bytes, crop_percent: int) -> tuple[bytes, str]:
    """(bytes to keep, extension). Raises ValueError when it is not a JPEG/PNG/WebP image."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format or ""
            if fmt not in TYPES:
                raise ValueError("not a JPG, PNG or WebP image")
            im.load()
            if not crop_percent:
                return data, TYPES[fmt]
            cut = int(im.height * crop_percent / 100)
            out = io.BytesIO()
            im.crop((0, cut, im.width, im.height)).save(out, format=fmt, **({"quality": 92} if fmt != "PNG" else {}))
            return out.getvalue(), TYPES[fmt]
    except (OSError, Image.DecompressionBombError, SyntaxError) as e:
        raise ValueError("not a readable JPG, PNG or WebP image") from e


def image_tools(store: ImageStore, fetcher: Any = fetch, creds: Store | None = None, keyring: Any = None) -> list[Tool]:
    async def save_product_image(args: dict[str, Any]) -> str:
        url = str(args.get("url", ""))
        try:
            crop = int(args.get("crop_top_percent") or 0)
            if not 0 <= crop <= MAX_CROP_PERCENT:
                raise ValueError
        except (TypeError, ValueError):
            return f"error: crop_top_percent must be a number from 0 to {MAX_CROP_PERCENT}"
        host = (urlsplit(url).hostname or "").lower()
        try:
            reply = await fetcher("GET", url, allowed_hosts=[host], headers={"Accept": "image/png,image/jpeg,image/webp", "User-Agent": "Mozilla/5.0"},
                                  timeout=15.0, max_body=MAX_BYTES)
        except NetGuardError as e:
            return f"error: {e}"
        if reply.status != 200:
            return f"error: the image server answered HTTP {reply.status}"
        if reply.truncated:
            return "error: the image is too large"
        try:
            data, ext = process(reply.body, crop)
        except ValueError as e:
            return f"error: {e}"
        name = f"{stem(str(args.get('name') or host.split('.')[0]))}.{ext}"
        try:
            store.save(name, data)
        except QuotaError as e:
            return f"error: {e}"
        return f"saved {name} ({len(data)} bytes{f', top {crop}% removed' if crop else ''}). The user can open it at /api/tryon/{name}."

    async def list_product_images(args: dict[str, Any]) -> str:
        return "\n".join(f"- {n}" for n in store.names()) or "No saved product images."

    def keep(data: bytes, name: str, host: str) -> str:
        data, ext = process(data, 0)
        out = f"{stem(name or host.split('.')[0])}.{ext}"
        store.save(out, data)
        return out

    async def generate_image(args: dict[str, Any]) -> str:
        if creds is None:
            return "error: image generation is not available"
        prompt = str(args.get("prompt", "")).strip()
        if not prompt or len(prompt) > 4000:
            return "error: prompt is required (up to 4000 characters)"
        try:
            cred = use_credential(creds, clean_name(str(args.get("credential", ""))), tool="generate_image", kind="api")
            path = str(args.get("path") or "/v1/images/generations")
            if not path.startswith("/") or "{" in path or "?" in path or ".." in path:
                return "error: path must be a plain absolute path such as /v1/images/generations"
            size = str(args.get("size") or "1024x1024")
            if not re.fullmatch(r"\d{3,4}x\d{3,4}", size):
                return "error: size looks like 1024x1024"
            url, auth = apply_secret(f"https://{cred['hosts'][0]}{path}", cred["placement"], cred["secret"])
            from ..cred_keyring import via
            reply = await via(cred, fetcher, keyring)("POST", url, allowed_hosts=cred["hosts"], headers={"Content-Type": "application/json", **auth},
                                  content=json.dumps({"model": str(args.get("model") or "gpt-image-1"), "prompt": prompt, "size": size, "n": 1}).encode(),
                                  timeout=180.0, max_body=MAX_BYTES)
            hint = record_http(creds, cred["name"], reply.status, "generate_image")
            if reply.status != 200 or reply.truncated:
                return f"error: the image service answered HTTP {reply.status}: {redact(reply.body[:300].decode('utf-8', 'replace'), cred['secret'])}{hint}"
            item = (json.loads(reply.body).get("data") or [{}])[0]
            if item.get("b64_json"):
                raw = base64.b64decode(item["b64_json"], validate=True)
            elif str(item.get("url", "")).startswith("https://"):
                host = (urlsplit(item["url"]).hostname or "").lower()
                got = await fetcher("GET", item["url"], allowed_hosts=[host], timeout=30.0, max_body=MAX_BYTES)   # no key goes to this host
                if got.status != 200 or got.truncated:
                    return "error: could not download the generated image"
                raw = got.body
            else:
                return "error: the image service returned no image"
            name = keep(raw, str(args.get("name") or "generated"), "generated")
        except (NetGuardError, QuotaError) as e:
            return f"error: {e}"
        except (ValueError, binascii.Error, AttributeError, KeyError, TypeError):
            return "error: the image service returned something that is not an image"
        return f"saved {name}. The user can open it at /api/tryon/{name}."

    return [
        Tool("generate_image",
             "Make an image from a text prompt with the image service the user connected; it costs money on their account.",
             {"type": "object", "properties": {"credential": {"type": "string", "description": "the service's key name (an OpenAI-compatible "
                                                              "/images/generations API added with add_service_key, http bearer)"},
                                               "prompt": {"type": "string"}, "size": {"type": "string"},
                                               "model": {"type": "string"}, "name": {"type": "string"}, "path": {"type": "string"}},
              "required": ["credential", "prompt"]}, generate_image, requires_approval=True),
        Tool("save_product_image",
             "Keep a product photo from a public https image URL (JPG, PNG or WebP up to 25 MB).",
             {"type": "object", "properties": {"url": {"type": "string"}, "name": {"type": "string"},
                                               "crop_top_percent": {"type": "integer", "description": "0-60: cut this share off the top"}},
              "required": ["url"]}, save_product_image),
        Tool("list_product_images", "List the product photos saved with save_product_image.", {"type": "object", "properties": {}},
             list_product_images, read_only=True),
    ]
