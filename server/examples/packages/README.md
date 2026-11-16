# Make a connector or skill for Kelo, with no code

A **package** is one folder. Put it in your server's packages folder and Kelo uses it on the next message. You do not change code
or restart anything. Delete the folder and it is gone.

- Where the folder goes: `<state>/packages/<id>/`. `<state>` is the folder that holds `assistant.db`, which is `/state` in Docker.
  You can point `ASSISTANT_PACKAGES_DIR` somewhere else.
- The folder's name must be the package `id`.
- Three working examples sit next to this file. Copy one and edit it.

| Example | Kind | How it connects |
| --- | --- | --- |
| [`example_notes/`](example_notes/kelo-package.json) | connector | hosted MCP server, user signs in on the service's page |
| [`example_weather/`](example_weather/kelo-package.json) | connector | API key the user makes on the service |
| [`trip-packing-list/`](trip-packing-list/) | skill | a written guide (`SKILL.md`) |
| [`example_tasks/`](example_tasks/kelo-package.json) | connector | OAuth 2 API, user signs in with their own app's client id |
| [`example_market/`](example_market/kelo-package.json) | connector | a website Kelo uses in its own browser |
| [`habit-tracker/`](habit-tracker/) | skill + screens | a guide plus a Habits tab and Home card bound to its own entries |
| [`example_parcels/`](example_parcels/kelo-package.json) | connector + screens | an API key plus a Parcels tab (package tracking) with confirmed changes |
| [`daily-countdown/`](daily-countdown/kelo-package.json) | extension | screens only: a Home card and a My page section |
| [`example_mail/`](example_mail/kelo-package.json) | connector | hosted MCP server that `provides` mail and a calendar: the briefing, heads-ups and email/calendar tools read it like a built-in mailbox |

To publish a package for every Kelo server (Kelo Nest), check it with `kelo nest validate <folder>` and read
[`docs/nest.md`](../../../docs/nest.md). The screens format is [`docs/nest-ui.md`](../../../docs/nest-ui.md).

The full reference, with every field and limit, is [`docs/packages.md`](../../../docs/packages.md).

## A connector in 3 steps

1. Make a folder whose name is your id. A connector id uses `a-z`, `0-9` and `_`, and starts with a letter. For example
   `my_service/`.
2. Put a `kelo-package.json` in it. Pick **one** of the `connect` blocks below.
3. Optionally add `icon.png` (a square PNG, 256 px is a good size) and set `"icon": "icon.png"`.

```json
{
  "format": 1,
  "id": "my_service",
  "kind": "connector",
  "version": "1.0.0",
  "name": "My Service",
  "description": "One line: what the user gets from it",
  "author": "Your name",
  "homepage": "https://my-service.example.com",
  "connector": {
    "regions": [],
    "connect": { "...": "one of the blocks below" }
  }
}
```

**The service has an MCP server.** The user signs in on the service's own page. The tokens are sent only to this host.

```json
"connect": { "type": "mcp", "url": "https://mcp.my-service.example.com/mcp", "blurb": "What Kelo can do with it" }
```

**The service uses an API key.** The user pastes the key in the app. The key is sent only to the hosts you list.

```json
"connect": {
  "type": "key",
  "hosts": ["api.my-service.example.com"],
  "placement": { "type": "bearer_header" },
  "base_url": "https://api.my-service.example.com/v1",
  "verify": "https://api.my-service.example.com/v1/me",
  "api": "Short notes for the agent: the main endpoints and what they return.",
  "help": ["Where the user finds or makes the key.", "Paste it here."],
  "blurb": "What Kelo can do with it"
}
```

`placement` can also be `{"type": "custom_header", "header": "X-Api-Key"}` or `{"type": "query_param", "param": "api_key"}`.

**The service is a website with no API.** Kelo opens it in its own browser.

```json
"connect": { "type": "browser", "url": "https://www.my-service.example.com" }
```

**Translations (optional).** Add `"strings": {"ko": {"name": "...", "description": "...", "blurb": "..."}}`. A language you leave
out uses the English text.

## A skill in 2 steps

1. Make a folder whose name is your id. A skill id uses `a-z`, `0-9` and `-`. For example `my-skill/`.
2. Put a `SKILL.md` in it:

```markdown
---
name: My skill
description: One line saying when the agent should use this guide
requires: []
---
# My skill
The steps, written for the agent.
```

`requires` lists connector ids. If none of them is connected, Kelo tells the user to connect one first. Extra `.md` files in the
folder are read only when the agent needs them. A `kelo-package.json` is optional for a skill. Add one when you want to set
`version`, `author` or `icon`.

A skill you want on your own server only does not need a package: Kelo can propose one after a task, or you can tap "Save as
skill" on a finished task. It is kept as a custom skill once you approve it in My → Records (see "Custom skills" in
[`docs/packages.md`](../../../docs/packages.md)).

## What a package can and cannot do

- **It cannot run code on the server.** A skill is text. A connector only describes a service. Anything that must run comes from an
  MCP server.
- **It cannot skip approvals.** Each MCP tool call is approved by the user, and payments and sign-ins keep their own rules.
- **Its text is treated as data, not instructions.** Kelo shows it to the model marked as another author's text, with a length limit.
- **A built-in always wins.** If a built-in skill or connector already has the same id, your package is left out.
- **Changing it after install needs a new approval.** Kelo remembers a hash of every file in your package when it is installed. If
  the files change later, the package is not used until the owner approves the new version (they are shown what changed, hosts and
  addresses first). A package installed by hand, not from a reviewed catalog, is marked "unreviewed".
- **A catalog pins exact code.** A catalog entry points to a git commit (a full 40-character id, never a tag or branch) or to an
  archive with its sha256. See "Catalog index" in [`docs/packages.md`](../../../docs/packages.md).
- **A broken package is skipped.** The reason is written to the server log, and the other packages still load. Packages are refused
  for:
  - symbolic links;
  - more than 64 files or 2 MiB in all;
  - URLs that are not https;
  - unknown fields inside `connector`, `connect` or `skill`.

## Check your package

Drop the folder in, then:

- **Connector:** open the Connectors screen in the app. It should be listed. Try connecting it.
- **Skill:** ask Kelo "what skills do you have?"
- **Not showing up:** look at the server log for a `package:` line. It says why the package was left out.
