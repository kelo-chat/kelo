"""The version contract on every request (docs/versioning.md, MIN VERSION CONTRACT).

- Every answer carries `X-Kelo-Server: <version>+<build>` (this server's release, version.json), so an app can tell that the
  server is older than its own `min_server` without an extra call.
- An app that names itself (`X-Kelo-App: android/0.9.0+402400`) and is older than `min_app` (or below the operator's per-platform
  ASSISTANT_MIN_APP_* build) gets 426 {"code": "app_too_old", ...} on /api/*, except the few calls an app needs to show its
  "update needed" screen (/api/about, /api/e2e/info) and /healthz. Requests without the header (older apps, scripts) are never
  refused here: their apps read min_app_version from /api/profile as before.
"""

from __future__ import annotations

import json
from typing import Any

from .. import app_releases
from .. import version as release_version

EXEMPT = ("/api/about", "/api/e2e/info")


class VersionMiddleware:
    """`releases` (the record of released apps) and `enforcement` default to the record shipped with the server. A minimum above
    every released app of a platform (a misbuilt image, an operator's typo) is not enforced for that platform and is logged once:
    no phone is ever locked out by a version it cannot install (docs/versioning.md, Minimum app version rule)."""

    def __init__(self, app: Any, release: release_version.Release | None = None, min_builds: dict[str, int] | None = None,
                 releases: app_releases.AppReleases | None = None, enforcement: app_releases.Enforcement | None = None):
        self.app = app
        self.release = release or release_version.current()
        if enforcement is None:   # (create_app passes its own, which it has already logged)
            enforcement = (releases or app_releases.AppReleases.load()).enforceable(self.release.min_app, dict(min_builds or {}))
            app_releases.report(enforcement, "version.json min_app / ASSISTANT_MIN_APP_*")
        self.enforcement = enforcement
        self.min_builds = dict(enforcement.min_builds)
        self.header = (b"x-kelo-server", self.release.header.encode("ascii"))

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        if path.startswith("/api/") and path not in EXEMPT:
            value = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k == b"x-kelo-app"), None)
            refused = release_version.app_too_old(value, self.release, self.min_builds, self.enforcement.skip_version)
            if refused is not None:
                body = json.dumps(refused).encode()
                await send({"type": "http.response.start", "status": 426,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                        self.header]})
                await send({"type": "http.response.body", "body": body})
                return

        async def send_with_header(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), self.header]}
            await send(message)

        await self.app(scope, receive, send_with_header)
