package chat.kelo.desktop

import androidx.compose.ui.input.key.Key
import chat.kelo.desktop.ui.OPEN_CHAT_SEARCH
import chat.kelo.desktop.ui.Page
import chat.kelo.desktop.ui.windowShortcut
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The window's Cmd (macOS) / Ctrl (elsewhere) keys: 1…5 the pages in the rail's order, F the chat's search. */
class WindowShortcutsTest {
    @Test fun withTheModifierFOpensTheChatSearch() {
        assertEquals(OPEN_CHAT_SEARCH, windowShortcut(Key.F, withModifier = true))
    }

    @Test fun withoutTheModifierNothingIsAShortcut() {
        assertNull(windowShortcut(Key.F, withModifier = false))
        assertNull(windowShortcut(Key.One, withModifier = false))
    }

    @Test fun digitsOpenThePagesInTheRailsOrder() {
        assertEquals(Page.CHAT.name, windowShortcut(Key.One, withModifier = true))
        assertEquals(Page.GOALS.name, windowShortcut(Key.Two, withModifier = true))
        assertEquals(Page.WORK.name, windowShortcut(Key.Three, withModifier = true))
        assertEquals(Page.CONNECTORS.name, windowShortcut(Key.Four, withModifier = true))
        assertEquals(Page.MY.name, windowShortcut(Key.Five, withModifier = true))
    }

    @Test fun otherKeysAreNotOurs() {
        assertNull(windowShortcut(Key.A, withModifier = true))
        assertNull(windowShortcut(Key.Six, withModifier = true))
    }
}
