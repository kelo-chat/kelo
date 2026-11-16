"""Kelo's release version: one tracked file, `version.json` at the repository root (docs/versioning.md).

    {"version": "0.9.0", "build": 1, "min_app": "0.9.0", "min_server": "0.9.0"}

- `version`: the release, MAJOR.MINOR.PATCH. Pre-release versions are 0.x.y; 1.0.0 is the public launch.
- `build`: a whole number that only rises, set when a release is made (tools/release/bump.py), never computed from the clock at
  build time, so building the same commit again gives the same version (verifiable builds).
- `min_app`: the oldest app release (its `version`) this server works with. An app that says which release it is
  (`X-Kelo-App` header) and is older gets 426 {"code": "app_too_old"} (see MIN VERSION CONTRACT in docs/versioning.md).
- `min_server`: the oldest server release the apps built from this commit work with; the apps compile it in and compare it with
  the server's `X-Kelo-Server` header or /api/about.

The server finds the file next to the package (a source checkout or the image's /app), or at $KELO_VERSION_FILE.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

FILE_NAME = "version.json"
VERSION_RE = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,5})$")
PLATFORMS = ("android", "ios", "desktop", "web", "cli")
# "android/0.9.0+402400": platform / release version, optionally "+" the platform's own build number
APP_HEADER = "x-kelo-app"
SERVER_HEADER = "X-Kelo-Server"
_APP_RE = re.compile(r"^([a-z]{2,10})/(\d{1,4}\.\d{1,5}\.\d{1,6})(?:\+(\d{1,12}))?$")


def parse(text: str) -> tuple[int, int, int]:
    """'0.9.0' -> (0, 9, 0). Raises ValueError for anything else."""
    m = VERSION_RE.fullmatch(text.strip()) if isinstance(text, str) else None
    if not m:
        raise ValueError(f"not a release version (MAJOR.MINOR.PATCH): {text!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


@dataclass(frozen=True)
class Release:
    version: str
    build: int
    min_app: str
    min_server: str
    source: str = ""          # where it was read ("" = not found: a development fallback)

    @property
    def label(self) -> str:
        """'0.9.0 (build 1)'."""
        return f"{self.version} (build {self.build})"

    @property
    def header(self) -> str:
        """The X-Kelo-Server value: '0.9.0+1'."""
        return f"{self.version}+{self.build}"

    def public(self) -> dict[str, Any]:
        return {"version": self.version, "build": self.build, "min_app": self.min_app, "min_server": self.min_server}


def read(path: str | Path) -> Release:
    """Reads and checks one version.json. Raises ValueError with the reason."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{p}: not readable JSON ({e})") from None
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: not an object")
    version = raw.get("version")
    parse(version)
    build = raw.get("build")
    if isinstance(build, bool) or not isinstance(build, int) or build < 0:
        raise ValueError(f"{p}: build must be a whole number >= 0")
    min_app = raw.get("min_app", version)
    min_server = raw.get("min_server", version)
    parse(min_app)
    parse(min_server)
    if parse(min_server) > parse(version):
        raise ValueError(f"{p}: min_server {min_server} is newer than the release {version}")
    return Release(version, build, min_app, min_server, str(p))


def candidates() -> list[Path]:
    out = []
    if env := os.environ.get("KELO_VERSION_FILE", "").strip():
        out.append(Path(env))
    here = Path(__file__).resolve()
    out += [here.parents[3] / FILE_NAME, here.parents[1] / FILE_NAME, Path("/app") / FILE_NAME]
    return out


@lru_cache(maxsize=1)
def current() -> Release:
    """This server's release. Without the file (an odd install), the package's version with build 0."""
    for c in candidates():
        if c.is_file():
            return read(c)
    try:
        from importlib.metadata import version as pkg_version
        ver = pkg_version("assistant")
        parse(ver)
    except Exception:  # noqa: BLE001 - no metadata either
        ver = "0.0.0"
    return Release(ver, 0, ver, ver, "")


def parse_app_header(value: str | None) -> tuple[str, str, int | None] | None:
    """'android/0.9.0+402400' -> ('android', '0.9.0', 402400); None when absent or not in that form (then nothing is enforced)."""
    if not value:
        return None
    m = _APP_RE.fullmatch(value.strip())
    if not m or m.group(1) not in PLATFORMS:
        return None
    try:
        parse(m.group(2))
    except ValueError:
        return None
    return m.group(1), m.group(2), int(m.group(3)) if m.group(3) else None


def app_too_old(header: str | None, release: Release, min_builds: dict[str, int],
                skip_version: frozenset[str] = frozenset()) -> dict[str, Any] | None:
    """The 426 body for an app older than this server accepts, or None. Two rules, either one refuses:
    its release is older than `min_app`, or the operator's per-platform minimum build (ASSISTANT_MIN_APP_*) is above its build.
    `skip_version`: platforms whose `min_app` is above every released app (app_releases.py): that rule is not applied to them."""
    got = parse_app_header(header)
    if got is None:
        return None
    platform, ver, build = got
    old = platform not in skip_version and parse(ver) < parse(release.min_app)
    floor = min_builds.get(platform)
    if floor and build is not None and platform != "desktop" and build < floor:
        old = True
    if not old:
        return None
    return {"code": "app_too_old", "detail": "this app is older than this Kelo server works with; update the app",
            "min_app": release.min_app, "min_app_version": dict(min_builds), "server": release.version}
