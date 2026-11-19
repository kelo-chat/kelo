package chat.kelo.app.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The page's own pop-up in the live view (owner report 2026-11-19): a native pop-up is never in the picture, so the status's `dialog`
 *  line says what the page asked and how Kelo answered. An older server sends none. */
class PageDialogTest {
    private fun status(json: String): LiveStatus = (LiveMessages.parse(json) as LiveIn.Status).status

    @Test fun `a status with a pop-up carries its line`() {
        val s = status("""{"type":"status","dialog":{"kind":"confirm","message":"Sign in first?","answer":"cancel","count":2,"at":1.5,"text":"The page asked: “Sign in first?” Kelo answered Cancel."}}""")
        assertEquals("confirm", s.dialog?.kind)
        assertEquals("cancel", s.dialog?.answer)
        assertEquals(2, s.dialog?.count)
        assertEquals("The page asked: “Sign in first?” Kelo answered Cancel.", LiveRules.dialogLine(s))
    }

    @Test fun `no pop-up, an older server or an empty line shows nothing`() {
        assertNull(LiveRules.dialogLine(status("""{"type":"status"}""")))
        assertNull(LiveRules.dialogLine(status("""{"type":"status","dialog":{"kind":"alert","text":"  "}}""")))
    }
}
