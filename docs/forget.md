# Forget on request (design, 2026-11-16)

Owner decision (board card, 2026-11-16): when the owner asks Kelo to forget something, find every place the topic lives, show the list,
ask ONE confirmation, delete, and never write it again. Logic is taken over from the studied reference agent; every name, table, tool
and text is Kelo's own. Evidence (private work notes, the schedules/forget evidence file): C1 rules, C2 tools and handoff, C3
inventory, D3 where Kelo stood.

## Logic taken over (section ids)
- C1: forgetting is cleanup across the whole assistant, not an edit of one note; plan first, one clear confirmation, silence or a changed
  scope is no confirmation; irreversible removal said before the approval; speak in everyday categories (memory, reminders, created
  items, logs, backups), never table or file names; refer to the subject as "that information" and never copy it into a new memory,
  note, name or report; preserve unrelated content and platform bookkeeping; never bulk-truncate; say "forgotten" only after a fresh
  check finds nothing left; name what remains (outside services, other people's copies, backups); a separate approval for anything
  outside (mail, calendar, shared items).
- C2: a planner that only reads (`forget_plan`) and an executor that runs after approval (`forget_confirm`) that revalidates the plan
  against the current state; a context fence so the cleanup is not undone by the next compaction or memory flush.
- C3: the inventory of places (conversation and its projections, memory files and dated logs, derived indexes, claims with their
  supersession chain, future producers: schedules, follow-ups, watchers; skills and artifacts; older copies; operational copies) and
  the list of what cannot be erased.
- D3: Kelo had nothing; nearest parts were `memory_upkeep` backups (which themselves hold the text), `scrub` (secrets only), schedule
  erase, chat clear.

Where Kelo differs on purpose: the one confirmation is Kelo's approval card (the owner taps yes; it is never remembered as a rule:
`forget_` is in `activity.NEVER_REMEMBER`), not a message in the chat. The visible chat is NOT a cleanup target (C1/C3 section 1;
review 2026-11-16): messages and their attachments stay, and it is never reported as a limitation. `forget_plan` still searches the
messages, but only to reach what they produced (claims citing them by handle m<id>, log lines, tasks, schedules...); the rolling
summary is a derived copy and is cleaned.

## Files
- `server/src/assistant/forget.py`: `Tombstones`, `Forget` (plan, confirm), the tools `forget_plan` and `forget_confirm`.
- `server/src/assistant/api/forget.py`: `GET /api/forget`, `DELETE /api/forget/{id}`, `GET /api/forget/plans/{id}`.
- Prompts: `server/content/prompts/forget/{select,screen}.md`; a paragraph in `memory/rules.md` and `memory/reconcile.md`.
- Hooks: `memory_claims.run_claims(tombstones=)`, `memory_upkeep.MemoryUpkeep(tombstones=)`, `prompt.PromptBuilder.forgotten`.

## The flow
1. The owner asks to forget something (not "forget it" meaning cancel). The agent calls `forget_plan(subject, terms)`: `subject` a short
   neutral description, `terms` the words, names and spellings (any language) the copies may contain. The terms are only a SEARCH (like
   grep); they decide nothing by themselves.
2. Candidates: every place in the table below is searched with the terms (case and accent folded). More than 400 hits = too broad, the
   agent asks for narrower terms.
3. Judge: the owner's main model (one model for everything, `ModelJson.ask`, batches of 40) picks which candidates are really about the
   subject (a candidate that only shares a word is dropped; unsure = not picked). No answer = no plan (fail closed).
4. Derived items, by STRUCTURE: claims cited from a removed message (`handle` m<id>), claims linked to a removed claim by `supersedes`
   in either direction (repeated to a fixpoint), and the dated log lines `- claim:<id>` of every removed claim.
5. The plan is stored (`forget_plans`: label, terms, items with locator + guard hash + a 60-character preview; open for one hour). The
   tool answers the agent with counts per everyday category and tells it to call `forget_confirm` next; it never repeats the subject.
6. `forget_confirm(plan_id)` is `requires_approval`: the card says "Forget for good: “subject”. This cannot be undone, and Kelo will not
   write it down again. Found: Chat messages: 3; Memory: 2; ... Copies kept elsewhere (backups you made, other services, other
   people) are not touched." The app can show the list of the plan with `GET /api/forget/plans/{id}`.
7. After the yes: each item is re-checked (guard hash; changed since the plan = left alone and reported), then removed in this order:
   schedules (the producers), table rows, the rolling summary, claims, memory file lines. A row that holds other content too (the judge
   marks it `mixed`) is EDITED, not deleted (C1: edit a mixed-content file): the main model rewrites each text column without the
   subject's part (`rewrite.md`), a row left empty goes, a failed rewrite leaves the row alone and reports it. Structured rows (reply log,
   data-trail labels) are only deleted whole.
   Then: older copies, the recall index refresh, the memory pass wait list, the card's own approval text, the reply log rows that carry
   the subject, the tombstone, the plan row. Verification: every planned item is looked up again; the answer says how many are left.
8. The agent says it in one line, in categories, names what stays (outside copies, the owner's own uploaded files), and refers to the
   subject only as "that information".

## Places (inventory)
| Place | Search | Action | Category |
|---|---|---|---|
| chat messages | content | searched only; they and their attachments stay | - |
| rolling summary | summary | emptied (rebuilt later from what is left) | memory |
| memory claims | text, quote | delete rows (the FTS follows by triggers) | memory |
| MEMORY.md, user.md, AGENTS.md, assistant.md, people pages, notes, inbox, other agent files | line | take the picked lines out; a non-core file with nothing else left is deleted | memory / notes |
| dated memory log | line | take lines out (the claim lines of removed claims by structure) | history |
| older saved versions (`.history`, `.memory-passes`, `.trash`, `.backup`) | exact removed text | a version holding a removed line is deleted; a whole pass backup goes (that pass can no longer be undone) | not counted |
| schedules and archived ones | name, prompt | `Schedules.erase` (removes grants and run history) | reminders |
| watchers, follow-ups, goals (with steps, facts, log, docs, metrics), offers, goal suggestions | their text columns | delete rows | reminders |
| learned preference rules | rule, evidence | delete rows | memory |
| tasks, approval log, notifications, journal, proactive ledger, schedule runs, reply log, data-trail labels | their text columns | delete rows | history |
| skills Kelo wrote | skill text | `Registry.remove_custom` | skills |
| recall index (in memory) | - | refreshed (files by modified time, journal by count) | - |

The store runs with `PRAGMA secure_delete = ON`, so a deleted row is overwritten in the file.
Never touched: the owner's uploaded files and library, outside services (mail, calendar, accounts), other people's copies, the
owner's own backups, shared or published items. The agent says so; a request to forget does not authorise changing those.

## Never again: tombstones
Table `forget_tombstones` (id, label, terms, created_at, removed counts). It is written when a forget runs, even when some items were
left alone. It is stored only in the database, never in a memory file, and never shown to a normal chat request except as the label.
Who respects it:
- Extract (`run_claims`): the request carries a fenced `<forgotten_topics>` block and a rule in `memory/rules.md`. Every candidate
  claim that passed the shape check is screened by the main model (`Tombstones.screener`, `screen.md`); flagged claims are dropped.
  The screen failing = nothing is stored this time (`error: screen_failed`, the window is read again later), like the verify step.
- Reconcile (`MemoryUpkeep._run`): the block is appended to every reconcile and review request, and after a file is written the lines
  the pass ADDED are screened; those about a forgotten topic are taken out again. A failed screen puts the file back as it was.
- The agent's prompt (`PromptBuilder.forgotten`): a short section naming the labels: never save, research, schedule or bring up.
- No cost when nothing is forgotten (no block, no screen request).
The owner can lift it: `DELETE /api/forget/{id}` (apps: Settings, "Allow again").

## Compaction
`Compactor.forgotten` (set to `Tombstones.block` by the server): the summary request and the memory step before a compaction get the same
`<forgotten_topics>` block plus "Do not make any reference to the forgotten topics" (C2 phase text), so a new summary never restates a
forgotten topic even while its messages are still in the chat.

## Decisions
- The terms are the model's search aid per request, never a stored word list that decides behaviour. Decisions are the main model's
  judgement over the candidates and over new text (docs/no-word-lists.md principle 3); structure (handles, supersession, guards) decides
  the derived items.
- The tombstone keeps the label and the terms (needed for the screening request); it is not a memory file and the owner can read the
  label and delete it.
- A plan lives one hour; a changed item is left alone and reported (the guard hash), never deleted blindly.
- The chat is never edited, so the owner's history stays whole; claims that cite a message by handle go with their source.
- The final answer of the agent is itself a chat message; the tool result tells it to say "that information". If it names the subject
  anyway the memory pass still screens it against the tombstone.

## Tests (`server/tests/test_forget.py`, fake model)
Plan finds claims, memory lines, schedule, goal, skill; the judge drops a look-alike; derived claims and log lines follow;
no model = no plan; confirm deletes and verifies; changed item is left alone; older copies purged; tombstone written; card text;
extract with a tombstone drops a flagged claim and a failing screen stores nothing; the pass takes added lines out; the agent prompt
notice; the API; `forget_` never remembered.

## Not done here / app work
- Apps (Android, iOS, desktop): a "Forgotten topics" list in Settings with "Allow again" (`GET/DELETE /api/forget`), and optionally the
  plan list on the card (`GET /api/forget/plans/{id}`).
- Phone data synced from the phone (notes, call log) and connector caches (`connector_data.py`) are outside this run: the answer names
  them as not touched. A source-scoped exclusion for a connected account that could bring the topic back is not built.
