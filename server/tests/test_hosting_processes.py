"""hosting/processes.py: a started program leads a group and stop_group ends all of it. Real on POSIX; Windows through fakes."""

import asyncio
import os
import subprocess
import sys
import time
import types

import pytest

from assistant.hosting import operating_system, processes
from assistant.hosting.processes import ProcessLimits, start_group, start_group_async, stop_group

PARENT_WITH_CHILD = ("import subprocess, sys, time\n"
                     "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                     "print(child.pid, flush=True)\n"
                     "time.sleep(60)\n")


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until_gone(pid: int, seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not is_alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_stop_group_kills_the_program_and_its_child():
    process = start_group([sys.executable, "-c", PARENT_WITH_CHILD], stdout=subprocess.PIPE, text=True)
    child_pid = int(process.stdout.readline())
    assert is_alive(child_pid)
    stop_group(process)
    process.wait(timeout=5)
    assert wait_until_gone(child_pid)
    assert process.returncode == -9


async def test_the_async_start_leads_a_group_too():
    process = await start_group_async([sys.executable, "-c", PARENT_WITH_CHILD], stdout=asyncio.subprocess.PIPE)
    child_pid = int((await process.stdout.readline()).decode())
    stop_group(process)
    await asyncio.wait_for(process.wait(), 5)
    assert wait_until_gone(child_pid)


def test_the_program_is_its_own_group_leader():
    process = start_group([sys.executable, "-c", "import os; print(os.getpgid(0) == os.getpid())"], stdout=subprocess.PIPE, text=True)
    assert process.communicate()[0].strip() == "True"


def test_stopping_a_program_that_already_ended_is_safe():
    process = start_group([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    stop_group(process)
    stop_group(process)


def test_a_refused_group_kill_falls_back_to_the_process(monkeypatch):     # macOS: EPERM once the leader is a zombie
    killed = []
    process = types.SimpleNamespace(pid=4242, kill=lambda: killed.append("process"))

    def refused(pid, sig):
        raise PermissionError
    monkeypatch.setattr(os, "killpg", refused)
    stop_group(process)
    assert killed == ["process"]


def test_limits_are_applied_before_the_program_starts():
    program = ("import os, resource\nprint(resource.getrlimit(resource.RLIMIT_FSIZE)[0], resource.getrlimit(resource.RLIMIT_CORE)[0], "
               "oct(os.umask(0)))\n")
    limits = ProcessLimits(file_size_limit_bytes=3 * 1024 * 1024, allow_core_dumps=False, umask=0o077)
    process = start_group([sys.executable, "-c", program], stdout=subprocess.PIPE, text=True, limits=limits)
    assert process.communicate()[0].split() == [str(3 * 1024 * 1024), "0", "0o77"]


def test_no_limits_leave_the_program_alone():
    options = processes.group_options(None)
    assert options == {"start_new_session": True}


# ---- Windows, through fakes ----------------------------------------------------------------------------------------
class FakeKernel32:
    def __init__(self, job_ok=True, assign_ok=True):
        self.calls, self.job_ok, self.assign_ok = [], job_ok, assign_ok

    def CreateJobObjectW(self, attributes, name):
        self.calls.append(("CreateJobObjectW", attributes, name))
        return 777 if self.job_ok else 0

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("OpenProcess", access, inherit, pid))
        return 555

    def AssignProcessToJobObject(self, job, process):
        self.calls.append(("AssignProcessToJobObject", job, process))
        return 1 if self.assign_ok else 0

    def TerminateJobObject(self, job, exit_code):
        self.calls.append(("TerminateJobObject", job, exit_code))
        return 1

    def CloseHandle(self, handle):
        self.calls.append(("CloseHandle", handle))
        return 1


class FakePopen:
    made: list = []

    def __init__(self, argv, **options):
        self.argv, self.options, self.pid, self.killed = argv, options, 4321, False
        FakePopen.made.append(self)

    def kill(self):
        self.killed = True


@pytest.fixture
def windows(monkeypatch):
    kernel32 = FakeKernel32()
    FakePopen.made = []
    monkeypatch.setattr(operating_system, "is_windows", lambda: True)
    monkeypatch.setattr(processes, "_load_kernel32", lambda: kernel32)
    monkeypatch.setattr(processes.subprocess, "Popen", FakePopen)
    return kernel32


def test_windows_starts_a_new_process_group_and_puts_it_in_a_job(windows):
    process = start_group(["chrome.exe", "--x"], cwd="C:\\work")
    assert process.argv == ["chrome.exe", "--x"]
    assert process.options == {"creationflags": processes.CREATE_NEW_PROCESS_GROUP, "cwd": "C:\\work"}    # no start_new_session
    assert windows.calls == [("CreateJobObjectW", None, None),
                             ("OpenProcess", processes.PROCESS_SET_QUOTA | processes.PROCESS_TERMINATE, False, 4321),
                             ("AssignProcessToJobObject", 777, 555),
                             ("CloseHandle", 555)]


def test_windows_stop_group_terminates_the_job_and_closes_it(windows):
    process = start_group(["x.exe"])
    windows.calls.clear()
    stop_group(process)
    assert windows.calls == [("TerminateJobObject", 777, processes.JOB_TERMINATE_EXIT_CODE), ("CloseHandle", 777)]
    assert process.killed is False
    windows.calls.clear()
    stop_group(process)                                    # the job is gone: the process itself is killed
    assert windows.calls == [] and process.killed is True


def test_windows_without_a_job_still_stops_the_process(windows):
    windows.assign_ok = False
    process = start_group(["x.exe"])
    assert ("TerminateJobObject", 777, 1) in windows.calls and ("CloseHandle", 777) in windows.calls     # the empty job is dropped
    windows.calls.clear()
    stop_group(process)
    assert windows.calls == [] and process.killed is True


def test_windows_limits_do_not_break_the_start(windows):
    process = start_group(["x.exe"], limits=ProcessLimits(file_size_limit_bytes=1, allow_core_dumps=False, umask=0o077))
    assert "preexec_fn" not in process.options and "start_new_session" not in process.options


async def test_windows_async_start_uses_the_same_options_and_job(windows, monkeypatch):
    seen = {}

    async def fake_exec(*argv, **options):
        seen.update(argv=argv, options=options)
        return FakePopen(list(argv))
    monkeypatch.setattr(processes.asyncio, "create_subprocess_exec", fake_exec)
    process = await start_group_async(["agy.exe", "login"], cwd="C:\\t")
    assert seen["argv"] == ("agy.exe", "login")
    assert seen["options"] == {"creationflags": processes.CREATE_NEW_PROCESS_GROUP, "cwd": "C:\\t"}
    assert ("AssignProcessToJobObject", 777, 555) in windows.calls
    stop_group(process)
    assert ("TerminateJobObject", 777, 1) in windows.calls
