"""Signatures for the Kelo Nest index: minisign (Ed25519), read and written here without the minisign program.

Why minisign: one small public key in the server pins who may publish the index; a signature is a short text file next to the index
(index.json.minisig); verifying needs no network, no certificate authority and no transparency log, and the format is a published,
widely used standard (https://jedisct1.github.io/minisign/) that operators can also make and check with the `minisign` command. The
alternative, sigstore, ties a signature to an identity through an online certificate authority and log, which a self-hosted server
offline or behind a firewall cannot always reach, for no gain when there is exactly one publisher (the Nest operator).

Formats (all base64 lines, as minisign writes them):
- public key:  "Ed" | key id (8) | Ed25519 public key (32)
- secret key:  "Ed" | kdf "\\0\\0" (not encrypted) | "B2" | salt (32) | opslimit (8) | memlimit (8) | key id (8) | secret key (64) | checksum (32)
               (an encrypted key, made by `minisign -G` without -W, is not read here: sign with the minisign program instead)
- signature:   "untrusted comment: ..." / "ED" | key id (8) | Ed25519(BLAKE2b-512(file)) / "trusted comment: ..." /
               Ed25519(signature | trusted comment)
The legacy "Ed" signature (over the file itself, not its hash) is verified too.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ALG = b"Ed"
ALG_HASHED = b"ED"
KDF_NONE = b"\x00\x00"
CKSUM = b"B2"


class SignatureError(ValueError):
    """A key or signature that cannot be read, or a signature that does not match."""


@dataclass(frozen=True)
class PublicKey:
    key_id: bytes
    key: bytes

    def line(self) -> str:
        return base64.b64encode(ALG + self.key_id + self.key).decode()

    def file(self) -> str:
        return f"untrusted comment: minisign public key {key_id_text(self.key_id)}\n{self.line()}\n"


@dataclass(frozen=True)
class SecretKey:
    key_id: bytes
    seed: bytes          # the Ed25519 seed (the first half of libsodium's 64-byte secret key)
    public: bytes

    def file(self) -> str:
        sk = self.seed + self.public
        checksum = hashlib.blake2b(ALG + self.key_id + sk, digest_size=32).digest()
        raw = ALG + KDF_NONE + CKSUM + bytes(32) + bytes(8) + bytes(8) + self.key_id + sk + checksum
        return f"untrusted comment: minisign secret key (not encrypted)\n{base64.b64encode(raw).decode()}\n"

    def public_key(self) -> PublicKey:
        return PublicKey(self.key_id, self.public)


def key_id_text(key_id: bytes) -> str:
    """The key id as minisign prints it (a little-endian number in upper-case hex)."""
    return key_id[::-1].hex().upper()


def _b64(line: str, what: str) -> bytes:
    try:
        return base64.b64decode(line.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError(f"{what} is not base64") from None


def _lines(text: str) -> list[str]:
    return [ln.rstrip("\r") for ln in (text or "").split("\n") if ln.strip()]


def read_public_key(text: str) -> PublicKey:
    """A public key: the key file, or just its base64 line."""
    lines = [ln for ln in _lines(text) if not ln.startswith("untrusted comment:")]
    if len(lines) != 1:
        raise SignatureError("a public key is one base64 line (with an optional comment line above)")
    raw = _b64(lines[0], "the public key")
    if len(raw) != 42 or raw[:2] != ALG:
        raise SignatureError("not a minisign Ed25519 public key")
    return PublicKey(raw[2:10], raw[10:])


def read_secret_key(text: str) -> SecretKey:
    lines = [ln for ln in _lines(text) if not ln.startswith("untrusted comment:")]
    if len(lines) != 1:
        raise SignatureError("a secret key file has one base64 line")
    raw = _b64(lines[0], "the secret key")
    if len(raw) != 158 or raw[:2] != ALG or raw[4:6] != CKSUM:
        raise SignatureError("not a minisign Ed25519 secret key")
    if raw[2:4] != KDF_NONE:
        raise SignatureError("this secret key is encrypted with a password; sign with the minisign program (minisign -Sm index.json)")
    key_id, sk, checksum = raw[54:62], raw[62:126], raw[126:158]
    if hashlib.blake2b(ALG + key_id + sk, digest_size=32).digest() != checksum:
        raise SignatureError("the secret key's checksum does not match (the file is damaged)")
    seed, public = sk[:32], sk[32:]
    if Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw() != public:
        raise SignatureError("the secret key's two halves do not belong together")
    return SecretKey(key_id, seed, public)


def generate() -> SecretKey:
    priv = Ed25519PrivateKey.generate()
    return SecretKey(secrets.token_bytes(8), priv.private_bytes_raw(), priv.public_key().public_bytes_raw())


def sign(data: bytes, key: SecretKey, *, trusted_comment: str = "", untrusted_comment: str = "signature from the Kelo Nest key") -> str:
    """A minisign signature file (pre-hashed "ED" form) for `data`."""
    if "\n" in trusted_comment or "\r" in trusted_comment or "\n" in untrusted_comment:
        raise SignatureError("comments are one line")
    priv = Ed25519PrivateKey.from_private_bytes(key.seed)
    sig = priv.sign(hashlib.blake2b(data, digest_size=64).digest())
    global_sig = priv.sign(sig + trusted_comment.encode())
    return (f"untrusted comment: {untrusted_comment}\n{base64.b64encode(ALG_HASHED + key.key_id + sig).decode()}\n"
            f"trusted comment: {trusted_comment}\n{base64.b64encode(global_sig).decode()}\n")


@dataclass(frozen=True)
class Verified:
    key_id: bytes
    trusted_comment: str


def verify(data: bytes, signature: str, keys: list[PublicKey]) -> Verified:
    """Check a minisign signature over `data` with one of `keys` (chosen by key id), and its trusted comment. Raises SignatureError."""
    lines = _lines(signature)
    if len(lines) != 4 or not lines[0].startswith("untrusted comment:") or not lines[2].startswith("trusted comment: "):
        raise SignatureError("not a minisign signature file (four lines: comment, signature, trusted comment, its signature)")
    raw = _b64(lines[1], "the signature")
    if len(raw) != 74 or raw[:2] not in (ALG, ALG_HASHED):
        raise SignatureError("not a minisign Ed25519 signature")
    key_id, sig = raw[2:10], raw[10:]
    key = next((k for k in keys if k.key_id == key_id), None)
    if key is None:
        raise SignatureError(f"signed with key {key_id_text(key_id)}, which this server does not trust")
    pub = Ed25519PublicKey.from_public_bytes(key.key)
    message = hashlib.blake2b(data, digest_size=64).digest() if raw[:2] == ALG_HASHED else data
    try:
        pub.verify(sig, message)
    except InvalidSignature:
        raise SignatureError("the signature does not match the file (it was changed, or signed with another key)") from None
    comment = lines[2][len("trusted comment: "):]
    global_sig = _b64(lines[3], "the trusted comment's signature")
    try:
        pub.verify(global_sig, sig + comment.encode())
    except InvalidSignature:
        raise SignatureError("the trusted comment was changed") from None
    return Verified(key_id, comment)


__all__ = ["PublicKey", "SecretKey", "SignatureError", "Verified", "generate", "key_id_text", "read_public_key", "read_secret_key", "sign",
           "verify"]
