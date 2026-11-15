#!/bin/sh
# Makes Kelo.xcodeproj from project.yml (XcodeGen). Run again after adding or removing files in App/ or changing project.yml;
# files inside the Swift packages (../KeloUI, ../KeloCore) need no regeneration.
set -e
cd "$(dirname "$0")"
if ! command -v xcodegen >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then brew install xcodegen; else echo "Install XcodeGen first: brew install xcodegen" >&2; exit 1; fi
fi
python3 ../tools/gen_strings.py
xcodegen generate --spec project.yml
echo "Open $(pwd)/Kelo.xcodeproj in Xcode, choose your team under Signing & Capabilities, then Run."
