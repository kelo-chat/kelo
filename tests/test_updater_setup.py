"""`assistant updater setup | off | status` (updater_setup.py): what it writes into .env, and that the secret is never shown."""

import json
import subprocess

from assistant import release as rel
from assistant import updater_setup as su
from assistant.cli import read_env, setup as cli_setup
from assistant.instances import Instances

D = {c: "sha256:" + f"{i:x}" * 64 for i, c in enumerate(rel.COMPONENTS, start=1)}


class Docker:
    def __init__(self, have_image=False):
        self.calls, self.have, self.tags = [], have_image, {}

    def __call__(self, cmd, cwd=None, capture_output=False, text=False, env=None, check=False, **kw):
        self.calls.append(cmd)
        ok = lambda out="", rc=0: subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")  # noqa: E731
        if cmd[:3] == ["docker", "image", "inspect"] and "{{json .RepoDigests}}" in cmd:
            return ok(json.dumps([cmd[-1]]))
        if cmd[:3] == ["docker", "image", "inspect"] and "{{json .Config.Labels}}" in cmd:
            return ok(json.dumps({rel.VERSION_LABEL: "0.5.0"}))
        if cmd[:3] == ["docker", "image", "inspect"]:
            return ok(rc=0 if self.have else 1)
        if cmd[:2] == ["docker", "run"] and "stat" in cmd:
            return ok("991\n")
        if cmd[:2] == ["docker", "run"] or cmd[0] == "cosign":
            return ok(json.dumps([{"critical": {"image": {"docker-manifest-digest": cmd[-1].split("@")[1]}}}]))
        if cmd[:2] == ["docker", "pull"]:
            return ok()
        if cmd[:2] == ["docker", "tag"]:
            self.tags[cmd[3]] = cmd[2]
            return ok()
        if cmd[:2] == ["docker", "compose"]:
            if "build" in cmd:
                self.have = True
            return ok("server\n" if "ps" in cmd else "")
        raise AssertionError(cmd)


def test_setup_writes_the_settings_builds_once_and_never_prints_the_secret(tmp_path):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    env = tmp_path / ".env"
    env.write_text("ASSISTANT_TOKEN=owner-token-owner-token-1234\nCOMPOSE_PROFILES=claude,kelo\n")
    out, d = [], Docker()
    m = Instances(tmp_path, run=d, out=out.append)
    assert su.setup(m, auto=True, registry="GHCR.io/Kelo-Chat") == 0
    token = read_env(env, "UPDATER_TOKEN")
    assert len(token) >= 32 and token not in "\n".join(out) and token not in json.dumps(d.calls)
    assert read_env(env, "KELO_ROOT") == str(tmp_path.resolve()) and read_env(env, "DOCKER_GID") == "991"
    assert read_env(env, "KELO_REGISTRY") == "ghcr.io/kelo-chat" and read_env(env, "KELO_AUTO_UPDATE") == "1"
    assert read_env(env, "COMPOSE_PROFILES") == "claude,kelo,updater" and read_env(env, "KELO_UID").isdigit()
    assert any("build" in c and "updater" in c for c in d.calls)
    up = next(c for c in d.calls if "up" in c)
    assert up[-2:] == ["updater", "server"] and "--no-deps" in up
    assert su.setup(m, auto=None) == 0 and read_env(env, "UPDATER_TOKEN") == token          # made once
    assert read_env(env, "KELO_AUTO_UPDATE") == "1" and read_env(env, "COMPOSE_PROFILES").count("updater") == 1
    assert su.off(m) == 0 and read_env(env, "COMPOSE_PROFILES") == "claude,kelo"
    # `assistant setup` choosing CLIs keeps the updater profile
    su.setup(m, auto=False)
    cli_setup(env, "chromium", interactive=False, clis=["agy"], out=lambda s: None, resources=None)
    assert read_env(env, "COMPOSE_PROFILES") == "kelo,updater,agy"


def test_windows_paths_become_the_docker_vms_path(monkeypatch):
    from pathlib import PureWindowsPath
    monkeypatch.setattr(su.os, "name", "nt")
    assert su.container_path(PureWindowsPath(r"C:\Users\me\kelo")) == ("/run/desktop/mnt/host/c/Users/me/kelo", r"C:\Users\me\kelo")


def test_a_first_install_from_the_release_checks_then_tags_only_kelos_images(tmp_path, monkeypatch):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    monkeypatch.setattr(rel, "fetch", lambda url, registry: rel.parse(rel.build_manifest("0.5.0", D)))
    d = Docker()
    m = Instances(tmp_path, run=d, out=lambda s: None)
    assert su.pull_release(m) == "0.5.0"
    assert d.tags == {"assistant:local": f"ghcr.io/kelo-chat/server@{D['server']}",
                      "assistant-workroom:local": f"ghcr.io/kelo-chat/workroom@{D['workroom']}",
                      "assistant-updater:local": f"ghcr.io/kelo-chat/updater@{D['updater']}"}
    assert not [c for c in d.calls if any("cli" in a for a in c if isinstance(a, str) and "kelo-chat" in a)]
