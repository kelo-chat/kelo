package chat.kelo.app.my

import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.runComposeUiTest
import chat.kelo.app.ui.theme.KeloTheme
import org.junit.After
import org.junit.Before
import java.util.Locale
import kotlin.test.Test
import kotlin.test.assertEquals

@OptIn(ExperimentalTestApi::class)
class ActionsPageTest {
    private val saved = Locale.getDefault()

    @Before fun before() {
        Locale.setDefault(Locale.ENGLISH)
    }

    @After fun after() {
        Locale.setDefault(saved)
    }

    @Test fun actionRowRendersLineSubtitleAndTagAndEmitsTaskIdOnClick() = runComposeUiTest {
        var clickedTask: Long? = null
        val item = ActionItem(
            id = 1,
            ts = 1794744000.0,
            taskId = 42L,
            sourceName = "Chat",
            line = "Sent a mail to test@example.com",
            kind = "send",
            state = "unknown",
        )

        setContent {
            KeloTheme(dark = false) {
                ActionRow(item = item, onOpenTask = { clickedTask = it })
            }
        }

        onNodeWithText("Sent a mail to test@example.com").assertIsDisplayed()
        onNodeWithText("Checking").assertIsDisplayed()
        onNodeWithText("Sent a mail to test@example.com").performClick()
        assertEquals(42L, clickedTask)
    }

    @Test fun failedStateTagRendersFailedLabel() = runComposeUiTest {
        val item = ActionItem(
            id = 2,
            ts = 1794744000.0,
            sourceName = "Schedule",
            line = "Deleted in Slack",
            kind = "delete",
            state = "failed",
        )

        setContent {
            KeloTheme(dark = false) {
                ActionRow(item = item, onOpenTask = {})
            }
        }

        onNodeWithText("Deleted in Slack").assertIsDisplayed()
        onNodeWithText("Didn't go through").assertIsDisplayed()
    }
}
