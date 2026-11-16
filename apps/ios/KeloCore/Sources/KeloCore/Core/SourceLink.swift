/// The link from the version line in My to the exact source of the build, on GitHub (docs/versioning.md). The build says which commit
/// the app's source came from (`commit`: 40 lowercase hex) and whether tracked files under the app's inputs differed from it
/// (`dirty`); a build from a source tarball has no commit (""). The same rule and test vectors as the Android and desktop apps
/// (apps/shared/.../core/SourceLink.kt).
public enum SourceLink {
    public static let repo = "https://github.com/kelo-chat/kelo"

    /// A page of this commit, only for a clean build of a full lowercase sha; nil otherwise (a modified build has no page to show).
    public static func url(commit: String, dirty: Bool) -> String? {
        !dirty && isFull(commit) ? "\(repo)/tree/\(commit)" : nil
    }

    /// The first seven hex of a valid commit; nil when the build did not say (empty, short, uppercase or not hex).
    public static func short(_ commit: String) -> String? {
        isFull(commit) ? String(commit.prefix(7)) : nil
    }

    /// What the version line shows: "a1b2c3d", or "a1b2c3d (modified)" when dirty; `modified` is the app's word for it (my_source_modified).
    public static func label(commit: String, dirty: Bool, modified: String) -> String? {
        guard let s = short(commit) else { return nil }
        return dirty ? "\(s) (\(modified))" : s
    }

    private static func isFull(_ commit: String) -> Bool {
        commit.utf8.count == 40 && commit.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x61 && $0 <= 0x66) }
    }
}
