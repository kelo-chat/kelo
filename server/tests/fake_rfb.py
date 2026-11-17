"""A small in-process RFB 3.8 server (security None) for the live-view tests: it answers a client's handshake, FramebufferUpdateRequest and
SetDesktopSize like TigerVNC's Xvnc does (ExtendedDesktopSize), and records what every connection sent after its handshake."""

from __future__ import annotations

import asyncio
import struct

SIZES = {0: 20, 3: 10, 4: 8, 5: 6, 150: 10}


class FakeRfb:
    def __init__(self, size=(1440, 900), *, screen_id=7, flags=1, resize_status=0, answer_update=True, answer_resize=True):
        self.size = size
        self.screen_id, self.flags = screen_id, flags
        self.resize_status = resize_status        # the status of the reply to SetDesktopSize (0 = done; anything else = refused)
        self.answer_update = answer_update
        self.answer_resize = answer_resize
        self.resizes: list[tuple[int, int, int, int]] = []   # (w, h, screen id, flags) of every SetDesktopSize
        self.encodings: list[list[int]] = []
        self.requests: list[tuple[int, int, int, int, int]] = []   # (incremental, x, y, w, h)
        self.connections: list[list[bytes]] = []             # per connection: the client messages after the handshake
        self.handshakes: list[bytes] = []                     # per connection: the bytes of the handshake
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    def messages(self, kind: int) -> list[bytes]:
        return [m for conn in self.connections for m in conn if m[0] == kind]

    def extended_size(self, reason: int, status: int) -> bytes:
        w, h = self.size
        return (struct.pack(">HHHHi", reason, status, w, h, -308) + bytes([1, 0, 0, 0])
                + struct.pack(">IHHHHI", self.screen_id, 0, 0, w, h, self.flags))

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        log: list[bytes] = []
        pending = b""
        self.connections.append(log)
        try:
            writer.write(b"RFB 003.008\n")
            hand = await reader.readexactly(12)
            writer.write(b"\x01\x01")
            hand += await reader.readexactly(1)
            writer.write(b"\x00\x00\x00\x00")
            hand += await reader.readexactly(1)
            self.handshakes.append(hand)
            name = b"fake"
            writer.write(struct.pack(">HH", *self.size) + bytes([32, 24, 0, 1]) + bytes(12) + struct.pack(">I", len(name)) + name)
            await writer.drain()
            while True:
                kind = (await reader.readexactly(1))[0]
                if kind == 2:
                    head = await reader.readexactly(3)
                    count = int.from_bytes(head[1:3], "big")
                    body = await reader.readexactly(4 * count)
                    self.encodings.append(list(struct.unpack(f">{count}i", body)))
                    log.append(bytes([kind]) + head + body)
                elif kind == 251:
                    head = await reader.readexactly(7)
                    count = head[5]
                    body = await reader.readexactly(16 * count)
                    log.append(bytes([kind]) + head + body)
                    w, h = struct.unpack(">HH", head[1:5])
                    screen = struct.unpack(">IHHHHI", body[:16]) if count else (0, 0, 0, 0, 0, 0)
                    self.resizes.append((w, h, screen[0], screen[5]))
                    if self.resize_status == 0:
                        self.size = (w, h)
                    if self.answer_resize:   # like Xvnc: the answer rides on the next update, which the client has to ask for
                        pending = self.extended_size(1, self.resize_status)
                elif kind == 6:
                    head = await reader.readexactly(7)
                    size = int.from_bytes(head[3:7], "big", signed=True)
                    log.append(bytes([kind]) + head + await reader.readexactly(abs(size)))
                elif kind in SIZES:
                    body = await reader.readexactly(SIZES[kind] - 1)
                    log.append(bytes([kind]) + body)
                    if kind == 3:
                        self.requests.append(struct.unpack(">BHHHH", body))
                        if self.answer_update:
                            # a Bell first, then one update: a Raw 1 x 1 rectangle and the screen layout, like Xvnc's first answer
                            writer.write(bytes([2]))
                            raw = struct.pack(">HHHHi", 0, 0, 1, 1, 0) + bytes(4)
                            writer.write(bytes([0, 0]) + struct.pack(">H", 2) + raw + (pending or self.extended_size(0, 0)))
                            pending = b""
                else:
                    raise AssertionError(f"the fake server does not know client message {kind}")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
