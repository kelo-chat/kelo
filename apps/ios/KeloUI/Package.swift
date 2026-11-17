// swift-tools-version:5.10
// KeloUI: the Kelo iPhone app's screens (SwiftUI) on top of KeloCore. The Xcode app target (apps/ios/Kelo, generated from
// project.yml) is only the @main entry, Info.plist and assets; everything else is here so it can be type-checked with
// `swift build` on a Mac without Xcode (against the macOS SDK; UIKit-only bits sit behind `#if os(iOS)`), and its logic tested
// with `./test.sh`.
//
// Targets (one per area, so each can be built alone: `swift build --target KeloChat`):
//   KeloShared      design system, strings (20 languages), app state, session store, router, the character, platform shims
//   KeloChat        채팅: messages, streaming replies, tool chips, approvals, quick replies, attachments, model sheet, sign-in
//   KeloMy          마이: models, usage, keychain (vault) with Face ID, documents, devices, notifications, settings, phone data
//   KeloWork        작업 page + 다짐 (goals) + schedule editor
//   KeloConnectors  커넥터 list and connect panels
//   KeloShell       start (language, connect, QR, onboarding), the main shell (floating header, tab bar), KeloRootView
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

let core: Target.Dependency = .product(name: "KeloCore", package: "KeloCore")
let live: Target.Dependency = .product(name: "KeloLive", package: "KeloCore")   // the Live Activity of a running reply (backlog 64b)

let package = Package(
    name: "KeloUI",
    defaultLocalization: "en",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "KeloUI", targets: ["KeloUI"]),
        .executable(name: "kelo-icon", targets: ["KeloIconTool"]),
    ],
    dependencies: [
        .package(path: "../KeloCore"),
    ],
    targets: [
        .target(name: "KeloShared", dependencies: [core], resources: [.process("Resources")]),
        // fakes the page-model tests share (a routed fake server, a gate); not part of the app
        .target(name: "KeloTestSupport", dependencies: [core, "KeloShared"]),
        .target(name: "KeloChat", dependencies: [core, live, "KeloShared"]),
        .target(name: "KeloMy", dependencies: [core, "KeloShared"]),
        // Resources/vnc: noVNC 1.5.0 and Kelo's page for the live browser screen (the same folder as the Android app's assets/vnc)
        .target(name: "KeloWork", dependencies: [core, "KeloShared"], resources: [.copy("Resources/vnc")]),
        .target(name: "KeloConnectors", dependencies: [core, "KeloShared"]),
        .target(name: "KeloShell", dependencies: [core, live, "KeloShared", "KeloChat", "KeloMy", "KeloWork", "KeloConnectors"]),
        // the module the app imports: re-exports KeloShell
        .target(name: "KeloUI", dependencies: ["KeloShell"]),
        // renders the app icon with the app's own character renderer (macOS tool; not part of the iPhone app)
        .executableTarget(name: "KeloIconTool", dependencies: ["KeloShared"]),
        .testTarget(name: "KeloSharedTests", dependencies: [core, "KeloShared", "KeloTestSupport"], swiftSettings: testSwift, linkerSettings: testLink),
        .testTarget(name: "KeloChatTests", dependencies: [core, "KeloShared", "KeloTestSupport", "KeloChat"], swiftSettings: testSwift, linkerSettings: testLink),
        .testTarget(name: "KeloMyTests", dependencies: [core, "KeloShared", "KeloTestSupport", "KeloMy"], swiftSettings: testSwift, linkerSettings: testLink),
        .testTarget(name: "KeloWorkTests", dependencies: [core, "KeloShared", "KeloTestSupport", "KeloWork"], swiftSettings: testSwift, linkerSettings: testLink),
        .testTarget(name: "KeloShellTests", dependencies: [core, "KeloShared", "KeloTestSupport", "KeloShell"], swiftSettings: testSwift, linkerSettings: testLink),
        .testTarget(name: "KeloConnectorsTests", dependencies: [core, "KeloShared", "KeloTestSupport", "KeloConnectors"], swiftSettings: testSwift, linkerSettings: testLink),
    ]
)
