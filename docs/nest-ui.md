# Kelo Nest screens: the server-driven UI schema (version 1)

A Nest package can add screens to the Kelo apps: a tab in the bottom bar, a section on My page, a card on Home, a card in the chat.
It does this **without shipping code and without a web view**. The package describes the screen with a small, fixed set of
components; the server checks the description, fetches the data, fills in every text and value, and sends the apps a plain tree they
render with their own native components (Jetpack Compose, SwiftUI, the desktop toolkit). The apps never run anything from a package,
never load a page from it, and never contact its hosts.

This document has two parts:

1. **For the apps**: the endpoints, what they return, and how to render each component. This is all an app needs.
2. **For package authors**: the `ui` section of `kelo-package.json` that the server turns into part 1.

Publishing and permissions are in [`nest.md`](nest.md). The server code is `src/assistant/nest.py` (resolving, data, actions) and
`src/assistant/nest_manifest.py` (checking a manifest).

---

## Part 1: for the apps

### Versioning

- The schema version is `1`. Every response below carries `"schema": 1`.
- A new version only **adds** component types, fields or places. A field an app does not know is ignored.
- A component type an app does not know is rendered as one row saying the app needs an update ("Update the app to see this part"),
  with no other content. The server also sends `{"type": "unsupported", "text": "..."}` for parts a package wrote for a newer schema
  than the server knows; render it the same way, using `text`.
- Never fail a whole screen because of one unknown part.

### Endpoints

All need the usual `Authorization: Bearer <token>` header and send `Accept-Language`; every text comes back in that language.

| Method and path | Who | What |
| --- | --- | --- |
| `GET /api/nest/extensions` | any signed-in device | what to render where |
| `PUT /api/nest/extensions/tabs` | owner | `{"show": ["<package>/<surface>", ...]}`: which package tabs are in the bottom bar |
| `GET /api/nest/extensions/{package}/data?surface={surface}` | any signed-in device | one surface's screen, filled in |
| `POST /api/nest/extensions/{package}/actions/{action}` | any signed-in device | a button or form was used |
| `GET /api/nest/extensions/{package}/image?src=...` | any signed-in device | a picture of a screen (the URL comes in the screen; send the auth header) |

Package management (browse, install, permissions) is in [`nest.md`](nest.md#the-server-api).

### `GET /api/nest/extensions`

```json
{
  "schema": 1,
  "max_tabs": 2,
  "icons": ["home", "star", "..."],
  "places": {
    "tab":        [{"key": "habit-tracker/habits", "package": "habit-tracker", "surface": "habits", "place": "tab",
                    "title": "Habits", "icon": "check_circle", "package_name": "Habit tracker", "author": "Example Author",
                    "data_url": "/api/nest/extensions/habit-tracker/data?surface=habits", "shown": true}],
    "my_section": [],
    "home_card":  [{"key": "habit-tracker/today", "...": "...", "shown": true}],
    "chat_card":  []
  }
}
```

Where each place goes:

| Place | Where it is shown | Notes |
| --- | --- | --- |
| `tab` | an extra tab in the bottom bar, after the app's own tabs | only entries with `"shown": true` (at most `max_tabs`); `title` and `icon` are the tab's label and icon. The owner picks which tabs show (settings screen listing every `tab` entry, saved with `PUT /api/nest/extensions/tabs`) |
| `my_section` | a section on My page, below the app's own sections | a header with `title` and `package_name`, then the screen inline |
| `home_card` | a card on Home | the screen inline in a card; tapping the card's header opens it full screen |
| `chat_card` | a card in the chat's add sheet ("+") | opens the screen in a bottom sheet over the chat; it is never inserted into the conversation |

Always show `package_name` (and `author` in details) somewhere near a package's surface, so the owner knows whose screen it is.
Re-read this list when the app comes to the foreground and after installing, updating, turning off or removing a package.

### `GET .../data?surface=...`: the screen

```json
{
  "schema": 1, "package": "habit-tracker", "surface": "habits", "place": "tab", "title": "Habits",
  "components": [ ... ],
  "errors": [{"data": "trackings", "error": "Connect Example Parcels first."}],
  "fetched_at": 1762600000.0
}
```

- `components` is the screen from top to bottom (a vertical scroll).
- `errors`: data that could not be fetched (the parts that use it are empty). Show each `error` once at the top as a quiet notice;
  the rest of the screen still renders.
- Fetch it when the surface is opened, on pull-to-refresh, and after an action answers `"refresh": true`. Do not poll; the server
  caches answers from outside services and limits how often a package's data is fetched.
- Status codes: `403` the package is turned off or may not show this surface any more (remove it from view and re-read
  `/api/nest/extensions`), `404` no such surface, `429` too many requests (show the `detail` text, try again later).

### Components

Every text below is plain text, already in the reader's language, with every value filled in. **Never interpret text as HTML,
Markdown or a link.** Texts can be long in any language: wrap them; row titles may be cut to two lines, everything else wraps fully.
An empty string means "nothing": do not draw an empty label.

| `type` | Fields | Render as |
| --- | --- | --- |
| `header` | `text`, `subtitle` | a section title with an optional line below |
| `text` | `text`, `style`: `body` \| `caption` \| `title` \| `label` | a paragraph in that text style |
| `stat` | `label`, `value`, `unit`, `caption`, `icon` | a large `value` (with `unit` after it), `label` above, `caption` below, optional icon |
| `list` | `rows`: [row], `empty`: `{"text", "icon"}` or null | rows (below); when there are none and `empty` is set, an empty-state block |
| `card` | `title`, `subtitle`, `children`: [component], `action` | a card holding the children; when `action` is set the whole card is tappable |
| `chart` | `kind`: `line` \| `bar`, `label`, `points`: [`{"x": "Mon", "y": 3}`] | a small chart; `x` are category labels in order, `y` numbers; an empty `points` draws nothing |
| `image` | `src`, `alt`, `height` | a picture of at most `height` dp, fitted inside; `src` is a server path (`/api/nest/...`): load it with the auth header; `alt` is its accessibility label |
| `button` | `label`, `style`: `primary` \| `secondary` \| `danger`, `action` | a button |
| `form` | `fields`: [field], `submit`: `{"label", "action"}` | input fields and a submit button |
| `empty` | `text`, `icon`, `action` | an empty-state block (icon, text, and a button when `action` is set) |
| `unsupported` | `text` | one row with `text` ("Update the app to see this part") |

A list row:

```json
{"key": "a1b2c3", "title": "Walk", "subtitle": "park", "trailing": "2026-11-09", "icon": "check", "image": "", "action": {...} | null}
```

`trailing` goes at the end of the row (a date, a status); `icon` or `image` at the start (an image wins). A row with `action` is
tappable; for a row whose action is `store_delete` or `danger`-like, show it as a swipe or a trailing menu item labelled with the
action's `label`, and ask "Remove?" first.

A form field:

```json
{"id": "habit", "type": "select", "label": "Habit", "required": true, "placeholder": "",
 "options": [{"value": "Walk", "label": "Walk 30 minutes"}], "default": "Walk"}
```

| `type` | Input | Value sent |
| --- | --- | --- |
| `text` | one-line text, at most `max_length` characters | string |
| `multiline` | multi-line text, at most `max_length` | string |
| `number` | numeric keyboard; `min` / `max` when given | number |
| `date` | date picker | `"YYYY-MM-DD"` |
| `time` | time picker | `"HH:MM"` |
| `select` | a picker of `options` (show `label`, send `value`) | string |
| `toggle` | a switch | `true` / `false` |

Check `required` before sending; the server checks everything again and answers `422` with a `detail` text naming the field.

### Icons

Tabs, rows, stats and empty states use only these names. An app maps each to its own icon set (Material Symbols on Android, SF
Symbols on iOS); anything else is drawn as `star`.

`home star heart check check_circle list calendar clock alarm bell box truck cart bag wallet card chart bar_chart book note pen camera
image music video map pin globe sun cloud moon fire drop leaf bolt flag tag gift trophy target person people chat mail phone link
search settings info warning plus refresh trash edit play lock key sparkle food coffee fitness pill plane car school work money weather
news game`

### Actions

Buttons, form submits, tappable cards and rows carry an `action`:

```json
{"id": "check_in", "type": "store_add", "confirm": false, "label": "Check in", "item": "a1b2c3", "data": "checkins", "url": "..."}
```

`item` and `data` are present on list rows (they say which row it is); `url` only for `open_url`. To run it:

```
POST /api/nest/extensions/{package}/actions/{id}
{"form": {"habit": "Walk", "note": "park"},   // a form's values (only for a form submit)
 "item": "a1b2c3", "data": "checkins"}        // the row's key and data id (only for a row)
```

The app never sends a row's values, only its key; the server finds the row again itself.

Answers (HTTP 200):

| `status` | Meaning | What the app does |
| --- | --- | --- |
| `done` | it ran | show `message` briefly (a snackbar); re-fetch the screen when `refresh` is true |
| `confirm` | the owner must confirm first (a change sent to an outside service) | show a dialog: `title` as the question, `detail` below (where it goes), and what is sent (`sends`, a small JSON object: show its keys and values as plain lines). On "Confirm", send the same POST with `{"confirm": "<token>"}` only. The token works once, for `expires_in` seconds |
| `open` | open a web page | open `url` in the **system browser** (Custom Tabs / Safari / the default browser), never in a web view inside the app |
| `started` | Kelo was asked to do something | show `message`, and offer to open the chat (`turn_id` is the reply, as from `POST /api/turns`) |

Errors: `{"detail": "..."}` with `403` (not allowed any more: re-read the screen), `404` (the button is gone: re-read the screen),
`409` (for example the service is not connected yet), `410` (the confirmation expired), `422` (a form value), `429` (too many taps),
`502` (the outside service failed). Show `detail`.

Hide a button while its request runs; do not send it twice.

### Rules every app follows

- Native components only; no web view, no HTML, no Markdown, no remote fonts or styles from a package.
- Pictures only through the `src` paths the server gives (they are fetched by the server: the phone never talks to a package's hosts).
- Nothing from a package is stored on the phone beyond normal screen caching.
- Package screens use the app's own colors, type and spacing; a package cannot style them.
- Check layouts at 320 dp and 412 dp widths with long, space-less labels in every language.
- The same features on Android, iOS and desktop.

---

## Part 2: for package authors: the `ui` section

```json
"ui": {
  "schema": 1,
  "surfaces": [ ... ],
  "data":     { "<id>": { ... } },
  "actions":  { "<id>": { ... } },
  "screens":  { "<id>": { "title"?: text, "components": [ ... ] } }
}
```

Ids (`surfaces[].id`, data, action and screen ids) are lower-case letters, digits and `_`, starting with a letter, up to 32
characters. Unknown fields anywhere in `ui` refuse the package (except unknown component types: below).

### Texts and values

A text is a string (up to 200 characters unless noted) or an object of languages with an `"en"` text:

```json
"title": {"en": "Habits", "fr": "Habitudes", "ja": "習慣"}
```

Texts may hold `{{...}}` bindings, filled in by the server:

| Binding | Where | Value |
| --- | --- | --- |
| `{{item.<path>}}` | a list row (`item.title`, ...) and a row's image | a field of the row (`item.carrier.name`, `item.tags.0`) |
| `{{data.<id>.<path>}}` | any text in a screen | a field of a data source's answer (`data.checkins.0.habit`: the first row's habit) |
| `{{count.<id>}}` | any text in a screen | how many rows a data source has |
| `{{now}}`, `{{today}}` | any text, action values | the current time (ISO) / date (`YYYY-MM-DD`) |
| `{{form.<field>}}` | action `values`, `body`, `arguments`, `path` | what the user typed in the form that submits the action |

A missing value becomes an empty string. Numbers are shown as they are, `true` as a check mark; objects and lists show nothing.

### Surfaces

```json
{"id": "habits", "place": "tab", "title": "Habits", "icon": "check_circle", "screen": "main"}
```

- `place`: `tab` (at most 1 per package), `my_section` (1), `home_card` (2), `chat_card` (2); at most 6 surfaces in all.
- `title`: up to 24 characters. `icon`: one of the icons above (required for a tab).
- The bottom bar shows at most 2 package tabs at once; when more packages add tabs, the owner chooses which.

### Data sources

| `source` | Fields | Needs |
| --- | --- | --- |
| `store` | `collection`, `limit` (1-100, 50), `order` (`newest` \| `oldest`) | a `store` section with that collection |
| `connector` | `path` (`/...`, no query), `query` (fixed values), `select`, `limit`, `refresh` (seconds, 60-86400, 300) | the package's own `key` or `oauth` connector with a `base_url`; a GET to `base_url + path` |
| `mcp` | `tool`, `arguments` (fixed values), `select`, `limit`, `refresh` | the package's own `mcp` connector; the tool is listed in `permissions.mcp_tools` |
| `kelo` | `feed`, `limit` (1-50, 20) | the owner's data: `calendar.upcoming` (`reads: calendar`), `tasks.recent` (`reads: tasks`), `files.recent` (`reads: files`) |

`select` is a dotted path to the rows in the answer (`data.items`). An answer that is a list gives rows; an object is one row.
`refresh` is how long the server keeps an answer before asking the service again. Mail, messages, contacts and the wallet are never
available to screens.

Fields of the `kelo` feeds: `calendar.upcoming` rows `title`, `start`, `where`; `tasks.recent` rows `title`, `status`, `started`,
`result`; `files.recent` rows `name`, `kind`, `bytes`, `created`.

### Actions

| `type` | Fields | What happens |
| --- | --- | --- |
| `store_add` | `collection`, `values` (every required field of the collection) | saves an entry |
| `store_delete` | `collection` | removes the row it is on (only on a row of that collection) |
| `agent` | `prompt` (text up to 600 characters), `label` (required) | starts a chat reply: the chat shows "`label` · package name"; Kelo gets the prompt marked as your text (a request, not instructions) with what the user filled in. Kelo's usual approvals apply |
| `connector` | `method` (`POST` \| `PUT` \| `PATCH` \| `DELETE`), `path`, `body`, `confirm` (the question, required) | after the owner confirms the exact request, sends it to `base_url + path` with the connection's key or sign-in |
| `mcp` | `tool`, `arguments`, `confirm` (required) | after the owner confirms, calls the tool on the package's MCP server |
| `open_url` | `url` (https, on a host in `permissions.hosts`) | the app opens it in the system browser |
| `refresh` | - | fetches the screen's data again |

Every action may have `label` (up to 60 characters) and `done` (the message after it ran, up to 120).

A row that shows the owner's data (`kelo` source) cannot carry a `connector` or `mcp` action: their data never goes out from a
package screen.

### Components

| `type` | Fields (author side) |
| --- | --- |
| `header` | `text` (required), `subtitle` |
| `text` | `text` (required, up to 600), `style`: `body` \| `caption` \| `title` \| `label` |
| `stat` | `label`, `value` (required, up to 60), `unit` (20), `caption`, `icon` |
| `list` | `data` (required), `item`: `{"title" (required), "subtitle", "trailing", "icon", "image": "{{item.x}}", "action"}`, `max` (1-100, 20), `empty`: `{"text", "icon"}` |
| `card` | `children` (1-20 components, required), `title`, `subtitle`, `action` |
| `chart` | `kind` (`line` \| `bar`), `data`, `x` and `y` (field paths of the rows; `y` must be numbers), `label`; at most 60 points |
| `image` | `src` (a fixed https address on a host in `permissions.image_hosts`, or a binding such as `{{item.photo}}`), `alt` (required), `height` (40-400, 160) |
| `button` | `label`, `action` (required), `style`: `primary` \| `secondary` \| `danger` |
| `form` | `fields` (1-12): `{"id", "type", "label", "required", "placeholder", "options" (select, 1-30 `{"value", "label"}`), "min", "max" (number), "max_length" (text), "default"}`; `submit`: `{"label", "action"}` |
| `empty` | `text` (required), `icon`, `action` |

Limits: at most 8 screens, 60 components in one screen (nested ones counted), cards nested at most 3 deep, 8 data sources, 16
actions, and 48 KiB for the whole `ui` section.

A component type this server does not know (one from a newer schema) is kept as an "update the app" row, so a package can use new
components while older servers and apps still show the rest of the screen.

### A complete example

`examples/packages/habit-tracker/` adds a Habits tab (a form, a list with a remove action, an "ask Kelo" button) and a Home card,
bound to entries its skill also writes. `examples/packages/example_parcels/` adds a Parcels tab bound to its API-key connector, with a
confirmed POST and DELETE. `examples/packages/daily-countdown/` is a screens-only extension that also shows the owner's upcoming
calendar events.
