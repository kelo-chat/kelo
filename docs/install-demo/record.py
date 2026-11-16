"""Render the guided `assistant setup` into docs/install-demo/ (SVG screenshots + plain text), with no Docker and no network.

    uv run python docs/install-demo/record.py

Docker, the Kelo address service and the running server are stood in by fakes, and the answers are scripted, so this never
touches a real stack.
"""

from __future__ import annotations

import io
import sys
import tempfile
import types
from pathlib import Path

from rich.console import Console

from assistant import wizard

HERE = Path(__file__).resolve().parent
GB = 1024**3
BUILD_LOG = ["#1 [server internal] load build definition from docker/server.Dockerfile", "#7 [server 4/9] RUN uv sync --frozen --no-dev",
             "#12 [workroom 3/6] RUN apt-get install -y libreoffice-writer", "#15 exporting to image", "#15 naming to docker.io/library/kelo:latest done",
             " Network assistant-server_agent  Created", " Container assistant-server-browser-1  Started",
             " Container assistant-server-server-1  Started"]


def fake_run(cmd, **kw):
    out = types.SimpleNamespace(returncode=0, stdout="", stderr="")
    if cmd[:2] == ["docker", "version"]:
        out.stdout = "28.4.0"
    elif cmd[:3] == ["docker", "compose", "version"]:
        out.stdout = "2.39.2"
    elif cmd[:2] == ["docker", "info"]:
        out.stdout = f"8 {int(7.7 * GB)}"
    return out


def fake_stream(cmd, cwd, on_line):
    for line in BUILD_LOG:
        on_line(line)
    return 0


def fake_post(url, json=None, **kw):
    if url.endswith("/broker/tunnel/claim"):
        return types.SimpleNamespace(status_code=200, json=lambda: {"hostname": f"{json['name']}.kelo.chat", "tunnel_token": "x" * 40})
    return types.SimpleNamespace(status_code=200, json=lambda: {"link": "https://mina.kelo.chat/pair#k7Qe2xVb9tLmR4sP"})


def record(lang: str, answers: list[str]) -> None:
    console = Console(file=io.StringIO(), record=True, width=100, color_system="truecolor", force_terminal=False, highlight=False)
    feed = iter(answers)

    def typed(prompt: str) -> str:
        value = next(feed)
        console.print(value, style="bold")          # show what was typed, as a terminal would
        return value

    ui = wizard.RichUI(lang, console=console, input_fn=typed)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "compose.yml").write_text("services: {}\n")
        w = wizard.Wizard(Path(d) / ".env", ui, run=fake_run, stream=fake_stream, get=lambda *a, **k: types.SimpleNamespace(status_code=200),
                          post=fake_post, which=lambda n: "/usr/local/bin/docker", system="Darwin",
                          free_disk=lambda p: 182 * GB, sleep=lambda s: None)
        import os
        os.environ["USER"] = "mina"
        os.environ["KELO_CMD"] = "kelo"
        code = w()
    title = "kelo setup" if lang == "en" else "kelo setup (한국어)"
    (HERE / f"wizard-{lang}.txt").write_text(console.export_text(clear=False), encoding="utf-8")
    console.save_svg(str(HERE / f"wizard-{lang}.svg"), title=title)
    print(f"wizard-{lang}: exit {code}", file=sys.stderr)


if __name__ == "__main__":
    #            size  address  name    phone
    record("en", ["", "1", "mina", ""])
    record("ko", ["", "1", "mina", ""])
