package chat.kelo.app.call

import chat.kelo.app.res.call_card_waiting
import chat.kelo.app.res.Res
import chat.kelo.app.ui.text.loadStringIn
import kotlinx.coroutines.runBlocking
import kotlin.test.Test
import kotlin.test.assertEquals

/** The spoken line for a card event is the app's own text in the app's language (call_card_waiting, 20 locales; 2026-11-19). */
class CardWaitingLineTest {
    @Test fun `the card line is spoken in the app language`() = runBlocking {
        assertEquals("화면에서 확인할 게 있어요.", loadStringIn("ko", Res.string.call_card_waiting))
        assertEquals("There's something to check on the screen.", loadStringIn("en", Res.string.call_card_waiting))
        assertEquals("Auf dem Bildschirm gibt es etwas zu prüfen.", loadStringIn("de", Res.string.call_card_waiting))
    }
}
