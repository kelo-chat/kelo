#!/usr/bin/env python3
"""Android's assets/vnc (noVNC + Kelo's view.html / view.js / zoom.js) -> the iPhone's copy in KeloWork/Resources/vnc.

The page is edited only in the Android folder (it is the original); then run this tool so the iPhone copy is the same bytes.
The iPhone copy is not a symlink (SwiftPM resources cannot reliably follow a folder outside the package), and the server test
server/tests/test_vnc_page_copies.py fails when a copy has drifted.

Usage (from anywhere):
  python3 apps/ios/tools/sync_vnc.py            # make the copy exactly the original
  python3 apps/ios/tools/sync_vnc.py --check    # exit 1 and list what differs; write nothing
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "apps/android/kelo/app/src/main/assets/vnc"
COPY = ROOT / "apps/ios/KeloUI/Sources/KeloWork/Resources/vnc"
SKIP_NAMES = {".DS_Store"}


def files(folder: Path) -> set[str]:
    """Relative paths of the regular files under folder, without the Finder's junk."""
    if not folder.is_dir():
        return set()
    return {
        p.relative_to(folder).as_posix()
        for p in folder.rglob("*")
        if p.is_file() and p.name not in SKIP_NAMES
    }


def differences() -> tuple[list[str], list[str], list[str]]:
    """(differing, missing from the copy, extra in the copy), relative paths, sorted."""
    src, dst = files(SOURCE), files(COPY)
    differing = sorted(r for r in src & dst if not filecmp.cmp(SOURCE / r, COPY / r, shallow=False))
    missing = sorted(src - dst)
    extra = sorted(dst - src)
    return differing, missing, extra


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit(f"missing original: {SOURCE.relative_to(ROOT)}")
    if "--check" in sys.argv[1:]:
        differing, missing, extra = differences()
        for label, items in (("differs", differing), ("missing", missing), ("extra", extra)):
            for rel in items:
                print(f"{label}: {rel}")
        if differing or missing or extra:
            print("run: python3 apps/ios/tools/sync_vnc.py", file=sys.stderr)
            sys.exit(1)
        return
    for rel in files(COPY) - files(SOURCE):
        (COPY / rel).unlink()
        print(f"removed {rel}")
    for rel in sorted(files(SOURCE)):
        target = COPY / rel
        if target.is_file() and filecmp.cmp(SOURCE / rel, target, shallow=False):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / rel, target)
        print(f"copied {rel}")
    # drop folders left empty by removed files
    for folder in sorted((p for p in COPY.rglob("*") if p.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()


if __name__ == "__main__":
    main()
