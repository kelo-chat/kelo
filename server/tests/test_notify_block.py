"""notify_block.py: the owner's don't-tell rules read by the main model (docs/no-word-lists.md, hc-rest)."""

from __future__ import annotations

from notify_fake import FakeRules

from assistant.home import NOTIFY_PREFS, OLD_SIDE_TEMPLATES, SIDE_TEMPLATES
from assistant.notify_block import has_rules, parse

RULES = "# NOTIFY_PREFS.md\n\n## Do not tell me\n- sports scores\n"


def test_only_a_written_rule_counts_not_headings_or_the_template():
    assert not has_rules("") and not has_rules(SIDE_TEMPLATES[NOTIFY_PREFS]) and not has_rules(OLD_SIDE_TEMPLATES[NOTIFY_PREFS][0])
    assert has_rules(RULES) and has_rules("# NOTIFY_PREFS.md\n\nNo news about football, please.\n")


def test_the_answer_must_be_one_bool_per_item():
    assert parse({"blocked": [True, False]}, 2) == [True, False]
    for bad in (None, {}, {"blocked": [True]}, {"blocked": [True, "no"]}, {"blocked": "yes"}, {"blocked": [1, 0]}, []):
        assert parse(bad, 2) is None


async def test_one_batched_request_unknown_answers_and_the_cache():
    r = FakeRules("sports")
    assert await r.block.check(RULES, ["Sports scores today", "Parcel arrives", "Rain tomorrow"]) == [True, False, False]
    assert len(r.calls) == 1 and [x["n"] for x in r.calls[0]["items"]] == [1, 2, 3]
    assert await r.block.check(RULES, ["Parcel arrives", "A new item"]) == [False, False] and len(r.calls) == 2   # only the new item is asked
    assert [x["text"] for x in r.calls[1]["items"]] == ["A new item"]
    assert await r.block.check("", ["x"]) == [False] and len(r.calls) == 2                                      # no rules: no request
    down = FakeRules("sports", down=True)
    assert await down.block.check(RULES, ["Sports scores"]) == [None]                                           # unknown, never "allowed"
