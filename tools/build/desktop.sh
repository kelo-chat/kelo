#!/bin/bash
# Kelo for the desktop (Compose Multiplatform, apps/desktop/). See tools/build/README.md.
#
#   tools/build/desktop.sh test                 the desktop unit tests (Gradle, JDK 17)
#   tools/build/desktop.sh mac                  the macOS disk image (jpackage via ./gradlew packageDmg) -> dist/, built with the JDK
#                                               pinned in apps/desktop/packaging/macos/JDK (downloaded once to ~/.cache/kelo/jdk)
#                                               signed + notarized only when a "Developer ID Application" certificate is in the
#                                               keychain; else it is left unsigned and says so
#   tools/build/desktop.sh windows              runs .github/workflows/desktop-windows.yml on GitHub (MSI, EXE, MSIX can only be
#                                               made on Windows), waits for it, downloads the files to dist/windows-<version>/
#
# Options:
#   --version X.Y.Z     the installers' package number (default: packageVersion in apps/desktop/version.properties)
#   --ref BRANCH        windows: the branch the workflow builds (default: the current branch; it builds what is PUSHED there)
#   --run-id N          windows: do not start a run, only wait for / download run N (e.g. one started by a push)
#   --timeout MIN       windows: how long to wait for the run (default 60)
#   --dry-run           print the commands (gh workflow run, gh run download, Gradle) and change nothing
source "$(dirname "$0")/lib.sh"

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

MODE="${1:-}"; [ -n "$MODE" ] || usage 1; shift
case "$MODE" in test|mac|windows) ;; -h|--help|help) usage 0 ;; *) die "unknown command '$MODE' (test, mac or windows)" ;; esac
VERSION="" REF="" RUN_ID="" WAIT_MIN=60
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?}"; shift ;;
    --ref) REF="${2:?}"; shift ;;
    --run-id) RUN_ID="${2:?}"; shift ;;
    --timeout) WAIT_MIN="${2:?}"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done

DESKTOP="$REPO/apps/desktop"
WORKFLOW="$REPO/.github/workflows/desktop-windows.yml"
DIST="$REPO/dist"
if [ -z "$VERSION" ]; then   # the installers' package number: one tracked place (docs/versioning.md)
  VERSION="$(sed -n 's/^packageVersion=\([0-9][0-9.]*\)[[:space:]]*$/\1/p' "$DESKTOP/version.properties" | head -1)"
  [ -n "$VERSION" ] || die "no packageVersion in $DESKTOP/version.properties; pass --version X.Y.Z"
fi
case "$VERSION" in [1-9]*.*[0-9].*[0-9]) ;; *) die "--version must be MAJOR.MINOR.BUILD with MAJOR >= 1 (MSI rule), got $VERSION" ;; esac

gradle_desktop() {   # gradle_desktop step|soft_step NAME TIMEOUT LOG tasks...
  local how="$1" name="$2" secs="$3" logf="$4"; shift 4
  if [ "$DRY_RUN" != 1 ]; then
    need_disk
    # macOS app: the one pinned JDK (apps/desktop/packaging/macos/JDK), and Gradle started with the zipclock agent (tools/build/zipclock/README.md)
    if [ "${PIN_JDK:-0}" = 1 ]; then kelo_packaging_jdk; kelo_zip_clock "$REPO"; else use_jdk17; fi
  fi
  cd "$DESKTOP"
  source_commit apps/desktop apps/shared version.json   # only "modified" is used: the version line in My links by release tag (docs/versioning.md)
  "$how" "$name" "$secs" "$logf" ./gradlew --no-daemon --console=plain -PkeloDirty="$SOURCE_DIRTY" \
    -Pkotlin.compiler.execution.strategy=in-process \
    ${GRADLE_REPRO_ARGS[@]+"${GRADLE_REPRO_ARGS[@]}"} "$@"
  cd "$REPO"
}

# ------------------------------------------------------------------------------------------------------------------------ test
if [ "$MODE" = test ]; then
  gradle_desktop soft_step "desktop tests" "${KELO_GRADLE_TIMEOUT:-2700}" "$LOGS/desktop-test.log" test
  log "done: desktop tests"
  if [ "$DRY_RUN" = 0 ]; then
    # counts and the failed tests with their first message line, from Gradle's JUnit XML
    python3 -I - "$DESKTOP/build/test-results/test" >&2 <<'EOF'
import glob, sys, xml.etree.ElementTree as ET
t = f = e = s = 0
failed = []
for p in glob.glob(sys.argv[1] + "/*.xml"):
    r = ET.parse(p).getroot()
    t += int(r.get("tests", 0)); f += int(r.get("failures", 0)); e += int(r.get("errors", 0)); s += int(r.get("skipped", 0))
    for tc in r.iter("testcase"):
        bad = tc.find("failure")
        if bad is None:
            bad = tc.find("error")
        if bad is not None:
            msg = (bad.get("message") or "").splitlines()
            failed.append(f"{tc.get('classname', '').rsplit('.', 1)[-1]}.{tc.get('name')}: {msg[0][:300] if msg else ''}")
print(f"    {t} tests: {t - f - e - s} passed, {f + e} failed, {s} skipped")
for line in failed[:20]:
    print("    failed: " + line)
EOF
    note "report: $DESKTOP/build/reports/tests/test/index.html"
  fi
  finish_soft
  exit 0
fi

# ------------------------------------------------------------------------------------------------------------------------- mac
if [ "$MODE" = mac ]; then
  [ "$(uname)" = Darwin ] || die "the disk image can only be made on a Mac"
  PIN_JDK=1   # built with the pinned JDK, so anyone can rebuild the same app (tools/verify/mac-verify.sh)
  # signing: a "Developer ID Application" certificate in the keychain (none -> unsigned); notarizing: the App Store Connect key
  # A dedicated signing keychain (kelo-signing.keychain-db, its random password in ~/.kelo-signing/keychain-pass, 0600) is
  # unlocked first: it is locked again after a restart.
  SIGN_KC="$HOME/Library/Keychains/kelo-signing.keychain-db"
  if [ -f "$SIGN_KC" ] && [ -f "$HOME/.kelo-signing/keychain-pass" ]; then
    security unlock-keychain -p "$(cat "$HOME/.kelo-signing/keychain-pass")" "$SIGN_KC" 2>/dev/null || warn "could not unlock $SIGN_KC"
  fi
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)"
  SIGN_ARGS=()
  if [ -n "$IDENTITY" ]; then
    SIGN_ARGS=(-Pcompose.desktop.mac.sign=true "-Pcompose.desktop.mac.signing.identity=${IDENTITY#Developer ID Application: }")
    note "signing with the keychain's Developer ID Application certificate"
  else
    warn "no 'Developer ID Application' certificate in the keychain: the DMG is UNSIGNED (Gatekeeper warns; not for distribution)"
  fi
  gradle_desktop step "packageDmg $VERSION" "${KELO_GRADLE_TIMEOUT:-2700}" "$LOGS/desktop-dmg.log" \
    packageDmg "-PkeloVersion=$VERSION" ${SIGN_ARGS[@]+"${SIGN_ARGS[@]}"}
  DMG="$DESKTOP/build/compose/binaries/main/dmg/Kelo-$VERSION.dmg"
  OUT="$DIST/Kelo-$VERSION$([ -z "$IDENTITY" ] && echo -unsigned || true).dmg"
  if [ "$DRY_RUN" = 1 ]; then
    note "[dry-run] would copy $DMG -> $OUT"
    exit 0
  fi
  [ -f "$DMG" ] || die "Gradle finished but $DMG is missing (log: $LOGS/desktop-dmg.log)"

  ASC_ENV="${KELO_ASC_ENV:-$HOME/.appstoreconnect/kelo.env}"
  if [ -n "$IDENTITY" ] && [ -f "$ASC_ENV" ]; then
    KEY_ID="$(sed -n 's/^KEY_ID=//p' "$ASC_ENV" | tr -d "\"'")"
    ISSUER_ID="$(sed -n 's/^ISSUER_ID=//p' "$ASC_ENV" | tr -d "\"'")"
    KEY_PATH="$(sed -n 's/^KEY_PATH=//p' "$ASC_ENV" | tr -d "\"'")"; KEY_PATH="${KEY_PATH/#\~/$HOME}"
    REDACT_VALUES="$KEY_ID $ISSUER_ID"
    # the app inside is signed by jpackage; the disk image itself is signed too, so Gatekeeper accepts the .dmg file as well
    step "sign the disk image" 300 "$LOGS/desktop-dmg-sign.log" codesign --force --timestamp --sign "$IDENTITY" "$DMG"
    step "notarize (Apple notary service)" 3600 "$LOGS/desktop-notarize.log" \
      xcrun notarytool submit "$DMG" --key "$KEY_PATH" --key-id "$KEY_ID" --issuer "$ISSUER_ID" --wait --timeout 50m
    grep -q "status: Accepted" "$LOGS/desktop-notarize.log" || { tail -20 "$LOGS/desktop-notarize.log" | redact >&2; die "notarization was not accepted"; }
    step "staple" 300 "$LOGS/desktop-staple.log" xcrun stapler staple "$DMG"
    note "signed, notarized and stapled"
  elif [ -n "$IDENTITY" ]; then
    warn "signed but NOT notarized: no App Store Connect key at $ASC_ENV"
  fi
  mkdir -p "$DIST"
  cp "$DMG" "$OUT"
  log "done: Kelo desktop $VERSION for macOS"
  note "output: $OUT ($(human_size "$OUT"))"
  note "offer it for download (main copy, signed builds only): (cd $REPO && uv run assistant publish-app mac $OUT --version $VERSION)"
  exit 0
fi

# --------------------------------------------------------------------------------------------------------------------- windows
command -v gh >/dev/null || die "gh (GitHub CLI) not found: brew install gh, then gh auth login"
[ -n "$REF" ] || REF="$(git -C "$REPO" branch --show-current 2>/dev/null || true)"
[ -n "$REF" ] || die "no current branch; pass --ref BRANCH"
OUTDIR="$DIST/windows-$VERSION"
WF="desktop-windows.yml"

if [ "$DRY_RUN" = 0 ]; then
  gh auth status >/dev/null 2>&1 || die "gh is not signed in (gh auth login)"
  need_disk
fi

if [ -z "$RUN_ID" ]; then
  # the runner builds what is on GitHub, not this folder: say so when the branch has local changes or unpushed commits
  if [ -n "$(git -C "$REPO" status --porcelain 2>/dev/null | head -1)" ]; then
    warn "this folder has uncommitted changes; the workflow builds '$REF' as pushed to GitHub, without them"
  fi
  ahead="$(git -C "$REPO" rev-list --count "origin/$REF..$REF" 2>/dev/null || echo 0)"
  [ "$ahead" = 0 ] || warn "'$REF' has $ahead commit(s) not pushed; the workflow will not see them"
  log "start the Windows build on GitHub ($WF, ref $REF, version $VERSION)"
  if [ "$DRY_RUN" = 1 ]; then
    note "[dry-run] gh workflow run $WF --ref $REF -f version=$VERSION"
    note "[dry-run] gh run list --workflow $WF --event workflow_dispatch --branch $REF --limit 1 --json databaseId,createdAt   (the new run's id)"
    RUN_ID="<run-id>"
  else
    started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    with_timeout 60 gh workflow run "$WF" --ref "$REF" -f "version=$VERSION"
    for i in $(seq 1 20); do   # the run shows up a few seconds after the dispatch
      sleep 6
      RUN_ID="$(gh run list --workflow "$WF" --event workflow_dispatch --branch "$REF" --limit 5 --json databaseId,createdAt \
        --jq "[.[] | select(.createdAt >= \"$started\")] | sort_by(.createdAt) | last | .databaseId // empty" 2>/dev/null || true)"
      [ -n "$RUN_ID" ] && break
    done
    [ -n "$RUN_ID" ] || die "the workflow was started but its run did not show up; see: gh run list --workflow $WF"
  fi
fi
note "run $RUN_ID: https://github.com/$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || echo '<owner/repo>')/actions/runs/$RUN_ID"

log "wait for run $RUN_ID (max $WAIT_MIN min)"
if [ "$DRY_RUN" = 1 ]; then
  note "[dry-run] gh run watch $RUN_ID --exit-status --interval 30   (timeout $WAIT_MIN min)"
  note "[dry-run] gh run download $RUN_ID --dir $OUTDIR   (the MSI, EXE and MSIX)"
  log "done (dry-run): nothing was started or downloaded"
  exit 0
fi
rc=0; with_timeout $((WAIT_MIN * 60)) gh run watch "$RUN_ID" --exit-status --interval 30 >"$LOGS/desktop-windows-watch.log" 2>&1 || rc=$?
case "$rc" in
  0) ;;
  124) die "run $RUN_ID still not finished after $WAIT_MIN min (it keeps running on GitHub; download later with --run-id $RUN_ID)" ;;
  *) tail -30 "$LOGS/desktop-windows-watch.log" >&2; die "run $RUN_ID failed; details: gh run view $RUN_ID --log-failed" ;;
esac

rm -rf "$OUTDIR"; mkdir -p "$OUTDIR"
make_temp windows DL
with_timeout 900 gh run download "$RUN_ID" --dir "$DL" || die "download of run $RUN_ID failed"
find "$DL" -type f \( -name '*.msi' -o -name '*.exe' -o -name '*.msix' \) -exec mv {} "$OUTDIR"/ \;
n="$(find "$OUTDIR" -type f | wc -l | tr -d ' ')"
[ "$n" -gt 0 ] || die "run $RUN_ID had no MSI/EXE/MSIX files"
log "done: Kelo desktop $VERSION for Windows ($n files)"
for f in "$OUTDIR"/*; do note "output: $f ($(human_size "$f"))"; done
note "offer for download (main copy): uv run assistant publish-app windows <the .exe>  /  publish-app windows-msi <the .msi>"
for f in "$OUTDIR"/*.msix; do [ -e "$f" ] && note "Microsoft Store draft (not run; see apps/desktop/store/MSSTORE.md): python3 -I apps/desktop/store/msstore.py submit --msix $f   (add --commit to send it to certification)"; done
