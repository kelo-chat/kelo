#!/bin/bash
# The Mac server bundle on a real Lima VM, at user level only (card p-mac-install-server, brief part B).
# Nothing here needs root or a LaunchDaemon, and nothing touches colima, Homebrew's lima, ~/.lima, ~/.colima or the docker context.
#   - its own LIMA_HOME under /private/tmp/kmt-<pid> (short path: Unix socket limit), its own instance "kelo"
#   - ports 18080 (server) and 18081 (setup page) only; the VM's other ports must stay unreachable from the Mac
#   - the bundle's own limactl (a dev build: --no-sign, then ad-hoc signed with the same entitlements in the TEST copy only)
#   - the cleanup runs on any exit (trap): the VM is deleted, the hostagent PID from ha.pid is ended only if still ours, the folder removed
#
#   tools/test/mac-server-vm-test.sh [--zip PATH]     --zip: use this bundle instead of building a --no-sign one
# Prints timings and results. It never prints the setup link or any token (the setup page's first line is checked, not shown).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ZIP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --zip) ZIP="${2:?--zip needs a path}"; shift ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 64 ;;
  esac
  shift
done

SERVER=18080
SETUP=18081
KMT="/private/tmp/kmt-$$"
export LIMA_HOME="$KMT/home"
PASS=0; FAIL=0
RESULTS=()

say()  { printf '==> %s\n' "$*"; }
ok()   { PASS=$((PASS + 1)); RESULTS+=("PASS  $*"); printf '    PASS  %s\n' "$*"; }
bad()  { FAIL=$((FAIL + 1)); RESULTS+=("FAIL  $*"); printf '    FAIL  %s\n' "$*"; }
clock() { date +%s; }

cleanup() {
  set +e
  say "cleanup"
  local bundle="$KMT/bundle/kelo-server"
  if [ -x "$bundle/bin/limactl" ] && [ -d "$LIMA_HOME/kelo" ]; then
    KELO_TEST_BUNDLE="$bundle" LIMA_HOME="$LIMA_HOME" "$bundle/bin/kelo-server-setup" remove-data >/dev/null 2>&1 || true
  fi
  if [ -n "${SETUP_PID:-}" ] && kill -0 "$SETUP_PID" 2>/dev/null; then kill "$SETUP_PID" 2>/dev/null || true; fi
  # the hostagent of THIS instance only: the PID file is inside our LIMA_HOME
  if [ -f "$LIMA_HOME/kelo/ha.pid" ]; then
    hpid="$(cat "$LIMA_HOME/kelo/ha.pid" 2>/dev/null || true)"
    if [ -n "$hpid" ] && kill -0 "$hpid" 2>/dev/null; then
      ps -o command= -p "$hpid" 2>/dev/null | grep -q "$KMT" && kill "$hpid" 2>/dev/null || true
    fi
  fi
  local left
  left="$(ps -U "$USER" -o pid=,command= 2>/dev/null | grep -F "$KMT" | grep -v grep || true)"
  if [ -n "$left" ]; then bad "processes still use $KMT after the cleanup"; else ok "no process left in $KMT"; fi
  rm -rf "$KMT"
  if [ "$(colima list 2>/dev/null || true)" = "$COLIMA_BEFORE" ]; then
    ok "colima list is unchanged (read-only comparison of the full listing)"
  else
    bad "colima list changed"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- 1. preconditions -------------------------------------------------------------------------------------------------------
say "preconditions"
free_gb="$(df -g /System/Volumes/Data 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "$free_gb" ] && [ "$free_gb" -ge 40 ]; then ok "disk: ${free_gb} GB free"; else echo "need 40 GB free, have ${free_gb:-?}" >&2; exit 1; fi
for port in $SERVER $SETUP; do
  if /usr/bin/python3 -I -c "import socket,sys; s=socket.socket(); s.bind(('127.0.0.1', $port)); s.close()" 2>/dev/null; then
    ok "port $port is free (bind test, nothing connected)"
  else
    bad "port $port is in use"; exit 1
  fi
done
COLIMA_BEFORE="$(colima list 2>/dev/null || true)"
mkdir -p "$KMT/bundle" "$LIMA_HOME"

# --- 2. the bundle ---------------------------------------------------------------------------------------------------------
say "bundle"
if [ -z "$ZIP" ]; then
  (cd "$REPO" && perl -e 'alarm 900; exec @ARGV' tools/build/mac-server-bundle.sh --no-sign >"$KMT/build.log" 2>&1) || { bad "build --no-sign"; exit 1; }
  ZIP="$(ls -t "$REPO"/dist/kelo-server-*-mac-arm64.zip | head -1)"
fi
ditto -x -k "$ZIP" "$KMT/bundle"
BUNDLE="$KMT/bundle/kelo-server"
ok "unpacked $(basename "$ZIP") ($(du -sh "$BUNDLE" | awk '{print $1}'))"
# a signed bundle (Developer ID, from --notarize) keeps its own signature: the test runs the bundle as shipped.
# An unsigned dev bundle gets an ad-hoc signature with the same entitlements (vz needs them) in the TEST copy only.
sign_info="$(codesign -dvv "$BUNDLE/bin/limactl" 2>&1 || true)"
if [[ "$sign_info" == *"Authority=Developer ID Application:"* ]]; then
  codesign --verify --strict "$BUNDLE/bin/limactl" && ok "limactl keeps its Developer ID signature (the signed bundle is tested as shipped)" || bad "the Developer ID signature of limactl does not verify"
else
  codesign --force --sign - --entitlements "$REPO/packaging/mac/limactl.entitlements" "$BUNDLE/bin/limactl" 2>/dev/null && ok "limactl ad-hoc signed with the bundle's entitlements (test copy)" || bad "ad-hoc signing of the test copy"
fi
"$BUNDLE/bin/limactl" --version >/dev/null && ok "limactl runs: $("$BUNDLE/bin/limactl" --version)"
export KELO_TEST_BUNDLE="$BUNDLE"
export KELO_TEST_IMAGE="$BUNDLE/images/debian-12-genericcloud-arm64.qcow2"
export KELO_SERVER_PORT=$SERVER KELO_SETUP_PORT=$SETUP
SETUPCMD="$BUNDLE/bin/kelo-server-setup"

# --- 3. the user stage: create, start, provision, install -------------------------------------------------------------------
say "prepare (create, start, provision, install Kelo in the VM)"
t0=$(clock)
"$SETUPCMD" prepare --memory 4 --cpus 2 --no-service > "$KMT/prepare.out" 2>&1 && prep_rc=0 || prep_rc=$?
t1=$(clock)
if [ "$prep_rc" = 0 ]; then ok "prepare exits 0 in $((t1 - t0)) s"; else bad "prepare exits $prep_rc after $((t1 - t0)) s (see the setup log under ~/Library/Logs/Kelo Server)"; fi
echo "    disk of the VM: $(du -sh "$LIMA_HOME/kelo" 2>/dev/null | awk '{print $1}') in $LIMA_HOME/kelo"

say "status (polled: ready once the server answers, up to 3 min)"
st=""
for _ in $(seq 1 36); do
  st="$("$SETUPCMD" status)"
  [ "$st" = ready ] && break
  sleep 5
done
echo "    status: $st"
[ "$st" = ready ] && ok "status is ready" || bad "status is $st"

say "server answers on $SERVER"
healthy=0
for _ in $(seq 1 120); do
  if curl -fsS --max-time 5 "http://127.0.0.1:$SERVER/healthz" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 5
done
[ "$healthy" = 1 ] && ok "/healthz on $SERVER answers" || bad "/healthz on $SERVER did not answer in 10 min"

# --- 4. the setup page ------------------------------------------------------------------------------------------------------
say "setup page"
"$SETUPCMD" setup-page > "$KMT/setup-page.out" 2>/dev/null &
SETUP_PID=$!
first=""
for _ in $(seq 1 60); do
  first="$(head -n 1 "$KMT/setup-page.out" 2>/dev/null || true)"
  [ -n "$first" ] && break
  sleep 1
done
if printf '%s\n' "$first" | grep -Eq "^http://127\\.0\\.0\\.1:$SETUP/#t=[A-Za-z0-9_-]{16,}\$"; then ok "the first line is the setup link on 127.0.0.1:$SETUP (URL OK)"; else bad "the first line is not the setup link"; fi
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$SETUP/" || true)"
[ "$code" = 200 ] && ok "the setup page answers 200 on $SETUP" || bad "the setup page answers $code"
kill "$SETUP_PID" 2>/dev/null || true; wait "$SETUP_PID" 2>/dev/null || true; unset SETUP_PID
rm -f "$KMT/setup-page.out"

# --- 5. isolation: the VM's other ports are not reachable from the Mac ------------------------------------------------------
say "port isolation (servers inside the VM on 18443, 18444, 18445)"
"$BUNDLE/bin/limactl" shell kelo -- sh -c 'nohup python3 -m http.server 18443 --bind 0.0.0.0 >/dev/null 2>&1 & nohup python3 -m http.server 18444 --bind :: >/dev/null 2>&1 & nohup python3 -m http.server 18445 --bind 127.0.0.1 >/dev/null 2>&1 & sleep 2' >/dev/null 2>&1 || true
sleep 3
for p in 18443 18444 18445; do
  if nc -z -G 2 127.0.0.1 "$p" 2>/dev/null; then bad "the Mac reaches VM port $p"; else ok "the Mac does not reach VM port $p"; fi
done

# --- 6. no files of the Mac in the VM ---------------------------------------------------------------------------------------
say "no mounts"
if "$BUNDLE/bin/limactl" shell kelo -- ls /Users >/dev/null 2>&1; then bad "the VM sees /Users"; else ok "the VM does not see the Mac's /Users"; fi

# --- 7. Docker in the VM ---------------------------------------------------------------------------------------------------
say "Docker in the VM"
"$BUNDLE/bin/limactl" shell kelo -- docker info >/dev/null 2>&1 && ok "docker info works in the VM" || bad "docker info fails in the VM"
"$BUNDLE/bin/limactl" shell kelo -- docker compose -p kelo ps --format '{{.Service}} {{.State}} {{.Health}}' > "$KMT/ps.out" 2>&1 || true
echo "    $(tr '\n' ';' < "$KMT/ps.out" | sed 's/  */ /g')"
if grep -q running "$KMT/ps.out"; then ok "compose services are running"; else bad "no compose service is running"; fi
for f in "$HOME/.docker/config.json"; do
  [ -f "$f" ] && echo "    ~/.docker/config.json sha256 $(shasum -a 256 "$f" | awk '{print $1}')" || echo "    ~/.docker/config.json: absent"
done

# --- 8. update: the instance is kept, the provisioning marker moves, the data stays ------------------------------------------
say "update to 0.0.0-test+1 with PROVISION_VERSION bumped"
vol="$("$BUNDLE/bin/limactl" shell kelo -- docker volume ls --format '{{.Name}}' 2>/dev/null | grep -m1 'server-state' || true)"
if [ -n "$vol" ]; then
  "$BUNDLE/bin/limactl" shell kelo -- docker run --rm -v "$vol:/d" alpine sh -c 'echo marker > /d/kelo-test-marker' >/dev/null 2>&1 || true
  ok "a marker written into volume $vol before the update"
else
  bad "no server-state volume found"
fi
cp -c -R "$BUNDLE" "$KMT/bundle2" 2>/dev/null || cp -R "$BUNDLE" "$KMT/bundle2"
echo "0.0.0-test+1" > "$KMT/bundle2/version.txt"
sed -i '' 's/^PROVISION_VERSION=1$/PROVISION_VERSION=2/' "$KMT/bundle2/share/kelo/provision.sh"
t2=$(clock)
KELO_TEST_BUNDLE="$KMT/bundle2" "$SETUPCMD" prepare --memory 4 --cpus 2 --no-service >"$KMT/update.out" 2>&1 && upd_rc=0 || upd_rc=$?
t3=$(clock)
if [ "$upd_rc" = 0 ]; then ok "update prepare exits 0 in $((t3 - t2)) s"; else bad "update prepare exits $upd_rc"; fi
marker="$("$BUNDLE/bin/limactl" shell kelo -- cat /var/lib/kelo/provisioned 2>/dev/null || true)"
[ "$marker" = 2 ] && ok "the provisioning marker is 2" || bad "the provisioning marker is '$marker'"
if [ -n "$vol" ] && "$BUNDLE/bin/limactl" shell kelo -- docker run --rm -v "$vol:/d" alpine cat /d/kelo-test-marker >/dev/null 2>&1; then ok "the data volume kept its marker"; else bad "the marker is gone"; fi
grep -q "Kelo is already installed" "$KMT/update.out" 2>/dev/null && ok "Kelo was not installed again" || bad "Kelo was reinstalled or the log has no 'already installed' line"

# --- 9. restart: the containers come back without help ---------------------------------------------------------------------
say "restart the VM"
"$BUNDLE/bin/limactl" stop kelo >/dev/null 2>&1 || true
"$BUNDLE/bin/limactl" start --tty=false kelo >"$KMT/restart.out" 2>&1 || true
back=0
for _ in $(seq 1 120); do
  if curl -fsS --max-time 5 "http://127.0.0.1:$SERVER/healthz" >/dev/null 2>&1; then back=1; break; fi
  sleep 5
done
[ "$back" = 1 ] && ok "after a restart the server answers again, without help" || bad "the server did not come back after a restart"

# --- 10. results (the cleanup runs in the trap) -----------------------------------------------------------------------------
say "results: $PASS passed, $FAIL failed"
printf '    %s\n' "${RESULTS[@]}"
[ "$FAIL" = 0 ]
