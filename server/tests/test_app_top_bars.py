"""App layout rule (owner 2026-11-17: the connector page's icon was cut under the top bar).

A screen that measures its floating top bar (`onSizeChanged { barH = ... }`) and pushes its content down by `barH` must measure the bar
WITH the status bar: `.onSizeChanged` before `.statusBarsPadding()` in the same modifier chain. Measured after the padding, `barH` lacks
the status bar's height and the first row of the page slides under the bar.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "apps"
CHAIN = re.compile(r"\.statusBarsPadding\(\)(?:\s*\n\s*\.padding\([^)]*\))*\s*\n\s*\.onSizeChanged\s*\{\s*barH\b")


def kotlin_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.kt") if "/build/" not in str(p)]


def test_top_bars_are_measured_with_the_status_bar():
    bad = [str(p.relative_to(ROOT)) for p in kotlin_files() if CHAIN.search(p.read_text(encoding="utf-8"))]
    assert not bad, f"measure the bar before .statusBarsPadding(): {bad}"


def test_the_rule_sees_the_wrong_order():
    assert CHAIN.search("            .statusBarsPadding()\n            .onSizeChanged { barH = with(density) { it.height.toDp() } }")
    assert CHAIN.search("            .statusBarsPadding()\n            .padding(top = below)\n            .onSizeChanged { barH = with(density) { it.height.toDp() } }")
    assert not CHAIN.search("            .onSizeChanged { barH = with(density) { it.height.toDp() } }\n            .statusBarsPadding()")
