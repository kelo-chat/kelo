"""`assistant enable|disable oauth` and `assistant status`: the unofficial sign-in providers are off unless the owner turns them on."""

from types import SimpleNamespace as ns

from assistant.cli import main, read_env
from assistant.manage import OAUTH_KEY, run_command

from test_instances import make


def oauth(cmd, instance=None, no_restart=False):
    return ns(cmd=cmd, feature="oauth", instance=instance, no_restart=no_restart)


def test_enable_saves_the_line_recreates_the_server_and_starts_the_cli_containers(tmp_path):
    m, run, msgs = make(tmp_path, running="server\nbrowser\n")
    assert run_command(oauth("enable"), m) == 0
    assert read_env(tmp_path / ".env", OAUTH_KEY) == "1"
    compose = ["docker", "compose", "-f", str(tmp_path / "compose.yml")]
    ups = [c for c in run.calls if "up" in c]
    assert ups == [[*compose, "up", "-d", "--no-deps", "--no-build", "server"],
                   # the phone signs Claude / agy in through their containers, which the server cannot start itself
                   [*compose, "--profile", "claude", "--profile", "agy", "up", "-d", "--no-deps", "claude-cli", "agy-cli"]]
    assert read_env(tmp_path / ".env", "COMPOSE_PROFILES").split(",") == ["claude", "agy"]
    assert len(read_env(tmp_path / ".env", "CLI_RUNNER_TOKEN")) >= 16
    text = "\n".join(msgs)
    assert read_env(tmp_path / ".env", "CLI_RUNNER_TOKEN") not in text
    assert "On for the main copy" in text and "assistant disable oauth" in text and "ASSISTANT_TOKEN" not in text
    assert "owner-token" not in text                                                            # never prints a secret
    run.calls.clear()
    assert run_command(oauth("enable"), m) == 0 and "already on" in msgs[-4] and not any("up" in c for c in run.calls)


def test_disable_for_an_extra_copy_that_is_stopped_only_saves(tmp_path):
    m, run, msgs = make(tmp_path)
    m.create("alice")
    assert read_env(m.env_path("alice"), OAUTH_KEY) == ""                                       # a new copy starts with it off
    assert run_command(oauth("enable", "alice"), m) == 0 and read_env(m.env_path("alice"), OAUTH_KEY) == "1"
    assert run_command(oauth("disable", "alice"), m) == 0 and read_env(m.env_path("alice"), OAUTH_KEY) == "0"
    assert read_env(tmp_path / ".env", OAUTH_KEY) == ""                                         # the main copy is untouched
    text = "\n".join(msgs)
    assert "next time this copy is started" in text and "keeps working" in text and "--instance alice" in text
    assert not any("up" in c for c in run.calls)
    assert run_command(oauth("enable", "ghost"), m) == 1


def test_status_shows_the_setting_per_copy(tmp_path):
    m, _, msgs = make(tmp_path)
    m.create("alice")
    run_command(oauth("enable", "alice", no_restart=True), m)
    msgs.clear()
    assert run_command(ns(cmd="status", instance=None), m) == 0
    lines = [ln for ln in msgs if "subscription sign-ins" in ln]
    assert len(lines) == 2 and ": off" in lines[0] and ": on" in lines[1] and "--instance alice" in lines[1]


def test_the_command_line_parses(tmp_path, monkeypatch, capsys):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=owner-token-owner-token-1234\n")
    monkeypatch.chdir(tmp_path)
    import assistant.manage as manage
    from assistant.instances import Instances
    from test_instances import FakeRun
    run = FakeRun()
    monkeypatch.setattr(manage, "Instances", lambda root: Instances(root, run=run, port_free=lambda p: True))   # no real docker
    assert main(["enable", "oauth"]) == 0 and read_env(tmp_path / ".env", OAUTH_KEY) == "1"
    assert main(["status"]) == 0 and "subscription sign-ins (unofficial providers): on" in capsys.readouterr().out
    assert main(["disable", "oauth", "--no-restart"]) == 0 and read_env(tmp_path / ".env", OAUTH_KEY) == "0"
    assert run.calls and all(c[:2] == ["docker", "compose"] for c in run.calls)


def test_the_runner_token_exists_before_the_server_restarts(tmp_path):
    """The server reads CLI_RUNNER_TOKEN when it starts; made after its restart it never reaches it (sign-in answered 502)."""
    m, run, msgs = make(tmp_path, running="server\nbrowser\n")
    seen = []
    real = m.run

    def spy(args, **kw):
        if "server" in args and "up" in args:
            seen.append(read_env(tmp_path / ".env", "CLI_RUNNER_TOKEN"))
        return real(args, **kw)

    m.run = spy
    assert run_command(oauth("enable"), m) == 0
    assert seen and all(len(t) >= 16 for t in seen)
