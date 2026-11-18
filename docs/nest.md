# Kelo Nest: packages for Kelo, shared by a GitHub link

**Kelo Nest** ("Nest") is how people share things Kelo can use: guides for the agent, connections to services, and screens for
the apps. A package lives in its author's GitHub repository. Anyone can share the link; the owner pastes it on their Kelo server, sees
what the package may do in plain words, and installs it with their approval. Nothing in a package is code: Kelo and its apps never run anything a package brings, so a package works on every server and
every app without an update.

This page is for the people who make packages. The screens a package adds are specified in
[`nest-ui.md`](nest-ui.md); the package folder format it builds on is in [`packages.md`](packages.md).

## What a package can be

One package is one folder with a `kelo-package.json` manifest. Its `kind` is one of:

| Kind | What it adds | Example |
| --- | --- | --- |
| `skill` | a written guide (`SKILL.md`) Kelo reads when it fits the user's request | [`trip-packing-list`](../server/examples/packages/trip-packing-list/) |
| `connector` | a connection to a service, in one of four ways (below) | [`example_notes`](../server/examples/packages/example_notes/), [`example_weather`](../server/examples/packages/example_weather/), [`example_tasks`](../server/examples/packages/example_tasks/), [`example_market`](../server/examples/packages/example_market/) |
| `extension` | screens for the apps (and entries kept on the server) only | [`daily-countdown`](../server/examples/packages/daily-countdown/) |

A skill or a connector can **also** add screens with a `ui` section: it is then listed with the kinds `skill, extension` or
`connector, extension`. [`habit-tracker`](../server/examples/packages/habit-tracker/) is a skill with a Habits tab;
[`example_parcels`](../server/examples/packages/example_parcels/) is a key connector with a Parcels tab.

Ids: a connector's id uses `a-z 0-9 _` and starts with a letter; a skill's or extension's uses `a-z 0-9 -`. 2-48 characters. The
folder is named by the id. An id a built-in skill or connector uses is refused.

### Connectors: the four ways

| `connect.type` | The user... | Fields |
| --- | --- | --- |
| `mcp` | signs in on the service's own page; Kelo uses the service's hosted MCP server | `url` (the MCP endpoint), `blurb` |
| `oauth` | signs in to their account on the service (OAuth 2, authorization code with PKCE) | `authorize_url`, `token_url`, `scopes`, `hosts` (the API hosts), `base_url`, `client` (`public` with a `client_id`, or `owner`), `params`, `api`, `docs`, `help`, `blurb`, `verify` |
| `key` | makes an API key on the service and pastes it in the app | `hosts`, `placement`, `base_url`, `verify`, `api`, `docs`, `help`, `key_label`, `key_pattern`, `mcp_url`, `blurb` |
| `browser` | nothing up front: Kelo opens the site in its own browser; a login they save stays in the vault | `url`, `blocks_bots`, `blurb` |

`mcp`, `key` and `browser` are described field by field in [`packages.md`](packages.md#connector-packages).

**`oauth`**: for a service with an OAuth 2 API and no MCP server.

```json
"connect": {
  "type": "oauth",
  "authorize_url": "https://auth.tasks.example.com/oauth/authorize",
  "token_url": "https://auth.tasks.example.com/oauth/token",
  "scopes": ["tasks.read", "tasks.write", "offline_access"],
  "hosts": ["api.tasks.example.com"],
  "base_url": "https://api.tasks.example.com/v2",
  "client": "owner",
  "api": "GET /lists returns the lists; ...",
  "help": ["Make an app at tasks.example.com/developers with your server's return address.", "Copy its client id into the package's settings."]
}
```

- **A secret never goes in a package.** A manifest with `client_secret` (or any key, token or password) is refused. Only public
  clients with PKCE are supported.
- `client: "public"` with `client_id`: the service gave you a public client id that any Kelo server may use. The return address must
  be the one Kelo servers use (the shared sign-in relay, or each server's `/oauth/callback`).
- `client: "owner"`: each owner registers their own app on the service and types its client id in the package's settings
  (`PUT /api/nest/packages/<id>/client-id`); the Connectors row is the usual sign-in button.
- The tokens are kept in the vault as `pkg-<id>` and are sent only to `hosts`. Kelo uses the API with its generic request tools
  (each change approved by the user), and the package's screens can use it too (below).
- `params`: up to 6 extra parameters for the sign-in page (for example `{"access_type": "offline"}`); the ones Kelo sets itself
  (`client_id`, `redirect_uri`, `state`, `scope`, `code_challenge`, ...) are refused.

### A mailbox or a calendar: `provides`

A connector that holds the owner's mail or calendar says so, with how Kelo's standard operations map to its tools (an MCP connector,
or a key connector with `mcp_url`) or to requests on its API (a key or OAuth connector with `base_url`). Kelo's built-in features then
read it like any built-in mailbox or calendar, with no change to Kelo: the morning briefing and the heads-ups (and the accounts list
under Notifications → accounts Kelo reads, where the owner can switch it off), `calendar_events`, and `email_list`, `email_read` and `email_update`.
[`example_mail`](../server/examples/packages/example_mail/kelo-package.json) is a complete one.

```json
"provides": {
  "mail": {
    "list":      {"tool": "search_messages", "arguments": {"unread_only": "{{unread}}", "max": "{{limit}}", "q": "{{query}}"},
                  "select": "messages", "fields": {"uid": "id", "subject": "subject", "from": "from", "date": "received",
                                                   "unread": "unread", "bulk": "promotion"}},
    "read":      {"tool": "get_message", "arguments": {"id": "{{uid}}"}, "fields": {"text": "body", "subject": "subject"}},
    "mark_read": {"tool": "set_read", "arguments": {"id": "{{uid}}", "read": true}},
    "archive":   {"request": {"method": "POST", "path": "/messages/{{uid}}/archive"}},
    "trash":     {"tool": "move_message", "arguments": {"id": "{{uid}}", "to": "trash"}}
  },
  "calendar": {
    "events":    {"tool": "list_events", "arguments": {"from": "{{start}}", "to": "{{end}}"}, "select": "events",
                  "fields": {"id": "id", "title": "title", "start": "start", "end": "end", "where": "location", "all_day": "all_day"}}
  }
}
```

| Capability | Operations (required in bold) | Bindings in `arguments` / `path` / `body` | Fields (required in bold) |
| --- | --- | --- | --- |
| `mail` | **`list`**, **`read`**, **`mark_read`**, **`archive`**, **`trash`**, `mark_unread` | `list`: `unread`, `limit`, `query`, `folder`; the others: `uid` (the id your `list` gave) | `list`: **`uid`**, **`subject`**, `from`, `date`, `unread`, `bulk` (marketing or bulk mail: left out of the briefing); `read`: **`text`**, `from`, `to`, `cc`, `subject`, `date` |
| `calendar` | **`events`** | `start`, `end` (ISO 8601 with the offset), `start_date`, `end_date` | **`title`**, **`start`**, `id`, `end`, `where`, `all_day` (times: ISO 8601, a date alone for all-day, or unix seconds) |

- `select` is where the rows are in the answer (a dotted path); `fields` name where each of Kelo's fields is in a row.
- A binding that is a whole value keeps its type (`"{{limit}}"` stays a number, `"{{unread}}"` a boolean).
- A package that declares a capability without its required operations or fields, or a tool on a connector with no MCP server, is
  refused when it is checked (`kelo nest validate`) and when it is installed.
- `provides` implies `reads` (`mail`, `calendar`) and its tools in `mcp_tools`: declare them in `permissions`. The owner sees "Reads
  your mail" before installing and can take it back at any time; Kelo then stops reading that mailbox or calendar.
- Changes pick messages by the ids `list` gave (Kelo does not run a search through your tools to change mail), and sending stays with
  your own tools.

### Which methods send, post or delete: `rules` (connectors only)

A connector can declare, as data, which of its MCP tools or API requests send, post, forward or delete, how strongly each is
protected and where its recipient is, so Kelo binds a permission to the exact recipient (full schema in `docs/connector-rules.md`):

```json
"rules": [
  {"id": "post", "via": "request", "http": "POST", "path": "messages/{recipient}/send", "kind": "send", "policy": "task", "recipient": "path"},
  {"id": "share", "via": "tool", "tool": "share_item", "kind": "send", "policy": "fresh", "recipient": ["arguments.with", "arguments.cc"]}
]
```

`policy` is `standing` (default), `task` (a task permission that names the recipient may cover it; a lasting allow may not) or `fresh`
(a card every time). A declaration can only make a call stricter, never weaker.

### Entries a package keeps: `store`

A package can keep small records on the owner's server (habit check-ins, saved days, a list of things). It declares them:

```json
"store": {
  "collections": {
    "checkins": {
      "fields": {"habit": {"type": "text", "max": 60, "required": true}, "note": {"type": "text", "max": 200}, "day": {"type": "date", "required": true}},
      "max_records": 1000
    }
  }
}
```

1-4 collections, 1-12 fields each; field types `text` (with `max` length), `number`, `bool`, `date`, `datetime`; `max_records` up to
1000 (default 200); at most 512 KiB for all of a package's entries. Only declared fields are kept, each of its type. Each entry also
gets an `_id` and a time `_at`. A package's screens read and write them; Kelo reads and writes them with the tools `package_entries`,
`package_entry_add` and `package_entry_delete` (name the package and collection in your skill). Removing the package removes its
entries.

### Screens: `ui`

Tabs, My page sections, Home cards and chat cards, built from a fixed set of native components bound to the package's data and
actions. The whole format is in [`nest-ui.md`](nest-ui.md#part-2-for-package-authors-the-ui-section).

## Permissions

Every package declares what it needs. The owner sees it in plain words before installing, and the server enforces it at run time.

```json
"permissions": {
  "hosts": ["api.parcels.example.com", "parcels.example.com"],
  "image_hosts": ["img.parcels.example.com"],
  "reads": ["calendar"],
  "scopes": ["tasks.read"],
  "mcp_tools": ["list_notes"],
  "ui": ["tab", "home_card"],
  "actions": ["store", "agent", "connector", "open_url"],
  "store": true
}
```

| Field | Meaning | What the owner reads (English) |
| --- | --- | --- |
| `hosts` | every host it contacts: connector hosts, OAuth endpoints, the MCP server, the browser site, links its buttons open | "Connects only to api.parcels.example.com, parcels.example.com" |
| `image_hosts` | where its screens' pictures come from | "Shows pictures from ... (fetched by your server, not your phone)" |
| `reads` | the owner's data it reads: `mail`, `calendar`, `contacts`, `messages`, `files`, `photos`, `location`, `health`, `wallet`, `tasks`, `memory` | "Reads your calendar", ... |
| `scopes` | OAuth scopes it asks for | "Asks for these permissions when you sign in: ..." |
| `mcp_tools` | tools of its MCP server its screens call to show data | "Shows data from these tools of its service: ..." |
| `ui` | where it adds screens: `tab`, `my_section`, `home_card`, `chat_card` | "Adds a tab to the bottom bar", ... |
| `actions` | what its buttons can do: `store`, `agent`, `connector`, `mcp`, `open_url` | "Its buttons send changes to its service (you confirm each one)", ... |
| `store` | it keeps entries on the server | "Keeps its own entries on your server" |

Every list ends with "Runs no code on your server or your phone".

Rules:

- **What the manifest uses must be declared.** The server works out what the manifest really uses (the connector's hosts, a fixed
  picture's host, an MCP tool a screen calls, a calendar feed, each kind of button) and refuses a package whose `permissions` leave
  any of it out. So the summary you publish is never smaller than what the package does. A package without a `permissions` section
  (written before Nest) gets exactly what it uses, with no `reads`.
- **`reads` for a skill is a promise you make to the owner.** A skill is text; when Kelo follows it, it uses its normal tools with
  their normal approvals. Declare what your guide makes Kelo use (for example `mail` for a guide that sorts mail), so the owner knows
  before installing. A screen can only show the owner's data through a `kelo` feed whose `reads` permission the package holds; mail,
  messages, contacts and the wallet are never shown on screens.
- **An update that asks for more needs the owner again.** When a new version adds anything (a host, a read, a surface, a kind of
  button, entries), every server holds the update until its owner approves the new list. An update that keeps or narrows the
  permissions installs with a tap.
- **The owner can take some back.** Single `reads`, `ui` surfaces and kinds of `actions` can be taken back at any time (the tab
  disappears, the button is gone, the feed is empty) and given back later. Hosts and the connector itself are what the package is:
  to stop those, the owner turns the package off or removes it.

What the server enforces for every package, whatever it declares:

- requests go only to declared hosts that the connection's vault item also allows; screens only ever GET (changes are buttons the
  owner confirms one by one, showing where the request goes and what it sends);
- a screen's data fetches are cached and limited per package (30 a minute; buttons 20 a minute; pictures 60 a minute);
- pictures are fetched by the server, never by the phone, and only PNG, JPEG, WebP or GIF up to 1 MiB;
- package text reaching Kelo (a skill, entries, a button's request) is fenced as third-party text: it cannot change Kelo's rules,
  grant permissions or skip approvals;
- a changed or turned-off package does nothing at all.

## Make and check a package

Install the server code (or use any Kelo server's image), then:

```sh
kelo nest init my-package --kind skill          # or connector / extension: a folder to edit
kelo nest validate my-package                   # checks it as a Kelo server will, and prints what the owner will be shown
kelo nest pack my-package --out dist            # a reproducible .tar.gz, its sha256 and the content hash
```

`kelo` is the same program as `assistant nest ...` on a server.

Test it on your own server first: copy the folder to `<state>/packages/<id>/`; it is picked up on the next message, without a
restart. Its screens show in the apps at once.

To share it, put the package in a GitHub repository with `kelo-package.json` at the top of the repository, and share the repository
link. See [Share and install by link](#share-and-install-by-link).

## Made by talking, shared on GitHub

Status: built (2026-11-15): drafts, the draft tools and live reload, Made here in all three apps, Share to GitHub, and install from a
link. The owner asks in chat ("make me a tab that tracks my reading", "turn the way I write reviews into a skill"), sees the result
in the app at once, changes it by talking, and shares it with one tap: Kelo puts it on the owner's GitHub and gives the owner the
repository link. No command line, no git.

### Drafts

- A draft is an ordinary package folder in `<state>/package-drafts/<id>/` (manifest, `SKILL.md`, `ui`, `store`), so it is read and
  checked by the same code as a published package (`packages.load_package(folder, source="draft")`).
- Agent tools (loaded on demand): `nest_draft_create(kind, id, name, description)`, `nest_draft_write(id, path, content)` (only the
  package's own files: `kelo-package.json`, `SKILL.md`, files under `ui/` and `assets/`; at most 256 KB per file, 2 MB per draft),
  `nest_draft_check(id)` (the validator's findings and what the owner would be shown), `nest_draft_delete(id)`.
- A draft is live on this server only while it is a draft: its skill is listed as a draft ("draft, made here"), its screens are
  served by `/api/nest/extensions` with `"draft": true`. Every write bumps the draft's version (`0.0.N`), and the server sends a new
  live event `nest_changed` {"id"} so the app reloads the open screen: the owner sees the change right after asking for it.
- A draft never gets more than a package would: the same permissions block, shown and approved the same way when it first asks for
  a permission (a draft with `connect` or `http` permissions asks before its first use, like an installed package).
- A skill the owner approved with `propose_skill` can become a draft ("share this skill"): its `SKILL.md` is copied into a new
  skill draft; the custom skill itself stays.

### In the apps

- My -> Nest -> "Made here": the drafts and the custom skills, each with its kind, version and three actions: Open (the screen, or
  the skill text read-only), Share, Delete. A draft's tab can be put on the tab bar like an installed extension (tab rules above).
- Draft screens carry a small "draft" badge in the screen header. Nothing else differs, so what the owner sees is what others get.
- Built in the apps (Android, desktop, iOS): the native renderer of [`nest-ui.md`](nest-ui.md) Part 1, My -> Nest -> Made here, the
  draft badge, Open / Share / Delete (Share's `409 github_write_needed` opens the GitHub connect panel and then shares again), the tab
  picker, and the live `nest_changed` reload of an open screen. Android: `shell/Nest*.kt`, `my/Nest*.kt`; iOS: `KeloShared/App/Nest*.swift`,
  `KeloShared/Design/NestViews.swift`, `KeloMy/Pages/NestPage.swift`.

### Share: to the owner's GitHub

1. Share needs the GitHub connector with write access to repositories ("Public repositories only" is enough for a public
   repository). Without it, the Share sheet offers to connect GitHub first (the device-code sign-in that exists today).
2. Tapping Share sends `POST /api/nest/drafts/{id}/share`. The server starts a chat turn with a hidden mark; Kelo asks in the
   owner's language, with quick replies: "I'll put it on GitHub. Name the repository `kelo-<id>`? Public?" The owner can answer
   with another name.
3. Kelo calls `nest_share_github(id, repo, public)`, which always shows an approval card: the account, the repository name,
   public or private, and the file list. On approval:
   - checks first: the validator again, the secret scan (`found_secrets`) and the privacy guard on every file (nothing of the
     owner's: no keychain values, no addresses, no entries from `store`); any finding stops it and is shown;
   - creates the repository when it does not exist (`POST /user/repos`, with a README made from the manifest and the license the
     owner picks once, default MIT for skills and screens);
   - pushes the files as one commit with the Git Data API (blobs -> tree -> commit -> update the ref), so no git program is needed;
   - answers with the repository link and the 40-character commit.
4. Then Kelo tells the owner the repository link, and that anyone can install it by pasting that link in My -> Nest -> Install from
   link. Nothing is published anywhere else: the repository is the package.
5. Sharing again pushes a new commit to the same repository. The installed copies get it as an update (see below).

### API (drafts)

```
GET    /api/nest/drafts                    {"items": [{"id", "kind", "name", "version", "updated_at", "shared": {"repo", "commit", "at"} | null}]}
GET    /api/nest/drafts/{id}               the files (text) and the validator's findings
DELETE /api/nest/drafts/{id}               {"ok"}
POST   /api/nest/drafts/{id}/share         202 {"turn_id"}: the chat turn that asks for the repository name; 409 without GitHub write access
```
New notification types (add to `push.NOTE_TYPES`): `nest_shared`.

### Order

1. Server: drafts folder, `load_package(..., source="draft")`, the draft tools, the `nest_changed` event on each write, tests.
2. Apps: "Made here" in My -> Nest, the draft badge, Share and Delete (Android first, then desktop and iOS).
3. Share to GitHub (Git Data API), the repository question and the approval card.
4. Install from a GitHub link.

## Share and install by link

A package is a folder in a GitHub repository, with `kelo-package.json` at the top of the repository. To share it, share the repository
link. The owner of a Kelo server installs it: in the app, **My -> Nest -> Install from link**, paste the link. The server reads the
newest commit of the repository, checks it like any package, and shows the owner its name, its author and what it may do (its
permissions, in plain words). **Install** pins that exact commit and its content hash, so what the owner saw is what gets installed.
The server fetches only the forge's archive of that commit, from github.com. A preview is kept for 15 minutes; after that the owner
pastes the link again.

Later commits show as an **update** in My -> Nest. The server checks the repository about every six hours, and when the owner asks.
An update that keeps or asks for less permission installs when the owner taps Update. An update that asks for more permission is
shown again, with what changed, and installs only when the owner approves it. A pinned package is not updated. The owner can turn a
package off, remove it, or take single permissions back at any time.

Nobody reviews packages: there is no review, no "verified" mark and no catalog. The app says so when the owner installs one. The
owner decides from what the package says it may do, and from who published it.

## The server API

For the apps (owner-only where it changes something; `Accept-Language` picks the language of every text):

| Method and path | Who | What |
| --- | --- | --- |
| `GET /api/nest/status` | any device | `max_tabs` |
| `POST /api/nest/install-from-link` | owner | `{"url": "https://github.com/<owner>/<repo>"}`: the package's card with its `token`, its permissions and their `hash`. Installs nothing; `422` when it is not a package |
| `POST /api/nest/install-from-link/confirm` | owner | `{"token", "accept": "<permissions hash>"}`: installs the previewed commit. `409` with `error` `look_again` (the permissions are not the ones `accept` names), `already_installed` or `changed` (the repository changed since the preview); `410` `link_expired`; `422` `refused` |
| `GET /api/nest/packages/{id}` | any device | the installed package: state, permissions, repository (`repo`), update as last checked |
| `POST /api/nest/packages/{id}/update` | owner | `{"accept"?: "<new permissions hash>"}`: installs the newest commit of the repository. `409` with `error` `look_again` plus `widened`, `narrowed` and `permissions` when it asks for more; `409` when up to date or pinned |
| `DELETE /api/nest/packages/{id}?keep_data=false` | owner | remove it (its entries and vault item go too) |
| `PUT /api/nest/packages/{id}/pin` | owner | `{"pinned": true}`: stay on this version |
| `PUT /api/nest/packages/{id}/enabled` | owner | `{"enabled": false}`: turn it off without removing it |
| `PUT /api/nest/packages/{id}/permissions` | owner | `{"revoke": ["reads.calendar", "ui.tab", "actions.connector"], "grant": [...]}` |
| `PUT /api/nest/packages/{id}/client-id` | owner | `{"client_id": "..."}` for an `oauth` package with `client: "owner"` |
| `GET /api/nest/installed` | any device | every installed package: `state` (`on` or `disabled`), `pinned`, `permissions`, `granted`, `repo`, `update` |
| `GET /api/nest/updates` | any device | installed packages with an update that is not pinned |
| `GET /api/nest/extensions` and the screen endpoints | | see [`nest-ui.md`](nest-ui.md) |

Packages copied into `<state>/packages/` by hand and custom skills keep working as before; they are listed as installed, with no
repository, and are never updated from Nest.

## Limits

| What | Limit |
| --- | --- |
| files in a package | 64, 2 MiB in all; the manifest 64 KiB; each `.md` 128 KiB; the icon 512 KiB (PNG) |
| programs | none: an executable bit, a program header, a script or program file name refuses the package |
| `ui` | 48 KiB; 6 surfaces (1 tab, 1 My page section, 2 Home cards, 2 chat cards); 8 screens; 60 components per screen |
| entries | 4 collections, 12 fields each, 1000 entries per collection, 512 KiB in all |

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
