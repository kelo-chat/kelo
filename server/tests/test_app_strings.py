"""The apps' string resources pass the Android string check (apps/android/tools/strings_check.py) in every language.

2026-11-16: a French string with an unescaped apostrophe ("Ouvrir l'écran du téléphone") reached main and the Play bundle failed at
aapt. The check already knew the rule but no test ran it; the server suite (the gate before every merge) runs it now."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "apps" / "android" / "tools" / "strings_check.py"


def test_every_app_string_file_passes_the_android_string_check():
    got = subprocess.run([sys.executable, str(CHECK)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, (got.stdout + got.stderr)[-3000:]
