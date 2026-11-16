"""The one place that asks which operating system this is, so a test can pretend to be another one."""

from __future__ import annotations

import sys


def is_windows() -> bool:
    """True on Windows. Every other supported system (Linux inside Docker, macOS) is POSIX."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """True on macOS."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """True on Linux (every Kelo Docker container)."""
    return sys.platform.startswith("linux")
