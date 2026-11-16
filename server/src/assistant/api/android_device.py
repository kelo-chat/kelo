"""The Android device's state for the apps' "Android device" page (docs/android-device.md, phase 4).

GET  /api/android        {"configured", "connected", "model", "android_version", "screen": {"width","height"} | null,
                          "play_store", "korean_input", "checked_at"}
POST /api/android/check  the same, from a fresh reading of the bridge's /status (the page's "check connection" button)

`configured` = ANDROID_DEVICE is set. Not configured: 200 with `configured` false and nothing else filled. The bridge down or the device
not answering: `connected` false, still 200. The device's address, the bridge's address and token never go out (only the model, the
Android version, the screen size and two yes/no answers). The address is changed in the server's .env, not here.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from ..android.device import DeviceError
from .context import AppContext


def reading(configured: bool, status: dict[str, Any] | None = None, now: float | None = None) -> dict[str, Any]:
    """The answer for the apps from the bridge's /status (`None`: unreachable). Only known fields are copied."""
    out: dict[str, Any] = {"configured": configured, "connected": False, "model": "", "android_version": "", "screen": None,
                           "play_store": False, "korean_input": False, "checked_at": 0.0}
    if not configured:
        return out
    out["checked_at"] = time.time() if now is None else now
    if not status or status.get("connected") is not True:
        return out
    size = status.get("size")
    width, height = (size[0], size[1]) if isinstance(size, (list, tuple)) and len(size) == 2 else (0, 0)
    ok = all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height))
    out.update(connected=True, model=str(status.get("model") or "")[:80], android_version=str(status.get("android") or "")[:20],
               screen={"width": width, "height": height} if ok else None,
               play_store=status.get("play_store") is True, korean_input=status.get("korean_input") is True)
    return out


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    require_auth = ctx.require_auth
    settings = ctx.settings
    client = ctx.android_client

    async def read() -> dict[str, Any]:
        configured = bool(settings.android_device)
        if not configured:
            return reading(False)
        try:
            return reading(True, await client.status())
        except DeviceError:   # the bridge is down or the device does not answer: "not connected", not an error
            return reading(True, None)

    @router.get("/api/android", dependencies=[Depends(require_auth)])
    async def android_state() -> dict[str, Any]:
        return await read()

    @router.post("/api/android/check", dependencies=[Depends(require_auth)])
    async def android_check() -> dict[str, Any]:
        return await read()

    return router
