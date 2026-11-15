# Kelo Nest: publish packages for Kelo

**Kelo Nest** ("Nest") is where people publish things Kelo can use: guides for the agent, connections to services, and screens for
the apps. Every Kelo server can browse Nest, show the owner what a package may do in plain words, and install it with the owner's
approval. Nothing in a package is code: Kelo and its apps never run anything a package brings, so a package works on every server and
every app without an update.

This page is for publishers (and, at the end, for whoever runs the Nest index). The screens a package adds are specified in
[`nest-ui.md`](nest-ui.md); the package folder format it builds on is in [`packages.md`](packages.md).

## What a package can be

One package is one folder with a `kelo-package.json` manifest. Its `kind` is one of:

| Kind | What it adds | Example |
| --- | --- | --- |
| `skill` | a written guide (`SKILL.md`) Kelo reads when it fits the user's request | [`trip-packing-list`](../examples/packages/trip-packing-list/) |
| `connector` | a connection to a service, in one of four ways (below) | [`example_notes`](../examples/packages/example_notes/), [`example_weather`](../examples/packages/example_weather/), [`example_tasks`](../examples/packages/example_tasks/), [`example_market`](../examples/packages/example_market/) |
| `extension` | screens for the apps (and entries kept on the server) only | [`daily-countdown`](../examples/packages/daily-countdown/) |

A skill or a connector can **also** add screens with a `ui` section: it is then listed with the kinds `skill, extension` or
`connector, extension`. [`habit-tracker`](../examples/packages/habit-tracker/) is a skill with a Habits tab;
[`example_parcels`](../examples/packages/example_parcels/) is a key connector with a Parcels tab.

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
[`example_mail`](../examples/packages/example_mail/kelo-package.json) is a complete one.

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
- a changed, turned-off or withdrawn package does nothing at all.

## Make, check and publish a package

Install the server code (or use any Kelo server's image), then:

```sh
kelo nest init my-package --kind skill          # or connector / extension: a folder to edit
kelo nest validate my-package                   # checks it as a Kelo server will, and prints what the owner will be shown
kelo nest pack my-package --out dist            # a reproducible .tar.gz, its sha256 and the content hash
```

`kelo` is the same program as `assistant nest ...` on a server.

Test it on your own server first: copy the folder to `<state>/packages/<id>/`; it is picked up on the next message, without a
restart. Its screens show in the apps at once.

### Publishing: a pull request to the registry

Nest is built from a public Git repository, the **registry** (`kelo-chat/nest`). Every published version is one small JSON file
there, added by pull request. Your package itself lives where you keep it:

- **a Git repository** on github.com, gitlab.com or codeberg.org, pinned to a full 40-character commit (a tag or branch can be moved
  later, so it is never accepted), or
- **an archive** (`.tar.gz` or `.zip`, ideally from `kelo nest pack`) at any public https address, pinned by its sha256.

```sh
git clone https://github.com/kelo-chat/nest && cd nest
kelo nest publish ../my-package --registry . --publisher <your id> \
    --git-url https://github.com/you/my-package --commit <40-character commit> [--path package]
# or: --archive dist/my-package-1.0.0.tar.gz --archive-url https://you.example/my-package-1.0.0.tar.gz
```

This writes `packages/<id>/<version>.json` and prints the git commands for the pull request; it does not push anything. The file
holds your package's names and descriptions in every language it has (`strings` in the manifest), its kinds, the source, the
**content hash** (one hash over every file of the package) and the permissions. On your first package, add yourself to
`publishers.json` in the same pull request (`{"<id>": {"name": "...", "homepage": "https://..."}}`).

The registry's check (`kelo nest check-registry`) refuses a file that does not match its folder and name, an unknown publisher, a
package id that belongs to another publisher, and anything a server would refuse. A published version never changes: to fix
something, publish a new version.

### Review and "verified"

The Nest operator reviews pull requests. A package the operator has looked at closely is listed in `verified.json` and marked
**verified** in the apps; a publisher whose identity the operator checked is marked verified in `publishers.json`. Everything else is
listed as unverified (the owner sees that before installing). Verified or not, every server checks the files against the index and
enforces the permissions.

### Withdrawal

When a published package turns out to be harmful, the operator adds it to `withdrawn.json` (all versions, some versions, or exact
content hashes, with a reason). Every server that reads the next index turns that package off, tells its owner why, and keeps it off
(even offline) until a version that is not withdrawn is installed. A withdrawn package cannot be turned back on.

## How a server trusts Nest

```
https://kelo.chat/nest/index.json           the index: every package, its versions, sources, content hashes, permissions
https://kelo.chat/nest/index.json.minisig   its signature
```

- **Signed with minisign (Ed25519).** The index is signed by the Nest operator's key; every server carries the operator's public key
  and reads no index without a valid signature. minisign is a small, standard format that needs no network, no certificate
  authority and no log to verify, so a server behind a firewall or offline checks it the same way; operators can use the `minisign`
  program or `kelo nest index sign`. (Sigstore was considered: it proves who signed through an online certificate authority and
  transparency log, which a self-hosted server cannot always reach, and there is exactly one signer here.)
- **Fresh and never older.** The index has an expiry time (a server stops trusting an index that someone keeps it stuck on) and a
  sequence number that only goes up (a server refuses an older signed index once it has seen a newer one).
- **Exact files.** Each version names its source (a commit or an archive's sha256) and its content hash. A server checks both after
  downloading, and that the package's id, version, kinds, connect type and permissions are exactly what the index says, before
  anything is installed.
- **Bound to the owner's approval.** The installed files' hash is what the owner approved; if the files change on disk, the package
  stops working until the owner approves again (see [`packages.md`](packages.md)).

## The server API

For the apps (owner-only where it changes something; `Accept-Language` picks the language of every text):

| Method and path | Who | What |
| --- | --- | --- |
| `GET /api/nest/status` | any device | the index: trusted key present, sequence, expiry, last error; `max_tabs` |
| `GET /api/nest/browse?q=&kind=` | any device | search (`kind`: `skill`, `connector`, `extension`) |
| `POST /api/nest/refresh` | owner | fetch the index now (applies the withdrawal list) |
| `GET /api/nest/packages/{id}` | any device | details: versions, permissions in plain words and their `hash`, installed state, update |
| `POST /api/nest/packages/{id}/install` | owner | `{"version"?, "accept": "<permissions hash>"}`; `409` with the permissions when `accept` is not the hash shown |
| `POST /api/nest/packages/{id}/update` | owner | `{"version"?, "accept"?, "switch_source"?}`; `409` with `widened` when it asks for more |
| `DELETE /api/nest/packages/{id}?keep_data=false` | owner | remove it (its entries and vault item go too) |
| `PUT /api/nest/packages/{id}/pin` | owner | `{"pinned": true}`: stay on this version |
| `PUT /api/nest/packages/{id}/enabled` | owner | `{"enabled": false}`: turn it off without removing it |
| `PUT /api/nest/packages/{id}/permissions` | owner | `{"revoke": ["reads.calendar", "ui.tab", "actions.connector"], "grant": [...]}` |
| `PUT /api/nest/packages/{id}/client-id` | owner | `{"client_id": "..."}` for an `oauth` package with `client: "owner"` |
| `GET /api/nest/installed` | any device | every installed package: state (`on`, `disabled`, `withdrawn`), pinned, permissions, granted, update |
| `GET /api/nest/updates` | any device | installed packages with an update that is not pinned |
| `GET /api/nest/extensions` and the screen endpoints | | see [`nest-ui.md`](nest-ui.md) |

Packages copied into `<state>/packages/` by hand and custom skills keep working as before; they are listed as installed (by hand,
"unreviewed") and never updated from Nest.

## Limits

| What | Limit |
| --- | --- |
| files in a package | 64, 2 MiB in all; the manifest 64 KiB; each `.md` 128 KiB; the icon 512 KiB (PNG) |
| programs | none: an executable bit, a program header, a script or program file name refuses the package |
| `ui` | 48 KiB; 6 surfaces (1 tab, 1 My page section, 2 Home cards, 2 chat cards); 8 screens; 60 components per screen |
| entries | 4 collections, 12 fields each, 1000 entries per collection, 512 KiB in all |
| index | 4 MiB, 2000 packages, 50 versions each |

## For the Nest operator

```sh
kelo nest keygen --out ~/nest-keys                     # once: nest.key (keep it offline) and nest.pub
# put the printed public line in src/assistant/nest_index.py TRUSTED_KEYS (or ASSISTANT_NEST_KEYS on a server)
kelo nest check-registry nest/                         # CI on every pull request
kelo nest index build --registry nest/ --out site/nest/index.json --expires-days 30
kelo nest index sign --key ~/nest-keys/nest.key site/nest/index.json   # or: minisign -Sm site/nest/index.json
kelo nest index verify --pub ~/nest-keys/nest.pub site/nest/index.json
```

Publish `index.json` and `index.json.minisig` at `https://kelo.chat/nest/`. Rebuild and sign at least every couple of weeks even with
no changes (the expiry), and at once after a withdrawal. The sequence defaults to the current time, so it always goes up.

Server settings: `ASSISTANT_NEST_KEYS` (more trusted public keys, comma-separated: a second key for rotation, or a private index),
`ASSISTANT_NEST_INDEX` (another index address). To rotate the key: add the new public key next to the old one in a server release,
sign with the new key once that release is out, then remove the old key.
