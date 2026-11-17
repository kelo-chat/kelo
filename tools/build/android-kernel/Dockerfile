# Build image for the Android-capable container kernel (arm64, native build inside an Apple container).
FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    bc bison flex gcc make xz-utils libelf-dev libssl-dev openssl python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
