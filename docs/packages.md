# Packages: skills and connectors as folders

A package is a folder that adds a **skill** (a written guide the agent reads) or a **connector** (an outside service the agent can
use) to Kelo. Drop the folder into the server's packages folder and the agent sees it on its next turn; the Connectors screen shows
it on its next refresh. No code change and no restart. Delete the folder and it is gone the same way.

New to this? Start with the short, copy-and-edit guide in [`server/examples/packages/README.md`](../server/examples/packages/README.md). To publish a
package for every Kelo server, with permissions the owner approves and screens for the apps, see [Kelo Nest](nest.md).

The server reads packages from these places:

| Where | What | Who writes it |
| --- | --- | --- |
| `server/content/skills/<id>/` (in the repo) | built-in skills | Kelo |
| code (`catalog.py`, `presets.py`, `mcp_oauth.py`, ...) | built-in connectors | Kelo |
| `<state>/custom_skills/<id>/` | custom skills (source `custom`) | the owner, through the app's approval (see "Custom skills") |
| `<state>/packages/<id>/` | installed skills and connectors | anyone |

`<state>` is the folder that holds `assistant.db` (`/state` in the Docker image). `ASSISTANT_PACKAGES_DIR` points somewhere else.

Installed packages are **untrusted data**. They never run code on the server: a skill is text, and a connector only *describes* a
service (where its MCP server is, or which hosts an API key may be sent to). Anything that must execute comes in as an MCP server
(hosted by its author today; a local sidecar container later), and the agent reaches it through the generic `mcp_list_tools` /
`mcp_call_tool` tools, each call approved by the user.

## The folder

```
<id>/
  kelo-package.json   the manifest (optional for a skill)
  SKILL.md            a skill's guide
  *.md                more pages of a skill, read on demand
  icon.png            optional, a square PNG (256 px is a good size)
```

Rules the server enforces (a package that breaks one is left out, and the reason is logged):

- The folder's name is the package's `id`.
- No symbolic links anywhere in the folder, no special files, at most 4 levels of subfolders.
- At most 64 files and 2 MiB in all; the manifest at most 64 KiB; `SKILL.md` and each `.md` page at most 128 KiB; the icon at most
  512 KiB and a real PNG.
- An id that a built-in skill or connector already uses is refused: **built-ins always win**.
- Unknown top-level manifest fields are ignored with a warning (so a newer manifest still loads). Unknown fields inside the `connector`,
  `connect`, `skill` or `strings` sections refuse the package.

## The manifest: `kelo-package.json`

```json
{
  "format": 1,
  "id": "example_notes",
  "kind": "connector",
  "version": "1.0.0",
  "name": "Example Notes",
  "description": "Notes and notebooks in Example Notes",
  "author": "Example Author",
  "homepage": "https://notes.example.com",
  "license": "MIT",
  "icon": "icon.png",
  "strings": { "fr": { "name": "Notes d'exemple", "description": "Notes et carnets dans Notes d'exemple" } },
  "connector": { ... }
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `format` | no (1) | manifest format; this server reads 1 |
| `id` | yes | skills: `a-z 0-9 -`; connectors: `a-z 0-9 _`, starting with a letter; 2–48 characters |
| `kind` | yes | `skill`, `connector` or `extension` (screens only; see [nest.md](nest.md)) |
| `version` | yes | `1.2.3` (an optional `-suffix` is allowed) |
| `name` | yes | up to 60 characters, one line |
| `description` | yes for connectors | one line, up to 300 characters (skills: up to 1024) |
| `author`, `homepage`, `license` | no | shown with the package; `homepage` must be https |
| `icon` | no | a `.png` file at the top of the folder |
| `strings` | no | per-language texts: `{"<lang>": {"name", "description", "blurb", "reason", "help", "key_label"}}` |
| `permissions`, `store`, `ui` | no | what the package may do, the entries it keeps, the screens it adds ([nest.md](nest.md), [nest-ui.md](nest-ui.md)) |

Languages are tags such as `ko`, `ja`, `pt-BR`, `zh-Hans`. The reader's language is picked the way the rest of Kelo picks it; a missing
text falls back to the English one in the manifest.

## Skill packages

A skill is a folder with `SKILL.md`, in the same style other agents use: front matter with `name` and `description`, then the guide.

```markdown
---
name: Trip packing list
description: Build a packing list for a trip from the destination, dates, weather and plans
requires: []
---
# Trip packing list
1. Ask only for what is missing ...
```

- `requires`: connector ids; when none of them is connected, the skill is listed as "not usable until the user connects what they need".
  The manifest may say the same as `"skill": {"requires": [...]}`.
- Without a manifest, the id is the folder's name and the version is `0.0.0`.
- Extra `.md` files at the top of the folder are offered to the agent by name (`read_skill(name, file)`). Subfolders (for example a
  `scripts/` folder from another agent's skill) are allowed but never read or run.

The agent sees installed skills in its prompt in a separate, delimited list marked as other people's text (each description cut to
200 characters, at most 40 entries), and `read_skill` returns an installed skill wrapped the same way. A skill cannot grant
permissions or skip approvals.

## Connector packages

A connector package is declarative: it says what the service is and how it connects. Five ways are open to packages (`oauth`, an
OAuth 2 API with PKCE, is described in [nest.md](nest.md#connectors-the-four-ways)):

### `mcp` — a hosted MCP server with OAuth sign-in

```json
"connector": {
  "regions": [],
  "featured": false,
  "connect": { "type": "mcp", "url": "https://mcp.notes.example.com/mcp", "blurb": "Finds and edits notes." }
}
```

The user signs in on the service's own page (OAuth with PKCE; the app registers itself automatically through a Client ID Metadata
Document or dynamic client registration, as for the built-in MCP connectors). Tokens are sealed in the vault as `pkg-<id>` and are
sent only to the host of `url`. Afterwards the agent lists and calls the server's tools with `mcp_list_tools` / `mcp_call_tool`.

### `key` — an API key the user makes on the service

```json
"connect": {
  "type": "key",
  "hosts": ["api.weather.example.com"],
  "placement": { "type": "query_param", "param": "appid" },
  "base_url": "https://api.weather.example.com/v1",
  "verify": "https://api.weather.example.com/v1/ping",
  "docs": "https://weather.example.com/docs",
  "api": "GET /forecast?q=<city> returns a 5-day forecast.",
  "help": ["Sign in at weather.example.com and open Account → API keys.", "Make a key and paste it here."],
  "key_label": "API key",
  "blurb": "Looks up forecasts for the places you ask about.",
  "mcp_url": "https://api.weather.example.com/mcp"
}
```

- `hosts` (1–5 public host names) are the **only** places the key is ever sent; every URL in the section must be on one of them.
- `placement`: `bearer_header` (the default), `{"type": "custom_header", "header": "X-Api-Key", "prefix": ""}`, or
  `{"type": "query_param", "param": "api_key"}`. Headers such as `Cookie` or `Host` are refused.
- `verify`: a GET that must answer 200 before the key is saved (optional; without it the key is saved unchecked).
- `api`, `base_url`, `docs`: notes for the agent. They reach it marked as the package author's text, cut to 400 characters.
- `mcp_url` (optional): an MCP endpoint on one of `hosts` that takes the same key, so the agent can also use `mcp_list_tools`.
- `key_pattern` (optional): the shape of this service's keys, e.g. `"wx_live_[A-Za-z0-9]{32}"`, so a key the owner pastes into chat
  is held back before it reaches a model and the app offers this connector's key form instead. Only a fixed prefix of at least 3
  characters followed by 1–4 bounded character classes (`[...]{n}` or `[...]{min,max}`, at most 200) is accepted, at least 16
  characters in all; anything else (alternation, groups, unbounded repeats) refuses the package.
- A key package has exactly one secret, the key itself, kept in the vault as `pkg-<id>`.

### `browser` — a site the agent opens in its own browser

```json
"connect": { "type": "browser", "url": "https://www.example.com", "blocks_bots": false }
```

Like the built-in browser sites, these are not listed on the Connectors screen; `connect_service` tells the agent to open the site,
and a login, when needed, is stored in the vault by the user. The browser can reach any public site anyway (through its egress proxy,
never the local network), so this adds no new access.

### `unsupported` — known, but not usable here

```json
"connect": { "type": "unsupported", "reason": "Needs a device on your home network." }
```

The agent learns the reason when asked to connect it; the Connectors screen leaves it out.

### Other connector fields

- `regions`: two-letter country codes where the service is widely used; it is featured for devices in those regions.
- `featured`: `true` asks to be listed among the featured connectors (after the built-in worldwide and regional ones).

Ways that need code — signing in with a code on the service's page (device flow), mailboxes over IMAP, Google sign-in — stay built in.
They are in the same registry (`packages.Registry.connectors()` lists every connector with its `connect.type`: `mcp`, `key`,
`device`, `mail`, `google`, `browser`, `unsupported`, or `none`), but a package cannot declare them.

## What the apps see

Nothing new is needed in the apps: an installed connector is one more row in `GET /api/connectors` (with `"source": "installed"` and
`"author"`), its icon is served at `/api/connectors/icons/pkg_<id>.png`, its key form at `GET/POST /api/keys/<id>`, and its sign-in
at `POST /api/mcp-oauth/<id>/start`, the same as a built-in connector.

## Installing

The owner installs a package from its GitHub link in the app (My -> Nest -> Install from link; see [`nest.md`](nest.md)). Copying the
folder into `<state>/packages/` by hand also works. `Registry.install_from_dir(folder, replace=False)` checks the package, copies only
its plain files into place through a temporary folder and reloads; `Registry.remove(id)` deletes an installed package (built-ins
cannot be removed). Every install records where the package came from and what the owner approved (next section).

## Where a package came from, and what was approved

Every installed package has a **source record** in `<state>/packages/.kelo-sources.json` (a hidden file next to the package folders,
never inside one, so a package cannot write its own record):

| Field | Meaning |
| --- | --- |
| `source` | where it came from: `{"type": "local"}` (a folder on this server), `{"type": "git", "url", "commit", "path"?}` or `{"type": "archive", "url", "sha256", "path"?}` |
| `version` | the manifest's version when it was installed or last approved |
| `installed_at`, `approved_at` | unix seconds |
| `approved_hash` | the **content hash** the owner approved |
| `files` | the sha256 of each file, by path (to show what changed) |
| `sensitive` | the connector fields an approval is really about: `type`, `url`, `hosts`, `placement`, `base_url`, `verify`, `mcp_url` (a skill: `requires`) |
| `reviewed` | always false: nobody reviews packages; everything is **unreviewed** |

The **content hash** is one sha256 over every file of the package: each relative path (sorted, `/`-separated) with the sha256 of its
bytes. Nothing is left out, hidden files included; a link or a special file refuses the package, as when loading it.

**Approval is bound to the content.** Installing a package is the owner's approval of exactly those files. On every reload the
registry hashes each installed package again; when the hash is not the approved one, the package is **not used** until it is approved
again (`Registry.approve(id, expected_hash=...)`). Until then it is listed in `Registry.pending` with what changed — connector fields
first (hosts, key placement, MCP address: marked `sensitive`), then each file added, removed or changed — and its reason is in
`problems`. `Registry.statuses()` lists every installed package with its source, `review` (`reviewed` | `unreviewed`) and `state`
(`approved` | `changed`). A folder copied in by hand is approved as it is the first time it is seen (only the owner can write the
state folder) and marked unreviewed; edit it afterwards and it is held like any other change. Copy a folder in under a name starting
with `.` and rename it when it is complete, or a half-copied folder is what gets approved.

**Updates never switch source silently.** `install_from_dir(..., replace=True)` refuses an update from another kind of source or
another address (a different repository, an archive from a different host) unless the owner confirms with `switch_source=True`. A new
commit, or a new archive from the same host (its address names its version), is an ordinary update, approved by installing it.

## Custom skills

Custom skills are skills written on this server: `<state>/custom_skills/<id>/SKILL.md` (front matter as above) plus optional extra
`.md` files at the top of the folder, nothing else (`ASSISTANT_CUSTOM_SKILLS_DIR` points somewhere else). They load through the same
checks as every skill folder, as source `custom`, and the skills index the agent sees lists them apart ("written on this server and
approved by the owner"); `GET /api/skills` lists every skill with its `source` (`builtin` | `custom` | `installed`) and `state`.

- **Only what the owner approved is used.** Kelo can propose a new custom skill or a change to one (the on-demand `propose_skill`
  tool), and the owner can ask for a draft from a finished task or background job ("Save as skill" on its page; the draft has the
  sections When to use / Steps / Pitfalls / How to check it worked, no code, secrets removed). Either way the text waits in
  My → Records next to the learned preferences, shown in full (a change as a diff), until the owner approves or discards it.
  Approving writes exactly that text and records its content hash in `<state>/custom_skills/.kelo-sources.json` with the approved
  text (`Registry.write_custom`).
- **A change outside the app needs a new approval.** When a custom skill's files no longer match the approved hash, or a folder
  without an approval appears, it is not used and shows up in the same list ("Changed outside the app", with the diff against the
  approved text). Approve it as it is now, or discard to put the approved text back (a folder never approved is removed).
- **Built-in ids are refused.** A custom skill cannot take a built-in skill's id (server updates replace built-in skills); on a clash
  with an installed package the custom skill wins.
- **Text from outside is data.** A proposal is only a proposal: one made after web pages, mail or files reached the run is marked so
  the app says so on the card, and nothing becomes a skill without the owner.
- **Notes on any skill.** `notes/skills/<id>.md` in the agent's files is this server's notes on a skill, built-in or not (at most
  6,000 bytes). The agent may write it like its other notes, no approval needed; `read_skill` shows it after the skill, marked as
  this server's notes and not as rules. Built-in skills themselves stay untouched, so a server update replaces them and the notes stay.

The endpoints are listed in `server.py` ("Custom skills") and `custom_skills.py`.

## Examples

`server/examples/packages/` holds a skill (`trip-packing-list`), connectors of every way (`example_notes`, an MCP server; `example_weather`, an
API key; `example_tasks`, OAuth; `example_market`, a browser site) and packages with screens (`habit-tracker`, a skill with a tab;
`example_parcels`, a key connector with a tab; `daily-countdown`, a screens-only extension). Their addresses are placeholders on
`example.com` and do not work.

## Situation read

A connector can say how Kelo looks at its state once an hour: one read-only call in `connector.situation` of `kelo-package.json`. Kelo runs it
at each hourly look, and what it finds goes into the look as a "Connected: <name>" section. A connector without it is left out of the hourly
look; Kelo can still read it with its own tools when a question needs it. The call is checked the same way as any tool call: only a read runs,
and the owner's limits for that connector apply (a read the owner turned off while Kelo is not chatting is not made).

Two forms (give exactly one):

- `request`: a GET on the connection's own base address, for a key or OAuth connector with a `base_url`: `{"method": "GET", "path": "/notifications?unread=true"}`
- `tool`: a read-only tool of an MCP connection (or a key connector with `mcp_url`), with `arguments`: `{"tool": "list_open_tickets", "arguments": {"limit": 20}}`

The rest says what the answer means:

- `mode`: `state` (a snapshot: any change of what is shown counts as a change) or `changes` (rows since the last look; only a row whose `id` was not seen before counts).
- `select`: the dotted path to the rows in a JSON answer (`""` = the answer itself, an object is one row).
- `fields`: label to dotted path in a row, for example `{"title": "subject.title"}`. Without it a row is shown as compact JSON.
- `id`: for `changes`, the dotted path or list of paths whose values make a row's id. A text answer uses `lines` instead: a regular expression; only the matching lines are kept.
- `max_rows` (1 to 100, default 30), `max_chars` (200 to 6000, default 3000), `every_minutes` (0, or at least 60: read at most this often), `wakes` (default true; false means the section is shown when the look runs, but its change alone never starts a research turn; use it for busy feeds and values that drift).

Bindings, written inside a string: `{{connection}}` (the connection's item), `{{since}}` (the last look, ISO 8601 UTC), `{{since_unix}}`, `{{since_date}}`
(owner's zone), `{{now}}`, `{{now_unix}}`, `{{today}}`. A string that is exactly one binding keeps its type. Inside a longer string the value is percent-encoded.

Example (a notifications list, changes only):

```json
"situation": {"request": {"method": "GET", "path": "/notifications?unread=true"}, "mode": "changes", "select": "items",
              "fields": {"title": "subject", "when": "updated"}, "id": "id", "max_rows": 20}
```

Example (an MCP tool, the current state):

```json
"situation": {"tool": "list_open_tickets", "arguments": {"limit": 20}, "mode": "state", "select": "tickets",
              "fields": {"title": "title", "status": "status"}}
```

Kelo checks the draft when it is saved: a connector without this section gets a warning from `nest_draft_check`. The MCP tool of a `tool` read is
listed among the package's MCP tools, so the install card shows it and the owner can take it back.
