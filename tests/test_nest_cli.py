"""`kelo nest` (nest_cli.py): a publisher makes, checks, packs and publishes a package (the pull request's file, no network), and the
operator checks the registry, makes a key, builds, signs and verifies the index that servers read."""

from __future__ import annotations

import json
import shutil
import time
from hashlib import sha256
from pathlib import Path

from assistant.nest_cli import main, pack_bytes
from assistant.nest_index import load, parse
from assistant.nest_sign import read_public_key
from assistant.packages import load_package

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "packages"


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


def test_publish_check_build_sign_and_verify(tmp_path, capsys):
    reg = tmp_path / "registry"
    (reg / "packages").mkdir(parents=True)
    (reg / "publishers.json").write_text(json.dumps({"acme": {"name": "Acme", "verified": True}}))
    (reg / "verified.json").write_text(json.dumps(["habit-tracker"]))
    (reg / "withdrawn.json").write_text(json.dumps([{"id": "old-thing", "versions": "*", "reason": "Gone."}]))
    commit = "a" * 40
    assert main(["publish", str(EXAMPLES / "habit-tracker"), "--registry", str(reg), "--publisher", "acme",
                 "--git-url", "https://github.com/acme/habit-tracker", "--commit", commit]) == 0
    assert "git checkout -b habit-tracker-1.0.0" in capsys.readouterr().out
    rec = json.loads((reg / "packages" / "habit-tracker" / "1.0.0.json").read_text())
    assert rec["source"] == {"type": "git", "url": "https://github.com/acme/habit-tracker", "commit": commit} and rec["kinds"] == ["skill", "extension"]
    assert rec["name"]["ko"] == "습관 기록" and rec["permissions"]["store"] is True
    assert main(["publish", str(EXAMPLES / "habit-tracker"), "--registry", str(reg), "--publisher", "acme",
                 "--git-url", "https://github.com/acme/habit-tracker", "--commit", commit]) == 1     # a published version never changes
    assert main(["publish", str(EXAMPLES / "habit-tracker"), "--registry", str(reg), "--publisher", "acme",
                 "--git-url", "https://github.com/acme/x", "--commit", "v1.0"]) == 1
    # an archive source: the archive must hold the same files
    archive = tmp_path / "parcels.tar.gz"
    archive.write_bytes(pack_bytes(EXAMPLES / "example_parcels"))
    assert main(["publish", str(EXAMPLES / "example_parcels"), "--registry", str(reg), "--publisher", "acme",
                 "--archive", str(archive), "--archive-url", "https://dl.acme.example/parcels-1.0.0.tar.gz"]) == 0
    assert main(["publish", str(EXAMPLES / "example_weather"), "--registry", str(reg), "--publisher", "acme",
                 "--archive", str(archive), "--archive-url", "https://dl.acme.example/w.tar.gz"]) == 1
    assert main(["check-registry", str(reg)]) == 0
    # someone else's version of a package refuses the registry
    (reg / "publishers.json").write_text(json.dumps({"acme": {"name": "Acme"}, "mallory": {"name": "M"}}))
    other = json.loads((reg / "packages" / "habit-tracker" / "1.0.0.json").read_text())
    (reg / "packages" / "habit-tracker" / "1.0.1.json").write_text(json.dumps({**other, "version": "1.0.1", "publisher": "mallory"}))
    assert main(["check-registry", str(reg)]) == 1
    (reg / "packages" / "habit-tracker" / "1.0.1.json").unlink()
    # the operator's key, index and signature
    assert main(["keygen", "--out", str(tmp_path / "keys")]) == 0
    assert (tmp_path / "keys" / "nest.key").stat().st_mode & 0o077 == 0
    assert main(["keygen", "--out", str(tmp_path / "keys")]) == 1   # never replaced by accident
    index = tmp_path / "site" / "index.json"
    index.parent.mkdir()
    assert main(["index", "build", "--registry", str(reg), "--out", str(index), "--sequence", "7"]) == 0
    assert main(["index", "sign", "--key", str(tmp_path / "keys" / "nest.key"), str(index)]) == 0
    assert main(["index", "verify", "--pub", str(tmp_path / "keys" / "nest.pub"), str(index)]) == 0
    idx = load(index.read_bytes(), (tmp_path / "site" / "index.json.minisig").read_text(),
               [read_public_key((tmp_path / "keys" / "nest.pub").read_text())], now=time.time())
    assert idx.sequence == 7 and set(idx.entries) == {"habit-tracker", "example_parcels"} and idx.entries["habit-tracker"].verified
    assert idx.withdrawn["old-thing"]["versions"] == "*" and idx.entries["example_parcels"].latest.connect == "key"
    index.write_text(index.read_text().replace("Habit tracker", "Habit trackers"))
    assert main(["index", "verify", "--pub", str(tmp_path / "keys" / "nest.pub"), str(index)]) == 1
    assert parse(index.read_bytes()).sequence == 7


def test_assistant_nest_runs_the_same_commands(tmp_path):
    from assistant.cli import main as assistant_main
    assert assistant_main(["nest", "validate", str(EXAMPLES / "daily-countdown")]) == 0
