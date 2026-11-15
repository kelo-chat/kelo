"""KE2E: end-to-end encryption between the apps and their own Kelo server (docs/security/e2e-and-phone-key.md, section 1).

Pure building blocks, no web framework here:

- HPKE base mode (RFC 9180) for the one suite KE2E uses: DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 / ChaCha20-Poly1305.
  `cryptography` ships only single-shot HPKE (no exporter, no AAD, no fixed ephemeral key for test vectors), so the key schedule is
  composed from its X25519, HMAC-SHA256 and ChaCha20Poly1305 primitives exactly as the RFC writes it. tests/test_e2e_core.py runs
  the RFC 9180 test vectors for this suite and cross-checks against `cryptography`'s own single-shot HPKE.
- `ServerKey`: the server's static X25519 key at <state>/e2e/server.key (0600), its kid and the fingerprint people compare.
- The request envelope (`0x01 | kid | enc | ct`) and the inner request (`u32 len(H) | H | body`), opened by `E2EServer`.
- Response frames (`u32 len(c) | c`) sealed by `FrameSealer` and read back by `FrameOpener`, also for streams.
- WebSocket keys and per-message sealing (`WsChannel`).
- `ReplayCache` and the timestamp window.

The private key never goes to a log, a repr or an API answer.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

# ---------------------------------------------------------------------------------------------------------------------------------
# HPKE (RFC 9180), base mode, DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 / ChaCha20-Poly1305
# ---------------------------------------------------------------------------------------------------------------------------------

KEM_ID = 0x0020
KDF_ID = 0x0001
AEAD_ID = 0x0003
MODE_BASE = 0x00

N_SECRET = 32   # DHKEM(X25519) shared secret
N_ENC = 32
N_PK = 32
N_SK = 32
N_H = 32        # HKDF-SHA256
N_K = 32        # ChaCha20-Poly1305
N_N = 12
N_T = 16

_KEM_SUITE_ID = b"KEM" + struct.pack(">H", KEM_ID)
_HPKE_SUITE_ID = b"HPKE" + struct.pack(">HHH", KEM_ID, KDF_ID, AEAD_ID)
_VERSION_LABEL = b"HPKE-v1"


class HpkeError(Exception):
    """An HPKE operation failed (bad key, a ciphertext that does not open, sequence overflow)."""


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt or b"\x00" * N_H, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    if length > 255 * N_H:
        raise HpkeError("expand length too large")
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


def labeled_extract(suite_id: bytes, salt: bytes, label: bytes, ikm: bytes) -> bytes:
    return _hkdf_extract(salt, _VERSION_LABEL + suite_id + label + ikm)


def labeled_expand(suite_id: bytes, prk: bytes, label: bytes, info: bytes, length: int) -> bytes:
    return _hkdf_expand(prk, struct.pack(">H", length) + _VERSION_LABEL + suite_id + label + info, length)


def _pub_bytes(pk: X25519PublicKey) -> bytes:
    return pk.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _priv_bytes(sk: X25519PrivateKey) -> bytes:
    return sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def derive_key_pair(ikm: bytes) -> X25519PrivateKey:
    """RFC 9180 7.1.3 DeriveKeyPair for X25519 (used for test vectors; real keys come from the OS random source)."""
    dkp_prk = labeled_extract(_KEM_SUITE_ID, b"", b"dkp_prk", ikm)
    return X25519PrivateKey.from_private_bytes(labeled_expand(_KEM_SUITE_ID, dkp_prk, b"sk", b"", N_SK))


def _dh(sk: X25519PrivateKey, pk: X25519PublicKey) -> bytes:
    try:
        dh = sk.exchange(pk)
    except ValueError as e:  # low-order point: cryptography refuses the all-zero result
        raise HpkeError("dh failed") from e
    if dh == b"\x00" * 32:
        raise HpkeError("dh failed")
    return dh


def _extract_and_expand(dh: bytes, kem_context: bytes) -> bytes:
    eae_prk = labeled_extract(_KEM_SUITE_ID, b"", b"eae_prk", dh)
    return labeled_expand(_KEM_SUITE_ID, eae_prk, b"shared_secret", kem_context, N_SECRET)


def encap(pk_r: X25519PublicKey, ikm_e: bytes | None = None) -> tuple[bytes, bytes]:
    """(shared_secret, enc). `ikm_e` fixes the ephemeral key (test vectors only)."""
    sk_e = derive_key_pair(ikm_e) if ikm_e is not None else X25519PrivateKey.generate()
    enc = _pub_bytes(sk_e.public_key())
    dh = _dh(sk_e, pk_r)
    return _extract_and_expand(dh, enc + _pub_bytes(pk_r)), enc


def decap(enc: bytes, sk_r: X25519PrivateKey) -> bytes:
    if len(enc) != N_ENC:
        raise HpkeError("bad enc length")
    try:
        pk_e = X25519PublicKey.from_public_bytes(enc)
    except ValueError as e:
        raise HpkeError("bad enc") from e
    dh = _dh(sk_r, pk_e)
    return _extract_and_expand(dh, enc + _pub_bytes(sk_r.public_key()))


@dataclass
class KeySchedule:
    """What RFC 9180's KeySchedule makes (exposed for the test vectors)."""
    key_schedule_context: bytes
    secret: bytes
    key: bytes
    base_nonce: bytes
    exporter_secret: bytes


def key_schedule(shared_secret: bytes, info: bytes) -> KeySchedule:
    psk, psk_id = b"", b""
    psk_id_hash = labeled_extract(_HPKE_SUITE_ID, b"", b"psk_id_hash", psk_id)
    info_hash = labeled_extract(_HPKE_SUITE_ID, b"", b"info_hash", info)
    ksc = bytes([MODE_BASE]) + psk_id_hash + info_hash
    secret = labeled_extract(_HPKE_SUITE_ID, shared_secret, b"secret", psk)
    return KeySchedule(
        key_schedule_context=ksc,
        secret=secret,
        key=labeled_expand(_HPKE_SUITE_ID, secret, b"key", ksc, N_K),
        base_nonce=labeled_expand(_HPKE_SUITE_ID, secret, b"base_nonce", ksc, N_N),
        exporter_secret=labeled_expand(_HPKE_SUITE_ID, secret, b"exp", ksc, N_H),
    )


class HpkeContext:
    """An HPKE encryption context (one direction: the sender seals, the recipient opens) plus the exporter."""

    def __init__(self, schedule: KeySchedule, shared_secret: bytes):
        self.schedule = schedule
        self.shared_secret = shared_secret
        self._aead = ChaCha20Poly1305(schedule.key)
        self.seq = 0

    def _nonce(self) -> bytes:
        if self.seq >= (1 << (8 * N_N)) - 1:
            raise HpkeError("message limit reached")
        seq_bytes = self.seq.to_bytes(N_N, "big")
        return bytes(a ^ b for a, b in zip(self.schedule.base_nonce, seq_bytes))

    def seal(self, aad: bytes, pt: bytes) -> bytes:
        ct = self._aead.encrypt(self._nonce(), pt, aad)
        self.seq += 1
        return ct

    def open(self, aad: bytes, ct: bytes) -> bytes:
        try:
            pt = self._aead.decrypt(self._nonce(), ct, aad)
        except InvalidTag as e:
            raise HpkeError("open failed") from e
        self.seq += 1
        return pt

    def export(self, exporter_context: bytes, length: int) -> bytes:
        return labeled_expand(_HPKE_SUITE_ID, self.schedule.exporter_secret, b"sec", exporter_context, length)


def setup_base_s(pk_r: X25519PublicKey | bytes, info: bytes, ikm_e: bytes | None = None) -> tuple[bytes, HpkeContext]:
    if isinstance(pk_r, (bytes, bytearray)):
        pk_r = X25519PublicKey.from_public_bytes(bytes(pk_r))
    shared_secret, enc = encap(pk_r, ikm_e)
    return enc, HpkeContext(key_schedule(shared_secret, info), shared_secret)


def setup_base_r(enc: bytes, sk_r: X25519PrivateKey, info: bytes) -> HpkeContext:
    shared_secret = decap(enc, sk_r)
    return HpkeContext(key_schedule(shared_secret, info), shared_secret)


# ---------------------------------------------------------------------------------------------------------------------------------
# KE2E constants and errors
# ---------------------------------------------------------------------------------------------------------------------------------

ENVELOPE_VERSION = 0x01        # the body inside ct
ENVELOPE_VERSION_STREAM = 0x02  # ct holds only the head; the body follows as frames (spec section 5 item 2)
KID_LEN = 8
CONTENT_TYPE = "application/x-kelo-e2e"
INFO_PREFIX = b"kelo-e2e v1 "
AAD_REQUEST = b"kelo-e2e v1 req"
LABEL_RESPONSE = b"kelo-e2e v1 res"      # exporter context of the response key and the AAD prefix of every response frame
LABEL_WS_C2S = b"kelo-e2e v1 ws c2s"
LABEL_WS_S2C = b"kelo-e2e v1 ws s2c"
AAD_WS = b"kelo-e2e v1 ws"
LABEL_REQ_BODY = b"kelo-e2e v1 req body"   # exporter context of a streamed request body's key (v2 envelope)
WS_TEXT = 0x01                 # first plaintext byte of every sealed WebSocket message
WS_BINARY = 0x02
WS_CLOSE_BAD_ENVELOPE = 4400   # the first WebSocket envelope does not open
WS_CLOSE_UNAUTHORIZED = 4401   # not signed in / ticket refused
WS_CLOSE_KEY_CHANGED = 4409    # e2e_key_changed
FLAG_MORE = 0x00
FLAG_LAST = 0x01
NONCE_LEN = 16                 # the inner request's "n", before base64url
TS_WINDOW = 300                # seconds either side of the server's clock
REPLAY_WINDOW = 600            # seconds a nonce is remembered
MAX_FRAME_PLAINTEXT = 64 * 1024
MAX_FRAME_CIPHERTEXT = MAX_FRAME_PLAINTEXT + N_T
MAX_HEAD_LEN = 64 * 1024       # the inner request's JSON head
# Only these request headers travel inside the envelope (plus every x-kelo-*); the server drops any other.
INNER_HEADERS = frozenset({"authorization", "content-type", "accept", "accept-language", "if-none-match", "range"})
# Response headers frame 0 leaves out: hop-by-hop ones and set-cookie (spec section 5 item 3); every other header goes, lowercased.
DROPPED_RESPONSE_HEADERS = frozenset({"connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "set-cookie"})


class E2EError(Exception):
    """Base of every KE2E failure. `code` is a stable name (used by the test vectors' negative cases)."""
    code = "e2e_error"


class BadEnvelope(E2EError):
    """Does not parse or open. The HTTP answer is a plaintext 400 with no detail."""
    code = "bad_envelope"


class UnknownKid(E2EError):
    """The envelope names another server key (rotated, or another server). Plaintext 409 {"code": "e2e_key_changed"}."""
    code = "unknown_kid"


class Rejected(E2EError):
    """Opened fine but refused (clock or replay). The context exists, so the answer can be a sealed error frame."""
    code = "rejected"

    def __init__(self, msg: str, opened: "OpenedRequest"):
        super().__init__(msg)
        self.opened = opened


class StaleTimestamp(Rejected):
    code = "stale_timestamp"


class Replayed(Rejected):
    code = "replayed"


class BadFrame(E2EError):
    """A response or WebSocket frame that does not open, is out of order, too large, or comes after the last frame."""
    code = "bad_frame"


class Truncated(E2EError):
    """A response stream that ended without its last frame."""
    code = "truncated"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or "=" in text:
        raise ValueError("not unpadded base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def kid_of(pk: bytes) -> bytes:
    return hashlib.sha256(pk).digest()[:KID_LEN]


def fingerprint_of(pk: bytes) -> str:
    """SHA-256(pk), first 15 bytes, base32 without padding, in groups of 4: 24 letters/digits, 6 groups."""
    text = base64.b32encode(hashlib.sha256(pk).digest()[:15]).decode("ascii")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def info_for(kid: bytes) -> bytes:
    return INFO_PREFIX + kid.hex().encode("ascii")


# ---------------------------------------------------------------------------------------------------------------------------------
# The server key
# ---------------------------------------------------------------------------------------------------------------------------------

class ServerKey:
    """The server's static X25519 key pair. The file holds the 32 raw private key bytes, mode 0600, in a 0700 folder."""

    FILE = Path("e2e") / "server.key"

    def __init__(self, private_key: X25519PrivateKey, path: Path | None = None):
        self._sk = private_key
        self.path = path
        self.public_bytes = _pub_bytes(private_key.public_key())
        self.kid = kid_of(self.public_bytes)
        self.kid_hex = self.kid.hex()
        self.info = info_for(self.kid)
        self.fingerprint = fingerprint_of(self.public_bytes)

    def __repr__(self) -> str:  # never the private key
        return f"ServerKey(kid={self.kid_hex})"

    @property
    def public_b64(self) -> str:
        return b64url(self.public_bytes)

    def public_info(self) -> dict:
        """The body of GET /api/e2e/info."""
        return {"v": 1, "kid": self.kid_hex, "pk": self.public_b64, "fingerprint": self.fingerprint}

    @classmethod
    def from_private_bytes(cls, raw: bytes, path: Path | None = None) -> "ServerKey":
        return cls(X25519PrivateKey.from_private_bytes(raw), path)

    @classmethod
    def path_in(cls, state_dir: str | Path) -> Path:
        return Path(state_dir) / cls.FILE

    @classmethod
    def load_or_create(cls, state_dir: str | Path) -> "ServerKey":
        path = cls.path_in(state_dir)
        if path.exists():
            raw = path.read_bytes()
            if len(raw) != N_SK:
                raise RuntimeError(f"{path} is not a KE2E server key (expected {N_SK} bytes)")
            if os.name == "posix" and path.stat().st_mode & 0o077:
                os.chmod(path, 0o600)
            return cls.from_private_bytes(raw, path)
        return cls._write_new(path, exclusive=True)

    @classmethod
    def rotate(cls, state_dir: str | Path) -> "ServerKey":
        """A new pair replaces the old one; every device must pair again."""
        return cls._write_new(cls.path_in(state_dir), exclusive=False)

    @classmethod
    def _write_new(cls, path: Path, exclusive: bool) -> "ServerKey":
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        sk = X25519PrivateKey.generate()
        tmp = path.with_name(path.name + ".tmp")
        if exclusive:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(_priv_bytes(sk))
                f.flush()
                os.fsync(f.fileno())
        else:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(_priv_bytes(sk))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        return cls(sk, path)

    def open_context(self, enc: bytes) -> HpkeContext:
        return setup_base_r(enc, self._sk, self.info)


# ---------------------------------------------------------------------------------------------------------------------------------
# Replay cache
# ---------------------------------------------------------------------------------------------------------------------------------

class ReplayCache:
    """Remembers request nonces for `window` seconds. Thread-safe.

    Anyone with the public key can make envelopes that open, so the cache is bounded: past `max_entries` the oldest entry goes.
    An evicted nonce is only replayable while its timestamp is still inside the ±300 s window, which takes a flood of
    `max_entries` envelopes within minutes; the HTTP layer's rate limit stands in front of that.
    """

    def __init__(self, window: int = REPLAY_WINDOW, max_entries: int = 200_000):
        self.window = window
        self.max_entries = max_entries
        self._seen: OrderedDict[bytes, float] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._seen)

    def check_and_add(self, nonce: bytes, now: float | None = None) -> bool:
        """True if new (and now remembered), False if seen within the window."""
        now = time.time() if now is None else now
        with self._lock:
            while self._seen:
                oldest, expiry = next(iter(self._seen.items()))
                if expiry > now:
                    break
                del self._seen[oldest]
            expiry = self._seen.get(nonce)
            if expiry is not None and expiry > now:
                return False
            self._seen[nonce] = now + self.window
            self._seen.move_to_end(nonce)
            while len(self._seen) > self.max_entries:
                self._seen.popitem(last=False)
            return True


# ---------------------------------------------------------------------------------------------------------------------------------
# Inner request and envelope
# ---------------------------------------------------------------------------------------------------------------------------------

@dataclass
class InnerRequest:
    method: str
    path: str                      # path plus query, e.g. "/api/talk?x=1"
    headers: dict[str, str]
    ts: int
    nonce: bytes
    body: bytes = b""

    def head_json(self) -> bytes:
        return json.dumps({"m": self.method, "p": self.path, "h": self.headers, "ts": self.ts, "n": b64url(self.nonce)},
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def encode(self) -> bytes:
        head = self.head_json()
        return struct.pack(">I", len(head)) + head + self.body


def filter_inner_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        name = k.lower()
        if name in INNER_HEADERS or name.startswith("x-kelo-"):
            out[name] = v
    return out


def decode_inner(data: bytes) -> InnerRequest:
    """Parse `u32 len(H) | H | body`. Raises BadEnvelope. Header names are lowered and filtered to the allowed set."""
    if len(data) < 4:
        raise BadEnvelope("short inner request")
    (hlen,) = struct.unpack(">I", data[:4])
    if hlen > MAX_HEAD_LEN or 4 + hlen > len(data):
        raise BadEnvelope("bad head length")
    try:
        head = json.loads(data[4:4 + hlen].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise BadEnvelope("head is not JSON") from e
    if not isinstance(head, dict):
        raise BadEnvelope("head is not an object")
    m, p, h, ts, n = head.get("m"), head.get("p"), head.get("h", {}), head.get("ts"), head.get("n")
    if not isinstance(m, str) or not m or not m.isascii() or not m.isalpha():
        raise BadEnvelope("bad method")
    if not isinstance(p, str) or not p.startswith("/") or p.startswith("//") or any(ord(c) < 0x21 or c == "\x7f" for c in p):
        raise BadEnvelope("bad path")
    if not isinstance(h, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in h.items()):
        raise BadEnvelope("bad headers")
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise BadEnvelope("bad ts")
    try:
        nonce = b64url_decode(n)
    except (ValueError, binascii.Error) as e:
        raise BadEnvelope("bad nonce") from e
    if len(nonce) != NONCE_LEN:
        raise BadEnvelope("bad nonce length")
    return InnerRequest(method=m.upper(), path=p, headers=filter_inner_headers(h), ts=ts, nonce=nonce, body=data[4 + hlen:])


def encode_envelope(kid: bytes, enc: bytes, ct: bytes) -> bytes:
    return bytes([ENVELOPE_VERSION]) + kid + enc + ct


def split_envelope(data: bytes) -> tuple[bytes, bytes, bytes]:
    """(kid, enc, ct) of a version 0x01 envelope. Raises BadEnvelope on a wrong version or size."""
    if len(data) < 1 + KID_LEN + N_ENC + N_T or data[0] != ENVELOPE_VERSION:
        raise BadEnvelope("bad envelope")
    return data[1:1 + KID_LEN], data[1 + KID_LEN:1 + KID_LEN + N_ENC], data[1 + KID_LEN + N_ENC:]


def encode_stream_head(kid: bytes, enc: bytes, ct: bytes) -> bytes:
    """The start of a version 0x02 envelope: 0x02 | kid | enc | u32 BE len(ct) | ct. The body frames follow."""
    return bytes([ENVELOPE_VERSION_STREAM]) + kid + enc + struct.pack(">I", len(ct)) + ct


STREAM_HEAD_FIXED = 1 + KID_LEN + N_ENC + 4


def stream_head_length(prefix: bytes) -> int | None:
    """How many bytes the 0x02 head takes, once its first STREAM_HEAD_FIXED bytes are there (None = need more bytes)."""
    if len(prefix) < STREAM_HEAD_FIXED:
        return None
    if prefix[0] != ENVELOPE_VERSION_STREAM:
        raise BadEnvelope("bad envelope")
    (ctlen,) = struct.unpack(">I", prefix[1 + KID_LEN + N_ENC:STREAM_HEAD_FIXED])
    if ctlen < N_T or ctlen > MAX_HEAD_LEN + 4 + N_T:
        raise BadEnvelope("bad head length")
    return STREAM_HEAD_FIXED + ctlen


def split_stream_head(data: bytes) -> tuple[bytes, bytes, bytes, bytes]:
    """(kid, enc, ct, rest) of a version 0x02 envelope; `rest` is the start of the body frames."""
    total = stream_head_length(data)
    if total is None or len(data) < total:
        raise BadEnvelope("short envelope")
    return data[1:1 + KID_LEN], data[1 + KID_LEN:1 + KID_LEN + N_ENC], data[STREAM_HEAD_FIXED:total], data[total:]


@dataclass
class OpenedRequest:
    """An opened envelope: the inner request and the HPKE context its answer is sealed with."""
    inner: InnerRequest
    ctx: HpkeContext = field(repr=False)

    def response_key(self) -> bytes:
        return self.ctx.export(LABEL_RESPONSE, 32)

    def response_sealer(self) -> "FrameSealer":
        return FrameSealer(self.response_key())

    def ws_channel(self) -> "WsChannel":
        """The server's side of the WebSocket: seals s2c, opens c2s."""
        return WsChannel(send_key=self.ctx.export(LABEL_WS_S2C, 32), recv_key=self.ctx.export(LABEL_WS_C2S, 32))

    def body_opener(self) -> "FrameOpener":
        """Reads the body frames of a version 0x02 (streamed) request."""
        return FrameOpener(self.ctx.export(LABEL_REQ_BODY, 32), aad=AAD_REQUEST, head=False)


class E2EServer:
    """Opens request envelopes for one server key: kid check, HPKE open, inner parse, clock window, replay cache."""

    def __init__(self, key: ServerKey, replay: ReplayCache | None = None, ts_window: int = TS_WINDOW):
        self.key = key
        self.replay = replay or ReplayCache()
        self.ts_window = ts_window

    def open_request(self, envelope: bytes, now: float | None = None) -> OpenedRequest:
        """A whole envelope, either version. For 0x02 the body frames are read here too and become `inner.body`;
        a server that streams the body instead calls open_stream_head() and reads the frames with body_opener()."""
        if envelope[:1] == bytes([ENVELOPE_VERSION_STREAM]):
            kid, enc, ct, rest = split_stream_head(envelope)
            opened = self._open(kid, enc, ct, now)
            reader = opened.body_opener()
            try:
                body = b"".join(reader.feed(rest))
                reader.close()
            except (BadFrame, Truncated) as e:
                raise BadEnvelope("body frames do not open") from e
            opened.inner.body = body
            return opened
        kid, enc, ct = split_envelope(envelope)
        return self._open(kid, enc, ct, now)

    def open_stream_head(self, head: bytes, now: float | None = None) -> OpenedRequest:
        """The head of a version 0x02 envelope (exactly stream_head_length() bytes). The body frames are not part of it."""
        kid, enc, ct, rest = split_stream_head(head)
        if rest:
            raise BadEnvelope("body bytes passed with the head")
        return self._open(kid, enc, ct, now, streamed=True)

    def _open(self, kid: bytes, enc: bytes, ct: bytes, now: float | None, streamed: bool = False) -> OpenedRequest:
        if not hmac.compare_digest(kid, self.key.kid):
            raise UnknownKid("unknown kid")
        try:
            ctx = self.key.open_context(enc)
            inner_bytes = ctx.open(AAD_REQUEST, ct)
        except HpkeError as e:
            raise BadEnvelope("does not open") from e
        opened = OpenedRequest(decode_inner(inner_bytes), ctx)
        if streamed and opened.inner.body:
            raise BadEnvelope("a streamed request seals its body in frames")
        now = time.time() if now is None else now
        if abs(opened.inner.ts - now) > self.ts_window:
            raise StaleTimestamp("timestamp outside the window", opened)
        if not self.replay.check_and_add(opened.inner.nonce, now):
            raise Replayed("nonce seen before", opened)
        return opened


# ---------------------------------------------------------------------------------------------------------------------------------
# Response frames
# ---------------------------------------------------------------------------------------------------------------------------------

def _frame_nonce(counter: int) -> bytes:
    if counter >= 1 << 64:
        raise E2EError("frame counter overflow")
    return b"\x00\x00\x00\x00" + counter.to_bytes(8, "big")


def response_head(status: int, headers: dict[str, str] | Iterable[tuple[str, str]] = ()) -> bytes:
    """Frame 0's plaintext: {"s": status, "h": {...}}: every response header, lowercased, except hop-by-hop ones and set-cookie.
    A header sent more than once is joined with ", " (as HTTP allows for list headers)."""
    items = headers.items() if isinstance(headers, dict) else headers
    h: dict[str, str] = {}
    for k, v in items:
        name = k.lower()
        if name in DROPPED_RESPONSE_HEADERS or name.startswith("proxy-"):
            continue
        h[name] = f"{h[name]}, {v}" if name in h else v
    return json.dumps({"s": status, "h": h}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class FrameSealer:
    """Seals one response stream: frame 0 = head JSON, then body pieces; the last frame has flag 0x01.
    With aad=AAD_REQUEST it seals the body frames of a streamed (0x02) request, which have no head frame."""

    def __init__(self, key: bytes, aad: bytes = LABEL_RESPONSE):
        self._aead = ChaCha20Poly1305(key)
        self._aad = aad
        self.counter = 0
        self.finished = False

    def seal(self, pt: bytes, last: bool = False) -> bytes:
        if self.finished:
            raise E2EError("stream already finished")
        if len(pt) > MAX_FRAME_PLAINTEXT:
            raise E2EError("frame too large: use seal_chunks")
        flag = FLAG_LAST if last else FLAG_MORE
        c = self._aead.encrypt(_frame_nonce(self.counter), pt, self._aad + bytes([flag]))
        self.counter += 1
        self.finished = last
        return struct.pack(">I", len(c)) + c

    def seal_chunks(self, data: bytes, last: bool = False) -> Iterator[bytes]:
        """`data` cut into frames of at most 64 KiB (an empty `data` with last=True still makes one empty last frame)."""
        if not data:
            if last:
                yield self.seal(b"", last=True)
            return
        for i in range(0, len(data), MAX_FRAME_PLAINTEXT):
            piece = data[i:i + MAX_FRAME_PLAINTEXT]
            yield self.seal(piece, last=last and i + MAX_FRAME_PLAINTEXT >= len(data))

    def finish(self) -> bytes:
        """An empty last frame (for streams whose end is only known after the last piece was sent)."""
        return self.seal(b"", last=True)


class FrameOpener:
    """Reads a response stream fed in arbitrary byte chunks. `feed` returns the plaintexts of complete frames.

    `head` is frame 0 parsed; `close()` raises Truncated if the last frame never came. Every error is final.
    head=False, aad=AAD_REQUEST: the body frames of a streamed (0x02) request (no head frame).
    """

    def __init__(self, key: bytes, aad: bytes = LABEL_RESPONSE, head: bool = True):
        self._aead = ChaCha20Poly1305(key)
        self._aad = aad
        self._want_head = head
        self._buf = bytearray()
        self.counter = 0
        self.done = False
        self.head: dict | None = None
        self._failed = False

    def feed(self, data: bytes) -> list[bytes]:
        if self._failed:
            raise BadFrame("stream already failed")
        self._buf += data
        out: list[bytes] = []
        try:
            while len(self._buf) >= 4:
                if self.done:
                    raise BadFrame("data after the last frame")
                (clen,) = struct.unpack(">I", self._buf[:4])
                if clen < N_T or clen > MAX_FRAME_CIPHERTEXT:
                    raise BadFrame("bad frame length")
                if len(self._buf) < 4 + clen:
                    break
                c = bytes(self._buf[4:4 + clen])
                del self._buf[:4 + clen]
                out.append(self._open(c))
        except E2EError:
            self._failed = True
            raise
        return out

    def _open(self, c: bytes) -> bytes:
        nonce = _frame_nonce(self.counter)
        for flag in (FLAG_MORE, FLAG_LAST):
            try:
                pt = self._aead.decrypt(nonce, c, self._aad + bytes([flag]))
            except InvalidTag:
                continue
            if self.counter == 0 and self._want_head:
                try:
                    head = json.loads(pt.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as e:
                    raise BadFrame("frame 0 is not the head") from e
                if not isinstance(head, dict) or not isinstance(head.get("s"), int):
                    raise BadFrame("frame 0 is not the head")
                self.head = head
            self.counter += 1
            self.done = flag == FLAG_LAST
            return pt
        raise BadFrame("frame does not open")

    def close(self) -> None:
        if self._failed:
            raise BadFrame("stream failed")
        if self._buf:
            self._failed = True
            raise Truncated("partial frame at the end")
        if not self.done:
            self._failed = True
            raise Truncated("no last frame")


def open_response(key: bytes, data: bytes) -> tuple[dict, bytes]:
    """A whole response body: (head, body bytes). Raises BadFrame / Truncated."""
    opener = FrameOpener(key)
    frames = opener.feed(data)
    opener.close()
    assert opener.head is not None
    return opener.head, b"".join(frames[1:])


# ---------------------------------------------------------------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------------------------------------------------------------

class WsChannel:
    """One side of an encrypted WebSocket: one message = one sealed frame, no length prefix, a counter per direction.
    The first plaintext byte is the message type (WS_TEXT 0x01 or WS_BINARY 0x02), then the payload."""

    def __init__(self, send_key: bytes, recv_key: bytes):
        self._send = ChaCha20Poly1305(send_key)
        self._recv = ChaCha20Poly1305(recv_key)
        self.send_counter = 0
        self.recv_counter = 0

    @classmethod
    def client(cls, ctx: HpkeContext) -> "WsChannel":
        return cls(send_key=ctx.export(LABEL_WS_C2S, 32), recv_key=ctx.export(LABEL_WS_S2C, 32))

    def seal(self, payload: bytes | str) -> bytes:
        """A str goes as a text message, bytes as a binary one."""
        if isinstance(payload, str):
            pt = bytes([WS_TEXT]) + payload.encode("utf-8")
        else:
            pt = bytes([WS_BINARY]) + payload
        c = self._send.encrypt(_frame_nonce(self.send_counter), pt, AAD_WS)
        self.send_counter += 1
        return c

    def open(self, c: bytes) -> str | bytes:
        """The payload: str for a text message, bytes for a binary one."""
        try:
            pt = self._recv.decrypt(_frame_nonce(self.recv_counter), c, AAD_WS)
        except InvalidTag as e:
            raise BadFrame("ws message does not open") from e
        if not pt or pt[0] not in (WS_TEXT, WS_BINARY):
            raise BadFrame("unknown ws message type")
        if pt[0] == WS_TEXT:
            try:
                text = pt[1:].decode("utf-8")
            except UnicodeDecodeError as e:
                raise BadFrame("ws text is not UTF-8") from e
            self.recv_counter += 1
            return text
        self.recv_counter += 1
        return pt[1:]


def new_nonce() -> bytes:
    return secrets.token_bytes(NONCE_LEN)
