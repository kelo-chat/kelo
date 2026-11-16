import Testing
import Foundation
import KeloCore
@testable import KeloShared

/// What the build writes into Info.plist (KeloSourceCommit / KeloSourceDirty) and what the version line in My makes of it.
@Suite struct AppSourceTests {
    let sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

    @Test func aCleanBuildHasTheCommitAndAUrl() {
        let s = AppSource.parse(["KeloSourceCommit": sha, "KeloSourceDirty": "NO"])
        #expect(s == AppSource(commit: sha, dirty: false))
        #expect(s.url?.absoluteString == "https://github.com/kelo-chat/kelo/tree/\(sha)")
    }

    @Test func aModifiedBuildHasNoUrl() {
        for yes in ["YES", "yes", "true", "1"] {
            let s = AppSource.parse(["KeloSourceCommit": sha, "KeloSourceDirty": yes])
            #expect(s.dirty, "\(yes)")
            #expect(s.url == nil, "\(yes)")
        }
        #expect(AppSource.parse(["KeloSourceCommit": sha, "KeloSourceDirty": true]).dirty)
    }

    @Test func aBuildThatWasNotGivenTheCommitShowsNothing() {
        // not in the plist at all, empty (project.yml's default), or the setting never expanded
        for info in [[:], ["KeloSourceCommit": "", "KeloSourceDirty": "NO"], ["KeloSourceCommit": "$(KELO_SOURCE_COMMIT)", "KeloSourceDirty": "$(KELO_SOURCE_DIRTY)"]] as [[String: Any]] {
            let s = AppSource.parse(info)
            #expect(s.url == nil)
            #expect(SourceLink.label(commit: s.commit, dirty: s.dirty, modified: "modified") == nil)
            #expect(!s.dirty)
        }
        #expect(AppSource.parse(nil) == AppSource(commit: "", dirty: false))
    }

    @Test func spacesAroundTheValuesAreIgnored() {
        #expect(AppSource.parse(["KeloSourceCommit": " \(sha)\n", "KeloSourceDirty": " NO "]) == AppSource(commit: sha, dirty: false))
    }
}
