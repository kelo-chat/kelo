"""A small SOCKS5 client and server side (RFC 1928, CONNECT only; RFC 1929 user/password for the client), used by:

* egress.py: the browser's connections go to the VPN sidecar as SOCKS5 CONNECT with the host NAME (ATYP 0x03, remote DNS) or the IP;
* vpn_sidecar.py: the Proxy connector's forwarder serves SOCKS5 on :1080 and forwards to the owner's proxy with its credentials;
* vpn.py: the "is it connected" probe.
"""

from __future__ import annotations

import asyncio
import ipaddress
import struct

OK, GENERAL, NOT_ALLOWED, NET_UNREACHABLE, HOST_UNREACHABLE, REFUSED, TTL, BAD_COMMAND, BAD_ADDRESS = 0, 1, 2, 3, 4, 5, 6, 7, 8
_TEXT = {1: "general failure", 2: "not allowed", 3: "network unreachable", 4: "host unreachable", 5: "connection refused",
         6: "TTL expired", 7: "command not supported", 8: "address type not supported"}


class Socks5Error(Exception):
    def __init__(self, reason: str, code: int = GENERAL):
        super().__init__(reason)
        self.reason, self.code = reason, code


def address_bytes(host: str) -> bytes:
    """ATYP + address: the IP for a literal, the name (ATYP 0x03, resolved by the other end) otherwise."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raw = host.encode("idna") if not host.isascii() else host.encode("ascii")
        if not 0 < len(raw) < 256:
            raise Socks5Error("bad host name", BAD_ADDRESS) from None
        return b"\x03" + bytes([len(raw)]) + raw
    return (b"\x01" if ip.version == 4 else b"\x04") + ip.packed


def request_bytes(host: str, port: int) -> bytes:
    return b"\x05\x01\x00" + address_bytes(host) + struct.pack(">H", port)


async def _read_address(reader: asyncio.StreamReader, atyp: int) -> str:
    if atyp == 1:
        return str(ipaddress.IPv4Address(await reader.readexactly(4)))
    if atyp == 4:
        return str(ipaddress.IPv6Address(await reader.readexactly(16)))
    if atyp == 3:
        n = (await reader.readexactly(1))[0]
        return (await reader.readexactly(n)).decode("ascii", "replace")
    raise Socks5Error("address type not supported", BAD_ADDRESS)


async def handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int,
                    user: str | None = None, password: str | None = None) -> None:
    """Greeting (+ user/password when given) and CONNECT to host:port on an open connection to a SOCKS5 server."""
    want_auth = user is not None
    writer.write(b"\x05\x02\x00\x02" if want_auth else b"\x05\x01\x00")
    ver, method = await reader.readexactly(2)
    if ver != 5:
        raise Socks5Error("not a SOCKS5 server")
    if method == 2:
        if not want_auth:
            raise Socks5Error("the proxy asks for a user name and password")
        u, p = (user or "").encode(), (password or "").encode()
        if len(u) > 255 or len(p) > 255:
            raise Socks5Error("user name or password too long")
        writer.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        if (await reader.readexactly(2))[1] != 0:
            raise Socks5Error("the proxy did not accept the user name and password", NOT_ALLOWED)
    elif method != 0:
        raise Socks5Error("the proxy wants a sign-in method that is not supported")
    writer.write(request_bytes(host, port))
    ver, rep, _, atyp = await reader.readexactly(4)
    if ver != 5:
        raise Socks5Error("not a SOCKS5 server")
    await _read_address(reader, atyp)
    await reader.readexactly(2)
    if rep != OK:
        raise Socks5Error(_TEXT.get(rep, f"error {rep}"), rep)


async def connect(proxy_host: str, proxy_port: int, host: str, port: int, *, user: str | None = None, password: str | None = None,
                  timeout: float = 15.0) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A connection to host:port through the SOCKS5 server at proxy_host:proxy_port. Raises Socks5Error (or OSError, TimeoutError)."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(proxy_host, proxy_port), timeout)
    try:
        await asyncio.wait_for(handshake(reader, writer, host, port, user, password), timeout)
    except BaseException:
        writer.close()
        raise
    return reader, writer


# ---------------------------------------------------------------------------------------------------- server side (no authentication)
async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> tuple[str, int]:
    """Reads the greeting and the CONNECT request of a client; returns (host, port). Call reply() next."""
    ver, n = await reader.readexactly(2)
    methods = await reader.readexactly(n)
    if ver != 5 or 0 not in methods:
        writer.write(b"\x05\xff")
        raise Socks5Error("no acceptable method", GENERAL)
    writer.write(b"\x05\x00")
    ver, cmd, _, atyp = await reader.readexactly(4)
    if ver != 5:
        raise Socks5Error("not SOCKS5")
    try:
        host = await _read_address(reader, atyp)
    except Socks5Error:
        await reply(writer, BAD_ADDRESS)
        raise
    port = struct.unpack(">H", await reader.readexactly(2))[0]
    if cmd != 1:
        await reply(writer, BAD_COMMAND)
        raise Socks5Error("only CONNECT is supported", BAD_COMMAND)
    return host, port


async def reply(writer: asyncio.StreamWriter, code: int) -> None:
    writer.write(b"\x05" + bytes([code]) + b"\x00\x01\x00\x00\x00\x00\x00\x00")
    await writer.drain()
