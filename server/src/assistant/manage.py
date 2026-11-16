"""`assistant instance|connect|phone ...`: run copies, open them to the outside, sign phones in. Run from the folder with compose.yml."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import connect, limits
from .cli import CLIS, ensure_runner_token, read_env, remove_env, write_env
from .instances import MAIN, Instance, InstanceError, Instances

OAUTH_KEY = "ASSISTANT_UNOFFICIAL_PROVIDERS"   # Settings.unofficial_providers: list the subscription sign-in providers in the app
OAUTH_WHAT = "signing in with a subscription account (Claude, Antigravity, ChatGPT/Codex, Qwen, MiniMax) instead of an API key"


def kelo_cmd() -> str:
    """The installer's `kelo` launcher, else `assistant`."""
    from .wizard import kelo_cmd as cmd
    return cmd()


def _who(inst: Any) -> str:
    return "the main copy" if inst.is_main else inst.name


def _closing(m: Instances, inst: Any, *, running: bool, paired: bool = False) -> None:
    """The last lines of `instance create`, in the system language, one command per indented line."""
    from .wizard import closing_lines, detect_lang
    m.out("")
    for line in closing_lines(detect_lang(), inst, running=running, paired=paired):
        m.out(line)


def _wanted(args: Any, base: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """Limit options from --profile (if given) with any single options on the command line laid over it."""
    wanted = dict(base or {})
    for opt, v in limits.parse_options(args).items():
        if v is not None:
            wanted[opt] = v
    return wanted


def run_command(args: Any, manager: Instances | None = None, ask: Callable[[str], str] | None = None, ui: Any = None) -> int:
    """`ui` (the setup wizard's terminal look) is given only in a terminal: `instance create` then goes on to the phone step."""
    m = manager or Instances(Path.cwd())
    if manager is None:   # a copy that starts makes its Kelo address tunnel again when it was parked or lost (connect.kelo_refresh)
        m.on_start = lambda inst: connect.kelo_refresh(m, inst)
    interactive = ask is not None or sys.stdin.isatty()
    ask = ask or input
    try:
        if args.cmd in ("limits", "status") or (args.cmd == "instance" and args.icmd in ("start", "update", "list")):
            for n, size in limits.migrate_all(m):
                m.out(f"{n}: sizes moved to today's numbers for {size} (applies the next time it starts).")
        if args.cmd == "instance":
            _instance(m, args, ask if interactive else None, ui)
        elif args.cmd == "connect":
            _connect(m, args)
        elif args.cmd == "limits":
            _limits(m, args, ask if interactive else None)
        elif args.cmd in ("enable", "disable"):
            _oauth(m, args.instance, on=args.cmd == "enable", restart=not args.no_restart)
        elif args.cmd == "status":
            _status(m, args.instance)
        elif args.cmd == "phone" and getattr(args, "pcmd", "add") == "fingerprint":
            info = connect.e2e_info(m, m.load(args.instance))
            m.out(f"Server key fingerprint: {info['fingerprint']}")
            m.out("Compare it with My -> Devices in the app; scanning a new QR (assistant phone add) marks the key verified.")
        elif args.cmd == "keyguard":
            inst = m.load(args.instance)
            if args.kcmd == "enable":
                _keyguard_enable(m, inst, restart=not getattr(args, "no_restart", False))
            elif args.kcmd == "status":
                st = connect.keyguard_status(m, inst)
                where = "the phones" if st["mode"] == "phone" else "this server (master.key or ASSISTANT_SECRET_KEY)"
                m.out(f"Passwords, keys and cards: the key is held by {where}." + (" Locked: open Kelo on a phone that holds the key." if st["locked"] else ""))
                for p in st.get("phones") or []:
                    m.out(f"  phone #{p['device_id']} {p.get('name') or ''}".rstrip())
            else:
                code = args.code or (ask("Recovery code (24 characters): ") if interactive else "")
                connect.keyguard_recover(m, inst, code.strip())
                m.out("Unlocked. Add a phone again from the app (My -> Security) while it is unlocked.")
        elif args.cmd == "e2e":
            inst = m.load(args.instance)
            if not args.yes and (ask is None or not interactive or ask(
                    "Make a new end-to-end key? Every phone must pair again afterwards (assistant phone add). [y/N]: ").strip().lower()
                    not in ("y", "yes")):
                m.out("Nothing was changed. (Add --yes to do it without the question.)")
            else:
                info = connect.e2e_rotate(m, inst)
                m.out(f"New server key. Fingerprint: {info['fingerprint']}")
                m.out("Every phone must pair again: show a new QR with `assistant phone add`.")
        elif args.cmd == "phone" and getattr(args, "pcmd", "add") == "owner":
            m.out(OWNER_GONE_NOTE)
        elif args.cmd == "phone":
            inst = m.load(args.instance)
            link, qr = connect.phone_add(m, inst, address=args.url)   # `--owner` is an old option and is ignored
            if sys.stdout.isatty():                    # painted black on white: scans the same on dark and light terminals
                qr = connect.qr_text(link, color=True)
            if connect.local_only(args.url or inst.address):
                m.out("Note: this address only works on this computer or this network, not from a phone elsewhere. "
                      "Set up an outside address first (assistant connect ...).")
            m.out(qr.rstrip())
            m.out(f"Scan this with the phone's camera (or open the link on it). It works once and expires in 10 minutes:\n{link}")
            try:   # the key the QR carries, to compare later in the app (My -> Devices)
                m.out(f"Server key fingerprint: {connect.e2e_info(m, inst)['fingerprint']}")
            except connect.ConnectError:
                pass   # an older server without KE2E
    except (EOFError, KeyboardInterrupt):
        m.out("\nCancelled; nothing was changed.")
        return 1
    except InstanceError as e:
        m.out(f"error: {e}")
        return 1
    except subprocess.CalledProcessError as e:
        m.out(f"error: a docker command failed (exit {e.returncode}); see the output above")
        return 1
    return 0


OWNER_GONE_NOTE = ("There are no owner devices any more: every device signed in to this server has the same full rights "
                   "(Keyguard, operator settings, auto-pay and the rest). Nothing to grant or take back. "
                   "Sign a new phone in with: assistant phone add")


def _instance(m: Instances, args: Any, ask: Callable[[str], str] | None, ui: Any = None) -> None:
    c = args.icmd
    if c == "create":
        draft = Instance(args.name, m.env_path(args.name), 0, 0, not args.no_browser, "")
        profile = getattr(args, "profile", None)
        wanted = _wanted(args, limits.wanted_for(profile, draft) if profile else None)
        if not wanted and ask is not None:                     # nothing said on the command line: ask, with the recommended size pre-selected
            wanted = limits.choose(m, draft, ask=ask, out=m.out)
        set_, clear, warn = limits.prepare(m, wanted)          # checked first, so a bad value leaves nothing half-made
        inst = m.create(args.name, browser=not args.no_browser, domain=args.domain)
        limits.save(inst, set_, clear)
        for w in warn:
            m.out(f"warning: {w}")
        m.out(f"Created {inst.name} (port {inst.server_port}).")
        paired = False
        if not args.no_start:
            m.start(inst.name)
            if ui is not None:                                 # a terminal: the same phone step as `assistant setup`
                from .wizard import phone_step
                m.out("")
                try:
                    paired = phone_step(ui, m, inst.name, suggestion=inst.name)
                except (KeyboardInterrupt, EOFError):          # the copy is made and running; only the phone part is skipped
                    m.out("")
                inst = m.load(inst.name)
        _closing(m, inst, running=not args.no_start, paired=paired)
    elif c == "start":
        inst = m.start(args.name)
        m.out(f"{inst.name} is up at {inst.local_url}" + (f" and at {inst.connect_url}" if inst.connect_url else ""))
    elif c == "stop":
        m.stop(args.name)
        m.out(f"{args.name} stopped (data kept). Start again with: {kelo_cmd()} instance start {args.name}")
    elif c == "token":
        m.out(m.token(args.name))
    elif c == "delete":
        if args.name == MAIN:
            m.delete(args.name)                    # explains how the main copy goes instead
        inst = m.load(args.name)
        what = [f"its containers, every chat, key, file and phone sign-in, and its settings folder {m.dir / inst.name}"]
        if inst.connect_mode == "kelo" and inst.connect_url:
            what.append(f"its address {inst.connect_url} is given back")
        m.out(f"This deletes {inst.name} for good: " + "; ".join(what) + ".")
        if not args.yes:
            if ask is None:
                raise InstanceError("deleting asks for confirmation, so it needs a terminal. Add --yes to delete without asking.")
            if ask(f"Type {inst.name} to delete it (anything else keeps it): ").strip() != inst.name:
                raise InstanceError(f"not deleted; {inst.name} is unchanged")
        for note in connect.disconnect(m, inst):   # stop publishing it (and give a Kelo name back) before it goes away
            m.out(note)
        m.delete(inst.name)
        m.out(f"{inst.name} is deleted.")
    elif c == "remove":                            # the old command: says what to use instead and changes nothing
        name = args.name or "<name>"
        raise InstanceError(f"`instance remove` is gone, so nothing was changed. Use `{kelo_cmd()} instance stop {name}` "
                            f"(stops it; its data stays) or `{kelo_cmd()} instance delete {name}` (deletes it completely).")
    elif c == "list":
        rows = [m.load(MAIN)] if m.env_path(MAIN).is_file() else []
        rows += [m.load(n) for n in m.names()]
        for inst in rows:
            state = "running" if m.running(inst.name) else "stopped"
            via = f"  via {inst.connect_mode}: {inst.connect_url}" if inst.connect_mode else ""
            m.out(f"{inst.name:20} {state:8} {inst.local_url}  {'browser' if inst.browser else 'no browser'}{via}")
        if len(rows) <= 1:
            m.out("Make another copy: assistant instance create <name>")
    elif c == "update":                            # normally routed to updates.run_command by cli.main (exit code, lock)
        from .updates import Updater
        if Updater(m).update(m.names(), build=not getattr(args, "no_build", False)) != 0:
            raise InstanceError("the update did not finish; see the messages above")
    elif c == "routes":
        m.out(m.routes(args.domain, args.format).rstrip())


def _status_lines(m: Instances, inst: Any, check: bool) -> list[str]:
    st = connect.status(m, inst, check=check)
    if not st["mode"]:
        return [f"{_who(inst)}: {st['method']}"]
    line = f"{_who(inst)}: {st['mode']} -> {st['host']}"
    if st["host"] != st["address"]:
        line += f"  ({st['address']})"
    return [line + f"  [{st['method']}]", f"  health: {st['health']}"] if check else [line + f"  [{st['method']}]"]


def _duckdns_token(args: Any) -> str | None:
    """Only when asked for with --duckdns: from $DUCKDNS_TOKEN, else typed without being shown. Never printed."""
    if not getattr(args, "duckdns", False):
        return None
    import getpass
    import os

    token = os.environ.get("DUCKDNS_TOKEN", "").strip()
    if not token:
        if not sys.stdin.isatty():
            raise InstanceError("--duckdns needs the token: set DUCKDNS_TOKEN in the environment, or run this in a terminal to type it")
        token = getpass.getpass("DuckDNS token (not shown): ").strip()
    return token or None


def _connect(m: Instances, args: Any) -> None:
    mode = args.mode
    if mode == "status" and not args.instance:
        rows = ([m.load(MAIN)] if m.env_path(MAIN).is_file() else []) + [m.load(n) for n in m.names()]
        if not rows:
            m.load(MAIN)                                   # explains that there is no .env here
        for inst in rows:
            for line in _status_lines(m, inst, not getattr(args, "no_check", False)):
                m.out(line)
        return
    inst = m.load(args.instance)
    flag = "" if inst.is_main else f" --instance {inst.name}"
    if mode == "status":
        for line in _status_lines(m, inst, not getattr(args, "no_check", False)):
            m.out(line)
        return
    if mode == "off":
        if not inst.connect_mode:
            m.out("Nothing to turn off.")
            return
        for note in connect.disconnect(m, inst):
            m.out(note)
        if m.running(inst.name):
            m.start(inst.name, build_if_missing=False)     # drops the tunnel / Caddy / LAN binding and the public address
        m.out(f"{_who(inst)} is no longer open to the outside.")
        return
    if mode == "tailscale" and getattr(args, "public", False):
        mode = "funnel"
    if mode == "domain":
        mode = "direct" if args.direct else "proxy"
    if inst.connect_mode and inst.connect_mode != mode:   # one way at a time: undo the old one first
        m.out(f"Turning off {connect.MODES.get(inst.connect_mode, inst.connect_mode)} ({inst.connect_url}) first.")
        for note in connect.disconnect(m, inst):
            m.out(note)
        inst = m.load(inst.name)
    restart = True
    extra: list[str] = []
    if mode == "tailscale":
        url = connect.tailscale_connect(m, inst, https_port=args.https_port, force=args.force)
        restart = False
    elif mode == "funnel":
        url = connect.funnel_connect(m, inst, https_port=getattr(args, "https_port", None))
    elif mode == "kelo":
        url = connect.kelo_connect(m, inst, getattr(args, "name", None), broker=getattr(args, "broker", None))
        extra.append("Traffic to this address passes through Cloudflare (https ends there). For end-to-end encryption use "
                     "Tailscale Funnel or your own domain instead.")
    elif mode == "cloudflare":
        url = connect.cloudflare_connect(m, inst, args.domain, hostname=args.hostname)
    elif mode == "direct":
        url, extra = connect.domain_direct(m, inst, args.host, check_dns=not args.skip_dns_check, duckdns_token=_duckdns_token(args))
    else:
        url, settings, extra = connect.domain_proxy(m, inst, args.host, bind=args.bind or "0.0.0.0", duckdns_token=_duckdns_token(args))
        m.out(settings)
    if restart:
        m.start(inst.name, build_if_missing=False)     # starts the tunnel / Caddy and gives the server its public address
    m.out(f"{_who(inst)} is open at {url}")
    for line in extra:
        m.out(line)
    if mode == "tailscale":
        m.out("Your phone needs the Tailscale app signed in to the same account to open it.")
    m.out("")
    m.out("Check it:")
    m.out(f"    assistant connect status{flag}")
    m.out("Connect a phone (shows a QR code):")
    m.out(f"    assistant phone add{flag}")


def _limits(m: Instances, args: Any, ask: Callable[[str], str] | None) -> None:
    if getattr(args, "choose", False):
        if ask is None:
            raise InstanceError("--choose asks questions, so it needs a terminal. Use --profile or the single options instead.")
        wanted = limits.choose(m, m.load(args.instance), ask=ask, out=m.out)
    else:
        profile = getattr(args, "profile", None)
        wanted = _wanted(args, limits.wanted_for(profile, m.load(args.instance)) if profile else None)
    if not any(v is not None for v in wanted.values()):
        for line in limits.report(m, only=args.instance):
            m.out(line)
        return
    inst = m.load(args.instance)
    set_, clear, warn = limits.prepare(m, wanted)
    limits.save(inst, set_, clear)
    for w in warn:
        m.out(f"warning: {w}")
    cap = limits.capacity(m)
    m.out(f"{_who(inst)}: " + ", ".join(f"{limits.OPTIONS[o][2]} {limits.shown(inst, o, cap)}" for o, v in wanted.items() if v is not None)
          + f"  [{limits.PROFILE_LABEL[limits.current_profile(inst)]}]")
    if args.no_apply or not m.running(inst.name):
        m.out("Saved. It applies the next time this copy is started.")
    else:
        m.start(inst.name, build_if_missing=False)
        m.out("Applied (containers that changed were restarted; the data is kept).")


def oauth_on(inst: Any) -> bool:
    return read_env(inst.env, OAUTH_KEY).lower() not in ("", "0", "false", "no")


def _cli_profiles_on(m: Instances, inst: Any, restart: bool) -> None:
    """`enable oauth` also turns on the Claude and Antigravity containers (COMPOSE_PROFILES claude,agy) and starts them, because the
    phone signs those two in through their containers and the server container cannot start containers itself. Their logins live in
    their own volumes, so starting them changes no sign-in."""
    profiles = [p for p in read_env(inst.env, "COMPOSE_PROFILES").split(",") if p]
    added = [c for c in CLIS if c not in profiles]
    created_token = ensure_runner_token(inst.env)
    if added:
        write_env(inst.env, {"COMPOSE_PROFILES": ",".join([*profiles, *added])})
        m.out(f"Turned on the {' and '.join('Claude' if c == 'claude' else 'Antigravity' for c in added)} container"
              f"{'s' if len(added) > 1 else ''} (COMPOSE_PROFILES in {inst.env}).")
    if (added or created_token) and restart and m.running(inst.name):
        flags = [a for c in CLIS for a in ("--profile", c)]
        # a token made just now must reach the server too, or it cannot talk to the containers (HTTP 502 on sign-in)
        services = [*(f"{c}-cli" for c in CLIS), *(["server"] if created_token else [])]
        m.run(m.compose(inst, *flags, "up", "-d", "--no-deps", *services), cwd=m.root, check=True)
        m.out("Started them (the first start builds their images; this can take a few minutes).")


KEYGUARD_PHONE_STEP = ("Now turn it on from your phone: open Kelo on your phone, My -> Security -> Keyguard. The phone keeps the key "
                       "of your passwords, keys and cards; write down the recovery code it shows.")


def _keyguard_enable(m: Instances, inst: Any, *, restart: bool = True) -> None:
    """`assistant keyguard enable`: Keyguard cannot start while the master key sits in .env (ASSISTANT_SECRET_KEY). This moves it
    out safely: the running server first writes the same key into its master.key, .env is backed up (0600), the line is removed and
    the server restarted; then the phone takes over (enrollment from the app). The key is never printed."""
    if not read_env(inst.env, "ASSISTANT_SECRET_KEY"):
        m.out("The master key is not in .env: nothing to move.")
        m.out(KEYGUARD_PHONE_STEP)
        return
    if not m.running(inst.name):
        raise InstanceError(f"{_who(inst)} is not running: start it first, so it can keep its key in master.key before the key "
                            "leaves .env. Nothing was changed.")
    connect.keyguard_prepare(m, inst)
    import shutil
    import time as _time
    backup = inst.env.with_name(f"{inst.env.name}.bak-keyguard-{int(_time.time())}")
    shutil.copyfile(inst.env, backup)
    backup.chmod(0o600)
    remove_env(inst.env, ["ASSISTANT_SECRET_KEY"])
    m.out(f"Moved the master key out of {inst.env} into the server's master.key (a copy of the old file: {backup}).")
    m.out(f"Delete {backup} once Keyguard is on: it still holds the old key.")
    if restart:
        m.run(m.compose(inst, "up", "-d", "--no-deps", "--no-build", "server"), cwd=m.root, check=True)
        m.out("Restarted the server container to apply it (chats and data are kept).")
    else:
        m.out("It applies the next time this copy is started.")
    m.out(KEYGUARD_PHONE_STEP)


def _oauth(m: Instances, name: str | None, *, on: bool, restart: bool = True) -> None:
    """`assistant enable|disable oauth`: one line in the copy's env file, then the server container is recreated to read it.
    Enabling also turns on and starts the Claude / agy containers (the app signs them in through them); disabling leaves them as
    they are (a sign-in in use keeps working)."""
    inst = m.load(name)
    who = _who(inst)
    flag = "" if inst.is_main else f" --instance {inst.name}"
    if on and read_env(inst.env, "ASSISTANT_REVIEW_MODE").lower() not in ("", "0", "false", "no"):
        raise InstanceError(f"{who} is an app store review copy: subscription sign-ins stay off there (review mode). Nothing was changed.")
    if oauth_on(inst) == on:
        m.out(f"{OAUTH_WHAT[0].upper() + OAUTH_WHAT[1:]} is already {'on' if on else 'off'} for {who}.")
    else:
        if on:
            ensure_runner_token(inst.env)   # before the server restarts, so the server and the Claude/agy containers share it
        write_env(inst.env, {OAUTH_KEY: "1" if on else "0"})
        m.out(f"Saved {OAUTH_KEY}={'1' if on else '0'} in {inst.env}.")
        if not restart or not m.running(inst.name):
            m.out("It applies the next time this copy is started.")
        else:
            m.run(m.compose(inst, "up", "-d", "--no-deps", "--no-build", "server"), cwd=m.root, check=True)
            m.out("Restarted the server container to apply it (chats and data are kept).")
    if on:
        _cli_profiles_on(m, inst, restart)
        m.out(f"On for {who}: the app now lists {OAUTH_WHAT}, under an \"unofficial\" divider. These vendors have not confirmed that "
              "their subscriptions may be used from other apps, so the app asks once before each one is used.")
        m.out("Sign in from the app: My → Model → choose one → Sign in. No terminal is needed.")
        m.out(f"Turn it off again: assistant disable oauth{flag}")
    else:
        m.out(f"Off for {who}: the app no longer lists {OAUTH_WHAT}, and they cannot be newly chosen.")
        m.out("A sign-in this copy already uses as its main or fallback model keeps working and stays listed until another model is "
              "chosen. Existing sign-ins are not deleted.")
        m.out(f"Turn it on again: assistant enable oauth{flag}")


def _status(m: Instances, name: str | None) -> None:
    """`assistant status`: each copy, whether it runs, its address, and its optional settings."""
    if name:
        rows = [m.load(name)]
    else:
        rows = ([m.load(MAIN)] if m.env_path(MAIN).is_file() else []) + [m.load(n) for n in m.names()]
        if not rows:
            m.load(MAIN)                                   # explains that there is no .env here
    for inst in rows:
        state = "running" if m.running(inst.name) else "stopped"
        m.out(f"{inst.name}: {state}  {inst.address}" + (f"  (via {inst.connect_mode})" if inst.connect_mode else ""))
        flag = "" if inst.is_main else f" --instance {inst.name}"
        m.out(f"  subscription sign-ins (unofficial providers): {'on' if oauth_on(inst) else 'off'}"
              f"  [assistant {'disable' if oauth_on(inst) else 'enable'} oauth{flag}]")
