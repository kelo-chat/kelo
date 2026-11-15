"""Safe update with automatic rollback (updates.py). Docker is a fake world that keeps images, containers and each copy's data volume
in memory; the in-container helper is simulated there and tested for real (on a temporary folder) at the end."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tarfile
from types import SimpleNamespace as NS

import pytest

from assistant.cli import read_env, write_env
from assistant.instances import IMAGE_VARS, Instances
from assistant.store import Store, schema_fingerprint
from assistant.updates import HELPER, Updater, run_command

VAR_OF = {svc: var for var, (_, svcs) in IMAGE_VARS.items() for svc in svcs}
OLD = {"KELO_IMAGE": "sha256:old-server", "KELO_WORKROOM_IMAGE": "sha256:old-workroom",
       "KELO_CLI_CLAUDE_IMAGE": "sha256:old-claude", "KELO_CLI_AGY_IMAGE": "sha256:old-agy"}


class World:
    """Fake docker. `bad` image ids never become healthy; `migrates` ids change the schema of a volume when they start."""

    def __init__(self, root):
        self.root = root
        self.images = {IMAGE_VARS[v][0]: i for v, i in OLD.items()}
        self.ids = set(OLD.values())
        self.containers = {}                    # (project, service) -> {"image": id, "running": bool}
        self.volumes = {}                       # project -> {"schema", "data", "backups": [..], "free", "state"}
        self.ports = {}                         # port -> project
        self.log = []
        self.bad, self.migrates = set(), set()
        self.fail_build = None                  # "server" | "workroom" | "claude-cli"
        self.fail_backup = self.fail_up = False
        self.builds = 0

    # -------------------------------------------------------------- helpers
    def resolve(self, ref):
        self.ids |= set(self.images.values())  # an image stays on the computer when its tag goes (until pruned)
        return self.images.get(ref) or (ref if ref in self.ids else None)

    def ok(self, out="", rc=0):
        return subprocess.CompletedProcess([], rc, stdout=out, stderr="" if rc == 0 else "boom")

    def project_of(self, cmd):
        return cmd[cmd.index("-p") + 1] if "-p" in cmd else "main"

    def volume(self, project):
        return self.volumes.setdefault(project, {"schema": "s1", "data": "v1", "backups": [], "free": 50 * 2**30, "state": 2**30})

    def add_copy(self, m, name, *, browser=True, clis=()):
        inst = m.create(name, browser=browser) if name != "main" else m.load("main")
        if clis:
            write_env(inst.env, {"COMPOSE_PROFILES": ",".join(clis)})
        project = inst.project or "main"
        self.ports[inst.server_port] = project
        u = Updater(m)
        for svc in u.services(m.load(name)):
            self.containers[(project, svc)] = {"image": OLD[VAR_OF[svc]], "running": True}
        self.volume(project)
        return m.load(name)

    # -------------------------------------------------------------- docker
    def __call__(self, cmd, cwd=None, capture_output=False, text=False, env=None, check=False, **kw):
        env = env or {}
        if cmd[:2] == ["docker", "compose"]:
            r = self.compose(cmd, env)
        elif cmd[:3] == ["docker", "image", "inspect"]:
            iid = self.resolve(cmd[-1])
            r = self.ok(iid or "", 0 if iid else 1)
        elif cmd[:2] == ["docker", "tag"]:
            iid = self.resolve(cmd[2])
            if iid:
                self.images[cmd[3]] = iid
                self.log.append(("tag", cmd[3]))
            r = self.ok(rc=0 if iid else 1)
        elif cmd[:3] == ["docker", "image", "ls"]:
            pattern = cmd[-1].split("=", 1)[1].rstrip("*")
            r = self.ok("\n".join(t for t in self.images if t.startswith(pattern)))
        elif cmd[:3] == ["docker", "image", "rm"]:
            for tag in cmd[3:]:
                self.images.pop(tag, None)
            r = self.ok()
        elif cmd[:2] == ["docker", "inspect"]:
            c = self.containers.get(tuple(cmd[-1].split("|")))
            r = self.ok(c["image"] if c else "", 0 if c else 1)
        else:
            raise AssertionError(f"unexpected command {cmd}")
        if check and r.returncode:
            raise subprocess.CalledProcessError(r.returncode, cmd)
        return r

    def compose(self, cmd, env):
        project = self.project_of(cmd)
        rest = cmd[cmd.index("--env-file") + 2:] if "--env-file" in cmd else cmd[4:]
        while rest and rest[0] == "--profile":
            rest = rest[2:]
        verb = rest[0]
        mine = {s: c for (p, s), c in self.containers.items() if p == project}
        if verb == "ps" and "--status" in rest:
            return self.ok("\n".join(s for s, c in mine.items() if c["running"]))
        if verb == "ps":
            return self.ok(f"{project}|{rest[-1]}" if rest[-1] in mine else "")
        if verb in ("exec", "run"):
            image = mine["server"]["image"] if verb == "exec" else self.resolve(env["KELO_IMAGE"])
            return self.helper(project, rest[rest.index("-c") + 2:], image)
        if verb == "build":
            if self.fail_build and self.fail_build in rest:
                return self.ok(rc=1)
            self.builds += 1
            for svc in rest[1:]:
                if svc.startswith("--"):
                    continue
                var = VAR_OF[svc]
                self.images[env[var]] = f"sha256:new-{svc}"
                self.log.append(("build", svc))
            return self.ok()
        if verb == "up":
            self.log.append(("up", project))
            if self.fail_up:
                return self.ok(rc=1)
            only = [s for s in rest[2:] if not s.startswith("-")]
            for svc in (only or [s for s in mine]):
                if svc not in mine:
                    continue
                iid = self.resolve(env[VAR_OF[svc]])
                assert iid, f"missing image {env[VAR_OF[svc]]}"
                mine[svc].update(image=iid, running=True)
                if svc == "server" and iid in self.migrates:
                    self.volume(project)["schema"] = "s2"
                    self.volume(project)["data"] = "v2"
            return self.ok()
        if verb == "down":
            for svc in list(mine):
                del self.containers[(project, svc)]
            self.volumes.pop(project, None)
            return self.ok()
        if verb == "stop":
            for svc in rest[1:] or list(mine):
                mine[svc]["running"] = False
            return self.ok()
        raise AssertionError(f"unexpected compose {rest}")

    def helper(self, project, args, image):
        vol = self.volume(project)
        cmd = args[0]
        self.log.append((cmd, project, image))
        if cmd == "info":
            return self.ok(json.dumps({"schema": vol["schema"], "free": vol["free"], "state": vol["state"], "backups": 0}))
        if cmd == "backup":
            if self.fail_backup:
                return self.ok(rc=1)
            bid, label, meta, protect = args[1], args[2], json.loads(args[3]), args[4:]
            b = {**meta, "id": bid, "label": label, "created": 1.0, "schema": vol["schema"], "data": vol["data"], "bytes": 1000}
            vol["backups"] = [x for x in vol["backups"] if x["id"] != bid] + [b]
            keep = {x["id"] for x in vol["backups"][-3:]} | set(protect) | {bid}
            vol["backups"] = [x for x in vol["backups"] if x["id"] in keep]
            return self.ok(json.dumps(b))
        if cmd == "list":
            return self.ok(json.dumps(vol["backups"]))
        if cmd == "restore":
            b = next(x for x in vol["backups"] if x["id"] == args[1])
            vol["schema"], vol["data"] = b["schema"], b["data"]
            self.log.append(("restored", project, b["id"]))
            return self.ok(json.dumps({"id": b["id"], "schema": b["schema"]}))
        raise AssertionError(cmd)

    # -------------------------------------------------------------- http
    def http(self, url, headers=None, timeout=5.0):
        port = int(url.split(":")[2].split("/")[0])
        project = self.ports[port]
        c = self.containers.get((project, "server"))
        if not c or not c["running"] or c["image"] in self.bad:
            return 0, None
        if url.endswith("/api/about"):
            assert headers and headers["Authorization"].startswith("Bearer ") and len(headers["Authorization"]) > 20
            return 200, {"version": "0.1.0", "db": {"ok": True, "schema": self.volume(project)["schema"], "migrations": "done"}}
        return 200, {}


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        self.t += 1
        return self.t


def make(tmp_path, **kw):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("BROWSER=chromium\nASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    msgs = []
    w = World(tmp_path)
    m = Instances(tmp_path, run=w, port_free=lambda p: True, out=msgs.append)
    clock = Clock()
    mono = [0.0]

    def sleep(s):
        mono[0] += s
    u = Updater(m, http=w.http, sleep=sleep, clock=lambda: clock() * 60, monotonic=lambda: mono[0], health_timeout=10, **kw)
    return m, u, w, msgs


@pytest.fixture(autouse=True)
def no_shell_images(monkeypatch):
    for var in IMAGE_VARS:
        monkeypatch.delenv(var, raising=False)


def steps(w):
    return [e[0] for e in w.log]


def test_a_good_update_checks_records_backs_up_then_builds_and_swaps_in_that_order(tmp_path):
    m, u, w, msgs = make(tmp_path)
    inst = w.add_copy(m, "alice", clis=("claude",))
    assert u.update(["alice"]) == 0
    order = steps(w)
    assert order.index("info") < order.index("tag") < order.index("backup") < order.index("build") < order.index("up")
    point = u.point(inst)
    assert point["images"]["KELO_IMAGE"]["id"] == "sha256:old-server" and point["images"]["KELO_CLI_CLAUDE_IMAGE"]["id"] == "sha256:old-claude"
    assert "KELO_CLI_AGY_IMAGE" not in point["images"] and point["backup"] == point["id"] and point["schema"] == "s1"
    assert point["version"] == "0.1.0"
    assert w.images[point["images"]["KELO_IMAGE"]["tag"]] == "sha256:old-server"            # the old image cannot be pruned
    assert point["images"]["KELO_IMAGE"]["tag"].startswith("assistant:rollback-alice-")
    assert {w.containers[("assistant-u-alice", s)]["image"] for s in ("server", "browser", "egress")} == {"sha256:new-server"}
    assert w.containers[("assistant-u-alice", "claude-cli")]["image"] == "sha256:new-claude-cli"
    assert not read_env(inst.env, "KELO_IMAGE") and "updated and healthy" in msgs[-1]
    assert stat.S_IMODE((tmp_path / "instances" / "alice" / "rollback.json").stat().st_mode) == 0o600
    assert "owner-token" not in "\n".join(msgs) and read_env(inst.env, "ASSISTANT_TOKEN") not in "\n".join(msgs)


def test_too_little_disk_stops_before_anything_changes(tmp_path):
    m, u, w, msgs = make(tmp_path)
    inst = w.add_copy(m, "alice")
    w.volume("assistant-u-alice")["free"] = 2 * 2**30
    assert u.update(["alice"]) == 1
    assert steps(w) == ["info"] and u.point(inst) is None and w.builds == 0
    assert "before changing anything" in msgs[-1] and "not enough free disk" in msgs[-1]


def test_a_failed_backup_stops_before_the_build_and_leaves_no_rollback_tags(tmp_path):
    m, u, w, msgs = make(tmp_path)
    w.add_copy(m, "alice")
    w.fail_backup = True
    assert u.update(["alice"]) == 1
    assert "build" not in steps(w) and "up" not in steps(w)
    assert not [t for t in w.images if "rollback" in t] and "before changing anything" in msgs[-1]


def test_a_failed_build_changes_nothing_and_puts_the_shared_tags_back(tmp_path):
    m, u, w, msgs = make(tmp_path)
    inst = w.add_copy(m, "alice")
    w.fail_build = "workroom"                  # the server image was already rebuilt when this fails
    assert u.update(["alice"]) == 1
    assert w.images["assistant:local"] == "sha256:old-server" and "up" not in steps(w)
    assert w.containers[("assistant-u-alice", "server")]["image"] == "sha256:old-server" and not read_env(inst.env, "KELO_IMAGE")
    assert "Nothing was changed" in msgs[-1]


def test_a_new_image_that_never_gets_healthy_is_rolled_back_with_its_migrated_data(tmp_path):
    m, u, w, msgs = make(tmp_path)
    inst = w.add_copy(m, "alice")
    w.bad.add("sha256:new-server")
    w.migrates.add("sha256:new-server")        # it changed the database before it hung
    assert u.update(["alice"]) == 1
    vol = w.volume("assistant-u-alice")
    assert vol["schema"] == "s1" and vol["data"] == "v1" and "restored" in steps(w)
    restore = next(e for e in w.log if e[0] == "restore")
    assert restore[2] == "sha256:old-server"                                # restored with the old image, the server stopped
    assert w.containers[("assistant-u-alice", "server")]["image"] == "sha256:old-server"
    point = u.point(inst)
    assert read_env(inst.env, "KELO_IMAGE") == point["images"]["KELO_IMAGE"]["tag"]       # held at the old images
    assert w.images["assistant:local"] == "sha256:old-server"               # nobody else picks up the broken build
    text = "\n".join(msgs)
    assert "ROLLED BACK" in text and "restored from the backup" in text and "/healthz" in text


def test_a_failure_without_a_schema_change_keeps_the_data(tmp_path):
    m, u, w, msgs = make(tmp_path)
    w.add_copy(m, "alice")
    w.bad.add("sha256:new-server")
    assert u.update(["alice"]) == 1
    assert "restored" not in steps(w) and "its data was kept" in msgs[-1]


def test_a_failing_restart_command_rolls_back_too(tmp_path):
    m, u, w, msgs = make(tmp_path)
    w.add_copy(m, "alice")
    w.fail_up = True
    assert u.update(["alice"]) == 1
    assert "restarting the containers failed (exit 1)" in "\n".join(msgs)
    assert "ROLLBACK INCOMPLETE" in msgs[-1]                # the fake's `up` keeps failing, so the copy cannot come back either


def test_backups_keep_the_newest_three_and_only_the_current_rollback_tags(tmp_path):
    m, u, w, msgs = make(tmp_path)
    inst = w.add_copy(m, "alice")
    for _ in range(5):
        assert u.update(["alice"]) == 0
    vol = w.volume("assistant-u-alice")
    assert len(vol["backups"]) == 3 and vol["backups"][-1]["id"] == u.point(inst)["backup"]
    tags = [t for t in w.images if t.startswith("assistant:rollback-alice-")]
    assert tags == [u.point(inst)["images"]["KELO_IMAGE"]["tag"]]


def test_deleting_a_copy_drops_its_rollback_tags_but_not_another_copys(tmp_path):
    m, u, w, msgs = make(tmp_path)
    w.add_copy(m, "alice")
    w.add_copy(m, "bob")
    assert u.update(["alice"]) == 0 and u.update(["bob"], build=False) == 0
    assert [t for t in w.images if "rollback-alice-" in t] and [t for t in w.images if "rollback-bob-" in t]
    m.delete("alice")
    assert not [t for t in w.images if "rollback-alice-" in t] and len([t for t in w.images if "rollback-bob-" in t]) == 2
    assert "sha256:old-server" in w.ids and ("assistant-u-alice", "server") not in w.containers


def test_rollback_on_purpose_restores_images_and_migrated_data_and_the_next_update_clears_the_hold(tmp_path):
    m, u, w, msgs = make(tmp_path)
    inst = w.add_copy(m, "alice")
    w.migrates.add("sha256:new-server")
    assert u.update(["alice"]) == 0 and w.volume("assistant-u-alice")["data"] == "v2"
    with pytest.raises(Exception, match="needs a terminal"):
        u.rollback("alice", yes=False, ask=None)
    assert u.rollback("alice", yes=False, ask=lambda q: "n") == 1 and w.volume("assistant-u-alice")["data"] == "v2"
    assert u.rollback("alice", yes=True, ask=None) == 0
    vol = w.volume("assistant-u-alice")
    assert vol["data"] == "v1" and vol["backups"][-1]["label"] == "before rollback" and vol["backups"][-1]["data"] == "v2"
    assert w.containers[("assistant-u-alice", "server")]["image"] == "sha256:old-server" and read_env(inst.env, "KELO_IMAGE")
    assert "Rolled back" in msgs[-1] and "with the data from that backup" in msgs[-1]
    w.migrates.clear()
    assert u.update(["alice"], build=False) == 0                    # the shared tag still has the newer build
    assert not read_env(inst.env, "KELO_IMAGE") and w.containers[("assistant-u-alice", "server")]["image"] == "sha256:new-server"


def test_rollback_keep_data_and_a_copy_without_a_point(tmp_path):
    m, u, w, msgs = make(tmp_path)
    w.add_copy(m, "alice")
    with pytest.raises(Exception, match="no rollback point"):
        u.rollback("alice", yes=True, ask=None)
    w.migrates.add("sha256:new-server")
    assert u.update(["alice"]) == 0
    assert u.rollback("alice", yes=True, ask=None, keep_data=True) == 0
    assert w.volume("assistant-u-alice")["data"] == "v2" and "with its current data" in msgs[-1]


def test_a_rollback_tag_that_went_away_is_made_again_from_the_image_id(tmp_path):
    m, u, w, msgs = make(tmp_path)
    inst = w.add_copy(m, "alice")
    assert u.update(["alice"]) == 0
    del w.images[u.point(inst)["images"]["KELO_IMAGE"]["tag"]]
    assert u.rollback("alice", yes=True, ask=None) == 0
    assert w.containers[("assistant-u-alice", "server")]["image"] == "sha256:old-server"


def test_every_extra_copy_shares_one_build_and_a_later_failure_keeps_the_good_ones(tmp_path):
    m, u, w, msgs = make(tmp_path)
    a, b, c = (w.add_copy(m, n) for n in ("alice", "bob", "carol"))
    w.volume("assistant-u-bob")
    orig = w.compose

    def compose(cmd, env):                    # bob's database makes the new server hang
        if w.project_of(cmd) == "assistant-u-bob" and "up" in cmd and w.resolve(env["KELO_IMAGE"]) == "sha256:new-server":
            w.bad.add("sha256:new-server")
        elif "up" in cmd:
            w.bad.discard("sha256:new-server")
        return orig(cmd, env)
    w.compose = compose
    assert u.update(m.names()) == 1
    assert w.builds == 2                                                     # server + workroom, once
    assert w.containers[("assistant-u-alice", "server")]["image"] == "sha256:new-server" and not read_env(a.env, "KELO_IMAGE")
    assert w.containers[("assistant-u-bob", "server")]["image"] == "sha256:old-server" and read_env(b.env, "KELO_IMAGE")
    assert w.containers[("assistant-u-carol", "server")]["image"] == "sha256:old-server" and not read_env(c.env, "KELO_IMAGE")
    assert w.images["assistant:local"] == "sha256:new-server"               # alice runs it, so the shared tag stays
    assert "Not updated" in msgs[-1] and "carol" in msgs[-1]


def test_cli_only_update_rebuilds_just_that_sidecar_and_refuses_one_that_is_off(tmp_path):
    m, u, w, msgs = make(tmp_path)
    w.add_copy(m, "main", clis=("claude",))
    with pytest.raises(Exception, match="not enabled"):
        u.update(["main"], clis=["agy"])
    assert u.update(["main"], clis=["claude"]) == 0
    assert [e[1] for e in w.log if e[0] == "build"] == ["claude-cli"]
    assert w.containers[("main", "claude-cli")]["image"] == "sha256:new-claude-cli"
    assert w.containers[("main", "server")]["image"] == "sha256:old-server"


def test_a_shell_image_variable_is_used_for_builds_but_a_hold_wins_for_its_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("KELO_IMAGE", "assistant:trial")
    m, u, w, msgs = make(tmp_path)
    w.images["assistant:trial"] = "sha256:old-server"
    inst = w.add_copy(m, "alice")
    assert u.update(["alice"]) == 0
    assert w.images["assistant:trial"] == "sha256:new-server" and w.images["assistant:local"] == "sha256:old-server"
    write_env(inst.env, {"KELO_IMAGE": "assistant:rollback-alice-x"})
    assert m.image_env(m.load("alice"))["KELO_IMAGE"] == "assistant:rollback-alice-x"


def test_backups_lists_them_with_the_rollback_point(tmp_path):
    m, u, w, msgs = make(tmp_path)
    inst = w.add_copy(m, "alice")
    assert u.list_backups("alice") == 0 and "no backups yet" in msgs[-1]
    u.update(["alice"])
    msgs.clear()
    assert u.list_backups("alice") == 0
    assert "<- rollback point" in msgs[1] and u.point(inst)["backup"] in msgs[1] and msgs[-1].startswith("Rollback point:")


def test_the_command_line_routes_and_a_second_run_is_locked_out(tmp_path, monkeypatch):
    m, u, w, msgs = make(tmp_path)
    w.add_copy(m, "alice")
    (tmp_path / "instances" / ".update.lock").write_text(str(os.getpid()))
    assert run_command(NS(cmd="update", name="alice", clis=None, no_build=False), manager=m, updater=u) == 1
    assert "another update" in msgs[-1]
    (tmp_path / "instances" / ".update.lock").write_text("999999")             # a lock left by a process that is gone
    assert run_command(NS(cmd="update", name="alice", clis=None, no_build=False), manager=m, updater=u) == 0
    assert not (tmp_path / "instances" / ".update.lock").exists()
    assert run_command(NS(cmd="backups", name="alice"), manager=m, updater=u) == 0
    assert run_command(NS(cmd="rollback", name="nobody", yes=True, keep_data=False), manager=m, updater=u) == 1
    assert "no such instance" in msgs[-1]
    from assistant import cli
    seen = []
    monkeypatch.setattr("assistant.updates.run_command", lambda args: seen.append(args) or 0)
    for argv in (["update"], ["update", "alice", "--cli", "claude", "--timeout", "30"], ["rollback", "alice", "--yes"],
                 ["backups"], ["instance", "update", "--no-build"]):
        assert cli.main(argv) == 0
    assert [a.cmd for a in seen] == ["update", "update", "rollback", "backups", "instance"]
    assert seen[1].name == "alice" and seen[1].clis == ["claude"] and seen[1].timeout == 30 and seen[4].no_build


# ---------------------------------------------------------------------------------------------- the helper, run for real
def helper(state, *args):
    r = subprocess.run([sys.executable, "-c", HELPER, *args], env={**os.environ, "KELO_STATE": str(state)},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_helper_backs_up_consistently_privately_keeps_three_and_restores(tmp_path):
    state = tmp_path / "state"
    store = Store(state / "assistant.db", secret_key=None)
    store.set_profile(user_name="Ann")
    (state / "home" / "uploads").mkdir(parents=True)
    (state / "home" / "uploads" / "a.txt").write_text("first")
    (state / "home" / "uploads" / "a.txt").chmod(0o644)
    info = helper(state, "info")
    assert info["schema"] == schema_fingerprint(store._db) == store.schema_info()["schema"] and info["free"] > 0

    first = helper(state, "backup", "20260101-000001", "before update", json.dumps({"version": "0.1.0"}))
    d = state / ".backups" / first["id"]
    assert first["schema"] == info["schema"] and first["version"] == "0.1.0"
    assert stat.S_IMODE((state / ".backups").stat().st_mode) == 0o700 and stat.S_IMODE(d.stat().st_mode) == 0o700
    for f in d.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) & 0o077 == 0, f                  # nothing readable by others
    con = sqlite3.connect(d / "assistant.db")
    assert con.execute("SELECT value FROM profile WHERE key='user_name'").fetchone()[0] == "Ann"
    con.close()
    with tarfile.open(d / "state.tar.gz") as t:
        names = t.getnames()
    assert "home/uploads/a.txt" in names and "master.key" in names and not any(n.startswith((".backups", "assistant.db")) for n in names)

    # the newer server migrates and changes things; then the backup is put back
    store.set_profile(user_name="Bob")
    store._db.execute("ALTER TABLE goals ADD COLUMN newer TEXT NOT NULL DEFAULT ''")
    store._db.commit()
    store._db.close()
    (state / "home" / "uploads" / "a.txt").write_text("second")
    (state / "home" / "new.txt").write_text("made later")
    assert helper(state, "info")["schema"] != first["schema"]
    back = helper(state, "restore", first["id"])
    assert back["schema"] == first["schema"]
    assert (state / "home" / "uploads" / "a.txt").read_text() == "first" and not (state / "home" / "new.txt").exists()
    assert stat.S_IMODE((state / "home" / "uploads" / "a.txt").stat().st_mode) == 0o644      # modes come back as they were
    con = sqlite3.connect(state / "assistant.db")
    assert con.execute("SELECT value FROM profile WHERE key='user_name'").fetchone()[0] == "Ann"
    con.close()
    assert sorted(p.name for p in (state / ".backups").iterdir()) == [first["id"]]          # no work folders left behind

    for i in range(2, 7):
        helper(state, "backup", f"20260101-00000{i}", "before update", "{}", first["id"])     # the rollback point's backup is protected
    kept = [b["id"] for b in helper(state, "list")]
    assert kept == [first["id"], "20260101-000004", "20260101-000005", "20260101-000006"]


async def test_about_reports_that_the_database_opened_with_its_schema(tmp_path):
    import httpx

    from assistant.server import Settings, create_app
    from test_server import AUTH, TOKEN, Running, scripted

    app = create_app(Settings(token=TOKEN, cdp_host=None, db_path=str(tmp_path / "s" / "assistant.db"), home_dir=str(tmp_path / "s" / "home")),
                     tools=None, stream_fn=scripted())
    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url) as c:
        assert (await c.get("/api/about")).status_code == 401
        db = (await c.get("/api/about", headers=AUTH)).json()["db"]
    con = sqlite3.connect(tmp_path / "s" / "assistant.db")
    assert db["ok"] is True and db["migrations"] == "done" and db["tables"] > 10 and db["schema"] == schema_fingerprint(con)
    con.close()
