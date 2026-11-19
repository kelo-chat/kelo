"""The browser container's start line and policy (docker/entrypoint.sh, docker/chromium-policy.json): the mouse is declared as a fine
pointer and hover, the environment names the user's language the way Linux Chrome reads it (LANGUAGE), and location asks."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "docker"
ENTRYPOINT = (ROOT / "entrypoint.sh").read_text()


def test_the_browser_line_declares_a_fine_pointer_with_hover():
    assert "--blink-settings=primaryPointerType=4,availablePointerTypes=4,primaryHoverType=2,availableHoverTypes=2" in ENTRYPOINT


def test_location_asks_and_is_never_granted():
    policy = json.loads((ROOT / "chromium-policy.json").read_text())
    assert policy["DefaultGeolocationSetting"] == 3        # 1 allow, 2 block, 3 ask
    assert policy.get("DefaultNotificationsSetting", 3) == 3          # notifications ask too


def test_the_language_is_exported_from_the_browser_language_as_linux_chrome_reads_it():
    # the language block of the browser role, run on its own with BROWSER_LANG set: ko-KR -> ko_KR:ko, en-US -> en_US:en, ja -> ja
    start = ENTRYPOINT.index('    LANG_FLAGS=""')
    end = ENTRYPOINT.index("    # Downloads (the server reads them", start)
    block = "\n".join(line[4:] for line in ENTRYPOINT[start:end].splitlines())
    for value, expected in (("ko-KR", "ko_KR:ko"), ("en-US", "en_US:en"), ("ja", "ja")):
        out = subprocess.run(["sh", "-c", f'BROWSER_LANG="{value}"\n{block}\nprintf %s "${{LANGUAGE:-}}"'], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert out.stdout == expected, (value, out.stdout)


def test_stock_permission_defaults_where_nobody_can_answer_harmfully():
    # addendum item 11: sensors and local fonts show the stock "ask" (a prompt nobody answers grants nothing);
    # serial, USB and HID stay blocked because a grant could expose the host's devices; QUIC stays off (egress proxy, LAN block)
    policy = json.loads((ROOT / "chromium-policy.json").read_text())
    assert policy["DefaultSensorsSetting"] == 3
    assert policy["DefaultLocalFontsSetting"] == 3
    assert policy["DefaultSerialGuardSetting"] == 2
    assert policy["DefaultWebUsbGuardSetting"] == 2
    assert policy["DefaultWebHidGuardSetting"] == 2
    assert policy["QuicAllowed"] is False


def test_the_identity_block_reads_the_stated_os_from_the_module():
    assert "python -m assistant.browser_identity --stated-os" in ENTRYPOINT


def test_the_identity_loop_and_flag_follow_the_browser_and_the_stated_os(tmp_path):
    # the identity block of the browser role, run for BROWSER=chrome and BROWSER=chromium (/etc/kelo-browser) and for the stated OS
    # linux (the default) and chromeos (the switch). Google Chrome on Linux needs no loop and no flag (it already states Linux);
    # every other case runs the loop and passes --user-agent. `python`, `browser` and `sleep` are stubs; the loop is a line of its own.
    start = ENTRYPOINT.index("    # Identity (docs/browser.md")
    end = ENTRYPOINT.index("    # --no-sandbox:", start)
    block = "\n".join(line[4:] for line in ENTRYPOINT[start:end].splitlines())
    marker = tmp_path / "kelo-browser"
    log = tmp_path / "calls.log"
    linux_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
    chromeos_ua = "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
    cases = [
        ("chrome", "linux", False, None),
        ("chromium", "linux", True, linux_ua),
        ("chrome", "chromeos", True, chromeos_ua),
        ("chromium", "chromeos", True, chromeos_ua),
    ]
    for kind, stated, expect_loop, ua in cases:
        marker.write_text(kind + "\n")
        log.write_text("")
        stubs = (f'STATED="{stated}"\n'
                 f'browser() {{ echo "Google Chrome 158.0.8127.36"; }}\n'
                 f'python() {{ echo "python $*" >> "{log}"; case "$*" in *--stated-os*) echo "$STATED";; '
                 f'*--user-agent*) if [ "$STATED" = linux ]; then echo "{linux_ua}"; else echo "{chromeos_ua}"; fi;; esac; }}\n'
                 f'sleep() {{ exit 0; }}\n')
        script = stubs + block.replace("/etc/kelo-browser", str(marker)) + '\nprintf "UA_FLAG=%s\\n" "${UA_FLAG:-}"\nwait\n'
        out = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        assert out.returncode == 0, (kind, stated, out.stderr)
        calls = log.read_text().splitlines()
        loop_lines = [c for c in calls if c == "python -m assistant.browser_identity"]   # the loop's own call, no arguments
        assert ("python -m assistant.browser_identity --stated-os" in calls), (kind, stated, calls)
        if expect_loop:
            assert loop_lines, (kind, stated, calls)
            assert "python -m assistant.browser_identity --user-agent 158.0.8127.36" in calls, (kind, stated, calls)
            assert f"--user-agent={ua}" in out.stdout, (kind, stated, out.stdout)
        else:
            assert not loop_lines and not any("--user-agent" in c for c in calls), (kind, stated, calls)
            assert "UA_FLAG=\n" in out.stdout, (kind, stated, out.stdout)


def _session_block() -> str:
    start = ENTRYPOINT.index("    # The owner's browser keeps its sign-ins across a restart")
    end = ENTRYPOINT.index("    # --no-sandbox:", start)
    return "\n".join(line[4:] for line in ENTRYPOINT[start:end].splitlines())


def test_the_main_browser_keeps_session_cookies_and_drops_saved_tabs(tmp_path):
    import sys

    profile = tmp_path / "profile"
    (profile / "Default" / "Sessions").mkdir(parents=True)
    (profile / "Default" / "Sessions" / "Session_1").write_text("tabs")
    (profile / "Default" / "Preferences").write_text(json.dumps({"profile": {"name": "x"}, "session": {"restore_on_startup": 5, "other": 1}}))
    block = _session_block().replace("/data/profile", str(profile)).replace("python -c", f"{sys.executable} -c")
    out = subprocess.run(["sh", "-c", f"BROWSER_EPHEMERAL=0\n{block}"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    prefs = json.loads((profile / "Default" / "Preferences").read_text())
    assert prefs["session"]["restore_on_startup"] == 1 and prefs["session"]["other"] == 1 and prefs["profile"]["name"] == "x"
    assert not (profile / "Default" / "Sessions").exists()


def test_the_work_browser_is_not_given_the_session_preference(tmp_path):
    import sys

    profile = tmp_path / "profile"
    (profile / "Default" / "Sessions").mkdir(parents=True)
    block = _session_block().replace("/data/profile", str(profile)).replace("python -c", f"{sys.executable} -c")
    subprocess.run(["sh", "-c", f"BROWSER_EPHEMERAL=1\n{block}"], capture_output=True, text=True)
    assert (profile / "Default" / "Sessions").exists() and not (profile / "Default" / "Preferences").exists()
