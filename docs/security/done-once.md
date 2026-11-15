# A step that sends, changes or deletes is done once

A mail that timed out may still have gone out; a model that simply tries again sends it twice. So Kelo keeps its own record of what it did
outside, looks in it before such a step runs, and says plainly when it does not know whether a step went through. There are no
idempotency keys: the record is Kelo's, and the way to settle a doubt is to look at the outside (the sent folder, a site's order page).

Covered: every step that `effects.effect_of` calls a send, change or delete: mail send and update, phone texts and calls, calendar
create / update / delete, `connector_send` (POST / PUT / PATCH / DELETE), `mcp_call_tool`, Nest entries, and browser clicks or typing with
submit on a site where the step is of the send / form / personal / apply / delete class. Reads are never recorded. **Purchases are not
part of this**: `confirm_purchase`, the "pay" click and the receipt check (`receipt.py`) have their own ledger.

## The order of a step (`agent/loop.py`, one call site)

hold, validate, the permission gate, the approval card, **done once**, the tool. If the owner approved a card but the step repeats one
that already went out, the step is not run and the model is told that the approval does not override it.

## What is recorded (`done_once.py`, table `actions`, 30 days)

`id, ts, run_id, task_id, turn_id, source, tool, kind, connector, targets, fingerprint, state (done | unknown | failed), outside_id,
detail (at most 200 characters), checked_at`. The record is made before the step runs, as `unknown`, so a step that never answered
(a stopped run, a restart) stays unknown. Then the answer decides:

| the answer | state |
| --- | --- |
| a success (with the message, order or entry id when it shows one; a page that shows a confirmation number) | `done` |
| a refusal: HTTP 4xx except 408 / 429, a validation error, a policy refusal, a page that shows a form error or did not change | `failed` |
| a timeout, a dropped connection, HTTP 5xx / 408 / 429, a page that did not settle, anything not clearly a refusal | `unknown` |

The **fingerprint** (sha256) is of the tool, kind, connector, sorted targets and the normalised text: whitespace collapsed, case kept,
volatile fields (request ids, timestamps, headers, a browser ref) dropped. Mail counts by subject, body and attachment names; a browser
step by site, class, page path (no query) and the typed or clicked words; an MCP or service call by its tool, address and arguments.
Two calls that would do the same thing outside give the same fingerprint however the model words its arguments.

## What blocks, and what does not (within 24 hours of the same fingerprint)

- **unknown** blocks every run, always: "Not run: the same ... has an unknown result (id n). Check first whether it went out ..., then call
  `action_result(id, done|not_done)`." `done` keeps the step blocked for a model retry; `not_done` marks it `failed` ("checked: not sent") and lets a retry run.
- **done**, by who asks. What this stops is a repeat the owner did not ask for: a model retrying inside a run, or a background run redoing
  what another run already did. A new request from the owner is not such a repeat ("불 켜줘" in the morning and again in the evening both
  run; a second, separate request to send the same short message runs).
  - *Inside the same run* (for chat, the same turn): blocked, unless the owner's words of this turn ask for it more than once ("두 번 보내",
    "send it twice", "3 times", "again", "다시", "한 번 더", "재전송").
  - *In an earlier run, a chat or API turn whose owner words ask for this kind of action* (one table in `OwnerAsk`: send: "보내", "전송",
    "답장", "send", "reply", "post", "mail", "text"; change: "켜", "꺼", "바꿔", "올려", "내려", "열어", "잠가", "turn", "set", "open",
    "lock", "add", "move", "update"; delete: "지워", "삭제", "delete", "remove"): it runs. The tool result for the model carries one line:
    "Note: the same step was done at <time> (id <n>); it ran again because the owner asked for it in this message."
  - *In an earlier run, a chat turn that does not ask for it* (the model redoing something on its own, for example after "잘 갔어?"):
    blocked, "Not run: the same ... already went out at ...".
  - *A background run* (schedule, watcher, job): its own words never count (it has no owner words). Across runs only the next run of the
    **same schedule** may repeat its step (a recurring send is expected).
  - A change that sets a state (a device on or off, a calendar event updated with the same fields) follows the same rules.
- **failed** never blocks.

When a result is unknown the tool's text for the model says so: tell the owner what you did and that you are checking, check, and do not
repeat it before you know. The tool `action_result` is offered only while some step's result is unknown, so the prompt does not grow.
An **unattended run that ends with a result still unknown** makes one follow-up (kind `unknown_result`, `followups.py`): "Kelo did ...
but could not confirm it went through; please check.", once, in waking hours.

`GET /api/actions?days=` (1 to 30) lists the records for the later "what Kelo did" screen: targets as they are, no message bodies.
