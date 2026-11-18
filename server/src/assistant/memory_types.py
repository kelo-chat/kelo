"""Shared shapes of the memory pass pipeline (docs/memory-pipeline.md): the window a run reads, the claims it extracts and verifies,
the pinned file it reconciles, the edit plan, the staged receipt and the review verdicts. memory_claims.py (steps 1-5) and
memory_reconcile.py (steps 6-13) both import these; memory_upkeep.py drives the run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CLAIM_KINDS = ("fact", "preference", "boundary", "event", "commitment", "correction", "person", "lesson")
SALIENCE = ("high", "medium", "low")
SPEAKERS = ("user", "assistant", "tool", "background")   # parsed as given; only "user" is admitted (memory_claims.check_shape)
EDIT_OPS = ("replace", "remove", "insert_after")
EDIT_REASONS = ("promote", "correct", "deduplicate", "condense", "retire_expired", "demote_to_log", "reframe_completed",
                "mark_undated", "mark_unresolved")
HANDLE_FREE_REASONS = frozenset({"deduplicate", "condense", "retire_expired", "demote_to_log", "mark_undated"})
CONTENT_REASONS = frozenset({"promote", "correct", "reframe_completed"})   # new words in a file: need the owner's own words as evidence
LENSES = ("evidence", "temporal", "completeness", "preservation")
MARK_UNDATED = "(as of an unknown date)"
MARK_UNRESOLVED = "(unconfirmed, other sources disagree)"
LOG_DIR = "memory/log"


@dataclass(slots=True)
class Window:
    """What one pass reads: only the owner's own messages (owner_messages.py), and the owner's inbox lines that name one of them.
    Nothing a background job, a tool, a page, a mail or Kelo itself wrote is in it."""
    run_id: str
    as_of: str                       # ISO local time of the run clock in the owner's timezone
    since: float
    messages: list[dict[str, Any]]   # the owner's messages: {"id", "role", "text", "created_at"}
    inbox: list[str] = field(default_factory=list)   # the owner's inbox lines (`m<id>` quotes), newest first
    log_path: str = ""               # memory/log/YYYY-MM-DD.md
    has_new: bool = False

    def source(self, handle: str) -> tuple[str, str] | None:
        """(role, text) of a handle inside this window: only m<id> of one of the owner's messages, role "user". None for anything
        else (a task, an approval, an inbox line, a message outside the window). Steps 4 (quote check) and 5 (fence) use it."""
        if handle.startswith("m") and handle[1:].isdigit():
            for m in self.messages:
                if str(m.get("id")) == handle[1:]:
                    return "user", str(m.get("text") or "")
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
