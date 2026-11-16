#!/bin/bash
# Kelo for iPhone: test on the simulator, or send a build to TestFlight. See tools/build/README.md.
#
#   tools/build/ios.sh sim          KeloCore + KeloUI package tests, then the app built and its UI walk (KeloScreensTests/testWalk)
#                                   run on the simulator "kelo-ios-check" against apps/desktop/tools/mock_server.py behind
#                                   apps/ios/tools/mock_e2e_gateway.py (KE2E; needs uv and the server's environment)
#   tools/build/ios.sh testflight   new build number (YYMMDDHH) in project.yml -> xcodegen -> unsigned archive -> export signed
#                                   by Apple (cloud signing, App Store Connect API key) -> upload -> wait until the build is
#                                   processed (VALID)
#
# Options (sim):
#   --skip-packages     do not run the swift package tests
#   --only ID           the UI test(s) to run (default KeloUITests/KeloScreensTests/testWalk); repeatable
#   --no-mock           no mock server: the UI tests skip themselves, so this only builds and starts the test runner
#   --lang en|ko        the walk's language (default en)
#   --shots DIR         keep the walk's screenshots in DIR (default: a temp folder, deleted)
# Options (testflight):
#   --build N           use this build number instead of a fresh YYMMDDHH (must be higher than every earlier upload)
#   --no-wait           stop after the upload (do not wait for App Store Connect's processing)
#   --wait-timeout S    how long to wait for processing (default 2700 s)
#   --keep-archive DIR  keep the .xcarchive (with its dSYMs) in DIR
#   --no-upload DIR     export the signed App Store IPA into DIR instead of uploading it (nothing goes to App Store Connect; the
#                       build number is still checked against App Store Connect's newest)
# Both: --dry-run prints the steps and the build number and changes nothing.
#
# KELO_LOCK_DIR=<folder> makes the script take <folder>/XCODE_LOCK (and DEVICE_LOCK for sim) before heavy Xcode work.
source "$(dirname "$0")/lib.sh"

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

MODE="${1:-}"; [ -n "$MODE" ] || usage 1; shift
case "$MODE" in sim|testflight) ;; -h|--help|help) usage 0 ;; *) die "unknown command '$MODE' (sim or testflight)" ;; esac
SKIP_PACKAGES=0 ONLY=() MOCK=1 LANG_=en SHOTS="" BUILD_NO="" WAIT=1 WAIT_TIMEOUT=2700 KEEP_ARCHIVE="" IPA_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-packages) SKIP_PACKAGES=1 ;;
    --only) ONLY+=("${2:?--only needs a test id}"); shift ;;
    --no-mock) MOCK=0 ;;
    --lang) LANG_="${2:?}"; shift; case "$LANG_" in en|ko) ;; *) die "--lang en or ko" ;; esac ;;
    --shots) SHOTS="${2:?}"; shift ;;
    --build) BUILD_NO="${2:?}"; shift ;;
    --no-wait) WAIT=0 ;;
    --wait-timeout) WAIT_TIMEOUT="${2:?}"; shift ;;
    --keep-archive) KEEP_ARCHIVE="${2:?}"; shift ;;
    --no-upload) IPA_DIR="${2:?--no-upload needs a folder for the .ipa}"; WAIT=0; shift ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done

IOS="$REPO/apps/ios"
# the commit this build's source came from, asked now (before this script edits project.yml for a TestFlight build number): My links to it
source_commit apps/ios apps/shared/strings version.json
SOURCE_ARGS=("KELO_SOURCE_COMMIT=$SOURCE_COMMIT" "KELO_SOURCE_DIRTY=$([ "$SOURCE_DIRTY" = true ] && echo YES || echo NO)")
PROJ_DIR="$IOS/Kelo"
SPEC="$PROJ_DIR/project.yml"
SIM_NAME="${KELO_SIM_NAME:-kelo-ios-check}"
ASC=(python3 -I "$BUILD_DIR/asc.py")
command -v xcodebuild >/dev/null || die "xcodebuild not found (install Xcode)"
command -v xcodegen >/dev/null || die "xcodegen not found (brew install xcodegen)"

[ "$DRY_RUN" = 1 ] || need_disk
make_temp ios TMP                # DerivedData, archive, export, SwiftPM scratch: all deleted on exit
DD="$TMP/DerivedData"

generate_project() {
  step "version (apps/ios/tools/gen_version.py, from version.json)" 60 "$LOGS/ios-version.log" python3 "$IOS/tools/gen_version.py"
  step "strings (apps/ios/tools/gen_strings.py)" 300 "$LOGS/ios-strings.log" python3 "$IOS/tools/gen_strings.py"
  step "xcodegen" 300 "$LOGS/ios-xcodegen.log" xcodegen generate --spec "$SPEC" --project "$PROJ_DIR"
}

# ------------------------------------------------------------------------------------------------------------------------- sim
if [ "$MODE" = sim ]; then
  if [ "$SKIP_PACKAGES" = 0 ]; then
    # KELO_REQUIRE_VECTORS=1: the KE2E vectors test fails (not skips) when docs/security/e2e-vectors.json is missing
    soft_step "KeloCore tests (swift test)" 1800 "$LOGS/ios-kelocore.log" env KELO_REQUIRE_VECTORS=1 "$IOS/KeloCore/test.sh" --scratch-path "$TMP/spm-core"
    soft_step "KeloUI tests (swift test)" 2400 "$LOGS/ios-keloui.log" "$IOS/KeloUI/test.sh" --scratch-path "$TMP/spm-ui"
    if [ "$DRY_RUN" = 0 ]; then
      for f in kelocore keloui; do note "$f: $(grep -E 'Test run with [0-9]+ tests|Executed [0-9]+ tests' "$LOGS/ios-$f.log" | tail -1 | sed 's/^[^A-Za-z]*//')"; done
      rm -rf "$TMP/spm-core" "$TMP/spm-ui"   # free the space before the Xcode build
    fi
  fi

  UDID="$(xcrun simctl list devices -j | python3 -I -c '
import json, sys
name = sys.argv[1]
for runtime, devs in json.load(sys.stdin)["devices"].items():
    for d in devs:
        if d["name"] == name and d.get("isAvailable"):
            print(d["udid"], d["state"]); sys.exit()' "$SIM_NAME")"
  [ -n "$UDID" ] || die "no simulator named $SIM_NAME (create it once, see apps/ios/README.md; this script never makes new ones)"
  SIM_STATE="${UDID#* }"; UDID="${UDID%% *}"
  note "simulator $SIM_NAME ($UDID, $SIM_STATE)"

  [ ${#ONLY[@]} -gt 0 ] || ONLY=(KeloUITests/KeloScreensTests/testWalk)
  ONLY_ARGS=(); for t in "${ONLY[@]}"; do ONLY_ARGS+=("-only-testing:$t"); done
  [ -n "$SHOTS" ] || SHOTS="$TMP/shots"
  [ "$DRY_RUN" = 1 ] || mkdir -p "$SHOTS"

  generate_project
  take_lock XCODE_LOCK DEVICE_LOCK

  TEST_ENV=()
  if [ "$MOCK" = 1 ]; then
    PORT="$(free_port)"
    if [ "$DRY_RUN" = 1 ]; then
      note "[dry-run] python3 apps/desktop/tools/mock_server.py --port $PORT --lang $LANG_   (stopped at the end)"
    else
      python3 "$REPO/apps/desktop/tools/mock_server.py" --port "$PORT" --lang "$LANG_" >"$LOGS/ios-mock.log" 2>&1 &
      MOCK_PID=$!
      on_exit "kill $MOCK_PID 2>/dev/null; wait $MOCK_PID 2>/dev/null"
      up=0
      for i in $(seq 1 30); do
        if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/"; then up=1; break; fi
        kill -0 "$MOCK_PID" 2>/dev/null || break
        sleep 1
      done
      [ "$up" = 1 ] || { tail -20 "$LOGS/ios-mock.log" >&2; die "the mock server did not start"; }
      note "mock server on 127.0.0.1:$PORT (pid $MOCK_PID)"
      # the app talks only KE2E (spec 5.18): the server's own KE2E layer in front of the plaintext mock
      UPSTREAM="$PORT"
      PORT="$(free_port)"
      (cd "$REPO" && exec uv run --quiet python apps/ios/tools/mock_e2e_gateway.py --port "$PORT" --upstream "$UPSTREAM") >"$LOGS/ios-e2e-gateway.log" 2>&1 &
      GW_PID=$!
      on_exit "kill $GW_PID 2>/dev/null; wait $GW_PID 2>/dev/null"
      up=0
      for i in $(seq 1 60); do
        if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/api/e2e/info"; then up=1; break; fi
        kill -0 "$GW_PID" 2>/dev/null || break
        sleep 1
      done
      [ "$up" = 1 ] || { tail -20 "$LOGS/ios-e2e-gateway.log" >&2; die "the KE2E gateway did not start"; }
      note "KE2E gateway on 127.0.0.1:$PORT -> mock (pid $GW_PID)"
    fi
    TOKEN="$(sed -n 's/^TOKEN = "\(.*\)"/\1/p' "$REPO/apps/desktop/tools/mock_server.py" | head -1)"
    TEST_ENV=(TEST_RUNNER_KELO_SERVER="http://127.0.0.1:$PORT" TEST_RUNNER_KELO_KEY="$TOKEN" TEST_RUNNER_KELO_LANG="$LANG_"
              TEST_RUNNER_KELO_TAG="$LANG_-check" TEST_RUNNER_KELO_SHOTS="$SHOTS")
  fi

  if [ "$DRY_RUN" = 0 ]; then
    if [ "$SIM_STATE" != Booted ]; then
      xcrun simctl boot "$UDID" >/dev/null 2>&1 || true
      on_exit "xcrun simctl shutdown $UDID >/dev/null 2>&1"   # only because this run booted it
      with_timeout 300 xcrun simctl bootstatus "$UDID" >/dev/null 2>&1 || die "simulator $SIM_NAME did not boot"
    fi
    # a fresh start for the walk: no installed app, no saved sign-in
    xcrun simctl uninstall "$UDID" chat.kelo.app >/dev/null 2>&1 || true
    xcrun simctl keychain "$UDID" reset >/dev/null 2>&1 || true
  fi

  soft_step "xcodebuild test on $SIM_NAME (${ONLY[*]})" "${KELO_XCODE_TIMEOUT:-2700}" "$LOGS/ios-test.log" \
    env ${TEST_ENV[@]+"${TEST_ENV[@]}"} xcodebuild -project "$PROJ_DIR/Kelo.xcodeproj" -scheme Kelo \
      -destination "platform=iOS Simulator,id=$UDID" -derivedDataPath "$DD" -resultBundlePath "$TMP/test.xcresult" \
      CODE_SIGNING_ALLOWED=NO "${SOURCE_ARGS[@]}" ${ONLY_ARGS[@]+"${ONLY_ARGS[@]}"} test
  release_lock DEVICE_LOCK
  release_lock XCODE_LOCK

  log "done: iOS simulator check"
  if [ "$DRY_RUN" = 0 ]; then
    grep -E "Executed [0-9]+ tests?, with" "$LOGS/ios-test.log" | tail -1 | sed 's/^ */    /' >&2 || true
    grep -cE "Test case .* skipped|' skipped \(" "$LOGS/ios-test.log" >/dev/null 2>&1 && \
      note "skipped: $(grep -cE "Test case .* skipped|' skipped \(" "$LOGS/ios-test.log")"
    note "screenshots: $(find "$SHOTS" -name '*.png' 2>/dev/null | wc -l | tr -d ' ') in $SHOTS$([ "$SHOTS" = "$TMP/shots" ] && echo ' (temp, deleted now; use --shots DIR to keep them)')"
    note "logs: $LOGS/ios-*.log"
  fi
  finish_soft
  exit 0
fi

# ------------------------------------------------------------------------------------------------------------------ testflight
ASC_ENV="${KELO_ASC_ENV:-$HOME/.appstoreconnect/kelo.env}"
[ -f "$ASC_ENV" ] || die "no App Store Connect API key settings at $ASC_ENV (ISSUER_ID, KEY_ID, KEY_PATH; see README)"
# read the key settings without echoing them; the values are only redacted from log tails and passed to xcodebuild
ISSUER_ID="$(sed -n 's/^ISSUER_ID=//p' "$ASC_ENV" | tr -d "\"'")"
KEY_ID="$(sed -n 's/^KEY_ID=//p' "$ASC_ENV" | tr -d "\"'")"
KEY_PATH="$(sed -n 's/^KEY_PATH=//p' "$ASC_ENV" | tr -d "\"'")"; KEY_PATH="${KEY_PATH/#\~/$HOME}"
[ -n "$ISSUER_ID" ] && [ -n "$KEY_ID" ] && [ -f "$KEY_PATH" ] || die "$ASC_ENV must set ISSUER_ID, KEY_ID and KEY_PATH (an existing .p8)"
REDACT_VALUES="$ISSUER_ID $KEY_ID"
TEAM="$(sed -n 's/^ *DEVELOPMENT_TEAM: *\([A-Z0-9]*\).*/\1/p' "$SPEC" | head -1)"
[ -n "$TEAM" ] || die "no DEVELOPMENT_TEAM in $SPEC"

log "App Store Connect key"
"${ASC[@]}" check >&2 || die "the App Store Connect key does not work (see above)"

# --- build number: YYMMDDHH, but always above what project.yml has and what App Store Connect has seen
CURRENT="$(sed -n 's/^ *CURRENT_PROJECT_VERSION: *"\{0,1\}\([0-9]*\)"\{0,1\}.*/\1/p' "$SPEC" | head -1)"
[ -n "$CURRENT" ] || die "no CURRENT_PROJECT_VERSION in $SPEC"
LATEST="$("${ASC[@]}" builds --limit 1 | awk 'NR==1 {print $2}')"
case "$LATEST" in ''|*[!0-9]*) LATEST=0 ;; esac
if [ -z "$BUILD_NO" ]; then
  BUILD_NO="$(date +%y%m%d%H)"
  [ "$BUILD_NO" -gt "$CURRENT" ] || BUILD_NO=$((CURRENT + 1))
  [ "$BUILD_NO" -gt "$LATEST" ] || BUILD_NO=$((LATEST + 1))
fi
case "$BUILD_NO" in ''|*[!0-9]*) die "--build must be a whole number" ;; esac
[ "$BUILD_NO" -gt "$LATEST" ] || die "build $BUILD_NO is not higher than the last upload ($LATEST)"
MARKETING="$(sed -n 's/^ *MARKETING_VERSION: *"\{0,1\}\([0-9.]*\)"\{0,1\}.*/\1/p' "$SPEC" | head -1)"
note "version $MARKETING ($BUILD_NO); project.yml had $CURRENT, App Store Connect's newest is $LATEST"

if [ "$DRY_RUN" = 1 ]; then
  note "[dry-run] would set CURRENT_PROJECT_VERSION: \"$BUILD_NO\" in apps/ios/Kelo/project.yml"
elif [ "$BUILD_NO" != "$CURRENT" ]; then
  sed -i '' -E "s/^( *CURRENT_PROJECT_VERSION: *)\"?[0-9]+\"?/\1\"$BUILD_NO\"/" "$SPEC"
  grep -q "CURRENT_PROJECT_VERSION: \"$BUILD_NO\"" "$SPEC" || die "could not write the build number into $SPEC"
  note "project.yml: CURRENT_PROJECT_VERSION -> $BUILD_NO (commit this change)"
fi

generate_project
take_lock XCODE_LOCK

step "archive (unsigned, Release)" "${KELO_ARCHIVE_TIMEOUT:-2400}" "$LOGS/ios-archive.log" \
  xcodebuild -project "$PROJ_DIR/Kelo.xcodeproj" -scheme Kelo -configuration Release -destination 'generic/platform=iOS' \
    -derivedDataPath "$DD" -archivePath "$TMP/Kelo.xcarchive" \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= "${SOURCE_ARGS[@]}" archive
[ "$DRY_RUN" = 1 ] || rm -rf "$DD"     # the archive holds what is needed; free the space before exporting

# An unsigned archive carries no entitlements (push, ...): put them on the app with an ad-hoc signature first; the export below
# signs it again with the distribution profile, which turns aps-environment into "production".
if [ "$DRY_RUN" = 1 ]; then
  note "[dry-run] codesign -f -s - --entitlements $PROJ_DIR/App/Kelo.entitlements <tmp>/Kelo.xcarchive/Products/Applications/Kelo.app"
else
  step "add the app's entitlements to the archive" 300 "$LOGS/ios-entitlements.log" \
    codesign -f -s - --entitlements "$PROJ_DIR/App/Kelo.entitlements" "$TMP/Kelo.xcarchive/Products/Applications/Kelo.app"
fi

cat >"$TMP/ExportOptions.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>method</key><string>app-store-connect</string>
	<key>destination</key><string>$([ -n "$IPA_DIR" ] && echo export || echo upload)</string>
	<key>teamID</key><string>$TEAM</string>
	<key>signingStyle</key><string>automatic</string>
	<key>manageAppVersionAndBuildNumber</key><false/>
	<key>uploadSymbols</key><true/>
	<key>testFlightInternalTestingOnly</key><false/>
</dict>
</plist>
EOF

# xcodebuild gets the key as a file path plus its ids (Apple signs in the cloud; no certificate on this Mac is needed)
if [ "$DRY_RUN" = 1 ]; then
  note "[dry-run] xcodebuild -exportArchive -archivePath <tmp>/Kelo.xcarchive -exportOptionsPlist <tmp>/ExportOptions.plist -exportPath <tmp>/export -allowProvisioningUpdates -authenticationKeyPath <KEY_PATH> -authenticationKeyID <KEY_ID> -authenticationKeyIssuerID <ISSUER_ID>   (timeout 30 min; UPLOADS to App Store Connect)"
else
  step "export and sign$([ -n "$IPA_DIR" ] && echo ' (no upload)' || echo ', upload to App Store Connect')" "${KELO_EXPORT_TIMEOUT:-1800}" "$LOGS/ios-export.log" \
    xcodebuild -exportArchive -archivePath "$TMP/Kelo.xcarchive" -exportOptionsPlist "$TMP/ExportOptions.plist" \
      -exportPath "$TMP/export" -allowProvisioningUpdates \
      -authenticationKeyPath "$KEY_PATH" -authenticationKeyID "$KEY_ID" -authenticationKeyIssuerID "$ISSUER_ID"
  grep -q "Upload succeeded\|EXPORT SUCCEEDED" "$LOGS/ios-export.log" || die "export finished without 'Upload succeeded' (log: $LOGS/ios-export.log)"
  if [ -n "$IPA_DIR" ]; then
    ipa="$(find "$TMP/export" -maxdepth 1 -name '*.ipa' | head -1)"
    [ -n "$ipa" ] || die "the export made no .ipa (log: $LOGS/ios-export.log)"
    mkdir -p "$IPA_DIR"
    mv "$ipa" "$IPA_DIR/Kelo-$MARKETING-$BUILD_NO.ipa"
    note "IPA (not uploaded): $IPA_DIR/Kelo-$MARKETING-$BUILD_NO.ipa"
  fi
fi
release_lock XCODE_LOCK

if [ -n "$KEEP_ARCHIVE" ] && [ "$DRY_RUN" = 0 ]; then
  mkdir -p "$KEEP_ARCHIVE"; rm -rf "$KEEP_ARCHIVE/Kelo-$BUILD_NO.xcarchive"
  mv "$TMP/Kelo.xcarchive" "$KEEP_ARCHIVE/Kelo-$BUILD_NO.xcarchive"
  note "archive kept: $KEEP_ARCHIVE/Kelo-$BUILD_NO.xcarchive"
fi

if [ "$WAIT" = 1 ]; then
  log "waiting for App Store Connect to process build $BUILD_NO (max $((WAIT_TIMEOUT / 60)) min)"
  if [ "$DRY_RUN" = 1 ]; then
    note "[dry-run] python3 -I tools/build/asc.py wait-build --version $BUILD_NO --timeout $WAIT_TIMEOUT"
  else
    rc=0; with_timeout $((WAIT_TIMEOUT + 120)) "${ASC[@]}" wait-build --version "$BUILD_NO" --timeout "$WAIT_TIMEOUT" >&2 || rc=$?
    case "$rc" in
      0) note "build $BUILD_NO is VALID: it shows up in TestFlight (testers get it once it is added to a group)" ;;
      2) die "App Store Connect rejected build $BUILD_NO (FAILED/INVALID): see the e-mail from Apple or App Store Connect -> TestFlight" ;;
      *) die "build $BUILD_NO was uploaded but not processed within $((WAIT_TIMEOUT / 60)) min; check later: python3 -I tools/build/asc.py builds" ;;
    esac
  fi
fi

log "done: Kelo iOS $MARKETING ($BUILD_NO)$([ "$DRY_RUN" = 1 ] && echo ' [dry-run: nothing built or uploaded]')"
note "CHANGELOG: mention 'iOS $MARKETING ($BUILD_NO) on TestFlight' in the release's entry"
