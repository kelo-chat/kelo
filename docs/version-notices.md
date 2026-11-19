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

## 4. Store releases through kelo.chat
A store build (Play, App Store, Microsoft Store) learns of a newer store release from kelo.chat too, not only from the bundled
releases/apps.json. latest.json's `"store"` object holds the newest live store release of each app, one row per platform and channel:
`{"android": {"play": row}, "ios": {"appstore": row}, "desktop": {"msstore": row}}`. A row is `{channel, version, build, date[, min_server],
sig, key}`; `sig` is the release key's Ed25519 signature over the text `kelo-store-v1` (release_sign.store_statement), so a store
signature never verifies as a file row or a notices list, and back. Readers verify each row (release_sign.verify_store_row) and fall
back to the bundled record when the list is unavailable.
- `assistant release-record <platform> <version> <build> <play|appstore|msstore>` (on the operator's computer) records the release and
  publishes the signed store row; `--no-publish` skips that. `assistant publish-store` re-publishes the newest store row of each app
  from releases/apps.json (after a failed publish, or for a record committed elsewhere).
- Every writer of the manifest keeps the `store` section (publish-app, sign-app, publish-server, publish-store).
- The minimum app version rule stays on the bundled releases/apps.json only: network rows never raise or lower what a server enforces.
  A signed store row whose `min_server` is above a server means that server offers nothing for that store channel.

## 5. Server update notice
When a newer server release exists, the apps tell the owner once in a while and lead to My -> Server update (the confirm and the
progress are there).
- Server: `ServerUpdates.offer()` reads the updater's last answer (no network call): the answer is at most 30 minutes old, the copy runs
  release images, the release is newer than this server's version.json, no job runs, and automatic updates are off. It is sent as
  header `X-Kelo-Server-Update: <version>` on /api/about and /api/profile and as `server_update` in GET /api/about, only to a caller that may
  update this server (the access key, or a signed-in device that is not on a review copy).
- No notice on a source build or Chrome build (the server cannot update itself), on a copy without an updater, and when automatic
  updates are on (the server updates itself at night and tells the owner afterwards).
- Apps: a dialog at most once per 24 hours per device, whatever the answer; never within an hour of the app-update popup, never over
  a full-screen notice. "See the update" opens My -> Server update. Its text is in every app language (apps/shared/strings).

## Tests
Contract test above; app_update_for picks only released rows, respects min_server and channel, ignores unsigned rows; banner state
tests in shared and iOS; ServerTooOld button visible only when the API answers. Texts via i18n in every app language.

## Built (2026-11-18, server part)
- Part 4 (store rows): release_sign.store_statement / sign_store_row / verify_store_row; release.py notes_i18n; downloads.py
  `_store_rows`, `StorePublisher`, `run_publish_store`; app_releases.py `newest_store`, `Release.store_row`, `release-record` publishes.
- Part 5 (server notice, server side): server_updates.py `offer()`; api/about.py `server_update_for` and the header.
- The shared Kotlin parser decodes only this platform's latest.json row (UpdateRules.parse): one row a build cannot read never hides
  the others. The server row carries `notes` as an English string plus `notes_i18n` (release.Release.to_public_json), so installed APKs read it.

## Built (2026-11-16)
- Part 1: `server/tests/test_released_apps_contract.py` and `server/tests/fixtures/app_requests/{android-0.10.1,desktop-1.0.9}.json`, recorded from
  the shared API client at tag v0.10.1. iOS has no entry in releases/apps.json yet: its fixture is required by the test the day one is added.
- Part 2: `ServerOldUpdater` / `ServerOldRules` (shared Kotlin, shell/ServerOldUpdate.kt) and `ServerOldModel` / `ServerOldRules` (iOS).
- Part 3: `server/src/assistant/app_updates.py`. The signed latest.json row has no `min_server`, so it comes from the optional `min_server` field
  of the releases/apps.json entry (`assistant release-record ... --min-server`; none recorded = any server). The app names its channel in
  `X-Kelo-App-Channel` (Android: play | download, iOS: appstore, desktop: download); without it nothing is offered. Desktop is compared by
  build, the other platforms too when the caller sent one.
