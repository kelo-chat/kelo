"""Hygiene for docs/no-word-lists.md, phase A: the modules that used to decide payment or the owner's consent by words no longer do.
A word list that comes back under one of the old names (or the old patterns) fails here."""

from __future__ import annotations

import re
from pathlib import Path

from assistant.tools import browser, commits

SRC = Path(commits.__file__).parent

REMOVED_NAMES = ("money_words", "money_page", "order_step", "looks_final", "_FINAL_WORDS", "_EN_PAY", "_KO_PAY", "_EN_MONEY", "_KO_MONEY",
                 "_EN_FINAL", "_KO_FINAL", "_EN_STEP", "_KO_STEP", "_MONEY_PAGE", "_PAY_HREF", "_PAY_PATH", "_ABOUT_A_PURCHASE", "PAYWORDS",
                 "cartPage", "cart_page")
# patterns that read a yes / no of the owner from words
CONSENT = (re.compile(r"\(yes\|clear\|retry|\\b\(no\|stop\|cancel", re.I), re.compile(r"지워\||그래\||좋아\||그만\|"), re.compile(r"user_said"))


def test_the_money_word_paths_are_gone_from_commits_and_browser():
    for path in (SRC / "commits.py", SRC / "browser.py"):
        text = path.read_text(encoding="utf-8")
        for name in REMOVED_NAMES:
            assert name not in text, f"{path.name} still has {name}"
        for pattern in CONSENT:
            assert not pattern.search(text), f"{path.name} reads consent from words: {pattern.pattern}"


def test_a_button_word_never_makes_a_commit_kind_for_paying():
    for name in ("judge_words", "judge_click"):
        assert not hasattr(commits, name), name   # no word of a button is judged any more (docs/click-judge.md)
    assert not hasattr(browser, "looks_final")
