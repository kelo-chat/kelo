#!/bin/bash
# Raw App Store captures: ios/Kelo/UITests/KeloStoreShotsTests.swift on the kelo-ios-check simulator (iPhone 17, 1206x2622)
# against tools/store/store_mock.py, through tools/build/ios.sh sim (it builds, takes XCODE_LOCK and DEVICE_LOCK when
# KELO_LOCK_DIR is set, and removes its DerivedData).
#
#   tools/store/ios_shots.sh OUT_DIR [en] [ko]        (default: en ko)
#   then: uv run python tools/store/compose.py ios OUT_DIR ios/store/screenshots
#
# Writes OUT_DIR/{en,ko}-01.png .. -08.png. The app runs in a time zone where it is morning now (store_mock.py --print-tz, passed
# to the app as TZ), and the simulator's status bar shows that zone's time, a full battery and Wi-Fi (simctl status_bar; cleared
# at the end). It stops only the mock it started.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="${1:?OUT_DIR}"; shift
LANGS=("$@"); [ ${#LANGS[@]} -gt 0 ] || LANGS=(en ko)
PORT="${KELO_STORE_PORT:-8792}"
SIM="${KELO_SIM_NAME:-kelo-ios-check}"
UDID="$(xcrun simctl list devices | sed -n "s/^ *$SIM (\([0-9A-F-]*\)).*/\1/p" | head -1)"
[ -n "$UDID" ] || { echo "no simulator named $SIM" >&2; exit 1; }
mkdir -p "$OUT"
RAW="$(mktemp -d "$(cd "$OUT" && pwd)/.ios-run.XXXXXX")"
LOGS="$RAW/logs"
MOCK_PID=""; RUN_PID=""
cleanup() {
  [ -n "$RUN_PID" ] && kill "$RUN_PID" 2>/dev/null || true
  [ -n "$MOCK_PID" ] && { kill "$MOCK_PID" 2>/dev/null || true; wait "$MOCK_PID" 2>/dev/null || true; }
  xcrun simctl status_bar "$UDID" clear >/dev/null 2>&1 || true
  rm -rf "$RAW"
  return 0
}
trap cleanup EXIT

TZ_NAME="$(uv run python tools/store/store_mock.py --print-tz)"
echo "time zone: $TZ_NAME"
for L in "${LANGS[@]}"; do
  uv run python tools/store/store_mock.py --lang "$L" --port "$PORT" --tz "$TZ_NAME" --brief 07:00 > "$RAW/mock-$L.log" 2>&1 &
  MOCK_PID=$!
  for _ in $(seq 1 30); do curl -s -o /dev/null --max-time 1 "127.0.0.1:$PORT/healthz" && break; sleep 0.5; done
  rm -f "$LOGS/ios-test.log"
  KELO_BUILD_LOGS="$LOGS" TEST_RUNNER_KELO_STORE_SERVER="http://127.0.0.1:$PORT" TEST_RUNNER_KELO_STORE_TZ="$TZ_NAME" \
    tools/build/ios.sh sim --skip-packages --lang "$L" --only KeloUITests/KeloStoreShotsTests/testStoreShots --shots "$RAW/shots" &
  RUN_PID=$!
  # once the tests start (the build is done), the status bar shows the zone's time
  while kill -0 "$RUN_PID" 2>/dev/null && ! grep -qE "Test (Suite|Case) .* started" "$LOGS/ios-test.log" 2>/dev/null; do sleep 2; done
  xcrun simctl status_bar "$UDID" override --time "$(TZ="$TZ_NAME" date -v+1M +%-H:%M)" --dataNetwork wifi --wifiMode active \
    --wifiBars 3 --cellularMode notSupported --batteryState discharging --batteryLevel 100 || true
  if ! wait "$RUN_PID"; then
    mkdir -p "$OUT/logs-$L"; cp "$LOGS"/ios-*.log "$RAW/mock-$L.log" "$OUT/logs-$L/" 2>/dev/null || true
    echo "ios.sh reported a failure for $L (logs kept in $OUT/logs-$L)" >&2
  fi
  RUN_PID=""
  kill "$MOCK_PID" 2>/dev/null || true; wait "$MOCK_PID" 2>/dev/null || true; MOCK_PID=""
  for f in "$RAW/shots/$L-check-"[0-9][0-9]-*.png; do
    [ -e "$f" ] || continue
    n="$(basename "$f" | sed -E "s/^$L-check-([0-9]{2})-.*/\1/")"
    cp "$f" "$OUT/$L-$n.png"; echo "$OUT/$L-$n.png"
  done
done
