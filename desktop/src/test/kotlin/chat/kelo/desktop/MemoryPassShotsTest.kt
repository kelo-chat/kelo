package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.desktop.i18n.Strings
import chat.kelo.desktop.pages.MyPane
import chat.kelo.desktop.pages.MyView
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * 마이 → 기록's memory pass line against tools/mock_server.py, in Korean and English, light and dark: the line ("기억 정리: 3개 추가,
 * 2개 정리"), what changed (grouped by area, never file names) and the undo. Saved as memory-pass-*.png in build/parity ($KELO_SHOTS).
 */
class MemoryPassShotsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private val mocks = mutableListOf<MockServer>()
    private val start = androidx.compose.runtime.mutableStateOf<String?>(null)

    @After fun stop() { mocks.forEach { it.stop() } }

    @Test fun ko() = shots("ko")

    @Test fun en() = shots("en")

    private fun shots(lang: String) {
        val server = MockServer("--lang", lang).also { mocks += it }
        val app = server.app(tmp.newFolder(), lang = lang, theme = "light")
        val s = Strings.of(lang)
        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            Box(Modifier.size(1000.dp, 1400.dp)) {
                KeloFrame(app) { MyPane(app, topInset = 0.dp, start = start.value, onStartUsed = { start.value = null }, onSignedOut = {}) }
            }
        }
        val line = s.get(R.string.memory_pass_line, s.get(R.string.memory_pass_both, s.plural(R.plurals.memory_pass_added, 3, 3),
            s.plural(R.plurals.memory_pass_tidied, 2, 2)))
        try {
            for (theme in listOf("light", "dark")) {
                app.settings.setTheme(theme)
                ui.waitText(s.get(R.string.my_section_account))
                start.value = MyView.RECORDS
                ui.settle()
                ui.waitText(line, timeout = 15_000)
                compose.onNodeWithText(line).performScrollTo()
                ui.shot("memory-pass-$lang-$theme")
                compose.onNodeWithText(s.get(R.string.memory_pass_view)).performClick()
                ui.waitText(s.get(R.string.memory_area_about_you))
                ui.waitText(s.get(R.string.memory_area_people))
                check(!ui.has("MEMORY.md") && !ui.has("user.md")) { "file names must not show" }
                ui.shot("memory-pass-changes-$lang-$theme")
                compose.onNodeWithText(s.get(R.string.close)).performClick()
                ui.settle()
                compose.onAllNodesWithContentDescription(s.get(R.string.back))[0].performClick()
                ui.waitText(s.get(R.string.my_section_account))
            }
            // the undo: asked first, then the line says it was put back
            start.value = MyView.RECORDS
            ui.settle()
            ui.waitText(line, timeout = 15_000)
            compose.onNodeWithText(line).performScrollTo()
            compose.onNodeWithText(s.get(R.string.memory_pass_view)).performClick()
            ui.waitText(s.get(R.string.memory_pass_undo))
            compose.onNodeWithText(s.get(R.string.memory_pass_undo)).performClick()
            ui.waitText(s.get(R.string.memory_pass_undo_confirm))
            ui.shot("memory-pass-undo-ask-$lang")
            compose.onAllNodesWithText(s.get(R.string.memory_pass_undo))[1].performClick()
            ui.waitUntil("the undone line") { compose.onAllNodesWithText(s.get(R.string.memory_pass_undone)).fetchSemanticsNodes().isNotEmpty() }
            ui.shot("memory-pass-undone-$lang")
        } catch (e: Throwable) {
            runCatching { ui.shot("memory-pass-zz-failed-$lang") }
            throw e
        }
    }
}
