#!/bin/bash
# Live eval of the checked-out branch in a throwaway container that borrows the main instance's key container (docs/browser-workers.md).
#
#   tools/eval/live-in-container.sh [--runs N] [--scenario NAME]... [--instance-container NAME] [--keep-image] [--no-build] [--show] [--dry-run]
#
# What it does (nothing of the running instance is stopped, changed or written to):
#   1. builds docker/server.Dockerfile from this checkout as kelotest-eval;
#   2. starts kelotest-eval-<pid> from it on the main instance's keyring network (the "keyring" name resolves there) with the variable
#      NAMES the server container has for the key container and the model call (KEYRING_URL, KEYRING_TOKEN, ASSISTANT_SECRET_KEY,
#      ASSISTANT_TOKEN, ASSISTANT_UNOFFICIAL_PROVIDERS); values go from `docker inspect` straight into the container, never to the screen
#      or to a file on this Mac (the env file lives in the container's tmpfs);
#   3. copies the database with SQLite's backup API run inside the server container (read-only source, serialized in memory, streamed
#      through stdout as a tar together with master.key) into the throwaway container's tmpfs. Nothing is written into /state;
#   4. runs `assistant eval live --scenario S --yes` N times per scenario (default: every live scenario, 3 runs);
#   5. removes the container (and its tmpfs with the DB copy) and the kelotest-eval tag, also on failure or Ctrl-C.
# The eval never writes to the key container beyond sending the ring key (eval_live.read_only_keyring), and refuses OpenAI models.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER=assistant-server-server-1
RUNS=3 SCEN=() KEEP=0 BUILD=1 DRY=0 SHOW=
while [ $# -gt 0 ]; do
  case "$1" in
    --runs) RUNS="${2:?}"; shift ;;
    --scenario) SCEN+=("${2:?}"); shift ;;
    --instance-container) SERVER="${2:?}"; shift ;;
    --keep-image) KEEP=1 ;;
    --no-build) BUILD=0 ;;
    --dry-run) DRY=1 ;;
    --show) SHOW=1 ;;   # also print each run's last tool results and reply (fixture texts; for debugging a failure)
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 1 ;;
  esac; shift
done
[ ${#SCEN[@]} -eq 0 ] && SCEN=(checkout-job-reaches-review checkout-cart-differs-asks-back checkout-second-job-refused checkout-split-off-chat-pays)
IMAGE=kelotest-eval NAME="kelotest-eval-$$"
say() { printf '==> %s\n' "$*" >&2; }
[ "$DRY" = 1 ] && { say "dry run: would build $IMAGE, start $NAME next to $SERVER, copy its DB over stdout, run ${SCEN[*]} x $RUNS"; exit 0; }

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 && say "removed container $NAME (and the DB copy in its tmpfs)" || true
  if [ "$KEEP" = 0 ]; then docker rmi "$IMAGE" >/dev/null 2>&1 && say "removed image tag $IMAGE" || true; fi
}
trap cleanup EXIT INT TERM

NET=$(docker inspect "$SERVER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}' | grep 'keyring-net$' | head -1)
[ -n "$NET" ] || { echo "no keyring network on $SERVER" >&2; exit 1; }
say "keyring network: $NET"
if [ "$BUILD" = 1 ]; then say "building $IMAGE"; docker build -q -f "$REPO/docker/server.Dockerfile" -t "$IMAGE" "$REPO" >/dev/null; fi

# names only come from here; the values travel from docker inspect into the container
env_lines() { docker inspect "$SERVER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(KEYRING_URL|KEYRING_TOKEN|ASSISTANT_SECRET_KEY|ASSISTANT_TOKEN|ASSISTANT_UNOFFICIAL_PROVIDERS)=' || true; }

docker run -d --name "$NAME" --network "$NET" --tmpfs /work:rw,mode=1777,exec --memory 1g --cap-drop ALL \
  --security-opt no-new-privileges --entrypoint sleep "$IMAGE" 7200 >/dev/null
docker cp "$REPO/server/tests" "$NAME:/app/server/tests"
{ env_lines; echo "ASSISTANT_HOME=/work/home"; } | docker exec -i "$NAME" sh -c 'umask 077; cat > /work/env; mkdir -p /work/home'

say "copying the database (backup API, in memory, through stdout)"
docker exec "$SERVER" python3 -c '
import io, sqlite3, sys, tarfile
src = sqlite3.connect("file:/state/assistant.db?mode=ro", uri=True)
mem = sqlite3.connect(":memory:")
src.backup(mem); src.close()
data = mem.serialize()
out = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
def add(name, blob):
    i = tarfile.TarInfo(name); i.size = len(blob); i.mode = 0o600; out.addfile(i, io.BytesIO(blob))
add("assistant.db", data)
try:
    add("master.key", open("/state/master.key", "rb").read())
except FileNotFoundError:
    pass
out.close()
' | docker exec -i "$NAME" tar -x -C /work/home
docker exec "$NAME" sh -c 'ls /work/home | tr "\n" " "; echo' >&2

fail=0
for s in "${SCEN[@]}"; do
  for i in $(seq 1 "$RUNS"); do
    say "scenario $s run $i"
    docker exec -w /app ${SHOW:+-e KELO_EVAL_SHOW=1} "$NAME" assistant eval live --env-file /work/env --scenario "$s" --yes || fail=1
  done
done
exit $fail
