"""The Kelo apps offered for download from the operator's website (https://kelo.chat/download/...).

The files live in a folder on the server's own state volume (`/state/downloads`, or ASSISTANT_DOWNLOADS_DIR), next to a manifest
the publisher writes (`assistant publish-app`), so a new build goes up without rebuilding the server image. The web routes only
read: a file is served when, and only when, the manifest names it for a platform, its name is a plain file name, it is a regular
file inside the folder (no links), and its size still matches what the manifest says (a copy still in progress is not served).
There is no listing of the folder.

manifest.json: {"platforms": {"android": {"file": "kelo-0.9.0-402500.apk", "version": "0.9.0", "version_code": 402500,
                                          "size": 28978903, "sha256": "<hex>", "date": "2026-11-08T09:00:00Z"}, "windows": {...}, ...}}
"version" is the name people see (and what older apps compare); "version_code" (when known) is the build number newer apps compare,
so 0.9.0 with a higher build is offered as an update (Android: versionCode; given as `--version 0.9.0+402500` or read from the APK).

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

from .app_releases import AppReleases

MANIFEST = "manifest.json"
# platform -> (file extension, media type, what the site calls it)
PLATFORMS: dict[str, tuple[str, str, str]] = {
    "android": (".apk", "application/vnd.android.package-archive", "Android"),
    "windows": (".exe", "application/vnd.microsoft.portable-executable", "Windows"),
    "windows-msi": (".msi", "application/x-msi", "Windows (MSI)"),
    "mac": (".dmg", "application/x-apple-diskimage", "macOS"),
}
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
VERSION = re.compile(r"^[0-9][0-9A-Za-z.-]{0,39}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
    return out


class Downloads:
    """The read side, used by the web routes and the website. Never writes."""

    def __init__(self, folder: str | Path | None):
        self.folder = Path(folder) if folder else None
        self._last: tuple[tuple[int, int], dict[str, dict[str, Any]]] | None = None
        self._server: dict[str, Any] | None = None   # the checked server release of the last good manifest

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
        self._server = server_entry(raw.get("server") if isinstance(raw, dict) else None)
        self._last = (key, out)
        return out

    def server(self) -> dict[str, Any] | None:
        """The server release (release.py manifest), checked; None when there is none or it does not pass."""
        self.manifest()
        return self._server

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

    def latest(self, base: str = "") -> dict[str, Any]:
        """The public summary (/download/latest.json): only platforms whose file is really there."""
        out: dict[str, Any] = {}
        for p in PLATFORMS:
            got = self.file(p)
            if got:
                row = got[1]
                out[p] = {"version": row["version"], "size": row["size"], "sha256": row["sha256"], "date": row["date"],
                          "file": row["file"], "url": f"{base}/download/{p}",
                          **({"version_code": row["version_code"]} if "version_code" in row else {})}
        server = self.server()
        if server:
            out["server"] = server
        return out


def server_entry(raw: Any) -> dict[str, Any] | None:
    """A server release manifest as latest.json shows it, or None when it does not pass release.parse (against its own registry:
    each server still checks it against the registry it is configured for)."""
    from .release import DEFAULT_REGISTRY, ReleaseError, parse

    if not isinstance(raw, dict):
        return None
    try:
        reg = raw.get("registry") if isinstance(raw.get("registry"), str) else DEFAULT_REGISTRY
        return parse(raw, reg).to_json()
    except ReleaseError:
        return None


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
        raise PublishError(f"unknown platform {platform!r}: android, windows or mac")
    if file.suffix.lower() != PLATFORMS[platform][0]:
        want = ".exe or .msi" if platform.startswith("windows") else PLATFORMS[platform][0]
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


class Publisher:
    """Copies a build into the downloads folder of the main copy's server container and updates the manifest there.

    `docker cp` writes into the state volume even though the container's own file system is read-only, and works whether or not the
    container is running. The file goes in first; the manifest that names it only after, so the site never offers a half-copied file.
    """

    def __init__(self, root: Path, *, run: Callable[..., Any] = subprocess.run, out: Callable[[str], None] = print,
                 folder: str = "/state/downloads"):
        self.root, self.run, self.out, self.folder = root, run, out, folder.rstrip("/")
        self.server_release: dict[str, Any] | None = None

    def container(self) -> str:
        r = self.run(["docker", "compose", "-f", str(self.root / "compose.yml"), "ps", "-a", "-q", "server"],
                     cwd=self.root, capture_output=True, text=True)
        cid = (r.stdout or "").strip().splitlines()[0].strip() if r.returncode == 0 and (r.stdout or "").strip() else ""
        if not cid:
            raise PublishError("the main copy's server container was not found here: run this in the folder that holds compose.yml "
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
        return {p: e for p, r2 in (rows.items() if isinstance(rows, dict) else ()) if (e := _entry(p, r2))}

    def _copy_in(self, cid: str, staged: Path) -> None:
        r = self.run(["docker", "cp", f"{staged}{os.sep}.", f"{cid}:{self.folder}"], capture_output=True, text=True)
        if r.returncode != 0:
            raise PublishError(f"docker cp failed: {(r.stderr or '').strip()[:300]}")

    def publish(self, platform: str, file: Path, version: str | None = None, *, base_url: str = "https://kelo.chat",
                now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
        file = Path(file).expanduser()
        if not file.is_file():
            raise PublishError(f"no such file: {file}")
        platform = platform_for(platform, file)
        version, code = versions_for(platform, file, version, self.run)
        name = f"kelo-{version}{f'-{code}' if code is not None else ''}{PLATFORMS[platform][0]}"
        size, sha = file.stat().st_size, sha256_of(file)
        cid = self.container()
        with tempfile.TemporaryDirectory(prefix="kelo-publish-") as tmp:
            scratch = Path(tmp)
            rows = self.current(cid, scratch)
            stage = scratch / "file"
            stage.mkdir(mode=0o755)
            stage.chmod(0o755)
            shutil.copyfile(file, stage / name)
            (stage / name).chmod(0o644)
            self.out(f"Copying {file.name} ({human_size(size)}) into the server as {name}...")
            self._copy_in(cid, stage)
            row = {"file": name, "version": version, "size": size, "sha256": sha,
                   "date": now().astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   **({"version_code": code} if code is not None else {})}
            rows[platform] = row
            meta = scratch / "meta"
            meta.mkdir(mode=0o755)
            meta.chmod(0o755)
            body = {"platforms": rows, **({"server": self.server_release} if self.server_release else {})}
            (meta / MANIFEST).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (meta / MANIFEST).chmod(0o644)
            self._copy_in(cid, meta)
        base = base_url.rstrip("/")
        self.out(f"Published {PLATFORMS[platform][2]} {version}{f' (build {code})' if code is not None else ''}: {base}/download/{platform}")
        self.out(f"  file   {base}/download/{platform}/{name}")
        self.out(f"  sha256 {sha}")
        self.out(f"  list   {base}/download/latest.json")
        return {"platform": platform, **row, "url": f"{base}/download/{platform}"}


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
            body = {"platforms": rows, "server": release.to_json()}
            (meta / MANIFEST).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (meta / MANIFEST).chmod(0o644)
            self._copy_in(cid, meta)
        self.out(f"Published server release {release.version} ({release.channel}, {len(release.images)} images from {release.registry}).")
        return release.to_json()


def run_publish_server(args: Any, root: Path | None = None, *, run: Callable[..., Any] = subprocess.run,
                       out: Callable[[str], None] = print, releases: AppReleases | None = None) -> int:
    """`assistant publish-server release.json`, on the operator's server only (the same rule as publish-app)."""
    from .cli import read_env

    root = (root or Path.cwd()).resolve()
    env = root / ".env"
    if not env.is_file() or not (root / "compose.yml").is_file():
        out("error: run this in the folder that holds compose.yml and .env (the main copy)")
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
    from .cli import read_env

    root = (root or Path.cwd()).resolve()
    env = root / ".env"
    if not env.is_file() or not (root / "compose.yml").is_file():
        out("error: run this in the folder that holds compose.yml and .env (the main copy)")
        return 1
    get = lambda k: read_env(env, k)  # noqa: E731
    flag = operator_flag(get("ASSISTANT_OPERATOR"))
    public = get("ASSISTANT_PUBLIC_URL")
    if not (flag if flag is not None else names_itself(public, get("ASSISTANT_OAUTH_BROKER"), get("ASSISTANT_KELO_BROKER"),
                                                        get("ASSISTANT_PUSH_BROKER"))):
        out("error: app downloads are published only on the operator's server (the one other copies use as their broker). "
            "If this is it, set ASSISTANT_OPERATOR=1 in .env.")
        return 1
    try:
        row = Publisher(root, run=run, out=out).publish(args.platform, Path(args.file), args.version,
                                                        base_url=_origin(public) or "https://kelo.chat")
    except PublishError as e:
        out(f"error: {e}")
        return 1
    record_published(root, row, out)
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
