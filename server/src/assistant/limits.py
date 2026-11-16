"""Give each copy of the assistant its own share of this computer: RAM, compute and storage, each set on its own.

- RAM and CPUs are enforced by Docker (a container that goes over its RAM is stopped, one that wants more CPU is slowed down).
- Storage is enforced by the assistant for everything it stores (database, uploads, images, notes): a write that would
  pass the cap is refused with a message, and chat keeps working. (Docker cannot cap a volume's size on every system.)
- Nothing is limited unless you say so, and no limit is ever applied on its own.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from . import profiles
from .cli import pick_prompt, read_env, remove_env, write_env
from .instances import MAIN, Instance, InstanceError, Instances
from .quota import fmt_size, parse_size

MB = 1024 * 1024
# option -> (setting in the copy's env file, kind, what it limits)
OPTIONS: dict[str, tuple[str, str, str]] = {
    "memory": ("SERVER_MEMORY", "size", "server RAM"),
    "cpus": ("SERVER_CPUS", "cpus", "server CPUs"),
    "disk": ("ASSISTANT_DISK_LIMIT", "size", "storage (the assistant's own data)"),
    "browser_memory": ("BROWSER_MEMORY", "size", "browser RAM"),
    "browser_cpus": ("BROWSER_CPUS", "cpus", "browser CPUs"),
    "browser_cache": ("BROWSER_CACHE_MB", "mb", "browser disk cache"),
    "cli_memory": ("CLI_MEMORY", "size", "Claude/agy container RAM"),
    "cli_cpus": ("CLI_CPUS", "cpus", "Claude/agy container CPUs"),
    "workroom_memory": ("WORKROOM_MEMORY", "size", "work room RAM"),
    "workroom_cpus": ("WORKROOM_CPUS", "cpus", "work room CPUs"),
    "workroom_disk": ("WORKROOM_DISK", "size", "work room scratch space"),
}
# The work room runs code the agent wrote, so it is capped even when nothing was chosen (compose.yml has the same defaults).
# `none` writes 0 for these, which lifts the cap; leaving them unset keeps the default.
DEFAULTS: dict[str, int] = {"workroom_memory": 2 * 1024**3, "workroom_disk": 2 * 1024**3}
DOCKER_MIN_MEMORY = 6 * MB


class LimitError(InstanceError):
    pass


def parse_cpus(text: str | float | None) -> float:
    if text is None or str(text).strip().lower() in ("", "0", "none", "unlimited", "off"):
        return 0.0
    try:
        v = float(str(text))
    except ValueError:
        raise ValueError(f"{text!r} is not a number of CPUs (examples: 0.5, 1, 2.5)") from None
    if v < 0.01:
        raise ValueError("CPUs must be at least 0.01")
    return v


@dataclass(slots=True)
class Capacity:
    """What this computer can give: the real ceiling wherever no limit was set."""
    cpus: int | None
    ram: int | None
    disk_free: int | None


def capacity(m: Instances) -> Capacity:
    cpus, ram = host_capacity(m)
    return Capacity(cpus, ram, m.disk_free(m.root))


def whole(opt: str, cap: Capacity) -> str:
    """How a setting reads when nothing was set: the computer's own capacity, never just "no limit"."""
    kind = OPTIONS[opt][1]
    if opt in ("disk", "workroom_disk"):
        return f"whole disk ({fmt_size(cap.disk_free)} free)" if cap.disk_free is not None else "whole disk"
    if opt == "browser_cache":
        return "browser default"
    if kind == "cpus":
        return f"all {cap.cpus} CPUs" if cap.cpus else "all CPUs"
    return f"whole computer ({fmt_size(cap.ram)})" if cap.ram else "whole computer"


def shown(inst: Instance, opt: str, cap: Capacity) -> str:
    """The setting for this copy as it should be printed: the limit, or else what the computer has."""
    cur = current(inst)[opt]
    return whole(opt, cap) if cur == "no limit" else cur


PROFILE_LABEL = profiles.LABEL


def host_capacity(m: Instances) -> tuple[int | None, int | None]:
    """(CPUs, RAM bytes) that Docker can hand out on this computer."""
    r = m.run(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"], capture_output=True, text=True)
    try:
        cpus, mem = (r.stdout or "").split()[:2]
        return int(cpus), int(mem)
    except (ValueError, TypeError):
        return None, None


def prepare(m: Instances, wanted: dict[str, str | None]) -> tuple[dict[str, str], list[str], list[str]]:
    """Check what was asked for. Returns (settings to write, settings to clear, warnings). Raises LimitError on a value that cannot work."""
    set_, clear, warn = {}, [], []
    host_cpus = host_mem = None
    looked = False
    for opt, raw in wanted.items():
        if raw is None:
            continue
        key, kind, label = OPTIONS[opt]
        try:
            value = parse_cpus(raw) if kind == "cpus" else parse_size(raw)
        except ValueError as e:
            raise LimitError(f"--{opt.replace('_', '-')}: {e}") from None
        if not value:
            if opt in DEFAULTS:
                set_[key] = "0"          # unset would bring the default back
            else:
                clear.append(key)
            continue
        if not looked:
            host_cpus, host_mem = host_capacity(m)
            looked = True
        if kind == "cpus":
            if host_cpus and value > host_cpus:
                raise LimitError(f"--{opt.replace('_', '-')}: this computer's Docker has {host_cpus} CPUs, so {value:g} is too many.")
            set_[key] = f"{value:g}"
        else:
            if opt not in ("disk", "browser_cache", "workroom_disk"):
                if value < DOCKER_MIN_MEMORY:
                    raise LimitError(f"--{opt.replace('_', '-')}: Docker needs at least {fmt_size(DOCKER_MIN_MEMORY)} of RAM.")
                if host_mem and value > host_mem:
                    raise LimitError(f"--{opt.replace('_', '-')}: this computer's Docker has {fmt_size(host_mem)} of RAM, so {fmt_size(value)} is too much.")
            set_[key] = str(max(1, round(value / MB))) if kind == "mb" else str(value)
        low = profiles.FLOOR[opt]
        if value < low:          # still allowed: the person may know better, but they are told what was measured
            warn.append(f"{label} {profiles.option_text(opt, value)} is below the minimum ({profiles.option_text(opt, low)}): "
                        f"{profiles.MEASURED[opt]}.")
    return set_, clear, warn


def save(inst: Instance, set_: dict[str, str], clear: list[str]) -> None:
    if clear:
        remove_env(inst.env, clear)
    if set_:
        write_env(inst.env, set_)


def current(inst: Instance) -> dict[str, str]:
    """What is set for a copy, as people read it. Missing = no limit."""
    out = {}
    for opt, (key, kind, _) in OPTIONS.items():
        raw = read_env(inst.env, key)
        if not raw and opt in DEFAULTS:
            out[opt] = f"{fmt_size(DEFAULTS[opt])} (default)"
        elif not raw or raw == "0":
            out[opt] = "no limit"
        elif kind == "size":
            out[opt] = fmt_size(int(raw))
        elif kind == "mb":
            out[opt] = fmt_size(int(raw) * MB)
        else:
            out[opt] = raw
    return out


def _stats(m: Instances, inst: Instance) -> dict[str, dict[str, Any]]:
    """service -> memory and CPU in use right now, for the containers of one copy that are running."""
    ps = m.run(m.compose(inst, "ps", "--format", "json"), cwd=m.root, capture_output=True, text=True)
    if ps.returncode != 0:
        return {}
    text = (ps.stdout or "").strip()
    try:
        rows = json.loads(text) if text.startswith("[") else [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    except ValueError:
        return {}
    by_id = {str(r.get("ID", "")): str(r.get("Service", "")) for r in rows if isinstance(r, dict) and r.get("ID") and r.get("State") in (None, "running")}
    if not by_id:
        return {}
    r = m.run(["docker", "stats", "--no-stream", "--format", "{{.ID}}|{{.MemUsage}}|{{.CPUPerc}}", *by_id], capture_output=True, text=True)
    out: dict[str, dict[str, Any]] = {}
    for line in (r.stdout or "").splitlines():
        try:
            short, mem, cpu = line.split("|")
            service = next(s for full, s in by_id.items() if full.startswith(short))
            out[service] = {"mem": parse_size(mem.split("/")[0].strip()), "cpu": cpu.strip()}
        except (ValueError, StopIteration):
            continue
    return out


def _disk(inst: Instance, get: Callable[..., Any], cap: Capacity) -> str:
    try:
        r = get(f"{inst.local_url}/api/resources", headers={"Authorization": f"Bearer {read_env(inst.env, 'ASSISTANT_TOKEN')}"}, timeout=5)
        d = r.json()["disk"]
        return f"{fmt_size(d['used'])} used, limit {fmt_size(d['limit']) if d['limit'] else whole('disk', cap)}"
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return "?"


def report(m: Instances, *, only: str | None = None, get: Callable[..., Any] = httpx.get) -> list[str]:
    names = [only or MAIN] if only else ([MAIN] if m.env_path(MAIN).is_file() else []) + m.names()
    cap = capacity(m)
    cpus, mem = cap.cpus, cap.ram
    lines = []
    committed = 0
    unlimited: list[str] = []
    for n in names:
        inst = m.load(n)
        cur, stats = {o: shown(inst, o, cap) for o in OPTIONS}, _stats(m, inst)
        lines.append(f"{n}  size: {size_text(inst)}")
        rows = [("server", "memory", "cpus"), ("browser", "browser_memory", "browser_cpus")]
        if inst.is_main:
            rows += [(s, "cli_memory", "cli_cpus") for s in ("claude-cli", "agy-cli") if s in stats]
        rows.append(("workroom", "workroom_memory", "workroom_cpus"))
        for service, mem_opt, cpu_opt in rows:
            if service == "browser" and not inst.browser:
                continue
            st = stats.get(service)
            now_mem = f"{fmt_size(st['mem'])} now, " if st else ""
            now_cpu = f"{st['cpu']} now, " if st else ""
            lines.append(f"  {service:11} RAM {now_mem}limit {cur[mem_opt]}  |  CPU {now_cpu}limit {cur[cpu_opt]}")
            raw = read_env(inst.env, OPTIONS[mem_opt][0]) or str(DEFAULTS.get(mem_opt, ""))
            if raw and raw != "0":
                committed += int(raw)
            else:
                unlimited.append(f"{n}/{service}")
        extra = (f"  |  browser cache {cur['browser_cache']}" if inst.browser else "") + f"  |  work room scratch {cur['workroom_disk']}"
        # the server is asked for its storage use only if it is running (a stopped copy has nothing to answer, and nothing should be called)
        disk = _disk(inst, get, cap) if stats.get("server") else f"limit {cur['disk']} (not running, usage unknown)"
        lines.append(f"  {'storage':11} {disk}{extra}")
    if cpus and mem:
        disk = f" · {profiles.gb(cap.disk_free)} free disk" if cap.disk_free is not None else ""
        lines.append(f"This computer: RAM {profiles.gb(mem)} · CPU {cpus}{disk}")
        if committed:
            lines.append(f"RAM limits added up: {fmt_size(committed)} of {fmt_size(mem)}.")
        if committed > mem:
            lines.append("! The limits add up to more than this computer has. That is allowed (copies are rarely all busy at once), "
                         "but when they are, Docker may stop a container.")
    if len(names) > 1 and unlimited:
        lines.append(f"! RAM is not capped on: {', '.join(unlimited)}. Each of those can use the computer's whole RAM, "
                     "including what the other copies would need.")
    return lines


def parse_options(args: Any) -> dict[str, str | None]:
    return {opt: getattr(args, opt, None) for opt in OPTIONS}


# ---------------------------------------------------------------------------------------------- the sizes: Recommended, Minimum, No limit
def cli_containers(inst: Instance) -> int:
    """How many Claude / agy containers this copy runs (only the main copy has any)."""
    return len([p for p in read_env(inst.env, "COMPOSE_PROFILES").split(",") if p in ("claude", "agy")]) if inst.is_main else 0


def layout(inst: Instance) -> dict[str, Any]:
    return {"browser": inst.browser, "clis": cli_containers(inst)}


def wanted_for(profile: str, inst: Instance) -> dict[str, str | None]:
    """The limit options a size sets for this copy (options it does not have, such as a browser it lacks, are left alone)."""
    mine = profiles.caps(profile, **layout(inst))
    return {o: profiles.to_argument(o, mine[o]) if o in mine else None for o in OPTIONS}


def _setting(inst: Instance, opt: str) -> float | None:
    raw = read_env(inst.env, OPTIONS[opt][0])
    if not raw or raw == "0":
        return None
    v = float(raw)
    return v * MB if OPTIONS[opt][1] == "mb" else v


def _same(a: float | None, b: float | None) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1)


def _legacy(inst: Instance) -> str | None:
    """'minimum' / 'recommended' when the copy still has the per-container sizes an older version wrote for that size."""
    opts = [o for o in profiles.applicable(**layout(inst)) if o in profiles.LEGACY]
    for name, i in (("minimum", 0), ("recommended", 1)):
        if all(_same(_setting(inst, o), profiles.LEGACY[o][i]) for o in opts):
            return name
    return None


def current_profile(inst: Instance) -> str:
    """'recommended', 'minimum', 'unlimited', or 'custom' (sizes set one by one), judged by the settings that matter for this copy.
    Nothing set counts as No limit (the work room then keeps its own default cap); sizes an older version wrote keep their name."""
    mine = profiles.applicable(**layout(inst))
    have = {o: _setting(inst, o) for o in mine}
    if all(v is None for v in have.values()):
        return "unlimited"
    for name in profiles.TOTALS:
        want = profiles.caps(name, **layout(inst))
        if all(_same(have[o], want[o]) for o in mine):
            return name
    return _legacy(inst) or "custom"


def migrate(m: Instances, inst: Instance) -> str | None:
    """A copy sized by an older version (other per-container numbers) is moved to the same size in today's numbers.
    Returns the size it was moved to, or None when there was nothing to move. Takes effect the next time the copy starts."""
    name = _legacy(inst)
    if not name:
        return None
    set_, clear, _ = prepare(m, wanted_for(name, inst))
    save(inst, set_, clear)
    return name


def migrate_all(m: Instances) -> list[tuple[str, str]]:
    """Every copy sized by an older version, moved to today's numbers: [(copy, size label)]. A copy that cannot be moved
    (say, this computer now has fewer CPUs than the size asks for) is left as it is."""
    moved = []
    for n in [MAIN, *m.names()]:
        if not m.env_path(n).is_file():
            continue
        try:
            name = migrate(m, m.load(n))
        except InstanceError:
            continue
        if name:
            moved.append((n, profiles.LABEL[name]))
    return moved


def size_text(inst: Instance) -> str:
    """'Minimum (RAM 2 GB · CPU 2 · disk 10 GB)', 'No limit', or 'Custom (set one by one)'."""
    name = current_profile(inst)
    if name in profiles.TOTALS:
        return f"{profiles.LABEL[name]} ({profiles.spec(name)})"
    return "No limit" if name == "unlimited" else "Custom (set one by one)"


def committed_ram(m: Instances, exclude: str | None = None) -> int:
    """RAM already promised to the other copies through their limits (copies without a limit promise nothing here)."""
    total = 0
    for n in [MAIN, *m.names()]:
        if n == (exclude or MAIN) or not m.env_path(n).is_file():
            continue
        inst = m.load(n)
        for opt in ("memory", "browser_memory", "workroom_memory"):
            total += int(_setting(inst, opt) or 0)
        total += int(_setting(inst, "cli_memory") or 0) * cli_containers(inst)
    return total


def choose(m: Instances, inst: Instance, *, ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> dict[str, str | None]:
    """Ask how much this copy may use: Recommended, Minimum (the site's numbers, for all its containers together) or No limit."""
    from .wizard import (  # imported here: wizard imports this module
        Machine,
        shortfalls,
        suggested_tier,
        tier,
    )
    cap = capacity(m)
    here = Machine(cap.cpus, cap.ram, cap.disk_free)
    gb = profiles.gb
    computer = f"RAM {gb(cap.ram)} · CPU {cap.cpus or '?'} · {gb(cap.disk_free)} free"
    out("How much may this Kelo use?")
    rows = [("1", "recommended", profiles.spec("recommended")), ("2", "minimum", profiles.spec("minimum")),
            ("3", "unlimited", f"everything this computer has ({computer})")]
    for key, name, text in rows:
        out(f"  {key}) {profiles.LABEL[name]:13} {text}")
    out(f"This computer: {computer} disk")
    default = "1" if suggested_tier(here) == "recommended" else "2"
    while True:
        pick = (ask(pick_prompt(["1", "2", "3"], default)) or default).strip()
        if pick in ("1", "2", "3"):
            break
        out("Please type 1, 2 or 3.")
    name = rows[int(pick) - 1][1]
    if name in profiles.TOTALS:
        short = shortfalls(tier(name), here)
        if short:
            out(f"Note: this computer has less than {profiles.LABEL[name]} asks for ("
                + ", ".join(f"{what} {have} of {need}" for what, have, need in short) + "). Kelo still runs, but may be slow under heavy use.")
        taken = committed_ram(m, exclude=inst.name)
        if taken and cap.ram and taken + profiles.TOTALS[name][0] > cap.ram:
            out(f"Note: other copies may already use {gb(taken)} of RAM; with this one that is more than this computer has. "
                "Fine unless they are all busy at once.")
    return wanted_for(name, inst)
