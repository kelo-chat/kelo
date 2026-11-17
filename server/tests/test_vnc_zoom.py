"""The phone's pinch-to-zoom on the live VNC view (owner decision 2026-11-17: the screen keeps the desktop width, the phone only zooms).
The math and the gesture rules live in apps/android/.../assets/vnc/zoom.js; server/tests/js/zoom_test.mjs runs them with node."""

import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "apps" / "android" / "kelo" / "app" / "src" / "main" / "assets" / "vnc"


def test_the_zoom_math_and_gesture_rules():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    done = subprocess.run([node, str(Path(__file__).parent / "js" / "zoom_test.mjs"), str(ASSETS / "zoom.js")],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "zoom ok" in done.stdout, done.stdout + done.stderr


def test_the_page_uses_the_zoom_and_keeps_noVNC_fitting_the_whole_screen():
    page = (ASSETS / "view.js").read_text()
    assert "from './zoom.js'" in page and "ZoomGesture" in page
    assert "rfb.scaleViewport = true" in page      # at zoom 1 the whole X screen is scaled down to fit
    assert "inset:0" not in page                   # the box noVNC draws into is placed by the zoom now
    assert "touch-action: none" in (ASSETS / "view.html").read_text()
