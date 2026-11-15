#!/bin/bash
# Kelo for Android, built the same way anywhere: the Play bundle (.aab) and the APKs in a pinned Docker environment, so that
# you can build the tagged public source yourself and compare it with what is in the store (docs/verify-builds.md).
#
#   tools/build/android-reproducible.sh                       Play bundle + sideload APK of the committed source (HEAD)
#   tools/build/android-reproducible.sh --targets play-aab,sideload-apk,full-apk
#   tools/build/android-reproducible.sh --sign                owner only: sign the results with android/keystore (after the build)
#
# Options:
#   --targets LIST   play-aab (Play bundle), sideload-apk (the APK of kelo.chat/download/android), full-apk (SMS / call log build);
#                    comma separated, default play-aab,sideload-apk
#   --out DIR        where the files go (default dist/android-reproducible)
#   --worktree       build the working tree (committed + new files, not ignored ones) instead of the commit HEAD: for trying a change
#   --from DIR       build a copy of android/kelo that is in DIR (made earlier; for testing that the same folder builds the same).
#                    The shared/ module is taken from DIR/../../shared (a repository-shaped folder: DIR is <root>/android/kelo)
#   --update-verification-metadata   maintainers: after changing a dependency, record the checksums of every file the build downloads
#                    in android/kelo/gradle/verification-metadata.xml (commit it; the normal build refuses any file not listed there)
#   --sign           sign what was built with android/keystore/keystore.properties: .aab with jarsigner, .apk with apksigner. The
#                    signature is the only difference to the files anyone else builds (it is left out of every comparison)
#   --cache DIR      Gradle download cache (default ~/.cache/kelo-repro/gradle); --no-cache uses a throw-away one
#   --dry-run        print the steps and stop
# Test knobs: --mount PATH (where android/kelo is inside the container, default /build/android/kelo; the repository-shaped source
# copy is mounted two folders up, so ../../shared and ../../version.json resolve), KELO_REPRO_WORK (where the source copy is made,
# default ~/.cache/kelo-repro/work; it must be a place Docker can mount).
#
# Needs Docker (on an Apple Silicon Mac the linux/amd64 image runs emulated: allow a few hours for the first build) and git.
# Output: <out>/kelo-<versionName>-<flavor>-release-unsigned.{aab,apk} (or ...-signed...), SHA256SUMS and build-info.txt.
# Nothing in the files or in build-info.txt comes from the clock or the machine: the version is android/kelo/version.properties.
source "$(dirname "$0")/lib.sh"

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

TARGETS="play-aab,sideload-apk" OUT="$REPO/dist/android-reproducible" WORKTREE=0 SIGN=0 FROM="" WRITE_VERIFY=0
CACHE="${KELO_REPRO_CACHE:-$HOME/.cache/kelo-repro/gradle}" NOCACHE=0 MOUNT=/build/android/kelo
while [ $# -gt 0 ]; do
  case "$1" in
    --targets) TARGETS="${2:?--targets needs a list}"; shift ;;
    --out) OUT="${2:?--out needs a folder}"; shift ;;
    --worktree) WORKTREE=1 ;;
    --from) FROM="${2:?--from needs a folder}"; shift ;;
    --sign) SIGN=1 ;;
    --update-verification-metadata) WRITE_VERIFY=1 ;;
    --cache) CACHE="${2:?--cache needs a folder}"; shift ;;
    --no-cache) NOCACHE=1 ;;
    --mount) MOUNT="${2:?--mount needs a path}"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
case "$MOUNT" in /*/*/*) ;; *) die "--mount must be an absolute path of at least three folders (the repository root is two levels up)" ;; esac
ROOT_MOUNT="$(dirname "$(dirname "$MOUNT")")"   # the repository-shaped source copy; android/kelo is $MOUNT, shared/ is $ROOT_MOUNT/shared
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac

# --- what to build: target -> Gradle task, output file, result name
TASKS=() WANT=()
IFS=',' read -r -a _T <<<"$TARGETS"
for t in "${_T[@]}"; do
  case "$t" in
    play-aab)     TASKS+=(bundlePlayRelease);      WANT+=("play:aab") ;;
    sideload-apk) TASKS+=(assembleSideloadRelease); WANT+=("sideload:apk") ;;
    full-apk)     TASKS+=(assembleFullRelease);     WANT+=("full:apk") ;;
    *) die "unknown target '$t' (play-aab, sideload-apk, full-apk)" ;;
  esac
done
[ ${#TASKS[@]} -gt 0 ] || die "no targets"

ANDROID_SRC="$REPO/android/kelo"
DOCKER_DIR="$REPO/android/reproducible"
[ -f "$ANDROID_SRC/version.properties" ] || die "android/kelo/version.properties is missing (the version comes from there)"
BUILD_NO="$(sed -n 's/^build=\([0-9][0-9]*\)$/\1/p' "$ANDROID_SRC/version.properties" | head -1)"
[ -n "$BUILD_NO" ] || die "android/kelo/version.properties has no 'build=<number>' line"
RELEASE="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$REPO/version.json")" || die "version.json at the repository root is missing or unreadable"
VERSION="$RELEASE"   # versionName (docs/versioning.md); versionCode is $BUILD_NO

# the image is named after the files that define it, so a changed Dockerfile builds a new image and an old one is never reused
HASH="$( (cat "$DOCKER_DIR/Dockerfile" "$DOCKER_DIR/build-in-container.sh") | shasum -a 256 | cut -c1-12)"
IMAGE="kelo-android-repro:$HASH"

log "Kelo Android $VERSION, reproducible build (${TARGETS})"
note "source: $([ -n "$FROM" ] && echo "the folder $FROM" || { [ "$WORKTREE" = 1 ] && echo 'working tree (uncommitted changes included)' || echo "commit $(git -C "$REPO" rev-parse --short=12 HEAD 2>/dev/null || echo '?') (HEAD)"; })"
note "image:  $IMAGE (linux/amd64)   android/kelo in the container: $MOUNT   (source copy mounted at $ROOT_MOUNT)"
if [ "$WORKTREE" = 0 ] && [ -z "$FROM" ] && [ -n "$(git -C "$REPO" status --porcelain -- android/kelo android/reproducible shared version.json 2>/dev/null)" ]; then
  warn "android/kelo, shared, version.json or android/reproducible has uncommitted changes: they are NOT in this build (it builds HEAD). Use --worktree to include them."
fi
if [ "$DRY_RUN" = 1 ]; then
  note "[dry-run] docker build --platform linux/amd64 -t $IMAGE $DOCKER_DIR"
  note "[dry-run] copy the source (android/kelo, shared, version.json) into a clean repository-shaped folder, then: docker run --rm --platform linux/amd64 -u <you> -v <copy>:$ROOT_MOUNT -w $MOUNT $IMAGE <task>   (once per task: ${TASKS[*]})"
  note "[dry-run] collect the files into $OUT$([ "$SIGN" = 1 ] && echo ', sign them')"
  exit 0
fi
command -v docker >/dev/null || die "docker is not installed (or not on PATH)"
docker info >/dev/null 2>&1 || die "docker is not running"
command -v git >/dev/null || die "git is not installed"
need_disk

# --- 1. the image
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  note "image $IMAGE exists"
else
  step "docker build $IMAGE" "${KELO_DOCKER_TIMEOUT:-3600}" "$LOGS/android-repro-image.log" \
    docker build --platform linux/amd64 -t "$IMAGE" "$DOCKER_DIR"
fi

# --- 2. a clean, repository-shaped copy of the source: only files git knows (so local junk, build folders, local.properties and keys
# cannot get in): $SRC/android/kelo, $SRC/shared (the module both apps use) and $SRC/version.json (app/build.gradle.kts reads
# ../../version.json). The container mounts $SRC at $ROOT_MOUNT and builds in $MOUNT, so ../../shared and ../../version.json resolve.
WORK_BASE="${KELO_REPRO_WORK:-$HOME/.cache/kelo-repro/work}"
mkdir -p "$WORK_BASE"
SRC="$(mktemp -d "$WORK_BASE/src.XXXXXX")"; _CLEAN_PATHS+=("$SRC")
mkdir -p "$SRC/android/kelo" "$SRC/shared"
RSYNC_SKIP=(--exclude build --exclude .gradle --exclude .kotlin --exclude local.properties)
if [ -n "$FROM" ]; then
  [ -f "$FROM/gradlew" ] || die "--from $FROM has no gradlew"
  [ -f "$FROM/../../shared/settings.gradle.kts" ] || die "--from $FROM needs the shared/ module two folders up ($FROM/../../shared): give the android/kelo folder of a repository-shaped copy"
  rsync -a "${RSYNC_SKIP[@]}" "$FROM/" "$SRC/android/kelo/"
  rsync -a "${RSYNC_SKIP[@]}" "$FROM/../../shared/" "$SRC/shared/"
elif [ "$WORKTREE" = 1 ]; then
  (cd "$ANDROID_SRC" && git ls-files -z -co --exclude-standard -- . ':!screens') | (cd "$ANDROID_SRC" && rsync -a --from0 --files-from=- ./ "$SRC/android/kelo/")
  (cd "$REPO/shared" && git ls-files -z -co --exclude-standard -- .) | (cd "$REPO/shared" && rsync -a --from0 --files-from=- ./ "$SRC/shared/")
else
  git -C "$REPO" archive --format=tar "HEAD:android/kelo" | tar -x -C "$SRC/android/kelo"
  rm -rf "$SRC/android/kelo/screens"
  git -C "$REPO" archive --format=tar "HEAD:shared" | tar -x -C "$SRC/shared"
fi
[ -f "$SRC/android/kelo/gradlew" ] || die "the source copy has no gradlew (is android/kelo committed?)"
[ -f "$SRC/shared/settings.gradle.kts" ] || die "the source copy has no shared/settings.gradle.kts (is shared/ committed?)"
# the app's version comes from the repository's root version.json: it goes along, from the same source as the rest (HEAD, or the
# working tree / the repository for --worktree and --from)
if [ "$WORKTREE" = 0 ] && [ -z "$FROM" ]; then
  git -C "$REPO" show HEAD:version.json > "$SRC/version.json" || die "version.json is not committed at the repository root"
else
  cp "$REPO/version.json" "$SRC/version.json"
fi
[ "$(sed -n 's/^build=//p' "$SRC/android/kelo/version.properties")" = "$BUILD_NO" ] || die "version.properties in the source copy differs from $BUILD_NO (commit it, or use --worktree)"
[ -f "$SRC/android/kelo/app/google-services.json" ] || warn "android/kelo/app/google-services.json is not in the source: this build has NO push notifications (and will not match the owner's)"

# --- 3. the build
# The commit the source came from goes INTO the container build (the copy has no .git), as Gradle properties through the environment
# (ORG_GRADLE_PROJECT_<name> = -P<name>; the image stays the same): the version line in My links to that commit. The same commit
# gives the same bytes. A build of the working tree says "modified" (no link); a --from folder has no commit to name.
if [ -n "$FROM" ]; then SRC_COMMIT="" SRC_DIRTY=false
else
  SRC_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
  case "$SRC_COMMIT" in *[!0-9a-f]*|"") die "could not read the commit of HEAD" ;; esac
  SRC_DIRTY=$([ "$WORKTREE" = 1 ] && echo true || echo false)
fi
if [ "$NOCACHE" = 1 ]; then CACHE="$(mktemp -d "$WORK_BASE/gradle.XXXXXX")"; _CLEAN_PATHS+=("$CACHE"); fi
mkdir -p "$CACHE"
# One container (a fresh Gradle JVM) per target: the result of one target can then never depend on what another target did in the same
# process, and the memory stays within what a laptop's Docker has (KELO_REPRO_MEMORY, default 6g).
for task in "${TASKS[@]}"; do
  step "gradle $task in the container" "${KELO_GRADLE_TIMEOUT:-14400}" "$LOGS/android-repro-$task.log" \
    docker run --rm --memory="${KELO_REPRO_MEMORY:-6g}" --memory-swap="${KELO_REPRO_MEMORY:-6g}" --platform linux/amd64 -u "$(id -u):$(id -g)" \
      -v "$SRC:$ROOT_MOUNT" -w "$MOUNT" -v "$CACHE:/gradle-home" -e GRADLE_USER_HOME=/gradle-home \
      -e KELO_WRITE_VERIFICATION="$WRITE_VERIFY" -e ORG_GRADLE_PROJECT_keloCommit="$SRC_COMMIT" -e ORG_GRADLE_PROJECT_keloDirty="$SRC_DIRTY" \
      "$IMAGE" "$task"
done
if [ "$WRITE_VERIFY" = 1 ]; then
  cp "$SRC/android/kelo/gradle/verification-metadata.xml" "$ANDROID_SRC/gradle/verification-metadata.xml"
  log "wrote android/kelo/gradle/verification-metadata.xml ($(grep -c '<component ' "$ANDROID_SRC/gradle/verification-metadata.xml") components): review and commit it"
  exit 0
fi

# --- 4. collect the files under fixed names, in one folder, with their checksums
mkdir -p "$OUT"
rm -f "$OUT"/kelo-*-release-*.aab "$OUT"/kelo-*-release-*.apk "$OUT"/kelo-*-mapping.txt "$OUT/SHA256SUMS" "$OUT/build-info.txt"
RESULTS=()
for w in "${WANT[@]}"; do
  flavor="${w%%:*}" ext="${w##*:}"
  if [ "$ext" = aab ]; then built="$SRC/android/kelo/app/build/outputs/bundle/${flavor}Release/app-${flavor}-release.aab"
  else built="$SRC/android/kelo/app/build/outputs/apk/$flavor/release/app-$flavor-release-unsigned.apk"; fi
  [ -f "$built" ] || die "Gradle finished but $built is missing (logs: $LOGS/android-repro-*.log)"
  target="$OUT/kelo-$VERSION-$BUILD_NO-$flavor-release-unsigned.$ext"
  cp "$built" "$target"; RESULTS+=("$target")
  # the R8 name map of this build, so a crash report from a tester's phone can be read (Play Console takes it as the
  # deobfuscation file); kept per release in dist/android-mappings, which no later build clears
  map="$SRC/android/kelo/app/build/outputs/mapping/${flavor}Release/mapping.txt"
  [ -f "$map" ] || die "Gradle finished but the R8 mapping $map is missing"
  mkdir -p "$REPO/dist/android-mappings"
  cp "$map" "$OUT/kelo-$VERSION-$BUILD_NO-$flavor-mapping.txt"
  cp "$map" "$REPO/dist/android-mappings/kelo-$VERSION-$BUILD_NO-$flavor-mapping.txt"
done
# the build says which version it made; it must be the one in version.properties
for f in "${RESULTS[@]}"; do
  case "$f" in *.apk)
    got="$(python3 -I - "$f" "$REPO/tools/verify" <<'PY'
import sys, zipfile
sys.path.insert(0, sys.argv[2])
import apkdiff
for _d, tag, attrs in apkdiff.parse_axml(zipfile.ZipFile(sys.argv[1]).read("AndroidManifest.xml")):
    if tag == "manifest":
        print(dict(attrs).get("versionName", ""))
        break
PY
)"
    [ "$got" = "$VERSION" ] || die "$f says version '$got', expected $VERSION"
  ;; esac
done

# --- 5. signing (owner only): after the build, so the signature cannot influence what was built
if [ "$SIGN" = 1 ]; then
  KEYSTORE_DIR="$REPO/android/keystore"; PROPS="$KEYSTORE_DIR/keystore.properties"
  [ -f "$PROPS" ] || die "--sign needs android/keystore/keystore.properties (the upload key; not in git)"
  prop() { sed -n "s/^$1=//p" "$PROPS" | head -1; }
  KS="$KEYSTORE_DIR/$(prop storeFile)"; [ -f "$KS" ] || die "keystore.properties names a storeFile that is not in android/keystore"
  ALIAS="$(prop keyAlias)"
  export KELO_STORE_PASS KELO_KEY_PASS            # passwords go to the signing tools through the environment, never to the screen
  KELO_STORE_PASS="$(prop storePassword)"; KELO_KEY_PASS="$(prop keyPassword)"
  SDK="$(sed -n 's/^sdk.dir=//p' "$ANDROID_SRC/local.properties" 2>/dev/null | head -1)"; SDK="${SDK:-${ANDROID_HOME:-$HOME/Library/Android/sdk}}"
  APKSIGNER="$(ls -d "$SDK"/build-tools/*/apksigner 2>/dev/null | sort -V | tail -1)"
  use_jdk17
  NEW=()
  for f in "${RESULTS[@]}"; do
    signed="${f/-unsigned./-signed.}"
    case "$f" in
      *.aab)
        cp "$f" "$signed"
        jarsigner -keystore "$KS" -storepass:env KELO_STORE_PASS -keypass:env KELO_KEY_PASS -sigalg SHA256withRSA -digestalg SHA-256 "$signed" "$ALIAS" >/dev/null 2>&1 \
          || die "jarsigner could not sign $f"
        jarsigner -verify "$signed" 2>/dev/null | grep -q "jar verified" || die "$signed is not properly signed" ;;
      *.apk)
        [ -n "$APKSIGNER" ] || die "apksigner not found in $SDK/build-tools"
        "$APKSIGNER" sign --ks "$KS" --ks-key-alias "$ALIAS" --ks-pass env:KELO_STORE_PASS --key-pass env:KELO_KEY_PASS --out "$signed" "$f" >/dev/null 2>&1 \
          || die "apksigner could not sign $f"
        "$APKSIGNER" verify "$signed" >/dev/null 2>&1 || die "$signed is not properly signed" ;;
    esac
    note "signed: $(basename "$signed")"
    NEW+=("$signed")
  done
  unset KELO_STORE_PASS KELO_KEY_PASS
  RESULTS=("${RESULTS[@]}" "${NEW[@]}")
fi

( cd "$OUT" && shopt -s nullglob && shasum -a 256 kelo-*-release-*.aab kelo-*-release-*.apk 2>/dev/null | sort -k2 >SHA256SUMS )
{
  echo "version: $VERSION (versionCode $BUILD_NO)"
  echo "source commit: $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)$([ "$WORKTREE" = 1 ] && echo ' + uncommitted changes')"
  echo "image: $IMAGE (Dockerfile sha256 prefix $HASH)"
  echo "targets: $TARGETS"
  echo "the files named -unsigned are what anyone building this commit gets, byte for byte; -signed adds only the owner's signature"
} >"$OUT/build-info.txt"

log "done: Kelo Android $VERSION"
for f in "${RESULTS[@]}"; do note "$(basename "$f")  $(human_size "$f")  sha256 $(shasum -a 256 "$f" | cut -c1-16)..."; done
note "folder: $OUT (SHA256SUMS, build-info.txt)"
