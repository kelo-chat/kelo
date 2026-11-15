"""NOTICE names the third-party files the repository carries, with the version that is really there."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_public_suffix_list_is_in_notice_with_its_version_and_licence():
    psl = (ROOT / "src" / "assistant" / "tools" / "public_suffix_list.dat").read_text(encoding="utf-8")
    version = re.search(r"^// VERSION: (\S+)", psl, re.M).group(1)
    assert "Mozilla Public" in psl[:300]
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    entry = notice.split("src/assistant/tools/public_suffix_list.dat", 1)[1][:400]
    assert version in entry and "Mozilla Public License 2.0" in entry
