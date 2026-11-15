"""The Kelo Nest index: the public, signed list of packages other people published for Kelo (served from kelo.chat), checked strictly.

    GET https://kelo.chat/nest/index.json          the index (format 2, below)
    GET https://kelo.chat/nest/index.json.minisig  its minisign signature (nest_sign.py), made by the Nest operator's key

The index is built from the Git registry (kelo-chat/nest: one folder per package with one file per version, merged by pull request)
by `kelo nest index build` and signed by `kelo nest index sign` (nest_cli.py). A server trusts it only when:
- the signature verifies with one of the trusted public keys (TRUSTED_KEYS, or ASSISTANT_NEST_KEYS);
- it has not expired (`expires_at`: a server kept on an old index by someone in the middle stops trusting it: the freeze guard);
- its `sequence` is not lower than the highest one this server has seen (an older signed index cannot be replayed: the rollback guard);
- every field checks out (an unknown field refuses the index: it is written for this server).

    {"format": 2, "name": "Kelo Nest", "sequence": 42, "generated_at": 1762600000, "expires_at": 1765200000,
     "publishers": {"acme": {"name": "Acme", "verified": true, "homepage": "https://acme.example"}},
     "packages": [
       {"id": "habit-tracker", "publisher": "acme", "name": {"en": "Habit tracker", "ko": "..."}, "description": {"en": "..."},
        "kinds": ["skill", "extension"], "verified": true, "homepage": "https://...", "versions": [
          {"version": "1.1.0", "published_at": 1762500000, "content_hash": "<64 hex>", "permissions": {...},
           "source": {"type": "git", "url": "https://github.com/acme/habit-tracker", "commit": "<40 hex>", "path": "package"}}]}],
     "withdrawn": [{"id": "bad-thing", "versions": "*", "hashes": [], "reason": "Sent data to a host it did not declare."}]}

Per package version, three things pin the exact files: the source (a full git commit, or an archive's sha256), the `content_hash`
(packages.content_hash over every file: checked after unpacking, whatever the source), and the `permissions` summary (the effective
permissions of those files: checked after unpacking too, so what the owner is shown before installing is what the package gets).
`withdrawn` is the kill-switch list: a matching installed package is turned off on every server that reads the index (nest.py).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .nest_manifest import PERMISSION_FIELDS, parse_permissions, pick_text, plain
from .nest_sign import PublicKey, SignatureError, read_public_key, verify
from .package_index import _source
from .packages import AUTHOR_LEN, CONNECT_TYPES, CONNECTOR_ID, DESC_LEN, KINDS, LANG, NAME_LEN, SKILL_ID, VERSION, PackageError, _https, _line

FORMAT = 2
INDEX_URL = "https://kelo.chat/nest/index.json"
SIGNATURE_SUFFIX = ".minisig"
# The Nest operator's public keys (minisign base64 lines). Empty until the operator makes the key (`kelo nest keygen`) and puts its
# public line here; until then a server reads only indexes signed by a key it was given in ASSISTANT_NEST_KEYS.
TRUSTED_KEYS: tuple[str, ...] = ()
MAX_INDEX = 4 * 1024 * 1024
MAX_SIGNATURE = 4096
MAX_PACKAGES = 2000
MAX_VERSIONS = 50
MAX_WITHDRAWN = 2000
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PUBLISHER_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
TOP = {"format", "name", "sequence", "generated_at", "expires_at", "publishers", "packages", "withdrawn"}
ENTRY = {"id", "publisher", "name", "description", "kinds", "verified", "homepage", "versions"}
VERSION_FIELDS = {"version", "published_at", "content_hash", "permissions", "source", "connect"}
PUBLISHER = {"name", "verified", "homepage"}
WITHDRAWN = {"id", "versions", "hashes", "reason"}


def version_key(v: str) -> tuple[Any, ...]:
    """Order of versions: 1.10.0 > 1.9.0, and a release above its pre-releases (1.0.0 > 1.0.0-beta)."""
    core, _, pre = v.partition("-")
    core = core.split("+")[0]
    nums = tuple(int(x) for x in core.split("."))
    return (*nums, 1 if not pre else 0, pre)


def _i18n(v: Any, what: str, limit: int, *, required: bool = True) -> Mapping[str, str]:
    if isinstance(v, str):
        v = {"en": v}
    if not isinstance(v, dict) or (required and "en" not in v) or len(v) > 60:
        raise PackageError(f"{what} must be an object of texts by language with an \"en\" text")
    out = {}
    for lang, s in v.items():
        if not isinstance(lang, str) or not LANG.fullmatch(lang):
            raise PackageError(f"{what}: {lang!r} is not a language tag")
        out[lang] = _line(s, f"{what}.{lang}", limit, required=True)
    return MappingProxyType(out)


def _time(v: Any, what: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not 1_500_000_000 <= v <= 10_000_000_000:
        raise PackageError(f"{what} must be unix seconds")
    return v


@dataclass(frozen=True)
class NestVersion:
    version: str
    published_at: int
    content_hash: str
    permissions: Mapping[str, Any]
    source: Mapping[str, Any]
    connect: str = ""                          # a connector's connect type (mcp, oauth, key, browser, unsupported), else ""

    def public(self) -> dict[str, Any]:
        return {"version": self.version, "published_at": self.published_at, "content_hash": self.content_hash,
                "permissions": plain(self.permissions), "source": dict(self.source), **({"connect": self.connect} if self.connect else {})}


@dataclass(frozen=True)
class NestEntry:
    id: str
    publisher: str
    publisher_name: str
    publisher_verified: bool
    name: Mapping[str, str]
    description: Mapping[str, str]
    kinds: tuple[str, ...]
    verified: bool
    homepage: str
    versions: tuple[NestVersion, ...] = field(default_factory=tuple)   # newest first

    @property
    def latest(self) -> NestVersion:
        return self.versions[0]

    def version(self, v: str | None) -> NestVersion | None:
        if not v:
            return self.latest
        return next((x for x in self.versions if x.version == v), None)

    def summary(self, lang: str | None = None) -> dict[str, Any]:
        return {"id": self.id, "name": pick_text(self.name, lang), "description": pick_text(self.description, lang), "kinds": list(self.kinds),
                "publisher": {"id": self.publisher, "name": self.publisher_name, "verified": self.publisher_verified},
                "verified": self.verified, "homepage": self.homepage, "latest": self.latest.version}


@dataclass(frozen=True)
class NestIndex:
    name: str
    sequence: int
    generated_at: int
    expires_at: int
    entries: Mapping[str, NestEntry]
    withdrawn: Mapping[str, Mapping[str, Any]]
    digest: str                                # sha256 of the index bytes

    def search(self, query: str = "", kind: str = "", lang: str | None = None) -> list[NestEntry]:
        q = " ".join((query or "").lower().split())
        out = []
        for e in self.entries.values():
            if e.id in self.withdrawn and self.withdrawn[e.id].get("versions") == "*":
                continue
            if kind and kind not in e.kinds:
                continue
            if q:
                hay = " ".join([e.id, e.publisher_name, *e.name.values(), *e.description.values()]).lower()
                if not all(w in hay for w in q.split()):
                    continue
            out.append(e)
        return sorted(out, key=lambda e: (not e.verified, pick_text(e.name, lang).lower()))


def _entry(raw: Any, publishers: Mapping[str, Mapping[str, Any]]) -> NestEntry:
    if not isinstance(raw, dict):
        raise PackageError("an entry must be an object")
    bad = set(raw) - ENTRY
    if bad:
        raise PackageError(f"unknown fields: {', '.join(sorted(bad))}")
    kinds = raw.get("kinds")
    if not isinstance(kinds, list) or not kinds or not all(k in KINDS for k in kinds) or len(set(kinds)) != len(kinds):
        raise PackageError(f"kinds must be a list of {', '.join(KINDS)}")
    base = next((k for k in kinds if k != "extension"), "extension")
    pid = raw.get("id")
    if not isinstance(pid, str) or not (CONNECTOR_ID if base == "connector" else SKILL_ID).fullmatch(pid):
        raise PackageError(f"id {pid!r} is not a valid {base} id")
    pub = raw.get("publisher")
    if pub not in publishers:
        raise PackageError(f"publisher {pub!r} is not in the publishers list")
    verified = raw.get("verified", False)
    if not isinstance(verified, bool):
        raise PackageError("verified must be true or false")
    versions_raw = raw.get("versions")
    if not isinstance(versions_raw, list) or not 1 <= len(versions_raw) <= MAX_VERSIONS:
        raise PackageError(f"versions must be a list of 1-{MAX_VERSIONS} versions")
    versions, seen = [], set()
    for n, v in enumerate(versions_raw, 1):
        if not isinstance(v, dict) or set(v) - VERSION_FIELDS or not {"version", "content_hash", "permissions", "source"} <= set(v):
            raise PackageError(f"version {n}: needs version, content_hash, permissions and source (and nothing else but published_at)")
        if not isinstance(v["version"], str) or not VERSION.fullmatch(v["version"]) or v["version"] in seen:
            raise PackageError(f"version {n}: version must look like 1.2.3 and be listed once")
        seen.add(v["version"])
        if not isinstance(v["content_hash"], str) or not SHA256.fullmatch(v["content_hash"]):
            raise PackageError(f"version {n}: content_hash must be 64 lower-case hex characters")
        perms = parse_permissions(v["permissions"])
        if set(v["permissions"]) != set(PERMISSION_FIELDS):
            raise PackageError(f"version {n}: permissions must list every field ({', '.join(PERMISSION_FIELDS)})")
        connect = v.get("connect", "")
        if connect not in ("", *CONNECT_TYPES) or (connect and "connector" not in kinds) or ("connector" in kinds and not connect):
            raise PackageError(f"version {n}: connect must be the connector's connect type ({', '.join(CONNECT_TYPES)}), and only for a connector")
        versions.append(NestVersion(v["version"], _time(v.get("published_at", 1_500_000_000), f"version {n}: published_at"), v["content_hash"],
                                    perms or MappingProxyType({}), _source(v["source"]), connect))
    versions.sort(key=lambda x: version_key(x.version), reverse=True)
    p = publishers[pub]
    desc = _i18n(raw["description"], "description", DESC_LEN) if raw.get("description") else MappingProxyType({"en": ""})
    return NestEntry(pid, pub, p["name"], p["verified"], _i18n(raw.get("name"), "name", NAME_LEN), desc, tuple(kinds), verified, _https(raw["homepage"], "homepage") if raw.get("homepage") else "", tuple(versions))


def parse(data: bytes, *, now: float | None = None, min_sequence: int = 0) -> NestIndex:
    """The index document, checked (not its signature: see load()). Raises PackageError."""
    if len(data) > MAX_INDEX:
        raise PackageError(f"the Nest index is larger than {MAX_INDEX // (1024 * 1024)} MiB")
    try:
        raw = json.loads(data)
    except ValueError as e:
        raise PackageError(f"the Nest index is not valid JSON ({e})") from None
    if not isinstance(raw, dict):
        raise PackageError("the Nest index must be one JSON object")
    bad = set(raw) - TOP
    if bad:
        raise PackageError(f"the Nest index has unknown fields: {', '.join(sorted(bad))}")
    if raw.get("format") != FORMAT:
        raise PackageError(f"the Nest index format must be {FORMAT}")
    seq = raw.get("sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise PackageError("sequence must be a positive whole number")
    generated, expires = _time(raw.get("generated_at"), "generated_at"), _time(raw.get("expires_at"), "expires_at")
    now = time.time() if now is None else now
    if expires <= now:
        raise PackageError("the Nest index has expired (it may be an old copy someone is replaying); try again later")
    if seq < min_sequence:
        raise PackageError(f"the Nest index is older than one this server already read (sequence {seq} < {min_sequence}); it was refused")
    pubs_raw = raw.get("publishers") or {}
    if not isinstance(pubs_raw, dict) or len(pubs_raw) > MAX_PACKAGES:
        raise PackageError("publishers must be an object")
    publishers: dict[str, Mapping[str, Any]] = {}
    for pid, p in pubs_raw.items():
        if not isinstance(pid, str) or not PUBLISHER_ID.fullmatch(pid) or not isinstance(p, dict) or set(p) - PUBLISHER:
            raise PackageError(f"publisher {pid!r} is not valid")
        if not isinstance(p.get("verified", False), bool):
            raise PackageError(f"publisher {pid}: verified must be true or false")
        publishers[pid] = MappingProxyType({"name": _line(p.get("name"), f"publisher {pid}: name", AUTHOR_LEN, required=True),
                                            "verified": bool(p.get("verified", False)),
                                            "homepage": _https(p["homepage"], f"publisher {pid}: homepage") if p.get("homepage") else ""})
    items = raw.get("packages")
    if not isinstance(items, list) or len(items) > MAX_PACKAGES:
        raise PackageError(f"packages must be a list of at most {MAX_PACKAGES} entries")
    entries: dict[str, NestEntry] = {}
    for n, item in enumerate(items, 1):
        try:
            e = _entry(item, publishers)
        except PackageError as err:
            raise PackageError(f"entry {n}: {err}") from None
        if e.id in entries:
            raise PackageError(f"entry {n}: {e.id} is listed twice")
        entries[e.id] = e
    withdrawn: dict[str, Mapping[str, Any]] = {}
    w_raw = raw.get("withdrawn") or []
    if not isinstance(w_raw, list) or len(w_raw) > MAX_WITHDRAWN:
        raise PackageError("withdrawn must be a list")
    for n, w in enumerate(w_raw, 1):
        if not isinstance(w, dict) or set(w) - WITHDRAWN or not isinstance(w.get("id"), str) or not (SKILL_ID.fullmatch(w["id"]) or CONNECTOR_ID.fullmatch(w["id"])):
            raise PackageError(f"withdrawn {n}: needs an id (and versions, hashes, reason)")
        versions = w.get("versions", "*")
        if versions != "*" and (not isinstance(versions, list) or not all(isinstance(v, str) and VERSION.fullmatch(v) for v in versions)):
            raise PackageError(f"withdrawn {n}: versions must be \"*\" or a list of versions")
        hashes = w.get("hashes") or []
        if not isinstance(hashes, list) or not all(isinstance(h, str) and SHA256.fullmatch(h) for h in hashes):
            raise PackageError(f"withdrawn {n}: hashes must be content hashes")
        withdrawn[w["id"]] = MappingProxyType({"versions": versions if versions == "*" else list(versions), "hashes": list(hashes),
                                               "reason": _line(w.get("reason"), f"withdrawn {n}: reason", DESC_LEN)})
    return NestIndex(_line(raw.get("name"), "name", NAME_LEN) or "Kelo Nest", seq, generated, expires, MappingProxyType(entries),
                     MappingProxyType(withdrawn), hashlib.sha256(data).hexdigest())


def trusted_keys(extra: str = "") -> list[PublicKey]:
    """The keys an index may be signed with: TRUSTED_KEYS and the comma-separated base64 lines of `extra` (ASSISTANT_NEST_KEYS)."""
    out = []
    for line in [*TRUSTED_KEYS, *(x for x in (extra or "").split(",") if x.strip())]:
        try:
            out.append(read_public_key(line))
        except SignatureError:
            continue
    return out


def load(data: bytes, signature: str, keys: list[PublicKey], *, now: float | None = None, min_sequence: int = 0) -> NestIndex:
    """A signed index: the signature first (nothing of an unsigned or tampered index is read), then the document. Raises PackageError."""
    if not keys:
        raise PackageError("this server trusts no Nest key yet, so it cannot read the Nest index")
    if len(signature) > MAX_SIGNATURE:
        raise PackageError("the Nest index signature is too large")
    try:
        verify(data, signature, keys)
    except SignatureError as e:
        raise PackageError(f"the Nest index signature is not valid: {e}") from None
    return parse(data, now=now, min_sequence=min_sequence)


__all__ = ["FORMAT", "INDEX_URL", "NestEntry", "NestIndex", "NestVersion", "TRUSTED_KEYS", "load", "parse", "trusted_keys", "version_key"]
