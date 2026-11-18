"""The VNC page (noVNC + Kelo's view.html/view.js/zoom.js) lives in apps/android/.../assets/vnc; other apps carry copies made by a tool.
A copy that drifted made the iPhone's VncAssets test fail on main (2026-11-17)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COPIES = [  # (tool, copy folder) - another app's copy is added here
    ("apps/ios/tools/sync_vnc.py", "apps/ios/KeloUI/Sources/KeloWork/Resources/vnc"),
]


@pytest.mark.parametrize("tool,folder", COPIES)
def test_every_copy_of_the_vnc_page_is_the_android_original(tool, folder):
    got = subprocess.run([sys.executable, str(ROOT / tool), "--check"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, f"{folder} differs from the Android original; run `python3 {tool}`:\n" + (got.stdout + got.stderr)
    assert (ROOT / folder / "zoom.js").is_file()


def test_the_zoom_rules_hold_in_the_iphone_copy_too():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    zoom = ROOT / "apps/ios/KeloUI/Sources/KeloWork/Resources/vnc/zoom.js"
    done = subprocess.run([node, str(Path(__file__).parent / "js" / "zoom_test.mjs"), str(zoom)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "zoom ok" in done.stdout, done.stdout + done.stderr
