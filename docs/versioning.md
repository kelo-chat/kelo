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

## Making a release

```
python3 tools/release/bump.py 0.9.1              # build = current + 1 (or --build N); --min-app / --min-server when they change
git commit -am "Release 0.9.1"                   # version.json, pyproject.toml, uv.lock, CHANGELOG.md
git tag -a v0.9.1 -m "Kelo 0.9.1 (build 2)"      # bump.py prints this line; it never tags, commits or pushes
```

`bump.py` writes `version.json`, the version in `pyproject.toml` and the project's own entry in `uv.lock`, and turns the
CHANGELOG's `## Unreleased` into `## 0.9.1 (build 2, <date>)` under a new empty `## Unreleased`. The release image workflow
(`.github/workflows/release-images.yml`) runs `bump.py --check-tag <tag>` and stops when the tag and `version.json` disagree
(`v0.9.1-beta.1`, the beta channel, is release 0.9.1).

## Where the server shows it

- `GET /api/about`: `version`, `build`, `min_app`, `min_server` (plus `min_app_version` and `db` as before).
- Every HTTP answer: header `X-Kelo-Server: 0.9.0+1`.
- The start-up log line `Kelo server 0.9.0 (build 1)`, and `assistant version` (`--json` for scripts).
- The image: `/app/version.json`, and the label `org.opencontainers.image.version` (a local build gets it from
  `tools/build/server.sh`; release images from the release workflow, which also adds `chat.kelo.release`).
- `pyproject.toml` carries the same version (a test checks that it, `uv.lock` and `version.json` agree).

## How each app reads it (wired by each app's own agent)

Every app reads `version.json` at **build time** (never at run time from the network), shows `version` to people (My → About,
settings), and compiles in `min_server`.

- **Android**: `versionName` = `version` (e.g. `0.9.0`). `versionCode` stays the store's rising number
  (`android/kelo/version.properties`, set at release time), because Google Play needs it to grow and it is already above any
  `build`. Gradle reads `../../version.json` (relative to `android/kelo/app`).
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

**App -> server ("this server is too old")**: every answer carries `X-Kelo-Server: <version>+<build>` (and `/api/about` the same
fields). An app whose compiled-in `min_server` is newer than the server's `version` shows "the server must be updated" (with the
owner's way to update) instead of failing calls one by one. A missing header means a server older than 0.9.0: too old for an app
that needs KE2E.
