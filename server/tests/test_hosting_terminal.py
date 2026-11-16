"""hosting/terminal.py: Pty for real on POSIX (a pty pair); the Windows ConPTY branch through a fake pywinpty."""

import asyncio
import sys
import time
import types

import pytest

from assistant.hosting import operating_system
from assistant.hosting.terminal import Pty


async def until(test, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return True
        await asyncio.sleep(0.05)
    return test()


async def test_what_is_typed_is_echoed_back_by_the_terminal_and_the_program():
    pty = Pty(rows=10, columns=100)
    seen = bytearray()
    await pty.spawn(["cat"], cwd="/tmp", env={"PATH": "/usr/bin:/bin", "TERM": "xterm"})
    pty.on_output(seen.extend)
    try:
        assert pty.returncode is None
        pty.write(b"hello\n")
        assert await until(lambda: seen.count(b"hello") >= 2)        # once by the terminal's own echo, once printed by cat
    finally:
        pty.close()
    assert await until(lambda: pty.returncode is not None)


async def test_the_program_sees_a_terminal_of_the_asked_size():
    pty = Pty(rows=7, columns=90)
    seen = bytearray()
    await pty.spawn([sys.executable, "-c", "import os; print(tuple(os.get_terminal_size()))"], cwd="/tmp", env={"PATH": "/usr/bin:/bin"})
    pty.on_output(seen.extend)
    assert await until(lambda: b"(90, 7)" in seen)
    assert await until(lambda: pty.returncode == 0)
    pty.close()


async def test_close_ends_the_program_and_everything_it_started():
    pty = Pty()
    await pty.spawn(["sh", "-c", "sleep 60 & wait"], cwd="/tmp", env={"PATH": "/usr/bin:/bin"})
    process = pty.process
    pty.close()
    await asyncio.wait_for(process.wait(), 5)
    assert pty.returncode == -9
    pty.close()                                                       # twice is safe
    pty.write(b"ignored after close")


async def test_the_callback_gets_an_empty_chunk_when_the_terminal_closes():
    pty = Pty()
    chunks = []
    await pty.spawn(["true"], cwd="/tmp", env={"PATH": "/usr/bin:/bin"})
    pty.on_output(chunks.append)
    assert await until(lambda: chunks and chunks[-1] == b"")
    pty.close()


async def test_output_before_spawn_is_an_error():
    with pytest.raises(RuntimeError):
        Pty().on_output(lambda data: None)


# ---- Windows (ConPTY through pywinpty), through fakes --------------------------------------------------------------
class FakeConPty:
    def __init__(self):
        self.written, self.terminated, self.alive, self.exitstatus = [], [], True, None
        self.pending = ["Sign in: https://example.com/auth\r\n"]

    def isalive(self):
        return self.alive

    def read(self, size):
        if self.pending:
            return self.pending.pop(0)
        raise EOFError

    def write(self, text):
        self.written.append(text)

    def terminate(self, force=False):
        self.terminated.append(force)
        self.alive, self.exitstatus = False, 1


@pytest.fixture
def conpty(monkeypatch):
    fake, spawned = FakeConPty(), []

    def spawn(argv, cwd=None, env=None, dimensions=None):
        spawned.append((argv, cwd, env, dimensions))
        return fake
    monkeypatch.setitem(sys.modules, "winpty", types.SimpleNamespace(PtyProcess=types.SimpleNamespace(spawn=spawn)))
    monkeypatch.setattr(operating_system, "is_windows", lambda: True)
    return fake, spawned


async def test_windows_runs_the_program_on_a_conpty_of_the_asked_size(conpty):
    fake, spawned = conpty
    pty = Pty(rows=60, columns=4000)
    await pty.spawn(["agy.exe", "login"], cwd="C:\\t", env={"A": "1"})
    assert spawned == [(["agy.exe", "login"], "C:\\t", {"A": "1"}, (60, 4000))]
    assert pty.process is None and pty.returncode is None


async def test_windows_output_arrives_on_the_loop_and_ends_with_an_empty_chunk(conpty):
    pty = Pty()
    await pty.spawn(["agy.exe"], cwd="C:\\t", env={})
    chunks = []
    pty.on_output(chunks.append)
    assert await until(lambda: chunks == [b"Sign in: https://example.com/auth\r\n", b""])
    pty.close()


async def test_windows_write_exit_code_and_close(conpty):
    fake, _ = conpty
    pty = Pty()
    await pty.spawn(["agy.exe"], cwd="C:\\t", env={})
    pty.write("héllo\r".encode())
    assert fake.written == ["héllo\r"]
    pty.close()
    assert fake.terminated == [True] and pty.returncode == 1
    pty.write(b"after close")
    assert fake.written == ["héllo\r"]
