#!/usr/bin/env python3
"""Writes release.json, the manifest of a Kelo server release (see src/assistant/release.py), in the release workflow.

  python3 tools/release_manifest.py --version 0.5.0 --digests-dir digests/ [--registry ghcr.io/kelo-chat] [--channel stable]
      [--min-app android=402400 --min-app ios=57 --min-app desktop=1.0.3] [--notes-dir docs/release-notes/0.5.0] [--notes-file en=notes.txt]
      [--date 2026-11-09T00:00:00Z] --out release.json

--digests-dir holds one file per image, named after the component (server, workroom, updater; the CLI sidecars are never published), each containing
the pushed multi-arch digest (sha256:...). --notes-dir holds <lang>.md files (en.md, ko.md, ...). Only the standard library is used.
The manifest is checked with the same code every server uses before it is written.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant.release import COMPONENTS, DEFAULT_CHANNEL, DEFAULT_REGISTRY, ReleaseError, build_manifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", required=True)
    p.add_argument("--digests-dir", required=True, type=Path)
    p.add_argument("--registry", default=DEFAULT_REGISTRY)
    p.add_argument("--channel", default=DEFAULT_CHANNEL)
    p.add_argument("--min-app", action="append", default=[], metavar="PLATFORM=VERSION")
    p.add_argument("--notes-dir", type=Path)
    p.add_argument("--notes-file", action="append", default=[], metavar="LANG=FILE")
    p.add_argument("--compose-level", type=int, default=None, help="default: x-kelo-compose-level of compose.yml")
    p.add_argument("--date", default="")
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args(argv)

    digests = {}
    for comp in COMPONENTS:
        f = a.digests_dir / comp
        if f.is_file():
            digests[comp] = f.read_text().strip()
    min_app = dict(x.split("=", 1) for x in a.min_app if "=" in x and x.split("=", 1)[1])
    notes: dict[str, str] = {}
    if a.notes_dir and a.notes_dir.is_dir():
        for f in sorted(a.notes_dir.glob("*.md")):
            notes[f.stem] = f.read_text(encoding="utf-8").strip()
    for x in a.notes_file:
        lang, _, path = x.partition("=")
        text = Path(path).read_text(encoding="utf-8").strip() if path and Path(path).is_file() else ""
        if text:
            notes[lang] = text
    from assistant.app_releases import AppReleases
    problems = AppReleases.load().check_manifest(min_app)
    if problems:   # a minimum app version may never be above an app people can install (docs/versioning.md)
        print("error: min_app names an app version that was never released:\n  " + "\n  ".join(p.message for p in problems), file=sys.stderr)
        return 1
    level = a.compose_level
    if level is None:
        from assistant.release import compose_level
        level = compose_level((Path(__file__).resolve().parents[1] / "compose.yml").read_text())
    date = a.date or datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        manifest = build_manifest(a.version, digests, registry=a.registry, channel=a.channel, date=date, min_app=min_app,
                                  notes={k: v for k, v in notes.items() if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", k)},
                                  compose_level=level)
    except ReleaseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    a.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {a.out}: version {manifest['version']}, {len(manifest['images'])} images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
