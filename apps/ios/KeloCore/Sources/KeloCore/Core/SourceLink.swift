/// The links from the version lines in My to the public source on GitHub (docs/versioning.md). They point at a RELEASE TAG of the
/// public repositories, never at a commit: the public repo has one commit per release, so a private development commit would be a 404.
/// The iPhone app's tag is `ios-<marketing version>` (CFBundleShortVersionString) in kelo-chat/kelo, the server's is
/// `release-<version>` in kelo-chat/kelo-server (the `version` of GET /api/about). A build from changed files (`dirty`) has no link.
/// The same rule and test vectors as the Android and desktop apps (apps/shared/.../core/SourceLink.kt).
public enum SourceLink {
    public static let repo = "https://github.com/kelo-chat/kelo"
    public static let serverRepo = "https://github.com/kelo-chat/kelo-server"

    /// `<platform>-<version>` ("ios-1.0.2"); nil when the version is not digits separated by dots (empty, unexpanded, "dev", "1.0.2-x").
    public static func appTag(platform: String, version: String) -> String? {
        isVersion(version) ? "\(platform)-\(version)" : nil
    }

    /// The release page of an app tag; nil for a modified build or a missing tag.
    public static func url(tag: String?, dirty: Bool) -> String? {
        guard let tag, !dirty else { return nil }
        return "\(repo)/tree/\(tag)"
    }

    /// What the version line shows: "ios-1.0.2", or "ios-1.0.2 (modified)" when dirty; `modified` is the app's word for it (my_source_modified).
    public static func label(tag: String?, dirty: Bool, modified: String) -> String? {
        guard let tag else { return nil }
        return dirty ? "\(tag) (\(modified))" : tag
    }

    /// The server's tag, "release-0.10.2"; nil when the server's version is not digits separated by dots.
    public static func serverTag(version: String) -> String? {
        isVersion(version) ? "release-\(version)" : nil
    }

    /// The server's release page.
    public static func serverUrl(version: String) -> String? {
        serverTag(version: version).map { "\(serverRepo)/tree/\($0)" }
    }

    private static func isVersion(_ s: String) -> Bool {
        let parts = s.utf8.split(separator: 0x2e, omittingEmptySubsequences: false)
        return !parts.isEmpty && parts.allSatisfy { !$0.isEmpty && $0.allSatisfy { $0 >= 0x30 && $0 <= 0x39 } }
    }
}
