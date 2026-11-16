#!/bin/bash
# Check that the signed Kelo disk image for macOS is built from the public source (docs/verify-builds.md, "macOS").
#
#   tools/verify/mac-verify.sh Kelo-1.0.9.dmg       (run from a checkout of the public repository, on a Mac with the Xcode command line tools)
#
# What it does:
#   1. mounts the disk image read-only and copies Kelo.app out
#   2. who signed it: `spctl` must say "Notarized Developer ID", and the app's TeamIdentifier must be the one in
#      apps/desktop/packaging/macos/TEAM_ID (printed both ways)
#   3. the app's version -> the tag mac-<version> must exist; that tag is checked out into a temporary worktree
#   4. builds it there, unsigned, with the pinned Java runtime (apps/desktop/packaging/macos/JDK): ./gradlew createDistributable
#   5. tools/verify/macdiff.py --normalize-signatures compares the two apps file by file. The signatures (Apple's, and the unsigned
#      build's) are replaced by the same ad-hoc one on both sides, so only the code and data are compared.
# Exit 0: every file is the same. 1: something differs, or the signer is not the expected one. 2: could not check.
# The build takes about 10 minutes; the first run also downloads the pinned JDK (about 180 MB) to ~/.cache/kelo/jdk.
source "$(dirname "$0")/../build/lib.sh"

DMG="${1:-}"
case "$DMG" in -h|--help|"") sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; [ -n "$DMG" ] && exit 0 || exit 2 ;; esac
[ "$(uname)" = Darwin ] || die "this check needs macOS (hdiutil, codesign, spctl)"
[ -f "$DMG" ] || die "no such file: $DMG"
DMG="$(cd "$(dirname "$DMG")" && pwd)/$(basename "$DMG")"
for c in hdiutil codesign spctl git python3 curl /usr/libexec/PlistBuddy; do command -v "$c" >/dev/null || die "$c is not installed"; done
TEAM_FILE="$REPO/apps/desktop/packaging/macos/TEAM_ID"
[ -f "$TEAM_FILE" ] || die "$TEAM_FILE is missing: run this from a checkout of the Kelo repository"
TEAM_ID="$(tr -d '[:space:]' <"$TEAM_FILE")"
need_disk
take_lock GRADLE_LOCK   # only matters where KELO_LOCK_DIR is set (the maintainer machine)

make_temp macverify WORK
MNT="$WORK/mnt"; mkdir -p "$MNT" "$WORK/downloaded"
detach() { hdiutil detach "$MNT" -force >/dev/null 2>&1 || true; }
SRC=""
remove_src() { [ -n "$SRC" ] && git -C "$REPO" worktree remove --force "$SRC" >/dev/null 2>&1; git -C "$REPO" worktree prune; return 0; }
on_exit detach
on_exit remove_src

log "1. open the disk image (read-only)"
hdiutil attach -readonly -nobrowse -noverify -mountpoint "$MNT" "$DMG" >/dev/null || die "could not mount $DMG"
[ -d "$MNT/Kelo.app" ] || die "no Kelo.app in the disk image"
ditto "$MNT/Kelo.app" "$WORK/downloaded/Kelo.app" || die "could not copy Kelo.app"
detach
APP="$WORK/downloaded/Kelo.app"

log "2. who signed it"
SPCTL="$(spctl -a -vv -t install "$DMG" 2>&1 || true)"
printf '%s\n' "$SPCTL" | sed 's/^/    /' >&2
CS="$(codesign -dv --verbose=4 "$APP" 2>&1 || true)"
printf '%s\n' "$CS" | grep -E '^(Identifier|TeamIdentifier|Authority|Timestamp)=' | sed 's/^/    /' >&2
APP_TEAM="$(printf '%s\n' "$CS" | sed -n 's/^TeamIdentifier=//p' | head -1)"
FAIL=""
printf '%s\n' "$SPCTL" | grep -q "source=Notarized Developer ID" || FAIL="the disk image is not notarized (spctl does not say 'Notarized Developer ID')"
[ "$APP_TEAM" = "$TEAM_ID" ] || FAIL="${FAIL:+$FAIL; }the app is signed by team '${APP_TEAM:-none}', not $TEAM_ID (apps/desktop/packaging/macos/TEAM_ID)"
codesign --verify --deep --strict "$APP" 2>/dev/null || FAIL="${FAIL:+$FAIL; }the signature of Kelo.app does not verify"
note "expected team (from this checkout): $TEAM_ID"
if [ -n "$FAIL" ]; then
  printf '\nKelo disk image %s: NOT the same. %s.\n' "$(basename "$DMG")" "$FAIL"; exit 1
fi

log "3. version and tag"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
[ -n "$VERSION" ] || die "no CFBundleShortVersionString in Info.plist"
TAG="mac-$VERSION"
COMMIT="$(git -C "$REPO" rev-parse --verify "refs/tags/$TAG^{commit}" 2>/dev/null)" || die "the tag $TAG does not exist in this checkout (git fetch --tags)"
note "version $VERSION, tag $TAG = commit $COMMIT"
SRC="$WORK/source"
git -C "$REPO" worktree add --detach "$SRC" "$COMMIT" >/dev/null 2>&1 || die "could not check out $TAG"

log "4. build $TAG (unsigned) with the pinned Java runtime"
JDK_FILE="$SRC/apps/desktop/packaging/macos/JDK"; [ -f "$JDK_FILE" ] || JDK_FILE="$REPO/apps/desktop/packaging/macos/JDK"
kelo_packaging_jdk "$JDK_FILE"
kelo_zip_clock "$REPO" "$SRC"   # the fixed zip entry time (tools/build/zipclock/README.md)
note "JAVA_HOME=$JAVA_HOME"
( cd "$SRC/apps/desktop" && with_timeout "${KELO_GRADLE_TIMEOUT:-2700}" ./gradlew --no-daemon --console=plain -Pkotlin.compiler.execution.strategy=in-process \
    ${GRADLE_REPRO_ARGS[@]+"${GRADLE_REPRO_ARGS[@]}"} createDistributable "-PkeloVersion=$VERSION" ) >"$LOGS/mac-verify-build.log" 2>&1 \
  || { tail -n 30 "$LOGS/mac-verify-build.log" >&2; die "the build failed (log: $LOGS/mac-verify-build.log)"; }
BUILT="$SRC/apps/desktop/build/compose/binaries/main/app/Kelo.app"
[ -d "$BUILT" ] || die "the build made no Kelo.app"

log "5. compare, file by file (signatures normalised)"
rc=0
python3 -I "$REPO/tools/verify/macdiff.py" --normalize-signatures "$APP" "$BUILT" || rc=$?
echo
if [ "$rc" = 0 ]; then
  echo "Kelo $VERSION for macOS: every file is the same as the source at $TAG ($COMMIT), apart from Apple's signature."
  echo "Signed by team $APP_TEAM, notarized."
  exit 0
fi
echo "Kelo $VERSION for macOS: NOT the same as the source at $TAG ($COMMIT). The differences are listed above."
exit 1
