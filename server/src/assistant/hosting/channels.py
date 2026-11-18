"""A private way for two programs on one machine to talk: a Unix socket file on POSIX, a named pipe on Windows."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import socketserver
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import operating_system

WORKROOM_LINK_ENV = "WORKROOM_LINK"      # "tcp": the work room is reached over a private network link instead of a socket in a shared folder
WORKROOM_AGENT_PORT = 9710               # the work room's agent, on the work room's side of the link
WORKROOM_PROXY_PORT = 9711               # the server's install proxy, on the server's side of the link
WORKROOM_HOST = "workroom"               # the names the two containers have for each other on the link
SERVER_HOST = "server"
PIPE_PREFIX = "\\\\.\\pipe\\"
PIPE_NAME_PREFIX = "kelo-"
GROUP_READ_WRITE = 0o660        # the server reaches a sibling's socket through a shared group; nobody else mounts the folder


class ChannelUnsupported(NotImplementedError):
    """This operation has no implementation on this operating system yet."""


if hasattr(socketserver, "UnixStreamServer"):
    class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        """A Unix-socket server that handles each connection in its own thread."""

        daemon_threads = True
else:                                                   # pragma: no cover - Windows
    ThreadedUnixServer = None   # type: ignore[assignment,misc]


class LocalChannel:
    """One named meeting point on this machine, with the same calls on every operating system.

    On POSIX it is the socket file `<folder>/<file_name>` (default `<name>.sock`); on Windows it is the named pipe
    `\\\\.\\pipe\\kelo-<name>`. Guarantees: `address` never changes for the life of the object; `serve_async` and `make_server`
    replace a stale leftover of a crashed run; the file is reachable by the owner and group only."""

    def __init__(self, name: str, folder: str | Path, *, file_name: str | None = None) -> None:
        self.name = name
        self.folder = Path(folder)
        self.file_name = file_name or f"{name}.sock"

    @classmethod
    def at_path(cls, socket_path: str | Path) -> LocalChannel:
        """The channel whose POSIX socket file is `socket_path` (its name, e.g. "agent", comes from the file name)."""
        path = Path(socket_path)
        return cls(path.stem, path.parent, file_name=path.name)

    @property
    def pipe_name(self) -> str:
        """The Windows named pipe of this channel."""
        return f"{PIPE_PREFIX}{PIPE_NAME_PREFIX}{self.name}"

    @property
    def socket_path(self) -> Path:
        """The POSIX socket file of this channel."""
        return self.folder / self.file_name

    @property
    def address(self) -> str:
        """What a client connects to: the socket file's path, or the pipe's name."""
        return self.pipe_name if operating_system.is_windows() else str(self.socket_path)

    def is_present(self) -> bool:
        """True when something has made the socket file / pipe (it may still refuse connections)."""
        return os.path.exists(self.address)

    def remove_leftover(self) -> None:
        """Delete a socket file a crashed run left behind (a named pipe disappears by itself)."""
        if not operating_system.is_windows():
            self.socket_path.unlink(missing_ok=True)

    def restrict_to_group(self) -> None:
        """Make the socket file readable and writable for its owner and group only (a pipe is limited by its own security)."""
        if not operating_system.is_windows():
            self.socket_path.chmod(GROUP_READ_WRITE)

    # ---- client side
    def connect(self, timeout_seconds: float | None = None) -> Any:
        """A connected, blocking client with `sendall`, `recv`, `settimeout` and `close`. OSError when nobody listens."""
        if operating_system.is_windows():
            return _PipeClient(self.pipe_name, timeout_seconds)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout_seconds)
        try:
            client.connect(str(self.socket_path))
        except OSError:
            client.close()
            raise
        return client

    def make_http_transport(self) -> Any:
        """An httpx transport that sends every request through this channel (the client says `http://<anything>/path`)."""
        if operating_system.is_windows():
            raise ChannelUnsupported("httpx has no named-pipe transport yet (arrives with the Windows host)")
        import httpx
        return httpx.AsyncHTTPTransport(uds=str(self.socket_path))

    # ---- server side
    def make_server(self, handler_class: type[socketserver.BaseRequestHandler]) -> socketserver.BaseServer:
        """A threaded server that answers connections on this channel with `handler_class`; the group may use the socket."""
        if operating_system.is_windows():
            raise ChannelUnsupported("a named-pipe server arrives with the Windows host")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.remove_leftover()
        server = ThreadedUnixServer(str(self.socket_path), handler_class)
        self.restrict_to_group()
        return server

    async def serve_async(self, on_connection: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any]) -> asyncio.AbstractServer:
        """Start an asyncio server on this channel (a Unix socket, or a pipe on the Proactor loop); the group may use the socket."""
        if operating_system.is_windows():
            return await _serve_pipe(self.pipe_name, on_connection)
        self.remove_leftover()
        server = await asyncio.start_unix_server(on_connection, path=str(self.socket_path))
        self.restrict_to_group()
        return server


class TcpChannel(LocalChannel):
    """The same meeting point over TCP, for a host whose containers cannot share a socket file
    (a socket on a folder shared with the host cannot be chmodded, and another container cannot connect to it).

    Safe only on a network that holds nothing but the two containers and has no way out (`workroom-link`, internal). The side that
    listens binds every address of its container; the other side connects to `host`. Same calls as LocalChannel."""

    BIND_ADDRESS = "0.0.0.0"

    def __init__(self, name: str, host: str, port: int) -> None:
        super().__init__(name, Path("/"), file_name=f"{name}.tcp")
        self.host, self.port = host, port

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def is_present(self) -> bool:
        return True   # nothing to look for: a refused connection says the other side is not up

    def remove_leftover(self) -> None:
        return None

    def restrict_to_group(self) -> None:
        return None

    def connect(self, timeout_seconds: float | None = None) -> Any:
        return socket.create_connection((self.host, self.port), timeout=timeout_seconds)

    def make_http_transport(self) -> Any:
        import httpx
        host, port = self.host, self.port

        class _Transport(httpx.AsyncHTTPTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                request.url = request.url.copy_with(host=host, port=port)
                return await super().handle_async_request(request)

        return _Transport()

    def make_server(self, handler_class: type[socketserver.BaseRequestHandler]) -> socketserver.BaseServer:
        class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True

        return _Server((self.BIND_ADDRESS, self.port), handler_class)

    async def serve_async(self, on_connection: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any]) -> asyncio.AbstractServer:
        return await asyncio.start_server(on_connection, self.BIND_ADDRESS, self.port)


def tcp_link_on(environ: Any = None) -> bool:
    """True when the work room is reached over the network link (WORKROOM_LINK=tcp)."""
    return (environ if environ is not None else os.environ).get(WORKROOM_LINK_ENV, "").strip().lower() == "tcp"


class _PipeClient:
    """A named-pipe client that looks like a socket for the three calls the callers make."""

    def __init__(self, pipe_name: str, timeout_seconds: float | None) -> None:
        self._timeout_seconds = timeout_seconds
        deadline = time.monotonic() + (timeout_seconds or 0)
        while True:
            try:
                self._file = open(pipe_name, "r+b", buffering=0)   # noqa: SIM115 - closed by close()
                return
            except OSError:                                          # all pipe instances busy, or nobody listens yet
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def settimeout(self, timeout_seconds: float | None) -> None:
        self._timeout_seconds = timeout_seconds

    def sendall(self, data: bytes) -> None:
        self._file.write(data)

    def recv(self, size: int) -> bytes:
        return self._file.read(size) or b""

    def shutdown(self, how: int) -> None:
        """A pipe cannot half-close; ending the conversation closes it."""
        self.close()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._file.close()


async def _serve_pipe(pipe_name: str, on_connection: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any]) -> asyncio.AbstractServer:
    """Windows: serve a named pipe through the Proactor loop's own pipe support (StreamReader/StreamWriter per client)."""
    loop = asyncio.get_running_loop()

    def protocol_factory() -> asyncio.StreamReaderProtocol:
        reader = asyncio.StreamReader()
        return asyncio.StreamReaderProtocol(reader, on_connection)

    servers = await loop.start_serving_pipe(protocol_factory, pipe_name)   # type: ignore[attr-defined]
    return servers[0] if isinstance(servers, list) else servers


def workroom_agent_channel(folder: str | Path) -> LocalChannel:
    """Where the server calls the work room's agent (`agent.sock` in the folder the two share; the pipe `kelo-workroom` on Windows)."""
    if tcp_link_on():
        return TcpChannel("workroom", WORKROOM_HOST, WORKROOM_AGENT_PORT)
    return LocalChannel("workroom", folder, file_name="agent.sock")


def workroom_proxy_channel(folder: str | Path) -> LocalChannel:
    """Where the work room's agent reaches the server's install proxy (`proxy.sock` in the shared folder; `kelo-workroom-proxy`)."""
    if tcp_link_on():
        return TcpChannel("workroom-proxy", SERVER_HOST, WORKROOM_PROXY_PORT)
    return LocalChannel("workroom-proxy", folder, file_name="proxy.sock")
