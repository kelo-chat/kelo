#!/bin/bash
# Builds the arm64 kernel Kelo passes to the virtual Android phone's container (`container run -k`).
# Source: Apple's containerization kernel config (pinned below) + binder.fragment. Only the redroid container uses this kernel.
# Usage: tools/build/android-kernel/build.sh <output-dir>      (needs Apple `container`; ~30 min; writes <output-dir>/kernel)
set -euo pipefail
LINUX_VERSION=6.18.5
LINUX_SHA256=189d1f409cef8d0d234210e04595172df392f8cb297e14b447ed95720e2fd940
# Pinned commit of apple/containerization providing kernel/config-arm64
CONFIG_URL=https://raw.githubusercontent.com/apple/containerization/706cb512ccfc50fe27b575084c17a2964526be8e/kernel/config-arm64
CONFIG_SHA256=dedb03de6d686534efbca3a90bac304721e818bd96bc421b4d2213724a4e260b
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"
NAME="${KELO_KERNEL_BUILD_NAME:-kelotest-kbuild}"
IMAGE="${NAME}-image:1"

[ -f "$OUT/source.tar.xz" ] || curl -SsfL -o "$OUT/source.tar.xz" "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-${LINUX_VERSION}.tar.xz"
echo "${LINUX_SHA256}  $OUT/source.tar.xz" | shasum -a 256 -c -
[ -f "$OUT/config-arm64" ] || curl -SsfL -o "$OUT/config-arm64" "$CONFIG_URL"
echo "${CONFIG_SHA256}  $OUT/config-arm64" | shasum -a 256 -c -
cp "$HERE/binder.fragment" "$OUT/binder.fragment"

container build -t "$IMAGE" "$HERE"
container run --rm --name "$NAME" --cpus 4 --memory 8g -v "$OUT:/kernel" --cwd /kernel "$IMAGE" /bin/bash -c '
  set -euo pipefail
  mkdir -p /kbuild && tar -xf source.tar.xz -C /kbuild --strip-components=1
  cp config-arm64 /kbuild/.config
  cd /kbuild
  ./scripts/kconfig/merge_config.sh -m .config /kernel/binder.fragment
  make olddefconfig
  grep -E "^CONFIG_ANDROID_BINDER(FS|_IPC)=y" .config
  make -j"$(nproc)" LOCALVERSION=-kelo-android Image
  cp arch/arm64/boot/Image /kernel/kernel
'
echo "kernel: $OUT/kernel"
