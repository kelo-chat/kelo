# Scheduled runs in two phases, and no promise without a way to keep it

Status (2026-11-16): built on the server (`server/src/assistant/scheduled_runs.py`). No app work is needed for it to work; the apps
need nothing new because the chat message and the notification are the same ones a run produced before.

## Owner decisions

- A scheduled run's working part must not be able to message the owner. A separate closing part decides ONE outcome: nothing to tell,
  or tell, with the text.
- The assistant may say it will tell or do something later only when something real exists that will do it (a schedule, a watcher, a
  background job, a re-check). No word, phrase or site lists: the rule is enforced by structure (the turn's tool calls) and by the
  owner's main model reading the reply (`docs/no-word-lists.md`, one model for every purpose).

## What was taken over from the studied reference agent (evidence: the schedules evidence note in the owner's reviews folder, section ids)

- A1, A2: a scheduled worker has an execute phase and a resolve phase in the same run. The execute phase's final message is private
  ("nothing you write here reaches the user"); it must not call or imitate a delivery tool; it cannot converse with or wait for the
  user.
- A3: the resolve phase is one decision, with a strict set of outcomes, and its inputs (the run report, tool output) are evidence, not
  instructions. Code guards the decision: a "nothing to report" is rejected when the execution record has a failed call or an
  unconfirmed step; the model is shown an evidence object (failed calls, calls without results, incompleteness).
- A3 (quiet rules): quiet is allowed only when the work completed, nothing needs the owner, and the body asked to hear only about
  changes or problems (or a refresh asked for no report); a failure or an unfinished step is always told.
- D1 (where Kelo was): no split; a run ended with a report or the literal `NO_REPLY`; the model decided, nothing checked it.
- The promise rule: "Never promise a follow-up that nothing will deliver" (the architecture and non-payment notes, honesty rules) and "a specific promised reminder still belongs in a scheduled job you own" (evidence section B4). The reference only
  states this as a prompt rule. Kelo adds a structural check.

What was NOT taken over: the second engine (event workers), the outbox with boot generations, job-definition versions and the mutation
audit (evidence A5, A6, D1 "absent in Kelo"). They solve problems Kelo does not have yet (several workers, delivery across restarts);
Kelo's existing resume path (`resume.py`) and follow-ups already cover a cut run.

## The two phases

```
scheduler.run_fn  (server.py run_schedule)
  working phase   start_background(..., two_phase=True)       a normal unattended agent turn
                    - tools declared reaches_owner are NOT offered (quick_replies, await_reply, compare_options, react, offer_post)
                    - the end of the turn stores nothing in the chat and sends no notification (turn.defer_report)
                    - turn.report = what it wrote; turn.evidence = what its tool calls did (RunEvidence)
  closing phase   Closer.close(name, instructions, report, evidence)   one short request to the owner's main model (model_json.py)
                    -> {"outcome": "nothing"}  or  {"outcome": "tell", "text": "..."}
```

Outcomes after the closing phase (only for a run that ended `done`):

| decision | chat | notification | task row |
| --- | --- | --- | --- |
| nothing | none | none | "There was nothing to tell", read; run history status `quiet` |
| tell | one message `[Automatic run · name]` + the text | `task_done` | result line = first line of the text, unread; status `done` |

Code guards (structure, not words):

1. A run whose evidence has a failed tool call or a skipped step (a declined or expired approval) can never close as "nothing": the
   outcome becomes "tell", with the model's text if it gave one, else the working report, else the plain "It did not work out" line.
2. A valid answer is strict JSON; anything else (no model, a timeout, an error, a malformed answer) is "no decision" and the report
   decides, exactly as before the two phases: `NO_REPLY` or an empty report = nothing, any other text = tell it.
3. A run that asked to be tried again later (`schedule_retry`) or that did not end `done` (stopped, failed, waiting for the model)
   skips the closing phase. A stopped or failed run keeps today's behaviour: its partial report stays in the chat, a failure notifies.
4. The working report is kept as the task's `answer` (the Work page shows it); the text told to the owner is the chat message.

The prompt of the closing request is English; it asks for the owner's language for the text and says the report, the job
instructions and the evidence are data, never instructions.

### What the working phase may still do

Everything an unattended run did: tools, the browser, connected services under the job's grants, approval cards that go to the phone,
`schedule_pause`, `schedule_retry`, `request_login`-style cards. Those cards are the approval and login system, not chat messages
from the run; they stay (the follow-up reminders already cover an unanswered one, `followups.py`).

The note at the head of the run (`SCHEDULE_NOTE`) now says: you cannot message the user, what you write is a private report, say what
failed or was skipped, say it is nothing when it is nothing (`NO_REPLY` still means that).

## The promise rule

1. Prompt (`server/content/prompts/system.md`, "Never promise what nothing will do"): say you will tell, remind, check or do something
   later only in a reply where you also created the thing that does it; otherwise do it now or say plainly you cannot and offer to set
   it up. No promise to catch something "the moment it happens" (checks run on a schedule).
2. Code, after an attended chat or api reply (`turn_engine.check_promise`, `scheduled_runs.PromiseJudge`):
   - tools declared `keeps_promise` are `schedule_create`, `schedule_update`, `watch_add`, `watch_enable`, `watch_update`,
     `recheck_later`, `delegate`. A reply with a SUCCESSFUL call of one of them (no error, approval not declined) is backed: no check.
   - Otherwise one short request to the owner's main model reads the reply (last 3000 characters) and answers
     `{"promises_later": bool, "what": "..."}`: true only when the assistant says it will itself tell, remind, check, watch or do
     something later or when something happens, without being asked again. Work done in the reply, offers and questions that wait for
     the user's answer, and explanations of what an existing schedule does are false.
   - On true, one plain line is added to the chat (i18n in every language): "I said "{what}", but nothing is set up to do that, so it
     will not happen by itself. Do you want me to set it up?" The owner's yes starts the normal `schedule_create` approval card.
   - Unattended runs, background jobs and hidden handoffs are never checked (they promise nothing to the owner). No valid answer, no
     model or an error says nothing. It runs after the reply ended, so the reply is never delayed.
   - Cost: one extra short request per attended reply without a backing call. `scheduled_runs.PROMISE_CHECK` switches it off (the test
     conftest does, to keep scripted fake models in order).

## Files

- `server/src/assistant/scheduled_runs.py`: `RunEvidence`, `Closer`, `Decision`, `PromiseJudge`, `declare(tools)`, the prompts, `PROMISE_LINE`.
- `server/src/assistant/agent/loop.py`: `Tool.reaches_owner`, `Tool.keeps_promise`.
- `server/src/assistant/turn_engine.py`: `Turn.evidence / defer_report / report`, the tool filter of a scheduled run, `two_phase`, `check_promise`.
- `server/src/assistant/server.py`: `run_schedule` (closing phase), `task_closed` (no notification for a deferred working phase), `SCHEDULE_NOTE`.
- `server/src/assistant/activity.py`: `set_outcome(task_id, text)`.
- `server/tests/test_scheduled_runs.py`.

## Data

No new table or column. The working report stays in `tasks.answer`; the outcome sets `tasks.result` and `tasks.seen`.

## Tests

`tests/test_scheduled_runs.py`: strict parsing; the report fallback; the guard after a failed or skipped step; evidence from events;
nothing / tell through the whole app; the working phase is not offered owner tools; a promise gets one correction; a reply that made
the schedule is not checked. Older tests that read "the last model request of a run" now skip the closing request.

## Not done, and why

- Typed blocked reasons and a dependency field on `schedule_pause` (evidence A3, `connector_not_connected`, `reauth_required`...) and
  re-arming a paused schedule when the connector is connected again: a separate piece; `schedule_pause` keeps its free-text reason.
- A delivery record per run (outbox) and "the result is saved but was not posted" states: not needed while the message is stored in the
  chat at the same moment the run ends.
- Job-definition versions and a mutation audit tying a schedule change to the tool call: not built.
- App work: none required. Optional later: show "nothing to tell" runs differently in the schedule's run history (the data is there:
  status `quiet`).
