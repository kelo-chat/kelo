"""How much one Kelo may use: Minimum, Recommended (the numbers on the Kelo site) or No limit, and how that is split between its containers.

Choosing a size means everything this Kelo runs, together, stays within it:
- Minimum      RAM 2 GB · CPU 2 · disk 10 GB
- Recommended  RAM 4 GB · CPU 4 · disk 20 GB
The disk number includes the program files (Docker images); what is left after them is the storage the copy may fill.

Each container gets a share of the total, weighted by what was measured (see MEASURED): the browser needs the most RAM and CPU,
the server and the work room about half of that each, and each Claude / agy container a quarter. The small tunnel / proxy
container a copy may run is not capped (a few MB).
"""

from __future__ import annotations

import math

MB = 1024**2
GB = 1024**3

# size -> (RAM bytes, CPUs, disk bytes incl. program files). The same numbers as the Kelo site, the README and the setup.
TOTALS: dict[str, tuple[int, float, int]] = {
    "minimum": (2 * GB, 2.0, 10 * GB),
    "recommended": (4 * GB, 4.0, 20 * GB),
}
PROFILES = ("recommended", "minimum", "unlimited")      # the menu order
LABEL = {"recommended": "Recommended", "minimum": "Minimum", "unlimited": "No limit", "custom": "Custom"}

# program files on disk: the server, browser and work room images, and each Claude / agy image (measured 1.1-1.5 GB; the upper end)
IMAGES = int(2.5 * GB)
CLI_IMAGE = int(1.5 * GB)

# share of RAM and CPU per container (measured peaks: browser ~715 MB, server ~490 MB, work room ~440 MB, agy ~184 MB)
WEIGHT = {"server": 1.0, "browser": 2.0, "workroom": 1.0, "cli": 0.5}
# what is left of the disk after the program files: the browser's page cache 5% (at most 512 MB), the work room's scratch space 25%,
# and the rest for what the assistant stores (chats, uploads, images, notes)
CACHE_SHARE, CACHE_MAX, WORKROOM_SHARE = 0.05, 512 * MB, 0.25

GROUPS = {"server": ("memory", "cpus", "disk"), "browser": ("browser_memory", "browser_cpus", "browser_cache"),
          "workroom": ("workroom_memory", "workroom_cpus", "workroom_disk"), "cli": ("cli_memory", "cli_cpus")}

# The least each container ran its heaviest ordinary work with (below it a single setting gets a warning; still allowed).
FLOOR: dict[str, float] = {
    "memory": 256 * MB, "cpus": 0.25, "disk": 256 * MB,
    "browser_memory": 768 * MB, "browser_cpus": 0.5, "browser_cache": 50 * MB,
    "cli_memory": 192 * MB, "cli_cpus": 0.1,
    "workroom_memory": 384 * MB, "workroom_cpus": 0.25, "workroom_disk": 256 * MB,
}

# The per-container sizes older versions wrote (option -> (minimum, recommended)). Only used to recognise a copy sized back then,
# so it can be moved to the same size in today's numbers.
LEGACY: dict[str, tuple[float, float]] = {
    "memory": (256 * MB, 1 * GB), "cpus": (0.5, 1.0), "disk": (512 * MB, 5 * GB),
    "browser_memory": (768 * MB, 1536 * MB), "browser_cpus": (1.0, 2.0), "browser_cache": (100 * MB, 500 * MB),
    "cli_memory": (512 * MB, 1 * GB), "cli_cpus": (0.5, 1.0),
}

# What was measured (4 CPUs, Docker Desktop on a Mac, 2026-11), and what each number rests on.
MEASURED = {
    "memory": "server idle 46 MB; a 300-page PDF 82 MB, a long Word file 99 MB, a 600,000-cell spreadsheet 180 MB; driving the browser 170 MB (peak); "
              "three heavy jobs at once need about 490 MB; an upload is written to disk as it arrives, so even a 100 MB video adds "
              "only a few MB while it streams in",
    "cpus": "the 600,000-cell spreadsheet: 3.3 s at 1 CPU, 7.9 s at 0.5 CPU, 64 s at 0.25 CPU",
    "browser_memory": "idle 264 MB; three phones on four heavy sites about 715 MB (about 150 MB per open tab); at 512 MB pages were killed, "
                      "and a 768 MB browser starts losing pages when several phones browse at once",
    "browser_cpus": "four heavy sites, slowest page: 1.7 s at 2 CPUs, 2.0 s at 1 CPU, 13 s at 0.5 CPU",
    "cli_memory": "agy: 52 MB idle, 184 MB peak during a short answer (Claude's CLI was not measured; it is assumed similar)",
    "disk": "an empty assistant is about 0.1 MB; an upload has no size limit of its own (big files go up in 90 MB parts, so Cloudflare's 100 MB request limit does not matter; a photo is at most 15 MB, a document usually "
            "under 25 MB) and is refused when it would pass this cap or the disk's free space; a generated image is about 1-3 MB; "
            "previews of photos and videos about 20-200 KB each",
    "browser_cache": "the browser's own page cache; only grows with browsing",
    "cli_cpus": "agy: at most about a quarter of a CPU while answering",
    "workroom_memory": "work room idle 20 MB; a 2-million-cell spreadsheet written with pandas, then a 300-paragraph Word file turned into a PDF by LibreOffice: about 440 MB at the peak",
    "workroom_cpus": "that job used about one CPU (pandas and LibreOffice work on one core); 11.5 s for the spreadsheet, 1.5 s for the PDF",
    "workroom_disk": "the image's own programs are not counted; a job's input files, its outputs and LibreOffice's profile are",
}


def applicable(*, browser: bool = True, clis: int = 0) -> list[str]:
    """The options that matter for a copy: its server and work room, its browser if it has one, the CLI containers if it has any."""
    out = list(GROUPS["server"])
    if browser:
        out += GROUPS["browser"]
    out += GROUPS["workroom"]
    if clis:
        out += GROUPS["cli"]
    return out


def image_size(clis: int = 0) -> int:
    return IMAGES + clis * CLI_IMAGE


def caps(profile: str, *, browser: bool = True, clis: int = 0) -> dict[str, float | None]:
    """option -> cap for every container of a copy in a size (None = no limit). The caps add up to the size's totals:
    RAM and CPUs over all containers (each Claude / agy container counts once), disk = program files + the storage caps."""
    opts = applicable(browser=browser, clis=clis)
    if profile == "unlimited":
        return dict.fromkeys(opts)
    ram, cpus, disk = TOTALS[profile]
    parts = {"browser": WEIGHT["browser"] if browser else 0.0, "workroom": WEIGHT["workroom"], "cli": WEIGHT["cli"] * clis}
    whole = WEIGHT["server"] + sum(parts.values())

    def split(total: float, unit: float) -> dict[str, float]:
        """Each share rounded down to `unit`; the server takes what is left, so the shares add up exactly."""
        got = {k: math.floor(total * w / whole / unit) * unit for k, w in parts.items()}
        got["cli"] = math.floor(total * WEIGHT["cli"] / whole / unit) * unit if clis else 0.0
        got["server"] = total - got["browser"] * bool(browser) - got["workroom"] - got["cli"] * clis
        return got

    r, c = split(ram, MB), split(cpus, 0.01)
    data = disk - image_size(clis)
    cache = min(math.floor(data * CACHE_SHARE / MB) * MB, CACHE_MAX) if browser else 0
    work = math.floor(data * WORKROOM_SHARE / MB) * MB
    out: dict[str, float | None] = {
        "memory": r["server"], "cpus": round(c["server"], 2), "disk": data - cache - work,
        "browser_memory": r["browser"], "browser_cpus": round(c["browser"], 2), "browser_cache": cache,
        "workroom_memory": r["workroom"], "workroom_cpus": round(c["workroom"], 2), "workroom_disk": work,
        "cli_memory": r["cli"], "cli_cpus": round(c["cli"], 2),
    }
    return {o: out[o] for o in opts}


def value(option: str, profile: str, *, browser: bool = True, clis: int = 0) -> float | None:
    """The cap for one option in a size; None means no limit."""
    return caps(profile, browser=browser, clis=clis).get(option)


def totals(profile: str, *, browser: bool = True, clis: int = 0) -> dict[str, float]:
    """RAM, CPUs and disk (incl. program files) the caps of a size add up to; zeros for No limit."""
    if profile == "unlimited":
        return {"ram": 0.0, "cpus": 0.0, "disk": 0.0}
    c = caps(profile, browser=browser, clis=clis)
    mult = lambda o: clis if o.startswith("cli_") else 1  # every Claude / agy container gets the same cap
    return {"ram": float(sum(v * mult(o) for o, v in c.items() if o.endswith("memory"))),
            "cpus": round(sum(v * mult(o) for o, v in c.items() if o.endswith("cpus")), 2),
            "disk": float(image_size(clis) + sum(v for o, v in c.items() if o in ("disk", "browser_cache", "workroom_disk")))}


def gb(n: float | None) -> str:
    """'4 GB', '11.7 GB', '0.5 GB'."""
    if n is None:
        return "?"
    return f"{n / GB:.1f}".removesuffix(".0") + " GB"


def spec(profile: str) -> str:
    """'RAM 4 GB · CPU 4 · disk 20 GB': the whole size, the way the site shows it."""
    ram, cpus, disk = TOTALS[profile]
    return f"RAM {gb(ram)} · CPU {cpus:g} · disk {gb(disk)}"


def option_text(option: str, size: float | None) -> str:
    """'1.5GB' / '2' (CPUs) / 'no limit', the way people read it."""
    from .quota import fmt_size
    if size is None:
        return "no limit"
    return f"{size:g}" if option.endswith("cpus") else fmt_size(int(size))


def to_argument(option: str, size: float | None) -> str:
    """The same value as the command-line option takes it: '1536m', '2', or 'none'."""
    if size is None:
        return "none"
    if option.endswith("cpus"):
        return f"{size:g}"
    return f"{int(size) // MB}m"


def _close(a: float | None, b: float | None) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= max(1.0, 0.001 * b))


def guess(memory: float | None, cpus: float | None, disk: float | None) -> str:
    """Which size the server container's own limits match: 'recommended', 'minimum', 'unlimited' or 'custom'.
    The server cannot see whether the copy has a browser or Claude / agy containers, so every likely layout is tried."""
    if memory is None and cpus is None and disk is None:
        return "unlimited"
    for name in TOTALS:
        for browser in (True, False):
            for clis in (0, 1, 2):
                c = caps(name, browser=browser, clis=clis)
                if _close(memory, c["memory"]) and _close(cpus, c["cpus"]) and _close(disk, c["disk"]):
                    return name
        old = {o: LEGACY[o][0 if name == "minimum" else 1] for o in GROUPS["server"]}
        if _close(memory, old["memory"]) and _close(cpus, old["cpus"]) and _close(disk, old["disk"]):
            return name
    return "custom"
