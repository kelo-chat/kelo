#!/usr/bin/env python3
"""Set Kelo's release version and build (version.json), for a release commit. It never tags, commits or pushes.

    python3 tools/release/bump.py 0.9.1                  # build = the current build + 1
    python3 tools/release/bump.py 0.9.1 --build 7        # an exact build number (must be higher than the current one)
    python3 tools/release/bump.py 0.9.1 --min-app 0.9.0 --min-server 0.9.0
    python3 tools/release/bump.py --check-tag v0.9.1     # exit 1 unless version.json says 0.9.1 (the release workflow runs this)

It writes version.json, the version in pyproject.toml and uv.lock (the project's own entry), renames the CHANGELOG.md heading
"## Unreleased" to "## 0.9.1 (build 7, 2026-11-10)" with a new empty "## Unreleased" above it, and prints the tag to create
(`git tag -a v0.9.1 -m ...`). See docs/versioning.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION_RE = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,5})$")


def key(v: str) -> tuple[int, int, int]:
    m = VERSION_RE.fullmatch(v)
    if not m:
        raise SystemExit(f"not a release version (MAJOR.MINOR.PATCH): {v!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bump(root: Path, version: str, build: int | None = None, min_app: str | None = None, min_server: str | None = None,
         today: dt.date | None = None) -> dict:
    vfile = root / "version.json"
    cur = json.loads(vfile.read_text())
    key(version)
    if key(version) < key(cur["version"]):
        raise SystemExit(f"{version} is older than the current {cur['version']}")
    new_build = cur["build"] + 1 if build is None else build
    if new_build <= cur["build"]:
        raise SystemExit(f"build {new_build} must be higher than the current {cur['build']}")
    data = {"version": version, "build": new_build, "min_app": min_app or cur.get("min_app", version),
            "min_server": min_server or cur.get("min_server", version)}
    for k in ("min_app", "min_server"):
        key(data[k])
    if key(data["min_server"]) > key(version):
        raise SystemExit("min_server cannot be newer than the release")
    vfile.write_text(json.dumps(data, indent=2) + "\n")

    py = root / "pyproject.toml"
    text = py.read_text()
    text, n = re.subn(r'(?m)^version = "[^"]*"$', f'version = "{version}"', text, count=1)
    if n != 1:
        raise SystemExit("pyproject.toml has no version line")
    py.write_text(text)

    lock = root / "uv.lock"
    if lock.exists():
        text = lock.read_text()
        text, n = re.subn(r'(name = "assistant"\nversion = ")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1)
        if n != 1:
            raise SystemExit("uv.lock has no entry for the assistant package")
        lock.write_text(text)

    log = root / "CHANGELOG.md"
    if log.exists():
        text = log.read_text()
        day = (today or dt.date.today()).isoformat()
        if "\n## Unreleased\n" not in text:
            raise SystemExit("CHANGELOG.md has no '## Unreleased' heading")
        text = text.replace("\n## Unreleased\n", f"\n## Unreleased\n\n## {version} (build {new_build}, {day})\n", 1)
        log.write_text(text)
    return data


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bump.py", description=__doc__.split("\n\n")[0])
    p.add_argument("version", nargs="?")
    p.add_argument("--build", type=int)
    p.add_argument("--min-app")
    p.add_argument("--min-server")
    p.add_argument("--check-tag", metavar="TAG")
    p.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.check_tag:
        want = json.loads((a.root / "version.json").read_text())["version"]
        got = a.check_tag.removeprefix("v").split("-", 1)[0]   # v0.9.1-beta.1 (the beta channel) is release 0.9.1
        if got != want:
            print(f"tag {a.check_tag} does not match version.json ({want}): run tools/release/bump.py {got} and commit first",
                  file=sys.stderr)
            return 1
        print(f"tag {a.check_tag} matches version.json")
        return 0
    if not a.version:
        p.error("give the new version (or --check-tag)")
    data = bump(a.root, a.version, a.build, a.min_app, a.min_server)
    print(f"version.json: {data['version']} build {data['build']} (min_app {data['min_app']}, min_server {data['min_server']})")
    print("Commit these files, then create the tag yourself:")
    print(f"  git tag -a v{data['version']} -m 'Kelo {data['version']} (build {data['build']})'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
