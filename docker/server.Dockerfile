# One image, several roles (ROLE=server | browser | egress | vpn). Multi-arch: linux/amd64, linux/arm64.
#
# Default browser is Debian's Chromium (redistributable; presents as the matching Chrome). BROWSER=chrome downloads Google Chrome (amd64, arm64)
# at build time on the user's own machine; never publish an image built that way.
FROM python:3.12-slim-bookworm
# lets updates delete only Kelo's own unused images (updates.prune_untagged)
LABEL chat.kelo.image="1"

ARG TARGETARCH
ARG BROWSER=chromium
# BROWSER=chrome: the exact Google Chrome build (the pool .deb version). Changing it is a decision with a rollback tag (review 2026-11-18:
# the 0.10.5 rebuild moved Chrome 157 -> 158 without anyone choosing it). The package is held after install (apt-mark hold).
# The Chrome OS platform version that shipped with each milestone, stated by the desktop identity (browser_identity.py
# CHROME_OS_PLATFORM_VERSIONS must have these rows; tests/test_browser_identity.py checks it). Source: chromiumdash, cros/fetch_serving_builds,
# read 2026-11-18 (Chrome 158 beta builds 158.0.8127.24 run ChromeOS 16868.13.0). A milestone with no row keeps the Linux identity.
# milestone  chromeos_platform_version
#   158      16868.13.0
ARG CHROME_VERSION=158.0.8127.36-1

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tini procps socat dbus-x11 poppler-utils \
      tigervnc-standalone-server openbox \
      fonts-liberation fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# BROWSER=chrome: Google Chrome stable from Google's signed apt repository (amd64 and arm64; Linux arm64 builds exist since 2026).
# On any other architecture, or with BROWSER=chromium, Debian's Chromium is installed instead and presents the same Chrome identity
# (assistant/browser_identity.py). /etc/kelo-browser records which one this image has.
RUN set -eux; \
    kind="$BROWSER"; arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    if [ "$kind" = "chrome" ]; then \
      case "$arch" in amd64|arm64) ;; *) echo "No Google Chrome build for $arch; installing Chromium with a Chrome identity" >&2; kind=chromium;; esac; \
    fi; \
    if [ "$kind" = "chrome" ]; then \
      install -d -m 0755 /etc/apt/keyrings; \
      curl -fsSL https://dl.google.com/linux/linux_signing_key.pub -o /etc/apt/keyrings/google-chrome.asc; \
      echo "deb [arch=$arch signed-by=/etc/apt/keyrings/google-chrome.asc] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list; \
      apt-get update; \
      curl -fsSL "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}_${arch}.deb" \
        -o /tmp/google-chrome.deb; \
      apt-get install -y --no-install-recommends /tmp/google-chrome.deb; \
      rm -f /tmp/google-chrome.deb; \
      apt-mark hold google-chrome-stable; \
      echo "$CHROME_VERSION" > /etc/kelo-chrome-version; \
      ln -s /usr/bin/google-chrome-stable /usr/local/bin/browser; \
    else \
      apt-get update; apt-get install -y --no-install-recommends chromium; \
      ln -s /usr/bin/chromium /usr/local/bin/browser; \
    fi; \
    echo "$kind" > /etc/kelo-browser; \
    rm -rf /var/lib/apt/lists/*

# wireproxy: WireGuard in user space for the `vpn` role (the owner's VPN connector). Pinned version and per-architecture SHA-256 in
# docker/wireproxy.lock; the download is refused when the hash does not match. No tun device and no capabilities are needed.
COPY docker/wireproxy.lock /tmp/wireproxy.lock
RUN set -eux; . /tmp/wireproxy.lock; arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64) sha="$WIREPROXY_SHA256_AMD64";; \
      arm64) sha="$WIREPROXY_SHA256_ARM64";; \
      *) echo "No wireproxy for $arch: the VPN (WireGuard) connector will not work on this image" >&2; rm -f /tmp/wireproxy.lock; exit 0;; \
    esac; \
    d="$(mktemp -d)"; \
    curl -fsSL -o "$d/wireproxy.tar.gz" "https://github.com/pufferffish/wireproxy/releases/download/v${WIREPROXY_VERSION}/wireproxy_linux_${arch}.tar.gz"; \
    echo "$sha  $d/wireproxy.tar.gz" | sha256sum -c -; \
    tar -xzf "$d/wireproxy.tar.gz" -C "$d" wireproxy; \
    install -m 0755 "$d/wireproxy" /usr/local/bin/wireproxy; \
    rm -rf "$d" /tmp/wireproxy.lock

# Two unprivileged users so the browser role can never read the server role's files.
RUN useradd -m -u 1000 browser && useradd -m -u 1001 assistant \
    && mkdir -p /data /state /downloads /browser-downloads && chown browser:browser /data /downloads /browser-downloads \
    && chmod 755 /downloads /browser-downloads && chown assistant:assistant /state
# the folder shared with the work room (a volume; this sets its owner when the volume is new): group 1050 only
# the VPN connector's two small volumes (set up here so a new volume gets these owners): the route file (public, egress reads it) and
# the VPN's files with keys (only the server writes, only the vpn container reads)
RUN mkdir -p /run/kelo-route /run/kelo-vpn && chown assistant:assistant /run/kelo-route /run/kelo-vpn \
    && chmod 755 /run/kelo-route && chmod 700 /run/kelo-vpn
# the key container (keyring_service.py): its own user and state folder (a new keyring-state volume gets this owner; /state belongs to the server's user)
RUN useradd -m -u 1005 keyring && mkdir -p /keyring-state && chown keyring:keyring /keyring-state && chmod 700 /keyring-state
RUN mkdir -p /run/workroom && chown 1003:1050 /run/workroom && chmod 2770 /run/workroom
# the voice container (voice_service.py): its own user; the Silero VAD model baked in, root-owned and checked by its hash (docker/voice.lock);
# the socket folder is shared only with the server (group 1051); the speech models come from the voice-models volume the server writes
COPY docker/voice.lock /tmp/voice.lock
RUN set -eux; . /tmp/voice.lock; mkdir -p /opt/voice; \
    curl -fsSL -o /opt/voice/silero_vad.onnx "$SILERO_VAD_URL"; \
    echo "$SILERO_VAD_SHA256  /opt/voice/silero_vad.onnx" | sha256sum -c -; \
    chmod 644 /opt/voice/silero_vad.onnx; rm -f /tmp/voice.lock
RUN groupadd -g 1051 voice-io && useradd -m -u 1006 -g voice-io voice \
    && mkdir -p /run/voice /voice-models && chown 1006:1051 /run/voice && chmod 2770 /run/voice \
    && chown assistant:assistant /voice-models && chmod 755 /voice-models

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY server/src ./server/src
RUN uv sync --frozen --no-dev
# the release version and build (docs/versioning.md); the server reads /app/version.json. A local build labels the image with it
# (tools/build/server.sh passes KELO_VERSION); release images get the same label from the release workflow, plus chat.kelo.release.
COPY version.json ./
# the record of released apps (releases/apps.json): the server refuses to enforce a minimum app version above it (docs/versioning.md)
COPY releases ./releases
# the one-line installers kelo.chat serves at /install.sh and /install.ps1 (api/site.py)
COPY scripts/install.sh scripts/install.ps1 ./scripts/
# the Windows one-line installer at /install-windows.ps1 (api/site.py)
COPY scripts/windows/kelo-get.ps1 ./scripts/windows/
ARG KELO_VERSION=""
LABEL org.opencontainers.image.version="${KELO_VERSION}"

COPY server/content /app/content
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
# Managed browser policy (docker/chromium-policy.json): read by Debian's Chromium and by Google Chrome; root-owned, so the browser
# (an unprivileged user) cannot change it.
COPY docker/chromium-policy.json /etc/chromium/policies/managed/kelo.json
COPY docker/chromium-policy.json /etc/opt/chrome/policies/managed/kelo.json

ENV DISPLAY=:99 SCREEN=1440x900x24 ASSISTANT_HOME=/state ASSISTANT_CONTENT_DIR=/app/content
USER assistant
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
