package chat.kelo.app.core

import chat.kelo.app.BuildConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The version line links by release tag (shared SourceLink): android-<versionName>. BuildConfig.SOURCE_COMMIT is empty unless the build
 * was given -PkeloCommit (app/build.gradle.kts), so a build of the public tag has the same bytes as the release.
 */
class SourceCommitBuildTest {
    @Test fun theBuildSaysNothingOrAFullSha() {
        val c = BuildConfig.SOURCE_COMMIT
        assertTrue("SOURCE_COMMIT is '$c'", c.isEmpty() || Regex("[0-9a-f]{40}").matches(c))
    }

    @Test fun theLinkIsTheReleaseTag() {
        val tag = SourceLink.appTag("android", BuildConfig.VERSION_NAME)
        assertEquals("android-${BuildConfig.VERSION_NAME}", tag)
        if (BuildConfig.SOURCE_DIRTY) assertNull(SourceLink.url(tag, true))
        else assertEquals("${SourceLink.REPO}/tree/$tag", SourceLink.url(tag, false))
    }
}
