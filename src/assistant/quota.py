"""How much of this container's storage the assistant may fill, and how much memory and CPU the container was given.

Storage: Docker cannot cap a volume's size on every system (Mac and Windows cannot at all), so the assistant keeps to the cap
itself: everything it writes lives under one folder, and writes that would go past the cap are refused with a clear message
(chat keeps working). Memory and CPU are limited by Docker itself; they are only *read* here so the app can show them.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .i18n import t

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.I)
_UNIT = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def parse_size(text: str | int | None) -> int:
    """'5g' -> bytes. 0 (or empty / none / unlimited) means no limit. Raises ValueError for anything else."""
    if text is None or str(text).strip().lower() in ("", "0", "none", "unlimited", "off"):
        return 0
    m = _SIZE.match(str(text))
    if not m:
        raise ValueError(f"{text!r} is not a size (examples: 512m, 2g, 1.5g)")
    return int(float(m.group(1)) * _UNIT[m.group(2).lower()])


def fmt_size(n: int | float) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f}GB"
    if n >= 1024**2:
        v = n / 1024**2
        return f"{v:.1f}MB" if v < 10 else f"{v:.0f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{int(n)}B"


class QuotaError(Exception):
    """A write that would go past the storage cap. The message is safe to show the user and the agent."""


class Quota:
    def __init__(self, root: str | Path, limit: int = 0, *, ttl: float = 15.0):
        self.root, self.limit, self.ttl = Path(root), max(0, int(limit)), ttl
        self._cache: tuple[float, dict[str, int]] | None = None
        self.listeners: list[Any] = []   # called after each write or delete (storage_watch.py looks again at its next tick)

    def invalidate(self) -> None:
        self._cache = None

    def _sizes(self) -> dict[str, int]:
        if self._cache and time.monotonic() - self._cache[0] < self.ttl:
            return self._cache[1]
        parts: dict[str, int] = {}
        for entry in self._scan(self.root):
            rel = entry[0].relative_to(self.root).parts
            if rel and rel[0] == "downloads" and len(rel) > 1:   # the app builds the operator publishes (downloads.py), not the user's
                continue
            if rel and rel[0] == ".backups":                     # `assistant update` backups (updates.py): the operator's, not the user's
                continue
            key = (rel[1] if len(rel) > 2 and rel[0] == "home" else rel[0]) if rel else "other"
            parts[key] = parts.get(key, 0) + entry[1]
        self._cache = (time.monotonic(), parts)
        return parts

    @staticmethod
    def _scan(root: Path) -> list[tuple[Path, int]]:
        out: list[tuple[Path, int]] = []
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if e.is_symlink():
                                continue
                            if e.is_dir(follow_symlinks=False):
                                stack.append(Path(e.path))
                            elif e.is_file(follow_symlinks=False):
                                out.append((Path(e.path), e.stat(follow_symlinks=False).st_size))
                        except OSError:
                            continue
            except OSError:
                continue
        return out

    def used(self) -> int:
        return sum(self._sizes().values())

    def parts(self) -> dict[str, int]:
        """Bytes per area: the database, uploads, product images, notes, everything else. A `.podcasts` folder left from an
        older version (the feature was removed; its files stay on disk untouched) still takes room, so it counts under "other"."""
        names = {"assistant.db": "database", "uploads": "uploads", ".tryon": "images",
                 "notes": "notes", "memory": "notes", "reflection": "notes", ".history": "history", ".memory-passes": "history"}
        out: dict[str, int] = {}
        for key, n in self._sizes().items():
            label = names.get(key) or ("database" if key.startswith("assistant.db") else "other")
            out[label] = out.get(label, 0) + n
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def check(self, extra: int, replacing: int = 0) -> None:
        """Raise QuotaError if adding `extra` bytes (minus `replacing` that a rewrite frees) would pass the cap."""
        if not self.limit:
            return
        need = max(0, extra - replacing)
        used = self.used()
        if used + need > self.limit:
            self.invalidate()           # the numbers may be a few seconds old (something was deleted): look again before refusing
            used = self.used()
        if used + need > self.limit:
            raise QuotaError(t("Storage is full ({used} / {limit}). Delete uploads or images in Settings to make room.",
                               used=fmt_size(used), limit=fmt_size(self.limit)))

    def wrote(self) -> None:
        self.invalidate()
        for fn in self.listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001 - a listener never breaks the write that called it
                pass


# ---------------------------------------------------------------------------------------------- what Docker gave this container
def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def cgroup_limits(root: str | Path = "/sys/fs/cgroup") -> dict[str, Any]:
    """Memory in use / memory cap / CPU cap as the container sees them (cgroup v2, with a v1 fallback). Missing = None."""
    r = Path(root)
    used = limit = cpus = None
    cur, mx = _read(r / "memory.current"), _read(r / "memory.max")
    if cur is not None:
        used = int(cur)
        stat = _read(r / "memory.stat") or ""
        m = re.search(r"^inactive_file (\d+)$", stat, re.M)
        if m:                                           # file cache is given back under pressure: not "used" (same as `docker stats`)
            used = max(0, used - int(m.group(1)))
        limit = int(mx) if mx and mx != "max" else None
    else:                                               # cgroup v1
        cur, mx = _read(r / "memory/memory.usage_in_bytes"), _read(r / "memory/memory.limit_in_bytes")
        if cur is not None:
            used = int(cur)
            limit = int(mx) if mx and int(mx) < 1 << 60 else None
    cpu = _read(r / "cpu.max")
    if cpu:
        quota, _, period = cpu.partition(" ")
        if quota != "max" and period.isdigit() and int(period) > 0:
            cpus = round(int(quota) / int(period), 2)
    else:
        q, p = _read(r / "cpu/cpu.cfs_quota_us"), _read(r / "cpu/cpu.cfs_period_us")
        if q and p and int(q) > 0 and int(p) > 0:
            cpus = round(int(q) / int(p), 2)
    return {"memory_used": used, "memory_limit": limit, "cpus": cpus}


def host_capacity(meminfo: str | Path = "/proc/meminfo") -> dict[str, int | None]:
    """What the computer behind this container has: its RAM, and the CPUs this container may run on.
    With no limit set these are the real ceiling, so the app shows them instead of "no limit"."""
    ram = None
    try:
        for line in Path(meminfo).read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count()
    return {"memory": ram, "cpus": cpus}
