"""`assistant update | rollback | backups`: update a copy with a backup and an automatic rollback.

An update of one copy (the main one, or `assistant update <name>`; `assistant instance update` does every extra copy) runs:

 1. Preflight: the running server's version and database schema (GET /api/about), and free disk in the copy's data volume
    (room for one more backup plus the new images). Too little room stops here, before anything changed.
 2. Rollback point: the image each of its containers runs (server, browser, egress, work room, Claude/agy sidecars) is tagged
    `<repo>:rollback-<copy>-<time>` so it cannot be pruned, and written to `instances/<copy>/rollback.json` with its id.
 3. Backup, inside the data volume (`/state/.backups/<time>/`, made by the server's own user with umask 077, so it stays exactly as
    private as the live data): a consistent copy of assistant.db made with SQLite's backup API (checked with integrity_check), and
    every other file of the state (home folder, notes, uploads, installed packages, master.key when there is one) as a tar.gz.
    The newest 3 backups are kept, plus the one the rollback point needs. Nothing secret is printed.
 4. The shared images are built (once for all copies being updated), the copy is restarted on them, and it must pass a health check
    before a deadline: the containers run, /healthz answers, /api/about says the database opened with its migrations done, and a
    request that needs no model (GET /api/profile) works.
 5. Any failure after the rollback point rolls back by itself: the copy is held at its previous images (KELO_*_IMAGE in its env
    file), the backup is put back when the database schema changed (the new server migrated it), the copy is restarted and checked
    again, and the message says what failed and what was rolled back. A failed build changes nothing in the copy, and the shared tags
    go back to what they were, so another copy never picks up a broken build when it restarts.

`assistant rollback [copy]` goes back to the last rollback point on purpose; it is the only way to downgrade. `assistant backups
[copy]` lists the backups. The next `assistant update` of a held copy clears the hold and brings it to the newest images.

THE MIGRATION RULE. The schema is upgraded in place at start-up (store.py and the modules that own a table: CREATE TABLE IF NOT
EXISTS, then ALTER TABLE ADD COLUMN for a missing column). There is no down-migration, and an older server is not guaranteed to work on
a database a newer one changed. So the database and the images move together: whenever the schema fingerprint (store.py
schema_fingerprint, the same formula as HELPER below) differs from the one saved with the rollback point, going back to the older
images also puts back the backup taken with that point (data written after it is lost, and the user is told). When the fingerprint is
the same, the current data is kept. `assistant rollback --keep-data` skips the restore at the operator's own risk.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cli import CLIS, ensure_keyring_token, read_env, remove_env, write_env
from .instances import IMAGE_VARS, MAIN, Instance, InstanceError, Instances

KEEP_BACKUPS = 3
HEALTH_TIMEOUT = 180.0
GiB = 1024 ** 3
MARGIN_BUILD = 3 * GiB      # free room the new images need on top of the backup (Docker keeps them on the same disk)
MARGIN_NO_BUILD = GiB // 2
CLI_VAR = {"claude": "KELO_CLI_CLAUDE_IMAGE", "agy": "KELO_CLI_AGY_IMAGE"}

# Runs inside the copy's server container (its own user, its /state volume) with `python -c HELPER <command> ...`. It only uses the
# standard library, so it works in an image older than this file. It prints one JSON line and never prints a value from the state.
HELPER = r'''
import hashlib, json, os, shutil, sqlite3, sys, tarfile, time
S = os.environ.get("KELO_STATE", "/state"); B = os.path.join(S, ".backups"); DB = os.path.join(S, "assistant.db"); KEEP = int(os.environ.get("KELO_KEEP_BACKUPS", "3"))
SKIP = {".backups", "assistant.db", "assistant.db-journal", "assistant.db-wal", "assistant.db-shm"}
os.umask(0o077)

def fp(con):
    rows = con.execute("SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
                       "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    return hashlib.sha256("\n".join("\t".join(str(c) for c in r) for r in rows).encode()).hexdigest()[:16]

def schema(path):
    if not os.path.exists(path):
        return None
    con = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    try:
        return fp(con)
    finally:
        con.close()

def size(root, skip=()):
    total = 0
    for d, dirs, files in os.walk(root):
        if d == root:
            dirs[:] = [x for x in dirs if x not in skip]
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return total

def backups():
    out = []
    if os.path.isdir(B):
        for n in sorted(os.listdir(B)):
            m = os.path.join(B, n, "meta.json")
            if not n.startswith(".") and os.path.isfile(m):
                with open(m) as f:
                    out.append(json.load(f))
    return out

def backup(bid, label, meta, protect):
    os.makedirs(B, 0o700, exist_ok=True)
    os.chmod(B, 0o700)
    tmp = os.path.join(B, "." + bid + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    os.mkdir(tmp, 0o700)
    try:
        out = dict(meta, id=bid, label=label, created=time.time(), schema=None)
        if os.path.exists(DB):
            src = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
            dst = sqlite3.connect(os.path.join(tmp, "assistant.db"))
            try:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SystemExit("the copy of the database failed its integrity check")
                out["schema"] = fp(dst)
            finally:
                src.close(); dst.close()
        with tarfile.open(os.path.join(tmp, "state.tar.gz"), "w:gz", compresslevel=1) as t:
            for n in sorted(os.listdir(S)):
                if n not in SKIP:
                    t.add(os.path.join(S, n), arcname=n)
        out["bytes"] = size(tmp)
        with open(os.path.join(tmp, "meta.json"), "w") as f:
            json.dump(out, f)
        os.rename(tmp, os.path.join(B, bid))
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    keep = set([b["id"] for b in backups()][-KEEP:]) | set(protect) | {bid}
    for n in os.listdir(B):
        if (n.startswith(".") and n.endswith(".part")) or (not n.startswith(".") and n not in keep):
            shutil.rmtree(os.path.join(B, n), ignore_errors=True)
    return out

def restore(bid):
    d = os.path.join(B, bid)
    with open(os.path.join(d, "meta.json")) as f:
        meta = json.load(f)
    work, old = os.path.join(B, ".restore-" + bid), os.path.join(B, ".replaced-" + bid)
    for p in (work, old):
        shutil.rmtree(p, ignore_errors=True)
        os.mkdir(p, 0o700)
    with tarfile.open(os.path.join(d, "state.tar.gz")) as t:
        try:
            t.extractall(work, filter="tar")
        except TypeError:
            t.extractall(work)
    if meta.get("schema") is not None:
        shutil.copy2(os.path.join(d, "assistant.db"), os.path.join(work, "assistant.db"))
    for n in os.listdir(S):
        if n != ".backups":
            os.rename(os.path.join(S, n), os.path.join(old, n))
    for n in os.listdir(work):
        os.rename(os.path.join(work, n), os.path.join(S, n))
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    return {"id": bid, "schema": schema(DB)}

cmd, args = sys.argv[1], sys.argv[2:]
if cmd == "info":
    st = os.statvfs(S)
    res = {"schema": schema(DB), "free": st.f_bavail * st.f_frsize, "state": size(S, {".backups"}), "backups": size(B)}
elif cmd == "backup":
    res = backup(args[0], args[1], json.loads(args[2]), args[3:])
elif cmd == "list":
    res = backups()
elif cmd == "restore":
    res = restore(args[0])
else:
    raise SystemExit("unknown command")
print(json.dumps(res))
'''



# Opt-in services built from this checkout (profile -> service). An update builds each one a copy has in COMPOSE_PROFILES: on
# 2026-11-14 the android bridge was not built, the restart found no image and the update rolled back (and the server was down).
LOCAL_PROFILE_BUILDS = {"android": "android"}

class UpdateError(Exception):
    """A step failed; the message says which one and why (never a secret)."""


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> tuple[int, Any]:
    import httpx
    try:
        r = httpx.get(url, headers=headers or {}, timeout=timeout)
    except httpx.HTTPError:
        return 0, None
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def _size(n: int | float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _when(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"


def _repo(tag: str) -> str:
    """'assistant:local' -> 'assistant'; 'registry:5000/x:tag' -> 'registry:5000/x'."""
    head, _, last = tag.rpartition(":")
    return head if head and "/" not in last else tag


class Updater:
    """`source`: where the new images come from. None = built here from the source files (step 4 above); otherwise an object with
    `prepare(updater, copies, clis)` that puts the new images on the shared tags or raises UpdateError before changing anything
    (updater_service.RegistrySource: signed release images, pulled and checked). `step(name)` is told each stage as it starts
    (preflight, backup, build, restart, health, rollback, done), and `result` says how the last update or rollback ended."""

    def __init__(self, m: Instances, *, http: Callable[..., tuple[int, Any]] = _http_get, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
                 health_timeout: float = HEALTH_TIMEOUT, source: Any = None, step: Callable[[str], None] | None = None):
        self.m, self.http, self.sleep, self.clock, self.monotonic = m, http, sleep, clock, monotonic
        self.health_timeout = health_timeout
        self.out = m.out
        self.source = source
        self.step = step or (lambda name: None)
        self.result: dict[str, Any] = {}

    # ------------------------------------------------------------------ docker plumbing
    def _run(self, cmd: list[str], *, env: dict[str, str] | None = None, check: bool = False) -> Any:
        r = self.m.run(cmd, cwd=self.m.root, capture_output=True, text=True, env=env or dict(os.environ))
        if check and r.returncode != 0:
            raise UpdateError(f"`{' '.join(cmd[:4])} ...` failed (exit {r.returncode}): {self._tail(r)}")
        return r

    @staticmethod
    def _tail(r: Any) -> str:
        lines = [ln for ln in ((getattr(r, "stderr", "") or "") + "\n" + (getattr(r, "stdout", "") or "")).splitlines() if ln.strip()]
        return (lines[-1].strip() if lines else "no output")[:300]

    def _who(self, inst: Instance) -> str:
        return "the main copy" if inst.is_main else inst.name

    def _flag(self, inst: Instance) -> str:
        return "" if inst.is_main else f" {inst.name}"

    def dir(self, inst: Instance) -> Path:
        d = self.m.dir / inst.name            # "main" can never be an extra copy's name, so the folder is free
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        return d

    def point(self, inst: Instance) -> dict[str, Any] | None:
        p = self.m.dir / inst.name / "rollback.json"
        try:
            return json.loads(p.read_text()) if p.is_file() else None
        except ValueError:
            return None

    def _save_point(self, inst: Instance, point: dict[str, Any]) -> None:
        p = self.dir(inst) / "rollback.json"
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(point, f, indent=1)
        os.replace(tmp, p)

    def used_vars(self, inst: Instance) -> list[str]:
        clis = [c for c in read_env(inst.env, "COMPOSE_PROFILES").split(",") if c in CLIS]
        return ["KELO_IMAGE", "KELO_WORKROOM_IMAGE", *(CLI_VAR[c] for c in CLIS if c in clis)]

    def services(self, inst: Instance) -> list[str]:
        """The containers of ours this copy runs, which must all be up after a restart."""
        out = ["server", "workroom", "vpn", *(["browser", "egress"] if inst.browser else [])]
        if inst.browser:                       # Instances.start leaves the sidecars out for a copy without a browser
            out += [svc for var in self.used_vars(inst) if var.startswith("KELO_CLI_") for svc in IMAGE_VARS[var][1]]
        return out

    def image_id(self, ref: str) -> str | None:
        r = self._run(["docker", "image", "inspect", "-f", "{{.Id}}", ref])
        return (r.stdout or "").strip() or None if r.returncode == 0 else None

    def running(self, inst: Instance) -> list[str]:
        r = self._run(self.m.compose(inst, "ps", "--status", "running", "--services"), env=self.m.compose_env(inst))
        return [s for s in (r.stdout or "").split() if s] if r.returncode == 0 else []

    def container_image(self, inst: Instance, service: str) -> str | None:
        r = self._run(self.m.compose(inst, "ps", "-a", "-q", service), env=self.m.compose_env(inst))
        cid = next(iter((r.stdout or "").split()), "") if r.returncode == 0 else ""
        if not cid:
            return None
        r = self._run(["docker", "inspect", "-f", "{{.Image}}", cid])
        return (r.stdout or "").strip() or None if r.returncode == 0 else None

    def helper(self, inst: Instance, *args: str, fresh: bool = False) -> Any:
        """HELPER inside the copy's server container: `exec` when it runs, else a one-off container of the server service (its image,
        user and volumes; no ports). `fresh` always uses a one-off container (the server is stopped for a restore)."""
        env = self.m.compose_env(inst)
        live = not fresh and "server" in self.running(inst)
        cmd = (self.m.compose(inst, "exec", "-T", "server", "python", "-c", HELPER, *args) if live else
               self.m.compose(inst, "run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "server", "-c", HELPER, *args))
        r = self._run(cmd, env=env)
        if r.returncode != 0:
            raise UpdateError(f"the {args[0]} step inside the server container failed: {self._tail(r)}")
        try:
            return json.loads((r.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise UpdateError(f"the {args[0]} step inside the server container gave no answer") from None

    # ------------------------------------------------------------------ 1. preflight
    def preflight(self, inst: Instance, *, build: bool) -> dict[str, Any]:
        live = "server" in self.running(inst)
        info: dict[str, Any] = {"running": live, "version": None, "schema": None}
        if live:
            token = read_env(inst.env, "ASSISTANT_TOKEN")
            code, about = self.http(f"{inst.local_url}/api/about", {"Authorization": f"Bearer {token}"})
            if code == 200 and isinstance(about, dict):
                info["version"] = about.get("version")
                db = about.get("db") if isinstance(about.get("db"), dict) else {}
                info["schema"] = db.get("schema")
            else:
                self.out(f"{self._who(inst)}: could not read its version (HTTP {code or 'no answer'}); going on.")
        disk = self.helper(inst, "info")
        info["schema"] = disk.get("schema") or info["schema"]
        need = int(disk.get("state") or 0) + (MARGIN_BUILD if build else MARGIN_NO_BUILD)
        if int(disk.get("free") or 0) < need:
            raise UpdateError(f"not enough free disk for a safe update of {self._who(inst)}: it needs {_size(need)} "
                              f"(a backup of {_size(disk.get('state'))} plus room for the new images), only {_size(disk.get('free'))} "
                              "is free. Free some space (for example `docker image prune`) and try again")
        info.update(free=disk.get("free"), state=disk.get("state"))
        return info

    # ------------------------------------------------------------------ 2. rollback point
    def record(self, inst: Instance, stamp: str) -> dict[str, dict[str, str]]:
        env = self.m.image_env(inst)
        images: dict[str, dict[str, str]] = {}
        for var in self.used_vars(inst):
            iid = next((i for i in (self.container_image(inst, s) for s in IMAGE_VARS[var][1]) if i), None) or self.image_id(env[var])
            if not iid:
                continue                       # never built (a sidecar switched on but not built yet): nothing to go back to
            tag = f"{_repo(self.m.shared_image(var))}:rollback-{inst.name}-{stamp}"
            self._run(["docker", "tag", iid, tag], check=True)
            images[var] = {"tag": tag, "id": iid}
        if "KELO_IMAGE" not in images:
            raise UpdateError(f"{self._who(inst)} has no server image yet, so there is nothing to roll back to; start it once first")
        return images

    # ------------------------------------------------------------------ 3. backup
    def backup(self, inst: Instance, stamp: str, label: str, meta: dict[str, Any], protect: list[str] | None = None) -> dict[str, Any]:
        b = self.helper(inst, "backup", stamp, label, json.dumps(meta), *(protect or []))
        self.out(f"{self._who(inst)}: backed up the data ({_size(b.get('bytes'))}, kept in its own volume as backup {b.get('id')}).")
        return b

    # ------------------------------------------------------------------ 4. build and health
    def build(self, insts: list[Instance], clis: list[str] | None) -> None:
        env = {**os.environ, **{v: self.m.shared_image(v) for v in IMAGE_VARS}}   # builds always go to the shared tags, never to a hold
        f = ["docker", "compose", "-f", str(self.m.root / "compose.yml")]
        steps: list[list[str]] = [] if clis else [[*f, "build", "server"], [*f, "build", "workroom"]]
        wanted = clis or sorted({c for i in insts for c in CLIS if CLI_VAR[c] in self.used_vars(i)}, key=CLIS.index)
        if not clis:                           # an opt-in service built here (not pulled) whose profile a copy has on: built with
            for prof, service in sorted(LOCAL_PROFILE_BUILDS.items()):   # the rest, else the restart finds no image and rolls back
                if any(prof in [p.strip() for p in read_env(i.env, "COMPOSE_PROFILES").split(",")] for i in insts):
                    steps.append([*f, "--profile", prof, "build", service])
        if wanted:                             # from scratch, so they get the newest claude / agy
            steps.append([*f, *(a for c in wanted for a in ("--profile", c)), "build", "--pull", "--no-cache", *(f"{c}-cli" for c in wanted)])
        for cmd in steps:
            self.out("$ " + " ".join(cmd[3:] if cmd[2] == "-f" else cmd))
            r = self.m.run(cmd, cwd=self.m.root, env=env)      # the build log streams to the terminal
            if r.returncode != 0:
                raise UpdateError(f"building the images failed (exit {r.returncode}); see the build output above")

    def healthy(self, inst: Instance, *, need_db: bool = True) -> str | None:
        """None when the copy works, else what is wrong at the deadline."""
        deadline = self.monotonic() + self.health_timeout
        auth = {"Authorization": f"Bearer {read_env(inst.env, 'ASSISTANT_TOKEN')}"}
        problem = "it did not start"
        while True:
            up = self.running(inst)
            missing = [s for s in self.services(inst) if s not in up]
            if "server" in missing:
                problem = "the server container is not running (see: docker compose logs server)"
            else:
                code, _ = self.http(f"{inst.local_url}/healthz")
                if code != 200:
                    problem = f"the server does not answer /healthz (HTTP {code or 'no answer'})"
                else:
                    code, about = self.http(f"{inst.local_url}/api/about", auth)
                    db = about.get("db") if isinstance(about, dict) else None
                    if code != 200:
                        problem = f"/api/about failed (HTTP {code or 'no answer'})"
                    elif need_db and not (isinstance(db, dict) and db.get("ok") and db.get("migrations") == "done"):
                        problem = "the database did not open or its migrations did not finish"
                    elif (code := self.http(f"{inst.local_url}/api/profile", auth)[0]) != 200:
                        problem = f"a request without a model (GET /api/profile) failed (HTTP {code or 'no answer'})"
                    elif missing:
                        problem = f"not running: {', '.join(missing)}"
                    else:
                        return None
            if self.monotonic() >= deadline:
                return f"{problem}, after {int(self.health_timeout)} seconds"
            self.sleep(2)

    # ------------------------------------------------------------------ going back
    def _hold(self, inst: Instance, point: dict[str, Any]) -> None:
        """Pin the copy to the point's images (re-tagging them from their id when the tag went away)."""
        pins = {}
        for var, img in point["images"].items():
            if self.image_id(img["tag"]) != img["id"]:
                if not self.image_id(img["id"]):
                    raise UpdateError(f"the previous image {img['id'][:19]} is no longer on this computer")
                self._run(["docker", "tag", img["id"], img["tag"]], check=True)
            pins[var] = img["tag"]
        write_env(inst.env, pins)

    def _go_back(self, inst: Instance, point: dict[str, Any], *, restore_data: bool) -> bool:
        """Hold the copy at the point's images; put the backup back when the schema moved; restart. True when data was restored."""
        self._hold(inst, point)
        restored = False
        if restore_data:
            self._run(self.m.compose(inst, "stop", "server"), env=self.m.compose_env(inst))
            now = self.helper(inst, "info", fresh=True).get("schema")
            if now != point.get("schema"):
                if not point.get("backup"):
                    raise UpdateError("the database changed and there is no backup to put back")
                self.helper(inst, "restore", point["backup"], fresh=True)
                restored = True
        self.m.start(inst.name, build_if_missing=False)
        return restored

    def _restore_shared(self, before: dict[str, str | None]) -> None:
        for var, iid in before.items():
            tag = self.m.shared_image(var)
            if iid and self.image_id(tag) != iid:
                self._run(["docker", "tag", iid, tag])

    def prune_tags(self, inst: Instance) -> None:
        """Remove this copy's rollback tags that neither its rollback point nor its hold uses any more."""
        point = self.point(inst) or {}
        keep = {i["tag"] for i in point.get("images", {}).values()} | {read_env(inst.env, v) for v in IMAGE_VARS} - {""}
        for repo in sorted({_repo(self.m.shared_image(v)) for v in IMAGE_VARS}):
            r = self._run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", "--filter",
                           f"reference={repo}:rollback-{inst.name}-*"])
            for tag in (r.stdout or "").split() if r.returncode == 0 else []:
                if tag not in keep:
                    self._run(["docker", "image", "rm", tag])

    def prune_untagged(self) -> None:
        """After a good update: delete Kelo's own image layers nothing points at any more (the image a rebuild or a new release
        replaced, minus the rollback point, which keeps its tag). Only images labelled chat.kelo.image, never the owner's other
        images. Without this every update left several GB behind until the disk filled (owner, 2026-11-15)."""
        self._run(["docker", "image", "prune", "-f", "--filter", "dangling=true", "--filter", "label=chat.kelo.image"])

    # ------------------------------------------------------------------ commands
    def update(self, names: list[str], *, build: bool = True, clis: list[str] | None = None) -> int:
        insts = [self.m.load(n) for n in names]
        if not insts:
            self.out("No copies to update.")
            return 0
        if clis:
            for inst in insts:
                missing = [c for c in clis if CLI_VAR[c] not in self.used_vars(inst)]
                if missing:
                    raise InstanceError(f"not enabled for {self._who(inst)}: {', '.join(missing)}. "
                                        f"Run `assistant setup --cli {missing[0]}` (or `assistant enable oauth`) first.")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        self.result = {"outcome": "failed", "changed": False, "data_restored": False, "problem": None, "stamp": stamp, "updated": []}
        self.step("preflight")
        try:                                   # 1. nothing changed yet when this fails
            pre = {i.name: self.preflight(i, build=build or self.source is not None) for i in insts}
        except UpdateError as e:
            self.out(f"Update stopped before changing anything: {e}.")
            self.result["problem"] = str(e)
            return 1
        for inst in insts:   # a copy made before the key container existed gets its shared secret before it restarts
            ensure_keyring_token(inst.env)
        shared_before = {v: self.image_id(self.m.shared_image(v)) for v in IMAGE_VARS}
        points: dict[str, dict[str, Any]] = {}
        self.step("backup")
        try:                                   # 2 + 3: tags and backups only; the copies keep running untouched
            for inst in insts:
                images = self.record(inst, stamp)
                p = pre[inst.name]
                meta = {"instance": inst.name, "version": p["version"], "images": {v: i["id"] for v, i in images.items()}}
                b = self.backup(inst, stamp, "before update", meta)
                points[inst.name] = {"id": stamp, "instance": inst.name, "created": self.clock(), "version": p["version"],
                                     "schema": b.get("schema"), "backup": b.get("id"), "images": images}
                self._save_point(inst, points[inst.name])
        except UpdateError as e:
            self.out(f"Update stopped before changing anything: {e}.")
            self.result["problem"] = str(e)
            for inst in insts:
                self.prune_tags(inst)
            return 1
        if build or self.source is not None:
            self.step("build")
            try:
                if self.source is not None:
                    self.source.prepare(self, insts, clis)
                else:
                    self.build(insts, clis)
            except Exception as e:  # noqa: BLE001 - whatever stopped it, the shared tags go back and nothing else changed
                self._restore_shared(shared_before)
                why = str(e) if isinstance(e, UpdateError) else f"{type(e).__name__}: {e}"[:300]
                self.out(f"Update failed: {why}. Nothing was changed: "
                         + ", ".join(self._who(i) for i in insts) + " keep running the version they had.")
                self.result["problem"] = why
                return 1
        done: list[str] = []
        for inst in insts:
            point = points[inst.name]
            self.step("restart")
            self.out(f"{self._who(inst)}: restarting on the new images...")
            self.result["changed"] = True
            remove_env(inst.env, list(IMAGE_VARS))             # an update ends any hold from an earlier rollback
            try:
                self.m.start(inst.name, build_if_missing=False)
                self.step("health")
                problem = self.healthy(inst)
            except Exception as e:  # noqa: BLE001 - any failure here must still roll back
                code = getattr(e, "returncode", None)
                problem = f"restarting the containers failed ({f'exit {code}' if code is not None else type(e).__name__})"
            if problem is None:
                done.append(inst.name)
                self.result["updated"] = list(done)
                self.prune_tags(inst)
                self.out(f"{self._who(inst)} is updated and healthy. To go back to the previous version: assistant rollback{self._flag(inst)}")
                continue
            return self._failed(inst, point, problem, shared_before if not done else None, rest=[i for i in insts if i.name not in done and i is not inst])
        self.prune_untagged()
        self.result.update(outcome="done")
        finish = getattr(self.source, "finish", None)
        if finish is not None:                 # e.g. the updater container itself moves to the new release, last
            finish(self)
        self.step("done")
        return 0

    def _failed(self, inst: Instance, point: dict[str, Any], problem: str, shared_before: dict[str, str | None] | None,
                rest: list[Instance]) -> int:
        who = self._who(inst)
        self.out(f"Update of {who} failed: {problem}.")
        self.out(f"Rolling {who} back to the images it ran before...")
        self.step("rollback")
        try:
            restored = self._go_back(inst, point, restore_data=True)
            if shared_before is not None:      # nobody runs the new build: the shared tags go back too
                self._restore_shared(shared_before)
            again = self.healthy(inst, need_db=False)
        except (UpdateError, InstanceError, OSError) as e:
            restored, again = False, f"rolling back stopped: {e}"
        except Exception as e:  # noqa: BLE001
            restored, again = False, f"rolling back stopped ({type(e).__name__})"
        self.prune_tags(inst)
        self.result.update(outcome="rolled_back" if again is None else "rollback_failed", data_restored=restored, problem=problem,
                           failed=inst.name, not_updated=[i.name for i in rest])
        data = (f" and its data was restored from the backup taken at {_when(point.get('created'))} (changes made after that are not kept)"
                if restored else " (its data was kept: the database was not changed)")
        if again is None:
            self.out(f"ROLLED BACK: the update of {who} failed ({problem}). {'The main copy' if inst.is_main else who} runs the previous images again{data}, "
                     f"and it is healthy. It stays on that version until the next `assistant update{self._flag(inst)}`.")
        else:
            self.out(f"ROLLBACK INCOMPLETE: the update of {who} failed ({problem}), and after rolling back it is still not healthy: {again}. "
                     f"Its backups are kept (`assistant backups{self._flag(inst)}`); see `docker compose logs server`.")
        if rest:
            self.out("Not updated (they keep running what they had): " + ", ".join(self._who(i) for i in rest) + ".")
        return 1

    def rollback(self, name: str | None, *, yes: bool, ask: Callable[[str], str] | None, keep_data: bool = False) -> int:
        inst = self.m.load(name)
        who, point = self._who(inst), self.point(inst)
        if not point:
            raise InstanceError(f"{who} has no rollback point yet; `assistant update{self._flag(inst)}` makes one before it changes anything")
        img = point["images"].get("KELO_IMAGE", {})
        self.out(f"Rollback point of {who}: made {_when(point.get('created'))} before an update; version {point.get('version') or '?'}, "
                 f"image {img.get('id', '?')[7:19]}; backup {point.get('backup') or '(none)'}.")
        self.out("Its containers go back to those images. If the database layout changed since, the data is put back from that backup "
                 "and anything changed after it is lost" + (" (--keep-data: the current data is kept as it is)." if keep_data else
                                                            " (the current data is backed up first)."))
        if not yes:
            if ask is None:
                raise InstanceError("rolling back asks for confirmation, so it needs a terminal. Add --yes to roll back without asking.")
            if ask("Roll back now? [y/N]: ").strip().lower() not in ("y", "yes"):
                self.out("Nothing was changed.")
                return 1
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        self.result = {"outcome": "failed", "changed": False, "data_restored": False, "problem": None, "stamp": stamp}
        self.step("backup")
        try:
            self.backup(inst, stamp, "before rollback", {"instance": inst.name, "version": None, "images": {}},
                        protect=[point["backup"]] if point.get("backup") else [])
            self.step("rollback")
            self.result["changed"] = True
            restored = self._go_back(inst, point, restore_data=not keep_data)
            self.result["data_restored"] = restored
            self.step("health")
            problem = self.healthy(inst, need_db=False)
        except UpdateError as e:
            self.out(f"Rollback of {who} failed: {e}.")
            self.result.update(outcome="rollback_failed" if self.result["changed"] else "failed", problem=str(e))
            return 1
        self.prune_tags(inst)
        if problem:
            self.out(f"Rolled {who} back, but it is not healthy: {problem}. Its backups are kept (`assistant backups{self._flag(inst)}`).")
            self.result.update(outcome="rollback_failed", problem=problem)
            return 1
        self.result.update(outcome="done", version=point.get("version"))
        self.step("done")
        self.out(f"Rolled back: {who} runs the images from {_when(point.get('created'))}"
                 + (", with the data from that backup" if restored else ", with its current data") +
                 f". It stays there until the next `assistant update{self._flag(inst)}`.")
        return 0

    def list_backups(self, name: str | None) -> int:
        inst = self.m.load(name)
        rows = self.helper(inst, "list")
        point = self.point(inst) or {}
        who = self._who(inst)
        if not rows:
            self.out(f"{who} has no backups yet; `assistant update{self._flag(inst)}` makes one before it changes anything.")
        else:
            self.out(f"Backups of {who} (inside its data volume, newest last; the newest {KEEP_BACKUPS} are kept):")
            for b in rows:
                mark = "  <- rollback point" if b.get("id") == point.get("backup") else ""
                self.out(f"  {b.get('id')}  {_when(b.get('created'))}  {_size(b.get('bytes')):>9}  {b.get('label', '')}"
                         f"{'  version ' + str(b['version']) if b.get('version') else ''}{mark}")
        if point:
            held = read_env(inst.env, "KELO_IMAGE")
            self.out(f"Rollback point: {_when(point.get('created'))} (`assistant rollback{self._flag(inst)}`)"
                     + ("; it is held at those images now" if held else ""))
        return 0


def run_command(args: Any, manager: Instances | None = None, updater: Updater | None = None,
                ask: Callable[[str], str] | None = None) -> int:
    """`assistant update [name]`, `assistant rollback [name]`, `assistant backups [name]`, `assistant instance update`."""
    import subprocess
    import sys

    env_file = getattr(args, "env_file", None)
    m = manager or Instances(Path.cwd(), main_env=Path(env_file).resolve() if env_file else None)
    u = updater or Updater(m, health_timeout=float(getattr(args, "timeout", None) or HEALTH_TIMEOUT))
    lock = UpdateLock(m)
    try:
        lock.acquire()
        try:
            if args.cmd in ("update", "instance") and updater is None and not getattr(args, "clis", None) \
                    and not getattr(args, "no_build", False):
                from .updater_service import terminal_source   # copies on signed release images update from the release list
                u.source = terminal_source(m, getattr(args, "source", None) or "auto")
            if args.cmd == "update":
                return u.update([getattr(args, "name", None) or MAIN], build=not getattr(args, "no_build", False),
                                clis=getattr(args, "clis", None))
            if args.cmd == "instance":         # `assistant instance update`: every extra copy, one build
                from . import limits
                for n, size in limits.migrate_all(m):
                    m.out(f"{n}: sizes moved to today's numbers for {size} (applies the next time it starts).")
                return u.update(m.names(), build=not getattr(args, "no_build", False))
            if args.cmd == "rollback":
                interactive = ask is not None or sys.stdin.isatty()
                return u.rollback(args.name, yes=args.yes, ask=(ask or input) if interactive else None, keep_data=args.keep_data)
            return u.list_backups(args.name)
        finally:
            lock.release()
    except (EOFError, KeyboardInterrupt):
        m.out("\nCancelled.")
        return 1
    except (InstanceError, UpdateError) as e:
        m.out(f"error: {e}")
        return 1
    except subprocess.CalledProcessError as e:
        m.out(f"error: a docker command failed (exit {e.returncode}); see the output above")
        return 1


LOCK_STALE = 15 * 60   # a lock held from another host (the updater container) that was not touched this long was left behind


class UpdateLock:
    """instances/.update.lock: one update or rollback at a time, whether it runs from the terminal or in the updater container.

    The file holds "<pid>@<host>". A holder on this same host is checked by its process number; one on another host (the updater
    container has its own process numbers) counts as long as it keeps the file fresh (touch() at least every few minutes) and is
    stale after LOCK_STALE. A plain number (an older file) is a process on this host."""

    def __init__(self, m: Instances, *, host: str | None = None, clock: Callable[[], float] = time.time):
        import socket

        self.path = m.dir / ".update.lock"
        self.host = host or socket.gethostname()
        self.clock = clock
        self.held = False

    def holder(self) -> str | None:
        """Who holds a live lock ("process N" / "process N on <host>"), else None (no lock, or a stale one)."""
        try:
            text, mtime = self.path.read_text().strip(), self.path.stat().st_mtime
        except OSError:
            return None
        pid, _, host = text.partition("@")
        if not pid.isdigit():
            return None
        if not host or host == self.host:
            return f"process {pid}" if _alive(int(pid)) else None
        return f"process {pid} on {host}" if self.clock() - mtime < LOCK_STALE else None

    def acquire(self) -> None:
        self.path.parent.mkdir(exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                who = self.holder()
                if who:
                    raise InstanceError(f"another update or rollback is running ({who}); wait for it to finish") from None
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()}@{self.host}")
            self.held = True
            return
        raise InstanceError("another update or rollback just started; wait for it to finish")

    def touch(self) -> None:
        if self.held:
            try:
                os.utime(self.path)
            except OSError:
                pass

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
