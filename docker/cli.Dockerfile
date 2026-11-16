# Sidecar image for ONE subscription CLI. Build a target: `claude` or `agy` (see compose.yml profiles).
# It runs assistant.cli_runner (port 9300): the server sends it chat requests, it runs the CLI.
# No model keys, no ASSISTANT_TOKEN: only the shared CLI_RUNNER_TOKEN.
# The CLI is installed at build time and always fetched fresh: update with `assistant update`
# (= rebuild with --pull --no-cache). The login lives in a named volume, so it survives a rebuild.
# The owner can also press 업데이트 in the app: the runner installs a newer CLI into that same volume
# (/home/cli/.kelo-cli/<cli>/<version>, at most two kept) and uses the newer of that copy and this image's (cli_update.py).

# --- claude: official npm package, installed with the node binary copied alongside (no apt node)
FROM node:22-slim AS claude-npm
# lets updates delete only Kelo's own unused images (updates.prune_untagged)
LABEL chat.kelo.image="1"
RUN npm install -g --prefix /opt/claude @anthropic-ai/claude-code

# --- shared base: python + this project's code
FROM python:3.12-slim-bookworm AS base
# lets updates delete only Kelo's own unused images (updates.prune_untagged)
LABEL chat.kelo.image="1"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1002 cli
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY server/src ./server/src
RUN uv sync --frozen --no-dev
EXPOSE 9300
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "--factory", "assistant.cli_runner:app_factory", "--host", "0.0.0.0", "--port", "9300", "--no-server-header"]

FROM base AS claude
# lets updates delete only Kelo's own unused images (updates.prune_untagged)
LABEL chat.kelo.image="1"
COPY --from=claude-npm /usr/local/bin/node /usr/local/bin/node
COPY --from=claude-npm /opt/claude /opt/claude
# npm itself, for the in-app update (npm install -g --prefix /home/cli/.kelo-cli/claude/<version>; root stays read-only)
COPY --from=claude-npm /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
ENV PATH=/opt/claude/bin:$PATH CLI_PROVIDER=claude DISABLE_AUTOUPDATER=1
USER cli

# Google's installer (https://antigravity.google/cli/install.sh): downloads the agy binary, verifies its
# SHA-512 against a manifest from the same host, installs into /opt/agy/bin. The installer itself is pinned by its SHA-256 (like every
# other download here), so a changed script stops the build instead of running as root. When Google changes it: read the new script,
# then update AGY_INSTALLER_SHA256 (shasum -a 256 install.sh).
FROM base AS agy
# lets updates delete only Kelo's own unused images (updates.prune_untagged)
LABEL chat.kelo.image="1"
ARG AGY_INSTALLER_SHA256=62966c07365423bd4dc209355060744058fb30d60f5323e2d360e39de64e5042
RUN curl -fsSL -o /tmp/agy-install.sh https://antigravity.google/cli/install.sh \
 && echo "${AGY_INSTALLER_SHA256}  /tmp/agy-install.sh" | sha256sum -c - \
 && bash /tmp/agy-install.sh -d /opt/agy/bin && rm -f /tmp/agy-install.sh
ENV PATH=/opt/agy/bin:$PATH CLI_PROVIDER=agy
USER cli
