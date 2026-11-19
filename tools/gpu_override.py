"""Pass a real GPU render node into Kelo's browser containers (Linux hosts with one), or leave them as they are.

Run by scripts/install.sh before `docker compose up` with the Kelo folder as the argument. When the host has a DRM render
node (/dev/dri/renderD*), it writes compose.override.yml (Docker Compose merges it by itself) which gives the `browser` and
`work-browser` services that device and its group, so Chrome can use the host's own driver: the GPU name and the rendering
are then the real ones. No render node: no file. An existing file that is not ours is never touched. Nothing is faked.

Standard library only (the host may have nothing else).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

OVERRIDE_NAME = "compose.override.yml"
MARKER = "# written by tools/gpu_override.py (a real GPU render node was found); removed when the node is gone\n"
SERVICES = ("browser", "work-browser")
_RENDER = re.compile(r"^renderD\d+$")


def render_nodes(dev_dir: str = "/dev/dri") -> list[str]:
    """The render nodes under dev_dir, sorted (renderD128 first). Empty when there is none or the folder is absent."""
    try:
        names = os.listdir(dev_dir)
    except OSError:
        return []
    return sorted((n for n in names if _RENDER.match(n)), key=lambda n: int(n[len("renderD"):]))


def override_text(gid: int) -> str:
    """The compose override: the DRI folder and the render group for the two browser services (nothing else changes)."""
    lines = [MARKER, "services:\n"]
    for service in SERVICES:
        lines += [
            f"  {service}:\n",
            "    devices:\n",
            '      - "/dev/dri:/dev/dri"\n',
            "    group_add:\n",
            f'      - "{gid}"\n',
        ]
    return "".join(lines)


def apply(project: Path, dev_dir: str = "/dev/dri", gid_of=lambda path: os.stat(path).st_gid) -> str:
    """Write, refresh or remove the override for this host. Returns "written", "unchanged", "removed" (no node and the file is
    ours), "kept" (no node, no file), or "skipped" (a file that is not ours is never touched)."""
    target = project / OVERRIDE_NAME
    nodes = render_nodes(dev_dir)
    ours = target.is_file() and target.read_text(errors="replace").startswith(MARKER)
    if not nodes:
        if ours:
            target.unlink()
            return "removed"
        return "kept"
    if target.exists() and not ours:
        return "skipped"
    gid = gid_of(os.path.join(dev_dir, nodes[0]))
    text = override_text(gid)
    if ours and target.read_text() == text:
        return "unchanged"
    target.write_text(text)
    return "written"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gpu_override.py <kelo-folder>", file=sys.stderr)
        return 64
    print(apply(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
