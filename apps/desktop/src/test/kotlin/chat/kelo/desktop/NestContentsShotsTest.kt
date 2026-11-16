package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.unit.dp
import chat.kelo.app.res.*
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * My → Nest at 900 px wide, against tools/mock_server.py with KELO_MOCK_NEST=1: a pasted GitHub link gets its preview card, and the
 * card's "See everything it will install" row opens the shared NestContentsSheet (every file, the endpoints, the notes). Saved as
 * nest-contents-*.png in $KELO_SHOTS (or build/parity).
 */
class NestContentsShotsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private var mock: MockServer? = null
    private val start = mutableStateOf<String?>(null)
    private val en = Words.of("en")

    @After fun stop() { mock?.stop() }

    @Test fun theContentsRowAndItsSheetRenderAt900Wide() {
        val server = MockServer("--lang", "en", env = mapOf("KELO_MOCK_NEST" to "1")).also { mock = it }
        val app = server.app(tmp.newFolder(), lang = "en", theme = "light")
        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            Box(Modifier.size(900.dp, 1000.dp)) {
                KeloFrame(app) { MyPaneAt(app, start) }
            }
        }
        val look = en.get(Res.string.nest_link_check)
        val contentsRow = en.get(Res.string.nest_contents_open)
        val contentsTitle = en.get(Res.string.nest_contents_title)
        start.value = MyView.NEST
        ui.settle()
        ui.waitText(look)
        compose.onAllNodes(hasSetTextAction()).onFirst().performTextInput("https://github.com/acme/habit-tracker")
        ui.settle()
        compose.onNodeWithText(look).performClick()
        ui.waitText(contentsRow, timeout = 15_000)
        ui.shot("nest-contents-link-row-900")
        compose.onNodeWithText(contentsRow).performClick()
        ui.waitText(contentsTitle)
        check(ui.has("SKILL.md") && ui.has("scripts/check_in.py")) { "every file of the package is listed" }
        check(ui.has("api.example.com")) { "the endpoints are listed" }
        ui.shot("nest-contents-sheet-900")
    }
}
