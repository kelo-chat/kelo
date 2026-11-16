"""The command-line flags Chrome gets on a native host (docs/native-server.md sections 4 and 5). No operating-system call here.

Docker's entrypoint.sh (`browser` case) builds the same flags with its own shell code; the test in test_hosting_browser_arguments.py checks
that the two agree on what the browser must do. Docker keeps its own flags in phase 1b-2."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

WINDOW_SIZE = "1440,900"
MEGABYTE = 1024 * 1024


@dataclass(frozen=True)
class BrowserOptions:
    """What one browser start needs, with the values already read from ROLES and the environment."""

    profile_dir: Path
    devtools_port: int                  # ROLES["browser"].listen_port
    proxy_url: str                      # "http://127.0.0.1:<egress port>"
    language: str = ""                  # BROWSER_LANG, e.g. "ko-KR"
    cache_mb: int | None = None         # BROWSER_CACHE_MB; 0 or None = the browser's default
    user_agent: str = ""
    headless: bool = False
    chrome_sandbox: bool = True         # False only where the container is the sandbox (Docker)


def browser_arguments(options: BrowserOptions) -> list[str]:
    """Chrome's arguments in a fixed order. Guarantees: DevTools listens on 127.0.0.1 only; loopback traffic goes through the proxy
    except DevTools itself (the bypass list is `<-loopback>`); `--headless=new` and `--no-sandbox` appear only when asked for."""
    arguments = [
        "--no-first-run", "--no-default-browser-check", "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={options.devtools_port}", f"--user-data-dir={options.profile_dir}",
        f"--window-size={WINDOW_SIZE}", f"--proxy-server={options.proxy_url}", "--proxy-bypass-list=<-loopback>",
        "--disable-blink-features=AutomationControlled",   # Blink's automation mode off, as in the Docker browser (entrypoint.sh)
    ]
    if options.headless:
        arguments.append("--headless=new")
    if not options.chrome_sandbox:
        arguments.append("--no-sandbox")
    if options.language:
        arguments += [f"--lang={options.language}", f"--accept-lang={options.language}"]
    if options.cache_mb:
        arguments.append(f"--disk-cache-size={options.cache_mb * MEGABYTE}")
    if options.user_agent:
        arguments.append(f"--user-agent={options.user_agent}")
    arguments += ["--disable-sync", "--password-store=basic", "--disable-background-networking", "about:blank"]
    return arguments
