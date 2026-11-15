"""One way to write a model's name: the server's model_label() and the apps' ModelNames.humanize() give the same names for the
cases in tests/fixtures/model_labels.json, and the CLI-given labels are written that way too."""

import json
from pathlib import Path

from assistant.models import label_all, model_label
from assistant.providers.cli_manage import CLAUDE_MODELS

CASES = json.loads((Path(__file__).parent / "fixtures" / "model_labels.json").read_text())["cases"]


def test_every_shared_case():
    assert [model_label(i) for i, _ in CASES] == [want for _, want in CASES]


def test_the_claude_cli_labels_follow_the_rule():
    assert {m: model_label(m) for m in CLAUDE_MODELS} == CLAUDE_MODELS


def test_agy_labels_with_a_thinking_level_follow_the_rule():
    # what `agy models` prints (id<TAB>name); claude-sonnet-4-6 is listed as "(Thinking)" with no word for it in its id
    for mid, name in (("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"), ("gemini-3.8-flash-medium", "Gemini 3.8 Flash (Medium)"),
                      ("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)"), ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)"),
                      ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)")):
        assert model_label(mid) == name


def test_label_all_keeps_the_providers_names_and_fills_the_rest():
    got = label_all(["claude-sonnet-4-6", "gemini-3.8-flash-medium", "x/y", "z"],
                    {"claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)", "x/y": "x/y", "z": "  "})
    assert got == {"claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)", "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)",
                   "x/y": "Y", "z": "Z"}
    assert model_label("") == "" and model_label("   ") == ""
