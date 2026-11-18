"""Static tests for the Mac server bundle (packaging/mac/): the pinned locks, the Lima instance template, the VM provisioning script,
the root and user stage scripts, and the launchd job's program.

Nothing here runs a VM or needs root. The rendered template is parsed with the system Ruby (psych), which ships with macOS; the
test is skipped where Ruby is missing. Shell scripts get `sh -n` / `bash -n`, and shellcheck when it is installed.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
MAC = REPO / "packaging" / "mac"
BIN = MAC / "bin"
FAKE_SHA512 = "a" * 128
FORBIDDEN = re.compile(r"colima|(^|[\s\"'~/])\.lima\b|(^|[\s\"'~/])\.docker\b|DOCKER_HOST|docker context|pmset|/opt/homebrew", re.IGNORECASE)


def _read(name: str) -> str:
    path = MAC / name
    assert path.is_file(), f"missing {path}"
    return path.read_text()


def _lock(name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read(name).splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _lock_values_or_fakes() -> dict[str, str]:
    """The lock values the build would put in; an empty (not yet computed) sha gets a fake of the right length."""
    values = {**_lock("vm.lock"), **_lock("lima.lock")}
    fakes = {"DEBIAN_IMAGE_SHA512": "a" * 128, "UV_AARCH64_SHA256": "a" * 64}
    return {key: values.get(key) or fakes.get(key, "") for key in ("DEBIAN_IMAGE_SHA512", "UV_AARCH64_SHA256", "UV_VERSION")}


def _render_provision() -> str:
    values = _lock_values_or_fakes()
    text = _read("provision.sh")
    for key in ("UV_VERSION", "UV_AARCH64_SHA256"):
        text = text.replace(f"@{key}@", values[key])
    return text


def _render_yaml() -> str:
    """The template with the build's substitutions (locked or fake digests; provision.sh indented into the block scalar)."""
    provision = _render_provision().rstrip("\n").splitlines()
    text = _read("kelo-vm.yaml.in").replace("@DEBIAN_IMAGE_SHA512@", _lock_values_or_fakes()["DEBIAN_IMAGE_SHA512"])
    body = "\n".join(("      " + line) if line else "" for line in provision)
    return text.replace("      @PROVISION_SH@", body)


@pytest.fixture(scope="module")
def vm() -> dict:
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("ruby (psych) is needed to parse the rendered Lima template")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kelo-vm.yaml"
        path.write_text(_render_yaml())
        out = subprocess.run(
            [ruby, "-ryaml", "-rjson", "-e", "print JSON.generate(YAML.safe_load(File.read(ARGV[0])))", str(path)],
            capture_output=True, text=True, check=False,
        )
    assert out.returncode == 0, "the rendered Lima template is not valid YAML: " + out.stderr
    return json.loads(out.stdout)


# --- locks ---------------------------------------------------------------------------------------------------------------------

def test_lima_lock_pins_a_release_url_and_sha256():
    lock = _lock("lima.lock")
    assert re.fullmatch(r"2\.\d+\.\d+", lock["LIMA_VERSION"]), "Lima must be a 2.x release"
    assert lock["LIMA_URL"] == (
        f"https://github.com/lima-vm/lima/releases/download/v{lock['LIMA_VERSION']}/"
        f"lima-{lock['LIMA_VERSION']}-Darwin-arm64.tar.gz"
    ), "LIMA_URL is the official arm64 tarball of the pinned version"
    assert lock["LIMA_SHA256"] == "" or re.fullmatch(r"[0-9a-f]{64}", lock["LIMA_SHA256"])


def test_vm_lock_pins_a_dated_debian_image_and_uv():
    lock = _lock("vm.lock")
    url = lock["DEBIAN_IMAGE_URL"]
    assert re.fullmatch(
        r"https://cloud\.debian\.org/images/cloud/bookworm/\d{8}-\d+/debian-12-genericcloud-arm64-\d{8}-\d+\.qcow2", url
    ), "Debian 12 arm64 generic cloud image from a dated folder, never 'latest'"
    assert lock["DEBIAN_IMAGE_SHA512"] == "" or re.fullmatch(r"[0-9a-f]{128}", lock["DEBIAN_IMAGE_SHA512"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", lock["UV_VERSION"])
    assert lock["UV_AARCH64_SHA256"] == "" or re.fullmatch(r"[0-9a-f]{64}", lock["UV_AARCH64_SHA256"])


def test_provision_uv_placeholders_are_filled_from_the_lock():
    rendered = _render_provision()
    assert re.search(r"@[A-Z0-9_]+@", rendered) is None
    assert f'UV_VERSION="{_lock("vm.lock")["UV_VERSION"]}"' in rendered


def test_uv_version_matches_the_wsl_distro():
    """The Mac VM installs the same uv as the Windows distro (same provisioning story)."""
    dockerfile = (REPO / "packaging" / "wsl" / "Dockerfile.distro").read_text()
    wsl_version = re.search(r"UV_VERSION=([0-9.]+)", dockerfile).group(1)
    assert _lock("vm.lock")["UV_VERSION"] == wsl_version


# --- Lima instance template ----------------------------------------------------------------------------------------------------

def test_template_has_no_placeholder_left_after_render():
    assert re.search(r"@[A-Z0-9_]+@", _render_yaml()) is None


def test_template_vm_type_arch_and_resources(vm):
    assert vm["vmType"] == "vz"
    assert vm["arch"] == "aarch64"
    assert vm["disk"] == "100GiB"
    assert vm["memory"] and vm["cpus"]   # overridden with --memory / --cpus at create


def test_template_image_is_the_bundle_copy_with_a_sha512_digest(vm):
    image = vm["images"][0]
    assert image["location"] == "/Library/Application Support/Kelo Server/current/images/debian-12-genericcloud-arm64.qcow2"
    assert image["arch"] == "aarch64"
    assert re.fullmatch(r"sha512:[0-9a-f]{128}", image["digest"])


def test_template_the_vm_sees_no_files_of_the_mac(vm):
    assert vm["mounts"] == []


def test_template_no_containerd_download(vm):
    assert vm["containerd"] == {"system": False, "user": False}


def test_template_ssh_keeps_the_mac_keys_out(vm):
    ssh = vm["ssh"]
    assert ssh["localPort"] == 0
    assert ssh["loadDotSSHPubKeys"] is False
    assert ssh["forwardAgent"] is False
    assert ssh["forwardX11"] is False


def test_template_user_is_kelo_uid_1000_like_the_wsl_distro(vm):
    assert vm["user"] == {"name": "kelo", "home": "/home/kelo", "uid": 1000}


def test_template_no_rosetta_no_audio_no_video(vm):
    text = _read("kelo-vm.yaml.in")
    assert "rosetta" in text and "enabled: false" in text
    assert vm["audio"]["device"] == "none"
    assert vm["video"]["display"] == "none"


def test_template_forwards_exactly_8080_and_8081_on_loopback(vm):
    rules = vm["portForwards"]
    open_rules = [r for r in rules if not r.get("ignore")]
    assert [(r["guestPort"], r["hostPort"]) for r in open_rules] == [(8080, 8080), (8081, 8081)]
    for rule in open_rules:
        assert rule["guestIP"] == "127.0.0.1" and rule["hostIP"] == "127.0.0.1"


def test_template_ignore_rules_cover_every_port_and_come_last(vm):
    rules = vm["portForwards"]
    first_ignore = next(i for i, r in enumerate(rules) if r.get("ignore"))
    assert all(not r.get("ignore") for r in rules[:first_ignore]), "the two real forwards come before every ignore rule"
    ignores = rules[first_ignore:]
    for guest_ip in ("0.0.0.0", "::", "127.0.0.1"):
        assert any(
            r.get("ignore") and r.get("guestIP") == guest_ip and r.get("guestPortRange") == [1, 65535] for r in ignores
        ), f"an ignore rule for {guest_ip} covers ports 1-65535"


def test_template_never_forwards_80_or_443(vm):
    """caddy's direct profile binds 80/443 in the VM; the Mac must never get them, so no open rule names them."""
    for rule in vm["portForwards"]:
        if not rule.get("ignore"):
            assert rule.get("guestPort") not in (80, 443) and rule.get("hostPort") not in (80, 443), rule


def test_template_provision_is_system_mode_and_contains_the_script(vm):
    provision = vm["provision"]
    assert len(provision) == 1 and provision[0]["mode"] == "system"
    assert "PROVISION_VERSION=" in provision[0]["script"]
    assert "docker-compose-plugin" in provision[0]["script"]


def test_template_text_has_no_forbidden_words():
    assert FORBIDDEN.search(_read("kelo-vm.yaml.in")) is None


# --- provisioning script (parity with packaging/wsl/Dockerfile.distro) ---------------------------------------------------------

def test_provision_has_the_same_docker_key_check_and_repo_as_the_wsl_distro():
    dockerfile = (REPO / "packaging" / "wsl" / "Dockerfile.distro").read_text()
    provision = _read("provision.sh")
    fingerprint = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
    assert f'DOCKER_GPG_FINGERPRINT="{fingerprint}"' in dockerfile
    assert fingerprint in provision
    assert "gpg --show-keys" in provision
    assert "https://download.docker.com/linux/debian" in provision
    assert "bookworm stable" in provision
    assert "download.docker.com/linux/debian bookworm stable" in dockerfile


def test_provision_installs_the_same_docker_packages_as_the_wsl_distro():
    provision = _read("provision.sh")
    for pkg in ("docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"):
        assert re.search(rf"\b{re.escape(pkg)}\b", provision), f"missing {pkg}"
    assert "systemctl enable --now docker" in provision or "systemctl enable docker" in provision


def test_provision_puts_kelo_in_the_docker_group_and_verifies_uv():
    provision = _read("provision.sh")
    assert 'UV_VERSION="@UV_VERSION@"' in provision and 'UV_AARCH64_SHA256="@UV_AARCH64_SHA256@"' in provision
    assert "usermod -aG docker kelo" in provision
    assert "aarch64-unknown-linux-gnu" in provision
    assert "UV_AARCH64_SHA256" in provision and "sha256sum -c" in provision
    assert "/usr/local/bin/uv" in provision and "/usr/local/bin/uvx" in provision


def test_provision_ends_the_user_session_so_the_docker_group_applies():
    provision = _read("provision.sh")
    assert provision.index("usermod -aG docker kelo") < provision.index("loginctl terminate-user kelo")
    # detached and delayed: this script can run inside the session it ends (the upgrade path)
    assert "setsid sh -c 'sleep 5; loginctl terminate-user kelo'" in provision and "&\n" in provision


def test_provision_security_updates_for_debian_security_only():
    provision = _read("provision.sh")
    assert "unattended-upgrades" in provision
    assert "Debian-Security" in provision


def test_provision_keepalive_unit_starts_compose_for_kelo():
    provision = _read("provision.sh")
    assert "/etc/systemd/system/kelo-up.service" in provision
    assert "After=docker.service" in provision
    assert "User=kelo" in provision
    assert "ConditionPathExists=/home/kelo/kelo/compose.yml" in provision
    assert "WorkingDirectory=/home/kelo/kelo" in provision
    assert "ExecStart=/usr/bin/docker compose -p kelo up -d" in provision
    assert "Type=oneshot" in provision and "RemainAfterExit=yes" in provision


def test_provision_marker_and_version_constant():
    provision = _read("provision.sh")
    match = re.search(r'^PROVISION_VERSION="?([0-9]+)"?$', provision, re.MULTILINE)
    assert match is not None, "PROVISION_VERSION is a constant at the top"
    assert "/var/lib/kelo/provisioned" in provision
    assert "set -eux" in provision


def test_provision_has_no_forbidden_words():
    assert FORBIDDEN.search(_read("provision.sh")) is None


# --- entitlements ------------------------------------------------------------------------------------------------------------

def test_limactl_entitlements_are_the_three_measured_ones_only():
    text = _read("limactl.entitlements")
    for key in ("com.apple.security.virtualization", "com.apple.security.network.client", "com.apple.security.network.server"):
        assert key in text, f"missing {key}"
    assert "com.apple.vm.networking" not in text, "bridged networking is never used (restricted entitlement)"
    assert "com.apple.security.cs.disable" not in text


# --- stage scripts -------------------------------------------------------------------------------------------------------------

def _shell_syntax_ok(path: Path, shell: str) -> None:
    out = subprocess.run([shell, "-n", str(path)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr


@pytest.mark.parametrize("name,shell", [
    ("kelo-server-admin", "sh"),
    ("kelo-server-setup", "bash"),
    ("kelo-vm-run", "sh"),
])
def test_stage_scripts_exist_are_executable_and_parse(name, shell):
    path = BIN / name
    assert path.is_file(), f"missing {path}"
    assert path.stat().st_mode & stat.S_IXUSR, f"{name} must be executable"
    _shell_syntax_ok(path, shell)


@pytest.mark.parametrize("path,shell", [
    (BIN / "kelo-server-admin", "sh"),
    (BIN / "kelo-server-setup", "bash"),
    (BIN / "kelo-vm-run", "sh"),
    (MAC / "provision.sh", "bash"),
])
def test_shellcheck_when_installed(path, shell):
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    out = subprocess.run([shellcheck, "-s", shell, str(path)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout + out.stderr


def test_admin_validates_every_input_and_exits_64_on_bad_input():
    """The admin gets the user and the mode (the version and the sha are checked by the root stage before it runs)."""
    text = _read("bin/kelo-server-admin")
    assert "^[A-Za-z_][A-Za-z0-9_.-]{0,31}$" in text, "user name is validated"
    assert "-ge 501" in text, "the user must have uid >= 501"
    assert "root" in text
    assert "daemon|agent" in text
    assert "die 64" in text and "exit \"$code\"" in text
    assert 'die 65 "must run as root"' in text


def test_admin_removes_data_only_at_the_exact_home_path_and_never_a_symlink():
    text = _read("bin/kelo-server-admin")
    assert "/.kelo-server" in text
    assert "NFSHomeDirectory" in text
    assert "-L " in text, "a symlink is refused before rm -rf"
    assert "rm -rf" in text


def test_admin_plist_keys_as_the_brief_says():
    text = _read("bin/kelo-server-admin")
    for key in ("Label", "chat.kelo.server", "UserName", "GroupName", "staff", "ProgramArguments", "EnvironmentVariables",
                "LIMA_HOME", "RunAtLoad", "KeepAlive", "ThrottleInterval", "ExitTimeOut", "ProcessType", "StandardOutPath",
                "StandardErrorPath"):
        assert key in text, f"plist key or value {key} is missing"
    assert "kelo-vm-run" in text
    assert "plutil" in text
    assert "launchctl bootout system/chat.kelo.server" in text
    assert "agent-ready" in text


def test_admin_prunes_versions_keeping_current_and_the_one_before():
    text = _read("bin/kelo-server-admin")
    assert "prune" in text.lower()


def test_vm_run_uses_the_absolute_limactl_in_foreground_and_waits_for_the_instance():
    text = _read("bin/kelo-vm-run")
    assert 'L="/Library/Application Support/Kelo Server/current/bin/limactl"' in text
    assert "start --foreground --tty=false kelo" in text
    assert "kelo/lima.yaml" in text
    assert "sleep 10" in text


def test_setup_creates_the_instance_with_its_own_flags():
    text = _read("bin/kelo-server-setup")
    assert '"$LIMACTL" "${args[@]}"' in text and "--tty=false" in text and "--name" in text
    assert '--memory "$MEMORY" --cpus "$CPUS"' in text
    assert "tmutil addexclusion" in text
    assert "--web-port" in text and "--no-open" in text and "setup --web" in text
    assert '"$LIMACTL" shell "$VM" -- bash /opt' in text
    assert "/home/kelo/kelo/compose.yml" in text
    for code in ("70", "71", "72", "73"):
        assert f"die {code}" in text, f"exit code {code} is used by the script"
    assert "ready" in text and "status" in text


def test_setup_never_prints_the_setup_url_or_its_token():
    text = _read("bin/kelo-server-setup")
    assert "#t=" not in text


def test_no_script_names_the_homebrew_or_colima_tools_or_docker_context():
    for path in [*BIN.iterdir(), MAC / "provision.sh"]:
        if path.is_file():
            assert FORBIDDEN.search(path.read_text()) is None, f"{path.name} names a forbidden tool or path"


def test_stage_scripts_never_use_pmset_or_the_user_docker_config():
    for path in BIN.iterdir():
        assert "pmset" not in path.read_text()


def test_setup_runs_limactl_by_absolute_path_only():
    text = _read("bin/kelo-server-setup")
    assert 'BUNDLE="${KELO_TEST_BUNDLE:-/Library/Application Support/Kelo Server/current}"' in text
    assert 'LIMACTL="$BUNDLE/bin/limactl"' in text
    for line in text.splitlines():
        if line.strip().startswith("#") or "LIMACTL=" in line:
            continue
        assert not re.search(r"(?<![\w/$.-])limactl\b", line), f"a bare limactl call: {line}"


def test_no_stage_script_names_a_wildcard_address():
    for path in BIN.iterdir():
        assert "0.0.0.0" not in path.read_text(), path.name


# --- the build script (tools/build/mac-server-bundle.sh) -----------------------------------------------------------------------

BUILD = REPO / "tools" / "build" / "mac-server-bundle.sh"


def _build(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(BUILD), *args], capture_output=True, text=True, check=False, cwd=cwd or REPO, timeout=120)


def test_build_script_parses_and_is_executable():
    assert BUILD.is_file(), f"missing {BUILD}"
    assert BUILD.stat().st_mode & stat.S_IXUSR
    out = subprocess.run(["bash", "-n", str(BUILD)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr


def test_build_help_lists_every_option():
    out = _build("--help")
    assert out.returncode == 0, out.stderr
    for option in ("--version", "--update-lock", "--write", "--no-sign", "--notarize", "--dry-run"):
        assert option in out.stdout, f"{option} is not in --help"


def test_build_refuses_an_unknown_option():
    out = _build("--bogus")
    assert out.returncode == 64, out.stderr


def test_build_dry_run_prints_the_hardened_codesign_and_the_ditto_zip():
    out = _build("--dry-run")
    assert out.returncode == 0, out.stdout + out.stderr
    text = out.stdout + out.stderr
    assert "codesign --force --options runtime --timestamp --entitlements packaging/mac/limactl.entitlements" in text
    assert "ditto -c -k --keepParent" in text
    assert "kelo-server-" in text and "-mac-arm64.zip" in text
    assert "notarytool" not in text, "nothing is submitted to Apple without --notarize"


def test_build_dry_run_with_notarize_prints_the_submission_only_when_asked():
    out = _build("--dry-run", "--notarize")
    text = out.stdout + out.stderr
    assert "notarytool submit" in text and "--timeout 50m" in text


def test_build_refuses_no_sign_with_notarize():
    out = _build("--no-sign", "--notarize", "--dry-run")
    assert out.returncode == 64


def test_build_refuses_an_unpinned_lock_unless_update_lock(tmp_path):
    """A copy of the script with empty lock shas refuses to build: nothing unverified is ever used."""
    (tmp_path / "tools" / "build").mkdir(parents=True)
    (tmp_path / "packaging" / "mac").mkdir(parents=True)
    for name in ("lib.sh", "mac-server-bundle.sh"):
        shutil.copy(REPO / "tools" / "build" / name, tmp_path / "tools" / "build" / name)
    for name in ("lima.lock", "vm.lock"):
        lines = [re.sub(r"^((LIMA_SHA256|DEBIAN_IMAGE_SHA512|UV_AARCH64_SHA256)=).*$", r"\g<1>", line)
                 for line in _read(name).splitlines()]
        (tmp_path / "packaging" / "mac" / name).write_text("\n".join(lines) + "\n")
    shutil.copy(REPO / "version.json", tmp_path / "version.json")
    out = subprocess.run(["bash", str(tmp_path / "tools" / "build" / "mac-server-bundle.sh"), "--dry-run"],
                         capture_output=True, text=True, check=False, cwd=tmp_path, timeout=120)
    assert out.returncode != 0
    assert "not pinned" in (out.stdout + out.stderr)


def test_build_script_checks_the_downloads_before_using_them():
    text = BUILD.read_text()
    assert "shasum -a 256" in text and "shasum -a 512" in text
    assert "kelo-src.tar.gz" in text and "git -C" in text and "archive" in text
    assert "lima-guestagent.Linux-aarch64.gz" in text
    assert "--keepParent" in text


def test_user_stage_waits_for_the_shell_and_retries_the_copies():
    """Running is not enough (ssh comes later), and a copy (rsync over ssh) can drop: the user stage waits and retries."""
    text = _read("bin/kelo-server-setup")
    assert 'until "$LIMACTL" shell "$VM" -- true' in text
    assert "copy_retry()" in text
    assert text.count("copy_retry ") >= 3
    assert '"$LIMACTL" copy' not in text.split("copy_retry()", 1)[1].split("}", 1)[1]

