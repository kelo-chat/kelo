"""Signed payment approvals (docs/approval-signing.md): the phone signs the answer to a payment card with a key that lives only in its
hardware keystore (Android Keystore / Secure Enclave), so a leaked device token alone cannot approve a payment.

Pure functions, no web framework here:

- `is_payment(event)`: which approval cards are payments (the kinds are listed in the doc and in the report).
- `approval_message(approval_id, approve, issued_at)`: the exact bytes the phone signs:
  UTF-8 of "kelo-approval v1\\n" + approval_id + "\\n" + ("approve" | "deny") + "\\n" + str(issued_at).
- `key_message(alg, public_key)`: the bytes an old key signs to replace a device's approval key.
- `check_public_key` / `verify`: ECDSA P-256 with SHA-256, DER signatures, keys as base64 SubjectPublicKeyInfo DER.

Shared test vectors: docs/security/approval-sign-vectors.json (server/tests/test_approval_sign.py checks them).
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

ALG = "ES256"
MESSAGE_PREFIX = "kelo-approval v1\n"
KEY_MESSAGE_PREFIX = "kelo-approval-key v1\n"
MAX_KEY_BYTES = 256          # a P-256 SubjectPublicKeyInfo is 91 bytes; anything much bigger is not that key
MAX_SIG_BYTES = 256          # a DER ECDSA P-256 signature is 70-72 bytes


class BadKey(ValueError):
    """A public key that is not a usable ES256 key (wrong encoding, not P-256, too big)."""


def is_payment(event: dict[str, Any]) -> bool:
    """A payment approval: the purchase card (checkout_review or confirm_purchase, which carry "payment"), or a browser click the page judged to be the
    one that pays (commit "pay"). Denials and every other kind are not payments."""
    return bool(event.get("payment")) or event.get("name") in ("confirm_purchase", "checkout_review") or event.get("commit") == "pay"


def approval_message(approval_id: str, approve: bool, issued_at: int) -> bytes:
    decision = "approve" if approve else "deny"
    return f"{MESSAGE_PREFIX}{approval_id}\n{decision}\n{int(issued_at)}".encode("utf-8")


def key_message(alg: str, public_key: str) -> bytes:
    return f"{KEY_MESSAGE_PREFIX}{alg}\n{public_key}".encode("utf-8")


def _b64decode(value: str, limit: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > limit * 2:
        raise BadKey("not base64")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise BadKey("not base64") from None


def load_public_key(public_key: str) -> ec.EllipticCurvePublicKey:
    """The P-256 key from base64 SubjectPublicKeyInfo DER. Raises BadKey for anything else."""
    der = _b64decode(public_key, MAX_KEY_BYTES)
    if len(der) > MAX_KEY_BYTES:
        raise BadKey("key too large")
    try:
        key = load_der_public_key(der)
    except (ValueError, TypeError):
        raise BadKey("not a public key (SubjectPublicKeyInfo DER)") from None
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise BadKey("not a P-256 key")
    return key


def check_public_key(alg: str, public_key: str) -> None:
    if alg != ALG:
        raise BadKey(f"alg must be {ALG}")
    load_public_key(public_key)


def verify(public_key: str, message: bytes, sig: str) -> bool:
    """True when `sig` (base64 DER ECDSA over SHA-256 of `message`) was made by the key. Never raises for a bad signature or key."""
    try:
        key = load_public_key(public_key)
        der = _b64decode(sig, MAX_SIG_BYTES)
    except BadKey:
        return False
    if len(der) > MAX_SIG_BYTES:
        return False
    try:
        key.verify(der, message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
