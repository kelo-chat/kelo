"""Run as many copies of the assistant on one computer as the computer can hold.

Each copy has its own server container (own port, own access token, own database and keys, own files) built from the same
image, so nothing is shared between copies. Copies are separate `docker compose` projects named `assistant-u-<name>`;
their settings live in `instances/<name>/env`. The stack you set up first (`.env`) is the "main" copy and is managed the
same way when a command is given no name. The number of copies, and of devices signed in to each, is not limited by this code.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cli import ensure_keyring_token, ensure_secret_key, ensure_token, read_env, write_env

NAME = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
SERVER_PORT_BASE = 8100
VIEWER_PORT_BASE = 6100
MAIN = "main"
IMAGE = "assistant:local"
WORKROOM_IMAGE = "assistant-workroom:local"   # the work room (runs code); also shared by every copy
# compose.yml names every image of ours through one variable each, so a copy can be held at older images (after a rollback, see
# updates.py) by setting the variable in its env file. Unset (the normal case) = the shared tag, or that variable from the shell.
IMAGE_VARS: dict[str, tuple[str, tuple[str, ...]]] = {
    "KELO_IMAGE": (IMAGE, ("server", "browser", "egress", "vpn")),
    "KELO_WORKROOM_IMAGE": (WORKROOM_IMAGE, ("workroom",)),
    "KELO_CLI_CLAUDE_IMAGE": ("assistant-cli-claude:local", ("claude-cli",)),
    "KELO_CLI_AGY_IMAGE": ("assistant-cli-agy:local", ("agy-cli",)),
}
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class InstanceError(Exception):
    """Something the administrator can fix; the message says how."""


@dataclass(slots=True)
class Instance:
    name: str
    env: Path
    server_port: int
    viewer_port: int
    browser: bool
    public_url: str
    connect_mode: str = ""       # how phones reach it from outside: kelo, tailscale, funnel, cloudflare, direct, proxy or "" (not set up)
    connect_url: str = ""

    @property
    def is_main(self) -> bool:
        return self.name == MAIN

    @property
    def project(self) -> str | None:
        return None if self.is_main else f"assistant-u-{self.name}"

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    @property
    def address(self) -> str:
        """Where a phone should open it: the outside address when one is set up, else the local one."""
        return self.connect_url or self.local_url


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _disk_free(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


class Instances:
    def __init__(self, root: str | Path, *, run: Callable[..., Any] = subprocess.run,
                 port_free: Callable[[int], bool] = _port_free, out: Callable[[str], None] = print, main_env: Path | None = None,
                 disk_free: Callable[[Path], int | None] = _disk_free):
        self.root = Path(root).resolve()
        self.main_env = main_env
        self.disk_free = disk_free
        self.dir = self.root / "instances"
        self.run, self.port_free, self.out = run, port_free, out
        self.skip_services: frozenset[str] = frozenset()   # never restarted by start() (the updater container sets {"updater"})
        # called with the copy right before start() brings it up (the CLI sets connect.kelo_refresh: a parked or lost Kelo address
        # tunnel is made again); its note, if any, is printed. Never stops the start.
        self.on_start: Callable[[Instance], str | None] | None = None

    # ------------------------------------------------------------------ files
    def env_path(self, name: str) -> Path:
        return (self.main_env or self.root / ".env") if name == MAIN else self.dir / name / "env"

    def names(self) -> list[str]:
        """The extra copies (not the main one)."""
        return sorted(p.name for p in self.dir.iterdir() if (p / "env").is_file()) if self.dir.is_dir() else []

    def load(self, name: str | None = None) -> Instance:
        name = name or MAIN
        if name != MAIN and (not NAME.match(name) or not self.env_path(name).is_file()):
            raise InstanceError(f"no such instance: {name!r}. Existing: {', '.join(self.names()) or '(none)'}")
        env = self.env_path(name)
        if name == MAIN and not env.is_file():
            raise InstanceError("no .env here: run `assistant setup` in the folder that holds compose.yml first")
        return Instance(name, env, int(read_env(env, "SERVER_PORT") or 8080), int(read_env(env, "VIEWER_PORT") or 6080),
                        name == MAIN or "BROWSER_CDP_HOST=" not in env.read_text().splitlines(),
                        read_env(env, "ASSISTANT_PUBLIC_URL"), read_env(env, "CONNECT_MODE"), read_env(env, "CONNECT_URL"))

    def token(self, name: str | None = None) -> str:
        return read_env(self.load(name).env, "ASSISTANT_TOKEN")

    def _next_port(self, base: int, avoid: frozenset[int] = frozenset()) -> int:
        used: set[int] = set(avoid)
        for n in [MAIN, *self.names()]:
            try:
                inst = self.load(n)
            except InstanceError:
                continue
            used |= {inst.server_port, inst.viewer_port}
        port = base
        while port in used or not self.port_free(port):
            port += 1
            if port > 65535:
                raise InstanceError("this computer has no free port left")
        return port

    # ------------------------------------------------------------------ commands
    def create(self, name: str, *, browser: bool = True, domain: str | None = None) -> Instance:
        if not NAME.match(name or "") or name == MAIN:
            raise InstanceError("name: 3-32 characters, lower-case letters, digits and '-', starting with a letter (example: alice)")
        if self.env_path(name).exists():
            raise InstanceError(f"{name!r} already exists")
        if domain is not None and not DOMAIN.match(domain):
            raise InstanceError(f"{domain!r} is not a domain name (example: example.com)")
        server_port = self._next_port(SERVER_PORT_BASE)
        viewer_port = self._next_port(VIEWER_PORT_BASE, frozenset({server_port}))   # never the same number as the server port
        env = self.env_path(name)
        env.parent.mkdir(parents=True, exist_ok=True)
        env.parent.chmod(0o700)
        write_env(env, {"ENV_FILE": f"instances/{name}/env", "SERVER_PORT": str(server_port), "VIEWER_PORT": str(viewer_port),
                        "BROWSER": read_env(self.root / ".env", "BROWSER") or "chromium"})
        if not browser:
            write_env(env, {"BROWSER_CDP_HOST": ""})
        if domain:
            write_env(env, {"ASSISTANT_PUBLIC_URL": f"https://{name}.{domain}"})
        ensure_token(env)
        ensure_secret_key(env)
        ensure_keyring_token(env)   # the key container's shared secret (keyring_service.py)
        return self.load(name)

    def compose(self, inst: Instance, *args: str) -> list[str]:
        base = ["docker", "compose", "-f", str(self.root / "compose.yml")]
        if inst.project:
            base += ["-p", inst.project, "--env-file", str(inst.env)]
        return [*base, *args]

    def shared_image(self, var: str) -> str:
        """The tag every copy runs when it is not held back: compose.yml's default, or the same variable from the shell."""
        return os.environ.get(var) or IMAGE_VARS[var][0]

    def image_env(self, inst: Instance) -> dict[str, str]:
        """Each KELO_*_IMAGE as compose should resolve it for this copy (a hold in its env file wins), given explicitly so a shell
        variable can never point one copy at another copy's hold."""
        return {var: read_env(inst.env, var) or self.shared_image(var) for var in IMAGE_VARS}

    def compose_env(self, inst: Instance) -> dict[str, str]:
        return {**os.environ, **self.image_env(inst)}

    def ensure_image(self) -> None:
        for image, service in ((self.shared_image("KELO_IMAGE"), "server"), (self.shared_image("KELO_WORKROOM_IMAGE"), "workroom")):
            have = self.run(["docker", "image", "inspect", image], cwd=self.root, capture_output=True)
            if have.returncode != 0:
                self.out(f"Building the image {image} once (all copies share it)...")
                self.run(["docker", "compose", "-f", str(self.root / "compose.yml"), "build", service], cwd=self.root, check=True)

    def start(self, name: str | None = None, *, build_if_missing: bool = True) -> Instance:
        inst = self.load(name)
        if build_if_missing:
            self.ensure_image()
        if self.on_start is not None:
            try:
                if note := self.on_start(inst):
                    self.out(note)
            except Exception as e:  # noqa: BLE001 - the copy starts either way
                self.out(f"could not check the Kelo address: {type(e).__name__}")
        # without a browser only the server (and the tunnel, when there is one) is started, not everything it could depend on
        sidecar = {"cloudflare": "cloudflared", "kelo": "kelo-tunnel", "direct": "caddy"}.get(inst.connect_mode)
        only = [] if inst.browser else ["--no-deps", "server", *([sidecar] if sidecar else []), "workroom", "vpn"]
        if self.skip_services and not only:   # the updater restarting its own copy names every service but itself
            r = self.run(self.compose(inst, "config", "--services"), cwd=self.root, capture_output=True, text=True,
                         env=self.compose_env(inst))
            only = [s for s in (r.stdout or "").split() if s not in self.skip_services]
            if r.returncode != 0 or not only:
                raise InstanceError(f"could not list the services of {inst.name} (docker compose config failed)")
        self.run(self.compose(inst, "up", "-d", "--no-build", *only), cwd=self.root, check=True, env=self.compose_env(inst))
        return inst

    def stop(self, name: str | None = None) -> None:
        self.run(self.compose(self.load(name), "stop"), cwd=self.root, check=True)

    def delete(self, name: str) -> None:
        """The copy goes completely: its containers, its volumes (chats, keys, files) and its settings folder."""
        if name == MAIN:
            raise InstanceError("the main copy is not deleted this way (uninstall Kelo with the installer's --uninstall instead)")
        inst = self.load(name)
        # every profile, so the networks of profile-only services (updater-net, updater-out) go too and never pile up in
        # Docker's limited address pools
        self.run(self.compose(inst, "--profile", "*", "down", "--volumes", "--remove-orphans"), cwd=self.root, check=True)
        for repo in sorted({self.shared_image(v).rpartition(":")[0] or self.shared_image(v) for v in IMAGE_VARS}):
            # its rollback tags (updates.py) would otherwise keep its older images on the disk for good; only the tags go
            r = self.run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", "--filter", f"reference={repo}:rollback-{name}-*"],
                         cwd=self.root, capture_output=True, text=True)
            tags = (r.stdout or "").split() if r.returncode == 0 else []
            if tags:
                self.run(["docker", "image", "rm", *tags], cwd=self.root, capture_output=True, text=True)
        shutil.rmtree(self.dir / name)

    def running(self, name: str | None = None) -> list[str]:
        r = self.run(self.compose(self.load(name), "ps", "--status", "running", "--services"), cwd=self.root, capture_output=True, text=True)
        return [s for s in (r.stdout or "").split() if s] if r.returncode == 0 else []

    # ------------------------------------------------------------------ front door config for a reverse proxy you run yourself
    def routes(self, domain: str, fmt: str = "cloudflared") -> str:
        if not DOMAIN.match(domain or ""):
            raise InstanceError(f"{domain!r} is not a domain name (example: example.com)")
        insts = [self.load(n) for n in self.names()]
        if fmt == "caddy":
            return "\n\n".join(f"{i.name}.{domain} {{\n\treverse_proxy 127.0.0.1:{i.server_port} {{\n\t\tflush_interval -1\n\t}}\n}}" for i in insts) + "\n"
        if fmt != "cloudflared":
            raise InstanceError("format must be cloudflared or caddy")
        rules = "".join(f"  - hostname: {i.name}.{domain}\n    service: http://127.0.0.1:{i.server_port}\n" for i in insts)
        return f"ingress:\n{rules}  - service: http_status:404\n"
