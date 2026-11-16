package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import chat.kelo.app.chat.vault.SecretEntryCard
import chat.kelo.app.chat.vault.SecretRequest
import chat.kelo.app.res.Res
import chat.kelo.app.res.secret_enter
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.FileKeyValueStore
import chat.kelo.desktop.core.StoreKey
import chat.kelo.desktop.ui.KeloFrame
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Screenshots of the secret entry bottom sheet at 320 and 412 dp, light and dark.
 * Saved as build/parity/secret-sheet-*.png or under $KELO_SHOTS.
 */
class SecretEntryShotsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()

    private fun testSheet(widthDp: Int) {
        val dir = tmp.newFolder()
        val app = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        app.settings.setLanguage("ko")
        app.applyLanguage()
        app.settings.setTheme("light")

        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            KeloFrame(app) {
                Box(Modifier.size(widthDp.dp, 640.dp)) {
                    SecretEntryCard(
                        request = SecretRequest(
                            id = "req-shots",
                            name = "openai",
                            label = "OpenAI",
                            hosts = listOf("api.openai.com"),
                        ),
                        onSaved = { _, _ -> },
                        onCancel = {},
                        framed = true,
                    )
                }
            }
        }
        val enterText = Words.of("ko").get(Res.string.secret_enter)
        ui.waitText(enterText)
        ui.settle(6)
        compose.onNodeWithText(enterText).performClick()
        ui.settle(12)
        ui.waitText("키 또는 토큰")
        ui.shot("secret-sheet-$widthDp-light")
        app.settings.setTheme("dark")
        ui.settle(12)
        ui.shot("secret-sheet-$widthDp-dark")
    }

    @Test fun sheet320() = testSheet(320)
    @Test fun sheet412() = testSheet(412)
}
