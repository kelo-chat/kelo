#!/bin/sh
# Runs KeloUI's tests. With Xcode installed this is just `swift test`. With only the Command Line Tools, Swift Testing lives
# outside the compiler's default search path, and SwiftPM's generated test runner (which is not given the test target's flags)
# would build without it and silently run nothing, so the framework path is passed to every target here.
set -e
cd "$(dirname "$0")"
CLT=/Library/Developer/CommandLineTools/Library/Developer/Frameworks
if ! ls -d /Applications/Xcode*.app >/dev/null 2>&1 && [ -d "$CLT/Testing.framework" ]; then
  exec swift test -Xswiftc -F -Xswiftc "$CLT" "$@"
fi
exec swift test "$@"
