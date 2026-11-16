package chat.kelo.desktop

import chat.kelo.app.res.*
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * 마이 → 공지 against tools/mock_server.py with KELO_MOCK_NOTICES (tools/mock_parts/notices.py): one important and one info notice
 * above 계정과 기기. The card, then the sheet of the important one (its read notice leaves the list), light and dark, Korean; and a
 * narrow window (320 dp) in English. Saved as notices-*.png in build/parity ($KELO_SHOTS).
 */
class NoticesShotsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private val mocks = mutableListOf<MockServer>()
    private val start = mutableStateOf<String?>(null)

    @After fun stop() { mocks.forEach { it.stop() } }

    @Test fun koLight() = shots("ko", "light", 1000)

    @Test fun koDark() = shots("ko", "dark", 1000)

    @Test fun enNarrowLight() = shots("en", "light", 320, "narrow-")

    private fun shots(lang: String, theme: String, width: Int, tag: String = "") {
        val server = MockServer("--lang", if (lang == "ko") "ko" else "en", env = mapOf("KELO_MOCK_NOTICES" to "1")).also { mocks += it }
        val app = server.app(tmp.newFolder(), lang = lang, theme = "light")
        val s = Words.of(lang)
        val ui = UiSteps(compose, parityShots())
        val important = if (lang == "ko") "보안 업데이트가 나왔어요. 서버를 0.10.0으로 올려 주세요" else "A security update is out. Please update your server to 0.10.0"
        val info = if (lang == "ko") "11월 20일 새벽 서버 점검 안내" else "Server maintenance on November 20 at night"
        val body = if (lang == "ko") "비밀번호 보관 기능의 보안을 높이는 수정이 들어 있어요." else "It hardens how stored passwords are kept."
        compose.mainClock.autoAdvance = false
        compose.setContent {
            Box(Modifier.size(width.dp, 760.dp)) {   // the captured window is 768 high: what is scrolled to is in the picture
                KeloFrame(app) { MyPaneAt(app, start) }
            }
        }
        try {
            app.settings.setTheme(theme)
            ui.waitText(s.get(Res.string.my_section_account))
            ui.waitText(s.get(Res.string.notices_title), timeout = 15_000)
            ui.waitText(important)
            ui.waitText(info)
            ui.settle()
            ui.shot("notices-card-$tag$lang-$theme")

            // the important notice opens in the sheet: the whole body and the link button; it is read, so its card goes
            compose.onAllNodesWithText(important).onFirst().performClick()
            ui.waitText(body, timeout = 15_000)
            ui.waitText(s.get(Res.string.notices_more))
            ui.settle()
            ui.shot("notices-sheet-$tag$lang-$theme")
            compose.onAllNodesWithText(s.get(Res.string.notices_close)).onFirst().performClick()
            ui.settle()
            check(!ui.has(important)) { "a read notice leaves the list" }
            check(ui.has(info)) { "the unread notice stays" }
        } catch (e: Throwable) {
            runCatching { ui.shot("notices-zz-failed-$tag$lang-$theme") }
            throw e
        }
    }
}
