# Proactive messages: prepare first, hold, bundle (design, 2026-11-16)

Owner decision (board card on holding and bundling proactive messages, 2026-11-16): Kelo prepares what would help before it speaks, bundles what one pass tells
into one message, and keeps what could not go out until its own deadline instead of throwing it away. Kelo's stricter
caps stay exactly as they are (daily_regular 2, daily_total 3, daily_now 3, the change-alert class, the unanswered guard, contact
hours). Owner decision 2026-11-19: the only daily number is "at most N a day" (daily_total; the apps derive daily_regular and
daily_now from it). There is no separate count of messages a day: the former daily_deliveries setting and its delivery slots are
retired (see 3). Nothing here uses word, phrase, site or country lists: every decision is a count, a time, or the main model's judgment.

The module docstring of `server/src/assistant/proactive.py` stays the reference for the whole pass (candidates, classifier, caps,
guard, hours). This file adds three things to it.

## Logic taken over (evidence: private notes, schedules evidence B1-B7 and D2; non-payment notes section 2)
- B2/B3: research prepares concrete help first ("turn insights into concrete help", "offer one specific next step", "keep the user in
  control": what needs approval is offered as a choice, never done). Claims are only what the facts show.
- B4: at most two ordinary deliveries a day, at most three counting time-sensitive ones; urgent ones at once (the separate count of deliveries was retired on 2026-11-19, see 3); code owns hours,
  capacity, quiet time and expiry. "Held candidates stay pending within their existing expiry; an expired candidate must be missed,
  not held." Every prepared opportunity carries `expires_at` ("useful until").
- B5: the item is judged again against the live conversation just before delivery; "a proposal to judge, never an instruction"; a
  declined or stale one is dropped, not sent.
- B6/D2: Kelo had no pending/held state: an item over the day's cap was dropped and a mail never came back. the studied agent keeps `held`,
  `reconsider_at`, `dropped (no confirmed delivery)`.

## 1. Prepare first (prompt, same single request)
The classifier request (`PROMPT`) already writes each item's message. It now also (a) says "prepare first": when the facts shown let it
work something out (when to leave, a one-line reply draft, what to bring, the date a form closes) it states that as ready and ends with
exactly one concrete offer the owner can accept; it never states what it could not check and never promises a result; (b) gives
`useful_until` for each kept item: the last local moment telling it still helps (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM`, null when
unknown). No extra model call, no tools: preparing from the supplied facts only (tool-using research is the hourly look, situation.py).

## 2. Hold instead of drop
New table `proactive_held` (key, source, title, urgency, reason, message, cand JSON, held_at, expires_at, why). An accepted item that
cannot go out now is held, not dropped, when it is not yet past its deadline:
- the day's cap for its level is used (`over_cap`) or the unanswered guard has no room (`waiting_for_reply`). These are the only two
  reasons.
- deadline (`expires_at`) = the item's own time (`Candidate.due`), else the end of its own day (`Candidate.day`), else the model's
  `useful_until`, else the end of the next local day. At most HOLD_MAX_DAYS (14). A deadline already past = missed: logged as
  dropped "Expired before it could go out"; an expired held item is never sent.
- A held item that the sources still list takes their current words when it is judged again (a big event held at "in 3 days" is told as
  "in 2 days" the next day).
- a held item is "in the ledger" (`_ledgered`), so a source that shows it again does not make a second copy.
- a held item is released in the next pass that is allowed to talk (contact hours, guard room, no chat running). Before it goes out
  the same main model judges it again together with the owner's recent chat (`_classify_batch(held=True)`: stays, changes its
  wording to the present, or is dropped because the owner already dealt with it or moved on). Released items are sorted with the
  new ones by level, oldest first, and meet the same caps. What still does not fit stays held until its deadline.
- a pass with nothing new but held items still runs (the model is asked only about the held ones).
- forget / clear-history / disconnect-a-connector delete held rows like they delete log rows.

## 3. Bundling
What one pass tells goes out as one message per group (one per mailbox, one for the rest; module docstring, 5); held items released in
that pass go into the same message as the new ones. There is no daily count of messages (owner 2026-11-19: the owner sets only how many
a day at most): every item that fits the daily caps goes out in the pass that accepts it. Each message carries at least one counted
item, so a day never has more ordinary messages than daily_total. Immediate-level items and change alerts stay outside the caps.
Retired on 2026-11-19: `daily_deliveries` and the held reason `slots`. A stored value is ignored (`config()` reads only known keys and
the next save drops it); held rows with why `slots` become `over_cap` when the server starts; a released app that still sends
`daily_deliveries` in PUT gets 200 and the value is ignored.

## 4. Send times, the hourly watch and how sends are counted (owner decisions 2026-11-19, research on)
The hourly look (situation.py) runs in two kinds of pass. Every hour on the hour inside the contact hours, the watch looks ONLY at new
mail and at what changed in the connected services (declared reads; SituationSources marked outside). Nothing new -> no model call.
What the model judges urgent (waiting until the next send time would cost the owner) is written by the main agent and goes out at once
as one urgent send at the immediate level, under daily_now (over it, it is an ordinary send and needs room in daily_total). Everything
else waits, as an open item, for the next send time. At the send times everything else is looked at (the last 24 h of conversation,
calendar, goals and follow-through, schedules and watchers, memory changes, the correction check of earlier answers, open items,
preferences, research and selection); a send time may carry a "today" and a "regular" message.
Counting: daily_total counts SENDS. Every message of one pass carries the same delivery id and the day's counts take each delivery
once (immediate when urgent, else today when any message is today, else regular); the unanswered guard counts sends too. The day is
the contact window's day (a window past midnight keeps its evening's day). Rows without a delivery id (the candidate pass with research
off, the checklist) count one each.
The send times (send_times.py): each day's contact window (its day type's window, the hours in effect) in equal parts, one per send a
day (daily_total) plus one for the morning briefing while it is on; whole minutes, rounded down; the briefing takes the part nearest to
its own time (Briefing.due_on). The briefing never goes out before the start of the contact hours (its time is clamped; an earlier fixed
time is refused). Example: 08:00-24:00, daily_total 3, briefing at 08:00 -> 08:00 briefing, 12:00, 16:00, 20:00; briefing off -> 08:00,
13:20, 18:40. A missed send time is caught up once, never past the next. With today's daily_total used up the day's remaining send times
pass without research; the watch goes on for what cannot wait. GET /api/proactive has "send_times"; the apps show them under the cap.
The apps' page "What Kelo looked at" shows when each thing is read, from GET /api/proactive/research "looks" (SituationResearch.looks):
every hour (new mail while mail_check is on, each connected service or phone item with a declared read), at each send time (the
conversation, calendar, goals and follow-through, schedules and watchers, memory, the correction check and the held items, with the
send times), at the morning briefing (its sections that are on and the correction check while the AI consent is given, with its
time; Briefing.covers), and only when needed (connected services without a read). "looked_at" stays as it was for released apps.
The "Kelo tells you first" text says the same in plain words when the server sends "send_times" (older servers keep the old text).

## 5. The checklist: its own timing or the send times (owner decision 2026-11-19)
CHECKLIST.md (the apps' "things to keep an eye on") has no pace of its own any more; the 2-hourly checklist pass is gone. When the
owner adds an item (in the app; any write by "user"), the server hands the new items to the main agent after a short pause, in one
hidden message (checklist.SETUP, led by CHECKLIST_MARK). The model judges each item: one that needs timing of its own gets the job
that fits (a schedule, a watcher, a live Home Assistant watch; made with the existing tools, without a card, because the turn carries
the owner's request: Turn.owner_asked and connect_tools.asked_in_chat) and is linked to it with checklist_link; the others are linked
to the send times. Asked in the chat, the agent does the same in its own turn. Items already on the list when this version first
started are handed over once the same way (it is the owner's own list: no card either). The links are rows of checklist_links;
an item without one, or whose job is gone, rides the send times. The owner taking an item off removes its job (unless another item
uses it); the agent taking one off offers to. Each send pass has a "Checklist" section with the send-time items, read in a pass that runs anyway because something changed
(an unchanged pack still makes no model call); the hourly watch does not read it. GET /api/checklist adds "entries" (each item
with its state and job); the apps show "checked separately · <job>" under an item with its own job. No prompt names any kind of item.

## API
`GET /api/proactive` adds `held` (count of held items). `GET /api/proactive/held` lists held items (key, source, title, urgency,
message, why: over_cap | waiting_for_reply, held_at, expires_at), soonest deadline first. The apps show "sent today · N held" under
the daily cap; the held part shows only when N > 0 and opens the list (three apps).

## Files
`server/src/assistant/proactive.py` (all of it), `server/src/assistant/situation.py` (blocked, watch, send pass), `server/src/assistant/send_times.py`, `server/content/prompts/situation_watch.md`, `forget.py`, `chat_clear.py`, `connector_data.py` (held rows),
`server/src/assistant/locales/*.json` (three new reason texts), `server/tests/test_proactive_hold.py`.

## Tests
Fake model and clock: hold over cap then release next day inside deadline; expiry = missed; revalidation drop and rewording; guard
hold; no daily message count
(every item under the caps goes out); a stored daily_deliveries is ignored; held slots rows become over_cap; PUT daily_deliveries
accepted and ignored; nothing-new pass with held; ledger dedupe; API; forget/clear.
