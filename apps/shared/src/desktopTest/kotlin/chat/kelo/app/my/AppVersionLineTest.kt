package chat.kelo.app.my

import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.runComposeUiTest
import chat.kelo.app.ui.theme.KeloTheme
import org.junit.After
import org.junit.Before
import java.util.Locale
import kotlin.test.Test
import kotlin.test.assertEquals

/** The version line at the bottom of My: the release tags are links to their source on GitHub for a clean build (all three apps). */
@OptIn(ExperimentalTestApi::class)
class AppVersionLineTest {
    private val saved = Locale.getDefault()
    @Before fun before() { Locale.setDefault(Locale.ENGLISH) }
    @After fun after() { Locale.setDefault(saved) }

    @Test fun theTagsAreLinksThatOpenTheReleasesInTheBrowser() = runComposeUiTest {
        val opened = mutableListOf<String>()
        setContent { KeloTheme(dark = false) { AppVersionLine("0.9.3", "0.9.2", "android-0.9.3", sourceDirty = false, openUrl = { opened += it }) } }
        onNodeWithText("App version: 0.9.3 ·", substring = true).assertExists()
        onNodeWithText("Server version: 0.9.2 ·", substring = true).assertExists()
        onAllNodes(hasContentDescription("Source code of this version on GitHub")).assertCountEquals(2)
        onNodeWithText("android-0.9.3").performClick()
        onNodeWithText("release-0.9.2").performClick()
        assertEquals(
            listOf("https://github.com/kelo-chat/kelo/tree/android-0.9.3", "https://github.com/kelo-chat/kelo-server/tree/release-0.9.2"), opened,
        )
    }

    @Test fun aModifiedBuildShowsTheTagWithoutALink() = runComposeUiTest {
        val opened = mutableListOf<String>()
        setContent { KeloTheme(dark = false) { AppVersionLine("0.9.3", null, "android-0.9.3", sourceDirty = true, openUrl = { opened += it }) } }
        onNodeWithText("android-0.9.3 (modified)").performClick()
        assertEquals(emptyList<String>(), opened)
        onAllNodesWithText("android-0.9.3 (modified)").assertCountEquals(1)
        onAllNodes(hasContentDescription("Source code of this version on GitHub")).assertCountEquals(0)
    }

    @Test fun aBuildWithoutATagAndAServerWithoutAVersionTagAddNothing() = runComposeUiTest {
        setContent { KeloTheme(dark = false) { AppVersionLine("0.9.3", "dev", null, sourceDirty = false, openUrl = {}) } }
        onNodeWithText("App version: 0.9.3").assertExists()
        onNodeWithText("Server version: dev").assertExists()
        onAllNodesWithText("·", substring = true).assertCountEquals(0)
    }
}
