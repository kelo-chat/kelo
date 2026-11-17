# Click judgement without word lists (design, 2026-11-16; docs/no-word-lists.md phase B, first part)

Owner decision: no word, phrase, site or country lists decide anything in the server. This replaces the word lists of
`server/src/assistant/tools/commits.py` (hard-coding scan items H1-H9) and the places that import them.

Logic taken over from the studied reference agent (evidence in the private work notes, architecture section 2 and 3): ordinary browsing
and ordinary form steps go on without asking; what asks is decided by what the step DOES (spends money, sends to people, deletes,
changes account security, signs up), raised by the runtime, never by the words on a button; a task's own permission covers the steps
it named. Kelo adapts this with page structure first and its step judge (`step_judge.py`, merged with payments P1) where structure
cannot tell.

## Decision for a click (and for typing with Enter / submit)
1. Not pressable (plain text, no role, no click code) -> nothing to ask (as today, structure only).
2. Structure (`commits.structure`, without its word part `_NEW_WINDOW_WORDS`):
   - "goes": a real link (href to another page) with no click code and no form submit -> looking only.
   - "opens": opens a window or tab by its code or link target -> looking only (what is done in that window is judged there).
   - "changes": submits a form, runs code that sends a request or changes state (`_STATE_CODE` is code, not language: keep it) ->
     step 3.
   - "": not readable -> step 3.
   Exception that stays structural: a page whose structure is a sign-in or security form (password, new-password, one-time-code
   autocomplete tokens, WebAuthn) -> its submit is judged in step 3 even when it is a link.
3. Step judge (main model, strict JSON, cached per page path + control role + name; `step_judge.StepJudge`), extended from five to
   seven flags: spends_money, sends, deletes, account_security, signs_up, applies (submits an application or registration for a job,
   program, event, benefit or official record), opts_out (unsubscribes, withdraws consent, closes a membership). Kinds:
   spends_money -> pay, account_security -> security, deletes -> delete, opts_out -> optout, applies -> apply, sends -> send,
   signs_up -> account; none -> "" (no ask). Judge unavailable or bad JSON -> ask (card), never pass.
4. Personal details never ask by themselves (ask-less, 2026-11-17, as in the reference: "an ordinary browser form submit is currently
   allowed by default"). A form that carries an e-mail address, a phone number, a name or a message and whose submit the judge flagged
   nothing -> "" (no card); the kind "form" is no longer produced (a contact form that SENDS still asks: the judge says `sends`).
   Typing asks only where it touches money or sign-in -> kind "personal": a card number by VALUE (13-19 digits passing Luhn), or a
   field of input type password / autocomplete current-password, new-password, one-time-code. Typing an e-mail address, a phone
   number, a name or an address asks nothing; the privacy guard (owner copies, secrets) still checks typed text. The word lists
   PERSONAL and _MESSAGE_FIELD go.
5. Grants first (unchanged order in tools/browser.py): task permission / schedule grant / remembered rule for (site, kind) -> no card.
   Payments keep their own path (checkout tools, never a lasting allow from here).

## Removed (each with its replacement)
- `_KO_NAV`/English nav words (H1): structure "goes"/"opens" + judge.
- delete / opt-out / apply / sign-in / sign-up / send / generic word lists (H2-H4): judge flags.
- `_SECURITY_PAGE`, `_SETTINGS_PAGE`, security verbs, `_DELEGATION` (H5): sign-in/security form structure + account_security flag.
- `_DOC_WORD`, `_DOC_ISSUE`, `_NOT_A_DOCUMENT`, `_GOV_PAGE` TLD list (H6): applies flag (official record) + grants.
- `PERSONAL`, `_MESSAGE_FIELD` (H7): structure of step 4.
- `continue_only`, keep-signed-in regexes, `_PLAIN_YES`, negative words (H8): a step whose judge flags are all false goes on; the
  "keep me signed in" choice is a checkbox state (structure), not a word.
- `_CANCEL_ORDER`, `_NOT_AN_ORDER` (H9): deletes flag.
- `_NEW_WINDOW_WORDS`, `_COUPON`, search-field words (M1, M2): structure (link target / window.open code; a search form = role
  search or input type search; a coupon box = an input + non-submit apply control inside the order form, judged by the model only if
  it changes state).
- `judge_words` callers: tools/task_permission.py (the kinds a task asks for become an enum argument, no word check on the action
  text), android/judge.py (already on the step judge since payments P1; remove the word fallback), tools/order.py imports
  (`_SIGN_UP`, `coupon_words`: use the structural helpers).

## Kept (not language)
`_STATE_CODE`/`_OPEN_CODE` (JavaScript patterns), TARGET_JS reading of tag/role/form/autocomplete, the Luhn check, `is_count` digits.

## Tests
A scoped hygiene test: commits.py, task_permission.py and android/judge.py contain no natural-language literals (same style as
tests/test_no_word_lists.py). Behaviour tests with a fake judge: a real link with a buy-looking label -> no ask; a form submit judged
applies on a site with a schedule's apply grant -> no card; the lotto weekly case (grant covers purchase on that site; an
"issue numbers automatically" button judged applies=false spends_money=false -> no card); a submit judged deletes -> card; judge down ->
card; Korean, Japanese and English pages behave the same with the same fake verdicts; typing with Enter in a search form -> no ask;
a form carrying the owner's phone number by autocomplete=tel, judge says nothing -> no card (judge says `sends` -> "send"); typing an
e-mail address, a phone number or a name -> no card; typing a card number or into a password field -> "personal" (tests/test_ask_less.py).
