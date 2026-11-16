package chat.kelo.desktop

import chat.kelo.app.res.*
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import chat.kelo.app.chat.BrowserCard
import chat.kelo.app.chat.ChatItem
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** The phone card in the desktop chat: the browser card for the Android device (phone title, the step line, "보기" opens the live view). */
class PhoneCardRenderTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private var mock: MockServer? = null

    @After fun stop() { mock?.stop() }

    private fun render(lang: String, card: ChatItem.Browser, onOpen: () -> Unit) {
        val app = MockServer("--lang", lang).also { mock = it }.app(tmp.newFolder(), lang = lang)
        compose.mainClock.autoAdvance = false
        compose.setContent { KeloFrame(app) { Column(Modifier.size(480.dp, 240.dp).padding(16.dp)) { BrowserCard(card, onOpenLive = onOpen) } } }
    }

    private fun device(status: String) = ChatItem.Browser("b1", "c1", "", "", "", status, null, 360, 780, tab = "android", device = true)

    @Test fun thePhoneCardSaysPhoneAndTheStepAndOpensTheLiveViewInKorean() {
        val ko = Words.of("ko")
        var opened = 0
        render("ko", device("휴대폰에서 누르는 중")) { opened++ }
        val ui = UiSteps(compose, parityShots())
        ui.waitText("휴대폰에서 누르는 중")
        assertEquals("휴대폰", ko.get(Res.string.chat_phone_title))
        compose.onNodeWithText("휴대폰").assertExists()
        compose.onAllNodesWithText(ko.get(Res.string.chat_browser_title)).assertCountEquals(0)   // not a browser card
        compose.onNodeWithText(ko.get(Res.string.chat_browser_view_short)).performClick()
        assertEquals(1, opened)
    }

    @Test fun thePhoneCardIsCalledPhoneInEnglish() {
        render("en", device("Typing on the phone")) {}
        UiSteps(compose, parityShots()).waitText("Typing on the phone")
        compose.onNode(hasText("Phone")).assertExists()
    }
}
