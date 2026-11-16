"""What each Kelo part (a "role") is and needs, written once and free of any operating system.

Docker's compose.yml and the native hosts (docs/native-server.md) are both checked against `ROLES`
(server/tests/test_hosting_roles.py compares it with compose.yml in both directions), so a new part, a new folder or a changed limit
is written here and in compose.yml, and a test fails until both agree. Roles are named like their compose services."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class NetworkAccess(enum.Enum):
    """How far a role may reach on the network."""

    NONE = "none"                   # the work room: no network at all
    PRIVATE_ONLY = "private_only"   # reaches only other Kelo parts (DevTools, sidecars)
    EGRESS_PROXY = "egress_proxy"   # the browser: out only through the egress proxy
    INTERNET = "internet"           # server, key container, egress, VPN, updater, tunnels


class FolderAccess(enum.Enum):
    """Whether a role may change a data folder."""

    READ_WRITE = "rw"
    READ_ONLY = "ro"


@dataclass(frozen=True)
class DataFolder:
    """A folder a role keeps or reads; in Docker a named volume."""

    name: str            # the compose volume name, e.g. "keyring-state"
    mount_path: str      # where the role sees it in Docker, e.g. "/keyring-state"
    access: FolderAccess


@dataclass(frozen=True)
class RoleSpec:
    """Everything a host needs to start one role and keep it apart from the others (no values, only names and limits)."""

    name: str                               # "server", "keyring", "browser", ... (= compose service name)
    entry_module: str = ""                  # "assistant.keyring_service"; empty for a program that is not ours
    executable: str = ""                    # a program instead of a module (chrome, cloudflared, caddy, the Android bridge script)
    data_folders: tuple[DataFolder, ...] = ()
    network_access: NetworkAccess = NetworkAccess.INTERNET
    listen_port: int | None = None
    depends_on: tuple[str, ...] = ()
    memory_limit_mb: int | None = None      # None = no limit by default (the owner may set one)
    health_path: str | None = "/healthz"    # None = checked by connecting to the port, or not checked
    env_names: tuple[str, ...] = ()         # environment variables it reads (never values)


def _rw(name: str, mount_path: str) -> DataFolder:
    return DataFolder(name, mount_path, FolderAccess.READ_WRITE)


def _ro(name: str, mount_path: str) -> DataFolder:
    return DataFolder(name, mount_path, FolderAccess.READ_ONLY)


ROLES: dict[str, RoleSpec] = {role.name: role for role in (
    RoleSpec(
        name="server", entry_module="assistant.server", listen_port=8080, depends_on=("browser",),
        data_folders=(_rw("server-state", "/state"), _rw("workroom-io", "/run/workroom"), _ro("browser-downloads", "/browser-downloads"),
                      _rw("kelo-route", "/run/kelo-route"), _rw("kelo-vpn", "/run/kelo-vpn")),
        env_names=("WORKROOM_SOCKET_DIR", "BROWSER_CDP_HOST", "ASSISTANT_CLI_CLAUDE_URL", "ASSISTANT_CLI_AGY_URL", "KELO_ROUTE_DIR",
                   "KELO_VPN_DIR", "BROWSER_DOWNLOADS_DIR", "BROWSER_LANG", "ASSISTANT_UPDATER_URL", "KEYRING_URL", "KEYRING_TOKEN",
                   "ANDROID_BRIDGE_URL", "ANDROID_BRIDGE_TOKEN", "ANDROID_DEVICE"),
    ),
    RoleSpec(
        name="browser", executable="chrome", listen_port=9223, depends_on=("egress",), health_path="/json/version",
        data_folders=(_rw("browser-data", "/data"), _rw("browser-downloads", "/downloads")),
        network_access=NetworkAccess.EGRESS_PROXY,
        env_names=("BROWSER_CACHE_MB", "BROWSER_PROXY", "BROWSER_LANG", "TZ"),
    ),
    RoleSpec(
        name="egress", entry_module="assistant.egress", listen_port=3128, health_path=None,
        data_folders=(_ro("kelo-route", "/run/kelo-route"),), memory_limit_mb=256,
        env_names=("EGRESS_RELAY",),
    ),
    RoleSpec(
        name="vpn", entry_module="assistant.vpn_sidecar", listen_port=1080, health_path=None,
        data_folders=(_ro("kelo-vpn", "/run/kelo-vpn"),), memory_limit_mb=64,
    ),
    RoleSpec(
        name="keyring", entry_module="assistant.keyring_service", listen_port=9500,
        data_folders=(_rw("keyring-state", "/keyring-state"),), memory_limit_mb=128,
        env_names=("KEYRING_TOKEN", "KEYRING_STATE"),
    ),
    RoleSpec(
        name="android", executable="bridge.py", listen_port=8090,
        data_folders=(_rw("android-adb", "/home/bridge/.android"),), memory_limit_mb=256,
        env_names=("ANDROID_DEVICE", "ANDROID_BRIDGE_TOKEN", "ANDROID_ADB_HOST"),
    ),
    RoleSpec(
        name="workroom", entry_module="assistant.workroom_agent", health_path=None,
        data_folders=(_rw("workroom-io", "/run/workroom"), _rw("workroom-scratch", "/work"), _rw("workroom-tools", "/tools")),
        network_access=NetworkAccess.NONE, memory_limit_mb=2048,
        env_names=("WORKROOM_DISK",),
    ),
    RoleSpec(
        name="claude-cli", entry_module="assistant.cli_runner", listen_port=9300,
        data_folders=(_rw("claude-home", "/home/cli"),),
        env_names=("CLI_RUNNER_TOKEN",),
    ),
    RoleSpec(
        name="agy-cli", entry_module="assistant.cli_runner", listen_port=9300,
        data_folders=(_rw("agy-home", "/home/cli"),),
        env_names=("CLI_RUNNER_TOKEN",),
    ),
    RoleSpec(
        name="updater", entry_module="assistant.updater_service", listen_port=9400, health_path=None, memory_limit_mb=256,
        env_names=("UPDATER_TOKEN", "KELO_ROOT", "KELO_ROOT_HOST", "KELO_REGISTRY", "KELO_RELEASES_URL", "KELO_DOCKER_SOCKET",
                   "KELO_COSIGN_KEY"),
    ),
    RoleSpec(name="cloudflared", executable="cloudflared", health_path=None),
    RoleSpec(name="kelo-tunnel", executable="cloudflared", health_path=None),
    RoleSpec(
        name="caddy", executable="caddy", listen_port=443, depends_on=("server",), health_path=None,
        data_folders=(_rw("caddy-data", "/data"), _rw("caddy-config", "/config")),
        env_names=("DIRECT_HOST",),
    ),
)}
