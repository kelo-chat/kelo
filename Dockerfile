# One image, two roles (ROLE=server | ROLE=browser). Multi-arch: linux/amd64, linux/arm64.
#
# Default browser is Debian's Chromium (redistributable; presents as the matching Chrome). BROWSER=chrome downloads Google Chrome (amd64, arm64)
# at build time on the user's own machine; never publish an image built that way.
FROM python:3.12-slim-bookworm

ARG TARGETARCH
ARG BROWSER=chromium

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tini procps socat dbus-x11 poppler-utils \
      xvfb openbox x11vnc novnc websockify \
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
      apt-get update; apt-get install -y --no-install-recommends google-chrome-stable; \
      ln -s /usr/bin/google-chrome-stable /usr/local/bin/browser; \
    else \
      apt-get update; apt-get install -y --no-install-recommends chromium; \
      ln -s /usr/bin/chromium /usr/local/bin/browser; \
    fi; \
    echo "$kind" > /etc/kelo-browser; \
    rm -rf /var/lib/apt/lists/*

# Two unprivileged users so the browser role can never read the server role's files.
RUN useradd -m -u 1000 browser && useradd -m -u 1001 assistant \
    && mkdir -p /data /state /downloads /browser-downloads && chown browser:browser /data /downloads /browser-downloads \
    && chmod 755 /downloads /browser-downloads && chown assistant:assistant /state
# the folder shared with the work room (a volume; this sets its owner when the volume is new): group 1050 only
RUN mkdir -p /run/workroom && chown 1003:1050 /run/workroom && chmod 2770 /run/workroom

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
# the release version and build (docs/versioning.md); the server reads /app/version.json. A local build labels the image with it
# (tools/build/server.sh passes KELO_VERSION); release images get the same label from the release workflow, plus chat.kelo.release.
COPY version.json ./
ARG KELO_VERSION=""
LABEL org.opencontainers.image.version="${KELO_VERSION}"

COPY content /app/content
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
# Managed browser policy (docker/chromium-policy.json): read by Debian's Chromium and by Google Chrome; root-owned, so the browser
# (an unprivileged user) cannot change it.
COPY docker/chromium-policy.json /etc/chromium/policies/managed/kelo.json
COPY docker/chromium-policy.json /etc/opt/chrome/policies/managed/kelo.json

ENV DISPLAY=:99 SCREEN=1440x900x24 ASSISTANT_HOME=/state ASSISTANT_CONTENT_DIR=/app/content
USER assistant
EXPOSE 8080 6080
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
