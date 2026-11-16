# The build environment of the Kelo Android app, pinned: the same tools wherever it is built (a laptop, GitHub Actions, yours).
# Build it with tools/build/android-reproducible.sh (it does `docker build --platform linux/amd64`); nothing here needs the network
# after the image exists except the Gradle dependencies, which gradle/verification-metadata.xml checks byte for byte.
#
# What is pinned and how:
#   - the Linux base image by its digest (Temurin JDK 17.0.21+9 on Ubuntu 22.04)
#   - the Android platform 36 (r02) and the build tools 36.0.0 by the SHA-256 of their zip files
#   - Gradle 8.11.1 by distributionSha256Sum in gradle/wrapper/gradle-wrapper.properties (the wrapper checks it)
#   - the Android Gradle plugin, Kotlin and every library by version in gradle/libs.versions.toml, and each file by SHA-256 in
#     gradle/verification-metadata.xml
# There is no NDK on purpose: with an NDK the build would strip native libraries, without it they are kept as they are. Both are
# deterministic, but the two must not be mixed between the owner's build and yours.
#
# linux/amd64 only: Google ships the build tools (aapt2, zipalign, ...) for x86-64 Linux. On an Apple Silicon Mac Docker runs the
# image through emulation (slow: allow a few hours); on GitHub's ubuntu-latest it runs natively.
FROM --platform=linux/amd64 eclipse-temurin:17.0.21_9-jdk-jammy@sha256:057a0daec60e87c96571bf5c9c7792065bcb19e804d584cd37d4144038a35717

ARG PLATFORM_URL=https://dl.google.com/android/repository/platform-36_r02.zip
ARG PLATFORM_SHA256=37607369a28c5b640b3a7998868d45898ebcb777565a0e85f9acf36f29631d2e
ARG BUILD_TOOLS_URL=https://dl.google.com/android/repository/build-tools_r36_linux.zip
ARG BUILD_TOOLS_SHA256=5d9ac77fb6ff43d9da518a337b4fcf8f9097113df531d99ccefe80ef7ce8250b

ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p "$ANDROID_HOME/platforms" "$ANDROID_HOME/build-tools" \
 && cd /tmp \
 && curl -fsSL -o platform.zip "$PLATFORM_URL" \
 && echo "$PLATFORM_SHA256  platform.zip" | sha256sum -c - \
 && unzip -q platform.zip -d platform && mv platform/android-* "$ANDROID_HOME/platforms/android-36" \
 && curl -fsSL -o build-tools.zip "$BUILD_TOOLS_URL" \
 && echo "$BUILD_TOOLS_SHA256  build-tools.zip" | sha256sum -c - \
 && unzip -q build-tools.zip -d build-tools && mv build-tools/android-* "$ANDROID_HOME/build-tools/36.0.0" \
 && rm -rf /tmp/platform* /tmp/build-tools* \
 && chmod -R a+rX "$ANDROID_HOME"

# the user the build runs as is chosen at run time (docker run -u): it needs a home that exists and is writable
ENV HOME=/tmp/home \
    ANDROID_USER_HOME=/tmp/home/.android
RUN mkdir -p "$HOME" && chmod 1777 "$HOME"

COPY build-in-container.sh /usr/local/bin/kelo-build
RUN chmod 0755 /usr/local/bin/kelo-build

WORKDIR /build/apps/android/kelo
ENTRYPOINT ["/usr/local/bin/kelo-build"]
