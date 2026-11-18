package chat.kelo.app.agent

import chat.kelo.app.res.*
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** The rules the desktop's VNC screen adds to the shared live view: the focus, the picture, and the clipboard words. */
class VncScreenRulesTest {
    @Test fun aFieldOnThePageRaisesTheTypeLineOnThePhone() {
        assertTrue(EditingRule.focusTypeLine(before = false, now = true, keysGoToScreen = false))
    }

    @Test fun aFieldOnThePageDoesNotPullTheFocusOffTheDesktopScreen() {
        assertFalse(EditingRule.focusTypeLine(before = false, now = true, keysGoToScreen = true))
    }

    @Test fun theTypeLineKeepsItsOwnRaiseRule() {
        assertFalse(EditingRule.focusTypeLine(before = true, now = true, keysGoToScreen = false))
        assertFalse(EditingRule.focusTypeLine(before = false, now = false, keysGoToScreen = false))
    }

    @Test fun aVncScreenWithoutAFrameIsAPicture() {
        assertTrue(LiveRules.hasPicture(hasFrame = false, vnc = VncState.Showing(1440, 900)))
    }

    @Test fun noFrameAndNoScreenIsNoPicture() {
        assertFalse(LiveRules.hasPicture(hasFrame = false, vnc = VncState.Idle))
        assertFalse(LiveRules.hasPicture(hasFrame = false, vnc = VncState.Failed(VncFailure.BUSY)))
    }

    @Test fun aFrameIsAPictureWithoutTheScreen() {
        assertTrue(LiveRules.hasPicture(hasFrame = true, vnc = VncState.Idle))
    }

    @Test fun theDesktopClipboardWordsAreItsOwn() {
        val desktop = ClipboardRules.words(desktop = true)
        assertEquals(Res.string.desktop_live_paste, desktop.paste)
        assertEquals(Res.string.desktop_live_copied, desktop.copied)
    }

    @Test fun thePhoneKeepsItsClipboardWords() {
        val phone = ClipboardRules.words(desktop = false)
        assertEquals(Res.string.live_paste, phone.paste)
        assertEquals(Res.string.live_copied_to_phone, phone.copied)
    }
}
