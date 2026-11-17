# App and server version notices (design, 2026-11-16)

Owner rule (2026-11-16): users run unknown mixes of app and server versions. (1) The server stays compatible with the last RELEASED
app on every platform. (2) When the server is too old for the app, the app tells the owner to update the server and offers it. (3)
When the server was updated and a newer released app that fits it exists, the app tells the owner to update the app.

Today: the server answers 426 app_too_old below `min_app` (only released versions may be named there); the apps show
ServerTooOldScreen below their compiled-in `min_server` with only a Retry; sideload/desktop builds check kelo.chat/download/latest.json
for their own updates; store builds (Play, App Store) never hear about a newer app.

## 1. Compatibility contract test (server)
`server/tests/test_released_apps_contract.py`: for every platform's newest entry in releases/apps.json, replay the request set that
release makes on start and in normal use (a fixture per release: method, path, headers incl. X-Kelo-App, minimal body; kept in
`server/tests/fixtures/app_requests/<platform>-<version>.json`, recorded from the app's API client code) against a fresh test server:
no 426, no 404/405 on any route, and the response fields that release reads are still there (a field list per request in the
fixture). A server change that breaks a released app fails CI. When an app release goes live, its fixture is added (release checklist,
docs/versioning.md).

## 2. Server too old -> offer the update (apps)
ServerTooOldScreen (shared Kotlin + iOS) gets "Update the server" when `GET /api/server-update` answers (the server has in-app updates):
it calls POST /api/server-update/check then /update, shows the progress the My -> Server update page already shows, and when done
re-checks the version and closes. When that API is missing (404) or the caller cannot update (not the owner device), it shows the
short text and a link to https://kelo.chat/install (update section) instead. Retry stays.

## 3. Newer compatible app -> tell the owner (server + apps)
- Server: `app_update_for(platform, channel, version, build)` reads the signed app rows of kelo.chat/download/latest.json the server
  already fetches for its own updates (release.py, verified signature), plus releases/apps.json for store channels. It returns the
  newest released app for that platform+channel whose `min_server` <= this server's version and that is newer than the caller, or
  None. Exposed as response header `X-Kelo-App-Update: <version>` on /api/about and /api/profile (cheap, cached 6 h) and as
  `app_update` in GET /api/about.
- Apps (all three; store builds too): when the header or field names a newer version, show a dismissible banner on the chat screen
  once per version: "A new Kelo app (x.y.z) works best with your server" with the platform's action (Play store page, App Store page,
  desktop/sideload: the existing in-app updater). Dismissed versions are remembered per device.
- Never shown when the app's own channel cannot get that version yet (a store row is only listed once live).

## Tests
Contract test above; app_update_for picks only released rows, respects min_server and channel, ignores unsigned rows; banner state
tests in shared and iOS; ServerTooOld button visible only when the API answers. Texts via i18n in every app language.

## Built (2026-11-16)
- Part 1: `server/tests/test_released_apps_contract.py` and `server/tests/fixtures/app_requests/{android-0.10.1,desktop-1.0.9}.json`, recorded from
  the shared API client at tag v0.10.1. iOS has no entry in releases/apps.json yet: its fixture is required by the test the day one is added.
- Part 2: `ServerOldUpdater` / `ServerOldRules` (shared Kotlin, shell/ServerOldUpdate.kt) and `ServerOldModel` / `ServerOldRules` (iOS).
- Part 3: `server/src/assistant/app_updates.py`. The signed latest.json row has no `min_server`, so it comes from the optional `min_server` field
  of the releases/apps.json entry (`assistant release-record ... --min-server`; none recorded = any server). The app names its channel in
  `X-Kelo-App-Channel` (Android: play | download, iOS: appstore, desktop: download); without it nothing is offered. Desktop is compared by
  build, the other platforms too when the caller sent one.
