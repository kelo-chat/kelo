// swift-tools-version:5.10
// KeloCore: everything of the Kelo iPhone app that is not UI (server API, pairing, rules, character motion).
// Builds and tests with the Command Line Tools alone: `swift build`, `swift test` (Swift Testing).
import PackageDescription
import Foundation

// The Command Line Tools ship Swift Testing outside the default search path; with Xcode installed none of this is needed.
let cltFrameworks = "/Library/Developer/CommandLineTools/Library/Developer/Frameworks"
let cltLib = "/Library/Developer/CommandLineTools/Library/Developer/usr/lib"
let needsCLTPath = FileManager.default.fileExists(atPath: cltFrameworks + "/Testing.framework")
    && !((try? FileManager.default.contentsOfDirectory(atPath: "/Applications")) ?? []).contains { $0.hasPrefix("Xcode") && $0.hasSuffix(".app") }
let testSwift: [SwiftSetting] = needsCLTPath ? [.unsafeFlags(["-F", cltFrameworks])] : []
let testLink: [LinkerSetting] = needsCLTPath
    ? [.unsafeFlags(["-F", cltFrameworks, "-framework", "Testing", "-Xlinker", "-rpath", "-Xlinker", cltFrameworks,
                     "-Xlinker", "-rpath", "-Xlinker", cltLib])] : []

let package = Package(
    name: "KeloCore",
    defaultLocalization: "en",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "KeloCore", targets: ["KeloCore"]),
        .library(name: "KeloLive", targets: ["KeloLive"]),
    ],
    targets: [
        .target(name: "KeloCore"),
        // the Live Activity's shared bits (attributes, sealed words, keys): the app and its widget extension both import it
        .target(name: "KeloLive"),
        .testTarget(name: "KeloCoreTests", dependencies: ["KeloCore", "KeloLive"], swiftSettings: testSwift, linkerSettings: testLink),
    ]
)
