# Versions

One tracked file at the repository root, `version.json`, holds Kelo's release version for the server and every app:

```json
{"version": "0.9.0", "build": 1, "min_app": "0.9.0", "min_server": "0.9.0"}
```

| Field | Meaning |
|---|---|
| `version` | The release, `MAJOR.MINOR.PATCH`. Pre-release versions are `0.x.y`; `1.0.0` is the public launch. |
| `build` | A whole number that only goes up, set when a release is made. It is never computed from the clock or the machine at build time, so building the same commit again gives the same version (verifiable builds, docs/verify-builds.md). |
| `min_app` | The oldest app release (its `version`) this server works with. |
| `min_server` | The oldest server release the apps built from this commit work with (each app compiles it in). |

## Which number goes up (owner's rule, 2026-11-11)

Every release that changes anything gets a new `version`; the kind of change picks the part, never its size:

| Change | Part | Example |
|---|---|---|
| A fix, wording, a small improvement (one line or many) | patch | 0.9.1 → 0.9.2 |
| A new feature, or an API the apps start to use | minor | 0.9.2 → 0.10.0 |
| Old apps can no longer connect (before 1.0: minor, and raise `min_app`, only after the new apps are released: see Minimum app version rule) | minor + `min_app` | 0.10.0, min_app 0.10.0 |
| The public launch, then breaking changes | major | 1.0.0 |

`build` is not chosen by people: it counts builds and goes up by one on every bump. The same `version` again is only for
the same code built again (`--rebuild`). People see `version` only (the apps' My page shows "Server version 0.9.2").
Releases 0.9.0+1 … 0.9.0+12 came before this rule; 0.9.1 was the first under it.

## Making a release

```
python3 tools/release/bump.py patch              # or minor / major / an exact 0.9.1; build = current + 1 (or --build N)
                                                 # --min-app / --min-server when they change; --rebuild for the same version
git commit -am "Release 0.9.1"                   # version.json, pyproject.toml, uv.lock, CHANGELOG.md
git tag -a v0.9.1 -m "Kelo 0.9.1 (build 2)"      # bump.py prints this line; it never tags, commits or pushes
```

`bump.py` writes `version.json`, the version in `pyproject.toml` and the project's own entry in `uv.lock`, and turns the
CHANGELOG's `## Unreleased` into `## 0.9.1 (build 2, <date>)` under a new empty `## Unreleased`. The release image workflow
(`.github/workflows/release-images.yml`) runs `bump.py --check-tag <tag>` and stops when the tag and `version.json` disagree
(`v0.9.1-beta.1`, the beta channel, is release 0.9.1).

## Where the server shows it

- `GET /api/about`: `version`, `build`, `min_app`, `min_server` (plus `min_app_version`, `min_app_ignored` and `db`).
- Every HTTP answer: header `X-Kelo-Server: 0.9.0+1`.
- The start-up log line `Kelo server 0.9.0 (build 1)`, and `assistant version` (`--json` for scripts).
- The image: `/app/version.json`, and the label `org.opencontainers.image.version` (a local build gets it from
  `tools/build/server.sh`; release images from the release workflow, which also adds `chat.kelo.release`).
- `pyproject.toml` carries the same version (a test checks that it, `uv.lock` and `version.json` agree).

## How each app reads it (wired by each app's own agent)

Every app reads `version.json` at **build time** (never at run time from the network), shows `version` to people (My → About,
settings), and compiles in `min_server`.

- **The version line in My** (all apps, `apps/shared/.../core/SourceLink.kt`, iPhone `KeloCore SourceLink.swift`, same test vectors):
  "App version: 0.10.2 · android-0.10.2". The label is the release tag and links to `https://github.com/kelo-chat/kelo/tree/<tag>`.
  Tags: `android-<versionName>`, `ios-<marketing version>`, `windows-<package version>`, `macos-<package version>`. The public repository
  has one commit per release, so a commit of the private repository would be a 404 there; no commit is built into the app (it is
  empty unless `-PkeloCommit=<sha>` is passed), which keeps a build of the public tag identical to the release. A build from changed
  files shows "(modified)" and no link. The server line links to `https://github.com/kelo-chat/kelo-server/tree/release-<server version>`
  (the version from `GET /api/about`). A version is valid only as digits separated by dots; anything else (empty, `dev`, `1.0.2-rc1`)
  shows no tag. iPhone: the Info.plist key `KeloSourceCommit` is empty by default; `KELO_EMBED_COMMIT=1` embeds the commit.
- **Android**: `versionName` = `version` (e.g. `0.9.0`). `versionCode` stays the store's rising number
  (`apps/android/kelo/version.properties`, set at release time), because Google Play needs it to grow and it is already above any
  `build`. Gradle reads `../../version.json` (relative to `apps/android/kelo/app`). The Android source is `apps/android/kelo` plus `apps/shared/` (the Kotlin module both apps use).
- **iPhone**: `CFBundleShortVersionString` = `version` and `CFBundleVersion` = the store's rising build number. If App Store
  Connect refuses a version lower than the published `1.0.0`, the short version stays `1.0.0` in the store and the app shows
  `version` from `version.json` in its About screen; the header below still says `0.9.0`.
- **Desktop (Windows, macOS)**: installers keep their own rising package number `1.0.8`, `1.0.9`, ... (1.0.7 is published; MSI
  and macOS need major >= 1, and an upgrade needs a higher number). The app shows `version` (`0.9.0`) and sends it in the header;
  `/download/latest.json` keeps the package number for updates.

## MIN VERSION CONTRACT (both directions)

**The app says which release it is** on every request (also inside KE2E, where `x-kelo-*` headers travel):

```
X-Kelo-App: <platform>/<version>[+<platform build>]     e.g. android/0.9.0+402400, ios/0.9.0+57, desktop/0.9.0+100000008
```

`platform` is `android`, `ios`, `desktop`, `web` or `cli`; the build is the store number the app already reports (Android
versionCode, iOS CFBundleVersion, desktop version code `major*100000000 + minor*100000 + patch` of the package number).

**Server -> app ("this app is too old")**: an `/api/*` request whose `X-Kelo-App` is older than `min_app`, or whose build is below
the operator's per-platform minimum (`ASSISTANT_MIN_APP_ANDROID` / `_IOS`, as before), gets

```
426 {"code": "app_too_old", "detail": "...", "min_app": "0.9.0", "min_app_version": {...}, "server": "0.9.1"}
```

The app shows its full-screen "update needed". Not refused: `/api/about`, `/api/e2e/info`, `/healthz`, the website and
`/download/*`, and any request without the header (older apps keep reading `min_app_version` from `/api/profile` as before; a
header that does not parse is ignored). The desktop's package-number minimum (`ASSISTANT_MIN_APP_DESKTOP`) stays advisory in
`min_app_version` (its app compares it itself).

## Minimum app version rule

**A minimum app version is never a version that was not released to people.** If a server demanded an app nobody can install,
every phone would show "update required" with no way out.

An app version counts as *released* only when ordinary people can install it:

- a store build that is live: Google Play production or open testing, App Store "Ready for Distribution", Microsoft Store published;
- or a file offered at kelo.chat/download (`assistant publish-app`, i.e. `/download/latest.json`).

TestFlight, internal testing, a build on a developer's machine and a draft submission do **not** count.

The record of what was released is `releases/apps.json` (tracked; shipped in the image):

```json
{"android": [{"version": "0.9.3", "build": 409159, "channel": "download", "date": "2026-11-13"}], "ios": [], "desktop": []}
```

`version` is what the app reports in `X-Kelo-App`; `build` is the platform's number (Android versionCode, iOS CFBundleVersion,
desktop `major*100000000 + minor*100000 + patch`); `channel` is `download`, `play`, `appstore` or `msstore`.

**Recording a release.** `assistant publish-app` adds its entry by itself (a file at /download is live at once; on a copy
without the repo it prints the exact line to add). A store release is recorded by the operator **when it is live**, never when
it is submitted (review can take days): the store scripts (`apps/android/play/upload.py`, `tools/build/asc.py`,
`apps/desktop/store/msstore.py`) print the command after a successful submission:

```
assistant release-record <android|ios|desktop> <version> <build> <download|play|appstore|msstore> [--date YYYY-MM-DD]
```

It checks its input, refuses a duplicate, and appends to `releases/apps.json`; commit the file. A minimum of `0.0.0` demands
nothing.

**When an app release goes live** its request fixture is added too: `server/tests/fixtures/app_requests/<platform>-<version>.json`,
recorded from that release's API client code at its tag (the requests it makes on start and in normal use, and the answer fields it
reads). `server/tests/test_released_apps_contract.py` replays the newest fixture of each platform against a fresh server and fails when
a server change would give that app a 426, a missing route or a lost field (docs/version-notices.md). Record the oldest server the app
works with too: `assistant release-record ... --min-server 0.9.0`; the server only tells an app about a newer app whose `min_server`
it meets (`X-Kelo-App-Update`, `app_update` in /api/about; the app sends `X-Kelo-App-Channel`).

**What refuses anything else** (`server/src/assistant/app_releases.py`):

- `server/tests/test_min_app_released.py`: `version.json` `min_app` must not be above a released version of any platform (CI fails).
- `tools/release_manifest.py` and `assistant publish-server`: refuse a manifest whose `min_app` is above the newest released
  version of a platform, naming the platform and that version.
- Server start: an operator minimum (`ASSISTANT_MIN_APP_*`) above the record is logged as an error and **ignored** for that
  platform (the server still starts); `/api/about` lists it in `min_app_ignored`, and `/api/profile` does not carry it.
- `VersionMiddleware`: a `version.json` `min_app` above the record (a misbuilt image) sends no 426 to that platform; logged once.

**App -> server ("this server is too old")**: every answer carries `X-Kelo-Server: <version>+<build>` (and `/api/about` the same
fields). An app whose compiled-in `min_server` is newer than the server's `version` shows "the server must be updated" (with the
owner's way to update) instead of failing calls one by one. A missing header means a server older than 0.9.0: too old for an app
that needs KE2E.
