"""`assistant` command line: first-run setup."""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

BROWSERS = ("chromium", "chrome")

CHROME_NOTICE = """\
Google Chrome is proprietary software. Choosing it means:
  - it is downloaded from Google and built into an image ON THIS MACHINE only;
  - do not publish or share that image (redistribution terms are unclear);
  - its H.264/AAC codecs are licensed by Google for ordinary personal use, not for resale.
Chromium (open source) is the safe default and is enough for most automation."""


def pick_range(keys: list[str]) -> str:
    """'1-3', or 'k, 1-3' when a letter is also allowed."""
    digits = [k for k in keys if k.isdigit()]
    others = [k for k in keys if not k.isdigit()]
    contiguous = len(digits) > 1 and [int(d) for d in digits] == list(range(int(digits[0]), int(digits[-1]) + 1))
    span = f"{digits[0]}-{digits[-1]}" if contiguous else ", ".join(digits)
    return ", ".join([*others, span] if span else others)


def pick_prompt(keys: list[str], default: str) -> str:
    """'Type 1-3 and press Enter (Enter alone = 1): ' -- the range and what Enter alone does, so a default is not read as fixed."""
    return f"Type {pick_range(keys)} and press Enter (Enter alone = {default}): "


def choose_browser(
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> str:
    out("Default browser for the agent:")
    out("  1) Chromium   (default, open source)")
    out("  2) Google Chrome")
    while True:
        answer = ask(pick_prompt(["1", "2"], "1")).strip().lower()
        if answer in ("", "1", "chromium"):
            return "chromium"
        if answer in ("2", "chrome"):
            out(CHROME_NOTICE)
            if ask("Use Google Chrome anyway? [y/N]: ").strip().lower() in ("y", "yes"):
                return "chrome"
            out("Staying with Chromium.")
            return "chromium"
        out("Please enter 1 or 2.")


CLIS = ("claude", "agy")
CLI_LABELS = {"claude": "Claude Code (Claude Pro/Max subscription)", "agy": "agy (Google Antigravity)"}


def choose_clis(ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> list[str]:
    """Which subscription CLIs get their own container. Each is optional; any mix is fine."""
    out("Subscription CLIs (each runs in its own container; log in once after starting):")
    chosen = []
    for name in CLIS:
        if ask(f"  Install {CLI_LABELS[name]}? [y/N]: ").strip().lower() in ("y", "yes"):
            chosen.append(name)
    return chosen


def read_env(path: Path, key: str) -> str:
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""


def ensure_runner_token(path: Path) -> bool:
    """CLI_RUNNER_TOKEN: shared secret between the server and the CLI containers. Created once, never rotated."""
    if len(read_env(path, "CLI_RUNNER_TOKEN")) >= 16:
        return False
    write_env(path, {"CLI_RUNNER_TOKEN": secrets.token_urlsafe(32)})
    return True


def ensure_keyring_token(path: Path) -> bool:
    """KEYRING_TOKEN: shared secret between the server and the key container (keyring_service.py). Created once."""
    if len(read_env(path, "KEYRING_TOKEN")) >= 32:
        return False
    write_env(path, {"KEYRING_TOKEN": secrets.token_urlsafe(32)})
    return True


def ensure_updater_token(path: Path) -> bool:
    """UPDATER_TOKEN: shared secret between the main copy's server and the updater container (updater_service.py). Created once."""
    if len(read_env(path, "UPDATER_TOKEN")) >= 16:
        return False
    write_env(path, {"UPDATER_TOKEN": secrets.token_urlsafe(32)})
    return True


def ensure_token(path: Path) -> bool:
    """Create ASSISTANT_TOKEN if missing/short. Returns True if one was created."""
    current = ""
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("ASSISTANT_TOKEN="):
                current = line.split("=", 1)[1].strip()
    if len(current) >= 16:
        return False
    write_env(path, {"ASSISTANT_TOKEN": secrets.token_urlsafe(32)})
    return True


def ensure_secret_key(path: Path) -> bool:
    """Create ASSISTANT_SECRET_KEY (encrypts API keys saved in the app). Never rotates: that would orphan them."""
    from cryptography.fernet import Fernet

    if path.exists() and any(l.startswith("ASSISTANT_SECRET_KEY=") and l.split("=", 1)[1].strip()
                             for l in path.read_text().splitlines()):
        return False
    write_env(path, {"ASSISTANT_SECRET_KEY": Fernet.generate_key().decode()})
    return True


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Set KEY=value lines, keeping every other line (and comments) as is."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in remaining:
            lines[i] = f"{m.group(1)}={remaining.pop(m.group(1))}"
    lines += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)  # holds secrets


def remove_env(path: Path, keys: list[str] | tuple[str, ...]) -> None:
    """Delete KEY=... lines (and nothing else) from an env file."""
    lines = [ln for ln in path.read_text().splitlines() if not any(re.match(rf"\s*{re.escape(k)}\s*=", ln) for k in keys)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    path.chmod(0o600)


def _choose_resources(env_file: Path, profile: str | None, *, asked: bool, ask: Callable[[str], str], out: Callable[[str], None],
                      run: Callable[..., Any] | None = None) -> None:
    """First run: how much of this computer the assistant may use. A plain rerun keeps what is already there."""
    from . import limits
    from .instances import Instances

    keys = [k for k, _, _ in limits.OPTIONS.values()]
    m = Instances(env_file.resolve().parent, out=out, main_env=env_file.resolve(), **({"run": run} if run else {}))
    if not profile and not (asked and not any(read_env(env_file, k) for k in keys)):
        if env_file.is_file() and limits.migrate(m, m.load()):         # sized by an older version: same size, today's numbers
            out(f"Moved the sizes in {env_file} to today's numbers: {limits.size_text(m.load())}.")
        return
    inst = m.load()
    wanted = limits.wanted_for(profile, inst) if profile else limits.choose(m, inst, ask=ask, out=out)
    set_, clear, warn = limits.prepare(m, wanted)
    limits.save(inst, set_, clear)
    for w in warn:
        out(f"warning: {w}")
    out(f"Saved the sizes to {env_file}: {limits.size_text(m.load())}. Change them any time with `assistant limits --choose`.")


def setup(
    env_file: Path,
    browser: str | None = None,
    *,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    interactive: bool = True,
    clis: list[str] | None = None,
    resources: str | None = None,
    run: Callable[..., Any] | None = None,
) -> str:
    asked_browser = browser is None
    if browser is None:
        browser = choose_browser(ask, out) if interactive else "chromium"
    if clis is None and asked_browser and interactive:
        clis = choose_clis(ask, out)  # first-run questions only; a plain rerun keeps what is already in .env
    unknown = [c for c in clis or [] if c not in CLIS]
    if unknown:
        raise SystemExit(f"unknown CLI {unknown[0]!r}; choose from {', '.join(CLIS)}")
    if browser not in BROWSERS:
        raise SystemExit(f"unknown browser {browser!r}; choose from {', '.join(BROWSERS)}")
    write_env(env_file, {"BROWSER": browser})
    out(f"Saved BROWSER={browser} to {env_file}")
    if ensure_token(env_file):
        out(f"Created ASSISTANT_TOKEN in {env_file} (the password for the API; keep it private).")
    if ensure_secret_key(env_file):
        out(f"Created ASSISTANT_SECRET_KEY in {env_file} (encrypts API keys saved in the app; losing it means re-entering them).")
    if ensure_runner_token(env_file):
        out(f"Created CLI_RUNNER_TOKEN in {env_file} (shared secret between the server and the CLI containers).")
    if ensure_keyring_token(env_file):
        out(f"Created KEYRING_TOKEN in {env_file} (shared secret between the server and the key container).")
    if clis is not None:   # the chosen CLI containers; other profiles (updater, kelo, cloudflare...) stay as they are
        keep = [p for p in read_env(env_file, "COMPOSE_PROFILES").split(",") if p and p not in CLIS]
        write_env(env_file, {"COMPOSE_PROFILES": ",".join([*keep, *clis])})
        out(f"Saved COMPOSE_PROFILES={','.join(clis)} to {env_file}" if clis else "No subscription CLI containers selected.")
    _choose_resources(env_file, resources, asked=asked_browser and interactive, ask=ask, out=out, run=run)
    logins = [f"    docker compose exec -it {name}-cli {name}" for name in read_env(env_file, "COMPOSE_PROFILES").split(",") if name in CLIS]
    out("Add model keys in the app (Settings), or put one in .env (e.g. OPENROUTER_API_KEY=...).")
    out("")
    out("Next, start it:")
    out("    docker compose up -d --build")
    if logins:
        out("Then log in once to each subscription CLI:")
        for line in logins:
            out(line)
    return browser


def add_limit_options(p: argparse.ArgumentParser) -> None:
    """RAM, compute and storage are separate knobs. Sizes: 512m, 2g, 1.5g. Use `none` to remove a limit."""
    g = p.add_argument_group("sizes (each is separate; `none` removes a limit, so that one may use the whole computer)")
    g.add_argument("--profile", choices=("recommended", "minimum", "unlimited"),
                   help="recommended = RAM 4 GB, CPU 4, disk 20 GB; minimum = RAM 2 GB, CPU 2, disk 10 GB (all of this copy's containers together, "
                        "program files included); unlimited = no limit. The options below then override single values")
    g.add_argument("--memory", metavar="SIZE", help="RAM for the server container, e.g. 1g")
    g.add_argument("--cpus", metavar="N", help="CPUs for the server container, e.g. 1.5")
    g.add_argument("--disk", metavar="SIZE", help="storage the assistant may fill (its database, uploads, images, notes), e.g. 5g")
    g.add_argument("--browser-memory", metavar="SIZE", help="RAM for the browser container, e.g. 1g")
    g.add_argument("--browser-cpus", metavar="N", help="CPUs for the browser container")
    g.add_argument("--browser-cache", metavar="SIZE", help="how much page cache the browser keeps on disk, e.g. 500m")
    g.add_argument("--cli-memory", metavar="SIZE", help="RAM for each Claude/agy container (main copy only)")
    g.add_argument("--cli-cpus", metavar="N", help="CPUs for each Claude/agy container (main copy only)")
    g.add_argument("--workroom-memory", metavar="SIZE", help="RAM for the work room, where the agent runs code (default 2g; `none` lifts the cap)")
    g.add_argument("--workroom-cpus", metavar="N", help="CPUs for the work room")
    g.add_argument("--workroom-disk", metavar="SIZE", help="scratch space one work room job may fill, and the cap for installed packages (default 2g)")


def _create_ui(args: argparse.Namespace) -> Any:
    """`instance create` in a terminal continues like `assistant setup`: choose the address, then a QR code for the phone.
    Scripts (no terminal) and --yes get the next commands printed instead."""
    if args.cmd != "instance" or args.icmd != "create" or args.yes or args.no_start:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    from .wizard import RichUI, detect_lang
    return RichUI(detect_lang())


def main(argv: list[str] | None = None) -> int:
    if (sys.argv[1:] if argv is None else argv)[:1] == ["nest"]:   # Kelo Nest's publisher and operator commands (nest_cli.py)
        from .nest_cli import main as nest_main
        return nest_main((sys.argv[1:] if argv is None else argv)[1:])
    parser = argparse.ArgumentParser(prog="assistant")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup", help="first-run configuration")
    p.add_argument("--browser", choices=BROWSERS, help="skip the question")
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    p.add_argument("--cli", action="append", choices=CLIS, dest="clis",
                   help="subscription CLI to run in a container (repeatable); skips the question")
    p.add_argument("--profile", choices=("recommended", "minimum", "unlimited"),
                   help="how much Kelo may use: recommended (RAM 4 GB, CPU 4, disk 20 GB), minimum (RAM 2 GB, CPU 2, disk 10 GB) "
                        "or unlimited (no limit); asked on the first run when not given")
    p.add_argument("--yes", action="store_true", help="non-interactive; use defaults for anything not given")
    p.add_argument("--plain", action="store_true", help="ask the questions as plain lines instead of the guided screen")
    p.add_argument("--no-start", action="store_true", help="guided screen only: save the answers but do not build or start Kelo")
    u = sub.add_parser("update", help="update a copy safely: back up its data, rebuild, restart, check it, and roll back by itself if it fails")
    u.add_argument("name", nargs="?", help="which extra copy (default: the main one; `instance update` does every extra copy)")
    u.add_argument("--cli", action="append", choices=CLIS, dest="clis",
                   help="only rebuild this Claude/agy container with the newest CLI (repeatable)")
    u.add_argument("--no-build", action="store_true", help="use the images already on this computer (built or pulled by hand)")
    src = u.add_mutually_exclusive_group()
    src.add_argument("--from-release", dest="source", action="store_const", const="release",
                     help="pull the newest signed release images (the default when the copy already runs release images)")
    src.add_argument("--from-source", dest="source", action="store_const", const="source",
                     help="build the images here from these source files (the default otherwise)")
    u.add_argument("--timeout", type=float, default=180, help="seconds the restarted copy has to pass the health check (default 180)")
    u.add_argument("--env-file", type=Path, default=Path(".env"))
    rb = sub.add_parser("rollback", help="go back to the images (and, if the database changed, the data) from before the last update")
    rb.add_argument("name", nargs="?", help="which extra copy (default: the main one)")
    rb.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    rb.add_argument("--keep-data", action="store_true", help="keep the current data even if the database changed (at your own risk)")
    rb.add_argument("--timeout", type=float, default=180, help="seconds it has to pass the health check (default 180)")
    rb.add_argument("--env-file", type=Path, default=Path(".env"))
    bk = sub.add_parser("backups", help="list a copy's backups and its rollback point")
    bk.add_argument("name", nargs="?", help="which extra copy (default: the main one)")
    bk.add_argument("--env-file", type=Path, default=Path(".env"))
    inst = sub.add_parser("instance", help="more copies of the assistant on this computer, each with its own data, keys and phones")
    isub = inst.add_subparsers(dest="icmd", required=True, metavar="{create,start,stop,delete,token,list,update,routes}")
    c = isub.add_parser("create", help="make a new copy (own port, token, data and API keys) and start it")
    c.add_argument("name")
    c.add_argument("--no-browser", action="store_true", help="skip the browser container (it is the heavy part of a copy)")
    c.add_argument("--domain", help="their address will be https://<name>.<domain> (used for links; set up routing with `routes`)")
    c.add_argument("--no-start", action="store_true", help="only create the settings")
    c.add_argument("--yes", action="store_true", help="ask nothing: skip the phone step and print the next commands")
    add_limit_options(c)
    for verb, text in (("start", "start (or restart after an update) a copy"), ("stop", "stop a copy; its data stays and `start` brings it back"),
                       ("delete", "delete a copy completely: containers, chats, keys, files, settings and its kelo.chat address"),
                       ("token", "print a copy's access token")):
        v = isub.add_parser(verb, help=text)
        v.add_argument("name")
        if verb == "delete":
            v.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    old = isub.add_parser("remove")                     # gone: only says to use stop or delete (never deletes or keeps anything by itself)
    old.add_argument("name", nargs="?")
    old.add_argument("--delete-data", action="store_true", help=argparse.SUPPRESS)
    old.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    isub.add_parser("list", help="every copy, its address and whether it runs")
    con = sub.add_parser("connect", help="open a copy to your phone from anywhere (Kelo address, Tailscale, or your own domain)")
    csub = con.add_subparsers(dest="mode", required=True)
    for mode, text in (("kelo", "easiest: a <name>.kelo.chat address, offered for now (through Cloudflare)"),
                       ("tailscale", "private, no domain: your phone needs the Tailscale app (--public: Funnel, open to everyone)"),
                       ("funnel", "public https on <computer>.<tailnet>.ts.net, no domain, encrypted to this computer"),
                       ("domain", "your own address (DuckDNS or a domain): --direct (this computer, ports 80/443) or --proxy (your proxy)"),
                       ("cloudflare", "your own domain on your Cloudflare account: each copy gets name.<your-domain>"),
                       ("status", "show how each copy is reachable, and check it"), ("off", "stop opening it to the outside")):
        cp = csub.add_parser(mode, help=text)
        cp.add_argument("--instance", help="which extra copy (default: the main one)")
        if mode == "kelo":
            cp.add_argument("--name", help="the name in <name>.kelo.chat: 3-30 letters, digits or '-' (default: the copy's name)")
            cp.add_argument("--broker", help="the server that hands out the addresses (default: ASSISTANT_KELO_BROKER, the sign-in broker, or https://kelo.chat)")
        if mode in ("tailscale", "funnel"):
            cp.add_argument("--https-port", type=int, help="HTTPS port on this computer's Tailscale name (Funnel: 443, 8443 or 10000; picked for you)")
        if mode == "tailscale":
            cp.add_argument("--public", action="store_true", help="open it to everyone with Tailscale Funnel (same as `connect funnel`)")
            cp.add_argument("--force", action="store_true", help="replace whatever already uses that HTTPS port (private only)")
        if mode == "domain":
            cp.add_argument("--host", required=True, help="exactly the address to use: me.duckdns.org, kelo.me.duckdns.org, https://me.example.com:8443")
            how = cp.add_mutually_exclusive_group(required=True)
            how.add_argument("--direct", action="store_true", help="Kelo answers on ports 80/443 of this computer and gets the certificate itself")
            how.add_argument("--proxy", action="store_true", help="you already run a reverse proxy (Nginx Proxy Manager, Traefik, Caddy...)")
            cp.add_argument("--bind", help="--proxy: the LAN address of this computer to listen on (default 0.0.0.0 = all)")
            cp.add_argument("--skip-dns-check", action="store_true", help="--direct: do not compare the DNS record with this computer's public address")
            cp.add_argument("--duckdns", action="store_true", help="keep a .duckdns.org name pointed here (token from $DUCKDNS_TOKEN or typed; kept sealed)")
        if mode == "cloudflare":
            cp.add_argument("--domain", required=True, help="a domain that is on your Cloudflare account, e.g. example.com")
            cp.add_argument("--hostname", help="full host name to use (default: assistant.<domain> or <name>.<domain>)")
        if mode == "status":
            cp.add_argument("--no-check", action="store_true", help="do not open the address to check it")
    lim = sub.add_parser("limits", help="show, or set, how much RAM, CPU and storage each copy may use (nothing is limited unless you say so)")
    lim.add_argument("--instance", help="which extra copy (default: all of them when nothing is being set)")
    lim.add_argument("--no-apply", action="store_true", help="save only; apply the next time the copy is started")
    lim.add_argument("--choose", action="store_true", help="pick Recommended / Minimum / No limit from a menu")
    add_limit_options(lim)
    ph = sub.add_parser("phone", help="sign a phone (or another computer) in")
    phsub = ph.add_subparsers(dest="pcmd", required=True)
    pa = phsub.add_parser("add", help="show a QR code to scan with the phone; any number of phones can join")
    pa.add_argument("--instance", help="which extra copy (default: the main one)")
    pa.add_argument("--url", help="the address the phone should use (default: the one set up with `connect`)")
    pa.add_argument("--owner", action="store_true", help=argparse.SUPPRESS)   # old option: every device has the same rights now; ignored
    pf = phsub.add_parser("fingerprint", help="the server key's fingerprint, to compare with My -> Devices in the app (KE2E)")
    pf.add_argument("--instance", help="which extra copy (default: the main one)")
    kgp = sub.add_parser("keyguard", help="who holds the key of passwords, keys and cards (Keyguard): status, or unlock with the recovery code")
    kgsub = kgp.add_subparsers(dest="kcmd", required=True)
    for verb, text in (("status", "where the key is and which phones hold it"), ("recover", "unlock with the recovery code (no phone needed)"),
                       ("enable", "get ready to turn it on: move ASSISTANT_SECRET_KEY out of .env, then turn it on from the phone")):
        kv = kgsub.add_parser(verb, help=text)
        kv.add_argument("--instance", help="which extra copy (default: the main one)")
        if verb == "enable":
            kv.add_argument("--no-restart", action="store_true", help="do not restart the server now")
        if verb == "recover":
            kv.add_argument("--code", help="the 24-character recovery code (asked for when left out)")
    e2 = sub.add_parser("e2e", help="end-to-end encryption between the apps and this server (KE2E)")
    e2sub = e2.add_subparsers(dest="ecmd", required=True)
    er = e2sub.add_parser("rotate", help="make a new server key; every phone must pair again afterwards")
    er.add_argument("--instance", help="which extra copy (default: the main one)")
    er.add_argument("--yes", action="store_true", help="do it without asking")
    po = phsub.add_parser("owner", help=argparse.SUPPRESS)   # old command: there are no owner devices any more; prints a note
    po.add_argument("--instance", help=argparse.SUPPRESS)
    og = po.add_mutually_exclusive_group()
    og.add_argument("--grant", metavar="ID|NAME", help=argparse.SUPPRESS)
    og.add_argument("--revoke", metavar="ID|NAME", help=argparse.SUPPRESS)
    iu = isub.add_parser("update", help="update every extra copy safely (one build; each is backed up, checked and rolled back on failure)")
    iu.add_argument("--no-build", action="store_true", help="use the images already on this computer")
    iu.add_argument("--timeout", type=float, default=180, help="seconds each copy has to pass the health check (default 180)")
    r = isub.add_parser("routes", help="print the front-door config that sends <name>.<domain> to each copy")
    r.add_argument("--domain", required=True)
    r.add_argument("--format", choices=("cloudflared", "caddy"), default="cloudflared")
    for verb, text in (("enable", "turn an optional feature on"), ("disable", "turn an optional feature off")):
        fp = sub.add_parser(verb, help=text)
        fp.add_argument("feature", choices=("oauth",),
                        help="oauth: sign in to a model with a subscription account (Claude, Antigravity, ChatGPT/Codex, Qwen, MiniMax) "
                             "instead of an API key. Unofficial, so off by default")
        fp.add_argument("--instance", help="which extra copy (default: the main one)")
        fp.add_argument("--no-restart", action="store_true", help="save only; apply the next time the copy is started")
    st = sub.add_parser("status", help="each copy: running or not, its address and its optional features")
    st.add_argument("--instance", help="only this extra copy")
    upd = sub.add_parser("updater", help="updates from the app: the updater container (main copy only)")
    usub = upd.add_subparsers(dest="ucmd", required=True)
    us = usub.add_parser("setup", help="turn it on: its secret, the registry and the settings in .env, then start it")
    auto = us.add_mutually_exclusive_group()
    auto.add_argument("--auto-update", dest="auto", action="store_const", const=True,
                      help="update by itself at night, when Kelo is usually not used (the owner can change it in the app)")
    auto.add_argument("--no-auto-update", dest="auto", action="store_const", const=False, help="only when the owner presses Update (default)")
    us.add_argument("--registry", help="where the release images come from (default ghcr.io/kelo-chat)")
    us.add_argument("--no-start", action="store_true", help="save the settings only")
    us.add_argument("--pull-release", action="store_true",
                    help="first install: pull and check the newest signed release images instead of building them here")
    us.add_argument("--env-file", type=Path, default=Path(".env"))
    for verb, text in (("status", "what it runs, the newest release, and the last update"), ("off", "stop and remove the updater container")):
        uv = usub.add_parser(verb, help=text)
        uv.add_argument("--env-file", type=Path, default=Path(".env"))
    pub = sub.add_parser("publish-app", help="operator's server only: offer an app build for download at <address>/download/<platform>")
    pub.add_argument("platform", choices=("android", "windows", "windows-msi", "mac"),
                     help="android (.apk), windows (.exe, or .msi), mac (.dmg)")
    pub.add_argument("file", help="the build to publish")
    pub.add_argument("--version", help="the version shown on the site, optionally with the build number: 0.9.0+402500 (default: from "
                     "the APK, or from the file name)")
    pubs = sub.add_parser("publish-server", help="operator's server only: offer a server release (its release.json) in /download/latest.json")
    pubs.add_argument("manifest", help="release.json written by the release workflow (tools/release_manifest.py)")
    pubs.add_argument("--registry", help="the registry the release must name (default ghcr.io/kelo-chat)")
    rec = sub.add_parser("release-record", help="record an app release that is live for people (releases/apps.json), so a minimum app "
                                                "version may be as high as it")
    rec.add_argument("platform", help="android, ios or desktop")
    rec.add_argument("version", help="the release version the app reports, for example 0.9.3")
    rec.add_argument("build", help="the platform's own build number (Android versionCode, iOS CFBundleVersion, desktop major*100000000+minor*100000+patch)")
    rec.add_argument("channel", help="download (kelo.chat/download), play (Google Play), appstore (App Store) or msstore (Microsoft Store)")
    rec.add_argument("--date", help="the day it went live, YYYY-MM-DD (default: today)")
    rec.add_argument("--file", help="the record to change (default: releases/apps.json of this checkout)")
    ver = sub.add_parser("version", help="this Kelo's release version and build (version.json)")
    ver.add_argument("--json", action="store_true", help="print it as JSON")
    from .review_cli import add_parser as add_review_parser
    add_review_parser(sub)
    args = parser.parse_args(argv)
    if args.cmd == "version":
        from . import version as release_version
        rel = release_version.current()
        import json as _json
        print(_json.dumps(rel.public()) if args.json else f"Kelo {rel.label}")
        return 0
    if args.cmd == "review":
        from .review_cli import run_command as run_review
        return run_review(args)
    if args.cmd == "release-record":
        from .app_releases import run_record
        return run_record(args)
    if args.cmd == "publish-app":
        from .downloads import run_publish
        return run_publish(args)
    if args.cmd == "publish-server":
        from .downloads import run_publish_server
        return run_publish_server(args)
    if args.cmd == "updater":
        from .updater_setup import run_command as run_updater
        return run_updater(args)
    if args.cmd in ("update", "rollback", "backups") or (args.cmd == "instance" and args.icmd == "update"):
        from .updates import run_command as run_update
        return run_update(args)
    if args.cmd in ("instance", "connect", "phone", "e2e", "keyguard", "limits", "enable", "disable", "status"):
        from .manage import run_command
        return run_command(args, ui=_create_ui(args))

    interactive = not args.yes and sys.stdin.isatty()
    if args.cmd == "setup":
        if args.browser == "chrome" and not args.yes:
            print(CHROME_NOTICE)
            if input("Use Google Chrome anyway? [y/N]: ").strip().lower() not in ("y", "yes"):
                print("Staying with Chromium.")
                args.browser = "chromium"
        if interactive and not args.plain:
            from . import wizard
            return wizard.run(args.env_file, start=not args.no_start, browser=args.browser, clis=args.clis, profile=args.profile)
        setup(args.env_file, args.browser, interactive=interactive, clis=args.clis, resources=args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
