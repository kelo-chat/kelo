import Testing
@testable import KeloCore

/// The same vectors as apps/shared/src/desktopTest/.../core/SourceLinkTest.kt (Android and desktop).
@Suite struct SourceLinkTests {
    @Test func aReleaseVersionGivesTheTagTheUrlAndTheLabel() {
        let tag = SourceLink.appTag(platform: "ios", version: "1.0.2")
        #expect(tag == "ios-1.0.2")
        #expect(SourceLink.appTag(platform: "android", version: "0.10.2") == "android-0.10.2")
        #expect(SourceLink.appTag(platform: "windows", version: "1.0.9") == "windows-1.0.9")
        #expect(SourceLink.url(tag: tag, dirty: false) == "https://github.com/kelo-chat/kelo/tree/ios-1.0.2")
        #expect(SourceLink.label(tag: tag, dirty: false, modified: "modified") == "ios-1.0.2")
    }

    @Test func aDirtyBuildHasNoUrlAndSaysModified() {
        let tag = SourceLink.appTag(platform: "ios", version: "1.0.2")
        #expect(SourceLink.url(tag: tag, dirty: true) == nil)
        #expect(SourceLink.label(tag: tag, dirty: true, modified: "modified") == "ios-1.0.2 (modified)")
        #expect(SourceLink.label(tag: tag, dirty: true, modified: "수정됨") == "ios-1.0.2 (수정됨)")
    }

    @Test func aVersionThatIsNotDigitsAndDotsGivesNothing() {
        for bad in ["", "dev", "1.0.2-rc1", "1..2", ".1", "1.", " 1.0.2", "1.0.2\n", "$(MARKETING_VERSION)", "v1.0.2", "1.0.2+3"] {
            #expect(SourceLink.appTag(platform: "ios", version: bad) == nil, "\(bad)")
            #expect(SourceLink.serverTag(version: bad) == nil, "\(bad)")
            #expect(SourceLink.serverUrl(version: bad) == nil, "\(bad)")
        }
        #expect(SourceLink.url(tag: nil, dirty: false) == nil)
        #expect(SourceLink.label(tag: nil, dirty: true, modified: "modified") == nil)
    }

    @Test func theServerLinksToItsReleaseTag() {
        #expect(SourceLink.serverTag(version: "0.10.2") == "release-0.10.2")
        #expect(SourceLink.serverUrl(version: "0.10.2") == "https://github.com/kelo-chat/kelo-server/tree/release-0.10.2")
        #expect(SourceLink.appTag(platform: "ios", version: "1") == "ios-1")
    }
}
