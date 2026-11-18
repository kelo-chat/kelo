"""The iPhone app's generated files match their sources, so apps/ios/ in the source is what the store build compiles.

2026-11-17: an iPhone TestFlight build regenerated 22 tracked files that were stale on main: KeloVersion.swift still said 0.10.2
(version.json: 0.10.4; tools/release/bump.py did not write it) and the KeloShared Localizable.strings/R.swift lacked four strings
added to apps/shared/strings for Android. The build was what people got, the source on main was not (docs/verify-builds.md). Both
generators had a --check mode but nothing ran it; the server suite (the gate before every merge) runs it now."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "apps" / "ios" / "tools"


@pytest.mark.parametrize("script, fix", [
    ("gen_version.py", "python3 apps/ios/tools/gen_version.py"),
    ("gen_strings.py", "python3 apps/ios/tools/gen_strings.py"),
])
def test_the_committed_iphone_files_are_what_the_generator_writes(script, fix):
    got = subprocess.run([sys.executable, str(TOOLS / script), "--check"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, f"run `{fix}` and commit the result:\n" + (got.stdout + got.stderr)[-3000:]
