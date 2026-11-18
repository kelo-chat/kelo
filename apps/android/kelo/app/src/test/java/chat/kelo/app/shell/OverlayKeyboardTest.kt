package chat.kelo.app.shell

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OverlayKeyboardTest {
    @Test
    fun theWorkPageOverTheChatDismissesTheKeyboard() {
        assertTrue(OverlayKeyboard.shouldDismiss(workOpen = true, liveTabOpen = false))
    }

    @Test
    fun aLiveTabOverTheChatDismissesTheKeyboard() {
        assertTrue(OverlayKeyboard.shouldDismiss(workOpen = false, liveTabOpen = true))
    }

    @Test
    fun withNoOverlayTheKeyboardIsLeftAlone() {
        assertFalse(OverlayKeyboard.shouldDismiss(workOpen = false, liveTabOpen = false))
    }
}
