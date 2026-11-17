# Phone worker (design, 2026-11-16)

Owner request (2026-11-16 23:08): the phone needs an agent of its own, like the browser worker and the payment specialist. The agent
that talks to the owner does not drive the phone itself; it hands phone work to a phone job with a self-contained brief and the
result comes back as a handoff. The live view of the device and the owner's take-over stay exactly as they are.

## Logic taken over (evidence: private work notes, browser evidence A1-A5)
- A1/A2: the worker is a different agent from the one that talks to the user and cannot see the conversation, so its assignment is
  self-contained (app, goal in the owner's terms, choices already made, limits, where to stop). Its result returns as a handoff.
  Kelo already has this in `workers.py` (`delegate`, hidden handoff, `needs_input` as the question, `resume` as the answer).
- A3: the worker does not guess. What the brief does not cover (a choice, a login, a permission dialog it may not answer, an order that
  differs) goes back as `needs_input`; the main agent answers from what it knows or asks the owner.
- A5: one owner of the live surface. There is one device, so one open phone job at a time.

## What changes (tool-set scoping and one job kind)
`server/src/assistant/tool_roles.py` declares the roles, by tool NAME only (no words, no apps):
- `PHONE_KIND = "phone"`. A phone tool is any tool whose name starts with `android_`
  (`android_apps, android_key, android_login, android_look, android_open, android_swipe, android_tap, android_type`),
  by prefix, as the checkout specialist takes every `browser_*`. `PHONE_TOOLS` lists these names.
- Two exceptions stay with the main agent, because they need the chat reply that is running: `android_send_screenshot` (a picture
  of the screen in the reply) and `android_show` (handing the phone to the owner: its card and Done button belong to a reply, and a
  job has no reply: `handover` is set only by `turn_engine`). A phone job that needs the owner's hands reports needs_input saying
  what they must do; the main agent hands the phone over with `android_show` and resumes the job (review 2026-11-16: a job calling
  android_show would have waited ten minutes with no card).
- The phone job is shown: phone tools + signing-in helpers (`vault_status`, `request_login`, `find_login_code`) + `read_skill`,
  `report`, `job_progress`, `action_result`. No `PAY_TOOLS`, no `browser_*`, no `web_search` / `web_fetch`, no `delegate`, no mail, no
  files. Its phone tools are loaded up front (they are not core tools), so it never goes looking for them.
- Every other run (the chat, schedules, handoff turns, jobs of kind browser / research / general / checkout) is shown no phone tool
  while the split is on. `for_kind(tools, kind, pay_split=True, phone_split=True)` covers both splits in one place; `finder_tools`
  and `checkout_tools` stay the two views `turn_engine.run_turn` and `Workers._worker_tools` already call.
- `workers.py`: kind `phone` in `KINDS`. `delegate(kind="phone", task)`: `task` must carry the whole assignment. There is no url (the
  device is the surface). It needs a device on this copy (the phone tools exist in the app's tool list), otherwise it is refused.
  Only ONE open phone job (running or paused) at a time: a second is refused with a text that points at the open job
  (`worker_message` / `resume`). The job's live surface is the device, so its progress shows on the phone card / phone live view as
  today. Budget: like research (half the browser's steps and minutes).
- Approvals stay as they are: `android/judge.py`, approvals, the data trail and payments in apps are untouched; the phone job is
  bound by exactly the same code as the chat was. Hand-over to the owner goes through the main agent (above) to
  the phone live view (`android/handover.py`, `turn_engine.hand_over`: not touched here).
- Prompts: the main prompt and the `delegate` description say phone work goes to a phone job with a full brief; the job's brief says
  what it may and may not do. A schedule that needs the phone delegates too (its run has no phone tool either).
- Kill switch `ASSISTANT_PHONE_WORKER=0` (setting `phone_worker`): every run is shown the phone tools again (old behaviour). A phone
  job still has only its own role (`for_kind` always gives kind phone the phone set). With the split on, `find_tools` no longer lists
  the `android_*` family for the chat (its description's map leaves them out); `android_send_screenshot` stays listed.
- Apps: the Work page and job cards show a job's kind (`kindWord` on Android / desktop, `WorkText.jobKind` on iOS); "phone" ("Phone
  task", `work_kind_phone`) is added in all 20 languages (apps/shared/strings, iOS regenerated with gen_strings.py). The jobs of kind
  checkout still show the general word (as before).
- Prompt budget: `content/prompts/system.md` grew by one line (212 bytes); `test_prompt` caps and the `delegate` description cap
  (`test_workers`) were raised a little, with the reason in the test comment.

## Not done / left to others
- No change to `android/mirror.py` or the hand-over (another run owns them).
- A typed question id (`answer_to`) is not added; the main agent answers with `worker_message` / `resume`, as for the checkout job.

## Tests
`server/tests/test_phone_worker.py`: role sets (chat and other jobs have no phone tool; the phone job has them and no PAY_TOOLS,
`browser_*` or search), a second phone job refused, the job's tools loaded up front, kill switch, every name in the role sets exists in
the full tool list. Neighbours: test_browser_workers, test_workers, test_toolbox, test_android*, test_hygiene*, test_no_word_lists.

## First run of an app (owner 2026-11-16 23:22)
A newly installed app meets the phone job with the system's permission dialog, an intro tour, a promotional popup, a "rate us" box, a
terms page or an update prompt. A person setting the app up gets through them; the job does too, without a card for each tap. Nothing
here is decided by a word list (docs/no-word-lists.md): structure narrows who is asked, the main model judges the screen and the job's
brief, and the existing checks still run on every tap.
- **Permission dialog by package.** The package of the system's permission dialog is asked once from the device (bridge `GET
  /resolve?action=android.content.pm.action.REQUEST_PERMISSIONS`, read only, `cmd package resolve-activity`) and kept per client; no
  package name is written in the code. The requesting app is the last other app the run saw (`app_in_front_before`).
- **The judge** (`StepJudge.setup`, `step_judge.SETUP_SYSTEM`, strict JSON `{screen, allow, note}`) gets the controls (ref, kind, words,
  ticked), the job's brief (`AndroidSession.task`, the worker row's task), the dialog flag and what was answered before for this app.
  Rules: notifications allowed; a permission the job needs gets the narrowest choice (only this time / while using the app); a
  permission it does not need gets the deny choice; special access that leaves the normal dialog (accessibility, device admin, default
  apps, unknown apps, all files, battery, overlay) is the owner's (`allow` is forced empty); intro -> skip/next; popup/ad -> close;
  rating/updates box -> later/close; terms -> only the boxes the app requires, then accept, optional boxes unticked; update prompt ->
  later unless the app cannot go on (then the update control); sign-up and sign-in are `none` (their card / `request_login` flow stays).
- **Who is asked.** The permission dialog always. Any other screen only when it has something to press, no field for the owner's own
  data (so never a sign-up or a message form), and the app's last screens still showed something (`SETUP_QUIET` = 2 "none" in a row ends
  it until the app is opened again with `android_open`).
- **The kind "setup".** `AndroidSession.judge` returns `"setup"` for a tap on a control the judge allowed AND the step judge found
  harmless; `decider` lets it through (still asks when the owner set every step to ask). Anything the step judge flags (pays, sends,
  security, unknown) keeps its kind and its card. On a `special_access` screen every clickable control is `unknown`: a card, no grant
  covers it. The look tells the model which refs get through, or that the owner must decide (needs_input).
- **Report and memory.** A setup tap's result carries "First-run step, for your report: <app>: <note>" (the phone brief tells the job to
  put these lines in its report); the note is also kept per app in the Store's meta table (`phone_setup:<package>`, at most 12 lines) and
  given to the judge next time. `AndroidSession.forget_setup(package)` clears it. No new screen strings for the owner.
- Tests: `server/tests/test_phone_first_run.py` (screens built as uiautomator dumps, a fake judge, mutations checked by hand).
