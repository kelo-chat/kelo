"""Writes docs/security/approval-sign-vectors.json: the shared test vectors for signed payment approvals (approval_sign.py).

The key pair is a TEST key derived from a fixed label: its private part lives only here, is used for these vectors and is never
used for anything real. The apps only need the public key and the signatures in the file.

  uv run python server/tests/approval_sign_vectors_gen.py --write   # (re)writes the file; signatures are randomised, so run it once
  uv run python server/tests/approval_sign_vectors_gen.py --check   # exit 1 when a vector does not verify
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assistant import approval_sign  # noqa: E402

FILE = Path(__file__).resolve().parents[2] / "docs" / "security" / "approval-sign-vectors.json"
LABEL = b"kelo approval-sign TEST key v1 (not a real key)"
APPROVALS = [   # (approval_id, decision, issued_at)
    ("3f9c0a1e5b7d42c8a6e1f0b9d2c4a7e3", True, 1789000000),
    ("b1e4d7a20c9f4e6b8d3a5c7e9f1b2d4a", True, 1789000123),
    ("6a2c8e4f0b1d3c5e7a9b2d4f6e8c0a1b", False, 1789003600),
]


def test_key() -> ec.EllipticCurvePrivateKey:
    order = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551   # P-256 group order
    d = int.from_bytes(hashlib.sha256(LABEL).digest(), "big") % (order - 1) + 1
    return ec.derive_private_key(d, ec.SECP256R1())


def generate() -> dict:
    key = test_key()
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    public_key = base64.b64encode(spki).decode()
    approvals = []
    for approval_id, approve, issued_at in APPROVALS:
        msg = approval_sign.approval_message(approval_id, approve, issued_at)
        sig = base64.b64encode(key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        approvals.append({"approval_id": approval_id, "decision": "approve" if approve else "deny", "issued_at": issued_at,
                          "message_hex": msg.hex(), "sig_b64": sig})
    first = approvals[0]
    msg_later = approval_sign.approval_message(first["approval_id"], True, first["issued_at"] + 1)
    flipped = bytearray(base64.b64decode(first["sig_b64"]))
    flipped[10] ^= 0x01   # inside r of the DER signature
    bad = [
        {"name": "issued_at_changed", "message_hex": msg_later.hex(), "sig_b64": first["sig_b64"], "expect": "bad_signature"},
        {"name": "signature_byte_flipped", "message_hex": first["message_hex"], "sig_b64": base64.b64encode(bytes(flipped)).decode(),
         "expect": "bad_signature"},
    ]
    return {"format": 1, "suite": "approval-sign v1", "alg": "ES256", "test_key_only": True,
            "public_key_spki_b64": public_key, "approvals": approvals, "bad": bad}


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def check(doc: dict) -> list[str]:
    problems = []
    pub = doc["public_key_spki_b64"]
    for a in doc["approvals"]:
        msg = approval_sign.approval_message(a["approval_id"], a["decision"] == "approve", a["issued_at"])
        if msg.hex() != a["message_hex"]:
            problems.append(f"message differs for {a['approval_id']}")
        if not approval_sign.verify(pub, bytes.fromhex(a["message_hex"]), a["sig_b64"]):
            problems.append(f"valid vector does not verify: {a['approval_id']}")
    for b in doc["bad"]:
        if approval_sign.verify(pub, bytes.fromhex(b["message_hex"]), b["sig_b64"]):
            problems.append(f"bad vector verifies: {b['name']}")
    return problems


if __name__ == "__main__":
    if "--write" in sys.argv:
        FILE.write_text(dumps(generate()), encoding="utf-8")
        print("wrote", FILE)
    doc = json.loads(FILE.read_text(encoding="utf-8"))
    problems = check(doc)
    for p in problems:
        print("PROBLEM", p)
    sys.exit(1 if problems else 0)
