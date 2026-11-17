"""Starting a program together with everything it starts, and ending all of it at once.

POSIX: the program leads a new process group and `stop_group` kills the group. Windows: the program starts in a new process group
and is put into a Job object, and `stop_group` terminates the job (the children it already made go with it)."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import sys
import signal
import subprocess
import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import operating_system

CREATE_NEW_PROCESS_GROUP = 0x00000200
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
JOB_TERMINATE_EXIT_CODE = 1


@dataclass(frozen=True)
class ProcessLimits:
    """What a started program may not do. Each limit is enforced where the system has it: POSIX applies all of them to the program
    before it starts; Windows has no file-size limit or umask, so there the caller's own watcher stays the guard."""

    file_size_limit_bytes: int | None = None   # the biggest file the program may write
    memory_limit_bytes: int | None = None      # the most address space the program may map (POSIX RLIMIT_AS)
    allow_core_dumps: bool = True              # False: no core file when it crashes
    umask: int | None = None                   # permission bits files it creates must not have


def _apply_posix_limits(limits: ProcessLimits) -> None:
    """Runs in the child between fork and exec."""
    import resource
    if not limits.allow_core_dumps:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if limits.file_size_limit_bytes is not None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_size_limit_bytes, limits.file_size_limit_bytes))
    if limits.memory_limit_bytes is not None and sys.platform != "darwin":
        # macOS refuses to lower RLIMIT_AS ("current limit exceeds maximum limit", checked on a clean macOS 15 VM): there the
        # Supervisor's memory watchdog enforces the limit instead (docs/native-server.md, phase 1b-3)
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_limit_bytes, limits.memory_limit_bytes))
    if limits.umask is not None:
        os.umask(limits.umask)


def group_options(limits: ProcessLimits | None = None) -> dict[str, Any]:
    """The keyword arguments that make `subprocess.Popen` / `asyncio.create_subprocess_exec` lead a group of its own (with the limits)."""
    if operating_system.is_windows():
        return {"creationflags": CREATE_NEW_PROCESS_GROUP}
    options: dict[str, Any] = {"start_new_session": True}
    if limits is not None:
        options["preexec_fn"] = lambda: _apply_posix_limits(limits)
    return options


class _WindowsJob:
    """One Windows Job object holding a started program and its children."""

    def __init__(self, kernel32: Any) -> None:
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError("CreateJobObjectW failed")

    def add(self, process_id: int) -> None:
        process_handle = self._kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, process_id)
        if not process_handle:
            raise OSError(f"OpenProcess failed for process {process_id}")
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
                raise OSError(f"AssignProcessToJobObject failed for process {process_id}")
        finally:
            self._kernel32.CloseHandle(process_handle)

    def terminate(self) -> None:
        """End every process in the job, then drop the job."""
        handle, self._handle = self._handle, None
        if handle:
            self._kernel32.TerminateJobObject(handle, JOB_TERMINATE_EXIT_CODE)
            self._kernel32.CloseHandle(handle)


def _load_kernel32() -> Any:
    """kernel32 with the 64-bit handle types set (only ever called on Windows; tests replace it)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)   # type: ignore[attr-defined]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel32


_jobs: weakref.WeakKeyDictionary[Any, _WindowsJob] = weakref.WeakKeyDictionary()


def _put_in_job(process: Any) -> None:
    """Windows only: the group of a started program is its job. If the job cannot be made, `stop_group` falls back to the process."""
    with contextlib.suppress(OSError):
        job = _WindowsJob(_load_kernel32())
        try:
            job.add(process.pid)
        except OSError:
            job.terminate()
            raise
        _jobs[process] = job


def start_group(argv: Sequence[str], *, limits: ProcessLimits | None = None, **popen_options: Any) -> subprocess.Popen[Any]:
    """`subprocess.Popen(argv, ...)` as the leader of a new group; `stop_group(process)` ends the whole group."""
    process = subprocess.Popen(list(argv), **group_options(limits), **popen_options)
    if operating_system.is_windows():
        _put_in_job(process)
    return process


async def start_group_async(argv: Sequence[str], *, limits: ProcessLimits | None = None,
                            **exec_options: Any) -> asyncio.subprocess.Process:
    """`asyncio.create_subprocess_exec(*argv, ...)` as the leader of a new group; `stop_group(process)` ends the whole group."""
    process = await asyncio.create_subprocess_exec(*argv, **group_options(limits), **exec_options)
    if operating_system.is_windows():
        _put_in_job(process)
    return process


def stop_group(process: subprocess.Popen[Any] | asyncio.subprocess.Process) -> None:
    """Kill the program and everything it started, at once. Safe to call on a program that has already ended.

    It does not wait: the caller still calls `wait()` to reap the leader."""
    if operating_system.is_windows():
        job = _jobs.pop(process, None)
        if job is not None:
            job.terminate()
            return
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):   # macOS answers EPERM once the leader is a zombie
            pass
    with contextlib.suppress(ProcessLookupError):
        process.kill()
