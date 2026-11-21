"""The Kelo apps offered for download from the operator's website (https://kelo.chat/download/...).

The files live in a folder on the server's own state volume (`/state/downloads`, or ASSISTANT_DOWNLOADS_DIR), next to a manifest
the publisher writes (`assistant publish-app`), so a new build goes up without rebuilding the server image. The web routes only
read: a file is served when, and only when, the manifest names it for a platform, its name is a plain file name, it is a regular
file inside the folder (no links), and its size still matches what the manifest says (a copy still in progress is not served).
There is no listing of the folder.
After each publish (and after a good `assistant update`) the folder keeps, per platform, only the file the manifest names;
older installers are removed (tidy_installers).

manifest.json: {"platforms": {"android": {"file": "kelo-0.9.0-402500.apk", "version": "0.9.0", "version_code": 402500,
                                          "size": 28978903, "sha256": "<hex>", "date": "2026-11-08T09:00:00Z"}, "windows": {...}, ...}}
"version" is the name people see (and what older apps compare); "version_code" (when known) is the build number newer apps compare,
so 0.9.0 with a higher build is offered as an update (Android: versionCode; given as `--version 0.9.0+402500` or read from the APK).

STORE ROWS: latest.json's "store" object holds the newest live store release of each app ({"android": {"play": row}, "ios":
{"appstore": row}, "desktop": {"msstore": row}}), each row signed with the release key over release_sign.store_statement. The
publisher writes them (`assistant release-record` for a store channel, `assistant publish-store`) and every writer keeps the section.

SIGNED ROWS: a row may also carry "sig", "key" (key id) and "commit" (the public source commit): the Ed25519 signature the apps check
(release_sign.py, release_keys.py). latest.json passes the three through; the apps offer nothing whose signature does not check.

Addresses: /download/<platform> sends the browser on to /download/<platform>/<file> (a stable link; the file name tells the
version), which serves the file with its length, an ETag from its sha256 and byte ranges (resumable downloads).
/download/latest.json is the public summary (version, size, sha256, date, url per platform).

SERVER RELEASES: the manifest may also carry "server": the release manifest of the newest signed server images (release.py; written
by the release workflow, published with `assistant publish-server release.json`). latest.json repeats it under "server" exactly as
checked by release.parse (only when it passes), and every Kelo server's updater reads it from there.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import release, release_sign
from .hosting import paths
from .app_releases import AppReleases, RecordError
from .release_sign import STORE_CHANNELS

MANIFEST = "manifest.json"
# platform -> (file extension, media type, what the site calls it)
PLATFORMS: dict[str, tuple[str, str, str]] = {
    "android": (".apk", "application/vnd.android.package-archive", "Android"),
    "windows": (".exe", "application/vnd.microsoft.portable-executable", "Windows"),
    "windows-msi": (".msi", "application/x-msi", "Windows (MSI)"),
    "windows-server-msi": (".msi", "application/x-msi", "Kelo server for Windows (MSI)"),   # not on the public download page: latest.json + the direct link only
    "mac": (".dmg", "application/x-apple-diskimage", "macOS"),
    "mac-server-bundle": (".zip", "application/zip", "Kelo server for Mac (bundle)"),        # the same rules as windows-server-msi
}
# the Kelo server's installers: never the desktop app's rows, never on the public page, never in apps.json
SERVER_PLATFORMS = frozenset({"windows-server-msi", "mac-server-bundle"})
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
VERSION = re.compile(r"^[0-9][0-9A-Za-z.-]{0,39}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE = re.compile(r"^[A-Za-z0-9+/]{86}==$")      # base64 (standard, padded) of the 64 raw signature bytes: 88 characters
KEY_ID = re.compile(r"^[0-9a-f]{16}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
KEEP_PREVIOUS_INSTALLERS = 0   # per platform, besides the file the manifest names: none (owner 2026-11-20: only the published one)
INSTALLER = re.compile(r"^kelo-(server-)?[0-9][0-9A-Za-z.-]*\.(apk|exe|msi|dmg|zip)$")   # the names Publisher.publish gives

# Runs inside the server container (`docker exec` / `docker compose exec`), stdlib only, so it works in an older image. Commands:
#   installers <folder>               -> {"files": [{"name", "mtime"}], "named": [file names the manifest's platform rows name] | null}
#   remove-installers <folder> <name>... -> {"removed": [...], "failed": [...]}; skips any name the manifest names (read again here),
#                                          any name that is not a plain file name, a link or not a regular file. Null "named" = no
#                                          readable manifest: nothing is removed.
INSTALLERS_HELPER = r'''
import json, os, re, sys
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
cmd, folder, names = sys.argv[1], sys.argv[2], sys.argv[3:]

def named():
    try:
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    rows = raw.get("platforms") if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        return None
    return sorted({r["file"] for r in rows.values() if isinstance(r, dict) and isinstance(r.get("file"), str)})

def plain(n):
    p = os.path.join(folder, n)
    return bool(NAME.match(n)) and os.path.isfile(p) and not os.path.islink(p)

if cmd == "installers":
    files = []
    if os.path.isdir(folder) and not os.path.islink(folder):
        for n in sorted(os.listdir(folder)):
            if plain(n):
                files.append({"name": n, "mtime": os.lstat(os.path.join(folder, n)).st_mtime})
    print(json.dumps({"files": files, "named": named()}))
elif cmd == "remove-installers":
    keep = named()
    removed, failed = [], []
    for n in names:
        if keep is None or n in keep or not plain(n):
            continue
        try:
            os.unlink(os.path.join(folder, n))
            removed.append(n)
        except OSError:
            failed.append(n)
    print(json.dumps({"removed": removed, "failed": failed}))
else:
    raise SystemExit("unknown command")
'''


def installer_platform(name: str) -> str | None:
    """The platform an installer's file name belongs to (Publisher.publish names them kelo-<version>[-<build>]<ext>, the server's
    kelo-server-...), or None for any other file (manifest.json, notices.json, anything else is never an installer)."""
    m = INSTALLER.match(name)
    if not m:
        return None
    ext, server = "." + m.group(2), m.group(1) is not None
    return next((p for p, (e, _, _) in PLATFORMS.items() if e == ext and (p in SERVER_PLATFORMS) == server), None)


def old_installers(files: list[dict[str, Any]], named: list[str] | None,
                   keep_previous: int = KEEP_PREVIOUS_INSTALLERS) -> list[str]:
    """The installers to remove from a downloads folder: per platform, every installer file that the manifest does not name and that
    is not among the newest `keep_previous` others of that platform (by mtime, then name). Never a file any manifest row names (of
    any platform), never a file that is not an installer, nothing for a platform the manifest names no file of, and nothing at all
    when `named` is None (no readable manifest)."""
    if named is None:
        return []
    named_set = set(named)
    current = {installer_platform(n) for n in named_set} - {None}
    groups: dict[str, list[tuple[float, str]]] = {}
    for f in files:
        name = f.get("name") if isinstance(f, dict) else None
        if not isinstance(name, str) or name in named_set:
            continue
        p = installer_platform(name)
        if p is None or p not in current:
            continue
        try:
            mtime = float(f.get("mtime") or 0)
        except (TypeError, ValueError):
            mtime = 0.0
        groups.setdefault(p, []).append((mtime, name))
    out: list[str] = []
    for rows in groups.values():
        rows.sort(reverse=True)
        out += [n for _, n in rows[max(0, keep_previous):]]
    return sorted(out)


def tidy_installers(run: Callable[[list[str], bool], Any], folder: str, keep_previous: int = KEEP_PREVIOUS_INSTALLERS) -> list[str]:
    """Remove the older installers of one downloads folder (old_installers). `run(args, as_root)` runs INSTALLERS_HELPER with `args`
    inside the server container and returns a CompletedProcess-like object. `docker cp` writes the files as root, so the removal is
    tried as root first and then, for what root could not remove (a folder the server's user owns), as the container's own user.
    Returns the names removed; PublishError when the folder cannot be listed."""
    r = run(["installers", folder], False)
    if r.returncode != 0:
        raise PublishError(f"could not list the downloads folder: {(getattr(r, 'stderr', '') or '').strip()[:200]}")
    seen = json.loads((r.stdout or "").strip().splitlines()[-1])
    gone = old_installers(seen.get("files") or [], seen.get("named"), keep_previous)
    removed: list[str] = []
    for as_root in (True, False):
        if not gone:
            break
        r = run(["remove-installers", folder, *gone], as_root)
        if r.returncode != 0:
            continue
        res = json.loads((r.stdout or "").strip().splitlines()[-1])
        removed += [n for n in res.get("removed") or [] if n in gone]
        gone = [n for n in res.get("failed") or [] if n in gone]
    return removed


# ---------------------------------------------------------------------------------------------------------------- who publishes
def _origin(url: str) -> str:
    try:
        u = urlsplit((url or "").strip())
    except ValueError:
        return ""
    return f"{u.scheme.lower()}://{(u.hostname or '').lower()}{f':{u.port}' if u.port else ''}" if u.scheme and u.hostname else ""


def names_itself(public_url: str, *brokers: str) -> bool:
    """This server is the broker its own settings point at (its public address is the sign-in / push / Kelo broker's)."""
    me = _origin(public_url)
    return bool(me) and any(_origin(b) == me for b in brokers if b)


def operator_flag(value: str | None) -> bool | None:
    """ASSISTANT_OPERATOR: "1"/"true"/"yes" -> True, "0"/"false"/"no" -> False, anything else (unset) -> None (decide by the rest)."""
    v = (value or "").strip().lower()
    return True if v in ("1", "true", "yes", "on") else False if v in ("0", "false", "no", "off") else None


# ---------------------------------------------------------------------------------------------------------------- reading
def _store_entry(platform: str, raw: Any) -> dict[str, Any] | None:
    """One store row of latest.json, checked for shape (the signature is checked by the readers): None when anything is off.
    Kept: channel, version, build, date, min_server and, both together, sig and key."""
    if platform not in STORE_CHANNELS or not isinstance(raw, dict):
        return None
    try:
        release_sign.store_statement(platform, raw)
    except ValueError:
        return None
    out = {k: raw[k] for k in ("channel", "version", "build", "date")}
    if raw.get("min_server") not in (None, ""):
        out["min_server"] = raw["min_server"]
    if "sig" in raw or "key" in raw:
        sig, key = raw.get("sig"), raw.get("key")
        if not (isinstance(sig, str) and SIGNATURE.fullmatch(sig) and isinstance(key, str) and KEY_ID.fullmatch(key)):
            return None
        out["sig"], out["key"] = sig, key
    return out


def _store_rows(raw: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """platform -> {channel: row} from a manifest's or latest.json's "store" object; anything malformed is left out."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    if not isinstance(raw, dict):
        return out
    for platform, channels in raw.items():
        channel = STORE_CHANNELS.get(platform)
        row = _store_entry(platform, channels.get(channel)) if channel and isinstance(channels, dict) else None
        if row:
            out[platform] = {channel: row}
    return out


def _entry(platform: str, raw: Any) -> dict[str, Any] | None:
    """One manifest row, checked; None when anything about it is off."""
    if platform not in PLATFORMS or not isinstance(raw, dict):
        return None
    name, version, size, sha, date = raw.get("file"), raw.get("version"), raw.get("size"), raw.get("sha256"), raw.get("date")
    if not (isinstance(name, str) and FILE_NAME.fullmatch(name) and name.lower().endswith(PLATFORMS[platform][0])):
        return None
    if not (isinstance(version, str) and VERSION.fullmatch(version)) or not (isinstance(sha, str) and SHA256.fullmatch(sha)):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    out = {"file": name, "version": version, "size": size, "sha256": sha, "date": date if isinstance(date, str) else ""}
    code = raw.get("version_code")
    if isinstance(code, int) and not isinstance(code, bool) and 0 < code < 2**31:
        out["version_code"] = code
    sig, key, commit = raw.get("sig"), raw.get("key"), raw.get("commit")   # the release signature (release_sign.py), kept when well-formed
    if isinstance(sig, str) and SIGNATURE.fullmatch(sig):
        out["sig"] = sig
    if isinstance(key, str) and KEY_ID.fullmatch(key):
        out["key"] = key
    if isinstance(commit, str) and COMMIT.fullmatch(commit):
        out["commit"] = commit
    return out


class Downloads:
    """The read side, used by the web routes and the website. Never writes."""

    def __init__(self, folder: str | Path | None):
        self.folder = Path(folder) if folder else None
        self._last: tuple[tuple[int, int], dict[str, dict[str, Any]]] | None = None
        self._server: dict[str, Any] | None = None   # the checked server release of the last good manifest, as to_json()
        self._server_rel: release.Release | None = None
        self._store: dict[str, dict[str, dict[str, Any]]] = {}

    def manifest(self) -> dict[str, dict[str, Any]]:
        """platform -> checked row. A manifest being rewritten (unreadable for a moment) keeps the last good one."""
        if self.folder is None:
            return {}
        path = self.folder / MANIFEST
        try:
            st = path.lstat()
            if not path.is_file() or path.is_symlink():
                return {}
            key = (st.st_mtime_ns, st.st_size)
            if self._last and self._last[0] == key:
                return self._last[1]
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return self._last[1] if self._last else {}
        rows = raw.get("platforms") if isinstance(raw, dict) else None
        out = {p: e for p, r in (rows.items() if isinstance(rows, dict) else ()) if (e := _entry(p, r))}
        self._server_rel = server_release(raw.get("server") if isinstance(raw, dict) else None)
        self._server = self._server_rel.to_json() if self._server_rel else None
        self._store = _store_rows(raw.get("store") if isinstance(raw, dict) else None)
        self._last = (key, out)
        return out

    def server(self) -> dict[str, Any] | None:
        """The server release (release.py manifest), checked; None when there is none or it does not pass."""
        self.manifest()
        return self._server

    def store(self) -> dict[str, dict[str, dict[str, Any]]]:
        """The checked store rows: platform -> {channel: row} (empty when there are none)."""
        self.manifest()
        return {p: dict(c) for p, c in self._store.items()}

    def server_release(self) -> release.Release | None:
        """The same server release as checked by release.parse, or None."""
        self.manifest()
        return self._server_rel

    def file(self, platform: str, name: str | None = None) -> tuple[Path, dict[str, Any]] | None:
        """(path, row) of the file the manifest names for `platform` (and, when given, only if it is that `name`), or None."""
        row = self.manifest().get(platform)
        if self.folder is None or row is None or (name is not None and name != row["file"]):
            return None
        path = self.folder / row["file"]
        try:
            st = path.lstat()
        except OSError:
            return None
        if path.is_symlink() or not path.is_file() or path.resolve().parent != self.folder.resolve() or st.st_size != row["size"]:
            return None
        return path, row

    def notices_file(self) -> Path | None:
        """(path) of notices.json inside the downloads folder, checked with the same safe file rules (regular file, no symlink), or None."""
        if self.folder is None:
            return None
        path = self.folder / "notices.json"
        try:
            st = path.lstat()
        except OSError:
            return None
        if path.is_symlink() or not path.is_file() or path.resolve().parent != self.folder.resolve():
            return None
        return path

    def latest(self, base: str = "") -> dict[str, Any]:
        """The public summary (/download/latest.json): only platforms whose file is really there."""
        out: dict[str, Any] = {}
        for p in PLATFORMS:
            got = self.file(p)
            if got:
                row = got[1]
                out[p] = {"version": row["version"], "size": row["size"], "sha256": row["sha256"], "date": row["date"],
                          "file": row["file"], "url": f"{base}/download/{p}",
                          **{k: row[k] for k in ("version_code", "sig", "key", "commit") if k in row}}
        server = self.server_release()
        if server:
            out["server"] = server.to_public_json()
        store = self.store()
        if store:
            out["store"] = store
        return out


def server_release(raw: Any) -> release.Release | None:
    """A server release manifest, checked by release.parse against its own registry (each server still checks it against the
    registry it is configured for), or None when it does not pass."""
    from .release import DEFAULT_REGISTRY, ReleaseError, parse

    if not isinstance(raw, dict):
        return None
    try:
        reg = raw.get("registry") if isinstance(raw.get("registry"), str) else DEFAULT_REGISTRY
        return parse(raw, reg)
    except ReleaseError:
        return None


def server_entry(raw: Any) -> dict[str, Any] | None:
    """A server release manifest as manifest.json keeps it (release.Release.to_json), or None when it does not pass."""
    got = server_release(raw)
    return got.to_json() if got else None


def human_size(n: int) -> str:
    """28978903 -> "27.6 MB" (binary megabytes, as the stores and file managers show them)."""
    mb = n / 1024 / 1024
    return f"{mb:.1f} MB" if mb < 100 else f"{mb:.0f} MB"


# ---------------------------------------------------------------------------------------------------------------- publishing
class PublishError(Exception):
    """Something the operator can fix; the message says how."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_aapt2() -> str | None:
    found = shutil.which("aapt2")
    if found:
        return found
    for root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"), str(Path.home() / "Library/Android/sdk"),
                 str(Path.home() / "Android/Sdk")):
        tools = Path(root) / "build-tools" if root else None
        if tools and tools.is_dir():
            for d in sorted(tools.iterdir(), key=lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split(".")], reverse=True):
                if (d / "aapt2").is_file():
                    return str(d / "aapt2")
    return None


def apk_version(path: Path, run: Callable[..., Any] = subprocess.run, aapt2: str | None = None) -> str | None:
    """versionName from `aapt2 dump badging`, or None when aapt2 is not there or says nothing."""
    return apk_versions(path, run, aapt2)[0]


def apk_versions(path: Path, run: Callable[..., Any] = subprocess.run, aapt2: str | None = None) -> tuple[str | None, int | None]:
    """(versionName, versionCode) from `aapt2 dump badging`; (None, None) when aapt2 is not there or says nothing."""
    tool = aapt2 or find_aapt2()
    if not tool:
        return None, None
    r = run([tool, "dump", "badging", str(path)], capture_output=True, text=True)
    text = (r.stdout or "") if r.returncode == 0 else ""
    name = re.search(r"versionName='([^']*)'", text)
    code = re.search(r"versionCode='(\d+)'", text)
    return (name.group(1) if name else None), (int(code.group(1)) if code else None)


def platform_for(platform: str, file: Path) -> str:
    """`windows` with an .msi file is the MSI download; everything else must carry its platform's extension."""
    if platform == "windows" and file.suffix.lower() == ".msi":
        platform = "windows-msi"
    if platform not in PLATFORMS:
        raise PublishError(f"unknown platform {platform!r}: android, windows, windows-msi, windows-server-msi, mac or mac-server-bundle")
    if file.suffix.lower() != PLATFORMS[platform][0]:
        want = ".exe or .msi" if platform == "windows" else PLATFORMS[platform][0]
        raise PublishError(f"{file.name}: a {PLATFORMS[platform][2]} download must be a {want} file")
    return platform


def version_for(platform: str, file: Path, given: str | None, run: Callable[..., Any] = subprocess.run) -> str:
    return versions_for(platform, file, given, run)[0]


def versions_for(platform: str, file: Path, given: str | None, run: Callable[..., Any] = subprocess.run) -> tuple[str, int | None]:
    """(the version people see, the build number or None). `given` may be "0.9.0+402500" (version + build, as the Android build
    scripts pass it); an APK says both itself (versionName, versionCode) when aapt2 is installed."""
    text = (given or "").strip().lstrip("vV")
    code: int | None = None
    if "+" in text:
        text, _, build = text.partition("+")
        if not build.isdigit() or not 0 < int(build) < 2**31:
            raise PublishError(f"{given!r}: the build after '+' must be a whole number (example: 0.9.0+402500)")
        code = int(build)
    version = text
    if platform == "android" and (not version or code is None):
        name, apk_code = apk_versions(file, run)
        version = version or name or ""
        code = code if code is not None else apk_code
    if not version:
        m = re.search(r"(\d+\.\d+(?:\.\d+){0,2})", file.stem)
        version = m.group(1) if m else ""
    if not version:
        raise PublishError(f"could not tell the version of {file.name}: give it with --version X")
    if not VERSION.fullmatch(version):
        raise PublishError(f"{version!r} is not a version (examples: 1.0.0, 0.1.402102)")
    return version, code


OPERATOR_INSTANCE_ENV = "KELO_OPERATOR_INSTANCE"


def operator_instance() -> str | None:
    """The extra copy (`assistant instance`) that is the operator, from KELO_OPERATOR_INSTANCE; None = the main copy (the default).

    A bad name stops the command instead of falling back to main: publishing to the wrong copy is worse than not publishing."""
    from .instances import MAIN, NAME

    name = (os.environ.get(OPERATOR_INSTANCE_ENV) or "").strip()
    if not name or name == MAIN:
        return None
    if not NAME.match(name):
        raise PublishError(f"{OPERATOR_INSTANCE_ENV}={name!r} is not a copy name")
    return name


def operator_env_file(root: Path, instance: str | None) -> Path:
    """The settings file of the operator: the repo's .env (main) or instances/<name>/env."""
    return root / "instances" / instance / "env" if instance else root / ".env"


class Publisher:
    """Copies a build into the downloads folder of the main copy's server container and updates the manifest there.

    `docker cp` writes into the state volume even though the container's own file system is read-only, and works whether or not the
    container is running. The file goes in first; the manifest that names it only after, so the site never offers a half-copied file.
    """

    def __init__(self, root: Path, *, run: Callable[..., Any] = subprocess.run, out: Callable[[str], None] = print,
                 folder: str = f"{paths.STATE_DIR_IN_DOCKER}/downloads"):
        self.root, self.run, self.out, self.folder = root, run, out, folder.rstrip("/")
        self.server_release: dict[str, Any] | None = None
        self.store_rows: dict[str, dict[str, dict[str, Any]]] = {}   # kept when apps or the server are published

    def _body(self, rows: dict[str, Any], server: dict[str, Any] | None = None) -> dict[str, Any]:
        """The manifest as written: the platform rows, the server release and the store rows (every writer keeps all three)."""
        server = server if server is not None else self.server_release
        return {"platforms": rows, **({"server": server} if server else {}), **({"store": self.store_rows} if self.store_rows else {})}

    def container(self) -> str:
        inst = operator_instance()
        cmd = ["docker", "compose", "-f", str(self.root / "compose.yml")]
        if inst:   # a separate operator copy: its own compose project and settings, exactly as Instances.compose builds them
            cmd += ["-p", f"assistant-u-{inst}", "--env-file", str(operator_env_file(self.root, inst))]
        r = self.run([*cmd, "ps", "-a", "-q", "server"], cwd=self.root, capture_output=True, text=True)
        cid = (r.stdout or "").strip().splitlines()[0].strip() if r.returncode == 0 and (r.stdout or "").strip() else ""
        if not cid:
            raise PublishError(f"the {inst + ' copy' if inst else 'main copy'}'s server container was not found here: run this in the folder that holds compose.yml "
                               "after `docker compose up -d`")
        return cid

    def current(self, cid: str, scratch: Path) -> dict[str, Any]:
        dest = scratch / "old-manifest.json"
        r = self.run(["docker", "cp", f"{cid}:{self.folder}/{MANIFEST}", str(dest)], capture_output=True, text=True)
        if r.returncode != 0:
            err = (r.stderr or "").lower()
            if "no such" in err or "could not find" in err or "not found" in err:
                return {}
            raise PublishError(f"could not read the current manifest: {(r.stderr or '').strip()[:300]}")
        try:
            raw = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        rows = raw.get("platforms") if isinstance(raw, dict) else None
        self.server_release = server_entry(raw.get("server")) if isinstance(raw, dict) else None   # kept when apps are published
        self.store_rows = _store_rows(raw.get("store")) if isinstance(raw, dict) else {}           # kept by every writer
        return {p: e for p, r2 in (rows.items() if isinstance(rows, dict) else ()) if (e := _entry(p, r2))}

    def _copy_in(self, cid: str, staged: Path) -> None:
        r = self.run(["docker", "cp", f"{staged}{os.sep}.", f"{cid}:{self.folder}"], capture_output=True, text=True)
        if r.returncode != 0:
            raise PublishError(f"docker cp failed: {(r.stderr or '').strip()[:300]}")

    def _tidy_installers(self, cid: str) -> None:
        """After the manifest that names the new file is in: the older installers go (tidy_installers). Never fails the publish."""
        def run(args: list[str], as_root: bool) -> Any:
            return self.run(["docker", "exec", *(["-u", "0"] if as_root else []), cid, "python", "-c", INSTALLERS_HELPER, *args],
                            capture_output=True, text=True)
        try:
            gone = tidy_installers(run, self.folder)
        except Exception as e:  # noqa: BLE001 - the new build is live already; a failed cleanup only leaves old files
            self.out(f"Removing older installers was skipped ({type(e).__name__}); the publish itself is done.")
            return
        if gone:
            self.out(f"Removed {len(gone)} older installer{'' if len(gone) == 1 else 's'} ({', '.join(gone)}); each platform keeps "
                     "only the published file.")

    def _signed(self, platform: str, row: dict[str, Any], key_path: Path | None) -> dict[str, Any]:
        """The row signed with the operator's key and checked against the keys the apps know; PublishError when that cannot be."""
        if key_path is None:
            raise PublishError("no signing key: make one with `assistant release-key new` (or publish with --unsigned: apps that check "
                               "signatures will not offer the build)")
        try:
            signed = release_sign.sign_row(platform, row, key_path, self.root)
        except (release_sign.SigningKeyError, ValueError) as e:
            raise PublishError(str(e)) from None
        if not release_sign.verify_row(platform, signed):
            raise PublishError("this key is not one the apps know: add its public key to release_keys.py and ReleaseKeys.kt first")
        return signed

    def publish(self, platform: str, file: Path, version: str | None = None, *, base_url: str = "https://kelo.chat",
                now: Callable[[], datetime] = lambda: datetime.now(UTC), commit: str | None = None, key_path: Path | None = None,
                unsigned: bool = False) -> dict[str, Any]:
        file = Path(file).expanduser()
        if not file.is_file():
            raise PublishError(f"no such file: {file}")
        platform = platform_for(platform, file)
        version, code = versions_for(platform, file, version, self.run)
        commit = (commit or "").strip().lower() or None
        if commit is not None and not COMMIT.fullmatch(commit):
            raise PublishError("--commit must be the full 40-character hex hash of the public source commit")
        prefix = "kelo-server-" if platform in SERVER_PLATFORMS else "kelo-"   # the server installers never share a file name with the desktop app
        name = f"{prefix}{version}{f'-{code}' if code is not None else ''}{PLATFORMS[platform][0]}"
        size, sha = file.stat().st_size, sha256_of(file)
        cid = self.container()
        with tempfile.TemporaryDirectory(prefix="kelo-publish-") as tmp:
            scratch = Path(tmp)
            rows = self.current(cid, scratch)
            row = {"file": name, "version": version, "size": size, "sha256": sha,
                   "date": now().astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   **({"version_code": code} if code is not None else {}), **({"commit": commit} if commit else {})}
            if unsigned and key_path is None:
                self.out("warning: publishing WITHOUT a signature: apps that check signatures will not offer this build.")
            else:
                row = self._signed(platform, row, key_path)       # before anything is copied: a bad key leaves the server as it was
            stage = scratch / "file"
            stage.mkdir(mode=0o755)
            stage.chmod(0o755)
            shutil.copyfile(file, stage / name)
            (stage / name).chmod(0o644)
            self.out(f"Copying {file.name} ({human_size(size)}) into the server as {name}...")
            self._copy_in(cid, stage)
            rows[platform] = row
            meta = scratch / "meta"
            meta.mkdir(mode=0o755)
            meta.chmod(0o755)
            body = self._body(rows)
            (meta / MANIFEST).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (meta / MANIFEST).chmod(0o644)
            self._copy_in(cid, meta)
            self._tidy_installers(cid)
        base = base_url.rstrip("/")
        self.out(f"Published {PLATFORMS[platform][2]} {version}{f' (build {code})' if code is not None else ''}: {base}/download/{platform}")
        self.out(f"  file   {base}/download/{platform}/{name}")
        self.out(f"  sha256 {sha}")
        self.out(f"  list   {base}/download/latest.json")
        return {"platform": platform, **row, "url": f"{base}/download/{platform}"}

    def sign(self, platform: str, commit: str | None, key_path: Path | None, *, base_url: str = "https://kelo.chat") -> dict[str, Any]:
        """Signs the row already in the manifest (for a build that is live today). The file is not touched: only the manifest is
        written again. The row's size and sha256 are kept, and the file's real sha256 is re-checked first (it is copied out of the
        container with `docker cp`, which works whether or not the container runs)."""
        if platform not in PLATFORMS:
            raise PublishError(f"unknown platform {platform!r}: android, windows, windows-msi, windows-server-msi, mac or mac-server-bundle")
        commit = (commit or "").strip().lower() or None
        if commit is not None and not COMMIT.fullmatch(commit):
            raise PublishError("--commit must be the full 40-character hex hash of the public source commit")
        cid = self.container()
        with tempfile.TemporaryDirectory(prefix="kelo-sign-") as tmp:
            scratch = Path(tmp)
            rows = self.current(cid, scratch)
            row = rows.get(platform)
            if row is None:
                raise PublishError(f"there is no published {platform} build to sign: publish it first")
            copy = scratch / "live-file"
            r = self.run(["docker", "cp", f"{cid}:{self.folder}/{row['file']}", str(copy)], capture_output=True, text=True)
            if r.returncode != 0 or not copy.is_file():
                raise PublishError(f"could not read {row['file']} from the server: {(r.stderr or '').strip()[:300]}")
            if copy.stat().st_size != row["size"] or sha256_of(copy) != row["sha256"]:
                raise PublishError(f"the file {row['file']} on the server does not match the manifest's size and sha256: not signing")
            copy.unlink()
            plain = {k: v for k, v in row.items() if k not in ("sig", "key", "commit")}
            if commit or row.get("commit"):
                plain["commit"] = commit or row["commit"]
            rows[platform] = self._signed(platform, plain, key_path)
            meta = scratch / "meta"
            meta.mkdir(mode=0o755)
            meta.chmod(0o755)
            body = self._body(rows)
            (meta / MANIFEST).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (meta / MANIFEST).chmod(0o644)
            self._copy_in(cid, meta)
        signed = rows[platform]
        self.out(f"Signed {PLATFORMS[platform][2]} {signed['version']} (key {signed['key']}"
                 f"{', commit ' + signed['commit'][:12] if signed.get('commit') else ''}): {base_url.rstrip('/')}/download/latest.json")
        return {"platform": platform, **signed}


class ServerPublisher(Publisher):
    def publish_server(self, manifest: Path, registry: str | None = None, releases: AppReleases | None = None) -> dict[str, Any]:
        """Puts a server release (release.json from the release workflow) into the manifest; the app rows stay as they are."""
        from .release import DEFAULT_REGISTRY, ReleaseError, parse

        manifest = Path(manifest).expanduser()
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise PublishError(f"could not read {manifest}: {type(e).__name__}") from None
        try:
            release = parse(raw, registry or (raw.get("registry") if isinstance(raw, dict) else None) or DEFAULT_REGISTRY)
        except ReleaseError as e:
            raise PublishError(f"{manifest.name} is not a valid server release: {e}") from None
        # a minimum app version may never be above an app people can install (app_releases.py)
        problems = (releases or AppReleases.load()).check_manifest(release.min_app)
        if problems:
            raise PublishError(f"{manifest.name} demands an app version that was never released, so it is not published:\n  "
                               + "\n  ".join(p.message for p in problems))
        cid = self.container()
        with tempfile.TemporaryDirectory(prefix="kelo-publish-") as tmp:
            scratch = Path(tmp)
            rows = self.current(cid, scratch)
            meta = scratch / "meta"
            meta.mkdir(mode=0o755)
            meta.chmod(0o755)
            body = self._body(rows, release.to_json())
            (meta / MANIFEST).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (meta / MANIFEST).chmod(0o644)
            self._copy_in(cid, meta)
        self.out(f"Published server release {release.version} ({release.channel}, {len(release.images)} images from {release.registry}).")
        return release.to_json()


class StorePublisher(Publisher):
    """Publishes the newest store release of each app (releases/apps.json) as a signed row of latest.json's "store" object."""

    def publish_store(self, record: AppReleases, key_path: Path | None, *, base_url: str = "https://kelo.chat") -> dict[str, dict[str, dict[str, Any]]]:
        if key_path is None:
            raise PublishError("no signing key: make one with `assistant release-key new` (an unsigned store row is ignored by every "
                               "server, so nothing is published)")
        wanted: dict[str, dict[str, dict[str, Any]]] = {}
        for platform, channel in release_sign.STORE_CHANNELS.items():
            rel = record.newest_store(platform)
            if rel is None:
                continue
            try:
                signed = release_sign.sign_store_row(platform, rel.store_row(), key_path, self.root)
            except (release_sign.SigningKeyError, ValueError) as e:
                raise PublishError(str(e)) from None
            if not release_sign.verify_store_row(platform, signed):
                raise PublishError("this key is not one the servers know: add its public key to release_keys.py and ReleaseKeys.kt first")
            entry = _store_entry(platform, signed)
            if entry is None:
                raise PublishError(f"the {platform} store row does not pass the checks: not published")
            wanted[platform] = {channel: entry}
        cid = self.container()
        with tempfile.TemporaryDirectory(prefix="kelo-store-") as tmp:
            scratch = Path(tmp)
            rows = self.current(cid, scratch)
            self.store_rows = wanted                       # platforms without a store release are left out
            meta = scratch / "meta"
            meta.mkdir(mode=0o755)
            meta.chmod(0o755)
            (meta / MANIFEST).write_text(json.dumps(self._body(rows), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (meta / MANIFEST).chmod(0o644)
            self._copy_in(cid, meta)
        shown = ", ".join(f"{p} {c[STORE_CHANNELS[p]]['version']} ({STORE_CHANNELS[p]})" for p, c in wanted.items()) or "none"
        self.out(f"Published store releases: {shown}: {base_url.rstrip('/')}/download/latest.json")
        return wanted


def run_publish_store(args: Any, root: Path | None = None, *, run: Callable[..., Any] = subprocess.run,
                      out: Callable[[str], None] = print, record: AppReleases | None = None) -> int:
    """`assistant publish-store`: re-publishes the newest store release of each app from releases/apps.json (signed), on the
    operator's server only. Also run by release-record for a store channel."""
    root = (root or Path.cwd()).resolve()
    public = _operator_public(root, out)
    if public is None:
        return 1
    try:
        if record is None:
            record = AppReleases.read(root / "releases" / "apps.json")
        StorePublisher(root, run=run, out=out).publish_store(record, _key_path(getattr(args, "sign_key", None)),
                                                            base_url=public or "https://kelo.chat")
    except (PublishError, RecordError) as e:
        out(f"error: {e}")
        return 1
    return 0


def run_publish_server(args: Any, root: Path | None = None, *, run: Callable[..., Any] = subprocess.run,
                       out: Callable[[str], None] = print, releases: AppReleases | None = None) -> int:
    """`assistant publish-server release.json`, on the operator's server only (the same rule as publish-app)."""
    from .cli import read_env

    root = (root or Path.cwd()).resolve()
    try:
        env = operator_env_file(root, operator_instance())
    except PublishError as e:
        out(f"error: {e}")
        return 1
    if not env.is_file() or not (root / "compose.yml").is_file():
        out("error: run this in the folder that holds compose.yml and .env (the main copy; the operator copy's settings when "
            f"{OPERATOR_INSTANCE_ENV} is set)")
        return 1
    get = lambda k: read_env(env, k)  # noqa: E731
    flag = operator_flag(get("ASSISTANT_OPERATOR"))
    if not (flag if flag is not None else names_itself(get("ASSISTANT_PUBLIC_URL"), get("ASSISTANT_OAUTH_BROKER"),
                                                        get("ASSISTANT_KELO_BROKER"), get("ASSISTANT_PUSH_BROKER"))):
        out("error: server releases are published only on the operator's server. If this is it, set ASSISTANT_OPERATOR=1 in .env.")
        return 1
    try:
        ServerPublisher(root, run=run, out=out).publish_server(Path(args.manifest), getattr(args, "registry", None), releases)
    except PublishError as e:
        out(f"error: {e}")
        return 1
    return 0


def run_publish(args: Any, root: Path | None = None, *, run: Callable[..., Any] = subprocess.run, out: Callable[[str], None] = print) -> int:
    """`assistant publish-app <android|windows|mac> <file> [--version X]`, on the operator's server only (the main copy that is
    the sign-in / push / Kelo broker its own settings point at, or ASSISTANT_OPERATOR=1 in .env)."""
    root = (root or Path.cwd()).resolve()
    public = _operator_public(root, out)
    if public is None:
        return 1
    unsigned = bool(getattr(args, "unsigned", False))
    key_path = None if unsigned else _key_path(getattr(args, "sign_key", None))
    try:
        row = Publisher(root, run=run, out=out).publish(args.platform, Path(args.file), args.version,
                                                        base_url=_origin(public) or "https://kelo.chat",
                                                        commit=getattr(args, "commit", None), key_path=key_path, unsigned=unsigned)
    except PublishError as e:
        out(f"error: {e}")
        return 1
    if args.platform not in SERVER_PLATFORMS:          # the server's installer is not an app release: nothing to record in apps.json
        record_published(root, row, out)
    return 0


def _operator_public(root: Path, out: Callable[[str], None]) -> str | None:
    """The public address from .env when this folder is the operator's main copy; None (after saying why) when it is not."""
    from .cli import read_env

    try:
        env = operator_env_file(root, operator_instance())
    except PublishError as e:
        out(f"error: {e}")
        return None
    if not env.is_file() or not (root / "compose.yml").is_file():
        out("error: run this in the folder that holds compose.yml and .env (the main copy; the operator copy's settings when "
            f"{OPERATOR_INSTANCE_ENV} is set)")
        return None
    get = lambda k: read_env(env, k)  # noqa: E731
    flag = operator_flag(get("ASSISTANT_OPERATOR"))
    public = get("ASSISTANT_PUBLIC_URL")
    if not (flag if flag is not None else names_itself(public, get("ASSISTANT_OAUTH_BROKER"), get("ASSISTANT_KELO_BROKER"),
                                                        get("ASSISTANT_PUSH_BROKER"))):
        out("error: app downloads are published only on the operator's server (the one other copies use as their broker). "
            "If this is it, set ASSISTANT_OPERATOR=1 in .env.")
        return None
    return public or ""


def _key_path(given: str | None) -> Path | None:
    """--sign-key, or the default ~/.kelo-release/signing.key when that file exists (None: no key, publish then says so)."""
    if given:
        return Path(given).expanduser()
    default = release_sign.DEFAULT_KEY_PATH.expanduser()
    return default if default.is_file() else None


def run_sign(args: Any, root: Path | None = None, *, run: Callable[..., Any] = subprocess.run, out: Callable[[str], None] = print) -> int:
    """`assistant sign-app <platform> [--commit HEX] [--sign-key PATH]`: signs the build that is already live (operator's server only)."""
    root = (root or Path.cwd()).resolve()
    public = _operator_public(root, out)
    if public is None:
        return 1
    try:
        Publisher(root, run=run, out=out).sign(args.platform, getattr(args, "commit", None), _key_path(getattr(args, "sign_key", None)),
                                               base_url=_origin(public) or "https://kelo.chat")
    except PublishError as e:
        out(f"error: {e}")
        return 1
    return 0


def run_release_key(args: Any, out: Callable[[str], None] = print, root: Path | None = None) -> int:
    """`assistant release-key new [--path P]`: makes the signing key and prints its id and PUBLIC key."""
    path = Path(getattr(args, "path", None) or release_sign.DEFAULT_KEY_PATH).expanduser()
    try:
        release_sign.check_location(path, root)
        kid, pub = release_sign.new_key(path)
    except release_sign.SigningKeyError as e:
        out(f"error: {e}")
        return 1
    out(f"Signing key written to {path} (mode 600; it never leaves this computer).")
    out(f"key id      {kid}")
    out(f"public key  {pub}")
    out("Add both to release_keys.py and ReleaseKeys.kt. Keep an offline copy of the private file (a USB stick or a password manager): "
        "a lost key means a new app release to change keys.")
    return 0


def record_published(root: Path, row: dict[str, Any], out: Callable[[str], None] = print) -> None:
    """A file offered at /download is live at once: add it to releases/apps.json (app_releases.py) when this folder has the repo's
    record, otherwise print the exact line to add. Never raises: the publish itself has succeeded."""
    from .app_releases import AppReleases, RecordError, line_for
    from .settings import desktop_version_code

    platform = "android" if row.get("platform") == "android" else "desktop"
    version = str(row.get("version") or "")
    build = row.get("version_code") if platform == "android" else desktop_version_code(version)
    target = root / "releases" / "apps.json"
    try:
        if build is None:
            raise RecordError("the build number is not known (aapt2 missing and none given with --version X+BUILD)")
        entry = line_for(platform, version, build, "download", now_date())
        if not target.is_file():
            out(f"Record this release (a copy without the repo's releases/apps.json): add to \"{platform}\" in releases/apps.json:\n  {entry}")
            return
        record = AppReleases.read(target)
        added = record.add(platform, version, build, "download", now_date())
        if added:
            record.save(target)
            out(f"Recorded {platform} {version} (build {build}) in {target}: commit it.")
    except RecordError as e:
        out(f"Not recorded in releases/apps.json ({e}). Record it by hand: assistant release-record {platform} {version or '<version>'} "
            f"{build if build is not None else '<build>'} download")


def now_date() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")
