#!/bin/bash
# Kelo server bundle for the Mac ("mac-server-bundle" row): Lima's limactl (vz driver), the Debian 12 arm64 VM image, Kelo's
# installer and sources, and the scripts the app runs. The Mac server runs Kelo's Docker images inside that VM (the same model as
# the Windows WSL distro). Apple silicon, macOS 14 or later. Design: docs/native-server.md ("Direction since 2026-11-17 (macOS)").
#
#   tools/build/mac-server-bundle.sh                    builds and signs dist/kelo-server-<v>-mac-arm64.zip
#   tools/build/mac-server-bundle.sh --dry-run          prints the commands only (downloads nothing, signs nothing)
#   tools/build/mac-server-bundle.sh --no-sign          a dev build (test part B only): the root stage refuses it
#   tools/build/mac-server-bundle.sh --update-lock [--write]
#                                                       downloads the pinned files and prints their sha256 / sha512; --write
#                                                       puts them into packaging/mac/*.lock (nothing else is changed)
#   tools/build/mac-server-bundle.sh --notarize         also submits the zip to Apple (the owner's OK each time)
#
# Options:
#   --version X.Y.Z     default: version.json
#   --dry-run           print the commands and stop
#   --no-sign           skip the Developer ID signature (dev builds only; never for release)
#   --notarize          submit to notarytool with the App Store Connect key (KELO_ASC_ENV, default ~/.appstoreconnect/kelo.env)
#   --update-lock       download and compute the pins (see above)
#   --write             with --update-lock: write the computed values into the locks
#   -h, --help          this help
#
# Every download is checked against its lock before it is used. A lock with an empty value refuses to build (run --update-lock).
# Secrets are never printed: the keychain password and the App Store Connect key stay in their files.

source "$(dirname "$0")/lib.sh"

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

VERSION=""; UPDATE_LOCK=0; WRITE=0; NO_SIGN=0; NOTARIZE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?--version needs X.Y.Z}"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    --no-sign) NO_SIGN=1 ;;
    --notarize) NOTARIZE=1 ;;
    --update-lock) UPDATE_LOCK=1 ;;
    --write) WRITE=1 ;;
    -h|--help) usage 0 ;;
    *) printf 'unknown option %s\n' "$1" >&2; usage 64 ;;
  esac
  shift
done

if [ "$WRITE" = 1 ] && [ "$UPDATE_LOCK" = 0 ]; then printf -- '--write needs --update-lock\n' >&2; exit 64; fi
if [ "$NO_SIGN" = 1 ] && [ "$NOTARIZE" = 1 ]; then printf -- '--no-sign and --notarize cannot be combined\n' >&2; exit 64; fi

cd "$REPO"
LOCK_DIR="packaging/mac"

lock_value() { sed -n "s/^$2=//p" "$REPO/$LOCK_DIR/$1" | head -1; }

LIMA_VERSION="$(lock_value lima.lock LIMA_VERSION)"
LIMA_URL="$(lock_value lima.lock LIMA_URL)"
LIMA_SHA256="$(lock_value lima.lock LIMA_SHA256)"
DEBIAN_IMAGE_URL="$(lock_value vm.lock DEBIAN_IMAGE_URL)"
DEBIAN_IMAGE_SHA512="$(lock_value vm.lock DEBIAN_IMAGE_SHA512)"
UV_VERSION="$(lock_value vm.lock UV_VERSION)"
UV_AARCH64_SHA256="$(lock_value vm.lock UV_AARCH64_SHA256)"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-unknown-linux-gnu.tar.gz"
DEBIAN_SUMS_URL="$(dirname "$DEBIAN_IMAGE_URL")/SHA512SUMS"
DEBIAN_IMAGE_NAME="debian-12-genericcloud-arm64.qcow2"   # the name inside the bundle (the image is the dated Debian file)

if [ "$UPDATE_LOCK" = 0 ]; then
  for value in LIMA_SHA256 DEBIAN_IMAGE_SHA512 UV_AARCH64_SHA256; do
    eval "v=\${$value}"
    [ -n "$v" ] || die "$value in packaging/mac is not pinned: run tools/build/mac-server-bundle.sh --update-lock --write first"
  done
fi

if [ -z "$VERSION" ]; then
  VERSION="$(python3 -c 'import json; print(json.load(open("version.json"))["version"])')"
fi
printf '%s\n' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || die "the version must be X.Y.Z, got $VERSION"

CACHE="${KELO_BUNDLE_CACHE:-$HOME/Library/Caches/Kelo/mac-server-bundle}"

# fetch URL FILE SHA ALGO: downloads FILE (once; a cached file is reused only when its checksum still matches) and checks it.
fetch() {
  local url="$1" file="$2" want="$3" algo="$4" got=""
  if [ -f "$file" ]; then
    got="$(shasum -a "$algo" "$file" | awk '{print $1}')"
    if [ "$got" = "$want" ]; then note "cached: $(basename "$file")"; return 0; fi
    rm -f "$file"
  fi
  note "downloading $(basename "$file")"
  mkdir -p "$(dirname "$file")"
  with_timeout 1800 curl -fsSL --retry 3 -o "$file.part" "$url" || { rm -f "$file.part"; die "download failed: $(basename "$file")"; }
  got="$(shasum -a "$algo" "$file.part" | awk '{print $1}')"
  if [ "$got" != "$want" ]; then rm -f "$file.part"; die "checksum mismatch for $(basename "$file") (expected $want, got $got)"; fi
  mv "$file.part" "$file"
}

# update_lock: downloads the pins (into the cache, so the build reuses them) and prints them; with --write it puts them into the locks.
update_lock() {
  local lima_tgz="$CACHE/$(basename "$LIMA_URL")" image="$CACHE/$(basename "$DEBIAN_IMAGE_URL")"
  local sums="$CACHE/SHA512SUMS-$(basename "$(dirname "$DEBIAN_IMAGE_URL")")" uv_tgz="$CACHE/$(basename "$UV_URL")"
  local lima_sha image_sha uv_sha expected
  mkdir -p "$CACHE"
  log "downloading the pinned files to compute their checksums (cache: $CACHE)"
  if [ ! -f "$lima_tgz" ]; then
    with_timeout 1800 curl -fsSL --retry 3 -o "$lima_tgz.part" "$LIMA_URL" || { rm -f "$lima_tgz.part"; die "Lima download failed"; }
    mv -f "$lima_tgz.part" "$lima_tgz"
  fi
  lima_sha="$(shasum -a 256 "$lima_tgz" | awk '{print $1}')"
  with_timeout 600 curl -fsSL --retry 3 -o "$sums" "$DEBIAN_SUMS_URL" || die "Debian SHA512SUMS download failed"
  expected="$(awk -v f="$(basename "$DEBIAN_IMAGE_URL")" '$2 == f {print $1}' "$sums" | head -1)"
  [ -n "$expected" ] || die "the Debian image is not listed in its SHA512SUMS"
  if [ ! -f "$image" ]; then
    with_timeout 1800 curl -fsSL --retry 3 -o "$image.part" "$DEBIAN_IMAGE_URL" || { rm -f "$image.part"; die "Debian image download failed"; }
    mv -f "$image.part" "$image"
  fi
  image_sha="$(shasum -a 512 "$image" | awk '{print $1}')"
  [ "$image_sha" = "$expected" ] || die "the Debian image does not match its SHA512SUMS"
  if [ ! -f "$uv_tgz" ]; then
    with_timeout 600 curl -fsSL --retry 3 -o "$uv_tgz.part" "$UV_URL" || { rm -f "$uv_tgz.part"; die "uv download failed"; }
    mv -f "$uv_tgz.part" "$uv_tgz"
  fi
  uv_sha="$(shasum -a 256 "$uv_tgz" | awk '{print $1}')"
  printf 'LIMA_SHA256=%s\nDEBIAN_IMAGE_SHA512=%s\nUV_AARCH64_SHA256=%s\n' "$lima_sha" "$image_sha" "$uv_sha"
  if [ "$WRITE" = 1 ]; then
    perl -pi -e "s/^LIMA_SHA256=.*/LIMA_SHA256=$lima_sha/" "$REPO/$LOCK_DIR/lima.lock"
    perl -pi -e "s/^DEBIAN_IMAGE_SHA512=.*/DEBIAN_IMAGE_SHA512=$image_sha/" "$REPO/$LOCK_DIR/vm.lock"
    perl -pi -e "s/^UV_AARCH64_SHA256=.*/UV_AARCH64_SHA256=$uv_sha/" "$REPO/$LOCK_DIR/vm.lock"
    note "written to $LOCK_DIR/lima.lock and $LOCK_DIR/vm.lock"
  else
    note "not written (add --write to put them into the locks)"
  fi
}

# build_bundle: assembles, signs and zips the bundle in a temp folder, then copies the zip to dist/.
build_bundle() {
  local work b zip identity sha size asc_env key_path key_id issuer
  [ "$(uname)" = Darwin ] || [ "$DRY_RUN" = 1 ] || die "the Mac bundle is built on a Mac"
  need_disk
  make_temp mac-bundle work
  b="$work/kelo-server"
  zip="$REPO/dist/kelo-server-$VERSION-mac-arm64.zip"

  if [ "$DRY_RUN" = 1 ]; then
    note "[dry-run] lima $LIMA_VERSION from $LIMA_URL (sha256 $LIMA_SHA256)"
    note "[dry-run] Debian image from $DEBIAN_IMAGE_URL (sha512 $DEBIAN_IMAGE_SHA512)"
    note "[dry-run] uv $UV_VERSION aarch64 (sha256 $UV_AARCH64_SHA256)"
    note "[dry-run] git archive HEAD -> kelo-server/share/kelo/kelo-src.tar.gz; scripts/install.sh -> share/kelo/install.sh"
    note "[dry-run] render packaging/mac/kelo-vm.yaml.in and provision.sh; copy packaging/mac/bin/* (mode 755); version.txt $VERSION"
  else
    mkdir -p "$b/bin" "$b/share/lima" "$b/share/kelo" "$b/images" "$b/licenses/lima"
    # 1. Lima: the binary and the guest agent (limactl looks in ../share/lima next to its bin folder)
    local lima_tgz="$CACHE/$(basename "$LIMA_URL")"
    fetch "$LIMA_URL" "$lima_tgz" "$LIMA_SHA256" 256
    tar -xzf "$lima_tgz" -C "$work" ./bin/limactl ./share/lima/lima-guestagent.Linux-aarch64.gz ./share/doc/lima/LICENSE
    cp "$work/bin/limactl" "$b/bin/limactl"
    cp "$work/share/lima/lima-guestagent.Linux-aarch64.gz" "$b/share/lima/"
    cp "$work/share/doc/lima/LICENSE" "$b/licenses/lima/LICENSE"
    if tar -xzf "$lima_tgz" -C "$work" ./share/doc/lima/NOTICE 2>/dev/null; then cp "$work/share/doc/lima/NOTICE" "$b/licenses/lima/NOTICE"; fi
    # 2. the VM image (Debian 12, the same release as the WSL distro)
    local image="$CACHE/$(basename "$DEBIAN_IMAGE_URL")"
    fetch "$DEBIAN_IMAGE_URL" "$image" "$DEBIAN_IMAGE_SHA512" 512
    cp -c "$image" "$b/images/$DEBIAN_IMAGE_NAME" 2>/dev/null || cp "$image" "$b/images/$DEBIAN_IMAGE_NAME"
    # 3. Kelo: the sources of HEAD (as the Windows distro gets them) and the installer
    if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then warn "tracked files differ from HEAD: the bundle has HEAD only"; fi
    git -C "$REPO" archive --format=tar.gz -o "$b/share/kelo/kelo-src.tar.gz" HEAD
    cp scripts/install.sh "$b/share/kelo/install.sh"
    # 4. the rendered Lima template and provisioning script, the bin scripts, the version
    local uv_sha="$UV_AARCH64_SHA256"
    python3 - "$REPO/$LOCK_DIR" "$b" "$DEBIAN_IMAGE_SHA512" "$UV_VERSION" "$uv_sha" <<'PY'
import pathlib, sys
mac, bundle, digest, uv_version, uv_sha = sys.argv[1:6]
mac, bundle = pathlib.Path(mac), pathlib.Path(bundle)
provision = (mac / "provision.sh").read_text().replace("@UV_VERSION@", uv_version).replace("@UV_AARCH64_SHA256@", uv_sha)
if "@" in provision.split("PROVISION_VERSION=", 1)[1].split("\n", 1)[0]:
    sys.exit("provision.sh has an unexpected placeholder")
(bundle / "share/kelo/provision.sh").write_text(provision)
body = "\n".join(("      " + line) if line else "" for line in provision.rstrip("\n").splitlines())
template = (mac / "kelo-vm.yaml.in").read_text().replace("@DEBIAN_IMAGE_SHA512@", digest)
if "      @PROVISION_SH@" not in template:
    sys.exit("kelo-vm.yaml.in has no @PROVISION_SH@ line")
(bundle / "share/kelo/kelo-vm.yaml").write_text(template.replace("      @PROVISION_SH@", body))
PY
    chmod 755 "$b/share/kelo/provision.sh"
    cp "$REPO/$LOCK_DIR"/bin/* "$b/bin/"
    chmod 755 "$b/bin/"*
    printf '%s\n' "$VERSION" > "$b/version.txt"
    # 5. signature: the one Mach-O in the bundle, with the hardened runtime and the three entitlements
    if [ "$NO_SIGN" = 0 ]; then
      SIGN_KC="$HOME/Library/Keychains/kelo-signing.keychain-db"
      if [ -f "$SIGN_KC" ] && [ -f "$HOME/.kelo-signing/keychain-pass" ]; then
        security unlock-keychain -p "$(cat "$HOME/.kelo-signing/keychain-pass")" "$SIGN_KC" 2>/dev/null || warn "could not unlock $SIGN_KC"
      fi
      identity="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)"
      [ -n "$identity" ] || die "no 'Developer ID Application' certificate in the keychain (a dev build: --no-sign)"
      codesign --force --options runtime --timestamp --entitlements packaging/mac/limactl.entitlements --sign "$identity" "$b/bin/limactl"
      codesign --verify --strict "$b/bin/limactl"
      codesign --verify --strict -R='anchor apple generic and certificate leaf[subject.OU] = "P6GD4DM9C5"' "$b/bin/limactl"
    else
      warn "--no-sign: a dev bundle, the root stage refuses it"
    fi
    # 6. the zip (ditto keeps the signature and the modes; the app downloads it without the quarantine flag)
    mkdir -p "$REPO/dist"
    (cd "$work" && ditto -c -k --keepParent kelo-server "$zip")
    sha="$(shasum -a 256 "$zip" | awk '{print $1}')"
    size="$(stat -f %z "$zip")"
    note "zip: $zip ($size bytes, sha256 $sha)"
  fi

  if [ "$DRY_RUN" = 1 ]; then
    run_or_print codesign --force --options runtime --timestamp --entitlements packaging/mac/limactl.entitlements --sign "<Developer ID Application identity>" "kelo-server/bin/limactl"
    run_or_print codesign --verify --strict "kelo-server/bin/limactl"
    run_or_print codesign --verify --strict -R='anchor apple generic and certificate leaf[subject.OU] = "P6GD4DM9C5"' "kelo-server/bin/limactl"
    run_or_print ditto -c -k --keepParent kelo-server "dist/kelo-server-$VERSION-mac-arm64.zip"
    note "[dry-run] the zip: dist/kelo-server-$VERSION-mac-arm64.zip"
  fi

  # 7. notarization: only with --notarize (the owner's OK each time); the zip itself cannot be stapled, and the app needs no stapling
  if [ "$NOTARIZE" = 1 ]; then
    asc_env="${KELO_ASC_ENV:-$HOME/.appstoreconnect/kelo.env}"
    key_id="$(sed -n 's/^KEY_ID=//p' "$asc_env" 2>/dev/null | tr -d "\"'")"
    issuer="$(sed -n 's/^ISSUER_ID=//p' "$asc_env" 2>/dev/null | tr -d "\"'")"
    key_path="$(sed -n 's/^KEY_PATH=//p' "$asc_env" 2>/dev/null | tr -d "\"'")"; key_path="${key_path/#\~/$HOME}"
    [ -n "$key_id" ] && [ -n "$issuer" ] && [ -f "$key_path" ] || die "no App Store Connect key in $asc_env"
    if [ "$DRY_RUN" = 1 ]; then
      run_or_print xcrun notarytool submit "dist/kelo-server-$VERSION-mac-arm64.zip" --key "<key file>" --key-id "<key id>" --issuer "<issuer>" --wait --timeout 50m
    else
      with_timeout 3600 xcrun notarytool submit "$zip" --key "$key_path" --key-id "$key_id" --issuer "$issuer" --wait --timeout 50m \
        || die "notarization failed"
    fi
  fi

  if [ "$DRY_RUN" = 0 ]; then
    printf '\nReady: %s\n' "$zip"
    printf 'Publish (owner only, after the notarization and the owner OK):\n'
    printf '  uv run assistant publish-app mac-server-bundle %s --commit <public commit>\n' "$zip"
  fi
}

if [ "$UPDATE_LOCK" = 1 ]; then
  if [ "$DRY_RUN" = 1 ]; then note "[dry-run] would download the pinned files and print their checksums"; exit 0; fi
  update_lock
else
  build_bundle
fi
