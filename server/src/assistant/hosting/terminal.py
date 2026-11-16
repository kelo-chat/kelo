"""A pseudo-terminal: a program that only behaves when a person sits at a terminal (a login screen, a full-screen UI) is run on one,
and its output is read and its keystrokes are written from here. POSIX: a pty pair. Windows: ConPTY through pywinpty."""

from __future__ import annotations

import asyncio
import contextlib
import os
import struct
import threading
from collections.abc import Callable, Sequence
from typing import Any

from . import operating_system, processes

READ_CHUNK_BYTES = 65536


class Pty:
    """One program running on a terminal of `columns` x `rows` characters.

    Guarantees: the program leads a process group of its own (`close()` ends all of it); `close()` also stops reading and releases
    the terminal; output is delivered to the callback given to `on_output`, in order, on the event loop's thread."""

    def __init__(self, rows: int = 24, columns: int = 80) -> None:
        self.rows = rows
        self.columns = columns
        self._process: asyncio.subprocess.Process | None = None     # POSIX
        self._master: int | None = None                              # POSIX
        self._conpty: Any = None                                     # Windows (winpty.PtyProcess)
        self._reader_thread: threading.Thread | None = None
        self._is_closed = False

    async def spawn(self, argv: Sequence[str], *, cwd: str, env: dict[str, str]) -> None:
        """Start `argv` on the terminal."""
        if operating_system.is_windows():
            self._spawn_conpty(argv, cwd, env)
            return
        import fcntl
        import termios
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.columns, 0, 0))
        try:
            self._process = await processes.start_group_async(argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._master = master
        os.set_blocking(master, False)

    def _spawn_conpty(self, argv: Sequence[str], cwd: str, env: dict[str, str]) -> None:
        import winpty
        self._conpty = winpty.PtyProcess.spawn(list(argv), cwd=cwd, env=env, dimensions=(self.rows, self.columns))

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        """The asyncio process of the program on POSIX; None on Windows (ConPTY has no asyncio process) or before `spawn`."""
        return self._process

    @property
    def returncode(self) -> int | None:
        """The program's exit code, or None while it still runs (or was never started)."""
        if self._conpty is not None:
            return None if self._conpty.isalive() else int(self._conpty.exitstatus or 0)
        return None if self._process is None else self._process.returncode

    def on_output(self, callback: Callable[[bytes], None]) -> None:
        """Deliver what the program prints to `callback(bytes)`; an empty `bytes` means the terminal closed. Call after `spawn`."""
        loop = asyncio.get_running_loop()
        if self._conpty is not None:
            self._reader_thread = threading.Thread(target=self._read_conpty, args=(loop, callback), daemon=True)
            self._reader_thread.start()
            return
        master = self._master
        if master is None:
            raise RuntimeError("on_output() before spawn()")

        def readable() -> None:
            try:
                data = os.read(master, READ_CHUNK_BYTES)
            except BlockingIOError:
                return
            except OSError:
                data = b""
            if not data:
                with contextlib.suppress(Exception):
                    loop.remove_reader(master)
            callback(data)
        loop.add_reader(master, readable)

    def _read_conpty(self, loop: asyncio.AbstractEventLoop, callback: Callable[[bytes], None]) -> None:
        while not self._is_closed:
            try:
                text = self._conpty.read(READ_CHUNK_BYTES)
            except (EOFError, OSError):
                text = ""
            if loop.is_closed():
                return
            loop.call_soon_threadsafe(callback, text.encode("utf-8", "replace"))
            if not text:
                return

    def write(self, data: bytes) -> None:
        """Type `data` into the terminal."""
        if self._is_closed:
            return
        if self._conpty is not None:
            self._conpty.write(data.decode("utf-8", "replace"))
        elif self._master is not None:
            os.write(self._master, data)

    def close(self) -> None:
        """End the program and everything it started, stop reading, release the terminal. Safe to call twice."""
        self._is_closed = True
        if self._conpty is not None:
            with contextlib.suppress(Exception):
                self._conpty.terminate(force=True)
            return
        if self._process is not None and self._process.returncode is None:
            processes.stop_group(self._process)
        if self._master is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self._master)
            with contextlib.suppress(OSError):
                os.close(self._master)
            self._master = None
