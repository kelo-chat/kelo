"""The record of app versions that were really released to people, and the one rule built on it (docs/versioning.md,
"Minimum app version rule"): a minimum app version is never a version nobody can install.

If a server demanded an app version that no phone can get, every phone would show "update required" with no way out. So every
place that sets a minimum (version.json `min_app`, the operator's ASSISTANT_MIN_APP_<PLATFORM>, a server release manifest's
`min_app`) is checked against `releases/apps.json`:

    {"android": [{"version": "0.9.3", "build": 409159, "channel": "download", "date": "2026-11-13"}], "ios": [...], "desktop": [...]}

An app version counts as released only when ordinary people can install it: a store build that is live (Google Play production
or open testing, App Store "Ready for Distribution", Microsoft Store published) or a file offered at kelo.chat/download
(`assistant publish-app`). TestFlight, internal testing, a build on a developer's machine and a draft submission do not count,
and nothing here ever marks a store build live by itself: the operator records it (`assistant release-record`) when it is live.

`version` is the release the app reports in `X-Kelo-App` (compared with version.parse, the comparator VersionMiddleware uses);
`build` is the platform's own number (Android versionCode, iOS CFBundleVersion, desktop major*100000000 + minor*100000 + patch).
A minimum of 0.0.0 demands nothing, so it is never a problem.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any

from . import version as release_version

log = logging.getLogger("assistant.app_releases")

FILE = Path("releases") / "apps.json"
PLATFORMS = ("android", "ios", "desktop")
CHANNELS = ("download", "play", "appstore", "msstore")   # kelo.chat/download, Google Play, App Store, Microsoft Store
_NONE = (0, 0, 0)
_MAX_BUILD = 2**63 - 1
RECORD_HINT = "assistant release-record <platform> <version> <build> <channel>"


class RecordError(ValueError):
    """An entry or the record file is not valid; the message says why (never a secret)."""


@dataclass(frozen=True, order=True)
class Release:
    """One app release that people can install. Orders by version, then build."""
    version_key: tuple[int, int, int]
    build: int
    version: str
    channel: str
    date: str

    @classmethod
    def make(cls, version: Any, build: Any, channel: Any, date: Any) -> Release:
        """Checks every field. Raises RecordError."""
        try:
            key = release_version.parse(version)
        except ValueError:
            raise RecordError(f"{version!r} is not a release version (MAJOR.MINOR.PATCH, for example 0.9.3)") from None
        if isinstance(build, bool) or not isinstance(build, int) or not 0 < build <= _MAX_BUILD:
            raise RecordError(f"the build must be a whole number above 0 (the platform's own build number), not {build!r}")
        if channel not in CHANNELS:
            raise RecordError(f"the channel must be one of {', '.join(CHANNELS)}, not {channel!r}")
        try:
            day = _date.fromisoformat(date) if isinstance(date, str) and len(date) == 10 else None
        except ValueError:
            day = None
        if day is None:
            raise RecordError(f"the date must be YYYY-MM-DD, not {date!r}")
        return cls(key, build, version.strip(), channel, day.isoformat())

    def to_json(self) -> dict[str, Any]:
        return {"version": self.version, "build": self.build, "channel": self.channel, "date": self.date}

    @property
    def label(self) -> str:
        return f"{self.version} (build {self.build})"


@dataclass(frozen=True)
class Problem:
    """One platform whose minimum is above anything people can install."""
    platform: str
    wanted: str                # the minimum as written: "9.9.9" or "build 405000"
    newest: Release | None     # the newest released app of that platform (None: none released at all)

    @property
    def message(self) -> str:
        if self.newest is None:
            return f"{self.platform}: minimum {self.wanted}, but no {self.platform} app has been released (record one with `{RECORD_HINT}`)"
        return (f"{self.platform}: minimum {self.wanted} is above the newest released {self.platform} app, {self.newest.label}; "
                "a minimum may only name a version people can already install")


@dataclass(frozen=True)
class Enforcement:
    """What a server may really enforce: the build minimums that passed, the platforms whose version.json minimum is skipped,
    and every platform that was ignored (for /api/about `min_app_ignored`)."""
    min_builds: dict[str, int]
    skip_version: frozenset[str]
    ignored: tuple[str, ...]
    problems: tuple[Problem, ...] = ()


def candidates() -> list[Path]:
    out = []
    if env := os.environ.get("KELO_APP_RELEASES", "").strip():
        out.append(Path(env))
    here = Path(__file__).resolve()
    out += [here.parents[3] / FILE, here.parents[1] / FILE, Path("/app") / FILE]
    return out


class AppReleases:
    """The loaded record. Pure checks; only `add` / `save` write."""

    def __init__(self, entries: dict[str, list[Release]] | None = None, path: Path | None = None):
        self.entries: dict[str, list[Release]] = {p: sorted(entries.get(p, ())) if entries else [] for p in PLATFORMS}
        self.path = path

    # ------------------------------------------------------------------------------------------------------------ loading
    @classmethod
    def parse(cls, raw: Any, path: Path | None = None) -> AppReleases:
        if not isinstance(raw, dict) or set(raw) - set(PLATFORMS):
            raise RecordError(f"the record must be an object with the platforms {', '.join(PLATFORMS)}")
        entries: dict[str, list[Release]] = {}
        for platform, rows in raw.items():
            if not isinstance(rows, list):
                raise RecordError(f"{platform}: not a list")
            seen: set[tuple[str, int]] = set()
            out = []
            for row in rows:
                if not isinstance(row, dict) or set(row) - {"version", "build", "channel", "date"}:
                    raise RecordError(f"{platform}: an entry is not {{version, build, channel, date}}")
                rel = Release.make(row.get("version"), row.get("build"), row.get("channel"), row.get("date"))
                if (rel.version, rel.build) in seen:
                    raise RecordError(f"{platform}: {rel.label} is listed twice")
                seen.add((rel.version, rel.build))
                out.append(rel)
            entries[platform] = out
        return cls(entries, path)

    @classmethod
    def read(cls, path: str | Path) -> AppReleases:
        p = Path(path)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RecordError(f"{p}: not readable JSON ({type(e).__name__})") from None
        try:
            return cls.parse(raw, p)
        except RecordError as e:
            raise RecordError(f"{p}: {e}") from None

    @classmethod
    def load(cls) -> AppReleases:
        """The record shipped with this server (a checkout or the image's /app). Without a readable record nothing counts as released,
        so no minimum is enforced: that can only let an old app in, never lock one out."""
        for c in candidates():
            if c.is_file():
                try:
                    return cls.read(c)
                except RecordError as e:
                    log.error("app release record ignored: %s", e)
                    return cls()
        log.error("app release record not found (%s): no minimum app version will be enforced", FILE)
        return cls()

    # ------------------------------------------------------------------------------------------------------------- queries
    def latest(self, platform: str) -> Release | None:
        """The newest released app of a platform, by version, then build."""
        rows = self.entries.get(platform) or []
        return max(rows) if rows else None

    def newest_build(self, platform: str) -> Release | None:
        """The released app with the highest build number (builds rise with versions, but the check does not rely on it)."""
        rows = self.entries.get(platform) or []
        return max(rows, key=lambda r: (r.build, r.version_key)) if rows else None

    def check_min(self, min_version: str | None, min_builds: dict[str, int] | None = None) -> list[Problem]:
        """Pure. Every platform whose minimum is above what people can install, or that has a minimum but no release at all:
        `min_version` (version.json `min_app`, one version for every platform; None / 0.0.0 = none) and `min_builds`
        (ASSISTANT_MIN_APP_*: a build per platform). Raises ValueError when `min_version` is not a version."""
        problems: list[Problem] = []
        if min_version and release_version.parse(min_version) > _NONE:
            want = release_version.parse(min_version)
            for platform in PLATFORMS:
                newest = self.latest(platform)
                if newest is None or want > newest.version_key:
                    problems.append(Problem(platform, min_version.strip(), newest))
        for platform, build in sorted((min_builds or {}).items()):
            if not build:
                continue
            top = self.newest_build(platform)
            if top is None or build > top.build:
                problems.append(Problem(platform, f"build {build}", top))
        return problems

    def check_manifest(self, min_app: dict[str, str] | None) -> list[Problem]:
        """The `min_app` of a server release manifest: per platform a version ("1.0.3") or a whole build number ("402400").
        Anything else is a problem too, because it cannot be shown to be released."""
        problems: list[Problem] = []
        for platform, text in sorted((min_app or {}).items()):
            text = str(text).strip()
            if text.isdigit():
                problems += self.check_min(None, {platform: int(text)})
                continue
            try:
                want = release_version.parse(text)
            except ValueError:
                problems.append(Problem(platform, f"{text!r} (not a version or a build number)", self.latest(platform)))
                continue
            if want <= _NONE:
                continue
            newest = self.latest(platform) if platform in PLATFORMS else None
            if newest is None or want > newest.version_key:
                problems.append(Problem(platform, text, newest))
        return problems

    def enforceable(self, min_version: str | None, min_builds: dict[str, int] | None = None) -> Enforcement:
        """What a running server may enforce. A platform whose minimum fails the check is ignored (never locks users out)."""
        try:
            by_version = self.check_min(min_version)
        except ValueError:
            by_version = []   # version.json is checked when it is read; an unreadable one enforces nothing here either
        by_build = self.check_min(None, min_builds)
        bad = {p.platform for p in by_build}
        kept = {p: b for p, b in (min_builds or {}).items() if p not in bad}
        skip = frozenset(p.platform for p in by_version)
        return Enforcement(kept, skip, tuple(sorted(bad | skip)), tuple(by_version + by_build))

    # ------------------------------------------------------------------------------------------------------------- writing
    def add(self, platform: str, version: Any, build: Any, channel: Any, date: Any = None) -> bool:
        """Adds a release that is live now. Returns False when exactly this one is already recorded; a different channel for the
        same version and build is the same release and is not added twice either. Raises RecordError for anything invalid."""
        if platform not in PLATFORMS:
            raise RecordError(f"unknown platform {platform!r}: {', '.join(PLATFORMS)}")
        if isinstance(build, str):
            if not build.isdigit():
                raise RecordError(f"the build must be a whole number, not {build!r}")
            build = int(build)
        rel = Release.make(version, build, channel, date or _date.today().isoformat())
        if any(r.version == rel.version and r.build == rel.build for r in self.entries[platform]):
            return False
        self.entries[platform] = sorted([*self.entries[platform], rel])
        return True

    def to_json(self) -> dict[str, Any]:
        return {p: [r.to_json() for r in rows] for p, rows in self.entries.items()}

    def dumps(self) -> str:
        """One entry per line, so a new release is a one-line change in git."""
        parts = []
        for platform in PLATFORMS:
            rows = self.entries[platform]
            body = ",\n".join(f"    {json.dumps(r.to_json())}" for r in rows)
            parts.append(f'  "{platform}": [\n{body}\n  ]' if rows else f'  "{platform}": []')
        return "{\n" + ",\n".join(parts) + "\n}\n"

    def save(self, path: Path | None = None) -> Path:
        target = path or self.path
        if target is None:
            raise RecordError("no file to write")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(self.dumps(), encoding="utf-8")
        tmp.replace(target)
        return target


_reported: set[str] = set()


def report(enforcement: Enforcement, where: str) -> None:
    """Logs each ignored minimum once per process (an error: someone set a minimum that nobody can meet)."""
    for p in enforcement.problems:
        line = f"{where}: {p.message}. The minimum for {p.platform} is IGNORED so no user is locked out."
        if line not in _reported:
            _reported.add(line)
            log.error(line)


def line_for(platform: str, version: str, build: int | str, channel: str, date: str | None = None) -> str:
    """The exact JSON entry to add by hand (publish-app on a copy without the repo)."""
    return json.dumps({"version": version, "build": int(build), "channel": channel, "date": date or _date.today().isoformat()})


def run_record(args: Any, out: Any = print, path: Path | None = None) -> int:
    """`assistant release-record <platform> <version> <build> <channel> [--date YYYY-MM-DD] [--file PATH]`: records an app release that is
    live now (people can install it) in releases/apps.json. Run it in a checkout of the source, then commit the file."""
    target = Path(args.file) if getattr(args, "file", None) else path or next((c for c in candidates()[:-1] if c.is_file()), None)
    if target is None:
        out(f"error: releases/apps.json was not found here; run this in a checkout of the source (or pass --file). The entry is: "
            f"{line_for(args.platform, args.version, args.build, args.channel, getattr(args, 'date', None))}"
            if _valid(args) else "error: releases/apps.json was not found here; run this in a checkout of the source (or pass --file)")
        return 1
    try:
        record = AppReleases.read(target)
        added = record.add(args.platform, args.version, args.build, args.channel, getattr(args, "date", None))
    except RecordError as e:
        out(f"error: {e}")
        return 1
    if not added:
        out(f"{args.platform} {args.version} (build {args.build}) is already recorded in {target}; nothing changed.")
        return 0
    record.save(target)
    out(f"Recorded {args.platform} {args.version} (build {args.build}, {args.channel}) in {target}. Commit it: from now on a minimum app "
        "version may be as high as this release.")
    return 0


def _valid(args: Any) -> bool:
    try:
        AppReleases().add(args.platform, args.version, args.build, args.channel, getattr(args, "date", None))
        return True
    except RecordError:
        return False

