"""The guided setup's host interface (copy_host.CopyHost): the Docker path keeps doing what it did, and the Apple container path
(the Kelo Server package on a Mac) starts, restarts and reads the copy through its LaunchAgent and never calls Docker.
No real Mac, no real container, no network: a scripted launchctl, a fake health check, time that only moves when something sleeps."""

from __future__ import annotations

import types
from pathlib import Path

import httpx
import pytest
from assistant.cli import read_env
from assistant.copy_host import CopyHost
from assistant.hosting.apple_container import copies
from assistant.hosting.apple_container.copies import (
    AppleCopyHost,
    AppleInstances,
    ServiceLog,
    detect,
)
from assistant.hosting.host import CommandResult
from assistant.hosting.macos.paths import MacPaths
from assistant.hosting.macos.services import LaunchdServices
from assistant.instances import InstanceError, Instances
from assistant.wizard import DockerCopyHost, Wizard
from hosting_fakes import FakeClock
from test_wizard import GB, FakeUI, Http

REPO = Path(__file__).resolve().parents[2]
LABEL = "gui/501/chat.kelo.server"


class Launchd:
    """A scripted `launchctl`, `container` and `sysctl`: the service has a process id that changes with each restart, and the server
    answers only some polls after a restart (`down_polls`)."""

    def __init__(self, *, loaded=True, pid=100, healthy=True, down_polls=2, container_ok=True, kickstart_code=0):
        self.loaded, self.pid, self.healthy_now = loaded, pid if loaded else None, healthy
        self.down_polls, self.container_ok, self.kickstart_code = down_polls, container_ok, kickstart_code
        self.calls: list[list[str]] = []
        self.polls_left = 0
        self.log: Path | None = None
        self.events: list[str] = []
        self.timeouts: set[float] = set()

    def run(self, argv, *, input_text=None):
        self.calls.append(list(argv))
        if argv[:2] == ["launchctl", "print"]:
            if not self.loaded:
                return CommandResult(113, "Could not find service")
            return CommandResult(0, "chat.kelo.server = {\n\tstate = running\n" + (f"\tpid = {self.pid}\n" if self.pid else "") + "}\n")
        if argv[:2] == ["launchctl", "bootstrap"]:
            self.loaded, self.pid, self.healthy_now = True, 200, False
            self.polls_left = self.down_polls
            self.events.append("bootstrap")
            return CommandResult(0)
        if argv[:2] == ["launchctl", "kickstart"]:
            self.events.append("kickstart")
            if self.kickstart_code:
                return CommandResult(self.kickstart_code, "no")
            self.pid = (self.pid or 0) + 1
            self.healthy_now, self.polls_left = False, self.down_polls
            if self.log:
                with self.log.open("a") as f:
                    f.write("kelo-server: started the parts again\n")
            return CommandResult(0)
        if argv[:2] == ["container", "--version"]:
            return CommandResult(0 if self.container_ok else 127, "container CLI version 1.7.1 (build: release)\n" if self.container_ok else "no")
        if argv[:3] == ["sysctl", "-n", "hw.memsize"]:
            return CommandResult(0, str(16 * GB) + "\n")
        raise AssertionError(f"unexpected command {argv}")

    def get(self, url, **kw):
        assert url.startswith("http://127.0.0.1:") and url.endswith("/healthz")
        self.timeouts.add(kw["timeout"])
        if self.polls_left > 0:
            self.polls_left -= 1
            if self.polls_left == 0:
                self.healthy_now = True
        return types.SimpleNamespace(status_code=200 if self.healthy_now else 503)


def make_host(tmp_path, launchd: Launchd, clock: FakeClock | None = None):
    clock = clock or FakeClock()
    root = tmp_path / "Library" / "Application Support" / "Kelo Server"
    root.mkdir(parents=True)
    (root / "compose.yml").write_text((REPO / "compose.yml").read_text(encoding="utf-8"), encoding="utf-8")
    paths = MacPaths.for_home(tmp_path)
    paths.logs_dir().mkdir(parents=True)
    launchd.log = paths.logs_dir() / "supervisor.log"
    service = LaunchdServices(launchd, paths, Path("/opt/kelo-server/bin/kelo-server"), plist_dir=tmp_path / "Library" / "LaunchAgents",
                              domain="gui/501")
    host = AppleCopyHost(root / ".env", runner=launchd, service=service, get=launchd.get, sleep=clock.sleep, clock=clock, home=tmp_path)
    return host, root


def test_both_hosts_have_the_interface():
    assert isinstance(DockerCopyHost(Path("/x/.env")), object)
    docker: CopyHost = DockerCopyHost(Path("/x/.env"))
    apple: CopyHost = AppleCopyHost(Path("/x/.env"), runner=Launchd())
    for host in (docker, apple):
        assert host.start_command and host.logs_command
        assert callable(host.check) and callable(host.manager) and callable(host.bring_up)
    assert "docker" in docker.start_command and "docker" not in apple.start_command and "docker" not in apple.logs_command


# ------------------------------------------------------------------------------------------------------------- the Docker path
def test_docker_host_check_and_capacity_are_what_the_wizard_always_asked(tmp_path):
    from test_wizard import Docker
    docker = Docker(cpus=6, mem=12 * GB)
    host = DockerCopyHost(tmp_path / ".env", run=docker.run, which=lambda n: "/usr/bin/docker", system="Linux")
    found = host.check()
    assert found.ok and found.facts == ["Docker 28.4.0", "Docker Compose 2.39.2"] and (found.cpus, found.ram) == (6, 12 * GB)
    mgr = host.manager(lambda s: None)
    assert isinstance(mgr, Instances) and mgr.runtime_name == "Docker" and mgr.host_capacity() == (6, 12 * GB)
    assert not DockerCopyHost(tmp_path / ".env", run=docker.run, which=lambda n: None, system="Linux").check().ok


# ------------------------------------------------------------------------------------------------------------- Apple container
def test_apple_check_reads_the_container_program_and_the_mac(tmp_path):
    host, _ = make_host(tmp_path, Launchd())
    found = host.check()
    assert found.ok and found.facts == ["container CLI version 1.7.1 (build: release)"] and found.ram == 16 * GB and found.cpus
    broken, _ = make_host(tmp_path / "b", Launchd(container_ok=False))
    result = broken.check()
    assert not result.ok and result.failure and result.failure[0] == "Apple container is not ready"


def test_apple_instances_never_call_docker_and_say_what_they_are(tmp_path):
    launchd = Launchd()
    host, root = make_host(tmp_path, launchd)
    (root / ".env").write_text("ASSISTANT_TOKEN=x\nSERVER_PORT=18090\n")
    mgr = host.manager(lambda s: None)
    assert isinstance(mgr, AppleInstances) and mgr.runtime_name == "Apple container"
    assert mgr.host_capacity()[1] == 16 * GB
    assert mgr.load().local_url == "http://127.0.0.1:18090"
    assert mgr.running() == ["server"] and mgr.ensure_image() is None
    with pytest.raises(InstanceError):
        mgr.compose(mgr.load(), "up")
    with pytest.raises(InstanceError):
        mgr.stop()
    assert not [c for c in launchd.calls if "docker" in c[0]]


def test_a_restart_waits_for_a_new_process_and_for_the_server(tmp_path):
    launchd = Launchd(pid=100, healthy=True)
    host, root = make_host(tmp_path, launchd)
    (root / ".env").write_text("SERVER_PORT=18090\n")
    inst = host.manager(lambda s: None).load()
    host.restart(inst)
    assert launchd.events == ["kickstart"] and launchd.pid == 101 and launchd.healthy_now
    assert ["launchctl", "kickstart", "-k", LABEL] in launchd.calls


def test_a_server_that_never_answers_is_an_error_not_a_hang(tmp_path):
    launchd = Launchd(down_polls=10**9)
    host, root = make_host(tmp_path, launchd)
    (root / ".env").write_text("SERVER_PORT=18090\n")
    with pytest.raises(Exception, match="did not answer"):
        host.restart(host.manager(lambda s: None).load())


def test_bring_up_waits_for_a_first_start_that_is_still_downloading_and_then_restarts_once(tmp_path):
    launchd = Launchd(pid=100, healthy=False, down_polls=3)
    launchd.polls_left = 4                       # still pulling images for four polls
    host, root = make_host(tmp_path, launchd)
    (root / ".env").write_text("SERVER_PORT=18090\n")
    launchd.log.write_text("old line\n")
    inst = host.manager(lambda s: None).load()
    ui = FakeUI()
    assert host.bring_up(ui, None, inst, rerun=False, release=False)
    assert launchd.events == ["kickstart"]       # never killed while it was downloading: the restart came after it answered
    assert ui.live_titles == ["Starting Kelo"]
    assert "log kelo-server: started the parts again" in ui.shown and not any("old line" in s for s in ui.shown)


def test_bring_up_on_a_mac_without_the_service_makes_the_folder_and_installs_it(tmp_path, monkeypatch):
    launchd = Launchd(loaded=False, down_polls=2)
    host, root = make_host(tmp_path, launchd)
    share = tmp_path / "share"
    share.mkdir()
    (share / "compose.yml").write_text((REPO / "compose.yml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("KELO_SHARE_DIR", str(share))
    inst = types.SimpleNamespace(local_url="http://127.0.0.1:18090", name="main")
    assert host.bring_up(FakeUI(), None, inst, rerun=False, release=False)
    assert launchd.events == ["bootstrap"]
    assert (tmp_path / "Library" / "LaunchAgents" / "chat.kelo.server.plist").is_file()
    assert len(read_env(root / ".env", "ASSISTANT_TOKEN")) >= 32


def test_bring_up_says_why_when_the_service_cannot_be_restarted(tmp_path):
    host, root = make_host(tmp_path, Launchd(kickstart_code=5))
    (root / ".env").write_text("SERVER_PORT=18090\n")
    ui = FakeUI()
    assert not host.bring_up(ui, None, host.manager(lambda s: None).load(), rerun=True, release=False)
    fail = next(s for s in ui.shown if s.startswith("fail "))
    assert "could not be started" in fail and "exit 5" in fail
    assert ui.live_titles == ["Updating and restarting Kelo"]


def test_bring_up_says_how_to_read_the_log_when_nothing_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(copies, "FIRST_START_SECONDS", 20.0)
    launchd = Launchd(pid=100, healthy=False, down_polls=10**9)
    launchd.polls_left = 10**9
    host, root = make_host(tmp_path, launchd)
    (root / ".env").write_text("SERVER_PORT=18090\n")
    ui = FakeUI()
    assert not host.bring_up(ui, None, host.manager(lambda s: None).load(), rerun=False, release=False)
    fail = next(s for s in ui.shown if s.startswith("fail "))
    assert "did not answer at http://127.0.0.1:18090" in fail and "kelo-server logs main server" in fail
    assert launchd.events == []                  # a service that never came up is not killed either


def test_the_log_tail_returns_only_what_was_written_after_it_was_made(tmp_path):
    log = tmp_path / "supervisor.log"
    log.write_text("before\n")
    tail = ServiceLog(log)
    assert tail.new_lines() == []
    with log.open("a") as f:
        f.write("one\n\ntwo\n")
    assert tail.new_lines() == ["one", "two"] and tail.new_lines() == []
    log.write_text("new file\n")                 # replaced by a shorter file
    assert tail.new_lines() == ["new file"]
    assert ServiceLog(tmp_path / "none.log").new_lines() == []


def test_detect_picks_apple_container_only_for_the_kelo_server_folder_of_a_mac_with_the_service_or_when_asked(tmp_path):
    ours = MacPaths.for_home(tmp_path).data_root() / ".env"
    other = tmp_path / "somewhere" / ".env"
    made = lambda p: AppleCopyHost(p, runner=Launchd())
    assert detect(ours, "auto", home=tmp_path, is_macos=lambda: True, make=made) is None          # no LaunchAgent: Docker
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "chat.kelo.server.plist").write_text("x")
    assert isinstance(detect(ours, "auto", home=tmp_path, is_macos=lambda: True, make=made), AppleCopyHost)
    assert detect(other, "auto", home=tmp_path, is_macos=lambda: True, make=made) is None         # a .env elsewhere is a Docker setup
    assert detect(ours, "auto", home=tmp_path, is_macos=lambda: False, make=made) is None          # not a Mac
    assert detect(ours, "docker", home=tmp_path, is_macos=lambda: True, make=made) is None
    assert isinstance(detect(other, "apple-container", home=tmp_path, is_macos=lambda: False, make=made), AppleCopyHost)


def test_the_whole_setup_on_apple_container_reaches_the_phone_qr_without_docker(tmp_path):
    """Size, start, a Kelo address and the phone link, all through the Apple container host."""
    launchd = Launchd(pid=100, healthy=True)
    host, root = make_host(tmp_path, launchd)
    http = Http()
    ui = FakeUI("", "1", "alice", True)          # the suggested size, the Kelo address, its name, a phone now
    wizard = Wizard(root / ".env", ui, host=host, get=launchd.get, post=http.post, free_disk=lambda p: 200 * GB, sleep=lambda s: None,
                    health_timeout=10)
    assert wizard() == 0
    env = root / ".env"
    assert len(read_env(env, "ASSISTANT_TOKEN")) >= 32 and read_env(env, "SERVER_MEMORY") == str(1 * GB)
    assert read_env(env, "CONNECT_URL") == "https://alice.kelo.chat" and "kelo" in read_env(env, "COMPOSE_PROFILES").split(",")
    assert launchd.events == ["kickstart", "kickstart"]       # once for the size, once for the address
    assert launchd.timeouts == {20.0}                         # one request through the Mac's port forwarding takes about 6 s
    assert ui.qr_links == ["https://alice.kelo.chat/pair#one-time"]
    assert not any(c[0] in ("docker", "compose") for c in launchd.calls)
    assert [s.split(" ", 2)[1] for s in ui.shown if s.startswith("step ")] == ["1", "2", "3", "4"]
    assert read_env(env, "ASSISTANT_TOKEN") not in "\n".join(ui.shown + ui.asked)


def test_the_setup_on_apple_container_stops_cleanly_when_the_container_program_is_missing(tmp_path):
    host, root = make_host(tmp_path, Launchd(container_ok=False))
    ui = FakeUI()
    assert Wizard(root / ".env", ui, host=host)() == 1
    fail = next(s for s in ui.shown if s.startswith("fail "))
    assert "Apple container is not ready" in fail and "Docker" not in fail and ui.asked == []


def test_the_setup_page_runs_the_same_wizard_for_either_host(tmp_path, monkeypatch):
    """run_web hands the host to the wizard; the page code itself knows nothing about Docker or Apple container."""
    from assistant import setup_web, wizard
    seen = {}

    class Spy(wizard.Wizard):
        def __call__(self):
            seen["host"] = self.host
            return 0

    def serve(run_wizard, *, lang, **kw):
        code = run_wizard(FakeUI())
        return "http://127.0.0.1:1/#t=x", types.SimpleNamespace(wait=lambda: code, stop=lambda: None)

    monkeypatch.setattr(wizard, "Wizard", Spy)
    monkeypatch.setattr(setup_web, "serve", serve)
    marker = object()
    assert setup_web.run_web(tmp_path / ".env", open_browser=False, page_file=True, out=lambda s: None, host=marker) == 0
    assert seen["host"] is marker


def test_http_errors_from_the_health_check_count_as_not_ready(tmp_path):
    def broken(url, **kw):
        raise httpx.ConnectError("refused")

    host, _ = make_host(tmp_path, Launchd())
    host._get = broken
    assert not host.healthy(types.SimpleNamespace(local_url="http://127.0.0.1:1"))
