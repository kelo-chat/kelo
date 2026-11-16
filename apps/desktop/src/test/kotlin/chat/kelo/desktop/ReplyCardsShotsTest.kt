package chat.kelo.desktop

import chat.kelo.app.chat.QuickReplies
import chat.kelo.app.chat.ReplyCard
import chat.kelo.app.chat.ReplyCardImage
import chat.kelo.app.chat.ReplyQuestion
import chat.kelo.app.chat.ReplySet
import chat.kelo.app.res.*
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.FileKeyValueStore
import chat.kelo.desktop.core.StoreKey
import chat.kelo.desktop.ui.KeloFrame
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Picture cards under a choice question: with and without a picture, a recommended one, a long spaceless name, at 320 and 412 dp.
 * A tap on a card sends the option like its button does. Screenshots: $KELO_SHOTS or build/parity as reply-cards-*.png.
 */
class ReplyCardsShotsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private val ko = Words.of("ko")

    private val long = "프리미엄무선청소기물걸레겸용로봇청소기초강력흡입력블랙에디션2024년형최신모델"
    private val set = ReplySet(listOf(ReplyQuestion(
        "어느 걸로 할까요?", listOf("파란 머그", long, "초록 머그"),
        cards = listOf(
            ReplyCard(ReplyCardImage("img_1", "/api/chat-images/img_1", 480, 320), "9,900원 · 화요일 도착", recommended = true),
            ReplyCard(null, "148,000원 · 내일 새벽 도착 · 무료 반품 가능한 판매자 상품"),
            ReplyCard(),
        ),
    )))

    private fun run(widthDp: Int, name: String) {
        val dir = tmp.newFolder()
        val app = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        app.settings.setLanguage("ko")
        app.applyLanguage()
        app.settings.setTheme("light")
        val sent = mutableListOf<String>()
        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            KeloFrame(app) {
                Box(Modifier.size(widthDp.dp, 560.dp).background(Kelo.colors.bg).padding(8.dp)) { QuickReplies(set, onSend = { text, _ -> sent += text }) }
            }
        }
        ui.waitText("파란 머그")
        ui.settle()
        ui.shot("reply-cards-$name-light")
        app.settings.setTheme("dark")
        ui.shot("reply-cards-$name-dark")
        // the whole card is the button, spoken as "option, detail, recommended"
        val label = "파란 머그, 9,900원 · 화요일 도착, ${ko.get(Res.string.reply_recommended)}"
        compose.onAllNodes(hasContentDescription(label, substring = true) and hasClickAction()).onFirst().performClick()
        ui.settle()
        assertEquals(listOf("파란 머그"), sent)
    }

    @Test fun at320dp() = run(320, "320")
    @Test fun at412dp() = run(412, "412")
}
