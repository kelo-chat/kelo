import stat

import pytest

from assistant.cli import choose_browser, ensure_token, setup, write_env


def answers(*values):
    it = iter(values)
    return lambda prompt: next(it)


def test_default_is_chromium():
    assert choose_browser(answers(""), lambda _: None) == "chromium"
    assert choose_browser(answers("1"), lambda _: None) == "chromium"


def test_chrome_requires_explicit_confirmation():
    shown = []
    assert choose_browser(answers("2", "y"), shown.append) == "chrome"
    assert any("proprietary" in s for s in shown)
    assert choose_browser(answers("2", ""), lambda _: None) == "chromium"      # Enter = decline
    assert choose_browser(answers("2", "no"), lambda _: None) == "chromium"


def test_invalid_input_reprompts():
    shown = []
    assert choose_browser(answers("x", "9", "1"), shown.append) == "chromium"
    assert shown.count("Please enter 1 or 2.") == 2


def test_write_env_preserves_other_lines(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# my settings\nOPENROUTER_API_KEY=abc\nBROWSER=chrome\n")
    write_env(f, {"BROWSER": "chromium", "NEW": "1"})
    assert f.read_text() == "# my settings\nOPENROUTER_API_KEY=abc\nBROWSER=chromium\nNEW=1\n"


def test_setup_noninteractive_defaults_and_flag(tmp_path):
    f = tmp_path / ".env"
    assert setup(f, interactive=False, out=lambda _: None) == "chromium"
    assert "BROWSER=chromium" in f.read_text()
    assert setup(f, "chrome", out=lambda _: None) == "chrome"
    assert f.read_text().count("BROWSER=") == 1


def test_setup_creates_private_env_with_a_strong_token_once(tmp_path):
    f = tmp_path / ".env"
    setup(f, "chromium", out=lambda _: None)
    text = f.read_text()
    token = next(l.split("=", 1)[1] for l in text.splitlines() if l.startswith("ASSISTANT_TOKEN="))
    assert len(token) >= 32 and stat.S_IMODE(f.stat().st_mode) == 0o600
    setup(f, "chromium", out=lambda _: None)          # rerun must not rotate the token
    assert f.read_text().count("ASSISTANT_TOKEN=") == 1 and token in f.read_text()
    assert ensure_token(f) is False


def test_setup_creates_secret_key_once_and_never_rotates(tmp_path):
    from assistant.cli import ensure_secret_key
    f = tmp_path / ".env"
    setup(f, "chromium", out=lambda _: None)
    first = [l for l in f.read_text().splitlines() if l.startswith("ASSISTANT_SECRET_KEY=")]
    assert len(first) == 1 and len(first[0].split("=", 1)[1]) == 44
    assert ensure_secret_key(f) is False
    setup(f, "chromium", out=lambda _: None)
    assert [l for l in f.read_text().splitlines() if l.startswith("ASSISTANT_SECRET_KEY=")] == first



def _docker(cmd, **kw):
    import subprocess
    return subprocess.CompletedProcess(cmd, 0, stdout="4 8589934592" if cmd[:2] == ["docker", "info"] else "", stderr="")


def _answers(*a):
    it = iter(a)
    return lambda prompt: next(it)


def test_setup_asks_which_clis_and_saves_compose_profiles(tmp_path):
    from assistant.cli import read_env
    f = tmp_path / ".env"
    logs = []
    setup(f, ask=_answers("", "y", "yes", ""), out=logs.append, run=_docker)  # browser default; claude yes; agy yes; size: recommended
    assert read_env(f, "COMPOSE_PROFILES") == "claude,agy"
    assert len(read_env(f, "CLI_RUNNER_TOKEN")) >= 16
    assert any("exec -it claude-cli claude" in l for l in logs) and any("exec -it agy-cli agy" in l for l in logs)
    g = tmp_path / "g.env"
    setup(g, ask=_answers("", "n", "y", "3"), out=lambda _: None, run=_docker)  # any mix: agy only; no limits
    assert read_env(g, "COMPOSE_PROFILES") == "agy"
    h = tmp_path / "h.env"
    setup(h, ask=_answers("", "", "", "3"), out=lambda _: None, run=_docker)  # none
    assert read_env(h, "COMPOSE_PROFILES") == ""


def test_setup_rerun_keeps_choice_and_never_rotates_runner_token(tmp_path):
    from assistant.cli import read_env
    f = tmp_path / ".env"
    setup(f, "chromium", out=lambda _: None, clis=["claude"])
    token = read_env(f, "CLI_RUNNER_TOKEN")
    setup(f, "chromium", out=lambda _: None)                      # no clis given, no questions: unchanged
    assert read_env(f, "COMPOSE_PROFILES") == "claude" and read_env(f, "CLI_RUNNER_TOKEN") == token
    setup(f, "chromium", out=lambda _: None, interactive=False)   # --yes rerun: unchanged too
    assert read_env(f, "COMPOSE_PROFILES") == "claude"
    with pytest.raises(SystemExit, match="unknown CLI"):
        setup(f, "chromium", out=lambda _: None, clis=["codex"])
