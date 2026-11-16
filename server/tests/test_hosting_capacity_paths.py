"""hosting/capacity.py (what the machine has) and hosting/paths.py (where a part's folders are by default)."""

import os
import sys
import types

from assistant.hosting import capacity, paths


def test_this_machine_has_memory_and_processors():
    assert capacity.machine_memory() > 0
    assert capacity.machine_cpu_count() >= 1


def test_memory_is_what_psutil_reports(monkeypatch):
    fake = types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(total=12220676 * 1024))
    monkeypatch.setitem(sys.modules, "psutil", fake)
    assert capacity.machine_memory() == 12220676 * 1024


def test_memory_is_none_when_it_cannot_be_read(monkeypatch):
    def broken():
        raise OSError("no /proc")
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(virtual_memory=broken))
    assert capacity.machine_memory() is None
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(total=0)))
    assert capacity.machine_memory() is None
    monkeypatch.setitem(sys.modules, "psutil", None)                   # not installed
    assert capacity.machine_memory() is None


def test_processors_follow_the_affinity_set_where_the_system_has_one(monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2}, raising=False)
    assert capacity.machine_cpu_count() == 3


def test_processors_fall_back_to_the_machines_count_without_affinity(monkeypatch):      # macOS and Windows have no sched_getaffinity
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert capacity.machine_cpu_count() == 8


def test_every_default_is_what_docker_has_always_used():
    empty: dict[str, str] = {}
    assert paths.default_state_dir(empty) == "/state"
    assert paths.default_keyring_state_dir(empty) == "/state"
    assert paths.default_route_file(empty) == "/run/kelo-route/route.json"
    assert paths.default_vpn_dir(empty) == "/run/kelo-vpn"
    assert paths.default_workroom_socket_dir(empty) == "/run/workroom"
    assert paths.default_downloads_dir(empty) == "/state/downloads"


def test_the_environment_variable_that_already_named_the_folder_wins():
    env = {"KELO_STATE": "/data/kelo/", "KEYRING_STATE": "/data/keys", "EGRESS_ROUTE_FILE": "/r/route.json", "KELO_VPN_DIR": "/v",
           "WORKROOM_SOCKET_DIR": "/w"}
    assert paths.default_state_dir(env) == "/data/kelo/"
    assert paths.default_keyring_state_dir(env) == "/data/keys"
    assert paths.default_route_file(env) == "/r/route.json"
    assert paths.default_vpn_dir(env) == "/v"
    assert paths.default_workroom_socket_dir(env) == "/w"
    assert paths.default_downloads_dir(env) == "/data/kelo/downloads"
    assert paths.default_downloads_dir({**env, "ASSISTANT_DOWNLOADS_DIR": "/builds"}) == "/builds"


def test_the_process_environment_is_used_when_none_is_given(monkeypatch):
    monkeypatch.setenv("KELO_VPN_DIR", "/from-process")
    assert paths.default_vpn_dir() == "/from-process"
    monkeypatch.delenv("KELO_VPN_DIR")
    assert paths.default_vpn_dir() == "/run/kelo-vpn"
