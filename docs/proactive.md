# Proactive messages: prepare first, hold, bundle (design, 2026-11-16)

Owner decision (board card on holding and bundling proactive messages, 2026-11-16): Kelo prepares what would help before it speaks, tells the owner in
one or two deliveries a day, and keeps what could not go out until its own deadline instead of throwing it away. Kelo's stricter
caps stay exactly as they are (daily_regular 2, daily_total 3, daily_now 3, the change-alert class, the unanswered guard, contact
hours). Nothing here uses word, phrase, site or country lists: every decision is a count, a time, or the main model's judgment.

The module docstring of `server/src/assistant/proactive.py` stays the reference for the whole pass (candidates, classifier, caps,
guard, hours). This file adds three things to it.

## Logic taken over (evidence: private notes, schedules evidence B1-B7 and D2; non-payment notes section 2)
- B2/B3: research prepares concrete help first ("turn insights into concrete help", "offer one specific next step", "keep the user in
  control": what needs approval is offered as a choice, never done). Claims are only what the facts show.
- B4: at most two ordinary deliveries a day, at most three counting time-sensitive ones; urgent ones at once; code owns hours,
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
unknown). No extra model call, no tools: preparing from the supplied facts only (tool-using research stays with recheck.py).

## 2. Hold instead of drop
New table `proactive_held` (key, source, title, urgency, reason, message, cand JSON, held_at, expires_at, why). An accepted item that
cannot go out now is held, not dropped, when it is not yet past its deadline:
- the day's cap for its level is used (`over_cap`), the unanswered guard has no room (`waiting_for_reply`), or the day's delivery
  slots are used (`slots`, see 3).
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

## 3. Bundling: delivery slots
`daily_deliveries` (default 2, 0..10): ordinary messages (a group with no immediate-level and no change alert item) per local day. A pass counts
its groups (one per mailbox, one for the rest) against what is left; groups over the number are held (why `slots`), lowest level
first. An item of the immediate level (urgency value stored as the Korean word for "now") or a change alert is never held for this and its group is not an ordinary delivery. The checklist run that said
something counts as one ordinary delivery. Because held items wait for the next delivery, the second delivery of the day carries
what the first could not, in one message.

## API
`GET /api/proactive` adds `daily_deliveries`, `today_deliveries` and `held` (count). `PUT /api/proactive` accepts `daily_deliveries`.
`GET /api/proactive/held` lists held items (key, source, title, urgency, message, why, held_at, expires_at), newest deadline last.
No app screen is changed in this run: the apps only read the existing fields, and the settings screens do not show the slot count yet
(listed as follow-up for the three-app parity batch).

## Files
`server/src/assistant/proactive.py` (all of it), `forget.py`, `chat_clear.py`, `connector_data.py` (held rows),
`server/src/assistant/locales/*.json` (three new reason texts), `server/tests/test_proactive_hold.py`.

## Tests
Fake model and clock: hold over cap then release next day inside deadline; expiry = missed; revalidation drop and rewording; guard
hold; slots bundle second delivery; immediate-level/change never held by slots; nothing-new pass with held; ledger dedupe; API; forget/clear.
