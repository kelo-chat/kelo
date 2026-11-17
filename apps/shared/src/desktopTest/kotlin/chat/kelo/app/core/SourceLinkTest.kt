package chat.kelo.app.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/** The same vectors as apps/ios/KeloCore/Tests/KeloCoreTests/Core/SourceLinkTests.swift. */
class SourceLinkTest {
    @Test fun aReleaseVersionGivesTheTagTheUrlAndTheLabel() {
        val tag = SourceLink.appTag("ios", "1.0.2")
        assertEquals("ios-1.0.2", tag)
        assertEquals("android-0.10.2", SourceLink.appTag("android", "0.10.2"))
        assertEquals("windows-1.0.9", SourceLink.appTag("windows", "1.0.9"))
        assertEquals("macos-1.0.10", SourceLink.appTag("macos", "1.0.10"))
        assertEquals("https://github.com/kelo-chat/kelo/tree/ios-1.0.2", SourceLink.url(tag, dirty = false))
        assertEquals("ios-1.0.2", SourceLink.label(tag, dirty = false, modified = "modified"))
    }

    @Test fun aDirtyBuildHasNoUrlAndSaysModified() {
        val tag = SourceLink.appTag("ios", "1.0.2")
        assertNull(SourceLink.url(tag, dirty = true))
        assertEquals("ios-1.0.2 (modified)", SourceLink.label(tag, dirty = true, modified = "modified"))
        assertEquals("ios-1.0.2 (수정됨)", SourceLink.label(tag, dirty = true, modified = "수정됨"))
    }

    @Test fun aVersionThatIsNotDigitsAndDotsGivesNothing() {
        for (bad in listOf("", "dev", "1.0.2-rc1", "1..2", ".1", "1.", " 1.0.2", "1.0.2\n", "\$(MARKETING_VERSION)", "v1.0.2", "1.0.2+3")) {
            assertNull(SourceLink.appTag("ios", bad), bad)
            assertNull(SourceLink.serverTag(bad), bad)
            assertNull(SourceLink.serverUrl(bad), bad)
        }
        assertNull(SourceLink.url(null, dirty = false))
        assertNull(SourceLink.label(null, dirty = true, modified = "modified"))
    }

    @Test fun theServerLinksToItsReleaseTag() {
        assertEquals("release-0.10.2", SourceLink.serverTag("0.10.2"))
        assertEquals("https://github.com/kelo-chat/kelo-server/tree/release-0.10.2", SourceLink.serverUrl("0.10.2"))
        assertEquals("ios-1", SourceLink.appTag("ios", "1"))
    }
}
