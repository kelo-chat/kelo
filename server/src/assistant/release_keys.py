"""The public keys the Kelo apps accept for app releases (Ed25519): key id -> base64 of the raw 32-byte public key.

The key id is the first 16 hex characters of sha256(raw public key) (release_sign.key_id). Any listed key is accepted, so there is
room for two at a time (the current key and the next one). The same map is in apps/shared/.../update/ReleaseKeys.kt: a test
(test_release_sign.py) keeps the two equal. Never put a private key here.

To change keys: add the next key to this file and to ReleaseKeys.kt, ship an app release, and only later sign with the new key.
"""

from __future__ import annotations

RELEASE_KEYS: dict[str, str] = {
    "307641d078a7bd67": "5CSNXFsfquOly92OHsU6StPwJ4KazTGIt8elvNb5UUc=",   # 2026-11-15, the first release key
}
