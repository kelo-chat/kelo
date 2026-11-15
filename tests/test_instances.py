import stat
import subprocess

import pytest

from assistant import instances as inst_mod
from assistant.cli import read_env
from assistant.instances import InstanceError, Instances


class FakeRun:
    """Records docker commands instead of running them."""

    def __init__(self, image_present=True, running=""):
        self.calls, self.image_present, self.running = [], image_present, running

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc = 1 if cmd[:3] == ["docker", "image", "inspect"] and not self.image_present else 0
        return subprocess.CompletedProcess(cmd, rc, stdout=self.running if "ps" in cmd else "", stderr="")


def make(tmp_path, **kw):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("BROWSER=chrome\nASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    msgs = []
    run = FakeRun(**kw)
    return Instances(tmp_path, run=run, port_free=lambda p: True, out=msgs.append), run, msgs


def test_each_copy_gets_its_own_secrets_ports_and_private_files(tmp_path):
    m, _, _ = make(tmp_path)
    a, b = m.create("alice"), m.create("bob", browser=False, domain="example.com")
    assert (a.server_port, a.viewer_port, b.server_port, b.viewer_port) == (8100, 6100, 8101, 6101)
    ta, tb = m.token("alice"), m.token("bob")
    ka, kb = (read_env(m.env_path(n), "ASSISTANT_SECRET_KEY") for n in ("alice", "bob"))
    assert len(ta) >= 32 and ta != tb and ka and kb and ka != kb and ta != "owner-token-owner-token-1234"
    assert stat.S_IMODE(m.env_path("alice").stat().st_mode) == 0o600 and stat.S_IMODE(m.env_path("alice").parent.stat().st_mode) == 0o700
    assert a.browser and not b.browser and b.public_url == "https://bob.example.com" and a.public_url == ""
    assert read_env(m.env_path("alice"), "BROWSER") == "chrome" and read_env(m.env_path("alice"), "ENV_FILE") == "instances/alice/env"
    text = m.env_path("alice").read_text()
    assert f"ASSISTANT_TOKEN={ta}" in text and "owner-token" not in text and tb not in text
    assert m.names() == ["alice", "bob"] and m.load("bob").browser is False


def test_the_number_of_copies_on_one_computer_is_not_capped(tmp_path):
    m, _, _ = make(tmp_path)
    made = [m.create(f"user{i:03d}") for i in range(150)]
    server = [i.server_port for i in made]
    viewer = [i.viewer_port for i in made]
    assert len(set(server)) == len(set(viewer)) == 150 and not set(server) & set(viewer)
    assert not ({8080, 6080} & (set(server) | set(viewer)))              # the main copy's ports are never reused
    assert max(server) < 65536 and len(m.names()) == 150


def test_server_and_viewer_ports_never_collide_even_if_their_ranges_overlap(tmp_path, monkeypatch):
    m, _, _ = make(tmp_path)
    monkeypatch.setattr(inst_mod, "VIEWER_PORT_BASE", inst_mod.SERVER_PORT_BASE)
    made = [m.create(f"u{i}x") for i in range(20)]
    ports = [p for i in made for p in (i.server_port, i.viewer_port)]
    assert len(set(ports)) == 40


def test_ports_skip_ones_in_use_by_other_programs_and_a_freed_port_is_reused(tmp_path):
    m, _, _ = make(tmp_path)
    m.port_free = lambda p: p != 8101
    assert m.create("aaa").server_port == 8100
    assert m.create("bbb").server_port == 8102        # 8101 is taken by something else
    m.delete("aaa")
    assert m.create("ccc").server_port == 8100


def test_running_out_of_ports_is_explained(tmp_path):
    m, _, _ = make(tmp_path)
    m.port_free = lambda p: False
    with pytest.raises(InstanceError, match="no free port"):
        m.create("alice")
    assert m.names() == [] and not (tmp_path / "instances" / "alice").exists()          # nothing half-made is left behind


@pytest.mark.parametrize("bad", ["", "A", "ab", "-a", "a_b", "a b", "../x", "x" * 40, "ends-", "1abc", "main"])
def test_bad_names_are_refused(tmp_path, bad):
    m, _, _ = make(tmp_path)
    with pytest.raises(InstanceError):
        m.create(bad)
    assert m.names() == []


def test_duplicates_and_bad_domains_are_refused_and_unknown_names_explain(tmp_path):
    m, _, _ = make(tmp_path)
    m.create("alice")
    for call in (lambda: m.create("alice"), lambda: m.create("bob", domain="not a domain"), lambda: m.load("ghost"), lambda: m.token("../x")):
        with pytest.raises(InstanceError):
            call()
    assert m.names() == ["alice"]


def test_the_main_copy_is_managed_the_same_way_without_a_project_name(tmp_path):
    m, run, _ = make(tmp_path)
    main = m.load()
    assert main.is_main and main.project is None and main.server_port == 8080 and main.viewer_port == 6080 and main.browser
    m.start()
    up = next(c for c in run.calls if "up" in c)
    assert up == ["docker", "compose", "-f", str(tmp_path / "compose.yml"), "up", "-d", "--no-build"]
    with pytest.raises(InstanceError, match="main copy"):
        m.delete("main")
    (tmp_path / ".env").unlink()
    with pytest.raises(InstanceError, match="assistant setup"):
        m.load()


def test_start_uses_the_copys_own_project_env_and_the_shared_image(tmp_path):
    m, run, _ = make(tmp_path)
    m.create("alice")
    m.create("bob", browser=False)
    m.start("alice")
    m.start("bob")
    a, b = [c for c in run.calls if "up" in c]
    assert a[:5] == ["docker", "compose", "-f", str(tmp_path / "compose.yml"), "-p"] and a[5] == "assistant-u-alice"
    assert a[a.index("--env-file") + 1] == str(tmp_path / "instances/alice/env") and a[-3:] == ["up", "-d", "--no-build"]
    assert b[5] == "assistant-u-bob" and b[-7:] == ["up", "-d", "--no-build", "--no-deps", "server", "workroom", "vpn"]   # no browser container for bob (the work room and the VPN sidecar are his too)
    assert not any("build" in c and "--no-build" not in c for c in run.calls)


def test_a_missing_image_is_built_once_before_the_first_start(tmp_path):
    m, run, _ = make(tmp_path, image_present=False)
    m.create("alice")
    m.start("alice")
    builds = [c for c in run.calls if c[-2:] == ["build", "server"]]
    assert len(builds) == 1 and run.calls.index(builds[0]) < next(i for i, c in enumerate(run.calls) if "up" in c)


def test_stop_keeps_everything_and_delete_takes_everything(tmp_path):
    m, run, _ = make(tmp_path)
    m.create("alice")
    m.stop("alice")
    assert run.calls[-1][-1] == "stop" and m.env_path("alice").is_file()
    m.delete("alice")
    downs = [i for i, c in enumerate(run.calls) if c[-3:] == ["down", "--volumes", "--remove-orphans"]]
    assert len(downs) == 1 and not (tmp_path / "instances" / "alice").exists()
    down = run.calls[downs[0]]
    assert down[down.index("--profile") + 1] == "*"          # profile-only networks (updater-net, updater-out) are removed too
    assert all(c[:3] == ["docker", "image", "ls"] for c in run.calls[downs[0] + 1:])   # then only its rollback tags are looked up
    assert not hasattr(m, "remove")


def test_running_state_comes_from_docker(tmp_path):
    m, _, _ = make(tmp_path, running="server\nbrowser\n")
    m.create("alice")
    assert m.running("alice") == ["server", "browser"]


def test_front_door_configs_list_every_copy(tmp_path):
    m, _, _ = make(tmp_path)
    m.create("alice")
    m.create("bob")
    assert m.routes("example.com") == ("ingress:\n  - hostname: alice.example.com\n    service: http://127.0.0.1:8100\n"
                                       "  - hostname: bob.example.com\n    service: http://127.0.0.1:8101\n  - service: http_status:404\n")
    caddy = m.routes("example.com", "caddy")
    assert "alice.example.com {\n\treverse_proxy 127.0.0.1:8100 {\n\t\tflush_interval -1" in caddy and "bob.example.com" in caddy
    for bad in (lambda: m.routes("nope"), lambda: m.routes("example.com", "nginx")):
        with pytest.raises(InstanceError):
            bad()
