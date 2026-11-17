import argparse
import subprocess

import pytest

from assistant import limits, profiles
from assistant.cli import main, read_env, setup
from assistant.instances import Instances
from assistant.manage import run_command

GB = 1024**3
MB = 1024**2


class Docker:
    def __init__(self, cpus=4, ram=8 * GB, running=True):
        self.calls, self.cpus, self.ram, self.running = [], cpus, ram, running

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = f"{self.cpus} {self.ram}" if cmd[:2] == ["docker", "info"] else ("server\n" if "--services" in cmd and self.running else "")
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def make(tmp_path, env="BROWSER=chromium\nASSISTANT_TOKEN=main-token-main-token-1234567\n", **kw):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text(env)
    msgs = []
    return Instances(tmp_path, run=Docker(**kw), port_free=lambda p: True, out=msgs.append, disk_free=lambda p: 20 * GB), msgs


def answers(*a):
    it = iter(a)
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return next(it)

    ask.seen = seen
    return ask


def ns(**kw):
    base = {o: None for o in limits.OPTIONS}
    base.update(profile=None, choose=False)
    base.update(kw)
    return argparse.Namespace(**base)


# ------------------------------------------------------------------------------------------------ the numbers
LAYOUTS = [dict(browser=True, clis=0), dict(browser=True, clis=1), dict(browser=True, clis=2), dict(browser=False, clis=0)]


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("name,ram,cpus,disk", [("minimum", 2 * GB, 2.0, 10 * GB), ("recommended", 4 * GB, 4.0, 20 * GB)])
def test_the_container_caps_add_up_to_the_sizes_on_the_site(name, ram, cpus, disk, layout):
    assert profiles.TOTALS[name] == (ram, cpus, disk)
    t = profiles.totals(name, **layout)
    assert (t["ram"], t["cpus"], t["disk"]) == (ram, cpus, disk)          # program files included in the disk
    c = profiles.caps(name, **layout)
    if layout["browser"]:
        assert c["browser_memory"] == max(v for o, v in c.items() if o.endswith("memory"))     # the browser needs the most RAM
    assert all(c[o] >= profiles.FLOOR[o] for o in c)                     # a size never warns about itself


def test_the_caps_people_will_see():
    assert profiles.caps("minimum") == {"memory": 512 * MB, "cpus": 0.5, "disk": 5376 * MB, "browser_memory": 1 * GB, "browser_cpus": 1.0,
                                        "browser_cache": 384 * MB, "workroom_memory": 512 * MB, "workroom_cpus": 0.5, "workroom_disk": 1920 * MB}
    assert profiles.caps("recommended") == {"memory": 1 * GB, "cpus": 1.0, "disk": 12928 * MB, "browser_memory": 2 * GB, "browser_cpus": 2.0,
                                            "browser_cache": 512 * MB, "workroom_memory": 1 * GB, "workroom_cpus": 1.0, "workroom_disk": 4480 * MB}
    two = profiles.caps("minimum", clis=2)
    assert two["cli_memory"] == 204 * MB and two["cli_cpus"] == 0.2 and two["disk"] < profiles.caps("minimum")["disk"]   # their images take disk
    assert profiles.caps("unlimited") == dict.fromkeys(profiles.applicable())
    assert profiles.totals("unlimited") == {"ram": 0.0, "cpus": 0.0, "disk": 0.0}
    assert profiles.spec("minimum") == "RAM 2 GB · CPU 2 · disk 10 GB" and profiles.spec("recommended") == "RAM 4 GB · CPU 4 · disk 20 GB"
    assert [profiles.gb(v) for v in (4 * GB, 11.7 * GB, GB / 2, None)] == ["4 GB", "11.7 GB", "0.5 GB", "?"]


def test_every_number_says_what_it_was_measured_from():
    assert set(profiles.MEASURED) == set(profiles.FLOOR)
    assert all(len(text) > 20 for text in profiles.MEASURED.values())


def test_sizes_are_written_back_the_way_the_command_line_reads_them():
    assert profiles.to_argument("memory", 1536 * MB) == "1536m" and profiles.to_argument("cpus", 0.5) == "0.5" and profiles.to_argument("disk", None) == "none"
    assert [profiles.option_text("memory", v) for v in (256 * MB, 1.5 * GB, None)] == ["256MB", "1.5GB", "no limit"]
    assert profiles.option_text("browser_cpus", 2.0) == "2"


def test_the_server_can_tell_which_profile_its_own_limits_match():
    assert profiles.guess(1 * GB, 1.0, 12928 * MB) == "recommended" and profiles.guess(512 * MB, 0.5, 5376 * MB) == "minimum"
    c = profiles.caps("minimum", clis=2)
    assert profiles.guess(c["memory"], c["cpus"], c["disk"]) == "minimum"                          # whatever else the copy runs
    assert profiles.guess(1 * GB, 1.0, 5 * GB) == "recommended" and profiles.guess(256 * MB, 0.5, 512 * MB) == "minimum"   # sized by an older version
    assert profiles.guess(None, None, None) == "unlimited" and profiles.guess(3 * GB, 1.0, 5 * GB) == "custom" and profiles.guess(1 * GB, None, 5 * GB) == "custom"


# ------------------------------------------------------------------------------------------------ applying a profile
def test_a_profile_sets_only_what_the_copy_has(tmp_path):
    m, _ = make(tmp_path, env="BROWSER=chromium\nASSISTANT_TOKEN=x\nCOMPOSE_PROFILES=claude,agy\n")
    wanted = limits.wanted_for("recommended", m.load())
    assert wanted["browser_memory"] == "1638m" and wanted["cli_memory"] == "409m" and wanted["workroom_memory"] == "819m"
    m.create("alice", browser=False)
    lone = limits.wanted_for("minimum", m.load("alice"))
    assert lone["memory"] == "1024m" and lone["cpus"] == "1" and lone["browser_memory"] is None and lone["cli_memory"] is None
    assert lone["workroom_memory"] == "1024m"


def test_the_current_profile_is_recognized_and_a_single_change_makes_it_custom(tmp_path):
    m, _ = make(tmp_path)
    inst = m.load()
    assert limits.current_profile(inst) == "unlimited"
    for name in ("recommended", "minimum", "unlimited"):
        set_, clear, _ = limits.prepare(m, limits.wanted_for(name, m.load()))
        limits.save(m.load(), set_, clear)
        assert limits.current_profile(m.load()) == name
    limits.save(m.load(), {"SERVER_CPUS": "3"}, [])
    assert limits.current_profile(m.load()) == "custom"


def test_a_copy_without_a_browser_is_judged_only_by_what_it_has(tmp_path):
    m, _ = make(tmp_path)
    m.create("alice", browser=False)
    set_, clear, _ = limits.prepare(m, limits.wanted_for("recommended", m.load("alice")))
    limits.save(m.load("alice"), set_, clear)
    assert limits.current_profile(m.load("alice")) == "recommended"


# ------------------------------------------------------------------------------------------------ the menu
def test_the_menu_shows_the_sites_numbers_and_defaults_to_recommended(tmp_path):
    m, msgs = make(tmp_path)
    ask = answers("")
    got = limits.choose(m, m.load(), ask=ask, out=msgs.append)
    assert got == limits.wanted_for("recommended", m.load())
    assert msgs == ["How much may this Kelo use?",
                    "  1) Recommended   RAM 4 GB · CPU 4 · disk 20 GB",
                    "  2) Minimum       RAM 2 GB · CPU 2 · disk 10 GB",
                    "  3) No limit      everything this computer has (RAM 8 GB · CPU 4 · 20 GB free)",
                    "This computer: RAM 8 GB · CPU 4 · 20 GB free disk"]
    assert ask.seen == ["Type 1-3 and press Enter (Enter alone = 1): "]


@pytest.mark.parametrize("pick,profile", [("1", "recommended"), ("2", "minimum"), ("3", "unlimited")])
def test_each_menu_choice_gives_its_profile(tmp_path, pick, profile):
    m, msgs = make(tmp_path)
    assert limits.choose(m, m.load(), ask=answers(pick), out=msgs.append) == limits.wanted_for(profile, m.load())


def test_a_wrong_answer_is_asked_again_and_custom_is_not_in_the_menu(tmp_path):
    m, msgs = make(tmp_path)
    assert limits.choose(m, m.load(), ask=answers("9", "4", "2"), out=msgs.append) == limits.wanted_for("minimum", m.load())
    assert msgs.count("Please type 1, 2 or 3.") == 2 and not any("Custom" in x for x in msgs)


def test_the_menu_warns_when_the_computer_is_smaller_but_still_lets_you_choose_and_suggests_minimum(tmp_path):
    m, msgs = make(tmp_path, ram=3 * GB, cpus=4)
    ask = answers("1")
    got = limits.choose(m, m.load(), ask=ask, out=msgs.append)
    assert ask.seen == ["Type 1-3 and press Enter (Enter alone = 2): "]
    assert got == limits.wanted_for("recommended", m.load())
    assert msgs[-1] == "Note: this computer has less than Recommended asks for (RAM 3 GB of 4 GB). Kelo still runs, but may be slow under heavy use."


def test_ram_already_promised_to_other_copies_is_taken_into_account(tmp_path):
    m, msgs = make(tmp_path)
    m.create("alice")
    set_, _, _ = limits.prepare(m, {"memory": "4g", "browser_memory": "2g"})
    limits.save(m.load("alice"), set_, [])
    assert limits.committed_ram(m, exclude="bob") == 6 * GB
    limits.choose(m, m.load(), ask=answers("1"), out=msgs.append)
    assert msgs[-1].startswith("Note: other copies may already use 6 GB of RAM; with this one that is more than this computer has.")
    assert limits.committed_ram(m, exclude="alice") == 0                       # a copy does not count against itself


def test_the_prompt_says_the_range_and_what_enter_alone_does():
    from assistant.cli import pick_prompt, pick_range
    assert pick_prompt(["1", "2", "3"], "1") == "Type 1-3 and press Enter (Enter alone = 1): "
    assert pick_range(["k", "1", "2", "3"]) == "k, 1-3" and pick_range(["1", "3"]) == "1, 3"


# ------------------------------------------------------------------------------------------------ older sizes
def test_a_copy_sized_by_an_older_version_keeps_its_size_name_and_moves_to_todays_numbers(tmp_path):
    m, msgs = make(tmp_path)
    m.create("alice")
    old = {"SERVER_MEMORY": str(256 * MB), "SERVER_CPUS": "0.5", "ASSISTANT_DISK_LIMIT": str(512 * MB),
           "BROWSER_MEMORY": str(768 * MB), "BROWSER_CPUS": "1", "BROWSER_CACHE_MB": "100"}
    limits.save(m.load("alice"), old, [])
    limits.save(m.load(), {"SERVER_MEMORY": str(3 * GB)}, [])          # the main copy: a size set by hand
    assert limits.current_profile(m.load("alice")) == "minimum"                                       # the old name, already
    assert run_command(ns(cmd="limits", instance=None, no_apply=True), m) == 0
    assert "alice: sizes moved to today's numbers for Minimum (applies the next time it starts)." in msgs
    assert read_env(m.env_path("alice"), "BROWSER_MEMORY") == str(1 * GB) and read_env(m.env_path("alice"), "WORKROOM_MEMORY") == str(512 * MB)
    assert limits.current_profile(m.load("alice")) == "minimum" and limits.migrate(m, m.load("alice")) is None    # once only
    assert limits.current_profile(m.load()) == "custom" and read_env(m.env_path("main"), "SERVER_MEMORY") == str(3 * GB)   # custom stays
    assert "main  size: Custom (set one by one)" in msgs and "alice  size: Minimum (RAM 2 GB · CPU 2 · disk 10 GB)" in msgs


# ------------------------------------------------------------------------------------------------ at launch
def test_creating_a_copy_asks_when_nothing_was_said_and_a_flag_skips_the_question(tmp_path):
    m, msgs = make(tmp_path)
    base = dict(cmd="instance", icmd="create", no_browser=False, domain=None, no_start=True)
    assert run_command(ns(name="alice", **base), m, ask=answers("2")) == 0
    assert read_env(m.env_path("alice"), "SERVER_MEMORY") == str(512 * MB) and limits.current_profile(m.load("alice")) == "minimum"

    assert run_command(ns(name="bob", profile="recommended", **base), m, ask=answers()) == 0          # no question was asked
    assert limits.current_profile(m.load("bob")) == "recommended"

    assert run_command(ns(name="carol", profile="recommended", memory="2g", **base), m, ask=answers()) == 0
    assert read_env(m.env_path("carol"), "SERVER_MEMORY") == str(2 * GB) and limits.current_profile(m.load("carol")) == "custom"      # a single option wins

    assert run_command(ns(name="dave", cpus="1", **base), m, ask=answers()) == 0                       # a single option is also an answer
    assert read_env(m.env_path("dave"), "SERVER_CPUS") == "1" and not read_env(m.env_path("dave"), "SERVER_MEMORY")


def test_without_a_terminal_nothing_is_asked_and_nothing_is_limited_unless_told(tmp_path):
    m, msgs = make(tmp_path)
    assert run_command(ns(cmd="instance", icmd="create", name="alice", no_browser=False, domain=None, no_start=True), m) == 0
    assert not read_env(m.env_path("alice"), "SERVER_MEMORY") and limits.current_profile(m.load("alice")) == "unlimited"


def test_a_copy_without_a_browser_is_not_asked_about_one_when_created(tmp_path):
    m, msgs = make(tmp_path)
    ask = answers("1")
    assert run_command(ns(cmd="instance", icmd="create", name="alice", no_browser=True, domain=None, no_start=True), m, ask=ask) == 0
    env = m.env_path("alice")
    assert read_env(env, "SERVER_MEMORY") == str(2 * GB) and not read_env(env, "BROWSER_MEMORY")         # the browser's share goes to the rest
    assert read_env(env, "WORKROOM_MEMORY") == str(2 * GB)


def test_limits_can_be_switched_to_a_profile_or_chosen_from_the_menu_later(tmp_path):
    m, msgs = make(tmp_path)
    assert run_command(ns(cmd="limits", instance=None, no_apply=True, profile="minimum"), m) == 0
    assert limits.current_profile(m.load()) == "minimum" and "[Minimum]" in msgs[-2]
    assert run_command(ns(cmd="limits", instance=None, no_apply=True, choose=True), m, ask=answers("1")) == 0
    assert limits.current_profile(m.load()) == "recommended"
    assert run_command(ns(cmd="limits", instance=None, no_apply=True, choose=True), m) == 1                # asking needs a terminal
    assert "needs a terminal" in msgs[-1]


# ------------------------------------------------------------------------------------------------ first run
def test_first_run_asks_for_the_size_after_the_other_questions_and_a_rerun_keeps_it(tmp_path):
    f = tmp_path / ".env"
    logs = []
    ask = answers("", "n", "y", "2")                        # browser default; claude no; agy yes; size: minimum
    setup(f, ask=ask, out=logs.append, run=Docker())
    assert limits.current_profile(Instances(tmp_path, main_env=f, run=Docker()).load()) == "minimum"
    assert read_env(f, "CLI_MEMORY") == str(227 * MB) and read_env(f, "SERVER_MEMORY") == str(456 * MB)       # agy container counted
    assert any("Saved the sizes to" in l and "Minimum (RAM 2 GB · CPU 2 · disk 10 GB)" in l and "`assistant limits --choose`" in l for l in logs)
    setup(f, "chromium", out=lambda _: None, run=Docker())                                                    # plain rerun: not asked again
    assert read_env(f, "SERVER_MEMORY") == str(456 * MB)


def test_the_profile_flag_skips_the_question_and_works_on_a_rerun_too(tmp_path):
    f = tmp_path / ".env"
    setup(f, "chromium", out=lambda _: None, resources="recommended", run=Docker())
    assert read_env(f, "SERVER_MEMORY") == str(1 * GB)
    setup(f, "chromium", out=lambda _: None, resources="unlimited", run=Docker())
    assert not read_env(f, "SERVER_MEMORY") and read_env(f, "ASSISTANT_TOKEN")                                 # limits removed, secrets kept


def test_non_interactive_setup_leaves_everything_unlimited(tmp_path):
    f = tmp_path / ".env"
    setup(f, "chromium", interactive=False, out=lambda _: None, run=Docker())
    assert not read_env(f, "SERVER_MEMORY")


def test_the_real_command_line_takes_the_profile_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "compose.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("ASSISTANT_TOKEN=x" * 5 + "\n")
    monkeypatch.setattr(subprocess, "run", Docker())
    assert main(["limits", "--no-apply", "--profile", "recommended", "--cpus", "2"]) == 0
    assert read_env(tmp_path / ".env", "SERVER_MEMORY") == str(1 * GB) and read_env(tmp_path / ".env", "SERVER_CPUS") == "2"
    assert "[Custom]" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["limits", "--profile", "huge"])


def test_closing_the_input_or_pressing_ctrl_c_cancels_cleanly_and_creates_nothing(tmp_path):
    m, msgs = make(tmp_path)

    def gone(prompt):
        raise EOFError

    assert run_command(ns(cmd="instance", icmd="create", name="alice", no_browser=False, domain=None, no_start=True), m, ask=gone) == 1
    assert "Cancelled" in msgs[-1] and m.names() == []


# ------------------------------------------------------------------------------------------------ nothing set = the computer itself
def test_where_nothing_is_set_the_computers_own_capacity_is_shown_not_just_no_limit(tmp_path):
    m, _ = make(tmp_path)
    cap = limits.capacity(m)
    assert (cap.cpus, cap.ram, cap.disk_free) == (4, 8 * GB, 20 * GB)
    inst = m.load()
    assert limits.shown(inst, "memory", cap) == "whole computer (8.0GB)" and limits.shown(inst, "cpus", cap) == "all 4 CPUs"
    assert limits.shown(inst, "disk", cap) == "whole disk (20.0GB free)" and limits.shown(inst, "browser_cache", cap) == "browser default (1 GB)"
    limits.save(inst, {"SERVER_MEMORY": str(GB), "SERVER_CPUS": "1.5"}, [])
    assert limits.shown(m.load(), "memory", cap) == "1.0GB" and limits.shown(m.load(), "cpus", cap) == "1.5"        # a limit that was set is shown as is
    assert limits.shown(m.load(), "disk", cap) == "whole disk (20.0GB free)"


def test_when_docker_cannot_say_what_the_computer_has_the_words_still_name_the_whole_computer(tmp_path):
    m, _ = make(tmp_path)
    m.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
    m.disk_free = lambda p: None
    cap = limits.capacity(m)
    inst = m.load()
    assert [limits.shown(inst, o, cap) for o in ("memory", "cpus", "disk")] == ["whole computer", "all CPUs", "whole disk"]


def test_setting_something_to_none_is_confirmed_with_what_it_now_may_use(tmp_path):
    m, msgs = make(tmp_path)
    assert run_command(ns(cmd="limits", instance=None, no_apply=True, memory="1g", cpus="2"), m) == 0
    assert run_command(ns(cmd="limits", instance=None, no_apply=True, memory="none", cpus="none", disk="none"), m) == 0
    line = next(x for x in reversed(msgs) if x.startswith("the main copy:"))
    assert "server RAM whole computer (8.0GB)" in line and "server CPUs all 4 CPUs" in line and "whole disk (20.0GB free)" in line
    assert "[No limit]" in line and "no limit" not in line


def test_the_first_run_message_calls_the_unlimited_choice_no_limit(tmp_path):
    f = tmp_path / ".env"
    logs = []
    setup(f, "chromium", out=logs.append, resources="unlimited", run=Docker())
    assert any("Saved the sizes to" in x and "No limit" in x for x in logs)
    assert read_env(f, "WORKROOM_MEMORY") == "0"                                   # No limit lifts the work room's default cap too
