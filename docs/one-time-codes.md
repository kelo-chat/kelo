# One-time codes hidden from the model (design, 2026-11-16)

Owner decision (board): the model only sees a handle like "verification code #3"; after the owner's approval the browser types the code
once. Take the studied reference agent's logic, names are Kelo's own. Evidence is in the private work notes (secrets evidence, B1 how codes
become handles, B2 approval, single use and expiry, A7 handles in the browser).

## Logic taken over
- B1: a code in a read result is cut out BEFORE the model sees it and replaced by a handle; the value is held by the server, not by the
  model. The reference does it with a classifier on selected read tools and FAILS OPEN when the scanner is down (a bug it fixed). Kelo
  masks by shape (below) and fails CLOSED: a failure while checking withholds the text.
- B2: the handle goes to the browser tool only; the owner approves on a card; the browser types it, then it is gone; it expires after
  about ten minutes; nothing is stored in the vault or memory. The reference binds handle + origin; Kelo binds handle + the sender's site.
- A7: the model never types a credential itself; the page must be https; the code goes into code boxes found by structure.

## What Kelo already had (kept)
`find_login_code` (tools/browser_more.py): searches the connected mailbox itself, the model sees sender and length only, the owner's card
shows the code, `code_enter` types it once. During a sign-in the agent started, the owner's setting "use codes from my mail
automatically" may type it without a card. The gap was: a code in a mailbox or phone read (email_read, email_list, phone_messages,
phone_notifications) reached the model in clear text.

## Pieces
1. `code_shelf.py`. `CodeShelf` (one shared `SHELF`, memory only): `mask(text, channel, sender)` replaces every code by
   `[verification code #N]` and holds `Held(ref, code, channel, sender, site, at)`. Same code + sender + channel keeps its number.
   TTL 600 s, at most 64 held (oldest first), `forget(ref)` after use. Numbers are never reused while the server runs.
2. Candidates by SHAPE, decision by the main model. `code_spans` (code, no model) finds candidates: every token of 4-8 digits (single
   space or hyphen between groups allowed), `AB-1234`, 6-8 capitals+digits, and the last-line `@example.com #123456` form. Longer digit runs
   (phones, accounts) and numbers beside a currency or percent sign are never candidates. Dates, prices and order numbers have the same
   shape, so shape alone cannot decide (the evidence, B1, shows the reference also uses a model-assisted classifier).
   No candidates -> no model call.
2b. The code judge (`code_judge.py`, same style as `step_judge.py`): ONE request to the owner's main model per message (subject and body
   together), per `email_list` call (all subjects batched) and per phone read. Input: the text with each candidate wrapped as `[[#N: token]]`
   plus sender and subject; output strict JSON `{"codes": [N, ...]}`: the candidates that are one-time sign-in, verification, login or
   reset codes (not order numbers, prices, dates, tracking or invoice ids). It runs outside the agent loop and returns indices only, so a
   code never enters the transcript. Cached per message id, else per hash of the input. FAIL CLOSED: no judge, an error or bad JSON masks
   every candidate; a candidate beyond the part of a long message the judge reads is masked too. No word list anywhere.
3. Where masking runs: `email_read` (headers and body), `email_list` (subjects), `phone_messages`, `phone_notifications`. The sender
   address (or phone number / app) is recorded; for mail its registrable domain is the code's `site`.
4. `find_login_code(code_ref=N)`: `BrowserExtras.hold_ref` checks that N is held and not expired, the page is https, and the page's
   site equals the code's site (a phone number or app has no site: the card names the sender and the owner decides). Then the usual
   approval card (code masked in the log line, clear only in the live card), `code_enter` types it once and calls `forget`. A refusal
   (wrong site, http) does not use the code up; a declined card is not a licence to retry.

## Not done / decisions
- A code in a shape the candidate finder does not know (for example 9+ digits or lowercase letters) is not masked; the card path
  (`find_login_code` without `code_ref`) still never shows it to the model.
- Chat-typed codes (the owner types a code to the assistant) are unchanged: `secret_guard` already keeps them from storage.
- Connector tools with their own output paths (Nest mail connectors, other read tools) are not covered yet.
- The existing mailbox search (`login_code.py`) still uses a code-word list to rank candidates; it only feeds the owner's card, never
  decides what the model sees, and was left as is.

## Tests
`tests/test_code_shelf.py` (fake judge): shapes, ordinary numbers stay readable, judge down masks all, one request per message/list, no candidates no call, handles and numbers, expiry/single use/capacity, fail closed, email_read/list masking, browser
typing once after approval, site binding, https, decline. Neighbours: test_browser_more, test_mail_codes, test_phone_data, hygiene.
