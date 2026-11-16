"""Environment hygiene, status and install for the CLI-backed providers (claude, agy).

The vendored plugins in ``vendor/`` pass the server's whole environment (ASSISTANT_TOKEN, every model
API key) to the CLI they spawn. ``cli_agents`` uses this module to hand them a filtered one instead,
without modifying the vendored code.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import weakref
from pathlib import Path
from typing import Any

from ..hosting import operating_system, processes
from ..i18n import N_, t
from .types import ListUnavailable, ProviderError

CLAUDE = "claude-subscription-directsdk-experimental"
AGY = "antigravity-oauth"

_BIN_DIRS = ("~/.local/bin", "~/.claude/local", "~/bin", "~/.npm-global/bin", "~/.bun/bin", "~/.volta/bin",
             "/opt/homebrew/bin", "/usr/local/bin")


def find_command(names: list[str], env_var: str) -> str | None:
    if os.environ.get(env_var):
        return os.environ[env_var]
    for n in names:
        if p := shutil.which(n):
            return p
    for d in _BIN_DIRS:
        for n in names:
            p = Path(d).expanduser() / n
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


# ---------------------------------------------------------------- environment

# Allowlist for `claude`: it must never inherit ASSISTANT_TOKEN or model API keys. Leaving
# ANTHROPIC_API_KEY / Bedrock / Vertex / base-URL overrides out also forces the subscription login.
_CLAUDE_ENV_ALLOW = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TZ", "TMPDIR",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "CLAUDE_CONFIG_DIR",  # where the subscription login lives
    "CLAUDE_CODE_OAUTH_TOKEN",  # subscription token from `claude setup-token`
    "CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND",  # path override for the claude binary
})
_CLAUDE_ENV_ALLOW_PREFIXES = ("LC_", "XDG_")


def claude_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    src = os.environ if environ is None else environ
    return {k: v for k, v in src.items()
            if k.upper() in _CLAUDE_ENV_ALLOW or k.startswith(_CLAUDE_ENV_ALLOW_PREFIXES)}


_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)


def agy_env(environ: dict[str, str]) -> dict[str, str]:
    """Denylist (agy's real needs are not fully known): drop secret-looking names and our own ASSISTANT_*."""
    return {k: v for k, v in environ.items()
            if k.startswith("ANTIGRAVITY_") or not (_SECRET_NAME.search(k) or k.startswith("ASSISTANT_"))}


def wrap_agy_child_env(build: Any) -> Any:
    def scrubbed(*a: Any, **kw: Any) -> dict[str, str]:
        return agy_env(build(*a, **kw))
    scrubbed._scrubbed = True  # type: ignore[attr-defined]
    return scrubbed


_SHELL_META = re.compile(r"[;&|<>`$()\n\\*?{}~!]")
_HARD_META = re.compile(r"[;&|<>`$()\n\\]")          # never allowed, even inside quotes
_GLOB = re.compile(r"[*?{}~!]")
_DOC_FILE = re.compile(r"[^\s'\"=:()<>|;,\[\]{}]+\.(?:pdf|docx|xlsx|xlsm)\b", re.I)
# commands a person (or model) uses to get at a document's content; none of them is ever run
_DOC_READERS = {"python", "python3", "pdftotext", "pdfinfo", "unzip", "cat", "head", "tail", "strings", "file", "less", "more", "wc",
                "libreoffice", "soffice", "pandoc", "docx2txt", "xlsx2csv", "antiword", "catdoc", "xxd", "hexdump"}
_LS_FLAGS = re.compile(r"-[A-Za-z1]+")


def safe_shell_to_tool(command: str) -> tuple[str, dict[str, Any]] | None:
    """A read-only `ls [flags] [path]` or `cat path` -> (our tool, its arguments). Anything else -> None.

    Nothing is executed: the model's intent ("show me what is there") is answered by our own read-only tools,
    which only see the agent's files directory. Pipes, redirects, substitutions, globs and every other
    command stay refused.
    """
    import shlex

    if isinstance(command, str):
        # "get me the text of this uploaded document", however it is phrased (a python one-liner, pdftotext, unzip, cat, ...):
        # nothing of the command runs; only the file name is taken and our extractor is asked for that same document.
        first = re.match(r"\s*(?:cd\s+\S+\s*&&\s*)?([A-Za-z0-9_./+-]+)", command)
        doc = _DOC_FILE.search(command)
        if first and doc and first.group(1).rsplit("/", 1)[-1] in _DOC_READERS and len(command) < 4000:
            return "extract_document", {"path": doc.group(0)}
    if isinstance(command, str):  # hiding errors is harmless; any other redirect stays refused
        command = re.sub(r"\s+2>\s*/dev/null\s*$", "", command)
        command = re.sub(r"\\\n\s*", " ", command)       # a documented multi-line command (backslash + newline)
    if not isinstance(command, str) or not command.strip() or len(command) > 300 or _HARD_META.search(command):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    if argv[0] == "find":  # find [path] [-maxdepth N] [-type f|d] [-name GLOB]: the only place a glob is allowed
        path, args, i, names = "", {"recursive": True}, 1, []
        while i < len(argv):
            a = argv[i]
            if a == "-maxdepth" and i + 1 < len(argv) and argv[i + 1].isdigit():
                args["max_depth"] = int(argv[i + 1]); i += 2
            elif a == "-type" and i + 1 < len(argv) and argv[i + 1] in ("f", "d"):
                args["type"] = argv[i + 1]; i += 2
            elif a in ("-name", "-iname") and i + 1 < len(argv) and not re.search(r"[/\x00]", argv[i + 1]):
                names.append(argv[i + 1]); i += 2
                if i < len(argv) and argv[i] in ("-o", "-or") and i + 1 < len(argv) and argv[i + 1] in ("-name", "-iname"):
                    i += 1                                   # name1 OR name2: only between two -name tests
            elif not a.startswith("-") and not path and not _GLOB.search(a):
                path = a; i += 1
            else:
                return None  # -exec, -delete, -print0, -o, ... : not offered
        if names:
            args["name"] = names[0] if len(names) == 1 else names
        return "list_files", {**args, "path": path}
    if any(_GLOB.search(a) for a in argv[1:]):
        return None
    if argv[0] == "ls":
        rest = [a for a in argv[1:] if not _LS_FLAGS.fullmatch(a)]
        if len(rest) > 1 or any(a.startswith("-") for a in rest):
            return None
        return "list_files", {"path": rest[0] if rest else ""}
    if argv[0] == "cat" and len(argv) == 2 and not argv[1].startswith("-"):
        return "read_file", {"path": argv[1]}
    return None


def extend_agy_native_tools() -> None:
    """Teach the vendored agy plugin the native tool names agy 1.5.x uses.

    The plugin turns an agy-native tool call into the same-named tool of ours (so OUR sandbox and approval run it,
    never agy on the host). Its table only knows older names (`view_file` + `AbsolutePath`); agy 1.5 calls
    `read_file` with `Path`, which was blocked and left the reply empty. Only tools we already have are added.
    """
    from .vendor.antigravity import native_tools as nt

    def list_dir(p: dict[str, Any]) -> dict[str, Any]:
        path = nt._pick(p, "DirectoryPath", "Path", "path")
        return {"path": str(path)} if path else {}

    original = nt.translate
    if not getattr(original, "_extended", False):
        from types import SimpleNamespace

        def translate(step: dict[str, Any], available: set[str], index: int) -> Any:
            call = original(step, available, index)
            if call is not None or str(step.get("tool_name") or "") != "run_command":
                return call
            params = (step.get("tool_info") or {}).get("parameters") or {}
            cmd = str(nt._pick(params, "CommandLine", "command", "cmd") or "")
            mapped = safe_shell_to_tool(cmd)
            if mapped is None or mapped[0] not in available:
                import logging
                logging.getLogger(__name__).warning("agy shell command refused (not run): %r | offered tools: %s", cmd[:240], sorted(available))
                return None
            name, args = mapped
            return SimpleNamespace(index=index, id=f"agy_{step.get('step_index', index)}_run_command", type="function",
                                   function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))

        translate._extended = True  # type: ignore[attr-defined]
        nt.translate = translate

    for native, target in (("read_file", ("read_file", nt._read_file)), ("write_file", ("write_file", nt._write_file)),
                           ("list_dir", ("list_files", list_dir)), ("list_directory", ("list_files", list_dir))):
        nt.NATIVE_TO_HERMES.setdefault(native, target)


# ---------------------------------------------------------------- status + install (used by the app's settings)

AGY_INSTALL_URL = "https://antigravity.google/cli/install.sh"  # Google's installer; fixed, never user-supplied
_AGY_TOKENS = ("jetski-standalone-oauth-token", "antigravity-oauth-token")
_INSTALL_TIMEOUT = 300.0
_MAX_SCRIPT_BYTES = 200_000
_installing = False
_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _agy_token_dir() -> Path:
    return Path(os.environ.get("ANTIGRAVITY_CONFIG_DIR") or Path.home() / ".gemini" / "antigravity-cli")


IN_CONTAINER_HINT = N_("This server container has no CLI. Turn on the CLI container in `assistant setup` (or run the Kelo server outside a container)")


def in_container() -> bool:
    return Path("/.dockerenv").exists()


async def _version(path: str, env: dict[str, str]) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(path, "--version", env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
        return out.decode().strip().splitlines()[0][:80] or None
    except (asyncio.TimeoutError, OSError, IndexError):
        return None


async def cli_status(name: str, *, local: bool = False) -> dict[str, Any]:
    """{installed, installable, logged_in (True/False/None=unknown), hint} for a CLI-backed provider."""
    from . import cli_remote

    if not local and cli_remote.remote_url(name):  # Docker: the CLI lives in a sidecar
        return await cli_remote.status(name)
    if not local and in_container():  # server container with no sidecar configured
        return {"installed": False, "installable": False, "logged_in": None, "version": None, "hint": t(IN_CONTAINER_HINT)}
    hit = _status_cache.get(name)
    if hit and time.monotonic() - hit[0] < 30:
        return _shown(hit[1])
    if name == AGY:
        path = find_command(["agy"], "AGY_COMMAND")
        logged = True if any((_agy_token_dir() / t).is_file() for t in _AGY_TOKENS) else None
        if logged is None and operating_system.is_linux() and not shutil.which("secret-tool"):
            logged = False   # no keyring to hold it (the sidecar): no token file means signed out
        st = {"installed": bool(path), "installable": operating_system.is_macos() or operating_system.is_linux(), "logged_in": logged,
              "version": await _version(path, agy_env(dict(os.environ))) if path else None,
              "update_hint": N_("In a terminal: `agy update`"),
              "hint": N_("Run `agy` once in a terminal and sign in with your Google account")}
    else:
        path = find_command(["claude"], "CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND")
        logged, account = None, None
        if path:
            try:
                proc = await asyncio.create_subprocess_exec(
                    path, "auth", "status", env=claude_env(), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(proc.communicate(), 10)
                auth = json.loads(out)
                logged = bool(auth.get("loggedIn"))
                email = auth.get("email")
                account = email[:120] if logged and isinstance(email, str) and "@" in email else None
            except (asyncio.TimeoutError, OSError, ValueError, AttributeError):
                logged = None
        st = {"installed": bool(path), "installable": False, "logged_in": logged, "account": account,
              "version": await _version(path, claude_env()) if path else None,
              "update_hint": N_("In a terminal: `claude update`"),
              "hint": N_("The server has no claude CLI. Install: curl -fsSL https://claude.ai/install.sh | bash (or npm install -g @anthropic-ai/claude-code), then in a terminal: `claude` \u2192 /login")
              if not path else N_("Run `claude` in a terminal and sign in with /login")}
    _status_cache[name] = (time.monotonic(), st)
    return _shown(st)


def _shown(st: dict[str, Any]) -> dict[str, Any]:
    """The status with its hints in the user's language (the cache keeps the English ones)."""
    return {**st, **{k: t(st[k]) for k in ("hint", "update_hint") if st.get(k)}}


# The claude CLI has no listing command; these are the aliases and ids it accepts.
CLAUDE_MODELS = {
    "sonnet": "Sonnet (latest)", "opus": "Opus (latest)", "haiku": "Haiku (latest)",
    "claude-fable-5-1": "Claude Fable 5.1", "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-haiku-5-5": "Claude Haiku 5.5",
}


async def cli_models(name: str, *, local: bool = False) -> dict[str, str]:
    """{model id: display label} for a CLI-backed provider. agy asks its own `agy models`."""
    from . import cli_remote

    if not local and cli_remote.remote_url(name):  # Docker: ask the sidecar
        return await cli_remote.models(name)
    if name == CLAUDE:
        return dict(CLAUDE_MODELS)
    path = find_command(["agy"], "AGY_COMMAND")
    if not path:
        raise ProviderError(t("agy is not installed"))
    try:
        proc = await asyncio.create_subprocess_exec(
            path, "models", env=agy_env(dict(os.environ)), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
    except asyncio.TimeoutError:
        raise ListUnavailable(t("agy models timed out")) from None
    except OSError as e:
        raise ProviderError(t("Could not run agy ({error})", error=type(e).__name__)) from None
    models = {}
    for line in out.decode("utf-8", "replace").splitlines():
        mid, sep, label = line.partition("\t")  # "id<TAB>Display name"; the "Fetching..." line has no tab
        if sep and mid.strip():
            models[mid.strip()] = label.strip()
    if not models:
        raise ProviderError(t("Could not get the model list. Check that you are signed in to agy"))
    return models


# ---------------------------------------------------------------- subscription usage (`/usage` of each CLI)

_CLAUDE_WINDOW = re.compile(r"^(Current [^:]+):\s*(\d+)%\s*used(?:\s*·\s*resets\s+(.+))?$", re.M)
_RESET_AT = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*(?:\(([^)]+)\))?$", re.I)
_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def _claude_reset_iso(text: str, now: Any = None) -> str | None:
    """'Nov 6 at 6am (Asia/Seoul)' -> ISO-8601 UTC, or None when it does not parse."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    m = _RESET_AT.match(text.strip())
    if not m or m.group(1).title() not in _MONTHS:
        return None
    try:
        tz = ZoneInfo(m.group(6)) if m.group(6) else timezone.utc
        hour = int(m.group(3)) % 12 + (12 if m.group(5).lower() == "pm" else 0)
        now = now or datetime.now(timezone.utc)
        dt = datetime(now.astimezone(tz).year, _MONTHS[m.group(1).title()], int(m.group(2)), hour,
                      int(m.group(4) or 0), tzinfo=tz)
        if dt < now - timedelta(days=2):  # a reset in early January read in late December
            dt = dt.replace(year=dt.year + 1)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, KeyError, OSError):
        return None


def parse_claude_usage(text: str, now: Any = None) -> list[dict[str, Any]]:
    """Windows from `claude -p /usage`; the per-machine "what is contributing" breakdown is ignored."""
    out = []
    for label, pct, resets in _CLAUDE_WINDOW.findall(text):
        out.append({"group": None, "label": label.strip(), "used_percent": int(pct),
                    "resets_at": _claude_reset_iso(resets, now) if resets else None, "resets_text": resets.strip() or None})
    return out


def parse_agy_usage(text: str) -> list[dict[str, Any]]:
    """`agy -p /usage` prints 'group<TAB>label<TAB>NN%<TAB>ISO reset time' with the REMAINING percentage."""
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].strip().endswith("%") and parts[2].strip()[:-1].isdigit():
            out.append({"group": parts[0].strip(), "label": parts[1].strip(),
                        "used_percent": 100 - int(parts[2].strip()[:-1]),
                        "resets_at": parts[3].strip() if len(parts) > 3 and parts[3].strip() else None, "resets_text": None})
    return out


async def cli_usage(name: str, *, local: bool = False) -> list[dict[str, Any]]:
    """Subscription limit windows as the CLI itself reports them. Costs no tokens."""
    from . import cli_remote

    if not local and cli_remote.remote_url(name):
        return await cli_remote.usage(name)
    with tempfile.TemporaryDirectory(prefix="cli-usage-") as tmp:
        if name == CLAUDE:
            path, args, env, parse = find_command(["claude"], "CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND"), ["-p", "/usage"], claude_env(), parse_claude_usage
        else:
            path, args, env, parse = find_command(["agy"], "AGY_COMMAND"), ["-p", "/usage"], agy_env(dict(os.environ)), parse_agy_usage
        if not path:
            raise ProviderError(t("The CLI is not installed"))
        try:
            proc = await asyncio.create_subprocess_exec(path, *args, cwd=tmp, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 45)
        except asyncio.TimeoutError:
            raise ProviderError(t("Checking usage timed out")) from None
        except OSError as e:
            raise ProviderError(t("Could not run the CLI ({error})", error=type(e).__name__)) from None
    windows = parse(out.decode("utf-8", "replace"))
    if not windows:
        raise ProviderError(t("Could not get usage information. Check that you are signed in"))
    return windows


# The server's kept statuses (cli_status_cache.CliStatusCache) hear of every change through forget_status: sign-in, sign-out,
# update, install. Held weakly, so an app that is gone (tests make many) is not kept alive by this list.
_forget_listeners: "weakref.WeakSet[Any]" = weakref.WeakSet()


def on_forget(listener: Any) -> None:
    """`listener.invalidate(name)` is called on every forget_status(name)."""
    _forget_listeners.add(listener)


def forget_status(name: str) -> None:
    _status_cache.pop(name, None)
    for listener in list(_forget_listeners):
        listener.invalidate(name, now=True)


# ---------------------------------------------------------------- sign out

async def _run_cli(argv: list[str], env: dict[str, str], timeout: float = 20.0) -> int:
    """Run one fixed sign-out command; only its exit code is used (its output is never shown or logged)."""
    with tempfile.TemporaryDirectory(prefix="cli-logout-") as tmp:
        try:
            proc = await asyncio.create_subprocess_exec(*argv, cwd=tmp, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            return await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise ProviderError(t("Signing out timed out")) from None
        except OSError as e:
            raise ProviderError(t("Could not run the CLI ({error})", error=type(e).__name__)) from None


def _claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        raise ProviderError(t("Could not remove the sign-in ({error})", error=type(e).__name__)) from None


async def cli_logout(name: str, *, local: bool = False) -> dict[str, Any]:
    """Sign the CLI out of its account: {"removed": bool, "still_signed_in": bool}.

    claude: its own `claude auth logout`, then its credentials file if one is left. agy has no sign-out command, so its
    token files are removed (and on macOS its keychain item). Nothing else in the CLI's home is touched."""
    from . import cli_remote

    if not local and cli_remote.remote_url(name):  # Docker: the login lives in the sidecar's home volume
        res = await cli_remote.logout(name)
        forget_status(name)
        return res
    if not local and in_container():
        return {"removed": False, "still_signed_in": False}
    removed = False
    if name == CLAUDE:
        path = find_command(["claude"], "CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND")
        if path:
            removed = await _run_cli([path, "auth", "logout"], claude_env()) == 0
        removed = _unlink(_claude_config_dir() / ".credentials.json") or removed
        still = bool(claude_env().get("CLAUDE_CODE_OAUTH_TOKEN"))   # a token set in the environment cannot be removed from here
    elif name == AGY:
        for fname in _AGY_TOKENS:
            removed = _unlink(_agy_token_dir() / fname) or removed
        if operating_system.is_macos() and not os.environ.get("ANTIGRAVITY_CONFIG_DIR"):
            removed = await _run_cli(["/usr/bin/security", "delete-generic-password", "-s", "gemini", "-a", "antigravity"],
                                     {"PATH": "/usr/bin:/bin"}, 10.0) == 0 or removed
        still = False
    else:
        raise ProviderError(f"{name}: not a CLI provider")
    forget_status(name)
    return {"removed": removed, "still_signed_in": still}


async def install_agy() -> str:
    """Download Google's installer and run it as the server user. No arguments come from the caller."""
    global _installing
    import httpx

    from . import cli_remote

    if cli_remote.remote_url(AGY):
        raise ProviderError(t("On Docker the app does not install it. Turn on the sidecar: {command}", command=cli_remote.profile_hint(AGY)))
    if in_container():
        raise ProviderError(t(IN_CONTAINER_HINT))
    if not (operating_system.is_macos() or operating_system.is_linux()):
        raise ProviderError(t("Automatic agy install works only on macOS/Linux"))
    if find_command(["agy"], "AGY_COMMAND"):
        raise ProviderError(t("agy is already installed"))
    if _installing:
        raise ProviderError(t("Already installing"))
    _installing = True
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
            r = await c.get(AGY_INSTALL_URL)
        if r.status_code != 200 or not r.content.startswith(b"#!") or len(r.content) > _MAX_SCRIPT_BYTES:
            raise ProviderError(t("Could not download the install script (HTTP {status})", status=r.status_code))
        with tempfile.TemporaryDirectory(prefix="agy-install-") as tmp:
            script = Path(tmp) / "install.sh"
            script.write_bytes(r.content)
            env = {k: v for k, v in claude_env().items() if not k.startswith("CLAUDE_")}  # no tokens to the installer
            proc = await processes.start_group_async(
                ["bash", str(script)], cwd=tmp, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), _INSTALL_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                raise ProviderError(t("The install timed out")) from None
        text = out.decode("utf-8", "replace")[-1500:]
        if proc.returncode != 0 or not find_command(["agy"], "AGY_COMMAND"):
            raise ProviderError(t("The install failed (exit {code}): {output}", code=proc.returncode, output=text[-400:]))
        return text
    finally:
        _installing = False
        forget_status(AGY)
