# Build and release scripts

One command per build, for every Kelo part. Run them from anywhere; they find the repository themselves.

| Script | Commands | What it makes |
|---|---|---|
| `android.sh` | `debug\|release` × `play\|sideload\|full`, `bundle [play\|full]`, `version` | APK / Play App Bundle (quick builds on this Mac); picks the next release number |
| `android-reproducible.sh` | `[--targets play-aab,sideload-apk,full-apk] [--sign]` | the same files built in a pinned Docker environment, byte for byte reproducible: **the builds that go to the stores** (see `docs/verify-builds.md`) |
| `ios.sh` | `sim`, `testflight` | simulator test run / TestFlight build |
| `desktop.sh` | `test`, `mac`, `windows` | desktop tests / macOS DMG / Windows MSI, EXE, MSIX (built on GitHub) |
| `server.sh` | `build`, `test`, `all` | the `assistant:local` image / the server tests; prints the deploy command |
| `asc.py` | `check`, `app-id`, `builds`, `wait-build`, `get` | App Store Connect API helper (used by `ios.sh`) |

Every script has `-h` and `--dry-run`. With `--dry-run` it only prints the commands it would run and changes nothing: no build,
no version bump, no upload, no workflow.

## Rules for all scripts

- `set -euo pipefail`, plain macOS bash 3.2.
- **Disk check:** a script stops before building if the data volume has less than 8 GB free (`KELO_MIN_FREE_GB` changes it).
- **Timeouts:** every long step runs with a hard limit, in its own process group. When the limit is up, only that group
  (the build and its children) is stopped, never anything else on the machine.
- **Logs:** one log per step in `$TMPDIR/kelo-build-logs/` (`KELO_BUILD_LOGS`), overwritten by the next run, so they do
  not pile up. On failure the last 40 lines are shown.
- **Secrets:** nothing is printed. Passwords stay in `apps/android/keystore/keystore.properties` and Gradle reads them. The App
  Store Connect key is passed as a file path. Its key id and issuer id are blanked out in any log lines shown.
- **Idempotent:** running a command again gives the same kind of result. The output files are overwritten, nothing is
  appended, and no state is left behind.
- **Locks (optional):** with `KELO_LOCK_DIR=<folder>`, `ios.sh` takes `<folder>/XCODE_LOCK` (and `DEVICE_LOCK` for `sim`)
  with `mkdir` before heavy Xcode work. It takes all or none, waits at most `KELO_LOCK_WAIT` seconds (default 3600) and gives
  the locks back at the end, also after a failure. Several agents building on the same Mac use this. Without the variable,
  nothing is locked.

## Prerequisites

| For | Needs |
|---|---|
| Android, desktop | JDK 17 at `/opt/homebrew/opt/openjdk@17` (`brew install openjdk@17`; other path: `KELO_JAVA_HOME`). Android SDK named in `apps/android/kelo/local.properties` (`sdk.dir`) |
| Android release | `apps/android/keystore/keystore.properties` + the `.jks` it names (not in git; keep a backup: Play only accepts updates signed with this upload key) |
| Play upload | a Play service account JSON key in `KELO_PLAY_KEY` (see `apps/android/play/PLAY.md`). **There is none on this Mac yet.** |
| iOS | Xcode (26.x), `xcodegen` (`brew install xcodegen`), the simulator `kelo-ios-check` (made once, see `apps/ios/README.md`; the script never creates simulators) |
| TestFlight | `~/.appstoreconnect/kelo.env` with `ISSUER_ID`, `KEY_ID`, `KEY_PATH` (path to the `AuthKey_….p8`), both files mode 600. The key's role must allow cloud signing (Admin or App Manager); the current key uploaded build 26110913 this way. No certificate or profile is needed on the Mac: Apple signs in the cloud |
| desktop windows | `gh` signed in (`gh auth login`) with rights to run workflows in `kelo-chat/kelo` (`kelo-chat/kelo-src` until the source is public) |
| macOS DMG signing | a "Developer ID Application" certificate in the login keychain. Notarizing also uses the App Store Connect key. **Neither certificate is there yet**, so the DMG is unsigned |
| server | Docker (on this Mac: colima; never restart it without asking), `uv` |

## Android: `tools/build/android.sh`

```sh
tools/build/android.sh debug sideload            # quickest check: debug APK of the file build
tools/build/android.sh release sideload          # signed APK for kelo.chat/download/android
tools/build/android.sh release sideload --publish   # ... and offer it on the download page (main copy only)
tools/build/android.sh release full              # signed APK with SMS / call log / notification reading (adb installs)
tools/build/android.sh release play              # signed APK of the Play build, to try on a phone
tools/build/android.sh bundle                    # signed .aab for Google Play (play build)
tools/build/android.sh release play --test       # run that build's unit tests first
```

- **Version:** `0.1.N` / versionCode `N`, N = minutes since late 2026. The formula lives only in
  `apps/android/kelo/app/build.gradle.kts`. The script reads its offset, works out N once and passes `-PkeloBuild=N`, then checks the
  APK's `output-metadata.json` says the same. `--build N` rebuilds a given version (e.g. the Play bundle of the same release).
  At the end it prints the CHANGELOG heading (`## 0.1.N — date`). A **release** build (and `android.sh version`, which only picks
  the number) also writes N into the tracked `apps/android/kelo/version.properties`: that file is where Gradle reads the version when
  no `-PkeloBuild` is given, so **commit it with the release** and every later build of that commit (yours, CI, anyone's) has
  the same version. Nothing in the build reads the clock otherwise.
- **Signing:** release builds refuse to run without `apps/android/keystore/keystore.properties` (`--unsigned` allows an unsigned
  build). The result is checked with `apksigner verify` (APK) or `jarsigner -verify` (AAB).
- **Outputs:** `apps/android/kelo/app/build/outputs/apk/<flavor>/<type>/app-<flavor>-<type>.apk`,
  `apps/android/kelo/app/build/outputs/bundle/<flavor>Release/app-<flavor>-release.aab`. `--out DIR` also copies the file as
  `kelo-0.1.N-<flavor>-<type>.apk`.
- **`--publish`** (release sideload only) runs `uv run assistant publish-app android <apk> --version 0.1.N` in the repository
  folder, which must be the main copy (`compose.yml` + `.env`).
- **Play upload** is not done by the script. After `bundle` it prints the `apps/android/play/upload.py` command. That needs
  `KELO_PLAY_KEY`, and it creates a draft on the internal track.
- Gradle runs with `--no-daemon` and the Kotlin compiler in-process, so no Gradle or Kotlin daemon is left running.

## Android for the stores: `tools/build/android-reproducible.sh`

```sh
tools/build/android.sh version                     # 1. pick the release number (writes apps/android/kelo/version.properties)
git add apps/android/kelo/version.properties && git commit -m "Android 0.1.N" && git tag v…   # 2. commit and tag (public repo: the owner's call)
tools/build/android-reproducible.sh --sign         # 3. Play bundle + sideload APK from that commit, signed with the upload key
tools/build/android-reproducible.sh --targets play-aab,sideload-apk,full-apk --out DIR     # unsigned, as CI builds them
```

Builds the committed source (`--worktree`: the working tree) in the image of `apps/android/reproducible/Dockerfile` (JDK, Android
SDK and build tools pinned by checksum; `linux/amd64`, so on an Apple Silicon Mac it runs emulated and takes hours; on GitHub's
runners about 15 minutes). The result is `<out>/kelo-0.1.N-<flavor>-release-unsigned.{aab,apk}` plus `SHA256SUMS`; `--sign` adds
`...-signed...` copies (AAB with `jarsigner`, APK with `apksigner`, from `apps/android/keystore/keystore.properties`) after the build.
The signature is the only difference between the owner's file and the one anyone else builds. The public CI
(`.github/workflows/release-build.yml`) builds exactly this way and attests every file. Checking a store app against it:
`tools/verify/android-verify.sh` (`docs/verify-builds.md`).

## iOS: `tools/build/ios.sh`

```sh
tools/build/ios.sh sim                      # package tests + app build + UI walk on kelo-ios-check
tools/build/ios.sh sim --skip-packages --lang ko --shots /tmp/kelo-shots
tools/build/ios.sh sim --only KeloUITests/KeloScreensTests/testWalk --only KeloUITests/KeloScreensTests/testModelFold
tools/build/ios.sh testflight --dry-run     # shows the next build number and the steps
tools/build/ios.sh testflight               # bump, archive, upload, wait for processing
```

**`sim`** runs these steps in order:

1. `apps/ios/KeloCore/test.sh` and `apps/ios/KeloUI/test.sh` (`swift test`), each with a temporary `--scratch-path`.
2. `apps/ios/tools/gen_strings.py` and `xcodegen generate`.
3. It starts `apps/desktop/tools/mock_server.py` on a free port (never 8765), resets the app and the keychain on `kelo-ios-check`,
   and runs `xcodebuild test` (default `KeloScreensTests/testWalk`) with the `TEST_RUNNER_KELO_*` settings.

Failed steps are listed at the end, and the script exits 1. It stops only the mock server it started itself. It shuts the
simulator down only if it booted it. `--no-mock` only builds and starts the test runner, because the UI tests skip without a
server.

**`testflight`** runs these steps in order:

1. It checks the key (`asc.py check`).
2. It picks the build number: `YYMMDDHH`, but always above both `project.yml`'s `CURRENT_PROJECT_VERSION` and App Store
   Connect's newest build (`--build N` overrides). It writes the number into `apps/ios/Kelo/project.yml`. Commit that change.
3. Strings and xcodegen.
4. An unsigned Release archive (40 min limit).
5. `xcodebuild -exportArchive` with `destination=upload` and `signingStyle=automatic`, signing with the API key
   (`-allowProvisioningUpdates -authenticationKey…`). Apple signs the app in the cloud and it is uploaded (30 min limit).
6. `asc.py wait-build` waits until App Store Connect reports the build `VALID` (45 min, `--wait-timeout`). It exits 2 on
   `FAILED`/`INVALID`, and on timeout it says how to check again later. `--no-wait` skips this step.

`--keep-archive DIR` keeps the `.xcarchive` with its dSYMs.

**`asc.py`** uses only the standard library plus `openssl`. It never prints the key or the token, and it sends the token only
to `api.appstoreconnect.apple.com`. To look at builds by hand: `python3 -I tools/build/asc.py builds`.

## Desktop: `tools/build/desktop.sh`

```sh
tools/build/desktop.sh test                   # unit tests, with a pass/fail count
tools/build/desktop.sh mac                    # dist/Kelo-<version>[-unsigned].dmg
tools/build/desktop.sh windows --dry-run      # what would be started on GitHub
tools/build/desktop.sh windows                # run the workflow, wait, download to dist/windows-<version>/
tools/build/desktop.sh windows --run-id 123   # only wait for / download an existing run (e.g. one a push started)
```

- **Version:** `--version X.Y.Z`. The default is the `default:` version in `.github/workflows/desktop-windows.yml`. That
  is the one place for the desktop version: raise it there for a release.
- **mac:** `./gradlew packageDmg -PkeloVersion=…`. If the keychain has a "Developer ID Application" certificate, the build is
  signed (`compose.desktop.mac.sign`) and then notarized with `xcrun notarytool` (App Store Connect key) and stapled.
  Without that certificate the DMG is built **unsigned**, named `-unsigned.dmg`, and the script says so.
- **windows:** MSI, EXE and MSIX can only be built on Windows, so the script runs these steps on GitHub:
  1. `gh workflow run desktop-windows.yml --ref <branch> -f version=…`
  2. It finds the new run.
  3. `gh run watch --exit-status`, with a hard limit (`--timeout MIN`, default 60). On timeout the run keeps going on
     GitHub and can be fetched later with `--run-id`.
  4. `gh run download` into `dist/windows-<version>/`.

  The runner builds what is pushed to GitHub, not this folder: the script warns about uncommitted changes and unpushed
  commits. A push that changes `apps/desktop/**` also starts the workflow by itself.
- `dist/` is in `.gitignore` and `.dockerignore`.

## Server: `tools/build/server.sh`

```sh
tools/build/server.sh build                    # docker build -f docker/server.Dockerfile -t assistant:local .
tools/build/server.sh test                     # uv run pytest (the whole suite, ~10-15 min)
tools/build/server.sh test -- server/tests/test_updates.py -q
tools/build/server.sh all                      # build, then test
tools/build/server.sh build --tag assistant:check --remove-image   # build check that leaves no image
```

`build` uses the same image tag and `BROWSER` build argument as `compose.yml`. Retagging `assistant:local` does not change
running containers; they switch only when they are recreated. The script **never deploys**. At the end it prints the
command for the main copy:

```sh
cd path/to/kelo && uv run assistant update
```

`assistant update` backs up, builds, restarts, checks health and rolls back by itself on failure. `--no-build` reuses the
images already built. Extra copies: `uv run assistant instance update`.

## Signing the builds offered on kelo.chat (the operator's computer)

The apps offer an update only when its row in `/download/latest.json` is signed with a release key built into the app
(`docs/verify-builds.md`, "The update inside the app"). The private key stays on the operator's Mac: never in a container, on the
state volume or inside the compose folder (the commands refuse such a path and a key file others can read).

1. Once: `assistant release-key new` (default `~/.kelo-release/signing.key`, mode 600). It prints the key id and the public key.
2. Keep an offline copy of the private file (a USB stick or a password manager). A lost key means a new app release to change keys.
3. Add the key id and public key to **both** key files, then ship an app release that has them:
   `server/src/assistant/release_keys.py` (`RELEASE_KEYS`) and `apps/shared/src/commonMain/kotlin/chat/kelo/app/update/ReleaseKeys.kt`
   (`ReleaseKeys.ALL`). A test keeps the two equal; `publish-app` refuses a key that is not in `RELEASE_KEYS`.
4. Publish each build with the public commit it was built from (the full 40-character hash):
   `assistant publish-app android dist/kelo.apk --version 0.9.0+402500 --commit <hash>`. The key is `~/.kelo-release/signing.key`
   unless `--sign-key PATH` is given. `--unsigned` publishes without a signature (apps that check signatures do not offer it).
5. Builds that are already live: `assistant sign-app <android|windows|windows-msi|windows-server-msi|mac> --commit <hash>` signs the row in the manifest
   (the file is not touched; its sha256 is checked against the manifest first).
6. Rotating: add the next key to both files in an app release first, and sign with the next key only after that release is what
   people run. Every listed key is accepted, so old and new apps both keep working during the switch.

## Disk: what each script cleans up

| Script | Removed at the end (also after a failure or Ctrl-C) | Left on purpose |
|---|---|---|
| all | their temp folder `$TMPDIR/kelo-build-<name>.XXXXXX` | step logs in `$TMPDIR/kelo-build-logs/` (overwritten each run) |
| android.sh | (Gradle without daemons: no background JVMs stay) | `apps/android/kelo/app/build` (incremental builds; `./gradlew clean` frees ~1-2 GB) |
| ios.sh | DerivedData, SwiftPM scratch folders and their lock files, the archive and export folder, screenshots (unless `--shots`), the mock server process | the build-number change in `project.yml`; the archive only with `--keep-archive` |
| desktop.sh | the downloaded artifact folder (the files are moved to `dist/`) | `apps/desktop/build`, `dist/` |
| server.sh | pytest's temp folder (`--basetemp`) | the image; `--remove-image` removes it. Old images: `docker image ls assistant`, and `docker image prune` for untagged ones (by hand) |

## Troubleshooting

- **"only N GB free":** delete old DerivedData (`~/Library/Developer/Xcode/DerivedData`), `./gradlew clean` in
  `apps/android/kelo` and `desktop`, old Docker images (`docker image ls`), or old files in `apps/android/dist` and `dist/`.
- **Android "keystore.properties names a storeFile that is not in apps/android/keystore":** restore the `.jks` from the
  backup. A new key cannot update the Play app.
- **Android "the APK says version …, expected …":** `build.gradle.kts` no longer reads `-PkeloBuild`. Bring the
  `findProperty("keloBuild")` line back.
- **iOS "no simulator named kelo-ios-check":** create it once, as in `apps/ios/README.md`. Another name: `KELO_SIM_NAME=…`.
- **iOS export "No Accounts" / "No profiles":** the API key's role cannot do cloud signing (needs Admin or App Manager), or `kelo.env` points to a wrong
  `.p8`. Check with `python3 -I tools/build/asc.py check`.
- **TestFlight "build number already used" or not higher:** pass a higher `--build N`. `asc.py builds` lists the newest
  ones.
- **Processing takes longer than 45 min:** the upload is done. Check later with `python3 -I tools/build/asc.py wait-build
  --version N --timeout 1800`.
- **windows run fails:** `gh run view <id> --log-failed`. The usual cause is a text the string check does not like: run
  `python3 apps/android/tools/strings_check.py`.
- **A step timed out:** the limits can be raised with `KELO_GRADLE_TIMEOUT`, `KELO_XCODE_TIMEOUT`,
  `KELO_ARCHIVE_TIMEOUT`, `KELO_EXPORT_TIMEOUT`, `KELO_DOCKER_TIMEOUT`, `KELO_PYTEST_TIMEOUT` (seconds).
