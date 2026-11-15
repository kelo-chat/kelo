# Shared helpers for tools/build/*.sh (sourced, not run). Works with macOS's own bash 3.2.
# Nothing here prints a secret: keys, passwords and tokens are only ever passed as file paths or environment variables.

set -euo pipefail

BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$BUILD_DIR/../.." && pwd)"
MIN_FREE_GB="${KELO_MIN_FREE_GB:-8}"
_TMP_BASE="${TMPDIR:-/tmp}"; _TMP_BASE="${_TMP_BASE%/}"
LOGS="${KELO_BUILD_LOGS:-$_TMP_BASE/kelo-build-logs}"   # one log per step, overwritten by the next run
mkdir -p "$LOGS"
DRY_RUN=0

_CLEAN_PATHS=()     # temp folders removed on exit (also on failure / Ctrl-C)
_CLEAN_GLOBS=()     # files matching these are removed on exit
_HELD_LOCKS=()      # lock folders this run took (KELO_LOCK_DIR), released on exit
_EXIT_HOOKS=()      # extra commands run on exit (e.g. stop the mock server this script started)

log()  { printf '\033[1m==> %s\033[0m\n' "$*" >&2; }
note() { printf '    %s\n' "$*" >&2; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Prints a command instead of running it with --dry-run; the arguments are shown quoted so it can be pasted.
run_or_print() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '    [dry-run] %s\n' "$(printf '%q ' "$@")" >&2
  else
    "$@"
  fi
}

_on_exit() {
  local rc=$?
  local h p
  for h in ${_EXIT_HOOKS[@]+"${_EXIT_HOOKS[@]}"}; do eval "$h" || true; done
  for p in ${_CLEAN_PATHS[@]+"${_CLEAN_PATHS[@]}"}; do
    [ -n "$p" ] && [ -e "$p" ] && rm -rf "$p"
  done
  for p in ${_CLEAN_GLOBS[@]+"${_CLEAN_GLOBS[@]}"}; do
    # shellcheck disable=SC2086
    rm -rf $p 2>/dev/null || true
  done
  for p in ${_HELD_LOCKS[@]+"${_HELD_LOCKS[@]}"}; do
    rm -rf "$p"
    note "released lock $(basename "$p")"
  done
  exit $rc
}
trap _on_exit EXIT
trap 'exit 130' INT TERM

on_exit() { _EXIT_HOOKS+=("$1"); }

# make_temp NAME VAR: a fresh temp folder in VAR, deleted when the script ends (also on failure). Not called as $(...): a
# subshell would forget the folder and leave it behind.
make_temp() {
  local d
  d="$(mktemp -d "$_TMP_BASE/kelo-build-$1.XXXXXX")"
  _CLEAN_PATHS+=("$d")
  # SwiftPM leaves <TMPDIR>/<scratch path with _>.lock files next to it
  _CLEAN_GLOBS+=("$_TMP_BASE/*$(basename "$d")*")
  printf -v "$2" '%s' "$d"
}

# Refuses to build when the data volume has less than MIN_FREE_GB free (a full disk broke builds before).
need_disk() {
  local vol=/System/Volumes/Data
  [ -d "$vol" ] || vol=/
  local free_kb
  free_kb="$(df -k "$vol" | awk 'NR==2 {print $4}')"
  local free_gb=$((free_kb / 1024 / 1024))
  if [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
    die "only ${free_gb} GB free on $vol (need ${MIN_FREE_GB} GB). Free some space first (see tools/build/README.md, 'Disk')."
  fi
  note "disk: ${free_gb} GB free on $vol"
}

# with_timeout SECONDS cmd... : runs cmd in its own process group and kills that whole group (only it) when time runs out.
# Exit code 124 on timeout, else the command's own. stdin is closed so nothing waits for a terminal.
with_timeout() {
  local secs="$1"; shift
  perl -e '
    my $secs = shift;
    my $pid = fork() // die "fork: $!";
    if ($pid == 0) { setpgrp(0, 0); open(STDIN, "<", "/dev/null"); exec { $ARGV[0] } @ARGV or exit 127; }
    my $stop = sub { my ($code, $why) = @_; kill "TERM", -$pid; for (1..30) { last if waitpid($pid, 1) > 0; sleep 1 } kill "KILL", -$pid;
                     print STDERR "$why\n" if $why; exit $code; };
    $SIG{ALRM} = sub { $stop->(124, "timed out after ${secs}s: $ARGV[0]") };
    $SIG{INT} = $SIG{TERM} = sub { $stop->(130, "") };
    alarm $secs;
    waitpid($pid, 0);
    my $st = $?;
    exit(($st & 127) ? 128 + ($st & 127) : $st >> 8);
  ' "$secs" "$@"
}

# Runs a long step with a timeout, its output going to LOG; on failure prints the last lines (secrets redacted) and stops.
step() {
  local name="$1" secs="$2" logf="$3"; shift 3
  if [ "$DRY_RUN" = 1 ]; then
    printf '    [dry-run] %s  (timeout %s min)\n' "$(printf '%q ' "$@")" "$((secs / 60))" >&2
    return 0
  fi
  log "$name (timeout $((secs / 60)) min, log: $logf)"
  local start rc=0
  start=$(date +%s)
  with_timeout "$secs" "$@" >"$logf" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '\n--- last lines of %s ---\n' "$logf" >&2
    tail -n 40 "$logf" | redact >&2
    [ "$rc" = 124 ] && die "$name timed out after $((secs / 60)) min"
    die "$name failed (exit $rc)"
  fi
  note "$name ok ($(( $(date +%s) - start ))s)"
}

# soft_step: like step, but a failure is recorded in FAILED_STEPS and the script goes on (report with finish_soft).
FAILED_STEPS=()
soft_step() {
  if ( step "$@" ); then return 0; fi
  FAILED_STEPS+=("$1")
}
finish_soft() {
  [ ${#FAILED_STEPS[@]} -eq 0 ] && return 0
  local f; for f in "${FAILED_STEPS[@]}"; do warn "failed: $f"; done
  exit 1
}

# Hides the values named in REDACT_VALUES (space separated) from text shown on screen.
REDACT_VALUES=""
redact() {
  if [ -z "$REDACT_VALUES" ]; then cat; return; fi
  local args=() v
  for v in $REDACT_VALUES; do [ -n "$v" ] && args+=(-e "s|$v|<redacted>|g"); done
  sed ${args[@]+"${args[@]}"}
}

# Optional cross-agent locks: with KELO_LOCK_DIR set, take_lock NAME... takes <dir>/<NAME> for every name (mkdir is atomic),
# all or none (so it never sits on one lock while waiting for another), waits at most KELO_LOCK_WAIT seconds (default 3600) and
# releases them on exit. Without KELO_LOCK_DIR nothing is locked.
take_lock() {
  [ -n "${KELO_LOCK_DIR:-}" ] || return 0
  local wait="${KELO_LOCK_WAIT:-3600}" i name got busy
  if [ "$DRY_RUN" = 1 ]; then note "[dry-run] would take lock(s) $* in $KELO_LOCK_DIR"; return 0; fi
  for i in $(seq 0 $((wait / 60))); do
    got=() busy=""
    for name in "$@"; do
      if mkdir "$KELO_LOCK_DIR/$name" 2>/dev/null; then
        echo "tools/build $(basename "$0") pid $$ $(date)" >"$KELO_LOCK_DIR/$name/owner"
        got+=("$KELO_LOCK_DIR/$name")
      else
        busy="$name"; break
      fi
    done
    if [ -z "$busy" ]; then
      _HELD_LOCKS+=(${got[@]+"${got[@]}"})
      note "took lock(s) $*"
      return 0
    fi
    for name in ${got[@]+"${got[@]}"}; do rm -rf "$name"; done   # give back what was taken, wait, try again
    [ "$i" = 0 ] && note "lock $busy is held by: $(cat "$KELO_LOCK_DIR/$busy/owner" 2>/dev/null || echo '?') - waiting (max $((wait / 60)) min)"
    sleep 60
  done
  die "lock $busy still held after $((wait / 60)) min by: $(cat "$KELO_LOCK_DIR/$busy/owner" 2>/dev/null || echo '?')"
}

release_lock() {
  [ -n "${KELO_LOCK_DIR:-}" ] || return 0
  local dir="$KELO_LOCK_DIR/$1" kept=() p
  for p in ${_HELD_LOCKS[@]+"${_HELD_LOCKS[@]}"}; do
    if [ "$p" = "$dir" ]; then rm -rf "$dir"; note "released lock $1"; else kept+=("$p"); fi
  done
  _HELD_LOCKS=(${kept[@]+"${kept[@]}"})
}

# A free TCP port on 127.0.0.1 (never 8765: another service on this Mac uses it).
free_port() {
  python3 -I -c '
import socket
while True:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    if p != 8765: print(p); break'
}

use_jdk17() {
  local jdk="${KELO_JAVA_HOME:-/opt/homebrew/opt/openjdk@17}"
  [ -x "$jdk/bin/java" ] || die "JDK 17 not found at $jdk (brew install openjdk@17, or set KELO_JAVA_HOME)"
  export JAVA_HOME="$jdk"
  export PATH="$JAVA_HOME/bin:$PATH"
}

human_size() { du -h "$1" 2>/dev/null | awk '{print $1}'; }
