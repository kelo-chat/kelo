#!/bin/bash
# Kelo for Android: one command per build. See tools/build/README.md.
#
#   tools/build/android.sh debug sideload                 a debug APK of the file build (quickest check)
#   tools/build/android.sh release sideload               the signed APK handed out as a file (kelo.chat/download/android)
#   tools/build/android.sh release full                   the signed APK with SMS / call log / notification reading (adb installs)
#   tools/build/android.sh release play                   a signed APK of the Play build (for testing it on a phone)
#   tools/build/android.sh bundle [play|full]             the signed App Bundle (.aab) for Google Play (default: play)
#   tools/build/android.sh version [--build N]            only pick the number of the next release and write it to apps/android/kelo/version.properties
#
# Quick builds on this Mac. The builds that go to the stores come from tools/build/android-reproducible.sh instead (a pinned
# environment, so anyone can build the same files from the public source and compare; see docs/verify-builds.md).
#
# Options:
#   --test            run that build's unit tests first (same Gradle run)
#   --build N         use versionCode N instead of a fresh one (rebuild a given release)
#   --out DIR         also copy the result to DIR as kelo-<version>-N-<flavor>-<type>.apk|.aab
#   --publish         after a release sideload build: offer it at <main copy>/download/android (assistant publish-app)
#   --unsigned        allow a release build without apps/android/keystore/keystore.properties (it is left unsigned)
#   --write-version   this release build IS the release: also write N to apps/android/kelo/version.properties (commit it with it)
#   --dry-run         print what would run (version, Gradle tasks, publish command) and stop
#
# Version: versionName = "version" of the root version.json (e.g. 0.9.0, docs/versioning.md); versionCode N = minutes since late
# 2026, from the formula in apps/android/kelo/app/build.gradle.kts. This script computes N once and passes -PkeloBuild=N, so the log
# and the APK agree. Only an explicit step writes N
# into apps/android/kelo/version.properties (the one tracked place the version is read from when no -PkeloBuild is given): `version`, or a
# release build with --write-version. Test, debug and trial release builds never touch it. Commit the file with the release, and
# building the tagged source again gives the same version (docs/verify-builds.md). (Versioning is moving to one root VERSION file.)
source "$(dirname "$0")/lib.sh"

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

TYPE="" FLAVOR="" BUNDLE=0 TEST=0 BUILD_NO="" OUT="" PUBLISH=0 UNSIGNED=0 WRITE_VERSION=0
[ $# -gt 0 ] || usage 1
case "$1" in
  debug|release) TYPE="$1"; shift
    case "${1:-}" in play|sideload|full) FLAVOR="$1"; shift ;; *) die "after '$TYPE' name the build: play, sideload or full" ;; esac ;;
  version) TYPE=version FLAVOR=play; shift ;;
  bundle) TYPE=release BUNDLE=1 FLAVOR=play; shift
    case "${1:-}" in play|full) FLAVOR="$1"; shift ;; sideload) die "the sideload build is handed out as an APK, not a bundle" ;; esac ;;
  -h|--help|help) usage 0 ;;
  *) die "unknown command '$1' (debug|release <play|sideload|full>, bundle or version)" ;;
esac
while [ $# -gt 0 ]; do
  case "$1" in
    --test) TEST=1 ;;
    --build) BUILD_NO="${2:?--build needs a number}"; shift ;;
    --out) OUT="${2:?--out needs a folder}"; shift ;;
    --publish) PUBLISH=1 ;;
    --unsigned) UNSIGNED=1 ;;
    --write-version) WRITE_VERSION=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done

ANDROID="$REPO/apps/android/kelo"
GRADLE_FILE="$ANDROID/app/build.gradle.kts"
VERSION_FILE="$ANDROID/version.properties"
KEYSTORE_PROPS="$REPO/apps/android/keystore/keystore.properties"

cap() { printf '%s%s' "$(printf '%s' "${1:0:1}" | tr '[:lower:]' '[:upper:]')" "${1:1}"; }

# --- the version: read the formula's offset from build.gradle.kts, so there is one place that defines it
if [ -z "$BUILD_NO" ]; then
  OFFSET="$(sed -n 's/.*System.currentTimeMillis() \/ 60_000L) - \([0-9_]*\)L.*/\1/p' "$GRADLE_FILE" | head -1 | tr -d _)"
  [ -n "$OFFSET" ] || die "could not find the version formula in $GRADLE_FILE (minutes - <offset>L); pass --build N"
  BUILD_NO=$(( $(date +%s) / 60 - OFFSET ))
fi
case "$BUILD_NO" in ''|*[!0-9]*) die "--build must be a whole number" ;; esac
grep -q 'findProperty("keloBuild")' "$GRADLE_FILE" || die "$GRADLE_FILE does not read -PkeloBuild; the version would not match"
RELEASE="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$REPO/version.json")" || die "version.json at the repository root is missing or unreadable"
VERSION="$RELEASE+$BUILD_NO"   # what the log says; the APK's versionName is $RELEASE and its versionCode $BUILD_NO

# The number of a release is written to the tracked version.properties (once, with the comment kept), so the commit that is
# tagged for the release builds to the same number anywhere.
write_version_file() {
  [ -f "$VERSION_FILE" ] || die "$VERSION_FILE is missing"
  if grep -q "^build=$BUILD_NO\$" "$VERSION_FILE"; then note "version.properties already says build=$BUILD_NO"; return 0; fi
  local tmp; tmp="$(mktemp "$VERSION_FILE.XXXXXX")"
  sed "s/^build=.*/build=$BUILD_NO/" "$VERSION_FILE" >"$tmp" && mv "$tmp" "$VERSION_FILE"
  grep -q "^build=$BUILD_NO\$" "$VERSION_FILE" || die "could not write build=$BUILD_NO to $VERSION_FILE"
  note "wrote build=$BUILD_NO to apps/android/kelo/version.properties - commit it with the release"
}
if [ "$TYPE" = version ]; then
  log "Kelo Android next release: $VERSION"
  if [ "$DRY_RUN" = 1 ]; then note "[dry-run] would write build=$BUILD_NO to apps/android/kelo/version.properties"; else write_version_file; fi
  note "CHANGELOG heading for this release: ## $VERSION — $(date +%Y-%m-%d)"
  exit 0
fi

# --- what to build
F="$(cap "$FLAVOR")" T="$(cap "$TYPE")"
if [ "$BUNDLE" = 1 ]; then
  TASKS=("bundle${F}Release")
  RESULT="$ANDROID/app/build/outputs/bundle/${FLAVOR}Release/app-${FLAVOR}-release.aab"
  EXT=aab
else
  TASKS=("assemble${F}${T}")
  RESULT="$ANDROID/app/build/outputs/apk/$FLAVOR/$TYPE/app-$FLAVOR-$TYPE.apk"
  EXT=apk
fi
[ "$TEST" = 1 ] && TASKS=("test${F}${T}UnitTest" ${TASKS[@]+"${TASKS[@]}"})

[ "$WRITE_VERSION" = 1 ] && [ "$TYPE" != release ] && die "--write-version is for a release build (or use: android.sh version)"
if [ "$PUBLISH" = 1 ] && ! { [ "$TYPE" = release ] && [ "$FLAVOR" = sideload ] && [ "$BUNDLE" = 0 ]; }; then
  die "--publish is for 'release sideload' only (the download page offers the file build; Play builds go through Play)"
fi

log "Kelo Android $VERSION ($TYPE $FLAVOR$([ "$BUNDLE" = 1 ] && echo ', bundle'))"

# --- signing: apps/android/keystore/keystore.properties (read by Gradle; never printed here)
if [ "$TYPE" = release ]; then
  if [ -f "$KEYSTORE_PROPS" ]; then
    STORE_FILE="$(sed -n 's/^storeFile=//p' "$KEYSTORE_PROPS" | head -1)"
    [ -n "$STORE_FILE" ] && [ -f "$REPO/apps/android/keystore/$STORE_FILE" ] || die "keystore.properties names a storeFile that is not in apps/android/keystore"
    note "signing: apps/android/keystore ($STORE_FILE)"
  elif [ "$UNSIGNED" = 1 ]; then
    warn "no apps/android/keystore/keystore.properties: the release build is left UNSIGNED (cannot be installed or uploaded)"
  else
    die "no apps/android/keystore/keystore.properties, so a release build would be unsigned. Restore the keystore, or pass --unsigned."
  fi
fi

source_commit apps/android/kelo apps/shared version.json   # only "modified" is used: the version line in My links by release tag (docs/versioning.md)
note "source: ${SOURCE_COMMIT:-unknown}$([ "$SOURCE_DIRTY" = true ] && echo ' (modified: files differ from this commit)')"
GRADLE=(./gradlew --no-daemon --console=plain -PkeloBuild="$BUILD_NO" -PkeloDirty="$SOURCE_DIRTY" -Pkotlin.compiler.execution.strategy=in-process
        ${TASKS[@]+"${TASKS[@]}"})
if [ "$DRY_RUN" = 1 ]; then
  note "[dry-run] (cd apps/android/kelo && JAVA_HOME=/opt/homebrew/opt/openjdk@17 $(printf '%q ' "${GRADLE[@]}"))"
else
  need_disk
  use_jdk17
  if [ "$WRITE_VERSION" = 1 ]; then write_version_file; fi
  cd "$ANDROID"
  step "gradle ${TASKS[*]}" "${KELO_GRADLE_TIMEOUT:-3600}" "$LOGS/android-gradle.log" "${GRADLE[@]}"
  cd "$REPO"

  # --- check the result
  [ -f "$RESULT" ] || die "Gradle finished but $RESULT is missing (log: $LOGS/android-gradle.log)"
  if [ "$EXT" = apk ]; then
    META="$(dirname "$RESULT")/output-metadata.json"
    got="$(python3 -I -c 'import json,sys; e=json.load(open(sys.argv[1]))["elements"][0]; print(e["versionCode"], e["versionName"])' "$META")"
    [ "$got" = "$BUILD_NO $RELEASE" ] || die "the APK says version '$got', expected '$BUILD_NO $RELEASE'"
    if [ "$TYPE" = release ] && [ "$UNSIGNED" = 0 ]; then
      SDK="$(sed -n 's/^sdk.dir=//p' "$ANDROID/local.properties" 2>/dev/null | head -1)"
      SDK="${SDK:-${ANDROID_HOME:-$HOME/Library/Android/sdk}}"
      APKSIGNER="$(ls -d "$SDK"/build-tools/*/apksigner 2>/dev/null | sort -V | tail -1)"
      if [ -n "$APKSIGNER" ]; then
        "$APKSIGNER" verify "$RESULT" >/dev/null 2>&1 || die "apksigner says $RESULT is not properly signed"
        note "signature verified (apksigner)"
      fi
    fi
  elif [ "$UNSIGNED" = 0 ]; then
    jarsigner -verify "$RESULT" 2>/dev/null | grep -q "jar verified" || die "$RESULT is not signed"
    note "signature verified (jarsigner)"
  fi

  # the R8 name map of a release build, kept per build so a crash report can be read (Play Console: deobfuscation file)
  MAP="$ANDROID/app/build/outputs/mapping/${FLAVOR}Release/mapping.txt"
  if [ "$TYPE" = release ] && [ -f "$MAP" ]; then
    mkdir -p "$REPO/dist/android-mappings"
    cp "$MAP" "$REPO/dist/android-mappings/kelo-$RELEASE-$BUILD_NO-$FLAVOR-mapping.txt"
    note "R8 mapping kept: dist/android-mappings/kelo-$RELEASE-$BUILD_NO-$FLAVOR-mapping.txt"
  fi
  if [ -n "$OUT" ]; then
    mkdir -p "$OUT"
    cp "$RESULT" "$OUT/kelo-$RELEASE-$BUILD_NO-$FLAVOR-$TYPE.$EXT"
    RESULT_COPY="$OUT/kelo-$RELEASE-$BUILD_NO-$FLAVOR-$TYPE.$EXT"
  fi
fi

# --- publish to the main copy's download page (operator's server only)
PUBLISH_CMD=(uv run assistant publish-app android "$RESULT" --version "$VERSION")
if [ "$PUBLISH" = 1 ]; then
  [ -f "$REPO/compose.yml" ] && [ -f "$REPO/.env" ] || die "publish-app must run in the main copy (compose.yml and .env in $REPO)"
  log "publish to the download page"
  if [ "$DRY_RUN" = 1 ]; then
    note "[dry-run] (cd $REPO && $(printf '%q ' "${PUBLISH_CMD[@]}"))"
  else
    (cd "$REPO" && with_timeout 300 "${PUBLISH_CMD[@]}")
  fi
fi

log "done: Kelo Android $VERSION"
if [ "$DRY_RUN" = 1 ]; then
  note "would write: $RESULT"
else
  note "output: $RESULT ($(human_size "$RESULT"))"
  [ -n "${RESULT_COPY:-}" ] && note "copy:   $RESULT_COPY"
fi
note "CHANGELOG heading for this build: ## $VERSION — $(date +%Y-%m-%d)"
if [ "$BUNDLE" = 1 ]; then
  note "upload to Play (internal track, as a draft):"
  note "  uv run --with google-auth --with requests python3 apps/android/play/upload.py --aab $RESULT --track internal"
  note "  (needs KELO_PLAY_KEY = the service account JSON; see apps/android/play/PLAY.md)"
fi
if [ "$PUBLISH" = 0 ] && [ "$TYPE" = release ] && [ "$FLAVOR" = sideload ] && [ "$BUNDLE" = 0 ]; then
  note "offer it for download (main copy): (cd $REPO && $(printf '%q ' "${PUBLISH_CMD[@]}"))"
fi
