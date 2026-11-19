"""Signing app releases: each row of the download list (latest.json) carries an Ed25519 signature the apps check.

The signed text (UTF-8, "\\n" line ends, one final "\\n"; identical in apps/shared/.../ReleaseSignature.kt):

    kelo-release-v1
    platform=<android | windows | windows-msi | windows-server-msi | mac | mac-server-bundle>
    file=<file>
    version=<version>
    version_code=<integer, or empty>
    size=<integer>
    sha256=<lowercase hex>
    commit=<40 lowercase hex of the public source commit, or empty>

The signature is the 64 raw bytes over that text, base64 (standard, padded). The key id is the first 16 hex characters of
sha256(raw 32-byte public key). The private key lives on the operator's computer only: never in a container, never on the state
volume, never inside the compose folder.

STORE ROWS (latest.json "store"): the newest live store release of an app, signed with the same key over its own text, which starts
with a different prefix so that no store signature verifies as a file row or a notices list, and back:

    kelo-store-v1
    platform=<android | ios | desktop>
    channel=<play | appstore | msstore>          (the one store channel of that platform, STORE_CHANNELS)
    version=<MAJOR.MINOR.PATCH>
    build=<integer>
    min_server=<MAJOR.MINOR.PATCH, or empty>
    date=<YYYY-MM-DD>
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import release_keys
from . import version as release_version
from .release_keys import RELEASE_KEYS

PLATFORM_KEYS = ("android", "windows", "windows-msi", "windows-server-msi", "mac", "mac-server-bundle")
# the app platforms that have a store, and the one store channel each: the rows of latest.json "store" (see the module doc)
STORE_CHANNELS = {"android": "play", "ios": "appstore", "desktop": "msstore"}
DEFAULT_KEY_PATH = Path("~/.kelo-release/signing.key")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
KEY_ID = re.compile(r"^[0-9a-f]{16}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.-]{0,39}$")


class SigningKeyError(Exception):
    """A signing key problem the operator can fix; the message says how."""


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def statement(platform: str, row: Mapping[str, Any]) -> bytes:
    """The exact bytes that are signed for one row. Raises ValueError when a field is malformed."""
    file, version, size, sha = row.get("file"), row.get("version"), row.get("size"), row.get("sha256")
    code, commit = row.get("version_code"), row.get("commit")
    if platform not in PLATFORM_KEYS:
        raise ValueError("platform")
    if not (isinstance(file, str) and _FILE.fullmatch(file)):
        raise ValueError("file")
    if not (isinstance(version, str) and _VERSION.fullmatch(version)):
        raise ValueError("version")
    if code is not None and not (_int(code) and 0 < code < 2**31):
        raise ValueError("version_code")
    if not (_int(size) and size > 0):
        raise ValueError("size")
    if not (isinstance(sha, str) and HEX64.fullmatch(sha)):
        raise ValueError("sha256")
    if commit not in (None, "") and not (isinstance(commit, str) and HEX40.fullmatch(commit)):
        raise ValueError("commit")
    return (f"kelo-release-v1\nplatform={platform}\nfile={file}\nversion={version}\nversion_code={'' if code is None else code}\n"
            f"size={size}\nsha256={sha}\ncommit={commit or ''}\n").encode("utf-8")


def store_statement(platform: str, row: Mapping[str, Any]) -> bytes:
    """The exact bytes signed for one store row (see the module doc). Raises ValueError when a field is malformed."""
    channel, version, build, min_server, day = row.get("channel"), row.get("version"), row.get("build"), row.get("min_server"), row.get("date")
    if platform not in STORE_CHANNELS:
        raise ValueError("platform")
    if channel != STORE_CHANNELS[platform]:
        raise ValueError("channel")
    if not (isinstance(version, str) and version == version.strip()):
        raise ValueError("version")
    try:
        release_version.parse(version)
    except ValueError:
        raise ValueError("version") from None
    if not (_int(build) and 0 < build <= 2**63 - 1):
        raise ValueError("build")
    if min_server in (None, ""):
        min_server = ""
    elif not (isinstance(min_server, str) and min_server == min_server.strip()):
        raise ValueError("min_server")
    else:
        try:
            release_version.parse(min_server)
        except ValueError:
            raise ValueError("min_server") from None
    try:
        if not (isinstance(day, str) and len(day) == 10 and date.fromisoformat(day).isoformat() == day):
            raise ValueError("date")
    except ValueError:
        raise ValueError("date") from None
    return (f"kelo-store-v1\nplatform={platform}\nchannel={channel}\nversion={version}\nbuild={build}\n"
            f"min_server={min_server}\ndate={day}\n").encode("utf-8")


def sign_store_row(platform: str, row: Mapping[str, Any], key_path: str | Path, root: Path | None = None) -> dict[str, Any]:
    """The store row plus "sig" and "key" (the row itself is not changed)."""
    key = load_key(Path(key_path), root)
    sig = key.sign(store_statement(platform, row))
    return {**row, "sig": base64.b64encode(sig).decode("ascii"), "key": key_id(_raw_public(key))}


def verify_store_row(platform: str, row: Mapping[str, Any], keys: Mapping[str, str] | None = None) -> bool:
    """True only when the store row's signature checks against a listed key. False on anything malformed; never raises."""
    try:
        text = store_statement(platform, row)
    except (ValueError, TypeError, AttributeError):
        return False
    return _check(text, row.get("key"), row.get("sig"), release_keys.RELEASE_KEYS if keys is None else keys)


def _check(text: bytes, kid: Any, sig_b64: Any, keys: Mapping[str, str]) -> bool:
    """The shared end of every verify: the key id is one of `keys`, the key matches its id, and the signature checks. Never raises."""
    try:
        if not (isinstance(kid, str) and isinstance(sig_b64, str)) or kid not in keys:
            return False
        sig = base64.b64decode(sig_b64, validate=True)
        pub = base64.b64decode(keys[kid], validate=True)
        if len(sig) != 64 or len(pub) != 32 or key_id(pub) != kid:
            return False
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, text)
        return True
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False


def key_id(pub_raw: bytes) -> str:
    return hashlib.sha256(pub_raw).hexdigest()[:16]


def _raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def sign_row(platform: str, row: Mapping[str, Any], key_path: str | Path, root: Path | None = None) -> dict[str, Any]:
    """The row plus "sig" and "key" (the row's own "commit", if any, is part of the signed text)."""
    key = load_key(Path(key_path), root)
    sig = key.sign(statement(platform, row))
    return {**row, "sig": base64.b64encode(sig).decode("ascii"), "key": key_id(_raw_public(key))}


def verify_row(platform: str, row: Mapping[str, Any], keys: Mapping[str, str] | None = None) -> bool:
    """True only when the row's signature checks against a listed key. False on anything malformed; never raises."""
    try:
        text = statement(platform, row)
    except (ValueError, TypeError, AttributeError):
        return False
    return _check(text, row.get("key"), row.get("sig"), release_keys.RELEASE_KEYS if keys is None else keys)


# ---------------------------------------------------------------------------------------------------------------- the key file
def new_key(path: str | Path) -> tuple[str, str]:
    """Makes a new signing key at `path` (PKCS8 PEM, unencrypted, mode 0600, in a 0700 folder when the folder is new). Refuses to
    overwrite. Returns (key id, base64 public key); the private part is never returned or printed."""
    path = Path(path).expanduser()
    if path.exists() or path.is_symlink():
        raise SigningKeyError(f"{path} already exists: a key is never overwritten (choose another --path, or remove it yourself)")
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, parents=True)
        path.parent.chmod(0o700)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    path.chmod(0o600)
    raw = _raw_public(key)
    return key_id(raw), base64.b64encode(raw).decode("ascii")


def _mounted_paths(root: Path) -> list[Path]:
    """Host folders the compose file mounts into containers (bind mounts like `./state:/state` or `/srv/x:/y`)."""
    out: list[Path] = []
    try:
        text = (root / "compose.yml").read_text(encoding="utf-8")
    except OSError:
        return out
    for m in re.finditer(r"^\s*-\s*[\"']?([^\s:\"']+):[^\s\"']+", text, re.MULTILINE):
        src = m.group(1)
        if src.startswith(("./", "../", "/", "~")):
            out.append((root / Path(src).expanduser()).resolve())
    return out


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def check_location(path: Path, root: Path | None) -> Path:
    """The resolved key path, or SigningKeyError when it sits where a container could reach it."""
    path = Path(path).expanduser().resolve()
    root = (root or Path.cwd()).resolve()
    if _inside(path, root):
        raise SigningKeyError(f"{path} is inside {root} (the compose folder, which docker may mount): keep the signing key outside it, "
                        f"for example {DEFAULT_KEY_PATH}")
    for mounted in _mounted_paths(root):
        if _inside(path, mounted):
            raise SigningKeyError(f"{path} is inside {mounted}, which docker mounts into a container: keep the signing key elsewhere")
    if _inside(path, Path("/state")):
        raise SigningKeyError(f"{path} is a state volume path: keep the signing key on the operator's own computer")
    return path


def notices_statement(notices: list) -> bytes:
    """The canonical bytes over which the notices feed is signed: a fixed prefix, then UTF-8 JSON of the notices list (sort_keys,
    compact). The prefix keeps a notices signature from ever being valid for anything else signed with the release key."""
    import json as _json
    return b"kelo-notices-v1\n" + _json.dumps(notices, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_notices(notices: list, key_path: str | Path, root: Path | None = None) -> tuple[str, str]:
    """Sign the notices list; returns (base64 sig, key id). The key is loaded from key_path (same rules as sign_row)."""
    key = load_key(Path(key_path), root)
    sig = key.sign(notices_statement(notices))
    return base64.b64encode(sig).decode("ascii"), key_id(_raw_public(key))


def verify_notices(notices: list, sig_b64: str, kid: str, keys: "dict[str, str] | None" = None) -> bool:
    """True only when sig_b64 is a valid Ed25519 signature over the notices list by a known key. Never raises."""
    try:
        text = notices_statement(notices)
    except (ValueError, TypeError):
        return False
    return _check(text, kid, sig_b64, release_keys.RELEASE_KEYS if keys is None else keys)


def load_key(path: str | Path, root: Path | None = None) -> Ed25519PrivateKey:
    """Reads the signing key; refuses one inside the compose folder / a docker-mounted path, and one others can read."""
    path = check_location(Path(path), root)
    try:
        st = path.stat()
    except OSError:
        raise SigningKeyError(f"no signing key at {path}: make one with `assistant release-key new`") from None
    if st.st_mode & 0o077:
        raise SigningKeyError(f"{path} can be read by other users (mode {st.st_mode & 0o777:03o}): run `chmod 600 {path}`")
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as e:
        raise SigningKeyError(f"{path} is not an unencrypted Ed25519 private key ({type(e).__name__})") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningKeyError(f"{path} is not an Ed25519 key")
    return key
