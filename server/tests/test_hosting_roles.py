"""hosting/roles.py: ROLES and compose.yml must describe the same parts. Compared in both directions, so a part, a folder, a port, a
limit or a network added to one and not the other fails here.

The suite has no YAML library, so compose.yml is read by a small reader that knows only the shapes this file uses."""

import importlib.util
import re
from pathlib import Path

import pytest

from assistant.hosting.roles import ROLES, DataFolder, FolderAccess, NetworkAccess, RoleSpec

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_LINES = (ROOT / "compose.yml").read_text().splitlines()


def without_comment(line: str) -> str:
    quote = ""
    for position, char in enumerate(line):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "#" and (position == 0 or line[position - 1].isspace()):
            return line[:position].rstrip()
    return line.rstrip()


def split_items(text: str) -> list[str]:
    """`a, "b, c", d` -> ["a", "b, c", "d"] (commas inside quotes or ${...} do not split)."""
    items, current, quote, depth = [], "", "", 0
    for char in text:
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(current)
            current = ""
            continue
        current += char
    items.append(current)
    return [item.strip().strip("\"'") for item in items if item.strip()]


class Service:
    """One compose service: its keys at 4 spaces, each with the text after the colon and the lines nested under it."""

    def __init__(self, name: str, lines: list[str]):
        self.name = name
        self.keys: dict[str, tuple[str, list[str]]] = {}
        key = None
        for raw in lines:
            line = without_comment(raw)
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            match = re.match(r"^    ([\w<-]+):\s*(.*)$", line) if indent == 4 else None
            if match:
                key = match.group(1)
                self.keys[key] = (match.group(2), [])
            elif key is not None and indent > 4:
                self.keys[key][1].append(line.strip())

    def inline(self, key: str) -> str:
        return self.keys[key][0] if key in self.keys else ""

    def items(self, key: str) -> list[str]:
        """A list written as `[a, b]` (possibly over several lines) or as `- a` lines."""
        if key not in self.keys:
            return []
        inline, nested = self.keys[key]
        if inline.startswith("["):
            text = " ".join([inline, *nested])
            return split_items(text[1:text.rindex("]")])
        return [line[2:].strip().strip("\"'") for line in nested if line.startswith("- ")]

    def environment_names(self) -> set[str]:
        return {m.group(1) for line in self.keys.get("environment", ("", []))[1] if (m := re.match(r"^([A-Z][A-Z0-9_]*):", line))}


def read_services() -> dict[str, Service]:
    services: dict[str, Service] = {}
    inside, name, body = False, None, []
    for raw in COMPOSE_LINES:
        if raw.startswith("services:"):
            inside = True
            continue
        if inside and re.match(r"^\S", raw) and not raw.startswith("#"):
            break
        match = re.match(r"^  ([\w-]+):\s*(#.*)?$", raw) if inside else None
        if match:
            if name:
                services[name] = Service(name, body)
            name, body = match.group(1), []
        elif inside and name:
            body.append(raw)
    if name:
        services[name] = Service(name, body)
    return services


def internal_networks() -> set[str]:
    return {m.group(1) for line in COMPOSE_LINES if (m := re.match(r"^  ([\w-]+): \{internal: true\}", without_comment(line)))}


SERVICES = read_services()


def default_of(text: str) -> str:
    """`${NAME:-default}` -> default; a plain value stays as it is."""
    match = re.fullmatch(r"\$\{[A-Z_]+:?-([^}]*)\}", text)
    return match.group(1) if match else text


def megabytes(text: str) -> int | None:
    value = default_of(text)
    if value in ("", "0"):
        return None
    number, unit = int(value[:-1]), value[-1]
    return number * {"m": 1, "g": 1024}[unit]


def named_volumes(service: Service) -> set[tuple[str, str, FolderAccess]]:
    found = set()
    for entry in service.items("volumes"):
        source, _, rest = entry.partition(":")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", source):      # a path or a ${...}: a bind mount of this install, not a data folder
            continue
        path, _, mode = rest.partition(":")
        found.add((source, path, FolderAccess.READ_ONLY if mode == "ro" else FolderAccess.READ_WRITE))
    return found


def health_of(service: Service) -> tuple[int | None, str | None]:
    text = " ".join(service.items("healthcheck")) or " ".join(service.keys.get("healthcheck", ("", []))[1])
    match = re.search(r"127\.0\.0\.1:(\d+)(/[\w/.-]*)?", text) or re.search(r"\('127\.0\.0\.1', (\d+)\)", text)
    if not match:
        return None, None
    return int(match.group(1)), (match.group(2) if match.lastindex and match.lastindex > 1 else None)


def test_the_reader_finds_every_service():
    assert len(SERVICES) >= 13
    assert {"server", "browser", "workroom", "caddy", "kelo-tunnel"} <= set(SERVICES)
    assert internal_networks() >= {"browser-net", "vpn-net", "keyring-net", "android-net", "updater-net"}


def test_every_compose_service_has_a_role_and_back():
    assert set(ROLES) == set(SERVICES)
    assert all(name == role.name for name, role in ROLES.items())


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_data_folders_match_compose_volumes(name):
    expected = {(f.name, f.mount_path, f.access) for f in ROLES[name].data_folders}
    assert expected == named_volumes(SERVICES[name])
    assert len(expected) == len(ROLES[name].data_folders)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_network_access_matches_compose_networks(name):
    service, role = SERVICES[name], ROLES[name]
    networks = service.items("networks")
    if service.inline("network_mode") == "none":
        assert role.network_access is NetworkAccess.NONE
    elif networks and set(networks) <= internal_networks():
        proxied = "BROWSER_PROXY" in service.environment_names()
        assert role.network_access is (NetworkAccess.EGRESS_PROXY if proxied else NetworkAccess.PRIVATE_ONLY)
    else:
        assert role.network_access is NetworkAccess.INTERNET
    assert (role.network_access is NetworkAccess.NONE) == (service.inline("network_mode") == "none")


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_port_and_health_match_compose(name):
    service, role = SERVICES[name], ROLES[name]
    health_port, health_path = health_of(service)
    published = {int(entry.rsplit(":", 1)[1]) for entry in service.items("ports")}
    if health_port is not None:
        assert role.listen_port == health_port
    if published and health_port is None:                        # (egress publishes the viewer's port, not its own proxy port)
        assert role.listen_port in published
    if published:
        assert role.listen_port is not None
    if health_port is not None:
        assert role.health_path == health_path
    elif role.health_path is not None:                           # a path is promised only where compose really checks it
        pytest.fail(f"{name} names health_path {role.health_path} but compose.yml has no healthcheck for it")


def test_listen_ports_match_the_urls_the_server_is_given():
    server_env = " ".join(SERVICES["server"].keys["environment"][1]) + " " + " ".join(SERVICES["browser"].keys["environment"][1])
    urls = re.findall(r"http://([\w-]+):(\d+)", server_env)
    assert {name for name, _ in urls} >= {"claude-cli", "agy-cli", "updater", "keyring", "android", "egress"}
    for name, port in urls:
        assert ROLES[name].listen_port == int(port), name


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_depends_on_matches_compose(name):
    assert tuple(SERVICES[name].items("depends_on")) == ROLES[name].depends_on
    assert set(ROLES[name].depends_on) <= set(ROLES)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_memory_limit_matches_compose(name):
    assert ROLES[name].memory_limit_mb == megabytes(SERVICES[name].inline("mem_limit"))


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_environment_names_match_compose(name):
    assert set(ROLES[name].env_names) == SERVICES[name].environment_names() - {"ROLE"}
    assert len(set(ROLES[name].env_names)) == len(ROLES[name].env_names)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_a_role_is_a_module_or_a_program(name):
    role = ROLES[name]
    assert bool(role.entry_module) != bool(role.executable)
    if role.entry_module:
        assert importlib.util.find_spec(role.entry_module) is not None


def test_roles_started_by_the_entrypoint_script_name_their_module():
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text()
    for name in ("server", "egress", "vpn", "keyring", "browser"):
        assert f"  {name})" in entrypoint
        if name != "browser":
            assert ROLES[name].entry_module in entrypoint
    assert "ROLE: workroom" not in " ".join(COMPOSE_LINES)      # the work room runs its own agent file, not the entrypoint


def test_no_dependency_loop():
    def depth(name, seen=()):
        assert name not in seen, f"loop through {name}"
        return 1 + max((depth(dep, (*seen, name)) for dep in ROLES[name].depends_on), default=0)
    assert max(depth(name) for name in ROLES) == 4        # caddy -> server -> browser -> egress


def test_a_role_spec_is_immutable_and_has_the_defaults_of_the_design():
    role = RoleSpec(name="x")
    assert role.network_access is NetworkAccess.INTERNET and role.health_path == "/healthz" and role.data_folders == ()
    with pytest.raises(AttributeError):
        role.name = "y"
    with pytest.raises(AttributeError):
        DataFolder("a", "/a", FolderAccess.READ_ONLY).name = "b"
