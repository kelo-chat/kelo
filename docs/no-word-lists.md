# No word lists in the server (design, 2026-11-16)

Owner decision (2026-11-16): no hard-coding in the container. Behaviour must not be decided by lists of human words, site names or
country domains in code. Decisions come from (1) what the owner allowed (grants, task permissions, cards), (2) tools whose meaning is
fixed by their type (a checkout tool spends money; a send tool sends), (3) page and protocol STRUCTURE (form semantics, autocomplete
attributes, HTTP status, URL changes, IMAP special-use flags), and (4) a short model judgment when the structure cannot tell.
The inventory of today's lists (27 places) is in the session notes; this file is the plan that replaces them.

## Principles
1. Grants first. A step inside what the owner allowed (site + purpose + caps + time) is never re-asked because of a word.
2. Meaning by tool. Spending money, sending to people, deleting, changing account security and signing up each have their own
   tool or a declared `intent` on the generic browser tools. The server's rules read the tool / intent, never the button text.
3. Independent check by a model, not by words. When a step would skip a card (inside a grant, or declared harmless), and the page
   structure says it submits something (a form submit, a button inside a form with card/payment/contact inputs, a POST that changes
   the URL), a small judge call (the background model; strict JSON {"spends_money", "sends", "deletes", "account_security", "signs_up"};
   one call, cached per page+element) confirms the declared intent. A mismatch asks the owner. No model available -> ask.
4. Consent through cards, not text. Yes/no from the owner is a card button (or the model's tool call after it asked), never a
   substring of the owner's message.
5. Words may remain only in prompts and in texts shown to people.

## Phases (each phase = one or two worker runs, server first, apps after)
A. Money and consent (urgent)
   A1 Checkout tools: `checkout_review(cart)` (merchant, items, total, currency, payment method) -> grant check or approval card;
      `checkout_submit(review_id, ref)` re-reads the page (ref label/role/url as reviewed) and clicks; `purchase_result` stays the
      receipt check (required before the reply ends). The generic click refuses on a page whose structure is a final checkout step
      (payment-method inputs or provider frames + an order total + a submit control) with "use checkout_review / checkout_submit".
   A2 Remove order.py BUY_REQUEST / _GO_ON / SEND_REQUEST standing-intent words: first payment on a new site and 6 h standing intent
      come only from an approval card or a task permission (task_allow).
   A3 forget_site and every other yes/no consent: a card with buttons.
   A4 outside_rule SITE_NAMES: the target of a send after outside text must appear in the OWNER's own messages (string match of the
      target the model declares against the owner's words, like the reflection quote check), else ask.
B. Clicks and typing (revised after studying a reference agent design): every browser task carries a STAGE set when it
   starts: research | cart | purchase (and send for a form that sends to people). Inside its stage ordinary clicks and form submits
   are allowed with no word check; money moves only through the checkout tools (A1) and only in a purchase-stage task; a cart- or
   research-stage task that reaches a checkout step stops and reports. Purchases never get a lasting allow except: a scheduled task's
   payment Allow covers its later purchases from the same store with the same payment method, and one pre-approved order (store, item,
   max total). Unattended runs that need a decision leave the purchase unfinished and name the open choice. An unknown payment
   outcome blocks retries; the order is checked again at 10 and 30 minutes. Where a stage is not enough, browser/Android click and type tools take `intent` (enum: navigate, fill, select, submit_form, send, delete,
   apply, sign_up, account_security, pay) required for submits; commits.py judge_words/judge_click/security_step/_FINAL_WORDS are
   replaced by intent + grant + the judge call of principle 3.
C. Sign-in, verification, challenges: login outcome from structure (password field gone, URL left the login page, session cookie
   set, HTTP status) then the judge model when unclear; challenge/blocked from HTTP status, known challenge iframes by their
   provider's script origin (protocol facts, not words) and the judge model; verification / payment-step / OTP from the page's form
   structure (one-time-code autocomplete, input lengths) and the judge model.
D. Notices and extraction: injection_watch replaced by the judge model on outside text (once per fetched document), follow-through
   refs extracted by the planner model (no FLIGHT_RE), mail folders by IMAP special-use then the model, said_do removed (the engine
   asks the model to finish with a tool call or say it is done).
E. (A reference design keeps per-method policy in each connector's manifest: allow|ask, approval_policy fresh_consent_only | task_allow | dedicated,
   card phrase, command -> method map.) Per-service rules (forbidden.py paths, presets) move from code into each connector's package data (Nest manifests), so the code
   holds no service names.
Each phase deletes the lists it replaces and adds a test that the module has no natural-language literals left (a hygiene test like
test_hygiene's Korean check, scoped to the replaced modules).

## MEDIUM and LOW items: what replaced each (c-hardcode-mid, 2026-11-16)
Source: the hard-coding scan of 2026-11-16 (MEDIUM 48, LOW 13). Standards used: the Public Suffix List (`tools/public_suffix_list.dat`,
`tools/sites.py`), HTTP status, the provider's own error code, ARIA roles and WHATWG autocomplete tokens, IMAP special-use flags (RFC 6154),
IATA airport codes. `tests/test_no_word_lists.py` now guards the cleaned modules (no non-Latin literal, no word alternation in a regex,
no list of four plain words). Not guarded on purpose: `effects.py`, `buy_replay.py` (their HIGH lists belong to phases A/B), `offers.py`
(model prompts with Korean examples), `model_limits.py` (reads a reset TIME out of a CLI's phrase; the decision is by status).

| Item | What replaced it |
|---|---|
| private-suffix lists (`.lan/.home/.corp` ...) in egress, netguard, vpn, keyring_client, ai_consent | `sites.in_public_dns(host)`: more than one label and a top-level domain delegated in the root zone (the PSL's ICANN section; `.arpa` excluded). ai_consent also resolves a public name that points into 100.64/10 (a tailnet name) |
| found_secrets and login_code second-level suffix lists | `sites.registrable` (PSL) |
| login_code keywords (~20 languages) | candidates by shape (amounts by currency symbol, years only when alone), then the code judge (`code_judge.CodeJudge`, one main-model request over the authenticated messages in the window) names the sign-in code; no judge or a failed one types nothing unasked (the owner's card still shows candidates). Sender, time window and exactly one code stay |
| egress DENY_HOSTS (Google) | data file `server/content/egress_deny_hosts.json` |
| presets BROWSER / BLOCKS_BOTS / UNSUPPORTED service names | data file `server/content/connector_ways.json` (texts stay in code under reason keys, so i18n keeps working) |
| mail.py trash / archive / junk folder names | IMAP special-use flags only; no flag -> the error lists the folders and the agent uses `move` |
| effects OUTLOOK_NAMES, calendar aliases | the Graph account's own name (`microsoft_mail.ACCOUNT`); the literal target `calendar` |
| model_limits / resume / compaction provider-error words | HTTP status + the provider's error code (`providers/types.py` CODES_*, parsed from the JSON body by `error_code`); a context overflow is a 413, a context code, or a 400/422 for a request estimated within 90 % of the window; a status-less CLI failure is a limit only when the CLI's own usage windows say the model is used up (`QuotaGuard.classify`); a rate limit (429 or its code, not a balance) with no reset time, or one within 60 s, is busy, not used up: retried before any answer (`usage.metered`), then held 120 s and said as busy (`QuotaGuard.hit`, `busy_wait`); CLI texts carry their status as "(code 503)" (`cli_agents._status`); job outage = `turn.outage` set by the engine, not a re-read of the error text |
| said_do promise words, skill-way words | deleted; one prompt line asks for the tool call instead of announcing it |
| reflection correction words | deleted; the nightly review's model names the corrections, `correction_turns` counts the owner messages holding a verified quote |
| big_events FLIGHT / STAY / TRIP | structure first: `flight` (IATA airport pair in the title) and `days` (a multi-day event); for the rest one batched main-model request (`event_kinds.EventKinds`: flight / stay / trip / appointment / other, cached per event id + updated time) adds single-day stays and trips. Model down = structure only |
| follow/items FLIGHT_RE + booking ref, runner status words, flight_icn words | flight designator stays (IATA format); the booking reference is a field of the model's answer (`event_kinds.MailBookings`, one request per pass, must appear in the mail text; the mail source of follow-through is not wired in the server yet); `cancelled` is a boolean the source sets from its API's own field; ICN airport by IATA/ICAO code only |
| proactive Korean date regex | ISO dates only (memory prompt asks for YYYY-MM-DD) |
| goals typing phrases (~25 languages) | deleted on the server and in the Android/desktop and iPhone apps: only the model's `typed` mark and the app's own Other box |
| offers money pattern | digits of any script and currency symbols |
| memory_upkeep section words | the three section names; any other heading goes to "About the user" |
| buy_ready SIGN_IN / ACCOUNT (10 languages) | `BrowserSession.password_box_shown` (a visible password input) |
| buy_replay PERSONAL | typed text is replayed only for the ARIA roles spinbutton and searchbox |
| receipt units / options | `x2`, `2x` only; option labels dropped by the `label: value` structure |
| done_once outcome and confirmation-number regexes | ARIA `alert` role lines mark a failed page step; ids only from keys a tool names (`order_id` ...) |
| browser login outcome | `classify_sign_in`: bot check by structure, one-time-code input, invalid/alert marks, password field gone / URL left |
| android/signin words | `SignInReader` by structure (password field, app change, a lone input); the submit control: the one clickable control after the field, else the step judge picks the first plain one of up to four (nearest first); Enter first, the tap only when Enter moved nothing |
| challenge titles / denied words / CDN vendors | removed; response headers, marks and frames stay; `blocked()` = HTTP 403/429/503 with a short body and no vendor mark |
| phone_data "mobile" label (English and Korean) | a contact with several numbers is asked about, never guessed |
| task title prefix, the Korean "none" marker | a one-word label + colon; deleted |
| catalog FEATURED, URL tracking params, weather KMA values, vendor token prefixes, eval_live pattern | kept: display order, parameter names, an API's own value enumeration and secret-format prefixes are data/protocol, not language. eval_live is the owner's own exception |

Not done in this run (need a real browser or a live model to verify): the JavaScript word probes of `tools/page_notes.py` (overlay close,
cookie, app-banner words), `tools/keypad.py` (protected-keyboard words), `tools/payment_step.py` and `tools/verification.py` (verification
and payment-step words), the shipping-field words in `tools/browser.py`, `signin_state.py` address words, `mcp_oauth` brand names,
search stopwords, `proactive.NEVER_HEADINGS` (older Korean heading, still read), `reflection._KO_DONT`.

## done_once.py: repeats, the owner's ask and the outcome of a step (hc-repeat, 2026-11-17)
Items H4, H5 (the owner's words) and H6 (the outcome of a step). Nothing in `done_once.py` reads a human word any more.

| Item | What replaces it |
|---|---|
| H4 `RepeatWords` (again / twice / two times + send, change, delete verbs, English and Korean) | `TurnAsks` (`done_once.py`): ONE main-model request per owner turn (`model_json.ModelJson.ask`, cached by turn id + request hash), strict JSON `{"count": n, "kinds": ["send"\|"change"\|"delete"]}`: `count` = how many times the message asks for the action to be carried out (0 = it asks for none, e.g. a question about a result), `kinds` = which kinds. A repeat inside one run is allowed only while the done steps of that run with the same fingerprint number fewer than `count`. |
| H5 `OwnerAsk.TABLE` (action words, result-question endings) | The same judge answer: a step of kind K that was done in an earlier run runs again (with the note) only when the owner's turn has K in `kinds` and `count` >= 1. A background run, a group, a job and an unattended turn have no owner words and are never asked. |
| H6 `_TIMEOUT`, `_REFUSED`, the class-name regex | Status and type only. HTTP status (408/429/5xx unknown, other 4xx failed), SMTP reply codes and `smtplib` exception classes, `asyncio.TimeoutError` / `TimeoutError` / `ConnectionError` / `httpx.TransportError` / `OSError` = unknown, `ValueError`/`TypeError`/`KeyError`/`LookupError`/`PermissionError`/`BrowserPolicyError` = failed. The tool's own prefix (`Error`, `Not run`, the MCP error marker) says it went wrong; with no code and no exception type behind it the result is `unknown` (a retry is blocked until `action_result` says what happened), never read from the words of the message. |

Fail closed: no judge, an error, an answer that is not exactly that shape -> `count` 1 and no kinds. So a done step is never repeated
inside a run, and a done step of an earlier run is not repeated by the model; the block text tells it to tell the owner, who asks again.
The judge is asked only when a done record of the same fingerprint exists (so not on ordinary sends), and the message is sent as data.

## hc-money (2026-11-17): the cancel window and the checkout total
| Was | Now |
|---|---|
| receipt.py `_FREE_WORDS`, `_NOT_FREE`, `_NO_FEE` (free / not free / no fee in English and Korean) read out of the receipt's `cancel` text | `cancel_terms` reads two typed fields of the model's receipt JSON: `free_until` (an ISO 8601 time; a plain date means the end of that day) and `fee` (a number, or text holding one amount; `0` = free). Free now only when `free_until` is an ISO time still ahead, or when `fee` is the number 0 and no `free_until` is given. A `free_until` that is not an ISO time, a fee text with no number, a missing `cancel` object, a deadline in the past: not free, so the owner is asked (a card). The tool schema tells the model to put the page's meaning into those fields. |
| receipt.py currency symbols in code (the won, yen and yuan signs among them) | data file `server/content/currency_symbols.json` (symbols and ISO codes are data, not words) |
| buy_replay.py `TOTAL_STRONG`, `TOTAL_WEAK`, `AMOUNT` (label words in Korean, English, Japanese, Chinese; currency words) | the replay's total is the amount of the page's own order-total elements (`checkout.read_page(...).totals`: schema.org totals, `data-total`-style attributes, `<output>`), the same structure `checkout_review` uses. Exactly one distinct amount among them, in the granted currency (a currency the page names that differs, or the page's declared currency that differs, stops it); none, several, or no structure = the replay stops and hands the run to the model. Never a guess from a label. |
Fail-closed rule: whenever the structure does not say, the result is the safer one (a card for the cancel; the model takes over for the replay).
`receipt.py` and `buy_replay.py` are in GUARDED in `tests/test_no_word_lists.py`.

## hc-actions (2026-11-17): what replaced H1, H2, H3 and H7
Source: the re-scan of 2026-11-17 (items H1, H2, H3, H7). Rule for all four: when no structural signal exists the result is the SAFER one
(a card, a refusal), never "allowed". One model for every purpose (the owner's main model); tests use fake judges.

| Item | What replaced it | When nothing can tell (fail closed) |
|---|---|---|
| H2 `effects.VERBS` / `VERB_KIND` (first verb in an MCP tool's name decided read / change / send / delete) | `effects.mcp_kind`: the server's `destructiveHint` (delete) and `readOnlyHint` (read), then the connector's manifest (`connector_rules.json`, can only tighten), then ONE cached request of the main model (`tool_judge.ToolJudge`, strict JSON `{"kind", "own_check"}`, cached per connector + tool + description; asked by `effects.ensure_judged` before the call is checked; a changed description is judged again). The same judge reads a REST call of a connected service (`connector_send`) by method + path. A judge answer can only make a kind stricter than the method's | a change, and it asks |
| H1 `effects.OWN_CHECK_WORDS` (pay, order, card, pin, sign up ... in the tool name or URL decided whether the owner's "allow" may skip the card) | `effects.keeps_own_checks`: a declaration in the manifest decides by its policy; a `readOnlyHint` read cannot pay; otherwise the judge's `own_check` flag (pays, buys, orders, moves money, card / PIN, sign-up or account, password or security change) | the call keeps its own card whatever "allow" says |
| H3 `schedule_grants.ACCOUNT_WORDS` (sign-up / account / password words in what a standing permission covers) | `StepJudge.text`: at setup, before the card (`task_allow`'s `hold`), each part's own words (purchase / send / change / apply) are read by the step judge; `signs_up` or `account_security` refuses the permission. The run-time purchase check no longer re-reads the order summary (the checkout tools and the click judge already stop sign-ups and account steps there) | no judge or no answer: nothing is allowed, the user is told |
| H3 `schedule_grants.DELETE_WORDS` (delete in an MCP tool's name) | the effect kind of the call (`e.kind == "delete"`: hint, manifest or judge), never the name | unknown is a change that asks |
| H7 `watchers.ACTION_WORDS` / `ACTION_KO` / `READ_KO` / `action_word` (an address or body naming an action refused a POST watcher) | the method: a watcher is a GET unless the owner approves a POST. The agent's `watch_dry_run` of a POST watcher (its first request) and `watch_enable` are cards that show the method, the address and the body (`auto_approve` only for GET); changing the request turns the watcher off until a new dry run, so the card comes again | a POST always asks |

`effects.py`, `schedule_grants.py`, `watchers.py` and `tool_judge.py` are in GUARDED (`tests/test_no_word_lists.py`).

## hc-injection (2026-11-17): what replaced H13
| Was | Now |
|---|---|
| `injection_watch.py` `STRONG`, `WEAK`, `DISCUSSION`, `SECRET_WORDS`, `ACCOUNT_WORDS` and the 30 `Rule` regexes (English and Korean) decided whether outside text steers the assistant | `InjectionJudge`: ONE request of the owner's main model per fetched outside document (`model_json.ModelJson.ask`), cached by the sha256 of the document, strict JSON `{"override": bool, "exfil": bool, "money": bool, "quote": str}`. override = drops or replaces the assistant's instructions, takes a role or mode, hides something from the owner; exfil = moves the owner's private details out; money = pays, transfers, buys, orders. The model reads the text after `fence.simplify` (look-alike letters and invisible characters resolved) and is told the text is data. A `quote` is kept as the log excerpt only when it is really inside the document, else the start of the document is. A true flag is a high hit (score 0.9), so the notices work exactly as before: exfil and money tell the owner at once (once per origin per day), override tells when a later step is turned into a card or refused |
| Structure that stays | the marker hit (text that looks like the app's fence lines, `fence.neutralize`), always high and always raised; the `LeakMarker` and its leak stop (`privacy_guard.py`); `fence.py` fencing and `outside_rule.py`; the fence's data marks (only the marked data of a result is read). The URL / mail-address stripping existed only for word matching and goes with the words |

Fail-closed rule: the judge's answer must be exactly that shape; down, slow (20 s), an error or any other shape = no answer. Then the document
is what it always is, fenced untrusted data (the model is told the fence is data; `outside_rule.py` still makes a step after outside text
ask), and only the structural marker hit can be raised. A notice that today's code raises for a structural reason (the marker, the leak
stop) is never dropped for lack of a judge. No answer is not cached, so the next read asks again. The judge runs in `loop._execute`
(`_result_async`); `loop._result_text` (sync, used by tests) runs the structural part only. `injection_watch.py` is in GUARDED.

hc-injection, review change (same day): the judge is not in a step's path.
- The tool result goes to the model at once with the structural part only. The document goes to the judge in the background (`InjectionWatch.judge_later`); the judge's 20 s limit now only bounds a background task.
- The judge reads at most 12,000 characters of the simplified text (the start and the end, with a note of what was cut out). A document under 200 characters is not sent; one already judged (cache key = origin + hash of the bounded text) is not sent again; one judge request per origin per run, so a page read again with small changes uses the first verdict.
- When a verdict arrives: exfil or money tells the owner at once, as before. Any true flag also marks the run: from then on every send, change or delete step asks (`OutsideReview.line(strict=True)`: the step counts as derived from outside text and nothing the owner said names its target; the owner's own addresses still pass), and the model gets the usual line on its NEXT result, once. No answer: nothing is marked, the next read asks again, and only the structural marker hit counts.


## hc-privacy (2026-11-17): privacy_guard.py, secret_guard.py, memory_upkeep.py
Items H10, H11, H12. The shapes stay (provider key prefixes, JWT, PEM, Luhn, IBAN mod 97, RRN/SSN, E.164-style phone digits, hyphenated
bank-account groups, entropy). What a human word decided is replaced as follows. Fail-closed rule everywhere: when the structure is
silent and the judge is missing, fails or answers in any other shape, the result is the safer one (mask / hold the message back / ask
a card / do not store).

| Was | Now |
|---|---|
| secret_guard `_PW_WORDS`, `_CODE_WORDS`, `_NOT_A_VALUE` (a password or code "given with its word", 14 languages) | Candidates by shape only (`secret_guard.candidates`): a token of 6-64 printable characters that mixes letters with digits or symbols (not a URL, address, path, UUID, hex), a standalone run of 4-8 digits, 3-8 digits after `:` or `=`, a message that is one number. ONE cached main-model request (`secret_judge.SecretJudge`, strict JSON `{"secrets": [N]}`, the context around each candidate sent as data) names the passwords, PINs, codes and credentials. No judge, an error or any other answer: EVERY candidate is a hit (the message is held back; the owner may mask it or send it as is). `secret_check` is therefore async. |
| secret_guard `_ORDER_WORDS` (order/invoice/tracking words before a card-looking number) | removed: a number that passes the card prefix and the Luhn check is a card, whatever stands next to it (the safe side) |
| privacy_guard `LABELLED` (address / health label words in user.md, MEMORY.md) and `_PHONE_WORD` | `PrivacyGuard.refresh()`: ONE cached request per version of those two files (hash) in which the model classifies each line (`address`, `health`, `phone`, `none`); a phone is also any `+` or `0`-led number of 8-15 digits. No verdict for the current text (never judged, model down, bad answer): every `label: value` value of the files counts as a `personal` value (the safe side) |
| privacy_guard `_PASSPORT`, `_ACCOUNT_WORD` | the passport shape (1-2 capitals + 7-8 digits) alone; IBAN by mod 97; hyphenated account groups (already structural) |
| privacy_guard `_CODE` (door / entry / lock words + 4-8 digits) and `_CARD_TAIL` (card / ending words + 4 digits) | a standalone 4-8 digit run or a keypad shape (`*1234#`) is a candidate that the same `SecretJudge` names, once per outgoing text (`PrivacyGuard.prepare`, awaited by `Gate` before the sync `check`, and by the group-chat vetting); not yet judged = category `code`. Digit runs inside a phone, card, ID, account group or date are not candidates. A card's last four digits count when they stand alone in the text and equal a wallet card's last four |
| memory_upkeep `_SECRET_WORDS`, `_ACCOUNT_WORDS`, `_NOT_A_VALUE` | `scrub` keeps only shapes: key prefixes, long random tokens, RRN, Luhn cards, IBAN, and a `label: value` line whose value is password-shaped (letters mixed with digits or symbols) is dropped whole. The model's claim JSON carries a boolean `secret`; a claim whose `secret` is not exactly `false` is dropped by `check_shape` (a missing or odd field counts as a secret) |
`privacy_guard.py`, `secret_guard.py` and `memory_upkeep.py` are in GUARDED in `tests/test_no_word_lists.py`.
Chat path (coordinator, 2026-11-17): `secret_guard.chat_candidates` is narrower so ordinary messages never wait: a token of 8+ characters with 3 of 4 classes (lower, upper, digit, symbol = `!@#$%^&*+=~?|<>[]{}\`), the value of a `key: value` / `key=value` pair that has a digit or symbol, or a message that is one 4-8 digit number. A digit run inside a sentence is not a candidate. The judge gets 3 s (`CHAT_TIMEOUT`); unanswered = held. Other callers keep the wider `candidates`.

## hc-pagefields (2026-11-17): what replaced H14-H20, M1-M5 and L1
Source: the re-scan of 2026-11-17. Rule for all of it: STRUCTURE first (HTML autocomplete tokens, input types and inputmode, maxlength, ARIA
roles, frame and link schemes, countdown elements, the page's own address parameters); when structure is silent, ONE cached request of the
owner's main model reads the page text (`page_judge.PageJudge`, strict JSON, cached per page text with digits ignored; the page text is sent
as data). **When neither can tell, the SAFER side**: treat the step as a payment approval or a sign-in verification (a card the user can
cancel), leave a field untouched, mask a typed value, hand a keypad to the user, never press a control that has words.

| Item | What replaced it | When nothing can tell (fail closed) |
|---|---|---|
| H14 `payment_step` words (`APP_WORDS`, `PASSKEY_WORDS`, `OTP_WORDS`, `EXPIRED`, JS `codeCtx`/`notCode`, the "send again" and "clear" regexes, time units in words) | `STEP_JS` reads structure only: a QR picture (square image/canvas/svg, named "qr" in its own attributes), short large codes (candidates), `[role=timer]` / `<time>` / timer-named elements with `mm:ss`, a one-time-code input (autocomplete one-time-code, or a numeric 5-8 box), the clickable controls (marked, not read). `PageJudge.payment_step`: `{kind: none/app/otp/passkey, expired, seconds, code: index, qr, resend: index, line: index}` points at the marked candidates and the numbered line; the keypad's erase key is `PageJudge.erase_key` over the controls around the pad. A present structure is never overruled by "none" | `payment_step_wait` with no structure and no judge answer = an approval card (`app`); a judge that says "none" with no structure = "no step found" |
| H15 `browser._EXTRA_AUTH` (3-D Secure, bank, card-app words) | `BrowserSession.extra_auth_signs`: a visible one-time-code input in the page or a frame, or the tab left the site a purchase was allowed on. The note still sends the agent to `payment_step_wait`, which reads the page with the judge | no sign = no note; `payment_step_wait` is still the agent's own call |
| H16 card-field label words (card number, CVC, expiry, holder) in `_TAG_CARD_FIELDS` | autocomplete tokens (WHATWG `cc-*`, `postal-code`) first. Fields still open are listed (ref, type, inputmode, maxlength, placeholder, label) and `PageJudge.fields("card", ...)` names the ones it is sure of (strict JSON `{fields: {ref: kind}}`, kinds of the card form only); a single number box shorter than 13 digits is dropped | a field neither names nor labels is not typed; the answer lists the missing parts and the user takes over |
| M3 shipping-field words in `_TAG_ADDRESS_FIELDS` | autocomplete address tokens and `type=tel` first; the rest by `PageJudge.fields("shipping", ...)` over the open fields (kinds of the shipping form only); e-mail, password, search, card and billing fields are skipped by token or type before the judge sees them | not filled |
| M4 OTP/password input words in `_TAG_CODE_FIELDS` | `autocomplete=one-time-code`, a row of 4-8 one-character boxes, or the ONE visible box shaped like a code (numeric inputmode or maxlength 4-8, no autocomplete token that names something else) | two or more such boxes: none is typed |
| H17 `verification._VERIFY`, `_SIGN_IN_PAGE` (~25 languages) | code boxes by structure (`probe.otp`); a short page (<= 1500 characters) after a sign-in the agent started with a short input = `code`, with none = `approve`; `BrowserSession.judge_probe` asks `PageJudge.verification` (none/code/approve) for exactly those unconfirmed pages and stores `probe["judged_step"]` | no judge or no answer: the short page after a sign-in IS a verification step (the user is asked; "signed in" is never said on a guess) |
| M5 `signin_state._SIGN_IN` (path and title words) | `sign_in_address`: an OAuth authorization request (client_id + response_type) or a query that carries a whole web address back (the page to return to); a watcher's redirect also by the password input of the body (`watchers.has_password_box`) and by "the address changed" | a probe that could not be read is still signing in |
| H18 `vault_tools._BY_MAIL` (mail named on the page) | the hint to call `find_login_code` is given for any code step when a mailbox is connected; `find_login_code` itself decides by the mail (code judge) and the user's grant | - |
| H19 `takeover` journal masking JS (card, cvc, password, ... in 8 languages) | a value is quoted only for a check box, radio, `<select>` and `type=search` outside any frame; everything else typed (text, number, phone, e-mail, password, one-time code, `cc-*`, anything in a frame) is masked; a clicked field's own value is never used as its label | mask |
| H20 `keypad.FIND_JS` `pinWords` and exclusion words | one password input shaped like a PIN (maxlength 4-8 or numeric inputmode), or 4-8 one-character password boxes; autocomplete tokens naming something else (username, `cc-*`, one-time-code, current-password, contact and address tokens) exclude a field | more than one candidate, or no PIN shape: not typed |
| M2 `keypad.PROTECTED_WORDS` | "protected" by structure only: a grid of 10-16 like-sized blank keys in 3 rows, a canvas next to a password box, a visible `<object>`/`<embed>` | the user enters the PIN in the card |
| M1 `page_notes` JS probes (`CLOSE`, `COOKIE_CLOSE`, `NEVER`, `APP`, `COOKIE`, `CHECK`, `CLOSE_HINT`) | a control is pressed only when it has NO words (a lone cross sign is not a word) and is declared a close control (`command=close`, `data-dismiss`, `data-bs-dismiss`, a `close` class or id) or is an icon drawn in the top corner of the layer or its sheet; a labelled icon control is taken only when declared. An app-install layer = a link whose scheme is not a web address. A cookie layer = a consent/cookie id or class. The human-check text probe is gone (a visible frame already leaves the layer alone) | a layer with only word controls is reported, the agent reads it and decides; "don't show again" and "accept" are never pressed by code |
| L1 masked-value unit words (won, USD, yen, yuan) | symbols and asterisks/dashes only | - |
| hc-challenge-probe: `challenge.PROBE_JS` one-time-code and search words (`otp`, `verification code`, `2fa`, `search`, `query` ...) | `otp` = inputs with the autocomplete token `one-time-code`, plus a row of 4-8 one-character boxes, else the ONE visible box shaped like a code (numeric inputmode or maxlength 4-8, no autocomplete token naming something else); `short_inputs` skips only `type=search`, `role=searchbox` and boxes inside `role=search` | two or more unmarked code-shaped boxes: not counted as a code box; a short box with a search-like NAME is counted (the sign-in is then asked of the user) |

`tools/payment_step.py`, `tools/verification.py`, `tools/keypad.py`, `tools/vault_tools.py`, `tools/takeover.py`, `tools/page_notes.py` and
`signin_state.py` are in GUARDED. `tools/browser.py` is not a guarded module (it carries other code): `test_the_browser_page_field_word_lists_are_gone`
checks that the names of the old lists are gone and that the field scripts hold no non-Latin letter. The tests that ran word fixtures now use
`tests/fake_page_judge.py`, a stand-in for the owner's model that holds its words in the test, never in the server.

## hc-rest (2026-11-17): brand scopes, stopwords, the don't-tell list, rule rewrites, per-service rules, app parsing
Items M8 (mcp_oauth brands), L5 (search stopwords), M6 (`NEVER_HEADINGS` and the keyword match), L2 (`reflection._KO_DONT`), H21 (forbidden.py,
Phase E), A1-A3 (apps). Rule for all of them: when nothing can tell, the result is the safer one (nothing requested, nothing sent, a card).

| Item | What replaces it | When nothing can tell (fail closed) |
|---|---|---|
| M8 `mcp_oauth.OTHER_ASSISTANT` (claude, anthropic, chatgpt, openai, cursor dropped from the scopes a server lists) | The scope a server's own challenge names (`WWW-Authenticate: scope=`) is used as is. When only the metadata's `scopes_supported` is left, ONE cached request of the main model (`scope_pick.ScopePick`, strict JSON `{"scopes": [...]}`, cached per service + scope list) picks the scopes for this app's own use out of that list; every name it returns must be in the list, others are discarded | no model or any other answer: no scope is requested (the server then grants its default) |
| L5 `recall._STOP`, `_HANGUL`, `agent/toolbox._STOP` (English stopwords, Hangul prefix forms) | Document frequency: a query word found in more than `COMMON_SHARE` of the sections (recall) or of the tool descriptions (tool search, from a sample of `MIN_SAMPLE` tools) counts for nothing; recall's FTS prefix match stays for every script, no per-script rule | recall: no useful word, no rows (as before). Tool search: when every word is common, all of them count (never an empty answer) |
| M6 `proactive.NEVER_HEADINGS` + `keywords()` substring match of the owner's list against an item (proactive, followups, recheck, follow runner) | proactive: the classify request already receives the whole of NOTIFY_PREFS and drops what the owner said not to be told; the word pre-filter is deleted. followups, recheck and the follow runner: `notify_block.NotifyBlock`, ONE cached main-model request per (the owner's saved rules, the item text), strict JSON `{"blocked": bool}`; asked only when the owner's saved copy holds a line that is not a heading or the unedited template. No heading name is read in code | the item is not told this pass and not marked (it is asked again next pass); never "allowed" |
| L2 `reflection._KO_DONT`, `_KO_DO`, `_EVIDENCE_LABEL` (rewrites of a stored rule's wording) | deleted; the nightly review's prompt already says to write a rule as a plain statement, never as an order | the rule is stored as the model wrote it |
| H21 `forbidden.RULES` (21 service tables in code) | data file `server/content/forbidden_rules.json` (per preset: methods, path pattern, body pattern, why, mode, doc). `forbidden.py` keeps the engine (`match`, `refusal`, `classify`, `normalised_path`) and loads the file | a data file that is missing or unreadable raises at import (the server does not start with no rules) |
| A1 `FollowRules` check-in words (Kotlin + Swift) | the plan's own `prep` entry carries `kind` (an enum in the follow plan prompt); the app reads `prep.kind == "check_in"` | no kind: the generic line |
| A2 "already connected" in the connect_service tool text | the tool's JSON result carries `already: true`; the apps read the field | no field: no chip |
| A3 "Saved the key for 'x' in the keychain (hosts: ...)" parsed in the apps | the tool's JSON result carries `service` and `hosts`; the apps read the fields | no fields: no chip |

`mcp_oauth.py`, `scope_pick.py`, `recall.py`, `notify_block.py` and `forbidden.py` are in GUARDED (`tests/test_no_word_lists.py`).
`agent/toolbox.py`, `reflection.py`, `followups.py`, `recheck.py` and `proactive.py` no longer read a human word to decide, but they hold state and
field names (and proactive the stored Korean urgency values, item L3, not in this run), which the "four plain words" check cannot tell from words: not guarded.

`tests/test_no_word_lists.py` also scans the JS strings of the GUARDED modules (`test_no_page_script_literal_carries_a_word`): the quoted strings and regex literals of a page script may hold only STANDARD / JS_STANDARD names (HTML, ARIA, autocomplete tokens, CSS values, DOM events, JS keywords, vendor element names, the server's own `data-kelo-*` markers).

## hc-final-a (2026-11-17): the last hard-coded words, batch A
Evidence: `~/cc-work/kelo-agents/reviews/hardcoding-final-2026-11-17.md` (N1-N3, L-a to L-d). Rule: decide by structure, a protocol value, or the
model's enum answer; never by a word of a language. Fail closed.

| Item | What replaced it | When nothing can tell (fail closed) |
|---|---|---|
| A `workers.py` cookie advice (the English and the Korean word for cookie, in the needs_input summary) | `report(need=...)` with the enum `REPORT_NEEDS = (answer, site_data_reset)`; the code carries `need` like `deadline` and branches on `need == "site_data_reset"`; the summary is never read | no `need` (or any other value): the generic "tell the user what is needed" line |
| B `tools/browser.py` `secret` by label words (`passw`, `pw` and the Korean word for password) | a typed value is secret when its field is a password field (type or `*password` autocomplete); when the field cannot be read at all and something is typed into it, it is secret too | masked. A click on an unreadable element is not secret (it types nothing) |
| C `chat_images.SECURE_RECTS_JS` field names (`card`, `cvc`, `cvv`, `pin`) | the password type, the autocomplete tokens (as before), any input drawn masked (`-webkit-text-security` not `none`), and every visible input or select of a card form (the form of a `cc-*` field, else the nearest box holding two or more fields) | blur more: the card form's other fields are covered too |
| D `tools/payment_step.py` countdown by class/id words (`timer`, `countdown`) | `[role=timer]` and `<time>`, plus a leaf element whose whole text is a clock shape (`m:ss`, `mm:ss`, `h:mm:ss`) | a clock shape next to any label still counts (the shape is the evidence; the label is ignored) |
| E `tools/page_notes.py` cookie/consent id and class probes, `CLOSE_CLASS`, `COOKIE` | removed, with no replacement by word: a bar along an edge (a consent bar, a sticky header, a tab bar) is not a layer at all (by geometry a consent bar and a header with a cart icon in its corner look alike; the agent reads the bar in the page, as the reference agent's model does). Close controls: `command=close`, `data-dismiss`, `data-bs-dismiss`, or an icon-only control in the top corner (as before); a class or id name is never a close control | a layer with only word controls is reported and not pressed (as before) |
| Guard | `JS_STANDARD` loses `cookie consent gdpr modal popup layer countdown`; the ARIA `aria-modal`, the WHATWG `cc-*` tokens, `webkit-text-security`, and the job status codes of `workers.py` are named as standards or code values. `workers.py` and `chat_images.py` join GUARDED. `timer` stays in `JS_STANDARD` (it is the ARIA role `timer`, a standard name) | - |
