#!/bin/bash
# Raw Google Play captures: the installed Kelo app on an Android emulator against tools/store/store_mock.py, at a Galaxy S size.
#
#   tools/store/android_shots.sh OUT_DIR [en] [ko]        (default: en ko)
#   then: uv run python tools/store/compose.py android OUT_DIR android/play/screenshots
#
# Writes OUT_DIR/{en,ko}-01.png .. -08.png, 1080x2340 (the scenes and their order: tools/store/compose.py SHOTS).
# Environment: KELO_ADB_SERIAL (default: the only device), KELO_LOCK_DIR (take <dir>/DEVICE_LOCK with mkdir first, as
# tools/build/ios.sh does), KELO_APK (install this APK first), KELO_STORE_PORT (default 8790).
# What it changes on the device and puts back at the end, also after a failure: the screen size and density (wm size 1080x2340,
# density 420), the time zone (one where it is morning now: store_mock.py --print-tz), the System UI demo mode (a clean status bar
# with the clock at the zone's time), the app's language and a port forwarding. It stops only the mock it started.
# The app's data is cleared first (pm clear chat.kelo.app), then it signs in to http://127.0.0.1:$PORT with the mock's key.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="${1:?OUT_DIR}"; shift
LANGS=("$@"); [ ${#LANGS[@]} -gt 0 ] || LANGS=(en ko)
PORT="${KELO_STORE_PORT:-8790}"
ADB_BIN="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
SERIAL="${KELO_ADB_SERIAL:-$("$ADB_BIN" devices | awk 'NR>1 && $2=="device" {print $1; exit}')}"
[ -n "$SERIAL" ] || { echo "no Android device" >&2; exit 1; }
a() { "$ADB_BIN" -s "$SERIAL" "$@"; }
mkdir -p "$OUT"
TMPD="$(mktemp -d -t kelo-store-android)"
MOCK_PID=""; LOCKED=""

# ---- lock
if [ -n "${KELO_LOCK_DIR:-}" ]; then
  n=0
  until mkdir "$KELO_LOCK_DIR/DEVICE_LOCK" 2>/dev/null; do
    n=$((n + 1)); [ $n -gt 720 ] && { echo "DEVICE_LOCK still held after 60 min" >&2; exit 1; }
    sleep 5
  done
  echo "store-shots android $$ $(date)" > "$KELO_LOCK_DIR/DEVICE_LOCK/owner"; LOCKED=1
fi

# ---- what is put back at the end
OLD_SIZE="$(a shell wm size | awk '/Override/ {print $3}')"
OLD_DENSITY="$(a shell wm density | awk '/Override/ {print $3}')"
OLD_TZ="$(a shell getprop persist.sys.timezone | tr -d '\r')"
OLD_AUTO_TZ="$(a shell settings get global auto_time_zone | tr -d '\r')"
cleanup() {
  [ -n "$MOCK_PID" ] && { kill "$MOCK_PID" 2>/dev/null || true; wait "$MOCK_PID" 2>/dev/null || true; }
  a shell am broadcast -a com.android.systemui.demo -e command exit >/dev/null 2>&1 || true
  if [ -n "$OLD_SIZE" ]; then a shell wm size "$OLD_SIZE"; else a shell wm size reset; fi
  if [ -n "$OLD_DENSITY" ]; then a shell wm density "$OLD_DENSITY"; else a shell wm density reset; fi
  [ -n "$OLD_TZ" ] && a shell cmd alarm set-timezone "$OLD_TZ" >/dev/null 2>&1 || true
  [ -n "$OLD_AUTO_TZ" ] && a shell settings put global auto_time_zone "$OLD_AUTO_TZ" || true
  a shell cmd locale set-app-locales chat.kelo.app --locales "" >/dev/null 2>&1 || true
  a reverse --remove "tcp:$PORT" >/dev/null 2>&1 || true
  rm -rf "$TMPD"
  [ -n "$LOCKED" ] && rm -rf "$KELO_LOCK_DIR/DEVICE_LOCK"
  return 0
}
trap cleanup EXIT

[ -n "${KELO_APK:-}" ] && a install -r "$KELO_APK" >/dev/null
a shell pm clear chat.kelo.app >/dev/null   # a fresh app, signed in below to this mock only

# ---- the device: Galaxy S size, a morning time zone, a clean status bar
TZ_NAME="$(uv run python tools/store/store_mock.py --print-tz)"
echo "time zone: $TZ_NAME"
a shell wm size 1080x2340
a shell wm density 420
a shell settings put global auto_time_zone 0
a shell cmd alarm set-timezone "$TZ_NAME"
a shell settings put global sysui_demo_allowed 1
demo() { a shell am broadcast -a com.android.systemui.demo -e command "$@" >/dev/null; }
demo enter
demo battery -e level 100 -e plugged false
demo network -e wifi show -e level 4 -e fully true
demo network -e mobile hide
demo notifications -e visible false
a reverse "tcp:$PORT" "tcp:$PORT" >/dev/null
a shell pm grant chat.kelo.app android.permission.POST_NOTIFICATIONS >/dev/null 2>&1 || true

# ---- helpers
ui() {   # visible text / description with centre points: "x,y<TAB>'text' 'desc'"
  a shell uiautomator dump /sdcard/kelo-ui.xml >/dev/null 2>&1
  a shell cat /sdcard/kelo-ui.xml > "$TMPD/ui.xml"
  python3 - "$TMPD/ui.xml" <<'PY'
import re, sys
x = open(sys.argv[1], encoding="utf-8").read()
for m in re.finditer(r"<node [^>]*>", x):
    n = m.group(0)
    t = re.search(r' text="([^"]*)"', n).group(1); d = re.search(r'content-desc="([^"]*)"', n).group(1)
    b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
    if (t or d) and b:
        x1, y1, x2, y2 = map(int, b.groups()); print(f"{(x1+x2)//2},{(y1+y2)//2}\t{t!r} {d!r}")
PY
}
tapt() {   # tapt PATTERN [n]: tap the nth element whose text or description matches PATTERN
  local c; c="$(ui | grep -E -- "$1" | sed -n "${2:-1}p" | cut -f1)"
  [ -n "$c" ] || { echo "not found: $1" >&2; return 1; }
  a shell input tap "${c%,*}" "${c#*,}"
}
clock() { demo clock -e hhmm "$(TZ="$TZ_NAME" date +%H%M)"; }
shot() { clock; sleep 0.8; a exec-out screencap -p > "$OUT/$1.png"; echo "$OUT/$1.png"; }
start_app() { a shell am force-stop chat.kelo.app; sleep 0.5; a shell am start -n chat.kelo.app/.MainActivity >/dev/null; sleep 4; }
chat() { curl -s -X POST -d "{\"chat\":\"$1\"}" "127.0.0.1:$PORT/mock/chat" >/dev/null; start_app; }
type_ascii() {   # type into the focused field; Gboard may be on its Korean layout, so check and switch the language once if needed
  local want="$1"
  for _ in 1 2 3; do
    a shell input text "$want"; sleep 0.6
    ui | grep -qF "'$want'" && return 0
    a shell input keyevent KEYCODE_MOVE_END
    for _ in $(seq 1 60); do echo -n "67 "; done | xargs "$ADB_BIN" -s "$SERIAL" shell input keyevent
    a shell input keyevent 204; sleep 0.5   # the keyboard's language switch
  done
  return 1
}
sign_in() {
  ui | grep -qE "'(Connect with access key|접속 키로 연결하기)'" || return 0
  tapt "'(Connect with access key|접속 키로 연결하기)'"; sleep 1.5
  tapt "'https://kelo.example.com'"; sleep 0.5
  type_ascii "http://127.0.0.1:$PORT"
  a shell input keyevent 4; sleep 0.8
  ui >/dev/null
  local box; box="$(grep -o '<node[^>]*password="true"[^>]*>' "$TMPD/ui.xml" | grep -o 'bounds="[^"]*"' | head -1)"
  local y1 y2; y1="$(echo "$box" | sed -E 's/.*\[[0-9]+,([0-9]+)\]\[.*/\1/')"; y2="$(echo "$box" | sed -E 's/.*\]\[[0-9]+,([0-9]+)\].*/\1/')"
  a shell input tap 540 $(( (y1 + y2) / 2 )); sleep 0.5
  a shell input text "mock-desktop-token"; a shell input keyevent 4; sleep 0.8
  tapt "^[0-9]+,[0-9]+	'(Connect|연결)' ''"; sleep 5
}

for L in "${LANGS[@]}"; do
  [ -n "$MOCK_PID" ] && { kill "$MOCK_PID" 2>/dev/null || true; wait "$MOCK_PID" 2>/dev/null || true; }
  uv run python tools/store/store_mock.py --lang "$L" --port "$PORT" --tz "$TZ_NAME" --brief 07:00 > "$TMPD/mock-$L.log" 2>&1 &
  MOCK_PID=$!
  for _ in $(seq 1 30); do curl -s -o /dev/null --max-time 1 "127.0.0.1:$PORT/healthz" && break; sleep 0.5; done
  a shell cmd locale set-app-locales chat.kelo.app --locales "$([ "$L" = ko ] && echo ko-KR || echo en-US)"
  start_app
  sign_in

  # 04: this morning's briefing
  chat briefing
  shot "$L-04"
  # 01: a task handed off earlier and its report
  chat handoff
  shot "$L-01"
  # 02: a ticket booked from the chat, its order card waiting (the text is shared into the composer, then sent)
  chat empty
  ASK="$([ "$L" = ko ] && echo '17일 아침 7시 부산 가는 KTX 예매해 줘' || echo 'Book the 6:05 AM train to Boston on the 17th')"
  a shell "am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT '$ASK' -n chat.kelo.app/.MainActivity" >/dev/null 2>&1
  sleep 1.5
  tapt "'' '(Send|보내기)'" || a shell input tap 968 1987
  sleep 3
  tapt "'(Later|나중에)'" 2>/dev/null || true
  sleep 7
  shot "$L-02"
  tapt "'(Allow|허용)'"; sleep 3   # answered, so the character no longer waits on the next pages
  # 03: Work page (the character) → Live view
  a shell input tap 540 235; sleep 2.5
  tapt "'(Live view|실시간 보기)'"; sleep 4
  shot "$L-03"
  a shell input keyevent 4; sleep 1; a shell input keyevent 4; sleep 1
  # 05: a goal's page
  tapt "'(Goals|다짐)' ''"; sleep 2
  tapt "'(Read 2 books a month|한 달에 책 2권 읽기)'"; sleep 3
  shot "$L-05"
  a shell input keyevent 4; sleep 1
  # 06: connectors
  tapt "'(Connectors|커넥터)' ''"; sleep 4
  shot "$L-06"
  # 07: My → Where your data lives
  tapt "'(My|마이)' ''"; sleep 2
  for _ in 1 2 3; do ui | grep -qE "'(Where your data lives|내 정보가 머무는 곳)'" && break; a shell input swipe 540 1700 540 900 300; sleep 0.8; done
  tapt "'(Where your data lives|내 정보가 머무는 곳)'"; sleep 2.5
  shot "$L-07"
  a shell input keyevent 4; sleep 1
  # 08: My → Agent (the tone)
  for _ in 1 2 3; do a shell input swipe 540 700 540 1800 300; sleep 0.5; done
  tapt "'(Agent|에이전트)' ''" 2; sleep 2.5
  shot "$L-08"
  a shell input keyevent 4; sleep 1
  tapt "'(Chat|채팅)' ''" || true
done
echo "done: $OUT"
