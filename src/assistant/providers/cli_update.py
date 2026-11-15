"""A CLI sidecar updating its own CLI (claude or agy) without Docker: the new version goes into the sidecar's home volume.

The image keeps the copy it was built with (/opt/claude, /opt/agy: read-only). An update installs a newer one into
``$HOME/.kelo-cli/<kind>/<version>/`` (the volume that also holds the login), checks that it runs (``--version``, and for
claude that the login still reads as before), and only then switches ``$HOME/.kelo-cli/<kind>/state.json`` (written
atomically) to it. The runner uses whichever is newer, the volume's copy or the image's, so a later ``assistant update``
(a newer image) wins again by itself; a missing or broken volume copy falls back to the image. At most two versions stay in the
volume (the one in use and the one before it, for rollback); older ones are removed.

  claude  ``npm install -g --prefix <dir> @anthropic-ai/claude-code@<version>`` (npm and node come with the image)
  agy     Google's own installer (antigravity.google/cli/install.sh) with ``-d <dir>/bin``: it downloads the binary and checks
          its SHA-512 against the manifest from the same host; it always installs the latest version

Nothing here takes a command, URL or path from the caller: only an optional x.y.z version (claude), checked against a pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..i18n import N_
from . import cli_manage, releases

log = logging.getLogger("uvicorn.error")

NPM_PACKAGE = "@anthropic-ai/claude-code"
INSTALL_TIMEOUT = 600.0          # one install, download included
VERSION_TIMEOUT = 30.0           # `<cli> --version` (a native binary's first start can be slow on a small machine)
MIN_FREE = {"claude": 600 * 2**20, "agy": 300 * 2**20}   # room the install needs in the volume
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
BIN = {"claude": ("bin", "claude"), "agy": ("bin", "agy")}
# the variables every resolver of the binary reads (cli_manage.find_command, the vendored claude and agy clients)
COMMAND_ENV = {"claude": ("CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND",), "agy": ("AGY_COMMAND", "ANTIGRAVITY_COMMAND")}
PRODUCT = {"claude": "Claude Code", "agy": "Antigravity CLI"}
BUSY = N_("The CLI is answering a chat right now. Try again when it finishes")


class UpdateRefused(Exception):
    """An update cannot start now (one is running, a chat uses the CLI, a bad version): HTTP 409 / 400 with this English
    template (the server shows it in the user's language)."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class UpdateFailed(Exception):
    """Why an update did not happen: an English template (N_) with its fields, and the installer's own words when there are any."""

    def __init__(self, template: str, detail: str = "", **fields: Any) -> None:
        super().__init__(template.format(**fields) if fields else template)
        self.template, self.fields, self.detail = template, fields, detail


def _vtuple(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".")) if v and _SEMVER.match(v) else ()


def _base_env() -> dict[str, str]:
    """For npm / the installer: what a program needs to run and reach the internet; no token of any kind."""
    keep = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy")
    return {k: os.environ[k] for k in keep if k in os.environ}


async def _run(argv: list[str], *, env: dict[str, str], cwd: str | None = None, timeout: float) -> tuple[int, str]:
    """Run a fixed command; (exit code, last part of its combined output). Killed (whole group) after `timeout`."""
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        await proc.wait()
        raise UpdateFailed(N_("The update timed out after {seconds} seconds"), seconds=int(timeout)) from None
    return proc.returncode or 0, out.decode("utf-8", "replace")[-2000:]


def _cli_env(kind: str) -> dict[str, str]:
    return cli_manage.claude_env() if kind == "claude" else cli_manage.agy_env(dict(os.environ))


async def real_version(kind: str, path: str) -> str | None:
    """x.y.z from `<path> --version`, or None when it does not run."""
    try:
        code, out = await _run([path, "--version"], env=_cli_env(kind), timeout=VERSION_TIMEOUT)
    except (OSError, UpdateFailed):
        return None
    return releases.version_of(out) if code == 0 else None


async def real_logged_in(kind: str, path: str) -> bool | None:
    """claude: `claude auth status` (reads the saved login; sends no chat). agy: its token files (shared by every version)."""
    if kind != "claude":
        return None
    try:
        code, out = await _run([path, "auth", "status"], env=_cli_env(kind), timeout=VERSION_TIMEOUT)
        return bool(json.loads(out.strip().splitlines()[-1] if out.strip() else "{}").get("loggedIn"))
    except (OSError, UpdateFailed, ValueError, AttributeError, IndexError):
        return None


async def real_install(kind: str, version: str | None, target: Path, work: Path) -> str:
    """Install into `target` (a new, empty directory). Returns the installer's output (tail).
    HOME is the throw-away work folder: the installers' caches, staging and shell-profile edits (agy's installer appends a PATH
    line to ~/.bashrc on every run) go there and are deleted with it, so the real home keeps only the login."""
    env = {**_base_env(), "HOME": str(work)}
    if kind == "claude":
        npm = shutil.which("npm") or "/usr/local/bin/npm"
        argv = [npm, "install", "-g", "--prefix", str(target), "--cache", str(work / "npm-cache"), "--no-audit", "--no-fund",
                "--no-update-notifier", "--loglevel=warn", f"{NPM_PACKAGE}@{version}"]
        env["npm_config_update_notifier"] = "false"
        code, out = await _run(argv, env=env, cwd=str(work), timeout=INSTALL_TIMEOUT)
    else:
        import httpx

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
            r = await c.get(cli_manage.AGY_INSTALL_URL)
        if r.status_code != 200 or not r.content.startswith(b"#!") or len(r.content) > cli_manage._MAX_SCRIPT_BYTES:
            raise UpdateFailed(N_("Could not download the install script (HTTP {status})"), status=r.status_code)
        script = work / "install.sh"
        script.write_bytes(r.content)
        code, out = await _run(["bash", str(script), "-d", str(target / "bin")], env=env, cwd=str(work), timeout=INSTALL_TIMEOUT)
    if code != 0:
        raise UpdateFailed(N_("The install failed (exit {code})"), out[-600:].strip(), code=code)
    return out


async def real_latest(kind: str) -> str | None:
    return await releases.fetch(kind)


class CliUpdater:
    """One sidecar's CLI: which copy is used (image or volume), and updating it. All hooks are replaceable for tests."""

    def __init__(self, kind: str, *, root: Path | None = None, image_path: str | None = None,
                 install: Callable[[str, str | None, Path, Path], Awaitable[str]] = real_install,
                 version_of: Callable[[str, str], Awaitable[str | None]] = real_version,
                 logged_in: Callable[[str, str], Awaitable[bool | None]] = real_logged_in,
                 latest: Callable[[str], Awaitable[str | None]] = real_latest,
                 busy: Callable[[], bool] = lambda: False,
                 on_switch: Callable[[], None] = lambda: None) -> None:
        if kind not in BIN:
            raise ValueError(kind)
        self.kind = kind
        self.root = root or Path(os.environ.get("KELO_CLI_DIR") or Path.home() / ".kelo-cli") / kind
        if image_path is None:   # what the image put on PATH, read before any switch of ours changes the variables
            image_path = next((os.environ[v] for v in COMMAND_ENV[kind] if os.environ.get(v)), None) or shutil.which(kind)
        self.image_path = image_path
        self._install, self._version_of, self._logged_in, self._latest = install, version_of, logged_in, latest
        self.busy = busy
        self.on_switch = on_switch
        self.active_path: str | None = image_path
        self.active_version: str | None = None
        self.source = "image"
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[Any] | None = None
        self.progress: dict[str, Any] = {"state": "idle"}

    # ---- where things are
    def _bin(self, name: str) -> Path:
        return self.root.joinpath(name, *BIN[self.kind])

    def _read_state(self) -> dict[str, Any]:
        try:
            st = json.loads((self.root / "state.json").read_text())
            return st if isinstance(st, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, st: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".state.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(st))
        os.replace(tmp, self.root / "state.json")   # atomic: a reader sees the old or the new pointer, never half

    # ---- which copy to use
    async def select(self) -> dict[str, Any]:
        """Use the newest copy that runs: the volume's current one (or the one before it), or the image's. Sets the variables
        the CLI is found by in this process, so every later run, status and sign-in uses it."""
        st = self._read_state()
        image_ver = await self._version_of(self.kind, self.image_path) if self.image_path else None
        best = (self.image_path, image_ver, "image") if image_ver else (self.image_path, None, "image")
        for key in ("current", "previous"):
            name = st.get(key)
            if not isinstance(name, str) or not _SEMVER.match(name):
                continue
            path = self._bin(name)
            if not path.is_file():
                continue
            ver = await self._version_of(self.kind, str(path))
            if not ver:
                log.warning("cli update: %s %s in the volume does not run; not used", self.kind, name)
                continue
            if _vtuple(ver) > _vtuple(best[1]):
                best = (str(path), ver, "volume")
            break   # the current one runs (or the previous one when current did not): no need to look further
        changed = best[0] != self.active_path
        self.active_path, self.active_version, self.source = best
        for var in COMMAND_ENV[self.kind]:
            if self.active_path and self.source == "volume":
                os.environ[var] = self.active_path
            elif self.image_path:
                os.environ[var] = self.image_path
        if changed:
            cli_manage.forget_status(cli_manage.CLAUDE if self.kind == "claude" else cli_manage.AGY)
            self.on_switch()
        log.info("cli update: using %s %s from the %s", self.kind, self.active_version or "?", self.source)
        return self.info()

    def info(self) -> dict[str, Any]:
        return {"source": self.source, "version": self.active_version}

    # ---- updating
    def status(self) -> dict[str, Any]:
        return {**self.progress, "active": self.info()}

    def _set(self, **kw: Any) -> None:
        self.progress.update(kw)

    async def start(self, version: str | None = None) -> dict[str, Any]:
        """Begin an update in the background (GET status follows it). Refused while one runs or a chat uses the CLI."""
        if version is not None and version != "" and not _SEMVER.match(version):
            raise UpdateRefused(N_("The version must look like 1.2.3"), 400)
        if version and self.kind == "agy":
            raise UpdateRefused(N_("The Antigravity installer installs only the latest version"), 400)
        if self._task and not self._task.done():
            raise UpdateRefused(N_("An update is already running"))
        if self.busy():
            raise UpdateRefused(BUSY)
        self.progress = {"state": "running", "step": "checking", "from": self.active_version, "to": None, "error": None,
                         "started_at": time.time(), "finished_at": None}
        self._task = asyncio.get_running_loop().create_task(self._update(version or None))
        return self.status()

    async def wait(self) -> dict[str, Any]:
        """Tests and the CLI: the finished result."""
        if self._task:
            await asyncio.shield(self._task)
        return self.status()

    async def _update(self, version: str | None) -> None:
        async with self._lock:
            new_dir: Path | None = None
            try:
                await self.select()                       # what is in use right now (an image rebuilt meanwhile, say)
                old_path, old_ver = self.active_path, self.active_version
                self._set(**{"from": old_ver})
                target = version or await self._latest(self.kind)
                if not target or not _SEMVER.match(target):
                    raise UpdateFailed(N_("Could not find out the latest version. Check the server's internet connection"))
                self._set(to=target)
                if old_ver and _vtuple(target) <= _vtuple(old_ver):   # never older: the newer copy is used anyway
                    self._finish(ok=True, unchanged=True, to=old_ver)
                    return
                if self.busy():
                    raise UpdateFailed(BUSY)
                self.root.mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(self.root).free < MIN_FREE[self.kind]:
                    raise UpdateFailed(N_("Not enough free disk space for the update (it needs {mb} MB)"), mb=MIN_FREE[self.kind] // 2**20)
                st = self._read_state()
                keep = {st.get("current"), st.get("previous")}
                new_dir = self.root / target
                path = self._bin(target)
                ver = await self._version_of(self.kind, str(path)) if path.is_file() else None
                if ver != target:                         # not there yet (or broken): a fresh install
                    if new_dir.exists() and target not in keep:
                        shutil.rmtree(new_dir, ignore_errors=True)
                    elif new_dir.exists():                # the pointer names a broken copy: install beside it, then swap
                        new_dir = self.root / f".{target}.new"
                        shutil.rmtree(new_dir, ignore_errors=True)
                    self._set(step="installing")
                    with tempfile.TemporaryDirectory(prefix=".work-", dir=self.root) as work:
                        log.info("cli update: installing %s %s", self.kind, target)
                        await self._install(self.kind, version or target, new_dir, Path(work))
                    if new_dir.name != target:
                        shutil.rmtree(self.root / target, ignore_errors=True)
                        os.replace(new_dir, self.root / target)
                        new_dir = self.root / target
                    self._set(step="verifying")
                    ver = await self._version_of(self.kind, str(path)) if path.is_file() else None
                    if not ver:
                        raise UpdateFailed(N_("The new version does not start"))
                    if self.kind == "claude" and ver != target:
                        raise UpdateFailed(N_("Installed {got} instead of {want}"), got=ver, want=target)
                self._set(step="verifying", to=ver)
                if old_path:
                    before = await self._logged_in(self.kind, old_path)
                    after = await self._logged_in(self.kind, str(path))
                    if before is True and after is not True:
                        raise UpdateFailed(N_("The new version does not see the saved sign-in"))
                # switch: the version in use now becomes the one to go back to
                self._set(step="switching")
                prev = st.get("current") if st.get("current") != ver else st.get("previous")
                self._write_state({"current": ver, "previous": prev if prev != ver else None, "switched_at": time.time()})
                new_dir = None                            # it is the current one now: never removed below
                await self.select()
                if self.active_version != ver:            # the image is newer still: fine, nothing is lost
                    log.info("cli update: %s %s installed, the image's %s is newer and stays in use", self.kind, ver, self.active_version)
                self._cleanup()
                log.info("cli update: %s %s -> %s", self.kind, old_ver, self.active_version)
                self._finish(ok=True, to=self.active_version)
            except UpdateFailed as e:
                log.warning("cli update: %s failed: %s %s", self.kind, e, e.detail[-300:])
                self._finish(ok=False, error=str(e), error_key=e.template, error_fields=e.fields, error_detail=e.detail or None)
            except Exception as e:  # noqa: BLE001 - reported to the app; the old version keeps working
                log.warning("cli update: %s failed", self.kind, exc_info=True)
                key = N_("The update stopped unexpectedly ({error})")
                self._finish(ok=False, error=key.format(error=type(e).__name__), error_key=key,
                             error_fields={"error": type(e).__name__}, error_detail=str(e)[:300] or None)
            finally:
                if new_dir is not None and new_dir.exists() and new_dir.name not in {self._read_state().get(k) for k in ("current", "previous")}:
                    shutil.rmtree(new_dir, ignore_errors=True)

    def _finish(self, *, ok: bool, **kw: Any) -> None:
        self._set(state="done" if ok else "failed", step=None, ok=ok, finished_at=time.time(), **kw)

    def _cleanup(self) -> None:
        """Keep the current and the previous version; remove every other version directory and leftovers."""
        st = self._read_state()
        keep = {st.get("current"), st.get("previous"), "state.json"}
        for p in self.root.iterdir():
            if p.name in keep:
                continue
            if p.is_dir() and (_SEMVER.match(p.name) or p.name.startswith((".work-", "."))):
                shutil.rmtree(p, ignore_errors=True)
            elif p.name.startswith(".state."):
                p.unlink(missing_ok=True)
