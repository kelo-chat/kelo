"""The Android bridge's HTTP API (docker/android/bridge.py), typed. One client per server; every call answers a dict or raises DeviceError."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

MIN_TOKEN = 32
SCREEN_TIMEOUT = 35.0   # the bridge's dump (20 s) + reading it + a screenshot
CALL_TIMEOUT = 15.0


class DeviceError(Exception):
    """The device or its bridge could not do it. The message is for the model (and the owner): what happened, never the token."""


class DeviceClient:
    def __init__(self, base_url: str, token: str, http: Callable[..., Any] = httpx.AsyncClient):
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self._http = http

    @property
    def enabled(self) -> bool:
        return bool(self.base_url) and len(self.token) >= MIN_TOKEN

    async def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None,
                    timeout: float = CALL_TIMEOUT) -> dict[str, Any]:
        if not self.enabled:
            raise DeviceError("the Android device is not set up (ANDROID_BRIDGE_URL and a 32+ character ANDROID_BRIDGE_TOKEN)")
        try:
            async with self._http(timeout=timeout, headers={"Authorization": f"Bearer {self.token}"}) as c:
                r = await c.request(method, self.base_url + path, params=params, json=body)
        except httpx.TimeoutException:
            raise DeviceError("the Android device did not answer in time") from None
        except httpx.HTTPError:
            raise DeviceError("the Android bridge could not be reached") from None
        try:
            data = r.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("error"):
            raise DeviceError(str(data["error"])[:300])
        if r.status_code >= 400 or not isinstance(data, dict):
            raise DeviceError(f"the Android bridge answered HTTP {r.status_code}")
        return data

    async def status(self) -> dict[str, Any]:
        return await self._call("GET", "/status")

    async def screen(self, image: bool = False) -> dict[str, Any]:
        return await self._call("GET", "/screen", params={"image": "1" if image else "0"}, timeout=SCREEN_TIMEOUT)

    async def frame(self) -> dict[str, Any]:
        """The picture of the screen alone ({"png": base64}), no dump: fast enough for the browser view."""
        return await self._call("GET", "/screen", params={"image": "only"})

    async def tap(self, x: int, y: int) -> dict[str, Any]:
        return await self._call("POST", "/tap", body={"x": int(x), "y": int(y)})

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> dict[str, Any]:
        return await self._call("POST", "/swipe", body={"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2), "ms": int(ms)})

    async def text(self, text: str) -> dict[str, Any]:
        return await self._call("POST", "/text", body={"text": text})

    async def key(self, key: str) -> dict[str, Any]:
        return await self._call("POST", "/key", body={"key": key})

    async def open(self, package: str) -> dict[str, Any]:
        return await self._call("POST", "/open", body={"package": package})

    async def apps(self) -> dict[str, Any]:
        return await self._call("GET", "/apps")
