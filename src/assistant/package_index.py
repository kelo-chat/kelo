"""A curated catalog of packages (the groundwork of a marketplace): its index format, a strict loader, and installing one entry.

The index is one JSON document (docs/packages.md, "Catalog index"):

    {"format": 1, "name": "...", "packages": [
      {"id": "acme_notes", "kind": "connector", "name": "Acme Notes", "description": "...", "author": "...", "version": "1.2.0",
       "reviewed": true,
       "source": {"type": "git", "url": "https://github.com/someone/acme-notes", "commit": "<40 hex>", "path": "package"}},
      {"id": "picnic", "kind": "skill", ..., "source": {"type": "archive", "url": "https://example.org/picnic-1.0.0.zip",
                                                        "sha256": "<64 hex>"}}]}

Rules:
- every field is checked and an unknown one refuses the entry (a catalog is written for this server, unlike a manifest);
- a git source names a full 40-character commit: a tag or a branch can be moved to other code after review, so it is refused; the
  commit is fetched as the forge's archive of that commit (github.com, gitlab.com, codeberg.org) and the archive's own record of the
  commit (the pax header git writes) must match it;
- an archive source names its sha256; the bytes must match before anything is unpacked;
- fetching goes through netguard (https, public addresses only, no redirects, bounded time) with a size limit; unpacking refuses links,
  devices, absolute paths and '..' (zip-slip / tar-slip), executables (an executable bit, a program header or a script), too many
  members, and more unpacked data than a package may hold; only the package's own folder is written, into a temporary folder;
- the unpacked package must have the entry's id, kind and version; it is then installed with Registry.install_from_dir, which records
  the source, the content hash and the owner's approval. An update never switches source silently (install_from_dir refuses a
  different kind of source or address unless the owner confirms the switch).

No endpoint or screen uses this yet.
"""

from __future__ import annotations

import io
import json
import re
import stat
import tarfile
import tempfile
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from . import netguard
from .packages import (AUTHOR_LEN, CONNECTOR_ID, DESC_LEN, KINDS, MANIFEST, MAX_FILES, MAX_TOTAL, NAME_LEN, SKILL_FILE, SKILL_ID,
                       VERSION, Package, PackageError, _https, _line, load_package)

INDEX_FORMAT = 1
MAX_INDEX = 1024 * 1024                       # bytes of one index document
MAX_ENTRIES = 1000
MAX_DOWNLOAD = 20 * 1024 * 1024               # one archive (a whole repository at a commit, of which only the package folder is kept)
MAX_MEMBERS = 5000                            # entries in one archive
MAX_UNPACKED = 200 * 1024 * 1024              # declared sizes of all members together (bounds the work of reading past them)
FETCH_TIMEOUT = 60.0
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TOP_FIELDS = {"format", "name", "packages"}
ENTRY_FIELDS = {"id", "kind", "name", "description", "author", "version", "reviewed", "source"}
SOURCE_FIELDS = {"git": {"type", "url", "commit", "path"}, "archive": {"type", "url", "sha256", "path"}}
MOVING_REFS = ("ref", "tag", "branch", "rev", "version")
# file names and starts of files that are programs: a package is declarative and never carries one
EXEC_SUFFIXES = (".exe", ".dll", ".so", ".dylib", ".bin", ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".vbs", ".jar", ".class",
                 ".py", ".pyc", ".js", ".mjs", ".cjs", ".rb", ".pl", ".php", ".app", ".apk", ".msi", ".com", ".scr", ".wasm")
EXEC_MAGIC = (b"\x7fELF", b"MZ", b"#!", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
              b"\xca\xfe\xba\xbe", b"\x00asm")

Fetcher = Callable[[str, int], Awaitable[bytes]]   # (https URL, most bytes) -> the body; raises PackageError


@dataclass(frozen=True)
class IndexEntry:
    id: str
    kind: str
    name: str
    description: str
    author: str
    version: str
    reviewed: bool
    source: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "name": self.name, "description": self.description, "author": self.author,
                "version": self.version, "reviewed": self.reviewed, "source": dict(self.source)}


# ---------------------------------------------------------------------------------------------------------------- the index
def _subpath(v: Any, what: str) -> str:
    if v in (None, ""):
        return ""
    if not isinstance(v, str) or len(v) > 200:
        raise PackageError(f"{what} must be a folder path inside the source")
    parts = v.strip("/").split("/")
    if not all(SEGMENT.fullmatch(p) and p not in (".", "..") for p in parts):
        raise PackageError(f"{what} must be a plain relative folder path (letters, digits, '.', '_', '-', separated by '/')")
    return "/".join(parts)


def git_archive_url(url: str, commit: str) -> str:
    """The forge's archive of one commit (the only form fetched: no git protocol, no redirects). Raises PackageError for another host."""
    p = urlsplit(url)
    segs = [s for s in p.path.split("/") if s]
    if segs and segs[-1].endswith(".git"):
        segs[-1] = segs[-1][:-4]
    host = (p.hostname or "").lower()
    if host == "github.com" and len(segs) == 2:
        return f"https://codeload.github.com/{segs[0]}/{segs[1]}/tar.gz/{commit}"
    if host == "codeberg.org" and len(segs) == 2:
        return f"https://codeberg.org/{segs[0]}/{segs[1]}/archive/{commit}.tar.gz"
    if host == "gitlab.com" and 2 <= len(segs) <= 4:
        return f"https://gitlab.com/{'/'.join(segs)}/-/archive/{commit}/{segs[-1]}-{commit}.tar.gz"
    raise PackageError("a git source must be a repository on github.com, codeberg.org or gitlab.com (https://<host>/<owner>/<repo>)")


def _source(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise PackageError("source must be an object")
    kind = raw.get("type")
    if kind not in SOURCE_FIELDS:
        raise PackageError("source.type must be git or archive")
    moving = [k for k in MOVING_REFS if k in raw]
    if moving:
        raise PackageError(f"source.{moving[0]}: tags and branches can be moved to other code; pin a full 40-character commit instead")
    bad = set(raw) - SOURCE_FIELDS[kind]
    if bad:
        raise PackageError(f"source: unknown fields for {kind}: {', '.join(sorted(bad))}")
    url = _https(raw.get("url"), "source.url", query=False)
    for seg in urlsplit(url).path.split("/"):
        if seg in (".", ".."):
            raise PackageError("source.url must not contain '.' or '..' segments")
    out: dict[str, Any] = {"type": kind, "url": url}
    if kind == "git":
        commit = raw.get("commit")
        if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
            raise PackageError("source.commit must be a full 40-character lower-case commit id (a tag or a branch name is not accepted)")
        git_archive_url(url, commit)   # a host this server can fetch a commit from
        out["commit"] = commit
    else:
        digest = raw.get("sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise PackageError("source.sha256 must be 64 lower-case hex characters")
        out["sha256"] = digest
    path = _subpath(raw.get("path"), "source.path")
    if path:
        out["path"] = path
    return MappingProxyType(out)


def parse_entry(raw: Any) -> IndexEntry:
    """One catalog entry, checked strictly. Raises PackageError."""
    if not isinstance(raw, dict):
        raise PackageError("an entry must be an object")
    bad = set(raw) - ENTRY_FIELDS
    if bad:
        raise PackageError(f"unknown fields: {', '.join(sorted(bad))}")
    missing = [k for k in ("id", "kind", "name", "version", "source") if k not in raw]
    if missing:
        raise PackageError(f"missing fields: {', '.join(missing)}")
    kind = raw["kind"]
    if kind not in KINDS:
        raise PackageError(f"kind must be one of {', '.join(KINDS)}")
    pid = raw["id"]
    if not isinstance(pid, str) or not (CONNECTOR_ID if kind == "connector" else SKILL_ID).fullmatch(pid):
        raise PackageError(f"id {pid!r} is not a valid {kind} id")
    if not isinstance(raw["version"], str) or not VERSION.fullmatch(raw["version"]):
        raise PackageError("version must look like 1.2.3")
    reviewed = raw.get("reviewed", False)
    if not isinstance(reviewed, bool):
        raise PackageError("reviewed must be true or false")
    return IndexEntry(pid, kind, _line(raw["name"], "name", NAME_LEN, required=True), _line(raw.get("description"), "description", DESC_LEN),
                      _line(raw.get("author"), "author", AUTHOR_LEN), raw["version"], reviewed, _source(raw["source"]))


def parse_index(raw: bytes | str | Mapping[str, Any]) -> list[IndexEntry]:
    """A whole index, checked strictly: one bad entry refuses the index (with the entry's number and the reason). Raises PackageError."""
    if isinstance(raw, (bytes, str)):
        if len(raw) > MAX_INDEX:
            raise PackageError(f"the index is larger than {MAX_INDEX // 1024} KiB")
        try:
            raw = json.loads(raw)
        except ValueError as e:
            raise PackageError(f"the index is not valid JSON ({e})") from None
    if not isinstance(raw, dict):
        raise PackageError("the index must be one JSON object")
    bad = set(raw) - TOP_FIELDS
    if bad:
        raise PackageError(f"the index has unknown fields: {', '.join(sorted(bad))}")
    if raw.get("format") != INDEX_FORMAT:
        raise PackageError(f"the index format must be {INDEX_FORMAT}")
    _line(raw.get("name"), "name", NAME_LEN)
    items = raw.get("packages")
    if not isinstance(items, list) or len(items) > MAX_ENTRIES:
        raise PackageError(f"packages must be a list of at most {MAX_ENTRIES} entries")
    out, seen = [], set()
    for n, item in enumerate(items, 1):
        try:
            e = parse_entry(item)
        except PackageError as err:
            raise PackageError(f"entry {n}: {err}") from None
        if e.id in seen:
            raise PackageError(f"entry {n}: {e.id} is listed twice")
        seen.add(e.id)
        out.append(e)
    return out


def load_index(path: str | Path) -> list[IndexEntry]:
    """An index from a file (a link is refused)."""
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise PackageError("the index must be a plain file")
    if p.stat().st_size > MAX_INDEX:
        raise PackageError(f"the index is larger than {MAX_INDEX // 1024} KiB")
    return parse_index(p.read_bytes())


# ---------------------------------------------------------------------------------------------------------------- fetching
async def netguard_fetcher(url: str, max_bytes: int) -> bytes:
    """GET through netguard: https, the URL's own host only, public addresses, no redirects, bounded time and size."""
    host = (urlsplit(url).hostname or "").lower()
    try:
        netguard.valid_host(host)
        r = await netguard.fetch("GET", url, allowed_hosts=[host], headers={"Accept": "application/octet-stream", "User-Agent": "Kelo"},
                                 timeout=FETCH_TIMEOUT, max_body=max_bytes)
    except netguard.NetGuardError as e:
        raise PackageError(f"could not fetch the package: {e}") from None
    if r.status != 200:
        raise PackageError(f"could not fetch the package (HTTP {r.status})")
    if r.truncated:
        raise PackageError(f"the package download is larger than {max_bytes // (1024 * 1024)} MiB")
    return r.body


# ---------------------------------------------------------------------------------------------------------------- unpacking
def _safe_rel(name: str) -> PurePosixPath:
    """A member name as a safe relative path, or PackageError (absolute paths, '..', drive letters, backslashes, empty names)."""
    if not name or "\0" in name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise PackageError(f"the archive has an unsafe path: {name[:120]!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise PackageError(f"the archive has an unsafe path: {name[:120]!r}")
    return PurePosixPath(*parts)


def _no_program(rel: PurePosixPath, mode: int, head: bytes) -> None:
    if mode & 0o111:
        raise PackageError(f"{rel} is marked executable; packages may not carry programs")
    if rel.name.lower().endswith(EXEC_SUFFIXES):
        raise PackageError(f"{rel} is a program or script; packages may not carry programs")
    if head.startswith(EXEC_MAGIC):
        raise PackageError(f"{rel} is a program or script; packages may not carry programs")


class _Members:
    """Plain files of an archive as (relative path, mode, size, read()), every member checked; dirs kept only to place files."""

    def __init__(self, data: bytes):
        self.data = data
        self.commit = ""                      # git's record of the commit in a tar archive (pax global header "comment")

    def files(self) -> list[tuple[PurePosixPath, int, int, Callable[[], bytes]]]:
        if self.data[:4] == b"PK\x03\x04":
            return self._zip()
        try:
            return self._tar()
        except tarfile.TarError as e:
            raise PackageError(f"the download is not a zip or tar archive ({e})") from None

    def _tar(self) -> list[tuple[PurePosixPath, int, int, Callable[[], bytes]]]:
        tf = tarfile.open(fileobj=io.BytesIO(self.data), mode="r:*")
        out, count, declared = [], 0, 0
        while True:
            m = tf.next()
            if m is None:
                break
            count += 1
            declared += max(0, m.size)
            if count > MAX_MEMBERS:
                raise PackageError(f"the archive has more than {MAX_MEMBERS} entries")
            if declared > MAX_UNPACKED:
                raise PackageError(f"the archive unpacks to more than {MAX_UNPACKED // (1024 * 1024)} MiB")
            rel = _safe_rel(m.name)
            if m.issym() or m.islnk():
                raise PackageError(f"{rel} is a link; packages may not contain links")
            if m.isdir():
                continue
            if not m.isreg():
                raise PackageError(f"{rel} is not a plain file")
            out.append((rel, m.mode, m.size, (lambda m=m: _read(tf, m))))
        self.commit = str(tf.pax_headers.get("comment") or "").strip()
        self._tf = tf   # kept open for the readers
        return out

    def _zip(self) -> list[tuple[PurePosixPath, int, int, Callable[[], bytes]]]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(self.data))
        except zipfile.BadZipFile as e:
            raise PackageError(f"the download is not a valid zip archive ({e})") from None
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise PackageError(f"the archive has more than {MAX_MEMBERS} entries")
        if sum(max(0, i.file_size) for i in infos) > MAX_UNPACKED:
            raise PackageError(f"the archive unpacks to more than {MAX_UNPACKED // (1024 * 1024)} MiB")
        out = []
        for i in infos:
            rel = _safe_rel(i.filename)
            mode = (i.external_attr >> 16) & 0xFFFF
            if i.flag_bits & 0x1:
                raise PackageError(f"{rel} is encrypted")
            if stat.S_ISLNK(mode):
                raise PackageError(f"{rel} is a link; packages may not contain links")
            if i.is_dir():
                continue
            if mode and not stat.S_ISREG(mode):
                raise PackageError(f"{rel} is not a plain file")
            out.append((rel, mode & 0o7777, i.file_size, (lambda i=i: _read_zip(zf, i))))
        return out


def _read(tf: tarfile.TarFile, m: tarfile.TarInfo) -> bytes:
    f = tf.extractfile(m)
    if f is None:
        raise PackageError(f"{m.name} could not be read")
    data = f.read(MAX_TOTAL + 1)
    if len(data) != m.size or len(data) > MAX_TOTAL:
        raise PackageError(f"{m.name} is not the size the archive says")
    return data


def _read_zip(zf: zipfile.ZipFile, i: zipfile.ZipInfo) -> bytes:
    with zf.open(i) as f:
        data = f.read(MAX_TOTAL + 1)
    if len(data) != i.file_size or len(data) > MAX_TOTAL:
        raise PackageError(f"{i.filename} is not the size the archive says")
    return data


def unpack(data: bytes, dest: Path, *, strip_top: bool | None, subpath: str = "") -> tuple[Path, str]:
    """Write the package's own files from an archive into `dest` (a new empty folder). `strip_top`: True removes the one top folder
    every member is in (a forge's archive of a commit), None removes it only when the package is not at the top. `subpath`: the
    package's folder inside. Returns (the package folder, git's commit record or "")."""
    members = _Members(data)
    files = members.files()
    if not files:
        raise PackageError("the archive is empty")
    tops = {rel.parts[0] for rel, *_ in files}
    nested = all(len(rel.parts) > 1 for rel, *_ in files)
    at_top = any(len(rel.parts) == 1 and rel.name in (MANIFEST, SKILL_FILE) for rel, *_ in files)
    if strip_top is True and not (len(tops) == 1 and nested):
        raise PackageError("the archive does not have the one top folder a commit archive has")
    strip = (len(tops) == 1 and nested) if strip_top is True else (strip_top is None and len(tops) == 1 and nested and not at_top)
    prefix = PurePosixPath(*subpath.split("/")) if subpath else None
    root = Path(dest) / "package"
    root.mkdir(parents=True)
    count, total = 0, 0
    for rel, mode, size, read in files:
        r = PurePosixPath(*rel.parts[1:]) if strip else rel
        if prefix is not None:
            if r.parts[: len(prefix.parts)] != prefix.parts or len(r.parts) == len(prefix.parts):
                continue
            r = PurePosixPath(*r.parts[len(prefix.parts):])
        if not r.parts:
            continue
        count += 1
        total += size
        if count > MAX_FILES:
            raise PackageError(f"the package has more than {MAX_FILES} files")
        if total > MAX_TOTAL:
            raise PackageError(f"the package is larger than {MAX_TOTAL // 1024} KiB in all")
        body = read()
        _no_program(r, mode, body[:8])
        target = root.joinpath(*r.parts)
        if root.resolve() not in target.resolve().parents:
            raise PackageError(f"the archive has an unsafe path: {str(r)[:120]!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as f:
            f.write(body)
        target.chmod(0o644)
    if count == 0:
        raise PackageError("the package folder is not in the archive" + (f" (looked in {subpath})" if subpath else ""))
    return root, members.commit


# ---------------------------------------------------------------------------------------------------------------- installing
async def install_entry(registry: Any, entry: IndexEntry, *, fetcher: Fetcher | None = None, replace: bool = False,
                        switch_source: bool = False) -> Package:
    """Fetch one catalog entry, check it against the entry (commit or sha256, id, kind, version) and install it. Raises PackageError."""
    if not isinstance(entry, IndexEntry):
        entry = parse_entry(entry)
    fetch = fetcher or netguard_fetcher
    src = entry.source
    if src["type"] == "git":
        data = await fetch(git_archive_url(src["url"], src["commit"]), MAX_DOWNLOAD)
    else:
        data = await fetch(src["url"], MAX_DOWNLOAD)
    if len(data) > MAX_DOWNLOAD:
        raise PackageError(f"the package download is larger than {MAX_DOWNLOAD // (1024 * 1024)} MiB")
    if src["type"] == "archive" and sha256(data).hexdigest() != src["sha256"]:
        raise PackageError("the download does not match the catalog's sha256; nothing was installed")
    with tempfile.TemporaryDirectory(prefix="kelo-pkg-") as tmp:
        folder, commit = unpack(data, Path(tmp), strip_top=True if src["type"] == "git" else None, subpath=src.get("path", ""))
        if src["type"] == "git" and commit != src["commit"]:
            raise PackageError("the archive is not of the catalog's commit (or does not say which commit it is); nothing was installed")
        pkg = load_package(folder, source="installed", check_name=False)
        if pkg.id != entry.id or pkg.kind != entry.kind:
            raise PackageError(f"the package is {pkg.kind} {pkg.id!r}, but the catalog lists {entry.kind} {entry.id!r}")
        if pkg.version != entry.version:
            raise PackageError(f"the package says version {pkg.version}, but the catalog lists {entry.version}")
        record = {**dict(src), "version": entry.version}
        return registry.install_from_dir(folder, replace=replace, source=record, reviewed=entry.reviewed, switch_source=switch_source,
                                         expect_id=entry.id)


__all__ = ["Fetcher", "IndexEntry", "git_archive_url", "install_entry", "load_index", "netguard_fetcher", "parse_entry", "parse_index",
           "unpack"]
