package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
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
 * 마이 → 사용량's "토큰을 쓴 곳" (today and this month by kind of work, the cached share only where reported) and 마이 → 모델's
 * background work model row, against tools/mock_server.py: Korean and English, light and dark, plus a narrow window in German
 * and Arabic (wrapping, right to left). Saved as usage-jobs-*.png and model-background-*.png in build/parity ($KELO_SHOTS).
 */
class UsageJobsShotsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private val mocks = mutableListOf<MockServer>()
    private val start = androidx.compose.runtime.mutableStateOf<String?>(null)

    @After fun stop() { mocks.forEach { it.stop() } }

    @Test fun ko() = shots("ko", listOf("light", "dark"), 1000)

    @Test fun en() = shots("en", listOf("light", "dark"), 1000)

    @Test fun deNarrow() = shots("de", listOf("light"), 360, "narrow-")

    @Test fun arNarrow() = shots("ar", listOf("light"), 360, "narrow-")

    private fun shots(lang: String, themes: List<String>, width: Int, tag: String = "") {
        val server = MockServer("--lang", if (lang == "ko") "ko" else "en").also { mocks += it }   // the mock speaks Korean or English
        val app = server.app(tmp.newFolder(), lang = lang, theme = "light")
        val s = Strings.of(lang)
        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            Box(Modifier.size(width.dp, 1500.dp)) {
                KeloFrame(app) { MyPane(app, topInset = 0.dp, start = start.value, onStartUsed = { start.value = null }, onSignedOut = {}) }
            }
        }
        val title = s.get(R.string.usage_jobs_title)
        try {
            for (theme in themes) {
                app.settings.setTheme(theme)
                ui.waitText(s.get(R.string.my_section_account))
                ui.waitText(s.get(R.string.usage_used, 61), timeout = 15_000)   // the usage row's value: GET /api/usage has answered
                start.value = MyView.USAGE
                ui.settle()
                ui.waitText(title, timeout = 15_000)
                ui.waitText(s.get(R.string.usage_kind_chat))
                ui.waitText(s.get(R.string.usage_jobs_cache, 65))   // the sample chat: 98,000 of 150,000 input tokens from the cache
                check(!ui.has(s.get(R.string.usage_jobs_cache, 0))) { "a kind without a reported cache shows no 0%" }
                compose.onAllNodesWithText(title).onFirst().performScrollTo()
                ui.shot("usage-jobs-$tag$lang-$theme")
                compose.onAllNodesWithContentDescription(s.get(R.string.back))[0].performClick()
                ui.waitText(s.get(R.string.my_section_account))
                start.value = MyView.MODEL
                ui.settle()
                ui.waitText(s.get(R.string.model_background_title), timeout = 15_000)
                ui.waitText(s.get(R.string.model_background_same))
                compose.onAllNodesWithText(s.get(R.string.model_background_title)).onFirst().performScrollTo()
                ui.shot("model-background-$tag$lang-$theme")
                compose.onAllNodesWithContentDescription(s.get(R.string.back))[0].performClick()
                ui.waitText(s.get(R.string.my_section_account))
            }
        } catch (e: Throwable) {
            runCatching { ui.shot("usage-jobs-zz-failed-$tag$lang") }
            throw e
        }
    }
}
