"""Packages: skills and connectors that live as folders, so something another person wrote is picked up without a code change or a
restart. The format is in docs/packages.md; examples are in examples/packages/.

Where packages come from:
- built in: the skill folders shipped in content/skills/ (SKILL.md, no manifest needed) and the connectors defined in code (catalog.py
  with presets.py, mcp_oauth.py, oauth_connect.py, mail.py). The built-in connectors are not files: they are listed here as packages
  with source "builtin" and a `connect.type` naming the code that connects them, so everything is in one list (`connectors()`).
- installed: one folder per package under the state folder (`<state>/packages/<id>/`), holding `kelo-package.json` and, for a skill,
  SKILL.md. Installed packages are untrusted data written by other people: they never run code here (a connector is declarative only:
  a hosted MCP server, an API key with the hosts it may reach, a browser site, or unsupported with a reason), and their text reaches the
  agent only marked as third-party and cut to a fixed length.

Rules:
- an id is lower-case letters, digits and '_' (connectors) or '-' (skills); the folder's name must be the manifest's id;
- a symlink anywhere in a package, a special file, too many or too large files, a bad manifest, or a connector field that is not
  understood refuses the package (it is left out and the reason is kept in `problems`); an unknown top-level manifest field is ignored
  with a warning so newer manifests still load;
- on an id conflict the built-in wins and the installed package is left out;
- `refresh()` compares a cheap fingerprint of the folders (names, sizes, modification times) and reloads only when it changed. Every
  reader calls it, so a folder dropped in shows up on the next turn and on the next GET /api/connectors, and a removed one disappears.

`install_from_dir()` and `remove()` are the hooks a later install endpoint (or a marketplace) will call.

Source and approval: every installed package has a source record (SOURCES_FILE, beside the package folders) with where it came from,
its version, when it was installed and the content hash (every path with the hash of its bytes) the owner approved. A package whose
files no longer match is held back (`pending`, with what changed: hosts, key placement and addresses first) until `approve()`.
Installing from a curated catalog (fetch, verify, unpack safely) is in package_index.py.

Custom skills (source "custom"): skills written on this server, one folder each under `<state>/custom_skills/<id>/` (SKILL.md with
front matter and optional extra .md files; nothing else). They load through the same checks as other skill folders, with their own
source record (SOURCES_FILE in that folder) holding the content hash and the text the owner approved. A custom skill is used only
while its files match that approval: a folder changed or added outside the app is held back (`custom_pending`) until the owner
approves it again or puts the approved text back. A built-in skill's id is refused there; on a clash with an installed package the
custom skill wins. Proposals (the agent's drafts waiting for the owner) are in custom_skills.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace as dc_replace
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from . import i18n
from .catalog import CATALOG, Service
from .credentials import clean_hosts, clean_placement
from .netguard import NetGuardError, valid_host

log = logging.getLogger("packages")

MANIFEST = "kelo-package.json"
SKILL_FILE = "SKILL.md"
FORMAT = 1                                   # the manifest format this server reads ("format" in the manifest; missing = 1)
KINDS = ("skill", "connector", "extension")  # extension: screens for the apps and entries on this server only (nest_manifest.py)
CONNECT_TYPES = ("mcp", "oauth", "key", "browser", "unsupported")   # what an installed connector may declare (no code)
BUILTIN_TYPES = ("device", "mail", "google")               # ways that need code; only built-in connectors use them

SKILL_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
CONNECTOR_ID = re.compile(r"^[a-z](?:[a-z0-9_]{0,46}[a-z0-9])$")
VERSION = re.compile(r"^\d{1,6}\.\d{1,6}\.\d{1,6}(?:[-+][0-9A-Za-z.-]{1,32})?$")
LANG = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
REGION = re.compile(r"^[A-Z]{2}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SOURCES_FILE = ".kelo-sources.json"           # in the installed folder: where each package came from and what the owner approved
NEST_STATE = ".kelo-nest.json"                # in the installed folder: packages the owner turned off, withdrawn ones, pins (nest.py)
ITEM_PREFIX = "pkg-"                          # vault items of installed connectors: pkg-<id>, apart from every built-in item
ICON_PREFIX = "pkg_"                          # icon names of installed connectors: pkg_<id> (same URL form as the built-in icons)

# sizes (bytes / counts)
MAX_MANIFEST = 64 * 1024
MAX_TEXT_FILE = 128 * 1024                    # SKILL.md and each extra .md file
MAX_ICON = 512 * 1024
MAX_FILES = 64                                # files in one package, every subfolder counted
MAX_TOTAL = 2 * 1024 * 1024                   # bytes in one package
MAX_DEPTH = 4
MAX_PACKAGES = 200                            # installed packages read; the rest are left out
CUSTOM_FILES = 16                             # .md files in one custom skill
CUSTOM_TOTAL = 512 * 1024                     # bytes in one custom skill (its approved text is kept in the source record)
SOURCES = ("builtin", "custom", "installed")  # where a skill comes from, in the order they are listed
# text lengths (characters)
NAME_LEN, DESC_LEN, AUTHOR_LEN, LINE_LEN, NOTE_LEN = 60, 300, 80, 200, 400
SKILL_DESC_LEN = 1024                         # SKILL.md descriptions run long in other agents' skills; the prompt index cuts them

TOP_FIELDS = {"format", "id", "kind", "version", "name", "description", "author", "homepage", "license", "icon", "strings",
              "skill", "connector", "permissions", "ui", "store", "provides"}
STRING_FIELDS = {"name", "description", "blurb", "reason", "help", "key_label"}
CONNECTOR_FIELDS = {"regions", "featured", "connect"}
SKILL_FIELDS = {"requires"}
CONNECT_FIELDS = {
    "mcp": {"type", "url", "blurb"},
    "key": {"type", "hosts", "placement", "base_url", "verify", "docs", "api", "help", "blurb", "key_label", "mcp_url", "key_pattern"},
    "oauth": {"type", "authorize_url", "token_url", "scopes", "hosts", "base_url", "client_id", "client", "params", "verify", "docs", "api",
              "help", "blurb"},
    "browser": {"type", "url", "blurb", "blocks_bots"},
    "unsupported": {"type", "reason"},
}


class PackageError(ValueError):
    """A package that cannot be used, with the reason (shown to whoever installs it)."""


@dataclass(frozen=True)
class Package:
    id: str
    kind: str                                 # "skill" | "connector"
    version: str
    name: str
    description: str
    source: str                               # "builtin" | "custom" | "installed"
    author: str = ""
    homepage: str = ""
    path: Path | None = None                  # the folder (None for connectors defined in code)
    icon: str = ""                            # file name of the icon inside the folder
    strings: Mapping[str, Mapping[str, Any]] = field(default_factory=lambda: MappingProxyType({}))
    requires: tuple[str, ...] = ()            # skill: service ids of which at least one must be connected
    files: tuple[str, ...] = ()               # skill: extra .md files next to SKILL.md
    regions: tuple[str, ...] = ()             # connector: countries where it is widely used (featured there)
    featured: bool = False                    # connector: the author asks to be listed among the featured ones
    connect: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))   # connector: how it connects ({"type": ...})
    content_hash: str = ""                    # installed: content_hash() of the folder, the one the owner approved
    reviewed: bool = False                    # installed: came from a curated catalog entry marked reviewed (else "unreviewed")
    # Kelo Nest (nest_manifest.py): what the package may do, the screens it adds to the apps, the entries it keeps on this server
    permissions: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    ui: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    store: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    declared: bool = False                    # the manifest has a permissions section (else they are what it uses)
    provides: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))   # connector: the mail / calendar it holds and how

    @property
    def kinds(self) -> tuple[str, ...]:
        """What the package adds: its kind, and "extension" when it also adds screens (a skill or connector with a ui section)."""
        return (self.kind, *(("extension",) if self.ui and self.kind != "extension" else ()))

    @property
    def installed(self) -> bool:
        return self.source == "installed"

    @property
    def item(self) -> str:
        """The vault item an installed connector keeps its key or tokens in."""
        return ITEM_PREFIX + self.id

    @property
    def icon_name(self) -> str:
        return ICON_PREFIX + self.id if self.icon else ""

    def text(self, key: str, lang: str | None = None) -> Any:
        """A field in the reader's language when the package has it (`strings.<lang>.<key>`), else the English one."""
        for cand in [*i18n.candidates(lang or i18n.current()), i18n.DEFAULT]:
            got = (self.strings.get(cand) or {}).get(key)
            if got:
                return got
        if key in ("name", "description"):
            return getattr(self, key)
        return self.connect.get(key, "") if key != "help" else self.connect.get("help", ())

    def public(self) -> dict[str, Any]:
        """What a list of packages shows (no paths)."""
        out = {"id": self.id, "kind": self.kind, "version": self.version, "name": self.text("name"), "description": self.text("description"),
               "author": self.author, "source": self.source}
        if self.kind == "connector":
            out["connect"] = self.connect.get("type", "")
        if self.installed:
            out["review"] = "reviewed" if self.reviewed else "unreviewed"
        if self.ui:
            out["kinds"] = list(self.kinds)
        return out


# ---------------------------------------------------------------------------------------------------------------- validation
def _line(v: Any, what: str, limit: int, *, required: bool = False) -> str:
    """One line of plain text: printable, no control characters, at most `limit` characters."""
    if v is None or v == "":
        if required:
            raise PackageError(f"{what} is required")
        return ""
    if not isinstance(v, str):
        raise PackageError(f"{what} must be text")
    s = " ".join(v.split())
    if not s.isprintable():
        raise PackageError(f"{what} has characters that are not printable")
    if len(s) > limit:
        raise PackageError(f"{what} is longer than {limit} characters")
    if required and not s:
        raise PackageError(f"{what} is required")
    return s


def _lines(v: Any, what: str, *, count: int = 8, limit: int = LINE_LEN) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or len(v) > count:
        raise PackageError(f"{what} must be a list of at most {count} lines")
    return tuple(_line(x, what, limit, required=True) for x in v)


def _https(url: Any, what: str, *, hosts: Iterable[str] | None = None, query: bool = True) -> str:
    s = _line(url, what, 400, required=True)
    p = urlsplit(s)
    try:
        host = valid_host(p.hostname or "")
    except NetGuardError as e:
        raise PackageError(f"{what}: {e}") from None
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443) or p.fragment or (p.query and not query):
        raise PackageError(f"{what} must be a plain https:// address" + ("" if query else " without a query string"))
    if hosts is not None and host not in hosts:
        raise PackageError(f"{what} must be on one of the declared hosts ({', '.join(hosts)})")
    if "{" in s or "}" in s:
        raise PackageError(f"{what} must not contain braces")
    return s


def _strings(raw: Any) -> Mapping[str, Mapping[str, Any]]:
    if raw is None:
        return MappingProxyType({})
    if not isinstance(raw, dict) or len(raw) > 60:
        raise PackageError("strings must be an object of at most 60 languages")
    out: dict[str, Mapping[str, Any]] = {}
    for lang, vals in raw.items():
        if not isinstance(lang, str) or not LANG.fullmatch(lang) or not isinstance(vals, dict):
            raise PackageError(f"strings: {lang!r} is not a language tag with an object of texts")
        bad = set(vals) - STRING_FIELDS
        if bad:
            raise PackageError(f"strings.{lang}: unknown fields {', '.join(sorted(bad))}")
        one: dict[str, Any] = {}
        for k, v in vals.items():
            if k == "help":
                one[k] = _lines(v, f"strings.{lang}.help")
            else:
                one[k] = _line(v, f"strings.{lang}.{k}", {"name": NAME_LEN, "description": DESC_LEN}.get(k, NOTE_LEN))
        out[lang] = MappingProxyType(one)
    return MappingProxyType(out)


def _connect(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise PackageError("connector.connect must be an object")
    kind = raw.get("type")
    if any(k in raw for k in ("client_secret", "secret", "password", "api_key", "token")):
        raise PackageError("connector.connect must not hold a secret: a client secret, key or password never goes in a package")
    if kind not in CONNECT_TYPES:
        raise PackageError(f"connector.connect.type must be one of {', '.join(CONNECT_TYPES)} (other ways need code and are built in only)")
    bad = set(raw) - CONNECT_FIELDS[kind]
    if bad:
        raise PackageError(f"connector.connect: unknown fields for {kind}: {', '.join(sorted(bad))}")
    out: dict[str, Any] = {"type": kind}
    if kind == "mcp":
        out["url"] = _https(raw.get("url"), "connect.url")
        out["hosts"] = (urlsplit(out["url"]).hostname or "",)
        out["blurb"] = _line(raw.get("blurb"), "connect.blurb", NOTE_LEN)
    elif kind == "key":
        try:
            hosts = tuple(clean_hosts(raw.get("hosts")))
            placement = clean_placement(raw.get("placement") or "bearer_header")
        except NetGuardError as e:
            raise PackageError(f"connect: {e}") from None
        if placement["type"] == "url_path_segment":
            raise PackageError("connect.placement: a key in the URL path is not supported for packages (use a header or a query parameter)")
        out.update(hosts=hosts, placement=MappingProxyType(placement))
        out["base_url"] = _https(raw.get("base_url"), "connect.base_url", hosts=hosts) if raw.get("base_url") else ""
        out["verify"] = _https(raw.get("verify"), "connect.verify", hosts=hosts) if raw.get("verify") else ""
        out["mcp_url"] = _https(raw.get("mcp_url"), "connect.mcp_url", hosts=hosts, query=False) if raw.get("mcp_url") else ""
        out["docs"] = _https(raw.get("docs"), "connect.docs") if raw.get("docs") else ""
        out["api"] = _line(raw.get("api"), "connect.api", NOTE_LEN)
        out["help"] = _lines(raw.get("help"), "connect.help")
        out["blurb"] = _line(raw.get("blurb"), "connect.blurb", NOTE_LEN)
        out["key_label"] = _line(raw.get("key_label"), "connect.key_label", NAME_LEN)
        # the shape of this service's keys, so one pasted into chat is caught before it reaches a model (secret_guard.py: a fixed
        # prefix and bounded character classes only)
        out["key_pattern"] = ""
        if raw.get("key_pattern") not in (None, ""):
            from .secret_guard import check_key_pattern
            try:
                out["key_pattern"] = check_key_pattern(raw.get("key_pattern"))
            except ValueError as e:
                raise PackageError(f"connect.{e}") from None
    elif kind == "oauth":
        # an OAuth 2 API: the user signs in on the service's page (authorization code with PKCE). A public client id, or the owner's
        # own (client "owner": they register an app on the service and type its id in). A client secret never goes in a package.
        try:
            hosts = tuple(clean_hosts(raw.get("hosts")))
        except NetGuardError as e:
            raise PackageError(f"connect: {e}") from None
        out["hosts"] = hosts
        out["authorize_url"] = _https(raw.get("authorize_url"), "connect.authorize_url")
        out["token_url"] = _https(raw.get("token_url"), "connect.token_url", query=False)
        scopes = raw.get("scopes") or []
        if not isinstance(scopes, list) or len(scopes) > 30 or not all(isinstance(x, str) and re.fullmatch(r"[\x21\x23-\x5b\x5d-\x7e]{1,120}", x) for x in scopes):
            raise PackageError("connect.scopes must be a list of up to 30 OAuth scopes")
        out["scopes"] = tuple(scopes)
        client = raw.get("client", "public")
        if client not in ("public", "owner"):
            raise PackageError("connect.client must be public (a client id in the package) or owner (the owner types their own)")
        out["client"] = client
        cid = raw.get("client_id")
        if client == "public":
            out["client_id"] = _line(cid, "connect.client_id", 200, required=True)
        elif cid not in (None, ""):
            raise PackageError("connect.client_id is only for client public; with client owner the owner types their own")
        else:
            out["client_id"] = ""
        params = raw.get("params") or {}
        if not isinstance(params, dict) or len(params) > 6 or not all(
                isinstance(k, str) and re.fullmatch(r"[a-z_]{1,30}", k) and k not in ("client_id", "client_secret", "redirect_uri", "state", "code_challenge",
                                                                                     "code_challenge_method", "response_type", "scope")
                and isinstance(v, str) and len(v) <= 100 for k, v in params.items()):
            raise PackageError("connect.params must be up to 6 extra sign-in parameters (not the ones the server sets itself)")
        out["params"] = MappingProxyType(dict(params))
        out["base_url"] = _https(raw.get("base_url"), "connect.base_url", hosts=hosts) if raw.get("base_url") else ""
        out["verify"] = _https(raw.get("verify"), "connect.verify", hosts=hosts) if raw.get("verify") else ""
        out["docs"] = _https(raw.get("docs"), "connect.docs") if raw.get("docs") else ""
        out["api"] = _line(raw.get("api"), "connect.api", NOTE_LEN)
        out["help"] = _lines(raw.get("help"), "connect.help")
        out["blurb"] = _line(raw.get("blurb"), "connect.blurb", NOTE_LEN)
        out["placement"] = MappingProxyType({"type": "bearer_header"})
    elif kind == "browser":
        out["url"] = _https(raw.get("url"), "connect.url")
        out["blurb"] = _line(raw.get("blurb"), "connect.blurb", NOTE_LEN)
        if not isinstance(raw.get("blocks_bots", False), bool):
            raise PackageError("connect.blocks_bots must be true or false")
        out["blocks_bots"] = bool(raw.get("blocks_bots", False))
    else:
        out["reason"] = _line(raw.get("reason"), "connect.reason", NOTE_LEN, required=True)
    return MappingProxyType(out)


def _scan(folder: Path) -> tuple[list[tuple[str, int]], int]:
    """Every file in a package folder as (relative path, size), refusing symlinks, special files, deep trees and size limits.
    Returns (files, fingerprint-ish total size)."""
    if folder.is_symlink() or not folder.is_dir():
        raise PackageError("a package must be a real folder (not a link)")
    files: list[tuple[str, int]] = []
    total = 0
    stack = [(folder, 0)]
    while stack:
        d, depth = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                st = e.stat(follow_symlinks=False)
                rel = os.path.relpath(e.path, folder)
                if stat.S_ISLNK(st.st_mode):
                    raise PackageError(f"{rel} is a link; packages may not contain links")
                if stat.S_ISDIR(st.st_mode):
                    if depth + 1 >= MAX_DEPTH:
                        raise PackageError(f"{rel}: folders nest too deep")
                    stack.append((Path(e.path), depth + 1))
                    continue
                if not stat.S_ISREG(st.st_mode):
                    raise PackageError(f"{rel} is not a plain file")
                files.append((rel, st.st_size))
                total += st.st_size
                if len(files) > MAX_FILES:
                    raise PackageError(f"more than {MAX_FILES} files")
                if total > MAX_TOTAL:
                    raise PackageError(f"larger than {MAX_TOTAL // 1024} KiB in all")
    return files, total


def _read_text(path: Path, limit: int, what: str) -> str:
    if path.stat().st_size > limit:
        raise PackageError(f"{what} is larger than {limit // 1024} KiB")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise PackageError(f"{what} is not UTF-8 text") from None


def _skill_files(folder: Path, files: list[tuple[str, int]]) -> tuple[str, ...]:
    extra = []
    for rel, size in files:
        if "/" in rel or os.sep in rel or not rel.endswith(".md") or rel == SKILL_FILE or not FILE_NAME.fullmatch(rel):
            continue
        if size > MAX_TEXT_FILE:
            raise PackageError(f"{rel} is larger than {MAX_TEXT_FILE // 1024} KiB")
        extra.append(rel)
    return tuple(sorted(extra))


# ---------------------------------------------------------------------------------------------------------------- content and approval
SENSITIVE = ("type", "url", "hosts", "placement", "base_url", "verify", "mcp_url", "authorize_url", "token_url", "scopes", "client_id",
             "client")   # connector fields that decide where a key or a sign-in goes


def file_hashes(folder: Path) -> dict[str, str]:
    """sha256 of every file in a package folder by its relative path ('/'-separated), sorted. The same walk as loading (_scan):
    nothing is left out, and a link, a special file or a size limit refuses the package."""
    files, _ = _scan(Path(folder))
    out: dict[str, str] = {}
    for rel, _size in files:
        h = hashlib.sha256()
        with open(Path(folder) / rel, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        out[rel.replace(os.sep, "/")] = h.hexdigest()
    return dict(sorted(out.items()))


def content_hash(hashes: Mapping[str, str]) -> str:
    """One hash over a package's files: every path (sorted) with the hash of its bytes."""
    h = hashlib.sha256(b"kelo-package-content-1\n")
    for rel in sorted(hashes):
        h.update(rel.encode("utf-8") + b"\0" + hashes[rel].encode() + b"\n")
    return h.hexdigest()


def _plain(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {str(k): _plain(x) for k, x in sorted(v.items())}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def sensitive_fields(p: Package) -> dict[str, Any]:
    """What an approval is really about besides the text: a connector's way of connecting, hosts, key placement and addresses; a
    skill's required connectors."""
    perms = {"permissions": _plain(dict(p.permissions))} if p.permissions else {}
    if p.kind == "connector":
        return {"kind": p.kind, **{k: _plain(p.connect[k]) for k in SENSITIVE if k in p.connect and p.connect[k] not in ("", ())}, **perms}
    if p.kind == "extension":
        return {"kind": p.kind, **perms}
    return {"kind": p.kind, "requires": list(p.requires), **perms}


def describe_changes(approved: Mapping[str, Any] | None, files: Mapping[str, str], sensitive: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What changed since the approval: connector fields first (marked sensitive: hosts, key placement, MCP address ...), then files."""
    approved = approved or {}
    out: list[dict[str, Any]] = []
    old_s = approved.get("sensitive") or {}
    for k in sorted(set(old_s) | set(sensitive)):
        if old_s.get(k) != sensitive.get(k):
            out.append({"what": "connect" if k not in ("kind", "requires", "permissions") else k, "field": k, "before": old_s.get(k),
                        "after": sensitive.get(k), "sensitive": True})
    old_f = approved.get("files") or {}
    for rel in sorted(set(old_f) | set(files)):
        if rel not in old_f:
            out.append({"what": "file", "path": rel, "change": "added", "sensitive": False})
        elif rel not in files:
            out.append({"what": "file", "path": rel, "change": "removed", "sensitive": False})
        elif old_f[rel] != files[rel]:
            out.append({"what": "file", "path": rel, "change": "changed", "sensitive": rel == MANIFEST})
    return out


def _same_source(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Whether an update comes from where the package came from (the kind of source and its address; a commit or a file hash may
    move, the place may not)."""
    a, b = a or {}, b or {}
    if a.get("type", "local") != b.get("type", "local"):
        return False
    if a.get("type") == "archive":   # an archive's address names its version; the host it comes from may not change
        return (urlsplit(str(a.get("url") or "")).hostname or "") == (urlsplit(str(b.get("url") or "")).hostname or "")
    return a.get("url") == b.get("url")


def load_package(folder: Path, *, source: str = "installed", warnings: list[str] | None = None, check_name: bool = True) -> Package:
    """Read and check one package folder. A folder with SKILL.md and no manifest is a skill (the SKILL.md style other agents use):
    its id is the folder's name and its name and description come from SKILL.md's front matter. Raises PackageError."""
    from .home import _as_list, front_matter   # home imports this module for Skills

    folder = Path(folder)
    warn = warnings if warnings is not None else []
    files, _ = _scan(folder)
    names = {rel for rel, _ in files}
    manifest: dict[str, Any] = {}
    if MANIFEST in names:
        raw = _read_text(folder / MANIFEST, MAX_MANIFEST, MANIFEST)
        try:
            manifest = json.loads(raw)
        except ValueError as e:
            raise PackageError(f"{MANIFEST} is not valid JSON ({e})") from None
        if not isinstance(manifest, dict):
            raise PackageError(f"{MANIFEST} must hold one JSON object")
    elif SKILL_FILE not in names:
        raise PackageError(f"no {MANIFEST} (and no {SKILL_FILE})")
    for k in sorted(set(manifest) - TOP_FIELDS):
        warn.append(f"{folder.name}: unknown field {k!r} ignored")
    fmt = manifest.get("format", FORMAT)
    if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt < 1 or fmt > FORMAT:
        raise PackageError(f"format {fmt!r} is not one this server reads (it reads {FORMAT})")
    kind = manifest.get("kind", "skill" if not manifest else None)
    if kind not in KINDS:
        raise PackageError(f"kind must be one of {', '.join(KINDS)}")
    pid = manifest.get("id", folder.name if not manifest else None)
    if not isinstance(pid, str) or not (CONNECTOR_ID if kind == "connector" else SKILL_ID).fullmatch(pid):
        rule = "lower-case letters, digits and '-'" if kind != "connector" else "lower-case letters, digits and '_', starting with a letter"
        raise PackageError(f"id {pid!r} is not valid ({rule}, 2-48 characters)")
    if check_name and pid != folder.name:
        raise PackageError(f"the folder is named {folder.name!r} but the manifest's id is {pid!r}")
    version = manifest.get("version", "0.0.0" if not manifest else None)
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise PackageError("version must look like 1.2.3")
    strings = _strings(manifest.get("strings"))
    common: dict[str, Any] = {
        "id": pid, "kind": kind, "version": version, "source": source, "path": folder, "strings": strings,
        "author": _line(manifest.get("author"), "author", AUTHOR_LEN),
        "homepage": _https(manifest.get("homepage"), "homepage") if manifest.get("homepage") else "",
    }
    icon = manifest.get("icon") or ""
    if icon:
        if not isinstance(icon, str) or not FILE_NAME.fullmatch(icon) or not icon.endswith(".png") or icon not in names:
            raise PackageError("icon must name a .png file at the top of the package")
        if (folder / icon).stat().st_size > MAX_ICON:
            raise PackageError(f"the icon is larger than {MAX_ICON // 1024} KiB")
        with open(folder / icon, "rb") as f:
            if f.read(8) != PNG_MAGIC:
                raise PackageError("the icon is not a PNG picture")
        common["icon"] = icon
    if kind == "skill":
        if SKILL_FILE not in names:
            raise PackageError(f"a skill needs {SKILL_FILE}")
        meta, _ = front_matter(_read_text(folder / SKILL_FILE, MAX_TEXT_FILE, SKILL_FILE))
        sec = manifest.get("skill") or {}
        if not isinstance(sec, dict) or set(sec) - SKILL_FIELDS:
            raise PackageError(f"skill may only hold {', '.join(sorted(SKILL_FIELDS))}")
        requires = sec.get("requires", _as_list(meta.get("requires")))
        if not isinstance(requires, list) or len(requires) > 20 or not all(isinstance(r, str) and re.fullmatch(r"[a-z0-9_-]{1,48}", r) for r in requires):
            raise PackageError("skill.requires must be a list of connector ids")
        if "connector" in manifest:
            raise PackageError("a skill has no connector section")
        pkg = Package(**common, name=_line(manifest.get("name") or meta.get("name") or pid, "name", NAME_LEN, required=True),
                      description=_line(manifest.get("description") or meta.get("description") or "", "description", SKILL_DESC_LEN),
                      requires=tuple(requires), files=_skill_files(folder, files))
        return _with_nest(pkg, manifest)
    if not manifest:
        raise PackageError(f"a {kind} needs {MANIFEST}")
    if kind == "extension":
        if "connector" in manifest or "skill" in manifest:
            raise PackageError("an extension has no connector or skill section (make it a skill or a connector with a ui section instead)")
        if not manifest.get("ui"):
            raise PackageError("an extension needs a ui section")
        pkg = Package(**common, name=_line(manifest.get("name"), "name", NAME_LEN, required=True),
                      description=_line(manifest.get("description"), "description", DESC_LEN, required=True))
        return _with_nest(pkg, manifest)
    sec = manifest.get("connector")
    if not isinstance(sec, dict):
        raise PackageError("a connector needs a connector section")
    bad = set(sec) - CONNECTOR_FIELDS
    if bad:
        raise PackageError(f"connector: unknown fields {', '.join(sorted(bad))}")
    regions = sec.get("regions") or []
    if not isinstance(regions, list) or len(regions) > 30 or not all(isinstance(r, str) and REGION.fullmatch(r) for r in regions):
        raise PackageError("connector.regions must be a list of two-letter country codes (KR, JP, ...)")
    featured = sec.get("featured", False)
    if not isinstance(featured, bool):
        raise PackageError("connector.featured must be true or false")
    if "skill" in manifest:
        raise PackageError("a connector has no skill section")
    pkg = Package(**common, name=_line(manifest.get("name"), "name", NAME_LEN, required=True),
                  description=_line(manifest.get("description"), "description", DESC_LEN, required=True),
                  regions=tuple(regions), featured=featured, connect=_connect(sec.get("connect")))
    return _with_nest(pkg, manifest)


def _with_nest(pkg: Package, manifest: Mapping[str, Any]) -> Package:
    """The package with its Kelo Nest sections checked (nest_manifest.py): its entries on this server, its screens and its permissions
    (what it declares; everything it uses must be declared once it has a permissions section)."""
    from . import nest_manifest as nm
    store = nm.parse_store(manifest.get("store"))
    declared = nm.parse_permissions(manifest.get("permissions"))
    # without a permissions section a fixed picture's host is taken from the ui itself (implied below)
    ui = nm.parse_ui(manifest.get("ui"), connect=pkg.connect, store=store, image_hosts=None if declared is None else declared["image_hosts"])
    provides = nm.parse_provides(manifest.get("provides"), kind=pkg.kind, connect=pkg.connect)
    perms = nm.effective_permissions(declared, nm.implied_permissions(pkg.connect, ui, store, provides))
    return dc_replace(pkg, permissions=perms, ui=ui, store=store, declared=declared is not None, provides=provides)


# ---------------------------------------------------------------------------------------------------------------- built-ins
def _builtin_connectors() -> dict[str, Package]:
    """Every connector defined in code, as a package whose connect.type names the code that connects it."""
    from .mail import PROVIDERS as MAIL
    from .mcp_oauth import SERVICES as MCP
    from .oauth_connect import PROVIDERS as DEVICE
    from .presets import BROWSER, GOOGLE_CONNECTORS, KEYS, UNSUPPORTED

    way: dict[str, str] = {}
    for p in DEVICE.values():
        for cid in p.serves:
            way[cid] = "device"
    way.update({cid: "mcp" for cid in MCP})
    way.update({cid: "mail" for p in MAIL.values() for cid in p.connectors})
    way.update({cid: "key" for cid in KEYS})
    way.update({cid: "key" for cid in GOOGLE_CONNECTORS})
    way["google_calendar"] = "google"
    way.update({cid: "browser" for cid in BROWSER})
    way.update({cid: "unsupported" for cid in UNSUPPORTED})
    out = {s.id: Package(s.id, "connector", "0.0.0", s.name, s.description, "builtin", regions=s.regions,
                         connect=MappingProxyType({"type": way.get(s.id, "none")})) for s in CATALOG}
    return out


def builtin_ids() -> frozenset[str]:
    """Ids an installed package may not take: every built-in connector and every way or provider name used in code."""
    from .mail import PROVIDERS as MAIL
    from .mcp_oauth import SERVICES as MCP
    from .oauth_connect import PROVIDERS as DEVICE
    from .presets import BROWSER, GOOGLE, GOOGLE_CONNECTORS, KEYS, UNSUPPORTED

    ids = {s.id for s in CATALOG} | set(MCP) | set(KEYS) | set(BROWSER) | set(UNSUPPORTED) | set(GOOGLE_CONNECTORS) | set(MAIL)
    ids |= {p.id for p in DEVICE.values()} | {c for p in DEVICE.values() for c in p.serves} | {GOOGLE.id, "browser", "calendar", "gmail", "imap_email"}
    return frozenset(ids)


# ---------------------------------------------------------------------------------------------------------------- registry
class Registry:
    """All packages: built-in skills (a folder of skill folders), built-in connectors (code) and installed packages (a folder of
    package folders). Cheap to ask: every reader calls `refresh()`, which reloads only when the folders changed."""

    def __init__(self, builtin_skills: str | Path | None = None, installed: str | Path | None = None, custom: str | Path | None = None):
        self.builtin_skills = Path(builtin_skills) if builtin_skills else None
        self.installed_dir = Path(installed) if installed else None
        self.custom_dir = Path(custom) if custom else None   # skills written on this server and approved by the owner
        self._lock = threading.RLock()
        self._fingerprint: str | None = None
        self._skills: dict[str, Package] = {}
        self._installed: dict[str, Package] = {}
        self._builtin_connectors: dict[str, Package] | None = None
        self._builtin_ids: frozenset[str] | None = None
        self.problems: list[str] = []         # packages left out, and why
        self.warnings: list[str] = []         # loaded, but something was ignored
        self.pending: dict[str, dict[str, Any]] = {}   # installed packages whose files changed since the owner approved them
        self._custom: dict[str, Package] = {}
        self.custom_pending: dict[str, dict[str, Any]] = {}   # custom skills not approved as they are now (new or changed on disk)
        self.disabled: dict[str, dict[str, Any]] = {}  # installed packages not used: turned off by the owner, or withdrawn from Nest
        self._disabled: dict[str, Package] = {}
        self.client_ids: Any = None             # (connector id) -> the owner's own OAuth client id, for client "owner" (nest.py sets it)
        self.clock = time.time
        self.refresh()

    # ---- loading
    def _fp(self) -> str:
        """Names, sizes and modification times of everything under both folders (bounded), hashed."""
        h = hashlib.sha256()
        for root in (self.builtin_skills, self.installed_dir, self.custom_dir):
            h.update(b"\0root\0" + str(root).encode())
            if root is None or not root.is_dir():
                continue
            try:
                entries = sorted(os.scandir(root), key=lambda e: e.name)
            except OSError:
                continue
            for e in entries[: MAX_PACKAGES * 2]:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                h.update(f"{e.name}|{st.st_mode}|{st.st_mtime_ns}|{st.st_size}\n".encode())
                if stat.S_ISDIR(st.st_mode):
                    stack, seen = [(e.path, 0)], 0
                    while stack and seen < MAX_FILES * 2:
                        d, depth = stack.pop()
                        try:
                            inner = sorted(os.scandir(d), key=lambda x: x.name)
                        except OSError:
                            continue
                        for f in inner:
                            seen += 1
                            try:
                                fs = f.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            h.update(f"{os.path.relpath(f.path, root)}|{fs.st_mode}|{fs.st_mtime_ns}|{fs.st_size}\n".encode())
                            if stat.S_ISDIR(fs.st_mode) and depth < MAX_DEPTH:
                                stack.append((f.path, depth + 1))
        return h.hexdigest()

    def refresh(self) -> bool:
        """Reload when anything under the folders changed. True when it reloaded."""
        with self._lock:
            fp = self._fp()
            if fp == self._fingerprint:
                return False
            if self._load():
                fp = self._fp()   # the load wrote the sources file (a package seen for the first time)
            self._fingerprint = fp
            return True

    def _connectors_builtin(self) -> dict[str, Package]:
        if self._builtin_connectors is None:
            self._builtin_connectors = _builtin_connectors()
            self._builtin_ids = builtin_ids()
        return self._builtin_connectors

    def _load(self) -> bool:
        """Read everything. True when it wrote the sources file."""
        problems: list[str] = []
        warnings: list[str] = []
        skills: dict[str, Package] = {}
        if self.builtin_skills and self.builtin_skills.is_dir():
            for d in sorted(self.builtin_skills.iterdir()):
                if not d.is_dir() or d.is_symlink() or not (d / SKILL_FILE).is_file() or not SKILL_ID.fullmatch(d.name):
                    continue
                try:
                    skills[d.name] = load_package(d, source="builtin", warnings=warnings)
                except (PackageError, OSError) as e:
                    problems.append(f"built-in skill {d.name}: {e}")
        self._connectors_builtin()
        custom, custom_pending = self._load_custom(set(skills), problems, warnings)
        taken = set(skills) | set(self._builtin_ids or ()) | set(custom) | set(custom_pending)
        installed: dict[str, Package] = {}
        pending: dict[str, dict[str, Any]] = {}
        records = self._read_records()
        nest = self.nest_state()
        disabled: dict[str, dict[str, Any]] = {}
        disabled_pkgs: dict[str, Package] = {}
        adopted = False
        if self.installed_dir and self.installed_dir.is_dir():
            try:
                folders = sorted(self.installed_dir.iterdir())
            except OSError:
                folders = []
            for d in folders:
                if d.name.startswith(".") or d.name.endswith(".tmp"):
                    continue   # half-copied installs and hidden files (install_from_dir works in a temporary folder)
                if len(installed) >= MAX_PACKAGES:
                    problems.append(f"more than {MAX_PACKAGES} packages installed; {d.name} and later ones are left out")
                    break
                try:
                    pkg = load_package(d, source="installed", warnings=warnings)
                except (PackageError, OSError) as e:
                    problems.append(f"{d.name}: {e}")
                    continue
                if pkg.id in taken:
                    whose = "a custom skill on this server" if pkg.id in custom or pkg.id in custom_pending else "a built-in package"
                    problems.append(f"{pkg.id}: {whose} has this id, so the installed one is left out")
                    continue
                try:
                    hashes = file_hashes(d)
                except (PackageError, OSError) as e:
                    problems.append(f"{d.name}: {e}")
                    continue
                digest = content_hash(hashes)
                rec = records.get(pkg.id)
                if rec is None:
                    # a folder put here by hand (only the owner can write the state folder): approved as it is now, unreviewed
                    rec = records[pkg.id] = self._new_record(pkg, hashes, {"type": "local"}, reviewed=False)
                    adopted = True
                elif rec.get("approved_hash") != digest:
                    changes = describe_changes(rec, hashes, sensitive_fields(pkg))
                    pending[pkg.id] = {"id": pkg.id, "kind": pkg.kind, "version": pkg.version, "approved_version": rec.get("version"),
                                       "content_hash": digest, "approved_hash": rec.get("approved_hash"), "changes": changes,
                                       "sensitive": any(c["sensitive"] for c in changes)}
                    problems.append(f"{pkg.id}: its files changed since they were approved, so it is not used until approved again")
                    continue
                pkg = dc_replace(pkg, content_hash=digest, reviewed=bool(rec.get("reviewed")))
                off = blocked(nest, pkg)
                if off is not None:
                    disabled[pkg.id], disabled_pkgs[pkg.id] = off, pkg
                    continue
                installed[pkg.id] = pkg
        if adopted:
            self._write_records(records)
        for msg in problems + warnings:
            log.warning("package: %s", msg)
        self._skills = {**skills, **custom, **{k: v for k, v in installed.items() if v.kind == "skill"}}
        self._installed = installed
        self._custom, self.custom_pending = custom, custom_pending
        self.problems, self.warnings, self.pending = problems, warnings, pending
        self.disabled, self._disabled = disabled, disabled_pkgs
        return adopted

    def _load_custom(self, builtin_skills: set[str], problems: list[str], warnings: list[str]) -> tuple[dict[str, Package], dict[str, dict[str, Any]]]:
        """The custom skills folder: (skills approved as they are now, skills held back with what changed). A folder without an
        approval record is held back too: only the owner's approval in the app makes a custom skill count."""
        custom: dict[str, Package] = {}
        pending: dict[str, dict[str, Any]] = {}
        root = self.custom_dir
        if root is None or not root.is_dir():
            return custom, pending
        records = self._read_records(root)
        try:
            folders = sorted(root.iterdir())
        except OSError:
            folders = []
        for d in folders:
            if d.name.startswith(".") or d.name.endswith(".tmp"):
                continue
            if len(custom) + len(pending) >= MAX_PACKAGES:
                problems.append(f"more than {MAX_PACKAGES} custom skills; {d.name} and later ones are left out")
                break
            if d.name in builtin_skills:
                problems.append(f"custom skill {d.name}: {builtin_refusal(d.name)}")
                continue
            try:
                pkg = load_custom(d, warnings)
                hashes = file_hashes(d)
            except (PackageError, OSError) as e:
                problems.append(f"custom skill {d.name}: {e}")
                continue
            digest = content_hash(hashes)
            rec = records.get(pkg.id)
            if rec is None or rec.get("approved_hash") != digest:
                pending[pkg.id] = {"id": pkg.id, "state": "new" if rec is None else "changed", "content_hash": digest,
                                   "approved_hash": (rec or {}).get("approved_hash"), "changes": describe_changes(rec, hashes, sensitive_fields(pkg)),
                                   "description": pkg.description, "name": pkg.name}
                problems.append(f"custom skill {pkg.id}: not approved as it is now, so it is not used until the owner approves it")
                continue
            custom[pkg.id] = dc_replace(pkg, content_hash=digest)
        return custom, pending

    # ---- where packages came from, and what the owner approved (SOURCES_FILE in the installed folder; hidden, so never a package)
    def _read_records(self, root: Path | None = None) -> dict[str, dict[str, Any]]:
        root = root or self.installed_dir
        if root is None:
            return {}
        f = root / SOURCES_FILE
        try:
            if f.is_symlink() or not f.is_file() or f.stat().st_size > 4 * 1024 * 1024:
                return {}
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("package: %s could not be read; every package is treated as new", SOURCES_FILE)
            return {}
        recs = data.get("packages") if isinstance(data, dict) else None
        return {k: v for k, v in recs.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(recs, dict) else {}

    def _write_records(self, records: Mapping[str, Mapping[str, Any]], root: Path | None = None) -> None:
        root = root or self.installed_dir
        if root is None:
            return
        root.mkdir(parents=True, exist_ok=True)
        f = root / SOURCES_FILE
        tmp = f.with_name(f"{SOURCES_FILE}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps({"format": 1, "packages": dict(sorted(records.items()))}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, f)

    def _new_record(self, pkg: Package, hashes: Mapping[str, str], source: Mapping[str, Any], *, reviewed: bool) -> dict[str, Any]:
        now = self.clock()
        return {"id": pkg.id, "kind": pkg.kind, "version": pkg.version, "source": dict(source), "installed_at": now, "reviewed": reviewed,
                "approved_hash": content_hash(hashes), "approved_at": now, "files": dict(hashes), "sensitive": sensitive_fields(pkg)}

    def nest_state(self) -> dict[str, Any]:
        """The Nest state beside the installed packages (NEST_STATE): {"disabled": {id: {...}}, "withdrawn": {id: {...}}, "pins": {...},
        ...}. Hidden, so never a package, and outside every package folder, so no package can write it."""
        if self.installed_dir is None:
            return {}
        f = self.installed_dir / NEST_STATE
        try:
            if f.is_symlink() or not f.is_file() or f.stat().st_size > 4 * 1024 * 1024:
                return {}
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("package: %s could not be read; no package is turned off", NEST_STATE)
            return {}
        return data if isinstance(data, dict) else {}

    def write_nest_state(self, state: Mapping[str, Any]) -> None:
        if self.installed_dir is None:
            raise PackageError("this server has no folder for installed packages")
        with self._lock:
            self.installed_dir.mkdir(parents=True, exist_ok=True)
            f = self.installed_dir / NEST_STATE
            tmp = f.with_name(f"{NEST_STATE}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_text(json.dumps(dict(state), ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, f)
            self._fingerprint = None

    def disabled_package(self, pid: str) -> Package | None:
        """An installed package that is turned off or withdrawn (not used), as loaded."""
        self.refresh()
        return self._disabled.get(pid)

    def source_record(self, pid: str) -> dict[str, Any] | None:
        """Where an installed package came from: {"id", "kind", "version", "source": {"type": local|git|archive, ...}, "installed_at",
        "reviewed", "approved_hash", "approved_at", "files", "sensitive"}, or None."""
        with self._lock:
            rec = self._read_records().get(pid)
        return dict(rec) if rec else None

    def statuses(self) -> list[dict[str, Any]]:
        """Every installed package with where it came from and whether it may be used: state "approved" or "changed" (its files
        changed since the approval: not used; `changes` says what), review "reviewed" | "unreviewed". For a later owner-only screen."""
        self.refresh()
        recs = self._read_records()
        out = []
        for pid in sorted(set(self._installed) | set(self.pending) | set(self.disabled)):
            rec = recs.get(pid) or {}
            row = {"id": pid, "version": rec.get("version"), "source": rec.get("source"), "installed_at": rec.get("installed_at"),
                   "review": "reviewed" if rec.get("reviewed") else "unreviewed", "state": "approved"}
            if pid in self.disabled:
                row.update(state="withdrawn" if self.disabled[pid].get("reason") == "withdrawn" else "disabled", disabled=self.disabled[pid])
            if pid in self.pending:
                row.update(state="changed", changes=self.pending[pid]["changes"], sensitive=self.pending[pid]["sensitive"],
                           content_hash=self.pending[pid]["content_hash"])
            out.append(row)
        return out

    def approve(self, pid: str, *, expected_hash: str | None = None) -> Package:
        """The owner approves an installed package's files as they are now (after looking at `pending[pid]["changes"]`). With
        `expected_hash`, only when the files are still the ones they looked at. Raises PackageError."""
        if self.installed_dir is None or not (SKILL_ID.fullmatch(pid or "") or CONNECTOR_ID.fullmatch(pid or "")):
            raise PackageError(f"no installed package {pid!r}")
        with self._lock:
            folder = self.installed_dir / pid
            pkg = load_package(folder, source="installed")
            hashes = file_hashes(folder)
            digest = content_hash(hashes)
            if expected_hash is not None and digest != expected_hash:
                raise PackageError(f"{pid} changed again since you looked; look at the changes once more")
            records = self._read_records()
            old = records.get(pid) or {}
            rec = self._new_record(pkg, hashes, old.get("source") or {"type": "local"}, reviewed=bool(old.get("reviewed")))
            if old.get("installed_at"):
                rec["installed_at"] = old["installed_at"]
            if old.get("reviewed") and old.get("approved_hash") != digest:
                rec["reviewed"] = False   # the reviewed files were the earlier ones
            records[pid] = rec
            self._write_records(records)
            self._fingerprint = None
        got = self.get(pid)
        if got is None or not got.installed:
            raise PackageError(f"{pid} could not be loaded: {'; '.join(self.problems) or 'unknown reason'}")
        return got

    # ---- reading
    def skills(self) -> list[Package]:
        """Built-in skills first (by id), then custom ones, then installed ones."""
        self.refresh()
        return sorted(self._skills.values(), key=lambda p: (SOURCES.index(p.source) if p.source in SOURCES else len(SOURCES), p.id))

    def installed(self, kind: str | None = None) -> list[Package]:
        self.refresh()
        return [p for p in self._installed.values() if kind is None or p.kind == kind]

    def get(self, pid: str) -> Package | None:
        """A package by id: a built-in skill or connector, else an installed one."""
        self.refresh()
        return self._skills.get(pid) or self._connectors_builtin().get(pid) or self._installed.get(pid)

    def connectors(self) -> list[Package]:
        """Every connector, built-in (code) and installed (folders), in one list."""
        self.refresh()
        return [*self._connectors_builtin().values(), *(p for p in self._installed.values() if p.kind == "connector")]

    def key_patterns(self) -> list[tuple[str, str]]:
        """(connector id, key_pattern) of every installed key connector that declares the shape of its keys."""
        return [(p.id, p.connect["key_pattern"]) for p in self.installed("connector")
                if p.connect.get("type") == "key" and p.connect.get("key_pattern")]

    def installed_connector(self, cid: str) -> Package | None:
        self.refresh()
        p = self._installed.get(cid)
        return p if p is not None and p.kind == "connector" else None

    def services(self) -> list[Service]:
        """The service catalog (catalog.CATALOG) followed by installed connectors, in the reader's language."""
        return [*CATALOG, *(Service(p.id, p.text("name"), p.text("description"), p.regions) for p in self.installed("connector"))]

    def featured_ids(self) -> list[str]:
        return [p.id for p in self.installed("connector") if p.featured]

    def mcp_service(self, cid: str) -> Any:
        """The hosted MCP server of a connector (built-in table first, then an installed mcp package), or None."""
        from .mcp_oauth import SERVICES as MCP, McpService
        if cid in MCP:
            return MCP[cid]
        p = self.installed_connector(cid)
        if p is None or p.connect.get("type") not in ("mcp", "oauth"):
            return None
        if p.connect["type"] == "oauth":
            c = p.connect
            client_id = c.get("client_id") or ""
            if c.get("client") == "owner":
                client_id = (self.client_ids(p.id) if callable(self.client_ids) else "") or ""
            oauth = MappingProxyType({"authorize_url": c["authorize_url"], "token_url": c["token_url"], "scopes": tuple(c.get("scopes") or ()),
                                      "hosts": tuple(c["hosts"]), "client_id": client_id, "client": c.get("client", "public"),
                                      "params": dict(c.get("params") or {})})
            return McpService(p.id, p.item, p.text("name"), c.get("base_url") or c["authorize_url"], p.text("blurb") or p.text("description"),
                              oauth=oauth)
        return McpService(p.id, p.item, p.text("name"), p.connect["url"], p.text("blurb") or p.text("description"))

    def key_preset(self, cid: str) -> Any:
        """The key form of a connector (built-in presets first, then an installed key package), or None."""
        from .presets import GOOGLE, KEYS, Field, KeyPreset
        if cid == GOOGLE.id:
            return GOOGLE
        if cid in KEYS:
            return KEYS[cid]
        p = self.installed_connector(cid)
        if p is None or p.connect.get("type") != "key":
            return None
        c = p.connect
        return KeyPreset(p.id, p.text("name"), p.item, tuple(c["hosts"]), dict(c["placement"]), tuple(p.text("help") or ()),
                         p.text("blurb") or p.text("description"), package_note(p),
                         fields=(Field("token", p.text("key_label") or "API key / token"),), verify=c.get("verify", ""),
                         endpoint=c.get("mcp_url", ""))

    def icon_fields(self, cid: str) -> dict[str, str]:
        """"icon", "icon_small", "icon_color" for an installed connector's row ({} when it has no icon)."""
        p = self.installed_connector(cid)
        got = self.icon(p.icon_name) if p is not None and p.icon else None
        if got is None:
            return {}
        url = f"/api/connectors/icons/{p.icon_name}.png?v={got[1]}"
        small = f"/api/connectors/icons/{p.icon_name}-128.png?v={got[1]}"
        return {"icon": url, "icon_small": small, "icon_color": ""}

    def icon(self, name: str) -> tuple[bytes, str] | None:
        """The PNG of an installed connector (`pkg_<id>`, or `pkg_<id>-128`: the same picture) and its ETag."""
        if not name.startswith(ICON_PREFIX):
            return None
        p = self.installed_connector(name.removeprefix(ICON_PREFIX).removesuffix("-128"))
        if p is None or not p.icon or p.path is None:
            return None
        f = p.path / p.icon
        try:
            if f.is_symlink() or f.stat().st_size > MAX_ICON:
                return None
            data = f.read_bytes()
        except OSError:
            return None
        if not data.startswith(PNG_MAGIC):
            return None
        return data, hashlib.sha256(data).hexdigest()[:16]

    def skill_text(self, pid: str, file: str | None = None) -> str:
        """SKILL.md (with front matter) or one of the skill's extra files, read from the package folder by name from the index."""
        p = self._skills.get(pid)
        if p is None or p.path is None:
            raise PackageError(f"no such skill: {pid!r}")
        name = file or SKILL_FILE
        if file is not None and file not in p.files:
            raise PackageError(f"{pid} has no file {file!r}")
        f = p.path / name
        if f.is_symlink():
            raise PackageError(f"{pid}/{name} is a link")
        return _read_text(f, MAX_TEXT_FILE, name)

    # ---- custom skills: written on this server, used only as the owner approved them (custom_skills.py proposes them)
    def custom(self) -> list[Package]:
        self.refresh()
        return sorted(self._custom.values(), key=lambda p: p.id)

    def custom_ids(self) -> set[str]:
        """Every custom skill folder that loads, approved or held back."""
        self.refresh()
        return set(self._custom) | set(self.custom_pending)

    def check_custom_id(self, sid: str) -> str:
        """The id a new custom skill may take, or PackageError with the reason (a built-in skill's or an installed package's id)."""
        sid = (sid or "").strip()
        if not SKILL_ID.fullmatch(sid):
            raise PackageError(f"id {sid!r} is not valid (lower-case letters, digits and '-', 1-48 characters)")
        self.refresh()
        if sid in self._skills and self._skills[sid].source == "builtin":
            raise PackageError(builtin_refusal(sid))
        if sid in self._installed or sid in self.pending or sid in self._connectors_builtin() or sid in (self._builtin_ids or ()):
            raise PackageError(f"{sid} is the id of an installed or built-in package; pick another id")
        return sid

    def custom_files(self, sid: str) -> dict[str, str]:
        """The custom skill's files as they are on disk now ({} when there is no folder)."""
        if self.custom_dir is None or not SKILL_ID.fullmatch(sid or ""):
            return {}
        folder = self.custom_dir / sid
        if folder.is_symlink() or not folder.is_dir():
            return {}
        files, _ = _scan(folder)
        return {rel.replace(os.sep, "/"): _read_text(folder / rel, MAX_TEXT_FILE, rel) for rel, _size in files}

    def custom_approved(self, sid: str) -> dict[str, str]:
        """The files of a custom skill as the owner last approved them ({} when never approved)."""
        if self.custom_dir is None:
            return {}
        texts = (self._read_records(self.custom_dir).get(sid) or {}).get("texts")
        return {k: v for k, v in texts.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(texts, dict) else {}

    def write_custom(self, sid: str, files: Mapping[str, str], *, origin: Mapping[str, Any] | None = None, keep_others: bool = True) -> Package:
        """The owner approved exactly this text: write the custom skill's files (by default the other approved .md files stay) and
        record their content hash as approved. Refuses a built-in or installed id. Raises PackageError."""
        if self.custom_dir is None:
            raise PackageError("this server has no folder for custom skills")
        sid = self.check_custom_id(sid)
        with self._lock:
            current = self.custom_files(sid) if keep_others else {}
            texts = {**current, **dict(files)}
            root = self.custom_dir
            root.mkdir(parents=True, exist_ok=True)
            work = root / f".{sid}-{uuid.uuid4().hex[:8]}.tmp"
            tmp = work / sid   # named as the skill: a folder without a manifest takes its id from its name
            target = root / sid
            try:
                tmp.mkdir(parents=True)
                for rel, text in texts.items():
                    if not FILE_NAME.fullmatch(rel) or not rel.endswith(".md"):
                        raise PackageError(f"{rel}: a custom skill holds only .md files at its top level")
                    (tmp / rel).write_text(text, encoding="utf-8")
                pkg = load_custom(tmp)
                hashes = file_hashes(tmp)
                records = self._read_records(root)
                old = records.get(sid) or {}
                rec = self._new_record(pkg, hashes, dict(origin or old.get("source") or {"type": "local"}), reviewed=False)
                rec["texts"] = dict(sorted(texts.items()))
                if old.get("installed_at"):
                    rec["installed_at"] = old["installed_at"]
                records[sid] = rec
                self._write_records(records, root)
                if target.exists() or target.is_symlink():
                    self._delete_custom(target)
                os.replace(tmp, target)
            finally:
                if work.exists():
                    shutil.rmtree(work, ignore_errors=True)
            self._fingerprint = None
        got = self.get(sid)
        if got is None or got.source != "custom":
            raise PackageError(f"{sid} was written but could not be loaded: {'; '.join(self.problems) or 'unknown reason'}")
        return got

    def approve_custom(self, sid: str, *, expected_hash: str | None = None) -> Package:
        """The owner approves a custom skill's files as they are on disk now (after looking at what changed). With `expected_hash`,
        only when the files are still the ones they looked at. Raises PackageError."""
        if self.custom_dir is None:
            raise PackageError("this server has no folder for custom skills")
        sid = self.check_custom_id(sid)
        with self._lock:
            folder = self.custom_dir / sid
            if not folder.is_dir() or folder.is_symlink():
                raise PackageError(f"no custom skill {sid!r}")
            pkg = load_custom(folder)
            hashes = file_hashes(folder)
            if expected_hash is not None and content_hash(hashes) != expected_hash:
                raise PackageError(f"{sid} changed again since you looked; look at it once more")
            records = self._read_records(self.custom_dir)
            old = records.get(sid) or {}
            rec = self._new_record(pkg, hashes, old.get("source") or {"type": "local"}, reviewed=False)
            rec["texts"] = dict(sorted(self.custom_files(sid).items()))
            if old.get("installed_at"):
                rec["installed_at"] = old["installed_at"]
            records[sid] = rec
            self._write_records(records, self.custom_dir)
            self._fingerprint = None
        got = self.get(sid)
        if got is None or got.source != "custom":
            raise PackageError(f"{sid} could not be loaded: {'; '.join(self.problems) or 'unknown reason'}")
        return got

    def restore_custom(self, sid: str) -> bool:
        """Undo a change made outside the app: the approved files go back (a folder never approved is removed). False when there
        was nothing to undo."""
        if self.custom_dir is None or not SKILL_ID.fullmatch(sid or ""):
            return False
        with self._lock:
            target = self.custom_dir / sid
            approved = self.custom_approved(sid)
            if not (target.exists() or target.is_symlink()):
                return False
            if not approved:
                self._delete_custom(target)
            else:
                tmp = self.custom_dir / f".{sid}-{uuid.uuid4().hex[:8]}.tmp"
                try:
                    tmp.mkdir()
                    for rel, text in approved.items():
                        if FILE_NAME.fullmatch(rel):
                            (tmp / rel).write_text(text, encoding="utf-8")
                    self._delete_custom(target)
                    os.replace(tmp, target)
                finally:
                    if tmp.exists():
                        shutil.rmtree(tmp, ignore_errors=True)
            self._fingerprint = None
        self.refresh()
        return True

    def remove_custom(self, sid: str) -> bool:
        """Delete a custom skill and its approval. False when there was none."""
        if self.custom_dir is None or not SKILL_ID.fullmatch(sid or ""):
            return False
        target = self.custom_dir / sid
        with self._lock:
            records = self._read_records(self.custom_dir)
            had = records.pop(sid, None) is not None
            if had:
                self._write_records(records, self.custom_dir)
            if target.exists() or target.is_symlink():
                self._delete_custom(target)
                had = True
            self._fingerprint = None
        self.refresh()
        return had

    def _delete_custom(self, target: Path) -> None:
        if self.custom_dir is None or target.parent != self.custom_dir:
            raise PackageError("not a custom skill folder")
        if target.is_symlink() or not target.is_dir():
            target.unlink()
        else:
            shutil.rmtree(target)

    # ---- installing (no endpoint yet: these are what one will call)
    def install_from_dir(self, src: str | Path, *, replace: bool = False, source: Mapping[str, Any] | None = None, reviewed: bool = False,
                         switch_source: bool = False, expect_id: str | None = None) -> Package:
        """Check a package folder and copy it into the installed folder (plain files only), then reload. Refuses a built-in id, and an
        installed one unless `replace`. Installing is the owner's approval of exactly these files: the source record (`source`, default
        {"type": "local"}; see install_from_index) keeps their content hash, and a later change to the files holds the package back
        until it is approved again. `reviewed` is true only for a curated catalog entry marked reviewed. An update from a different
        source (another kind, another address) is refused unless `switch_source`. Raises PackageError."""
        if self.installed_dir is None:
            raise PackageError("this server has no folder for installed packages")
        src = Path(src)
        pkg = load_package(src, source="installed", check_name=False)
        if expect_id is not None and pkg.id != expect_id:
            raise PackageError(f"the package's id is {pkg.id!r}, not {expect_id!r}")
        source = dict(source or {"type": "local"})
        self._connectors_builtin()
        builtin_skill_ids = {p.id for p in self._skills.values() if not p.installed}
        if pkg.id in (self._builtin_ids or set()) or pkg.id in builtin_skill_ids:
            raise PackageError(f"{pkg.id} is the id of a built-in package")
        files, _ = _scan(src)
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        target = self.installed_dir / pkg.id
        if (target.exists() or target.is_symlink()) and not replace:
            raise PackageError(f"{pkg.id} is already installed")
        old = self._read_records().get(pkg.id)
        if (target.exists() or target.is_symlink()) and old and not switch_source and not _same_source(old.get("source"), source):
            was, now = old.get("source") or {}, source
            raise PackageError(f"{pkg.id} was installed from {was.get('type', 'local')} {was.get('url') or ''}".rstrip()
                               + f"; this update comes from {now.get('type')} {now.get('url') or ''}".rstrip()
                               + ". Switching where a package comes from needs the owner to confirm it")
        tmp = self.installed_dir / f".{pkg.id}-{uuid.uuid4().hex[:8]}.tmp"
        try:
            for rel, _ in files:
                dst = tmp / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(src / rel, "rb") as fin, open(dst, "xb") as fout:
                    shutil.copyfileobj(fin, fout)
            load_package(tmp, source="installed", check_name=False)   # the copy is what will be read: check it too
            hashes = file_hashes(tmp)
            with self._lock:
                records = self._read_records()
                records[pkg.id] = self._new_record(pkg, hashes, source, reviewed=reviewed)
                self._write_records(records)
                if target.exists() or target.is_symlink():
                    self._delete(target)
                os.replace(tmp, target)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        self._fingerprint = None
        got = self.get(pkg.id)
        if got is None or not got.installed:
            raise PackageError(f"{pkg.id} was copied but could not be loaded: {'; '.join(self.problems) or 'unknown reason'}")
        return got

    def remove(self, pid: str) -> bool:
        """Delete an installed package's folder. Built-in packages cannot be removed. False when it was not installed."""
        if self.installed_dir is None or not (SKILL_ID.fullmatch(pid or "") or CONNECTOR_ID.fullmatch(pid or "")):
            return False
        target = self.installed_dir / pid
        if not (target.exists() or target.is_symlink()):
            return False
        with self._lock:
            self._delete(target)
            records = self._read_records()
            if records.pop(pid, None) is not None:
                self._write_records(records)
            self._fingerprint = None
        self.refresh()
        return True

    async def install_from_index(self, entry: Any, *, fetcher: Any = None, replace: bool = False, switch_source: bool = False) -> Package:
        """Install a curated catalog entry (package_index.IndexEntry): fetched through netguard into a temporary folder, checked
        against the entry's commit or sha256, then installed with install_from_dir and a source record. See package_index.py."""
        from .package_index import install_entry
        return await install_entry(self, entry, fetcher=fetcher, replace=replace, switch_source=switch_source)

    def _delete(self, target: Path) -> None:
        if target.parent != self.installed_dir:
            raise PackageError("not an installed package folder")
        if target.is_symlink() or not target.is_dir():
            target.unlink()
        else:
            shutil.rmtree(target)


def blocked(nest: Mapping[str, Any], pkg: Package) -> dict[str, Any] | None:
    """Why an installed package is not used (the Nest state): withdrawn from Nest (its id with every version, its version, or its
    content hash on the kill-switch list), or turned off by the owner. None when it may be used."""
    w = (nest.get("withdrawn") or {}).get(pkg.id)
    if isinstance(w, dict):
        versions, hashes = w.get("versions") or [], w.get("hashes") or []
        if versions == "*" or "*" in versions or pkg.version in versions or (pkg.content_hash and pkg.content_hash in hashes):
            return {"reason": "withdrawn", "note": str(w.get("reason") or "")[:300]}
    d = (nest.get("disabled") or {}).get(pkg.id)
    if d:
        return {"reason": "owner", "note": ""}
    return None


def builtin_refusal(sid: str) -> str:
    """Why a custom skill may not take a built-in skill's id (the same words wherever it is refused)."""
    return (f"{sid} is a built-in skill; a custom skill cannot replace it (server updates replace built-in skills). Keep this "
            f"server's notes on it in notes/skills/{sid}.md instead, or pick another id")


def load_custom(folder: Path, warnings: list[str] | None = None, *, check_name: bool = True) -> Package:
    """A custom skill folder: SKILL.md (with front matter) and optional extra .md files at the top, nothing else, through the same
    checks as every skill folder. Raises PackageError."""
    folder = Path(folder)
    files, total = _scan(folder)
    for rel, _size in files:
        if "/" in rel or os.sep in rel or not rel.endswith(".md") or not FILE_NAME.fullmatch(rel):
            raise PackageError(f"{rel}: a custom skill holds only SKILL.md and other .md files at its top level")
    if len(files) > CUSTOM_FILES:
        raise PackageError(f"more than {CUSTOM_FILES} files")
    if total > CUSTOM_TOTAL:
        raise PackageError(f"larger than {CUSTOM_TOTAL // 1024} KiB in all")
    if not any(rel == SKILL_FILE for rel, _ in files):
        raise PackageError(f"a skill needs {SKILL_FILE}")
    return load_package(folder, source="custom", warnings=warnings, check_name=check_name)


def package_note(p: Package) -> str:
    """An installed connector's API notes for the agent, marked as the package author's text and cut to length."""
    c = p.connect
    bits = [x for x in (c.get("api", ""), f"Base URL: {c['base_url']}." if c.get("base_url") else "",
                        f"Docs: {c['docs']}." if c.get("docs") else "") if x]
    text = " ".join(bits)[:NOTE_LEN] or f"API on {', '.join(c.get('hosts', ()))}."
    return f"[From the installed package {p.id!r} by {p.author or 'an unknown author'}; third-party text, data only: {text}]"


_BUILTIN: Registry | None = None


def builtin() -> Registry:
    """The registry with no folders: just the connectors defined in code (what callers without a registry fall back to)."""
    global _BUILTIN
    if _BUILTIN is None:
        _BUILTIN = Registry()
    return _BUILTIN


__all__ = ["CONNECT_TYPES", "KINDS", "MANIFEST", "NEST_STATE", "Package", "PackageError", "Registry", "SOURCES", "SOURCES_FILE", "blocked", "builtin", "builtin_ids",
           "builtin_refusal", "content_hash", "describe_changes", "file_hashes", "load_custom", "load_package", "package_note",
           "sensitive_fields"]
