# Connector rules in data, and per-room discretion (design, 2026-11-16)

Owner decisions (board cards "connector rules as a settings file" and "per-room careful-speech rules", 2026-11-16): what a connector
method does (sends, posts, deletes, forwards), how strongly it is protected, and where its recipient is are DECLARED IN DATA, not in
code and not by words. When Kelo talks for the owner in a messenger room, that room has its own rules of what may be said; repeated
approvals only ever become a SUGGESTED rule, never an automatic one. Logic taken from the studied reference agent (recipients evidence,
sections a-d: manifest `approval_policy`, `preapproval` bindings, recipient scope, the discretion rules and "repeated approvals can
suggest a rule"); every name, file and text below is Kelo's own.

## Part 1: connector rules

### Logic taken over (evidence sections)
- (c) Every method of a connector manifest carries an approval policy from a small enum (fresh consent only / fresh consent or task
  allow / ordinary) and the tool says which CLI or API request maps to which method.
- (b) A task approval is bound to the recipients, which the checker reads from the call itself at execution time.
- (a) An allowance is per recipient and per account; a messenger allowance is per conversation; some classes never get a lasting allow.
- Gap left by `docs/data-trail.md` "Bound recipients": sends through connectors carried `connector:<name>` as their only target, so no
  recipient list, no per-recipient allow and no recipient-bound task permission existed for a messenger send.

### Files
- `server/src/assistant/data/connector_rules.json`: the built-in declarations (Slack, Telegram, Discord today). Data only.
- `server/src/assistant/connector_rules.py`: loads and checks the data (`parse_rules`), finds the declared method of a call and reads its
  recipients (`ConnectorRules`). A Nest package may carry the same section (`rules` in its manifest, `nest_manifest`/`packages.py`);
  `connector_rules.package_source` (set in server.py) gives the rules of an installed package.
- `effects.effect_of` uses it: `Effect` gains `policy` (and `method`), and the targets of a declared send are its bound recipients.

### Schema
```
{"version": 1, "connectors": {"<catalog id>": [ <method>, ... ]}}
method = {
  "id":        short name of the method (shown nowhere but logs),
  "via":       "request" (connector_send: an HTTP request through the saved key) | "tool" (mcp_call_tool: an MCP tool),
  "http":      request: the HTTP method ("POST"),          "path":  request: the END of the URL path, "/"-separated; a segment
                                                                    "{recipient}" is captured, "{}" matches any one segment,
  "tool":      tool: the MCP tool's own name,
  "kind":      "send" (send, post, forward, reply) | "change" | "delete",
  "policy":    "standing" | "task" | "fresh"  (default "standing"),
  "recipient": where the recipient is: a spec or a list of specs: "path" (the captured segment), "json.<dotted key>" (request body),
               "arguments.<dotted key>" (MCP arguments), "query.<key>"
}
```
Policy, from weakest to strongest protection (each one adds a refusal of a way to skip the card; the owner's own click on a card is
always possible):
- `standing`: today's behaviour. The owner's per-connector "allow", an owner-made per-recipient allow and a task permission may cover it.
- `task`: a task permission that names the recipient may cover it; neither the connector's "allow" setting nor a per-recipient lasting
  allow does (the kind of send that must be asked again unless one task was set up for it).
- `fresh`: a card every time. No task permission, no per-recipient allow, no "allow" setting covers it (posting as the owner in a place
  nobody reviewed, deleting what others can see).
A declaration can only make a call STRICTER: the kind used is the stricter of the declared and the derived one (effects.KIND_RANK), so a
package cannot call its send a read. A call that matches no declaration behaves exactly as before.

### Bound recipients
A matched call's targets are `connector:<name>#<recipient>` (recipient case-folded; an address list is split like a mail's). A task
permission naming the recipient covers only that recipient; one naming the connector whole (`connector:<name>`) still covers all, as
before (the owner chose that breadth). A declared recipient that cannot be read from the call leaves the target `connector:<name>`,
which a recipient-bound permission does not cover: it asks (fail closed). The data trail (`data_trail.Outbound.where`) sees the same
targets, so "always for this recipient" now works for a messenger room.

### Enforcement points (one decision each, no lists)
1. `permissions.Gate.verdict`: a mode "allow" does not skip the card for a method whose policy is not `standing`.
2. `schedule_grants.action_check`: policy `fresh` -> `ActionCheck(None, "task_fresh_only")`.
3. `data_trail._review`: the per-recipient allow (rule 5) applies only to `standing`; the task permission (rule 6) not to `fresh`.

### Tests
`tests/test_connector_rules.py`: the shipped data parses and every connector id exists in the catalog; request matching with a bot token
segment in the path, captured and JSON recipients, a recipient list; stricter-only kinds; a package's rules through the hook; refusal of
a bad schema (unknown policy, kind, via, missing path/tool); effect targets and policy; task permission naming one room covers only it,
the connector named whole covers all; `fresh` ignores a task permission and a recipient allow; `task` ignores a recipient allow; the
gate keeps the card under "allow"; no word list in the module (tests/test_no_word_lists.py).

## Part 2: per-room discretion

### Logic taken over
The studied reference agent (section d): per side chat a policy of what may be shared, as `never` and `allow` lines edited only by the main agent (the worker
in the room has no tool for it), changes announced with the exact lines added and removed; every outgoing message is checked against
the policy before it goes; a failed message is not sent; repeated approvals "can suggest a useful rule to offer, but do not make one
permanent without the user's instruction". Nothing anyone else writes in the room can change the rules.

### Kelo
- Stored in the channel's state, per room: `groups[<room>]["rules"] = [{"id", "kind": "never"|"allow", "title", "text"}]` (title one
  line, text one line, at most 20 rules, 200 characters each), plus `suggestions` and `held`. Code: `channels/rooms.py` (`RoomRules`).
- The group turn's system prompt gets a "Rules of this room" block listing the rules (owner's words verbatim); a group turn is the
  model with three public tools and no owner data, so `allow` lines are statements the owner wants said (the text itself) and `never`
  lines are subjects to keep out.
- Check before saying: when the room has `never` lines the final reply goes to the owner's main model (`ModelJson.ask`, the same
  judge as the step judge) with the rules; strict JSON `{"breaks": ["<rule id>", ...]}`; no model, an error or anything else counts as
  "unclear" and the reply is HELD (fail closed). A held reply is not said; the room gets a short neutral line; the owner is told in
  the 1:1 chat which room, which rules (titles) or which privacy categories, with the held text, and may release it.
- The privacy guard's hold (`privacy.scan`) goes the same way: held, not lost.
- Releasing (the `messenger` tool, action `room_release`, asks the owner): posts the held reply to the room. Each release counts under
  its reason key (the sorted rule ids, or the sorted privacy categories). The second release of the same key in the same room adds a
  SUGGESTION `{"kind": "allow", "title", "text", "reason", "count"}` to the room; it is shown to the owner (the `messenger` tool, action
  `room_rules`) and becomes a rule only through `room_suggestion_accept` (asks the owner). Never automatic.
- The agent's tool for rules (`messenger`, only the main agent): `room_rules` (list rules, suggestions, held), `room_rule_set`
  (kind, title, text; an `allow` rule asks the owner, a `never` rule applies at once and is announced), `room_rule_remove` (asks),
  `room_suggestion_accept` / `room_suggestion_dismiss`, `room_release`. The group worker has none of these.
- The result of a change lists the lines added and removed so the agent can tell the owner exactly what changed.

### Tests
`tests/test_channels_rooms.py`: rules stored per room and kept apart from another room; the system prompt carries them; a reply that
breaks a `never` rule (scripted judge) is held, not sent; judge error holds; a clean reply passes; a room without rules makes no judge
call; release posts it; the second release of the same key suggests, the first does not, accepting adds a rule, dismissing drops it;
the tool changes (ask vs apply); another room never sees the rules; unpair drops everything.
