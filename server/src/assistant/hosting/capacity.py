"""What this machine has: its memory and the processors this program may run on (the same answer on every operating system)."""

from __future__ import annotations

import os


def machine_memory() -> int | None:
    """Total physical memory of the machine in bytes, or None when it cannot be read.

    On Linux psutil reads MemTotal from /proc/meminfo, which is what the server read itself before, so the number in a container
    is unchanged. On macOS and Windows psutil asks the system."""
    try:
        import psutil
        total = int(psutil.virtual_memory().total)
    except (ImportError, OSError, ValueError, AttributeError):
        return None
    return total if total > 0 else None


def machine_cpu_count() -> int | None:
    """How many processors this program may run on: the scheduler's affinity set where the system has one (Linux, so a container
    pinned to some cores reports those), else every processor of the machine."""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count()
