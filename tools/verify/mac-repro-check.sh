#!/bin/bash
# Maintainer tool: is the macOS app (unsigned) built the same twice? (docs/verify-builds.md)
#
#   tools/verify/mac-repro-check.sh [--commit REF] [--version X.Y.Z] [--gap SECONDS] [--keep]
#
# Makes two fresh git worktrees of one commit (default HEAD) in two DIFFERENT temp paths, builds
#   cd desktop && ./gradlew --no-daemon createDistributable -PkeloVersion=<version>
# in each (the second at least --gap seconds later, default 65, with another TZ and LANG), then runs tools/verify/macdiff.py
# (without --normalize-signatures) on the two apps/desktop/build/compose/binaries/main/app/Kelo.app and prints the differences.
# Exit 0: no difference. 1: differences. Takes about 2 x 8 minutes. The worktrees are removed at the end (--keep leaves them).
source "$(dirname "$0")/../build/lib.sh"

COMMIT=HEAD VERSION="" GAP=65 KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --commit) COMMIT="${2:?}"; shift ;;
    --version) VERSION="${2:?}"; shift ;;
    --gap) GAP="${2:?}"; shift ;;
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ "$(uname)" = Darwin ] || die "the macOS app can only be built on a Mac"
SHA="$(git -C "$REPO" rev-parse --verify "$COMMIT^{commit}")" || die "no such commit: $COMMIT"
if [ -z "$VERSION" ]; then
  VERSION="$(git -C "$REPO" show "$SHA:apps/desktop/version.properties" | sed -n 's/^packageVersion=\([0-9][0-9.]*\)[[:space:]]*$/\1/p' | head -1)"
  [ -n "$VERSION" ] || die "no packageVersion in apps/desktop/version.properties at $COMMIT; pass --version X.Y.Z"
fi
need_disk
take_lock GRADLE_LOCK

if [ "$KEEP" = 1 ]; then ROOT="$(mktemp -d "$_TMP_BASE/kelo-macrepro.XXXXXX")"; else make_temp macrepro ROOT; fi
WT=()
remove_worktrees() { local w; for w in ${WT[@]+"${WT[@]}"}; do git -C "$REPO" worktree remove --force "$w" >/dev/null 2>&1 || true; done; git -C "$REPO" worktree prune; }
[ "$KEEP" = 1 ] && note "worktrees are kept under $ROOT" || on_exit remove_worktrees

build_one() {   # build_one NAME PATH-UNDER-ROOT TZ LANG
  local name="$1" dir="$ROOT/$2" tz="$3" lang="$4"
  mkdir -p "$(dirname "$dir")"
  git -C "$REPO" worktree add --detach "$dir" "$SHA" >/dev/null 2>&1 || die "git worktree add failed for $dir"
  WT+=("$dir")
  kelo_packaging_jdk "$REPO/apps/desktop/packaging/macos/JDK"
  kelo_zip_clock "$REPO" "$dir"
  log "build $name in $dir (TZ=$tz LANG=$lang, $(date '+%H:%M:%S'))"
  ( cd "$dir/desktop" && TZ="$tz" LANG="$lang" LC_ALL="$lang" with_timeout "${KELO_GRADLE_TIMEOUT:-2700}" \
      ./gradlew --no-daemon --console=plain -Pkotlin.compiler.execution.strategy=in-process ${GRADLE_REPRO_ARGS[@]+"${GRADLE_REPRO_ARGS[@]}"} \
      createDistributable "-PkeloVersion=$VERSION" ) \
    >"$LOGS/mac-repro-$name.log" 2>&1 || { tail -n 30 "$LOGS/mac-repro-$name.log" >&2; die "build $name failed (log: $LOGS/mac-repro-$name.log)"; }
  [ -d "$dir/desktop/build/compose/binaries/main/app/Kelo.app" ] || die "build $name made no Kelo.app"
}

log "commit $SHA, version $VERSION"
build_one a "one/kelo" UTC en_US.UTF-8
log "wait ${GAP}s so the second build happens at another minute"
sleep "$GAP"
build_one b "second-folder/with/a/longer/path/kelo-b" Asia/Seoul ko_KR.UTF-8

APP=apps/desktop/build/compose/binaries/main/app/Kelo.app
log "compare"
rc=0
python3 -I "$REPO/tools/verify/macdiff.py" "${WT[0]}/$APP" "${WT[1]}/$APP" --json "$LOGS/mac-repro-diff.json" || rc=$?
if [ "$rc" = 0 ]; then log "no differences: two builds of $SHA are the same"; else warn "the two builds differ (json: $LOGS/mac-repro-diff.json)"; fi
exit $rc
