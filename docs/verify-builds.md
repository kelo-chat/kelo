# Check that the app you downloaded is the public source

Korean version: [ko/verify-builds.md](ko/verify-builds.md).

Kelo's source is public. This page shows how you can check that the app on your phone or computer was built from it, and
says plainly where that check is strong, where it is weaker, and where it is not possible.

| What you have | What you can check | How strong |
|---|---|---|
| Android app from Google Play | Build the tagged source yourself and compare it with the APKs on your phone | Same files, byte for byte, except the signature |
| Android APK from kelo.chat | The same, with the one APK | Same file, byte for byte, except the signature |
| Windows installers (MSI, EXE, MSIX) | `gh attestation verify` shows which workflow and commit built the file | Provenance. Not reproducible: you cannot rebuild the same bytes |
| macOS disk image | Not covered yet (needs the owner's Apple certificate) | None yet |
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
2. gets the source of the tag and checks that its `android/kelo/version.properties` has the same version as the phone;
3. builds it with `tools/build/android-reproducible.sh` in the pinned Docker image (the first build is long: a few hours on an
   Apple Silicon Mac, where the Linux x86-64 image runs emulated; much faster on an x86-64 PC);
4. **Google Play version:** `bundletool build-apks --connected-device` makes the APKs your phone should get from the bundle, `adb`
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
| Version from the build time | The version is `android/kelo/version.properties` (`build=N` gives versionCode N and versionName `0.1.N`), committed with each release. `tools/build/android.sh version` writes it. The minutes-since-late-2026 rule only picks the number when the owner starts a release |
| JDK, Gradle, Android SDK, build tools | `android/reproducible/Dockerfile`: the JDK image by digest, Android platform 36 (r02) and build tools 36.0.0 by SHA-256; Gradle 8.11.1 by `distributionSha256Sum` in the wrapper properties |
| Libraries and plugins | Exact versions in `gradle/libs.versions.toml`; every downloaded file is checked against `gradle/verification-metadata.xml` (strict in the reproducible build) |
| R8's timing note in the bundle | R8 writes how long it ran and with how many threads into `BUNDLE-METADATA/com.android.tools/r8.json`; the two numbers are blanked (`app/build.gradle.kts`). This was the only difference between two builds of the bundle |
| Encrypted dependency list in the APK | `dependenciesInfo { includeInApk = false }`: a file APK has no per-build random value (the bundle keeps its plain list) |
| Source folder, user, time zone, machine | Tested: the same files from two different folders, a different folder inside the container, two time zones |
| Native libraries | The image has no NDK, so native libraries are kept as shipped; an NDK would strip them (also deterministic, but the two must not be mixed) |
| Firebase settings | `android/kelo/app/google-services.json` is part of the source (next section) |

Not covered: the Google Play signature (Play signs with its own key), and anything Google Play itself changes after the upload
beyond the stamp described above. I could not test against a real Play install yet: the comparison was tested with the bundle
installed by `bundletool` on an emulator. The first real Play release should be run through `android-verify.sh` once by the owner,
and this page updated with the result.

### The Firebase file and your own build

Push notifications need `android/kelo/app/google-services.json`. It is the Firebase **client** configuration, the same file that
sits inside every APK, so it is public and is committed with the source: that is why the source builds the same APK as the one in the
store. It contains the project number and id, the app id, and a client API key (restrict it to the app in Google Cloud so that only
Kelo can use it). It has no service-account key and nothing secret; the key that sends pushes lives only on the operator's server.

To build with **your own** Firebase project, replace the file with yours (Firebase console → project settings → your Android app
`chat.kelo.app` → download `google-services.json`; `google-services.json.example` shows the shape), or delete it to build
without push. Your build will then differ from the store's in exactly that file, so the comparison reports it.

## Windows

`release-build.yml` calls `desktop-windows.yml` on every release tag and attaches an attestation to the `.msi`, `.exe` and
`.msix`:

```sh
gh attestation verify Kelo-1.0.3.msi -R kelo-chat/kelo
```

This proves that GitHub Actions built the file from a given commit of `kelo-chat/kelo` with that workflow, and that it was not
changed afterwards. It does not let you rebuild the same bytes: the installer tools write times and ids into the files. Read
the workflow file and the commit to see what was built. The installers are not code-signed yet, so Windows SmartScreen asks first.

## macOS

The disk image is built on the owner's Mac and is neither signed nor notarized yet, so there is no check beyond the
source. When the Developer ID certificate exists, the same workflow will build and attest it.

## iPhone

A byte-for-byte check is **impossible** for the App Store app. Apple encrypts every app per account (FairPlay) and re-signs it,
and slices it for your device, so no one, including the owner, can produce the file you have from the source. Nothing we could
build would change that, and a claim that it does would be false.

What you can check instead:

- **The readable parts.** An App Store app's `Info.plist`, entitlements, resources, localized strings and the list of linked
  frameworks are not encrypted. They show the app's identifier and team, the capabilities and permissions it asks for (with the
  reason texts) and which frameworks are linked, so you can see whether an analytics or ad framework is in it. Compare them with
  `ios/` in the source.
- **What the app does.** Put the phone behind a proxy you control and look at where the app connects: it should be the server you
  gave it, plus kelo.chat for the things the privacy page lists.
- **Build it yourself.** `ios/` builds in Xcode with your own signing; the app you build is the app of that source, which you
  can run next to the store one and compare.
- **The server side.** What matters most for your data is the server you run: its images are signed and built in public.

An idea we have not built: a CI-built iPhone archive with an attestation, for people who want a binary to inspect. It would still
not be the App Store file.

## For the maintainer: releasing

1. `tools/build/android.sh version` (writes `android/kelo/version.properties`), update `CHANGELOG.md`, commit, tag.
2. `tools/build/android-reproducible.sh --sign`: Play bundle and sideload APK from exactly that commit, then signed with the upload
   key. Upload the signed bundle to Play, offer the signed APK on kelo.chat.
3. Push the tag to the public repository: `release-build.yml` builds and attests the same files; its `SHA256SUMS` must equal the
   `-unsigned` sums of step 2 (compare them; if they differ, do not publish).
4. After Google Play has published it, run `tools/verify/android-verify.sh` once on a phone that installed from Play.

