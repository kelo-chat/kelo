#!/bin/zsh
# The public source of one released commit (owner 2026-11-15: kelo-src stays private for development; kelo-chat/kelo gets one
# commit per release, tagged by app and version, nothing unreleased): public_snapshot.sh OUT COMMIT. Never pushes. Then: rsync OUT
# into a kelo-chat/kelo checkout (keep its README.md, README.ko.md, docs/media), commit "Release …", tag android-X / ios-X /
# windows-X / macos-X / release-X (never v*: those tags start the image and release workflows), push.
set -eu
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
# the native (no Docker) macOS / Windows server is not published yet (owner 2026-11-15)
for base in src server/src; do
  rm -rf $base/assistant/hosting/macos $base/assistant/hosting/windows
  rm -f $base/assistant/hosting/host.py $base/assistant/hosting/supervisor.py $base/assistant/hosting/cli.py $base/assistant/hosting/doctor.py
done
rm -f docs/native-server.md
for base in tests server/tests; do
  rm -f $base/hosting_fakes.py $base/test_hosting_supervisor.py $base/test_hosting_macos.py $base/test_hosting_cli.py \
        $base/test_hosting_users.py $base/test_hosting_folders.py $base/test_hosting_seatbelt.py
done
[ -f pyproject.toml ] && sed -i '' '/^kelo-server = /d' pyproject.toml
grep -rlI "192\.168\.0\.101" . | while read -r f; do sed -i '' 's/192\.168\.0\.101/192.168.1.50/g' "$f"; done || true
echo "exported $commit: $(find . -type f | wc -l | tr -d ' ') files"
