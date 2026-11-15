"""`assistant setup` in a terminal: a guided, four-step first run (and re-run = update).

    1/4  Check the server      Docker + Compose installed and running, CPUs / RAM / free disk
    2/4  Size                  minimum / recommended / no limit, checked against what the server's computer really has
    3/4  Start                 docker compose up -d --build with a spinner and a short live log, then a health check
    4/4  Phone                 a Kelo address (https://<name>.kelo.chat, offered for now) or later, then a QR code to sign a phone in

The AI model is not asked here: it is chosen in the phone app once the phone is connected (the app leads there while the
server has none). Model settings already in .env are left exactly as they are.

The logic lives in plain functions and in `Wizard`, which talks to the person only through a small UI object (`RichUI` here, a
scripted fake in the tests) and to the computer only through `run` / `stream` / `get` (fakes in the tests). Nothing secret is ever
printed: the access token and encryption key are created, never shown.

Text is English in the code, passed through `i18n.t()` like the server's own strings, and shown in the system language when
src/assistant/locales/ has it (KELO_LANG=<code> overrides). tools/i18n_check.py finds these templates like any other.
"""

from __future__ import annotations

import locale
import os
import platform
import re
import shutil
import subprocess
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import i18n, profiles
from .cli import (
    CLIS,
    ensure_keyring_token,
    ensure_runner_token,
    ensure_secret_key,
    ensure_token,
    pick_range,
    read_env,
    write_env,
)
from .i18n import N_

GB = 1024**3
STEPS = 4

# ------------------------------------------------------------------------------------------------------------- language
def _windows_ui_language() -> str:
    try:
        import ctypes
        return locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")  # type: ignore[attr-defined]
    except (AttributeError, OSError, ImportError):
        return ""


def detect_lang(environ: dict[str, str] | None = None, getlocale: Callable[[], tuple[str | None, str | None]] | None = None,
                windows: Callable[[], str] = _windows_ui_language) -> str:
    """The terminal speaks English whatever the system language (the owner's decision, 2026-11-08). KELO_LANG=<code> still
    switches it to one of Kelo's languages. `getlocale` and `windows` are kept for callers that pass them; they are not used."""
    env = os.environ if environ is None else environ
    tag = (env.get("KELO_LANG") or "").strip().split(".")[0].split("@")[0].replace("_", "-")
    return (i18n.pick(tag) or i18n.DEFAULT) if tag else i18n.DEFAULT


def translate(text: str, lang: str, **kw: Any) -> str:
    return i18n.t(text, lang=lang, **kw)


def kelo_cmd() -> str:
    return os.environ.get("KELO_CMD") or "assistant"      # the installer puts a `kelo` launcher on PATH


# ------------------------------------------------------------------------------------------------------------- closing text
def _cells(s: str) -> int:
    """Terminal width of a label (CJK letters take two cells), so translated labels still line the commands up."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def command_rows(rows: list[tuple[str, str]], indent: int = 4) -> list[str]:
    """One command per line, indented; labels padded so the commands start in one column."""
    w = max((_cells(label) for label, _ in rows), default=0)
    return [" " * indent + (label + " " * (w - _cells(label) + 3) if w else "") + command for label, command in rows]


def _flag(inst: Any) -> str:
    return "" if inst.is_main else f" --instance {inst.name}"


def reach_rows(lang: str, inst: Any, cmd: str, *, funnel: bool = False) -> list[tuple[str, str]]:
    """The ways to open a copy to a phone anywhere, as (label, command)."""
    def t(text: str) -> str:
        return translate(text, lang)

    flag = _flag(inst)
    rows = [(t("Kelo address (offered for now)"), f"{cmd} connect kelo{flag} --name <name>"),
            ("Tailscale", f"{cmd} connect tailscale{flag}")]
    if funnel:
        rows.append(("Tailscale Funnel", f"{cmd} connect funnel{flag}"))
    rows.append((t("Own domain"), f"{cmd} connect domain{flag} --host <address> --proxy"))
    return rows


def closing_lines(lang: str, inst: Any, *, running: bool = True, paired: bool = False, system: str | None = None,
                  cmd: str | None = None) -> list[str]:
    """What `instance create` ends with: where it runs, then each next command on its own indented line."""
    def t(text: str, **kw: Any) -> str:
        return translate(text, lang, **kw)

    cmd = cmd or kelo_cmd()
    if not running:
        start = "docker compose up -d" if inst.is_main else f"{cmd} instance start {inst.name}"
        out = [t('Kelo "{name}" is ready but not started. Start it with:', name=inst.name), f"    {start}", ""]
    elif inst.connect_url:
        out = [t('Kelo "{name}" is running: {url}', name=inst.name, url=inst.connect_url), ""]
    else:
        where = t("this Mac only") if (system or platform.system()) == "Darwin" else t("only on the computer the server runs on")
        out = [t('Kelo "{name}" is running: {url} ({where})', name=inst.name, url=inst.local_url, where=where), ""]
    if inst.connect_url:
        out.append(t("Connect another phone (shows a QR code):") if paired else t("Connect a phone (shows a QR code):"))
    else:
        out += [t("Reach it from your phone (pick one):"), *command_rows(reach_rows(lang, inst, cmd)), "",
                t("Then connect a phone (shows a QR code):")]
    out.append(f"    {cmd} phone add{_flag(inst)}")
    return out


def phone_step(ui: Any, mgr: Any, name: str | None = None, *, cmd: str | None = None, post: Callable[..., Any] | None = None,
               start: bool = True, suggestion: str = "") -> bool:
    """Where the phone reaches Kelo (a Kelo address now, Tailscale / own domain later, or not now), then a QR code to
    sign a phone in. Shared by `assistant setup` (4/4) and `assistant instance create` in a terminal. True if a QR was shown. Every device that signs in has the same rights."""
    from . import connect
    from .instances import InstanceError
    from .kelo_names import name_problem

    def t(text: str, **kw: Any) -> str:
        return translate(text, ui.lang, **kw)

    cmd = cmd or kelo_cmd()
    inst = mgr.load(name)
    options = []
    if inst.connect_url:
        options.append(("k", t("Keep {url}", url=inst.connect_url), ""))
    shown_name = next((n for n in (read_env(inst.env, "KELO_NAME"), suggestion) if n and not name_problem(n)), "<name>")
    # the phone app talks to a server over https only, so without an address it cannot be signed in
    options += [("1", t("Kelo address (offered for now): {host}", host=f"{shown_name}.kelo.chat"), t("ready in a minute; works from anywhere")),
                ("2", t("Tailscale or my own domain"), t("needs setup on your side; shows the commands to run")),
                ("3", t("Skip for now"), t("the phone can't connect until an address is set"))]
    pick = ui.choose(t("Where should your phone reach Kelo?"), options, "k" if inst.connect_url else "1")
    if pick == "1":
        suggestion = suggestion if suggestion and not name_problem(suggestion) else ""
        while True:
            wanted = ui.ask(t("Name for your address (3-30 letters, digits or '-')"), read_env(inst.env, "KELO_NAME") or suggestion).strip().lower()
            problem = name_problem(wanted)
            if not problem:
                try:
                    url = connect.kelo_connect(mgr, inst, wanted, post=post)
                    if start:
                        mgr.start(inst.name, build_if_missing=False)
                    ui.ok(t("Kelo is open at {url}", url=url))
                    ui.info(t("Traffic to this address passes through Cloudflare. For end-to-end encryption use Tailscale or your own domain."))
                    break
                except InstanceError as e:
                    problem = str(e)
            ui.warn(t("Could not get that address: {why}", why=problem))
            if not ui.confirm(t("Try another name?"), True):
                break
    elif pick == "2":
        ui.info(t("Later, one of:"))
        for line in command_rows(reach_rows(ui.lang, inst, cmd, funnel=True)[1:], indent=2):
            ui.info(line)
    inst = mgr.load(inst.name)
    if start and inst.connect_url and ui.confirm(t("Sign a phone in now?"), True):
        try:
            link, _ = connect.phone_add(mgr, inst, post=post or connect._post)
            ui.qr(link)
            ui.info(t("Scan with the phone's camera, or open the link on the phone. It works once and expires in 10 minutes."))
            return True
        except InstanceError as e:
            ui.warn(t("Could not make a phone link: {why}", why=str(e)))
    return False


# ------------------------------------------------------------------------------------------------------------- small helpers
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")


def clean_line(line: str, width: int = 100) -> str:
    """A docker log line fit for a one-line tail: no colour codes, no carriage returns, cut to the width."""
    s = ANSI.sub("", line).rstrip()
    return s if len(s) <= width else s[: width - 1] + "…"


# ------------------------------------------------------------------------------------------------------------- the computer
@dataclass(slots=True)
class DockerCheck:
    installed: bool = False
    version: str = ""
    compose: str = ""
    running: bool = False
    permission: bool = True
    cpus: int | None = None
    ram: int | None = None

    @property
    def ok(self) -> bool:
        return self.installed and bool(self.compose) and self.running and self.permission


def check_docker(run: Callable[..., Any], which: Callable[[str], str | None] = shutil.which) -> DockerCheck:
    d = DockerCheck()
    if not which("docker"):
        return d
    d.installed = True
    r = run(["docker", "version", "--format", "{{.Client.Version}}"], capture_output=True, text=True)
    d.version = (r.stdout or "").strip().splitlines()[0] if (r.stdout or "").strip() else ""
    r = run(["docker", "compose", "version", "--short"], capture_output=True, text=True)
    d.compose = (r.stdout or "").strip() if r.returncode == 0 else ""
    r = run(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"], capture_output=True, text=True)
    if r.returncode == 0:
        d.running = True
        try:
            cpus, mem = (r.stdout or "").split()[:2]
            d.cpus, d.ram = int(cpus), int(mem)
        except (ValueError, TypeError):
            pass
    else:
        err = (r.stderr or "").lower()
        if "permission denied" in err:
            d.running, d.permission = True, False
    return d


def docker_help(d: DockerCheck, system: str) -> tuple[str, list[str]]:
    """(headline, lines) for the one friendly screen shown when Docker is not ready. Lines that start with two spaces are
    links or commands and stay as they are; the rest are English templates, translated where they are shown."""
    if not d.installed or not d.compose:
        head = N_("Docker is not installed") if not d.installed else N_("Docker Compose v2 is missing")
        first = N_("Kelo runs in Docker, so Docker comes first.")
        if system == "Darwin":
            return head, [first, N_("Install Docker Desktop (free for personal use):"), "  https://docs.docker.com/desktop/setup/install/mac-install/"]
        if system == "Windows":
            return head, [first, N_("Install Docker Desktop (free for personal use):"),
                          "  https://docs.docker.com/desktop/setup/install/windows-install/", N_("Turn on WSL 2 when Docker Desktop asks for it.")]
        return head, [first, N_("Install Docker Engine and the Compose plugin:"), "  https://docs.docker.com/engine/install/"]
    if not d.permission:
        return N_("Docker is running, but this user may not use it"), [
            N_("Let this user use Docker, then log out and back in:"), "  sudo usermod -aG docker $USER"]
    if system == "Linux":
        return N_("Docker is installed but not running"), [N_("Start it with:"), "  sudo systemctl start docker"]
    return N_("Docker is installed but not running"), [N_("Open Docker Desktop and wait until it says it is running.")]


@dataclass(slots=True)
class Machine:
    cpus: int | None
    ram: int | None
    disk_free: int | None


def disk_free(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


# What a computer needs for each size (the numbers on the Kelo site). The containers' own caps come from `profiles`.
@dataclass(frozen=True, slots=True)
class Tier:
    key: str
    label: str
    ram: int | None
    cpus: float | None
    disk: int | None


TIERS = (     # the menu order, the same in `limits --choose` and `instance create`; the numbers live in profiles.TOTALS
    Tier("recommended", N_("Recommended"), *profiles.TOTALS["recommended"]),
    Tier("minimum", N_("Minimum"), *profiles.TOTALS["minimum"]),
    Tier("unlimited", N_("No limit"), None, None, None),
)


def tier(key: str) -> Tier:
    return next(t for t in TIERS if t.key == key)


def shortfalls(t: Tier, m: Machine) -> list[tuple[str, str, str]]:
    """(what, have, need) for each resource this computer has less of than the size expects. Unknown numbers are not judged."""
    out = []
    # Docker reports a little less RAM than is installed (the kernel keeps some), so 5% of slack before calling it short
    if t.ram and m.ram is not None and m.ram < t.ram * 0.95:
        out.append((N_("RAM"), profiles.gb(m.ram), profiles.gb(t.ram)))
    if t.cpus and m.cpus is not None and m.cpus < t.cpus:
        out.append((N_("CPUs"), str(m.cpus), f"{t.cpus:g}"))
    if t.disk and m.disk_free is not None and m.disk_free < t.disk:
        out.append((N_("free disk"), profiles.gb(m.disk_free), profiles.gb(t.disk)))
    return out


def suggested_tier(m: Machine) -> str:
    return "recommended" if not shortfalls(tier("recommended"), m) else "minimum"


def merge_profiles(existing: str, clis: list[str]) -> str:
    """COMPOSE_PROFILES with the chosen CLI containers, keeping every other profile (kelo, cloudflare, ...) as it is."""
    keep = [p for p in existing.split(",") if p and p not in CLIS]
    return ",".join(keep + [c for c in CLIS if c in clis])


# ------------------------------------------------------------------------------------------------------------- talking to people
class UI(Protocol):
    lang: str
    def banner(self, rerun: bool) -> None: ...
    def step(self, n: int, title: str) -> None: ...
    def ok(self, text: str) -> None: ...
    def warn(self, text: str) -> None: ...
    def info(self, text: str) -> None: ...
    def fail(self, title: str, lines: list[str]) -> None: ...
    def choose(self, question: str, options: list[tuple[str, str, str]], default: str) -> str: ...
    def ask(self, question: str, default: str = "") -> str: ...
    def confirm(self, question: str, default: bool) -> bool: ...
    def live(self, title: str, run: Callable[[Callable[[str], None]], int]) -> int: ...
    def qr(self, link: str) -> None: ...
    def summary(self, url: str, commands: list[tuple[str, str]], note: str) -> None: ...


def stream_process(cmd: list[str], cwd: Path, on_line: Callable[[str], None]) -> int:
    """Run a command, hand each output line to on_line, return its exit code."""
    env = {**os.environ, "BUILDKIT_PROGRESS": "plain", "COMPOSE_ANSI": "never"}
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", env=env)
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line)
    return proc.wait()


@dataclass
class Wizard:
    env_file: Path
    ui: UI
    run: Callable[..., Any] = subprocess.run
    stream: Callable[[list[str], Path, Callable[[str], None]], int] = stream_process
    get: Callable[..., Any] | None = None
    post: Callable[..., Any] | None = None
    which: Callable[[str], str | None] = shutil.which
    system: str = field(default_factory=platform.system)
    free_disk: Callable[[Path], int | None] = disk_free
    sleep: Callable[[float], None] = time.sleep
    start: bool = True
    health_timeout: float = 180.0
    browser: str | None = None
    clis: list[str] | None = None
    profile: str | None = None

    def t(self, text: str, **kw: Any) -> str:
        return translate(text, self.ui.lang, **kw)

    @property
    def cmd(self) -> str:
        return kelo_cmd()

    @property
    def root(self) -> Path:
        return self.env_file.resolve().parent

    def manager(self) -> Any:
        from .instances import Instances
        return Instances(self.root, run=self.run, out=lambda s: self.ui.info(s), main_env=self.env_file.resolve())

    # -------------------------------------------------------------------------------------------------------- the run
    def __call__(self) -> int:
        rerun = bool(read_env(self.env_file, "ASSISTANT_TOKEN"))
        self.ui.banner(rerun)
        try:
            machine = self.check()
            if machine is None:
                return 1
            self.size(machine, rerun)
            if self.clis is not None:    # only from the command line (`--cli`); the guided screen does not offer them
                write_env(self.env_file, {"COMPOSE_PROFILES": merge_profiles(read_env(self.env_file, "COMPOSE_PROFILES"), self.clis)})
            if self.start and not self.up(rerun):
                return 1
            if not self.start:
                self.ui.step(3, self.t("Start Kelo"))
                self.ui.info(self.t("Not started (--no-start). Start it with: {command}", command="docker compose up -d --build"))
            url = self.phone()
        except (KeyboardInterrupt, EOFError):
            self.ui.warn(self.t("Stopped. Steps already finished are kept; run `{command}` again any time.", command=f"{self.cmd} setup"))
            return 130
        cmd = self.cmd
        self.ui.summary(url, [(f"{cmd} status", self.t("status of every copy")),
                              (f"{cmd} phone add", self.t("sign another phone in")),
                              (f"{cmd} limits --choose", self.t("change how much it may use")),
                              (f"{cmd} setup", self.t("change these answers"))],
                        self.t("Choose the AI model in the Kelo app after connecting the phone."))
        return 0

    # -------------------------------------------------------------------------------------------------------- 1/4
    def check(self) -> Machine | None:
        self.ui.step(1, self.t("Check the Kelo server"))
        d = check_docker(self.run, self.which)
        if not d.ok:
            head, lines = docker_help(d, self.system)
            self.ui.fail(self.t(head), [l if l.startswith("  ") else self.t(l) for l in lines]
                         + ["", self.t("Then run the same command again; it picks up where it stopped.")])
            return None
        self.ui.ok(f"Docker {d.version or '?'}")
        self.ui.ok(f"Docker Compose {d.compose}")
        m = Machine(d.cpus, d.ram, self.free_disk(self.root))
        self.ui.ok(self.t("This computer: RAM {ram} · CPU {cpus} · {disk} free disk",
                          ram=profiles.gb(m.ram), cpus=m.cpus or "?", disk=profiles.gb(m.disk_free)))
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        made = [ensure_token(self.env_file), ensure_secret_key(self.env_file), ensure_runner_token(self.env_file), ensure_keyring_token(self.env_file)]
        if not read_env(self.env_file, "BROWSER") or self.browser:
            write_env(self.env_file, {"BROWSER": self.browser or "chromium"})
        if any(made):
            self.ui.ok(self.t("Created the access token and the encryption key in {env} (kept private, never shown).", env=self.env_file.name))
        return m

    # -------------------------------------------------------------------------------------------------------- 2/4
    def size(self, m: Machine, rerun: bool) -> None:
        from . import limits

        self.ui.step(2, self.t("Size"))
        mgr = self.manager()
        inst = mgr.load()
        options = []
        for i, tr in enumerate(TIERS):
            if tr.key == "unlimited":
                spec = self.t("everything this computer has (RAM {ram} · CPU {cpus} · {disk} free)",
                              ram=profiles.gb(m.ram), cpus=m.cpus or "?", disk=profiles.gb(m.disk_free))
            else:
                spec = self.t("RAM {ram} · CPU {cpus} · disk {disk}", ram=profiles.gb(tr.ram), cpus=f"{tr.cpus:g}", disk=profiles.gb(tr.disk))
            options.append((str(i + 1), self.t(tr.label), spec))
        keys = [k for k, _, _ in limits.OPTIONS.values()]
        have = any(read_env(self.env_file, k) for k in keys)
        current = limits.current_profile(inst) if have else ""
        default = self.profile or (current if current in ("minimum", "recommended", "unlimited") else suggested_tier(m))
        if self.profile:
            pick = self.profile
        else:
            pick = self.ui.choose(self.t("How much may this Kelo use?"), options,
                                  str([tr.key for tr in TIERS].index(default) + 1))
            pick = TIERS[int(pick) - 1].key
        tr = tier(pick)
        short = shortfalls(tr, m)
        if short:
            self.ui.warn(self.t("This computer has less than {tier} asks for ({what}). Kelo still runs, but may be slow under heavy use.",
                                tier=self.t(tr.label),
                                what=", ".join(self.t("{what} {have} of {need}", what=self.t(w), have=h, need=n) for w, h, n in short)))
        set_, clear, warn = limits.prepare(mgr, limits.wanted_for(pick, inst))
        limits.save(inst, set_, clear)
        for w in warn:
            self.ui.warn(w)
        self.ui.ok(self.t("Saved: {tier}. Change it any time with `{command}`.", tier=self.t(tr.label), command=f"{self.cmd} limits --choose"))

    # -------------------------------------------------------------------------------------------------------- 3/4
    def up(self, rerun: bool) -> bool:
        self.ui.step(3, self.t("Start Kelo"))
        mgr = self.manager()
        inst = mgr.load()
        cmd = mgr.compose(inst, "up", "-d", "--build", "--remove-orphans")
        title = self.t("Updating and restarting Kelo") if rerun else self.t("Building and starting Kelo (the first time takes a few minutes)")
        code = self.ui.live(title, lambda on_line: self.stream(cmd, self.root, on_line))
        if code != 0:
            self.ui.fail(self.t("Start Kelo"), [self.t("docker compose failed (exit {code}). The last lines are above; the full log: {command}",
                                                       code=code, command="docker compose logs")])
            return False
        return self.wait_healthy(inst.local_url)

    def wait_healthy(self, url: str) -> bool:
        import httpx

        get = self.get or httpx.get
        deadline = time.monotonic() + self.health_timeout
        tries = 0
        while True:
            tries += 1
            try:
                if get(f"{url}/healthz", timeout=3).status_code == 200:
                    self.ui.ok(self.t("Kelo is running at {url}", url=url))
                    return True
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline or tries * 2 >= self.health_timeout:
                self.ui.fail(self.t("Start Kelo"), [self.t("Kelo did not answer at {url} within {secs} seconds. See: {command}",
                                                           url=url, secs=int(self.health_timeout), command="docker compose logs server")])
                return False
            self.sleep(2)

    # -------------------------------------------------------------------------------------------------------- 4/4
    def phone(self) -> str:
        self.ui.step(4, self.t("Phone"))
        mgr = self.manager()
        user = re.sub(r"[^a-z0-9-]", "", (os.environ.get("USER") or os.environ.get("USERNAME") or "").lower())
        phone_step(self.ui, mgr, cmd=self.cmd, post=self.post, start=self.start, suggestion=user)
        self.ui.info(self.t("More phones any time: {command}", command=f"{self.cmd} phone add"))
        return mgr.load().address


# ------------------------------------------------------------------------------------------------------------- the look
# Kelo, drawn in half blocks: two pixel rows per terminal line. Colours from android/design/character/kelo_idle.svg.
MASCOT = (
    "......GG......",
    ".....GGG......",
    "....BBBBBB....",
    "..BBBBBBBBBB..",
    ".BBBBBBBBBBBB.",
    ".BBKBBBBBBKBB.",
    ".BBBBBYYBBBBB.",
    "DBBBBBYYBBBBBD",
    "DBBBbbbbbbBBBD",
    ".BBbbbbbbbbBB.",
    ".BBbbbbbbbbBB.",
    "..BBBbbbbBBB..",
    "....L....L....",
    "...LL...LL....",
)
COLOURS = {"G": "#66BB6A", "B": "#29B6F6", "b": "#B3E5FC", "D": "#039BE5", "K": "#1A1A1A", "Y": "#FFB300", "L": "#90A4AE"}


def mascot_lines() -> list[list[tuple[str, str]]]:
    """Each terminal line as (character, rich style) cells."""
    rows = list(MASCOT) + (["." * len(MASCOT[0])] if len(MASCOT) % 2 else [])
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2], strict=True):
        cells = []
        for a, b in zip(top, bottom, strict=True):
            ca, cb = COLOURS.get(a), COLOURS.get(b)
            if ca and cb:
                cells.append(("▀", f"{ca} on {cb}"))
            elif ca:
                cells.append(("▀", ca))
            elif cb:
                cells.append(("▄", cb))
            else:
                cells.append((" ", ""))
        lines.append(cells)
    return lines


ACCENT = "#29B6F6"


class RichUI:
    """The terminal look: banner, step headers, checkmarks, menus, a spinner with a live log tail, a big QR code."""

    def __init__(self, lang: str, console: Any = None, input_fn: Callable[[str], str] | None = None):
        from rich.console import Console

        self.lang = lang
        self.c = console or Console(highlight=False)
        self._input = input_fn

    def t(self, text: str, **kw: Any) -> str:
        return translate(text, self.lang, **kw)

    def _read(self, prompt: Any) -> str:
        if self._input:
            self.c.print(prompt, end="")
            return self._input("")
        return self.c.input(prompt)

    def banner(self, rerun: bool) -> None:
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        grid = Table.grid(padding=(0, 3))
        grid.add_column()
        grid.add_column(vertical="middle")
        art = Text()
        if self.c.color_system:
            for i, cells in enumerate(mascot_lines()):
                for ch, style in cells:
                    art.append(ch, style)
                if i < len(mascot_lines()) - 1:
                    art.append("\n")
        words = Text()
        words.append("Kelo", style=f"bold {ACCENT}")
        words.append("  setup\n", style="dim")
        words.append(self.t("Your personal assistant, on a server you run.") + "\n\n")
        words.append(self.t("Press Enter to accept the suggestion in [brackets]. Ctrl+C stops at any time."), style="dim")
        if self.c.color_system:
            grid.add_row(art, words)
        else:
            grid.add_row(words)
        self.c.print(Panel(grid, border_style=ACCENT, padding=(1, 2), expand=False))
        if rerun:
            self.c.print(Text("↻ " + self.t("Kelo is already set up here. This run updates it and keeps your data, keys and phones."), style="yellow"))

    def step(self, n: int, title: str) -> None:
        from rich.rule import Rule
        self.c.print()
        self.c.print(Rule(f"[bold {ACCENT}]{n}/{STEPS}[/]  [bold]{title}[/]", align="left", style="dim"))

    def _line(self, mark: str, mark_style: str, text: str, style: str = "") -> None:
        from rich.table import Table
        from rich.text import Text
        row = Table.grid(padding=0)               # the mark in its own column, so a long line wraps under the text, not under it
        row.add_column(width=4, no_wrap=True)
        row.add_column()
        row.add_row(Text(mark, style=mark_style), Text(text, style=style))
        self.c.print(row)

    def ok(self, text: str) -> None:
        self._line("  ✓", "bold green", text)

    def warn(self, text: str) -> None:
        self._line("  !", "bold yellow", text, "yellow")

    def info(self, text: str) -> None:
        self._line("", "", text, "dim" if text.startswith("  ") else "")

    def fail(self, title: str, lines: list[str]) -> None:
        from rich.panel import Panel
        from rich.text import Text
        body = Text("\n".join(lines))
        self.c.print(Panel(body, title=f"[bold red]✗ {title}[/]", border_style="red", padding=(1, 2), expand=False))

    def choose(self, question: str, options: list[tuple[str, str, str]], default: str) -> str:
        from rich.text import Text
        self.c.print(Text(question, style="bold"))
        for key, label, note in options:
            mark = "›" if key == default else " "
            line = Text.assemble((f"  {mark} ", f"bold {ACCENT}"), (f"{key}) ", f"bold {ACCENT}" if key == default else "dim"),
                                 (label, "bold" if key == default else ""))
            first, *more = note.split("\n") if note else [""]
            if first:
                line.append(f"  {first}", style="dim")
            self.c.print(line)
            for extra in more:
                self.c.print(Text("         " + extra, style="yellow" if extra.endswith("(!)") or "(!)" in extra else "dim"))
        keys = [k for k, _, _ in options]
        while True:
            got = self._read(Text.assemble(("  " + self.t("Type {keys} and press Enter (Enter alone = {default}):", keys=pick_range(keys), default=default), ""),
                                           " ")).strip().lower() or default
            if got in keys:
                return got
            self.warn(self.t("Please type one of: {keys}", keys=", ".join(keys)))

    def ask(self, question: str, default: str = "") -> str:
        from rich.text import Text
        prompt = Text.assemble(("  " + question, ""), (f" [{default}]" if default else "", f"bold {ACCENT}"), ": ")
        return self._read(prompt).strip() or default

    def confirm(self, question: str, default: bool) -> bool:
        from rich.text import Text
        yes, no = self.t("yes"), self.t("no")
        hint = "Y/n" if default else "y/N"
        while True:
            got = self._read(Text.assemble(("  " + question + " ", ""), (f"[{hint}]", f"bold {ACCENT}"), ": ")).strip().lower()
            if not got:
                return default
            if got in ("y", "yes", yes, "ㅛ"):
                return True
            if got in ("n", "no", no, "ㅜ"):
                return False

    def live(self, title: str, run: Callable[[Callable[[str], None]], int]) -> int:
        from rich.console import Group
        from rich.live import Live
        from rich.spinner import Spinner
        from rich.text import Text

        tail: deque[str] = deque(maxlen=6)
        started = time.monotonic()

        def view() -> Any:
            secs = int(time.monotonic() - started)
            head = Spinner("dots", text=Text.assemble((title, "bold"), (f"  {secs // 60}:{secs % 60:02d}", "dim")), style=ACCENT)
            body = Text("\n".join("    │ " + ln for ln in tail), style="dim")
            return Group(head, body) if tail else head

        width = max(40, self.c.width - 8)
        if not self.c.is_terminal:                 # a pipe or a recording: plain lines, no redraws
            self.c.print(Text("  … " + title, style="bold"))
            code = run(lambda line: tail.append(clean_line(line, width)))
            for ln in tail:
                self.c.print(Text("    │ " + ln, style="dim"))
        else:
            with Live(view(), console=self.c, refresh_per_second=10, transient=True) as live:
                def on_line(line: str) -> None:
                    if s := clean_line(line, width):
                        tail.append(s)
                        live.update(view())
                code = run(on_line)
            if code != 0:
                for ln in tail:
                    self.c.print(Text("    │ " + ln, style="dim"))
        secs = int(time.monotonic() - started)
        if code == 0:
            self.ok(f"{title}  [{secs // 60}:{secs % 60:02d}]")
        return code

    def qr(self, link: str) -> None:
        from rich.align import Align
        from rich.text import Text

        from .connect import qr_lines, qr_matrix

        if self.c.color_system:                     # half blocks painted black on white: right on dark and light terminals
            rows = qr_matrix(link)
            if len(rows) % 2:
                rows.append([False] * len(rows[0]))
            art = Text()
            for top, bottom in zip(rows[0::2], rows[1::2], strict=True):
                for a, b in zip(top, bottom, strict=True):
                    art.append("▀", f"{'#000000' if a else '#FFFFFF'} on {'#000000' if b else '#FFFFFF'}")
                art.append("\n")
            art.right_crop(1)                       # the last newline
        else:                                       # no colours: light modules as blocks (reads right on a dark terminal)
            art = Text("\n".join(qr_lines(link)))
        self.c.print()
        self.c.print(Align.center(art))
        self.c.print(Align.center(Text(link, style=f"underline {ACCENT}")))

    def summary(self, url: str, commands: list[tuple[str, str]], note: str) -> None:
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        tab = Table.grid(padding=(0, 2))
        tab.add_column(style=f"bold {ACCENT}")
        tab.add_column(style="dim")
        for cmd, what in commands:
            tab.add_row(cmd, what)
        from rich.console import Group
        head = Text.assemble(("✓ ", "bold green"), (self.t("All set"), "bold"), "   ", (url, f"underline {ACCENT}"))
        self.c.print()
        nxt = Text.assemble(("→ ", f"bold {ACCENT}"), (note, "bold"))
        self.c.print(Panel(Group(head, Text(""), nxt, Text(""), Text(self.t("Useful later"), style="bold"), tab),
                           border_style="green", padding=(1, 2), expand=False))


def run(env_file: Path, *, start: bool = True, browser: str | None = None, clis: list[str] | None = None,
        profile: str | None = None) -> int:
    ui = RichUI(detect_lang())
    return Wizard(env_file, ui, start=start, browser=browser, clis=clis, profile=profile)()
