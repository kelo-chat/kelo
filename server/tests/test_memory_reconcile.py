"""Tests for memory pipeline part 2: reconcile (steps 6-13, docs/memory-pipeline.md)."""

import json
from pathlib import Path
import pytest

from assistant.home import Home
from assistant.memory_reconcile import (
    RefusedError,
    apply,
    build_preservation_second_request,
    build_reconcile_request,
    build_review_request,
    choose_targets,
    ground,
    parse_preservation_second_response,
    parse_reconcile_response,
    parse_review_response,
    pin,
    prune_and_restage,
    reconcile_file,
    stage,
    undo,
)
from assistant.memory_types import (
    Claim,
    Edit,
    LensVerdict,
    MARK_UNDATED,
    MARK_UNRESOLVED,
    Pinned,
)


def _make_pinned(tmp_path: Path, rel: str, content: str, cap: int = 1500) -> tuple[Home, Pinned]:
    home = Home(tmp_path / "home")
    if content:
        home.write(rel, content, by="system")
    p = pin(home, rel, as_of="2026-11-16T12:00:00", cap=cap)
    return home, p


# -------------------------------------------------------------------------------------------------- Step 6: pin & choose_targets
def test_pin_and_choose_targets(tmp_path):
    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Item 1\n- Item 2\n", cap=100)
    assert pinned.rel == "MEMORY.md"
    assert pinned.lines == ["- Item 1", "- Item 2"]
    assert not pinned.over_budget
    assert "   1| - Item 1" in pinned.numbered

    # choose_targets: MEMORY.md always, person pages, AGENTS.md for lesson, user.md every 3rd sweep
    claims = [
        Claim(id="c1", kind="person", salience="high", text="Alice: friend", quote="Alice", speaker="user", handle="m1"),
        Claim(id="c2", kind="lesson", salience="medium", text="Keep notes brief", quote="brief", speaker="tool", handle="m2"),
    ]
    # sweep 1: MEMORY.md + person + AGENTS.md (no user.md)
    t1 = choose_targets(claims, sweep_counter=1)
    assert t1 == ["MEMORY.md", "memory/people/alice.md", "AGENTS.md"]

    # sweep 2: no user.md
    t2 = choose_targets(claims, sweep_counter=2)
    assert t2 == ["MEMORY.md", "memory/people/alice.md", "AGENTS.md"]

    # sweep 3: user.md included
    t3 = choose_targets(claims, sweep_counter=3)
    assert t3 == ["MEMORY.md", "memory/people/alice.md", "AGENTS.md", "user.md"]

    # sweep 0: no user.md
    t0 = choose_targets(claims, sweep_counter=0)
    assert t0 == ["MEMORY.md", "memory/people/alice.md", "AGENTS.md"]


# -------------------------------------------------------------------------------------------------- Step 7: build & parse request
def test_reconcile_request_builder_and_parser(tmp_path):
    _, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Old line\n", cap=500)
    claims = [Claim(id="c1", kind="fact", salience="high", text="User likes tea", quote="I like tea", speaker="user", handle="m1")]
    req = build_reconcile_request(pinned, admitted=claims, related=[])
    assert len(req) == 2
    assert "User likes tea" in req[1]["content"]

    # parse response with edits
    raw = json.dumps({
        "edits": [
            {
                "handle": "edit:1",
                "op": "replace",
                "start": 1,
                "end": 1,
                "guard": "- Old",
                "reason": "correct",
                "replacement": "- Corrected line",
                "evidence": ["claim:c1"],
            }
        ]
    })
    parsed = parse_reconcile_response(raw)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0].handle == "edit:1"
    assert parsed[0].reason == "correct"

    # parse response with no_change
    raw_nc = json.dumps({"no_change": "Everything current"})
    parsed_nc = parse_reconcile_response(raw_nc)
    assert parsed_nc == "Everything current"


# -------------------------------------------------------------------------------------------------- Step 8: each grounding rule
def test_grounding_line_numbers_exist(tmp_path):
    _, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Line 1\n- Line 2\n")
    # invalid start/end
    edits = [
        Edit(handle="edit:1", op="replace", start=0, end=1, guard="-", reason="deduplicate", replacement="- Fixed"),
        Edit(handle="edit:2", op="replace", start=1, end=5, guard="-", reason="deduplicate", replacement="- Fixed"),
        Edit(handle="edit:3", op="replace", start=2, end=1, guard="-", reason="deduplicate", replacement="- Fixed"),
    ]
    accepted, salvage = ground(pinned, edits, lambda _h: True)
    assert len(accepted) == 0
    assert len(salvage) == 3


def test_grounding_guards(tmp_path):
    _, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Line 1\n- Line 2\n")
    edits = [
        Edit(handle="edit:1", op="replace", start=1, end=1, guard="WrongGuard", reason="deduplicate", replacement="- New 1"),
        Edit(handle="edit:2", op="replace", start=2, end=2, guard="- Line 2", reason="deduplicate", replacement="- New 2"),
    ]
    accepted, salvage = ground(pinned, edits, lambda _h: True)
    assert len(accepted) == 1
    assert accepted[0].handle == "edit:2"
    assert any("guard mismatch" in s for s in salvage)


def test_grounding_overlap(tmp_path):
    _, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Line 1\n- Line 2\n- Line 3\n")
    edits = [
        Edit(handle="edit:1", op="replace", start=1, end=2, guard="- Line 1", reason="deduplicate", replacement="- Combined"),
        Edit(handle="edit:2", op="replace", start=2, end=3, guard="- Line 2", reason="deduplicate", replacement="- Overlapping"),
    ]
    accepted, salvage = ground(pinned, edits, lambda _h: True)
    assert len(accepted) == 1
    assert accepted[0].handle == "edit:1"
    assert any("overlaps with edit:1" in s for s in salvage)


def test_grounding_handle_resolution_and_free_reasons(tmp_path):
    _, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Line 1\n- Line 2\n- Line 3\n")

    def resolve(h: str) -> bool:
        return h == "claim:good"

    # deduplicate is handle-free -> allowed with no evidence
    # promote requires evidence -> allowed only if resolved
    # correct with unresolvable handle -> dropped
    edits = [
        Edit(handle="edit:1", op="replace", start=1, end=1, guard="- Line 1", reason="deduplicate", replacement="- Clean"),
        Edit(handle="edit:2", op="replace", start=2, end=2, guard="- Line 2", reason="promote", replacement="- Fact", evidence=["claim:good"]),
        Edit(handle="edit:3", op="replace", start=3, end=3, guard="- Line 3", reason="correct", replacement="- Fixed", evidence=["claim:bad"]),
    ]
    accepted, salvage = ground(pinned, edits, resolve)
    assert [e.handle for e in accepted] == ["edit:1", "edit:2"]
    assert any("unresolvable evidence handle" in s for s in salvage)


def test_grounding_promote_targets(tmp_path):
    _, pinned_user = _make_pinned(tmp_path, "user.md", "# user.md\n\n## Where and when\n- Timezone: UTC\n")
    edits = [
        Edit(handle="edit:1", op="replace", start=4, end=4, guard="- Timezone", reason="promote", replacement="- Timezone: Asia/Seoul", evidence=["claim:1"]),
    ]
    accepted, salvage = ground(pinned_user, edits, lambda _h: True)
    assert len(accepted) == 0
    assert any("promote not allowed into user.md" in s for s in salvage)


def test_grounding_user_md_no_new_entries_and_labels_headings(tmp_path):
    _, pinned_user = _make_pinned(tmp_path, "user.md", "# user.md\n\n## Where and when\n- Timezone: UTC\n")

    # 1. Heading cannot be replaced/removed
    e_head = Edit(handle="edit:1", op="replace", start=1, end=1, guard="# user.md", reason="condense", replacement="# notes")
    # 2. Label must be preserved
    e_bad_label = Edit(handle="edit:2", op="replace", start=4, end=4, guard="- Timezone", reason="condense", replacement="- Asia/Seoul")
    # 3. Label cannot be removed
    e_del_label = Edit(handle="edit:3", op="remove", start=4, end=4, guard="- Timezone", reason="condense")
    # 4. No new entries in user.md
    e_insert = Edit(handle="edit:4", op="insert_after", start=4, end=4, guard="- Timezone", reason="condense", replacement="- New fact")
    # 5. Good label update
    e_good = Edit(handle="edit:5", op="replace", start=4, end=4, guard="- Timezone", reason="condense", replacement="- Timezone: Asia/Seoul")

    accepted, salvage = ground(pinned_user, [e_head, e_bad_label, e_del_label, e_insert, e_good], lambda _h: True)
    assert [e.handle for e in accepted] == ["edit:5"]
    assert len(salvage) == 4


def test_grounding_scrub_finds_secret(tmp_path):
    _, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Safe line\n")
    edits = [
        Edit(handle="edit:1", op="replace", start=1, end=1, guard="- Safe", reason="deduplicate", replacement="- Router: Tr0ub4dor&3"),
    ]
    accepted, salvage = ground(pinned, edits, lambda _h: True)
    assert len(accepted) == 0
    assert any("secret detected" in s for s in salvage)


# -------------------------------------------------------------------------------------------------- Step 8: retire_expired vs reframe_completed
def test_retire_expired_vs_reframe_completed_on_passed_date(tmp_path):
    _, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Event on 2026-10-01. Meeting with team.\n")

    # retire_expired deleting whole sentence -> allowed
    e_retire_ok = Edit(
        handle="edit:1",
        op="replace",
        start=1,
        end=1,
        guard="- Event",
        reason="retire_expired",
        replacement="- Meeting with team.",
    )
    accepted, _ = ground(pinned, [e_retire_ok], lambda _h: True)
    assert len(accepted) == 1

    # retire_expired rewriting to completed -> refused (only whole sentence deletion allowed)
    e_retire_rewrite = Edit(
        handle="edit:2",
        op="replace",
        start=1,
        end=1,
        guard="- Event",
        reason="retire_expired",
        replacement="- Event completed on 2026-10-01.",
    )
    accepted2, salvage2 = ground(pinned, [e_retire_rewrite], lambda _h: True)
    assert len(accepted2) == 0
    assert any("retire_expired replace may only delete whole sentences" in s for s in salvage2)

    # reframe_completed without evidence -> refused
    e_reframe_no_ev = Edit(
        handle="edit:3",
        op="replace",
        start=1,
        end=1,
        guard="- Event",
        reason="reframe_completed",
        replacement="- Meeting completed successfully.",
        evidence=[],
    )
    accepted3, salvage3 = ground(pinned, [e_reframe_no_ev], lambda _h: True)
    assert len(accepted3) == 0
    assert any("requires evidence handle" in s for s in salvage3)

    # reframe_completed with valid evidence -> allowed
    e_reframe_ok = Edit(
        handle="edit:4",
        op="replace",
        start=1,
        end=1,
        guard="- Event",
        reason="reframe_completed",
        replacement="- Meeting completed successfully.",
        evidence=["claim:done1"],
    )
    accepted4, _ = ground(pinned, [e_reframe_ok], lambda h: h == "claim:done1")
    assert len(accepted4) == 1


# -------------------------------------------------------------------------------------------------- Step 8: markers once
def test_markers_once(tmp_path):
    _, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Bob is 35 years old.\n- Alice is 30 years old (as of an unknown date).\n")

    # mark_undated on undated line -> allowed
    e1 = Edit(
        handle="edit:1",
        op="replace",
        start=1,
        end=1,
        guard="- Bob",
        reason="mark_undated",
        replacement=f"- Bob is 35 years old. {MARK_UNDATED}",
    )
    # mark_undated on line that already has marker -> dropped
    e2 = Edit(
        handle="edit:2",
        op="replace",
        start=2,
        end=2,
        guard="- Alice",
        reason="mark_undated",
        replacement=f"- Alice is 30 years old (as of an unknown date). {MARK_UNDATED}",
    )
    accepted, salvage = ground(pinned, [e1, e2], lambda _h: True)
    assert [e.handle for e in accepted] == ["edit:1"]
    assert any("already present" in s for s in salvage)


# -------------------------------------------------------------------------------------------------- Step 9: stage & over cap not shrinking refused
def test_stage_and_over_cap_not_shrinking_refused(tmp_path):
    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "a" * 1200, cap=1000)
    assert pinned.over_budget

    # 1. Edit that grows the file over cap -> refused
    e_grow = Edit(handle="edit:1", op="replace", start=1, end=1, guard="a", reason="deduplicate", replacement="a" * 1300)
    with pytest.raises(RefusedError):
        stage(home, "run-1", pinned, [e_grow])

    # 2. Edit that shrinks the file over cap -> allowed
    e_shrink = Edit(handle="edit:2", op="replace", start=1, end=1, guard="a", reason="demote_to_log", replacement="a" * 1050)
    receipt = stage(home, "run-2", pinned, [e_shrink])
    assert receipt.over_budget
    assert Path(receipt.candidate_path).is_file()
    assert receipt.touched_bytes > 0


# -------------------------------------------------------------------------------------------------- Step 10: review missing lens -> nothing applied
def test_review_missing_a_lens_nothing_applied(tmp_path):
    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Line 1\n")
    edits = [Edit(handle="edit:1", op="replace", start=1, end=1, guard="- Line 1", reason="deduplicate", replacement="- Updated")]
    receipt = stage(home, "run-1", pinned, edits)

    # Missing completeness lens:
    partial_review = {
        "verdicts": [
            {"lens": "evidence", "verdict": "pass"},
            {"lens": "temporal", "verdict": "pass"},
            {"lens": "preservation", "verdict": "pass"},
        ]
    }
    verdicts = parse_review_response(partial_review)
    assert any(v.lens == "completeness" and v.verdict == "hold" for v in verdicts)
    assert any(v.blocking for v in verdicts)

    # In reconcile_file, missing lens causes nothing to be applied
    responses = [
        {"edits": [{"handle": "edit:1", "op": "replace", "start": 1, "end": 1, "guard": "- Line 1", "reason": "deduplicate", "replacement": "- Updated"}]},
        partial_review,
    ]
    call_idx = 0

    def fake_model(_req):
        nonlocal call_idx
        res = responses[call_idx]
        call_idx += 1
        return res

    stats = reconcile_file(pinned, [], [], "", fake_model, lambda _h: True, lambda _l: None, home=home)
    assert not stats["applied"]
    assert stats["status"] == "blocked"
    # Live file untouched
    assert home.read("MEMORY.md") == "- Line 1\n"


# -------------------------------------------------------------------------------------------------- Step 12: blocking verdict naming edit:2 restaged without it
def test_blocking_verdict_naming_edit_2_restaged_without_it(tmp_path):
    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Line 1\n- Line 2\n")

    responses = [
        # 1. Step 7: two edits
        {
            "edits": [
                {"handle": "edit:1", "op": "replace", "start": 1, "end": 1, "guard": "- Line 1", "reason": "deduplicate", "replacement": "- One"},
                {"handle": "edit:2", "op": "replace", "start": 2, "end": 2, "guard": "- Line 2", "reason": "deduplicate", "replacement": "- Two"},
            ]
        },
        # 2. Step 10: first review blocks naming edit:2
        {
            "verdicts": [
                {"lens": "evidence", "verdict": "pass"},
                {"lens": "temporal", "verdict": "fail", "reason": "edit:2 has temporal issue", "evidence": ["edit:2"]},
                {"lens": "completeness", "verdict": "pass"},
                {"lens": "preservation", "verdict": "pass"},
            ]
        },
        # 3. Second review on restaged candidate without edit:2 -> all pass
        {
            "verdicts": [
                {"lens": "evidence", "verdict": "pass"},
                {"lens": "temporal", "verdict": "pass"},
                {"lens": "completeness", "verdict": "pass"},
                {"lens": "preservation", "verdict": "pass"},
            ]
        },
        # 4. If restaged candidate is broad, second preservation is called
        {"lens": "preservation", "verdict": "pass"},
    ]
    call_idx = 0

    def fake_model(_req):
        nonlocal call_idx
        res = responses[call_idx]
        call_idx += 1
        return res

    stats = reconcile_file(pinned, [], [], "", fake_model, lambda _h: True, lambda _l: None, home=home)
    assert stats["applied"]
    assert stats["edits_applied"] == 1
    assert stats["withheld"] == ["edit:2"]
    assert home.read("MEMORY.md") == "- One\n- Line 2\n"


# -------------------------------------------------------------------------------------------------- Step 12: second block -> plan waits
def test_second_block_plan_waits(tmp_path):
    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Line 1\n- Line 2\n")

    responses = [
        # 1. Step 7: two edits
        {
            "edits": [
                {"handle": "edit:1", "op": "replace", "start": 1, "end": 1, "guard": "- Line 1", "reason": "deduplicate", "replacement": "- One"},
                {"handle": "edit:2", "op": "replace", "start": 2, "end": 2, "guard": "- Line 2", "reason": "deduplicate", "replacement": "- Two"},
            ]
        },
        # 2. First review blocks edit:2
        {
            "verdicts": [
                {"lens": "evidence", "verdict": "pass"},
                {"lens": "temporal", "verdict": "fail", "evidence": ["edit:2"]},
                {"lens": "completeness", "verdict": "pass"},
                {"lens": "preservation", "verdict": "pass"},
            ]
        },
        # 3. Second review also blocks (e.g. edit:1)
        {
            "verdicts": [
                {"lens": "evidence", "verdict": "fail", "evidence": ["edit:1"]},
                {"lens": "temporal", "verdict": "pass"},
                {"lens": "completeness", "verdict": "pass"},
                {"lens": "preservation", "verdict": "pass"},
            ]
        },
    ]
    call_idx = 0

    def fake_model(_req):
        nonlocal call_idx
        res = responses[call_idx]
        call_idx += 1
        return res

    stats = reconcile_file(pinned, [], [], "", fake_model, lambda _h: True, lambda _l: None, home=home)
    assert not stats["applied"]
    assert stats["status"] == "blocked"
    # Live file untouched
    assert home.read("MEMORY.md") == "- Line 1\n- Line 2\n"


# -------------------------------------------------------------------------------------------------- Step 11: second preservation only when broad and independent
def test_second_preservation_only_when_broad_and_independent(tmp_path):
    # Narrow change (touched < kept): 1 line changed out of 5 long lines
    home, pinned_narrow = _make_pinned(
        tmp_path,
        "MEMORY.md",
        "- Durable fact one that is quite long and stays.\n"
        "- Durable fact two that is quite long and stays.\n"
        "- Durable fact three that is quite long and stays.\n"
        "- Short edit\n"
        "- Durable fact five that is quite long and stays.\n",
    )
    review_pass = {
        "verdicts": [
            {"lens": "evidence", "verdict": "pass"},
            {"lens": "temporal", "verdict": "pass"},
            {"lens": "completeness", "verdict": "pass"},
            {"lens": "preservation", "verdict": "pass"},
        ]
    }
    calls = []

    def fake_model_narrow(req):
        calls.append(req)
        if len(calls) == 1:
            return {"edits": [{"handle": "edit:1", "op": "replace", "start": 4, "end": 4, "guard": "- Short", "reason": "deduplicate", "replacement": "- Short edited"}]}
        return review_pass

    stats_narrow = reconcile_file(pinned_narrow, [], [], "", fake_model_narrow, lambda _h: True, lambda _l: None, home=home)
    assert stats_narrow["applied"]
    # Exactly 2 calls: reconcile + review (NO second preservation call)
    assert len(calls) == 2

    # Broad change (touched >= kept): whole file replaced
    home2, pinned_broad = _make_pinned(tmp_path, "AGENTS.md", "- Short line\n")
    calls_broad = []

    def fake_model_broad(req):
        calls_broad.append(req)
        if len(calls_broad) == 1:
            return {"edits": [{"handle": "edit:1", "op": "replace", "start": 1, "end": 1, "guard": "- Short", "reason": "deduplicate", "replacement": "- Completely rewritten line here"}]}
        elif len(calls_broad) == 2:
            return review_pass
        else:
            # Second preservation call
            # Check independence: Step 10's answer is NOT in the prompt!
            prompt_content = str(req)
            assert "completeness" not in prompt_content
            assert "temporal" not in prompt_content
            return {"lens": "preservation", "verdict": "pass"}

    stats_broad = reconcile_file(pinned_broad, [], [], "", fake_model_broad, lambda _h: True, lambda _l: None, home=home2)
    assert stats_broad["applied"]
    # Exactly 3 calls: reconcile + review + second preservation
    assert len(calls_broad) == 3


# -------------------------------------------------------------------------------------------------- Step 13: live file changed -> left alone
def test_live_file_changed_left_alone(tmp_path):
    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Line 1\n")

    # In the meantime, another process writes to MEMORY.md:
    home.write("MEMORY.md", "- Changed by user\n", by="system")

    edits = [Edit(handle="edit:1", op="replace", start=1, end=1, guard="- Line 1", reason="deduplicate", replacement="- Updated")]
    receipt = stage(home, "run-1", pinned, edits)

    res = apply(home, receipt, pinned)
    assert not res["applied"]
    assert res["reason"] == "live file changed meanwhile"
    assert home.read("MEMORY.md") == "- Changed by user\n"


# -------------------------------------------------------------------------------------------------- Step 13: demoted lines verbatim in log
def test_demoted_lines_verbatim_in_log(tmp_path):
    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Old important fact\n- Current fact\n")
    logged = []

    def log_append(block: str):
        logged.append(block)

    edits = [Edit(handle="edit:1", op="remove", start=1, end=1, guard="- Old", reason="demote_to_log", replacement="")]
    accepted, _ = ground(pinned, edits, lambda _h: True)
    receipt = stage(home, "run-demote", pinned, accepted)

    res = apply(home, receipt, pinned, log_append=log_append)
    assert res["applied"]
    assert len(logged) == 1
    assert "## Demoted run-demote" in logged[0]
    assert "- Old important fact" in logged[0]
    assert home.read("MEMORY.md") == "- Current fact\n"


# -------------------------------------------------------------------------------------------------- Step 13: undo restores
def test_undo_restores(tmp_path):
    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Original version\n")
    edits = [Edit(handle="edit:1", op="replace", start=1, end=1, guard="- Original", reason="deduplicate", replacement="- Modified version")]
    accepted, _ = ground(pinned, edits, lambda _h: True)
    receipt = stage(home, "run-undo", pinned, accepted)

    res = apply(home, receipt, pinned)
    assert res["applied"]
    assert home.read("MEMORY.md") == "- Modified version\n"

    # Call undo
    undo_res = undo(home, "run-undo")
    assert "MEMORY.md" in undo_res["restored"]
    assert home.read("MEMORY.md") == "- Original version\n"


# -------------------------------------------------------------------------------------------------- Async streaming fake model
@pytest.mark.asyncio
async def test_reconcile_file_with_async_streaming_model(tmp_path):
    from assistant.providers import TextDelta, Done

    home, pinned = _make_pinned(tmp_path, "MEMORY.md", "- Initial item\n")

    responses = [
        json.dumps({"edits": [{"handle": "edit:1", "op": "replace", "start": 1, "end": 1, "guard": "- Initial", "reason": "deduplicate", "replacement": "- Updated item"}]}),
        json.dumps({
            "verdicts": [
                {"lens": "evidence", "verdict": "pass"},
                {"lens": "temporal", "verdict": "pass"},
                {"lens": "completeness", "verdict": "pass"},
                {"lens": "preservation", "verdict": "pass"},
            ]
        }),
        json.dumps({"lens": "preservation", "verdict": "pass"}),
    ]
    call_idx = 0

    def stream_model(_req):
        nonlocal call_idx
        res = responses[call_idx]
        call_idx += 1

        async def _stream():
            yield TextDelta(res[:10])
            yield TextDelta(res[10:])
            yield Done("stop")

        return _stream()

    stats = await reconcile_file(pinned, [], [], "", stream_model, lambda _h: True, lambda _l: None, home=home)
    assert stats["applied"]
    assert home.read("MEMORY.md") == "- Updated item\n"

