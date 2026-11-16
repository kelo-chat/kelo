"""The work room, seen from the server: the agent's tools for running code and installing packages, the install proxy, and the
ledger of what the user allowed to be installed.

Design (see compose.yml, docker/workroom.Dockerfile, workroom_agent.py):
- The `workroom` container has no network at all and holds no secret. The server reaches its agent through a Unix socket in a
  volume only the two of them mount (`workroom-io`, group 1050). The work room cannot reach the server's API: it has no network,
  and the only thing the server serves in that folder is the install proxy below.
- Running code needs no approval (no network, no secrets, nothing outside its scratch folder); the chat shows a chip for it. What
  the code writes to $OUT_DIR is copied into the user's library (`files/`, see deliverables.py) within size caps. Pictures it writes
  to $VIEW_DIR are shown to the model only (ToolOutput images), never kept.
- Installing always asks the user (an approval card each time, unless the user chose to remember that one package). While an
  approved install runs, the server's proxy (proxy.sock) lets through HTTPS to the package registries only (REGISTRY_HOSTS), to
  public addresses only, and only with the one-time password of that install; before and after, it refuses everything. /work is
  empty while the network is on.
- The ledger (`.workroom/ledger.json` in the agent's home) lists every approved package with its version. When the work room image
  is rebuilt (an update: its build stamp changes), the packages are put back from the ledger before the next job.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

from .agent import Tool, ToolOutput
from .agent.fence import data
from .deliverables import Deliverables, LibraryFileError
from .home import Home, HomeError
from .hosting.channels import LocalChannel
from .i18n import t
from .netguard import NetGuardError, resolve_public
from .photos import PhotoError, view_bytes
from .quota import fmt_size

log = logging.getLogger("assistant.workroom")

LANGUAGES = ("python", "bash", "node")
KINDS = {"pip": "PyPI", "npm": "npm"}
REGISTRY_HOSTS = {"pip": ("pypi.org", "files.pythonhosted.org"), "npm": ("registry.npmjs.org",)}
MAX_TIMEOUT = 300
DEFAULT_TIMEOUT = 60
MAX_IN_FILES, MAX_IN_TOTAL = 20, 100 * 1024**2
MAX_OUT_FILES, MAX_OUT_FILE, MAX_OUT_TOTAL = 20, 20 * 1024**2, 50 * 1024**2
MAX_VIEWS = 8                # pictures a job may leave in $VIEW_DIR (the agent program caps them too)
SHOW_OUTPUT = 6_000          # characters of stdout / stderr the model sees (start and end kept)
MAX_CODE = 200_000
PIP_SPEC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}(\[[A-Za-z0-9,._-]{1,60}\])?((==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]{1,40})?$")
NPM_SPEC = re.compile(r"^(@[a-z0-9][a-z0-9._-]{0,60}/)?[a-z0-9][a-z0-9._-]{0,100}(@[A-Za-z0-9.^~<>=*_+-]{1,40})?$")
NOT_RUNNING = ("the work room is not running on this server (its container `workroom` is stopped or missing). Tell the user; "
               "the owner starts it with `docker compose up -d workroom`.")
NO_VISION_ROOM = ("[The model you are running on cannot look at pictures, so the rendered pictures were NOT seen. Do not claim you checked "
                  "how it looks; say the visual check was not possible.]")


class WorkroomError(Exception):
    """Something the agent should be told in plain words."""


class Runner(Protocol):
    async def call(self, path: str, body: dict[str, Any] | None, timeout: float) -> dict[str, Any]: ...


class SocketRunner:
    """Talks to workroom_agent.py over its channel (a Unix socket; a named pipe on Windows). GET when body is None.
    Takes a LocalChannel, or the path of the socket file."""

    def __init__(self, channel: LocalChannel | str | Path):
        self.channel = channel if isinstance(channel, LocalChannel) else LocalChannel.at_path(channel)

    async def call(self, path: str, body: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        if not self.channel.is_present():
            raise WorkroomError(NOT_RUNNING)
        transport = self.channel.make_http_transport()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://workroom", timeout=timeout, trust_env=False) as c:
                r = await (c.get(path) if body is None else c.post(path, json=body))
        except (httpx.ConnectError, FileNotFoundError, ConnectionRefusedError):
            raise WorkroomError(NOT_RUNNING) from None
        except httpx.TimeoutException:
            raise WorkroomError("the work room did not answer in time") from None
        try:
            data = r.json()
        except ValueError:
            raise WorkroomError(f"the work room answered HTTP {r.status_code} without a result") from None
        if r.status_code == 409:
            raise WorkroomError(str(data.get("error") or "the work room is busy"))
        return data if isinstance(data, dict) else {"error": "bad answer"}


# ---------------------------------------------------------------------------------------------- the install proxy
class InstallProxy:
    """An HTTPS CONNECT proxy on a local channel (a Unix socket), armed only for the length of one approved install.

    Refuses: anything while not armed, a wrong or missing one-time password, any method but CONNECT, any port but 443, any host
    not in the allowed registries, and any host that resolves to a private / loopback / link-local address (netguard)."""

    def __init__(self, channel: LocalChannel | str | Path, *, resolver: Callable[..., list] | None = None,
                 connect: Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]] | None = None,
                 max_bytes: int = 2 * 1024**3, idle: float = 120.0):
        self.channel = channel if isinstance(channel, LocalChannel) else LocalChannel.at_path(channel)
        self.resolver, self.max_bytes, self.idle = resolver, max_bytes, idle
        self._connect = connect or (lambda ip, port: asyncio.open_connection(ip, port))
        self._server: asyncio.base_events.Server | None = None
        self._password: str | None = None
        self._hosts: frozenset[str] = frozenset()
        self.contacted: list[str] = []
        self.refused: list[str] = []

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await self.channel.serve_async(self._handle)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self.channel.remove_leftover()

    def arm(self, hosts: tuple[str, ...]) -> str:
        self._password, self._hosts = secrets.token_urlsafe(24), frozenset(hosts)
        self.contacted, self.refused = [], []
        return self._password

    def disarm(self) -> None:
        self._password, self._hosts = None, frozenset()

    @property
    def armed(self) -> bool:
        return self._password is not None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
                return
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split()
            headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ":" in ln)}
            target = parts[1] if len(parts) == 3 else ""
            host, _, port = target.rpartition(":")
            host = host.strip("[]").lower()
            why = self._refusal(parts, headers, host, port)
            if why:
                self.refused.append(f"{host or '?'} ({why})")
                writer.write(f"HTTP/1.1 403 Forbidden\r\nContent-Length: {len(why)}\r\nConnection: close\r\n\r\n{why}".encode())
                await writer.drain()
                return
            try:
                ip = await resolve_public(host, self.resolver)
                up_r, up_w = await asyncio.wait_for(self._connect(ip, 443), 15)
            except (NetGuardError, OSError, asyncio.TimeoutError) as e:
                self.refused.append(f"{host} ({type(e).__name__})")
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            if host not in self.contacted:
                self.contacted.append(host)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            budget = [self.max_bytes]

            async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
                try:
                    while self.armed:
                        data = await asyncio.wait_for(src.read(65536), self.idle)
                        if not data:
                            break
                        budget[0] -= len(data)
                        if budget[0] < 0:
                            break
                        dst.write(data)
                        await dst.drain()
                except (OSError, asyncio.TimeoutError):
                    pass
                finally:
                    try:
                        dst.close()
                    except OSError:
                        pass

            await asyncio.gather(pump(reader, up_w), pump(up_r, writer))
        finally:
            try:
                writer.close()
            except OSError:
                pass

    def _refusal(self, parts: list[str], headers: dict[str, str], host: str, port: str) -> str:
        if self._password is None:
            return "no install is running"
        scheme, _, value = headers.get("proxy-authorization", "").partition(" ")
        try:
            given = base64.b64decode(value).decode().partition(":")[2] if scheme.lower() == "basic" else ""
        except (binascii.Error, UnicodeDecodeError):
            given = ""
        if not given or not secrets.compare_digest(given.encode(), self._password.encode()):
            return "wrong install pass"
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            return "only HTTPS through CONNECT"
        if port != "443":
            return "only port 443"
        if host not in self._hosts:
            return "not a package registry"
        return ""


# ---------------------------------------------------------------------------------------------- ledger
def normalize(kind: str, spec: str) -> str:
    """The package's name without version, as the registry compares names (pip: PEP 503)."""
    if kind == "pip":
        return re.sub(r"[-_.]+", "-", re.split(r"[=<>!~\[]", spec, maxsplit=1)[0]).lower()
    return re.sub(r"(?<=.)@.*$", "", spec).lower()


def check_install(kind: Any, packages: Any) -> str | None:
    if kind not in KINDS:
        return "kind must be pip (Python packages from PyPI) or npm (Node packages)"
    if not isinstance(packages, list) or not packages or len(packages) > 10:
        return "packages: a list of 1 to 10 package names"
    rx = PIP_SPEC if kind == "pip" else NPM_SPEC
    for p in packages:
        if not isinstance(p, str) or not rx.match(p.strip()):
            return f"{p!r} is not a plain package name (name, optionally with a version such as name==1.2 or name@1.2; no URLs or paths)"
    return None


def install_site(args: dict[str, Any]) -> str:
    """What an approval of one install is remembered under (a pseudo host per package), or "" when it cannot be remembered."""
    kind, packages = args.get("kind"), args.get("packages")
    if check_install(kind, packages) or len(packages) != 1:
        return ""
    name = normalize(str(kind), packages[0].strip())
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", name):
        return ""
    return f"{name}.{'pypi' if kind == 'pip' else 'npm'}.install"


class Ledger:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = path, clock

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("packages"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"build": "", "packages": []}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def packages(self) -> list[dict[str, Any]]:
        return self.load()["packages"]

    def record(self, kind: str, specs: list[str], versions: dict[str, str], reason: str) -> list[dict[str, Any]]:
        data = self.load()
        names = {normalize(kind, s): s for s in specs}
        keep = [p for p in data["packages"] if not (p["kind"] == kind and p["name"] in names)]
        added = [{"kind": kind, "name": n, "spec": s, "version": versions.get(n, ""), "reason": reason[:300], "installed_at": self.clock()}
                 for n, s in names.items()]
        data["packages"] = keep + added
        self.save(data)
        return added

    def forget(self, kind: str, specs: list[str]) -> None:
        data = self.load()
        names = {normalize(kind, s) for s in specs}
        data["packages"] = [p for p in data["packages"] if not (p["kind"] == kind and p["name"] in names)]
        self.save(data)

    def set_build(self, build: str) -> None:
        data = self.load()
        data["build"] = build
        self.save(data)


# ---------------------------------------------------------------------------------------------- the work room
def _clip(text: str, cap: int = SHOW_OUTPUT) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    head = cap * 2 // 3
    return text[:head] + f"\n[... {len(text) - cap} characters cut ...]\n" + text[-(cap - head):]


def _versions(kind: str, listed: list[dict[str, str]]) -> dict[str, str]:
    return {normalize(kind, r.get("name", "")): r.get("version", "") for r in listed if r.get("name")}


class Workroom:
    def __init__(self, home: Home, runner: Runner, *, files: Deliverables | None = None, proxy: InstallProxy | None = None,
                 ledger: Ledger | None = None):
        self.home, self.runner, self.proxy = home, runner, proxy
        self.files = files or Deliverables(home)
        self.ledger = ledger or Ledger(home.root / ".workroom" / "ledger.json")
        self.lock = asyncio.Lock()
        self._checked_build = ""

    # ------------------------------------------------------------------ inputs
    def _input(self, raw: str) -> tuple[str, bytes]:
        path = self.home.relativize(str(raw or "").strip())
        tail = Path(path.replace("\\", "/")).name
        try:
            if path.startswith("uploads/") or (not path.startswith("files/") and "/" not in path and (self.home.root / "uploads" / tail).is_file()):
                p = self.home.upload_path(tail)
            elif path.startswith("files/") or "/" not in path:
                p = self.files.path(tail)
            else:
                p, _ = self.home._resolve(path, must_exist=True)
                if not p.is_file():
                    raise HomeError(f"{path} is not a file")
        except HomeError as e:
            raise WorkroomError(f"files_in: {e}") from None
        if p.stat().st_size > MAX_IN_TOTAL:    # checked before reading: an upload can be a 2 GB video
            raise WorkroomError(f"files_in: {p.name} is larger than {MAX_IN_TOTAL // 1024**2} MB")
        return p.name, p.read_bytes()

    def inputs(self, files_in: Any) -> list[dict[str, str]]:
        if files_in in (None, "", []):
            return []
        if isinstance(files_in, str):
            files_in = [files_in]
        if not isinstance(files_in, list) or len(files_in) > MAX_IN_FILES:
            raise WorkroomError(f"files_in: a list of at most {MAX_IN_FILES} paths (uploads/<name> or files/<name>)")
        out, total, seen = [], 0, set()
        for raw in files_in:
            name, data = self._input(str(raw))
            total += len(data)
            if total > MAX_IN_TOTAL:
                raise WorkroomError(f"files_in: more than {MAX_IN_TOTAL // 1024**2} MB together")
            if name in seen:
                continue
            seen.add(name)
            out.append({"name": name, "b64": base64.b64encode(data).decode()})
        return out

    # ------------------------------------------------------------------ outputs
    def keep_outputs(self, files: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Copy what the code produced into the user's library, within the caps. Returns (kept, not kept)."""
        kept, refused, total = [], list(skipped or []), 0
        for f in files or []:
            name = str(f.get("name") or "file")
            try:
                data = base64.b64decode(str(f.get("b64", "")), validate=True)
            except (binascii.Error, ValueError):
                refused.append({"name": name, "bytes": 0, "reason": "unreadable"})
                continue
            if len(kept) >= MAX_OUT_FILES:
                refused.append({"name": name, "bytes": len(data), "reason": f"more than {MAX_OUT_FILES} files"})
            elif len(data) > MAX_OUT_FILE:
                refused.append({"name": name, "bytes": len(data), "reason": f"larger than {MAX_OUT_FILE // 1024**2} MB"})
            elif total + len(data) > MAX_OUT_TOTAL:
                refused.append({"name": name, "bytes": len(data), "reason": f"all outputs together over {MAX_OUT_TOTAL // 1024**2} MB"})
            else:
                try:
                    saved = self.files.save(name, data)
                except HomeError as e:
                    refused.append({"name": name, "bytes": len(data), "reason": str(e)})
                    continue
                total += len(data)
                kept.append({"name": saved, "path": f"files/{saved}", "bytes": len(data)})
        return kept, refused

    def decode_views(self, views: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The pictures the code put in $VIEW_DIR, decoded for the model. Never written to the library. Returns (pictures, not shown)."""
        shown, refused = [], list(skipped or [])
        for v in views or []:
            name = str(v.get("name") or "picture")
            if len(shown) >= MAX_VIEWS:
                refused.append({"name": name, "bytes": 0, "reason": f"more than {MAX_VIEWS} pictures"})
                continue
            try:
                raw = base64.b64decode(str(v.get("b64", "")), validate=True)
            except (binascii.Error, ValueError):
                refused.append({"name": name, "bytes": 0, "reason": "unreadable"})
                continue
            shown.append({"name": name, "bytes": len(raw), "raw": raw})
        return shown, refused

    # ------------------------------------------------------------------ after an update: put the approved packages back
    async def ensure_ready(self) -> str:
        """Checks the work room answers; when its image changed since the packages were installed, reinstalls them from the ledger.
        Returns a note for the agent ("" when nothing happened)."""
        health = await self.runner.call("/health", None, 10)
        build = str(health.get("build") or "")
        if not build or build == self._checked_build:
            return ""
        data = self.ledger.load()
        note = ""
        if data["build"] and data["build"] != build and data["packages"]:
            await self.runner.call("/reset", {}, 60)
            lines = []
            for kind in KINDS:
                specs = [f"{p['name']}=={p['version']}" if kind == "pip" and p.get("version") else
                         f"{p['name']}@{p['version']}" if p.get("version") else p["spec"] for p in data["packages"] if p["kind"] == kind]
                for i in range(0, len(specs), 10):
                    r = await self._install_now(kind, specs[i:i + 10])
                    lines.append(f"{kind}: {'ok' if r.get('exit_code') == 0 else 'failed'} ({', '.join(specs[i:i + 10])})")
            note = "[The work room was updated; the packages the user approved earlier were installed again: " + "; ".join(lines) + "]"
        self.ledger.set_build(build)
        self._checked_build = build
        return note

    async def _install_now(self, kind: str, specs: list[str]) -> dict[str, Any]:
        if self.proxy is None:
            raise WorkroomError("installing is not available on this server (no install proxy)")
        await self.proxy.start()
        password = self.proxy.arm(REGISTRY_HOSTS[kind])
        try:
            return await self.runner.call("/install", {"kind": kind, "packages": specs, "proxy_password": password, "timeout_s": 600}, 660)
        finally:
            self.proxy.disarm()

    # ------------------------------------------------------------------ jobs
    async def run(self, language: str, code: str, files_in: Any = None, timeout_s: Any = None) -> dict[str, Any]:
        if language not in LANGUAGES:
            raise WorkroomError(f"language must be one of {', '.join(LANGUAGES)}")
        if not isinstance(code, str) or not code.strip():
            raise WorkroomError("code is empty")
        if len(code) > MAX_CODE:
            raise WorkroomError(f"code longer than {MAX_CODE} characters; write it in parts or read data from files_in")
        try:
            timeout = max(1, min(int(timeout_s or DEFAULT_TIMEOUT), MAX_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        files = self.inputs(files_in)
        async with self.lock:
            note = await self.ensure_ready()
            # only code, the input files and a time limit go over: never a key, token or any of the server's environment
            r = await self.runner.call("/run", {"language": language, "code": code, "files": files, "timeout_s": timeout}, timeout + 60)
        if r.get("error"):
            raise WorkroomError(str(r["error"]))
        kept, refused = self.keep_outputs(r.get("files") or [], r.get("skipped") or [])
        views, not_viewed = self.decode_views(r.get("views") or [], r.get("skipped_views") or [])
        return {**{k: r.get(k) for k in ("exit_code", "stdout", "stderr", "timed_out", "killed_for", "duration_s")},
                "saved": kept, "not_saved": refused, "views": views, "skipped_views": not_viewed, "note": note}

    async def install(self, kind: str, packages: list[str], reason: str) -> dict[str, Any]:
        problem = check_install(kind, packages)
        if problem:
            raise WorkroomError(problem)
        specs = [p.strip() for p in packages]
        async with self.lock:
            note = await self.ensure_ready()
            r = await self._install_now(kind, specs)
            contacted, refused = list(self.proxy.contacted) if self.proxy else [], list(self.proxy.refused) if self.proxy else []
        if r.get("error"):
            raise WorkroomError(str(r["error"]))
        versions = _versions(kind, r.get("installed") or [])
        ok = r.get("exit_code") == 0
        missing = [s for s in specs if normalize(kind, s) not in versions]
        added = self.ledger.record(kind, [s for s in specs if normalize(kind, s) in versions], versions, reason) if ok else []
        return {"ok": ok and not missing, "installed": [{"name": a["name"], "version": a["version"]} for a in added], "missing": missing,
                "output": r.get("output", ""), "contacted": contacted, "refused": refused, "note": note}

    async def uninstall(self, kind: str, packages: list[str]) -> dict[str, Any]:
        problem = check_install(kind, packages)
        if problem:
            raise WorkroomError(problem)
        specs = [p.strip() for p in packages]
        async with self.lock:
            r = await self.runner.call("/uninstall", {"kind": kind, "packages": specs}, 180)
        if r.get("error"):
            raise WorkroomError(str(r["error"]))
        left = _versions(kind, r.get("installed") or [])
        gone = [s for s in specs if normalize(kind, s) not in left]
        self.ledger.forget(kind, gone)
        return {"ok": len(gone) == len(specs), "removed": gone, "output": r.get("output", "")}

    async def installed(self) -> list[dict[str, Any]]:
        return self.ledger.packages()


# ---------------------------------------------------------------------------------------------- agent tools
def _size(n: int) -> str:
    return fmt_size(int(n or 0))


def run_text(r: dict[str, Any]) -> str:
    head = f"exit code {r.get('exit_code')} ({r.get('duration_s')} s)"
    if r.get("killed_for") == "timeout":
        head += " - stopped: the time limit was reached"
    elif r.get("killed_for") == "disk":
        head += " - stopped: the job wrote more than the work room's scratch space allows"
    parts = [r["note"]] if r.get("note") else []
    parts.append(head)
    # what the program printed can quote its inputs (uploads, saved pages): marked as external data, fenced by the agent loop
    if (r.get("stdout") or "").strip():
        parts.append("stdout:\n" + data(_clip(r["stdout"])))
    if (r.get("stderr") or "").strip():
        parts.append("stderr:\n" + data(_clip(r["stderr"])))
    if r.get("saved"):
        parts.append("Saved in the user's library (files/): " + ", ".join(f"{f['name']} ({_size(f['bytes'])})" for f in r["saved"])
                     + ". Tell the user the file names; they find them under Results in the app.")
    else:
        parts.append("No files were produced (write results to $OUT_DIR to give the user a file).")
    if r.get("not_saved"):
        parts.append("Not saved: " + "; ".join(f"{f['name']} ({_size(f.get('bytes', 0))}): {f['reason']}" for f in r["not_saved"]))
    return "\n".join(parts)


def _pictures(shown: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """(name, data: URL) of each picture the job left in $VIEW_DIR, and the ones that are not pictures. Pillow work: run in a thread."""
    pics, refused = [], []
    for v in shown:
        try:
            pics.append((v["name"], view_bytes(v["raw"])[0]))
        except PhotoError:
            refused.append({"name": v["name"], "bytes": v["bytes"], "reason": "not a picture"})
    return pics, refused


def workroom_tools(room: Workroom, *, share: Callable[[str], dict[str, Any]] | None = None) -> list[Tool]:
    """workroom_run (no approval), workroom_install (approval each time unless remembered per package), workroom_uninstall,
    workroom_files (list / read the library and the installed packages) and, with `share`, share_file (approval every time)."""

    async def run(args: dict[str, Any]) -> Any:
        try:
            r = await room.run(str(args.get("language", "")), args.get("code", ""), args.get("files_in"), args.get("timeout_s"))
        except WorkroomError as e:
            return f"error: {e}"
        text = run_text(r)
        if not r.get("views") and not r.get("skipped_views"):
            return text
        # pictures go to the model only (never the library); the chat never gets the bytes
        pics, refused = await asyncio.to_thread(_pictures, r.get("views") or [])
        lines = [text]
        if pics:
            lines.append("Pictures from $VIEW_DIR follow (not saved to the user's files): " + ", ".join(name for name, _ in pics))
        refused = list(r.get("skipped_views") or []) + refused
        if refused:
            lines.append("Not shown from $VIEW_DIR: " + "; ".join(f"{f['name']} ({_size(f.get('bytes', 0))}): {f['reason']}" for f in refused))
        if not pics:
            return "\n".join(lines)
        return ToolOutput(text="\n".join(lines), images=[url for _, url in pics], no_vision=NO_VISION_ROOM)

    def validate_install(args: dict[str, Any]) -> str | None:
        problem = check_install(args.get("kind"), args.get("packages"))
        if problem:
            return problem
        if not str(args.get("reason") or "").strip():
            return "reason is required: say in one sentence why the user's task needs this package"
        return None

    async def describe_install(args: dict[str, Any]) -> str:
        pk = ", ".join(str(p) for p in args.get("packages") or [])
        return t("Install a program in the work room: {packages} ({registry}) — why: {reason}", packages=pk,
                 registry=KINDS.get(str(args.get("kind")), "?"), reason=str(args.get("reason") or "").strip()[:160])

    async def install(args: dict[str, Any]) -> str:
        try:
            r = await room.install(str(args["kind"]), list(args["packages"]), str(args.get("reason") or ""))
        except WorkroomError as e:
            return f"error: {e}"
        lines = [r["note"]] if r.get("note") else []
        if r["installed"]:
            lines.append("Installed: " + ", ".join(f"{p['name']} {p['version']}" for p in r["installed"]) + ". Kept across updates.")
        if r["missing"] or not r["ok"]:
            lines.append("Not installed: " + (", ".join(r["missing"]) or "see the output") + "\nThe installer's output:\n" + data(_clip(r["output"], 3000)))
        if r["refused"]:
            lines.append("Blocked by the work room's network rule (only package registries): " + ", ".join(r["refused"][:5]))
        return "\n".join(lines) or "nothing happened"

    async def uninstall(args: dict[str, Any]) -> str:
        try:
            r = await room.uninstall(str(args.get("kind", "")), list(args.get("packages") or []))
        except WorkroomError as e:
            return f"error: {e}"
        return ("Removed: " + ", ".join(r["removed"])) if r["removed"] else "Nothing was removed.\n" + data(_clip(r["output"], 2000))

    async def files(args: dict[str, Any]) -> str:
        action = str(args.get("action") or "list")
        if action == "read":
            try:
                return data(room.files.read_text(str(args.get("name") or "")))   # a library file can come from a site: fenced
            except HomeError as e:
                return f"error: {e}"
        rows = room.files.list()
        lines = [f"- files/{r['name']} ({_size(r['bytes'])}, {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['created']))})"
                 + (" [shared]" if r["shared_until"] else "") for r in rows[:60]]
        pk = room.ledger.packages()
        out = "Library (files/), newest first:\n" + ("\n".join(lines) if lines else "(empty)")
        out += "\nInstalled in the work room: " + (", ".join(f"{p['name']} {p['version']} ({p['kind']})" for p in pk) if pk else
                                                 "only what comes with it (python: numpy pandas scipy matplotlib seaborn plotly openpyxl "
                                                 "xlsxwriter python-docx python-pptx pypdf pillow; LibreOffice, ffmpeg, poppler, ImageMagick, node)")
        return out

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    tools = [
        Tool("workroom_run",
             "Run code in your work room, a private computer without internet, to analyse data or make and convert files and media. "
             "Inputs are in $IN_DIR; every file written to $OUT_DIR lands in the user's library (files/); print what you need to see; "
             "pictures written to $VIEW_DIR are shown to you (not saved). Read the work-room skill first.",
             obj({"language": {"type": "string", "enum": list(LANGUAGES)}, "code": {"type": "string"},
                  "files_in": {"type": "array", "items": {"type": "string"}, "description": "uploads/<name> or files/<name> to copy in"},
                  "timeout_s": {"type": "integer", "description": "up to 300 (default 60)"}}, ["language", "code"]), run),
        Tool("workroom_install",
             "Install a package the task needs into your work room; the user approves each install. Installed packages stay.",
             obj({"kind": {"type": "string", "enum": list(KINDS)},
                  "packages": {"type": "array", "items": {"type": "string"}, "description": "names, optionally pinned"},
                  "reason": {"type": "string", "description": "one sentence for the approval card"}}, ["kind", "packages", "reason"]), install,
             requires_approval=True, validate=validate_install, describe=describe_install,
             declined="The user declined the install. Do not retry it; do the task with what the work room already has, or ask the user."),
        Tool("workroom_uninstall", "Remove packages you installed in the work room.",
             obj({"kind": {"type": "string", "enum": list(KINDS)}, "packages": {"type": "array", "items": {"type": "string"}}},
                 ["kind", "packages"]), uninstall),
        Tool("workroom_files",
             "List the user's library of results (files/) and the work room's packages, or read a small text result by name.",
             obj({"action": {"type": "string", "enum": ["list", "read"]}, "name": {"type": "string"}}, []), files, read_only=True),
    ]
    if share is not None:
        async def share_file(args: dict[str, Any]) -> str:
            try:
                s = share(str(args.get("name") or ""))
            except (HomeError, LibraryFileError) as e:
                return f"error: {e}"
            return (f"Link for {s['name']} (anyone with it can download the file until {time.strftime('%Y-%m-%d %H:%M', time.localtime(s['expires_at']))}; "
                    f"the user can stop it in the app): {s['url']}")

        async def describe_share(args: dict[str, Any]) -> str:
            return t("Make a public link: {name} — anyone with the link can download it for 48 hours", name=Path(str(args.get("name") or "")).name)

        def validate_share(args: dict[str, Any]) -> str | None:
            try:
                room.files.path(str(args.get("name") or ""))
            except HomeError as e:
                return str(e)
            return None

        tools.append(Tool("share_file", "Make a 48-hour download link for a file of the user's library when they ask to share it; the user "
                          "approves every link.",
                          obj({"name": {"type": "string"}}, ["name"]), share_file, requires_approval=True, describe=describe_share,
                          validate=validate_share, declined="The user declined the link. Do not make it; ask how they want to share the file."))
    return tools
