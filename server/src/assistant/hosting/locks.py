"""A lock on a file that several programs share (flock on POSIX, msvcrt.locking on Windows)."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import TracebackType

from . import operating_system

POLL_SECONDS = 0.05


class FileLockTimeout(TimeoutError):
    """The lock was still held by another program when the wait ran out."""


class FileLock:
    """An exclusive lock on one file, held until `release()` (or until the program dies: the system drops it).

    Guarantees: while one FileLock holds the file, every other FileLock on the same path, in this or another process, fails
    `try_acquire()` and waits in `acquire()`. The lock file is created if missing (owner-only) and its content is never used.
    One FileLock object is one holder: it does not re-enter itself."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._descriptor: int | None = None

    @property
    def is_held(self) -> bool:
        """True while this object holds the lock."""
        return self._descriptor is not None

    def try_acquire(self) -> bool:
        """Take the lock if nobody holds it; True when it is now held, False (at once) when another holder has it."""
        if self._descriptor is not None:
            raise RuntimeError(f"{self.path} is already held by this lock object")
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self._lock_without_waiting(descriptor)
        except OSError:      # BlockingIOError on POSIX; Windows raises a plain OSError for a held region
            os.close(descriptor)
            return False
        self._descriptor = descriptor
        return True

    def acquire(self, timeout_seconds: float | None = None) -> None:
        """Wait for the lock (forever when `timeout_seconds` is None); FileLockTimeout when the wait runs out."""
        if timeout_seconds is None and not operating_system.is_windows():
            self._acquire_posix_blocking()           # the system wakes us the moment the holder lets go
            return
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while not self.try_acquire():
            if deadline is not None and time.monotonic() > deadline:
                raise FileLockTimeout(f"timed out waiting for the lock on {self.path}")
            time.sleep(POLL_SECONDS)

    def _acquire_posix_blocking(self) -> None:
        import fcntl
        if self._descriptor is not None:
            raise RuntimeError(f"{self.path} is already held by this lock object")
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    async def acquire_async(self, timeout_seconds: float | None = None) -> None:
        """Like `acquire` but waits with the event loop free, so other tasks keep running."""
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while not self.try_acquire():
            if deadline is not None and time.monotonic() > deadline:
                raise FileLockTimeout(f"timed out waiting for the lock on {self.path}")
            await asyncio.sleep(POLL_SECONDS)

    def release(self) -> None:
        """Let go of the lock (does nothing when not held)."""
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            if operating_system.is_windows():
                _windows_unlock(descriptor)
        finally:
            os.close(descriptor)        # closing also drops a POSIX flock

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.release()

    @staticmethod
    def _lock_without_waiting(descriptor: int) -> None:
        if operating_system.is_windows():
            _windows_lock(descriptor)
            return
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _windows_lock(descriptor: int) -> None:
    """Lock the first byte of the file without waiting (raises OSError when another holder has it)."""
    import msvcrt
    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)


def _windows_unlock(descriptor: int) -> None:
    import msvcrt
    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
