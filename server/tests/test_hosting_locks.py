"""hosting/locks.py: FileLock for real on POSIX (flock), and the Windows branch (msvcrt.locking) through a fake msvcrt."""

import asyncio
import os
import subprocess
import sys
import threading
import time
import types

import pytest

from assistant.hosting import operating_system
from assistant.hosting.locks import FileLock, FileLockTimeout


def test_a_second_holder_is_refused_while_the_first_holds(tmp_path):
    first, second = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    assert first.try_acquire() and first.is_held
    assert second.try_acquire() is False and not second.is_held
    first.release()
    assert second.try_acquire()
    second.release()


def test_the_lock_file_is_made_owner_only(tmp_path):
    lock = FileLock(tmp_path / "x.lock")
    with lock:
        assert (os.stat(tmp_path / "x.lock").st_mode & 0o777) == 0o600


def test_another_process_is_blocked_until_release(tmp_path):
    path = tmp_path / "x.lock"
    program = ("import sys\nfrom assistant.hosting.locks import FileLock\n"
               "sys.exit(0 if FileLock(sys.argv[1]).try_acquire() else 3)\n")
    with FileLock(path):
        assert subprocess.run([sys.executable, "-c", program, str(path)]).returncode == 3
    assert subprocess.run([sys.executable, "-c", program, str(path)]).returncode == 0


def test_acquire_waits_for_the_holder_and_gets_the_lock(tmp_path):
    holder, waiter = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    holder.acquire()
    threading.Timer(0.3, holder.release).start()
    started = time.monotonic()
    waiter.acquire()
    assert waiter.is_held and time.monotonic() - started >= 0.2
    waiter.release()


def test_acquire_gives_up_after_the_timeout(tmp_path):
    holder = FileLock(tmp_path / "x.lock")
    holder.acquire()
    with pytest.raises(FileLockTimeout):
        FileLock(tmp_path / "x.lock").acquire(timeout_seconds=0.2)
    holder.release()


async def test_async_acquire_keeps_the_event_loop_free(tmp_path):
    holder = FileLock(tmp_path / "x.lock")
    holder.acquire()
    ticks = []

    async def tick():
        for _ in range(5):
            ticks.append(1)
            await asyncio.sleep(0.02)
        holder.release()

    ticker = asyncio.create_task(tick())
    waiter = FileLock(tmp_path / "x.lock")
    await waiter.acquire_async(timeout_seconds=5)
    await ticker
    assert waiter.is_held and len(ticks) == 5
    with pytest.raises(FileLockTimeout):                       # now the waiter holds it, so a third one gives up
        await FileLock(tmp_path / "x.lock").acquire_async(timeout_seconds=0.1)
    waiter.release()


def test_one_object_does_not_hold_twice_and_release_is_safe_twice(tmp_path):
    lock = FileLock(tmp_path / "x.lock")
    lock.acquire()
    with pytest.raises(RuntimeError):
        lock.try_acquire()
    lock.release()
    lock.release()
    assert not lock.is_held


class FakeMsvcrt(types.SimpleNamespace):
    """msvcrt.locking as Windows has it: one holder per path at a time, OSError for the next."""

    def __init__(self):
        super().__init__(LK_NBLCK=2, LK_UNLCK=0, calls=[], held=set())

    def locking(self, descriptor, mode, size):
        path = os.fstat(descriptor).st_ino
        self.calls.append((mode, size, os.lseek(descriptor, 0, os.SEEK_CUR)))
        if mode == self.LK_NBLCK:
            if path in self.held:
                raise OSError(13, "locked")
            self.held.add(path)
        else:
            self.held.discard(path)


def test_windows_locks_the_first_byte_without_waiting_and_unlocks_on_release(tmp_path, monkeypatch):
    fake = FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(operating_system, "is_windows", lambda: True)
    first, second = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    assert first.try_acquire()
    assert second.try_acquire() is False
    first.release()
    assert fake.calls == [(2, 1, 0), (2, 1, 0), (0, 1, 0)]       # lock, refused lock, unlock: always the first byte
    assert second.try_acquire()
    second.release()


def test_windows_acquire_polls_because_msvcrt_has_no_unbounded_wait(tmp_path, monkeypatch):
    fake = FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(operating_system, "is_windows", lambda: True)
    holder = FileLock(tmp_path / "x.lock")
    holder.acquire()
    threading.Timer(0.2, holder.release).start()
    waiter = FileLock(tmp_path / "x.lock")
    waiter.acquire()
    assert waiter.is_held
    with pytest.raises(FileLockTimeout):
        FileLock(tmp_path / "x.lock").acquire(timeout_seconds=0.1)
    waiter.release()
