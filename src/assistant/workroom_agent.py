"""The work room's own small program: runs code and installs packages for the assistant server, inside the `workroom` container.

Standard library only (the work room image does not carry this project's dependencies). It listens on a Unix socket in a volume
that only the server and the work room mount; the work room has no network at all (compose: network_mode none). Nothing secret is
ever in this container: no ASSISTANT_TOKEN, no keys, no database. The server sends code and input files, gets back the output and the
files the code wrote to $OUT_DIR. Every job starts from an empty /work, and every process a job left behind is killed after it.

Installing (pip / npm into /tools, the persistent volume) is the only time anything leaves the machine: the agent opens a loopback
port that forwards to the server's install proxy (proxy.sock in the same volume), which lets through only package registries, and
only while the server has armed it for this one install with a one-time password. /work is empty during an install.

    POST /run      {"language": python|bash|node, "code", "files": [{"name", "b64"}], "timeout_s"}
                   -> {"exit_code", "stdout", "stderr", "timed_out", "killed_for", "duration_s", "files": [{"name", "bytes", "b64"}],
                       "skipped": [{"name", "bytes", "reason"}]}
    POST /install  {"kind": pip|npm, "packages": [...], "proxy_password", "timeout_s"} -> {"exit_code", "output", "installed": [{"name", "version"}]}
    POST /uninstall {"kind", "packages"} -> {"exit_code", "output"}
    POST /list     {"kind"?} -> {"pip": [{"name", "version"}], "npm": [...]}
    POST /reset    {} -> {"ok"}   (empties /tools/py and /tools/npm, for a reinstall after an update)
    GET  /health   -> {"ok", "build", "busy", "tools_bytes"}
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

SOCK_DIR = Path(os.environ.get("WORKROOM_SOCKET_DIR", "/run/workroom"))
WORK = Path(os.environ.get("WORKROOM_WORK", "/work"))
TOOLS = Path(os.environ.get("WORKROOM_TOOLS", "/tools"))
def _build_id() -> str:
    try:   # stamped when the image's programs were installed: a new stamp means user-installed packages must be put back
        return Path("/opt/workroom/build").read_text().strip() or "dev"
    except OSError:
        return os.environ.get("WORKROOM_BUILD", "dev")


BUILD = _build_id()
PY_SITE = f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
_DISK = (os.environ.get("WORKROOM_DISK") or "").strip()
# bytes a job may write under /work (and /tools may hold): unset = 2 GB, 0 = no cap
DISK_LIMIT = 2 * 1024**3 if not _DISK.isdigit() else (int(_DISK) or 1 << 50)

OUT_KEEP_HEAD, OUT_KEEP_TAIL = 48_000, 16_000      # characters of stdout / stderr returned (start and end)
MAX_OUT_FILES, MAX_OUT_FILE, MAX_OUT_TOTAL = 20, 20 * 1024**2, 50 * 1024**2
MAX_REQUEST = 120 * 1024**2
MAX_TIMEOUT = 300
INSTALL_TIMEOUT = 600
LANGS = {"python": ("main.py", ["python3", "main.py"]), "bash": ("main.sh", ["bash", "main.sh"]), "node": ("main.js", ["node", "main.js"])}
# what pip / npm accept as a package to install: a name with an optional version (no URLs, paths, options or extras with spaces)
PIP_SPEC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}(\[[A-Za-z0-9,._-]{1,60}\])?((==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]{1,40})?$")
NPM_SPEC = re.compile(r"^(@[a-z0-9][a-z0-9._-]{0,60}/)?[a-z0-9][a-z0-9._-]{0,100}(@[A-Za-z0-9.^~<>=*_+-]{1,40})?$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-가-힣 ()]+")

_job_lock = threading.Lock()


def safe_name(name: str) -> str:
    base = SAFE_NAME.sub("_", Path(str(name).replace("\\", "/")).name).strip(" .")
    return base[:120] or "file"


def clip(text: str) -> str:
    if len(text) <= OUT_KEEP_HEAD + OUT_KEEP_TAIL:
        return text
    return text[:OUT_KEEP_HEAD] + f"\n[... {len(text) - OUT_KEEP_HEAD - OUT_KEEP_TAIL} characters cut ...]\n" + text[-OUT_KEEP_TAIL:]


def tree_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def empty_dir(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for p in root.iterdir():
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass


def sweep() -> int:
    """Kill every process in this container except this program and PID 1 (tini): nothing a job started outlives it."""
    me, n = os.getpid(), 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) in (me, 1):
            continue
        try:
            os.kill(int(entry), signal.SIGKILL)
            n += 1
        except (ProcessLookupError, PermissionError):
            pass
    return n


def job_env(job: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """The whole environment a job sees, built from nothing (never this program's own environment)."""
    env = {
        "PATH": f"{TOOLS}/py/bin:{TOOLS}/npm/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(job / "home"), "TMPDIR": str(job / "tmp"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(TOOLS / "py" / PY_SITE), "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "NODE_PATH": f"{TOOLS}/npm/node_modules", "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(job / "home" / ".mpl"),
        "IN_DIR": str(job / "in"), "OUT_DIR": str(job / "out"), "SAL_USE_VCLPLUGIN": "svp",
    }
    env.update(extra or {})
    return env


def _set_limits() -> None:   # in the child, before exec
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (DISK_LIMIT, DISK_LIMIT))
    os.umask(0o077)


def run_process(argv: list[str], cwd: Path, env: dict[str, str], timeout: float, watch: Path | None = None) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, preexec_fn=_set_limits)
    bufs: dict[str, list[bytes]] = {"out": [], "err": []}
    sizes = {"out": 0, "err": 0}
    cap = (OUT_KEEP_HEAD + OUT_KEEP_TAIL) * 4

    def reader(stream: Any, key: str) -> None:
        tail = bytearray()
        for chunk in iter(lambda: stream.read1(65536), b""):
            if sizes[key] < cap:
                bufs[key].append(chunk[: cap - sizes[key]])
            else:
                tail = (tail + chunk)[-OUT_KEEP_TAIL * 4:]
            sizes[key] += len(chunk)
        if tail:
            bufs[key].append(b"\n[... output cut ...]\n" + bytes(tail))

    threads = [threading.Thread(target=reader, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, "err"), daemon=True)]
    for t in threads:
        t.start()
    killed_for = ""
    while proc.poll() is None:
        if time.monotonic() - started > timeout:
            killed_for = "timeout"
        elif watch is not None and tree_bytes(watch) > DISK_LIMIT:
            killed_for = "disk"
        if killed_for:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            break
        time.sleep(0.2)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    for t in threads:
        t.join(timeout=5)
    dec = lambda k: clip(b"".join(bufs[k]).decode("utf-8", "replace"))  # noqa: E731
    return {"exit_code": proc.returncode, "stdout": dec("out"), "stderr": dec("err"), "timed_out": killed_for == "timeout",
            "killed_for": killed_for, "duration_s": round(time.monotonic() - started, 2)}


def collect_outputs(out: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Files the code left in $OUT_DIR (subfolders flattened into names), within the size caps."""
    files, skipped, total = [], [], 0
    found = []
    for dirpath, dirs, names in os.walk(out):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(dirpath, d)))
        for n in sorted(names):
            p = Path(dirpath) / n
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(out).as_posix()
            found.append((rel, p))
    for rel, p in found:
        size = p.stat().st_size
        name = safe_name(rel.replace("/", "_"))
        if size == 0:
            skipped.append({"name": name, "bytes": 0, "reason": "empty"})
        elif len(files) >= MAX_OUT_FILES:
            skipped.append({"name": name, "bytes": size, "reason": f"more than {MAX_OUT_FILES} files"})
        elif size > MAX_OUT_FILE:
            skipped.append({"name": name, "bytes": size, "reason": f"larger than {MAX_OUT_FILE // 1024**2} MB"})
        elif total + size > MAX_OUT_TOTAL:
            skipped.append({"name": name, "bytes": size, "reason": f"all outputs together over {MAX_OUT_TOTAL // 1024**2} MB"})
        else:
            files.append({"name": name, "bytes": size, "b64": base64.b64encode(p.read_bytes()).decode()})
            total += size
    return files, skipped


def do_run(body: dict[str, Any]) -> dict[str, Any]:
    lang = str(body.get("language", ""))
    if lang not in LANGS:
        return {"error": f"language must be one of {', '.join(LANGS)}"}
    timeout = max(1, min(int(body.get("timeout_s") or 60), MAX_TIMEOUT))
    sweep()
    empty_dir(WORK)
    job = WORK / "job"
    for d in ("in", "out", "home", "tmp"):
        (job / d).mkdir(parents=True, exist_ok=True)
    for f in body.get("files") or []:
        (job / "in" / safe_name(f.get("name", ""))).write_bytes(base64.b64decode(f.get("b64", "")))
    script, argv = LANGS[lang]
    (job / script).write_text(str(body.get("code", "")), encoding="utf-8")
    try:
        result = run_process(argv, job, job_env(job), timeout, watch=WORK)
    finally:
        sweep()
    result["files"], result["skipped"] = collect_outputs(job / "out")
    empty_dir(WORK)
    return result


# ---------------------------------------------------------------------------------------------- installing
class Forwarder:
    """127.0.0.1:<port> -> the server's install proxy socket. Lives only for one install."""

    def __init__(self, target: Path):
        self.target = target
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        self.stopped = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self.stopped:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._pipe_pair, args=(client,), daemon=True).start()

    def _pipe_pair(self, client: socket.socket) -> None:
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(str(self.target))
        except OSError:
            client.close()
            return

        def pump(a: socket.socket, b: socket.socket) -> None:
            try:
                while True:
                    data = a.recv(65536)
                    if not data:
                        break
                    b.sendall(data)
            except OSError:
                pass
            finally:
                for s in (a, b):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t = threading.Thread(target=pump, args=(upstream, client), daemon=True)
        t.start()
        pump(client, upstream)
        t.join(timeout=60)
        client.close()
        upstream.close()

    def stop(self) -> None:
        self.stopped = True
        self.sock.close()


def installed(kind: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if kind == "pip":
        site = TOOLS / "py" / PY_SITE
        if site.is_dir():
            for d in sorted(site.glob("*.dist-info")):
                meta = d / "METADATA"
                name = version = ""
                try:
                    for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.startswith("Name: "):
                            name = line[6:].strip()
                        elif line.startswith("Version: "):
                            version = line[9:].strip()
                        elif not line.strip():
                            break
                except OSError:
                    continue
                if name:
                    out.append({"name": name, "version": version})
    elif kind == "npm":
        mods = TOOLS / "npm" / "node_modules"
        if mods.is_dir():
            dirs = [p for p in mods.iterdir() if p.is_dir() and not p.name.startswith(".")]
            for p in sorted(dirs):
                for d in (sorted(p.iterdir()) if p.name.startswith("@") else [p]):
                    try:
                        pkg = json.loads((d / "package.json").read_text(encoding="utf-8"))
                        out.append({"name": str(pkg.get("name") or d.name), "version": str(pkg.get("version") or "")})
                    except (OSError, ValueError):
                        continue
    return out


def _top_level(kind: str) -> list[str]:
    if kind == "npm":
        try:
            return list(json.loads((TOOLS / "npm" / "package.json").read_text(encoding="utf-8")).get("dependencies", {}))
        except (OSError, ValueError):
            return []
    return []


def check_specs(kind: str, packages: Any) -> list[str] | str:
    if kind not in ("pip", "npm"):
        return "kind must be pip or npm"
    if not isinstance(packages, list) or not packages or len(packages) > 10:
        return "packages: 1 to 10 names"
    rx = PIP_SPEC if kind == "pip" else NPM_SPEC
    bad = [p for p in packages if not isinstance(p, str) or not rx.match(p)]
    return f"not a package name: {bad[0]!r}" if bad else [str(p) for p in packages]


def do_install(body: dict[str, Any]) -> dict[str, Any]:
    kind = str(body.get("kind", ""))
    specs = check_specs(kind, body.get("packages"))
    if isinstance(specs, str):
        return {"error": specs}
    password = str(body.get("proxy_password", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", password):
        return {"error": "missing install pass"}
    timeout = max(30, min(int(body.get("timeout_s") or INSTALL_TIMEOUT), INSTALL_TIMEOUT))
    sweep()
    empty_dir(WORK)                       # no user files anywhere while the network is on
    job = WORK / "install"
    for d in ("home", "tmp"):
        (job / d).mkdir(parents=True, exist_ok=True)
    fwd = Forwarder(SOCK_DIR / "proxy.sock")
    proxy = f"http://kelo:{password}@127.0.0.1:{fwd.port}"
    env = job_env(job, {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy, "NO_PROXY": "", "no_proxy": ""})
    env.pop("PYTHONPATH", None)
    try:
        if kind == "pip":
            (TOOLS / "py").mkdir(parents=True, exist_ok=True)
            # -I: nothing from /tools (or the environment) is loaded into pip itself
            argv = ["python3", "-I", "-m", "pip", "install", "--prefix", str(TOOLS / "py"), "--no-cache-dir", "--disable-pip-version-check",
                    "--no-input", "--progress-bar", "off", "--prefer-binary", "--no-warn-script-location", *specs]
        else:
            (TOOLS / "npm").mkdir(parents=True, exist_ok=True)
            (TOOLS / "npm" / ".npmrc").unlink(missing_ok=True)   # no settings left in /tools by earlier code
            env.update({"npm_config_proxy": proxy, "npm_config_https_proxy": proxy, "npm_config_cache": str(job / "npm-cache"),
                        "npm_config_update_notifier": "false"})
            argv = ["npm", "install", "--prefix", str(TOOLS / "npm"), "--ignore-scripts", "--no-audit", "--no-fund", "--userconfig", "/dev/null",
                    "--globalconfig", "/dev/null", "--registry", "https://registry.npmjs.org/", *specs]
        r = run_process(argv, job, env, timeout, watch=None)
    finally:
        fwd.stop()
        sweep()
        empty_dir(WORK)
    tools_bytes = tree_bytes(TOOLS)
    note = f"\n[warning: installed programs now take {tools_bytes // 1024**2} MB, over the work room's {DISK_LIMIT // 1024**2} MB]" \
        if tools_bytes > DISK_LIMIT else ""
    return {"exit_code": r["exit_code"], "output": clip((r["stdout"] + "\n" + r["stderr"]).strip()) + note, "timed_out": r["timed_out"],
            "installed": installed(kind), "tools_bytes": tools_bytes}


def do_uninstall(body: dict[str, Any]) -> dict[str, Any]:
    kind = str(body.get("kind", ""))
    specs = check_specs(kind, body.get("packages"))
    if isinstance(specs, str):
        return {"error": specs}
    sweep()
    empty_dir(WORK)
    job = WORK / "uninstall"
    for d in ("home", "tmp"):
        (job / d).mkdir(parents=True, exist_ok=True)
    names = [re.split(r"[=<>!~\[]", s)[0] if kind == "pip" else re.sub(r"(?<=.)@.*$", "", s) for s in specs]
    if kind == "pip":
        argv = ["python3", "-m", "pip", "uninstall", "-y", "--disable-pip-version-check", *names]
    else:
        argv = ["npm", "uninstall", "--prefix", str(TOOLS / "npm"), "--ignore-scripts", "--offline", "--no-audit", "--no-fund",
                "--userconfig", "/dev/null", "--globalconfig", "/dev/null", *names]
    try:
        r = run_process(argv, job, job_env(job), 120)
    finally:
        sweep()
        empty_dir(WORK)
    return {"exit_code": r["exit_code"], "output": clip((r["stdout"] + "\n" + r["stderr"]).strip()), "installed": installed(kind)}


def do_reset(_: dict[str, Any]) -> dict[str, Any]:
    sweep()
    for d in ("py", "npm"):
        shutil.rmtree(TOOLS / d, ignore_errors=True)
    return {"ok": True}


# ---------------------------------------------------------------------------------------------- HTTP on a Unix socket
ROUTES = {"/run": do_run, "/install": do_install, "/uninstall": do_uninstall, "/reset": do_reset,
          "/list": lambda b: {"pip": installed("pip"), "npm": installed("npm"), "top_npm": _top_level("npm")}}


class Handler(BaseHTTPRequestHandler):
    server_version = "workroom"
    sys_version = ""

    def address_string(self) -> str:   # Unix sockets have no client address
        return "server"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("workroom: " + (fmt % args) + "\n")

    def _send(self, code: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            return self._send(404, {"error": "not found"})
        busy = not _job_lock.acquire(blocking=False)
        if not busy:
            _job_lock.release()
        self._send(200, {"ok": True, "build": BUILD, "busy": busy, "disk_limit": DISK_LIMIT})

    def do_POST(self) -> None:  # noqa: N802
        fn = ROUTES.get(self.path)
        if fn is None:
            return self._send(404, {"error": "not found"})
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_REQUEST:
            return self._send(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad JSON"})
        if not _job_lock.acquire(timeout=5):
            return self._send(409, {"error": "the work room is busy with another job"})
        try:
            result = fn(body if isinstance(body, dict) else {})
        except Exception as e:  # noqa: BLE001 - reported to the server, never crash the agent
            result = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        finally:
            _job_lock.release()
        self._send(200 if "error" not in result else 422, result)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def health_check() -> int:
    """For the container health check: 0 when the agent answers."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(str(SOCK_DIR / "agent.sock"))
        s.sendall(b"GET /health HTTP/1.0\r\n\r\n")
        return 0 if b" 200 " in s.recv(200) else 1
    except OSError:
        return 1
    finally:
        s.close()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        sys.exit(health_check())
    try:   # not dumpable: code running as the same user cannot attach to this process or read its memory
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
    except (OSError, AttributeError):
        pass
    SOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = SOCK_DIR / "agent.sock"
    path.unlink(missing_ok=True)
    srv = Server(str(path), Handler)
    os.chmod(path, 0o660)              # the server reaches it through the shared group; nobody else mounts this folder
    empty_dir(WORK)
    sys.stderr.write(f"workroom: ready (build {BUILD})\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()
