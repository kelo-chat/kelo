"""Where a part's folders are by default. Each function reads the environment variable that already named the folder in Docker and
falls back to the value Docker has always used, so nothing changes in a container; a native install sets the variables (or, in
phase 1, the host's own Paths object does)."""

from __future__ import annotations

import os
from collections.abc import Mapping

STATE_DIR_IN_DOCKER = "/state"   # the server's state volume as the container sees it (also what `docker cp` addresses)


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def default_state_dir(environ: Mapping[str, str] | None = None) -> str:
    """The folder holding the database and everything else the server keeps: KELO_STATE, else /state."""
    return _environment(environ).get("KELO_STATE", STATE_DIR_IN_DOCKER)


def default_keyring_state_dir(environ: Mapping[str, str] | None = None) -> str:
    """The key container's own folder: KEYRING_STATE, else /state."""
    return _environment(environ).get("KEYRING_STATE", STATE_DIR_IN_DOCKER)


def default_route_file(environ: Mapping[str, str] | None = None) -> str:
    """The file in which the server tells egress "direct" or "through the VPN": EGRESS_ROUTE_FILE, else /run/kelo-route/route.json."""
    return _environment(environ).get("EGRESS_ROUTE_FILE", "/run/kelo-route/route.json")


def default_vpn_dir(environ: Mapping[str, str] | None = None) -> str:
    """The folder where the server writes the VPN sidecar's files: KELO_VPN_DIR, else /run/kelo-vpn."""
    return _environment(environ).get("KELO_VPN_DIR", "/run/kelo-vpn")


def default_workroom_socket_dir(environ: Mapping[str, str] | None = None) -> str:
    """The folder the server and the work room share for their sockets: WORKROOM_SOCKET_DIR, else /run/workroom."""
    return _environment(environ).get("WORKROOM_SOCKET_DIR", "/run/workroom")


def default_downloads_dir(environ: Mapping[str, str] | None = None) -> str:
    """Where the app builds offered at /download live: ASSISTANT_DOWNLOADS_DIR, else <state folder>/downloads."""
    env = _environment(environ)
    return env.get("ASSISTANT_DOWNLOADS_DIR") or f"{default_state_dir(env).rstrip('/')}/downloads"
