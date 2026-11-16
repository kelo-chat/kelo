"""Each service's own app icon for the Connectors screen, served by this server so every app shows the same picture and a new
connector needs no app release.

The icons live in connector_icons/ next to this file: <key>.png (256x256) and <key>-128.png (128x128), square, fetched once from
the service's own site (its web app manifest, apple-touch-icon or brand page) and recorded in connector_icons/manifest.json with
the source URL and date. They are the services' trademarks and are used only to show which service a row is (NOTICE).

GET /api/connectors rows (and /api/mail/providers) carry "icon" (256 px), "icon_small" (128 px) and "icon_color" (the icon's main
colour, "#RRGGBB", for the placeholder while it loads). A service without an icon has none of the three; the apps draw its
initials tile. URLs end with ?v=<content hash>, so a changed picture is a new URL; GET /api/connectors/icons/<name>.png is public
(no secret in it), cacheable with an ETag, and served with a CSP that allows nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DIR = Path(__file__).with_name("connector_icons")
NAME = re.compile(r"^[a-z0-9_]+(-128)?$")
PREFIX = "/api/connectors/icons/"
# no script, no style, nothing loaded: the file is a picture and nothing else
HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
           "Cross-Origin-Resource-Policy": "cross-origin"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    try:
        return json.loads((DIR / "manifest.json").read_text())
    except (OSError, ValueError):
        return {"icons": {}, "connectors": {}, "mail": {}}


@lru_cache(maxsize=512)
def read(name: str) -> tuple[bytes, str] | None:
    """The PNG for `name` (<key> or <key>-128) and its ETag; None when there is none or the name is not one of ours."""
    if not NAME.match(name) or name.removesuffix("-128") not in manifest()["icons"]:
        return None
    try:
        data = (DIR / f"{name}.png").read_bytes()
    except OSError:
        return None
    if not data.startswith(PNG_MAGIC):
        return None
    return data, hashlib.sha256(data).hexdigest()[:16]


def _fields(key: str | None) -> dict[str, str]:
    info = manifest()["icons"].get(key or "")
    big, small = (read(key) if info else None), (read(f"{key}-128") if info else None)
    if not big or not small:
        return {}
    return {"icon": f"{PREFIX}{key}.png?v={big[1]}", "icon_small": f"{PREFIX}{key}-128.png?v={small[1]}",
            "icon_color": info.get("color", "")}


def for_connector(cid: str) -> dict[str, str]:
    """"icon", "icon_small", "icon_color" for a connector row; {} when it has no icon."""
    return _fields(manifest()["connectors"].get(cid))


def for_mail(provider: str) -> dict[str, str]:
    """The same for a mailbox provider (/api/mail/providers)."""
    return _fields(manifest()["mail"].get(provider))
