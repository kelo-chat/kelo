"""tools/vnc.ClientFilter: what of a viewer's RFB bytes goes on to the X server. Pure bytes in, bytes out."""

import struct

import pytest

from assistant.tools.vnc import RFB_CLIENT_SIZES, ClientFilter, VncError

HANDSHAKE = b"RFB 003.008\n" + b"\x01" + b"\x01"
POINTER = struct.pack(">BBHH", 5, 1, 120, 340)
KEY = struct.pack(">BBxxI", 4, 1, 0x61)
CUT = struct.pack(">B3xI", 6, 5) + b"hello"
UPDATE_REQUEST = struct.pack(">BBHHHH", 3, 1, 0, 0, 500, 900)
ENCODINGS = struct.pack(">BxHiii", 2, 3, 0, 7, -308)
PIXEL_FORMAT = bytes([0]) + bytes(19)
FENCE = struct.pack(">B3xIB", 248, 1, 3) + b"abc"
DESKTOP_SIZE = struct.pack(">BxHHBx", 251, 500, 900, 1) + struct.pack(">IHHHHI", 7, 0, 0, 500, 900, 0)
CONTINUOUS = struct.pack(">BBHHHH", 150, 1, 0, 0, 500, 900)
QEMU_KEY = struct.pack(">BBHII", 255, 0, 1, 0x61, 30)
QEMU_AUDIO = struct.pack(">BBH", 255, 1, 0)
XVP = bytes([250, 0, 1, 2])

PASSING = [PIXEL_FORMAT, ENCODINGS, UPDATE_REQUEST, FENCE, DESKTOP_SIZE, CONTINUOUS, XVP, QEMU_AUDIO]
INPUT = [POINTER, KEY, CUT, QEMU_KEY]


def run(stream: bytes, holder: bool, cut: int | None = None) -> bytes:
    """Feed `stream` to a filter (in two pieces split at `cut`, or whole) and return everything it forwarded."""
    f = ClientFilter(lambda: holder)
    if cut is None:
        return f.feed(stream)
    return f.feed(stream[:cut]) + f.feed(stream[cut:])


def test_the_handshake_passes_raw_and_so_does_everything_else_for_the_holder():
    stream = HANDSHAKE + b"".join(PASSING + INPUT)
    assert run(stream, True) == stream


def test_a_viewer_that_does_not_hold_the_tab_loses_pointer_key_and_clipboard_messages_only():
    stream = HANDSHAKE + POINTER + UPDATE_REQUEST + KEY + ENCODINGS + CUT + DESKTOP_SIZE + QEMU_KEY + FENCE
    assert run(stream, False) == HANDSHAKE + UPDATE_REQUEST + ENCODINGS + DESKTOP_SIZE + FENCE


@pytest.mark.parametrize("holder", [True, False])
def test_the_result_is_the_same_when_the_bytes_are_split_at_every_position(holder):
    stream = HANDSHAKE + b"".join([POINTER, UPDATE_REQUEST, CUT, KEY, ENCODINGS, FENCE, QEMU_KEY, DESKTOP_SIZE, POINTER])
    expected = run(stream, holder)
    assert expected != b"" and (expected == stream) == holder
    for cut in range(len(stream) + 1):
        assert run(stream, holder, cut) == expected, cut


def test_one_byte_at_a_time_gives_the_same_result():
    stream = HANDSHAKE + POINTER + CUT + UPDATE_REQUEST + KEY + FENCE
    f = ClientFilter(lambda: False)
    out = b"".join(f.feed(bytes([b])) for b in stream)
    assert out == HANDSHAKE + UPDATE_REQUEST + FENCE


def test_holding_is_asked_for_every_message_so_taking_over_counts_from_the_next_message():
    state = {"holder": False}
    f = ClientFilter(lambda: state["holder"])
    assert f.feed(HANDSHAKE + POINTER) == HANDSHAKE
    state["holder"] = True
    assert f.feed(POINTER) == POINTER
    state["holder"] = False
    assert f.feed(POINTER + UPDATE_REQUEST) == UPDATE_REQUEST


def test_a_long_clipboard_is_dropped_as_it_comes_and_never_held_back():
    big = struct.pack(">B3xI", 6, 300_000) + bytes(300_000)
    f = ClientFilter(lambda: False)
    assert f.feed(HANDSHAKE) == HANDSHAKE
    out = b"".join(f.feed(big[i:i + 7000]) for i in range(0, len(big), 7000))
    assert out == b"" and len(f._buf) == 0
    assert f.feed(UPDATE_REQUEST) == UPDATE_REQUEST   # the next message is read right after the clipboard's last byte


def test_an_extended_clipboard_with_a_negative_length_is_a_clipboard_too():
    extended = struct.pack(">B3xi", 6, -9) + bytes(9)
    assert run(HANDSHAKE + extended + UPDATE_REQUEST, False) == HANDSHAKE + UPDATE_REQUEST
    assert run(HANDSHAKE + extended + UPDATE_REQUEST, True) == HANDSHAKE + extended + UPDATE_REQUEST


def test_an_unknown_message_type_raises_so_the_relay_closes():
    f = ClientFilter(lambda: True)
    f.feed(HANDSHAKE)
    with pytest.raises(VncError):
        f.feed(bytes([99, 0, 0, 0]))


def test_something_that_is_not_an_rfb_version_raises():
    with pytest.raises(VncError):
        ClientFilter(lambda: True).feed(b"GET / HTTP/1.1\r\n")


def test_version_3_3_has_no_security_choice_byte():
    stream = b"RFB 003.003\n" + b"\x01" + POINTER + UPDATE_REQUEST
    assert run(stream, False) == b"RFB 003.003\n" + b"\x01" + UPDATE_REQUEST


def test_the_message_lengths_follow_the_rfb_protocol():
    sizes = {kind: rule[1](bytes([kind]) + bytes(20)) for kind, rule in RFB_CLIENT_SIZES.items() if kind not in (2, 6, 248, 251, 255)}
    assert sizes == {0: 20, 3: 10, 4: 8, 5: 6, 150: 10, 250: 4}
    assert RFB_CLIENT_SIZES[2][1](struct.pack(">BxH", 2, 3)) == 4 + 4 * 3
    assert RFB_CLIENT_SIZES[6][1](struct.pack(">B3xI", 6, 11)) == 8 + 11
    assert RFB_CLIENT_SIZES[248][1](struct.pack(">B3xIB", 248, 0, 5)) == 9 + 5
    assert RFB_CLIENT_SIZES[251][1](struct.pack(">BxHHBx", 251, 1, 1, 2)) == 8 + 32
