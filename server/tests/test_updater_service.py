"""The updater container (updater_service.py): fixed commands, its token, release images checked before use, the same safe flow as
`assistant update` (updates.py), rollback, and the updater moving itself last. Docker is test_updates.World plus a fake registry."""

import json
import os
import subprocess
import threading
import time

import httpx
import pytest

from assistant import release as rel
from assistant import updater_service as us
from assistant.cli import read_env, write_env
from assistant.instances import IMAGE_VARS, Instances
from assistant.updates import UpdateLock, Updater, run_command
from test_updates import Clock, World, steps

TOKEN = "u" * 40
DIG = {c: "sha256:" + f"{i:x}" * 64 for i, c in enumerate(rel.COMPONENTS, start=1)}
NEW_ID = {c: f"sha256:rel-{c}" for c in rel.COMPONENTS}
OLD_UPDATER = "sha256:" + "a" * 64
NEW_UPDATER = "sha256:" + "b" * 64
NEW_ID["updater"] = NEW_UPDATER


class RegWorld(World):
    """World + a registry of signed release images + labels + the updater's own container."""

    def __init__(self, root):
        super().__init__(root)
        self.registry = {f"ghcr.io/kelo-chat/{c}@{DIG[c]}": NEW_ID[c] for c in rel.COMPONENTS}
        self.labels = {"sha256:old-server": {rel.RELEASE_LABEL: "0.4.0", rel.VERSION_LABEL: "0.4.0"}}
        for c, iid in NEW_ID.items():
            self.labels[iid] = {rel.RELEASE_LABEL: "0.5.0", rel.VERSION_LABEL: "0.5.0"}
        self.unsigned, self.wrong_digest = set(), set()
        self.pulled, self.runs = [], []
        self.updater_image = OLD_UPDATER
        self.images["assistant-updater:local"] = OLD_UPDATER
        self.ids.add(OLD_UPDATER)

    def compose(self, cmd, env):
        project = self.project_of(cmd)
        upd = self.containers.get((project, "updater"))
        if "up" in cmd and cmd[-1] == "updater":               # the updater recreated on its shared tag
            self.log.append(("recreate-updater", project))
            upd["image"] = self.resolve("assistant-updater:local")
            return self.ok()
        if upd is None or "up" not in cmd:
            return super().compose(cmd, env)
        if upd is not None:                                    # never part of a copy's own restart in this fake
            del self.containers[(project, "updater")]
        try:
            return super().compose(cmd, env)
        finally:
            if upd is not None and (project, "updater") not in self.containers:
                self.containers[(project, "updater")] = upd

    def http(self, url, headers=None, timeout=5.0):
        code, body = super().http(url, headers, timeout)
        if code == 200 and url.endswith("/api/about"):              # the package version, as the release workflow sets it
            image = self.containers[(self.ports[int(url.split(":")[2].split("/")[0])], "server")]["image"]
            body = {**body, "version": self.labels.get(image, {}).get(rel.VERSION_LABEL, "dev")}
        return code, body

    def __call__(self, cmd, cwd=None, capture_output=False, text=False, env=None, check=False, **kw):
        if cmd[:2] == ["docker", "pull"]:
            ref = cmd[-1]
            if ref not in self.registry:
                return self.ok(rc=1)
            self.images[ref] = self.registry[ref]
            self.pulled.append(ref)
            self.log.append(("pull", ref))
            return self.ok()
        if cmd[:3] == ["docker", "image", "inspect"] and "{{json .RepoDigests}}" in cmd:
            ref = cmd[-1]
            self.log.append(("verify", ref))
            return self.ok(json.dumps([] if ref in self.wrong_digest else [ref]) if ref in self.images else "", 0 if ref in self.images else 1)
        if cmd[:3] == ["docker", "image", "inspect"] and "{{json .Config.Labels}}" in cmd:
            iid = self.resolve(cmd[-1])
            return self.ok(json.dumps(self.labels.get(iid)) if iid else "", 0 if iid else 1)
        if cmd[0] == "cosign" or cmd[:2] == ["docker", "run"] and "verify" in cmd:
            ref = cmd[-1]
            if ref in self.unsigned:
                return self.ok(rc=1)
            return self.ok(json.dumps([{"critical": {"image": {"docker-manifest-digest": ref.split("@")[1]}}}]))
        if cmd[:2] == ["docker", "inspect"] and cmd[-1] == "updater-host":
            return self.ok(self.updater_image)
        if cmd[:2] == ["docker", "run"]:
            self.runs.append(cmd)
            return self.ok("swapcontainer")
        if cmd[:2] == ["docker", "compose"] and "config" in cmd and "--services" in cmd:
            project = self.project_of(cmd)
            return self.ok("\n".join(s for (p, s) in self.containers if p == project) + "\nupdater")
        return super().__call__(cmd, cwd=cwd, capture_output=capture_output, text=text, env=env, check=check, **kw)


def release(**kw):
    m = rel.build_manifest("0.5.0", DIG, date="2026-11-09T00:00:00Z", notes={"en": "New", "ko": "새 기능"})
    m.update(kw)
    return rel.parse(m)


@pytest.fixture(autouse=True)
def no_shell_images(monkeypatch):
    for var in (*IMAGE_VARS, us.UPDATER_VAR):
        monkeypatch.delenv(var, raising=False)


def make(tmp_path, *, rel_=None, threaded=False):
    (tmp_path / "compose.yml").write_text("x-kelo-compose-level: 1\nservices: {}\n")
    (tmp_path / ".env").write_text("BROWSER=chromium\nASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    w = RegWorld(tmp_path)
    clock = Clock()
    mono = [0.0]

    def sleep(s):
        mono[0] += s

    def factory(m, http=None, clock=None, health_timeout=None, **kw):
        return Updater(m, http=w.http, sleep=sleep, clock=lambda: clock_() * 60, monotonic=lambda: mono[0], health_timeout=10, **kw)
    clock_ = clock
    swaps = []
    current = {"release": rel_ or release()}
    svc = us.Service(tmp_path, token=TOKEN, run=w, fetch=lambda url, reg: current["release"], clock=clock, host="updater-host",
                     swap=swaps.append, updater_factory=factory, threaded=threaded)
    m = Instances(tmp_path, run=w, port_free=lambda p: True, out=lambda s: None)
    w.add_copy(m, "main")
    return svc, w, m, swaps, current


def job(svc):
    return svc.load()["job"]


# ---------------------------------------------------------------------------------------------------------------- the flow
def test_an_update_pulls_and_checks_every_image_before_any_tag_moves_then_runs_the_safe_flow_in_order(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    w.add_copy(m, "alice")
    st = svc.status()
    assert st["mode"] == "registry" and st["current"] == "0.4.0" and st["update_available"] is False   # nothing checked yet
    assert svc.check()["update_available"] is True and svc.status()["latest"]["version"] == "0.5.0"
    svc.start("update")
    order = steps(w)
    first_pull, last_verify = order.index("pull"), max(i for i, s in enumerate(order) if s == "verify")
    assert order.index("info") < order.index("tag") < order.index("backup") < first_pull < last_verify < order.index("up")
    shared_tags = [i for i, e in enumerate(w.log) if e[0] == "tag" and e[1] in ("assistant:local", "assistant-workroom:local")]
    assert shared_tags and min(shared_tags) > last_verify                    # nothing is tagged before every check passed
    assert w.images["assistant:local"] == NEW_ID["server"] and w.images["assistant-updater:incoming"] == NEW_UPDATER
    assert w.images["assistant-updater:local"] == OLD_UPDATER                # the running updater's tag waits for the swap
    for svc_ in ("server", "browser", "egress"):
        assert w.containers[("main", svc_)]["image"] == NEW_ID["server"]
        assert w.containers[("assistant-u-alice", svc_)]["image"] == NEW_ID["server"]
    ups = [e for e in w.log if e[0] == "up"]
    assert ups[0][1] == "main"                                               # the main copy first
    j = job(svc)
    assert j["step"] == "self_update" and j["self_update"] == {"state": "pending", "from": OLD_UPDATER, "to": NEW_UPDATER}
    assert swaps and swaps[0]["self_update"]["to"] == NEW_UPDATER
    assert svc.load()["release"]["version"] == "0.5.0"
    assert not (tmp_path / "instances" / ".update.lock").exists()


def test_the_cli_sidecars_are_never_pulled_or_rebuilt_and_keep_running_their_local_images(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    w.add_copy(m, "main", clis=("claude", "agy"))
    svc.start("update")
    assert job(svc)["step"] == "self_update"
    assert not [r for r in w.pulled if "cli" in r] and "build" not in steps(w)
    assert {r.split("@")[0].rsplit("/", 1)[1] for r in w.pulled} == {"server", "workroom", "updater"}
    assert w.containers[("main", "claude-cli")]["image"] == "sha256:old-claude"
    assert w.containers[("main", "agy-cli")]["image"] == "sha256:old-agy"
    assert w.containers[("main", "server")]["image"] == NEW_ID["server"]


def test_the_running_updater_is_never_restarted_by_its_own_update(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    seen = []
    orig = w.compose

    def compose(cmd, env):
        if "up" in cmd:
            seen.append(cmd)
        return orig(cmd, env)
    w.compose = compose
    svc.start("update")
    assert seen and all("updater" not in c[c.index("up"):] for c in seen)
    assert all("server" in c[c.index("up"):] for c in seen)                  # services are named, so the updater is left alone


@pytest.mark.parametrize("break_it, why", [
    (lambda w: w.unsigned.add(f"ghcr.io/kelo-chat/workroom@{DIG['workroom']}"), "signature"),
    (lambda w: w.wrong_digest.add(f"ghcr.io/kelo-chat/server@{DIG['server']}"), "digest"),
    (lambda w: w.labels.update({NEW_ID["server"]: {rel.RELEASE_LABEL: "0.3.0", rel.VERSION_LABEL: "0.3.0"}}), "another version"),
    (lambda w: w.registry.pop(f"ghcr.io/kelo-chat/updater@{DIG['updater']}"), "downloading"),
])
def test_an_image_that_fails_a_check_stops_the_update_before_anything_changes(tmp_path, break_it, why):
    svc, w, m, swaps, _ = make(tmp_path)
    break_it(w)
    svc.start("update")
    j = job(svc)
    assert j["state"] == "failed" and why in j["error"] and not swaps
    assert "up" not in steps(w) and w.images["assistant:local"] == "sha256:old-server"
    assert w.containers[("main", "server")]["image"] == "sha256:old-server"
    assert not read_env(m.load("main").env, "KELO_IMAGE")


def test_a_release_that_does_not_come_up_is_rolled_back_with_its_data(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    w.bad.add(NEW_ID["server"])
    w.migrates.add(NEW_ID["server"])
    svc.start("update")
    j = job(svc)
    assert j["state"] == "rolled_back" and j["data_restored"] is True and "/healthz" in j["error"] and not swaps
    assert w.containers[("main", "server")]["image"] == "sha256:old-server" and w.volume("main")["data"] == "v1"
    assert w.images["assistant:local"] == "sha256:old-server" and j["from"] == "0.4.0" and j["to"] == "0.5.0"


def test_rollback_goes_back_for_every_copy_of_that_update(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    w.add_copy(m, "alice")
    svc.start("update")
    svc._patch_job(state="done")                                             # the swap finished
    st = svc.status()
    assert st["rollback"]["available"] is True and st["rollback"]["version"] == "0.4.0"
    svc.start("rollback")
    j = job(svc)
    assert j["action"] == "rollback" and j["state"] == "done" and j["to"] == "0.4.0"
    assert w.containers[("main", "server")]["image"] == "sha256:old-server"
    assert w.containers[("assistant-u-alice", "server")]["image"] == "sha256:old-server"


def test_refusals(tmp_path):
    svc, w, m, swaps, current = make(tmp_path)
    current["release"] = release(version="0.4.0")
    with pytest.raises(us.Refused) as e:
        svc.start("update")
    assert e.value.code == "up_to_date"
    with pytest.raises(us.Refused) as e:
        svc.start("rollback")
    assert e.value.code == "no_rollback"
    with pytest.raises(us.Refused) as e:
        svc.start("run")
    assert e.value.status == 404
    current["release"] = release()
    lock = UpdateLock(m, host="the-terminal")                                # `kelo update` running in a terminal right now
    lock.path.parent.mkdir(exist_ok=True)
    lock.path.write_text(f"{os.getpid()}@the-terminal")
    with pytest.raises(us.Refused) as e:
        svc.start("update")
    assert e.value.code == "busy" and "server machine" in e.value.detail
    os.utime(lock.path, (time.time() - 3600, time.time() - 3600))            # left behind long ago
    svc.start("update")
    assert job(svc)["state"] != "failed"


def test_a_copy_built_from_source_is_reported_and_never_updated_from_a_release(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    w.labels.pop("sha256:old-server")
    st = svc.check()
    assert st["mode"] == "local" and st["reason"] == "local_build" and st["update_available"] is False
    with pytest.raises(us.Refused) as e:
        svc.start("update")
    assert e.value.code == "local" and "kelo update" in e.value.detail
    write_env(m.load("main").env, {"BROWSER": "chrome"})
    assert svc.status()["reason"] == "chrome"


def test_a_release_that_needs_newer_setup_files_or_a_chrome_copy_is_refused_before_changing_anything(tmp_path):
    svc, w, m, swaps, current = make(tmp_path)
    current["release"] = release(compose_level=2)
    svc.start("update")
    assert job(svc)["state"] == "failed" and "installer" in job(svc)["error"] and "up" not in steps(w)


def test_one_extra_copy_failing_leaves_the_main_copy_updated(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    w.add_copy(m, "bob")
    orig = w.compose

    def compose(cmd, env):
        if w.project_of(cmd) == "assistant-u-bob" and "up" in cmd and w.resolve(env["KELO_IMAGE"]) == NEW_ID["server"]:
            w.bad.add(NEW_ID["server"])
        elif "up" in cmd:
            w.bad.discard(NEW_ID["server"])
        return orig(cmd, env)
    w.compose = compose
    svc.start("update")
    j = job(svc)
    assert "bob" in j["warning"] and j["state"] in ("running", "done") and j["step"] == "self_update"
    assert w.containers[("main", "server")]["image"] == NEW_ID["server"]
    assert w.containers[("assistant-u-bob", "server")]["image"] == "sha256:old-server"


def test_no_swap_when_the_updater_image_is_unchanged(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    w.updater_image = NEW_UPDATER
    svc.start("update")
    assert job(svc)["state"] == "done" and not swaps


def test_a_job_whose_updater_vanished_is_reported_as_failed(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    svc.save({"job": {"id": "x", "action": "update", "state": "running", "step": "restart", "heartbeat": svc.clock() - 3600,
                      "started_at": svc.clock() - 3600}})
    assert svc.status()["job"]["state"] == "failed"


# ---------------------------------------------------------------------------------------------------------------- the swap
def test_the_swap_is_launched_as_a_one_shot_container_with_no_network(tmp_path, monkeypatch):
    svc, w, m, _, _ = make(tmp_path)
    svc.swap_fn = svc._launch_swap
    svc.start("update")
    cmd = w.runs[-1]
    assert cmd[cmd.index("--network") + 1] == "none" and "--rm" in cmd and "--read-only" in cmd and "ALL" in cmd
    assert f"{tmp_path.resolve()}:{tmp_path.resolve()}" in cmd and cmd[cmd.index("--entrypoint") + 1] == "python"
    assert cmd[-6:] == ["swap", "--root", str(tmp_path.resolve()), "--new", NEW_UPDATER, "--old", OLD_UPDATER][-6:]
    assert NEW_UPDATER in cmd and TOKEN not in " ".join(cmd)


class SwapDocker:
    def __init__(self, healthy):
        self.healthy, self.calls, self.image = healthy, [], OLD_UPDATER

    def __call__(self, cmd, cwd=None, capture_output=False, text=False, env=None, **kw):
        self.calls.append(cmd)
        ok = lambda out="", rc=0: subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")  # noqa: E731
        if cmd[:2] == ["docker", "tag"]:
            self.image = cmd[2]
            return ok()
        if "up" in cmd:
            return ok()
        if "ps" in cmd:
            return ok("cid1")
        if cmd[:2] == ["docker", "inspect"]:
            good = self.image == OLD_UPDATER or self.healthy
            return ok(f"{self.image} {'healthy' if good else 'starting'}")
        raise AssertionError(cmd)


@pytest.mark.parametrize("healthy", [True, False])
def test_the_swap_moves_the_updater_or_puts_the_old_one_back(tmp_path, healthy):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    svc = us.Service(tmp_path, token=TOKEN, threaded=False)
    svc.save({"job": {"id": "j", "state": "running", "step": "self_update", "self_update": {"state": "pending"}}})
    d = SwapDocker(healthy)
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += s
    rc = us.swap(tmp_path, NEW_UPDATER, OLD_UPDATER, run=d, clock=clock, sleep=sleep, timeout=10)
    j = svc.load()["job"]
    ups = [c for c in d.calls if "up" in c]
    assert all(c[-3:] == ["--no-deps", "--force-recreate", "updater"] for c in ups)
    assert j["state"] == "done" and j["progress"] == 100
    if healthy:
        assert rc == 0 and j["self_update"]["state"] == "done" and len(ups) == 1
    else:
        assert rc == 1 and j["self_update"]["state"] == "failed" and j["self_update"]["restored"] is True and len(ups) == 2
        assert d.image == OLD_UPDATER


# ---------------------------------------------------------------------------------------------------------------- HTTP and token
@pytest.fixture
def http(tmp_path):
    svc, w, m, swaps, _ = make(tmp_path)
    srv = us.make_server(svc, host="127.0.0.1", port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", svc, w
    srv.shutdown()
    srv.server_close()


def test_the_commands_are_fixed_and_need_the_token(http):
    url, svc, w = http
    good = {"Authorization": f"Bearer {TOKEN}"}
    with httpx.Client(base_url=url, timeout=10) as c:
        assert c.get("/healthz").json() == {"ok": True}
        assert c.get("/status").status_code == 401
        assert c.get("/status", headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401
        assert c.get("/status", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
        assert c.post("/update").status_code == 401
        st = c.get("/status", headers=good)
        assert st.status_code == 200 and st.json()["mode"] == "registry" and TOKEN not in st.text
        for path in ("/run", "/exec", "/update/../exec", "/images", "/compose"):
            assert c.post(path, headers=good).status_code == 404
        assert c.put("/update", headers=good).status_code == 405 and c.delete("/status", headers=good).status_code == 405
        for body in ({"image": "evil/image:latest"}, {"tag": "x"}, {"version": "0.0.1"}, [1], "rm -rf /"):
            assert c.post("/update", headers=good, json=body).status_code == 422
        assert c.post("/update?image=evil", headers=good).status_code == 422
        assert c.post("/check", headers=good, content=b"x" * 100).status_code == 422
        assert c.post("/check", headers=good, json={}).status_code == 200
        r = c.post("/update", headers=good)
        assert r.status_code == 202 and r.json()["job"]["action"] == "update"
        r = c.post("/rollback", headers=good)
        assert r.status_code in (202, 409)


def test_the_service_needs_a_real_token(tmp_path):
    with pytest.raises(RuntimeError, match="UPDATER_TOKEN"):
        us.Service(tmp_path, token="short")


# ---------------------------------------------------------------------------------------------------------------- health over exec
def test_health_requests_run_inside_the_copys_server_container_with_its_own_token(tmp_path):
    (tmp_path / ".env").write_text("SERVER_PORT=8080\nASSISTANT_TOKEN=secret-secret-secret-123\n")
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"code": 200, "body": json.dumps({"db": {"ok": True}})}), stderr="")
    m = Instances(tmp_path, run=run, port_free=lambda p: True, out=lambda s: None)
    h = us.ExecHttp(m)
    code, body = h("http://127.0.0.1:8080/api/about", {"Authorization": "Bearer secret-secret-secret-123"})
    assert code == 200 and body == {"db": {"ok": True}}
    cmd = seen[-1]
    assert cmd[cmd.index("exec") + 1:cmd.index("exec") + 4] == ["-T", "server", "python"] and cmd[-2:] == ["/api/about", "1"]
    assert "secret-secret-secret-123" not in " ".join(cmd)
    assert h("http://127.0.0.1:9999/healthz") == (0, None)                   # not a copy of ours
    assert h("http://127.0.0.1:8080/x;rm -rf") == (0, None)


# ---------------------------------------------------------------------------------------------------------------- the terminal
def test_kelo_update_in_a_terminal_uses_the_release_on_a_release_copy_and_builds_on_a_source_copy(tmp_path, monkeypatch):
    (tmp_path / "compose.yml").write_text("x-kelo-compose-level: 1\nservices: {}\n")
    (tmp_path / ".env").write_text("BROWSER=chromium\nASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    w = RegWorld(tmp_path)
    msgs = []
    m = Instances(tmp_path, run=w, port_free=lambda p: True, out=msgs.append)
    w.add_copy(m, "main")
    monkeypatch.setattr(rel, "fetch", lambda url, registry: release())
    monkeypatch.setattr("shutil.which", lambda name: None)                    # no cosign here: the updater image's is used
    src = us.terminal_source(m)
    assert isinstance(src, us.RegistrySource) and src.terminal and src.verifier.cosign == ["docker", "run", "--rm", rel.COSIGN_IMAGE]
    assert us.terminal_source(m, "source") is None
    w.labels.pop("sha256:old-server")
    assert us.terminal_source(m) is None                                      # built from source: build again
    assert isinstance(us.terminal_source(m, "release"), us.RegistrySource)

    w.labels["sha256:old-server"] = {rel.RELEASE_LABEL: "0.4.0", rel.VERSION_LABEL: "0.4.0"}
    w.containers[("main", "updater")] = {"image": OLD_UPDATER, "running": True}
    mono = [0.0]
    u = Updater(m, http=w.http, sleep=lambda s: mono.__setitem__(0, mono[0] + s), monotonic=lambda: mono[0], health_timeout=10,
                source=us.terminal_source(m))
    assert u.update(["main"]) == 0
    assert w.containers[("main", "server")]["image"] == NEW_ID["server"] and w.images["assistant-updater:local"] == NEW_UPDATER
    assert w.containers[("main", "updater")]["image"] == NEW_UPDATER and ("recreate-updater", "main") in w.log
    assert "every image is the official one" in "\n".join(msgs)
    from types import SimpleNamespace as NS
    seen = []
    monkeypatch.setattr(us, "terminal_source", lambda m_, choice: seen.append(choice) or None)
    monkeypatch.setattr(Updater, "update", lambda self, names, **kw: 0)    # only the routing is checked here
    run_command(NS(cmd="update", name=None, clis=None, no_build=False, source="source"), manager=m)
    assert seen == ["source"]


# ---------------------------------------------------------------------------------------------------------------- the lock
def test_the_lock_tells_a_live_holder_in_the_container_from_one_left_behind(tmp_path):
    m = Instances(tmp_path, run=lambda *a, **k: None, out=lambda s: None)
    a = UpdateLock(m, host="container-1")
    a.acquire()
    b = UpdateLock(m, host="laptop")
    assert b.holder() == f"process {os.getpid()} on container-1"
    with pytest.raises(Exception, match="another update"):
        b.acquire()
    a.touch()
    old = time.time() - 3600
    os.utime(a.path, (old, old))
    assert b.holder() is None
    b.acquire()
    assert a.path.read_text().endswith("@laptop")
    b.release()
    assert not a.path.exists()


# ---------------------------------------------------------------------------------------------------------------- updater-net
def test_only_the_main_copys_server_is_attached_to_the_updaters_network_and_again_after_a_recreate(tmp_path):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    nets = {"updater-host": {"kelo_updater-net": {}, "kelo_updater-out": {}}, "srv1": {"kelo_agent": {}}}
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        ok = lambda out="", rc=0: subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")  # noqa: E731
        if cmd[:2] == ["docker", "inspect"]:
            return ok(json.dumps(nets.get(cmd[-1], {})))
        if cmd[:2] == ["docker", "compose"] and "ps" in cmd:
            assert "-p" not in cmd                                           # the main copy, never another project
            return ok("srv1\n")
        if cmd[:3] == ["docker", "network", "connect"]:
            nets[cmd[4]][cmd[3]] = {}
            return ok()
        raise AssertionError(cmd)
    svc = us.Service(tmp_path, token=TOKEN, run=run, host="updater-host", threaded=False)
    assert svc.attach_server() is True and calls[-1] == ["docker", "network", "connect", "kelo_updater-net", "srv1"]
    assert svc.attach_server() is False                                      # already there
    nets["srv1"] = {"kelo_agent": {}}                                        # the server was recreated by compose
    assert svc.attach_server() is True
    nets["updater-host"] = {"x_other": {}}
    assert svc.attach_server() is False                                      # no updater-net: nothing to do
