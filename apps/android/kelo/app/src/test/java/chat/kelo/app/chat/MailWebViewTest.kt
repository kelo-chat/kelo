package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MailWebViewTest {
    @Test fun everyLoadIsRefused() {
        for (url in listOf("https://a.example/x", "http://a", "file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "")) {
            assertTrue(url, MailWebPolicy.blockAll(url))
        }
        assertTrue(MailWebPolicy.blockAll(null))
    }

    @Test fun aLinkIsHandedOnOnlyWhenTheOwnerTappedIt() {
        assertNull(MailWebPolicy.tappedLink("https://a.example/x", hasGesture = false))
        assertEquals("https://a.example/x", MailWebPolicy.tappedLink("https://a.example/x", hasGesture = true))
    }
}
