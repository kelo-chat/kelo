package chat.kelo.app.shell

import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Owner 2026-11-16: a solid band as tall as the whole header hid the chat. Only the clock strip may be solid. */
class HeaderScrimTest {
    @Test fun `solid part is no taller than the status bar`() {
        for (status in listOf(0.dp, 24.dp, 48.dp)) for (header in listOf(0.dp, 90.dp, 140.dp, 220.dp)) {
            val s = headerScrimShape(status, header)
            assertTrue("solid ${s.solid} > status $status (header $header)", s.solid <= status)
        }
    }

    @Test fun `solid plus fade stays within the status bar plus the fade`() {
        val s = headerScrimShape(40.dp, 140.dp)
        assertEquals(40.dp, s.solid)
        assertTrue(s.solid + s.fade <= 40.dp + 16.dp)
        assertTrue("the scrim must leave the header's own area see-through", s.solid + s.fade < 140.dp)
    }
}
