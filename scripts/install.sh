#!/usr/bin/env bash
# Kelo installer for macOS and Linux. (Windows: scripts/install.ps1)
#
#   curl -fsSL https://kelo.chat/install.sh | bash                 install, or update when Kelo is already there
#   curl -fsSL https://kelo.chat/install.sh | bash -s -- --yes     no questions (defaults everywhere)
#   bash install.sh --uninstall                                          remove Kelo (your data is kept unless --delete-data)
#
# What it does, in order: checks this computer (OS, CPU type), checks Docker and Compose (and helps get them), installs `uv`
# if it is missing, puts the Kelo files in ~/kelo, prepares Python with uv, adds a `kelo` command, pulls Kelo's official images
# (signed; checked before use), and opens the guided setup, which starts Kelo and shows a QR code for your phone.
# Running it again is an update: your .env, chats, keys and phones stay as they are.
#
# Options (each also as an environment variable):
#   -y, --yes          answer every question with its default (KELO_YES=1); never deletes data
#   --add NAME         add another Kelo server copy without asking (KELO_ADD)
#   --dir DIR          where Kelo lives (KELO_HOME, default ~/kelo)
#   --source SRC       where to get the Kelo files: a folder, a .tar.gz URL or a git URL (KELO_SOURCE). A custom source is
#                      built here, since its images are not the official ones, unless --from-release is given too
#   --from-release     the default: pull the signed official images instead of building them here (KELO_FROM_RELEASE=1).
#                      If the pull fails they are built here instead, with a warning. The Claude/agy containers are always
#                      built here (their CLIs are never published)
#   --build-here       build the images on this computer instead (KELO_FROM_RELEASE=0); slow, and it can fail on small machines
#   --no-start         save the answers but do not start the containers (KELO_NO_START=1)
#   --auto-update      let Kelo update the server by itself at night (KELO_AUTO_UPDATE=1); --no-auto-update: only when the owner
#                      presses Update in the app (the default; asked when there is a terminal)
#   --uninstall        stop and remove Kelo; add --delete-data to also delete chats, keys and files for good
#   -h, --help         this text
# NO_COLOR=1 turns colours off. Prompts are read from the terminal even when this script arrives through a pipe.

set -euo pipefail

KELO_HOME="${KELO_HOME:-$HOME/kelo}"
KELO_SOURCE="${KELO_SOURCE:-}"
YES="${KELO_YES:-0}"
START=1; [ "${KELO_NO_START:-0}" = "1" ] && START=0
FROM_RELEASE="${KELO_FROM_RELEASE:-}"   # 1 = official images, 0 = build here; empty = decided after the options
AUTO_UPDATE="${KELO_AUTO_UPDATE:-}"
UNINSTALL=0
DELETE_DATA=0
BIN_DIR="${KELO_BIN_DIR:-$HOME/.local/bin}"
ADD_NAME="${KELO_ADD:-}"

usage() {
  if [ -f "$0" ]; then sed -n '2,/^# NO_COLOR/p' "$0" | sed 's/^# \{0,1\}//'
  else printf 'Kelo installer. Options: --yes --add NAME --dir DIR --source SRC --from-release --build-here --no-start --auto-update --uninstall [--delete-data] --help\n'; fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) YES=1 ;;
    --add) ADD_NAME="${2:?--add needs a name}"; shift ;;
    --add=*) ADD_NAME="${1#*=}" ;;
    --dir) KELO_HOME="${2:?--dir needs a folder}"; shift ;;
    --dir=*) KELO_HOME="${1#*=}" ;;
    --source) KELO_SOURCE="${2:?--source needs a folder or URL}"; shift ;;
    --source=*) KELO_SOURCE="${1#*=}" ;;
    --no-start) START=0 ;;
    --from-release) FROM_RELEASE=1 ;;
    --build-here) FROM_RELEASE=0 ;;
    --auto-update) AUTO_UPDATE=1 ;;
    --no-auto-update) AUTO_UPDATE=0 ;;
    --uninstall) UNINSTALL=1 ;;
    --delete-data) DELETE_DATA=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s (try --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
# The official images are the default. A custom --source is not the release, so it is built here unless asked otherwise.
if [ -z "$FROM_RELEASE" ]; then
  if [ -n "$KELO_SOURCE" ]; then FROM_RELEASE=0; else FROM_RELEASE=1; fi
fi

# ---------------------------------------------------------------------------------------------------------------- language
LANG_KO=0
case "${KELO_LANG:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}" in ko*|KO*) LANG_KO=1 ;; esac
# say "English" "한국어"
say() { if [ "$LANG_KO" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

# ---------------------------------------------------------------------------------------------------------------- look
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
  COLOR=1
  B=$'\033[1m'; D=$'\033[2m'; R=$'\033[0m'
  BLUE=$'\033[38;5;39m'; GREEN=$'\033[38;5;71m'; YELLOW=$'\033[38;5;214m'; RED=$'\033[38;5;203m'
else
  COLOR=0; B=""; D=""; R=""; BLUE=""; GREEN=""; YELLOW=""; RED=""
fi

ok()   { printf '  %s✓%s %s\n' "$GREEN$B" "$R" "$*"; }
warn() { printf '  %s!%s %s%s%s\n' "$YELLOW$B" "$R" "$YELLOW" "$*" "$R"; }
note() { printf '    %s%s%s\n' "$D" "$*" "$R"; }
die()  { printf '\n  %s✗%s %s\n\n' "$RED$B" "$R" "$*" >&2; exit 1; }
section() { printf '\n%s›%s %s%s%s\n' "$BLUE$B" "$R" "$B" "$*" "$R"; }

# Kelo in half blocks (same drawing as the setup screen; colours from the app's character art).
MASCOT=(
  "......GG......" ".....GGG......" "....BBBBBB...." "..BBBBBBBBBB.." ".BBBBBBBBBBBB." ".BBKBBBBBBKBB." ".BBBBBYYBBBBB."
  "DBBBBBYYBBBBBD" "DBBBbbbbbbBBBD" ".BBbbbbbbbbBB." ".BBbbbbbbbbBB." "..BBBbbbbBBB.." "....L....L...." "...LL...LL...."
)
colour_of() { case "$1" in G) echo 71 ;; B) echo 39 ;; b) echo 153 ;; D) echo 32 ;; K) echo 234 ;; Y) echo 214 ;; L) echo 109 ;; *) echo "" ;; esac; }

banner() {
  local title sub
  title="${BLUE}${B}Kelo${R} ${D}$(say "installer" "설치")${R}"
  sub="$(say "Your personal assistant, on a server you run." "내가 운영하는 서버에서 돌아가는 나만의 비서.")"
  printf '\n'
  if [ "$COLOR" = 1 ]; then
    local i j line top bot a b ca cb
    for ((i = 0; i < ${#MASCOT[@]}; i += 2)); do
      top="${MASCOT[$i]}"; bot="${MASCOT[$((i + 1))]}"; line="  "
      for ((j = 0; j < ${#top}; j++)); do
        a="${top:$j:1}"; b="${bot:$j:1}"; ca="$(colour_of "$a")"; cb="$(colour_of "$b")"
        if [ -n "$ca" ] && [ -n "$cb" ]; then line+=$'\033'"[38;5;${ca};48;5;${cb}m▀"$'\033[0m'
        elif [ -n "$ca" ]; then line+=$'\033'"[38;5;${ca}m▀"$'\033[0m'
        elif [ -n "$cb" ]; then line+=$'\033'"[38;5;${cb}m▄"$'\033[0m'
        else line+=" "; fi
      done
      case "$i" in 2) line+="    $title" ;; 4) line+="    $sub" ;; esac
      printf '%s\n' "$line"
    done
  else
    printf '  Kelo %s\n  %s\n' "$(say "installer" "설치")" "$sub"
  fi
}

# ---------------------------------------------------------------------------------------------------------------- terminal input
# Piped through `curl | bash`, stdin is the script itself, so questions are read from the terminal instead. Probed by opening
# it for real: a container build can have /dev/tty without a terminal behind it.
TTY_IN=""
if [ -t 0 ]; then TTY_IN=/dev/stdin
elif { : </dev/tty; } 2>/dev/null; then TTY_IN=/dev/tty
fi
INTERACTIVE=0; [ -n "$TTY_IN" ] && [ "$YES" != 1 ] && INTERACTIVE=1

# ask_yn "question" default(y|n) -> 0 for yes. --yes (or no terminal) takes the default.
ask_yn() {
  local q="$1" def="$2" hint answer
  [ "$def" = y ] && hint="Y/n" || hint="y/N"
  if [ "$INTERACTIVE" != 1 ]; then [ "$def" = y ]; return; fi
  while true; do
    printf '  %s %s[%s]%s: ' "$q" "$BLUE$B" "$hint" "$R"
    IFS= read -r answer <"$TTY_IN" || answer=""
    case "$answer" in
      "") [ "$def" = y ]; return ;;
      y|Y|yes|YES|예|ㅛ) return 0 ;;
      n|N|no|NO|아니요|ㅜ) return 1 ;;
    esac
  done
}

# ---------------------------------------------------------------------------------------------------------------- quiet steps
LOG="$(mktemp "${TMPDIR:-/tmp}/kelo-install.XXXXXX")"   # kept when something fails (the message says where); removed on success
FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)

# step "what is happening" command...: one line with a spinner; the command's output goes to the log, shown only if it fails.
# With STEP_SOFT=1 a failed step returns its exit code instead of stopping the installer, so the caller can fall back.
step() {
  local label="$1"; shift
  local start=$SECONDS status=0
  printf '\n==> %s\n' "$label" >>"$LOG"
  if [ "$COLOR" = 1 ]; then
    "$@" >>"$LOG" 2>&1 &
    local pid=$! i=0 last msg cols
    cols="$(tput cols 2>/dev/null || echo 80)"; [ "$cols" -gt 20 ] 2>/dev/null || cols=80
    while kill -0 "$pid" 2>/dev/null; do
      last="$(tail -n 1 "$LOG" 2>/dev/null | tr -d '\r' | sed 's/^==> .*//')"
      msg="$label ($((SECONDS - start))s)  $last"
      # one terminal line at most: a wrapped line could not be redrawn in place
      printf '\r\033[K  %s%s%s %s' "$BLUE" "${FRAMES[$((i % 10))]}" "$R" "${msg:0:$((cols - 6))}"
      i=$((i + 1)); sleep 0.1
    done
    wait "$pid" || status=$?
    printf '\r\033[K'
  else
    printf '  … %s\n' "$label"
    "$@" >>"$LOG" 2>&1 || status=$?
  fi
  if [ "$status" -eq 0 ]; then
    ok "$label ${D}($((SECONDS - start))s)${R}"
    return 0
  else
    printf '  %s✗%s %s\n' "$RED$B" "$R" "$label"
    if [ "${STEP_SOFT:-0}" = 1 ]; then return "$status"; fi
    tail -n 20 "$LOG" | sed "s/^/    ${D}│${R} /"
    die "$(say "That step failed (exit $status). Full log: $LOG" "이 단계가 실패했습니다 (종료 코드 $status). 전체 로그: $LOG")"
  fi
}

# ---------------------------------------------------------------------------------------------------------------- this computer
OS="$(uname -s)"; ARCH="$(uname -m)"
detect_platform() {
  case "$OS" in
    Darwin) PLATFORM="macOS $(sw_vers -productVersion 2>/dev/null || true)" ;;
    Linux)
      PLATFORM="Linux"
      [ -r /etc/os-release ] && PLATFORM="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-Linux}")"
      grep -qi microsoft /proc/version 2>/dev/null && PLATFORM="$PLATFORM (WSL)" ;;
    *) die "$(say "This installer is for macOS and Linux. On Windows use install.ps1 in PowerShell." \
                  "이 설치 스크립트는 macOS와 Linux용입니다. Windows에서는 PowerShell에서 install.ps1을 사용하세요.")" ;;
  esac
  case "$ARCH" in
    x86_64|amd64) ARCH_NAME="x86_64" ;;
    arm64|aarch64) ARCH_NAME="arm64" ;;
    *) ARCH_NAME="$ARCH"; warn "$(say "CPU type $ARCH is untested; Docker images may not exist for it." "CPU 종류 $ARCH 는 확인되지 않았습니다. Docker 이미지가 없을 수 있습니다.")" ;;
  esac
}

docker_screen() {   # one friendly screen, then stop
  local head="$1"; shift
  printf '\n  %s╭─ %s%s\n' "$RED" "$head" "$R"
  local l; for l in "$@"; do [ -n "$l" ] && printf '  %s│%s  %s\n' "$RED" "$R" "$l"; done
  printf '  %s│%s\n  %s│%s  %s\n  %s╰─%s\n\n' "$RED" "$R" "$RED" "$R" \
    "$(say "Then run the same command again; it picks up where it stopped." "그다음 같은 명령을 다시 실행하면 이어서 진행합니다.")" "$RED" "$R"
  exit 1
}

wait_for_docker() {
  local i; for i in $(seq 1 90); do docker info >/dev/null 2>&1 && return 0; sleep 2; done; return 1
}

check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    if [ "$OS" = Linux ] && ! grep -qi microsoft /proc/version 2>/dev/null; then
      warn "$(say "Docker is not installed." "Docker가 설치되어 있지 않습니다.")"
      note "$(say "Docker's own install script can set it up now (it uses sudo): https://get.docker.com" \
                  "Docker 공식 설치 스크립트로 지금 설치할 수 있습니다 (sudo 사용): https://get.docker.com")"
      if ask_yn "$(say "Install Docker now?" "지금 Docker를 설치할까요?")" n; then
        step "$(say "Installing Docker" "Docker 설치 중")" sh -c 'curl -fsSL https://get.docker.com | sudo sh'
        sudo systemctl enable --now docker >/dev/null 2>&1 || true
        if ! docker info >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
          sudo usermod -aG docker "$USER" || true
          docker_screen "$(say "Docker is installed" "Docker를 설치했습니다")" \
            "$(say "Log out and back in once so this user may use Docker." "이 사용자가 Docker를 쓸 수 있도록 한 번 로그아웃했다가 다시 로그인하세요.")"
        fi
      else
        docker_screen "$(say "Docker is needed" "Docker가 필요합니다")" \
          "$(say "Kelo runs in Docker, so Docker comes first." "Kelo는 Docker 안에서 동작하므로 Docker가 먼저 필요합니다.")" \
          "$(say "Install Docker Engine and the Compose plugin:" "Docker Engine과 Compose 플러그인을 설치하세요:")" \
          "  https://docs.docker.com/engine/install/"
      fi
    elif [ "$OS" = Darwin ]; then
      docker_screen "$(say "Docker is needed" "Docker가 필요합니다")" \
        "$(say "Kelo runs in Docker, so Docker comes first." "Kelo는 Docker 안에서 동작하므로 Docker가 먼저 필요합니다.")" \
        "$(say "Install Docker Desktop (free for personal use):" "Docker Desktop을 설치하세요 (개인 사용 무료):")" \
        "  https://docs.docker.com/desktop/setup/install/mac-install/" \
        "$(command -v brew >/dev/null 2>&1 && say "or with Homebrew:  brew install --cask docker" "또는 Homebrew로:  brew install --cask docker")"
    else
      docker_screen "$(say "Docker is needed" "Docker가 필요합니다")" \
        "$(say "In WSL, install Docker Desktop for Windows and turn on WSL integration for this distribution:" \
               "WSL에서는 Windows용 Docker Desktop을 설치하고 이 배포판의 WSL 통합을 켜세요:")" \
        "  https://docs.docker.com/desktop/features/wsl/"
    fi
  fi
  ok "Docker $(docker version --format '{{.Client.Version}}' 2>/dev/null || echo '?')"

  if ! docker compose version >/dev/null 2>&1; then
    docker_screen "$(say "Docker Compose v2 is missing" "Docker Compose v2가 없습니다")" \
      "$(say "Install the Compose plugin:" "Compose 플러그인을 설치하세요:")" "  https://docs.docker.com/compose/install/"
  fi
  ok "Docker Compose $(docker compose version --short 2>/dev/null)"

  local err
  if ! err="$(docker info 2>&1 >/dev/null)"; then
    if printf '%s' "$err" | grep -qi "permission denied"; then
      docker_screen "$(say "This user may not use Docker yet" "이 사용자에게 아직 Docker 권한이 없습니다")" \
        "sudo usermod -aG docker \$USER" "$(say "then log out and back in." "그다음 로그아웃했다가 다시 로그인하세요.")"
    fi
    if [ "$OS" = Darwin ]; then
      if ask_yn "$(say "Docker Desktop is not running. Open it now?" "Docker Desktop이 실행 중이 아닙니다. 지금 열까요?")" y; then
        open -a Docker >/dev/null 2>&1 || true
        step "$(say "Waiting for Docker Desktop to start" "Docker Desktop이 시작되기를 기다리는 중")" wait_for_docker
      else
        docker_screen "$(say "Docker is not running" "Docker가 실행 중이 아닙니다")" \
          "$(say "Open Docker Desktop and wait until it says it is running." "Docker Desktop을 열고 실행 중이라고 나올 때까지 기다려 주세요.")"
      fi
    elif command -v systemctl >/dev/null 2>&1 && ask_yn "$(say "Docker is not running. Start it now (sudo)?" "Docker가 실행 중이 아닙니다. 지금 시작할까요 (sudo)?")" y; then
      step "$(say "Starting Docker" "Docker 시작 중")" sh -c 'sudo systemctl start docker && sleep 1 && docker info'
    else
      docker_screen "$(say "Docker is not running" "Docker가 실행 중이 아닙니다")" "sudo systemctl start docker"
    fi
  fi
  ok "$(say "Docker is running" "Docker 실행 중")"
}

# ---------------------------------------------------------------------------------------------------------------- uv
find_uv() {
  UV="$(command -v uv 2>/dev/null || true)"
  [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
  [ -z "$UV" ] && [ -x "$HOME/.cargo/bin/uv" ] && UV="$HOME/.cargo/bin/uv"
  [ -n "$UV" ]
}

ensure_uv() {
  if ! find_uv; then
    note "$(say "uv (Astral's Python tool) is needed to run the setup; installing it for this user only (no sudo)." \
                "설정을 실행하려면 uv(Astral의 Python 도구)가 필요합니다. 이 사용자에게만 설치합니다 (sudo 없음).")"
    step "$(say "Installing uv" "uv 설치 중")" sh -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh'
    find_uv || die "$(say "uv was installed but cannot be found. Open a new terminal and run this again." "uv를 설치했지만 찾을 수 없습니다. 새 터미널을 열고 다시 실행하세요.")"
  fi
  ok "uv $("$UV" --version 2>/dev/null | awk '{print $2}')"
}

# ---------------------------------------------------------------------------------------------------------------- Kelo files
# Never overwritten by an update: settings, keys, extra copies, address credentials, and anything the phone apps need to build.
EXCLUDES=(--exclude=./.env --exclude=./.env.* --exclude=./instances --exclude=./connect --exclude=./.venv --exclude=./.git
          --exclude=./android --exclude=./ios --exclude=./server/web/node_modules --exclude=./server/web/dist
          --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache "--exclude=*.log")

copy_tree() {   # copy_tree FROM TO
  local from to
  from="$(cd "$1" && pwd -P)"; mkdir -p "$2"; to="$(cd "$2" && pwd -P)"
  [ "$from" = "$to" ] && return 0
  (cd "$from" && tar -cf - "${EXCLUDES[@]}" .) | (cd "$to" && tar -xf -)
}

# Where the Kelo files come from: KELO_SOURCE (a local folder, a .tar.gz URL or a git URL), else the public source's newest release
# (github.com/kelo-chat/kelo-server: the server part of each release; its main branch is the newest release).
DEFAULT_SOURCE="https://github.com/kelo-chat/kelo-server/archive/refs/heads/main.tar.gz"
fetch_kelo() {
  local src="$KELO_SOURCE" stage
  if [ -z "$src" ]; then
    if [ -f "$KELO_HOME/compose.yml" ]; then
      note "$(say "No KELO_SOURCE given: keeping the Kelo files already in $KELO_HOME." "KELO_SOURCE가 없어 $KELO_HOME 에 있는 Kelo 파일을 그대로 사용합니다.")"
      return 0
    fi
    src="$DEFAULT_SOURCE"
  fi
  mkdir -p "$KELO_HOME"
  case "$src" in
    *.git|git@*)
      stage="$(mktemp -d)"
      step "$(say "Downloading Kelo" "Kelo 내려받는 중")" git clone --depth 1 "$src" "$stage/kelo"
      step "$(say "Putting the files in $KELO_HOME" "$KELO_HOME 에 파일 넣는 중")" copy_tree "$stage/kelo" "$KELO_HOME"
      rm -rf "$stage" ;;
    http://*|https://*)
      stage="$(mktemp -d)"
      step "$(say "Downloading Kelo" "Kelo 내려받는 중")" curl -fsSL --proto '=https,http' -o "$stage/kelo.tar.gz" "$src"
      mkdir -p "$stage/x"
      step "$(say "Unpacking" "압축 푸는 중")" tar -xzf "$stage/kelo.tar.gz" -C "$stage/x"
      local top="$stage/x"
      [ "$(ls -A "$stage/x" | wc -l | tr -d ' ')" = 1 ] && [ -d "$stage/x/$(ls -A "$stage/x")" ] && top="$stage/x/$(ls -A "$stage/x")"
      [ -f "$top/compose.yml" ] || die "$(say "That archive does not look like Kelo (no compose.yml)." "그 압축 파일은 Kelo가 아닌 것 같습니다 (compose.yml 없음).")"
      step "$(say "Putting the files in $KELO_HOME" "$KELO_HOME 에 파일 넣는 중")" copy_tree "$top" "$KELO_HOME"
      rm -rf "$stage" ;;
    *.tar.gz|*.tgz|*.tar)
      stage="$(mktemp -d)"
      mkdir -p "$stage/x"
      step "$(say "Unpacking" "압축 푸는 중")" tar -xf "$src" -C "$stage/x"
      local top="$stage/x"
      [ "$(ls -A "$stage/x" | wc -l | tr -d ' ')" = 1 ] && [ -d "$stage/x/$(ls -A "$stage/x")" ] && top="$stage/x/$(ls -A "$stage/x")"
      [ -f "$top/compose.yml" ] || die "$(say "That archive does not look like Kelo (no compose.yml)." "그 압축 파일은 Kelo가 아닌 것 같습니다 (compose.yml 없음).")"
      step "$(say "Putting the files in $KELO_HOME" "$KELO_HOME 에 파일 넣는 중")" copy_tree "$top" "$KELO_HOME"
      rm -rf "$stage" ;;
    *)
      [ -f "$src/compose.yml" ] || die "$(say "$src does not look like a Kelo folder (no compose.yml)." "$src 는 Kelo 폴더가 아닌 것 같습니다 (compose.yml 없음).")"
      step "$(say "Copying Kelo into $KELO_HOME" "$KELO_HOME 에 Kelo 복사 중")" copy_tree "$src" "$KELO_HOME" ;;
  esac
}

install_launcher() {
  mkdir -p "$BIN_DIR"
  cat >"$BIN_DIR/kelo" <<EOF
#!/bin/sh
# Kelo's command (made by the installer): runs \`assistant\` in $KELO_HOME. Example: kelo status, kelo phone add
cd "$KELO_HOME" || exit 1
KELO_CMD=kelo exec "$UV" run --quiet --no-dev --frozen assistant "\$@"
EOF
  chmod +x "$BIN_DIR/kelo"
  ok "$(say "Added the kelo command ($BIN_DIR/kelo)" "kelo 명령을 추가했습니다 ($BIN_DIR/kelo)")"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) note "$(say "$BIN_DIR is not on your PATH yet. Add it, e.g.: echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.profile" \
                   "$BIN_DIR 가 아직 PATH에 없습니다. 예: echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.profile")" ;;
  esac
}

# ---------------------------------------------------------------------------------------------------------------- uninstall
uninstall() {
  banner
  section "$(say "Remove Kelo" "Kelo 제거")"
  [ -f "$KELO_HOME/compose.yml" ] || die "$(say "No Kelo in $KELO_HOME (use --dir to point at it)." "$KELO_HOME 에 Kelo가 없습니다 (--dir로 위치를 지정하세요).")"
  if [ "$DELETE_DATA" = 1 ]; then
    warn "$(say "--delete-data: every chat, key, file and phone sign-in of every copy is deleted for good." \
                "--delete-data: 모든 복사본의 대화, 키, 파일, 휴대폰 연결이 영구히 삭제됩니다.")"
  else
    note "$(say "Your data stays (Docker volumes and $KELO_HOME/.env). Installing again brings everything back." \
                "데이터는 남겨 둡니다 (Docker 볼륨과 $KELO_HOME/.env). 다시 설치하면 모두 돌아옵니다.")"
  fi
  if [ "$YES" != 1 ]; then
    [ -n "$TTY_IN" ] || die "$(say "Removing needs a terminal to confirm, or --yes." "제거하려면 터미널에서 확인하거나 --yes가 필요합니다.")"
    ask_yn "$(say "Remove Kelo from this server?" "이 서버에서 Kelo를 제거할까요?")" n || { note "$(say "Nothing was changed." "아무것도 바꾸지 않았습니다.")"; exit 0; }
  fi
  cd "$KELO_HOME"
  find_uv || true
  local run_assistant=()
  [ -n "${UV:-}" ] && run_assistant=("$UV" run --quiet --no-dev --frozen assistant)
  if [ ${#run_assistant[@]} -gt 0 ] && [ -d instances ]; then
    local d name
    for d in instances/*/; do
      [ -f "$d/env" ] || continue
      name="$(basename "$d")"
      if [ "$DELETE_DATA" = 1 ]; then
        step "$(say "Deleting the copy $name" "복사본 $name 삭제 중")" "${run_assistant[@]}" instance delete "$name" --yes
      else
        step "$(say "Stopping the copy $name (its data stays)" "복사본 $name 멈추는 중 (데이터는 남김)")" "${run_assistant[@]}" instance stop "$name"
      fi
    done
  fi
  if [ ${#run_assistant[@]} -gt 0 ] && [ -f .env ]; then     # gives a <name>.kelo.chat address back; harmless when there is none
    "${run_assistant[@]}" connect off >>"$LOG" 2>&1 || true
  fi
  if [ "$DELETE_DATA" = 1 ]; then
    step "$(say "Stopping Kelo and deleting its data" "Kelo를 멈추고 데이터 삭제 중")" docker compose --profile '*' down --volumes --remove-orphans
    cd "$HOME"
    rm -rf "$KELO_HOME"
    ok "$(say "Deleted $KELO_HOME" "$KELO_HOME 삭제함")"
  else
    step "$(say "Stopping and removing the containers" "컨테이너를 멈추고 제거하는 중")" docker compose --profile '*' down --remove-orphans
  fi
  rm -f "$BIN_DIR/kelo"
  ok "$(say "Kelo is removed. Docker and uv stay installed." "Kelo를 제거했습니다. Docker와 uv는 그대로 둡니다.")"
}

# ---------------------------------------------------------------------------------------------------------------- updates from the app
# The updater container (compose profile `updater`): the owner presses Update in the app, or lets it update at night. Its secret
# (UPDATER_TOKEN) is made by `assistant updater setup` and never shown. The images are the signed official ones (checked by
# `assistant`, digest and signature) or were built from these files; the app says to update with `kelo update` in the second case.
ask_auto_update() {
  section "$(say "Updates from the app" "앱에서 하는 업데이트")"
  note "$(say "The owner's phone can update this server with one button (My → Server update). Kelo can also do it by itself at night," \
              "주인 휴대폰에서 버튼 하나로 이 서버를 업데이트할 수 있습니다 (마이 → 서버 업데이트). 밤에 Kelo가 알아서 할 수도 있습니다,")"
  note "$(say "when you usually do not use it; a backup is made first and it goes back by itself if the new version fails." \
              "평소 쓰지 않는 시간에요. 먼저 백업하고, 새 버전이 실패하면 저절로 되돌립니다.")"
  if [ -z "$AUTO_UPDATE" ]; then
    if ask_yn "$(say "Update automatically at night?" "밤에 자동으로 업데이트할까요?")" n; then AUTO_UPDATE=1; else AUTO_UPDATE=0; fi
  fi
}

# assistant updater setup with this run's choices, plus the extra arguments; a failure stops the installer unless STEP_SOFT=1
run_updater_setup() {   # run_updater_setup "what is happening" [extra arguments]
  local label="$1"; shift
  local args=(updater setup)
  if [ "$AUTO_UPDATE" = 1 ]; then args+=(--auto-update); else args+=(--no-auto-update); fi
  if [ -n "${KELO_REGISTRY:-}" ]; then args+=(--registry "$KELO_REGISTRY"); fi
  if [ "$START" = 0 ]; then args+=(--no-start); fi
  args+=("$@")
  step "$label" env KELO_CMD=kelo "$UV" run --quiet --no-dev --frozen assistant "${args[@]}"
}

setup_updater() {
  ask_auto_update
  run_updater_setup "$(say "Setting up the updater" "업데이트 도구 설정 중")"
  if [ "$AUTO_UPDATE" = 1 ]; then ok "$(say "Nightly auto-update is on (change it in the app: My → Server update)" "밤 자동 업데이트를 켰습니다 (앱의 마이 → 서버 업데이트에서 바꿀 수 있습니다)")"
  else ok "$(say "Updates when the owner presses Update in the app (My → Server update)" "앱에서 업데이트를 누를 때 업데이트합니다 (마이 → 서버 업데이트)")"; fi
}

# Without a terminal, the first install pulls the official images before Kelo starts (the updater's settings come with the pull).
# If the pull fails, the images are built here instead, as before, with a warning. Only the Claude / agy containers are built
# when the official images are used (their CLIs are never published).
pull_first_install() {
  if STEP_SOFT=1 run_updater_setup "$(say "Downloading the official Kelo images and checking their signatures" \
                                       "공식 Kelo 이미지를 내려받고 서명을 확인하는 중")" --no-start --pull-release; then
    if [ "$START" = 1 ]; then
      step "$(say "Starting Kelo" "Kelo 시작 중")" env BUILDKIT_PROGRESS=plain docker compose up -d --remove-orphans
    fi
  else
    local why
    why="$(grep '^error' "$LOG" 2>/dev/null | tail -n 1 | sed 's/^error: //' || true)"   # the reason assistant printed, if any
    warn "$(say "Could not get the official images${why:+: $why}. Building them here instead." \
                "공식 이미지를 받지 못했습니다${why:+: $why}. 이 서버에서 직접 빌드합니다.")"
    if [ "$START" = 1 ]; then
      step "$(say "Building and starting Kelo (the first time takes a few minutes)" "Kelo를 빌드하고 시작하는 중 (처음에는 몇 분 걸립니다)")" \
        env BUILDKIT_PROGRESS=plain docker compose up -d --build --remove-orphans
    fi
  fi
}

# ---------------------------------------------------------------------------------------------------------------- multiple instances
get_free_memory_mb() {
  if [ -n "${KELO_TEST_FREE_MEM_MB:-}" ]; then
    printf '%s' "$KELO_TEST_FREE_MEM_MB"
    return
  fi
  local free_mb=0
  if [ "$OS" = Linux ] && [ -r /proc/meminfo ]; then
    free_mb="$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || true)"
    if [ -z "$free_mb" ] || [ "$free_mb" -eq 0 ]; then
      free_mb="$(awk '/^MemFree:/ {free=$2} /^Buffers:/ {buf=$2} /^Cached:/ {cache=$2} END {print int((free+buf+cache)/1024)}' /proc/meminfo 2>/dev/null || true)"
    fi
  elif [ "$OS" = Darwin ]; then
    free_mb="$(vm_stat 2>/dev/null | awk '
      BEGIN {ps=4096; free=0; spec=0}
      /page size of/ {sub(/.*page size of /,""); sub(/ bytes.*/,""); ps=$1}
      /Pages free/ {sub(/\./,"",$3); free=$3}
      /Pages speculative/ {sub(/\./,"",$3); spec=$3}
      END {printf "%d", (free+spec)*ps/(1024*1024)}
    ' 2>/dev/null || true)"
  fi
  printf '%s' "${free_mb:-0}"
}

show_memory_info() {
  local free_mb
  free_mb="$(get_free_memory_mb)"
  if [ -n "$free_mb" ] && [ "$free_mb" -gt 0 ] 2>/dev/null; then
    local free_gb
    free_gb="$(awk -v mb="$free_mb" 'BEGIN {printf "%.1f", mb/1024}')"
    note "$(say "Free memory: ${free_gb} GB (recommended for another copy: 4 GB)" \
                "여유 메모리: ${free_gb} GB (서버 추가 권장: 4 GB)")"
    if [ "$free_mb" -lt 4096 ]; then
      warn "$(say "Free memory is below the recommended 4 GB for one more copy." \
                  "여유 메모리가 복사본 추가 권장 사양(4 GB)보다 적습니다.")"
    fi
  else
    note "$(say "Recommended memory for another copy: 4 GB" \
                "서버 추가 권장 메모리: 4 GB")"
  fi
}

valid_name_format() {
  local n="$1"
  [ "${#n}" -ge 3 ] && [ "${#n}" -le 20 ] || return 1
  case "${n:0:1}" in
    [a-z]) ;;
    *) return 1 ;;
  esac
  case "${n: -1}" in
    [a-z0-9]) ;;
    *) return 1 ;;
  esac
  case "$n" in
    *[!a-z0-9-]*) return 1 ;;
  esac
  return 0
}

is_existing_name() {
  local n="$1"
  [ "$n" = "main" ] && return 0
  [ -e "$KELO_HOME/instances/$n" ] && return 0
  return 1
}

ensure_kelo_command() {
  if [ ! -x "$BIN_DIR/kelo" ]; then
    find_uv || ensure_uv
    install_launcher
  fi
}

add_instance() {
  local name="$1"
  section "$(say "Add another Kelo server" "Kelo 서버 하나 더 만들기")"
  show_memory_info

  if [ -z "$name" ]; then
    while true; do
      printf '  %s: ' "$(say "Name of the new copy (3-20 lowercase letters, digits or '-')" \
                             "새 복사본 이름 (3~20자의 영문 소문자, 숫자, '-')")"
      if ! IFS= read -r name <"$TTY_IN"; then
        printf '\n'
        exit 0
      fi
      if [ -z "$name" ]; then
        continue
      fi
      if ! valid_name_format "$name"; then
        warn "$(say "Name must be 3-20 lowercase letters, digits or '-', starting with a letter." \
                    "이름은 영문 소문자로 시작하는 3~20자의 소문자, 숫자, '-'이어야 합니다.")"
        name=""
        continue
      fi
      if is_existing_name "$name"; then
        warn "$(say "A Kelo server named '$name' already exists." \
                    "'$name' 이름을 가진 Kelo 서버가 이미 있습니다.")"
        name=""
        continue
      fi
      break
    done
  else
    if ! valid_name_format "$name"; then
      die "$(say "Name '$name' is invalid: must be 3-20 lowercase letters, digits or '-', starting with a letter." \
                 "잘못된 이름 '$name': 영문 소문자로 시작하는 3~20자의 소문자, 숫자, '-'이어야 합니다.")"
    fi
    if is_existing_name "$name"; then
      die "$(say "A Kelo server named '$name' already exists." \
                 "'$name' 이름을 가진 Kelo 서버가 이미 있습니다.")"
    fi
  fi

  ensure_kelo_command
  local cmd=("$BIN_DIR/kelo" instance create "$name")
  [ "$YES" = 1 ] && cmd+=(--yes)
  if [ -n "$TTY_IN" ] && [ -t 0 ]; then
    "${cmd[@]}"
  elif [ -n "$TTY_IN" ]; then
    "${cmd[@]}" <"$TTY_IN"
  else
    "${cmd[@]}"
  fi
}

ask_reinstall_choice() {
  printf '\n  %s\n' "$(say "Kelo is already installed here. What would you like to do?" \
                           "이 컴퓨터에 Kelo가 이미 있어요. 무엇을 할까요?")"
  printf '    %s1%s) %s %s(%s)%s\n' "$BLUE$B" "$R" "$(say "Update it" "업데이트하기")" "$D" "$(say "default" "기본값")" "$R"
  printf '    %s2%s) %s\n' "$BLUE$B" "$R" "$(say "Add another Kelo server (for another person or a test)" \
                                              "Kelo 서버 하나 더 만들기 (다른 사람용이나 테스트용)")"
  local ans
  while true; do
    printf '  %s %s[1]%s: ' "$(say "Choice" "선택")" "$BLUE$B" "$R"
    if ! IFS= read -r ans <"$TTY_IN"; then
      ans=""
    fi
    case "$ans" in
      ""|1) return 0 ;;
      2)
        add_instance ""
        exit 0
        ;;
      *) ;;
    esac
  done
}

# ---------------------------------------------------------------------------------------------------------------- main
main() {
  [ "$UNINSTALL" = 1 ] && { uninstall; return; }
  banner
  detect_platform
  if [ -f "$KELO_HOME/compose.yml" ]; then
    if [ -n "$ADD_NAME" ]; then
      add_instance "$ADD_NAME"
      return 0
    fi
    if [ "$INTERACTIVE" = 1 ]; then
      ask_reinstall_choice
    fi
  elif [ -n "$ADD_NAME" ]; then
    die "$(say "Kelo is not installed in $KELO_HOME yet. Install it first without --add." \
                "Kelo가 아직 $KELO_HOME 에 설치되어 있지 않습니다. 먼저 --add 없이 설치하세요.")"
  fi
  local mode
  if [ -f "$KELO_HOME/compose.yml" ]; then mode="$(say "update (data kept)" "업데이트 (데이터 유지)")"; else mode="$(say "new install" "새로 설치")"; fi
  printf '\n'
  printf '    %s%-12s%s %s\n' "$D" "$(say "server" "서버")" "$R" "$PLATFORM · $ARCH_NAME"
  printf '    %s%-12s%s %s\n' "$D" "$(say "folder" "폴더")" "$R" "$KELO_HOME"
  printf '    %s%-12s%s %s\n' "$D" "$(say "what" "작업")" "$R" "$mode"
  [ -n "$KELO_SOURCE" ] && printf '    %s%-12s%s %s\n' "$D" "$(say "from" "가져올 곳")" "$R" "$KELO_SOURCE"

  section "$(say "Docker" "Docker")"
  check_docker
  section "$(say "Tools" "도구")"
  ensure_uv
  section "$(say "Kelo files" "Kelo 파일")"
  fetch_kelo
  cd "$KELO_HOME"
  step "$(say "Preparing Python and Kelo's packages" "Python과 Kelo 패키지 준비 중")" "$UV" sync --frozen --no-dev
  install_launcher

  # Kelo files from before 0.10.2 have no `setup --from-release`: their guided screen saves the answers only, and the images are
  # pulled (or built) after it, as before (no QR step then: the closing note says how to connect a phone).
  local wizard_pulls=0
  if [ "$FROM_RELEASE" = 1 ] && "$UV" run --quiet --no-dev --frozen assistant setup --help 2>/dev/null | grep -q -- '--from-release'; then
    wizard_pulls=1
  fi
  local args=(setup)
  if [ "$START" = 0 ] || { [ "$FROM_RELEASE" = 1 ] && [ "$wizard_pulls" = 0 ]; }; then args+=(--no-start); fi
  if [ "$wizard_pulls" = 1 ]; then args+=(--from-release); fi   # the guided screen pulls the official images, then starts Kelo
  if [ "$INTERACTIVE" = 1 ]; then
    printf '\n'
    KELO_CMD=kelo "$UV" run --quiet --no-dev --frozen assistant "${args[@]}" <"$TTY_IN"
    if [ "$FROM_RELEASE" = 1 ] && [ "$wizard_pulls" = 0 ]; then
      pull_first_install
      note "$(say "Next: kelo connect kelo --name <name>   then   kelo phone add" "다음: kelo connect kelo --name <이름>   그리고   kelo phone add")"
    fi
    setup_updater
  else
    section "$(say "Setup (no questions)" "설정 (질문 없이)")"
    KELO_CMD=kelo "$UV" run --quiet --no-dev --frozen assistant setup --yes >>"$LOG" 2>&1
    ok "$(say "Saved the settings in $KELO_HOME/.env" "$KELO_HOME/.env 에 설정을 저장했습니다")"
    if [ "$FROM_RELEASE" = 1 ]; then
      pull_first_install
    elif [ "$START" = 1 ]; then
      step "$(say "Building and starting Kelo (the first time takes a few minutes)" "Kelo를 빌드하고 시작하는 중 (처음에는 몇 분 걸립니다)")" \
        env BUILDKIT_PROGRESS=plain docker compose up -d --build --remove-orphans
    fi
    setup_updater
  fi
  [ "$INTERACTIVE" = 1 ] || note "$(say "Next: kelo connect kelo --name <name>   then   kelo phone add" "다음: kelo connect kelo --name <이름>   그리고   kelo phone add")"
  rm -f "$LOG"
}

main
