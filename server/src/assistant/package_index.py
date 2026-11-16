"""Fetching and unpacking a package archive safely (GitHub links, drafts).

- git_archive_url: the forge's archive of one commit (github.com, gitlab.com, codeberg.org); only https, no git protocol;
- netguard_fetcher: GET through netguard (https, public addresses, no redirects, bounded time and size);
- unpack: writes only the package's own files into a new folder. It refuses links, devices, absolute paths and '..' (zip-slip /
  tar-slip), programs (an executable bit, a program header or a script), too many members, and more unpacked data than a package may
  hold. A commit archive's record of its commit (the pax header git writes) is returned, so the caller can check it.
"""

from __future__ import annotations

import io
import re
import stat
import tarfile
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from . import netguard
from .packages import MANIFEST, MAX_FILES, MAX_TOTAL, SKILL_FILE, PackageError

MAX_DOWNLOAD = 20 * 1024 * 1024               # one archive (a whole repository at a commit, of which only the package folder is kept)
MAX_MEMBERS = 5000                            # entries in one archive
MAX_UNPACKED = 200 * 1024 * 1024              # declared sizes of all members together (bounds the work of reading past them)
FETCH_TIMEOUT = 60.0
# file names and starts of files that are programs: a package is declarative and never carries one
EXEC_SUFFIXES = (".exe", ".dll", ".so", ".dylib", ".bin", ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".vbs", ".jar", ".class",
                 ".py", ".pyc", ".js", ".mjs", ".cjs", ".rb", ".pl", ".php", ".app", ".apk", ".msi", ".com", ".scr", ".wasm")
EXEC_MAGIC = (b"\x7fELF", b"MZ", b"#!", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
              b"\xca\xfe\xba\xbe", b"\x00asm")

Fetcher = Callable[[str, int], Awaitable[bytes]]   # (https URL, most bytes) -> the body; raises PackageError


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


__all__ = ["Fetcher", "git_archive_url", "netguard_fetcher", "unpack"]
