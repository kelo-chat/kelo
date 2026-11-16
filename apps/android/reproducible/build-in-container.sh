#!/bin/bash
# Runs inside the image (see Dockerfile): one Gradle build of the Android app in the current folder (apps/android/kelo of the mounted
# repository-shaped source: ../../shared and ../../../version.json are next to it).
# The same flags everywhere, so nothing about how it is started can change the result. Extra arguments are the Gradle tasks.
set -euo pipefail

[ -f gradlew ] && [ -f settings.gradle.kts ] || { echo "kelo-build: run it in the apps/android/kelo folder (gradlew not found in $PWD)" >&2; exit 2; }
[ ! -e local.properties ] || { echo "kelo-build: local.properties would change the build; it must not exist" >&2; exit 2; }
[ -f ../../shared/settings.gradle.kts ] && [ -f ../../../version.json ] || { echo "kelo-build: ../../shared and ../../../version.json must exist (the source is a repository-shaped folder: apps/android/kelo, apps/shared, version.json)" >&2; exit 2; }
[ ! -e ../../shared/local.properties ] || { echo "kelo-build: apps/shared/local.properties would change the build; it must not exist" >&2; exit 2; }
[ ! -e ../keystore/keystore.properties ] || { echo "kelo-build: a keystore next to the source would sign the build; it must not exist" >&2; exit 2; }

echo "== java:   $(java -version 2>&1 | head -1)"
echo "== sdk:    platforms=$(ls "$ANDROID_HOME/platforms" | tr '\n' ' ') build-tools=$(ls "$ANDROID_HOME/build-tools" | tr '\n' ' ')"
echo "== folder: $PWD   user: $(id -u):$(id -g)"

# Lint's "vital" check runs before every release build in a normal build. It writes no byte of the APK or bundle and is slow here
# (the image runs emulated on some machines), so it is left out; the owner's quick builds (tools/build/android.sh) still run it.
EXCLUDE=()
for t in "$@"; do
  case "$t" in
    bundle*Release|assemble*Release) f="${t#bundle}"; f="${f#assemble}"; EXCLUDE+=(-x "lintVital$f") ;;
  esac
done

# --no-daemon + in-process Kotlin: no background JVM stays behind. strict: every downloaded file must match
# gradle/verification-metadata.xml (the source copy is fresh, so there is no project cache; --no-build-cache keeps it that way).
# KELO_WRITE_VERIFICATION=1 (tools/build/android-reproducible.sh --update-verification-metadata) records the checksums instead.
VERIFY=(--dependency-verification=strict)
[ "${KELO_WRITE_VERIFICATION:-}" = 1 ] && VERIFY=(--write-verification-metadata sha256)
exec ./gradlew --no-daemon --console=plain --no-build-cache "${VERIFY[@]}" \
  -Pkotlin.compiler.execution.strategy=in-process ${EXCLUDE[@]+"${EXCLUDE[@]}"} "$@"
