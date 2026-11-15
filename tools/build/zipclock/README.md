# zipclock: a fixed time for zip entries in the macOS app build

A Java agent (`ZipClock.java`, about 60 lines) that the macOS build (`tools/build/desktop.sh mac`, `tools/verify/mac-repro-check.sh`,
`tools/verify/mac-verify.sh`) starts Gradle with. It is not part of the app and is not used for any other build.

## Why

The Compose Desktop Gradle plugin (1.7.3, and still 1.12.2) re-writes **every jar** it packs into `Kelo.app/Contents/app` with
`java.util.zip.ZipOutputStream` and a `new ZipEntry(name)` that has no time, so the JDK stamps each entry with the clock and the
time zone of the build. It also re-writes the skiko jar (to take the native library out) and puts an MD5 of the file into the jar's
name (`skiko-awt-runtime-macos-arm64-0.8.18-<md5>.jar`, listed in `Kelo.cfg`). Nothing in the plugin sets that time. The result:
two builds of the same source differ in every jar and in the name of one, and so in `Kelo.cfg` and in the app's code signature
data. `tools/verify/mac-repro-check.sh` showed exactly that.

## What it does

It adds one line to the start of `ZipOutputStream.putNextEntry`: set the entry's time to 1980-02-01 00:00 (wall clock fields, so
the time zone does not matter), whether it had none or the plugin stamped it with the current time (as it does for some jna entries). The agent changes nothing else.

## How it runs

`kelo_zip_clock` in `tools/build/lib.sh` compiles it once with the pinned JDK (`desktop/packaging/macos/JDK`) into
`~/.cache/kelo/zipclock/` and gives Gradle `-Dorg.gradle.jvmargs="... --add-exports=java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED
-javaagent:<jar>"` (the JDK's own bundled ASM does the byte-code edit). Delete the folder and the next build compiles it again.

If a Compose plugin release sets the entry time itself, this folder and the `kelo_zip_clock` calls can go.
