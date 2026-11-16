package chat.kelo.desktop

import chat.kelo.app.res.*
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performSemanticsAction
import chat.kelo.app.connectors.ConnectorData
import chat.kelo.app.connectors.ForgetOffer
import chat.kelo.desktop.chat.DesktopChat
import chat.kelo.desktop.ui.KeloFrame
import chat.kelo.desktop.ui.Page
import chat.kelo.desktop.ui.Shell
import org.junit.After
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The question after a disconnect (or a deleted login) that left data behind, on the desktop shell: the shared dialog (ForgetDataHost)
 * asks "…에서 가져온 자료도 지울까요?" with the counts and the trash note; "Keep" closes it and forgets nothing. Against
 * tools/mock_server.py in English. The offer is set directly (Sessions.offerForget, as a disconnect's answer does) so the test needs no
 * connector. Screenshot: build/parity (or $KELO_SHOTS) forget-offer-en-*.png.
 */
class ForgetOfferTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private var mock: MockServer? = null

    @After fun stop() { mock?.stop() }

    @Test fun aDisconnectOfferAsksAndKeepClosesIt() {
        val words = Words.of("en")
        val m = MockServer("--lang", "en").also { mock = it }
        val app = m.app(tmp.newFolder(), lang = "en")
        val chatHandle = DesktopChat(app)
        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            KeloFrame(app) {
                Shell(app, chatHandle.vm, Page.CHAT, onPage = {}, onSignedOut = {}, connectorToOpen = null, onConnectorOpened = {}, side = chatHandle.side)
            }
        }
        try {
            ui.waitUntil("the history") { chatHandle.vm.state.value.loaded }
            app.sessions.offerForget(ForgetOffer("gmail", "Gmail", ConnectorData(files = 2, items = 3)))
            val title = words.get(Res.string.connector_forget_title, "Gmail")
            ui.waitText(title)
            assertTrue("the trash note shows for files", ui.has(words.get(Res.string.connector_forget_trash)))
            ui.shot("forget-offer-en")
            fun click(text: String) = compose.onAllNodes(hasText(text) and hasClickAction()).onFirst().performSemanticsAction(SemanticsActions.OnClick)
            click(words.get(Res.string.connector_forget_keep))
            ui.waitUntil("the question closed") { app.sessions.forgetOffer.value == null }
            assertNull(app.sessions.forgetOffer.value)
            ui.waitUntil("the dialog gone") { !ui.has(title) }
        } catch (e: Throwable) {
            runCatching { ui.shot("forget-offer-en-zz-failed") }
            throw e
        }
    }
}
