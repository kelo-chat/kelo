import argparse
import json
import subprocess

import pytest

from assistant import limits
from assistant.cli import main, read_env
from assistant.instances import Instances
from assistant.limits import LimitError
from assistant.manage import run_command

GB = 1024**3
MB = 1024**2


class Docker:
    """A fake docker: 4 CPUs and 8 GB, with whatever containers are listed. Records every command."""

    def __init__(self, ps=None, stats="", running=True):
        self.calls, self.ps, self.stats, self.running = [], ps or [], stats, running

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[:3] == ["docker", "info", "--format"]:
            out = f"4 {8 * GB}"
        elif "ps" in cmd and "--format" in cmd:
            out = "\n".join(json.dumps(r) for r in self.ps)
        elif "ps" in cmd and "--services" in cmd:
            out = "server\n" if self.running else ""
        elif cmd[:2] == ["docker", "stats"]:
            out = self.stats
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def make(tmp_path, **kw):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("BROWSER=chromium\nASSISTANT_TOKEN=main-token-main-token-1234567\nCOMPOSE_PROFILES=claude,agy\n")
    run = Docker(**kw)
    msgs = []
    return Instances(tmp_path, run=run, port_free=lambda p: True, out=msgs.append, disk_free=lambda p: 20 * GB), run, msgs


# ------------------------------------------------------------------------------------------------ checking values
def test_each_knob_is_converted_for_docker_and_the_app(tmp_path):
    m, _, _ = make(tmp_path)
    set_, clear, warn = limits.prepare(m, {"memory": "1g", "cpus": "1.5", "disk": "5g", "browser_memory": "1.5g", "browser_cpus": "2",
                                          "browser_cache": "500m", "cli_memory": "512m", "cli_cpus": "0.5"})
    assert set_ == {"SERVER_MEMORY": str(GB), "SERVER_CPUS": "1.5", "ASSISTANT_DISK_LIMIT": str(5 * GB), "BROWSER_MEMORY": str(int(1.5 * GB)),
                    "BROWSER_CPUS": "2", "BROWSER_CACHE_MB": "500", "CLI_MEMORY": str(512 * MB), "CLI_CPUS": "0.5"}
    assert clear == [] and warn == []


def test_none_removes_a_limit_and_leaves_unmentioned_ones_alone(tmp_path):
    m, _, _ = make(tmp_path)
    set_, clear, _ = limits.prepare(m, {"memory": "none", "cpus": "0", "disk": "unlimited", "browser_memory": None})
    assert set_ == {} and clear == ["SERVER_MEMORY", "SERVER_CPUS", "ASSISTANT_DISK_LIMIT"]


@pytest.mark.parametrize("opt,val,text", [
    ("cpus", "9", "4 CPUs"), ("browser_cpus", "5", "4 CPUs"), ("memory", "9g", "8.0GB"), ("browser_memory", "100g", "too much"),
    ("memory", "1m", "at least"), ("memory", "lots", "--memory"), ("cpus", "fast", "--cpus"), ("disk", "-1g", "--disk"), ("cpus", "0.001", "at least 0.01")])
def test_values_that_cannot_work_are_refused_with_the_option_named(tmp_path, opt, val, text):
    m, _, _ = make(tmp_path)
    with pytest.raises(LimitError, match=text):
        limits.prepare(m, {opt: val})


def test_small_but_possible_values_only_warn(tmp_path):
    m, _, _ = make(tmp_path)
    set_, _, warn = limits.prepare(m, {"memory": "128m", "browser_memory": "512m", "disk": "10m"})
    assert set_["SERVER_MEMORY"] == str(128 * MB) and len(warn) == 3 and all("below the minimum" in w for w in warn)
    assert "256" in warn[0] and "600,000-cell" in warn[0]                 # it says what the minimum is and what it rests on


def test_the_computers_own_size_is_the_only_ceiling_so_many_copies_can_each_have_a_big_share(tmp_path):
    m, _, _ = make(tmp_path)
    assert limits.prepare(m, {"memory": "8g", "cpus": "4"})[0] == {"SERVER_MEMORY": str(8 * GB), "SERVER_CPUS": "4"}
    m.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")          # docker cannot say: do not guess a ceiling
    assert limits.prepare(m, {"memory": "64g", "cpus": "32"})[0]["SERVER_MEMORY"] == str(64 * GB)


def test_saving_and_reading_back_keeps_everything_else_in_the_settings_file(tmp_path):
    m, _, _ = make(tmp_path)
    inst = m.load()
    limits.save(inst, {"SERVER_MEMORY": str(GB), "SERVER_CPUS": "1.5", "ASSISTANT_DISK_LIMIT": str(5 * GB), "BROWSER_CACHE_MB": "500"}, [])
    cur = limits.current(m.load())
    assert (cur["memory"], cur["cpus"], cur["disk"], cur["browser_cache"], cur["browser_memory"]) == ("1.0GB", "1.5", "5.0GB", "500MB", "no limit")
    limits.save(m.load(), {}, ["SERVER_CPUS"])
    text = (tmp_path / ".env").read_text()
    assert "SERVER_CPUS" not in text and "SERVER_MEMORY=" in text and "COMPOSE_PROFILES=claude,agy" in text and "ASSISTANT_TOKEN=main-token" in text


# ------------------------------------------------------------------------------------------------ the commands
def ns(**kw):
    base = {o: None for o in limits.OPTIONS}
    base.update(kw)
    return argparse.Namespace(**base)


def test_a_new_copy_can_be_given_its_limits_up_front_and_a_bad_value_creates_nothing(tmp_path):
    m, run, msgs = make(tmp_path)
    args = ns(cmd="instance", icmd="create", name="alice", no_browser=False, domain=None, no_start=False, memory="1g", cpus="1.5", disk="5g",
              browser_memory="2g")
    assert run_command(args, m) == 0
    env = m.env_path("alice")
    assert read_env(env, "SERVER_MEMORY") == str(GB) and read_env(env, "SERVER_CPUS") == "1.5" and read_env(env, "BROWSER_MEMORY") == str(2 * GB)
    assert read_env(env, "ASSISTANT_DISK_LIMIT") == str(5 * GB) and read_env(env, "SERVER_PORT") == "8100"
    bad = ns(cmd="instance", icmd="create", name="bob", no_browser=False, domain=None, no_start=False, cpus="64")
    assert run_command(bad, m) == 1 and "4 CPUs" in msgs[-1] and m.names() == ["alice"]


def test_changing_limits_on_a_running_copy_restarts_only_what_changed_and_keeps_data(tmp_path):
    m, run, msgs = make(tmp_path, running=True)
    m.create("alice")
    assert run_command(ns(cmd="limits", instance="alice", no_apply=False, memory="512m", disk="2g"), m) == 0
    assert read_env(m.env_path("alice"), "SERVER_MEMORY") == str(512 * MB) and "Applied" in msgs[-1]
    assert any("up" in c and "-p" in c and "assistant-u-alice" in c for c in run.calls)            # compose recreates what changed
    assert not any("--volumes" in c or "down" in c for c in run.calls)                              # nothing is deleted

    msgs.clear()
    assert run_command(ns(cmd="limits", instance="alice", no_apply=True, cpus="1"), m) == 0 and "next time" in msgs[-1]
    run.running = False
    before = len(run.calls)
    assert run_command(ns(cmd="limits", instance="alice", no_apply=False, cpus="2"), m) == 0 and "next time" in msgs[-1]
    assert not [c for c in run.calls[before:] if "up" in c]                                           # a stopped copy is not started by a setting


def test_limits_are_set_on_the_main_copy_too_without_a_name(tmp_path):
    m, run, msgs = make(tmp_path)
    assert run_command(ns(cmd="limits", instance=None, no_apply=True, memory="2g", browser_memory="1g", cli_memory="512m"), m) == 0
    env = tmp_path / ".env"
    assert read_env(env, "SERVER_MEMORY") == str(2 * GB) and read_env(env, "CLI_MEMORY") == str(512 * MB) and "main copy" in msgs[-2]


def test_the_report_shows_what_each_container_uses_and_warns_about_overcommit_and_missing_limits(tmp_path):
    ps = [{"ID": "aaa111" + "0" * 58, "Service": "server", "State": "running"}, {"ID": "bbb222" + "0" * 58, "Service": "browser", "State": "running"}]
    stats = "aaa111000000|46.1MiB / 7.8GiB|0.30%\nbbb222000000|264MiB / 1GiB|2.50%\n"
    m, run, msgs = make(tmp_path, ps=ps, stats=stats)
    limits.save(m.load(), {"SERVER_MEMORY": str(6 * GB), "BROWSER_MEMORY": str(GB), "SERVER_CPUS": "2"}, [])
    m.create("alice", browser=False)
    limits.save(m.load("alice"), {"SERVER_MEMORY": str(4 * GB), "ASSISTANT_DISK_LIMIT": str(2 * GB)}, [])

    def get(url, headers=None, timeout=None):
        class R:
            def json(self_):
                return {"disk": {"used": 3 * MB, "limit": 2 * GB if "8100" in url else None}}
        return R()

    text = "\n".join(limits.report(m, get=get))
    assert "main" in text and "alice" in text
    assert "RAM 46MB now, limit 6.0GB" in text and "CPU 0.30% now, limit 2" in text and "RAM 264MB now, limit 1.0GB" in text
    assert "3.0MB used, limit 2.0GB" in text and "3.0MB used, limit whole disk (20.0GB free)" in text
    assert "This computer: RAM 8 GB · CPU 4 · 20 GB free disk" in text and "RAM limits added up: 15.0GB of 8.0GB" in text   # 11 GB set + the two work rooms' 2 GB default and "more than this computer has" in text
    assert "limit no limit" not in text and "browser cache browser default (1 GB)" in text            # nothing is reported as a bare "no limit"
    assert "main/claude-cli" not in text                                                            # only containers that exist are listed
    assert "workroom    RAM limit 2.0GB (default)  |  CPU limit all 4 CPUs" in text and "work room scratch 2.0GB (default)" in text
    assert limits.report(m, only="alice")[0] == "alice  size: Custom (set one by one)" and "main" not in "\n".join(limits.report(m, only="alice", get=get)).split("This computer")[0]


def test_a_lone_copy_without_limits_gets_no_nagging_but_two_do(tmp_path):
    m, _, _ = make(tmp_path)
    assert not any(line.startswith("!") for line in limits.report(m))
    m.create("alice")
    assert any(line.startswith("! RAM is not capped on:") and "alice/server" in line for line in limits.report(m))


def test_the_real_command_line_reaches_limits(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=x" * 5 + "\n")
    monkeypatch.setattr(subprocess, "run", Docker())
    assert main(["limits", "--no-apply", "--memory", "1g", "--disk", "none"]) == 0
    assert read_env(tmp_path / ".env", "SERVER_MEMORY") == str(GB)
    assert main(["limits", "--memory", "banana"]) == 1 and "--memory" in capsys.readouterr().out


def test_an_uncapped_copy_reads_as_the_computer_it_runs_on(tmp_path):
    m, _, _ = make(tmp_path)
    text = "\n".join(limits.report(m))
    assert "limit whole computer (8.0GB)  |  CPU limit all 4 CPUs" in text and "limit whole disk (20.0GB free) (not running, usage unknown)" in text
    assert "no limit" not in text


def test_the_work_room_is_capped_by_default_and_none_lifts_the_cap_instead_of_bringing_the_default_back(tmp_path):
    m, run, msgs = make(tmp_path)
    assert limits.current(m.load())["workroom_memory"] == "2.0GB (default)"
    set_, clear, warn = limits.prepare(m, {"workroom_memory": "none", "workroom_disk": "4g", "workroom_cpus": "1"})
    assert set_ == {"WORKROOM_MEMORY": "0", "WORKROOM_DISK": str(4 * GB), "WORKROOM_CPUS": "1"} and clear == [] and warn == []
    limits.save(m.load(), set_, clear)
    cur = limits.current(m.load())
    assert (cur["workroom_memory"], cur["workroom_disk"], cur["workroom_cpus"]) == ("no limit", "4.0GB", "1")
    assert limits.prepare(m, {"workroom_memory": "256m"})[2][0].startswith("work room RAM 256MB is below the minimum (384MB): work room idle 20 MB")
