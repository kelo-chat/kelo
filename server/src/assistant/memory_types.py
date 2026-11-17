"""Shared shapes of the memory pass pipeline (docs/memory-pipeline.md): the window a run reads, the claims it extracts and verifies,
the pinned file it reconciles, the edit plan, the staged receipt and the review verdicts. memory_claims.py (steps 1-5) and
memory_reconcile.py (steps 6-13) both import these; memory_upkeep.py drives the run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CLAIM_KINDS = ("fact", "preference", "boundary", "event", "commitment", "correction", "person", "lesson", "observation")
SALIENCE = ("high", "medium", "low")
SPEAKERS = ("user", "assistant", "tool", "background")
EDIT_OPS = ("replace", "remove", "insert_after")
EDIT_REASONS = ("promote", "correct", "deduplicate", "condense", "retire_expired", "demote_to_log", "reframe_completed",
                "mark_undated", "mark_unresolved")
HANDLE_FREE_REASONS = frozenset({"deduplicate", "condense", "retire_expired", "demote_to_log", "mark_undated"})
LENSES = ("evidence", "temporal", "completeness", "preservation")
MARK_UNDATED = "(as of an unknown date)"
MARK_UNRESOLVED = "(unconfirmed, other sources disagree)"
LOG_DIR = "memory/log"


@dataclass(slots=True)
class Window:
    run_id: str
    as_of: str                       # ISO local time of the run clock in the owner's timezone
    since: float
    messages: list[dict[str, Any]]   # {"id", "role", "text", "created_at"}
    tasks: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    inbox: list[str] = field(default_factory=list)
    log_path: str = ""               # memory/log/YYYY-MM-DD.md
    has_new: bool = False

    def source(self, handle: str) -> tuple[str, str] | None:
        """(role, text) of a handle inside this window: m<id>, task:<id>, approval:<id>, inbox:<sha8>; None when outside it.
        Steps 4 (quote check) and 5 (fence) use it."""
        import hashlib
        kind, _, key = handle.partition(":")
        if handle.startswith("m") and handle[1:].isdigit():
            for m in self.messages:
                if str(m.get("id")) == handle[1:]:
                    return str(m.get("role") or ""), str(m.get("text") or "")
            return None
        rows = {"task": self.tasks, "approval": self.approvals}.get(kind)
        if rows is not None:
            for r in rows:
                if str(r.get("id")) == key:
                    return "tool", str(r.get("text") or r.get("summary") or r.get("title") or "")
            return None
        if kind == "inbox":
            for line in self.inbox:
                if hashlib.sha256(line.encode()).hexdigest()[:8] == key:
                    return "user", line
        return None


@dataclass(slots=True)
class Claim:
    id: str
    kind: str
    salience: str
    text: str
    quote: str
    speaker: str
    handle: str
    valid_until: str | None = None
    supersedes: str | None = None
    secret: bool = False   # the extracting model's own judgement; a claim from model JSON is a secret unless it says exactly false


@dataclass(slots=True)
class ClaimVerdict:
    claim_id: str
    ok: bool
    lens: Literal["shape", "refute", "evidence"]
    reason: str = ""


@dataclass(slots=True)
class Pinned:
    rel: str
    lines: list[str]
    sha: str
    as_of: str
    cap: int
    size: int
    over_budget: bool

    @property
    def numbered(self) -> str:
        return "\n".join(f"{i:>4}| {line}" for i, line in enumerate(self.lines, 1))


@dataclass(slots=True)
class Edit:
    handle: str                      # "edit:N", N from 1 in plan order
    op: str
    start: int
    end: int
    guard: str
    reason: str
    replacement: str = ""
    evidence: list[str] = field(default_factory=list)
    origin: dict[str, str] | None = None
    resolved_target: str = ""        # the snapshot lines start..end, filled by grounding (step 8)


@dataclass(slots=True)
class Receipt:
    run_id: str
    target: str
    before_path: str
    candidate_path: str
    as_of: str
    cap: int
    over_budget: bool
    edits: list[Edit]
    salvage: list[str] = field(default_factory=list)
    touched_bytes: int = 0
    kept_bytes: int = 0
    withheld: list[str] = field(default_factory=list)   # edit handles withheld by a first review (step 12)

    @property
    def broad(self) -> bool:
        return self.touched_bytes >= self.kept_bytes


@dataclass(slots=True)
class LensVerdict:
    lens: str
    verdict: Literal["pass", "fail", "hold"]
    reason: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.verdict != "pass"

    def edits(self) -> list[str]:
        return [h for h in self.evidence if h.startswith("edit:")]
