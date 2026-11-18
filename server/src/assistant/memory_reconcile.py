"""Memory pipeline part 2: reconcile (steps 6-13, docs/memory-pipeline.md).

Pure, testable functions for:
  step 6:  pin(home, rel, as_of, cap) -> Pinned and choose_targets(admitted, sweep_counter)
  step 7:  build_reconcile_request + parse_reconcile_response -> list[Edit] | str
  step 8:  ground(pinned, edits, resolve_handle) -> (edits, salvage)
  step 9:  stage(home, run_id, pinned, edits, salvage, withheld) -> Receipt
  step 10: build_review_request + parse_review_response -> list[LensVerdict]
  step 11: build_preservation_second_request + parse_preservation_second_response -> LensVerdict
  step 12: prune_and_restage(home, receipt, pinned, blocking_verdicts) -> (Receipt | None, withheld)
  step 13: apply(home, receipt, pinned, log_append) -> dict
  and reconcile_file(pinned, admitted, related, paired_text, model_call, resolve_handle, log_append) -> dict stats.
"""

from __future__ import annotations

import os
import asyncio
import hashlib
import inspect
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .home import Home, HomeError, PEOPLE_DIR
from .memory_types import (
    CONTENT_REASONS,
    Claim,
    Edit,
    EDIT_OPS,
    EDIT_REASONS,
    HANDLE_FREE_REASONS,
    LENSES,
    LensVerdict,
    MARK_UNDATED,
    MARK_UNRESOLVED,
    Pinned,
    Receipt,
)
from .memory_upkeep import (
    BACKUPS,
    CAPS,
    fit_text,
    MEMORY_BUDGET,
    PERSON_CAP,
    scrub,
    _norm,
    _sha,
    _slug,
)
from .reflection import parse_output

log = logging.getLogger(__name__)

_PROMPT_DIR = Path(os.environ.get("ASSISTANT_CONTENT_DIR") or Path(__file__).resolve().parents[2] / "content") / "prompts" / "memory"
_PINNED_HOME: dict[int, Any] = {}


class RefusedError(ValueError):
    """A candidate over cap that does not shrink is refused (the plan waits)."""


class ReconcileStats(dict[str, Any]):
    """Dictionary of reconciliation stats that can also be awaited directly."""

    def __await__(self):
        async def _id():
            return self
        return _id().__await__()


def _load_prompt(name: str, default: str) -> str:
    path = _PROMPT_DIR / name
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            pass
    return default


_RECONCILE_PROMPT = _load_prompt("reconcile.md", """You reconcile one long-term memory file against newly admitted claims and active related claims.""")
_REVIEW_PROMPT = _load_prompt("review.md", """You review proposed edits to a long-term memory file across four independent lenses.""")
_PRESERVATION_SECOND_PROMPT = _load_prompt("preservation_second.md", """You perform an independent preservation review for a substantial rewrite of a long-term memory file.""")


# -------------------------------------------------------------------------------------------------- step 6: pin and choose_targets
def pin(home: Any, rel: str, as_of: str, cap: int) -> Pinned:
    """Step 6: Takes a snapshot of a file to reconcile, recording its line-numbered content, sha256, and budget posture."""
    root = home.root if hasattr(home, "root") else Path(home)
    p = root / rel
    if p.is_file() and not p.is_symlink():
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        text = ""
    lines = text.splitlines()
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    size = len(text)
    over_budget = size > cap
    pinned = Pinned(
        rel=rel,
        lines=lines,
        sha=sha,
        as_of=as_of,
        cap=cap,
        size=size,
        over_budget=over_budget,
    )
    _PINNED_HOME[id(pinned)] = home
    return pinned


def choose_targets(admitted: list[Claim], sweep_counter: int) -> list[str]:
    """Step 6: Chooses reconciliation targets in priority order:
    1. MEMORY.md (every due run)
    2. people pages named by admitted `person` claims
    3. AGENTS.md when `lesson` claims were admitted
    4. user.md (cleanup only) every 3rd sweep (counter % 3 == 0 and counter > 0).
    """
    targets: list[str] = ["MEMORY.md"]

    for c in admitted:
        if c.kind == "person":
            text = (c.text or "").strip()
            if text.startswith("memory/people/") and text.endswith(".md"):
                rel = text
            else:
                name = text.split(":")[0].split(",")[0].strip()
                slug = _slug(name)
                rel = f"{PEOPLE_DIR}/{slug}.md" if slug else ""
            if rel and rel not in targets:
                targets.append(rel)

    if any(c.kind == "lesson" for c in admitted):
        if "AGENTS.md" not in targets:
            targets.append("AGENTS.md")

    if sweep_counter > 0 and sweep_counter % 3 == 0:
        if "user.md" not in targets:
            targets.append("user.md")

    return targets


# -------------------------------------------------------------------------------------------------- step 7: reconcile request builder + parse
def build_reconcile_request(pinned: Pinned, admitted: list[Claim], related: list[Claim], paired_text: str = "") -> list[dict[str, Any]]:
    """Step 7: Builds the reconcile model request for a pinned file."""
    prompt = _RECONCILE_PROMPT

    admitted_lines = []
    for c in admitted:
        until = f" · until {c.valid_until}" if c.valid_until else ""
        admitted_lines.append(f"- id:{c.id} · {c.kind} · {c.salience} · {c.speaker} · {c.handle} · quote:{c.quote!r} · {c.text}{until}")
    admitted_block = "\n".join(admitted_lines) if admitted_lines else "(none)"

    related_lines = []
    for c in related:
        until = f" · until {c.valid_until}" if c.valid_until else ""
        related_lines.append(f"- id:{c.id} · {c.kind} · {c.salience} · {c.speaker} · {c.handle} · {c.text}{until}")
    related_block = "\n".join(related_lines) if related_lines else "(none)"

    user_parts = [
        f"File: {pinned.rel}",
        f"As of: {pinned.as_of}",
        f"Budget posture: cap={pinned.cap}, size={pinned.size}, over_budget={pinned.over_budget}",
        f"Current snapshot ({pinned.rel}):\n{pinned.numbered or '(empty file)'}",
        f"Admitted claims this pass:\n{admitted_block}",
        f"Active related claims:\n{related_block}",
    ]
    if paired_text:
        user_parts.append(f"Paired comparison projection (for comparison only, do not copy verbatim):\n{paired_text}")

    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def parse_reconcile_response(raw: str | dict[str, Any]) -> list[Edit] | str | None:
    """Step 7: Parses the model's reconcile response into a list of Edit objects or 'no_change' string."""
    if isinstance(raw, dict):
        data = raw
    else:
        data = parse_output(str(raw or ""))
        if data is None:
            try:
                data = json.loads(str(raw or ""))
            except Exception:
                return None

    if not isinstance(data, dict):
        return None

    if "no_change" in data and data["no_change"]:
        return str(data["no_change"])

    edits_raw = data.get("edits")
    if edits_raw is None:
        if "no_change" in data:
            return "no_change"
        return None

    if not isinstance(edits_raw, list):
        return None

    edits: list[Edit] = []
    for i, item in enumerate(edits_raw, 1):
        if not isinstance(item, dict):
            continue
        handle = str(item.get("handle") or f"edit:{i}")
        op = str(item.get("op") or "")
        start = int(item.get("start", 0))
        end = int(item.get("end", start))
        guard = str(item.get("guard") or "")
        reason = str(item.get("reason") or "")
        replacement = str(item.get("replacement") or "")
        evidence_raw = item.get("evidence")
        evidence = [str(x) for x in evidence_raw] if isinstance(evidence_raw, list) else []
        origin_raw = item.get("origin")
        origin = {str(k): str(v) for k, v in origin_raw.items()} if isinstance(origin_raw, dict) else None

        edits.append(
            Edit(
                handle=handle,
                op=op,
                start=start,
                end=end,
                guard=guard,
                reason=reason,
                replacement=replacement,
                evidence=evidence,
                origin=origin,
            )
        )

    return edits


# -------------------------------------------------------------------------------------------------- step 8: ground
def _extract_sentences(text: str) -> list[str]:
    """Splits text into trimmed sentences, ignoring leading list bullet markers."""
    sentences: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
        if not cleaned:
            continue
        parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
        sentences.extend(parts)
    return sentences


def _is_sentence_subset(orig_text: str, repl_text: str) -> bool:
    """Returns True if repl_text consists only of a strict subset of whole sentences from orig_text, in original order."""
    orig_s = _extract_sentences(orig_text)
    repl_s = _extract_sentences(repl_text)
    if not repl_s or len(repl_s) >= len(orig_s):
        return False
    it = iter(orig_s)
    return all(s in it for s in repl_s)


_USER_MD_LABEL = re.compile(r"^(\s*[-*+]?\s*[A-Za-z0-9_ ]+:)")


def ground(pinned: Pinned, edits: list[Edit], resolve_handle: Callable[[str], bool],
           owner_evidence: Callable[[str], bool] = lambda _h: False) -> tuple[list[Edit], list[str]]:
    """Step 8: Grounds proposed edits against the snapshot and verification rules.
    Returns (accepted_edits, salvage_reasons). New content (a promote, correct or reframe, or any insert) needs at least one evidence
    handle that is the owner's own words (`owner_evidence`): a file line or the assistant's text never is (memory-owner-only). The
    default refuses, so a caller that passes nothing gets no new content.
    """
    accepted: list[Edit] = []
    salvage: list[str] = []
    num_lines = len(pinned.lines)

    for e in edits:
        # 1. Operation check
        if e.op not in EDIT_OPS:
            salvage.append(f"{e.handle}: unknown op {e.op!r}")
            continue

        # 2. Reason check
        if e.reason not in EDIT_REASONS:
            salvage.append(f"{e.handle}: unknown reason {e.reason!r}")
            continue

        # 3. Line numbers exist
        if e.op == "insert_after":
            if not (0 <= e.start <= num_lines):
                salvage.append(f"{e.handle}: invalid insert_after position start={e.start} for {num_lines} lines")
                continue
        else:
            if num_lines == 0:
                salvage.append(f"{e.handle}: cannot {e.op} on empty file")
                continue
            if not (1 <= e.start <= e.end <= num_lines):
                salvage.append(f"{e.handle}: invalid line range {e.start}..{e.end} for {num_lines} lines")
                continue

        # 4. Guard check
        if e.start >= 1 and e.start <= num_lines:
            target_first_line = pinned.lines[e.start - 1]
            if not e.guard:
                salvage.append(f"{e.handle}: missing guard for start line {e.start}")
                continue
            if not (target_first_line.strip().startswith(e.guard.strip()) or _norm(target_first_line).startswith(_norm(e.guard))):
                salvage.append(f"{e.handle}: guard mismatch ({e.guard!r} does not match start of line {e.start})")
                continue

        # 5. Overlap check
        overlap = False
        for a in accepted:
            if a.op in ("replace", "remove"):
                if e.op in ("replace", "remove"):
                    if max(e.start, a.start) <= min(e.end, a.end):
                        overlap = True
                elif e.op == "insert_after":
                    if a.start <= e.start <= a.end:
                        overlap = True
            elif a.op == "insert_after":
                if e.op in ("replace", "remove"):
                    if e.start <= a.start <= e.end:
                        overlap = True
                elif e.op == "insert_after":
                    if e.start == a.start:
                        overlap = True
            if overlap:
                salvage.append(f"{e.handle}: overlaps with {a.handle}")
                break
        if overlap:
            continue

        # 6. Handle resolution check
        if e.reason not in HANDLE_FREE_REASONS:
            if not e.evidence:
                salvage.append(f"{e.handle}: reason {e.reason} requires evidence handle")
                continue
            unresolved = [h for h in e.evidence if not resolve_handle(h)]
            if unresolved:
                salvage.append(f"{e.handle}: unresolvable evidence handle(s): {', '.join(unresolved)}")
                continue
        else:
            if e.evidence:
                unresolved = [h for h in e.evidence if not resolve_handle(h)]
                if unresolved:
                    salvage.append(f"{e.handle}: unresolvable evidence handle(s): {', '.join(unresolved)}")
                    continue

        # 6b. New content needs the owner's own words as evidence
        if (e.reason in CONTENT_REASONS or e.op == "insert_after") and not any(owner_evidence(h) for h in e.evidence):
            salvage.append(f"{e.handle}: needs the owner's own words as evidence")
            continue

        # 7. Promote targets
        if e.reason == "promote":
            allowed_promote = (
                pinned.rel in ("MEMORY.md", "AGENTS.md")
                or (pinned.rel.startswith("memory/people/") and pinned.rel.endswith(".md"))
            )
            if not allowed_promote:
                salvage.append(f"{e.handle}: promote not allowed into {pinned.rel}")
                continue

        # 8. user.md rules: no new entries, labels and headings kept
        if pinned.rel == "user.md":
            if e.op == "insert_after":
                salvage.append(f"{e.handle}: no new entries allowed in user.md")
                continue
            orig_target_lines = pinned.lines[e.start - 1 : e.end]
            has_heading = any(line.strip().startswith("#") for line in orig_target_lines)
            if has_heading:
                salvage.append(f"{e.handle}: cannot remove or replace heading in user.md")
                continue

            label_violation = False
            for line in orig_target_lines:
                m = _USER_MD_LABEL.match(line)
                if m:
                    label = m.group(1).strip()
                    if e.op == "remove":
                        salvage.append(f"{e.handle}: cannot remove label {label!r} in user.md")
                        label_violation = True
                        break
                    elif e.op == "replace":
                        if not any(rep_line.strip().lower().startswith(label.lower()) for rep_line in e.replacement.splitlines()):
                            salvage.append(f"{e.handle}: replacement must preserve label {label!r} in user.md")
                            label_violation = True
                            break
            if label_violation:
                continue

        # 9. Whole-sentence-only retire_expired
        if e.reason == "retire_expired":
            if e.op == "insert_after":
                salvage.append(f"{e.handle}: retire_expired cannot insert new content")
                continue
            elif e.op == "replace":
                orig_target_text = "\n".join(pinned.lines[e.start - 1 : e.end])
                if not _is_sentence_subset(orig_target_text, e.replacement):
                    salvage.append(f"{e.handle}: retire_expired replace may only delete whole sentences of the original")
                    continue

        # 10. Marker-only mark_*
        if e.reason in ("mark_undated", "mark_unresolved"):
            if e.op != "replace":
                salvage.append(f"{e.handle}: {e.reason} must use replace operation")
                continue
            marker = MARK_UNDATED if e.reason == "mark_undated" else MARK_UNRESOLVED
            orig_target_text = "\n".join(pinned.lines[e.start - 1 : e.end])
            if marker in orig_target_text:
                salvage.append(f"{e.handle}: marker {marker!r} already present")
                continue
            expected_replacement = f"{orig_target_text.rstrip()} {marker}"
            if e.replacement.strip() != expected_replacement.strip():
                salvage.append(f"{e.handle}: {e.reason} may only append its exact marker once")
                continue

        # 11. Scrub secrets
        if e.replacement:
            _, secret_count = scrub(e.replacement)
            if secret_count > 0:
                salvage.append(f"{e.handle}: secret detected in replacement text")
                continue

        # 12. Resolved target lines
        if e.op in ("replace", "remove") and 1 <= e.start <= num_lines:
            e.resolved_target = "\n".join(pinned.lines[e.start - 1 : e.end])
        else:
            e.resolved_target = ""

        accepted.append(e)

    return accepted, salvage


# -------------------------------------------------------------------------------------------------- step 9: stage
def _apply_edits_to_lines(lines: list[str], edits: list[Edit]) -> list[str]:
    """Applies non-overlapping grounded edits in reverse line order."""
    out = list(lines)
    sorted_edits = sorted(edits, key=lambda e: (e.start, e.end), reverse=True)
    for e in sorted_edits:
        if e.op == "replace":
            repl_lines = e.replacement.splitlines() if e.replacement else []
            out[e.start - 1 : e.end] = repl_lines
        elif e.op == "remove":
            out[e.start - 1 : e.end] = []
        elif e.op == "insert_after":
            repl_lines = e.replacement.splitlines() if e.replacement else []
            out[e.start : e.start] = repl_lines
    return out


def stage(
    home: Any,
    run_id: str,
    pinned: Pinned,
    edits: list[Edit],
    salvage: list[str] | None = None,
    withheld: list[str] | None = None,
) -> Receipt:
    """Step 9: Stages the before and candidate files under .memory-passes/<run_id>/stage/,
    calculates touched/kept bytes, and enforces cap shrinking.
    """
    root = home.root if hasattr(home, "root") else Path(home)
    stage_dir = root / BACKUPS / run_id / "stage"
    stage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    before_lines = list(pinned.lines)
    before_text = "\n".join(before_lines) + ("\n" if before_lines else "")

    candidate_lines = _apply_edits_to_lines(before_lines, edits)
    candidate_text = "\n".join(candidate_lines) + ("\n" if candidate_lines else "")

    candidate_size = len(candidate_text)
    # Check cap posture: over cap and not shrinking -> refused
    if candidate_size > pinned.cap and candidate_size >= pinned.size:
        raise RefusedError(f"Candidate size ({candidate_size}) exceeds cap ({pinned.cap}) and did not shrink from original size ({pinned.size})")

    # Compute touched and kept bytes
    touched_indices: set[int] = set()
    for e in edits:
        if e.start >= 1 and e.op in ("replace", "remove"):
            touched_indices.update(range(e.start - 1, min(e.end, len(before_lines))))

    kept_bytes = sum(len((before_lines[i] + "\n").encode("utf-8")) for i in range(len(before_lines)) if i not in touched_indices)
    touched_before_bytes = sum(len((before_lines[i] + "\n").encode("utf-8")) for i in touched_indices)
    inserted_bytes = sum(len(e.replacement.encode("utf-8")) for e in edits if e.replacement)
    touched_bytes = touched_before_bytes + inserted_bytes

    safe_name = pinned.rel.replace("/", "__")
    before_file = stage_dir / f"{safe_name}.before"
    candidate_file = stage_dir / f"{safe_name}.candidate"
    before_file.write_text(before_text, encoding="utf-8")
    candidate_file.write_text(candidate_text, encoding="utf-8")

    return Receipt(
        run_id=run_id,
        target=pinned.rel,
        before_path=str(before_file),
        candidate_path=str(candidate_file),
        as_of=pinned.as_of,
        cap=pinned.cap,
        over_budget=candidate_size > pinned.cap,
        edits=edits,
        salvage=salvage or [],
        touched_bytes=touched_bytes,
        kept_bytes=kept_bytes,
        withheld=withheld or [],
    )


# -------------------------------------------------------------------------------------------------- step 10: review request + parse
def build_review_request(receipt: Receipt, before_text: str, candidate_text: str) -> list[dict[str, Any]]:
    """Step 10: Builds the four-lens review request for a staged candidate."""
    edits_desc = []
    for e in receipt.edits:
        ev = f" · evidence: {', '.join(e.evidence)}" if e.evidence else ""
        orig = f" · origin: {e.origin}" if e.origin else ""
        edits_desc.append(f"- {e.handle}: {e.op} lines {e.start}..{e.end} · reason: {e.reason}{ev}{orig}")
    edits_block = "\n".join(edits_desc) if edits_desc else "(none)"

    user_parts = [
        f"File: {receipt.target}",
        f"As of: {receipt.as_of}",
        f"Cap: {receipt.cap}, touched_bytes: {receipt.touched_bytes}, kept_bytes: {receipt.kept_bytes}",
        f"Proposed edits in plan:\n{edits_block}",
        f"Original before text:\n{before_text or '(empty)'}",
        f"Candidate staged text:\n{candidate_text or '(empty)'}",
    ]

    return [
        {"role": "system", "content": _REVIEW_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def parse_review_response(raw: str | dict[str, Any]) -> list[LensVerdict]:
    """Step 10: Parses the review response for the four lenses: evidence, temporal, completeness, preservation.
    Any missing lens defaults to hold.
    """
    if isinstance(raw, dict):
        data = raw
    else:
        data = parse_output(str(raw or ""))
        if data is None:
            try:
                data = json.loads(str(raw or ""))
            except Exception:
                data = {}

    verdicts_by_lens: dict[str, dict[str, Any]] = {}
    if isinstance(data, dict):
        items = data.get("verdicts")
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and "lens" in it:
                    verdicts_by_lens[str(it["lens"]).lower()] = it
        for lens in LENSES:
            if lens in data and isinstance(data[lens], dict):
                verdicts_by_lens[lens] = data[lens]

    out: list[LensVerdict] = []
    for lens in LENSES:
        entry = verdicts_by_lens.get(lens)
        if entry is not None:
            v_str = str(entry.get("verdict", "")).lower()
            verdict: Any = v_str if v_str in ("pass", "fail", "hold") else "hold"
            reason = str(entry.get("reason", ""))
            ev_raw = entry.get("evidence", [])
            evidence = [str(x) for x in ev_raw] if isinstance(ev_raw, list) else []
            out.append(LensVerdict(lens=lens, verdict=verdict, reason=reason, evidence=evidence))
        else:
            out.append(LensVerdict(lens=lens, verdict="hold", reason=f"missing {lens} lens", evidence=[]))

    return out


# -------------------------------------------------------------------------------------------------- step 11: second preservation request
def build_preservation_second_request(receipt: Receipt, before_text: str, candidate_text: str) -> list[dict[str, Any]]:
    """Step 11: Independent second preservation review request (never sees step 10's answer)."""
    user_parts = [
        f"File: {receipt.target}",
        f"As of: {receipt.as_of}",
        f"Metrics: cap={receipt.cap}, touched_bytes={receipt.touched_bytes}, kept_bytes={receipt.kept_bytes}",
        f"Original before text:\n{before_text or '(empty)'}",
        f"Candidate staged text:\n{candidate_text or '(empty)'}",
    ]
    return [
        {"role": "system", "content": _PRESERVATION_SECOND_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def parse_preservation_second_response(raw: str | dict[str, Any]) -> LensVerdict:
    """Step 11: Parses second preservation review response."""
    if isinstance(raw, dict):
        data = raw
    else:
        data = parse_output(str(raw or ""))
        if data is None:
            try:
                data = json.loads(str(raw or ""))
            except Exception:
                data = {}

    if isinstance(data, dict):
        if "verdict" in data:
            entry = data
        elif "preservation" in data and isinstance(data["preservation"], dict):
            entry = data["preservation"]
        elif "verdicts" in data and isinstance(data["verdicts"], list):
            entry = next((it for it in data["verdicts"] if isinstance(it, dict) and it.get("lens") == "preservation"), {})
        else:
            entry = {}
    else:
        entry = {}

    v_str = str(entry.get("verdict", "")).lower()
    verdict: Any = v_str if v_str in ("pass", "fail", "hold") else "hold"
    reason = str(entry.get("reason", ""))
    ev_raw = entry.get("evidence", [])
    evidence = [str(x) for x in ev_raw] if isinstance(ev_raw, list) else []
    return LensVerdict(lens="preservation", verdict=verdict, reason=reason, evidence=evidence)


# -------------------------------------------------------------------------------------------------- step 12: prune_and_restage
def prune_and_restage(
    home: Any,
    receipt: Receipt,
    pinned: Pinned,
    blocking_verdicts: list[LensVerdict],
) -> tuple[Receipt | None, list[str]]:
    """Step 12: Prunes edits named in blocking verdicts and restages the remaining edits.
    If no edit is named, or if no edits remain, the plan waits (returns None, withheld).
    """
    blocked_handles: set[str] = set()
    for v in blocking_verdicts:
        blocked_handles.update(v.edits())

    if not blocked_handles:
        # First block naming no edit -> plan waits
        return None, []

    remaining_edits = [e for e in receipt.edits if e.handle not in blocked_handles]
    withheld = list(dict.fromkeys([*receipt.withheld, *sorted(blocked_handles)]))

    if not remaining_edits:
        # All edits withheld -> plan waits
        return None, withheld

    new_receipt = stage(
        home,
        receipt.run_id,
        pinned,
        remaining_edits,
        salvage=receipt.salvage,
        withheld=withheld,
    )
    return new_receipt, withheld


# -------------------------------------------------------------------------------------------------- step 13: apply
def apply(home: Any, receipt: Receipt, pinned: Pinned, log_append: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Step 13: Applies staged candidate file to home:
    1. SHA check against pinned snapshot (if live file changed, leave alone).
    2. Demoted lines appended verbatim to today's log first.
    3. Save backup under .memory-passes/<run_id>/ for undo.
    4. Write candidate file.
    5. Return result stats.
    """
    root = home.root if hasattr(home, "root") else Path(home)
    live_path = root / pinned.rel
    current_text = live_path.read_text(encoding="utf-8", errors="replace") if live_path.is_file() and not live_path.is_symlink() else ""
    current_sha = hashlib.sha256(current_text.encode("utf-8")).hexdigest()

    if current_sha != pinned.sha:
        log.warning("memory reconcile: %s changed meanwhile, leaving alone", pinned.rel)
        return {"applied": False, "reason": "live file changed meanwhile", "rel": pinned.rel}

    # Demoted lines appended verbatim first
    demoted_lines: list[str] = []
    for e in receipt.edits:
        if e.reason == "demote_to_log":
            target = e.resolved_target or ("\n".join(pinned.lines[e.start - 1 : e.end]) if 1 <= e.start <= len(pinned.lines) else "")
            if target:
                demoted_lines.extend(target.splitlines())

    if demoted_lines and log_append is not None:
        block = f"## Demoted {receipt.run_id}\n" + "\n".join(demoted_lines) + "\n"
        log_append(block)

    # Backup for undo
    backup_dir = root / BACKUPS / receipt.run_id
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    candidate_file = Path(receipt.candidate_path)
    candidate_text = candidate_file.read_text(encoding="utf-8") if candidate_file.is_file() else ""
    after_sha = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()[:16]

    index = {
        pinned.rel: {
            "existed": bool(current_text),
            "after": after_sha,
        }
    }
    if current_text:
        bak_file = backup_dir / "files" / pinned.rel
        bak_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        bak_file.write_text(current_text, encoding="utf-8")

    (backup_dir / "pass.json").write_text(
        json.dumps({"id": receipt.run_id, "at": time.time(), "files": index}, ensure_ascii=False),
        encoding="utf-8",
    )

    # Write candidate
    home.write(pinned.rel, candidate_text, by="system")

    filed_claims = [h for e in receipt.edits if e.reason == "promote" for h in e.evidence if h.startswith("claim:")]

    return {
        "applied": True,
        "rel": pinned.rel,
        "run_id": receipt.run_id,
        "edits_applied": len(receipt.edits),
        "filed_claims": filed_claims,
        "demoted_count": len(demoted_lines),
    }


def undo(home: Any, run_id: str) -> dict[str, Any]:
    """Restores the backup made by a reconciliation pass (same format as MemoryUpkeep.undo)."""
    root = home.root if hasattr(home, "root") else Path(home)
    backup_dir = root / BACKUPS / run_id
    pass_file = backup_dir / "pass.json"
    if not pass_file.is_file():
        raise LookupError(f"no backup for run {run_id}")

    index = json.loads(pass_file.read_text(encoding="utf-8"))["files"]
    restored: list[str] = []
    kept: list[str] = []

    for rel, info in index.items():
        p = root / rel
        now = p.read_text(encoding="utf-8", errors="replace") if p.is_file() and not p.is_symlink() else ""
        now_sha = hashlib.sha256(now.encode("utf-8")).hexdigest()[:16]
        if now_sha != info.get("after"):
            kept.append(rel)
            continue
        if info.get("existed"):
            bak_file = backup_dir / "files" / rel
            if bak_file.is_file():
                home.write(rel, bak_file.read_text(encoding="utf-8"), by="system")
        else:
            if p.is_file() and not p.is_symlink():
                p.unlink()
        restored.append(rel)

    return {"restored": restored, "kept": kept}


# -------------------------------------------------------------------------------------------------- reconcile_file
async def _async_call_model(model_call: Any, req: Any) -> str:
    res = model_call(req)
    if asyncio.iscoroutine(res):
        res = await res
    elif hasattr(res, "__aiter__"):
        parts = []
        async for ev in res:
            if hasattr(ev, "text"):
                parts.append(ev.text)
            elif isinstance(ev, str):
                parts.append(ev)
        res = "".join(parts)
    if isinstance(res, dict):
        return json.dumps(res, ensure_ascii=False)
    return str(res or "")


def _sync_call_model(model_call: Any, req: Any) -> str:
    res = model_call(req)
    if isinstance(res, dict):
        return json.dumps(res, ensure_ascii=False)
    return str(res or "")


def reconcile_file(
    pinned: Pinned,
    admitted: list[Claim],
    related: list[Claim],
    paired_text: str,
    model_call: Callable[..., Any],
    resolve_handle: Callable[[str], bool],
    log_append: Callable[[str], None],
    home: Any = None,
    run_id: str | None = None,
    owner_evidence: Callable[[str], bool] = lambda _h: False,
) -> ReconcileStats:
    """Coordinates steps 7-13 for one pinned file. Supports both sync and async model_call. `owner_evidence(handle)`: True for the
    owner's own words (m<id> of an owner message, or a claim made from one); see ground()."""
    if home is None:
        home = _PINNED_HOME.get(id(pinned)) or Home(".")

    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 10**6:06d}"

    # Determine if async coroutine function
    if inspect.iscoroutinefunction(model_call):
        async def _run_async() -> ReconcileStats:
            return await _reconcile_file_core(
                pinned=pinned,
                admitted=admitted,
                related=related,
                paired_text=paired_text,
                call_fn=_async_call_model,
                model_call=model_call,
                resolve_handle=resolve_handle,
                owner_evidence=owner_evidence,
                log_append=log_append,
                home=home,
                run_id=run_id,
            )
        try:
            loop = asyncio.get_running_loop()
            return loop.create_task(_run_async())  # type: ignore[return-value]
        except RuntimeError:
            return asyncio.run(_run_async())

    # Step 7 first call:
    first_req = build_reconcile_request(pinned, admitted, related, paired_text)
    first_res = model_call(first_req)

    if asyncio.iscoroutine(first_res) or hasattr(first_res, "__aiter__"):
        async def _run_from_coro() -> ReconcileStats:
            first_raw = await _async_call_model(lambda _: first_res, None)
            return await _reconcile_file_core(
                pinned=pinned,
                admitted=admitted,
                related=related,
                paired_text=paired_text,
                call_fn=_async_call_model,
                model_call=model_call,
                resolve_handle=resolve_handle,
                owner_evidence=owner_evidence,
                log_append=log_append,
                home=home,
                run_id=run_id,
                initial_raw=first_raw,
            )
        try:
            loop = asyncio.get_running_loop()
            return loop.create_task(_run_from_coro())  # type: ignore[return-value]
        except RuntimeError:
            return asyncio.run(_run_from_coro())

    return _reconcile_file_sync(
        pinned=pinned,
        admitted=admitted,
        related=related,
        paired_text=paired_text,
        model_call=model_call,
        resolve_handle=resolve_handle,
        log_append=log_append,
        home=home,
        run_id=run_id,
        initial_res=first_res,
        owner_evidence=owner_evidence,
    )


def _reconcile_file_sync(
    pinned: Pinned,
    admitted: list[Claim],
    related: list[Claim],
    paired_text: str,
    model_call: Callable[..., Any],
    resolve_handle: Callable[[str], bool],
    log_append: Callable[[str], None],
    home: Any,
    run_id: str,
    initial_res: Any = None,
    owner_evidence: Callable[[str], bool] = lambda _h: False,
) -> ReconcileStats:
    # 1. Step 7: Reconcile request + parse
    if initial_res is not None:
        raw = _sync_call_model(lambda _: initial_res, None)
    else:
        req = build_reconcile_request(pinned, admitted, related, paired_text)
        raw = _sync_call_model(model_call, req)
    parsed = parse_reconcile_response(raw)

    if isinstance(parsed, str) or parsed is None or not parsed:   # "no_change" or the model's own words for it
        return ReconcileStats({
            "status": "no_change",
            "applied": False,
            "edits_proposed": 0,
            "edits_grounded": 0,
            "edits_withheld": 0,
            "edits_applied": 0,
            "touched_bytes": 0,
            "kept_bytes": pinned.size,
        })

    edits: list[Edit] = parsed

    # 2. Step 8: Ground
    grounded, salvage = ground(pinned, edits, resolve_handle, owner_evidence)
    if not grounded:
        return ReconcileStats({
            "status": "no_grounded_edits",
            "applied": False,
            "salvage": salvage,
            "edits_proposed": len(edits),
            "edits_grounded": 0,
            "edits_withheld": 0,
            "edits_applied": 0,
            "touched_bytes": 0,
            "kept_bytes": pinned.size,
        })

    # 3. Step 9: Stage
    try:
        receipt = stage(home, run_id, pinned, grounded, salvage=salvage)
    except RefusedError as e:
        return ReconcileStats({
            "status": "refused",
            "applied": False,
            "reason": str(e),
            "salvage": salvage,
            "edits_proposed": len(edits),
            "edits_grounded": len(grounded),
            "edits_withheld": 0,
            "edits_applied": 0,
            "touched_bytes": 0,
            "kept_bytes": pinned.size,
        })

    before_text = Path(receipt.before_path).read_text(encoding="utf-8")
    candidate_text = Path(receipt.candidate_path).read_text(encoding="utf-8")

    # 4. Step 10: Review
    review_req = build_review_request(receipt, before_text, candidate_text)
    review_raw = _sync_call_model(model_call, review_req)
    verdicts = parse_review_response(review_raw)
    blocking = [v for v in verdicts if v.blocking]

    withheld: list[str] = []
    if blocking:
        # Step 12: prune_and_restage
        new_receipt, withheld = prune_and_restage(home, receipt, pinned, blocking)
        if new_receipt is None or not new_receipt.edits:
            return ReconcileStats({
                "status": "blocked",
                "applied": False,
                "reason": "review blocked",
                "verdicts": verdicts,
                "salvage": receipt.salvage,
                "withheld": withheld,
                "edits_proposed": len(edits),
                "edits_grounded": len(receipt.edits),
                "edits_withheld": len(withheld),
                "edits_applied": 0,
                "touched_bytes": receipt.touched_bytes,
                "kept_bytes": receipt.kept_bytes,
            })
        receipt = new_receipt
        before_text = Path(receipt.before_path).read_text(encoding="utf-8")
        candidate_text = Path(receipt.candidate_path).read_text(encoding="utf-8")

        review_req2 = build_review_request(receipt, before_text, candidate_text)
        review_raw2 = _sync_call_model(model_call, review_req2)
        verdicts2 = parse_review_response(review_raw2)
        if any(v.blocking for v in verdicts2):
            return ReconcileStats({
                "status": "blocked",
                "applied": False,
                "reason": "second review blocked",
                "verdicts": verdicts2,
                "salvage": receipt.salvage,
                "withheld": withheld,
                "edits_proposed": len(edits),
                "edits_grounded": len(receipt.edits),
                "edits_withheld": len(withheld),
                "edits_applied": 0,
                "touched_bytes": receipt.touched_bytes,
                "kept_bytes": receipt.kept_bytes,
            })

        if receipt.broad:
            p2_req = build_preservation_second_request(receipt, before_text, candidate_text)
            p2_raw = _sync_call_model(model_call, p2_req)
            p2_v = parse_preservation_second_response(p2_raw)
            if p2_v.blocking:
                return ReconcileStats({
                    "status": "blocked",
                    "applied": False,
                    "reason": "second preservation blocked",
                    "verdicts": [p2_v],
                    "salvage": receipt.salvage,
                    "withheld": withheld,
                    "edits_proposed": len(edits),
                    "edits_grounded": len(receipt.edits),
                    "edits_withheld": len(withheld),
                    "edits_applied": 0,
                    "touched_bytes": receipt.touched_bytes,
                    "kept_bytes": receipt.kept_bytes,
                })
    else:
        # Step 11: second preservation request when broad
        if receipt.broad:
            p2_req = build_preservation_second_request(receipt, before_text, candidate_text)
            p2_raw = _sync_call_model(model_call, p2_req)
            p2_v = parse_preservation_second_response(p2_raw)
            if p2_v.blocking:
                return ReconcileStats({
                    "status": "blocked",
                    "applied": False,
                    "reason": "second preservation blocked",
                    "verdicts": [p2_v],
                    "salvage": receipt.salvage,
                    "withheld": withheld,
                    "edits_proposed": len(edits),
                    "edits_grounded": len(receipt.edits),
                    "edits_withheld": 0,
                    "edits_applied": 0,
                    "touched_bytes": receipt.touched_bytes,
                    "kept_bytes": receipt.kept_bytes,
                })

    # 5. Step 13: Apply
    applied_info = apply(home, receipt, pinned, log_append)
    return ReconcileStats({
        "status": "applied" if applied_info.get("applied") else "left_alone",
        "applied": bool(applied_info.get("applied")),
        "receipt": receipt,
        "salvage": receipt.salvage,
        "withheld": withheld,
        "edits_proposed": len(edits),
        "edits_grounded": len(receipt.edits),
        "edits_withheld": len(withheld),
        "edits_applied": applied_info.get("edits_applied", 0),
        "filed_claims": applied_info.get("filed_claims", []),
        "touched_bytes": receipt.touched_bytes,
        "kept_bytes": receipt.kept_bytes,
        **applied_info,
    })


async def _reconcile_file_core(
    pinned: Pinned,
    admitted: list[Claim],
    related: list[Claim],
    paired_text: str,
    call_fn: Callable[[Any, Any], Any],
    model_call: Any,
    resolve_handle: Callable[[str], bool],
    log_append: Callable[[str], None],
    home: Any,
    run_id: str,
    initial_raw: str | None = None,
    owner_evidence: Callable[[str], bool] = lambda _h: False,
) -> ReconcileStats:
    # 1. Step 7: Reconcile request + parse
    if initial_raw is not None:
        raw = initial_raw
    else:
        req = build_reconcile_request(pinned, admitted, related, paired_text)
        raw = await call_fn(model_call, req)
    parsed = parse_reconcile_response(raw)

    if isinstance(parsed, str) or parsed is None or not parsed:   # "no_change" or the model's own words for it
        return ReconcileStats({
            "status": "no_change",
            "applied": False,
            "edits_proposed": 0,
            "edits_grounded": 0,
            "edits_withheld": 0,
            "edits_applied": 0,
            "touched_bytes": 0,
            "kept_bytes": pinned.size,
        })

    edits: list[Edit] = parsed

    # 2. Step 8: Ground
    grounded, salvage = ground(pinned, edits, resolve_handle, owner_evidence)
    if not grounded:
        return ReconcileStats({
            "status": "no_grounded_edits",
            "applied": False,
            "salvage": salvage,
            "edits_proposed": len(edits),
            "edits_grounded": 0,
            "edits_withheld": 0,
            "edits_applied": 0,
            "touched_bytes": 0,
            "kept_bytes": pinned.size,
        })

    # 3. Step 9: Stage
    try:
        receipt = stage(home, run_id, pinned, grounded, salvage=salvage)
    except RefusedError as e:
        return ReconcileStats({
            "status": "refused",
            "applied": False,
            "reason": str(e),
            "salvage": salvage,
            "edits_proposed": len(edits),
            "edits_grounded": len(grounded),
            "edits_withheld": 0,
            "edits_applied": 0,
            "touched_bytes": 0,
            "kept_bytes": pinned.size,
        })

    before_text = Path(receipt.before_path).read_text(encoding="utf-8")
    candidate_text = Path(receipt.candidate_path).read_text(encoding="utf-8")

    # 4. Step 10: Review
    review_req = build_review_request(receipt, before_text, candidate_text)
    review_raw = await call_fn(model_call, review_req)
    verdicts = parse_review_response(review_raw)
    blocking = [v for v in verdicts if v.blocking]

    withheld: list[str] = []
    if blocking:
        # Step 12: prune_and_restage
        new_receipt, withheld = prune_and_restage(home, receipt, pinned, blocking)
        if new_receipt is None or not new_receipt.edits:
            return ReconcileStats({
                "status": "blocked",
                "applied": False,
                "reason": "review blocked",
                "verdicts": verdicts,
                "salvage": receipt.salvage,
                "withheld": withheld,
                "edits_proposed": len(edits),
                "edits_grounded": len(receipt.edits),
                "edits_withheld": len(withheld),
                "edits_applied": 0,
                "touched_bytes": receipt.touched_bytes,
                "kept_bytes": receipt.kept_bytes,
            })
        receipt = new_receipt
        before_text = Path(receipt.before_path).read_text(encoding="utf-8")
        candidate_text = Path(receipt.candidate_path).read_text(encoding="utf-8")

        review_req2 = build_review_request(receipt, before_text, candidate_text)
        review_raw2 = await call_fn(model_call, review_req2)
        verdicts2 = parse_review_response(review_raw2)
        if any(v.blocking for v in verdicts2):
            return ReconcileStats({
                "status": "blocked",
                "applied": False,
                "reason": "second review blocked",
                "verdicts": verdicts2,
                "salvage": receipt.salvage,
                "withheld": withheld,
                "edits_proposed": len(edits),
                "edits_grounded": len(receipt.edits),
                "edits_withheld": len(withheld),
                "edits_applied": 0,
                "touched_bytes": receipt.touched_bytes,
                "kept_bytes": receipt.kept_bytes,
            })

        if receipt.broad:
            p2_req = build_preservation_second_request(receipt, before_text, candidate_text)
            p2_raw = await call_fn(model_call, p2_req)
            p2_v = parse_preservation_second_response(p2_raw)
            if p2_v.blocking:
                return ReconcileStats({
                    "status": "blocked",
                    "applied": False,
                    "reason": "second preservation blocked",
                    "verdicts": [p2_v],
                    "salvage": receipt.salvage,
                    "withheld": withheld,
                    "edits_proposed": len(edits),
                    "edits_grounded": len(receipt.edits),
                    "edits_withheld": len(withheld),
                    "edits_applied": 0,
                    "touched_bytes": receipt.touched_bytes,
                    "kept_bytes": receipt.kept_bytes,
                })
    else:
        # Step 11: second preservation request when broad
        if receipt.broad:
            p2_req = build_preservation_second_request(receipt, before_text, candidate_text)
            p2_raw = await call_fn(model_call, p2_req)
            p2_v = parse_preservation_second_response(p2_raw)
            if p2_v.blocking:
                return ReconcileStats({
                    "status": "blocked",
                    "applied": False,
                    "reason": "second preservation blocked",
                    "verdicts": [p2_v],
                    "salvage": receipt.salvage,
                    "withheld": withheld,
                    "edits_proposed": len(edits),
                    "edits_grounded": len(receipt.edits),
                    "edits_withheld": 0,
                    "edits_applied": 0,
                    "touched_bytes": receipt.touched_bytes,
                    "kept_bytes": receipt.kept_bytes,
                })

    # 5. Step 13: Apply
    applied_info = apply(home, receipt, pinned, log_append)
    return ReconcileStats({
        "status": "applied" if applied_info.get("applied") else "left_alone",
        "applied": bool(applied_info.get("applied")),
        "receipt": receipt,
        "salvage": receipt.salvage,
        "withheld": withheld,
        "edits_proposed": len(edits),
        "edits_grounded": len(receipt.edits),
        "edits_withheld": len(withheld),
        "edits_applied": applied_info.get("edits_applied", 0),
        "filed_claims": applied_info.get("filed_claims", []),
        "touched_bytes": receipt.touched_bytes,
        "kept_bytes": receipt.kept_bytes,
        **applied_info,
    })
