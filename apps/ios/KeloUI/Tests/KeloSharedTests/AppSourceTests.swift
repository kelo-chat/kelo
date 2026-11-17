import Testing
import Foundation
import KeloCore
@testable import KeloShared

/// What the build writes into Info.plist (CFBundleShortVersionString / KeloSourceDirty) and what the version line in My makes of it.
@Suite struct AppSourceTests {
    @Test func aCleanBuildLinksTheReleaseTag() {
        let s = AppSource.parse(["CFBundleShortVersionString": "1.0.2", "KeloSourceDirty": "NO"])
        #expect(s == AppSource(version: "1.0.2", dirty: false))
        #expect(s.tag == "ios-1.0.2")
        #expect(s.url?.absoluteString == "https://github.com/kelo-chat/kelo/tree/ios-1.0.2")
    }

    @Test func aModifiedBuildHasNoUrlButKeepsTheLabel() {
        for yes in ["YES", "yes", "true", "1"] {
            let s = AppSource.parse(["CFBundleShortVersionString": "1.0.2", "KeloSourceDirty": yes])
            #expect(s.dirty, "\(yes)")
            #expect(s.url == nil, "\(yes)")
            #expect(SourceLink.label(tag: s.tag, dirty: s.dirty, modified: "modified") == "ios-1.0.2 (modified)")
        }
        #expect(AppSource.parse(["CFBundleShortVersionString": "1.0.2", "KeloSourceDirty": true]).dirty)
    }

    @Test func aMissingOrUnexpandedVersionShowsNothing() {
        for info in [[:], ["CFBundleShortVersionString": "", "KeloSourceDirty": "NO"], ["CFBundleShortVersionString": "$(MARKETING_VERSION)"]] as [[String: Any]] {
            let s = AppSource.parse(info)
            #expect(s.url == nil)
            #expect(SourceLink.label(tag: s.tag, dirty: s.dirty, modified: "modified") == nil)
        }
        #expect(AppSource.parse(nil) == AppSource(version: "", dirty: false))
    }

    @Test func spacesAroundTheValuesAreIgnored() {
        #expect(AppSource.parse(["CFBundleShortVersionString": " 1.0.2\n", "KeloSourceDirty": " NO "]) == AppSource(version: "1.0.2", dirty: false))
    }
}
