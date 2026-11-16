package chat.kelo.app.core

import chat.kelo.app.BuildConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The commit the build says it came from (BuildConfig.SOURCE_COMMIT, set in app/build.gradle.kts from -PkeloCommit or git) is either
 * absent ("": a source tarball) or a full lowercase sha, and the link follows it (shared SourceLink). The Gradle checks with and without
 * -PkeloCommit are in the notes of the change that added it.
 */
class SourceCommitBuildTest {
    @Test fun theBuildSaysNothingOrAFullSha() {
        val c = BuildConfig.SOURCE_COMMIT
        assertTrue("SOURCE_COMMIT is '$c'", c.isEmpty() || Regex("[0-9a-f]{40}").matches(c))
    }

    @Test fun theLinkFollowsTheBuild() {
        val c = BuildConfig.SOURCE_COMMIT
        if (c.isEmpty() || BuildConfig.SOURCE_DIRTY) assertNull(SourceLink.url(c, BuildConfig.SOURCE_DIRTY))
        else assertEquals("${SourceLink.REPO}/tree/$c", SourceLink.url(c, false))
        assertNotNull(BuildConfig.VERSION_NAME)
    }
}
