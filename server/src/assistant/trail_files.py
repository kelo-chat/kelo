"""The labels a file carries (docs/data-trail.md, "T3 as built"): one extended attribute, `user.kelo_trail`, holding JSON.

macOS: the `xattr` tool. Linux: setxattr. Windows: an NTFS stream next to the file (`<file>:kelo_trail`). `read` returns None for a file
without the attribute; `write` returns False when the filesystem cannot hold it (the caller then fails closed); `write(path, None)` removes it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ATTR = "user.kelo_trail"
MAX_BYTES = 8000


def read(path: Path) -> dict[str, Any] | None:
    raw = _get(Path(path))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return {"v": 0, "labels": [], "unreadable": True}   # something is there but not ours: the caller treats it as an outside label
    return data if isinstance(data, dict) else {"v": 0, "labels": [], "unreadable": True}


def write(path: Path, data: dict[str, Any] | None) -> bool:
    path = Path(path)
    try:
        if data is None:
            _del(path)
            return True
        blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(blob.encode("utf-8")) > MAX_BYTES:
            return False
        _set(path, blob)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _get(path: Path) -> str | None:
    try:
        if sys.platform == "win32":
            with open(f"{path}:kelo_trail", "r", encoding="utf-8") as f:
                return f.read()
        if sys.platform == "darwin":
            done = subprocess.run(["xattr", "-p", ATTR, str(path)], capture_output=True, text=True, timeout=10)
            return done.stdout if done.returncode == 0 else None
        return os.getxattr(path, ATTR).decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError, AttributeError):
        return None


def _set(path: Path, blob: str) -> None:
    if sys.platform == "win32":
        with open(f"{path}:kelo_trail", "w", encoding="utf-8") as f:
            f.write(blob)
    elif sys.platform == "darwin":
        subprocess.run(["xattr", "-w", ATTR, blob, str(path)], capture_output=True, check=True, timeout=10)
    else:
        os.setxattr(path, ATTR, blob.encode("utf-8"))


def _del(path: Path) -> None:
    if sys.platform == "win32":
        try:
            os.remove(f"{path}:kelo_trail")
        except FileNotFoundError:
            pass
    elif sys.platform == "darwin":
        subprocess.run(["xattr", "-d", ATTR, str(path)], capture_output=True, timeout=10)   # (a file without it: nothing to remove)
    else:
        try:
            os.removexattr(path, ATTR)
        except OSError:
            pass
