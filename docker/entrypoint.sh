#!/bin/sh
set -eu

case "${ROLE:-}" in
  server)
    exec uvicorn --factory assistant.server:app_factory --host 0.0.0.0 --port 8080 --no-server-header
    ;;
  egress)
    # The browser's only way out (assistant/egress.py): forwards public addresses only, refuses the LAN, the host and metadata.
    exec python -m assistant.egress
    ;;
  vpn)
    # The owner's VPN or proxy (assistant/vpn_sidecar.py): WireGuard in user space (wireproxy) or a forwarder to the owner's proxy,
    # as SOCKS5 on :1080 of the private network vpn-net. Idle until the server writes the configuration into /run/kelo-vpn.
    exec python -m assistant.vpn_sidecar
    ;;
  voice)
    # Speech to text (assistant/voice_service.py, docs/voice.md): no network, reads the models the server downloaded, on voice.sock.
    exec python -m assistant.voice_service
    ;;
  keyring)
    # The AI providers' keys (assistant/keyring_service.py, docs/keyring.md): locked until the server sends the ring key.
    exec python -m assistant.keyring_service
    ;;
  browser)
    umask 022   # downloads must stay readable by the server, which mounts the download folder read-only
    # A restarted container keeps /tmp: a lock left by the last X server would stop the display (and the browser) from ever starting.
    rm -f "/tmp/.X${DISPLAY#:}-lock" "/tmp/.X11-unix/X${DISPLAY#:}"
    # The screen is TigerVNC's own X server (Xvnc): a real X display that also speaks RFB, so the live view of a tab is this screen.
    # RFB on loopback only, no password: the only way to it is the socat below, on the private browser network, the same trust as
    # DevTools. The server's ticketed /vnc route (api/browser.py) is the only thing that talks to it, and it drops pointer, key and
    # clipboard messages from anyone who does not hold the tab. -AcceptSetDesktopSize: the screen follows the phone's size.
    Xvnc "$DISPLAY" -geometry "${SCREEN%x*}" -depth "${SCREEN##*x}" -localhost -rfbport 5900 -SecurityTypes None \
      -AlwaysShared -AcceptSetDesktopSize=1 -nolisten tcp >/dev/null 2>&1 &
    sleep 1
    openbox >/dev/null 2>&1 &
    # RFB for the server container, republished like DevTools below; never published by compose.
    socat TCP-LISTEN:"${RFB_PORT:-5901}",fork,reuseaddr TCP:127.0.0.1:5900 &
    # DevTools stays on loopback; socat republishes it on :9223 for other containers on the
    # private network. Compose never publishes this port.
    socat TCP-LISTEN:"${DEVTOOLS_PORT:-9223}",fork,reuseaddr TCP:127.0.0.1:9222 &
    # A recreated container gets a new hostname; Chromium then treats the old SingletonLock in the profile
    # volume as "in use on another computer" and never opens the DevTools port. This container is the only
    # user of the profile, so the leftover lock is always stale.
    # BROWSER_EPHEMERAL=1 (the work browser): the profile is emptied before Chrome starts, so nothing of the owner's is kept on this disk.
    if [ "${BROWSER_EPHEMERAL:-0}" = 1 ]; then rm -rf /data/profile; fi
    rm -f /data/profile/SingletonLock /data/profile/SingletonCookie /data/profile/SingletonSocket
    # Cap on the page cache kept on disk: BROWSER_CACHE_MB, and when that is 0 or unset the default of 1024 MB (Chrome alone would
    # grow it to a share of the free disk). docs/disk-growth.md.
    CACHE_MB="${BROWSER_CACHE_MB:-0}"
    case "$CACHE_MB" in ''|*[!0-9]*) CACHE_MB=0 ;; esac
    [ "$CACHE_MB" -gt 0 ] || CACHE_MB=1024
    CACHE_FLAG="--disk-cache-size=$((CACHE_MB * 1048576)) --media-cache-size=$((CACHE_MB * 1048576 / 4))"
    # Caches the browser rebuilds by itself are emptied at every start (nothing is running yet), and crash reports are dropped. The
    # profile's cookies, logins and site storage are not touched.
    for d in "Default/Code Cache" "Default/GPUCache" "Default/DawnCache" "GrShaderCache" "ShaderCache" "GraphiteDawnCache" "component_crx_cache" \
             "Crashpad/reports" "Crashpad/completed"; do
      rm -rf "/data/profile/$d" 2>/dev/null || true
    done
    # While it runs: crash reports and leftovers in /tmp older than a day go, every half hour (the page cache is capped by the flag).
    ( while true; do
        find /data/profile/Crashpad -type f -mmin +1440 -delete 2>/dev/null
        find /tmp -mindepth 1 -maxdepth 1 \( -name '.org.chromium.*' -o -name '.com.google.Chrome.*' -o -name 'scoped_dir*' \) -mmin +1440 -exec rm -rf {} + 2>/dev/null
        sleep 1800
      done ) &
    # The browser's way out: the egress proxy (BROWSER_PROXY; compose sets http://egress:3128). The container's own network has no
    # route anywhere else, so this is the only path, and the proxy refuses private addresses.
    PROXY_FLAG=""
    if [ -n "${BROWSER_PROXY:-}" ]; then
      PROXY_FLAG="--proxy-server=$BROWSER_PROXY"
    fi
    # The user's language (BROWSER_LANG, e.g. ko-KR): what pages see in Accept-Language and navigator.languages.
    LANG_FLAGS=""
    if [ -n "${BROWSER_LANG:-}" ]; then
      first=$(printf '%s' "$BROWSER_LANG" | cut -d, -f1)
      base=$(printf '%s' "$first" | cut -d- -f1)
      list="$first"
      [ "$base" != "$first" ] && list="$list,$base"
      case ",$list," in *,en,*) ;; *) list="$list,en-US,en" ;; esac
      LANG_FLAGS="--lang=$first --accept-lang=$list"
      # Linux Chrome ignores --lang for its own UI locale (Intl), so the environment says it too: ko-KR -> ko_KR:ko, en-US -> en_US:en.
      if [ "$base" != "$first" ]; then export LANGUAGE="$(printf '%s' "$first" | tr - _):$base"; else export LANGUAGE="$first"; fi
    fi
    # Downloads (the server reads them through a read-only mount) are kept for a day.
    mkdir -p /downloads 2>/dev/null || true
    ( while true; do find /downloads -mindepth 1 -maxdepth 1 -type f -mmin +1440 -delete 2>/dev/null; sleep 1800; done ) &
    # Identity (docs/browser.md "Identity"): the browser states the machine's own OS, Linux, unless browser_identity.STATED_OS is
    # "chromeos" (the one switch; read from the module so there is one source of truth). Google Chrome on Linux already states exactly
    # that, so BROWSER=chrome (/etc/kelo-browser) needs no identity loop and no flag while the stated OS is Linux (as before f5277b45).
    # A Chromium build always needs them (Debian's Chromium names itself "Chromium"), and so does Google Chrome when the switch is on.
    # The loop and the flag give the matching Chrome release's User-Agent (flag, for requests made outside DevTools) and the same
    # User-Agent string plus its User-Agent Client Hints on every tab, popup, frame and worker.
    UA_FLAG=""
    STATED_OS=$(python -m assistant.browser_identity --stated-os 2>/dev/null || echo linux)
    if [ "$(cat /etc/kelo-browser 2>/dev/null || echo chromium)" != "chrome" ] || [ "$STATED_OS" != "linux" ]; then
      full=$(browser --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+){3}' | head -n1)
      if [ -n "$full" ]; then
        UA_FLAG="--user-agent=$(python -m assistant.browser_identity --user-agent "$full")"
        ( while true; do python -m assistant.browser_identity; sleep 2; done ) &
      fi
    fi
    # The owner's browser keeps its sign-ins across a restart (cards p-browser-signin-shared, p-signin-gaps): Chrome deletes session
    # cookies (a sign-in without "stay signed in") at start unless it restores the last session. The preference alone did not do that in
    # a live check (tests/test_work_seed_live.py); the --restore-last-session switch does, so the browser line passes it. The tabs it had
    # open are removed first, so none come back (the cookies are what stays). The work browser (BROWSER_EPHEMERAL=1) starts empty and is
    # not touched.
    RESTORE_FLAG=""
    if [ "${BROWSER_EPHEMERAL:-0}" != 1 ]; then
      mkdir -p /data/profile/Default
      python -c 'import json, os; p = "/data/profile/Default/Preferences"; d = json.load(open(p)) if os.path.exists(p) else {}; d = d if isinstance(d, dict) else {}; s = d.get("session") if isinstance(d.get("session"), dict) else {}; s["restore_on_startup"] = 1; d["session"] = s; json.dump(d, open(p, "w"))' 2>/dev/null || true
      rm -rf /data/profile/Default/Sessions
      RESTORE_FLAG="--restore-last-session"
    fi
    # --no-sandbox: Chrome's sandbox needs user namespaces the container does not grant. The
    # container (unprivileged, no capabilities, no host mounts, no network route but the proxy) is the sandbox instead.
    # Managed policy (/etc/chromium/policies/managed/kelo.json): no sync, sign-in, password manager, autofill, metrics or component
    # updates; notifications and location ask (nobody answers, so no site is ever granted); PDFs open in the browser. The mouse is
    # declared as a fine pointer with hover (--blink-settings below): the Xvnc pointer is not seen as a mouse otherwise. Headful on a
    # 1440x900 screen with a Chrome identity (above). WebGL is on the way a desktop Chrome without a GPU has it: SwiftShader (software
    # GL inside the browser, no GPU needed). That enables a feature every normal Chrome has. The stated OS is the machine's own, Linux
    # (the identity above); the CPU, the language and the time zone are the machine's own, and the GPU name is SwiftShader's (never
    # faked as a real one; a host render node replaces it). navigator.webdriver is
    # already false when the browser is driven over DevTools like this; --disable-blink-features=AutomationControlled keeps the
    # rest of Blink's automation mode off as well, as on a desktop Chrome a person uses.
    # A real GPU: when the host passed its render node in (tools/gpu_override.py writes compose.override.yml on Linux hosts that
    # have one), Chrome uses that driver and no software GL is named; without a node the SwiftShader line stays (no fake name).
    if ls /dev/dri/renderD* >/dev/null 2>&1; then SWIFT_FLAGS=""; else SWIFT_FLAGS="--use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist"; fi
    # shellcheck disable=SC2086
    exec browser \
      --no-sandbox --no-first-run --no-default-browser-check \
      --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
      --user-data-dir=/data/profile \
      --window-size=1440,900 --start-maximized --disable-dev-shm-usage \
      --kiosk --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
      $SWIFT_FLAGS \
      --disable-blink-features=AutomationControlled \
      --blink-settings=primaryPointerType=4,availablePointerTypes=4,primaryHoverType=2,availableHoverTypes=2 \
      $CACHE_FLAG $PROXY_FLAG $LANG_FLAGS $RESTORE_FLAG \
      ${UA_FLAG:+"$UA_FLAG"} \
      about:blank
    ;;
  *)
    echo "ROLE must be 'server', 'browser', 'egress', 'vpn', 'keyring' or 'voice' (got '${ROLE:-}')" >&2
    exit 64
    ;;
esac
