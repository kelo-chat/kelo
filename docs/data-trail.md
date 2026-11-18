# Data trail: code-first guard against data leaving the owner's world (design, 2026-11-16)

Owner decisions (2026-11-16): guard outgoing data with CODE first (no model call, no word lists); a model guard may be added later as an
option. Take the studied reference agent's logic and adapt it, names are Kelo's own. Its layers: a code taint (per command process
tree, labels carried through files, fails closed) under a model guard on each tool call with three rules: (1) private data sent beyond
what the user asked, (2) unrequested private data typed into a web form, (3) a credential sent outside its authentication scope. Its
browser logins type a credential handle only after https, origin binding, a masked password field and a typing check. Evidence is in
the private work notes (secrets evidence, sections A-C).

## Pieces
1. Labels (`server/src/assistant/data_trail.py`). Every tool result that carries data from somewhere gets a label:
   `TrailLabel(id, kind, source_id, party, title, private, outside, at)`.
   - `kind` comes from the TOOL's declaration (a new `trail` field on `Tool`, set where each tool is defined: mail, file, memory,
     contacts, calendar, photos, messages, notes, vault, profile, web, channel, device, connector:<id>), never from the text.
   - `source_id`: the item's own id (message id, path, event id, contact id, URL). `party`: the address or account the item came from
     when the source has one (mail From, chat author). `title`: the item's own name (subject, file name, event title) for rule 4 below.
   - `private`: true for every owner source (mail, files, memory, contacts, calendar, photos, messages, notes, profile, vault, device,
     connectors that read the owner's accounts). `outside`: true when the content is fenced as outside text today (agent/fence.py).
   - Fingerprints: the text is normalised (NFKC, case-folded, whitespace collapsed) and cut into overlapping character runs of 12
     (language-neutral, works for any script) hashed with a fast hash; plus exact "value tokens" (addresses, numbers of 6+ digits,
     URLs). Kept per turn and per job (store table `trail_labels` with the run id, deleted with the run's records).
2. Outbound reader. Every call whose effect kind sends, posts, uploads, shares, submits a form or opens a URL (effects.py decides the
   kind from the tool, as today) is read before it runs: body, subject, recipients, attachments' text, form values, the full URL
   including host labels, path, query and fragment. Common encodings are undone before matching (percent, base64 runs of 16+, hex
   runs of 32+, punycode). The same fingerprints are computed.
3. Matching. A label is "in" the outgoing call when 3+ of its runs or any value token appear. Result: the list of labels going out,
   each with the destination (recipient address, chat, host).

## Decisions (in this order, first match wins; all are comparisons of facts, no lists)
1. Secret values (rule 3): a stored vault value or connector key (exact or decoded) in any outgoing call -> allowed only to that
   credential's own hosts (vault binding, credentials.py placements); otherwise REFUSED (not a card), message names the credential.
2. Owner's own destination: recipient equals one of the owner's connected account addresses, the owner's own chat/channel or notes
   -> pass.
3. Back to its origin: label.party equals the recipient (replying to someone with their own words) -> pass.
4. Picked by the owner: label.source_id is in this turn's picked set (items the owner attached or shared in the app; the app sends their
   ids) OR the agent declared it in the call's `uses` argument and the label's title occurs literally in the owner's own words this
   turn (owner messages or the saved job request; outside text never counts) -> pass.
5. Recipient allow: an owner-made allow for (recipient, label kind) exists (created from the card's "always for this recipient"
   button; listed and removable in the owner's permissions page; the agent cannot create or remove one) -> pass.
6. Task permission: the task/schedule permission names this recipient and this kind -> pass.
7. Otherwise -> card: "<kind> from <title> would go to <destination>" per label (titles only, never the content), with Allow once /
   Always for this recipient / Deny.
- Private form values (rule 2): a value typed into a page field that matches one of the owner's private values (the owner copies made
  by audit fix F2) passes only when the field's HTML autocomplete token is of that value's type (WHATWG token table: a standard, not
  words) and the page origin is the task's site or covered by a grant; else card. Fields without a token -> card for private values.
- Outside text (precise taint, item 13): today one outside result latches the whole turn (agent/loop.py saw_outside). New: a send needs
  the outside-text card only when its TARGET or CONTENT derives from an outside label (target string found in outside text and not in
  the owner's words; or outside fingerprints in the content). A send made later in the same turn that uses only owner data no longer
  asks because of earlier outside text. Files the agent saves from outside content get the label in an extended attribute
  (`user.kelo_trail`, label ids) and reading them carries the label on.
- Fail closed: labels missing for a private tool result, the matcher failing, or a broken label store -> the outgoing call asks (card).

## Browser login checks (item 11)
`fill_login` (tools/browser.py) and `browser_login` (tools/vault_tools.py) additionally check, at the moment of typing: the target is an
`input` whose type is password and is rendered masked (computed style text-security not none, type unchanged since tagging); page is
https and on the credential's site (existing); after typing, the field's value length equals the secret's length (read the length only,
never the value). Failing any -> nothing typed / field cleared, a precise error (not masked, not applied, ambiguous target), no retry.

## Phases
- T1: labels + Tool.trail declarations + outbound reader + matching + decisions 1-7 + fail closed + permissions API for recipient
  allows (GET/DELETE /api/permissions/recipients) + card buttons.
- T2: private form values + browser login checks.
- T3: precise outside rule (replace the turn latch) + file labels. Built 2026-11-16, see "T3 as built" below.
- Bound recipients (2026-11-16): see "Bound recipients" below.
- Apps after T1/T3: the card's new button and the permissions list (Android first, desktop, iPhone).

## Tests
Secret to its own host passes, to another host refused, base64/percent-encoded refused; reply quoting the sender passes; forwarding that
mail to a stranger asks; owner-attached photo to mom passes, a contact list added asks; declared `uses` with title in owner words
passes, title only in outside text asks; recipient allow covers only that recipient and kind; Korean, Japanese and Arabic texts match by
character runs; form: home address into shipping autocomplete on the task's site passes, into a newsletter name field asks; password
into an unmasked field refused; typing length mismatch reported; outside text earlier in the turn does not make an owner-data send ask;
target found only in outside text asks; label store error -> card; hygiene: no word lists in data_trail.py.

## T3 as built (precise outside text)
Outside labels. Every fenced tool result (also of a tool with no `trail` declaration) and every fenced tool result already in a run's
starting history gets one label with `outside` set (`DataTrail.label_outside`; id = hash of the text, so a second pass over the same
history adds nothing). It stores the 12-character runs and the exact tokens of the text plus every target the text names (addresses,
numbers as `norm_target` writes them, `@handle`, `site:<domain>`, the same extraction the owner's-words rule uses). A result a tool
declares to be the owner's own data (`own_data`) is not outside text, as before.

The rule (`DataTrail.derive`, called through `OutsideReview(derive=...)`; `OutsideTextRule.needs_card` takes the result). A send, change
or delete after outside text needs the outside-text card only when
1. one of its targets is not named by the owner (as before: owner's words, task permission, order site, the owner's own destination)
   AND that target comes from outside text: the target is among the targets an outside label names, or it has no text form to compare
   (`connector:<name>`, `calendar`: no way to prove it did not come from the outside text, so it asks as before); OR
2. one of its targets is not named by the owner AND the content (body, typed value, full URL) carries 3+ runs or a value token of an
   outside label, runs and tokens the owner's own words of this turn contain not counted.
A call whose unnamed target is neither found in outside text nor made of it passes this rule (the other layers, the data-trail
review and the connector's own approval, still apply). Nothing outside yet in the run: no card. Group turns (what others wrote IS the
request) keep the turn latch. Fail closed: a label store or matcher error makes every unnamed target count as derived.
A send later in the turn that uses only owner data and an owner-named target no longer asks because of earlier outside text.

File labels. A file the agent writes (`write_file`, also the memory step's) takes the labels of the run whose text is in the content
(`DataTrail.mark_file`): the extended attribute `user.kelo_trail` holds `{"v":1,"labels":[{id, kind, source_id, party, title,
outside}]}` (at most 8; macOS `xattr`, Linux `setxattr`, Windows the stream `:kelo_trail`; a content with none of them clears the
attribute). Reading such a file (`read_file`) carries them on: an `outside` label makes the result fenced (so the run gets an outside
label for it), a private label is noted again with its kind, party and title. A filesystem that cannot hold the attribute fails
closed: the run is marked broken and its outgoing calls ask.

## Bound recipients
Owner decision (board card "bind task permission to the exact recipients", 2026-11-16). Logic taken over from the studied reference
agent (recipients evidence, preapproval `recipients_and_thread` + the frozen prepared send): an allowance is per recipient, a send is
covered only when ALL its recipients are covered, and an approval is bound to the exact recipients, the thread and the frozen body, so
anything changed afterwards asks again. Names and texts are Kelo's own.
- Real recipients (`prepared_send.recipients_of`): the addresses the message will really go to: every To, Cc and Bcc, each argument a
  string or a list, each element parsed as an address list (`Name <a@b>`, "a@b, c@d" are split; a piece that is no address stays as
  written and so matches nothing). `effects.effect_of` uses it for `email_send`, so the task permission (`schedule_grants.action_check`
  asks `all(_target_ok)`), the owner's per-recipient allows, the data-trail review and the outside-text rule all see the same list. The
  mail tool gained `bcc` (it had none: a model's bcc used to be dropped by the tool but not read by the checks).
- A task or schedule permission that names recipients covers a send only when every real recipient of that send is named, checked at
  the moment of sending, never from the task's description or the model's statement.
- Frozen send (`prepared_send.PreparedSend`): tool, account, real recipients (sorted), thread (`reply_to_uid`), subject and body, hashed
  into one digest. The loop freezes it when the gate has judged the call and checks it again after the owner's answer, right before the
  tool runs. A different digest (anything between the card and the run changed the call) is not run on the old answer: the owner is
  asked again with the changed call, once; a decline stops it.
- Not built: sends through connectors and MCP tools carry their target in the service's own arguments; no structure tells a channel
  from a message there, so they keep `connector:<name>` as the target (a recipient list needs a per-connector declaration, later).

Tests: bcc and display-name forms are all read, a bcc outside the permission asks, the same send with every recipient named passes, a
changed body or recipient after approval asks again, an unchanged call runs once; outside text earlier in the turn does not make an
owner-named send ask, a target only in outside text asks, content copied from outside text to an unnamed target asks, files written
from outside content are labelled and fenced when read, an unsupported attribute store fails closed.

Memory the owner did not say (a mail, a page, a job report) is never kept: see memory-pipeline.md, "Owner's words only".
