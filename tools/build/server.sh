#!/bin/bash
# Kelo server: build the image and run the test suite. Deploying is printed, never run. See tools/build/README.md.
#
#   tools/build/server.sh build             docker build -t assistant:local . (the image compose.yml runs)
#   tools/build/server.sh test [-- ARGS]    the server tests (uv run pytest; ARGS go to pytest, e.g. -- tests/test_updates.py -q)
#   tools/build/server.sh all               build, then test (default)
#
# Options:
#   --tag NAME          image tag (default assistant:local). Another tag leaves the running copies' tag untouched
#   --browser B         chromium (default) or chrome, as compose.yml's BROWSER build arg
#   --remove-image      after a successful build, remove the tag again (a build check that keeps no image)
#   --dry-run           print the commands and stop
#
# At the end it prints the command that deploys the main copy (assistant update: backup, rebuild, restart, health check, automatic
# rollback). It never runs it.
source "$(dirname "$0")/lib.sh"

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

MODE=all
case "${1:-}" in build|test|all) MODE="$1"; shift ;; -h|--help|help) usage 0 ;; '') ;; -*) ;; *) die "unknown command '$1' (build, test or all)" ;; esac
TAG=assistant:local BROWSER=chromium REMOVE=0 PYTEST_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:?}"; shift ;;
    --browser) BROWSER="${2:?}"; shift; case "$BROWSER" in chromium|chrome) ;; *) die "--browser chromium or chrome" ;; esac ;;
    --remove-image) REMOVE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --) shift; PYTEST_ARGS=("$@"); break ;;
    -h|--help) usage 0 ;;
    *) die "unknown option $1 (pytest arguments go after --)" ;;
  esac
  shift
done

cd "$REPO"
[ "$DRY_RUN" = 1 ] || need_disk
VERSION="$(python3 -c 'import json; print(json.load(open("version.json"))["version"])')"   # version.json (docs/versioning.md)
BUILD="$(python3 -c 'import json; print(json.load(open("version.json"))["build"])')"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
DIRTY="$([ -n "$(git status --porcelain 2>/dev/null | head -1)" ] && echo ' + local changes' || true)"
log "Kelo server $VERSION build $BUILD ($COMMIT$DIRTY)"

if [ "$MODE" != test ]; then
  if [ "$DRY_RUN" = 0 ]; then
    command -v docker >/dev/null || die "docker not found"
    with_timeout 30 docker info >/dev/null 2>&1 || die "Docker is not running (on this Mac: colima; do not restart it without asking the owner)"
  fi
  step "docker build -t $TAG" "${KELO_DOCKER_TIMEOUT:-3600}" "$LOGS/server-docker.log" \
    docker build --build-arg "BROWSER=$BROWSER" --build-arg "KELO_VERSION=$VERSION" -t "$TAG" .
  if [ "$DRY_RUN" = 0 ]; then
    note "image: $TAG $(docker image inspect "$TAG" --format '{{.Id}} {{.Size}}' | awk '{printf "%s, %.0f MB", substr($1, 8, 12), $2 / 1e6}')"
    if [ "$REMOVE" = 1 ]; then
      docker rmi "$TAG" >/dev/null && note "removed the tag $TAG again (--remove-image)"
    fi
  fi
fi

if [ "$MODE" != build ]; then
  command -v uv >/dev/null || die "uv not found (brew install uv)"
  make_temp server TMP   # pytest's temp files go here and are deleted at the end
  step "server tests (uv run pytest)" "${KELO_PYTEST_TIMEOUT:-2700}" "$LOGS/server-pytest.log" \
    uv run pytest -q -p no:cacheprovider --basetemp="$TMP/pytest" ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
  [ "$DRY_RUN" = 1 ] || note "$(grep -E '[0-9]+ (passed|failed)' "$LOGS/server-pytest.log" | tail -1 | sed 's/=//g; s/^ *//')"
fi

log "done"
note "deploy to the main copy (NOT run by this script; it backs up, rebuilds server + work room, restarts, checks health and"
note "rolls back by itself on failure):"
note "  cd $REPO && uv run assistant update"
note "  (with --no-build it reuses the images already built here instead of building again; extra copies: uv run assistant instance update)"
note "then check: curl -fsS https://kelo.chat/healthz  (and the copy's own address)"
