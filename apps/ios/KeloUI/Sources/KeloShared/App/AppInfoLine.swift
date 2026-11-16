import Foundation
import KeloCore

/// The app's version as people see it: Kelo's release from version.json (`KeloVersion.version`, the same on every app and in
/// `X-Kelo-App`) and the App Store build number (CFBundleVersion), "0.9.0 (26111011)". CFBundleShortVersionString is the App Store's
/// own marketing number and is not shown (docs/versioning.md).
public enum AppInfoLine {
    public static var current: String {
        line(version: KeloVersion.version, build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String)
    }

    public static func line(version: String, build: String?) -> String {
        let v = version.trimmingCharacters(in: .whitespaces), b = (build ?? "").trimmingCharacters(in: .whitespaces)
        if v.isEmpty { return b }
        return b.isEmpty || b == v ? v : "\(v) (\(b))"
    }
}

/// The commit the app's source came from and whether files differed from it, as the build wrote them into Info.plist
/// (`KeloSourceCommit` / `KeloSourceDirty`, from the build settings KELO_SOURCE_COMMIT / KELO_SOURCE_DIRTY that tools/build/ios.sh passes;
/// apps/ios/Kelo/project.yml). A build that was not given them has "" and "NO", or the unexpanded "$(KELO_SOURCE_COMMIT)", which `SourceLink` ignores.
public struct AppSource: Equatable, Sendable {
    public var commit: String
    public var dirty: Bool

    public init(commit: String, dirty: Bool) { self.commit = commit; self.dirty = dirty }

    public static var current: AppSource { parse(Bundle.main.infoDictionary) }

    public static func parse(_ info: [String: Any]?) -> AppSource {
        let commit = ((info?["KeloSourceCommit"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let dirty: Bool
        switch info?["KeloSourceDirty"] {
        case let flag as Bool: dirty = flag
        case let text as String: dirty = ["yes", "true", "1"].contains(text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
        default: dirty = false
        }
        return AppSource(commit: commit, dirty: dirty)
    }

    /// The page of this commit on GitHub (a clean build of a full sha), else nil.
    public var url: URL? { SourceLink.url(commit: commit, dirty: dirty).flatMap(URL.init(string:)) }
}
