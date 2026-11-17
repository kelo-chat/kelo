"""Which newer released app fits this server (docs/version-notices.md, part 3).

`app_update_for(platform, channel, version, build)` answers with the version of the newest RELEASED app of that platform and channel
that is newer than the caller and whose `min_server` (releases/apps.json) is at or below this server's version, or None.

- Released = an entry of releases/apps.json (a store row is recorded only once it is live: docs/versioning.md).
- Channel `download` (sideload, desktop installers): the entry must also be a row of kelo.chat/download/latest.json whose Ed25519
  signature checks (release_sign.verify_row); an unsigned or unlisted row is never offered. Store channels (play, appstore, msstore)
  rely on the record alone: the app's own store is the only place it could update from.
- The caller names its channel in `X-Kelo-App-Channel`; without it nothing is offered (a store build must never be told about a file
  only the download page has).
- Shown by `X-Kelo-App-Update: <version>` on /api/about and /api/profile and `app_update` in GET /api/about. The list is read at most
  every 6 hours (10 minutes after a failure); the answer never blocks or fails a request.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from . import release_sign
from . import version as release_version
from .app_releases import AppReleases, Release

log = logging.getLogger("assistant.app_updates")

HEADER = "X-Kelo-App-Update"
CHANNEL_HEADER = "X-Kelo-App-Channel"
TTL = 6 * 3600
RETRY = 600
LATEST_URL = "https://kelo.chat/download/latest.json"
# the download rows of latest.json that belong to an app platform
_ROWS = {"android": ("android",), "desktop": ("windows", "windows-msi", "mac"), "ios": ()}

Fetcher = Callable[[], Awaitable[bytes]]


async def fetch_latest() -> bytes:
    """kelo.chat/download/latest.json through the network guard (https, public address, no redirects)."""
    from . import netguard

    r = await netguard.fetch("GET", LATEST_URL, allowed_hosts=["kelo.chat"], headers={"Accept": "application/json", "User-Agent": "Kelo"},
                             timeout=15.0, max_body=1_000_000)
    if r.status != 200 or r.truncated:
        raise RuntimeError(f"the release list answered HTTP {r.status}")
    return r.body


def signed_rows(latest: Any) -> dict[str, list[dict[str, Any]]]:
    """The rows of a latest.json that carry a valid signature, by latest.json platform key."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(latest, dict):
        return out
    for key in ("android", "windows", "windows-msi", "mac"):
        row = latest.get(key)
        if isinstance(row, dict) and release_sign.verify_row(key, row):
            out[key] = [row]
    return out


class AppUpdates:
    def __init__(self, records: AppReleases, server_version: Callable[[], str] | str, *, fetcher: Fetcher | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.records = records
        self._server = server_version if callable(server_version) else (lambda: server_version)
        self.fetcher = fetcher or fetch_latest
        self.clock = clock
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self._until = 0.0

    async def _signed(self) -> dict[str, list[dict[str, Any]]]:
        now = self.clock()
        if now < self._until:
            return self._rows
        try:
            self._rows = signed_rows(json.loads((await self.fetcher()).decode("utf-8")))
            self._until = now + TTL
        except Exception as e:  # noqa: BLE001 - a missing list only means no banner
            log.info("app release list not read (%s)", type(e).__name__)
            self._rows = {}
            self._until = now + RETRY
        return self._rows

    @staticmethod
    def _listed(rel: Release, rows: list[dict[str, Any]]) -> bool:
        return any(r.get("version") == rel.version or r.get("version_code") == rel.build for r in rows)

    async def app_update_for(self, platform: str, channel: str | None, version: str | None, build: int | None = None) -> str | None:
        if platform not in _ROWS or channel not in ("download", "play", "appstore", "msstore"):
            return None
        try:
            have = release_version.parse(version) if version else None
            server = release_version.parse(self._server())
        except ValueError:
            return None
        rows: list[dict[str, Any]] = []
        if channel == "download":
            signed = await self._signed()
            rows = [r for key in _ROWS[platform] for r in signed.get(key, [])]
            if not rows:
                return None
        best: Release | None = None
        for rel in self.records.entries.get(platform, []):
            if rel.channel != channel:
                continue
            try:
                if rel.min_server and release_version.parse(rel.min_server) > server:
                    continue
            except ValueError:
                continue
            if channel == "download" and not self._listed(rel, rows):
                continue
            if build is not None and rel.build > 0:
                newer = rel.build > build
            else:
                newer = have is not None and rel.version_key > have
            if newer and (best is None or (rel.build, rel.version_key) > (best.build, best.version_key)):
                best = rel
        return best.version if best else None

    async def for_header(self, app_header: str | None, channel: str | None) -> str | None:
        """The same, from the caller's X-Kelo-App and X-Kelo-App-Channel headers (None when either is missing or odd)."""
        got = release_version.parse_app_header(app_header)
        if got is None or not channel:
            return None
        platform, version, build = got
        return await self.app_update_for(platform, channel.strip().lower(), version, build)
