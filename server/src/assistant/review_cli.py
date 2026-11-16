"""`assistant review ...`: the copies app store reviewers use (one per store, so no reviewer ever sees another's data).

    assistant review create <apple|google|ms> [--no-start] [--address]   the copy review-<store>: recommended size, review mode on
    assistant review model <store> --provider P --model M               its model (an API key; read hidden, never printed)
    assistant review code <store> [--days 30] [--uses 20]                a multi-use sign-in code + the review notes, in a 0600 file
    assistant review status [<store>]                                    mode, address, model, codes (never a code or a key)
    assistant review revoke <store>                                      every code ends; the phones that used one are signed out
    assistant review reset <store> [--yes]                               chats, memory, files and logins back to fresh; settings kept
    assistant review delete <store> [--yes]                              the copy goes completely (and its kelo.chat address)

Review mode itself (what a reviewer can and cannot do) is in review.py. The notes templates are docs/review/notes-<store>.txt; the
whole procedure is docs/review/README.md.
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import connect, limits
from .cli import read_env, write_env
from .instances import Instance, InstanceError, Instances
from .review import ENV_KEY, STORES

OAUTH_KEY = "ASSISTANT_UNOFFICIAL_PROVIDERS"
NOTES_FILE = "review-notes.txt"
KEY_ENV = "KELO_REVIEW_API_KEY"          # `review model` reads the API key from here when set (else asks, hidden)
VOLUMES_RESET = ("browser-data", "browser-downloads", "workroom-scratch")   # the browser's logins, cookies and downloads


def add_parser(sub: Any) -> None:
    rv = sub.add_parser("review", help="copies for app store reviewers (review-apple, review-google, review-ms): demo mode, sign-in codes")
    rsub = rv.add_subparsers(dest="rcmd", required=True, metavar="{create,model,code,status,revoke,reset,delete}")
    stores = tuple(STORES)
    c = rsub.add_parser("create", help="make the copy review-<store> (recommended size, review mode) and start it")
    c.add_argument("store", choices=stores)
    c.add_argument("--no-start", action="store_true", help="only create the settings")
    c.add_argument("--address", action="store_true",
                   help="also claim https://review-<store>.kelo.chat now (asks the kelo.chat address service; off unless given)")
    mo = rsub.add_parser("model", help="the model it answers with: a provider that takes an API key (the key is read hidden)")
    mo.add_argument("store", choices=stores)
    mo.add_argument("--provider", required=True, help="e.g. anthropic (a provider that takes an API key; sign-ins are refused)")
    mo.add_argument("--model", required=True, help="the model id, e.g. the one your API key's spending cap is set for")
    co = rsub.add_parser("code", help="a sign-in code for reviewers, written with the review notes to a 0600 file (never printed)")
    co.add_argument("store", choices=stores)
    co.add_argument("--days", type=float, default=30, help="days the code can be used to sign in (default 30)")
    co.add_argument("--uses", type=int, default=20, help="how many phones may sign in with it (default 20)")
    st = rsub.add_parser("status", help="review mode, address, model and codes of each review copy (no codes or keys shown)")
    st.add_argument("store", nargs="?", choices=stores)
    rk = rsub.add_parser("revoke", help="end every code of this copy and sign out the phones that used them")
    rk.add_argument("store", choices=stores)
    for verb, text in (("reset", "wipe chats, memory, files, connectors and browser logins back to fresh; settings, model and codes stay"),
                       ("delete", "delete the copy completely (containers, data, settings and its kelo.chat address)")):
        v = rsub.add_parser(verb, help=text)
        v.add_argument("store", choices=stores)
        v.add_argument("--yes", action="store_true", help="do not ask for confirmation")


def name_of(store: str) -> str:
    if store not in STORES:
        raise InstanceError(f"store must be one of: {', '.join(STORES)}")
    return f"review-{store}"


def is_review(inst: Instance) -> bool:
    return read_env(inst.env, ENV_KEY).lower() not in ("", "0", "false", "no")


def _load(m: Instances, store: str) -> Instance:
    name = name_of(store)
    if name not in m.names():
        raise InstanceError(f"there is no {name} yet. Make it with: assistant review create {store}")
    inst = m.load(name)
    if not is_review(inst):
        raise InstanceError(f"{name} is not in review mode ({ENV_KEY} is not set in {inst.env}); nothing was changed")
    return inst


def notes_path(m: Instances, inst: Instance) -> Path:
    return m.dir / inst.name / NOTES_FILE


def kelo_address(inst: Instance) -> str:
    return f"https://{inst.name}.kelo.chat"


def run_command(args: Any, manager: Instances | None = None, ask: Callable[[str], str] | None = None,
                secret: Callable[[str], str] | None = None) -> int:
    m = manager or Instances(Path.cwd())
    interactive = ask is not None or sys.stdin.isatty()
    ask = ask or input
    try:
        c = args.rcmd
        if c == "create":
            _create(m, args)
        elif c == "model":
            _model(m, args, secret)
        elif c == "code":
            _code(m, args)
        elif c == "status":
            _status(m, args.store)
        elif c == "revoke":
            _revoke(m, args.store)
        elif c == "reset":
            _reset(m, args, ask if interactive else None)
        elif c == "delete":
            _delete_copy(m, args, ask if interactive else None)
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


# ---------------------------------------------------------------------------------------------------------------------- create
def _create(m: Instances, args: Any) -> None:
    name = name_of(args.store)
    if m.env_path(name).exists():
        raise InstanceError(f"{name} already exists. See it with: assistant review status {args.store}")
    draft = Instance(name, m.env_path(name), 0, 0, True, "")
    set_, clear, warn = limits.prepare(m, limits.wanted_for("recommended", draft))   # checked first: a bad value leaves nothing made
    inst = m.create(name, browser=True)
    limits.save(inst, set_, clear)
    write_env(inst.env, {ENV_KEY: "1", OAUTH_KEY: "0"})
    for w in warn:
        m.out(f"warning: {w}")
    m.out(f"Created {name} for {STORES[args.store]} reviewers (port {inst.server_port}, recommended size, review mode on).")
    if not args.no_start:
        m.start(name)
        m.out(f"{name} is running at {inst.local_url}")
    connect_cmd = f"assistant connect kelo --instance {name}"
    if args.address:
        from .manage import _connect
        _connect(m, argparse.Namespace(mode="kelo", instance=name, name=name, broker=None))
    m.out("")
    m.out("Next:")
    m.out("  1. Its model (an API key with a spending cap; the key is asked for hidden):")
    m.out(f"       assistant review model {args.store} --provider anthropic --model <model id>")
    if not args.address:
        m.out(f"  2. Its address {kelo_address(inst)} (asks the kelo.chat address service; run it when you are ready):")
        m.out(f"       {connect_cmd}")
    m.out("  3. A sign-in code and the review notes for the store (written to a 0600 file, never printed):")
    m.out(f"       assistant review code {args.store}")


# ---------------------------------------------------------------------------------------------------------------------- model
def _model(m: Instances, args: Any, secret: Callable[[str], str] | None) -> None:
    from .providers import get_provider

    inst = _load(m, args.store)
    try:
        prov = get_provider(args.provider)
    except KeyError:
        raise InstanceError(f"unknown provider {args.provider!r}") from None
    if prov.auth_type != "api_key" or not prov.api_key_envs:
        raise InstanceError(f"{prov.name} does not take an API key. A review copy only uses a provider with an API key "
                            "(subscription sign-ins are off in review mode).")
    model = args.model.strip()
    if not model or any(ch.isspace() for ch in model):
        raise InstanceError("give the model id, e.g. --model <id from the provider's model list>")
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        if secret is None and not sys.stdin.isatty():
            raise InstanceError(f"the API key is read hidden, so this needs a terminal; or set {KEY_ENV} in the environment")
        key = (secret or getpass.getpass)(f"{prov.name} API key for {inst.name} (not shown): ").strip()
    if len(key) < 8 or any(ch.isspace() for ch in key):
        raise InstanceError("that does not look like an API key; nothing was changed")
    env_name = prov.api_key_envs[0]
    write_env(inst.env, {"ASSISTANT_PROVIDER": prov.name, "ASSISTANT_MODEL": model, env_name: key})
    m.out(f"Saved for {inst.name}: {prov.name} / {model}, the key in {env_name} in {inst.env} (not shown).")
    if m.running(inst.name):
        m.run(m.compose(inst, "up", "-d", "--no-deps", "--no-build", "server"), cwd=m.root, check=True, env=m.compose_env(inst))
        m.out("Restarted the server container to use it (chats and data are kept).")
        wait_healthy(inst)
        try:   # the profile is what the phones show and use (a model chosen there earlier would win): it gets this one
            connect._server_call(inst, connect._put, "/api/profile", json={"provider": prov.name, "model": model})
        except InstanceError as e:
            m.out(f"note: could not set it as the chosen model ({e}); it is used after `assistant review reset {args.store}`")
    else:
        m.out(f"It applies when the copy starts: assistant instance start {inst.name}")


def wait_healthy(inst: Instance, *, tries: int = 45, pause: float = 2.0) -> bool:
    """Until the restarted server answers /healthz (at most tries x pause seconds)."""
    import time

    import httpx
    for _ in range(tries):
        try:
            if connect._get(f"{inst.local_url}/healthz", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(pause)
    return False


# ---------------------------------------------------------------------------------------------------------------------- code
def template_path(m: Instances, store: str) -> Path:
    return m.root / "docs" / "review" / f"notes-{store}.txt"


def render_notes(template: str, **values: str) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


def write_private(path: Path, text: str) -> None:
    """Written 0600 from the first byte (never readable by others, not even for a moment)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, text.encode())
    finally:
        os.close(fd)


def _code(m: Instances, args: Any) -> None:
    inst = _load(m, args.store)
    if not 0 < args.days <= 365:
        raise InstanceError("--days must be more than 0 and at most 365")
    if not 1 <= args.uses <= 1000:
        raise InstanceError("--uses must be 1 to 1000")
    tpl = template_path(m, args.store)
    if not tpl.is_file():
        raise InstanceError(f"the review notes template {tpl} is missing (it comes with the source; update this folder)")
    address = inst.connect_url if inst.connect_mode == "kelo" and inst.connect_url else ""
    got = connect._server_call(inst, connect._post, "/api/devices/codes", json={"label": inst.name, "days": args.days, "uses": args.uses})
    expires = datetime.fromtimestamp(float(got["expires_at"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    shown = address or kelo_address(inst)
    text = render_notes(tpl.read_text(), address=shown, code=str(got["code"]), link=f"{shown}/#pair={got['code']}",
                        expires=expires, uses=str(got["max_uses"]), store=STORES[args.store])
    path = notes_path(m, inst)
    write_private(path, text)
    m.out(str(path))
    m.out(f"(code #{got['id']}: valid until {expires}, up to {got['max_uses']} phones; older codes keep working until "
          f"`assistant review revoke {args.store}`)")
    if not address:
        m.out(f"warning: {inst.name} has no kelo.chat address yet, so the notes name {shown}, which does not answer until you run: "
              f"assistant connect kelo --instance {inst.name}")


# ---------------------------------------------------------------------------------------------------------------------- status
def _delete(*a: Any, **k: Any) -> Any:      # looked up when called, so tests can stand in a fake
    import httpx
    return httpx.delete(*a, **k)


def _codes(inst: Instance) -> list[dict[str, Any]]:
    return list(connect._server_call(inst, connect._get, "/api/devices/codes").get("codes") or [])


def _status(m: Instances, store: str | None) -> None:
    stores = [store] if store else [s for s in STORES if name_of(s) in m.names()]
    if not stores:
        m.out("No review copies yet. Make one with: assistant review create <apple|google|ms>")
        return
    for s in stores:
        inst = _load(m, s)
        running = bool(m.running(inst.name))
        provider, model = read_env(inst.env, "ASSISTANT_PROVIDER"), read_env(inst.env, "ASSISTANT_MODEL")
        has_key = False
        if provider:
            try:
                from .providers import get_provider
                has_key = any(read_env(inst.env, k) for k in get_provider(provider).api_key_envs)
            except KeyError:
                pass
        m.out(f"{inst.name} ({STORES[s]}): {'running' if running else 'stopped'}, review mode on")
        m.out(f"  address: {inst.connect_url or 'not set up (assistant connect kelo --instance ' + inst.name + ')'}")
        m.out(f"  model:   {provider + ' / ' + model if provider and model else 'not set (assistant review model ' + s + ' ...)'}"
              + (f", API key {'set' if has_key else 'missing'}" if provider else ""))
        if not running:
            continue
        try:
            codes = _codes(inst)
        except InstanceError as e:
            m.out(f"  codes:   could not ask the server ({e})")
            continue
        if not codes:
            m.out(f"  codes:   none (assistant review code {s})")
        for c in codes:
            until = datetime.fromtimestamp(float(c["expires_at"]), tz=timezone.utc).strftime("%Y-%m-%d")
            m.out(f"  code #{c['id']}: {c['state']}, {c['uses']}/{c['max_uses']} used, {c['devices']} signed in, until {until}")


# ---------------------------------------------------------------------------------------------------------------------- revoke
def _revoke(m: Instances, store: str) -> None:
    inst = _load(m, store)
    live = [c for c in _codes(inst) if c.get("state") != "revoked"]
    signed_out = 0
    for c in live:
        signed_out += int(connect._server_call(inst, _delete, f"/api/devices/codes/{int(c['id'])}").get("signed_out") or 0)
    path = notes_path(m, inst)
    if path.exists():
        path.unlink()
    m.out(f"{inst.name}: {len(live)} code{'s' if len(live) != 1 else ''} revoked, {signed_out} phone{'s' if signed_out != 1 else ''} "
          "signed out." + (" The notes file with the old code was removed." if live else ""))
    m.out(f"A new code: assistant review code {store}")


# ---------------------------------------------------------------------------------------------------------------------- reset
def _confirm(inst: Instance, args: Any, ask: Callable[[str], str] | None, what: str) -> None:
    if args.yes:
        return
    if ask is None:
        raise InstanceError(f"{what} asks for confirmation, so it needs a terminal. Add --yes to go ahead without asking.")
    if ask(f"Type {inst.name} to go ahead (anything else stops): ").strip() != inst.name:
        raise InstanceError(f"stopped; {inst.name} is unchanged")


def _reset(m: Instances, args: Any, ask: Callable[[str], str] | None) -> None:
    inst = _load(m, args.store)
    m.out(f"This wipes {inst.name} back to fresh: every chat, memory, file, goal, schedule, connector, login and wallet "
          "card, and the browser's cookies and downloads. Kept: its settings file (model, key, address), the sign-in codes and the phones signed in with them.")
    _confirm(inst, args, ask, "Resetting")
    env = m.compose_env(inst)
    m.run(m.compose(inst, "stop"), cwd=m.root, check=True, env=env)
    m.run(m.compose(inst, "run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "server", "-m", "assistant.review",
                    "reset-state", "/state"), cwd=m.root, check=True, env=env)
    m.run(m.compose(inst, "--profile", "*", "down", "--remove-orphans"), cwd=m.root, check=True, env=env)
    for vol in VOLUMES_RESET:
        m.run(["docker", "volume", "rm", "-f", f"{inst.project}_{vol}"], cwd=m.root, capture_output=True, text=True)
    m.start(inst.name, build_if_missing=False)
    m.out(f"{inst.name} is fresh and running again at {inst.address}.")


# ---------------------------------------------------------------------------------------------------------------------- delete
def _delete_copy(m: Instances, args: Any, ask: Callable[[str], str] | None) -> None:
    inst = _load(m, args.store)
    from .manage import _instance
    _instance(m, argparse.Namespace(icmd="delete", name=inst.name, yes=args.yes), ask)
