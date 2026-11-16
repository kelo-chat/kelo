"""`kelo nest` (nest_cli.py): a package maker makes, checks and packs a package folder. Nothing here touches the network."""

from __future__ import annotations

import shutil
from hashlib import sha256
from pathlib import Path

from assistant.nest_cli import main, pack_bytes
from assistant.packages import load_package


ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "server" / "examples" / "packages"


def test_init_validate_and_pack_every_kind(tmp_path, capsys):
    for kind in ("skill", "connector", "extension"):
        folder = tmp_path / f"new-{kind}" if kind != "connector" else tmp_path / "new_connector"
        assert main(["init", str(folder), "--kind", kind]) == 0
        assert main(["validate", str(folder)]) == 0
        assert load_package(folder).kind == kind
    out = capsys.readouterr().out
    assert "Runs no code on your server or your phone" in out
    assert main(["init", str(tmp_path / "new-skill")]) == 1          # not empty
    assert main(["pack", str(EXAMPLES / "habit-tracker"), "--out", str(tmp_path / "out")]) == 0
    a = (tmp_path / "out" / "habit-tracker-1.0.0.tar.gz").read_bytes()
    assert a == pack_bytes(EXAMPLES / "habit-tracker")                # reproducible
    assert f"sha256: {sha256(a).hexdigest()}" in capsys.readouterr().out
    bad = tmp_path / "bad-skill"
    shutil.copytree(EXAMPLES / "trip-packing-list", bad)
    (bad / "run.sh").write_text("#!/bin/sh\necho hi\n")
    assert main(["pack", str(bad), "--out", str(tmp_path / "out")]) == 1   # a program never goes in a package


def test_assistant_nest_runs_the_same_commands(tmp_path):
    from assistant.cli import main as assistant_main
    assert assistant_main(["nest", "validate", str(EXAMPLES / "daily-countdown")]) == 0
