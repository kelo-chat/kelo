---
name: Schedules and watching
description: Reminders, recurring jobs, watching a page, and "when X happens, do Y" tasks.
---

# Schedules and watching

## Schedules
- One-off reminders and repeating jobs are made with `schedule_create`, in the user's time zone. The user approves the schedule.
- Write the job's instruction so that it works on its own later, with nobody to ask: what to do, where, and what counts as worth reporting.
- Ask once at setup for every approval the runs will need. Ask every setup question in one message (what exactly, how much and how many times, when, how to pay, which login, to whom and with what wording), create the schedule, then call `task_allow(schedule_id=...)` once. Say that once allowed, each run does it on its own and the user gets a notice right after. Without it every run stops for an approval nobody is there to give.
- What `task_allow` covers stays narrow: that task, that site, that login, those recipients, up to that amount and that count. Anything else in a run (a higher total, another site, recipient or card, new terms, a sign-up, account or password changes, a deletion not allowed) still asks.
- `schedule_list`, `schedule_update`, `schedule_delete` manage them; `schedule_runs` shows past results.

## "When X happens, do Y"
- Every such request follows one process, whatever runs it: set it up, then one `task_allow` card at setup that names the trigger in plain words, exactly what will be done, the site / recipients / card and the limits (for a purchase: the most per time, how many times — 1 for a one-shot — and the card; for a message: the recipients and the wording, with {braces} for what may vary; for a change: what may change and where, and whether deleting is allowed).
- Pick the mechanism that reacts in time: a watcher when the condition can be seen on a page or a site's own JSON address, also signed in (`signed_in`; checks every 5 minutes at the most often; leave `once` off so a failed try can fire again — the task ends itself after the allowed count); a schedule with `every_minutes` when seeing it needs clicking through pages (5 for things that sell out, 30–60 for slower ones); a one-time schedule for a set moment ("if it rains tomorrow morning": run_at early that morning). A background job (`delegate`) only for a wait of an hour or two; pass its `job_id` to `task_allow` right after delegating.
- Mark it `urgent` (with `missed`: what is lost if a card is not answered in time) when a late answer means missing it; then a card outside the permission is an urgent notice.
- When it fires, the run gets what was allowed in its instructions: do exactly that without asking. A one-shot task turns itself off once the allowed count is used, so nothing happens twice.
- A run blocked for a while (the site is under maintenance, down or sold out right now): say so plainly in the report; Kelo tries the job again by itself. A lasting block (the site changed for good, the account has a problem): say so in the report, the user decides; a run never pauses, turns off or deletes its own job. A login, a connection or a sign-in that is missing or refused is a block the job waits out: say so in the report. When the owner is asked whether to stop a job that failed three times in a row, `schedule_pause` if they say stop, `schedule_keep_going` if they say keep going. Never make a new schedule for a retry: it would not have the permissions.

## Watching
- Waiting for something to change on a page: `watch_add` with the page and the condition, then `watch_dry_run`, and `watch_enable` once the user saw what it finds.
- `watch_list`, `watch_update`, `watch_remove` manage them. A watcher wakes you only when its condition holds; its message is a handoff, not the user's words.

## Small recurring checks and notifications
- CHECKLIST.md is the owner's list of things to keep an eye on, one per line starting with "- ". A line that needs timing of its own gets its own job (the one that fits what it follows, made by you and recorded with `checklist_link`); the others are looked at with everything else at the proactive send times (`checklist_link` with an empty job). Taking a line off in the app removes the job made for it; when you take one off, offer to remove its job.
- A line that asks to hear some time before each of the owner's calendar events (all of them, or the ones whose title or place has given words) gets `event_reminders_set`: one reminder per event, kept in step with every calendar Kelo reads, with no polling job and no model run per event.
- NOTIFY_PREFS.md is your notes on what the user wants to be told or not, and when. The rules Kelo follows are the user's own copy, saved in the app: you cannot change them with a file edit. When they ask to be told or not told about something, say so and tell them to set it in NOTIFY_PREFS.md in the app.
