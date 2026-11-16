# The work room: where the assistant runs code and makes documents, charts and media files. Debian (bookworm) with Python, Node,
# LibreOffice (headless), ffmpeg, poppler, ImageMagick and fonts for most of the world's scripts.
#
# It runs with no network at all (compose: network_mode none), as an unprivileged user, on a read-only root; only /work (scratch,
# emptied for every job) and /tools (packages the user approved, kept across updates) are writable. No keys, no ASSISTANT_TOKEN.
#
# System packages are part of this image, not installed at run time. To add one, put it in WORKROOM_APT_PACKAGES (the env file of
# the copy, space separated) and run `assistant update` (or `docker compose build workroom`): the image is rebuilt with it.
# Python and Node packages are installed at run time by the assistant, after the user approves (workroom_install), into /tools.
FROM node:22-slim AS node

FROM python:3.12-slim-bookworm
# lets updates delete only Kelo's own unused images (updates.prune_untagged)
LABEL chat.kelo.image="1"
ARG WORKROOM_APT_PACKAGES=""
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates tini procps jq ripgrep unzip zip git file \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      ffmpeg poppler-utils imagemagick \
      fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji fonts-noto-mono fonts-liberation2 fonts-dejavu-core \
      $WORKROOM_APT_PACKAGES \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

RUN pip install --no-cache-dir numpy pandas scipy matplotlib seaborn plotly openpyxl xlsxwriter python-docx python-pptx pypdf pillow \
      defusedxml pdfplumber pdf2image \
    && python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot" \
    && mkdir -p /opt/workroom && date -u +%Y%m%d%H%M%S > /opt/workroom/build

# 1003 = the work room's user; 1050 = the group it shares with the server for the socket folder only
RUN groupadd -g 1050 workroom-io && useradd -m -u 1003 -g 1050 work \
    && mkdir -p /work /tools /run/workroom && chown work:workroom-io /work /tools && chown 1003:1050 /run/workroom \
    && chmod 0700 /work /tools && chmod 2770 /run/workroom

COPY server/src/assistant/workroom_agent.py /opt/workroom/agent.py
# the agent imports assistant.hosting (standard library only): the package's __init__ and the hosting folder go beside it
COPY server/src/assistant/__init__.py /opt/workroom/assistant/__init__.py
COPY server/src/assistant/hosting/ /opt/workroom/assistant/hosting/
# offline helper scripts for documents (Word, Excel, PowerPoint, PDF); see server/content/skills/documents/
COPY server/content/workroom-tools /opt/kelo-tools
USER work
WORKDIR /work
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python3", "/opt/workroom/agent.py", "health"]
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/opt/workroom/agent.py"]
