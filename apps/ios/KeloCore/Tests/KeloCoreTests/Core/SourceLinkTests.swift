import Testing
@testable import KeloCore

/// The same vectors as apps/shared/src/desktopTest/.../core/SourceLinkTest.kt (Android and desktop).
@Suite struct SourceLinkTests {
    let sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

    @Test func aCleanFullShaGivesTheUrlAndTheShortHash() {
        #expect(SourceLink.url(commit: sha, dirty: false) == "https://github.com/kelo-chat/kelo/tree/\(sha)")
        #expect(SourceLink.label(commit: sha, dirty: false, modified: "modified") == "a1b2c3d")
    }

    @Test func aDirtyBuildHasNoUrlAndSaysModified() {
        #expect(SourceLink.url(commit: sha, dirty: true) == nil)
        #expect(SourceLink.label(commit: sha, dirty: true, modified: "modified") == "a1b2c3d (modified)")
        #expect(SourceLink.label(commit: sha, dirty: true, modified: "수정됨") == "a1b2c3d (수정됨)")
    }

    @Test func anEmptyShortUppercaseOrNonHexValueGivesNothing() {
        let bad = ["", "a1b2c3d", String(sha.dropLast()), sha + "0", sha.uppercased(), String(repeating: "g", count: 40),
                   " " + String(sha.dropLast()), "$(KELO_SOURCE_COMMIT)"]
        for b in bad {
            #expect(SourceLink.url(commit: b, dirty: false) == nil, "\(b)")
            #expect(SourceLink.url(commit: b, dirty: true) == nil, "\(b)")
            #expect(SourceLink.short(b) == nil, "\(b)")
            #expect(SourceLink.label(commit: b, dirty: false, modified: "modified") == nil, "\(b)")
            #expect(SourceLink.label(commit: b, dirty: true, modified: "modified") == nil, "\(b)")
        }
    }
}
