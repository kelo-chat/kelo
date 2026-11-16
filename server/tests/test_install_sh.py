"""Tests for scripts/install.sh (reinstall choices, --add, memory warning, validation)."""

import os
from pathlib import Path
import pty
import select
import subprocess
import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def fake_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    kelo_home = tmp_path / "kelo"
    kelo_home.mkdir(parents=True)
    log_file = tmp_path / "calls.log"

    # Fake docker
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "version" ]; then echo "27.0.0"; exit 0; fi\n'
        'if [ "$1" = "compose" ] && [ "$2" = "version" ]; then echo "v2.29.0"; exit 0; fi\n'
        'if [ "$1" = "info" ]; then echo "Server Version: 27.0.0"; exit 0; fi\n'
        "exit 0\n"
    )
    docker.chmod(0o755)

    # Fake uv
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "uv 0.5.0"; exit 0; fi\n'
        'if [ "$1" = "sync" ]; then exit 0; fi\n'
        'if [ "$1" = "run" ]; then\n'
        "  shift\n"
        "  while [ $# -gt 0 ]; do\n"
        '    case "$1" in\n'
        "      --*) shift ;;\n"
        "      *) break ;;\n"
        "    esac\n"
        "  done\n"
        f'  echo "$*" >> "{log_file}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    uv.chmod(0o755)

    # Fake kelo launcher
    kelo_cmd = bin_dir / "kelo"
    kelo_cmd.write_text(
        "#!/bin/sh\n"
        f'echo "kelo $*" >> "{log_file}"\n'
        "exit 0\n"
    )
    kelo_cmd.chmod(0o755)

    # Set up existing Kelo installation
    (kelo_home / "compose.yml").write_text("services: {}\n")
    (kelo_home / ".env").write_text("ASSISTANT_TOKEN=fake-token\n")
    (kelo_home / "instances" / "alice").mkdir(parents=True)
    (kelo_home / "instances" / "alice" / "env").write_text("SERVER_PORT=8101\n")

    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["KELO_HOME"] = str(kelo_home)
    env["KELO_BIN_DIR"] = str(bin_dir)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["KELO_TEST_FREE_MEM_MB"] = "8192"
    env["KELO_AUTO_UPDATE"] = "0"
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)

    return {
        "root": tmp_path,
        "kelo_home": kelo_home,
        "bin_dir": bin_dir,
        "log_file": log_file,
        "env": env,
        "install_sh": _repo_root() / "scripts" / "install.sh",
    }


def _run_with_pty(script: Path, args: list[str], env: dict[str, str], input_text: str | None = None, timeout: float = 8.0) -> tuple[int, str]:
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", str(script)] + args,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        env=env,
    )
    os.close(slave)

    if input_text:
        os.write(master, input_text.encode("utf-8"))

    output = b""
    start_time = os.times().elapsed
    while os.times().elapsed - start_time < timeout:
        r, _, _ = select.select([master], [], [], 0.5)
        if r:
            try:
                chunk = os.read(master, 2048)
                if not chunk:
                    break
                output += chunk
            except OSError:
                break
        if proc.poll() is not None:
            # Drain remaining output
            while True:
                r, _, _ = select.select([master], [], [], 0.1)
                if not r:
                    break
                try:
                    chunk = os.read(master, 2048)
                    if not chunk:
                        break
                    output += chunk
                except OSError:
                    break
            break

    os.close(master)
    proc.wait(timeout=5)
    return proc.returncode, output.decode("utf-8", errors="replace")


def test_help_text(fake_env):
    res = subprocess.run(
        ["bash", str(fake_env["install_sh"]), "--help"],
        capture_output=True,
        text=True,
        env=fake_env["env"],
    )
    assert res.returncode == 0
    assert "--add NAME" in res.stdout


def test_yes_flag_updates_without_asking(fake_env):
    res = subprocess.run(
        ["bash", str(fake_env["install_sh"]), "--yes"],
        capture_output=True,
        text=True,
        env=fake_env["env"],
    )
    assert res.returncode == 0
    # Should run update (assistant setup)
    assert not fake_env["log_file"].exists() or "instance create" not in fake_env["log_file"].read_text()
    assert "Kelo is already installed here" not in res.stdout


def test_non_interactive_updates_without_asking(fake_env):
    res = subprocess.run(
        ["bash", str(fake_env["install_sh"])],
        capture_output=True,
        text=True,
        env=fake_env["env"],
        preexec_fn=os.setsid,
    )
    assert res.returncode == 0
    assert "Kelo is already installed here" not in res.stdout


def test_pty_interactive_answer_1_updates(fake_env):
    rc, out = _run_with_pty(
        fake_env["install_sh"],
        [],
        fake_env["env"],
        input_text="1\n",
    )
    assert rc == 0
    assert "Kelo is already installed here. What would you like to do?" in out
    assert "1) Update it" in out
    assert "2) Add another Kelo server (for another person or a test)" in out
    # Did not create an instance
    if fake_env["log_file"].exists():
        assert "instance create" not in fake_env["log_file"].read_text()


def test_pty_interactive_answer_2_creates_instance(fake_env):
    rc, out = _run_with_pty(
        fake_env["install_sh"],
        [],
        fake_env["env"],
        input_text="2\nbob\n",
    )
    assert rc == 0
    assert "Kelo is already installed here. What would you like to do?" in out
    assert "Add another Kelo server" in out
    assert "Free memory: 8.0 GB (recommended for another copy: 4 GB)" in out
    assert fake_env["log_file"].exists()
    assert "kelo instance create bob" in fake_env["log_file"].read_text()


def test_flag_add_creates_instance_without_asking(fake_env):
    res = subprocess.run(
        ["bash", str(fake_env["install_sh"]), "--add", "charlie"],
        capture_output=True,
        text=True,
        env=fake_env["env"],
    )
    assert res.returncode == 0
    assert "Kelo is already installed here" not in res.stdout
    assert "Add another Kelo server" in res.stdout
    assert fake_env["log_file"].exists()
    assert "kelo instance create charlie" in fake_env["log_file"].read_text()


def test_flag_add_bad_name(fake_env):
    res = subprocess.run(
        ["bash", str(fake_env["install_sh"]), "--add", "x"],
        capture_output=True,
        text=True,
        env=fake_env["env"],
    )
    assert res.returncode != 0
    assert "invalid" in res.stderr.lower() or "invalid" in res.stdout.lower()


def test_flag_add_duplicate_name_alice(fake_env):
    res = subprocess.run(
        ["bash", str(fake_env["install_sh"]), "--add", "alice"],
        capture_output=True,
        text=True,
        env=fake_env["env"],
    )
    assert res.returncode != 0
    assert "already exists" in res.stderr.lower() or "already exists" in res.stdout.lower()


def test_flag_add_main_name(fake_env):
    res = subprocess.run(
        ["bash", str(fake_env["install_sh"]), "--add", "main"],
        capture_output=True,
        text=True,
        env=fake_env["env"],
    )
    assert res.returncode != 0
    assert "already exists" in res.stderr.lower() or "already exists" in res.stdout.lower()


def test_pty_interactive_bad_name_then_valid(fake_env):
    rc, out = _run_with_pty(
        fake_env["install_sh"],
        [],
        fake_env["env"],
        input_text="2\nbad_name!\nalice\ndave\n",
    )
    assert rc == 0
    assert "must be 3-20 lowercase letters" in out
    assert "already exists" in out
    assert fake_env["log_file"].exists()
    assert "kelo instance create dave" in fake_env["log_file"].read_text()


def test_low_memory_warning(fake_env):
    fake_env["env"]["KELO_TEST_FREE_MEM_MB"] = "2048"
    res = subprocess.run(
        ["bash", str(fake_env["install_sh"]), "--add", "lowmem"],
        capture_output=True,
        text=True,
        env=fake_env["env"],
    )
    assert res.returncode == 0
    assert "below the recommended 4 GB" in res.stdout


def test_korean_language(fake_env):
    fake_env["env"]["KELO_LANG"] = "ko"
    fake_env["env"]["KELO_TEST_FREE_MEM_MB"] = "2048"
    rc, out = _run_with_pty(
        fake_env["install_sh"],
        [],
        fake_env["env"],
        input_text="2\nmin\n",
    )
    assert rc == 0
    assert "이 컴퓨터에 Kelo가 이미 있어요. 무엇을 할까요?" in out
    assert "1) 업데이트하기" in out
    assert "2) Kelo 서버 하나 더 만들기 (다른 사람용이나 테스트용)" in out
    assert "권장 사양(4 GB)보다 적습니다" in out
