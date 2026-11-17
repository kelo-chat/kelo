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
    socat TCP-LISTEN:5901,fork,reuseaddr TCP:127.0.0.1:5900 &
    # DevTools stays on loopback; socat republishes it on :9223 for other containers on the
    # private network. Compose never publishes this port.
    socat TCP-LISTEN:9223,fork,reuseaddr TCP:127.0.0.1:9222 &
    # A recreated container gets a new hostname; Chromium then treats the old SingletonLock in the profile
    # volume as "in use on another computer" and never opens the DevTools port. This container is the only
    # user of the profile, so the leftover lock is always stale.
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
    fi
    # Downloads (the server reads them through a read-only mount) are kept for a day.
    mkdir -p /downloads 2>/dev/null || true
    ( while true; do find /downloads -mindepth 1 -maxdepth 1 -type f -mmin +1440 -delete 2>/dev/null; sleep 1800; done ) &
    # Identity: the browser presents as Google Chrome. Google Chrome (BROWSER=chrome, amd64/arm64) simply is Chrome. A Chromium build
    # gets the matching Chrome release's User-Agent (flag, for requests made outside DevTools) and, from browser_identity.py, the same
    # User-Agent string plus Chrome's User-Agent Client Hints on every tab, popup, frame and worker.
    UA_FLAG=""
    if [ "$(cat /etc/kelo-browser 2>/dev/null || echo chromium)" != "chrome" ]; then
      full=$(browser --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+){3}' | head -n1)
      if [ -n "$full" ]; then
        UA_FLAG="--user-agent=$(python -m assistant.browser_identity --user-agent "$full")"
        ( while true; do python -m assistant.browser_identity; sleep 2; done ) &
      fi
    fi
    # --no-sandbox: Chrome's sandbox needs user namespaces the container does not grant. The
    # container (unprivileged, no capabilities, no host mounts, no network route but the proxy) is the sandbox instead.
    # Managed policy (/etc/chromium/policies/managed/kelo.json): no sync, sign-in, password manager, autofill, metrics or component
    # updates; notifications ask (nobody answers, so no site is ever granted); location denied; PDFs open in the browser. Headful on a
    # 1440x900 screen with a Chrome identity (above). WebGL is on the way a desktop Chrome without a GPU has it: SwiftShader (software
    # GL inside the browser, no GPU needed). That enables a feature every normal Chrome has; no value is faked. navigator.webdriver is
    # already false when the browser is driven over DevTools like this; --disable-blink-features=AutomationControlled keeps the
    # rest of Blink's automation mode off as well, as on a desktop Chrome a person uses.
    # shellcheck disable=SC2086
    exec browser \
      --no-sandbox --no-first-run --no-default-browser-check \
      --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
      --user-data-dir=/data/profile \
      --window-size=1440,900 --start-maximized --disable-dev-shm-usage \
      --kiosk --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
      --use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist \
      --disable-blink-features=AutomationControlled \
      $CACHE_FLAG $PROXY_FLAG $LANG_FLAGS \
      ${UA_FLAG:+"$UA_FLAG"} \
      about:blank
    ;;
  *)
    echo "ROLE must be 'server', 'browser', 'egress', 'vpn' or 'keyring' (got '${ROLE:-}')" >&2
    exit 64
    ;;
esac
