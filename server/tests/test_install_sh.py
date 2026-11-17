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


@pytest.mark.parametrize("name,wrapped", [("kelo.tar.gz", True), ("kelo.tgz", False)])
def test_a_local_tar_gz_is_unpacked_into_a_temp_folder_and_installed(fake_env, name, wrapped):
    """KELO_SOURCE=/path/archive.tar.gz (the 0.10.1 installer refused it: 'does not look like a Kelo folder (no compose.yml)')."""
    import tarfile

    root = fake_env["root"]
    (fake_env["kelo_home"] / "compose.yml").unlink()
    (fake_env["kelo_home"] / ".env").unlink()
    stage = root / "stage" / ("kelo-1.2.3" if wrapped else "")
    stage.mkdir(parents=True)
    (stage / "compose.yml").write_text("services: {}\n")
    (stage / "marker.txt").write_text("from-the-archive\n")
    archive = root / name
    with tarfile.open(archive, "w:gz") as tar:
        if wrapped:
            tar.add(root / "stage" / "kelo-1.2.3", arcname="kelo-1.2.3")
        else:
            for f in ("compose.yml", "marker.txt"):
                tar.add(stage / f, arcname=f)
    env = {**fake_env["env"], "KELO_SOURCE": str(archive)}
    res = subprocess.run(["bash", str(fake_env["install_sh"]), "--yes", "--no-start"], capture_output=True, text=True, env=env, timeout=60,
                         stdin=subprocess.DEVNULL)
    assert "no compose.yml" not in res.stdout + res.stderr and "does not look like" not in res.stdout + res.stderr
    assert (fake_env["kelo_home"] / "marker.txt").read_text() == "from-the-archive\n"
    assert (fake_env["kelo_home"] / "compose.yml").is_file()


def test_a_local_archive_that_is_not_kelo_is_refused(fake_env):
    import tarfile

    root = fake_env["root"]
    (fake_env["kelo_home"] / "compose.yml").unlink()
    (root / "x").mkdir()
    (root / "x" / "readme.txt").write_text("hi")
    archive = root / "other.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root / "x" / "readme.txt", arcname="readme.txt")
    env = {**fake_env["env"], "KELO_SOURCE": str(archive)}
    res = subprocess.run(["bash", str(fake_env["install_sh"]), "--yes", "--no-start"], capture_output=True, text=True, env=env, timeout=60,
                         stdin=subprocess.DEVNULL)
    assert res.returncode != 0 and "does not look like Kelo" in res.stdout + res.stderr


# ------------------------------------------------------------------------------------------------------------- --version
import http.server
import io
import tarfile
import threading


def _release_archive(version: str, marker: str = "from-the-release") -> bytes:
    """A tag archive as GitHub serves it: one top folder with compose.yml, version.json and a marker file."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in (("compose.yml", "services: {}\n"), ("version.json", '{"version": "%s", "build": 1}\n' % version),
                           ("marker.txt", marker + "\n")):
            data = text.encode()
            info = tarfile.TarInfo(f"kelo-server-release-{version}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def releases(request):
    """A local stand-in for github.com/kelo-chat/kelo-server: tag archives of the given versions (a dict version -> the version.json
    inside), 404 for every other path. `seen` lists the requests."""
    served = {"0.10.2": "0.10.2", "0.10.5": "0.10.5", "0.11.0": "0.11.0", "0.9.0": "0.9.0", "0.8.0": "0.9.9"}
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self, head: bool):
            seen.append(f"{'HEAD' if head else 'GET'} {self.path}")
            prefix, suffix = "/archive/refs/tags/release-", ".tar.gz"
            version = self.path[len(prefix):-len(suffix)] if self.path.startswith(prefix) and self.path.endswith(suffix) else None
            if version not in served:
                self.send_error(404)
                return
            body = _release_archive(served[version])
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self):
            self._answer(False)

        def do_HEAD(self):
            self._answer(True)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{server.server_address[1]}", "seen": seen}
    server.shutdown()
    server.server_close()


def _fresh(fake_env, base):
    """Kelo is not installed yet; the installer reads its releases from the local stand-in."""
    (fake_env["kelo_home"] / "compose.yml").unlink()
    (fake_env["kelo_home"] / ".env").unlink()
    return {**fake_env["env"], "KELO_RELEASES_BASE": base}


def _run(fake_env, args, env):
    return subprocess.run(["bash", str(fake_env["install_sh"]), *args], capture_output=True, text=True, env=env, timeout=60,
                          stdin=subprocess.DEVNULL)


def _installed(fake_env, version):
    (fake_env["kelo_home"] / "version.json").write_text('{"version": "%s", "build": 1, "min_app": "0.9.0"}\n' % version)


def test_help_lists_the_version_options(fake_env):
    res = subprocess.run(["bash", str(fake_env["install_sh"]), "--help"], capture_output=True, text=True, env=fake_env["env"])
    assert "--version X.Y.Z" in res.stdout and "--allow-downgrade" in res.stdout


@pytest.mark.parametrize("how", ["flag", "equals", "env", "leading-v"])
def test_version_installs_that_release_from_its_tag(fake_env, releases, how):
    env = _fresh(fake_env, releases["base"])
    args = {"flag": ["--version", "0.10.2"], "equals": ["--version=0.10.2"], "env": [], "leading-v": ["--version", "v0.10.2"]}[how]
    if how == "env":
        env["KELO_VERSION"] = "0.10.2"
    res = _run(fake_env, ["--yes", "--no-start", *args], env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (fake_env["kelo_home"] / "marker.txt").read_text() == "from-the-release\n"
    assert (fake_env["kelo_home"] / "version.json").read_text().count("0.10.2") == 1
    assert "/archive/refs/tags/release-0.10.2.tar.gz" in " ".join(releases["seen"])
    assert not any("heads/main" in s for s in releases["seen"])                      # not the newest release


def test_without_version_the_newest_release_is_still_the_default(fake_env, releases):
    env = _fresh(fake_env, releases["base"])
    res = _run(fake_env, ["--yes", "--no-start"], env)
    assert any("/archive/refs/heads/main.tar.gz" in s for s in releases["seen"]) and res.returncode != 0   # the stand-in has no main
    assert not any("release-" in s for s in releases["seen"])


def test_an_unknown_version_is_one_clear_line_and_changes_nothing(fake_env, releases):
    env = _fresh(fake_env, releases["base"])
    res = _run(fake_env, ["--yes", "--no-start", "--version", "9.9.9"], env)
    assert res.returncode != 0
    lines = [ln for ln in (res.stdout + res.stderr).splitlines() if "9.9.9" in ln and "not found" in ln]
    assert len(lines) == 1 and f"{releases['base']}/releases" in lines[0]
    assert not (fake_env["kelo_home"] / "compose.yml").exists() and not (fake_env["kelo_home"] / "marker.txt").exists()
    assert not fake_env["log_file"].exists()                                         # no setup or updater command ran
    assert not any(s.startswith("GET") for s in releases["seen"])                    # nothing was downloaded


@pytest.mark.parametrize("bad", ["latest", "1.2", "1.2.3;rm", "../x"])
def test_a_version_that_is_not_a_release_number_is_refused_without_the_network(fake_env, releases, bad):
    env = _fresh(fake_env, releases["base"])
    res = _run(fake_env, ["--yes", "--no-start", f"--version={bad}"], env)
    assert res.returncode != 0 and "is not a release number" in res.stdout + res.stderr
    assert releases["seen"] == [] and not (fake_env["kelo_home"] / "compose.yml").exists()


def test_a_version_option_without_a_value_is_refused(fake_env):
    res = _run(fake_env, ["--version"], fake_env["env"])
    assert res.returncode != 0 and "--version needs a release number" in res.stderr


def test_an_update_to_an_older_version_is_refused_unless_allowed(fake_env, releases):
    _installed(fake_env, "0.10.5")
    env = {**fake_env["env"], "KELO_RELEASES_BASE": releases["base"]}
    before = (fake_env["kelo_home"] / "compose.yml").read_text()
    res = _run(fake_env, ["--yes", "--no-start", "--version", "0.9.0"], env)
    out = res.stdout + res.stderr
    assert res.returncode != 0 and "0.10.5 is installed" in out and "--allow-downgrade" in out and "Nothing was changed" in out
    assert (fake_env["kelo_home"] / "compose.yml").read_text() == before and not (fake_env["kelo_home"] / "marker.txt").exists()
    assert releases["seen"] == [] and not fake_env["log_file"].exists()
    ok = _run(fake_env, ["--yes", "--no-start", "--version", "0.9.0", "--allow-downgrade"], env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert (fake_env["kelo_home"] / "marker.txt").is_file()
    assert (fake_env["kelo_home"] / ".env").read_text() == "ASSISTANT_TOKEN=fake-token\n"      # the data stays


def test_the_downgrade_flag_can_come_from_the_environment(fake_env, releases):
    _installed(fake_env, "0.10.5")
    env = {**fake_env["env"], "KELO_RELEASES_BASE": releases["base"], "KELO_ALLOW_DOWNGRADE": "1"}
    assert _run(fake_env, ["--yes", "--no-start", "--version", "0.9.0"], env).returncode == 0


@pytest.mark.parametrize("version", ["0.10.5", "0.11.0"])
def test_the_same_or_a_newer_version_needs_no_extra_flag(fake_env, releases, version):
    _installed(fake_env, "0.10.5")
    env = {**fake_env["env"], "KELO_RELEASES_BASE": releases["base"]}
    res = _run(fake_env, ["--yes", "--no-start", "--version", version], env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (fake_env["kelo_home"] / "marker.txt").is_file()


@pytest.mark.parametrize("a,b,want", [("0.10.2", "0.9.9", 1), ("0.9.9", "0.10.2", -1), ("1.0.0", "1.0.0", 0), ("0.5.0-rc.1", "0.5.0", -1),
                                      ("0.5.0", "0.5.0-rc.1", 1), ("0.5.0-rc.2", "0.5.0-rc.10", -1), ("0.5.0+7", "0.5.0", 0)])
def test_versions_compare_like_the_server_does(fake_env, a, b, want):
    from assistant.release import version_key
    script = fake_env["install_sh"].read_text()
    start = script.index("version_cmp() {")
    func = script[start:script.index("\n}\n", start) + 3]
    res = subprocess.run(["bash", "-c", func + f'\nversion_cmp "{a}" "{b}"'], capture_output=True, text=True)
    assert int(res.stdout) == want
    assert (version_key(a.split("+")[0]) > version_key(b.split("+")[0])) - (version_key(a.split("+")[0]) < version_key(b.split("+")[0])) == want


def test_files_of_another_version_than_asked_are_refused_before_copying(fake_env, releases):
    env = _fresh(fake_env, releases["base"])
    res = _run(fake_env, ["--yes", "--no-start", "--version", "0.8.0"], env)      # the tag release-0.8.0 holds Kelo 0.9.9
    assert res.returncode != 0 and "These files are Kelo 0.9.9, not 0.8.0" in res.stdout + res.stderr
    assert not (fake_env["kelo_home"] / "compose.yml").exists() and not (fake_env["kelo_home"] / "marker.txt").exists()


def test_a_source_of_your_own_must_be_that_version(fake_env):
    src = fake_env["root"] / "mine"
    src.mkdir()
    (src / "compose.yml").write_text("services: {}\n")
    (src / "version.json").write_text('{"version": "0.7.0"}\n')
    (fake_env["kelo_home"] / "compose.yml").unlink()
    env = {**fake_env["env"], "KELO_SOURCE": str(src)}
    bad = _run(fake_env, ["--yes", "--no-start", "--version", "0.8.0"], env)
    assert bad.returncode != 0 and "These files are Kelo 0.7.0, not 0.8.0" in bad.stdout + bad.stderr
    assert not (fake_env["kelo_home"] / "compose.yml").exists()
    assert _run(fake_env, ["--yes", "--no-start", "--version", "0.7.0"], env).returncode == 0
    assert (fake_env["kelo_home"] / "compose.yml").is_file()


def _uv_that_knows(fake_env, knows: bool):
    """The fake uv, but `assistant ... --help` lists --from-release (and --release-version when `knows`), like the downloaded code does."""
    uv = fake_env["bin_dir"] / "uv"
    text = uv.read_text()
    help_text = "--from-release" + (" --release-version" if knows else "")
    uv.write_text(text.replace('  echo "$*" >> ', f'  case "$*" in *--help*) echo "{help_text}"; exit 0;; esac\n  echo "$*" >> ', 1))


def test_the_images_of_that_version_are_pulled_when_its_code_can_do_it(fake_env, releases):
    _uv_that_knows(fake_env, True)
    env = _fresh(fake_env, releases["base"])
    res = _run(fake_env, ["--yes", "--no-start", "--version", "0.10.2"], env)
    assert res.returncode == 0, res.stdout + res.stderr
    calls = fake_env["log_file"].read_text()
    assert "updater setup" in calls and "--pull-release --release-version 0.10.2" in calls
    assert "cannot pull its own signed images" not in res.stdout


def test_an_older_release_that_cannot_pull_by_version_is_built_here_with_a_warning(fake_env, releases):
    _uv_that_knows(fake_env, False)
    env = _fresh(fake_env, releases["base"])
    res = _run(fake_env, ["--yes", "--no-start", "--version", "0.10.2"], env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Kelo 0.10.2 cannot pull its own signed images by version" in res.stdout
    assert "--pull-release" not in fake_env["log_file"].read_text()                  # never the newest release's images


def test_no_version_pulls_the_newest_images_as_before(fake_env, releases):
    _uv_that_knows(fake_env, True)
    (fake_env["kelo_home"] / "version.json").write_text('{"version": "0.10.2"}\n')
    res = _run(fake_env, ["--yes", "--no-start"], {**fake_env["env"], "KELO_RELEASES_BASE": releases["base"]})
    assert res.returncode == 0, res.stdout + res.stderr
    calls = fake_env["log_file"].read_text()
    assert "--pull-release" in calls and "--release-version" not in calls


def test_korean_unknown_version_message(fake_env, releases):
    env = _fresh(fake_env, releases["base"])
    env["KELO_LANG"] = "ko"
    res = _run(fake_env, ["--yes", "--no-start", "--version", "9.9.9"], env)
    assert res.returncode != 0 and "9.9.9 버전을 찾을 수 없습니다" in res.stdout + res.stderr


def test_install_ps1_has_the_same_options():
    """No PowerShell here: check the script text for the same behaviour as install.sh."""
    ps1 = (_repo_root() / "scripts" / "install.ps1").read_text()
    for needle in ("[string]$Version", "[switch]$AllowDowngrade", "$env:KELO_VERSION", "$env:KELO_ALLOW_DOWNGRADE", "$env:KELO_RELEASES_BASE",
                   "archive/refs/tags/release-$Version.zip", "function Test-Version", "function Compare-Version", "--release-version",
                   "was not found", "To go back anyway add -AllowDowngrade", "These files are Kelo"):
        assert needle in ps1, needle
    assert ps1.index("Test-Version\n") < ps1.index("Fetch-Kelo\n  Set-Location")           # checked before any file is fetched
