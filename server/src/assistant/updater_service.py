"""The updater: updates this computer's Kelo copies to the newest signed release when the owner presses Update in the app.

It runs as its own container (compose service `updater`, profile `updater`, started with the main copy) and it is the ONLY container
that has the Docker socket. It has no AI, no model keys of its own, and reads no web pages or mail. It listens on the internal network
`updater-net`, which only the main copy's server shares (the updater attaches that one container to it, attach_server(), so the other
copies never get the network; no port is published; it reaches the internet only through `updater-out`,
for the release list and the signature checks), and it answers only requests that carry UPDATER_TOKEN, a secret made by
`assistant setup` (like CLI_RUNNER_TOKEN for the CLI sidecars) that only the main copy's server and this container get.

The commands are fixed and take no parameters (a body other than nothing or `{}` is refused):

    GET  /healthz     liveness (no token)
    GET  /status      mode (registry | local), current and latest version, the running or last job with its steps, rollback point
    POST /check       read the release list again (kelo.chat/download/latest.json, key "server"; KELO_RELEASES_URL)
    POST /update      update every copy to the release in that list            202 + status | 409 {"detail", "code"}
    POST /rollback    go back to the rollback point of the last update          202 + status | 409 {"detail", "code"}

An update runs exactly the flow of `assistant update` (updates.Updater, the same code): preflight, rollback point, backup, then the
new images, restart, health check, and a rollback by itself on failure. Only the step that gets the images differs: RegistrySource
pulls Kelo's own images of the release (server, which also runs the browser and egress roles, workroom, updater; the Claude / agy
CLI sidecars hold proprietary programs, so they are never published: they stay built on this machine as they are, and each CLI
updates itself from the app) by its digest from the configured registry (KELO_REGISTRY, ghcr.io/kelo-chat by default), checks
the digest, the cosign signature and the version label (release.Verifier), and only then puts them on the shared tags. Nothing
in the release list is ever run as a command, mounted or used as a free-form tag: it can only name `<registry>/<component>@sha256:..`.
The main copy is updated first, then every extra copy (they share the images; one shared updater for the computer, so a copy's
owner cannot update the other people's copies: only the main copy's server reaches it).

The updater updates itself last: when every copy is healthy, it starts a one-shot container of the NEW updater image (no network,
the socket and the Kelo folder only) that tags it, recreates the `updater` service, waits for it to be healthy, and puts the old
image back if it is not (`swap` below). The job's state lives in instances/.updater/state.json, so the new updater reports how the
old one's job ended.

A copy whose images were built here from the source files (no `chat.kelo.release` label) is reported as mode "local": update it
from the source with `kelo update`; the app shows that and no button. The same goes for an image built with Google Chrome.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import release as rel
from .cli import read_env
from .instances import IMAGE_VARS, MAIN, Instance, InstanceError, Instances
from .updates import UpdateError, UpdateLock, Updater

PORT = 9400
UPDATER_VAR = "KELO_UPDATER_IMAGE"
UPDATER_IMAGE = "assistant-updater:local"
STATE = "state.json"
MAX_LOG = 200
HEARTBEAT = 60.0
SWAP_TIMEOUT = 120.0
# how far along the app's progress bar each step is (the restart and health steps repeat for each copy)
PROGRESS = {"queued": 1, "preflight": 5, "backup": 12, "build": 20, "download": 25, "verify": 50, "restart": 65, "health": 75,
            "rollback": 85, "self_update": 95, "done": 100}
APP_STEP = {"build": "download"}           # updates.Updater's "build" is the download when the images come from a release
COMPONENT_OF_VAR = {v: c for c, v in rel.COMPONENTS.items()}


class Refused(Exception):
    def __init__(self, code: str, detail: str, status: int = 409):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def shared_updater_image() -> str:
    return os.environ.get(UPDATER_VAR) or UPDATER_IMAGE


def incoming_updater_image() -> str:
    """Where a new updater image waits until the swap (never the tag the running updater uses: compose would recreate it mid-job)."""
    tag = shared_updater_image()
    head, _, last = tag.rpartition(":")
    repo = head if head and "/" not in last else tag
    return f"{repo}:incoming"


# ---------------------------------------------------------------------------------------------------------------- health over exec
# Runs inside a copy's server container (`docker compose exec server python -c EXEC_GET <path> <auth>`): a request to the server's own
# 127.0.0.1:8080, with that container's own ASSISTANT_TOKEN when asked, so the token never passes a command line or this container.
EXEC_GET = r'''
import json, os, sys, urllib.error, urllib.request
path, auth = sys.argv[1], sys.argv[2] == "1"
h = {"Authorization": "Bearer " + os.environ.get("ASSISTANT_TOKEN", "")} if auth else {}
try:
    with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8080" + path, headers=h), timeout=5) as r:
        code, body = r.status, r.read(1000000)
except urllib.error.HTTPError as e:
    code, body = e.code, b""
except Exception:
    code, body = 0, b""
print(json.dumps({"code": code, "body": body.decode("utf-8", "replace")}))
'''


class ExecHttp:
    """updates.Updater's `http` inside the updater container. The copies publish their servers on the host's 127.0.0.1, which a
    container cannot reach, so each request is made from inside the copy's own server container instead (see EXEC_GET)."""

    def __init__(self, m: Instances):
        self.m = m

    def _inst(self, port: int) -> Instance | None:
        for name in [MAIN, *self.m.names()]:
            try:
                inst = self.m.load(name)
            except InstanceError:
                continue
            if inst.server_port == port:
                return inst
        return None

    def __call__(self, url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> tuple[int, Any]:
        u = urlsplit(url)
        inst = self._inst(u.port or 80)
        path = u.path + (f"?{u.query}" if u.query else "")
        if inst is None or not re.fullmatch(r"/[A-Za-z0-9/_.?=&-]*", path):
            return 0, None
        auth = "1" if headers and "Authorization" in headers else "0"
        try:
            r = self.m.run(self.m.compose(inst, "exec", "-T", "server", "python", "-c", EXEC_GET, path, auth), cwd=self.m.root,
                           capture_output=True, text=True, env=self.m.compose_env(inst), timeout=timeout + 20)
        except (subprocess.TimeoutExpired, OSError):
            return 0, None
        if r.returncode != 0:
            return 0, None
        try:
            got = json.loads((r.stdout or "").strip().splitlines()[-1])
            body = json.loads(got["body"]) if got.get("body") else None
        except (ValueError, IndexError, KeyError, TypeError):
            return 0, None
        return int(got.get("code") or 0), body


# ---------------------------------------------------------------------------------------------------------------- the images
class RegistrySource:
    """updates.Updater's image step for a release: pull Kelo's images (server, which also runs browser and egress, workroom,
    updater) by digest, check them, then tag them. The CLI sidecars are never pulled: they are built locally and left alone.

    The updater image goes to the `incoming` tag, never to the tag the running updater uses (`self_update` there: the swap tags it
    later). From the terminal (`assistant update` on release images) `finish()` moves the updater container too, since nothing
    there runs inside it."""

    builds = False      # pulls finished, signed images: the disk preflight needs no room for a build

    def __init__(self, release: rel.Release, verifier: rel.Verifier, *, terminal: bool = False):
        self.release, self.verifier, self.terminal = release, verifier, terminal
        self.updater_id: str | None = None

    def needed(self, u: Updater, insts: list[Instance]) -> list[str]:
        """Kelo's own images only. The Claude / agy sidecars are built on this machine (they hold proprietary CLIs that are never
        published) and stay as they are: compose does not recreate them, and each CLI updates itself from the app."""
        return ["server", "workroom", "updater"]

    def prepare(self, u: Updater, insts: list[Instance], clis: list[str] | None) -> None:
        if clis:
            raise UpdateError("a release updates every image together; --cli only works for images built here")
        local = (u.m.root / "compose.yml").read_text() if (u.m.root / "compose.yml").is_file() else ""
        if self.release.compose_level > rel.compose_level(local):
            raise UpdateError(f"release {self.release.version} needs newer setup files on the server machine: run the installer "
                              "there again (it keeps your data), then update")
        if any(read_env(i.env, "BROWSER") == "chrome" for i in insts):
            raise UpdateError("this computer uses Google Chrome, which is built here and never published: update it from the source "
                              "with `kelo update --from-source`")
        comps = self.needed(u, insts)
        missing = [c for c in comps if c not in self.release.images]
        if missing:
            raise UpdateError(f"release {self.release.version} has no image for {', '.join(missing)}")
        for comp in comps:                     # everything is fetched and checked before any tag moves
            image = self.release.images[comp]
            u.out(f"Downloading and checking {image.repo} {self.release.version}...")
            try:
                self.verifier.fetch_and_check(image, self.release.version, u.step)
            except rel.ReleaseError as e:
                raise UpdateError(str(e)) from None
        for comp in comps:
            image = self.release.images[comp]
            var = rel.COMPONENTS[comp]
            tag = incoming_updater_image() if comp == "updater" else u.m.shared_image(var)
            u._run(["docker", "tag", image.ref, tag], check=True)
            if comp == "updater":
                self.updater_id = u.image_id(image.ref)
        u.out(f"Release {self.release.version}: every image is the official one (digest and signature checked).")

    def finish(self, u: Updater) -> None:
        if not self.terminal or not self.updater_id:
            return
        u._run(["docker", "tag", self.updater_id, shared_updater_image()], check=True)
        main = u.m.load(MAIN)
        if "updater" in u.running(main):       # nothing runs inside it here, so it is simply recreated on the new image
            u._run(u.m.compose(main, "up", "-d", "--no-deps", "--force-recreate", "updater"), env=u.m.compose_env(main))
            u.out("The updater container runs the new release too.")


def image_labels(run: Callable[..., Any], ref: str) -> dict[str, str]:
    return rel.Verifier(run).labels(ref)


def current_release(m: Instances, u: Updater) -> dict[str, Any]:
    """What the main copy runs: {"mode": registry|local, "version", "image", "reason"}."""
    main = m.load(MAIN)
    iid = u.container_image(main, "server") or u.image_id(m.image_env(main)["KELO_IMAGE"])
    labels = image_labels(m.run, iid) if iid else {}
    if read_env(main.env, "BROWSER") == "chrome":
        return {"mode": "local", "version": labels.get(rel.VERSION_LABEL) or None, "image": iid, "reason": "chrome"}
    if labels.get(rel.RELEASE_LABEL):
        return {"mode": "registry", "version": labels.get(rel.VERSION_LABEL) or labels[rel.RELEASE_LABEL], "image": iid, "reason": None}
    return {"mode": "local", "version": labels.get(rel.VERSION_LABEL) or None, "image": iid, "reason": "local_build"}


def terminal_source(m: Instances, choice: str = "auto") -> RegistrySource | None:
    """`assistant update` from the terminal: None (build here from the source) unless the copy runs release images (or
    --from-release asks for them). Uses cosign from this computer, or the official cosign image."""
    if choice == "source":
        return None
    u = Updater(m)
    try:
        now = current_release(m, u)
    except InstanceError:
        return None
    if choice != "release" and now["mode"] != "registry":
        return None
    env = m.load(MAIN).env
    registry = read_env(env, "KELO_REGISTRY") or rel.DEFAULT_REGISTRY
    url = read_env(env, "KELO_RELEASES_URL") or rel.DEFAULT_RELEASES_URL
    try:
        release = rel.fetch(url, registry)
    except rel.ReleaseError as e:
        raise UpdateError(f"could not read the release list: {e}") from None
    if not rel.newer(release.version, now["version"]) and choice != "release":
        raise UpdateError(f"already up to date (version {now['version']}); nothing to do")
    key = read_env(env, "KELO_COSIGN_KEY") or None
    m.out(f"Updating to release {release.version} from {registry} (signed images; nothing is built here).")
    return RegistrySource(release, rel.Verifier(m.run, cosign=rel.cosign_command(), key=key), terminal=True)


# ---------------------------------------------------------------------------------------------------------------- the service
def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


class Service:
    def __init__(self, root: str | Path, *, token: str, registry: str = rel.DEFAULT_REGISTRY, releases_url: str = rel.DEFAULT_RELEASES_URL,
                 run: Callable[..., Any] = subprocess.run, verifier: rel.Verifier | None = None,
                 fetch: Callable[[str, str], rel.Release] | None = None, clock: Callable[[], float] = time.time,
                 health_timeout: float = 180.0, host: str | None = None, swap: Callable[[dict[str, Any]], None] | None = None,
                 updater_factory: Callable[..., Updater] | None = None, threaded: bool = True):
        if len(token) < 16:
            raise RuntimeError("UPDATER_TOKEN must be set (16+ characters); `assistant setup` creates it")
        self.root = Path(root).resolve()
        self.token = token
        self.registry = rel.check_registry(registry)
        self.releases_url = releases_url
        self.run, self.clock, self.health_timeout = run, clock, health_timeout
        self.verifier = verifier or rel.Verifier(run, key=os.environ.get("KELO_COSIGN_KEY") or None)
        self.fetch = fetch or (lambda url, registry: rel.fetch(url, registry))
        self.host = host or socket.gethostname()
        self.swap_fn = swap or self._launch_swap
        self.updater_factory = updater_factory or Updater
        self.threaded = threaded
        self.dir = self.root / "instances" / ".updater"
        self.lock = threading.Lock()          # state.json read-modify-write
        self.starting = threading.Lock()      # one start at a time
        self.thread: threading.Thread | None = None

    # ------------------------------------------------------------------ state
    def load(self) -> dict[str, Any]:
        try:
            data = json.loads((self.dir / STATE).read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: dict[str, Any]) -> None:
        _write_json(self.dir / STATE, data)

    def _patch_job(self, **changes: Any) -> dict[str, Any]:
        with self.lock:
            st = self.load()
            job = dict(st.get("job") or {})
            job.update(changes)
            st["job"] = job
            self.save(st)
            return job

    def manager(self, out: Callable[[str], None] = lambda s: None) -> Instances:
        m = Instances(self.root, run=self.run, out=out)
        m.skip_services = frozenset({"updater"})
        return m

    def updater(self, m: Instances, **kw: Any) -> Updater:
        return self.updater_factory(m, http=ExecHttp(m), clock=self.clock, health_timeout=self.health_timeout, **kw)

    # ------------------------------------------------------------------ reading
    def status(self) -> dict[str, Any]:
        st = self.load()
        m = self.manager()
        u = self.updater(m)
        try:
            now = current_release(m, u)
        except InstanceError:
            now = {"mode": "local", "version": None, "reason": "not_set_up"}
        latest = st.get("latest") if isinstance(st.get("latest"), dict) else None
        job = st.get("job") if isinstance(st.get("job"), dict) else None
        if job and job.get("state") == "running" and not self._job_alive(job):
            job = self._patch_job(state="failed", error="the updater stopped in the middle of this job; see `kelo backups`",
                                  finished_at=self.clock())
        point = u.point(m.load(MAIN)) if (self.root / ".env").is_file() else None
        return {
            "mode": now["mode"], "reason": now.get("reason"), "current": now.get("version"),
            "latest": latest, "update_available": bool(now["mode"] == "registry" and latest and rel.newer(latest.get("version"), now.get("version"))),
            "checked_at": st.get("checked_at"), "check_error": st.get("check_error"),
            "job": {k: v for k, v in job.items() if k != "log"} | {"log": (job.get("log") or [])[-30:]} if job else None,
            "rollback": {"available": bool(point and point.get("images")), "version": (point or {}).get("version"),
                         "created_at": (point or {}).get("created")},
            "registry": self.registry,
        }

    def _job_alive(self, job: dict[str, Any]) -> bool:
        """A job marked running is real while this process runs it, or while the swap container finishes it (its heartbeat)."""
        if self.thread is not None and self.thread.is_alive():
            return True
        beat = float(job.get("heartbeat") or job.get("started_at") or 0)
        return self.clock() - beat < 3 * HEARTBEAT + (SWAP_TIMEOUT if job.get("step") == "self_update" else 0)

    def check(self) -> dict[str, Any]:
        try:
            r = self.fetch(self.releases_url, self.registry)
        except rel.ReleaseError as e:
            with self.lock:
                st = self.load()
                st.update(checked_at=self.clock(), check_error=str(e))
                self.save(st)
            return self.status()
        with self.lock:
            st = self.load()
            st.update(checked_at=self.clock(), check_error=None,
                      latest={"version": r.version, "date": r.date, "channel": r.channel, "notes": r.notes, "min_app": r.min_app})
            self.save(st)
        return self.status()

    # ------------------------------------------------------------------ starting a job
    def _busy(self) -> bool:
        if self.thread is not None and self.thread.is_alive():
            return True
        job = self.load().get("job") or {}
        return job.get("state") == "running" and self._job_alive(job)

    def start(self, action: str) -> dict[str, Any]:
        if action not in ("update", "rollback"):
            raise Refused("unknown", "unknown command", 404)
        if not self.starting.acquire(blocking=False):
            raise Refused("busy", "an update or rollback is already starting")
        try:
            return self._start(action)
        finally:
            self.starting.release()

    def _start(self, action: str) -> dict[str, Any]:
        with self.lock:
            if self._busy():
                raise Refused("busy", "an update or rollback is already running")
            holder = UpdateLock(self.manager(), host=self.host).holder()
            if holder:
                raise Refused("busy", f"an update or rollback started on the server machine is running ({holder})")
        m = self.manager()
        u = self.updater(m)
        now = current_release(m, u)
        target: str | None = None
        release: rel.Release | None = None
        if action == "update":
            if now["mode"] != "registry":
                raise Refused("local", "this computer builds Kelo from the source files: update it there with `kelo update`")
            try:
                release = self.fetch(self.releases_url, self.registry)
            except rel.ReleaseError as e:
                raise Refused("no_release", f"could not read the release list: {e}") from None
            if not rel.newer(release.version, now.get("version")):
                raise Refused("up_to_date", f"already up to date (version {now.get('version')})")
            target = release.version
        else:
            point = u.point(m.load(MAIN))
            if not point or not point.get("images"):
                raise Refused("no_rollback", "there is no rollback point yet (an update makes one before it changes anything)")
            target = point.get("version")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        job = {"id": f"{stamp}-{secrets.token_hex(2)}", "action": action, "state": "running", "step": "queued", "progress": PROGRESS["queued"],
               "from": now.get("version"), "to": target, "error": None, "data_restored": False, "started_at": self.clock(),
               "heartbeat": self.clock(), "finished_at": None, "log": [], "self_update": None}
        with self.lock:
            st = self.load()
            if release is not None:
                st.update(checked_at=self.clock(), check_error=None,
                          latest={"version": release.version, "date": release.date, "channel": release.channel, "notes": release.notes,
                                  "min_app": release.min_app})
            st["job"] = job
            self.save(st)
        if self.threaded:
            self.thread = threading.Thread(target=self._job, args=(action, release), daemon=True, name=f"kelo-{action}")
            self.thread.start()
        else:
            self._job(action, release)
        return self.status()

    # ------------------------------------------------------------------ running a job
    def _log(self, line: str) -> None:
        with self.lock:
            st = self.load()
            job = st.get("job") or {}
            job["log"] = [*(job.get("log") or []), line[:500]][-MAX_LOG:]
            job["heartbeat"] = self.clock()
            st["job"] = job
            self.save(st)

    def _step(self, name: str) -> None:
        step = APP_STEP.get(name, name)
        with self.lock:
            st = self.load()
            job = st.get("job") or {}
            job.update(step=step, progress=max(int(job.get("progress") or 0), PROGRESS.get(step, 0)), heartbeat=self.clock())
            st["job"] = job
            self.save(st)

    def _job(self, action: str, release: rel.Release | None) -> None:
        m = self.manager(out=self._log)
        lock = UpdateLock(m, host=self.host)
        stop = threading.Event()
        try:
            lock.acquire()
        except InstanceError as e:
            self._patch_job(state="failed", error=str(e), finished_at=self.clock())
            return

        def beat() -> None:
            while not stop.wait(HEARTBEAT):
                lock.touch()
                self._patch_job(heartbeat=self.clock())
        threading.Thread(target=beat, daemon=True).start()
        swapping = False
        try:
            if action == "update":
                assert release is not None
                swapping = self._update(m, release)
            else:
                self._rollback(m)
        except Exception as e:  # noqa: BLE001 - the job always ends with a state the app can show
            self._log(f"error: {type(e).__name__}: {e}")
            self._patch_job(state="failed", error=f"{type(e).__name__}: {e}"[:300], finished_at=self.clock())
        finally:
            stop.set()
            lock.release()
        if swapping:
            self._start_swap()

    def _update(self, m: Instances, release: rel.Release) -> bool:
        """True when the updater itself moves to the new release next (the swap)."""
        source = RegistrySource(release, self.verifier)
        u = self.updater(m, source=source, step=self._step)
        names = [MAIN, *m.names()]            # the main copy first: the rest only when it came back healthy
        u.update(names)
        res = u.result
        outcome = res.get("outcome")
        if outcome != "done":
            if res.get("failed") and res.get("failed") != MAIN:
                outcome = "done"               # the main copy runs the new release; one extra copy went back
                self._patch_job(warning=f"the copy {res['failed']} could not start on {release.version} and went back: {res.get('problem')}")
            else:
                self._patch_job(state=outcome or "failed", error=res.get("problem"), data_restored=bool(res.get("data_restored")),
                                finished_at=self.clock(), progress=100)
                return False
        with self.lock:
            st = self.load()
            st["release"] = {"version": release.version, "images": {c: i.digest for c, i in release.images.items()}, "at": self.clock()}
            self.save(st)
        mine = self.own_image()
        if source.updater_id and mine and source.updater_id != mine:
            self._patch_job(step="self_update", progress=PROGRESS["self_update"],
                            self_update={"state": "pending", "from": mine, "to": source.updater_id})
            return True
        self._patch_job(state="done", step="done", progress=100, finished_at=self.clock())
        return False

    def _rollback(self, m: Instances) -> None:
        u = self.updater(m, step=self._step)
        main = m.load(MAIN)
        point = u.point(main) or {}
        names = [MAIN, *[n for n in m.names() if (u.point(m.load(n)) or {}).get("id") == point.get("id")]]
        restored = False
        for name in names:
            rc = u.rollback(name, yes=True, ask=None)
            restored = restored or bool(u.result.get("data_restored"))
            if rc != 0:
                self._patch_job(state="rollback_failed" if u.result.get("changed") else "failed", error=u.result.get("problem"),
                                data_restored=restored, finished_at=self.clock(), progress=100)
                return
        self._patch_job(state="done", step="done", progress=100, data_restored=restored, finished_at=self.clock())

    # ------------------------------------------------------------------ the main copy's server on updater-net
    def _networks(self, container: str) -> list[str]:
        r = self.run(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", container], capture_output=True, text=True)
        try:
            nets = json.loads((r.stdout or "").strip() or "{}") if r.returncode == 0 else {}
        except ValueError:
            nets = {}
        return list(nets) if isinstance(nets, dict) else []

    def attach_server(self) -> bool:
        """Connects the main copy's server container to this updater's internal network (updater-net), which compose.yml gives to
        the updater only, so no other copy has it. Checked every few seconds: a recreated server container is attached again.
        True when it attached one just now."""
        net = next((n for n in self._networks(self.host) if n.endswith("_updater-net")), None)
        if not net:
            return False
        m = self.manager()
        try:
            main = m.load(MAIN)
        except InstanceError:
            return False
        r = self.run(m.compose(main, "ps", "-q", "server"), cwd=m.root, capture_output=True, text=True, env=m.compose_env(main))
        cid = next(iter((r.stdout or "").split()), "") if r.returncode == 0 else ""
        if not cid or net in self._networks(cid):
            return False
        return self.run(["docker", "network", "connect", net, cid], capture_output=True, text=True).returncode == 0

    # ------------------------------------------------------------------ the updater updating itself
    def own_image(self) -> str | None:
        r = self.run(["docker", "inspect", "-f", "{{.Image}}", self.host], capture_output=True, text=True)
        return (r.stdout or "").strip() or None if r.returncode == 0 else None

    def _start_swap(self) -> None:
        job = self.load().get("job") or {}
        try:
            self.swap_fn(job)
        except Exception as e:  # noqa: BLE001 - the copies are updated; only the updater stays on its old image
            self._patch_job(state="done", step="done", progress=100, finished_at=self.clock(),
                            self_update={**(job.get("self_update") or {}), "state": "failed", "error": f"{type(e).__name__}: {e}"[:200]})

    def _launch_swap(self, job: dict[str, Any]) -> None:
        su = job.get("self_update") or {}
        new, old = su.get("to"), su.get("from")
        if not (isinstance(new, str) and isinstance(old, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", new) and re.fullmatch(r"sha256:[0-9a-f]{64}", old)):
            raise UpdateError("no updater image to move to")
        # Docker-only (the user and group ids below map the container to its host user); moves into DockerHost in phase 1
        sock = os.environ.get("KELO_DOCKER_SOCKET") or "/var/run/docker.sock"
        groups = sorted(set(os.getgroups()) - {os.getgid()})
        env = [a for v in (UPDATER_VAR, *IMAGE_VARS) if os.environ.get(v) for a in ("-e", f"{v}={os.environ[v]}")]
        cmd = ["docker", "run", "-d", "--rm", "--name", f"kelo-updater-swap-{job.get('id', 'x')}", "--network", "none", "--read-only",
               "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--label", "chat.kelo.updater-swap=1",
               "--user", f"{os.getuid()}:{os.getgid()}", *(a for g in groups for a in ("--group-add", str(g))),
               "-v", f"{sock}:/var/run/docker.sock", "-v", f"{os.environ.get('KELO_ROOT_HOST') or self.root}:{self.root}", "-w", str(self.root),
               *(["-e", f"KELO_ROOT_HOST={os.environ['KELO_ROOT_HOST']}"] if os.environ.get("KELO_ROOT_HOST") else []),
               "-e", "HOME=/tmp", "-e", "DOCKER_CONFIG=/tmp/.docker", *env,
               "--entrypoint", "python", new, "-m", "assistant.updater_service", "swap", "--root", str(self.root),
               "--new", new, "--old", old]
        r = self.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise UpdateError(f"could not start the one-shot updater: {((r.stderr or '').strip().splitlines() or ['?'])[-1][:200]}")
        self._log("The new updater is taking over (one-shot container).")


def swap(root: Path, new: str, old: str, *, run: Callable[..., Any] = subprocess.run, clock: Callable[[], float] = time.time,
         sleep: Callable[[float], None] = time.sleep, timeout: float = SWAP_TIMEOUT) -> int:
    """The one-shot copy of the NEW updater image: tag it, recreate the `updater` service on it, wait until it is healthy, and put the
    old image back when it is not. Writes how it went into the job of state.json."""
    svc = Service(root, token="x" * 16, run=run, clock=clock, threaded=False)
    m = svc.manager(out=svc._log)
    main = m.load(MAIN)
    lock = UpdateLock(m, host=socket.gethostname())
    for _ in range(30):
        try:
            lock.acquire()
            break
        except InstanceError:
            sleep(1)
    tag = shared_updater_image()

    def up(image: str) -> bool:
        run(["docker", "tag", image, tag], capture_output=True, text=True)
        r = run(m.compose(main, "up", "-d", "--no-deps", "--force-recreate", "updater"), cwd=m.root, capture_output=True, text=True,
                env=m.compose_env(main))
        if r.returncode != 0:
            return False
        deadline = clock() + timeout
        while clock() < deadline:
            ps = run(m.compose(main, "ps", "-q", "updater"), cwd=m.root, capture_output=True, text=True, env=m.compose_env(main))
            cid = (ps.stdout or "").split()[0] if ps.returncode == 0 and (ps.stdout or "").split() else ""
            if cid:
                h = run(["docker", "inspect", "-f", "{{.Image}} {{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", cid],
                        capture_output=True, text=True)
                got = (h.stdout or "").split()
                if len(got) == 2 and got[0] == image and got[1] in ("healthy", "running"):
                    return True
            svc._patch_job(heartbeat=clock())
            sleep(2)
        return False

    try:
        if up(new):
            svc._patch_job(state="done", step="done", progress=100, finished_at=clock(),
                           self_update={"state": "done", "from": old, "to": new})
            svc._log("The updater runs the new release.")
            return 0
        svc._log("The new updater did not become healthy; putting the previous one back.")
        back = up(old)
        svc._patch_job(state="done", step="done", progress=100, finished_at=clock(),
                       self_update={"state": "failed", "from": old, "to": new, "restored": back,
                                    "error": "the new updater did not become healthy"})
        return 1
    finally:
        lock.release()


# ---------------------------------------------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "kelo-updater"
    sys_version = ""
    service: Service

    def log_message(self, format: str, *args: Any) -> None:   # noqa: A002 - no request log (nothing secret, but nothing useful)
        return

    def _send(self, code: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        scheme, _, value = (self.headers.get("Authorization") or "").partition(" ")
        return scheme.lower() == "bearer" and secrets.compare_digest(value.encode(), self.service.token.encode())

    def _no_input(self) -> bool:
        """Every command is fixed: no query string, and no body other than nothing or {}."""
        if urlsplit(self.path).query:
            return False
        n = self.headers.get("Content-Length")
        if self.headers.get("Transfer-Encoding"):
            return False
        try:
            size = int(n or 0)
        except ValueError:
            return False
        if size > 64:
            return False
        raw = self.rfile.read(size) if size else b""
        if not raw.strip():
            return True
        try:
            return json.loads(raw) == {}
        except ValueError:
            return False

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self._send(200, {"ok": True})
        if path != "/status":
            return self._send(404, {"detail": "not found"})
        if not self._authorized():
            return self._send(401, {"detail": "invalid or missing bearer token"})
        if urlsplit(self.path).query:
            return self._send(422, {"detail": "this updater takes no parameters"})
        self._send(200, self.service.status())

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path not in ("/check", "/update", "/rollback"):
            return self._send(404, {"detail": "not found"})
        if not self._authorized():
            return self._send(401, {"detail": "invalid or missing bearer token"})
        if not self._no_input():
            return self._send(422, {"detail": "this updater takes no parameters"})
        try:
            if path == "/check":
                return self._send(200, self.service.check())
            return self._send(202, self.service.start(path[1:]))
        except Refused as e:
            return self._send(e.status, {"detail": e.detail, "code": e.code})
        except (InstanceError, UpdateError, rel.ReleaseError) as e:
            return self._send(409, {"detail": str(e), "code": "error"})

    def _refuse(self) -> None:
        self._send(405, {"detail": "method not allowed"})

    do_PUT = do_DELETE = do_PATCH = _refuse   # noqa: N815


def make_server(service: Service, host: str = "0.0.0.0", port: int = PORT) -> ThreadingHTTPServer:   # noqa: S104 - internal network only
    handler = type("BoundHandler", (Handler,), {"service": service})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


def attach_loop(service: Service, every: float = 5.0) -> None:
    while True:
        try:
            service.attach_server()
        except Exception:  # noqa: BLE001 - the next look tries again
            pass
        time.sleep(every)


KELO_REFRESH_SECONDS = 6 * 3600


def kelo_loop(service: Service, every: float = KELO_REFRESH_SECONDS, *, refresh: Callable[..., Any] | None = None,
              sleep: Callable[[float], None] = time.sleep, rounds: int | None = None) -> None:
    """Every few hours: each copy on a Kelo address asks for its own name again (kelo_address.kelo_refresh), so a tunnel the broker parked
    while the copy was offline, or one that is gone, is made again and the tunnel container gets the new token by itself."""
    from . import kelo_address     # not connect: that needs httpx and segno, which the updater image does not have
    refresh = refresh or kelo_address.kelo_refresh
    n = 0
    while rounds is None or n < rounds:
        n += 1
        try:
            m = service.manager()
            for name in [MAIN, *m.names()]:
                try:
                    note = refresh(m, m.load(name))
                    if note:
                        print(f"kelo address of {name}: {note}", flush=True)
                except Exception:  # noqa: BLE001 - the next copy, and the next round, go on
                    pass
        except Exception:  # noqa: BLE001
            pass
        sleep(every)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="kelo-updater")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=PORT)
    w = sub.add_parser("swap")
    w.add_argument("--root", required=True)
    w.add_argument("--new", required=True)
    w.add_argument("--old", required=True)
    h = sub.add_parser("healthcheck")
    h.add_argument("--port", type=int, default=PORT)
    args = p.parse_args(argv)
    root = Path(os.environ.get("KELO_ROOT") or "/kelo-not-set-up")
    if args.cmd == "healthcheck":
        import urllib.request
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{args.port}/healthz", timeout=3)   # noqa: S310
            return 0
        except OSError:
            return 1
    if args.cmd == "swap":
        return swap(Path(args.root), args.new, args.old)
    if not os.environ.get("KELO_ROOT") or not (root / "compose.yml").is_file():
        print("error: KELO_ROOT is not set or has no compose.yml: run `assistant updater setup` on the server machine "
              "(it mounts the Kelo folder at the same path as on the host)", file=sys.stderr)
        return 1
    service = Service(root, token=os.environ.get("UPDATER_TOKEN", ""), registry=os.environ.get("KELO_REGISTRY") or rel.DEFAULT_REGISTRY,
                      releases_url=os.environ.get("KELO_RELEASES_URL") or rel.DEFAULT_RELEASES_URL)
    srv = make_server(service, port=args.port)
    threading.Thread(target=attach_loop, args=(service,), daemon=True, name="attach").start()
    threading.Thread(target=kelo_loop, args=(service,), daemon=True, name="kelo").start()
    print(f"kelo updater listening on :{args.port} (registry {service.registry})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
