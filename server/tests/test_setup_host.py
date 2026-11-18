"""The guided setup's Docker host (copy_host.CopyHost) and the setup page, which hands its host to the wizard.
No real Docker, no network: a scripted docker command and a fake page."""

from __future__ import annotations

import types
from pathlib import Path

from assistant.copy_host import CopyHost
from assistant.instances import Instances
from assistant.wizard import DockerCopyHost
from test_wizard import GB, FakeUI


def test_the_docker_host_has_the_interface():
    docker: CopyHost = DockerCopyHost(Path("/x/.env"))
    assert docker.start_command and docker.logs_command and "docker" in docker.start_command
    assert callable(docker.check) and callable(docker.manager) and callable(docker.bring_up)


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


def test_the_setup_page_hands_the_given_host_to_the_wizard(tmp_path, monkeypatch):
    """run_web hands the host to the wizard; the page code itself knows nothing about the runtime."""
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
