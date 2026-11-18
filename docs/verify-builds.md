# Check that the app you downloaded is the public source

Korean version: [ko/verify-builds.md](ko/verify-builds.md).

Kelo's source is public. This page shows how you can check that the app on your phone or computer was built from it, and
says plainly where that check is strong, where it is weaker, and where it is not possible.

| What you have | What you can check | How strong |
|---|---|---|
| Android APK from kelo.chat | Build the tagged source yourself and compare it with the APK on your phone | Same file, byte for byte, except the signature |
| Android app from Google Play | Not byte for byte: Google Play adds its own protection code to the apps it delivers (automatic protection, on for Kelo). Use the APK from kelo.chat to check | None for the Play build itself |
| Windows installers (MSI, EXE, MSIX) or installed app | Every file of the app against the source, apart from the signatures (`tools/verify/windows-verify.ps1`) | Strong for the code, none for the signatures; second check: `gh attestation verify` (provenance) |
| macOS disk image | Every file of the app against the source, apart from Apple's signature (`tools/verify/mac-verify.sh`) | Strong for the code, none for the signature itself |
| iPhone app | The readable parts of the app against the source | Weak. A byte-for-byte check is impossible (see below) |
| Server images | `cosign verify` (see `release-images.yml`) | Signed by the public workflow |

Whatever you check, you are trusting two things that are not Kelo: the toolchain (Docker, the JDK, Google's Android build tools,
Gradle and the libraries, pinned by checksum below) and that you read the source you care about. The check proves that the
file equals what that source builds to. It does not prove the source is free of mistakes.

## Android

### One command

You need Docker, `git`, `adb` (Android platform-tools), Java 17 or newer (for `bundletool`), `python3` and `curl`. Connect the phone
with USB debugging on (`adb devices` shows it), then:

```sh
git clone https://github.com/kelo-chat/kelo && cd kelo
git checkout <tag of your version>            # My page shows the version; tags are listed on GitHub
tools/verify/android-verify.sh                # finds the tag by the phone's version, or: --tag <tag>
```

The script:

1. asks the phone for the app's version and where it was installed from (Google Play or a file);
2. gets the source of the tag and checks that its `apps/android/kelo/version.properties` has the same version as the phone;
3. builds it with `tools/build/android-reproducible.sh` in the pinned Docker image (the source it builds is `apps/android/kelo`, `apps/shared/` and `version.json`) (the first build is long: a few hours on an
   Apple Silicon Mac, where the Linux x86-64 image runs emulated; much faster on an x86-64 PC);
4. **Google Play version** (since Kelo turned on Google Play's automatic protection, 2026-11-15, Google adds its own checking code to
   the Play build, so this comparison reports differences in the code: install the APK from kelo.chat to verify; the steps below stay
   for builds without that protection): `bundletool build-apks --connected-device` makes the APKs your phone should get from the bundle, `adb`
   pulls the APKs installed on the phone (`base.apk` and the `split_config.*.apk` files) and `tools/verify/apkdiff.py` compares them
   pair by pair (a pair is the same split: base, `config.arm64_v8a`, `config.xxhdpi`, `config.en`, ...).
   **File version:** the one APK is compared with the one on the phone;
5. prints `APKs match!` or what differs, and exits 0 or 1.

What `apkdiff.py` leaves out, and nothing else:
`META-INF/MANIFEST.MF` and `META-INF/*.SF|RSA|DSA|EC` (jar signatures; the APK signature block itself is not a file in the zip),
`stamp-cert-sha256` (a Google Play stamp), and in `AndroidManifest.xml` the `<meta-data>` elements Google Play adds
(`com.android.vending.derived.apk.id`, `com.android.stamp.source`, `com.android.stamp.type`). Files are compared by their
uncompressed bytes, so a different zip compression or file order does not matter. Google Play signs with its own key, so a
signature comparison could never match; the signature is what you cannot check this way.

If the script says a split is missing or extra, your phone's language or screen density set differs from the one the build made
for the connected device (Google Play also looks at all your languages). Compare the files that exist on both sides; the others
are not a sign of a changed app.

### The same by hand

```sh
git clone https://github.com/kelo-chat/kelo && cd kelo && git checkout <tag>
tools/build/android-reproducible.sh --targets play-aab --out build-out   # or sideload-apk
java -jar bundletool-all-1.18.3.jar build-apks --bundle=build-out/kelo-0.1.N-play-release-unsigned.aab \
     --output=mine.apks --connected-device --overwrite
mkdir phone && for p in $(adb shell pm path chat.kelo.app | tr -d '\r' | sed 's/^package://'); do adb pull "$p" phone/; done
python3 tools/verify/apkdiff.py --splits mine.apks phone
```

For the APK from kelo.chat: `tools/build/android-reproducible.sh --targets sideload-apk`, then
`python3 tools/verify/apkdiff.py build-out/kelo-0.1.N-sideload-release-unsigned.apk <the APK you installed>`. Its signature
fingerprint is the owner's upload key: `apksigner verify --print-certs <apk>`.

### Without the script: CI

Every release tag is also built in public by GitHub Actions
([`release-build.yml`](../.github/workflows/release-build.yml)), in the same Docker environment, and each file has an attestation:

```sh
gh attestation verify kelo-0.1.N-play-release-unsigned.aab -R kelo-chat/kelo
```

The SHA-256 of what the run built is in the run's summary and in `SHA256SUMS`. Compare it with your own build's
`SHA256SUMS`: the `-unsigned` files are identical byte for byte. The owner adds only a signature to them before uploading.

### What makes the build the same everywhere

| Source of difference | What Kelo does |
|---|---|
| Version from the build time | The version is `apps/android/kelo/version.properties` (`build=N` gives versionCode N and versionName `0.1.N`), committed with each release. `tools/build/android.sh version` writes it. The minutes-since-late-2026 rule only picks the number when the owner starts a release |
| JDK, Gradle, Android SDK, build tools | `apps/android/reproducible/Dockerfile`: the JDK image by digest, Android platform 36 (r02) and build tools 36.0.0 by SHA-256; Gradle 8.11.1 by `distributionSha256Sum` in the wrapper properties |
| The release shown in My | The version line in My ends with the release tag of the app (`android-0.10.2`, `ios-<version>`, `windows-<package number>`, `macos-<package number>`), a link to that tag in `kelo-chat/kelo` on GitHub (the server line links to `release-<version>` in `kelo-chat/kelo-server`). The link goes by tag because the public repository has one commit per release: the private development commit would give a 404 there. No commit is built into the app (`BuildConfig.SOURCE_COMMIT` and `ReleaseInfo.SOURCE_COMMIT` are empty unless `-PkeloCommit=<sha>` is passed, for people who want it), so a build of the public tag gives the same bytes as the release whatever commit it sits at. A `--worktree` build (or one from changed files) says "modified" and has no link. On the iPhone the Info.plist key `KeloSourceCommit` is empty by default, and `KELO_EMBED_COMMIT=1` embeds the commit. |
| Libraries and plugins | Exact versions in `gradle/libs.versions.toml`; every downloaded file is checked against `gradle/verification-metadata.xml` (strict in the reproducible build) |
| R8's timing note in the bundle | R8 writes how long it ran and with how many threads into `BUNDLE-METADATA/com.android.tools/r8.json`; the two numbers are blanked (`app/build.gradle.kts`). This was the only difference between two builds of the bundle |
| Encrypted dependency list in the APK | `dependenciesInfo { includeInApk = false }`: a file APK has no per-build random value (the bundle keeps its plain list) |
| Source folder, user, time zone, machine | Tested: the same files from two different folders, a different folder inside the container, two time zones |
| Native libraries | The image has no NDK, so native libraries are kept as shipped; an NDK would strip them (also deterministic, but the two must not be mixed) |
| Firebase settings | `apps/android/kelo/app/google-services.json` is part of the source (next section) |

Not covered: the Google Play signature (Play signs with its own key), and anything Google Play itself changes after the upload
beyond the stamp described above. I could not test against a real Play install yet: the comparison was tested with the bundle
installed by `bundletool` on an emulator. The first real Play release should be run through `android-verify.sh` once by the owner,
and this page updated with the result.

### The Firebase file and your own build

Push notifications need `apps/android/kelo/app/google-services.json`. It is the Firebase **client** configuration, the same file that
sits inside every APK, so it is public and is committed with the source: that is why the source builds the same APK as the one in the
store. It contains the project number and id, the app id, and a client API key (restrict it to the app in Google Cloud so that only
Kelo can use it). It has no service-account key and nothing secret; the key that sends pushes lives only on the operator's server.

To build with **your own** Firebase project, replace the file with yours (Firebase console → project settings → your Android app
`chat.kelo.app` → download `google-services.json`; `google-services.json.example` shows the shape), or delete it to build
without push. Your build will then differ from the store's in exactly that file, so the comparison reports it.

## Windows

You can check an installed Kelo (`C:\Program Files\Kelo`), the downloaded MSI (`Kelo-<version>.msi`), or the MSIX for the
Microsoft Store against the public source. The installer itself is not byte-for-byte reproducible (jpackage and the MSI tools
write times and ids into the installer binaries), but **every file of the app inside it** is checked against what the public
source builds to.

In Windows PowerShell (5.1+) or PowerShell 7, in a checkout of this repository:

```powershell
powershell -ExecutionPolicy Bypass -File tools\verify\windows-verify.ps1 <Kelo-1.0.9.msi | Kelo-1.0.9.0.msix | "C:\Program Files\Kelo">
```

(The EXE installer wraps the same files as the MSI; test the MSI from `kelo.chat/download/windows-msi`).

The script:

1. unpacks the MSI (`msiexec /a`, an administrative install into a temporary folder: no administrator rights needed) or the MSIX
   (as a zip), or reads the installed folder directly;
2. reads the package number from the app's own jar (`chat.kelo.desktop.core.ReleaseInfo`); the app does not name a commit, so you give
   `-Commit <40 hex>` (the commit of the tag `windows-<package number>` in the public repository) or `-Source <dir>`;
3. downloads that commit's source from GitHub (a zip: no `git` is needed on the PC) or takes an unpacked folder with `-Source <dir>`;
4. builds it unsigned with the pinned Java runtime (`apps/desktop/packaging/windows/JDK`: Eclipse Temurin, downloaded once into
   `%LOCALAPPDATA%\kelo\jdk` and checked against its SHA-256) and the fixed zip entry times of `tools/build/zipclock`:
   `gradlew createDistributable`;
5. compares the two app folders file by file, and inside every jar entry by entry (`tools/verify/appdiff.py --windows`). Python 3 is
   taken from the PC, or a small pinned embeddable Python (`apps/desktop/packaging/windows/PYTHON`) is downloaded once.

It ends with `every file is the same as the source at commit <hash>, apart from signatures` (exit 0) or lists what differs (exit 1).

### What is left out and why

- **Authenticode signatures:** `.exe` and `.dll` files (including those inside jars, like `skiko.dll`) have their certificate table
  and PE header checksum stripped on both sides before comparing. The signature is made with the owner's certificate and cannot
  be reproduced by anyone else, so only the code and data are compared.
- **MSIX packaging files:** `AppxManifest.xml`, `AppxSignature.p7x`, `AppxBlockMap.xml`, `[Content_Types].xml`, `AppxMetadata/`,
  and `Assets/` (the Store's images) are files that the MSIX packaging adds around the app; they are left out of the comparison
  and listed on stderr as installer files.

### Second check: build provenance

Because the installer binaries themselves contain timestamps and UUIDs, GitHub Actions attaches a Sigstore build provenance
attestation to each `.msi`, `.exe` and `.msix` in `release-build.yml`:

```sh
gh attestation verify Kelo-1.0.9.msi -R kelo-chat/kelo
```

This proves that GitHub Actions built the installer file from that commit with `desktop-windows.yml`, and that it was not
modified afterwards. The installers are not code-signed yet, so Windows SmartScreen asks first.

## macOS

The disk image (`Kelo-X.Y.Z.dmg`) is built on the owner's Mac, signed with a Developer ID certificate and notarized by Apple. That
certificate never leaves the owner's Mac, so nobody else can produce the signed file. What anyone can check is that **everything
inside it** is what the public source builds to.

You need a Mac with the Xcode command line tools, `git`, `python3` and `curl`, and about 8 GB of free disk. Download the disk image,
then, in a clone of this repository:

```sh
git clone https://github.com/kelo-chat/kelo && cd kelo
git fetch --tags
tools/verify/mac-verify.sh ~/Downloads/Kelo-1.0.9.dmg
```

The script:

1. opens the disk image read-only and copies `Kelo.app` out;
2. asks macOS who signed it: `spctl` must say "Notarized Developer ID", and the app's team must be the one written in
   `apps/desktop/packaging/macos/TEAM_ID` (printed both ways);
3. reads the app's version and checks out the tag `mac-<version>` into a temporary folder;
4. builds that tag without a signature (`./gradlew createDistributable`), with the one Java runtime pinned in
   `apps/desktop/packaging/macos/JDK` (Eclipse Temurin, downloaded once into `~/.cache/kelo/jdk` and checked against its SHA-256);
5. compares the two apps file by file, and inside every jar entry by entry (`tools/verify/macdiff.py`).

It ends with "every file is the same as the source at mac-<version> (<commit>), apart from Apple's signature" or lists what differs.
The first run takes about 15 minutes.

What this proves: the code, the Java runtime, the resources and the settings in the app are the ones the tag builds to. What it
cannot: Apple's signature and notarization ticket are made with the owner's certificate and by Apple, so nobody else can
reproduce them. That is why both sides are given the same throwaway signature before they are compared (only the code is
compared). The signature itself is checked the way macOS checks it, against the team id above. The build is the same on every Mac
only with the pinned Java runtime and the fixed file times of `tools/build/zipclock`; `tools/verify/mac-repro-check.sh` builds
one commit twice in different folders, time zones and minutes and must print no difference.

## iPhone

A byte-for-byte check is **impossible** for the App Store app. Apple encrypts every app per account (FairPlay) and re-signs it,
and slices it for your device, so no one, including the owner, can produce the file you have from the source. Nothing we could
build would change that, and a claim that it does would be false.

What you can check instead:

- **The readable parts.** An App Store app's `Info.plist`, entitlements, resources, localized strings and the list of linked
  frameworks are not encrypted. They show the app's identifier and team, the capabilities and permissions it asks for (with the
  reason texts) and which frameworks are linked, so you can see whether an analytics or ad framework is in it. Compare them with
  `apps/ios/` in the source.
- **What the app does.** Put the phone behind a proxy you control and look at where the app connects: it should be the server you
  gave it, plus kelo.chat for the things the privacy page lists.
- **Build it yourself.** `apps/ios/` builds in Xcode with your own signing; the app you build is the app of that source, which you
  can run next to the store one and compare.
- **The server side.** What matters most for your data is the server you run: its images are signed and built in public.

The iPhone's generated files (`KeloVersion.swift` from `version.json`, the `.lproj` strings and `R.swift` from
`apps/shared/strings`) are committed, so `apps/ios/` is exactly what the build compiles. `server/tests/test_ios_generated_files.py`
fails when they differ from what `apps/ios/tools/gen_version.py` and `gen_strings.py` produce, and `tools/build/ios.sh testflight`
refuses to build from such a tree.

An idea we have not built: a CI-built iPhone archive with an attestation, for people who want a binary to inspect. It would still
not be the App Store file.

## The update inside the app

The Android APK from kelo.chat and the Windows and macOS apps check kelo.chat for a newer version. They do not trust the website
alone: each entry of `https://kelo.chat/download/latest.json` carries an Ed25519 signature, made on the owner's own computer
with a key that never goes to the server. The apps carry the matching public key and **offer nothing whose signature does not
check**: an entry that is unsigned, signed by another key or changed afterwards is treated like a missing one, and nothing is
shown. A file is downloaded only from the address the list itself came from, over https (plain http only for a mock on your own
computer), and its sha256 and size must equal the signed ones. The Google Play and App Store builds are updated by the stores.

What the signature covers is exactly this text (UTF-8, `\n` line ends, one final `\n`):

```
kelo-release-v1
platform=<android | windows | windows-msi | mac>
file=<file name>
version=<version>
version_code=<build number, or empty>
size=<bytes>
sha256=<lowercase hex of the file>
commit=<40 hex characters, or empty>
```

`commit` names the public source commit (in `kelo-chat/kelo`) the file was built from, so the update an app installs is tied to the
source you can read and, for Android, rebuild (sections above). The row in `latest.json` repeats it as `"commit"`, next to
`"sig"` (the signature, base64) and `"key"` (the key id: the first 16 hex characters of the sha256 of the raw public key). An
entry published without a commit has an empty `commit=` line.

The public keys are in `apps/shared/src/commonMain/kotlin/chat/kelo/app/update/ReleaseKeys.kt` (key id → base64 of the raw 32-byte
key; the server has the same map in `server/src/assistant/release_keys.py`). Two keys can be listed at once, so a key can be
replaced without a flag day.

Check an entry by hand (`pip install cryptography`; put the public key from `ReleaseKeys.kt` in `PUB`):

```python
import base64, hashlib, json, urllib.request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
PUB, platform = "<base64 public key from ReleaseKeys.kt>", "windows"       # android | windows | windows-msi | mac
r = json.load(urllib.request.urlopen("https://kelo.chat/download/latest.json"))[platform]
pub = base64.b64decode(PUB)
assert hashlib.sha256(pub).hexdigest()[:16] == r["key"], "signed by another key"
text = (f"kelo-release-v1\nplatform={platform}\nfile={r['file']}\nversion={r['version']}\nversion_code={r.get('version_code', '')}\n"
        f"size={r['size']}\nsha256={r['sha256']}\ncommit={r.get('commit', '')}\n")
Ed25519PublicKey.from_public_bytes(pub).verify(base64.b64decode(r["sig"]), text.encode())   # raises InvalidSignature if wrong
print("signature ok;", "built from commit " + r["commit"] if r.get("commit") else "no commit named")
```

Then compare `sha256` with the file you download (`shasum -a 256 <file>`).

## For the maintainer: releasing

1. Android only: when screens moved since the last release, regenerate the baseline profiles first (with the emulator from the android
   notes): `./gradlew :app:generateReleaseBaselineProfile -Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true` in `apps/android/kelo`,
   and commit `app/src/release/generated/baselineProfiles`.
2. `tools/build/android.sh version` (writes `apps/android/kelo/version.properties`), update `CHANGELOG.md`, commit, tag.
3. `tools/build/android-reproducible.sh --sign`: Play bundle and sideload APK from exactly that commit, then signed with the upload
   key. Upload the signed bundle to Play, offer the signed APK on kelo.chat.
4. Push the tag to the public repository: `release-build.yml` builds and attests the same files; its `SHA256SUMS` must equal the
   `-unsigned` sums of step 3 (compare them; if they differ, do not publish).
5. After Google Play has published it, run `tools/verify/android-verify.sh` once on a phone that installed from Play.

macOS (after the Android steps, or on its own):

1. `tools/build/desktop.sh mac` builds `Kelo-X.Y.Z.dmg` (signed and notarized when the Developer ID certificate is in the keychain) with the pinned Java runtime. Tag the commit it was built
   from `mac-<version>` and push the tag to the public repository.
2. Before publishing the disk image, run `tools/verify/mac-verify.sh` on that final file; publish only when it says every file is the
   same.
