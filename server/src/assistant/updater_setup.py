"""`assistant updater setup | status | off`: the updater container (updater_service.py) on this computer, main copy only.

setup writes into the main copy's .env, never printing a secret:
  UPDATER_TOKEN       made once (cli.ensure_updater_token); only the main copy's server and the updater get it
  KELO_ROOT           this folder, mounted at the same path inside the updater, so compose paths mean the same there (Windows:
                      the Docker VM's path of it, with KELO_ROOT_HOST the Windows path)
  KELO_UID, KELO_GID  the folder's owner: the updater runs as them, so the files it writes (rollback points, env holds) stay theirs
  DOCKER_GID          the Docker socket's group as Docker sees it (read with a one-off container of the updater image)
  KELO_DOCKER_SOCKET  the socket's path on the Docker host (default /var/run/docker.sock; a rootless Docker's own path)
  KELO_REGISTRY       where release images come from (--registry; ghcr.io/kelo-chat when unset)
  KELO_AUTO_UPDATE    1 = nightly auto-update until the owner changes it in the app (--auto-update; off by default)
and adds the `updater` profile to COMPOSE_PROFILES. Then it builds the updater image when it is missing (copies built from source)
and starts it, recreating the server so it gets the secret and joins updater-net.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .cli import ensure_updater_token, read_env, write_env
from .instances import MAIN, InstanceError, Instances

PROFILE = "updater"
VERSION_ENV = "KELO_RELEASE_VERSION"   # the installer's --version: pull_release takes the images of exactly this release


def _profiles(env: Path) -> list[str]:
    return [p for p in read_env(env, "COMPOSE_PROFILES").split(",") if p]


def docker_socket() -> str:
    """The socket path on the Docker host: a rootless Docker's own path from DOCKER_HOST on Linux, else the standard one (Docker
    Desktop and colima keep the socket inside their VM at the standard path)."""
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("unix://") and os.uname().sysname == "Linux":
        path = host[len("unix://"):]
        if path and os.path.exists(path):
            return path
    return "/var/run/docker.sock"


def container_path(root: Path) -> tuple[str, str]:
    """(KELO_ROOT, KELO_ROOT_HOST). On Windows (Docker Desktop) the folder C:\\Users\\me\\kelo is seen by Linux containers and by the
    Docker VM as /run/desktop/mnt/host/c/Users/me/kelo, so that is the path inside the updater and the Windows path is the mount's
    source; elsewhere both are the same path and KELO_ROOT_HOST stays empty."""
    text = str(root)
    if os.name == "nt" and len(text) > 2 and text[1] == ":":
        return "/run/desktop/mnt/host/" + text[0].lower() + text[2:].replace("\\", "/"), text
    return text, ""


def setup(m: Instances, *, auto: bool | None = None, registry: str | None = None, start: bool = True) -> int:
    from .release import check_registry
    from .updater_service import shared_updater_image

    inst = m.load(MAIN)
    env = inst.env
    if registry:
        write_env(env, {"KELO_REGISTRY": check_registry(registry)})
    made = ensure_updater_token(env)
    sock = docker_socket()
    st = m.root.stat()
    root, host = container_path(m.root)
    write_env(env, {"KELO_ROOT": root, "KELO_UID": str(st.st_uid), "KELO_GID": str(st.st_gid), "KELO_DOCKER_SOCKET": sock})
    if host:
        write_env(env, {"KELO_ROOT_HOST": host})
    if auto is not None:
        write_env(env, {"KELO_AUTO_UPDATE": "1" if auto else "0"})
    profiles = _profiles(env)
    if PROFILE not in profiles:
        write_env(env, {"COMPOSE_PROFILES": ",".join([*profiles, PROFILE])})
    m.out("Saved the updater settings in " + str(env) + (" (and made its secret, UPDATER_TOKEN; it is never shown)." if made else "."))
    image = shared_updater_image()
    if m.run(["docker", "image", "inspect", image], cwd=m.root, capture_output=True, text=True).returncode != 0:
        m.out(f"Building the updater image {image} once...")
        m.run(m.compose(inst, "--profile", PROFILE, "build", "updater"), cwd=m.root, check=True)
    r = m.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "stat", "-v", f"{sock}:/var/run/docker.sock", image,
               "-c", "%g", "/var/run/docker.sock"], cwd=m.root, capture_output=True, text=True)
    gid = (r.stdout or "").strip()
    if r.returncode == 0 and gid.isdigit():
        write_env(env, {"DOCKER_GID": gid})
    else:
        m.out("warning: could not read the Docker socket's group; the updater may not reach Docker (set DOCKER_GID in .env)")
    on = read_env(env, "KELO_AUTO_UPDATE") == "1"
    m.out(f"Nightly auto-update: {'on' if on else 'off'} (the owner can change it in the app: My → Server update).")
    if not start:
        m.out("Start it with: docker compose up -d updater server")
        return 0
    running = m.running(MAIN)
    m.run(m.compose(inst, "up", "-d", "--no-build", "--no-deps", "updater", *(["server"] if "server" in running else [])),
          cwd=m.root, check=True, env=m.compose_env(inst))
    m.out("The updater runs. The owner's app shows server updates under My → Server update.")
    return 0


def pull_release(m: Instances, version: str | None = None) -> str:
    """A first install from the release (the installer's --from-release): pull Kelo's images of the newest release (or of exactly
    `version`, else $KELO_RELEASE_VERSION: the installer's --version), check each one like an update does (digest, signature,
    version), then put them on the shared tags. The Claude / agy sidecars are not in a release (proprietary CLIs);
    `docker compose up -d` builds them here. Returns the version."""
    from . import release as rel
    from .updater_service import shared_updater_image

    env = m.load(MAIN).env
    registry = read_env(env, "KELO_REGISTRY") or rel.DEFAULT_REGISTRY
    version = (version or os.environ.get(VERSION_ENV) or "").strip()
    if version:
        r = rel.by_version(version, registry, m.run)
    else:
        r = rel.fetch(read_env(env, "KELO_RELEASES_URL") or rel.DEFAULT_RELEASES_URL, registry)
    v = rel.Verifier(m.run, cosign=rel.cosign_command(), key=read_env(env, "KELO_COSIGN_KEY") or None)
    for comp in ("server", "workroom", "updater"):
        m.out(f"Downloading and checking {r.images[comp].repo} {r.version}...")
        v.fetch_and_check(r.images[comp], r.version)
    for comp in ("server", "workroom", "updater"):
        tag = shared_updater_image() if comp == "updater" else m.shared_image(rel.COMPONENTS[comp])
        m.run(["docker", "tag", r.images[comp].ref, tag], cwd=m.root, check=True, capture_output=True, text=True)
    m.out(f"Release {r.version}: every image is the official one (digest and signature checked).")
    return r.version


def off(m: Instances) -> int:
    inst = m.load(MAIN)
    profiles = _profiles(inst.env)
    if PROFILE in profiles:
        write_env(inst.env, {"COMPOSE_PROFILES": ",".join(p for p in profiles if p != PROFILE)})
    m.run(m.compose(inst, "--profile", PROFILE, "rm", "-s", "-f", "updater"), cwd=m.root, capture_output=True, text=True)
    m.out("The updater is off (its container is removed; `assistant updater setup` brings it back). `assistant update` still works.")
    return 0


def status(m: Instances) -> int:
    from .updater_service import current_release
    from .updates import Updater

    inst = m.load(MAIN)
    on = PROFILE in _profiles(inst.env)
    now = current_release(m, Updater(m))
    m.out(f"updater: {'on' if on else 'off'}" + ("" if on else "  (assistant updater setup)"))
    m.out(f"images: {'signed release ' + str(now['version']) if now['mode'] == 'registry' else 'built here from the source files'}"
          + ("" if now["mode"] == "registry" else "  (update with: assistant update)"))
    try:
        state: dict[str, Any] = json.loads((m.dir / ".updater" / "state.json").read_text())
    except (OSError, ValueError):
        state = {}
    latest = (state.get("latest") or {}).get("version")
    if latest:
        m.out(f"newest release: {latest}")
    job = state.get("job") or {}
    if job:
        m.out(f"last job: {job.get('action')} {job.get('from') or '?'} -> {job.get('to') or '?'}: {job.get('state')}"
              + (f" ({job['error']})" if job.get("error") else ""))
    m.out(f"nightly auto-update default: {'on' if read_env(inst.env, 'KELO_AUTO_UPDATE') == '1' else 'off'} (the app's switch wins)")
    return 0


def run_command(args: Any, manager: Instances | None = None) -> int:
    env_file = getattr(args, "env_file", None)
    m = manager or Instances(Path.cwd(), main_env=Path(env_file).resolve() if env_file else None)
    try:
        if args.ucmd == "setup":
            if getattr(args, "pull_release", False):
                if args.registry:
                    from .release import check_registry
                    write_env(m.load(MAIN).env, {"KELO_REGISTRY": check_registry(args.registry)})
                pull_release(m, getattr(args, "release_version", None))
            return setup(m, auto=args.auto, registry=args.registry, start=not args.no_start)
        if args.ucmd == "off":
            return off(m)
        return status(m)
    except InstanceError as e:
        m.out(f"error: {e}")
        return 1
    except subprocess.CalledProcessError as e:
        m.out(f"error: a docker command failed (exit {e.returncode}); see the output above")
        return 1
    except Exception as e:  # noqa: BLE001 - ReleaseError from a bad --registry
        m.out(f"error: {e}")
        return 1
