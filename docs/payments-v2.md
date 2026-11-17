# Payments v2: order terms, approval contract, attempt journal (design, 2026-11-16)

Owner decisions (2026-11-16): take the studied reference agent's payment LOGIC and adapt it; every name is Kelo's own. Evidence with
paths and quotes is kept in the private work notes (payments evidence, sections a-f). Builds on feat/nwl-a1 (checkout_review /
checkout_submit, structural checkout detection, tools/checkout.py, tools/purchase.py:594-735). One model for everything (the owner's
main model). No word lists (docs/no-word-lists.md).

## Logic taken over (evidence section in brackets)
1. Two separate checks [a1]: an ORDER CHECK (does the cart match what the owner asked: items, variants, quantities, delivery, dates,
   address, cancellation terms, other obligations) and a PAYMENT APPROVAL (money: the owner's card, or a grant). An order check is not
   a payment approval.
2. What a payment approval covers [a2]: every page of the same checkout while the currency is the same and the total and any
   conditional charge stay within the approved amounts, on the same merchant origin (scheme + exact host, so another subdomain is
   another site). A higher total, a higher conditional charge, another currency or another origin needs a NEW payment approval. A
   lower price never allows a different product.
3. What needs a new order check [a2]: any change of items, variants, quantities, delivery, dates, address, cancellation terms or other
   obligations, even when the price stays within the approval. A change of only the total after a confirmed order check needs no new
   order check (record it as a terms update), but the payment approval must still cover it.
4. Draft then ready [b2, b3]: terms are first recorded as a draft (no approval asked), checked, then recorded as ready (this asks for the
   payment approval when nothing covers it). Submitting is refused unless the latest terms are ready; a later draft replaces ready terms.
5. Re-verification at submit [a4]: the pay press re-reads the live page and refuses when the origin left the approved merchant, the
   amount due exceeds the approval, the currency changed, the approval expired, or the terms differ from the approved ones. Every
   refusal says nothing was paid and what to do (review again; never invent a higher total).
6. Journal before the press, unknown outcome, no replacement [d1-d3]: an attempt row is written before the press; after it the
   outcome is placed / not_placed / unknown. While an attempt for the task is unknown: no new pay press, no replacement purchase, no
   reload of that tab. A retry only after not_placed is recorded with its evidence.
7. Later checks [d4]: an unknown outcome schedules one check about 10 minutes later (a one-time job that does not pay, it only looks
   for the order), then one final check about 30 minutes after that; then tell the owner to look in their mail or account and stop.
8. One-order pre-approval [e1]: before checkout the owner can approve one order spec (intent, items, requirements, shipping, merchant
   policy, choices left to Kelo, an all-in max total with currency, expiry default 30 days). One per task or schedule; approving
   another revokes the previous. Checkout compares the live cart to the spec; outside it -> ordinary payment approval.
9. Scheduled payment allow [e2]: an Allow on a scheduled task's payment card also covers that schedule's later purchases from the same
   merchant origin with the same payment method, until the schedule is deleted or the owner removes it. No other background work can
   pre-approve or batch purchase approval. Each such purchase still gets its own approval contract (rule 2 and 5 apply).
10. Checkout safety net [c1, c2]: a pay press outside the checkout tools must not happen. Kelo adaptation: the structural refusal of
   nwl-a1 stays; where structure cannot tell (a submit control on a page without checkout structure, any Android tap), the judge call
   of docs/no-word-lists.md principle 3 decides (the reference agent has no phone control at all, so this part is Kelo's own).
11. Checkout worker [b1]: the planning agent has no pay tool; a pay hand-off starts a checkout worker that alone has the order and pay
   tools and never talks to the owner. Phase P4, after the browser worker design (browser as a separate worker).

## Data (store migration, next schema number)
- `order_terms`: id TEXT PK ("ot_"+hex), owner TEXT (browser owner key), task TEXT (turn/job/schedule id), status TEXT
  (draft|ready|superseded), merchant_origin TEXT, merchant_name TEXT, items JSON [{name, qty, unit_minor, line_minor, variants[],
  obligations[]}], obligations JSON {delivery, dates, address_digest, cancellation, other[]}, currency TEXT (ISO 4217, never guessed
  from a symbol or country), total_minor INT, conditional_minor INT, conditional_terms TEXT, payment_label TEXT (words, never a
  number), terms_digest TEXT (sha256 of items+obligations; addresses as digests only), checked_at REAL NULL (order check passed),
  created_at REAL.
- `pay_approvals`: id TEXT PK ("pa_"+hex), terms_id, owner, task, merchant_origin, approved_total_minor, approved_conditional_minor,
  currency, terms_digest, source TEXT (card|task_grant|schedule_allow|preapproval|auto_rule), approval_id TEXT NULL (the card),
  signed INT (phone signature present), expires_at REAL, state TEXT (approved|declined|expired|used|revoked), created_at.
- `pay_attempts`: id TEXT PK ("pt_"+hex), approval_id, terms_id, owner, task, merchant_origin, state TEXT
  (reserved|pressed|placed|not_placed|unknown), pressed_at REAL, evidence TEXT, receipt_digest TEXT, receipt_text TEXT,
  check_jobs JSON [job ids], created_at, updated_at.
- `order_preapprovals`: id, owner_kind (task|schedule), owner_id, spec JSON (rule 8 fields), max_total_minor, currency, expires_at,
  state (active|used|revoked|expired), card approval_id, created_at. Unique active row per (owner_kind, owner_id).
- `schedule_pay_allows`: schedule_id, merchant_origin, payment_label, approval_id, created_at; deleted with the schedule; listed and
  removable in the owner's permissions page (GET/DELETE /api/permissions/pay-allows).

## Tools (Kelo names)
- `checkout_review` gains `status: "draft"|"ready"` (default draft) and the order fields: items[{name, qty, unit_price, variants[],
  obligations[]}], delivery, dates, ship_to (shown, stored as digest), cancellation, conditional_total, conditional_terms, currency
  (required, ISO), total (required). Draft: records `order_terms` (status draft), runs the ORDER CHECK, returns its answer. Ready:
  requires a passed order check on the same terms_digest (or a terms update that only changed the total), then looks for cover in
  this order: active preapproval spec -> schedule_pay_allow -> task grant / auto rule -> else the payment card (signed on the phone).
  Writes `pay_approvals` and returns {review_id: pay approval id, approved}.
- ORDER CHECK (adaptation of the reference agent's cart review by its main agent): one request to the main model with a fresh context:
  the owner's own request words for this task (owner messages or the saved job request only; outside text never), the recorded terms,
  and the preapproval spec if any. Strict JSON {"verdict": "confirm"|"change"|"open_choice", "reason", "change"?, "choice"?}.
  confirm -> checked_at set; change -> returned to the agent; open_choice -> the agent asks the owner (chat) or, unattended, stops and
  names the open choice. No model / bad JSON -> open_choice (fail closed).
- `checkout_submit(review_id)`: before the press, re-read the page and refuse (nothing pressed) when any holds, each with its own
  message: origin != merchant_origin; amount due on the page > approved_total_minor; currency differs; conditional amount > approved;
  approval expired (approval TTL = the card's lifetime, at most 20 minutes for a card, the spec's expires_at for a preapproval);
  approval state != approved; an attempt of this owner/task is pressed or unknown; recorded control not found once (existing check).
  Then INSERT pay_attempts(state reserved) -> press -> state pressed. `purchase_result` sets placed / not_placed / unknown with
  evidence (the merchant's confirmation only counts as placed; a spinner, an accepted card or a payment-provider success page does not).
- Unknown: schedule a one-time follow-up job (followups.py) at +10 min whose prompt only looks for the order in the same browser task
  (no pay tools in that run); if still unknown, one final job at +30 min; then a message telling the owner to check mail or the
  account, and the attempt stays unknown until the owner says otherwise.
- Blocks while pressed/unknown for that owner/task: checkout_review(status ready), checkout_submit, and browser reload/navigate of that
  tab (message: the order may have gone through; find it first).
- `order_preapprove(intent, items[{description, qty}], requirements[], shipping, merchant_policy, delegated_choices[], max_total{amount,
  currency}, expires_at?)`: only after the owner confirmed the spec in chat; shows a card with intent, all-in cap and expiry; one
  active per task/schedule (a new one revokes the old); the agent cannot revoke; at expiry a schedule run that needs it stops and
  says so (never asks for a new one from a scheduled run).

## Safety net for pay presses outside the checkout tools (closes the nwl-a1 gap)
- Web: a click on a submit control (button type=submit, input type=submit, a control inside a form that the press would submit, or a
  POST that changes the URL) on a page WITHOUT checkout structure goes through the judge call (docs/no-word-lists.md principle 3:
  main model, strict JSON {"spends_money", "sends", "deletes", "account_security", "signs_up"}, cached per page path + control role +
  name). spends_money true -> refused with the checkout tools message. Judge unavailable -> ask (card).
- Android: every tap/swipe-as-tap/Enter on a clickable node goes through the same judge with the screen's node tree (package, class,
  text, resource ids) and the target node; spends_money -> card (payment card, phone-signed); judge unavailable -> card.
- This replaces the pay-word paths that nwl-a1 removed; nwl-a1 merges together with P1.

## Phases
- P1 (with nwl-a1): order_terms + pay_approvals tables, draft/ready + ORDER CHECK, submit re-verification, the safety net (web submit
  judge + Android judge). Tests listed below.
- P2: pay_attempts journal, unknown outcome, +10/+30 checks, replacement and reload blocks.
- P3: order_preapprove + schedule_pay_allows (+ permissions page API; apps show and remove them).
- P4: checkout worker split (after the browser-worker design).

## Tests (fake model; real Chromium where nwl-a1 already does)
Higher total after approval -> refused, nothing pressed; other currency -> refused; subdomain change -> refused; expired approval ->
refused; item quantity changed with lower total -> order check required again; total-only change after confirmed check -> new
approval only if above approved; draft after ready -> submit refused; order check open_choice unattended -> stops and names the
choice; attempt unknown -> second submit refused, reload refused, follow-up jobs at +10 and +30 created; not_placed with evidence ->
retry allowed; preapproval within cap incl. shipping -> no card; above cap -> card; second preapproval revokes the first; schedule
allow same origin + same payment label -> covered, other card -> card; submit on a page without checkout structure judged
spends_money -> refused; judge down -> card; Android pay-looking tap judged spends_money -> payment card; hygiene: no word lists.

## P3 build notes (2026-11-16, owner decisions on the board)
Owner decisions: ONE order pre-approved (items, terms, an all-in maximum incl. tax and shipping, expiry at most 30 days); a scheduled
payment is covered only for the same shop (merchant origin) AND the same payment method; a settings switch turns pre-approval off
(it covers both: the one-order pre-approval and the schedule allow).

Files: `pay_preapprovals.py` (tables `order_preapprovals`, `schedule_pay_allows`; class `Preapprovals`; the switch is the store meta key
`preapprove_off`), the tool `order_preapprove` (tools/preapprove.py), hooks in tools/purchase.py (`judge`, `confirm_inner`,
`_checkout_tools`), `order_check.py` (the spec goes into the order check), `api/grants.py` (permissions API).

Logic (evidence e1, e2):
- `order_preapprove` shows ONE card (describe + `preapproval` extra: intent, items, requirements, all-in cap, expiry). Allow stores the
  spec; a new one for the same task or schedule revokes the old one; the agent has no revoke tool. Not available in a run nobody
  watches (it cannot ask) unless it passes the `schedule_id` of the schedule being set up in chat. Expiry default and maximum: 30 days.
- Which order the spec covers is NOT decided by words in code: the ORDER CHECK (main model, same request as before) also receives the
  spec and answers `within_spec` (boolean). Only confirm + within_spec true records the terms digest and the merchant origin on the
  spec row (`matched_digest`, `matched_origin`). Covering at `checkout_review(ready)` is then pure structure: switch on, spec active
  and not expired, owner matches (task id, or the running schedule), same digest and origin, same currency, `total + conditional` <=
  the cap. Above the cap, another currency or origin, changed items -> the ordinary card.
- Schedule allow: when the owner answers Allow on a payment card in a schedule run, a row (schedule, origin, payment label, currency)
  is kept. Later runs of that schedule: a passed order check + same origin + same payment label + same currency -> covered with no
  card (source `schedule_allow`); another shop or another payment method -> card. Deleted with the schedule (`on_task_changed`) or by
  the owner. Each such purchase still gets its own approval contract (origin, total, expiry re-checks at the press).
- Covered approvals are written with source `preapproval` / `schedule_allow` (approval_id = the spec's / allow's id). A preapproval
  approval lives until the spec's expiry; at the press `checkout_submit` refuses when the spec or the allow is gone meanwhile
  (code `preapproval`: revoked or removed) and marks a spec `used` when the press happens (one order).
- A card kept only on the phone, a paused wallet or a locked vault still asks (as for task grants).
- Expired spec in an unattended schedule run with no other cover: the run stops and says so; it never asks for a new one.
- Switch off: nothing is covered, `order_preapprove` refuses, existing rows stay (listed, removable) but cover nothing.

API (apps show and remove them; app work listed in the report):
- GET /api/permissions/preapprovals -> {"enabled", "items": [{"id", "owner_kind", "owner_id", "owner_name", "intent", "items", "requirements",
  "shipping", "merchant_policy", "delegated_choices", "max_total", "currency", "max_total_text", "expires_at", "state", "created_at"}]}
  (active ones only), DELETE /api/permissions/preapprovals/{id} -> {"ok"} (404 gone).
- GET /api/permissions/pay-allows -> {"items": [{"id", "schedule_id", "schedule_name", "merchant_origin", "payment_label", "currency", "created_at"}]},
  DELETE /api/permissions/pay-allows/{id} -> {"ok"}.
- GET /api/permissions/payment-cover -> {"enabled"}; PUT same {"enabled": bool} -> {"enabled"} (the switch).
