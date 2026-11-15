#!/bin/bash
# Check that the Kelo app on your Android phone is what the public source says it is (docs/verify-builds.md).
#
#   tools/verify/android-verify.sh --tag v0.5.0               build that tag, compare with the app on the connected phone
#   tools/verify/android-verify.sh --source ~/kelo            use a checkout you already have (it must be the tagged one)
#   tools/verify/android-verify.sh --tag v0.5.0 --aab my.aab  use a bundle you built yourself instead of building one
#
# What it does:
#   1. gets the source of the tag (git clone of https://github.com/kelo-chat/kelo, or --source) and prints the commit
#   2. builds it in the pinned Docker environment (tools/build/android-reproducible.sh of THAT source)
#   3. Google Play version of the app: bundletool makes the APKs your phone should have from the bundle (--connected-device), adb pulls
#      the APKs installed on the phone, and tools/verify/apkdiff.py compares them one by one
#      File version of the app (kelo.chat/download/android): the one APK is compared with the one on the phone
#   Signatures are not compared (Google Play signs with its own key; the file version carries the owner's signature, whose
#   fingerprint is printed so you can look at it). Everything else inside the APKs must be identical.
#
# Options:
#   --tag TAG        the version tag to build (default: the tag that matches the app version on the phone, vX.Y.Z is not needed:
#                    without it the script asks the phone for its version and looks for the tag in the repository's tags)
#   --repo URL       where to get the source (default https://github.com/kelo-chat/kelo)
#   --source DIR     a checkout of the source instead of cloning
#   --device SERIAL  the phone, if more than one is connected (adb devices)
#   --flavor F       play, sideload or full: which app is on the phone (default: guessed from where it was installed from)
#   --aab FILE       use this bundle (play) or APK (sideload, full) instead of building; its signature does not matter
#   --package NAME   the app's package name (default chat.kelo.app)
#   --keep           keep the work folder (clone, build output, pulled APKs) and print where it is
#
# Needs: git, docker, adb (Android platform-tools), java 17+ (for bundletool), python3, curl. bundletool is downloaded once to
# ~/.cache/kelo-verify and checked against the checksum below. The first build takes long (a few hours emulated on an Apple
# Silicon Mac, about 15 minutes on a normal x86-64 PC); later ones reuse the downloads.
set -euo pipefail

BUNDLETOOL_VERSION=1.18.3
BUNDLETOOL_SHA256=a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29
BUNDLETOOL_URL="https://github.com/google/bundletool/releases/download/$BUNDLETOOL_VERSION/bundletool-all-$BUNDLETOOL_VERSION.jar"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APKDIFF="$HERE/apkdiff.py"
usage() { sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
say()  { printf '\033[1m==> %s\033[0m\n' "$*" >&2; }
note() { printf '    %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 2; }

TAG="" REPO_URL="https://github.com/kelo-chat/kelo" SOURCE="" DEVICE="" FLAVOR="" GIVEN="" PKG="chat.kelo.app" KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:?--tag needs a value}"; shift ;;
    --repo) REPO_URL="${2:?--repo needs a URL}"; shift ;;
    --source) SOURCE="${2:?--source needs a folder}"; shift ;;
    --device) DEVICE="${2:?--device needs a serial}"; shift ;;
    --flavor) FLAVOR="${2:?--flavor needs play, sideload or full}"; shift ;;
    --aab|--apk) GIVEN="${2:?$1 needs a file}"; shift ;;
    --package) PKG="${2:?--package needs a name}"; shift ;;
    --keep) KEEP=1 ;;
    -h|--help) usage 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done

for c in git python3 curl adb; do command -v "$c" >/dev/null || die "$c is not installed (or not on PATH)"; done
[ -n "$GIVEN" ] || { command -v docker >/dev/null && docker info >/dev/null 2>&1 || die "docker is not installed or not running (needed to build; or pass --aab / --apk)"; }

mkdir -p "$HOME/.cache/kelo-verify"
WORK="$(mktemp -d "$HOME/.cache/kelo-verify/work.XXXXXX")"
cleanup() { if [ "$KEEP" = 1 ]; then echo "work folder kept: $WORK" >&2; else rm -rf "$WORK"; fi; }
trap cleanup EXIT
ADB=(adb); [ -n "$DEVICE" ] && ADB=(adb -s "$DEVICE")

# ---- the phone
say "the phone"
"${ADB[@]}" get-state >/dev/null 2>&1 || die "no phone found: connect it with USB debugging on (adb devices), or pass --device SERIAL"
DUMP="$("${ADB[@]}" shell dumpsys package "$PKG" 2>/dev/null | tr -d '\r')"
[ -n "$DUMP" ] && printf '%s' "$DUMP" | grep -q "versionName=" || die "$PKG is not installed on the phone"
PHONE_VERSION="$(printf '%s' "$DUMP" | sed -n 's/.*versionName=\([^ ]*\).*/\1/p' | head -1)"
PHONE_CODE="$(printf '%s' "$DUMP" | sed -n 's/.*versionCode=\([0-9]*\).*/\1/p' | head -1)"
INSTALLER="$(printf '%s' "$DUMP" | sed -n 's/.*installerPackageName=\([^ ]*\).*/\1/p' | head -1)"
note "$PKG version $PHONE_VERSION (versionCode $PHONE_CODE), installed by: ${INSTALLER:-unknown}"
if [ -z "$FLAVOR" ]; then
  case "$INSTALLER" in com.android.vending) FLAVOR=play ;; *) FLAVOR=sideload ;; esac
  note "this looks like the $FLAVOR app (use --flavor to say otherwise)"
fi
case "$FLAVOR" in play|sideload|full) ;; *) die "--flavor must be play, sideload or full" ;; esac
"${ADB[@]}" shell pm path "$PKG" | tr -d '\r' | sed -n 's/^package://p' >"$WORK/paths.txt"
[ -s "$WORK/paths.txt" ] || die "adb shell pm path $PKG printed nothing"
mkdir -p "$WORK/phone"
while IFS= read -r p; do
  n="$(basename "$p")"
  "${ADB[@]}" pull "$p" "$WORK/phone/$n" >"$WORK/pull.log" 2>&1 || { cat "$WORK/pull.log" >&2; die "could not pull $p (a phone with a locked-down ROM may refuse: try another device)"; }
done <"$WORK/paths.txt"
note "pulled $(wc -l <"$WORK/paths.txt" | tr -d ' ') APK file(s) from the phone"

# ---- the source
say "the source"
if [ -z "$SOURCE" ]; then
  SOURCE="$WORK/source"
  if [ -z "$TAG" ]; then
    # the tag whose android/kelo/version.properties says the phone's build (versionCode): the repository's tags, newest first
    note "no --tag: looking for the tag with build $PHONE_CODE"
    git clone --quiet --filter=blob:none --no-checkout "$REPO_URL" "$SOURCE" || die "could not clone $REPO_URL"
    for t in $(git -C "$SOURCE" tag --sort=-creatordate | head -40); do
      if git -C "$SOURCE" show "$t:android/kelo/version.properties" 2>/dev/null | grep -q "^build=${PHONE_CODE}\$"; then TAG="$t"; break; fi
    done
    [ -n "$TAG" ] || die "no tag in $REPO_URL has build $PHONE_CODE in android/kelo/version.properties; pass --tag"
    rm -rf "$SOURCE"
  fi
  note "cloning $REPO_URL at $TAG"
  git clone --quiet --depth 1 --branch "$TAG" "$REPO_URL" "$SOURCE" || die "could not clone $TAG from $REPO_URL"
fi
[ -f "$SOURCE/android/kelo/version.properties" ] || die "$SOURCE does not look like the Kelo source (android/kelo/version.properties missing)"
SRC_BUILD="$(sed -n 's/^build=//p' "$SOURCE/android/kelo/version.properties" | head -1)"
note "source: $(git -C "$SOURCE" rev-parse HEAD 2>/dev/null || echo 'not a git checkout')  build $SRC_BUILD"
[ "$SRC_BUILD" = "$PHONE_CODE" ] || die "the source is build $SRC_BUILD but the app on the phone is build $PHONE_CODE ($PHONE_VERSION): check out the tag of the version you have"
if [ -z "$GIVEN" ] && [ -n "$(git -C "$SOURCE" status --porcelain 2>/dev/null)" ]; then
  die "the source folder has uncommitted changes, so it is not the tagged source"
fi

# ---- the build
if [ -n "$GIVEN" ]; then
  say "using the file you gave"
  BUILT="$GIVEN"; [ -f "$BUILT" ] || die "$BUILT not found"
else
  say "building $FLAVOR in the pinned environment (long the first time)"
  case "$FLAVOR" in play) TARGET=play-aab ;; sideload) TARGET=sideload-apk ;; full) TARGET=full-apk ;; esac
  "$SOURCE/tools/build/android-reproducible.sh" --targets "$TARGET" --out "$WORK/built"
  BUILT="$(ls "$WORK"/built/kelo-*-"$FLAVOR"-release-unsigned.* | head -1)"
fi
note "built: $BUILT"
( cd "$(dirname "$BUILT")" && shasum -a 256 "$(basename "$BUILT")" ) >&2 || true

# ---- the comparison
if [ "$FLAVOR" = play ]; then
  say "bundletool: the APKs this phone should get from the bundle"
  command -v java >/dev/null || die "java is not installed (bundletool needs Java 17 or newer)"
  CACHE="${KELO_VERIFY_CACHE:-$HOME/.cache/kelo-verify}"; mkdir -p "$CACHE"
  JAR="$CACHE/bundletool-all-$BUNDLETOOL_VERSION.jar"
  if ! { [ -f "$JAR" ] && [ "$(shasum -a 256 "$JAR" | cut -d' ' -f1)" = "$BUNDLETOOL_SHA256" ]; }; then
    note "downloading bundletool $BUNDLETOOL_VERSION"
    curl -fsSL -o "$JAR.part" "$BUNDLETOOL_URL" || die "could not download bundletool"
    [ "$(shasum -a 256 "$JAR.part" | cut -d' ' -f1)" = "$BUNDLETOOL_SHA256" ] || { rm -f "$JAR.part"; die "bundletool's checksum is not the expected one"; }
    mv "$JAR.part" "$JAR"
  fi
  ADBPATH="$(command -v adb)"
  BT=(java -jar "$JAR" build-apks --bundle="$BUILT" --output="$WORK/mine.apks" --connected-device --adb="$ADBPATH" --overwrite)
  [ -n "$DEVICE" ] && BT+=(--device-id="$DEVICE")
  "${BT[@]}" >"$WORK/bundletool.log" 2>&1 || { tail -20 "$WORK/bundletool.log" >&2; die "bundletool could not make the APKs"; }
  say "comparing (signatures are left out)"
  python3 -I "$APKDIFF" --splits "$WORK/mine.apks" "$WORK/phone"
else
  say "comparing (signatures are left out)"
  PHONE_BASE="$WORK/phone/base.apk"; [ -f "$PHONE_BASE" ] || PHONE_BASE="$(ls "$WORK"/phone/*.apk | head -1)"
  python3 -I "$APKDIFF" "$BUILT" "$PHONE_BASE"
fi
