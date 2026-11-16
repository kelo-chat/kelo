"""hosting/channels.py: LocalChannel for real on a Unix socket; the Windows named-pipe branch through fakes."""

import asyncio
import socketserver
import tempfile
import threading
from pathlib import Path

import httpx
import pytest

from assistant.hosting import channels, operating_system
from assistant.hosting.channels import ChannelUnsupported, LocalChannel, workroom_agent_channel, workroom_proxy_channel


@pytest.fixture
def folder():
    with tempfile.TemporaryDirectory(prefix="kc") as short:      # a Unix socket path must be short
        yield Path(short)


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(b"echo:" + self.request.recv(100))


def test_the_names_are_the_ones_docker_has_always_used():
    assert workroom_agent_channel("/run/workroom").address == "/run/workroom/agent.sock"
    assert workroom_proxy_channel("/run/workroom").address == "/run/workroom/proxy.sock"
    assert LocalChannel("thing", "/x").address == "/x/thing.sock"
    assert LocalChannel.at_path("/a/b/agent.sock").address == "/a/b/agent.sock"
    assert LocalChannel.at_path("/a/b/agent.sock").name == "agent"


def test_a_message_goes_through_a_unix_socket_and_back(folder):
    channel = LocalChannel("t", folder)
    assert not channel.is_present()
    server = channel.make_server(Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert channel.is_present()
        assert (channel.socket_path.stat().st_mode & 0o777) == 0o660
        client = channel.connect(timeout_seconds=5)
        client.sendall(b"hi")
        assert client.recv(100) == b"echo:hi"
        client.close()
    finally:
        server.shutdown()
        server.server_close()


def test_a_server_replaces_the_socket_a_crashed_run_left(folder):
    channel = LocalChannel("t", folder)
    channel.socket_path.write_text("stale")
    server = channel.make_server(Echo)
    server.server_close()
    channel.remove_leftover()
    assert not channel.is_present()
    channel.remove_leftover()                                    # nothing there: no error


def test_connecting_to_nobody_is_an_oserror(folder):
    with pytest.raises(OSError):
        LocalChannel("t", folder).connect(timeout_seconds=1)


async def test_the_asyncio_server_answers_and_restricts_the_socket(folder):
    channel = LocalChannel("t", folder)

    async def handle(reader, writer):
        writer.write(b"pong:" + await reader.read(10))
        await writer.drain()
        writer.close()
    server = await channel.serve_async(handle)
    try:
        assert (channel.socket_path.stat().st_mode & 0o777) == 0o660
        reader, writer = await asyncio.open_unix_connection(str(channel.socket_path))
        writer.write(b"ping")
        assert await reader.read(100) == b"pong:ping"
        writer.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_httpx_talks_through_the_channel(folder):
    channel = LocalChannel("t", folder)

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 12\r\nContent-Type: application/json\r\n\r\n{"ok": true}')
        await writer.drain()
        writer.close()
    server = await channel.serve_async(handle)
    try:
        async with httpx.AsyncClient(transport=channel.make_http_transport(), base_url="http://workroom") as client:
            assert (await client.get("/health")).json() == {"ok": True}
    finally:
        server.close()
        await server.wait_closed()


# ---- Windows, through fakes ----------------------------------------------------------------------------------------
@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(operating_system, "is_windows", lambda: True)


def test_windows_uses_a_named_pipe(windows):
    channel = workroom_agent_channel("/run/workroom")
    assert channel.address == "\\\\.\\pipe\\kelo-workroom"
    assert workroom_proxy_channel("/x").address == "\\\\.\\pipe\\kelo-workroom-proxy"
    assert LocalChannel("a", "/x").pipe_name == "\\\\.\\pipe\\kelo-a"


def test_windows_has_no_socket_file_to_clean_or_restrict(windows, tmp_path):
    channel = LocalChannel("t", tmp_path)
    channel.socket_path.write_text("a file with the socket's name")
    channel.remove_leftover()
    channel.restrict_to_group()
    assert channel.socket_path.read_text() == "a file with the socket's name"


def test_windows_client_opens_the_pipe_like_a_file(windows, monkeypatch):
    opened = []

    class FakePipe:
        def __init__(self):
            self.sent, self.closed = [], False

        def write(self, data):
            self.sent.append(data)

        def read(self, size):
            return b"HTTP/1.0 200 OK"

        def close(self):
            self.closed = True
    pipe = FakePipe()
    monkeypatch.setattr(channels, "open", lambda name, mode, buffering: opened.append((name, mode, buffering)) or pipe, raising=False)
    client = workroom_agent_channel("/x").connect(timeout_seconds=5)
    client.sendall(b"GET /health\r\n\r\n")
    assert client.recv(200) == b"HTTP/1.0 200 OK"
    client.shutdown(2)
    assert opened == [("\\\\.\\pipe\\kelo-workroom", "r+b", 0)] and pipe.sent == [b"GET /health\r\n\r\n"] and pipe.closed


def test_windows_client_retries_a_busy_pipe_until_the_timeout(windows, monkeypatch):
    attempts = []

    def busy(name, mode, buffering):
        attempts.append(name)
        raise OSError(231, "all pipe instances are busy")
    monkeypatch.setattr(channels, "open", busy, raising=False)
    with pytest.raises(OSError):
        LocalChannel("t", "/x").connect(timeout_seconds=0.2)
    assert len(attempts) >= 2


async def test_windows_serves_the_pipe_through_the_loop(windows, monkeypatch):
    loop = asyncio.get_running_loop()
    served = []

    async def fake_start_serving_pipe(factory, address):
        served.append((type(factory()).__name__, address))
        return ["pipe-server"]
    monkeypatch.setattr(loop, "start_serving_pipe", fake_start_serving_pipe, raising=False)
    server = await LocalChannel("workroom", "/x").serve_async(lambda reader, writer: None)
    assert server == "pipe-server" and served == [("StreamReaderProtocol", "\\\\.\\pipe\\kelo-workroom")]


def test_windows_says_plainly_what_is_not_there_yet(windows):
    channel = LocalChannel("t", "/x")
    with pytest.raises(ChannelUnsupported):
        channel.make_server(Echo)
    with pytest.raises(ChannelUnsupported):
        channel.make_http_transport()
