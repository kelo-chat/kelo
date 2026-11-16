"""hosting/browser_arguments.py: Chrome's flags on a native host, and their agreement with Docker's flags in docker/entrypoint.sh.
Pure functions and a read of the entrypoint text; nothing starts a browser."""

from dataclasses import replace
from pathlib import Path

from assistant.hosting.browser_arguments import WINDOW_SIZE, BrowserOptions, browser_arguments

PROFILE = Path("/Library/Application Support/Kelo Server/browser-data/profile")
ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh"


def options(**changes) -> BrowserOptions:
    base = BrowserOptions(profile_dir=PROFILE, devtools_port=9223, proxy_url="http://127.0.0.1:3128")
    return replace(base, **changes)


def test_the_proxy_flag_points_at_the_given_url_and_loopback_goes_through_it_except_devtools():
    arguments = browser_arguments(options())
    assert "--proxy-server=http://127.0.0.1:3128" in arguments
    assert "--proxy-bypass-list=<-loopback>" in arguments


def test_devtools_listens_on_127_0_0_1_only_on_the_given_port():
    arguments = browser_arguments(options(devtools_port=9999))
    assert "--remote-debugging-address=127.0.0.1" in arguments
    assert "--remote-debugging-port=9999" in arguments
    assert "--user-data-dir=" + str(PROFILE) in arguments


def test_the_default_run_is_headful_with_the_sandbox_on_and_no_headless_or_no_sandbox_flag():
    arguments = browser_arguments(options())
    assert "--headless=new" not in arguments
    assert "--no-sandbox" not in arguments


def test_headless_and_no_sandbox_appear_only_when_asked_for():
    arguments = browser_arguments(options(headless=True, chrome_sandbox=False))
    assert "--headless=new" in arguments and "--no-sandbox" in arguments


def test_the_language_flags_come_only_when_a_language_is_set():
    assert "--lang=ko-KR" in browser_arguments(options(language="ko-KR"))
    assert "--accept-lang=ko-KR" in browser_arguments(options(language="ko-KR"))
    assert not any(argument.startswith(("--lang=", "--accept-lang=")) for argument in browser_arguments(options()))


def test_the_cache_flag_is_the_size_in_bytes_and_zero_or_none_means_the_browser_default():
    assert "--disk-cache-size=67108864" in browser_arguments(options(cache_mb=64))
    assert not any(argument.startswith("--disk-cache-size") for argument in browser_arguments(options(cache_mb=0)))
    assert not any(argument.startswith("--disk-cache-size") for argument in browser_arguments(options(cache_mb=None)))


def test_the_user_agent_flag_comes_only_when_a_user_agent_is_set():
    agent = "Mozilla/5.0 (Macintosh) Chrome/140.0"
    assert f"--user-agent={agent}" in browser_arguments(options(user_agent=agent))
    assert not any(argument.startswith("--user-agent") for argument in browser_arguments(options()))


def test_the_fixed_flags_are_always_there_and_the_page_is_about_blank_last():
    arguments = browser_arguments(options(language="ko-KR", cache_mb=8, user_agent="x", headless=True))
    for flag in ("--no-first-run", "--no-default-browser-check", f"--window-size={WINDOW_SIZE}", "--disable-sync",
                 "--password-store=basic", "--disable-background-networking"):
        assert flag in arguments
    assert arguments[-1] == "about:blank"


def test_docker_and_the_native_flags_agree_on_devtools_first_run_window_proxy_and_the_sandbox():
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    native = browser_arguments(options(chrome_sandbox=False))
    for flag in ("--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
                 f"--window-size={WINDOW_SIZE}", "--proxy-server=", "--no-sandbox", "--user-data-dir="):
        assert flag in entrypoint, flag
    assert "--remote-debugging-address=127.0.0.1" in native
    assert "--no-first-run" in native and "--no-default-browser-check" in native
    assert f"--window-size={WINDOW_SIZE}" in native
    assert any(argument.startswith("--proxy-server=") for argument in native)
    assert "--no-sandbox" in native


def test_docker_stays_headful_and_so_is_the_default_of_browser_arguments():
    assert "--headless" not in ENTRYPOINT.read_text(encoding="utf-8")
    assert "--headless=new" not in browser_arguments(options())
