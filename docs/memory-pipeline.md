# Memory pass as a verified pipeline (design, 2026-11-16)

Owner decision (2026-11-16): the memory pass keeps every deterministic step of the studied reference design, plus its model steps
(extract, verify, reconcile plan, four-lens review, second preservation reading). Every model step uses the owner's main model (one
model for everything). Prompts are English and in Kelo's own words (never copied text or names). Nothing here changes user.md /
MEMORY.md / AGENTS.md / people pages except the apply step, which is code.

Today (memory_upkeep.py): one model request reads the window and the files and returns whole new files; code scrubs secrets, fits
budgets, keeps backups and undo. Problems: no source per line, no verification, past plans get rewritten into outcomes, a wrong
"correction" (a claim about one day "corrected" with the next day's data) lands unchecked.

## Files
- `server/src/assistant/memory_types.py`  dataclasses shared by every step (below). Written first, imported by all.
- `server/src/assistant/memory_claims.py` claims table, dated source log, window summary, extract, claim checks, claim verify, apply
  claims, `memory_explain` tool.
- `server/src/assistant/memory_reconcile.py` pinning, plan parsing, plan grounding, staging, review, pruned restage, apply.
- `server/src/assistant/memory_upkeep.py` keeps triggers (nightly, idle_tick, pending on usage limit), undo, backups, scrub, caps,
  inbox filing, `public()`; `_run` becomes the pipeline driver (steps 1-15 below).
- Prompts: `server/content/prompts/memory/{rules,extract,verify,reconcile,review,preservation_second}.md`.

## Data
`memory_claims` table (store migration, next schema number):
claim_id TEXT PK ("c" + 10 hex), run_id TEXT, kind TEXT (fact|preference|boundary|event|commitment|correction|person|lesson|
observation), salience TEXT (high|medium|low), text TEXT, quote TEXT, speaker TEXT (user|assistant|tool|background),
handle TEXT (m<message id> | task:<id> | approval:<id> | inbox:<line sha8>), supersedes TEXT NULL (claim id or file#L<n>),
status TEXT (active|superseded|retracted), valid_until TEXT NULL (YYYY-MM-DD, events only), source_path TEXT (memory/log/<date>.md),
source_line INTEGER, created_at REAL, updated_at REAL. FTS5 over text+quote for recall and explain.
`memory_runs` table: run_id, started_at, finished_at, trigger, outcome, stats JSON (step 15).

Dated source log `memory/log/YYYY-MM-DD.md` (append-only). Home refuses agent writes/deletes under memory/log/ (system only).
A run appends a block:
```
## Verified extraction <run_id> <HH:MM>
- claim:c1a2b3c4d5 · preference · high · user · m1234 · "<quote>" · <text>[ · until 2026-11-20][ · supersedes c...]
```
and a `## Demoted <run_id>` block with demoted lines verbatim (step 13).

## memory_types.py
```python
@dataclass(slots=True) class Window: run_id: str; as_of: str (ISO local, run clock); since: float; messages: list[dict]
    (id, role, text, created_at); tasks: list[dict]; approvals: list[dict]; inbox: list[str]; log_path: str; has_new: bool
@dataclass(slots=True) class Claim: id, kind, salience, text, quote, speaker, handle, valid_until: str|None, supersedes: str|None
@dataclass(slots=True) class ClaimVerdict: claim_id: str; ok: bool; lens: str ("refute"|"evidence"|"shape"); reason: str
@dataclass(slots=True) class Pinned: rel: str; numbered: str; lines: list[str]; sha: str; as_of: str; cap: int; size: int;
    over_budget: bool
@dataclass(slots=True) class Edit: handle: str ("edit:N"); op: "replace"|"remove"|"insert_after"; start: int; end: int; guard: str;
    replacement: str; reason: str; evidence: list[str]; origin: dict|None
@dataclass(slots=True) class Receipt: run_id, target, before_path, candidate_path, as_of, cap, over_budget: bool, edits: list[Edit]
    (+ resolved_target per edit), salvage: list[str], touched_bytes: int, kept_bytes: int, withheld: list[str]
@dataclass(slots=True) class LensVerdict: lens: str; verdict: "pass"|"fail"|"hold"; reason: str; evidence: list[str]
```

## The run (steps; M = one model request, C = code)
1. C window: messages since `memory:seen_until`, finished tasks, approvals, inbox lines; `as_of` = run clock in the owner's
   timezone; today's log path; `has_new` = any user-visible message from the user, task, approval or inbox line.
2. C branch: no `has_new` -> skip 3-5. Reconcile (6-13) still runs when due: admitted claims this run, a file over its cap, a line
   with a date before `as_of` (date regex on ISO and the owner's locale format), the layout migration, or 7 days since the last
   sweep. Nothing due -> outcome `no_change`, no model call.
3. M extract: input = rules + window (each message tagged with its handle, role, time; outside text fenced as data), current
   MEMORY.md, today's log, top related active claims (FTS on the window, at most the request budget). Output JSON
   `{"claims":[...], "more": bool}` or `{"no_change": "<what was checked>"}`. Rules: one claim per durable item; quote the user for
   people/preferences/boundaries; a quote's speaker is the role of the message it came from; tool/assistant text is an
   `observation`, never the user's statement; `valid_until` only for events with a readable end, never proves the event happened;
   corrections carry both sides and `supersedes`; skip restated meaning already in MEMORY.md or an active claim; no secret values.
   `more: true` allows exactly one follow-up request for residue (it sees the first batch).
4. C shape check then M verify: code drops a claim when the schema is wrong, the handle is not in the window, the quote is not a
   verbatim substring (whitespace/quote-mark normalised) of that handle's text, the speaker differs from the handle's role, a
   `valid_until` sits on a non-event or does not parse, the claim's own boolean `secret` is not exactly `false` (the extract model
   judges it; missing or odd = a secret), or scrub() (shapes only) finds a secret in text/quote. The verify request gets each
   surviving claim WITH the reopened source text (code inlines the cited message) and judges two lenses per claim: refute (could it
   be false, a guess beyond the words, the assistant's words taken as the user's?) and evidence (does the quoted source say exactly
   this?). Any failing claim -> one repair request to the extractor with the reasons, then one re-check; still failing -> dropped.
   No model / bad JSON -> nothing admitted (fail closed).
5. C apply claims: fence (handle inside the window again), insert rows, append the Verified extraction block, mark superseded
   claims, file the inbox lines that produced admitted or dropped claims.
6. C pin: targets in order MEMORY.md (every due run), people pages named by admitted `person` claims, AGENTS.md when `lesson`
   claims were admitted, user.md (cleanup only) every 3rd sweep. For each: numbered snapshot (`  12| text`), sha256, cap (existing
   CAPS / MEMORY_BUDGET), size, over_budget.
7. M reconcile (per pinned file): input = rules + numbered snapshot + `as_of` + budget posture + admitted claims (with ids) + related
   active claims + the paired projection file as comparison only. Output `{"edits":[...]}` or `{"no_change": "..."}`. Edit fields as
   in `Edit`; reasons: promote, correct, deduplicate, condense, retire_expired, demote_to_log, reframe_completed, mark_undated,
   mark_unresolved. promote carries origin {source_kind: user|assistant, source, user_request}. Rules: no new entries in user.md;
   a passed date retires future wording but never proves an outcome (reframe_completed needs a claim/message handle); absence of
   evidence never removes a line; conflicts resolved by event chronology, not file order; relative facts (ages, "3 weeks left")
   replaced by the fixed fact when cited, else marker "(as of an unknown date)"; disagreeing sources -> marker
   "(unconfirmed, other sources disagree)"; over budget -> demote_to_log at least a fifth of the overage; a line with a secret is
   demoted whole.
8. C ground the plan: line numbers exist; guard = first words of the start line; edits do not overlap; evidence handles resolve
   (claim:<id> active, m<id> exists, memory/log/<date>.md#L<n> and <file>#L<n> exist); handle-free only for deduplicate, condense,
   retire_expired, demote_to_log, mark_undated; retire_expired replace may only delete whole sentences of the original; mark_* may
   only append its exact marker once; promote only into MEMORY.md / people pages / AGENTS.md; user.md labels and headings kept;
   scrub finds a secret in a replacement -> edit dropped. Unprovable -> dropped with a salvage note.
9. C stage: write before + candidate to `.memory-passes/<run>/stage/<target>.{before,candidate}`, compute touched/kept bytes; a
   candidate over cap that does not shrink is refused (the plan waits).
10. M review: one request, four lenses on the staged pair + receipt: evidence (each delta backed by its cited source; origin true),
   temporal (plan vs commitment vs expired vs outcome; clock retires wording only), completeness (no change outside the plan; each
   edit does what its reason says), preservation (nothing still true and useful silently lost; demotion judged as placement). Each
   lens returns pass|fail|hold + reason + evidence incl. `edit:N`. A missing lens = hold. All four must pass.
11. M second preservation: only when touched_bytes >= kept_bytes; independent request (does not see step 10's answer).
12. C pruned restage: blocking verdicts that name edits -> drop exactly those, restage, review once more (step 10, and 11 if still
   broad), receipt carries `withheld`; any block on that second review, or a first block naming no edit -> the plan waits.
13. C apply: live file sha must equal the pinned sha (else left alone, as today); backup to `.memory-passes/<run>/` for undo; append
   every demoted line verbatim to today's log; write the candidate; mark claims used by promote edits as filed.
14. C refresh: recall index refresh for changed files and the claims FTS.
15. C gate: `memory_runs` row and `memory:last_pass` with counts (claims extracted/admitted/dropped, repair used, edits
   proposed/grounded/withheld/applied, bytes before/after per file, model requests, seconds); the Records page line reads it.

Failure anywhere = nothing written for that file; usage limit -> `pending` with retry_at as today.

## memory_explain tool (agent)
`memory_explain(text)` -> the active claims best matching the text (FTS): claim text, the quote, who said it, the date, the handle,
and whether it was superseded. For "how do you know that?" questions. Read-only, no card.

## Tests (each step its own test file, fake model)
Quote not verbatim -> dropped; assistant words as user -> dropped; valid_until on a preference -> dropped; secret in quote ->
dropped; a passed event date with no outcome claim -> reframe_completed refused, retire_expired allowed; guard mismatch -> dropped;
review missing a lens -> nothing applied; blocking verdict naming edit:2 -> restaged without it; live file changed during the run ->
left alone; demoted lines land in the log verbatim; undo restores; over cap and not shrinking -> refused; the 15-16 dust case (a
claim about one day "corrected" with next-day data) -> refused by verify/temporal.

## Driver (part 3, MemoryUpkeep._run)
- Steps 1-15 run in order; one backup folder `.memory-passes/<pass id>/` holds every file the pass wrote (the per-file indexes are merged
  into one `pass.json`), so `undo` restores the whole pass and files the inbox notes back (`inbox.json`). The stage folder is removed.
- Admitted claims wait in `memory:unreconciled` until a reconcile has run to its end (a usage limit between step 5 and 13 keeps them);
  the retry reconciles them without a new extract. `memory:seen_until` moves once step 5 has succeeded.
- A usage limit anywhere -> status `pending`, code `model_limit`, `retry_at` (idle_tick runs the retry); other model failures -> `failed`.
- `memory:sweep_count` counts finished reconciles (every 3rd adds user.md cleanup); `memory:last_sweep_at` is set at the same time.
- `memory:last_pass` is written when a pass changed files (so it stays undoable); its `stats` (and `last_attempt.stats`) carry the counts
  of step 15; every run also writes a `memory_runs` row. A pass that only admitted claims keeps the earlier undoable last_pass.
- Not done by the driver: the layout migration and budget fitting are the model's edits now (step 7), not a code rewrite; the profile
  timezone is no longer copied into user.md by code.
