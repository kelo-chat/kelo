#!/bin/zsh
# The public source of one released commit (owner 2026-11-15: kelo-src stays private for development; kelo-chat/kelo gets one
# commit per release, tagged by app and version, nothing unreleased): public_snapshot.sh OUT COMMIT. Never pushes. Then: rsync OUT
# into a kelo-chat/kelo checkout (keep its README.md, README.ko.md, docs/media), commit "Release …", tag android-X / ios-X /
# windows-X / macos-X / release-X (never v*: those tags start the image and release workflows), push.
set -eu
setopt null_glob   # the test globs below may match nothing in an older commit
out="$1"; commit="${2:-main}"; repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
rm -rf "$out"; mkdir -p "$out"
cd "$repo"
git archive "$commit" | tar -x -C "$out"
cd "$out"
for base in . apps; do   # before and after the apps/ move (2026-11-14)
  rm -rf $base/android/play/PLAY.md $base/android/play/console-answers.md
  rm -rf $base/ios/screens $base/desktop/screens $base/android/screens $base/android/kelo/screens
done
rm -rf docs/open-source-drafts tools/release/desktop_mirror.py
rm -f ./*-todo.md ./PROGRESS.md   # worker notes that slipped into a commit never go public
# the prompts export stays in the private repo only (owner 2026-11-19): the folder, its generator and its test
rm -rf prompts tools/prompts; rm -f server/tests/test_prompts_export.py
sed -i.bak '/^├── prompts\//d' docs/repo-layout.md && rm -f docs/repo-layout.md.bak
# the native (no Docker) macOS / Windows server was never published and was removed from main on 2026-11-18 (tag archive/native-server-2026-11-17); these lines keep older commits clean
for base in src server/src; do
  rm -rf $base/assistant/hosting/macos $base/assistant/hosting/windows
  rm -f $base/assistant/hosting/host.py $base/assistant/hosting/supervisor.py $base/assistant/hosting/cli.py $base/assistant/hosting/doctor.py
done
rm -f docs/native-server.md docs/android-device-option.md
# the Apple container (macOS) and WSL / MSI (Windows) server installs are not published yet either (2026-11-16)
for base in src server/src; do rm -rf $base/assistant/hosting/apple_container; done
rm -rf packaging/macos packaging/windows packaging/wsl
rm -f scripts/windows/kelo-wsl-install.ps1 scripts/windows/kelo-wsl-uninstall.ps1 scripts/windows/kelo-android-setup.ps1
rm -f tools/build/mac-server-pkg.sh tools/build/server-windows.ps1 tools/build/wsl-distro.sh
for base in tests server/tests; do
  rm -f $base/hosting_fakes.py $base/test_hosting_supervisor.py $base/test_hosting_macos.py $base/test_hosting_cli.py \
        $base/test_hosting_users.py $base/test_hosting_folders.py $base/test_hosting_seatbelt.py \
        $base/test_apple_container_*.py $base/test_hosting_windows_*.py $base/test_hosting_firewall.py \
        $base/test_mac_pkg.py $base/test_windows_*.py $base/test_wsl_distro.py \
        $base/test_hosting_browser_setup.py $base/test_hosting_memory.py $base/test_hosting_system_tool.py
done
[ -f pyproject.toml ] && sed -i '' '/^kelo-server = /d' pyproject.toml
grep -rlI "192\.168\.0\.101" . | while read -r f; do sed -i '' 's/192\.168\.0\.101/192.168.1.50/g' "$f"; done || true
echo "exported $commit: $(find . -type f | wc -l | tr -d ' ') files"
