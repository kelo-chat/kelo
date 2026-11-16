# The Android bridge (docs/android-device.md): adb + a small HTTP API (bridge.py), nothing else. Built from this folder:
#   docker build -f docker/android/Dockerfile docker/android
FROM python:3.12-slim

# adb only (no Android SDK); no recommended packages keeps the image small
RUN apt-get update \
 && apt-get install -y --no-install-recommends adb \
 && rm -rf /var/lib/apt/lists/*

# not root; ~/.android keeps adb's key, so a device that was paired once (wireless debugging) stays paired (a volume in compose.yml)
RUN useradd --uid 1006 --create-home --home-dir /home/bridge --shell /usr/sbin/nologin bridge \
 && mkdir -p /home/bridge/.android && chown -R bridge:bridge /home/bridge

COPY bridge.py /app/bridge.py
USER bridge
ENV HOME=/home/bridge PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python", "/app/bridge.py"]
