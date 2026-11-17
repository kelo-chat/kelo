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

/// Where the app's source is, as the build wrote it into Info.plist: the marketing version (CFBundleShortVersionString, tag `ios-<version>`
/// in the public repo) and whether files differed from the commit it was built from (`KeloSourceDirty`, from KELO_SOURCE_DIRTY that
/// tools/build/ios.sh passes; apps/ios/Kelo/project.yml). The private commit is NOT embedded by default (KELO_SOURCE_COMMIT is empty
/// unless a public-source builder sets it), so a build of the public tag is the store build. An unexpanded "$(...)" has no tag.
public struct AppSource: Equatable, Sendable {
    public var version: String
    public var dirty: Bool

    public init(version: String, dirty: Bool) { self.version = version; self.dirty = dirty }

    public static var current: AppSource { parse(Bundle.main.infoDictionary) }

    public static func parse(_ info: [String: Any]?) -> AppSource {
        let version = ((info?["CFBundleShortVersionString"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let dirty: Bool
        switch info?["KeloSourceDirty"] {
        case let flag as Bool: dirty = flag
        case let text as String: dirty = ["yes", "true", "1"].contains(text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased())
        default: dirty = false
        }
        return AppSource(version: version, dirty: dirty)
    }

    /// `ios-<marketing version>`, or nil when the version is not a plain number.
    public var tag: String? { SourceLink.appTag(platform: "ios", version: version) }

    /// The release page on GitHub (a clean build), else nil.
    public var url: URL? { SourceLink.url(tag: tag, dirty: dirty).flatMap(URL.init(string:)) }
}
