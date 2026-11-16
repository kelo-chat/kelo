"""Sign the claude / agy CLI in from the phone: drive the CLI's own login, hand back its sign-in address, type the code in.

Both CLIs show a sign-in address whose page ends on a code to paste back (no localhost callback is needed):

- claude: ``claude auth login`` prints ``https://claude.com/cai/oauth/authorize?code=true&...redirect_uri=https://platform.claude.com/
  oauth/code/callback...`` and reads "Paste code here" from stdin, also without a terminal. The page shows ``<code>#<state>``.
  On success the CLI writes its own refreshable login (``~/.claude/.credentials.json``) and exits 0; a wrong code exits 1.
  (``claude setup-token`` would only make a one-year ``user:inference`` token printed to the screen, which then has to be kept
  in the environment, so it is not used.)
- agy: has no login subcommand. Run with no sign-in, the interactive ``agy`` shows "Select login method" (1. Google OAuth); Enter
  prints ``https://accounts.google.com/o/oauth2/auth?...redirect_uri=https://antigravity.google/oauth-callback...`` and an
  "authorization code" field. It needs a terminal, so it runs under a pty. Success = its token file appears; the CLI is then
  stopped at once (it would go on to a chat screen). agy is only started while no token file exists, so a signed-in agy is
  never opened interactively.

One sign-in at a time per CLI. The code goes to the CLI's stdin, never on a command line, and nothing the CLI prints is logged
or returned except the sign-in address.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..hosting import processes
from ..hosting.terminal import Pty
from ..i18n import t
from . import cli_manage
from .types import ProviderError

TTL = 600.0            # a started sign-in is kept this long, then the CLI is stopped
URL_TIMEOUT = 40.0     # waiting for the CLI to print its address
FINISH_TIMEOUT = 60.0  # waiting for the CLI to accept the code

_ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]|\x1b[=>]")
_OSC8 = re.compile(r"\x1b\]8;[^;\x07\x1b]*;(https://[^\x07\x1b]+)(?:\x07|\x1b\\)")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

# Only these addresses are ever handed to the phone.
URL_PREFIXES = {
    cli_manage.CLAUDE: ("https://claude.com/cai/oauth/authorize?", "https://claude.ai/oauth/authorize?",
                        "https://console.anthropic.com/oauth/authorize?", "https://platform.claude.com/oauth/authorize?"),
    cli_manage.AGY: ("https://accounts.google.com/o/oauth2/auth?", "https://accounts.google.com/o/oauth2/v2/auth?"),
}


def clean(raw: str) -> str:
    return _ANSI.sub("", _OSC.sub("", raw)).replace("\r", "")


def find_url(provider: str, raw: str) -> str | None:
    """The sign-in address in what the CLI printed so far, or None. A terminal hyperlink (OSC 8) carries it whole; the plain text
    may be wrapped over several lines, which are joined back until the first blank line."""
    prefixes = URL_PREFIXES[provider]
    for m in _OSC8.finditer(raw):
        if m.group(1).startswith(prefixes):
            return m.group(1)
    text = clean(raw)
    for p in prefixes:
        i = text.find(p)
        if i < 0:
            continue
        lines = text[i:].split("\n")
        parts = [lines[0].strip()]
        for ln in lines[1:]:
            st = ln.strip()
            if not st or not _URL_PIECE.fullmatch(st):
                break                                # the address ended (a blank line or ordinary text)
            parts.append(st)
        else:
            return None                              # still being printed
        url = re.split(r"\s", "".join(parts), maxsplit=1)[0]
        return url if "state=" in url else None
    return None


_URL_PIECE = re.compile(r"[A-Za-z0-9%&=._~:/?#+\-]+")


def normalize_code(provider: str, text: str) -> str:
    """What the user pasted -> what the CLI expects. Takes the bare code, or the whole address the page ended on."""
    s = (text or "").strip()
    if not s or len(s) > 4096 or not s.isprintable() or any(c.isspace() for c in s):
        raise ProviderError(t("Paste the code shown on the sign-in page (one line, no spaces)"), status=422)
    if s.startswith(("https://", "http://")):
        q = parse_qs(urlsplit(s).query)
        code = (q.get("code") or [""])[0]
        if not code:
            raise ProviderError(t("That address has no sign-in code. Paste the code shown on the page"), status=422)
        state = (q.get("state") or [""])[0]
        return f"{code}#{state}" if provider == cli_manage.CLAUDE and state else code
    return s


class _Proc:
    """The running CLI and what it printed (kept in memory only)."""

    def __init__(self) -> None:
        self.buf = ""
        self.proc: asyncio.subprocess.Process | None = None      # the running CLI (None on Windows, where only `pty` is set)
        self.pty: Pty | None = None                               # the CLI that needs a terminal (agy)
        self.tmp: tempfile.TemporaryDirectory[str] | None = None
        self.reader: asyncio.Task[None] | None = None

    @property
    def returncode(self) -> int | None:
        """The CLI's exit code, or None while it runs (or was not started)."""
        if self.pty is not None:
            return self.pty.returncode
        return None if self.proc is None else self.proc.returncode


class Session:
    def __init__(self, sid: str, provider: str) -> None:
        self.id = sid
        self.provider = provider
        self.url = ""
        self.started = time.monotonic()
        self.state = "pending"    # pending | done | failed | expired
        self.message = ""
        self.p = _Proc()


Spawner = Callable[[str, str], Any]


class CliLogin:
    """Sign-ins for one CLI provider in this process (the sidecar, or a server running outside Docker)."""

    def __init__(self, provider: str, *, command: list[str] | None = None, token_dir: Callable[[], Path] | None = None,
                 env: Callable[[], dict[str, str]] | None = None, ttl: float = TTL, url_timeout: float = URL_TIMEOUT,
                 finish_timeout: float = FINISH_TIMEOUT) -> None:
        if provider not in URL_PREFIXES:
            raise ValueError(provider)
        self.provider = provider
        self._command = command
        self._token_dir = token_dir or cli_manage._agy_token_dir
        self._env = env
        self.ttl, self.url_timeout, self.finish_timeout = ttl, url_timeout, finish_timeout
        self._session: Session | None = None
        self._lock = asyncio.Lock()

    # ---- helpers
    def _argv(self) -> list[str]:
        if self._command:
            return list(self._command)
        if self.provider == cli_manage.CLAUDE:
            path = cli_manage.find_command(["claude"], "CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND")
            if not path:
                raise ProviderError(t("The CLI is not installed"), status=503)
            return [path, "auth", "login"]
        path = cli_manage.find_command(["agy"], "AGY_COMMAND")
        if not path:
            raise ProviderError(t("The CLI is not installed"), status=503)
        return [path]

    def _child_env(self) -> dict[str, str]:
        if self._env:
            return self._env()
        if self.provider == cli_manage.CLAUDE:
            return {**cli_manage.claude_env(), "BROWSER": "/bin/false"}
        return {**cli_manage.agy_env(dict(os.environ)), "TERM": "xterm-256color", "BROWSER": "/bin/false"}

    def agy_signed_in(self) -> bool:
        return any((self._token_dir() / n).is_file() for n in cli_manage._AGY_TOKENS)

    def _expire(self) -> None:
        s = self._session
        if s and s.state == "pending" and time.monotonic() - s.started > self.ttl:
            s.state = "expired"
            self._stop(s)

    def _stop(self, s: Session) -> None:
        p = s.p
        if p.pty is not None:
            p.pty.close()           # the exit code stays readable
        elif p.proc and p.proc.returncode is None:
            processes.stop_group(p.proc)
        if p.reader:
            p.reader.cancel()
        if p.tmp:
            with contextlib.suppress(OSError):
                p.tmp.cleanup()
            p.tmp = None

    async def _spawn(self, s: Session) -> None:
        argv, env = self._argv(), self._child_env()
        s.p.tmp = tempfile.TemporaryDirectory(prefix="cli-login-")
        if self.provider == cli_manage.CLAUDE:
            proc = await processes.start_group_async(
                argv, cwd=s.p.tmp.name, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            s.p.proc = proc

            async def read() -> None:
                assert proc.stdout is not None
                while chunk := await proc.stdout.read(4096):
                    s.p.buf = (s.p.buf + chunk.decode("utf-8", "replace"))[-65536:]
            s.p.reader = asyncio.create_task(read())
            return
        pty = Pty(rows=60, columns=4000)   # wide: the address stays on one line
        await pty.spawn(argv, cwd=s.p.tmp.name, env=env)
        s.p.pty, s.p.proc = pty, pty.process

        def on_output(data: bytes) -> None:
            if not data:
                return
            if b"\x1b[6n" in data:            # the UI asks where the cursor is; answer so it goes on drawing
                with contextlib.suppress(OSError):
                    pty.write(b"\x1b[1;1R")
            s.p.buf = (s.p.buf + data.decode("utf-8", "replace"))[-65536:]
        pty.on_output(on_output)

    async def _write(self, s: Session, text: str) -> None:
        p = s.p
        if p.pty is not None:
            p.pty.write(text.encode())
        elif p.proc and p.proc.stdin:
            p.proc.stdin.write(text.encode())
            await p.proc.stdin.drain()

    async def _wait_for(self, s: Session, test: Callable[[], bool], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if test():
                return True
            if s.p.returncode is not None:
                await asyncio.sleep(0.1)       # let the reader take the last output
                return test()
            await asyncio.sleep(0.1)
        return test()

    # ---- the API
    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self._session:
                self._stop(self._session)
                self._session = None
            if self.provider == cli_manage.AGY and self.agy_signed_in():
                raise ProviderError(t("Already signed in. Sign out first to use another account"), status=409)
            s = Session(secrets.token_urlsafe(18), self.provider)
            try:
                await self._spawn(s)
                if self.provider == cli_manage.AGY:
                    menu = await self._wait_for(s, lambda: "login method" in clean(s.p.buf).lower(), self.url_timeout)
                    if not menu:                  # never press a key in a screen that is not the sign-in menu
                        raise ProviderError(t("The CLI did not show its sign-in screen. Try again in a moment"), status=502)
                    await asyncio.sleep(0.3)
                    await self._write(s, "\r")    # 1. Google OAuth (the first choice)
                got = await self._wait_for(s, lambda: find_url(self.provider, s.p.buf) is not None, self.url_timeout)
                if not got:
                    raise ProviderError(t("The CLI did not give a sign-in address. Try again in a moment"), status=502)
            except OSError as e:
                self._stop(s)
                raise ProviderError(t("Could not run the CLI ({error})", error=type(e).__name__), status=502) from None
            except BaseException:
                self._stop(s)
                raise
            s.url = find_url(self.provider, s.p.buf) or ""
            s.p.buf = ""                          # from here on only what comes after the code matters
            self._session = s
            return {"session": s.id, "url": s.url, "expires_in": int(self.ttl)}

    def _get(self, sid: str) -> Session:
        self._expire()
        s = self._session
        if not s or not secrets.compare_digest(s.id.encode(), str(sid).encode()):
            raise ProviderError(t("This sign-in is no longer open. Start again"), status=404)
        return s

    async def finish(self, sid: str, code: str) -> dict[str, Any]:
        async with self._lock:
            s = self._get(sid)
            if s.state == "done":
                return self.status(sid)
            if s.state != "pending":
                raise ProviderError(t("This sign-in is no longer open. Start again"), status=410)
            value = normalize_code(self.provider, code)
            await self._write(s, value)
            if self.provider == cli_manage.AGY:
                await asyncio.sleep(0.2)
            await self._write(s, "\r" if self.provider == cli_manage.AGY else "\n")
            if self.provider == cli_manage.CLAUDE:
                ok = await self._wait_for(s, lambda: s.p.returncode is not None, self.finish_timeout)
                ok = ok and s.p.returncode == 0
            else:
                failed = re.compile(r"invalid|failed|error|expired", re.I)
                await self._wait_for(s, lambda: self.agy_signed_in() or bool(failed.search(clean(s.p.buf))), self.finish_timeout)
                ok = self.agy_signed_in()
            s.p.buf = ""
            self._stop(s)
            cli_manage.forget_status(self.provider)
            if not ok:
                s.state = "failed"
                s.message = t("The code was not accepted. It may be incomplete or expired. Start the sign-in again")
                raise ProviderError(s.message, status=400)
            s.state = "done"
            return self.status(sid)

    def status(self, sid: str) -> dict[str, Any]:
        s = self._get(sid)
        left = max(0, int(self.ttl - (time.monotonic() - s.started))) if s.state == "pending" else 0
        return {"state": s.state, "expires_in": left, **({"message": s.message} if s.message else {})}

    async def cancel(self, sid: str) -> dict[str, Any]:
        async with self._lock:
            s = self._session
            if s and secrets.compare_digest(s.id.encode(), str(sid).encode()):
                self._stop(s)
                self._session = None
                return {"ok": True}
            return {"ok": False}

    def close(self) -> None:
        if self._session:
            self._stop(self._session)
            self._session = None
